# Phase 1.4 Foundation Stabilization

## Status: IN PROGRESS

## Foundation Health

| Layer | Status |
|-------|--------|
| Network / Protocol | ✅ Stable |
| Gameplay State | ✅ Stable |
| Editor Presentation | ✅ Mostly Stable |
| Cache / Invalidation | ⚠ Under Investigation |
| Ownership / Lifecycle | ✅ Stable |

---

## Exit Criteria

Phase 1.4 Foundation Stabilization is complete when all layers are
marked ✅ Stable. After that, remaining bugs are feature-specific → Phase 2.

---

## Bug Lifecycle Status

Three statuses. Only **Confirmed** and **Closed** bugs count as bugs.

- **Candidate** — hypothesis only, no evidence. Not a bug yet.
- **Confirmed** — reproduced with evidence. Root cause identified.
- **Closed** — fixed, regression passed, merged.

---

## Closed Bugs

| ID | Title | Layer | Root Cause |
|----|-------|-------|------------|
| BUG-001 | Viewport not refreshed after mesh spawn | Presentation | Missing viewport invalidation |
| BUG-002 | Viewport not refreshed after transform | Presentation | Missing viewport invalidation |
| BUG-003 | Actor spawned at world origin | Presentation | SpawnActor drops transform |
| BUG-004 | Hierarchy attach/detach sync | Gameplay | Cache invalidation after detach |
| BUG-005 | Visibility sync (wrong protocol) | Protocol | PT_Visibility not dispatched |
| BUG-006 | Viewport not refreshed after visibility | Presentation | Missing viewport invalidation |

---

## Confirmed Bugs

None currently.

---

## Candidates (Investigation Backlog)

These are hypotheses, not confirmed bugs. Each must be investigated
with evidence before being promoted to Confirmed.

### Presentation Layer

| ID | Title | Status |
|----|-------|--------|
| CAND-001 | Collection hide/show | Not Investigated |
| CAND-002 | Collection isolate | Not Investigated |
| CAND-003 | Material parameter change | Not Investigated |
| CAND-004 | Texture replacement | Not Investigated |
| CAND-005 | Camera FOV | Not Investigated |
| CAND-006 | Camera clipping | Not Investigated |
| CAND-007 | Camera DOF | Not Investigated |
| CAND-008 | Light intensity | Not Investigated |
| CAND-009 | Light color | Not Investigated |
| CAND-010 | Sky changes | Not Investigated |

### Hierarchy Layer

| ID | Title | Status |
|----|-------|--------|
| CAND-011 | Parent under hidden object | Not Investigated |
| CAND-012 | Parent under hidden collection | Not Investigated |
| CAND-013 | Attach when object hidden | Not Investigated |
| CAND-014 | Detach when hidden | Not Investigated |
| CAND-015 | Reparent multi-level | Not Investigated |

### Lifetime Layer

| ID | Title | Status |
|----|-------|--------|
| CAND-016 | Delete hidden object | Not Investigated |
| CAND-017 | Undo hide | Not Investigated |
| CAND-018 | Redo hide | Not Investigated |
| CAND-019 | Undo parent | Not Investigated |
| CAND-020 | Duplicate hidden object | Not Investigated |

### Cache Invalidation Layer

| ID | Title | Status |
|----|-------|--------|
| CAND-021 | `last_sent_transforms` stale | Not Investigated |
| CAND-022 | `_last_visibility_state` stale | Not Investigated |
| CAND-023 | Parent cache stale | Not Investigated |
| CAND-024 | Material cache stale | Not Investigated |

---

## Promotion Rules

1. **Candidate → Confirmed**: Evidence collected, root cause identified.
2. **Confirmed → Closed**: Fix applied, regression passed, merged.

A Candidate is not a bug until it is Confirmed. Investigation must
follow the evidence-first workflow (Bug Lifecycle in AGENTS.md).

---

## Related

- `64-editor-presentation-contract.md` — Presentation Contract
- `00-index.md` — Architecture document index
