# Phase 6E — Lifecycle/Delete Replication Scope Lock

> **Created**: 2026-05-26
> **Status**: PLANNING — NOT IMPLEMENTED
> **Predecessors**: Rename (STABILIZED, Phase 6A/6B · `0x0C`) · Visibility (STABILIZED, Phase 6C · `0x0B`) · Hierarchy (STABILIZED, Phase 6D · `0x0D`)
> **Next**: Vertical Slice Design (`30-phase6E-vertical-slice-lifecycle.md`)
> **Implementation**: BLOCKED — requires demonstrated reconnect determinism, replay determinism, GUID lifetime determinism, and hierarchy invalidation determinism in planning docs.
>
> This document defines the **hard scope boundaries** for the fourth Phase 6
> semantic-event vertical slice: lifecycle/delete replication.
>
> Lifecycle/delete is the **highest-risk semantic lane so far** — it is the
> first identity-destruction event, introducing tombstone semantics, replay
> resurrection prevention, GUID lifetime rules, and irreversible graph
> destruction behavior. Unlike rename, visibility, and hierarchy (all
> reversible property mutations), delete is a **terminal event**.

---

## 1. Purpose

Lifecycle/delete replication is the fourth minimal editor-authority workflow.
It replicates **object destruction intent** from Blender to the Unreal Editor:
when Blender deletes a mesh object, the corresponding UE actor is destroyed.

### Why Delete After Hierarchy

| Criterion | Rename | Visibility | Hierarchy | **Lifecycle/Delete** |
|-----------|--------|------------|-----------|----------------------|
| **Mutation scope** | Single object | Single object | Object pair | **Object + dependents** |
| **Reversibility** | Reversible | Reversible | Reversible | **Irreversible** |
| **Dependency sensitivity** | None | None | Required | **Maximum** — cascading |
| **Replay complexity** | Low | Low | Medium/High | **High** — resurrection risk |
| **Tombstone required** | No | No | No | **Likely required** |
| **Graph invalidation** | None | None | None | **Orphan cascade, detach cascade** |
| **GUID reuse risk** | None | None | None | **High** — deleted GUID must not resurrect |
| **Reconnect determinism** | Trivial | Trivial | Medium | **Hard** — was it deleted during disconnect? |
| **Existing runtime support** | None | None | Existing attach APIs | **`DestroyActor` exists but must be wrapped** |

Delete is architecturally distinct from all prior lanes in one critical way:
**every prior lane assumes the object/actor continues to exist**. Rename changes
a label on a live object. Visibility toggles a flag on a live object. Hierarchy
re-parents a live object. Delete destroys the object — and with it, all state
associated with that GUID.

### Key Insight: Delete Is Not Detach

Delete is **NOT**:
- A detach-to-root (the object is gone, not just parentless)
- A visibility hide (the object is gone, not just invisible)
- An orphan timeout (the orphan is self-healing; deletion is terminal)

Delete is fundamentally different because it **destroys the authority target**.
After delete:
- No more transform packets for that GUID are meaningful
- No more rename packets for that GUID are meaningful
- No more hierarchy packets for that GUID are meaningful
- No more visibility packets for that GUID are meaningful
- Any stale packet for a deleted GUID must be **silently discarded**

---

## 2. Risk Classification

| Risk | Severity | Likelihood | Impact | Mitigation |
|------|----------|-----------|--------|------------|
| **Replay resurrection** | CRITICAL | High | Deleted actor recreated by stale snapshot replay | Tombstone barrier; clear tombstones on reconnect |
| **Reconnect resurrection** | CRITICAL | Medium | Actor deleted while disconnected, resurrected on reconnect | Snapshot-authority negotiation; user choice |
| **Stale delete replay** | HIGH | Medium | Delete packet replayed after object was recreated | Sequence tracker + tombstone cross-check |
| **Duplicate delete crash** | HIGH | Low | `DestroyActor` called on already-destroyed actor | Existence check before destroy |
| **Parent-deletes-child ordering** | HIGH | Medium | Parent destroyed before child during reconnect replay | Depth-sort deletion (children before parents) |
| **GUID reuse collision** | HIGH | Low | Blender reuses a hex GUID for a new object | `ensure_unique_guid` collision detection |
| **Tombstone memory leak** | MEDIUM | Medium | Tombstone map grows unboundedly | Bounded LRU tombstone map |
| **Hierarchy orphan drift** | MEDIUM | Medium | Orphan's parent deleted while deferred | Orphan eviction on parent delete |
| **Transform-after-delete** | LOW | High | Transform packet arrives for deleted actor | Silence — must not crash |
| **Delete during snapshot replay** | MEDIUM | Low | Delete packet interleaved with create during snapshot | Delete in snapshot = stable state; must not corrupt |

---

## 3. IN SCOPE

### 3.1 Delete Intent Packet (PT_Delete)

| Field | Value |
|-------|-------|
| **Packet type** | `PT_Delete = 0x0E` (next available, see §8.2 of semantic conventions) |
| **Direction** | Blender → UE only (editor-authority delete is deferred — see OUT OF SCOPE) |
| **Semantics** | Replicates object destruction: Blender deleted a MESH object → UE destroys corresponding actor |
| **Wire format** | Fixed: `GUID(16) + seq(4) + ts(8)` — **28 bytes fixed per object** |
| **Event type** | Discrete terminal semantic mutation — NOT reversible, NOT a state stream |
| **Scope** | MESH objects only (existing object filter). Cameras, lights, armatures excluded. |

### 3.2 Blender-Side Detection

| Item | Description |
|------|-------------|
| Change detection | Existing `ReferenceError` catch in `sync.py` `check_updates()` loop — when `obj.name` raises `ReferenceError`, the object was deleted. Currently this emits `serialize_delete_v3` (PT_Delete = 0x04). **Phase 6E must replace or augment this path.** |
| Detection coverage | Two paths: (1) immediate `ReferenceError` catch during `tracked_objects` iteration, (2) periodic `scan_scene()` diff that detects objects removed from `bpy.data.objects` |
| Scope | Same MESH-only filter |
| First-sync behavior | Deleted objects are NOT emitted during first sync or snapshot — delete is a delta event only |
| Batch coalescing | Multiple deletes in a single frame should be batched into one `send_objects` call |
| Sequence tracking | Per-GUID monotonic sequence counter (`_delete_sequences`), cleared on disconnect |

### 3.3 UE-Side Application

| Item | Description |
|------|-------------|
| Handler | `HandleDelete(FGuid TargetGuid, uint32 Seq, double Timestamp)` |
| Provenance | `EChangeOrigin::RemoteReplicated` (normal) / `EChangeOrigin::Replay` (snapshot replay — but deletes in snapshot replay are exceptional) |
| Existence check | Target must exist in `ActorCache` before destroying. If not found, the delete is **already committed** — silently discard. |
| Sequence check | Stale/duplicate delete rejection via `FDeleteSequenceTracker` |
| Tombstone check | After successful destroy, record GUID in tombstone map. Stale delete packets for tombstoned GUIDs are silently discarded. |
| Destroy method | `Actor->Destroy()` via game thread. NOT `Actor->K2_DestroyActor()` (requires `bNetStartup` check). |
| Children handling | Children of a deleted parent are **detached to root** before parent destroy. NOT recursively deleted — hierarchy scope lock forbids lifecycle cascade. |
| Log prefix | `[DELETE]` |
| Suppression | `FScopedDeleteSuppression` RAII guard wrapping `Actor->Destroy()`. Pattern-conformance — no standard callback risk from `DestroyActor`, but required by semantic-event conventions (§2.6, §6.5) for all lanes. Suppression enters before detach cascade, exits after destroy. Verbose logging on enter/exit. |
| Profiler scopes | `UELiveSync_HandleDelete`, `UELiveSync_ProcessDeletePackets` |

### 3.4 Replay Semantics

| Aspect | Behavior |
|--------|----------|
| Sequence tracker | `FDeleteSequenceTracker` — bounded 2048, stale/duplicate rejection via `<=` |
| Snapshot replay | Delete packets during snapshot replay are **generally not expected** (snapshot is built from live scene, and deleted objects are absent). If a delete packet arrives during snapshot build, it is tagged `EChangeOrigin::Replay` and processed normally. |
| Stale rejection | Same pattern as all prior lanes: `IncomingSeq <= LastSeq` → reject |
| Reconnect | Tracker cleared in `StopNetworkThread()`, `ConsoleReset()`, Blender disconnect. **Tombstone map is also cleared on reconnect.** |
| Tombstone clearing | On reconnect, the tombstone map is cleared. This means any GUID that was deleted before disconnect is now **eligible for resurrection** via the reconnect snapshot. This is intentional: the snapshot IS the authoritative state, and if the object is absent from the snapshot, it stays dead. |

### 3.5 Tombstone Model

| Aspect | Decision | Rationale |
|--------|----------|-----------|
| Tombstone required | **Yes**, bounded | Without tombstones, a stale replay packet can resurrect a deleted actor |
| Bounding strategy | LRU eviction at 2048 entries | Same as all other bounded structures in the system |
| Tombstone duration | Lifetime of connection + one `EndSnapshot` boundary | Tombstones persist across snapshot replay, cleared on reconnect |
| Tombstone key | `FGuid` (16 bytes) | Same key as all other state maps |
| Storage | `TMap<FGuid, uint32>` — GUID to last delete sequence | Sequence value enables LRU ordering and diagnostic DumpState |
| Reset trigger | `StopNetworkThread`, `ConsoleReset` | Same as all sequence trackers |
| Race condition | Network thread writes tombstone, game thread reads | Protected by the same queue ownership: game thread both processes packets and manages tombstones. Network thread never touches tombstones. |
| CREATE blocked? | **Yes** — PT_Create for a tombstoned GUID is discarded | Prevents actor creation after delete within the same connection. Override: snapshot replay defers deletes to after EndSnapshot (§2.4 of vertical slice), so CREATEs in the same snapshot batch are processed first. |

### 3.6 GUID Lifetime Rules

| Rule | Rationale |
|------|-----------|
| Deleted GUIDs are silent | Any packet (transform, rename, visibility, hierarchy) for a deleted GUID is silently discarded. No crash, no warning storm. |
| Deleted GUIDs can reappear after reconnect | Reconnect clears tombstones. If the object still exists in Blender's scene, it will be recreated via the snapshot. This is correct. |
| GUID reuse within a session | Blender's `ensure_unique_guid()` prevents intra-session collision. Cross-session GUID reuse is possible but irrelevant (tombstones cleared on disconnect). |
| GUID reuse from Object.copy() | Existing `ensure_unique_guid()` in `__init__.py` detects and reassigns GUIDs of copied objects. No change needed. |
| Orphan hierarchy after delete | Child actors of a deleted parent are **detached to root** before parent destroy. They continue to exist as independent actors. |

### 3.7 Observability Requirements

| Requirement | Count | Description |
|-------------|-------|-------------|
| Log prefix | `[DELETE]` | All delete-related logs use this prefix |
| Counter: DeletesProcessed | 1 | Number of delete packets applied (actor destroyed) |
| Counter: DeleteStaleRejections | 1 | Stale/duplicate delete packets rejected |
| Counter: DeleteReplayApplied | 1 | Delete applied during snapshot replay |
| Counter: DeleteReplaySkipped | 1 | Delete skipped during snapshot replay |
| Counter: DeleteTombstoneHits | 1 | Delete packet for already-tombstoned GUID |
| Counter: DeleteChildrenDetached | 1 | Children detached from parent before parent destroy |
| Counter: DeleteDeferredEvictions | 1 | Deferred hierarchy entries evicted due to parent delete |
| Counter: DeleteSuppressionEnter | 1 (verbose) | Suppression scope entered |
| Counter: DeleteTombstoneEvictions | 1 | Tombstone entry evicted at 2048 capacity |
| Profiler scope | `UELiveSync_HandleDelete` | CPU profiler scope for delete handler |
| Profiler scope | `UELiveSync_ProcessDeletePackets` | CPU profiler scope for delete batch processing |
| Verbose logging | Existence checks, tombstone hits, stale rejections, suppression enter/exit | Behind `UE.LiveSync.Verbose` CVar |

### 3.8 Packet Number Reservation

| Constant | Value | Status |
|----------|-------|--------|
| `PT_Delete = 0x0E` | 0x0E | Reserved — NOT implemented |

This follows the sequential allocation pattern established by all prior lanes.
0x0E is the next available packet type after `PT_Hierarchy = 0x0D`.

---

## 4. OUT OF SCOPE

### 4.1 Editor-Authority Delete (UE → Blender)

| Item | Rationale |
|------|-----------|
| Deleting actors in UE Viewport | Would require UE → Blender delete packet, GUID reverse-lookup, Blender-side `bpy.data.objects.remove()`. Requires bidirectional authority framework — explicitly deferred. |
| Blueprint-level destroy | `K2_DestroyActor` from gameplay code — out of scope for editor sync. |
| Level/streaming delete | World Composition, Level Instancing, sub-level delete — separate system entirely. |

### 4.2 Recursive/Collection Delete

| Item | Rationale |
|------|-----------|
| Delete parent → delete children cascade | This would destroy actors that were created by Blender. Children are **detached to root**, not deleted. Recursive delete is a user-facing feature that requires UI confirmation and is deferred to Phase 6F/6G (collection/duplicate). |
| Delete collection → delete members | Collection parenting is completely different from scene-graph parenting. Deferred until collection lane. |
| Bulk multi-select delete | Single-object delete only. Batch delete is a UI optimization, not a semantic change. |

### 4.3 Tombstone Full Persistence

| Item | Rationale |
|------|-----------|
| Disk-backed tombstone log | Would persist across editor restarts. Over-engineering for the current use case. If a user restarts the editor, the reconnect snapshot handles state reconciliation. |
| Per-user tombstone ACL | Not applicable — single-user editor scenario. |
| Tombstone audit trail | Counter values are sufficient. Full event log would be destructive for diagnostics history size. |

### 4.4 Lifecycle Systems Beyond Delete

| Item | Rationale |
|------|-----------|
| Object creation from UE | Blender-authority only. UE-side create (e.g., spawn from Blueprint) is not replicated to Blender. |
| Duplicate detection | GUID collision detection exists but duplicate-creation-as-semantic-event is deferred. |
| Undo/redo integration | Blender's undo stack is separate from UE's. Building a two-way undo bridge is out of scope. |

---

## 5. Critical Constraint: Hierarchy Interaction

Delete has **cascading correctness requirements** with the hierarchy lane:

### 5.1 Delete Parent With Children

When a parent actor is deleted and it has children:
1. Children are **detached to root** before parent destroy
2. Children continue to exist as independent actors
3. Children's `FSyncTransformState.bHasParent` is updated to `false`
4. Children's `FSyncTransformState.ParentGuid` is cleared to zero
5. No `PT_Hierarchy` event is emitted for this detachment (it is an implicit consequence of delete, not a user-intended re-parent)

**Why no hierarchy event**: If a hierarchy packet were emitted for each
detached child, the replay sequence would be non-deterministic (the sequence
of child detachments depends on the order of children in the parent's child
list, which may differ between runs). Implicit detachment is silent.

**No hierarchy sequence coupling**: The implicit detachment does NOT update
the child's hierarchy sequence tracker. The existing tracker state (the last
genuine hierarchy event's sequence) already rejects stale hierarchy packets
via the `<=` check. A stale hierarchy packet for C→P has seq ≤ N (the last
genuine event) and is correctly rejected. Blender's next genuine hierarchy
event for C will have seq N+1 and be accepted normally. See threat audit
finding DEL-001 (31-phase6E-lifecycle-threat-audit.md §3.1).

**Child's pending deferred entries evicted**: For each child of the deleted
parent, any pending entry in `PendingHierarchyAttachments` targeting any
parent is explicitly removed. This prevents stale deferred resolution from
re-attaching the child to an incorrect parent (see §5.5).

### 5.2 Delete Orphaned Actor

Deleting an orphan (an actor in the deferred hierarchy queue whose parent
has not yet arrived) is safe:
1. Remove the orphan from the deferred queue explicitly
2. Destroy the actor normally
3. Any remaining deferred entry referencing the orphaned actor as child
   is evicted explicitly

### 5.3 Delete During Deferred Hierarchy Resolve

If a delete packet arrives for an actor that is listed as a **child** in a
pending deferred hierarchy entry:
1. The actor is destroyed
2. The pending deferred entry for that child is evicted explicitly

If a delete packet arrives for an actor that is listed as a **parent** in a
pending deferred hierarchy entry:
1. The actor is destroyed
2. The pending deferred entries for all children targeting this parent are
   evicted explicitly (see §5.5)
3. The child actors continue to exist as orphaned roots

### 5.4 Hierarchy Event After Delete

If a hierarchy packet arrives for a GUID that has been deleted:
1. Silently discard (same as all post-delete packets)
2. The tombstone check handles this

### 5.5 Deferred Hierarchy Entry Eviction Policy

When a parent actor is deleted, NONE of its children's pending deferred
hierarchy entries are valid — the parent no longer exists. Explicit eviction
is required for three categories:

| Category | Eviction Trigger | Mechanism |
|----------|-----------------|-----------|
| **Child of deleted parent** — child C has a deferred entry awaiting parent X, AND C was a child of the now-deleted parent P | Parent P deleted → evict C's deferred entry | `HandleDelete()` iterates children → for each child, removes any entry in `PendingHierarchyAttachments` where `Entry.ChildGuid == ChildGuid` |
| **Deleted actor as child in deferred entry** — deleted actor D was listed as a child in a pending deferred entry | Actor D deleted → evict D's deferred entry | `HandleDelete()` removes any entry where `Entry.ChildGuid == TargetGuid` before destroying actor |
| **Deleted actor as parent in deferred entry** — deleted actor D was listed as a parent in a pending deferred entry | Actor D deleted → evict all entries referencing D as parent | `HandleDelete()` removes any entry where `Entry.ParentGuid == TargetGuid` |

**Why explicit eviction is required**: The alternative (relying on FINDING-001
re-validation during `ResolveHierarchyAttachments`) fails when the child's
hierarchy sequence tracker is NOT updated by the implicit detach (see §5.1).
The deferred entry's sequence would pass the stale check and the child would
be transiently attached to the wrong parent. Explicit eviction guarantees
deterministic behavior within the same Tick.

**Observability**:
- Counter: `DeleteChildrenDetached` incremented per child evicted from deferred queue
- Log: `[DELETE][DETACH] Evicted deferred entry: child=%s parent=%s (reason=parent %s deleted)`

**Memory safety**: The deferred hierarchy queue is bounded at 2048 entries
(Phase 6D invariant). Eviction reduces the count. No unbounded growth
possible.

---

## 6. Critical Constraint: Transform Pipeline Interaction

| Scenario | Behavior |
|----------|----------|
| Transform packet arrives after destroy | Silently discarded — actor not in `ActorCache` |
| Transform packet arrives for GUID that was deleted and recreated by snapshot during reconnect | Works correctly — actor was recreated, `ActorCache` has new entry |
| Delete during InterpolateTransforms | Deleting an actor that is currently being interpolated: safe if the mutex/critical section is respected. The Tick pipeline owns actor access during interpolation. Delete is processed in `ProcessQueuedPackets`, which runs BEFORE `InterpolateTransforms` in the Tick pipeline. A delete packet processed during one tick means the actor is gone before interpolation runs. |
| Delete during ResolvePendingAttachments | Same as above — delete is processed before ResolvePendingAttachments in Tick ordering. If the actor being deleted is also pending attachment, the attachment resolver will find no actor and skip. |

---

## 7. Critical Constraint: Frozen Runtime

| System | Frozen? | Phase 6E Interaction | Allowed Change? |
|--------|---------|---------------------|-----------------|
| `LiveSyncQueue` | FROZEN | Network thread enqueues `FLiveSyncPacket` with `PT_Delete` payload | **NO** |
| `PendingAssetQueue` | FROZEN | Delete packet processing is entirely separate | **NO** |
| `LiveSyncRunnable` | FROZEN | No changes to thread lifecycle | **NO** |
| `FSyncTransformState` | FROZEN | Delete clears ActorCache entry — no struct modification | **NO** |
| Tick ordering | FROZEN | Delete processing in ProcessQueuedPackets (existing slot) | **NO** |
| Transform interpolation | FROZEN | Delete removes actor — no interpolation change | **NO** |
| `AttachToParent()` / `DetachFromParent()` | FROZEN | Detach cascade uses raw `DetachFromActor` | **NO** |
| `ResolvePendingAttachments()` | FROZEN | No changes to existing resolution logic | **NO** |
| `RecoverMissingActors()` | FROZEN | Must check tombstone map before recreating an actor (see DEL-016) | **Tombstone check additive only** |

**Phase 6E must not modify any frozen system.** All delete logic must be
additive: new handler function, new packet processing branch, new counter map,
new sequence tracker, new tombstone map, new suppression guard. Same pattern
as rename, visibility, and hierarchy.

---

## 8. Rollback Criteria

Implementation must be rolled back if ANY of the following are detected:

| Criterion | Detection Method | Severity |
|-----------|-----------------|----------|
| Delete packet resurrects an actor | Live test: send delete, then resend same delete packet — must be rejected | CRITICAL |
| Deleted actor's actor is recreated by stale snapshot replay | Reconnect test: delete actor, reconnect — actor must stay dead | CRITICAL |
| Children of deleted parent lose attachment incorrectly | Hierarchy test: verify children are roots after parent delete | HIGH |
| Transform packet for deleted GUID causes crash | Fuzz test: send transform after delete — must silently discard | CRITICAL |
| Tombstone map grows without bound | Counter test: verify eviction at 2048 | HIGH |
| Sequence tracker leaks deleted GUIDs | Memory test: verify bounded at 2048 | MEDIUM |
| Delete during snapshot replay causes corruption | Replay test: interleave delete with create packets | CRITICAL |
| Reconnect snapshot recreates deleted object | Integration test: delete, reconnect, verify object absent | CRITICAL |

---

## 9. Implementation Prerequisites

Before Phase 6E can begin implementation, the following must be **proved**
in the planning/design docs (this document and the vertical slice design):

| Prerequisite | Proof Required | Section |
|-------------|----------------|---------|
| Reconnect determinism | Formal analysis showing snapshot replay cannot resurrect a deleted actor | §3.5 (Tombstone Model), §3.4 (Replay Semantics) |
| Replay determinism | Formal analysis showing stale delete packet rejection works correctly across all packet orderings | §3.4 (Replay Semantics) |
| GUID lifetime determinism | Formal analysis showing GUID lifecycle rules prevent all resurrection, collision, and stale-corruption scenarios | §3.6 (GUID Lifetime Rules) |
| Hierarchy invalidation determinism | Formal analysis showing parent-delete-with-children produces correct, deterministic actor state | §5 (Hierarchy Interaction) |
| Frozen-runtime compatibility | Formal audit showing zero modifications to all frozen systems | §7 (Frozen Runtime) |

---

## 10. Done Criteria

Phase 6E is **done** when:

1. `PT_Delete = 0x0E` packet is defined and registered in protocol constants
2. `FDeleteSequenceTracker` is implemented (bounded 2048, stale/duplicate rejection)
3. Tombstone map is implemented (bounded 2048, LRU eviction, cleared on reconnect)
4. Blender-side delete detection emits `PT_Delete` packets (augment or replace existing PT_Delete = 0x04 path)
5. UE-side `HandleDelete()` destroys the actor and records tombstone
6. Children of deleted parent are detached to root before parent destroy
7. Deferred hierarchy entries for children of deleted parent are explicitly evicted
8. All post-delete packets for deleted GUID are silently discarded
9. `FScopedDeleteSuppression` RAII guard wraps destroy path
10. 8 observability counters exist and are wired (including DeleteDeferredEvictions, DeleteTombstoneEvictions)
11. 2 profiler scopes exist and are wired
10. FNV protocol signature includes `0x0E`
11. All standalone tests pass
12. All integration tests pass (requires UE Editor)
13. No frozen-runtime modifications
14. No Phase 5 regressions
15. No editor crashes during 10-minute mixed soak (delete + transform + rename + visibility + hierarchy + reconnect)

---

## 11. Complexity Classification

| Dimension | Rating | Rationale |
|-----------|--------|-----------|
| **Packet complexity** | Low | 28-byte fixed payload (simpler than hierarchy's 44) |
| **Replay complexity** | High | Resurrection prevention requires tombstones + sequence tracking |
| **Reconnect complexity** | High | Snapshot must not resurrect; tombstone cleared on reconnect (intentional) |
| **Hierarchy coupling** | MEDIUM | Parent-delete-children handling with deferred entry eviction; no sequence coupling |
| **Transform coupling** | Medium | Post-delete silences; delete-in-Tick ordering analysis |
| **Pipeline coupling** | Medium | Delete in ProcessQueuedPackets before InterpolateTransforms |
| **Existing runtime coupling** | LOW | Fully additive — no frozen-zone modifications required |
| **Testing complexity** | HIGH | Replay, reconnect, ordering, cascade, fuzz — significant test surface |

**Overall**: HIGH complexity — comparable to hierarchy, with higher reconnect
stakes due to irreversibility.

---

## 12. Reference Documents

| Document | Relationship |
|----------|-------------|
| `18-phase6-scope-lock.md` | Phase 6 master scope — §3.4 references lifecycle/delete as deferred |
| `24-phase6D-hierarchy-scope-lock.md` | Hierarchy scope lock — delete interaction with parent-child defined |
| `25-phase6D-vertical-slice-hierarchy.md §12` | Lifecycle/Delete dependency warning |
| `22-semantic-event-architecture-conventions.md` | Canonical conventions — all §2 mandatory requirements apply to delete |
| `12-core-runtime-invariants.md` | Frozen runtime invariants |
| `16-known-safe-modification-zones.md` | SAFE/CAUTION/HIGH-RISK/FROZEN modification zones |

---

## 13. Changelog

| Date | Version | Changes |
|------|---------|---------|
| 2026-05-26 | 1.0 | Initial scope lock — Phase 6E planning. Defines delete/lifecycle scope, tombstone model, GUID lifetime rules, hierarchy interaction policy, frozen-runtime audit, risk matrix, and done criteria. Implementation BLOCKED until reconnect/replay/GUID determinism proved in planning. |
| 2026-05-26 | 1.1 | Design remediation (DEL-001 through DEL-004): removed invalid hierarchy sequence coupling; added FScopedDeleteSuppression RAII guard; added explicit deferred hierarchy entry eviction on parent delete; unified tombstone storage to TMap<FGuid,uint32>; added CREATE-blocked-by-tombstone rule; enhanced observability counters (DeleteDeferredEvictions, DeleteTombstoneEvictions); added tombstone check to RecoverMissingActors. |
