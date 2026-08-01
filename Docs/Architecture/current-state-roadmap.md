# UELiveSync — Current State Roadmap

**Canonical reference.** Supersedes stale scope-lock assumptions from earlier phase docs.
Last updated: 2026-06-28 (Phase 10A.3.x documentation closeout). Tag: `current-state-roadmap-stable`.

---

## Stable Tags

| Tag | Phase / Scope | Classification |
|-----|---------------|----------------|
| `phase7-core-stable` | Phase 7 — Animation & Sequencer Sync | CORE-COMPLETE |
| `phase7e-stage10a-stable` | Phase 7E Stage 10A — Visibility Keyframes | CORE-COMPLETE |
| `phase7e-stage10b-stable` | Phase 7E Stage 10B — Asset-backed LevelSequence | CORE-COMPLETE |
| `phase7e-stage10c-stable` | Phase 7E Stage 10C — Persist Applied Sequence | CORE-COMPLETE |
| `phase7e-stage10d-stable` | Phase 7E Stage 10D — Sequencer Editor Usability | CORE-COMPLETE |
| `phase7f-stage1-stable` | Phase 7F Stage 1 — Timeline State Packet (0x19) | CORE-COMPLETE |
| `phase7f-stage2-stable` | Phase 7F Stage 2 — Playback Transport (0x1A) | CORE-COMPLETE |
| `phase7g-stage2-stable` | Phase 7G Stage 2 — Camera Actor Spawn + Viewport | CORE-COMPLETE |
| `phase7g-stage3-stable` | Phase 7G Stage 3 — Camera Definition Sync (0x1B) | CORE-COMPLETE |
| `phase7g-stage4-stable` | Phase 7G Stage 4 — Camera Transform Sync | CORE-COMPLETE |
| `phase7g-stage5-stable` | Phase 7G Stage 5 — Camera Sequencer Binding | CORE-COMPLETE |
| `fbx-handoff-audit-stable` | FBX Handoff Pipeline Audit | COMPLETE |
| `fbx-test-hygiene-stable` | FBX Test Debt Cleanup | COMPLETE |
| `phase8-audit-stable` | Phase 8 High Performance Streaming Audit | `PASS_PHASE8_AUDIT_ONLY` |
| `e2e-runtime-validation-audit-stable` | E2E Runtime Validation Suite Audit | `PASS_E2E_AUDIT_ONLY` |
| `manual-e2e-camera-crash-guard-stable` | Manual E2E.1/E2E.10 — Camera Frustum Crash Guard + SceneOutliner Workaround | `PASS_CAMERA_FRUSTUM_CRASH_GUARD` / `PASS_E2E10_CAMERA_SCENEOUTLINER_WORKAROUND` — **FINAL** (Signal 6 and Signal 11 both resolved via W3 `bHideFromSceneOutliner`) |
| `phase9-audit-stable` | Phase 9 Production Ecosystem Audit | `PASS_PHASE9_AUDIT_ONLY` |
| `phase9-stage3b-discovery-stable` | Phase 9 Stage 3B — Discovery Scan | `PASS_DISCOVERY_LOCALHOST_SCAN` |
| `phase9-stage3c-connect-ux-stable` | Phase 9 Stage 3C — Discovery Auto-fill / Connect UX | `PASS_DISCOVERY_CONNECT_UX` |
| `current-state-roadmap-stable` | This document — Current State Roadmap | — |

---

## Phase Status Table

| Phase | Scope | Status | Classification |
|-------|-------|--------|----------------|
| 3.4–3.5 | Performance, stabilization, protocol cleanup | COMPLETE | — |
| 3.6 | Robustness & validation tests | COMPLETE | — |
| 4A–4D | Stability core, diagnostics, editor tooling, validation | COMPLETE | — |
| 5A–5E | Protocol evolution, asset identity, stress testing | COMPLETE | — |
| 6I.1 | Transport hardening (bounds, observability, lifecycle) | COMPLETE | — |
| 6 | Visibility, rename, hierarchy, delete, collection | COMPLETE | — |
| 10A.3.1 | Collision-safe texture sidecar identity | COMPLETE | `bb765f5` |
| 10A.3.2 | Structured sidecar preparation result | COMPLETE | `61d6b15` |
| 10A.3.3 | Content-based sidecar asset identity | COMPLETE | `d0f5b8e` |
| 10A.3.4 | Deterministic manifest v3 persistence | COMPLETE | `b9d1c2a` |
| 10A.3.5 | Manifest-informed sidecar reuse | COMPLETE | `e0967c7` |
| 10A.3.6 | Safe orphan sidecar pruning | COMPLETE | `2288508` |
| 10A.3.7 | Not defined | NOT DEFINED | — |
| 7A | Scope Lock / Identity Hygiene | COMPLETE | — |
| 7B | Timeline Sync (0x13) + Material Pipeline (0x05) | COMPLETE | — |
| 7C | Playback Sync (0x14) + Mesh Pipeline (0x06) + FBX Handoff (0x16) | COMPLETE | — |
| 7D | Active Camera Sync (0x15) | COMPLETE | — |
| 7E | Sequencer + Keyframe Replication (0x17, 0x18) | CORE-COMPLETE | — |
| 7F | Timeline State (0x19) + Playback Transport (0x1A) | CORE-COMPLETE | — |
| 7G | Camera Actor Spawn, CameraDef (0x1B), Camera Transform, Camera Sequencer Binding + CameraCutTrack | CORE-COMPLETE | — |
| 8 | High Performance Streaming | DESIGN ONLY — AUDITED | `PASS_PHASE8_AUDIT_ONLY` |
| 9 | Production Ecosystem (Capability, Discovery, Reconnect, Diagnostics) | CORE-COMPLETE (Stage 3B + Stage 3C implemented) | `PASS_PHASE9_AUDIT_ONLY` / `PASS_DISCOVERY_LOCALHOST_SCAN` / `PASS_DISCOVERY_CONNECT_UX` |
| 10I–10K | FBX unit scale, temp import, texture pipeline | COMPLETE | — |
| E2E | Runtime Validation Suite | AUDITED | `PASS_E2E_AUDIT_ONLY` |

---

## Packet Registry (Complete Truth Table)

| Value | Name | Phase | Implemented | Notes |
|-------|------|-------|-------------|-------|
| 0x01 | PT_Transform | Core | ✅ Yes | Per-frame transform update, 81 bytes V5 |
| 0x02 | PT_Reserved_02 | — | ❌ No | Legacy; labeled `PT_Reserved_02` in SyncTypes.h. **NOT in kValidTypes, NOT in network.py.** Reserved/invalid. |
| 0x03 | PT_Create | Core | ✅ Yes | Object spawn, 81 bytes V5 |
| 0x04 | PT_Delete | Core | ✅ Yes | Legacy delete, 16 bytes V3 |
| 0x05 | PT_Material | 7B | ✅ Yes | Material identity + slot metadata |
| 0x06 | PT_Mesh | 7C | ✅ Yes | Procedural mesh chunk (experimental — FBX is production mesh path) |
| 0x07 | PT_Heartbeat | Core | ✅ Yes | Keep-alive, 0 bytes |
| 0x08 | PT_AssetDef | Core | ✅ Yes | Asset identity definition, 33 bytes V5 |
| 0x09 | PT_BeginSnapshot | Core | ✅ Yes | Snapshot start marker |
| 0x0A | PT_EndSnapshot | Core | ✅ Yes | Snapshot end marker |
| 0x0B | PT_Visibility | 6 | ✅ Yes | Semantic visibility toggle |
| 0x0C | PT_Rename | 6 | ✅ Yes | Semantic rename event |
| 0x0D | PT_Hierarchy | 6 | ✅ Yes | Semantic attach/detach |
| 0x0E | PT_Delete_V5 | 6E | ✅ Yes | V5+ delete with sequence + tombstone semantics |
| 0x0F | PT_Collection | 6F | ✅ Yes | Collection membership events |
| **0x10** | *Unused gap* | — | **❌ NOT IMPLEMENTED** | **BackpressureACK was designed in Phase 8 scope-lock but never coded.** Not in kValidTypes, not in network.py. |
| 0x11 | PT_CapabilityAnnounce | 9 | ✅ Yes | Capability bitmask from Blender to UE |
| 0x12 | PT_CapabilityResponse | 9 | ✅ Yes | Capability bitmask from UE to Blender |
| 0x13 | PT_Timeline | 7B | ✅ Yes | Timeline/playhead frame sync (storage-only) |
| 0x14 | PT_PlaybackState | 7C | ✅ Yes | Playback state (play/pause/stop, storage-only) |
| 0x15 | PT_ActiveCamera | 7D | ✅ Yes | Active camera GUID (viewport apply behind CVar) |
| 0x16 | PT_FBXImportRequest | 7C/3A | ✅ Yes | FBX mesh import request, 688 bytes fixed |
| 0x17 | PT_Keyframe | 7E | ✅ Yes | Keyframe replication (header + entries, ch 0–10, transform ch 0–8 + visibility BoolTrack ch 9–10) |
| 0x18 | PT_SequencerOp | 7E | ✅ Yes | Sequencer opcode (create sequence, add binding, etc.) |
| 0x19 | PT_TimelineState | 7F | ✅ Yes | Timeline state applied to LevelSequence |
| 0x1A | PT_PlaybackTransport | 7F | ✅ Yes | Playback transport command (SetFrame/Play/Pause/Stop) |
| 0x1B | PT_CameraDef | 7G | ✅ Yes | Camera parameters (focal, sensor, clip, ortho) |

> **0x02** — reserved/invalid in all scopes. **0x10** — unused gap in real enumeration; claimed by Phase 8 scope-lock for BackpressureACK but never implemented.

---

## Implemented vs NOT Implemented

### Fully Implemented
- Transform sync (0x01) — per-frame, V5 format
- Actor lifecycle (0x03 create, 0x04/0x0E delete, 0x0C rename, 0x0D hierarchy)
- Visibility toggle (0x0B)
- Collection membership (0x0F)
- Heartbeat / keep-alive (0x07)
- Asset identity (0x08) + snapshot markers (0x09, 0x0A)
- Material sync (0x05 with MTEX texture metadata extension)
- Procedural mesh (0x06) — experimental baseline; FBX is production path
- Timeline sync (0x13) — storage-only
- Playback state (0x14) — storage-only
- Active camera sync (0x15) — viewport apply behind opt-in CVar
- Camera definition/parameter sync (0x1B) — focal, sensor, clip, ortho
- Camera transform sync — via PT_Create (LSP_Camera=0x05) + PT_Transform
- Camera Sequencer binding + CameraCutTrack integration
- FBX mesh handoff import (0x16) — unique temp asset per sync
- Keyframe replication (0x17) — transform ch 0–8, visibility ch 9–10
- Sequencer ops (0x18) — CREATE_SEQUENCE, ADD_POSSESSABLE, REMOVE_POSSESSABLE, ADD_CAMERA_CUT, CLEAR_SEQUENCE, SET_FRAME_RANGE
- Timeline state (0x19) — applies frame range + FPS to LevelSequence
- Playback transport (0x1A) — SetFrame/Play/Pause/Stop commands
- Capability announce/response (0x11, 0x12) — Phase 9
- Capability gating for timeline, keyframe, active camera, sequencer ops, camera def, camera seq bind
- Reconnect with exponential backoff (0.5–10s) and idle probe
- Diagnostics console dump (DumpStateToConsole / dump_diagnostics)
- Burst packet counting (Blender) + queue depth/drop diagnostics (UE) + static packet rate limiter (UE)
- Discovery scan (Phase 9 Stage 3B) — `discover_servers()` probes default candidates (127.0.0.1, localhost, configured host) via TCP connect on port 57000. Returns structured results. Button in addon panel.
- Discovery auto-fill / connect UX (Phase 9 Stage 3C) — `get_best_discovery_result()` / `apply_discovery_result()` helpers. "Use Discovered Server" and "Discover & Connect" operators in addon panel. `DISCOVERY][APPLY/CONNECT]` diagnostics markers. 38 tests PASS with dummy TCP listener. `PASS_DISCOVERY_CONNECT_UX`.

### NOT Implemented (Designed / Documented but Never Coded)
- **Backpressure ACK** (Phase 8, packet type 0x10) — no PT_BackpressureACK, no ack-based flow control, no retransmit.
- **Adaptive throttle** (Phase 8) — no dynamic send interval based on queue depth / round-trip time.
- **Mesh compression (zlib)** (Phase 8) — PT_Mesh payloads are not compressed.
- **Dirty-flag interest management** (Phase 8) — no per-client subscription; all objects broadcast.
- **Section builder optimization** (Phase 8) — no ProceduralMesh section builder dedup/hash.
- **MaterialGroups removal** (Phase 8) — procedural mesh still groups by material index.
- **Diagnostics zip/support bundle** (Phase 9 Stage 6B) — no `export_support_bundle()`; `dump_diagnostics()` prints to console only.
- **Camera property keyframes** — FCurves for focal length, aperture, focus distance, sensor, DOF are silently skipped in `_extract_keyframes()`.
- **Procedural mesh full attribute sync** — normals, UVs, tangents, vertex colors not carried in PT_Mesh V5 payload.
- **UE→Blender reverse sync** — no UE→Blender camera, timeline, or playback sync.

---

## Validation Classifications

| Classification | Meaning | Used By |
|----------------|---------|---------|
| `PASS_PHASE8_AUDIT_ONLY` | Phase 8 was audited against scope-lock doc; only minimal code exists. No runtime validation. | Phase 8 audit |
| `PASS_PHASE9_AUDIT_ONLY` | Phase 9 was audited against source; capability announce/response exist. Stage 3B discovery scan added separately. No live UE E2E. | Phase 9 audit |
| `PASS_DISCOVERY_LOCALHOST_SCAN` | Discovery scan probes default hosts (127.0.0.1, localhost, configured host). Validated with dummy TCP listener. | Phase 9 Stage 3B |
| `PASS_DISCOVERY_CONNECT_UX` | Discovery auto-fill and connect UX: apply_discovery_result(), get_best_discovery_result(), "Use Discovered Server"/"Discover & Connect" operators. Validated with dummy TCP listener. | Phase 9 Stage 3C |
| `PASS_E2E_AUDIT_ONLY` | E2E runtime validation suite was audited; orchestration plan exists, injectors exist, but full Blender→UE E2E requires manually running Blender + UE. | E2E suite |
| `PASS_CAMERA_TRANSFORM_APPLY` | Camera transform sync (CREATE + TRANSFORM + ACTIVE_CAMERA) validated at runtime on UE 5.7.4. | Phase 7G Stage 4 |
| `PASS_CAMERADEF_APPLY` | Camera definition/parameter sync (0x1B) validated at runtime. | Phase 7G Stage 3 |
| `PASS_CAMERA_SEQ_BIND_APPLY` | Camera Sequencer binding + CameraCutTrack integration validated at runtime. | Phase 7G Stage 5 |
| `PASS_LOAD_ONLY` | UE Python can load the asset; no binding/keyframe data inspection. | Phase 7E Stage 10B.3 |
| `PASS_BINDING_ONLY` | UE Python can detect binding count and track types. | Phase 7E Stage 10C.1 |
| `PASS_EDITOR_DATA_ONLY` | Sequencer Editor can open the sequence; binding and sections persist. | Phase 7E Stage 10D.1 |

---

## Stable Test Totals

| Suite | Tests | Result |
|-------|-------|--------|
| Phase 9 Stage 3B Discovery Scan | 46 | ✅ PASS (dummy TCP listener) |
| Phase 9 Stage 3C Discovery Connect UX | 38 | ✅ PASS (dummy TCP listener) |
| Phase 9 Production Ecosystem Audit | 71 | ✅ PASS |
| Phase 8 Performance Streaming Audit | 37 | ✅ PASS |
| E2E Runtime Validation Suite Audit | 27 | ✅ PASS |
| FBX Handoff Pipeline Audit | 52 | ✅ PASS |
| Phase 7E Keyframe Apply (Stage 9) | 97 | ✅ PASS |
| Phase 7E Keyframe Wire (Stage 7) | 79 | ✅ PASS |
| Phase 7E Visibility Extract (Stage 10A.1) | 67 | ✅ PASS |
| Phase 7E Visibility BoolTrack Apply (Stage 10A.2) | 32 | ✅ PASS |
| Phase 7E BoolTrack Runtime Smoke (Stage 10A.3) | 26 | ✅ PASS |
| Phase 7E Blender Visibility E2E (Stage 10A.4) | 73 | ✅ PASS |
| Phase 7E Stage 10A.5 SequencerOp wrap + reserved guard | 4 | ✅ PASS |
| Phase 7E Stage 10A.6 pytest collection fix | 101 | ✅ PASS |
| Phase 7E Stage 10E Transform Keyframe Runtime | 45 | ✅ PASS |
| Phase 7E SequencerOp Wire (Stage 3) | 81 | ✅ PASS |
| Phase 7E End-to-End Pipeline (Stage 9B) | 63 | ✅ PASS |
| Phase 7D Stage 3 UE Handler | 92 | ✅ PASS |
| Phase 7D Stage 2 Detection | 60 | ✅ PASS |
| Phase 7C Stage 3 UE Handler | 53 | ✅ PASS |
| Phase 7C Stage 2 Detection | 41 | ✅ PASS |
| Phase 7C Stage 1 Wire | 42 | ✅ PASS |
| Phase FBX test suites (10J, 10K) | 18 + 9 | ✅ PASS |
| Phase 5d reconnect | 11 | ✅ PASS |
| Phase 10A.3.1–A3.6 Texture Sidecar Lifecycle (6 stages) | 614 + 15 subtests | ✅ PASS |
| — A3.6 focused tests | 58 | ✅ PASS |
| — Canonical texture identity | 45 | ✅ PASS |
| — Serialization (phase10k) | 19 | ✅ PASS |
| — Phase10K6 | 68 | ✅ PASS |

## Post-A3.x Scope Selection

A3.1–A3.6 are complete. A3.7 is not defined.

This documentation closeout does not select the next production stage.
Any next production work requires a new evidence-based scope lock.

Existing roadmap candidates remain options only and are not active work.

---

## Known Limitations

1. **Camera property keyframes not implemented.** FCurves for focal length, aperture, focus distance, sensor size, DOF are silently skipped in `_extract_keyframes()`.

2. **Discovery scan is TCP-connect-probe only.** `discover_servers()` probes default candidates (127.0.0.1, localhost, configured host) via TCP connect on port 57000. No UDP broadcast, no port scan, no LAN subnet auto-discovery. "Available UE Instances" list is not populated from network broadcast.

3. **Backpressure/adaptive throttle/compression not implemented.** Phase 8 scope-lock stages (BackpressureACK 0x10, adaptive send interval, zlib mesh compression, dirty-flag interest management, section builder optimization) were designed but never coded.

4. **Diagnostics zip/support bundle not implemented.** `dump_diagnostics()` prints to console only; no `export_support_bundle()` function exists.

5. **E2E actual Blender operator FBX path still manual.** The full Blender→FBX→UE StaticMesh pipeline requires manual operator invocation in Blender (`Sync Selected Mesh to UE`). No automated E2E test covers this path.

6. **UE Python cannot inspect some Sequencer internals.** `FMovieSceneDoubleChannel` and `FMovieSceneBoolChannel` key data are not exposed through the UE Python API. Keyframe inspection relies on C++ markers (`[KEYFRAME] AppliedKeys=N`) and log parsing.

7. **CameraCutTrack not exposed through UE Python.** CameraCutTrack operations are verified via C++ diagnostic markers only (`[CAMERA][CUT_TRACK/APPLY/SKIP/SAVE]`).

8. **Do not use `-NullRHI` for runtime LiveSync validation.** `-NullRHI` disables networking (FSocket operations hang on some platforms) and suppresses Tick in certain configurations. Use normal editor or `-RenderOffScreen`.

9. **No interpolation/tangent mapping for keyframes.** Blender Bézier/auto/vector tangents are not mapped to UE's `FMovieSceneTangentData`. Keys use default Hermite interpolation.

10. **No periodic scene health check.** Scene scan (GUID reconciliation) only triggers on timeout mismatch; no proactive health check.

11. **PT_Mesh (0x06) is experimental.** Full attribute sync (normals, UVs, tangents, vertex colors) not implemented. Production mesh sync uses FBX handoff (0x16) path.

---

## Recommended Next Work Options

> These are options only — no commitment to any particular direction.

### Investigation Closeout (2026-07-31)

Camera synchronization investigations completed:

- **INV-2026-009 (INV-C9) — Camera orientation mismatch (Blender ↔ UE): Closed.** Root cause: `get_transform` converted the object frame (`C*M*C`) but not the camera's intrinsic view axis (Blender −Z vs UE +X). Fixed with a camera-local basis rotation in `Blender_Addon/sync.py`. Verified: identity settles to UE forward `(0,0,-1)`; Roll 90 / Pitch 90 runtime quaternions match expected corrected values; Yaw 90 validated mathematically. All `[INV-C9]` instrumentation removed.
- **INV-2026-010 (INV-E2) — PiP viewport invalidation after OBJECT_CREATE / mesh rebuild: Closed.** Root cause was editor viewport invalidation policy, not RenderThread. Resolved via viewport invalidation after create/visibility/mesh rebuild.
- **INV-2026-011 — Ortho scale unit mismatch (Blender m → UE cm): Closed.** Root cause: `ortho_scale` sent raw from Blender (meters) while `UCameraComponent::OrthoWidth` is in world units (cm). Fixed with `_ue_ortho_scale()` (`ortho_scale * 100.0`) applied in the signature and both CameraDef emission paths. Commit `5ec62be`.
- **INV-2026-012 — Camera aspect ratio not synced via protocol: Closed.** Root cause: `PT_CameraDef` carried no aspect field; UE derived `1.5` from sensor ratio while Blender framing depends on render resolution (`1.7778`). Fixed by extending `PT_CameraDef` 44 → 48 bytes with `AspectRatio` (offset 40), an explicit V1/V2 parser (44/48), aspect applied once for both Perspective and Orthographic, and removal of all sensor-derived aspect overrides (CAMERA_UPDATE, CAMERA_CREATE, perspective branch). Commit `6dea4f8`.

No MIG numbering changed here. Migration numbering normalization (AGENTS.md vs `65-phase1.4-foundation-stabilization.md`) is deferred to a separate documentation cleanup.

### Short-term (Standalone, No UE Build Required)
1. ~~**Phase 9 Stage 3B — Discovery Scan.**~~ **IMPLEMENTED** ✅ `PASS_DISCOVERY_LOCALHOST_SCAN` (TCP connect probe; no UDP broadcast)
2. ~~**Phase 9 Stage 3C — Discovery Auto-fill / Connect UX.**~~ **IMPLEMENTED** ✅ `PASS_DISCOVERY_CONNECT_UX` (apply_discovery_result, "Use Discovered Server"/"Discover & Connect" operators, 38 tests)
3. **Manual E2E.1 — Camera Frustum Crash Guard.** `PASS_CAMERA_FRUSTUM_CRASH_GUARD`. Helper `ConfigureLiveSyncCameraActor()` suppresses frustum renderer on LiveSync cameras. Build: clean. Static tests: 24/24 PASS. Runtime: UE 5.7.4 validated — no crash, all 6 required markers present. Blockers: STALE_LOG_READER_RISK, BLENDER_ADDON_ENABLE_UNVERIFIED. Docs: `manual-e2e-camera-crash-investigation.md`, `manual-e2e-log-hygiene.md`.
4. **Manual E2E.4 — Signal 6 + Signal 11 Runtime Revalidation.** Signal 6: FIXED (`[CAMERA][FRUSTUM_GUARD]` present, camera lifecycle successful). Signal 11: CONFIRMED — `CommonUnixCrashHandler: Signal=11` in `libUnrealEditor-SceneOutliner.so`. Crash: `SSceneOutliner::EnsureParentForItem` ↔ `AddUnfilteredItemToTree` infinite loop. Hierarchy guard not exercised (test camera had no parent). 106/106 static tests PASS. Classification: `FAIL_MANUAL_E2E_SCENE_OUTLINER_PARENT_GUARD`. Tag `manual-e2e-camera-crash-guard-stable` remains **provisional**. Docs: `manual-e2e-camera-crash-investigation.md` (E2E.4 section).
5. **Manual E2E.5 — SceneOutliner Crash Isolation (Runtime Complete).** Injector created + injector bug fixed (`--hierarchy` mode now uses PT_CREATE with parent GUID, not PT_TRANSFORM). Static: 132/132 PASS. Runtime (5 tests, fresh UE per test): All PASS — 0 Signal 11, 0 Signal 6. **PASS_E2E5_SCENE_OUTLINER_ISOLATION_NO_REPRO** — The original Signal 11 crash did NOT reproduce under isolation. Docs: `manual-e2e-camera-crash-investigation.md` (E2E.5 section).
6. **Manual E2E.6 — Hierarchy Guard Marker Confirmation (Runtime Complete).** Added `--hierarchy-confirm` mode. Valid attach confirmed via `[HIERARCHY][ATTACH]` markers. Cycle detection confirmed (4x `[HIERARCHY][CYCLE]`). `[HIERARCHY][ATTACH_GUARD]` not visible in pre-built binary (Log level). C++ changes ready but blocked by pre-existing build errors. **PASS_E2E6_VALID_HIERARCHY_ATTACH_CONFIRMED_PARTIAL**. Static: 139/139 PASS.
7. **Manual E2E.6B — C++ Diagnostic Logging Revert.** Build failed (11 errors, pre-existing `bPendingKill` removal in UE5.7). C++ production source change reverted from 2939ce1. Hierarchy confirmation remains tooling-only. **PASS_E2E6_VALID_HIERARCHY_ATTACH_CONFIRMED_PARTIAL_NO_CPP_CHANGE**.
8. **Manual E2E.7 — UE5.7 Compile Compatibility Cleanup.** Fixed 4 `AActor::bPendingKill` access locations via `IsLiveSyncActorInvalidForAttach()` helper. Fixed `SetNum(bool)` deprecation. Build SUCCEEDED (0 errors, 0 warnings). Runtime smoke PASS — `[HIERARCHY][ATTACH_GUARD]` visible in rebuilt binary, all markers confirmed, 0 signals. Static: 158/158 PASS. **PASS_E2E7_UE57_COMPILE_COMPATIBILITY_CLEAN**.
9. **Manual E2E.8 — Full Signal 6/11 Runtime Regression After Rebuild.** Full regression after UE5.7 compile cleanup. **FAIL_E2E8_SCENE_OUTLINER_REGRESSION**. Test A (camera full lifecycle): Signal 11=1, SceneOutliner crash. Test B (hierarchy confirm): PASS, all markers, 0 signals. Test C (legacy camera): Signal 11=1, SceneOutliner crash. Key finding: SceneOutliner crash is a separate code path from frustum guard (`[CAMERA][FRUSTUM_GUARD]` present). Pre-existing issue, not a regression from E2E7. No tag created. Old tag remains PROVISIONAL.
10. **Manual E2E.9 — Camera SceneOutliner Safe Lifecycle (PARTIAL).** Frustum guard confirmed working. SceneOutliner crash remained (heap corruption during outliner tree rebuild). Superseded by W3 workaround in E2E.10.
11. **Manual E2E.10 — Camera SceneOutliner Workaround (COMPLETED).** `PASS_E2E10_CAMERA_SCENEOUTLINER_WORKAROUND`. `FActorSpawnParameters::bHideFromSceneOutliner=true` eliminates SceneOutliner crash. Tag `manual-e2e-camera-crash-guard-stable` is now **FINAL (non-provisional)**.

### Short-term (Standalone, No UE Build Required)
3. **Proactive Scene Health.** Recurring scene scan to detect divergent GUID states.
4. **Test-only gap coverage.** Create test for `phase5d_reconnect_ui.py` content assertions.
5. **Phase 8 BackpressureACK (0x10).** Implement ack-based flow control with retransmit for high-throughput scenes.

### Medium-term (May Require UE Build)
6. **Camera property keyframe extraction.** Extract FCurves for focal length, aperture, focus distance, sensor size, DOF → new property track types.
7. **Procedural mesh full attribute sync.** Extend PT_Mesh V5 payload with normals, UVs, tangents, vertex colors.
8. **UE→Blender reverse sync.** Active camera, timeline, or playback state from UE back to Blender.

### Long-term
9. **Multi-client / interest management.** Per-client subscription filtering (dirty-flag approach from Phase 8 design).
10. **LevelSequence asset streaming.** Load/save LevelSequence assets to disk with full keyframe data for persistent playback.
