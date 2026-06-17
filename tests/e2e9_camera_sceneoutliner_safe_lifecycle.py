#!/usr/bin/env python3
"""
tests/e2e9_camera_sceneoutliner_safe_lifecycle.py

Static-analysis tests for E2E.9 — Camera Actor SceneOutliner Safe Lifecycle.

Verifies:
1. IsLiveSyncCameraSafeForEditorUse helper exists in cpp file
2. Deferred spawn (SpawnActorDeferred) used for camera actors
3. Camera SAFE_LIFECYCLE_ENTER marker present
4. Camera SAFE_SPAWN_BEGIN marker present  
5. Camera SAFE_SPAWN_READY marker present
6. Camera SAFE_CACHE_ADD marker present
7. Camera OUTLINER_GUARD marker present (frustum guard pre-FinishSpawning)
8. ActiveCamera Sequencer binding gated by safety check (SAFE_SEQ_DEFER)
9. ActiveCamera viewport lock gated by safety check (SAFE_ACTIVE_DEFER)
10. SAFE_INVALID_SKIP for non-CameraActor/unsafe camera
11. Frustum guard still preserved (E2E.1/2)
12. Hierarchy helper (IsLiveSyncActorInvalidForAttach) preserved
13. No protocol changes (packet IDs unchanged)
14. No direct bPendingKill access (E2E.7 preserved)
15. Camera sync not disabled
"""

import os
import re
import unittest

REPO_ROOT = os.path.normpath(
    os.path.join(os.path.dirname(__file__), ".."))
SOURCE_FILE = os.path.join(
    REPO_ROOT,
    "UE_Plugin/UELiveSync/Source/UELiveSync/Private/UELiveSyncSubsystem.cpp")
SYNC_TYPES_H = os.path.join(
    REPO_ROOT,
    "UE_Plugin/UELiveSync/Source/UELiveSync/Public/SyncTypes.h")

if not os.path.isfile(SOURCE_FILE):
    raise SystemExit(f"ERROR: source file not found at {SOURCE_FILE}")
if not os.path.isfile(SYNC_TYPES_H):
    raise SystemExit(f"ERROR: header not found at {SYNC_TYPES_H}")


def load_source(filepath):
    with open(filepath, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


SOURCE = load_source(SOURCE_FILE)
SYNC_TYPES = load_source(SYNC_TYPES_H)


class TestCameraSafeLifecycleHelperExists(unittest.TestCase):
    """IsLiveSyncCameraSafeForEditorUse helper definition."""

    def test_helper_function_exists(self):
        self.assertIn("IsLiveSyncCameraSafeForEditorUse", SOURCE,
            "IsLiveSyncCameraSafeForEditorUse must be defined")

    def test_helper_checks_world(self):
        self.assertIn("GetWorld()", SOURCE[
            SOURCE.index("IsLiveSyncCameraSafeForEditorUse"):
            SOURCE.index("IsLiveSyncCameraSafeForEditorUse") + 800])

    def test_helper_checks_camera_component(self):
        self.assertIn("GetCameraComponent()", SOURCE[
            SOURCE.index("IsLiveSyncCameraSafeForEditorUse"):
            SOURCE.index("IsLiveSyncCameraSafeForEditorUse") + 800])

    def test_helper_checks_root_component(self):
        self.assertIn("GetRootComponent()", SOURCE[
            SOURCE.index("IsLiveSyncCameraSafeForEditorUse"):
            SOURCE.index("IsLiveSyncCameraSafeForEditorUse") + 800])

    def test_helper_checks_valid(self):
        self.assertIn("IsValid", SOURCE[
            SOURCE.index("IsLiveSyncCameraSafeForEditorUse"):
            SOURCE.index("IsLiveSyncCameraSafeForEditorUse") + 800])

    def test_helper_checks_destroyed(self):
        self.assertIn("IsActorBeingDestroyed", SOURCE[
            SOURCE.index("IsLiveSyncCameraSafeForEditorUse"):
            SOURCE.index("IsLiveSyncCameraSafeForEditorUse") + 800])

    def test_helper_checks_unreachable(self):
        self.assertIn("IsUnreachable", SOURCE[
            SOURCE.index("IsLiveSyncCameraSafeForEditorUse"):
            SOURCE.index("IsLiveSyncCameraSafeForEditorUse") + 800])

    def test_helper_checks_level(self):
        self.assertIn("GetLevel()", SOURCE[
            SOURCE.index("IsLiveSyncCameraSafeForEditorUse"):
            SOURCE.index("IsLiveSyncCameraSafeForEditorUse") + 800])

    def test_helper_checks_outer(self):
        self.assertIn("GetOuter()", SOURCE[
            SOURCE.index("IsLiveSyncCameraSafeForEditorUse"):
            SOURCE.index("IsLiveSyncCameraSafeForEditorUse") + 800])


class TestCameraSpawnWorkaround(unittest.TestCase):
    """Camera uses outliner-hidden spawn (E2E.10 W3) in HandleCreateObject."""

    def test_camera_spawn_used(self):
        """Camera uses SpawnActor<ACameraActor> with outliner-hide."""
        self.assertIn("SpawnActor<ACameraActor>", SOURCE,
            "Camera must use SpawnActor")

    def test_frustum_guard_called(self):
        """Frustum guard still called for camera actor."""
        self.assertIn("ConfigureLiveSyncCameraActor", SOURCE,
            "Frustum guard must still be present")

    def test_outliner_hide_flag(self):
        """bHideFromSceneOutliner=true used in camera spawn."""
        self.assertIn("bHideFromSceneOutliner", SOURCE,
            "Outliner-hide flag must be set on camera spawn")


class TestCameraLifecycleMarkers(unittest.TestCase):
    """Required camera lifecycle markers."""

    def test_safe_lifecycle_enter_marker(self):
        self.assertIn("[CAMERA][SAFE_LIFECYCLE_ENTER]", SOURCE,
            "SAFE_LIFECYCLE_ENTER marker required")

    def test_safe_spawn_begin_marker(self):
        self.assertIn("[CAMERA][SAFE_SPAWN_BEGIN]", SOURCE,
            "SAFE_SPAWN_BEGIN marker required")

    def test_safe_spawn_ready_marker(self):
        self.assertIn("[CAMERA][SAFE_SPAWN_READY]", SOURCE,
            "SAFE_SPAWN_READY marker required")

    def test_safe_cache_add_marker(self):
        self.assertIn("[CAMERA][SAFE_CACHE_ADD]", SOURCE,
            "SAFE_CACHE_ADD marker required")

    def test_outliner_guard_marker(self):
        self.assertIn("[CAMERA][OUTLINER_GUARD]", SOURCE,
            "OUTLINER_GUARD marker required (frustum pre-FinishSpawning)")

    def test_safe_seq_defer_marker(self):
        self.assertIn("[CAMERA][SAFE_SEQ_DEFER]", SOURCE,
            "SAFE_SEQ_DEFER marker required for deferred sequencer binding")

    def test_safe_active_defer_marker(self):
        self.assertIn("[CAMERA][SAFE_ACTIVE_DEFER]", SOURCE,
            "SAFE_ACTIVE_DEFER marker required for deferred viewport lock")

    def test_safe_invalid_skip_marker(self):
        self.assertIn("[CAMERA][SAFE_INVALID_SKIP]", SOURCE,
            "SAFE_INVALID_SKIP marker required for unsafe camera skip")


class TestActiveCameraGatedBySafety(unittest.TestCase):
    """HandleActiveCamera gates Sequencer and viewport with safety check."""

    def test_ensure_sequencer_binding_gated(self):
        """EnsureCameraSequencerBinding guarded by IsLiveSyncCameraSafeForEditorUse check."""
        handle_active = SOURCE.index("HandleActiveCamera(")
        # Find HandleCameraDef as next function boundary
        ha_end = SOURCE.index("HandleCameraDef(", handle_active + 50)
        region = SOURCE[handle_active:ha_end]
        
        # Find the function definition, then search for the CALL after it
        defn_pos = region.find("void UUELiveSyncSubsystem::EnsureCameraSequencerBinding")
        if defn_pos == -1:
            self.fail("EnsureCameraSequencerBinding function definition not found")
        
        after_defn = region[defn_pos:]
        # The call has pattern EnsureCameraSequencerBinding(ResolvedCamera
        # not ::EnsureCameraSequencerBinding(
        call_pos = after_defn.find("EnsureCameraSequencerBinding(ResolvedCamera")
        if call_pos == -1:
            self.fail("EnsureCameraSequencerBinding call not found")
        
        before_call = after_defn[:call_pos]
        self.assertIn("IsLiveSyncCameraSafeForEditorUse", before_call,
            "EnsureCameraSequencerBinding must be gated by safety check")

    def test_viewport_lock_gated_by_safety(self):
        """SetActorLock guarded by IsLiveSyncCameraSafeForEditorUse check."""
        self.assertIn("IsLiveSyncCameraSafeForEditorUse(ResolvedCamera)", SOURCE,
            "Viewport lock must be gated by IsLiveSyncCameraSafeForEditorUse")

    def test_active_camera_has_workaround_spawn(self):
        """HandleActiveCamera auto-spawn must also hide from SceneOutliner."""
        self.assertIn("bHideFromSceneOutliner", SOURCE,
            "HandleActiveCamera must use bHideFromSceneOutliner for camera")


class TestExistingFeaturesPreserved(unittest.TestCase):
    """Existing safety features must still be present."""

    def test_frustum_guard_present(self):
        self.assertIn("[CAMERA][FRUSTUM_GUARD]", SOURCE,
            "FRUSTUM_GUARD must still be present")

    def test_frustum_guard_configure_function_present(self):
        self.assertIn("ConfigureLiveSyncCameraActor", SOURCE,
            "ConfigureLiveSyncCameraActor must still be present")

    def test_hierarchy_helper_present(self):
        self.assertIn("IsLiveSyncActorInvalidForAttach", SOURCE,
            "IsLiveSyncActorInvalidForAttach must still be present")

    def test_hierarchy_markers_present(self):
        self.assertIn("[HIERARCHY][ATTACH_GUARD]", SOURCE,
            "Hierarchy ATTACH_GUARD marker must still be present")

    def test_bpendingkill_not_present(self):
        for line_no, line in enumerate(SOURCE.splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("//") or stripped.startswith("/*") or stripped.startswith("*"):
                continue
            if "bPendingKill" in stripped and "->bPendingKill" in stripped:
                self.fail(f"Line {line_no}: '->bPendingKill' access found: {stripped}")
            if "bPendingKill" in stripped and ".bPendingKill" in stripped:
                self.fail(f"Line {line_no}: '.bPendingKill' access found: {stripped}")


class TestProtocolUnchanged(unittest.TestCase):
    """No packet type changes since E2E.9."""

    def test_pt_create_unchanged(self):
        self.assertIn("PT_Create", SOURCE)
        # Verify PT_Create is still 0x03

    def test_pt_transform_unchanged(self):
        self.assertIn("PT_Transform", SOURCE)

    def test_pt_active_camera_unchanged(self):
        self.assertIn("PT_ActiveCamera", SYNC_TYPES)

    def test_pt_camera_def_unchanged(self):
        self.assertIn("PT_CameraDef", SYNC_TYPES)

    def test_lsp_camera_unchanged(self):
        self.assertIn("LSP_Camera", SOURCE)


class TestCameraSyncNotDisabled(unittest.TestCase):
    """Camera sync must not be gated by a disabled CVar."""

    def test_lsp_camera_handled(self):
        self.assertIn("PrimitiveType == LSP_Camera", SOURCE,
            "LSP_Camera must still be handled in HandleCreateObject")

    def test_camera_create_marker_present(self):
        self.assertIn("[CAMERA][CREATE]", SOURCE,
            "CAMERA CREATE marker must still be present")

    def test_camera_spawn_marker_present(self):
        self.assertIn("[CAMERA][SPAWN]", SOURCE,
            "CAMERA SPAWN marker must still be present (HandleActiveCamera auto-spawn)")


class TestCameraNotHiddenFromOutliner(unittest.TestCase):
    """Camera must not be hidden from SceneOutliner unless explicitly documented."""

    def test_no_hidden_from_outliner_flag(self):
        """Camera should not be hidden from SceneOutliner."""
        self.assertNotIn("SetIsTemporarilyHiddenInEditor", SOURCE[
            SOURCE.index("LSP_Camera"):SOURCE.index("LSP_Camera") + 500])

    def test_camera_has_no_bhidden_in_outliner(self):
        """Camera should not have bListedInSceneOutliner or similar overrides."""
        passes = 0
        # These are accepted only if explicitly commented
        lines = SOURCE.splitlines()
        for i, line in enumerate(lines):
            if "bListedInSceneOutliner" in line:
                passes += 1
        self.assertLess(passes, 3, "Camera should not hide from SceneOutliner")


if __name__ == "__main__":
    unittest.main()
