# INV-2026-007: Instrumentation Strategy

## Metadata

- **Status**: Complete
- **Owner**: Khanh
- **Started**: 2026-07-20
- **Closed**: —
- **Classification**: Instrumentation Strategy — select optimal measurement configuration for next experiment
- **Depends-on**: INV-2026-006 (Boundary Evaluation, DEC-002)

## Inputs

| | Boundary | INV-006 Result | Position in gap |
|--|----------|---------------|-----------------|
| B-001 | Tick return (P8) | 5/5 Yes | Gap end (return from FEngineLoop::Tick) |
| B-002 | EngineTick return | 5/5 Yes | Early gap (right after P8) |
| B-004 | EngineTick call | 5/5 Yes | Late gap (right before P0) |
| B-005 | Tick entry (P0) | 5/5 Yes | Gap start (entry to FEngineLoop::Tick) |

**Eliminated** (INV-006): B-003, B-006 — FORCEINLINE eliminates runtime function boundary.

**Known interval**: P8 → P0 ≈ 331ms. Tick body (P0→P8) ≈ 0.2ms (INV-004).

**All 4 viable boundaries are already selected** by DEC-002. This investigation does not re-select — it decides which subset to instrument and in what configuration.

## Objective

Design the instrumentation strategy for the next experiment (INV-2026-008): choose which subset of the 4 viable boundaries to instrument, in which configuration, to maximize information gain about the ~331ms gap source while minimizing measurement cost.

This investigation does NOT re-evaluate Observable, Instrumentable, Partitionable, or Reachable — those are settled by INV-006.

## Strategy Criteria

Each instrumentation configuration is evaluated against:

| Criterion | Definition | Scale |
|-----------|------------|-------|
| Information gain | How much does this configuration reduce the unknown search space? | High / Medium / Low |
| Search efficiency | How many distinct intervals per execution does it produce? | High / Medium / Low |
| Measurement cost | Number of instrumentation points, builds, and runs required | Low / Medium / High |
| Ambiguity reduction | Does it distinguish between competing hypotheses about the gap origin? | Yes / No / Partial |
| Diagnostic power | Does the measurement reveal the mechanism or just the location? | Yes / No / Partial |

## Strategies

### S-0: Use existing markers only (B-001 / B-005)

P8 and P0 are already instrumented from INV-003. No new code needed.

- Information gain: Low — endpoints alone cannot resolve the interior.
- Search efficiency: Low — produces one interval (P8→P0 = ~331ms), which is already known.
- Measurement cost: Zero (existing markers).
- Ambiguity reduction: None — the gap is already measured.
- Diagnostic power: Already exhausted.

### S-1: Instrument B-002 (EngineTick return) alone

One timer at EngineTick() return (Launch.cpp:~61).

- Information gain: Low — B-002 is an edge boundary (immediately after P8). The interior interval B-002→P0 still spans ≈331ms. An interior boundary would produce more information per measurement.
- Search efficiency: Low — only one new interval beyond P8→P0. The measurement captures return overhead, which is expected to be negligible.
- Measurement cost: Low — one timer, one build, one run.
- Ambiguity reduction: Partial — confirms return path is fast, but does not narrow the gap.
- Diagnostic power: Partial — identifies the owning execution scope (post-EngineTick) but not the root cause within that scope.

### S-2: Instrument B-004 (EngineTick call) alone

One timer before EngineTick() call at the while-loop (Launch.cpp:~190).

- Information gain: Low — B-004 is an edge boundary (immediately before P0). Same limitation as S-1.
- Search efficiency: Low — only one new interval. Captures call overhead, expected negligible.
- Measurement cost: Low — one timer, one build, one run.
- Ambiguity reduction: Partial — confirms call path is fast, but does not narrow the gap.
- Diagnostic power: Partial — identifies the owning execution scope (pre-EngineTick) but not the root cause within that scope.

### S-3: Instrument B-002 + B-004 as a pair (RECOMMENDED)

Two timers in a single build: one at EngineTick() return (Launch.cpp:~61), one before EngineTick() call in the while-loop (Launch.cpp:~190). Produces three interval measurements with the existing P0/P8 markers:

```
P8 ──[return overhead]──→ B-002 ──[while-loop scope]──→ B-004 ──[call overhead]──→ P0
```

- Information gain: **High** — B-002 and B-004 form the first interior boundary pair within the gap. An interior boundary pair is expected to provide higher localization power than a single boundary because it bounds an interior interval rather than measuring only from a known endpoint. This configuration captures the entire interior of the gap in one measurement.
- Search efficiency: **High** — evaluates both internal edges in one execution without requiring a second rebuild. Produces three distinct intervals (P8→B-002, B-002→B-004, B-004→P0), of which two are known-small (return and call overhead) and one captures almost all of the ~331ms.
- Measurement cost: Low — two timers, one build, one run. Same build complexity as S-1 or S-2.
- Ambiguity reduction: **Yes** — distinguishes between:
  - Gap inside EngineTick scope (B-002→B-004 ≪ 331ms, time is inside EngineTick)
  - Gap outside EngineTick scope (B-002→B-004 ≈ 331ms, time is in while-loop)
- Diagnostic power: **Partial** — identifies the owning execution scope but not the root cause within that scope. If the gap is in while-loop scope, the source space collapses to: IsEngineExitRequested() check (negligible) + loop overhead (negligible) + scheduler preemption.

**Key property**: This is the first candidate pair that bounds an **interior** interval rather than an outer interval. P8 and P0 bound the gap from the outside. B-002 and B-004 bound a region inside the gap — a closed interval with unknowns on both sides. That is why information gain is High while measurement cost stays Low.

## Recommendation

**Adopt Strategy S-3**: instrument B-002 + B-004 as a pair in a single build.

Expected outcome: the measurement substantially reduces the remaining search space by isolating the unresolved interval to one of two execution scopes:

| Outcome | Conclusion | Next step |
|---------|-----------|-----------|
| B-002 → B-004 ≈ B-002 → P0 (B-004→P0 negligible) | Gap is between EngineTick return and call (while-loop scope) | Investigate while-loop scheduling |
| B-002 → B-004 ≪ 331ms (time is inside EngineTick) | Gap is inside EngineTick → Tick path | Divide Tick body with finer binary search markers |

In both outcomes, the search space is reduced to a single execution scope.

## Decision

- [ ] Accept recommendation: Adopt S-3 (B-002 + B-004 pair)
- [ ] Reject and select alternative: \_\_\_
- [ ] Need discussion

## Decision Log

| ID | Decision | Based on | Accepted | Rejected | Justification |
|----|----------|----------|----------|----------|---------------|
| DEC-003 | Adopt instrumentation strategy S-3 (B-002 + B-004 pair) | GR-003 | S-3 | S-0, S-1, S-2 | Strategy completeness gate passed. |

## Gate: Strategy Completeness (GR-003)

**Schema**:

```
Gate Name:  Strategy Completeness
Input:      INV-2026-007 (Instrumentation Strategy document)
Checks:     At least one instrumentation strategy evaluated and selected
Pass Criteria: At least one strategy recommended
Output:     Gate Report (artifact, ID: GR-003)
Result:     PASS / FAIL / NOT RUN
```

Result: PASS.
