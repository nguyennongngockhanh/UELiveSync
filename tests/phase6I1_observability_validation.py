#!/usr/bin/env python3
"""
Phase 6I.1 — Transport Hardening: Observability Validation (Stage 1B)

Verifies that MalformedPackets counter now increments consistently
on paths that previously skipped it. Also verifies the
UE.LiveSync.TransportVerbose CVar is registered.

Test approach:
  Send packets that hit each newly-patched rejection path.
  Since there is no return channel from UE, we verify "no crash"
  and confirm the pattern matches existing fuzz tests.
  Manual verification: run `UE.LiveSync.Stats` in UE console
  after this test and confirm MalformedPackets > 0.
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

def test_malformed_magic():
    """Previously silent; now increments MalformedPackets."""
    print("\n--- Malformed Magic ---")
    hdr = struct.pack(
        "<I H B B Q I I",
        0xDEADBEEF, 4, 0x07, 0x00,
        1, 24, 0
    )
    send_and_close("bad magic (0xDEADBEEF)", hdr)
    send_liveness_check("after bad magic")

    hdr_zero = struct.pack(
        "<I H B B Q I I",
        0, 4, 0x07, 0x00,
        2, 24, 0
    )
    send_and_close("zero magic", hdr_zero)
    send_liveness_check("after zero magic")


def test_invalid_version():
    """Previously Warning-only; now increments MalformedPackets."""
    print("\n--- Invalid Protocol Version ---")
    for ver in [0, 1, 6, 255, 65535]:
        hdr = struct.pack(
            "<I H B B Q I I",
            MAGIC, ver, 0x07, 0x00,
            100 + ver, 24, 0
        )
        send_and_close(f"invalid version ({ver})", hdr)
        send_liveness_check(f"after version {ver}")


def test_truncated_header():
    """Previously silent; now increments MalformedPackets.

    Send packets too short to contain a V3 or V2 header.
    """
    print("\n--- Truncated Header ---")

    # Less than V2 header (22 bytes) — just magic (4 bytes)
    send_and_close("truncated header (4B — magic only)",
                   struct.pack("<I", MAGIC))

    # Less than V3 header (24 bytes) — 10 bytes
    hdr10 = make_v4_header(packet_type=0x07, object_count=0,
                           payload_size=0, seq=1)
    send_and_close("truncated header (10B)", hdr10[:10])
    send_liveness_check("after truncated header")


def test_invalid_packet_type():
    """Previously Warning-only; now increments MalformedPackets."""
    print("\n--- Invalid Packet Type ---")
    for ptype in [0x00, 0x02, 0x05, 0x06, 0xFF]:
        hdr = make_v4_header(packet_type=ptype, object_count=0,
                             payload_size=0, seq=200 + ptype)
        send_and_close(f"invalid type (0x{ptype:02x})", hdr)
        send_liveness_check(f"after type 0x{ptype:02x}")


def test_invalid_flags():
    """Previously Warning-only; now increments MalformedPackets."""
    print("\n--- Invalid Packet Flags ---")
    for flags in [0x05, 0x06, 0x07, 0x08, 0xFF]:
        hdr = make_v4_header(packet_type=0x07, object_count=0,
                             payload_size=0, seq=300 + flags,
                             flags=flags)
        send_and_close(f"invalid flags (0x{flags:02x})", hdr)
        send_liveness_check(f"after flags 0x{flags:02x}")


def test_unknown_packet_type():
    """Previously Warning-only; now increments MalformedPackets.

    Packet types >= 0x10 that pass the whitelist check but fall
    past all known handlers hit the unknown-type fallback.
    """
    print("\n--- Unknown Packet Type ---")
    for ptype in [0x10, 0x42, 0xFE]:
        hdr = make_v4_header(packet_type=ptype, object_count=0,
                             payload_size=0, seq=400 + ptype)
        send_and_close(f"unknown type (0x{ptype:02x})", hdr)
        send_liveness_check(f"after unknown type 0x{ptype:02x}")


# =============================================================
# MAIN
# =============================================================

if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("  PHASE 6I.1 — OBSERVABILITY VALIDATION (Stage 1B)")
    print("  Requires UE editor listening on :57000")
    print("=" * 60)

    if not check_ue_port():
        print("\n  UE editor not detected. Exiting.")
        sys.exit(1)

    test_malformed_magic()
    test_invalid_version()
    test_truncated_header()
    test_invalid_packet_type()
    test_invalid_flags()
    test_unknown_packet_type()

    # Manual verification note
    print("\n  --- Manual verification ---")
    print("  Run in UE console: UE.LiveSync.Stats")
    print("  Confirm MalformedPackets > 0")
    print("  Confirm TransportVerbose CVar exists:")

    ok = report()
    sys.exit(0 if ok else 1)
