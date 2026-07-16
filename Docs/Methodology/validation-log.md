# Methodology Validation Log

Track how each investigation exercises AGENTS.md rules.
Use this data to evaluate rule effectiveness after 5–10 investigations.

## Format

```
Investigation: INV-xxxx
Methodology version: v1.x
Rules exercised:
  - [rule name]
  - ...
What worked:
What caused friction:
Proposed methodology change: None | [description]
```

## Entries

### INV-2026-002 — Viewport Not Refreshed After Actor Spawn

```
Investigation: INV-2026-002
Methodology version: v1.9
Rules exercised:
  - Evidence Ownership
  - Evidence Before Conclusion
  - Observation Completeness
  - Single-Variable Experiment
  - Least Perturbation (observation before intervention)
  - Alternative Explanations
  - Exit Criteria
  - Investigation Escalation Ladder
  - Implementation Contract
  - Scope Freeze
  - Playbook Evolution (v1.0 → v1.9)

What worked:
  - Least Perturbation forced observation (EXP-C: DumpDetailedPrimitives)
    before intervention (EXP-D: r.RecreateRenderStateContext).
  - Evidence Before Conclusion prevented over-interpreting
    r.RecreateRenderStateContext results as proof of specific mechanism.
  - Alternative Explanations kept multiple hypotheses alive after
    EXP-D (if it succeeds).
  - Escalation Ladder guided progression from Phase 0B → Level 3 CVars
    without jumping to engine instrumentation.
  - Observation vs Instrumentation distinction prevented conflating
    UE_LOG provenance with HiddenEd=0 observation.

What caused friction:
  - Least Perturbation hierarchy needed 5 iterations (v1.4 → v1.9)
    to correctly classify check() vs ensure() vs UE_LOG.
  - Early EXP-D design over-interpreted: "MarkRenderStateDirty missing"
    from "console command might change behavior." Evidence Before
    Conclusion rule existed but was not applied strictly enough.
  - No validation log existed during the investigation — this entry
    is reconstructed retrospectively.
  - EXP-C designed to use DumpDetailedPrimitives, but running the
    command requires clicking UE Output Log → typing command. This
    interaction may cause the actor to appear (the bug itself).
    Observer effect invalidated the experiment design.

Proposed methodology change:
  - None for v1.9. Methodology proved its value through iteration.
  - Consider: validation log should be created at investigation start,
    not retrospectively.
  - New implicit rule discovered: "Observation should not require a
    perturbation that may eliminate the phenomenon being observed."
    This is a corollary of Least Perturbation that was not explicit
    in v1.9. Candidate for v2.0 if validated by future incidents.
  - Escalation for blocked observation: investigate whether the
    underlying API can be invoked programmatically before resorting
    to workarounds (ExecCmds, automation, engine instrumentation).
  - Step A audit result: no public equivalent API exists to reproduce
    DumpDetailedPrimitives behavior. Static bool not exported.
    Public ViewDebug APIs exist but suitability for EXP-C is unknown
    (separate investigation: EXP-C1).
```

## Summary After N Investigations

| Rule | Times Exercised | Friction Reports | Status |
|------|----------------|-----------------|--------|
| Evidence Ownership | | | |
| Evidence Before Conclusion | | | |
| Observation Completeness | | | |
| Single-Variable Experiment | | | |
| Least Perturbation | | | |
| Alternative Explanations | | | |
| Exit Criteria | | | |
| Investigation Escalation Ladder | | | |
| Implementation Contract | | | |
| Scope Freeze | | | |
| Playbook Evolution | | | |
| Observation vs Instrumentation | | | |
| Artifact Ownership | | | |
| Build Policy | | | |
| Rollback Policy | | | |
| Engine Immutability | | | |

Update this table after each investigation. Review when N >= 5.
