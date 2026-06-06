# Phase 7C Stage 2 — Manual Mesh Attribute Sync (Loop-Expanded Render Vertices)

## Overview

Extend PT_Mesh packet to carry loop-expanded full-attribute vertex data (positions, loop normals, UV0, optional color0) for selected mesh sync to UE.

**Manual only.** Operator `bpy.ops.uelivesync.sync_selected_mesh_to_ue()` triggers sync. No hook into `check_updates()`.

Existing 89-byte PT_Mesh chunk header unchanged. FULL_ATTR flag gates schema detection. SchemaVersion=1 payload parsed only when FULL_ATTR flag present. Legacy V5 path untouched.

---

## 1. Wire Protocol — Chunk Layout

### 1.1 89-Byte Header (unchanged)

```
[16-byte GUID]
[64-byte VersionHash]
[4-byte ChunkIndex LE]
[4-byte ChunkCount LE]
[1-byte Flags]
= 89 bytes total
```

### 1.2 FULL_ATTR Flag

```cpp
// UE (SyncTypes.h)
static constexpr uint8_t MESH_CHUNK_FLAG_FULL_ATTR = 0x80;

// Blender (network.py)
MESH_CHUNK_FLAG_FULL_ATTR = 0x80
```

- `0x80` confirmed safe: no existing V5 mesh handler reads this bit.
- `CAP_SUPPORTS_SEQUENCER_OPS=0x80` is a capability flag (different domain, no conflict).
- `Flags & 0x80 != 0` → v1 payload (SchemaVersion present in chunk 0).
- `Flags & 0x80 == 0` → legacy V5 payload (no SchemaVersion).

### 1.3 Chunk Payload (FULL_ATTR present, SchemaVersion=1)

```
[89-byte header]

Chunk 0 only (when FULL_ATTR flag set):
  [4-byte SchemaVersion LE (uint32)]     — SchemaVersion=1
  [4-byte vertex_stride LE (uint32)]     — 32 or 48
  [4-byte vertex_count LE (uint32)]      — 3 × triangles_in_chunk
  [vertex_count × stride bytes]          — VertexV1[]
  [4-byte index_count LE (uint32)]       — 3 × triangles_in_chunk
  [index_count × 4 bytes]                — uint32[]

Chunks 1..N-1 (same FULL_ATTR flag inheritance):
  [4-byte vertex_stride LE (uint32)]
  [4-byte vertex_count LE (uint32)]
  [vertex_count × stride bytes]
  [4-byte index_count LE (uint32)]
  [index_count × 4 bytes]

Chunk 0 (FULL_ATTR absent, legacy V5):
  [4-byte VertexCount LE]               — V5 format, NOT SchemaVersion
  [... V5 vertex/index data ...]
```

**Key invariant:** SchemaVersion is NEVER read when FULL_ATTR flag is absent. V5 VertexCount bytes are never misinterpreted.

---

## 2. Render Vertex Layout

### 2.1 VertexV1 (packed, no padding)

| Offset | Size | Field | Type | Notes |
|--------|------|-------|------|-------|
| 0 | 12 | pos | float[3] | XYZ from loop corner |
| 12 | 12 | normal | float[3] | Loop-smoothed normal (XYZ) |
| 24 | 8 | uv[2] | float[2] | UV0 (always present) |
| 32 | 16 | color[4] | float[4] | (optional) RGBA |

**Stride:**
- **stride = 32** — no color0 layer (pos + normal + uv0)
- **stride = 48** — color0 layer present (pos + normal + uv0 + color0)

`vertex_stride` written per-chunk as uint32 LE. UE validates stride strictly.

### 2.2 Render Vertex Definition

Each render vertex represents **one triangle corner** (one loop).

```
render vertex = {
    position:  loop_triangles[t].vertices[i] → loops[loop_idx].co
    normal:    loops[loop_idx].no (loop-normalized)
    uv0:       loops[loop_idx].uv[0] (or fallback (0,0))
    color0:    loops[loop_idx].color (if vertex color layer exists, else absent)
}
```

Duplicate source vertices when UV/normal/color differs by corner.

### 2.3 UV0 Policy

- **UV0 always present** in fixed VertexV1 base stride.
- If Blender mesh has **no UV layer**: emit `uv0 = (0.0, 0.0)` per vertex. Emit diagnostic: `[MESH][ATTR] uv0Fallback=1`.
- If mesh has ≥1 UV layer: use first UV map. Emit diagnostic: `[MESH][ATTR] uv0Layer=<layer_name>`.
- No optional UV0 in Stage 2.

### 2.4 Color0 Policy

- Color0 presence determined **solely by stride**:
  - stride 32 → no color0.
  - stride 48 → color0 present.
- **No second flag** for color0. Stride is the single source of truth.
- Color0 = float4 RGBA from active vertex color layer.

---

## 3. Triangle-Range Chunking

### 3.1 Policy

Each chunk contains **complete triangles only**. No split triangles.

```
triangle_count_in_chunk = number of loop_triangles in this chunk
vertex_count            = 3 × triangle_count_in_chunk
index_count             = 3 × triangle_count_in_chunk
```

**vertex_count == index_count** always.

### 3.2 Indices

- Indices **local to the chunk**: triangle `j` inside chunk uses `3*j, 3*j+1, 3*j+2`.
- UE reassembly: `global_index = local_index + VertexBase`, where `VertexBase = sum(vertex_count of chunks 0..i-1)`.
- No explicit VertexBase field — computed cumulatively by UE.

### 3.3 Chunk Count

```python
TRIANGLES_PER_CHUNK = 8192  # target
chunk_count = ceil(len(loop_triangles) / TRIANGLES_PER_CHUNK)
```

---

## 4. SchemaVersion Validation

### 4.1 FULL_ATTR Absent

- **Do NOT read SchemaVersion.**
- Route to legacy V5 parser unchanged.
- First 4 bytes after header = V5 VertexCount.
- `KeyframeMeshSchemaV5++`.

### 4.2 FULL_ATTR Present + ChunkIndex == 0

Read bytes [89:93] as `SchemaVersion` (uint32 LE):

| SchemaVersion | Action |
|---------------|--------|
| **1** | Parse v1 vertex/index arrays. `KeyframeMeshSchemaV1++`. |
| **0** | **Reject safely.** `KeyframeMeshVersionRejected++`. NOT legacy V5. |
| **>1** | **Reject safely.** `KeyframeMeshVersionRejected++`. NOT legacy V5. |

### 4.3 FULL_ATTR Present + ChunkIndex > 0

- **Do NOT read SchemaVersion.**
- Parse vertex_stride, vertex_count, vertices, index_count, indices directly.
- SchemaVersion consumed from chunk 0 only.

---

## 5. UE Parser Validation Rules

### 5.1 Stride Validation

| Stride | Action |
|--------|--------|
| 32 | Accept. No color0. |
| 48 | Accept. color0 present. |
| Other | **Reject snapshot.** `KeyframeMeshStrideInvalid++`. |

### 5.2 Cross-Chunk Stride Consistency

- First chunk's stride is reference.
- All subsequent chunks must match.
- Mismatch → **reject snapshot.** `KeyframeMeshStrideMismatch++`.

### 5.3 Index Validity

- `local_index < vertex_count` for all indices in chunk → accept.
- Any `local_index >= vertex_count` → **reject snapshot.** `KeyframeMeshIndexOutOfBounds++`.

### 5.4 Chunk Completeness

- All ChunkIndex in [0, ChunkCount) received → accept.
- Missing any ChunkIndex → **reject.** `KeyframeMeshIncomplete++`.

---

## 6. Coordinate Conversion & Winding (UE-side, post-parse)

### 6.1 Position

```
position = { X*100.0, -Y*100.0, Z*100.0 }
```

### 6.2 Normal/Tangent

```
normal/tangent = { X, -Y, Z }
```

### 6.3 Winding Flip

For each triangle index triplet `(A, B, C)`, use `(A, C, B)` for rendering.

---

## 7. Diagnostic Counters

```
// Per-frame / per-message (reset each sync frame)
KeyframeMeshVerticesTotal      — total render vertices emitted
KeyframeMeshIndicesTotal       — total indices emitted
KeyframeMeshChunksTotal        — chunks in this message
KeyframeMeshSchemaV1           — 1 if SchemaVersion=1 parsed
KeyframeMeshSchemaV5           — 1 if legacy V5 path used
KeyframeMeshVerticesSkipped    — vertices skipped by clamp/overflow
KeyframeMeshIndicesSkipped     — indices skipped by clamp/overflow
KeyframeMeshVerticesParsed     — UE-side vertices applied
KeyframeMeshIndicesParsed      — UE-side indices applied
KeyframeMeshChunksParsed       — UE-side chunks parsed
KeyframeMeshVersionRejected    — bad/unrecognized SchemaVersion
KeyframeMeshStrideInvalid      — invalid stride
KeyframeMeshStrideMismatch     — cross-chunk stride mismatch
KeyframeMeshIndexOutOfBounds   — local index >= vertex_count
KeyframeMeshIncomplete         — missing chunk in set
KeyframeMeshHeaderBytes        — bytes consumed by 89-byte header (audit)
KeyframeMeshAttrUv0Fallback    — uv0Fallback=1 emitted (Blender)
```

Reset policy: all counters reset at start of each `HandleMeshPacket()`.

---

## 8. Tests

### 8.1 Blender-side (pytest)

| # | Test | Assertion |
|---|------|-----------|
| T1 | Single tris mesh, loop-expanded vertices = 3×tris | `len(render_verts) == len(loop_triangles) * 3` |
| T2 | Duplicate source vertex split correctly | Same source vertex → different render verts when UV/normal differ |
| T3 | UV0 always float2 (32 bytes in stride) | `len(render_verts[0].uv) == 2` |
| T4 | Color0 present iff stride=48 | stride 32 → no color; stride 48 → color present |
| T5 | Tangent/handedness NOT present (Stage 2) | No tangent field in render vertex |
| T6 | Triangle-range chunking: vc == ic == 3 × triangles_in_chunk | For every chunk |
| T7 | FULL_ATTR flag 0x80 unused in existing code | grep confirms absent from V5 path |
| T8 | V5 payload (no FULL_ATTR) → no SchemaVersion written | Chunk 0 starts with V5 vertex data |
| T9 | v1 chunk 0: FULL_ATTR set AND SchemaVersion=1 written after 89B | `payload[89:93] == b'\x01\x00\x00\x00'` |
| T10 | No-UV mesh → fallback UV0=(0,0) + diagnostic | `uv == (0.0, 0.0)` AND `[MESH][ATTR] uv0Fallback=1` |
| T11 | Mesh with UV layer → first UV map used | UV values match first UV layer |
| T12 | Operator `bpy.ops.uelivesync.sync_selected_mesh_to_ue()` callable | Under `uelivesync` namespace, not `mesh` |
| T13 | No `check_updates()` mesh sync hook | Code inspection |

### 8.2 UE-side (C++ / GUnit)

| # | Test | Assertion |
|---|------|-----------|
| U1 | V5 payload (no FULL_ATTR) → legacy V5, no false SchemaVersion | `KeyframeMeshSchemaV5 == 1` |
| U2 | FULL_ATTR + SchemaVersion=1 → v1 path parsed | `KeyframeMeshSchemaV1 == 1` |
| U3 | FULL_ATTR absent + bytes look like valid uint32 → NO schema parse | `KeyframeMeshSchemaV5 == 1` |
| U4 | FULL_ATTR + SchemaVersion=99 → reject safely | `KeyframeMeshVersionRejected == 1` |
| U5 | FULL_ATTR + SchemaVersion=0 → reject safely (NOT legacy V5) | `KeyframeMeshVersionRejected == 1`, `KeyframeMeshSchemaV5 == 0` |
| U6 | Chunk 0 FULL_ATTR → SchemaVersion read from bytes [89:93] | Not from VertexCount field |
| U7 | Chunk 1+ FULL_ATTR → NO SchemaVersion read | SchemaVersion consumed from chunk 0 only |
| U8 | Stride 32 accepted, color0 absent | stride == 32, color not accessed |
| U9 | Stride 48 accepted, color0 present | stride == 48, color parsed |
| U10 | Stride 36 (invalid) → reject snapshot | `KeyframeMeshStrideInvalid == 1` |
| U11 | Chunk 0 stride=32, Chunk 1 stride=48 → reject | `KeyframeMeshStrideMismatch == 1` |
| U12 | Local chunk indices converted via VertexBase | `global = local + VertexBase` |
| U13 | Triangle-range: vc == ic == 3 × triangles_in_chunk | Per-chunk verified |
| U14 | MeshGUID lookup → create actor if missing | Actor created with correct name |
| U15 | Winding flip A,C,B applied | Vertex order flipped |
| U16 | Incomplete chunk set → reject | `KeyframeMeshIncomplete == 1` |
| X1 | Existing V5 mesh sync unchanged (non-regression) | `KeyframeMeshSchemaV5 == 1` |
| X2 | Existing mesh update path unchanged (non-regression) | No new `check_updates` calls |

---

## 9. Runtime Validation

### 9.1 Pre-Transmit (Blender)

1. **Vertex count sanity:**
   - `render_verts_count == len(loop_triangles) * 3`.
   - If 0, skip mesh message entirely.
2. **Chunk boundary:**
   - `chunk_count = ceil(len(loop_triangles) / TRIANGLES_PER_CHUNK)`.
   - Each chunk byte size ≤ MAX_CHUNK_BYTES (e.g., 256 KB).
3. **FULL_ATTR flag:**
   - Chunk 0: `flags |= MESH_CHUNK_FLAG_FULL_ATTR` (0x80).
   - Chunks 1..N-1: inherit FULL_ATTR flag from chunk 0.
4. **SchemaVersion write (chunk 0 only, after 89B header):**
   - Write 4-byte LE `SchemaVersion=1`. Never written if FULL_ATTR absent.
5. **UV0:** always present (fallback (0,0) if no UV layer).
6. **Color0:** present only if stride=48.
7. **Tangent/handedness:** NOT present (deferred).

### 9.2 On-Receive (UE)

1. **89-byte header parse** (unchanged): GUID(16) + VersionHash(64) + ChunkIndex(4) + ChunkCount(4) + Flags(1).
2. **FULL_ATTR gating:**
   - Absent → legacy V5 path. First 4 bytes = VertexCount. `KeyframeMeshSchemaV5++`.
   - Present + chunk 0 → read SchemaVersion from bytes [89:93].
   - Present + chunk >0 → no SchemaVersion read.
3. **SchemaVersion (FULL_ATTR present, chunk 0):**
   - 1 → parse v1 vertex/index.
   - 0 or >1 → reject. `KeyframeMeshVersionRejected++`.
4. **Stride validation:** must be 32 or 48. Reject invalid.
5. **Cross-chunk stride:** all chunks must match. Reject mismatch.
6. **Index validity:** `local_index < vertex_count`. Reject out of bounds.
7. **Chunk completeness:** all ChunkIndex in [0, ChunkCount) received. Reject incomplete.
8. **Coordinate conversion:** position `X*100,-Y*100,Z*100`; normal/tangent `X,-Y,Z`.
9. **Winding flip:** use (A,C,B) for each triangle.
10. **Mesh creation/update:** GUID lookup → existing actor or new. Error → count only applied vertices; no crash.

---

## 10. Non-Regression Checklist

| # | Item | Verification |
|---|------|-------------|
| N1 | Existing V5 PT_Mesh packet type unchanged | Wire format identical |
| N2 | Existing 89-byte chunk header unchanged | GUID+VersionHash+ChunkIndex+ChunkCount+Flags |
| N3 | FULL_ATTR gating prevents VertexCount/SchemaVersion ambiguity | V5 path never reads SchemaVersion |
| N4 | FULL_ATTR + SchemaVersion=0 rejected (not legacy V5) | `KeyframeMeshVersionRejected == 1` |
| N5 | Manual operator only: `bpy.ops.uelivesync.sync_selected_mesh_to_ue()` | Under `uelivesync` namespace, no auto trigger |
| N6 | No `check_updates()` auto mesh attribute sync | Code inspection |
| N7 | Transform keyframe handling (channels 0–8) unaffected | Same code path |
| N8 | Visibility keyframe handling (channels 9–10) unaffected | Same code path |
| N9 | Rename/hierarchy/keyframe lanes unaffected | No changes to rename/hierarchy/keyframe handlers |
| N10 | Stride validation prevents cross-chunk layout errors | Stride mismatch → reject snapshot |
| N11 | Triangle-range chunking prevents split-triangle errors | Complete triangles per chunk, local indices valid |
| N12 | No UV layer → fallback UV0=(0,0) with diagnostic | `uv0Fallback=1` emitted |
| N13 | 0x80 flag safe for mesh chunks | Confirmed unused in V5 path; CAP flag in different domain |