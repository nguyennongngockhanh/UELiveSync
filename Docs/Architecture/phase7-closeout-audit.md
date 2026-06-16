# Phase 7 — Animation & Sequencer Sync: Closeout Audit

**Audit Date**: 2026-06-16  
**Baseline Commit**: `29ca55f` (tagged `phase7g-stage5a-stable`)  
**Working Tree**: Clean  
**Total Tests Sampled**: 710/710 PASS (24 test files, 0 failures)

---

## 1. Scope

Phase 7 covers animation-related packets and Sequencer integration for the UELiveSync system:

- **PT_Keyframe (0x17)** — Transform + visibility keyframe replication
- **PT_SequencerOp (0x18)** — Sequencer create/add/remove operations
- **PT_TimelineState (0x19)** — Frame range + FPS sync
- **PT_PlaybackTransport (0x1A)** — Play/pause/stop/scrub
- **PT_CameraDef (0x1B)** — Camera parameter sync (FOV, clip planes, ortho)
- **PT_ActiveCamera (0x15)** — Active camera designation + auto-spawn
- **PT_Create (0x03) + LSP_Camera (0x05)** — Camera actor spawn
- **PT_Transform (0x01)** — Camera transform apply

---

## 2. Stable Tags

All 11 Phase 7 stable tags exist locally:

| Tag | Commit | Component |
|-----|--------|-----------|
| `phase7e-stage10a-stable` | `21692d0` | Visibility keyframe extraction + apply |
| `phase7e-stage10b-stable` | `a2164eb` | Asset-backed LevelSequence |
| `phase7e-stage10c-stable` | `0054e31` | Persist applied sequence data |
| `phase7e-stage10d-stable` | `6645c85` | Sequencer editor usability |
| `phase7f-stage1-stable` | `8619952` | PT_TimelineState (0x19) |
| `phase7f-stage2-stable` | `aac0ad7` | PT_PlaybackTransport (0x1A) |
| `phase7g-stage2-stable` | `0a98c0f` | Camera actor spawn + view target |
| `phase7g-stage3-stable` | `67c08da` | PT_CameraDef (0x1B) |
| `phase7g-stage4-stable` | `079a500` | Camera transform sync |
| `phase7g-stage5-stable` | `099a69c` | Camera sequencer binding |
| `phase7g-stage5a-stable` | `29ca55f` | Camera seq binding runtime validation |

---

## 3. Packet Registry Verification

### kValidTypes (UELiveSyncSubsystem.cpp:2989)

```
{ 0x01, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08, 0x09, 0x0A, 0x0B, 0x0C, 0x0D,
  0x0E, 0x0F, 0x11, 0x12, 0x13, 0x14, 0x15, 0x16, 0x17, 0x18, 0x19, 0x1A, 0x1B }
```

All Phase 7 types present: `0x13` (Timeline), `0x14` (Playback), `0x15` (ActiveCamera), `0x16` (Playlist), `0x17` (Keyframe), `0x18` (SequencerOp), `0x19` (TimelineState), `0x1A` (PlaybackTransport), `0x1B` (CameraDef).

**0x02 is NOT in kValidTypes.** The byte `0x02` appears only in `kValidFlags` (line 2992), which validates packet flags, not packet types.

### UE_LOCAL_CAPABILITIES (SyncTypes.h:2169-2175)

```cpp
constexpr uint32 UE_LOCAL_CAPABILITIES =
    CAP_SUPPORTS_TIMELINE_SYNC |          // 0x10
    CAP_SUPPORTS_KEYFRAME_REPLICATION |   // 0x20
    CAP_SUPPORTS_ACTIVE_CAMERA_SYNC |     // 0x40
    CAP_SUPPORTS_SEQUENCER_OPS |          // 0x80
    CAP_SUPPORTS_CAMERA_DEF_SYNC |        // 0x100
    CAP_SUPPORTS_CAMERA_SEQ_BIND;         // 0x200
```

All six bits defined and OR'd into the mask.

### LSP_Camera = 0x05 (SyncTypes.h:294)

```cpp
enum ELiveSyncPrimitiveType : uint8 {
    LSP_Cube     = 0x00,
    LSP_Sphere   = 0x01,
    LSP_Cylinder = 0x02,
    LSP_Plane    = 0x03,
    LSP_Empty    = 0x04,
    LSP_Camera   = 0x05,
};
```

Valid primitive. Camera spawn path at `UELiveSyncSubsystem.cpp:7817`.

---

## 4. Handler Registry

### Phase 7E — Sequencer & Keyframe

| Handler | Packet | Line Range | Binding Map | Save Seq | Creates Sequencer? |
|---------|--------|-----------|-------------|----------|-------------------|
| `HandleKeyframe` | 0x17 | 9399-9626 | YES (9445) | YES (9623) | Bool/Transform tracks + sections |
| `HandleSequencerOp` | 0x18 | 8997-9378 | YES (multi) | NO | Possessables, CameraCutTrack, sections |

### Phase 7F — Timeline & Playback

| Handler | Packet | Line Range | Binding Map | Save Seq | Creates Sequencer? |
|---------|--------|-----------|-------------|----------|-------------------|
| `HandleTimelineState` | 0x19 | 12628-12676 | NO | NO | Playback range + display rate only |
| `HandlePlaybackTransport` | 0x1A | 12683-12749 | NO | NO | None (state-only) |

### Phase 7G — Camera Sync

| Handler | Packet | Line Range | Binding Map | Save Seq | Creates Sequencer? |
|---------|--------|-----------|-------------|----------|-------------------|
| `HandleActiveCamera` | 0x15 | 10930-11071 | Indirect | Indirect | Via EnsureCameraSequencerBinding |
| `HandleCameraDef` | 0x1B | 11085-11195 | NO | NO | Camera params only |
| `EnsureCameraSeqBinding` | (helper) | 10776-10926 | YES (10810) | YES (10922) | Possessable + CameraCutTrack + section |

### Standard V3 Packets (not Phase 7-specific but used by camera)

| Handler | Packet | Line Range |
|---------|--------|-----------|
| PT_Create camera branch | 0x03 | 7817-7831 |
| PT_Transform apply | 0x01 | ~6341+ |

---

## 5. Safety Compliance

| Requirement | Status | Evidence |
|-------------|--------|----------|
| 0x02 NOT in kValidTypes | PASS | Array at line 2989 excludes 0x02 |
| LSP_Camera = 0x05 | PASS | SyncTypes.h:294 |
| Missing binding safe | PASS | HandleKeyframe:9446-9467 gracefully skips |
| Unsupported channel safe | PASS | HandleKeyframe:9540-9549 increments counter |
| Stale sequence rejection | PASS | All handlers check `LiveSyncSequence.IsValid()` |
| Only subsystem-owned sequence mutated | PASS | All handlers gate on `bHasLiveSyncSequence` |
| Non-object packets use obj_count=0 | PASS | Phase 7 packets dispatched before V3 object loop |
| No packet format change | PASS | Wire structs unchanged across all stages |
| Game-thread only mutation | PASS | All handlers called from `ProcessQueuedPackets` (game thread) |

---

## 6. Test Results (Representative Sample)

| Test Suite | Tests | Pass | Fail |
|-----------|-------|-----|------|
| Phase 7E Stage 10A — Visibility keyframe extraction | 67 | 67 | 0 |
| Phase 7E Stage 10A.2 — Visibility keyframe apply | 49 | 49 | 0 |
| Phase 7E Stage 3 — SequencerOp wire format | 81 | 81 | 0 |
| Phase 7E Stage 5 — Binding apply | 50 | 50 | 0 |
| Phase 7E Stage 6 — Camera cut apply | 72 | 72 | 0 |
| Phase 7E Stage 7 — Keyframe wire format | 79 | 79 | 0 |
| Phase 7E Stage 9 — Keyframe apply | 97 | 97 | 0 |
| Phase 7E Stage 10B — Pack UE FGuid | 22 | 22 | 0 |
| Phase 7E Stage 10C — Persist applied sequence | 7 | 7 | 0 |
| Phase 7F Stage 1 — Timeline wire + UE apply + guard | 21 | 21 | 0 |
| Phase 7F Stage 2 — Playback wire + UE apply + guard | 27 | 27 | 0 |
| Phase 7G Stage 2 — Camera spawn + view target + guard | 30 | 30 | 0 |
| Phase 7G Stage 3 — CameraDef wire + UE apply + guard | 48 | 48 | 0 |
| Phase 7G Stage 4 — Camera transform sync | 26 | 26 | 0 |
| Phase 7G Stage 5 — Camera sequencer binding | 34 | 34 | 0 |
| **Total sampled** | **710** | **710** | **0** |

Note: `phase7e_stage10d_editor_sequence_validation.py` skipped gracefully — requires UE Python runtime (not available in offline test environment). This is expected and documented behavior.

---

## 7. Known Limitations

1. **UE Python API cannot inspect FMovieSceneFloatChannel / FMovieSceneBoolChannel key values.** Keyframe channel data is stored in internal UE types not exposed to Python. Log-based validation (`applied=N miss=0 unsupp=0` + per-channel diagnostics) is the accepted method.

2. **Camera property keyframes are not implemented.** Phase 7G covers camera actor spawn, transform sync, parameter sync (FOV/clip/ortho), and Sequencer binding with CameraCutTrack. Individual camera property animation (aperture, focal length animation over time) is deferred.

3. **CameraCutTrack is not exposed through UE Python API.** The C++ method `UMovieScene::GetCameraCutTrack()` exists but is not exposed in Python bindings. CameraCutTrack creation is confirmed via C++ log markers (`[CAMERA][CUT_APPLY]`, `[CAMERA][CUT_SAVE]`) which are authoritative.

4. **Playback Play/Pause/Stop is `PASS_TRANSPORT_STATE_ONLY`.** These commands are received, validated, and logged but do not drive actual Sequencer playback. `SetFrame` applies the clamped frame to `LiveSyncSequenceFrameCurrent`. Full Sequencer transport control is deferred.

5. **`-NullRHI` is not valid for LiveSync runtime validation.** Networking requires a live RHI. Use normal editor or `-RenderOffScreen`.

6. **The asset-backed LevelSequence accumulates bindings across sessions.** The sequence at `/Game/UELiveSync/Sequences/LS_UELiveSync_Runtime` is created once and reused. Bindings and tracks from previous connections persist. A reset mechanism (`[SEQ][RESET]` via SequencerOp CLEAR) exists but must be explicitly triggered.

---

## 8. Counter Inventory

All Phase 7 counters are defined in `SyncTypes.h:1263-1280` and registered in `UELiveSyncSubsystem_Diagnostics.inl`.

| Domain | Counters |
|--------|----------|
| Keyframe | `KeyframePacketsReceived/Applied/Stale/Malformed`, `KeyframeKeysApplied/MissingBinding/UnsupportedChannel`, `KeyframeTrackCreated/SectionCreated`, `KeyframeVisibilityKeysApplied/TrackCreated/SectionCreated/Unsupported` |
| SequencerOp | `SequencerOpPacketsReceived/Applied/Stale/Malformed`, `SequencerPossessablesAdded/Removed/MissingActor/Duplicate`, `SequencerCameraCutsAdded/MissingBinding/MalformedRange` |
| Timeline | `TimelinePacketsReceived/Applied/Stale/Malformed` |
| Playback | `PlaybackPacketsReceived/Applied/Stale/Malformed` |
| ActiveCamera | `ActiveCameraPacketsReceived/Applied/Stale/Malformed`, `ActiveCameraPacketsSpawned/AppliedToViewport/NotCamera/ViewTargetFailed/MissingGUID` |
| CameraDef | `CameraDefPacketsReceived/Applied/Stale` |
| CameraSeqBind | `ActiveCameraBindingCreated/Exists`, `ActiveCameraCutTrackCreated/Applied/Skipped`, `ActiveCameraSeqSaved` |

All counters reset in `ResetDiagnosticCounters()` and emitted in periodic diagnostic logs.

---

## 9. Validation Classes

| Component | Validation Class | Method |
|-----------|-----------------|--------|
| Transform keyframes | PASS | Source tests + log markers |
| Visibility keyframes | PASS | Source tests + log markers (`applied=N miss=0 unsupp=0`) |
| Sequencer ops | PASS | Source tests + log markers |
| Timeline state | PASS | Source tests + log markers |
| Playback transport | PASS_TRANSPORT_STATE_ONLY | Source tests (SetFrame applies; Play/Pause/Stop = state-only) |
| Camera actor spawn | PASS | Source tests + log markers |
| Camera view target | PASS_CAMERA_VIEW_TARGET_APPLY | Source tests + runtime markers (editor mode) |
| CameraDef sync | PASS_CAMERADEF_APPLY | Source tests + runtime markers |
| Camera transform sync | PASS_CAMERA_TRANSFORM_APPLY | Source tests + runtime markers |
| Camera seq binding | PASS_CAMERA_SEQ_BIND_APPLY | Source tests + C++ log markers (Python API limitation) |
| Asset-backed sequence | PASS_BINDING_ONLY | UE Python validates bindings survive save/load |
| Editor usability | PASS_EDITOR_DATA_ONLY | UE Python validates editor can open sequence |

---

## 10. Recommendations

### Phase 7 Core Status: **CORE-COMPLETE**

Phase 7 covers all planned animation and Sequencer sync functionality:
- Transform keyframe replication (channels 0-8)
- Visibility keyframe replication (channels 9-10)
- Sequencer op lifecycle (create, add possessable, remove possessable, add camera cut)
- Timeline state sync (frame range + FPS)
- Playback transport (transport-level state)
- Camera lifecycle (spawn, transform, parameters, active camera, sequencer binding)
- CameraCutTrack integration
- Asset-backed LevelSequence persistence
- All with established test suites, diagnostic markers, and counters

### Next Steps

1. **Camera property keyframes** — The most natural next extension. Animate individual camera properties (focal length, aperture, focus distance) over time using existing PT_Keyframe mechanism with new channel IDs. Currently no channels are allocated beyond 10 (visibility).
2. **Phase 6 closeout audit** — Phase 6 (High-Performance Streaming) was noted as complete but lacks a formal closeout audit. Worth documenting.
3. **Phase 8 performance** — Phase 8 was scoped as "High Performance Streaming" but existing benchmarks show no bottleneck for 1-500 objects. Can be closed or deferred.
4. **Sequencer transport playback** — Full playback control via Sequencer API (actual seek/play/pause) would upgrade PlaybackTransport from PASS_TRANSPORT_STATE_ONLY to full PASS.

### Recommendation: Close Phase 7 as core-complete.

No remaining open items block the Phase 7 feature set. All 710 representative tests pass. Known limitations are documented and acceptable. The next implementation phase should be camera property keyframes if camera animation is the priority, or the FBX handoff pipeline if mesh sync is the priority.
