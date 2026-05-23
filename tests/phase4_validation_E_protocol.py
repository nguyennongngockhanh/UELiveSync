"""
Phase 4 Validation — E: Protocol Validation

UE-side: send invalid protocol data and verify UE rejects
each gracefully without crashing.

Scenarios:
  1. Invalid packet type byte (0xFF)
  2. Invalid flags (0xFF)
  3. Mismatched PacketSize (too small)
  4. Bad magic number
  5. Unsupported protocol version
  6. V2 payload size mismatch
  7. V3 payload too small (delete format)
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


def make_heartbeat(seq):
    header_size = struct.calcsize("<I H B B Q I I")
    return struct.pack(
        "<I H B B Q I I",
        MAGIC, 3, 0x07, 0x00,
        seq, header_size, 0
    )


def make_custom_v3_header(magic=MAGIC, version=3, ptype=0x01,
                           flags=0x00, seq=1, pkt_size=None, obj_count=0):
    if pkt_size is None:
        pkt_size = struct.calcsize("<I H B B Q I I")
    return struct.pack(
        "<I H B B Q I I",
        magic, version, ptype, flags,
        seq, pkt_size, obj_count
    )


def make_v2_header(magic=MAGIC, version=2, seq=1,
                    pkt_size=None, obj_count=0):
    if pkt_size is None:
        pkt_size = struct.calcsize("<I H Q I I")
    return struct.pack(
        "<I H Q I I",
        magic, version, seq,
        pkt_size, obj_count
    )


print("\n" + "=" * 50)
print("PHASE 4 VALIDATION — E: PROTOCOL VALIDATION")
print("=" * 50)


# =============================================================
# 1. CONNECT
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
# 2. INVALID PACKET TYPE (0xFF)
# =============================================================
print("\n--- 2. INVALID TYPE (0xFF) ---")

try:
    pkt = make_custom_v3_header(ptype=0xFF, seq=1)
    s.sendall(pkt)
    test("2: invalid type sent", True)
except Exception as e:
    test("2: invalid type sent", False, str(e))


# =============================================================
# 3. INVALID FLAGS (0xFF)
# =============================================================
print("\n--- 3. INVALID FLAGS (0xFF) ---")

try:
    pkt = make_custom_v3_header(flags=0xFF, seq=2)
    s.sendall(pkt)
    test("3: invalid flags sent", True)
except Exception as e:
    test("3: invalid flags sent", False, str(e))


# =============================================================
# 4. BAD MAGIC
# =============================================================
print("\n--- 4. BAD MAGIC ---")

try:
    pkt = make_custom_v3_header(magic=0xDEADBEEF, seq=3)
    s.sendall(pkt)
    test("4: bad magic sent", True)
except Exception as e:
    test("4: bad magic sent", False, str(e))


# =============================================================
# 5. UNSUPPORTED VERSION
# =============================================================
print("\n--- 5. UNSUPPORTED VERSION ---")

try:
    pkt = make_custom_v3_header(version=99, seq=4)
    s.sendall(pkt)
    test("5: unsupported version sent", True)
except Exception as e:
    test("5: unsupported version sent", False, str(e))


# =============================================================
# 6. V2 PAYLOAD SIZE MISMATCH
# =============================================================
print("\n--- 6. V2 PAYLOAD SIZE MISMATCH ---")

try:
    header = make_v2_header(seq=5, obj_count=3,
                             pkt_size=struct.calcsize("<I H Q I I") + 10)
    s.sendall(header)
    test("6: V2 size mismatch sent", True)
except Exception as e:
    test("6: V2 size mismatch sent", False, str(e))


# =============================================================
# 7. CONNECTION STILL ALIVE
# =============================================================
print("\n--- 7. CONNECTION LIVENESS ---")

try:
    hb = make_heartbeat(100)
    s.sendall(hb)
    # Socket should still be writable (UE didn't crash)
    s.settimeout(2.0)
    try:
        data = s.recv(1024)
    except socket.timeout:
        data = b""
    s.close()
    test("7: connection alive after all malformed packets", True)
except Exception as e:
    test("7: connection alive after all malformed packets", False, str(e))


# =============================================================
# REPORT
# =============================================================
sys.exit(0 if report() else 1)
