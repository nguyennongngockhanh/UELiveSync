#!/usr/bin/env python3
"""Phase 7G Stage 2 — Camera Actor Spawn Source Tests.

Validates that:
1. LSP_Camera = 0x05 exists in ELiveSyncPrimitiveType enum
2. HandleActiveCamera auto-spawns ACameraActor when GUID missing
3. [CAMERA][SPAWN] marker exists
4. [CAMERA][SPAWN_FAIL] marker exists
5. ActiveCameraPacketsSpawned counter exists
6. 0x02 remains reserved/invalid
7. Existing primitive types unchanged
"""

import sys
import os

UE_CPP = os.path.join(os.path.dirname(__file__), "..",
    "UE_Plugin/UELiveSync/Source/UELiveSync/Private/UELiveSyncSubsystem.cpp")

UE_H = os.path.join(os.path.dirname(__file__), "..",
    "UE_Plugin/UELiveSync/Source/UELiveSync/Public/UELiveSyncSubsystem.h")

SYNC_TYPES_H = os.path.join(os.path.dirname(__file__), "..",
    "UE_Plugin/UELiveSync/Source/UELiveSync/Public/SyncTypes.h")


def read_cpp():
    with open(UE_CPP) as f:
        return f.read()


def read_h():
    with open(UE_H) as f:
        return f.read()


def read_sync():
    with open(SYNC_TYPES_H) as f:
        return f.read()


def test_lsp_camera_enum_exists():
    content = read_sync()
    # Check LSP_Camera = 0x05 exists in enum
    lines = content.split('\n')
    found = False
    for line in lines:
        if 'LSP_Camera' in line or ('LSP_' in line and 'Camera' in line):
            if '0x05' in line:
                found = True
                break
    assert found, "LSP_Camera = 0x05 line not found"
    print("  PASS: LSP_Camera = 0x05 exists in ELiveSyncPrimitiveType")
    return True


def test_existing_primitives_unchanged():
    content = read_sync()
    assert "LSP_Cube     = 0x00" in content
    assert "LSP_Sphere   = 0x01" in content
    assert "LSP_Cylinder = 0x02" in content
    assert "LSP_Plane    = 0x03" in content
    assert "LSP_Empty    = 0x04" in content
    print("  PASS: Existing primitive types unchanged (0x00-0x04)")
    return True


def test_camera_spawn_marker_exists():
    content = read_cpp()
    assert "[CAMERA][SPAWN]" in content, \
        "[CAMERA][SPAWN] marker not found in cpp"
    assert "[CAMERA][SPAWN_FAIL]" in content, \
        "[CAMERA][SPAWN_FAIL] marker not found in cpp"
    print("  PASS: [CAMERA][SPAWN] and [CAMERA][SPAWN_FAIL] markers exist")
    return True


def test_camera_active_recv_marker_exists():
    content = read_cpp()
    assert "[CAMERA][ACTIVE_RECV]" in content, \
        "[CAMERA][ACTIVE_RECV] marker not found"
    print("  PASS: [CAMERA][ACTIVE_RECV] marker exists")
    return True


def test_acameraactor_spawn_in_handle():
    content = read_cpp()
    # Find the function definition (not the dispatch call or log messages)
    idx = content.rfind("\nHandleActiveCamera(")
    if idx < 0:
        idx = content.rfind("HandleActiveCamera(")
    region = content[idx: idx + 2000]
    assert "ACameraActor" in region, \
        "ACameraActor not referenced in HandleActiveCamera"
    assert "SpawnActorDeferred<ACameraActor>" in region, \
        "SpawnActorDeferred<ACameraActor> not found in HandleActiveCamera"
    print("  PASS: HandleActiveCamera contains SpawnActorDeferred<ACameraActor>")
    return True


def test_camera_spawn_tags_and_caches():
    content = read_cpp()
    idx = content.rfind("HandleActiveCamera")
    region = content[idx: idx + 2500]
    assert "LiveSync_GUID=" in region, \
        "GUID tagging not found in HandleActiveCamera"
    assert "ActorCache.Add" in region, \
        "ActorCache.Add not found in HandleActiveCamera"
    print("  PASS: Camera actor is tagged and cached on spawn")
    return True


def test_active_camera_spawned_counter():
    content = read_sync()
    assert "ActiveCameraPacketsSpawned" in content, \
        "ActiveCameraPacketsSpawned counter not found"
    print("  PASS: ActiveCameraPacketsSpawned counter exists")
    return True


def test_active_camera_view_target_failed_counter():
    content = read_sync()
    assert "ActiveCameraPacketsViewTargetFailed" in content, \
        "ActiveCameraPacketsViewTargetFailed counter not found"
    print("  PASS: ActiveCameraPacketsViewTargetFailed counter exists")
    return True


def test_kvalidtypes_0x1A_still_valid():
    content = read_cpp()
    idx = content.find("kValidTypes")
    end = content.find("};", idx)
    array = content[idx:end]
    assert "0x1A" in array, "0x1A removed from kValidTypes"
    print("  PASS: 0x1A (PT_PlaybackTransport) still valid")
    return True


def test_kvalidtypes_0x02_reserved():
    content = read_cpp()
    idx = content.find("kValidTypes")
    end = content.find("};", idx)
    array = content[idx:end]
    assert "0x02" not in array, "0x02 found in kValidTypes (should be reserved)"
    print("  PASS: 0x02 remains reserved/invalid")
    return True


if __name__ == '__main__':
    tests = [
        ("test_lsp_camera_enum_exists", test_lsp_camera_enum_exists),
        ("test_existing_primitives_unchanged", test_existing_primitives_unchanged),
        ("test_camera_spawn_marker_exists", test_camera_spawn_marker_exists),
        ("test_camera_active_recv_marker_exists", test_camera_active_recv_marker_exists),
        ("test_acameraactor_spawn_in_handle", test_acameraactor_spawn_in_handle),
        ("test_camera_spawn_tags_and_caches", test_camera_spawn_tags_and_caches),
        ("test_active_camera_spawned_counter", test_active_camera_spawned_counter),
        ("test_active_camera_view_target_failed_counter", test_active_camera_view_target_failed_counter),
        ("test_kvalidtypes_0x1A_still_valid", test_kvalidtypes_0x1A_still_valid),
        ("test_kvalidtypes_0x02_reserved", test_kvalidtypes_0x02_reserved),
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
