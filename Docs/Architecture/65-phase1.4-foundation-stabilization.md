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

## Work Types

Four distinct work types. Each has its own ID scheme and lifecycle.

| Type | ID Scheme | Lifecycle |
|------|-----------|-----------|
| Investigation | INV-XXXX | Open → Evidence Collected → Closed |
| Bug | BUG-XXX | Candidate → Confirmed → Closed |
| Migration | MIG-XXX | Pending → In Progress → Done |
| Feature | (Phase 2+) | Planned → In Progress → Done |

**Investigations** collect evidence. They may lead to:
- No bug found
- A new BUG-XXX
- A new MIG-XXX
- An ADR update

**Bugs** have symptoms, evidence, and regression tests.
**Migration tasks** are code changes without confirmed bugs.
**Features** are new functionality.

### Investigation Lifecycle

```
Candidate
    ↓
INV-XXXX opened
    ↓
Evidence collected
    ↓
├── No bug → INV closed
├── BUG-XXX → fix
├── MIG-XXX → migrate
└── ADR update → document
```

### Migration Definition of Done

A migration item is complete only when:

```
[ ] Legacy path removed
[ ] MsgType path verified
[ ] Backward compatibility evaluated
[ ] Regression tests passed
[ ] Documentation updated
[ ] Tracker updated
```

---

## Phase 1.4 — Foundation Bugs

| ID | Title | Layer | Root Cause | Status |
|----|-------|-------|------------|--------|
| BUG-001 | Viewport not refreshed after mesh spawn | Presentation | Missing viewport invalidation | ✅ Closed |
| BUG-002 | Viewport not refreshed after transform | Presentation | Missing viewport invalidation | ✅ Closed |
| BUG-003 | Actor spawned at world origin | Presentation | SpawnActor drops transform | ✅ Closed |
| BUG-004 | Hierarchy attach/detach sync | Gameplay | Cache invalidation after detach | ✅ Closed |
| BUG-005 | Visibility sync (wrong protocol) | Protocol | PT_Visibility not dispatched | ✅ Closed |
| BUG-006 | Viewport not refreshed after visibility | Presentation | Missing viewport invalidation | ✅ Closed |
| BUG-007 | Rename sync (wrong protocol) | Protocol | PT_Rename not dispatched | ✅ Closed |

---

## Phase 1.5 — Legacy Protocol Elimination

Migration tasks. Not bugs — just code that hasn't been migrated yet.
Only becomes a BUG if testing reveals actual failure.

| ID | Lane | Legacy PT_* | Target MsgType | Status |
|----|------|-------------|----------------|--------|
| MIG-001 | Object Delete | PT_Delete_V5 | OBJECT_DELETE (0x22) | ✅ Done |
| MIG-002 | Material | PT_Material (0x05) | MATERIAL_CREATE/UPDATE (0x40/0x41) | Pending |
| MIG-003 | Mesh | PT_Mesh (0x06) | MESH_DATA/CHUNK (0x30-0x34) | Pending |
| MIG-004 | Camera Create | PT_CameraDef (0x1B) | CAMERA_CREATE (0x50) | Pending |
| MIG-005 | Camera Set Active | PT_ActiveCamera (0x15) | CAMERASETACTIVE (0x52) | Pending |

**Not in scope** (no MsgType equivalent yet):
Collection, Keyframe, AssetDef, Snapshot, Playback, Timeline, Sequencer, Capability.

**If a migration item fails during testing:**
```
MIG-XXX
    ↓ Investigate
    ↓ Evidence
    ↓ BUG-YYY created
    ↓ Fix
    ↓ MIG-XXX marked Done
```

---

## Candidates (Investigation Backlog)

Hypotheses, not confirmed bugs. Each must be investigated with
evidence before being promoted to Confirmed.

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

**Bugs**: Candidate → Confirmed (evidence) → Closed (fix + regression)
**Migration**: Pending → In Progress → Done (or → BUG if failure found)

---

## Related

- `64-editor-presentation-contract.md` — Presentation Contract
- `66-confirmed-root-cause-rename-sync.md` — BUG-007 root cause
- `00-index.md` — Architecture document index
