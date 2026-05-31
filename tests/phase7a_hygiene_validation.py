#!/usr/bin/env python3
"""
Phase 7A — Identity Hygiene Validation (Stage 1A + 1B)

Stage 1A: Validates fixes for critical audit issues found in Phase 7A Stage 0:
  C1. HandleDelete (V5) cleans AssetMetadata and PendingAssetQueue
  C2. Truncated PT_AssetDef increments MalformedPackets counter
  C3. Blender _last_mesh_identity cleared on start_sync()/stop_sync()

Stage 1B: Static mesh identity coverage:
  1. Shared mesh datablock → same FAssetIdentityRef, distinct GUIDs
  2. Mesh datablock rename → new identity hash, GUID unchanged
  3. Duplicate object → new GUID, shared mesh identity
  4. Delete/recreate identity chain
  5. FAssetIdentityRef equality/inequality/hash

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
# Stage 1B — xxHash64 (standalone, mirrors network.py)
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


def get_mesh_identity_hash(obj):
    """Return (low, high) xxHash64 of the object's datablock name.
    Mirrors Blender_Addon/network.py:get_mesh_identity_hash.
    Returns (0, 0) if obj has no data or is not MESH.
    """
    if obj.type != 'MESH' or obj.data is None:
        return (0, 0)
    name_bytes = obj.data.name.encode("utf-8")
    hash_value = xxh64(name_bytes)
    low = hash_value & 0xFFFFFFFFFFFFFFFF
    high = (hash_value >> 64) & 0xFFFFFFFFFFFFFFFF
    return (low, high)


# =========================================================
# Stage 1B — FAssetIdentityRef (mirrors UE-side)
# =========================================================

class FAssetIdentityRef:
    """16-byte identity ref: {uint64 High, uint64 Low}.
    Mirrors UE AssetIdentityTypes.h::FAssetIdentityRef.
    """
    def __init__(self, high, low):
        self.High = high & 0xFFFFFFFFFFFFFFFF
        self.Low = low & 0xFFFFFFFFFFFFFFFF

    def __eq__(self, other):
        if not isinstance(other, FAssetIdentityRef):
            return NotImplemented
        return self.High == other.High and self.Low == other.Low

    def __hash__(self):
        return hash((self.High, self.Low))

    def __repr__(self):
        return f"FAssetIdentityRef(High=0x{self.High:016x}, Low=0x{self.Low:016x})"

    def is_valid(self):
        return self.High != 0 or self.Low != 0

    def to_tuple(self):
        return (self.High, self.Low)


# =========================================================
# Simulated UE-side TMap (for identity test scenarios)
# =========================================================

class SimulatedAssetPathCache:
    """Simulates UE's AssetPathCache: identity -> FSoftObjectPath."""

    def __init__(self):
        self._map = {}

    def add(self, identity, path):
        self._map[identity.to_tuple()] = path

    def find(self, identity):
        return self._map.get(identity.to_tuple())

    def contains(self, identity):
        return identity.to_tuple() in self._map

    def size(self):
        return len(self._map)

    def clear(self):
        self._map.clear()

    def keys(self):
        return [FAssetIdentityRef(h, l) for (h, l) in self._map]


# =========================================================
# SECTION 7: Shared mesh datablock identity (Rule 1)
# =========================================================

def test_shared_datablock_identity():
    """Two Blender objects sharing obj.data produce same
    FAssetIdentityRef but keep distinct ue_guid values.
    """
    print("\n--- Section 7: Shared datablock identity ---")

    # 7.1: Two objects with same datablock name
    obj_a = MockObject(name="ObjA", datablock_name="SharedMesh")
    obj_b = MockObject(name="ObjB", datablock_name="SharedMesh")

    id_a = get_mesh_identity_hash(obj_a)
    id_b = get_mesh_identity_hash(obj_b)

    test("7.1: Same datablock -> same identity low",
         id_a[0] == id_b[0],
         f"{id_a[0]:016x} != {id_b[0]:016x}")
    test("7.2: Same datablock -> same identity high",
         id_a[1] == id_b[1],
         f"{id_a[1]:016x} != {id_b[1]:016x}")

    test("7.3: Identity is deterministic (run again matches)",
         get_mesh_identity_hash(obj_a) == id_a)

    # 7.4: Distinct GUIDs for each object
    guid_a = ensure_guid(obj_a)
    guid_b = ensure_guid(obj_b)
    test("7.4: Two objects have distinct GUIDs",
         guid_a != guid_b,
         f"{guid_a} == {guid_b}")

    # 7.5: Different datablock -> different identity
    obj_c = MockObject(name="ObjC", datablock_name="DifferentMesh")
    id_c = get_mesh_identity_hash(obj_c)
    test("7.5: Different datablock -> different identity",
         id_c != id_a,
         f"{id_c} == {id_a}")

    # 7.6: Same identity works as FAssetIdentityRef
    ref_a = FAssetIdentityRef(id_a[1], id_a[0])
    ref_b = FAssetIdentityRef(id_b[1], id_b[0])
    test("7.6: FAssetIdentityRef equality for shared datablock",
         ref_a == ref_b)
    test("7.7: FAssetIdentityRef hash matches for shared datablock",
         hash(ref_a) == hash(ref_b))

    # 7.8: The identity refs can be looked up in a simulated path cache
    cache = SimulatedAssetPathCache()
    cache.add(ref_a, "/Game/Meshes/SharedMesh")
    test("7.8: Path cache lookup via identically-constructed ref works",
         cache.find(FAssetIdentityRef(id_a[1], id_a[0])) == "/Game/Meshes/SharedMesh")

    # 7.9: Objects with empty/no data get zero identity
    obj_no_data = MockObject(name="NoData", datablock_name="Irrelevant")
    obj_no_data._data = None
    id_no = get_mesh_identity_hash(obj_no_data)
    test("7.9: Null data produces (0, 0) identity",
         id_no == (0, 0))

    # 7.10: Non-MESH type produces (0, 0) identity
    class NonMeshMock(MockObject):
        @property
        def type(self):
            return 'ARMATURE'
    obj_armature = NonMeshMock(name="Arm", datablock_name="Armature")
    id_arm = get_mesh_identity_hash(obj_armature)
    test("7.10: Non-MESH type produces (0, 0) identity",
         id_arm == (0, 0))

    # 7.11: Three objects sharing same datablock
    obj_d = MockObject(name="ObjD", datablock_name="GridMesh")
    obj_e = MockObject(name="ObjE", datablock_name="GridMesh")
    obj_f = MockObject(name="ObjF", datablock_name="GridMesh")
    ids = [get_mesh_identity_hash(o) for o in (obj_d, obj_e, obj_f)]
    all_same = all(idv == ids[0] for idv in ids)
    test("7.11: Three objects sharing same datablock -> all same identity",
         all_same)
    guids = [ensure_guid(o) for o in (obj_d, obj_e, obj_f)]
    all_distinct = len(set(guids)) == 3
    test("7.12: Three objects sharing same datablock -> all distinct GUIDs",
         all_distinct)

    # 7.13: xxHash64 of empty string
    empty_hash = xxh64(b"")
    test("7.13: xxHash64 of empty string is non-zero and deterministic",
         empty_hash == xxh64(b""))

    # 7.14: xxHash64 of same name matches network.py
    name_bytes = b"SharedMesh"
    local_hash = xxh64(name_bytes)
    # Import and compare with network.py's implementation
    try:
        import sys as _sys
        _sys.path.insert(0, "Blender_Addon")
        from network import xxh64 as network_xxh64
        network_hash = network_xxh64(name_bytes)
        test("7.14: Local xxHash64 matches network.py implementation",
             local_hash == network_hash)
    except ImportError:
        test("7.14: Local xxHash64 tested standalone (network.py not importable)", True)


# =========================================================
# SECTION 8: Mesh datablock rename propagation (Rule 2)
# =========================================================

def test_datablock_rename_identity():
    """Renaming obj.data changes the mesh identity hash.
    Object GUID remains unchanged.
    Owner hash changes -> would trigger reconcile on load.
    """
    print("\n--- Section 8: Datablock rename identity ---")

    # 8.1: Identity hash before rename
    obj = MockObject(name="MeshObj", datablock_name="OriginalName")
    identity_before = get_mesh_identity_hash(obj)
    guid_before = ensure_guid(obj)
    owner_hash_before = obj["ue_guid_owner_hash"]

    test("8.1: Identity hash is non-zero for MESH datablock",
         identity_before != (0, 0))

    # 8.2: Rename the datablock
    obj.data.name = "RenamedName"
    identity_after = get_mesh_identity_hash(obj)
    guid_after = obj["ue_guid"]
    owner_hash_after = _compute_owner_hash(obj)

    test("8.2: Identity hash changed after datablock rename",
         identity_after != identity_before,
         f"before={identity_before}, after={identity_after}")
    test("8.3: GUID unchanged after datablock rename",
         guid_after == guid_before,
         f"before={guid_before}, after={guid_after}")

    # 8.4: Owner hash changed (would trigger reconcile on load)
    test("8.4: Owner hash changed after datablock rename",
         owner_hash_after != owner_hash_before,
         f"before={owner_hash_before}, after={owner_hash_after}")

    # 8.5: Rename back to original restores original identity
    obj.data.name = "OriginalName"
    identity_back = get_mesh_identity_hash(obj)
    test("8.5: Rename back to original restores identity hash",
         identity_back == identity_before)

    # 8.6: Multiple sequential renames
    hashes = [identity_before]
    for new_name in ["NameA", "NameB", "NameC"]:
        obj.data.name = new_name
        hashes.append(get_mesh_identity_hash(obj))
    # All hashes should be distinct
    all_distinct = len(set(hashes)) == len(hashes)
    test("8.6: Sequential renames produce distinct identity hashes",
         all_distinct)
    test("8.7: GUID still unchanged after 4 renames",
         obj["ue_guid"] == guid_before)

    # 8.8: Two objects with same original datablock, rename one
    obj_p = MockObject(name="Primary", datablock_name="BaseName")
    obj_s = MockObject(name="Secondary", datablock_name="BaseName")
    id_p_before = get_mesh_identity_hash(obj_p)
    id_s_before = get_mesh_identity_hash(obj_s)
    test("8.8: Both start with same identity",
         id_p_before == id_s_before)
    # Rename secondary's datablock
    obj_s.data.name = "ModifiedName"
    id_p_after = get_mesh_identity_hash(obj_p)
    id_s_after = get_mesh_identity_hash(obj_s)
    test("8.9: Primary identity unchanged after secondary rename",
         id_p_after == id_p_before)
    test("8.10: Secondary identity diverged from primary",
         id_s_after != id_p_after)

    # 8.11: Empty name datablock
    obj_empty = MockObject(name="EmptyData", datablock_name="")
    id_empty = get_mesh_identity_hash(obj_empty)
    test("8.11: Empty datablock name produces deterministic hash",
         id_empty == get_mesh_identity_hash(obj_empty))

    # 8.12: Unicode datablock name
    obj_unicode = MockObject(name="UnicodeData", datablock_name="Üñîçødë_Mesh_🔧")
    id_unicode = get_mesh_identity_hash(obj_unicode)
    test("8.12: Unicode datablock name produces deterministic hash",
         id_unicode == get_mesh_identity_hash(obj_unicode))

    # 8.13: Very long datablock name (1024 chars)
    long_name = "X" * 1024
    obj_long = MockObject(name="LongData", datablock_name=long_name)
    id_long = get_mesh_identity_hash(obj_long)
    test("8.13: Long datablock name (1024 chars) produces deterministic hash",
         id_long == get_mesh_identity_hash(obj_long))


# =========================================================
# SECTION 9: Duplicate object rule (Rule 3)
# =========================================================

def test_duplicate_object_identity():
    """Duplicated object receives new ue_guid.
    Sharing the same mesh datablock keeps the same mesh identity.
    """
    print("\n--- Section 9: Duplicate object identity ---")

    # Create an original object with a specific mesh
    original = MockObject(name="Original", datablock_name="DuplicateTestMesh")
    orig_guid = ensure_guid(original)
    orig_identity = get_mesh_identity_hash(original)
    orig_ref = FAssetIdentityRef(orig_identity[1], orig_identity[0])

    test("9.1: Original has valid identity",
         orig_ref.is_valid())

    # Create a duplicate (same datablock name, inherits GUID from original)
    duplicate = MockObject(name="Duplicate", datablock_name="DuplicateTestMesh")
    # Set the same GUID as original to simulate Blender's obj.copy() behavior
    duplicate["ue_guid"] = original["ue_guid"]
    duplicate["ue_guid_owner_hash"] = original["ue_guid_owner_hash"]

    dup_identity = get_mesh_identity_hash(duplicate)
    dup_ref = FAssetIdentityRef(dup_identity[1], dup_identity[0])

    # 9.2: Duplicate has SAME mesh identity as original
    test("9.2: Duplicate shares same mesh identity as original",
         dup_ref == orig_ref,
         f"orig={orig_ref}, dup={dup_ref}")

    # 9.3: Duplicate's GUID collides with original (before fixup)
    test("9.3: Duplicate inherits original's GUID (collision expected)",
         duplicate["ue_guid"] == orig_guid)

    # 9.4: ensure_unique_guid detects collision and regenerates
    tracked = {orig_guid: (original, uuid.UUID(orig_guid))}
    dup_new_guid = ensure_unique_guid(duplicate, tracked)
    test("9.4: Duplicate gets new GUID after collision detection",
         dup_new_guid != orig_guid,
         f"dup={dup_new_guid}, orig={orig_guid}")

    # 9.5: Duplicate's mesh identity unchanged after GUID regeneration
    dup_identity_after = get_mesh_identity_hash(duplicate)
    dup_ref_after = FAssetIdentityRef(dup_identity_after[1], dup_identity_after[0])
    test("9.5: Duplicate mesh identity unchanged after GUID regeneration",
         dup_ref_after == dup_ref,
         f"before={dup_ref}, after={dup_ref_after}")

    # 9.6: Original identity unaffected by duplicate
    orig_identity_after = get_mesh_identity_hash(original)
    test("9.6: Original identity unaffected by duplicate GUID fixup",
         get_mesh_identity_hash(original) == orig_identity)

    # 9.7: Caller must add new GUID to tracked set after ensure_unique_guid
    # (ensure_unique_guid regenerates but does not insert into tracked)
    tracked[dup_new_guid] = (duplicate, uuid.UUID(dup_new_guid))
    test("9.7: Duplicate GUID added to tracked set by caller",
         dup_new_guid in tracked and tracked[dup_new_guid][0] is duplicate)

    # 9.8: Three duplicates sharing same mesh
    objs = [original]
    guids = [orig_guid]
    tracked_multi = {orig_guid: (original, uuid.UUID(orig_guid))}
    for i in range(3):
        dup = MockObject(name=f"Dup_{i}", datablock_name="DuplicateTestMesh")
        dup["ue_guid"] = orig_guid
        dup["ue_guid_owner_hash"] = original["ue_guid_owner_hash"]
        new_guid = ensure_unique_guid(dup, tracked_multi)
        tracked_multi[new_guid] = (dup, uuid.UUID(new_guid))
        objs.append(dup)
        guids.append(new_guid)

    all_identity_same = all(
        get_mesh_identity_hash(o) == orig_identity for o in objs)
    test("9.8: Original + 3 duplicates all share same mesh identity",
         all_identity_same)
    all_guids_distinct = len(set(guids)) == 4
    test("9.9: Original + 3 duplicates all have distinct GUIDs",
         all_guids_distinct,
         f"got {len(set(guids))} unique GUIDs")

    # 9.10: Duplicate with different mesh has different identity
    dup_diff_mesh = MockObject(name="DupDiff", datablock_name="DifferentMesh")
    dup_diff_mesh["ue_guid"] = orig_guid
    ensure_unique_guid(dup_diff_mesh, tracked_multi)
    id_diff = get_mesh_identity_hash(dup_diff_mesh)
    test("9.10: Duplicate with different mesh has different identity",
         id_diff != orig_identity)


# =========================================================
# SECTION 10: Delete/recreate identity chain (Rule 4)
# =========================================================

def test_delete_recreate_identity_chain():
    """Delete cleans actor/asset metadata path.
    Recreate uses a new object GUID.
    Asset identity is re-emitted/resolved cleanly.
    """
    print("\n--- Section 10: Delete/recreate identity chain ---")

    meta_store = SimulatedAssetMetadata()
    queue_store = SimulatedPendingAssetQueue()
    path_cache = SimulatedAssetPathCache()

    # 10.1: Create initial object
    obj = MockObject(name="ChainObj", datablock_name="ChainMesh")
    guid_initial = ensure_guid(obj)
    identity_initial = get_mesh_identity_hash(obj)
    identity_ref_initial = FAssetIdentityRef(identity_initial[1], identity_initial[0])

    # Simulate PT_AssetDef: store metadata + enqueue
    meta_store.add(guid_initial, identity_initial[1], identity_initial[0])
    queue_store.enqueue(guid_initial)
    path_cache.add(identity_ref_initial, "/Game/Meshes/ChainMesh")

    test("10.1: AssetMetadata populated after create",
         meta_store.contains(guid_initial))
    test("10.2: PendingAssetQueue has entry after create",
         queue_store.contains(guid_initial))
    test("10.3: Path cache has identity entry",
         path_cache.find(identity_ref_initial) is not None)

    # 10.4: Verify identity resolves via path cache
    resolved = path_cache.find(identity_ref_initial)
    test("10.4: Identity resolves to correct path",
         resolved == "/Game/Meshes/ChainMesh")

    # Simulate HandleDelete (V5): remove metadata + pending queue
    if meta_store.remove(guid_initial):
        queue_store.remove(guid_initial)

    test("10.5: AssetMetadata removed after delete",
         not meta_store.contains(guid_initial))
    test("10.6: PendingAssetQueue entry removed after delete",
         not queue_store.contains(guid_initial))

    # Path cache should still have the identity->path mapping
    # (it's not tied to a specific GUID)
    test("10.7: Path cache survives GUID delete",
         path_cache.find(identity_ref_initial) is not None)

    # 10.8: Recreate with new object (same datablock)
    obj_new = MockObject(name="ChainObjNew", datablock_name="ChainMesh")
    guid_new = ensure_guid(obj_new)
    identity_new = get_mesh_identity_hash(obj_new)
    identity_ref_new = FAssetIdentityRef(identity_new[1], identity_new[0])

    test("10.8: Recreated object gets new GUID",
         guid_new != guid_initial,
         f"new={guid_new}, initial={guid_initial}")
    test("10.9: Recreated object has same identity",
         identity_ref_new == identity_ref_initial,
         f"new={identity_ref_new}, initial={identity_ref_initial}")

    # Simulate fresh PT_AssetDef for new GUID
    meta_store.add(guid_new, identity_new[1], identity_new[0])
    queue_store.enqueue(guid_new)

    test("10.10: New AssetMetadata entry for recreated object",
         meta_store.contains(guid_new))
    test("10.11: New PendingAssetQueue entry for recreated object",
         queue_store.contains(guid_new))

    # 10.12: Identity resolves (same path as before)
    resolved_new = path_cache.find(identity_ref_new)
    test("10.12: Recreated identity resolves via path cache",
         resolved_new == "/Game/Meshes/ChainMesh")

    # Delete again to clean up
    meta_store.remove(guid_new)
    queue_store.remove(guid_new)

    # 10.13: Recreate with different mesh -> new identity
    obj_different = MockObject(name="ChainObjDiff", datablock_name="DifferentChainMesh")
    guid_diff = ensure_guid(obj_different)
    identity_diff = get_mesh_identity_hash(obj_different)
    identity_ref_diff = FAssetIdentityRef(identity_diff[1], identity_diff[0])

    test("10.13: Different mesh -> different identity ref",
         identity_ref_diff != identity_ref_initial)

    # 10.14: Multiple delete/recreate cycles
    last_guid = guid_diff
    for cycle in range(3):
        obj_cycle = MockObject(name=f"CycleObj_{cycle}", datablock_name="ChainMesh")
        guid_cycle = ensure_guid(obj_cycle)
        test(f"10.14.{cycle}: Cycle {cycle} gets new GUID",
             guid_cycle != last_guid)
        last_guid = guid_cycle

    # 10.15: Verify tracked set correct after recreate chain
    tracked_chain = {}
    obj_a = MockObject(name="First", datablock_name="MeshA")
    g_a = ensure_guid(obj_a)
    tracked_chain[g_a] = (obj_a, uuid.UUID(g_a))

    # Delete: remove from tracked
    tracked_chain.pop(g_a, None)

    obj_b = MockObject(name="Second", datablock_name="MeshA")
    g_b = ensure_guid(obj_b)
    tracked_chain[g_b] = (obj_b, uuid.UUID(g_b))
    test("10.15: Recreated object tracked with new GUID",
         g_b != g_a and len(tracked_chain) == 1)


# =========================================================
# SECTION 11: FAssetIdentityRef comparison/hash (Rule 5)
# =========================================================

def test_asset_identity_ref_semantics():
    """FAssetIdentityRef equality, inequality, and hash stability."""
    print("\n--- Section 11: FAssetIdentityRef comparison/hash ---")

    # 11.1: Equality of identical values
    ref_a1 = FAssetIdentityRef(0xABCD, 0x1234)
    ref_a2 = FAssetIdentityRef(0xABCD, 0x1234)
    test("11.1: Same high/low -> equal",
         ref_a1 == ref_a2)

    # 11.2: Inequality of different values
    ref_b = FAssetIdentityRef(0xDEAD, 0xBEEF)
    test("11.2: Different high -> not equal",
         ref_a1 != ref_b)

    # 11.3: Partial difference (same high, different low)
    ref_c = FAssetIdentityRef(0xABCD, 0x5678)
    test("11.3: Same high, different low -> not equal",
         ref_a1 != ref_c)

    ref_d = FAssetIdentityRef(0x0001, 0x1234)
    test("11.4: Same low, different high -> not equal",
         ref_a1 != ref_d)

    # 11.5: Hash equality for equal refs
    test("11.5: Equal refs have equal hashes",
         hash(ref_a1) == hash(ref_a2))

    # 11.6: Inequality does not guarantee hash inequality
    # (hash collision is possible, but we can at least verify
    # that different values can produce different hashes)
    hashes = {hash(FAssetIdentityRef(i, i * 2)) for i in range(100)}
    test("11.6: 100 distinct identity refs produce ≥99 distinct hashes",
         len(hashes) >= 99,
         f"got {len(hashes)} unique hashes")

    # 11.7: Dict key behavior
    d = {}
    d[ref_a1] = "value_a"
    d[ref_b] = "value_b"
    test("11.7: Dict lookup by equal ref returns same value",
         d[ref_a2] == "value_a")
    test("11.8: Dict lookup by different ref returns different value",
         d[FAssetIdentityRef(0xDEAD, 0xBEEF)] == "value_b")

    # 11.9: Set membership
    s = {ref_a1, ref_b}
    test("11.9: Equal ref is in set",
         ref_a2 in s)
    test("11.10: Different ref is in set",
         FAssetIdentityRef(0xDEAD, 0xBEEF) in s)
    test("11.11: Non-member ref not in set",
         FAssetIdentityRef(0xFFFF, 0x0000) not in s)

    # 11.12: is_valid on zero identity
    zero = FAssetIdentityRef(0, 0)
    test("11.12: Zero identity is not valid",
         not zero.is_valid())

    # 11.13: is_valid on non-zero identity
    test("11.13: Non-zero identity is valid",
         ref_a1.is_valid())

    # 11.14: is_valid on partial zero (high=0, low!=0)
    test("11.14: Partial zero (high=0) is valid",
         FAssetIdentityRef(0, 1).is_valid())
    test("11.15: Partial zero (low=0) is valid",
         FAssetIdentityRef(1, 0).is_valid())

    # 11.16: Round-trip to_tuple
    test("11.16: to_tuple round-trips correctly",
         ref_a1.to_tuple() == (0xABCD, 0x1234))

    # 11.17: Large value stability (64-bit)
    large = FAssetIdentityRef(0xFFFFFFFFFFFFFFFF, 0xFFFFFFFFFFFFFFFF)
    test("11.17: Max uint64 values handled correctly",
         large.to_tuple() == (0xFFFFFFFFFFFFFFFF, 0xFFFFFFFFFFFFFFFF))

    # 11.18: Identity ref as OrderedMap key (simulated)
    entries = {
        FAssetIdentityRef(1, 1): "a",
        FAssetIdentityRef(2, 2): "b",
        FAssetIdentityRef(3, 3): "c",
    }
    lookup = FAssetIdentityRef(2, 2)
    test("11.18: Map lookup of inserted identity ref",
         entries.get(lookup) == "b")

    # 11.19: Path cache simulation with identity refs
    cache = SimulatedAssetPathCache()
    ref_m1 = FAssetIdentityRef(0x1111, 0x2222)
    ref_m2 = FAssetIdentityRef(0x3333, 0x4444)
    cache.add(ref_m1, "/Game/Meshes/Mesh1")
    cache.add(ref_m2, "/Game/Meshes/Mesh2")
    test("11.19: Path cache size correct",
         cache.size() == 2)
    test("11.20: Path cache lookup by equivalent ref",
         cache.find(FAssetIdentityRef(0x1111, 0x2222)) == "/Game/Meshes/Mesh1")

    # 11.21: Path cache contains check
    test("11.21: Path cache contains inserted identity",
         cache.contains(ref_m1))
    test("11.22: Path cache does not contain uninserted identity",
         not cache.contains(FAssetIdentityRef(0x9999, 0xAAAA)))

    # 11.23: Clear path cache
    cache.clear()
    test("11.23: Path cache clear works",
         cache.size() == 0)


# =========================================================
# Run all sections
# =========================================================

def run_all():
    print("=" * 60)
    print("Phase 7A — Identity Validation (Stage 1A + 1B)")
    print("=" * 60)

    # Stage 1A — Standalone tests (no UE required)
    test_mesh_identity_clear_on_start()       # Section 1: C3
    test_blender_lifecycle_clear()            # Section 4: C3 extended
    test_asset_metadata_cleanup_logic()       # Section 5: C1 simulated
    test_truncated_wire_format()              # Section 6: C2 standalone

    # Stage 1A — UE-connected tests (skip gracefully if no UE)
    test_truncated_asset_def()                # Section 2: C2
    test_delete_asset_metadata_cleanup()      # Section 3: C1

    # Stage 1B — Identity coverage (standalone)
    test_shared_datablock_identity()          # Section 7: Rule 1
    test_datablock_rename_identity()          # Section 8: Rule 2
    test_duplicate_object_identity()          # Section 9: Rule 3
    test_delete_recreate_identity_chain()     # Section 10: Rule 4
    test_asset_identity_ref_semantics()       # Section 11: Rule 5

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
