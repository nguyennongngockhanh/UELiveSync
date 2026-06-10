# UELiveSync Runtime Validation

## Summary

**Result: PASS WITH GAPS** (2026-06-10)

Validated:
- UE plugin loads.
- UELiveSync listener opens on port 57000.
- Tick executes in windowed + CEF_DISABLE_GPU=1 mode (NOT in -RenderOffScreen -NoCEF).
- Blender connects to UE.
- TCP packets reach UE.
- UE network thread enqueues packets.
- UE game-thread packet processor handles packets.
- Actors spawn, transforms apply, renames replicate.
- Mesh/asset identity packet flow confirmed.
- FBX import request packet parsed.
- No queue backlog, no dropped packets.

Gaps:
- Visibility runtime path not validated in background Blender.
- Hierarchy with EMPTY parent not transmitted.
- Keyframe visibility 9/10 not tested.
- FBX import/reimport not tested with real file.

## Launch Profile

See `.opencode/evidence/runtime_full_test/final_report.md` for the recommended stable launch command.

Critical: `-RenderOffScreen -NoCEF` blocks Tick() in UE5.7.4 on this platform. Use windowed mode with `CEF_DISABLE_GPU=1` instead.

## Evidence

Full evidence in `.opencode/evidence/runtime_full_test/`:
- `final_report.md` — structured report
- `ue_launch.log` — UE log with packet processing
- `blender_launch.log` — Blender driver output
- `source_tests.txt` — static test results

## Feature Matrix

| Feature | Result | Notes |
|---------|--------|-------|
| UE plugin load | PASS | |
| TCP listener | PASS | port 57000 |
| Tick/FTSTicker | PASS | windowed + CEF_DISABLE_GPU=1 |
| Blender addon load | PASS | |
| Blender connection | PASS | |
| NetworkThread enqueue | PASS | |
| Game-thread processing | PASS | |
| Object create/spawn | PASS | 22 actors |
| Transform packets | PASS | |
| Rename replication | PASS | |
| Mesh/asset packets | PASS | |
| FBX request parsing | PASS/PARTIAL | fake file |
| FBX import/reimport | NOT TESTED | |
| Visibility | PARTIAL | background mode limit |
| Hierarchy | NOT TESTED | EMPTY parent |
| Keyframe 9/10 | NOT TESTED | |
| Burst 20 | PASS | |
| Stability | PASS | no crash |

## Required Follow-up

1. Interactive visibility test.
2. Hierarchy test with MESH parent.
3. Keyframe 9/10 runtime test.
4. Real FBX import test.
