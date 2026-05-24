#!/usr/bin/env python3
"""
Phase 5E — Stress Testing & Observability Suite Runner

Runs all Phase 5E validation suites in sequence:
  - Long-Duration Stability Test (30+ min)
  - Large-Scene Stress Test (1000+ objects)
  - Reconnect Storm Test (rapid cycles)

This is late Phase 5 (Protocol Evolution & Runtime Stabilization)
work — NOT Phase 6 (Live Editing) or Phase 7 (Animation Sync).

Usage:
    python3 tests/run_phase5e_all.py [--quick]

Options:
    --quick    Skip long-duration test (30 min), run only scene + reconnect

Exit code = number of failed tests (0 = all pass).
"""

import subprocess
import sys
import time

SUITES = [
    ("5EA: Long-Duration Stability (30 min)",
     "phase5e_stress_long_duration.py"),
    ("5EB: Large-Scene Stress (1000+ objects)",
     "phase5e_stress_large_scene.py"),
    ("5EC: Reconnect Storm",
     "phase5e_stress_reconnect_storm.py"),
]

QUICK_SUITES = [
    ("5EB: Large-Scene Stress (1000+ objects)",
     "phase5e_stress_large_scene.py"),
    ("5EC: Reconnect Storm",
     "phase5e_stress_reconnect_storm.py"),
]


def run_suite(label, filename):
    print(f"\n{'#' * 60}")
    print(f"#  {label}")
    print(f"{'#' * 60}\n")

    start = time.time()
    result = subprocess.run(
        [sys.executable, filename],
        cwd="tests",
        capture_output=True,
        text=True,
    )
    elapsed = time.time() - start

    print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="")

    passed = result.returncode == 0
    status = "PASS" if passed else "FAIL"
    print(f"\n  [{status}] {label} ({elapsed:.1f}s)")
    return passed


def main():
    quick = "--quick" in sys.argv
    suites = QUICK_SUITES if quick else SUITES

    failed_suites = 0
    total_start = time.time()

    mode = "QUICK" if quick else "FULL"
    print(f"Phase 5E — Stress Testing & Observability ({mode})")
    print(f"Started: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Suites to run: {len(suites)}")
    if quick:
        print("NOTE: Use --quick to skip 30-min long-duration test")
    print()

    for label, filename in suites:
        if not run_suite(label, filename):
            failed_suites += 1

    elapsed = time.time() - total_start
    print(f"\n{'=' * 60}")
    print(f"Phase 5E Complete: {len(suites) - failed_suites}/{len(suites)} passed "
          f"({elapsed:.1f}s)")

    if failed_suites:
        print(f"  {failed_suites} suite(s) FAILED")
    else:
        print("  All suites PASSED")

    sys.exit(failed_suites)


if __name__ == "__main__":
    main()
