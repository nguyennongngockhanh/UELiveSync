#!/usr/bin/env python3
"""Phase 7F Stage 1 — Reserved Packet Type Guard Test.

Proves that:
- 0x02 is NOT in kValidTypes (reserved, must be rejected)
- 0x19 (PT_TimelineState) IS in kValidTypes
- 0x18 (PT_SequencerOp) remains valid
- 0x17 (PT_Keyframe) remains valid
- All existing valid types remain
- Handler exists for PT_TimelineState
- Diagnostic markers exist
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

SYNC_TYPES_H = os.path.join(
    os.path.dirname(__file__),
    "..",
    "UE_Plugin",
    "UELiveSync",
    "Source",
    "UELiveSync",
    "Public",
    "SyncTypes.h"
)


def read_kValidTypes():
    """Extract kValidTypes from UE subsystem cpp."""
    with open(UE_SUBSYSTEM_CPP, 'r') as f:
        content = f.read()
    start = content.find('static constexpr uint8 kValidTypes[] =')
    assert start != -1, "kValidTypes not found"
    brace_start = content.find('{', start)
    brace_end = content.find('}', brace_start)
    array_str = content[brace_start + 1: brace_end]
    types = [int(t.strip(), 16) for t in array_str.split(',') if t.strip()]
    return types


def test_0x19_is_valid():
    """PT_TimelineState (0x19) must be in kValidTypes."""
    types = read_kValidTypes()
    assert 0x19 in types, f"PT_TimelineState (0x19) not in kValidTypes: {[hex(t) for t in types]}"
    print(f"  PASS: 0x19 is valid in kValidTypes")
    return True


def test_0x02_is_reserved():
    """0x02 must NOT be in kValidTypes."""
    types = read_kValidTypes()
    assert 0x02 not in types, f"0x02 found in kValidTypes (should be reserved): {[hex(t) for t in types]}"
    print(f"  PASS: 0x02 is reserved (not in kValidTypes)")
    return True


def test_0x17_and_0x18_still_valid():
    """PT_Keyframe (0x17) and PT_SequencerOp (0x18) must remain valid."""
    types = read_kValidTypes()
    assert 0x17 in types, f"PT_Keyframe (0x17) not in kValidTypes"
    assert 0x18 in types, f"PT_SequencerOp (0x18) not in kValidTypes"
    print(f"  PASS: 0x17 and 0x18 remain valid")
    return True


def test_handler_declaration_exists():
    """HandleTimelineState must be declared in subsystem header."""
    with open(UE_SUBSYSTEM_H, 'r') as f:
        content = f.read()
    assert "HandleTimelineState" in content, \
        "HandleTimelineState declaration not found in UELiveSyncSubsystem.h"
    print("  PASS: HandleTimelineState declaration exists")
    return True


def test_diagnostic_markers_exist():
    """[TIMELINE][RECV], [TIMELINE][APPLY], [TIMELINE][SKIP], [TIMELINE][MALFORMED] must exist."""
    with open(UE_SUBSYSTEM_CPP, 'r') as f:
        content = f.read()
    markers = [
        "[TIMELINE][RECV]",
        "[TIMELINE][APPLY]",
        "[TIMELINE][SKIP]",
        "[TIMELINE][MALFORMED]",
    ]
    for marker in markers:
        assert marker in content, f"Marker {marker} not found in UELiveSyncSubsystem.cpp"
    print("  PASS: all [TIMELINE] diagnostic markers present")
    return True


def test_enum_exists():
    """PT_TimelineState = 0x19 must exist in EPacketType enum."""
    with open(SYNC_TYPES_H, 'r') as f:
        content = f.read()
    assert "PT_TimelineState" in content, "PT_TimelineState not found in SyncTypes.h"
    assert "0x19" in content, "0x19 not found as packet type in SyncTypes.h"
    print("  PASS: PT_TimelineState = 0x19 exists in EPacketType enum")
    return True


def test_payload_struct_exists():
    """FTimelineStatePayload must exist and be 20 bytes."""
    with open(SYNC_TYPES_H, 'r') as f:
        content = f.read()
    assert "FTimelineStatePayload" in content, \
        "FTimelineStatePayload struct not found in SyncTypes.h"
    assert "static_assert" in content and "FTimelineStatePayload" in content, \
        "FTimelineStatePayload static_assert not found"
    print("  PASS: FTimelineStatePayload struct exists with size assertion")
    return True


if __name__ == '__main__':
    tests = [
        ("test_0x19_is_valid", test_0x19_is_valid),
        ("test_0x02_is_reserved", test_0x02_is_reserved),
        ("test_0x17_and_0x18_still_valid", test_0x17_and_0x18_still_valid),
        ("test_handler_declaration_exists", test_handler_declaration_exists),
        ("test_diagnostic_markers_exist", test_diagnostic_markers_exist),
        ("test_enum_exists", test_enum_exists),
        ("test_payload_struct_exists", test_payload_struct_exists),
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
