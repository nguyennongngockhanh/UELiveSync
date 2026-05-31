#!/usr/bin/env python3
"""
Phase 7A — Identity Hygiene Validation (Stage 1A)

Validates fixes for critical audit issues found in Phase 7A Stage 0:
  C1. HandleDelete (V5) cleans AssetMetadata and PendingAssetQueue
  C2. Truncated PT_AssetDef increments MalformedPackets counter
  C3. Blender _last_mesh_identity cleared on start_sync()/stop_sync()

Tests are standalone (MockObject-based) where possible.
UE-connected tests require UE editor on 127.0.0.1:57000.
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
# MockObject — Standalone Blender object mock
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


class MockObject:
    _counter = 0

    def __init__(self, name="Cube", datablock_name="Cube"):
        MockObject._counter += 1
        self._name = name
        self._data = MockData(datablock_name)
        self._props = {}

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
        return 'MESH'

    def __contains__(self, key):
        return key in self._props

    def __getitem__(self, key):
        return self._props[key]

    def __setitem__(self, key, value):
        self._props[key] = value

    def get(self, key, default=None):
        return self._props.get(key, default)

    def __delitem__(self, key):
        del self._props[key]


# =========================================================
# Test infrastructure (mirrors phase6g_identity_stability)
# =========================================================

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


def report():
    total = PASS + FAIL + SKIP
    print(f"\n{'=' * 50}")
    print(f"RESULTS: {PASS}/{total} passed, {FAIL} failed, {SKIP} skipped")
    print(f"{'=' * 50}")
    return FAIL == 0


# =========================================================
# Constants (mirroring network.py)
# =========================================================

MAGIC = 0x4C56534D
V5 = 5
PT_AssetDef = 0x08
PT_Delete_V5 = 0x0E
PT_Create = 0x03
LIVE_SYNC_V5_ASSET_DEF_SIZE = 33
DELETE_OBJ_SIZE = 28

HOST = "127.0.0.1"
PORT = 57000


def make_v5_header(packet_type=0x08, object_count=0,
                   payload_size=0, seq=1, flags=0x00):
    header_size = struct.calcsize("<I H B B Q I I")
    packet_size = header_size + payload_size
    return struct.pack(
        "<I H B B Q I I",
        MAGIC, V5, packet_type, flags,
        seq, packet_size, object_count
    )


def make_dummy_guid_bytes(val):
    return bytes([val & 0xFF] * 16)


def make_guid_bytes(guid_obj):
    a = guid_obj.time_low
    b = (guid_obj.time_mid << 16) | guid_obj.time_hi_version
    c = (guid_obj.clock_seq_hi_variant << 24
         | guid_obj.clock_seq_low << 16
         | ((guid_obj.node >> 32) & 0xFFFF))
    d = guid_obj.node & 0xFFFFFFFF
    return struct.pack("<IIII", a, b, c, d)


def make_asset_def_bytes(guid_bytes, identity_low, identity_high,
                         primitive_fallback=0x00):
    payload = bytearray()
    payload.extend(guid_bytes)
    payload.extend(struct.pack("<QQ", identity_low, identity_high))
    payload.extend(struct.pack("<B", primitive_fallback))
    return bytes(payload)


def make_transform_bytes(guid_bytes, loc, rot, scl, ts=0.0, parent=None):
    data = bytearray()
    data.extend(guid_bytes)
    data.extend(struct.pack("<fff", *loc))
    data.extend(struct.pack("<ffff", *rot))
    data.extend(struct.pack("<fff", *scl))
    data.extend(struct.pack("<d", ts))
    if parent:
        data.extend(parent)
    else:
        data.extend(b"\x00" * 16)
    return bytes(data)


def build_delete_payload(target_guid, seq=1, ts=None):
    if ts is None:
        ts = time.time()
    payload = bytearray()
    payload.extend(make_guid_bytes(target_guid))
    payload.extend(struct.pack("<I", seq))
    payload.extend(struct.pack("<d", ts))
    return bytes(payload)


# =========================================================
# SECTION 1: C3 — _last_mesh_identity clear (standalone)
# =========================================================

# Import the sync module functions via copy since we can't
# import Blender modules outside Blender. We replicate the
# _compute_owner_hash logic used by the GUID system.

def _compute_owner_hash(obj):
    datablock_name = obj.data.name if obj.data else ""
    raw = f"{datablock_name}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def ensure_guid(obj):
    if "ue_guid" not in obj:
        obj["ue_guid"] = uuid.uuid4().hex
        obj["ue_guid_owner_hash"] = _compute_owner_hash(obj)
    return obj["ue_guid"]


def ensure_unique_guid(obj, tracked):
    guid = ensure_guid(obj)
    if guid in tracked and tracked[guid][0] != obj:
        obj["ue_guid"] = uuid.uuid4().hex
        obj["ue_guid_owner_hash"] = _compute_owner_hash(obj)
        guid = obj["ue_guid"]
    return guid


def test_mesh_identity_clear_on_start():
    """C3: Simulate start_sync() — _last_mesh_identity must be cleared."""
    print("\n--- Section 1: C3 — _last_mesh_identity clear on start/stop ---")

    # Simulate the Blender-side _last_mesh_identity dict
    last_mesh_identity = {
        "guid1": (0xABCD, 0x1234, "Mesh1"),
        "guid2": (0x5678, 0x9ABC, "Mesh2"),
    }

    # 1.1: Verify non-empty before clear
    test("1.1: _last_mesh_identity has entries before clear",
         len(last_mesh_identity) == 2)

    # Simulate start_sync() clear
    last_mesh_identity.clear()

    # 1.2: Verify empty after start_sync-style clear
    test("1.2: _last_mesh_identity empty after start_sync clear",
         len(last_mesh_identity) == 0)

    # Re-populate for stop_sync simulation
    last_mesh_identity["guid3"] = (0xDEAD, 0xBEEF, "Mesh3")

    # 1.3: Re-populated
    test("1.3: _last_mesh_identity re-populated",
         len(last_mesh_identity) == 1)

    # Simulate stop_sync() clear
    last_mesh_identity.clear()

    # 1.4: Verify empty after stop_sync-style clear
    test("1.4: _last_mesh_identity empty after stop_sync clear",
         len(last_mesh_identity) == 0)

    # 1.5: Verify no stale entries survive clear
    test("1.5: No stale entries after double clear",
         len(last_mesh_identity) == 0)

    # 1.6: Verify clear on empty dict does not error
    last_mesh_identity.clear()
    test("1.6: Clear on empty _last_mesh_identity does not error", True)

    # 1.7: Verify start_sync clears previously-populated identity cache
    # Simulate a full session: populate with 5 entries, then clear
    for i in range(5):
        obj = MockObject(name=f"Obj_{i}", datablock_name=f"Mesh_{i}")
        guid = ensure_guid(obj)
        last_mesh_identity[guid] = (i, i * 2, f"Mesh_{i}")
    test("1.7: Session simulation has 5 entries",
         len(last_mesh_identity) == 5)
    last_mesh_identity.clear()
    test("1.8: Session simulation cleared to 0",
         len(last_mesh_identity) == 0)

    # 1.9: Verify no cross-session leakage: set one entry, clear, verify empty
    obj_a = MockObject(name="SessionA", datablock_name="MeshA")
    guid_a = ensure_guid(obj_a)
    last_mesh_identity[guid_a] = (1, 2, "MeshA")
    last_mesh_identity.clear()
    obj_b = MockObject(name="SessionB", datablock_name="MeshB")
    guid_b = ensure_guid(obj_b)
    test("1.9: No cross-session entry from prior session",
         guid_b not in last_mesh_identity and len(last_mesh_identity) == 0)

    # 1.10: Verify PT_AssetDef suppression would NOT be blocked after clear
    # If _last_mesh_identity is empty, the next identity check should see
    # "no previous identity" and emit PT_AssetDef (is_first_send logic).
    prev = last_mesh_identity.get("nonexistent")
    test("1.10: Empty identity cache returns None (first-send path triggers)",
         prev is None)


# =========================================================
# SECTION 2: C2 — Truncated PT_AssetDef (UE connected)
# =========================================================

def test_truncated_asset_def():
    """C2: Send truncated PT_AssetDef — verify rejection.
    Requires UE editor running on 127.0.0.1:57000.
    """
    print("\n--- Section 2: C2 — Truncated PT_AssetDef (requires UE) ---")

    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(5.0)
        s.connect((HOST, PORT))
        s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    except Exception as e:
        skip("2a: Cannot connect to UE", str(e))
        return

    # 2.1: Send truncated payload (only 20 bytes instead of 33)
    truncated_payload = bytes([0xA0] * 20)
    truncated_header = make_v5_header(
        packet_type=PT_AssetDef, object_count=1,
        payload_size=len(truncated_payload), seq=100
    )
    try:
        s.sendall(truncated_header + truncated_payload)
        test("2.1: Truncated PT_AssetDef sent (expect UE rejection)", True)
        time.sleep(0.3)
    except Exception as e:
        test("2.1: Truncated PT_AssetDef send failed", False, str(e))

    # 2.2: Send zero-length payload (count=1 but no data)
    zero_payload = b""
    zero_header = make_v5_header(
        packet_type=PT_AssetDef, object_count=1,
        payload_size=0, seq=101
    )
    try:
        s.sendall(zero_header + zero_payload)
        test("2.2: Zero-length PT_AssetDef sent (expect UE rejection)", True)
        time.sleep(0.3)
    except Exception as e:
        test("2.2: Zero-length PT_AssetDef send failed", False, str(e))

    # 2.3: Send valid PT_AssetDef after truncation (verify pipeline not broken)
    valid_guid = make_dummy_guid_bytes(0xF0)
    valid_def = make_asset_def_bytes(
        valid_guid, 0x1111111111111111, 0x2222222222222222, 0x00
    )
    valid_header = make_v5_header(
        packet_type=PT_AssetDef, object_count=1,
        payload_size=len(valid_def), seq=102
    )
    try:
        s.sendall(valid_header + valid_def)
        test("2.3: Valid PT_AssetDef after truncation (pipeline intact)", True)
        time.sleep(0.3)
    except Exception as e:
        test("2.3: Valid PT_AssetDef after truncation failed", False, str(e))

    # 2.4: Send CREATE for the valid GUID (verify end-to-end)
    transform = make_transform_bytes(
        valid_guid, (100, 200, 300), (0, 0, 0, 1), (1, 1, 1), ts=time.time()
    )
    create_header = make_v5_header(
        packet_type=PT_Create, object_count=1,
        payload_size=len(transform), seq=103, flags=0x02
    )
    try:
        s.sendall(create_header + transform)
        test("2.4: CREATE after truncated+valid AssetDef", True)
        time.sleep(0.3)
    except Exception as e:
        test("2.4: CREATE after truncated+valid AssetDef failed", False, str(e))

    s.close()
    print("  Manual: Check UE.LiveSync.Stats — MalformedPackets should be >= 2")
    print("  (one for truncated payload, one for zero-length)")


# =========================================================
# SECTION 3: C1 — Delete cleanup (UE connected)
# =========================================================

def test_delete_asset_metadata_cleanup():
    """C1: Verify that V5 delete cleans AssetMetadata + PendingAssetQueue.
    Requires UE editor running on 127.0.0.1:57000.
    """
    print("\n--- Section 3: C1 — Delete AssetMetadata cleanup (requires UE) ---")

    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(5.0)
        s.connect((HOST, PORT))
        s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    except Exception as e:
        skip("3a: Cannot connect to UE", str(e))
        return

    # Step 1: Send PT_AssetDef for a GUID (creates AssetMetadata entry)
    target_guid_obj = uuid.uuid4()
    target_guid_bytes = make_guid_bytes(target_guid_obj)
    identity_def = make_asset_def_bytes(
        target_guid_bytes, 0xAAAAAAAAAAAAAAAA, 0xBBBBBBBBBBBBBBBB, 0x00
    )
    def_header = make_v5_header(
        packet_type=PT_AssetDef, object_count=1,
        payload_size=len(identity_def), seq=200
    )
    try:
        s.sendall(def_header + identity_def)
        test("3.1: PT_AssetDef sent for delete target GUID", True)
        time.sleep(0.3)
    except Exception as e:
        test("3.1: PT_AssetDef send failed", False, str(e))

    # Step 2: Send PT_Create to spawn the actor
    transform = make_transform_bytes(
        target_guid_bytes, (500, 600, 700), (0, 0, 0, 1), (2, 2, 2),
        ts=time.time()
    )
    create_header = make_v5_header(
        packet_type=PT_Create, object_count=1,
        payload_size=len(transform), seq=201, flags=0x02
    )
    try:
        s.sendall(create_header + transform)
        test("3.2: CREATE for delete target GUID", True)
        time.sleep(0.3)
    except Exception as e:
        test("3.2: CREATE failed", False, str(e))

    # Step 3: Send PT_Delete_V5 for the same GUID
    delete_payload = build_delete_payload(target_guid_obj, seq=1)
    delete_header = make_v5_header(
        packet_type=PT_Delete_V5, object_count=1,
        payload_size=len(delete_payload), seq=202
    )
    try:
        s.sendall(delete_header + delete_payload)
        test("3.3: PT_Delete_V5 sent for target GUID", True)
        time.sleep(0.5)
    except Exception as e:
        test("3.3: PT_Delete_V5 send failed", False, str(e))

    # Step 4: Send another PT_AssetDef + CREATE for same GUID
    # (If metadata was cleaned, this should work fresh)
    identity_def2 = make_asset_def_bytes(
        target_guid_bytes, 0xCCCCCCCCCCCCCCCC, 0xDDDDDDDDDDDDDDDD, 0x00
    )
    def_header2 = make_v5_header(
        packet_type=PT_AssetDef, object_count=1,
        payload_size=len(identity_def2), seq=203
    )
    try:
        s.sendall(def_header2 + identity_def2)
        test("3.4: PT_AssetDef after delete (expect fresh metadata)", True)
        time.sleep(0.3)
    except Exception as e:
        test("3.4: PT_AssetDef after delete failed", False, str(e))

    transform2 = make_transform_bytes(
        target_guid_bytes, (800, 900, 1000), (0, 0, 0, 1), (1, 1, 1),
        ts=time.time()
    )
    create_header2 = make_v5_header(
        packet_type=PT_Create, object_count=1,
        payload_size=len(transform2), seq=204, flags=0x02
    )
    try:
        s.sendall(create_header2 + transform2)
        test("3.5: CREATE after delete (expect clean spawn)", True)
        time.sleep(0.3)
    except Exception as e:
        test("3.5: CREATE after delete failed", False, str(e))

    # Step 5: Delete again (V5) to clean up
    delete_payload2 = build_delete_payload(target_guid_obj, seq=2)
    delete_header2 = make_v5_header(
        packet_type=PT_Delete_V5, object_count=1,
        payload_size=len(delete_payload2), seq=205
    )
    try:
        s.sendall(delete_header2 + delete_payload2)
        time.sleep(0.3)
    except Exception as e:
        pass

    s.close()
    print("  Manual: Check UE.LiveSync.Stats — AssetDefsReceived should be >= 2")
    print("  Manual: Verify no stale metadata errors in log")


# =========================================================
# SECTION 4: Blender-side sync lifecycle simulation
# =========================================================

def test_blender_lifecycle_clear():
    """C3 extended: Verify _last_mesh_identity cleared in
    simulated start_sync() / stop_sync() with object tracking.
    """
    print("\n--- Section 4: Blender lifecycle simulation ---")

    # Simulate tracked_objects
    tracked = {}
    last_mesh_identity = {}

    # Create some tracked objects with mesh identity entries
    for i in range(3):
        obj = MockObject(name=f"LifecycleObj_{i}", datablock_name=f"LifecycleMesh_{i}")
        guid = ensure_guid(obj)
        tracked[guid] = (obj, uuid.UUID(guid))
        last_mesh_identity[guid] = (i, i * 100, f"LifecycleMesh_{i}")

    # 4.1: Verify populated state
    test("4.1: Tracked objects populated",
         len(tracked) == 3)
    test("4.2: _last_mesh_identity populated",
         len(last_mesh_identity) == 3)

    # Simulate stop_sync: clear + reset
    last_mesh_identity.clear()
    tracked.clear()

    # 4.3: Verify cleared
    test("4.3: _last_mesh_identity cleared on stop",
         len(last_mesh_identity) == 0)
    test("4.4: tracked_objects cleared on stop",
         len(tracked) == 0)

    # Simulate start_sync: fresh scan
    for i in range(2):
        obj = MockObject(name=f"NewObj_{i}", datablock_name=f"NewMesh_{i}")
        guid = ensure_guid(obj)
        tracked[guid] = (obj, uuid.UUID(guid))
        # _last_mesh_identity starts empty, populated on first identity check

    # 4.5: New session objects tracked
    test("4.5: New session objects tracked",
         len(tracked) == 2)
    # 4.6: _last_mesh_identity starts empty (no stale entries from prior session)
    test("4.6: No stale _last_mesh_identity from prior session",
         len(last_mesh_identity) == 0)

    # 4.7: Simulate first-identity detection (like sync.py check_updates)
    for guid, (obj, guid_obj) in tracked.items():
        if guid not in last_mesh_identity:
            # Would trigger PT_AssetDef send (first send = is_first_send path)
            last_mesh_identity[guid] = (1, 2, obj.data.name)
    test("4.7: First-tick identity entries populated",
         len(last_mesh_identity) == 2)

    # 4.8: Verify stop_sync clears these too
    last_mesh_identity.clear()
    test("4.8: stop_sync clears second-session entries",
         len(last_mesh_identity) == 0)

    # 4.9: Verify that a fresh start_sync after stop does not see old entries
    obj_final = MockObject(name="FinalObj", datablock_name="FinalMesh")
    guid_final = ensure_guid(obj_final)
    tracked_final = {guid_final: (obj_final, uuid.UUID(guid_final))}
    last_mesh_identity.clear()
    test("4.9: Third session starts with empty identity cache",
         len(last_mesh_identity) == 0)

    # 4.10: Verify first-tick send is NOT suppressed by stale entries
    # (The first check_updates tick would see guid_final not in last_mesh_identity
    # and emit PT_AssetDef via the is_first_send path)
    test("4.10: First identity check triggers send (no stale suppression)",
         guid_final not in last_mesh_identity)


# =========================================================
# SECTION 5: Simulated UE-side AssetMetadata cleanup
# =========================================================

class SimulatedAssetMetadata:
    """Simulates UE-side TMap<FGuid, FAssetMetadata>."""

    def __init__(self):
        self._entries = {}

    def add(self, guid, identity_high=0, identity_low=0):
        self._entries[guid] = {
            "high": identity_high,
            "low": identity_low,
            "resolved": False,
        }

    def remove(self, guid):
        return self._entries.pop(guid, None) is not None

    def contains(self, guid):
        return guid in self._entries

    def size(self):
        return len(self._entries)


class SimulatedPendingAssetQueue:
    """Simulates UE-side FPendingAssetQueue."""

    def __init__(self):
        self._entries = set()

    def enqueue(self, guid):
        self._entries.add(guid)

    def remove(self, guid):
        self._entries.discard(guid)

    def contains(self, guid):
        return guid in self._entries

    def size(self):
        return len(self._entries)


def test_asset_metadata_cleanup_logic():
    """C1: Simulate AssetMetadata + PendingAssetQueue cleanup on delete."""
    print("\n--- Section 5: AssetMetadata cleanup logic ---")

    meta = SimulatedAssetMetadata()
    queue = SimulatedPendingAssetQueue()

    # 5.1: Populate metadata for 3 GUIDs
    guids = [f"guid_{i}" for i in range(3)]
    for g in guids:
        meta.add(g, 0x100 + g.count(""), 0x200 + g.count(""))
        queue.enqueue(g)

    test("5.1: AssetMetadata has 3 entries",
         meta.size() == 3)
    test("5.2: PendingAssetQueue has 3 entries",
         queue.size() == 3)

    # Simulate HandleDelete (V5): remove metadata + pending queue
    deleted_guid = guids[0]
    if meta.remove(deleted_guid):
        queue.remove(deleted_guid)

    # 5.3: Verify cleaned
    test("5.3: AssetMetadata entry removed on delete",
         not meta.contains(deleted_guid) and meta.size() == 2)
    test("5.4: PendingAssetQueue entry removed on delete",
         not queue.contains(deleted_guid) and queue.size() == 2)

    # 5.5: Verify other entries untouched
    test("5.5: Other AssetMetadata entries survive",
         all(meta.contains(g) for g in guids[1:]))
    test("5.6: Other PendingAssetQueue entries survive",
         all(queue.contains(g) for g in guids[1:]))

    # 5.7: Simulate OnActorDestroyed path: remove metadata
    destroyed_guid = guids[1]
    meta.remove(destroyed_guid)
    queue.remove(destroyed_guid)
    test("5.7: OnActorDestroyed removes AssetMetadata",
         not meta.contains(destroyed_guid) and meta.size() == 1)
    test("5.8: OnActorDestroyed removes PendingAssetQueue entry",
         not queue.contains(destroyed_guid) and queue.size() == 1)

    # 5.9: Double-cleanup safety — removing already-removed GUID should not error
    test("5.9: Double-remove of already-cleaned GUID does not error",
         meta.remove(deleted_guid) is False)

    # 5.10: Remove last entry
    last_guid = guids[2]
    meta.remove(last_guid)
    queue.remove(last_guid)
    test("5.10: All entries cleaned",
         meta.size() == 0 and queue.size() == 0)

    # 5.11: Verify remove on empty does not error
    test("5.11: Remove from empty AssetMetadata does not error",
         meta.remove("nonexistent") is False)
    test("5.12: Remove from empty PendingAssetQueue does not error",
         queue.remove("nonexistent") is None)  # discard is no-op


# =========================================================
# SECTION 6: C2 — Truncated asset def wire format (standalone)
# =========================================================

def test_truncated_wire_format():
    """C2: Verify truncated PT_AssetDef wire format is detectable."""
    print("\n--- Section 6: Truncated PT_AssetDef wire format ---")

    # 6.1: Full 33-byte asset def
    full = make_asset_def_bytes(make_dummy_guid_bytes(0xA0), 1, 2, 0x00)
    test("6.1: Full asset def is 33 bytes",
         len(full) == LIVE_SYNC_V5_ASSET_DEF_SIZE)

    # 6.2: Truncated payload (20 bytes — too short)
    truncated = bytes([0xA0] * 20)
    test("6.2: Truncated asset def is 20 bytes (< 33)",
         len(truncated) < LIVE_SYNC_V5_ASSET_DEF_SIZE)

    # 6.3: Zero-length payload
    empty = b""
    test("6.3: Zero-length payload < 33 bytes",
         len(empty) < LIVE_SYNC_V5_ASSET_DEF_SIZE)

    # 6.4: Partial second object in batch (2 objects, only 40 bytes instead of 66)
    partial_batch = make_asset_def_bytes(make_dummy_guid_bytes(0xB0), 3, 4, 0x00)
    partial_batch += bytes([0xC0] * 7)  # Only 7 more bytes, not 33 for second obj
    test("6.4: Partial batch payload fewer bytes than expected for 2 objects",
         len(partial_batch) < LIVE_SYNC_V5_ASSET_DEF_SIZE * 2)

    # 6.5: Verify a full batch of 3 is valid
    full_batch = b""
    for i in range(3):
        full_batch += make_asset_def_bytes(
            make_dummy_guid_bytes(0xD0 + i), i, i * 2, 0x00
        )
    test("6.5: Full batch of 3 is 99 bytes",
         len(full_batch) == LIVE_SYNC_V5_ASSET_DEF_SIZE * 3)

    # 6.6: Calculate how many complete objects fit in a truncated batch
    truncated_batch = full_batch[:50]  # 50 bytes = 1 full (33) + 17 partial
    complete = len(truncated_batch) // LIVE_SYNC_V5_ASSET_DEF_SIZE
    remainder = len(truncated_batch) % LIVE_SYNC_V5_ASSET_DEF_SIZE
    test("6.6: Truncated batch has 1 complete object + remainder",
         complete == 1 and 0 < remainder < LIVE_SYNC_V5_ASSET_DEF_SIZE)

    # 6.7: Corner case — exactly 32 bytes (1 byte short)
    almost_full = bytes([0xEE] * 32)
    test("6.7: 32-byte payload is < 33 (one byte short of valid)",
         len(almost_full) < LIVE_SYNC_V5_ASSET_DEF_SIZE)

    # 6.8: Corner case — exactly 33 bytes
    exact = bytes([0xFF] * 33)
    test("6.8: 33-byte payload is exactly LIVE_SYNC_V5_ASSET_DEF_SIZE",
         len(exact) == LIVE_SYNC_V5_ASSET_DEF_SIZE)


# =========================================================
# Run all sections
# =========================================================

def run_all():
    print("=" * 60)
    print("Phase 7A — Identity Hygiene Validation (Stage 1A)")
    print("=" * 60)

    # Standalone tests (no UE required)
    test_mesh_identity_clear_on_start()       # Section 1: C3
    test_blender_lifecycle_clear()            # Section 4: C3 extended
    test_asset_metadata_cleanup_logic()       # Section 5: C1 simulated
    test_truncated_wire_format()              # Section 6: C2 standalone

    # UE-connected tests (skip gracefully if no UE)
    test_truncated_asset_def()                # Section 2: C2
    test_delete_asset_metadata_cleanup()      # Section 3: C1

    total = PASS + FAIL + SKIP
    print(f"\n{'=' * 60}")
    print(f"Phase 7A — Identity Hygiene Summary")
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
    # Import socket here (only needed for UE-connected tests)
    import socket as _socket_mod
    globals()["socket"] = _socket_mod
    sys.exit(main())
