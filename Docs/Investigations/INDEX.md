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
