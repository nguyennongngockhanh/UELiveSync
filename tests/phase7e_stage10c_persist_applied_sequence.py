#!/usr/bin/env python3
"""Phase 7E Stage 10C — Persist Applied Sequencer Data.

Validates:
1. [SEQ][ASSET_SAVE] marker is emitted after successful keyframe apply
2. SaveLiveSyncLevelSequenceAsset exists in source
3. HandleKeyframe calls SaveLiveSyncLevelSequenceAsset when AppliedKeys > 0
4. 0x02 remains reserved/invalid
"""

import sys
import os
import re

UE_SUBSYSTEM_CPP = os.path.join(
    os.path.dirname(__file__),
    "..",
    "UE_Plugin",
    "UELiveSync",
    "Source",
    "UELiveSync",
    "Private",
    "UELiveSyncSubsystem.cpp"
)

UE_SUBSYSTEM_H = os.path.join(
    os.path.dirname(__file__),
    "..",
    "UE_Plugin",
    "UELiveSync",
    "Source",
    "UELiveSync",
    "Public",
    "UELiveSyncSubsystem.h"
)


def read_source():
    with open(UE_SUBSYSTEM_CPP) as f:
        return f.read()


def test_save_marker_exists():
    """[SEQ][ASSET_SAVE] log marker is emitted in SaveLiveSyncLevelSequenceAsset."""
    content = read_source()
    assert "[SEQ][ASSET_SAVE]" in content, \
        "[SEQ][ASSET_SAVE] marker not found in source"
    print("  PASS: [SEQ][ASSET_SAVE] marker exists")
    return True


def test_dirty_marker_exists():
    """[SEQ][ASSET_DIRTY] log marker is emitted before save."""
    content = read_source()
    assert "[SEQ][ASSET_DIRTY]" in content, \
        "[SEQ][ASSET_DIRTY] marker not found in source"
    print("  PASS: [SEQ][ASSET_DIRTY] marker exists")
    return True


def test_save_fail_marker_exists():
    """[SEQ][ASSET_SAVE_FAIL] marker exists for error paths."""
    content = read_source()
    assert "[SEQ][ASSET_SAVE_FAIL]" in content, \
        "[SEQ][ASSET_SAVE_FAIL] marker not found in source"
    print("  PASS: [SEQ][ASSET_SAVE_FAIL] marker exists")
    return True


def test_save_skip_marker_exists():
    """[SEQ][ASSET_SAVE_SKIP] marker exists for null seq."""
    content = read_source()
    assert "[SEQ][ASSET_SAVE_SKIP]" in content, \
        "[SEQ][ASSET_SAVE_SKIP] marker not found in source"
    print("  PASS: [SEQ][ASSET_SAVE_SKIP] marker exists")
    return True


def test_save_function_exists():
    """SaveLiveSyncLevelSequenceAsset function is defined."""
    content = read_source()
    assert "SaveLiveSyncLevelSequenceAsset" in content, \
        "SaveLiveSyncLevelSequenceAsset not found in source"
    print("  PASS: SaveLiveSyncLevelSequenceAsset defined")
    return True


def test_save_called_from_handle_keyframe():
    """SaveLiveSyncLevelSequenceAsset is called from HandleKeyframe after applied > 0."""
    content = read_source()
    # Find the pattern: AppliedKeys > 0 && LiveSyncSequence.IsValid -> SaveLiveSyncLevelSequenceAsset
    assert "AppliedKeys > 0 && LiveSyncSequence.IsValid()" in content or \
           "AppliedKeys > 0" in content and "SaveLiveSyncLevelSequenceAsset" in content.split("AppliedKeys > 0")[-1][:200], \
           "SaveLiveSyncLevelSequenceAsset not called after AppliedKeys > 0 in HandleKeyframe"
    print("  PASS: Save called after successful keyframe apply in HandleKeyframe")
    return True


def test_0x02_reserved():
    """0x02 is NOT in kValidTypes (reserved/invalid)."""
    content = read_source()
    start = content.find('static constexpr uint8 kValidTypes[] =')
    assert start != -1, "kValidTypes not found"

    brace_start = content.find('{', start)
    brace_end = content.find('}', brace_start)
    array_str = content[brace_start + 1: brace_end]
    types = [int(x.strip(), 16) for x in array_str.split(',') if x.strip()]

    assert 0x02 not in types, f"0x02 found in kValidTypes: {[hex(t) for t in types]}"
    assert 0x01 in types, "PT_Transform (0x01) must be present"
    assert 0x17 in types, "PT_Keyframe (0x17) must be present"
    assert 0x18 in types, "PT_SequencerOp (0x18) must be present"
    print("  PASS: 0x02 reserved, 0x01/0x17/0x18 valid")
    return True


if __name__ == '__main__':
    tests = [
        test_save_marker_exists,
        test_dirty_marker_exists,
        test_save_fail_marker_exists,
        test_save_skip_marker_exists,
        test_save_function_exists,
        test_save_called_from_handle_keyframe,
        test_0x02_reserved,
    ]

    passed = 0
    failed = 0
    for test in tests:
        try:
            test()
            passed += 1
        except Exception as e:
            print(f"FAIL: {test.__name__}: {e}")
            failed += 1

    print(f"\nResults: {passed} passed, {failed} failed, {passed + failed} total")
    sys.exit(1 if failed > 0 else 0)
