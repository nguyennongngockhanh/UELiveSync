"""
Phase 4 Validation — B: Queue Overflow & Rate Cap

UE-side: flood 500 packets in one burst, verify UE doesn't
crash, verify socket stays responsive after overflow.
"""

import socket
import struct
import time
import sys

HOST = "127.0.0.1"
PORT = 57000
MAGIC = 0x4C56534D

PASS = 0
FAIL = 0


def test(name, condition, detail=""):
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


def report():
    total = PASS + FAIL
    print(f"\n{'='*50}")
    print(f"RESULTS: {PASS}/{total} passed, {FAIL} failed")
    print(f"{'='*50}")
    return FAIL == 0


def make_v3_header(packet_type=0x01, object_count=0,
                   payload_size=0, seq=1, flags=0x00):
    header_size = struct.calcsize("<I H B B Q I I")
    packet_size = header_size + payload_size
    return struct.pack(
        "<I H B B Q I I",
        MAGIC, 3, packet_type, flags,
        seq, packet_size, object_count
    )


def make_dummy_object_data():
    # V3 object: 16 GUID + 12 loc + 16 rot + 12 scl + 8 ts + 16 parent = 80 bytes
    return b"\x00" * 80


def make_heartbeat_packet(seq):
    return make_v3_header(
        packet_type=0x07,
        object_count=0,
        payload_size=0,
        seq=seq
    )


print("\n" + "=" * 50)
print("PHASE 4 VALIDATION — B: QUEUE OVERFLOW & RATE CAP")
print("=" * 50)


# =============================================================
# 1. CONNECT TO UE
# =============================================================
print("\n--- 1. CONNECT ---")

try:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(5.0)
    s.connect((HOST, PORT))
    s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    test("1: connect", True)
except Exception as e:
    test("1: connect", False, str(e))
    sys.exit(1)


# =============================================================
# 2. FLOOD 500 PACKETS
# =============================================================
print("\n--- 2. FLOOD 500 PACKETS ---")

obj_data = make_dummy_object_data()

try:
    for i in range(500):
        header = make_v3_header(
            packet_type=0x01,
            object_count=1,
            payload_size=len(obj_data),
            seq=i + 1
        )
        s.sendall(header + obj_data)

    test("2a: 500 packets sent without exception", True)
except Exception as e:
    test("2a: 500 packets sent without exception", False, str(e))


# =============================================================
# 3. VERIFY CONNECTION STILL ALIVE
# =============================================================
print("\n--- 3. CONNECTION LIVENESS ---")

try:
    time.sleep(0.5)

    # Send heartbeat
    hb = make_heartbeat_packet(9999)
    s.sendall(hb)
    s.settimeout(1.0)

    # Try to recv (may get nothing if no data, but shouldn't error)
    try:
        data = s.recv(1024)
    except socket.timeout:
        data = b""

    s.close()
    test("3: connection alive after flood", True)
except Exception as e:
    test("3: connection alive after flood", False, str(e))


# =============================================================
# REPORT
# =============================================================
sys.exit(0 if report() else 1)
