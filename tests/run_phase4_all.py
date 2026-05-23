#!/usr/bin/env python3
"""
Phase 4 — Consolidated Validation Harness

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
REPO = "/home/nguyennongngockhanh/Projects/UELiveSync"


def find_blender():
    """Find Blender executable (binary or flatpak)."""
    try:
        r = subprocess.run(
            ["flatpak", "run", "--command=python3", "org.blender.Blender", "--version"],
            capture_output=True, text=True, timeout=10)
        if r.returncode == 0 or "Blender" in r.stdout:
            return "flatpak run org.blender.Blender"
    except:
        pass

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

        passed = result.returncode == 0 and fail_count == 0

        summary = f"  {'PASS' if passed else 'FAIL'} {pass_count} passed, {fail_count} failed"
        print(summary)

        return passed, pass_count, fail_count

    except subprocess.TimeoutExpired:
        print("  TIMEOUT (120s)")
        return False, 0, 1
    except Exception as e:
        print(f"  ERROR: {e}")
        return False, 0, 1


def main():
    print()
    print("=" * 60)
    print("  PHASE 4 — VALIDATION HARNESS")
    print("  Consolidated test runner")
    print("=" * 60)

    # =====================================================
    # DISCOVERY
    # =====================================================
    print("\n--- Environment Discovery ---")

    ue_available = check_ue_port()
    print(f"  UE Editor on :57000:  {'yes' if ue_available else 'no'}")

    blender_path = find_blender()
    print(f"  Blender:            {'yes ' + blender_path if blender_path else 'not found'}")

    all_passed = True
    total_pass = 0
    total_fail = 0
    total_skipped = 0

    # =====================================================
    # BLENDER TESTS (A)
    # =====================================================
    if blender_path:
        test_file = "phase4_validation_A_prefs.py"
        path = os.path.join(HERE, test_file)
        if os.path.exists(path):
            btype = "flatpak" if "flatpak" in blender_path else "blender"
            passed, p, f = run_test(path, "A: Preferences & Config", runner_type=btype)
            all_passed = all_passed and passed
            total_pass += p
            total_fail += f
        else:
            print(f"\n  {test_file} not found, skipping")
            total_skipped += 1
    else:
        print("\n  Skipping Blender test (A) — no blender executable found")
        total_skipped += 1

    # =====================================================
    # UE TESTS (B, C, E)
    # =====================================================
    if ue_available:
        for test_file, label in [
            ("phase4_validation_B_overflow.py", "B: Queue Overflow"),
            ("phase4_validation_C_diagnostics.py", "C: Diagnostics Commands"),
            ("phase4_validation_E_protocol.py", "E: Protocol Validation"),
        ]:
            path = os.path.join(HERE, test_file)
            if os.path.exists(path):
                passed, p, f = run_test(path, label, runner_type="python3")
                all_passed = all_passed and passed
                total_pass += p
                total_fail += f
            else:
                print(f"\n  {test_file} not found, skipping")
                total_skipped += 1
    else:
        print("\n  Skipping UE tests (B, C, E) — no UE editor detected on :57000")
        total_skipped += 3

    # =====================================================
    # UE TEST D (watchdog — requires UE, takes 40s)
    # =====================================================
    if ue_available:
        test_file = "phase4_validation_D_watchdog.py"
        path = os.path.join(HERE, test_file)
        if os.path.exists(path):
            print("\n  Test D (Watchdog) requires ~40s. Run separately:")
            print(f"    python3 {path}")
            print("  Skipping in consolidated run.\n")
            total_skipped += 1
        else:
            print(f"\n  {test_file} not found, skipping")
            total_skipped += 1
    else:
        print("\n  Skipping UE test (D) — no UE editor detected on :57000")
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
    print(f"  Overall:       {'ALL PASSED' if all_passed and total_fail == 0 else 'SOME FAILED'}")

    if total_skipped > 0:
        print()
        print("  Some tests were skipped due to missing runtimes.")
        print("  To run all tests, ensure:")
        print("    - UE Editor with UELiveSync plugin is running on :57000")
        print("    - Blender executable is in PATH")
        print("    - For test D: python3 tests/phase4_validation_D_watchdog.py")

    print()
    return 0 if all_passed and total_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
