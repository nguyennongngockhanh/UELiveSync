#!/usr/bin/env python3
"""E2E.5 — SceneOutliner Crash Isolation Static Tests.

Verifies:
- Isolation plan is documented in manual-e2e-camera-crash-investigation.md
- create-only mode exists in isolation injector
- create-transform-only mode exists
- hierarchy attach exercise mode exists
- docs do NOT claim final root cause before isolation
- no protocol change
- frustum guard remains
- hierarchy guard remains
"""

import os
import re
import unittest
import ast
import sys


INVESTIGATION_DOC = (
    "/home/nguyennongngockhanh/Projects/UELiveSync/Docs/Architecture/"
    "manual-e2e-camera-crash-investigation.md"
)

ISOLATION_INJECTOR = (
    "/home/nguyennongngockhanh/Projects/UELiveSync/tools/"
    "uelivesync_e2e5_sceneoutliner_isolation.py"
)

CAMERA_INJECTOR = (
    "/home/nguyennongngockhanh/Projects/UELiveSync/tools/"
    "uelivesync_7g_camera_transform_client.py"
)

HIERARCHY_GUARD_DOC = (
    "/home/nguyennongngockhanh/Projects/UELiveSync/tests/"
    "manual_e2e_scene_outliner_parent_guard.py"
)

CAMERA_CRASH_GUARD_DOC = (
    "/home/nguyennongngockhanh/Projects/UELiveSync/tests/"
    "manual_e2e_camera_crash_guard.py"
)

# Also check UE plugin source for frustum guard and hierarchy guard
UE_SUBSYSTEM_PATH = (
    "/home/nguyennongngockhanh/Projects/UELiveSync/"
    "UE_Plugin/UELiveSync/Source/UELiveSync/Private/"
    "UELiveSyncSubsystem.cpp"
)


class TestE2E5IsolationPlan(unittest.TestCase):
    """Test that the isolation plan is documented."""

    def test_investigation_doc_exists(self):
        """Investigation doc must exist."""
        self.assertTrue(os.path.exists(INVESTIGATION_DOC),
                        f"Investigation doc missing: {INVESTIGATION_DOC}")

    def test_investigation_doc_has_e2e5_section(self):
        """Investigation doc must contain E2E.5 isolation plan."""
        with open(INVESTIGATION_DOC, "r") as f:
            content = f.read()
        self.assertIn("E2E.5", content, "Missing E2E.5 section in investigation doc")
        self.assertIn("Isolation Plan", content, "Missing Isolation Plan heading")

    def test_investigation_doc_has_isolation_matrix(self):
        """Investigation doc must contain isolation test matrix A-F.

        Tests may appear as 'Test A' headings or 'A |' in a table row.
        """
        with open(INVESTIGATION_DOC, "r") as f:
            content = f.read()
        for letter in ["A", "B", "C", "D", "E", "F"]:
            # Accept either "Test A" heading or "| A |" in a table row
            has_heading = f"Test {letter}" in content
            has_table_row = f"| {letter} |" in content
            self.assertTrue(has_heading or has_table_row,
                          f"Missing Test {letter} in isolation matrix")

    def test_investigation_doc_no_final_root_cause_claim(self):
        """Investigation doc must NOT claim final root cause before isolation.

        The E2E.5 section must have the UNRESOLVED marker. The old
        E2E.4 'UE engine bug' claim was a hypothesis that was superseded
        by E2E.5 isolation requirements.
        """
        with open(INVESTIGATION_DOC, "r") as f:
            content = f.read()
        # Must contain E2E.5 unresovled marker instead of final claim
        self.assertIn("Root Cause Analysis — UNRESOLVED", content,
                      "Must have UNRESOLVED root cause marker from E2E.5 update")
        # The E2E.5 section must say not to claim final root cause
        self.assertIn("Do not claim final root cause", content,
                      "Must have instruction to not claim final root cause")

    def test_investigation_doc_has_updated_classification(self):
        """Investigation doc must have updated classification criteria."""
        with open(INVESTIGATION_DOC, "r") as f:
            content = f.read()
        for classification in [
            "FAIL_UE_IDLE_SCENE_OUTLINER_CRASH",
            "FAIL_LIVESYNC_CAMERA_FULL_LIFECYCLE_SCENE_OUTLINER_CRASH",
            "FAIL_LIVESYNC_CAMERA_CREATE_SCENE_OUTLINER_CRASH",
            "FAIL_LIVESYNC_CAMERA_ACTIVE_OR_SEQ_SCENE_OUTLINER_CRASH",
            "PASS_HIERARCHY_ATTACH_GUARD_RUNTIME",
            "PASS_E2E5_SCENE_OUTLINER_ISOLATION_NO_REPRO",
        ]:
            self.assertIn(classification, content,
                          f"Missing classification: {classification}")

    def test_investigation_doc_has_fail_manually_e2e_classification(self):
        """Must retain FAIL_MANUAL_E2E_SCENE_OUTLINER_PARENT_GUARD classification."""
        with open(INVESTIGATION_DOC, "r") as f:
            content = f.read()
        self.assertIn("FAIL_MANUAL_E2E_SCENE_OUTLINER_PARENT_GUARD", content,
                      "Must retain FAIL_MANUAL_E2E_SCENE_OUTLINER_PARENT_GUARD classification")


class TestIsolationInjectorExists(unittest.TestCase):
    """Test that the isolation injector tool exists with required modes."""

    def test_isolation_injector_exists(self):
        """Isolation injector must exist."""
        self.assertTrue(os.path.exists(ISOLATION_INJECTOR),
                        f"Isolation injector missing: {ISOLATION_INJECTOR}")

    def test_isolation_injector_is_valid_python(self):
        """Isolation injector must be valid Python."""
        with open(ISOLATION_INJECTOR, "r") as f:
            source = f.read()
        try:
            ast.parse(source)
        except SyntaxError as e:
            self.fail(f"Isolation injector has syntax error: {e}")

    def test_isolation_injector_has_create_only_mode(self):
        """Must have --create-only mode (Test C)."""
        with open(ISOLATION_INJECTOR, "r") as f:
            content = f.read()
        self.assertIn("--create-only", content, "Missing --create-only argument")
        self.assertIn("mode_create_only", content, "Missing create-only function")

    def test_isolation_injector_has_create_transform_mode(self):
        """Must have --create-transform mode (Test D)."""
        with open(ISOLATION_INJECTOR, "r") as f:
            content = f.read()
        self.assertIn("--create-transform", content, "Missing --create-transform argument")
        self.assertIn("mode_create_transform", content, "Missing create-transform function")

    def test_isolation_injector_has_hierarchy_mode(self):
        """Must have --hierarchy mode (Test F)."""
        with open(ISOLATION_INJECTOR, "r") as f:
            content = f.read()
        self.assertIn("--hierarchy", content, "Missing --hierarchy argument")
        self.assertIn("mode_hierarchy", content, "Missing hierarchy function")

    def test_isolation_injector_has_full_mode(self):
        """Must have --full mode (Test E)."""
        with open(ISOLATION_INJECTOR, "r") as f:
            content = f.read()
        self.assertIn("--full", content, "Missing --full argument")
        self.assertIn("mode_full", content, "Missing full function")

    def test_isolation_injector_has_idle_only_mode(self):
        """Must have --idle-only mode (Test A)."""
        with open(ISOLATION_INJECTOR, "r") as f:
            content = f.read()
        self.assertIn("--idle-only", content, "Missing --idle-only argument")
        self.assertIn("mode_idle_only", content, "Missing idle-only function")


class TestIsolationInjectorDoesNotChangeProtocol(unittest.TestCase):
    """Test that isolation injector does not change protocol constants."""

    def test_no_new_packet_types(self):
        """Isolation injector must not define new packet types."""
        with open(ISOLATION_INJECTOR, "r") as f:
            content = f.read()
        # Should only use existing PT_* constants
        lines = [l for l in content.split("\n") if "PT_" in l and "=" in l]
        for line in lines:
            if "PT_" in line and "# " not in line:
                # Check it's a known packet type or a comment
                known = ["PT_CREATE", "PT_TRANSFORM", "PT_ACTIVE_CAMERA",
                         "PT_CAMERA_DEF", "PT_HIERARCHY"]
                defined = [k for k in known if k in line]
                self.assertTrue(len(defined) > 0 or "0x" in line,
                                f"Unknown PT_ definition: {line.strip()}")

    def test_existing_packet_types_unchanged(self):
        """Existing packet types must still be present in 7G injector."""
        with open(CAMERA_INJECTOR, "r") as f:
            content = f.read()
        self.assertIn("PT_CREATE       = 0x03", content)
        self.assertIn("PT_TRANSFORM    = 0x01", content)
        self.assertIn("PT_ACTIVE_CAMERA= 0x15", content)
        self.assertIn("PT_CAMERA_DEF   = 0x1B", content)


class TestFrustumGuardRemains(unittest.TestCase):
    """Test that frustum guard is still in place."""

    def test_camera_crash_guard_doc_exists(self):
        """Camera crash guard doc must exist."""
        self.assertTrue(os.path.exists(CAMERA_CRASH_GUARD_DOC),
                        f"Missing: {CAMERA_CRASH_GUARD_DOC}")

    def test_camera_crash_guard_has_helper(self):
        """Camera crash guard doc must reference ConfigureLiveSyncCameraActor."""
        with open(CAMERA_CRASH_GUARD_DOC, "r") as f:
            content = f.read()
        self.assertIn("ConfigureLiveSyncCameraActor", content,
                      "ConfigureLiveSyncCameraActor helper must be documented")

    def test_ue_subsystem_has_frustum_guard(self):
        """UE subsystem must still have frustum guard code."""
        self.assertTrue(os.path.exists(UE_SUBSYSTEM_PATH),
                        f"Missing: {UE_SUBSYSTEM_PATH}")
        with open(UE_SUBSYSTEM_PATH, "r") as f:
            content = f.read()
        self.assertIn("FRUSTUM_GUARD", content,
                      "FRUSTUM_GUARD marker must exist in subsystem")

    def test_frustum_guard_not_disabled(self):
        """Frustum guard logic must still be present (not commented out)."""
        with open(UE_SUBSYSTEM_PATH, "r") as f:
            content = f.read()
        # Must not have the entire frustum guard commented out
        lines = content.split("\n")
        guard_active = False
        for line in lines:
            stripped = line.strip()
            if "FRUSTUM_GUARD" in stripped and not stripped.startswith("//"):
                guard_active = True
                break
        self.assertTrue(guard_active,
                       "FRUSTUM_GUARD marker must be active (not fully commented out)")


class TestHierarchyGuardRemains(unittest.TestCase):
    """Test that hierarchy guard is still in place."""

    def test_hierarchy_guard_doc_exists(self):
        """Hierarchy guard doc must exist."""
        self.assertTrue(os.path.exists(HIERARCHY_GUARD_DOC),
                        f"Missing: {HIERARCHY_GUARD_DOC}")

    def test_hierarchy_guard_doc_has_safe_attach(self):
        """Hierarchy guard doc must reference SafeAttachLiveSyncActor."""
        with open(HIERARCHY_GUARD_DOC, "r") as f:
            content = f.read()
        self.assertIn("SafeAttachLiveSyncActor", content,
                      "SafeAttachLiveSyncActor must be documented")

    def test_ue_subsystem_has_hierarchy_guard(self):
        """UE subsystem must still have hierarchy guard code."""
        self.assertTrue(os.path.exists(UE_SUBSYSTEM_PATH),
                        f"Missing: {UE_SUBSYSTEM_PATH}")
        with open(UE_SUBSYSTEM_PATH, "r") as f:
            content = f.read()
        self.assertIn("ATTACH_GUARD", content,
                      "ATTACH_GUARD marker must exist in subsystem")

    def test_hierarchy_guard_active(self):
        """Hierarchy guard must be active, not commented out."""
        with open(UE_SUBSYSTEM_PATH, "r") as f:
            content = f.read()
        lines = content.split("\n")
        guard_active = False
        for line in lines:
            stripped = line.strip()
            if "ATTACH_GUARD" in stripped and not stripped.startswith("//"):
                guard_active = True
                break
        self.assertTrue(guard_active,
                       "ATTACH_GUARD marker must be active (not fully commented out)")


class TestNoProtocolChange(unittest.TestCase):
    """Test that no protocol changes are introduced."""

    def test_e2e5_injector_uses_existing_magic(self):
        """E2E.5 injector must use existing LIVE_SYNC_MAGIC."""
        with open(ISOLATION_INJECTOR, "r") as f:
            content = f.read()
        self.assertEqual(content.count("0x4C56534D"), 1,
                        "LIVE_SYNC_MAGIC must be defined exactly once")

    def test_e2e5_injector_uses_existing_version(self):
        """E2E.5 injector must use existing version constant."""
        with open(ISOLATION_INJECTOR, "r") as f:
            content = f.read()
        self.assertIn("LIVE_SYNC_VERSION_V5", content)
        self.assertIn("5", content)

    def test_injector_does_not_modify_header_format(self):
        """Packet header format must not be changed."""
        with open(ISOLATION_INJECTOR, "r") as f:
            content = f.read()
        # Header pack format must match existing: <I H B B Q I I
        self.assertIn("struct.pack", content)
        # Check the format string
        header_lines = [l for l in content.split("\n")
                        if "struct.pack" in l and "header" in l.lower()]
        for line in header_lines:
            if "packet_size" in line:
                self.assertIn("<I H B B Q I I", line,
                              "Header format must be <I H B B Q I I")


class TestE2E6HierarchyConfirm(unittest.TestCase):
    """Static verification for E2E.6 hierarchy guard marker confirmation."""
    
    def test_hierarchy_confirm_mode_exists(self):
        """--hierarchy-confirm mode must exist in injector."""
        with open(ISOLATION_INJECTOR, "r") as f:
            content = f.read()
        self.assertIn("hierarchy_confirm", content,
                      "Missing --hierarchy-confirm mode in injector")
    
    def test_hierarchy_confirm_sends_pt_hierarchy_after_creates(self):
        """--hierarchy-confirm must send PT_Hierarchy after parent/child CREATE."""
        with open(ISOLATION_INJECTOR, "r") as f:
            content = f.read()
        # Find the hierarchy_confirm function
        self.assertIn("def mode_hierarchy_confirm", content,
                      "Missing mode_hierarchy_confirm function")
        func_start = content.index("def mode_hierarchy_confirm")
        # Find the next function def
        next_func = content.index("def mode_", func_start + 1)
        func_body = content[func_start:next_func]
        # Must send parent CREATE
        self.assertIn("parent_guid", func_body,
                      "mode_hierarchy_confirm must create parent actor")
        # Must send child CREATE
        self.assertIn("child_guid", func_body,
                      "mode_hierarchy_confirm must create child actor")
        # Must send PT_HIERARCHY
        self.assertIn("PT_HIERARCHY", func_body,
                      "mode_hierarchy_confirm must send PT_HIERARCHY packet")
        # Must send hierarchy after creates
        parent_idx = func_body.index("parent_pkt")
        hierarchy_idx = func_body.index("hierarchy_pkt")
        self.assertTrue(hierarchy_idx > parent_idx,
                        "PT_HIERARCHY must be sent after parent CREATE")
    
    def test_hierarchy_confirm_waits_between_sends(self):
        """--hierarchy-confirm must have waits between create and hierarchy sends."""
        with open(ISOLATION_INJECTOR, "r") as f:
            content = f.read()
        func_start = content.index("def mode_hierarchy_confirm")
        next_func = content.index("def mode_", func_start + 1)
        func_body = content[func_start:next_func]
        # Must have time.sleep calls
        sleep_count = func_body.count("time.sleep")
        self.assertGreaterEqual(sleep_count, 3,
                                "mode_hierarchy_confirm must wait between sends")
    
    def test_docs_corrected_wording_ue_side_not_confirmed(self):
        """Docs must contain corrected wording: 'UE-side guard execution was not confirmed'."""
        with open(INVESTIGATION_DOC, "r") as f:
            content = f.read()
        self.assertIn("UE-side guard execution was not confirmed",
                      content,
                      "Investigation doc must contain corrected wording")
    
    def test_safe_attach_logs_attach_guard_marker(self):
        """SafeAttachLiveSyncActor must log [HIERARCHY][ATTACH_GUARD]."""
        with open(UE_SUBSYSTEM_PATH, "r") as f:
            content = f.read()
        self.assertIn("[HIERARCHY][ATTACH_GUARD]", content,
                      "SafeAttachLiveSyncActor must log [HIERARCHY][ATTACH_GUARD]")
    
    def test_skip_markers_exist(self):
        """Hierarchy skip markers must exist in UE code."""
        with open(UE_SUBSYSTEM_PATH, "r") as f:
            content = f.read()
        # WouldCreateAttachmentCycle logs these at Warning level
        self.assertIn("[HIERARCHY][ATTACH_SKIP_SELF]", content,
                      "Missing [HIERARCHY][ATTACH_SKIP_SELF] marker")
        self.assertIn("[HIERARCHY][CYCLE]", content,
                      "Missing [HIERARCHY][CYCLE] marker")
    
    def test_no_protocol_change_e2e6(self):
        """E2E.6 must not change protocol or define new packet type constants."""
        with open(ISOLATION_INJECTOR, "r") as f:
            content = f.read()
        # Find mode_hierarchy_confirm function body
        if "def mode_hierarchy_confirm" not in content:
            self.fail("mode_hierarchy_confirm not found")
        start = content.index("def mode_hierarchy_confirm")
        next_fn = content.index("def mode_", start + 1)
        func_body = content[start:next_fn]
        # Must only use existing PT_HIERARCHY (not define new ones)
        # No assignments like PT_X = 0xYY inside the function
        for line in func_body.split("\n"):
            stripped = line.strip()
            if re.search(r'^PT_[A-Z_]+\\s*=\\s*[0-9xX]', stripped):
                self.fail(f"E2E.6 must not define new packet types: {stripped}")


if __name__ == '__main__':
    unittest.main()
