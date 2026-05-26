# Phase 6E — Lifecycle/Delete Threat Audit

> **Created**: 2026-05-26
> **Status**: COMPLETE
> **Review Target**: `29-phase6E-lifecycle-scope-lock.md` · `30-phase6E-vertical-slice-lifecycle.md`
> **Secondary Context**: `22-semantic-event-architecture-conventions.md` · `24-phase6D-hierarchy-scope-lock.md` · `25-phase6D-vertical-slice-hierarchy.md` · `26-phase6D-hierarchy-implementation-plan.md` · `28-phase6D-live-runtime-validation.md`
> **Methodology**: Adversarial design review assuming replay ordering fails, stale packets survive, tombstones evict, hierarchy races deletes, GUID reuse becomes ambiguous
>
> **This is a DESIGN-LEVEL audit. No implementation, no runtime changes, no parser modifications.**

---

## Table of Contents

1. [Executive Verdict](#1-executive-verdict)
2. [Finding Summary](#2-finding-summary)
3. [Detailed Findings](#3-detailed-findings)
4. [Replay Resurrection Analysis](#4-replay-resurrection-analysis)
5. [Tombstone Correctness Analysis](#5-tombstone-correctness-analysis)
6. [Cross-Lane Invalidation Analysis](#6-cross-lane-invalidation-analysis)
7. [GUID Lifetime Determinism Analysis](#7-guid-lifetime-determinism-analysis)
8. [Snapshot/Reconnect Ordering Analysis](#8-snapshotreconnect-ordering-analysis)
9. [Queue & Memory Safety Analysis](#9-queue--memory-safety-analysis)
10. [Failure Mode Enumeration](#10-failure-mode-enumeration)
11. [Architecture Risk Summary](#11-architecture-risk-summary)
12. [Implementation Readiness Verdict](#12-implementation-readiness-verdict)

---

## 1. Executive Verdict

**GO WITH BLOCKERS**

The Phase 6E design is fundamentally sound — the 3-barrier stale-rejection system (sequence tracker → tombstone map → ActorCache existence check) provides defense-in-depth against replay resurrection, replay determinism is provably correct, and reconnect semantics are deterministic under the snapshot-authority model.

**However, 4 implementation-blocking issues (P1) were discovered:**

1. **FINDING-DEL-001**: The design mandates cross-lane hierarchy sequence coupling during parent-delete-child-detach, but the prescribed approach (increment child's hierarchy tracker) **will cause Blender's next genuine hierarchy event to be rejected as stale**. The correct approach is to NOT update the child's hierarchy tracker during implicit detach — the existing tracker state already rejects stale packets. If implemented as designed, the next Blender hierarchy event for every child of a deleted parent will be silently dropped.

2. **FINDING-DEL-002**: Tombstone table (§3.5 of vertical slice) states CREATE packets are NOT blocked by tombstone, but §2.3 text claims they ARE. This ambiguity creates a resurrection vector during snapshot replay: if a delete is processed during `bInSnapshotBuild`, a subsequent CREATE for the same GUID in the same batch will bypass the tombstone and recreate the actor.

3. **FINDING-DEL-003**: The design specifies no suppression RAII guard (§3.3 of scope lock: "No (no callback)"). The semantic-event conventions (§6.5) **mandate** suppression for ALL lanes regardless of callback risk, for pattern consistency and future-proofing.

4. **FINDING-DEL-004**: No explicit eviction of deferred hierarchy entries for children of a deleted parent. The design relies on FINDING-001 stale re-validation, which may be insufficient when the child's hierarchy tracker was NOT updated (per FINDING-DEL-001 resolution).

**Additionally, 9 containable issues (P2) and 3 documentation gaps (P3) were identified.**

Implementation must NOT proceed until FINDING-DEL-001 and FINDING-DEL-002 are resolved in the design documents.

---

## 2. Finding Summary

| ID | Severity | Area | Title | Implementation Blocker? |
|----|----------|------|-------|------------------------|
| DEL-001 | **P1** | Cross-lane coupling | Implicit detach sequence increment breaks genuine hierarchy events | **YES** |
| DEL-002 | **P1** | Tombstone semantics | CREATE tombstone policy self-contradictory; resurrection vector | **YES** |
| DEL-003 | **P1** | Conventions compliance | Suppression RAII guard absent | **YES** |
| DEL-004 | **P1** | Cross-lane invalidation | No explicit deferred entry eviction on parent delete | **YES** |
| DEL-005 | **P2** | Replay resurrection | 2048 eviction on both tracker and tombstone reduces to single-barrier | No — acceptable risk |
| DEL-006 | **P2** | V3 backward compat | PT_Delete 0x04 handler may bypass sequence tracking | No — pre-existing |
| DEL-007 | **P2** | Snapshot replay | Delete before CREATE in same batch (non-replay) causes resurrection | No — requires Blender bug |
| DEL-008 | **P2** | Blender dedup | Dual detection paths (ReferenceError + scan_scene) may send duplicate deletes | No — bandwidth only |
| DEL-009 | **P2** | Cross-lane | Transform-after-delete path not fully documented for timestamp ordering | No — containable |
| DEL-010 | **P2** | Tombstone eviction | Cross-connection stale delete + snapshot-recreated actor = wrong destroy | No — TCP boundary |
| DEL-011 | **P2** | Determine proofs | §7.1 proof assumes no queued deletes survive reconnect; not proven | No — plausible but implicit |
| DEL-012 | **P2** | Deferred resolution | Child's deferred entry may re-attach before hierarchy packet corrects it | No — transient only |
| DEL-013 | **P2** | Blender send queue | Queued delete during disconnect may race with snapshot on reconnect | No — eventual consistency |
| DEL-014 | **P3** | Observability | DeleteChildrenDetached cannot be verified without access to deleted parent's child list | No — cosmetic |
| DEL-015 | **P3** | Observability | No counter for tombstone eviction events | No — cosmetic |
| DEL-016 | **P3** | Edge case | No handling for delete during RecoverMissingActors | No — edge case |

---

## 3. Detailed Findings

### 3.1 DEL-001 [P1] — Implicit Detach Sequence Increment Breaks Genuine Hierarchy Events

**Severity**: P1 — Implementation Blocker

**Scenario**:
```
1. Child C attached to Parent P via PT_Hierarchy (seq=5 for C). Tracker has C→5.
2. User deletes P in Blender.
3. PT_Delete for P arrives at UE. HandleDelete():
   a. Detects C as child of P.
   b. Implicitly detaches C from P.
   c. [DESIGN SAYS] Increments C's hierarchy tracker from 5 to 6.
4. Blender detects C's parent changed (P deleted). Sends PT_Hierarchy for C→root with seq=6.
5. UE receives PT_Hierarchy. Tracker checks: incoming seq=6, last seq=6 → 6 ≤ 6 → STALE.
6. Hierarchy packet for C→root is REJECTED. C remains in detached state but tracker thinks seq=6 was applied as a hierarchy event.
```

**Root Cause**: The design (§7.4 of vertical slice, §10.3 of frozen-runtime audit) mandates "Parent-delete-child-detach MUST update the child's hierarchy sequence." But updating the tracker to any value that coincides with Blender's next genuine sequence causes a collision.

**Trace of Options**:
| Approach | Tracker After Implicit Detach | Blender Sends (seq) | Result |
|----------|-------------------------------|---------------------|--------|
| Increment (design intent) | N+1 | N+1 | STALE — rejected ❌ |
| Set to sequence value 0 | 0 | N+1 (where N+1 > 0) | ACCEPTED — but now 0 is the baseline and any stale packet with seq=1 is also accepted ❌ |
| Leave unchanged | N (unchanged) | N+1 | ACCEPTED ✅. Stale packet with seq=N is rejected (N ≤ N). |

**Correct Approach**: The implicit detach must **NOT** update the child's hierarchy sequence tracker. The existing tracker state (the last genuine hierarchy event's sequence) already rejects stale packets via the `<=` check. A stale hierarchy packet for C→P would have seq ≤ N and be correctly rejected.

**This eliminates the design's ONLY cross-lane coupling.** The coupling is unnecessary.

**Edge case — child never had a hierarchy event**: If C was never the target of a PT_Hierarchy, the tracker has no entry for C. Implicit detach does NOT add one. Blender's first hierarchy event for C will have seq=1 (or whatever Blender assigns). Tracker check: no entry → GetLastSeq returns 0 → 1 > 0 → accepted. ✅

**Edge case — stale hierarchy packet with seq < N arrives after detach**: If a stale hierarchy packet with seq=4 arrives (last genuine was seq=5), tracker check: 4 ≤ 5 → stale → rejected. ✅

**Impact on design**: §7.4 proof is incorrect as written. It claims the coupling is required. In fact, the coupling would CAUSE the exact bug it was designed to prevent. The proof must be revised.

**Recommended Fix**: In both scope lock and vertical slice, replace:
> "Parent-delete-child-detach MUST update the child's hierarchy sequence, even though no PT_Hierarchy packet is emitted."

With:
> "Parent-delete-child-detach MUST NOT update the child's hierarchy sequence tracker. The existing tracker state (last genuine hierarchy event's sequence) is sufficient to reject stale hierarchy packets. Blender's subsequent hierarchy event for the now-root child will have a higher sequence and be accepted normally."

**Implementation Blocker?**: **YES**. If implemented as designed, every child of a deleted parent will have its next hierarchy event silently dropped. Children will be detached (correct) but the hierarchy tracker will be in an inconsistent state for the child.

---

### 3.2 DEL-002 [P1] — CREATE Tombstone Policy Self-Contradictory

**Severity**: P1 — Implementation Blocker

**Scenario**: During snapshot replay with concurrent delete:
```
1. BeginSnapshot sets bInSnapshotBuild = true.
2. PT_Delete for GUID 0xAAAA arrives (user deleted object during snapshot build).
3. HandleDelete: actor A destroyed, GUID 0xAAAA added to tombstone map.
4. PT_Create for GUID 0xAAAA arrives (object was in snapshot — race).
5. Does the tombstone block the CREATE?
   a. TABLE (§3.5): "PT_Create (0x03) — NO — let through"
   b. TEXT (§2.3): "Subsequent create packets for the same GUID in the same snapshot batch are discarded (tombstone check)."
6. If (a): CREATE recreates actor A → RESURRECTION ❌
7. If (b): CREATE rejected → actor stays dead ✅
```

**Root Cause**: Two contradictory statements:
- Vertical slice §3.5 table: "PT_Create (0x03) | **NO** — let through (will be rejected by ActorCache existence check)"
- Vertical slice §2.3: "Subsequent create packets for the same GUID in the same snapshot batch are discarded (tombstone check)."

The table's rationale ("will be rejected by ActorCache existence check") is also WRONG. CREATE packets do NOT check ActorCache — they CREATE actors and ADD them to ActorCache. An ActorCache existence check on CREATE would prevent the first creation, which is the opposite of what CREATE does.

**Actual behavior analysis**:
| Check Applied | Result |
|---------------|--------|
| Tombstone check on CREATE | Blocks if GUID in tombstone map |
| Sequence tracker check on CREATE | Not applicable — CREATE uses different tracking |
| ActorCache existence check on CREATE | Should not be applied (CREATE adds to cache) |
| ActorCache existence check on the created actor | Would always be "not found" before creation |

The table's "rejected by ActorCache existence check" is meaningless — CREATE is the path by which actors ENTER the ActorCache. The tombstone IS the only barrier against CREATE-after-delete.

**Recommended Fix**: Resolve the contradiction. Either:
- **Option A**: Block CREATE at tombstone check. Rationale: tombstone means "this GUID's actor was intentionally destroyed and should not be recreated within this connection."
- **Option B**: Do NOT block CREATE at tombstone, but add an explicit tombstone re-check after actor creation that immediately destroys the actor. Rationale: batch parsing integrity.

**Recommendation**: Option A is simpler and correct. The batch parsing concern is moot because if the tombstone check rejects the CREATE, the packet is still parsed (boundary checks pass, just not applied). The same pattern is used for all other packet types blocked by tombstone.

**Implementation Blocker?**: **YES**. An implementation cannot proceed without knowing which behavior is correct. If the table is followed, CREATE packets during snapshot replay after a delete will resurrect the actor.

---

### 3.3 DEL-003 [P1] — Suppression RAII Guard Absent

**Severity**: P1 — Implementation Blocker

**Violation**: Semantic-event conventions §2.6 and §6.5 mandate per-lane RAII suppression for ALL semantic lanes. The scope lock (§3.3) states "No (no callback)" for delete.

**Why suppression is required**:
| Rationale | Source |
|-----------|--------|
| Pattern consistency — future maintainers don't need to know which lanes have callback risks | Conventions §6.5.1 |
| Future-proofing — if a future UE version adds a callback for DestroyActor | Conventions §6.5.2 |
| Verifiable — FScopedDeleteSuppression is grep-able | Conventions §6.5.3 |
| All prior lanes have suppression (rename, visibility, hierarchy) | Conventions §11 |

**Risk**: While `AActor::Destroy()` currently fires no standard callback that would trigger a re-sync, UE plugins can hook `OnDestroy` or similar. If a third-party plugin or future engine version calls back into the sync system, the suppression guard prevents re-replication.

**Recommended Fix**: Add `FScopedDeleteSuppression` RAII guard to the mandatory requirements:
- Add to scope lock §3.3: "Suppression: FScopedDeleteSuppression RAII guard"
- Add to vertical slice §9 (observability): "Suppression enter/exit logs (Verbose)"
- Remove "No (no callback)" from the delete handler specification

**Implementation Blocker?**: **YES**. Conventions require suppression for all lanes. Implementation without suppression fails the mandatory lane checklist (§10.6 item 8).

---

### 3.4 DEL-004 [P1] — Missing Explicit Deferred Entry Eviction on Parent Delete

**Severity**: P1 — Implementation Blocker

**Scenario**:
```
1. Child C has a pending deferred hierarchy entry (C→X) with seq=5.
2. Parent P (C's current parent in UE) is deleted via PT_Delete.
3. HandleDelete(P):
   a. Detaches C from P (C becomes root).
   b. [DEL-001 resolution]: Does NOT update C's hierarchy tracker (stays at seq=3 from last genuine hierarchy event for C).
4. ResolveHierarchyAttachments runs (same Tick, step 2a in pipeline):
   a. Finds C's deferred entry (C→X, seq=5).
   b. FINDING-001: IsStaleOrDuplicate(C, 5) → tracker has 3 → 5 > 3 → NOT stale.
   c. Attempts to attach C to X.
   d. If X exists: C is attached to X. WRONG — C should be root (Blender state after P's deletion).
   e. If X doesn't exist: entry re-deferred. Corrected next Tick when Blender's detach-to-root arrives.
```

**Root Cause**: The design (§6.2–6.3 of vertical slice) relies on the deferred queue's stale-detection mechanism (FINDING-001) to evict entries. But with DEL-001 resolution (tracker unchanged), FINDING-001 does NOT catch the stale entry because the deferred entry's sequence (seq=5) is higher than the tracker's (seq=3).

**Impact**: If X exists, C is transiently attached to X until Blender's hierarchy packet for C→root arrives (next sync frame). While the net effect converges correctly, this is a ~1-frame wrong attachment that violates the determinism claim.

**Deterministic repro**:
```
Tick N:   ProcessQueuedPackets processes PT_Hierarchy(C→X) → X not found → deferred (seq=5)
Tick N+1: ProcessQueuedPackets processes PT_Delete(P) → C detached from P. Tracker unchanged (seq=3).
          ResolveHierarchyAttachments → C→X found in deferred → FINDING-001: seq=5 > 3 → not stale.
            → X exists? → YES → AttachToActor(C, X). WRONG.
          Blender sends PT_Hierarchy(C→root, seq=6). NOT YET AT UE.
Tick N+2: ProcessQueuedPackets processes PT_Hierarchy(C→root, seq=6).
            → Tracker check: 6 > 3 → accepted. Detach from X, root. Corrected.
```

**Recommended Fix**: When parent P is deleted, iterate P's children and explicitly evict any deferred hierarchy entries for those children. This avoids the FINDING-001 dependency:

```cpp
// In HandleDelete(), after detaching children to root:
for (FGuid ChildGuid : DetachedChildren)
{
    // Explicitly evict any deferred entry for this child
    // This child's parent was deleted; its pending hierarchy intent is stale.
    PendingHierarchyAttachments.RemoveAll(
        [ChildGuid](const FPendingHierarchyAttachment& Entry)
        {
            return Entry.ChildGuid == ChildGuid;
        });
}
```

**Implementation Blocker?**: **YES**. Without explicit eviction, children of deleted parents retain stale deferred entries that may resolve incorrectly.

---

### 3.5 DEL-005 [P2] — 2048 Eviction Reduces to Single Barrier

**Severity**: P2 — Dangerous but containable

**Scenario**:
```
1. GUID 0xAAAA deleted in connection. Tracker records seq=5. Tombstone records 0xAAAA.
2. 2048+ other GUIDs are tracked across all sequence trackers and tombstones (unusual but possible with large scene churn).
3. Tracker evicts 0xAAAA entry. Tombstone map evicts 0xAAAA entry.
4. Stale delete packet for 0xAAAA arrives with seq=3 (genuinely stale).
5. Tracker: no entry → GetLastSeq returns 0 → 3 > 0 → accepted.
6. Tombstone: no entry → not blocked.
7. ActorCache: actor was destroyed → not found → silently discarded.
```

**Mitigation**: ActorCache saves the day. However, if step 4 were a STALE DELETE for a recreated actor (astronomically unlikely UUID collision), no barrier would prevent destruction.

**Risk**: Acceptable. Requiring 2048+ evictions within a single session AND a UUID collision AND a stale packet arrival is a 3-event chain with negligible probability.

**Mitigation**: None needed beyond the existing 2048 bound. Documented accepted limitation.

---

### 3.6 DEL-006 [P2] — V3 PT_Delete (0x04) Backward Compat Gap

**Severity**: P2 — Dangerous but containable

**Scenario**:
```
1. Blender sends PT_Delete (0x04) via V3 path (backward compatibility).
2. Blender also sends PT_Delete (0x0E) via V5+ path.
3. If V3 handler (0x04) does NOT have a sequence tracker, it cannot reject stale packets.
4. A stale 0x04 packet arrives first → destroys valid actor via unguarded destroy.
5. The legitimate 0x0E packet arrives → actor not found → discarded.
```

**Root Cause**: The design keeps 0x04 for V3 compatibility. If the V3 handler lacks a sequence tracker, a stale V3 packet can bypass all Phase 6E protections.

**Pre-existing**: This vulnerability exists in the current codebase regardless of Phase 6E. Phase 6E doesn't make it worse.

**Mitigation**: Either:
- Add sequence tracking to the V3 PT_Delete handler, OR
- Document that V3 PT_Delete does NOT have replay protection and Phase 6E's 3-barrier system only applies to PT_Delete (0x0E), OR
- Phase out V3 PT_Delete support entirely on reconnect.

**Recommendation**: Document the gap in the scope lock. Add a note that V3 PT_Delete (0x04) is NOT protected by the sequence tracker, and stale V3 delete packets may destroy actors. Mitigation is to use V5+ protocol.

---

### 3.7 DEL-007 [P2] — Delete Before CREATE in Same Batch (Non-Replay)

**Severity**: P2 — Dangerous but containable

**Scenario**:
```
1. bInSnapshotBuild == false (live traffic, not replay).
2. ProcessQueuedPackets receives batch with DELETE for G followed by CREATE for G.
3. Process DELETE: ActorCache doesn't have G → silently discarded.
4. Process CREATE: ActorCache doesn't have G → actor created.
5. Net effect: actor is alive. WRONG (object was deleted in Blender).
```

**Mitigation**: This scenario requires Blender to send both a DELETE and CREATE for identical GUID in the same batch during live traffic. This cannot happen because:
- CREATE is only sent during snapshot (BeginSnapshot/EndSnapshot framing)
- DELETE is only sent during live operation (delta detection)
- During live traffic, bInSnapshotBuild is false

During snapshot replay, the design correctly defers deletes to after EndSnapshot (if CREATE not yet processed) or processes them normally (if CREATE already processed).

**Containable by**: Code review ensuring CREATE and DELETE paths are temporally isolated (CREATE only during snapshot).

---

### 3.8 DEL-008 [P2] — Dual Detection Paths May Send Duplicate Deletes

**Severity**: P2 — Dangerous but containable

**Scenario**:
```
1. Blender sync loop detects deletion via ReferenceError during tracked_objects iteration.
   → Immediately queues PT_Delete (seq=N).
2. scan_scene() diff detects object missing from bpy.data.objects (every 300 frames).
   → Queues PT_Delete again with seq=N+1.
3. UE receives two delete packets: seq=N (accepted) and seq=N+1 (accepted by tracker, but actor already gone).
```

**Impact**: Bandwidth waste only. UE correctly handles both.

**Containable by**: Not a correctness issue. However, the design should document that Blender MAY send duplicate delete packets and UE MUST handle them gracefully (which it does via tombstone + ActorCache).

**Recommendation**: Add Blender-side dedup: if an object's GUID is already in `_delete_sequences` as pending, skip scan_scene emission.

---

### 3.9 DEL-009 [P2] — Transform-After-Delete Timestamp Ordering

**Severity**: P2 — Dangerous but containable

**Scenario**:
```
1. Transforms for Actor A arrive in ProcessQueuedPackets.
2. Delete for Actor A arrives in SAME batch.
3. Order within ProcessQueuedPackets depends on packet arrival order (TCP).
   a. If delete processed first: actor destroyed. Transforms discarded (ActorCache miss).
   b. If transforms processed first: transforms applied. Then delete: actor destroyed.
```

**Impact**: In scenario (b), the transforms are applied to an actor that is about to be destroyed. The final transform just before deletion might not be applied if the delete arrives first.

**Mitigation**: This is existing behavior for all semantic lanes. Transforms are overwrite-oriented — the last transform before delete is NOT guaranteed to be applied if the delete packet arrives first. Acceptable for eventual consistency.

**Recommendation**: Document in the transform interaction section (§6 of scope lock) that transform ordering relative to delete is non-deterministic within a single batch and the last transform before delete may not be applied.

---

### 3.10 DEL-010 [P2] — Cross-Connection Stale Delete + Snapshot Recreation

**Severity**: P2 — Dangerous but containable

**Scenario**:
```
1. Connection 1: Actor A (GUID:aaa) exists. A is NOT deleted.
2. Connection drops. Tracker cleared. Tombstone cleared. ActorCache cleared.
3. Connection 2: Snapshot recreates Actor A (GUID:aaa) — object still exists in Blender.
4. Hypothetical stale delete packet arrives from connection 1.
5. Tracker: no entry → seq > 0 → accepted.
6. Tombstone: cleared → no block.
7. ActorCache: A exists → Destroyed. WRONG.
```

**Barrier**: TCP connection boundaries prevent step 4. Old TCP connection was closed. Stale packets on old connection are lost when the socket is closed. The new connection has no stale packets.

**Containable by**: TCP guarantees. However, if Blender re-sends a queued packet from the old connection's send queue after reconnect (which it SHOULD NOT — the send queue is cleared on disconnect), this scenario becomes possible.

**Recommendation**: Verify Blender-side send queue clearing on disconnect. Add an explicit note in the scope lock: "Blender's send queue MUST be cleared on disconnect to prevent stale packet replay across connection boundaries."

---

### 3.11 DEL-011 [P2] — §7.1 Reconnect Proof Has Implicit Assumption

**Severity**: P2 — Dangerous but containable

**Proof gap**: The §7.1 proof assumes "Blender sends queued packets + snapshot" (§4.3) but the proof itself only considers the snapshot. Proof statement: "After snapshot replay, ActorCache = {actors created by CREATE packets in the snapshot}."

This is only correct if:
1. The snapshot is the SOLE source of actors (no queued deletes pre-process actors)
2. No stale packets from the send queue interfere

The proof should explicitly address the interaction of queued packets (delete during disconnect, §4.3) with snapshot initialization. The current proof is correct but incomplete.

**Recommendation**: Extend §7.1 to include:
- A lemma that queued delete packets from pre-disconnect cannot bypass the snapshot
- A lemma that queued delete packets do not interfere with snapshot actor creation

---

### 3.12 DEL-012 [P2] — Deferred Entry Re-Attach Before Hierarchy Correction

**Severity**: P2 — Dangerous but containable

**Scenario** (detailed trace):
```
Precondition: C has deferred entry (C→X, seq=5). C's current tracker: seq=3.

Tick N:
  ProcessQueuedPackets:
    PT_Delete(P) processed.
      C detached from P (C is root).
      C's tracker unchanged (seq=3). [per DEL-001 resolution]
      C's deferred entry NOT explicitly evicted. [per DEL-004 resolution]
  ResolveHierarchyAttachments:
    C's deferred entry (C→X, seq=5) evaluated.
      FINDING-001: tracker(3) < seq(5) → NOT stale.
      X found in ActorCache? → YES → AttachToActor(C, X). WRONG.
      Counter: HierarchyDeferredResolved++. Logged as success.

Tick N+1:
  ProcessQueuedPackets:
    PT_Hierarchy(C→root, seq=6) arrives (Blender detected parent change).
      Tracker: 6 > 3 → accepted. Detach C from X. Attach to root. Corrected.
      Counter: HierarchyProcessed++.
```

**Impact**: ~1 frame of wrong attachment. While the system converges correctly, the transient violates the strict determinism claim.

**Containment**: If FINDING-DEL-004 is implemented (explicit deferred entry eviction on parent delete), this scenario cannot occur. The explicit eviction removes C's deferred entry before ResolveHierarchyAttachments runs.

**Recommendation**: This finding is a restatement of DEL-004's impact. The fix for DEL-004 (explicit eviction) resolves this.

---

### 3.13 DEL-013 [P2] — Queued Delete During Disconnect Races Snapshot

**Severity**: P2 — Dangerous but containable

**Scenario**:
```
1. User deletes object A in Blender.
2. Connection is healthy. Delete packet enqueued.
3. Before delete is sent, connection drops (network blip).
4. Blender's send queue still has the delete packet.
5. Blender detects disconnect, clears state. Reconnects.
6. On reconnect: Blender sends queued packets + snapshot.
7. Possibility: Queued delete for A + snapshot that does NOT contain A.
   a. Delete arrives first: ActorCache miss → discarded. ✅
   b. Snapshot arrives first: A not in snapshot → A not created. ✅
   c. Both fine.
8. BUT: What if Blender does NOT clear the send queue on disconnect?
   → Delete for A is re-sent on new connection.
   → Snapshot doesn't contain A (deleted).
   → Delete arrives first: ActorCache miss → discarded. ✅ (ActorCache is empty after reconnect.)
```

**Containment**: Even without send queue clearing, the result is correct because ActorCache is empty after reconnect. The queued delete finds no target.

**Edge case — snapshot contains A (race)**:
```
1. User deletes A in Blender. Queued: PT_Delete(A).
2. BUT Blender's snapshot was built BEFORE the delete was detected.
3. Snapshot includes A (CREATE for A).
4. On reconnect: PT_Delete(A) + PT_Create(A) both sent.
   a. Delete first: ActorCache miss → discarded. Create later: A created. WRONG.
   b. Create first: A created. Delete later: A destroyed. ✅
```

In case (a), the object was deleted in Blender but UE has it alive. On the next sync frame, Blender's tracked_objects no longer has A, so no new packets are sent. But the actor is alive in UE — this is a stale state until UE reconnects again or the user manually removes it.

**Mitigation**: The design should specify that Blender's send queue MUST be cleared on disconnect/reconnect (or, more precisely, the snapshot send process MUST flush the send queue first).

**Recommendation**: Add to scope lock: "Blender's send queue is cleared on disconnect. On reconnect, the snapshot is the SOLE source of truth. No pre-disconnect events are replayed across the new connection."

---

### 3.14 DEL-014 [P3] — DeleteChildrenDetached Counter Verification

**Severity**: P3 — Observability/documentation gap

The `DeleteChildrenDetached` counter is incremented in `HandleDelete()` when children are detached before parent destroy. However, the counter cannot distinguish between:
- A parent with 10 children → 1 increment
- A parent with 1 child → 1 increment

The counter counts "parent delete events that had children" not "total children detached." If the intent is the latter, the counter should be incremented per-child. If the former, the name is misleading.

**Recommendation**: Clarify whether the counter tracks "parent deletions with children" or "total children detached." If total children, use per-child increment. If parent deletions, rename to `DeleteParentWithChildren`.

---

### 3.15 DEL-015 [P3] — No Tombstone Eviction Counter

**Severity**: P3 — Cosmetic

The tombstone map has LRU eviction at 2048, but there is no counter tracking how many times eviction occurs. Without this counter, silent tombstone loss cannot be detected.

**Recommendation**: Add a `DeleteTombstoneEvictions` counter incremented on each LRU eviction.

---

### 3.16 DEL-016 [P3] — Delete During RecoverMissingActors

**Severity**: P3 — Edge case

`RecoverMissingActors` runs in the Tick pipeline. If a delete packet arrives during or just before `RecoverMissingActors`, the actor might be re-created by the recovery mechanism before the delete is processed.

**Analysis**: The Tick pipeline processes packets BEFORE recovery:
1. ProcessQueuedPackets (delete processed here)
2. ResolvePendingAttachments
3. InterpolateTransforms
4. RecoverMissingActors

If delete is processed in step 1, the actor is gone before step 4 runs. ✅

But what if the delete and recovery criteria interleave across Ticks?
- Tick N: Delete processed → actor destroyed
- Tick N+1: RecoverMissingActors criteria met → actor RECREATED ❌ (if recovery doesn't know about the delete)

**Mitigation**: RecoverMissingActors should check the tombstone map before recreating an actor. If the GUID is in the tombstone map, the actor was intentionally deleted and should NOT be recovered.

**Recommendation**: Add a tombstone check to RecoverMissingActors.

---

## 4. Replay Resurrection Analysis

### 4.1 Primary Barriers

```
                    ┌─────────────────────┐
                    │  STALE DELETE PACKET │
                    └──────────┬──────────┘
                               │
                    ┌──────────▼──────────┐
                    │  Barrier 1:         │
                    │  Sequence tracker   │──→ Stale seq ≤ LastSeq → REJECT
                    │  (per-GUID, ≤ 2048) │
                    └──────────┬──────────┘
                               │ (passed)
                    ┌──────────▼──────────┐
                    │  Barrier 2:         │
                    │  Tombstone map      │──→ GUID in map → REJECT
                    │  (LRU, ≤ 2048)      │
                    └──────────┬──────────┘
                               │ (passed)
                    ┌──────────▼──────────┐
                    │  Barrier 3:         │
                    │  ActorCache check   │──→ Actor not found → REJECT
                    │  (cross-connection) │
                    └──────────┬──────────┘
                               │ (passed)
                    ┌──────────▼──────────┐
                    │  ACTOR DESTROYED    │
                    └─────────────────────┘
```

### 4.2 Cross-Connection Analysis

Each barrier has different effectiveness across connection boundaries:

| Barrier | Intra-Connection | Cross-Connection |
|---------|-----------------|------------------|
| Sequence tracker | ✅ Strong — per-GUID monotonic | ❌ Cleared on reconnect |
| Tombstone map | ✅ Strong — GUID presence check | ❌ Cleared on reconnect |
| ActorCache existence | ✅ Always effective | ✅ Always effective (empty after reconnect until snapshot populates it) |

**Key insight**: After reconnect, ActorCache is empty. Snapshot populates it. Until the snapshot is fully processed, no actor can be deleted because no actor exists. After snapshot, only actors in the snapshot exist. A stale delete can only destroy an actor that WAS in the snapshot — but if the object was deleted in Blender, it's NOT in the snapshot.

### 4.3 Identified Resurrection Vectors

| Vector | Prevented By | Residual Risk |
|--------|-------------|---------------|
| Stale intra-connection delete | Sequence tracker (≤) | Eviction at 2048 (DEL-005) |
| Stale replay after actor recreated | Tombstone map | Eviction at 2048 (DEL-005) |
| Cross-connection stale delete | ActorCache empty → miss | Stale delete during snapshot build (DEL-007) |
| Snapshot includes deleted object (race) | Snapshot rebuilds from current tracked_objects | Concurrent delete during snapshot build (§2.3) |
| CREATE-after-delete during snapshot | (Ambiguous — DEL-002) | Contradictory design → MUST RESOLVE |
| Queued delete from old connection | TCP connection boundary | Blender send queue NOT cleared (DEL-013) |

**Verdict**: With DEL-001 and DEL-002 resolved, the design eliminates all identifiable resurrection vectors. The 3-barrier system provides defense-in-depth.

---

## 5. Tombstone Correctness Analysis

### 5.1 Lifecycle Verification

| Lifecycle Phase | Correctness | Issues |
|-----------------|-------------|--------|
| **Entry**: Successful delete → GUID added to tombstone map | ✅ Correct | Tombstone recorded after ActorCache removal |
| **Lookup**: Stale packet → GUID in tombstone → discard | ✅ Correct | Unless tombstone evicted (DEL-005) |
| **Update**: New delete for same GUID → sequence updated | ✅ Correct | Higher seq updates tracker and tombstone |
| **Clear**: Reconnect → tombstone map cleared | ✅ Correct | Snapshot authority applies |
| **Eviction**: LRU at 2048 → oldest removed | ✅ Correct bounded | No counter for eviction events (DEL-015) |

### 5.2 Eviction Safety Proof

**Claim**: Tombstone eviction cannot cause actor resurrection within a single connection.

**Proof**:
1. For a stale packet to resurrect a tombstoned actor, the actor must be recreated (CREATE) after the tombstone eviction.
2. CREATE for a tombstoned GUID can only arrive if:
   a. Blender regenerated the same GUID (prevented by `ensure_unique_guid()`), OR
   b. A snapshot is replayed (only happens on reconnect, which clears tombstones), OR
   c. The tombstone was evicted and a stale CREATE packet arrives (astronomically unlikely — TCP ordering prevents stale packets within a connection)
3. Since (a) is prevented by Blender collision detection, (b) clears tombstones first, and (c) is astronomically unlikely:
4. **QED**: Tombstone eviction cannot cause resurrection.

### 5.3 Tombstone Map — CREATES Blocking Design Ambiguity

See FINDING-DEL-002. The table says CREATEs are NOT blocked by tombstone; the text implies they ARE. This MUST be resolved before implementation.

**Recommendation**: Block CREATE at tombstone check. This is the simplest correct approach.

---

## 6. Cross-Lane Invalidation Analysis

### 6.1 Interaction Matrix

| Lane | Interaction with Delete | Risk Level | Requires Sequence Coupling? |
|------|------------------------|------------|----------------------------|
| **Hierarchy** | Parent delete → children detached. Stale hierarchy packets → rejected by tracker. | **HIGH** | DEL-001: NO (design says YES, which is wrong) |
| **Rename** | Rename for deleted GUID → ActorCache miss → discarded | **LOW** | NO |
| **Visibility** | Visibility for deleted GUID → ActorCache miss → discarded | **LOW** | NO |
| **Transform** | Transform for deleted GUID → ActorCache miss → discarded | **LOW** | NO |
| **Replay queues** | Delete during snapshot replay → must not resurrect | **HIGH** | NO (use deferral mechanism) |
| **Orphan queue** | Parent deleted → child's deferred entry stale | **MEDIUM** | DEL-004: pending explicit eviction |
| **PendingAssetQueue** | Delete for GUID with pending asset → discarded | **LOW** | NO |

### 6.2 Hierarchy Coupling Analysis

**Design claim**: "This is the ONLY cross-lane coupling in the entire Phase 6E design."

**Audit result**: The coupling is UNNECESSARY and would be HARMFUL if implemented as "increment child's hierarchy sequence." See FINDING-DEL-001.

**Correct behavior**: Implicit detach during parent delete must NOT update the child's hierarchy sequence tracker. The existing tracker state (last genuine hierarchy event's sequence) provides sufficient protection against stale hierarchy replay.

### 6.3 Deferred Queue Interaction

When parent P is deleted and child C has a pending deferred hierarchy entry:

| Scenario | Current Design | Correct? |
|----------|---------------|----------|
| C's deferred entry targets P | Evicted on next retry (parent not found) | ✅ Eventually correct |
| C's deferred entry targets X (not P) | Not evicted; may resolve before hierarchy correction | ❌ Transient wrong state (DEL-004, DEL-012) |
| C has multiple deferred entries | Dedup per FINDING-002 | ✅ |

### 6.4 Suppression Cross-Lane Impact

Without suppression (DEL-003), if `DestroyActor` in a future UE version fires `OnDestroy` → callback triggers `SetActorLabel` or similar → triggers rename detection → sends PT_Rename for deleted actor → wasted bandwidth. Suppression prevents this cascade.

### 6.5 Frozen Runtime Impact

| Frozen System | Phase 6E Interaction | Allowed? |
|--------------|---------------------|----------|
| `LiveSyncQueue` | Delete packets use existing FLiveSyncPacket discriminated union | ✅ No change |
| `PendingAssetQueue` | Delete bypasses asset pipeline (no asset needed for destruction) | ✅ No change |
| `LiveSyncRunnable` | Delete parsed in ProcessBinaryPacket on game thread | ✅ No change |
| `FSyncTransformState` | Delete removes ActorCache entry — no struct modification | ✅ No change |
| Tick ordering | Delete processed in ProcessQueuedPackets (slot 1) | ✅ No change |
| `AttachToParent` / `DetachFromParent` | Detach cascade uses raw DetachFromActor | ✅ No change |
| `ResolvePendingAttachments` | No interaction with semantic lane deferred queue | ✅ No change |
| `RecoverMissingActors` | DEL-016: tombstone check needed | ⚠️ Acceptable |

---

## 7. GUID Lifetime Determinism Analysis

### 7.1 Scenario Analysis

| Scenario | Deterministic? | Barrier |
|----------|---------------|---------|
| Same-session GUID reuse via copy | ✅ Yes — `ensure_unique_guid()` detects and reassigns | Object-level |
| Same-session GUID reuse after delete | ✅ Yes — deleted GUID removed from bpy.data.objects; ensure_unique_guid checks current objects only; 128-bit UUID collision negligible | Object-level + statistical |
| Cross-session GUID collision | ✅ Yes — GUIDs regenerated on start_sync() | Session boundary |
| Stale packet after GUID reuse | ✅ Yes — old GUID's ActorCache entry was removed on delete; new object creates new entry | ActorCache |
| Tombstone ambiguity (was this GUID deleted in this session?) | ✅ Yes — tombstone map tracks it | Tombstone map |
| Reconnect GUID ambiguity | ✅ Yes — fresh state, snapshot authority | Reconnect clear |

### 7.2 Ambiguity at Eviction

When both tracker and tombstone evict a GUID:

| Question | Answer |
|----------|--------|
| Was this GUID ever deleted in this session? | Unknown after eviction |
| Can a stale packet reach it? | ActorCache miss → discarded |
| Can a genuine new packet reach it? | Only if the GUID was regenerated (statistically impossible) |

**Verdict**: GUID semantics are deterministic under all practical scenarios. Edge cases require UUID collision, which is astronomically unlikely.

---

## 8. Snapshot/Reconnect Ordering Analysis

### 8.1 Packet Ordering Matrix

```
Scenario: Create + Delete for same GUID arrive in same batch
┌────────────────────┬─────────────────────────────────────────────────────────────┐
│                    │  bInSnapshotBuild = true                                    │
│                    │  (snapshot replay)                                          │
│                    │                                                             │
│  CREATE before     │  CREATE: actor created.                                     │
│  DELETE            │  DELETE: actor destroyed (or deferred if DELETE before      │
│                    │          CREATE was processed — §2.4 deferral).             │
│                    │  Net: actor gone.                                           │
│                    │  Flicker: ~1 frame (acceptable).                            │
├────────────────────┼─────────────────────────────────────────────────────────────┤
│  DELETE before     │  DELETE: DEFERRED to after EndSnapshot.                    │
│  CREATE            │  CREATE: actor created.                                     │
│                    │  EndSnapshot: deferred DELETE runs → actor destroyed.       │
│                    │  Net: actor gone.                                           │
├────────────────────┼─────────────────────────────────────────────────────────────┤
│                    │  bInSnapshotBuild = false                                    │
│                    │  (live traffic)                                             │
│                    │                                                             │
│  CREATE before     │  CREATE: actor created.                                     │
│  DELETE            │  DELETE: actor destroyed.                                   │
│                    │  Net: actor gone.                                           │
├────────────────────┼─────────────────────────────────────────────────────────────┤
│  DELETE before     │  DELETE: ActorCache miss → discarded.                      │
│  CREATE            │  CREATE: actor created.                                     │
│                    │  Net: actor ALIVE. **WRONG**                                │
│                    │  **Cannot occur**: DELETE and CREATE don't coexist in       │
│                    │  live traffic. CREATE is snapshot-only.                     │
└────────────────────┴─────────────────────────────────────────────────────────────┘
```

### 8.2 Reconnect Ordering

```
Disconnect → Clear all state (trackers, tombstones, ActorCache, queues)
    │
    ▼
Blender rebuilds snapshot from tracked_objects (deleted objects excluded)
    │
    ▼
Snapshot sent: BeginSnapshot → ...CREATE packets... → EndSnapshot
    │
    ▼
UE processes CREATE packets → ActorCache populated
    │
    ▼
No delete packet for a deleted object exists (object not in snapshot)
```

**Correctness**: The only threat is a delete packet sent just before disconnect, queued in Blender, and re-sent on reconnect. The design must guarantee the send queue is cleared. See DEL-013.

---

## 9. Queue & Memory Safety Analysis

### 9.1 Bounded Structure Audit

| Structure | Bound | Element Size | Max Memory | Eviction |
|-----------|-------|-------------|------------|----------|
| `FDeleteSequenceTracker` | 2048 entries | 20 bytes (FGuid + uint32) | ~41 KB | Arbitrary TMap eviction |
| `GDeleteTombstoneMap` | 2048 entries | 20 bytes (FGuid + uint32) | ~41 KB | LRU eviction |
| `Pending delete during snapshot` | Per-object, bounded by scene size | ~28 bytes per pending delete | ~28 KB for 1000 objects | Cleared on EndSnapshot |
| Deferred hierarchy queue (Phase 6D) | 2048 entries | ~42 bytes per entry | ~86 KB | FIFO, timeout, reconnect |

**Total additional memory**: ~200 KB at worst case. ✅ Acceptable.

### 9.2 Growth Analysis

| Scenario | Growth Pattern | Bound |
|----------|---------------|-------|
| Steady-state delete operations | Entry added, stays until eviction at 2048 | ✅ Bounded |
| Delete storm (1000 deletes in 1 frame) | 1000 entries added to tracker + tombstone | ✅ Within 2048 bound |
| Reconnect clear | All structures emptied | ✅ O(1) clear |
| ConsoleReset | All structures emptied + counters zeroed | ✅ O(1) clear |
| Pending deletes during snapshot | Accumulates until EndSnapshot | ✅ Bounded by scene size |

### 9.3 Eviction Chain Safety

When 2048 limit is reached on the tracker:

```
Tracker evicts oldest entry
    → GUID X's last sequence forgotten
    → Next packet for X with seq S is accepted if S > 0
    → But: tombstone may still have X
    → If tombstone also evicted X: ActorCache is the sole barrier
    → If ActorCache doesn't have X (deleted long ago): discarded
    → If ActorCache has X (recreated, UUID collision): destroyed
```

The eviction chain reduces from 3 barriers to 1, but the remaining barrier (ActorCache) is the strongest cross-connection barrier. **This is acceptable.**

---

## 10. Failure Mode Enumeration

### 10.1 Master Failure Mode Table

| ID | Category | Failure Mode | Root Cause | P0? | Mitigation |
|----|----------|-------------|------------|-----|------------|
| F01 | Resurrection | Stale delete recreates destroyed actor | Tracker eviction + tombstone eviction + ActorCache miss | No | 3-barrier defense-in-depth |
| F02 | Resurrection | CREATE bypasses tombstone during snapshot replay | DEL-002: contradictory tombstone policy | **YES** | Resolve DEL-002 before implementation |
| F03 | Resurrection | Deferred delete creates flicker then actor stays alive | DEL-007: DELETE before CREATE in live traffic | No | Temporal isolation of CREATE/DELETE |
| F04 | Resurrection | Queued delete races snapshot on reconnect | DEL-013: send queue not cleared on disconnect | No | TCP boundary + eventual consistency |
| F05 | Graph corruption | Child re-attached to wrong parent after parent delete | DEL-004: deferred entry not evicted on parent delete | **YES** | Explicit eviction in HandleDelete |
| F06 | Graph corruption | Hierarchy event rejected after implicit detach | DEL-001: tracker incremented incorrectly | **YES** | Do NOT update tracker during implicit detach |
| F07 | Graph corruption | Child wrongfully detached by stale 0x04 | DEL-006: V3 handler lacks sequence tracker | No | Pre-existing; document limitation |
| F08 | Replay divergence | Delete during snapshot replay causes nondeterministic state | DEL-002: ambiguous CREATE blocking | **YES** | Resolve DEL-002 |
| F09 | Replay divergence | Same delete sequence after reconnect causes wrong state | Tracker cleared on reconnect (by design) | No | Snapshot authority |
| F10 | Memory safety | Tombstone grows unboundedly | Eviction at 2048 prevents this | No | ✅ Bounded |
| F11 | Memory safety | Deferred pending deletes accumulate | EndSnapshot clears all pending | No | ✅ Bounded |
| F12 | Observability | Silent tombstone eviction | DEL-015: no eviction counter | No | Add counter |
| F13 | Observability | Cannot verify child detachment count | DEL-014: counter ambiguity | No | Clarify counter semantics |
| F14 | Edge case | Delete + RecoverMissingActors cycles | DEL-016: recovery doesn't check tombstone | No | Add tombstone check to recovery |
| F15 | Edge case | Delete during ResolvePendingAttachments | Both on game thread; ordering is deterministic | No | Document |
| F16 | Edge case | Blender sends 0x04 + 0x0E for same delete | Dual detection paths (DEL-008) | No | Sequence tracker handles duplicate |
| F17 | Edge case | Suppression guard missing (non-callback path) | DEL-003: no suppression | **YES** | Add FScopedDeleteSuppression |
| F18 | Design | Proof §7.1 incomplete | DEL-011: doesn't address queued deletes | No | Extend proof |

### 10.2 P0 Classification Summary

| ID | Failure Mode | Determined P0? |
|----|-------------|----------------|
| DEL-001 | Hierarchy event silently dropped after parent delete | **YES** — prevents correct hierarchy sync for all children of deleted parents |
| DEL-002 | Actor resurrected by CREATE after delete during snapshot replay | **YES** — direct resurrection vector |
| DEL-003 | No suppression guard violates conventions; future callback risk | **YES** — mandatory lane requirement |
| DEL-004 | Child wrongfully re-attached via stale deferred entry | **YES** — graph corruption within 1-2 frames |

These 4 findings MUST be resolved before implementation proceeds.

---

## 11. Architecture Risk Summary

### 11.1 Risk Heat Map

```
                    Likelihood
              Low              Medium           High
    ┌─────────────────────────────────────────────────┐
    │                                                  │
High │ DEL-001 (P1)                                    │
    │ Hierarchy seq                                    │
    │ coupling                                         │
    │                                                  │
    │ DEL-004 (P1)        DEL-002 (P1)                 │
    │ Deferred eviction   CREATE tombstone             │
    │                    ◄────────────────►            │
Med  │ DEL-012            DEL-013                      │
    │ Deferred re-attach  Queued delete race            │
    │                                                  │
    │ DEL-005            DEL-006                       │
    │ Eviction chain     V3 compat gap                  │
    │                                                  │
    │ DEL-014            DEL-008                       │
    │ Counter ambiguity  Blender dedup                  │
    │                                                  │
Low  │ DEL-015            DEL-016                      │
    │ Eviction counter   Recovery check                 │
    │                                                  │
    └─────────────────────────────────────────────────┘
```

### 11.2 Risk by Category

| Category | Risk Level | Key Findings |
|----------|-----------|--------------|
| **Replay resurrection** | MEDIUM (resolvable) | DEL-002 must be fixed; all other vectors contained |
| **Tombstone correctness** | LOW | Well-designed; eviction safety proved |
| **Cross-lane invalidation** | HIGH (resolvable) | DEL-001, DEL-004 must be fixed; hierarchy interaction is the riskiest area |
| **GUID determinism** | LOW | Robust design; statistical guarantees sufficient |
| **Snapshot ordering** | MEDIUM (resolvable) | DELETE-before-CREATE during replay handled via deferral |
| **Queue safety** | LOW | All structures bounded; no unbounded growth |
| **Conventions compliance** | MEDIUM (resolvable) | DEL-003 must be fixed |

### 11.3 Overall Architecture Soundness

The Phase 6E architecture is **sound** at its core:
- 3-barrier stale packet rejection system is correctly designed
- Tombstone lifecycle is properly bounded
- Reconnect semantics are deterministic under snapshot authority
- GUID lifetime rules are comprehensive
- Frozen-runtime compatibility is confirmed (additive-only)

The 4 P1 findings are **not architectural flaws** — they are design-level errors in specific mechanism descriptions. Fixing them requires:
1. DEL-001: Remove the cross-lane coupling (don't update tracker) — simplifies the design
2. DEL-002: Clarify that CREATE is blocked by tombstone — unambiguous correctness
3. DEL-003: Add FScopedDeleteSuppression — trivial additive change
4. DEL-004: Add explicit deferred entry eviction in HandleDelete — targeted fix

---

## 12. Implementation Readiness Verdict

### 12.1 Prerequisite Resolution

Implementation MUST NOT begin until:

| Finding | Resolution Required | Verification |
|---------|-------------------|-------------|
| DEL-001 | Update §7.4 and §10.3: implicit detach must NOT update child's hierarchy tracker | Design doc review |
| DEL-002 | Resolve CREATE tombstone contradiction: Option A (block CREATE at tombstone) recommended | Design doc review |
| DEL-003 | Add FScopedDeleteSuppression to mandatory requirements in scope lock and design doc | Design doc review |
| DEL-004 | Add explicit deferred entry eviction for children of deleted parent to design | Design doc review |

### 12.2 Recommended Resolution Order

1. **DEL-001 first** — affects the design's core claim about cross-lane coupling
2. **DEL-002 second** — affects the fundamental tombstone behavior
3. **DEL-004 third** — depends on DEL-001's resolution (if tracker is NOT updated, explicit eviction is REQUIRED)
4. **DEL-003 last** — additive change, no design dependencies

### 12.3 Post-Resolution Verdict

Once all 4 P1 findings are resolved:

**READY FOR IMPLEMENTATION** ✅

The design is deterministic, replay-safe, reconnect-safe, graph-safe, bounded, and frozen-runtime-safe. The 4 P1 findings are specific, containable, and have clear resolution paths. No architectural redesign is required.

### 12.4 Residual Risk (Post-Fix)

| Risk | Severity | Acceptable? |
|------|----------|-------------|
| Tracker eviction reduces to single barrier | P2 | ✅ Yes — astronomically unlikely |
| V3 PT_Delete backward compat gap | P2 | ✅ Yes — pre-existing, documented |
| Queued delete race on disconnect | P2 | ✅ Yes — send queue clearing resolves |
| Transient wrong attachment (1-2 frames) | P2 | ✅ Yes — eventual consistency |
| Tombstone eviction counter missing | P3 | ✅ Yes — cosmetic |
| Counter naming ambiguity | P3 | ✅ Yes — cosmetic |
| Recovery tombstone check | P3 | ✅ Yes — edge case |

---

## Appendix A: Key Design Errors Found

| # | Document | Section | Error | Fix |
|---|----------|---------|-------|-----|
| 1 | 30-phase6E-vertical-slice-lifecycle.md | §7.4 | Claims cross-lane coupling REQUIRED; implicit detach must increment child's hierarchy seq | Must NOT update tracker; coupling unnecessary |
| 2 | 30-phase6E-vertical-slice-lifecycle.md | §3.5 table | "PT_Create — NO — let through (will be rejected by ActorCache existence check)" | Wrong on two counts: should block CREATE at tombstone; ActorCache doesn't reject CREATEs |
| 3 | 30-phase6E-vertical-slice-lifecycle.md | §2.3 | "Subsequent create packets for the same GUID in the same snapshot batch are discarded (tombstone check)" | Contradicts §3.5 table |
| 4 | 29-phase6E-lifecycle-scope-lock.md | §3.3 | "Suppression: No (no callback)" | Violates conventions §2.6 and §6.5 |
| 5 | 30-phase6E-vertical-slice-lifecycle.md | §6.3 | "No special handling needed" for child's deferred entry on parent delete | Explicit eviction needed |
| 6 | 30-phase6E-vertical-slice-lifecycle.md | §7.1 | Proof assumes no queued deletes survive reconnect | Extend proof with queued packet handling |

## Appendix B: Corrected Determinism Proofs

### B.1 Revised §7.1: Reconnect Determinism

**Original proof**: `S ⊆ B` — every actor in UE after snapshot was live in Blender.

**Addendum**: Queued delete packets from pre-disconnect do NOT interfere:
- Lemma 1: Blender's send queue is cleared on disconnect. No pre-disconnect delete packets are re-sent on the new connection.
- Lemma 2: Even if a queued delete packet arrives on the new connection (e.g., via application bug), the ActorCache is empty at connection start. The delete finds no target.
- Lemma 3: Snapshot builds from current `tracked_objects`, which excludes deleted objects. The snapshot is the sole source of actors.

### B.2 Revised §7.4: Hierarchy Invalidation Determinism

**Original claim**: Cross-lane coupling (increment child's hierarchy seq) is required.

**Corrected claim**: Cross-lane coupling is UNNECESSARY. The existing hierarchy tracker state (last genuine event's sequence) provides stale rejection:
- A stale hierarchy packet with seq ≤ N is rejected (N ≤ N → stale)
- A genuine new hierarchy packet with seq N+1 is accepted (N+1 > N)
- The implicit detach does NOT create a tracker entry

## Appendix C: Cross-Lane Coupling Audit

| Coupling Point | Design Claims | Actual Need | Status |
|---------------|---------------|-------------|--------|
| Delete → hierarchy: update child's tracker | REQUIRED | **NOT NEEDED** — harmful if implemented | DEL-001 |
| Delete → deferred queue: evict child's entries | NOT MENTIONED | **REQUIRED** — prevents transient wrong attachment | DEL-004 |
| Delete → suppression: RAII guard | NOT NEEDED | **REQUIRED** — conventions mandate | DEL-003 |
| Delete → V3 PT_Delete (0x04) | Backward compat | Sequence tracking gap | DEL-006 |

**Net cross-lane coupling count after fixes**: 1 (DEL-004: explicit deferred entry eviction). This is simpler than the design's claimed 1 coupling (which was DEL-001, now eliminated).

---

## Changelog

| Date | Version | Changes |
|------|---------|---------|
| 2026-05-26 | 1.0 | Initial threat audit — Phase 6E adversarial review. 16 findings (4 P1, 10 P2, 3 P3). Executive verdict: GO WITH BLOCKERS. Implementation requires DEL-001 through DEL-004 resolution first. |
