# 61 — Confirmed Root Causes: Viewport Refresh & Actor Spawn Position

## Status: FROZEN

All three root causes below are **Root Cause Confirmed** — proven by direct
runtime evidence, not hypothesis.

---

## BUG-001: Viewport Not Refreshed After Mesh Reconstruction

**Root cause**: `MarkRenderStateDirty()` does not trigger an editor viewport
redraw. The SceneProxy is correct, but the editor viewport only repaints on
editor tick or user focus.

**Evidence**: CVar `UE.LiveSync.ForceViewportRedraw` toggle test proved that
`Invalidate(false, false)` on the perspective viewport makes the mesh visible.

**Fix**: `RequestEditorViewportRefresh()` — calls
`FEditorViewportClient::Invalidate(false, false)` on the perspective viewport.
Applied after `ReconstructCompletedMeshes()` when `Reconstructed.Num() > 0`.

**Scope**: Plugin only. No engine changes.

**Runtime logs**: `[MESH][VIEWPORT-REDRAW]`

---

## BUG-002: Viewport Not Refreshed After Transform Update

**Root cause**: `SetActorTransform()` does not trigger an editor viewport
redraw. Same underlying mechanism as BUG-001.

**Evidence**: `[TRANSFORM][VERIFY]` instrumentation proved:
`old=(0,0,0) new=(8,0,0) renderState=1 renderDirty=0 recentlyRendered=0`
→ after `Invalidate(false, false)` → `recentlyRendered=1` → mesh moves
without clicking UE.

**Fix**: `RequestEditorViewportRefresh()` — same helper, applied after
`InterpolateTransforms()` when `InterpCount > 0`.

**Scope**: Plugin only. No engine changes.

**Runtime logs**: `[TRANSFORM][VIEWPORT]`

---

## BUG-003: Actor Spawned at Origin Instead of Blender Location

**Root cause**: `SpawnActor<AActor>(AActor::StaticClass(), FTransform(...))`
silently drops the spawn transform because `AActor::StaticClass()` has no
root component at spawn time. The transform parameter is discarded by the
engine.

**Evidence** (6-point lifecycle instrumentation):

| Step | Location | Meaning |
|------|----------|---------|
| [1-ENTRY] | (257.6, 0, 0) | Incoming wire value — **correct** |
| [2-POST-SPAWN] | (0, 0, 0) | **Transform lost at SpawnActor** — root=NULL |
| [3-PRE-SETROOT] | (0, 0, 0) | Downstream of step 2 |
| [4-POST-SETROOT] | (0, 0, 0) | Downstream of step 2 |
| [5-POST-REGISTER] | (0, 0, 0) | Downstream of step 2 |
| [6-PRE-RETURN] | (304.9, 0, 0) | Fix applied — correct |

The transform was correct at entry but lost immediately after `SpawnActor`.
Every subsequent step is a downstream consequence.

**Fix**: After `SetRootComponent(MeshComp)` + `RegisterComponent()`, call:

```cpp
NewActor->SetActorTransform(
    FTransform(Rotation, Location, Scale));
```

This restores the intended spawn transform after the root component exists.

**Scope**: Plugin only (`HandleCreateObject` in `UELiveSyncSubsystem.cpp`).
No engine changes. No `UpdateTargetTransform` added.

---

## Summary

| Bug | Root cause | Fix location | Engine change? |
|-----|-----------|-------------|----------------|
| BUG-001 | MarkRenderStateDirty ≠ viewport invalidation | `ReconstructCompletedMeshes` | No |
| BUG-002 | SetActorTransform ≠ viewport invalidation | `InterpolateTransforms` | No |
| BUG-003 | SpawnActor drops transform (no root component) | `HandleCreateObject` | No |
