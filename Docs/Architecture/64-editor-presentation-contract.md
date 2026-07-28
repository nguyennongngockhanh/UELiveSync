# 64 — Architectural Decision: Editor Presentation Contract

## Status: FROZEN

## Context

Through BUG-001, BUG-002, BUG-005, and BUG-006, we discovered a recurring
pattern: editor state updates correctly but viewport does not reflect the
change until manual interaction. This reveals three independent responsibility
layers in UELiveSync.

---

## Decision

Define the **Editor Presentation Contract**: every remote feature must
define its presentation contract explicitly. If the contract is not
defined, the feature is incomplete.

---

## Architecture Layers

```
Network / Protocol
        ↓
Gameplay State
        ↓
Editor Presentation
```

Each layer has distinct failure modes:

| Layer | Failure symptom | Example |
|-------|----------------|---------|
| Network/Protocol | UE does not receive change | BUG-005: PT_Visibility not dispatched |
| Gameplay State | Actor state incorrect | (none yet) |
| Editor Presentation | State correct, viewport stale | BUG-001, BUG-002, BUG-006 |

**Dependency**: If a layer is broken, downstream layers cannot function.
BUG-005 demonstrates this: Presentation cannot be correct if Gameplay
was never updated due to a protocol error.

---

## Bug Classification Rule

When a new bug is reported, classify immediately:

- **Outliner correct, viewport wrong** → Presentation layer
- **UE does not receive change** → Protocol/Bridge layer
- **UE receives correct data, actor state wrong** → Gameplay layer

This classification determines investigation scope instantly.

---

## Presentation Contract Matrix

| Feature | State mutation | Presentation contract |
|---------|---------------|----------------------|
| Transform | Actor transform | Viewport refresh |
| Visibility | Hidden flag | Viewport refresh |
| Spawn | Actor/component creation | Viewport refresh |
| Rename | Object name | Outliner refresh |
| Material assignment | Material slots | Viewport refresh |
| Material parameter | Parameter cache | Viewport refresh |
| Texture | Texture reference | Viewport refresh |
| Camera FOV | Camera component | Viewport refresh |
| Collection visibility | Collection state | Outliner + Viewport refresh |

---

## Invariants

### 1. State mutation ≠ viewport presentation

Editor state mutation does not imply viewport presentation update.
Every feature that changes editor-visible state must call the appropriate
invalidation mechanism. The invalidation is not automatic — it must be
explicitly issued.

### 2. Every remote feature must define its Presentation Contract

When implementing a new feature, the Presentation Contract must be
declared before code review. If the contract is not defined, the
feature is not ready for review.

---

## Definition of Done (Remote Feature)

A remote feature is considered complete only if all contracts are satisfied:

```
[ ] Network Contract
    - Message type defined
    - Serialization verified
    - Dispatch verified

[ ] Gameplay Contract
    - Runtime state updated
    - Local caches updated
    - Ownership/invariants preserved

[ ] Presentation Contract
    - Required editor/UI invalidation performed
    - Immediate visual correctness verified
```

If any contract is not satisfied, the feature is not ready for review.

---

## Standard Fix

`RequestEditorViewportRefresh()` is the single entry point for viewport
invalidation. It calls `FEditorViewportClient::Invalidate(false, false)`
on the perspective viewport.

Gate: `CVarLiveSyncForceViewportRedraw >= 1` (default: 1).

---

## Evidence

### Presentation Layer (BUG-001, BUG-002, BUG-006)

These bugs share the same pattern: gameplay state was correct, but editor
presentation was stale until viewport invalidation occurred.

| Bug | State correct? | Viewport updated? | Fix |
|-----|---------------|-------------------|-----|
| BUG-001 | ✅ Spawn at correct location | ❌ Until click | `RequestEditorViewportRefresh()` |
| BUG-002 | ✅ Transform applied | ❌ Until click | `RequestEditorViewportRefresh()` |
| BUG-006 | ✅ Hidden flag applied | ❌ Until click | `RequestEditorViewportRefresh()` |

### Protocol Layer (BUG-005)

BUG-005 is NOT evidence for the Presentation Contract. It demonstrates
that Presentation cannot function if Gameplay was never updated.

| Bug | State correct? | Viewport updated? | Fix |
|-----|---------------|-------------------|-----|
| BUG-005 | ❌ Protocol message not dispatched | N/A | Fix transport dispatch |

---

## Scope

This document defines invariants, not a refactoring plan.

When Material, Camera, Lighting, Collection Visibility, and other features
are implemented, if they also require viewport refresh, we will have
sufficient evidence to design a stable abstraction (e.g., EditorUpdateManager).

Until then, each feature applies `RequestEditorViewportRefresh()` explicitly
after its state mutation. No premature abstraction.

---

## Related

- `61-confirmed-root-causes-viewport-spawn.md` — BUG-001/002/003
- `62-confirmed-root-cause-visibility-sync.md` — BUG-005
- `63-confirmed-root-cause-visibility-viewport-refresh.md` — BUG-006
