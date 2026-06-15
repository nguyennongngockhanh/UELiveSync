#!/usr/bin/env python3
"""Phase 7E Stage 10D.1 — Sequencer Editor Usability Test.

Validates the /tmp/uelivesync_10d1_result.json written by in-UE Python.

Skips if result file not found (not yet run inside UE).
"""

import json
import os
import sys

RESULT_PATH = "/tmp/uelivesync_10d1_result.json"


def read_result():
    if not os.path.exists(RESULT_PATH):
        print(f"SKIP: Result file not found at {RESULT_PATH}")
        print("  Run in-UE Python script first:")
        print("    tools/uelivesync_10d_editor_sequence_validation.py --generate")
        return None
    with open(RESULT_PATH) as f:
        return json.load(f)


def test_asset_loads(result):
    assert result.get("asset_load") == "OK", f"asset_load={result.get('asset_load')}"
    assert result.get("class_name") == "LevelSequence"
    print("  PASS: asset loads as LevelSequence")
    return True


def test_editor_open(result):
    assert result.get("editor_open") is True, "editor_open was False"
    print("  PASS: open_level_sequence() succeeded")
    return True


def test_playback_range(result):
    assert result.get("playback_range") and "exists" in str(result["playback_range"])
    print("  PASS: MovieScene playback range exists")
    return True


def test_binding_exists(result):
    bc = result.get("binding_count", 0)
    assert bc >= 1, f"binding_count={bc}, expected >= 1"
    print(f"  PASS: binding_count={bc}")
    return True


def test_binding_details(result):
    bindings = result.get("bindings", [])
    assert len(bindings) >= 1, "no bindings data"
    b = bindings[0]
    assert "name" in b, "binding missing name"
    assert len(b.get("tracks", [])) >= 2, f"expected >=2 tracks, got {len(b.get('tracks', []))}"
    print(f"  PASS: binding '{b.get('name')}' has {len(b.get('tracks', []))} tracks")
    for t in b.get("tracks", []):
        sec_key = f"sections_{t}"
        assert sec_key in b and b[sec_key] >= 1, f"track {t} missing sections"
        print(f"    {t}: {b[sec_key]} section(s)")
    return True


def test_transform_track(result):
    tracks = result.get("tracks_detected", {})
    assert "MovieScene3DTransformTrack" in tracks, "missing transform track"
    print("  PASS: MovieScene3DTransformTrack detected")
    return True


def test_bool_track(result):
    tracks = result.get("tracks_detected", {})
    assert "MovieSceneBoolTrack" in tracks, "missing bool track"
    print("  PASS: MovieSceneBoolTrack detected")
    return True


def test_classification(result):
    cls = result.get("classification")
    assert cls in ("PASS_EDITOR_DATA_ONLY", "PASS_BINDING_ONLY"), f"classification={cls}"
    print(f"  PASS: classification={cls}")
    return True


def test_no_errors(result):
    errors = result.get("errors", [])
    assert len(errors) == 0, f"errors: {errors}"
    print("  PASS: no errors")
    return True


if __name__ == '__main__':
    result = read_result()
    if result is None:
        sys.exit(0)

    tests = [
        ("asset_loads", test_asset_loads),
        ("editor_open", test_editor_open),
        ("playback_range", test_playback_range),
        ("binding_exists", test_binding_exists),
        ("binding_details", test_binding_details),
        ("transform_track", test_transform_track),
        ("bool_track", test_bool_track),
        ("classification", test_classification),
        ("no_errors", test_no_errors),
    ]

    passed = 0
    failed = 0
    for name, fn in tests:
        try:
            fn(result)
            passed += 1
        except Exception as e:
            print(f"FAIL: {name}: {e}")
            failed += 1

    print(f"\nResults: {passed} passed, {failed} failed, {passed + failed} total")
    sys.exit(1 if failed > 0 else 0)
