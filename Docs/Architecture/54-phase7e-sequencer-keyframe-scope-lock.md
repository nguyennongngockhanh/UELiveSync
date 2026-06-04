# Phase 7E — Sequencer + Keyframe Replication Scope Lock

**Date**: 2026-06-03
**Status**: IMPLEMENTED ✅ (2026-06-04)
**Depends on**: Phase 7B ✅ (Timeline Sync), Phase 7C ✅ (Playback Sync), Phase 7D ✅ (Active Camera Sync)
**Blocks**: Phase 7F (Sequencer Integration — Sequencer-driven playback control)
**Related Docs**: `Docs/Architecture/52-phase7-animation-sequencer-scope-lock.md`

---

## 1. Purpose

This document defines the scope, architecture, and implementation plan for replicating Blender animation keyframes into UE5 Sequencer via the LiveSync protocol. It establishes:

- How Blender FCurves map to UE Sequencer tracks and sections
- The wire format for keyframe data and sequencer control operations
- The boundary between what is implemented now vs deferred to Phase 7F
- The failure-mode model and acceptance criteria for each implementation stage

### 1.1 Relationship to Wider Phase 7

| Phase | Scope | Status |
|-------|-------|--------|
| Phase 7B | Timeline state sync (frame range, FPS, current frame) | ✅ IMPLEMENTED |
| Phase 7C | Playback state sync (play/pause/stop) | ✅ IMPLEMENTED |
| Phase 7D | Active camera sync (camera GUID) | ✅ IMPLEMENTED |
| **Phase 7E** | **Keyframe replication + sequencer asset creation** | **✅ IMPLEMENTED** |
| Phase 7F | Sequencer playback control (play/pause/stop from UE) | 🔒 PENDING |
| Phase 7G | Sequencer camera cut integration | 🔒 PENDING |

Phase 7E builds on the animation pipeline validated by Phases 7B–7D. It adds the ability to replicate Blender's animated keyframes (transform, visibility, camera properties) to UE's Sequencer system, creating a fully editable UE Level Sequence that mirrors the Blender scene's animation.

---

## 2. Blender Side Investigation

### 2.1 FCurve Architecture

Blender stores animation as **Actions** containing **FCurves**. Each FCurve maps a single property channel over time.

```
bpy.data.objects["Cube"]
  └── animation_data              # bpy.types.AnimData (None if no animation)
       └── action                 # bpy.types.Action
            └── fcurves           # bpy.types.FCurves (collection)
                 ├── [0] FCurve   # data_path="location",  array_index=0  → Location X
                 ├── [1] FCurve   # data_path="location",  array_index=1  → Location Y
                 ├── [2] FCurve   # data_path="location",  array_index=2  → Location Z
                 ├── [3] FCurve   # data_path="rotation_euler", array_index=0  → Euler X
                 ├── [4] FCurve   # data_path="rotation_quaternion", array_index=0  → Quat W
                 ├── ...
                 └── [N] FCurve   # data_path="hide_viewport", array_index=0  → Visibility
```

Each FCurve contains:
```python
fcurve = action.fcurves[i]
fcurve.data_path             # str, e.g. "location", "rotation_euler", "scale"
fcurve.array_index           # int, e.g. 0, 1, 2 for X, Y, Z (or -1 for non-array)
fcurve.keyframe_points       # bpy.types.KeyframePoints (collection)
fcurve.extrapolation         # 'CONSTANT', 'LINEAR', 'MAKE_LINEAR' (not used in copy)

for kp in fcurve.keyframe_points:
    kp.co                     # Vector((frame_float, value_float))
    kp.handle_left            # Vector((frame, value)) — left handle position
    kp.handle_right           # Vector((frame, value)) — right handle position
    kp.handle_left_type       # 'FREE', 'ALIGNED', 'VECTOR', 'AUTO', 'AUTO_CLAMPED'
    kp.handle_right_type      # 'FREE', 'ALIGNED', 'VECTOR', 'AUTO', 'AUTO_CLAMPED'
    kp.interpolation          # 'CONSTANT', 'LINEAR', 'BEZIER', 'SINE', 'QUAD', 'CUBIC',
                              # 'QUART', 'QUINT', 'EXPO', 'CIRC', 'BACK', 'BOUNCE', 'ELASTIC'
    kp.easing                 # 'EASE_IN', 'EASE_OUT', 'EASE_IN_OUT'
    kp.back                   # float — Back factor (for BACK interpolation)
    kp.amplitude              # float — Amplitude (for BOUNCE/ELASTIC)
    kp.periodicity            # float — Periodicity
```

### 2.2 Transform Channel to FCurve Mapping

| Property | `data_path` | `array_index` | Components | UE Sequencer Track |
|----------|-------------|---------------|------------|-------------------|
| Location X | `"location"` | 0 | float | `UMovieScene3DTransformTrack` |
| Location Y | `"location"` | 1 | float | (same track, channel 1) |
| Location Z | `"location"` | 2 | float | (same track, channel 2) |
| Rotation X (Euler) | `"rotation_euler"` | 0 | float | (same track, channel 3) |
| Rotation Y (Euler) | `"rotation_euler"` | 1 | float | (same track, channel 4) |
| Rotation Z (Euler) | `"rotation_euler"` | 2 | float | (same track, channel 5) |
| Rotation W (Quat) | `"rotation_quaternion"` | 0 | float | (same track, channel 3) |
| Rotation X (Quat) | `"rotation_quaternion"` | 1 | float | (same track, channel 4) |
| Rotation Y (Quat) | `"rotation_quaternion"` | 2 | float | (same track, channel 5) |
| Rotation Z (Quat) | `"rotation_quaternion"` | 3 | float | (same track, channel 6) |
| Scale X | `"scale"` | 0 | float | (same track, channel 7) |
| Scale Y | `"scale"` | 1 | float | (same track, channel 8) |
| Scale Z | `"scale"` | 2 | float | (same track, channel 9) |
| Viewport Visibility | `"hide_viewport"` | -1 | bool | `UMovieSceneBoolTrack` (property) |
| Render Visibility | `"hide_render"` | -1 | bool | `UMovieSceneBoolTrack` (property) |
| Camera Focal Length | `"data.lens"` | 0 | float | `UMovieSceneFloatTrack` (property) |
| Camera Sensor Width | `"data.sensor_width"` | 0 | float | `UMovieSceneFloatTrack` (property) |
| Camera Focus Distance | `"data.dof.focus_distance"` | 0 | float | `UMovieSceneFloatTrack` (property) |
| Camera F-Stop | `"data.dof.aperture_fstop"` | 0 | float | `UMovieSceneFloatTrack` (property) |

### 2.3 Rotation Mode Handling

Blender objects have a `rotation_mode` property that determines which FCurves exist:

| `rotation_mode` | FCurve `data_path` | Array Indices | Notes |
|----------------|--------------------|---------------|-------|
| `'QUATERNION'` | `"rotation_quaternion"` | 0, 1, 2, 3 (W, X, Y, Z) | Default for some objects; 4 channels |
| `'XYZ'`..`'ZYX'` | `"rotation_euler"` | 0, 1, 2 | 6 Euler orders; 3 channels per mode |
| `'AXIS_ANGLE'` | `"rotation_axis_angle"` | 0, 1, 2, 3 | Rarely used |

**Decision**: Blender's transform serialization (in `get_transform()`) always converts to quaternion. For keyframe replication, the rotation mode matters because we must create the correct channels on the UE transform track. **The UE transform section stores rotation as a quaternion** — we convert Euler FCurves to quaternion values at each keyframe for UE compatibility.

### 2.4 Existing Blender Detection Infrastructure

The current `sync.py` object iteration loop (in `check_updates()`) processes each tracked object for:

1. **Transform changes** — via `get_transform()` + `transforms_different()` threshold comparison
2. **Visibility changes** — via `obj.hide_get()` state comparison
3. **Rename changes** — via `obj.name` comparison
4. **Parent changes** — via `get_parent_guid()` comparison
5. **Material slot changes** — via `get_object_material_slots()`
6. **Mesh geometry changes** — via `extract_evaluated_mesh_data()`
7. **Collection membership changes** — via `obj.users_collection`

**No keyframe or FCurve detection code exists.** The Phase 7E detection block will:

1. Check `obj.animation_data is not None` and `obj.animation_data.action is not None`
2. Iterate `action.fcurves` to build a channel inventory
3. Compare against `_last_keyframe_state[guid]` to detect:
   - New animation on a previously unanimated object
   - New FCurve added (new data_path or array_index)
   - New keyframe added (count or frame set differs)
   - Keyframe value changed
   - Keyframe removed
4. Collect all current keyframe points for changed channels
5. Send as PT_Keyframe batch packets

### 2.5 Blender Detection Strategy (Snapshot vs Incremental)

Two approaches exist for replicating keyframes:

| Approach | Description | Suited for | Challenge |
|----------|-------------|------------|-----------|
| **Full snapshot** | Send ALL keyframes for ALL animated objects at scene start / on reconnect | Small scenes, initial sync | Large payload for complex scenes |
| **Incremental diff** | Send only changed FCurves or new/modified keyframes per tick | Ongoing sync | Complex diff logic, edge cases |
| **Hybrid** | Full snapshot on first connect / reconnect; incremental thereafter | Production use | Most complex to implement |

**Decision**: Use **full snapshot per-channel** approach:
- On connect/reconnect: iterate all tracked objects, send all FCurves for all animated objects
- Per tick: only send channels whose keyframe count, frame set, or values have changed
- Track per-channel hash (SHA-256 of serialized keyframe data) to detect changes efficiently

---

## 3. UE Sequencer Side Investigation

### 3.1 Core Sequencer Classes

| Class | Role | Key API for Phase 7E |
|-------|------|---------------------|
| `ULevelSequence` | Top-level asset representing a sequence | `ULevelSequence::CreateLevelSequence()` |
| `UMovieScene` | Time/model data container inside a LevelSequence | `GetMovieScene()`, `GetPlayRange()`, `SetFrameRate()` |
| `UMovieSceneSequencePlayer` | Runtime playback controller | Not used in Phase 7E (storage only) |
| `UMovieScene3DTransformTrack` | Transform animation track | `AddSection()`, `CreateTransformSection()` |
| `UMovieScene3DTransformSection` | Transform key data container | `GetChannel(0..8)`, `AddKey()`, `SetDefault()` |
| `UMovieSceneBoolTrack` | Boolean property track (visibility) | `AddSection()` |
| `UMovieSceneBoolSection` | Bool key data | `GetChannel()`, `AddKey()` |
| `UMovieSceneFloatTrack` | Float property track (camera props) | `AddSection()` |
| `UMovieScenePropertyTrack` | Generic property track | `SetPropertyNameAndPath()` |
| `UMovieSceneCameraCutTrack` | Camera cut track | `AddSection()`, `UMovieSceneCameraCutSection` |
| `UMovieSceneCameraCutSection` | Camera cut section with binding | `SetCameraGuid()`, `SetStartFrame()`, `SetEndFrame()` |

### 3.2 Creating a Level Sequence Programmatically

```cpp
// Create a new Level Sequence asset
UPackage* Package = CreatePackage(nullptr, TEXT("/Game/Sequence/LiveSyncSequence"));
ULevelSequence* LevelSequence = NewObject<ULevelSequence>(
    Package,
    ULevelSequence::StaticClass(),
    FName("LiveSyncSequence"),
    RF_Public | RF_Standalone
);
LevelSequence->Initialize();
// ... register with asset registry ...
```

### 3.3 Adding Possessables and Tracks

```cpp
UMovieScene* MovieScene = LevelSequence->GetMovieScene();
MovieScene->SetPlayRange(FFrameNumber(0), FFrameNumber(250));
MovieScene->SetFrameRate(FFrameRate(24, 1));

// Add possessable for actor
FGuid PossessableGuid = MovieScene->AddPossessable(
    TEXT("Cube"),
    AActor::StaticClass()
);
// Bind to GUID (this is set after actor is created/spawned)
LevelSequence->BindPossessableObject(
    PossessableGuid,
    *Actor,
    Actor->GetWorld()
);

// Add transform track
UMovieScene3DTransformTrack* TransformTrack = MovieScene->AddTrack(
    UMovieScene3DTransformTrack::StaticClass(),
    PossessableGuid
);

// Add section to transform track
UMovieScene3DTransformSection* Section = Cast<UMovieScene3DTransformSection>(
    TransformTrack->CreateNewSection()
);
Section->SetRange(TRange<FFrameNumber>(FFrameNumber(0), FFrameNumber(250)));
TransformTrack->AddSection(*Section);

// Add key to transform channel
// Channel 0-2: Location X,Y,Z (3 doubles)
// Channel 3-5: Rotation Roll,Pitch,Yaw (3 doubles)
// Channel 6-8: Scale X,Y,Z (3 doubles)
FMovieSceneDoubleChannel& ChannelX = Section->GetChannel(0);
ChannelX.AddCubicKey(FFrameNumber(0), 100.0);
ChannelX.AddCubicKey(FFrameNumber(100), 500.0);
```

### 3.4 Visibility Bool Track

```cpp
UMovieSceneBoolTrack* VisibilityTrack = MovieScene->AddTrack(
    UMovieSceneBoolTrack::StaticClass(),
    PossessableGuid
);
// Set property path
VisibilityTrack->SetPropertyNameAndPath(
    TEXT("bHidden"),
    TEXT("bHidden")
);
UMovieSceneBoolSection* VisSection = Cast<UMovieSceneBoolSection>(
    VisibilityTrack->CreateNewSection()
);
VisSection->SetRange(TRange<FFrameNumber>(FFrameNumber(0), FFrameNumber(250)));
VisSection->GetChannel().AddKey(FFrameNumber(0), true);
VisSection->GetChannel().AddKey(FFrameNumber(50), false);
VisibilityTrack->AddSection(*VisSection);
```

### 3.5 Camera Cut Track

```cpp
UMovieSceneCameraCutTrack* CutTrack = MovieScene->AddTrack(
    UMovieSceneCameraCutTrack::StaticClass(),
    MovieScene->GetPossessable(0).GetGuid()  // Master track
);

UMovieSceneCameraCutSection* CutSection = Cast<UMovieSceneCameraCutSection>(
    CutTrack->CreateNewSection()
);
CutSection->SetStartFrame(FFrameNumber(0));
CutSection->SetEndFrame(FFrameNumber(100));
CutSection->SetCameraGuid(PossessableGuidForCamera);  // FGuid from AddPossessable
CutTrack->AddSection(*CutSection);
```

### 3.6 No Existing Sequencer Code in Plugin

The UELiveSync plugin currently contains **zero** Sequencer API usage:
- No `LevelSequence`, `MovieScene`, `Sequencer` includes
- No `UMovieScene*` references
- No `FMovieScene*` types
- All Sequencer references are in comments stating "NOT implemented" or "storage only"

**Phase 7E will add `LevelSequence`, `MovieScene`, and `Sequencer` dependencies to `UELiveSync.Build.cs`.**

---

## 4. Scope Definition

### 4.1 In-Scope

| Feature | Direction | Description |
|---------|-----------|-------------|
| **LevelSequence creation** | Blender → UE | Create/update a single Level Sequence asset mirroring the Blender scene animation |
| **Object possessable binding** | Blender → UE | Each animated Blender object gets a possessable in the Level Sequence, bound by GUID to the UE actor |
| **Transform keyframes** | Blender → UE | Location, rotation (as quaternion), and scale keyframe values for each animated object |
| **Visibility keyframes** | Blender → UE | Viewport visibility (`bHidden` property track) keyframes |
| **Camera property keyframes** | Blender → UE | Focal length, sensor width, focus distance, f-stop keyframes on camera objects |
| **Camera cut sections** | Blender → UE | Camera cut track sections matching Blender's active camera changes over time |
| **Frame range sync** | Blender → UE | Level Sequence play range set from Blender's `frame_start` / `frame_end` |
| **Frame rate sync** | Blender → UE | Level Sequence frame rate set from Blender's FPS |
| **Full snapshot on connect** | Blender → UE | All keyframes sent in batch on initial connection or reconnection |
| **Incremental update** | Blender → UE | Changed keyframes sent per tick; stable channels skipped |
| **Interpolation preservation** | Blender → UE | Bézier handle positions and interpolation type carried over wire for UE cubic key support |  |
| **Capability gating** | Both | New capability bits for Phase 7E features, following same pattern as Phase 7D |

### 4.2 Explicitly Out of Scope

| Feature | Rationale |
|---------|-----------|
| Skeletal animation / bones | Skeletal animation uses NLA/Armature, different data model; deferred to Phase 8+ |
| NLA tracks | NLA is a Blender-only concept; not representable in UE Sequencer directly |
| Drivers | Blender driver expressions have no UE equivalent |
| Shape keys | Shape key animation is mesh-level, not Sequencer-level; deferred |
| FCurve modifiers | `ENVELOPE`, `CYCLES`, `NOISE`, `LIMITS`, `STEPPED`, etc. — no UE equivalent |
| Physics simulations | No Sequencer representation |
| Particle systems | No Sequencer representation |
| Constraints | Blender constraints have no built-in UE Sequencer equivalent |
| Sequencer playback control | Phase 7F: playing/pausing/stopping UE Sequencer from Blender transport |
| Sequencer editor UI integration | Displaying LiveSync sequences in UE Sequencer editor is deferred |
| Multiple Level Sequences | Only one "Live Sync Sequence" is maintained |
| Runtime (non-editor) sequence playback | Phase 7E targets `WITH_EDITOR` only, like Phase 7D Stage 4 viewport apply |
| UE→Blender reverse sync | Unidirectional only: Blender→UE |
| UE-side keyframe editing | UE editor keyframe changes are not synced back to Blender |
| Audio tracks | Not relevant to object animation |
| Event tracks | Not relevant to object animation |
| Director tracks / cut tracks | Director blueprint tracks excluded; camera cuts handled as time-ranged sections |

### 4.3 Deferred To Phase 7F

| Feature | Rationale | Target |
|---------|-----------|--------|
| Play/pause/stop UE Sequencer from Blender transport | Requires transport event API integration | Phase 7F |
| Sequencer → Blender playback state sync | Reverse direction | Phase 7F |
| Loop mode sync | Requires UE Sequencer loop settings | Phase 7F |
| Real-time frame scrubbing | High bandwidth, low latency requirement | Phase 7F |

---

## 5. Semantic Model

### 5.1 Ownership Model

| Item | Owner | Direction |
|------|-------|-----------|
| Level Sequence asset | UE | Created on UE side via Blender commands |
| Object possessable bindings | UE | Created per-animated-object; bound by GUID |
| Keyframe data | Blender | Source of truth; pushed to UE on change |
| Frame range | Blender | `scene.frame_start` / `frame_end` pushed via Phase 7B timeline sync |
| Frame rate | Blender | `render.fps` / `fps_base` pushed via Phase 7B |
| Camera cuts | Blender | Timeline of active camera changes → camera cut sections |

### 5.2 Event-Driven vs State-Driven

| Operation | Model | Reasoning |
|-----------|-------|-----------|
| LevelSequence creation | Event-driven | Created once on connect/reconnect, not on every tick |
| Object binding | Event-driven | Possessable added when an object becomes animated, not every frame |
| Keyframe batch | State-driven | Full keyframe snapshot sent, then incremental deltas |
| Frame range update | State-driven | Updated when Blender timeline changes (via Phase 7B) |
| Camera cut addition | Event-driven | One PT_SequencerOp sent per camera transition |

### 5.3 Replay Semantics

Keyframe data uses the same replay infrastructure as Phase 6 semantic events:

- Each PT_Keyframe and PT_SequencerOp packet carries a monotonic sequence number
- Replay records packets into an in-memory log
- On reconnect: replay log replays all keyframe and sequencer ops to reconstruct the UE scene
- After replay: sends a fresh full snapshot to ensure consistency

**Decision**: Phase 7E does NOT implement replay initially. Replay will be added in Phase 7E validation stage following the same pattern as Phase 6B.

### 5.4 Duplicate Suppression

| Channel | Suppression Strategy |
|---------|---------------------|
| Same keyframe values at same frame | Suppressed — per-channel hash comparison |
| Unchanged object (no new keyframes, no modified values) | Suppressed — `_last_keyframe_hash[guid]` unchanged |
| First tick after connect | Always send full snapshot |
| Reconnect | Clears `_last_keyframe_hash`, forces full resend |

### 5.5 Reconnect Behavior

1. Blender reconnects to UE
2. On `start_sync()`: reset per-object `_last_keyframe_hash` to empty
3. On first `check_updates()` tick: detect ALL animated objects, send FULL keyframe snapshot
4. UE handles: clear existing Level Sequence, recreate from scratch (or detach/reattach sections)
5. After initial snapshot: resume incremental mode

### 5.6 Conflict Resolution

| Scenario | Resolution |
|----------|------------|
| Blender connects, UE already has stale sequence | Recreate — full snapshot overwrites |
| Keyframe for unknown possessable | Drop packet, log warning, increment `StaleRejections` counter |
| Frame out of sequence bounds | Clamp to `[frame_start, frame_end]` |
| Interpolation type not supported by UE | Fall back to `LINEAR` (most compatible) |

---

## 6. Packet Proposal

### 6.1 PT_Keyframe (0x17) — Keyframe Data

#### Packet Type Allocation

| Packet | Value | Payload Type | Status |
|--------|-------|-------------|--------|
| PT_Keyframe | `0x17` | Variable — batch of keyframe entries | 🔒 PROPOSED |
| PT_SequencerOp | `0x18` | Variable — opcode + name payload | 🔒 PROPOSED |

**Rationale for variable-length payload**: Keyframe data is inherently variable — each FCurve has a different number of keyframes, different interpolation types, and different handle configurations. A fixed-size payload would either waste bandwidth (too large for simple curves) or truncate data (too small for dense curves).

#### Per-Object Batch Header

```
PT_Keyframe packet layout (variable length, per-channel batching):
[0-15]   ObjectGUID         bytes(16)  — GUID of the animated Blender object
[16-19]  ChannelCount       uint32     — Number of FCurve channels in this packet
[20-N]   Channel entries    variable   — One per FCurve
```

#### Per-Channel Entry

```
Channel entry layout:
[0-3]    data_path_len      uint8      — Length of data_path string (max 64)
[4-N]    data_path          bytes      — data_path string (e.g. "location", "rotation_quaternion")
[N+0]    array_index        int32      — FCurve array index (0-3, or -1 for non-array)
[N+4]    keyframe_count     uint32     — Number of keyframe points in this channel
[N+8]    keyframe_entries   variable   — One per keyframe point
```

#### Per-Keyframe Entry

```
Keyframe entry layout (24 bytes fixed + 0-48 bytes variable for handles):
[0-3]    frame              float32    — Frame number (float, e.g. 1.0, 2.5)
[4-7]    value              float32    — Keyframe value (e.g. location X in cm)
[8-11]   interpolation      uint8      — Interpolation type enum
[12]     easing             uint8      — Easing type enum
[13-15]  handle_flags       uint8[3]   — Reserved: handle type flags
[16-23]  handle_left_frame  double     — Left handle frame (only if BEZIER)
[24-31]  handle_left_value  double     — Left handle value (only if BEZIER)
[32-39]  handle_right_frame double     — Right handle frame (only if BEZIER)
[40-47]  handle_right_value double     — Right handle value (only if BEZIER)

Total: 24 bytes fixed, 48 bytes with Bézier handles
```

#### Interpolation Type Enum

| Value | Blender Constant | UE Equivalent | Notes |
|-------|-----------------|---------------|-------|
| 0 | `CONSTANT` | `ERichCurveInterpMode::RCIM_Constant` | Step function |
| 1 | `LINEAR` | `ERichCurveInterpMode::RCIM_Linear` | Linear interpolation |
| 2 | `BEZIER` | `ERichCurveInterpMode::RCIM_Cubic` | Cubic (Bézier) with handles |
| 3 | `SINE` | `RCIM_Cubic` (approximate) | No direct UE equivalent |
| 4 | `QUAD` | `RCIM_Cubic` (approximate) | No direct UE equivalent |
| 5 | `CUBIC` | `RCIM_Cubic` (approximate) | No direct UE equivalent |
| 6 | `EXPO` | `RCIM_Cubic` (approximate) | No direct UE equivalent |
| 7 | `CIRC` | `RCIM_Cubic` (approximate) | No direct UE equivalent |

**All non-LINEAR/BEZIER interpolations are approximated as Bézier** on the UE side, or fall back to LINEAR if conversion is not feasible. The interpolation type is preserved in the packet for diagnostics even when UE approximates it.

#### Maximum Batch Size

| Parameter | Value | Rationale |
|-----------|-------|-----------|
| Max channels per packet | 64 | Covers all possible FCurves on a single object (7 channels × 3-4 indices) |
| Max keyframes per channel | 1024 | Protects against pathological curves; >1024 split across multiple packets |
| Max batch payload | 480KB | Within LIVE_SYNC_MAX_PACKET_SIZE (524288) with safety margin |
| Max objects per tick | Unlimited | Each object gets its own batch(es); multiple packets per tick OK |

### 6.2 PT_SequencerOp (0x18) — Sequencer Control Operations

```
PT_SequencerOp packet layout (variable length):
[0-3]    opcode             uint32     — Operation type enum
[4-7]    sequence_number    uint32     — Monotonic sequencer operation sequence
[8-11]   payload_size       uint32     — Size of operation-specific payload
[12-N]   payload            variable   — Operation-specific data
```

#### SequencerOp Opcodes

| Opcode | Name | Payload | Description |
|--------|------|---------|-------------|
| 0 | `CREATE_SEQUENCE` | sequence_name(string) + frame_start(int32) + frame_end(int32) + fps_num(int32) + fps_den(int32) | Create or clear the Level Sequence asset |
| 1 | `ADD_POSSESSABLE` | object_guid(bytes16) + object_name(string) + actor_type(uint8) | Add possessable binding for a tracked object |
| 2 | `REMOVE_POSSESSABLE` | object_guid(bytes16) | Remove possessable binding (object deleted) |
| 3 | `ADD_CAMERA_CUT` | camera_guid(bytes16) + start_frame(int32) + end_frame(int32) | Create camera cut section for the given frame range |
| 4 | `CLEAR_SEQUENCE` | (none) | Clear all tracks, sections, and possessables but keep sequence asset |
| 5 | `SET_FRAME_RANGE` | frame_start(int32) + frame_end(int32) | Update the sequence play range |

### 6.3 Batching Strategy

| Scenario | Batch Size | Packets Per Tick |
|----------|-----------|-----------------|
| Initial connect (simple scene, 5 objects, 50 keyframes total) | 1 × PT_SequencerOp (create) + 5 × PT_SequencerOp (bind) + 1 × PT_Keyframe | 7 packets |
| Initial connect (complex scene, 50 objects, 5000 keyframes total) | 1 + 50 + multiple PT_Keyframe batches | ~10-20 packets |
| Incremental tick (no changes) | 0 | 0 packets |
| Incremental tick (1 object, 1 keyframe modified) | 1 × PT_Keyframe | 1 packet |
| Camera cut during playback | 1 × PT_SequencerOp (add camera cut) | 1 packet |

### 6.4 Sequence / Replay Model

**Replay not implemented in initial Phase 7E stages.** The replay model (following Phase 6B patterns) will be added during Stage 8 (Validation):

| Property | Value |
|----------|-------|
| Sequence tracker | `GKeyframeSequences` — `TMap<FGuid, uint32>` per object |
| Replay log | `FKeyframeReplayStream` — `TArray<FSerializedKeyframePacket>` |
| ConsoleReset behavior | Clears replay log, sends `CLEAR_SEQUENCE` op, reconnects |
| Max replay entries | Configurable (default 10,000) |

### 6.5 Capability Gating

Following the same pattern as Phase 7D (capability announce/response):

| Capability Bit | Value | Description |
|---------------|-------|-------------|
| `CAP_SUPPORTS_KEYFRAME_REPLICATION` | `0x01` (Bit 0) | Blender announces; UE must respond with same bit for Blender to send keyframe data |
| `CAP_SUPPORTS_SEQUENCER_OPS` | `0x02` (Bit 1) | Blender announces; UE must respond with bit for operation commands |

**Decision**: Use two separate capability bits so that keyframe data and sequencer operations can be gated independently. A UE plugin that can receive keyframes but not create sequences (if running without editor) would only set `0x01`.

The `CAP_SUPPORTS_TIMELINE_SYNC` bit remains `0x10` — no overlap.

#### Updated Capability Bit Allocation

| Bit | Constant | Phase | Status |
|-----|----------|-------|--------|
| Bit 0 | `CAP_SUPPORTS_KEYFRAME_REPLICATION` | 7E | 🔒 PROPOSED |
| Bit 1 | `CAP_SUPPORTS_SEQUENCER_OPS` | 7E | 🔒 PROPOSED |
| Bit 4 | `CAP_SUPPORTS_TIMELINE_SYNC` | 7B | ✅ EXISTING |
| Bit 6 | `CAP_SUPPORTS_ACTIVE_CAMERA_SYNC` | 7D | ✅ EXISTING |

---

## 7. Failure-Mode Analysis

### 7.1 Failure Mode Table

| # | Failure Mode | Symptom | Severity | Detection | Mitigation |
|---|-------------|---------|----------|-----------|------------|
| F1 | Blender object has no UE counterpart (not yet created / deleted) | Possessable bind fails | Medium | `MissingActor` counter | Log warning, skip binding, retry on next update |
| F2 | Keyframe frame exceeds sequence range | Keyframe outside play range | Low | Clamp to range | Clamp FFrameNumber to `[frame_start, frame_end]`, log verbose |
| F3 | FCurve interpolation type not supported by UE | Interpolation approximated or falls back to LINEAR | Low | `InterpolationFallback` counter | Always send original type in packet; fall back gracefully |
| F4 | Bézier handle data lost/corrupted | Key shape differs from Blender | Low | Handle validation | Skip handle data on corruption, fall back to LINEAR |
| F5 | LevelSequence creation fails (package name conflict) | Empty sequence | High | `CreateSequenceFailed` counter | Retry with unique name suffix |
| F6 | Camera cut with unknown camera GUID | No camera cut added to track | Medium | `UnknownCameraGUID` counter | Log warning, skip cut section |
| F7 | Massive keyframe count on single object (>1024) | Packet too large | Medium | Split across packets | Enforce 1024 keyframes-per-channel max, send remainder in follow-up packets |
| F8 | Object deleted during active animation | Orphaned possessable in sequence | Medium | `OrphanedPossessable` counter | Clean up on `PT_Delete` / `PT_Delete_V5` receipt; remove possessable via `REMOVE_POSSESSABLE` op |
| F9 | Reconnect during keyframe transmission | Incomplete sequence | High | Full snapshot on reconnect | On reconnect: clear sequence, send full snapshot from scratch |

### 7.2 Diagnostic Counters

| Counter | Type | Read Method | Clear Method | Purpose |
|---------|------|-------------|--------------|---------|
| `KeyframePacketsReceived` | `std::atomic<int32>` | `Stats.KeyframePacketsReceived` | ConsoleReset | Total PT_Keyframe packets received |
| `KeyframePacketsApplied` | `std::atomic<int32>` | `Stats.KeyframePacketsApplied` | ConsoleReset | Packets with valid GUID + channel data |
| `KeyframePacketsStale` | `std::atomic<int32>` | `Stats.KeyframePacketsStale` | ConsoleReset | Packets rejected (stale/duplicate sequence) |
| `KeyframePacketsMalformed` | `std::atomic<int32>` | `Stats.KeyframePacketsMalformed` | ConsoleReset | Packets rejected (bad size or data_path) |
| `KeyframeKeysInserted` | `std::atomic<int32>` | `Stats.KeyframeKeysInserted` | ConsoleReset | Individual keyframe points added to sections |
| `SequencerOpsReceived` | `std::atomic<int32>` | `Stats.SequencerOpsReceived` | ConsoleReset | Total PT_SequencerOp packets received |
| `SequencerOpsApplied` | `std::atomic<int32>` | `Stats.SequencerOpsApplied` | ConsoleReset | Operations executed successfully |
| `SequencerOpsFailed` | `std::atomic<int32>` | `Stats.SequencerOpsFailed` | ConsoleReset | Operations that failed (e.g. sequence creation) |
| `PossessablesAdded` | `std::atomic<int32>` | `Stats.PossessablesAdded` | ConsoleReset | Tracks added to Level Sequence |
| `CameraCutsAdded` | `std::atomic<int32>` | `Stats.CameraCutsAdded` | ConsoleReset | Camera cut sections added |
| `InterpolationFallback` | `std::atomic<int32>` | `Stats.InterpolationFallback` | ConsoleReset | Keyframes where interpolation fell back to LINEAR |
| `MissingActorBindings` | `std::atomic<int32>` | `Stats.MissingActorBindings` | ConsoleReset | Possessable bind attempts for unknown actors |

### 7.3 Blender-Side Failure Modes

| # | Failure Mode | Detection | Mitigation |
|---|-------------|-----------|------------|
| B1 | `obj.animation_data` is None (object not animated) | `animation_data is None` check | Skip silently |
| B2 | `action.fcurves` is empty | `len(fcurves) == 0` | Skip silently |
| B3 | Keyframe with NaN/inf value | `math.isnan()` / `math.isinf()` check | Skip keyframe, log warning, increment `MalformedPackets` |
| B4 | Object not in `tracked_objects` (non-MESH type) | Not in `tracked_objects` dict | Skip (keyframe replication limited to tracked MESH objects initially) |

### 7.4 Recovery Paths

| Failure | Immediate Action | Recovery |
|---------|-----------------|----------|
| Sequence creation fails | Skip all subsequent packets for this session | User triggers ConsoleReset or restarts sync |
| Possessable binding fails | Log warning, skip per-object keyframes | Retry on next reconnect |
| Keyframe insertion fails | Skip individual keyframe, continue batch | Full snapshot on reconnect |
| Camera cut fails | Skip cut section, continue sequence | User triggers ConsoleReset to rebuild |

---

## 8. Acceptance Criteria

### 8.1 Wire Format (Stage 2)

| # | Criterion | Verification |
|---|-----------|-------------|
| AC1 | `PT_Keyframe = 0x17` defined in both Blender `network.py` and UE `SyncTypes.h` | Static constant check |
| AC2 | `PT_SequencerOp = 0x18` defined in both sides | Static constant check |
| AC3 | Per-channel keyframe batch header parses correctly | Round-trip test: serialize → parse → compare fields |
| AC4 | Per-keyframe entry (24-byte fixed + optional 24-byte handles) serializes and deserializes correctly | Unit test for each field: frame, value, interpolation, easing, handles |
| AC5 | All 8 interpolation types round-trip through wire format | Enum value test (CONSTANT=0 through CIRC=7) |
| AC6 | `MAX_CHANNELS_PER_PACKET=64` enforced on parse | Reject >64 channel entries |
| AC7 | `MAX_KEYFRAMES_PER_CHANNEL=1024` enforced on serialize | Auto-split channels exceeding limit |
| AC8 | Bézier handle data only included when interpolation=BEZIER; omitted for CONSTANT/LINEAR | Payload size varies correctly |
| AC9 | `data_path` strings truncated to 64 bytes | Strings >64 bytes rejected as malformed |
| AC10 | Protocol signature FNV hash includes `0x17` size, `0x18` size, 0x17, 0x18 | Cross-check signatures match |
| AC11 | `CAP_SUPPORTS_KEYFRAME_REPLICATION = 0x01` and `CAP_SUPPORTS_SEQUENCER_OPS = 0x02` defined | Static constant check |

### 8.2 SequencerOp Format (Stage 2)

| # | Criterion | Verification |
|---|-----------|-------------|
| AC12 | `CREATE_SEQUENCE` opcode=0 payload parses correctly | Round-trip: opcode + name + range + fps |
| AC13 | `ADD_POSSESSABLE` opcode=1 payload parses correctly | Round-trip: object_guid + name + actor_type |
| AC14 | `REMOVE_POSSESSABLE` opcode=2 payload parses correctly | Round-trip: object_guid |
| AC15 | `ADD_CAMERA_CUT` opcode=3 payload parses correctly | Round-trip: camera_guid + frame range |
| AC16 | `CLEAR_SEQUENCE` opcode=4 payload (empty) parses correctly | Zero-length payload accepted |
| AC17 | `SET_FRAME_RANGE` opcode=5 payload parses correctly | Round-trip: frame_start + frame_end |
| AC18 | Unknown opcode (>5) rejected as malformed | `SequencerOpsFailed` counter incremented |

### 8.3 Blender Keyframe Extraction (Stage 3)

| # | Criterion | Verification |
|---|-----------|-------------|
| AC19 | Objects with `animation_data` and `action` are detected as animated | Unit test with mocked object |
| AC20 | Objects without `animation_data` are skipped | Unit test with non-animated object |
| AC21 | `location` FCurves (3 indices) extracted as transform channel | Compare keyframe count per index |
| AC22 | `rotation_quaternion` FCurves (4 indices) extracted correctly | Handle `rotation_mode == 'QUATERNION'` |
| AC23 | `rotation_euler` FCurves (3 indices) converted to quaternion values at each keyframe | Unit test: Euler → Quat conversion at each keyframe |
| AC24 | `scale` FCurves (3 indices) extracted correctly | Compare keyframe values |
| AC25 | `hide_viewport` FCurves extracted as boolean visibility channel | Bool value at each keyframe |
| AC26 | Camera property FCurves (data.lens, etc.) extracted on camera objects | Filtered by object type |
| AC27 | Per-channel SHA-256 hash computed from serialized keyframe data | Hash changes when keyframe added/modified/removed |
| AC28 | Same hash → same channel suppressed (no duplicate send) | Verify reduced bandwidth on stable animation |
| AC29 | First tick sends full snapshot for all animated objects | All channels sent regardless of hash state |
| AC30 | Reconnect clears hash cache, forces full resend | Verify hash reset in `start_sync()` |

### 8.4 UE Sequence Creation (Stage 4)

| # | Criterion | Verification |
|---|-----------|-------------|
| AC31 | `CREATE_SEQUENCE` op creates a `ULevelSequence` asset | `IsValid()` on created sequence |
| AC32 | Sequence play range set from Blender's `frame_start`/`frame_end` | `MovieScene->GetPlayRange()` matches |
| AC33 | Sequence frame rate set from Blender's FPS | `MovieScene->GetFrameRate()` matches |
| AC34 | `ADD_POSSESSABLE` adds possessable with correct object name and class | `MovieScene->GetPossessableCount()` returns correct count |
| AC35 | Possessable is bound to actor by GUID (if actor in ActorCache) | `ULevelSequence::FindPossessableObjectId()` returns valid binding |
| AC36 | `REMOVE_POSSESSABLE` removes possessable and all its tracks | Possessable count decreases; tracks removed |
| AC37 | `CLEAR_SEQUENCE` removes all tracks and sections but retains sequence | Verify empty sequence after clear |
| AC38 | `SET_FRAME_RANGE` updates existing sequence play range | `GetPlayRange()` matches request |

### 8.5 Transform Key Insertion (Stage 5)

| # | Criterion | Verification |
|---|-----------|-------------|
| AC39 | Location X/Y/Z keyframes inserted into transform track section channels 0-2 | `FMovieSceneDoubleChannel` key count matches |
| AC40 | Rotation (quaternion) converted to Euler for UE transform section channels 3-5 | Verify conversion math: Quat → Roll/Pitch/Yaw |
| AC41 | Scale X/Y/Z keyframes inserted into transform track section channels 6-8 | Key values match after UE cm → Blender m scaling |
| AC42 | Bézier handle data applied to cubic keys (when interpolation=BEZIER) | `GetCubicKey()` handle positions match sent data |
| AC43 | LINEAR interpolation keys added as linear keys | Key interpolation mode is `RCIM_Linear` |
| AC44 | CONSTANT interpolation keys added as constant keys | Key interpolation mode is `RCIM_Constant` |
| AC45 | Handles omitted for non-BEZIER keys (zero-initialized, not applied) | Verify handle data ignored for LINEAR/CONSTANT |
| AC46 | Frame number clamped to `[frame_start, frame_end]` | Out-of-range keys clamped, not rejected |
| AC47 | Multiple batch packets for same object append to same section | Verify cumulative key count |

### 8.6 Camera Cuts (Stage 6)

| # | Criterion | Verification |
|---|-----------|-------------|
| AC48 | First `ADD_CAMERA_CUT` op creates `UMovieSceneCameraCutTrack` if not existing | Track exists after op |
| AC49 | `ADD_CAMERA_CUT` adds `UMovieSceneCameraCutSection` with correct camera GUID binding | `Section->GetCameraGuid()` matches |
| AC50 | Camera cut section start/end frame matches Blender's active camera transition timeline | `Section->GetRange()` matches expected |
| AC51 | Camera cut for unknown camera GUID logged and skipped (not fatal) | `UnknownCameraGUID` counter incremented |

### 8.7 Validation & Regression (Stage 7)

| # | Criterion | Verification |
|---|-----------|-------------|
| AC52 | Full standalone test suite for PT_Keyframe wire format: 30+ tests | `tests/phase7e_keyframe_wire.py` |
| AC53 | Full standalone test suite for PT_SequencerOp wire format: 20+ tests | `tests/phase7e_sequencer_op_wire.py` |
| AC54 | Full standalone test suite for Blender extraction: 20+ tests | `tests/phase7e_keyframe_extraction.py` |
| AC55 | Full standalone test suite for UE handler simulation: 30+ tests | `tests/phase7e_ue_handler_validation.py` |
| AC56 | Phase 7C Playback regression: 136/136 tests PASS | No regressions |
| AC57 | Phase 7D Camera regression: 364/364 tests PASS | No regressions |
| AC58 | Phase 7B Timeline regression: 44/44 tests PASS | No regressions |
| AC59 | Total standalone: 100+ tests PASS | Cumulative |

---

## 9. Implementation Stages

### Stage 0 — Scope Lock (THIS DOCUMENT)

| Step | Description | Deliverable |
|------|-------------|-------------|
| 0.1 | Investigate Blender FCurve API | Research doc (above) |
| 0.2 | Investigate UE Sequencer API | Research doc (above) |
| 0.3 | Define scope, exclusions, packet strategy | This document |
| 0.4 | Define failure-mode matrix | Section 7 |
| 0.5 | Define acceptance criteria | Section 8 |
| 0.6 | Define implementation stages | Section 9 |

**Validation gate**: Document reviewed and approved. Zero source files modified.

### Stage 1 — Audit Only (No code changes)

| Step | Description | Deliverable |
|------|-------------|-------------|
| 1.1 | Identify required UE classes and their module locations | Class inventory |
| 1.2 | Determine Level Sequence creation, possessable binding, track/key insertion APIs | API workflow |
| 1.3 | Determine module dependencies needed in `Build.cs` | Dependency list |
| 1.4 | Identify existing UELiveSync code reusable for Sequencer integration | Reuse analysis |
| 1.5 | Identify GUID lookup path compatibility | GUID path audit |
| 1.6 | Identify risks | Risk table |

**Validation gate**: Audit report below. No source files modified.

---

### Stage 1 — Unreal Sequencer Audit Report

#### 1. Required UE Classes

| Class | Header | Module | Purpose |
|-------|--------|--------|---------|
| `ULevelSequence` | `Runtime/LevelSequence/Public/LevelSequence.h` | `LevelSequence` | Top-level sequence asset; holds MovieScene data, possessable bindings |
| `UMovieScene` | `Runtime/MovieScene/Public/MovieScene.h` | `MovieScene` | Time/model data container; manages possessables, tracks, bindings, frame range |
| `UMovieSceneTrack` | `Runtime/MovieScene/Public/MovieSceneTrack.h` | `MovieScene` | Abstract base for all tracks (transform, bool, camera cut, property) |
| `UMovieSceneSection` | `Runtime/MovieScene/Public/MovieSceneSection.h` | `MovieScene` | Abstract base for all sections; holds time range and channel data |
| `UMovieScene3DTransformTrack` | `Runtime/MovieSceneTracks/Public/Tracks/MovieScene3DTransformTrack.h` | `MovieSceneTracks` | Transform animation track (inherits `UMovieScenePropertyTrack`) |
| `UMovieScene3DTransformSection` | `Runtime/MovieSceneTracks/Public/Sections/MovieScene3DTransformSection.h` | `MovieSceneTracks` | Transform section containing 9 `FMovieSceneDoubleChannel`s and weight channel |
| `UMovieSceneBoolTrack` | `Runtime/MovieSceneTracks/Public/Tracks/MovieSceneBoolTrack.h` | `MovieSceneTracks` | Boolean property track (e.g. visibility `bHidden`) |
| `UMovieSceneBoolSection` | `Runtime/MovieScene/Public/Sections/MovieSceneBoolSection.h` | `MovieScene` | Bool section containing `FMovieSceneBoolChannel` |
| `UMovieSceneCameraCutTrack` | `Runtime/MovieSceneTracks/Public/Tracks/MovieSceneCameraCutTrack.h` | `MovieSceneTracks` | Camera cut track (master track, unbound) |
| `UMovieSceneCameraCutSection` | `Runtime/MovieSceneTracks/Public/Sections/MovieSceneCameraCutSection.h` | `MovieSceneTracks` | Camera cut section with camera binding ID and time range |
| `UMovieScenePropertyTrack` | `Runtime/MovieSceneTracks/Public/Tracks/MovieScenePropertyTrack.h` | `MovieSceneTracks` | Base for property-driven tracks; provides `SetPropertyNameAndPath()` |
| `FMovieSceneDoubleChannel` | `Runtime/MovieScene/Public/Channels/MovieSceneDoubleChannel.h` | `MovieScene` | Double keyframe channel used by transform sections (X/Y/Z loc, rot, scale) |
| `FMovieSceneBoolChannel` | `Runtime/MovieScene/Public/Channels/MovieSceneBoolChannel.h` | `MovieScene` | Bool keyframe channel used by bool sections |
| `FMovieScenePossessable` | `Runtime/MovieScene/Public/MovieScenePossessable.h` | `MovieScene` | Possessable data: name, class, GUID, parent GUID, tags |
| `FMovieSceneBinding` | `Runtime/MovieScene/Public/MovieSceneBinding.h` | `MovieScene` | Binding linking a possessable GUID to its tracks |
| `FMovieSceneObjectBindingID` | `Runtime/MovieScene/Public/MovieSceneObjectBindingID.h` | `MovieScene` | Binding ID used by camera cut sections to reference camera possessable |

**Key finding**: All required classes are in **Runtime** modules — not Editor modules. This means Sequence creation, possessable management, track/section creation, and key insertion are all available in non-editor builds. The `MovieScene` module contains the fundamental channel types and section base classes; `MovieSceneTracks` contains the concrete transform, bool, and camera cut implementations; `LevelSequence` orchestrates the top-level asset.

---

#### 2. Minimal Sequence Creation Path

```cpp
// PREREQUISITE: Modules LevelSequence, MovieScene, MovieSceneTracks in Build.cs

#include "LevelSequence.h"
#include "MovieScene.h"
#include "Tracks/MovieScene3DTransformTrack.h"
#include "Sections/MovieScene3DTransformSection.h"
#include "Channels/MovieSceneDoubleChannel.h"

// --- Step 1: Create Level Sequence ---
// Outer must be a UPackage or UObject that persists the asset.
// Use RF_Public | RF_Standalone to make the asset visible in Content Browser.
ULevelSequence* Sequence = NewObject<ULevelSequence>(
    GetTransientPackage(),              // Or CreatePackage() for persistent asset
    NAME_None,
    RF_Public | RF_Standalone
);
Sequence->Initialize();

// --- Step 2: Configure MovieScene ---
UMovieScene* MovieScene = Sequence->GetMovieScene();
if (!MovieScene)
{
    // error: Initialize() should have created MovieScene
    return;
}

// Set frame rate from Blender FPS (e.g. 24fps)
MovieScene->SetDisplayRate(FFrameRate(24, 1));
// Set tick resolution (e.g. 24000 fps for sub-frame precision)
MovieScene->SetTickResolutionDirectly(FFrameRate(24000, 1));
// Set play range from Blender frame_start / frame_end
MovieScene->SetPlaybackRange(FFrameNumber(0), FFrameNumber(250));

// --- Step 3: Add Possessable ---
// AddPossessable returns an FGuid that identifies the binding.
FGuid ObjectGuid = MovieScene->AddPossessable(
    TEXT("Cube"),             // Name matching Blender object name
    AActor::StaticClass()     // Actor class (all synced actors are AActor-derived)
);

// --- Step 4: Bind Possessable to Actor by GUID ---
// Use FindActorFast(BlenderGUID) to get the AActor* from ActorCache.
AActor* Actor = FindActorFast(BlenderGUID);
if (Actor)
{
    Sequence->BindPossessableObject(
        ObjectGuid,           // FGuid from AddPossessable
        *Actor,               // The actual AActor instance
        Actor->GetWorld()     // World as context
    );
}
else
{
    // Defer binding — actor may not exist yet.
    // Store ObjectGuid → BlenderGUID mapping for later resolution.
}
```

**Key method signatures:**

| Method | Availability | Notes |
|--------|-------------|-------|
| `UMovieScene::AddPossessable(FString, UClass*)` | Runtime (no `#if WITH_EDITOR`) | Returns `FGuid`; creates `FMovieScenePossessable` + `FMovieSceneBinding` |
| `UMovieScene::RemovePossessable(FGuid)` | Runtime | Removes possessable + all bound tracks |
| `ULevelSequence::BindPossessableObject(FGuid, UObject&, UObject*)` | Runtime | Binds possessable to actual object at runtime |
| `ULevelSequence::CreatePossessable(UObject*)` | `#if WITH_EDITOR` only | Not available at runtime; use `AddPossessable` + `BindPossessableObject` instead |
| `ULevelSequence::FindOrAddBinding(UObject*)` | `#if WITH_EDITOR` only | Not needed; manage bindings via `AddPossessable` directly |

**Critical note**: `ULevelSequence::CreatePossessable(UObject*)` is editor-only, but the two-step `MovieScene::AddPossessable()` + `Sequence::BindPossessableObject()` is fully available at runtime. Use the two-step path.

---

#### 3. Minimal Transform-Key Insertion Path

```cpp
// --- Step 1: Create or find Transform Track ---
// First check if a transform track already exists for this binding
UMovieScene3DTransformTrack* TransformTrack = Cast<UMovieScene3DTransformTrack>(
    MovieScene->FindTrack(
        UMovieScene3DTransformTrack::StaticClass(),
        ObjectGuid
    )
);
if (!TransformTrack)
{
    TransformTrack = MovieScene->AddTrack<UMovieScene3DTransformTrack>(ObjectGuid);
}

// --- Step 2: Create Section ---
UMovieScene3DTransformSection* Section = Cast<UMovieScene3DTransformSection>(
    TransformTrack->CreateNewSection()
);
// Set section range to cover the full sequence duration
Section->SetRange(TRange<FFrameNumber>(
    FFrameNumber(0),
    FFrameNumber(SequenceDuration)
));
TransformTrack->AddSection(*Section);

// --- Step 3: Access Double Channels via Channel Proxy ---
// The 3D transform section has 9 double channels + 1 float weight channel:
// Index 0-2: Translation X, Y, Z (FMovieSceneDoubleChannel)
// Index 3-5: Rotation X, Y, Z   (FMovieSceneDoubleChannel)
// Index 6-8: Scale X, Y, Z      (FMovieSceneDoubleChannel)
// Index 9:   ManualWeight        (FMovieSceneFloatChannel)

FMovieSceneChannelProxy& Proxy = Section->GetChannelProxy();

FMovieSceneDoubleChannel* LocX = Proxy.GetChannel<FMovieSceneDoubleChannel>(0);
FMovieSceneDoubleChannel* LocY = Proxy.GetChannel<FMovieSceneDoubleChannel>(1);
FMovieSceneDoubleChannel* LocZ = Proxy.GetChannel<FMovieSceneDoubleChannel>(2);

// --- Step 4: Insert Keys ---
// Key insertion methods on FMovieSceneDoubleChannel:
//   AddConstantKey(FFrameNumber InTime, double InValue)         — step function
//   AddLinearKey(FFrameNumber InTime, double InValue)           — linear interpolation
//   AddCubicKey(FFrameNumber InTime, double InValue, ...)       — Bézier interpolation

// Insert location key at frame 0
LocX->AddLinearKey(FFrameNumber(0), 0.0);
LocY->AddLinearKey(FFrameNumber(0), 0.0);
LocZ->AddLinearKey(FFrameNumber(0), 0.0);

// Insert location key at frame 100 with cubic (Bézier) interpolation
FRichCurveTangentMode TangentMode = RCTM_Auto;
FMovieSceneTangentData Tangent;
LocX->AddCubicKey(FFrameNumber(100), 500.0, TangentMode, Tangent);

// Simpler: batch-insert using UpdateOrAddKeys
TArray<FFrameNumber> Times = { 0, 50, 100 };
TArray<FMovieSceneDoubleValue> Values;
for (int32 i = 0; i < 3; i++)
{
    FMovieSceneDoubleValue Val;
    Val.Value = i * 100.0;
    Val.InterpMode = ERichCurveInterpMode::RCIM_Linear;
    Values.Add(Val);
}
LocX->UpdateOrAddKeys(Times, Values);

// For bool tracks:
#include "Tracks/MovieSceneBoolTrack.h"
#include "Sections/MovieSceneBoolSection.h"

UMovieSceneBoolTrack* BoolTrack = MovieScene->AddTrack<UMovieSceneBoolTrack>(ObjectGuid);
BoolTrack->SetPropertyNameAndPath(
    FName("bHidden"),
    TEXT("bHidden")           // Property path on the actor
);
UMovieSceneBoolSection* BoolSection = Cast<UMovieSceneBoolSection>(
    BoolTrack->CreateNewSection()
);
BoolSection->SetRange(TRange<FFrameNumber>(FFrameNumber(0), FFrameNumber(250)));
BoolTrack->AddSection(*BoolSection);
BoolSection->GetChannel().AddKeys(
    TArray<FFrameNumber>{ 0, 50, 100 },
    TArray<bool>{ false, true, false }
);

// For camera cuts:
#include "Tracks/MovieSceneCameraCutTrack.h"
#include "Sections/MovieSceneCameraCutSection.h"

UMovieSceneCameraCutTrack* CameraCutTrack = Cast<UMovieSceneCameraCutTrack>(
    MovieScene->AddCameraCutTrack(UMovieSceneCameraCutTrack::StaticClass())
);
FMovieSceneObjectBindingID CameraBindingID(
    UE::MovieScene::FRelativeObjectBindingID(CameraPossessableGuid)
);
CameraCutTrack->AddNewCameraCut(CameraBindingID, FFrameNumber(0));
```

**Channel index mapping for UMovieScene3DTransformSection:**

| Channel Index | Property | UE Coordinate | Scale | Blender Source |
|---------------|----------|---------------|-------|----------------|
| 0 | Translation X | UE cm | 1.0 | `location.x * 100.0` |
| 1 | Translation Y | UE cm | 1.0 | `location.y * 100.0` |
| 2 | Translation Z | UE cm | 1.0 | `location.z * 100.0` |
| 3 | Rotation X (Roll) | Radians | 1.0 | Quat→Euler: converted from Blender quaternion |
| 4 | Rotation Y (Pitch) | Radians | 1.0 | " |
| 5 | Rotation Z (Yaw) | Radians | 1.0 | " |
| 6 | Scale X | Unreal Units | 1.0 | `scale.x` |
| 7 | Scale Y | Unreal Units | 1.0 | `scale.y` |
| 8 | Scale Z | Unreal Units | 1.0 | `scale.z` |

Scale must be divided by 100.0 relative to Blender source (Blender stores meters, UE stores centimeters, but scale is a multiplier, not absolute distance). Location is already in UE cm from `get_transform()` (multiplied by 100.0), so no further scaling is needed.

---

#### 4. Module Dependencies

**Required additions to `UELiveSync.Build.cs`:**

```csharp
// New module dependencies for Phase 7E Sequencer integration
PublicDependencyModuleNames.AddRange(new string[]
{
    "LevelSequence",      // ULevelSequence, binding APIs
    "MovieScene",         // UMovieScene, sections, channels, possessables
    "MovieSceneTracks",   // UMovieScene3DTransformTrack/Section, BoolTrack, CameraCutTrack
});
```

**Current `UELiveSync.Build.cs` already has:**
```
Core, CoreUObject, Engine, InputCore           (Public)
Slate, SlateCore, Sockets, Networking,          (Private)
Json, JsonUtilities, ProceduralMeshComponent,
UnrealEd
```

**Editor-only concern:**
- `ULevelSequence`, `UMovieScene`, `UMovieSceneTracks` are all **Runtime** modules.
- The existing `UnrealEd` dependency in `PrivateDependencyModuleNames` is already present for Phase 7D viewport apply (guarded by `#if WITH_EDITOR`).
- For Phase 7E, no additional Editor-only modules are required. The full API surface (AddPossessable, BindPossessableObject, AddTrack, CreateNewSection, AddKey) is available at runtime in all build configurations.
- **However**: `ULevelSequence::CreatePossessable(UObject*)` is `#if WITH_EDITOR` — this is avoided by using the two-step `MovieScene::AddPossessable()` + `Sequence::BindPossessableObject()` path which is available at runtime.

**Build impact:**
- Adding `LevelSequence`, `MovieScene`, `MovieSceneTracks` as `PublicDependencyModuleNames` will trigger recompilation of any translation unit that includes the new headers.
- Recommend adding includes only to `UELiveSyncSubsystem.cpp` (not the header) to minimize recompilation impact.
- No link-time impact beyond the added modules.

---

#### 5. Existing UELiveSync Code Reuse

| Existing Code | Function | Reuse for Phase 7E |
|---------------|----------|-------------------|
| `FindActorFast(FGuid)` | `ActorCache.Find(Guid) → TWeakObjectPtr<AActor>*` | Directly usable to find actor for `BindPossessableObject()` |
| `BuildActorCache()` | Scans all actors via `TActorIterator<AActor>`, parses `LiveSync_GUID=<digits>` tags | Already populates `ActorCache`; no changes needed |
| `TryCacheActor(AActor*)` | Parses GUID tag from actor FName tags | Already works; Sequencer possessables use same `FGuid` |
| `ActorCache` (`TMap<FGuid, TWeakObjectPtr<AActor>>`) | GUID → Actor mapping | Primary lookup for possessable binding |
| `LastTimelineState` | Contains `FrameStart`, `FrameEnd`, `FPSNum`, `FPSDen` from Phase 7B | Provides frame range and FPS for sequence initialization |
| `HandleTimeline()` | Storage-only timeline handler | Can trigger sequence update when frame range/FPS changes |
| `HandlePlaybackState()` | Storage-only playback handler | Pattern reference for new handler implementation |
| `HandleActiveCamera()` | Storage-only camera handler with sequence validation | Pattern reference for stale-check, counter, diagnostic patterns |
| `ConsoleReset()` / `ConsoleDumpState()` | Reset + dump all persistent state | Add Sequencer-specific counters and sequence reset |
| `Stats` (`FLiveSyncStats`) | Atomic counters for all domains | Add keyframe/sequencer counters |
| `ProcessBinaryPacket()` | Packet dispatch with kValidTypes, size check, sequence monotonicity | Add dispatch cases for `0x17` and `0x18` |
| `kValidTypes[]` | Packet type whitelist | Add `0x17`, `0x18` |
| `LiveSyncQueue` / `FLiveSyncPacket` | Thread-safe packet ingress | No changes needed; keyframe packets ride the same queue |
| `FLiveSyncRunnable` | Network receive thread | No changes needed |
| Capability negotiation (`RemoteCapabilities`, `_send_announce()`, `PT_CapabilityAnnounce/Response`) | Capability gating | Add `CAP_SUPPORTS_KEYFRAME_REPLICATION=0x01`, `CAP_SUPPORTS_SEQUENCER_OPS=0x02` |

---

#### 6. GUID Lookup Path Compatibility

The existing GUID lookup path is **fully compatible** with Sequencer possessable binding:

```
1. Blender assigns GUID → stores as obj["ue_guid"]
2. Blender creates actor in UE → UE tags actor with "LiveSync_GUID=<digits>" FName tag
3. BuildActorCache() scans actors → parses tag → stores in ActorCache[FGuid] = TWeakObjectPtr<AActor>
4. FindActorFast(BlenderGUID) → returns AActor*
5. Phase 7E: Sequence->BindPossessableObject(PossessableGuid, *Actor, World)
   where PossessableGuid comes from MovieScene->AddPossessable()
```

**Key requirement**: The Blender object GUID (used for possessable binding) is the same GUID used for all existing operations (transform, visibility, rename, hierarchy, etc.). No GUID translation or mapping is needed.

**Deferred binding for actors not yet in cache**: Same pattern as Phase 6D hierarchy (`PendingHierarchyAttachments`). Store a `PendingPossessableBindings` map: `TMap<FGuid /* BlenderGUID */, FGuid /* PossessableGUID */>` and resolve it when the actor appears in the cache.

---

#### 7. Risks

| # | Risk | Probability | Impact | Mitigation |
|---|------|-------------|--------|------------|
| R1 | `UMovieScene::AddPossessable(FString, UClass*)` is editor-only in some UE versions | Medium | High | Verify in UE 5.7.4 header; fallback to constructing `FMovieScenePossessable` + `FMovieSceneBinding` and using `AddPossessable(const FMovieScenePossessable&, const FMovieSceneBinding&)` |
| R2 | `ULevelSequence::BindPossessableObject()` requires valid UWorld* context | Low | High | Store BlenderGUID ↔ PossessableGUID mapping; retry binding when actor appears; same pattern as `PendingAttachments` |
| R3 | Double channel key insertion performance with thousands of keys | Medium | Low | Batch keys via `UpdateOrAddKeys()` instead of per-key `AddKey()`; enforce max 1024 keys per channel per tick |
| R4 | `LevelSequence` asset not saved/persisted after creation | Low | Medium | Wrap in `UPackage` with `FString AssetPath`; provide CVar `UE.LiveSync.Sequencer.AssetPath` for persistent storage |
| R5 | Multiple LiveSync sessions create multiple overlapping sequences | Low | Medium | Track one active sequence; clear and recreate on reconnect/ConsoleReset |
| R6 | Transform section channels ingest float data but UE expects double | Low | Low | `FMovieSceneDoubleChannel::AddKey(FFrameNumber, double, ...)` — Blender sends float, cast to double on insertion |
| R7 | Adding 3 new modules (`LevelSequence`, `MovieScene`, `MovieSceneTracks`) increases compile time | Medium | Low | Include headers only in `.cpp` files, not `.h`; forward-declare where possible |
| R8 | `MovieSceneCameraCutTrack::AddNewCameraCut()` auto-manages sections and may reorder existing cuts | Medium | Medium | Use `SetIsAutoManagingSections(false)` before adding cuts; manually manage sections via `CreateNewSection()` + `AddSection()` |
| R9 | Rotation: Blender stores quaternion but UE transform section expects Euler (Roll/Pitch/Yaw) | Low | Medium | Convert at key insertion: Quat → FRotator → decompose to channels 3-5; document minor precision differences |
| R10 | `SetRange()` must extend section range to cover all keys, else keys outside range are invisible | Medium | Low | After adding all keys, call `Section->SetRange()` to span min/max keyframe times; or extend on each addition |

---

**Validation gate**: Audit report written. No source files modified.

### Stage 2 — Wire Format

| Step | Description | File | Deliverable |
|------|-------------|------|-------------|
| 2.1 | Add `PT_Keyframe = 0x17` constant to Blender `network.py` | `network.py` | Constant defined |
| 2.2 | Add `PT_SequencerOp = 0x18` constant to Blender `network.py` | `network.py` | Constant defined |
| 2.3 | Add `serialize_keyframe_batch()` function to Blender `network.py` | `network.py` | Batch serializer |
| 2.4 | Add `serialize_sequencer_op()` function to Blender `network.py` | `network.py` | Op serializer |
| 2.5 | Add `CAP_SUPPORTS_KEYFRAME_REPLICATION = 0x01` to Blender `network.py` | `network.py` | Capability bit |
| 2.6 | Add `CAP_SUPPORTS_SEQUENCER_OPS = 0x02` to Blender `network.py` | `network.py` | Capability bit |
| 2.7 | Update `_local_capabilities` in Blender `network.py` | `network.py` | Caps updated |
| 2.8 | Add `PT_Keyframe = 0x17` to UE `SyncTypes.h` | `SyncTypes.h` | Enum entry |
| 2.9 | Add `PT_SequencerOp = 0x18` to UE `SyncTypes.h` | `SyncTypes.h` | Enum entry |
| 2.10 | Add `FKeyframeBatchHeader` + `FKeyframeEntry` structs to UE `SyncTypes.h` | `SyncTypes.h` | Struct definitions |
| 2.11 | Add `FSequencerOpPayload` struct to UE `SyncTypes.h` | `SyncTypes.h` | Struct definitions |
| 2.12 | Add capability bits to UE `SyncTypes.h` | `SyncTypes.h` | Cap bits |
| 2.13 | Update `UE_LOCAL_CAPABILITIES` | `SyncTypes.h` | Caps mask |
| 2.14 | Update protocol signature FNV hash (both sides) | `network.py`, `SyncTypes.h` | Signatures match |
| 2.15 | Write standalone tests for wire format | `tests/phase7e_keyframe_wire.py` | 30+ tests |

**Validation gate**: 30+ wire format tests PASS. Protocol signatures match across Blender and UE.

### Stage 3 — Blender Keyframe Extraction

| Step | Description | File | Deliverable |
|------|-------------|------|-------------|
| 3.1 | Add `get_fcurve_data(obj)` extraction helper to `sync.py` | `sync.py` | FCurve extraction |
| 3.2 | Add `get_action_fcurves(action)` to iterate FCurves with channel metadata | `sync.py` | Channel enumeration |
| 3.3 | Add `extract_keyframe_point(kp, data_path, array_index)` per-point extraction | `sync.py` | Point extraction |
| 3.4 | Add `euler_to_quaternion_at_frame(obj, frame)` conversion helper | `sync.py` | Euler→Quat conversion |
| 3.5 | Add `compute_keyframe_channel_hash(channel_data)` — SHA-256 of serialized points | `sync.py` | Channel diff |
| 3.6 | Add `_last_keyframe_state` global dict: `guid → {channel_key → hash}` | `sync.py` | Change tracking |
| 3.7 | Add `_is_keyframe_replication_effective()` gating function | `sync.py` | Cap gating |
| 3.8 | Add `set_keyframe_replication_enabled()` preference setter | `sync.py` | Pref control |
| 3.9 | Add detection block to `check_updates()`: after playback, before camera | `sync.py` | Detection loop |
| 3.10 | Add full-snapshot-on-reconnect: clear `_last_keyframe_state` in `start_sync()` | `sync.py` | Reconnect |
| 3.11 | Add diagnostics stats to `dump_diagnostics()` | `sync.py` | Observability |
| 3.12 | Add `keyframe_replication_sync` BoolProperty to `__init__.py` (default OFF) | `__init__.py` | UI pref |
| 3.13 | Add `_on_keyframe_replication_update` callback | `__init__.py` | Pref callback |
| 3.14 | Wire UI display in preferences `draw()` | `__init__.py` | UI draw |
| 3.15 | Write standalone tests for Blender extraction | `tests/phase7e_keyframe_extraction.py` | 20+ tests |

**Validation gate**: 20+ extraction tests PASS. Simulation shows correct FCurve enumeration and keyframe serialization.

### Stage 4 — UE Sequence Creation

| Step | Description | File | Deliverable |
|------|-------------|------|-------------|
| 4.1 | Add ULevelSequence include to UE `UELiveSyncSubsystem.cpp` | `.cpp` | Include |
| 4.2 | Add MovieScene + Sequencer module dependencies to `UELiveSync.Build.cs` | `Build.cs` | Deps |
| 4.3 | Add `HandleSequencerOp()` declaration to `UELiveSyncSubsystem.h` | `.h` | Decl |
| 4.4 | Add `HandleSequencerOp()` implementation: opcode dispatch | `.cpp` | Implementation |
| 4.5 | Implement `CREATE_SEQUENCE`: create `ULevelSequence` asset | `.cpp` | Sequence creation |
| 4.6 | Implement `ADD_POSSESSABLE`: add possessable, bind by GUID | `.cpp` | Possessable binding |
| 4.7 | Implement `REMOVE_POSSESSABLE`: remove possessable + tracks | `.cpp` | Possessable removal |
| 4.8 | Implement `CLEAR_SEQUENCE`: clear all tracks/sections | `.cpp` | Sequence clear |
| 4.9 | Implement `SET_FRAME_RANGE`: update play range | `.cpp` | Frame range |
| 4.10 | Add `0x18` to `kValidTypes[]` | `.cpp` | Type whitelist |
| 4.11 | Add `0x18` dispatch case in `ProcessBinaryPacket` | `.cpp` | Dispatch |
| 4.12 | Add counters + ConsoleReset/DumpState to `Diagnostics.inl` | `.inl` | Diagnostics |
| 4.13 | Add `bHasSequence` + `CurrentLevelSequence` member vars | `.h` | State members |
| 4.14 | Write standalone tests for SequencerOp handler | `tests/phase7e_sequencer_op_wire.py` | 20+ tests |

**Validation gate**: 20+ op handler tests PASS. UE handler dispatches all opcodes correctly.

### Stage 5 — Transform Key Insertion

| Step | Description | File | Deliverable |
|------|-------------|------|-------------|
| 5.1 | Add `HandleKeyframeBatch()` declaration to `UELiveSyncSubsystem.h` | `.h` | Decl |
| 5.2 | Add `HandleKeyframeBatch()` implementation: parse header, iterate channels | `.cpp` | Batch parser |
| 5.3 | Add per-channel track lookup / creation: find or create `UMovieScene3DTransformTrack` | `.cpp` | Track management |
| 5.4 | Add per-channel section creation: find or create section for possessable | `.cpp` | Section management |
| 5.5 | Implement location key insertion (channels 0-2): `FMovieSceneDoubleChannel::AddKey()` | `.cpp` | Location keys |
| 5.6 | Implement rotation key insertion (channels 3-5): convert Quat→Euler, insert | `.cpp` | Rotation keys |
| 5.7 | Implement scale key insertion (channels 6-8): insert with proper scaling | `.cpp` | Scale keys |
| 5.8 | Implement Bézier handle application for cubic keys | `.cpp` | Handle support |
| 5.9 | Implement LINEAR key insertion (RCIM_Linear) | `.cpp` | Linear keys |
| 5.10 | Implement CONSTANT key insertion (RCIM_Constant) | `.cpp` | Constant keys |
| 5.11 | Handle interpolation fallback (SINE→CUBIC, etc.) with counter | `.cpp` | Fallback |
| 5.12 | Add `0x17` to `kValidTypes[]` | `.cpp` | Type whitelist |
| 5.13 | Add `0x17` dispatch case | `.cpp` | Dispatch |
| 5.14 | Add counters + ConsoleReset/DumpState | `.inl` | Diagnostics |
| 5.15 | Add member vars: `TMap<FGuid, UMovieScene3DTransformTrack*>` | `.h` | Track cache |
| 5.16 | Write standalone tests for key insertion handler | `tests/phase7e_ue_handler_validation.py` | 30+ tests |

**Validation gate**: 30+ key handler tests PASS. Location, rotation (Quat→Euler), and scale keys insert correctly with proper interpolation.

### Stage 6 — Camera Cuts

| Step | Description | File | Deliverable |
|------|-------------|------|-------------|
| 6.1 | Implement `ADD_CAMERA_CUT` sequencer op: create or get `UMovieSceneCameraCutTrack` | `.cpp` | Cut track |
| 6.2 | Create `UMovieSceneCameraCutSection` with camera GUID binding | `.cpp` | Cut section |
| 6.3 | Set cut section start/end frames from op payload | `.cpp` | Section range |
| 6.4 | Store multiple cut sections for camera transition timeline | `.cpp` | Multiple cuts |
| 6.5 | Add counter: `CameraCutsAdded` | `.cpp` | Counter |
| 6.6 | Add visibility bool track key insertion (property: bHidden) | `.cpp` | Visibility keys |
| 6.7 | Add camera property float track key insertion (data.lens, etc.) | `.cpp` | Camera prop keys |
| 6.8 | Write standalone tests | `tests/phase7e_camera_cuts.py` | 15+ tests |

**Validation gate**: 15+ camera cut tests PASS. Camera cut sections created and bound correctly.

### Stage 7 — Validation & Regression

| Step | Description | Deliverable |
|------|-------------|-------------|
| 7.1 | Run full wire format test suite (Stage 2) | 30+ PASS |
| 7.2 | Run full extraction test suite (Stage 3) | 20+ PASS |
| 7.3 | Run full op handler test suite (Stage 4) | 20+ PASS |
| 7.4 | Run full key handler test suite (Stage 5) | 30+ PASS |
| 7.5 | Run full camera cut test suite (Stage 6) | 15+ PASS |
| 7.6 | Run Phase 7B regression | 44/44 PASS |
| 7.7 | Run Phase 7C regression | 136/136 PASS |
| 7.8 | Run Phase 7D regression | 364/364 PASS |
| 7.9 | Update STATUS.md Phase 7E entry | Documented |
| 7.10 | Update ARCHITECTURE.md | Documented |

**Validation gate**: 115+ Phase 7E tests PASS. 544 Phase 7B/7C/7D regression tests PASS. STATUS.md updated.

---

## 10. Explicitly Out of Scope (Stage-by-Stage)

| Feature | Reason | Deferred To |
|---------|--------|-------------|
| Skeletal animation keyframes | Requires bone map, retargeting, different packet format | Phase 8 |
| NLA strip stacking | Blender-only concept; UE Sequencer has no NLA equivalent | Out of scope |
| FCurve modifiers (ENVELOPE, CYCLES, etc.) | No UE equivalent; evaluation would require baking | Out of scope |
| Driver-based animation | Blender driver graph has no UE representation | Out of scope |
| Shape key animation | Mesh morph target animation, not Sequencer track | Phase 8 |
| Sequencer playback control (play/pause/stop) | Separate capability, requires transport control | Phase 7F |
| Sequencer frame scrubbing sync | Requires real-time frame sync, high bandwidth | Phase 7F |
| UE→Blender keyframe sync | Reverse sync adds significant complexity | Deferred |
| Multiple Level Sequences | Single sequence simplifies ownership model | Future |
| Sequencer editor UI integration | Editor-level feature, not pipeline-critical | Future |
| Runtime (non-editor) sequence playback | Requires cooked sequence assets | Phase 8 |
| Audio tracks | Not object-related animation | Out of scope |
| Event tracks | Blender has no equivalent event system | Out of scope |
| Material parameter animation | Material parameter curves are not FCurve-accessible via standard API | Future |
| Constraint keyframes | No UE Sequencer constraint system | Out of scope |

---

## 11. Risks & Mitigations

| Risk | Probability | Impact | Mitigation |
|------|-------------|--------|------------|
| UE `ULevelSequence` API changes between engine versions | Medium | High | Guard with `ENGINE_MAJOR_VERSION`; target UE 5.7+ |
| `#if WITH_EDITOR` adds build complexity for non-editor builds | Low | Medium | Guard all Sequencer calls; provide stub implementations for non-editor |
| Large keyframe count causes packet queue overflow | Medium | Medium | Enforce per-channel max (1024); batch across multiple packets; monitor queue depth |
| Bézier handle precision loss in float32 | Low | Low | Store handles as double in packet; only convert to float at UE key insertion |
| Euler→Quat→Euler round-trip loss for rotation keys | Medium | Low | Accept minor precision loss (<0.01°); document rotation differences |
| Multiple Blender objects animating simultaneously → packet storm | Medium | Medium | Throttle: max 10 object updates per tick; queue remainder |
| `ULevelSequence` asset persistence across sessions | Low | High | Name based on session ID; allow user to specify asset path via CVar |
| Transform section channel count mismatch (9 vs 6 vs 3) | Low | High | Use fixed 9-channel transform sections; pad unused channels |
| `CreatePackage()` fails in content browser location | Low | Medium | Fall back to `/Game/LiveSync/LiveSyncSequence` with unique suffix |

---

## 12. Files Touched (Estimated)

### Stage 2 — Wire Format

| File | Change |
|------|--------|
| `Blender_Addon/network.py` | Add `PT_Keyframe=0x17`, `PT_SequencerOp=0x18`, `serialize_keyframe_batch()`, `serialize_sequencer_op()`, capability bits, protocol sig update |
| `UE_Plugin/.../Public/SyncTypes.h` | Add `PT_Keyframe=0x17`, `PT_SequencerOp=0x18`, `FKeyframeBatchHeader`, `FKeyframeEntry`, `FSequencerOpPayload`, capability bits, `UE_LOCAL_CAPABILITIES` update, protocol sig update |
| `tests/phase7e_keyframe_wire.py` | New: 30+ wire format tests |

### Stage 3 — Blender Keyframe Extraction

| File | Change |
|------|--------|
| `Blender_Addon/sync.py` | Add `get_fcurve_data()`, `extract_keyframe_point()`, `compute_keyframe_channel_hash()`, `euler_to_quaternion_at_frame()`, detection block, globals, diagnostics, `start_sync()` reset |
| `Blender_Addon/__init__.py` | Add `keyframe_replication_sync` BoolProperty + callback + UI draw |
| `tests/phase7e_keyframe_extraction.py` | New: 20+ extraction tests |

### Stage 4 — UE Sequence Creation

| File | Change |
|------|--------|
| `UE_Plugin/.../Public/UELiveSyncSubsystem.h` | Add `HandleSequencerOp()` decl, `bHasSequence`, `CurrentLevelSequence`, member vars |
| `UE_Plugin/.../Private/UELiveSyncSubsystem.cpp` | Add includes, `0x18` in `kValidTypes[]`, dispatch case, `HandleSequencerOp()` implementation |
| `UE_Plugin/.../Private/UELiveSyncSubsystem_Diagnostics.inl` | SequencerOp counters + ConsoleReset/DumpState |
| `UE_Plugin/.../UELiveSync.Build.cs` | Add `LevelSequence`, `MovieScene`, `MovieSceneTracks` module dependencies |
| `tests/phase7e_sequencer_op_wire.py` | New: 20+ op handler tests |

### Stage 5 — Transform Key Insertion

| File | Change |
|------|--------|
| `UE_Plugin/.../Public/UELiveSyncSubsystem.h` | Add `HandleKeyframeBatch()` decl, track cache member vars |
| `UE_Plugin/.../Private/UELiveSyncSubsystem.cpp` | Add `0x17` in `kValidTypes[]`, dispatch case, `HandleKeyframeBatch()` implementation |
| `UE_Plugin/.../Private/UELiveSyncSubsystem_Diagnostics.inl` | Keyframe counters + ConsoleReset/DumpState |
| `tests/phase7e_ue_handler_validation.py` | New: 30+ key insertion tests |

### Stage 6 — Camera Cuts

| File | Change |
|------|--------|
| `UE_Plugin/.../Private/UELiveSyncSubsystem.cpp` | Implement camera cut track section creation in `HandleSequencerOp` |
| `UE_Plugin/.../Private/UELiveSyncSubsystem_Diagnostics.inl` | Add `CameraCutsAdded` counter |
| `tests/phase7e_camera_cuts.py` | New: 15+ camera cut tests |

### Stage 7 — Validation & Regression

| File | Change |
|------|--------|
| `STATUS.md` | Mark Phase 7E complete, update regression table |
| `Docs/ARCHITECTURE.md` | Update with Phase 7E keyframe/sequencer architecture |

---

## Appendix A — References

| Document | Purpose |
|----------|---------|
| `Docs/Architecture/52-phase7-animation-sequencer-scope-lock.md` | Parent Phase 7 architecture document |
| `Docs/Architecture/53-phase7d-camera-sync-scope-lock.md` | Phase 7D camera sync architecture (reference for format and patterns) |
| `STATUS.md` | Project phase tracking and regression status |
| `Blender_Addon/network.py` | Blender protocol definitions and serialization |
| `Blender_Addon/sync.py` | Blender detection loop and state machines |
| `UE_Plugin/.../Public/SyncTypes.h` | UE protocol definitions and payload structs |
| `UE_Plugin/.../Private/UELiveSyncSubsystem.cpp` | UE packet dispatch and handler implementations |

---

## Appendix B — Proposed Implementation Order

| Priority | Stage | Reason |
|----------|-------|--------|
| 1 | Stage 2 (Wire Format) | Must define packet format before any other code |
| 2 | Stage 3 (Blender Extraction) | Blender side drives what data is available |
| 3 | Stage 4 (UE Sequence Creation) | Must have sequence before inserting keys |
| 4 | Stage 5 (Transform Keys) | Core feature — transform animation replication |
| 5 | Stage 6 (Camera Cuts) | Depends on possessables + transform tracks existing |
| 6 | Stage 7 (Validation) | Final closeout, after all stages complete |

**Total estimated effort**: 10–14 days
- Stage 2: 2 days
- Stage 3: 3 days
- Stage 4: 2 days
- Stage 5: 3 days
- Stage 6: 1 day
- Stage 7: 1 day

---

## Appendix C — Comparison with Parent Phase 7 Document

| Original Phase 7 Proposal (Doc 52) | Phase 7E Scope Lock | Rationale |
|-----------------------------------|---------------------|-----------|
| Keyframe replication for all FCurve types | Transform + visibility + camera properties only | Skeletal/NLA/shape deferred; scope managed |
| Bidirectional keyframe sync | Unidirectional (Blender→UE) only | Reverse sync adds significant complexity |
| LevelSequence management | One Level Sequence, recreated on reconnect | Simpler ownership; avoids stale sequence issues |
| Sequencer playback control | Deferred to Phase 7F | Separates concerns; reduces scope |
| Real-time frame scrubbing | Deferred to Phase 7F | Requires low-latency transport path |

---

## Appendix D — Implementation Closeout

**Date**: 2026-06-04
**Status**: IMPLEMENTATION COMPLETE ✅

### Scope Delivered

The transform keyframe pipeline is fully implemented and verified:

| Stage | Component | Tests | Status |
|-------|-----------|-------|--------|
| 1 | UE Sequencer API audit | — | Complete |
| 2 | Compile probe | — | Complete |
| 3 | PT_SequencerOp (0x18) wire format + parser | 81/81 | Complete |
| 4 | CREATE_SEQUENCE / SET_FRAME_RANGE / CLEAR_SEQUENCE apply | 50/50 | Complete |
| 5 | ADD_POSSESSABLE / REMOVE_POSSESSABLE apply | 50/50 | Complete |
| 6 | ADD_CAMERA_CUT apply | 72/72 | Complete |
| 7 | PT_Keyframe (0x17) wire format + parser | 79/79 | Complete |
| 8 | Blender FCurve extraction | 54/54 | Complete |
| 9 | UE transform keyframe apply (HandleKeyframe) | 97/97 | Complete |
| 9B | End-to-end pipeline validation | 63/63 | Complete |
| **Total** | | **496/496** | ✅ |

### Current Scope Limits

- **Transform channels only** (LocX/Y/Z, RotX/Y/Z, ScaleX/Y/Z) — channels 0–8
- **No visibility keys** — deferred to Stage 10A
- **No camera property keys** — deferred
- **No interpolation/tangent mapping** — deferred (requires packet format extension)
- **No Bézier handles** — deferred
- **No live Sequencer UI opening** — deferred to Phase 7F

### Packet Layout (unchanged)

```
FKeyframeHeader: 14 bytes (Sequence[4] + Timestamp[8] + KeyCount[1] + Flags[1])
FKeyframeEntry:  25 bytes (ObjectGUID[16] + Frame[4] + Value[4] + ChannelIndex[1])
```

No changes to the wire format were required for Stage 9B closeout.

### Recommendation: Stage 10A — Visibility Keyframes

Implement `UMovieSceneBoolTrack` writes for object visibility, reusing the existing visibility lane data. Lower risk than interpolation, fits existing infrastructure, uses verified track types, no packet format change required.
