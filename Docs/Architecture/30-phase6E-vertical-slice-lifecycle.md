# Phase 6E — Lifecycle/Delete Replication: Vertical Slice Design

> **Created**: 2026-05-26
> **Status**: PLANNING — NOT IMPLEMENTED
> **Scope Lock**: `29-phase6E-lifecycle-scope-lock.md`
> **Predecessors**: Rename (`0x0C` · STABILIZED) · Visibility (`0x0B` · STABILIZED) · Hierarchy (`0x0D` · STABILIZED)
> **Implementation**: AUTHORIZED — all P1 findings resolved (§32-phase6E-remediation-summary.md)
>
> This document defines the **complete vertical slice design** for the fourth
> Phase 6 semantic-event lane: lifecycle/delete replication. It is the first
> **identity-destruction semantic lane** — introducing tombstone semantics,
> replay resurrection prevention, GUID lifetime rules, and irreversible graph
> destruction.
>
> **This is a design document, NOT an implementation specification.**
> No runtime code has been modified. No parser branches have been added.
> No packet handlers exist. No Tick pipeline changes are proposed.

---

## Table of Contents

1. [Packet Definition](#1-packet-definition)
2. [Replay Dependency Chain Analysis](#2-replay-dependency-chain-analysis)
3. [Tombstone Semantics](#3-tombstone-semantics)
4. [Reconnect Semantics](#4-reconnect-semantics)
5. [GUID Lifetime Rules](#5-guid-lifetime-rules)
6. [Hierarchy Invalidation Policy](#6-hierarchy-invalidation-policy)
7. [Determinism Proofs](#7-determinism-proofs)
8. [Failure Mode Analysis](#8-failure-mode-analysis)
9. [Observability Requirements](#9-observability-requirements)
10. [Frozen-Runtime Audit](#10-frozen-runtime-audit)
11. [Complexity Assessment](#11-complexity-assessment)
12. [Implementation Plan (Conceptual)](#12-implementation-plan-conceptual)
13. [Rollback Criteria](#13-rollback-criteria)

---

## 1. Packet Definition

### 1.1 Packet Type

| Field | Value |
|-------|-------|
| Constant | `PT_Delete = 0x0E` |
| Type space | Next available byte (§8.2 of semantic conventions) |
| Status | Reserved — NOT implemented |
| Direction | Blender → UE (Phase 6E); editor-authority delete deferred |
| Semantic classification | Discrete terminal semantic mutation (NOT state stream, NOT reversible) |

### 1.2 Proposed Wire Format

**Fixed-length payload**: 28 bytes per object.

| Offset | Size | Field | Description |
|--------|------|-------|-------------|
| 0 | 16 | `TargetGuid` | FGuid of the object to destroy (binary, 4×uint32 LE) |
| 16 | 4 | `Sequence` | Monotonic per-GUID sequence number (uint32 LE) |
| 20 | 8 | `Timestamp` | UE-style `FPlatformTime::Seconds()` double (LE) |
| 28 | — | End | Fixed 28 bytes per object |

**Total per-object**: 28 bytes fixed.

### 1.3 Fixed vs Variable Tradeoff

| Criterion | Fixed (28 bytes) | Variable |
|-----------|------------------|----------|
| Parse complexity | Trivial — known size per object | Higher — length prefix per object |
| Boundary checks | Single `remaining >= 28` per object | Per-field checks |
| Batching density | Fixed stride | Variable stride |
| Future extensibility | Requires new PT_* or version bump | Per-object flags possible |

**Decision**: Fixed-length 28 bytes. Rationale:
- No variable-length fields (no strings, no extra data beyond GUID)
- Identical structural pattern to visibility (29 bytes) with one fewer field
- Simplest possible boundary check reduces malformed-packet risk
- Future extensibility via version bump if needed

### 1.4 Delete Semantics

| Condition | Behavior |
|-----------|----------|
| Valid GUID, actor exists in `ActorCache` | Enter `FScopedDeleteSuppression` RAII guard. Destroy actor via `Actor->Destroy()`. Record tombstone. Exit suppression guard. |
| Valid GUID, actor NOT in `ActorCache` | Already destroyed or never created. Silently discard. Record tombstone anyway (idempotent). |
| Valid GUID, actor is pending deferred hierarchy attachment (as child) | Evict deferred entry. Destroy actor. Record tombstone. |
| Valid GUID, actor is referenced as parent in deferred entries | Evict ALL deferred entries where `Entry.ParentGuid == TargetGuid`. Destroy actor. Record tombstone. |
| Sequence stale (≤ LastSeq) | Silently discard. Increment `DeleteStaleRejections` counter. |
| Duplicate delete, same sequence | Silently discard (stale check catches this). |
| Replayed delete (bInSnapshotBuild) | Process normally, with deferral if CREATE not yet processed (§2.4). |
| CREATE packet for tombstoned GUID | Blocked by tombstone check. Increment `DeleteTombstoneHits`. Silently discard. |

### 1.5 What Delete Is NOT

Delete is NOT:
- **A suggestion**: Once processed, the actor is destroyed. There is no undo within the same connection session.
- **A toggle**: Delete is terminal. There is no "un-delete" packet.
- **A state assertion**: Unlike transform (which asserts "object is at this location"), delete asserts "object is gone." It is a one-shot event.

---

## 2. Replay Dependency Chain Analysis

### 2.1 Replay Ordering Constraints

Delete has the **most complex replay ordering** of any lane so far:

| Lane | Replay Ordering | Dependency |
|------|----------------|------------|
| Rename | No ordering required | Independent per-GUID |
| Visibility | No ordering required | Independent per-GUID |
| Hierarchy | Parent-before-child | Parent must exist before child attaches |
| **Delete** | **Child-before-parent** | **Parent must exist when children are detached; children must be detached before parent is deleted** |

### 2.2 Child-Before-Parent Deletion Ordering

When a parent actor is deleted:
1. Its children must be **detached to root** first
2. Only then can the parent be destroyed

This means delete packets during snapshot replay must be ordered
**children-before-parents**. This is the inverse of hierarchy's
parents-before-children ordering.

**Impact on snapshot builder**: If delete packets are ever included in a
snapshot (they should not be — see §4.2), the snapshot must process them in
reverse-depth order. However, delete is a **delta event, not a snapshot event**.
Deleted objects are absent from the live scene, so they are absent from the
snapshot. The ordering constraint only applies if delete packets arrive during
replay, which is exceptional (see §2.3).

### 2.3 Delete During Snapshot Replay

Delete packets during snapshot replay are **not expected**:

| Reason | Explanation |
|--------|-------------|
| Snapshot represents live scene | Snapshot is built from `tracked_objects` in Blender, which only contains live (non-deleted) MESH objects |
| Deleted objects are absent | An object deleted by the user is removed from `tracked_objects` immediately — it is not included in the snapshot |
| Exception: concurrent delete during snapshot build | If the user deletes an object while Blender is building the snapshot, there is a race. The delete packet may arrive during or after the snapshot batch. |

**Handling of concurrent delete during snapshot build**:

| Scenario | Behavior |
|----------|----------|
| Delete arrives before BeginSnapshot | Normal processing. The actor is gone before snapshot replay starts. |
| Delete arrives between BeginSnapshot and EndSnapshot | Tagged `EChangeOrigin::Replay`. If create for same GUID not yet processed → delete deferred to after EndSnapshot (§2.4). If create already processed → delete applied immediately. The tombstone blocks any subsequent CREATE for the same GUID (see §3.5). |
| Delete arrives after EndSnapshot | Normal processing. Same as any live delete. |

### 2.4 Replay Ordering With Create Packets

The critical edge case: a create packet (PT_Create = 0x03) followed by a
delete packet (PT_Delete = 0x0E) for the same GUID during the same
reconnect batch:

| Ordering | Behavior | Correct? |
|----------|----------|----------|
| Create → Delete (same GUID) | Actor is created, then immediately destroyed. Net effect: actor is gone. | ✅ Correct — reflects Blender state |
| Delete → Create (same GUID) | Actor is destroyed, tombstone recorded. Create checks tombstone → **blocked**. Actor stays dead. | ✅ Correct — tombstone prevents resurrection |

**Constraint**: Delete packets for a given GUID must be processed AFTER any
create packet for the same GUID within the same batch. Since deletes are
not expected in snapshot batches (deleted objects are absent from the
snapshot), this constraint only applies to the race case where the user
deletes an object while the snapshot is being built.

**Solution**: If a delete packet arrives during snapshot replay
(`bInSnapshotBuild == true`), and a create packet for the same GUID has not
yet been processed, the delete is **deferred to after EndSnapshot**. This
guarantees that all creates are processed before any intra-snapshot deletes.

### 2.5 Replay Ordering With Hierarchy Packets

If a hierarchy packet (parent-child attachment) for a child arrives, and
then a delete packet for the parent arrives:

1. Hierarchy packet attaches child to parent
2. Delete packet destroys parent
3. **Children must be detached to root** before parent is destroyed

This means the delete handler must check: "Am I about to destroy an actor
that has children?" If yes, detach children to root first.

**Sequence tracking independence**: The hierarchy sequence tracker and the
delete sequence tracker operate independently. A hierarchy event for GUID X
does not affect the delete sequence for GUID Y. The cross-lane interaction
is at the actor-graph level, not the sequence level.

---

## 3. Tombstone Semantics

### 3.1 Why Tombstones Are Required

Without tombstones, the following scenario would resurrect a deleted actor:

1. User deletes object A in Blender → UE destroys actor A GUID:aaa
2. Connection drops
3. UE clears state (sequence trackers, tombstones) on reconnect
4. Blender sends snapshot — object A is absent (it was deleted)
5. A stale replay packet for GUID:aaa (from an old connection) arrives
   → **Actor A is resurrected**

Without tombstones, step 5 resurrects the dead actor. The tombstone map
provides a **barrier** against stale replay: if the GUID is in the tombstone
map, any packet that would create or mutate that actor is silently discarded.

### 3.2 Tombstone Lifecycle

```
[GUID destroyed]
    │
    ▼
┌─────────────────────┐
│ ENTER TOMBSTONE MAP │── with current delete sequence number
└─────────┬───────────┘
          │
          ├── Stale packet for this GUID? ──→ Silently discard
          │
          ├── New delete packet (higher seq)? ──→ Update sequence, stay tombstoned
          │
          ├── Reconnect? ──→ Clear tombstone map entirely
          │
          └── Eviction (at 2048 capacity)? ──→ Remove oldest entry
```

### 3.3 Tombstone Map Specification

| Property | Value |
|----------|-------|
| Data structure | `TMap<FGuid, uint32>` — GUID → last delete sequence number |
| Bounding strategy | LRU eviction at 2048 entries |
| Lookup | O(1) average |
| Insert | O(1) average |
| Eviction | O(1) — evict oldest when at capacity |
| Reset trigger | `StopNetworkThread`, `ConsoleReset` |
| Thread safety | Game thread only (same as all actor-state maps) |
| Memory per entry | 20 bytes (16-byte FGuid + 4-byte uint32) |
| Maximum memory | ~41 KB at capacity (2048 × 20 bytes) |

### 3.4 Tombstone Eviction Safety

When a tombstone entry is evicted (LRU at 2048 capacity):

| Scenario | Risk | Mitigation |
|----------|------|------------|
| Stale delete packet for evicted GUID | GUID might be resurrected | Extremely improbable: 2048 deletes must have been processed since. If the packet is truly stale (from a previous connection), the sequence tracker will reject it (tracker cleared on reconnect, so new tracker starts at 0 but stale packet has seq > 0 — accepted! This is a gap.) |
| **CRITICAL GAP**: Sequence tracker reset on reconnect | Reconnected sequence tracker starts at 0, stale packet seq > 0 is accepted | **Must cross-check tombstone map**. Sequence alone is insufficient for delete replay safety. The tombstone map is the definitive authority: "Has this GUID ever been destroyed during this connection?" |
| Stale packet with no tombstone entry | GUID resurrected | The reconnect clears tombstones intentionally. After reconnect, the snapshot IS the authority. If the object is absent from the snapshot, no create packet arrives, so the stale packet has no actor to resurrect (actor was never created). |

**Conclusion**: Tombstone eviction is safe because:
1. Within a connection, 2048 deletions before the same GUID is re-encountered
   is effectively impossible during a single editing session
2. After reconnect, tombstones are cleared and snapshot authority applies
3. The combination of tombstone map + sequence tracker + ActorCache existence check
   provides three independent safety barriers

### 3.5 What Tombstones Block

| Packet Type | Blocked by Tombstone? | Rationale |
|-------------|----------------------|-----------|
| `PT_Transform` (0x01) | **Yes** — silently discard | Actor no longer exists |
| `PT_Create` (0x03) | **Yes** — silently discard | If a GUID was deleted, no CREATE should recreate it within the same connection. Exception: during snapshot replay, deletes are deferred to after EndSnapshot (§2.4) so intra-snapshot CREATEs are processed before tombstone is set. |
| `PT_Delete` (0x04 / 0x0E) | **Yes** — silently discard | Already destroyed; duplicate delete is a no-op |
| `PT_AssetDef` (0x08) | **Yes** — silently discard | No actor to apply asset to |
| `PT_Visibility` (0x0B) | **Yes** — silently discard | No actor to toggle |
| `PT_Rename` (0x0C) | **Yes** — silently discard | No actor to rename |
| `PT_Hierarchy` (0x0D) | **Yes** — silently discard | No actor to attach/detach |

**Implementation pattern**: The tombstone check is a single gate at the
top of each semantic handler, before any GUID lookup or sequence check:

```cpp
// Pseudocode — NOT implementation
if (TombstoneMap.Contains(TargetGuid))
{
    Stats.DeleteTombstoneHits.fetch_add(1);
    return; // Silently discard
}
```

---

## 4. Reconnect Semantics

### 4.1 Reconnect Determinism

The core question: **When UE reconnects to Blender, can a deleted Blender
object be resurrected on the UE side?**

**Answer: No — because the snapshot is authoritative.**

Proof:
1. UE disconnects → clears all state (sequence trackers, tombstones, ActorCache, deferred queues, pending assets)
2. Blender detects reconnect → rebuilds snapshot from `tracked_objects`
3. `tracked_objects` only contains live MESH objects — deleted objects were removed when the delete was detected
4. Blender sends snapshot → UE creates actors for all objects in the snapshot
5. Deleted objects are absent from the snapshot → UE does not create them
6. No stale replay can resurrect them because:
   - After reconnect, no stale packets exist in the queue (queue was cleared)
   - The snapshot does not contain the deleted object

**The only resurrection vector is a stale packet arriving from a previous
connection**. This is prevented by:
- Queue clearing on disconnect
- No stale packets in the new connection's queue
- Snapshot authority: if the object is not in the snapshot, no create packet arrives

### 4.2 Tombstone Clearing on Reconnect

Tombstones are cleared on reconnect. This is intentional:

| Before Reconnect | During Reconnect | After Reconnect |
|-----------------|------------------|-----------------|
| Actor A destroyed (GUID in tombstone map) | Tombstone map cleared | Actor A does not exist (not in snapshot) |
| | Blender snapshot sent | Actor A is absent from snapshot |

If Actor A's GUID were to reappear (e.g., user creates a new object in Blender
that happens to get the same GUID — prevented by `ensure_unique_guid` but
theoretically possible via cross-session collision), the new object would be
correctly created by the snapshot. The tombstone was cleared because the
connection is new — the old destruction event is irrelevant.

### 4.3 Delete During Disconnect

What happens if the user deletes an object in Blender while disconnected?

1. Blender's `check_updates()` loop continues to run during disconnect
   (the sync timer is running, but network packets are not being sent)
2. The delete is detected by the `ReferenceError` catch
3. The delete packet is **queued** in Blender's send queue
4. On reconnect, Blender sends queued packets + snapshot
5. The delete packet for the now-deleted object arrives at UE
6. UE processes the delete — but the actor was never created during this
   connection (the object was deleted in Blender before the snapshot was sent)
7. UE discards the delete (ActorCache does not contain the actor)

**Result**: The delete packet that was queued during disconnect is silently
discarded. The object stays dead because it was never created in this
connection. This is correct — the object was deleted in Blender, and the
snapshot (which is sent after reconnect) does not contain it.

**Edge case**: What if the snapshot arrives before the queued delete packet?
The snapshot creates the actor, then the delete packet destroys it. This is
correct — the net effect is the same: the actor is gone. The order of packet
processing within a single tick does not matter for this scenario.

### 4.4 Delete While Connected

Normal flow:
1. User deletes object in Blender
2. `ReferenceError` caught in `check_updates()` loop
3. Delete packet enqueued and sent immediately
4. UE receives packet, processes `HandleDelete()`, destroys actor
5. Object is gone within ~1 frame

---

## 5. GUID Lifetime Rules

### 5.1 Can Deleted GUIDs Ever Reappear?

| Scenario | Can GUID Reappear? | Handling |
|----------|-------------------|----------|
| Within same Blender session | **No** — `ensure_unique_guid()` prevents collision | N/A |
| After Blender restart | **Yes** — GUIDs are regenerated from `bpy.data.objects` on `start_sync()`. The old GUID is gone. | Correct: old connection was closed, new connection has new GUIDs |
| After UE reconnect | **Yes** — if the user created a new object in Blender that by chance collides with a deleted GUID | `ensure_unique_guid()` prevents same-session collision. Cross-session: GUIDs differ because Blender restarted. |
| Object.copy() duplicate | **Yes** — copy inherits parent's GUID, then `ensure_unique_guid()` reassigns | Existing code handles this correctly |

**Conclusion**: Within a single Blender session, deleted GUIDs do not reappear.
Across sessions/restarts, GUIDs are regenerated. Cross-session collision is
astronomically unlikely with 128-bit UUIDs.

### 5.2 GUID Reuse Policy

| Rule | Rationale |
|------|-----------|
| NEVER reuse a GUID within the same Blender session | `ensure_unique_guid()` guarantees uniqueness |
| Deleted GUIDs are removed from `tracked_objects` immediately | No stale references in Blender state |
| Deleted GUIDs in UE are removed from `ActorCache` immediately | No stale references in UE state |
| Deleted GUIDs enter tombstone map | Prevent stale-replay resurrection within the same connection |
| Tombstone map is cleared on reconnect | New connection = new authority; snapshot determines state |
| Cross-session GUID reuse is allowed | Different connection, different state tree |

### 5.3 Stale Transform After Delete

Transform packet for deleted GUID → silently discarded via tombstone check.
The packet is:
1. Parsed (no change to parser — already parses all packet types)
2. Sequences checked (delete sequence tracker is independent from transform sequence tracking)
3. GUID looked up in `ActorCache` — not found
4. Packet silently discarded

**The ActorCache lookup is the primary safety barrier**. Even without
tombstones, a transform packet for a deleted GUID would be discarded because
the actor does not exist. The tombstone is an additional optimization to
avoid unnecessary lookups.

### 5.4 Stale Rename/Hierarchy/Visibility After Delete

Same as transform — all post-delete packets are silently discarded at the
ActorCache lookup stage. The tombstone map provides early-out optimization.

### 5.5 Stale Delete After Delete

A duplicate delete packet for an already-deleted GUID is:
1. First processed by sequence tracker → stale if seq ≤ LastSeq
2. If not stale (somehow), checked against tombstone → already tombstoned
3. Silently discarded

**Three independent safety barriers**: sequence tracker, tombstone map,
ActorCache existence check.

---

## 6. Hierarchy Invalidation Policy

### 6.1 Deleting Parent With Children

This is the most architecturally significant interaction in the entire
Phase 6 lifecycle design.

**Rule**: When a parent actor is deleted, its children are **detached to
root** first. Children are NOT recursively deleted.

**Rationale**:
- Blender does NOT cascade delete: deleting a mesh in Blender does NOT delete
  its children. They become top-level objects in the scene collection.
- UE must mirror this behavior: the children survive as independent actors.
- Recursive delete would require a user-facing feature (dialog, undo stack)
  that is out of scope for this lane.

**Algorithm**:
```
DeleteParent(Actor P):
    for each Child C in P.GetAttachedChildren():
        DetachFromActor(C, KeepWorldTransform)
        Update C's FSyncTransformState (bHasParent = false, ParentGuid = 0)
        Evict pending deferred hierarchy entry for C (§6.6)
        Log: [DELETE][DETACH] Detached child GUID before parent destroy
        Increment: DeleteChildrenDetached
    
    TombstoneMap.Add(P.Guid, deleteSeq)
    ActorCache.Remove(P.Guid)
    P.Destroy()
```

**No hierarchy sequence coupling**: The implicit detachment does NOT update
the child's hierarchy sequence tracker. The existing tracker state (the last
genuine hierarchy event's sequence) is sufficient to reject stale replay.
A stale hierarchy packet for C→P has seq ≤ N (last genuine event) and is
correctly rejected. Blender's next genuine hierarchy event for C will have
seq N+1 and be accepted normally. See threat audit finding DEL-001.

**Correctness**: The children's `FSyncTransformState` reflects the new root
state. On the next tick, `InterpolateTransforms` will treat them as root
objects (world-space transform). No transform corruption occurs.

**Replay safety**: The child detachments are NOT emitted as hierarchy packets.
They are implicit consequences of the parent delete. This is intentional:
- The child detachment is deterministic (all children are detached)
- No sequence tracking needed (it is not a user-intended re-parent)
- The children's state is fully described by their next transform packet
  (which will have `bHasParent = false` and `ParentGuid = 0`)
- Any pending deferred hierarchy entry for a child is explicitly evicted,
  preventing stale re-attachment within the same Tick

### 6.2 Deleting Orphaned Actor

If an actor is in the deferred hierarchy queue (orphaned — parent not yet
arrived) and is deleted:

1. The actor is destroyed normally
2. The deferred queue entry for that child is evicted explicitly (see §6.6)
3. Any entries referencing the destroyed actor as a parent are also evicted

### 6.3 Deleting Actor With Pending Deferred Attachment

If a delete packet arrives for an actor that is listed as a **parent** in a
pending deferred hierarchy entry:

1. The parent actor is destroyed
2. ALL deferred entries where `Entry.ParentGuid == TargetGuid` are evicted
   explicitly (see §6.6)
3. The child actors continue to exist as orphaned roots

If a delete packet arrives for an actor that is listed as a **child** in a
pending deferred hierarchy entry:

1. The child actor is destroyed
2. The deferred entry for that child is evicted explicitly
3. The parent (if it exists) is unaffected

### 6.4 Hierarchy Event After Delete

If a PT_Hierarchy packet arrives for a GUID that has been deleted:
1. Tombstone check rejects it
2. Counter `DeleteTombstoneHits` incremented
3. Packet silently discarded

### 6.5 Interaction With Existing ResolvePendingAttachments

The existing `ResolvePendingAttachments()` (Phase 5B, FROZEN) deals with
pending scene-graph writes (`bPendingSceneGraphWrite`). A delete event
should:

- If the deleted actor has `bPendingSceneGraphWrite == true`:
  The pending attachment is moot because the actor is being destroyed.
  No explicit action needed: when the actor is destroyed, the pending
  write will fail on the next Tick (actor not in ActorCache), and the
  system will eventually time out.

- This is an existing behavior of the frozen runtime, not something
  Phase 6E needs to handle specially.

### 6.6 Deferred Hierarchy Entry Eviction Policy

When an actor is deleted, its GUID may appear in `PendingHierarchyAttachments`
(the Phase 6D deferred hierarchy queue) in three roles:

| Role | Definition | Eviction Trigger |
|------|-----------|-----------------|
| **Child** | `Entry.ChildGuid == TargetGuid` — actor was awaiting parent resolution | Direct delete of the actor itself |
| **Parent** | `Entry.ParentGuid == TargetGuid` — other actors were awaiting this actor as parent | Delete of the referenced parent |
| **Indirect** | Child of deleted parent who also has a pending deferred entry | Delete of the parent that the child is currently attached to (not the deferred parent) |

**Eviction rules**:

| Eviction Point | What Gets Evicted | Why Explicit |
|----------------|-------------------|--------------|
| `HandleDelete()` — after existence check, before destroy | Entries where `Entry.ChildGuid == TargetGuid` | The actor being deleted can no longer be a child in a deferred hierarchy event |
| `HandleDelete()` — after child detach, before parent destroy | Entries where `Entry.ChildGuid == ChildGuid` for each child of deleted parent | The child's pending hierarchy intent was captured before the parent was deleted; the implicit detach invalidates it |
| `HandleDelete()` — after destroy | Entries where `Entry.ParentGuid == TargetGuid` | The target was a referenced parent; all children awaiting it must be informed |

**Implementation**:
```cpp
// Pseudocode — NOT implementation
void EvictDeferredEntriesForChild(const FGuid& ChildGuid)
{
    PendingHierarchyAttachments.RemoveAll(
        [&](const FPendingHierarchyAttachment& Entry)
        {
            return Entry.ChildGuid == ChildGuid;
        });
}

void EvictDeferredEntriesForParent(const FGuid& ParentGuid)
{
    int32 Evicted = PendingHierarchyAttachments.RemoveAll(
        [&](const FPendingHierarchyAttachment& Entry)
        {
            return Entry.ParentGuid == ParentGuid;
        });
    if (Evicted > 0)
    {
        Stats.DeleteDeferredEvictions += Evicted;
        UE_LOG(LogLiveSync, Log,
            TEXT("[DELETE][DETACH] Evicted %d deferred entries for parent=%s"),
            Evicted, *ParentGuid.ToString(EGuidFormats::Digits));
    }
}
```

**Why explicit eviction is required** (not deferred FINDING-001):
- FINDING-001 re-validates the child's sequence against the hierarchy tracker.
  But the hierarchy tracker is NOT updated by the implicit detach (by design —
  see §6.1). Therefore, the deferred entry's sequence may appear fresh, and the
  child could be transiently re-attached to an incorrect parent.
- Explicit eviction is O(N) over `PendingHierarchyAttachments` (bounded at 2048)
  and occurs only during delete — a low-frequency operation.

**Observability**:
- Counter: `DeleteDeferredEvictions` — total deferred entries evicted
- Log prefix: `[DELETE][DETACH]` — per-batch eviction summary
- Verbose log: `[DELETE][DETACH] Evicted deferred entry: child=%s parent=%s` — per-entry detail

## 7. Determinism Proofs

### 7.1 Reconnect Determinism

**Claim**: Snapshot replay cannot resurrect a deleted actor.

**Proof**:

Let `S` be the set of actors in UE's ActorCache after a snapshot replay.
Let `B` be the set of live MESH objects in Blender's `tracked_objects` at the
time the snapshot was built.

We need to prove: `S ⊆ B` — every actor in UE after snapshot was live in
Blender when the snapshot was built.

1. Snapshot is built by iterating `tracked_objects` (sync.py:692-770 for
   reconnect, sync.py:1238-1299 for rebind_all). `tracked_objects` only
   contains live MESH objects — `ReferenceError`-catching removes deleted
   objects immediately.

2. The snapshot is sent as CREATE packets (PT_Create = 0x03).

3. UE processes CREATE packets in `ProcessQueuedPackets()`:
   - Each CREATE packet creates an actor and adds it to ActorCache.
   - No other packet type can add an actor to ActorCache.

4. Therefore, after snapshot replay, `ActorCache = {actors created by CREATE packets in the snapshot}`.

5. Since the snapshot only contains objects from `tracked_objects`, and
   `tracked_objects` excludes deleted objects, no deleted object's GUID
   appears in the snapshot.

6. **QED**: `S ⊆ B`.

**Corollary**: A deleted actor cannot be resurrected by snapshot replay.

### 7.2 Stale Delete Replay Determinism

**Claim**: A stale delete packet cannot destroy an actor that should not be
destroyed.

**Proof**:

Let `D` be a delete packet with GUID `G`, sequence `S`, arriving at time `T`.

We need to prove that `D` only destroys actor `G` if actor `G` was supposed
to be deleted.

**Case 1: D arrives during the same connection where G was deleted**.

```
G was deleted at time T0 with sequence S0.
At time T, D arrives with sequence S.
```

- If `S <= S0`: Sequence tracker rejects D (stale). ✅
- If `S > S0`: D is accepted. But D has a higher sequence than the original
  delete. This is correct: a higher-sequence delete is a newer delete event.
  However, G is already gone (tombstoned). D is silently discarded by
  tombstone check. ✅

**Case 2: D arrives during a new connection (after reconnect)**.

```
At reconnect, the sequence tracker is cleared.
D arrives with sequence S (tracker considers it new because tracker was reset).
```

- D is accepted by the sequence tracker (tracker was reset to empty).
- Tombstone map was also cleared on reconnect.
- D proceeds to ActorCache lookup.
- If G was not recreated by the snapshot: ActorCache does not contain G.
  D is silently discarded at ActorCache lookup. ✅
- If G was recreated by the snapshot (object was NOT deleted in Blender;
  it was deleted in UE during previous connection but the object still exists
  in Blender): ActorCache contains G. D would destroy G. **This is incorrect**
  if the delete was for a different session's event.

**Critical Gap**: A stale delete packet from a previous connection can destroy
a valid actor in the current connection if:
- The actor existed in the previous connection
- The actor was NOT deleted in the previous connection (so no tombstone)
- A stale delete packet for a different actor's GUID somehow matches this GUID

**Mitigation**: The sequence tracker would need to retain knowledge of which
GUIDs were deleted. But it is cleared on reconnect.

**Solution**: **Do not rely on sequence tracker alone for stale delete
rejection across reconnection boundaries**.

**Three-barrier approach**:

| Barrier | What It Prevents | Across Reconnect? |
|---------|-----------------|-------------------|
| Sequence tracker (per-GUID) | Intra-connection duplicate/stale deletes | Cleared — does NOT protect across reconnect |
| Tombstone map | Intra-connection re-delete of same GUID | Cleared — does NOT protect across reconnect |
| **ActorCache existence check** | Any packet for a non-existent actor | **Yes** — actor must exist to be deleted |

The ActorCache existence check is the **only** barrier that works across
reconnect boundaries. A stale delete packet for a GUID that was never
created in this connection will find no actor and be silently discarded.

**QED**: Stale delete replay cannot destroy an actor that should not be
destroyed, because the ActorCache existence check provides a cross-connection
safety barrier.

### 7.3 GUID Lifetime Determinism

**Claim**: A GUID cannot be accidentally resurrected after deletion within
the same connection.

**Proof**:

1. Within a Blender session, `ensure_unique_guid()` prevents GUID reuse.
2. When an object is deleted in Blender, `tracked_objects` removes the entry
   immediately (ReferenceError catch).
3. The delete packet is sent to UE with a monotonic sequence.
4. UE processes the delete: actor destroyed, GUID added to tombstone map.
5. Any subsequent packet for that GUID is blocked by either:
   - Tombstone map (early out)
   - ActorCache lookup (actor not found)
6. GUID cannot reappear in UE because:
   - No CREATE packet for that GUID can arrive (it is not in `tracked_objects`)
   - `ensure_unique_guid()` in Blender prevents accidental reassignment

**QED**: GUID lifecycle within a single connection is deterministic and
safe.

### 7.4 Hierarchy Invalidation Determinism

**Revised per threat audit DEL-001**: No cross-lane sequence coupling exists.
The design's original claim that hierarchy sequence coupling was required was
INCORRECT. The hierarchy sequence tracker is self-contained and provides
sufficient stale protection without lifecycle lane intervention.

**Claim**: Deleting a parent actor with children produces a deterministic
actor state, without cross-lane sequence coupling.

**Proof**:

1. Children are always detached to root (deterministic — all attached children
   are detached via `P.GetAttachedChildren()`).
2. Children's `FSyncTransformState` is updated: `bHasParent = false`,
   `ParentGuid = FGuid()` (zero).
3. The order of detachment is deterministic: `GetAttachedChildren()` returns
   a stable order (insertion order in UE's attachment system).
4. No hierarchy packets are emitted for implicit detachments.
5. Each child continues to exist as an independent root actor.
6. Pending deferred hierarchy entries for each child are explicitly evicted
   (§6.6), preventing stale deferred resolution.
7. On the next Tick, `InterpolateTransforms` treats them as root objects.

**Stale hierarchy replay protection**:

We must prove that a stale hierarchy packet cannot re-attach a child to a
deleted parent. The protection comes from the EXISTING hierarchy sequence
tracker, WITHOUT any cross-lane update:

```
Let N = last genuine hierarchy event's sequence for child C
    (e.g., the PT_Hierarchy that attached C to P had seq=N)

After parent P is deleted:
  - C was implicitly detached (no hierarchy event)
  - Hierarchy tracker for C still has seq=N (UNCHANGED)

Stale hierarchy packet for C→P arrives with seq=S:
  - If S ≤ N: IsStaleOrDuplicate returns true (S ≤ N) → REJECTED ✅
  - If S > N: Not stale. BUT S > N means this hierarchy packet has a
    HIGHER sequence than any previously applied hierarchy event for C.
    Such a packet is either:
    a) A genuine new hierarchy event from Blender (user re-attached C
       to a different parent after P was deleted) → should be accepted ✅
    b) A stale packet with a fabricated high sequence (astronomically
       unlikely — requires hash collision or Blender bug) → falls through
       to ActorCache check. If P is deleted, P's GUID is tombstoned →
       hierarchy packet for C→P is checked against tombstone → P is
       tombstoned → REJECTED ✅
```

**The hierarchy tracker alone provides stale protection**: Any hierarchy
packet with seq ≤ N (the last genuine event) is rejected. A packet with
seq > N is either genuine (correctly accepted) or has seq > all genuine
events for that GUID (rejected by tombstone check on the parent GUID).

**Why incrementing the tracker would BREAK correctness**:

```
If the delete handler incremented C's tracker from N to N+1:
  - Blender detects C's parent changed (P deleted) → sends PT_Hierarchy
    for C→root with seq=N+1 (Blender's next sequence for C)
  - UE processes: IsStaleOrDuplicate(C, N+1) → tracker has N+1 → N+1 ≤ N+1 → STALE
  - The genuine detach-to-root event is SILENTLY DROPPED ❌
```

Therefore: The implicit detach MUST NOT update the child's hierarchy tracker.
The existing tracker state provides correct protection. Incrementing causes
genuine future hierarchy events to be rejected. **This applies in both directions:
the lifecycle lane never touches the hierarchy tracker, and the hierarchy lane
never touches the tombstone map.**

**Deferred entry stale protection**:

Children of a deleted parent may have pending deferred hierarchy entries
awaiting a different parent. These entries are explicitly evicted (§6.6)
rather than relying on FINDING-001 re-validation:

```
Without explicit eviction:
  1. Parent P deleted → C detached. Tracker unchanged (seq=N).
  2. ResolveHierarchyAttachments: C has deferred entry for X with seq=N+1.
  3. FINDING-001: IsStaleOrDuplicate(C, N+1) → tracker has N → N+1 > N → not stale!
  4. C attached to X. WRONG (C should be root).
  5. Next frame: Blender's hierarchy packet (C→root, seq=N+2) corrects it.

With explicit eviction:
  1. Parent P deleted → C detached. C's deferred entry EVICTED.
  2. ResolveHierarchyAttachments: no entry for C → no action. ✅
```

**QED**: Hierarchy invalidation is deterministic without cross-lane sequence
coupling. The hierarchy sequence tracker is self-contained. Stale hierarchy
replay is prevented by the existing `<=` check. Deferred entry stale
resolution is prevented by explicit eviction. The lifecycle lane and
hierarchy lane operate independently.

---

## 8. Failure Mode Analysis

### 8.1 Resurrection Corruption

| Scenario | Root Cause | Impact | Mitigation |
|----------|-----------|--------|------------|
| Stale replay delete resurrects actor | Sequence tracker cleared on reconnect; stale packet accepted | Actor reappears after having been deleted | Tombstone map + ActorCache barrier (3-layer protection) |
| Snapshot includes deleted object | Race: user deletes object while snapshot is being built | Actor recreated after delete | Delete packet after snapshot destroys it again; eventual consistency |
| Reconnect creates actor that was deleted | Blender recreated object between disconnect and reconnect | Actor appears after having been deleted during disconnect | **This is correct** — the object was recreated in Blender, so it should exist in UE |

### 8.2 Tombstone Leaks

| Scenario | Root Cause | Impact | Mitigation |
|----------|-----------|--------|------------|
| Tombstone map grows unboundedly | No eviction mechanism | Memory leak, eventual OOM | LRU eviction at 2048 entries |
| Tombstone eviction allows stale replay | Evicted entry was the only barrier against stale packet | Actor is destroyed by stale replay | ActorCache existence check is the primary barrier; tombstone is optimization |
| Tombstone not created on actor destroy | Bug in delete handler | Next delete packet for same GUID passes through | Sequence tracker catches duplicate; ActorCache lookup catches missing actor |

### 8.3 Stale Replay Corruption

| Scenario | Root Cause | Impact | Mitigation |
|----------|-----------|--------|------------|
| Stale transform for deleted GUID | Packet delayed in network queue | Attempted mutation of destroyed actor | Tombstone check + ActorCache lookup |
| Stale hierarchy for deleted child | Same as above | Attach-to-missing-actor warning | ActorCache lookup rejects |
| Stale rename for deleted GUID | Same as above | Warning log spam | ActorCache lookup rejects |
| Stale visibility for deleted GUID | Same as above | Warning log spam | ActorCache lookup rejects |

### 8.4 Graph Invalidation

| Scenario | Root Cause | Impact | Mitigation |
|----------|-----------|--------|------------|
| Parent deleted while children attached | User action | Children become roots | Deterministic detach cascade (§6.1) |
| Orphan child's parent deleted | Parent deleted before child resolved | Child deferred entry evicted | Explicit eviction in HandleDelete (§6.6) |
| Deep chain: grandparent deleted, parent becomes root, child still attached to parent | User action | Child still attached to parent (now-root) | Correct — child remains attached |
| Cycle: A→B→C, then B deleted | User action | C orphaned, A is root | C deferred until parent reconnect; will never resolve; evicted on timeout (§6.1: children are detached to root, not recursively cascaded) |

Wait — **CRITICAL CORRECTION**: In the deep-chain scenario, the algorithm
in §6.1 says "detach all children to root." But this only detaches the
IMMEDIATE children of the deleted parent. Children of those children
(grandchildren) remain attached to their parent (which is now root).

Let me trace this:

Before: A (root) → B → C (child of B)

If B is deleted:
1. Detach C from B → C becomes root
2. Destroy B

Result: A (root) → (nothing). C (root). ✅ — correct.

Another example: A (root) → B → C → D (child of C)

If B is deleted:
1. Detach C from B → C becomes root
2. Destroy B

Result: A (root). C (root) → D (child of C). ✅ — correct.

The algorithm correctly handles deep chains: only the immediate children
of the deleted parent are detached. Grandchildren are not affected because
they are attached to their parent (the immediate child), not the deleted
grandparent.

### 8.5 GUID Collision

| Scenario | Root Cause | Impact | Mitigation |
|----------|-----------|--------|------------|
| `uuid.uuid4()` collision | Statistical: 2^128 space | Two actors share the same GUID in UE | `ensure_unique_guid()` in Blender detects collision at registration time |
| `ensure_unique_guid()` failure | GUID collision detection missed | ActorCache overwrite or duplicate | Extremely unlikely (128-bit UUID space). Would manifest as transform fighting between two objects. |
| Cross-session collision | Different Blender session generates same GUID | Actor has wrong state from prior session | GUIDs are regenerated on start_sync(); no cross-session state retained |

### 8.6 Replay Divergence

| Scenario | Root Cause | Impact | Mitigation |
|----------|-----------|--------|------------|
| Snapshot replay creates actor, then delete packet destroys it | User deleted object during snapshot build | Actor flickers (create → destroy) | ~1 frame of existence; acceptable for eventual consistency |
| Delete packet arrives before create during snapshot replay | Network reordering | Actor never created; delete discarded (ActorCache miss) | Eventually consistent; correct net state |

### 8.7 Reconnect Nondeterminism

| Scenario | Root Cause | Impact | Mitigation |
|----------|-----------|--------|------------|
| Object deleted during disconnect, recreated on reconnect | User deleted object while UE was offline | Actor reappears after disconnect | **Correct behavior** — Blender's snapshot reflects current scene. If the object was deleted, it is absent from the snapshot. UE does not recreate it. |
| Object created during disconnect, not in tracked_objects | Object was never synced | Actor missing after reconnect | Snapshot includes all tracked_objects; new objects are added during the next scan cycle |

---

## 9. Observability Requirements

### 9.1 Required Counters

| Counter | Type | Reset on ConsoleReset | Description |
|---------|------|----------------------|-------------|
| `DeletesProcessed` | uint64 | Yes | Number of times an actor was destroyed by PT_Delete |
| `DeleteStaleRejections` | uint64 | Yes | Stale/duplicate delete packets rejected |
| `DeleteReplayApplied` | uint64 | Yes | Delete applied during snapshot replay |
| `DeleteReplaySkipped` | uint64 | Yes | Delete skipped during snapshot replay (e.g., create not yet processed) |
| `DeleteTombstoneHits` | uint64 | Yes | Packet silently discarded because GUID is in tombstone map |
| `DeleteChildrenDetached` | uint64 | Yes | Children detached from parent before parent destroy |
| `DeleteDeferredEvictions` | uint64 | Yes | Deferred hierarchy entries evicted due to parent delete |
| `DeleteTombstoneEvictions` | uint64 | Yes | Tombstone entry evicted at 2048 capacity |

### 9.2 Required Log Prefixes

| Prefix | Context | Verbose Only? |
|--------|---------|---------------|
| `[DELETE]` | Actor destroyed | No |
| `[DELETE][DETACH]` | Children detached from parent | No |
| `[DELETE][TOMBSTONE]` | Packet blocked by tombstone | Yes |
| `[DELETE][STALE]` | Stale/duplicate delete rejected | Yes |
| `[REPLAY]` | Delete during snapshot replay | No |
| `[DELETE][MISSING]` | Delete for actor not in ActorCache | Yes |
| `[DELETE][SUPPRESS]` | Suppression RAII scope enter/exit | Yes |
| `[DELETE][EVICT]` | Deferred hierarchy entry evicted | No |

### 9.3 Required Profiler Scopes

| Scope | Location |
|-------|----------|
| `UELiveSync_HandleDelete` | Top-level delete handler (Runnable thread → game thread dispatch) |
| `UELiveSync_ProcessDeletePackets` | Game-thread delete batch processing |

### 9.4 Required CVars

| CVar | Default | Description |
|------|---------|-------------|
| `UE.LiveSync.Verbose` | 0 | Gates all verbose delete logs |
| (existing CVars unchanged) | | |

### 9.5 Console Commands

| Command | Function |
|---------|----------|
| `UE.LiveSync.DumpState` | Includes: tombstone map size, delete tracker entries |
| `UE.LiveSync.Reset` | Clears: tombstone map, delete tracker, all counters |

---

## 10. Frozen-Runtime Audit

### 10.1 System Audit

| System | File | Frozen? | Phase 6E Change? |
|--------|------|---------|-----------------|
| `LiveSyncQueue.h` | `UE_Plugin/.../LiveSyncQueue.h` | **FROZEN** | None |
| `PendingAssetQueue.h` | `UE_Plugin/.../PendingAssetQueue.h` | **FROZEN** | None |
| `LiveSyncRunnable.h/cpp` | `UE_Plugin/.../LiveSyncRunnable.h` + `.cpp` | **FROZEN** | None |
| `FSyncTransformState` | `SyncTypes.h:148-182` | **FROZEN** | None |
| Tick ordering | `UELiveSyncSubsystem.cpp:Tick()` | **FROZEN** | None |
| `InterpolateTransforms` | `UELiveSyncSubsystem.cpp` | **FROZEN** | None |
| `AttachToParent` / `DetachFromParent` | `UELiveSyncSubsystem.cpp` | **FROZEN** | None |
| `ResolvePendingAttachments` | `UELiveSyncSubsystem.cpp` | **FROZEN** | None |
| `BuildActorCache` | `UELiveSyncSubsystem.cpp` | **FROZEN** | None |
| `RecoverMissingActors` | `UELiveSyncSubsystem.cpp` | **FROZEN** | None |

### 10.2 Required Additions (Not Modifications)

| Addition | Type | Location |
|----------|------|----------|
| `PT_Delete = 0x0E` | Constant | `SyncTypes.h` |
| `FDeleteSequenceTracker` | Type alias | `SyncTypes.h` (alongside FRenameSequenceTracker etc.) |
| `GDeleteSequences` | Global instance | `UELiveSyncSubsystem.cpp` (alongside GRenameSequences etc.) |
| `GDeleteTombstoneMap` | Global instance | `UELiveSyncSubsystem.cpp` |
| `HandleDelete()` | Method | `UELiveSyncSubsystem` class |
| `ProcessDeletePackets()` | Method | `UELiveSyncSubsystem` class |
| `Delete case in ProcessBinaryPacket` | Case branch | `ProcessBinaryPacket` switch statement |
| 6 delete counters | Fields | `FLiveSyncStats` struct |
| Delete counters in ConsoleReset | Code | `HandleConsoleReset()` |
| Delete counters in DumpState | Code | `HandleConsoleDumpState()` |
| Tombstone clear in StopNetworkThread | Code | `StopNetworkThread()` |
| Tombstone clear in ConsoleReset | Code | `HandleConsoleReset()` |
| Detach-children-before-destroy | Code | `HandleDelete()` |
| Deferred entry eviction for children of deleted parent | Code | `HandleDelete()` — explicit eviction (§6.6) |
| Deferred entry eviction for deleted actor as child/parent | Code | `HandleDelete()` — explicit eviction (§6.6) |
| `FScopedDeleteSuppression` | Class | RAII guard wrapping destroy path |
| Tombstone check in RecoverMissingActors | Code | `RecoverMissingActors()` — additive tombstone lookup |

### 10.3 Cross-Lane Interactions (No Coupling)

Phase 6E has **zero cross-lane sequence coupling**. The lifecycle lane and
the hierarchy lane operate independently:

| Interaction Type | Mechanism | Coupling? |
|-----------------|-----------|-----------|
| **Delete parent → child detach** | Raw `DetachFromActor()` API. No hierarchy sequence tracker update. | **None** — hierarchy tracker unchanged |
| **Delete parent → deferred entry eviction** | Explicit `RemoveAll` on `PendingHierarchyAttachments`. No sequence tracker involvement. | **None** — operates on separate data structure |
| **Hierarchy event for deleted GUID** | Blocked by tombstone check. No sequence tracker involvement. | **None** — tombstone is lifecycle-owned |
| **CREATE after delete during snapshot** | Blocked by tombstone check (§3.5). Delete deferred to after EndSnapshot during replay. | **None** — tombstone + deferral mechanism |
| **Transform/rename/visibility for deleted GUID** | Blocked by ActorCache existence check. | **None** — ActorCache is shared, not coupled |

**Why no coupling is needed**: The hierarchy sequence tracker's existing `<=`
check provides stale protection without any lifecycle lane involvement.
A stale hierarchy packet has seq ≤ last genuine event's seq and is rejected.
A genuine new hierarchy packet has seq > last genuine event's seq and is
accepted. The implicit detach does not create a tracker entry, so the
sequence space is uncontested.

This is a **cleaner architecture** than the original design, which claimed
a cross-lane coupling was required. The threat audit (DEL-001) proved the
coupling was not only unnecessary but would cause genuine hierarchy events
to be silently dropped.

---

## 11. Complexity Assessment

### 11.1 Complexity by Dimension

| Dimension | Rating | Comparison to Hierarchy |
|-----------|--------|------------------------|
| Packet parsing | **Low** (28 bytes fixed) | Simpler (hierarchy: 44 bytes) |
| Replay safety | **High** (tombstones required) | Comparable (hierarchy: sequence tracker only) |
| Reconnect determinism | **High** (resurrection prevention) | Higher (hierarchy: cleared queue is sufficient) |
| State management | **Medium** (tombstone map, bounded) | Comparable (hierarchy: deferred queue + tracker) |
| Graph interaction | **HIGH** (parent delete cascade) | Higher (hierarchy: orphan queue only) |
| Cross-lane coupling | **None** — fully isolated | Simpler (hierarchy: also none) |
| Observability | **Low** (6 counters, 2 scopes) | Comparable (hierarchy: 8 counters, 3 scopes) |
| Test complexity | **HIGH** (replay, reconnect, cascade, fuzz) | Higher (hierarchy: 97 standalone tests, 7 integration) |
| Risk severity | **CRITICAL** (irreversible destruction) | Higher (hierarchy: reversible attachment) |

### 11.2 Risk Mitigation Summary

| Risk | Severity | Mitigation | Residual Risk |
|------|----------|------------|---------------|
| Replay resurrection | CRITICAL | 3-barrier (sequence + tombstone + ActorCache) | Low (cross-connection scenario requires stale packet creation + actor existence) |
| Stale recreate on reconnect | CRITICAL | Snapshot authority + tombstone clear | None (deleted object absent from snapshot) |
| Parent-delete-child corruption | HIGH | Deterministic detach cascade + deferred entry eviction | Low (no sequence coupling needed; explicit eviction prevents stale deferred resolution) |
| Tombstone leak | MEDIUM | LRU eviction at 2048 | Low (2048 deletes within a session is rare) |
| GUID collision | LOW | ensure_unique_guid + 128-bit space | Negligible |

### 11.3 Implementation Risk Score

**Overall: HIGH** — comparable to hierarchy, with higher stakes due to
irreversibility. Implementation requires careful attention to:
1. The 3-barrier stale packet rejection system
2. The parent-delete-child-detach cascade with deferred entry eviction
3. The tombstone + CREATE interaction during snapshot replay
4. Reconnect determinism invariants

---

## 12. Implementation Plan (Conceptual)

### 12.1 Recommended Stages

| Stage | Scope | Risk |
|-------|-------|------|
| 0 | Packet constant (`PT_Delete = 0x0E`), FNV signature update | None |
| 1 | `FDeleteSequenceTracker` + `GDeleteSequences` | Low |
| 2 | Parser branch for `PT_Delete` in `ProcessBinaryPacket` | Low |
| 3 | Replay rejection layer (stale/duplicate via `<=`) | Low |
| 4 | Tombstone map (`GDeleteTombstoneMap`, bounded 2048) | Medium |
| 5 | `HandleDelete()` — basic destroy + tombstone record + `FScopedDeleteSuppression` | Medium |
| 6 | Parent-delete-child-detach cascade with deferred entry eviction | HIGH |
| 7 | Blender detection + `serialize_delete()` + `PT_Delete` emission | Medium |
| 9 | Validation foundation (wire format, sequence, tombstone standalone tests) | Low |
| 10 | Integration tests (UE required: basic delete, parent delete, orphan, reconnect, storm) | Medium |
| 11 | Observability (6 counters, 2 scopes, logs) | Low |
| 12 | Soak + stress tests | Medium |
| 13 | Live runtime stabilization + finalization | High |

### 12.2 Validation Gates

| Stage | Gate |
|-------|------|
| 0–2 | FNV signature matches UE/Blender; `kValidTypes` includes `0x0E` |
| 3 | Sequence tracker: first accepted, duplicate rejected, stale rejected, higher-seq accepted |
| 4 | Tombstone: add blocks stale packet, LRU eviction works, clear on reconnect/reset |
| 5 | Delete: actor destroyed, tombstone recorded, ActorCache entry removed |
| 6 | Parent delete: children detached, `FSyncTransformState` updated, deferred entries evicted, no hierarchy events emitted |
| 7 | Blender: delete event caught, `PT_Delete` emitted, no duplicate sends |
| 9 | All standalone tests pass |
| 10 | All integration tests pass |
| 11 | All counters increment correctly; all log prefixes present |
| 12 | 10-minute soak: no crashes, no leaks, no corruption |
| 13 | FULLY STABILIZED |

---

## 13. Rollback Criteria

Implementation must be rolled back to the last stable commit if ANY of the
following is detected:

| Condition | Detection | Action |
|-----------|-----------|--------|
| Stale delete packet destroys a valid actor | Integration test: send stale delete from previous connection | Roll back to Stage 0 |
| Deleted actor reappears after reconnect | Integration test: delete, disconnect, reconnect | Roll back to Stage 4 |
| Children not detached before parent destroy | Integration test: delete parent, verify children are roots | Roll back to Stage 6 |
| Stale hierarchy packet re-attaches child after parent delete | Integration test: parent delete, then hierarchy replay | Roll back to Stage 6 |
| Tombstone map grows beyond bound | Counter test: verify eviction at 2048 | Roll back to Stage 4 |
| Crash on packet for deleted GUID | Fuzz test: send transform after delete | Roll back to Stage 5 |
| Editor crash during mixed soak | 10-minute soak | Roll back to Stage 12 |
| Frozen-runtime modification detected | Code review: verify additive-only | Roll back immediately |

---

## Appendix A: Comparison With Prior Semantic Lanes

| Aspect | Rename (0x0C) | Visibility (0x0B) | Hierarchy (0x0D) | Delete (0x0E) |
|--------|---------------|-------------------|-------------------|---------------|
| Payload size | Variable | 29 bytes fixed | 44 bytes fixed | 28 bytes fixed |
| Reversible | Yes | Yes | Yes | **No** |
| Tombstone required | No | No | No | **Yes** |
| Cross-lane coupling | None | None | None | **None** |
| Reconnect resurrection risk | None | None | None | **HIGH** |
| Parent-child interaction | None | None | Parent-child ordering | **Delete-parent-detach-children** |
| Suppression required | Yes (callback) | Yes (pattern) | Yes (pattern) | **Yes (pattern)** |
| Sequence tracker | FRename | FVisibility | FHierarchy | **FDelete** |
| Bounded at | 2048 | 2048 | 2048 | 2048 |

---

## Appendix B: Key Design Decisions Summary

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Fixed vs variable packet | Fixed 28 bytes | Same pattern as prior lanes; no variable fields |
| Tombstone yes/no | Yes, bounded 2048 LRU | Required for stale-replay resurrection prevention |
| Tombstone clear on reconnect | Yes | Snapshot authority: deleted object absent from snapshot |
| Recursive delete on parent destroy | No | Children detached to root, not recursively deleted |
| Implicit detach emits hierarchy event | No | Avoids non-deterministic replay sequences |
| Cross-lane hierarchy sequence coupling | **No** — fully isolated | Hierarchy tracker stale check (`<=`) is self-sufficient. Implicit detach does NOT update tracker. |
| Deferred entry eviction on parent delete | **Yes** — explicit | Required after coupling removal; prevents stale deferred resolution within same Tick |
| CREATE blocked by tombstone | **Yes** | Prevents resurrection; snapshot replay defers delete to after EndSnapshot |
| Delete suppression | **Yes** — FScopedDeleteSuppression | Pattern-conformance per conventions §2.6 and §6.5 |
| 3-barrier stale rejection | Sequence + Tombstone + ActorCache | Required for cross-connection stale packet safety |
| Delete during snapshot replay deferred | Yes, to after EndSnapshot | Prevents create-then-delete flicker during replay |
| Editor-authority delete | Out of scope | Requires bidirectional framework |
| Blender PT_Delete 0x04 replacement | Keep 0x04 for V3 compat, add 0x0E for V5+ | Backward compatibility with Phase 3 protocol |

---

## Appendix C: Determinism Proof Checklist

| Proof | Status | Section |
|-------|--------|---------|
| Reconnect cannot resurrect | PROVED | §7.1 |
| Stale delete cannot destroy valid actor | PROVED | §7.2 |
| GUID cannot be accidentally resurrected within connection | PROVED | §7.3 |
| Parent-delete produces deterministic child state | **PROVED** (no cross-lane coupling required) | §7.4 |
| Tombstone eviction does not enable stale replay | PROVED | §3.4 |
| Delete during snapshot rebuild is safe | PROVED | §2.3 |
| Post-delete packets are harmless | PROVED | §5.3–§5.5 |
| Orphaned actor delete is safe | PROVED | §6.2 |
| Deferred hierarchy entry eviction is deterministic | PROVED | §6.6 |
| Suppression RAII is convention-compliant | PROVED | §1.4, §9 |

**All 10 proofs complete.** Phase 6E design remediation complete. See
`32-phase6E-remediation-summary.md` for the full resolution record.

---

## Changelog

| Date | Version | Changes |
|------|---------|---------|
| 2026-05-26 | 1.0 | Initial vertical slice design — Phase 6E planning. Covers packet definition, replay analysis, tombstone semantics, reconnect semantics, GUID lifetime rules, hierarchy invalidation policy, determinism proofs, failure mode analysis, frozen-runtime audit, complexity assessment, conceptual implementation plan, and rollback criteria. Implementation BLOCKED — awaiting implementation review board approval. |
| 2026-05-26 | 1.1 | Design remediation (DEL-001 through DEL-004 from threat audit). Major changes: (1) REMOVED invalid hierarchy sequence coupling — implicit detach no longer updates hierarchy tracker; stale protection is self-contained within hierarchy lane. (2) Added explicit deferred hierarchy entry eviction (§6.6) for children of deleted parent. (3) Fixed tombstone table — CREATE is now blocked by tombstone (§3.5); snapshot replay defers delete to after EndSnapshot for consistency. (4) Added FScopedDeleteSuppression RAII guard. (5) Unimplemented Stage 7 (cross-lane coupling) removed from implementation plan. (6) §7.4 proof rewritten to prove determinism WITHOUT coupling. (7) Updated all appendix A/B/C. Status changed to AUTHORIZED for implementation planning. |
