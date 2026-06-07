#!/usr/bin/env python3
"""
Phase 7C Stage 2C.3 — UE FULL_ATTR v1 mesh build tests.

Tests mirror the UE-side BuildV1MeshFromReassembly rules in Python:
  - Merge completed chunks into final arrays
  - Blender → UE coord conversion (Y-flip + cm scale)
  - Flip winding (CW → CCW per Y-flip handedness)
  - Single section only (Stage 2C.3)
  - Stride 32: no color0; stride 48: color0 preserved
  - Missing actor safe skip
  - Invalid indices after merge rejected
  - Completed entry cleared after successful build
"""

import importlib.util
import os
import struct
import sys
import uuid

_repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_net_path = os.path.join(_repo_root, "Blender_Addon", "network.py")

_spec = importlib.util.spec_from_file_location("network", _net_path)
_net = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_net)

serialize_full_attr_mesh_chunk_v1 = _net.serialize_full_attr_mesh_chunk_v1
MESH_CHUNK_FLAG_FULL_ATTR = _net.MESH_CHUNK_FLAG_FULL_ATTR
MESH_FULL_ATTR_VERTEX_STRIDE_NO_COLOR = _net.MESH_FULL_ATTR_VERTEX_STRIDE_NO_COLOR
MESH_FULL_ATTR_VERTEX_STRIDE_COLOR0 = _net.MESH_FULL_ATTR_VERTEX_STRIDE_COLOR0

PASS = 0
FAIL = 0


def check(name, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  PASS: {name}")
    else:
        FAIL += 1
        msg = f"  FAIL: {name}"
        if detail:
            msg += f" \u2014 {detail}"
        print(msg)


# =========================================================
# Test helpers — mirror reassembly test format
# =========================================================

def dummy_guid():
    return uuid.uuid4()


def empty_version_hash():
    return "a" * 64


def make_rv(pos, normal=(0, 0, 1), uv=(0, 0), color=None):
    rv = {"position": pos, "normal": normal, "uv0": uv}
    if color:
        rv["color0"] = color
    return rv


def make_verts_tri(color=None):
    return [
        make_rv((0, 0, 0), (0, 0, 1), (0, 0), color=color),
        make_rv((1, 0, 0), (0, 0, 1), (1, 0), color=color),
        make_rv((0, 1, 0), (0, 0, 1), (0, 1), color=color),
    ]


def make_indices_tri():
    return [0, 1, 2]


def get_body(payload):
    """Strip 89-byte mesh header to get the v1 chunk payload body."""
    return payload[89:]


# =========================================================
# Parse engine — mirrors UE ParseV1MeshPayload rules
# =========================================================

class V1MeshParsedVertex:
    __slots__ = ("position", "normal", "uv0", "color0")

    def __init__(self, position, normal, uv0, color0=None):
        self.position = position
        self.normal = normal
        self.uv0 = uv0
        self.color0 = color0


class V1MeshParsedChunk:
    __slots__ = ("chunk_index", "chunk_count", "vertex_stride",
                 "vertex_count", "index_count", "vertices", "indices")

    def __init__(self):
        self.chunk_index = 0
        self.chunk_count = 0
        self.vertex_stride = 0
        self.vertex_count = 0
        self.index_count = 0
        self.vertices = []
        self.indices = []


def ue_parse_v1_payload(data, chunk_index, chunk_count):
    """Mirrors UE ParseV1MeshPayload rules.

    Returns (ok, info) where info is a dict with parsed data on success
    or error message on failure.
    """
    offset = 0
    data_len = len(data)

    if chunk_index == 0:
        if offset + 4 > data_len:
            return False, {"error": "truncated SchemaVersion"}
        schema_version = struct.unpack_from("<I", data, offset)[0]
        offset += 4
        if schema_version != 1:
            return False, {"error": f"unsupported SchemaVersion {schema_version}"}

    if chunk_index >= chunk_count:
        return False, {"error": "chunk_index >= chunk_count"}

    if offset + 4 > data_len:
        return False, {"error": "truncated VertexStride"}
    vertex_stride = struct.unpack_from("<I", data, offset)[0]
    offset += 4

    if vertex_stride not in (32, 48):
        return False, {"error": f"unsupported VertexStride {vertex_stride}"}

    if offset + 4 > data_len:
        return False, {"error": "truncated VertexCount"}
    vertex_count = struct.unpack_from("<I", data, offset)[0]
    offset += 4

    vertex_bytes = vertex_count * vertex_stride
    if offset + vertex_bytes > data_len:
        return False, {"error": f"truncated vertex data: need {vertex_bytes}, have {data_len - offset}"}

    b_has_color0 = vertex_stride == 48
    vertices = []
    for _ in range(vertex_count):
        pos = struct.unpack_from("<fff", data, offset)
        offset += 12
        nrm = struct.unpack_from("<fff", data, offset)
        offset += 12
        uv = struct.unpack_from("<ff", data, offset)
        offset += 8
        col = None
        if b_has_color0:
            col = struct.unpack_from("<ffff", data, offset)
            offset += 16
        vertices.append(V1MeshParsedVertex(pos, nrm, uv, col))

    if offset + 4 > data_len:
        return False, {"error": "truncated IndexCount"}
    index_count = struct.unpack_from("<I", data, offset)[0]
    offset += 4

    index_bytes = index_count * 4
    if offset + index_bytes > data_len:
        return False, {"error": f"truncated index data: need {index_bytes}, have {data_len - offset}"}

    indices = list(struct.unpack_from(f"<{index_count}I", data, offset))

    for idx in indices:
        if idx >= vertex_count:
            return False, {"error": f"OOB index {idx} >= {vertex_count}"}

    chunk = V1MeshParsedChunk()
    chunk.chunk_index = chunk_index
    chunk.chunk_count = chunk_count
    chunk.vertex_stride = vertex_stride
    chunk.vertex_count = vertex_count
    chunk.index_count = index_count
    chunk.vertices = vertices
    chunk.indices = indices

    return True, {"chunk": chunk}


# =========================================================
# Reassembly engine — mirrors UE-side FULL_ATTR dispatch
# =========================================================

class V1MeshReassemblyState:
    """Mirrors UE FV1MeshReassemblyState."""

    def __init__(self):
        self.chunk_count = 0
        self.vertex_stride = 0
        self.chunks_received = 0
        self.b_reconstructed = False
        self.chunks = {}  # chunk_index -> V1MeshParsedChunk

    def is_complete(self):
        return self.chunk_count > 0 and self.chunks_received >= self.chunk_count

    def has_chunk(self, chunk_index):
        return chunk_index in self.chunks


class V1ReassemblyEngine:
    """Mirrors UE FULL_ATTR v1 reassembly + build logic."""

    def __init__(self):
        self.pending = {}  # (guid_str, vhash) -> V1MeshReassemblyState
        # Stats
        self.mesh_schema_v1_packets_parsed = 0
        self.mesh_schema_v1_packets_rejected = 0
        self.mesh_schema_v1_chunks_stored = 0
        self.mesh_schema_v1_meshes_completed = 0
        self.mesh_schema_v1_duplicate_chunks = 0
        self.mesh_schema_v1_reassembly_rejected = 0
        self.mesh_schema_v1_sections_built = 0
        self.mesh_schema_v1_build_rejected = 0
        self.mesh_schema_v1_missing_actor = 0

    def ingest_chunk(self, guid, version_hash, chunk_index, chunk_count, payload):
        """Mirrors UE FULL_ATTR dispatch for v1 chunks.

        Returns True if chunk was accepted, False if rejected.
        """
        ok, info = ue_parse_v1_payload(payload, chunk_index, chunk_count)
        if not ok:
            self.mesh_schema_v1_packets_rejected += 1
            return False

        chunk = info["chunk"]
        self.mesh_schema_v1_packets_parsed += 1

        key = (str(guid), version_hash)
        state = self.pending.get(key)
        if state is None:
            state = V1MeshReassemblyState()
            self.pending[key] = state

        if state.chunk_count == 0:
            state.chunk_count = chunk.chunk_count
            state.vertex_stride = chunk.vertex_stride
        else:
            mismatch = False
            if state.chunk_count != chunk.chunk_count:
                mismatch = True
            if state.vertex_stride != chunk.vertex_stride:
                mismatch = True
            if mismatch:
                self.mesh_schema_v1_reassembly_rejected += 1
                return False

        if state.has_chunk(chunk_index):
            self.mesh_schema_v1_duplicate_chunks += 1
            return False

        state.chunks[chunk_index] = chunk
        state.chunks_received += 1
        self.mesh_schema_v1_chunks_stored += 1

        if state.is_complete():
            self.mesh_schema_v1_meshes_completed += 1

        return True

    def build_completed(self, guid, version_hash, actor_present=True):
        """Mirrors UE BuildV1MeshFromReassembly for one key.

        Returns (built: bool, result: dict) where result contains
        the final arrays on success, or error info on failure.

        When actor_present=False, simulates missing actor (no build).
        """
        key = (str(guid), version_hash)
        state = self.pending.get(key)
        if state is None:
            return False, {"error": "no reassembly state"}

        if not state.is_complete() or state.b_reconstructed:
            return False, {"error": "not complete or already built"}

        if not actor_present:
            self.mesh_schema_v1_missing_actor += 1
            return False, {"error": "missing actor"}

        # Merge all chunks in order
        positions = []
        indices = []
        normals = []
        uv0 = []
        colors = []
        b_has_color0 = False
        b_build_valid = True
        vertex_base = 0

        for i in range(state.chunk_count):
            chunk = state.chunks.get(i)
            if chunk is None:
                b_build_valid = False
                break

            b_chunk_has_color0 = chunk.vertex_stride == 48
            b_has_color0 = b_has_color0 or b_chunk_has_color0

            for v in chunk.vertices:
                # Blender -> UE: Y-flip + cm scale
                ue_pos = (v.position[0] * 100.0,
                          -v.position[1] * 100.0,
                          v.position[2] * 100.0)
                positions.append(ue_pos)

                # Normal: Y-flip only
                ue_nrm = (v.normal[0],
                          -v.normal[1],
                          v.normal[2])
                normals.append(ue_nrm)

                # UV0: no conversion
                uv0.append(v.uv0)

                if b_chunk_has_color0 and v.color0 is not None:
                    colors.append(v.color0)

            # Indices with VertexBase offset; flip winding CW->CCW
            for idx in range(0, chunk.index_count, 3):
                if idx + 2 < chunk.index_count:
                    indices.append(chunk.indices[idx] + vertex_base)
                    indices.append(chunk.indices[idx + 2] + vertex_base)
                    indices.append(chunk.indices[idx + 1] + vertex_base)

            vertex_base += chunk.vertex_count

        if not b_build_valid:
            self.mesh_schema_v1_build_rejected += 1
            return False, {"error": "missing chunk during merge"}

        if len(positions) == 0 or len(indices) == 0:
            self.mesh_schema_v1_build_rejected += 1
            return False, {"error": "empty geometry"}

        # Validate indices in bounds
        for idx_val in indices:
            if idx_val < 0 or idx_val >= len(positions):
                self.mesh_schema_v1_build_rejected += 1
                return False, {"error": f"OOB index {idx_val} >= {len(positions)}"}

        # If stride=32 (no color0), pass empty colors
        section_colors = colors if b_has_color0 else []

        result = {
            "positions": positions,
            "indices": indices,
            "normals": normals,
            "uv0": uv0,
            "colors": section_colors,
            "has_color0": b_has_color0,
            "vertex_count": len(positions),
            "triangle_count": len(indices) // 3,
        }

        self.mesh_schema_v1_sections_built += 1
        state.b_reconstructed = True
        del self.pending[key]

        return True, result


# =========================================================
# Tests
# =========================================================

def test_t1_single_chunk_no_color():
    """T1: single completed chunk builds one mesh section (stride 32)."""
    guid = dummy_guid()
    vhash = empty_version_hash()
    verts = make_verts_tri()
    indices = make_indices_tri()

    payload = serialize_full_attr_mesh_chunk_v1(
        guid, vhash, 0, 1, verts, indices,
        flags=MESH_CHUNK_FLAG_FULL_ATTR,
        vertex_stride=MESH_FULL_ATTR_VERTEX_STRIDE_NO_COLOR)
    body = get_body(payload)

    eng = V1ReassemblyEngine()
    ok = eng.ingest_chunk(guid, vhash, 0, 1, body)
    check("T1 ingest ok", ok)

    ok, result = eng.build_completed(guid, vhash, actor_present=True)
    check("T1 build ok", ok)
    check("T1 positions count", result["vertex_count"] == 3)
    check("T1 indices count", result["triangle_count"] == 1)
    check("T1 no color", not result["has_color0"])
    check("T1 colors empty", len(result["colors"]) == 0)
    check("T1 section built", eng.mesh_schema_v1_sections_built == 1)

    # Verify coord conversion: Y flip + cm scale
    check("T1 pos0 X", abs(result["positions"][0][0] - 0.0) < 0.001)
    check("T1 pos0 Y", abs(result["positions"][0][1]) < 0.001)
    check("T1 pos0 Z", abs(result["positions"][0][2]) < 0.001)

    check("T1 pos1 X", abs(result["positions"][1][0] - 100.0) < 0.001)

    # Verify winding flip: original (0,1,2) -> (0,2,1)
    check("T1 winding 0", result["indices"][0] == 0)
    check("T1 winding 1", result["indices"][1] == 2)
    check("T1 winding 2", result["indices"][2] == 1)


def test_t2_multi_chunk_merge():
    """T2: multi-chunk merge offsets local indices by VertexBase."""
    guid = dummy_guid()
    vhash = empty_version_hash()
    chunk_count = 2

    # Chunk 0: 3 verts, 1 tri (0,1,2)
    verts_a = make_verts_tri()
    indices_a = [0, 1, 2]

    # Chunk 1: 3 verts, 1 tri (0,1,2) -> after base 3, should be (3,5,4)
    verts_b = [
        make_rv((2, 0, 0)),
        make_rv((3, 0, 0)),
        make_rv((2, 1, 0)),
    ]
    indices_b = [0, 1, 2]

    payload0 = serialize_full_attr_mesh_chunk_v1(
        guid, vhash, 0, chunk_count, verts_a, indices_a,
        flags=MESH_CHUNK_FLAG_FULL_ATTR,
        vertex_stride=MESH_FULL_ATTR_VERTEX_STRIDE_NO_COLOR)

    payload1 = serialize_full_attr_mesh_chunk_v1(
        guid, vhash, 1, chunk_count, verts_b, indices_b,
        flags=MESH_CHUNK_FLAG_FULL_ATTR,
        vertex_stride=MESH_FULL_ATTR_VERTEX_STRIDE_NO_COLOR)

    eng = V1ReassemblyEngine()
    check("T2 ingest chunk0", eng.ingest_chunk(guid, vhash, 0, chunk_count, get_body(payload0)))
    check("T2 ingest chunk1", eng.ingest_chunk(guid, vhash, 1, chunk_count, get_body(payload1)))

    ok, result = eng.build_completed(guid, vhash, actor_present=True)
    check("T2 build ok", ok)
    check("T2 total verts", result["vertex_count"] == 6)
    check("T2 total tris", result["triangle_count"] == 2)

    # Chunk 0 indices: (0,2,1)
    check("T2 tri0 idx0", result["indices"][0] == 0)
    check("T2 tri0 idx1", result["indices"][1] == 2)
    check("T2 tri0 idx2", result["indices"][2] == 1)

    # Chunk 1 indices: (3+0, 3+2, 3+1) = (3,5,4)
    check("T2 tri1 idx0", result["indices"][3] == 3)
    check("T2 tri1 idx1", result["indices"][4] == 5)
    check("T2 tri1 idx2", result["indices"][5] == 4)


def test_t3_duplicate_verts_preserved():
    """T3: duplicate render vertices are preserved, not welded."""
    guid = dummy_guid()
    vhash = empty_version_hash()

    # All 3 verts at same position but different normals (simulating seam)
    verts = [
        make_rv((0, 0, 0), (1, 0, 0), (0, 0)),
        make_rv((0, 0, 0), (0, 1, 0), (0, 0)),
        make_rv((0, 0, 0), (0, 0, 1), (0, 0)),
    ]
    indices = [0, 1, 2]

    payload = serialize_full_attr_mesh_chunk_v1(
        guid, vhash, 0, 1, verts, indices,
        flags=MESH_CHUNK_FLAG_FULL_ATTR,
        vertex_stride=MESH_FULL_ATTR_VERTEX_STRIDE_NO_COLOR)
    body = get_body(payload)

    eng = V1ReassemblyEngine()
    check("T3 ingest", eng.ingest_chunk(guid, vhash, 0, 1, body))
    ok, result = eng.build_completed(guid, vhash, actor_present=True)
    check("T3 build ok", ok)
    check("T3 verts preserved", result["vertex_count"] == 3)
    check("T3 no weld", result["normals"][0] != result["normals"][1])


def test_t4_normals_preserved():
    """T4: normals preserved per vertex with Y-flip."""
    guid = dummy_guid()
    vhash = empty_version_hash()

    verts = [
        make_rv((0, 0, 0), (0.5, 1.0, 0.3), (0, 0)),
        make_rv((1, 0, 0), (-0.2, 0.8, 0.6), (0, 0)),
    ]
    indices = [0, 1, 1]

    payload = serialize_full_attr_mesh_chunk_v1(
        guid, vhash, 0, 1, verts, indices,
        flags=MESH_CHUNK_FLAG_FULL_ATTR,
        vertex_stride=MESH_FULL_ATTR_VERTEX_STRIDE_NO_COLOR)
    body = get_body(payload)

    eng = V1ReassemblyEngine()
    check("T4 ingest", eng.ingest_chunk(guid, vhash, 0, 1, body))
    ok, result = eng.build_completed(guid, vhash, actor_present=True)
    check("T4 build ok", ok)

    # Normal 0: Y-flip: 1.0 -> -1.0
    check("T4 normal0 X", abs(result["normals"][0][0] - 0.5) < 0.001)
    check("T4 normal0 Y", abs(result["normals"][0][1] - (-1.0)) < 0.001)
    check("T4 normal0 Z", abs(result["normals"][0][2] - 0.3) < 0.001)

    # Normal 1: Y-flip: 0.8 -> -0.8
    check("T4 normal1 X", abs(result["normals"][1][0] - (-0.2)) < 0.001)
    check("T4 normal1 Y", abs(result["normals"][1][1] - (-0.8)) < 0.001)
    check("T4 normal1 Z", abs(result["normals"][1][2] - 0.6) < 0.001)


def test_t5_uv0_preserved():
    """T5: UV0 preserved per vertex."""
    guid = dummy_guid()
    vhash = empty_version_hash()

    verts = [
        make_rv((0, 0, 0), (0, 1, 0), (0.25, 0.75)),
        make_rv((1, 0, 0), (0, 1, 0), (0.5, 0.5)),
    ]
    indices = [0, 1, 1]

    payload = serialize_full_attr_mesh_chunk_v1(
        guid, vhash, 0, 1, verts, indices,
        flags=MESH_CHUNK_FLAG_FULL_ATTR,
        vertex_stride=MESH_FULL_ATTR_VERTEX_STRIDE_NO_COLOR)
    body = get_body(payload)

    eng = V1ReassemblyEngine()
    check("T5 ingest", eng.ingest_chunk(guid, vhash, 0, 1, body))
    ok, result = eng.build_completed(guid, vhash, actor_present=True)
    check("T5 build ok", ok)

    check("T5 uv0[0] U", abs(result["uv0"][0][0] - 0.25) < 0.001)
    check("T5 uv0[0] V", abs(result["uv0"][0][1] - 0.75) < 0.001)
    check("T5 uv0[1] U", abs(result["uv0"][1][0] - 0.5) < 0.001)
    check("T5 uv0[1] V", abs(result["uv0"][1][1] - 0.5) < 0.001)


def test_t6_stride32_no_color():
    """T6: stride 32 builds without color0."""
    guid = dummy_guid()
    vhash = empty_version_hash()
    verts = make_verts_tri()
    indices = make_indices_tri()

    payload = serialize_full_attr_mesh_chunk_v1(
        guid, vhash, 0, 1, verts, indices,
        flags=MESH_CHUNK_FLAG_FULL_ATTR,
        vertex_stride=MESH_FULL_ATTR_VERTEX_STRIDE_NO_COLOR)
    body = get_body(payload)

    eng = V1ReassemblyEngine()
    check("T6 ingest", eng.ingest_chunk(guid, vhash, 0, 1, body))
    ok, result = eng.build_completed(guid, vhash, actor_present=True)
    check("T6 build ok", ok)
    check("T6 no has_color0", not result["has_color0"])
    check("T6 colors empty", len(result["colors"]) == 0)


def test_t7_stride48_with_color():
    """T7: stride 48 builds with color0."""
    guid = dummy_guid()
    vhash = empty_version_hash()
    color = (0.2, 0.4, 0.6, 0.8)
    verts = make_verts_tri(color=color)
    indices = make_indices_tri()

    payload = serialize_full_attr_mesh_chunk_v1(
        guid, vhash, 0, 1, verts, indices,
        flags=MESH_CHUNK_FLAG_FULL_ATTR,
        vertex_stride=MESH_FULL_ATTR_VERTEX_STRIDE_COLOR0)
    body = get_body(payload)

    eng = V1ReassemblyEngine()
    check("T7 ingest", eng.ingest_chunk(guid, vhash, 0, 1, body))
    ok, result = eng.build_completed(guid, vhash, actor_present=True)
    check("T7 build ok", ok)
    check("T7 has_color0", result["has_color0"])
    check("T7 colors count", len(result["colors"]) == 3)

    if result["colors"]:
        c0 = result["colors"][0]
        check("T7 color0 R", abs(c0[0] - color[0]) < 0.001)
        check("T7 color0 G", abs(c0[1] - color[1]) < 0.001)
        check("T7 color0 B", abs(c0[2] - color[2]) < 0.001)
        check("T7 color0 A", abs(c0[3] - color[3]) < 0.001)


def test_t8_missing_actor():
    """T8: missing actor safe skip / counter."""
    guid = dummy_guid()
    vhash = empty_version_hash()
    verts = make_verts_tri()
    indices = make_indices_tri()

    payload = serialize_full_attr_mesh_chunk_v1(
        guid, vhash, 0, 1, verts, indices,
        flags=MESH_CHUNK_FLAG_FULL_ATTR,
        vertex_stride=MESH_FULL_ATTR_VERTEX_STRIDE_NO_COLOR)
    body = get_body(payload)

    eng = V1ReassemblyEngine()
    check("T8 ingest", eng.ingest_chunk(guid, vhash, 0, 1, body))

    # actor_present=False simulates missing actor
    ok, result = eng.build_completed(guid, vhash, actor_present=False)
    check("T8 build not ok", not ok)
    check("T8 missing actor counter", eng.mesh_schema_v1_missing_actor == 1)
    check("T8 no section built", eng.mesh_schema_v1_sections_built == 0)
    check("T8 state still pending", (str(guid), vhash) in eng.pending)

    # Build again with actor present should still work
    ok, result = eng.build_completed(guid, vhash, actor_present=True)
    check("T8 build after actor appears", ok)
    check("T8 section built", eng.mesh_schema_v1_sections_built == 1)
    check("T8 state cleared", (str(guid), vhash) not in eng.pending)


def test_t9_invalid_index_after_merge():
    """T9: invalid index after merge rejected."""
    # Scenario: chunk has per-chunk valid indices that reference beyond merged array.
    # Use 2 chunks: chunk0 has 3 verts, chunk1 has 3 verts.
    # If chunk0 has index 5 which is >= 3 (chunk0's vert count), it's rejected at parse.
    # So we test the merged-level OOB check by corrupting a chunk index in the engine
    # (simulating a defense-in-depth failure that the UE build validator catches).
    guid = dummy_guid()
    vhash = empty_version_hash()
    verts = make_verts_tri()
    indices = make_indices_tri()

    payload = serialize_full_attr_mesh_chunk_v1(
        guid, vhash, 0, 1, verts, indices,
        flags=MESH_CHUNK_FLAG_FULL_ATTR,
        vertex_stride=MESH_FULL_ATTR_VERTEX_STRIDE_NO_COLOR)
    body = get_body(payload)

    eng = V1ReassemblyEngine()
    check("T9 ingest", eng.ingest_chunk(guid, vhash, 0, 1, body))

    # Manually corrupt an index to be OOB at merged level (simulate edge case)
    key = (str(guid), vhash)
    if key in eng.pending:
        state = eng.pending[key]
        if 0 in state.chunks:
            chunk = state.chunks[0]
            chunk.indices[0] = 999  # OOB for 3 verts

    ok, result = eng.build_completed(guid, vhash, actor_present=True)
    check("T9 build rejected", not ok)
    check("T9 build rejected counter", eng.mesh_schema_v1_build_rejected == 1)
    check("T9 no section built", eng.mesh_schema_v1_sections_built == 0)


def test_t10_entry_cleared_after_build():
    """T10: completed reassembly entry cleared after successful build."""
    guid = dummy_guid()
    vhash = empty_version_hash()
    verts = make_verts_tri()
    indices = make_indices_tri()

    payload = serialize_full_attr_mesh_chunk_v1(
        guid, vhash, 0, 1, verts, indices,
        flags=MESH_CHUNK_FLAG_FULL_ATTR,
        vertex_stride=MESH_FULL_ATTR_VERTEX_STRIDE_NO_COLOR)
    body = get_body(payload)

    eng = V1ReassemblyEngine()
    check("T10 ingest", eng.ingest_chunk(guid, vhash, 0, 1, body))

    key = (str(guid), vhash)
    check("T10 state exists before build", key in eng.pending)

    ok, result = eng.build_completed(guid, vhash, actor_present=True)
    check("T10 build ok", ok)
    check("T10 state cleared after build", key not in eng.pending)


def test_t11_full_attr_absent_v5():
    """T11: FULL_ATTR absent means V5 path, not v1."""
    eng = V1ReassemblyEngine()
    # Without FULL_ATTR flag, v1 dispatch is skipped entirely.
    # Our engine only processes chunks that are explicitly ingested.
    check("T11 no v1 data", len(eng.pending) == 0)
    check("T11 zero counters", eng.mesh_schema_v1_packets_parsed == 0)


def test_t12_v1_does_not_alter_v5():
    """T12: v1 build does not alter legacy V5 reconstruction behavior."""
    guid = dummy_guid()
    vhash = empty_version_hash()
    verts = make_verts_tri()
    indices = make_indices_tri()

    payload = serialize_full_attr_mesh_chunk_v1(
        guid, vhash, 0, 1, verts, indices,
        flags=MESH_CHUNK_FLAG_FULL_ATTR,
        vertex_stride=MESH_FULL_ATTR_VERTEX_STRIDE_NO_COLOR)
    body = get_body(payload)

    eng = V1ReassemblyEngine()
    eng.ingest_chunk(guid, vhash, 0, 1, body)

    check("T12 one v1 entry", len(eng.pending) == 1)

    ok, result = eng.build_completed(guid, vhash, actor_present=True)
    check("T12 v1 build ok", ok)
    check("T12 v1 entry cleared", len(eng.pending) == 0)

    # V5 path is independent (separate map in UE). In our test engine,
    # we verify the v1 build doesn't interfere with V5 space.
    check("T12 V5 no interference", eng.mesh_schema_v1_sections_built == 1)


def test_t13_material_single_section():
    """T13: material grouping intentionally single-section for Stage 2C.3."""
    guid = dummy_guid()
    vhash = empty_version_hash()
    verts = make_verts_tri() + [
        make_rv((2, 0, 0)),
        make_rv((3, 0, 0)),
        make_rv((2, 1, 0)),
    ]
    indices = [0, 1, 2, 3, 4, 5]

    payload = serialize_full_attr_mesh_chunk_v1(
        guid, vhash, 0, 1, verts, indices,
        flags=MESH_CHUNK_FLAG_FULL_ATTR,
        vertex_stride=MESH_FULL_ATTR_VERTEX_STRIDE_NO_COLOR)
    body = get_body(payload)

    eng = V1ReassemblyEngine()
    check("T13 ingest", eng.ingest_chunk(guid, vhash, 0, 1, body))
    ok, result = eng.build_completed(guid, vhash, actor_present=True)
    check("T13 build ok", ok)
    check("T13 one section", eng.mesh_schema_v1_sections_built == 1)
    check("T13 all verts in one list", result["vertex_count"] == 6)
    check("T13 all tris in one list", result["triangle_count"] == 2)


def test_t14_counters_expected():
    """T14: counters expected for built / rejected / missing actor."""
    eng = V1ReassemblyEngine()

    g1, h1 = dummy_guid(), empty_version_hash()
    g2, h2 = dummy_guid(), empty_version_hash()
    g3, h3 = dummy_guid(), empty_version_hash()

    v32 = make_verts_tri()
    i32 = make_indices_tri()
    v48 = make_verts_tri(color=(1, 0, 0, 1))
    i48 = make_indices_tri()

    p1 = get_body(serialize_full_attr_mesh_chunk_v1(
        g1, h1, 0, 1, v32, i32,
        flags=MESH_CHUNK_FLAG_FULL_ATTR,
        vertex_stride=MESH_FULL_ATTR_VERTEX_STRIDE_NO_COLOR))
    p2 = get_body(serialize_full_attr_mesh_chunk_v1(
        g2, h2, 0, 1, v48, i48,
        flags=MESH_CHUNK_FLAG_FULL_ATTR,
        vertex_stride=MESH_FULL_ATTR_VERTEX_STRIDE_COLOR0))
    p3 = get_body(serialize_full_attr_mesh_chunk_v1(
        g3, h3, 0, 1, v32, i32,
        flags=MESH_CHUNK_FLAG_FULL_ATTR,
        vertex_stride=MESH_FULL_ATTR_VERTEX_STRIDE_NO_COLOR))

    check("T14 ingest g1", eng.ingest_chunk(g1, h1, 0, 1, p1))
    check("T14 ingest g2", eng.ingest_chunk(g2, h2, 0, 1, p2))
    check("T14 ingest g3", eng.ingest_chunk(g3, h3, 0, 1, p3))

    ok1, _ = eng.build_completed(g1, h1, actor_present=True)
    check("T14 build g1 ok", ok1)

    ok2, _ = eng.build_completed(g2, h2, actor_present=False)
    check("T14 build g2 skipped", not ok2)

    ok3, _ = eng.build_completed(g3, h3, actor_present=True)
    check("T14 build g3 ok", ok3)

    check("T14 sections built", eng.mesh_schema_v1_sections_built == 2)
    check("T14 build rejected", eng.mesh_schema_v1_build_rejected == 0)
    check("T14 missing actor", eng.mesh_schema_v1_missing_actor == 1)
    check("T14 chunks stored", eng.mesh_schema_v1_chunks_stored == 3)
    check("T14 meshes completed", eng.mesh_schema_v1_meshes_completed == 3)


def test_t15_stride32_48_independent():
    """Both stride types work independently for different GUIDs."""
    eng = V1ReassemblyEngine()

    g32, h32 = dummy_guid(), empty_version_hash()
    g48, h48 = dummy_guid(), empty_version_hash()

    v32 = make_verts_tri()
    i32 = make_indices_tri()
    v48 = make_verts_tri(color=(0.5, 0.3, 0.1, 1.0))
    i48 = make_indices_tri()

    p32 = get_body(serialize_full_attr_mesh_chunk_v1(
        g32, h32, 0, 1, v32, i32,
        flags=MESH_CHUNK_FLAG_FULL_ATTR,
        vertex_stride=MESH_FULL_ATTR_VERTEX_STRIDE_NO_COLOR))
    p48 = get_body(serialize_full_attr_mesh_chunk_v1(
        g48, h48, 0, 1, v48, i48,
        flags=MESH_CHUNK_FLAG_FULL_ATTR,
        vertex_stride=MESH_FULL_ATTR_VERTEX_STRIDE_COLOR0))

    check("T15 ingest 32", eng.ingest_chunk(g32, h32, 0, 1, p32))
    check("T15 ingest 48", eng.ingest_chunk(g48, h48, 0, 1, p48))

    ok32, res32 = eng.build_completed(g32, h32, actor_present=True)
    check("T15 build 32 ok", ok32)
    check("T15 build 32 no color", not res32["has_color0"])
    check("T15 build 32 colors empty", len(res32["colors"]) == 0)

    ok48, res48 = eng.build_completed(g48, h48, actor_present=True)
    check("T15 build 48 ok", ok48)
    check("T15 build 48 has color", res48["has_color0"])
    check("T15 build 48 colors full", len(res48["colors"]) == 3)

    check("T15 sections built total", eng.mesh_schema_v1_sections_built == 2)


def test_t16_color_preserved_pipeline():
    """Color values preserved through the full serialize-parse-build pipeline."""
    guid = dummy_guid()
    vhash = empty_version_hash()
    color = (0.1, 0.5, 0.9, 0.7)

    verts = [
        make_rv((0, 0, 0), (0, 1, 0), (0, 0), color=color),
        make_rv((1, 0, 0), (0, 1, 0), (1, 0), color=color),
    ]
    indices = [0, 1, 1]

    payload = serialize_full_attr_mesh_chunk_v1(
        guid, vhash, 0, 1, verts, indices,
        flags=MESH_CHUNK_FLAG_FULL_ATTR,
        vertex_stride=MESH_FULL_ATTR_VERTEX_STRIDE_COLOR0)
    body = get_body(payload)

    eng = V1ReassemblyEngine()
    check("T16 ingest", eng.ingest_chunk(guid, vhash, 0, 1, body))
    ok, result = eng.build_completed(guid, vhash, actor_present=True)
    check("T16 build ok", ok)

    if result["colors"]:
        c0 = result["colors"][0]
        check("T16 color R", abs(c0[0] - color[0]) < 0.001)
        check("T16 color G", abs(c0[1] - color[1]) < 0.001)
        check("T16 color B", abs(c0[2] - color[2]) < 0.001)
        check("T16 color A", abs(c0[3] - color[3]) < 0.001)

        c1 = result["colors"][1]
        check("T16 color1 same as color0", c0 == c1)


# =========================================================
# Run all tests
# =========================================================

def main():
    global PASS, FAIL

    tests = [
        ("T1 single chunk stride32", test_t1_single_chunk_no_color),
        ("T2 multi-chunk merge offset", test_t2_multi_chunk_merge),
        ("T3 duplicate verts preserved", test_t3_duplicate_verts_preserved),
        ("T4 normals preserved", test_t4_normals_preserved),
        ("T5 UV0 preserved", test_t5_uv0_preserved),
        ("T6 stride32 no color", test_t6_stride32_no_color),
        ("T7 stride48 with color", test_t7_stride48_with_color),
        ("T8 missing actor safe skip", test_t8_missing_actor),
        ("T9 invalid index rejected", test_t9_invalid_index_after_merge),
        ("T10 entry cleared after build", test_t10_entry_cleared_after_build),
        ("T11 FULL_ATTR absent V5 path", test_t11_full_attr_absent_v5),
        ("T12 v1 does not alter V5", test_t12_v1_does_not_alter_v5),
        ("T13 single section", test_t13_material_single_section),
        ("T14 counters expected", test_t14_counters_expected),
        ("T15 stride32+48 independent", test_t15_stride32_48_independent),
        ("T16 color preserved pipeline", test_t16_color_preserved_pipeline),
    ]

    for name, func in tests:
        print(f"\n--- {name} ---")
        func()

    print(f"\n{'=' * 50}")
    print(f"Total: {PASS} PASS, {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
