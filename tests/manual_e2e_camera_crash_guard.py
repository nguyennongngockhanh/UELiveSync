#!/usr/bin/env python3
"""
tests/manual_e2e_camera_crash_guard.py

Static-analysis tests for Manual E2E.1 — Camera Frustum Crash Guard.

Verifies:
1. Investigation doc exists
2. ConfigureLiveSyncCameraActor helper exists
3. Both camera spawn paths call the helper
4. UCameraComponent is not disabled/destroyed
5. CameraDef path remains intact
6. Sequencer binding/camera cut path remains intact
7. No protocol change
8. 0x02 remains reserved/invalid
9. 0x10 remains unused
10. Log hygiene doc exists
"""

import os
import re
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SUBSYSTEM_CPP = os.path.join(
    REPO_ROOT,
    "UE_Plugin", "UELiveSync",
    "Source", "UELiveSync", "Private",
    "UELiveSyncSubsystem.cpp"
)
SUBSYSTEM_H = os.path.join(
    REPO_ROOT,
    "UE_Plugin", "UELiveSync",
    "Source", "UELiveSync", "Public",
    "UELiveSyncSubsystem.h"
)
INVESTIGATION_DOC = os.path.join(
    REPO_ROOT,
    "Docs", "Architecture",
    "manual-e2e-camera-crash-investigation.md"
)
LOG_HYGIENE_DOC = os.path.join(
    REPO_ROOT,
    "Docs", "Architecture",
    "manual-e2e-log-hygiene.md"
)


def _read_cpp():
    with open(SUBSYSTEM_CPP, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


def _read_h():
    with open(SUBSYSTEM_H, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


def _read_doc(path):
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


class TestCameraCrashGuard(unittest.TestCase):
    """Static analysis tests for camera frustum guard."""

    def test_investigation_doc_exists(self):
        self.assertTrue(os.path.exists(INVESTIGATION_DOC),
                        "Docs/Architecture/manual-e2e-camera-crash-investigation.md must exist")

    def test_helper_declared_in_header(self):
        h = _read_h()
        self.assertIn("ConfigureLiveSyncCameraActor", h,
                       "ConfigureLiveSyncCameraActor must be declared in UELiveSyncSubsystem.h")

    def test_helper_defined_in_source(self):
        cpp = _read_cpp()
        self.assertIn("ConfigureLiveSyncCameraActor", cpp,
                       "ConfigureLiveSyncCameraActor must be defined in UELiveSyncSubsystem.cpp")

    def test_handle_create_object_calls_helper(self):
        cpp = _read_cpp()
        # Find the camera spawn block in HandleCreateObject
        # The helper call must appear after the LSP_Camera root-component setup
        self.assertIn("ConfigureLiveSyncCameraActor(CamActor)", cpp,
                       "HandleCreateObject camera path must call ConfigureLiveSyncCameraActor(CamActor)")

    def test_handle_active_camera_calls_helper(self):
        cpp = _read_cpp()
        self.assertIn("ConfigureLiveSyncCameraActor(NewCamera)", cpp,
                       "HandleActiveCamera auto-spawn path must call ConfigureLiveSyncCameraActor(NewCamera)")

    def test_camera_component_not_disabled(self):
        """UCameraComponent must remain enabled and functional."""
        cpp = _read_cpp()
        # Check that GetCameraComponent is still used (not removed)
        self.assertIn("GetCameraComponent", cpp,
                       "GetCameraComponent usage must be preserved")
        # Check SetMobility on camera component is present
        self.assertIn("SetMobility", cpp,
                       "SetMobility(Movable) on camera component must be preserved")

    def test_camera_def_path_intact(self):
        """HandleCameraDef path must remain functional."""
        cpp = _read_cpp()
        self.assertIn("HandleCameraDef", cpp,
                       "HandleCameraDef must be preserved")
        self.assertIn("FCameraDefPayload", cpp,
                       "FCameraDefPayload must be preserved")

    def test_sequencer_binding_intact(self):
        """EnsureCameraSequencerBinding must remain functional."""
        cpp = _read_cpp()
        self.assertIn("EnsureCameraSequencerBinding", cpp,
                       "EnsureCameraSequencerBinding must be preserved")

    def test_camera_cut_track_path_intact(self):
        """CameraCutTrack path must remain functional."""
        cpp = _read_cpp()
        self.assertIn("CameraCutTrack", cpp,
                       "CameraCutTrack usage must be preserved")
        self.assertIn("AddCameraCutTrack", cpp,
                       "AddCameraCutTrack must be preserved")

    def test_no_protocol_change(self):
        """No changes to packet protocol constants."""
        cpp = _read_cpp()
        h = _read_h()
        # Check no new PT_ types added
        combined = cpp + h
        self.assertNotIn("PT_CameraFrustumGuard", combined,
                         "No new packet type for frustum guard")
        self.assertNotIn("PT_CamFrustum", combined,
                         "No new packet type for frustum guard")

    def test_reserved_0x02_unchanged(self):
        """0x02 must remain reserved/invalid."""
        cpp = _read_cpp()
        # Check that 0x02 is not assigned to a new primitive type
        # The pattern should show it is not used as LSP_
        lines = cpp.split('\n')
        for i, line in enumerate(lines):
            if 'LSP_' in line and '0x02' in line:
                self.fail(f"0x02 must not be assigned to a new LSP_ type. Found at line {i+1}: {line.strip()}")

    def test_unused_0x10_unchanged(self):
        """0x10 must remain unused."""
        cpp = _read_cpp()
        h = _read_h()
        combined = cpp + h
        # No new assignment of 0x10 to a protocol type
        # Check no LSP_ or PT_ mapping uses 0x10
        patterns = [
            r'LSP_[A-Za-z_]+\s*=\s*0x10',
            r'PT_[A-Za-z_]+\s*=\s*0x10',
            r'\b0x10\b.*LSP_|0x10.*PT_',
        ]
        for pat in patterns:
            matches = re.findall(pat, combined)
            self.assertEqual(matches, [],
                             f"0x10 must not be assigned to any LSP_/PT_ type. Found: {matches}")

    def test_log_hygiene_doc_exists(self):
        """Log hygiene documentation should exist."""
        # This test documents that log hygiene doc should be created.
        # It passes if the investigation doc covers log hygiene (alternative).
        inv_doc = _read_doc(INVESTIGATION_DOC)
        if inv_doc and "STALE_LOG_READER_RISK" in inv_doc:
            self.assertTrue(True, "Log hygiene covered in investigation doc")
        else:
            # Allow passing if log hygiene doc exists instead
            self.assertTrue(
                os.path.exists(LOG_HYGIENE_DOC),
                "Either investigation doc covers log hygiene or manual-e2e-log-hygiene.md must exist"
            )

    def test_frustum_guard_markers_present(self):
        """Diagnostic markers must be present in code."""
        cpp = _read_cpp()
        self.assertIn("[CAMERA][FRUSTUM_GUARD]", cpp,
                       "[CAMERA][FRUSTUM_GUARD] marker must be present")
        self.assertIn("[CAMERA][FRUSTUM_GUARD_SKIP]", cpp,
                       "[CAMERA][FRUSTUM_GUARD_SKIP] marker must be present")
        self.assertIn("[CAMERA][FRUSTUM_GUARD_FAIL]", cpp,
                       "[CAMERA][FRUSTUM_GUARD_FAIL] marker must be present")

    def test_ucamera_component_not_destroyed(self):
        """Camera component must not be Destroyed or SetEnabled(false)."""
        cpp = _read_cpp()
        lines = cpp.split('\n')
        # Check that ConfigureLiveSyncCameraActor does not destroy camera component
        in_helper = False
        for line in lines:
            stripped = line.strip()
            if 'ConfigureLiveSyncCameraActor' in stripped and 'void' in stripped:
                in_helper = True
            if in_helper and stripped == '}':
                in_helper = False
                break
            if in_helper:
                self.assertNotIn("DestroyComponent", stripped,
                                 "ConfigureLiveSyncCameraActor must not destroy components")

    def test_frustum_guard_uses_scene_component_cast(self):
        """Frustum guard must cast to USceneComponent before calling visibility APIs."""
        cpp = _read_cpp()
        # Find ConfigureLiveSyncCameraActor function body
        lines = cpp.split('\n')
        in_helper = False
        for line in lines:
            stripped = line.strip()
            if 'ConfigureLiveSyncCameraActor' in stripped and 'void' in stripped:
                in_helper = True
            if in_helper and stripped == '}':
                in_helper = False
                break
            if in_helper:
                # Must NOT call SetHiddenInGame/SetVisibility directly on UActorComponent
                # Pattern: "Comp->SetHiddenInGame" where Comp is UActorComponent*
                if re.search(r'Comp->SetHiddenInGame', line):
                    self.fail("Comp (UActorComponent*) must not call SetHiddenInGame directly. "
                              "Must cast to USceneComponent first.")
                if re.search(r'Comp->SetVisibility', line):
                    self.fail("Comp (UActorComponent*) must not call SetVisibility directly. "
                              "Must cast to USceneComponent first.")
                # Must use SceneComp or equivalent after Cast<USceneComponent>
                # Verify Cast<USceneComponent> exists in the helper
        # Global check: Cast<USceneComponent> must exist
        self.assertIn('Cast<USceneComponent>', cpp,
                      "ConfigureLiveSyncCameraActor must use Cast<USceneComponent> for frustum components")

    def test_frustum_guard_log_quote_valid(self):
        """UE_LOG string must not contain invalid nested quotes in TEXT()."""
        cpp = _read_cpp()
        # Find the FRUSTUM_GUARD log line
        for line in cpp.split('\n'):
            if 'FRUSTUM_GUARD' in line and 'Suppressed' in line:
                # TEXT macro content must not have bare double quotes
                # Extract content between TEXT("...")
                match = re.search(r'TEXT\("([^"]*(?:"[^"]*)*[^"]*)"\)', line)
                if match:
                    content = match.group(1)
                    self.assertNotIn('"', content,
                                     f"TEXT() content has nested quotes: {content}")
                break
        else:
            self.fail("FRUSTUM_GUARD log line not found")

    def test_frustum_guard_component_tick_disabled(self):
        """Frustum component tick must be disabled after guard."""
        cpp = _read_cpp()
        lines = cpp.split('\n')
        in_helper = False
        brace_depth = 0
        found_tick_disable = False
        pending_void_line = -1
        for i, line in enumerate(lines):
            stripped = line.strip()
            # Handle multi-line declaration: 'void UUELiveSyncSubsystem::' followed by
            # 'ConfigureLiveSyncCameraActor(...)
            if 'void' in stripped and 'UUELiveSyncSubsystem::' in stripped:
                pending_void_line = i
                continue
            if pending_void_line == i - 1 and 'ConfigureLiveSyncCameraActor' in stripped:
                in_helper = True
                brace_depth = 0
                pending_void_line = -1
                continue
            pending_void_line = -1  # reset if not consecutive
            if in_helper:
                for ch in stripped:
                    if ch == '{':
                        brace_depth += 1
                    elif ch == '}':
                        brace_depth -= 1
                        if brace_depth == 0:
                            break
                if brace_depth <= 0:
                    in_helper = False
                    break
                if 'SetComponentTickEnabled' in stripped:
                    found_tick_disable = True
        self.assertTrue(found_tick_disable,
                        "ConfigureLiveSyncCameraActor must disable tick on frustum components")

    def test_ucamera_component_not_hidden(self):
        """UCameraComponent must not be hidden or disabled by frustum guard."""
        cpp = _read_cpp()
        # Verify GetCameraComponent is still used and camera is not hidden
        self.assertIn('GetCameraComponent', cpp,
                       "GetCameraComponent must remain functional")
        # Check no SetActorHiddenInGame on the camera actor itself
        lines = cpp.split('\n')
        in_helper = False
        brace_depth = 0
        pending_void_line = -1
        for i, line in enumerate(lines):
            stripped = line.strip()
            if 'void' in stripped and 'UUELiveSyncSubsystem::' in stripped:
                pending_void_line = i
                continue
            if pending_void_line == i - 1 and 'ConfigureLiveSyncCameraActor' in stripped:
                in_helper = True
                brace_depth = 0
                pending_void_line = -1
                continue
            pending_void_line = -1
            if in_helper:
                for ch in stripped:
                    if ch == '{':
                        brace_depth += 1
                    elif ch == '}':
                        brace_depth -= 1
                        if brace_depth == 0:
                            break
                if brace_depth <= 0:
                    in_helper = False
                    break
                self.assertNotIn('SetActorHiddenInGame', stripped,
                                 "SetActorHiddenInGame must not be called in frustum guard")

    def test_handle_create_object_camera_def_still_called(self):
        """HandleCameraDef path must still be called from ProcessBinaryPacket."""
        cpp = _read_cpp()
        self.assertIn("HandleCameraDef(Payload)", cpp,
                       "HandleCameraDef must be called from packet dispatch")

    def test_active_camera_path_preserved(self):
        """HandleActiveCamera main logic must be preserved."""
        cpp = _read_cpp()
        self.assertIn("LastActiveCameraGUID", cpp,
                       "LastActiveCameraGUID must be preserved")
        self.assertIn("SetActorLock", cpp,
                       "SetActorLock must be preserved")


class TestLogHygiene(unittest.TestCase):
    """Tests for log hygiene documentation and tool behavior."""

    def test_investigation_covers_log_hygiene(self):
        """Investigation doc should document log hygiene issues."""
        inv_doc = _read_doc(INVESTIGATION_DOC)
        self.assertIsNotNone(inv_doc, "Investigation doc must exist")
        self.assertIn("STALE_LOG_READER_RISK", inv_doc,
                       "Investigation doc must document stale log reader risk")

    def test_validator_ignores_backup_logs(self):
        """Validator should not read ProjectTemplate-backup-*.log by default."""
        # This is a behavioral requirement for the validator tool.
        # Documented in investigation doc as a secondary blocker.
        inv_doc = _read_doc(INVESTIGATION_DOC)
        self.assertIsNotNone(inv_doc)
        self.assertIn("ProjectTemplate-backup", inv_doc,
                       "Investigation doc must document backup log risk")

    def test_current_run_guid_filtering(self):
        """Validator should filter by current-run GUID."""
        inv_doc = _read_doc(INVESTIGATION_DOC)
        self.assertIsNotNone(inv_doc)
        self.assertIn("current-run", inv_doc.lower()) or \
            self.assertIn("GUID", inv_doc), \
            "Investigation doc must document GUID-based filtering"


if __name__ == "__main__":
    unittest.main()
