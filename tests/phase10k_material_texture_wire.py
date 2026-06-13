#!/usr/bin/env python3
"""
Phase 10K.1 — MTEX Texture Map Identity: Wire Format Tests

Tests:
  1. MTEX magic/version constants exist
  2. Channel enum constants exist
  3. Flags constants exist
  4. MTEX records use uint16 path length (not fragile fixed-size buffer)
  5. serialize_material_slots appends MTEX after MATX
  6. serialize_material_slots appends MTEX without MATX
  7. serialize_material_slots does NOT append MTEX when texture_maps=None
  8. serialize_material_slots does NOT append MTEX when texture_maps empty
  9. Path length uses uint16 correctly
  10. ImageName length uses uint8 correctly
  11. MTEX record round-trip: serialize → parse
  12. MTEX backward compat: old parser ignores MTEX
  13. No MTEX when texture_maps is None or empty
  14. Multiple slots in MTEX
  15. Multiple records per slot
  16. Path clamping to MTEX_MAX_PATH_LEN
  17. Image name clamping to MTEX_MAX_IMAGE_NAME_LEN
  18. Empty path with packed image still sends image name
  19. UTF-8 path encoding
  20. MTEX + MATX + old identity block all round-trip correctly
"""

import struct
import sys
import uuid
import os

PASS = 0
FAIL = 0
SKIP = 0
RESULTS = []


# =========================================================
# MTEX constants (mirroring network.py)
# =========================================================

MTEX_MAGIC = 0x4D544558
MTEX_VERSION = 1
MTEX_CHANNEL_BASECOLOR = 1
MTEX_CHANNEL_ROUGHNESS = 2
MTEX_CHANNEL_METALLIC = 3
MTEX_CHANNEL_ALPHA = 4
MTEX_CHANNEL_NORMAL = 5
MTEX_FLAG_PATH_ABSOLUTE = 0x01
MTEX_FLAG_IMAGE_PACKED = 0x02
MTEX_FLAG_COLORSPACE_SRGB = 0x04
MTEX_FLAG_COLORSPACE_NON_COLOR = 0x08
MTEX_MAX_PATH_LEN = 2048
MTEX_MAX_IMAGE_NAME_LEN = 255
MAX_MATERIAL_SLOTS = 8

MATX_MAGIC = 0x4D415458
MATX_VERSION = 1

# Per-record wire size bounds
MTEX_RECORD_MIN_SIZE = 6   # SlotIndex(1) + Channel(1) + Flags(1) + PathLen(2) + ImageNameLen(1)
MTEX_HEADER_SIZE = 6       # Magic(4) + Version(1) + RecordCount(1)


# =========================================================
# Simulated Blender serializer (mirrors network.py logic)
# =========================================================

def _encode_guid(guid_obj):
    a = guid_obj.time_low
    b = (guid_obj.time_mid << 16) | guid_obj.time_hi_version
    c = (guid_obj.clock_seq_hi_variant << 24) | (guid_obj.clock_seq_low << 16) | ((guid_obj.node >> 32) & 0xFFFF)
    d = guid_obj.node & 0xFFFFFFFF
    return struct.pack("<IIII", a, b, c, d)


def simulate_serialize_material_slots(guid_obj, slots, properties=None, texture_maps=None):
    """Simulated version of serialize_material_slots from network.py."""
    payload = bytearray()
    payload.extend(_encode_guid(guid_obj))
    slot_count = min(len(slots), MAX_MATERIAL_SLOTS)
    payload.extend(struct.pack("<B", slot_count))
    for slot_index in range(slot_count):
        low, high = slots.get(slot_index, (0, 0))
        payload.extend(struct.pack("<B", slot_index & 0xFF))
        payload.extend(struct.pack("<QQ", low & 0xFFFFFFFFFFFFFFFF, high & 0xFFFFFFFFFFFFFFFF))

    # MATX extension
    if properties is not None and properties:
        ext_slot_count = min(len(properties), MAX_MATERIAL_SLOTS)
        payload.extend(struct.pack("<I", MATX_MAGIC))
        payload.extend(struct.pack("<B", MATX_VERSION))
        payload.extend(struct.pack("<B", ext_slot_count))
        for slot_index in range(ext_slot_count):
            p = properties.get(slot_index)
            if p is None:
                payload.extend(struct.pack("<B", slot_index & 0xFF))
                payload.extend(struct.pack("<ffff", 0.8, 0.8, 0.8, 1.0))
                payload.extend(struct.pack("<ff", 0.5, 0.0))
            else:
                payload.extend(struct.pack("<B", slot_index & 0xFF))
                payload.extend(struct.pack("<ffff",
                    p.get("BaseColorR", 0.8), p.get("BaseColorG", 0.8),
                    p.get("BaseColorB", 0.8), p.get("Alpha", 1.0)))
                payload.extend(struct.pack("<ff",
                    p.get("Roughness", 0.5), p.get("Metallic", 0.0)))

    # MTEX extension
    if texture_maps is not None and texture_maps:
        flat_records = []
        for slot_index in sorted(texture_maps.keys()):
            records = texture_maps[slot_index]
            if not records:
                continue
            for rec in records:
                channel, filepath, image_name, flags = rec
                flat_records.append((slot_index, channel, filepath, image_name, flags))

        if flat_records:
            rec_count = len(flat_records)
            payload.extend(struct.pack("<I", MTEX_MAGIC))
            payload.extend(struct.pack("<B", MTEX_VERSION))
            payload.extend(struct.pack("<B", rec_count))

            for slot_index, channel, filepath, image_name, flags in flat_records:
                path_bytes = filepath.encode("utf-8", errors="replace")
                if len(path_bytes) > MTEX_MAX_PATH_LEN:
                    path_bytes = path_bytes[:MTEX_MAX_PATH_LEN]
                name_bytes = image_name.encode("utf-8", errors="replace")
                if len(name_bytes) > MTEX_MAX_IMAGE_NAME_LEN:
                    name_bytes = name_bytes[:MTEX_MAX_IMAGE_NAME_LEN]
                path_len = len(path_bytes)
                name_len = len(name_bytes)
                payload.extend(struct.pack("<B", slot_index & 0xFF))
                payload.extend(struct.pack("<B", channel & 0xFF))
                payload.extend(struct.pack("<B", flags & 0xFF))
                payload.extend(struct.pack("<H", path_len))
                payload.extend(path_bytes)
                payload.extend(struct.pack("<B", name_len))
                payload.extend(name_bytes)

    return bytes(payload)


# =========================================================
# Simulated UE parser (mirrors UELiveSyncSubsystem.cpp logic)
# =========================================================

def simulate_parse_mtex(data, start_offset):
    """Parse MTEX block after old identity block (with optional MATX).
    
    Returns (records, error) where records is list of (slot, channel, flags, path, name).
    """
    ptr = start_offset
    remaining = len(data) - ptr

    # Must have at least MTEX header
    if remaining < MTEX_HEADER_SIZE:
        return None, "truncated_header"

    magic = struct.unpack_from("<I", data, ptr)[0]
    ptr += 4
    if magic != MTEX_MAGIC:
        return None, "no_mtex_magic"

    version = data[ptr]
    ptr += 1
    if version != MTEX_VERSION:
        return None, f"unsupported_version_{version}"

    rec_count = data[ptr]
    ptr += 1

    records = []
    for ri in range(rec_count):
        remaining = len(data) - ptr
        if remaining < MTEX_RECORD_MIN_SIZE:
            return records, f"truncated_record_{ri}"

        slot_idx = data[ptr]
        ptr += 1
        channel = data[ptr]
        ptr += 1
        flags = data[ptr]
        ptr += 1

        path_len = struct.unpack_from("<H", data, ptr)[0]
        ptr += 2

        if path_len > MTEX_MAX_PATH_LEN:
            return records, f"path_len_exceeds_max_{path_len}"

        if ptr + path_len > len(data):
            return records, f"path_exceeds_packet_{ri}"

        path = data[ptr:ptr + path_len].decode("utf-8", errors="replace") if path_len > 0 else ""
        ptr += path_len

        if ptr >= len(data):
            return records, f"missing_name_len_{ri}"

        name_len = data[ptr]
        ptr += 1

        if name_len > MTEX_MAX_IMAGE_NAME_LEN:
            return records, f"name_len_exceeds_max_{name_len}"

        if ptr + name_len > len(data):
            return records, f"name_exceeds_packet_{ri}"

        name = data[ptr:ptr + name_len].decode("utf-8", errors="replace") if name_len > 0 else ""
        ptr += name_len

        records.append((slot_idx, channel, flags, path, name))

    return records, None


def simulate_parse_old_block(data, offset=0):
    """Parse old identity block, return (guid, slot_count, end_ptr)."""
    ptr = offset
    if ptr + 17 > len(data):
        return None, 0, ptr
    guid_data = data[ptr:ptr+16]
    ptr += 16
    slot_count = data[ptr]
    ptr += 1
    for _ in range(slot_count):
        if ptr + 17 > len(data):
            return None, 0, ptr
        ptr += 17
    return guid_data, slot_count, ptr


def _test(name, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        RESULTS.append(f"  PASS  {name}")
    else:
        FAIL += 1
        msg = f"  FAIL  {name}"
        if detail:
            msg += f"  ({detail})"
        RESULTS.append(msg)


# =========================================================
# Tests
# =========================================================

def run_tests():
    test_guid = uuid.uuid4()
    dummy_slots = {0: (12345, 67890)}

    # ------------------------------------------------------------------
    # Test 1: MTEX constants
    # ------------------------------------------------------------------
    _test("MTEX magic matches expected", MTEX_MAGIC == 0x4D544558)
    _test("MTEX version is 1", MTEX_VERSION == 1)
    _test("Channel BaseColor = 1", MTEX_CHANNEL_BASECOLOR == 1)
    _test("Channel Roughness = 2", MTEX_CHANNEL_ROUGHNESS == 2)
    _test("Channel Metallic = 3", MTEX_CHANNEL_METALLIC == 3)
    _test("Channel Alpha = 4", MTEX_CHANNEL_ALPHA == 4)
    _test("Channel Normal = 5", MTEX_CHANNEL_NORMAL == 5)
    _test("Flag PATH_ABSOLUTE = 0x01", MTEX_FLAG_PATH_ABSOLUTE == 0x01)
    _test("Flag IMAGE_PACKED = 0x02", MTEX_FLAG_IMAGE_PACKED == 0x02)
    _test("Flag COLORSPACE_SRGB = 0x04", MTEX_FLAG_COLORSPACE_SRGB == 0x04)
    _test("Flag COLORSPACE_NON_COLOR = 0x08", MTEX_FLAG_COLORSPACE_NON_COLOR == 0x08)

    # ------------------------------------------------------------------
    # Test 2: MTEX appended after MATX
    # ------------------------------------------------------------------
    tex_maps = {0: [(MTEX_CHANNEL_BASECOLOR, "/path/to/albedo.png", "albedo.png",
                     MTEX_FLAG_PATH_ABSOLUTE | MTEX_FLAG_COLORSPACE_SRGB)]}
    mat_props = {0: {"BaseColorR": 0.9, "BaseColorG": 0.1, "BaseColorB": 0.2, "Alpha": 1.0,
                     "Roughness": 0.3, "Metallic": 0.0}}
    payload = simulate_serialize_material_slots(test_guid, dummy_slots, mat_props, tex_maps)

    # Parse old block
    _, _, end_ptr = simulate_parse_old_block(payload, 0)
    # Should see MATX then MTEX
    matx_magic_found = struct.unpack_from("<I", payload, end_ptr)[0] == MATX_MAGIC
    _test("MATX block present after identity", matx_magic_found)

    if matx_magic_found:
        # Find MATX end
        matx_header_end = end_ptr + 6  # magic(4) + version(1) + count(1)
        ext_slot_count = payload[end_ptr + 5]
        matx_end = matx_header_end + ext_slot_count * 25  # MATX_PROP_SLOT_SIZE
        mtex_magic_found = struct.unpack_from("<I", payload, matx_end)[0] == MTEX_MAGIC
        _test("MTEX block present after MATX", mtex_magic_found)
    else:
        _test("MTEX block present after MATX", False, "No MATX found")

    # ------------------------------------------------------------------
    # Test 3: MTEX without MATX
    # ------------------------------------------------------------------
    payload2 = simulate_serialize_material_slots(test_guid, dummy_slots, None, tex_maps)
    _, _, end_ptr2 = simulate_parse_old_block(payload2, 0)
    mtex_magic_found2 = struct.unpack_from("<I", payload2, end_ptr2)[0] == MTEX_MAGIC
    _test("MTEX without MATX detected", mtex_magic_found2)

    # ------------------------------------------------------------------
    # Test 4: No MTEX when texture_maps is None
    # ------------------------------------------------------------------
    payload3 = simulate_serialize_material_slots(test_guid, dummy_slots, mat_props, None)
    _, _, end_ptr3 = simulate_parse_old_block(payload3, 0)
    # Find MATX first
    matx_end3 = end_ptr3 + 6 + 1 * 25
    remaining3 = len(payload3) - matx_end3
    _test("No MTEX when texture_maps=None",
          matx_end3 >= len(payload3) or struct.unpack_from("<I", payload3, matx_end3)[0] != MTEX_MAGIC)

    # ------------------------------------------------------------------
    # Test 5: No MTEX when texture_maps empty dict
    # ------------------------------------------------------------------
    payload4 = simulate_serialize_material_slots(test_guid, dummy_slots, mat_props, {})
    _, _, end_ptr4 = simulate_parse_old_block(payload4, 0)
    matx_end4 = end_ptr4 + 6 + 1 * 25
    _test("No MTEX when texture_maps={}",
          matx_end4 >= len(payload4))

    # ------------------------------------------------------------------
    # Test 6: No MTEX when texture_maps has empty slot list
    # ------------------------------------------------------------------
    payload5 = simulate_serialize_material_slots(test_guid, dummy_slots, mat_props, {0: []})
    _, _, end_ptr5 = simulate_parse_old_block(payload5, 0)
    matx_end5 = end_ptr5 + 6 + 1 * 25
    _test("No MTEX when slot has empty record list",
          matx_end5 >= len(payload5))

    # ------------------------------------------------------------------
    # Test 7: Path length uses uint16
    # ------------------------------------------------------------------
    tex_maps7 = {0: [(MTEX_CHANNEL_ROUGHNESS, "/path/rough.png", "rough.png", 0)]}
    payload7 = simulate_serialize_material_slots(test_guid, dummy_slots, None, tex_maps7)
    _, _, end_ptr7 = simulate_parse_old_block(payload7, 0)
    ptr7 = end_ptr7 + 6  # skip MTEX header
    path_len_field = struct.unpack_from("<H", payload7, ptr7 + 3)[0]  # after slot(1)+channel(1)+flags(1)
    _test("Path length uses uint16 (2 bytes)", path_len_field == len("/path/rough.png"))

    # ------------------------------------------------------------------
    # Test 8: ImageName length uses uint8
    # ------------------------------------------------------------------
    ptr8 = ptr7 + 3 + 2 + path_len_field  # skip header + path
    name_len_field = payload8_val = payload7[ptr8] if ptr8 < len(payload7) else 255
    if ptr8 < len(payload7):
        name_len_field = payload7[ptr8]
        _test("ImageName length uses uint8 (1 byte)", name_len_field == len("rough.png"))

    # ------------------------------------------------------------------
    # Test 9: Round-trip: serialize → parse
    # ------------------------------------------------------------------
    records, err = simulate_parse_mtex(payload7, end_ptr7)
    _test("MTEX round-trip parses successfully", err is None)
    _test("MTEX round-trip record count", records and len(records) == 1)
    if records:
        _test("MTEX round-trip channel",
              records[0][1] == MTEX_CHANNEL_ROUGHNESS)
        _test("MTEX round-trip path",
              records[0][3] == "/path/rough.png")
        _test("MTEX round-trip image name",
              records[0][4] == "rough.png")

    # ------------------------------------------------------------------
    # Test 10: Multiple slots in MTEX
    # ------------------------------------------------------------------
    tex_maps10 = {
        0: [(MTEX_CHANNEL_BASECOLOR, "/tex/base.png", "base.png",
             MTEX_FLAG_PATH_ABSOLUTE | MTEX_FLAG_COLORSPACE_SRGB)],
        1: [(MTEX_CHANNEL_ROUGHNESS, "/tex/rough.png", "rough.png",
             MTEX_FLAG_PATH_ABSOLUTE | MTEX_FLAG_COLORSPACE_NON_COLOR)]
    }
    payload10 = simulate_serialize_material_slots(test_guid, {0: (1,2), 1: (3,4)}, None, tex_maps10)
    _, _, end_ptr10 = simulate_parse_old_block(payload10, 0)
    records10, err10 = simulate_parse_mtex(payload10, end_ptr10)
    _test("Multi-slot MTEX parses", err10 is None and len(records10) == 2)
    if records10:
        slots_found = {r[0] for r in records10}
        _test("Slot 0 in multi-slot MTEX", 0 in slots_found)
        _test("Slot 1 in multi-slot MTEX", 1 in slots_found)

    # ------------------------------------------------------------------
    # Test 11: Multiple records per slot
    # ------------------------------------------------------------------
    tex_maps11 = {
        0: [
            (MTEX_CHANNEL_BASECOLOR, "/tex/base.png", "base.png",
             MTEX_FLAG_PATH_ABSOLUTE | MTEX_FLAG_COLORSPACE_SRGB),
            (MTEX_CHANNEL_ROUGHNESS, "/tex/rough.png", "rough.png",
             MTEX_FLAG_PATH_ABSOLUTE | MTEX_FLAG_COLORSPACE_NON_COLOR)
        ]
    }
    payload11 = simulate_serialize_material_slots(test_guid, dummy_slots, None, tex_maps11)
    _, _, end_ptr11 = simulate_parse_old_block(payload11, 0)
    records11, err11 = simulate_parse_mtex(payload11, end_ptr11)
    _test("Multi-record per slot MTEX parses", err11 is None and len(records11) == 2)
    if records11:
        channels_found = {r[1] for r in records11}
        _test("BaseColor in multi-record", MTEX_CHANNEL_BASECOLOR in channels_found)
        _test("Roughness in multi-record", MTEX_CHANNEL_ROUGHNESS in channels_found)

    # ------------------------------------------------------------------
    # Test 12: Empty path with packed image still sends name
    # ------------------------------------------------------------------
    tex_maps12 = {
        0: [(MTEX_CHANNEL_BASECOLOR, "", "PackedImage",
             MTEX_FLAG_IMAGE_PACKED | MTEX_FLAG_COLORSPACE_SRGB)]
    }
    payload12 = simulate_serialize_material_slots(test_guid, dummy_slots, None, tex_maps12)
    _, _, end_ptr12 = simulate_parse_old_block(payload12, 0)
    records12, err12 = simulate_parse_mtex(payload12, end_ptr12)
    _test("Packed image MTEX parses", err12 is None and len(records12) == 1)
    if records12:
        _test("Packed image path empty", records12[0][3] == "")
        _test("Packed image name preserved", records12[0][4] == "PackedImage")
        _test("Packed flag preserved", records12[0][2] & MTEX_FLAG_IMAGE_PACKED)

    # ------------------------------------------------------------------
    # Test 13: Full round-trip: old + MATX + MTEX
    # ------------------------------------------------------------------
    tex_maps13 = {
        0: [(MTEX_CHANNEL_BASECOLOR, "/tex/albedo.png", "albedo.png",
             MTEX_FLAG_PATH_ABSOLUTE | MTEX_FLAG_COLORSPACE_SRGB)]
    }
    mat_props13 = {
        0: {"BaseColorR": 0.5, "BaseColorG": 0.6, "BaseColorB": 0.7, "Alpha": 1.0,
            "Roughness": 0.2, "Metallic": 0.8}
    }
    payload13 = simulate_serialize_material_slots(
        test_guid, {0: (111, 222)}, mat_props13, tex_maps13)
    _, _, end_old13 = simulate_parse_old_block(payload13, 0)

    # Verify MATX
    matx_magic13 = struct.unpack_from("<I", payload13, end_old13)[0]
    _test("Full round-trip: MATX present", matx_magic13 == MATX_MAGIC)

    # Find MTEX after MATX
    matx_end13 = end_old13 + 6 + 1 * 25
    records13, err13 = simulate_parse_mtex(payload13, matx_end13)
    _test("Full round-trip: MTX parses after MATX", err13 is None and len(records13) == 1)
    if records13:
        _test("Full round-trip: channel preserved", records13[0][1] == MTEX_CHANNEL_BASECOLOR)

    # ------------------------------------------------------------------
    # Test 14: Only old identity — no extensions
    # ------------------------------------------------------------------
    payload14 = simulate_serialize_material_slots(test_guid, dummy_slots, None, None)
    _, _, end_ptr14 = simulate_parse_old_block(payload14, 0)
    _test("Payload ends at old block when no extensions",
          end_ptr14 == len(payload14))

    # ------------------------------------------------------------------
    # Test 15: Path clamping
    # ------------------------------------------------------------------
    long_path = "x" * (MTEX_MAX_PATH_LEN + 50)
    tex_maps15 = {0: [(MTEX_CHANNEL_BASECOLOR, long_path, "long.png", 0)]}
    payload15 = simulate_serialize_material_slots(test_guid, dummy_slots, None, tex_maps15)
    _, _, end_ptr15 = simulate_parse_old_block(payload15, 0)
    records15, err15 = simulate_parse_mtex(payload15, end_ptr15)
    _test("Long path clamped", err15 is None and len(records15) == 1)
    if records15 and err15 is None:
        _test("Clamped path length <= MTEX_MAX_PATH_LEN",
              len(records15[0][3]) <= MTEX_MAX_PATH_LEN)

    # ------------------------------------------------------------------
    # Test 16: Name clamping
    # ------------------------------------------------------------------
    long_name = "n" * (MTEX_MAX_IMAGE_NAME_LEN + 50)
    tex_maps16 = {0: [(MTEX_CHANNEL_ROUGHNESS, "r.png", long_name, 0)]}
    payload16 = simulate_serialize_material_slots(test_guid, dummy_slots, None, tex_maps16)
    _, _, end_ptr16 = simulate_parse_old_block(payload16, 0)
    records16, err16 = simulate_parse_mtex(payload16, end_ptr16)
    _test("Long name clamped", err16 is None and len(records16) == 1)
    if records16:
        _test("Clamped name length <= MTEX_MAX_IMAGE_NAME_LEN",
              len(records16[0][4]) <= MTEX_MAX_IMAGE_NAME_LEN)

    # ------------------------------------------------------------------
    # Test 17: Backward compat — old parser ignores MTEX
    # ------------------------------------------------------------------
    payload17 = simulate_serialize_material_slots(test_guid, dummy_slots, None,
        {0: [(MTEX_CHANNEL_ALPHA, "alpha.png", "alpha.png", 0)]})
    guid_data, sc, _ = simulate_parse_old_block(payload17, 0)
    _test("Old parser reads identity with MTEX ignored",
          guid_data is not None and sc == 1)

    # ------------------------------------------------------------------
    # Test 18: Both slots and records sorted by slot index
    # ------------------------------------------------------------------
    tex_maps18 = {
        2: [(MTEX_CHANNEL_NORMAL, "n.png", "n.png", MTEX_FLAG_COLORSPACE_NON_COLOR)],
        0: [(MTEX_CHANNEL_BASECOLOR, "b.png", "b.png", MTEX_FLAG_COLORSPACE_SRGB)]
    }
    payload18 = simulate_serialize_material_slots(
        test_guid, {0: (1,2)}, None, tex_maps18)
    _, _, end_ptr18 = simulate_parse_old_block(payload18, 0)
    records18, _ = simulate_parse_mtex(payload18, end_ptr18)
    _test("Slot 0 appears before slot 2 in serialized order",
          records18 and records18[0][0] == 0 and records18[1][0] == 2)

    # ------------------------------------------------------------------
    # Test 19: Static analysis — constants in network.py
    # ------------------------------------------------------------------
    network_path = os.path.join(os.path.dirname(__file__), "..", "Blender_Addon", "network.py")
    if os.path.exists(network_path):
        with open(network_path, "r") as f:
            content = f.read()
        _test("MTEX_MAGIC constant in network.py", "MTEX_MAGIC" in content)
        _test("MTEX_MAX_PATH_LEN in network.py", "MTEX_MAX_PATH_LEN" in content)
        _test("MTEX_MAX_IMAGE_NAME_LEN in network.py", "MTEX_MAX_IMAGE_NAME_LEN" in content)
        _test("MTEX_FLAG_PATH_ABSOLUTE in network.py", "MTEX_FLAG_PATH_ABSOLUTE" in content)
        _test("MTEX_FLAG_IMAGE_PACKED in network.py", "MTEX_FLAG_IMAGE_PACKED" in content)
        _test("MTEX_FLAG_COLORSPACE_SRGB in network.py", "MTEX_FLAG_COLORSPACE_SRGB" in content)
        _test("MTEX_FLAG_COLORSPACE_NON_COLOR in network.py",
              "MTEX_FLAG_COLORSPACE_NON_COLOR" in content)
    else:
        RESULTS.append("  SKIP  Static analysis — network.py not found")
        global SKIP
        SKIP += 7

    # ------------------------------------------------------------------
    # Test 20: Static analysis — __init__.py passes texture_maps to serialize
    # ------------------------------------------------------------------
    init_path = os.path.join(os.path.dirname(__file__), "..",
                             "Blender_Addon", "__init__.py")
    if os.path.exists(init_path):
        with open(init_path, "r") as f:
            init_content = f.read()
        _test("__init__.py calls serialize_material_slots",
              "serialize_material_slots" in init_content)
        _test("__init__.py passes tex_maps to serialize_material_slots",
              "tex_maps" in init_content)
    else:
        RESULTS.append("  SKIP  Static analysis — __init__.py not found")
        SKIP += 2

    # ------------------------------------------------------------------
    # Test 21: Static analysis — constants in AssetIdentityTypes.h
    # ------------------------------------------------------------------
    header_path = os.path.join(os.path.dirname(__file__), "..",
        "UE_Plugin/UELiveSync/Source/UELiveSync/Public/AssetIdentityTypes.h")
    if os.path.exists(header_path):
        with open(header_path, "r") as f:
            content = f.read()
        _test("MTEX_MAGIC in AssetIdentityTypes.h",
              "MTEX_MAGIC" in content)
        _test("EMTEXChannel enum in AssetIdentityTypes.h",
              "EMTEXChannel" in content)
        _test("FMaterialTextureMapRef in AssetIdentityTypes.h",
              "FMaterialTextureMapRef" in content)
        _test("MTEX_RECORD_MIN_SIZE in AssetIdentityTypes.h",
              "MTEX_RECORD_MIN_SIZE" in content)
        _test("MTEX_RECORD_MAX_SIZE in AssetIdentityTypes.h",
              "MTEX_RECORD_MAX_SIZE" in content)
    else:
        RESULTS.append("  SKIP  Static analysis — AssetIdentityTypes.h not found")
        SKIP += 5


if __name__ == "__main__":
    run_tests()
    print(f"\nPhase 10K.1 — Wire Format: {PASS} passed, {FAIL} failed, {SKIP} skipped")
    for r in RESULTS:
        print(r)
    sys.exit(0 if FAIL == 0 else 1)
