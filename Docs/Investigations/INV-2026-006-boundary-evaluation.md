# INV-2026-006: Boundary Evaluation

## Metadata

- **Status**: Complete
- **Owner**: Khanh
- **Started**: 2026-07-20
- **Closed**: 2026-07-20
- **Classification**: Execution Boundary Evaluation
- **Depends-on**: INV-2026-005 (Boundary Inventory v2 + Candidate Records)

## Summary

All 6 candidate boundaries evaluated. 4 viable (5/5 Yes): B-001 (Tick return), B-002 (EngineTick return), B-004 (EngineTick call), B-005 (Tick entry). 2 eliminated: B-003, B-006 (FORCEINLINE eliminates IsEngineExitRequested() function boundary at runtime). GR-002 (Evaluation Completeness Gate) — PASS. DEC-002 issued: admit 4 viable candidates into Boundary Selection.

## Inputs from INV-2026-005

**Established facts:**

- The unresolved interval is bounded between P8 (return from `FEngineLoop::Tick()`) and the next observed P0 (entry into `FEngineLoop::Tick()`).
- 6 boundary instances identified across C1 (Function return) and C2 (Function call).
- C3/C4/C5: no instances within scope.
- 6 Candidate Boundary Records exist (BR-001 through BR-006, Lifecycle State = Candidate).
- Boundary Inventory v2 is frozen.
- Gate: Record Completeness — PASS (GR-001).

**Inherited evidence:**

| ID | Kind | Source | Content |
|----|------|--------|---------|
| EVID-001 | SourceAudit | LaunchEngineLoop.cpp | FEngineLoop::Tick() function body and return (lines 5575–6197) |
| EVID-002 | SourceAudit | Launch.cpp | EngineTick() wrapper and while-loop (lines 58–61, 190–193) |
| EVID-003 | SourceAudit | CoreGlobals.h | IsEngineExitRequested() = GIsRequestingExit flag read (line 398) |

## Objective

Evaluate each of the 6 candidate boundary instances against the five evaluation criteria. Produce Evaluated Boundary Records (Lifecycle State = Evaluated) with all criteria decided. Recommend a first boundary for INV-2026-007 (Boundary Selection).

## Evaluation Criteria

Each candidate is evaluated against:

| Criterion | Definition | Answer | Required evidence |
|-----------|------------|--------|-------------------|
| Observable | Can the boundary be detected at runtime without perturbing behavior? | Yes / No / Unknown | Existing observability, instrumentation hooks, or trace points |
| Instrumentable | Can instrumentation be inserted at this boundary? | Yes / No / Unknown | Source line accessible, function boundary, or intercept point |
| Partitionable | Can the interval before and after the boundary be measured independently? | Yes / No / Unknown | Timer placement feasible before and after, no aliasing |
| Reachable (Structural) | Is the boundary statically reachable from the unresolved interval? | Yes / No / Unknown | Control flow graph evidence |
| Reachable (Runtime) | Is the boundary dynamically executed each iteration during the slow state? | Yes / No / Unknown | Log evidence, counter evidence, or runtime trace |

All criteria may be recorded as Yes / No / Unknown during evaluation. Before a candidate may proceed to Selection, every criterion shall be resolved to either Yes or No.

## Procedure

For each candidate boundary (B-001 through B-006):

1. **Review evidence**: Examine existing Evidence (EVID-001 through EVID-003) and source code to answer each criterion.
2. **Create Evaluated Boundary Record** (BR-007 through BR-012): New Boundary Record with Lifecycle State = Evaluated. Populate all five evaluation fields with Yes/No + supporting Evidence IDs. Supersedes the corresponding Candidate record (BR-001 through BR-006).
3. If evidence is insufficient for a criterion, collect new evidence (EVID-004+) or document the gap explicitly.

## Evaluated Boundary Records

### BR-007: FEngineLoop::Tick() return (supersedes BR-001)

```
Record ID:           BR-007
Boundary ID:         B-001
Owning-Investigation: INV-2026-006
Class:               C1 — Execution leaves current component
Type:                Function return
Instance:            FEngineLoop::Tick() return

Evidence IDs:        EVID-001
Inference IDs:       INF-002, INF-003

Observable:
  Result:            Yes
  Evidence IDs:      EVID-001
  Justification:     Return location is identifiable in source as the closing region of FEngineLoop::Tick() after the Render_EndFrame block (LaunchEngineLoop.cpp ~line 6197). Observable via UE_LOG at the return point (behavior-preserving, perturbation level 2).
Instrumentable:
  Result:            Yes
  Evidence IDs:      EVID-001
  Justification:     INV-003 Phase 2 placed P8 instrumentation at this location. The post-Render_EndFrame point is a single, stable location for instrumentation.
Partitionable:
  Result:            Yes
  Evidence IDs:      EVID-001, EVID-004
  Justification:     Before: timer at the return point inside Tick(). After: timer at the caller (EngineTick) after the call returns. The 'after' interval spans the entire unresolved gap (return → next Tick entry). This partition is asymmetric (INF-002) — the gap is entirely in the after interval (INV-003).
Reachable (Structural):
  Result:            Yes
  Evidence IDs:      EVID-001
  Justification:     The function contains a normal completion return at its end (~line 6197, after Render_EndFrame). Control flow reaches this return during the normal execution path identified in EVID-001.
Reachable (Runtime):
  Result:            Yes
  Evidence IDs:      EVID-004
  Justification:     Supported by INF-003. P0 markers observed at ~331ms intervals (EVID-004); successive entries require completion of the prior Tick invocation.

Lifecycle State:     Evaluated
Supersedes:          BR-001
```

### BR-008: EngineTick() return (supersedes BR-002)

```
Record ID:           BR-008
Boundary ID:         B-002
Owning-Investigation: INV-2026-006
Class:               C1 — Execution leaves current component
Type:                Function return
Instance:            EngineTick() return

Evidence IDs:        EVID-002
Inference IDs:       INF-006, INF-007

Observable:
  Result:            Yes
  Evidence IDs:      EVID-002
  Justification:     EngineTick() return location is identifiable in source as the closing region (~Launch.cpp:61). Observable via UE_LOG at the return point (behavior-preserving).
Instrumentable:
  Result:            Yes
  Evidence IDs:      EVID-002
  Justification:     The return point in EngineTick() (Launch.cpp:~61) is directly instrumentable.
Partitionable:
  Result:            Yes
  Evidence IDs:      EVID-002, EVID-004
  Justification:     Before: timer at the return point inside EngineTick(). After: timer in the while loop after the call returns. The partition is asymmetric (INF-006): the 'after' interval spans most of the unresolved gap.
Reachable (Structural):
  Result:            Yes
  Evidence IDs:      EVID-002
  Justification:     EngineTick() has a single exit point; it must return for the while loop to continue.
Reachable (Runtime):
  Result:            Yes
  Evidence IDs:      EVID-004
  Justification:     Supported by INF-007. EngineTick() executes each iteration (P0 observed, EVID-004); any call that enters must return.

Lifecycle State:     Evaluated
Supersedes:          BR-002
```

### BR-009: IsEngineExitRequested() return (supersedes BR-003)

```
Record ID:           BR-009
Boundary ID:         B-003
Owning-Investigation: INV-2026-006
Class:               C1 — Execution leaves current component
Type:                Function return
Instance:            IsEngineExitRequested() return

Evidence IDs:        EVID-003
Inference IDs:       INF-008

Observable:
  Result:            No
  Evidence IDs:      EVID-003
  Justification:     IsEngineExitRequested() is FORCEINLINE (CoreGlobals.h:395); no distinct runtime return event exists that can be observed independently of the caller.
Instrumentable:
  Result:            No
  Evidence IDs:      EVID-003
  Justification:     FORCEINLINE eliminates the function boundary — no function entry/exit point to instrument.
Partitionable:
  Result:            No
  Evidence IDs:      EVID-003
  Justification:     No observable boundary to partition around.
Reachable (Structural):
  Result:            Yes
  Evidence IDs:      EVID-002
  Justification:     The function definition exists and the call site in the while condition (Launch.cpp:190) is structurally reachable.
Reachable (Runtime):
  Result:            No
  Evidence IDs:      EVID-003
  Justification:     Supported by INF-008. No distinct runtime function-return event exists because the function is inlined into the caller.

Lifecycle State:     Evaluated
Supersedes:          BR-003
```

### BR-010: EngineTick() call (supersedes BR-004)

```
Record ID:           BR-010
Boundary ID:         B-004
Owning-Investigation: INV-2026-006
Class:               C2 — Execution enters another component
Type:                Function call
Instance:            EngineTick() call (while loop → EngineTick)

Evidence IDs:        EVID-002 (Launch.cpp:58–61, 190–193)
Inference IDs:       INF-004, INF-005

Observable:
  Result:            Yes
  Evidence IDs:      EVID-002
  Justification:     EngineTick() entry is identifiable in source (Launch.cpp:58). Observable via UE_LOG at function entry (behavior-preserving, perturbation level 2).
Instrumentable:
  Result:            Yes
  Evidence IDs:      EVID-002
  Justification:     The call site and function entry in Launch.cpp are directly instrumentable.
Partitionable:
  Result:            Yes
  Evidence IDs:      EVID-002, EVID-004
  Justification:     Before: timer in the while loop before EngineTick() call. After: timer at EngineTick() entry. The partition is symmetric (INF-004): both pre-call and post-entry regions are independently measurable and non-empty.
Reachable (Structural):
  Result:            Yes
  Evidence IDs:      EVID-002
  Justification:     The while loop unconditionally calls EngineTick() when IsEngineExitRequested() returns false (normal operation during slow state).
Reachable (Runtime):
  Result:            Yes
  Evidence IDs:      EVID-004
  Justification:     Supported by INF-005. EngineTick() is the only caller of GEngineLoop::Tick(); since Tick entry (P0) was observed each iteration, EngineTick() must have executed.

Lifecycle State:     Evaluated
Supersedes:          BR-004
```

### BR-011: GEngineLoop::Tick() call (supersedes BR-005)

```
Record ID:           BR-011
Boundary ID:         B-005
Owning-Investigation: INV-2026-006
Class:               C2 — Execution enters another component
Type:                Function call
Instance:            GEngineLoop::Tick() call (EngineTick → FEngineLoop::Tick)

Evidence IDs:        EVID-001 (LaunchEngineLoop.cpp:5575), EVID-002 (Launch.cpp:60)
Inference IDs:       INF-001

Observable:
  Result:            Yes
  Evidence IDs:      EVID-001, EVID-002
  Justification:     Entry location is uniquely identifiable in source (LaunchEngineLoop.cpp:5575) and can be instrumented by an execution trace or UE_LOG without changing control flow.
Instrumentable:
  Result:            Yes
  Evidence IDs:      EVID-001
  Justification:     FEngineLoop::Tick() entry at line 5575 is directly instrumentable. Confirmed in INV-003 Phase 2 (P0 markers).
Partitionable:
  Result:            Yes
  Evidence IDs:      EVID-001, EVID-002
  Justification:     Timer before at EngineTick() call site (Launch.cpp:60), timer after at FEngineLoop::Tick() entry (line 5575). Sequential on same thread, no aliasing.
Reachable (Structural):
  Result:            Yes
  Evidence IDs:      EVID-002
  Justification:     EngineTick() unconditionally calls GEngineLoop::Tick() (Launch.cpp:60). No conditional, no early return.
Reachable (Runtime):
  Result:            Yes
  Evidence IDs:      EVID-004
  Justification:     INV-003 binary search: P0 markers observed at ~331ms intervals throughout the slow-state capture, demonstrating the boundary is entered each iteration.

Lifecycle State:     Evaluated
Supersedes:          BR-005
```

### BR-012: IsEngineExitRequested() call (supersedes BR-006)

```
Record ID:           BR-012
Boundary ID:         B-006
Owning-Investigation: INV-2026-006
Class:               C2 — Execution enters another component
Type:                Function call
Instance:            IsEngineExitRequested() call (while condition)

Evidence IDs:        EVID-003 (CoreGlobals.h:395), EVID-002 (Launch.cpp:190)
Inference IDs:       INF-008

Observable:
  Result:            No
  Evidence IDs:      EVID-003
  Justification:     IsEngineExitRequested() is FORCEINLINE (CoreGlobals.h:395); no distinct runtime call event exists that can be observed independently of the caller.
Instrumentable:
  Result:            No
  Evidence IDs:      EVID-003
  Justification:     FORCEINLINE eliminates the function boundary — no function entry point to instrument.
Partitionable:
  Result:            No
  Evidence IDs:      EVID-003
  Justification:     No observable boundary to partition around.
Reachable (Structural):
  Result:            Yes
  Evidence IDs:      EVID-002
  Justification:     The call site in the while condition (Launch.cpp:190) is structurally reachable as source code.
Reachable (Runtime):
  Result:            No
  Evidence IDs:      EVID-003
  Justification:     Supported by INF-008. No distinct runtime function-call event exists because the function is inlined into the caller.

Lifecycle State:     Evaluated
Supersedes:          BR-006
```

## New Evidence

| ID | Kind | Source | Content |
|----|------|--------|---------|
| EVID-004 | Observation (INV-003 trace) | INV-2026-003 binary search logs | P0 markers observed at approximately 331ms intervals throughout the slow-state capture |

## New Inferences

| ID | Statement | Evidence IDs | Confidence | Status |
|----|-----------|-------------|------------|--------|
| INF-001 | B-005 executes every iteration during the slow state: structural (unconditional call in source) and supported by P0 marker observations | EVID-002, EVID-004 | High | Confirmed |
| INF-002 | B-001 (Tick return) partition is asymmetric: the 'after' interval spans the entire unresolved gap (return → next Tick entry). Partitioning at this boundary separates the fast interior (Tick body) from the gap (post-return). | EVID-001, EVID-004 | High | Confirmed |
| INF-003 | Successive Tick entries (P0 markers observed at ~331ms intervals, EVID-004) require completion of the prior Tick invocation; therefore the return boundary (B-001) is reached each iteration the slow state persists. | EVID-004 | High | Confirmed |
| INF-004 | B-004 (EngineTick call) partition is symmetric: the boundary separates the unresolved interval into independently measurable pre-call (P8 → call site) and post-entry (EngineTick entry → P0) regions. Both partitions are non-empty. | EVID-002, EVID-004 | High | Confirmed |
| INF-005 | Since EngineTick() unconditionally calls GEngineLoop::Tick() (EVID-002, Launch.cpp:60), and Tick entry (P0) was observed each iteration (EVID-004), EngineTick() must have executed each iteration during the slow state. | EVID-002, EVID-004 | High | Confirmed |
| INF-006 | B-002 (EngineTick return) partition is asymmetric: return is followed by the IsEngineExitRequested check, then EngineTick call, then Tick entry (P0). The 'after' interval spans most of the unresolved gap. | EVID-002, EVID-004 | High | Confirmed |
| INF-007 | EngineTick() executes each iteration (inferred from EVID-004, EVID-002). Any call that enters must eventually return; therefore the return boundary (B-002) is reached each iteration. | EVID-004, EVID-002 | High | Confirmed |
| INF-008 | IsEngineExitRequested() is declared FORCEINLINE (EVID-003, CoreGlobals.h:395). The compiler omits distinct function call/return boundaries; the function body is inlined at the call site. Therefore neither B-003 (function return) nor B-006 (function call) exist as distinct runtime events. | EVID-003 | High | Confirmed |

## Evidence Index

| ID | Kind | Source | Content |
|----|------|--------|---------|
| EVID-001 | SourceAudit | LaunchEngineLoop.cpp | FEngineLoop::Tick() function body and return (lines 5575–6197) |
| EVID-002 | SourceAudit | Launch.cpp | EngineTick() wrapper and while-loop (lines 58–61, 190–193) |
| EVID-003 | SourceAudit | CoreGlobals.h | IsEngineExitRequested() = GIsRequestingExit flag read (line 398) |
| EVID-004 | Observation (INV-003 trace) | INV-2026-003 binary search logs | P0 markers observed at approximately 331ms intervals throughout the slow-state capture |

## Evaluation Completeness Gate (GR-002)

**Schema**:

```
Gate Name:  Evaluation Completeness
Input:      Evaluated Boundary Records (BR-007 through BR-012)
Checks:     All evaluation criteria decided for all candidates (see checklist)
Pass Criteria: All checkboxes satisfied
Output:     Gate Report (artifact, ID: GR-002)
Result:     PASS / FAIL / NOT RUN
```

Checklist:

- [x] BR-007: Observable decided (Yes/No) with Evidence IDs
- [x] BR-007: Instrumentable decided (Yes/No) with Evidence IDs
- [x] BR-007: Partitionable decided (Yes/No) with Evidence IDs
- [x] BR-007: Reachable (Structural) decided (Yes/No) with Evidence IDs
- [x] BR-007: Reachable (Runtime) decided (Yes/No) with Evidence IDs
- [x] BR-008: all five criteria decided
- [x] BR-009: all five criteria decided
- [x] BR-010: all five criteria decided
- [x] BR-011: all five criteria decided
- [x] BR-012: all five criteria decided
- [x] Every criterion not decided has a documented evidence gap
- [x] All referenced EVID-xxx exist
- [x] Supersedes chain valid (each BR-N+6 supersedes BR-N)

Result: PASS — all checkboxes satisfied. Evaluation complete for all 6 candidates.

## Decision Log

| ID | Decision | Based on | Accepted | Rejected | Justification |
|----|----------|----------|----------|----------|---------------|
| DEC-002 | Admit 4 viable candidates (B-001, B-002, B-004, B-005) into Boundary Selection. Eliminate B-003, B-006 (FORCEINLINE eliminates function boundary at runtime). | GR-002, BR-007, BR-008, BR-009, BR-010, BR-011, BR-012 | B-001, B-002, B-004, B-005 | B-003, B-006 | All 6 candidates evaluated against 5 criteria. 4 pass all five (5/5 Yes). 2 eliminated (Observable=No due to FORCEINLINE). Ready for successor investigation INV-2026-007. |

## Exit Criteria

- [x] All 5 criteria evaluated for all 6 candidates (B-001 through B-006)
- [x] BR-007 through BR-012 created with Lifecycle State = Evaluated
- [x] Evaluation Completeness Gate (GR-002) — PASS
- [x] All evidence gaps documented
- [x] Ready for Boundary Selection (successor investigation INV-2026-007)
