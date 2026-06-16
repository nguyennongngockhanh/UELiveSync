# Changelog

## [unreleased]

### Added
- Phase 7G Stage 4: Camera Transform Sync — validates CREATE + TRANSFORM + ACTIVE_CAMERA pipeline.
- Injector modes: `--create-transform-active` (one connection, 0.2s inter-packet sleeps), `--cameradef-only` (fresh connection, GUID from log), `--full-separated` (combined lifecycle with 3s pause).
- Phase 7G Stage 5: Camera Sequencer Binding + CameraCutTrack Integration.
- `EnsureCameraSequencerBinding()` helper in HandleActiveCamera — creates possessable binding + CameraCutTrack + CameraCutSection in asset-backed LevelSequence.
- 6 new counters: `ActiveCameraBindingCreated/Exists`, `ActiveCameraCutTrackCreated/Applied/Skipped`, `ActiveCameraSeqSaved`.
- Diagnostic markers: `[CAMERA][SEQ_BIND]`, `[CAMERA][SEQ_BIND_SKIP]`, `[CAMERA][CUT_TRACK]`, `[CAMERA][CUT_APPLY]`, `[CAMERA][CUT_SKIP]`, `[CAMERA][CUT_SAVE]`.
- `CAP_SUPPORTS_CAMERA_SEQ_BIND = 0x200` capability bit.
- HandleActiveCamera restructured: camera resolution unblocked before CVar gate; EnsureCameraSequencerBinding called unconditionally.
- 34 new source tests for helper, markers, counters, capability bit, HandleActiveCamera integration.
- Runtime validation: all 4 markers confirmed (SEQ_BIND, CUT_TRACK, CUT_APPLY, CUT_SAVE). 235/235 tests passing.
- Injector timing fix: switched from fresh-socket-per-packet to one-connection with 0.2s sleeps to avoid SeenThisTick dedup skipping TRANSFORM after CREATE in same UE tick. Fresh sockets cause UE `StopNetworkThread` exit, preventing subsequent connections.
- Injector lifecycle race documented: combined CREATE+TRANSFORM+ACTIVE+CAMERA_DEF on one connection can drop CAMERA_DEF due to socket close / queue timing; validated via separated modes.
- Runtime markers: `[CAMERA][CREATE]`, `[CAMERA][TRANSFORM_APPLY]`, `[CAMERA][TRANSFORM_CONVERGED]`, `[CAMERA][ACTIVE_RECV]`, `[CAMERA][VIEW_TARGET]` (VIEW_TARGET_FAIL in `-game` mode — GEditor null).
- Classification: `PASS_CAMERA_TRANSFORM_APPLY`.
- 26 new source tests for injector modes, timing, dedup documentation, and camera transform sync.
- Runtime validation confirmed on UE 5.7.4 port 57000: all 5 camera markers present, `--cameradef-only` and `--full-separated` pass. 201/201 total tests passing across all suites.
- Phase 7G Stage 3: PT_CameraDef (0x1B) — camera definition / parameter sync.
- `LSP_CamDef = 0x05` in `ELiveSyncPrimitiveType` enum — camera definition objects carry `FCameraDefPayload`.
- `FCameraDefPayload` (64 bytes): FGuid, focal length (f32), sensor dimensions (w/f32, h/f32), clip planes (start/f32, end/f32), ortho scale (f32), CameraFlags (u8, 3 bytes reserved).
- `HandleCameraDef()` applies `SetProjectionMode` (Perspective/Orthographic) based on `CameraFlags & 0x01`.
- Perspective mode: computes FOV from focal length and sensor width (`2 * atan(sensor_width / (2 * focal))`), sets `AspectRatio`.
- Orthographic mode: applies `OrthoWidth = Payload.OrthoScale`, `OrthoNearClipPlane`, `OrthoFarClipPlane`.
- Clip planes applied via `SetNearClipPlane` / `SetFarClipPlane` for both projection modes.
- Stale packet detection: rejects DEF packets received before corresponding ActiveCamera with `[CAMERA][DEF] Stale`.
- Non-object packets use `obj_count=0` for PT_CameraDef (V3+ validation).
- Blender addon: `sync.py` sends `PT_CameraDef` alongside `PT_Transform` in the camera update path; `network.py` adds `LSP_CamDef` to `PRIMITIVE_TYPE_MAP`.
- `PT_CameraDef = 0x1B` added to `kValidTypes`.
- 29 new tests: wire format (17), UE apply (19), reserved packet guard (7).
- 0x02 remains reserved/invalid. `-NullRHI` caveat: clip plane and viewport pilot require RHI; `FieldOfView` applies even on `-NullRHI`.
- Phase 7G Stage 2: Camera Actor Spawn + Active View Target Apply.
- `LSP_Camera = 0x05` in `ELiveSyncPrimitiveType` enum — camera objects spawn as `ACameraActor`.
- `HandleActiveCamera()` auto-spawns `ACameraActor` when GUID not in cache; tags with `LiveSync_GUID=<guid>`, registers in ActorCache.
- `HandleActiveCamera()` applies `SetActorLock` on all `FLevelEditorViewportClient` viewports via `GEditor->GetLevelViewportClients()`.
- CVar `UE.LiveSync.ActiveCamera.ApplyToViewport` (default 0) gates viewport pilot.
- `[CAMERA][ACTIVE_RECV/SPAWN/SPAWN_FAIL/VIEW_TARGET/VIEW_TARGET_SKIP/VIEW_TARGET_FAIL]` diagnostics at Log level.
- `ActiveCameraPacketsSpawned`, `ActiveCameraPacketsViewTargetFailed`, `ActiveCameraPacketsNotCamera` counters.
- 30 new tests: camera actor spawn (10), active view target apply (11), reserved packet guard (9).
- Changed `[CAMERA][ACTIVE_RECV]` and `[CAMERA] Stale packet` from Verbose to Log level for runtime observability.
- Phase 7F Stage 2: PT_PlaybackTransport (0x1A) packet — sends playback transport commands (SetFrame, Play, Pause, Stop) from Blender to UE.
- `EPlaybackTransportCommand` enum (SetFrame=0/Play=1/Pause=2/Stop=3) in SyncTypes.h.
- `FPlaybackTransportPayload` packed struct (6 bytes: command+frame_current+flags) with static_assert.
- `HandlePlaybackTransport()` in UELiveSyncSubsystem with [PLAYBACK][RECV/APPLY/SKIP/MALFORMED] diagnostics.
- `LastPlaybackTransportPayload` + `bHasPlaybackTransportState` + `LiveSyncSequenceFrameCurrent` storage members.
- SetFrame applies clamped frame to LiveSyncSequenceFrameCurrent; Play/Pause/Stop logged as PASS_TRANSPORT_STATE_ONLY.
- Blender `serialize_playback_transport()` and `send_playback_transport()` with obj_count=0 for V3+ validation.
- TCP injector: `tools/uelivesync_7f_playback_transport_client.py` (transport-only and full 5-packet flow modes).
- 27 new tests: wire format (11), UE apply (9), reserved packet guard (7).
- 0x1A added to kValidTypes; 0x02 remains reserved/invalid.
- Phase 7F Stage 1: PT_TimelineState (0x19) packet — sends Blender frame range + FPS to UE.
- PT_TimelineState applies frame range via `SetPlaybackRange` and display rate via `SetDisplayRate` on LiveSync LevelSequence.
- `FTimelineStatePayload` (20 bytes: frame_start, frame_end, frame_current, fps_num, fps_den) in SyncTypes.h.
- `HandleTimelineState()` in UELiveSyncSubsystem with [TIMELINE][RECV/APPLY/SKIP/MALFORMED] diagnostics.
- Blender `serialize_timeline_state()` and `PT_TimelineState` send alongside PT_Timeline in timeline change path.
- TCP injector for runtime validation: `tools/uelivesync_7f_timeline_state_client.py` (timeline-only and full 5-packet flow modes).
- 21 new tests: wire format (7), reserved packet guard (7), UE apply (7).
- 0x19 added to kValidTypes; 0x02 remains reserved/invalid.

### Changed
- Stage 10A.7A log-based playback validator (`tools/uelivesync_10a7a_validation.py`).
- Automated playback state validation for visibility channels 9/10 at frames 1/10/20.
- Added Blender 5.1+ slotted Action keyframe extraction (`_iter_action_fcurves_51`).
- Added `action.is_action_layered` detection in `_extract_keyframes()`.
- Added capability gating fallback when UE does not send `PT_CapabilityResponse`.
- Added 81 tests for Blender 5.1 keyframe extraction (transform + visibility channels).
- Added wrapped SequencerOp send path for CREATE_SEQUENCE / ADD_POSSESSABLE runtime setup.
- Added Stage 10A.5 active LevelSequence runtime helper (`tools/uelivesync_stage10a5_active_sequence.py`).
- Added 2 tests for SequencerOp packet wrap and reserved type guard.
- Stage 10B.1: Asset-backed LevelSequence at `/Game/UELiveSync/Sequences/LS_UELiveSync_Runtime` via `GetOrCreateLiveSyncLevelSequenceAsset()`.
- Stage 10B.2: Runtime asset sequence validation with TCP injector (`tools/uelivesync_10b_tcp_client.py`) and validator (`tools/uelivesync_10b_asset_sequence_validation.py`).
- Added `UPackage::SavePackage()` in `GetOrCreateLiveSyncLevelSequenceAsset()` to persist asset to disk.
- Added `[SEQ][ASSET_LOAD / ASSET_CREATE / ASSET_READY / ASSET_FAIL]` diagnostic markers.
- Stage 10B.3: UE Python asset load verification — `unreal.load_asset()` returns valid `LevelSequence` (PASS_LOAD_ONLY).
- Stage 10D.1: Sequencer Editor usability validation — `open_level_sequence()` success, binding/tracks/sections persist (PASS_EDITOR_DATA_ONLY).
- Fixed `NewObject` `NAME_None` → named `FName("LS_UELiveSync_Runtime")` for clean `FSoftObjectPath` resolution.
- Stage 10C.1: Persist applied sequencer data — `SaveLiveSyncLevelSequenceAsset()` called after successful keyframe apply.
- Upgraded UE Python inspection from PASS_LOAD_ONLY to PASS_BINDING_ONLY (binding_count=1, track types detected).
- Added `[SEQ][ASSET_DIRTY / ASSET_SAVE / ASSET_SAVE_FAIL / ASSET_SAVE_SKIP]` diagnostic markers.
- Added MTEX texture metadata sync for material slots.
- Added UE-side texture import/cache from MTEX paths.
- Added generated MID texture parameter application.
- Added UELiveSync master material for texture rendering.
- Added texture pipeline diagnostics and tests.
- FBX temp asset lifecycle diagnostics: `[FBX][TEMP_IMPORT/ASSIGN/CLEANUP/KEEP_PREVIOUS/DELETE_FAIL/UNIT_INVALID/SCALE_INVARIANT]`.
- FBX unit/scale invariant checks — invalid unit imports are rejected/preserved, actor and component scale stay at 1.

### Changed
- FBX mesh sync now uses unique temp StaticMesh asset per sync instead of reimport-over-existing.
- Temp mesh assignment is validated before applying; previous temp mesh cleaned up after success.
- Blender FBX export policy: `global_scale=1.0`, `apply_scale_options='FBX_SCALE_UNITS'`, `bake_space_transform=False`.
- Material generated MID is restored after FBX mesh assignment.
- Phase 10J tests updated for current temp import lifecycle architecture (18/18 PASS).

### Fixed
- Fixed UE 5.7.4 FBX reimport meter-size regression — no longer uses reimport-over-existing path.
- Fixed material sync losing generated MID after FBX mesh refresh.
- Fixed scale invariant regressions — actor/component scale preserved at 1.
- Preserved mesh/scale invariants during material/texture sync.
- Hardened unsupported/missing texture path handling.

### Validated
- Phase 7F Stage 2: Runtime validated PT_PlaybackTransport (0x1A) — [PLAYBACK][RECV] + [PLAYBACK][APPLY] confirmed with SetFrame frame=48 (clamped=48).
- Phase 7F Stage 2: Full 5-packet keyframe regression clean (applied=11 miss=0 unsupp=0).
- Phase 7F Stage 2: Visibility keyframes on channels 9/10 applied through UE BoolTrack path.
- Phase 7F Stage 2: All 96/96 regression tests pass (27 new + 69 existing).
- Validated active LevelSequence runtime path with PT_Keyframe applied=11, miss=0, unsupp=0.
- Validated visibility channels 9 and 10 apply through UE bool keyframe path.
- Confirmed PT_Transform remains 0x01 and 0x02 remains reserved/invalid.
- Stage 10B.2: Validated asset-backed LevelSequence persistence to disk (4055 bytes).
- Stage 10B.2: All 59/59 regression tests pass (Stage 10A.2 + 10A.5A).
- Stage 10B.3: Validated `unreal.load_asset()` returns non-null `LevelSequence` (PASS_LOAD_ONLY).
- Stage 10B.3: All 64/64 regression tests pass (added 10B.3 with 5/5).
- Stage 10D.1: Validated Sequencer Editor usability — `open_level_sequence()` succeeds, binding + tracks + sections all persist (PASS_EDITOR_DATA_ONLY).
- Stage 10D.1: All 75/75 regression tests pass (added 10D.1 with 9/9).
- Stage 10C.1: Validated `unreal.load_asset()` returns binding_count=1 with TransformTrack + BoolTrack sections (PASS_BINDING_ONLY).
- Stage 10C.1: All 66/66 regression tests pass (added 10C.1 with 7/7).
- Phase 7F Stage 1: Runtime validated PT_TimelineState (0x19) — [TIMELINE][RECV] + [TIMELINE][APPLY] confirmed with frame range=[1,120] fps=24/1.
- Phase 7F Stage 1: Full 5-packet keyframe regression clean (applied=11 miss=0 unsupp=0).
- Phase 7F Stage 1: All 96/96 regression tests pass (21 new + 75 existing).

### Known notes
- Keyframe runtime requires `prefs.keyframe_sync=True`.
- UE keyframe apply requires an active LevelSequence and binding.
- `-NullRHI` should not be used for networking/runtime validation; use normal editor or `-RenderOffScreen`.

### Known limitations
- Packed Blender images are not imported.
- Complex material node graphs are not traversed.
- Alpha/Normal visual support remains limited/deferred.

## [0.2.4] - 2026-06-10

### Added
- Runtime validation for visibility hide/show, MESH-parent hierarchy, real FBX import/reimport, and visibility keyframe channels 9–10.
- Sequencer state reset on reconnect to prevent stale SEQOP rejection after reconnect.
- Buyer documentation pack (quick start, system requirements, known limitations, license FAQ, support policy).
- Updated Linux runtime validation launch profile (bare UE command instead of `CEF_DISABLE_GPU=1`).

### Fixed
- Sequencer reconnect bug where stale sequencer state caused CREATE_SEQUENCE and ADD_POSSESSABLE rejection.
- Visibility keyframe applied counter now correctly reports applied keys.

### Notes
- v0.2.4 is a patch release with runtime validation closure and sequencer reconnect fix.
- Runtime protocol and packet formats are unchanged from v0.2.3.
- v0.2.0, v0.2.1, v0.2.2, and v0.2.3 remain published and untouched.

## [0.2.3] - 2026-06-10

### Fixed
- Added missing `get_object_material_slots` fallback import in `Blender_Addon/sync.py` for standalone/background execution paths.

### Added
- Runtime validation documentation: `Docs/runtime_validation.md`.
- Full runtime validation status entry documenting the stable UE launch profile.

### Notes
- v0.2.3 is a hotfix/docs patch release.
- Runtime protocol and packet formats are unchanged from v0.2.2.
- v0.2.0, v0.2.1, and v0.2.2 remain published and untouched.
- Recommended runtime validation profile is UE windowed mode with `CEF_DISABLE_GPU=1`.
- `-RenderOffScreen -NoCEF` is not recommended for LiveSync runtime validation because Tick/FTSTicker did not execute in that mode.

## [0.2.2] - 2026-06-09

### Added
- Commercial source-available root license.
- Commercial end-user license agreement: `EULA.md`.
- Blender addon GPL-2.0-or-later component license notice: `Blender_Addon/LICENSE`.

### Changed
- README and release notes now document commercial/source-available licensing.
- Release package now includes commercial licensing terms required for paid distribution.

### Notes
- v0.2.0 and v0.2.1 remain published and untouched.
- v0.2.2 is a license/documentation patch release.
- Runtime sync code is unchanged from v0.2.1.

## [0.2.1] - 2026-06-09

### Added
- Cross-platform installer helper: `install_uelivesync.py`.
- `INSTALL.md` with Windows, Linux, Linux Flatpak, and macOS install instructions.
- Installer source/functional tests for dry-run, backup, force, and destination-exists safety.

### Fixed
- Installer overwrite safety before release: existing destinations now require `--backup` or `--force`.
- `--backup` now preserves existing installs as `.bak-YYYYMMDD-HHMMSS`.
- `--dry-run` performs no filesystem writes.

### Notes
- v0.2.0 remains published and untouched.
- v0.2.1 is a patch release to include the installer helper in the release tag/source archive.
- Runtime sync code is unchanged from v0.2.0.

## [0.2.0] - 2026-06-09

### Added
- FBX mesh handoff production path for selected Blender meshes.
- UE StaticMesh import with StaticMeshActor spawn/update by LiveSync GUID.
- Material slot count logging for FBX imports.
- FBX asset lifecycle diagnostics: created vs replaced imported asset.
- Rename/new asset path diagnostic for possible orphaned imported assets.
- Blender burst packet diagnostics: `burst_packet_count` and `burst_packet_count_peak`.

### Fixed
- Removed redundant `RegisterComponent()` calls in FBX reimport path.
- Fixed FBX scene unit conversion so Blender meters import as UE centimeters.
- Extracted and hardened the FBX importer implementation.

### Validated
- Stage 5 rename/new asset path runtime validation PASS.
- 2m Blender cube imports as 200 × 200 × 200 UE units.
- Large scene streaming benchmark PASS for 50, 100, 250, and 500 objects.
- Queue depth remained 0 and dropped packets remained 0 in Phase 8 benchmark.

### Known limitations
- Imported FBX StaticMesh assets are not automatically deleted.
- Renaming a Blender object can leave the old imported asset orphaned; a diagnostic warning is emitted.
- Reimport may overwrite user-edited imported StaticMesh/material settings.
- PT_Mesh procedural mesh sync remains experimental/debug.
- UE runtime LiveSync requires a GPU/RHI session; NullRHI disables packet processing.

## 2026-05-28 — Decouple Semantic Domains from Transform Gate

### Problem

All semantic event detections (rename, visibility, hierarchy) were inside `if transforms_different(...)` (`sync.py:1068-1180`). This meant these events only emitted when the object's transform also changed.

| Domain | Status Before | Status After |
|--------|--------------|--------------|
| Rename | Only detected on object move | Always detected |
| Visibility | Only detected on object move | Always detected |
| Hierarchy | Only detected on object move | Always detected |
| Collection | Already outside gate | Unchanged |

### Changes

**`Blender_Addon/sync.py`** — moved rename, visibility, hierarchy detection from inside `if transforms_different()` to independent indent-8 scope. Each domain now evaluates every tick:
- Visibility: `obj.hide_get()` diff against `_last_visibility_state`
- Rename: `obj.name` diff against `_last_object_names`
- Hierarchy: `get_parent_guid(obj)` diff against `_last_parent_guid`

Added `[DIAG]` logging for all domains.

**`UE_Plugin/.../UELiveSyncSubsystem.cpp`** — added diagnostic logging:
- `[VISIBILITY][DIAG]` post-apply (actor name + hidden state)
- `[COLLECTION][DIAG]` packet-received + post-apply (registry member count)

**`Docs/KNOWN_BAD_PATTERNS.md`** — added entry #11: "Transform-Gated Semantic Event Detection" documenting the anti-pattern.

### Invariants Preserved

- GI-1 (GUID stable across rename) — unchanged hash derivation
- TF-4/TF-5 (transform authority) — transform path unchanged
- RN-1 (GRenamePersistentLabel authority) — UE side untouched
- HI-1 (parent stable) — hierarchy detection still uses `get_parent_guid()`
- CL-1 (collection idempotent) — collection detection unchanged
- No replay divergence — Blender-side detection only
- No packet format changes
- No networking changes
