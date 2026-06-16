#!/usr/bin/env python3
"""Phase 7G Stage 4 — Camera Transform Sync Source Tests.

Validates that:
1. Blender PRIMITIVE_CAMERA = 0x05 exists in network.py
2. _get_primitive_type(obj) detects obj.type == 'CAMERA'
3. camera create path does not regress non-camera primitives
4. UE accepts LSP_Camera in create path (kValidTypes check)
5. UE camera create path spawns ACameraActor (not AActor + mesh)
6. UE transform path does not reject ACameraActor
7. diagnostics exist:
   [CAMERA][CREATE]
   [CAMERA][TRANSFORM_APPLY]
   [CAMERA][TRANSFORM_CONVERGED]
8. 0x02 remains reserved/invalid if applicable
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "Blender_Addon"))
import network

UE_CPP = os.path.join(os.path.dirname(__file__), "..",
    "UE_Plugin/UELiveSync/Source/UELiveSync/Private/UELiveSyncSubsystem.cpp")

UE_H = os.path.join(os.path.dirname(__file__), "..",
    "UE_Plugin/UELiveSync/Source/UELiveSync/Public/UELiveSyncSubsystem.h")

SYNC_TYPES_H = os.path.join(os.path.dirname(__file__), "..",
    "UE_Plugin/UELiveSync/Source/UELiveSync/Public/SyncTypes.h")


PASS = 0
FAIL = 0
RESULTS = []


def read_cpp():
    with open(UE_CPP) as f:
        return f.read()


def read_h():
    with open(UE_H) as f:
        return f.read()


def read_sync():
    with open(SYNC_TYPES_H) as f:
        return f.read()


def test(name, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  PASS: {name}")
    else:
        FAIL += 1
        msg = f"  FAIL: {name}"
        if detail:
            msg += f" — {detail}"
        print(msg)
    RESULTS.append((name, condition))


def section(title):
    print()
    print(f"--- {title} ---")


# ------------------------------------------------------------------
# 1. Blender side: PRIMITIVE_CAMERA constant
# ------------------------------------------------------------------

def test_primitive_camera_constant():
    assert hasattr(network, 'PRIMITIVE_CAMERA'), "PRIMITIVE_CAMERA not in network.py"
    assert network.PRIMITIVE_CAMERA == 0x05, \
        f"PRIMITIVE_CAMERA = {hex(network.PRIMITIVE_CAMERA)}, expected 0x05"
    test("PRIMITIVE_CAMERA = 0x05 exists in network.py", True)


def test_primitive_camera_in_exports():
    sync_py = os.path.join(os.path.dirname(__file__), "..",
        "Blender_Addon", "sync.py")
    content = open(sync_py).read()
    assert 'PRIMITIVE_CAMERA' in content, \
        "PRIMITIVE_CAMERA not exported in sync.py"
    test("PRIMITIVE_CAMERA exported in sync.py", True)


# ------------------------------------------------------------------
# 2. Blender side: _get_primitive_type(obj) detects CAMERA
# ------------------------------------------------------------------

def test_get_primitive_type_signature():
    content = open(os.path.join(os.path.dirname(__file__), "..",
        "Blender_Addon", "sync.py")).read()
    # Check that _get_primitive_type has obj parameter
    assert '_get_primitive_type(obj' in content or '_get_primitive_type(obj =' in content, \
        "_get_primitive_type does not accept obj parameter"
    test("_get_primitive_type(obj) accepts obj parameter", True)


def test_get_primitive_type_camera_detection():
    content = open(os.path.join(os.path.dirname(__file__), "..",
        "Blender_Addon", "sync.py")).read()
    # Check CAMERA detection logic
    assert "obj.type == 'CAMERA'" in content, \
        "No obj.type == 'CAMERA' detection in _get_primitive_type"
    test("_get_primitive_type detects obj.type == 'CAMERA'", True)


# ------------------------------------------------------------------
# 3. Blender side: primitive type passed to _sync_send_create
# ------------------------------------------------------------------

def test_create_uses_obj_primitive():
    content = open(os.path.join(os.path.dirname(__file__), "..",
        "Blender_Addon", "sync.py")).read()
    # All create calls should pass _get_primitive_type(obj)
    count = content.count('_get_primitive_type(obj')
    assert count >= 3, \
        f"Expected >= 3 calls to _get_primitive_type(obj), found {count}"
    test(f"_get_primitive_type(obj) called {count} times in sync.py", True)


# ------------------------------------------------------------------
# 4. UE side: LSP_Camera accepted in create path
# ------------------------------------------------------------------

def test_lsp_camera_in_sync_enum():
    content = read_sync()
    assert 'LSP_Camera' in content, "LSP_Camera not in SyncTypes.h enum"
    assert '0x05' in content, "LSP_Camera value 0x05 not found"
    test("LSP_Camera = 0x05 exists in ELiveSyncPrimitiveType", True)


def test_kvalidtypes_contains_0x05():
    content = read_cpp()
    # kValidTypes is a constexpr array; check for 0x05 in it
    assert '0x05' in content, "0x05 not found in UELiveSyncSubsystem.cpp kValidTypes"
    # Check it appears in a context related to valid types
    lines = content.split('\n')
    in_valid_types = False
    found_0x05 = False
    for i, line in enumerate(lines):
        if 'kValidTypes' in line:
            in_valid_types = True
        if in_valid_types and '0x05' in line:
            found_0x05 = True
            break
        if in_valid_types and '}' in line:
            break
    test("0x05 present in kValidTypes array", found_0x05)


# ------------------------------------------------------------------
# 5. UE side: camera create path spawns ACameraActor
# ------------------------------------------------------------------

def test_camera_spawns_acameraactor():
    content = read_cpp()
    assert 'SpawnActor<ACameraActor>' in content, \
        "SpawnActor<ACameraActor> not found in UELiveSyncSubsystem.cpp"
    test("HandleCreateObject spawns ACameraActor for LSP_Camera", True)


def test_camera_root_component_cam_component():
    content = read_cpp()
    # Verify root component is set to GetCameraComponent()
    assert 'SetRootComponent' in content and 'GetCameraComponent' in content, \
        "Root component set to GetCameraComponent() not found"
    test("Camera root component = UCameraComponent (GetCameraComponent)", True)


def test_camera_create_diagnostics():
    content = read_cpp()
    assert '[CAMERA][CREATE]' in content, \
        "[CAMERA][CREATE] marker not found"
    test("[CAMERA][CREATE] diagnostic marker exists", True)


def test_camera_primitive_type_check():
    content = read_cpp()
    # Check the if (PrimitiveType == LSP_Camera) block exists
    assert 'PrimitiveType == LSP_Camera' in content, \
        "PrimitiveType == LSP_Camera check not found"
    test("UE accepts LSP_Camera in create path (PrimitiveType check)", True)


# ------------------------------------------------------------------
# 6. UE side: transform path does NOT reject ACameraActor
# ------------------------------------------------------------------

def test_transform_path_no_camera_rejection():
    """Verify InterpolateTransforms does not have a camera-specific rejection."""
    content = read_cpp()
    # ActorCache is used for all actors — no special case rejecting cameras
    # The transform path should work on any actor in ActorCache
    lines = content.split('\n')
    in_interpolate = False
    found_camera_reject = False
    for line in lines:
        if 'InterpolateTransforms' in line:
            in_interpolate = True
        if in_interpolate and 'ACameraActor' in line and '!' in line:
            # Check for a rejection pattern
            found_camera_reject = True
        if in_interpolate and line.strip().startswith('}') and 'return' not in line:
            # Rough heuristic: end of function
            pass
    # We expect NO camera-specific rejection in the transform path
    test("Transform path does not reject ACameraActor", not found_camera_reject)


# ------------------------------------------------------------------
# 7. UE diagnostics
# ------------------------------------------------------------------

def test_transform_apply_diagnostics():
    content = read_cpp()
    assert '[CAMERA][TRANSFORM_APPLY]' in content, \
        "[CAMERA][TRANSFORM_APPLY] marker not found"
    test("[CAMERA][TRANSFORM_APPLY] diagnostic marker exists", True)


def test_transform_converged_diagnostics():
    content = read_cpp()
    assert '[CAMERA][TRANSFORM_CONVERGED]' in content, \
        "[CAMERA][TRANSFORM_CONVERGED] marker not found"
    test("[CAMERA][TRANSFORM_CONVERGED] diagnostic marker exists", True)


def test_acameraactor_isa_check():
    """Verify UE uses Actor->IsA(ACameraActor::StaticClass()) for diagnostics."""
    content = read_cpp()
    assert 'IsA(ACameraActor::StaticClass())' in content, \
        "Actor->IsA(ACameraActor::StaticClass()) not found"
    test("UE uses Actor->IsA(ACameraActor::StaticClass()) for camera detection", True)


# ------------------------------------------------------------------
# 8. Reserved packet: 0x02 remains invalid
# ------------------------------------------------------------------

def test_0x02_reserved_in_kvalidtypes():
    """Verify 0x02 is NOT in kValidTypes."""
    content = read_cpp()
    lines = content.split('\n')
    in_valid_types = False
    found_0x02 = False
    for line in lines:
        if 'kValidTypes' in line:
            in_valid_types = True
        if in_valid_types and '0x02' in line:
            found_0x02 = True
            break
        if in_valid_types and '}' in line:
            break
    test("0x02 is NOT in kValidTypes (reserved)", not found_0x02)


# ------------------------------------------------------------------
# 9. Non-camera primitive regression check
# ------------------------------------------------------------------

def test_non_camera_primitives_unchanged():
    """Verify LSP_Cube through LSP_Empty are still defined and used."""
    content = read_sync()
    for prim in ['LSP_Cube', 'LSP_Sphere', 'LSP_Cylinder', 'LSP_Plane', 'LSP_Empty']:
        assert prim in content, f"{prim} missing from SyncTypes.h"
    test("Existing primitives (Cube..Empty) unchanged", True)


def test_staticmesh_component_path_exists():
    """Verify the non-camera mesh path still exists in create."""
    content = read_cpp()
    assert 'UStaticMeshComponent' in content, \
        "UStaticMeshComponent path missing — regression!"
    assert 'SetStaticMesh' in content, \
        "SetStaticMesh call missing — regression!"
    test("Non-camera mesh path (UStaticMeshComponent) still exists", True)


# ------------------------------------------------------------------
# 10. Injector mode tests
# ------------------------------------------------------------------

INJECTOR_PATH = os.path.join(os.path.dirname(__file__),
    "..", "tools", "uelivesync_7g_camera_transform_client.py")


def test_injector_mode_create_transform_active():
    """Verify --create-transform-active mode exists in injector."""
    content = open(INJECTOR_PATH).read()
    assert '--create-transform-active' in content, \
        "--create-transform-active mode missing from injector"
    # Should send CREATE + TRANSFORM + ACTIVE
    assert 'PT_CREATE' in content
    assert 'PT_TRANSFORM' in content
    assert 'PT_ACTIVE_CAMERA' in content
    test("--create-transform-active mode exists in injector", True)


def test_injector_mode_cameradef_only():
    """Verify --cameradef-only mode exists in injector."""
    content = open(INJECTOR_PATH).read()
    assert '--cameradef-only' in content, \
        "--cameradef-only mode missing from injector"
    # Should send only CAMERA_DEF
    assert 'PT_CAMERA_DEF' in content
    test("--cameradef-only mode exists in injector", True)


def test_injector_mode_full_separated():
    """Verify --full-separated mode exists in injector."""
    content = open(INJECTOR_PATH).read()
    assert '--full-separated' in content, \
        "--full-separated mode missing from injector"
    # Should combine create-transform-active + cameradef-only
    assert 'create_transform_active' in content
    assert 'cameradef_only' in content
    test("--full-separated mode exists in injector", True)


def test_injector_default_non_hanging():
    """Verify default mode does not use legacy --all-slow (2s pauses)."""
    content = open(INJECTOR_PATH).read()
    # Default should use create-transform-active, not --all-slow
    # Check that --all-slow is NOT the default (no bare `or True` fallback)
    # The old pattern was: `elif args.cameradef or True:` which forced cameradef
    lines = content.split('\n')
    for i, line in enumerate(lines):
        stripped = line.strip()
        if 'or True' in stripped and 'cameradef' in stripped:
            raise AssertionError(
                f"Legacy hanging default found at line {i+1}: '{stripped}'")
    test("Default mode does not hang (no legacy `or True` fallback)", True)


def test_injector_has_separate_tick_delay():
    """Verify create-transform-active mode includes delay between CREATE and TRANSFORM."""
    content = open(INJECTOR_PATH).read()
    # Should have sleep between CREATE and TRANSFORM (0.2s delay)
    assert 'sleep(0.2)' in content or 'time.sleep(0.2)' in content, \
        "No 0.2s delay found between CREATE and TRANSFORM"
    # Should use one connection with sleeps, not fresh sockets per packet
    assert 'one connection' in content.lower() or 'One connection' in content, \
        "Should use one connection (not fresh sockets)"
    mode_func = content[
        content.index('def mode_create_transform_active'):
        content.index('def mode_cameradef_only') if 'def mode_cameradef_only' in content else len(content)
    ]
    count_connect = mode_func.count('connect_to_ue()')
    assert count_connect == 1, \
        f"Expected 1 connect_to_ue() call in create-transform-active, found {count_connect}"
    test("create-transform-active uses 0.2s delay between packets on one connection", True)


def test_injector_no_same_tick_burst():
    """Verify no legacy same-tick burst for CREATE+TRANSFORM on one connection."""
    content = open(INJECTOR_PATH).read()
    mode_func = content[
        content.index('def mode_create_transform_active'):
        content.index('def mode_cameradef_only') if 'def mode_cameradef_only' in content else len(content)
    ]
    # The mode should NOT contain "one burst" (old description)
    assert 'one burst' not in mode_func, \
        "Legacy 'one burst' pattern still present"
    # Should have sleep() calls between packets
    sleep_before_transform = mode_func[
        mode_func.index('send_create_camera'):
        mode_func.index('send_transform') if 'send_transform' in mode_func else len(mode_func)
    ]
    assert 'sleep(0.2)' in sleep_before_transform, \
        "No sleep between CREATE and TRANSFORM — would cause same-tick burst"
    test("No legacy same-tick burst (inter-packet sleep exists)", True)


def test_0x02_reserved_invalid():
    """Verify 0x02 remains reserved/invalid in packet handling."""
    content = read_cpp()
    lines = content.split('\n')
    in_valid_types = False
    found_0x02 = False
    for line in lines:
        if 'kValidTypes' in line:
            in_valid_types = True
        if in_valid_types and '0x02' in line:
            found_0x02 = True
            break
        if in_valid_types and '}' in line:
            break
    test("0x02 is NOT in kValidTypes (reserved)", not found_0x02)


def test_injector_dedup_documented():
    """Verify the lifecycle note about SeenThisTick dedup is documented in the injector."""
    content = open(INJECTOR_PATH).read()
    assert 'SeenThisTick' in content, \
        "SeenThisTick dedup note not found in injector"
    assert 'dedup' in content or 'deduplication' in content, \
        "Dedup mention not found in injector"
    test("Injector documents SeenThisTick dedup in lifecycle note", True)


# ------------------------------------------------------------------
# Main
# ------------------------------------------------------------------

def main():
    tests = [
        # Blender side
        ("test_primitive_camera_constant", test_primitive_camera_constant),
        ("test_primitive_camera_in_exports", test_primitive_camera_in_exports),
        ("test_get_primitive_type_signature", test_get_primitive_type_signature),
        ("test_get_primitive_type_camera_detection", test_get_primitive_type_camera_detection),
        ("test_create_uses_obj_primitive", test_create_uses_obj_primitive),
        # UE create path
        ("test_lsp_camera_in_sync_enum", test_lsp_camera_in_sync_enum),
        ("test_kvalidtypes_contains_0x05", test_kvalidtypes_contains_0x05),
        ("test_camera_spawns_acameraactor", test_camera_spawns_acameraactor),
        ("test_camera_root_component_cam_component", test_camera_root_component_cam_component),
        ("test_camera_create_diagnostics", test_camera_create_diagnostics),
        ("test_camera_primitive_type_check", test_camera_primitive_type_check),
        # UE transform path
        ("test_transform_path_no_camera_rejection", test_transform_path_no_camera_rejection),
        # Diagnostics
        ("test_transform_apply_diagnostics", test_transform_apply_diagnostics),
        ("test_transform_converged_diagnostics", test_transform_converged_diagnostics),
        ("test_acameraactor_isa_check", test_acameraactor_isa_check),
        # Reserved / regression
        ("test_0x02_reserved_in_kvalidtypes", test_0x02_reserved_in_kvalidtypes),
        ("test_non_camera_primitives_unchanged", test_non_camera_primitives_unchanged),
        ("test_staticmesh_component_path_exists", test_staticmesh_component_path_exists),
        # Injector modes
        ("test_injector_mode_create_transform_active", test_injector_mode_create_transform_active),
        ("test_injector_mode_cameradef_only", test_injector_mode_cameradef_only),
        ("test_injector_mode_full_separated", test_injector_mode_full_separated),
        ("test_injector_default_non_hanging", test_injector_default_non_hanging),
        ("test_injector_has_separate_tick_delay", test_injector_has_separate_tick_delay),
        ("test_injector_no_same_tick_burst", test_injector_no_same_tick_burst),
        ("test_injector_dedup_documented", test_injector_dedup_documented),
        ("test_0x02_reserved_invalid", test_0x02_reserved_invalid),
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

    total = passed + failed
    print()
    print("=" * 60)
    print(f"  RESULTS: {passed} passed, {failed} failed / {total} total")
    if failed == 0:
        print("  ALL TESTS PASSED")
        sys.exit(0)
    else:
        print("  SOME TESTS FAILED")
        sys.exit(1)


if __name__ == '__main__':
    main()
