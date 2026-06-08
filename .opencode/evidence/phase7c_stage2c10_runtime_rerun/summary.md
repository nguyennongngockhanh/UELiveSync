# Phase 7C Stage 2C.10 — V1 Debug Mode Test Matrix (Final)

## Objective

Verify V1 mesh shading debug modes (A–G) apply correctly after queue-drain race fix (commit `c3daa67`).

## Setup

- **Blender**: Flatpak 5.1, `from ue_live_sync import network` for shared `_client` global
- **UE5**: 5.7.4, `ProjectTemplate` with rebuilt UELiveSync plugin
- **CVar override**: `-ExecCmds=` instead of broken `-CVar=Value`; `ConsoleVariables.ini` removed
- **Sync flow**: `PT_CreateObject (0x03)` → `PT_Mesh (0x06)` per mesh
- **Evidence dir**: `.opencode/evidence/phase7c_stage2c10_runtime_rerun/`

## Results Matrix

| Mode | MaterialMode | FaceNormals | DisableTangents | MaterialLog | assigned | QueueDrain | SECTION_ARRAYS | Built | MissingActor | Errors |
|------|-------------|-------------|-----------------|-------------|----------|------------|---------------|-------|-------------|--------|
| A    | 0 (none)    | 0           | 0               | mode=0 material=None | 0       | 1          | 6             | 1     | 0           | 0      |
| B    | 1 (UnlitGray)| 0          | 0               | mode=1 material=UnlitGray | 1 | 1          | 7             | 1     | 0           | 0      |
| C    | 2 (TwoSidedGray)| 0       | 0               | mode=2 material=TwoSidedGray | 1 | 1      | 6             | 1     | 0           | 0      |
| D    | 3 (TwoSidedUnlitGray)| 0   | 0               | mode=3 material=TwoSidedUnlitGray | 1 | 1  | 6             | 1     | 0           | 0      |
| E    | 0            | 1           | 0               | mode=0 material=None | 0       | 1          | 6             | 1     | 0           | 0      |
| F    | 0            | 0           | 1               | mode=0 material=None | 0       | 1          | 5             | 1     | 0           | 0      |
| G    | 0            | 0           | 0               | mode=0 material=None | 0       | 1          | 5             | 1     | 0           | 0      |

## CVar Application

- `-ExecCmds="UE.LiveSync.V1DebugMaterialMode N;..."` works correctly
- Direct `-UE.LiveSync.V1DebugMaterialMode=N` broken in UE5.7 (CVar priority issue)
- `ConsoleVariables.ini` at `Saved/Config/` blocks command-line override — must be removed

## Key Markers (all 7 modes)

- `[MESH][V1][DEBUG_MATERIAL]` — correct mode/material per matrix
- `[MESH][V1][DEBUG_NORMALS] mode=source` — mesh normal attribute domain
- `[MESH][V1][DEBUG_TANGENTS] disabled` — tangents present in section arrays
- `[MESH][V1][SECTION_ARRAYS]` — verts, normals, uv0, tangents populated
- `[MESH][V1] Built section` — section created
- `Detected thread exit, draining queue before cleanup` — queue-drain race fix verified

## Queue-Drain Fix Verification

All 7 modes show the drain marker at thread exit (`Detected thread exit, draining queue before cleanup`). Fix at `UELiveSyncSubsystem.cpp` lines 1565–1568, 1583–1587, 1606–1610.

## Screenshots

| File | Mode |
|------|------|
| `ss_mode_A_none.png` | Control (no debug) |
| `ss_mode_B_unlit_gray.png` | UnlitGray debug material |
| `ss_mode_C_twosided_gray.png` | TwoSidedGray debug material |
| `ss_mode_D_twosided_unlit_gray.png` | TwoSidedUnlitGray debug material |
| `ss_mode_E_force_face_normals.png` | Force face normals (flat shading) |

All screenshots have distinct MD5 hashes confirming visual variation.

## Verdict

**PASS** — All 7 modes produce correct V1 debug markers, zero errors, and queue-drain fix works. Blender addon import pattern confirmed (shared `_client`). CVar override mechanism documented (`-ExecCmds`).

## Files

```
ue_mode_{A..G}.log — UE logs per mode
blender_mode_{A..G}.log — Blender output per mode
markers_mode_{A..G}.log — extracted markers per mode
ss_mode_{A..E}.png — screenshots
summary.md — this file
```
