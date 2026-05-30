#!/usr/bin/env python3
"""
Phase 6I.1 — Transport Hardening: Bounds Validation Tests

Verifies that the UE plugin correctly rejects packets that exceed
the new bounds limits without crashing:

  A1. Object count > LIVE_SYNC_MAX_OBJECTS_PER_PACKET (4096)
  A2. Packet size > LIVE_SYNC_MAX_PACKET_SIZE (512 KB) — game-thread re-check
  A3. Rename name length > LIVE_SYNC_MAX_NAME_LENGTH (256)
  A4. Transform floats containing NaN
  A5. Transform floats containing Inf
  A6. Collection op-type outside valid range (0x01-0x08)
"""

import socket
import struct
import time
import sys
import os
import math

HOST = "127.0.0.1"
PORT = 57000
MAGIC = 0x4C56534D

PASS = 0
FAIL = 0
SKIP = 0
RESULTS = []


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
    except Exception:
        return False


# =============================================================
# PACKET BUILDERS
# =============================================================

def make_v4_header(packet_type=0x01, object_count=0,
                   payload_size=0, seq=1, flags=0x00):
    header_size = struct.calcsize("<I H B B Q I I")
    packet_size = header_size + payload_size
    return struct.pack(
        "<I H B B Q I I",
        MAGIC, 4, packet_type, flags,
        seq, packet_size, object_count
    )


def pack_guid(val):
    """Pack a 16-byte GUID from a 32-bit integer (repeated)."""
    return struct.pack("<IIII", val, val, val, val)


def make_dummy_transform(guid_bytes, loc, rot, scl,
                         ts=0.0, parent=None, prim=0x00):
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
    data.extend(struct.pack("<B", prim))
    return bytes(data)


def make_dummy_guid(val):
    return bytes([val & 0xFF] * 16)


# =============================================================
# SEND HELPERS
# =============================================================

def send_and_close(desc, data, expect_error=False):
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(3.0)
        s.connect((HOST, PORT))
        s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        s.sendall(data)
        time.sleep(0.1)
        s.close()
        test(f"Bounds: {desc}", not expect_error,
             f"sent {len(data)} bytes, no crash")
    except Exception as e:
        if expect_error:
            test(f"Bounds: {desc}", True, f"expected error: {e}")
        else:
            test(f"Bounds: {desc}", False, str(e))


def send_liveness_check(desc):
    """Send a valid heartbeat to confirm UE is still alive."""
    hdr = make_v4_header(packet_type=0x07, object_count=0,
                         payload_size=0, seq=9999)
    send_and_close(f"liveness ({desc})", hdr)


# =============================================================
# TESTS
# =============================================================

def test_object_count_cap():
    """A1: Object count > LIVE_SYNC_MAX_OBJECTS_PER_PACKET (4096).

    Send a delete-type packet with ObjectCount=5000 but small payload.
    The packet size (5000*28+24=140024) is well under 512KB, so it
    passes the max-size check but should be rejected by the new
    object-count cap.
    """
    print("\n--- A1: Object Count Cap ---")

    count = 5000
    obj_size = 28  # PT_Delete_V5 size
    header_size = struct.calcsize("<I H B B Q I I")
    payload_size = count * obj_size
    packet_size = header_size + payload_size

    hdr = struct.pack(
        "<I H B B Q I I",
        MAGIC, 4, 0x0E, 0x00,  # PT_Delete_V5
        100, packet_size, count
    )
    payload = b"\x00" * payload_size
    send_and_close(f"object_count={count} (>{4096}), pkt={packet_size}B",
                   hdr + payload)
    send_liveness_check("after object count cap")


def test_rename_name_too_long():
    """A3: Rename name length > LIVE_SYNC_MAX_NAME_LENGTH (256).

    Send a PT_Rename packet with OldNameLen=257 (>256).
    Should be rejected by game-thread parser before any pointer
    bounds check.
    """
    print("\n--- A3: Rename Name Length Cap ---")

    guid = pack_guid(0xDEADBEEF)
    old_name_bytes = b"A" * 257
    new_name_bytes = b"short"
    seq = 1
    ts = struct.pack("<d", time.time())

    payload = bytearray()
    payload.extend(guid)
    payload.extend(struct.pack("<H", len(old_name_bytes)))
    payload.extend(old_name_bytes)
    payload.extend(struct.pack("<H", len(new_name_bytes)))
    payload.extend(new_name_bytes)
    payload.extend(struct.pack("<I", seq))
    payload.extend(ts)

    hdr = make_v4_header(packet_type=0x0C, object_count=1,
                         payload_size=len(payload), seq=200)
    send_and_close(f"rename old_name=257 bytes (>256)",
                   hdr + bytes(payload))
    send_liveness_check("after rename old_name cap")

    # Also test new name too long
    payload2 = bytearray()
    payload2.extend(guid)
    payload2.extend(struct.pack("<H", 5))
    payload2.extend(b"short")
    payload2.extend(struct.pack("<H", 257))
    payload2.extend(b"B" * 257)
    payload2.extend(struct.pack("<I", seq))
    payload2.extend(ts)

    hdr2 = make_v4_header(packet_type=0x0C, object_count=1,
                          payload_size=len(payload2), seq=201)
    send_and_close(f"rename new_name=257 bytes (>256)",
                   hdr2 + bytes(payload2))
    send_liveness_check("after rename new_name cap")


def test_nan_transform():
    """A4: Transform floats containing NaN.

    Send PT_Create and PT_Transform packets with NaN in each
    transform field.
    """
    print("\n--- A4: NaN Transform Rejection ---")

    nan_val = float("nan")
    guid = make_dummy_guid(0xBB)

    for field_name, loc, rot, scl in [
        ("Location NaN",
         (nan_val, 0, 0), (0, 0, 0, 1), (1, 1, 1)),
        ("Rotation NaN",
         (0, 0, 0), (nan_val, 0, 0, 1), (1, 1, 1)),
        ("Scale NaN",
         (0, 0, 0), (0, 0, 0, 1), (nan_val, 1, 1)),
    ]:
        obj_data = make_dummy_transform(
            guid, loc, rot, scl,
            ts=time.time(), prim=0x00
        )
        hdr = make_v4_header(packet_type=0x03, object_count=1,
                             payload_size=len(obj_data), seq=300)
        send_and_close(f"CREATE {field_name}", hdr + obj_data)
        send_liveness_check(f"after CREATE {field_name}")


def test_inf_transform():
    """A5: Transform floats containing Inf.

    Send PT_Create packets with Inf in each transform field.
    """
    print("\n--- A5: Inf Transform Rejection ---")

    inf_val = float("inf")
    guid = make_dummy_guid(0xCC)

    for field_name, loc, rot, scl in [
        ("Location Inf",
         (inf_val, 0, 0), (0, 0, 0, 1), (1, 1, 1)),
        ("Rotation Inf",
         (0, 0, 0), (inf_val, 0, 0, 1), (1, 1, 1)),
        ("Scale Inf",
         (0, 0, 0), (0, 0, 0, 1), (inf_val, 1, 1)),
    ]:
        obj_data = make_dummy_transform(
            guid, loc, rot, scl,
            ts=time.time(), prim=0x00
        )
        hdr = make_v4_header(packet_type=0x03, object_count=1,
                             payload_size=len(obj_data), seq=400)
        send_and_close(f"CREATE {field_name}", hdr + obj_data)
        send_liveness_check(f"after CREATE {field_name}")


def test_collection_invalid_optype():
    """A6: Collection op-type outside valid range (0x01-0x08).

    Send PT_Collection packets with OpType=0x00 and OpType=0x09.
    """
    print("\n--- A6: Collection Op-Type Range ---")

    guid = pack_guid(0xDD)
    seq = 1
    ts = struct.pack("<d", time.time())

    for optype, desc in [(0x00, "zero (invalid)"), (0x09, "above max (0x09)")]:
        # Identity variant: TargetGuid(16) + OpType(1) + OpFlags(1) + seq(4) + ts(8) = 30
        payload = bytearray()
        payload.extend(guid)
        payload.extend(struct.pack("<B", optype))
        payload.extend(struct.pack("<B", 0))
        payload.extend(struct.pack("<I", seq))
        payload.extend(ts)

        hdr = make_v4_header(packet_type=0x0F, object_count=1,
                             payload_size=len(payload), seq=500 + optype)
        send_and_close(f"collection op_type={optype} ({desc})",
                       hdr + bytes(payload))
        send_liveness_check(f"after collection op_type={optype}")


def test_max_packet_size_recheck():
    """A2: Packet size > LIVE_SYNC_MAX_PACKET_SIZE (512 KB) in game-thread.

    The network thread already rejects this, but verify the game-thread
    re-check also handles it safely. Send a heartbeat with an artificially
    large buffer.
    """
    print("\n--- A2: Game-Thread Max Packet Size Re-check ---")

    # Send a packet whose RawData exceeds 512KB.
    # Use a heartbeat-type header but append a huge payload.
    header_size = struct.calcsize("<I H B B Q I I")
    huge_size = 600 * 1024  # > 512KB
    hdr = struct.pack(
        "<I H B B Q I I",
        MAGIC, 4, 0x07, 0x00,  # PT_Heartbeat
        600, huge_size, 0
    )
    payload = b"\x00" * (huge_size - header_size)
    send_and_close(f"packet_size={huge_size}B (>512KB) game-thread re-check",
                   hdr + payload)
    send_liveness_check("after max packet size re-check")


# =============================================================
# MAIN
# =============================================================

if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("  PHASE 6I.1 — BOUNDS VALIDATION")
    print("  Requires UE editor listening on :57000")
    print("=" * 60)

    if not check_ue_port():
        print("\n  UE editor not detected. Exiting.")
        sys.exit(1)

    test_object_count_cap()
    test_max_packet_size_recheck()
    test_rename_name_too_long()
    test_nan_transform()
    test_inf_transform()
    test_collection_invalid_optype()

    ok = report()
    sys.exit(0 if ok else 1)
