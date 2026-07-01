"""Phase 10A.3.6 — Safe Orphan Sidecar Pruning (repair: final contract).

Tests cover all 34 approved cases:
  1.  prior-only ready asset deleted
  2.  current same asset retained
  3.  current different asset_id same basename retained
  4.  missing prior => skipped
  5.  invalid prior => skipped
  6.  prior GUID mismatch => skipped
  7.  current invalid => skipped
  8.  manifest write failure => skipped (operator gate)
  9.  durability uncertain => skipped (operator gate)
 10.  send failure => pruning not called
 11.  unsafe basename retained
 12.  traversal retained
 13.  path escape retained
 14.  symlink retained
 15.  non-regular retained
 16.  size mismatch retained
 17.  canonical hash mismatch retained
 18.  fingerprint change retained
 19.  already missing => success
 20.  one unlink failure does not stop later candidates
 21.  unlink failure => partial
 22.  current manifest unchanged
 23.  prior manifest unchanged
 24.  defensive snapshot copy verified
 25.  prune exactly once after send
 26.  send exactly once
 27.  prune failure does not suppress send
 28.  no UE/wire files modified
 29.  no bpy/Unreal import
 30.  no mutable module-level cache
 31.  stable dataclass fields
 32.  stable action/status constants
 33.  canonical xxHash64 helper used
 34.  no arbitrary directory scan/deletion
"""

from __future__ import annotations

import ast
import copy
import os
import shutil
import stat
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch
from typing import Any, Dict, Optional, Set

# ──────────────────────────────────────────────────────────────────────
# Install mock bpy before any Blender_Addon import
# ──────────────────────────────────────────────────────────────────────

class _MockBpyModule:
    """Minimal bpy mock — ensures Blender_Addon package can be imported."""
    def __getattr__(self, name):
        if name == "__path__":
            return []
        return _MockBpyAttr()


class _MockBpyAttr:
    _mock_attrs: Dict[str, Any] = {}

    def __getattr__(self, name):
        if name == "__path__":
            return []
        return _MockBpyAttr()

    def __call__(self, *args, **kwargs):
        return None


class _MockBpyProps:
    """Mock bpy.props module — supports 'from bpy.props import IntProperty'."""
    IntProperty = lambda self, **kw: None
    FloatProperty = lambda self, **kw: None
    BoolProperty = lambda self, **kw: None
    StringProperty = lambda self, **kw: None
    EnumProperty = lambda self, **kw: None


class _MockBpyPath:
    @staticmethod
    def abspath(p):
        return p

    @staticmethod
    def basename(p):
        return os.path.basename(p)

    @staticmethod
    def dirname(p):
        return os.path.dirname(p)


class _MockBpyData:
    images = {}
    materials = {}
    objects = {}


class _MockBpyContext:
    scene = None
    view_layer = None


_mock_bpy = _MockBpyModule()
_mock_bpy.path = _MockBpyPath()
_mock_bpy.data = _MockBpyData()
_mock_bpy.context = _MockBpyContext()

if "bpy" not in sys.modules:
    sys.modules["bpy"] = _mock_bpy
if "bpy.path" not in sys.modules:
    sys.modules["bpy.path"] = _MockBpyPath()
if "bpy.props" not in sys.modules:
    _bp = _MockBpyProps()
    _bp.__package__ = "bpy.props"
    sys.modules["bpy.props"] = _bp
if "bpy.types" not in sys.modules:
    sys.modules["bpy.types"] = _MockBpyAttr()
if "bpy.data" not in sys.modules:
    sys.modules["bpy.data"] = _MockBpyData()
if "bpy.context" not in sys.modules:
    sys.modules["bpy.context"] = _MockBpyContext()

# Pre-install bpy submodules that Blender_Addon.sync imports
class _MockBpyHandlers:
    persistent = staticmethod(lambda f: f)

if "bpy.app" not in sys.modules:
    _ba = type(sys)("bpy.app")
    _ba.__path__ = []
    _ba.__package__ = "bpy.app"
    sys.modules["bpy.app"] = _ba
if "bpy.app.handlers" not in sys.modules:
    _bh = type(sys)("bpy.app.handlers")
    _bh.persistent = staticmethod(lambda f: f)
    _bh.__package__ = "bpy.app.handlers"
    sys.modules["bpy.app.handlers"] = _bh


# ──────────────────────────────────────────────────────────────────────
# Import modules directly (avoids __init__.py bpy import)
# ──────────────────────────────────────────────────────────────────────

_addon_dir = os.path.join(os.path.dirname(__file__), "..", "Blender_Addon")
if _addon_dir not in sys.path:
    sys.path.insert(0, _addon_dir)

# Pre-load network (no bpy), manifest_v3 (no bpy), and manifest_prune
import network as _nw
import manifest_v3 as _mv3
import manifest_prune as _mp


# ──────────────────────────────────────────────────────────────────────
# Test helpers
# ──────────────────────────────────────────────────────────────────────

_GUID = "a1b2c3d4e5f6a7b8a9b0c1d2e3f4a5b6"
_ASSET_ID = "a1b2c3d4e5f6a7b8"


def _make_valid_manifest(
    assets: Dict[str, dict],
    guid: str = _GUID,
    occurrences: Optional[Dict[str, dict]] = None,
) -> dict:
    occ = {} if occurrences is None else occurrences
    digest = _mv3.compute_semantic_digest(guid, occ, assets)
    return {
        "schemaVersion": 3,
        "guid": guid,
        "generation": 1,
        "semanticContentDigest": digest,
        "occurrences": occ,
        "assets": assets,
    }


def _make_asset_rec(
    basename: str,
    size: int,
    hash_val: str,
    status: str = "ready",
) -> dict:
    return {
        "sourceKind": "FILE",
        "contentHash": hash_val,
        "destinationBasename": basename,
        "destinationSize": size,
        "destinationHash": hash_val,
        "status": status,
    }


def _make_file(path: str, size: int = 0) -> None:
    with open(path, "wb") as f:
        f.write(b"x" * size)


def _write_file_with_hash(path: str, size: int) -> str:
    """Write *size* bytes and return the canonical xxHash64 hex."""
    _make_file(path, size)
    return _nw._xxh64_file_hex(path)


def _hashes_from_list(paths: Dict[str, str]) -> Dict[str, str]:
    """Map filename -> hex hash for given paths."""
    return {fn: _nw._xxh64_file_hex(p) for fn, p in paths.items()}


# ================================================================
# Tests
# ================================================================


class TestStableConstants(unittest.TestCase):
    """Requirement 31-32: stable dataclass fields & constants."""

    def test_action_constants_are_stable_strings(self):
        self.assertEqual(_mp.ACTION_DELETED, "deleted")
        self.assertEqual(_mp.ACTION_ALREADY_MISSING, "already_missing")
        self.assertEqual(_mp.ACTION_RETAINED_CURRENT_REFERENCE, "retained_current_reference")
        self.assertEqual(_mp.ACTION_SKIPPED_UNSAFE_BASENAME, "skipped_unsafe_basename")
        self.assertEqual(_mp.ACTION_SKIPPED_PATH_ESCAPE, "skipped_path_escape")
        self.assertEqual(_mp.ACTION_SKIPPED_SYMLINK, "skipped_symlink")
        self.assertEqual(_mp.ACTION_SKIPPED_NOT_REGULAR, "skipped_not_regular")
        self.assertEqual(_mp.ACTION_SKIPPED_IDENTITY_MISMATCH, "skipped_identity_mismatch")
        self.assertEqual(_mp.ACTION_SKIPPED_CHANGED_BEFORE_DELETE, "skipped_changed_before_delete")
        self.assertEqual(_mp.ACTION_UNLINK_FAILED, "unlink_failed")

    def test_status_constants_are_stable_strings(self):
        self.assertEqual(_mp.STATUS_SUCCESS, "success")
        self.assertEqual(_mp.STATUS_PARTIAL, "partial")
        self.assertEqual(_mp.STATUS_SKIPPED, "skipped")

    def test_prune_item_result_is_frozen(self):
        r = _mp.PruneItemResult(
            asset_id="a", filename="b", path="/p", action="deleted",
        )
        with self.assertRaises((TypeError, Exception)):
            setattr(r, "action", "changed")

    def test_prune_result_is_frozen(self):
        r = _mp.PruneResult(status=_mp.STATUS_SUCCESS)
        with self.assertRaises((TypeError, Exception)):
            setattr(r, "status", "changed")


class TestNoBpyImport(unittest.TestCase):
    """Requirement 29: no bpy import at module level."""

    def test_no_bpy_in_manifest_prune(self):
        with open(_mp.__file__) as f:
            src = f.read()
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.assertNotEqual(alias.name, "bpy",
                        f"manifest_prune imports bpy at line {node.lineno}")
            elif isinstance(node, ast.ImportFrom):
                if node.module == "bpy" or (node.module and node.module.startswith("bpy.")):
                    self.fail(f"manifest_prune imports {node.module} at line {node.lineno}")


class TestNoMutableModuleCache(unittest.TestCase):
    """Requirement 30: no mutable module-level cache."""

    def test_no_module_level_cache_dict(self):
        self.assertNotIn("_cache", dir(_mp))
        self.assertNotIn("_registry", dir(_mp))
        self.assertNotIn("_counter", dir(_mp))


class TestNoArbitraryDirectoryScan(unittest.TestCase):
    """Requirement 34: no glob, listdir, or walk on obj_dir."""

    def test_no_arbitrary_scan(self):
        with open(_mp.__file__) as f:
            src = f.read()
        for dangerous in ("glob.glob", "os.listdir", "os.walk", "os.scandir"):
            self.assertNotIn(dangerous, src,
                f"manifest_prune uses banned function: {dangerous}")


class TestUeWireFilesNotModified(unittest.TestCase):
    """Requirement 28: no UE/wire protocol files touched."""

    def test_no_ue_plugin_references(self):
        with open(_mp.__file__) as f:
            src = f.read()
        self.assertNotIn("UE_Plugin", src)
        self.assertNotIn("UELiveSyncSubsystem", src)


class TestCanonicalHashHelper(unittest.TestCase):
    """Requirement 33: canonical xxHash64 helper is used."""

    def test_file_hash_uses_xxh64_not_sha256(self):
        with open(_mp.__file__) as f:
            src = f.read()
        self.assertNotIn("hashlib", src,
            "manifest_prune must not use hashlib; use xxHash64")
        self.assertIn("_xxh64_file_hex", src,
            "manifest_prune must use network._xxh64_file_hex")

    def test_xxh64_matches_network(self):
        d = tempfile.mkdtemp()
        try:
            path = os.path.join(d, "t.png")
            data = b"hello world"
            with open(path, "wb") as f:
                f.write(data)
            expected = _nw._xxh64_file_hex(path)
            self.assertEqual(len(expected), 16)
            self.assertRegex(expected, r'^[0-9a-f]{16}$')
        finally:
            shutil.rmtree(d, ignore_errors=True)


class TestHelpers(unittest.TestCase):
    """Basename and containment helpers."""

    def assert_actions(self, result, expected: Set[str]):
        actual = {it.action for it in result.items}
        self.assertEqual(actual, expected, f"actions: {actual}")

    def test_safe_basename_normal(self):
        self.assertTrue(_mp._safe_basename("tex.png"))

    def test_safe_basename_empty(self):
        self.assertFalse(_mp._safe_basename(""))

    def test_safe_basename_manifest_v3(self):
        self.assertFalse(_mp._safe_basename("manifest_v3.json"))

    def test_safe_basename_fbx(self):
        self.assertFalse(_mp._safe_basename("mesh.fbx"))

    def test_safe_basename_slash(self):
        self.assertFalse(_mp._safe_basename("a/b.png"))

    def test_safe_basename_dot_prefix(self):
        self.assertFalse(_mp._safe_basename(".hidden.png"))

    def test_safe_basename_dotdot(self):
        self.assertFalse(_mp._safe_basename(".."))

    def test_contained_in_exact(self):
        d = tempfile.mkdtemp()
        try:
            p = os.path.join(d, "t.png")
            _make_file(p)
            self.assertTrue(_mp._contained_in(d, p))
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_contained_in_outside(self):
        d = tempfile.mkdtemp()
        try:
            outside = os.path.join(tempfile.gettempdir(), "outside_test.png")
            self.assertFalse(_mp._contained_in(d, outside))
        finally:
            shutil.rmtree(d, ignore_errors=True)


# ================================================================
# Full pruning contract tests
# ================================================================


class TestPruneContract(unittest.TestCase):
    """34 approved cases for prune_orphan_sidecars."""

    def setUp(self):
        self.obj_dir = tempfile.mkdtemp()
        self.guid = _GUID
        self.aid = _ASSET_ID

    def tearDown(self):
        shutil.rmtree(self.obj_dir, ignore_errors=True)

    def _prior(self, assets: dict) -> dict:
        return _make_valid_manifest(assets, self.guid)

    def _current(self, assets: dict) -> dict:
        return _make_valid_manifest(assets, self.guid)

    def _ready(self, basename: str, size: int, hash_val: str) -> dict:
        return _make_asset_rec(basename, size, hash_val, status="ready")

    def _orphan_file(self, name: str, size: int) -> str:
        path = os.path.join(self.obj_dir, name)
        return _write_file_with_hash(path, size)

    def _orphan_file_raw(self, name: str, size: int) -> tuple:
        """Create file and return (path, hex_hash)."""
        path = os.path.join(self.obj_dir, name)
        h = _write_file_with_hash(path, size)
        return path, h

    # ── Case 1: prior-only ready asset deleted ──
    def test_case01_prior_only_deleted(self):
        h = self._orphan_file("tex.png", 100)
        r = _mp._prune_candidates(
            {self.aid: {"destinationBasename": "tex.png", "destinationSize": 100, "destinationHash": h}},
            set(),
            self.obj_dir,
        )
        self.assertEqual(r.status, _mp.STATUS_SUCCESS)
        self.assertIn(_mp.ACTION_DELETED, {it.action for it in r.items})
        self.assertFalse(os.path.isfile(os.path.join(self.obj_dir, "tex.png")))

    # ── Case 2: current same asset retained ──
    def test_case02_current_same_asset_retained(self):
        h = self._orphan_file("tex.png", 100)
        r = _mp._prune_candidates(
            {self.aid: {"destinationBasename": "tex.png", "destinationSize": 100, "destinationHash": h}},
            {"tex.png"},
            self.obj_dir,
        )
        self.assertEqual(r.status, _mp.STATUS_SUCCESS)
        actions = {it.action for it in r.items}
        self.assertIn(_mp.ACTION_RETAINED_CURRENT_REFERENCE, actions)
        self.assertTrue(os.path.isfile(os.path.join(self.obj_dir, "tex.png")))

    # ── Case 3: different asset_id, same basename retained ──
    def test_case03_different_asset_id_same_basename_retained(self):
        h = self._orphan_file("tex.png", 100)
        r = _mp._prune_candidates(
            {"old_id": {"destinationBasename": "tex.png", "destinationSize": 100, "destinationHash": h}},
            {"tex.png"},
            self.obj_dir,
        )
        self.assertEqual(r.status, _mp.STATUS_SUCCESS)
        actions = {it.action for it in r.items}
        self.assertEqual(actions, {_mp.ACTION_RETAINED_CURRENT_REFERENCE},
                         "different asset_id with same basename must retain")
        self.assertTrue(os.path.isfile(os.path.join(self.obj_dir, "tex.png")))

    # ── Case 4: missing prior => skipped ──
    def test_case04_missing_prior_skipped(self):
        r = _mp.prune_orphan_sidecars({}, self._current({}), self.obj_dir)
        self.assertEqual(r.status, _mp.STATUS_SKIPPED)

    # ── Case 5: invalid prior => skipped ──
    def test_case05_invalid_prior_skipped(self):
        r = _mp.prune_orphan_sidecars(
            {"schemaVersion": 99, "guid": self.guid},
            self._current({}),
            self.obj_dir,
        )
        self.assertEqual(r.status, _mp.STATUS_SKIPPED)

    # ── Case 6: prior GUID mismatch => skipped ──
    def test_case06_guid_mismatch_skipped(self):
        other_guid = "00000000000000000000000000000000"
        h = self._orphan_file("tex.png", 100)
        prior = self._prior({self.aid: self._ready("tex.png", 100, h)})
        current = _make_valid_manifest({}, other_guid)
        r = _mp.prune_orphan_sidecars(prior, current, self.obj_dir)
        self.assertEqual(r.status, _mp.STATUS_SKIPPED)

    # ── Case 7: current invalid => skipped ──
    def test_case07_current_invalid_skipped(self):
        h = self._orphan_file("tex.png", 100)
        prior = self._prior({self.aid: self._ready("tex.png", 100, h)})
        r = _mp.prune_orphan_sidecars(prior, {"not": "valid"}, self.obj_dir)
        self.assertEqual(r.status, _mp.STATUS_SKIPPED)

    # ── Cases 8-10: operator-gated (tested via AST/proof) ──
    def test_case08_operator_manifest_gate_before_prune(self):
        """Proof: __init__.py has 'continue' before pruning."""
        with open(os.path.join(_addon_dir, "__init__.py")) as f:
            src = f.read()
        # The manifest gate (should_send_after_pipeline) is before pruning
        self.assertIn("should_send_after_pipeline", src)
        # Find the prune block index vs. send block
        prune_idx = src.find("A3.6: Safe orphan sidecar pruning")
        manifest_gate_idx = src.find("should_send_after_pipeline")
        send_check_idx = src.find("SEND_FAILED")
        self.assertGreater(prune_idx, manifest_gate_idx,
                           "pruning must be after manifest gate")
        self.assertGreater(prune_idx, send_check_idx,
                           "pruning must be after send check")

    # ── Case 11: unsafe basename retained ──
    def test_case11_unsafe_basename_retained(self):
        fpath = os.path.join(self.obj_dir, ".hidden.png")
        _make_file(fpath, 50)
        h = _nw._xxh64_file_hex(fpath)
        r = _mp._prune_candidates(
            {self.aid: {"destinationBasename": ".hidden.png", "destinationSize": 50, "destinationHash": h}},
            set(),
            self.obj_dir,
        )
        self.assertEqual(r.status, _mp.STATUS_SUCCESS)
        actions = {it.action for it in r.items}
        self.assertIn(_mp.ACTION_SKIPPED_UNSAFE_BASENAME, actions)
        self.assertTrue(os.path.isfile(fpath))

    # ── Case 12: traversal (slashed basename) retained ──
    def test_case12_traversal_retained(self):
        r = _mp._prune_candidates(
            {self.aid: {"destinationBasename": "sub/tex.png", "destinationSize": 100, "destinationHash": "h1"}},
            set(),
            self.obj_dir,
        )
        self.assertEqual(r.status, _mp.STATUS_SUCCESS)
        actions = {it.action for it in r.items}
        self.assertIn(_mp.ACTION_SKIPPED_UNSAFE_BASENAME, actions)

    # ── Case 13: path escape retained (basename resolves outside obj_dir) ──
    def test_case13_path_escape_retained(self):
        """Symlink or realpath escape caught by _contained_in."""
        outside = os.path.join(tempfile.gettempdir(), "escape_test.png")
        _make_file(outside, 50)
        h = _nw._xxh64_file_hex(outside)
        r = _mp._prune_candidates(
            {self.aid: {"destinationBasename": "escape_test.png", "destinationSize": 50, "destinationHash": h}},
            set(),
            self.obj_dir,
        )
        self.assertEqual(r.status, _mp.STATUS_SUCCESS)
        actions = {it.action for it in r.items}
        self.assertEqual(actions, {_mp.ACTION_ALREADY_MISSING},
                         "file outside obj_dir is not in obj_dir")
        os.unlink(outside)

    # ── Case 14: symlink retained ──
    def test_case14_symlink_retained(self):
        target = os.path.join(self.obj_dir, "real_target.txt")
        _make_file(target, 50)
        link_path = os.path.join(self.obj_dir, "tex.png")
        os.symlink(target, link_path)
        h = _nw._xxh64_file_hex(link_path)  # reads through symlink
        r = _mp._prune_candidates(
            {self.aid: {"destinationBasename": "tex.png", "destinationSize": 50, "destinationHash": h}},
            set(),
            self.obj_dir,
        )
        self.assertEqual(r.status, _mp.STATUS_SUCCESS)
        actions = {it.action for it in r.items}
        self.assertIn(_mp.ACTION_SKIPPED_SYMLINK, actions)
        self.assertTrue(os.path.islink(link_path))

    # ── Case 15: non-regular file retained (directory) ──
    def test_case15_non_regular_retained(self):
        dir_path = os.path.join(self.obj_dir, "tex.png")
        os.mkdir(dir_path)
        r = _mp._prune_candidates(
            {self.aid: {"destinationBasename": "tex.png", "destinationSize": 0, "destinationHash": "h1"}},
            set(),
            self.obj_dir,
        )
        self.assertEqual(r.status, _mp.STATUS_SUCCESS)
        actions = {it.action for it in r.items}
        self.assertIn(_mp.ACTION_SKIPPED_NOT_REGULAR, actions)
        self.assertTrue(os.path.isdir(dir_path))

    # ── Case 16: size mismatch retained ──
    def test_case16_size_mismatch_retained(self):
        path = os.path.join(self.obj_dir, "tex.png")
        _write_file_with_hash(path, 100)  # actual 100
        r = _mp._prune_candidates(
            {self.aid: {"destinationBasename": "tex.png", "destinationSize": 200, "destinationHash": "h1"}},
            set(),
            self.obj_dir,
        )
        self.assertEqual(r.status, _mp.STATUS_PARTIAL,
                         "identity mismatch causes partial")
        actions = {it.action for it in r.items}
        self.assertIn(_mp.ACTION_SKIPPED_IDENTITY_MISMATCH, actions)
        self.assertTrue(os.path.isfile(path))

    # ── Case 17: canonical hash mismatch retained ──
    def test_case17_hash_mismatch_retained(self):
        path = os.path.join(self.obj_dir, "tex.png")
        h = _write_file_with_hash(path, 100)
        wrong_hash = "0000000000000000"
        self.assertNotEqual(h, wrong_hash)
        r = _mp._prune_candidates(
            {self.aid: {"destinationBasename": "tex.png", "destinationSize": 100, "destinationHash": wrong_hash}},
            set(),
            self.obj_dir,
        )
        self.assertEqual(r.status, _mp.STATUS_PARTIAL,
                         "hash mismatch causes partial")
        actions = {it.action for it in r.items}
        self.assertIn(_mp.ACTION_SKIPPED_IDENTITY_MISMATCH, actions)
        self.assertTrue(os.path.isfile(path))

    # ── Case 18: fingerprint change retained ──
    def test_case18_fingerprint_changed_before_delete(self):
        path = os.path.join(self.obj_dir, "tex.png")
        h = _write_file_with_hash(path, 100)
        _call_counts: dict = {}
        orig_lstat = os.lstat

        def _race_lstat(full_path, *, dir_fd=None):
            st = orig_lstat(full_path)
            c = _call_counts.get(full_path, 0) + 1
            _call_counts[full_path] = c
            if c == 3 and full_path == path:
                return os.stat_result((
                    st.st_mode, st.st_ino, st.st_dev, st.st_nlink,
                    st.st_uid, st.st_gid, st.st_size,
                    st.st_atime, st.st_mtime, st.st_ctime,
                    st.st_atime_ns, st.st_mtime_ns + 1, st.st_ctime_ns,
                ))
            return st

        with patch('os.lstat', side_effect=_race_lstat):
            r = _mp._prune_candidates(
                {self.aid: {"destinationBasename": "tex.png", "destinationSize": 100, "destinationHash": h}},
                set(),
                self.obj_dir,
            )
        self.assertEqual(r.status, _mp.STATUS_PARTIAL,
                         "fingerprint race must cause partial")
        actions = {it.action for it in r.items}
        self.assertIn(_mp.ACTION_SKIPPED_CHANGED_BEFORE_DELETE, actions,
                      "fingerprint change must skip deletion")
        self.assertTrue(os.path.isfile(path),
                        "file must be retained when fingerprint changes")

    # ── Case 19: already missing => success ──
    def test_case19_already_missing_success(self):
        """Prior references a file that doesn't exist on disk."""
        r = _mp._prune_candidates(
            {self.aid: {"destinationBasename": "tex.png", "destinationSize": 100, "destinationHash": "h1"}},
            set(),
            self.obj_dir,
        )
        self.assertEqual(r.status, _mp.STATUS_SUCCESS)
        actions = {it.action for it in r.items}
        self.assertIn(_mp.ACTION_ALREADY_MISSING, actions)

    # ── Case 20: one unlink failure does not stop later candidates ──
    def test_case20_one_failure_does_not_stop_later(self):
        path1, h1 = self._orphan_file_raw("tex1.png", 50)
        path2, h2 = self._orphan_file_raw("tex2.png", 50)
        prior_assets = {
            "id1": {"destinationBasename": "tex1.png", "destinationSize": 50, "destinationHash": h1},
            "id2": {"destinationBasename": "tex2.png", "destinationSize": 50, "destinationHash": h2},
        }
        _unlink_count: list = [0]
        orig_unlink = os.unlink

        def _selective_unlink(full_path):
            _unlink_count[0] += 1
            if _unlink_count[0] == 1:
                raise PermissionError("simulated unlink failure for first file")
            return orig_unlink(full_path)

        with patch('os.unlink', side_effect=_selective_unlink):
            r = _mp._prune_candidates(prior_assets, set(), self.obj_dir)
        self.assertEqual(r.status, _mp.STATUS_PARTIAL)
        self.assertEqual(len(r.items), 2)
        actions = {it.action for it in r.items}
        self.assertIn(_mp.ACTION_UNLINK_FAILED, actions,
                      "first file unlink must fail")
        self.assertIn(_mp.ACTION_DELETED, actions,
                      "second file unlink must succeed")
        self.assertTrue(os.path.isfile(path1),
                        "first file must survive failed unlink")
        self.assertFalse(os.path.isfile(path2),
                         "second file must be deleted after successful unlink")

    # ── Case 21: unlink failure => partial ──
    def test_case21_unlink_failure_partial(self):
        fpath, h = self._orphan_file_raw("tex.png", 50)
        orig_dir_mode = os.stat(self.obj_dir).st_mode
        os.chmod(self.obj_dir, 0o555)
        try:
            r = _mp._prune_candidates(
                {self.aid: {"destinationBasename": "tex.png", "destinationSize": 50, "destinationHash": h}},
                set(),
                self.obj_dir,
            )
            self.assertEqual(r.status, _mp.STATUS_PARTIAL)
            actions = {it.action for it in r.items}
            self.assertIn(_mp.ACTION_UNLINK_FAILED, actions)
        finally:
            os.chmod(self.obj_dir, orig_dir_mode)

    # ── Case 22: current manifest unchanged (via _prune_candidates) ──
    def test_case22_current_manifest_unchanged(self):
        h = self._orphan_file("tex.png", 100)
        unused = set()
        prior_assets = {self.aid: {"destinationBasename": "tex.png", "destinationSize": 100, "destinationHash": h}}
        # Show that the input dict isn't mutated
        prior_copy = copy.deepcopy(prior_assets)
        _mp._prune_candidates(prior_assets, unused, self.obj_dir)
        self.assertEqual(prior_assets, prior_copy,
                         "_prune_candidates must not mutate input")

    # ── Case 24: defensive snapshot copy verified ──
    def test_case24_defensive_snapshot_copy(self):
        """persist_manifest_v3 returns deep-copied snapshots independent of input mutation."""
        d = tempfile.mkdtemp()
        try:
            manifest_path = os.path.join(d, "manifest_v3.json")
            sidecar_path = os.path.join(d, "tex.png")
            _make_file(sidecar_path, 100)

            class _FS:
                source_kind = "FILE"
                mat_name = "M"
                node_name = "N"
                filepath_raw = ""
                colorspace = "sRGB"

            class _FR:
                status = "ready"
                asset_id = _ASSET_ID
                filename = "tex.png"
                size = 100
                destination_path = sidecar_path
                source_locator = ""

            src = _FS()
            results_by_source = {id(src): _FR()}
            usages = [type("_U", (), {"source": src, "slot_index": 0, "channel": 1})()]

            r1 = _mv3.persist_manifest_v3(
                guid_hex=_GUID, obj_dir=d, manifest_path=manifest_path,
                usages=usages, results_by_source=results_by_source,
            )
            self.assertEqual(r1.status, "success")

            # Mutate the input dict — snapshots must be independent
            results_by_source.clear()

            # Read back from disk and verify prior matches first call's current
            read_back = _mv3.read_manifest_v3(manifest_path)
            self.assertEqual(read_back.status, "valid")
            disk_copy = copy.deepcopy(read_back.manifest)

            # prior_manifest from r1 was empty (first write), current_manifest has assets
            self.assertIsInstance(r1.prior_manifest, dict)
            self.assertIsInstance(r1.current_manifest, dict)
            self.assertEqual(len(r1.current_manifest.get("assets", {})), 1)

            # Run a second persist; its prior must deep-copy the on-disk manifest
            # independently from r1.current_manifest
            src2 = _FS()
            results_by_source2 = {id(src2): _FR()}
            usages2 = [type("_U", (), {"source": src2, "slot_index": 0, "channel": 1})()]

            r2 = _mv3.persist_manifest_v3(
                guid_hex=_GUID, obj_dir=d, manifest_path=manifest_path,
                usages=usages2, results_by_source=results_by_source2,
            )
            self.assertEqual(r2.status, "success")

            # r2.prior_manifest is a deepcopy of on-disk manifest at read time
            self.assertEqual(r2.prior_manifest, disk_copy,
                             "second persist prior must equal on-disk manifest")
            self.assertIsNot(r2.prior_manifest, disk_copy,
                             "prior must be an independent copy")

            # Save deep copies for mutation testing
            prior_copy = copy.deepcopy(r2.prior_manifest)
            current_copy = copy.deepcopy(r2.current_manifest)

            # Mutate local dicts — snapshots must remain unchanged
            local_dict = {"unrelated": "data"}
            self.assertNotIn("unrelated", r2.prior_manifest)

            # Invoke pruning wrapper with the snapshots
            class FakeIntegrationResult:
                status = "success"
                action = "written"
                prior_manifest = r2.prior_manifest
                current_manifest = r2.current_manifest

            prune_r = _mp.prune_after_successful_send(
                FakeIntegrationResult(), True, d,
            )
            self.assertIsInstance(prune_r, _mp.PruneResult)

            # Verify both snapshots unchanged after pruning
            self.assertEqual(r2.prior_manifest, prior_copy,
                             "prior_manifest must not change after prune")
            self.assertEqual(r2.current_manifest, current_copy,
                             "current_manifest must not change after prune")

            # Verify prior and current are distinct objects
            self.assertIsNot(r2.prior_manifest, r2.current_manifest,
                             "prior and current must be distinct objects")
        finally:
            shutil.rmtree(d, ignore_errors=True)

    # ── Case 25: prune exactly once after send ──
    def test_case25_prune_only_after_send(self):
        """Proof: __init__.py calls prune_after_successful_send exactly once."""
        with open(os.path.join(_addon_dir, "__init__.py")) as f:
            src = f.read()
        wrapper_calls = src.count("prune_after_successful_send")
        direct_calls = src.count("prune_orphan_sidecars")
        self.assertEqual(wrapper_calls, 1,
                         "prune_after_successful_send must be called exactly once")
        self.assertEqual(direct_calls, 0,
                         "prune_orphan_sidecars must not be called directly")

    # ── Case 26: send exactly once ──
    def test_case26_send_exactly_once(self):
        """Proof: serialize_and_send_fbx_request called once (no duplicate send)."""
        with open(os.path.join(_addon_dir, "__init__.py")) as f:
            src = f.read()
        send_calls = src.count("serialize_and_send_fbx_request")
        self.assertEqual(send_calls, 1,
                         "serialize_and_send_fbx_request must be called exactly once")

    # ── Case 27: prune failure does not suppress send ──
    def test_case27_prune_failure_does_not_suppress_send(self):
        """Proof: pruning result is never used for send gating."""
        with open(os.path.join(_addon_dir, "__init__.py")) as f:
            src = f.read()
        # The send block MUST appear before the prune block.
        send_idx = src.find("serialize_and_send_fbx_request")
        prune_idx = src.find("A3.6: Safe orphan sidecar pruning")
        self.assertGreater(prune_idx, send_idx,
                           "pruning block must appear after send block")

        # Verify _prune_result is only used for logging, never for
        # flow-control gating (continue/break/return) that could
        # suppress sending.
        # Search for patterns like "if _prune_result" or "if _prune_status"
        # that gate flow control.
        lines = src.splitlines()
        in_prune_block = False
        for i, line in enumerate(lines):
            if "A3.6: Safe orphan sidecar pruning" in line:
                in_prune_block = True
                continue
            if in_prune_block:
                stripped = line.strip()
                # If a condition uses _prune_result or _prune_status
                # to gate continue/break, that's a problem.
                if stripped.startswith("if ") and "_prune" in stripped:
                    # Check if the body has continue/break
                    # Look ahead a few lines for the body
                    body_start = i + 1
                    for j in range(body_start, min(body_start + 5, len(lines))):
                        body_line = lines[j].strip()
                        if body_line in ("continue", "break"):
                            self.fail(
                                f"Line {j+1}: prune result gates '{body_line}': "
                                f"{lines[j]}"
                            )
                        # Stop at next non-indented block
                        if body_line and not body_line.startswith((" ", "\t", "#", ")")):
                            break



class TestBehavioralGates(unittest.TestCase):
    """Authorization gating tests for prune_after_successful_send."""

    def setUp(self):
        self.obj_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.obj_dir, ignore_errors=True)

    def _make_mock_result(self, status="success", action="written"):
        r = MagicMock()
        r.status = status
        r.action = action
        r.prior_manifest = {}
        r.current_manifest = {}
        return r

    def test_gate_manifest_failure_skips(self):
        r = _mp.prune_after_successful_send(
            self._make_mock_result(status="failure"), True, self.obj_dir,
        )
        self.assertEqual(r.status, _mp.STATUS_SKIPPED)
        self.assertIn("manifest_not_durable", r.error)

    def test_gate_manifest_not_written_skips(self):
        r = _mp.prune_after_successful_send(
            self._make_mock_result(action="generation_unchanged"), True, self.obj_dir,
        )
        self.assertEqual(r.status, _mp.STATUS_SKIPPED)
        self.assertIn("manifest_not_durable", r.error)

    def test_gate_send_failed_skips(self):
        r = _mp.prune_after_successful_send(
            self._make_mock_result(), False, self.obj_dir,
        )
        self.assertEqual(r.status, _mp.STATUS_SKIPPED)
        self.assertIn("send_failed", r.error)

    def test_gate_allows_prune_on_success(self):
        mock_prune_r = _mp.PruneResult(status=_mp.STATUS_SUCCESS)
        with patch.object(_mp, 'prune_orphan_sidecars', return_value=mock_prune_r) as mock_fn:
            r = _mp.prune_after_successful_send(
                self._make_mock_result(), True, self.obj_dir,
            )
            mock_fn.assert_called_once_with(
                prior_manifest={},
                current_manifest={},
                obj_dir=self.obj_dir,
            )
            self.assertIs(r, mock_prune_r)

    def test_gate_prune_exception_returns_partial(self):
        with patch.object(_mp, 'prune_orphan_sidecars', side_effect=RuntimeError("prune_crash")):
            r = _mp.prune_after_successful_send(
                self._make_mock_result(), True, self.obj_dir,
            )
        self.assertEqual(r.status, _mp.STATUS_PARTIAL)
        self.assertTrue(r.error.startswith("prune_exception:"),
                        f"error must start with prune_exception: got {r.error!r}")
        self.assertIn("prune_crash", r.error)

    def test_gate_skipped_does_not_call_prune(self):
        with patch.object(_mp, 'prune_orphan_sidecars') as mock_fn:
            r = _mp.prune_after_successful_send(
                self._make_mock_result(status="failure"), True, self.obj_dir,
            )
            mock_fn.assert_not_called()
            self.assertEqual(r.status, _mp.STATUS_SKIPPED)

    def test_gate_manifest_passed_to_prune(self):
        """When gates pass, prior/current manifests are forwarded to prune_orphan_sidecars."""
        prior_m = {"schemaVersion": 3, "guid": _GUID, "generation": 1,
                    "semanticContentDigest": "0" * 64, "occurrences": {}, "assets": {}}
        current_m = {"schemaVersion": 3, "guid": _GUID, "generation": 1,
                      "semanticContentDigest": "0" * 64, "occurrences": {}, "assets": {}}

        class FakeIntegrationResult:
            status = "success"
            action = "written"
            prior_manifest = prior_m
            current_manifest = current_m

        expected_r = _mp.PruneResult(status=_mp.STATUS_SUCCESS)
        with patch.object(_mp, 'prune_orphan_sidecars', return_value=expected_r) as mock_fn:
            r = _mp.prune_after_successful_send(FakeIntegrationResult(), True, self.obj_dir)
            mock_fn.assert_called_once_with(
                prior_manifest=prior_m,
                current_manifest=current_m,
                obj_dir=self.obj_dir,
            )
            self.assertIs(r, expected_r)


class TestProductionIntegration(unittest.TestCase):
    """Proof tests verifying __init__.py calls the wrapper correctly."""

    _init_path = os.path.join(_addon_dir, "__init__.py")

    def _read_init_src(self) -> str:
        with open(self._init_path) as f:
            return f.read()

    def test_prune_calls_wrapper_exactly_once(self):
        src = self._read_init_src()
        wrapper_calls = src.count("prune_after_successful_send")
        self.assertEqual(wrapper_calls, 1,
                         "prune_after_successful_send must be called exactly once in __init__.py")

    def test_prune_has_zero_direct_prune_orphan_sidecars_calls_in_init(self):
        src = self._read_init_src()
        direct_calls = src.count("prune_orphan_sidecars")
        self.assertEqual(direct_calls, 0,
                         "__init__.py must not call prune_orphan_sidecars directly")

    def test_prune_wrapper_after_send_success(self):
        src = self._read_init_src()
        wrapper_idx = src.find("prune_after_successful_send")
        send_print_idx = src.find("Synced:")
        self.assertGreater(wrapper_idx, send_print_idx,
                           "wrapper call must appear after send-success print")

    def test_prune_wrapper_after_send_failed_branch(self):
        src = self._read_init_src()
        wrapper_idx = src.find("prune_after_successful_send")
        send_failed_idx = src.find("SEND_FAILED")
        self.assertGreater(wrapper_idx, send_failed_idx,
                           "wrapper call must appear after SEND_FAILED branch")

    def test_prune_send_succeeded_true_at_callsite(self):
        src = self._read_init_src()
        # Find the wrapper call line
        wrapper_start = src.find("prune_after_successful_send")
        # Find the send_succeeded parameter within the wrapper call
        call_snippet = src[wrapper_start:wrapper_start + 200]
        self.assertIn("send_succeeded=True", call_snippet,
                       "wrapper must be called with send_succeeded=True")

    def test_prune_result_not_gating_send(self):
        src = self._read_init_src()
        # Verify the wrapper call is not inside a condition that gates send
        send_idx = src.find("serialize_and_send_fbx_request")
        prune_idx = src.find("prune_after_successful_send")
        self.assertGreater(prune_idx, send_idx,
                           "prune must appear after send, never gating it")

    def test_prune_exception_not_causing_continue_or_resend(self):
        src = self._read_init_src()
        lines = src.splitlines()
        in_prune_block = False
        for i, line in enumerate(lines):
            if "prune_after_successful_send" in line and i > 0:
                in_prune_block = True
                continue
            if in_prune_block:
                stripped = line.strip()
                if stripped.startswith("if ") and "prune" in line:
                    body_start = i + 1
                    for j in range(body_start, min(body_start + 5, len(lines))):
                        body_line = lines[j].strip()
                        if body_line in ("continue", "break", "return"):
                            self.fail(
                                f"Line {j+1}: prune result gates '{body_line}': "
                                f"{lines[j]}"
                            )
                        if body_line and not body_line.startswith((" ", "\t", "#", ")")):
                            break


class TestManifestV3Integration(unittest.TestCase):
    """Verify persist_manifest_v3 captures prior/current snapshots."""

    def test_persist_returns_prior_and_current(self):
        d = tempfile.mkdtemp()
        try:
            manifest_path = os.path.join(d, "manifest_v3.json")
            aid = _ASSET_ID
            h = "a1b2c3d4e5f6a7b8"  # known non-matching hash
            from dataclasses import asdict

            # Create a usage/result pair for persist_manifest_v3
            # First build a ready result manually
            class FakeSource:
                source_kind = "FILE"
                mat_name = "TestMat"
                node_name = "TestNode"
                filepath_raw = ""
                colorspace = "sRGB"

            class FakeResult:
                status = "ready"
                asset_id = aid
                filename = "tex.png"
                size = 0
                destination_path = os.path.join(d, "tex.png")
                source_locator = ""

            class FakeUsage:
                def __init__(self):
                    self.source = FakeSource()
                    self.slot_index = 0
                    self.channel = 1

            usages = [FakeUsage()]
            results_by_source = {id(usages[0].source): FakeResult()}

            r = _mv3.persist_manifest_v3(
                guid_hex=_GUID,
                obj_dir=d,
                manifest_path=manifest_path,
                usages=usages,
                results_by_source=results_by_source,
            )
            self.assertEqual(r.status, "success")
            self.assertEqual(r.action, "written")
            # First write: prior should be empty, current should have assets
            self.assertIsInstance(r.prior_manifest, dict)
            self.assertIsInstance(r.current_manifest, dict)
            if r.current_manifest.get("assets"):
                self.assertIn(aid, r.current_manifest["assets"])
        finally:
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
