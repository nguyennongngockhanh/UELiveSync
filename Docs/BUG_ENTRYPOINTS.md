# Bug Entrypoints — Surgical Debugging Navigation

Use this to skip generic exploration and go directly to the right code.

---

## Connection/Bootstrap

- **Files**: `sync.py:696` (reconnect_block), `network.py` (TCP client)
- **Functions**: `_reconnect()`, `_sender_loop()`, `stop_sync()`
- **Registries**: `_sync_running`, `_reconnect_backoff`
- **Failure modes**: Port mismatch, Blender blocking on reconnect, deadlock on StopNetworkThread order violation

## Packet Serialization

- **Files**: `network.py` (all `serialize_*`), `sync.py` (calls serializers)
- **Functions**: `serialize_transform()`, `serialize_create()`, `serialize_hierarchy()`, `serialize_delete()`, `serialize_visibility()`, `serialize_rename()`, `build_v5_header()`
- **Verification**: FNV protocol signature parity between `network.py:38-42` and `SyncTypes.h:755-761`
- **Failure modes**: Missing PT type from FNV hash, payload size mismatch, endianness flip, version field wrong

## Sender Queue (Blender)

- **Files**: `network.py` (send queue), `sync.py` (enqueue calls)
- **Registries**: `_send_queue` (Python `queue.Queue`)
- **Failure modes**: Queue full → silent drop, daemon thread crash, heartbeat starvation during burst

## Replay System

- **Files**: `UELiveSyncSubsystem.cpp` (GWorldReplayBuffer, SaveWorldState, RestoreWorldState, RebuildWorldFromSnapshot, VerifyWorldReplay)
- **Functions**: `SaveWorldState()` (~line 7800), `RestoreWorldState()` (~line 8100), `RebuildWorldFromSnapshot()` (~line 8400), `VerifyWorldReplay()` (~line 8200), `ComputeWorldStateHash()` (~line 7940), `ExportWorldSnapshot()` (~line 8700)
- **Registries**: `GWorldReplayBuffer` (line 265), `GWorldReplayBuffer` cleared at lines 1893, 11993
- **Failure modes**: Hash mismatch → rollback, missing replay domain, cross-domain dependency violation (create-before-rename), replay buffer overflow

## Collection Sync (Phase 6F)

- **Files**: `UELiveSyncSubsystem.cpp` (HandleCollection, ApplyCollectionMembership), `SyncTypes.h` (FCollectionSequenceTracker, PT_Collection)
- **Functions**: `HandleCollection()`, `ApplyCollectionMembership()`, `ComputeCollectionStateHash()`
- **Registries**: `GCollectionMembership`, `GCollectionIdentities`
- **Failure modes**: Collection replay divergence, membership hash mismatch, cross-lane sequence coupling

## Rename Persistence (Phase 6G)

- **Files**: `UELiveSyncSubsystem.cpp` (GRenamePersistentLabel), `sync.py` (_compute_owner_hash)
- **Functions**: `HandleRename()` (line 5992), `HandleCreateObject()` (line 5682), `RestoreWorldState()` (line 8172, 8196), `RebuildWorldFromSnapshot()` (line 8632), `ComputeWorldStateHash()` (line 7972)
- **Registries**: `GRenamePersistentLabel` (line 189, cleared line 11908 on ConsoleReset, NOT on StopNetworkThread)
- **Failure modes**: Label lost on reconnect, obj.name included in hash → GUID churn, HandleCreateObject missing persistent restore

## GUID Stability

- **Files**: `sync.py:361-424` (GUID system)
- **Functions**: `_compute_owner_hash()` (line 365), `ensure_guid()` (line 387), `ensure_unique_guid()` (line 400), `_reconcile_guids_on_load()` (line 425)
- **Registries**: `tracked_objects`, `_known_guids`, `obj["ue_guid"]`, `obj["ue_guid_owner_hash"]`
- **Failure modes**: obj.name in hash → GUID churn on rename, copy inherits GUID without collision detection, corrupted hash → false reconcile

## Hierarchy Parenting

- **Files**: `sync.py` (`_last_parent_guid`, `serialize_hierarchy`), `UELiveSyncSubsystem.cpp` (HandleHierarchy, ResolveHierarchyAttachments)
- **Functions**: Blender: parent diff (line 1202-1220), serialize_hierarchy (new GUID per call). UE: HandleHierarchy (~line 6200), ResolveHierarchyAttachments (~line 6532)
- **Registries**: `_last_parent_guid` (Blender), `GReplayHierarchyAttachments` (UE, deferred queue)
- **Failure modes**: Parent GUID not set before child, deferred queue overflow, cycle detection false positive, attachment lost on replay rebuild, **transform overwriting child local space after parent move** (see Problem B)

## Transform Propagation

- **Files**: `UELiveSyncSubsystem.cpp` (InterpolateTransforms, HandleTransformPacket, FSyncTransformState), `sync.py` (serialize_transform)
- **Functions**: `InterpolateTransforms()` (line 4029), `HandleTransformPacket()` (~line 2600), `serialize_transform()` (network.py)
- **Registries**: TransformStates map (FSyncTransformState per GUID)
- **Failure modes**: Local/world space mixup, child transform applied as world-space while parented, parent move not propagated to children, root↔child authority transition gap (see transform-domain transition invariant TF-5), **InterpolateTransforms before ResolvePendingAttachments** (see Bug #3)

## Duplicate/Create Flows

- **Files**: `sync.py` (scan_scene, ensure_unique_guid, serialize_create, serialize_transform), `UELiveSyncSubsystem.cpp` (HandleCreateObject)
- **Functions**: `scan_scene()` (line 698), `serialize_create()` (network.py), `HandleCreateObject()` (~line 5400), `HandleTransformPacket()` (~line 2600)
- **Critical**: Ensure duplicated objects get unique GUIDs via `ensure_unique_guid` collision detection (line 400)
- **Failure modes**: **Multi-object duplicate transform corruption** — parent-relative transforms serialized as world, batched CREATE+TRANSFORM reordered, tracked_objects snapshot race (see Problem A)

## Delete Lifecycle

- **Files**: `sync.py` (detect_deleted_objects, serialize_delete), `UELiveSyncSubsystem.cpp` (HandleDelete_V5)
- **Functions**: `detect_deleted_objects()` (~line 980), `serialize_delete()` (network.py), `HandleDelete_V5()` (~line 6900)
- **Registries**: `_known_guids`, tombstone map, `_delete_sequences`
- **Failure modes**: Tombstone miss, child delete before parent, deferred snapshot queue overflow, delete during replay → state divergence

## Snapshot Rebuild

- **Files**: `UELiveSyncSubsystem.cpp` (RebuildWorldFromSnapshot, ExportWorldSnapshot)
- **Functions**: `RebuildWorldFromSnapshot()` (~line 8400), `ExportWorldSnapshot()` (~line 8700)
- **Registries**: GWorldReplayBuffer, ActorCache (rebuilt)
- **Failure modes**: Missing rename domain in Export → lost labels, collection rebuild order wrong, hierarchy parent missing at rebuild time

## Replay Rollback

- **Files**: `UELiveSyncSubsystem.cpp` (RestoreWorldState)
- **Functions**: `RestoreWorldState()` (~line 8100), `SaveWorldState()` (~line 7800)
- **Registries**: World state snapshots (temp copies during restore)
- **Failure modes**: Rename domain not restored, collection divergence unrecoverable, rollback fails → corrupt world state

## UE Spawn/Apply

- **Files**: `UELiveSyncSubsystem.cpp` (HandleCreateObject, BuildActorCache)
- **Functions**: `HandleCreateObject()` (~line 5400), `BuildActorCache()` (line 4745)
- **Registries**: ActorCache
- **Failure modes**: Duplicate spawn, missing mesh/primitive type, level override, spawn-at-origin before snapshot applies

## Diagnostics/Console

- **Files**: `UELiveSyncSubsystem.cpp` (DumpState, Stats), `SyncTypes.h` (FLiveSyncStats)
- **Commands**: `UE.LiveSync.DumpState`, `UE.LiveSync.Stats`, `UE.LiveSync.Ping`, `UE.LiveSync.Reset`, `UE.LiveSync.DumpReplayBuffer`, `UE.LiveSync.DumpCollectionGraph`, `UE.LiveSync.VerifyCollectionReplay`, `UE.LiveSync.VerifyWorldReplay`, `UE.LiveSync.DumpReplayTimeline`, `UE.LiveSync.ExportWorldSnapshot`
- **CVars**: `UE.LiveSync.Port`, `UE.LiveSync.Verbose`, `UE.LiveSync.VerboseSyncLogs`, `UE.LiveSync.DebugDraw`, `UE.LiveSync.Threshold.*`, `UE.LiveSync.InterpMode`, `UE.LiveSync.MaxPacketRate`, `UE.LiveSync.QueueWarnThreshold`

---

## A — Duplicate Spawn Offset Drift

**Symptoms**: Multi-object Shift+D → correct count, wrong/inconsistent positions.

**Entry points** (Blender):
- `sync.py:738` — `ensure_unique_guid(obj, tracked_objects)` — GUID collision detection for copies
- `sync.py:698` — `scan_scene()` — how duplicates enter tracked_objects
- `network.py` — `serialize_create()` — what transform is serialized (WORLD? LOCAL?)
- `sync.py` — `serialize_transform()` — first transform after create, potential stale cache issue

**Entry points** (UE):
- `UELiveSyncSubsystem.cpp:5400` — `HandleCreateObject` — how initial transform is applied
- `UELiveSyncSubsystem.cpp:2600` — `HandleTransformPacket` — subsequent transform correction
- `UELiveSyncSubsystem.cpp:4029` — `InterpolateTransforms` — tick pipeline ordering relative to attachments
- `UELiveSyncSubsystem.cpp:6532` — `ResolveHierarchyAttachments` — deferred parent resolution

**Key questions**:
- Are duplicated objects serialized with local transforms (parent-relative) instead of world transforms?
- Does tracked_objects snapshot the duplicate before or after parent transform is resolved?
- Does the burst of CREATE + TRANSFORM packets arrive in correct order?
- Are transforms applied to actors before hierarchy/attachment resolves?

## B — Parent Relationship Decay

**Symptoms**: Parenting works initially, child follows parent, then after unrelated interaction the child stops following.

**Entry points** (Blender):
- `sync.py:1202-1220` — parent diff logic: `_last_parent_guid[guid]` comparison
- `sync.py:142` — `_last_parent_guid` registry declaration and lifecycle
- `sync.py:987` — `detect_deleted_objects()` — could delete the parent tracking?
- `network.py` — `serialize_hierarchy()` — PT_Hierarchy packet construction

**Entry points** (UE):
- `UELiveSyncSubsystem.cpp:6200` — `HandleHierarchy` — AttachToActor/DetachFromActor application
- `UELiveSyncSubsystem.cpp:6532` — `ResolveHierarchyAttachments` — deferred parent resolution retry
- `UELiveSyncSubsystem.cpp:4029` — `InterpolateTransforms` — child transform handling after parent move
- `UELiveSyncSubsystem.cpp:2600` — `HandleTransformPacket` — does it overwrite attachment child transforms?
- `UELiveSyncSubsystem.cpp:4800` — `BuildActorCache` / `RecoverMissingActors` — could rebuild invalidate hierarchy?

**Key questions**:
- Does InterpolateTransforms set world transforms on attached children, breaking relative transform?
- Does something clear/reset `_last_parent_guid` on the Blender side?
- Does an unrelated object's transform update cause hierarchy re-evaluation that detaches the child?
- Is there a missing re-attach step in the transform update path for children?
- Does `HandleTransformPacket` for a child use KeepWorld (breaking attachment) vs StayInPlace?

---

**Companion docs**:
- `ARCHITECTURE.md` — topology, packet flow, tick pipeline
- `CRITICAL_INVARIANTS.md` — 60 hard invariants across 10 categories (GUID, replay, rename, transform, hierarchy, collection, snapshot, rollback, networking, diagnostics)
- `KNOWN_GOOD_FLOWS.md` — 9 canonical execution paths with success/failure signatures
- `PROJECT_INIT.md` — current status, protocol versions, console commands
- `TASK_PROMPT_TEMPLATES.md` — scoped task templates
