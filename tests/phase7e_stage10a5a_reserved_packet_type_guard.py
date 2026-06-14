#!/usr/bin/env python3
"""Phase 7E Stage 10A.5A -- Reserved 0x02 guard test.

Proves that:
- 0x01 is valid as PT_Transform
- 0x02 is NOT in kValidTypes (reserved, must be rejected)
- 0x03 is valid as PT_Create
- PT_Keyframe (0x17) and PT_SequencerOp (0x18) remain valid
- no existing valid packet type is removed
- no duplicate packet IDs
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


def read_kValidTypes():
    """Extract kValidTypes from UELiveSyncSubsystem.cpp."""
    with open(UE_SUBSYSTEM_CPP, 'r') as f:
        content = f.read()

    start = content.find('static constexpr uint8 kValidTypes[] =')
    assert start != -1, "kValidTypes not found in UELiveSyncSubsystem.cpp"

    brace_start = content.find('{', start)
    brace_end = content.find('}', brace_start)

    array_str = content[brace_start + 1: brace_end]

    types = []
    for token in array_str.split(','):
        token = token.strip()
        if token.startswith('0x') or token.startswith('0X'):
            types.append(int(token, 16))
        elif token.isdigit():
            types.append(int(token))

    return types


def test_0x01_is_valid_transform():
    """0x01 must be in kValidTypes (PT_Transform)."""
    types = read_kValidTypes()
    assert 0x01 in types, f"0x01 not in kValidTypes: {[hex(t) for t in types]}"
    print("PASS: test_0x01_is_valid_transform")
    return True


def test_0x02_is_reserved_and_rejected():
    """0x02 must NOT be in kValidTypes (reserved/unused)."""
    types = read_kValidTypes()
    assert 0x02 not in types, f"0x02 must NOT be in kValidTypes (reserved): {[hex(t) for t in types]}"
    print("PASS: test_0x02_is_reserved_and_rejected")
    return True


def test_0x03_is_valid_create():
    """0x03 must be in kValidTypes (PT_Create)."""
    types = read_kValidTypes()
    assert 0x03 in types, f"0x03 not in kValidTypes: {[hex(t) for t in types]}"
    print("PASS: test_0x03_is_valid_create")
    return True


def test_no_existing_type_removed():
    """All known packet types must still be present."""
    types = read_kValidTypes()
    known_types = [
        0x01,  # PT_Transform
        0x03,  # PT_Create
        0x04,  # PT_Delete
        0x05,  # PT_Material
        0x06,  # PT_Mesh
        0x07,  # PT_Heartbeat
        0x08,  # PT_AssetDef
        0x09,  # PT_BeginSnapshot
        0x0A,  # PT_EndSnapshot
        0x0B,  # PT_Visibility
        0x0C,  # PT_Rename
        0x0D,  # PT_Hierarchy
        0x0E,  # PT_Delete_V5
        0x0F,  # PT_Collection
        0x11,  # PT_CapabilityAnnounce
        0x12,  # PT_CapabilityResponse
        0x13,  # PT_Timeline
        0x14,  # PT_PlaybackState
        0x15,  # PT_ActiveCamera
        0x16,  # PT_FBXImportRequest
        0x17,  # PT_Keyframe
        0x18,  # PT_SequencerOp
    ]

    missing = [hex(t) for t in known_types if t not in types]
    assert not missing, f"Missing packet types in kValidTypes: {missing}"

    print("PASS: test_no_existing_type_removed")
    return True


def test_no_duplicate_packet_ids():
    """kValidTypes must not contain duplicates."""
    types = read_kValidTypes()
    seen = set()
    for t in types:
        assert t not in seen, f"Duplicate packet type: 0x{t:02x}"
        seen.add(t)

    print("PASS: test_no_duplicate_packet_ids")
    return True


def test_keyframe_and_sequencer_op_valid():
    """PT_Keyframe (0x17) and PT_SequencerOp (0x18) must remain valid."""
    types = read_kValidTypes()

    assert 0x17 in types, f"PT_Keyframe (0x17) not in kValidTypes: {[hex(t) for t in types]}"
    assert 0x18 in types, f"PT_SequencerOp (0x18) not in kValidTypes: {[hex(t) for t in types]}"

    print("PASS: test_keyframe_and_sequencer_op_valid")
    return True


if __name__ == '__main__':
    tests = [
        test_0x01_is_valid_transform,
        test_0x02_is_reserved_and_rejected,
        test_0x03_is_valid_create,
        test_no_existing_type_removed,
        test_no_duplicate_packet_ids,
        test_keyframe_and_sequencer_op_valid,
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
