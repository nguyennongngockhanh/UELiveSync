#!/usr/bin/env python3
"""UELiveSync E2E Runtime Validator.

Orchestrates existing standalone injectors/validators via subprocess
to run a comprehensive end-to-end check against a live UE session.

Steps:
  1. Timeline State (PT_TimelineState)
  2. Playback Transport (PT_PlaybackTransport)
  3. Camera Lifecycle (CREATE + TRANSFORM + ACTIVE_CAMERA)
  4. Camera Definition (PT_CameraDef)
  5. Sequencer + Keyframes (full 5-packet flow)
  6. Log-based queue diagnostics check
  7. Summary report

Usage:
  # Full E2E run (requires running UE editor with CVars set)
  python tools/uelivesync_e2e_runtime_validator.py

  # Check-only mode (skip injection, just read existing log)
  python tools/uelivesync_e2e_runtime_validator.py --check-log
"""

import os
import sys
import subprocess
import re
import time
import json
import argparse

PASS = 0
FAIL = 0
SKIP = 0

TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(TOOLS_DIR)
DEFAULT_LOG = os.path.expanduser(
    "~/Documents/Unreal Projects/ProjectTemplate/Saved/Logs/ProjectTemplate.log"
)


def banner(title):
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print(f"{'=' * 60}")


def step_result(name, passed, detail=""):
    global PASS, FAIL
    if passed:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}")
        if detail:
            for line in detail.split("\n"):
                print(f"        {line}")


def check_log_markers(log_path, markers, name):
    """Check that all markers exist in the UE log file."""
    if not os.path.isfile(log_path):
        return step_result(name, False, f"Log not found: {log_path}")
    try:
        with open(log_path, "r") as f:
            text = f.read()
    except Exception as e:
        return step_result(name, False, f"Read error: {e}")

    missing = [m for m in markers if m not in text]
    if missing:
        detail = f"Missing markers: {', '.join(missing[:5])}"
        if len(missing) > 5:
            detail += f" (+{len(missing)-5} more)"
        return step_result(name, False, detail)
    return step_result(name, True)


def run_tool(tool_name, args, name):
    """Run a tool script via subprocess and check exit code."""
    tool_path = os.path.join(TOOLS_DIR, tool_name)
    if not os.path.isfile(tool_path):
        return step_result(name, False, f"Tool not found: {tool_path}")

    cmd = [sys.executable, tool_path] + args
    print(f"  Running: {' '.join(cmd)}")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        print(f"  Exit code: {result.returncode}")
        if result.stdout:
            for line in result.stdout.strip().split("\n"):
                if any(tag in line for tag in ["PASS", "FAIL", "[CAMERA]", "[TIMELINE]", "[PLAYBACK]", "[KEYFRAME]", "[SEQ]", "Results:", "RESULTS:"]):
                    print(f"    {line.strip()}")
        if result.stderr:
            for line in result.stderr.strip().split("\n"):
                if "error" in line.lower() or "traceback" in line.lower():
                    print(f"    [STDERR] {line.strip()}")
        if result.returncode == 0:
            step_result(name, True)
        else:
            step_result(name, False, f"Exit code {result.returncode}")
    except subprocess.TimeoutExpired:
        step_result(name, False, "Timed out (120s)")
    except Exception as e:
        step_result(name, False, f"Exception: {e}")


def step_timeline_state(log_path):
    banner("Step 1 — Timeline State")
    run_tool("uelivesync_7f_timeline_state_client.py", ["timeline"], "Timeline state inject + markers")
    time.sleep(0.5)
    check_log_markers(log_path, ["[TIMELINE][RECV]", "[TIMELINE][APPLY]"], "Timeline markers in log")


def step_playback_transport(log_path):
    banner("Step 2 — Playback Transport")
    run_tool("uelivesync_7f_playback_transport_client.py", ["transport"], "Playback transport inject + markers")
    time.sleep(0.5)
    check_log_markers(log_path, ["[PLAYBACK][RECV]", "[PLAYBACK][APPLY]"], "Playback markers in log")


def step_camera_lifecycle(log_path):
    banner("Step 3 — Camera Lifecycle")
    run_tool("uelivesync_7g_camera_transform_client.py", ["--create-transform-active"], "Camera lifecycle inject + markers")
    time.sleep(2.5)
    check_log_markers(log_path, [
        "[CAMERA][CREATE]", "[CAMERA][TRANSFORM_APPLY]",
        "[CAMERA][ACTIVE_RECV]", "[CAMERA][SEQ_BIND]", "[CAMERA][CUT_APPLY]"
    ], "Camera lifecycle markers")


def step_camera_def(log_path):
    banner("Step 4 — Camera Definition")
    run_tool("uelivesync_7g_camera_def_client.py", [], "Camera definition inject + markers")
    time.sleep(0.5)
    check_log_markers(log_path, ["[CAMERA][DEF_RECV]"], "Camera definition markers")


def step_sequencer_keyframes(log_path):
    banner("Step 5 — Sequencer + Keyframes")
    run_tool("uelivesync_10b_tcp_client.py", [], "Sequencer + keyframe inject + markers")
    time.sleep(0.5)
    check_log_markers(log_path, [
        "[SEQOP] CREATE_SEQUENCE", "[SEQOP] ADD_POSSESSABLE",
        "[KEYFRAME] Applied", "[CAMERA][CUT_SAVE]"
    ], "Sequencer/keyframe markers")
    check_log_markers(log_path, [
        "applied=11 miss=0 unsupp=0"
    ], "Keyframe apply all 11 (ch 0-10)")


def step_queue_diagnostics(log_path):
    banner("Step 6 — Queue Diagnostics")
    if not os.path.isfile(log_path):
        return step_result("Queue log check", False, f"Log not found: {log_path}")

    with open(log_path, "r") as f:
        text = f.read()

    warnings = []
    drops = re.findall(r"PacketsDropped[ =:]+(\d+)", text)
    if drops and any(int(d) > 0 for d in drops):
        warnings.append(f"PacketsDropped > 0: {drops}")

    overflows = re.findall(r"OverflowEvent|Queue depth[ =:]+(\d+)", text)
    overflow_depths = [int(d) for d in re.findall(r"Queue depth[ =:]+(\d+)", text) if int(d) > 32]
    if overflow_depths:
        warnings.append(f"High queue depths: {overflow_depths}")

    if warnings:
        step_result("Queue diagnostics", False, "; ".join(warnings))
    else:
        step_result("Queue diagnostics (no drops or overflow)", True)


def step_malformed_check(log_path):
    banner("Step 7 — Malformed/Invalid Packet Check")
    if not os.path.isfile(log_path):
        return step_result("Malformed packet check", False, f"Log not found: {log_path}")

    with open(log_path, "r") as f:
        text = f.read()

    malformed = re.findall(r"Invalid packet type|Malformed|\[MALFORMED\]", text)
    if malformed:
        step_result("No malformed/invalid packets", False, f"Found {len(malformed)} occurrences")
    else:
        step_result("No malformed/invalid packets detected", True)


def do_orchestrated_run(log_path):
    """Run all steps in sequence."""
    total_start = time.time()

    step_timeline_state(log_path)
    step_playback_transport(log_path)
    step_camera_lifecycle(log_path)
    step_camera_def(log_path)
    step_sequencer_keyframes(log_path)
    step_queue_diagnostics(log_path)
    step_malformed_check(log_path)

    elapsed = time.time() - total_start

    banner("E2E Runtime Validation Summary")
    print(f"  Total: {PASS+FAIL} checks ({PASS} PASS, {FAIL} FAIL, {SKIP} SKIP)")
    print(f"  Elapsed: {elapsed:.1f}s")
    print(f"  Log: {log_path}")
    print(f"  Classification: PASS_E2E_RUNTIME_PARTIAL" if FAIL == 0 else "  Classification: FAIL_E2E_RUNTIME")
    print()


def do_check_only(log_path):
    """Check-only mode: validate existing log without injection."""
    step_queue_diagnostics(log_path)
    step_malformed_check(log_path)
    check_log_markers(log_path, [
        "[TIMELINE]", "[PLAYBACK]", "[CAMERA]",
        "[KEYFRAME]", "[SEQOP]"
    ], "All expected marker prefixes present in log")


def main():
    parser = argparse.ArgumentParser(description="UELiveSync E2E Runtime Validator")
    parser.add_argument("--check-log", default=None, nargs="?", const=DEFAULT_LOG,
                        help="Check-only: validate existing log without injection")
    parser.add_argument("--log", default=DEFAULT_LOG,
                        help=f"Path to ProjectTemplate.log (default: {DEFAULT_LOG})")
    parser.add_argument("--max-steps", type=int, default=7,
                        help="Maximum steps to run (1-7, default 7)")
    args = parser.parse_args()

    log_path = args.check_log if args.check_log else args.log
    print(f"UELiveSync E2E Runtime Validator")
    print(f"Log: {log_path}")

    if not os.path.isfile(log_path):
        print(f"  WARNING: Log file not found at {log_path}")

    if args.check_log is not None:
        do_check_only(log_path)
    else:
        do_orchestrated_run(log_path)

    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
