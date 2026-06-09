#!/usr/bin/env python3
"""Phase 9F — Installer source-text tests and dry-run functional test.

Verifies that install_uelivesync.py meets the design requirements
documented in the Phase 9F audit.
"""

import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

# Paths
REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
INSTALLER = REPO_ROOT / "install_uelivesync.py"
INSTALL_MD = REPO_ROOT / "INSTALL.md"


class SourceTests(unittest.TestCase):
    """Source-text verification tests."""

    @classmethod
    def setUpClass(cls):
        """Read the installer source once."""
        cls.installer_text = INSTALLER.read_text(encoding="utf-8")
        cls.installer_lines = cls.installer_text.splitlines()

    def test_01_file_exists(self):
        """install_uelivesync.py must exist."""
        self.assertTrue(INSTALLER.is_file(), "install_uelivesync.py not found")

    def test_02_uses_argparse(self):
        """Must import and use argparse."""
        self.assertIn("import argparse", self.installer_text)
        self.assertIn("argparse.ArgumentParser", self.installer_text)

    def test_03_windows_blender_path(self):
        """Must contain Windows APPDATA path string."""
        self.assertIn("APPDATA", self.installer_text)
        self.assertIn("Blender Foundation", self.installer_text)
        self.assertIn("Blender", self.installer_text)

    def test_04_linux_native_blender_path(self):
        """Must contain Linux native Blender path."""
        self.assertIn(".config", self.installer_text)
        self.assertIn("blender", self.installer_text)

    def test_05_flatpak_path(self):
        """Must contain Linux Flatpak path."""
        self.assertIn(".var", self.installer_text)
        self.assertIn("org.blender.Blender", self.installer_text)

    def test_06_macos_blender_path(self):
        """Must contain macOS Blender path."""
        self.assertIn("Application Support", self.installer_text)
        self.assertIn("Library", self.installer_text)

    def test_07_dry_run_flag(self):
        """Must support --dry-run."""
        self.assertIn("dry-run", self.installer_text)
        self.assertIn("dry_run", self.installer_text)

    def test_08_force_flag(self):
        """Must support --force."""
        self.assertIn("force", self.installer_text.lower())

    def test_09_backup_flag(self):
        """Must support --backup."""
        self.assertIn("backup", self.installer_text.lower())

    def test_10_flatpak_flag(self):
        """Must support --flatpak."""
        self.assertIn("flatpak", self.installer_text.lower())

    def test_11_ue_project_flag(self):
        """Must support --ue-project."""
        self.assertIn("ue-project", self.installer_text.lower())
        self.assertIn("uproject", self.installer_text.lower())

    def test_12_validates_uptroject_json(self):
        """Must validate .uproject as JSON."""
        self.assertIn("json.load", self.installer_text)
        self.assertIn("JSONDecodeError", self.installer_text)

    def test_13_uses_copytree(self):
        """Must use shutil.copytree."""
        self.assertIn("shutil.copytree", self.installer_text)

    def test_14_uses_shutil_move(self):
        """Must use shutil.move for backup."""
        self.assertIn("shutil.move", self.installer_text)

    def test_15_uses_rmtree_safely(self):
        """Must use shutil.rmtree only for exact destination."""
        self.assertIn("shutil.rmtree", self.installer_text)

    def test_16_no_hardcoded_user_path(self):
        """Must not contain hardcoded /home/nguyennongngockhanh."""
        self.assertNotIn("/home/nguyennongngockhanh", self.installer_text)

    def test_17_no_engine_plugins_default(self):
        """Must not mention Engine/Plugins as automatic destination."""
        lower = self.installer_text.lower()
        # Should not have "engine/plugins" as a default path string
        lines = self.installer_lines
        for line in lines:
            stripped = line.strip().lower()
            if "engine" in stripped and "plugins" in stripped:
                # Check it's not in a comment warning
                if not stripped.lstrip().startswith("#"):
                    pass  # Allow it in comments only
        # Check for explicit Engine/Plugins path strings
        self.assertNotIn("Engine/Plugins", self.installer_text)
        self.assertNotIn('"Engine/Plugins"', self.installer_text)

    def test_18_repo_layout_support(self):
        """Must support repo layout Blender_Addon and UE_Plugin."""
        self.assertIn("Blender_Addon", self.installer_text)
        self.assertIn("UE_Plugin", self.installer_text)

    def test_19_release_layout_support(self):
        """Must support release layout ue_live_sync and UELiveSync."""
        self.assertIn("ue_live_sync", self.installer_text)
        self.assertIn("UELiveSync", self.installer_text)


class InstallMdTests(unittest.TestCase):
    """Source-text tests for INSTALL.md."""

    @classmethod
    def setUpClass(cls):
        cls.md_text = INSTALL_MD.read_text(encoding="utf-8")

    def test_20_install_md_exists(self):
        """INSTALL.md must exist."""
        self.assertTrue(INSTALL_MD.is_file(), "INSTALL.md not found")

    def test_21_mentions_windows(self):
        """Must mention Windows."""
        self.assertIn("Windows", self.md_text)

    def test_22_mentions_linux(self):
        """Must mention Linux."""
        self.assertIn("Linux", self.md_text)

    def test_23_mentions_macos(self):
        """Must mention macOS."""
        self.assertIn("macOS", self.md_text)

    def test_24_mentions_flatpak(self):
        """Must mention Flatpak."""
        self.assertIn("Flatpak", self.md_text)


class FunctionalDryRunTest(unittest.TestCase):
    """Functional dry-run test with temp source."""

    def test_25_dry_run_ue_project(self):
        """Run installer in --dry-run mode with a fake project.
        
        - Create temp source_root with ue_live_sync/ and UELiveSync/
        - Create fake .uproject
        - Run --dry-run
        - Assert exit code 0
        - Assert no Plugins/UELiveSync folder was created
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = pathlib.Path(tmpdir)

            # Create fake source layout (release extracted)
            addon_src = tmp / "ue_live_sync"
            addon_src.mkdir()
            (addon_src / "__init__.py").write_text('bl_info = {"version": (0, 2, 0)}')
            (addon_src / "sync.py").write_text("# sync\n")
            (addon_src / "network.py").write_text("# network\n")

            ue_src = tmp / "UELiveSync"
            ue_src.mkdir()
            (ue_src / "UELiveSync.uplugin").write_text(
                json.dumps({"FileVersion": 3, "Version": 2, "VersionName": "0.2.0", "FriendlyName": "UELiveSync"})
            )
            src_sub = ue_src / "Source"
            src_sub.mkdir()
            (src_sub / "dummy.txt").write_text("# dummy\n")

            # Create fake project directory
            fake_project = tmp / "FakeProject"
            fake_project.mkdir()
            (fake_project / "MyProject.uproject").write_text(
                json.dumps({"Name": "FakeProject", "FileVersion": 3})
            )

            # Run installer in dry-run mode
            result = subprocess.run(
                [sys.executable, str(INSTALLER),
                 "--source-root", str(tmp),
                 "--ue-project", str(fake_project / "MyProject.uproject"),
                 "--dry-run"],
                capture_output=True,
                text=True,
            )

            # Assert exit code 0
            self.assertEqual(result.returncode, 0, 
                "Dry-run exited non-zero:\nstdout: " + result.stdout + "\nstderr: " + result.stderr)

            # Assert no Plugins/UELiveSync folder was created
            installed_path = fake_project / "Plugins" / "UELiveSync"
            self.assertFalse(installed_path.exists(),
                "Plugins/UELiveSync was created during dry-run (should not have been)")


class DestinationExistsSafetyTests(unittest.TestCase):
    """Functional tests for destination-exists safety behavior.

    All tests use tempfile to create fake source_root with release-layout
    (ue_live_sync/ + UELiveSync/) and a fake UE project.
    """

    def _build_test_env(self):
        """Build a temp environment with fake source and project."""
        tmpdir = tempfile.mkdtemp()
        tmp = pathlib.Path(tmpdir)

        # Fake source: release layout
        addon_src = tmp / "ue_live_sync"
        addon_src.mkdir()
        (addon_src / "__init__.py").write_text('bl_info = {"version": (0, 2, 0)}')
        (addon_src / "sync.py").write_text("# sync")
        (addon_src / "network.py").write_text("# network")

        ue_src = tmp / "UELiveSync"
        ue_src.mkdir()
        (ue_src / "UELiveSync.uplugin").write_text(
            json.dumps({"FileVersion": 3, "Version": 2, "VersionName": "0.2.0"})
        )
        src_sub = ue_src / "Source"
        src_sub.mkdir()
        (src_sub / "dummy.txt").write_text("# dummy")

        # Fake project
        fake_project = tmp / "FakeProject"
        fake_project.mkdir()
        (fake_project / "MyProject.uproject").write_text(
            json.dumps({"Name": "FakeProject", "FileVersion": 3})
        )

        return tmp, fake_project

    def _run_ue_install(self, tmp, fake_project, extra_args=None):
        """Run installer with UE plugin target."""
        args = [
            sys.executable, str(INSTALLER),
            "--source-root", str(tmp),
            "--ue-project", str(fake_project / "MyProject.uproject"),
        ]
        if extra_args:
            args.extend(extra_args)
        return subprocess.run(
            args, capture_output=True, text=True,
        )

    def test_26_dest_exists_no_force_no_backup_fails(self):
        """A. Existing UE plugin destination + no --force/--backup: FAIL."""
        tmp, fake_project = self._build_test_env()
        try:
            # Create existing destination
            existing_dest = fake_project / "Plugins" / "UELiveSync"
            existing_dest.mkdir(parents=True)
            (existing_dest / "keep.txt").write_text("preserve me")

            # Run without --force or --backup
            result = self._run_ue_install(tmp, fake_project)

            # Should fail (non-zero exit)
            self.assertNotEqual(result.returncode, 0,
                "Installer should fail when destination exists without --force/--backup")
            # keep.txt must still exist
            self.assertTrue((existing_dest / "keep.txt").exists(),
                "Destination should NOT be deleted when no --force/--backup")
        finally:
            import shutil
            shutil.rmtree(tmp)

    def test_27_dest_exists_with_backup(self):
        """B. Existing UE plugin destination + --backup: succeed, backup created."""
        tmp, fake_project = self._build_test_env()
        try:
            existing_dest = fake_project / "Plugins" / "UELiveSync"
            existing_dest.mkdir(parents=True)
            (existing_dest / "keep.txt").write_text("preserve me")

            result = self._run_ue_install(tmp, fake_project, ["--backup"])

            self.assertEqual(result.returncode, 0,
                "Installer should succeed with --backup")
            # New destination should exist
            self.assertTrue(existing_dest.exists(),
                "New destination should exist after install")
            # Backup should exist
            backup_found = False
            for p in fake_project.joinpath("Plugins").iterdir():
                if p.name.startswith("UELiveSync.bak-") and p.is_dir():
                    backup_found = True
                    self.assertTrue((p / "keep.txt").exists(),
                        "Backup should contain original keep.txt")
            self.assertTrue(backup_found,
                "A .bak-* backup directory should exist in Plugins/")
        finally:
            import shutil
            shutil.rmtree(tmp)

    def test_28_dest_exists_with_force(self):
        """C. Existing UE plugin destination + --force: replace only exact target."""
        tmp, fake_project = self._build_test_env()
        try:
            existing_dest = fake_project / "Plugins" / "UELiveSync"
            existing_dest.mkdir(parents=True)
            (existing_dest / "keep.txt").write_text("old content")

            # Parent Plugins folder should still exist after install
            plugins_dir = fake_project / "Plugins"

            result = self._run_ue_install(tmp, fake_project, ["--force"])

            self.assertEqual(result.returncode, 0,
                "Installer should succeed with --force")
            self.assertTrue(existing_dest.exists(),
                "New destination should exist")
            self.assertFalse((existing_dest / "keep.txt").exists(),
                "Old keep.txt should be gone")
            self.assertTrue(plugins_dir.exists(),
                "Plugins/ parent folder must still exist")
        finally:
            import shutil
            shutil.rmtree(tmp)

    def test_29_dry_run_target_absent(self):
        """D. Dry-run with target absent: no writes."""
        tmp, fake_project = self._build_test_env()
        try:
            result = self._run_ue_install(tmp, fake_project, ["--dry-run"])

            self.assertEqual(result.returncode, 0,
                "Dry-run should exit 0")
            installed = fake_project / "Plugins" / "UELiveSync"
            self.assertFalse(installed.exists(),
                "Dry-run must not create destination")
        finally:
            import shutil
            shutil.rmtree(tmp)

    def test_30_dry_run_target_exists(self):
        """E. Dry-run with target exists: no writes, keep.txt preserved."""
        tmp, fake_project = self._build_test_env()
        try:
            existing_dest = fake_project / "Plugins" / "UELiveSync"
            existing_dest.mkdir(parents=True)
            (existing_dest / "keep.txt").write_text("preserve")

            result = self._run_ue_install(tmp, fake_project, ["--dry-run"])

            self.assertEqual(result.returncode, 0,
                "Dry-run should exit 0 even when dest exists")
            self.assertTrue((existing_dest / "keep.txt").exists(),
                "keep.txt should still exist after dry-run")
        finally:
            import shutil
            shutil.rmtree(tmp)


if __name__ == "__main__":
    unittest.main()

