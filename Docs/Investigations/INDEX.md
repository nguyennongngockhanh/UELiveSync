# Investigation Registry

| ID | Title | Status | Priority | Depends-on | Started | Owner |
|----|-------|--------|----------|------------|---------|-------|
| INV-2026-001 | FBX Reconnect Import Failure | Suspended | P0 | — | 2026-07-03 | Khanh |
| INV-2026-002 | Viewport Not Refreshed After Actor Spawn | Plugin Workaround Failed | P0 | — | 2026-07-12 | Khanh |
| INV-2026-003 | Identify Source of ~331ms Viewport Tick Interval | Causal Boundary Established | P0 | — | 2026-07-20 | Khanh |
| INV-2026-004 | Identify Source of ~329ms Main Loop Scheduling Gap | Execution Boundary Identification Required | P0 | INV-2026-003 | 2026-07-20 | Khanh |
| INV-2026-005 | Execution Boundary Inventory v2 and Boundary Records | Closed — Gate PASS | P0 | INV-2026-004 | 2026-07-20 | Khanh |
| INV-2026-006 | Boundary Evaluation | In Progress | P0 | INV-2026-005 | 2026-07-20 | Khanh |
| INV-2026-009 | Camera Orientation Mismatch (Blender ↔ UE) | Closed | P0 | — | 2026-07-30 | Khanh |
| INV-2026-010 | PiP Viewport Invalidation After OBJECT_CREATE / Mesh Rebuild | Closed | P0 | — | 2026-07-30 | Khanh |
| INV-2026-011 | Ortho Scale Unit Mismatch (Blender m → UE cm) | Closed | P0 | — | 2026-07-31 | Khanh |
| INV-2026-012 | Camera Aspect Ratio Not Synced via Protocol | Closed | P0 | — | 2026-07-31 | Khanh |
| INV-2026-013 | Camera Aspect Not Updated When Render Resolution Changes | Closed | P0 | — | 2026-08-01 | Khanh |

## INV-2026-009 — Camera Orientation Mismatch (Blender ↔ UE)

Alias: INV-C9.

- **Symptom**: UE camera faced `+X` while Blender camera viewed along `-Z`; yaw/pitch/roll rotations were wrong after sync.
- **Root cause**: `get_transform` applied the object-frame conversion `C*M*C` (Y-flip) but never converted the camera's intrinsic view axis (Blender −Z vs UE +X).
- **Resolution**: Applied a fixed camera-local basis rotation mapping `+X→−Z, +Y→+X, +Z→−Y` after `C*M*C` when `obj.type == 'CAMERA'`. Explanatory comment kept in `Blender_Addon/sync.py`; all `[INV-C9]` instrumentation removed from Blender addon and UE plugin.
- **Evidence**: Identity now settles `recvQ=(0.5,0.5,-0.5,0.5)` with root forward `(0,0,-1)`; Roll 90 and Pitch 90 runtime quaternions match expected corrected values. Yaw 90 validated mathematically.
- **Commit**: none yet (working-tree change, uncommitted).

## INV-2026-010 — PiP Viewport Invalidation After OBJECT_CREATE / Mesh Rebuild

Alias: INV-E2.

- **Symptom**: Viewport did not refresh (PiP/camera view) after actor spawn or mesh rebuild.
- **Root cause**: Editor viewport invalidation policy — spawn/rebuild did not invalidate the locked-actor viewport, so it kept showing stale frames.
- **Resolution**: Viewport invalidation after OBJECT_CREATE / visibility / mesh rebuild. Instrumentation retained in `UELiveSyncSubsystem.cpp` (`[INV-E2][...]` markers) for future regression checks.
- **Commit**: none yet (working-tree change, uncommitted).

## INV-2026-011 — Ortho Scale Unit Mismatch (Blender m → UE cm)

- **Symptom**: Orthographic framing in UE did not match the Blender camera; ortho width was off by the Blender meter → UE cm factor.
- **Root cause**: `ortho_scale` was sent raw from Blender (meters) while `UCameraComponent::OrthoWidth` is in world units (cm). The m→cm conversion already applied to location in `get_transform` was never applied to ortho scale.
- **Resolution**: Added `_ue_ortho_scale()` in `Blender_Addon/sync.py` (`ortho_scale * 100.0`), used in `_build_camera_signature`, the CameraDef def-scan path, and the standalone dirty-detection path.
- **Evidence**: Runtime verified — ortho framing matches Blender only after applying `ortho_scale × 100`; both projection modes then match the Blender camera.
- **Commit**: `5ec62be`.

## INV-2026-012 — Camera Aspect Ratio Not Synced via Protocol

- **Symptom**: UE camera aspect did not match Blender framing in both Perspective and Orthographic. UE derived aspect from sensor ratio (`36/24 = 1.5`) while Blender framing depends on render resolution (`1920×1080 = 1.7778`).
- **Root cause**: `PT_CameraDef` carried no aspect field, so UE fell back to `SensorWidthMM / SensorHeightMM`. Aspect ratio is camera state (render framing), not viewport — it must come from Blender render resolution including pixel aspect.
- **Resolution**: Extended `PT_CameraDef` from 44 → 48 bytes adding `AspectRatio` at offset 40 (V2; CameraFlags → 44, Reserved → [45-47]). Blender sends `render_aspect_ratio()` at both CameraDef emission points. UE parses both V1 (44B — legacy: sensor-ratio fallback) and V2 (48B) via an explicit `switch(ObjSize)`, applies aspect once before the projection branch (both Perspective and Orthographic), and removes every sensor-derived aspect override (perspective branch, CAMERA_UPDATE, CAMERA_CREATE).
- **Evidence**: Runtime verified — `DEF_APPLY persp ... aspect=1.7778` and `DEF_APPLY ortho width=500.0 ... aspect=1.7778` match Blender. Wire tests 26/26 PASS.
- **Commit**: `6dea4f8`.

## INV-2026-013 — Camera Aspect Not Updated When Render Resolution Changes

- **Symptom**: Changing Blender render resolution (Output Properties → Resolution X/Y) during live sync did not update UE camera aspect; UE kept the old aspect until a camera-datablock field changed.
- **Root cause**: `_build_camera_signature()` (signature feeding the CameraDef dirty-detection gate) held only camera-datablock fields. `render.resolution_x/y` and `pixel_aspect_x/y` were absent, so a render-settings change never dirtied the signature → CameraDef not re-emitted. `_aspect = render_aspect_ratio(bpy.context)` was computed only inside the already-failed gate.
- **Resolution**: Added `render.resolution_x`, `render.resolution_y`, `render.pixel_aspect_x`, `render.pixel_aspect_y` to `_build_camera_signature()` in `Blender_Addon/sync.py`. Signature now depends on source state, not derived aspect — any render-settings change (including same-aspect changes like `1920×1080 → 3840×2160`) re-emits CameraDef. Protocol/wire unchanged (aspect payload already present from INV-2026-012).
- **Evidence**: Runtime verified — each resolution field change re-emits CameraDef with the correct aspect applied in UE (`1.4815`, `1.3333`, `3.2`, `1.7778`, `0.8889`); same-aspect round-trip `3840×2160 → 1920×1080` returns to `1.7778`. Regression test `tests/phase7g_stage3_camera_signature_render_state.py` 11/11 PASS; wire test 26/26 PASS.
- **Commit**: `37adf40`.
