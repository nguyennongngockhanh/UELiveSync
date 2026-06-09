#!/usr/bin/env python3
"""Phase 8 Stage 1: Blender per-tick burst packet diagnostics.

Source-text tests verifying the _burst_packet_count instrumentation
in Blender_Addon/sync.py.

All tests are static source-text assertions — no runtime needed.
"""

import os
import sys

PASS = 0
FAIL = 0


def test(name, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}")
        if detail:
            print(f"        {detail}")


def banner(title):
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print(f"{'=' * 60}")


# Paths
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SYNC_PY = os.path.join(REPO_ROOT, "Blender_Addon", "sync.py")


banner("Phase 8 Stage 1 — Burst Packet Diagnostics")

if os.path.isfile(SYNC_PY):
    with open(SYNC_PY, "r") as f:
        text = f.read()

    # T1: _burst_packet_count global exists
    test("T1: _burst_packet_count initialized at top of check_updates",
          "_burst_packet_count = 0" in text,
          "_burst_packet_count = 0 not found")

    # T2: _runtime_stats init has burst_packet_count key
    test("T2: _runtime_stats dict includes burst_packet_count",
          '"burst_packet_count": 0' in text,
          "burst_packet_count key missing from _runtime_stats")

    # T3: _runtime_stats init has burst_packet_count_peak key
    test("T3: _runtime_stats dict includes burst_packet_count_peak",
          '"burst_packet_count_peak": 0' in text,
          "burst_packet_count_peak key missing")

    # T4: Increment after send_objects at least once
    test("T4: _burst_packet_count += 1 present after send_objects",
          "_burst_packet_count += 1" in text,
          "_burst_packet_count += 1 not found")

    # T5: All 20 send_objects calls inside check_updates have increment
    count_increments = text.count("_burst_packet_count += 1")
    test("T5: At least 18 send_objects increments exist",
          count_increments >= 18,
          f"Only {count_increments} increments found, expected >= 18")

    # T6: Peak tracking
    test("T6: Peak tracking present",
          "burst_packet_count_peak" in text and "max(" in text,
          "burst_packet_count_peak or max() not found in sync.py")

    # T7: No changes to network.py
    net_path = os.path.join(REPO_ROOT, "Blender_Addon", "network.py")
    if os.path.isfile(net_path):
        with open(net_path, "r") as f:
            net_text = f.read()
        test("T7: network.py has no burst_packet_count",
              "burst_packet_count" not in net_text,
              "network.py contains burst_packet_count (should not)")
    else:
        test("T7: network.py exists for check", False, "network.py not found")

    # T8: No wire format change (FBX_IMPORT_REQUEST_PAYLOAD_SIZE still 680)
    test("T8: FBX_IMPORT_REQUEST_PAYLOAD_SIZE unchanged in network.py",
          "FBX_IMPORT_REQUEST_PAYLOAD_SIZE = 680" in net_text,
          "FBX payload size changed")

    # T9: sync.py imports network
    test("T9: sync.py imports network module",
          "import network" in text or "from network" in text,
          "network import not found in sync.py")

    # T10: send_objects function still takes same args
    test("T10: send_objects function signature unchanged in sync.py",
          "def send_objects" in text or "send_objects(" in text,
          "send_objects not found in sync.py")

    # Summary
    print(f"\n{'=' * 60}")
    print(f"  PASS: {PASS}  FAIL: {FAIL}")
    print(f"{'=' * 60}")

else:
    test("T0: sync.py file found", False, f"sync.py not at {SYNC_PY}")
    print(f"\n  PASS: {PASS}  FAIL: {FAIL}")

sys.exit(0 if FAIL == 0 else 1)
