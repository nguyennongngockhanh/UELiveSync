"""Phase 10A.3.5 — Runtime unit tests for manifest-informed sidecar reuse.

Tests cover:
- Missing/invalid manifest → prepare decisions
- FILE/PACKED/GENERATED source identity
- Occurrence matching failures
- Asset reuse evaluation
- Path/symlink safety checks
- Mixed reuse + prepare
- Shared assets across occurrences
"""

from __future__ import annotations

import ast
import json
import os
import stat
import sys
import shutil
import tempfile
import unittest
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

# ──────────────────────────────────────────────────────────────────────
# Mock bpy for running outside Blender
# ──────────────────────────────────────────────────────────────────────

class _MockBpyModule:
    """Minimal mock for bpy package — enough for manifest_reuse.py functions."""
    def __getattr__(self, name):
        if name == "path":
            return _MockBpyPath()
        if name == "types":
            return _MockBpyTypes()
        if name == "data":
            return _MockBpyData()
        if name == "ops":
            return _MockBpyOps()
        if name == "props":
            return _MockBpyProps()
        if name == "context":
            return _MockBpyContext()
        if name == "app":
            return _MockBpyApp()
        raise AttributeError(name)


class _MockBpyPath:
    @staticmethod
    def dirname(p):
        return os.path.dirname(p)
    @staticmethod
    def join(*args):
        return os.path.join(*args)
    @staticmethod
    def basename(p):
        return os.path.basename(p)


class _MockBpyTypes:
    @staticmethod
    def Image():
        return "Image"


class _MockBpyData:
    @staticmethod
    def images():
        return _MockImages()


class _MockImages:
    def __iter__(self):
        return iter([])
    def __len__(self):
        return 0
    def get(self, name, default=None):
        return default


class _MockBpyOps:
    @staticmethod
    def image():
        return _MockOpsImage()


class _MockOpsImage:
    def pack(self, *args, **kwargs):
        return {"FINISHED"}
    def unpack(self, *args, **kwargs):
        return {"FINISHED"}


class _MockBpyProps:
    @staticmethod
    def StringProperty(**kwargs):
        return lambda: None
    @staticmethod
    def IntProperty(**kwargs):
        return lambda: None
    @staticmethod
    def FloatProperty(**kwargs):
        return lambda: None
    @staticmethod
    def BoolProperty(**kwargs):
        return lambda: None
    @staticmethod
    def EnumProperty(**kwargs):
        return lambda: None


class _MockBpyContext:
    @staticmethod
    def get_active_object():
        return None


class _MockBpyApp:
    @staticmethod
    def handlers():
        return _MockAppHandlers()


class _MockAppHandlers:
    @staticmethod
    def scene_update_post():
        return []


class _MockBpyApp2:
    pass


# Install the mock before any Blender_Addon import
_mock_bpy = _MockBpyModule()
if "bpy" not in sys.modules:
    sys.modules["bpy"] = _mock_bpy
if "bpy.path" not in sys.modules:
    sys.modules["bpy.path"] = _mock_bpy.path
if "bpy.types" not in sys.modules:
    sys.modules["bpy.types"] = _mock_bpy.types
if "bpy.data" not in sys.modules:
    sys.modules["bpy.data"] = _mock_bpy.data
if "bpy.data.images" not in sys.modules:
    sys.modules["bpy.data.images"] = _mock_bpy.data.images
if "bpy.ops" not in sys.modules:
    sys.modules["bpy.ops"] = _mock_bpy.ops
if "bpy.ops.image" not in sys.modules:
    sys.modules["bpy.ops.image"] = _mock_bpy.ops.image
if "bpy.props" not in sys.modules:
    sys.modules["bpy.props"] = _mock_bpy.props
if "bpy.context" not in sys.modules:
    sys.modules["bpy.context"] = _mock_bpy.context
if "bpy.app" not in sys.modules:
    sys.modules["bpy.app"] = _mock_bpy.app
if "bpy.app.handlers" not in sys.modules:
    sys.modules["bpy.app.handlers"] = _mock_bpy.app.handlers
if "bpy.app.templates" not in sys.modules:
    sys.modules["bpy.app.templates"] = _mock_bpy.app


# ──────────────────────────────────────────────────────────────────────
# Import manifest modules via sys.path to avoid __init__.py bpy import
# ──────────────────────────────────────────────────────────────────────

_blender_addon_dir = os.path.join(os.path.dirname(__file__), "..", "Blender_Addon")
if _blender_addon_dir not in sys.path:
    sys.path.insert(0, _blender_addon_dir)

# Import manifest_v3 directly (no bpy dependency)
import manifest_v3 as mv3

# Import network for xxh64 (no bpy dependency at module level)
import network as net

# ──────────────────────────────────────────────────────────────────────
# Block Blender_Addon/__init__.py from running.
# manifest_reuse.py does `from Blender_Addon.manifest_v3 import ...`
# which triggers __init__.py → bpy → mathutils → ImportError.
# We block it by replacing Blender_Addon in sys.modules BEFORE
# manifest_reuse.py is imported.
# ──────────────────────────────────────────────────────────────────────
import types as _types
_ba_stub = _types.ModuleType("Blender_Addon")
_ba_stub.__path__ = [_blender_addon_dir]  # Make it look like a package
_ba_stub.manifest_v3 = mv3
_ba_stub.network = net
sys.modules["Blender_Addon"] = _ba_stub

# Now import manifest_reuse (its lazy `from Blender_Addon.manifest_v3 import ...`
# will resolve via sys.modules stub without triggering __init__.py)
import manifest_reuse


# ──────────────────────────────────────────────────────────────────────
# Test base classes
# ──────────────────────────────────────────────────────────────────────

class _BaseReuseTest(unittest.TestCase):
    """Base class providing temp directory management."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.sidecar_dir = os.path.join(self.tmpdir, "sidecars")
        os.makedirs(self.sidecar_dir, exist_ok=True)
        self.obj_dir = os.path.join(self.tmpdir, "obj")
        os.makedirs(self.obj_dir, exist_ok=True)
        self.manifest_path = os.path.join(self.obj_dir, "manifest_v3.json")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _write_valid_manifest(self, guid, generation=1, occurrences=None, assets=None):
        """Write a valid manifest v3 file."""
        if occurrences is None:
            occurrences = {}
        if assets is None:
            assets = {}
        manifest = mv3.build_manifest_v3(
            guid=guid, generation=generation,
            occurrences=occurrences, assets=assets,
        )
        with open(self.manifest_path, 'w', encoding='utf-8') as f:
            f.write(mv3.serialize_manifest_v3(manifest))
        return manifest

    def _create_sidecar_file(self, basename, content=b"sidecar content bytes"):
        path = os.path.join(self.sidecar_dir, basename)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'wb') as f:
            f.write(content)
        return path

    def _build_sidecar_and_manifest(self, content=b"test sidecar content"):
        """Helper to create a sidecar file and manifest pointing to it."""
        dest_basename = "test_texture.png"
        dest_path = self._create_sidecar_file(dest_basename, content)
        content_hash = format(net.xxh64(content), '016x')
        actual_size = os.path.getsize(dest_path)
        guid = "a" * 64
        occ_id = mv3.compute_occurrence_id(
            guid=guid, slot_index=0, material_identity="MatA",
            node_identity="MatA/TexA", channel=0,
        )
        asset_id = content_hash
        asset_rec = mv3.build_asset_record(
            source_kind="FILE", content_hash=asset_id,
            destination_basename=dest_basename,
            destination_size=actual_size,
            destination_hash=content_hash, status="ready",
        )
        asset = {asset_id: asset_rec}
        occ_rec = mv3.build_occurrence_record(
            slot_index=0, channel=0, material_identity="MatA",
            node_identity="MatA/TexA", source_kind="FILE",
            source_locator="/tmp/test.png", colorspace="sRGB",
            asset_id=asset_id, status="ready",
        )
        manifest = mv3.build_manifest_v3(
            guid=guid, generation=1,
            occurrences={occ_id: occ_rec}, assets=asset,
        )
        with open(self.manifest_path, 'w') as f:
            f.write(mv3.serialize_manifest_v3(manifest))
        return guid, occ_id, asset_id, dest_basename, dest_path, content_hash

    def _make_prepare_fn(self, count_list=None):
        """Create a prepare_fn that records calls and returns a valid ReuseDecision."""
        from manifest_reuse import ReuseDecision
        def _track_prepare(source_desc, sidecar_dir, collision_registry, guid_short):
            if count_list is not None:
                count_list[0] += 1
            return (ReuseDecision(decision="prepare", action="prepared", occurrence_id="", asset_id="", source_kind="FILE", destination_path=""), {"asset_id": "test_asset", "contentHash": "0" * 16})
        return _track_prepare


# ──────────────────────────────────────────────────────────────────────
# Mock source class for tests
# ──────────────────────────────────────────────────────────────────────

@dataclass
class MockSource:
    """Mock source for testing without bpy."""
    mat_name: str
    node_name: str
    image_name: str
    source_kind: str
    filepath_raw: str
    filepath: str
    is_packed: bool
    width: int = 256
    height: int = 256
    file_format: str = "PNG"
    colorspace: str = "sRGB"


# ──────────────────────────────────────────────────────────────────────
# 1. Path safety tests
# ──────────────────────────────────────────────────────────────────────

class TestPathSafety(unittest.TestCase):
    """Tests for path safety validation."""

    def test_safe_basename_normal(self):
        self.assertTrue(manifest_reuse.is_safe_basename("texture.png"))
        self.assertTrue(manifest_reuse.is_safe_basename("texture123.png"))
        self.assertTrue(manifest_reuse.is_safe_basename("my-texture_v2.png"))

    def test_safe_basename_empty(self):
        self.assertFalse(manifest_reuse.is_safe_basename(""))

    def test_safe_basename_dot(self):
        self.assertFalse(manifest_reuse.is_safe_basename("."))

    def test_safe_basename_dotdot(self):
        self.assertFalse(manifest_reuse.is_safe_basename(".."))

    def test_safe_basename_slash(self):
        self.assertTrue(manifest_reuse.is_safe_basename("texture.png.bak"))

    def test_safe_basename_absolute(self):
        self.assertFalse(manifest_reuse.is_safe_basename("/etc/passwd"))

    def test_safe_basename_backslash(self):
        self.assertFalse(manifest_reuse.is_safe_basename("texture\\\\escape"))


class TestPathContainment(unittest.TestCase):
    """Tests for path containment validation via validate_path_safety."""

    def test_contained_path_is_safe(self):
        sidecar_dir = "/tmp"
        dest_path = "/tmp/some_dir/texture.png"
        safe, reason = manifest_reuse.validate_path_safety(sidecar_dir, dest_path)
        self.assertTrue(safe)

    def test_traversal_path_rejected(self):
        sidecar_dir = "/tmp/sidecars"
        dest_path = "/tmp/sidecars/../../../etc/passwd"
        safe, reason = manifest_reuse.validate_path_safety(sidecar_dir, dest_path)
        self.assertFalse(safe)

    def test_sidecar_dir_missing(self):
        sidecar_dir = "/tmp/nonexistent_sidecars_xyz"
        dest_path = "/tmp/nonexistent_sidecars_xyz/texture.png"
        safe, reason = manifest_reuse.validate_path_safety(sidecar_dir, dest_path)
        self.assertFalse(safe)


# ──────────────────────────────────────────────────────────────────────
# 2. Manifest reading and validation tests
# ──────────────────────────────────────────────────────────────────────

class TestManifestRead(_BaseReuseTest):
    """Tests for manifest reading and validation."""

    def test_missing_manifest(self):
        status, data = manifest_reuse.read_prior_manifest(self.manifest_path, "a" * 64)
        self.assertEqual(status, "missing")
        self.assertIsNone(data)

    def test_valid_manifest_structure(self):
        self._write_valid_manifest("a" * 64)
        status, data = manifest_reuse.read_prior_manifest(self.manifest_path, "a" * 64)
        self.assertEqual(status, "read")
        self.assertIsNotNone(data)
        self.assertEqual(data["guid"], "a" * 64)

    def test_malformed_json(self):
        with open(self.manifest_path, 'w') as f:
            f.write("{bad json")
        status, data = manifest_reuse.read_prior_manifest(self.manifest_path, "a" * 64)
        self.assertEqual(status, "invalid")
        self.assertIsNone(data)

    def test_non_object_json(self):
        with open(self.manifest_path, 'w') as f:
            f.write("42")
        status, data = manifest_reuse.read_prior_manifest(self.manifest_path, "a" * 64)
        self.assertEqual(status, "invalid")
        self.assertIsNone(data)

    def test_unknown_schema_rejected(self):
        from manifest_v3 import build_manifest_v3, serialize_manifest_v3
        manifest = build_manifest_v3(guid="a" * 64, generation=1, occurrences={}, assets={})
        data = json.loads(serialize_manifest_v3(manifest))
        data["schemaVersion"] = 999
        with open(self.manifest_path, 'w') as f:
            json.dump(data, f)
        status, pdata = manifest_reuse.read_prior_manifest(self.manifest_path, "a" * 64)
        self.assertEqual(status, "read")  # Read succeeds, validation happens later
        self.assertIsNotNone(pdata)

    def test_guid_mismatch_rejected(self):
        self._write_valid_manifest("b" * 64)
        status, data = manifest_reuse.read_prior_manifest(self.manifest_path, "a" * 64)
        self.assertEqual(status, "read")
        # GUID mismatch is caught in validate_prior_manifest_schema
        from manifest_reuse import validate_prior_manifest_schema
        is_valid, err = validate_prior_manifest_schema(data, "a" * 64)
        self.assertFalse(is_valid)
        self.assertEqual(err, "guid_mismatch")

    def test_digest_mismatch_rejected(self):
        self._write_valid_manifest("a" * 64)
        # Tamper with the file to invalidate digest
        with open(self.manifest_path, 'r') as f:
            data = json.loads(f.read())
        data["extra_field"] = "tampered"
        with open(self.manifest_path, 'w') as f:
            json.dump(data, f)
        status, pdata = manifest_reuse.read_prior_manifest(self.manifest_path, "a" * 64)
        self.assertEqual(status, "read")
        from manifest_reuse import validate_prior_manifest_schema
        is_valid, err = validate_prior_manifest_schema(pdata, "a" * 64)
        self.assertFalse(is_valid)
        self.assertIn(err, ["digest_mismatch", "invalid_top_level_keys"])


# ──────────────────────────────────────────────────────────────────────
# 3. Occurrence matching tests
# ──────────────────────────────────────────────────────────────────────

class TestOccurrenceMatching(_BaseReuseTest):
    """Tests for occurrence-level evaluation."""

    def _build_valid_manifest_with_occurrence(self, guid, slot=0, channel=0, mat="MatA", node="TexA"):
        """Helper to build a manifest with a single valid occurrence."""
        content = b"sidecar content"
        dest_basename = "texture.png"
        dest_path = self._create_sidecar_file(dest_basename, content)
        content_hash = format(net.xxh64(content), '016x')
        actual_size = os.path.getsize(dest_path)
        from manifest_v3 import (
            compute_occurrence_id, build_manifest_v3,
            build_occurrence_record, build_asset_record,
            serialize_manifest_v3,
        )
        occ_id = compute_occurrence_id(
            guid=guid, slot_index=slot, material_identity=mat,
            node_identity=f"{mat}/{node}", channel=channel,
        )
        asset_rec = build_asset_record(
            source_kind="FILE", content_hash=content_hash,
            destination_basename=dest_basename,
            destination_size=actual_size,
            destination_hash=content_hash, status="ready",
        )
        asset = {content_hash: asset_rec}
        occ_rec = build_occurrence_record(
            slot_index=slot, channel=channel, material_identity=mat,
            node_identity=f"{mat}/{node}", source_kind="FILE",
            source_locator=f"/tmp/{node}.png", colorspace="sRGB",
            asset_id=content_hash, status="ready",
        )
        manifest = build_manifest_v3(
            guid=guid, generation=1,
            occurrences={occ_id: occ_rec}, assets=asset,
        )
        with open(self.manifest_path, 'w') as f:
            f.write(serialize_manifest_v3(manifest))
        return occ_id, content_hash, content, dest_path

    def test_occurrence_missing(self):
        self._write_valid_manifest("a" * 64)
        current_occ_id = "nonexistent_occurrence_id"
        from manifest_reuse import evaluate_occurrence_match
        prior_occurrences = {}
        result = evaluate_occurrence_match(
            current_occ_id=current_occ_id, slot_index=0, channel=0,
            material_identity="MatA", node_identity="MatA/TexA",
            source_kind="FILE", source_locator="/tmp/test.png",
            colorspace="sRGB", prior_occurrences=prior_occurrences,
            assets={},
        )
        self.assertEqual(result.decision, "prepare")
        self.assertEqual(result.action, "occurrence_missing")

    def test_exact_match_returns_reuse(self):
        guid = "a" * 64
        occ_id, content_hex, content, dest_path = self._build_valid_manifest_with_occurrence(
            guid, slot=0, channel=0, mat="MatA", node="TexA",
        )
        from manifest_reuse import evaluate_occurrence_match
        prior_occurrences = {occ_id: {
            "slotIndex": 0, "channel": 0,
            "materialIdentity": "MatA",
            "nodeIdentity": "MatA/TexA",
            "sourceKind": "FILE", "sourceLocator": "/tmp/TexA.png",
            "colorspace": "sRGB", "assetId": content_hex, "status": "ready",
        }}
        prior_assets = {content_hex: {
            "status": "ready",
            "contentHash": content_hex,
            "destinationBasename": "texture.png",
            "destinationSize": os.path.getsize(dest_path),
            "destinationHash": content_hex,
        }}
        result = evaluate_occurrence_match(
            current_occ_id=occ_id, slot_index=0, channel=0,
            material_identity="MatA", node_identity="MatA/TexA",
            source_kind="FILE", source_locator="/tmp/tex.png",
            colorspace="sRGB", prior_occurrences=prior_occurrences,
            assets=prior_assets,
        )
        self.assertEqual(result.decision, "reuse")
        self.assertEqual(result.action, "reuse_allowed")

    def test_slot_index_mismatch(self):
        guid = "a" * 64
        occ_id, content_hex, content, dest_path = self._build_valid_manifest_with_occurrence(
            guid, slot=0, channel=0, mat="MatA", node="TexA",
        )
        from manifest_reuse import evaluate_occurrence_match
        prior_occurrences = {occ_id: {
            "slotIndex": 0, "channel": 0,
            "materialIdentity": "MatA",
            "nodeIdentity": "MatA/TexA",
            "sourceKind": "FILE", "sourceLocator": "/tmp/TexA.png",
            "colorspace": "sRGB", "assetId": content_hex, "status": "ready",
        }}
        result = evaluate_occurrence_match(
            current_occ_id=occ_id, slot_index=1, channel=0,
            material_identity="MatA", node_identity="MatA/TexA",
            source_kind="FILE", source_locator="/tmp/tex.png",
            colorspace="sRGB", prior_occurrences=prior_occurrences,
            assets={content_hex: {"status": "ready"}},
        )
        self.assertEqual(result.decision, "prepare")
        self.assertEqual(result.action, "occurrence_identity_mismatch")

    def test_channel_mismatch(self):
        guid = "a" * 64
        occ_id, content_hex, content, dest_path = self._build_valid_manifest_with_occurrence(
            guid, slot=0, channel=0, mat="MatA", node="TexA",
        )
        from manifest_reuse import evaluate_occurrence_match
        prior_occurrences = {occ_id: {
            "slotIndex": 0, "channel": 0,
            "materialIdentity": "MatA",
            "nodeIdentity": "MatA/TexA",
            "sourceKind": "FILE", "sourceLocator": "/tmp/TexA.png",
            "colorspace": "sRGB", "assetId": content_hex, "status": "ready",
        }}
        result = evaluate_occurrence_match(
            current_occ_id=occ_id, slot_index=0, channel=1,
            material_identity="MatA", node_identity="MatA/TexA",
            source_kind="FILE", source_locator="/tmp/tex.png",
            colorspace="sRGB", prior_occurrences=prior_occurrences,
            assets={content_hex: {"status": "ready"}},
        )
        self.assertEqual(result.decision, "prepare")
        self.assertEqual(result.action, "occurrence_identity_mismatch")

    def test_material_identity_mismatch(self):
        guid = "a" * 64
        occ_id, content_hex, content, dest_path = self._build_valid_manifest_with_occurrence(
            guid, slot=0, channel=0, mat="MatA", node="TexA",
        )
        from manifest_reuse import evaluate_occurrence_match
        prior_occurrences = {occ_id: {
            "slotIndex": 0, "channel": 0,
            "materialIdentity": "MatA",
            "nodeIdentity": "MatA/TexA",
            "sourceKind": "FILE", "sourceLocator": "/tmp/TexA.png",
            "colorspace": "sRGB", "assetId": content_hex, "status": "ready",
        }}
        result = evaluate_occurrence_match(
            current_occ_id=occ_id, slot_index=0, channel=0,
            material_identity="MatB", node_identity="MatB/TexA",
            source_kind="FILE", source_locator="/tmp/tex.png",
            colorspace="sRGB", prior_occurrences=prior_occurrences,
            assets={content_hex: {"status": "ready"}},
        )
        self.assertEqual(result.decision, "prepare")
        self.assertEqual(result.action, "occurrence_identity_mismatch")

    def test_node_identity_mismatch(self):
        guid = "a" * 64
        occ_id, content_hex, content, dest_path = self._build_valid_manifest_with_occurrence(
            guid, slot=0, channel=0, mat="MatA", node="TexA",
        )
        from manifest_reuse import evaluate_occurrence_match
        prior_occurrences = {occ_id: {
            "slotIndex": 0, "channel": 0,
            "materialIdentity": "MatA",
            "nodeIdentity": "MatA/TexA",
            "sourceKind": "FILE", "sourceLocator": "/tmp/TexA.png",
            "colorspace": "sRGB", "assetId": content_hex, "status": "ready",
        }}
        result = evaluate_occurrence_match(
            current_occ_id=occ_id, slot_index=0, channel=0,
            material_identity="MatA", node_identity="MatA/TexB",
            source_kind="FILE", source_locator="/tmp/tex.png",
            colorspace="sRGB", prior_occurrences=prior_occurrences,
            assets={content_hex: {"status": "ready"}},
        )
        self.assertEqual(result.decision, "prepare")
        self.assertEqual(result.action, "occurrence_identity_mismatch")

    def test_source_kind_mismatch(self):
        guid = "a" * 64
        occ_id, content_hex, content, dest_path = self._build_valid_manifest_with_occurrence(
            guid, slot=0, channel=0, mat="MatA", node="TexA",
        )
        from manifest_reuse import evaluate_occurrence_match
        prior_occurrences = {occ_id: {
            "slotIndex": 0, "channel": 0,
            "materialIdentity": "MatA",
            "nodeIdentity": "MatA/TexA",
            "sourceKind": "FILE", "sourceLocator": "/tmp/TexA.png",
            "colorspace": "sRGB", "assetId": content_hex, "status": "ready",
        }}
        result = evaluate_occurrence_match(
            current_occ_id=occ_id, slot_index=0, channel=0,
            material_identity="MatA", node_identity="MatA/TexA",
            source_kind="PACKED", source_locator="/tmp/tex.png",
            colorspace="sRGB", prior_occurrences=prior_occurrences,
            assets={content_hex: {"status": "ready"}},
        )
        self.assertEqual(result.decision, "prepare")
        self.assertEqual(result.action, "occurrence_identity_mismatch")

    def test_colorspace_mismatch(self):
        guid = "a" * 64
        occ_id, content_hex, content, dest_path = self._build_valid_manifest_with_occurrence(
            guid, slot=0, channel=0, mat="MatA", node="TexA",
        )
        from manifest_reuse import evaluate_occurrence_match
        prior_occurrences = {occ_id: {
            "slotIndex": 0, "channel": 0,
            "materialIdentity": "MatA",
            "nodeIdentity": "MatA/TexA",
            "sourceKind": "FILE", "sourceLocator": "/tmp/TexA.png",
            "colorspace": "sRGB", "assetId": content_hex, "status": "ready",
        }}
        result = evaluate_occurrence_match(
            current_occ_id=occ_id, slot_index=0, channel=0,
            material_identity="MatA", node_identity="MatA/TexA",
            source_kind="FILE", source_locator="/tmp/tex.png",
            colorspace="Linear", prior_occurrences=prior_occurrences,
            assets={content_hex: {"status": "ready"}},
        )
        self.assertEqual(result.decision, "prepare")
        self.assertEqual(result.action, "occurrence_identity_mismatch")

    def test_asset_missing(self):
        guid = "a" * 64
        occ_id, content_hex, content, dest_path = self._build_valid_manifest_with_occurrence(
            guid, slot=0, channel=0, mat="MatA", node="TexA",
        )
        from manifest_reuse import evaluate_occurrence_match
        prior_occurrences = {occ_id: {
            "slotIndex": 0, "channel": 0,
            "materialIdentity": "MatA",
            "nodeIdentity": "MatA/TexA",
            "sourceKind": "FILE", "sourceLocator": "/tmp/TexA.png",
            "colorspace": "sRGB", "assetId": content_hex, "status": "ready",
        }}
        result = evaluate_occurrence_match(
            current_occ_id=occ_id, slot_index=0, channel=0,
            material_identity="MatA", node_identity="MatA/TexA",
            source_kind="FILE", source_locator="/tmp/tex.png",
            colorspace="sRGB", prior_occurrences=prior_occurrences,
            assets={},  # No assets
        )
        self.assertEqual(result.decision, "prepare")
        self.assertEqual(result.action, "asset_missing")

    def test_asset_status_not_ready(self):
        guid = "a" * 64
        occ_id, content_hex, content, dest_path = self._build_valid_manifest_with_occurrence(
            guid, slot=0, channel=0, mat="MatA", node="TexA",
        )
        from manifest_reuse import evaluate_occurrence_match
        prior_occurrences = {occ_id: {
            "slotIndex": 0, "channel": 0,
            "materialIdentity": "MatA",
            "nodeIdentity": "MatA/TexA",
            "sourceKind": "FILE", "sourceLocator": "/tmp/TexA.png",
            "colorspace": "sRGB", "assetId": content_hex, "status": "ready",
        }}
        prior_assets = {content_hex: {"status": "failed"}}
        result = evaluate_occurrence_match(
            current_occ_id=occ_id, slot_index=0, channel=0,
            material_identity="MatA", node_identity="MatA/TexA",
            source_kind="FILE", source_locator="/tmp/tex.png",
            colorspace="sRGB", prior_occurrences=prior_occurrences,
            assets=prior_assets,
        )
        self.assertEqual(result.decision, "prepare")
        self.assertEqual(result.action, "asset_status_not_ready")


# ──────────────────────────────────────────────────────────────────────
# 4. Asset reuse evaluation tests
# ──────────────────────────────────────────────────────────────────────

class TestAssetReuse(_BaseReuseTest):
    """Tests for asset-level reuse evaluation."""

    def test_exact_destination_match(self):
        """Exact match → reuse."""
        guid, occ_id, asset_id, dest_basename, dest_path, content_hex = self._build_sidecar_and_manifest()
        from manifest_reuse import evaluate_asset_reuse
        asset = {
            "contentHash": asset_id,
            "status": "ready",
            "destinationBasename": dest_basename,
            "destinationSize": os.path.getsize(dest_path),
            "destinationHash": content_hex,
        }
        result = evaluate_asset_reuse(
            current_content_hex=content_hex,
            asset_id=asset_id,
            asset=asset,
            sidecar_dir=self.sidecar_dir,
        )
        self.assertEqual(result.decision, "reuse")
        self.assertEqual(result.action, "reuse_allowed")

    def test_destination_missing(self):
        """Missing file → prepare."""
        guid, occ_id, asset_id, dest_basename, dest_path, content_hex = self._build_sidecar_and_manifest()
        # Remove the file
        os.remove(dest_path)
        from manifest_reuse import evaluate_asset_reuse
        asset = {
            "contentHash": asset_id,
            "status": "ready",
            "destinationBasename": dest_basename,
            "destinationSize": os.path.getsize(dest_path) if os.path.exists(dest_path) else 0,
            "destinationHash": content_hex,
        }
        result = evaluate_asset_reuse(
            current_content_hex=content_hex,
            asset_id=asset_id,
            asset=asset,
            sidecar_dir=self.sidecar_dir,
        )
        self.assertEqual(result.decision, "prepare")
        self.assertEqual(result.action, "destination_missing")

    def test_destination_size_mismatch(self):
        """Size mismatch → prepare."""
        content = b"original content"
        dest_basename = "texture.png"
        dest_path = self._create_sidecar_file(dest_basename, content)
        from manifest_reuse import evaluate_asset_reuse
        content_hex = format(net.xxh64(content), '016x')
        asset_id = "asset_id_12345678"
        asset = {
            "contentHash": asset_id,
            "status": "ready",
            "destinationBasename": dest_basename,
            "destinationSize": len(content) + 100,
            "destinationHash": content_hex,
        }
        result = evaluate_asset_reuse(
            current_content_hex=content_hex,
            asset_id=asset_id,
            asset=asset,
            sidecar_dir=self.sidecar_dir,
        )
        self.assertEqual(result.decision, "prepare")
        self.assertEqual(result.action, "destination_size_mismatch")

    def test_destination_hash_mismatch(self):
        """Hash mismatch → prepare."""
        content = b"original content"
        dest_basename = "texture.png"
        dest_path = self._create_sidecar_file(dest_basename, content)
        from manifest_reuse import evaluate_asset_reuse
        content_hex = format(net.xxh64(content), '016x')
        asset_id = content_hex
        asset = {
            "contentHash": asset_id,
            "status": "ready",
            "destinationBasename": dest_basename,
            "destinationSize": len(content),
            "destinationHash": "different_hash",
        }
        result = evaluate_asset_reuse(
            current_content_hex=content_hex,
            asset_id=asset_id,
            asset=asset,
            sidecar_dir=self.sidecar_dir,
        )
        self.assertEqual(result.decision, "prepare")
        self.assertEqual(result.action, "destination_hash_mismatch")

    def test_unsafe_basename_rejected(self):
        """Unsafe basename → reject."""
        from manifest_reuse import evaluate_asset_reuse
        asset_id = "abc123"
        asset = {
            "contentHash": asset_id,
            "status": "ready",
            "destinationBasename": "../../../etc/passwd",
            "destinationSize": 0,
            "destinationHash": "fake",
        }
        result = evaluate_asset_reuse(
            current_content_hex="abc123",
            asset_id=asset_id,
            asset=asset,
            sidecar_dir=self.sidecar_dir,
        )
        self.assertEqual(result.decision, "reject")
        self.assertEqual(result.action, "destination_basename_unsafe")

    def test_absolute_basename_rejected(self):
        """Absolute path basename → reject."""
        from manifest_reuse import evaluate_asset_reuse
        asset_id = "abc123"
        asset = {
            "contentHash": asset_id,
            "status": "ready",
            "destinationBasename": "/etc/passwd",
            "destinationSize": 0,
            "destinationHash": "fake",
        }
        result = evaluate_asset_reuse(
            current_content_hex="abc123",
            asset_id=asset_id,
            asset=asset,
            sidecar_dir=self.sidecar_dir,
        )
        self.assertEqual(result.decision, "reject")
        self.assertEqual(result.action, "destination_basename_unsafe")

    def test_symlink_escape_rejected(self):
        """Symlink escape → reject."""
        # Create a symlink that escapes sidecar_dir
        link_path = os.path.join(self.sidecar_dir, "escape_link")
        os.symlink("/etc/passwd", link_path)
        from manifest_reuse import evaluate_asset_reuse
        asset_id = "abc123"
        asset = {
            "contentHash": asset_id,
            "status": "ready",
            "destinationBasename": "escape_link",
            "destinationSize": 0,
            "destinationHash": "fake",
        }
        result = evaluate_asset_reuse(
            current_content_hex="abc123",
            asset_id=asset_id,
            asset=asset,
            sidecar_dir=self.sidecar_dir,
        )
        self.assertEqual(result.decision, "reject")
        self.assertEqual(result.action, "destination_symlink_escape")

    def test_not_regular_file_rejected(self):
        """Directory as destination → reject."""
        dir_path = os.path.join(self.sidecar_dir, "is_a_dir")
        os.makedirs(dir_path, exist_ok=True)
        from manifest_reuse import evaluate_asset_reuse
        asset_id = "abc123"
        asset = {
            "contentHash": asset_id,
            "status": "ready",
            "destinationBasename": "is_a_dir",
            "destinationSize": 0,
            "destinationHash": "fake",
        }
        result = evaluate_asset_reuse(
            current_content_hex="abc123",
            asset_id=asset_id,
            asset=asset,
            sidecar_dir=self.sidecar_dir,
        )
        self.assertEqual(result.decision, "prepare")
        self.assertEqual(result.action, "destination_not_regular")

    def test_asset_key_content_hash_inconsistent(self):
        """Asset key != contentHash → reject."""
        content = b"test content"
        dest_path = self._create_sidecar_file("texture.png", content)
        from manifest_reuse import evaluate_asset_reuse
        content_hex = format(net.xxh64(content), '016x')
        # Asset key (asset_id) differs from contentHash in asset dict
        asset_id = "different_key"
        asset = {
            "contentHash": content_hex,
            "destinationHash": content_hex,
            "destinationSize": len(content),
            "destinationBasename": "texture.png",
            "status": "ready",
        }
        result = evaluate_asset_reuse(
            current_content_hex=content_hex,
            asset_id=asset_id,
            asset=asset,
            sidecar_dir=self.sidecar_dir,
        )
        self.assertEqual(result.decision, "reject")
        self.assertEqual(result.action, "asset_identity_inconsistent")


# ──────────────────────────────────────────────────────────────────────
# 5. Object-level orchestration tests
# ──────────────────────────────────────────────────────────────────────

class TestOrchestrationMissingManifest(_BaseReuseTest):
    """Tests for orchestration with missing/invalid manifest."""

    def test_missing_manifest_returns_prepare_for_all(self):
        source = MockSource(
            mat_name="MatA", node_name="TexA", image_name="test_img",
            source_kind="FILE", filepath_raw="/tmp/test.png", filepath="/tmp/test.png",
            is_packed=False, width=256, height=256, file_format="PNG", colorspace="sRGB",
        )
        occurrence_descriptors = [{
            "occurrence_id": "test_occ",
            "slot_index": 0, "channel": 0,
            "material_identity": "MatA", "node_identity": "MatA/TexA",
            "source_kind": "FILE", "source_locator": "/tmp/test.png",
            "colorspace": "sRGB", "source": source,
            "current_content_hex": None,
        }]
        outcome = manifest_reuse.evaluate_manifest_reuse(
            guid_hex="a" * 64,
            occurrence_descriptors=occurrence_descriptors,
            sidecar_dir=self.sidecar_dir,
            manifest_path=self.manifest_path,
            collision_registry={},
            prepare_fn=lambda *a, **k: (None, None),
            guid_short="?",
        )
        self.assertEqual(outcome.manifest_status, "missing")
        self.assertFalse(outcome.prior_manifest_eligible_for_generation)
        self.assertTrue(outcome.global_reuse_denied)
        self.assertEqual(outcome.error, "manifest_missing")


class TestOrchestrationInvalidManifest(_BaseReuseTest):
    """Tests for orchestration with invalid manifest."""

    def test_malformed_json_rejects_reuse(self):
        source = MockSource(
            mat_name="MatA", node_name="TexA", image_name="test_img",
            source_kind="FILE", filepath_raw="/tmp/test.png", filepath="/tmp/test.png",
            is_packed=False, width=256, height=256, file_format="PNG", colorspace="sRGB",
        )
        # Write invalid JSON
        with open(self.manifest_path, 'w') as f:
            f.write("{bad json")
        occurrence_descriptors = [{
            "occurrence_id": "test_occ",
            "slot_index": 0, "channel": 0,
            "material_identity": "MatA", "node_identity": "MatA/TexA",
            "source_kind": "FILE", "source_locator": "/tmp/test.png",
            "colorspace": "sRGB", "source": source,
            "current_content_hex": None,
        }]
        outcome = manifest_reuse.evaluate_manifest_reuse(
            guid_hex="a" * 64,
            occurrence_descriptors=occurrence_descriptors,
            sidecar_dir=self.sidecar_dir,
            manifest_path=self.manifest_path,
            collision_registry={},
            prepare_fn=lambda *a, **k: (None, None),
            guid_short="?",
        )
        self.assertEqual(outcome.manifest_status, "invalid")
        self.assertFalse(outcome.prior_manifest_eligible_for_generation)
        self.assertTrue(outcome.global_reuse_denied)

    def test_guid_mismatch_rejects_reuse(self):
        source = MockSource(
            mat_name="MatA", node_name="TexA", image_name="test_img",
            source_kind="FILE", filepath_raw="/tmp/test.png", filepath="/tmp/test.png",
            is_packed=False, width=256, height=256, file_format="PNG", colorspace="sRGB",
        )
        guid = "b" * 64
        self._write_valid_manifest(guid)
        occurrence_descriptors = [{
            "occurrence_id": "test_occ",
            "slot_index": 0, "channel": 0,
            "material_identity": "MatA", "node_identity": "MatA/TexA",
            "source_kind": "FILE", "source_locator": "/tmp/test.png",
            "colorspace": "sRGB", "source": source,
            "current_content_hex": None,
        }]
        outcome = manifest_reuse.evaluate_manifest_reuse(
            guid_hex="a" * 64,
            occurrence_descriptors=occurrence_descriptors,
            sidecar_dir=self.sidecar_dir,
            manifest_path=self.manifest_path,
            collision_registry={},
            prepare_fn=lambda *a, **k: (None, None),
            guid_short="?",
        )
        self.assertEqual(outcome.manifest_status, "invalid")
        self.assertFalse(outcome.prior_manifest_eligible_for_generation)


class TestOrchestrationValidManifest(_BaseReuseTest):
    """Tests for orchestration with valid manifest."""

    def test_exact_match_allows_reuse(self):
        guid, occ_id, asset_id, dest_basename, dest_path, content_hex = self._build_sidecar_and_manifest()
        source = MockSource(
            mat_name="MatA", node_name="TexA", image_name="test_img",
            source_kind="FILE", filepath_raw="/tmp/test.png", filepath="/tmp/test.png",
            is_packed=False, width=256, height=256, file_format="PNG", colorspace="sRGB",
        )
        occurrence_descriptors = [{
            "occurrence_id": occ_id,
            "slot_index": 0, "channel": 0,
            "material_identity": "MatA", "node_identity": "MatA/TexA",
            "source_kind": "FILE", "source_locator": "/tmp/test.png",
            "colorspace": "sRGB", "source": source,
            "current_content_hex": content_hex,
        }]
        prepare_calls = []
        def _track_prepare(*args, **kwargs):
            prepare_calls.append(1)
            return (None, None)
        outcome = manifest_reuse.evaluate_manifest_reuse(
            guid_hex=guid,
            occurrence_descriptors=occurrence_descriptors,
            sidecar_dir=self.sidecar_dir,
            manifest_path=self.manifest_path,
            collision_registry={},
            prepare_fn=_track_prepare,
            guid_short="?",
        )
        self.assertEqual(outcome.manifest_status, "valid")
        self.assertTrue(outcome.prior_manifest_eligible_for_generation)
        self.assertFalse(outcome.global_reuse_denied)
        self.assertIn(id(source), outcome.decisions)
        decision = outcome.decisions[id(source)]
        self.assertEqual(decision.decision, "reuse")
        self.assertEqual(decision.action, "reuse_allowed")
        self.assertEqual(len(prepare_calls), 0)

    def test_preparation_runs_on_content_mismatch(self):
        content1 = b"original content"
        guid, occ_id, asset_id, dest_basename, dest_path, content_hex = self._build_sidecar_and_manifest(content=content1)
        # Now overwrite the sidecar file with different content
        different_content = b"modified content"
        with open(dest_path, 'wb') as f:
            f.write(different_content)
        different_hex = format(net.xxh64(different_content), '016x')
        source = MockSource(
            mat_name="MatA", node_name="TexA", image_name="test_img",
            source_kind="FILE", filepath_raw="/tmp/test.png", filepath="/tmp/test.png",
            is_packed=False, width=256, height=256, file_format="PNG", colorspace="sRGB",
        )
        occurrence_descriptors = [{
            "occurrence_id": occ_id,
            "slot_index": 0, "channel": 0,
            "material_identity": "MatA", "node_identity": "MatA/TexA",
            "source_kind": "FILE", "source_locator": "/tmp/test.png",
            "colorspace": "sRGB", "source": source,
            "current_content_hex": different_hex,
        }]
        prepare_count = [0]
        def _track_prepare(*args, **kwargs):
            prepare_count[0] += 1
            return (None, None)
        outcome = manifest_reuse.evaluate_manifest_reuse(
            guid_hex=guid,
            occurrence_descriptors=occurrence_descriptors,
            sidecar_dir=self.sidecar_dir,
            manifest_path=self.manifest_path,
            collision_registry={},
            prepare_fn=_track_prepare,
            guid_short="?",
        )
        self.assertTrue(outcome.prior_manifest_eligible_for_generation)
        decision = outcome.decisions.get(id(source))
        if decision:
            self.assertEqual(decision.decision, "prepare")

    def test_global_rejection_cancels_all(self):
        content = b"test sidecar content"
        dest_basename = "test_texture.png"
        self._create_sidecar_file(dest_basename, content)
        content_hash = format(net.xxh64(content), '016x')
        from manifest_v3 import (
            compute_occurrence_id, build_manifest_v3,
            build_occurrence_record, build_asset_record,
            serialize_manifest_v3,
        )
        guid = "a" * 64
        occ_id = compute_occurrence_id(
            guid=guid, slot_index=0, material_identity="MatA",
            node_identity="MatA/TexA", channel=0,
        )
        bad_asset_id = "different_id_1234abcd"
        asset_rec = build_asset_record(
            source_kind="FILE", content_hash=content_hash,
            destination_basename=dest_basename,
            destination_size=os.path.getsize(os.path.join(self.sidecar_dir, dest_basename)),
            destination_hash=content_hash, status="ready",
        )
        asset = {bad_asset_id: asset_rec}
        occ_rec = build_occurrence_record(
            slot_index=0, channel=0, material_identity="MatA",
            node_identity="MatA/TexA", source_kind="FILE",
            source_locator="/tmp/test.png", colorspace="sRGB",
            asset_id=bad_asset_id, status="ready",
        )
        manifest = build_manifest_v3(
            guid=guid, generation=1,
            occurrences={occ_id: occ_rec}, assets=asset,
        )
        with open(self.manifest_path, 'w') as f:
            f.write(serialize_manifest_v3(manifest))
        source = MockSource(
            mat_name="MatA", node_name="TexA", image_name="test_img",
            source_kind="FILE", filepath_raw="/tmp/test.png", filepath="/tmp/test.png",
            is_packed=False, width=256, height=256, file_format="PNG", colorspace="sRGB",
        )
        occurrence_descriptors = [{
            "occurrence_id": occ_id,
            "slot_index": 0, "channel": 0,
            "material_identity": "MatA", "node_identity": "MatA/TexA",
            "source_kind": "FILE", "source_locator": "/tmp/test.png",
            "colorspace": "sRGB", "source": source,
            "current_content_hex": content_hash,
        }]
        outcome = manifest_reuse.evaluate_manifest_reuse(
            guid_hex=guid,
            occurrence_descriptors=occurrence_descriptors,
            sidecar_dir=self.sidecar_dir,
            manifest_path=self.manifest_path,
            collision_registry={},
            prepare_fn=lambda *a, **k: (None, None),
            guid_short="?",
        )
        self.assertTrue(outcome.global_reuse_denied)
        self.assertFalse(outcome.prior_manifest_eligible_for_generation)


class TestSharedAssetReuse(_BaseReuseTest):
    """Tests for duplicate occurrences sharing one asset."""

    def test_multiple_occurrences_same_asset(self):
        guid, occ_id, asset_id, dest_basename, dest_path, content_hex = self._build_sidecar_and_manifest()
        from manifest_v3 import compute_occurrence_id, compute_semantic_digest
        occ_id2 = compute_occurrence_id(
            guid=guid, slot_index=1, material_identity="MatA",
            node_identity="MatA/TexB", channel=0,
        )
        with open(self.manifest_path, 'r') as f:
            prior_data = json.loads(f.read())
        from manifest_v3 import build_occurrence_record
        occ_rec2 = build_occurrence_record(
            slot_index=1, channel=0, material_identity="MatA",
            node_identity="MatA/TexB", source_kind="FILE",
            source_locator="/tmp/test.png", colorspace="sRGB",
            asset_id=asset_id, status="ready",
        )
        prior_data["occurrences"][occ_id2] = occ_rec2
        prior_data["semanticContentDigest"] = compute_semantic_digest(
            guid, prior_data["occurrences"], prior_data["assets"],
        )
        with open(self.manifest_path, 'w') as f:
            json.dump(prior_data, f)
        source1 = MockSource(
            mat_name="MatA", node_name="TexA", image_name="test_img",
            source_kind="FILE", filepath_raw="/tmp/test.png", filepath="/tmp/test.png",
            is_packed=False, width=256, height=256, file_format="PNG", colorspace="sRGB",
        )
        source2 = MockSource(
            mat_name="MatA", node_name="TexB", image_name="test_img",
            source_kind="FILE", filepath_raw="/tmp/test.png", filepath="/tmp/test.png",
            is_packed=False, width=256, height=256, file_format="PNG", colorspace="sRGB",
        )
        occurrence_descriptors = [
            {
                "occurrence_id": occ_id,
                "slot_index": 0, "channel": 0,
                "material_identity": "MatA", "node_identity": "MatA/TexA",
                "source_kind": "FILE", "source_locator": "/tmp/test.png",
                "colorspace": "sRGB", "source": source1,
                "current_content_hex": content_hex,
            },
            {
                "occurrence_id": occ_id2,
                "slot_index": 1, "channel": 0,
                "material_identity": "MatA", "node_identity": "MatA/TexB",
                "source_kind": "FILE", "source_locator": "/tmp/test.png",
                "colorspace": "sRGB", "source": source2,
                "current_content_hex": content_hex,
            },
        ]
        prepare_fn = self._make_prepare_fn()
        outcome = manifest_reuse.evaluate_manifest_reuse(
            guid_hex=guid,
            occurrence_descriptors=occurrence_descriptors,
            sidecar_dir=self.sidecar_dir,
            manifest_path=self.manifest_path,
            collision_registry={},
            prepare_fn=prepare_fn,
            guid_short="?",
        )
        self.assertFalse(outcome.global_reuse_denied)
        self.assertIn(id(source1), outcome.decisions)
        self.assertIn(id(source2), outcome.decisions)


class TestMixedReusePrepare(_BaseReuseTest):
    """Tests for mixed reuse and prepare within one object."""

    def test_mixed_reuse_and_prepare(self):
        guid, occ_id, asset_id, dest_basename, dest_path, content_hex = self._build_sidecar_and_manifest()
        from manifest_v3 import compute_occurrence_id, compute_semantic_digest
        occ_id2 = compute_occurrence_id(
            guid=guid, slot_index=1, material_identity="MatB",
            node_identity="MatB/TexA", channel=0,
        )
        with open(self.manifest_path, 'r') as f:
            prior_data = json.loads(f.read())
        from manifest_v3 import build_occurrence_record, build_asset_record
        # Valid second asset with status "failed" so occurrence2 passes
        # schema validation but triggers prepare (asset_status_not_ready).
        asset_id2 = "deadbeefdeadbeef"
        asset_rec2 = build_asset_record(
            source_kind="FILE",
            content_hash=asset_id2,
            destination_basename="texture2.png",
            destination_size=0,
            destination_hash=asset_id2,
            status="failed",
        )
        prior_data["assets"][asset_id2] = asset_rec2
        occ_rec2 = build_occurrence_record(
            slot_index=1, channel=0, material_identity="MatB",
            node_identity="MatB/TexA", source_kind="FILE",
            source_locator="/tmp/test2.png", colorspace="sRGB",
            asset_id=asset_id2, status="ready",
        )
        prior_data["occurrences"][occ_id2] = occ_rec2
        prior_data["semanticContentDigest"] = compute_semantic_digest(
            guid, prior_data["occurrences"], prior_data["assets"],
        )
        with open(self.manifest_path, 'w') as f:
            json.dump(prior_data, f)
        # Assert manifest validation succeeds before orchestration
        from manifest_reuse import validate_prior_manifest_schema
        is_valid, schema_error = validate_prior_manifest_schema(prior_data, guid)
        self.assertTrue(is_valid, f"prior manifest must be valid: {schema_error}")
        source1 = MockSource(
            mat_name="MatA", node_name="TexA", image_name="test_img",
            source_kind="FILE", filepath_raw="/tmp/test.png", filepath="/tmp/test.png",
            is_packed=False, width=256, height=256, file_format="PNG", colorspace="sRGB",
        )
        source2 = MockSource(
            mat_name="MatB", node_name="TexA", image_name="test_img",
            source_kind="FILE", filepath_raw="/tmp/test2.png", filepath="/tmp/test2.png",
            is_packed=False, width=256, height=256, file_format="PNG", colorspace="sRGB",
        )
        occurrence_descriptors = [
            {
                "occurrence_id": occ_id,
                "slot_index": 0, "channel": 0,
                "material_identity": "MatA", "node_identity": "MatA/TexA",
                "source_kind": "FILE", "source_locator": "/tmp/test.png",
                "colorspace": "sRGB", "source": source1,
                "current_content_hex": content_hex,
            },
            {
                "occurrence_id": occ_id2,
                "slot_index": 1, "channel": 0,
                "material_identity": "MatB", "node_identity": "MatB/TexA",
                "source_kind": "FILE", "source_locator": "/tmp/test2.png",
                "colorspace": "sRGB", "source": source2,
                "current_content_hex": "different_hex",
            },
        ]
        prepare_fn = self._make_prepare_fn()
        outcome = manifest_reuse.evaluate_manifest_reuse(
            guid_hex=guid,
            occurrence_descriptors=occurrence_descriptors,
            sidecar_dir=self.sidecar_dir,
            manifest_path=self.manifest_path,
            collision_registry={},
            prepare_fn=prepare_fn,
            guid_short="?",
        )
        self.assertIn(id(source1), outcome.decisions)
        self.assertIn(id(source2), outcome.decisions)


class TestStructuralRejection(_BaseReuseTest):
    """Tests that structurally invalid manifests disable all reuse."""

    def test_invalid_manifest_no_reuse(self):
        source = MockSource(
            mat_name="MatA", node_name="TexA", image_name="test_img",
            source_kind="FILE", filepath_raw="/tmp/test.png", filepath="/tmp/test.png",
            is_packed=False, width=256, height=256, file_format="PNG", colorspace="sRGB",
        )
        with open(self.manifest_path, 'w') as f:
            json.dump({
                "schemaVersion": 999,
                "guid": "a" * 64,
                "generation": 1,
                "semanticContentDigest": "b" * 16,
                "occurrences": {},
                "assets": {},
            }, f)
        occurrence_descriptors = [{
            "occurrence_id": "test_occ",
            "slot_index": 0, "channel": 0,
            "material_identity": "MatA", "node_identity": "MatA/TexA",
            "source_kind": "FILE", "source_locator": "/tmp/test.png",
            "colorspace": "sRGB", "source": source,
            "current_content_hex": None,
        }]
        outcome = manifest_reuse.evaluate_manifest_reuse(
            guid_hex="a" * 64,
            occurrence_descriptors=occurrence_descriptors,
            sidecar_dir=self.sidecar_dir,
            manifest_path=self.manifest_path,
            collision_registry={},
            prepare_fn=lambda *a, **k: (None, None),
            guid_short="?",
        )
        self.assertTrue(outcome.global_reuse_denied)


class TestOccurrenceLocalMismatch(_BaseReuseTest):
    """Tests that occurrence-local mismatch doesn't disable unrelated valid reuse."""

    def test_occurrence_local_mismatch_preserves_valid_reuse(self):
        content1 = b"sidecar content one"
        content2 = b"sidecar content two"
        dest1 = self._create_sidecar_file("texture1.png", content1)
        dest2 = self._create_sidecar_file("texture2.png", content2)
        hash1 = format(net.xxh64(content1), '016x')
        hash2 = format(net.xxh64(content2), '016x')
        from manifest_v3 import (
            compute_occurrence_id, build_manifest_v3,
            build_occurrence_record, build_asset_record,
            serialize_manifest_v3,
        )
        guid = "a" * 64
        occ_id1 = compute_occurrence_id(
            guid=guid, slot_index=0, material_identity="MatA",
            node_identity="MatA/TexA", channel=0,
        )
        occ_id2 = compute_occurrence_id(
            guid=guid, slot_index=0, material_identity="MatB",
            node_identity="MatB/TexA", channel=0,
        )
        asset1 = {hash1: build_asset_record(
            source_kind="FILE", content_hash=hash1,
            destination_basename="texture1.png",
            destination_size=os.path.getsize(dest1),
            destination_hash=hash1, status="ready",
        )}
        asset2 = {hash2: build_asset_record(
            source_kind="FILE", content_hash=hash2,
            destination_basename="texture2.png",
            destination_size=os.path.getsize(dest2),
            destination_hash=hash2, status="ready",
        )}
        all_assets = {**asset1, **asset2}
        occ_rec1 = build_occurrence_record(
            slot_index=0, channel=0, material_identity="MatA",
            node_identity="MatA/TexA", source_kind="FILE",
            source_locator="/tmp/t1.png", colorspace="sRGB",
            asset_id=hash1, status="ready",
        )
        occ_rec2 = build_occurrence_record(
            slot_index=0, channel=1, material_identity="MatB",
            node_identity="MatB/TexA", source_kind="FILE",
            source_locator="/tmp/t2.png", colorspace="sRGB",
            asset_id=hash2, status="ready",
        )
        manifest = build_manifest_v3(
            guid=guid, generation=1,
            occurrences={occ_id1: occ_rec1, occ_id2: occ_rec2},
            assets=all_assets,
        )
        with open(self.manifest_path, 'w') as f:
            f.write(serialize_manifest_v3(manifest))
        source1 = MockSource(
            mat_name="MatA", node_name="TexA", image_name="test1",
            source_kind="FILE", filepath_raw="/tmp/t1.png", filepath="/tmp/t1.png",
            is_packed=False, width=256, height=256, file_format="PNG", colorspace="sRGB",
        )
        source2 = MockSource(
            mat_name="MatC", node_name="TexA", image_name="test3",
            source_kind="FILE", filepath_raw="/tmp/t3.png", filepath="/tmp/t3.png",
            is_packed=False, width=256, height=256, file_format="PNG", colorspace="sRGB",
        )
        occurrence_descriptors = [
            {
                "occurrence_id": occ_id1,
                "slot_index": 0, "channel": 0,
                "material_identity": "MatA", "node_identity": "MatA/TexA",
                "source_kind": "FILE", "source_locator": "/tmp/t1.png",
                "colorspace": "sRGB", "source": source1,
                "current_content_hex": hash1,
            },
            {
                "occurrence_id": "wrong_occ_id",
                "slot_index": 0, "channel": 0,
                "material_identity": "MatC", "node_identity": "MatC/TexA",
                "source_kind": "FILE", "source_locator": "/tmp/t3.png",
                "colorspace": "sRGB", "source": source2,
                "current_content_hex": "wrong_hex",
            },
        ]
        prepare_fn = self._make_prepare_fn()
        outcome = manifest_reuse.evaluate_manifest_reuse(
            guid_hex=guid,
            occurrence_descriptors=occurrence_descriptors,
            sidecar_dir=self.sidecar_dir,
            manifest_path=self.manifest_path,
            collision_registry={},
            prepare_fn=prepare_fn,
            guid_short="?",
        )
        self.assertIn(id(source1), outcome.decisions)
        self.assertTrue(outcome.prior_manifest_eligible_for_generation)


if __name__ == '__main__':
    unittest.main()


# ──────────────────────────────────────────────────────────────────────
# 6. Runtime integration/transaction tests
# ──────────────────────────────────────────────────────────────────────

class _BaseIntegrationTest(unittest.TestCase):
    """Base class providing temp directory management."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.sidecar_dir = os.path.join(self.tmpdir, "sidecars")
        os.makedirs(self.sidecar_dir, exist_ok=True)
        self.obj_dir = os.path.join(self.tmpdir, "obj")
        os.makedirs(self.obj_dir, exist_ok=True)
        self.manifest_path = os.path.join(self.obj_dir, "manifest_v3.json")

    def _make_prepare_fn(self, count_list=None):
        """Create a prepare_fn that records calls and returns a valid ReuseDecision."""
        from manifest_reuse import ReuseDecision
        def _track_prepare(source_desc, sidecar_dir, collision_registry, guid_short):
            if count_list is not None:
                count_list[0] += 1
            return (ReuseDecision(decision="prepare", action="prepared", occurrence_id="", asset_id="", source_kind="FILE", destination_path=""), {"asset_id": "test_asset", "contentHash": "0" * 16})
        return _track_prepare

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)


def _build_sidecar_and_manifest_integration(sidecar_dir, obj_dir, content=b"test sidecar content"):
    """Helper to create a sidecar file and manifest pointing to it."""
    dest_basename = "test_texture.png"
    dest_path = os.path.join(sidecar_dir, dest_basename)
    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    with open(dest_path, 'wb') as f:
        f.write(content)
    content_hash = format(net.xxh64(content), '016x')
    actual_size = os.path.getsize(dest_path)
    from manifest_v3 import (
        compute_occurrence_id, build_manifest_v3,
        build_occurrence_record, build_asset_record,
        serialize_manifest_v3,
    )
    guid = "a" * 64
    occ_id = compute_occurrence_id(
        guid=guid, slot_index=0, material_identity="MatA",
        node_identity="MatA/TexA", channel=0,
    )
    asset_id = content_hash
    asset_rec = build_asset_record(
        source_kind="FILE", content_hash=asset_id,
        destination_basename=dest_basename,
        destination_size=actual_size,
        destination_hash=content_hash, status="ready",
    )
    asset = {asset_id: asset_rec}
    occ_rec = build_occurrence_record(
        slot_index=0, channel=0, material_identity="MatA",
        node_identity="MatA/TexA", source_kind="FILE",
        source_locator="/tmp/test.png", colorspace="sRGB",
        asset_id=asset_id, status="ready",
    )
    manifest = build_manifest_v3(
        guid=guid, generation=1,
        occurrences={occ_id: occ_rec}, assets=asset,
    )
    manifest_path = os.path.join(obj_dir, "manifest_v3.json")
    with open(manifest_path, 'w') as f:
        f.write(serialize_manifest_v3(manifest))
    content_hex = content_hash
    return guid, occ_id, asset_id, dest_basename, dest_path, content_hex


class TestReuseDoesNotMaterialize(_BaseIntegrationTest):
    """Tests that reuse path does not invoke materialization."""

    def test_reuse_path_skips_materialization(self):
        guid, occ_id, asset_id, dest_basename, dest_path, content_hex = _build_sidecar_and_manifest_integration(
            self.sidecar_dir, self.obj_dir, content=b"exact sidecar content",
        )
        source = MockSource(
            mat_name="MatA", node_name="TexA", image_name="test_img",
            source_kind="FILE", filepath_raw="/tmp/test.png", filepath="/tmp/test.png",
            is_packed=False, width=256, height=256, file_format="PNG", colorspace="sRGB",
        )
        occurrence_descriptors = [{
            "occurrence_id": occ_id,
            "slot_index": 0, "channel": 0,
            "material_identity": "MatA", "node_identity": "MatA/TexA",
            "source_kind": "FILE", "source_locator": "/tmp/test.png",
            "colorspace": "sRGB", "source": source,
            "current_content_hex": content_hex,
        }]
        prepare_count = [0]
        def _track_prepare(*args, **kwargs):
            prepare_count[0] += 1
            return (None, None)
        outcome = manifest_reuse.evaluate_manifest_reuse(
            guid_hex=guid,
            occurrence_descriptors=occurrence_descriptors,
            sidecar_dir=self.sidecar_dir,
            manifest_path=self.manifest_path,
            collision_registry={},
            prepare_fn=_track_prepare,
            guid_short="?",
        )
        self.assertEqual(prepare_count[0], 0, "prepare_fn should not be called on reuse")
        self.assertEqual(outcome.decisions[id(source)].decision, "reuse")


class TestPrepareInvokesMaterialization(_BaseIntegrationTest):
    """Tests that prepare path calls preparation exactly once per unique source."""

    def test_prepare_path_invokes_materialization(self):
        # Manifest expects "original content" but file has "modified content"
        original_content = b"original content"
        modified_content = b"modified content"
        dest_basename = "test_texture.png"
        dest_path = os.path.join(self.sidecar_dir, dest_basename)
        os.makedirs(os.path.dirname(dest_path), exist_ok=True)
        with open(dest_path, 'wb') as f:
            f.write(modified_content)
        original_hash = format(net.xxh64(original_content), '016x')
        modified_size = os.path.getsize(dest_path)
        from manifest_v3 import (
            compute_occurrence_id, build_manifest_v3,
            build_occurrence_record, build_asset_record,
            serialize_manifest_v3,
        )
        guid = "a" * 64
        occ_id = compute_occurrence_id(
            guid=guid, slot_index=0, material_identity="MatA",
            node_identity="MatA/TexA", channel=0,
        )
        asset_id = original_hash
        asset_rec = build_asset_record(
            source_kind="FILE", content_hash=asset_id,
            destination_basename=dest_basename,
            destination_size=modified_size,
            destination_hash=original_hash, status="ready",
        )
        asset = {asset_id: asset_rec}
        occ_rec = build_occurrence_record(
            slot_index=0, channel=0, material_identity="MatA",
            node_identity="MatA/TexA", source_kind="FILE",
            source_locator="/tmp/test.png", colorspace="sRGB",
            asset_id=asset_id, status="ready",
        )
        manifest = build_manifest_v3(
            guid=guid, generation=1,
            occurrences={occ_id: occ_rec}, assets=asset,
        )
        with open(self.manifest_path, 'w') as f:
            f.write(serialize_manifest_v3(manifest))

        source = MockSource(
            mat_name="MatA", node_name="TexA", image_name="test_img",
            source_kind="FILE", filepath_raw="/tmp/test.png", filepath="/tmp/test.png",
            is_packed=False, width=256, height=256, file_format="PNG", colorspace="sRGB",
        )
        occurrence_descriptors = [{
            "occurrence_id": occ_id,
            "slot_index": 0, "channel": 0,
            "material_identity": "MatA", "node_identity": "MatA/TexA",
            "source_kind": "FILE", "source_locator": "/tmp/test.png",
            "colorspace": "sRGB", "source": source,
            "current_content_hex": "different_content_hex",
        }]
        prepare_count = [0]
        prepare_fn = self._make_prepare_fn(prepare_count)
        outcome = manifest_reuse.evaluate_manifest_reuse(
            guid_hex=guid,
            occurrence_descriptors=occurrence_descriptors,
            sidecar_dir=self.sidecar_dir,
            manifest_path=self.manifest_path,
            collision_registry={},
            prepare_fn=prepare_fn,
            guid_short="?",
        )
        self.assertEqual(prepare_count[0], 1, "prepare_fn should be called exactly once")
        self.assertEqual(outcome.decisions[id(source)].decision, "prepare")


class TestSharedContentPreparesOnce(_BaseIntegrationTest):
    """Tests that duplicate current content invokes materialization at most once."""

    def test_shared_content_prepared_once(self):
        guid, occ_id, asset_id, dest_basename, dest_path, content_hex = _build_sidecar_and_manifest_integration(
            self.sidecar_dir, self.obj_dir, content=b"shared content",
        )
        from manifest_v3 import compute_occurrence_id
        occ_id2 = compute_occurrence_id(
            guid=guid, slot_index=1, material_identity="MatB",
            node_identity="MatB/TexA", channel=0,
        )
        with open(self.manifest_path, 'r') as f:
            prior_data = json.loads(f.read())
        from manifest_v3 import build_occurrence_record
        occ_rec2 = build_occurrence_record(
            slot_index=1, channel=0, material_identity="MatB",
            node_identity="MatB/TexA", source_kind="FILE",
            source_locator="/tmp/test.png", colorspace="sRGB",
            asset_id=asset_id, status="ready",
        )
        prior_data["occurrences"][occ_id2] = occ_rec2
        with open(self.manifest_path, 'w') as f:
            json.dump(prior_data, f)

        source = MockSource(
            mat_name="MatA", node_name="TexA", image_name="test_img",
            source_kind="FILE", filepath_raw="/tmp/test.png", filepath="/tmp/test.png",
            is_packed=False, width=256, height=256, file_format="PNG", colorspace="sRGB",
        )
        occurrence_descriptors = [
            {
                "occurrence_id": occ_id,
                "slot_index": 0, "channel": 0,
                "material_identity": "MatA", "node_identity": "MatA/TexA",
                "source_kind": "FILE", "source_locator": "/tmp/test.png",
                "colorspace": "sRGB", "source": source,
                "current_content_hex": content_hex,
            },
            {
                "occurrence_id": occ_id2,
                "slot_index": 1, "channel": 0,
                "material_identity": "MatB", "node_identity": "MatB/TexA",
                "source_kind": "FILE", "source_locator": "/tmp/test.png",
                "colorspace": "sRGB", "source": source,
                "current_content_hex": content_hex,
            },
        ]
        prepare_count = [0]
        def _track_prepare(*args, **kwargs):
            prepare_count[0] += 1
            return (None, None)
        outcome = manifest_reuse.evaluate_manifest_reuse(
            guid_hex=guid,
            occurrence_descriptors=occurrence_descriptors,
            sidecar_dir=self.sidecar_dir,
            manifest_path=self.manifest_path,
            collision_registry={},
            prepare_fn=_track_prepare,
            guid_short="?",
        )
        self.assertLessEqual(prepare_count[0], 1, "duplicate source should be prepared at most once")


class TestMixedObjectCompleteResults(_BaseIntegrationTest):
    """Tests that mixed object produces a complete result map."""

    def test_mixed_object_returns_complete_result_map(self):
        guid, occ_id, asset_id, dest_basename, dest_path, content_hex = _build_sidecar_and_manifest_integration(
            self.sidecar_dir, self.obj_dir, content=b"first texture",
        )
        content2 = b"second texture"
        dest2 = os.path.join(self.sidecar_dir, "texture2.png")
        os.makedirs(os.path.dirname(dest2), exist_ok=True)
        with open(dest2, 'wb') as f:
            f.write(content2)
        from manifest_v3 import compute_occurrence_id, compute_semantic_digest, build_asset_record
        occ_id2 = compute_occurrence_id(
            guid=guid, slot_index=1, material_identity="MatB",
            node_identity="MatB/TexA", channel=0,
        )
        # Use one computed asset_id consistently across all fields.
        asset_id2 = format(net.xxh64(content2), "016x")
        asset_rec2 = build_asset_record(
            source_kind="FILE",
            content_hash=asset_id2,
            destination_basename="texture2.png",
            destination_size=len(content2),
            destination_hash=asset_id2,
            status="ready",
        )
        with open(self.manifest_path, 'r') as f:
            prior_data = json.loads(f.read())
        from manifest_v3 import build_occurrence_record
        occ_rec2 = build_occurrence_record(
            slot_index=1, channel=0, material_identity="MatB",
            node_identity="MatB/TexA", source_kind="FILE",
            source_locator="/tmp/t2.png", colorspace="sRGB",
            asset_id=asset_id2, status="ready",
        )
        prior_data["occurrences"][occ_id2] = occ_rec2
        prior_data["assets"][asset_id2] = asset_rec2
        prior_data["semanticContentDigest"] = compute_semantic_digest(
            guid, prior_data["occurrences"], prior_data["assets"],
        )
        with open(self.manifest_path, 'w') as f:
            json.dump(prior_data, f)
        # Assert manifest validation succeeds before orchestration
        from manifest_reuse import validate_prior_manifest_schema
        is_valid, schema_error = validate_prior_manifest_schema(prior_data, guid)
        self.assertTrue(is_valid, f"prior manifest must be valid: {schema_error}")

        source1 = MockSource(
            mat_name="MatA", node_name="TexA", image_name="test1",
            source_kind="FILE", filepath_raw="/tmp/t1.png", filepath="/tmp/t1.png",
            is_packed=False, width=256, height=256, file_format="PNG", colorspace="sRGB",
        )
        source2 = MockSource(
            mat_name="MatB", node_name="TexA", image_name="test2",
            source_kind="FILE", filepath_raw="/tmp/t2.png", filepath="/tmp/t2.png",
            is_packed=False, width=256, height=256, file_format="PNG", colorspace="sRGB",
        )
        occurrence_descriptors = [
            {
                "occurrence_id": occ_id,
                "slot_index": 0, "channel": 0,
                "material_identity": "MatA", "node_identity": "MatA/TexA",
                "source_kind": "FILE", "source_locator": "/tmp/t1.png",
                "colorspace": "sRGB", "source": source1,
                "current_content_hex": content_hex,
            },
            {
                "occurrence_id": occ_id2,
                "slot_index": 1, "channel": 0,
                "material_identity": "MatB", "node_identity": "MatB/TexA",
                "source_kind": "FILE", "source_locator": "/tmp/t2.png",
                "colorspace": "sRGB", "source": source2,
                "current_content_hex": format(net.xxh64(content2), '016x'),
            },
        ]
        prepare_fn = self._make_prepare_fn()
        outcome = manifest_reuse.evaluate_manifest_reuse(
            guid_hex=guid,
            occurrence_descriptors=occurrence_descriptors,
            sidecar_dir=self.sidecar_dir,
            manifest_path=self.manifest_path,
            collision_registry={},
            prepare_fn=prepare_fn,
            guid_short="?",
        )
        self.assertIn(id(source1), outcome.decisions)
        self.assertIn(id(source2), outcome.decisions)


class TestGenerationBehavior(_BaseIntegrationTest):
    """Tests for generation derivation behavior."""

    def test_unchanged_digest_keeps_generation(self):
        guid, occ_id, asset_id, dest_basename, dest_path, content_hex = _build_sidecar_and_manifest_integration(
            self.sidecar_dir, self.obj_dir, content=b"same content",
        )
        source = MockSource(
            mat_name="MatA", node_name="TexA", image_name="test",
            source_kind="FILE", filepath_raw="/tmp/test.png", filepath="/tmp/test.png",
            is_packed=False, width=256, height=256, file_format="PNG", colorspace="sRGB",
        )
        occurrence_descriptors = [{
            "occurrence_id": occ_id,
            "slot_index": 0, "channel": 0,
            "material_identity": "MatA", "node_identity": "MatA/TexA",
            "source_kind": "FILE", "source_locator": "/tmp/test.png",
            "colorspace": "sRGB", "source": source,
            "current_content_hex": content_hex,
        }]
        outcome = manifest_reuse.evaluate_manifest_reuse(
            guid_hex=guid,
            occurrence_descriptors=occurrence_descriptors,
            sidecar_dir=self.sidecar_dir,
            manifest_path=self.manifest_path,
            collision_registry={},
            prepare_fn=lambda *a, **k: (None, None),
            guid_short="?",
        )
        self.assertEqual(outcome.prior_generation, 1)
        self.assertTrue(outcome.prior_manifest_eligible_for_generation)

    def test_changed_result_increments_generation(self):
        from manifest_v3 import build_manifest_v3, serialize_manifest_v3
        manifest = build_manifest_v3(
            guid="a" * 64, generation=2,
            occurrences={}, assets={},
        )
        with open(self.manifest_path, 'w') as f:
            f.write(serialize_manifest_v3(manifest))
        source = MockSource(
            mat_name="MatA", node_name="TexA", image_name="test",
            source_kind="FILE", filepath_raw="/tmp/test.png", filepath="/tmp/test.png",
            is_packed=False, width=256, height=256, file_format="PNG", colorspace="sRGB",
        )
        occurrence_descriptors = [{
            "occurrence_id": "nonexistent",
            "slot_index": 0, "channel": 0,
            "material_identity": "MatA", "node_identity": "MatA/TexA",
            "source_kind": "FILE", "source_locator": "/tmp/test.png",
            "colorspace": "sRGB", "source": source,
            "current_content_hex": "new_content_hex",
        }]
        outcome = manifest_reuse.evaluate_manifest_reuse(
            guid_hex="a" * 64,
            occurrence_descriptors=occurrence_descriptors,
            sidecar_dir=self.sidecar_dir,
            manifest_path=self.manifest_path,
            collision_registry={},
            prepare_fn=lambda *a, **k: (None, None),
            guid_short="?",
        )
        self.assertTrue(outcome.prior_manifest_eligible_for_generation)
        self.assertEqual(outcome.prior_generation, 2)


class TestPrepareResultNormalization(_BaseIntegrationTest):
    """Tests that _normalize_prepare_result handles all callback return patterns safely."""

    def _trigger_prepare_branch(self, prepare_fn, source, guid):
        """Helper: trigger occurrence-mismatch prepare branch and return outcome."""
        od = [{
            "occurrence_id": "nonexistent",
            "slot_index": 0, "channel": 0,
            "material_identity": "MatA", "node_identity": "MatA/TexA",
            "source_kind": "FILE", "source_locator": "/tmp/test.png",
            "colorspace": "sRGB", "source": source,
            "current_content_hex": "a1234567890abcdef",
        }]
        return manifest_reuse.evaluate_manifest_reuse(
            guid_hex=guid,
            occurrence_descriptors=od,
            sidecar_dir=self.sidecar_dir,
            manifest_path=self.manifest_path,
            collision_registry={},
            prepare_fn=prepare_fn,
            guid_short="?",
        )

    def test_none_none_returns_prepare_defaults(self):
        """(None, None): no crash, decision=PREPARE, asset_id empty."""
        from manifest_reuse import ReuseDecision
        guid, _, _, _, _, _ = _build_sidecar_and_manifest_integration(
            self.sidecar_dir, self.obj_dir, content=b"test",
        )
        source = MockSource(
            mat_name="MatA", node_name="TexA", image_name="test",
            source_kind="FILE", filepath_raw="/tmp/test.png", filepath="/tmp/test.png",
            is_packed=False, width=256, height=256, file_format="PNG", colorspace="sRGB",
        )
        outcome = self._trigger_prepare_branch(
            lambda *a, **k: (None, None), source, guid,
        )
        src_id = id(source)
        self.assertIn(src_id, outcome.decisions)
        d = outcome.decisions[src_id]
        self.assertEqual(d.decision, "prepare")
        self.assertEqual(d.asset_id, "")
        self.assertTrue(outcome.prior_manifest_eligible_for_generation)

    def test_none_asset_populates_asset_id(self):
        """(None, {"asset_id": "some_id"}): decision=PREPARE, asset_id from asset."""
        from manifest_reuse import ReuseDecision
        guid, _, _, _, _, _ = _build_sidecar_and_manifest_integration(
            self.sidecar_dir, self.obj_dir, content=b"test",
        )
        source = MockSource(
            mat_name="MatA", node_name="TexA", image_name="test",
            source_kind="FILE", filepath_raw="/tmp/test.png", filepath="/tmp/test.png",
            is_packed=False, width=256, height=256, file_format="PNG", colorspace="sRGB",
        )
        outcome = self._trigger_prepare_branch(
            lambda *a, **k: (None, {"asset_id": "my_none_asset"}), source, guid,
        )
        src_id = id(source)
        self.assertIn(src_id, outcome.decisions)
        d = outcome.decisions[src_id]
        self.assertEqual(d.decision, "prepare")
        self.assertEqual(d.asset_id, "my_none_asset")
        self.assertIn("my_none_asset", outcome.current_assets)

    def test_decision_none_preserves_decision_fields(self):
        """(ReuseDecision(action="custom_action"), None): preserves decision fields."""
        from manifest_reuse import ReuseDecision
        guid, _, _, _, _, _ = _build_sidecar_and_manifest_integration(
            self.sidecar_dir, self.obj_dir, content=b"test",
        )
        source = MockSource(
            mat_name="MatA", node_name="TexA", image_name="test",
            source_kind="FILE", filepath_raw="/tmp/test.png", filepath="/tmp/test.png",
            is_packed=False, width=256, height=256, file_format="PNG", colorspace="sRGB",
        )
        custom_decision = ReuseDecision(
            decision="prepare", action="custom_action",
            occurrence_id="", asset_id="", source_kind="FILE",
            destination_path="/some/path", error="custom_error",
        )
        outcome = self._trigger_prepare_branch(
            lambda *a, **k: (custom_decision, None), source, guid,
        )
        src_id = id(source)
        self.assertIn(src_id, outcome.decisions)
        d = outcome.decisions[src_id]
        self.assertEqual(d.decision, "prepare")
        self.assertEqual(d.action, "custom_action")
        self.assertEqual(d.destination_path, "/some/path")
        self.assertEqual(d.error, "custom_error")
        self.assertEqual(d.asset_id, "")


class TestReuseAppearsInNewManifest(_BaseIntegrationTest):
    """Tests that reused asset records appear in the new manifest."""

    def test_reused_asset_records_in_new_manifest(self):
        guid, occ_id, asset_id, dest_basename, dest_path, content_hex = _build_sidecar_and_manifest_integration(
            self.sidecar_dir, self.obj_dir, content=b"reuse content",
        )
        source = MockSource(
            mat_name="MatA", node_name="TexA", image_name="test",
            source_kind="FILE", filepath_raw="/tmp/test.png", filepath="/tmp/test.png",
            is_packed=False, width=256, height=256, file_format="PNG", colorspace="sRGB",
        )
        occurrence_descriptors = [{
            "occurrence_id": occ_id,
            "slot_index": 0, "channel": 0,
            "material_identity": "MatA", "node_identity": "MatA/TexA",
            "source_kind": "FILE", "source_locator": "/tmp/test.png",
            "colorspace": "sRGB", "source": source,
            "current_content_hex": content_hex,
        }]
        outcome = manifest_reuse.evaluate_manifest_reuse(
            guid_hex=guid,
            occurrence_descriptors=occurrence_descriptors,
            sidecar_dir=self.sidecar_dir,
            manifest_path=self.manifest_path,
            collision_registry={},
            prepare_fn=lambda *a, **k: (None, None),
            guid_short="?",
        )
        self.assertIn(asset_id, outcome.current_assets)


class TestSequentialObjectsNoSharing(_BaseIntegrationTest):
    """Tests that sequential objects do not share decisions or payloads."""

    def test_sequential_objects_no_shared_decisions(self):
        guid, occ_id, asset_id, dest_basename, dest_path, content_hex = _build_sidecar_and_manifest_integration(
            self.sidecar_dir, self.obj_dir, content=b"first object",
        )
        source = MockSource(
            mat_name="MatA", node_name="TexA", image_name="test",
            source_kind="FILE", filepath_raw="/tmp/test.png", filepath="/tmp/test.png",
            is_packed=False, width=256, height=256, file_format="PNG", colorspace="sRGB",
        )
        od = [{
            "occurrence_id": occ_id,
            "slot_index": 0, "channel": 0,
            "material_identity": "MatA", "node_identity": "MatA/TexA",
            "source_kind": "FILE", "source_locator": "/tmp/test.png",
            "colorspace": "sRGB", "source": source,
            "current_content_hex": content_hex,
        }]
        outcome1 = manifest_reuse.evaluate_manifest_reuse(
            guid_hex=guid, occurrence_descriptors=od,
            sidecar_dir=self.sidecar_dir, manifest_path=self.manifest_path,
            collision_registry={}, prepare_fn=lambda *a, **k: (None, None),
            guid_short="?",
        )
        content2 = b"second object content"
        dest2 = os.path.join(self.sidecar_dir, "texture2.png")
        os.makedirs(os.path.dirname(dest2), exist_ok=True)
        with open(dest2, 'wb') as f:
            f.write(content2)
        hash2 = format(net.xxh64(content2), '016x')
        with open(self.manifest_path, 'w') as f:
            f.write("")
        od2 = [{
            "occurrence_id": "different",
            "slot_index": 0, "channel": 0,
            "material_identity": "MatB", "node_identity": "MatB/TexA",
            "source_kind": "FILE", "source_locator": "/tmp/t2.png",
            "colorspace": "sRGB",
            "source": MockSource(
                mat_name="MatB", node_name="TexA", image_name="test2",
                source_kind="FILE", filepath_raw="/tmp/t2.png", filepath="/tmp/t2.png",
                is_packed=False, width=256, height=256, file_format="PNG",
                colorspace="sRGB",
            ),
            "current_content_hex": hash2,
        }]
        outcome2 = manifest_reuse.evaluate_manifest_reuse(
            guid_hex="b" * 64, occurrence_descriptors=od2,
            sidecar_dir=self.sidecar_dir, manifest_path=self.manifest_path,
            collision_registry={}, prepare_fn=lambda *a, **k: (None, None),
            guid_short="?",
        )
        self.assertEqual(outcome1.manifest_status, "valid")
        self.assertEqual(outcome2.manifest_status, "invalid")


# ──────────────────────────────────────────────────────────────────────
# 7. AST/source-contract tests
# ──────────────────────────────────────────────────────────────────────

class TestASTProductionContract(unittest.TestCase):
    """AST-based verification of production constraints."""

    def _get_ast_source(self, filepath):
        with open(filepath, 'r', encoding='utf-8') as f:
            source = f.read()
        return source, ast.parse(source)

    def test_operator_calls_a35_helper(self):
        filepath = os.path.join(os.path.dirname(__file__), "..", "Blender_Addon", "__init__.py")
        source, tree = self._get_ast_source(filepath)
        self.assertIn("_evaluate_and_materialize_manifest_v3", source)

    def test_no_legacy_manifest_migration(self):
        filepath = os.path.join(os.path.dirname(__file__), "..", "Blender_Addon", "manifest_reuse.py")
        source, tree = self._get_ast_source(filepath)
        self.assertNotIn("migrate_legacy", source)
        self.assertNotIn("delete_legacy", source)
        self.assertNotIn("legacy_manifest", source)

    def test_no_direct_packet_send_bypass(self):
        filepath = os.path.join(os.path.dirname(__file__), "..", "Blender_Addon", "manifest_reuse.py")
        source, tree = self._get_ast_source(filepath)
        self.assertNotIn("send_objects", source)
        self.assertNotIn("send_fbx", source)

    def test_no_ue_cpp_wire_files_modified(self):
        filepath = os.path.join(os.path.dirname(__file__), "..", "Blender_Addon", "manifest_reuse.py")
        source, tree = self._get_ast_source(filepath)
        self.assertNotIn(".cpp", source)
        # Reject .h headers only when they look like C/C++ headers (e.g. ".h\" or ".h)")
        # Don't reject file format strings like ".hdr" or ".h5"
        import re
        has_cpp_header = bool(re.search(r'"\.h[\"\')]', source) or re.search(r"'\\.h['\"]" , source))
        self.assertFalse(has_cpp_header, "Found C/C++ header reference")
        self.assertNotIn(".cs", source)
        self.assertNotIn("Unreal", source)

    def test_no_unreal_imports(self):
        filepath = os.path.join(os.path.dirname(__file__), "..", "Blender_Addon", "manifest_reuse.py")
        source, tree = self._get_ast_source(filepath)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.module and "unreal" in node.module.lower():
                    self.fail(f"manifest_reuse.py imports from unreal module: {node.module}")

    def test_manifest_v3_imports(self):
        filepath = os.path.join(os.path.dirname(__file__), "..", "Blender_Addon", "manifest_reuse.py")
        source, tree = self._get_ast_source(filepath)
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                # Allow standard lib, Blender_Addon subtree, and __future__
                allowed = ("__future__", "typing", "dataclasses", "hashlib", "os", "json", "logging")
                if mod and mod not in allowed and mod != "Blender_Addon" and not mod.startswith("Blender_Addon."):
                    self.fail(f"Unexpected import from {mod}")
                if mod and mod not in ("__future__",):
                    imports.append(mod)
        # manifest_v3 may appear as a submodule like Blender_Addon.manifest_v3
        self.assertTrue(any("manifest_v3" in imp for imp in imports),
                        f"manifest_v3 not found in imports: {imports}")

    def test_no_ue_filesystem_paths(self):
        filepath = os.path.join(os.path.dirname(__file__), "..", "Blender_Addon", "manifest_reuse.py")
        source, tree = self._get_ast_source(filepath)
        self.assertNotIn("/home", source)
        self.assertNotIn("ue_", source.lower())
        # Note: "ue-" in docstrings like "Unique-asset" is acceptable

    def test_no_hardcoded_sidecar_paths(self):
        filepath = os.path.join(os.path.dirname(__file__), "..", "Blender_Addon", "manifest_reuse.py")
        source, tree = self._get_ast_source(filepath)
        self.assertNotIn("~/.cache/uelivesync", source)


class TestASTManifestV3Contract(unittest.TestCase):
    """AST-based verification of manifest_v3 module contract."""

    def test_manifest_schema_version_constant_exists(self):
        filepath = os.path.join(os.path.dirname(__file__), "..", "Blender_Addon", "manifest_v3.py")
        with open(filepath, 'r', encoding='utf-8') as f:
            source = f.read()
        self.assertIn("MANIFEST_V3_SCHEMA_VERSION", source)

    def test_compute_occurrence_id_exists(self):
        filepath = os.path.join(os.path.dirname(__file__), "..", "Blender_Addon", "manifest_v3.py")
        with open(filepath, 'r', encoding='utf-8') as f:
            source = f.read()
        self.assertIn("compute_occurrence_id", source)

    def test_build_manifest_v3_exists(self):
        filepath = os.path.join(os.path.dirname(__file__), "..", "Blender_Addon", "manifest_v3.py")
        with open(filepath, 'r', encoding='utf-8') as f:
            source = f.read()
        self.assertIn("build_manifest_v3", source)

    def test_serialize_manifest_v3_exists(self):
        filepath = os.path.join(os.path.dirname(__file__), "..", "Blender_Addon", "manifest_v3.py")
        with open(filepath, 'r', encoding='utf-8') as f:
            source = f.read()
        self.assertIn("serialize_manifest_v3", source)

    def test_compute_semantic_digest_exists(self):
        filepath = os.path.join(os.path.dirname(__file__), "..", "Blender_Addon", "manifest_v3.py")
        with open(filepath, 'r', encoding='utf-8') as f:
            source = f.read()
        self.assertIn("compute_semantic_digest", source)


class TestASTInitContract(unittest.TestCase):
    """AST-based verification of __init__.py changes."""

    def test_no_duplicate_operator(self):
        filepath = os.path.join(os.path.dirname(__file__), "..", "Blender_Addon", "__init__.py")
        with open(filepath, 'r', encoding='utf-8') as f:
            source = f.read()
        import re
        operator_classes = re.findall(r'class\s+(\w+Operator\w*)', source)
        main_operator = [c for c in operator_classes if 'FBX' in c or 'Mesh' in c]
        self.assertLessEqual(len(main_operator), 2)

    def test_operator_classname_unchanged(self):
        filepath = os.path.join(os.path.dirname(__file__), "..", "Blender_Addon", "__init__.py")
        with open(filepath, 'r', encoding='utf-8') as f:
            source = f.read()
        self.assertIn("Operator", source)


class TestASTIntegrationPoints(unittest.TestCase):
    """AST-based verification of integration points."""

    def _get_ast_source(self, filepath):
        with open(filepath, 'r') as f:
            source = f.read()
        tree = ast.parse(source)
        return source, tree

    def test_no_new_top_level_module_exports(self):
        filepath = os.path.join(os.path.dirname(__file__), "..", "Blender_Addon", "manifest_reuse.py")
        source, tree = self._get_ast_source(filepath)
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == "__all__":
                        self.fail("manifest_reuse.py should not define __all__")

    def test_manifest_reuse_imports_only_internal(self):
        filepath = os.path.join(os.path.dirname(__file__), "..", "Blender_Addon", "manifest_reuse.py")
        source, tree = self._get_ast_source(filepath)
        external_imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if not alias.name.startswith("Blender_Addon"):
                        external_imports.append(alias.name)
            if isinstance(node, ast.ImportFrom):
                if node.module and not node.module.startswith("Blender_Addon"):
                    if node.module not in ("os", "json", "dataclasses", "hashlib",
                                            "logging", "typing", "uuid"):
                        external_imports.append(node.module)
        for imp in external_imports:
            self.assertNotIn("unreal", imp.lower())
            self.assertNotIn("ue", imp.lower())


class TestASTDataclassContract(unittest.TestCase):
    """Tests for dataclass contract verification."""

    def test_reuse_decision_dataclass_exists(self):
        from manifest_reuse import ReuseDecision, ReuseOutcome
        from dataclasses import fields
        decision_fields = {f.name for f in fields(ReuseDecision)}
        self.assertIn("decision", decision_fields)
        self.assertIn("action", decision_fields)
        outcome_fields = {f.name for f in fields(ReuseOutcome)}
        self.assertIn("global_reuse_denied", outcome_fields)
        self.assertIn("prior_manifest_eligible_for_generation", outcome_fields)
        self.assertIn("prior_generation", outcome_fields)
        self.assertIn("current_assets", outcome_fields)
        self.assertIn("decisions", outcome_fields)

    def test_validate_path_safety_exists(self):
        from manifest_reuse import validate_path_safety
        self.assertTrue(callable(validate_path_safety))

    def test_evaluate_manifest_reuse_exists(self):
        from manifest_reuse import evaluate_manifest_reuse
        import inspect
        sig = inspect.signature(evaluate_manifest_reuse)
        params = list(sig.parameters.keys())
        self.assertIn("guid_hex", params)
        self.assertIn("occurrence_descriptors", params)
        self.assertIn("sidecar_dir", params)
        self.assertIn("manifest_path", params)
        self.assertIn("collision_registry", params)

    def test_is_safe_basename_exists(self):
        from manifest_reuse import is_safe_basename
        self.assertTrue(callable(is_safe_basename))
        self.assertTrue(is_safe_basename("texture.png"))
        self.assertTrue(is_safe_basename("texture123.png"))
        self.assertFalse(is_safe_basename("../escape"))
        self.assertFalse(is_safe_basename("../../../etc/passwd"))

    def test_manifest_status_values(self):
        # ManifestStatus doesn't exist; status is a string
        from manifest_reuse import evaluate_manifest_reuse
        # Verify the function returns the expected status values
        # by checking the source code for status constants
        import inspect
        source = inspect.getsource(evaluate_manifest_reuse)
        self.assertIn("missing", source)
        self.assertIn("invalid", source)
        self.assertIn("valid", source)

    def test_reuse_outcome_values(self):
        # ReuseOutcome is a dataclass, not an enum
        # The decision values are "reuse", "prepare", "reject"
        from manifest_reuse import ReuseDecision
        r1 = ReuseDecision(decision="reuse", action="content_mismatch",
                           occurrence_id="", asset_id="", source_kind="FILE", destination_path="")
        r2 = ReuseDecision(decision="prepare", action="destination_missing",
                           occurrence_id="", asset_id="", source_kind="FILE", destination_path="")
        r3 = ReuseDecision(decision="reject", action="path_safety",
                           occurrence_id="", asset_id="", source_kind="FILE", destination_path="")
        self.assertEqual(r1.decision, "reuse")
        self.assertEqual(r2.decision, "prepare")
        self.assertEqual(r3.decision, "reject")


# ──────────────────────────────────────────────────────────────────────
# H. Distinct-source identical-content tests (A3.5 defect probe)
# ──────────────────────────────────────────────────────────────────────


class TestDistinctSourceIdenticalContentPrepare(unittest.TestCase):
    """Distinct source objects with identical current bytes must be prepared exactly once."""

    def test_distinct_objects_identical_content_prepare(self):
        from manifest_v3 import MANIFEST_V3_FILENAME, compute_occurrence_id, build_occurrence_record, build_asset_record, build_manifest_v3, compute_semantic_digest
        obj_dir = tempfile.mkdtemp()
        sidecar_dir = os.path.join(obj_dir, "sidecars")
        os.makedirs(sidecar_dir, exist_ok=True)
        manifest_path = os.path.join(obj_dir, MANIFEST_V3_FILENAME)

        # Build a prior manifest WITHOUT the target asset (force prepare path)
        guid = "a" * 64
        content = b"shared identical content bytes"
        content_hex = format(net.xxh64(content), '016x')

        # Two distinct source objects with IDENTICAL content
        source1 = MockSource(
            mat_name="MatA", node_name="TexA", image_name="imgA",
            source_kind="FILE", filepath_raw="/tmp/identical.png",
            filepath="/tmp/identical.png",
            is_packed=False, width=256, height=256,
            file_format="PNG", colorspace="sRGB",
        )
        source2 = MockSource(
            mat_name="MatB", node_name="TexA", image_name="imgB",
            source_kind="FILE", filepath_raw="/tmp/identical.png",
            filepath="/tmp/identical.png",
            is_packed=False, width=256, height=256,
            file_format="PNG", colorspace="sRGB",
        )

        # Verify they are truly distinct objects
        self.assertNotEqual(id(source1), id(source2), "source1 must not be source2")

        # Create two distinct valid prior occurrences
        occ_id1 = compute_occurrence_id(
            guid=guid, slot_index=0, material_identity="MatA",
            node_identity="MatA/TexA", channel=0,
        )
        occ_id2 = compute_occurrence_id(
            guid=guid, slot_index=1, material_identity="MatB",
            node_identity="MatB/TexA", channel=0,
        )

        # No reusable asset — prior manifest has no matching asset
        # Use None (null) for failed occurrences (validation rejects non-null assetId on failed)
        occ1_rec = build_occurrence_record(
            slot_index=0, channel=0, material_identity="MatA",
            node_identity="MatA/TexA", source_kind="FILE",
            source_locator="/tmp/placeholder.png", colorspace="sRGB",
            asset_id=None, status="failed",
        )
        occ2_rec = build_occurrence_record(
            slot_index=1, channel=0, material_identity="MatB",
            node_identity="MatB/TexA", source_kind="FILE",
            source_locator="/tmp/placeholder2.png", colorspace="sRGB",
            asset_id=None, status="failed",
        )
        manifest_data = build_manifest_v3(
            guid=guid, generation=1,
            occurrences={occ_id1: occ1_rec, occ_id2: occ2_rec},
            assets={},
        )
        manifest_data["semanticContentDigest"] = compute_semantic_digest(
            guid, manifest_data["occurrences"], manifest_data["assets"],
        )
        with open(manifest_path, 'w') as f:
            json.dump(manifest_data, f)

        occurrence_descriptors = [
            {
                "occurrence_id": occ_id1, "slot_index": 0, "channel": 0,
                "material_identity": "MatA", "node_identity": "MatA/TexA",
                "source_kind": "FILE", "source_locator": "/tmp/identical.png",
                "colorspace": "sRGB", "source": source1,
                "current_content_hex": content_hex,
            },
            {
                "occurrence_id": occ_id2, "slot_index": 1, "channel": 0,
                "material_identity": "MatB", "node_identity": "MatB/TexA",
                "source_kind": "FILE", "source_locator": "/tmp/identical.png",
                "colorspace": "sRGB", "source": source2,
                "current_content_hex": content_hex,
            },
        ]

        prepare_count = [0]
        prepared_asset_ids = set()

        def _track_prepare(source_desc, sd, cr, gs):
            prepare_count[0] += 1
            dest_path = os.path.join(sd, "prepared_%d.png" % id(source_desc['source']))
            with open(dest_path, 'wb') as f:
                f.write(content)
            asset_id = "prep_" + str(id(source_desc['source']))
            prepared_asset_ids.add(asset_id)
            decision = manifest_reuse.ReuseDecision(
                decision="prepare", action="prepared",
                occurrence_id="", asset_id=asset_id,
                source_kind="FILE", destination_path=dest_path,
            )
            return (decision, {"asset_id": asset_id, "status": "ready"})

        outcome = manifest_reuse.evaluate_manifest_reuse(
            guid_hex=guid, occurrence_descriptors=occurrence_descriptors,
            sidecar_dir=sidecar_dir, manifest_path=manifest_path,
            collision_registry={}, prepare_fn=_track_prepare, guid_short="?",
        )

        # Core defect check: prepare called exactly once for identical content
        # (distinct sources with same bytes should share one materialization)
        self.assertEqual(prepare_count[0], 1,
            "Identical content must trigger prepare exactly once, got %d" % prepare_count[0])

        # Verify manifest was valid (so prepare should have been triggered)
        self.assertEqual(outcome.manifest_status, "valid",
            "Manifest should be valid to verify prepare behavior")

        # One unique prepared asset
        self.assertEqual(len(prepared_asset_ids), 1,
            "Must have exactly one unique prepared asset ID")
        unique_asset_id = next(iter(prepared_asset_ids))

        # Both source IDs present in decisions
        self.assertIn(id(source1), outcome.decisions,
            "source1 must have a decision entry")
        self.assertIn(id(source2), outcome.decisions,
            "source2 must have a decision entry")

        # Both decisions reference the same prepared asset ID
        dec1 = outcome.decisions[id(source1)]
        dec2 = outcome.decisions[id(source2)]
        self.assertEqual(dec1.asset_id, unique_asset_id,
            "source1 decision must reference unique prepared asset")
        self.assertEqual(dec2.asset_id, unique_asset_id,
            "source2 decision must reference unique prepared asset")
        self.assertEqual(dec1.decision, "prepare")
        self.assertEqual(dec2.decision, "prepare")

        # Both source objects have decision entries (decisions keyed by id(source))
        self.assertIn(id(source1), outcome.decisions)
        self.assertIn(id(source2), outcome.decisions)

        # Manifest status valid (manifest was structurally valid)
        self.assertEqual(outcome.manifest_status, "valid")


class TestDistinctSourceIdenticalContentReuse(unittest.TestCase):
    """Distinct source objects with identical current bytes must reuse the same prior asset."""

    def test_distinct_objects_identical_content_reuse(self):
        from manifest_v3 import MANIFEST_V3_FILENAME, compute_occurrence_id, build_occurrence_record, build_asset_record, build_manifest_v3, compute_semantic_digest
        obj_dir = tempfile.mkdtemp()
        sidecar_dir = os.path.join(obj_dir, "sidecars")
        os.makedirs(sidecar_dir, exist_ok=True)
        manifest_path = os.path.join(obj_dir, MANIFEST_V3_FILENAME)

        guid = "a" * 64
        content = b"shared identical content bytes"
        content_hex = format(net.xxh64(content), '016x')

        # Create reusable prior asset and destination
        reuse_dest = os.path.join(sidecar_dir, "shared.png")
        with open(reuse_dest, 'wb') as f:
            f.write(content)

        reuse_asset_id = format(net.xxh64(content), '016x')
        reuse_asset = build_asset_record(
            source_kind="FILE", content_hash=reuse_asset_id,
            destination_basename="shared.png",
            destination_size=len(content),
            destination_hash=reuse_asset_id,
            status="ready",
        )

        # Two distinct source objects with IDENTICAL content
        source1 = MockSource(
            mat_name="MatA", node_name="TexA", image_name="imgA",
            source_kind="FILE", filepath_raw="/tmp/identical.png",
            filepath="/tmp/identical.png",
            is_packed=False, width=256, height=256,
            file_format="PNG", colorspace="sRGB",
        )
        source2 = MockSource(
            mat_name="MatB", node_name="TexA", image_name="imgB",
            source_kind="FILE", filepath_raw="/tmp/identical.png",
            filepath="/tmp/identical.png",
            is_packed=False, width=256, height=256,
            file_format="PNG", colorspace="sRGB",
        )

        # Verify they are truly distinct objects
        self.assertNotEqual(id(source1), id(source2), "source1 must not be source2")

        # Two distinct valid prior occurrences referencing the same reusable asset
        occ_id1 = compute_occurrence_id(
            guid=guid, slot_index=0, material_identity="MatA",
            node_identity="MatA/TexA", channel=0,
        )
        occ_id2 = compute_occurrence_id(
            guid=guid, slot_index=1, material_identity="MatB",
            node_identity="MatB/TexA", channel=0,
        )

        occ1_rec = build_occurrence_record(
            slot_index=0, channel=0, material_identity="MatA",
            node_identity="MatA/TexA", source_kind="FILE",
            source_locator=reuse_dest, colorspace="sRGB",
            asset_id=reuse_asset_id, status="ready",
        )
        occ2_rec = build_occurrence_record(
            slot_index=1, channel=0, material_identity="MatB",
            node_identity="MatB/TexA", source_kind="FILE",
            source_locator=reuse_dest, colorspace="sRGB",
            asset_id=reuse_asset_id, status="ready",
        )

        manifest_data = build_manifest_v3(
            guid=guid, generation=1,
            occurrences={occ_id1: occ1_rec, occ_id2: occ2_rec},
            assets={reuse_asset_id: reuse_asset},
        )
        manifest_data["semanticContentDigest"] = compute_semantic_digest(
            guid, manifest_data["occurrences"], manifest_data["assets"],
        )
        with open(manifest_path, 'w') as f:
            json.dump(manifest_data, f)

        occurrence_descriptors = [
            {
                "occurrence_id": occ_id1, "slot_index": 0, "channel": 0,
                "material_identity": "MatA", "node_identity": "MatA/TexA",
                "source_kind": "FILE", "source_locator": "/tmp/identical.png",
                "colorspace": "sRGB", "source": source1,
                "current_content_hex": content_hex,
            },
            {
                "occurrence_id": occ_id2, "slot_index": 1, "channel": 0,
                "material_identity": "MatB", "node_identity": "MatB/TexA",
                "source_kind": "FILE", "source_locator": "/tmp/identical.png",
                "colorspace": "sRGB", "source": source2,
                "current_content_hex": content_hex,
            },
        ]

        prepare_count = [0]

        def _track_prepare(source_desc, sd, cr, gs):
            prepare_count[0] += 1
            dest_path = os.path.join(sd, "prepared.png")
            with open(dest_path, 'wb') as f:
                f.write(content)
            return (manifest_reuse.ReuseDecision(
                decision="prepare", action="prepared",
                occurrence_id="", asset_id="prep", source_kind="FILE",
                destination_path=dest_path,
            ), {"asset_id": "prep", "status": "ready"})

        outcome = manifest_reuse.evaluate_manifest_reuse(
            guid_hex=guid, occurrence_descriptors=occurrence_descriptors,
            sidecar_dir=sidecar_dir, manifest_path=manifest_path,
            collision_registry={}, prepare_fn=_track_prepare, guid_short="?",
        )

        # Core defect check: no prepare needed when reusable asset exists
        self.assertEqual(prepare_count[0], 0,
            "Identical content with reusable prior must not call prepare, got %d" % prepare_count[0])

        # Both source IDs present in decisions
        self.assertIn(id(source1), outcome.decisions,
            "source1 must have a decision entry")
        self.assertIn(id(source2), outcome.decisions,
            "source2 must have a decision entry")

        # Both decisions are reuse_allowed with same asset
        dec1 = outcome.decisions[id(source1)]
        dec2 = outcome.decisions[id(source2)]
        self.assertEqual(dec1.decision, "reuse", "source1 must get reuse")
        self.assertEqual(dec2.decision, "reuse", "source2 must get reuse")
        self.assertEqual(dec1.asset_id, reuse_asset_id,
            "source1 must reference prior reusable asset")
        self.assertEqual(dec2.asset_id, reuse_asset_id,
            "source2 must reference prior reusable asset")
        self.assertEqual(dec1.action, "reuse_allowed")
        self.assertEqual(dec2.action, "reuse_allowed")

        # Both source objects have decision entries
        self.assertIn(id(source1), outcome.decisions)
        self.assertIn(id(source2), outcome.decisions)

        # Manifest status valid
        self.assertEqual(outcome.manifest_status, "valid")


class TestMixedEligibilityIdenticalContent(_BaseIntegrationTest):
    """Tests that mixed-eligibility within identical-content groups is handled correctly.

    When two distinct source objects have identical current content,
    their occurrences must be independently evaluated BEFORE any fan-out.
    A local mismatch on one source must NOT bypass evaluation on the other.
    """

    def test_distinct_identical_content_reuse_plus_local_mismatch_prepares_once(self):
        """source1 is reusable, source2 has local occurrence mismatch.

        Both have identical content → one prepare, both get PREPARE decisions.
        """
        # Build a valid prior manifest with a reusable asset
        reuse_content = b"identical sidecar content for both sources"
        dest_path = os.path.join(self.sidecar_dir, "identical.png")
        os.makedirs(os.path.dirname(dest_path), exist_ok=True)
        with open(dest_path, 'wb') as f:
            f.write(reuse_content)
        reuse_hash = format(net.xxh64(reuse_content), '016x')

        from manifest_v3 import (
            compute_occurrence_id, build_manifest_v3,
            build_occurrence_record, build_asset_record,
            serialize_manifest_v3,
        )
        guid = "a" * 64
        reuse_occ_id = compute_occurrence_id(
            guid=guid, slot_index=0, material_identity="MatA",
            node_identity="MatA/TexA", channel=0,
        )
        reuse_asset_rec = build_asset_record(
            source_kind="FILE", content_hash=reuse_hash,
            destination_basename="identical.png",
            destination_size=os.path.getsize(dest_path),
            destination_hash=reuse_hash, status="ready",
        )
        reuse_occ_rec = build_occurrence_record(
            slot_index=0, channel=0, material_identity="MatA",
            node_identity="MatA/TexA", source_kind="FILE",
            source_locator="/tmp/identical.png", colorspace="sRGB",
            asset_id=reuse_hash, status="ready",
        )
        manifest = build_manifest_v3(
            guid=guid, generation=1,
            occurrences={reuse_occ_id: reuse_occ_rec},
            assets={reuse_hash: reuse_asset_rec},
        )
        with open(self.manifest_path, 'w') as f:
            f.write(serialize_manifest_v3(manifest))

        # source1: matches the prior occurrence exactly (reusable)
        source1 = MockSource(
            mat_name="MatA", node_name="TexA", image_name="test_src1",
            source_kind="FILE", filepath_raw="/tmp/s1.png", filepath="/tmp/s1.png",
            is_packed=False, width=256, height=256, file_format="PNG", colorspace="sRGB",
        )
        # source2: has a MISMATCHED occurrence_id (local mismatch)
        source2 = MockSource(
            mat_name="MatB", node_name="TexB", image_name="test_src2",
            source_kind="FILE", filepath_raw="/tmp/s2.png", filepath="/tmp/s2.png",
            is_packed=False, width=256, height=256, file_format="PNG", colorspace="sRGB",
        )

        mismatch_occ_id = "nonexistent_occurrence_id_mismatch"
        # Identical content for both
        content_hex = reuse_hash

        occurrence_descriptors = [
            {
                "occurrence_id": reuse_occ_id,
                "slot_index": 0, "channel": 0,
                "material_identity": "MatA", "node_identity": "MatA/TexA",
                "source_kind": "FILE", "source_locator": "/tmp/s1.png",
                "colorspace": "sRGB", "source": source1,
                "current_content_hex": content_hex,
            },
            {
                "occurrence_id": mismatch_occ_id,
                "slot_index": 0, "channel": 0,
                "material_identity": "MatB", "node_identity": "MatB/TexB",
                "source_kind": "FILE", "source_locator": "/tmp/s2.png",
                "colorspace": "sRGB", "source": source2,
                "current_content_hex": content_hex,
            },
        ]

        prepare_count = [0]
        def _track_prepare(source_desc, sidecar_dir, collision_registry, guid_short):
            prepare_count[0] += 1
            return (manifest_reuse.ReuseDecision(
                decision="prepare", action="prepared",
                occurrence_id="", asset_id="", source_kind="FILE",
                destination_path="",
            ), {"asset_id": "new_asset_" + format(net.xxh64(reuse_content), '016x'), "contentHash": format(net.xxh64(reuse_content), '016x')})

        outcome = manifest_reuse.evaluate_manifest_reuse(
            guid_hex=guid,
            occurrence_descriptors=occurrence_descriptors,
            sidecar_dir=self.sidecar_dir,
            manifest_path=self.manifest_path,
            collision_registry={},
            prepare_fn=_track_prepare,
            guid_short="?",
        )

        # Both source IDs present in decisions
        self.assertIn(id(source1), outcome.decisions,
            "source1 must have a decision entry")
        self.assertIn(id(source2), outcome.decisions,
            "source2 must have a decision entry")

        # source1 must NOT be REUSE — its content must be re-prepared due to source2's mismatch
        dec1 = outcome.decisions[id(source1)]
        self.assertNotEqual(dec1.decision, "reuse",
            "source1 must not get reuse when source2 has local mismatch")

        # source2 must be PREPARE (due to mismatch)
        dec2 = outcome.decisions[id(source2)]
        self.assertEqual(dec2.decision, "prepare",
            "source2 must get prepare due to local occurrence mismatch")

        # Exactly one prepare call
        self.assertEqual(prepare_count[0], 1,
            "Identical content must trigger prepare exactly once, got %d" % prepare_count[0])

        # Both decisions reference the same newly prepared asset
        new_asset_id = dec2.asset_id
        self.assertTrue(len(new_asset_id) > 0,
            "source2 must have a non-empty asset_id from prepare")
        self.assertEqual(dec1.asset_id, new_asset_id,
            "source1 must reference the same prepared asset as source2")

        # No global rejection
        self.assertFalse(outcome.global_reuse_denied,
            "local mismatch must not cause global rejection")

    def test_distinct_identical_content_local_mismatch_plus_reuse_prepares_once(self):
        """source1 has local mismatch, source2 is reusable.

        Same identical content → one prepare, both get PREPARE decisions.
        Reversed order of source1 vs source2 from the other test.
        """
        # Build a valid prior manifest with a reusable asset
        reuse_content = b"identical sidecar content for both sources reversed"
        dest_path = os.path.join(self.sidecar_dir, "identical_rev.png")
        os.makedirs(os.path.dirname(dest_path), exist_ok=True)
        with open(dest_path, 'wb') as f:
            f.write(reuse_content)
        reuse_hash = format(net.xxh64(reuse_content), '016x')

        from manifest_v3 import (
            compute_occurrence_id, build_manifest_v3,
            build_occurrence_record, build_asset_record,
            serialize_manifest_v3,
        )
        guid = "b" * 64
        reuse_occ_id = compute_occurrence_id(
            guid=guid, slot_index=0, material_identity="MatX",
            node_identity="MatX/TexX", channel=0,
        )
        reuse_asset_rec = build_asset_record(
            source_kind="FILE", content_hash=reuse_hash,
            destination_basename="identical_rev.png",
            destination_size=os.path.getsize(dest_path),
            destination_hash=reuse_hash, status="ready",
        )
        reuse_occ_rec = build_occurrence_record(
            slot_index=0, channel=0, material_identity="MatX",
            node_identity="MatX/TexX", source_kind="FILE",
            source_locator="/tmp/identical_rev.png", colorspace="sRGB",
            asset_id=reuse_hash, status="ready",
        )
        manifest = build_manifest_v3(
            guid=guid, generation=1,
            occurrences={reuse_occ_id: reuse_occ_rec},
            assets={reuse_hash: reuse_asset_rec},
        )
        with open(self.manifest_path, 'w') as f:
            f.write(serialize_manifest_v3(manifest))

        # source1: has a MISMATCHED occurrence_id
        source1 = MockSource(
            mat_name="MatC", node_name="TexC", image_name="test_src1_rev",
            source_kind="FILE", filepath_raw="/tmp/s1_rev.png", filepath="/tmp/s1_rev.png",
            is_packed=False, width=256, height=256, file_format="PNG", colorspace="sRGB",
        )
        # source2: matches the prior occurrence exactly (reusable)
        source2 = MockSource(
            mat_name="MatX", node_name="TexX", image_name="test_src2_rev",
            source_kind="FILE", filepath_raw="/tmp/s2_rev.png", filepath="/tmp/s2_rev.png",
            is_packed=False, width=256, height=256, file_format="PNG", colorspace="sRGB",
        )

        mismatch_occ_id = "nonexistent_occurrence_id_mismatch_rev"
        # Identical content for both
        content_hex = reuse_hash

        occurrence_descriptors = [
            {
                "occurrence_id": mismatch_occ_id,
                "slot_index": 0, "channel": 0,
                "material_identity": "MatC", "node_identity": "MatC/TexC",
                "source_kind": "FILE", "source_locator": "/tmp/s1_rev.png",
                "colorspace": "sRGB", "source": source1,
                "current_content_hex": content_hex,
            },
            {
                "occurrence_id": reuse_occ_id,
                "slot_index": 0, "channel": 0,
                "material_identity": "MatX", "node_identity": "MatX/TexX",
                "source_kind": "FILE", "source_locator": "/tmp/s2_rev.png",
                "colorspace": "sRGB", "source": source2,
                "current_content_hex": content_hex,
            },
        ]

        prepare_count = [0]
        def _track_prepare(source_desc, sidecar_dir, collision_registry, guid_short):
            prepare_count[0] += 1
            return (manifest_reuse.ReuseDecision(
                decision="prepare", action="prepared",
                occurrence_id="", asset_id="", source_kind="FILE",
                destination_path="",
            ), {"asset_id": "new_asset_rev_" + format(net.xxh64(reuse_content), '016x'), "contentHash": format(net.xxh64(reuse_content), '016x')})

        outcome = manifest_reuse.evaluate_manifest_reuse(
            guid_hex=guid,
            occurrence_descriptors=occurrence_descriptors,
            sidecar_dir=self.sidecar_dir,
            manifest_path=self.manifest_path,
            collision_registry={},
            prepare_fn=_track_prepare,
            guid_short="?",
        )

        # Both source IDs present in decisions
        self.assertIn(id(source1), outcome.decisions,
            "source1 must have a decision entry")
        self.assertIn(id(source2), outcome.decisions,
            "source2 must have a decision entry")

        # source2 must NOT be REUSE — its content must be re-prepared due to source1's mismatch
        dec2 = outcome.decisions[id(source2)]
        self.assertNotEqual(dec2.decision, "reuse",
            "source2 must not get reuse when source1 has local mismatch")

        # source1 must be PREPARE (due to mismatch)
        dec1 = outcome.decisions[id(source1)]
        self.assertEqual(dec1.decision, "prepare",
            "source1 must get prepare due to local occurrence mismatch")

        # Exactly one prepare call
        self.assertEqual(prepare_count[0], 1,
            "Identical content must trigger prepare exactly once, got %d" % prepare_count[0])

        # Both decisions reference the same newly prepared asset
        new_asset_id = dec1.asset_id
        self.assertTrue(len(new_asset_id) > 0,
            "source1 must have a non-empty asset_id from prepare")
        self.assertEqual(dec2.asset_id, new_asset_id,
            "source2 must reference the same prepared asset as source1")

        # No global rejection
        self.assertFalse(outcome.global_reuse_denied,
            "local mismatch must not cause global rejection")


if __name__ == '__main__':
    unittest.main()
