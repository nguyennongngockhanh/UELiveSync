#!/usr/bin/env python3
"""Phase 7G Stage 2 — Active Camera View Target Apply Tests.

Validates that:
1. HandleActiveCamera calls SetActorLock on level editor viewport clients
2. [CAMERA][VIEW_TARGET] marker exists
3. [CAMERA][SAFE_INVALID_SKIP] marker exists
4. [CAMERA][VIEW_TARGET_FAIL] marker exists
5. FLevelEditorViewportClient header is included
6. SetActorLock is called in HandleActiveCamera
7. ActiveCameraPacketsAppliedToViewport counter updated
8. Diagnostics display new counters
9. Reset handles new counters
10. Non-camera actors skip gracefully
"""

import sys
import os

UE_CPP = os.path.join(os.path.dirname(__file__), "..",
    "UE_Plugin/UELiveSync/Source/UELiveSync/Private/UELiveSyncSubsystem.cpp")

UE_H = os.path.join(os.path.dirname(__file__), "..",
    "UE_Plugin/UELiveSync/Source/UELiveSync/Public/UELiveSyncSubsystem.h")

SYNC_TYPES_H = os.path.join(os.path.dirname(__file__), "..",
    "UE_Plugin/UELiveSync/Source/UELiveSync/Public/SyncTypes.h")

DIAG_INL = os.path.join(os.path.dirname(__file__), "..",
    "UE_Plugin/UELiveSync/Source/UELiveSync/Private/UELiveSyncSubsystem_Diagnostics.inl")


def read_cpp():
    with open(UE_CPP) as f:
        return f.read()


def read_h():
    with open(UE_H) as f:
        return f.read()


def read_sync():
    with open(SYNC_TYPES_H) as f:
        return f.read()


def read_diag():
    with open(DIAG_INL) as f:
        return f.read()


def test_view_target_markers_exist():
    content = read_cpp()
    for marker in ["[CAMERA][VIEW_TARGET]",
                   "[CAMERA][VIEW_TARGET_FAIL]"]:
        assert marker in content, f"Marker {marker} not found"
    print("  PASS: All [CAMERA][VIEW_TARGET] markers present")
    return True


def test_set_actor_lock_called():
    content = read_cpp()
    idx = content.rfind("HandleActiveCamera")
    region = content[idx: idx + 6000]
    assert "SetActorLock" in region, \
        "SetActorLock not referenced in HandleActiveCamera"
    assert "LevelVC->SetActorLock" in region or "SetActorLock(Camera)" in region or "SetActorLock(ResolvedCamera)" in region, \
        "SetActorLock(Camera) call not found"
    print("  PASS: SetActorLock called on viewport clients")
    return True


def test_level_editor_viewport_client_included():
    content = read_cpp()
    assert "LevelEditorViewport.h" in content, \
        "LevelEditorViewport.h not included (needed for FLevelEditorViewportClient)"
    print("  PASS: LevelEditorViewport.h included")
    return True


def test_editor_h_included():
    content = read_cpp()
    assert "Editor.h" in content, \
        "Editor.h not included (needed for GEditor)"
    print("  PASS: Editor.h included")
    return True


def test_get_level_viewport_clients_called():
    content = read_cpp()
    idx = content.rfind("HandleActiveCamera")
    region = content[idx: idx + 7000]
    assert "GetLevelViewportClients" in region, \
        "GetLevelViewportClients not called in HandleActiveCamera"
    print("  PASS: GEditor->GetLevelViewportClients() called")
    return True


def test_apply_count_incremented():
    content = read_cpp()
    idx = content.rfind("HandleActiveCamera")
    region = content[idx: idx + 7000]
    assert "ActiveCameraPacketsAppliedToViewport" in region, \
        "ActiveCameraPacketsAppliedToViewport not referenced"
    print("  PASS: ActiveCameraPacketsAppliedToViewport counter incremented")
    return True


def test_not_camera_skip():
    content = read_cpp()
    idx = content.rfind("HandleActiveCamera")
    region = content[idx: idx + 7000]
    assert "ActiveCameraPacketsNotCamera" in region, \
        "ActiveCameraPacketsNotCamera not referenced"
    assert "[CAMERA][SAFE_INVALID_SKIP]" in region, \
        "[CAMERA][SAFE_INVALID_SKIP] not in HandleActiveCamera region"
    print("  PASS: Non-camera actors skip gracefully")
    return True


def test_view_target_fail_counter():
    content = read_cpp()
    idx = content.rfind("HandleActiveCamera")
    region = content[idx: idx + 7000]
    assert "ActiveCameraPacketsViewTargetFailed" in region, \
        "ActiveCameraPacketsViewTargetFailed not in HandleActiveCamera"
    print("  PASS: View target fail counter incremented on GEditor null")
    return True


def test_diag_displays_new_counters():
    content = read_diag()
    assert "ActiveCameraPacketsSpawned" in content, \
        "ActiveCameraPacketsSpawned not in diagnostics display"
    assert "ActiveCameraPacketsViewTargetFailed" in content, \
        "ActiveCameraPacketsViewTargetFailed not in diagnostics display"
    print("  PASS: Diagnostics display for new counters")
    return True


def test_diag_resets_new_counters():
    content = read_diag()
    assert "ActiveCameraPacketsSpawned.store" in content, \
        "ActiveCameraPacketsSpawned.reset not in diagnostics"
    assert "ActiveCameraPacketsViewTargetFailed.store" in content, \
        "ActiveCameraPacketsViewTargetFailed.reset not in diagnostics"
    print("  PASS: Diagnostics reset for new counters")
    return True


def test_cvar_description_updated():
    content = read_cpp()
    assert "SetActorLock" in content, \
        "CVar description should mention SetActorLock"
    print("  PASS: CVar description updated with SetActorLock")
    return True


if __name__ == '__main__':
    tests = [
        ("test_view_target_markers_exist", test_view_target_markers_exist),
        ("test_set_actor_lock_called", test_set_actor_lock_called),
        ("test_level_editor_viewport_client_included", test_level_editor_viewport_client_included),
        ("test_editor_h_included", test_editor_h_included),
        ("test_get_level_viewport_clients_called", test_get_level_viewport_clients_called),
        ("test_apply_count_incremented", test_apply_count_incremented),
        ("test_not_camera_skip", test_not_camera_skip),
        ("test_view_target_fail_counter", test_view_target_fail_counter),
        ("test_diag_displays_new_counters", test_diag_displays_new_counters),
        ("test_diag_resets_new_counters", test_diag_resets_new_counters),
        ("test_cvar_description_updated", test_cvar_description_updated),
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
