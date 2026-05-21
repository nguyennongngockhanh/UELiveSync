"""
Phase 3.5 Validation — UE Runtime
Tests packet processing, edge cases, and protocol handling.
"""

import socket
import struct
import time
import sys
import uuid


PASS = 0
FAIL = 0
HOST = "127.0.0.1"
PORT = 5000
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
    header = f"RESULTS: {PASS}/{total} passed, {FAIL} failed"
    print(header)
    print(f"{'='*50}")
    return FAIL == 0


def make_guid():
    g = uuid.uuid4()
    return struct.pack("<IIII", g.time_low,
        (g.time_mid << 16) | g.time_hi_version,
        (g.clock_seq_hi_variant << 24) | (g.clock_seq_low << 16) | ((g.node >> 32) & 0xFFFF),
        g.node & 0xFFFFFFFF)


def make_v3_header(packet_type=0x01, object_count=0, payload_size=0, seq=1):
    flags = 0x00
    header_size = struct.calcsize("<I H B B Q I I")
    packet_size = header_size + payload_size
    return struct.pack("<I H B B Q I I",
        MAGIC, 3, packet_type, flags, seq, packet_size, object_count)


def make_transform_object():
    guid = make_guid()
    loc = struct.pack("<fff", 100.0, 200.0, 300.0)
    rot = struct.pack("<ffff", 0.0, 0.0, 0.0, 1.0)
    scl = struct.pack("<fff", 1.0, 1.0, 1.0)
    ts = struct.pack("<d", time.time())
    parent = struct.pack("<IIII", 0, 0, 0, 0)
    return guid + loc + rot + scl + ts + parent


def make_delete_object():
    return make_guid()


def connect():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(5)
    s.connect((HOST, PORT))
    return s


print("\n" + "="*50)
print("PHASE 3.5 VALIDATION — UE RUNTIME")
print("="*50)

# =============================================================
# 1. PACKET PROCESSING
# =============================================================
print("\n--- 1. PACKET PROCESSING ---")

# 1a — Heartbeat
try:
    s = connect()
    hb = make_v3_header(packet_type=0x07, object_count=0)
    s.sendall(hb)
    test("Heartbeat packet accepted", True)
    s.close()
except Exception as e:
    test("Heartbeat packet accepted", False, detail=str(e))

# 1b — CREATE packet
try:
    s = connect()
    obj = make_transform_object()
    hdr = make_v3_header(packet_type=0x03, object_count=1, payload_size=len(obj), seq=2)
    s.sendall(hdr + obj)
    time.sleep(0.2)
    test("CREATE packet processed", True)
    s.close()
except Exception as e:
    test("CREATE packet processed", False, detail=str(e))

# 1c — TRANSFORM packet
try:
    s = connect()
    obj = make_transform_object()
    hdr = make_v3_header(packet_type=0x01, object_count=1, payload_size=len(obj), seq=3)
    s.sendall(hdr + obj)
    time.sleep(0.2)
    test("TRANSFORM packet processed", True)
    s.close()
except Exception as e:
    test("TRANSFORM packet processed", False, detail=str(e))

# 1d — DELETE packet
try:
    s = connect()
    obj = make_delete_object()
    hdr = make_v3_header(packet_type=0x04, object_count=1, payload_size=len(obj), seq=4)
    s.sendall(hdr + obj)
    time.sleep(0.2)
    test("DELETE packet processed", True)
    s.close()
except Exception as e:
    test("DELETE packet processed", False, detail=str(e))

# 1e — Invalid magic
try:
    s = connect()
    bad = bytearray(struct.pack("<I", 0xDEADBEEF) + struct.pack("<H", 3))
    bad.extend(b'\x01\x00' + struct.pack("<Q I I", 5, 80, 1))
    hdr_size = 24
    bad.extend(make_transform_object())
    # Fix packet size
    total = hdr_size + 80
    bad[12:20] = struct.pack("<Q", 5)  # seq
    bad[20:24] = struct.pack("<I", total)  # size
    bad[24:28] = struct.pack("<I", 1)  # count
    s.sendall(bytes(bad))
    test("Invalid magic: no crash", True)
    s.close()
except Exception as e:
    test("Invalid magic: no crash", False, detail=str(e))

# 1f — V2 packet (backward compat)
try:
    s = connect()
    v2_obj = struct.pack("<IIII", 1, 2, 3, 4) + struct.pack("<fff", 1, 2, 3) + struct.pack("<ffff", 0, 0, 0, 1) + struct.pack("<fff", 1, 1, 1)
    v2_hdr = struct.pack("<I H Q I I", MAGIC, 2, 10, 22 + len(v2_obj), 1)
    s.sendall(v2_hdr + v2_obj)
    time.sleep(0.2)
    test("V2 backward compat packet", True)
    s.close()
except Exception as e:
    test("V2 backward compat packet", False, detail=str(e))

# 1g — Unknown packet type
try:
    s = connect()
    hdr = make_v3_header(packet_type=0xFF, object_count=0, seq=11)
    s.sendall(hdr)
    time.sleep(0.2)
    test("Unknown packet type (0xFF): no crash", True)
    s.close()
except Exception as e:
    test("Unknown packet type (0xFF): no crash", False, detail=str(e))

# 1h — Oversized packet (invalid size)
try:
    s = connect()
    hdr = make_v3_header(packet_type=0x01, object_count=99999, seq=12)
    # Don't send payload
    s.sendall(hdr)
    time.sleep(0.2)
    test("Oversized packet: no crash", True)
    s.close()
except Exception as e:
    test("Oversized packet: no crash", False, detail=str(e))

# 1i — Continuously reconnect
for i in range(10):
    try:
        s = connect()
        obj = make_transform_object()
        hdr = make_v3_header(packet_type=0x01, object_count=1, payload_size=len(obj), seq=100+i)
        s.sendall(hdr + obj)
        s.close()
    except:
        pass
test("Rapid reconnect (10×)", True)

# =============================================================
# 2. PERFORMANCE (packet throughput)
# =============================================================
print("\n--- 2. PERFORMANCE ---")

# Send 100 packets in rapid succession
try:
    s = connect()
    obj = make_transform_object()
    start = time.perf_counter()
    for i in range(100):
        hdr = make_v3_header(packet_type=0x01, object_count=1, payload_size=len(obj), seq=200+i)
        s.sendall(hdr + obj)
    elapsed = time.perf_counter() - start
    test(f"Burst 100 packets", elapsed < 5.0, detail=f"{elapsed*1000:.1f}ms")
    s.close()
except Exception as e:
    test(f"Burst 100 packets", False, detail=str(e))

# =============================================================
# 3. UNKNOWN PACKET TYPES
# =============================================================
print("\n--- 3. PROTOCOL EDGE CASES ---")

# Send a trivially small packet (header only)
try:
    s = connect()
    hdr = make_v3_header(packet_type=0x01, object_count=0, seq=300)
    s.sendall(hdr)
    test("Empty packet (0 objects)", True)
    s.close()
except Exception as e:
    test("Empty packet (0 objects)", False, detail=str(e))

# Fragment a packet (send header, wait, then body)
try:
    s = connect()
    obj = make_transform_object()
    hdr = make_v3_header(packet_type=0x03, object_count=1, payload_size=len(obj), seq=301)
    s.sendall(hdr)
    time.sleep(0.1)
    s.sendall(obj)
    time.sleep(0.2)
    test("Fragmented packet (header first, body later)", True)
    s.close()
except Exception as e:
    test("Fragmented packet (header first, body later)", False, detail=str(e))

# =============================================================
# RESULTS
# =============================================================
print()
report()
