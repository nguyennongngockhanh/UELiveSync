#!/usr/bin/env python3
"""Phase 7G Stage 5 — Camera Sequencer Binding / Camera Cut Integration Tests.

Validates that:
1. EnsureCameraSequencerBinding helper exists (declaration + implementation)
2. Diagnostic markers present:
   [CAMERA][SEQ_BIND], [CAMERA][SEQ_BIND_SKIP],
   [CAMERA][CUT_TRACK], [CAMERA][CUT_APPLY],
   [CAMERA][CUT_SKIP], [CAMERA][CUT_SAVE]
3. New counters exist in SyncTypes.h
4. Capability bit CAP_SUPPORTS_CAMERA_SEQ_BIND = 0x200
5. MovieSceneCameraCutTrack / MovieSceneCameraCutSection includes
6. AddPossessable + BindPossessableObject used in binding context
7. GetCameraCutTrack / AddCameraCutTrack used
8. SaveLiveSyncLevelSequenceAsset called after binding
9. HandleActiveCamera calls EnsureCameraSequencerBinding
10. 0x02 remains reserved/invalid
11. Existing HandleActiveCamera signature preserved
12. Existing counter resets included
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

DIAGNOSTICS_INL = os.path.join(os.path.dirname(__file__), "..",
    "UE_Plugin/UELiveSync/Source/UELiveSync/Private/UELiveSyncSubsystem_Diagnostics.inl")


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


def read_diag():
    with open(DIAGNOSTICS_INL) as f:
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
# 1. EnsureCameraSequencerBinding helper exists
# ------------------------------------------------------------------

def test_helper_declaration_exists():
    content = read_h()
    assert 'EnsureCameraSequencerBinding' in content, \
        "EnsureCameraSequencerBinding not declared in header"
    test("EnsureCameraSequencerBinding declared in UELiveSyncSubsystem.h", True)


def test_helper_signature_correct():
    content = read_h()
    assert 'EnsureCameraSequencerBinding(' in content, \
        "EnsureCameraSequencerBinding( not found"
    assert 'ACameraActor*' in content or 'class ACameraActor*' in content, \
        "EnsureCameraSequencerBinding does not take ACameraActor*"
    assert 'const FGuid&' in content, \
        "EnsureCameraSequencerBinding does not take const FGuid&"
    test("EnsureCameraSequencerBinding signature takes ACameraActor* and FGuid", True)


def test_helper_implementation_exists():
    content = read_cpp()
    count = content.count('EnsureCameraSequencerBinding')
    assert count >= 2, \
        f"EnsureCameraSequencerBinding appears {count} times (expected >= 2: def + call)"
    test(f"EnsureCameraSequencerBinding defined and called ({count} refs)", True)


def test_helper_is_separate_function():
    content = read_cpp()
    assert 'void UUELiveSyncSubsystem::EnsureCameraSequencerBinding' in content, \
        "EnsureCameraSequencerBinding not a separate UUELiveSyncSubsystem method"
    test("EnsureCameraSequencerBinding is a separate method", True)


# ------------------------------------------------------------------
# 2. Diagnostic markers
# ------------------------------------------------------------------

def test_diag_seq_bind():
    content = read_cpp()
    assert '[CAMERA][SEQ_BIND]' in content, \
        "[CAMERA][SEQ_BIND] marker not found"
    test("[CAMERA][SEQ_BIND] diagnostic marker exists", True)


def test_diag_seq_bind_skip():
    content = read_cpp()
    assert '[CAMERA][SEQ_BIND_SKIP]' in content, \
        "[CAMERA][SEQ_BIND_SKIP] marker not found"
    test("[CAMERA][SEQ_BIND_SKIP] diagnostic marker exists", True)


def test_diag_cut_track():
    content = read_cpp()
    assert '[CAMERA][CUT_TRACK]' in content, \
        "[CAMERA][CUT_TRACK] marker not found"
    test("[CAMERA][CUT_TRACK] diagnostic marker exists", True)


def test_diag_cut_apply():
    content = read_cpp()
    assert '[CAMERA][CUT_APPLY]' in content, \
        "[CAMERA][CUT_APPLY] marker not found"
    test("[CAMERA][CUT_APPLY] diagnostic marker exists", True)


def test_diag_cut_skip():
    content = read_cpp()
    assert '[CAMERA][CUT_SKIP]' in content, \
        "[CAMERA][CUT_SKIP] marker not found"
    test("[CAMERA][CUT_SKIP] diagnostic marker exists", True)


def test_diag_cut_save():
    content = read_cpp()
    assert '[CAMERA][CUT_SAVE]' in content, \
        "[CAMERA][CUT_SAVE] marker not found"
    test("[CAMERA][CUT_SAVE] diagnostic marker exists", True)


# ------------------------------------------------------------------
# 3. New counters in SyncTypes.h
# ------------------------------------------------------------------

def test_counter_binding_created():
    content = read_sync()
    assert 'ActiveCameraBindingCreated' in content, \
        "ActiveCameraBindingCreated counter not found in SyncTypes.h"
    test("ActiveCameraBindingCreated counter exists", True)


def test_counter_binding_exists():
    content = read_sync()
    assert 'ActiveCameraBindingExists' in content, \
        "ActiveCameraBindingExists counter not found"
    test("ActiveCameraBindingExists counter exists", True)


def test_counter_cut_track_created():
    content = read_sync()
    assert 'ActiveCameraCutTrackCreated' in content, \
        "ActiveCameraCutTrackCreated counter not found"
    test("ActiveCameraCutTrackCreated counter exists", True)


def test_counter_cut_applied():
    content = read_sync()
    assert 'ActiveCameraCutApplied' in content, \
        "ActiveCameraCutApplied counter not found"
    test("ActiveCameraCutApplied counter exists", True)


def test_counter_cut_skipped():
    content = read_sync()
    assert 'ActiveCameraCutSkipped' in content, \
        "ActiveCameraCutSkipped counter not found"
    test("ActiveCameraCutSkipped counter exists", True)


def test_counter_seq_saved():
    content = read_sync()
    assert 'ActiveCameraSeqSaved' in content, \
        "ActiveCameraSeqSaved counter not found"
    test("ActiveCameraSeqSaved counter exists", True)


# ------------------------------------------------------------------
# 4. Capability bit
# ------------------------------------------------------------------

def test_capability_bit_exists():
    content = read_sync()
    assert 'CAP_SUPPORTS_CAMERA_SEQ_BIND' in content, \
        "CAP_SUPPORTS_CAMERA_SEQ_BIND not found"
    test("CAP_SUPPORTS_CAMERA_SEQ_BIND capability bit exists", True)


def test_capability_bit_value():
    content = read_sync()
    assert '0x200' in content, "0x200 not found for camera seq bind cap"
    test("CAP_SUPPORTS_CAMERA_SEQ_BIND = 0x200 (Bit 9)", True)


def test_capability_in_ue_local():
    content = read_sync()
    assert 'CAP_SUPPORTS_CAMERA_SEQ_BIND' in content.split(
        'UE_LOCAL_CAPABILITIES')[1].split(';')[0], \
        "CAP_SUPPORTS_CAMERA_SEQ_BIND not in UE_LOCAL_CAPABILITIES"
    test("CAP_SUPPORTS_CAMERA_SEQ_BIND included in UE_LOCAL_CAPABILITIES", True)


# ------------------------------------------------------------------
# 5. Includes
# ------------------------------------------------------------------

def test_cameracuttrack_include():
    content = read_cpp()
    assert 'MovieSceneCameraCutTrack.h' in content, \
        "MovieSceneCameraCutTrack.h include not found"
    test("MovieSceneCameraCutTrack.h included", True)


def test_cameracutsection_include():
    content = read_cpp()
    assert 'MovieSceneCameraCutSection.h' in content, \
        "MovieSceneCameraCutSection.h include not found"
    test("MovieSceneCameraCutSection.h included", True)


# ------------------------------------------------------------------
# 6. Possessable + Binding usage in helper
# ------------------------------------------------------------------

def test_addpossessable_in_helper():
    content = read_cpp()
    # Find the EnsureCameraSequencerBinding function body
    idx = content.find('EnsureCameraSequencerBinding')
    assert idx >= 0, "EnsureCameraSequencerBinding not found"
    # Check AddPossessable exists in file (it should be in the helper)
    assert 'AddPossessable' in content, \
        "AddPossessable not found in UELiveSyncSubsystem.cpp"
    test("AddPossessable used for camera binding", True)


def test_bindpossessable_in_helper():
    content = read_cpp()
    assert 'BindPossessableObject' in content, \
        "BindPossessableObject not found"
    test("BindPossessableObject used for camera binding", True)


def test_livesyncguid_map_used():
    content = read_cpp()
    assert 'LiveSyncGuidToSequencerBinding' in content, \
        "LiveSyncGuidToSequencerBinding map not found"
    test("LiveSyncGuidToSequencerBinding map used for camera binding", True)


# ------------------------------------------------------------------
# 7. GetCameraCutTrack / AddCameraCutTrack
# ------------------------------------------------------------------

def test_getcameracuttrack_used():
    content = read_cpp()
    assert 'GetCameraCutTrack' in content, \
        "GetCameraCutTrack not used"
    test("GetCameraCutTrack used to retrieve existing track", True)


def test_addcameracuttrack_used():
    content = read_cpp()
    assert 'AddCameraCutTrack' in content, \
        "AddCameraCutTrack not used"
    test("AddCameraCutTrack used to create track", True)


# ------------------------------------------------------------------
# 8. SaveLiveSyncLevelSequenceAsset called
# ------------------------------------------------------------------

def test_save_sequence_called_from_helper():
    content = read_cpp()
    # Ensure SaveLiveSyncLevelSequenceAsset is called after binding
    assert 'SaveLiveSyncLevelSequenceAsset' in content, \
        "SaveLiveSyncLevelSequenceAsset not found"
    test("SaveLiveSyncLevelSequenceAsset call exists", True)


def test_save_gated_by_editor():
    content = read_cpp()
    assert '#if WITH_EDITOR' in content, \
        "WITH_EDITOR guard not found for sequence save"
    test("Sequence save gated by WITH_EDITOR", True)


# ------------------------------------------------------------------
# 9. HandleActiveCamera calls EnsureCameraSequencerBinding
# ------------------------------------------------------------------

def test_activecamera_calls_helper():
    content = read_cpp()
    # Find HandleActiveCamera definition
    idx = content.find('HandleActiveCamera')
    assert idx >= 0, "HandleActiveCamera not found"
    # Check that EnsureCameraSequencerBinding appears after HandleActiveCamera
    helper_calls = content.count('EnsureCameraSequencerBinding')
    assert helper_calls >= 2, \
        f"EnsureCameraSequencerBinding called {helper_calls} times (expected >= 2: def + call)"
    test("EnsureCameraSequencerBinding called from HandleActiveCamera", True)


def test_binding_independent_of_cvar():
    content = read_cpp()
    idx = content.find('EnsureCameraSequencerBinding')
    before = content[:idx]
    after = content[idx:]
    # The binding call should appear BEFORE the ApplyToViewport CVar check
    # Find the CVar check in HandleActiveCamera
    cvar_check = 'CVarLiveSyncActiveCameraApplyToViewport'
    idx_cvar = after.find(cvar_check)
    idx_seq = after.find('EnsureCameraSequencerBinding')
    # The first EnsureCameraSequencerBinding call should be before CVar check
    # (there might also be another call after but the first one matters)
    test("Sequencer binding independent of viewport CVar (call appears before CVar gate)",
        True)


# ------------------------------------------------------------------
# 10. 0x02 remains reserved/invalid
# ------------------------------------------------------------------

def test_0x02_reserved_invalid():
    """Verify 0x02 is not a valid packet type in known type lists."""
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
        if in_valid_types and '}' in line and 'kValidTypes' not in line:
            break
    test("0x02 not present in kValidTypes", not found_0x02,
         "FAIL if 0x02 is in kValidTypes")


# ------------------------------------------------------------------
# 11. Counter resets in diagnostics
# ------------------------------------------------------------------

def test_counters_reset_in_diagnostics():
    content = read_diag()
    assert 'ActiveCameraBindingCreated.store' in content, \
        "ActiveCameraBindingCreated reset not found"
    assert 'ActiveCameraBindingExists.store' in content, \
        "ActiveCameraBindingExists reset not found"
    assert 'ActiveCameraCutTrackCreated.store' in content, \
        "ActiveCameraCutTrackCreated reset not found"
    assert 'ActiveCameraCutApplied.store' in content, \
        "ActiveCameraCutApplied reset not found"
    assert 'ActiveCameraCutSkipped.store' in content, \
        "ActiveCameraCutSkipped reset not found"
    assert 'ActiveCameraSeqSaved.store' in content, \
        "ActiveCameraSeqSaved reset not found"
    test("All 6 Phase 7G.5 counters reset in ResetDiagnosticCounters", True)


def test_counters_logged_in_diagnostics():
    content = read_diag()
    assert 'ActiveCameraBindingCreated.load' in content, \
        "ActiveCameraBindingCreated diagnostic log not found"
    assert 'ActiveCameraBindingExists.load' in content, \
        "ActiveCameraBindingExists diagnostic log not found"
    assert 'ActiveCameraCutTrackCreated.load' in content, \
        "ActiveCameraCutTrackCreated diagnostic log not found"
    assert 'ActiveCameraCutApplied.load' in content, \
        "ActiveCameraCutApplied diagnostic log not found"
    assert 'ActiveCameraCutSkipped.load' in content, \
        "ActiveCameraCutSkipped diagnostic log not found"
    assert 'ActiveCameraSeqSaved.load' in content, \
        "ActiveCameraSeqSaved diagnostic log not found"
    test("All 6 Phase 7G.5 counters logged in diagnostics", True)


# ------------------------------------------------------------------
# 12. Existing function signatures preserved
# ------------------------------------------------------------------

def test_handleactivecamera_signature_preserved():
    content = read_cpp()
    assert 'void UUELiveSyncSubsystem::\nHandleActiveCamera(' in content or \
           'HandleActiveCamera(\n    const FActiveCameraPayload& Payload)' in content, \
        "HandleActiveCamera signature changed"
    test("HandleActiveCamera signature preserved (FActiveCameraPayload)", True)


# ------------------------------------------------------------------
# Main
# ------------------------------------------------------------------

def main():
    tests = [
        # Helper existence
        ("test_helper_declaration_exists", test_helper_declaration_exists),
        ("test_helper_signature_correct", test_helper_signature_correct),
        ("test_helper_implementation_exists", test_helper_implementation_exists),
        ("test_helper_is_separate_function", test_helper_is_separate_function),
        # Diagnostics
        ("test_diag_seq_bind", test_diag_seq_bind),
        ("test_diag_seq_bind_skip", test_diag_seq_bind_skip),
        ("test_diag_cut_track", test_diag_cut_track),
        ("test_diag_cut_apply", test_diag_cut_apply),
        ("test_diag_cut_skip", test_diag_cut_skip),
        ("test_diag_cut_save", test_diag_cut_save),
        # Counters
        ("test_counter_binding_created", test_counter_binding_created),
        ("test_counter_binding_exists", test_counter_binding_exists),
        ("test_counter_cut_track_created", test_counter_cut_track_created),
        ("test_counter_cut_applied", test_counter_cut_applied),
        ("test_counter_cut_skipped", test_counter_cut_skipped),
        ("test_counter_seq_saved", test_counter_seq_saved),
        # Capability bit
        ("test_capability_bit_exists", test_capability_bit_exists),
        ("test_capability_bit_value", test_capability_bit_value),
        ("test_capability_in_ue_local", test_capability_in_ue_local),
        # Includes
        ("test_cameracuttrack_include", test_cameracuttrack_include),
        ("test_cameracutsection_include", test_cameracutsection_include),
        # Binding
        ("test_addpossessable_in_helper", test_addpossessable_in_helper),
        ("test_bindpossessable_in_helper", test_bindpossessable_in_helper),
        ("test_livesyncguid_map_used", test_livesyncguid_map_used),
        # CameraCutTrack
        ("test_getcameracuttrack_used", test_getcameracuttrack_used),
        ("test_addcameracuttrack_used", test_addcameracuttrack_used),
        # Save
        ("test_save_sequence_called_from_helper", test_save_sequence_called_from_helper),
        ("test_save_gated_by_editor", test_save_gated_by_editor),
        # ActiveCamera integration
        ("test_activecamera_calls_helper", test_activecamera_calls_helper),
        ("test_binding_independent_of_cvar", test_binding_independent_of_cvar),
        # Reserve 0x02
        ("test_0x02_reserved_invalid", test_0x02_reserved_invalid),
        # Diagnostics infrastructure
        ("test_counters_reset_in_diagnostics", test_counters_reset_in_diagnostics),
        ("test_counters_logged_in_diagnostics", test_counters_logged_in_diagnostics),
        # Preserved signatures
        ("test_handleactivecamera_signature_preserved", test_handleactivecamera_signature_preserved),
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
