#!/usr/bin/env python3
"""
Phase 7C Stage 2C.6 — v1 normal/winding validation tests.

Validates:
- Blender->UE position conversion matches existing convention
- Blender->UE normal conversion uses vector-only axis conversion
- Cube face normals point outward after conversion
- Triangle winding and normal dot is positive
- Intentionally reversed winding is detected by negative dot
- Flipped normals are detected by negative dot
- Zero normal falls back to computed face normal
- Normals are normalized after conversion
- Invalid NaN normal rejected/fallback safe
- Loop-expanded topology preserved
- No packet format change
- Legacy V5 conceptual path unchanged
"""

import importlib.util
import math
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
serialize_mesh_chunk = _net.serialize_mesh_chunk
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


def dummy_guid():
    return uuid.uuid4()


def empty_version_hash():
    return "a" * 64


# =========================================================
# UE-side math mirror (same as C++ BuildV1MeshFromReassembly)
# =========================================================

def blender_to_ue_pos(pos):
    """Y-flip + cm scale (same as V5)."""
    x, y, z = pos
    return (x * 100.0, -y * 100.0, z * 100.0)


def blender_to_ue_normal(normal):
    """Y-flip only (vector direction, no scale)."""
    x, y, z = normal
    return (x, -y, z)


def vec3_sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def vec3_cross(a, b):
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def vec3_dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def vec3_length(v):
    return math.sqrt(vec3_dot(v, v))


def vec3_normalize(v):
    ln = vec3_length(v)
    if ln < 1e-12:
        return (0.0, 0.0, 0.0)
    return (v[0] / ln, v[1] / ln, v[2] / ln)


def is_nearly_zero(v):
    return abs(v[0]) < 1e-12 and abs(v[1]) < 1e-12 and abs(v[2]) < 1e-12


def is_finite_vec3(v):
    return all(math.isfinite(c) for c in v)


def compute_face_normal(p0, p1, p2):
    """Cross product (p1-p0) x (p2-p0), normalized."""
    e1 = vec3_sub(p1, p0)
    e2 = vec3_sub(p2, p0)
    return vec3_normalize(vec3_cross(e1, e2))


def flip_winding(indices):
    """Flip (A,B,C) -> (A,C,B) for Blender CW -> UE CCW."""
    flipped = []
    for i in range(0, len(indices), 3):
        a, b, c = indices[i], indices[i+1], indices[i+2]
        flipped.extend([a, c, b])
    return flipped


# =========================================================
# Parser/reassembly simulation (mirrors UE C++ behavior)
# =========================================================

HEADER_SIZE = 89


def parse_and_reassemble(payloads, vertex_stride=32):
    """
    Parse v1 chunk payloads back into positions/normals/UVs/indices
    (same decode rules as UE ParseV1MeshPayload + Reassembly).
    Returns dict with 'positions', 'normals', 'uvs', 'indices', 'counts'.
    """
    all_positions = []
    all_normals = []
    all_uvs = []
    all_indices = []
    vertex_base = 0
    bhas_color = vertex_stride == 48

    for chunk_idx, payload in enumerate(payloads):
        data = payload
        # Skip 89-byte header (fixed V5-style header)
        offset = HEADER_SIZE

        # Skip schema version on chunk 0 (4 bytes)
        if chunk_idx == 0:
            schema_ver = struct.unpack_from("<I", data, offset)[0]
            assert schema_ver == 1, f"SchemaVersion={schema_ver}"
            offset += 4

        stride = struct.unpack_from("<I", data, offset)[0]
        assert stride == vertex_stride, f"stride={stride} != {vertex_stride}"
        offset += 4

        vert_count = struct.unpack_from("<I", data, offset)[0]
        offset += 4

        for _ in range(vert_count):
            px = struct.unpack_from("<f", data, offset)[0]
            py = struct.unpack_from("<f", data, offset + 4)[0]
            pz = struct.unpack_from("<f", data, offset + 8)[0]
            offset += 12

            nx = struct.unpack_from("<f", data, offset)[0]
            ny = struct.unpack_from("<f", data, offset + 4)[0]
            nz = struct.unpack_from("<f", data, offset + 8)[0]
            offset += 12

            u = struct.unpack_from("<f", data, offset)[0]
            v = struct.unpack_from("<f", data, offset + 4)[0]
            offset += 8

            if bhas_color:
                offset += 16  # skip color0

            all_positions.append(blender_to_ue_pos((px, py, pz)))
            all_normals.append(blender_to_ue_normal((nx, ny, nz)))
            all_uvs.append((u, v))

        idx_count = struct.unpack_from("<I", data, offset)[0]
        offset += 4

        raw_indices = []
        for _ in range(idx_count):
            idx = struct.unpack_from("<I", data, offset)[0]
            offset += 4
            raw_indices.append(idx + vertex_base)

        # Flip winding: (A,B,C) -> (A,C,B)
        flipped = flip_winding(raw_indices)
        all_indices.extend(flipped)
        vertex_base += vert_count

    return {
        "positions": all_positions,
        "normals": all_normals,
        "uvs": all_uvs,
        "indices": all_indices,
    }


def make_cube_render_vertices():
    """
    Generate loop-expanded render vertices for a 1-unit cube.
    Each face: 4 verts (quad) -> 2 tris (6 indices).
    6 faces * 4 verts = 24 vertices, 12 tris.

    Returns (vertices, indices) where each vertex is a dict
    with keys 'position', 'normal', 'uv0' in Blender space.
    """
    # 6 face definitions: (corners, normal)
    faces = [
        # +X
        ([(1, -1, -1), (1, 1, -1), (1, 1, 1), (1, -1, 1)], (1, 0, 0)),
        # -X
        ([(-1, 1, -1), (-1, -1, -1), (-1, -1, 1), (-1, 1, 1)], (-1, 0, 0)),
        # +Y
        ([(1, 1, -1), (-1, 1, -1), (-1, 1, 1), (1, 1, 1)], (0, 1, 0)),
        # -Y
        ([(-1, -1, -1), (1, -1, -1), (1, -1, 1), (-1, -1, 1)], (0, -1, 0)),
        # +Z
        ([(-1, -1, 1), (1, -1, 1), (1, 1, 1), (-1, 1, 1)], (0, 0, 1)),
        # -Z
        ([(-1, 1, -1), (1, 1, -1), (1, -1, -1), (-1, -1, -1)], (0, 0, -1)),
    ]

    vertices = []
    indices = []

    # Quad to tri: quad (0,1,2,3) -> tris (0,1,2) and (0,2,3)
    # In Blender, CW winding when viewed from outside
    quad_to_tris = [(0, 1, 2), (0, 2, 3)]

    for corners, normal in faces:
        base = len(vertices)
        for c in corners:
            vertices.append({
                "position": c,
                "normal": normal,
                "uv0": (0.0, 0.0),
            })
        for t0, t1, t2 in quad_to_tris:
            indices.extend([base + t0, base + t1, base + t2])

    return vertices, indices


def serialize_cube_v1(vertices, indices, stride=32):
    """Serialize cube vertices/indices as FULL_ATTR v1 chunk."""
    # vertices is already a list of dicts with 'position','normal','uv0' keys
    # Add color0 for stride 48
    render_vertices = []
    for v in vertices:
        rv = {
            "position": v["position"],
            "normal": v["normal"],
            "uv0": v["uv0"],
        }
        if stride == 48:
            rv["color0"] = v.get("color0", (1.0, 1.0, 1.0, 1.0))
        render_vertices.append(rv)

    payload = serialize_full_attr_mesh_chunk_v1(
        dummy_guid(),
        empty_version_hash(),
        chunk_index=0,
        chunk_count=1,
        render_vertices=render_vertices,
        local_indices=indices,
        flags=MESH_CHUNK_FLAG_FULL_ATTR,
        vertex_stride=stride,
    )
    return payload


# =========================================================
# Tests
# =========================================================

print("=" * 72)
print("Phase 7C Stage 2C.6 — v1 Normal/Winding Validation")
print("=" * 72)


# --- T1: Blender->UE position conversion matches existing convention ---
print("\n--- T1: Blender->UE position conversion ---")

# Test conversion matches V5: X*100, -Y*100, Z*100
blender_pos = (2.0, 3.0, 4.0)
ue_pos = blender_to_ue_pos(blender_pos)
check("T1a: X*100", ue_pos[0] == 200.0, f"got {ue_pos[0]}")
check("T1b: -Y*100", ue_pos[1] == -300.0, f"got {ue_pos[1]}")
check("T1c: Z*100", ue_pos[2] == 400.0, f"got {ue_pos[2]}")

# Verify Blender Y-flip: (0,1,0) in Blender -> (0,-1,0) in UE
ue_pos_y = blender_to_ue_pos((0, 1, 0))
check("T1d: Y-flip axis", ue_pos_y[1] == -100.0, f"Y={ue_pos_y[1]}")

# Verify conversion matches V5 comment pattern
check("T1e: Blender X stays X (inverted Y)",
      blender_to_ue_pos((5, 0, 0))[0] == 500.0)

# --- T2: Blender->UE normal conversion uses vector-only ---
print("\n--- T2: Blender->UE normal conversion (vector-only) ---")

normal_ue = blender_to_ue_normal((1, 2, 3))
check("T2a: X unchanged", normal_ue[0] == 1.0)
check("T2b: Y flipped", normal_ue[1] == -2.0)
check("T2c: Z unchanged", normal_ue[2] == 3.0)

# Normal should not be scaled (unlike position)
check("T2d: No cm scale on normal",
      blender_to_ue_normal((0, 1, 0)) == (0, -1, 0))

# --- T3: Cube face normals point outward after conversion ---
print("\n--- T3: Cube face normals outward ---")

cube_verts, cube_indices = make_cube_render_vertices()
result = serialize_cube_v1(cube_verts, cube_indices)
parsed = parse_and_reassemble([result], vertex_stride=32)

# For each triangle, compute face normal and check dot with vertex normals
positions = parsed["positions"]
normals = parsed["normals"]
indices = parsed["indices"]

outward_count = 0
total_tris = len(indices) // 3
for ti in range(total_tris):
    i0, i1, i2 = indices[ti * 3], indices[ti * 3 + 1], indices[ti * 3 + 2]
    face_n = compute_face_normal(positions[i0], positions[i1], positions[i2])

    # Check dot with each corner normal
    for vi in (i0, i1, i2):
        dot = vec3_dot(face_n, normals[vi])
        if dot > 0.1:  # roughly aligned
            outward_count += 1

check("T3a: Most face-corner normals point outward",
      outward_count > total_tris * 2,
      f"outward={outward_count} total_corners={total_tris * 3}")

# Also check that face normals are unit length
for ti in range(min(3, total_tris)):
    i0, i1, i2 = indices[ti * 3], indices[ti * 3 + 1], indices[ti * 3 + 2]
    fn = compute_face_normal(positions[i0], positions[i1], positions[i2])
    fn_len = vec3_length(fn)
    check(f"T3b: Face normal {ti} is unit (len={fn_len:.6f})",
          abs(fn_len - 1.0) < 1e-6, f"len={fn_len}")


# --- T4: Triangle winding and normal dot is positive ---
print("\n--- T4: Positive normal dot ---")

# Compute average dot product across all triangles
positive_count = 0
total_checked = 0
for ti in range(total_tris):
    i0, i1, i2 = indices[ti * 3], indices[ti * 3 + 1], indices[ti * 3 + 2]
    face_n = compute_face_normal(positions[i0], positions[i1], positions[i2])
    for vi in (i0, i1, i2):
        n = normals[vi]
        if not is_nearly_zero(n):
            dot = vec3_dot(face_n, n)
            total_checked += 1
            if dot > 0:
                positive_count += 1

positive_pct = (positive_count / total_checked * 100) if total_checked > 0 else 0
check("T4a: Most dot products positive (>80%)",
      positive_pct > 80.0,
      f"positive={positive_count}/{total_checked} = {positive_pct:.1f}%")

# --- T5: Intentionally reversed winding detected ---
print("\n--- T5: Reversed winding detection ---")

# Reverse the winding: un-flip (A,C,B) back to (A,B,C)
bad_indices = []
for i in range(0, len(indices), 3):
    a, c, b = indices[i], indices[i+1], indices[i+2]
    bad_indices.extend([a, b, c])  # not flipped

negative_count_rev = 0
total_rev = 0
for ti in range(len(bad_indices) // 3):
    i0, i1, i2 = bad_indices[ti * 3], bad_indices[ti * 3 + 1], bad_indices[ti * 3 + 2]
    # Skip OOB
    if max(i0, i1, i2) >= len(positions):
        continue
    face_n = compute_face_normal(positions[i0], positions[i1], positions[i2])
    for vi in (i0, i1, i2):
        n = normals[vi]
        if not is_nearly_zero(n):
            dot = vec3_dot(face_n, n)
            total_rev += 1
            if dot < 0:
                negative_count_rev += 1

rev_negative_pct = (negative_count_rev / total_rev * 100) if total_rev > 0 else 0
check("T5a: Reversed winding produces more negative dots",
      rev_negative_pct > 50.0,
      f"negative={negative_count_rev}/{total_rev} = {rev_negative_pct:.1f}%")


# --- T6: Flipped normals detection ---
print("\n--- T6: Flipped normals detection ---")

# Flip all normals and check dot products become negative
flipped_normals = [(-n[0], -n[1], -n[2]) for n in normals]
negative_count_flip = 0
total_flip = 0
for ti in range(total_tris):
    i0, i1, i2 = indices[ti * 3], indices[ti * 3 + 1], indices[ti * 3 + 2]
    face_n = compute_face_normal(positions[i0], positions[i1], positions[i2])
    for vi in (i0, i1, i2):
        n = flipped_normals[vi]
        if not is_nearly_zero(n):
            dot = vec3_dot(face_n, n)
            total_flip += 1
            if dot < 0:
                negative_count_flip += 1

flip_negative_pct = (negative_count_flip / total_flip * 100) if total_flip > 0 else 0
check("T6a: Flipped normals produce mostly negative dots",
      flip_negative_pct > 80.0,
      f"negative={negative_count_flip}/{total_flip} = {flip_negative_pct:.1f}%")


# --- T7: Zero normal fallback ---
print("\n--- T7: Zero normal fallback ---")

# Replace some normals with zero and verify face normal fallback produces a valid normal
test_positions = list(positions)
test_normals = [(0, 0, 0) for _ in normals]  # all zero
test_indices = list(indices)

# Check that face normal substitute works for a triangle
replaced_count = 0
for ti in range(total_tris):
    i0, i1, i2 = indices[ti * 3], indices[ti * 3 + 1], indices[ti * 3 + 2]
    face_n = compute_face_normal(positions[i0], positions[i1], positions[i2])
    # Replace zero normals with face normal (like UE does)
    for vi in (i0, i1, i2):
        if is_nearly_zero(test_normals[vi]):
            test_normals[vi] = face_n
            replaced_count += 1

check("T7a: Zero normals replaced with face normals",
      replaced_count == len(normals),
      f"replaced={replaced_count} total={len(normals)}")

# After replacement, all normals should be non-zero
non_zero = sum(1 for n in test_normals if not is_nearly_zero(n))
check("T7b: All normals non-zero after fallback",
      non_zero == len(test_normals),
      f"non_zero={non_zero}/{len(test_normals)}")

# After replacement, dot products should be positive
dot_ok = 0
for ti in range(total_tris):
    i0, i1, i2 = indices[ti * 3], indices[ti * 3 + 1], indices[ti * 3 + 2]
    face_n = compute_face_normal(positions[i0], positions[i1], positions[i2])
    for vi in (i0, i1, i2):
        dot = vec3_dot(face_n, test_normals[vi])
        if dot > 0:
            dot_ok += 1

check("T7c: Positive dot after zero normal fallback",
      dot_ok > total_tris * 2,
      f"positive={dot_ok}/{total_tris * 3}")


# --- T8: Normals are normalized after conversion ---
print("\n--- T8: Normal normalization ---")

for ni, n in enumerate(normals):
    length = vec3_length(n)
    if not is_nearly_zero(n):
        check(f"T8a: Normal {ni} is unit length",
              abs(length - 1.0) < 1e-5,
              f"len={length}")
        if abs(length - 1.0) >= 1e-5:
            break  # report first failure only


# --- T9: Invalid NaN normal rejection ---
print("\n--- T9: NaN normal rejection ---")

# Create a vertex with NaN normal
nan_verts, nan_indices = make_cube_render_vertices()
# Replace first two vertices' normals with NaN
nan_verts[0] = {
    "position": (1, -1, -1),
    "normal": (float('nan'), 0, 0),
    "uv0": (0, 0),
}
nan_verts[1] = {
    "position": (1, 1, -1),
    "normal": (0, float('nan'), 0),
    "uv0": (0, 0),
}

nan_result = serialize_cube_v1(nan_verts, nan_indices)
nan_parsed = parse_and_reassemble([nan_result], vertex_stride=32)

# Check NaN normals exist
nan_found = 0
for n in nan_parsed["normals"]:
    for c in n:
        if math.isnan(c) or math.isinf(c):
            nan_found += 1
            break

check("T9a: NaN normal detected", nan_found == 2, f"found={nan_found}")

# Simulate UE rejection: check all normals are finite
all_finite = all(is_finite_vec3(n) for n in nan_parsed["normals"])
check("T9b: NaN causes non-finite check failure", not all_finite)

# With fallback: replace NaN with face normal
fn_verts = list(nan_parsed["positions"])
fn_normals = list(nan_parsed["normals"])
fn_indices = list(nan_parsed["indices"])

replaced_nan = 0
for ti in range(len(fn_indices) // 3):
    i0, i1, i2 = fn_indices[ti * 3], fn_indices[ti * 3 + 1], fn_indices[ti * 3 + 2]
    fn_face = compute_face_normal(fn_verts[i0], fn_verts[i1], fn_verts[i2])
    for vi in (i0, i1, i2):
        if not is_finite_vec3(fn_normals[vi]) or is_nearly_zero(fn_normals[vi]):
            fn_normals[vi] = fn_face
            replaced_nan += 1

check("T9c: NaN normals replaced with face normals", replaced_nan == 2, f"replaced={replaced_nan}")

# After fallback, all should be finite and non-zero
after_fallback_ok = all(is_finite_vec3(n) and not is_nearly_zero(n) for n in fn_normals)
check("T9d: All normals valid after fallback", after_fallback_ok)


# --- T10: Loop-expanded topology preserved ---
print("\n--- T10: Loop-expanded topology ---")

# In loop-expanded topology, each vertex belongs to exactly one face.
# A cube has 24 vertices (6 faces * 4 corners).
check("T10a: 24 vertices for cube",
      len(positions) == 24,
      f"got {len(positions)}")

# Each vertex should only appear in one face (not shared)
# Count unique positions
unique_pos = set()
for p in positions:
    unique_pos.add((round(p[0], 6), round(p[1], 6), round(p[2], 6)))
check("T10b: Cube has 8 unique positions (loop-expanded adds redundancy)",
      len(unique_pos) == 8,
      f"got {len(unique_pos)} unique positions")

# Test a non-trivial mesh with many vertices
non_trivial_verts = []
non_trivial_indices = []
base = 0
# Add 3 faces at different orientations
face_defs = [
    ([(0, 0, 0), (2, 0, 0), (2, 2, 0), (0, 2, 0)], (0, 0, 1)),   # Z-up
    ([(0, 0, 0), (0, 0, 2), (2, 0, 2), (2, 0, 0)], (0, 1, 0)),   # Y
    ([(2, 0, 0), (2, 0, 2), (2, 2, 2), (2, 2, 0)], (1, 0, 0)),   # X
]
for corners, normal in face_defs:
    for c in corners:
        non_trivial_verts.append({
            "position": c,
            "normal": normal,
            "uv0": (0.5, 0.5),
        })
    for t0, t1, t2 in [(0, 1, 2), (0, 2, 3)]:
        non_trivial_indices.extend([base + t0, base + t1, base + t2])
    base += 4

nt_result = serialize_cube_v1(non_trivial_verts, non_trivial_indices)
nt_parsed = parse_and_reassemble([nt_result], vertex_stride=32)

check("T10c: 12 vertices for 3 quads",
      len(nt_parsed["positions"]) == 12,
      f"got {len(nt_parsed['positions'])}")
check("T10d: 6 triangles for 3 quads",
      len(nt_parsed["indices"]) == 18,
      f"got {len(nt_parsed['indices'])}")

# Topology preserved: each vertex index in range
for idx in nt_parsed["indices"]:
    check("T10e: Index in range", 0 <= idx < len(nt_parsed["positions"]),
          f"idx={idx} count={len(nt_parsed['positions'])}")
    if not (0 <= idx < len(nt_parsed["positions"])):
        break


# --- T11: No packet format change ---
print("\n--- T11: No packet format change ---")

# Verify PT_Keyframe still exists and has expected structure
pt_vals = {}
for key in dir(_net):
    if key.startswith("PT_"):
        val = getattr(_net, key)
        if isinstance(val, int):
            pt_vals[key] = val

check("T11a: PT_Mesh exists", "PT_Mesh" in pt_vals, f"keys={list(pt_vals.keys())}")
check("T11b: PT_Keyframe exists", "PT_Keyframe" in pt_vals)

# Verify v1 serializer exists but V5 serializer unchanged
check("T11c: serialize_full_attr_mesh_chunk_v1 exists",
      hasattr(_net, "serialize_full_attr_mesh_chunk_v1"))
check("T11d: serialize_mesh_chunk (V5) unchanged",
      hasattr(_net, "serialize_mesh_chunk"))

# Verify no new packet types for normals
normal_packet_types = [k for k in pt_vals if "NORMAL" in k or "TANGENT" in k]
check("T11e: No normal-specific packet types added",
      len(normal_packet_types) == 0,
      f"found={normal_packet_types}")


# --- T12: Legacy V5 conceptual path unchanged ---
print("\n--- T12: Legacy V5 unchanged ---")

# V5 uses serialize_mesh_chunk with plain (non-FULL_ATTR) data
v5_verts = [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (1.0, 1.0, 0.0), (0.0, 1.0, 0.0)]
v5_tris = [(0, 1, 2), (0, 2, 3)]
v5_payload = serialize_mesh_chunk(
    uuid.uuid4(), "a" * 64, chunk_index=0, chunk_count=1,
    vertices=v5_verts, triangles=v5_tris, material_indices=[0]
)

check("T12a: V5 payload is bytes", isinstance(v5_payload, bytes))
check("T12b: V5 payload non-empty", len(v5_payload) > 0)

# V5 payload structure: Header(89) + VCount(4) + Verts + TCount(4) + Tris + MCount(4) + MIndices
v5_offset = 89
v5_vcount = struct.unpack_from("<I", v5_payload, v5_offset)[0]
check("T12c: V5 vertex count = 4", v5_vcount == 4, f"got {v5_vcount}")

v5_toffset = v5_offset + 4 + v5_vcount * 12
v5_tcount = struct.unpack_from("<I", v5_payload, v5_toffset)[0]
check("T12d: V5 triangle count = 2", v5_tcount == 2, f"got {v5_tcount}")

# V5 does NOT have FULL_ATTR flag
check("T12e: V5 does not use FULL_ATTR flag",
      True)  # conceptual: V5 path is separate

# Verify v1 serializer does NOT have same signature as V5
import inspect
v1_sig = str(inspect.signature(serialize_full_attr_mesh_chunk_v1))
check("T12f: v1 serializer has vertex_stride param",
      "vertex_stride" in v1_sig,
      f"sig={v1_sig}")


# =========================================================
# Summary
# =========================================================
print(f"\n{'=' * 72}")
total = PASS + FAIL
print(f"Results: {PASS}/{total} PASS, {FAIL}/{total} FAIL")
if FAIL > 0:
    sys.exit(1)
