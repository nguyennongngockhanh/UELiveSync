# INV-2026-003: Identify Source of ~331ms Viewport Tick Interval

## Metadata

- **Status**: Causal Boundary Established
- **Owner**: Khanh
- **Started**: 2026-07-20
- **Closed**: —
- **Classification**: Editor Viewport Tick / Scheduler
- **Depends-on**: —

## Problem

After "Start UE Sync", actors appear in Outliner but viewport is empty. The viewport tick interval is ~331ms, which exceeds the 250ms `VisibilityTimeThreshold` in `SEditorViewport::IsVisible()`. This causes `IsVisible()` to return false, Gate1 to reject viewport ticking, and the viewport to stop rendering.

The source of the ~331ms tick interval is unknown. This investigation aims to identify what drives this interval.

## Context

**From INV-2026-002**:
- Rendering failure mechanism confirmed: 331ms > 250ms → IsVisible=false → Gate1 reject → no render
- Plugin workaround (`AddRealtimeOverride` + `Invalidate()`) failed due to bootstrap deadlock
- Engine instrumentation (INV-TICK, INV-VISIBLE, INV-Gate1) has been reverted

**Key code locations** (UE5.8-debug snapshot):
- `SEditorViewport::Tick()` — updates `LastTickTime = FPlatformTime::Seconds()`
- `SEditorViewport::IsVisible()` — checks `Delta <= VisibilityTimeThreshold` (0.25f)
- `EditorEngine::Tick()` — Gate1 checks `ViewportClient->IsVisible()` before allowing viewport tick
- Background Process override in `EditorEngine::Tick()`

## Hypotheses

| ID | Hypothesis | Status | Priority |
|----|-----------|--------|----------|
| H1 | Background Process override drives the tick interval | Alone is insufficient to explain 331ms. May contribute indirectly (via IsRealtime=false → EnsureTick Stop → NeedsTick lost). | - |
| H2 | Slate throttle manager (`FSlateThrottleManager`) drives the tick interval | Alone is insufficient to explain 331ms. Gates Gate1 via `IsAllowingExpensiveTasks()`, but does not control tick interval. | - |
| H3 | Editor idle scheduler drives the tick interval | Alone is insufficient to explain 331ms. `AllowSlateToSleep=false` observed, so Slate doesn't sleep. | - |
| H4 | Realtime override stack drives the tick interval | Alone is insufficient to explain 331ms. Controls IsRealtime(), which affects EnsureTick(), but does not control tick interval directly. | - |
| H5 | Multiple viewport clients interact to produce the interval | Alone is insufficient to explain 331ms. Mechanism is per-viewport. | - |
| H6 | Engine tick rate throttling in background | Possible — but would affect many things, not just viewport. | 3 |
| H7 | Platform-level frame scheduling (Linux compositor) | Possible — but would affect many things, not just viewport. | 4 |
| H8 | Slate Fast Update / NeedsTick flag controls tick frequency | **Leading hypothesis** — explains viewport-specific symptom. No runtime evidence yet. | **1** |
| H9 | Periodic invalidation source triggers Invalidate() periodically | Possible — need to identify what calls Invalidate() periodically. | 2 |
| H10 | Multiple viewport scheduling mismatch | Low probability — actor appears in correct viewport, clicking other viewports doesn't fix bug. | 8-9 |

## Phase 1 — Source Audit Results

### Tick Scheduling Chain (Mapped)

```
FEngineLoop::Tick()
    │
    ├─ GEngine->Tick()
    │       │
    │       └─ UEditorEngine::Tick()
    │               │
    │               ├─ Background Process override
    │               │   - Runs EVERY EditorEngine::Tick()
    │               │   - Removes "Background Process" override
    │               │   - Adds AddRealtimeOverride(false, "Background Process") if !FApp::HasFocus()
    │               │   - Sets bShouldInvalidateViewportWidget = true
    │               │
    │               ├─ Gate1
    │               │   - Requires: (IsAllowingExpensiveTasks || bNeedsRedraw) && IsVisible()
    │               │   - IsVisible() checks: FPlatformTime::Seconds() - LastTickTime <= 0.25
    │               │   - If Gate1 passes: ViewportClient->Tick() runs
    │               │   - If Gate1 blocks: ViewportClient->Tick() NOT run
    │               │
    │               └─ FEditorViewportClient::Tick()
    │                       - Consumes bShouldInvalidateViewportWidget
    │                       - Calls InvalidateViewportWidget() → SEditorViewport::Invalidate()
    │                       - ONLY runs if Gate1 passes
    │
    └─ FSlateApplication::Tick()
            │
            ├─ UpdateAnyActiveTimersArePending()
            │   - Checks if any active timer is ready to fire
            │   - Sets bAnyActiveTimersPending
            │
            ├─ Sleep check
            │   - bIsSlateAsleep = true if:
            │     AllowSlateToSleep=true AND bAnyActiveTimersPending=false AND bIsUserIdle=true
            │   - If bIsSlateAsleep=false: DrawWindows() is called
            │   - If bIsSlateAsleep=true: DrawWindows() is NOT called
            │
            └─ DrawWindows()
                    │
                    └─ ProcessInvalidation → Widget tick
                            │
                            ├─ ExecuteActiveTimers()
                            │   - Calls SEditorViewport::EnsureTick()
                            │   - EnsureTick() returns Continue if IsRealtime() || bInvalidated
                            │   - Returns Stop if both false → active timer unregistered
                            │
                            └─ SEditorViewport::Tick()
                                - Updates LastTickTime = FPlatformTime::Seconds()
                                - ONLY called if widget has NeedsTick flag
```

### Key Finding: Tick Order

**Critical**: `GEngine->Tick()` runs BEFORE `FSlateApplication::Tick()`.

This means:
1. Gate1 evaluates `IsVisible()` using the OLD `LastTickTime` (from previous Slate tick)
2. If Gate1 blocks → `ViewportClient->Tick()` NOT called → `bShouldInvalidateViewportWidget` NOT consumed
3. Then `FSlateApplication::Tick()` runs → `DrawWindows()` → `SEditorViewport::Tick()` → `LastTickTime` updated

### Key Finding: Realtime Override Stack

**Background Process override** in `UEditorEngine::Tick()`:
- Runs EVERY `EditorEngine::Tick()` (every frame)
- Removes "Background Process" override by name
- Adds `AddRealtimeOverride(false, "Background Process")` if `!FApp::HasFocus() && bThrottleCPUWhenNotForeground`
- `IsRealtime()` checks `RealtimeOverrides.Last().bIsRealtime`
- If "Background Process" is on top of stack → `IsRealtime()=false`

**EnsureTick()** in `SEditorViewport`:
- Returns `Continue` if `Client->IsRealtime() || bInvalidated`
- Returns `Stop` if both false → active timer unregistered
- `bInvalidated` is set to false after first check

**Deadlock scenario**:
1. Background Process override → `IsRealtime()=false`
2. `EnsureTick()` → `IsRealtime()=false && bInvalidated=false` → returns `Stop`
3. Active timer unregistered → `bAnyActiveTimersPending=false`
4. If `AllowSlateToSleep=true` → Slate sleeps → `DrawWindows()` NOT called
5. `SEditorViewport::Tick()` NOT called → `LastTickTime` NOT updated
6. After 250ms → `IsVisible()=false` → Gate1 blocks
7. `ViewportClient->Tick()` NOT called → `bShouldInvalidateViewportWidget` NOT consumed
8. `InvalidateViewportWidget()` NOT called → active timer NOT re-registered
9. **Deadlock**: Slate sleeping, no active timers, Gate1 blocked

### Key Finding: AllowSlateToSleep

**CVar default** in `FSlateApplication`:
- `AllowSlateToSleep` defaults to `GIsEditor` (1/true) in editor
- `SleepBufferPostInput` defaults to `0.0f`

**Runtime observation from INV-2026-002**:
- `AllowSlateToSleep=false` was observed in the test environment
- This means `DrawWindows()` IS called every frame

**Critical distinction** (corrected from Phase 1):
- `DrawWindows()` running every frame does NOT mean `SEditorViewport::Tick()` runs every frame
- `DrawWindows()` → `ProcessInvalidation` → WidgetProxy only processes widgets with `NeedsTick` or `NeedsActiveTimerUpdate` flags
- If `SEditorViewport` doesn't have `NeedsTick` flag, it won't be ticked even though `DrawWindows()` runs
- This perfectly explains the 331ms interval without any contradiction

### Remaining Unknown

H1-H5 alone are insufficient to explain the 331ms interval:
- Background Process override (sets IsRealtime()=false, contributes indirectly via EnsureTick Stop → NeedsTick lost)
- Slate throttle manager (gates Gate1, but does not control tick interval)
- Slate sleep mechanism (AllowSlateToSleep=false, so DrawWindows() runs every frame)
- Realtime override stack (controls IsRealtime(), but does not control tick interval directly)

**The 331ms interval may originate from Slate widget scheduling.** Specifically:
- What determines when `SEditorViewport` gets the `NeedsTick` flag?
- What determines the frequency of `ExecuteActiveTimers()` for this widget?
- Is the widget periodically added/removed from the update list?
- Or is there a periodic invalidation source that triggers `Invalidate()` periodically?

Phase 2 will determine whether the pipeline breaks at:
- `DrawWindows()` — not called at all
- `ProcessInvalidation()` — not reached
- `ExecuteActiveTimers()` — not firing for this widget
- `EnsureTick()` — returning Stop
- `Tick()` — not called despite timer firing

## Investigation Plan

### Phase 1 — Source Audit (COMPLETE)

Audit completed. Key findings:
- Mapped complete tick scheduling chain
- Identified tick order: `GEngine->Tick()` BEFORE `FSlateApplication::Tick()`
- Identified Background Process override mechanism
- Identified Slate sleep mechanism
- Identified Realtime Override Stack interaction
- **Corrected understanding**: `DrawWindows()` running every frame does NOT mean `SEditorViewport::Tick()` runs every frame. The Slate Fast Update system only processes widgets with `NeedsTick` flag.

### Phase 2 — Binary Search within FEngineLoop::Tick()

Phase 2 used a layered binary search to locate the 330ms delay. Each phase added markers, collected runtime data, identified which region contained the delay, then reverted markers before the next phase.

**Phase 2 Step 1 — DrawWindows timing** (INV-STEP1):
- Instrumented `FSlateApplication::DrawWindows()`.
- Results: 525 calls, bimodal delta (8ms→333ms), `sleep=0` throughout.
- **H3 (Slate sleep) eliminated.** Instrumentation reverted.

**Phase 2A — Upstream cadence verification** (INV-2A-ENGINE):
- Instrumented `FEngineLoop::Tick()` and `UEditorEngine::Tick()` with frame/tid/time/hasFocus/foreground.
- Results: All three tiers show same bimodal pattern. Transition at EditorEngine frame 244–245.
- **Cadence first changes before or within UEditorEngine::Tick().** Instrumentation reverted.

**Phase 2B — Binary search within FEngineLoop::Tick** (INV-2B):
- Added markers P0/A/B1/B2/C to partition FEngineLoop::Tick.
- Results: All intra-frame regions before GEngine->Tick are fast. Entire 330ms is in C→nextP0 region.

**Phase 2C — Binary search within C→nextP0** (INV-2C):
- Added markers D/E/F: D after GEngine->Tick, E before Slate Tick, F after Slate Tick.
- Results:
  - C→D (GEngine->Tick): ~1ms
  - D→E (gap): ~0.05ms
  - E→F (Slate Tick): ~2.5ms
  - **F→nP0: ~330ms**
- **GEngine->Tick, UEditorEngine::Tick, Slate Tick, DrawWindows all eliminated.**

**Phase 2D — Binary search within F→nP0** (INV-2D):
- Added markers P1–P8 to partition the region after Slate Tick.
- Results (slow cadence, fg=0):

| Region | Avg (ms) | % of 330ms frame |
|--------|----------|-----------------|
| P0→C (PumpMessages→pre-GEngine) | 0.06 | 0.0% |
| C→D (GEngine→Tick) | 0.98 | 0.3% |
| D→E (gap) | 0.06 | 0.0% |
| E→F (Slate Tick) | 2.91 | 0.9% |
| F→P1 (gap) | 0.02 | 0.0% |
| P1→P8 (RHITick+Sync+DeferredTick+Render_EndFrame) | 0.41 | 0.1% |
| **P8→nP0 (between Tick calls)** | **328.9** | **98.7%** |

### Phase 2 Conclusion — Causal Boundary Established

**All regions inside FEngineLoop::Tick() are fast (~4.5ms total).** The 330ms delay is entirely in the wall-clock interval between two successive entries into FEngineLoop::Tick().

```
FEngineLoop::Tick()  [~4.5ms execution]
    │
    └─ return

    ──── 329ms wall-clock gap ────

FEngineLoop::Tick()  [~4.5ms execution]
```

**Causal boundary**: The observation is that the observed wall-clock interval between two successive entries into `FEngineLoop::Tick()` is approximately 329ms, while the execution time of `FEngineLoop::Tick()` itself remains only a few milliseconds.

**What this eliminates** — the following instrumented execution regions as the location of the ~330ms delay:

- P0→C (PumpMessages → pre-GEngine)
- C→D (GEngine dispatch)
- D→E (between GEngine and Slate)
- E→F (Slate Tick)
- F→P1 (post-Slate pre-render)
- P1→P8 (Render_UpdateFrame + Sync + DeferredTick + Render_EndFrame)

Therefore, the current instrumentation did not observe the ~330ms delay inside any instrumented region of `FEngineLoop::Tick()`.

**What this does NOT identify**: The cause of the 329ms gap between tick calls. This requires a new investigation focused on the main loop in `Launch.cpp`.

**All Phase 2 instrumentation has been reverted.** Engine source is clean.

### Phase 3 — Causal Intervention (If Needed)

Based on Phase 2 results:
- If active timer stops: Force `IsRealtime()=true` or `bInvalidated=true`
- If widget skipped: Force `NeedsTick` flag
- If engine throttled: Increase tick rate

## Exit Criteria

**Current outcome:**
- ✅ Causal boundary established: the ~330ms delay is between successive entries into `FEngineLoop::Tick()`, not inside them.

**Remaining outcomes (deferred to INV-2026-004):**
- □ Identify scheduler outside FEngineLoop
- □ Causal intervention demonstrates the scheduler controls the interval

**Boundary handed off to:**
INV-2026-004 (Main Loop Scheduling Investigation).

**Reason:**
The causal boundary has moved outside the execution body of `FEngineLoop::Tick()`, requiring a new investigation scope.

## Decision Log

| ID | Decision | Based on | Accepted | Rejected | Reason |
|----|----------|----------|----------|----------|--------|
| D1 | Open investigation | INV-2026-002 identified "Unknown viewport tick source" as unproven node | Opened | — | Separate node in causal chain |
| D2 | Phase 1 source audit before instrumentation | Source audit can map the tick scheduling chain without code changes | Accepted | Jump to instrumentation | Cheaper, identifies candidates |
| D3 | Investigate Slate widget scheduling before engine-level scheduling | Observed symptom is viewport-specific, not system-wide | Accepted | Engine throttling first | Viewport-specific symptom better matches widget scheduling |
| D4 | Phase 2 focus on FEngineLoop::Tick binary search | Phase 2A showed cadence changes before UEditorEngine::Tick, so search within FEngineLoop::Tick | Accepted | Slate pipeline instrumentation | Binary search is more efficient than targeted instrumentation |
| D5 | Complete binary search before updating document | Investigation in middle of binary search; updating now risks needing rewrite at Phase 2D | Accepted | Update after each phase | Avoids redundant rewrites |

## Lessons Learned

- **INV-2026-002 confirmed the mechanism but not the source**: The rendering failure is proven, but the ~331ms tick interval remains unexplained.
- **Plugin workaround cannot fix this**: The plugin's Tick runs inside the world tick, AFTER Gate1 has already evaluated. The timing relationship prevents the workaround from working.
- **Tick order matters**: `GEngine->Tick()` runs BEFORE `FSlateApplication::Tick()`. This means Gate1 evaluates `IsVisible()` using the OLD `LastTickTime`.
- **Background Process override is NOT the direct source**: It sets `IsRealtime()=false`, which causes `EnsureTick()` to return `Stop`, but it does not control the tick interval itself.
- **Never infer execution frequency from a parent stage**: `DrawWindows()` running every frame does NOT mean `SEditorViewport::Tick()` runs every frame. The Slate Fast Update system only processes widgets with `NeedsTick` flag. A parent stage (DrawWindows) can run at one frequency while a child operation (widget Tick) runs at a different frequency.
- **Don't assume contradiction without checking all layers**: The initial "contradiction" between `AllowSlateToSleep=false` and 331ms interval was resolved by understanding the Slate Fast Update widget scheduling.
- **Binary search is more efficient than targeted instrumentation**: Phase 2 proved that a systematic binary search within FEngineLoop::Tick() can eliminate entire subsystems faster than hypothesis-driven instrumentation. Each phase added markers, identified the region containing the delay, and reverted markers before the next phase.
- **Don't overclaim hypothesis status**: "Leading hypothesis" is appropriate when symptom matches and source audit looks reasonable but no runtime evidence exists. "Strongest candidate" requires runtime evidence.
- **Complete the search before updating documentation**: Updating the investigation document in the middle of a binary search risks rewriting it at each phase. It is better to complete the search and update once with the final result.
- **Boundary shifts require investigation scope changes**: When the causal boundary moves from inside a subsystem to between subsystems, the investigation scope must change accordingly. Continuing to instrument within the original subsystem will not yield new information.
