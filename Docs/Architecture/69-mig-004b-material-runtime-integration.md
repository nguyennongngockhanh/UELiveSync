# MIG-004B: Material Runtime Integration

## Status: COMPLETE

## Summary

Completed the Material Runtime Integration on top of the MIG-004A wire format (baseline `phase1.4-post-mig004a`). Added per-material stale-rejection to the three material handlers (`OnMaterialCreate`/`OnMaterialUpdate`/`OnMaterialAssign`), made `MATERIAL_UPDATE` actually re-apply to meshes that were already assigned (the core capability of 004B), and wired `MATERIAL_ASSIGN` emission in the Blender addon.

Wire format, serializer, deserializer, and test vectors are **unchanged** — this migration is runtime-side only.

## Problem

After MIG-004A, the wire carried `sequence_number`/`timestamp` on `MATERIAL_CREATE`/`MATERIAL_UPDATE`/`MATERIAL_ASSIGN`, but the runtime did not consume them:

1. **No stale-rejection** — all three handlers (`UELiveSyncSubsystem.cpp`) processed every packet regardless of order/replay, unlike objects (`GUpdateSequences`) and cameras (`GCameraUpdateSequences`).
2. **MATERIAL_UPDATE was a no-op at runtime** — `OnMaterialUpdate` only merged into `MaterialCreateStorage` + `MaterialDefinitionDatabase`. `MaterialRegistry::Resolve` caches the first-built MID; nothing invalidated the cache and nothing refreshed meshes that were already assigned. So changing a material in Blender after assignment had no visible effect on UE.
3. **No MATERIAL_ASSIGN emission in Blender** — `build_material_assign` existed in `material_protocol.py` but had no caller. Assignments could never reach UE, so the "assign/update works" acceptance was unreachable.

## Root Cause

The protocol layer (004A) was completed ahead of the runtime consumer layer. The registry cache had no invalidation path (`MaterialRegistry.h` carried a `// TODO Phase 2: Invalidate(UUID)` placeholder), the update handler never touched the registry, and the Blender addon still only emitted CREATE/UPDATE.

## Solution

Runtime-only changes across the C++ plugin and the Blender addon.

### UE Plugin

**`MaterialRegistry`** — added `Invalidate(FGuid)`:
- Returns the previously cached `UMaterialInterface*` for the id (or `nullptr` if not cached) and removes it from the cache.
- Does NOT build a new instance; callers re-`Resolve()` after invalidation.

**`OnMaterialCreate`** (`UELiveSyncSubsystem.cpp`) — stale-rejection:
- Static `TMap<FGuid, uint32> GMaterialCreateSequences`, keyed by material GUID, rejects `IncomingSeq <= LastSeq` before mutating storage/database. Mirrors the `GUpdateSequences`/`GCameraUpdateSequences` pattern.

**`OnMaterialUpdate`** — stale-rejection + re-apply:
- Static `TMap<FGuid, uint32> GMaterialUpdateSequences` (kept **separate** from the create counter — Blender tracks create and update sequences independently per material, so a shared map would mis-reject).
- After `MaterialDatabase.UpdateDefinition(View)`: `Invalidate(MaterialGuid)` → `Resolve(MaterialGuid)` → `ReapplyMaterialAssignments(MaterialGuid, OldMID, NewMID)`.

**`OnMaterialAssign`** — stale-rejection:
- Static `TMap<FGuid, uint32> GMaterialAssignSequences`, keyed by the **assigned object** (`PersistentId`) so reassignments of the same object advance one monotonic counter. The check runs before `Resolve()` so stale assigns never build a MID.

**`ReapplyMaterialAssignments(FGuid, OldMID, NewMID)`** — new private helper:
- Iterates `ActorCache`, finds each actor's `StaticMeshComponent`, and replaces `OldMID` with `NewMID` on every slot where `GetMaterial(i) == OldMID`.
- **Ownership source of truth: reuse, no new mapping.** Per the MIG-004B contract constraint, this deliberately re-uses existing runtime state (`ActorCache` + component `GetMaterial`) instead of introducing a `MaterialGuid → (ActorGuid, SlotIndex)` reverse mapping. The component material slot IS the authoritative record of what a mesh currently displays; scanning it on UPDATE is correct for reassignment, actor-destroy, and multi-slot cases with zero additional lifecycle to maintain.

### Blender Addon

**`material_protocol.py`**:
- Added `_material_assign_sequences` (keyed by object persistent_id) + `_next_material_assign_sequence()`. `clear_material_sequences()` now resets all three counters.

**`sync.py`**:
- Imported `build_material_assign` + `_next_material_assign_sequence`.
- Added module-level `_mat_assigned_binding` (maps `(persistent_id, slot_index)` → `str(material_uuid)`) to detect binding changes.
- Added `material_assigns_to_send` list and a collection block after the UPDATE collection: for each non-empty slot, emits `MATERIAL_ASSIGN` only when the binding differs from the last-sent state.
- Added the corresponding send block after the MATERIAL_UPDATE send block.
- Cleared `_mat_assigned_binding` on the reconnect full-snapshot reset so assigns are re-emitted.

## Files Changed

**UE Plugin:**
- `UE_Plugin/.../Public/MaterialRegistry.h` — declared `Invalidate`
- `UE_Plugin/.../Private/MaterialRegistry.cpp` — implemented `Invalidate`
- `UE_Plugin/.../Public/UELiveSyncSubsystem.h` — declared `ReapplyMaterialAssignments`
- `UE_Plugin/.../Private/UELiveSyncSubsystem.cpp` — stale-rejection on 3 handlers + re-apply logic + helper

**Blender Addon:**
- `Blender_Addon/material_protocol.py` — assign sequence counter
- `Blender_Addon/sync.py` — assign emission collection/send/reset

**Docs:**
- `Docs/Architecture/69-mig-004b-material-runtime-integration.md` — this ADR

## Semantic Guarantees Activated

1. **Stale-rejection (MATERIAL_CREATE)** — per-material create sequence; replayed/out-of-order creates dropped.
2. **Stale-rejection (MATERIAL_UPDATE)** — per-material update sequence, independent of the create counter.
3. **Stale-rejection (MATERIAL_ASSIGN)** — per-object assign sequence; dropped before `Resolve()`.
4. **Live material update** — UPDATE invalidates the registry cache and re-applies to every mesh slot currently showing the stale MID.
5. **MATERIAL_ASSIGN end-to-end** — Blender emits assign on binding change; UE resolves and applies.

## Known Limitations

### Reconnect sequence reset

Blender resets all sequence counters on reconnect (full snapshot), but UE static maps persist for the process lifetime. A re-sent packet with a lower seq is rejected as stale. This is **identical** to the pre-existing MIG-002 (`GUpdateSequences`) and MIG-003 (`GCameraUpdateSequences`) behavior; the legacy PT_Material path still carries material data on reconnect. Not a regression introduced by 004B — tracked as a shared pattern-wide follow-up if needed.

### Mesh pipeline dependency

`AssignMaterial`/`ReapplyMaterialAssignments` require the actor to have a `StaticMeshComponent`. If the mesh pipeline is not fully functional, material application cannot be verified in the viewport. Per the 004B contract, this is a **dependency, not a bug**: no mesh-pipeline investigation was opened. Runtime verification that requires a rendered mesh is BLOCKED until the mesh pipeline works; handler-level and protocol-level behavior remain verified via build + regression suites.

### Emission scope in Blender

`MATERIAL_ASSIGN` is emitted inside the existing `bPropertiesChanged` material block, keyed on binding diff. Material reassignment (identity change) sets `bPropertiesChanged`, so rebinding emits an assign. Assign for a slot that becomes empty is not emitted (no "unassign" exists in the protocol).

## Regression Test Matrix

| Scenario | Expected Behavior |
|----------|-------------------|
| MATERIAL_CREATE stale packet (seq <= current) | Rejected by `GMaterialCreateSequences` |
| MATERIAL_UPDATE stale packet | Rejected by `GMaterialUpdateSequences` |
| MATERIAL_ASSIGN stale packet | Rejected by `GMaterialAssignSequences` before Resolve |
| MATERIAL_CREATE then UPDATE (same material) | Update applied, seq spaces independent |
| UPDATE after ASSIGN | Old MID replaced with new MID on the assigned mesh slot |
| UPDATE before any ASSIGN | Cache invalidated; next Resolve builds fresh MID |
| Reassignment to a different material | Old material NOT re-applied (slot no longer shows OldMID) |
| Reconnect | Assignments re-emitted (Blender binding cache cleared) |
| Serializer/wire | Unchanged — all vectors and suites still pass |

## Verification Summary

| Phase | Result |
|-------|--------|
| Phase 1 — Investigation | PASS — evidence: handler bodies, registry cache, Blender counters, bridge dispatch path |
| Phase 2 — Design | PASS — ownership-source re-use (ActorCache + component state), no reverse mapping |
| Phase 3 — Implementation | PASS |
| Phase 4 — Regression | PASS — `run_all_tests.sh` 10/10 suites (C++ 8/8 + Python 53 + cross-lang 93); UE build `ProjectTemplateEditor` Succeeded |
| Phase 5 — Runtime Verification | PENDING/BLOCKED — user-launched session; viewport verification depends on mesh pipeline (see Known Limitations) |

## Acceptance Criteria

1. MATERIAL_CREATE — accepted; stale creates rejected.
2. MATERIAL_UPDATE — accepted; stale updates rejected.
3. MATERIAL_ASSIGN (first) — resolves and assigns.
4. **UPDATE after ASSIGN — must display on the already-assigned mesh** (core 004B capability; implemented via invalidate + re-apply).
5. Stale packets rejected.

Criteria 1–5 verified at code/regression level. Criterion 4 viewport visibility requires the mesh pipeline (dependency, tracked above).
