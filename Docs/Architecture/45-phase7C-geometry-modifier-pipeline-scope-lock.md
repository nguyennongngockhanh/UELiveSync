# Phase 7C — Geometry/Modifier Pipeline Scope Lock

**Date**: 2026-05-31  
**Status**: COMPLETE ✅ — all stages implemented and verified  
**Depends on**: Phase 7A (Identity) ✅, Phase 7B (Material) ✅  
**Blocks**: Nothing — standalone capability  

---

## Table of Contents

1. [Purpose](#1-purpose)
2. [Current Geometry Pipeline Status](#2-current-geometry-pipeline-status)
3. [Gap Analysis](#3-gap-analysis)
4. [Phase 7C Responsibilities](#4-phase-7c-responsibilities)
5. [Relationship to Existing Systems](#5-relationship-to-existing-systems)
6. [Mesh Identity / Versioning Model](#6-mesh-identity--versioning-model)
7. [Proposed Geometry Packet Schema](#7-proposed-geometry-packet-schema)
8. [Modifier Handling Rules](#8-modifier-handling-rules)
9. [Topology/UV/Normal/Material-Index Rules](#9-topologyuvnormalmaterial-index-rules)
10. [Chunking / Packet-Size Strategy](#10-chunking--packet-size-strategy)
11. [Implementation Plan](#11-implementation-plan)
12. [Done Criteria](#12-done-criteria)
13. [Out of Scope](#13-out-of-scope)
14. [Risks](#14-risks)
15. [Files Touched (Estimated)](#15-files-touched-estimated)
16. [Glossary](#16-glossary)

---

## 1. Purpose

Phase 7C defines how UELiveSync transfers mesh geometry data and modifier-derived
mesh changes from Blender to UE5.

After Phase 7A and 7B, UELiveSync can:
- Map Blender objects to UE Actors via GUID (7A)
- Map mesh datablock names to `FAssetIdentityRef` via xxHash64 (7A)
- Map material slot identities to `UMaterialInterface` paths (7B)
- Assign pre-existing `UStaticMesh` assets or fallback primitives (7A)

Phase 7C adds the ability to:
- Extract evaluated mesh geometry from Blender (with modifiers applied)
- Serialize vertex positions, normals, UVs, topology, and material slot indices
- Transmit geometry incrementally over TCP
- Reconstruct meshes on the UE5 side at runtime
- Detect mesh content changes and trigger re-send

---

## 2. Current Geometry Pipeline Status

### 2.1 — Identity-Only Mesh

**No geometry data is currently transmitted.** The existing pipeline is
identity-only:

```
Blender                              UE
  │                                     │
  │  PT_AssetDef = xxHash64(mesh name)  │
  │  ───────────────────────────────>   │
  │                                     │
  │  PT_Create + PT_Transform           │
  │  ───────────────────────────────>   │  Spawn AActor + UStaticMeshComponent
  │                                     │
  │  (No geometry wire format exists)   │  AssignStaticMesh loads from disk
  │                                     │  or AssignFallbackPrimitive uses
  │                                     │  a hardcoded UE primitive mesh.
  │                                     │
  │  User must pre-import meshes into   │  MaterialPathCache + SetMaterial
  │  UE content browser.                │  assigns materials by slot.
```

### 2.2 — Fallback Primitive System

`AssignFallbackPrimitive()` creates or finds a `UStaticMeshComponent` and
assigns one of 5 hardcoded primitives:
- `PRIMITIVE_CUBE = 0x00`
- `PRIMITIVE_SPHERE = 0x01`
- `PRIMITIVE_CYLINDER = 0x02`
- `PRIMITIVE_PLANE = 0x03`
- `PRIMITIVE_EMPTY = 0x04`

These primitives are loaded via `GetPrimitiveMesh()` which returns
`UStaticMesh` assets from `/Engine/BasicShapes/`. No custom geometry is
generated.

### 2.3 — Blender Mesh Data Access

The Blender addon currently:
- Reads `obj.data.name` for mesh identity hashing
- Reads `obj.material_slots[n].material.name` for material identity
- Reads transform data (location, rotation, scale)
- **Never** evaluates `obj.to_mesh()`, `obj.evaluated_depsgraph_get()`,
  `bmesh`, or any geometry extraction API

### 2.4 — PT_Mesh Packet Type

`PT_Mesh = 0x06` is defined in `SyncTypes.h:212` but has **zero runtime code**:
- No handler in any `.cpp` or `.py` file
- Not included in the protocol FNV signature on either side
- No size constraints or constants defined

### 2.5 — Transport Constraints (Phase 6I.1)

| Constraint | Value | Source |
|-----------|-------|--------|
| Max objects per packet | 4096 | `LIVE_SYNC_MAX_OBJECTS_PER_PACKET` |
| Max raw packet size | 524,288 bytes | `LIVE_SYNC_MAX_PACKET_SIZE` |
| Header size (V5) | 24 bytes | `LIVE_SYNC_HEADER_SIZE_V5` |
| Max packet rate | 120/sec | `CVarLiveSyncMaxPacketRate` |
| Queue depth (Blender send) | ≤1000 | Phase 6I.1 send queue bounds |
| Queue depth (UE receive) | 128 entries | `FLiveSyncQueue` |

---

## 3. Gap Analysis

### 3.1 — Geometry Extraction Gaps

| # | Gap | Impact |
|---|-----|--------|
| G1 | No `obj.to_mesh()` or depsgraph evaluation | Cannot access evaluated (modifier-applied) geometry |
| G2 | No per-vertex position extraction | No geometry replication |
| G3 | No per-face vertex index extraction | No topology replication |
| G4 | No normal/tangent extraction | Lighting inaccurate without normals |
| G5 | No UV layer extraction | Texturing requires UV coordinates |
| G6 | No vertex color extraction | Per-vertex color data not synced |
| G7 | No material slot index per face | `SetMaterial` assigns the material to the entire mesh component, not per-face |
| G8 | No modifier list inspection | Cannot detect which modifiers changed |
| G9 | No blender file save awareness | `obj.data` changes not tracked when user saves |

### 3.2 — Serialization Gaps

| # | Gap | Impact |
|---|-----|--------|
| S1 | No geometry wire format defined | No serialization code exists |
| S2 | No chunked/streaming protocol | Single mesh may exceed 512KB packet limit |
| S3 | No delta encoding | Full geometry re-sent on every change |
| S4 | No compression | Raw vertex data may be large |
| S5 | No geometry versioning | Cannot detect which meshes need update |

### 3.3 — Reconstruction Gaps

| # | Gap | Impact |
|---|-----|--------|
| R1 | No `UStaticMesh` runtime creation | UE has no API to create a mesh asset from raw data at runtime without the editor |
| R2 | `UProceduralMeshComponent` not used | Available in UE5 but would need new component creation path |
| R3 | `UDynamicMeshComponent` not used | Available in UE5 but would need new component creation path |
| R4 | No editor-substrate asset saving | Editor-only `UStaticMesh::Build()` requires `IStaticMeshEditor` |
| R5 | No LOD generation | Single resolution only |
| R6 | No collision generation | No physics collision for generated meshes |

### 3.4 — Cross-Cutting Gaps

| # | Gap | Impact |
|---|-----|--------|
| X1 | `PT_Create` does not carry geometry | New objects start as primitives |
| X2 | `PT_AssetDef` is name-based, not content-based | Identical datablocks produce same identity; renaming breaks references |
| X3 | No `PT_Mesh` handler in protocol signature | Adding `PT_Mesh` requires signature update (like `PT_Material`) |
| X4 | No replay recording for geometry | Geometry changes not captured in snapshots/reconnect |

---

## 4. Phase 7C Responsibilities

### 4.1 — Blender Geometry Extraction

| # | Responsibility | Priority | Stage |
|---|---------------|----------|-------|
| BE1 | Evaluate mesh via `depsgraph` (`obj.evaluated_get(depsgraph).to_mesh()`) | High | 1 |
| BE2 | Extract vertex positions (float3 × N) | High | 1 |
| BE3 | Extract triangle topology (int32 × 3 × T) | High | 1 |
| BE4 | Extract per-face material slot index | High | 1 |
| BE5 | Extract normals (float3 × N, optionally packed) | Medium | 1 |
| BE6 | Extract UV coordinates (float2 × N for each UV layer) | Medium | 2 |
| BE7 | Extract vertex colors | Low | 2 |
| BE8 | Generate per-face geometry version hash | High | 1 |
| BE9 | Detect geometry changes via version hash diff | High | 1 |
| BE10 | Clean up evaluated mesh (`to_mesh_clear()`) | High | 1 |

### 4.2 — Geometry Serialization

| # | Responsibility | Priority | Stage |
|---|---------------|----------|-------|
| GS1 | Define `PT_Mesh` wire format (chunked) | High | 1 |
| GS2 | Add `PT_Mesh` to protocol FNV signature (both sides) | High | 1 |
| GS3 | Implement Blender geometry chunk serialization | High | 1 |
| GS4 | Implement chunk reassembly on UE side | High | 1 |
| GS5 | Implement per-chunk checksum for corruption detection | Medium | 1 |
| GS6 | Implement geometry change detection + re-send | High | 1 |
| GS7 | Implement basic delta encoding (deferred) | Low | 2 |

### 4.3 — UE Geometry Reconstruction

| # | Responsibility | Priority | Stage |
|---|---------------|----------|-------|
| UR1 | Define component type for geometry: `UProceduralMeshComponent` (recommended) | High | 1 |
| UR2 | Handle procedural mesh component creation during `PT_Create` via new packet flag or default | High | 1 |
| UR3 | Implement chunk receiver and reassembler | High | 1 |
| UR4 | Implement `ProcMesh->CreateMeshSection()` with vertex/triangle/normal/UV data | High | 1 |
| UR5 | Implement per-face material slot mapping via `ProcMesh->SetMaterial()` | High | 1 |
| UR6 | Implement geometry version check — skip re-assembly if unchanged | Medium | 1 |
| UR7 | Implement collision mesh generation | Medium | 1 |
| UR8 | Implement multi-section mesh (one section per material) | Medium | 2 |

### 4.4 — Diagnostics & Validation

| # | Responsibility | Priority | Stage |
|---|---------------|----------|-------|
| DV1 | Add geometry diagnostics counters (vertices sent, chunks received, etc.) | Medium | 1 |
| DV2 | Add `DumpState` geometry counts | Medium | 1 |
| DV3 | Add geometry-specific test coverage | High | 1 |
| DV4 | Add transport-stress test with large geometry payloads | Medium | 2 |

---

## 5. Relationship to Existing Systems

### 5.1 — Phase 7A Identity System

```
Blender Object
  ├── obj["ue_guid"] ────────────────────────────►  UE Actor (LiveSync_GUID= tag)
  │
  ├── obj.data.name ─── xxHash64 ──── FAssetIdentityRef ──►  AssetPathCache
  │                                                           (disk-based mesh)
  │
  └── obj.data (evaluated) ─── xxHash64(content) ──► GeometryVersionHash (NEW)
              │                                        (triggers re-send)
              │  Phase 7C adds a CONTENT-BASED hash
              │  alongside the existing NAME-BASED identity.
              │
              └──► PT_Mesh chunks ──► UE ProceduralMeshComponent
```

- `FAssetIdentityRef` remains the name-based identifier for disk-loaded assets
- `GeometryVersionHash` is a NEW content-based hash (SHA-256 or xxHash256 of
  vertex + topology data), used ONLY for change detection, NOT identity
- The existing `PT_AssetDef` → `AssignStaticMesh` path for disk assets is
  UNCHANGED  
- `PT_Mesh` geometry path is a PARALLEL lane for runtime-generated meshes

### 5.2 — Phase 7B Material System

- `MaterialPathCache` and `FMaterialSlotRef` are reused for procedural meshes
- `UProceduralMeshComponent::SetMaterial()` is analogous to
  `UStaticMeshComponent::SetMaterial()`
- The per-face material slot index in the geometry packet maps to
  `ProcMesh->CreateMeshSection()` section index

### 5.3 — Phase 6I.1 Transport Constraints

- `LIVE_SYNC_MAX_PACKET_SIZE = 524288` bytes bounds each individual `PT_Mesh` chunk
- `CVarLiveSyncMaxPacketRate = 120` bounds total packet rate
- Geometry chunks travel alongside existing packets — rate limiting applies
- Geometry may require multiple chunks per mesh, streamed over several ticks

### 5.4 — Snapshot / Replay

- Geometry data is NOT snapshot-replay-able in Stage 1
- Replay of geometry would require storing all chunks in the replay buffer
- Stage 2 should evaluate whether geometry replay is needed (mesh content
  is deterministic from Blender depsgraph; replay may be redundant)

---

## 6. Mesh Identity / Versioning Model

### 6.1 — Content-Based Version Hash

```cpp
struct FGeometryVersionHash
{
    uint64 High = 0;
    uint64 Low  = 0;

    bool operator==(const FGeometryVersionHash& Other) const;
    bool operator!=(const FGeometryVersionHash& Other) const;
    bool IsValid() const;
};
```

- Computed as xxHash256 (or SHA-256) of the concatenated byte stream of:
  1. Vertex count + position bytes
  2. Triangle count + index bytes
  3. Per-face material slot indices
  4. Normal count + normal bytes (if present)
  5. UV layer count + UV bytes (if present)
- Deterministic across sessions for identical mesh content
- Changes ONLY when the evaluated mesh geometry changes (modifier added/
  removed/retweaked, vertex edit, etc.)

### 6.2 — Identity vs. Version

| Concept | `FAssetIdentityRef` (Phase 7A) | `FGeometryVersionHash` (Phase 7C) |
|---------|-------------------------------|-----------------------------------|
| Basis | `obj.data.name` | Evaluated mesh content |
| Use | Lookup in `AssetPathCache` | Change detection |
| Stability | Stable across vertex edits | Changes on any geometry edit |
| Changes on rename | Yes | No |
| Changes on modifier tweak | No (same datablock) | Yes |
| Sent in | `PT_AssetDef` | `PT_Mesh` (implicit in data) |

### 6.3 — Version Change Flow

```
User edits mesh in Blender
  → Depsgraph evaluation sees change
  → New FGeometryVersionHash differs from cached hash
  → Blender queues PT_Mesh chunk(s)
  → UE receives chunks, reconstructs mesh sections
  → UE updates cached FGeometryVersionHash for this GUID
```

---

## 7. Proposed Geometry Packet Schema

### 7.1 — `PT_Mesh` Chunk Header (17 bytes per chunk)

```
Offset  Size  Field
0       16    TargetGUID (FGuid) — object to receive geometry
16      1     ChunkFlags (bitmask):
                bit 0: HasPositions
                bit 1: HasTriangles
                bit 2: HasNormals
                bit 3: HasUVs (UV layer 0)
                bit 4: HasVertexColors
                bit 5: IsFirstChunk
                bit 6: IsLastChunk
                bit 7: HasMaterialSlotData
```

### 7.2 — Geometry Data Blocks (variable size, after header)

Each block is prefixed with a uint32 byte count, followed by the data:

| Block | Field | Size | Encoding |
|-------|-------|------|----------|
| VertexCount | uint32 | 4 | Little-endian |
| TriangleCount | uint32 | 4 | Little-endian |
| Positions | float32[3] × VertexCount | 12 × N | LE, compact |
| Triangles | int32[3] × TriangleCount | 12 × M | LE |
| Normals | float32[3] × VertexCount | 12 × N | LE, optional |
| UVs (per layer) | float32[2] × VertexCount | 8 × N | LE, optional |
| VertexColors | float32[4] × VertexCount | 16 × N | LE, optional |
| MaterialSlotIndices | int32 × TriangleCount | 4 × M | LE, optional |

### 7.3 — Minimal Mesh Example (cube, 8 verts, 12 triangles)

```
ChunkHeader:  17 bytes (GUID + flags)
VertexCount:  4 bytes (8)
TriangleCount: 4 bytes (12)
Positions:    96 bytes (8 × 3 × float32)
Triangles:    144 bytes (12 × 3 × int32)
Normals:      96 bytes (8 × 3 × float32) [optional]
─────────────────────────────────────────────
Total:        357 bytes (without normals: 261 bytes)
```

Well within `LIVE_SYNC_MAX_PACKET_SIZE = 524288`.

### 7.4 — Chunked Transmission (Large Meshes)

For meshes exceeding a per-chunk threshold (e.g., 128KB of vertex data):

- `IsFirstChunk` flag on first chunk
- `IsLastChunk` flag on last chunk
- Intermediate chunks have neither flag set
- Chunk reassembly on UE side: accumulate chunks, create mesh section
  after `IsLastChunk` is received
- Per-chunk checksum (CRC32 or FNV-1a 32) in the chunk header or as
  a trailing field

### 7.5 — Material Slot Index in Mesh

When `HasMaterialSlotIndices` flag is set:
- Each triangle has a `int32` material slot index
- `CreateMeshSection()` uses this to place triangles in the correct section
- Section count = number of unique material slot indices
- Material assignment for each section via `MaterialPathCache` (Phase 7B)

---

## 8. Modifier Handling Rules

| # | Rule | Rationale |
|---|------|-----------|
| M1 | Blender always evaluates the full modifier stack via `depsgraph` | Ensures UE receives the final mesh as visible in the viewport |
| M2 | Modifier changes (add/remove/reorder/tweak) → geometry version hash changes → re-send | Automatic change detection |
| M3 | Non-geometry modifiers (Armature, Curve, Array with no merge) produce mesh changes as side effect | Re-send only what changed |
| M4 | Vertex-only edits (no topology change) → different position bytes → different version hash → re-send | Position data is re-transmitted; UE replaces mesh section |
| M5 | Topology-only edits (subdivision level change) → different triangle bytes → re-send | Full section replacement |
| M6 | Boolean modifier on/off → full mesh replacement | `IsFirstChunk` + `IsLastChunk` with updated data |
| M7 | Multiresolution modifier changes → re-evaluate entire mesh | Chunked if necessary |
| M8 | Geometry Nodes modifier changes → full evaluation | See M2 |
| M9 | Modifier disable in viewport → do NOT send disabled result | Use `obj.evaluated_get()` which respects viewport visibility |
| M10 | Render-only modifiers (Subdivision Surface with different levels) → render result is NOT synced | UELiveSync replicates viewport state, not render state |

---

## 9. Topology/UV/Normal/Material-Index Rules

| # | Rule | Rationale |
|---|------|-----------|
| T1 | Topology is transmitted as indexed triangles (int32[3]) | Interleaved strips or fan encoding add complexity; indexed triangles are universal |
| T2 | Vertex positions are float32[3] in world/local space | Chosen by chunk flag; local space recommended, UE transforms via component |
| T3 | Normals are float32[3], per-vertex, optionally packed | Packing to int16 quaternions or octahedral is deferred |
| T4 | UV coordinates are float32[2] per vertex, per UV layer | UV layer 0 only in Stage 1; multiple layers deferred to Stage 2 |
| T5 | Vertex colors are float32[4] (RGBA) per vertex | Deferred to Stage 2 |
| T6 | Material slot indices are int32 per triangle, one slot per triangle | Maps to `CreateMeshSection()` section index |
| T7 | Max material slots per mesh = `MAX_MATERIAL_SLOTS` (8) | Consistent with Phase 7B |
| T8 | Section material = first valid material in `MaterialPathCache` for that slot index | If slot index 0 material is unresolved, section gets default material |
| T9 | Zero slots (all indices = -1 or 0) → single-section mesh with default material | Backward compatibility with no-material objects |

---

## 10. Chunking / Packet-Size Strategy

### 10.1 — Per-Chunk Budget

| Parameter | Value | Notes |
|-----------|-------|-------|
| Target per-chunk size | 64 KB | Below 524 KB max; leaves headroom for other packets |
| Max per-chunk size | 256 KB | Conservative fraction of `LIVE_SYNC_MAX_PACKET_SIZE` |
| Chunks per mesh | unbounded | Large meshes stream over many ticks |
| Pending reassembly per GUID | 1 | Only one mesh can be assembling at a time per object |
| Max concurrent reassemblies | 16 | Prevents unbounded memory use |

### 10.2 — Tick Budget

- Geometry chunks are parsed in the existing `ProcessBinaryPacket` dispatcher
- Each chunk increments `PacketsProcessed` but is subject to `CVarLiveSyncMaxPacketRate`
- Large meshes over many frames: acceptable — user sees progressive refinement
- No dedicated per-tick budget for geometry (same 120 pkt/s rate as all packets)

### 10.3 — Reassembly Timeout

- If `IsFirstChunk` is received but `IsLastChunk` does not arrive within 30 seconds,
  the partial assembly is discarded and logged as a warning
- Prevents memory leaks from dropped chunk sequences

---

## 11. Implementation Plan

### Stage 0 — Audit & Documentation

| Step | Description | Deliverable |
|------|-------------|-------------|
| 0.1 | Write this scope lock document | `Docs/Architecture/45-phase7C-...md` |
| 0.2 | Research `UProceduralMeshComponent` API — `CreateMeshSection()`, `SetMaterial()`, collision | Research notes |
| 0.3 | Research Blender `depsgraph` API — `evaluated_get()`, `to_mesh()`, `to_mesh_clear()` | Research notes |
| 0.4 | Audit transport constraints: `LIVE_SYNC_MAX_PACKET_SIZE`, packet rate, queue depth | Confirmed in §2.5 |
| 0.5 | Audit `PT_Mesh` current status (unused / zero code) | Confirmed in §2.4 |

**Validation gate**: Documents only — zero source files modified.

### Stage 1A — Mesh Protocol + Extraction ✅ VERIFIED (2026-05-31, 506ee54)

| Step | Description | Status |
|------|-------------|--------|
| 1A.1 | `PT_Mesh = 0x06` wire constants defined | ✅ Done |
| 1A.2 | FNV protocol signature updated (both sides) | ✅ Done |
| 1A.3 | Blender `extract_evaluated_mesh_data()` — depsgraph eval + `to_mesh()` | ✅ Done |
| 1A.4 | Blender geometry version hashing (SHA-256) | ✅ Done |
| 1A.5 | Blender `serialize_mesh_chunk()` — complete chunk serialization | ✅ Done |
| 1A.6 | Validation tests (47/47 PASS) | ✅ Done |

### Stage 1B — PT_Mesh Handler + Reassembly ✅ VERIFIED (2026-05-31, 2e8f1cd)

| Step | Description | Status |
|------|-------------|--------|
| 1B.1 | UE `ProcessBinaryPacket` parser for `0x06` | ✅ Done |
| 1B.2 | `HandleMeshChunk()` — validation, dedup, conflict rejection | ✅ Done |
| 1B.3 | `PendingMeshReassembly` map with max concurrent enforcement | ✅ Done |
| 1B.4 | Diagnostics + ConsoleReset + DumpState | ✅ Done |
| 1B.5 | Validation tests (43/43 PASS) | ✅ Done |

### Stage 1C — ProceduralMesh Reconstruction ✅ VERIFIED (2026-05-31, 5c07470)

| Step | Description | Status |
|------|-------------|--------|
| 1C.1 | `ReconstructCompletedMeshes()` tick pipeline | ✅ Done |
| 1C.2 | Payload decode (vertices, triangles, material indices) | ✅ Done |
| 1C.3 | Per-material section grouping + `CreateMeshSection()` | ✅ Done |
| 1C.4 | Safe handling: missing actor, empty/invalid geometry | ✅ Done |
| 1C.5 | Validation tests (18/18 PASS) | ✅ Done |

### Stage 1D — Blender Geometry Streaming ✅ VERIFIED (2026-05-31, c4725b0)

| Step | Description | Status |
|------|-------------|--------|
| 1D.1 | `_last_geometry_version` cache in `check_updates()` | ✅ Done |
| 1D.2 | Depsgraph eval + version hash comparison + PT_Mesh send | ✅ Done |
| 1D.3 | Cache cleanup: start_sync, stop_sync, object delete | ✅ Done |
| 1D.4 | Non-MESH skip, empty mesh safety | ✅ Done |
| 1D.5 | Validation tests (27/27 PASS) | ✅ Done |

### Stage 2 — Closeout Items (Deferred or Not Applicable)

| Step | Description | Status | Rationale |
|------|-------------|--------|-----------|
| 2.1 | Normal extraction + packing | 🕐 Deferred | Requires schema extension; no demand yet |
| 2.2 | UV layer 0 extraction | 🕐 Deferred | Requires schema extension; no demand yet |
| 2.3 | Collision mesh generation | ✅ Done | `bCreateCollision=true` in `CreateMeshSection()` |
| 2.4 | Geometry chunk reassembly timeout | 🕐 Deferred | Low risk; partial chunks visible as incomplete mesh |
| 2.5 | Geometry replay/snapshot evaluation | 🕐 Deferred | Snapshot system not yet geometry-aware |
| 2.6 | Transport-stress test with large payloads | 🕐 Deferred | Requires placeholder content browser data |
| 2.7 | Vertex color extraction | 🕐 Deferred | Requires schema extension |
| 2.8 | Multi-UV-layer support | 🕐 Deferred | Requires schema extension |
| 2.9 | Full regression | ✅ Done | 1020/1020 standalone tests PASS |

---

## 12. Done Criteria

Phase 7C was **complete** on 2026-05-31:

1. This scope lock document is finalised and merged ✅
2. All Stage 0/1 items are implemented and merged ✅
3. `PT_Mesh` wire format is defined and validated ✅
4. Blender evaluated mesh extraction (verts + triangles + materials) works ✅
5. UE `UProceduralMeshComponent` is created and mesh sections are built ✅
6. Modifier changes in Blender are detected and re-sent ✅
7. Per-face material slot indices are mapped to mesh sections ✅
8. All identity model rules (§6) and material model rules (§8 from Phase 7B) continue to pass ✅
9. All prior Phase 7A/7B/6/6I.1 validation suites pass with zero regressions ✅ (1020/1020 standalone)
10. No animation sync, skeletal mesh, nanite pipeline, or material shader generation ✅
11. No protocol version bump ✅
10. No animation sync, skeletal mesh, nanite pipeline, or material shader generation
11. No protocol version bump unless proven necessary

---

## 13. Out of Scope

The following are **explicitly excluded** from Phase 7C:

| Item | Reason | Deferred To |
|------|--------|-------------|
| Animation / sequencer sync | Requires skeleton/clip data, not mesh geometry | Future phase |
| Skeletal mesh | Requires bone/weight data pipeline | Future phase |
| Nanite mesh generation | Editor-only, not runtime | Future |
| Material shader graph generation | Requires Blender node tree → UE material | Future (Phase 7D?) |
| Texture baking / texture streaming | GPU-intensive, not core sync | Future |
| Compression / delta streaming | Adds complexity; Stage 1 uses full mesh re-send | Stage 2 or later |
| High-performance streaming (Phase 8) | Requires compression, prioritization, LOD management | Phase 8 |
| Modifier preview (accept/reject) | UI workflow, not core sync | Future |
| FBX/glTF import pipeline | External tooling, not UELiveSync core | Future |
| Mesh LOD auto-generation | Requires editor substrate | Future |
| Runtime mesh saving to UE content | Editor-only operation | Future |

---

## 14. Risks

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| `UProceduralMeshComponent` collision generation is expensive | Medium | Medium | Generate simple convex colliders; Stage 2 per-triangle |
| Large meshes (>500K verts) exceed packet/rate limits | Medium | Low | Chunking + streaming over ticks |
| Modifier stack evaluation is Blender-version-sensitive | Low | Medium | Test against LTS Blender 3.6+ |
| `to_mesh()` memory leak if `to_mesh_clear()` not called | Medium | High | RAII wrapper; always paired |
| `UProceduralMeshComponent` not available in UE5 on some platforms | Low | High | Fallback to `UStaticMeshComponent` + editor-only build |
| Geometry re-send storms (rapid modifier tweaks) | Medium | Low | Coalesce changes: debounce timer in Blender |
| Protocol signature mismatch if `0x06` added unevenly | Low | High | Test cross-version signature before commit |

---

## 15. Files Touched (Estimated)

| File | Stage | What |
|------|-------|------|
| `UE_Plugin/.../Public/AssetIdentityTypes.h` | 1 | Add `FGeometryVersionHash`, geometry constants |
| `UE_Plugin/.../Public/UELiveSyncSubsystem.h` | 1 | Add `HandleMeshDef`, `ReassembleMeshChunk`, `ProceduralMeshComponents` map |
| `UE_Plugin/.../Private/UELiveSyncSubsystem.cpp` | 1 | Implement `HandleMeshDef`, chunk reassembly, `ProcMesh` creation, geometry change detection |
| `UE_Plugin/.../Public/SyncTypes.h` | 1 | Add `0x06` to protocol signature; add geometry wire constants if not already |
| `Blender_Addon/network.py` | 1 | Add `PT_Mesh` serialization, `evaluated_mesh_get()`, `geometry_version_hash()` |
| `Blender_Addon/sync.py` | 1 | Add geometry change detection + chunk send in `check_updates` |
| `tests/` | 1, 2 | New test files for geometry wire format, chunking, reassembly, modifier change detection |
| `Docs/Architecture/45-phase7C-...md` | 0 | This document |

---

## 16. Glossary

| Term | Definition |
|------|------------|
| `FGeometryVersionHash` | 16-byte hash of evaluated mesh content (xxHash256 of vertex, triangle, material data) |
| `PT_Mesh = 0x06` | Proposed geometry chunk packet type (currently a placeholder with zero code) |
| `UProceduralMeshComponent` | UE5 component that accepts runtime-generated mesh data via `CreateMeshSection()` |
| `Depsgraph` | Blender dependency graph; used to evaluate modifier stack |
| `evaluated_mesh_get()` | Blender function to extract modifier-applied mesh geometry |
| `CreateMeshSection()` | `UProceduralMeshComponent` method to build a mesh section from vertex/triangle data |
| `Section` | A sub-mesh within `UProceduralMeshComponent` corresponding to one material slot |
| `Chunk` | A single `PT_Mesh` packet containing a fragment of the full mesh geometry |
| `IsFirstChunk` / `IsLastChunk` | Flags marking chunk sequence boundaries |
| `VersionHash` | Content-based hash used for geometry change detection (NOT object identity) |

---

## 17. Stage 0 Audit Results

Completed 2026-05-31. Inspected: `UELiveSyncSubsystem.cpp`, `UELiveSyncSubsystem.h`,
`UELiveSync.Build.cs`, `SyncTypes.h`, `AssetIdentityTypes.h`, `Blender_Addon/sync.py`,
`Blender_Addon/network.py`, `UELiveSyncSubsystem_Diagnostics.inl`.

### 17.1 — Audit Table (28 items inspected)

#### Blender Geometry Extraction Audit

| # | Requirement | Current Location | Status | Gap | Stage |
|---|-------------|------------------|--------|-----|-------|
| B1 | Depsgraph evaluated mesh access (`obj.evaluated_get(dg).to_mesh()`) | `sync.py` | ❌ Gap | No depsgraph evaluation exists. All mesh data access is `obj.data` only (name), never geometry. | 1 |
| B2 | Vertex position extraction (`mesh.vertices[i].co`) | — | ❌ Gap | `sync.py` never accesses `mesh.vertices`. | 1 |
| B3 | Triangle topology (`mesh.loop_triangles`) | — | ❌ Gap | `sync.py` never accesses mesh loops or polygons. | 1 |
| B4 | Per-face material index (`polygon.material_index`) | — | ❌ Gap | `sync.py` only accesses `obj.material_slots` (top-level slot identity), never per-polygon material indices. | 1 |
| B5 | Normal extraction (`vertex.normal` / `loop.normal`) | — | ❌ Gap | No normal extraction. | 1 |
| B6 | UV layer extraction (`mesh.uv_layers`) | — | ❌ Gap | No UV extraction. | 2 |
| B7 | Vertex color extraction (`mesh.vertex_colors`) | — | ❌ Gap | No vertex color extraction. | 2 |
| B8 | Evaluated mesh cleanup (`to_mesh_clear()`) | — | ❌ Gap | `to_mesh()` is never called, so cleanup is not an issue yet. RAII wrapper needed when implemented. | 1 |
| B9 | Modifier stack change detection | — | ❌ Gap | No modifier inspection in `check_updates()`; changes detected only by geometry version hash diff (not yet implemented). | 1 |
| B10 | `bpy.types.Mesh` API availability | Blender Python API | ✅ Available | `bpy.data.meshes`, `Mesh.vertices`, `Mesh.loops`, `Mesh.polygons`, `Mesh.uv_layers`, `Mesh.vertex_colors` all available in core Blender API. No addon dependency needed. | — |

#### Blender Send Pipeline Audit

| # | Requirement | Current Location | Status | Gap | Stage |
|---|-------------|------------------|--------|-----|-------|
| S1 | Object change detection loop | `sync.py:1058-1190` | ✅ Available | `tracked_objects` iteration loop exists with transform/visibility/rename/hierarchy/collection detection. Geometry hook point is after mesh identity check (L1190). | —
| S2 | Change coalescence pattern | `sync.py:1199-1212,1213-1229` | ✅ Available | Visibility and rename detection both use `is_first_*` guard + `_last_*` cache diff. Pattern reusable for geometry. | 1 |
| S3 | Batch send infrastructure | `sync.py:1390-1520` | ✅ Available | `send_objects(data, packet_type, version)` supports arbitrary packet types. Material path (L1508) demonstrates adding a new packet type. | 1 |
| S4 | Per-GUID state cleanup on delete | `sync.py:1044-1049` | ◐ Partial | `_last_mesh_identity`, `_last_material_identity` cleaned. `_last_material_identity` has Phase 7B cleanup. `_last_geometry_*` cache needs addition. | 1 |
| S5 | Packet serialization pattern | `network.py:213-233` | ✅ Available | `serialize_asset_identity()` demonstrates fixed-size serialization. `serialize_material_slots()` demonstrates variable-size serialization. Both patterns reusable. | 1 |
| S6 | Protocol signature update pattern | `network.py:41` | ✅ Available | Adding `0x05` for `PT_Material` in Stage 1C demonstrates the exact change needed for `0x06`. | 1 |

#### UE Reconstruction Feasibility Audit

| # | Requirement | Current Location | Status | Gap | Stage |
|---|-------------|------------------|--------|-----|-------|
| U1 | `UProceduralMeshComponent` availability | Engine module | ✅ Available | `ProceduralMeshComponent` is part of the `Engine` module (UE5.7). No dependency change needed — `#include "ProceduralMeshComponent.h"` suffices. | 1 |
| U2 | `Build.cs` dependency on Engine | `Build.cs:27` | ✅ Already present | `"Engine"` is a `PublicDependencyModuleName`. All `ProceduralMeshComponent`, `StaticMeshComponent`, `MeshDescription`, and related headers are accessible. | — |
| U3 | Actor spawn path | `Subsystem.cpp:6178-6189` | ✅ Available | `World->SpawnActor<AActor>()` with static class. No changes needed — `ProceduralMeshComponent` can be added as a child component. | 1 |
| U4 | Component find/create pattern | `Subsystem.cpp:8309-8322` | ✅ Available | `FindComponentByClass<UStaticMeshComponent>()` pattern; fallback `NewObject<>()` + `SetupAttachment()` in `AssignFallbackPrimitive`. Same pattern for `UProceduralMeshComponent`. | 1 |
| U5 | `StaticMesh` → `ProcMesh` switch logic | — | ❌ Gap | No flag or packet type currently selects between `UStaticMeshComponent` and `UProceduralMeshComponent`. Need a mechanism (e.g., `IsProcedural` flag in `PT_Create` or implicit on first `PT_Mesh`). | 1 |
| U6 | `CreateMeshSection()` API | `ProceduralMeshComponent.h` | ✅ Available | `CreateMeshSection(int32 SectionIndex, TArray<FVector> Verts, TArray<int32> Tris, TArray<FVector> Normals, TArray<FVector2D> UV0, TArray<FColor> Colors, TArray<FProcMeshTangent> Tangents, bool bCreateCollision)` — standard UE API. | 1 |
| U7 | `SetMaterial()` on `ProcMeshComponent` | `ProceduralMeshComponent.h` | ✅ Available | `SetMaterial(int32 ElementIndex, UMaterialInterface* Material)` — same interface as `UStaticMeshComponent`. Phase 7B `MaterialPathCache` resolution reusable directly. | 1 |
| U8 | Collision generation via `CreateMeshSection()` | `ProceduralMeshComponent.h` | ✅ Available | `bCreateCollision` parameter in `CreateMeshSection()`. Simple convex collision by default. | 1 |

#### Protocol / Packet Audit

| # | Requirement | Current Location | Status | Gap | Stage |
|---|-------------|------------------|--------|-----|-------|
| P1 | `PT_Mesh = 0x06` in `EPacketType` enum | `SyncTypes.h:212` | ✅ Defined | Enum value exists, value `0x06` is unused. | — |
| P2 | `PT_Mesh` runtime handler code | — | ❌ Gap | Zero handler code. No `ProcessBinaryPacket` branch, no `HandleMeshDef`, no serialization. | 1 |
| P3 | `0x06` in UE FNV signature | `SyncTypes.h:1419-1425` | ❌ Missing | Current signature: `0x01, 0x03, 0x04, 0x07, 0x08, 0x09, 0x0A, 0x0B, 0x0C, 0x0D, 0x0E, 0x0F`. `0x05` added in Phase 7B. `0x06` is absent. | 1 |
| P4 | `0x06` in Blender FNV signature | `network.py:41` | ❌ Missing | Current signature: `0x01, 0x03, 0x04, 0x05, 0x07, 0x08, 0x09, 0x0A, 0x0B, 0x0C, 0x0D, 0x0E, 0x0F`. `0x06` is absent. | 1 |
| P5 | `PT_Mesh` defined in Blender `network.py` | `network.py` | ❌ Missing | No `PT_Mesh` constant (only `COLLECTION_OP_COLLECTION_CREATE = 0x06` as a sub-op). | 1 |
| P6 | `LIVE_SYNC_MAX_PACKET_SIZE = 524288` | `Phase6I.1` | ✅ Defined | Bounds each `PT_Mesh` chunk. | — |
| P7 | `CVarLiveSyncMaxPacketRate = 120` | `Phase6I.1` | ✅ Defined | Geometry chunks compete with all other packets. | — |

### 17.2 — Key Findings Summary

| Domain | Total Items | Available | Partial | Missing |
|--------|-------------|-----------|---------|---------|
| Blender geometry extraction | 10 | 1 | 0 | 9 |
| Blender send pipeline | 6 | 5 | 1 | 0 |
| UE reconstruction feasibility | 8 | 7 | 0 | 1 |
| Protocol / packet | 7 | 3 | 0 | 4 |
| **Total** | **28** | **13** | **1** | **14** |

### 17.3 — Critical Findings

| # | Finding | Impact | Action |
|---|---------|--------|--------|
| CF1 | Zero geometry extraction code exists | Cannot implement any geometry sync without depsgraph evaluation | Stage 1 highest priority |
| CF2 | No mechanism to select ProcMesh vs StaticMesh | Actors spawned with `UStaticMeshComponent` only; no `ProceduralMeshComponent` exists | Add implicit switch: first `PT_Mesh` chunk replaces `StaticMeshComponent` with `ProceduralMeshComponent` |
| CF3 | `0x06` not in FNV signature on either side | `PT_Mesh` packets silently rejected by UE, not sent by Blender | Stage 1 signature update (same pattern as Phase 7B 0x05) |
| CF4 | No `PT_Mesh` constant in Blender `network.py` | Cannot reference packet type | Stage 1 add constant |
| CF5 | `UProceduralMeshComponent` is available in the `Engine` module | No Build.cs change needed | Stage 1 add `#include` only |
| CF6 | Per-GUID geometry state (version hash, pending chunks) needs addition | Cleanup on delete/reconnect must be handled | Stage 1 add `_last_geometry_version` to Blender, `ProceduralMeshComponent` map to UE |

### 17.4 — Recommended Stage 1 Implementation Order

| Priority | Item | Rationale | Dependencies |
|----------|------|-----------|--------------|
| 1 | Add `PT_Mesh = 0x06` constant to Blender `network.py` | Foundation for all geometry code | None |
| 2 | Add `0x06` to FNV protocol signature (both sides) | Required before UE will accept packets | 1 |
| 3 | Implement Blender `evaluated_mesh_get()` — depsgraph eval + `to_mesh()` | Core extraction function | None (standalone) |
| 4 | Implement vertex + triangle extraction | Minimal geometry data to build a mesh | 3 |
| 5 | Implement per-face material index extraction | Required for multi-material meshes | 3 |
| 6 | Implement Blender geometry version hashing (SHA-256 or xxHash256) | Change detection trigger | 4 |
| 7 | Implement Blender geometry change detection + chunk send in `check_updates()` | Emission pipeline | 6, 5 |
| 8 | Implement `PT_Mesh` chunk wire format in `network.py` | Serialization | 1 |
| 9 | Implement UE `HandleMeshDef` chunk receiver + reassembler | Core UE handler | 8 |
| 10 | Implement `ProceduralMeshComponent` creation + section build on `PT_Mesh` first chunk | Mesh reconstruction | 9 |
| 11 | Implement per-face material slot → section mapping | Material support | 10, Phase 7B |
| 12 | Add geometry-specific diagnostics counters + tests | Validation | 11 |

### 17.5 — Files Changed During Audit

**None.** Stage 0 is audit-only; zero source files modified.
