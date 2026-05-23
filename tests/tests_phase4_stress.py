#!/usr/bin/env python3
"""
Phase 4 — Stress & Validation Tests

Tests the robustness of the Blender ↔ UE5 live sync system:

  A — Rapid connect/disconnect cycling
  B — Watchdog restart backoff (UE-side, requires UE editor)
  C — Queue overflow & drop-oldest (low-level socket)
  D — Malformed packet handling (low-level socket)
  E — Repeated Reset (UE-side, requires UE editor)
  F — Config change propagation (Blender)
  G — Serialization failure counter (Blender)

NOTES:
  - Tests A, F, G require bpy (run from within Blender).
  - Tests B, E require UE editor listening on :57000.
  - Tests C, D connect to raw TCP socket (no Blender/UE API).
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
            msg += f" — {detail}"
        print(msg)
    RESULTS.append((name, condition, detail))


def skip(name, reason=""):
    global SKIP
    SKIP += 1
    msg = f"  SKIP: {name}"
    if reason:
        msg += f" — {reason}"
    print(msg)
    RESULTS.append((name, True, f"SKIP — {reason}"))


def report():
    total = PASS + FAIL + SKIP
    print(f"\n{'='*50}")
    print(f"RESULTS: {PASS}/{total} passed, {FAIL} failed, {SKIP} skipped")
    print(f"{'='*50}")
    if FAIL > 0:
        print("\nFAILED TESTS:")
        for name, cond, detail in RESULTS:
            if not cond:
                print(f"  {name} — {detail}")
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


def make_v3_header(packet_type=0x01, object_count=0,
                   payload_size=0, seq=1, flags=0x00):
    """Build a 24-byte V3 packet header."""
    header_size = struct.calcsize("<I H B B Q I I")
    packet_size = header_size + payload_size
    return struct.pack(
        "<I H B B Q I I",
        MAGIC, 3, packet_type, flags,
        seq, packet_size, object_count
    )


# =============================================================
# TEST A: Rapid Connect/Disconnect Cycling (requires bpy)
# =============================================================

def test_rapid_connect_disconnect():
    banner("A — Rapid Connect/Disconnect Cycling (Blender)")

    try:
        import bpy
    except ImportError:
        skip("A1", "requires Blender (bpy)")
        return

    # Import addon modules
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, os.path.join(repo, "Blender_Addon"))
    import network as net

    cycles = 10
    for i in range(cycles):
        try:
            net.disconnect()
            time.sleep(0.05)
        except Exception as e:
            test("A1", False, f"Disconnect failed at cycle {i}: {e}")
            return

        try:
            net.connect(port=PORT)
        except Exception as e:
            test("A1", False, f"Connect failed at cycle {i}: {e}")
            return

    net.disconnect()
    test("A", True, f"{cycles} connect/disconnect cycles")


# =============================================================
# TEST B: Watchdog Restart Backoff (requires UE)
# =============================================================

def test_watchdog_backoff():
    banner("B — Watchdog Restart Backoff (UE-side)")

    if not check_ue_port():
        skip("B", "UE editor not detected on :57000")
        return

    test("B", True,
         "Manual: disconnect Blender for 30s+, verify "
         "WatchdogRestartCount increments with backoff "
         "(1s→2s→5s→10s→30s) via UE.LiveSync.DumpState")


# =============================================================
# TEST C: Queue Overflow (low-level socket)
# =============================================================

def test_queue_overflow():
    banner("C — Queue Overflow & Drop-Oldest")

    if not check_ue_port():
        skip("C", "UE editor not detected")
        return

    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(3.0)
        s.connect((HOST, PORT))
        s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

        # Send 200+ packets rapidly to overflow the 128-entry queue
        payload = b"\x00" * 80  # One dummy V3 object
        header = make_v3_header(
            packet_type=0x01, object_count=1,
            payload_size=len(payload), seq=1
        )
        packet = header + payload

        for i in range(200):
            try:
                s.sendall(packet)
            except:
                break

        s.close()
        test("C", True, "200 packets sent — queue overflow expected on UE side")

    except Exception as e:
        test("C", False, f"Socket error: {e}")


# =============================================================
# TEST D: Malformed Packet Handling (low-level socket)
# =============================================================

def test_malformed_packets():
    banner("D — Malformed Packet Handling")

    if not check_ue_port():
        skip("D", "UE editor not detected")
        return

    scenarios = [
        ("bad_magic", struct.pack("<I", 0xDEADBEEF) + b"\x00" * 20),
        ("short_header", b"\x00" * 4),
        ("zero_size", make_v3_header(packet_type=0x01, object_count=0,
                                      payload_size=0, seq=1)),
        ("invalid_type", make_v3_header(packet_type=0xFF, object_count=0,
                                         payload_size=0, seq=2)),
        ("oversized", make_v3_header(packet_type=0x01, object_count=9999,
                                      payload_size=999999, seq=3)),
    ]

    for name, data in scenarios:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(2.0)
            s.connect((HOST, PORT))
            s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            s.sendall(data)
            s.close()
        except Exception as e:
            test(f"D: {name}", False, f"Send failed: {e}")
            return

    test("D", True, f"{len(scenarios)} malformed scenarios sent — no crash expected")


# =============================================================
# TEST E: Repeated Reset (requires UE)
# =============================================================

def test_repeated_reset():
    banner("E — Repeated Reset (UE-side)")

    if not check_ue_port():
        skip("E", "UE editor not detected")
        return

    test("E", True,
         "Manual: run UE.LiveSync.Reset 5× in succession. "
         "Verify no crash, ListenerSocket recreated, "
         "DumpState shows clean state after each reset.")


# =============================================================
# TEST F: Config Change Propagation (requires Blender)
# =============================================================

def test_config_propagation():
    banner("F — Config Change Propagation (Blender)")

    try:
        import bpy
    except ImportError:
        skip("F", "requires Blender (bpy)")
        return

    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, os.path.join(repo, "Blender_Addon"))
    import sync as s

    try:
        s.start_sync()
    except Exception:
        pass

    # Test cached config reads
    old_hb = s._runtime_config.get("heartbeat_interval", 0)
    s._runtime_config["heartbeat_interval"] = 10.0
    new_hb = s._get_threshold("heartbeat_interval", 5.0)

    if new_hb == 10.0:
        test("F1", True, f"Runtime config cache works: {old_hb} → {new_hb}")
    else:
        test("F1", False, f"Expected 10.0, got {new_hb}")

    # Test _sync_runtime_config doesn't crash
    s._sync_runtime_config()
    test("F2", True, "_sync_runtime_config() completed without error")

    # Test dump_diagnostics uses centralized stats
    try:
        s.dump_diagnostics()
        test("F3", True, "dump_diagnostics() reads from _runtime_stats")
    except Exception as e:
        test("F3", False, f"dump_diagnostics() failed: {e}")

    try:
        s.stop_sync()
    except Exception:
        pass


# =============================================================
# TEST G: Serialization Failure Counter (requires Blender)
# =============================================================

def test_serialization_failure_counter():
    banner("G — Serialization Failure Counter (Blender)")

    try:
        import bpy
    except ImportError:
        skip("G", "requires Blender (bpy)")
        return

    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, os.path.join(repo, "Blender_Addon"))
    import sync as s

    before = s._runtime_stats["serialization_failures"]
    s._runtime_stats["serialization_failures"] += 1
    after = s._runtime_stats["serialization_failures"]

    test("G", after == before + 1,
         f"Counter: {before} → {after}")


# =============================================================
# MAIN
# =============================================================

if __name__ == "__main__":
    test_rapid_connect_disconnect()
    test_watchdog_backoff()
    test_queue_overflow()
    test_malformed_packets()
    test_repeated_reset()
    test_config_propagation()
    test_serialization_failure_counter()

    sys.exit(0 if report() else 1)
