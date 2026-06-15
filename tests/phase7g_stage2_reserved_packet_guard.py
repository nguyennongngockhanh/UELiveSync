#!/usr/bin/env python3
"""Phase 7G Stage 2 — Reserved Packet Type Guard Test.

Proves that:
- 0x1A (PT_PlaybackTransport) still in kValidTypes
- 0x02 still reserved/invalid
- 0x15 (PT_ActiveCamera) still valid
- 0x17 (PT_Keyframe) still valid
- 0x18 (PT_SequencerOp) still valid
- 0x19 (PT_TimelineState) still valid
- LSP_Camera = 0x05 exists separately from packet types
"""

import sys
import os

UE_CPP = os.path.join(os.path.dirname(__file__), "..",
    "UE_Plugin/UELiveSync/Source/UELiveSync/Private/UELiveSyncSubsystem.cpp")

SYNC_TYPES_H = os.path.join(os.path.dirname(__file__), "..",
    "UE_Plugin/UELiveSync/Source/UELiveSync/Public/SyncTypes.h")


def read_kValidTypes():
    with open(UE_CPP, 'r') as f:
        content = f.read()
    start = content.find('static constexpr uint8 kValidTypes[] =')
    assert start != -1, "kValidTypes not found"
    brace_start = content.find('{', start)
    brace_end = content.find('}', brace_start)
    array_str = content[brace_start + 1: brace_end]
    types = [int(t.strip(), 16) for t in array_str.split(',') if t.strip()]
    return types


def test_0x1A_still_valid():
    types = read_kValidTypes()
    assert 0x1A in types, f"0x1A removed from kValidTypes"
    print("  PASS: 0x1A (PT_PlaybackTransport) still valid")
    return True


def test_0x15_still_valid():
    types = read_kValidTypes()
    assert 0x15 in types, f"0x15 (PT_ActiveCamera) removed"
    print("  PASS: 0x15 (PT_ActiveCamera) still valid")
    return True


def test_0x17_still_valid():
    types = read_kValidTypes()
    assert 0x17 in types, f"0x17 (PT_Keyframe) removed"
    print("  PASS: 0x17 (PT_Keyframe) still valid")
    return True


def test_0x18_still_valid():
    types = read_kValidTypes()
    assert 0x18 in types, f"0x18 (PT_SequencerOp) removed"
    print("  PASS: 0x18 (PT_SequencerOp) still valid")
    return True


def test_0x19_still_valid():
    types = read_kValidTypes()
    assert 0x19 in types, f"0x19 (PT_TimelineState) removed"
    print("  PASS: 0x19 (PT_TimelineState) still valid")
    return True


def test_0x02_reserved():
    types = read_kValidTypes()
    assert 0x02 not in types, f"0x02 found in kValidTypes (should be reserved)"
    print("  PASS: 0x02 remains reserved/invalid")
    return True


def test_0x16_missing_or_fbx():
    types = read_kValidTypes()
    # 0x16 may or may not be in kValidTypes (PT_FBXImportRequest)
    # This test just documents the current state
    has_0x16 = 0x16 in types
    print(f"  PASS: 0x16 (PT_FBXImportRequest) in kValidTypes: {has_0x16} (documenting, not asserting)")
    return True


def test_lsp_camera_in_sync_types():
    with open(SYNC_TYPES_H, 'r') as f:
        content = f.read()
    assert "LSP_Camera" in content, "LSP_Camera not found in SyncTypes.h"
    assert "0x05" in content, "0x05 not found in SyncTypes.h"
    # Verify LSP_Camera and 0x05 are on the same line
    found = False
    for line in content.split('\n'):
        if 'LSP_Camera' in line and '0x05' in line:
            found = True
            break
    assert found, "LSP_Camera = 0x05 not found as value"
    print("  PASS: LSP_Camera = 0x05 in ELiveSyncPrimitiveType enum")
    return True


def test_lsp_camera_not_packet_type():
    """LSP_Camera = 0x05 must NOT be conflated with packet type 0x05 (PT_Material)."""
    with open(SYNC_TYPES_H, 'r') as f:
        content = f.read()
    # LSP_Camera should be in ELiveSyncPrimitiveType enum
    # PT_Material = 0x05 should be in EPacketType enum
    assert "PT_Material" in content, "PT_Material not found"
    epacket_idx = content.find("enum class EPacketType")
    eprim_idx = content.find("enum ELiveSyncPrimitiveType")
    assert eprim_idx > epacket_idx, \
        "ELiveSyncPrimitiveType should be after EPacketType"
    # LSP_Camera must be inside the ELiveSyncPrimitiveType scope
    lsp_region = content[eprim_idx:eprim_idx + 200]
    assert "LSP_" in lsp_region, "LSP_ not in ELiveSyncPrimitiveType region"
    print("  PASS: LSP_Camera is in ELiveSyncPrimitiveType, separate from EPacketType")
    return True


if __name__ == '__main__':
    tests = [
        ("test_0x1A_still_valid", test_0x1A_still_valid),
        ("test_0x15_still_valid", test_0x15_still_valid),
        ("test_0x17_still_valid", test_0x17_still_valid),
        ("test_0x18_still_valid", test_0x18_still_valid),
        ("test_0x19_still_valid", test_0x19_still_valid),
        ("test_0x02_reserved", test_0x02_reserved),
        ("test_0x16_missing_or_fbx", test_0x16_missing_or_fbx),
        ("test_lsp_camera_in_sync_types", test_lsp_camera_in_sync_types),
        ("test_lsp_camera_not_packet_type", test_lsp_camera_not_packet_type),
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
