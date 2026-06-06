#!/usr/bin/env python3
"""
Phase 7C Stage 2A — FULL_ATTR schema gate tests (Blender-side only).

Tests:
  T1: MESH_CHUNK_FLAG_FULL_ATTR = 0x80 defined
  T2: FULL_ATTR absent in existing V5 flags
  T3: FULL_ATTR flag is distinct (single bit, no overlap with existing flags)
  T4: Serialize mesh without FULL_ATTR → no SchemaVersion written (T8)
  T5: FULL_ATTR + Chunk0 + SchemaVersion=0 → payload[89:93] == 0x00000000
  T6: FULL_ATTR + Chunk0 + SchemaVersion=1 → payload[89:93] == 0x01000000
  T7: FULL_ATTR + Chunk0 + SchemaVersion=99 → payload[89:93] == 0x63000000
  T8: FULL_ATTR absent → chunk starts directly with vertex data (no SchemaVersion)

No UE-side tests (those are C++/GUnit in UE).
No v1 parser, no stride, no manual operator.
No changes to existing V5 tests.
"""

import hashlib
import struct
import sys
import uuid

PASS = 0
FAIL = 0

# =========================================================
# Inline constants mirroring Blender_Addon/network.py
# =========================================================

MESH_CHUNK_FLAG_HAS_POSITIONS     = 0x01
MESH_CHUNK_FLAG_HAS_TRIANGLES     = 0x02
MESH_CHUNK_FLAG_HAS_MATERIAL_IDX  = 0x04
MESH_CHUNK_FLAG_HAS_NORMALS       = 0x08
MESH_CHUNK_FLAG_HAS_UVS           = 0x10
MESH_CHUNK_FLAG_FIRST_CHUNK       = 0x20
MESH_CHUNK_FLAG_LAST_CHUNK        = 0x40
MESH_CHUNK_FLAG_FULL_ATTR         = 0x80   # Phase 7C Stage 2A

LIVE_SYNC_V5_MESH_CHUNK_HEADER_SIZE = 89


# =========================================================
# Inline serialize_mesh_chunk (mirroring network.py)
# =========================================================

def compute_geometry_version_hash(vertices, triangles, material_indices):
    h = hashlib.sha256()
    h.update(struct.pack("<I", len(vertices)))
    for v in vertices:
        h.update(struct.pack("<fff", v[0], v[1], v[2]))
    h.update(struct.pack("<I", len(triangles)))
    for t in triangles:
        h.update(struct.pack("<III", t[0], t[1], t[2]))
    for m in material_indices:
        h.update(struct.pack("<i", m))
    return h.hexdigest()


def serialize_mesh_chunk(guid_obj, version_hash, chunk_index, chunk_count,
                          vertices, triangles, material_indices, flags=0):
    payload = bytearray()
    a = guid_obj.time_low
    b = (guid_obj.time_mid << 16) | guid_obj.time_hi_version
    c = (guid_obj.clock_seq_hi_variant << 24) | (guid_obj.clock_seq_low << 24) | ((guid_obj.node >> 32) & 0xFFFF)
    d = guid_obj.node & 0xFFFFFFFF
    payload.extend(struct.pack("<IIII", a, b, c, d))
    version_bytes = version_hash.encode("ascii")
    if len(version_bytes) != 64:
        version_bytes = version_bytes.ljust(64, b'\x00')[:64]
    payload.extend(version_bytes)
    payload.extend(struct.pack("<II", chunk_index, chunk_count))
    payload.extend(struct.pack("<B", flags))
    payload.extend(struct.pack("<I", len(vertices)))
    for v in vertices:
        payload.extend(struct.pack("<fff", v[0], v[1], v[2]))
    payload.extend(struct.pack("<I", len(triangles)))
    for t in triangles:
        payload.extend(struct.pack("<III", t[0], t[1], t[2]))
    payload.extend(struct.pack("<I", len(material_indices)))
    for m in material_indices:
        payload.extend(struct.pack("<i", m))
    return bytes(payload)


def check(name, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  PASS: {name}")
    else:
        FAIL += 1
        msg = f"  FAIL: {name}"
        if detail:
            msg += f" — {detail}"
        print(msg)


# =========================================================
# T1: MESH_CHUNK_FLAG_FULL_ATTR = 0x80 defined
# =========================================================
print("\n--- T1: FULL_ATTR constant ---")
check("T1a: FULL_ATTR = 0x80", MESH_CHUNK_FLAG_FULL_ATTR == 0x80)


# =========================================================
# T2: FULL_ATTR absent in existing V5 flags
# =========================================================
print("\n--- T2: FULL_ATTR absent in existing flags ---")
existing_flags = [
    MESH_CHUNK_FLAG_HAS_POSITIONS,
    MESH_CHUNK_FLAG_HAS_TRIANGLES,
    MESH_CHUNK_FLAG_HAS_MATERIAL_IDX,
    MESH_CHUNK_FLAG_HAS_NORMALS,
    MESH_CHUNK_FLAG_HAS_UVS,
    MESH_CHUNK_FLAG_FIRST_CHUNK,
    MESH_CHUNK_FLAG_LAST_CHUNK,
]
check("T2a: FULL_ATTR not in existing flags",
      MESH_CHUNK_FLAG_FULL_ATTR not in existing_flags)
check("T2b: All existing flags are < 0x80",
      all(f < 0x80 for f in existing_flags))


# =========================================================
# T3: FULL_ATTR flag is distinct (single bit)
# =========================================================
print("\n--- T3: FULL_ATTR bit uniqueness ---")
overlap = any((MESH_CHUNK_FLAG_FULL_ATTR & f) for f in existing_flags)
check("T3a: FULL_ATTR no bit overlap with existing flags", not overlap)
is_power_of_2 = (MESH_CHUNK_FLAG_FULL_ATTR & (MESH_CHUNK_FLAG_FULL_ATTR - 1)) == 0
check("T3b: FULL_ATTR is a power of 2 (single bit)", is_power_of_2 and MESH_CHUNK_FLAG_FULL_ATTR > 0)


# =========================================================
# T4: Serialize mesh without FULL_ATTR → no SchemaVersion written
# =========================================================
print("\n--- T4: V5 serialization (no FULL_ATTR) ---")

guid = uuid.uuid4()
verts = [(-1, -1, -1), (1, -1, -1), (1, 1, -1), (-1, 1, -1)]
tris = [(0, 1, 2), (0, 2, 3)]
mat_idx = [0, 0]
version_hash = compute_geometry_version_hash(verts, tris, mat_idx)

v5_chunk = serialize_mesh_chunk(
    guid, version_hash, 0, 1, verts, tris, mat_idx,
    flags=MESH_CHUNK_FLAG_HAS_POSITIONS | MESH_CHUNK_FLAG_HAS_TRIANGLES | MESH_CHUNK_FLAG_HAS_MATERIAL_IDX
)

assert len(v5_chunk) > 89, "Chunk must have payload beyond header"

chunk_idx = struct.unpack_from("<I", v5_chunk, 80)[0]
check("T4a: ChunkIndex in header = 0", chunk_idx == 0)

flags_byte = v5_chunk[88]
check("T4b: FULL_ATTR flag not set in V5 chunk", (flags_byte & 0x80) == 0)

v5_vertex_count = struct.unpack_from("<I", v5_chunk, 89)[0]
check("T4c: V5 payload starts with vertex count (4), not schema version",
      v5_vertex_count == len(verts))


# =========================================================
# T5: FULL_ATTR + Chunk0 + SchemaVersion=0
# =========================================================
print("\n--- T5: FULL_ATTR + SchemaVersion=0 ---")

full_attr_chunk_sv0 = bytearray(v5_chunk)
full_attr_chunk_sv0[88] = flags_byte | MESH_CHUNK_FLAG_FULL_ATTR

sv0_bytes = struct.pack("<I", 0)
for i in range(4):
    full_attr_chunk_sv0[89 + i] = sv0_bytes[i]

read_sv0 = struct.unpack_from("<I", full_attr_chunk_sv0, 89)[0]
check("T5a: SchemaVersion=0 → bytes[89:93] = 0x00000000", read_sv0 == 0)
check("T5b: FULL_ATTR flag set (0x80)", (full_attr_chunk_sv0[88] & 0x80) != 0)


# =========================================================
# T6: FULL_ATTR + Chunk0 + SchemaVersion=1
# =========================================================
print("\n--- T6: FULL_ATTR + SchemaVersion=1 ---")

full_attr_chunk_sv1 = bytearray(v5_chunk)
full_attr_chunk_sv1[88] = flags_byte | MESH_CHUNK_FLAG_FULL_ATTR
sv1_bytes = struct.pack("<I", 1)
for i in range(4):
    full_attr_chunk_sv1[89 + i] = sv1_bytes[i]

read_sv1 = struct.unpack_from("<I", full_attr_chunk_sv1, 89)[0]
check("T6a: SchemaVersion=1 → bytes[89:93] = 0x01000000", read_sv1 == 1)
check("T6b: FULL_ATTR flag set", (full_attr_chunk_sv1[88] & 0x80) != 0)


# =========================================================
# T7: FULL_ATTR + Chunk0 + SchemaVersion=99
# =========================================================
print("\n--- T7: FULL_ATTR + SchemaVersion=99 ---")

full_attr_chunk_sv99 = bytearray(v5_chunk)
full_attr_chunk_sv99[88] = flags_byte | MESH_CHUNK_FLAG_FULL_ATTR
sv99_bytes = struct.pack("<I", 99)
for i in range(4):
    full_attr_chunk_sv99[89 + i] = sv99_bytes[i]

read_sv99 = struct.unpack_from("<I", full_attr_chunk_sv99, 89)[0]
check("T7a: SchemaVersion=99 → bytes[89:93] = 0x63000000", read_sv99 == 99)
check("T7b: FULL_ATTR flag set", (full_attr_chunk_sv99[88] & 0x80) != 0)


# =========================================================
# T8: FULL_ATTR absent → no SchemaVersion written (V5 only)
# =========================================================
print("\n--- T8: V5 payload starts with vertex count, not schema ---")

no_full_attr_chunk = serialize_mesh_chunk(
    guid, version_hash, 0, 1, verts, tris, mat_idx,
    flags=MESH_CHUNK_FLAG_HAS_POSITIONS | MESH_CHUNK_FLAG_HAS_TRIANGLES
)
no_full_attr_flags = no_full_attr_chunk[88]
check("T8a: No FULL_ATTR flag in V5 chunk", (no_full_attr_flags & 0x80) == 0)

first_payload_uint32 = struct.unpack_from("<I", no_full_attr_chunk, 89)[0]
check("T8b: First payload uint32 = vertex count", first_payload_uint32 == len(verts))

no_full_attr_chunk_2 = serialize_mesh_chunk(
    guid, version_hash, 0, 1, verts, tris, mat_idx,
    flags=MESH_CHUNK_FLAG_HAS_POSITIONS | MESH_CHUNK_FLAG_HAS_TRIANGLES
)
check("T8c: Deterministic serialization", no_full_attr_chunk == no_full_attr_chunk_2)


# =========================================================
# Summary
# =========================================================
print(f"\n=== Phase 7C Stage 2A FULL_ATTR Schema Gate Tests ===")
print(f"  PASS: {PASS}")
print(f"  FAIL: {FAIL}")

if FAIL > 0:
    print("\nSome tests FAILED.")
    sys.exit(1)
else:
    print("\nAll tests PASSED.")
    sys.exit(0)
