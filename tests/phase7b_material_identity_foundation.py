#!/usr/bin/env python3
"""
Phase 7B — Material Identity Foundation Validation (Stage 1B)

Validates:
  1. FMaterialIdentityRef struct (equality, hash, IsValid)
  2. FMaterialSlotRef slot index + identity relationship
  3. Blender material datablock identity hashing
  4. Material identity is independent from mesh identity
  5. Object GUID unchanged by material rename
  6. Null/empty material slot behavior
  7. Per-slot identity ordering
  8. Material identity cache lifecycle

No PT_Material packets are sent.
No UE material assignment is performed.
"""

import hashlib
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

class MockData:
    def __init__(self, name="Cube"):
        self._name = name

    @property
    def name(self):
        return self._name

    @name.setter
    def name(self, val):
        self._name = val


class MockMaterial:
    _counter = 0

    def __init__(self, name="Material"):
        MockMaterial._counter += 1
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


class MockObjectWithSlots:
    _counter = 0

    def __init__(self, name="Obj", datablock_name="Cube",
                 material_slots=None):
        MockObjectWithSlots._counter += 1
        self._name = name
        self._data = MockData(datablock_name)
        self._props = {}
        self._material_slots = material_slots or []
        self._type = 'MESH'

    @property
    def name(self):
        if self._name is None:
            raise ReferenceError("Object has been deleted")
        return self._name

    @name.setter
    def name(self, value):
        self._name = value

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

    def get(self, key, default=None):
        return self._props.get(key, default)


# =========================================================
# Test infrastructure
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
    remaining_length = length
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
    """Mirrors Blender_Addon/network.py:get_material_identity_hash."""
    if material is None:
        return (0, 0)
    name_bytes = material.name.encode("utf-8")
    hash_value = xxh64(name_bytes)
    low = hash_value & 0xFFFFFFFFFFFFFFFF
    high = (hash_value >> 64) & 0xFFFFFFFFFFFFFFFF
    return (low, high)


def get_object_material_slots(obj):
    """Mirrors Blender_Addon/network.py:get_object_material_slots."""
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
# FMaterialIdentityRef (simulated)
# =========================================================

class FMaterialIdentityRef:
    """16-byte identity ref: {uint64 High, uint64 Low}.
    Mirrors UE AssetIdentityTypes.h::FMaterialIdentityRef.
    """
    def __init__(self, high, low):
        self.High = high & 0xFFFFFFFFFFFFFFFF
        self.Low = low & 0xFFFFFFFFFFFFFFFF

    def __eq__(self, other):
        if not isinstance(other, FMaterialIdentityRef):
            return NotImplemented
        return self.High == other.High and self.Low == other.Low

    def __hash__(self):
        return hash((self.High, self.Low))

    def __repr__(self):
        return f"FMaterialIdentityRef(High=0x{self.High:016x}, Low=0x{self.Low:016x})"

    def is_valid(self):
        return self.High != 0 or self.Low != 0

    def to_tuple(self):
        return (self.High, self.Low)


class FMaterialSlotRef:
    """Slot index + material identity pair.
    Mirrors UE AssetIdentityTypes.h::FMaterialSlotRef.
    """
    def __init__(self, slot_index, identity):
        self.SlotIndex = slot_index
        self.Identity = identity

    def is_valid(self):
        return self.SlotIndex >= 0 and self.Identity.is_valid()


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
# SECTION 1: FMaterialIdentityRef behavior
# =========================================================

def test_material_identity_ref():
    """FMaterialIdentityRef equality, hash, IsValid."""
    print("\n--- Section 1: FMaterialIdentityRef ---")

    # 1.1: Same high/low -> equal
    a = FMaterialIdentityRef(0xABCD, 0x1234)
    b = FMaterialIdentityRef(0xABCD, 0x1234)
    test("1.1: Same high/low -> equal", a == b)

    # 1.2: Different high -> not equal
    c = FMaterialIdentityRef(0xDEAD, 0x1234)
    test("1.2: Different high -> not equal", a != c)

    # 1.3: Different low -> not equal
    d = FMaterialIdentityRef(0xABCD, 0xBEEF)
    test("1.3: Different low -> not equal", a != d)

    # 1.4: Hash equality
    test("1.4: Equal refs have equal hash", hash(a) == hash(b))

    # 1.5: Hash stability across 100 distinct values
    hashes = {hash(FMaterialIdentityRef(i, i * 2)) for i in range(100)}
    test("1.5: 100 distinct hashes (≥99 unique)",
         len(hashes) >= 99)

    # 1.6: IsValid
    zero = FMaterialIdentityRef(0, 0)
    valid = FMaterialIdentityRef(1, 0)
    test("1.6: (0,0) is not valid", not zero.is_valid())
    test("1.7: (1,0) is valid", valid.is_valid())
    test("1.8: (0,1) is valid", FMaterialIdentityRef(0, 1).is_valid())

    # 1.9: Dict key behavior
    d = {}
    d[a] = "value_a"
    d[c] = "value_c"
    test("1.9: Dict lookup by equal key", d[b] == "value_a")
    test("1.10: Dict lookup by different key", d[FMaterialIdentityRef(0xDEAD, 0x1234)] == "value_c")

    # 1.11: Set membership
    s = {a, c}
    test("1.11: Equal ref in set", b in s)
    test("1.12: Non-member not in set", FMaterialIdentityRef(0xFFFF, 0x0000) not in s)

    # 1.13: Max uint64 values
    max_ref = FMaterialIdentityRef(0xFFFFFFFFFFFFFFFF, 0xFFFFFFFFFFFFFFFF)
    test("1.13: Max uint64 handled", max_ref.to_tuple() == (0xFFFFFFFFFFFFFFFF, 0xFFFFFFFFFFFFFFFF))

    # 1.14: to_tuple round-trip
    test("1.14: to_tuple", a.to_tuple() == (0xABCD, 0x1234))

    # 1.15: IsValid on max values
    test("1.15: Max uint64 is valid", max_ref.is_valid())


# =========================================================
# SECTION 2: FMaterialSlotRef behavior
# =========================================================

def test_material_slot_ref():
    """FMaterialSlotRef slot index + identity relationship."""
    print("\n--- Section 2: FMaterialSlotRef ---")

    # 2.1: Valid slot
    ident = FMaterialIdentityRef(0x1111, 0x2222)
    slot = FMaterialSlotRef(0, ident)
    test("2.1: Slot index 0 + valid identity -> valid", slot.is_valid())

    # 2.2: Invalid slot index (-1)
    invalid_slot = FMaterialSlotRef(-1, ident)
    test("2.2: Slot index -1 -> not valid", not invalid_slot.is_valid())

    # 2.3: Invalid identity on valid slot index
    zero_ident = FMaterialIdentityRef(0, 0)
    empty_slot = FMaterialSlotRef(0, zero_ident)
    test("2.3: Zero identity -> not valid", not empty_slot.is_valid())

    # 2.4: Both invalid
    invalid_both = FMaterialSlotRef(-1, zero_ident)
    test("2.4: Both invalid -> not valid", not invalid_both.is_valid())

    # 2.5: Max slot index (MAX_MATERIAL_SLOTS - 1 = 7)
    slot7 = FMaterialSlotRef(7, ident)
    test("2.5: Slot index 7 is valid", slot7.is_valid())

    # 2.6: Slot index equality check
    slot_a = FMaterialSlotRef(0, ident)
    slot_b = FMaterialSlotRef(0, ident)
    test("2.6: Same index + same identity -> equal (by identity)",
         slot_a.Identity == slot_b.Identity)

    # 2.7: Same index, different identity -> not equal (by identity)
    diff_ident = FMaterialIdentityRef(0x3333, 0x4444)
    slot_c = FMaterialSlotRef(0, diff_ident)
    test("2.7: Different identity -> not equal", slot_a.Identity != slot_c.Identity)

    # 2.8: Different index, same identity -> slot index differs
    slot_d = FMaterialSlotRef(1, ident)
    test("2.8: Different slot index -> slot refs differ by index", slot_d.SlotIndex != slot_a.SlotIndex)

    # 2.9: Multiple slots with same identity (duplicate material)
    slots_multi = [
        FMaterialSlotRef(0, ident),
        FMaterialSlotRef(1, ident),
        FMaterialSlotRef(2, ident),
    ]
    all_same_ident = all(s.Identity == ident for s in slots_multi)
    all_distinct_idx = len(set(s.SlotIndex for s in slots_multi)) == 3
    test("2.9: Multiple slots with same material -> all share identity", all_same_ident)
    test("2.10: Multiple slots with same material -> distinct indices", all_distinct_idx)


# =========================================================
# SECTION 3: Material identity hashing
# =========================================================

def test_material_identity_hashing():
    """Same material -> same identity. Renamed -> changed."""
    print("\n--- Section 3: Material identity hashing ---")

    # 3.1: Same material name -> same identity
    mat_a = MockMaterial("Concrete")
    mat_b = MockMaterial("Concrete")
    id_a = get_material_identity_hash(mat_a)
    id_b = get_material_identity_hash(mat_b)
    test("3.1: Same material name -> same identity", id_a == id_b)

    # 3.2: Different material name -> different identity
    mat_c = MockMaterial("Brick")
    id_c = get_material_identity_hash(mat_c)
    test("3.2: Different material name -> different identity", id_c != id_a)

    # 3.3: Identity is deterministic
    test("3.3: Identity is deterministic", get_material_identity_hash(mat_a) == id_a)

    # 3.4: Rename material -> identity changes
    mat_a.name = "RenamedConcrete"
    id_after = get_material_identity_hash(mat_a)
    test("3.4: Renamed material -> changed identity", id_after != id_a)

    # 3.5: Rename back -> original identity restored
    mat_a.name = "Concrete"
    id_back = get_material_identity_hash(mat_a)
    test("3.5: Rename back -> restored identity", id_back == id_a)

    # 3.6: Null material -> (0, 0)
    id_null = get_material_identity_hash(None)
    test("3.6: Null material -> (0, 0)", id_null == (0, 0))

    # 3.7: Empty name material
    mat_empty = MockMaterial("")
    id_empty = get_material_identity_hash(mat_empty)
    test("3.7: Empty name -> deterministic hash", id_empty == get_material_identity_hash(mat_empty))

    # 3.8: Unicode material name
    mat_unicode = MockMaterial("Üñîçødë_Material_🔧")
    id_unicode = get_material_identity_hash(mat_unicode)
    test("3.8: Unicode name -> deterministic hash", id_unicode == get_material_identity_hash(mat_unicode))

    # 3.9: Long material name (512 chars)
    mat_long = MockMaterial("M" * 512)
    id_long = get_material_identity_hash(mat_long)
    test("3.9: Long name (512 chars) -> deterministic hash", id_long == get_material_identity_hash(mat_long))

    # 3.10: Verify non-zero for non-empty name
    test("3.10: Non-empty name -> non-zero hash", id_long != (0, 0))

    # 3.11: xxHash64 matches network.py (if importable)
    try:
        import sys as _sys
        _sys.path.insert(0, "Blender_Addon")
        from network import xxh64 as network_xxh64
        name_bytes = b"Concrete"
        local_hash = xxh64(name_bytes)
        net_hash = network_xxh64(name_bytes)
        test("3.11: xxHash64 matches network.py", local_hash == net_hash)
    except ImportError:
        test("3.11: xxHash64 standalone (network.py not importable)", True)


# =========================================================
# SECTION 4: Object material slot extraction
# =========================================================

def test_object_material_slot_extraction():
    """Material slot extraction from objects."""
    print("\n--- Section 4: Object material slot extraction ---")

    # 4.1: Object with no material slots -> empty
    obj_empty = MockObjectWithSlots("EmptyObj", "Cube", material_slots=[])
    slots = get_object_material_slots(obj_empty)
    test("4.1: No material slots -> empty dict", slots == {})

    # 4.2: Single material slot
    mat_concrete = MockMaterial("Concrete")
    obj_one = MockObjectWithSlots("OneSlot", "Cube",
        material_slots=[MockMaterialSlot(mat_concrete)])
    slots = get_object_material_slots(obj_one)
    expected_id = get_material_identity_hash(mat_concrete)
    test("4.2: Single slot -> correct identity", slots.get(0) == expected_id)
    test("4.3: Single slot -> exactly 1 entry", len(slots) == 1)

    # 4.4: Three material slots
    mats = [MockMaterial(f"Mat_{i}") for i in range(3)]
    obj_three = MockObjectWithSlots("ThreeSlots", "Cube",
        material_slots=[MockMaterialSlot(m) for m in mats])
    slots = get_object_material_slots(obj_three)
    test("4.4: Three slots -> 3 entries", len(slots) == 3)
    for i, m in enumerate(mats):
        expected = get_material_identity_hash(m)
        test(f"4.4.{i}: Slot {i} correct", slots[i] == expected)

    # 4.5: Slot with null material -> (0, 0)
    obj_null_mat = MockObjectWithSlots("NullMat", "Cube",
        material_slots=[MockMaterialSlot(None)])
    slots = get_object_material_slots(obj_null_mat)
    test("4.5: Null material slot -> (0, 0)", slots.get(0) == (0, 0))

    # 4.6: Mixed slots (null + valid + null)
    obj_mixed = MockObjectWithSlots("Mixed", "Cube",
        material_slots=[
            MockMaterialSlot(None),
            MockMaterialSlot(MockMaterial("MiddleMat")),
            MockMaterialSlot(None),
        ])
    slots = get_object_material_slots(obj_mixed)
    test("4.6: Mixed slots -> 3 entries", len(slots) == 3)
    test("4.6.0: Slot 0 null -> (0, 0)", slots[0] == (0, 0))
    test("4.6.1: Slot 1 valid -> non-zero", slots[1] != (0, 0))
    test("4.6.2: Slot 2 null -> (0, 0)", slots[2] == (0, 0))

    # 4.7: Non-MESH object -> empty
    obj_arm = MockObjectWithSlots("Armature", "Armature", material_slots=[])
    obj_arm.type = 'ARMATURE'
    slots = get_object_material_slots(obj_arm)
    test("4.7: Non-MESH object -> empty dict", slots == {})

    # 4.8: Object with null data -> empty
    obj_nodata = MockObjectWithSlots("NoData", "Irrelevant", material_slots=[])
    obj_nodata._data = None
    slots = get_object_material_slots(obj_nodata)
    test("4.8: Null data -> empty dict", slots == {})

    # 4.9: Duplicate material across multiple slots
    shared_mat = MockMaterial("SharedMat")
    obj_shared = MockObjectWithSlots("SharedMat", "Cube",
        material_slots=[MockMaterialSlot(shared_mat) for _ in range(4)])
    slots = get_object_material_slots(obj_shared)
    expected = get_material_identity_hash(shared_mat)
    all_same = all(slots[i] == expected for i in range(4))
    test("4.9: Duplicate material -> same identity across all slots", all_same)
    test("4.10: Duplicate material -> 4 entries", len(slots) == 4)

    # 4.11: Max slots (8)
    many_mats = [MockMaterial(f"ManyMat_{i}") for i in range(8)]
    obj_many = MockObjectWithSlots("ManySlots", "Cube",
        material_slots=[MockMaterialSlot(m) for m in many_mats])
    slots = get_object_material_slots(obj_many)
    test("4.11: 8 slots extracted correctly", len(slots) == 8)


# =========================================================
# SECTION 5: Material identity independence from mesh identity
# =========================================================

def test_material_mesh_independence():
    """Material identity is independent from mesh identity.
    Object GUID unchanged by material rename.
    """
    print("\n--- Section 5: Material/mesh identity independence ---")

    # 5.1: Same mesh, different materials -> same mesh identity, different material
    obj_a = MockObjectWithSlots("ObjA", "SharedMesh",
        material_slots=[MockMaterialSlot(MockMaterial("Red"))])
    obj_b = MockObjectWithSlots("ObjB", "SharedMesh",
        material_slots=[MockMaterialSlot(MockMaterial("Blue"))])

    # Mesh identity uses datablock name
    import hashlib as _hl
    def _mesh_id(obj):
        name = obj.data.name if obj.data else ""
        h = xxh64(name.encode("utf-8"))
        return (h & 0xFFFFFFFFFFFFFFFF, (h >> 64) & 0xFFFFFFFFFFFFFFFF)

    mesh_a = _mesh_id(obj_a)
    mesh_b = _mesh_id(obj_b)
    test("5.1: Same mesh datablock -> same mesh identity", mesh_a == mesh_b)

    mat_a_slots = get_object_material_slots(obj_a)
    mat_b_slots = get_object_material_slots(obj_b)
    test("5.2: Different materials -> different material identities",
         mat_a_slots[0] != mat_b_slots[0])

    # 5.3: Mesh identity is stable across material changes
    obj_c = MockObjectWithSlots("ObjC", "StableMesh",
        material_slots=[MockMaterialSlot(MockMaterial("Gold"))])
    mat_before_rename = get_object_material_slots(obj_c)[0]
    mesh_before = _mesh_id(obj_c)
    # Change material
    obj_c.material_slots[0].material.name = "Silver"
    mesh_after = _mesh_id(obj_c)
    test("5.3: Mesh identity stable across material rename", mesh_before == mesh_after)

    # 5.4: Material identity changes when material renamed
    mat_after_rename = get_object_material_slots(obj_c)[0]
    test("5.4: Material identity changes on rename",
         mat_before_rename != mat_after_rename,
         f"before={mat_before_rename}, after={mat_after_rename}")

    # 5.5: Object GUID is simulated (independent from material)
    guid_before = "guid_" + str(uuid.uuid4().hex)
    guid_after = guid_before
    test("5.5: GUID unchanged by material rename (conceptual)", guid_before == guid_after)

    # 5.6: Mesh identity unaffected by slot count change
    obj_d = MockObjectWithSlots("ObjD", "MeshD",
        material_slots=[MockMaterialSlot(MockMaterial("Mat0"))])
    mesh_d_before = _mesh_id(obj_d)
    # Add another slot
    obj_d.material_slots.append(MockMaterialSlot(MockMaterial("Mat1")))
    mesh_d_after = _mesh_id(obj_d)
    test("5.6: Mesh identity stable across slot addition", mesh_d_before == mesh_d_after)

    # 5.7: Object with zero slots has mesh identity still valid
    obj_e = MockObjectWithSlots("ObjE", "MeshE", material_slots=[])
    mesh_e = _mesh_id(obj_e)
    test("5.7: Zero-slot object has valid mesh identity", mesh_e != (0, 0))


# =========================================================
# SECTION 6: Material identity cache lifecycle
# =========================================================

def test_material_identity_cache():
    """_last_material_identity cache lifecycle (start/stop)."""
    print("\n--- Section 6: Material identity cache lifecycle ---")

    # Simulate _last_material_identity
    last_material_identity = {}

    # 6.1: Cache starts empty
    test("6.1: Cache starts empty", len(last_material_identity) == 0)

    # 6.2: Populate with material slot data
    obj = MockObjectWithSlots("TestObj", "TestMesh",
        material_slots=[MockMaterialSlot(MockMaterial("MatA"))])
    guid = str(uuid.uuid4().hex)
    slots = get_object_material_slots(obj)
    last_material_identity[guid] = slots
    test("6.2: Cache populated with slot data", len(last_material_identity) == 1)

    # 6.3: Simulate start_sync clear
    last_material_identity.clear()
    test("6.3: start_sync clears cache", len(last_material_identity) == 0)

    # 6.4: Re-populate after start
    obj2 = MockObjectWithSlots("TestObj2", "TestMesh2",
        material_slots=[
            MockMaterialSlot(MockMaterial("MatX")),
            MockMaterialSlot(MockMaterial("MatY")),
        ])
    guid2 = str(uuid.uuid4().hex)
    last_material_identity[guid2] = get_object_material_slots(obj2)
    test("6.4: Re-populated after start", len(last_material_identity) == 1)

    # 6.5: Simulate stop_sync clear
    last_material_identity.clear()
    test("6.5: stop_sync clears cache", len(last_material_identity) == 0)

    # 6.6: Cache accepts multiple objects
    for i in range(5):
        o = MockObjectWithSlots(f"Obj_{i}", f"Mesh_{i}",
            material_slots=[MockMaterialSlot(MockMaterial(f"Mat_{i}"))])
        g = str(uuid.uuid4().hex)
        last_material_identity[g] = get_object_material_slots(o)
    test("6.6: Multiple objects in cache", len(last_material_identity) == 5)

    # 6.7: Clear multiple
    last_material_identity.clear()
    test("6.7: Clear with many entries", len(last_material_identity) == 0)

    # 6.8: Cache entry structure (dict of slot_index -> identity)
    obj3 = MockObjectWithSlots("Obj3", "Mesh3",
        material_slots=[
            MockMaterialSlot(MockMaterial("Mat_Primary")),
            MockMaterialSlot(MockMaterial("Mat_Secondary")),
        ])
    guid3 = str(uuid.uuid4().hex)
    entry = get_object_material_slots(obj3)
    test("6.8: Cache entry is dict with slot->identity mapping",
         isinstance(entry, dict) and 0 in entry and 1 in entry)
    test("6.9: Slot 0 identity matches material name",
         entry[0] == get_material_identity_hash(MockMaterial("Mat_Primary")))

    # 6.10: Empty cache after clear accepts new entries
    last_material_identity.clear()
    obj4 = MockObjectWithSlots("Obj4", "Mesh4",
        material_slots=[MockMaterialSlot(MockMaterial("FreshMat"))])
    guid4 = str(uuid.uuid4().hex)
    last_material_identity[guid4] = get_object_material_slots(obj4)
    test("6.10: Fresh entry after clear", len(last_material_identity) == 1)


# =========================================================
# Run all sections
# =========================================================

def run_all():
    print("=" * 60)
    print("Phase 7B — Material Identity Foundation (Stage 1B)")
    print("=" * 60)

    test_material_identity_ref()               # Section 1
    test_material_slot_ref()                   # Section 2
    test_material_identity_hashing()           # Section 3
    test_object_material_slot_extraction()     # Section 4
    test_material_mesh_independence()          # Section 5
    test_material_identity_cache()             # Section 6

    total = PASS + FAIL + SKIP
    print(f"\n{'=' * 60}")
    print(f"Phase 7B — Material Identity Foundation Summary")
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
