# Phase 7A — Static Mesh Identity Mapping Scope Lock

> **Created**: 2026-05-30
> **Status**: PLANNING — scope/design only, not yet implemented
> **Predecessors**: Phase 6I.1 Transport Hardening COMPLETE ✅ — `56ee468`
> **Next**: Stage 0 audit + docs
>
> This document defines the **identity model** for mapping Blender
> objects and mesh datablocks to Unreal Engine actors, static mesh
> components, and static mesh assets. Phase 7A formalises the
> existing Phase 5D asset-identity plumbing but does **not** add
> geometry streaming, material mapping, or a modifier pipeline.

---

## 1. Purpose

Phase 5D implemented the core identity wire protocol:
`PT_AssetDef = 0x08`, `FAssetIdentityRef` (xxHash64),
`PendingAssetQueue`, and `ResolvePendingAssets`. This identity
layer has been stable through Phase 6 visibility, rename,
hierarchy, collection, and transport hardening — but it has
never been formally documented as a complete identity model.

Phase 7A is the **first Phase 7 sub-slice**. It does *not* add
new payloads or protocol changes. Instead, it formalises the
existing identity mapping rules, adds missing edge-case coverage,
and documents known gaps for Phase 7B (materials) and Phase 7C
(geometry).

### Why Formalise Identity Before Geometry/Materials

| Risk | Without formal identity | With formal identity |
|------|----------------------|---------------------|
| Shared mesh datablock identity | Implicit — relies on xxHash64 of `obj.data.name` | Explicit rule: same `obj.data` → same `FAssetIdentityRef` → same `UStaticMesh` |
| Mesh datablock rename | Changes identity silently; UE re-resolves path | Documented as identity-change trigger; new `PT_AssetDef` expected |
| Duplicate detection via GUID collision | `ensure_unique_guid()` handles it | Documented rule: duplicates share mesh identity, differ in object identity |
| Delete/recreate identity chain | Delete V5 + CREATE preserves asset identity | Documented: `PT_Delete_V5` removes actor + metadata; `PT_Create` starts fresh; `PT_AssetDef` re-resolves |

Phase 7B and 7C depend on a stable identity foundation. Without
this document, material overrides (7B) and geometry streaming (7C)
would risk inconsistent asset lookups and path resolution.

---

## 2. Title

**Phase 7A — Static Mesh Identity Mapping**

Phase 7A formalises the identity model for the five identity
layers (Blender object, Blender mesh datablock, UE actor,
UE StaticMesh asset, UE StaticMeshComponent) and defines the
rules covering shared datablocks, rename, delete/recreate, and
duplicate detection. It adds **no new packet types, no protocol
version bump, no runtime code changes**, and **no Phase 7B/7C
payloads**.

---

## 3. Current Identity Model

### 3.1 — Blender Object Identity

| Property | Value |
|----------|-------|
| **Storage** | `obj["ue_guid"]` — bpy `IDProperty` string |
| **Format** | UUID hex (`uuid.uuid4().hex`), 32 chars |
| **Assignment** | `ensure_unique_guid()` in `sync.py:390–425` |
| **Collision resolution** | Detects existing GUID → regenerates |
| **Load persistence** | `_reconcile_guids_on_load()` in `sync.py:428–493` — compares stored `ue_guid_owner_hash` against current `_compute_owner_hash(obj)`; if stale, regenerates GUID |
| **Owner hash input** | `obj.data.name` (mesh datablock name), **not** `obj.name` |
| **Change tracking** | `_last_mesh_identity` dict — keyed by GUID → `(identity_low, identity_high, mesh_name)` in `sync.py:136` |
| **Iteration** | `scan_scene()` iterates `bpy.data.objects`, filters `obj.type == 'MESH'` |
| **Stale cleanup** | `ReferenceError` on deleted objects → removed from `tracked_objects` |

### 3.2 — Blender Mesh Datablock Identity

| Property | Value |
|----------|-------|
| **Hash algorithm** | xxHash64 of `obj.data.name` (Python str) |
| **Output** | `(uint64 Low, uint64 High, uint8 PrimitiveType)` |
| **Implementation** | `get_mesh_identity_hash(obj)` in `network.py:185–210` |
| **Wire format** | `serialize_asset_identity()` in `network.py:213–233` — 33 bytes |
| **Change trigger** | Emitted on first send **or** when `_last_mesh_identity[guid]` differs from current hash (checked in `sync.py:1159–1189`) |
| **Limitation** | Hash covers **name only**, not mesh geometry content. Renaming a mesh datablock changes the identity. Editing geometry (without rename) does **not** change the identity hash. |

### 3.3 — UE Actor Identity

| Property | Value |
|----------|-------|
| **Tag format** | `LiveSync_GUID=<FGuid.ToString(EGuidFormats::Digits)>` |
| **Tag assignment** | `HandleCreateObject()` at `UELiveSyncSubsystem.cpp:6163–6174` |
| **Cache** | `TMap<FGuid, TWeakObjectPtr<AActor>> ActorCache` — `UELiveSyncSubsystem.h:437–441` |
| **Cache rebuild** | `BuildActorCache()` — scans all actors via `TActorIterator<AActor>`, parses `LiveSync_GUID=` tags |
| **Lookup** | `FindActorFast(Guid)` — `O(1)` map lookup, returns `nullptr` if not found or stale |
| **Reverse lookup** | `FindGuidForActor(Actor)` — scans actor tags for `LiveSync_GUID=` prefix |
| **Stale removal** | `OnActorDestroyed()` removes from `ActorCache` and `TransformStates` |
| **Spawn event** | `OnActorSpawned()` calls `TryCacheActor()` for auto-registration |

### 3.4 — UE StaticMesh Asset Identity

| Property | Value |
|----------|-------|
| **Key** | `FAssetIdentityRef` — 16-byte POD: `{uint64 High, uint64 Low}` |
| **Value** | `FSoftObjectPath` — deferred-loadable asset path |
| **Cache** | `TMap<FAssetIdentityRef, FSoftObjectPath> AssetPathCache` |
| **Population** | Manual via `CacheAssetPath()` — **empty by default** |
| **Resolution** | `ResolvePendingAssets()` → `AssignStaticMesh()` — loads via `Path.TryLoad()`, casts to `UStaticMesh*` |
| **Fallback** | `AssignFallbackPrimitive()` — uses `UELiveSyncSubsystem::GetPrimitiveMesh(uint8 PrimitiveType)` which resolves built-in shapes (Sphere/Cylinder/Plane/Cube) |
| **Retry** | Up to 5 attempts with exponential backoff (1s → 2s → 4s → 8s → 16s) |
| **Metadata** | `TMap<FGuid, FAssetMetadata> AssetMetadata` — per-GUID: identity, resolved path, retry state, fallback flag |

### 3.5 — UE StaticMeshComponent Identity

| Property | Value |
|----------|-------|
| **Creation** | `HandleCreateObject()` at `UELiveSyncSubsystem.cpp:6281–6330` via `NewObject<UStaticMeshComponent>(NewActor)` |
| **Mesh assignment** | `MeshComp->SetStaticMesh(GetPrimitiveMesh(PrimitiveType))` — defaults to fallback shape |
| **Root component** | `NewActor->SetRootComponent(MeshComp)` — Single root, always mesh component |
| **Mobility** | `SetMobility(EComponentMobility::Movable)` |
| **Collision** | `SetCollisionEnabled(ECollisionEnabled::NoCollision)` |
| **Registration** | `MeshComp->RegisterComponent()` |
| **Re-assignment** | `AssignStaticMesh()` in `ResolvePendingAssets()` — finds component via `FindComponentByClass<UStaticMeshComponent>()` on the target actor; calls `SetStaticMesh(Mesh)` |
| **Fallback re-assignment** | `AssignFallbackPrimitive()` — same pattern, also creates component if missing |

---

## 4. Existing Protocol Data Flow

```
Blender (sync.py)                    UE (UELiveSyncSubsystem.cpp)
─────────────────                    ────────────────────────────
scan_scene()                         
  └─ ensure_unique_guid(obj)         
  └─ tracked_objects[guid] = (obj, type)

check_updates() (per tick)           
  └─ get_mesh_identity_hash(obj)     
  └─ if changed from _last_mesh_identity[guid]:
       └─ serialize_asset_identity() 
            → PT_AssetDef (0x08) packet
                                     ProcessBinaryPacket()
                                       └─ PacketType == PT_AssetDef
                                       └─ for each of ObjectCount×33B:
                                             Guid(16) + IdentityHigh(8)
                                             + IdentityLow(8)
                                             + PrimitiveFallback(1)
                                       └─ HandleAssetDef(Guid, Hi, Lo, Prim)
                                            └─ Update FAssetMetadata
                                            └─ Enqueue Guid in PendingAssetQueue

                                     Tick() → ResolvePendingAssets()
                                       └─ Dequeue up to 8 per tick
                                       └─ Lookup AssetPathCache[Identity]
                                       └─ If found: AssignStaticMesh(Actor, Path)
                                       └─ If not found: AssignFallbackPrimitive(Actor, PrimitiveType)
```

### PT_AssetDef Wire Format

```
Offset  Size  Field
────────────────────────────
  0      16   FGuid (object GUID, LE)
 16       8   IdentityLow (uint64 LE, xxHash64 low)
 24       8   IdentityHigh (uint64 LE, xxHash64 high)
 32       1   PrimitiveFallback (uint8)
 ────   ────
          33  Total (LIVE_SYNC_V5_ASSET_DEF_SIZE)
```

---

## 5. Shared Mesh Datablock / Instancing Rules

**Rule 1**: Two Blender objects with the same `obj.data` reference
produce identical `FAssetIdentityRef` values (same xxHash64 of
`obj.data.name` → same `High` + `Low`). UE resolves both to the
same `FSoftObjectPath` in `AssetPathCache`. A single `UStaticMesh`
load serves all instances.

**Rule 2**: Each instance still gets its own `UStaticMeshComponent`
and `AActor`. Identity is per-object (GUID), not per-datablock.
The `StaticMesh` pointer is shared across components (UE reference
counting manages lifetime).

**Rule 3**: Renaming a shared mesh datablock changes the identity
hash for ALL objects sharing it. A `PT_AssetDef` is emitted for
each affected GUID. Each UE instance re-resolves independently.

**Rule 4**: Per-object material overrides are not tracked
(Phase 7B). All instances of a shared datablock display the
same material until Phase 7B adds per-instance material slots.

---

## 6. Rename / Delete / Recreate Rules

### Object Rename (GUID stays the same)

| Step | Action | Packet | UE Effect |
|------|--------|--------|-----------|
| 1 | Blender detects `obj.name != _last_object_names[guid]` | `PT_Rename` | Actor label updated, GUID unchanged |
| 2 | Mesh identity unchanged (same `obj.data`) | (none) | `AssetMetadata` unchanged, `StaticMesh` unchanged |

### Mesh Datablock Rename (identity changes)

| Step | Action | Packet | UE Effect |
|------|--------|--------|-----------|
| 1 | Blender detects `get_mesh_identity_hash() != _last_mesh_identity[guid]` | `PT_AssetDef` | `FAssetMetadata.Identity` updated, re-enqueued in `PendingAssetQueue` |
| 2 | UE resolves new identity | (resolve) | `AssignStaticMesh()` sets new `UStaticMesh` on the component |
| 3 | Old identity removed | — | Not removed (may be shared by other actors) |

### Delete + Recreate (GUID changes)

| Step | Action | Packet | UE Effect |
|------|--------|--------|-----------|
| 1 | Blender object deleted | `PT_Delete_V5` | Actor destroyed, `ActorCache` entry removed, `AssetMetadata` removed |
| 2 | Blender new object (or undo+redo) | `PT_Create` | New actor spawned, new GUID assigned, tagged |
| 3 | Mesh identity send | `PT_AssetDef` | New `FAssetMetadata` created, resolved anew |

---

## 7. Duplicate Detection Rules

**Rule 1** (`Shift+D` in Blender): `ensure_unique_guid()` assigns a
new GUID to the duplicate object. The duplicate shares the original's
mesh datablock (`obj.data`), so `FAssetIdentityRef` is identical.

**Rule 2** (Load duplicate): `_reconcile_guids_on_load()` detects
stale `ue_guid_owner_hash` (computed from `obj.data.name`) and
regenerates GUIDs if the owner hash has changed. Two objects sharing
a datablock that was renamed will both get fresh GUIDs.

**Rule 3** (GUID collision): If two objects somehow end up with the
same GUID, `ensure_unique_guid()` detects the collision and
regenerates. This is a safety net, not a normal flow.

---

## 8. Known Gaps

| # | Gap | Impact | Target |
|---|-----|--------|--------|
| 1 | **Mesh identity is name-hash only** — xxHash64 covers `obj.data.name`, not mesh geometry. Editing geometry without renaming the datablock produces no identity change. | UE does not know the mesh changed. No re-import triggered. | Phase 7C |
| 2 | **`AssetPathCache` empty by default** — no automatic discovery of UE `StaticMesh` assets matching Blender datablock names. User must manually call `CacheAssetPath()` or rely on fallback primitives. | New Blender objects always get fallback shapes (Sphere/Cylinder/Plane/Cube) until paths are cached. | Phase 7B |
| 3 | **No mesh versioning** — no monotonically increasing content version on `FAssetMetadata`. UE cannot tell if a previously resolved mesh is stale. | Re-import workflows have no staleness signal. | Phase 7C |
| 4 | **One `UStaticMeshComponent` per actor** — no support for multi-component actors, component hierarchy, or component selection. | Complex objects requiring multiple meshes have no representation. | Future work |
| 5 | **No material overrides per instance** — materials are baked into the `UStaticMesh` asset or fallback primitive. | Different instances of the same mesh datablock must all use the same material. | Phase 7B |
| 6 | **No `MalformedPackets` counter on truncated `PT_AssetDef`** — the truncation check at `UELiveSyncSubsystem.cpp:2697` is silent (no counter increment). | `MalformedPackets` under-reports truncated asset-def payloads. | Stage 1 |

---

## 9. Implementation Plan

### Stage 0 — Audit & Documentation (no runtime code) ✅ VERIFIED

| Step | Description |
|------|-------------|
| 0.1 | Write this scope lock document |
| 0.2 | Audit all existing Phase 5D identity tests (`phase5d_validation_A_asset_identity.py`) for coverage against rules in §§5–7 |
| 0.3 | Document all `FAssetIdentityRef` consumers: creation, comparison, hashing, storage |
| 0.4 | Verify `AssetMetadata` age-out rules (60s stale timeout) against delete/recreate identity chain |

**Validation gate**: Stage 0 produces documents and test gap report only — zero source files modified.

### Stage 1A — Identity Hygiene Fixes ✅ VERIFIED (2026-05-31)

| Step | Description |
|------|-------------|
| 1A.C1 | `HandleDelete` (V5) now cleans `AssetMetadata` + `PendingAssetQueue` — `UELiveSyncSubsystem.cpp:7528-7532` |
| 1A.C1b | `OnActorDestroyed` cleans `AssetMetadata` + `PendingAssetQueue` — `UELiveSyncSubsystem.cpp:5353-5355` |
| 1A.C2 | Truncated `PT_AssetDef` payload path increments `MalformedPackets` — `UELiveSyncSubsystem.cpp:2772` |
| 1A.C3 | `_last_mesh_identity` cleared in Blender `start_sync()` / `stop_sync()` — `sync.py:1737,1746,1843,1845` |

**Validation gate**: 578/578 standalone tests PASS, 0 regressions. See §15.3 for gap items deferred to Stage 1B.

### Stage 1B — Identity Coverage Hardening ✅ VERIFIED (2026-05-31)

| Step | Section | Tests | Description |
|------|---------|-------|-------------|
| 1B.1 | §7 | 14 | Shared datablock identity: two objs sharing `obj.data` → same `FAssetIdentityRef`, distinct GUIDs; null data; non-MESH; xxHash64 cross-impl validation |
| 1B.2 | §8 | 13 | Mesh datablock rename: renaming `obj.data` changes identity hash, GUID unchanged; owner hash diverges; multi-object; empty/unicode/long names |
| 1B.3 | §9 | 10 | Duplicate object: inherits GUID → collision detected → new GUID; shared mesh identity; multi-duplicate; different mesh divergence |
| 1B.4 | §10 | 17 | Delete/recreate chain: metadata clean on delete; new GUID on recreate; identity re-emission; path cache survival; multi-cycle |
| 1B.5 | §11 | 23 | `FAssetIdentityRef` semantics: equality/inequality; hash stability; dict/set; `is_valid`; `to_tuple`; max uint64; path cache simulation |

**Validation gate**: 77/77 Stage 1B standalone tests PASS. All prior suites pass. Zero regressions.

### Stage 2 — Identity Hygiene (PENDING)

| Step | Description |
|------|-------------|
| 2.1 | Add `AssetMetadata` periodic age-out verification test (stale entries evicted after `ASSET_STALE_TIMEOUT = 60.0s`) |
| 2.2 | Verify `PendingAssetQueue` bounds (2048 max) and overflow behavior — add test |
| 2.3 | Add identity-mapping section to `UE.LiveSync.Stats` console output if not already present (verify `AssetDefsReceived`, `AssetAssignmentsSucceeded`, `AssetLookupsFailed` counters) |
| 2.4 | Full regression: run all Phase 5D/6/6I.1 validation suites |

**Validation gate**: All prior suites pass. Identity counters are visible in stats. No regressions.

---

## 10. Done Criteria

Phase 7A is **complete** when:

1. This scope lock document is finalised and merged
2. All Stage 0/1A/1B/2 items are implemented and merged
3. `MalformedPackets` increments on truncated `PT_AssetDef` paths (✅ Stage 1A)
4. Identity model rules (§§5–7) are validated by automated tests (✅ Stage 1B: 77/77 tests, 5 rules covered)
5. All prior Phase 5D/6/6I.1 validation suites pass with zero regressions (✅ 655/655 PASS as of Stage 1B)
6. No new packet types, no protocol version bump, no runtime code changes outside the scope of §9
7. No Phase 7B (material mapping) or Phase 7C (geometry pipeline) work was started

---

## 11. OUT OF SCOPE

| Item | Rationale |
|------|-----------|
| **Geometry streaming (FBX push, auto-reimport)** | Requires FBX export, threading, and asset pipeline integration. Phase 7C territory. |
| **Material mapping** | Per-instance material overrides, material slots, material assets. Phase 7B territory. |
| **Modifier pipeline** | Blender modifiers → UE equivalent. Phase 7C territory. |
| **Content-addressed mesh hashing** | Replacing xxHash64 of name with hash of mesh geometry. Phase 7C+. |
| **Asset registry implementation** | Automatic discovery of UE `StaticMesh` assets matching Blender datablock names. Described in Phase 7B scope lock. |
| **Protocol version bump** | `PT_AssetDef = 0x08` wire format is unchanged. No V6 header needed. |
| **New packet types** | No `PT_*` additions. Phase 7A reuses existing `PT_AssetDef`. |
| **Multi-component actors** | One `UStaticMeshComponent` per actor remains. Component hierarchy selection is future work. |
| **Procedural mesh / runtime geometry** | `UProceduralMeshComponent` is not used. Phase 7C may evaluate it. |
| **Sequencer / animation sync** | Original Phase 7 plan (Animation & Sequencer Sync) is replanned to Phase 8+. |
| **TLS / encryption** | Out of scope for localhost editor sync. |
| **Concurrent connections / multi-client** | Single-connection model unchanged. |

---

## 12. Risks

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Identity hash (name-only) misses geometry changes | High | Medium | Documented gap for Phase 7C. Users must re-import manually for geometry-only edits. |
| `AssetPathCache` empty on fresh project | High | Low | Fallback primitives provide visual feedback. Phase 7B adds registry for automatic discovery. |
| Shared datablock rename triggers N asset-def packets | Medium | Low | Each re-resolves independently. Bounded by `MAX_OBJECTS_PER_PACKET = 4096` and per-tick resolve limit (8/tick). |
| `ensure_unique_guid` fails under rapid create/delete | Low | Medium | Collision detection + regeneration is robust. UUID hex space is collision-resistant. |
| `AssetMetadata` stale entries accumulate | Low | Low | 60s TTL eviction + `PendingAssetQueue` 2048 cap. Stage 2 adds test coverage. |
| Phase 7A delays Phase 7B/7C start | Low | Low | Stage 0 is audit-only (zero source). Stage 1 is 1–2 days. Stage 2 is 1–2 days. |

---

## 13. Files Touched

| File | Stage | What |
|------|-------|------|
| `UE_Plugin/.../UELiveSyncSubsystem.cpp` | 1A | `MalformedPackets` add for truncated `PT_AssetDef` (C2); `AssetMetadata.Remove` + `PendingAssetQueue.Remove` in `HandleDelete` (C1) and `OnActorDestroyed` (C1b) |
| `Blender_Addon/sync.py` | 1A | `_last_mesh_identity` global + clear in `start_sync()` / `stop_sync()` (C3) |
| `tests/phase5d_validation_A_asset_identity.py` | 0, 1A | Audit + truncated/zero-length `PT_AssetDef` wire tests (C2) |
| `tests/phase6e_delete_validation.py` | 1A | §49 AssetMetadata cleanup on delete (C1) — 12 tests |
| `tests/phase7a_hygiene_validation.py` | 1A | New test file — 40 tests covering C1/C1b/C2/C3 |
| `Docs/Architecture/43-phase7A-static-mesh-identity-scope-lock.md` | 0 | This document |

---

## 14. Glossary

| Term | Definition |
|------|------------|
| `FAssetIdentityRef` | 16-byte POD: `{uint64 High, uint64 Low}` — xxHash64 of Blender `obj.data.name` |
| `FAssetMetadata` | Per-GUID metadata: identity, resolved path, retry count, fallback flag, staleness time |
| `AssetPathCache` | Global identity → `FSoftObjectPath` map for resolving asset identity to loadable paths |
| `PendingAssetQueue` | 2048-entry bounded FIFO of GUIDs awaiting asset resolution |
| `ResolvePendingAssets()` | Per-tick function that dequeues up to 8 GUIDs, looks up `AssetPathCache`, and calls `AssignStaticMesh` |
| `ensure_unique_guid()` | Blender-side GUID assignment with collision detection and regeneration |
| `_reconcile_guids_on_load()` | Blender-side load-time GUID validation using owner hash (`obj.data.name` based) |

---

## 15. Stage 0 Audit Results

Completed 2026-05-31. Inspected: `sync.py`, `network.py`, `AssetIdentityTypes.h`,
`PendingAssetQueue.h`, `UELiveSyncSubsystem.h`, `UELiveSyncSubsystem.cpp`,
`phase5d_validation_A_asset_identity.py`, `phase6g_identity_stability.py`,
`phase6e_delete_validation.py`, `phase6_rename_validation.py`,
`phase6_visibility_validation.py`, `phase6d_hierarchy_validation.py`.

### 15.1 — Audit Table (24 rules inspected)

| # | Rule | Status | Gap | Stage |
|---|------|--------|-----|-------|
| 1 | `obj["ue_guid"]` storage | ✅ Tested | — | — |
| 2 | GUID collision detection/regeneration | ✅ Tested | — | — |
| 3 | Load-time reconcile via `ue_guid_owner_hash` | ✅ Tested | — | — |
| 4 | Owner hash excludes `obj.name` | ✅ Tested | — | — |
| 5 | Mesh identity = xxHash64 of `obj.data.name` | ◐ Partial | No test for two objs sharing same datablock | 1 |
| 6 | PT_AssetDef on first send or identity change | ◐ Partial | Mesh datablock rename not tested | 1 |
| 7 | `_last_mesh_identity` lifecycle | ❌ Gap | **Not cleared in `start_sync()`/`stop_sync()`** — stale across cycles | 1 |
| 8 | PT_AssetDef wire format (33 bytes) | ✅ Tested | — | — |
| 9 | HandleAssetDef stores metadata + enqueues | ◐ Partial | No enqueue-verification test | 1 |
| 10 | ResolvePendingAssets retry/backoff | ◐ Partial | Timing not assertable standalone | — |
| 11 | AssetPathCache (identity → path) | ❌ Gap | Empty by default (Phase 7B) | 7B |
| 12 | AssignStaticMesh end-to-end | ❌ Gap | Requires UE editor integration | — |
| 13 | HandleCreateObject sets LiveSync_GUID= tag | ✅ Tested | — | — |
| 14 | ActorCache / FindActorFast O(1) | ◐ Partial | No direct cache-hit test | — |
| 15 | BuildActorCache scan + parse tags | ❌ Gap | No rebuild-correctness test | 1 |
| 16 | HandleDeleteObject cleans AssetMetadata | ✅ Tested (Blender-side only) | — | — |
| 17 | **HandleDelete (V5) cleans AssetMetadata** | **❌ CRITICAL** | **Does NOT call `AssetMetadata.Remove` or `PendingAssetQueue.Remove`** | 1 |
| 18 | OnActorDestroyed cleans AssetMetadata | ❌ Gap | Stale metadata accumulates on external destroy | 1 |
| 19 | PendingAssetQueue.CleanupStale() | ❌ No-op | `CleanupStale()` body is empty | 2 |
| 20 | PT_AssetDef truncation → MalformedPackets | ❌ Gap | Silent return at L2769, no counter increment | 1 |
| 21 | Delete/recreate identity chain | ❌ Gap | No integrated delete→create→assetdef chain test | 1 |
| 22 | Shift+D duplicate → new GUID | ✅ Tested | — | — |
| 23 | GUID collision safety net | ✅ Tested | — | — |
| 24 | FAssetIdentityRef comparison/hashing | ❌ Gap | No unit test for ==, !=, GetTypeHash | 1 |

### 15.2 — Critical Issues Found

**C1. `HandleDelete` V5 path skips `AssetMetadata` cleanup**
- Location: `UELiveSyncSubsystem.cpp:7418-7547`
- Impact: Metadata + pending queue entries survive V5 delete; stale entries may cause unexpected re-resolution
- Fix: Add `AssetMetadata.Remove(Guid)` + `PendingAssetQueue.Remove(Guid)` after tombstone insertion

**C2. `PT_AssetDef` truncated payload silent return**
- Location: `UELiveSyncSubsystem.cpp:2769-2772`
- Impact: `MalformedPackets` under-reports truncated asset-def payloads (scope doc §8 #6)
- Fix: Add `Stats.MalformedPackets.fetch_add(1, std::memory_order_relaxed)` before `return`

**C3. `_last_mesh_identity` not cleared on start/stop**
- Location: `sync.py:1723-1860`
- Impact: After stop→start, stale identity cache may suppress first-tick PT_AssetDef emission
- Fix: Add `_last_mesh_identity.clear()` to both `start_sync()` and `stop_sync()`

### 15.3 — Test Coverage Gaps (Stage 1B Resolved)

| # | Test | Priority | Status |
|---|------|----------|--------|
| G1 | Shared datablock → identical `FAssetIdentityRef` (two objects, same `obj.data`) | High | ✅ Stage 1B §7 |
| G2 | Mesh datablock rename → new identity hash effects | High | ✅ Stage 1B §8 |
| G3 | Delete/recreate chain: V5 delete → CREATE → PT_AssetDef → resolve | High | ✅ Stage 1B §10 |
| G4 | `HandleDelete` V5 `AssetMetadata` cleanup | Critical | ✅ Stage 1A |
| G5 | `OnActorDestroyed` `AssetMetadata` cleanup | Medium | ✅ Stage 1A |
| G6 | PT_AssetDef truncation `MalformedPackets` counter | High | ✅ Stage 1A |
| G7 | `FAssetIdentityRef` comparison/hashing | Low | ✅ Stage 1B §11 |
| G8 | `PendingAssetQueue.CleanupStale()` no-op | Low | 🕐 Stage 2 |

### 15.4 — Files Changed

| File | Stage | What |
|------|-------|------|
| `UE_Plugin/.../UELiveSyncSubsystem.cpp` | 1A | `MalformedPackets` add; `AssetMetadata.Remove` in `HandleDelete` + `OnActorDestroyed` |
| `Blender_Addon/sync.py` | 1A | `_last_mesh_identity` global + clear in start/stop |
| `tests/phase7a_hygiene_validation.py` | 1A, 1B | Stage 1A (40 tests) + Stage 1B (77 tests = §7–11 covering all 5 identity rules) |
| `tests/phase5d_validation_A_asset_identity.py` | 1A | Truncated/zero-length PT_AssetDef wire tests |
| `tests/phase6e_delete_validation.py` | 1A | §49 AssetMetadata cleanup on delete (12 tests) |
| `STATUS.md` | 1B | Updated validation table to 655/655 PASS |
| `Docs/Architecture/43-phase7A-static-mesh-identity-scope-lock.md` | 0, 1A, 1B | This document |
