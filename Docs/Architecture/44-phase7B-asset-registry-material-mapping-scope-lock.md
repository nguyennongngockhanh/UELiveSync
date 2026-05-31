# Phase 7B — Asset Registry + Material Mapping Scope Lock

**Date**: 2026-05-31  
**Status**: Draft — scope lock (Stage 0)  
**Depends on**: Phase 7A (Static Mesh Identity Mapping) — COMPLETE ✅  
**Blocks**: Phase 7C (Geometry/Modifier Pipeline)

---

## Table of Contents

1. [Purpose](#1-purpose)
2. [Current Architecture](#2-current-architecture)
3. [Gap Analysis](#3-gap-analysis)
4. [Phase 7B Responsibilities](#4-phase-7b-responsibilities)
5. [Relationship to Phase 7A Identity Model](#5-relationship-to-phase-7a-identity-model)
6. [Relationship to Phase 7C](#6-relationship-to-phase-7c)
7. [Identity Model Rules (Extended)](#7-identity-model-rules-extended)
8. [Material Model Rules](#8-material-model-rules)
9. [Implementation Plan](#9-implementation-plan)
10. [Done Criteria](#10-done-criteria)
11. [Out of Scope](#11-out-of-scope)
12. [Risks](#12-risks)
13. [Files Touched (Estimated)](#13-files-touched-estimated)
14. [Glossary](#14-glossary)

---

## 1. Purpose

Phase 7B defines how UELiveSync resolves, registers, caches, and assigns static mesh assets and material slot mappings, building on the stable identity model established in Phase 7A.

After Phase 7A, UELiveSync can:
- Map Blender GUID to UE Actor via `LiveSync_GUID=` tag
- Map Blender mesh datablock name to `FAssetIdentityRef` (xxHash64)
- Enqueue pending asset resolution
- Look up `FSoftObjectPath` in `AssetPathCache` by identity ref
- Assign a single `UStaticMesh` or a fallback primitive

Phase 7B adds:
- A structured **Asset Registry** that manages the `AssetPathCache` lifecycle
- **Slot-based material mapping** from Blender material slots to UE material slots on `UStaticMeshComponent`
- **Material identity** — a deterministic key for Blender materials
- **Material sync pipeline** — emission, resolution, assignment
- Validation and observability for both domains

---

## 2. Current Architecture

### 2.1 — Asset Resolution Model (Phase 7A)

```
Blender                         UE
  │                               │
  │  PT_AssetDef (33 bytes)       │
  │  ┌─────────────────────────── │
  │  │ GUID(16)                  │ │
  │  │ IdentityLow(8)            │ │
  │  │ IdentityHigh(8)           │ │
  │  │ PrimitiveFallback(1)      │ │
  │  └─────────────────────────── │
  │                               │
  │             ┌─────────────────▼──────────┐
  │             │ HandleAssetDef()           │
  │             │  • Store FAssetMetadata    │
  │             │  • Enqueue in              │
  │             │    PendingAssetQueue       │
  │             └───────────┬─────────────── ┘
  │                         │
  │             ┌───────────▼─────────────── ┐
  │             │ ResolvePendingAssets()     │
  │             │  • Per tick, up to 8       │
  │             │  • Lookup AssetPathCache   │
  │             │  • Retry (max 5, exp.bkoff)│
  │             │  • Fallback primitive      │
  │             └───────────┬─────────────── ┘
  │                         │
  │             ┌───────────▼─────────────── ┐
  │             │ AssignStaticMesh()         │
  │             │  • FindActorFast(Guid)     │
  │             │  • Find UStaticMeshComponent│
  │             │  • TryLoad + SetStaticMesh │
  │             └─────────────────────────────┘
  │
  │  AssetPathCache = TMap<FAssetIdentityRef, FSoftObjectPath>
  │    • Populated externally (console command, init callback)
  │    • No auto-discovery
  │    • No material slots
```

### 2.2 — Current Asset Data Structures

| Structure | File | Role |
|-----------|------|------|
| `FAssetIdentityRef` | `AssetIdentityTypes.h:15` | 16-byte identity key (xxHash64 of `obj.data.name`) |
| `FAssetMetadata` | `AssetIdentityTypes.h:56` | Per-GUID metadata: identity, resolved path, retry state, fallback flag |
| `FAssetDiagnostics` | `AssetIdentityTypes.h:87` | Lock-free atomic counters |
| `FPendingAssetQueue` | `PendingAssetQueue.h:55` | Bounded (2048) FIFO of GUIDs awaiting resolution |
| `AssetPathCache` | `UELiveSyncSubsystem.h:627` | `TMap<FAssetIdentityRef, FSoftObjectPath>` — identity → mesh path |

### 2.3 — Material Mapping Status

**No material mapping currently exists.**

- `PT_Material = 0x05` is defined in `SyncTypes.h:211` but has **no handler** in either Blender or UE
- `PT_Mesh = 0x06` is defined in `SyncTypes.h:212` but has **no handler**
- `AssignStaticMesh()` sets the static mesh on the component but never touches material slots
- `AssignFallbackPrimitive()` creates/sets `UStaticMeshComponent` but never touches material slots
- Blender addon has **zero material extraction or sync code**
- `UStaticMeshComponent` has `GetMaterials()` / `SetMaterial()` but UELiveSync never calls them
- No material identity model exists
- No material slot index mapping exists

---

## 3. Gap Analysis

### 3.1 — Asset Registry Gaps

| # | Gap | Current State | Impact |
|---|-----|---------------|--------|
| AR1 | `AssetPathCache` has no lifecycle | Flat `TMap`, populated externally, never pruned | Paths persist forever; stale entries never cleaned |
| AR2 | `AssetPathCache` has no auto-population | Must be populated by console command or external tool | No self-contained mesh discovery |
| AR3 | No name-convention resolution | `AssetPathCache` maps identity → path, but no fallback to UE asset path naming | If cache miss, asset is unresolvable (only fallback primitive) |
| AR4 | No multi-asset identity | One mesh path per identity key | Cannot represent LODs, variants, or importer-generated sub-assets |
| AR5 | No collision detection | `TMap` silently overwrites on duplicate identity key | Undefined behavior if two distinct mesh datablocks produce same xxHash64 |
| AR6 | No registry diagnostics | `UE.LiveSync.Stats` shows `AssetPathCache` count but no per-entry detail | Debugging registry state requires `UE.LiveSync.DumpState` |

### 3.2 — Material Mapping Gaps

| # | Gap | Current State | Impact |
|---|-----|---------------|--------|
| MM1 | No material identity | Blender material names are not hashed or transmitted | No material change detection |
| MM2 | No material packet/protocol | `PT_Material` (0x05) defined but no handler | No wire format for material sync |
| MM3 | No material slot mapping | Blender material slots → UE material slot indices | Materials cannot be assigned to correct slots |
| MM4 | No material resolution | No equivalent of `HandleAssetDef` for materials | No retry, fallback, or time-out for material load |
| MM5 | No Blender material extraction | `sync.py` never reads `obj.material_slots` | Materials not transmitted to UE |
| MM6 | No material identity cache | No `_last_material_identity` equivalent | No change detection for material-only edits |
| MM7 | No multi-material support | Only `SetStaticMesh()` called | No per-slot `SetMaterial()` |
| MM8 | No material fallback | `AssignFallbackPrimitive` uses `GetPrimitiveMesh` with default material | Cannot represent Blender material colors as UE materials |

### 3.3 — Cross-Cutting Gaps

| # | Gap | Current State | Impact |
|---|-----|---------------|--------|
| X1 | Reconnect material replay | No replay recording for material events | Material state lost on reconnect |
| X2 | Snapshot material batch | No `PT_BeginSnapshot`/`PT_EndSnapshot` for materials | Material state not captured in snapshots |
| X3 | ConsoleReset material cleanup | No material state to reset (none exists) | Adding material state requires lifecycle hook |

---

## 4. Phase 7B Responsibilities

### 4.1 — Asset Registry Responsibilities

| # | Responsibility | Priority | Stage |
|---|---------------|----------|-------|
| AR-R1 | Replace flat `TMap` with structured `FAssetRegistry` wrapping `AssetPathCache` | High | 1 |
| AR-R2 | Add name-convention asset scanning (project content path + identity hash) | Medium | 1 |
| AR-R3 | Add periodic registry refresh / staleness check | Low | 2 |
| AR-R4 | Add registry diagnostic output (`UE.LiveSync.Registry.Dump`) | Medium | 1 |
| AR-R5 | Add asset identity collision warning | High | 1 |
| AR-R6 | Add `CacheAssetPath` console command for manual entry | Low | 2 |

### 4.2 — Material Mapping Responsibilities

| # | Responsibility | Priority | Stage |
|---|---------------|----------|-------|
| MM-R1 | Define `FMaterialIdentityRef` (xxHash64 of Blender material name) | High | 1 |
| MM-R2 | Define `FMaterialSlot` (slot index + material identity) | High | 1 |
| MM-R3 | Define `PT_Material` (or new) packet format for material assignment | High | 1 |
| MM-R4 | Implement Blender material extraction (`obj.material_slots`, `slot.material.name`) | High | 1 |
| MM-R5 | Implement Blender material change detection (slot count, slot name, material name) | High | 1 |
| MM-R6 | Implement Blender material send (wire serialization of material slots) | High | 1 |
| MM-R7 | Implement UE `HandleMaterialDef` (store material identity, enqueue resolution) | High | 1 |
| MM-R8 | Implement UE `ResolvePendingMaterials` (load `UMaterialInterface` by path) | High | 1 |
| MM-R9 | Implement UE `AssignMaterial` per slot on `UStaticMeshComponent` | High | 1 |
| MM-R10 | Add material identity cache (`_last_material_identity`) in Blender | Medium | 1 |
| MM-R11 | Add material diagnostics counters | Medium | 2 |
| MM-R12 | Add material snapshot replay (Begin/End) | Medium | 2 |
| MM-R13 | Add material ConsoleReset lifecycle | Medium | 2 |

### 4.3 — Shared Responsibilities

| # | Responsibility | Priority | Stage |
|---|---------------|----------|-------|
| S-R1 | Unified replay recording for material events | Medium | 2 |
| S-R2 | Tombstone gating for material assignment (skip if GUID tombstoned) | High | 1 |
| S-R3 | Stale entry age-out for material metadata | Low | 2 |
| S-R4 | Console command for manual material path registration | Low | 2 |

---

## 5. Relationship to Phase 7A Identity Model

### 5.1 — Identity Hierarchy

```
Blender Object
  ├── obj["ue_guid"]  ────────────────────────►  UE Actor (LiveSync_GUID= tag)
  │
  ├── obj.data.name  ─── xxHash64 ────────────►  FAssetIdentityRef (mesh identity)
  │    │                                             │
  │    │   Phase 7A                                  │
  │    │   AssetPathCache ───────────────────────►   FSoftObjectPath (UStaticMesh)
  │
  ├── obj.material_slots[0].material.name ── xxHash64 ───► FMaterialIdentityRef
  │    │   Phase 7B                                    │
  │    │   MaterialPathCache ───────────────────────►   FSoftObjectPath (UMaterialInterface)
  │
  ├── obj.material_slots[1].material.name ── xxHash64 ───► FMaterialIdentityRef
  │    ...
  └── obj.material_slots[N].material.name ── xxHash64 ───► FMaterialIdentityRef
```

### 5.2 — Invariants

1. **One GUID per object, unique.** (Phase 7A, unchanged)
2. **One `FAssetIdentityRef` per mesh datablock.** (Phase 7A, unchanged)
3. **One `UStaticMeshComponent` per actor.** (Phase 7A, unchanged)
4. **One `UStaticMesh` per component.** (Phase 7A, unchanged)
5. **N material slots per `UStaticMeshComponent`.** (Phase 7B — new)
6. **One `FMaterialIdentityRef` per Blender material.** (Phase 7B — new)
7. **Slots are ordered.** Slot index in Blender = slot index in UE. (Phase 7B — new)
8. **Material identity is independent of mesh identity.** Changing a material name resends material data, not the asset def.

### 5.3 — Delta from Phase 7A

Phase 7B **extends** the identity model downward from the mesh datablock to its materials. The mesh-level path remains identical to Phase 7A. The material path is a new parallel lane with its own:
- Identity ref type (`FMaterialIdentityRef`)
- Packet type (reuse `PT_Material` 0x05 or define new)
- Metadata map (`MaterialMetadata`)
- Pending resolution queue (reuse `PendingAssetQueue` pattern or new)
- Path cache (`MaterialPathCache`)
- Assignment handler (`AssignMaterial`)
- Change detection (`_last_material_identity`)
- Diagnostics counters

---

## 6. Relationship to Phase 7C

### 6.1 — Deferred to Phase 7C

The following are explicitly deferred to Phase 7C (Geometry/Modifier Pipeline):

| Item | Reason |
|------|--------|
| Mesh content hashing (SHA256 of mesh geometry) | Asset identity uses datablock name, not content |
| Modifier detection (subdivision, mirror, etc.) | Geometry pipeline requires mesh content hashing first |
| FBX/glTF import pipeline | Asset discovery is out of scope for identity/mapping |
| Per-face material assignment | Phase 7B only covers per-slot assignment |
| Mesh LOD generation | Requires mesh content pipeline |
| UV/vertex color detection | Not needed for material slot mapping |

### 6.2 — Blocking Relationship

Phase 7C **may depend on** certain Phase 7B outputs:
- Material identity refs may be needed for per-face material baking
- Slot-count information may be needed for geometry export
- But Phase 7B does not block Phase 7C start

---

## 7. Identity Model Rules (Extended)

### 7.1 — FMaterialIdentityRef

```cpp
struct FMaterialIdentityRef
{
    uint64 High = 0;
    uint64 Low  = 0;

    // xxHash64 of Blender material name (slot.material.name).
    // WARNING: Renaming a material in Blender changes this identity.
    // Design choice: deterministic-by-name, not by content.
    // Content-based material identity requires material content hashing
    // and is deferred (may never be implemented — see Phase 7C).

    bool operator==(const FMaterialIdentityRef& Other) const;
    bool operator!=(const FMaterialIdentityRef& Other) const;
    bool IsValid() const;
};
```

### 7.2 — Material Identity Rules

| # | Rule | Rationale |
|---|------|-----------|
| M1 | Material identity = xxHash64 of Blender `slot.material.name` | Consistent with mesh identity approach |
| M2 | Empty slot = zero identity | No material assigned |
| M3 | Same material name = same identity | Deterministic cross-session |
| M4 | Material rename = new identity | Forces re-resolution (like mesh datablock rename) |
| M5 | Slot count change = full slot re-send | Slot reordering detected by index comparison |
| M6 | Slot index is stable | Slot 0 in Blender = slot 0 on `UStaticMeshComponent` |
| M7 | Max material slots = 8 (configurable, `MAX_MATERIAL_SLOTS`) | Prevents oversized packets |

### 7.3 — Wire Format (Proposed)

**PT_Material (0x05) — 46 bytes per object** (tentative):

| Field | Size | Description |
|-------|------|-------------|
| TargetGuid | 16 | The object GUID this material def applies to |
| SlotIndex | 1 | Material slot index (0–7) |
| MaterialHigh | 8 | xxHash64 high of material name |
| MaterialLow | 8 | xxHash64 low of material name |
| _padding_ | 1 | Reserved for future use (flags) |
| SequenceNumber | 4 | Replay sequence |
| Timestamp | 8 | Wall-clock timestamp |

**Alternative**: Batch packet (multi-slot per object):

| Field | Size | Description |
|-------|------|-------------|
| TargetGuid | 16 | The object GUID |
| SlotCount | 1 | Number of slots in this batch (1–8) |
| For each slot: | 17 | (SlotIndex(1) + MaterialHigh(8) + MaterialLow(8)) |

Batch size: 17 + (17 × N) bytes (34 bytes for single slot, 153 bytes for 8 slots).

---

## 8. Material Model Rules

| # | Rule | Rationale |
|---|------|-----------|
| 8.1 | Material assignment follows mesh assignment | Material cannot be set before mesh exists |
| 8.2 | Material packet with unknown GUID is enqueued, not rejected | Order-independence (like asset def) |
| 8.3 | Material with zero identity is skipped (counter incremented) | Consistent with `HandleAssetDef` |
| 8.4 | Material slot-out-of-range is rejected (counter incremented) | Safety: `MAX_MATERIAL_SLOTS` boundary |
| 8.5 | Material slot change detected = per-slot send | Diff-based, not full resend |
| 8.6 | Material-only edit in Blender does NOT resend asset identity | Decoupled lanes |
| 8.7 | Path cache for materials is separate from mesh path cache | Different `FSoftObjectPath` types |
| 8.8 | Material path resolution uses same retry/fallback pattern as mesh | Consistent UX |
| 8.9 | Fallback material = UE default material (no color-from-Blender) | Colors may require Phase 7C for material baking |

---

## 9. Implementation Plan

### Stage 0 — Audit & Documentation

| Step | Description | Deliverable |
|------|-------------|-------------|
| 0.1 | Write this scope lock document | `Docs/Architecture/44-phase7B-...md` |
| 0.2 | Audit existing `AssetPathCache` consumers: `HandleAssetDef`, `ResolvePendingAssets`, `CacheAssetPath` | Gap table in §3 |
| 0.3 | Audit existing `PT_Material` usage (currently unused) | Confirm no legacy handler |
| 0.4 | Verify `FSoftObjectPath` resolution pattern for materials | Research `UMaterialInterface` loading API |
| 0.5 | Audit Blender `obj.material_slots` API for extraction patterns | Research `bpy.types.MaterialSlot` API |

**Validation gate**: Documents only — zero source files modified.

### Stage 1 — Asset Registry + Material Pipeline

| Step | Description | Priority |
|------|-------------|----------|
| 1.1 | Define `FMaterialIdentityRef` in `AssetIdentityTypes.h` | High |
| 1.2 | Define `FMaterialMetadata` struct (identity, path, retry state, slot) | High |
| 1.3 | Define `FMaterialSlot` struct (slot index + identity) | High |
| 1.4 | Add `MaterialMetadata` map (`TMap<FGuid, TArray<FMaterialSlot>>`) | High |
| 1.5 | Add `MaterialPathCache` (`TMap<FMaterialIdentityRef, FSoftObjectPath>`) | High |
| 1.6 | Implement Blender material extraction in `sync.py` (`check_updates` or `_detect_*`) | High |
| 1.7 | Implement Blender material change detection + send | High |
| 1.8 | Implement `HandleMaterialDef` on UE side | High |
| 1.9 | Implement `ResolvePendingMaterials` on UE side | High |
| 1.10 | Implement `AssignMaterial` per slot on `UStaticMeshComponent` | High |
| 1.11 | Add tombstone gating for material assignment | High |
| 1.12 | Wrap `AssetPathCache` in `FAssetRegistry` structure with diagnostics | High |
| 1.13 | Add name-convention asset scanning fallback | Medium |
| 1.14 | Add material identity cache (`_last_material_identity`) in Blender | Medium |
| 1.15 | Add asset identity collision warning | High |
| 1.16 | Add registry diagnostic output (`UE.LiveSync.Registry.Dump`) | Medium |
| 1.17 | Add material diagnostic counters | Medium |
| 1.18 | Add validation tests for material identity, wire format, resolution | High |

**Validation gate**: Material appears on UE `StaticMeshComponent` matching Blender slots. Existing Phase 7A/6/5 suites pass.

### Stage 2 — Material Mapping Closeout

| Step | Description | Priority |
|------|-------------|----------|
| 2.1 | Add material snapshot replay (Begin/End packet wrap) | Medium |
| 2.2 | Add material ConsoleReset lifecycle | Medium |
| 2.3 | Add stale entry age-out for material metadata | Low |
| 2.4 | Add material path cache periodic refresh | Low |
| 2.5 | Add `CacheAssetPath` / `CacheMaterialPath` console commands | Low |
| 2.6 | Add material stress tests (batch/slot/rename/reconnect) | Medium |
| 2.7 | Add material-only edit regression tests | Medium |
| 2.8 | Full regression: all Phase 5D/6/6I.1/7A suites pass | High |

**Validation gate**: All prior suites pass. Material slots replicate correctly. No regressions.

---

## 10. Done Criteria

Phase 7B is **complete** when:

1. This scope lock document is finalised and merged ✅
2. All Stage 0/1/2 items are implemented and merged
3. `FMaterialIdentityRef` is defined and used for material identification
4. Blender material slots are extracted, change-detected, and sent to UE
5. UE receives material definitions, resolves paths, and assigns materials to correct slots on `UStaticMeshComponent`
6. `AssetPathCache` is wrapped in `FAssetRegistry` with collision detection and diagnostics
7. All identity model rules (§7) and material model rules (§8) are validated by automated tests
8. All prior Phase 5D/6/6I.1/7A validation suites pass with zero regressions
9. No new packet types beyond those documented in §7.3 unless proven necessary
10. No protocol version bump unless proven necessary
11. No Phase 7C (geometry pipeline) or Phase 7C material-baking work was started

---

## 11. Out of Scope

The following are **explicitly excluded** from Phase 7B:

| Item | Reason | Deferred To |
|------|--------|-------------|
| Mesh content hashing | Identity uses datablock name, not geometry | Phase 7C |
| Modifier detection / pipeline | Geometry pipeline, not material mapping | Phase 7C |
| Per-face material assignment | Requires mesh content analysis | Future |
| Mesh LOD generation | Requires mesh content pipeline | Future |
| UV / vertex color mapping | Mesh content attribute | Future |
| FBX / glTF import pipeline | External tooling, not UELiveSync core | Future |
| Material content hashing | Material identity is name-based | May never implement |
| Node-based material recreation | Blender node tree → UE material instance | Future (Phase 7D?) |
| Texture baking | GPU-intensive, not core sync | Future |
| Runtime material parameter sync | Color/roughness/metalness overrides | Future |

---

## 12. Risks

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| Blender material slot API changes across Blender versions | Low | Medium | Test against LTS Blender 3.6+ |
| Material name collisions produce same identity | Low | Medium | Log collision warning; user renames |
| `UStaticMeshComponent` slot count mismatch with Blender | Medium | Low | Clamp to `MAX_MATERIAL_SLOTS`, log |
| Material load fails (path not found) | Medium | Low | Use default UE material as fallback |
| PT_Material (0x05) conflicts with legacy use | Low | Low | Currently unused; confirm in audit |
| Material-only storm (rapid name changes) | Low | Medium | Throttle sends in Blender side |
| Wire format size with 8 slots (153 bytes per object) | Low | Low | Minor compared to transform (80 bytes) |

---

## 13. Files Touched (Estimated)

| File | Stage | What |
|------|-------|------|
| `UE_Plugin/.../Public/AssetIdentityTypes.h` | 1 | Add `FMaterialIdentityRef`, `FMaterialMetadata`, `FMaterialSlot`, `MAX_MATERIAL_SLOTS`, material diagnostic counters |
| `UE_Plugin/.../Public/UELiveSyncSubsystem.h` | 1 | Add `MaterialMetadata`, `MaterialPathCache`, `PendingMaterialQueue`, handler declarations |
| `UE_Plugin/.../Private/UELiveSyncSubsystem.cpp` | 1 | Implement `HandleMaterialDef`, `ResolvePendingMaterials`, `AssignMaterial`; wrap `AssetPathCache` |
| `Blender_Addon/sync.py` | 1 | Add material extraction, change detection, send in `check_updates`; add `_last_material_identity` |
| `Blender_Addon/network.py` | 1 | Add material serialization helpers; `serialize_material_slot()`, constants |
| `tests/` | 1, 2 | New test files for material identity, wire format, assignment, regression |
| `Docs/Architecture/44-phase7B-...md` | 0 | This document |

---

## 14. Glossary

| Term | Definition |
|------|------------|
| `FAssetRegistry` | Structured wrapper around `AssetPathCache` with collision detection, diagnostics, and refresh support |
| `FMaterialIdentityRef` | 16-byte POD: `{uint64 High, uint64 Low}` — xxHash64 of Blender material name |
| `FMaterialMetadata` | Per-slot metadata: material identity, resolved path, retry state, assignment flag |
| `FMaterialSlot` | Ordered pair: `{int8 SlotIndex, FMaterialIdentityRef Identity}` |
| `MaterialPathCache` | `TMap<FMaterialIdentityRef, FSoftObjectPath>` — material identity → `UMaterialInterface` path |
| `SlotIndex` | Integer index into `UStaticMeshComponent` material slot array (0–7) |
| `MAX_MATERIAL_SLOTS` | Upper bound on material slots per component (proposed: 8) |
| `_last_material_identity` | Blender per-GUID dict mapping slot index → cached material identity for change detection |

---

## 15. Stage 0 Audit Results

Completed 2026-05-31. Inspected: `AssetIdentityTypes.h`, `PendingAssetQueue.h`,
`UELiveSyncSubsystem.h`, `UELiveSyncSubsystem.cpp` (full file, 9862 lines),
`UELiveSyncSubsystem_Diagnostics.inl` (1136 lines), `SyncTypes.h` (1482 lines),
`Blender_Addon/sync.py` (1860 lines), `Blender_Addon/network.py` (1756 lines).

### 15.1 — Audit Table (25 items inspected)

#### Asset Registry Audit

| # | Rule / Responsibility | Current Location | Status | Gap | Stage |
|---|-----------------------|------------------|--------|-----|-------|
| AR1 | `AssetPathCache` has lifecycle management | `UELiveSyncSubsystem.h:627` | ❌ Gap | Flat `TMap<FAssetIdentityRef, FSoftObjectPath>` — never pruned, cleared, or refreshed. Survives `StopNetworkThread` and `ConsoleReset`. | 1 |
| AR2 | `AssetPathCache` auto-population | `UELiveSyncSubsystem.cpp:8289-8301` | ❌ Gap | `CacheAssetPath()` exists but is never called by any runtime path. Must be invoked manually or by console. | 1 |
| AR3 | Name-convention fallback resolution | — | ❌ Gap | No fallback: if cache miss, asset is unresolvable → fallback primitive after 5 retries. | 1 |
| AR4 | Multi-asset identity (LODs, variants) | — | ❌ Gap | One `FSoftObjectPath` per `FAssetIdentityRef`. No concept of LODs or variants. | 2 |
| AR5 | Identity collision detection | — | ❌ Gap | `TMap` silently overwrites on duplicate key. No collision warning or dedup logic. | 1 |
| AR6 | Registry diagnostics | `UELiveSyncSubsystem.cpp:9450-9480` | ◐ Partial | `UE.LiveSync.Stats` shows `PendingAssets` count but no per-entry detail. `UE.LiveSync.DumpState` does not include `AssetPathCache` entries. | 1 |
| AR7 | Asset lifecycle: `StopNetworkThread` preserves cache | `UELiveSyncSubsystem.cpp:2096-2119` | ✅ Intentional | Cache intentionally survives network restart so snapshot-rebuilt actors can resolve immediately. | — |
| AR8 | Asset lifecycle: `ConsoleReset` clears cache | `UELiveSyncSubsystem_Diagnostics.inl:790-799` | ❌ Gap | `ConsoleReset` resets _diagnostic counters_ but NOT `AssetMetadata`, `AssetPathCache`, or `PendingAssetQueue`. Stale entries persist forever. | 1 |
| AR9 | Asset lifecycle: `HandleEndSnapshot` does not touch cache | `UELiveSyncSubsystem.cpp:8356-8390` | ✅ Intentional | Snapshot rebuild relies on existing cache for quick mesh assignment. No flush needed. | — |
| AR10 | `FAssetMetadata.ResolvedPath` is set but never consumed | `AssetIdentityTypes.h:59`, `UELiveSyncSubsystem.cpp:8117` | ◐ Partial | `ResolvedPath` is stored in metadata but `AssignStaticMesh` uses `AssetPathCache.Find()` instead. Metadata path is written but unused — potential inconsistency. | 1 |

#### Material Mapping Audit

| # | Rule / Responsibility | Current Location | Status | Gap | Stage |
|---|-----------------------|------------------|--------|-----|-------|
| MM1 | `FMaterialIdentityRef` definition | — | ❌ Gap | Does not exist. No material identity type. | 1 |
| MM2 | `FMaterialMetadata` struct | — | ❌ Gap | Does not exist. No material metadata. | 1 |
| MM3 | Material slot data structure | — | ❌ Gap | Does not exist. No slot representation. | 1 |
| MM4 | Material identity hashing (xxHash64 of material name) | — | ❌ Gap | Not implemented in Blender or UE. | 1 |
| MM5 | Blender material slot extraction | `sync.py` (full file) | ❌ Gap | `sync.py` never accesses `obj.material_slots`, `slot.material`, or any material API. | 1 |
| MM6 | Blender material change detection | `sync.py` (full file) | ❌ Gap | `check_updates` tracks mesh identity (`_last_mesh_identity`) but not material changes. | 1 |
| MM7 | Blender material wire serialization | `network.py` (full file) | ❌ Gap | No material serialization functions exist. | 1 |
| MM8 | UE `HandleMaterialDef` handler | `UELiveSyncSubsystem.cpp` (full file) | ❌ Gap | Does not exist. No material handler function. | 1 |
| MM9 | UE `ResolvePendingMaterials` | `UELiveSyncSubsystem.cpp` (full file) | ❌ Gap | Does not exist. No material resolution. | 1 |
| MM10 | UE `AssignMaterial` per slot | `UELiveSyncSubsystem.cpp` (full file) | ❌ Gap | `AssignStaticMesh` and `AssignFallbackPrimitive` set mesh but never call `SetMaterial()` on component. | 1 |
| MM11 | Material path cache | — | ❌ Gap | No `MaterialPathCache` analogous to `AssetPathCache`. | 1 |
| MM12 | Material metadata map | — | ❌ Gap | No `MaterialMetadata` analogous to `AssetMetadata`. | 1 |
| MM13 | Material staleness / age-out | — | ❌ Gap | No material stale entry mechanism planned. | 2 |
| MM14 | Material snapshot replay | — | ❌ Gap | No Begin/End material snapshot. | 2 |
| MM15 | Material ConsoleReset lifecycle | — | ❌ Gap | No material state hooks in `ConsoleReset`. | 2 |

#### Protocol / Packet Availability Audit

| # | Item | Current Location | Status | Finding | Stage |
|---|------|------------------|--------|---------|-------|
| P1 | `PT_Material = 0x05` defined in `EPacketType` | `SyncTypes.h:211` | ⚠️ Unused | Enum value exists but has NO handler in any `.cpp` file. Not included in protocol FNV signature at `network.py:41`. Can be reused or replaced. | 1 |
| P2 | `PT_Mesh = 0x06` defined in `EPacketType` | `SyncTypes.h:212` | ⚠️ Unused | Enum value exists but has NO handler. Not included in protocol FNV signature. Available but likely more suitable for Phase 7C geometry streaming. | — |
| P3 | `PT_Reserved_02 = 0x02` | `SyncTypes.h:208` | ⚠️ Reserved | Legacy — was `PT_Hierarchy` in early Phase 3. Not in FNV signature. Available if `PT_Material` is insufficient. | — |
| P4 | Protocol FNV signature includes `0x05` and `0x06`? | `network.py:41` | ❌ Missing | `PT_Material (0x05)` and `PT_Mesh (0x06)` are NOT in the FNV signature. Adding material handler requires signature update AND both-side sync. | 1 |
| P5 | `PT_AssetDef` payload size constant | `AssetIdentityTypes.h:125-127` | ✅ Defined | `ASSET_DEF_OBJECT_SIZE = 33`. Used consistently in parser. | — |
| P6 | Wire format range: 0x00–0x0F used | `SyncTypes.h:205-234` | ◐ 12/16 used | 0x01, 0x03, 0x04, 0x07, 0x08, 0x09, 0x0A, 0x0B, 0x0C, 0x0D, 0x0E, 0x0F = 12 assigned. 0x02 (reserved-legacy), 0x05 (PT_Material-unused), 0x06 (PT_Mesh-unused), 0x00 (invalid) = 4 available. | 1 |

### 15.2 — Recommended Stage 1 Implementation Order

Based on audit findings, Stage 1 should proceed in this order:

| Priority | Item | Rationale | Dependencies |
|----------|------|-----------|--------------|
| 1 | Define `FMaterialIdentityRef` (struct + hash) | Foundation type for all material work | None |
| 2 | Define `FMaterialMetadata` and `FMaterialSlot` | Data structures for material state | 1 |
| 3 | Add material identity hashing to Blender (`network.py`) | `xxHash64` of `slot.material.name` | None (standalone) |
| 4 | Implement Blender material slot extraction (`sync.py`) | Read `obj.material_slots` → emit material identity per slot | 3 |
| 5 | Implement Blender material change detection (`sync.py`) | Add `_last_material_identity` dict, diff logic | 4 |
| 6 | Implement Blender material wire send (`network.py`) | Serialize material slots into `PT_Material` packets | 3, 5 |
| 7 | Add `PT_Material` to FNV protocol signature (both sides) | Required before packet acceptance | 6 |
| 8 | Implement UE `HandleMaterialDef` (`UELiveSyncSubsystem.cpp`) | Receive, validate, store material metadata | 2, 7 |
| 9 | Add `MaterialPathCache` and `MaterialMetadata` maps to header | Data store for material state | 2 |
| 10 | Implement UE `ResolvePendingMaterials` | Load `UMaterialInterface` by path with retry/fallback | 9 |
| 11 | Implement UE `AssignMaterial` per slot | Call `SetMaterial(SlotIndex, Material)` on `UStaticMeshComponent` | 10 |
| 12 | Fix `ConsoleReset` to clear `AssetMetadata`/`AssetPathCache`/`PendingAssetQueue` | Prevent unbounded stale entry accumulation | None |
| 13 | Add asset identity collision detection / warning | Log warning when duplicate identity key added to `AssetPathCache` | None |
| 14 | Add `AssetPathCache` entries to `DumpState` output | Allow developers to inspect cached paths | None |
| 15 | Write validation tests for all of the above | Ensure material pipeline + registry changes pass | 1–14 |

### 15.3 — Test Coverage Gaps (Beyond Phase 7A)

| # | Missing Test | Priority | Stage |
|---|-------------|----------|-------|
| TG1 | `AssetPathCache` collision detection (duplicate identity) | High | 1 |
| TG2 | `ConsoleReset` clears `AssetMetadata` + `AssetPathCache` + `PendingAssetQueue` | High | 1 |
| TG3 | `CacheAssetPath` round-trip (add + find) | Low | 1 |
| TG4 | `FAssetMetadata.ResolvedPath` consistency with `AssetPathCache` | Medium | 1 |
| TG5 | `FMaterialIdentityRef` equality, hashing, `IsValid` | High | 1 |
| TG6 | Blender material slot extraction (0, 1, multiple slots) | High | 1 |
| TG7 | Blender material slot change detection (add, remove, rename slot) | High | 1 |
| TG8 | Material wire format serialization/deserialization | High | 1 |
| TG9 | UE `HandleMaterialDef` tombstone gating | High | 1 |
| TG10 | UE `AssignMaterial` per slot on `UStaticMeshComponent` | High | 1 |
| TG11 | Material resolution retry + fallback | Medium | 1 |
| TG12 | Protocol signature validation for `PT_Material` | Medium | 1 |
| TG13 | `StopNetworkThread` does NOT clear asset cache (regression) | Low | 1 |
| TG14 | Material-only edit in Blender does not re-send `PT_AssetDef` | Medium | 2 |
| TG15 | Material snapshot replay (Begin/End) | Low | 2 |
| TG16 | `ConsoleReset` material lifecycle hook | Low | 2 |

### 15.4 — Files Changed During Audit

**None.** Stage 0 is audit-only; zero source files modified.
