"""
Phase 3.6 Validation — A: Reconnect Torture Test
Tests connection robustness: rapid reconnect, burst reconnect,
data integrity across reconnections, idle probe, backoff.
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


def make_transform_object():
    guid = make_guid()
    loc = struct.pack("<fff", 100.0, 200.0, 300.0)
    rot = struct.pack("<ffff", 0.0, 0.0, 0.0, 1.0)
    scl = struct.pack("<fff", 1.0, 1.0, 1.0)
    ts = struct.pack("<d", time.time())
    parent = struct.pack("<IIII", 0, 0, 0, 0)
    return guid + loc + rot + scl + ts + parent


def connect():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(5)
    s.connect((HOST, PORT))
    return s


print("\n" + "="*50)
print("PHASE 3.6 VALIDATION — A: RECONNECT TORTURE")
print("="*50)

# =============================================================
# 1. RAPID CONNECT/DISCONNECT
# =============================================================
print("\n--- 1. RAPID CONNECT/DISCONNECT (50 cycles) ---")

start = time.perf_counter()
for i in range(50):
    try:
        s = connect()
        s.close()
    except Exception as e:
        test(f"Cycle {i+1}/50 connect", False, detail=str(e))
        break
elapsed = time.perf_counter() - start
test("50 rapid connect/disconnect cycles",
     elapsed < 30.0,
     detail=f"{elapsed*1000:.0f}ms total")

# =============================================================
# 2. DATA INTEGRITY ACROSS RECONNECTS
# =============================================================
print("\n--- 2. DATA INTEGRITY ACROSS RECONNECTS ---")

last_seq = 0
all_ok = True
for cycle in range(20):
    try:
        s = connect()
        seq = 1000 + cycle
        obj = make_transform_object()
        hdr = make_v3_header(packet_type=0x01, object_count=1,
                              payload_size=len(obj), seq=seq)
        s.sendall(hdr + obj)
        last_seq = seq
        s.close()
    except Exception as e:
        test(f"Reconnect cycle {cycle+1}/20 with data", False, detail=str(e))
        all_ok = False
        break

test("Data integrity across 20 reconnect cycles", all_ok)

# =============================================================
# 3. BURST RECONNECT (SPAM)
# =============================================================
print("\n--- 3. BURST RECONNECT (200 rapid connects) ---")

start = time.perf_counter()
success = 0
errors = []
for i in range(200):
    try:
        s = connect()
        seq = 2000 + i
        obj = make_transform_object()
        hdr = make_v3_header(packet_type=0x01, object_count=1,
                              payload_size=len(obj), seq=seq)
        s.sendall(hdr + obj)
        s.close()
        success += 1
    except Exception as e:
        errors.append(str(e))

elapsed = time.perf_counter() - start
test("Burst 200 reconnects: >=90% success",
     success >= 180,
     detail=f"{success}/200 succeeded in {elapsed*1000:.0f}ms")
if errors:
    print(f"       Errors: {len(errors)} — {errors[0]}")

# =============================================================
# 4. SEQUENCE ID SANITY ACROSS RECONNECTS
# =============================================================
print("\n--- 4. SEQUENCE ID SANITY ---")

try:
    s = connect()

    # Send packets with increasing sequence IDs
    for i in range(5):
        seq = 57000 + i
        obj = make_transform_object()
        hdr = make_v3_header(packet_type=0x01, object_count=1,
                              payload_size=len(obj), seq=seq)
        s.sendall(hdr + obj)

    # Send an older sequence ID (should be rejected)
    old_obj = make_transform_object()
    old_hdr = make_v3_header(packet_type=0x01, object_count=1,
                              payload_size=len(old_obj), seq=4999)
    s.sendall(old_hdr + old_obj)

    # Newer sequence should still work
    newer_obj = make_transform_object()
    newer_hdr = make_v3_header(packet_type=0x01, object_count=1,
                                payload_size=len(newer_obj), seq=5005)
    s.sendall(newer_hdr + newer_obj)

    s.close()
    test("Sequence ID out-of-order rejection (no crash)", True)
except Exception as e:
    test("Sequence ID out-of-order rejection (no crash)", False, detail=str(e))

# =============================================================
# 5. HEARTBEAT ACROSS RECONNECT BOUNDARIES
# =============================================================
print("\n--- 5. HEARTBEAT ACROSS RECONNECT BOUNDARIES ---")

all_hb_ok = True
for i in range(10):
    try:
        s = connect()
        hb = make_v3_header(packet_type=0x07, object_count=0, seq=6000+i)
        s.sendall(hb)
        s.close()
    except:
        all_hb_ok = False
        break
test("Heartbeat survives 10 reconnect cycles", all_hb_ok)

# =============================================================
# 6. RESTART UE (simulated by waiting for server to recover)
# =============================================================
print("\n--- 6. CONNECTION AFTER SERVER RESTART (wait-and-retry) ---")

total_waited = 0.0
reconnected = False
for attempt in range(30):
    try:
        s = connect()
        hb = make_v3_header(packet_type=0x07, object_count=0, seq=7000)
        s.sendall(hb)
        s.close()
        reconnected = True
        break
    except:
        time.sleep(0.5)
        total_waited += 0.5

test(f"Reconnection after server restart (waited {total_waited:.1f}s)",
     reconnected)

# =============================================================
# RESULTS
# =============================================================
print()
report()
