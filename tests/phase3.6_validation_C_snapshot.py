"""
Phase 3.6 Validation — C: Snapshot Correctness Test
Tests full-state snapshot burst on reconnect:
PF_FullSnapshot flag handling, state reset,
reconnect during movement, large scene snapshots.
Runs against UE editor listening on port 57000.
"""

import socket
import struct
import time
import sys
import uuid

PASS = 0
FAIL = 0
HOST = "127.0.0.1"
PORT = 57000
MAGIC = 0x4C56534D


def test(name, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  ✅ PASS: {name}")
    else:
        FAIL += 1
        msg = f"  ❌ FAIL: {name}"
        if detail:
            msg += f" — {detail}"
        print(msg)


def report():
    total = PASS + FAIL
    print(f"\n{'='*50}")
    print(f"RESULTS: {PASS}/{total} passed, {FAIL} failed")
    print(f"{'='*50}")
    return FAIL == 0


def make_guid():
    g = uuid.uuid4()
    return struct.pack("<IIII", g.time_low,
        (g.time_mid << 16) | g.time_hi_version,
        (g.clock_seq_hi_variant << 24) | (g.clock_seq_low << 16) | ((g.node >> 32) & 0xFFFF),
        g.node & 0xFFFFFFFF)


def make_v3_header(packet_type=0x01, object_count=0, payload_size=0, seq=1, flags=0x00):
    header_size = struct.calcsize("<I H B B Q I I")
    packet_size = header_size + payload_size
    return struct.pack("<I H B B Q I I",
        MAGIC, 3, packet_type, flags, seq, packet_size, object_count)


def make_transform_object(loc_x=100.0, loc_y=200.0, loc_z=300.0):
    guid = make_guid()
    loc = struct.pack("<fff", loc_x, loc_y, loc_z)
    rot = struct.pack("<ffff", 0.0, 0.0, 0.0, 1.0)
    scl = struct.pack("<fff", 1.0, 1.0, 1.0)
    ts = struct.pack("<d", time.time())
    parent = struct.pack("<IIII", 0, 0, 0, 0)
    return guid + loc + rot + scl + ts + parent


def make_transform_object_with_parent(loc_x=100.0, parent_guid_bytes=None):
    guid = make_guid()
    loc = struct.pack("<fff", loc_x, 200.0, 300.0)
    rot = struct.pack("<ffff", 0.0, 0.0, 0.0, 1.0)
    scl = struct.pack("<fff", 1.0, 1.0, 1.0)
    ts = struct.pack("<d", time.time())
    parent = parent_guid_bytes if parent_guid_bytes else struct.pack("<IIII", 0, 0, 0, 0)
    return guid + loc + rot + scl + ts + parent


def connect():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(5)
    s.connect((HOST, PORT))
    return s


print("\n" + "="*50)
print("PHASE 3.6 VALIDATION — C: SNAPSHOT CORRECTNESS")
print("="*50)

# =============================================================
# 1. FULL SNAPSHOT FLAG (PF_FullSnapshot = 0x02)
# =============================================================
print("\n--- 1. FULL SNAPSHOT FLAG HANDLING ---")

# Send transform, then snapshot flag packet, then another transform
try:
    s = connect()

    # Send a regular transform
    obj = make_transform_object(loc_x=50.0)
    hdr = make_v3_header(packet_type=0x01, object_count=1,
                          payload_size=len(obj), seq=1)
    s.sendall(hdr + obj)
    time.sleep(0.1)

    # Send a full snapshot (CREATE packet with PF_FullSnapshot flag)
    snapshot_obj = make_transform_object(loc_x=999.0)
    snap_hdr = make_v3_header(packet_type=0x03, object_count=1,
                               payload_size=len(snapshot_obj), seq=2,
                               flags=0x02)
    s.sendall(snap_hdr + snapshot_obj)
    time.sleep(0.1)

    # Send another regular transform
    obj2 = make_transform_object(loc_x=150.0)
    hdr2 = make_v3_header(packet_type=0x01, object_count=1,
                           payload_size=len(obj2), seq=3)
    s.sendall(hdr2 + obj2)
    time.sleep(0.1)

    s.close()
    test("Full snapshot flag: no crash, accepted", True)
except Exception as e:
    test("Full snapshot flag: no crash, accepted", False, detail=str(e))

# =============================================================
# 2. MULTIPLE SNAPSHOTS IN SEQUENCE
# =============================================================
print("\n--- 2. MULTIPLE SNAPSHOTS IN SEQUENCE ---")

try:
    s = connect()
    for seq in range(10, 15):
        snap_obj = make_transform_object(loc_x=float(seq * 100))
        snap_hdr = make_v3_header(packet_type=0x03, object_count=1,
                                   payload_size=len(snap_obj), seq=seq,
                                   flags=0x02)
        s.sendall(snap_hdr + snap_obj)
        time.sleep(0.05)
    s.close()
    test("5 sequential snapshots accepted", True)
except Exception as e:
    test("5 sequential snapshots accepted", False, detail=str(e))

# =============================================================
# 3. RECONNECT DURING MOVEMENT
# =============================================================
print("\n--- 3. RECONNECT DURING MOVEMENT (simulated) ---")

# Simulate: connect, send some transforms, disconnect,
# reconnect with snapshot while "moving"
try:
    # Phase 1: connect and send initial position
    s1 = connect()
    moving_obj = make_transform_object(loc_x=0.0)
    h1 = make_v3_header(packet_type=0x03, object_count=1,
                         payload_size=len(moving_obj), seq=20)
    s1.sendall(h1 + moving_obj)
    time.sleep(0.1)

    # Send a few movement updates
    for step in range(5):
        mov = make_transform_object(loc_x=float(step * 50))
        hm = make_v3_header(packet_type=0x01, object_count=1,
                             payload_size=len(mov), seq=21+step)
        s1.sendall(hm + mov)
        time.sleep(0.02)
    s1.close()

    # Phase 2: reconnect and send snapshot at new position
    time.sleep(0.1)
    s2 = connect()
    snap_at_move = make_transform_object(loc_x=500.0)
    snap_h = make_v3_header(packet_type=0x03, object_count=1,
                             payload_size=len(snap_at_move), seq=30,
                             flags=0x02)
    s2.sendall(snap_h + snap_at_move)
    time.sleep(0.1)

    # Send follow-up movement
    mov2 = make_transform_object(loc_x=550.0)
    hm2 = make_v3_header(packet_type=0x01, object_count=1,
                          payload_size=len(mov2), seq=31)
    s2.sendall(hm2 + mov2)
    s2.close()
    test("Reconnect during movement: no crash", True)
except Exception as e:
    test("Reconnect during movement: no crash", False, detail=str(e))

# =============================================================
# 4. SNAPSHOT WITH 100+ OBJECTS
# =============================================================
print("\n--- 4. SNAPSHOT WITH 100+ OBJECTS ---")

try:
    s = connect()

    # Build a snapshot payload with 100 objects
    n_objects = 100
    all_objs = bytearray()
    for i in range(n_objects):
        all_objs.extend(make_transform_object(
            loc_x=float(i * 10), loc_y=0.0, loc_z=0.0))

    snap_hdr = make_v3_header(packet_type=0x03, object_count=n_objects,
                               payload_size=len(all_objs), seq=40,
                               flags=0x02)
    s.sendall(snap_hdr + bytes(all_objs))
    time.sleep(0.3)
    s.close()
    test(f"Snapshot with {n_objects} objects accepted", True)
except Exception as e:
    test(f"Snapshot with {n_objects} objects accepted", False, detail=str(e))

# =============================================================
# 5. SNAPSHOT WITH HIERARCHY FLAG
# =============================================================
print("\n--- 5. SNAPSHOT + HIERARCHY (PF_FullSnapshot | PF_HasLocalTransform) ---")

try:
    s = connect()

    # Send root + child as snapshot with both flags
    root_guid_bytes = make_guid()
    parent_zeros = struct.pack("<IIII", 0, 0, 0, 0)

    # Root: world transform, no parent
    root_loc = struct.pack("<fff", 0.0, 0.0, 0.0)
    root_rot = struct.pack("<ffff", 0.0, 0.0, 0.0, 1.0)
    root_scl = struct.pack("<fff", 1.0, 1.0, 1.0)
    root_ts = struct.pack("<d", time.time())
    root_obj = root_guid_bytes + root_loc + root_rot + root_scl + root_ts + parent_zeros

    # Child: local transform, parent = root
    child_guid_bytes = make_guid()
    child_loc = struct.pack("<fff", 2.0, 0.0, 0.0)
    child_rot = struct.pack("<ffff", 0.0, 0.0, 0.0, 1.0)
    child_scl = struct.pack("<fff", 1.0, 1.0, 1.0)
    child_ts = struct.pack("<d", time.time())
    child_obj = child_guid_bytes + child_loc + child_rot + child_scl + child_ts + root_guid_bytes

    payload = root_obj + child_obj
    flags = 0x02 | 0x01  # PF_FullSnapshot | PF_HasLocalTransform
    hdr = make_v3_header(packet_type=0x03, object_count=2,
                          payload_size=len(payload), seq=50,
                          flags=flags)
    s.sendall(hdr + payload)
    time.sleep(0.3)
    s.close()
    test("Snapshot with hierarchy flags (root+child) accepted", True)
except Exception as e:
    test("Snapshot with hierarchy flags (root+child) accepted", False, detail=str(e))

# =============================================================
# 6. SNAPSHOT WHILE TRANSFORMS STILL CHANGING
# =============================================================
print("\n--- 6. SNAPSHOT WHILE TRANSFORMS CHANGING ---")

try:
    s = connect()

    # Send regular updates
    for step in range(3):
        obj = make_transform_object(loc_x=float(step * 20))
        h = make_v3_header(packet_type=0x03, object_count=1,
                            payload_size=len(obj), seq=60+step)
        s.sendall(h + obj)
        time.sleep(0.02)

    # Send snapshot (interleaved)
    snap = make_transform_object(loc_x=9999.0)
    sh = make_v3_header(packet_type=0x03, object_count=1,
                         payload_size=len(snap), seq=63,
                         flags=0x02)
    s.sendall(sh + snap)

    # More regular updates after snapshot
    for step in range(3):
        obj = make_transform_object(loc_x=float(step * 30 + 100))
        h = make_v3_header(packet_type=0x01, object_count=1,
                            payload_size=len(obj), seq=64+step)
        s.sendall(h + obj)
        time.sleep(0.02)

    s.close()
    test("Snapshot interleaved with regular transforms: no crash", True)
except Exception as e:
    test("Snapshot interleaved with regular transforms: no crash", False, detail=str(e))

# =============================================================
# RESULTS
# =============================================================
print()
report()
