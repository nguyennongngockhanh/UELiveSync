#!/usr/bin/env python3
"""
tests/ue57_compile_compatibility.py

Static-analysis tests for E2E.7 — UE5.7 Compile Compatibility Cleanup.

Verifies:
1. No "->bPendingKill" or ".bPendingKill" access in UELiveSyncSubsystem.cpp
2. IsLiveSyncActorInvalidForAttach helper exists
3. WouldCreateAttachmentCycle uses helper or safe public API
4. No protocol change (packet IDs unchanged)
5. 0x02 remains reserved/invalid
6. 0x10 remains unused
7. Frustum guard still exists (E2E.2 preserved)
8. Hierarchy attach/cycle markers still exist
9. SetNum with EAllowShrinking::No (not bool)
"""

import os
import re
import sys
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


class TestNoBPendingKillAccess(unittest.TestCase):
    """No direct access to removed AActor::bPendingKill member."""

    def test_no_bpedingkill_member_access(self):
        for line_no, line in enumerate(SOURCE.splitlines(), 1):
            stripped = line.strip()
            # Skip comments
            if stripped.startswith("//") or stripped.startswith("/*") or stripped.startswith("*"):
                continue
            if "bPendingKill" in stripped and "->bPendingKill" in stripped:
                self.fail(f"Line {line_no}: '->bPendingKill' access found: {stripped}")
            if "bPendingKill" in stripped and ".bPendingKill" in stripped:
                self.fail(f"Line {line_no}: '.bPendingKill' access found: {stripped}")


class TestIsLiveSyncActorInvalidForAttach(unittest.TestCase):
    """Helper function exists to replace bPendingKill checks."""

    def test_helper_exists(self):
        self.assertIn(
            "IsLiveSyncActorInvalidForAttach", SOURCE,
            "IsLiveSyncActorInvalidForAttach must exist in source")

    def test_helper_is_static(self):
        self.assertIn(
            "static bool IsLiveSyncActorInvalidForAttach", SOURCE,
            "Helper must be static")

    def test_helper_checks_null(self):
        self.assertIn(
            "Actor == nullptr", SOURCE,
            "Helper must check for null")

    def test_helper_checks_isvalid(self):
        self.assertIn(
            "IsValid(Actor)", SOURCE,
            "Helper must call IsValid")

    def test_helper_checks_actor_being_destroyed(self):
        self.assertIn(
            "IsActorBeingDestroyed", SOURCE,
            "Helper must check IsActorBeingDestroyed")


class TestWouldCreateAttachmentCycleUsesHelper(unittest.TestCase):
    """WouldCreateAttachmentCycle uses the helper or safe public API."""

    def test_uses_helper_in_pending_kill_block(self):
        self.assertIn(
            "IsLiveSyncActorInvalidForAttach(Child)",
            SOURCE,
            "WouldCreateAttachmentCycle must use helper for Child validity")
        self.assertIn(
            "IsLiveSyncActorInvalidForAttach(Parent)",
            SOURCE,
            "WouldCreateAttachmentCycle must use helper for Parent validity")

    def test_uses_helper_in_chain_walk(self):
        self.assertIn(
            "IsLiveSyncActorInvalidForAttach(ParentActor)",
            SOURCE,
            "Chain walk must use helper for ParentActor validity")


class TestNoProtocolChange(unittest.TestCase):
    """Packet IDs, reserved values, and unused values unchanged."""

    def test_PT_Transform_unchanged(self):
        self.assertIn("PT_Transform = 0x01", SYNC_TYPES)

    def test_PT_Reserved_02_unchanged(self):
        self.assertIn("PT_Reserved_02 = 0x02", SYNC_TYPES)

    def test_no_packet_type_0x10(self):
        packet_type_lines = [l for l in SYNC_TYPES.splitlines() if "PT_" in l]
        for line in packet_type_lines:
            stripped = line.strip()
            if stripped.startswith("//"):
                continue
            if "constexpr" in stripped:
                continue
            if "0x10" in stripped:
                self.fail(f"Packet type should not use 0x10: {line}")


class TestFrustumGuardPreserved(unittest.TestCase):
    """E2E.1 frustum guard not removed."""

    def test_frustum_guard_marker_exists(self):
        self.assertIn("FRUSTUM_GUARD", SOURCE, "FRUSTUM_GUARD marker must exist")

    def test_configure_live_sync_camera_actor_exists(self):
        self.assertIn(
            "ConfigureLiveSyncCameraActor", SOURCE,
            "ConfigureLiveSyncCameraActor helper must exist")


class TestHierarchyMarkersPreserved(unittest.TestCase):
    """Hierarchy attach/cycle markers still present."""

    def test_hierarchy_attach_marker(self):
        self.assertIn("[HIERARCHY][ATTACH]", SOURCE)

    def test_hierarchy_cycle_marker(self):
        self.assertIn("[HIERARCHY][CYCLE]", SOURCE)

    def test_hierarchy_attach_skip_marker(self):
        self.assertIn("[HIERARCHY][ATTACH_SKIP]", SOURCE)


class TestSetNumDeprecationFixed(unittest.TestCase):
    """SetNum with bool arg replaced with EAllowShrinking::No."""

    def test_setnum_no_bool_arg(self):
        for line_no, line in enumerate(SOURCE.splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("//"):
                continue
            if "SetNum" in stripped and "false" in stripped:
                self.fail(f"Line {line_no}: SetNum with bool 'false' found: {stripped}")

    def test_setnum_uses_allow_shrinking(self):
        self.assertIn(
            "EAllowShrinking::No", SOURCE,
            "SetNum must use EAllowShrinking::No instead of bool")


class TestNoStaleReferences(unittest.TestCase):
    """No references to removed/deprecated APIs."""

    def test_no_dead_comment_references_to_bPendingKill_as_method(self):
        for line_no, line in enumerate(SOURCE.splitlines(), 1):
            if "bPendingKill" in line and not line.strip().startswith("//") and \
               not line.strip().startswith("*") and not line.strip().startswith("/*"):
                self.fail(f"Line {line_no}: non-comment bPendingKill reference: {line}")


if __name__ == "__main__":
    unittest.main()
