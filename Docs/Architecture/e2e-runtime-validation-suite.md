# E2E Runtime Validation Suite

**Date:** 2026-06-16
**Baseline:** `phase8-audit-stable` (679b95b)
**Classification:** `PASS_E2E_AUDIT_ONLY`

## Purpose

Define a practical end-to-end runtime validation suite for the currently
implemented UELiveSync features. The suite orchestrates existing standalone
TCP injectors and validators to run a comprehensive runtime check against
a live UE session.

## Current Automation Inventory

### Standalone TCP Injectors (no Blender required)

| Tool | Packets | Validates |
|------|---------|-----------|
| `tools/uelivesync_7f_timeline_state_client.py` | PT_TimelineState (0x19) | Timeline state apply markers |
| `tools/uelivesync_7f_playback_transport_client.py` | PT_PlaybackTransport (0x1A) + optional full flow | Playback transport apply markers |
| `tools/uelivesync_7g_camera_transform_client.py` | PT_Create (0x03), PT_Transform (0x01), PT_ActiveCamera (0x15), PT_CameraDef (0x1B) | Camera lifecycle markers |
| `tools/uelivesync_7g_camera_def_client.py` | PT_ActiveCamera (0x15), PT_CameraDef (0x1B) | Camera definition markers |
| `tools/uelivesync_10b_tcp_client.py` | PT_SequencerOp (0x18), PT_Create (0x03), PT_Transform (0x01), PT_Keyframe (0x17) | Keyframe apply markers |

### Log Validators (no UE Python required)

| Tool | Reads | Validates |
|------|-------|-----------|
| `tools/uelivesync_10a7a_validation.py` | ProjectTemplate.log | Visibility keyframe channels 9/10 |

### UE Python Validators (require in-UE script execution)

| Tool | Validates | Requires |
|------|-----------|----------|
| `tools/uelivesync_10b3_uepython_asset_load.py` | Asset load (`unreal.load_asset()`) | UE Python console |
| `tools/uelivesync_10c_saved_sequence_inspection.py` | Binding persistence | UE Python result JSON |
| `tools/uelivesync_10d_editor_sequence_validation.py` | Editor open sequence | UE Python result JSON |

### Blender-Dependent Tools (not automated in this suite)

| Tool | Validates | Reason |
|------|-----------|--------|
| `tools/uelivesync_stage10a5_active_sequence.py` | Active sequence via addon | Requires Blender + bpy |
| `tools/uelivesync_10b_asset_sequence_validation.py` | Asset-backed sequence | Requires Blender addon import |

## E2E Orchestrated Flow

The `tools/uelivesync_e2e_runtime_validator.py` tool runs the following
steps in sequence against a live UE editor session:

```
Step 1: Timeline State (3 PT_TimelineState packets)
Step 2: Playback Transport (SetFrame + Play + Pause + Stop)
Step 3: Camera Lifecycle (CREATE + TRANSFORM + ACTIVE_CAMERA)
Step 4: Camera Definition (PT_CameraDef with focal/sensor/clip)
Step 5: Sequencer + Keyframes (CREATE_SEQUENCE + ADD_POSSESSABLE + PT_Keyframe)
Step 6: Queue Diagnostics (read UE log for queue depth / overflow)
Step 7: Summary Report
```

### Manual Steps (not automated)

```
Manual Step A: FBX Handoff Import
  - Open Blender
  - Select mesh object
  - Click "Sync Selected Mesh to UE (FBX)"
  - Check UE log for [FBX] markers

Manual Step B: UE Python Asset Inspection
  - In UE Editor Python console, run generated script
  - Run: python tools/uelivesync_e2e_runtime_validator.py --check-python-result
```

## Validation Categories

| Category | Automated? | Markers Checked |
|----------|-----------|-----------------|
| Timeline state | YES (Step 1) | `[TIMELINE][RECV]`, `[TIMELINE][APPLY]` |
| Playback transport | YES (Step 2) | `[PLAYBACK][RECV]`, `[PLAYBACK][APPLY]` |
| Camera create/transform | YES (Step 3) | `[CAMERA][CREATE]`, `[CAMERA][TRANSFORM_APPLY]`, `[CAMERA][ACTIVE_RECV]` |
| Camera sequencer binding | YES (Step 3) | `[CAMERA][SEQ_BIND]`, `[CAMERA][CUT_APPLY]` |
| Camera definition | YES (Step 4) | `[CAMERA][DEF_RECV]`, `[CAMERA][DEF_APPLY]` |
| Keyframe apply | YES (Step 5) | `[KEYFRAME] Applied seq=... count=11 applied=11 miss=0 unsupp=0` |
| Visibility channels 9/10 | YES (Step 5) | ch9=[0,1,0], ch10=[0,1,0] at frames [1,10,20] |
| Queue depth / drops | YES (Step 6) | No `[QUEUE]` or `PacketsDropped` warnings |
| FBX import | MANUAL | `[FBX][TEMP_IMPORT]`, `[FBX][TEMP_ASSIGN]`, `[FBX][SCALE_INVARIANT]` |
| Asset persistence | MANUAL (UE Python) | `unreal.load_asset()` returns LevelSequence |
| Editor usability | MANUAL (UE Python) | `open_level_sequence()` succeeds |
| Invalid packet rejection | YES (Step 1-7 baseline) | No `Invalid packet type` warnings for reserved types |

## What Cannot Be Validated Runtime

1. **Backpressure ACK (0x10)** — NOT implemented. No validation possible.
2. **Adaptive throttling** — NOT implemented. Send rate is hardcoded 0.016s.
3. **Mesh compression (zlib)** — NOT implemented.
4. **Section builder optimization** — NOT implemented (UE source only, no runtime effect).
5. **Interest management** — NOT implemented.
6. **FMovieSceneFloatChannel/BoolChannel key values** — Not exposed to Python API.
7. **CameraCutTrack via Python** — Not exposed to Python API.
8. **-NullRHI** — Invalid for LiveSync runtime validation (no networking).

## Required CVars

```
UE.LiveSync.Verbose=1
UE.LiveSync.VerboseSyncLogs=1
```

These must be set via `ConsoleVariables.ini` or in-UE `~` console before
running the E2E suite. Without Verbose logging, some diagnostic markers
may not appear.

## Invariant Checks

- `0x02` (PT_Reserved_02) remains reserved/invalid — NOT in `kValidTypes`.
- `0x10` (proposed PT_BackpressureACK) NOT in `kValidTypes` — NOT implemented.
- Phase 8 claimed-implemented features (backpressure, compression, throttle)
  are NOT validated because they do not exist in the codebase.
- No packet type values or protocol version were changed in this audit.

## Classification

`PASS_E2E_AUDIT_ONLY` — E2E suite is documented and tool is created.
Full runtime execution requires:
1. UE editor session (not -NullRHI)
2. CVars set for Verbose logging
3. Blender for FBX import validation (manual step)
4. UE Python console for asset inspection (manual step)

Recommended next stage after runtime execution:
`PASS_E2E_RUNTIME_PARTIAL` if UE-only steps complete successfully.
