#!/usr/bin/env python3
"""
Phase 6I.1 — Transport Hardening: Lifecycle Validation (Stage 2)

Verifies that lifecycle hardening changes do not cause regressions:
  C1. Socket receive timeout configured on new connections
  C2. TCP keepalive enabled on new connections
  C3. Send queue drained on reconnect (Blender-side)
  C4. StartNetworkThread double-accept guard with atomic exchange

Test approach:
  Perform basic connect/disconnect/reconnect sequences and verify
  the editor remains healthy. Since there is no return channel,
  we verify "no crash" and rely on UE log for detailed confirmation.

Manual verification:
  - Check UE log for "SetReceiveTimeout" or recv-timeout-related messages
  - Check UE log for keepalive configuration
  - Check UE log for "StartNetworkThread: already starting, rejected"
  - Check Blender console for "Drained N stale packet(s) from send queue"
"""

import socket
import struct
import time
import sys
import os

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


def make_v4_header(packet_type=0x01, object_count=0,
                   payload_size=0, seq=1, flags=0x00):
    header_size = struct.calcsize("<I H B B Q I I")
    packet_size = header_size + payload_size
    return struct.pack(
        "<I H B B Q I I",
        MAGIC, 4, packet_type, flags,
        seq, packet_size, object_count
    )


def send_and_close(desc, data):
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(3.0)
        s.connect((HOST, PORT))
        s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        s.sendall(data)
        time.sleep(0.1)
        s.close()
        test(desc, True, f"sent {len(data)} bytes, no crash")
    except Exception as e:
        test(desc, False, str(e))


def send_liveness_check(desc):
    hdr = make_v4_header(packet_type=0x07, object_count=0,
                         payload_size=0, seq=9999)
    send_and_close(f"liveness ({desc})", hdr)


# =============================================================
# TESTS
# =============================================================

def test_connect_send_disconnect():
    """Basic connect → send heartbeat → disconnect cycle.

    Verifies the socket lifecycle path works correctly with
    the new SetReceiveTimeout and SetKeepAlive calls.
    """
    print("\n--- Connect/Send/Disconnect ---")
    for i in range(5):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(3.0)
        try:
            s.connect((HOST, PORT))
            s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            hdr = make_v4_header(packet_type=0x07, object_count=0,
                                 payload_size=0, seq=5000 + i)
            s.sendall(hdr)
            time.sleep(0.05)
            s.close()
            test(f"connect/send/disconnect cycle {i+1}", True)
        except Exception as e:
            test(f"connect/send/disconnect cycle {i+1}", False, str(e))

    send_liveness_check("after connect/disconnect cycles")


def test_rapid_reconnect():
    """Rapid reconnect storm — verify thread lifecycle handles it.

    Create and close 10 connections in quick succession.
    """
    print("\n--- Rapid Reconnect ---")
    for i in range(10):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(1.0)
        try:
            s.connect((HOST, PORT))
            s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            # Send a few packets
            for j in range(3):
                hdr = make_v4_header(
                    packet_type=0x07, object_count=0,
                    payload_size=0, seq=6000 + i * 10 + j)
                s.sendall(hdr)
                time.sleep(0.01)
            s.close()
            test(f"rapid reconnect cycle {i+1}", True)
        except Exception as e:
            test(f"rapid reconnect cycle {i+1}", False, str(e))
        time.sleep(0.05)

    send_liveness_check("after rapid reconnect")


def test_concurrent_connection():
    """Open two connections simultaneously.

    The second connection should be accepted while the first is
    still active, exercising the StartNetworkThread double-start
    guard.
    """
    print("\n--- Concurrent Connection ---")
    s1 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s1.settimeout(3.0)
    try:
        s1.connect((HOST, PORT))
        s1.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        test("first connection established", True)
    except Exception as e:
        test("first connection established", False, str(e))
        return

    time.sleep(0.2)

    s2 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s2.settimeout(3.0)
    try:
        s2.connect((HOST, PORT))
        s2.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        test("second connection accepted while first active", True)
    except Exception as e:
        test("second connection accepted while first active", False, str(e))

    # Send on both
    for s, label in [(s1, "first"), (s2, "second")]:
        try:
            hdr = make_v4_header(packet_type=0x07, object_count=0,
                                 payload_size=0, seq=7000)
            s.sendall(hdr)
            test(f"heartbeat on {label} connection", True)
        except Exception as e:
            test(f"heartbeat on {label} connection", False, str(e))

    s1.close()
    time.sleep(0.1)
    s2.close()

    send_liveness_check("after concurrent connections")


# =============================================================
# MAIN
# =============================================================

if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("  PHASE 6I.1 — LIFECYCLE VALIDATION (Stage 2)")
    print("  Requires UE editor listening on :57000")
    print("=" * 60)

    if not check_ue_port():
        print("\n  UE editor not detected. Exiting.")
        sys.exit(1)

    test_connect_send_disconnect()
    test_rapid_reconnect()
    test_concurrent_connection()

    print("\n  --- Manual verification ---")
    print("  Check UE log: 'SetReceiveTimeout' or recv-timeout messages")
    print("  Check UE log: keepalive configuration")
    print("  Check UE log: 'StartNetworkThread: already starting, rejected'")
    print("  Check Blender: 'Drained N stale packet(s)'")

    ok = report()
    sys.exit(0 if ok else 1)
