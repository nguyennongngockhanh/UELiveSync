# Phase 6F — Collection/Group Replication Scope Lock

> **Created**: 2026-05-27
> **Status**: IMPLEMENTED — TESTED
> **Predecessors**: Rename (STABILIZED, Phase 6A/6B · `0x0C`) · Visibility (STABILIZED, Phase 6C · `0x0B`) · Hierarchy (STABILIZED, Phase 6D · `0x0D`) · Lifecycle/Delete (STABILIZED, Phase 6E · `0x0E`)
> **Next**: — (complete)
> **Implementation**: IMPLEMENTED — parser, handler (HandleCollection), per-pair sequence tracking, replay recording, and ConsoleReset/StopNetworkThread lifecycle are all active.
> **Freeze**: Phase 6 Stabilization Freeze ACTIVE — must be additive-only, no frozen-runtime modifications, no cross-lane coupling (see `36-phase6-stabilization-freeze-checkpoint.md`)
>
> This document defines the **hard scope boundaries** for the fifth Phase 6
> semantic-event vertical slice: collection/group replication.
>
> Collection sync is architecturally distinct from all prior lanes in one
> critical way: **objects in Blender can belong to multiple collections**,
> but UE's World Outliner has no direct multi-group membership concept.
> Collections are a **reference-only metadata grouping layer** — they do
> NOT affect transform propagation, attachment hierarchy, visibility
> state, or object lifecycle.

---

## 1. Purpose

Collection/group replication is the fifth minimal editor-authority workflow.
It replicates **group membership intent** from Blender to the Unreal Editor:
when Blender adds, removes, renames, or re-parents a collection, the
corresponding UE actor's metadata is updated to reflect the grouping.

### 1.1 What "Collection" Means as a Semantic Mutation

A **collection** is a named, user-visible group of objects in Blender
(bpy.types.Collection). Key properties:

| Property | Value |
|----------|-------|
| **Nature** | Reference-only grouping layer |
| **Object membership** | Many-to-many: one object can belong to N collections, one collection contains M objects |
| **Nesting** | Collections can be parented (nested collections in the Outliner) |
| **Identity** | Collection objects have their own data identity in Blender (bpy.data.collections) |
| **Visibility** | Each collection has viewport/render visibility, independent of its members |
| **Lifecycle coupling** | Deleting a collection unlinks objects but does NOT delete the objects themselves |
| **UE equivalent** | No direct 1:1 equivalent. UE World Outliner folders are flat, single-membership, and non-nesting by default. |

### 1.2 Why Collection After Lifecycle/Delete

| Criterion | Rename | Visibility | Hierarchy | Lifecycle/Delete | **Collection** |
|-----------|--------|------------|-----------|------------------|----------------|
| **Mutation scope** | Single object | Single object | Object pair | Object + dependents | **Object + group(s)** |
| **Reversibility** | Reversible | Reversible | Reversible | Irreversible | **Reversible** |
| **Dependency sensitivity** | None | None | Required | Maximum — cascading | **Low** — metadata only |
| **Multi-object semantics** | No | No | No | Yes (detach cascade) | **Yes** — group add/remove |
| **Tombstone required** | No | No | No | Required | **Respects existing, no new** |
| **Graph invalidation** | None | None | None | Orphan cascade | **None** — metadata only |
| **Multi-collection membership** | N/A | N/A | N/A | N/A | **Fundamental challenge** |
| **Existing UE runtime** | None | None | Attach APIs | DestroyActor | **Actor metadata tags** |

### 1.3 Collection ≠ Hierarchy

| Misconception | Correction |
|--------------|------------|
| "Collections are like hierarchy parenting" | Collection membership is **reference-only grouping**, NOT parent-child attachment. Collection groups have no transform propagation, no interpolation coupling, no attachment graph interaction. |
| "Moving an object in the Collection Outliner re-parents it" | In Blender, the Outliner's drag-and-drop on collection items changes **collection membership**, not scene-graph parenting. These are distinct systems. |
| "UE folder structure = collection nesting" | UE World Outliner folders are flat, non-nesting, single-membership by default. Blender collections are nested, multi-membership. Direct folder mapping is NOT supported. |
| "Collection visibility = actor visibility" | Collection-level viewport hide is distinct from per-object `hide_get()`. Collection visibility affects all members and is not replicated to UE. |

### 1.4 Collection ≠ Lifecycle/Delete

| Misconception | Correction |
|--------------|------------|
| "Deleting a collection deletes its objects" | In Blender, deleting a collection unlinks objects but does NOT destroy them. Objects survive as ungrouped top-level entities. |
| "Deleting an object removes it from all collections" | In Blender, deleting an object implicitly removes it from all collections. This is a lifecycle consequence, not a collection mutation. |
| "Collection membership blocks delete" | Metadata coupling is forbidden. Collection state must not create dependencies that prevent deletion. |

---

## 2. IN SCOPE

### 2.1 Packet Boundary Definition (PT_Collection = 0x0F — Reserved ONLY)

| Field | Value |
|-------|-------|
| **Packet type** | `PT_Collection = 0x0F` (next available after `0x0E`, see §8.2 of semantic conventions) |
| **Direction** | Blender → UE only (Phase 6F); UE → Blender deferred (see §3) |
| **Status** | **IMPLEMENTED — TESTED.** PT_Collection (0x0F) is parsed in ProcessBinaryPacket, reaches HandleCollection(), updates per-pair sequence tracking, records world replay, and is reset on StopNetworkThread/ConsoleReset. Wire format: membership ops 46 bytes (TargetGuid(16)+OpType(1)+OpFlags(1)+seq(4)+ts(8)+CollectionGuid(16)), identity ops 30 bytes (without CollectionGuid). |
| **Semantics** | Replicates **collection group membership** as metadata: object X is a member of collection Y. Collection structure changes (create/delete/rename/re-parent) are also replicated. |
| **Wire format** | **NOT DEFINED.** The payload layout is to be determined during vertical slice design. Candidate fields: `GUID(16) + CollectionGuid(16) + Operation(1) + seq(4) + ts(8)`. |
| **Event type** | Discrete semantic mutation — NOT a state stream. Fires on collection membership change, not on every transform tick. |
| **Scope** | MESH objects only (existing object filter). Collections containing only non-MESH objects are not replicated. |

### 2.2 Allowed Operations

| Operation | Description | Replicated? |
|-----------|-------------|-------------|
| **Group add** | Object added to a collection → UE actor tagged with collection GUID | ✅ IN SCOPE |
| **Group remove** | Object removed from a collection → collection tag removed from UE actor | ✅ IN SCOPE |
| **Collection create** | New collection created in Blender → new collection identity registered on UE side | ✅ IN SCOPE |
| **Collection delete (empty)** | Collection deleted in Blender → collection identity removed from all tagged UE actors | ✅ IN SCOPE |
| **Collection rename** | Collection renamed in Blender → collection identity label updated on UE side | ✅ IN SCOPE |
| **Collection re-parent** | Collection moved under a different parent collection in Blender → nesting metadata updated on UE side | ✅ IN SCOPE |
| **Bulk add** | Multiple objects added to a collection in a single operation → batched | ✅ IN SCOPE |
| **Bulk remove** | Multiple objects removed from a collection in a single operation → batched | ✅ IN SCOPE |
| **Collection visibility toggle** | Collection-level viewport hide → NOT replicated (per-object visibility is Phase 6C) | ❌ OUT OF SCOPE |
| **Collection color tag** | Blender collection color tag → NOT replicated | ❌ OUT OF SCOPE |

### 2.3 Blender-Side Detection (Planned)

| Item | Description |
|------|-------------|
| Change detection | Periodically diff `bpy.data.collections` to detect new/deleted/renamed/re-parented collections. Diff collection membership per-object via `obj.users_collection`. |
| Detection granularity | Frame-level: diff collections once per sync tick. Membership changes per-object detected via `_last_collections` map. |
| Scope | MESH objects only. Collections containing only non-MESH objects are not tracked. |
| First-sync behavior | Current collection membership is emitted during snapshot — snapshot enumerates each object's collection memberships. |
| Batch coalescing | Multiple collection changes in a single frame should be batched into one `send_objects` call. Membership changes for the same collection are coalesced. |
| Sequence tracking | Per-collection monotonic sequence counter (`_collection_sequences`), cleared on disconnect. Per-operation sequence for membership changes. |
| Collection identity | Collections are identified by their own GUID (`collection["ue_guid"]`), NOT by name. Names can change. |

### 2.4 UE-Side Representation (Planned)

| Item | Description |
|------|-------------|
| Representation | Collection membership stored as metadata on the actor — **NOT** as UE World Outliner folder structure. This is the critical architectural choice. |
| Storage | `TMap<FGuid, TSet<FGuid>>` — mapping per-actor GUID to set of collection GUIDs it belongs to. Separate `TMap<FGuid, FCollectionMetadata>` for collection identity data (name, parent). |
| Handler | `HandleCollection(FGuid TargetGuid, FGuid CollectionGuid, ECollectionOp Op, uint32 Seq, double Timestamp)` |
| Provenance | `EChangeOrigin::RemoteReplicated` (normal) / `EChangeOrigin::Replay` (snapshot replay) |
| Sequence check | Stale/duplicate rejection via `FCollectionSequenceTracker` (bounded 2048) |
| Tombstone check | If `TargetGuid` is tombstoned → silently discard. Collection operations never resurrect deleted objects. |
| Suppression | Pattern-conformance `FScopedCollectionSuppression` RAII guard. No known callback risk from collection metadata updates — required by semantic-event conventions (§2.6, §6.5). |
| Log prefix | `[COLLECTION]` |
| Profiler scopes | `UELiveSync_HandleCollection`, `UELiveSync_ProcessCollectionPackets` |

### 2.5 Collection Identity

| Aspect | Decision | Rationale |
|--------|----------|-----------|
| Collection identified by GUID | Yes — `collection["ue_guid"]` | Same as all object identity. Collection renames must not break membership tracking. |
| GUID generation | `uuid.uuid4().hex` in Blender on collection creation | Same pattern as object GUIDs. |
| GUID collision detection | Existing `ensure_unique_guid()` extended for collections | Collections are separate from objects — no collision with existing object GUIDs. |
| Collection name | Stored as metadata on UE side | Collection name is display-only. GUID is the identity key. |

### 2.6 Packet Number Reservation

| Constant | Value | Status |
|----------|-------|--------|
| `PT_Collection = 0x0F` | 0x0F | **Reserved — NOT implemented.** |

This follows the sequential allocation pattern established by all prior lanes.
`0x0F` is the next available packet type after `PT_Delete_V5 = 0x0E`.

---

## 3. OUT OF SCOPE

### 3.1 UE World Outliner Folder Mapping

| Item | Rationale |
|------|-----------|
| Mapping Blender collections to UE folders | UE World Outliner folders are conceptually different from Blender collections: folders are flat, single-membership, non-nesting by default. Forcing Blender's multi-membership nested collections into UE folders would create a lossy, confusing mapping with no user recourse. **Collection membership is metadata, not folder structure.** |
| UE folder create/delete/reparent | Not triggered by Blender collection changes. If a future phase adds UE folder sync, it would be a separate lane with its own semantic rules. |
| UE Outliner drag-to-reparent | Dragging actors into folders in the UE World Outliner is a separate system. Not replicated to Blender collections. |

### 3.2 Collection Visibility

| Item | Rationale |
|------|-----------|
| Blender collection viewport hide | Collection-level visibility is a Blender UI concept with no UE equivalent. Per-object visibility is already handled by Phase 6C. Collection-level hide affects all members — replicating it would require sending `0x0B` packets for every member, which is both redundant and semantically wrong. |
| Blender collection render visibility | Render visibility is a Blender-specific concept. Not applicable to UE Editor. |
| UE folder eye icon / visibility toggle | UE Outliner folder visibility is a UI display concept. Not replicated to Blender. |

### 3.3 Lifecycle/Delete Coupling

| Item | Rationale |
|------|-----------|
| Deleting a collection deletes its members | Explicitly forbidden. Collection delete = unlink objects, not destroy them. This is the most critical constraint from the freeze checkpoint (`36-phase6-stabilization-freeze-checkpoint.md §7.3`). |
| Deleting an actor removes it from a collection on UE side | Collection membership is metadata. Actor delete must NOT implicitly mutate collection state. On reconnect, the snapshot re-establishes correct membership. |
| Collection membership prevents actor delete | Metadata must not create lifecycle dependencies. Tombstone gating exists, but collection state must not introduce new blocking conditions. |

### 3.4 Hierarchy Reuse

| Item | Rationale |
|------|-----------|
| Reusing hierarchy attachment graph for collection nesting | Collection nesting (parent-collection containing child-collection) is conceptually distinct from actor attachment. Hierarchy lane owns actor attachment. Collection lane owns grouping metadata. |
| Reusing `ResolvePendingAttachments` for collection dependencies | Collection dependencies (parent collection must exist before child collection) are metadata-only. The hierarchy lane's deferred resolution infrastructure is specific to actor attachment — not reusable for collections. |
| Collection nesting affecting transform interpolation | Transform propagation follows the actor attachment graph, NOT the collection nesting tree. Collections are metadata — no transform coupling. |

### 3.5 Transform Ownership

| Item | Rationale |
|------|-----------|
| Collection-level transforms | Collections do not have transforms in Blender. No transform data to replicate. |
| Transform propagation via collections | Collection membership does not affect world-space transforms. Only the attachment hierarchy (Phase 6D) does. |

### 3.6 Authority Model Changes

| Item | Rationale |
|------|-----------|
| UE → Blender collection sync | Would require bidirectional authority infrastructure (Blender TCP listener, origin propagation for `EChangeOrigin::LocalUser`). Deferred until Phase 9 or later. |
| Conflict resolution for collection membership | Last-writer-wins with timestamp comparison. No merge algorithms, no conflict UI. Same model as Phase 6A–6E. |
| Multi-user collection editing | Not supported. Exactly one Blender ↔ one UE Editor. |

### 3.7 Collection-Level Operations (Deferred)

| Item | Rationale |
|------|-----------|
| Collection color tags | Blender UI feature. No UE equivalent. |
| Collection instance/override | Blender collection instances are advanced features. Not in scope for minimal slice. |
| View Layer collection settings | View Layer-specific collection properties (holdout, indirect only, etc.) are Blender rendering features. Not applicable to UE Editor. |
| Collection property sets (Blender 4.0+) | Overrides, asset collections — out of scope for initial collection sync. |

---

## 4. Semantic Rules

### 4.1 Collection ≠ Folder System

Blender collections and UE World Outliner folders are **fundamentally different**
concepts. Phase 6F must NOT map one to the other:

| Property | Blender Collection | UE World Outliner Folder |
|----------|-------------------|-------------------------|
| Membership | Many-to-many | Single-membership (one folder per actor) |
| Nesting | Recursive (parent → child collections) | Flat (single level by default) |
| Identity | Data-block with GUID | UI path string |
| Visibility | Per-collection viewport hide | Eye icon toggle (per-folder, display only) |
| Lifecycle | Delete = unlink members, NOT destroy | Delete folder = delete contents |

**Phase 6F stores collection membership as metadata on the UE actor** —
not as folder assignment. The UE actor carries a `TSet<FGuid>` of collection
GUIDs it belongs to. The UE side can optionally render this metadata in a
diagnostics panel, but the World Outliner folder hierarchy is untouched.

### 4.2 Collection ≠ Hierarchy

| Constraint | Rationale |
|------------|-----------|
| Collection parent-child is metadata | Collection nesting (e.g., "Characters" → "Heroes") is replicated as metadata on the collection identity, NOT as actor attachment. |
| No transform propagation | Collection membership has zero effect on actor world-space transforms. |
| No interpolation coupling | Collection changes never trigger or affect transform interpolation. |
| No scene graph mutation | Collection operations never call `AttachToActor` or `DetachFromActor`. |
| Separate deferred resolution (if needed) | If collection parent must exist before child collection, a separate bounded deferred metadata queue may be needed — NOT reusing `ResolvePendingAttachments` or `PendingHierarchyAttachments`. |

### 4.3 Collection Is Reference-Only Grouping Layer

| Rule | Rationale |
|------|-----------|
| Collection membership is metadata | Stored as `TMap<FGuid, TSet<FGuid>>` on UE side. Not part of `FSyncTransformState`. |
| Collection identity is separate | `TMap<FGuid, FCollectionMetadata>` for collection name, parent GUID, etc. Separate from actor state. |
| Collection operations are pure metadata mutations | No side effects on transforms, visibility, hierarchy, or actor lifecycle. |
| Collection data is bounded | Each actor's membership set is bounded (max 64 collections per actor, enforced). Total collection count bounded at 2048. |

### 4.4 Must Be Replay-Safe

| Requirement | Mechanism |
|-------------|-----------|
| Per-GUID monotonic sequence tracking | `FCollectionSequenceTracker` — bounded 2048, stale/duplicate rejection via `<=` |
| Snapshot replay | Collection membership during snapshot rebuild is tagged `EChangeOrigin::Replay`. Same stale rejection logic applies. |
| Reconnect | Tracker cleared on `StopNetworkThread()`, `ConsoleReset()`, Blender disconnect. Snapshot re-establishes correct membership state. |
| Duplicate rejection | Same `<=` pattern: duplicate sequence for same (actor GUID, collection GUID) pair → rejected. |

### 4.5 Must Be Stale-Safe

| Scenario | Behavior |
|----------|----------|
| Stale collection add for deleted object | Blocked by tombstone gate — silently discarded |
| Stale collection remove for already-removed object | Accepted if sequence > last tracked (idempotent metadata mutation) |
| Stale collection create for existing collection | Rejected by sequence tracker |
| Stale collection rename for deleted collection | Blocked by collection-level tombstone or tracking cleanup |
| Stale membership update for non-existent collection | Discarded — collection identity not found |

### 4.6 Must Be Suppression-Safe

| Requirement | Mechanism |
|-------------|-----------|
| RAII suppression guard | `FScopedCollectionSuppression` wrapping collection metadata mutation |
| No callback recursion risk | Pattern-conformance — no known callback fires from metadata tag writes |
| Scope lifetime | Active only within `HandleCollection()` call stack |
| Verbose logging | `[COLLECTION] Enter/Exit suppression scope (ActorGUID=%s, CollectionGUID=%s)` |

---

## 5. Cross-Lane Interaction Matrix

### 5.1 Rename ↔ Collection

| Interaction | Expected Behavior | Validation |
|-------------|------------------|------------|
| Rename of a collection-member object | Name changes, collection membership unchanged | ✅ No conflict — independent state |
| Rename of a collection | Collection label changes on UE side, membership unaffected | ✅ No conflict — collection metadata |
| Collection add creates no rename cycle | Rename suppression is per-lane, no cross-lane suppression coupling | ✅ Pattern enforced |

**Verdict**: NO CONFLICT — rename operates on actor label; collection
operates on grouping metadata. Independent state, independent trackers,
independent suppression.

### 5.2 Visibility ↔ Collection

| Interaction | Expected Behavior | Validation |
|-------------|------------------|------------|
| Visibility toggle of collection-member object | Visibility changes, collection membership unchanged | ✅ No conflict — independent state |
| Collection add of hidden object | Membership added, hidden state unchanged | ✅ No conflict — metadata only |
| Collection visibility does not toggle member visibility | Collection-level visibility is OUT OF SCOPE | ✅ Frozen runtime untouched |

**Verdict**: NO CONFLICT — visibility operates on `bHidden` flag;
collection operates on grouping metadata. Independent state.

### 5.3 Hierarchy ↔ Collection

| Interaction | Expected Behavior | Validation |
|-------------|------------------|------------|
| Reparent of a collection-member object | Attachment changes, collection membership unchanged | ✅ No conflict — independent subsystems |
| Collection add of an attached child | Membership added, attachment unchanged | ✅ No conflict — metadata vs attachment graph |
| Collection nesting (parent/child collections) | Does NOT use hierarchy attachment APIs | ✅ Explicitly forbidden — separate metadata system |
| Collection operation for a GUID with pending hierarchy resolution | Both processed independently in Tick | ✅ Additive-only — no shared state |

**Verdict**: NO CONFLICT — hierarchy operates on actor attachment graph;
collection operates on grouping metadata. No shared parser branches,
no shared trackers, no shared state.

**Critical constraint**: Collection nesting must NEVER call
`AttachToActor`/`DetachFromActor`. Collection parent-child relationships
are metadata-only.

### 5.4 Lifecycle/Delete ↔ Collection

| Interaction | Expected Behavior | Validation |
|-------------|------------------|------------|
| Delete of a collection-member object | Actor destroyed. Collection membership metadata is **not mutated** on UE side — the actor is gone and membership is irrelevant. On reconnect, snapshot re-establishes correct membership for surviving actors. | ✅ Metadata is not structural — no implicit cleanup required |
| Delete of a collection | Collection identity removed. Member objects survive — no delete cascade. | ✅ Explicitly required — see §3.3 |
| Tombstone gating for collection operations | If `TargetGuid` is tombstoned → collection operation silently discarded. Collection never resurrects deleted GUIDs. | ✅ Three-barrier protection: sequence → tombstone → ActorCache |
| Collection add of a deleted object (after tombstone) | Blocked by tombstone gate | ✅ Tombstone gate applies uniformly to all semantic lanes |
| Collection membership prevents delete | **Forbidden.** Collection metadata must not create lifecycle dependencies. | ✅ Freeze checkpoint §7.3 invariant — P0 rollback condition |
| Collection operation during snapshot rebuild (delete deferred) | Collection packet processed normally. If target is deleted in the same tick, tombstone check applies. | ✅ Additive-only — no shared deferred queue |

**Verdict**: NO CONFLICT — delete lane provides tombstone gating that
uniformly blocks collection operations on deleted GUIDs. No cross-lane
sequence coupling. No implicit membership cleanup required (metadata is
not structural; reconnect snapshot re-establishes correctness). The
single critical constraint is §5.4 row 5: collection membership must
NOT prevent delete.

### 5.5 Transform Pipeline ↔ Collection

| Interaction | Expected Behavior | Validation |
|-------------|------------------|------------|
| Transform packet for collection-member actor | Transforms unaffected by collection membership | ✅ No coupling — metadata only |
| Collection operation during `InterpolateTransforms` | Collection processing happens in `ProcessQueuedPackets`, BEFORE interpolation. No ordering conflict. | ✅ Tick ordering preserved |
| Collection operation affecting transform state | NOT POSSIBLE — collection is metadata, not transform | ✅ Invariant enforced |

**Verdict**: NO CONFLICT — collection is metadata only. No transform
pipeline interaction.

### 5.6 All Lanes Simultaneous

| Property | Expected Behavior |
|----------|-------------------|
| 6 lanes running concurrently | ✅ Transforms + rename + visibility + hierarchy + delete + collection — all additive, no shared state |
| All operations on same GUID in same tick | Each lane processes independently. Tombstone gates collection ops on deleted GUIDs. Sequence trackers are per-lane. |
| All operations on different GUIDs in same tick | Fully independent. No shared maps, no shared mutexes. |
| All operations during snapshot rebuild | Each lane tags with `EChangeOrigin::Replay`. Separate trackers prevent cross-lane contamination. |

**Verdict**: NO CONFLICT predicted, subject to verification during
implementation. All lanes follow the same additive-only, per-lane-tracker,
isolated-parser-branch pattern.

---

## 6. Replay Semantics

### 6.1 Sequence Tracking Rules

| Rule | Value |
|------|-------|
| Tracker type | `FCollectionSequenceTracker` |
| Key | `TPair<FGuid, FGuid>` — (actor GUID, collection GUID) pair for membership operations. Collection GUID alone for collection-identity operations (create, delete, rename, reparent). |
| Bounding | 2048 entries, FIFO eviction |
| Acceptance | `IncomingSeq > LastSeq` for the key |
| Rejection | `IncomingSeq <= LastSeq` — stale or duplicate |

### 6.2 Stale Rejection Rules

| Condition | Action |
|-----------|--------|
| Stale membership add | Reject with `[COLLECTION] Rejected — stale/duplicate sequence (ActorGUID=%s, CollectionGUID=%s)`, increment `CollectionStaleRejections` |
| Stale membership remove | Reject with same pattern |
| Stale collection create | Reject — collection GUID already tracked with higher sequence |
| Stale collection rename | Reject — collection identity tracker rejects |
| Stale collection delete | Reject — collection already deleted or tracked with higher sequence |

### 6.3 Reconnect Behavior

| Event | Tracker action | Metadata action |
|-------|---------------|-----------------|
| StopNetworkThread | `CollectionSequences.Empty()` | All collection metadata cleared |
| ConsoleReset | `CollectionSequences.Empty()` | All collection metadata cleared; counters `.store(0)` |
| Blender disconnect | `_collection_sequences.clear()` | Blender-side sequence counters reset |
| Reconnect snapshot | Collection membership is re-established via snapshot enumeration | Correct membership restored — no explicit tombstone interaction needed |

### 6.4 Must NOT Resurrect Deleted Objects

| Requirement | Mechanism |
|-------------|-----------|
| Collection operation for tombstoned GUID | Silently discarded — tombstone check before any collection metadata mutation |
| Collection operation for non-existent actor | Silently discarded — ActorCache lookup fails |
| Collection membership during snapshot replay | Snapshot only includes currently alive objects. Deleted objects are absent from snapshot. No resurrection path. |

---

## 7. Observability Contract

### 7.1 Planned Counters

All counters are `std::atomic<int32>` with `std::memory_order_relaxed`.
O(1) update, no allocation. Display values only.

| Counter | Description |
|---------|-------------|
| `CollectionPackets` | Total collection packets received |
| `CollectionProcessed` | Collection membership operations applied |
| `CollectionStaleRejections` | Stale/duplicate collection packets rejected |
| `CollectionReplayApplied` | Collection operations applied during snapshot replay |
| `CollectionReplaySkipped` | Collection operations skipped during snapshot replay |
| `CollectionTombstoneRejections` | Collection operations blocked by tombstone |
| `CollectionIdentityCreated` | New collection identities registered |
| `CollectionIdentityDeleted` | Collection identities removed |

### 7.2 Planned Profiler Scopes

| Scope | Description |
|-------|-------------|
| `UELiveSync_HandleCollection` | Single collection operation handler |
| `UELiveSync_ProcessCollectionPackets` | Batch collection packet processing |

### 7.3 Log Prefix Standardization

| Context | Prefix |
|---------|--------|
| General | `[COLLECTION]` |
| Application | `[COLLECTION] Applying: ActorGUID=%s, CollectionGUID=%s, Op=%s, Origin=%s` |
| Suppression enter | `[COLLECTION] Enter suppression scope (ActorGUID=%s, CollectionGUID=%s)` (Verbose) |
| Suppression exit | `[COLLECTION] Exit suppression scope (ActorGUID=%s, CollectionGUID=%s)` (Verbose) |
| Stale rejection | `[COLLECTION] Rejected — stale/duplicate sequence (ActorGUID=%s, CollectionGUID=%s, IncomingSeq=%u, LastSeq=%u)` (Warning) |
| Tombstone rejection | `[COLLECTION] Rejected — target actor tombstoned (ActorGUID=%s)` (Warning) |
| Collection identity create | `[COLLECTION] New collection identity registered: CollectionGUID=%s, Name=%s` |
| Collection identity delete | `[COLLECTION] Collection identity removed: CollectionGUID=%s` |
| Tracker clear | `[COLLECTION] Tracker cleared (StopNetworkThread/ConsoleReset)` |

### 7.4 UE Console Commands

| Command | Extension |
|---------|-----------|
| `UE.LiveSync.DumpState` | Include collection tracker size and membership map summary |
| `UE.LiveSync.Stats` | Include all 8 collection counters |

---

## 8. Frozen-Runtime Guarantees

### 8.1 Systems That Must NOT Be Touched

| System | Files | Risk if Modified |
|--------|-------|-----------------|
| `LiveSyncQueue` | FROZEN | Network thread enqueues `FLiveSyncPacket` with `PT_Collection` payload — uses existing queue path only. No queue modification. |
| `PendingAssetQueue` | FROZEN | Collection processing is entirely separate from asset resolution. |
| `LiveSyncRunnable` | FROZEN | No changes to thread lifecycle, shutdown order, or receive loop. |
| `FSyncTransformState` | FROZEN | Collection membership stored in separate `TMap<FGuid, TSet<FGuid>>` — NOT in `FSyncTransformState`. |
| 24-byte header layout | FROZEN | `PT_Collection` uses the same 24-byte header as all packet types. No header changes. |
| Tick pipeline ordering | FROZEN | Collection processing in `ProcessQueuedPackets` (existing slot) — no reordering of existing stages. |
| Transform interpolation | FROZEN | Collection is metadata — zero interpolation coupling. |
| Phase 5 parser dispatch (version, magic, header) | FROZEN | New `case PT_Collection` branch only — no modification to existing branches, version dispatch, or header parsing. |
| `AttachToActor` / `DetachFromActor` | FROZEN | Collection nesting is metadata-only — no attachment API calls. |
| `ResolvePendingAttachments` | FROZEN | No changes to existing resolution logic. Collection dependencies get separate processing if needed. |
| `RecoverMissingActors` | FROZEN | No changes. Collection metadata is not actor lifecycle. |

### 8.2 Forbidden Modifications for Phase 6F

| Action | Why |
|--------|-----|
| Add fields to `FSyncTransformState` | Object layout FROZEN — use separate `TMap<FGuid, TSet<FGuid>>` |
| Modify existing case branches in `ProcessBinaryPacket` | Parser FROZEN — add new `case PT_Collection` only |
| Reorder Tick pipeline stages | Pipeline FROZEN — collection processed in `ProcessQueuedPackets` |
| Modify `FLiveSyncQueue` capacity or ownership | Queue FROZEN — use existing enqueue paths |
| Modify `StopNetworkThread` shutdown sequence | Thread lifecycle FROZEN |
| Add cross-thread state for collection data | Thread safety FROZEN — game-thread only |
| Call `AttachToActor`/`DetachFromActor` for collection nesting | Hierarchy APIs FROZEN — collection is metadata only |
| Remove or skip existing Tick stages | Pipeline integrity FROZEN |
| Share sequence tracker or counters with any other lane | Cross-lane coupling FORBIDDEN by freeze rules |
| Create lifecycle dependency via collection membership | Freeze checkpoint §7.3 INVARIANT — P0 rollback condition |

### 8.3 Ownership Invariants

| Component | Owner | Access rules |
|-----------|-------|-------------|
| `FLiveSyncQueue` (128 MPSC) | Game thread (dequeue), Network thread (enqueue) | Enqueue only on network thread; dequeue only on game thread |
| `FCollectionSequenceTracker` | Game thread only | All sequence tracker access via `HandleCollection` (CHECK_GAME_THREAD) |
| Collection membership maps | Game thread only | Separate from actor cache — game-thread only |
| Collection identity maps | Game thread only | Separate from actor cache — game-thread only |
| Blender send queue | Main thread (enqueue), Daemon thread (dequeue) | Mutex-guarded; main thread must not block |

---

## 9. Rollback Rules (P0 Conditions)

Phase 6F implementation must be rolled back immediately if ANY of the
following conditions are detected:

### 9.1 Freeze Break Conditions

| # | Condition | Detection Method | Severity |
|---|-----------|-----------------|----------|
| FBR-1 | Any modification to existing semantic lane code (6A–6E) | Git diff review — all changes must be in new files or additive-only insertions in CAUTION/SAFE zones | **P0 — CRITICAL** |
| FBR-2 | Any modification to frozen runtime systems (Tick, queue, thread, transform state, header, heartbeat) | Git diff review — zero changes to frozen files | **P0 — CRITICAL** |
| FBR-3 | Cross-lane sequence tracker sharing or coupling | Code review — collection tracker must be a new standalone type | **P0 — CRITICAL** |
| FBR-4 | FNV signature mismatch | FNV validation test fails | **P0 — CRITICAL** |

### 9.2 Invariant Break Conditions

| # | Condition | Detection Method | Severity |
|---|-----------|-----------------|----------|
| IBR-1 | Collection membership blocks or prevents actor delete | Integration test: create actor → add to collection → delete actor → verify actor destroyed and collection membership absent | **P0 — CRITICAL** |
| IBR-2 | Delete of collection cascades to object destruction | Integration test: create collection → add actor → delete collection → verify actor survives | **P0 — CRITICAL** |
| IBR-3 | Tombstone gating bypassed by collection operations | Unit test: tombstone GUID → send collection add → verify rejected | **P0 — CRITICAL** |
| IBR-4 | Snapshot replay resurrects deleted objects via collection membership | Reconnect test: delete actor → reconnect → verify actor stays dead (collection membership absent from snapshot) | **P0 — CRITICAL** |
| IBR-5 | Collection operations modify transform, visibility, or hierarchy state | Regression test: apply collection op → verify transform/visibility/hierarchy counters unchanged | **HIGH** |

### 9.3 Safety Break Conditions

| # | Condition | Detection Method | Severity |
|---|-----------|-----------------|----------|
| SBR-1 | Collection tracker grows without bound | Memory test: verify bounded at 2048 with FIFO eviction | **HIGH** |
| SBR-2 | Collection membership map grows without bound | Memory test: verify bounded at 2048 collections, 64 memberships per actor | **HIGH** |
| SBR-3 | `AttachToActor`/`DetachFromActor` called for collection nesting | Code review — hierarchy APIs are FROZEN for collection operations | **P0 — CRITICAL** |
| SBR-4 | Collection operation causes UE editor crash | Fuzz test: send malformed collection packets | **P0 — CRITICAL** |

### 9.4 Rollback Procedure

If any P0 condition is detected:

1. **Immediately halt** all Phase 6F work
2. **Revert** all Phase 6F commits via `git revert`
3. **Verify** that no residual Phase 6F symbols remain in source (grep for `PT_Collection`, `CollectionSequences`, `FCollectionSequenceTracker`, `FScopedCollectionSuppression`)
4. **Re-run** all existing Phase 6 test suites to confirm no regressions:
   - `python3 tests/run_phase6e_all.py` — 308/308 + 102/102 audit
   - `python3 tests/run_phase6d_hierarchy.py` — 107/107
   - `python3 tests/run_phase6_visibility.py` — 15/15
   - `python3 tests/run_phase6_rename.py` — 13/13
5. **Re-verify** freeze checkpoint invariants (`37-phase6-invariant-checklist.md`)
6. **Document** the rollback reason and corrective action in the freeze checkpoint revision history

### 9.5 No Partial Phase 6F Rollback

Phase 6F is a single semantic lane. If any condition triggers rollback,
the entire Phase 6F implementation must be rolled back. No "partial keep"
of collection infrastructure — the additive-only pattern means each change
is isolated and fully revertible.

---

## 10. Implementation Prerequisites

Before Phase 6F can begin implementation, the following must be completed
**in design/planning only** (no code changes):

| # | Prerequisite | Deliverable | Section |
|---|-------------|-------------|---------|
| 1 | Collection authority model design | Vertical slice design doc defining exactly how Blender collections map to UE-side representation | TBD |
| 2 | Multi-collection membership strategy | Design doc explaining how Blender's many-to-many membership is represented on UE side without loss | TBD |
| 3 | Collection identity system | Design for `collection["ue_guid"]` generation, collision detection, and lifecycle | TBD |
| 4 | Cross-lane interaction test plan | Test scenarios covering all 12 interaction pairs in §5 | TBD |
| 5 | Frozen-runtime audit | Formal audit showing zero modifications to all frozen systems | §8 |
| 6 | Replay determinism proof | Formal analysis showing collection replay safety | §6 |
| 7 | Blender detection and serialization plan | Design for `_last_collections` diff and `serialize_collection()` | TBD |

---

## 11. Done Criteria

Phase 6F is **done** when:

1. `PT_Collection = 0x0F` packet type constant is defined and registered in protocol constants
2. `FCollectionSequenceTracker` is implemented (bounded 2048, stale/duplicate rejection)
3. Blender-side collection detection emits `PT_Collection` packets (create/delete/rename/re-parent/add/remove)
4. UE-side `HandleCollection()` applies collection metadata mutations
5. `FScopedCollectionSuppression` RAII guard wraps collection mutation path
6. Collection membership stored in separate `TMap<FGuid, TSet<FGuid>>` — NOT in `FSyncTransformState`
7. Collection identity stored in separate `TMap<FGuid, FCollectionMetadata>` — NOT in `FSyncTransformState`
8. All 8 observability counters exist and are wired
9. 2 profiler scopes exist and are wired
10. FNV protocol signature includes `0x0F`
11. `kValidTypes[]` includes `PT_Collection`
12. Tombstone gating is verified: collection operations on deleted GUIDs are silently discarded
13. No frozen-runtime modifications
14. No Phase 5 or Phase 6A–6E regressions
15. No editor crashes during 10-minute mixed soak (transforms + rename + visibility + hierarchy + delete + collection + reconnect)
16. All standalone tests pass
17. All integration tests pass (requires UE Editor)

---

## 12. Complexity Classification

| Dimension | Rating | Rationale |
|-----------|--------|-----------|
| **Packet complexity** | Medium | Wire format TBD — must encode operation type (add/remove/create/delete/rename/reparent), collection GUID, optional actor GUID |
| **Replay complexity** | Medium | Per-pair sequence tracking (actor GUID, collection GUID) adds complexity over per-GUID tracking |
| **Reconnect complexity** | Low | Snapshot re-establishes membership — no tombstone interaction |
| **Lifecycle coupling** | LOW (designed) | Critical constraint: membership must NOT block delete. Explicitly enforced. |
| **Hierarchy coupling** | NONE | Collection nesting is metadata-only. No attachment API calls. |
| **Transform coupling** | NONE | Collection is metadata only. No transform pipeline interaction. |
| **Existing runtime coupling** | LOW | Fully additive — same pattern as all prior lanes. No frozen-zone modifications required. |
| **Multi-collection membership** | HIGH | Blender's many-to-many membership model has no UE equivalent. The representation strategy is the single hardest design problem. |
| **Testing complexity** | Medium | Membership boundary tests, cross-lane interaction, multi-collection edge cases. Lower than hierarchy or delete due to no irreversible state or graph consistency constraints. |

**Overall**: MEDIUM-HIGH complexity. The packet/replay/runtime infrastructure
is straightforward (same pattern as 6A–6E). The hard problem is the
**multi-collection membership representation** on the UE side — this is a
design challenge, not an implementation challenge. All runtime code follows
the same additive-only, per-lane-tracker, isolated-parser-branch pattern.

---

## 13. Reference Documents

| Document | Relationship |
|----------|-------------|
| `36-phase6-stabilization-freeze-checkpoint.md` | Freeze checkpoint — defines additive-only requirement, cross-lane coupling prohibition, tombstone gating requirement, and §7.3 collection/lifecycle coupling invariant |
| `37-phase6-invariant-checklist.md` | Freeze checkpoint invariant checklist — all 66 invariants must remain verified after Phase 6F |
| `22-semantic-event-architecture-conventions.md` | Canonical conventions — all §2 mandatory requirements apply to collection lane |
| `18-phase6-scope-lock.md` | Phase 6 master scope — §3.4 references collection sync as IN-SCOPE |
| `29-phase6E-lifecycle-scope-lock.md` | Lifecycle scope lock — §4.1 explicitly defers collection delete to Phase 6F |
| `12-core-runtime-invariants.md` | Frozen runtime invariants |
| `16-known-safe-modification-zones.md` | SAFE/CAUTION/HIGH-RISK/FROZEN modification zones |

---

## 14. Changelog

| Date | Version | Changes |
|------|---------|---------|
| 2026-05-27 | 1.0 | Initial scope lock — Phase 6F planning. Defines collection/group replication scope, semantic rules, cross-lane interaction matrix, replay semantics, observability contract, frozen-runtime guarantees, rollback conditions, and done criteria. Implementation BLOCKED — design phase only. |
| 2026-05-30 | 2.0 | Updated: Collection is IMPLEMENTED — TESTED. Parser (ProcessBinaryPacket), handler (HandleCollection), per-pair sequence tracking, world replay recording, ConsoleReset/StopNetworkThread lifecycle all active. Integration tests in `tests/run_phase6f_collection.py` pass 10/10. |
