# Surgical Task Prompt Templates

Copy-paste these to scope debugging tasks. **Always read `Docs/ARCHITECTURE.md` first.**

> **File split**: `UELiveSyncSubsystem.cpp` (9327 lines) is the entry point. Replay functions live in `UELiveSyncSubsystem_Replay.inl` (1942 lines). Console commands live in `UELiveSyncSubsystem_Diagnostics.inl` (919 lines). All three compile as a single TU — grep across all three files to find any symbol.

---

## Networking Bug

```
Investigate [describe symptom — e.g., "heartbeat timeout on reconnect"].

Scope:
- network.py only — TCP client, sender loop, reconnect logic
- sync.py reconnect_block — call site only

Do NOT explore:
- UE plugin code
- replay system
- Blender scene scan

Return:
- root cause with line numbers
- file to modify
- proposed additive fix
```

## Replay Divergence

```
Investigate replay hash mismatch between [save/restore cycle].

Scope:
- UELiveSyncSubsystem_Replay.inl — SaveWorldState, RestoreWorldState,
  ComputeWorldStateHash, VerifyWorldReplay
- SyncTypes.h — FWorldReplayEntry, EWorldReplayDomain

Entry functions:
- UELiveSyncSubsystem_Replay.inl: SaveWorldState (~590), RestoreWorldState (~667),
  VerifyWorldReplay (~801)

Do NOT explore:
- Blender addon
- network thread
- Tick pipeline before ProcessQueuedPackets

Return:
- exact domain where hash diverges
- root cause with line numbers
- additive fix only
- confirm no frozen-runtime modification
```

## Hierarchy Bug

```
Investigate [describe symptom — e.g., "child stops following parent after unrelated transform"].

Scope:
- Blender_Addon/sync.py — _last_parent_guid diff, serialize_hierarchy
- UE/UELiveSyncSubsystem.cpp — HandleHierarchy, ResolveHierarchyAttachments
  InterpolateTransforms, HandleTransformPacket (child transform path)

Entry functions:
- sync.py lines 1202-1220 (parent diff)
- UE HandleHierarchy ~line 6200
- UE InterpolateTransforms ~line 4029
- UE ResolveHierarchyAttachments ~line 6532

Do NOT explore:
- collection sync
- rename persistence (unless directly related)
- replay rollback path

Return:
- root cause with exact line numbers
- whether attachment is lost on UE side or parent signal stops on Blender side
- additive fix proposal
```

## Transform Desync

```
Investigate [describe symptom — e.g., "duplicate objects spawn with wrong positions"].

Scope:
- Blender_Addon/sync.py — serialize_transform, serialize_create
- Blender_Addon/network.py — create/transform serialization functions
- UE/UELiveSyncSubsystem.cpp — HandleCreateObject, HandleTransformPacket, InterpolateTransforms

Entry functions:
- sync.py: serialize_transform (network.py)
- UE HandleCreateObject ~line 5400
- UE InterpolateTransforms ~line 4029

Do NOT explore:
- rename, visibility, collection, delete lifecycle
- replay architecture (unless suspect)

Return:
- whether transform is world or local space at serialization
- whether UE applies initial transform correctly
- whether parent-relative transforms leak into world space
- exact code path and line numbers
```

## Create/Delete Regression

```
Investigate [describe symptom — e.g., "object created but no actor spawned in UE"].

Scope:
- Blender_Addon/sync.py — scan_scene, detect_deleted_objects
- Blender_Addon/network.py — serialize_create, serialize_delete
- UE/UELiveSyncSubsystem.cpp — HandleCreateObject, HandleDelete_V5

Entry functions:
- scan_scene() sync.py line 698
- HandleCreateObject ~line 5400
- HandleDelete_V5 ~line 6900

Do NOT explore:
- transform pipeline
- replay architecture
- rename

Return:
- whether the packet is sent at all
- whether UE receives and parses it
- whether the handler bails early
- exact line numbers
```

## Rename Persistence

```
Investigate [describe symptom — e.g., "rename label lost after reconnect"].

Scope:
- UE/UELiveSyncSubsystem.cpp — GRenamePersistentLabel
  HandleRename, HandleCreateObject
- UELiveSyncSubsystem_Replay.inl — RestoreWorldState, RebuildWorldFromSnapshot
- Blender_Addon/sync.py — _compute_owner_hash (if GUID changed)

Entry functions:
- HandleRename ~line 6013
- HandleCreateObject line 5682
- GRenamePersistentLabel line 189, cleared: ConsoleReset in _Diagnostics.inl ~576
- _compute_owner_hash sync.py line 365

Do NOT explore:
- hierarchy, visibility, collection
- replay rollback (unless suspect)
- transform pipeline

Return:
- whether label was stored in GRenamePersistentLabel
- whether it was cleared at the wrong lifecycle point
- whether HandleCreateObject missed the persistent restore
- exact line numbers
```

## Packet Corruption

```
Investigate malformed packet rejection — [describe symptom].

Scope:
- UE/UELiveSyncSubsystem.cpp — ProcessBinaryPacket, packet dispatch
- SyncTypes.h — packet size constants, magic, version validation

Entry:
- ProcessQueuedPackets ~line 1952
- Binary packet parsing ~line 2550

Do NOT explore:
- Blender addon
- semantics handlers (rename/create/etc.)
- replay architecture

Return:
- exact parsing stage where packet is rejected
- whether it's a size/type/version/magic mismatch
- line numbers
```

## World Rebuild Issue

```
Investigate [describe symptom — e.g., "RebuildWorldFromSnapshot missing transforms"].

Scope:
- UELiveSyncSubsystem_Replay.inl — RebuildWorldFromSnapshot, ExportWorldSnapshot
  ComputeWorldStateHash, SaveWorldState

Entry:
- UELiveSyncSubsystem_Replay.inl: RebuildWorldFromSnapshot (~1099),
  ExportWorldSnapshot (~958)

Do NOT explore:
- Blender addon
- live packet processing
- collection-specific replay

Return:
- which domain fails to export/rebuild
- whether GUIDs match between export and rebuild
- line numbers
```

---

> **Always add**: "Confirm no frozen-runtime modifications required. Propose additive-only fix."
