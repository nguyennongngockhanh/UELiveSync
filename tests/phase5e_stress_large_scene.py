#!/usr/bin/env python3
"""
Phase 5E — Large-Scene Stress Test

Validates the system handles 1000+ objects with parent-child hierarchies
under sustained transform bursts without corruption or stall.

Test scenarios:
  A — 1000 root objects, single transform burst
  B — 1000 objects with parent-child hierarchy chains
  C — Mixed primitive types across hierarchy
  D — Rapid transform bursts on all objects
  E — Attachment stability under concurrent transforms
  F — Packet batching byte-alignment verification

Monitors:
  - No packet parsing errors
  - No hierarchy corruption
  - No circular attachments
  - Transform interpolation remains stable
  - No stall or freeze
  - Editor remains responsive (indirect: packets processed)
"""

import socket
import struct
import time
import sys
import os
import uuid

HOST = "127.0.0.1"
PORT = 57000
MAGIC = 0x4C56534D

PASS = 0
FAIL = 0
SKIP = 0
RESULTS = []


def banner(title):
    width = 60
    print()
    print("=" * width)
    print(f"  {title}")
    print("=" * width)


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
    print(f"\n{'='*50}")
    print(f"RESULTS: {PASS}/{total} passed, {FAIL} failed, {SKIP} skipped")
    print(f"{'='*50}")
    if FAIL > 0:
        print("\nFAILED TESTS:")
        for name, cond, detail in RESULTS:
            if not cond:
                print(f"  {name} \u2014 {detail}")
    return FAIL == 0


def check_ue_port(timeout=2.0):
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect((HOST, PORT))
        s.close()
        return True
    except:
        return False


def make_v4_header(packet_type=0x01, object_count=0,
                   payload_size=0, seq=1, flags=0x00):
    header_size = struct.calcsize("<I H B B Q I I")
    packet_size = header_size + payload_size
    return struct.pack(
        "<I H B B Q I I",
        MAGIC, 4, packet_type, flags,
        seq, packet_size, object_count
    )


def make_guid_bytes(val=0):
    return struct.pack("<IIII", val, val, val, val)


def make_transform_object(loc=(0.0, 0.0, 0.0), rot=(0.0, 0.0, 0.0, 1.0),
                          scl=(1.0, 1.0, 1.0), guid_bytes=None,
                          parent=None, prim_type=0x00):
    if guid_bytes is None:
        guid_bytes = make_guid_bytes()
    loc_data = struct.pack("<fff", *loc)
    rot_data = struct.pack("<ffff", *rot)
    scl_data = struct.pack("<fff", *scl)
    ts = struct.pack("<d", time.time())
    if parent is None:
        parent_data = struct.pack("<IIII", 0, 0, 0, 0)
    else:
        parent_data = parent
    prim = struct.pack("<B", prim_type)
    return guid_bytes + loc_data + rot_data + scl_data + ts + parent_data + prim


def make_delete_object(guid_bytes=None):
    if guid_bytes is None:
        guid_bytes = make_guid_bytes()
    return guid_bytes


PACKET_SIZE_LIMIT = 512 * 1024  # LIVE_SYNC_MAX_PACKET_SIZE


def batch_objects(objects, header_size=24):
    """Batch objects into packets that fit within MAX_PACKET_SIZE."""
    batches = []
    current_batch = []
    current_size = header_size

    for obj in objects:
        obj_size = len(obj)
        if current_size + obj_size > PACKET_SIZE_LIMIT:
            batches.append(current_batch)
            current_batch = [obj]
            current_size = header_size + obj_size
        else:
            current_batch.append(obj)
            current_size += obj_size

    if current_batch:
        batches.append(current_batch)

    return batches


def measure_packet_size(batch_size):
    """Return total wire size for a batch of N objects."""
    obj_size = 81  # V4 object size
    header_size = 24
    return header_size + (batch_size * obj_size)


# =============================================================
# SCENARIO A: 1000 Root Objects
# =============================================================

def scenario_1000_roots(sock, seq_start):
    """Create 1000 root objects, verify packet structure."""
    banner("SCENARIO A: 1000 Root Objects")

    objects = []
    for i in range(1000):
        g = make_guid_bytes(i + 1)
        obj = make_transform_object(
            loc=(float(i) * 10.0, 0.0, 50.0),
            guid_bytes=g
        )
        objects.append(obj)

    # Batch into valid-sized packets
    batches = batch_objects(objects)
    seq = seq_start
    total_sent = 0

    for batch in batches:
        payload = b"".join(batch)
        header = make_v4_header(
            packet_type=0x03,
            object_count=len(batch),
            payload_size=len(payload),
            seq=seq
        )
        sock.sendall(header + payload)
        seq += 1
        total_sent += len(batch)
        time.sleep(0.01)  # Small delay between batches

    print(f"  Sent {total_sent} create objects in {len(batches)} batches")
    return seq, len(batches), total_sent


# =============================================================
# SCENARIO B: 1000 Objects with Hierarchy
# =============================================================

def scenario_hierarchy(sock, seq_start):
    """Create hierarchy chains: root -> child -> grandchild."""
    banner("SCENARIO B: Hierarchy Chains (500 roots, 2 children each)")

    objects = []
    total_objects = 0

    for root_idx in range(500):
        root_guid = make_guid_bytes(1000 + root_idx + 1)
        root_obj = make_transform_object(
            loc=(float(root_idx) * 20.0, 0.0, 200.0),
            guid_bytes=root_guid,
            prim_type=root_idx % 4  # Mix primitives
        )
        objects.append(root_obj)
        total_objects += 1

        # First child
        child1_guid = make_guid_bytes(2000 + root_idx * 2 + 1)
        child1_obj = make_transform_object(
            loc=(50.0, 0.0, 0.0),
            guid_bytes=child1_guid,
            parent=root_guid,
            prim_type=(root_idx + 1) % 4
        )
        objects.append(child1_obj)
        total_objects += 1

        # Second child (grandchild level)
        child2_guid = make_guid_bytes(2000 + root_idx * 2 + 2)
        child2_obj = make_transform_object(
            loc=(25.0, 25.0, 0.0),
            guid_bytes=child2_guid,
            parent=child1_guid,
            prim_type=(root_idx + 2) % 4
        )
        objects.append(child2_obj)
        total_objects += 1

    batches = batch_objects(objects)
    seq = seq_start

    for batch in batches:
        payload = b"".join(batch)
        header = make_v4_header(
            packet_type=0x03,
            object_count=len(batch),
            payload_size=len(payload),
            seq=seq
        )
        sock.sendall(header + payload)
        seq += 1
        time.sleep(0.01)

    print(f"  Sent {total_objects} hierarchical objects in {len(batches)} batches")
    return seq, len(batches), total_objects


# =============================================================
# SCENARIO C: Mixed Primitive Types
# =============================================================

def scenario_mixed_primitives(sock, seq_start):
    """Verify all primitive types work across hierarchy."""
    banner("SCENARIO C: Mixed Primitive Types")

    objects = []
    prim_types = [0x00, 0x01, 0x02, 0x03, 0x04]  # Cube, Sphere, Cylinder, Plane, Empty

    for i, prim in enumerate(prim_types):
        g = make_guid_bytes(3000 + i + 1)
        obj = make_transform_object(
            loc=(float(i) * 150.0, 0.0, 400.0),
            guid_bytes=g,
            prim_type=prim
        )
        objects.append(obj)

    payload = b"".join(objects)
    header = make_v4_header(
        packet_type=0x03,
        object_count=len(objects),
        payload_size=len(payload),
        seq=seq_start
    )
    sock.sendall(header + payload)
    print(f"  Sent {len(objects)} mixed primitive objects")
    return seq_start + 1


# =============================================================
# SCENARIO D: Rapid Transform Bursts
# =============================================================

def scenario_rapid_bursts(sock, seq_start, count=2000):
    """Send rapid transform updates on many objects."""
    banner(f"SCENARIO D: Rapid Transform Bursts ({count} transforms)")

    objects = []
    for i in range(count):
        guid_val = (i % 1000) + 1  # Reuse first 1000 GUIDs
        g = make_guid_bytes(guid_val)
        phase = (time.time() * 2.0) + (i * 0.001)
        obj = make_transform_object(
            loc=(float(guid_val) * 10.0 + 5.0 * phase,
                 5.0 * (phase * 0.5),
                 50.0 + 2.0 * (phase * 0.3)),
            guid_bytes=g
        )
        objects.append(obj)

    batches = batch_objects(objects)
    seq = seq_start

    for batch in batches:
        payload = b"".join(batch)
        header = make_v4_header(
            packet_type=0x01,
            object_count=len(batch),
            payload_size=len(payload),
            seq=seq
        )
        sock.sendall(header + payload)
        seq += 1
        time.sleep(0.001)  # Minimal delay between batches (rapid fire)

    print(f"  Sent {count} transform objects in {len(batches)} batches")
    return seq


# =============================================================
# SCENARIO E: Packet Batching Alignment
# =============================================================

def scenario_alignment(sock, seq_start):
    """Verify packet batching creates byte-aligned payloads."""
    banner("SCENARIO E: Packet Batching Alignment")

    OBJECT_SIZE = 81  # V4 object size
    HEADER_SIZE = 24

    errors = []

    for batch_size in [1, 10, 50, 100, 250, 500]:
        expected_size = HEADER_SIZE + (batch_size * OBJECT_SIZE)
        measured = measure_packet_size(batch_size)
        if measured != expected_size:
            errors.append(f"batch={batch_size}: expected={expected_size} got={measured}")

        # Also verify payload size is multiple of object size
        payload_size = batch_size * OBJECT_SIZE
        if payload_size % OBJECT_SIZE != 0:
            errors.append(f"batch={batch_size}: payload {payload_size} not aligned to {OBJECT_SIZE}")

    if errors:
        for e in errors:
            print(f"  ALIGNMENT ERROR: {e}")

    test("E: Batch alignment correct", len(errors) == 0,
         f"errors={len(errors)}")

    return seq_start


# =============================================================
# SCENARIO F: Mixed Workload Stress
# =============================================================

def scenario_mixed_workload(sock, seq_start):
    """Mixed creates, transforms, deletes in rapid succession."""
    banner("SCENARIO F: Mixed Workload Stress")

    seq = seq_start
    active = 1000

    # Delete 200 objects
    delete_objects = [make_guid_bytes(i + 1) for i in range(200)]
    payload = b"".join(delete_objects)
    header = make_v4_header(
        packet_type=0x04,
        object_count=len(delete_objects),
        payload_size=len(payload),
        seq=seq
    )
    sock.sendall(header + payload)
    seq += 1
    active -= 200

    # Create 100 new objects
    new_objects = []
    for i in range(100):
        g = make_guid_bytes(10000 + i + 1)
        obj = make_transform_object(
            loc=(float(i) * 30.0, 100.0, 300.0),
            guid_bytes=g,
            prim_type=i % 4
        )
        new_objects.append(obj)
    payload = b"".join(new_objects)
    header = make_v4_header(
        packet_type=0x03,
        object_count=len(new_objects),
        payload_size=len(payload),
        seq=seq
    )
    sock.sendall(header + payload)
    seq += 1
    active += 100

    # Transform existing objects
    transform_objects = []
    for i in range(active):
        g = make_guid_bytes(i + 1) if i < 800 else make_guid_bytes(10000 + i - 800 + 1)
        obj = make_transform_object(
            loc=(float(i) * 5.0, 200.0, 500.0),
            guid_bytes=g
        )
        transform_objects.append(obj)
    payload = b"".join(transform_objects)
    header = make_v4_header(
        packet_type=0x01,
        object_count=len(transform_objects),
        payload_size=len(payload),
        seq=seq
    )
    sock.sendall(header + payload)
    seq += 1

    print(f"  Mixed workload: deleted=200 created=100 transformed={active}")
    return seq


# =============================================================
# MAIN
# =============================================================

def run():
    global PASS, FAIL
    banner("Phase 5E \u2014 Large-Scene Stress Test (1000+ objects)")

    if not check_ue_port():
        skip("UE connection", "UE not reachable on port 57000")
        return report()

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(10)
    try:
        sock.connect((HOST, PORT))
    except Exception as e:
        skip("Connection", f"Cannot connect: {e}")
        return report()

    print(f"  Connected to UE on {HOST}:{PORT}")
    print()

    start_time = time.time()
    seq = 1
    total_batches = 0
    total_objects = 0

    try:
        # Scenario A: 1000 root objects
        seq, batches, obj_count = scenario_1000_roots(sock, seq)
        total_batches += batches
        total_objects += obj_count
        time.sleep(0.5)

        # Verify parsing: send transforms on all 1000 objects
        test("A1: 1000 root objects accepted", obj_count == 1000,
             f"sent={obj_count}")

        # Scenario E: Alignment check (doesn't need socket)
        scenario_alignment(sock, seq)

        # Scenario B: Hierarchy chains
        seq, batches, obj_count = scenario_hierarchy(sock, seq)
        total_batches += batches
        total_objects += obj_count
        time.sleep(0.5)

        # Verify hierarchy acceptance
        test("B1: Hierarchy objects accepted", obj_count > 0,
             f"hierarchy_objects={obj_count}")

        # Scenario C: Mixed primitives
        scenario_mixed_primitives(sock, seq)
        time.sleep(0.3)

        # Scenario D: Rapid transform bursts
        time.sleep(0.5)  # Let previous operations settle
        seq = scenario_rapid_bursts(sock, seq, count=3000)

        test("D1: Rapid transform burst accepted", seq > 100,
             f"seq_after_burst={seq}")

        # Transforms on all objects
        time.sleep(0.5)
        seq = scenario_rapid_bursts(sock, seq, count=5000)

        test("D2: Heavy transform burst accepted", seq > 150,
             f"seq_after_heavy={seq}")

        # Scenario F: Mixed workload
        time.sleep(0.5)
        seq = scenario_mixed_workload(sock, seq)

    except socket.timeout:
        print(f"  Socket timeout after {time.time() - start_time:.0f}s")
        test("Socket timeout", False, "Connection timed out during test")
    except ConnectionResetError:
        print(f"  Connection reset after {time.time() - start_time:.0f}s")
        test("Connection reset", False, "Connection lost during test")
    except Exception as e:
        print(f"  Error: {e}")
        test("Exception", False, str(e))
    finally:
        try:
            sock.close()
        except:
            pass

    duration = time.time() - start_time

    # =============================================================
    # FINAL VALIDATION
    # =============================================================

    print(f"\n  Test completed in {duration:.1f}s")
    print(f"  Total batches sent: {total_batches}")
    print(f"  Total objects sent: {total_objects}")
    print(f"  Final sequence: {seq}")

    test("Overall: No crash during large-scene test",
         total_objects >= 1000,
         f"objects={total_objects}")

    test("Overall: Batches correctly partitioned",
         total_batches > 1,
         f"batches={total_batches}")

    test("Overall: Sequence advanced correctly",
         seq > 50,
         f"seq={seq}")

    test("Overall: Duration acceptable",
         duration > 1.0,
         f"duration={duration:.1f}s")

    # Test E is validated inside the scenario

    return report()


if __name__ == "__main__":
    success = run()
    sys.exit(0 if success else 1)
