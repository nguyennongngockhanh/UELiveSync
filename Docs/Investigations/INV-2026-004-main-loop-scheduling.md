# INV-2026-004: Identify Source of ~329ms Main Loop Scheduling Gap

## Metadata

- **Status**: Execution Boundary Identification Required
- **Owner**: Khanh
- **Started**: 2026-07-20
- **Closed**: 2026-07-20
- **Classification**: Main Loop Scheduling / Platform
- **Depends-on**: INV-2026-003

## Inputs from INV-2026-003

**Established facts:**

- The observed wall-clock interval between successive entries into `FEngineLoop::Tick()` is ~329ms during the slow state.
- The execution time inside the instrumented regions of `FEngineLoop::Tick()` remains only a few milliseconds.
- All Phase 2 instrumentation has been removed.

**Known causal boundary:**

```
return from FEngineLoop::Tick()
    ↓
~329ms wall-clock gap
    ↓
next entry into FEngineLoop::Tick()
```

## Problem

The main loop in `Launch.cpp` calls `FEngineLoop::Tick()` in a `while` loop (simplified control flow):

```cpp
while (!IsEngineExitRequested())
{
    EngineTick();
}
```

During the slow state (Bug C), the wall-clock interval between successive entries into `FEngineLoop::Tick()` is ~329ms, while the execution time of `FEngineLoop::Tick()` itself is only ~4.5ms. This means ~329ms is spent outside the engine's tick function.

The source of this scheduling gap is unknown.

## Hypotheses

| ID | Observation boundary | Status | Priority |
|----|---------------------|--------|----------|
| L1 | Before Launch.cpp while-loop re-enters | Open | — |
| L2 | Inside Launch.cpp loop (between iterations) | Open | — |
| L3 | Inside platform abstraction before EngineTick() | Open | — |
| L4 | Outside UE (OS/platform scheduling) | Open | — |

Mechanisms within each location are not hypothesized until the location is identified.

## Investigation Plan

### Invariant

Every new marker must reduce the unknown interval.

Never instrument inside a region already proven to execute in only a few milliseconds.

If a marker cannot shrink the causal boundary, it should not be added.

A source audit must identify candidate boundaries before any runtime instrumentation is added.

Runtime instrumentation is only permitted after the source audit demonstrates that the boundary can be partitioned.

No instrumentation may be added unless the source audit identifies at least one observable boundary that could move the causal boundary.

Every instrumentation phase must reduce the unexplained interval. If the unexplained interval is unchanged, the investigation must return to Boundary Audit before adding more markers.

If an instrumentation result is compatible with more than one causal model, the investigation must not choose between them until another observation distinguishes them.

If the causal boundary moves outside the current investigation scope, close the investigation after documenting the observational boundary and open a successor investigation rather than expanding scope.

A Boundary Audit may conclude that the current scope is exhausted, but it may not conclude that the cause lies outside that scope unless an observable boundary into the next scope has been identified.

A successor investigation must inherit an observational boundary, not a suspected mechanism.

A new investigation may inherit only the unresolved observational boundary and established facts. It must not inherit unresolved hypotheses from the predecessor investigation.

Every completed investigation must leave the unexplained interval no larger than it was at the beginning. If the interval cannot be reduced, the investigation must explicitly document why no observable partition exists within its scope.

Before identifying which boundary to partition, an execution boundary inventory must list all candidate boundary types in the unresolved interval. Each candidate must be evaluated for observability, instrumentability, partitionability, and reachability before selection.

A candidate boundary is not evidence. Listing a boundary in the inventory does not imply that execution reaches that boundary. Boundary reachability must be established independently before the boundary can be selected for binary partitioning.

Binary partition may operate only on observed execution boundaries. Architectural, conceptual, or hypothetical boundaries must first be converted into observable execution boundaries before they may be partitioned.

Observation shall contain only directly observed evidence. Any interpretation or causal explanation shall be recorded separately as inference.

A boundary may be selected only if evaluating it is expected to reduce the unresolved interval. Selection without expected interval reduction is prohibited.

A rejected boundary shall not be re-evaluated unless new evidence changes at least one evaluation criterion (observability, instrumentability, partitionability, or reachability).

An accepted boundary shall remain accepted unless new evidence invalidates the observation or demonstrates that the apparent interval reduction resulted from an unrelated effect.

Decision shall reference one or more Investigation Artifacts. A decision without traceable supporting artifact references is invalid.

A decision shall be reproducible from its referenced artifacts.

Evidence is the primary artifact. Every evidence entry has a unique ID (EVID-xxx) and a Kind (Observation, Trace, SourceAudit, CodeInspection, Perf, etc.). Observation is one Kind of Evidence, not a separate artifact class.

Evidence represents the smallest independently referenceable observation.

Inference may only reference Evidence (by EVID-xxx). Decision may only reference Evidence and/or Inference (by EVID-xxx, INF-xxx).

Inference shall never increase confidence beyond the supporting evidence.

Absence of evidence is not evidence of absence. Unknown shall not be interpreted as No.

Evaluation shall be repeatable using the same Boundary Record and Evidence.

Every investigation artifact shall identify the investigation that created it. Every artifact has exactly one owning investigation.

Only the owning investigation or a declared successor may supersede an artifact.

Artifacts are immutable. Successor investigations reference existing artifacts instead of copying them.

Boundary Records are append-only. Evidence and Inference may be appended. Existing entries shall not be modified. Corrections shall be recorded as superseding entries. An artifact superseded remains readable but is marked with Status: Superseded, Superseded-by, and Reason.

Boundary Record ownership never changes. Successor investigations create a new Boundary Record instead of modifying an existing one.

Each investigation has a frozen scope. Activities outside the declared scope are prohibited even if they appear useful. Scope may only expand through a successor investigation. The scope of a completed investigation is immutable.

A closed investigation shall not produce new artifacts. If new artifacts are needed, a successor investigation is required.

A completed investigation shall not be modified except for Lessons Learned.

Artifact IDs are globally unique across the framework. No two artifacts may share the same ID, even across different investigations. Each ID type uses its own prefix:

- EVID-xxx (Evidence)
- INF-xxx (Inference)
- BR-xxx (Boundary Record)
- DEC-xxx (Decision)
- GR-xxx (Gate Report)
- B-xxx (Boundary ID — also globally unique, distinct from artifact IDs)

IDs are never reset at the start of a new investigation. Monotonic allocation across the entire framework.

Allocated IDs shall never be reused, even if an artifact is superseded or withdrawn.

The content of an Evidence artifact shall never change after creation. Corrections require a superseding Evidence artifact.

Evidence shall contain only directly acquired information. Interpretation belongs exclusively to Inference artifacts.

Decisions are immutable after publication. A revised decision shall be recorded as a new Decision artifact referencing the superseded decision.

Decision artifact records only state transitions (e.g., candidate admitted, candidate rejected, gate passed, inventory frozen). It does not serve as a narrative log.

Evidence supporting a published decision shall be frozen. New evidence discovered after a decision requires a successor investigation.

A Gate executed on the same input artifacts shall always produce the same result.

Every Gate shall identify the evaluator responsible for the result.

Boundary IDs (B-xxx) are logical identities, not artifacts. They are never stored as independent artifacts; only Boundary Records (BR-xxx) are artifacts. Boundary IDs are stable for the lifetime of the framework. Record IDs (BR-xxx) change with each lifecycle transition.

Each concrete execution boundary shall correspond to exactly one Boundary ID.

Each lifecycle transition shall produce exactly one new Boundary Record for that Boundary ID. Lifecycle: Candidate → Evaluated → Selected → Observed → Outcome (Accepted/Rejected).

Lifecycle State cannot move backward. A Boundary Record may only transition to a later state in the lifecycle sequence.

Supersedes shall always reference exactly one previous Boundary Record. The supersession chain is a linked list, not a DAG.

A superseded artifact may have at most one direct successor. Forking (multiple artifacts superseding the same predecessor) is prohibited.

A Boundary Record may supersede only the immediately preceding lifecycle state for the same Boundary ID. Skipping states (e.g., Candidate → Observed without Evaluated → Selected) is prohibited.

An artifact may supersede only another artifact of the same artifact type (BR → BR, EVID → EVID, INF → INF, DEC → DEC, GR → GR, BI → BI). Cross-type supersession is prohibited.

Every Boundary Record shall correspond to exactly one Instance in exactly one Boundary Inventory.

A Boundary Inventory shall never be modified after publication. Corrections require a successor investigation that produces a new Boundary Inventory.

No artifact may reference an artifact that does not yet exist. Every referenced EVID, INF, BR, DEC, or GR ID must have been created before the referencing artifact. The artifact graph is a directed acyclic graph in creation time.

Every investigation shall declare its dependencies (Depends-on: INV-xxx, INV-yyy) in its metadata.

Every referenced investigation shall be Closed before a dependent investigation may start. Parallel investigations are permitted only when explicitly declared as non-conflicting.

### Investigation Artifacts

The following artifact types are defined by the framework. The production order is:

```
Evidence
    ↓
Inference
    ↓
Boundary Inventory       ← derived from Evidence and Inference
    ↓
Boundary Record
    ↓
Gate Report
    ↓
Decision
```

| Artifact | Description | Append-only? | Schema |
|----------|-------------|-------------|--------|
| Evidence | Primary raw data with Kind (Observation, Trace, SourceAudit, CodeInspection, Perf, ...). ID: EVID-xxx. Content shall contain only directly acquired information; interpretation belongs exclusively to Inference artifacts. Immutable after creation. | Yes | ID (EVID-xxx), Owning-Investigation, Kind, Status (Active/Superseded), Superseded-by, Reason, Timestamp, Data, Source |
| Inference | Interpretation derived exclusively from Evidence. May not increase confidence beyond supporting evidence. | Yes | ID (INF-xxx), Owning-Investigation, Statement, Evidence IDs, Confidence (Low/Medium/High), Status (Draft/Confirmed/Rejected/Superseded), Supersedes |
| Boundary Inventory | Snapshot of candidate space derived from Evidence and Inference. Single-shot per investigation; successors create new inventories referencing the previous one. Not evidence. | Yes | ID (BI-xxx), Owning-Investigation, Class → Type → Instance tree, Derived-from (BI IDs) |
| Boundary Record | Standardized record for a single boundary instance. Each record has a unique Record ID (BR-xxx). The Boundary ID (B-xxx) groups records across lifecycle phases. Ownership never changes; successor investigations create new records with updated Lifecycle State. Lifecycle: Candidate → Evaluated → Selected → Observed → Outcome (Accepted/Rejected). Evaluation fields populated only at Lifecycle=Evaluated (empty at Candidate). Each evaluation field has a Result (Yes/No/Unknown) and supporting Evidence IDs. | Yes | Record ID (BR-xxx), Boundary ID (B-xxx), Owning-Investigation, Class, Type, Instance, Evidence IDs, Inference IDs, Observable (Result, Evidence IDs), Instrumentable (Result, Evidence IDs), Partitionable (Result, Evidence IDs), Reachable-Structural (Result, Evidence IDs), Reachable-Runtime (Result, Evidence IDs), Lifecycle State, Supersedes (Record ID) |
| Decision Log | Records only state transitions (candidate admitted, candidate rejected, gate passed, inventory frozen). Immutable after publication. Based on a Gate Report. | Yes | ID (DEC-xxx), Owning-Investigation, Decision, Gate Report IDs (GR-xxx), Justification, Timestamp, Supersedes |
| Gate Report | Result of a quality gate evaluation. Same input artifacts always produce the same result. Informs a Decision. References input artifact IDs; evidence is consumed indirectly through those artifacts. | Yes | ID (GR-xxx), Gate Name, Owning-Investigation, Result (PASS/FAIL/NOT RUN), Input (artifact IDs), Checks, Pass Criteria, Evaluator, Timestamp, Justification |
| Lessons Learned | Retrospective observations after investigation closure. Mutable — may be revised as understanding evolves. Corrections update in place. | No (mutable) | LL-xxx (informal identifier), Owning-Investigation, Content |

### Scope

Do not re-instrument any of the following — they have been eliminated by INV-2026-003:

- DrawWindows
- Slate
- EditorEngine
- Viewport
- Render thread
- Any region inside FEngineLoop::Tick()

The current instrumentation did not observe the delay inside any instrumented execution region of `FEngineLoop::Tick()`. The unexplained interval is bounded to the period after the last instrumented point (P8) and before the next observed entry into `FEngineLoop::Tick()`.

### Phase 1 — Boundary Audit

Audit the full call chain from P8 (last instrumented point) through return, Launch.cpp, EngineTick(), to P0 (next observed entry). Identify observable boundaries suitable for binary partitioning: mark which segments are UE code, SDL, platform abstraction, or OS. This is binary search at the call graph level.

#### B1. After P8 in FEngineLoop::Tick()

| Line | Statement | Notes |
|------|-----------|-------|
| 6170–6173 | CPU stats (`FPlatformTime::GetCPUTime`, `SET_FLOAT_STAT`) | Fast |
| 6176–6178 | UObject count stat (conditional) | Fast |
| 6179 | `}` closing main block | — |
| 6181 | `TRACE_END_FRAME(TraceFrameType_Game)` | Fast |
| 6183–6191 | `BUILD_EMBEDDED_APP` sleep block | Not active on Linux |
| 6193–6196 | `PLATFORM_MAC` async task drain | Not active on Linux |
| 6197 | `}` — function return | — |

#### B2. Launch.cpp main loop (line 190–193)

```cpp
while( !IsEngineExitRequested() )
{
    EngineTick();
}
```

Between `EngineTick()` calls: `IsEngineExitRequested()` = `return GIsRequestingExit;` (flag read, `CoreGlobals.h:398`). No sleep, no yield, no event pump, no guard.

#### B3. EngineTick wrapper (line 58–61)

```cpp
LAUNCH_API void EngineTick( void )
{
    GEngineLoop.Tick();
}
```

Pure wrapper. No try/catch, no platform callback, no yield, no sleep.

#### B4. SDL event pumping

`FLinuxPlatformApplicationMisc::PumpMessages` (`LinuxPlatformApplicationMisc.cpp:662`) calls `SDL_PollEvent`. Called INSIDE `FEngineLoop::Tick()` at line 5784 — part of the P0→C region already proven fast (~0.06ms).

#### Boundary Audit Table

| Boundary | Source file | Observable? | Partitionable? | Instrument? |
|----------|------------|-------------|----------------|-------------|
| P8 → return | LaunchEngineLoop.cpp | Yes | Yes | Unlikely — all fast |
| return → EngineTick | Launch.cpp | Yes | No (function call) | No |
| EngineTick → GEngineLoop.Tick | Launch.cpp | Yes | No (pure wrapper) | No |
| while-loop iteration | Launch.cpp | Yes | No (flag check only) | No |
| GEngineLoop.Tick → P0 | LaunchEngineLoop.cpp | Yes | Already partitioned | No |

#### Boundary Audit Conclusion

The Boundary Audit did not identify any observable, partitionable boundary within the audited UE control flow that could account for the unexplained interval.

The current investigation cannot further partition the interval using UE source alone.

The entire code path from P8 through return, Launch.cpp, EngineTick(), and back to P0 is ~0.2ms. No single statement or block in this path accounts for 329ms. The unexplained interval (~329ms) remains bounded between P8 and P0, but no observable boundary within UE source exists to partition it further.

### Phase 2 — Execution Boundary Discovery

**Goal**: Identify an observable execution boundary immediately adjacent to the current observational boundary.

The distinction between architectural boundary and execution boundary is critical:

- **Architectural boundary**: P8 → return → Launch.cpp → EngineTick → P0 (source-level control flow)
- **Execution boundary**: userspace → syscall → kernel scheduler → wake up → userspace (runtime execution flow)

The Boundary Audit (Phase 1) exhausted all observable boundaries within UE source at the architectural level. This phase operates at the execution level.

**Status**: Complete.

#### Source Audit — Full Call Graph

The complete control flow from P8 to next P0:

```
main()                                          [LaunchLinux.cpp:14]
  → CommonUnixMain()                            [UnixCommonStartup.cpp:242]
    → GuardedMain()                             [Launch.cpp:87]
      → while (!IsEngineExitRequested())        [Launch.cpp:190]
          → EngineTick()                        [Launch.cpp:192]
            → GEngineLoop.Tick()                [LaunchEngineLoop.cpp:5575]
              → ... FEngineLoop::Tick() body ...
              → return
            ← return to EngineTick()
          ← return to while loop
        ← check IsEngineExitRequested()         [flag read only]
        ← call EngineTick() again
```

#### Source Audit — Between P8 and next P0

| Step | Code | File:Line | Notes |
|------|------|-----------|-------|
| P8 | last instrumented point | LaunchEngineLoop.cpp | After Render_EndFrame |
| CPU stats | `FPlatformTime::GetCPUTime()` | LaunchEngineLoop.cpp:6170 | Fast |
| UObject count | `SET_DWORD_STAT` | LaunchEngineLoop.cpp:6177 | Fast, conditional |
| TRACE_END_FRAME | `TRACE_END_FRAME` | LaunchEngineLoop.cpp:6181 | Fast |
| BUILD_EMBEDDED_APP | conditional | LaunchEngineLoop.cpp:6183 | Not active on Linux |
| PLATFORM_MAC | conditional | LaunchEngineLoop.cpp:6193 | Not active on Linux |
| return | function return | LaunchEngineLoop.cpp:6197 | — |
| return to EngineTick | function return | Launch.cpp:60 | — |
| return to while | function return | Launch.cpp:192 | — |
| IsEngineExitRequested | `return GIsRequestingExit` | CoreGlobals.h:398 | Flag read only |
| EngineTick() | `GEngineLoop.Tick()` | Launch.cpp:60 | — |
| P0 | next instrumented point | LaunchEngineLoop.cpp | Before PumpMessages |

Total observable UE code: ~0.2ms.

#### Source Audit — Additional observations

- `CommonUnixMain()` wraps `GuardedMain()` with crash handler setup and stdin ticker. No event pump, no sleep, no yield between iterations.
- `GuardedMain()` contains initialization (PreInit, EditorInit) and the while loop. No platform callback between iterations.
- `EngineTick()` is a pure wrapper: `GEngineLoop.Tick();`.
- `IsEngineExitRequested()` is a flag read: `return GIsRequestingExit;`. No side effect.
- SDL event pumping (`PumpMessages`) is INSIDE `FEngineLoop::Tick()` at line 5784 — already proven fast (~0.06ms).
- No sleep, yield, event pump, or platform callback was identified in any audited code path between P8 and next P0.

#### Execution Boundary Discovery Conclusion

The audited UE source does not expose any additional observable, partitionable execution boundary adjacent to the current observational boundary (P8 → next observed P0).

The current source audit therefore cannot reduce the observational boundary further.

This result does not imply that no such execution boundary exists. It only establishes that no additional execution boundary was identified within the audited UE source.

The unexplained interval (~329ms) remains bounded between P8 and next P0. No observable partition exists within the audited UE source to reduce it further.

Within the 329ms gap, the thread's execution state is unknown:
- Is the thread RUNNING?
- Is the thread SLEEPING?
- Is the thread BLOCKED?
- Is the thread preempted?
- Is the thread in a futex?
- Is the thread in poll()?
- Is the thread in epoll_wait()?
- Is the thread in nanosleep()?

The audited UE source does not answer these questions. The next phase must identify an observable execution boundary that can.

### Phase 3 — Execution Boundary Identification

**Goal**: Identify the next observable execution boundary adjacent to the current observational boundary.

**Input**: The current observational boundary (P8 → next observed P0) cannot be reduced within UE source.

**Required**: A new execution boundary that partitions the 329ms gap into observable segments. For example:

```
P8
    ↓
syscall entry
    ↓
kernel scheduling
    ↓
syscall exit
    ↓
P0
```

or

```
P8
    ↓
SDL_PumpEvents
    ↓
poll()
    ↓
SDL_PumpEvents return
    ↓
P0
```

Only when such a boundary is identified can a successor investigation (INV-2026-005) inherit it and proceed with binary search.

**Status**: Not started.

Possible approaches include:
- runtime tracing
- platform source audit
- SDL source audit
- userspace tracing
- kernel tracing

Selection deferred until a new observable execution boundary is identified.

### Phase 4 — Causal Intervention

Based on Phase 3 results, intervene to confirm the identified region controls the interval.

## Exit Criteria

- ✅ Boundary Audit complete — no observable, partitionable boundary found within UE source
- ✅ Execution Boundary Discovery complete — no additional execution boundary identified within audited UE source
- ✅ Execution Boundary Identification required — next phase must identify a new observable execution boundary
- □ Execution boundary identified
- □ Binary Search — partition the gap using the discovered execution boundary
- □ Causal intervention demonstrates the identified region controls the interval

**Current state**: INV-2026-004 complete. All phases within UE source scope exhausted.

**Pipeline status**:

| Phase / Gate | Status | Owner |
|------|--------|-------|
| Observation | ✅ INV-2026-003 | Khanh |
| Boundary Audit | ✅ INV-2026-004 | Khanh |
| Boundary Inventory v1 | ✅ INV-2026-004 | Khanh |
| Boundary Inventory v2 | ✅ INV-2026-005 | Khanh |
| Boundary Record Generation | ✅ INV-2026-005 | Khanh |
| Gate: Record Completeness | Passed | — |
| Boundary Evaluation | Waiting for Gate | — |
| Boundary Selection | Waiting for Evaluation | — |
| Binary Search | Waiting for Selection | — |
| Causal Intervention | Waiting for Binary Search | — |

**Deliverables for INV-2026-005**: Boundary Inventory v2 (types → instances) + full Boundary Records for every instance. No evaluation, no selection, no instrumentation, no tracing.

**Current unresolved interval**:

```
P8
    ↓
unexplained interval
    ↓
next observed P0
```

**Current objective**: identify an observable execution boundary capable of partitioning this interval.

## Decision Log

| ID | Decision | Based on | Accepted | Rejected | Reason |
|----|----------|----------|----------|----------|--------|
| D1 | Open investigation | INV-2026-003 handed off the unexplained interval between P8 and the next observed P0 | Opened | — | Boundary handed off from INV-2026-003 |
| D2 | Boundary Audit complete | Audited all observable UE code between P8 and the next observed P0. All audited code paths execute in approximately 0.2ms. This observation does not imply that the remaining ~329ms occurs outside UE. | Scope Exhausted | — | No observable, partitionable boundary found within UE source |
| D3 | Execution Boundary Discovery complete | Source-audited full call graph from P8 through return, Launch.cpp, EngineTick(), and back to P0. No additional observable, partitionable execution boundary identified within the audited UE source. This result does not imply that no such boundary exists — only that none was identified within the audited scope. | Scope Exhausted | — | No additional execution boundary identified within audited UE source |
| D4 | INV-2026-004 closed | All phases within UE source scope exhausted. No additional observable partition was identified within the audited UE source. Any successor investigation must first establish a new observable execution boundary before binary search can continue. | Closed | — | Investigation complete — successor must establish new observable execution boundary |

## Lessons Learned

_(To be updated as investigation progresses)_

## Boundary Inventory (v1 — predecessor to INV-2026-005)

Before INV-2026-005 can open, an execution boundary inventory must list all candidate boundaries in the unresolved interval (P8 → next observed P0). This v1 inventory uses abstract boundary classes only. INV-2026-005 should start with a v2 inventory that refines class → type → instance.

### Hierarchy

```
Boundary Class (abstract, reusable)
    └── Boundary Type (general mechanism)
            └── Boundary Instance (concrete, observable, partitionable)
```

Example:
```
Execution leaves current component (class)
    └── Function return (type)
            └── FEngineLoop::Tick() return (instance)

Execution changes privilege level (class)
    └── Syscall entry (type)
            └── poll() syscall entry (instance)

Execution suspends (class)
    └── Scheduler preemption (type)
            └── schedule() context switch (instance)
```

### Candidate boundary classes (v1)

| ID | Boundary class | Description | Observable? | Instrumentable? | Partitionable? | Notes |
|----|---------------|-------------|-------------|-----------------|----------------|-------|
| C1 | Execution leaves current component | Transition from current code to another component (function return, library call, signal trampoline) | ? | ? | ? | Includes P8 return, while-loop iteration. |
| C2 | Execution enters another component | Transition into a different userspace component (library, runtime, handler) | ? | ? | ? | Requires component source audit. |
| C3 | Execution changes privilege level | Transition between userspace and kernel (syscall, interrupt, signal) | ? | ? | ? | Requires runtime or kernel tracing. |
| C4 | Execution suspends | Current execution stops (scheduler preemption, blocking wait, sleep) | ? | ? | ? | Requires kernel tracing. |
| C5 | Execution resumes | Execution restarts after suspension (wake-up, context switch return) | ? | ? | ? | Requires kernel tracing. |

Note: "Execution changes thread" is not a boundary class — it is a scheduler consequence that occurs between C4 (Execution suspends) and C5 (Execution resumes). It may become a boundary instance under C4 or C5 once refined to type → instance level in v2.

### Boundary lifecycle

Each boundary instance progresses through the following states within a single investigation:

```
Candidate
    ↓
Evaluating
    ↓
Selected
    ↓
Observed
    ↓
Interval Reduced?
    ├── Yes → Accepted
    └── No  → Rejected
```

- **Candidate**: Listed in inventory, not yet evaluated.
- **Evaluating**: Under audit for observability, instrumentability, partitionability, and reachability.
- **Selected**: Passed evaluation, assigned to observation phase.
- **Observed**: Runtime observation of the boundary produced a measurable segment.
- **Accepted**: The unresolved interval was reduced after observation.
- **Rejected**: The unresolved interval was not reduced after observation.

### Reachability

Reachability has two distinct dimensions:

- **Structural Reachability**: Can execution reach this boundary from source analysis? (e.g., `EngineTick()` → `GEngineLoop.Tick()` is structurally reachable.)
- **Runtime Reachability**: In the execution trace under investigation, does execution actually cross this boundary? (e.g., `poll()` may be structurally reachable within SDL, but not called in the current trace.)

Both must be established independently. Structural reachability does not imply runtime reachability.

### Boundary Record format

Each boundary instance evaluated in an investigation should have a standard record:

```
Boundary ID: <unique-id>
Class:      <boundary class from inventory>
Type:       <general mechanism>
Instance:   <concrete, observable boundary>

Observable:         Yes / No / ?
Instrumentable:     Yes / No / ?
Partitionable:      Yes / No / ?
Reachable (Struct): Yes / No / ?
Reachable (Runtime): Yes / No / ?

Observation:  directly observed evidence only (no interpretation)
Inference:    interpretation or causal explanation derived from observation

Interval before:
Interval after:

Decision: Accepted / Rejected
Reason:
```

Observation and Inference are separate fields. Observation shall contain only directly measured evidence. Any interpretation — including statements about where execution is "blocked," "waiting," or "scheduled" — shall be recorded in Inference. This prevents successor investigations from inheriting conclusions as if they were raw data.

This record concentrates all evidence for a single boundary in one place. Decision Log entries then reference the record instead of duplicating evidence.

### Inventory status

v1 complete (classes only). v2 (type → instance) deferred to INV-2026-005.
