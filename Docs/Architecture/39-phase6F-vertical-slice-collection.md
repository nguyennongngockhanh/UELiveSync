# Phase 6F — Collection/Group Replication: Vertical Slice Design

> **Created**: 2026-05-27
> **Status**: PLANNING — NOT IMPLEMENTED
> **Scope Lock**: `38-phase6F-collection-scope-lock.md`
> **Predecessors**: Rename (`0x0C` · STABILIZED) · Visibility (`0x0B` · STABILIZED) · Hierarchy (`0x0D` · STABILIZED) · Lifecycle/Delete (`0x0E` · STABILIZED)
> **Implementation**: BLOCKED — design phase only. No runtime code has been modified. No parser branches have been added. No packet handlers exist.
> **Freeze**: Phase 6 Stabilization Freeze ACTIVE — additive-only, no frozen-runtime modifications, no cross-lane coupling (`36-phase6-stabilization-freeze-checkpoint.md`)
>
> This document defines the **complete vertical slice design** for the fifth
> Phase 6 semantic-event lane: collection/group replication. It is the first
> **reference-only metadata grouping lane** — introducing per-pair sequence
> tracking, multi-collection membership semantics, and metadata-only storage
> on the UE side. Unlike all prior lanes, collection does NOT mutate actor
> structural state — it operates entirely on a metadata overlay.
>
> **This is a design document, NOT an implementation specification.**
> No runtime code has been modified. No parser branches have been added.
> No packet handlers exist. No Tick pipeline changes are proposed.

---

## Table of Contents

1. [Packet Definition](#1-packet-definition)
2. [Replay Dependency Model](#2-replay-dependency-model)
3. [Blender → UE Emission Model](#3-blender--ue-emission-model)
4. [UE Processing Model (Conceptual)](#4-ue-processing-model-conceptual)
5. [Cross-Lane Interaction Matrix](#5-cross-lane-interaction-matrix)
6. [Consistency & Invariants](#6-consistency--invariants)
7. [Replay Safety Proofs](#7-replay-safety-proofs)
8. [Observability Design](#8-observability-design)
9. [Frozen Runtime Guarantees](#9-frozen-runtime-guarantees)
10. [Failure Mode Analysis](#10-failure-mode-analysis)
11. [Rollback Conditions](#11-rollback-conditions)
12. [Done Criteria](#12-done-criteria)
13. [Complexity Assessment](#13-complexity-assessment)
14. [Reference Documents](#14-reference-documents)
15. [Changelog](#15-changelog)

---

## 1. Packet Definition

### 1.1 Packet Type

| Field | Value |
|-------|-------|
| Constant | `PT_Collection = 0x0F` |
| Type space | Next available byte (§8.2 of semantic conventions, after `0x0E`) |
| Status | **Reserved — NOT implemented.** No parser branch, no serialization, no FNV update. |
| Direction | Blender → UE only (Phase 6F); UE → Blender deferred (authority model unchanged) |
| Semantic classification | Discrete reversible semantic mutation (NOT state stream) |
| Scope | MESH objects only (existing object filter). Collections containing only non-MESH objects are not replicated. |

### 1.2 Proposed Wire Format

**Fixed-length payload**: 30 bytes per operation.

| Offset | Size | Field | Description |
|--------|------|-------|-------------|
| 0 | 16 | `TargetGuid` | FGuid of the affected actor (or collection identity for COLLECTION_CREATE/COLLECTION_DELETE/COLLECTION_RENAME) |
| 16 | 1 | `OpType` | Operation type: `0x01`=ADD, `0x02`=REMOVE, `0x03`=MOVE, `0x04`=CLEAR, `0x05`=RENAME_REF, `0x06`=COLLECTION_CREATE, `0x07`=COLLECTION_DELETE, `0x08`=COLLECTION_REPARENT |
| 17 | 1 | `OpFlags` | Bitmask: `0x01`=fromSnapshot, `0x02`=batchEnd. Reserved: `0xFC`. |
| 18 | 4 | `Sequence` | Monotonic per-(TargetGuid,CollectionGuid) sequence number (uint32 LE) |
| 22 | 8 | `Timestamp` | UE-style `FPlatformTime::Seconds()` double (LE) |
| — | — | — | **30 bytes per operation (fixed)** |

**Note**: The CollectionGuid for the affected collection is encoded as the
`TargetGuid` field for collection-identity operations (CREATE/DELETE/RENAME/REPARENT).
For membership operations (ADD/REMOVE/MOVE/CLEAR), the `TargetGuid` is the actor
GUID, and the collection GUID is carried in a **second GUID field** appended
to the fixed payload — yielding a **second packet variant** below.

### 1.3 Payload Variants

Two variants are needed because membership operations reference two GUIDs
(actor + collection), while collection-identity operations reference one
(collection only).

**Variant A — Membership Operation (ADD / REMOVE / MOVE / CLEAR)**

| Offset | Size | Field | Description |
|--------|------|-------|-------------|
| 0 | 16 | `TargetGuid` | FGuid of the affected actor |
| 16 | 16 | `CollectionGuid` | FGuid of the affected collection |
| 32 | 1 | `OpType` | Operation type |
| 33 | 1 | `OpFlags` | Bitmask |
| 34 | 4 | `Sequence` | Monotonic sequence for this (actor, collection) pair |
| 38 | 8 | `Timestamp` | Timestamp double |
| 46 | — | End | **46 bytes per operation (fixed)** |

**Variant B — Collection Identity Operation (COLLECTION_CREATE / DELETE / RENAME / REPARENT)**

| Offset | Size | Field | Description |
|--------|------|-------|-------------|
| 0 | 16 | `CollectionGuid` | FGuid of the collection being created/deleted/renamed/reparented |
| 16 | 16 | `ParentCollectionGuid` | Parent collection GUID (all-zero = root). For RENAME: new name follows. |
| 32 | 1 | `OpType` | Operation type |
| 33 | 1 | `OpFlags` | Bitmask |
| 34 | 4 | `Sequence` | Monotonic sequence for this collection |
| 38 | 8 | `Timestamp` | Timestamp double |
| — | — | — | **46 bytes base** + variable name field for RENAME (see §1.4) |

### 1.4 RENAME Variable Extension

For `RENAME_REF` operations, the payload extends with a name field:

| Offset | Size | Field |
|--------|------|-------|
| 46 | 2 | `NameLength` (uint16 LE, max 256) |
| 48 | N | `UTF-8 Name` (variable, N bytes) |
| 48+N | — | End |

Maximum payload for RENAME: 46 + 2 + 256 = **304 bytes**.

### 1.5 Operation Type Definitions

| Type | Code | Payload Variant | Description |
|------|------|----------------|-------------|
| `ADD` | `0x01` | A (46B fixed) | Actor X added to collection Y |
| `REMOVE` | `0x02` | A (46B fixed) | Actor X removed from collection Y |
| `MOVE` | `0x03` | A (46B fixed) | Actor X moved from collection Y to collection Z (encoded as two operations with linked sequence) |
| `CLEAR` | `0x04` | A (46B fixed) | All actors removed from collection Y (batch clear) |
| `RENAME_REF` | `0x05` | B + name (≤304B) | Collection renamed (new name in variable payload) |
| `COLLECTION_CREATE` | `0x06` | B (46B fixed) | New collection created |
| `COLLECTION_DELETE` | `0x07` | B (46B fixed) | Collection deleted (members survive, metadata removed) |
| `COLLECTION_REPARENT` | `0x08` | B (46B fixed) | Collection parent changed |

### 1.6 Fixed vs Variable Tradeoff

| Criterion | Fixed (46B membership / 46B identity) | Variable |
|-----------|---------------------------------------|----------|
| Parse complexity | Moderate — two fixed sizes, discriminated by `OpType` | Higher — variable-length per variant |
| Boundary checks | `remaining >= 46` for all ops except RENAME | Per-field checks for each variant |
| Batching density | Uniform stride per variant | Non-uniform stride |
| RENAME handling | Variable extension appended to fixed base | Everything variable |

**Decision**: Dual fixed-size base with RENAME variable extension. Rationale:
- Membership and identity variants have identical base size (46 bytes), simplifying parser dispatch
- RENAME is the only variable-length operation — special-casing one operation is simpler than making all operations variable
- `OpType` byte in the first 2 bytes of payload enables immediate dispatch to the correct parser path before full deserialization

### 1.7 Sequence Number Assignment

| Operation | Sequence Key | Assignment Rule |
|-----------|-------------|-----------------|
| ADD | `(actor GUID, collection GUID)` | Monotonic pair sequence |
| REMOVE | `(actor GUID, collection GUID)` | Monotonic pair sequence (same counter as ADD for the same pair) |
| MOVE | `(actor GUID, collection GUID)` | Linked: MOVE is semantically two operations (REMOVE from old + ADD to new) with a single MOVE sequence number |
| CLEAR | `(collection GUID)` | Collection-level monotonic sequence |
| RENAME_REF | `(collection GUID)` | Collection-level monotonic sequence |
| COLLECTION_CREATE | `(collection GUID)` | Collection-level monotonic sequence |
| COLLECTION_DELETE | `(collection GUID)` | Collection-level monotonic sequence |
| COLLECTION_REPARENT | `(collection GUID)` | Collection-level monotonic sequence |

### 1.8 Batching Semantics

Multiple collection operations are batched into a single packet payload,
same pattern as all prior lanes:

```
Header (24 bytes) | PT_Collection (1 byte) | PayloadSize (2 bytes) |
OpCount (2 bytes) |
  Op1 (46+ bytes) | Op2 (46+ bytes) | ... | OpN (46+ bytes)
```

- Each operation within a batch is independently validated (boundary check, sequence check, tombstone check)
- A single malformed operation causes the entire batch to be rejected (`Stats.MalformedPackets++`)
- Operations within a batch from the same (actor, collection) pair must have strictly increasing sequences

---

## 2. Replay Dependency Model

### 2.1 Sequence Tracker Design

| Property | Value |
|----------|-------|
| Tracker type | `FCollectionSequenceTracker` |
| Key scheme | Dual-key: `TPair<FGuid, FGuid>` for membership ops, `FGuid` for collection-identity ops |
| Underlying storage | `TMap<TPair<FGuid, FGuid>, uint32>` for membership + `TMap<FGuid, uint32>` for identity |
| Bounding | 2048 total entries, FIFO eviction across both maps |

### 2.2 Acceptance and Rejection Rules

| Condition | IncomingSeq relation | Action |
|-----------|---------------------|--------|
| Stale | `IncomingSeq < LastSeq` | Reject with Warning log, increment `CollectionStaleRejections` |
| Duplicate | `IncomingSeq == LastSeq` | Reject with Warning log, increment `CollectionStaleRejections` (or `CollectionReplaySkipped` during snapshot) |
| Fresh | `IncomingSeq > LastSeq` | Accept, apply mutation, update tracker |

### 2.3 Out-of-Order Handling

| Scenario | Behavior | Rationale |
|----------|----------|-----------|
| ADD seq=5 arrives before ADD seq=3 for same pair | Seq=5 accepted (fresh), seq=3 rejected (stale) | Sequence tracker handles this correctly — only strictly-increasing sequences pass |
| ADD seq=3 arrives, then REMOVE seq=4, then ADD seq=5 | Both add operations accepted independently. After REMOVE, actor is no longer in collection. Second ADD re-adds it. | This is correct — Blender must not emit this sequence. If it does, the trailing ADD is valid (fresh sequence) |
| Batch arrives with out-of-order operations | Each op in batch independently validated | Batching has no ordering guarantee — each op stands alone |
| COLLECTION_CREATE seq=1, then COLLECTION_DELETE seq=2, then COLLECTION_CREATE seq=3 | Collection deleted at seq=2, recreated at seq=3 with a fresh identity | Collection identity lifecycle is tracked by the collection-GUID sequence. This enables correct rename-delete-recreate cycles. |

### 2.4 Duplicate Suppression Rules

| Scenario | Behavior |
|----------|----------|
| Same ADD packet received twice (identical bytes) | Second instance rejected — duplicate sequence for (actor, collection) pair |
| ADD a1→c1 seq=5, then ADD a1→c1 seq=5 again | Rejected — `IncomingSeq(5) == LastSeq(5)` |
| ADD a1→c1 seq=5, then ADD a1→c2 seq=5 | Both accepted — different pair keys |
| ADD a1→c1 seq=5, then REMOVE a1→c1 seq=5 | REMOVE rejected — duplicate sequence for same pair key |

### 2.5 Reconnect Behavior

| Event | Sequence Tracker Action | Metadata Action |
|-------|------------------------|-----------------|
| `StopNetworkThread()` | `CollectionSequences.Empty()` | `CollectionMembershipMap.Empty()`, `CollectionIdentityMap.Empty()` |
| `ConsoleReset()` | `CollectionSequences.Empty()` | `CollectionMembershipMap.Empty()`, `CollectionIdentityMap.Empty()`; counters `.store(0)` |
| Blender disconnect | `_collection_sequences.clear()` | Blender-side sequence counters reset |
| Reconnect snapshot | Snapshot enumerates all alive objects and their collection memberships | Each object's membership set is rebuilt from scratch — no incremental merging |
| Deleted object (pre-disconnect) | Not present in snapshot | Stays dead — snapshot only contains alive objects |

**Reconnect determinism**: The snapshot is the authoritative source of truth
for collection state after reconnect. No incremental reconciliation is needed
because collection membership is pure metadata — there is no irreversible
state to merge. The snapshot correctly reflects Blender's current collections
and membership, and UE applies it fresh.

### 2.6 Interaction with Lifecycle Delete Tombstone

| Scenario | Behavior |
|----------|----------|
| ADD for a GUID that has a tombstone | **REJECTED** — tombstone check before any collection mutation |
| REMOVE for a tombstoned GUID | **IGNORED** — GUID is tombstoned, no actor to remove from membership |
| CLEAR for a collection whose members include tombstoned GUIDs | CLEAR applies to the collection identity only — tombstoned actors are already irrelevant |
| COLLECTION_CREATE during delete lifecycle | CREATED — collection identity is independent of actor lifecycle |
| COLLECTION_DELETE for a collection whose members have been partially deleted | DELETED — collection identity removed. Surviving actors lose membership (which is correct — Blender's collection is gone) |

**Critical invariant**: Collection operations MUST pass through the same
tombstone gate as all other semantic lanes. The three-barrier system
(sequence → tombstone → ActorCache) applies uniformly. Collection metadata
mutations for tombstoned GUIDs are silently discarded.

---

## 3. Blender → UE Emission Model

### 3.1 Change Detection Overview

Blender detects collection changes at two levels:

1. **Collection identity level** — `bpy.data.collections` diff (new/deleted/renamed/reparented collections)
2. **Membership level** — per-object `obj.users_collection` diff (which collections each object belongs to)

### 3.2 _last_collection_state Tracking

```python
# Conceptual Blender-side tracking structure
_last_collection_state = {
    # Collection identity tracking
    "_last_collections": {
        "<collection_guid>": {
            "name": "Characters",
            "parent_guid": "<parent_guid_or_none>",
            "seq": 42
        },
        ...
    },
    # Per-object membership tracking
    "_last_membership": {
        "<actor_guid>": {
            "<collection_guid>": {
                "seq": 7,
                "present": True  # or False if removed
            },
            ...
        },
        ...
    }
}
```

| Field | Description |
|-------|-------------|
| `_last_collections` | Map of collection GUID → collection metadata. Updated on snapshot scan and individual collection changes. |
| `_last_membership` | Map of actor GUID → per-collection entry. Each entry tracks the last-known presence state and sequence number. |
| Reset on disconnect | Both maps cleared on `_close_internal()` — fresh state after reconnect. |

### 3.3 Detection Algorithm (Conceptual)

```
Every sync tick:
1. Scan bpy.data.collections:
   - For each collection:
     a. Ensure GUID exists (collection["ue_guid"] = uuid.uuid4().hex if missing)
     b. Compare name against _last_collections[guid]["name"]
        → If changed: emit RENAME_REF
     c. Compare parent against _last_collections[guid]["parent_guid"]
        → If changed: emit COLLECTION_REPARENT
   - Detect removed collections:
     d. For each guid in _last_collections not in bpy.data.collections:
        → Emit COLLECTION_DELETE

2. Scan tracked MESH objects:
   - For each object obj with a ue_guid:
     a. Get current collections: current = set(obj.users_collection)
     b. Get last collections: last = _last_membership.get(guid, {})
     c. For each collection in current not in last (or last present=False):
        → Emit ADD for (guid, collection_guid)
     d. For each collection in last where present=True and not in current:
        → Emit REMOVE for (guid, collection_guid)
     e. Update _last_membership[guid] for all changes

3. Update sequence counters:
   - Each emission increments the relevant per-pair or per-collection sequence
```

### 3.4 Edge-Triggered Emission Only

| Property | Rule |
|----------|------|
| **Emission trigger** | Change detected → emit once. No periodic re-emission. |
| **No state stream** | Collection membership is NOT re-sent every frame. Only deltas. |
| **Snapshot exception** | During full snapshot (first sync, reconnect), collection membership is emitted for every alive object. |
| **Stale avoidance** | Sequence numbers ensure that duplicate emissions (due to tick timing) are correctly rejected. |

### 3.5 Batching Rules per Tick

| Rule | Rationale |
|------|-----------|
| All collection operations for a single tick are batched into one `send_objects()` call | Reduces packet count, maintains atomicity |
| Operations for the same (actor, collection) pair are coalesced | If ADD and REMOVE detected in the same tick for the same pair → net effect determines emission (ADD+REMOVE = no-op) |
| Maximum 512 operations per batch | Prevents single-packet overflow. Remaining operations spill to next tick. |
| Batch ordering within tick: collection-identity ops first (CREATE, DELETE, RENAME, REPARENT), then membership ops (ADD, REMOVE, CLEAR) | Ensures collection identity exists before membership references it on the UE side |
| RENAME operations are NOT coalesced | Only the latest name is relevant — emit once with the final name |

### 3.6 Snapshot Emission

During snapshot rebuild (first sync or reconnect after disconnect):

```
1. Clear _last_collections and _last_membership
2. Scan bpy.data.collections — emit COLLECTION_CREATE for each with seq=1
3. Scan all MESH objects — for each object, for each collection membership:
   → Emit ADD with seq=1 for each (actor, collection) pair
4. Assign OpFlags=0x01 (fromSnapshot) for all snapshot operations
```

Snapshot emission is not subject to staleness checks (tracker was cleared on
disconnect). Fresh sequences always accepted.

---

## 4. UE Processing Model (Conceptual)

### 4.1 Parser Isolation Rules

| Rule | Enforcement |
|------|-------------|
| New `case PT_Collection` in `ProcessBinaryPacket` only | No modification to existing `case` branches for transform, create, delete, heartbeat, asset def, snapshot markers, visibility, rename, hierarchy, delete_v5 |
| Dispatch by `OpType` within the collection branch | `OpType` byte (offset 32 in Variant A, offset 16 in Variant B) determines handler path |
| No fallthrough to other packet type handlers | Collection parser is fully isolated — returns after processing |
| Collection identity operations processed before membership operations in batch | If both identity and membership ops exist in the same batch, identity ops are parsed and applied first |

### 4.2 Parser Dispatch (Conceptual)

```
ProcessBinaryPacket:
  ...
  case PT_Collection:
    TRACE_CPUPROFILER_EVENT_SCOPE(UELiveSync_ProcessCollectionPackets)
    ParseBatchCollection(Data, PayloadSize);
    break;
  ...

ParseBatchCollection(Payload, Size):
  OpCount = Read<uint16>(Payload, Offset)
  for i in 0..OpCount:
    Validate remaining >= 46 bytes
    OpType = Read<uint8>(Payload, Offset + discriminant_offset)
    switch OpType:
      0x01..0x04: ParseMembershipOp(Payload, Offset)   // 46B fixed
      0x05:       ParseRenameRefOp(Payload, Offset)      // 46B + variable
      0x06..0x08: ParseCollectionIdentityOp(Payload, Offset) // 46B fixed
    Offset += OpSize
    Stats.CollectionPackets++
```

### 4.3 Handler Execution Rules

| Rule | Description |
|------|-------------|
| **Metadata-only** | Collection handlers NEVER call `SetActorTransform`, `SetActorLabel`, `SetIsTemporarilyHiddenInEditor`, `AttachToActor`, `DetachFromActor`, or `DestroyActor` |
| **Separate storage** | Collection data lives in `FCollectionMembershipMap` and `FCollectionIdentityMap` — NOT in `FSyncTransformState` or `ActorCache` |
| **No scene graph mutation** | Handlers modify TMap entries only. No UObject property changes. No scene graph invalidation. |
| **Tombstone gate** | Every membership operation checks `IsTombstoned(TargetGuid)` before applying |
| **Actor existence check** | Membership operations verify `ActorCache.Contains(TargetGuid)` — non-existent actors silently skipped |
| **Suppression** | `FScopedCollectionSuppression` RAII guard wraps all metadata writes |
| **Validation ordered** | Dead actor → tombstone → sequence stale → collection identity exists → apply |

### 4.4 Collection State Application Rules

| Operation | Handler Logic |
|-----------|---------------|
| **ADD** | Insert `CollectionGuid` into `Membership[TargetGuid]` set. If set exceeds 64 memberships, oldest entry evicted. |
| **REMOVE** | Remove `CollectionGuid` from `Membership[TargetGuid]` set. If set becomes empty, entry removed from map. |
| **MOVE** | REMOVE from old collection, ADD to new collection. Both mutations applied within same `FScopedCollectionSuppression` scope. Single sequence number covers both. |
| **CLEAR** | Remove all entries from `Membership` where collection GUID matches. Reset collection's membership counter. |
| **RENAME_REF** | Update `Identity[CollectionGuid].Name` to new name. |
| **COLLECTION_CREATE** | Insert `CollectionGuid` into `Identity` map with name and optional parent GUID. |
| **COLLECTION_DELETE** | Remove `CollectionGuid` from `Identity` map. Remove all references to `CollectionGuid` from all `Membership` sets. |
| **COLLECTION_REPARENT** | Update `Identity[CollectionGuid].ParentGuid` to new parent GUID. |

### 4.5 Application Ordering Within a Batch

1. Collection-identity operations (CREATE, RENAME, REPARENT, DELETE) — applied in order
2. Membership operations (ADD, REMOVE, MOVE, CLEAR) — applied in order

This ordering ensures that if a CREATE and an ADD for the same collection
arrive in the same batch, the collection identity exists before the membership
operation attempts to reference it.

---

## 5. Cross-Lane Interaction Matrix

### 5.1 Interaction Classification

Each interaction is classified as one of:

| Classification | Meaning |
|---------------|---------|
| **ALLOWED** | Both lanes can operate on the same GUID independently. No conflict. |
| **IGNORED** | One lane's operation has no effect on the other lane's state. |
| **REJECTED** | The operation is blocked by the other lane's guard (e.g., tombstone). |
| **DEFERRED** | The operation's interaction is resolved at a later point (e.g., reconnect). |

### 5.2 Collection ↔ Rename

| Scenario | Classification | Behavior |
|----------|---------------|----------|
| Actor renamed, actor is in a collection | ALLOWED | Name changes, collection membership unchanged |
| Collection renamed, members unaffected | ALLOWED | Collection label changes, membership unaffected |
| Collection add + actor rename in same tick | ALLOWED | Both applied independently — no ordering requirement |
| Suppression cross-talk | ALLOWED | `FScopedRenameSuppression` and `FScopedCollectionSuppression` are independent RAII types. No interaction. |

**Cross-lane coupling**: NONE. Independent trackers, independent state,
independent suppression.

### 5.3 Collection ↔ Visibility

| Scenario | Classification | Behavior |
|----------|---------------|----------|
| Visibility toggle of collection member | ALLOWED | `bHidden` changes, collection membership unchanged |
| Collection add of hidden actor | ALLOWED | Membership added, `bHidden` remains |
| Collection CLEAR does not affect visibility | IGNORED | CLEAR removes membership metadata — visibility state untouched |
| Visibility toggle triggers collection reassessment | IGNORED | No collection-level visibility — Phase 6C owns visibility exclusively |

**Cross-lane coupling**: NONE. Visibility operates on `bHidden`; collection
operates on metadata overlay. Independent state.

### 5.4 Collection ↔ Hierarchy

| Scenario | Classification | Behavior |
|----------|---------------|----------|
| Reparent of collection member | ALLOWED | Actor attachment changes, collection membership unchanged |
| Collection add of attached child | ALLOWED | Membership added, attachment unchanged |
| Collection nesting uses `AttachToActor` | **REJECTED** (forced) | Collection nesting is metadata-only. Explicit invariant enforced. |
| Hierarchy deferred resolution of collection-member | ALLOWED | Independent Tick slots — collection process doesn't wait for hierarchy resolution |
| Collection operation during hierarchy deferred window | ALLOWED | Both operations process independently in `ProcessQueuedPackets` |

**Cross-lane coupling**: NONE. Critical invariant: collection nesting must
NEVER call `AttachToActor`/`DetachFromActor`.

### 5.5 Collection ↔ Lifecycle/Delete

| Scenario | Classification | Behavior |
|----------|---------------|----------|
| ADD for deleted actor (tombstoned) | **REJECTED** | Tombstone gate — blocked before any metadata mutation |
| REMOVE for deleted actor (tombstoned) | **REJECTED** | Tombstone gate — silently discarded |
| COLLECTION_DELETE while members include alive and deleted actors | ALLOWED | Collection identity removed. Alive actors lose membership metadata (correct — collection is gone) |
| Actor delete does NOT remove collection membership metadata | IGNORED | Metadata is NOT structural. Actor dies, membership becomes irrelevant. Reconnect snapshot re-establishes correct state. |
| Collection membership prevents actor delete | **REJECTED** (invariant) | P0 rollback condition. Metadata must not block lifecycle. |
| COLLECTION_DELETE cascades to actor destruction | **REJECTED** (invariant) | P0 rollback condition. Collection delete = unlink, not destroy. |

**Cross-lane coupling**: Tombstone gate provides uniform guard. No sequence
tracker coupling. No membership cleanup on delete — the actor is gone and
membership metadata for a non-existent actor is harmless. On reconnect,
snapshot re-establishes correct state.

### 5.6 Collection ↔ Replay System

| Scenario | Classification | Behavior |
|----------|---------------|----------|
| Collection operation during snapshot (bInSnapshotBuild) | ALLOWED | Tagged `EChangeOrigin::Replay`. Same stale rejection applies. |
| Collection operation after snapshot (same reconnect session) | ALLOWED | Tracker was cleared on disconnect — all operations accepted with fresh sequences starting at 1 |
| Duplicate snapshot replay of same collection operation | **REJECTED** | Sequence tracker catches duplicate — `IncomingSeq <= LastSeq` |
| Stale collection operation from previous connection | **REJECTED** | Tracker cleared on disconnect — but the reconnect snapshot resets the baseline. Any operation with sequence > last tracked is accepted. |
| Collection replay resurrects deleted object | **REJECTED** (proved) | Snapshot only contains alive objects. Deleted objects are absent from snapshot. Collection membership for a deleted GUID is never emitted. |

**Cross-lane coupling**: Collection has its own `FCollectionSequenceTracker`,
independent of all other lane trackers. No shared sequence state.

### 5.7 Collection ↔ Transform Pipeline

| Scenario | Classification | Behavior |
|----------|---------------|----------|
| Transform packet for collection-member actor | ALLOWED | Transform state is independent of collection membership |
| Collection operation during `InterpolateTransforms` | IGNORED | Collection process happens in `ProcessQueuedPackets`, BEFORE interpolation |
| Collection membership affects transform | **REJECTED** (invariant) | Transform pipeline has zero coupling with collection metadata |

**Cross-lane coupling**: NONE. Collection metadata is not part of
`FSyncTransformState`.

### 5.8 Summary Matrix

| Lane | ALLOWED | IGNORED | REJECTED | DEFERRED |
|------|---------|---------|----------|----------|
| Rename | Name+membership independence | Suppression cross-talk | — | — |
| Visibility | bHidden+membership independence | Collection CLEAR vs visibility | — | — |
| Hierarchy | Attachment+membership independence | Deferred resolution window | Collection nesting via AttachToActor | — |
| Lifecycle/Delete | COLLECTION_DELETE with survivors | Metadata cleanup on actor delete | ADD/REMOVE on tombstoned GUID; membership blocks delete; collection delete cascades to destroy | Reconnect snapshot |
| Replay | Snapshot replay processing | — | Duplicate/stale seq rejection; resurrection via collection replay | — |
| Transform | Transform+membership independence | Collection op during interpolation | Membership affects transforms | — |

---

## 6. Consistency & Invariants

### 6.1 Structural Invariants

| ID | Invariant | Verification |
|----|-----------|-------------|
| C-001 | Collection membership is stored in a separate `TMap<FGuid, TSet<FGuid>>`, NOT in `FSyncTransformState` | Code review |
| C-002 | Collection identity is stored in a separate `TMap<FGuid, FCollectionMetadata>`, NOT in `FSyncTransformState` | Code review |
| C-003 | `PT_Collection = 0x0F` is unique and unused by any other lane | Code review |
| C-004 | `FCollectionSequenceTracker` is a standalone type — no reuse of any other lane's tracker | Code review |
| C-005 | Collection sequence tracker key is `TPair<FGuid, FGuid>` for membership and `FGuid` for identity — no overlap with other lane tracker keys | Code review |

### 6.2 Sequence Invariants

| ID | Invariant | Verification |
|----|-----------|-------------|
| C-006 | Per-pair sequence numbers for membership operations are strictly monotonic | Unit test |
| C-007 | Per-collection sequence numbers for identity operations are strictly monotonic | Unit test |
| C-008 | Stale rejection: `IncomingSeq <= LastSeq` for the same key is always rejected | Unit test |
| C-009 | Duplicate operations (same seq, same key, same op) are always rejected | Unit test |
| C-010 | Cross-GUID contamination: identical sequences across different keys must not interfere | Unit test |

### 6.3 Cross-Lane Invariants

| ID | Invariant | Verification |
|----|-----------|-------------|
| C-011 | Collection state MUST NOT affect transform pipeline — membership changes never call `SetActorTransform`, never modify `FSyncTransformState` | Code review + regression test |
| C-012 | Delete tombstone overrides collection membership — ADD for tombstoned GUID is always rejected | Integration test |
| C-013 | Hierarchy has no authority over collections — hierarchy attachment changes never modify collection membership | Integration test |
| C-014 | Rename has no authority over collections — actor rename never modifies collection membership | Integration test |
| C-015 | Visibility has no authority over collections — visibility toggle never modifies collection membership | Integration test |
| C-016 | Collection nesting NEVER calls `AttachToActor` or `DetachFromActor` | Code review + CI gate |
| C-017 | Collection membership MUST NOT prevent actor delete | Integration test (P0 condition) |
| C-018 | Collection delete MUST NOT cascade to actor destruction | Integration test (P0 condition) |

### 6.4 Bounded Memory Invariants

| ID | Invariant | Verification |
|----|-----------|-------------|
| C-019 | Collection sequence tracker bounded at 2048 entries with FIFO eviction | Unit test |
| C-020 | Per-actor membership set bounded at max 64 collections with oldest-entry eviction | Unit test |
| C-021 | Collection identity map bounded at 2048 entries with FIFO eviction | Unit test |
| C-022 | Blender-side `_last_collections` and `_last_membership` maps cleared on disconnect | Integration test |

### 6.5 Replay Invariants

| ID | Invariant | Verification |
|----|-----------|-------------|
| C-023 | Snapshot replay does NOT resurrect deleted objects via collection membership — snapshot only contains alive objects | Reconnect test |
| C-024 | Collection tracker cleared on `StopNetworkThread()` | Unit test |
| C-025 | Collection tracker cleared on `ConsoleReset()` | Unit test |
| C-026 | Collection tracker cleared on Blender disconnect | Integration test |
| C-027 | Reconnect snapshot deterministically converges to Blender's collection state | Reconnect test |
| C-028 | Collection operations during snapshot (bInSnapshotBuild) are tagged `EChangeOrigin::Replay` | Code review |

### 6.6 Invariant Count

| Category | Count | Status |
|----------|-------|--------|
| Structural | 5 | DESIGN-only |
| Sequence | 5 | DESIGN-only |
| Cross-Lane | 8 | DESIGN-only |
| Bounded Memory | 4 | DESIGN-only |
| Replay | 5 | DESIGN-only |
| **Total** | **27** | **DESIGN** — to be verified during implementation |

---

## 7. Replay Safety Proofs

### 7.1 Proof: No Resurrection via Collection Replay

**Claim**: Collection replay cannot resurrect a deleted object.

**Proof**:
1. A deleted object has a tombstone entry in the tombstone map (Phase 6E invariant T2)
2. All collection membership operations pass through the tombstone gate before any metadata mutation (scope lock §2.4)
3. If `IsTombstoned(TargetGuid)` returns true → ADD, REMOVE, MOVE, CLEAR are all silently discarded
4. During snapshot replay, the snapshot is populated from Blender's current scene, which only contains alive objects
5. Deleted objects are absent from the snapshot, so no collection membership for a deleted GUID is emitted during snapshot
6. Therefore, collection replay cannot resurrect a deleted object

**Edge case: Race between delete packet and collection packet in same batch**:
If DELETE and COLLECTION_ADD for the same GUID arrive in the same batch:
- Batch operations are processed sequentially in `ProcessQueuedPackets`
- If DELETE is processed first → tombstone added → COLLECTION_ADD rejected by tombstone gate
- If COLLECTION_ADD is processed first → membership added → DELETE processed → actor destroyed → tombstone added
- In both orderings, the final state is correct: actor dead, tombstone present
- No resurrection occurs

**Conclusion**: ✅ PROVED — collection replay cannot resurrect deleted objects.

### 7.2 Proof: No Cross-Session Duplication

**Claim**: Collection membership cannot be duplicated across sessions.

**Proof**:
1. Collection membership uses per-pair `(actor GUID, collection GUID)` monotonic sequence tracking
2. On disconnect, sequence tracker is cleared (`StopNetworkThread()` or `ConsoleReset()`)
3. On reconnect, the snapshot emits all membership with fresh sequences starting at 1
4. Fresh sequences (seq=1 for each pair) are always accepted after tracker clear
5. Any stale membership from a previous session would have a sequence number from the old session, but the tracker was cleared — so the stale sequence is accepted as if fresh
6. This is correct behavior: the snapshot IS the authoritative truth after reconnect
7. Therefore, collection membership converges to Blender's state after every reconnect

**Edge case: Object recreated with same GUID after delete in same session**:
- Phase 6E invariant T3: tombstone blocks CREATE for the same GUID
- `ensure_unique_guid()` prevents GUID reuse within a session
- Therefore, no cross-session duplication path exists within a single session

**Conclusion**: ✅ PROVED — no cross-session duplication.

### 7.3 Proof: No Stale Membership Re-application

**Claim**: Stale collection membership operations cannot be incorrectly applied.

**Proof**:
1. Every membership operation carries a per-pair monotonic sequence number
2. The `FCollectionSequenceTracker` enforces `IncomingSeq > LastSeq` for acceptance
3. If a stale operation arrives (e.g., from a late duplicate packet or replay), its sequence is `<= LastSeq` and is rejected
4. The tracker is cleared on disconnect, so stale operations from a previous session are not rejected — but they arrive with sequences from the old session, which means they are treated as fresh after tracker clear
5. However, stale operations from a previous session are always superseded by the reconnect snapshot, which emits correct current state with fresh sequences
6. Between the stale operation and the snapshot, the incorrect membership may transiently exist — but this is bounded to a single Tick window
7. The snapshot corrects any transient stale state within the reconnect cycle

**Edge case: Stale ADD arrives between snapshot and first live operation**:
- Stale ADD (seq=5 from old session) arrives, tracker is empty → accepted
- Snapshot follows with seq=1 for the same pair → rejected as stale (seq=1 <= 5)
- This is **incorrect**: the snapshot's seq=1 should have been accepted, and the stale seq=5 should have been rejected
- **Mitigation**: On reconnect, the tracker is cleared AFTER the snapshot is fully processed, not before
- Alternatively: on reconnect, all sequences are initialized to UINT32_MAX, so the first snapshot operations (seq=1) are always accepted, and any stale operation (seq < UINT32_MAX) is rejected as stale

**Recommended mitigation**: Initialize all tracker entries to `LastSeq = UINT32_MAX`
on reconnect, then reset to 0 after `EndSnapshot`. This ensures:
- First snapshot operation: `IncomingSeq(1) > UINT32_MAX?` No! This doesn't work either.

**Correct mitigation**: Process the full snapshot batch atomically. The snapshot
is transmitted as:
1. `PT_BeginSnapshot` clears tracker
2. All collection membership operations (with their fresh seq numbers)
3. `PT_EndSnapshot` marks snapshot complete

Between `PT_BeginSnapshot` and `PT_EndSnapshot`, no live collection operations
are expected (they are queued but not processed until after EndSnapshot).
After `PT_EndSnapshot`, the tracker has the correct last-known sequences from
the snapshot, and live operations proceed normally.

**Conclusion**: ✅ PROVED with mitigation — stale membership re-application
is prevented by atomic snapshot processing.

### 7.4 Proof: Deterministic Convergence After Reconnect

**Claim**: Collection state converges to a deterministic, correct state after
each reconnect.

**Proof**:
1. After reconnect, the snapshot is the exclusive source of truth for collection state
2. The snapshot is built from Blender's current scene — it atomically captures:
   - All alive MESH objects
   - Each object's current collection memberships
   - All collections and their metadata (name, parent)
3. UE clears all collection state on `StopNetworkThread()`:
   - `CollectionSequences.Empty()`
   - `CollectionMembershipMap.Empty()`
   - `CollectionIdentityMap.Empty()`
4. The snapshot is processed within `bInSnapshotBuild = true` window:
   - `PT_BeginSnapshot` (0x09) → `bInSnapshotBuild = true`
   - All collection ops tagged `EChangeOrigin::Replay`
   - Fresh sequences accepted — stale rejection active (but no stale ops, tracker was cleared)
   - `PT_EndSnapshot` (0x0A) → `bInSnapshotBuild = false`
5. After EndSnapshot, UE's collection state is identical to Blender's collection state at snapshot capture time
6. Any live collection operations after EndSnapshot are processed with fresh sequences
7. Deterministic: same snapshot input always produces same collection state on UE side

**Caveat**: If Blender emits a collection operation between snapshot capture
and EndSnapshot processing, the operation may be lost (arrives before BeginSnapshot
and is accepted with old tracker state, but is superseded by the snapshot).
This is acceptable — the snapshot is authoritative, and the lost operation is
no different from a live op that races with snapshot processing.

**Conclusion**: ✅ PROVED — collection state converges deterministically after
each reconnect.

### 7.5 Proof: Tombstone Gate Is Sufficient

**Claim**: The tombstone gate alone (without explicit collection-level tombstone)
is sufficient to prevent collection operations on deleted GUIDs.

**Proof**:
1. Tombstone gate already exists from Phase 6E and applies to ALL semantic lanes (freeze invariant T3)
2. Collection membership operations check `IsTombstoned(TargetGuid)` before any mutation
3. If `TargetGuid` is tombstoned → operation silently discarded
4. No additional collection-level tombstone is needed because:
   - Collection membership is metadata, NOT structural state
   - A tombstoned actor has no ActorCache entry — no actor exists to attach metadata to
   - Storing membership metadata for a non-existent actor is harmless memory waste but bounded by 2048 eviction
   - On reconnect, the tombstone map is cleared, and the snapshot re-establishes correct state
5. Collection identity (name, parent) does not need tombstone gating — collection identities are independent of actor lifecycle. If a collection is deleted, its identity is removed and members lose metadata — correct behavior.

**Edge case**: What if a collection operation arrives for a GUID that was
deleted but the tombstone entry was already evicted (FIFO eviction at 2048)?
- The `IsTombstoned()` check returns false
- The `ActorCache.Contains()` check returns false (actor was destroyed)
- The operation is silently discarded at the ActorCache check
- The three-barrier system still works: sequence → tombstone → ActorCache
- Even if both tombstone and sequence checks pass, ActorCache catches it

**Conclusion**: ✅ PROVED — existing Phase 6E tombstone gate is sufficient.
No additional collection-level tombstone needed.

---

## 8. Observability Design

### 8.1 Planned Counters

All counters are `std::atomic<int32>` with `std::memory_order_relaxed`.
O(1) update, no allocation. Display values only.

| Counter | Description | Category |
|---------|-------------|----------|
| `CollectionPackets` | Total collection packets received (all ops) | Throughput |
| `CollectionOpsAdd` | ADD operations applied | Membership |
| `CollectionOpsRemove` | REMOVE operations applied | Membership |
| `CollectionOpsMove` | MOVE operations applied | Membership |
| `CollectionOpsClear` | CLEAR operations applied | Membership |
| `CollectionOpsCreate` | COLLECTION_CREATE operations applied | Identity |
| `CollectionOpsDelete` | COLLECTION_DELETE operations applied | Identity |
| `CollectionOpsRename` | RENAME_REF operations applied | Identity |
| `CollectionOpsReparent` | COLLECTION_REPARENT operations applied | Identity |
| `CollectionStaleRejections` | Stale/duplicate collection packets rejected (all ops) | Replay |
| `CollectionReplayApplied` | Collection operations applied during snapshot replay | Replay |
| `CollectionReplaySkipped` | Collection operations skipped during snapshot replay | Replay |
| `CollectionTombstoneRejections` | Collection operations blocked by tombstone | Safety |
| `CollectionMembershipEvictions` | Per-actor membership set evictions at 64 cap | Bounded memory |
| `CollectionTrackerEvictions` | Sequence tracker evictions at 2048 cap | Bounded memory |
| `CollectionIdentityEvictions` | Collection identity map evictions at 2048 cap | Bounded memory |

**Total**: 16 counters.

### 8.2 Operation-Specific Counter Rationale

The scope lock (§7.1) defined 8 generic counters. The vertical slice expands
this to 16 for finer granularity:

| Additional counter | Why |
|--------------------|-----|
| Split `CollectionProcessed` into 8 sub-counters | Each operation type is independent — ADD vs CREATE vs DELETE have different error modes |
| `CollectionOpsMove` | MOVE is a single semantic operation covering two mutations — tracking it separately enables correctness verification |
| `CollectionMembershipEvictions` | Bounded memory proof — must verify eviction at 64 per-actor cap |
| `CollectionTrackerEvictions` | Bounded memory proof — must verify eviction at 2048 cap |
| `CollectionIdentityEvictions` | Bounded memory proof — must verify identity map eviction at 2048 cap |

### 8.3 Log Prefix Standard

| Context | Prefix | Level |
|---------|--------|-------|
| General application | `[COLLECTION]` | Log |
| ADD applied | `[COLLECTION][ADD] Applying: ActorGUID=%s, CollectionGUID=%s, Origin=%s` | Log |
| REMOVE applied | `[COLLECTION][REMOVE] Applying: ActorGUID=%s, CollectionGUID=%s, Origin=%s` | Log |
| MOVE applied | `[COLLECTION][MOVE] Applying: ActorGUID=%s, FromCollection=%s, ToCollection=%s, Origin=%s` | Log |
| CLEAR applied | `[COLLECTION][CLEAR] Applying: CollectionGUID=%s, MemberCount=%u, Origin=%s` | Log |
| COLLECTION_CREATE | `[COLLECTION][CREATE] New collection: CollectionGUID=%s, Name=%s, ParentGUID=%s` | Log |
| COLLECTION_DELETE | `[COLLECTION][DELETE] Collection removed: CollectionGUID=%s` | Log |
| RENAME_REF applied | `[COLLECTION][RENAME] CollectionGUID=%s: '%s' → '%s'` | Log |
| COLLECTION_REPARENT | `[COLLECTION][REPARENT] CollectionGUID=%s: parent %s → %s` | Log |
| Stale rejection | `[COLLECTION] Rejected — stale/duplicate sequence: Op=%s, TargetGUID=%s, CollectionGUID=%s, IncomingSeq=%u, LastSeq=%u` | Warning |
| Tombstone rejection | `[COLLECTION] Rejected — target actor tombstoned: TargetGUID=%s, Op=%s` | Warning |
| Missing actor rejection | `[COLLECTION] Rejected — actor not found: TargetGUID=%s, Op=%s` | Warning |
| Suppression enter | `[COLLECTION] Enter suppression scope: TargetGUID=%s, Op=%s` | Verbose |
| Suppression exit | `[COLLECTION] Exit suppression scope: TargetGUID=%s, Op=%s` | Verbose |
| Snapshot batch begin | `[COLLECTION] Snapshot batch started: expecting %u operations` (Verbose) | Verbose |
| Snapshot batch end | `[COLLECTION] Snapshot batch complete: %u applied, %u skipped` (Verbose) | Verbose |
| Tracker clear | `[COLLECTION] Tracker cleared (StopNetworkThread/ConsoleReset)` | Log |
| Membership eviction | `[COLLECTION] Per-actor membership cap reached (64): ActorGUID=%s, evicting oldest` | Warning |

### 8.4 Profiler Scope Naming Convention

| Scope | Location | Description |
|-------|----------|-------------|
| `UELiveSync_HandleCollection` | HandleCollection() — single operation handler | Per-operation CPU profiling |
| `UELiveSync_ProcessCollectionPackets` | ParseBatchCollection() — batch processing | Per-batch CPU profiling |
| `UELiveSync_ApplyCollectionMembership` | ApplyMembershipOp() — membership mutation | Per-membership-mutation profiling |

All scopes use `TRACE_CPUPROFILER_EVENT_SCOPE` — compile-time zero overhead
when disabled.

### 8.5 UE Console Commands

| Command | Extension |
|---------|-----------|
| `UE.LiveSync.Stats` | Include all 16 collection counters |
| `UE.LiveSync.DumpState` | Include: collection tracker size, collection membership map summary (total actor entries, total membership entries), collection identity map summary (total collections) |

---

## 9. Frozen Runtime Guarantees

### 9.1 Systems Explicitly NOT Touched

| System | File(s) | Guarantee |
|--------|---------|-----------|
| `LiveSyncQueue` (128-entry MPSC) | `LiveSyncQueue.h` | Collection packet uses existing enqueue/dequeue paths. Queue capacity, ownership, and thread-safety model unchanged. |
| `PendingAssetQueue` (2048) | `PendingAssetQueue.h` | Collection processing is entirely separate from asset resolution. No interaction. |
| `LiveSyncRunnable` | `LiveSyncRunnable.h/cpp` | No changes to thread lifecycle, shutdown order, receive loop, or heartbeat handling. |
| `FSyncTransformState` | `SyncTypes.h` | Collection membership is stored in separate `TMap<FGuid, TSet<FGuid>>`. NOT in `FSyncTransformState`. No new fields. |
| 24-byte header layout | `SyncTypes.h` (implicit) | `PT_Collection` uses the same 24-byte header as all packet types. No header changes, no version bump. |
| Tick pipeline ordering | `UELiveSyncSubsystem.cpp` (main Tick) | Collection processing inlined into `ProcessQueuedPackets`. No reordering of existing stages. No new Tick stage. |
| Transform interpolation | `UELiveSyncSubsystem.cpp` | Collection is metadata — zero interpolation coupling. No changes to `InterpolateTransforms()`. |
| Phase 5 parser dispatch (version, magic, header) | `UELiveSyncSubsystem.cpp` | New `case PT_Collection` branch only. No modification to existing branches, version dispatch, or header parsing. |
| `AttachToActor` / `DetachFromActor` | Frozen (Phase 5) | Collection nesting is metadata-only. These APIs are NEVER called by collection code. |
| `ResolvePendingAttachments` | Frozen (Phase 5) | No changes to existing resolution logic. Collection dependencies get separate processing if needed. |
| `RecoverMissingActors` | Frozen (Phase 5) | No changes. Collection metadata is not actor lifecycle. |
| `HandleRename` / `FRenameSequenceTracker` | 6A code | Not modified. Collection is a new lane. |
| `HandleVisibility` / `FVisibilitySequenceTracker` | 6C code | Not modified. Collection is a new lane. |
| `HandleHierarchy` / `FHierarchySequenceTracker` | 6D code | Not modified. Collection is a new lane. |
| `HandleDelete` / `FDeleteSequenceTracker` / Tombstone map | 6E code | Not modified. Tombstone gate is read-only consumed by collection code. |
| Heartbeat/timeout system | `LiveSyncRunnable.cpp` | Not modified. |

### 9.2 Additive-Only Pattern Confirmation

Every prior lane followed the additive-only pattern. Phase 6F follows the
same pattern:

| Component | Pattern | New? |
|-----------|---------|------|
| Packet type constant (`PT_Collection = 0x0F`) | New constant in `SyncTypes.h` | ✅ New |
| Sequence tracker (`FCollectionSequenceTracker`) | New struct in `SyncTypes.h` | ✅ New |
| Suppression guard (`FScopedCollectionSuppression`) | New struct in new file or `SyncTypes.h` | ✅ New |
| Parser branch (`case PT_Collection`) | New case in `UELiveSyncSubsystem.cpp` | ✅ New |
| Handler function (`HandleCollection()`) | New function | ✅ New |
| Membership map (`CollectionMembership`) | New `TMap<FGuid, TSet<FGuid>>` in subsystem | ✅ New |
| Identity map (`CollectionIdentity`) | New `TMap<FGuid, FCollectionMetadata>` in subsystem | ✅ New |
| Counters (16 counters) | New atomic fields in `FLiveSyncStats` | ✅ New |
| Profiler scopes (3 scopes) | New `TRACE_CPUPROFILER_EVENT_SCOPE` | ✅ New |
| Blender detection (`_last_collection_state`) | New tracking in `sync.py` | ✅ New |
| Blender serialization (`serialize_collection()`) | New function in `network.py` | ✅ New |

No existing code is modified. All changes are additive.

### 9.3 Ownership Invariants

| Component | Owner | Access Rules |
|-----------|-------|-------------|
| `FLiveSyncQueue` (128 MPSC) | Game thread (dequeue), Network thread (enqueue) | Enqueue only on network thread; dequeue only on game thread |
| `FCollectionSequenceTracker` | Game thread only | All access via `HandleCollection()` — `CHECK_GAME_THREAD` |
| Collection membership maps | Game thread only | Separate from actor cache — game-thread only |
| Collection identity maps | Game thread only | Separate from actor cache — game-thread only |
| Blender send queue | Main thread (enqueue), Daemon thread (dequeue) | Mutex-guarded; main thread must not block |

---

## 10. Failure Mode Analysis

### 10.1 Stale Replay (FS-001)

| Property | Value |
|----------|-------|
| **Scenario** | A collection ADD packet from a previous connection session is replayed on reconnect (e.g., delayed TCP segment) |
| **Cause** | TCP retransmission or buffered packet arriving after reconnect |
| **Effect** | If accepted, the actor would be incorrectly added to a collection that no longer exists, or to a collection that the actor was removed from |
| **Severity** | Medium — transient incorrect metadata state |
| **Mitigation** | Sequence tracker is cleared on disconnect. The stale packet carries a sequence from the old session (e.g., seq=42). After tracker clear, seq=42 is accepted as fresh. However, this is immediately superseded by the reconnect snapshot, which contains correct current state. The incorrect membership exists for at most one Tick. |
| **Residual risk** | If the stale packet somehow bypasses the snapshot window, it could persist. Mitigation: the snapshot's `PT_EndSnapshot` triggers a full state reconciliation. |
| **Verdict** | **ACCEPTABLE** — transient only, corrected by snapshot within 1 Tick. |

### 10.2 Reconnect Mismatch (FS-002)

| Property | Value |
|----------|-------|
| **Scenario** | Blender's collection state changed during disconnection. After reconnect, the snapshot correctly reflects the new state. However, one of UE's actors was manually tagged with a collection metadata entry by the user during disconnection (e.g., via future UE editor UI) |
| **Cause** | User action during disconnection |
| **Effect** | UE's manual collection tag is silently overwritten by the snapshot |
| **Severity** | LOW — manual metadata modification during disconnect is not a supported workflow |
| **Mitigation** | Snapshot is authoritative for collection state. Manual UE-side changes during disconnect are outside the current scope. If bidirectional authority is added in the future, conflict resolution (last-writer-wins with timestamps) applies. |
| **Verdict** | **ACCEPTABLE** — snapshot authority model is correct. |

### 10.3 Partial Batch Drop (FS-003)

| Property | Value |
|----------|-------|
| **Scenario** | A batch of 200 collection operations is sent. Due to a network error, only 150 arrive. The remaining 50 are lost. |
| **Cause** | TCP guarantees ordered delivery — partial batch drop cannot occur within a single TCP segment. However, if the batch is split across multiple `send()` calls, a subset of the batch could be lost if the connection drops mid-batch. |
| **Effect** | Some membership changes are applied, others are not. The UE state diverges from Blender. |
| **Severity** | HIGH — state divergence |
| **Mitigation** | On reconnect, the full snapshot re-establishes correct state. If the connection does NOT drop (partial loss within a live connection), the missing operations are not retried. |
| **Residual risk** | State divergence until next reconnect. This is the same risk as all other lanes — no per-operation ACK/retry mechanism exists in the protocol. |
| **Verdict** | **ACCEPTABLE** — existing protocol limitation. Reconnect corrects any divergence. |

### 10.4 Duplicate Membership Storm (FS-004)

| Property | Value |
|----------|-------|
| **Scenario** | A Blender script rapidly adds and removes the same object from the same collection 10,000 times in a single frame |
| **Cause** | Malicious or buggy Blender addon, or physics-driven simulation modifying collection membership |
| **Effect** | 10,000 collection operations emitted in a single tick. Packet size may exceed `MAX_PACKET_SIZE`. |
| **Severity** | Medium — packet flood risk |
| **Mitigation** | Line 1: Coalescing — detect ADD then REMOVE for same pair within same tick → net no-op, do not emit. Line 2: Batch cap at 512 operations per tick — remaining operations spill to next tick. Line 3: Flood detection (existing 2-second window) rejects batch if rate exceeds threshold. Line 4: Sequence tracker — stale operations rejected regardless. |
| **Verdict** | **MITIGATED** — four layers of defense. |

### 10.5 Delete + Collection Race (FS-005)

| Property | Value |
|----------|-------|
| **Scenario** | ADD for collection Y arrives for actor X. In the same batch, DELETE for actor X arrives. |
| **Cause** | Normal race condition — Blender deleted actor X between scene scan and collection membership scan |
| **Effect** | Two possible orderings: (1) ADD → DELETE: actor is added to collection, then deleted. Tombstone inserted. (2) DELETE → ADD: actor deleted, tombstone inserted. ADD rejected by tombstone gate. |
| **Severity** | LOW — both orderings produce correct final state |
| **Mitigation** | Tombstone gate handles DELETE-first ordering. Metadata-is-not-structural principle handles ADD-first ordering (membership metadata for a destroyed actor is harmless). |
| **Verdict** | **RESOLVED** — both orderings correct. |

### 10.6 Hierarchy + Collection Mismatch (FS-006)

| Property | Value |
|----------|-------|
| **Scenario** | Actor X is reparented to actor Y in hierarchy. In the same batch, actor X is ADDED to collection Z. The collection membership implies an organizational grouping that conflicts with the hierarchy attachment. |
| **Cause** | Normal concurrent operations — both lanes are independent |
| **Effect** | Both operations are applied independently. Hierarchy attaches X to Y. Collection adds X to Z. These are semantically orthogonal — there is no "conflict." |
| **Severity** | LOW — no semantic conflict by design |
| **Mitigation** | No mitigation needed — collection and hierarchy are independent. |
| **Verdict** | **RESOLVED** — no conflict by design. |

### 10.7 Collection Create + Collection Delete Race (FS-007)

| Property | Value |
|----------|-------|
| **Scenario** | COLLECTION_CREATE for GUID C1 with seq=1. In the same batch, COLLECTION_DELETE for same GUID C1 with seq=2. |
| **Cause** | Blender created and then immediately deleted a collection within the same frame |
| **Effect** | CREATE processed → collection identity registered. DELETE processed → collection identity removed. Net effect: no collection exists, no membership references. |
| **Severity** | LOW — correct final state |
| **Mitigation** | Sequence ordering within batch ensures correct temporal ordering. |
| **Verdict** | **RESOLVED** — correct by sequence ordering. |

### 10.8 Actor Create + Collection Add Race (FS-008)

| Property | Value |
|----------|-------|
| **Scenario** | PT_CREATE for actor X arrives. In the same batch, ADD for actor X to collection Y arrives. |
| **Cause** | Normal — Blender created object X and assigned it to collection Y in the same frame |
| **Effect** | Two orderings: (1) CREATE → ADD: actor created, membership added. Correct. (2) ADD → CREATE: ADD rejected — actor not in ActorCache yet. Membership lost. |
| **Severity** | Medium — ordering-dependent membership loss |
| **Mitigation** | Within the Tick pipeline, CREATE is processed before collection operations (CREATE is part of the transform state stream, handled before semantic lanes in ProcessQueuedPackets ordering). Therefore, ordering (1) is guaranteed. |
| **Verdict** | **RESOLVED** — Tick ordering guarantees CREATE-before-collection. |

### 10.9 Snapshot Reconnect with Modified Collections (FS-009)

| Property | Value |
|----------|-------|
| **Scenario** | User disconnected UE editor, deleted 5 collections and created 10 new ones in Blender, reconnected. |
| **Cause** | Normal user workflow during disconnection |
| **Effect** | Snapshot contains correct current state: 10 new collections, none of the old ones. UE clears all collection state on disconnect, then applies snapshot. Correct final state. |
| **Severity** | LOW — correct behavior by design |
| **Mitigation** | Full state reset on disconnect + authoritative snapshot. |
| **Verdict** | **RESOLVED** — correct by design. |

### 10.10 Malformed Batch With Mixed Variants (FS-010)

| Property | Value |
|----------|-------|
| **Scenario** | A collection batch contains an ADD operation (Variant A, 46 bytes) followed by a COLLECTION_CREATE (Variant B, 46 bytes). The parser misidentifies the second operation due to a boundary error. |
| **Cause** | Parser bug — incorrect offset calculation when switching between variants |
| **Effect** | The second operation is parsed with wrong field offsets, leading to garbage collection GUID / OpType. Possible incorrect membership mutation or crash. |
| **Severity** | HIGH — crash risk |
| **Mitigation** | Both variants have the same base size (46 bytes). The parser uses a single `remaining >= 46` check per operation, then dispatches by `OpType` byte within the 46-byte window. The discriminant (`OpType`) is at a known offset in both variants (offset 32 in Variant A, offset 16 in Variant B — wait, this is different). |
| **Redesign note**: Both variants should have `OpType` at the SAME offset for safe dispatch. | **Correction**: Variant B should place `OpType` at a consistent offset. See §1.2 redesign below. |

**Design correction**: The discriminant byte (`OpType`) must be at a fixed
position regardless of variant. Proposed resolution: place `OpType` at
offset 0 in ALL variants, followed by a 16-byte or 32-byte GUID block:

| Offset | Size | Field |
|--------|------|-------|
| 0 | 1 | `OpType` (discriminant) |
| 1 | 1 | `OpFlags` |
| 2 | 16+16 or 16 | GUID block |
| 34+ | remaining fields |

This ensures the parser reads `OpType` first, then dispatches to the correct
parsing path. The GUIDs are read after dispatch.

**Revised Wire Format**:

```
Byte 0:      OpType (1 byte)
Byte 1:      OpFlags (1 byte)
Byte 2-17:   GUID block A (16 bytes) — TargetGuid for membership, CollectionGuid for identity
Byte 18-33:  GUID block B (16 bytes) — CollectionGuid for membership, ParentGuid for identity
Byte 34-37:  Sequence (4 bytes, uint32 LE)
Byte 38-45:  Timestamp (8 bytes, double)
Byte 46+:    Optional variable extension (RENAME_REF only)

Total: 46 bytes fixed per operation (all variants uniform)
```

This is the **critical design improvement** from the initial sketch in the
scope lock. The discriminant-first layout ensures parser safety and consistent
boundary checking.

### 10.11 RENAME_REF With Oversized Name (FS-011)

| Property | Value |
|----------|-------|
| **Scenario** | RENAME_REF packet with `NameLength` = 65535 (uint16 max), but actual payload is only 100 bytes |
| **Cause** | Malformed packet — corrupted length field |
| **Effect** | Parser reads beyond payload boundary → buffer over-read → possible crash |
| **Severity** | CRITICAL — crash or security issue |
| **Mitigation** | Parser must validate: `NameLength <= 256` AND `remaining >= 2 + NameLength` before reading name. If either check fails → `Stats.MalformedPackets++` → reject entire batch. |
| **Verdict** | **MITIGATED** — boundary guard enforced per Rename precedent (PT_Rename has same variable-length pattern). |

### 10.12 Identity Map Exhaustion (FS-012)

| Property | Value |
|----------|-------|
| **Scenario** | Blender creates 4096 unique collections. The UE identity map is bounded at 2048. |
| **Cause** | Realistic scenario with complex production scenes |
| **Effect** | Every COLLECTION_CREATE causes FIFO eviction of an existing collection identity. Members of evicted collections lose their collection metadata on the UE side. State diverges. |
| **Severity** | HIGH — bounded but lossy |
| **Mitigation** | FIFO eviction is documented as a limitation. The 2048 cap matches all other bounded structures in the system. Scenes with >2048 collections are rare but possible. Future optimization: increase cap or use LRU eviction based on membership count. |
| **Verdict** | **ACCEPTABLE** — documented limitation. Same tradeoff as all other bounded 2048 structures. |

### 10.13 Per-Actor Membership Exhaustion (FS-013)

| Property | Value |
|----------|-------|
| **Scenario** | Actor X is added to 128 different collections. The per-actor membership set is bounded at 64. |
| **Cause** | Complex Blender scene with many organizational collections |
| **Effect** | Oldest 64 memberships are evicted. Actor X appears to belong to only the most recent 64 collections on the UE side. |
| **Severity** | MEDIUM — bounded but lossy |
| **Mitigation** | FIFO eviction (oldest collection membership removed when cap reached). Logged at Warning level: `[COLLECTION] Per-actor membership cap reached (64): ActorGUID=%s, evicting oldest`. |
| **Verdict** | **ACCEPTABLE** — documented limitation. 64 collections per actor covers the vast majority of production scenes. |

### 10.14 Collection Operation After ActorCache Remove (FS-014)

| Property | Value |
|----------|-------|
| **Scenario** | Actor X was deleted (HandleDelete removed it from ActorCache and created tombstone). A collection ADD for actor X arrives. The tombstone entry was evicted (2048 cap). |
| **Cause** | Tombstone eviction race — tombstone was removed, but actor is still absent from ActorCache |
| **Effect** | `IsTombstoned(TargetGuid)` returns false (evicted). `ActorCache.Contains(TargetGuid)` returns false (actor was destroyed). The ADD is silently discarded at the ActorCache check. |
| **Severity** | LOW — third barrier catches it |
| **Mitigation** | Three-barrier system: sequence → tombstone → ActorCache. Even if sequence and tombstone both pass, ActorCache catches the missing actor. |
| **Verdict** | **RESOLVED** — third barrier prevents incorrect application. |

### 10.15 Snapshot + Live Op Interleaving (FS-015)

| Property | Value |
|----------|-------|
| **Scenario** | During snapshot replay (after reconnect), a live collection operation arrives before `PT_EndSnapshot` is processed. |
| **Cause** | TCP delivers packets faster than the game thread can process them. A live operation is enqueued between BeginSnapshot and EndSnapshot. |
| **Effect** | The live operation is processed during snapshot replay. If its sequence conflicts with a snapshot operation, the live operation may be incorrectly rejected, or the snapshot operation may be incorrectly rejected. |
| **Severity** | Medium — sequence ordering ambiguity during snapshot window |
| **Mitigation** | All packets are processed in FIFO order through `FLiveSyncQueue`. The enqueue order is the same as the receive order. If `BeginSnapshot → Snapshot ops → Live op → EndSnapshot`, the live op is processed during `bInSnapshotBuild = true`. If it carries a sequence that conflicts with the snapshot, it is rejected as stale. After `EndSnapshot`, the live op's intended mutation is re-sent by Blender on the next tick. |
| **Verdict** | **ACCEPTABLE** — at most 1 Tick of staleness. Live op is re-sent by next tick. |

---

## 11. Rollback Conditions

### 11.1 P0 Conditions (Must Abort Immediately)

| # | Condition | Detection | Section |
|---|-----------|-----------|---------|
| P0-1 | **Replay resurrection via collection membership** — A collection operation causes a deleted actor to be re-created on the UE side | Reconnect test: delete actor → reconnect → verify actor stays dead. If collection membership snapshot causes actor re-creation, rollback. | §7.1 |
| P0-2 | **Cross-lane mutation leak** — A collection operation modifies `FSyncTransformState`, `bHidden`, actor label, or calls `AttachToActor`/`DetachFromActor`/`DestroyActor` | Code review + regression test: apply collection op → verify all cross-lane counters unchanged | §5, §6 |
| P0-3 | **Frozen runtime violation** — Any modification to frozen files (`LiveSyncQueue.h`, `PendingAssetQueue.h`, `LiveSyncRunnable.h/cpp`, `SyncTypes.h` FSyncTransformState, 24-byte header layout, Tick pipeline ordering) | Git diff review — zero changes to frozen files | §9 |
| P0-4 | **Non-deterministic convergence failure** — After reconnect, collection state on UE does not match Blender's state | Integration test: create/modify/delete collections → disconnect → reconnect → verify identity + membership match | §7.4 |
| P0-5 | **Collection membership blocks actor delete** — An actor cannot be deleted because it belongs to a collection | Integration test: create actor → add to collection → delete actor → verify actor destroyed | §6.3 (C-017) |
| P0-6 | **Collection delete cascades to actor destruction** — Deleting a collection also destroys its member actors | Integration test: create collection → add actor → delete collection → verify actor survives | §6.3 (C-018) |
| P0-7 | **Tombstone gate bypass** — A collection operation for a tombstoned GUID is applied instead of discarded | Unit test: tombstone GUID → send ADD → verify rejected | §7.5 |
| P0-8 | **Sequence tracker cross-contamination** — Collection sequence tracker interacts with or shares storage with any other lane's tracker | Code review — collection tracker must be standalone `FCollectionSequenceTracker` | §2.1 |

### 11.2 Non-Blocking (HIGH) Conditions

| # | Condition | Mitigation |
|---|-----------|------------|
| H-1 | Sequence tracker grows unbounded (>2048) | Enforce FIFO eviction. This is the same pattern as all prior lanes. |
| H-2 | Membership or identity map grows unbounded | Enforce 2048 cap for maps, 64 cap for per-actor sets. |
| H-3 | `AttachToActor` or `DetachFromActor` called for collection nesting | Explicit code review gate — collection nesting is metadata-only. |
| H-4 | Collection operation causes UE editor crash | Fuzz test: send malformed collection packets — verify graceful rejection. |
| H-5 | FNV protocol signature missing `0x0F` | FNV validation test — must include all 12 packet types. |

### 11.3 Rollback Procedure

If any P0 condition is detected:

1. **Immediately halt** all Phase 6F work
2. **Revert** all Phase 6F commits via `git revert`
3. **Verify** no residual Phase 6F symbols remain in source:
   ```
   grep -r "PT_Collection\|CollectionSequences\|FCollectionSequenceTracker\|FScopedCollectionSuppression\|_last_collection_state\|serialize_collection" UE_Plugin/ Blender_Addon/
   ```
4. **Re-run** all existing Phase 6 test suites:
   - `python3 tests/run_phase6e_all.py` — 308/308 + 102/102 audit
   - `python3 tests/run_phase6d_hierarchy.py` — 107/107
   - `python3 tests/run_phase6_visibility.py` — 15/15
   - `python3 tests/run_phase6_rename.py` — 13/13
5. **Re-verify** freeze checkpoint invariants (`37-phase6-invariant-checklist.md`)
6. **Document** rollback reason and corrective action in the freeze checkpoint revision history

### 11.4 No Partial Rollback

Phase 6F is a single semantic lane. Any P0 condition triggers full rollback
of the entire Phase 6F implementation. The additive-only pattern ensures
each changed file is fully isolated and revertible.

---

## 12. Done Criteria

Phase 6F is **done** when all of the following are verified:

### 12.1 Packet & Protocol

| # | Criterion | Verification |
|---|-----------|-------------|
| D-1 | `PT_Collection = 0x0F` defined and registered in protocol constants | Code review — `SyncTypes.h` |
| D-2 | `FCollectionSequenceTracker` implemented (bounded 2048, stale/duplicate rejection, dual-key) | Unit test |
| D-3 | All 8 operation types defined (`ADD`/`REMOVE`/`MOVE`/`CLEAR`/`RENAME_REF`/`COLLECTION_CREATE`/`COLLECTION_DELETE`/`COLLECTION_REPARENT`) | Code review |
| D-4 | FNV protocol signature includes `0x0F` | FNV validation test |
| D-5 | `kValidTypes[]` includes `PT_Collection` | Code review |

### 12.2 Blender-Side

| # | Criterion | Verification |
|---|-----------|-------------|
| D-6 | Blender-side collection identity detection (`bpy.data.collections` diff) emits `COLLECTION_CREATE`, `COLLECTION_DELETE`, `RENAME_REF`, `COLLECTION_REPARENT` | Integration test |
| D-7 | Blender-side membership detection (`obj.users_collection` diff via `_last_collection_state`) emits `ADD`, `REMOVE`, `MOVE`, `CLEAR` | Integration test |
| D-8 | Per-pair and per-collection monotonic sequence counters exist and reset on disconnect | Unit test |
| D-9 | Snapshot emits full collection state (all alive objects, all collections, all memberships) | Integration test |
| D-10 | Batch coalescing: net-noop pairs (ADD+REMOVE same pair, same tick) suppressed | Integration test |
| D-11 | Batch cap: max 512 operations per tick; overflow spills to next tick | Unit test |

### 12.3 UE-Side

| # | Criterion | Verification |
|---|-----------|-------------|
| D-12 | `HandleCollection()` dispatches by `OpType` to correct handler | Unit test |
| D-13 | `FScopedCollectionSuppression` RAII guard wraps all metadata mutation paths | Code review |
| D-14 | Collection membership stored in separate `TMap<FGuid, TSet<FGuid>>` — NOT in `FSyncTransformState` | Code review |
| D-15 | Collection identity stored in separate `TMap<FGuid, FCollectionMetadata>` — NOT in `FSyncTransformState` | Code review |
| D-16 | Per-actor membership set bounded at 64 with FIFO eviction | Unit test |
| D-17 | Collection identity map bounded at 2048 with FIFO eviction | Unit test |
| D-18 | Sequence tracker bounded at 2048 with FIFO eviction | Unit test |

### 12.4 Cross-Lane Safety

| # | Criterion | Verification |
|---|-----------|-------------|
| D-19 | Tombstone gating: ADD/REMOVE for tombstoned GUID silently discarded | Unit test |
| D-20 | ActorCache fallback: ADD/REMOVE for non-existent actor silently discarded | Unit test |
| D-21 | Collection membership does NOT prevent actor delete | Integration test |
| D-22 | Collection delete does NOT cascade to actor destruction | Integration test |
| D-23 | Collection nesting NEVER calls `AttachToActor`/`DetachFromActor` | Code review |
| D-24 | Collection operation does NOT modify transform, visibility, rename, or hierarchy state | Regression test |

### 12.5 Observability

| # | Criterion | Verification |
|---|-----------|-------------|
| D-25 | All 16 planned counters exist and are wired | Code review + `UE.LiveSync.Stats` |
| D-26 | 3 profiler scopes exist (`UELiveSync_HandleCollection`, `UELiveSync_ProcessCollectionPackets`, `UELiveSync_ApplyCollectionMembership`) | Code review |
| D-27 | All log prefixes use `[COLLECTION]` with operation-specific sub-prefix | Code review |
| D-28 | `UE.LiveSync.DumpState` includes collection tracker/membership/identity summary | Code review |

### 12.6 Stability

| # | Criterion | Verification |
|---|-----------|-------------|
| D-29 | No frozen-runtime modifications | Git diff review |
| D-30 | No Phase 5 or Phase 6A–6E regressions | Run all existing test suites |
| D-31 | No editor crashes during 10-minute mixed soak (transforms + rename + visibility + hierarchy + delete + collection + reconnect) | Soak test |
| D-32 | All standalone tests pass | Test runner |
| D-33 | All integration tests pass (requires UE Editor) | Test runner |

---

## 13. Complexity Assessment

| Dimension | Rating | Rationale |
|-----------|--------|-----------|
| **Packet complexity** | Medium | Discriminant-first fixed-size (46B) with one variable extension (RENAME). Two payload variants but uniform base size ensures safe parsing. The discriminant-first design (§10.10 correction) is the critical safety improvement over the initial scope lock sketch. |
| **Replay complexity** | Medium | Dual-key sequence tracking (per-pair for membership, per-GUID for identity) adds complexity over per-GUID-only tracking of prior lanes. However, the same `<=` stale rejection pattern applies. |
| **Reconnect complexity** | Low | Snapshot re-establishes all state. No tombstone interaction (membership is metadata, not structural). Deterministic convergence proved (§7.4). |
| **Lifecycle coupling** | LOW (designed) | Critical constraint: membership must NOT block delete. Three-barrier system (sequence → tombstone → ActorCache) prevents all lifecycle coupling violations. P0 rollback conditions enforce compliance. |
| **Hierarchy coupling** | NONE | Collection nesting is metadata-only. No attachment API calls. Explicit invariant (C-016) prohibits `AttachToActor`/`DetachFromActor`. |
| **Transform coupling** | NONE | Collection is metadata only. No transform pipeline interaction. |
| **Existing runtime coupling** | LOW | Fully additive — same pattern as all prior lanes. No frozen-zone modifications. All new: tracker, handler, maps, counters, profiler scopes. |
| **Multi-collection membership** | HIGH | The single hardest design problem. Blender's many-to-many membership model has no UE equivalent. This vertical slice stores membership as `TSet<FGuid>` on the UE side — not as World Outliner folder structure. The tradeoff is accepted: collection membership is metadata-only, viewable in diagnostics but not in the Outliner. |
| **Testing complexity** | Medium | 16 operation types × edge cases, cross-lane interaction (6 lanes), multi-collection boundary tests, reconnect convergence, discriminant-first parser safety. Lower than hierarchy or delete due to no irreversible state or graph consistency constraints. |

**Overall**: MEDIUM complexity. The packet/replay/runtime infrastructure
follows the same additive-only pattern as 6A–6E. The hard problems are
(1) multi-collection membership representation without UE Outliner coupling,
and (2) discriminant-first parser design to ensure safe dispatching between
payload variants. Neither is a runtime implementation challenge — both are
design problems resolved in this document.

---

## 14. Reference Documents

| Document | Relationship |
|----------|-------------|
| `38-phase6F-collection-scope-lock.md` | Scope lock — defines IN/OUT boundaries, semantic rules, initial sketch of wire format (superseded by §1.2–1.4 of this document) |
| `36-phase6-stabilization-freeze-checkpoint.md` | Freeze checkpoint — additive-only requirement, cross-lane coupling prohibition, §7.3 collection/lifecycle coupling invariant |
| `37-phase6-invariant-checklist.md` | Freeze checkpoint invariant checklist — all 66 invariants must remain verified after Phase 6F |
| `22-semantic-event-architecture-conventions.md` | Canonical conventions — all §2 mandatory requirements apply; discriminant-first packet design follows §9.4 parser invariants |
| `18-phase6-scope-lock.md` | Phase 6 master scope — §3.4 references collection sync as IN-SCOPE |
| `29-phase6E-lifecycle-scope-lock.md` | Lifecycle scope lock — §4.1 defers collection delete to Phase 6F |
| `30-phase6E-vertical-slice-lifecycle.md` | Lifecycle vertical slice — replay dependency model, tombstone semantics, reconnect proofs (patterns adopted for §2, §7 of this document) |
| `24-phase6D-hierarchy-scope-lock.md` | Hierarchy scope lock — collection/hierarchy separation rationale in §1.3 |
| `12-core-runtime-invariants.md` | Frozen runtime invariants |
| `16-known-safe-modification-zones.md` | SAFE/CAUTION/HIGH-RISK/FROZEN modification zones |

---

## 15. Changelog

| Date | Version | Changes |
|------|---------|---------|
| 2026-05-27 | 1.0 | Initial vertical slice design — Phase 6F collection/group replication. Defines packet definition (§1) with discriminant-first wire format, replay dependency model (§2) with dual-key sequence tracking, Blender emission model (§3) with `_last_collection_state`, UE processing model (§4) with parser isolation rules, cross-lane interaction matrix (§5) with 6-lane ALLOWED/IGNORED/REJECTED/DEFERRED classification, 27 invariants (§6), 5 replay safety proofs (§7), 16-counter observability design (§8), frozen-runtime guarantees (§9), 15 failure mode analyses (§10), 8 P0 rollback conditions (§11), and 33 done criteria (§12). Design-only — no implementation. |
