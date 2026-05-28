# Hot Paths — Ultra-Fast Bug Navigation

MAX 80 lines. Function-level only. No explanations >1-2 lines.

## Connection Bootstrap
- Files: `sync.py:696` (reconnect_block), `network.py` (TCP client)
- Functions: `connect()`, `stop_sync()`, `_sender_loop()`
- Registries: `_sync_running`, `_client`, `_reconnect_backoff`
- Invariant: NW-1 (magic validation), NW-2 (version compat)

## Create Flow
- Files: `sync.py:698` (scan_scene), `sync.py:738` (ensure_unique_guid)
- UE: `HandleCreateObject()` ~5400, `ProcessBinaryPacket()` ~3170
- Registries: `tracked_objects`, `ActorCache`, `FSyncTransformState`
- Invariant: GI-3 (duplicate GUID regeneration), SN-1 (domain completeness)

## Transform Drift
- Files: `sync.py:630` (get_transform), `network.py:300` (serialize_object_v3)
- UE: `InterpolateTransforms()` ~4035, `UpdateTargetTransform()` ~3593
- Registries: `TransformStates`, `last_sent_transforms`
- Critical: local vs world mixup (TF-4), bHasLocalTarget checks at line 4247, root↔child authority transition (TF-5)

## Hierarchy Decay
- Files: `sync.py:1204` (parent diff), `network.py:588` (serialize_hierarchy)
- UE: `HandleHierarchy()` ~6255, `ResolveHierarchyAttachments()` ~6637
- Registries: `_last_parent_guid`, `GHierarchySequences`, `PendingHierarchyAttachments`
- Invariant: HI-4 (transforms must NOT detach children)

## Replay Divergence
- Files: `SyncTypes.h` (EWorldReplayDomain, FWorldReplayEntry)
- UE: `SaveWorldState()` ~590, `RestoreWorldState()` ~667, `ComputeWorldStateHash()` ~418 (all in _Replay.inl)
- UE: `VerifyWorldReplay()` ~801, `RebuildWorldFromSnapshot()` ~1099 (all in _Replay.inl)
- Registries: `GWorldReplayBuffer` (4096), `GWorldSavedState`
- Invariant: RD-1 (determinism)

## Rename Persistence
- Files: `sync.py:365` (_compute_owner_hash — excludes obj.name)
- UE: `HandleRename()` ~6013, `HandleCreateObject()` ~5692 (label restore)
- UE: `RestoreWorldState()` ~667, `RebuildWorldFromSnapshot()` ~1099 (both in _Replay.inl)
- Registries: `GRenamePersistentLabel` (NOT cleared on StopNetworkThread, only ConsoleReset in _Diagnostics.inl ~576)
- Invariant: RN-1 (GRenamePersistentLabel is sole label authority)

## GUID Instability
- Files: `sync.py:365` (_compute_owner_hash), `sync.py:400` (ensure_unique_guid)
- Files: `sync.py:425` (_reconcile_guids_on_load)
- Registries: `obj["ue_guid"]`, `obj["ue_guid_owner_hash"]`, `tracked_objects`
- Invariant: GI-1 (exclude obj.name from hash)

## Duplicate Spawn Drift
- Files: `sync.py:630` (get_transform — matrix_local for parented)
- UE: `ProcessBinaryPacket()` ~3464 (world-spawn computation)
- UE: `HandleCreateObject()` ~5400, `InterpolateTransforms()` ~4306 (bPendingSceneGraphWrite retry)
- Root cause: parent not in ActorCache at CREATE time → local-as-world spawn

## Collection Rebuild
- Files: `network.py` (serialize_collection)
- UE: `HandleCollection()` ~6925, `ApplyCollectionMembership()` ~7082
- Registries: `GCollectionMembership`, `GCollectionIdentities`, `GCollectionReplayBuffer` (2048)
- Invariant: CL-1 (idempotent replay)

## Rollback Corruption
- Files: `SyncTypes.h` (FWorldStateSnapshot)
- UE: `SaveWorldState()` ~590, `RestoreWorldState()` ~667 (both in _Replay.inl)
- UE: `ComputeWorldStateHash()` ~418 (_Replay.inl)
- Invariant: RB-1 (full restore), RB-2 (no diagnostic mutation)

## Snapshot Mismatch
- Files: `SyncTypes.h`
- UE: `ExportWorldSnapshot()` ~958, `RebuildWorldFromSnapshot()` ~1099 (both in _Replay.inl)
- Registries: rename domain, collection domain, hierarchy domain
- Invariant: SN-2 (rebuild must mirror original)

## Actor Spawn Failure
- UE: `HandleCreateObject()` ~5400, `BuildActorCache()` ~4758
- UE: `RecoverMissingActors()` ~4797
- Registries: `ActorCache`, `TransformStates`
- Root causes: missing primitive type, level override, GUID collision
