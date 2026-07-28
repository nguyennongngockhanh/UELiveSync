# 63 — Confirmed Root Cause: Visibility Viewport Refresh

## Status: FROZEN

Root cause below is **Root Cause Confirmed** — proven by direct runtime
evidence, not hypothesis.

---

## BUG-006: Viewport Not Refreshed After Visibility Change

**Symptom**: Hiding/unhiding objects in Blender during sync updated UE
Outliner and actor state correctly, but viewport continued rendering the
old visibility state until manual interaction (click viewport, move camera).

---

### Root Cause

`HandleVisibility()` correctly calls `SetIsTemporarilyHiddenInEditor(bHidden)`
to update actor visibility state, but does not call `RequestEditorViewportRefresh()`
afterwards. The `FEditorViewportClient::Invalidate()` is never issued, so the
editor viewport does not redraw until external interaction triggers it.

This is the same pattern as BUG-001 (mesh reconstruction) and BUG-002
(transform update): Editor state changes correctly, but viewport invalidation
is missing.

---

### Evidence

**Code analysis**:

| Call site | After what | Has viewport refresh? |
|-----------|-----------|----------------------|
| Line 6998 | `InterpolateTransforms()` | ✅ `RequestEditorViewportRefresh()` |
| Line 17728 | `ReconstructCompletedMeshes()` | ✅ `RequestEditorViewportRefresh()` |
| Line 9577 | `SetIsTemporarilyHiddenInEditor()` | ❌ **Missing** |

**Runtime verification** (TEST A: Hide, TEST B: Unhide):

- UE log confirmed `HandleVisibility()` executed
- UE log confirmed `SetIsTemporarilyHiddenInEditor()` applied
- Viewport updated immediately without user interaction

---

### Fix

Added `RequestEditorViewportRefresh()` after `SetIsTemporarilyHiddenInEditor()`
in `HandleVisibility()`. One line. Matches existing pattern from BUG-001/002.

**Scope**: Plugin only (`UELiveSyncSubsystem.cpp`). No engine changes.

---

### Regression Tests

| Test | Scenario | Expected | Result |
|------|----------|----------|--------|
| A | Hide object in Blender | UE viewport updates immediately | PASS |
| B | Unhide object in Blender | UE viewport updates immediately | PASS |

---

### Files Modified

| File | Change |
|------|--------|
| `UE_Plugin/UELiveSync/Source/UELiveSync/Private/UELiveSyncSubsystem.cpp` | Added `RequestEditorViewportRefresh()` to `HandleVisibility()` |
| `UE_Plugin/UELiveSync/Source/UELiveSync/Public/UELiveSyncSubsystem.h` | Added `RequestEditorViewportRefresh()` declaration |

---

### Summary

| Bug | Root cause | Fix location | Engine change? |
|-----|-----------|-------------|----------------|
| BUG-001 | MarkRenderStateDirty ≠ viewport invalidation | `ReconstructCompletedMeshes` | No |
| BUG-002 | SetActorTransform ≠ viewport invalidation | `InterpolateTransforms` | No |
| BUG-006 | SetIsTemporarilyHiddenInEditor ≠ viewport invalidation | `HandleVisibility` | No |

**Pattern**: All three are the same class of bug — editor state updates
correctly but viewport invalidation is missing. `RequestEditorViewportRefresh()`
is the standard fix.

---

**Commit**: TBD on `bugfix/visibility-viewport-refresh`
**Branch workflow**: `bugfix/visibility-viewport-refresh` → `--ff-only` merge → delete
