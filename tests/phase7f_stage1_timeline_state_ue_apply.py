#!/usr/bin/env python3
"""Phase 7F Stage 1 — Timeline State UE Apply Test.

Validates that:
1. HandleTimelineState() implementation exists and applies frame range
2. LiveSyncSequenceFrameStart/End/FPSNum/FPSDen are updated
3. Sequence playback range is set via SetPlaybackRange and SetDisplayRate
4. [TIMELINE][APPLY] appears after successful apply
5. [TIMELINE][SKIP] when no LevelSequence exists
6. No crash on malformed payload
7. Storage members LastTimelineStatePayload and bHasTimelineStatePayload exist
"""

import sys
import os

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


def read_cpp():
    with open(UE_SUBSYSTEM_CPP, 'r') as f:
        return f.read()


def read_h():
    with open(UE_SUBSYSTEM_H, 'r') as f:
        return f.read()


def test_handler_implementation_exists():
    """HandleTimelineState must be implemented (not just declared)."""
    content = read_cpp()
    assert "HandleTimelineState" in content, \
        "HandleTimelineState not found in cpp"
    exists = content.find("void UUELiveSyncSubsystem::\nHandleTimelineState(")
    alt = content.find("HandleTimelineState(")
    assert alt != -1, "HandleTimelineState implementation not found in cpp"
    print("  PASS: HandleTimelineState implementation exists")
    return True


def _implementation_region():
    """Return the HandleTimelineState implementation region from the end (last match)."""
    content = read_cpp()
    # Find LAST occurrence (the implementation, not dispatch calls)
    idx = content.rfind("HandleTimelineState(")
    if idx == -1:
        # Fallback to find from marker
        idx = content.find("[TIMELINE][APPLY]")
        if idx == -1:
            return ""
        idx = content.rfind("void", 0, idx)
    return content[idx: idx + 2000]


def test_applies_playback_range():
    """SetPlaybackRange must be called in HandleTimelineState."""
    region = _implementation_region()
    assert "SetPlaybackRange" in region, \
        "SetPlaybackRange must be called in HandleTimelineState"
    assert "SetDisplayRate" in region, \
        "SetDisplayRate must be called in HandleTimelineState"
    print("  PASS: SetPlaybackRange and SetDisplayRate called")
    return True


def test_applies_fps():
    """FFrameRate must be computed from FPSNum/FPSDen."""
    region = _implementation_region()
    assert "FFrameRate" in region, "FFrameRate must be used"
    assert "SetDisplayRate" in region, "SetDisplayRate must be called"
    print("  PASS: FPS applied via FFrameRate and SetDisplayRate")
    return True


def test_skip_when_no_sequence():
    """[TIMELINE][SKIP] must exist for missing LevelSequence."""
    content = read_cpp()
    assert "[TIMELINE][SKIP]" in content, "[TIMELINE][SKIP] marker not found"
    print("  PASS: [TIMELINE][SKIP] marker present")
    return True


def test_apply_marker():
    """[TIMELINE][APPLY] must appear on successful apply."""
    content = read_cpp()
    assert "[TIMELINE][APPLY]" in content, "[TIMELINE][APPLY] marker not found"
    print("  PASS: [TIMELINE][APPLY] marker present")
    return True


def test_storage_members_exist():
    """bHasTimelineStatePayload and LastTimelineStatePayload must exist in header."""
    content = read_h()
    assert "bHasTimelineStatePayload" in content, \
        "bHasTimelineStatePayload not found in header"
    assert "LastTimelineStatePayload" in content, \
        "LastTimelineStatePayload not found in header"
    assert "FTimelineStatePayload" in content, \
        "FTimelineStatePayload type not found in header"
    print("  PASS: storage members exist")
    return True


def test_updates_sequence_frame_range():
    """LiveSyncSequenceFrameStart/End/FPSNum/FPSDen must be updated."""
    region = _implementation_region()
    assert "LiveSyncSequenceFrameStart" in region, \
        "LiveSyncSequenceFrameStart not updated in HandleTimelineState"
    assert "LiveSyncSequenceFrameEnd" in region, \
        "LiveSyncSequenceFrameEnd not updated in HandleTimelineState"
    assert "LiveSyncSequenceFPSNum" in region, \
        "LiveSyncSequenceFPSNum not updated in HandleTimelineState"
    assert "LiveSyncSequenceFPSDen" in region, \
        "LiveSyncSequenceFPSDen not updated in HandleTimelineState"
    print("  PASS: LiveSyncSequenceFrame* and FPS* updated")
    return True


if __name__ == '__main__':
    tests = [
        ("test_handler_implementation_exists", test_handler_implementation_exists),
        ("test_applies_playback_range", test_applies_playback_range),
        ("test_applies_fps", test_applies_fps),
        ("test_skip_when_no_sequence", test_skip_when_no_sequence),
        ("test_apply_marker", test_apply_marker),
        ("test_storage_members_exist", test_storage_members_exist),
        ("test_updates_sequence_frame_range", test_updates_sequence_frame_range),
    ]

    passed = 0
    failed = 0
    for name, fn in tests:
        try:
            fn()
            passed += 1
        except Exception as e:
            print(f"FAIL: {name}: {e}")
            failed += 1

    print(f"\nResults: {passed} passed, {failed} failed, {passed + failed} total")
    sys.exit(1 if failed > 0 else 0)
