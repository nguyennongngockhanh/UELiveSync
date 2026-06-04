# Phase 7 — Animation & Sequencer Sync Scope Lock

**Date**: 2026-06-02
**Status**: SCOPE LOCK — no implementation started
**Depends on**: Phase 6 (Live Editing) ✅, Phase 8 (Streaming) ✅, Phase 9 (Production) ✅
**Blocks**: Nothing — greenfield capability
**Original roadmap slot**: Reassigned from "Phase 7 — Animation & Sequencer Sync" (original Phase 7 scope was absorbed into Phases 7A/7B/7C mesh/material/geometry pipeline; animation sync was deferred to "Phase 8+" per Phase 7A scope-lock §Architecture Implications)

---

## Table of Contents

1. [Purpose](#1-purpose)
2. [Current Animation State](#2-current-animation-state)
3. [Blender Capabilities & API Hooks](#3-blender-capabilities--api-hooks)
4. [Unreal Engine Sequencer Capabilities](#4-unreal-engine-sequencer-capabilities)
5. [Existing Infrastructure for Reuse](#5-existing-infrastructure-for-reuse)
6. [Scope Definition](#6-scope-definition)
7. [Semantic Model](#7-semantic-model)
8. [Performance Analysis](#8-performance-analysis)
9. [Recommended Packet Architecture](#9-recommended-packet-architecture)
10. [Implementation Phases](#10-implementation-phases)
11. [Gap Analysis](#11-gap-analysis)
12. [Risks & Mitigations](#12-risks--mitigations)
13. [Acceptance Criteria](#13-acceptance-criteria)
14. [In-Scope / Out-of-Scope / Deferred Summary](#14-in-scope--out-of-scope--deferred-summary)

---

## 1. Purpose

Phase 7 extends UELiveSync to synchronize **animation, timeline, playback, camera, and keyframe state** between Blender and Unreal Engine 5 Sequencer. After Phase 6, UELiveSync can replicate static object state (transform, visibility, hierarchy, collection, identity, geometry). Phase 7 adds **time-variant state** — the temporal dimension.

This scope lock exists to:

1. **Define the data model** — Animation sync is fundamentally different from transform sync. Keyframes, curves, and timelines are higher-dimensional data than per-frame poses.
2. **Prevent scope creep** — Animation is a broad domain. This document fences off what Phase 7 does and does not include.
3. **Establish the packet architecture** — Before writing any code, the wire format for keyframes, timeline events, and camera definitions must be designed.
4. **Identify reuse opportunities** — The existing replay/snapshot/sequence-tracker infrastructure may be reusable for animation domains.
5. **Provide clear "done" definitions** — Each sub-phase has measurable exit criteria.

### 1.1 — Canonical Phase Reconciliation

| Original Roadmap | Current Phase | Status |
|---|---|---|
| Phase 7 — Animation & Sequencer Sync (Timeline, Playback, Camera, Keyframe, Sequencer Integration) | **Phase 7 — Animation & Sequencer Sync** | **SCOPE LOCK (this document)** |
| Phase 8 — High Performance Streaming (Backpressure, Mesh Compression) | **Phase 8** | COMPLETE ✅ |
| Phase 9 — Production Ecosystem (Capability, Discovery, Recovery) | **Phase 9** | COMPLETE ✅ |

The original "Phase 7" label was reassigned to the Static Mesh Identity / Material / Geometry pipeline (Phases 7A/7B/7C). The animation work originally planned as Phase 7 remained deferred. This document reclaims the Phase 7 label for its original purpose.

---

## 2. Current Animation State

### 2.1 — Protocol Audit

**No animation, keyframe, timeline, camera, or sequencer wire formats exist.** The protocol has zero animation-related packet types. All 18 assigned packet types (0x01–0x12) are for transform sync, lifecycle, semantic events, and infrastructure:

| Range | Usage | Phase |
|-------|-------|-------|
| 0x01–0x0F | Transform, Create, Delete, Heartbeat, AssetDef, Snapshot markers, Visibility, Rename, Hierarchy, Collection | Phases 3–6 |
| 0x10 | PT_BackpressureACK | Phase 8 |
| 0x11–0x12 | PT_CapabilityAnnounce / Response | Phase 9 |
| 0x13–0x1F | **Available** (reserved range) | — |
| 0x20+ | **Available** (extended range) | — |

### 2.2 — Blender Addon Audit

**Zero animation code exists.** The three files in `Blender_Addon/`:

| File | Lines | Animation Content |
|-------|-------|----------|
| `__init__.py` | 816 | UI, preferences, operators. No animation. |
| `sync.py` | 2014 | Core sync loop. No animation, keyframe, FCurve, camera, timeline, or frame code. |
| `network.py` | 2816 | TCP/serialization. All 49 "sequence" references are network replay-dedup sequence numbers, not Blender Sequencer. |

No:
- `bpy.context.scene.frame_current` usage
- `bpy.types.Object.animation_data` access
- FCurve iteration or keyframe handling
- Sequencer strip or timeline marker code
- Camera parameter serialization
- Playback state detection
- NLA track or Action references

### 2.3 — UE Plugin Audit

**Zero animation/sequencer code exists.** The plugin (16 KLOC) has no:

- `ULevelSequence`, `ULevelSequencePlayer`, or `ISequencer` references
- `UMovieScene`, `FMovieSceneFloatChannel` usage
- `CineCameraActor`, `CameraComponent`, or `ViewTarget` references
- `OnPlay`, `OnPause`, `OnStop` delegate hooks
- `FFrameNumber`, `FFrameTime`, `FFrameRate` usage
- Camera cut or possessable/spawnable code

The plugin is entirely transform-sync with no animation awareness.

### 2.4 — Replay System (Reusable)

The existing replay infrastructure is mature and could be extended:

| System | File | Lines | Reusable? |
|--------|------|-------|-----------|
| GWorldReplayBuffer | Replay.inl | 1475+ | **Yes** — extend replay domains for animation |
| SaveWorldState / RestoreWorldState | Replay.inl | 589–784 | **Yes** — add animation/camera domains |
| FWorldStateSnapshot | SyncTypes.h | 1328–1367 | **Yes** — extend with anim state |
| Sequence trackers (x6) | Subsystem.cpp | per-handler | **Yes** — one per new animation domain |
| Collection replay streams | Replay.inl | 1342–1475+ | **Reference** — pattern for animation replay |
| Phase 6H diagnostics | Phase6H.inl | 1144 | **Reference** — pattern for anim diagnostics |
| Phase 6I performance | Phase6I.inl | 493 | **Reference** — pattern for anim perf tracking |

---

## 3. Blender Capabilities & API Hooks

### 3.1 — Observable Animation State

| State | API | Polling? | Handler? | Frequency |
|-------|-----|----------|----------|-----------|
| Current frame | `scene.frame_current` | Yes | `frame_change_pre/post` | Per-frame during playback |
| Frame range | `scene.frame_start / frame_end` | Yes | — | On change (low) |
| Playback state | No direct API | — | — | — |
| Playback start | — | — | No standard handler | — |
| Playback stop | — | — | No standard handler | — |
| Scrubbing | `frame_current` | Yes | `frame_change_pre/post` | Per-frame |
| Frame rate | `scene.render.fps / frame_base` | Yes | — | Rarely changes |
| Active camera | `scene.camera` | Yes | `depsgraph_update_post` | On change |
| Camera FOV | `obj.data.angle` | Yes | `depsgraph_update_post` | On change |
| Camera focal length | `obj.data.lens` | Yes | `depsgraph_update_post` | On change |
| Camera sensor | `obj.data.sensor_width / height` | Yes | `depsgraph_update_post` | On change |
| Camera focus | `obj.data.dof.focus_distance` | Yes | `depsgraph_update_post` | On change |
| Camera aperture | `obj.data.aperture` | Yes | `depsgraph_update_post` | On change |

### 3.2 — Keyframe / FCurve API

| Operation | API | Notes |
|-----------|-----|-------|
| Enumerate FCurves | `obj.animation_data.action.fcurves` | Iterate all curves |
| Read keyframes | `fcurve.keyframe_points` | List of `Keyframe` objects |
| Detect keyframe changes | No direct handler | Must poll `fcurve.keyframe_points` |
| Detect curve structure change | No direct handler | Must poll curve count |
| Keyframe insertion | `obj.keyframe_insert()`, `fcurve.keyframe_points.insert()` | Programmatic |
| Keyframe deletion | `fcurve.keyframe_points.remove()` | Programmatic |
| Keyframe values | `keypoint.co[1]` | (frame, value) pairs |
| Keyframe handles | `keypoint.handle_left/right` | Interpolation control |
| Interpolation type | `keypoint.interpolation` | `'CONSTANT'`, `'LINEAR'`, `'BEZIER'`, etc. |
| FCurve modifiers | `fcurve.modifiers` | Generators, envelopes, etc. |
| Drivers | `fcurve.driver` | Expression-based animation |
| NLA tracks | `obj.animation_data.nla_tracks` | Non-linear animation layers |
| Actions | `obj.animation_data.action` | Named action datablocks |

### 3.3 — API Hooks & Limitations

| Hook | Availability | Limitation |
|------|-------------|------------|
| `frame_change_pre/post` | Standard Blender | Fires for ALL frame changes (scrubbing AND playback). Cannot distinguish user scrub from automated playback. |
| `depsgraph_update_post` | Already used | Fires on datablock changes. May miss keyframe-only edits if datablock identity unchanged. |
| `bpy.app.handlers.animation_playback_pre/post` | **Does not exist** | No standard handler for playback state changes. Must use polling or a timer-based heuristic. |
| `bpy.msgbus` | Available but heavy | Can subscribe to specific RNA properties, but not to "any FCurve changed" generically. |
| `bpy.app.timers` | Already used | The existing sync loop runs as a timer. Animation polling can be integrated into the existing cadence. |

**Key limitation — Detecting Blender animation edits:**

Blender provides no single "animation data changed" callback. The current transform sync model (polling `matrix_world`) works because transform reads are cheap. FCurve scanning is more expensive:

- An action with 30 FCurves × 100 keyframes = 3000 objects to compare per object
- For 100 animated objects: 300,000 comparisons per tick
- **Mitigation**: Use version hash on action data, only re-scan when hash changes

### 3.4 — Blender Camera Properties

| Property | Path | Type | Notes |
|----------|------|------|-------|
| Type | `obj.data.type` | enum | `'PERSP'`, `'ORTHO'`, `'PANO'` |
| FOV | `obj.data.angle` | float | Horizontal field of view (radians) |
| Focal length | `obj.data.lens` | float | mm |
| Sensor width | `obj.data.sensor_width` | float | mm |
| Sensor height | `obj.data.sensor_height` | float | mm |
| Focus distance | `obj.data.dof.focus_distance` | float | m (if DOF enabled) |
| Aperture | `obj.data.aperture` | float | f-stop |
| Shift X/Y | `obj.data.shift_x / _y` | float | Lens shift |
| Clip start/end | `obj.data.clip_start / _end` | float | Near/far plane |
| Stereo convergence | `obj.data.stereo.convergence_distance` | float | VR camera |

---

## 4. Unreal Engine Sequencer Capabilities

### 4.1 — Core Classes

| Class | Namespace | Role | Editor Only? |
|-------|-----------|------|--------------|
| `ULevelSequence` | `LevelSequence` | Asset containing a movie scene | No |
| `ULevelSequencePlayer` | `LevelSequence` | Runtime playback controller | No |
| `UMovieScene` | `MovieScene` | Timeline data model (tracks, sections, keys) | No |
| `UMovieSceneFloatChannel` | `MovieScene` | Keyframe storage for float properties | No |
| `UMovieSceneTransformTrack` | `MovieSceneTracks` | Transform track (location, rotation, scale) | No |
| `UMovieSceneVisibilityTrack` | `MovieSceneTracks` | Visibility track | No |
| `UMovieSceneCameraCutTrack` | `MovieSceneTracks` | Camera cut track | No |
| `ISequencer` | `Sequencer` | Editor UI integration | **Yes** |
| `ACineCameraActor` | `CineCamera` | Camera actor with `UCineCameraComponent` | No |
| `UCineCameraComponent` | `CineCamera` | Camera settings (FOV, focal, sensor, focus) | No |
| `FMovieScenePossessable` | `MovieScene` | Binds to existing actor | No |
| `FMovieSceneSpawnable` | `MovieScene` | Sequence-owned actor spawn | No |

### 4.2 — Playback API (Runtime)

```cpp
// ULevelSequencePlayer playback control — available at runtime
ULevelSequencePlayer* Player = ULevelSequencePlayer::CreateLevelSequencePlayer(
    World, LevelSequence, FMovieSceneSequencePlaybackSettings(), PlayerActor);

Player->Play();                          // Start playback
Player->Pause();                         // Pause at current frame
Player->Stop();                          // Stop and return to start
Player->SetPlaybackPosition(FMovieSceneSequencePlaybackParams(
    FFrameTime(TargetFrame), EUpdatePositionMethod::Play));  // Seek to frame
Player->GetCurrentTime().Time;           // Current playback position
Player->GetLength();                     // Total duration
Player->GetFrameRate();                  // Display rate
Player->OnPlay / OnStop / OnPause;       // Delegates (not available in all versions)
```

### 4.3 — Sequencer Editor API

```cpp
// ISequencer interface — editor-only
ISequencer* Sequencer = ...;  // Via FSequencerModule or GEditor

Sequencer->SetLocalTime(FFrameTime(Frame), ESnapTimeMode::DontSnap);  // Set timeline position
Sequencer->GetLocalTime();     // Current time
Sequencer->OnGlobalTimeChanged().AddRaw(...);  // Time change notification
```

### 4.4 — MovieScene Track Creation (Runtime)

```cpp
// Creating tracks and adding keyframes at runtime
UMovieScene* MovieScene = LevelSequence->GetMovieScene();

// Add a transform track for a possessable binding
UMovieSceneTransformTrack* TransformTrack = MovieScene->AddTrack<UMovieSceneTransformTrack>(BindingGuid);

// Add a float track
UMovieSceneFloatTrack* FloatTrack = MovieScene->AddTrack<UMovieSceneFloatTrack>(BindingGuid);

// Add keys to a float channel
UMovieSceneFloatChannel* Channel = ...;
Channel->AddKeys(TArray<FFrameNumber> Times, TArray<FMovieSceneValue> Values);

// Camera cut track
UMovieSceneCameraCutTrack* CutTrack = MovieScene->AddCameraCutTrack();
CutTrack->AddNewCameraCut(FFrameNumber(StartFrame), FFrameNumber(EndFrame), CameraBindingGuid);
```

### 4.5 — Key Integration Points

| Integration Point | API | Runtime | Editor | Purpose |
|-------------------|-----|---------|--------|---------|
| Set timeline position | `ISequencer::SetLocalTime()` | — | ✅ | Sync playhead during scrubbing |
| Get timeline position | `ISequencer::GetLocalTime()` | — | ✅ | Report current time |
| Playback control | `Player::Play/Pause/Stop()` | ✅ | ✅ | Transport sync |
| Create sequence | `NewObject<ULevelSequence>()` | ✅ | ✅ | Blender→UE sequence replication |
| Create track | `MovieScene->AddTrack<>()` | ✅ | ✅ | Per-property track creation |
| Insert keyframe | `Channel->AddKeys()` | ✅ | ✅ | Keyframe replication |
| Camera cut | `CameraCutTrack->AddNewCameraCut()` | ✅ | ✅ | Camera sync |
| Possess existing actor | `MovieScene->AddPossessable()` | ✅ | ✅ | Bind Blender object to sequence |
| Camera parameter | `UCineCameraComponent` direct set | ✅ | ✅ | Camera parameter sync |

### 4.6 — Frame Rate & Time Model

```cpp
// Unreal frame rate model
FFrameRate DisplayRate = LevelSequence->GetMovieScene()->GetDisplayRate();  // e.g. 24, 30, 60
FFrameRate TickResolution = LevelScene->GetMovieScene()->GetTickResolution(); // e.g. 24000 (sub-frame)

FFrameNumber FrameAt24fps = ...;
FFrameTime SubFrameTime(FrameAt24fps, 0.5f);  // With sub-frame (half tick)
```

---

## 5. Existing Infrastructure for Reuse

### 5.1 — Sequence Trackers (Per-GUID Monotonic)

Already used by rename, visibility, hierarchy, delete, and collection for replay deduplication. Phase 7 would add one new tracker per new domain:

```
_animation_sequences = {}    # Per-GUID sequence for timeline/playback events
_keyframe_sequences = {}     # Per-GUID sequence for keyframe replication
_camera_sequences = {}       # Per-GUID sequence for camera events
```

### 5.2 — Replay System (GWorldReplayBuffer)

Current replay domains: Lifecycle, Rename, Collection, Transform.

Phase 7 would add new replay domains:

| Domain | Marker Byte | Content |
|--------|-------------|---------|
| Timeline (proposed) | `0xCB` | Frame position, playback state |
| Keyframe (proposed) | `0xCA` | Keyframe data (per-GUID curve state) |
| Camera (proposed) | `0xC9` | Active camera, camera parameters |

### 5.3 — Diagnostics Counters

Pattern from Phase 6H/6I — add per-domain counters:

```
// Timeline
TimelinePackets, TimelineStaleRejections, TimelineReplayApplied, TimelineReplaySkipped

// Keyframe
KeyframePackets, KeyframeKeysReplicated, KeyframeStaleRejections, KeyframeReplayApplied

// Camera
CameraPackets, CameraChangesApplied, CameraStaleRejections, CameraReplayApplied

// Sequencer
SequencerTracksCreated, SequencerTracksUpdate, SequencerErrors
```

### 5.4 — Capability Negotiation

Phase 9 already provides capability bits for gating optional features. Phase 7 would reserve capability bits for animation domains:

| Capability Bit | Feature | Notes |
|----------------|---------|-------|
| 0x10 | Timeline Sync | Gated on both sides |
| 0x20 | Keyframe Replication | Gated on both sides |
| 0x40 | Camera Sync | Gated on both sides |
| 0x80 | Sequencer Integration | Gated on both sides |

### 5.5 — Collection Replay Stream Pattern

The collection replay stream (`GCollectionReplayBuffer`, `ReplayCollectionStream`) provides a proven pattern for domain-specific replay:
- Per-domain buffer with strict/relaxed ordering modes
- Sequence gap detection
- Corruption detection via CRC32
- Rollback-safe replay via SaveWorldState / RestoreWorldState

This pattern should be replicated for animation domains.

---

## 6. Scope Definition

### 6.1 — Timeline Sync (Phase 7B)

**Goal**: Synchronize the Blender timeline playhead position with UE Sequencer.

**Blender→UE**:
- `scene.frame_current` changes → UE Sequencer `SetLocalTime()` or `Player::SetPlaybackPosition()`
- Frame rate (`scene.render.fps`) → UE `LevelSequence::SetDisplayRate()`
- Frame range (`frame_start`, `frame_end`) → UE Sequence duration

**UE→Blender**:
- (Optional) UE Sequencer time change → Blender `scene.frame_current`

**Excluded**:
- Sub-frame accuracy (single-frame resolution is sufficient)
- Blender frame mapping to UE frames (1:1 by default)
- Timecode / SMPTE timecode sync

**Packet type**: Proposed `PT_Timeline = 0x13`

| Field | Size | Notes |
|-------|------|-------|
| GUID | 16 bytes | Change origin tracking |
| FrameNumber | 4 bytes (int32) | Current frame position |
| FrameRate | 4 bytes (float) | FPS (e.g. 24.0, 30.0, 60.0) |
| FrameStart | 4 bytes (int32) | Range start |
| FrameEnd | 4 bytes (int32) | Range end |
| **Total** | **32 bytes** | |

### 6.2 — Playback Sync (Phase 7C)

**Goal**: Synchronize playback state (play, pause, stop, loop) between Blender and UE.

**Blender→UE**:
- Playback start → UE `LevelSequencePlayer::Play()`
- Playback pause → UE `LevelSequencePlayer::Pause()`
- Playback stop → UE `LevelSequencePlayer::Stop()`
- Loop toggle → UE `PlaybackSettings.LoopCount`

**UE→Blender**:
- UE Sequencer Play/Pause → Blender `bpy.ops.screen.animation_play()`

**Excluded**:
- Frame-accurate start offset
- Scrub-to-play transition (scrubbing that becomes playback)
- Reverse playback

**Packet type**: Proposed `PT_PlaybackState = 0x14`

| Field | Size | Notes |
|-------|------|-------|
| GUID | 16 bytes | Origin tracking |
| State | 1 byte | 0=Stop, 1=Play, 2=Pause |
| LoopCount | 1 byte (signed) | -1=infinite, 0=once, N=N times |
| **Total** | **18 bytes** | |

### 6.3 — Camera Sync (Phase 7D)

**Goal**: Synchronize active camera and camera parameters.

**Blender→UE**:
- `scene.camera` changes → UE Set View Target to `ACineCameraActor`
- Camera parameter changes (FOV, focal length, sensor, focus distance, aperture) → `UCineCameraComponent` settings
- Camera create → UE spawn `ACineCameraActor`
- Camera delete → UE despawn camera

**Camera creation flow**:
1. Blender camera object detected as new (`scene.camera` reference or `tracked_objects`)
2. Regular `PT_Create + PT_AssetDef` flow (reuse existing pipeline)
3. Additional `PT_CameraDef` for cine camera parameters
4. UE spawns `ACineCameraActor` instead of default `AActor`

**Camera parameter update flow**:
1. Detect parameter change via `depsgraph_update_post` or poll
2. Send `PT_CameraDef` with updated parameters
3. UE applies to `UCineCameraComponent`

**Excluded**:
- Camera animation curves (handled by Keyframe Replication, Phase 7E)
- Post-process settings
- Camera rig/constraint sync
- Multi-camera switcher

**Packet type**: Proposed `PT_ActiveCamera = 0x15`, `PT_CameraDef = 0x16`

`PT_ActiveCamera` (18 bytes):
| Field | Size | Notes |
|-------|------|-------|
| Camera GUID | 16 bytes | Object GUID of active camera |
| Sequence | 2 bytes | Replay dedup |
| **Total** | **18 bytes** | |

`PT_CameraDef` (52 bytes):
| Field | Size | Notes |
|-------|------|-------|
| GUID | 16 bytes | Object GUID |
| FOV (angle) | 4 bytes (float) | radians |
| FocalLength | 4 bytes (float) | mm |
| SensorWidth | 4 bytes (float) | mm |
| SensorHeight | 4 bytes (float) | mm |
| FocusDistance | 4 bytes (float) | cm |
| Aperture | 4 bytes (float) | f-stop |
| ClipNear | 4 bytes (float) | cm |
| ClipFar | 4 bytes (float) | cm |
| CameraType | 1 byte | 0=Perspective, 1=Orthographic, 2=Panoramic |
| **Total** | **53 bytes** | |

### 6.4 — Keyframe Replication (Phase 7E)

**Goal**: Replicate Blender keyframe data to UE Sequencer tracks.

**What is replicated**:
- Transform keyframes (location, rotation, scale) → `UMovieSceneTransformTrack`
- Visibility keyframes → `UMovieSceneVisibilityTrack`
- Camera property keyframes (FOV, focal length, focus distance) → float tracks on `CineCameraActor`
- Property keyframes (custom properties) → float tracks

**Replication model**:
- **Snapshot on connect**: Send all current keyframes for animated objects
- **Incremental during sync**: Send only changed keyframes (added, modified, deleted)
- **Batching**: Multiple keyframes per packet for efficiency

**Keyframe identification**:
- Each keyframe is identified by (ObjectGUID, PropertyPath, FrameNumber) — a triple that forms a unique identity
- Modified keys: same (ObjectGUID, PropertyPath, FrameNumber) but different value/interpolation
- Deleted keys: explicit notification with the same triple

**Keyframe packet format**:
Proposed `PT_Keyframe = 0x17`

Single-keyframe payload (variable, ~48 bytes without property path):
| Field | Size | Notes |
|-------|------|-------|
| Object GUID | 16 bytes | Owner object |
| Property Tag | 2 bytes | Predefined property enum (replaces path string) |
| Op Type | 1 byte | 0=Add/Update, 1=Delete, 2=Clear all |
| FrameNumber | 4 bytes (int32) | Keyframe time |
| Value | 4 bytes (float) | Single value (or packed for transforms) |
| Interp | 1 byte | 0=Constant, 1=Linear, 2=CubicAuto, 3=CubicManual |
| LeftHandle | 4 bytes (float) | Handle position for Bezier |
| RightHandle | 4 bytes (float) | Handle position for Bezier |
| Sequence | 4 bytes (uint32) | Replay dedup |
| **Total** | **36 + optional extras** | Per keyframe |

**Multi-keyframe batching**: Header + N × keyframe payloads.

**Property tag system** (predefined, avoids string overhead):

```
TAG_LOCATION_X      = 0x0001
TAG_LOCATION_Y      = 0x0002
TAG_LOCATION_Z      = 0x0003
TAG_ROTATION_EULER_X = 0x0004
TAG_ROTATION_EULER_Y = 0x0005
TAG_ROTATION_EULER_Z = 0x0006
TAG_SCALE_X         = 0x0007
TAG_SCALE_Y         = 0x0008
TAG_SCALE_Z         = 0x0009
TAG_VISIBILITY      = 0x000A
TAG_CAMERA_FOV      = 0x000B
TAG_CAMERA_FOCAL    = 0x000C
TAG_CAMERA_FOCUS    = 0x000D
// ... up to 65535
```

**Excluded**:
- Per-bone/skeletal keyframes (deferred)
- Shape keyframes
- Custom property keyframes where property path does not map to predefined tag
- FCurve modifiers (generators, envelopes, noise)
- Drivers

### 6.5 — Sequencer Integration (Phase 7F)

**Goal**: Create and manage Level Sequences in UE based on Blender animation data.

**What is created**:
- `ULevelSequence` asset per Blender scene or action
- `UMovieSceneTransformTrack` per animated object (populated from keyframes)
- `UMovieSceneVisibilityTrack` per animated object
- Camera cut tracks when active camera changes
- Possessable bindings for existing actors

**Lifecycle**:
- **Create**: Blender action/anim detected → create `ULevelSequence` + populate tracks
- **Update**: New keyframes added in Blender → update existing track channels
- **Delete**: Object deleted → remove corresponding binding/track

**Excluded**:
- Spawnable binding creation (all objects exist in scene)
- Sequencer strip editing (cuts, transitions, effects)
- Audio track creation
- Event track creation
- Sub-sequence/nesting

**Packet type**: Proposed `PT_SequencerOp = 0x18`

| Field | Size | Notes |
|-------|------|-------|
| Sequence GUID | 16 bytes | Identifies the Level Sequence |
| Op Type | 1 byte | 0=CreateSequence, 1=DeleteSequence, 2=CreateTrack, 3=RemoveTrack |
| Object GUID | 16 bytes | Target object (for track ops) |
| Property Tag | 2 bytes | Target property (for track ops) |
| Track Type | 1 byte | 0=Transform, 1=Visibility, 2=Float (camera) |
| Sequence | 4 bytes | Replay dedup |
| **Total** | **40 bytes** | |

---

## 7. Semantic Model

### 7.1 — Event-Driven vs State-Driven

| Domain | Primary Model | Rationale |
|--------|---------------|-----------|
| Timeline sync | **State-driven** (broadcast current frame periodically) | Scrubbing generates rapid events; state-driven avoids flooding. Send current frame at ~10 Hz during playback. |
| Playback sync | **Event-driven** (send only on state change) | Low frequency — user initiates play/pause. No need for periodic broadcast. |
| Camera sync | **Event-driven** (send on selection/parameter change) | Camera changes are infrequent. |
| Keyframe replication | **Event-driven** (send on keyframe add/change/delete) | Keyframe edits occur in bursts. Batch multiple keys per packet. |
| Sequencer ops | **Event-driven** (send on action/track lifecycle) | Infrequent — user creates/removes tracks. |

### 7.2 — Replay Semantics

All animation domains follow the existing replay model:
- **Monotonic sequence per GUID** per domain (inherited from existing sequence trackers)
- **Stale rejection**: `current_seq <= last_seq` → skip (inherited from `IsStaleOrDuplicate()`)
- **Replay recording**: New `EWorldReplayDomain` entries for Timeline, Keyframe, Camera
- **Replay application**: On connection, replay stored timeline/keyframe state in order

### 7.3 — Snapshot Semantics

On full reconnect:
1. Blender sends current frame position as snapshot (single `PT_Timeline`)
2. Blender sends current playback state (single `PT_PlaybackState`)
3. Blender sends active camera (single `PT_ActiveCamera`)
4. Blender sends ALL current keyframes for all animated objects (potentially many `PT_Keyframe`)
5. UE rebuilds Sequencer state from received data

**Snapshot optimization**: Keyframe snapshot sends all keys for all animated objects. For a scene with N animated objects averaging K keyframes each, this is O(N×K) packets. Mitigation: batch multiple keyframes per packet.

### 7.4 — Ordering Requirements

| Rule | Description |
|------|-------------|
| Timeline before keyframe | Frame position must be established before keyframe data is applied |
| Create before animate | Object must exist (PT_Create received) before its keyframes are applied |
| Camera before camera param | Camera must be spawned before camera parameters are set |
| Sequence before track | Level sequence must exist before tracks are added to it |
| Track before keyframe | Track must exist before keyframes are inserted into it |

These ordering requirements map to the existing `Sequence != 0` / `LastSeq` pattern used by all existing semantic handlers. Sequence tracking ensures ordering without requiring strict packet arrival order.

### 7.5 — Conflict Resolution

| Conflict | Resolution |
|----------|------------|
| Blender scrubs while UE Sequencer plays | Last-writer-wins (both sides publish state; final position is last received) |
| Keyframe modified on both sides | Last-writer-wins per keyframe identity (GUID, PropertyTag, FrameNumber) |
| Camera switch on both sides | Last-writer-wins (active camera GUID) |
| Play/Stop race | Last-writer-wins (playback state is set to most recent received value) |

### 7.6 — Duplicate Suppression

All Phase 7 packets use the existing sequence-tracker pattern:
1. Blender assigns monotonic sequence per GUID per domain
2. UE checks `current_seq <= last_seq` → discard as stale/duplicate
3. UE stores `last_seq = current_seq` on accept

### 7.7 — Reconnect Behavior

| State | Behavior |
|-------|----------|
| Timeline position | Re-send current frame on reconnect (single PT_Timeline) |
| Playback state | Re-send current state (single PT_PlaybackState) |
| Active camera | Re-send current camera (single PT_ActiveCamera) |
| Camera parameters | Re-send with camera create (PT_Create + PT_CameraDef) |
| Keyframes | Re-send ALL keyframes (batched PT_Keyframe packets) |
| Level Sequences | Re-created from keyframe data on UE side |
| Tracks | Re-created from keyframe property tags |

**Key concern**: Keyframe re-send on reconnect. For a scene with 5000 keyframes across 50 animated objects, this could be ~500 packets (at 10 keys/packet) or ~200 KB. Mitigation: priority queue during reconnect snapshot, interleave with other snapshot packets.

---

## 8. Performance Analysis

### 8.1 — Keyframe Traffic Volume

| Scenario | Objects | Keys/Object | Total Keys | Packet Size | Packets | Total Data |
|----------|---------|------------|------------|-------------|---------|------------|
| Simple scene | 5 | 50 | 250 | 56B (single key) | 250 | 14 KB |
| Moderate scene | 20 | 100 | 2000 | 200B (4 keys) | 500 | 100 KB |
| Complex scene | 50 | 200 | 10000 | 500B (10 keys) | 1000 | 500 KB |
| Heavy scene | 100 | 500 | 50000 | 500B (10 keys) | 5000 | 2.5 MB |

**Snapshot cost** (initial connect): 14 KB to 2.5 MB. Acceptable for TCP (takes 0.1–2 seconds at 1 MB/s).

**Incremental cost** (during editing): Typically 1–10 keys per edit → 1 tiny packet. Negligible.

### 8.2 — Timeline Update Frequency

| Mode | Frequency | Notes |
|------|-----------|-------|
| Scrubbing | ~5 Hz | User scrubs through timeline |
| Playback | 10–20 Hz | Throttled — frame-accurate not needed for visual sync |
| Idle | 0 Hz | No frame changes |

Timeline updates during playback: ~10 packets/sec × 32 bytes = 320 bytes/sec. Negligible.

### 8.3 — Playback Sync Frequency

| State | Frequency | Notes |
|-------|-----------|-------|
| Play/Stop/Pause | Event-driven | Only on user action |
| State poll | 1 Hz | Heartbeat-like state confirmation |

Playback sync: < 1 packet/sec average. Negligible.

### 8.4 — Camera Sync Frequency

| Parameter | Frequency | Notes |
|-----------|-----------|-------|
| Active camera | Event-driven | On selection change |
| Camera parameters | Event-driven | On parameter edit |
| Initial snapshot | Once per reconnect | Single packet |

Camera sync: negligible traffic.

### 8.5 — Total Estimated Traffic

| Source | Bandwidth | Latency Sensitivity | Notes |
|--------|-----------|---------------------|-------|
| Timeline (playback) | ~320 B/s | Low (+100ms acceptable) | Visual sync only |
| Timeline (editing) | ~160 B/s | Medium (+50ms acceptable) | Scrubbing response |
| Playback state | ~0 B/s average | Medium (+100ms) | Event-driven |
| Camera | ~0 B/s average | Low (+200ms) | Event-driven |
| Keyframe incremental | ~500 B/s peak | Low (+500ms) | Bulk data, not real-time |
| Keyframe snapshot | ~500 KB burst | Low (+2s) | Reconnect only |
| **Total (steady state)** | **~500 B/s** | — | Primarily timeline |
| **Total (snapshot)** | **~2.5 MB burst** | — | Reconnect only |

### 8.6 — Batching Opportunities

| Scenario | Strategy | Benefit |
|----------|----------|---------|
| Multiple keyframes created at once | Batch into single packet (N × keyframe payload) | 10:1 reduction in packet count |
| Keyframe snapshot on reconnect | Send ALL keys in large batched packets | 50:1 reduction for complex scenes |
| Timeline state during playback | Reduce to 10 Hz from theoretical 60 Hz | 6:1 reduction |
| Multiple camera params changed | Single packet with all parameters | Avoids 8 separate packets |

---

## 9. Recommended Packet Architecture

### 9.1 — Type Allocation

The reserved range 0x13–0x1F provides 13 available type slots. Phase 7 proposes using 6 of them:

| Packet Type | Value | Phase | Payload Size |
|-------------|-------|-------|--------------|
| PT_Timeline | 0x13 | 7B | ~32 bytes |
| PT_PlaybackState | 0x14 | 7C | ~18 bytes |
| PT_ActiveCamera | 0x15 | 7D | ~18 bytes |
| PT_CameraDef | 0x16 | 7D | ~53 bytes |
| PT_Keyframe | 0x17 | 7E | Variable (batch) |
| PT_SequencerOp | 0x18 | 7F | ~40 bytes |

### 9.2 — Versioning Strategy

**Option A**: Same protocol version, capability-gated (recommended).

- Protocol version remains V5.
- Animation features gated by capability negotiation bits (extending the Phase 9 capability system).
- Old Blender with new UE: UE ignores unknown packets (types fall in the reserved range 0x13–0x18 which old UE silently skips).
- New Blender with old UE: Old UE silently ignores 0x13–0x18. Blender gracefully degrades (no anim sync).
- **Advantage**: No protocol version bump needed. Backward compatible by design.
- **Disadvantage**: Old UE cannot declare "I have Sequencer but no keyframe support" at sub-type granularity.

**Option B**: Bump protocol version to V6.

- All animation packets require V6.
- Capability negotiation includes V6 support flag.
- **Advantage**: Clean version boundary. Simpler feature gating.
- **Disadvantage**: Requires version constants on both sides. Old UE cannot gracefully degrade — it would need V6 awareness to accept non-animation packets.

**Recommendation**: **Option A** — capability gating. Reuse the existing capability negotiation infrastructure (Phase 9 Stage 2). Add animation capability bits. Keep protocol V5.

### 9.3 — Packet Ownership

| Domain | Direction | Primary Owner | Secondary |
|--------|-----------|---------------|-----------|
| Timeline | Blender → UE (primary) | Blender scene | UE Sequencer (optional reverse) |
| Playback | Bidirectional | Configurable by user | — |
| Camera | Blender → UE (primary) | Blender scene.camera | — |
| Keyframe | Blender → UE | Blender FCurve data | — |
| Sequencer op | Blender → UE | Blender action/anim data | — |

**Primary direction rationale**: Blender is the animation authoring environment. UE Sequencer is the display/render target. Bidirectional playback control is useful (play/pause from UE timeline), but keyframe data flows one way.

### 9.4 — Compatibility Matrix

| Blender → UE | Old UE (no Phase 7) | New UE (Phase 7) |
|--------------|---------------------|------------------|
| Old Blender (no Phase 7) | ✅ Full existing sync | ✅ Existing sync works |
| New Blender (Phase 7) | ✅ Existing sync works; anim packets silently ignored | ✅ Full anim sync |

| UE → Blender | Old Blender (no Phase 7) | New Blender (Phase 7) |
|--------------|--------------------------|-----------------------|
| Old UE (no Phase 7) | ✅ Full existing sync | ✅ Existing sync works |
| New UE (Phase 7) | ✅ Existing sync works; reverse anim packets silently ignored by old Blender | ✅ Full bidirectional |

### 9.5 — Relationship to Existing Transform Replication

Keyframe replication is **complementary** to transform replication, not a replacement:

| Feature | Transform Replication (Existing) | Keyframe Replication (Phase 7) |
|---------|----------------------------------|-------------------------------|
| When used | Every tick during live session | On keyframe edit in Blender |
| Data | Per-frame pose (location/rotation/scale) | Keyframe curve data (time-value pairs) |
| Granularity | 81 bytes/object/tick | ~40 bytes/keyframe |
| UE behavior | Interpolates between received poses | Populates Sequencer tracks |
| Purpose | Real-time visual sync | Persistent animation data sync |
| Coexistence | Active while anim keys are being authored | Active during keyframe editing |

**Design rule**: Transform replication continues to run during animation editing. Keyframe replication does NOT replace transform streaming — it provides the persistent keyframe data that Sequencer uses to reconstruct the animation.

---

## 10. Implementation Phases

### Phase 7A — Scope Lock (This Document)

| Deliverable | Effort |
|-------------|--------|
| Architecture document | 1–2 days |
| STATUS.md update | 0.5 day |
| **Total** | **1–2 days** |

### Phase 7B — Timeline Sync

| Task | Effort |
|------|--------|
| Blender: observe `frame_current`, `frame_start`, `frame_end` | 1 day |
| Blender: serialize `PT_Timeline` packets | 0.5 day |
| Blender: integrate into `check_updates()` cadence | 0.5 day |
| UE: parse `PT_Timeline` in `ProcessBinaryPacket` | 0.5 day |
| UE: `HandleTimeline()` — set Sequencer position | 1 day |
| UE: sequence tracker for timeline replays | 0.5 day |
| Tests: standalone wire format + replay | 1 day |
| Tests: UE runtime (requires Sequencer) | 1 day |
| **Total** | **5–6 days** |

### Phase 7C — Playback Sync

| Task | Effort |
|------|--------|
| Blender: detect playback state (heuristic via timer) | 1 day |
| Blender: serialize `PT_PlaybackState` packets | 0.25 day |
| UE: parse `PT_PlaybackState` in `ProcessBinaryPacket` | 0.25 day |
| UE: `HandlePlaybackState()` — Play/Pause/Stop | 1 day |
| UE→Blender: optional reverse playback sync | 1 day |
| Tests | 1 day |
| **Total** | **3–4 days** |

### Phase 7D — Camera Sync

| Task | Effort |
|------|--------|
| Blender: detect camera create/delete (reuse existing lifecycle) | 0.5 day |
| Blender: observe `scene.camera` changes | 0.5 day |
| Blender: serialize `PT_ActiveCamera` + `PT_CameraDef` | 0.5 day |
| UE: `HandleActiveCamera()` — set view target | 1 day |
| UE: `HandleCameraDef()` — apply cine camera params | 1 day |
| UE: spawn `ACineCameraActor` for camera creates | 1 day |
| Tests | 1 day |
| **Total** | **4–5 days** |

### Phase 7E — Keyframe Replication

| Task | Effort |
|------|--------|
| Blender: FCurve scanning and diff engine | 2 days |
| Blender: property tag system | 0.5 day |
| Blender: keyframe change detection (version hash heuristic) | 1 day |
| Blender: `PT_Keyframe` serialization (single + batch) | 1 day |
| Blender: snapshot on reconnect (send all keyframes) | 1 day |
| UE: `HandleKeyframe()` — store in MovieScene channels | 2 days |
| UE: keyframe replay + snapshot reconstruction | 1 day |
| Tests: wire format, batching, snapshot, incremental | 2 days |
| **Total** | **10–12 days** |

### Phase 7F — Sequencer Integration

| Task | Effort |
|------|--------|
| UE: LevelSequence creation from keyframe data | 1 day |
| UE: `HandleSequencerOp()` — create/remove tracks | 1 day |
| UE: possessable binding management | 1 day |
| UE: camera cut track creation | 0.5 day |
| Blender: detect action/anim lifecycle (action create/delete) | 1 day |
| Blender: `PT_SequencerOp` serialization | 0.5 day |
| Tests | 1 day |
| **Total** | **5–6 days** |

### Phase 7G — Validation & Closeout

| Task | Effort |
|------|--------|
| Stress testing: 50 objects × 200 keyframes | 2 days |
| Replay determinism verification (extend Phase 6H) | 1 day |
| Regressions: all existing tests still pass | 0.5 day |
| Documentation | 1 day |
| **Total** | **4–5 days** |

### Total Estimated Effort: 32–40 days

---

## 11. Gap Analysis

### 11.1 — Critical Gaps (Must Resolve Before Implementation)

| # | Gap | Impact | Resolution |
|---|-----|--------|------------|
| G1 | No Blender playback state API | Cannot reliably detect Play/Pause | Workaround: polling heuristic based on timer interval and frame_current rate of change |
| G2 | No keyframe change callback | Cannot efficiently detect keyframe edits | Workaround: version hash on action data, poll at lower frequency than transform sync |
| G3 | No Sequencer at UE runtime | Timeline sync requires editor ISequencer API | Runtime fallback: use ULevelSequencePlayer::SetPlaybackPosition if no editor |
| G4 | Frame mapping (Blender → UE) | Blender frames are integers; UE uses FFrameNumber + TickResolution | Design choice: 1:1 mapping at display rate. Blender frame N → UE display frame N. |
| G5 | Camera actor lifecycle | Blender camera objects → ACineCameraActor in UE. How to distinguish camera from mesh? | Existing primitive type system: assign PRIMITIVE_CAMERA type to camera objects. |

### 11.2 — Moderate Gaps

| # | Gap | Impact | Resolution |
|---|-----|--------|------------|
| G6 | Keyframe snapshot size | Up to 2.5 MB for heavy scenes | Batch keys; send during reconnect before PT_EndSnapshot; compress if needed |
| G7 | Reverse playback sync (UE→Blender) | UE Sequencer play can't drive Blender playback | Defer to Phase 7C.2. Initial implementation: Blender→UE only. |
| G8 | Undo/redo interaction | Blender undo may revert keyframes without notification | Re-scan after undo (detect via depsgraph or timer). Acceptable latency. |
| G9 | Multiple Blender actions | Blender objects can have multiple actions via NLA | Defer NLA support. Track only the active action. |

### 11.3 — Minor Gaps

| # | Gap | Impact | Resolution |
|---|-----|--------|------------|
| G10 | Camera cut blending | UE camera cuts (cross-fade) not needed for direct sync | Simple cut (no blend). |
| G11 | Sub-frame keyframe accuracy | Blender has no sub-frame keyframes | FrameNumber is int. Sub-frame accuracy not needed. |
| G12 | Property tag extensibility | Custom properties don't have predefined tags | Extend tag system with "use string path" flag for unknown properties. |

### 11.4 — Existing Infrastructure Gaps

The existing system has no animation-related gaps because there is no animation code. All gaps are greenfield design decisions enumerated above.

---

## 12. Risks & Mitigations

| Risk | Probability | Impact | Mitigation |
|------|-------------|--------|------------|
| **R1**: Blender FCurve scanning is too slow for real-time sync | Medium | High (sync delay) | Poll FCurves at reduced cadence (5 Hz vs 60 Hz transform). Use version hash to skip unchanged actions. |
| **R2**: UE Sequencer API is editor-only; runtime level sequences have limited track editing | High | High (must support both) | Implement two code paths: `ISequencer` for editor (full CRUD), `ULevelSequencePlayer` + MovieScene direct API for runtime (read-only playback). |
| **R3**: Packet type 0x13–0x18 conflicts with future features | Low | Low | Reserve these types for animation in the protocol spec. New features use 0x19+ or extended range. |
| **R4**: Keyframe snapshot floods reconnect | Medium | Medium | Prioritize timeline/camera packets first, then batch keyframes. Use PT_EndSnapshot gating (existing mechanism). |
| **R5**: Animation data creates O(n²) replay complexity | Low | Medium | Extend existing replay domains with animation-specific entry types. Use same 4096-entry buffer cap. |
| **R6**: Undo in Blender creates inconsistent keyframe state | Medium | Low | Accept eventual consistency. Re-scan all keyframes after undo event (detect via depsgraph). |
| **R7**: Sequencer UI not available in UE runtime builds | High | Medium | Phase 7F (Sequencer Integration) is editor-only for track creation. Runtime can play pre-existing sequences. |

---

## 13. Acceptance Criteria

### 13.1 — Phase 7A (Scope Lock)

| # | Criterion | Verification |
|---|-----------|-------------|
| A1 | Architecture document published | `Docs/Architecture/52-phase7-animation-sequencer-scope-lock.md` exists |
| A2 | STATUS.md updated with Phase 7 entry | Phase 7 shown as ACTIVE/SCOPE LOCK |
| A3 | Packet types reserved in protocol doc | Types 0x13–0x18 documented as reserved for animation |
| A4 | In-scope/out-of-scope defined | Clear boundary between Phase 7 and future work |
| A5 | Risks documented | All identified risks in §12 |

### 13.2 — Phase 7B (Timeline Sync)

| # | Criterion | Verification |
|---|-----------|-------------|
| B1 | `PT_Timeline (0x13)` parser on UE | Test: valid packet parsed, invalid rejected |
| B2 | Blender sends frame_current at ~10 Hz during playback | Test: packet capture shows periodic timeline updates |
| B3 | UE Sequencer setlocalTime matches Blender frame | Test: frame comparison after timeline packet |
| B4 | Replay: monotonic sequence dedup | Test: stale timeline packets rejected |
| B5 | Reconnect: frame position restored | Test: disconnect → reconnect → frame matches |
| B6 | Frame range sync | Test: frame_start/frame_end match between sides |

### 13.3 — Phase 7C (Playback Sync)

| # | Criterion | Verification |
|---|-----------|-------------|
| C1 | `PT_PlaybackState (0x14)` parser on UE | Test: valid packet parsed, invalid rejected |
| C2 | Blender play triggers UE Play() | Test: playback state observed on UE side |
| C3 | Blender pause triggers UE Pause() | Test: same |
| C4 | Blender stop triggers UE Stop() | Test: same |
| C5 | Loop count replicated | Test: loop=infinite/once/N-times match |
| C6 | Stale suppression via sequence tracker | Test: duplicate Play packets rejected |

### 13.4 — Phase 7D (Camera Sync)

| # | Criterion | Verification |
|---|-----------|-------------|
| D1 | `PT_ActiveCamera (0x15)` parser on UE | Test: valid packet parsed |
| D2 | `PT_CameraDef (0x16)` parser on UE | Test: all camera fields decoded |
| D3 | Camera create spawns ACineCameraActor | Test: new Blender camera → ACineCameraActor in scene |
| D4 | Camera switch sets view target | Test: scene.camera change → UE view target updated |
| D5 | Camera params applied to UCineCameraComponent | Test: FOV, focal, sensor, focus match Blender values |
| D6 | Camera delete removes camera actor | Test: Blender camera delete → actor despawned |

### 13.5 — Phase 7E (Keyframe Replication)

| # | Criterion | Verification |
|---|-----------|-------------|
| E1 | `PT_Keyframe (0x17)` parser on UE | Test: single keyframe parsed; batch parsed |
| E2 | Transform keyframes → UMovieSceneTransformTrack channels | Test: LOC_X/Y/Z, ROT_X/Y/Z, SCL_X/Y/Z stored correctly |
| E3 | Visibility keyframes → UMovieSceneVisibilityTrack | Test: visibility key value matches |
| E4 | Camera property keyframes → float channels | Test: FOV, focal length, focus distance stored |
| E5 | Keyframe add creates new key in channel | Test: key count +1 |
| E6 | Keyframe update modifies existing key | Test: same frame, new value |
| E7 | Keyframe delete removes key | Test: key count -1 |
| E8 | Clear all removes all keys for property | Test: channel empty |
| E9 | Batch: N keyframes in single packet | Test: all N stored |
| E10 | Snapshot: all keyframes re-sent on reconnect | Test: post-reconnect state matches pre-disconnect |
| E11 | Stale suppression via sequence tracker | Test: stale PT_Keyframe rejected |

### 13.6 — Phase 7F (Sequencer Integration)

| # | Criterion | Verification |
|---|-----------|-------------|
| F1 | `PT_SequencerOp (0x18)` parser on UE | Test: valid packet parsed |
| F2 | CreateSequence creates ULevelSequence asset | Test: sequence exists in world |
| F3 | CreateTrack adds track to MovieScene | Test: track type matches property tag |
| F4 | RemoveTrack removes track | Test: track count -1 |
| F5 | Possessable binding created for object | Test: binding exists in MovieScene |
| F6 | Camera cut track created when camera changes | Test: cut section matches active camera timing |

### 13.7 — Phase 7G (Validation & Closeout)

| # | Criterion | Verification |
|---|-----------|-------------|
| G1 | All existing regression tests PASS | Pre-Phase 7 test suites: 100% PASS |
| G2 | Replay determinism: animation domains hash-verified | 6H-style determinism check includes animation domains |
| G3 | Stress: 50 objects × 200 keyframes without crash | Memory stable; all keys replicated |
| G4 | Stress: reconnect during active playback | State consistent after reconnect |
| G5 | Stress: rapid keyframe edit storm (1000 ops in 1s) | Queue does not overflow; all keyframes eventually consistent |
| G6 | Standalone tests for ALL acceptance criteria | Each acceptance criterion has a test (or documented skip with reason) |
| G7 | Diagnostic counters implemented (all new domains) | Timeline/Keyframe/Camera counters visible in DumpState |

---

## 14. In-Scope / Out-of-Scope / Deferred Summary

### In-Scope (Phase 7)

| Feature | Phase | Priority |
|---------|-------|----------|
| Blender→UE timeline position sync | 7B | High |
| Blender→UE playback state sync (Play/Pause/Stop) | 7C | High |
| UE→Blender reverse playback control | 7C.2 | Medium |
| Active camera sync (scene.camera → ViewTarget) | 7D | High |
| Camera parameter sync (FOV, focal, sensor, focus, aperture) | 7D | High |
| Blender camera spawn → ACineCameraActor | 7D | High |
| Transform keyframe replication | 7E | High |
| Visibility keyframe replication | 7E | Medium |
| Camera property keyframe replication | 7E | Medium |
| Keyframe batching | 7E | High |
| LevelSequence creation from keyframe data | 7F | High |
| Track creation (Transform, Visibility, Float) | 7F | High |
| Possessable binding management | 7F | High |
| Camera cut track creation | 7F | Medium |
| Snapshot/replay for all new domains | 7B–7F | High |
| Diagnostic counters for all new domains | 7B–7F | Medium |

### Out-of-Scope (Phase 7)

| Feature | Reason |
|---------|--------|
| Skeletal animation / armature / bone sync | Requires pose-space transform system. Post-v1.0 feature. |
| NLA track editing | Complex layered animation model. Deferred. |
| Shape keys / blend shapes | Different data model (morph targets). Not Sequencer-track compatible. |
| FCurve modifiers (generators, envelopes, noise) | Procedural curves not representable as keyframe data. |
| Drivers | Expression-based animation. Requires expression parser. |
| Sequencer strip editing (cuts, transitions, effects) | Editor-UI specific. Not data-flow. |
| Audio track sync | Different data type (audio samples, not keyframes). |
| Particle system keyframe sync | Requires particle system mapping. Out of scope. |
| Shader/material animation | Requires material parameter mapping. Deferred. |
| Grease pencil animation | Different data model (strokes, not transforms). |
| Physics simulation sync | Simulation nondeterminism. Cannot replicate deterministically. |
| Cloth/hair simulation | Same as physics — simulation-dependent. |
| Rigid body simulation | Same as physics. |
| Sequencer spawnable binding | All objects exist in scene. Possessable binding sufficient. |
| Post-process settings sync | Not mapped to Blender camera properties. |
| Multi-camera switcher / camera rig | Complex UE-specific feature. Deferred. |

### Deferred (Future Phase)

| Feature | Target | Rationale |
|---------|--------|-----------|
| Skeletal animation sync | Phase 8+ | Requires bone hierarchy, pose-space transforms, skinning data |
| NLA track editing | Phase 8+ | Requires action stack model, blending, mute/solo |
| Shape key/blend shape sync | Phase 8+ | Needs morph target data model |
| FCurve modifier bake | Phase 8+ | Bake to keyframes on send |
| Driver bake | Phase 8+ | Evaluate driver and send as baked keyframe |
| Multi-sequence management | Phase 8+ | Multiple LevelSequences for multiple Blender actions |
| Full bidirectional camera sync | Phase 8+ | UE Sequencer camera changes → Blender |
| Sequencer spawnables | Phase 8+ | Object lifecycle ownership model |
| Post-process camera settings | Phase 8+ | Exposure, DOF, color grading mapping |
| Timecode / SMPTE sync | Phase 8+ | Broadcast industry standard |
| Multi-user collaborative anim sync | Future | Requires locking/merge model |

---

## Appendix A — References

| Document | Relevance |
|----------|-----------|
| `Docs/Architecture/18-phase6-scope-lock.md` | Phase 6 boundaries — explicitly fences off animation sync |
| `Docs/Architecture/43-phase7A-static-mesh-identity-scope-lock.md` | Original Phase 7 replanning — animation deferred to Phase 8+ |
| `Docs/Architecture/45-phase7C-geometry-modifier-pipeline-scope-lock.md` | Explicitly excludes animation sync |
| `Docs/Architecture/46-phase8-high-performance-streaming-scope-lock.md` | Explicitly excludes animation sync |
| `Docs/Architecture/48-phase9-production-ecosystem-scope-lock.md` | Explicitly excludes animation sync |
| `Docs/_archive/00-consolidated-roadmap.md` | Original roadmap with PT_Timeline/Camera/Keyframe proposals |
| `Docs/Protocol/live_sync_v4.md` | V4 protocol — skeletal animation deferred |
| `Docs/CRITICAL_INVARIANTS.md` | Hard rules — no change to existing invariants |
| `Docs/ARCHITECTURE.md` | Current architecture — Phase 7 mapping update needed |

## Appendix B — Glossary

| Term | Definition |
|------|------------|
| **FCurve** | Blender's animation curve data structure. Maps frame number → value with interpolation. |
| **Keyframe** | A single control point on an FCurve: (frame, value, handles, interpolation type). |
| **LevelSequence** | UE asset containing a MovieScene timeline with tracks and keyframes. |
| **MovieScene** | UE data model for time-based animation data (tracks, sections, channels, keys). |
| **Possessable** | UE Sequencer binding to an existing actor in the world. |
| **Spawnable** | UE Sequencer binding that spawns a new actor. |
| **Sequencer** | UE editor tool for creating and editing Level Sequences. |
| **TickResolution** | UE MovieScene sub-frame precision (e.g. 24000 ticks/second for 24 FPS). |
| **DisplayRate** | UE MovieScene nominal frame rate for display purposes. |
| **ISequencer** | UE editor interface for Sequencer UI integration (editor-only). |
| **NLA** | Blender Non-Linear Animation — layered animation system with tracks and strips. |
| **Action** | Blender named animation data block containing FCurves. |
| **Property Tag** | Predefined 16-bit identifier for a Blender property path (avoids string overhead). |
