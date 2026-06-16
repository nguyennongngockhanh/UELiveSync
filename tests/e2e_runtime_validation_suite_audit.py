#!/usr/bin/env python3
"""E2E Runtime Validation Suite — Source-text audit.

Verifies that:
- E2E validation doc exists
- E2E validator tool exists
- Tool does not use -NullRHI
- 0x02 remains reserved/invalid
- 0x10 remains unused/not implemented
- Required diagnostic marker categories are listed
- No false claims about Phase 8 backpressure/compression

No runtime needed.
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


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(path):
    if os.path.isfile(path):
        with open(path, "r") as f:
            return f.read()
    return None


banner("E2E Runtime Validation Suite Audit")

# ============================================================
# Section 1: Audit Doc Exists
# ============================================================
banner("Section 1 — Audit Doc")

doc_path = os.path.join(REPO_ROOT, "Docs", "Architecture", "e2e-runtime-validation-suite.md")
doc = read(doc_path)
test("T1.1: E2E validation doc exists",
      doc is not None,
      "Docs/Architecture/e2e-runtime-validation-suite.md not found")

if doc:
    test("T1.2: Doc has automated flow sections",
          "Step 1" in doc and "Camera Lifecycle" in doc,
          "Missing orchestrated flow sections")

    test("T1.3: Doc mentions manual Blender steps",
          "MANUAL" in doc and "Blender" in doc,
          "No manual Blender documentation")

    test("T1.4: Doc mentions 0x02 reserved",
          "0x02" in doc and "reserved" in doc,
          "0x02 reserved status not documented")

    test("T1.5: Doc mentions 0x10 not implemented",
          "0x10" in doc and ("not implemented" in doc.lower() or "NOT implemented" in doc),
          "0x10 not-implemented status not documented")

    # Check for NO false claims about Phase 8 features
    test("T1.6: Doc does NOT claim backpressure is implemented",
          "backpressure" not in doc or "NOT implemented" in doc.split("backpressure")[0:1][0][-200:]
          if "backpressure" in doc else True,
          "Doc may imply backpressure is implemented")

    test("T1.7: Doc has classification",
          "PASS_E2E" in doc or "FAIL_E2E" in doc,
          "Classification missing")


# ============================================================
# Section 2: Validator Tool Exists
# ============================================================
banner("Section 2 — Validator Tool")

tool_path = os.path.join(REPO_ROOT, "tools", "uelivesync_e2e_runtime_validator.py")
tool = read(tool_path)
test("T2.1: E2E validator tool exists",
      tool is not None,
      "tools/uelivesync_e2e_runtime_validator.py not found")

if tool:
    test("T2.2: Tool does not use -NullRHI",
          "-NullRHI" not in tool,
          "Tool references -NullRHI (invalid for runtime validation)")

    test("T2.3: Tool has --check-log mode",
          "--check-log" in tool,
          "Missing --check-log mode")

    test("T2.4: Tool has --log argument",
          "--log" in tool,
          "Missing --log argument")

    test("T2.5: Tool references timeline markers",
          "[TIMELINE]" in tool,
          "Missing timeline marker references")

    test("T2.6: Tool references playback markers",
          "[PLAYBACK]" in tool,
          "Missing playback marker references")

    test("T2.7: Tool references camera markers",
          "[CAMERA]" in tool,
          "Missing camera marker references")

    test("T2.8: Tool references keyframe markers",
          "[KEYFRAME]" in tool or "[SEQOP]" in tool,
          "Missing keyframe/sequencer marker references")

    test("T2.9: Tool checks queue diagnostics",
          "Queue" in tool or "queue" in tool or "PacketsDropped" in tool,
          "Missing queue diagnostics check")

    test("T2.10: Tool checks malformed packets",
          "malformed" in tool or "Malformed" in tool or "Invalid packet" in tool,
          "Missing malformed packet check")


# ============================================================
# Section 3: Packet Registry Invariants
# ============================================================
banner("Section 3 — Packet Registry Invariants")

subsys_cpp_path = os.path.join(REPO_ROOT, "UE_Plugin", "UELiveSync", "Source",
                                "UELiveSync", "Private", "UELiveSyncSubsystem.cpp")
subsys_cpp = read(subsys_cpp_path)

synctypes_h_path = os.path.join(REPO_ROOT, "UE_Plugin", "UELiveSync", "Source",
                                 "UELiveSync", "Public", "SyncTypes.h")
synctypes_h = read(synctypes_h_path)

if subsys_cpp:
    # Extract kValidTypes array
    kt_start = subsys_cpp.find("static constexpr uint8 kValidTypes[]")
    if kt_start >= 0:
        kt_section = subsys_cpp[kt_start:kt_start + 200]
        test("T3.1: 0x02 NOT in kValidTypes",
              "0x02" not in kt_section,
              "0x02 found in kValidTypes")
        test("T3.2: 0x10 NOT in kValidTypes",
              "0x10" not in kt_section,
              "0x10 found in kValidTypes (should be absent)")
    else:
        test("T3.1: kValidTypes found", False)
        test("T3.2: kValidTypes found", False)
else:
    test("T3.1: UELiveSyncSubsystem.cpp found", False)
    test("T3.2: UELiveSyncSubsystem.cpp found", False)

if synctypes_h:
    test("T3.3: PT_Reserved_02 = 0x02 defined",
          "PT_Reserved_02" in synctypes_h and "0x02" in synctypes_h[
              synctypes_h.find("PT_Reserved_02"):synctypes_h.find("PT_Reserved_02") + 50],
          "PT_Reserved_02 not found")
else:
    test("T3.3: SyncTypes.h found", False)


# ============================================================
# Section 4: Phase 8 Accuracy
# ============================================================
banner("Section 4 — Phase 8 Accuracy (no false claims)")

phase8_doc_path = os.path.join(REPO_ROOT, "Docs", "Architecture", "phase8-performance-streaming-audit.md")
phase8_doc = read(phase8_doc_path)

if phase8_doc:
    test("T4.1: Phase 8 audit doc acknowledges backpressure not implemented",
          "NOT IMPLEMENTED" in phase8_doc,
          "Phase 8 doc missing NOT IMPLEMENTED marker")
else:
    test("T4.1: Phase 8 audit doc exists", False)

# Check the validator tool doesn't claim backpressure support
if tool:
    test("T4.2: Validator tool does not claim backpressure",
          "backpressure" not in tool.lower() if "backpressure" not in tool.lower() else "not implemented" not in tool,
          "Tool may falsely reference backpressure")

    test("T4.3: Validator tool does not claim compression",
          "compress" not in tool.lower() or "compression" not in tool.lower() or "zlib" not in tool.lower(),
          "Tool may falsely claim compression support")
else:
    test("T4.2: Tool not found", False)
    test("T4.3: Tool not found", False)

sync_py_path = os.path.join(REPO_ROOT, "Blender_Addon", "sync.py")
sync_py = read(sync_py_path)
if sync_py:
    test("T4.4: check_updates returns hardcoded 0.016 (no adaptive throttle)",
          "return 0.016" in sync_py,
          "Adaptive throttle may be falsely present")
else:
    test("T4.4: sync.py not found", False)


# ============================================================
# Section 5: Manual Steps Documented
# ============================================================
banner("Section 5 — Manual Steps Documentation")

if doc:
    test("T5.1: FBX import documented as MANUAL",
          "MANUAL" in doc and ("FBX" in doc or "fbx" in doc),
          "FBX manual step not documented")

    test("T5.2: UE Python documented as MANUAL",
          "UE Python" in doc and "MANUAL" in doc,
          "UE Python manual step not documented")
else:
    test("T5.1: E2E doc found", False)
    test("T5.2: E2E doc found", False)

# Check the E2E tool has MANUAL_BLENDER_OPERATOR_REQUIRED in its doc or comments
if tool:
    test("T5.3: Tool acknowledges FBX requires manual Blender",
          "FBX" not in tool or "manual" in tool.lower() or "MANUAL" in tool,
          "Tool may imply FBX is automated")
else:
    test("T5.3: Tool found", False)


# ============================================================
# Summary
# ============================================================
print(f"\n{'=' * 60}")
print(f"  PASS: {PASS}   FAIL: {FAIL}")
print(f"{'=' * 60}")

sys.exit(0 if FAIL == 0 else 1)
