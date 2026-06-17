# Changelog

## [unreleased]

### Added
- **Phase 7E Stage 10A.4 — Blender-to-UE Visibility BoolTrack E2E Validation**: New test file `tests/phase7e_stage10a4_blender_visibility_e2e.py` (73 tests) validates the full Blender-to-UE visibility bool keyframe pipeline end-to-end. Audit verified: Blender `_KEYFRAME_CHANNEL_MAP` (hide_viewport→channel 9, hide_render→channel 10), `network.py` constants (`KEYFRAME_CHANNEL_VISIBILITY_VIEWPORT=9`, `KEYFRAME_CHANNEL_VISIBILITY_RENDER=10`), `serialize_keyframe()` PT_Keyframe wire format, UE `HandleKeyframe` channel 9/10 dispatch to `UMovieSceneBoolTrack`, all BOOL diagnostic markers (`BOOL_TRACK_CREATE`, `BOOL_SECTION_CREATE`, `BOOL_KEY`, `BOOL_APPLY`, `BOOL_UNSUPPORTED`), `SyncTypes.h` visibility counters, wire format consistency (PT_Keyframe=0x17 unchanged), FCurve extraction paths (5.1+ slotted + legacy fallback), and architecture documentation alignment. Runtime injector `tools/uelivesync_stage10a3_booltrack_runtime.py` structurally validated. Static: 73/73 PASS. No C++ changes. No protocol changes. No tag change. **2642/2642 grand total.** Commit: `10d0366`. ✅
- **Phase 7E Stage 10E — Transform Keyframe E2E Runtime Validation (Runtime Complete)**: Runtime validated. Blender created Stage10E_TransformProbe with 27 transform keyframes (9 axes × 3 frames) → extracted FCurves via `_iter_action_fcurves_51` → sent CREATE_SEQUENCE → CREATE → ADD_POSSESSABLE → PT_Keyframe(×3) via raw TCP → UE applied all 27 keys. UE log: applied=27, missing_binding=0, unsupported_channel=0, Signal11=0, Signal6=0, SceneOutliner=0, DrawFrustum=0. UE process stable. Static: 45/45 PASS. Regression: 101/101 PASS. Classification: `PASS_PHASE7E_STAGE10E_TRANSFORM_KEYFRAME_RUNTIME`. Fix: Blender 5.1 keyframe_insert API changed — `data_path="location"` + `index=N` instead of `data_path="location[N]"`.
- **Phase 7E Stage 10F — Multi-object Transform + Visibility Sequencer Runtime**: Runtime validated. Created two Blender objects (Object A: 9 transform + 6 visibility keyframes; Object B: 9 transform only) → extracted FCurves via `_iter_action_fcurves_51` → sent CREATE_SEQUENCE → CREATE(A/B) → ADD_POSSESSABLE(A/B) → PT_Keyframe(A×3: 11 keys/channels 0-10, B×3: 9 keys/channels 0-8) via raw TCP with unique seq IDs 4000-4010 → UE applied all 60 keys (0 missing, 0 unsupported). UE log `/tmp/uelivesync-stage10f-ue-full.log`: visibility values span 0.0→1.0 across frames on channels 9+10, BoolTrack markers present (BOOL_TRACK_CREATE, BOOL_SECTION_CREATE, BOOL_KEY×6, BOOL_APPLY×6), multi-object bindings independent (Object A=185AB10E..., Object B=3A71ADC2...), crash counters all 0 (Signal11/6/SceneOutliner/DrawFrustum), UE process stable. **Blender 5.1 boolean keyframe fix**: `keyframe_insert` on `hide_viewport`/`hide_render` captures current property value — harness updated to temporarily set property → keyframe_insert → restore, ensuring correct 0.0/1.0 visibility values. Static tests created. Classification: `PASS_PHASE7E_STAGE10F_MULTI_OBJECT_TRANSFORM_VISIBILITY`.
- **Phase 7E Stage 10A.3 — BoolTrack Runtime Smoke**: Created runtime injector `tools/uelivesync_stage10a3_booltrack_runtime.py` — sends PT_Keyframe (0x17) packets with channels 9 (hide_viewport), 10 (hide_render), and unsupported channel 99 to a running UE editor, preceded by CREATE_SEQUENCE → CREATE_ACTOR → ADD_POSSESSABLE. Static test file `tests/phase7e_stage10a3_booltrack_runtime.py` (26 tests) verifies injector syntax, constants, channel semantics, wire format, and send order. **Runtime validation (fresh UE launch)**: BOOL_TRACK_CREATE=1, BOOL_SECTION_CREATE=1, BOOL_KEY=6, BOOL_APPLY=6, unsupp=1, Signal11=0, Signal6=0, SceneOutliner=0, DrawFrustum=0. Classification: `PASS_PHASE7E_STAGE10A3_BOOLTRACK_RUNTIME`. No C++ changes. Static: 26/26 PASS. Commit: `29218e2`.
- **Phase 7E Stage 10A.2 — Visibility BoolTrack Apply Refinement**: Added dedicated BOOL diagnostic markers (`[KEYFRAME][BOOL_APPLY/KEY/TRACK_CREATE/SECTION_CREATE/UNSUPPORTED]`) to HandleKeyframe channels 9–10 `UMovieSceneBoolTrack` path. New dedicated test file `tests/phase7e_stage10a2_booltrack_apply.py` with 32 tests: hide_viewport/hide_render key apply, mixed transform+visibility packet, missing binding safety, unsupported channel >10 safety, stale rejection before apply, visibility counter correctness, existing transform tests preserved. Total Stage 10A.2: 81/81 PASS (32 new + 49 existing). Build: 0 errors, 0 warnings. Static: 224/224 PASS. Audit: 27/27 PASS. Classification: `PASS_PHASE7E_STAGE10A2_BOOLTRACK_APPLY`. Channel semantics: ch9=hide_viewport, ch10=hide_render, same `UMovieSceneBoolTrack`, value `>= 0.5f` = visible. Existing `[KEYFRAME][VISIBILITY]` markers and counters preserved.
- **Manual E2E.5 — SceneOutliner Crash Isolation (Runtime Complete)**: Isolation injector `tools/uelivesync_e2e5_sceneoutliner_isolation.py` with modes `--idle-only` (A), `--create-only` (C), `--create-transform` (D), `--full` (E), `--hierarchy` (F), `--cameraguid`. Fresh UE per test. **Results:** A: PASS_NO_CRASH (UE idle, 60s, 0 Signal 11). C: FAIL — Signal 11 on camera CREATE (26 SSceneOutliner pairs). D: FAIL — Signal 11 on create+transform (26 pairs). E: FAIL — Signal 11 on full lifecycle (27 pairs). F: PASS_HIERARCHY_ATTACH_GUARD_RUNTIME — `SafeAttachLiveSyncActor` guards self/cycle attach (7 AttachToParent calls, 0 crashes). **Overall: PARTIALLY RESOLVED** — Signal 11 triggered by LiveSync camera CREATE (not UE idle). Hierarchy guard confirmed working. Static: 132/132 PASS. Crashes narrow to SceneOutliner tree rebuild on actor add. Docs: `manual-e2e-camera-crash-investigation.md` (E2E.5 runtime results), `STATUS.md`, `CHANGELOG.md`, `current-state-roadmap.md`. Injector fixed: `--hierarchy` mode now uses PT_CREATE with parent GUID (not PT_TRANSFORM) to exercise hierarchy path. 0x02 reserved/invalid. 0x10 unused.

### Fixed
- **Manual E2E.4 — Signal 6 + Signal 11 runtime revalidation**: Signal 6 (UDrawFrustumComponent::CreateSceneProxy / GetSelectionParent) confirmed fixed — frustum guard marker present, UE process stable during camera lifecycle. Signal 11 (SSceneOutliner::EnsureParentForItem recursion) crash CONFIRMED — UE engine bug in `libUnrealEditor-SceneOutliner.so`, not LiveSync code. Hierarchy guard (`SafeAttachLiveSyncActor`) was not exercised in this run (test camera had no parent). Classification: `FAIL_MANUAL_E2E_SCENE_OUTLINER_PARENT_GUARD`. 106/106 static tests PASS. Tag `manual-e2e-camera-crash-guard-stable` remains provisional. Crash log: `/tmp/uelivesync-manual-e2e-ue.log`.
- **Manual E2E.1 — Camera frustum crash guard**: Added `ConfigureLiveSyncCameraActor()` helper that suppresses frustum/editor-visualization components on LiveSync-spawned `ACameraActor` without disabling `UCameraComponent` or the camera actor. Called from both `HandleCreateObject()` camera spawn path and `HandleActiveCamera()` auto-spawn path. Prevents SIG 6 crash in `AActor::GetSelectionParent()` → `UDrawFrustumComponent::CreateSceneProxy()`. Diagnostics: `[CAMERA][FRUSTUM_GUARD]`, `[CAMERA][FRUSTUM_GUARD_SKIP]`, `[CAMERA][FRUSTUM_GUARD_FAIL]`. No protocol change. 0x02 remains reserved/invalid. 0x10 remains unused. 24/24 static tests PASS. Build: clean.
- Manual E2E.2 — Camera frustum crash runtime revalidation: UE 5.7.4 launched, camera lifecycle injector ran, UE process alive, no crash, all 6 required markers present (`[CAMERA][CREATE]`, `[CAMERA][FRUSTUM_GUARD]`, `[CAMERA][TRANSFORM_APPLY]`, `[CAMERA][ACTIVE_RECV]`, `[CAMERA][SEQ_BIND]`, `[CAMERA][CUT_APPLY]`). Classification: `PASS_CAMERA_FRUSTUM_CRASH_GUARD`. Log: `/tmp/uelivesync-manual-e2e-ue.log`.

### Added
- Phase 9 Stage 3B — Discovery Scan: `discover_servers()` in `Blender_Addon/network.py` probes default candidates (127.0.0.1, localhost, configured host) via TCP connect on port 57000. Synchronous with bounded timeout (default 1.0s per candidate). Returns structured list `{host, port, success, error}`. Diagnostics markers: `[DISCOVERY][START/PROBE/FOUND/MISS/DONE]`. UI button "Discover LiveSync Server" in addon panel. Discovery diagnostics in `dump_diagnostics()`. No UE C++ change required. 46/46 tests PASS. Validated with dummy TCP listener. Classification: `PASS_DISCOVERY_LOCALHOST_SCAN`.
- Current State Roadmap: Canonical consolidation document at `Docs/Architecture/current-state-roadmap.md`. Supersedes stale scope-lock assumptions. Includes phase status table (17 phases), stable tags table (17 tags), complete packet registry truth table (24 entries 0x01–0x1B with 0x02 reserved and 0x10 unused), implemented vs NOT implemented table, validation classifications (8), known limitations (11), and recommended next work options. Source-text audit: `tests/current_state_roadmap_audit.py` (54/54 PASS). Tag: `current-state-roadmap-stable`.
- Phase 9 Production Ecosystem Closeout Audit: Full implementation truth table of 8 scope-lock stages. Capability announce/response (0x11/0x12) fully implemented with 3 atomic counters. Discovery scan (stage 3B) confirmed NOT IMPLEMENTED — no UDP broadcast, no port scan, no auto-connect. Reconnect/backoff (exponential 0.5-10s with idle probe) fully implemented. Diagnostics export is console-only (no zip/support bundle). Rewrote 4 stale test files that referenced nonexistent constants/functions: `phase9_stage2f_compat_matrix.py` (62/62 PASS), `phase9_stage3b_discovery_scan.py` (22/22 PASS), `phase9_stage5e_stale_session_cleanup.py` (22/22 PASS), `phase9_stage6b_diagnostics_export.py` (32/32 PASS). Fixed 2 stale import test files: `phase9_stage5b_session_change.py` (17/17 PASS), `phase9_stage5c_state_cleanup.py` (10/10 PASS). New source-text invariant test: `tests/phase9_production_ecosystem_audit.py` (71/71 PASS). Audit doc: `Docs/Architecture/57-phase9-production-ecosystem-audit.md`. Key discrepancy: 3 earlier Phase 9 test files missing from disk (older stage5b/c/d), covered by new audit test. Classification: `PASS_PHASE9_AUDIT_ONLY`. 209 Phase 9 tests all PASS; representative regressions all PASS. Tag: `phase9-audit-stable`.
- E2E Runtime Validation Suite: Audited all 11 standalone TCP injector/validator tools. Created orchestration plan doc at `Docs/Architecture/e2e-runtime-validation-suite.md`. Created `tools/uelivesync_e2e_runtime_validator.py` — subprocess-based orchestrator chaining timeline, playback, camera lifecycle, camera def, sequencer/keyframe, queue diagnostics, and malformed packet checks. New audit test: `tests/e2e_runtime_validation_suite_audit.py` (27/27 PASS). Classification: `PASS_E2E_AUDIT_ONLY`.
- Phase 8 Closeout Audit: Comprehensive source audit comparing scope-lock design doc against actual code. Key finding: 7 of 10 stages claimed as COMPLETE were never implemented (backpressure ACK 0x10, adaptive throttling, mesh compression zlib, section builder optimization, MaterialGroups removal, dirty-flag interest management). What exists: burst packet counting (Blender), queue depth/drop diagnostics (UE), static packet rate limiter (UE). New audit test: `tests/phase8_performance_streaming_audit.py` (37/37 PASS). Audit doc: `Docs/Architecture/phase8-performance-streaming-audit.md`. Classification: `PASS_PHASE8_AUDIT_ONLY`. Tag: `phase8-audit-stable`.
- Phase 7G Stage 4: Camera Transform Sync — validates CREATE + TRANSFORM + ACTIVE_CAMERA pipeline.
- Injector modes: `--create-transform-active` (one connection, 0.2s inter-packet sleeps), `--cameradef-only` (fresh connection, GUID from log), `--full-separated` (combined lifecycle with 3s pause).
- Phase 7G Stage 5: Camera Sequencer Binding + CameraCutTrack Integration.
- `EnsureCameraSequencerBinding()` helper in HandleActiveCamera — creates possessable binding + CameraCutTrack + CameraCutSection in asset-backed LevelSequence.
- 6 new counters: `ActiveCameraBindingCreated/Exists`, `ActiveCameraCutTrackCreated/Applied/Skipped`, `ActiveCameraSeqSaved`.
- FBX Handoff Pipeline Audit: Full pipeline audit completed. `Docs/Architecture/fbx-handoff-pipeline-audit.md`. `tests/phase_fbx_handoff_pipeline_audit.py` (52/52 PASS). All 20/21 existing FBX test suites PASS. Unit conversion, scale invariant, material slot preservation, and GUID-based asset reuse all verified. Phase 7 regression 710/710 PASS.
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
- Added Phase 7E Stage 10A.5 visibility bool runtime validation: Blender probe object with hide_viewport/hide_render keyframes, direct raw TCP socket transport (bypassing addon _send_queue), deterministic packet ordering (CREATE_SEQUENCE → CREATE → ADD_POSSESSABLE → PT_Keyframe).
- Added `tests/phase7e_stage10a5_blender_runtime_automation.py` — 43 static tests verifying quaternion wire format (4-float FQuat), unique sequence numbers, packet ordering, and socket transport.
- Added `tools/uelivesync_stage10a5_blender_visibility_runtime.py` — Real Blender 5.1 probe object creation, FCurve extraction via `_iter_action_fcurves_51`, direct TCP packet send.
- Added `tools/run_stage10a5_blender_visibility_runtime.sh` — Shell wrapper for runtime validation.
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
- **Manual E2E.6 — Hierarchy Guard Marker Confirmation (Runtime Complete)**: Added `--hierarchy-confirm` mode to isolation injector. Creates parent actor, waits for registration, creates child actor, waits, sends PT_Hierarchy child->parent. Valid attach confirmed via `[HIERARCHY][ATTACH]` BEGIN/END markers. Cycle detection confirmed (4x `[HIERARCHY][CYCLE]` markers). `[HIERARCHY][ATTACH_GUARD]` not visible in pre-built binary (Log level). No Signal 11/6 crash. **Classification: PASS_E2E6_VALID_HIERARCHY_ATTACH_CONFIRMED_PARTIAL**. Static: 139/139 PASS.
- **Manual E2E.6B — Hierarchy Guard C++ Diagnostic Logging Revert**: Build attempted against deployed UE5.7 plugin — failed (11 errors, pre-existing `AActor::bPendingKill` removal). Reverted C++ production source change from 2939ce1. Keep docs/tooling/test changes. C++ diagnostic logging (Warning level `[HIERARCHY][ATTACH_GUARD]`) removed from production source. Hierarchy confirmation remains tooling-only. **Classification: PASS_E2E6_VALID_HIERARCHY_ATTACH_CONFIRMED_PARTIAL_NO_CPP_CHANGE**. Static: 139/139 PASS.
- **Manual E2E.7 — UE5.7 Compile Compatibility Cleanup**: Fixed 4 `AActor::bPendingKill` access locations in `WouldCreateAttachmentCycle()` with `IsLiveSyncActorInvalidForAttach()` helper (null + `IsActorBeingDestroyed()` + `!IsValid()`). Fixed `SetNum(bool)` deprecation (`EAllowShrinking::No`). Resolved UE_LOG format validation cascade via precomputed locals. Build SUCCEEDED (0 errors, 0 warnings). Runtime smoke PASS — rebuilt binary shows `[HIERARCHY][ATTACH_GUARD]` with all markers confirmed (ATTACH=1, ATTACH_SAFE=1, CYCLE=4, Signal 6/11=0). Static: 158/158 PASS (19 new + 139 existing). New test: `tests/ue57_compile_compatibility.py`. Classification: **PASS_E2E7_UE57_COMPILE_COMPATIBILITY_CLEAN**.
- **Manual E2E.8 — Full Signal 6/11 Runtime Regression After Rebuild**: Full runtime regression after UE5.7 compile cleanup. **FAIL_E2E8_SCENE_OUTLINER_REGRESSION**. Test A (camera full lifecycle): Signal 11=1, SceneOutliner crash. Test B (hierarchy confirm): PASS, all markers present, 0 signals. Test C (legacy camera): Signal 11=1, SceneOutliner crash. The SceneOutliner crash (`FActorMode::IsActorDisplayable` → `AActor::GetWorld()` → SEGFAULT) is a **separate code path** from the frustum guard — `[CAMERA][FRUSTUM_GUARD]` was present but the outliner recursively crashed during tree refresh. Not a regression from E2E.7 (pre-existing, undetected by PID-alive check). Static: 158/158 PASS. No tag created. Old tag `manual-e2e-camera-crash-guard-stable` remains PROVISIONAL. See `FAIL_E2E8_SCENE_OUTLINER_REGRESSION` section in STATUS.md.
- **Manual E2E.9 — Camera SceneOutliner Safe Lifecycle (PARTIAL — FRUSTUM GUARD WORKS, SCENEOUTLINER CRASH REMAINS)**: Added `IsLiveSyncCameraSafeForEditorUse()` helper (9 checks). Changed HandleCreateObject (LSP_Camera) and HandleActiveCamera auto-spawn to `SpawnActorDeferred<ACameraActor>` + frustum guard before `FinishSpawning`. Added safety gates on Sequencer binding and viewport lock. Renamed `[CAMERA][VIEW_TARGET_SKIP]` → `[CAMERA][SAFE_INVALID_SKIP]`. **FAIL_E2E9_SCENEOUTLINER_CRASH_REMAINS**. Test A (--full): Signal 11=1, SceneOutliner crash **47ms after transform converge** — heap corruption in mimalloc during next-tick outliner tree rebuild, not frustum-related. Test B (hierarchy): PASS. Frustum guard confirmed working (OUTLINER_GUARD marker). Build 0/0. Static: 453+ PASS (38 new e2e9 tests + all existing). New test: `tests/e2e9_camera_sceneoutliner_safe_lifecycle.py` (38 tests). Classification: **PARTIAL_E2E9_FRUSTUM_GUARD_OK_SCENEOUTLINER_CRASH_REMAINS**. No stable tag.
- **Manual E2E.10 — Camera SceneOutliner Workaround (COMPLETED)**: W3 workaround: `FActorSpawnParameters::bHideFromSceneOutliner = true` for ACameraActor spawns in both HandleCreateObject (LSP_Camera) and HandleActiveCamera auto-spawn paths. Switched from `SpawnActorDeferred` back to `SpawnActor` with `bHideFromSceneOutliner=true` and post-spawn frustum guard. Removed E2E10_DEFER_EXPOSURE/DEFER_ACTIVE markers and timer-based ProcessDeferredCameras. Added `[CAMERA][E2E10_OUTLINER_HIDE]` marker. All existing safety helpers preserved. **PASS_E2E10_CAMERA_SCENEOUTLINER_WORKAROUND**. Build 0/0. Runtime: Test A (--full) PASS, Test B (--hierarchy) PASS, Test C (--full-separated) PASS — 0 Signal 11/6, 0 EnsureParentForItem, 0 AddUnfilteredItemToTree. Static: 167/167 PASS (23 new e2e10 tests + 38 e2e9 + 19 ue57 + 24 camera_crash_guard + 30 parent_guard + 33 scene_outliner_isolation). Audit: 27/27 PASS. New test: `tests/e2e10_sceneoutliner_camera_workaround.py` (23 tests). Classification: **PASS_E2E10_CAMERA_SCENEOUTLINER_WORKAROUND**. Tag `manual-e2e-camera-crash-guard-stable` is now **FINAL (non-provisional)**.
