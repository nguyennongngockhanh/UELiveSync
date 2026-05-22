#!/usr/bin/env python3
"""
Phase 3.6 — Consolidated Test Harness
Detects available runtimes (Blender, UE) and runs matching suites.
Reports consolidated pass/fail across all executed tests.
"""

import subprocess
import sys
import os
import time
import socket

HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = []


def banner(title):
    width = 60
    print()
    print("=" * width)
    print(f"  {title}")
    print("=" * width)


def check_ue_port(host="127.0.0.1", port=57000, timeout=2.0):
    """Check if UE editor is listening on sync port."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect((host, port))
        s.close()
        return True
    except:
        return False


# =====================================================
# KNOWN PATHS
# =====================================================

UE_EDITOR = "/home/nguyennongngockhanh/Unreal/UE5.7.4/Engine/Binaries/Linux/UnrealEditor"
UE_PROJECT = "/home/nguyennongngockhanh/Documents/Unreal Projects/ProjectTemplate/ProjectTemplate.uproject"
UE_PLUGIN = "/home/nguyennongngockhanh/Documents/Unreal Projects/ProjectTemplate/Plugins/UELiveSync"
REPO = "/home/nguyennongngockhanh/Projects/UELiveSync"


def find_blender():
    """Find Blender executable (binary or flatpak)."""
    # Try flatpak first (user provided)
    try:
        r = subprocess.run(
            ["flatpak", "run", "--command=python3", "org.blender.Blender", "--version"],
            capture_output=True, text=True, timeout=10)
        if r.returncode == 0 or "Blender" in r.stdout:
            return "flatpak run org.blender.Blender"
    except:
        pass

    # Try direct binary
    candidates = [
        "blender",
        "/usr/bin/blender",
        "/usr/local/bin/blender",
        "/opt/blender/blender",
        "/snap/bin/blender",
        os.path.expanduser("~/blender/blender"),
        os.path.expanduser("~/Applications/Blender/blender"),
    ]
    for c in candidates:
        try:
            r = subprocess.run([c, "--version"],
                               capture_output=True, text=True, timeout=5)
            if r.returncode == 0:
                return c
        except:
            continue
    return None


def run_test(test_file, label, runner_type="python3"):
    """Run a single test file and return (pass_count, fail_count)."""
    banner(f"{label}: {os.path.basename(test_file)}")

    if runner_type == "python3":
        cmd = [sys.executable, test_file]
    elif runner_type == "flatpak":
        cmd = ["flatpak", "run", "--branch=stable", "--arch=x86_64",
               "--command=blender", "org.blender.Blender",
               "-b", "--python", os.path.abspath(test_file)]
    elif runner_type == "blender":
        cmd = [runner_type, "-b", "--python", test_file]
    else:
        cmd = [runner_type, test_file]

    print(f"  Command: {' '.join(cmd)}")
    print()

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120
        )
        print(result.stdout)

        if result.stderr:
            # Filter out benign Blender warnings
            stderr_lines = [
                l for l in result.stderr.split("\n")
                if l and "Fra:0" not in l
                and "BKE" not in l
                and "ALSA" not in l
                and "Report: " not in l
                and "suspect" not in l
                and "blender" not in l.lower()
                and "Warning" not in l
            ]
            if stderr_lines:
                print("  [stderr]")
                for l in stderr_lines[:10]:
                    print(f"    {l}")
                if len(stderr_lines) > 10:
                    print(f"    ... ({len(stderr_lines)} total lines)")

        # Parse results
        pass_count = 0
        fail_count = 0
        for line in result.stdout.split("\n"):
            if "PASS:" in line:
                pass_count += 1
            elif "FAIL:" in line:
                fail_count += 1

        # Check final RESULTS line
        passed = result.returncode == 0 and fail_count == 0

        summary = f"  {'✅' if passed else '❌'} {pass_count} passed, {fail_count} failed"
        print(summary)

        return passed, pass_count, fail_count

    except subprocess.TimeoutExpired:
        print("  ⏰ TIMEOUT (120s)")
        return False, 0, 1
    except Exception as e:
        print(f"  💥 ERROR: {e}")
        return False, 0, 1


def main():
    print()
    print("=" * 60)
    print("  PHASE 3.6 — VALIDATION HARNESS")
    print("  Consolidated test runner")
    print("=" * 60)

    # =====================================================
    # DISCOVERY
    # =====================================================
    print("\n--- Environment Discovery ---")

    ue_available = check_ue_port()
    print(f"  UE Editor on :57000:  {'✅ yes' if ue_available else '❌ no'}")

    blender_path = find_blender()
    print(f"  Blender:            {'✅ ' + blender_path if blender_path else '❌ not found'}")

    all_passed = True
    total_pass = 0
    total_fail = 0
    total_skipped = 0

    # =====================================================
    # UE TESTS (A, C, D)
    # =====================================================
    if ue_available:
        for test_file, label in [
            ("phase3.6_validation_A_reconnect.py", "A: Reconnect Torture"),
            ("phase3.6_validation_C_snapshot.py",  "C: Snapshot Correctness"),
            ("phase3.6_validation_D_long_session.py", "D: Long-Session Runtime"),
        ]:
            path = os.path.join(HERE, test_file)
            if os.path.exists(path):
                passed, p, f = run_test(path, label, runner_type="python3")
                all_passed = all_passed and passed
                total_pass += p
                total_fail += f
            else:
                print(f"\n  ⚠️  {test_file} not found, skipping")
                total_skipped += 1
    else:
        print("\n  ⏭️  Skipping UE tests (no UE editor detected on :57000)")
        total_skipped += 3

    # =====================================================
    # BLENDER TEST (B)
    # =====================================================
    if blender_path:
        test_file = "phase3.6_validation_B_hierarchy.py"
        path = os.path.join(HERE, test_file)
        if os.path.exists(path):
            btype = "flatpak" if "flatpak" in blender_path else "blender"
            passed, p, f = run_test(path, "B: Hierarchy Stress", runner_type=btype)
            all_passed = all_passed and passed
            total_pass += p
            total_fail += f
        else:
            print(f"\n  ⚠️  {test_file} not found, skipping")
            total_skipped += 1
    else:
        print("\n  ⏭️  Skipping Blender test (no blender executable found)")
        total_skipped += 1

    # =====================================================
    # CONSOLIDATED REPORT
    # =====================================================
    print()
    print("=" * 60)
    print("  CONSOLIDATED RESULTS")
    print("=" * 60)
    print(f"  Total passed:  {total_pass}")
    print(f"  Total failed:  {total_fail}")
    print(f"  Skipped:       {total_skipped}")
    print(f"  Overall:       {'✅ ALL PASSED' if all_passed and total_fail == 0 else '❌ SOME FAILED'}")

    if total_skipped > 0:
        print()
        print("  ⚠️  Some tests were skipped due to missing runtimes.")
        print("     To run all tests, ensure:")
        print("       - UE Editor with UELiveSync plugin is running on :57000")
        print("       - Blender executable is in PATH")

    print()
    return 0 if all_passed and total_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
