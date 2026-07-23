# INV-2026-005: Execution Boundary Inventory v2 and Boundary Records

## Metadata

- **Status**: Closed
- **Gate: Record Completeness**: PASS
- **Owner**: Khanh
- **Started**: 2026-07-20
- **Closed**: 2026-07-20
- **Classification**: Execution Boundary Inventory (Preparation)
- **Depends-on**: INV-2026-004

## Inputs from INV-2026-004

**Established facts:**

- The unresolved interval is bounded between P8 (return from `FEngineLoop::Tick()`) and the next observed P0 (entry into `FEngineLoop::Tick()`).
- The audited UE source does not expose any additional observable, partitionable execution boundary within this interval.
- Five boundary classes have been identified at the abstract level (C1–C5).

**Known pipeline:**

```
Observation                  ✅ INV-2026-003
Boundary Audit               ✅ INV-2026-004
Boundary Inventory v1        ✅ INV-2026-004
──────────────────────────────────────
Boundary Inventory v2        ← INV-2026-005
Boundary Record Generation
Gate: Record Completeness
──────────────────────────────────────
Boundary Evaluation          (successor)
Boundary Selection           (successor)
Binary Search                (successor)
Causal Intervention          (successor)
```

## Objective

Refine the Boundary Inventory from Class → Type → Instance for all structurally identified instances within the current investigation scope, and generate one Boundary Record for each identified instance.

## Scope

### In Scope

1. **Boundary Inventory v2**: Decompose each boundary class into concrete types, then into specific instances reachable from the unresolved interval (P8 → next P0).
2. **Boundary Records**: For every instance, create one record following the standardized format.
3. **Record Completeness Gate**: Verify all records satisfy the required schema.

### Out of Scope

- Boundary evaluation (good/bad assessment)
- Boundary selection (choosing which boundary to pursue)
- Runtime tracing
- Instrumentation (UE markers, logging)
- Binary partition
- Causal analysis
- Any hypothesis about where the interval is spent

### Invariants

**Structural reachability scope**: Permitted source audit is limited to establishing structural reachability required to instantiate candidate boundaries. No runtime behaviour may be inferred from structural reachability.

**Runtime observation scope**: Runtime observation alone shall not establish structural reachability.

**Inventory hierarchy**: Each boundary instance has exactly one parent boundary type. Each boundary type is assigned uniquely within the current inventory to avoid ambiguity across types.

## Deliverables

### 1. Boundary Inventory v2

Each boundary class (C1–C5 from INV-2026-004) decomposed into types and instances based on structural evidence from the audited call graph (P8 → next P0).

```
C1: Execution leaves current component
    └── Type: Function return
          ├── Instance: FEngineLoop::Tick() return
          ├── Instance: EngineTick() return
          └── Instance: IsEngineExitRequested() return

C2: Execution enters another component
    └── Type: Function call
          ├── Instance: EngineTick() call
          ├── Instance: GEngineLoop::Tick() call
          └── Instance: IsEngineExitRequested() call

C3: Execution changes privilege level
    (no instances)

C4: Execution suspends
    (no instances)

C5: Execution resumes
    (no instances)
```

### 2. Boundary Records (Lifecycle State = Candidate)

Each instance generates exactly one Boundary Record. Evaluation fields (Observable, Instrumentable, Partitionable, Reachable) are not part of the inventory — they belong in INV-006.

#### B-001: FEngineLoop::Tick() return

```
Record ID:      BR-001
Boundary ID:    B-001
Owning-Investigation: INV-2026-005
Class:       C1 — Execution leaves current component
Type:        Function return
Instance:    FEngineLoop::Tick() return

Evidence IDs:   EVID-001 (LaunchEngineLoop.cpp:6170–6197)
Inference IDs:  —

Lifecycle State: Candidate
Supersedes:     —
```

#### B-002: EngineTick() return

```
Record ID:      BR-002
Boundary ID:    B-002
Owning-Investigation: INV-2026-005
Class:       C1 — Execution leaves current component
Type:        Function return
Instance:    EngineTick() return

Evidence IDs:   EVID-002 (Launch.cpp:58–61)
Inference IDs:  —

Lifecycle State: Candidate
Supersedes:     —
```

#### B-003: IsEngineExitRequested() return

```
Record ID:      BR-003
Boundary ID:    B-003
Owning-Investigation: INV-2026-005
Class:       C1 — Execution leaves current component
Type:        Function return
Instance:    IsEngineExitRequested() return

Evidence IDs:   EVID-003 (CoreGlobals.h:398)
Inference IDs:  —

Lifecycle State: Candidate
Supersedes:     —
```

#### B-004: EngineTick() call

```
Record ID:      BR-004
Boundary ID:    B-004
Owning-Investigation: INV-2026-005
Class:       C2 — Execution enters another component
Type:        Function call
Instance:    EngineTick() call (while loop → EngineTick)

Evidence IDs:   EVID-002 (Launch.cpp:58–61, 190–193)
Inference IDs:  —

Lifecycle State: Candidate
Supersedes:     —
```

#### B-005: GEngineLoop::Tick() call

```
Record ID:      BR-005
Boundary ID:    B-005
Owning-Investigation: INV-2026-005
Class:       C2 — Execution enters another component
Type:        Function call
Instance:    GEngineLoop::Tick() call (EngineTick → FEngineLoop::Tick)

Evidence IDs:   EVID-001 (LaunchEngineLoop.cpp:5575), EVID-002 (Launch.cpp:60)
Inference IDs:  —

Lifecycle State: Candidate
Supersedes:     —
```

#### B-006: IsEngineExitRequested() call

```
Record ID:      BR-006
Boundary ID:    B-006
Owning-Investigation: INV-2026-005
Class:       C2 — Execution enters another component
Type:        Function call
Instance:    IsEngineExitRequested() call (while condition)

Evidence IDs:   EVID-003 (CoreGlobals.h:398), EVID-002 (Launch.cpp:190)
Inference IDs:  —

Lifecycle State: Candidate
Supersedes:     —
```

#### C3–C5: No instances

Classes C3 (Execution changes privilege level), C4 (Execution suspends), and C5 (Execution resumes) have no identified instances within the current investigation scope.

### 3. Record Completeness Gate

**Schema**:

```
Gate Name: Record Completeness
Input:     Boundary Records (one per instance, Lifecycle State = Candidate)
Checks:    Schema completeness (see checklist below)
Pass Criteria: All checkboxes satisfied
Output:    Gate Report (artifact, ID: GR-001)
Result:    PASS / FAIL / NOT RUN
Evidence IDs: <list of EVID-xxx referenced during evaluation>
```

Checklist:

- [ ] Record ID exists (BR-xxx)
- [ ] Boundary ID exists (B-xxx)
- [ ] Class exists (one of C1–C5)
- [ ] Type exists (non-empty)
- [ ] Instance exists (concrete, non-empty)
- [ ] Evidence IDs field exists
- [ ] Inference IDs field exists
- [ ] Lifecycle State exists (= Candidate)
- [ ] Owning-Investigation exists
- [ ] Supersedes field exists

Result: PASS when all checkboxes are satisfied. FAIL otherwise. NOT RUN before evaluation begins.

Note: Evaluation fields (Observable, Instrumentable, Partitionable, Reachable) are not part of inventory Boundary Records (Lifecycle State = Candidate). They belong to INV-006 Evaluated Boundary Records.

## Input

- **Boundary classes** (C1–C5) from INV-2026-004:
  - C1: Execution leaves current component
  - C2: Execution enters another component
  - C3: Execution changes privilege level
  - C4: Execution suspends
  - C5: Execution resumes
- **Unresolved interval**: P8 → next observed P0
- **Audited call graph** from INV-2026-004 Phase 2

## Exit Criteria

- ✅ Boundary Inventory v2 complete within the current investigation scope (all classes decomposed to instances)
- ✅ Every instance has exactly one Boundary Record (Lifecycle State = Candidate)
- ✅ Gate: Record Completeness — PASS
- ✅ Inventory frozen — no candidate may be added, removed, or reclassified
- ✅ Ready for Boundary Evaluation (successor investigation INV-006)

## Inventory Freeze

Boundary Inventory v2 is frozen. No candidate may be added, removed, or reclassified within this investigation. Successor investigations shall reference this inventory rather than modify it.

## Decision Log

| ID | Decision | Based on | Accepted | Rejected | Reason |
|----|----------|----------|----------|----------|--------|
| DEC-001 | Admit B-001 through B-006 into candidate inventory | EVID-001, EVID-002, EVID-003 | 6 candidates | C3, C4, C5: no structurally identified instance | Structurally identified instances within P8 → P0 path |
| DEC-002 | No candidate generated for C3, C4, C5 | INV-2026-004 audit | Scope exhausted | — | No structurally identified instance within current investigation scope |

## Evidence Index

| ID | Kind | File | Description |
|----|------|------|-------------|
| EVID-001 | SourceAudit | LaunchEngineLoop.cpp | FEngineLoop::Tick() function body and return (lines 5575–6197) |
| EVID-002 | SourceAudit | Launch.cpp | EngineTick() wrapper and while-loop (lines 58–61, 190–193) |
| EVID-003 | SourceAudit | CoreGlobals.h | IsEngineExitRequested() = GIsRequestingExit flag read (line 398) |
