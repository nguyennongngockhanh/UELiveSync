# Phase 6C — Visibility Replication Vertical Slice

**Date**: 2026-05-25  
**Scope**: Second semantic-event vertical slice — Blender hide/unhide → UE editor visibility  
**Status**: IMPLEMENTED (pending live UE validation)

## Summary

Visibility replication validates that the semantic-event pattern
(provenance → suppression → replay → observability) established by
rename generalizes to a different editor mutation:

| Criterion | Rename | Visibility | Validation |
|-----------|--------|------------|------------|
| Wire format | Variable-length (strings) | Fixed 29 bytes | Different, both correct |
| Callback risk | High | None | Pattern works with and without |
| Suppression | Hard requirement | Architectural consistency | RAII pattern generalizes |
| Replay idempotency | String compare | Bool compare | Sequence tracker pattern identical |
| Blender API | `obj.name` | `obj.hide_set()`/`obj.hide_get()` | Different API, same diff pattern |

## Files Changed

| File | Change |
|------|--------|
| `UE_Plugin/.../Public/SyncTypes.h` | PT_Visibility (0x0B), FVisibilitySequenceTracker, 4 visibility counters, FNV hash update |
| `UE_Plugin/.../Public/UELiveSyncSubsystem.h` | HandleVisibility() declaration |
| `UE_Plugin/.../Private/UELiveSyncSubsystem.cpp` | HandleVisibility(), FScopedVisibilitySuppression, GVisibilitySequences, PT_Visibility dispatch case, kValidTypes 0x0B, tracker clears in StopNetworkThread + ConsoleReset, [VISIBILITY] logs |
| `Blender_Addon/network.py` | PT_Visibility constant, serialize_visibility(), _visibility_sequences, _close_internal cleanup |
| `Blender_Addon/sync.py` | _last_visibility_state dict, hide_get() detection, vis_payloads_to_send list, stale cleanup |
| `tests/phase6_visibility_validation.py` | 12 tests (all auto-skip without UE) |
| `tests/run_phase6_visibility.py` | Consolidated test runner |
| `Docs/Architecture/20-phase6-visibility-scope-lock.md` | Status → IMPLEMENTED |
| `Docs/Architecture/21-phase6-vertical-slice-visibility.md` | Status → STABILIZED, revision history updated |

## Docs Created

| File | Purpose |
|------|---------|
| `Docs/Architecture/22-semantic-event-architecture-conventions.md` | Formalized architectural conventions for ALL semantic lanes |

## Verification

- 28/28 visibility constructs verified present across all source files
- 49/49 runtime audit checks pass (no regressions)
- 12 visibility tests: all structurally complete, auto-skip when UE unavailable
- Frozen runtime systems: zero modifications (LiveSyncQueue, PendingAssetQueue, LiveSyncRunnable, FSyncTransformState, header layout, Tick pipeline, thread ownership)

## Pending

- End-to-end validation against UE editor on `:57000`
- `python3 tests/run_phase6_visibility.py` full run with live UE
