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

## FBX Mesh Temp Import Architecture

- **Do not use UE FBX reimport-over-existing StaticMesh for live sync.** UE 5.7.4 reimport can regress to meter-size geometry under certain conditions.
- **Live sync uses unique temp StaticMesh asset per sync.** Each sync exports FBX to a unique temp path, imports it as a new StaticMesh asset, validates the imported mesh, then assigns it to the SMC.
- **Previous temp mesh cleanup** happens after successful assignment. If assignment fails, the previous mesh is preserved for diagnostics.
- **Material generated MID** is restored after FBX mesh assignment to prevent material loss.
- **Unit conversion policy**:
  - Blender writes FBX with meter unit metadata using `apply_scale_options='FBX_SCALE_UNITS'` (`UnitScaleFactor=100`), `global_scale=1.0`, `bake_space_transform=False`.
  - UE converts scene unit to cm with `bConvertSceneUnit=true`.
  - No actor or component scale compensation — actor scale remains `(1,1,1)`, StaticMeshComponent relative scale remains `(1,1,1)`.
  - Invalid unit imports are rejected/preserved rather than compensated.

## Packet Types

| Type | Value | Payload | Description |
|------|-------|---------|-------------|
| PT_Transform | 0x01 | 81 bytes V5 | Per-frame transform update |
| PT_Create | 0x03 | 81 bytes V5 | Object spawn |
| PT_Delete | 0x04 | 16 bytes V3 | Legacy delete |
| PT_Material | 0x05 | variable | Material identity + slot metadata |
| PT_Mesh | 0x06 | variable | Procedural mesh chunk |
| PT_Heartbeat | 0x07 | 0 bytes | Keep-alive (5s Blender → 15s timeout) |
| PT_AssetDef | 0x08 | 33 bytes V5 | Asset identity |
| PT_BeginSnapshot | 0x09 | 0 bytes | Snapshot start marker |
| PT_EndSnapshot | 0x0A | 0 bytes | Snapshot end marker |
| PT_Visibility | 0x0B | 29 bytes | Toggle visibility |
| PT_Rename | 0x0C | variable | Rename with old/new name |
| PT_Hierarchy | 0x0D | 44 bytes | Parent-child attachment |
| PT_Delete_V5 | 0x0E | 28 bytes | Lifecycle delete |
| PT_Collection | 0x0F | 30/46 bytes | Collection membership |
| PT_CapabilityResponse | 0x12 | 1 byte | Capability negotiation response |
| PT_Timeline | 0x13 | 36 bytes | Timeline state (frame range, FPS) |
| PT_PlaybackState | 0x14 | 14 bytes | Playback state (play/pause/stop) |
| PT_ActiveCamera | 0x15 | 28 bytes | Active camera GUID |
| PT_FBXImportRequest | 0x16 | 680 bytes | FBX import request |
| PT_Keyframe | 0x17 | 14 + N×25 bytes | Keyframe batch (transform ch 0–8, visibility ch 9–10) |
| PT_SequencerOp | 0x18 | 16 + payload bytes | Sequencer operation (create sequence, add binding, etc.) |
| PT_TimelineState | 0x19 | 20 bytes | Timeline state (frame range, FPS, apply to LevelSequence) |
| PT_PlaybackTransport | 0x1A | 6 bytes | Playback transport command: SetFrame=0/Play=1/Pause=2/Stop=3 |

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

## Keyframe Extraction Pipeline

Keyframe extraction runs at the end of `check_updates()`, after transform/visibility/hierarchy detection, and is gated on `is_keyframe_effective()` (local pref enabled + remote capability confirmed or connected).

### Blender FCurve Access (5.1+ Slotted Actions)

In Blender 5.1+, the classic `action.fcurves` property is removed. FCurves live inside a slotted/layered Action structure:

```
Action (is_action_layered=True)
├── slots[]                 (ActionSlot: identifier, handle, target_id_type)
└── layers[]
    └── strips[]            (ActionKeyframeStrip: type='KEYFRAME')
        └── channelbags[]   (ActionChannelbag: slot_handle, fcurves)
            └── fcurves[]   (FCurve: data_path, array_index, keyframe_points)
```

The `_iter_action_fcurves_51()` helper iterates this structure, resolving the correct slot by matching `target_id_type` against the object's `id_type`. The `_extract_keyframes()` function prefers this slotted path when `action.is_action_layered` is True; falls back to legacy `action.fcurves` for pre-5.1 Blender.

### Channel Mapping

| Channel | Property | Track Type |
|---------|----------|------------|
| 0 | location.x | UMovieScene3DTransformTrack (double) |
| 1 | location.y | UMovieScene3DTransformTrack (double) |
| 2 | location.z | UMovieScene3DTransformTrack (double) |
| 3 | rotation_euler.x | UMovieScene3DTransformTrack (double) |
| 4 | rotation_euler.y | UMovieScene3DTransformTrack (double) |
| 5 | rotation_euler.z | UMovieScene3DTransformTrack (double) |
| 6 | scale.x | UMovieScene3DTransformTrack (double) |
| 7 | scale.y | UMovieScene3DTransformTrack (double) |
| 8 | scale.z | UMovieScene3DTransformTrack (double) |
| 9 | hide_viewport | UMovieSceneBoolTrack |
| 10 | hide_render | UMovieSceneBoolTrack |

### Duplicate Suppression

FNV-1a 32-bit hash of all extracted keyframe entries (`_hash_keyframes()`) is stored per-action. If the hash matches the previous frame's hash, the packet is suppressed. Cache cleared on reconnect and stop-sync.

### PT_Keyframe Wire Format

- 14-byte header: Sequence(4) + Timestamp(8) + KeyCount(1) + Flags(1)
- 25-byte entries: ObjectGUID(16) + Frame(4) + Value(4) + ChannelIndex(1)
- Batch split at 255 entries per packet

### Keyframe / Sequencer Runtime

**Blender 5.1+ extraction**: Uses slotted/layered Action API (`action.is_action_layered=True`). Legacy `action.fcurves` no longer used/supported.

**Channel mapping**:
| Channel | Property | Track Type |
|---------|----------|------------|
| 0–2 | location.x/y/z | UMovieScene3DTransformTrack (double) |
| 3–5 | rotation_euler.x/y/z | UMovieScene3DTransformTrack (double) |
| 6–8 | scale.x/y/z | UMovieScene3DTransformTrack (double) |
| 9 | hide_viewport | UMovieSceneBoolTrack |
| 10 | hide_render | UMovieSceneBoolTrack |

**PT_Keyframe remains unchanged** — wire format preserved. Channels 9–10 write to `UMovieSceneBoolTrack` / `UMovieSceneBoolSection` / `FMovieSceneBoolChannel`.

**UE keyframe application prerequisites**:
1. Active LiveSync LevelSequence (created via `PT_SequencerOp CREATE_SEQUENCE`).
2. Actor binding in `LiveSyncGuidToSequencerBinding` (set via `PT_SequencerOp ADD_POSSESSABLE`).

**Sequencer setup packet order**:
1. `PT_SequencerOp CREATE_SEQUENCE` — creates asset-backed `ULevelSequence` at `/Game/UELiveSync/Sequences/LS_UELiveSync_Runtime`.
2. `PT_Create` — spawns actor, populates ActorCache.
3. `PT_SequencerOp ADD_POSSESSABLE` — binds actor via GUID. Must occur after actor creation/cache so `FindActorFast()` can resolve the GUID.
4. `PT_Transform` — transform keyframes (channel 0–8).
5. `PT_Keyframe` — keyframe data (channels 0–10).

**Packet type constants**: `PT_Transform = 0x01` (canonical); `0x02` is reserved/invalid.

**Runtime helper**: `tools/uelivesync_stage10a5_active_sequence.py` validates the active LevelSequence setup path.

**Stage 10B.2 Asset-backed sequence validation**: `tools/uelivesync_10b_tcp_client.py` injects the full 5-packet sequence flow via standalone TCP. `tools/uelivesync_10b_asset_sequence_validation.py` validates log markers (`[SEQ][ASSET_CREATE/LOAD/READY]`, `[KEYFRAME]`) and asset file persistence on disk (`LS_UELiveSync_Runtime.uasset`). Supports three modes: full flow (default), `--check-log` (no TCP injection), and `--ue-python` (run inside UE).

**Stage 10B.3 UE Python load asset verification**: `unreal.load_asset("/Game/UELiveSync/Sequences/LS_UELiveSync_Runtime")` returns a valid `LevelSequence`. Classification PASS_LOAD_ONLY — asset loads, but bindings/keyframes were not persisted because `SavePackage()` was only called at sequence creation. Tool: `tools/uelivesync_10b3_uepython_asset_load.py`. Note: `NewObject` must use a named `FName` (not `NAME_None`) for clean asset resolution.

**Stage 10C.1 Persist applied sequencer data**: Added `SaveLiveSyncLevelSequenceAsset()` called from `HandleKeyframe()` after `AppliedKeys > 0`. Upgrades UE Python inspection to PASS_BINDING_ONLY (binding_count=1, MovieScene3DTransformTrack + MovieSceneBoolTrack sections detected). Keyframe keys stored in internal UE channel types not exposed to Python API. Diagnostics: `[SEQ][ASSET_DIRTY]`, `[SEQ][ASSET_SAVE]`, `[SEQ][ASSET_SAVE_FAIL]`, `[SEQ][ASSET_SAVE_SKIP]`. Tool: `tools/uelivesync_10c_saved_sequence_inspection.py`. Test: `tests/phase7e_stage10c_persist_applied_sequence.py` (7/7 PASS).

**Stage 10D.1 Sequencer Editor usability validation**: `LevelSequenceEditorBlueprintLibrary.open_level_sequence()` succeeds. Classification: PASS_EDITOR_DATA_ONLY — the asset is editor-usable with binding_count=1, Actor_0 display name, MovieScene3DTransformTrack + MovieSceneBoolTrack each with 1 section. Manual UI scrub not achievable via `-ExecutePythonScript` (requires interactive editor session). Tool: `tools/uelivesync_10d_editor_sequence_validation.py`. Test: `tests/phase7e_stage10d_editor_sequence_validation.py` (9/9 PASS).

**NullRHI caveat**: `-NullRHI` suppresses Tick/networking in this workflow. Use normal editor or `-RenderOffScreen`.

### Phase 7F Stage 1 — Timeline State Packet (PT_TimelineState = 0x19)

PT_TimelineState (0x19) is distinct from PT_Timeline (0x13). PT_Timeline is storage-only; PT_TimelineState applies frame range and display rate to the LiveSync LevelSequence's playback range via `SetPlaybackRange` and `SetDisplayRate`.

### Phase 7G Stage 2 — Camera Actor Spawn + Active View Target Apply

**LSP_Camera primitive type (0x05)**: Added to `ELiveSyncPrimitiveType` enum. When the Blender addon creates a camera object, UE spawns `ACameraActor` instead of `AActor`. Currently the Blender addon only sends `PT_Create` for MESH-type objects; camera auto-spawn is triggered by `HandleActiveCamera()` when the camera GUID is not found in ActorCache.

**HandleActiveCamera() auto-spawn**: When CVar `UE.LiveSync.ActiveCamera.ApplyToViewport` is enabled, `HandleActiveCamera()` checks `FindActorFast()` for the camera GUID. If not found, it spawns an `ACameraActor` at a default location (0, -200, 100), tags it with `LiveSync_GUID=<guid>`, and registers it in ActorCache.

**Viewport pilot via SetActorLock**: After finding/spawning the `ACameraActor`, the handler iterates over all `FLevelEditorViewportClient` viewports via `GEditor->GetLevelViewportClients()` and calls `SetActorLock(Camera)` on each. This enables the "pilot" (locked camera view) in editor viewports.

**CVar gating**: `UE.LiveSync.ActiveCamera.ApplyToViewport` (default 0) — applies to all level editor viewport clients.

**Diagnostics**: `[CAMERA][ACTIVE_RECV/SPAWN/SPAWN_FAIL/VIEW_TARGET/VIEW_TARGET_SKIP/VIEW_TARGET_FAIL]` at Log level.

**Counters**: `ActiveCameraPacketsSpawned`, `ActiveCameraPacketsViewTargetFailed`, `ActiveCameraPacketsNotCamera`.

**Limitations**: Camera transform is not synced (camera spawns at default position). Camera parameters (FOV, focal length, DOF) are not synced. Camera property keyframes are not extracted. Active camera → Sequencer camera cut binding is not automatic. All these require future packet type additions.

**Wire format** (20 bytes, packed):
| Offset | Field | Type | Description |
|--------|-------|------|-------------|
| 0 | frame_start | int32 | Playback range start |
| 4 | frame_end | int32 | Playback range end |
| 8 | frame_current | int32 | Current frame (informational) |
| 12 | fps_num | int32 | FPS numerator |
| 16 | fps_den | int32 | FPS denominator |

**UE handler**: `HandleTimelineState()` stores payload in `LastTimelineStatePayload`, updates `LiveSyncSequenceFrameStart/End/FPSNum/FPSDen`, and applies to MovieScene. Skips silently if no LiveSync LevelSequence exists. No sequence number or staleness check (applies immediately on each state change).

**Blender send**: `serialize_timeline_state()` uses `struct.pack("<iiiii", ...)`. Sent alongside existing PT_Timeline in the timeline state change branch of `sync.py`.

**Diagnostic markers**: `[TIMELINE][RECV]`, `[TIMELINE][APPLY]`, `[TIMELINE][SKIP]`, `[TIMELINE][MALFORMED]`.

### Phase 7G Stage 3 — Camera Definition / Parameter Sync (PT_CameraDef = 0x1B)

PT_CameraDef (0x1B) carries camera parameters (focal length, sensor dimensions, clip planes, ortho scale) from Blender to UE. Unlike PT_ActiveCamera (which only controls view target switching), PT_CameraDef applies actual camera parameters to the spawned `ACameraActor`.

**Activation**: Requires the corresponding `ACameraActor` to be spawned first via `HandleActiveCamera()` (which is gated by `UE.LiveSync.ActiveCamera.ApplyToViewport=1`). The DEF packet resolves the actor via `FindActorFast()` using the same GUID. Stale DEF packets (received before ActiveCamera) are rejected.

**Perspective mode** (CameraFlags & 0x01 == 0): `SetProjectionMode(Perspective)`. FOV computed as `2 * atan(sensor_width / (2 * focal_length))`. Aspect ratio from sensor dimensions.

**Orthographic mode** (CameraFlags & 0x01 != 0): `SetProjectionMode(Orthographic)`. `OrthoWidth = OrthoScale`, clip planes set via `SetOrthoNearClipPlane` / `SetOrthoFarClipPlane`.

**Clip planes**: `SetNearClipPlane` and `SetFarClipPlane` applied for both modes.

**CVar**: `UE.LiveSync.ActiveCamera.ApplyToViewport` must be 1 — ActiveCamera spawn + viewport pilot + CameraDef apply all gated by this CVar.

**Wire format** (64 bytes, packed):
| Offset | Field | Type | Description |
|--------|-------|------|-------------|
| 0 | CameraGUID | u8[16] | FGuid bytes (TimeLow/TimeMid/TimeHiVersion/ClockSeqHi/Node) |
| 16 | FocalLengthMM | f32 | Focal length in millimeters |
| 20 | SensorWidthMM | f32 | Sensor width in millimeters |
| 24 | SensorHeightMM | f32 | Sensor height in millimeters |
| 28 | ClipStart | f32 | Near clip plane |
| 32 | ClipEnd | f32 | Far clip plane |
| 36 | OrthoScale | f32 | Orthographic width |
| 40 | CameraFlags | u8 | 0x01 = IS_ORTHO, 0x02 = HAS_CAMERA_DEF (0x02 reserved/invalid) |
| 41-63 | Reserved | u8[23] | Zero |

**Non-object packets**: obj_count = 0 for PT_CameraDef (V3+ validation). Byte 10 of header = 0x00.

**Diagnostic markers**: `[CAMERA][DEF_RECV]`, `[CAMERA][DEF_APPLY]`, `[CAMERA][MALFORMED]`, `[CAMERA][DEF] Stale`.

**Counters**: `CameraDefPacketsApplied`, `CameraDefPacketsStale`, `CameraDefPacketsMalformed`.

**Limitations**: Camera transform is not synced (camera spawns at default position). Camera property keyframes are not extracted. Active camera → Sequencer camera cut binding is not automatic.

**Known caveat**: On `-NullRHI`, clip plane and viewport pilot operations may be no-ops. `FieldOfView` still applies. `-NullRHI` testing not included in validation.

---

### Phase 7G Stage 4 — Camera Transform Sync (PT_Create + PT_Transform + PT_ActiveCamera)

**Objective**: Validate liveSync camera transform sync via CREATE + TRANSFORM + ACTIVE_CAMERA pipeline.

**Validation Classification**: `PASS_CAMERA_TRANSFORM_APPLY`

**Runtime Markers**:
- `[CAMERA][CREATE]` — Camera actor spawned
- `[CAMERA][TRANSFORM_APPLY]` — Transform interpolation active
- `[CAMERA][TRANSFORM_CONVERGED]` — Interpolation complete
- `[CAMERA][ACTIVE_RECV]` — ActiveCamera packet received
- `[CAMERA][VIEW_TARGET]` — View target locked

**Changes**:

- **Blender**: `PRIMITIVE_CAMERA = 0x05` added to `network.py`. `_get_primitive_type(obj)` detects `obj.type == 'CAMERA'` and returns `PRIMITIVE_CAMERA`.
- **UE**: `HandleCreateObject` spawns `ACameraActor` when `PrimitiveType == LSP_Camera` (0x05). Root component set to `UCameraComponent`. Primitive type validation updated to allow `LSP_Camera`.
- **Transform**: `InterpolateTransforms` works on all actors in `ActorCache` — no special handling needed for `ACameraActor`.
- **Active Camera**: `HandleActiveCamera()` auto-spawns `ACameraActor` when GUID not in cache, tags and caches it, applies `SetActorLock` on viewport clients.
- **Diagnostics**: `[CAMERA][CREATE]`, `[CAMERA][TRANSFORM_APPLY]`, `[CAMERA][TRANSFORM_CONVERGED]`, `[CAMERA][ACTIVE_RECV]`, `[CAMERA][VIEW_TARGET]` log markers.

**Wire Format**: No new packet type. Camera uses `PT_Create` with `PrimitiveType=0x05`, then `PT_Transform` (0x01) for position/rotation updates.

**Actor Spawn Path**:

1. Blender sends `PT_Create` with `PrimitiveType=0x05` and GUID.
2. UE spawns `ACameraActor` (not `AActor` + `UStaticMeshComponent`).
3. `UCameraComponent` set as root component.
4. `PT_Transform` packets update position/rotation via `InterpolateTransforms`.
5. `PT_ActiveCamera` (0x15) makes this the active viewport camera.

**Injector Timing**: Uses one TCP connection with 0.2s sleeps between CREATE, TRANSFORM, and ACTIVE_CAMERA packets. Fresh sockets per packet are unreliable — UE's network thread calls `StopNetworkThread` on disconnect, preventing subsequent connections from being accepted. One connection keeps the network thread alive across all sends while ensuring packets arrive in separate ticks.

**Injector Lifecycle Note**: The combined CREATE+TRANSFORM+ACTIVE+CAMERA_DEF on one connection can race with socket close / queue timing, potentially dropping CAMERA_DEF. Validation uses separated modes: `--create-transform-active` for Stage 7G.4 markers, `--cameradef-only` for Stage 7G.3 CameraDef markers, `--full-separated` for combined lifecycle testing.

**`-game` Limitation**: In `-game` mode, `GEditor` is null, so `[CAMERA][VIEW_TARGET]` emits `[CAMERA][VIEW_TARGET_FAIL]` instead of locking the viewport. Transform apply (TRANSFORM_APPLY, TRANSFORM_CONVERGED) and active camera receive (ACTIVE_RECV) work correctly in `-game` mode.

**Limitations**: Camera rotation in Blender is Z-forward (up=Z). UE camera is Y-forward (up=Z). The Y-axis flip convention handles translation; rotation requires explicit conversion.

### Phase 7G Stage 5 — Camera Sequencer Binding + CameraCutTrack Integration

**Objective**: Sync the LiveSync active camera to the LevelSequence as a possessable binding with a CameraCutTrack, enabling Sequencer-driven camera cuts.

**Validation Classification**: `PASS_CAMERA_SEQ_BIND_APPLY`

**Runtime Markers**:
- `[CAMERA][SEQ_BIND]` — Found or created possessable binding for camera
- `[CAMERA][SEQ_BIND_SKIP]` — Camera already bound (ExistingBinding reuse)
- `[CAMERA][CUT_TRACK]` — CameraCutTrack created on sequence
- `[CAMERA][CUT_APPLY]` — CameraCutSection added with range (0, 1)
- `[CAMERA][CUT_SKIP]` — CameraCutSection already exists
- `[CAMERA][CUT_SAVE]` — Sequence saved via SaveLiveSyncLevelSequenceAsset

**Capability Flag**: `CAP_SUPPORTS_CAMERA_SEQ_BIND = 0x200` (bit 9)

**Changes**:

- **SyncTypes.h**: Added 6 counters (`ActiveCameraBindingCreated`, `ActiveCameraBindingExists`, `ActiveCameraCutTrackCreated`, `ActiveCameraCutApplied`, `ActiveCameraCutSkipped`, `ActiveCameraSeqSaved`) and capability bit `0x200` included in `UE_LOCAL_CAPABILITIES`.
- **UELiveSyncSubsystem.h**: Declared `EnsureCameraSequencerBinding(ACameraActor*, const FGuid&)` — returns `bool`.
- **UELiveSyncSubsystem.cpp**: Implemented `EnsureCameraSequencerBinding` — gets/creates asset-backed LevelSequence, adds possessable binding via `LiveSyncGuidToSequencerBinding`, creates `UMovieSceneCameraCutTrack` via `AddNewCameraCut` with section range `[0, 1]`, saves sequence. Restructured `HandleActiveCamera` to resolve the camera actor before the CVar gate and call `EnsureCameraSequencerBinding` unconditionally.
- **Diagnostics.inl**: Added logging + reset for all 6 new counters.

**Wire Format**: No new packet type. Uses existing `PT_ActiveCamera` (0x15) path — binding/track creation is triggered as a side-effect of `HandleActiveCamera()`.

**Architecture Flow**:

1. Blender sends `PT_ActiveCamera` with GUID.
2. UE `HandleActiveCamera` resolves camera (FindActorFast / auto-spawn / Cast).
3. `EnsureCameraSequencerBinding` called unconditionally:
   a. Gets asset-backed LevelSequence via `GetOrCreateLiveSyncLevelSequenceAsset()`.
   b. Looks up or creates possessable binding via `LiveSyncGuidToSequencerBinding`.
   c. Finds or creates `UMovieSceneCameraCutTrack` via `AddNewCameraCut`.
   d. Sets CameraCutSection range `[0, 1]` and sets camera binding ID.
   e. Saves sequence via `SaveLiveSyncLevelSequenceAsset()`.
4. CVar `UE.LiveSync.ActiveCamera.ApplyToViewport` gates `SetActorLock` (viewport lock only).

**Editor vs Game Mode**: In editor mode, `StopNetworkThread` on disconnect drains queued packets before the next tick, so the active camera packet can be lost if the connection closes too quickly. Workaround: keep the connection alive ~1.5s after sending ACTIVE_CAMERA. Game mode ticks continuously (~0.7s heartbeat), so packets are processed within one tick of arrival.

**Counters**: 6 new counters — `ActiveCameraBindingCreated`, `ActiveCameraBindingExists`, `ActiveCameraCutTrackCreated`, `ActiveCameraCutApplied`, `ActiveCameraCutSkipped`, `ActiveCameraSeqSaved`.

---

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

---

## Texture Pipeline / MTEX

### Overview

MTEX is an optional texture metadata extension appended after MATX (material slot definition) in the material sync packet. It carries texture slot metadata only — no pixel data, no encoded images.

### MTEX Fields

| Field | Type | Description |
|-------|------|-------------|
| slot | uint8 | Material slot index |
| channel | uint8 | Texture channel (BaseColor=0, Roughness=1, Metallic=2, Alpha=3, Normal=4) |
| flags | uint8 | Bitfield flags (reserved) |
| path | string | Absolute filesystem path to source texture |
| image_name | string | Blender image datablock name |

### Blender Extraction

- Intentionally conservative: only direct Image Texture → Principled BSDF links are extracted.
- Simple Normal Map chain may be supported if implemented.
- No procedural/complex graph traversal — materials using node groups, math, or mix nodes for texture inputs are not resolved.
- Packed Blender images are explicitly not supported (no pixel data serialization).

### UE Texture Import/Cache

- Textures are imported from absolute filesystem paths only.
- Packed images, missing files, relative paths, and unsupported extensions are skipped safely.
- Imported texture assets are stored under `/Game/UELiveSync/Textures`.
- Texture cache is keyed by source path; duplicate paths reuse cached imports.

### UE Material Visual Path

- Generated MID uses the custom master material at `/Game/UELiveSync/Materials/M_UELiveSync_Master`.
- Master material exposes the following texture parameters:
  - `BaseColorTexture`
  - `RoughnessTexture`
  - `MetallicTexture`
  - `AlphaTexture`
  - `NormalTexture`
- Texture on/off toggles:
  - `UseBaseColorTexture`
  - `UseRoughnessTexture`
  - `UseMetallicTexture`
  - `UseAlphaTexture`
  - `UseNormalTexture`

### Limitations

- Alpha visual support is limited/deferred if the master material uses an opaque blend mode.
- Normal visual may be deferred or limited depending on the current master material graph implementation.
- Packed Blender images are not imported (requires pixel data extraction, not in scope).
- Complex node graphs (node groups, math-driven texture blending, procedural textures) are not traversed.

## Architecture Documents

- `Docs/Architecture/phase8-performance-streaming-audit.md` — Phase 8 closeout audit: scope-lock design vs. codebase reality. Only burst packet counting, queue diagnostics, and static rate limiter exist. Backpressure ACK (0x10), adaptive throttling, mesh compression, section optimization, and interest management were designed but never implemented. Classification: `PASS_PHASE8_AUDIT_ONLY`.
- `Docs/Architecture/e2e-runtime-validation-suite.md` — E2E runtime validation orchestration plan. Audits all 11 standalone TCP injectors and provides a 7-phase sequenced validator at `tools/uelivesync_e2e_runtime_validator.py`. Includes manual Blender operator and UE Python reflection steps. Classification: `PASS_E2E_AUDIT_ONLY`.
- `Docs/Architecture/current-state-roadmap.md` — **Canonical current-state reference.** Supersedes all stale scope-lock assumptions. Complete phase status table, packet registry truth table, implemented vs NOT implemented, validation classifications, known limitations, and recommended next work options. Stage 3B discovery scan + Stage 3C auto-fill/connect UX implemented (`PASS_DISCOVERY_LOCALHOST_SCAN`, `PASS_DISCOVERY_CONNECT_UX`). Tag: `current-state-roadmap-stable`.
- `Docs/Architecture/57-phase9-production-ecosystem-audit.md` — Phase 9 production ecosystem audit: capability announce/response, discovery scan, reconnect/backoff, diagnostics. Classification (Stage 3B complete, Stage 3C auto-fill/connect UX added): `PASS_PHASE9_AUDIT_ONLY`.
- `Docs/Architecture/manual-e2e-camera-crash-investigation.md` — Manual E2E.1: Camera frustum crash investigation, root cause hypothesis, fix design, runtime evidence, and validation plan. Classification: `PASS_CAMERA_FRUSTUM_GUARD_CODE_ONLY + ENV_RUNTIME_TICK_BLOCKED`.
- `Docs/Architecture/manual-e2e-log-hygiene.md` — Runtime log hygiene: stale backup log avoidance, timestamp-based active log detection, GUID-based filtering, tick-blocked packet classification rules.
