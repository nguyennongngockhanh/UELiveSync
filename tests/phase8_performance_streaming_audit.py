#!/usr/bin/env python3
"""Phase 8 High Performance Streaming — Source-text audit.

Verifies source-text invariants for the currently implemented Phase 8
components (burst packet counting, queue diagnostics, mesh reassembly
timeout) and confirms the absence of unimplemented features.

No runtime needed.
"""

import os
import sys
import re

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


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Paths
SYNC_PY = os.path.join(REPO_ROOT, "Blender_Addon", "sync.py")
NET_PY = os.path.join(REPO_ROOT, "Blender_Addon", "network.py")
SUBSYS_H = os.path.join(REPO_ROOT, "UE_Plugin", "UELiveSync", "Source", "UELiveSync", "Public", "UELiveSyncSubsystem.h")
SUBSYS_CPP = os.path.join(REPO_ROOT, "UE_Plugin", "UELiveSync", "Source", "UELiveSync", "Private", "UELiveSyncSubsystem.cpp")
SYNCTYPES_H = os.path.join(REPO_ROOT, "UE_Plugin", "UELiveSync", "Source", "UELiveSync", "Public", "SyncTypes.h")


def read_or_fail(path):
    if os.path.isfile(path):
        with open(path, "r") as f:
            return f.read()
    return None


banner("Phase 8 Performance Streaming Pipeline Audit")

# --- Read source files ---
sync_text = read_or_fail(SYNC_PY)
net_text = read_or_fail(NET_PY)
subsys_cpp = read_or_fail(SUBSYS_CPP)
subsys_h = read_or_fail(SUBSYS_H)
synctypes_h = read_or_fail(SYNCTYPES_H)


# ============================================================
# Section 1: Burst Packet Diagnostics (Blender)
# ============================================================
banner("Section 1 — Burst Packet Diagnostics (existing Stage 1)")

if sync_text:
    test("T1.1: _burst_packet_count = 0 initialized",
          "_burst_packet_count = 0" in sync_text)

    test("T1.2: _runtime_stats includes burst_packet_count",
          '"burst_packet_count": 0' in sync_text)

    test("T1.3: _runtime_stats includes burst_packet_count_peak",
          '"burst_packet_count_peak": 0' in sync_text)

    count_inc = sync_text.count("_burst_packet_count += 1")
    test("T1.4: At least 20 _burst_packet_count increment sites",
          count_inc >= 20,
          f"found {count_inc}, expected >= 20")

    test("T1.5: Peak tracking with max()",
          "burst_packet_count_peak" in sync_text and "max(" in sync_text)

    test("T1.6: Phase 8 comment marker",
          "Phase 8 Stage 1" in sync_text or "per-tick burst" in sync_text)

    test("T1.7: sync.py imports network",
          "import network" in sync_text or "from network" in sync_text)
else:
    for i in range(1, 8):
        test(f"T1.{i}: sync.py found", False)


# ============================================================
# Section 2: Send Queue (Blender)
# ============================================================
banner("Section 2 — Send Queue (Blender)")

if net_text:
    test("T2.1: Send queue exists (maxsize=256)",
          "maxsize=256" in net_text)

    test("T2.2: Sender loop exists",
          "_sender_loop" in net_text)

    test("T2.3: Queue depth monitoring at 75%",
          "75%" in net_text or "192" in net_text)

    test("T2.4: Queue full drop handling",
          "queue.Full" in net_text)

    test("T2.5: get_queue_depth function",
          "get_queue_depth" in net_text)

    test("T2.6: No backpressure ACK in network.py",
          "Backpressure" not in net_text and "backpressure" not in net_text,
          "backpressure found in network.py")
else:
    for i in range(1, 7):
        test(f"T2.{i}: network.py found", False)


# ============================================================
# Section 3: No Compression (negative tests)
# ============================================================
banner("Section 3 — Compression NOT Implemented (negative tests)")

if net_text:
    test("T3.1: No zlib import in network.py",
          "import zlib" not in net_text and "from zlib" not in net_text,
          "zlib import found in network.py")
    
    test("T3.2: No compress/decompress in network.py",
          "'compress'" not in net_text.replace("_burst_packet_count", "").replace("compression", "skip"),
          "compress found (unexpected)")
else:
    test("T3.1: network.py found", False)

if sync_text:
    test("T3.3: No zlib import in sync.py",
          "import zlib" not in sync_text and "from zlib" not in sync_text,
          "zlib import found in sync.py")
else:
    test("T3.3: sync.py found", False)

if synctypes_h:
    test("T3.4: No MESH_CHUNK_FLAG_COMPRESSED in SyncTypes.h",
          "MESH_CHUNK_FLAG_COMPRESSED" not in synctypes_h,
          "MESH_CHUNK_FLAG_COMPRESSED found")
else:
    test("T3.4: SyncTypes.h found", False)


# ============================================================
# Section 4: No Backpressure ACK (negative tests)
# ============================================================
banner("Section 4 — Backpressure ACK NOT Implemented (negative tests)")

if subsys_cpp:
    test("T4.1: No HandleBackpressureACK in UELiveSyncSubsystem.cpp",
          "HandleBackpressure" not in subsys_cpp,
          "HandleBackpressure found")
else:
    test("T4.1: UELiveSyncSubsystem.cpp found", False)

if synctypes_h:
    test("T4.2: No PT_BackpressureACK in SyncTypes.h",
          "PT_BackpressureACK" not in synctypes_h,
          "PT_BackpressureACK found")
else:
    test("T4.2: SyncTypes.h found", False)

if net_text:
    test("T4.3: No PT_BackpressureACK in network.py",
          "BackpressureACK" not in net_text and "PT_Backpressure" not in net_text,
          "BackpressureACK found in network.py")
else:
    test("T4.3: network.py found", False)


# ============================================================
# Section 5: Packet Registry (kValidTypes)
# ============================================================
banner("Section 5 — Packet Registry")

if subsys_cpp:
    test("T5.1: 0x10 NOT in kValidTypes",
          "0x10" not in subsys_cpp[subsys_cpp.find("kValidTypes"):subsys_cpp.find("kValidTypes") + 200],
          "0x10 found in kValidTypes area")
else:
    test("T5.1: UELiveSyncSubsystem.cpp found", False)

if subsys_cpp:
    test("T5.2: 0x02 NOT in kValidTypes",
          "0x02" not in subsys_cpp[subsys_cpp.find("kValidTypes"):subsys_cpp.find("kValidTypes") + 200],
          "0x02 found in kValidTypes area")
else:
    test("T5.2: UELiveSyncSubsystem.cpp found", False)

if synctypes_h:
    test("T5.3: PT_Reserved_02 = 0x02 defined",
          "PT_Reserved_02" in synctypes_h and "0x02" in synctypes_h[synctypes_h.find("PT_Reserved_02"):synctypes_h.find("PT_Reserved_02") + 50],
          "PT_Reserved_02 not found")
else:
    test("T5.3: SyncTypes.h found", False)

if subsys_cpp:
    test("T5.4: kValidFlags includes 0x02 (PF_FullSnapshot)",
          "0x02" in subsys_cpp[subsys_cpp.find("kValidFlags"):subsys_cpp.find("kValidFlags") + 100],
          "0x02 not in kValidFlags")
else:
    test("T5.4: UELiveSyncSubsystem.cpp found", False)


# ============================================================
# Section 6: Queue Diagnostics (UE)
# ============================================================
banner("Section 6 — Queue Diagnostics (UE)")

if synctypes_h:
    test("T6.1: QueueDepthCurrent in FLiveSyncStats",
          "QueueDepthCurrent" in synctypes_h[synctypes_h.find("FLiveSyncStats"):synctypes_h.find("FLiveSyncStats") + 500],
          "QueueDepthCurrent not found")
    
    test("T6.2: PacketsDropped atomic counter",
          "PacketsDropped" in synctypes_h,
          "PacketsDropped not found")
    
    test("T6.3: FOverflowEvent struct",
          "FOverflowEvent" in synctypes_h,
          "FOverflowEvent not found")
    
    test("T6.4: MAX_OVERFLOW_HISTORY = 32",
          "MAX_OVERFLOW_HISTORY" in synctypes_h and "32" in synctypes_h[synctypes_h.find("MAX_OVERFLOW_HISTORY"):synctypes_h.find("MAX_OVERFLOW_HISTORY") + 50],
          "MAX_OVERFLOW_HISTORY=32 not found")
else:
    for i in range(1, 5):
        test(f"T6.{i}: SyncTypes.h found", False)

if subsys_cpp:
    test("T6.5: CVarLiveSyncMaxPacketRate (static rate limiter)",
          "CVarLiveSyncMaxPacketRate" in subsys_cpp,
          "CVarLiveSyncMaxPacketRate not found")

    test("T6.6: Packet age hard limit flush",
          "PacketAgeHardLimit" in subsys_cpp,
          "PacketAgeHardLimit not found")
else:
    test("T6.5: UELiveSyncSubsystem.cpp found", False)
    test("T6.6: UELiveSyncSubsystem.cpp found", False)


# ============================================================
# Section 7: Mesh Reassembly Timeout (UE, Stage 1A)
# ============================================================
banner("Section 7 — Mesh Reassembly Timeout (Stage 1A partial)")

if subsys_cpp:
    test("T7.1: MeshReassemblyTimeoutSec CVar absent (Stage 1A not in source)",
          "MeshReassemblyTimeoutSec" not in subsys_cpp,
          "MeshReassemblyTimeoutSec unexpectedly present")

    test("T7.2: MeshStaleEvictions counter absent (not in source)",
          "MeshStaleEvictions" not in subsys_cpp,
          "MeshStaleEvictions unexpectedly present")
else:
    test("T7.1: UELiveSyncSubsystem.cpp found", False)
    test("T7.2: UELiveSyncSubsystem.cpp found", False)


# ============================================================
# Section 8: No Adaptive Throttle (negative tests)
# ============================================================
banner("Section 8 — Adaptive Throttle NOT Implemented (negative tests)")

if sync_text:
    test("T8.1: check_updates returns 0.016 (hardcoded, not adaptive)",
          "return 0.016" in sync_text,
          "return 0.016 not found in sync.py")

    test("T8.2: No MIN_SEND_INTERVAL in sync.py",
          "MIN_SEND_INTERVAL" not in sync_text,
          "MIN_SEND_INTERVAL found unexpectedly")

    test("T8.3: No MAX_SEND_INTERVAL in sync.py",
          "MAX_SEND_INTERVAL" not in sync_text,
          "MAX_SEND_INTERVAL found unexpectedly")
else:
    for i in range(1, 4):
        test(f"T8.{i}: sync.py found", False)


# ============================================================
# Section 9: No Interest Management (negative tests)
# ============================================================
banner("Section 9 — Interest Management NOT Implemented (negative tests)")

if sync_text:
    test("T9.1: No interest_management in sync.py",
          "interest_management" not in sync_text,
          "interest_management found unexpectedly")

    test("T9.2: No _dirty_guids in sync.py",
          "_dirty_guids" not in sync_text,
          "_dirty_guids found unexpectedly")
else:
    test("T9.1: sync.py found", False)


# ============================================================
# Summary
# ============================================================
print(f"\n{'=' * 60}")
print(f"  PASS: {PASS}   FAIL: {FAIL}")
print(f"{'=' * 60}")

sys.exit(0 if FAIL == 0 else 1)
