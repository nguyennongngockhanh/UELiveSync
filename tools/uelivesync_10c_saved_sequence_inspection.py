#!/usr/bin/env python3
"""Stage 10C.1 — Saved LevelSequence Asset Inspection Validator.

Validates the /tmp/uelivesync_10c1_result.json written by the UE Python
asset inspection script.

Modes:
  --check-result   Validate the result file (default)
  --generate       Print the UE Python script to stdout
"""

import json
import os
import sys

RESULT_PATH = "/tmp/uelivesync_10c1_result.json"


def validate_result(result_path=RESULT_PATH):
    """Validate the asset inspection result from UE Python."""
    print(f"\n--- Checking saved sequence inspection result: {result_path} ---")
    if not os.path.exists(result_path):
        print(f"  Result file not found: {result_path}")
        print("  Run UE Python script first:")
        print("    py \"/tmp/ue_10c1_inspect_asset.py\"")
        return False

    with open(result_path) as f:
        result = json.load(f)

    all_pass = True

    # Overall PASS
    if result.get("pass"):
        print(f"  PASS: overall result is True")
    else:
        print(f"  FAIL: overall result is False")
        all_pass = False

    # Load result
    load = result.get("load_result")
    if load == "OK":
        print(f"  PASS: load_result={load}")
    else:
        print(f"  FAIL: load_result={load}")
        all_pass = False

    # Class
    cls = result.get("class_name")
    if cls == "LevelSequence":
        print(f"  PASS: class_name={cls}")
    else:
        print(f"  FAIL: class_name={cls}")
        all_pass = False

    # Bindings
    bc = result.get("binding_count", 0)
    if bc > 0:
        print(f"  PASS: binding_count={bc} (bindings persisted through save/load)")
        for b in result.get("bindings", []):
            print(f"    Binding: {b.get('name', '?')}")
            for t in b.get("tracks", []):
                tc = t.get("class", "?")
                sc = len(t.get("sections", []))
                print(f"      Track: {tc} ({sc} sections)")
    else:
        print(f"  FAIL: binding_count={bc}, expected >0")
        all_pass = False

    # Errors
    for err in result.get("errors", []):
        print(f"  ERROR: {err}")

    classification = "PASS_BINDING_ONLY" if all_pass else "FAIL"
    print(f"\n  Classification: {classification}")
    return all_pass


if __name__ == '__main__':
    sys.exit(0 if validate_result() else 1)
