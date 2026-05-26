# Phase 6E — Design Remediation Summary

> **Created**: 2026-05-26
> **Status**: COMPLETE — all P1 findings resolved
> **Threat Audit**: `31-phase6E-lifecycle-threat-audit.md`
> **Modified**: `29-phase6E-lifecycle-scope-lock.md` · `30-phase6E-vertical-slice-lifecycle.md`
>
> This document records all 4 P1 findings from the threat audit and the
> exact remediations applied to resolve them. Implementation planning may
> proceed after this remediation pass.

---

## Remediation Summary

| Finding | Severity | Root Cause | Remediation | Resulting Architecture Change |
|---------|----------|------------|-------------|-------------------------------|
| **DEL-001** | P1 | Design mandated incrementing child's hierarchy tracker during parent-delete implicit detach | Removed ALL language requiring lifecycle lane to mutate hierarchy sequence tracker. Proved existing tracker state is self-sufficient. Updated §7.4 proof. | **ZERO cross-lane sequence coupling.** Cleaner architecture than originally claimed. |
| **DEL-002** | P1 | Contradictory tombstone policy: table said CREATE NOT blocked by tombstone, text said it IS | Unified to: CREATE IS blocked by tombstone. Added canonical truth table (§3.5). Added CREATE blocking to scope lock tombstone model. Snapshot replay defers delete to after EndSnapshot for consistency. | **Deterministic CREATE handling.** No ambiguity. |
| **DEL-003** | P1 | No suppression RAII guard for lifecycle lane — violates conventions §2.6 and §6.5 | Added `FScopedDeleteSuppression` to §3.3 of scope lock and §1.4 of vertical slice. Defined enter/exit logging. Added suppression to done criteria. | **Convention-compliant.** Matches rename, visibility, hierarchy patterns. |
| **DEL-004** | P1 | No explicit eviction of deferred hierarchy entries for children of deleted parent | Added §6.6 to vertical slice with complete eviction policy (3 categories: child, parent, indirect). Added to HandleDelete algorithm in §6.1. Updated §6.2 and §6.3. | **Deterministic deferred cleanup.** No transient wrong attachment. |

---

## Detailed Remediation Record

### DEL-001 — Remove Invalid Cross-Lane Coupling

**Affected documents**:
- `29-phase6E-lifecycle-scope-lock.md`: §5.1
- `30-phase6E-vertical-slice-lifecycle.md`: §6.1, §7.4, §10.3, §12.1 (Stage 7), Appendix A/B/C

**Changes made**:

| Change | Location |
|--------|----------|
| Removed "hierarchy sequence update on detach" from required additions | 30 §10.2 |
| Replaced cross-lane coupling section with "no coupling" section | 30 §10.3 |
| Rewrote §7.4 proof to show determinism WITHOUT coupling | 30 §7.4 |
| Added "No hierarchy sequence coupling" explanation to scope lock | 29 §5.1 |
| Added "No hierarchy sequence coupling" to algorithm description | 30 §6.1 |
| Removed Stage 7 (cross-lane coupling) from implementation plan | 30 §12.1 |
| Updated Appendix A: cross-lane coupling → "None" | 30 Appendix A |
| Updated Appendix B: removed coupling entry, added eviction entry | 30 Appendix B |
| Updated Appendix C: proof status → "PROVED (no coupling required)" | 30 Appendix C |
| Updated changelogs in both documents | 29 §13, 30 Changelog |

**Architecture impact**: **BENEFICIAL**. The design is simpler without the coupling.
The lifecycle lane and hierarchy lane are fully isolated. The threat audit
discovered that the coupling would have caused genuine hierarchy events to
be silently dropped — fixing it prevents a runtime bug.

**Correctness argument** (from §7.4):
```
Let N = last genuine hierarchy event's sequence for child C.
After parent delete, C's tracker still has seq=N (UNCHANGED).

Stale hierarchy packet for C→P with seq=S:
  S ≤ N ? → stale → REJECTED ✅ (tracker self-check)
  S > N ? → genuine new event → ACCEPTED ✅ (or tombstone-checked)

No update needed. The tracker is self-sufficient.
```

---

### DEL-002 — Fix Tombstone/CREATE Contradiction

**Affected documents**:
- `29-phase6E-lifecycle-scope-lock.md`: §3.5 (Tombstone Model)
- `30-phase6E-vertical-slice-lifecycle.md`: §3.5 (What Tombstones Block), §2.3, §2.4

**Changes made**:

| Change | Location |
|--------|----------|
| Changed CREATE row: "NO (let through)" → "YES (silently discard)" | 30 §3.5 |
| Updated rationale to mention snapshot replay deferral exception | 30 §3.5 |
| Added "CREATE blocked? Yes" row to tombstone model | 29 §3.5 |
| Updated §2.3 to clarify deferral + tombstone interaction | 30 §2.3 |
| Fixed §2.4 ordering table (Delete → Create is now CORRECT) | 30 §2.4 |
| Updated §1.4 delete semantics: added CREATE blocked row | 30 §1.4 |

**Architecture impact**: **NECESSARY**. Without this fix, an implementation
following the table would allow resurrection. The batch-parsing concern
(tombstone blocked CREATE breaking batch parsing) is moot because the
packet is still parsed — it's just not applied.

**Canonical tombstone truth table** (from remediated §3.5):

| Packet Type | Blocked by Tombstone? | Rationale |
|-------------|----------------------|-----------|
| `PT_Transform` (0x01) | **Yes** | Actor no longer exists |
| `PT_Create` (0x03) | **Yes** | GUID was deleted; recreation would be resurrection. Exception: snapshot replay defers deletes to after EndSnapshot |
| `PT_Delete` (0x04 / 0x0E) | **Yes** | Already destroyed |
| `PT_AssetDef` (0x08) | **Yes** | No actor to apply asset to |
| `PT_Visibility` (0x0B) | **Yes** | No actor to toggle |
| `PT_Rename` (0x0C) | **Yes** | No actor to rename |
| `PT_Hierarchy` (0x0D) | **Yes** | No actor to attach/detach |

---

### DEL-003 — Add Suppression Semantics

**Affected documents**:
- `29-phase6E-lifecycle-scope-lock.md`: §3.3, §3.7, §10
- `30-phase6E-vertical-slice-lifecycle.md`: §1.4, §9, §10.2, Appendix A

**Changes made**:

| Change | Location |
|--------|----------|
| Added "Suppression: FScopedDeleteSuppression RAII guard" to UE-Side Application table | 29 §3.3 |
| Added DeleteSuppressionEnter counter to observability | 29 §3.7 |
| Added suppression to done criteria (item 9) | 29 §10 |
| Added FScopedDeleteSuppression to delete semantics | 30 §1.4 |
| Added [DELETE][SUPPRESS] log prefix | 30 §9.2 |
| Added FScopedDeleteSuppression to required additions | 30 §10.2 |
| Changed Appendix A: "No (no callback)" → "Yes (pattern)" | 30 Appendix A |

**Suppression specification**:
- **Guard name**: `FScopedDeleteSuppression`
- **Scope**: Wraps the entire `HandleDelete()` destroy path, from detach cascade through `Actor->Destroy()`
- **Enter log**: `[DELETE][SUPPRESS] Enter suppression scope (GUID=%s)` (Verbose)
- **Exit log**: `[DELETE][SUPPRESS] Exit suppression scope (GUID=%s)` (Verbose)
- **Rationale**: Pattern-conformance per conventions §2.6 and §6.5. Although `DestroyActor` currently fires no standard callback that would re-trigger replication, suppression is required for future-proofing and grep-ability.
- **No callback risk**: Delete has no callback risk from DestroyActor. Suppression is present for consistency, not functional necessity.

---

### DEL-004 — Add Deferred Hierarchy Entry Eviction

**Affected documents**:
- `29-phase6E-lifecycle-scope-lock.md`: §5 (all subsections)
- `30-phase6E-vertical-slice-lifecycle.md`: §6 (all subsections), §6.6 (new), §9, §10.2

**Changes made**:

| Change | Location |
|--------|----------|
| Added §5.5 "Deferred Hierarchy Entry Eviction Policy" | 29 §5 |
| Updated §5.1 to include "Child's pending deferred entries evicted" | 29 §5.1 |
| Updated §5.2 to use explicit eviction | 29 §5.2 |
| Updated §5.3 to cover both child-role and parent-role eviction | 29 §5.3 |
| Added §6.6 "Deferred Hierarchy Entry Eviction Policy" | 30 §6 |
| Updated §6.1 algorithm to include eviction step | 30 §6.1 |
| Rewrote §6.2 and §6.3 to use explicit eviction | 30 §6.2–§6.3 |
| Added DeleteDeferredEvictions counter | 30 §9.1 |
| Added [DELETE][EVICT] log prefix | 30 §9.2 |
| Added deferred entry eviction to required additions | 30 §10.2 |

**Eviction policy summary** (from §6.6):

| Role | Eviction Trigger | What Gets Removed |
|------|-----------------|-------------------|
| **Child** | Actor D deleted | `Entry.ChildGuid == D` |
| **Parent** | Actor D deleted | `Entry.ParentGuid == D` |
| **Indirect** | Parent P deleted, child C was attached to P | `Entry.ChildGuid == C` (for each child of deleted parent) |

**Why explicit, not deferred**: FINDING-001 re-validation (sequence re-check
in ResolveHierarchyAttachments) is insufficient because the hierarchy tracker
is NOT updated by the implicit detach (DEL-001 resolution). A deferred entry's
sequence may appear fresh relative to the unchanged tracker, causing transient
wrong attachment. Explicit removal guarantees correctness within the same Tick.

---

## Remaining Risks

After remediation, the following P2/P3 risks remain:

| Risk | Category | Acceptable? | Notes |
|------|----------|-------------|-------|
| Tracker eviction reduces to single-barrier ActorCache | P2 | ✅ Yes | Requires UUID collision + 2048 evictions |
| V3 PT_Delete (0x04) backward compat gap | P2 | ✅ Yes | Pre-existing; documented |
| Queued delete race on disconnect (Blender send queue clearing) | P2 | ✅ Yes | TCP boundary + eventual consistency |
| Transient wrong attachment (1-2 frames via non-evicted deferred path edge cases) | P2 | ✅ Yes | All known paths now explicitly evicted |
| No tombstone eviction counter initially specified | P3 | ✅ Yes | Added during remediation |

All 4 P1 findings are closed. The remaining P2 risks are containable and
documented.

---

## Verification

The following verifications confirm all remediations were applied:

- [x] `29-phase6E-lifecycle-scope-lock.md`: 403 lines (was 375) — added suppression, deferred eviction, tombstone CREATE rule
- [x] `30-phase6E-vertical-slice-lifecycle.md`: 1078 lines (was 1034) — §7.4 rewritten, §6.6 added, §10.3 rewritten, Appendix A/B/C updated
- [x] No remaining references to "hierarchy sequence coupling" in lifecycle documents
- [x] No remaining references to "cross-lane coupling" as a Phase 6E feature
- [x] CREATE tombstone blocking is unambiguous (both table and prose agree)
- [x] Suppression RAII guard defined in both documents
- [x] Deferred entry eviction policy defined with all three categories
- [x] Implementation plan no longer includes Stage 7 (removed cross-lane coupling stage)

---

## Final Verdict

**GO FOR IMPLEMENTATION PLANNING.**

All 4 P1 findings (DEL-001 through DEL-004) from the threat audit are resolved.
The architecture is:

- **Deterministic**: §7 proofs all updated and consistent
- **Replay-safe**: 3-barrier system intact; CREATE tombstone blocking unambiguous
- **Reconnect-safe**: Snapshot authority confirmed; no cross-connection stale leaks
- **Tombstone-consistent**: Canonical truth table with no contradictions
- **Cross-lane-safe**: Zero sequence coupling between lifecycle and hierarchy
- **Convention-compliant**: FScopedDeleteSuppression added per conventions §2.6

**What implementation planning must cover next**:
1. Stage 0: PT_Delete = 0x0E constant registration (SyncTypes.h + network.py)
2. Stage 1-2: FDeleteSequenceTracker + parser branch
3. Stage 3: Replay rejection layer
4. Stage 4: Tombstone map (bounded 2048 LRU)
5. Stage 5: HandleDelete with suppression + basic destroy
6. Stage 6: Parent-delete-child-detach cascade with deferred entry eviction
7. Stage 7: Blender detection + serialization
8. Stages 8-13: Tests, soak, stabilization

**Implementation must still observe frozen-runtime boundaries**: no
LiveSyncQueue, PendingAssetQueue, LiveSyncRunnable, FSyncTransformState,
Tick ordering, or AttachToParent/DetachFromParent modifications.

---

## Changelog

| Date | Version | Changes |
|------|---------|---------|
| 2026-05-26 | 1.0 | Initial remediation summary. Records all 4 P1 finding resolutions from the Phase 6E threat audit. Verdict: GO FOR IMPLEMENTATION PLANNING. |
