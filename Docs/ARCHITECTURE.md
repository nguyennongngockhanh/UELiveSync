# UELiveSync Architecture

**Read this first before exploring repository.**

**Also read**: `CRITICAL_INVARIANTS.md` (hard rules), `KNOWN_GOOD_FLOWS.md` (canonical paths)

## System Topology

```
Blender Addon (Python)                UE5 Plugin (C++)
┌─────────────────────┐   TCP :57000  ┌──────────────────────────┐
│ Main Thread         │ ────────────→ │ Network Thread            │
│  sync.py            │   binary      │  LiveSyncRunnable.cpp     │
│  network.py         │   packets     │  → FLiveSyncQueue (128)   │
│  __init__.py        │ ← ────────── │  (unidirectional)         │
│ (daemon sender)     │   ACK-less   │                           │
└─────────────────────┘              │ Game Thread (Tick)        │
                                      │  UELiveSyncSubsystem.cpp  │
                                      └──────────────────────────┘
```

## Packet Flow (Blender → UE)

1. **Blender Main Thread**: `sync.py` iterates `bpy.data.objects`, diffs against `tracked_objects`, detects changes
2. **Serialization**: `network.py` packs structs via `struct.pack("<f...")` — little-endian binary
3. **Enqueue**: serialized bytes pushed to background daemon thread's send queue
4. **TCP Send**: daemon thread calls `socket.sendall()` — blocking OK in background thread
5. **UE Network Thread**: `Wait(10ms)` + `Recv()` → parses header → packs `FLiveSyncPacket`
6. **Enqueue**: `FLiveSyncPacket` pushed to `FLiveSyncQueue` (bounded 128, drop-oldest)
7. **UE Game Thread**: `ProcessQueuedPackets()` dequeues → dispatches by `EPacketType`

## Tick Pipeline (Strict Order)

```
ProcessQueuedPackets → InterpolateTransforms → ResolvePendingAttachments
  → RecoverMissingActors → ResolvePendingAssets
```

**FROZEN** — do not reorder or skip stages without critical-bug justification.

## Authoritative State Ownership

| Domain | Authoritative | UE Role |
|--------|-------------|---------|
| Transform | Blender (obj.matrix_world.decompose) | Interpolation client-only |
| Create/Delete | Blender (scene scan diff) | Spawn/destroy actor |
| Rename | Blender (PT_Rename packet) | Apply + persist via GRenamePersistentLabel |
| Hierarchy | Blender (obj.parent) | AttachActor/DetachFromActor |
| Visibility | Blender (hide_viewport/hide_render) | Toggle actor visibility |
| Collection | Blender (users_collection) | Membership registry |
| Lifecycle | Blender (_known_guids diff) | Tombstone-gated destroy |

## Replay Architecture

- **GWorldReplayBuffer**: `TArray<FWorldReplayEntry>`, max 4096 entries
- **FWorldReplayEntry**: domain (EWorldReplayDomain), packet type, GUID, seq, timestamp, payload, FNV-1a checksum
- **SaveWorldState**: captures current state across all domains (collection, lifecycle, rename, transform)
- **RestoreWorldState**: transactional rollback — temp save → apply replay → hash compare → rollback if divergent
- **RebuildWorldFromSnapshot**: full rebuild from exported snapshot data
- **ComputeWorldStateHash**: FNV-1a 64-bit across all domains, sorted GUIDs
- **EWorldReplayDomain**: Unknown(0), Collection(1), Lifecycle(2), Rename(3), Transform(4)

## Packet Types

| Type | Value | Payload | Description |
|------|-------|---------|-------------|
| PT_Transform | 0x01 | 81 bytes V5 | Per-frame transform update |
| PT_Create | 0x03 | 81 bytes V5 | Object spawn |
| PT_Delete | 0x04 | 16 bytes V3 | Legacy delete |
| PT_Heartbeat | 0x07 | 0 bytes | Keep-alive (5s Blender → 15s timeout) |
| PT_AssetDef | 0x08 | 33 bytes V5 | Asset identity |
| PT_BeginSnapshot | 0x09 | 0 bytes | Snapshot start marker |
| PT_EndSnapshot | 0x0A | 0 bytes | Snapshot end marker |
| PT_Visibility | 0x0B | 29 bytes | Toggle visibility |
| PT_Rename | 0x0C | variable | Rename with old/new name |
| PT_Hierarchy | 0x0D | 44 bytes | Parent-child attachment |
| PT_Delete_V5 | 0x0E | 28 bytes | Lifecycle delete |
| PT_Collection | 0x0F | 30/46 bytes | Collection membership |

## Key Registries/Maps (UE Side)

| Registry | Type | Scope | Persistence |
|----------|------|-------|-------------|
| ActorCache | `TMap<FGuid, AActor*>` | Session | Rebuilt on reconnect |
| GRenamePersistentLabel | `TMap<FGuid, FString>` | Session | Survives reconnect; cleared on ConsoleReset |
| GWorldReplayBuffer | `TArray<FWorldReplayEntry>` | Session | Cleared on ConsoleReset/StopNetworkThread |
| TransformStates | `TMap<FGuid, FSyncTransformState>` | Session | Rebuilt on reconnect |
| GCollectionMembership | `TMap<FGuid, TSet<FGuid>>` | Session | Rebuilt on reconnect |
| Sequence Trackers (x6) | `TMap<FGuid, uint32>` | Session | Cleared on disconnect/reconnect/ConsoleReset |

## Key Registries (Blender Side)

| Registry | Type | Scope |
|----------|------|-------|
| tracked_objects | `dict[guid → (obj, UUID)]` | Session |
| _known_guids | `set[guid]` | Per-tick |
| _last_parent_guid | `dict[guid → guid\|None]` | Per-object |
| _last_sent_transforms | `dict[guid → (loc, rot, scl)]` | Per-object |
| _delete_sequences | `dict[guid → int]` | Per-object monotonic |

## Critical Invariants (Condensed)

1. **GUID stability**: GUID depends ONLY on `obj.data.name` (excludes `obj.name`)
2. **Blender main thread**: bpy API only; no socket I/O
3. **Blender daemon thread**: socket I/O only; no bpy access
4. **UE network thread**: recv + enqueue only; no UObject access
5. **UE game thread**: all UObject/world mutations
6. **StopNetworkThread order**: `Runnable->Stop()` → `Socket->Shutdown()` → `Socket->Close()` → `WaitForCompletion()`
7. **No GUID remap on rename**: `_compute_owner_hash` excludes `obj.name`
8. **Packet header**: 24 bytes fixed, little-endian, magic `0x4C56534D`
9. **Queues**: FLiveSyncQueue bounded 128 (drop-oldest)
10. **No O(1) → O(n) regressions**: scene scan on mismatch/300 frames only

## Blender → UE Execution Chain (Per-Tick)

```
scan_scene()                     — detect adds/removes
_reconcile_guids_on_load()       — fix stale owner hashes
detect_deleted_objects()         — _known_guids diff → PT_Delete_V5
  for each new object:
    ensure_guid()                — assign/verify GUID
    serialize_create()           — PT_Create packet
    serialize_hierarchy()        — PT_Hierarchy if parented
  for each changed object:
    serialize_transform()        — PT_Transform if transform dirty
    serialize_rename()           — PT_Rename if name changed
    serialize_visibility()       — PT_Visibility if visibility toggled
    serialize_hierarchy()        — PT_Hierarchy if parent changed
  for each deleted object:
    serialize_delete()           — PT_Delete_V5 packet
_known_guids = set(tracked_objects.keys())
```

## Blender Coord → UE Coord

- Scale ×100 (meters → cm)
- Y-axis flip (Z-up with Y flip)
- Single conversion point in `sync.py`

---

**Companion docs**:
- `CRITICAL_INVARIANTS.md` — hard rules, do-not-break barriers
- `KNOWN_GOOD_FLOWS.md` — canonical execution paths for all 9 major flows
- `BUG_ENTRYPOINTS.md` — surgical debugging navigation
- `PROJECT_INIT.md` — current state, protocol versions, console commands
- `TASK_PROMPT_TEMPLATES.md` — copy-paste scoped task templates
