# Session ses_0e48 — UE Hang on Start Sync (P0 Investigation + Time-Slicing Fix)

## Goal
Fix UE game-thread hang when pressing Start Sync in Blender with multiple mesh objects + camera objects.

## Root Cause (Mesh Scene)
Both `ReconstructCompletedMeshes()` (V5) and `BuildV1MeshFromReassembly()` (V1) process ALL completed mesh rebuilds in a single game-thread tick. During Start Sync burst:
- 50+ meshes × (CalculateTangentsForMesh + CreateMeshSection → MarkRenderStateDirty → scene proxy rebuild stall) freezes the game thread for seconds.
- `SendAllEndOfFrameUpdatesInternal()` blocks on render thread sync.

## Fix Applied (UELiveSyncSubsystem.cpp)

### 1. Time-slicing: `CVarLiveSyncMaxMeshBuildsPerTick` (default 10)
- Added per-tick build counter in both `ReconstructCompletedMeshes()` (V5, L16382+) and `BuildV1MeshFromReassembly()` (V1, L16948+).
- When `BuildsThisTick >= MaxBuildsPerTick`, the loop breaks and remaining GUIDs stay in `PendingMeshReassembly` / `PendingV1MeshReassembly` for next tick.
- CVar declared at L723 with `ECVF_Default`.

### 2. UpdateMeshSection optimization (V5 multi- + single-section, V1)
- When `ExistingSection->ProcVertexBuffer.Num() == NewVerts` (topology unchanged), use `UpdateMeshSection()` instead of `CreateMeshSection()`.
- Avoids `MarkRenderStateDirty` → deferred scene-proxy rebuild.
- Applied to V5 multi-section path (L16725), V5 single-section fallback (L16763), V1 final section (L17807).

### 3. Removed stale `DiagBuildCount++` dupe (V5)
- Moved `DiagBuildCount++` to correct position in multi-section path (L16833 removed, L16902 added).

### 4. Removed redundant `MarkRenderStateDirty()` call (V5 multi-section, L16874)
- `UpdateBounds()` + `SetRootComponent` already handle this; explicit `MarkRenderStateDirty()` is unnecessary and worsens stall.

## Pre-existing Camera Hang (Not Fixed)
The camera-specific hang (scene with ACameraActor) was confirmed pre-existing:
- GDB backtrace: `SendAllEndOfFrameUpdatesInternal()` → `FStaticMeshComponentBulkReregisterContext`.
- Occurs even with 0 mesh builds (camera-only scene).
- NOT caused by time-slicing changes.
- Root cause: likely render-thread sync issue triggered by CameraActor spawn/component registration in editor world.
- Needs separate investigation.

## Key Files Changed
- `UE_Plugin/UELiveSync/Source/UELiveSync/Private/UELiveSyncSubsystem.cpp` (L723 CVar, L16382 V5 time-slice, L16948 V1 time-slice)

## Files NOT Changed
- No packet format changes.
- No protocol constant changes.
- No Blender addon changes.
- No header changes (CVar is static-local, no header declaration needed).

## Tests Run
- Mesh-only scene: UE responsive (time-slicing works).
- Camera scene: pre-existing hang confirmed (separate issue).
