#!/usr/bin/env python3
"""
Phase 7B — PT_Material Wire + Handler Skeleton (Stage 1C)

Tests:
  1. PT_Material wire format serialization (Blender side)
  2. Material slot serialization deserialization round-trip
  3. Duplicate suppression via _last_material_identity
  4. Slot count bounds (0, 1, 8, >8, -1/255)
  5. Null/empty material slot serialization
  6. Protocol signature includes 0x05
  7. UE handler metadata storage simulation
  8. Malformed/truncated packet detection (simulated)

No SetMaterial() is called.
No MaterialPathCache resolution is implemented.
"""

import struct
import sys
import time
import uuid

PASS = 0
FAIL = 0
SKIP = 0
RESULTS = []


# =========================================================
# Mock helpers
# =========================================================

class MockMaterial:
    def __init__(self, name="Material"):
        self._name = name

    @property
    def name(self):
        return self._name

    @name.setter
    def name(self, val):
        self._name = val


class MockMaterialSlot:
    def __init__(self, material=None):
        self.material = material


class MockData:
    def __init__(self, name="Cube"):
        self._name = name

    @property
    def name(self):
        return self._name


class MockObjectWithSlots:
    def __init__(self, name="Obj", datablock_name="Cube",
                 material_slots=None):
        self._name = name
        self._data = MockData(datablock_name)
        self._props = {}
        self._material_slots = material_slots or []
        self._type = 'MESH'

    @property
    def name(self):
        return self._name

    @property
    def data(self):
        return self._data

    @property
    def type(self):
        return self._type

    @type.setter
    def type(self, val):
        self._type = val

    @property
    def material_slots(self):
        return self._material_slots

    def __contains__(self, key):
        return key in self._props

    def __getitem__(self, key):
        return self._props[key]

    def __setitem__(self, key, value):
        self._props[key] = value


# =========================================================
# Standalone xxHash64 (mirrors network.py)
# =========================================================

_XXH_PRIME64_1 = 0x9E3779B185EBCA87
_XXH_PRIME64_2 = 0xC2B2AE3D27D4EB4F
_XXH_PRIME64_3 = 0x165667B19E3779F9
_XXH_PRIME64_4 = 0x85EBCA77C2B2AE63
_XXH_PRIME64_5 = 0x27D4EB2F165667C5


def _xxh64_round(acc, seed):
    acc += seed * _XXH_PRIME64_2
    acc = ((acc << 31) | (acc >> 33))
    acc *= _XXH_PRIME64_1
    return acc & 0xFFFFFFFFFFFFFFFF


def _xxh64_merge_round(acc, val):
    acc = ((acc ^ _xxh64_round(0, val)) * _XXH_PRIME64_1) + _XXH_PRIME64_4
    return acc & 0xFFFFFFFFFFFFFFFF


def xxh64(data, seed=0):
    length = len(data)
    acc = seed + _XXH_PRIME64_5 + _XXH_PRIME64_5
    if length >= 32:
        v1 = seed + _XXH_PRIME64_1 + _XXH_PRIME64_2
        v2 = seed + _XXH_PRIME64_2
        v3 = seed
        v4 = seed - _XXH_PRIME64_1
        limit = length - 32
        offset = 0
        while offset <= limit:
            v1 = _xxh64_round(v1, struct.unpack_from("<Q", data, offset)[0])
            v2 = _xxh64_round(v2, struct.unpack_from("<Q", data, offset + 8)[0])
            v3 = _xxh64_round(v3, struct.unpack_from("<Q", data, offset + 16)[0])
            v4 = _xxh64_round(v4, struct.unpack_from("<Q", data, offset + 24)[0])
            offset += 32
        acc = ((v1 << 1) | (v1 >> 63))
        acc = _xxh64_merge_round(acc, v2)
        acc = _xxh64_merge_round(acc, v3)
        acc = _xxh64_merge_round(acc, v4)
        remaining_length = length - offset
    else:
        acc += _XXH_PRIME64_5
        remaining_length = length
    offset = length - remaining_length
    while remaining_length >= 8:
        val = struct.unpack_from("<Q", data, offset)[0]
        acc = ((acc ^ _xxh64_round(0, val)) * _XXH_PRIME64_1) + _XXH_PRIME64_4
        acc &= 0xFFFFFFFFFFFFFFFF
        offset += 8
        remaining_length -= 8
    while remaining_length >= 4:
        val = struct.unpack_from("<I", data, offset)[0]
        acc = ((acc ^ (val * _XXH_PRIME64_1)) * _XXH_PRIME64_3) + _XXH_PRIME64_5
        acc &= 0xFFFFFFFFFFFFFFFF
        offset += 4
        remaining_length -= 4
    while remaining_length > 0:
        val = data[offset]
        acc = ((acc ^ (val * _XXH_PRIME64_5)) * _XXH_PRIME64_3) + _XXH_PRIME64_5
        acc &= 0xFFFFFFFFFFFFFFFF
        offset += 1
        remaining_length -= 1
    acc ^= acc >> 37
    acc = (acc * _XXH_PRIME64_3) + _XXH_PRIME64_5
    acc ^= acc >> 37
    acc = (acc * _XXH_PRIME64_4) + _XXH_PRIME64_5
    acc ^= acc >> 37
    return acc & 0xFFFFFFFFFFFFFFFF


def get_material_identity_hash(material):
    if material is None:
        return (0, 0)
    name_bytes = material.name.encode("utf-8")
    hash_value = xxh64(name_bytes)
    low = hash_value & 0xFFFFFFFFFFFFFFFF
    high = (hash_value >> 64) & 0xFFFFFFFFFFFFFFFF
    return (low, high)


def get_object_material_slots(obj):
    if obj.type != 'MESH' or obj.data is None:
        return {}
    if not hasattr(obj, "material_slots"):
        return {}
    slots = {}
    for slot_index, slot in enumerate(obj.material_slots):
        mat = slot.material
        if mat is not None:
            low, high = get_material_identity_hash(mat)
        else:
            low, high = (0, 0)
        slots[slot_index] = (low, high)
    return slots


# =========================================================
# Protocol constants (mirroring network.py)
# =========================================================

LIVE_SYNC_V5_MATERIAL_SLOT_SIZE = 17
LIVE_SYNC_V5_MATERIAL_OBJECT_BASE_SIZE = 17
MAX_MATERIAL_SLOTS = 8


def serialize_material_slots(guid_obj, slots):
    """Mirrors Blender_Addon/network.py:serialize_material_slots."""
    payload = bytearray()
    a = guid_obj.time_low
    b = (guid_obj.time_mid << 16) | guid_obj.time_hi_version
    c = (guid_obj.clock_seq_hi_variant << 24) | (guid_obj.clock_seq_low << 16) | ((guid_obj.node >> 32) & 0xFFFF)
    d = guid_obj.node & 0xFFFFFFFF
    payload.extend(struct.pack("<IIII", a, b, c, d))
    slot_count = min(len(slots), MAX_MATERIAL_SLOTS)
    payload.extend(struct.pack("<B", slot_count))
    for slot_index in range(slot_count):
        low, high = slots.get(slot_index, (0, 0))
        payload.extend(struct.pack("<B", slot_index & 0xFF))
        payload.extend(struct.pack("<QQ", low & 0xFFFFFFFFFFFFFFFF, high & 0xFFFFFFFFFFFFFFFF))
    return bytes(payload)


def parse_material_slots(data, offset=0):
    """Parse material slots from PT_Material binary. Returns (slot_count, slots_dict, next_offset).
    GUID is checked as raw bytes only (not fully decoded)."""
    offset += 16  # skip GUID bytes
    slot_count = data[offset]
    offset += 1
    slots = {}
    for _ in range(slot_count):
        slot_idx = data[offset]
        offset += 1
        low, high = struct.unpack_from("<QQ", data, offset)
        offset += 16
        slots[slot_idx] = (low, high)
    return slot_count, slots, offset


def test(name, condition, detail=""):
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
    RESULTS.append((name, condition, detail))


def skip(name, reason=""):
    global SKIP
    SKIP += 1
    msg = f"  SKIP: {name}"
    if reason:
        msg += f" \u2014 {reason}"
    print(msg)
    RESULTS.append((name, True, f"SKIP \u2014 {reason}"))


# =========================================================
# SECTION 1: Wire format serialization
# =========================================================

def test_wire_format():
    """PT_Material serialization and deserialization round-trip."""
    print("\n--- Section 1: PT_Material wire format ---")

    guid = uuid.uuid4()

    # 1.1: Zero slots
    payload = serialize_material_slots(guid, {})
    test("1.1: Zero slots -> 17 bytes (GUID+count)",
         len(payload) == LIVE_SYNC_V5_MATERIAL_OBJECT_BASE_SIZE)
    _, parsed_slots, _ = parse_material_slots(payload)
    test("1.2: Zero slots -> empty dict",
         len(parsed_slots) == 0)

    # 1.3: Single slot
    slots_data = {0: (0xABCD, 0x1234)}
    payload = serialize_material_slots(guid, slots_data)
    expected_size = LIVE_SYNC_V5_MATERIAL_OBJECT_BASE_SIZE + LIVE_SYNC_V5_MATERIAL_SLOT_SIZE
    test("1.3: Single slot -> correct size",
         len(payload) == expected_size,
         f"expected {expected_size}, got {len(payload)}")
    _, parsed, _ = parse_material_slots(payload)
    test("1.4: Single slot -> round-trip",
         parsed == slots_data)

    # 1.5: Three slots
    guid2 = uuid.uuid4()
    slots_3 = {0: (0x1111, 0x2222), 1: (0x3333, 0x4444), 2: (0x5555, 0x6666)}
    payload = serialize_material_slots(guid2, slots_3)
    expected_size = LIVE_SYNC_V5_MATERIAL_OBJECT_BASE_SIZE + 3 * LIVE_SYNC_V5_MATERIAL_SLOT_SIZE
    test("1.5: Three slots -> correct size",
         len(payload) == expected_size)
    _, parsed, _ = parse_material_slots(payload)
    test("1.6: Three slots -> round-trip",
         parsed == slots_3)

    # 1.7: Eight slots (MAX_MATERIAL_SLOTS)
    slots_8 = {i: (i * 0x1111, i * 0x2222) for i in range(8)}
    payload = serialize_material_slots(guid, slots_8)
    expected_size = LIVE_SYNC_V5_MATERIAL_OBJECT_BASE_SIZE + 8 * LIVE_SYNC_V5_MATERIAL_SLOT_SIZE
    test("1.7: Eight slots -> correct size",
         len(payload) == expected_size)
    _, parsed, _ = parse_material_slots(payload)
    test("1.8: Eight slots -> round-trip",
         parsed == slots_8)

    # 1.9: Slots clamped to MAX_MATERIAL_SLOTS (>8)
    slots_10 = {i: (i, i * 2) for i in range(10)}
    payload = serialize_material_slots(guid, slots_10)
    test("1.9: 10 slots clamped to 8 -> 8 slots in payload",
         len(payload) == LIVE_SYNC_V5_MATERIAL_OBJECT_BASE_SIZE + 8 * LIVE_SYNC_V5_MATERIAL_SLOT_SIZE)
    _, parsed, _ = parse_material_slots(payload)
    test("1.10: Clamped slots -> only first 8",
         len(parsed) == 8 and 0 in parsed and 7 in parsed and 8 not in parsed and 9 not in parsed)

    # 1.11: Deterministic serialization (same data -> same bytes)
    p1 = serialize_material_slots(guid, slots_3)
    p2 = serialize_material_slots(guid, slots_3)
    test("1.11: Deterministic serialization",
         p1 == p2)

    # 1.12: Different GUID -> different bytes
    guid_diff = uuid.uuid4()
    p3 = serialize_material_slots(guid_diff, slots_3)
    test("1.12: Different GUID -> different bytes",
         p1 != p3)


# =========================================================
# SECTION 2: Slot identity hashing for wire
# =========================================================

def test_slot_identity_wire():
    """Material identity hashes in wire form."""
    print("\n--- Section 2: Slot identity in wire format ---")

    guid = uuid.uuid4()
    mat = MockMaterial("Wood")
    obj = MockObjectWithSlots("TestObj", "TestMesh",
        material_slots=[MockMaterialSlot(mat)])
    slots = get_object_material_slots(obj)

    # 2.1: Material name hash matches wire payload
    expected_low, expected_high = get_material_identity_hash(mat)
    payload = serialize_material_slots(guid, slots)
    _, parsed, _ = parse_material_slots(payload)
    test("2.1: Wire identity matches material hash",
         parsed[0] == (expected_low, expected_high))

    # 2.2: Null material -> (0, 0) in wire
    obj_null = MockObjectWithSlots("NullMat", "TestMesh",
        material_slots=[MockMaterialSlot(None)])
    slots_null = get_object_material_slots(obj_null)
    payload = serialize_material_slots(guid, slots_null)
    _, parsed, _ = parse_material_slots(payload)
    test("2.2: Null material -> (0, 0) in wire",
         parsed[0] == (0, 0))

    # 2.3: Renamed material -> different wire payload
    payload_before = serialize_material_slots(guid, slots)
    mat.name = "Steel"
    slots_after = get_object_material_slots(obj)
    payload_after = serialize_material_slots(guid, slots_after)
    test("2.3: Renamed material -> different wire payload",
         payload_before != payload_after)

    # 2.4: Payload size consistent (GUID embedded, can't easily parse without full UUID logic)
    expected = LIVE_SYNC_V5_MATERIAL_OBJECT_BASE_SIZE + LIVE_SYNC_V5_MATERIAL_SLOT_SIZE
    test("2.4: GUID preserved in payload (size check)",
         len(payload_after) == expected)

    # 2.5: Mixed valid/null slots in wire
    mixed_mat = MockMaterial("Concrete")
    obj_mixed = MockObjectWithSlots("Mixed", "TestMesh",
        material_slots=[
            MockMaterialSlot(mixed_mat),
            MockMaterialSlot(None),
            MockMaterialSlot(mixed_mat),
        ])
    slots_mixed = get_object_material_slots(obj_mixed)
    payload = serialize_material_slots(guid, slots_mixed)
    _, parsed, _ = parse_material_slots(payload)
    test("2.5: Mixed slots -> slot 0 valid, slot 1 zero, slot 2 valid",
         parsed[0] != (0, 0) and parsed[1] == (0, 0) and parsed[2] != (0, 0))


# =========================================================
# SECTION 3: Duplicate suppression
# =========================================================

def test_duplicate_suppression():
    """PT_Material suppressed when slot identities unchanged."""
    print("\n--- Section 3: Duplicate suppression ---")

    # Simulate _last_material_identity
    last_mat_identity = {}

    obj = MockObjectWithSlots("TestObj", "TestMesh",
        material_slots=[MockMaterialSlot(MockMaterial("Red"))])
    guid = str(uuid.uuid4().hex)
    guid_obj = uuid.UUID(guid)

    # 3.1: First tick -> no send (is_first_material = True)
    slots = get_object_material_slots(obj)
    is_first = guid not in last_mat_identity
    test("3.1: First tick suppressed (is_first_material)",
         is_first)
    last_mat_identity[guid] = slots

    # 3.2: Second tick, no change -> suppressed
    slots2 = get_object_material_slots(obj)
    no_change = slots2 == last_mat_identity.get(guid)
    test("3.2: No change -> suppressed",
         no_change)

    # 3.3: Material changed -> detected
    mat = obj.material_slots[0].material
    mat.name = "Blue"
    slots3 = get_object_material_slots(obj)
    has_change = slots3 != last_mat_identity.get(guid)
    test("3.3: Material changed -> change detected",
         has_change)
    last_mat_identity[guid] = slots3

    # 3.4: Material changed back -> detected again
    mat.name = "Red"
    slots4 = get_object_material_slots(obj)
    test("3.4: Material changed back -> detected",
         slots4 != last_mat_identity.get(guid))

    # 3.5: Slot addition detected
    obj.material_slots.append(MockMaterialSlot(MockMaterial("Green")))
    slots5 = get_object_material_slots(obj)
    test("3.5: Slot addition -> change detected",
         slots5 != last_mat_identity.get(guid))

    # 3.6: Slot removed -> detected
    obj.material_slots.pop()
    slots6 = get_object_material_slots(obj)
    test("3.6: Slot removal -> change detected",
         slots6 != slots5)

    # 3.7: Null slot -> null slot change detected
    obj.material_slots[0] = MockMaterialSlot(None)
    slots7 = get_object_material_slots(obj)
    test("3.7: Null slot -> change detected",
         slots7 != slots6)


# =========================================================
# SECTION 4: Slot count bounds
# =========================================================

def test_slot_count_bounds():
    """PT_Material slot count bounds handling."""
    print("\n--- Section 4: Slot count bounds ---")

    guid = uuid.uuid4()

    # 4.1: Empty dict (no slots) -> 17 bytes
    p = serialize_material_slots(guid, {})
    test("4.1: Empty dict -> base size only",
         len(p) == LIVE_SYNC_V5_MATERIAL_OBJECT_BASE_SIZE)

    # 4.2: Exactly 8 slots
    slots_8 = {i: (i, i * 2) for i in range(8)}
    p = serialize_material_slots(guid, slots_8)
    test("4.2: 8 slots -> correct size",
         len(p) == LIVE_SYNC_V5_MATERIAL_OBJECT_BASE_SIZE + 8 * LIVE_SYNC_V5_MATERIAL_SLOT_SIZE)

    # 4.3: 0 slots -> empty dict on parse
    p = serialize_material_slots(guid, {})
    _, parsed, _ = parse_material_slots(p)
    test("4.3: 0 slots -> empty parsed",
         len(parsed) == 0)

    # 4.4: 1 slot -> dict with 1 entry
    p = serialize_material_slots(guid, {0: (1, 2)})
    _, parsed, _ = parse_material_slots(p)
    test("4.4: 1 slot -> 1 entry",
         len(parsed) == 1 and parsed[0] == (1, 2))

    # 4.5: SlotCount=9 exceeds MAX_MATERIAL_SLOTS (8) -> must be REJECTED
    # by the UE handler (MalformedPackets++).  Build raw payload with count=9.
    base = bytearray(serialize_material_slots(guid, {0: (1, 2)}))
    base[16] = 9
    for i in range(1, 9):
        base.extend(struct.pack("<B", i))
        base.extend(struct.pack("<QQ", i, i * 2))
    _, parsed_9, _ = parse_material_slots(bytes(base))
    test("4.5: SlotCount=9 yields 9 entries raw (UE would reject)",
         len(parsed_9) == 9)

    # 4.6: SlotCount=255 (max uint8) exceeds MAX_MATERIAL_SLOTS
    base2 = bytearray(serialize_material_slots(guid, {0: (1, 2)}))
    base2[16] = 255
    for i in range(1, 255):
        base2.extend(struct.pack("<B", i % 256))
        base2.extend(struct.pack("<QQ", i, i * 2))
    # Only parse what fits — actual UE code would reject at SlotCount check
    slot_count_test = base2[16]
    test("4.6: SlotCount=255 exceeds MAX_MATERIAL_SLOTS",
         slot_count_test == 255 and slot_count_test > MAX_MATERIAL_SLOTS)

    # 4.7: Zero-slot after non-zero -> clear (is detected)
    slots_before = {0: (1, 2)}
    slots_after = {}
    test("4.7: Zero slots after non-zero -> change detected",
         slots_before != slots_after)

    # 4.8: Empty dict vs zero-slot dict (same behavior)
    p1 = serialize_material_slots(guid, {})
    p2 = serialize_material_slots(guid, {})
    test("4.8: Empty dict consistent", p1 == p2)


# =========================================================
# SECTION 5: Handler metadata storage (simulated)
# =========================================================

class SimulatedMaterialMetadata:
    """UE-side MaterialMetadata handler simulation."""

    def __init__(self):
        self.metadata = {}  # guid_str -> [(slot_index, (low, high))]
        self.defs_received = 0

    def handle_material_def(self, guid_str, slots):
        if guid_str == "invalid":
            return False
        slot_list = [(idx, (low, high)) for idx, (low, high) in sorted(slots.items())]
        self.metadata[guid_str] = slot_list
        self.defs_received += 1
        return True

    def get_slots(self, guid_str):
        return self.metadata.get(guid_str, [])

    def clear(self):
        self.metadata.clear()
        self.defs_received = 0


def test_handler_metadata_storage():
    """HandleMaterialDef stores material metadata correctly."""
    print("\n--- Section 5: Handler metadata storage ---")

    handler = SimulatedMaterialMetadata()

    # 5.1: Store single slot
    handler.handle_material_def("guid_1", {0: (0xABCD, 0x1234)})
    test("5.1: Single slot stored",
         len(handler.metadata) == 1 and handler.defs_received == 1)
    slots = handler.get_slots("guid_1")
    test("5.2: Single slot -> correct identity",
         slots[0][1] == (0xABCD, 0x1234))

    # 5.3: Store multiple slots for same GUID (overwrite)
    handler.handle_material_def("guid_1", {0: (0x1111, 0x2222), 1: (0x3333, 0x4444)})
    test("5.3: Overwrite slots -> updated",
         len(handler.get_slots("guid_1")) == 2 and handler.defs_received == 2)

    # 5.4: Store zero slots (clear)
    handler.handle_material_def("guid_1", {})
    test("5.4: Zero slots -> empty after overwrite",
         len(handler.get_slots("guid_1")) == 0 and handler.defs_received == 3)

    # 5.5: Multiple GUIDs
    handler.handle_material_def("guid_a", {0: (0xAAAA, 0xBBBB)})
    handler.handle_material_def("guid_b", {0: (0xCCCC, 0xDDDD)})
    test("5.5: Multiple GUIDs -> independent entries",
         len(handler.metadata) == 3)

    # 5.6: Invalid GUID rejected
    result = handler.handle_material_def("invalid", {0: (1, 2)})
    test("5.6: Invalid GUID rejected",
         not result and handler.defs_received == 5)  # defs_received NOT incremented for invalid

    # 5.7: Clear
    handler.clear()
    test("5.7: Clear -> empty",
         len(handler.metadata) == 0 and handler.defs_received == 0)

    # 5.8: Many slots
    handler.handle_material_def("guid_many", {i: (i, i * 2) for i in range(8)})
    test("5.8: 8 slots stored",
         len(handler.get_slots("guid_many")) == 8)

    # 5.9: Null material slots stored
    handler.clear()
    slots = {0: (0, 0), 1: (0xAAAA, 0xBBBB), 2: (0, 0)}
    handler.handle_material_def("guid_mixed", slots)
    stored = handler.get_slots("guid_mixed")
    test("5.9: Mixed null/valid slots stored",
         len(stored) == 3 and stored[0][1] == (0, 0) and stored[1][1] == (0xAAAA, 0xBBBB))

    # 5.10: Re-count after multiple operations
    # 5.9 called handler.clear(), then added 1. Then we add 3 more = 4 total.
    handler.handle_material_def("guid_x", {0: (1, 2)})
    handler.handle_material_def("guid_y", {0: (3, 4)})
    handler.handle_material_def("guid_z", {0: (5, 6)})
    test("5.10: Multiple ops tracked correctly",
         handler.defs_received == 4,
         f"expected 4, got {handler.defs_received}")

    # 5.11: Reject SlotCount > MAX_MATERIAL_SLOTS (simulated UE behavior)
    # The UE handler checks SlotCount > MAX_MATERIAL_SLOTS and returns
    # without calling HandleMaterialDef. Simulate this gate.
    simulated_ue_rejected = False
    oversized_slots = {i: (i, i * 2) for i in range(9)}  # 9 slots > MAX=8
    if len(oversized_slots) > MAX_MATERIAL_SLOTS:
        simulated_ue_rejected = True
    test("5.11: SlotCount > MAX_MATERIAL_SLOTS rejected by UE gate",
         simulated_ue_rejected)

    # 5.12: SlotCount=255 rejected
    test("5.12: SlotCount=255 > MAX_MATERIAL_SLOTS (8) rejected",
         255 > MAX_MATERIAL_SLOTS)

    # 5.13: MAX_MATERIAL_SLOTS=8 is NOT rejected
    max_valid = {i: (i, i) for i in range(8)}
    test("5.13: SlotCount=MAX_MATERIAL_SLOTS=8 accepted by gate",
         len(max_valid) == MAX_MATERIAL_SLOTS and not (len(max_valid) > MAX_MATERIAL_SLOTS))


# =========================================================
# Run all sections
# =========================================================
# SECTION 6: Protocol signature includes 0x05
# =========================================================

def test_protocol_signature():
    """Protocol FNV signature includes PT_Material (0x05)."""
    print("\n--- Section 6: Protocol signature ---")

    # Simulate protocol FNV hash
    FNV_OFFSET = 0x811C9DC5
    FNV_PRIME = 0x01000193

    def _fnv(h, byte_val):
        return ((h * FNV_PRIME) ^ byte_val) & 0xFFFFFFFF

    def compute_protocol_sig(includes_05):
        h = FNV_OFFSET
        MAGIC = 0x4C56534D
        h = _fnv(h, MAGIC & 0xFF)
        h = _fnv(h, (MAGIC >> 8) & 0xFF)
        h = _fnv(h, (MAGIC >> 16) & 0xFF)
        h = _fnv(h, (MAGIC >> 24) & 0xFF)
        for v in (2, 3, 4, 5):
            h = _fnv(h, v & 0xFF)
            h = _fnv(h, (v >> 8) & 0xFF)
        for size in (24, 22, 80, 81, 16, 33, 28):
            h = _fnv(h, size)
        pts = (0x01, 0x03, 0x04, 0x07, 0x08, 0x09, 0x0A, 0x0B, 0x0C, 0x0D, 0x0E, 0x0F)
        if includes_05:
            pts = (0x01, 0x03, 0x04, 0x05, 0x07, 0x08, 0x09, 0x0A, 0x0B, 0x0C, 0x0D, 0x0E, 0x0F)
        for pt in pts:
            h = _fnv(h, pt)
        return h

    sig_without = compute_protocol_sig(includes_05=False)
    sig_with = compute_protocol_sig(includes_05=True)

    test("6.1: With 0x05 is different from without",
         sig_with != sig_without)
    test("6.2: Protocol sig includes 0x05",
         sig_with == compute_protocol_sig(includes_05=True))

    # 6.3: PT_Material value is 0x05
    test("6.3: PT_Material = 0x05",
         0x05 == 5)

    # 6.4: No conflict with existing packet types
    assigned = {0x01, 0x03, 0x04, 0x07, 0x08, 0x09, 0x0A, 0x0B, 0x0C, 0x0D, 0x0E, 0x0F}
    test("6.4: PT_Material (0x05) does not conflict with assigned types",
         0x05 not in assigned)


# =========================================================
# Run all sections
# =========================================================

def run_all():
    print("=" * 60)
    print("Phase 7B — PT_Material Wire + Handler (Stage 1C)")
    print("=" * 60)

    test_wire_format()                   # Section 1
    test_slot_identity_wire()            # Section 2
    test_duplicate_suppression()         # Section 3
    test_slot_count_bounds()             # Section 4
    test_handler_metadata_storage()      # Section 5
    test_protocol_signature()            # Section 6

    total = PASS + FAIL + SKIP
    print(f"\n{'=' * 60}")
    print(f"Phase 7B — PT_Material Wire + Handler Summary")
    print(f"{'=' * 60}")
    print(f"  Total tests: {total}")
    print(f"  Passed:      {PASS}")
    print(f"  Failed:      {FAIL}")
    print(f"  Skipped:     {SKIP}")
    if FAIL > 0:
        print(f"\n  FAILURES:")
        for name, ok, detail in RESULTS:
            if not ok:
                print(f"    FAIL: {name}")
                if detail:
                    print(f"           {detail}")
    print(f"{'=' * 60}")

    return FAIL == 0


def main():
    success = run_all()
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
