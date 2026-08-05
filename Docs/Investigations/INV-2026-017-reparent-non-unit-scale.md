# INV-2026-017 — Reparent with Non-Unit Scale Causes Child Position/Size Jump

**Status**: CLOSED (FIXED) — 2026-08-05
**Priority**: P1
**Owner**: Khanh
**Started**: 2026-08-04
**Closed**: 2026-08-05

## Symptom

When a parent or child has scale ≠ (1,1,1), performing "Set Parent" in Blender causes the child to:
- Jump to a wrong position (location)
- Change size (scale)

The bug does NOT appear when all objects have scale = (1,1,1). The child stays wrong permanently, not just one frame.

## Root Cause — TWO causally-linked bugs (CONFIRMED 2026-08-05)

The two bugs are not independent; they form one causal chain on a parent whose scale is changed *before* any reparent:

1. **B1 — Blender-local transform applied as world after reparent.**
   Blender flushes `OBJECT_UPDATE` (local transform) before `OBJECT_REPARENT` in the same flush. OBJECT_UPDATE carries no parent field, and the child's `TransformStates` still has `bHasParent=false` at that moment → the local value is stored as a *world* target. The subsequent `PT_Transform` carries `bIsLocalTransform=1` + valid parent GUID but hits the "unchanged" early-return (the value equals the stored target), which never established local-space state (`bHasLocalTarget` stayed false). `InterpolateTransforms` then took the ROOT path and applied the Blender-local value as world → child jumps.

2. **B2 — Parent's first static scale update is swallowed by the init path.**
   The actor is spawned from `OBJECT_CREATE` with the transform present at Start Sync (e.g. scale 1). If the user then changes the parent's scale (e.g. 1→2) while it is static, the first `OBJECT_UPDATE` is the first `UpdateTargetTransform` call for that GUID. The initialization path (`!State.bInitialized`) seeded `Current* = Target* = incoming`, assuming the actor already equals the first update. Since the update differs from the actor's actual transform, the change was never detected (state reads as converged) and never applied → the parent stays at spawn scale. The deferred value is only flushed later by a *second* update (motion), at which point the parent snaps to scale 2 and any attached child inherits it — the second half of the user-visible bug.

Evidence (UE log, session 2026-08-05, boundary `b2clean`, GUID `717AEAFC...` chair):

```
06:32:31  OBJECT_CREATE chair scl=(1,1,1)  → actor spawned scale 1 (correct per create data)
06:32:56  OBJECT_UPDATE seq=1 blender_scl=(2,2,2)  → init path seeded Current=Target=2,
          NO apply → ue_world_scl stays (1,1,1)          [B2 symptom]
06:33:00  AFTER_ATTACH cube ... parent_scl=(1.0000,1.0000,1.0000)   [B2 parent scale lost]
06:33:48  OBJECT_UPDATE seq=2 (motion) → ROOT_APPLY target_scl=2 → ue_world_scl=(2,2,2)
          → parent snaps to scale 2; attached child inherits          [flush of deferred state]
```

## Fixes (APPLIED — 2026-08-05, build PASS)

Both fixes are in `UE_Plugin/UELiveSync/Source/UELiveSync/Private/UELiveSyncSubsystem.cpp`, inside `UpdateTargetTransform` only. No protocol, Blender addon, interpolation, threshold, or spawn-path change.

### Patch A (B1) — unchanged early-return branch

When the "unchanged" early-return fires on a packet that explicitly carries a LOCAL transform with a valid parent GUID but the state has no local target yet, establish local-space state from the incoming value and derive the informational world target from `LocalXForm * ParentWorld`. `InterpolateTransforms` then takes the attached path instead of applying a Blender-local value as world.

### Patch B (B2) — idempotent initialization

The init path now seeds `Current*` from the actor's **actual** transform (read-only) instead of from the incoming value:
- Root: `GetActorLocation()/GetActorRotation()/GetActorScale3D()`.
- Attached child: `GetRootComponent()->GetRelativeLocation()/GetRelativeRotation()/GetActorRelativeScale3D()`.
- `Target*`/`LocalTarget*` keep the incoming authoritative value.
- Fallback to the incoming value when the actor is missing (previous behavior).

The unchanged early-return is skipped on the very first call (`bInitializedThisCall`), so the normal store + interpolation pipeline runs. When spawn transform == first update the interpolation is a no-op (idempotent — actor never overwritten, existing behavior unchanged); when they differ the change is applied through the normal pipeline.

Runtime invariants preserved:
- Initialization never writes to the actor (read-only seed).
- Initialization does not bypass the interpolation/apply pipeline.
- Threshold change detection and `InterpolateTransforms` untouched.

## Verification (PASS — 2026-08-05)

### Instrumented build (Patch A + Patch B, boundary `b2verify`, GUIDs chair `E1320F9C...` / cube `FFD6E3F0...`)

| # | Scenario | Evidence | Result |
|---|----------|----------|--------|
| 1 | Spawn | 07:19:21 OBJECT_CREATE chair+cube scl=1, correct create transform | PASS |
| 2 | Transform | 07:21:26 chair move → ROOT_APPLY (100,0,0) kept scl=(2,2,2) | PASS |
| 3 | Reparent scale=1 | 07:24:09 AFTER_ATTACH parent_scl=(1,1,1); cube kept world (250,0.5); ROOT_APPLY cube = 0 | PASS |
| 4 | Reparent scale≠1 | 07:21:04 AFTER_ATTACH parent_scl=(2,2,2); cube rel=(150,0.5), world (300,1); ROOT_APPLY cube = 0 | PASS |
| 5 | B2 static parent scale | 07:20:10 scale-2 update applied immediately (seq=1 ROOT_APPLY → ue_world_scl=2); move chair → parent keeps scl 2, cube follows, no pos/scale jump | PASS |

### Clean build (instrumentation removed, rebuilt; viewport-confirmed on all scenarios)

All 5 scenarios re-verified on the clean build with UE viewport acceptance (chair scale 2 applied immediately; reparent scale=1 and scale≠1 with no child jump; transform + follow correct). Removing the `[INV017B]` instrumentation did not change behavior.

## History (preserved for provenance)

- **2026-08-04 / 05**: Original investigation. First hypothesis (OBJECT_UPDATE handler detached children via empty parent GUID) implemented as a candidate fix, passed a log-only regression, but the bug reappeared after instrumentation removal. That fix was a FAILED HYPOTHESIS (kept only briefly for bisect, then squashed away); the detach mechanism was disproved.
- **2026-08-05 (reopen)**: Full-pipeline instrumentation (`[INV017B]` STAGE1 REPARENT_RECV / STAGE2 AFTER_ATTACH / STAGE3 UPDATE_ENTRY / STAGE4 ROOT_APPLY + AFTER_SET_XFORM) confirmed B1 and B2 as the real root causes (evidence above). Patch A then Patch B applied; both verified (table above).
- **2026-08-05 (close)**: All `[INV017B]` instrumentation removed; clean build re-verified 5/5; doc updated; closed.

## Next Steps

Investigation closed. No open items.
