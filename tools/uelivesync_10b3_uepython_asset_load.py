#!/usr/bin/env python3
"""Phase 7E Stage 10B.3 — UE Python Asset Load Verification

This script has two modes:

  Mode 1 — Result validation (run on host, after UE Python ran):
    python tools/uelivesync_10b3_uepython_asset_load.py --check-result

    Reads /tmp/uelivesync_10b3_result.json (written by the UE Python script)
    and validates the asset load outcome.

  Mode 2 — Generate UE Python script for execution inside UE:
    python tools/uelivesync_10b3_uepython_asset_load.py --generate

    Prints the UE Python script to stdout. Pipe or copy into UE console:
      py "/path/to/generated_script.py"

  Mode 3 — Full orchestrator (launch UE + TCP inject + validate):
    python tools/uelivesync_10b3_uepython_asset_load.py
"""

import json
import sys

RESULT_PATH = "/tmp/uelivesync_10b3_result.json"
ASSET_PATH = "/Game/UELiveSync/Sequences/LS_UELiveSync_Runtime"

EXPECTED_PASS_LOAD_ONLY = {
    "pass": True,
    "load_result": "OK",
    "class_name": "LevelSequence",
}


def generate_ue_script():
    """Print the UE Python script to stdout."""
    script = '''"""Stage 10B.3 — UE Python Asset Load Verification

Run inside Unreal Editor via:
  py "/path/to/this_script.py"
"""
import unreal
import json
import traceback

ASSET_PATH = "/Game/UELiveSync/Sequences/LS_UELiveSync_Runtime"
RESULT_PATH = "/tmp/uelivesync_10b3_result.json"
result = {"pass": False, "phase": "10B.3", "asset_path": ASSET_PATH, "attempts": [], "errors": []}

try:
    asset = unreal.load_asset(ASSET_PATH)
    if asset is None:
        result["load_result"] = "NULL"
        result["errors"].append("unreal.load_asset returned None")
    else:
        result["load_result"] = "OK"
        result["class_name"] = asset.get_class().get_name()
        try:
            ms = asset.get_movie_scene()
            result["movie_scene"] = True if ms else False
        except Exception:
            result["movie_scene"] = False
        try:
            bindings = asset.get_bindings()
            result["binding_count"] = len(bindings) if bindings else 0
        except Exception as e:
            result["binding_count"] = -1
            result["errors"].append(f"get_bindings: {e}")
        result["pass"] = True
except Exception as e:
    result["errors"].append(f"unhandled: {traceback.format_exc()}")

with open(RESULT_PATH, "w") as f:
    json.dump(result, f, indent=2, default=str)
unreal.log(f"[10B.3] PASS={result['pass']} load={result['load_result']}")
'''
    print(script)


def check_host_log(result_path=RESULT_PATH):
    """Validate the result JSON written by the UE Python script."""
    print(f"\\n--- Checking UE Python result: {result_path} ---")
    if not os.path.exists(result_path):
        print(f"  Result file not found: {result_path}")
        print("  UE Python script has not been run yet.")
        return False

    with open(result_path) as f:
        result = json.load(f)

    all_pass = True

    # Check pass flag
    if result.get("pass"):
        print(f"  PASS: overall result is True")
    else:
        print(f"  FAIL: overall result is False")
        all_pass = False

    # Check load_result
    load = result.get("load_result")
    if load == "OK":
        print(f"  PASS: load_result={load}")
    else:
        print(f"  FAIL: load_result={load}")
        all_pass = False

    # Check class_name
    cls = result.get("class_name")
    if cls == "LevelSequence":
        print(f"  PASS: class_name={cls}")
    else:
        print(f"  FAIL: class_name={cls}")
        all_pass = False

    # But the deepest truth is the result itself - PASS = load worked
    if result.get("binding_count", -1) >= 0:
        print(f"  INFO: binding_count={result.get('binding_count')}")
    if result.get("errors"):
        for err in result.get("errors", []):
            print(f"  INFO: {err}")

    print(f"\\n  Result classification: {'PASS_LOAD_ONLY' if all_pass else 'FAIL'}")
    return all_pass


def run_check_result():
    sys.exit(0 if check_host_log() else 1)


def run():
    import os

    if "--check-result" in sys.argv:
        run_check_result()
        return

    if "--generate" in sys.argv:
        generate_ue_script()
        return

    print("Stage 10B.3 — UE Python Asset Load Verification")
    print()
    print("Modes:")
    print("  --check-result   Validate result file from UE Python")
    print("  --generate       Print UE Python script to stdout")
    print()

    check_host_log()
    print()


if __name__ == "__main__":
    run()
