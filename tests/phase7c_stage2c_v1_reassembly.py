#!/usr/bin/env python3
"""
Phase 7C Stage 2C.2 — UE FULL_ATTR v1 reassembly tests.

Tests mirror the UE-side reassembly logic in Python:
  - Parse V1 chunk payload → store in reassembly state keyed by (Guid, VersionHash)
  - Validate consistency (ChunkCount, VertexStride)
  - Reject duplicates and mismatches
  - Track completion
  - Counters: ChunksStored, MeshesCompleted, DuplicateChunks, ReassemblyRejected
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
# Reassembly engine — mirrors UE-side logic
# =========================================================

def ue_parse_v1_payload(data, chunk_index, chunk_count):
    """Mirrors UE ParseV1MeshPayload rules.

    Returns (ok: bool, info: dict) where info contains parsed fields
    on success or error message on failure.
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
        return False, {"error": f"truncated vertex payload: need {vertex_bytes}, have {data_len - offset}"}

    # Parse vertices
    has_color0 = vertex_stride == 48
    vertices = []
    for _ in range(vertex_count):
        px, py, pz = struct.unpack_from("<fff", data, offset)
        nx, ny, nz = struct.unpack_from("<fff", data, offset + 12)
        u, v = struct.unpack_from("<ff", data, offset + 24)
        if has_color0:
            cr, cg, cb, ca = struct.unpack_from("<ffff", data, offset + 32)
            color0 = (cr, cg, cb, ca)
        else:
            color0 = (0, 0, 0, 0)
        vertices.append({
            "position": (px, py, pz),
            "normal": (nx, ny, nz),
            "uv0": (u, v),
            "color0": color0,
        })
        offset += vertex_stride

    if offset + 4 > data_len:
        return False, {"error": "truncated IndexCount"}
    index_count = struct.unpack_from("<I", data, offset)[0]
    offset += 4

    index_bytes = index_count * 4
    if offset + index_bytes > data_len:
        return False, {"error": f"truncated index payload: need {index_bytes}, have {data_len - offset}"}

    indices = []
    for i in range(index_count):
        idx = struct.unpack_from("<I", data, offset + i * 4)[0]
        if idx >= vertex_count:
            return False, {"error": f"out-of-bounds index {idx} >= {vertex_count}"}
        indices.append(idx)

    info = {
        "vertices": vertices,
        "indices": indices,
        "vertex_stride": vertex_stride,
        "vertex_count": vertex_count,
        "index_count": index_count,
        "chunk_index": chunk_index,
        "chunk_count": chunk_count,
    }
    if chunk_index == 0:
        info["schema_version"] = 1

    return True, info


class V1ReassemblyState:
    """Mirrors UE FV1MeshReassemblyState."""
    def __init__(self):
        self.chunk_count = 0
        self.vertex_stride = 0
        self.chunks_received = 0
        self.chunks = {}  # chunk_index -> parsed info

    def is_complete(self):
        return self.chunk_count > 0 and self.chunks_received >= self.chunk_count

    def has_chunk(self, chunk_index):
        return chunk_index in self.chunks


class V1ReassemblyEngine:
    """Mirrors UE-side FULL_ATTR reassembly dispatch logic."""

    def __init__(self):
        self.states = {}  # (guid_str, version_hash) -> V1ReassemblyState
        self.counters = {
            "chunks_stored": 0,
            "meshes_completed": 0,
            "duplicate_chunks": 0,
            "reassembly_rejected": 0,
        }

    def process_chunk(self, guid_str, version_hash, chunk_index, chunk_count, body):
        """Process one v1 chunk payload. Returns (accepted, info)."""
        ok, info = ue_parse_v1_payload(body, chunk_index, chunk_count)
        if not ok:
            return False, info

        key = (guid_str, version_hash)
        if key not in self.states:
            self.states[key] = V1ReassemblyState()

        state = self.states[key]

        if state.chunk_count == 0:
            state.chunk_count = info["chunk_count"]
            state.vertex_stride = info["vertex_stride"]
        else:
            mismatched = False
            if state.chunk_count != info["chunk_count"]:
                mismatched = True
            if state.vertex_stride != info["vertex_stride"]:
                mismatched = True
            if mismatched:
                self.counters["reassembly_rejected"] += 1
                return False, {"error": "consistency mismatch"}

        if state.has_chunk(chunk_index):
            self.counters["duplicate_chunks"] += 1
            return False, {"error": "duplicate chunk"}

        state.chunks[chunk_index] = info
        state.chunks_received += 1
        self.counters["chunks_stored"] += 1

        if state.is_complete():
            self.counters["meshes_completed"] += 1

        return True, info


# =========================================================
# Helpers
# =========================================================

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


def dummy_guid():
    return uuid.uuid4()


def empty_version_hash():
    return "a" * 64


def get_body(payload):
    """Strip 89-byte mesh header to get the v1 chunk payload body."""
    return payload[89:]


# =========================================================
# T1: Single chunk stride32 — reassembly complete immediately
# =========================================================

def test_t1():
    guid = dummy_guid()
    vhash = empty_version_hash()
    verts = make_verts_tri()
    indices = make_indices_tri()

    payload = serialize_full_attr_mesh_chunk_v1(
        guid, vhash, 0, 1, verts, indices,
        flags=MESH_CHUNK_FLAG_FULL_ATTR,
        vertex_stride=MESH_FULL_ATTR_VERTEX_STRIDE_NO_COLOR)

    body = get_body(payload)
    engine = V1ReassemblyEngine()
    ok, info = engine.process_chunk(str(guid), vhash, 0, 1, body)

    check("T1: single chunk accepted", ok)
    check("T1: chunks_stored=1", engine.counters["chunks_stored"] == 1)
    check("T1: meshes_completed=1", engine.counters["meshes_completed"] == 1)
    check("T1: no duplicates", engine.counters["duplicate_chunks"] == 0)
    check("T1: no rejections", engine.counters["reassembly_rejected"] == 0)


# =========================================================
# T2: Multi-chunk stride32 — complete on second chunk
# =========================================================

def test_t2():
    guid = dummy_guid()
    vhash = empty_version_hash()
    verts_a = make_verts_tri()
    indices_a = [0, 1, 2]
    verts_b = [
        make_rv((2, 0, 0)),
        make_rv((3, 0, 0)),
        make_rv((2, 1, 0)),
    ]
    indices_b = [0, 1, 2]

    payload0 = serialize_full_attr_mesh_chunk_v1(
        guid, vhash, 0, 2, verts_a, indices_a,
        flags=MESH_CHUNK_FLAG_FULL_ATTR,
        vertex_stride=MESH_FULL_ATTR_VERTEX_STRIDE_NO_COLOR)

    payload1 = serialize_full_attr_mesh_chunk_v1(
        guid, vhash, 1, 2, verts_b, indices_b,
        flags=MESH_CHUNK_FLAG_FULL_ATTR,
        vertex_stride=MESH_FULL_ATTR_VERTEX_STRIDE_NO_COLOR)

    engine = V1ReassemblyEngine()

    ok0, _ = engine.process_chunk(str(guid), vhash, 0, 2, get_body(payload0))
    check("T2: chunk0 accepted", ok0)
    check("T2: not yet complete after chunk0", not engine.states[(str(guid), vhash)].is_complete())
    check("T2: meshes_completed=0 after chunk0", engine.counters["meshes_completed"] == 0)

    ok1, _ = engine.process_chunk(str(guid), vhash, 1, 2, get_body(payload1))
    check("T2: chunk1 accepted", ok1)
    check("T2: complete after chunk1", engine.states[(str(guid), vhash)].is_complete())
    check("T2: meshes_completed=1 after chunk1", engine.counters["meshes_completed"] == 1)
    check("T2: chunks_stored=2", engine.counters["chunks_stored"] == 2)


# =========================================================
# T3: Single chunk stride48 (color0)
# =========================================================

def test_t3():
    guid = dummy_guid()
    vhash = empty_version_hash()
    color = (1.0, 0.5, 0.0, 0.75)
    verts = make_verts_tri(color=color)
    indices = make_indices_tri()

    payload = serialize_full_attr_mesh_chunk_v1(
        guid, vhash, 0, 1, verts, indices,
        flags=MESH_CHUNK_FLAG_FULL_ATTR,
        vertex_stride=MESH_FULL_ATTR_VERTEX_STRIDE_COLOR0)

    engine = V1ReassemblyEngine()
    ok, info = engine.process_chunk(str(guid), vhash, 0, 1, get_body(payload))

    check("T3: stride48 chunk accepted", ok)
    check("T3: meshes_completed=1", engine.counters["meshes_completed"] == 1)
    if ok:
        v0 = info["vertices"][0]
        check("T3: color0 preserved", v0["color0"] == color)


# =========================================================
# T4: Multi-chunk stride48
# =========================================================

def test_t4():
    guid = dummy_guid()
    vhash = empty_version_hash()
    color = (0.0, 1.0, 0.0, 1.0)
    verts_a = make_verts_tri(color=color)
    indices_a = [0, 1, 2]
    verts_b = [make_rv((5, 0, 0), color=color)]
    indices_b = [0, 0, 0]

    payload0 = serialize_full_attr_mesh_chunk_v1(
        guid, vhash, 0, 2, verts_a, indices_a,
        flags=MESH_CHUNK_FLAG_FULL_ATTR,
        vertex_stride=MESH_FULL_ATTR_VERTEX_STRIDE_COLOR0)

    payload1 = serialize_full_attr_mesh_chunk_v1(
        guid, vhash, 1, 2, verts_b, indices_b,
        flags=MESH_CHUNK_FLAG_FULL_ATTR,
        vertex_stride=MESH_FULL_ATTR_VERTEX_STRIDE_COLOR0)

    engine = V1ReassemblyEngine()

    ok0, _ = engine.process_chunk(str(guid), vhash, 0, 2, get_body(payload0))
    check("T4: chunk0 stride48 accepted", ok0)
    check("T4: incomplete after chunk0", not engine.states[(str(guid), vhash)].is_complete())

    ok1, _ = engine.process_chunk(str(guid), vhash, 1, 2, get_body(payload1))
    check("T4: chunk1 stride48 accepted", ok1)
    check("T4: complete after chunk1", engine.states[(str(guid), vhash)].is_complete())
    check("T4: meshes_completed=1", engine.counters["meshes_completed"] == 1)
    check("T4: chunks_stored=2", engine.counters["chunks_stored"] == 2)

    if ok0 and ok1:
        v0 = engine.states[(str(guid), vhash)].chunks[0]["vertices"][0]
        check("T4: color0 preserved across chunks", v0["color0"] == color)


# =========================================================
# T5: Duplicate chunk index rejected
# =========================================================

def test_t5():
    guid = dummy_guid()
    vhash = empty_version_hash()
    verts = make_verts_tri()
    indices = make_indices_tri()

    payload = serialize_full_attr_mesh_chunk_v1(
        guid, vhash, 0, 2, verts, indices,
        flags=MESH_CHUNK_FLAG_FULL_ATTR,
        vertex_stride=MESH_FULL_ATTR_VERTEX_STRIDE_NO_COLOR)

    engine = V1ReassemblyEngine()

    ok_first, _ = engine.process_chunk(str(guid), vhash, 0, 2, get_body(payload))
    check("T5: first chunk accepted", ok_first)
    check("T5: chunks_stored=1", engine.counters["chunks_stored"] == 1)

    ok_dup, _ = engine.process_chunk(str(guid), vhash, 0, 2, get_body(payload))
    check("T5: duplicate chunk rejected", not ok_dup)
    check("T5: duplicate_chunks=1", engine.counters["duplicate_chunks"] == 1)
    check("T5: chunks_stored still=1", engine.counters["chunks_stored"] == 1)


# =========================================================
# T6: ChunkCount mismatch rejected
# =========================================================

def test_t6():
    guid = dummy_guid()
    vhash = empty_version_hash()
    verts = make_verts_tri()
    indices = make_indices_tri()

    payload0 = serialize_full_attr_mesh_chunk_v1(
        guid, vhash, 0, 2, verts, indices,
        flags=MESH_CHUNK_FLAG_FULL_ATTR,
        vertex_stride=MESH_FULL_ATTR_VERTEX_STRIDE_NO_COLOR)

    # Same GUID/version but claims ChunkCount=3 vs 2
    payload1 = serialize_full_attr_mesh_chunk_v1(
        guid, vhash, 1, 3, verts, indices,
        flags=MESH_CHUNK_FLAG_FULL_ATTR,
        vertex_stride=MESH_FULL_ATTR_VERTEX_STRIDE_NO_COLOR)

    engine = V1ReassemblyEngine()

    ok0, _ = engine.process_chunk(str(guid), vhash, 0, 2, get_body(payload0))
    check("T6: first chunk accepted", ok0)

    ok1, _ = engine.process_chunk(str(guid), vhash, 1, 3, get_body(payload1))
    check("T6: mismatched ChunkCount rejected", not ok1)
    check("T6: reassembly_rejected=1", engine.counters["reassembly_rejected"] == 1)
    check("T6: chunks_stored=1", engine.counters["chunks_stored"] == 1)


# =========================================================
# T7: Stride mismatch rejected
# =========================================================

def test_t7():
    guid = dummy_guid()
    vhash = empty_version_hash()
    color = (1.0, 0.0, 0.0, 1.0)
    verts_nc = make_verts_tri()
    verts_c = make_verts_tri(color=color)
    indices = make_indices_tri()

    payload0 = serialize_full_attr_mesh_chunk_v1(
        guid, vhash, 0, 2, verts_nc, indices,
        flags=MESH_CHUNK_FLAG_FULL_ATTR,
        vertex_stride=MESH_FULL_ATTR_VERTEX_STRIDE_NO_COLOR)

    # Same GUID/version but stride 48 instead of 32
    payload1 = serialize_full_attr_mesh_chunk_v1(
        guid, vhash, 1, 2, verts_c, indices,
        flags=MESH_CHUNK_FLAG_FULL_ATTR,
        vertex_stride=MESH_FULL_ATTR_VERTEX_STRIDE_COLOR0)

    engine = V1ReassemblyEngine()

    ok0, _ = engine.process_chunk(str(guid), vhash, 0, 2, get_body(payload0))
    check("T7: first chunk stride32 accepted", ok0)

    ok1, _ = engine.process_chunk(str(guid), vhash, 1, 2, get_body(payload1))
    check("T7: mismatched stride rejected", not ok1)
    check("T7: reassembly_rejected=1", engine.counters["reassembly_rejected"] == 1)
    check("T7: chunks_stored=1", engine.counters["chunks_stored"] == 1)


# =========================================================
# T8: Incomplete when missing chunk
# =========================================================

def test_t8():
    guid = dummy_guid()
    vhash = empty_version_hash()
    verts = make_verts_tri()
    indices = make_indices_tri()

    payload0 = serialize_full_attr_mesh_chunk_v1(
        guid, vhash, 0, 3, verts, indices,
        flags=MESH_CHUNK_FLAG_FULL_ATTR,
        vertex_stride=MESH_FULL_ATTR_VERTEX_STRIDE_NO_COLOR)

    payload2 = serialize_full_attr_mesh_chunk_v1(
        guid, vhash, 2, 3, verts, indices,
        flags=MESH_CHUNK_FLAG_FULL_ATTR,
        vertex_stride=MESH_FULL_ATTR_VERTEX_STRIDE_NO_COLOR)

    engine = V1ReassemblyEngine()

    ok0, _ = engine.process_chunk(str(guid), vhash, 0, 3, get_body(payload0))
    check("T8: chunk0 accepted", ok0)

    ok2, _ = engine.process_chunk(str(guid), vhash, 2, 3, get_body(payload2))
    check("T8: chunk2 accepted", ok2)

    state = engine.states[(str(guid), vhash)]
    check("T8: not complete (missing chunk1)", not state.is_complete())
    check("T8: chunks_stored=2", engine.counters["chunks_stored"] == 2)
    check("T8: meshes_completed=0", engine.counters["meshes_completed"] == 0)


# =========================================================
# T9: Parse failure (bad schema version) → rejected
# =========================================================

def test_t9():
    guid = dummy_guid()
    vhash = empty_version_hash()
    verts = make_verts_tri()
    indices = make_indices_tri()

    payload = serialize_full_attr_mesh_chunk_v1(
        guid, vhash, 0, 1, verts, indices,
        flags=MESH_CHUNK_FLAG_FULL_ATTR,
        vertex_stride=MESH_FULL_ATTR_VERTEX_STRIDE_NO_COLOR)

    body = bytearray(get_body(payload))
    # Corrupt SchemaVersion to 99 (invalid)
    struct.pack_into("<I", body, 0, 99)

    engine = V1ReassemblyEngine()
    ok, info = engine.process_chunk(str(guid), vhash, 0, 1, bytes(body))

    check("T9: bad schema version rejected", not ok)
    check("T9: meshes_completed=0", engine.counters["meshes_completed"] == 0)
    check("T9: chunks_stored=0", engine.counters["chunks_stored"] == 0)
    if not ok:
        check("T9: error mentions schema",
              "schema" in info.get("error", "").lower() or "SchemaVersion" in info.get("error", ""))


# =========================================================
# T10: Multiple independent reassemblies
# =========================================================

def test_t10():
    verts = make_verts_tri()
    indices = make_indices_tri()

    # Two different GUIDs, each with single chunks
    guid_a = dummy_guid()
    guid_b = dummy_guid()
    vhash = empty_version_hash()

    payload_a = serialize_full_attr_mesh_chunk_v1(
        guid_a, vhash, 0, 1, verts, indices,
        flags=MESH_CHUNK_FLAG_FULL_ATTR,
        vertex_stride=MESH_FULL_ATTR_VERTEX_STRIDE_NO_COLOR)

    payload_b = serialize_full_attr_mesh_chunk_v1(
        guid_b, vhash, 0, 1, verts, indices,
        flags=MESH_CHUNK_FLAG_FULL_ATTR,
        vertex_stride=MESH_FULL_ATTR_VERTEX_STRIDE_NO_COLOR)

    engine = V1ReassemblyEngine()

    ok_a, _ = engine.process_chunk(str(guid_a), vhash, 0, 1, get_body(payload_a))
    ok_b, _ = engine.process_chunk(str(guid_b), vhash, 0, 1, get_body(payload_b))

    check("T10: guid_a accepted", ok_a)
    check("T10: guid_b accepted", ok_b)
    check("T10: 2 independent reassemblies",
          engine.counters["meshes_completed"] == 2)
    check("T10: chunks_stored=2", engine.counters["chunks_stored"] == 2)

    # Same GUID, different version hash is a separate reassembly
    vhash2 = "b" * 64
    payload_c = serialize_full_attr_mesh_chunk_v1(
        guid_a, vhash2, 0, 1, verts, indices,
        flags=MESH_CHUNK_FLAG_FULL_ATTR,
        vertex_stride=MESH_FULL_ATTR_VERTEX_STRIDE_NO_COLOR)

    ok_c, _ = engine.process_chunk(str(guid_a), vhash2, 0, 1, get_body(payload_c))
    check("T10: same GUID different version is separate reassembly", ok_c)
    check("T10: meshes_completed=3", engine.counters["meshes_completed"] == 3)
    check("T10: chunks_stored=3", engine.counters["chunks_stored"] == 3)


# =========================================================
# T11: Parse failure (bad stride) → rejected, no reassembly
# =========================================================

def test_t11():
    guid = dummy_guid()
    vhash = empty_version_hash()
    verts = make_verts_tri()
    indices = make_indices_tri()

    payload = serialize_full_attr_mesh_chunk_v1(
        guid, vhash, 0, 1, verts, indices,
        flags=MESH_CHUNK_FLAG_FULL_ATTR,
        vertex_stride=MESH_FULL_ATTR_VERTEX_STRIDE_NO_COLOR)

    body = bytearray(get_body(payload))
    # Corrupt stride to 99
    struct.pack_into("<I", body, 4, 99)

    engine = V1ReassemblyEngine()
    ok, _ = engine.process_chunk(str(guid), vhash, 0, 1, bytes(body))

    check("T11: bad stride rejected", not ok)
    check("T11: meshes_completed=0", engine.counters["meshes_completed"] == 0)
    check("T11: chunks_stored=0", engine.counters["chunks_stored"] == 0)


# =========================================================
# T12: Counter validation — all 4 counters correct
# =========================================================

def test_t12():
    engine = V1ReassemblyEngine()

    # Process a mix of valid, duplicate, and mismatched chunks
    guid_a = dummy_guid()
    guid_b = dummy_guid()
    guid_c = dummy_guid()
    vhash = empty_version_hash()
    verts = make_verts_tri()
    indices = make_indices_tri()

    # Session 1: valid single-chunk for guid_a
    pa0 = serialize_full_attr_mesh_chunk_v1(
        guid_a, vhash, 0, 1, verts, indices,
        flags=MESH_CHUNK_FLAG_FULL_ATTR,
        vertex_stride=MESH_FULL_ATTR_VERTEX_STRIDE_NO_COLOR)
    engine.process_chunk(str(guid_a), vhash, 0, 1, get_body(pa0))

    # Session 2: valid multi-chunk for guid_b
    pb0 = serialize_full_attr_mesh_chunk_v1(
        guid_b, vhash, 0, 2, verts, indices,
        flags=MESH_CHUNK_FLAG_FULL_ATTR,
        vertex_stride=MESH_FULL_ATTR_VERTEX_STRIDE_NO_COLOR)
    pb1 = serialize_full_attr_mesh_chunk_v1(
        guid_b, vhash, 1, 2, verts, indices,
        flags=MESH_CHUNK_FLAG_FULL_ATTR,
        vertex_stride=MESH_FULL_ATTR_VERTEX_STRIDE_NO_COLOR)
    engine.process_chunk(str(guid_b), vhash, 0, 2, get_body(pb0))
    engine.process_chunk(str(guid_b), vhash, 1, 2, get_body(pb1))

    # Session 3: duplicate chunk
    engine.process_chunk(str(guid_b), vhash, 0, 2, get_body(pb0))

    # Session 4: ChunkCount mismatch
    pc0 = serialize_full_attr_mesh_chunk_v1(
        guid_c, vhash, 0, 2, verts, indices,
        flags=MESH_CHUNK_FLAG_FULL_ATTR,
        vertex_stride=MESH_FULL_ATTR_VERTEX_STRIDE_NO_COLOR)
    pc1 = serialize_full_attr_mesh_chunk_v1(
        guid_c, vhash, 1, 3, verts, indices,
        flags=MESH_CHUNK_FLAG_FULL_ATTR,
        vertex_stride=MESH_FULL_ATTR_VERTEX_STRIDE_NO_COLOR)
    engine.process_chunk(str(guid_c), vhash, 0, 2, get_body(pc0))
    engine.process_chunk(str(guid_c), vhash, 1, 3, get_body(pc1))

    check("T12: chunks_stored=4 (a:1 + b:2 + c:1)", engine.counters["chunks_stored"] == 4)
    check("T12: meshes_completed=2 (a:1 + b:1)", engine.counters["meshes_completed"] == 2)
    check("T12: duplicate_chunks=1", engine.counters["duplicate_chunks"] == 1)
    check("T12: reassembly_rejected=1", engine.counters["reassembly_rejected"] == 1)


# =========================================================
# Main
# =========================================================

def main():
    print("=" * 72)
    print("Phase 7C Stage 2C.2 — UE FULL_ATTR v1 reassembly tests")
    print("=" * 72)

    tests = [
        ("T1: single chunk stride32", test_t1),
        ("T2: multi-chunk stride32", test_t2),
        ("T3: single chunk stride48", test_t3),
        ("T4: multi-chunk stride48", test_t4),
        ("T5: duplicate chunk rejected", test_t5),
        ("T6: ChunkCount mismatch rejected", test_t6),
        ("T7: stride mismatch rejected", test_t7),
        ("T8: incomplete when missing chunk", test_t8),
        ("T9: bad schema version rejected", test_t9),
        ("T10: multiple independent reassemblies", test_t10),
        ("T11: bad stride rejected", test_t11),
        ("T12: counter validation", test_t12),
    ]

    for name, func in tests:
        print(f"\n--- {name} ---")
        func()

    global PASS, FAIL
    total = PASS + FAIL
    print(f"\n{'=' * 72}")
    print(f"Results: {PASS}/{total} PASS, {FAIL}/{total} FAIL")
    print(f"{'=' * 72}")

    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
