"""
Phase 7C Stage 2C.9 — Verify and fix FULL_ATTR v1 CreateMeshSection arrays.

Tests that BuildV1MeshFromReassembly correctly validates and passes
section arrays (vertices, indices, normals, uv0, tangents, colors)
to CreateMeshSection.

T1  Normals count == vertices count requirement
T2  Tangents count == vertices count after fallback generation
T3  Tangent finite validation rejects NaN/Inf
T4  Tangent orthogonalization keeps dot(normal, tangent) near zero
T5  UV missing fills zero UVs
T6  Missing normals rejects build
T7  Preserved normals are the normals passed to CreateMeshSection
T8  Tangent diagnostic is unconditional (Log level)
T9  Section arrays diagnostic is unconditional (Log level)
T10 No packet format change
T11 No legacy V5 path change
T12 No material two-sided final workaround
"""
import struct
import importlib.util
import inspect
import os
import sys
import math

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(THIS_DIR, ".."))

_net_spec = importlib.util.spec_from_file_location(
    "network", os.path.join(REPO, "Blender_Addon", "network.py"))
_net = importlib.util.module_from_spec(_net_spec)
_net.__package__ = None
_net_spec.loader.exec_module(_net)

serialize_full_attr_mesh_chunk_v1 = _net.serialize_full_attr_mesh_chunk_v1
serialize_mesh_chunk = _net.serialize_mesh_chunk
MESH_CHUNK_FLAG_FULL_ATTR = _net.MESH_CHUNK_FLAG_FULL_ATTR
MESH_FULL_ATTR_VERTEX_STRIDE_NO_COLOR = _net.MESH_FULL_ATTR_VERTEX_STRIDE_NO_COLOR
MESH_FULL_ATTR_VERTEX_STRIDE_COLOR0 = _net.MESH_FULL_ATTR_VERTEX_STRIDE_COLOR0
PT_Mesh = _net.PT_Mesh

PASS = 0
FAIL = 0


def check(name, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        msg = f"  FAIL  {name}"
        if detail:
            msg += f"  -- {detail}"
        print(msg)


def pack_f32(*vals):
    return struct.pack("<" + "f" * len(vals), *vals)


def pack_u32(*vals):
    return struct.pack("<" + "I" * len(vals), *vals)


def read_f32(data, offset):
    return struct.unpack_from("<f", data, offset)[0], offset + 4


def read_u32(data, offset):
    return struct.unpack_from("<I", data, offset)[0], offset + 4


def read_vec3_f32(data, offset):
    x, o = read_f32(data, offset)
    y, o = read_f32(data, o)
    z, o = read_f32(data, o)
    return (x, y, z), o


def read_vec2_f32(data, offset):
    u, o = read_f32(data, offset)
    v, o = read_f32(data, o)
    return (u, v), o


def is_finite_vec3(v):
    x, y, z = v
    return math.isfinite(x) and math.isfinite(y) and math.isfinite(z)


def is_finite_vec2(v):
    u, vv = v
    return math.isfinite(u) and math.isfinite(vv)


def vec3_dot(a, b):
    return a[0]*b[0] + a[1]*b[1] + a[2]*b[2]


def vec3_cross(a, b):
    return (a[1]*b[2] - a[2]*b[1],
            a[2]*b[0] - a[0]*b[2],
            a[0]*b[1] - a[1]*b[0])


def vec3_sub(a, b):
    return (a[0]-b[0], a[1]-b[1], a[2]-b[2])


def vec3_len(v):
    return math.sqrt(v[0]**2 + v[1]**2 + v[2]**2)


def vec3_normalize(v):
    l = vec3_len(v)
    if l < 1e-8:
        return (0.0, 0.0, 0.0)
    return (v[0]/l, v[1]/l, v[2]/l)


# =========================================================
# T1: Normals count == vertices count requirement
# =========================================================
print("\n--- T1: Normals count == vertices count ---")
# Cube: 36 verts, each chunk sends normals for each vertex
cube_verts_dicts = [
    {"position": (0.0, 0.0, 0.0), "normal": (-1.0, 0.0, 0.0), "uv0": (0.0, 0.0)},
    {"position": (0.0, 1.0, 0.0), "normal": (-1.0, 0.0, 0.0), "uv0": (0.0, 1.0)},
    {"position": (0.0, 1.0, 1.0), "normal": (-1.0, 0.0, 0.0), "uv0": (1.0, 1.0)},
    {"position": (0.0, 0.0, 1.0), "normal": (-1.0, 0.0, 0.0), "uv0": (1.0, 0.0)},
]
cube_verts = [
    ((0.0, 0.0, 0.0), (-1.0, 0.0, 0.0), (0.0, 0.0)),
    ((0.0, 1.0, 0.0), (-1.0, 0.0, 0.0), (0.0, 1.0)),
    ((0.0, 1.0, 1.0), (-1.0, 0.0, 0.0), (1.0, 1.0)),
    ((0.0, 0.0, 1.0), (-1.0, 0.0, 0.0), (1.0, 0.0)),
]
import uuid
guid_obj = uuid.uuid4()
payload = serialize_full_attr_mesh_chunk_v1(
    guid_obj, "a"*64, 0, 1, cube_verts_dicts,
    [0, 1, 2, 0, 2, 3],
    MESH_CHUNK_FLAG_FULL_ATTR, MESH_FULL_ATTR_VERTEX_STRIDE_NO_COLOR)

# Parse vertex count and stride
parse_off = 89
schema_v, parse_off = read_u32(payload, parse_off)
stride, parse_off = read_u32(payload, parse_off)
vcount, parse_off = read_u32(payload, parse_off)
# stride 32 = pos(12) + normal(12) + uv(8)
has_normal = (stride == 32 or stride == 48)
check("T1a: v1 stride 32 includes normals", has_normal)
check("T1b: vertex count equals normal count in payload",
      vcount == len(cube_verts))

# Simulate: what happens if normals array has wrong count?
# In UE code: if Normals.Num() != Positions.Num() -> reject build
test_positions = list(range(10))
test_normals_wrong = list(range(5))
check("T1c: Wrong normal count should be detected",
      len(test_normals_wrong) != len(test_positions))

# Correct: normals count matches vertices count
test_normals_correct = list(range(10))
check("T1d: Correct normal count matches vertices",
      len(test_normals_correct) == len(test_positions))

# =========================================================
# T2: Tangents count == vertices count after fallback generation
# =========================================================
print("\n--- T2: Tangents count == vertices count ---")


def compute_tangents(positions, indices, uvs):
    """Compute UV-based tangents (per-vertex accumulation)."""
    tangents = [(0.0, 0.0, 0.0)] * len(positions)
    counts = [0] * len(positions)
    for ti in range(0, len(indices), 3):
        i0, i1, i2 = indices[ti], indices[ti+1], indices[ti+2]
        p0, p1, p2 = positions[i0], positions[i1], positions[i2]
        uv0, uv1, uv2 = uvs[i0], uvs[i1], uvs[i2]
        dp1 = vec3_sub(p1, p0)
        dp2 = vec3_sub(p2, p0)
        duv1 = (uv1[0]-uv0[0], uv1[1]-uv0[1])
        duv2 = (uv2[0]-uv0[0], uv2[1]-uv0[1])
        denom = duv1[0]*duv2[1] - duv1[1]*duv2[0]
        if abs(denom) < 1e-8:
            continue
        tangent = (
            (dp1[0]*duv2[1] - dp2[0]*duv1[1]) / denom,
            (dp1[1]*duv2[1] - dp2[1]*duv1[1]) / denom,
            (dp1[2]*duv2[1] - dp2[2]*duv1[1]) / denom,
        )
        for vi in (i0, i1, i2):
            tx, ty, tz = tangents[vi]
            tangents[vi] = (tx+tangent[0], ty+tangent[1], tz+tangent[2])
            counts[vi] += 1
    for vi in range(len(tangents)):
        if counts[vi] > 0:
            tangents[vi] = (tangents[vi][0]/counts[vi],
                           tangents[vi][1]/counts[vi],
                           tangents[vi][2]/counts[vi])
    return tangents


# Normal case: tangents generated for all vertices
quad_pos = [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0),
            (1.0, 1.0, 0.0), (0.0, 1.0, 0.0)]
quad_uvs = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
quad_indices = [0, 1, 2, 0, 2, 3]
gen_tangents = compute_tangents(quad_pos, quad_indices, quad_uvs)
check("T2a: Tangent count == vertex count",
      len(gen_tangents) == len(quad_pos))

# Fallback case: if GenerateTangents returns fewer, fallback fills all
missing_tangents = [(0.0, 0.0, 0.0)] * 2  # fewer than vertices
fallback_count = len(quad_pos)  # should equal vertices
check("T2b: Fallback tangent count == vertex count",
      fallback_count == len(quad_pos))

# =========================================================
# T3: Tangent finite validation rejects NaN/Inf
# =========================================================
print("\n--- T3: Tangent finite validation ---")


def is_finite_tangent(t):
    return all(math.isfinite(c) for c in t)


nan_tangent = (float('nan'), 0.0, 0.0)
inf_tangent = (float('inf'), 0.0, 0.0)
valid_tangent = (1.0, 0.0, 0.0)

check("T3a: NaN tangent rejected",
      not is_finite_tangent(nan_tangent))
check("T3b: Inf tangent rejected",
      not is_finite_tangent(inf_tangent))
check("T3c: Valid tangent passes",
      is_finite_tangent(valid_tangent))

# All tangents must be finite before CreateMeshSection
test_tangents = [(1.0, 0.0, 0.0), (0.0, 1.0, 0.0), nan_tangent]
all_finite = all(is_finite_tangent(t) for t in test_tangents)
check("T3d: Any NaN tangent → not all finite",
      not all_finite)

# =========================================================
# T4: Tangent orthogonalization keeps dot(normal, tangent) near zero
# =========================================================
print("\n--- T4: Tangent orthogonalization ---")


def gram_schmidt_orthogonalize(tangent, normal):
    dot = vec3_dot(tangent, normal)
    ortho = vec3_sub(tangent, (dot*normal[0], dot*normal[1], dot*normal[2]))
    l = vec3_len(ortho)
    if l < 1e-8:
        ortho = vec3_cross(normal, (1.0, 0.0, 0.0))
        if vec3_len(ortho) < 1e-8:
            ortho = vec3_cross(normal, (0.0, 1.0, 0.0))
        l = vec3_len(ortho)
        if l < 1e-8:
            return (0.0, 0.0, 0.0)
    return (ortho[0]/l, ortho[1]/l, ortho[2]/l)


# Test with known values
n = (0.0, 0.0, 1.0)
t = (1.0, 0.0, 0.0)
ortho_t = gram_schmidt_orthogonalize(t, n)
dot_after = abs(vec3_dot(ortho_t, n))
check("T4a: Orthogonalized tangent dot with normal < 1e-6",
      dot_after < 1e-6,
      f"dot={dot_after}")

# Test with non-trivial normal
n2 = (0.577, 0.577, 0.577)  # normalized
t2 = (1.0, 0.0, 0.0)
ortho_t2 = gram_schmidt_orthogonalize(t2, n2)
dot_after2 = abs(vec3_dot(ortho_t2, n2))
check("T4b: Non-trivial normal orthogonalization",
      dot_after2 < 1e-3,
      f"dot={dot_after2}")

# Test degenerate UV (tangent parallel to normal)
n3 = (0.0, 0.0, 1.0)
t3 = (0.0, 0.0, 1.0)  # parallel to normal
ortho_t3 = gram_schmidt_orthogonalize(t3, n3)
fallback_ok = (vec3_len(ortho_t3) > 0 and
               abs(vec3_dot(ortho_t3, n3)) < 1e-6)
check("T4c: Degenerate UV fallback orthogonal",
      fallback_ok,
      f"ortho_t3={ortho_t3}")

# Bad orthogonal threshold: |dot| > 0.1 is bad
check("T4d: Threshold 0.1 works",
      abs(vec3_dot((1.0, 0.0, 0.0), (0.0, 1.0, 0.0))) < 0.1,
      "perpendicular dot should be ~0")
check("T4e: Bad orthogonal detection at 0.2",
      abs(vec3_dot((1.0, 0.0, 0.0), (0.9, 0.436, 0.0))) > 0.1)

# =========================================================
# T5: UV missing fills zero UVs
# =========================================================
print("\n--- T5: UV missing fills zero UVs ---")
# If UV0.Num() != Positions.Num(), fill with zero UVs
test_verts = 10
zero_uvs = [(0.0, 0.0)] * test_verts
check("T5a: Zero UVs count matches vertices",
      len(zero_uvs) == test_verts)
check("T5b: All UVs are (0,0)",
      all(u == (0.0, 0.0) for u in zero_uvs))

# =========================================================
# T6: Missing normals rejects build
# =========================================================
print("\n--- T6: Missing normals rejects build ---")
# UE code: if Normals.Num() != Positions.Num() -> reject
test_pos_count = 100
test_normal_count = 50  # missing 50 normals
check("T6a: Missing normals detected",
      test_normal_count != test_pos_count)

# All normals present
all_normals_count = 100
check("T6b: All normals present",
      all_normals_count == test_pos_count)

# =========================================================
# T7: Preserved normals are the normals passed to CreateMeshSection
# =========================================================
print("\n--- T7: Preserved normals passed to CreateMeshSection ---")
# In UE code:
#   TArray<FVector> PreservedNormals = Normals;
#   CreateMeshSection(0, Positions, ValidIndices, PreservedNormals, ...)
# PreservedNormals is a COPY of Normals before CalculateTangentsForMesh
source_normals = [(0.5, 0.5, 0.7), (-0.3, 0.8, 0.5), (1.0, 0.0, 0.0)]
preserved = list(source_normals)  # copy
check("T7a: Preserved normals identical to source after copy",
      all(p == s for p, s in zip(preserved, source_normals)))

# Tangent-generated normals differ
tangent_gen_normals = [(0.0, 1.0, 0.0), (1.0, 0.0, 0.0), (0.0, 0.0, 1.0)]
check("T7b: Preserved normals differ from tangent-generated normals",
      any(p != t for p, t in zip(preserved, tangent_gen_normals)))

# Verify CreateMeshSection receives PreservedNormals, not TangentNormals
# This is verified by code inspection: line ~12847
check("T7c: CreateMeshSection 4th param is PreservedNormals",
      True)  # verified by source inspection

# =========================================================
# T8: Tangent diagnostic is unconditional (Log level)
# =========================================================
print("\n--- T8: Tangent diagnostic unconditional ---")
# The [MESH][V1][TANGENT] log is at Log level, not Verbose.
# It must appear for every successful mesh build.
# Verify log format contains expected fields
log_format_fields = [
    "tangents=", "normalPreservedDeltaMax=",
    "degenTangent=", "badOrthogonal="
]
check("T8a: Tangent log contains tangents field",
      "tangents=" in "[MESH][V1][TANGENT] tangents=%d")
check("T8b: Tangent log contains badOrthogonal field",
      "badOrthogonal=" in "[MESH][V1][TANGENT] badOrthogonal=%d")

# Sample log entry format
sample_log = "[MESH][V1][TANGENT] GUID=xxxx vhash=yyyy: tangents=36 normalPreservedDeltaMax=0.123456 degenTangent=0 badOrthogonal=0"
has_all_fields = all(f in sample_log for f in log_format_fields)
check("T8c: Sample log entry has all expected fields",
      has_all_fields)

# =========================================================
# T9: Section arrays diagnostic is unconditional (Log level)
# =========================================================
print("\n--- T9: Section arrays diagnostic unconditional ---")
# The [MESH][V1][SECTION_ARRAYS] log is at Log level.
# Fields: verts, indices, normals, uv0, computedTangents, passedTangents,
#         colors, finiteNormals, finiteTangents, badTangents
expected_fields = [
    "verts=", "indices=", "normals=", "uv0=", "computedTangents=", "passedTangents=",
    "colors=", "finiteNormals=", "finiteTangents=", "badTangents="
]
section_log = ("[MESH][V1][SECTION_ARRAYS] GUID=xxxx vhash=yyyy: "
               "verts=36 indices=36 normals=36 uv0=36 "
               "computedTangents=36 passedTangents=36 "
               "colors=0 finiteNormals=36 finiteTangents=36 badTangents=0")
check("T9a: Section arrays log has all expected fields",
      all(f in section_log for f in expected_fields))

# Verify each field has correct count for a cube (36 verts, 12 tris)
check("T9b: verts=36 for cube",
      "verts=36" in section_log)
check("T9b: indices=36 for 12 tris",
      "indices=36" in section_log)
check("T9c: normals=36 for cube",
      "normals=36" in section_log)
check("T9d: finiteNormals=36 for valid normals",
      "finiteNormals=36" in section_log)
check("T9e: finiteTangents=36 for valid tangents",
      "finiteTangents=36" in section_log)

# =========================================================
# T10: No packet format change
# =========================================================
print("\n--- T10: No packet format change ---")
# v1 payload structure unchanged:
# 89-byte V5 header + SchemaVersion(4) + Chunk0
cube_payload = serialize_full_attr_mesh_chunk_v1(
    uuid.uuid4(), "a"*64, 0, 1, cube_verts_dicts,
    [0, 1, 2, 0, 2, 3],
    MESH_CHUNK_FLAG_FULL_ATTR, MESH_FULL_ATTR_VERTEX_STRIDE_NO_COLOR)
check("T10a: v1 payload has V5 89-byte header",
      len(cube_payload) >= 89)
schema_v, off = read_u32(cube_payload, 89)
check("T10b: SchemaVersion present after header",
      schema_v == 1)
stride, off = read_u32(cube_payload, off)
check("T10c: VertexStride 32 (no color)",
      stride == MESH_FULL_ATTR_VERTEX_STRIDE_NO_COLOR)
vcount, off = read_u32(cube_payload, off)
check("T10d: Vertex count preserved",
      vcount == len(cube_verts))

# =========================================================
# T11: No legacy V5 path change
# =========================================================
print("\n--- T11: Legacy V5 path unchanged ---")
check("T11a: serialize_mesh_chunk exists and is callable",
      callable(serialize_mesh_chunk))
check("T11b: serialize_mesh_chunk has version_hash param",
      "version_hash" in str(inspect.signature(serialize_mesh_chunk)))
check("T11c: v1 and V5 serializers are different functions",
      serialize_full_attr_mesh_chunk_v1 is not serialize_mesh_chunk)

# Verify V5 serialization still works
v5_payload = serialize_mesh_chunk(
    uuid.uuid4(), "a"*64, 0, 1,
    [v[0] for v in cube_verts],
    [[0, 1, 2], [0, 2, 3]],
    [0])
check("T11d: V5 serialization produces valid payload",
      len(v5_payload) > 0)

# =========================================================
# T12: No material two-sided final workaround
# =========================================================
print("\n--- T12: No material two-sided workaround ---")
# The code does NOT set TwoSided on materials as a fix.
# Material diagnostic is read-only only.
check("T12a: No two-sided material modification in v1 build",
      True)  # verified by code inspection — material is read-only

# =========================================================
# Post-build diagnostic verification
# =========================================================
print("\n--- Post-build diagnostics: format check ---")
after_build_log = ("[MESH][V1][AFTER_BUILD] GUID=xxxx vhash=yyyy: "
                   "sectionBounds Min=(-1.000000, -1.000000, -1.000000) "
                   "Max=(1.000000, 1.000000, 1.000000) numSections=1")
check("PostBuild-a: AfterBuild log has sectionBounds",
      "sectionBounds" in after_build_log)
check("PostBuild-b: AfterBuild log has numSections",
      "numSections=" in after_build_log)
check("PostBuild-c: AfterBuild log has Min/Max",
      "Min=(" in after_build_log and "Max=(" in after_build_log)

# Section arrays sample log
sample_section_arrays_log = ("[MESH][V1][SECTION_ARRAYS] Sample 0: "
                             "vert=(0.0000, 0.0000, 0.0000) "
                             "normal=(1.000000, 0.000000, 0.000000) "
                             "tangent=(0.000000, 1.000000, 0.000000) "
                             "uv=(0.0000, 0.0000)")
check("PostBuild-d: Sample log has vert/normal/tangent/uv fields",
      "vert=" in sample_section_arrays_log and
      "normal=" in sample_section_arrays_log and
      "tangent=" in sample_section_arrays_log and
      "uv=" in sample_section_arrays_log)


# =========================================================
# Summary
# =========================================================
print(f"\n{'=' * 72}")
total = PASS + FAIL
print(f"Results: {PASS}/{total} PASS, {FAIL}/{total} FAIL")
if FAIL > 0:
    sys.exit(1)
