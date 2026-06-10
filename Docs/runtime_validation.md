# UELiveSync Runtime Validation

## Summary

**Result: PASS** (2026-06-10)

All Phase 10D runtime gaps are closed. Commit `1ef954a` fixes sequencer state reset on reconnect.

Validated:
- UE plugin loads.
- UELiveSync listener opens on port 57000.
- Tick executes in windowed mode (NOT in -RenderOffScreen -NoCEF or -NullRHI).
- Blender connects to UE.
- TCP packets reach UE.
- UE network thread enqueues packets.
- UE game-thread packet processor handles packets.
- Actors spawn, transforms apply, renames replicate.
- Visibility hide/show apply.
- MESH-parent hierarchy attach/detach.
- Mesh/asset identity packet flow confirmed.
- FBX import/reimport with real FBX files.
- Sequencer keyframe visibility channels 9–10 (hide_viewport, hide_render).
- No queue backlog, no dropped packets.

## Historical Gaps (Closed in Phase 10D)

| Gap | Phase 10D Evidence | Result |
|-----|--------------------|--------|
| Visibility runtime path | `.opencode/evidence/runtime_gap_tests/10d2r/` | ✅ PASS |
| Hierarchy with MESH parent | `.opencode/evidence/runtime_gap_tests/10d2r/` | ✅ PASS |
| Real FBX import/reimport | `.opencode/evidence/runtime_gap_tests/10d3/` | ✅ PASS |
| Keyframe visibility 9/10 | `.opencode/evidence/runtime_gap_tests/keyframe_10d4_final_smoke/` | ✅ PASS |

## Launch Profile

The stable runtime validation profile on Fedora 44 / NVIDIA 595.80:

```bash
./UnrealEditor <Project>.uproject -windowed -ResX=1280 -ResY=720 -nohighdpi -log
```

Critical: `-RenderOffScreen -NoCEF` blocks Tick() in UE5.7.4 on this platform. `-NullRHI` disables networking. Use windowed mode only.

The previous profile (`CEF_DISABLE_GPU=1` with SDL/X11 env vars) is no longer recommended — it caused CEF GPU crash cascades.

## Evidence

Phase 10D runtime evidence:
- `.opencode/evidence/runtime_gap_tests/10d2r/` — visibility + hierarchy
- `.opencode/evidence/runtime_gap_tests/10d3/` — FBX import/reimport
- `.opencode/evidence/runtime_gap_tests/keyframe_10d4_final_smoke/` — keyframe visibility

## Feature Matrix

| Feature | Result | Notes |
|---------|--------|-------|
| UE plugin load | PASS | |
| TCP listener | PASS | port 57000 |
| Tick/FTSTicker | PASS | windowed mode |
| Blender addon load | PASS | |
| Blender connection | PASS | |
| NetworkThread enqueue | PASS | |
| Game-thread processing | PASS | |
| Object create/spawn | PASS | |
| Transform packets | PASS | |
| Rename replication | PASS | |
| Visibility hide/show | PASS | Blender driver |
| MESH-parent hierarchy | PASS | |
| Mesh/asset packets | PASS | |
| FBX import (real file) | PASS | StaticMesh asset created |
| FBX reimport (same GUID) | PASS | existing asset replaced |
| Sequencer keyframe ch9–10 | PASS | BoolTrack apply, applied=6 |
| Burst 20 | PASS | |
| Stability | PASS | no crash |
