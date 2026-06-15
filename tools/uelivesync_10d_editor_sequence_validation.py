#!/usr/bin/env python3
"""Phase 7E Stage 10D.1 — Sequencer Editor Usability Validation.

Validates that the persisted LiveSync LevelSequence asset is usable in
UE Sequencer Editor workflow.

Two modes:
  --check-result   Validate the /tmp/uelivesync_10d1_result.json written by
                   the in-UE Python script.
  --generate       Print the in-UE Python script to stdout.

Default: run --check-result
"""

import json
import os
import sys

RESULT_PATH = "/tmp/uelivesync_10d1_result.json"
ASSET_PATH = "/Game/UELiveSync/Sequences/LS_UELiveSync_Runtime"

EXPECTED = {
    "pass": True,
    "classification": "PASS_EDITOR_DATA_ONLY",
    "asset_load": "OK",
    "editor_open": True,
    "binding_count": 1,
    "tracks_detected": {
        "MovieScene3DTransformTrack": 1,
        "MovieSceneBoolTrack": 1,
    },
}


def check_result(result_path=RESULT_PATH):
    print(f"--- Checking UE Python result: {result_path} ---")
    if not os.path.exists(result_path):
        print(f"  Result file not found: {result_path}")
        print("  Run in-UE Python script first.")
        return False

    with open(result_path) as f:
        result = json.load(f)

    checks = [
        ("overall pass", result.get("pass") is True),
        ("classification", result.get("classification") == "PASS_EDITOR_DATA_ONLY"),
        ("asset_load", result.get("asset_load") == "OK"),
        ("editor_open", result.get("editor_open") is True),
        ("binding_count >= 1", result.get("binding_count", 0) >= 1),
        ("MovieScene3DTransformTrack", "MovieScene3DTransformTrack" in result.get("tracks_detected", {})),
        ("MovieSceneBoolTrack", "MovieSceneBoolTrack" in result.get("tracks_detected", {})),
        ("section_3d_transform", any(
            s.get("sections_MovieScene3DTransformTrack", 0) > 0
            for b in result.get("bindings", [])
            if "sections_MovieScene3DTransformTrack" in s
            for s in [b]
        ) or result.get("bindings", [])[0].get("sections_MovieScene3DTransformTrack", 0) > 0
            if result.get("bindings", [])
            else False),
    ]

    all_pass = True
    for name, ok in checks:
        status = "PASS" if ok else "FAIL"
        if not ok:
            all_pass = False
        print(f"  {status}: {name}")

    # Print binding details
    print(f"\n  --- Binding details ---")
    for binding in result.get("bindings", []):
        print(f"    name: {binding.get('name')}")
        for t in binding.get("tracks", []):
            sec_key = f"sections_{t}"
            print(f"      {t}: {binding.get(sec_key, '?')} section(s)")

    print(f"\n  Errors: {len(result.get('errors', []))}")
    for err in result.get("errors", []):
        print(f"    {err}")

    return all_pass


def generate_script():
    script = '''"""Stage 10D.1 — UE Python Sequencer Editor Usability Validation
Run inside UE via: py "/path/to/this_script.py"
"""
import unreal
import json
ASSET_PATH = "/Game/UELiveSync/Sequences/LS_UELiveSync_Runtime"
RESULT_PATH = "/tmp/uelivesync_10d1_result.json"
result = {"pass": False, "phase": "10D.1", "classification": "UNKNOWN", "asset_path": ASSET_PATH, "errors": []}
try:
    asset = unreal.load_asset(ASSET_PATH)
    if not asset:
        result["asset_load"] = "FAIL"
        result["errors"].append("unreal.load_asset returned None")
    else:
        result["asset_load"] = "OK"
        result["class_name"] = asset.get_class().get_name()
        try:
            unreal.LevelSequenceEditorBlueprintLibrary.open_level_sequence(asset)
            result["editor_open"] = True
        except Exception as e:
            result["editor_open"] = False
            result["errors"].append(f"open_level_sequence: {e}")
        try:
            result["playback_range"] = "exists (MovieScene OK)" if asset.get_movie_scene() else "None"
        except Exception as e:
            result["playback_range"] = f"error: {e}"
        try:
            bindings = asset.get_bindings()
            result["binding_count"] = len(bindings) if bindings else 0
            result["bindings"] = []
            result["tracks_detected"] = {}
            for binding in (bindings or []):
                b_name = str(binding.get_display_name()) if hasattr(binding, "get_display_name") else str(binding)
                b_info = {"name": b_name, "tracks": []}
                for track in (binding.get_tracks() if hasattr(binding, "get_tracks") else []):
                    tc = track.get_class().get_name()
                    b_info["tracks"].append(tc)
                    result["tracks_detected"][tc] = result["tracks_detected"].get(tc, 0) + 1
                    sections = track.get_sections() if hasattr(track, "get_sections") else []
                    b_info[f"sections_{tc}"] = len(sections)
                result["bindings"].append(b_info)
            ht = "MovieScene3DTransformTrack" in result.get("tracks_detected", {})
            hb = "MovieSceneBoolTrack" in result.get("tracks_detected", {})
            result["classification"] = "PASS_EDITOR_DATA_ONLY" if (result.get("binding_count", 0) > 0 and ht and hb) else (
                "PASS_BINDING_ONLY" if result.get("binding_count", 0) > 0 else "PASS_LOAD_ONLY")
            result["pass"] = True
        except Exception as e:
            result["binding_error"] = str(e)
except Exception as e:
    result["errors"].append(f"unhandled: {traceback.format_exc()}")
    result["classification"] = "FAIL"
with open(RESULT_PATH, "w") as f:
    json.dump(result, f, indent=2, default=str)
unreal.log(f"[10D.1] classification={result['classification']} pass={result['pass']}")
'''
    print(script)


def run():
    if "--generate" in sys.argv:
        generate_script()
        return
    success = check_result()
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    run()
