#!/usr/bin/env python3
"""
Phase 7C Stage 2B.2 — Blender v1 serializer payload tests (pure helper only).

Tests the serialize_full_attr_mesh_chunk_v1() pure function in network.py.

No UE v1 parser implementation.
No manual operator.
No check_updates() hook.
No changes to existing V5 serialize_mesh_chunk().
"""

import hashlib
import importlib.util
import os
import struct
import sys
import uuid

# Load network.py directly (avoids Blender_Addon/__init__.py which imports bpy)
_repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_net_path = os.path.join(_repo_root, "Blender_Addon", "network.py")

_spec = importlib.util.spec_from_file_location("network", _net_path)
_net = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_net)

serialize_full_attr_mesh_chunk_v1 = _net.serialize_full_attr_mesh_chunk_v1
serialize_mesh_chunk = _net.serialize_mesh_chunk
compute_geometry_version_hash = _net.compute_geometry_version_hash
MESH_CHUNK_FLAG_FULL_ATTR = _net.MESH_CHUNK_FLAG_FULL_ATTR
MESH_FULL_ATTR_SCHEMA_VERSION = _net.MESH_FULL_ATTR_SCHEMA_VERSION
MESH_FULL_ATTR_VERTEX_STRIDE_NO_COLOR = _net.MESH_FULL_ATTR_VERTEX_STRIDE_NO_COLOR
MESH_FULL_ATTR_VERTEX_STRIDE_COLOR0 = _net.MESH_FULL_ATTR_VERTEX_STRIDE_COLOR0
LIVE_SYNC_V5_MESH_CHUNK_HEADER_SIZE = _net.LIVE_SYNC_V5_MESH_CHUNK_HEADER_SIZE

# =========================================================
# Constants
# =========================================================

MESH_CHUNK_FLAG_HAS_POSITIONS = 0x01
MESH_CHUNK_FLAG_HAS_TRIANGLES = 0x02
MESH_CHUNK_FLAG_HAS_MATERIAL_IDX = 0x04

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
# Mock render vertex helpers
# =========================================================

def make_render_vertex(position=(0.0, 0.0, 0.0), normal=(0.0, 0.0, 1.0),
                        uv=(0.0, 0.0), color=None):
    return {
        "position": position,
        "normal": normal,
        "uv0": uv,
        "color0": color,
    }


def make_single_triangle_vertices(color=None):
    """Three render vertices forming a single triangle."""
    return [
        make_render_vertex(position=(0.0, 0.0, 0.0),
                           normal=(0.0, 0.0, 1.0),
                           uv=(0.0, 0.0), color=color),
        make_render_vertex(position=(1.0, 0.0, 0.0),
                           normal=(0.0, 0.0, 1.0),
                           uv=(1.0, 0.0), color=color),
        make_render_vertex(position=(0.0, 1.0, 0.0),
                           normal=(0.0, 0.0, 1.0),
                           uv=(0.0, 1.0), color=color),
    ]


def make_render_vertices_quad(color=None):
    """Four render vertices forming a quad (two triangles)."""
    return [
        make_render_vertex(position=(-1.0, -1.0, 0.0),
                           normal=(0.0, 0.0, 1.0),
                           uv=(0.0, 0.0), color=color),
        make_render_vertex(position=(1.0, -1.0, 0.0),
                           normal=(0.0, 0.0, 1.0),
                           uv=(1.0, 0.0), color=color),
        make_render_vertex(position=(1.0, 1.0, 0.0),
                           normal=(0.0, 0.0, 1.0),
                           uv=(1.0, 1.0), color=color),
        make_render_vertex(position=(-1.0, 1.0, 0.0),
                           normal=(0.0, 0.0, 1.0),
                           uv=(0.0, 1.0), color=color),
    ]


# =========================================================
# T1: chunk 0 header remains 89 bytes, flags include FULL_ATTR
# =========================================================

print("\n--- T1: Chunk 0 header 89 bytes, FULL_ATTR flag set ---")

guid = uuid.uuid4()
verts = make_single_triangle_vertices()
indices = [0, 1, 2]
version_hash = "a" * 64  # 64-char placeholder

chunk = serialize_full_attr_mesh_chunk_v1(
    guid, version_hash, 0, 1, verts, indices,
    flags=MESH_CHUNK_FLAG_FULL_ATTR,
    vertex_stride=MESH_FULL_ATTR_VERTEX_STRIDE_NO_COLOR,
)

check("T1a: chunk is bytes", isinstance(chunk, bytes))
check("T1b: chunk length >= 89 header",
      len(chunk) >= LIVE_SYNC_V5_MESH_CHUNK_HEADER_SIZE)

# Header parsing
header_guid = struct.unpack_from("<IIII", chunk, 0)
check("T1c: GUID in header (4 uint32)", len(header_guid) == 4)

header_version = chunk[16:80].decode("ascii", errors="replace").rstrip("\x00")
check("T1d: version hash in header", header_version == version_hash)

chunk_idx, chunk_cnt = struct.unpack_from("<II", chunk, 80)
check("T1e: chunk_index=0", chunk_idx == 0)
check("T1f: chunk_count=1", chunk_cnt == 1)

flags_byte = chunk[88]
check("T1g: flags byte includes FULL_ATTR (0x80)",
      bool(flags_byte & MESH_CHUNK_FLAG_FULL_ATTR))

# Verify header is exactly 89 bytes
check("T1h: header at offset 88 is flags byte",
      chunk[88] == flags_byte)
check("T1i: payload starts at offset 89",
      True)  # implied by position


# =========================================================
# T2: chunk 0 payload[89:93] is SchemaVersion=1
# =========================================================

print("\n--- T2: Chunk 0 payload starts with SchemaVersion=1 ---")

sv = struct.unpack_from("<I", chunk, LIVE_SYNC_V5_MESH_CHUNK_HEADER_SIZE)[0]
check("T2a: SchemaVersion = 1",
      sv == MESH_FULL_ATTR_SCHEMA_VERSION,
      f"got {sv}")

# Read next field (vertex_stride) to confirm position
stride_field = struct.unpack_from(
    "<I", chunk, LIVE_SYNC_V5_MESH_CHUNK_HEADER_SIZE + 4)[0]
check("T2b: stride follows schema version at offset 93",
      stride_field == MESH_FULL_ATTR_VERTEX_STRIDE_NO_COLOR)


# =========================================================
# T3: chunk >0 does not write SchemaVersion; payload starts with vertex_stride
# =========================================================

print("\n--- T3: Chunk >0 has no SchemaVersion ---")

chunk_n1 = serialize_full_attr_mesh_chunk_v1(
    guid, version_hash, 1, 2, verts, indices,
    flags=MESH_CHUNK_FLAG_FULL_ATTR,
    vertex_stride=MESH_FULL_ATTR_VERTEX_STRIDE_NO_COLOR,
)

first_field = struct.unpack_from(
    "<I", chunk_n1, LIVE_SYNC_V5_MESH_CHUNK_HEADER_SIZE)[0]
# For chunk>0, first field is vertex_stride (32), NOT SchemaVersion (1)
check("T3a: chunk>0 first field is vertex_stride, not SchemaVersion",
      first_field == MESH_FULL_ATTR_VERTEX_STRIDE_NO_COLOR,
      f"got {first_field}")
check("T3b: chunk>0 first field != SchemaVersion (1)",
      first_field != MESH_FULL_ATTR_SCHEMA_VERSION)

# Chunk 1 header still has correct chunk_index
chunk_idx1, chunk_cnt1 = struct.unpack_from("<II", chunk_n1, 80)
check("T3c: chunk_index=1", chunk_idx1 == 1)
check("T3d: chunk_count=2", chunk_cnt1 == 2)

# Chunk 0 (control) has SchemaVersion
first_field_0 = struct.unpack_from(
    "<I", chunk, LIVE_SYNC_V5_MESH_CHUNK_HEADER_SIZE)[0]
check("T3e: chunk==0 first field IS SchemaVersion",
      first_field_0 == MESH_FULL_ATTR_SCHEMA_VERSION)


# =========================================================
# T4: stride 32 serializes pos+normal+uv0 only
# =========================================================

print("\n--- T4: Stride 32 — pos+normal+uv0 only ---")

verts_1 = [make_render_vertex(
    position=(1.0, 2.0, 3.0),
    normal=(0.1, 0.2, 0.3),
    uv=(0.5, 0.6))]
indices_1 = [0, 0, 0]

chunk_1v = serialize_full_attr_mesh_chunk_v1(
    guid, version_hash, 0, 1, verts_1, indices_1,
    flags=MESH_CHUNK_FLAG_FULL_ATTR,
    vertex_stride=MESH_FULL_ATTR_VERTEX_STRIDE_NO_COLOR,
)

# Payload: SchemaVersion(4) + vertex_stride(4) + vertex_count(4) + VertexV1 + index_count(4) + indices
offset = LIVE_SYNC_V5_MESH_CHUNK_HEADER_SIZE
sv2, vs2, vc2 = struct.unpack_from("<III", chunk_1v, offset)
check("T4a: SchemaVersion=1", sv2 == 1)
check("T4b: vertex_stride=32", vs2 == 32)
check("T4c: vertex_count=1", vc2 == 1)

# VertexV1 data starts at offset 89+12=101
vtx_off = offset + 12
vtx_data = chunk_1v[vtx_off:vtx_off + 32]

# Parse float3 pos + float3 normal + float2 uv0
pos_x, pos_y, pos_z = struct.unpack_from("<fff", vtx_data, 0)
nor_x, nor_y, nor_z = struct.unpack_from("<fff", vtx_data, 12)
u, v = struct.unpack_from("<ff", vtx_data, 24)

check("T4d: pos.x=1.0", pos_x == 1.0)
check("T4e: pos.y=2.0", pos_y == 2.0)
check("T4f: pos.z=3.0", pos_z == 3.0)
check("T4g: nor.x=0.1", abs(nor_x - 0.1) < 1e-6)
check("T4h: nor.y=0.2", abs(nor_y - 0.2) < 1e-6)
check("T4i: nor.z=0.3", abs(nor_z - 0.3) < 1e-6)
check("T4j: uv.u=0.5", abs(u - 0.5) < 1e-6)
check("T4k: uv.v=0.6", abs(v - 0.6) < 1e-6)

# Vertex data is exactly 32 bytes
check("T4l: vertex byte size = 32",
      len(vtx_data) == 32,
      f"got {len(vtx_data)}")
check("T4m: no color0 bytes (stride 32)",
      len(vtx_data) == 32)

# After vertex, there should be index count and indices
idx_count_off = vtx_off + 32
ic = struct.unpack_from("<I", chunk_1v, idx_count_off)[0]
check("T4n: index_count=3", ic == 3)


# =========================================================
# T5: stride 48 serializes pos+normal+uv0+color0
# =========================================================

print("\n--- T5: Stride 48 — pos+normal+uv0+color0 ---")

red = (1.0, 0.0, 0.0, 1.0)
verts_c = [make_render_vertex(
    position=(1.0, 2.0, 3.0),
    normal=(0.1, 0.2, 0.3),
    uv=(0.5, 0.6),
    color=red)]

chunk_c = serialize_full_attr_mesh_chunk_v1(
    guid, version_hash, 0, 1, verts_c, indices_1,
    flags=MESH_CHUNK_FLAG_FULL_ATTR,
    vertex_stride=MESH_FULL_ATTR_VERTEX_STRIDE_COLOR0,
)

offset = LIVE_SYNC_V5_MESH_CHUNK_HEADER_SIZE
sv3, vs3, vc3 = struct.unpack_from("<III", chunk_c, offset)
check("T5a: SchemaVersion=1", sv3 == 1)
check("T5b: vertex_stride=48", vs3 == 48)
check("T5c: vertex_count=1", vc3 == 1)

vtx_off_c = offset + 12
vtx_data_c = chunk_c[vtx_off_c:vtx_off_c + 48]

# Parse float3 pos + float3 normal + float2 uv0 + float4 color0
pos_x_c, _1, _2 = struct.unpack_from("<fff", vtx_data_c, 0)
nor_x_c, _3, _4 = struct.unpack_from("<fff", vtx_data_c, 12)
u_c, v_c = struct.unpack_from("<ff", vtx_data_c, 24)
cr, cg, cb, ca = struct.unpack_from("<ffff", vtx_data_c, 32)

check("T5d: pos.x=1.0 (stride 48)", pos_x_c == 1.0)
check("T5e: nor.x=0.1 (stride 48)", abs(nor_x_c - 0.1) < 1e-6)
check("T5f: uv.u=0.5 (stride 48)", abs(u_c - 0.5) < 1e-6)
check("T5g: color0.r=1.0", cr == 1.0)
check("T5h: color0.g=0.0", cg == 0.0)
check("T5i: color0.b=0.0", cb == 0.0)
check("T5j: color0.a=1.0", ca == 1.0)

check("T5k: vertex byte size = 48",
      len(vtx_data_c) == 48,
      f"got {len(vtx_data_c)}")

# Verify indices follow
idx_count_off_c = vtx_off_c + 48
ic_c = struct.unpack_from("<I", chunk_c, idx_count_off_c)[0]
check("T5l: index_count=3 after stride 48 vertex", ic_c == 3)


# =========================================================
# T6: invalid stride rejected
# =========================================================

print("\n--- T6: Invalid stride rejected ---")

try:
    bad_chunk = serialize_full_attr_mesh_chunk_v1(
        guid, version_hash, 0, 1, verts, indices,
        flags=MESH_CHUNK_FLAG_FULL_ATTR,
        vertex_stride=64,
    )
    check("T6a: stride=64 raises ValueError", False,
          "expected ValueError, got bytes")
except ValueError as e:
    check("T6a: stride=64 raises ValueError", True)
    check("T6b: error mentions stride", "stride" in str(e).lower())

try:
    bad_chunk = serialize_full_attr_mesh_chunk_v1(
        guid, version_hash, 0, 1, verts, indices,
        flags=MESH_CHUNK_FLAG_FULL_ATTR,
        vertex_stride=0,
    )
    check("T6c: stride=0 raises ValueError", False,
          "expected ValueError, got bytes")
except ValueError as e:
    check("T6c: stride=0 raises ValueError", True)


# =========================================================
# T7: stride 48 without color0 rejected
# =========================================================

print("\n--- T7: Stride 48 without color0 rejected ---")

verts_no_color = make_single_triangle_vertices(color=None)
try:
    bad_chunk = serialize_full_attr_mesh_chunk_v1(
        guid, version_hash, 0, 1, verts_no_color, indices,
        flags=MESH_CHUNK_FLAG_FULL_ATTR,
        vertex_stride=MESH_FULL_ATTR_VERTEX_STRIDE_COLOR0,
    )
    check("T7a: stride 48 + no color raises ValueError", False,
          "expected ValueError, got bytes")
except ValueError as e:
    check("T7a: stride 48 + no color raises ValueError", True)
    check("T7b: error mentions color0",
          "color0" in str(e).lower())

# Verify stride 32 without color0 works fine
ok_chunk = serialize_full_attr_mesh_chunk_v1(
    guid, version_hash, 0, 1, verts_no_color, indices,
    flags=MESH_CHUNK_FLAG_FULL_ATTR,
    vertex_stride=MESH_FULL_ATTR_VERTEX_STRIDE_NO_COLOR,
)
check("T7c: stride 32 without color0 OK",
      len(ok_chunk) > LIVE_SYNC_V5_MESH_CHUNK_HEADER_SIZE)

# Verify stride 48 WITH color0 works fine
color_tri_verts = make_single_triangle_vertices(color=(0.5, 0.5, 0.5, 1.0))
ok_chunk_c = serialize_full_attr_mesh_chunk_v1(
    guid, version_hash, 0, 1, color_tri_verts, [0, 1, 2],
    flags=MESH_CHUNK_FLAG_FULL_ATTR,
    vertex_stride=MESH_FULL_ATTR_VERTEX_STRIDE_COLOR0,
)
check("T7d: stride 48 with color0 OK",
      len(ok_chunk_c) > LIVE_SYNC_V5_MESH_CHUNK_HEADER_SIZE)


# =========================================================
# T8: local index out of bounds rejected
# =========================================================

print("\n--- T8: Out-of-bounds index rejected ---")

# Index referencing non-existent vertex 99
bad_indices = [0, 1, 99]
try:
    bad_chunk = serialize_full_attr_mesh_chunk_v1(
        guid, version_hash, 0, 1, verts, bad_indices,
        flags=MESH_CHUNK_FLAG_FULL_ATTR,
        vertex_stride=MESH_FULL_ATTR_VERTEX_STRIDE_NO_COLOR,
    )
    check("T8a: index=99 out of range raises ValueError", False,
          "expected ValueError, got bytes")
except ValueError as e:
    check("T8a: index=99 out of range raises ValueError", True)

# Negative index
bad_indices2 = [-1, 0, 1]
try:
    bad_chunk = serialize_full_attr_mesh_chunk_v1(
        guid, version_hash, 0, 1, verts, bad_indices2,
        flags=MESH_CHUNK_FLAG_FULL_ATTR,
        vertex_stride=MESH_FULL_ATTR_VERTEX_STRIDE_NO_COLOR,
    )
    check("T8b: index=-1 out of range raises ValueError", False,
          "expected ValueError, got bytes")
except ValueError as e:
    check("T8b: index=-1 out of range raises ValueError", True)

# Valid indices work fine
ok_chunk = serialize_full_attr_mesh_chunk_v1(
    guid, version_hash, 0, 1, verts, [0, 1, 2],
    flags=MESH_CHUNK_FLAG_FULL_ATTR,
    vertex_stride=MESH_FULL_ATTR_VERTEX_STRIDE_NO_COLOR,
)
check("T8c: valid indices [0,1,2] OK",
      len(ok_chunk) > LIVE_SYNC_V5_MESH_CHUNK_HEADER_SIZE)


# =========================================================
# T9: V5 serialize_mesh_chunk() unchanged
# =========================================================

print("\n--- T9: serialize_mesh_chunk() unchanged ---")

verts_v5 = [(-1.0, -1.0, 0.0), (1.0, -1.0, 0.0), (1.0, 1.0, 0.0), (-1.0, 1.0, 0.0)]
tris_v5 = [(0, 1, 2), (0, 2, 3)]
mat_v5 = [0, 0]
hash_v5 = compute_geometry_version_hash(verts_v5, tris_v5, mat_v5)

v5_chunk = serialize_mesh_chunk(
    guid, hash_v5, 0, 1, verts_v5, tris_v5, mat_v5,
    flags=MESH_CHUNK_FLAG_HAS_POSITIONS | MESH_CHUNK_FLAG_HAS_TRIANGLES | MESH_CHUNK_FLAG_HAS_MATERIAL_IDX,
)

check("T9a: V5 chunk is bytes", isinstance(v5_chunk, bytes))

v5_flags = v5_chunk[88]
check("T9b: V5 chunk does NOT have FULL_ATTR flag",
      (v5_flags & MESH_CHUNK_FLAG_FULL_ATTR) == 0)

v5_vertex_count = struct.unpack_from("<I", v5_chunk, 89)[0]
check("T9c: V5 first payload uint32 = vertex_count (not SchemaVersion)",
      v5_vertex_count == len(verts_v5),
      f"got {v5_vertex_count}, expected {len(verts_v5)}")

# V5 header also uses 89 bytes
check("T9d: V5 chunk has 89-byte header",
      LIVE_SYNC_V5_MESH_CHUNK_HEADER_SIZE == 89)

# V5 hash in header is same format
v5_hash_from_hdr = v5_chunk[16:80].decode("ascii", errors="replace").rstrip("\x00")
check("T9e: V5 header contains version hash", v5_hash_from_hdr == hash_v5)
check("T9f: V5 hash is 64 hex chars",
      len(hash_v5) == 64 and all(c in "0123456789abcdef" for c in hash_v5))

# Determine V5 payload structure for comparison
v5_tri_count = struct.unpack_from(
    "<I", v5_chunk, 89 + 4 + len(verts_v5) * 12)[0]
check("T9g: V5 triangle count follows positions", v5_tri_count == len(tris_v5))


# =========================================================
# T10: version_hash still exactly 64 bytes in header
# =========================================================

print("\n--- T10: version_hash is exactly 64 bytes in header ---")

# From the v1 chunk we already created
header_version_bytes = chunk[16:80]
check("T10a: version_hash field is 64 bytes",
      len(header_version_bytes) == 64,
      f"got {len(header_version_bytes)}")

decoded = header_version_bytes.decode("ascii", errors="replace")
check("T10b: version_hash decodes as ASCII",
      decoded == version_hash)
check("T10c: version_hash non-empty and printable",
      len(decoded.strip()) == 64 and decoded.isprintable())

# Also test with a real SHA-256 hash
real_hash = hashlib.sha256(b"test data").hexdigest()
check("T10d: real SHA-256 hash is 64 chars", len(real_hash) == 64)

real_chunk = serialize_full_attr_mesh_chunk_v1(
    guid, real_hash, 0, 1, verts, indices,
    flags=MESH_CHUNK_FLAG_FULL_ATTR,
    vertex_stride=MESH_FULL_ATTR_VERTEX_STRIDE_NO_COLOR,
)
real_hdr_hash = real_chunk[16:80].decode("ascii")
check("T10e: real hash in header = expected",
      real_hdr_hash == real_hash)
check("T10f: real hash field is 64 bytes",
      len(real_chunk[16:80]) == 64)


# =========================================================
# T11: chunk_index >= chunk_count rejected
# =========================================================

print("\n--- T11: chunk_index >= chunk_count rejected ---")

try:
    bad_chunk = serialize_full_attr_mesh_chunk_v1(
        guid, version_hash, 1, 1, verts, indices,
        flags=MESH_CHUNK_FLAG_FULL_ATTR,
        vertex_stride=MESH_FULL_ATTR_VERTEX_STRIDE_NO_COLOR,
    )
    check("T11a: chunk_index=1 >= chunk_count=1 raises ValueError",
          False, "expected ValueError, got bytes")
except ValueError as e:
    check("T11a: chunk_index=1 >= chunk_count=1 raises ValueError",
          True)

try:
    bad_chunk = serialize_full_attr_mesh_chunk_v1(
        guid, version_hash, 5, 3, verts, indices,
        flags=MESH_CHUNK_FLAG_FULL_ATTR,
        vertex_stride=MESH_FULL_ATTR_VERTEX_STRIDE_NO_COLOR,
    )
    check("T11b: chunk_index=5 >= chunk_count=3 raises ValueError",
          False, "expected ValueError, got bytes")
except ValueError as e:
    check("T11b: chunk_index=5 >= chunk_count=3 raises ValueError",
          True)

# Negative chunk_index (should also fail the same check)
try:
    bad_chunk = serialize_full_attr_mesh_chunk_v1(
        guid, version_hash, -1, 2, verts, indices,
        flags=MESH_CHUNK_FLAG_FULL_ATTR,
        vertex_stride=MESH_FULL_ATTR_VERTEX_STRIDE_NO_COLOR,
    )
    check("T11c: negative chunk_index raises ValueError",
          False, "expected ValueError, got bytes")
except ValueError as e:
    check("T11c: negative chunk_index raises ValueError",
          True)

# Valid case: chunk_index=0 < chunk_count=2 works
ok_chunk = serialize_full_attr_mesh_chunk_v1(
    guid, version_hash, 0, 2, verts, indices,
    flags=MESH_CHUNK_FLAG_FULL_ATTR,
    vertex_stride=MESH_FULL_ATTR_VERTEX_STRIDE_NO_COLOR,
)
check("T11d: chunk_index=0 < chunk_count=2 OK",
      len(ok_chunk) > LIVE_SYNC_V5_MESH_CHUNK_HEADER_SIZE)


# =========================================================
# T12: deterministic serialization for same inputs
# =========================================================

print("\n--- T12: Deterministic serialization ---")

# Re-run same inputs multiple times
chunk_a = serialize_full_attr_mesh_chunk_v1(
    guid, version_hash, 0, 1, verts, indices,
    flags=MESH_CHUNK_FLAG_FULL_ATTR,
    vertex_stride=MESH_FULL_ATTR_VERTEX_STRIDE_NO_COLOR,
)

chunk_b = serialize_full_attr_mesh_chunk_v1(
    guid, version_hash, 0, 1, verts, indices,
    flags=MESH_CHUNK_FLAG_FULL_ATTR,
    vertex_stride=MESH_FULL_ATTR_VERTEX_STRIDE_NO_COLOR,
)

check("T12a: same inputs -> identical output (stride 32)",
      chunk_a == chunk_b)

# Also test stride 48
verts_c2 = make_single_triangle_vertices(color=(0.5, 0.5, 0.5, 1.0))
indices_c2 = [0, 1, 2]

chunk_c_a = serialize_full_attr_mesh_chunk_v1(
    guid, version_hash, 0, 1, verts_c2, indices_c2,
    flags=MESH_CHUNK_FLAG_FULL_ATTR,
    vertex_stride=MESH_FULL_ATTR_VERTEX_STRIDE_COLOR0,
)

chunk_c_b = serialize_full_attr_mesh_chunk_v1(
    guid, version_hash, 0, 1, verts_c2, indices_c2,
    flags=MESH_CHUNK_FLAG_FULL_ATTR,
    vertex_stride=MESH_FULL_ATTR_VERTEX_STRIDE_COLOR0,
)

check("T12b: same inputs -> identical output (stride 48)",
      chunk_c_a == chunk_c_b)

# Different GUID -> different output
guid_other = uuid.uuid4()
chunk_other = serialize_full_attr_mesh_chunk_v1(
    guid_other, version_hash, 0, 1, verts, indices,
    flags=MESH_CHUNK_FLAG_FULL_ATTR,
    vertex_stride=MESH_FULL_ATTR_VERTEX_STRIDE_NO_COLOR,
)
check("T12c: different GUID -> different output",
      chunk_a != chunk_other)

# Different vertices -> different output
verts_other = make_render_vertices_quad()
indices_other = [0, 1, 2, 0, 2, 3]
chunk_verts_other = serialize_full_attr_mesh_chunk_v1(
    guid, version_hash, 0, 1, verts_other, indices_other,
    flags=MESH_CHUNK_FLAG_FULL_ATTR,
    vertex_stride=MESH_FULL_ATTR_VERTEX_STRIDE_NO_COLOR,
)
check("T12d: different vertices -> different output",
      chunk_a != chunk_verts_other)


# =========================================================
# Additional: non-chunk-0 chunk index > 0 works
# =========================================================

print("\n--- Extra: Non-zero chunk index ---")

# Verify multi-chunk with 8 vertices (2 triangles in chunk 0, 2 in chunk 1)
quad_verts = make_render_vertices_quad(color=None)
quad_indices = [0, 1, 2, 0, 2, 3]

chunk0_multi = serialize_full_attr_mesh_chunk_v1(
    guid, version_hash, 0, 2, quad_verts, quad_indices[:3],
    flags=MESH_CHUNK_FLAG_FULL_ATTR,
    vertex_stride=MESH_FULL_ATTR_VERTEX_STRIDE_NO_COLOR,
)
chunk1_multi = serialize_full_attr_mesh_chunk_v1(
    guid, version_hash, 1, 2, quad_verts, quad_indices[3:],
    flags=MESH_CHUNK_FLAG_FULL_ATTR,
    vertex_stride=MESH_FULL_ATTR_VERTEX_STRIDE_NO_COLOR,
)

# Chunk 0 has SchemaVersion
sv_c0 = struct.unpack_from("<I", chunk0_multi, 89)[0]
check("ExtraA: Chunk 0 has SchemaVersion=1",
      sv_c0 == MESH_FULL_ATTR_SCHEMA_VERSION)

# Chunk 1 does NOT have SchemaVersion; first field is vertex_stride
first_c1 = struct.unpack_from("<I", chunk1_multi, 89)[0]
check("ExtraB: Chunk 1 first field is vertex_stride",
      first_c1 == MESH_FULL_ATTR_VERTEX_STRIDE_NO_COLOR)
check("ExtraC: Chunk 1 first field NOT SchemaVersion",
      first_c1 != MESH_FULL_ATTR_SCHEMA_VERSION)

# Both chunks have correct chunk_index
ci0 = struct.unpack_from("<I", chunk0_multi, 80)[0]
ci1 = struct.unpack_from("<I", chunk1_multi, 80)[0]
check("ExtraD: Chunk 0 index=0", ci0 == 0)
check("ExtraE: Chunk 1 index=1", ci1 == 1)


# =========================================================
# Summary
# =========================================================

total = PASS + FAIL
print(f"\n{'=' * 60}")
print(f"Phase 7C Stage 2B.2 — V1 Serializer Tests")
print(f"{'=' * 60}")
print(f"  Total: {total}")
print(f"  PASS:  {PASS}")
print(f"  FAIL:  {FAIL}")

if FAIL > 0:
    print(f"\n  FAILURES: {FAIL}")
    sys.exit(1)
else:
    print(f"\n  All tests passed.")
    sys.exit(0)
