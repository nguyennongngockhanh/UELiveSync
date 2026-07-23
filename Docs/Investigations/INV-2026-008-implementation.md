# INV-2026-008: Instrumentation Implementation

## Metadata

- **Status**: Draft
- **Owner**: Khanh
- **Started**: 2026-07-20
- **Closed**: —
- **Classification**: Implementation — instrument B-002 + B-004 per S-3
- **Depends-on**: INV-2026-007 (Instrumentation Strategy, DEC-003)

## Objective

Place timing probes for boundaries B-002 and B-004 in a single build, per strategy S-3:

| ID | Boundary | Probe Location | Type |
|----|----------|---------------|------|
| B-002 | EngineTick() return | Launch.cpp:~61 | Timer at function exit |
| B-004 | EngineTick() call site | Launch.cpp:~190 | Timer before while-loop call |

Combined with existing P0/P8 markers (INV-003), this produces three interval measurements per iteration:

```
P8 ──→ B-002 ──→ B-004 ──→ P0
```

## Implementation Plan

### Instrumentation points

EXP-A requires four active probe points to compute the three intervals. P0 and P8 are pre-existing probe definitions from INV-003 that were removed after that experiment per Instrumentation Lifetime policy. Implementing EXP-A requires reinstating P0/P8 temporarily alongside the new B-002/B-004 probes.

All four probes are in engine source. Two files are affected:

**File 1**: `Engine/Source/Runtime/Launch/Private/LaunchEngineLoop.cpp`

**P0** — timestamp at FEngineLoop::Tick() entry (~line 5575)
**P8** — timestamp at FEngineLoop::Tick() return (~line 6197)

**File 2**: `Engine/Source/Runtime/Launch/Private/Launch.cpp`

**B-002** — timestamp at EngineTick() return (~line 61):
```cpp
LAUNCH_API void EngineTick( void )
{
    GEngineLoop.Tick();
    // INV-2026-008: timer_B002 = FPlatformTime::Cycles64()
}
```

**B-004** — timestamp before EngineTick() call in the while-loop (~line 190):
```cpp
while (!IsEngineExitRequested())
{
    // INV-2026-008: timer_B004 = FPlatformTime::Cycles64()
    EngineTick();
}
```

### Output

Log the three intervals for each iteration:
- P8 → B-002: Tick-return → EngineTick-return interval
- B-002 → B-004: EngineTick-return → EngineTick-call interval (while-loop scope)
- B-004 → P0: EngineTick-call → Tick-entry interval

Duration: capture ~5 seconds of slow-state ticks (~15 iterations).

### Perturbation level

Level 2 — behavior-preserving instrumentation. `FPlatformTime::Cycles64()` is a read of the CPU cycle counter with no side effects. The instruction overhead (~10 cycles per call) is negligible relative to the ~331ms gap.

## Escalation Required

Both instrumentation points are in **engine source** (`Engine/Source/Runtime/Launch/Private/Launch.cpp`). Per policy:

| Level | Approach | Status |
|-------|----------|--------|
| 1 | Plugin instrumentation | Insufficient — Launch.cpp is outside plugin scope |
| 2 | Test project diagnostics | Insufficient |
| 3 | CVars / Unreal Insights / Trace | Insufficient — need precise timestamps at specific source lines |
| 4 | Engine source analysis (read-only) | Done (INV-004) |
| **5** | **Engine instrumentation** | **Required — needs explicit approval** |

No production behavior is intentionally modified. Add temporary timestamp probes only; no control-flow or behavioral changes.

Approval requested for:
1. Add temporary timestamp probes (four `FPlatformTime::Cycles64()` calls across two files):
   - `Engine/Source/Runtime/Launch/Private/LaunchEngineLoop.cpp` (P0, P8 — reinstated from INV-003)
   - `Engine/Source/Runtime/Launch/Private/Launch.cpp` (B-002, B-004 — new)
2. Rebuild `UnrealEditor` (engine target) after modification
3. Run experiment and collect interval timings
4. Remove all four probes after experiment (per Instrumentation Lifetime rule)

## Risk Assessment

- Scope: Four temporary probe points across two source files (Launch.cpp and LaunchEngineLoop.cpp) within the Launch module.
- Reversibility: Full. No persistent changes. All instrumentation annotated with `INV-2026-008`.
- Rebuild cost: Rebuild the Launch module and relink UnrealEditor (engine build).
- Rollback: `git restore` on both files + rebuild.

## Instrumentation Lifetime

The probes introduced by INV-2026-008 are temporary diagnostic instrumentation. They:

- exist only for EXP-A
- shall not be committed as production engine code
- shall be removed immediately after data collection
- shall never become dependencies for subsequent investigations

This ensures subsequent investigations do not inadvertently depend on stale probe points.

## Experimental Configuration

| Property | Value |
|----------|-------|
| Experiment ID | EXP-A (INV-2026-008) |
| Timestamp source | `FPlatformTime::Cycles64()` — CPU cycle counter, same clock for all probes |
| Conversion | cycles → µs using `FPlatformTime::GetSecondsPerCycle64()` |
| Acceptance threshold | median(interval) ≥95% of median(P8→P0) (chosen for EXP-A only; not a system property) |

### Hypotheses

| Hypothesis | Prediction | Acceptance Criterion | Interpretation |
|------------|-----------|---------------------|---------------|
| **H1** | B-002→B-004 is the dominant interval | median(B-002→B-004) ≥95% of median(P8→P0) | The dominant contributor to the unresolved interval lies between EngineTick return and the next EngineTick call |
| **H2** | B-004→P0 is the dominant interval | median(B-004→P0) ≥95% of median(P8→P0) | The dominant contributor to the unresolved interval lies between the EngineTick call and Tick entry |

Both hypotheses predict P8→B-002 is negligible (<5% of P8→P0).

### Observation per run

For each interval (P8→B-002, B-002→B-004, B-004→P0) across N iterations, record:
- N (iteration count)
- minimum, maximum (µs)
- median (µs) and MAD (median absolute deviation)
- potential outliers flagged by modified Z-score (|Z| > 3.5)

All intervals use the same timestamp source (`FPlatformTime::Cycles64()`) as the existing P0/P8 probes.

## Escalation Decision

- [x] Approve Level 5 escalation (engine instrumentation for EXP-A)
- [ ] Reject — explore alternative approach
- [ ] Need discussion

## Gate: Implementation Readiness (GR-004)

**Schema**:

```
Gate Name:  Implementation Readiness
Input:      INV-2026-008 (Implementation document)
Checks:     Implementation plan complete, experimental configuration defined,
            risk assessment documented, Instrumentation Lifetime policy defined,
            rollback procedure documented, escalation decision documented
Pass Criteria: All checklist items satisfied
Output:     Gate Report (artifact, ID: GR-004)
Result:     PASS / FAIL / NOT RUN
```

Checklist:

- [x] Implementation plan complete (probe locations, code, output format)
- [x] Experimental Configuration defined (timestamp source, hypotheses, observation plan)
- [x] Risk assessment documented
- [x] Instrumentation Lifetime policy defined
- [x] Rollback procedure documented
- [x] Escalation decision documented (approved)

Result: PASS.

## Decision Log

| ID | Decision | Based on | Accepted | Rejected | Justification |
|----|----------|----------|----------|----------|---------------|
| DEC-004 | Authorize EXP-A: instrument B-002 + B-004 under Level 5 escalation | GR-004 | Level 5 engine instrumentation | N/A | Implementation readiness gate passed. Experiment may proceed with timestamp probes only. |
