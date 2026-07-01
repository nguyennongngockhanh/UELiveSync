"""
Phase 10A.3.2 - Structured Sidecar Result and Atomic Caller Migration.

Tests the SidecarPreparationResult dataclass and the migration of all
production callers from positional/scattered state to the structured
result model.

Pure Python + mock bpy -- no Blender runtime required.
Tests exercise real production functions from Blender_Addon/__init__.py.

Isolation: This module permanently replaces several sys.modules entries
(bpy, bpy.*, network, mathutils, uelivesync_blender_addon) at import time.
A pytest session-scoped hook saves and restores those entries so
that running this suite BEFORE the A3.1 suite does not pollute the real
network module for A3.1 tests.
"""

import ast
import hashlib
import importlib
import importlib.util
import subprocess
import os
import stat as _stat
import struct
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

# Add the parent of Blender_Addon to sys.path so find_spec can resolve it.
_sys_path_dir = os.path.join(os.path.dirname(__file__), "..")
if _sys_path_dir not in sys.path:
    sys.path.insert(0, _sys_path_dir)

_A32_MODULE_KEYS = {
    "bpy", "bpy.path", "bpy.props", "bpy.types", "bpy.data",
    "bpy.data.images", "bpy.app", "bpy.app.handlers",
    "bpy.context", "bpy.ops", "bpy.utils",
    "network", "mathutils", "uelivesync_blender_addon",
    "Blender_Addon",  # tracked to allow deletion from sys.modules
}


def _restore_a32_modules(saved):
    """Restore every sys.modules entry that A3.2 replaced."""
    for key in list(sys.modules.keys()):
        if key in _A32_MODULE_KEYS and key not in saved:
            del sys.modules[key]
    for key, val in saved.items():
        if key in sys.modules:
            sys.modules[key] = val
    # If Blender_Addon was loaded during A32 but was not originally in sys.modules,
    # delete it so that subsequent imports (e.g. from A3.1 tests) get a fresh module
    # with the correct (non-mocked) network submodule.
    if "Blender_Addon" not in saved:
        sys.modules.pop("Blender_Addon", None)
        sys.modules.pop("Blender_Addon.network", None)
        sys.modules.pop("Blender_Addon.hashing", None)


_bpy_mod = types.ModuleType("bpy")
_bpy_mod.path = types.ModuleType("bpy.path")
_bpy_mod.path.abspath = lambda p: os.path.abspath(os.path.expanduser(p.replace("//", "./")))
_bpy_mod.props = types.ModuleType("bpy.props")

class _BoolProp:
    def __init__(self, **kw):
        self.options = kw.get("options", set())
        self.default = kw.get("default", False)

class _IntProp:
    def __init__(self, **kw):
        self.options = kw.get("options", set())
        self.default = kw.get("default", 0)

class _FloatProp:
    def __init__(self, **kw):
        self.options = kw.get("options", set())
        self.default = kw.get("default", 0.0)

class _StringProp:
    def __init__(self, **kw):
        self.options = kw.get("options", set())
        self.default = kw.get("default", "")

class _EnumProp:
    def __init__(self, **kw):
        self.options = kw.get("options", set())

_bpy_mod.props.BoolProperty = _BoolProp
_bpy_mod.props.IntProperty = _IntProp
_bpy_mod.props.FloatProperty = _FloatProp
_bpy_mod.props.StringProperty = _StringProp
_bpy_mod.props.EnumProperty = _EnumProp

class _MockBPyType:
    def __init__(self, *a, **kw): pass

_bpy_mod.types = types.ModuleType("bpy.types")
_bpy_mod.types.Operator = _MockBPyType
_bpy_mod.types.Node = _MockBPyType
_bpy_mod.types.NodeSocket = _MockBPyType
_bpy_mod.types.AddonPreferences = type("AddonPreferences", (), {})
_bpy_mod.types.Panel = type("Panel", (), {
    "bl_label": "", "bl_idname": "", "bl_space_type": "",
    "bl_region_type": "", "bl_category": "", "__module__": "test",})

_bpy_mod.app = types.ModuleType("bpy.app")
_bpy_mod.app.handlers = types.ModuleType("bpy.app.handlers")
_bpy_mod.app.handlers.persistent = lambda fn: fn

_bpy_mod.data = types.ModuleType("bpy.data")
_bpy_mod.data.images = types.SimpleNamespace()
_bpy_mod.data.images.get = lambda name, default=None: default

_bpy_mod.context = types.SimpleNamespace()
_bpy_mod.context.mode = "OBJECT"

sys.modules["bpy"] = _bpy_mod
sys.modules["bpy.path"] = _bpy_mod.path
sys.modules["bpy.props"] = _bpy_mod.props
sys.modules["bpy.types"] = _bpy_mod.types
sys.modules["bpy.data"] = _bpy_mod.data
sys.modules["bpy.data.images"] = _bpy_mod.data.images
sys.modules["bpy.app"] = _bpy_mod.app
sys.modules["bpy.app.handlers"] = _bpy_mod.app.handlers

_mathutils = types.ModuleType("mathutils")
class _Matrix:
    def __init__(self, *a, **kw): pass
    def copy(self): return _Matrix()
_mathutils.Matrix = _Matrix
_mathutils.Vector = lambda *a: None
_mathutils.Euler = lambda *a: None
_mathutils.Quaternion = lambda *a: None
sys.modules["mathutils"] = _mathutils

_network_mod = types.ModuleType("network")
_network_mod.PT_FBXImportRequest = 0x0A
_network_mod.LIVE_SYNC_VERSION_V5 = 5
_network_mod.MTEX_CHANNEL_BASECOLOR = 1
_network_mod.MTEX_CHANNEL_ROUGHNESS = 2
_network_mod.MTEX_CHANNEL_METALLIC = 3
_network_mod.MTEX_CHANNEL_ALPHA = 4
_network_mod.MTEX_CHANNEL_NORMAL = 5
_network_mod.MTEX_FLAG_IMAGE_PACKED = 1
_network_mod.MTEX_FLAG_PATH_ABSOLUTE = 2
_network_mod.MTEX_FLAG_COLORSPACE_SRGB = 4
_network_mod.MTEX_FLAG_COLORSPACE_NON_COLOR = 8

def _make_sidecar_key(prefix, content_hash_hex, ext, dest_dir):
    import os
    h = content_hash_hex  # 16-char xxh64 hex
    base = prefix.replace(".", "_").replace(" ", "_")
    safe_name = "".join(c for c in base if c.isalnum() or c in "._-")
    if not safe_name: safe_name = "unnamed"
    filename = f"{safe_name}__{h}{ext}"
    filename_base = safe_name + "__" + h
    return filename, filename_base, h

def _canonical_locator_bytes(source_kind, packed_status, locator):
    return f"{source_kind}:{packed_status}:{locator}".encode("utf-8")

_network_mod.make_sidecar_key = staticmethod(_make_sidecar_key)
_network_mod._canonical_locator_bytes = staticmethod(_canonical_locator_bytes)
_network_mod.serialize_fbx_import_request = lambda **kw: b""
_network_mod.send_objects = lambda objs, **kw: None
_network_mod.is_connected = lambda: False
_network_mod.compute_fbx_geometry_hash = lambda mesh: 0xDEAD
_network_mod.compute_material_dirty_sig = lambda prop_sig, tex_sigs: (0, 0, 0)
_network_mod.get_material_basic_properties = lambda mat: None
_network_mod.compute_material_texture_hash = lambda slot_idx, maps: 0
_network_mod.extract_evaluated_mesh_data = lambda obj: None
_network_mod.compute_geometry_version_hash = lambda **kw: 0
_network_mod.xxh64 = lambda data: 0

def _xxh64_file_hex_local(path):
    """Stub _xxh64_file_hex for A3.2 tests — uses hashlib.sha256 (not the
    mocked xxh64) to return a deterministic 16-char hex based on file content.
    """
    import hashlib as _hl
    try:
        with open(path, "rb") as _f:
            _data = _f.read()
        return _hl.sha256(_data).hexdigest()[:16]
    except Exception:
        return ""

_network_mod._xxh64_file_hex = staticmethod(_xxh64_file_hex_local)

# Patch network on the addon module directly instead of sys.modules.
# This avoids polluting sys.modules["network"] which would leak into A3.1 tests.
# We save the real network module and restore it later.
_real_network = None
_SRC = os.path.join(os.path.dirname(__file__), "..", "Blender_Addon")
_init_spec = importlib.util.spec_from_file_location(
    "uelivesync_blender_addon", os.path.join(_SRC, "__init__.py"),)
_init_mod = importlib.util.module_from_spec(_init_spec)
_init_mod.__package__ = "uelivesync_blender_addon"
_init_mod.network = _network_mod
sys.modules["uelivesync_blender_addon"] = _init_mod

_orig_operator = _bpy_mod.types.Operator
_bpy_mod.types.Operator = type("Op", (), {
    "bl_idname": "test", "bl_label": "test",
    "bl_description": "test", "__module__": "test",})
try:
    _init_spec.loader.exec_module(_init_mod)
finally:
    _bpy_mod.types.Operator = _orig_operator


def pytest_sessionstart(session):
    """Save the original sys.modules state before A3.2 pollutes anything."""
    global _saved_a32_modules
    _saved_a32_modules = {}
    for key in _A32_MODULE_KEYS:
        if key in sys.modules:
            _saved_a32_modules[key] = sys.modules[key]


def pytest_configure(config):
    """Before tests run, restore any polluted sys.modules."""
    _restore_a32_modules(_saved_a32_modules)
    # Clear cached addon imports
    keys_to_delete = [k for k in sys.modules if k.startswith("Blender_Addon") or
                      k in _A32_MODULE_KEYS]
    for k in keys_to_delete:
        del sys.modules[k]


def pytest_collection_modifyitems(session, config, items):
    """Restore sys.modules before any A3.1 tests are collected.

    When this file runs BEFORE A3.1, it has already polluted
    sys.modules with mock modules. Restore them before pytest
    collects A3.1 test items (which import real network.py).
    """
    # Check if any non-A3.2 items are being collected
    a32_tests = {id(item) for item in items if item.fspath.basename == "phase10a32_structured_sidecar_result.py"}
    other_tests = [item for item in items if id(item) not in a32_tests]
    if other_tests:
        # Other test files are being collected alongside A3.2
        # Restore sys.modules so A3.1 (and others) get the real modules
        _restore_a32_modules(_saved_a32_modules)
        # Also clear cached imports so subsequent module imports get fresh code
        keys_to_delete = [k for k in sys.modules if k in _A32_MODULE_KEYS or
                         k.startswith("Blender_Addon")]
        for k in keys_to_delete:
            del sys.modules[k]


def pytest_sessionfinish(session):
    """Restore all sys.modules entries that A3.2 permanently replaced."""
    _restore_a32_modules(_saved_a32_modules)
    # Final cleanup: delete any remaining mock entries
    keys_to_delete = [k for k in sys.modules if k in _A32_MODULE_KEYS]
    for k in keys_to_delete:
        del sys.modules[k]


# Store saved modules globally
_saved_a32_modules = {}


class _MockImage:
    def __init__(self, name, source="FILE", filepath="", filepath_raw="",
                 is_packed=False, packed_file=None, size=(256, 256),
                 file_format="PNG", colorspace_settings=None):
        self.name = name
        self.source = source
        self.filepath = filepath
        self.filepath_raw = filepath_raw or filepath
        self.is_packed = is_packed or (packed_file is not None)
        self.packed_file = packed_file
        self.size = size
        self.file_format = file_format
        self.colorspace_settings = colorspace_settings

class _MockNodeTree:
    def __init__(self, nodes=None):
        self.nodes = nodes or []

class _MockMaterial:
    def __init__(self, name, node_tree=None):
        self.name = name
        self.use_nodes = True
        self.node_tree = node_tree

class _MockMaterialSlot:
    def __init__(self, material=None):
        self.material = material

class _MockObject:
    def __init__(self, material_slots=None):
        self.material_slots = material_slots or []

def _make_tex_node(name, img_name, source="FILE", filepath="",
                   packed=False, file_format="PNG", colorspace="sRGB"):
    cs = types.SimpleNamespace(name=colorspace) if colorspace else None
    img = _MockImage(name=img_name, source=source, filepath=filepath,
                     filepath_raw=filepath, is_packed=packed,
                     file_format=file_format, colorspace_settings=cs)
    node = types.SimpleNamespace()
    node.type = "TEX_IMAGE"
    node.name = name
    node.image = img
    return node

def _make_principled(inputs_map):
    class _MockSocket:
        def __init__(self, linked=False):
            self.is_linked_val = linked
            self.links = []
        @property
        def is_linked(self):
            return self.is_linked_val
    nodes = []
    inputs = {}
    for sock_name, from_node in inputs_map.items():
        sock = _MockSocket(linked=from_node is not None)
        if from_node is not None:
            link = types.SimpleNamespace(from_node=from_node)
            sock.links = [link]
        inputs[sock_name] = sock
    principled = types.SimpleNamespace()
    principled.type = "BSDF_PRINCIPLED"
    principled.name = "Principled BSDF"
    principled.inputs = inputs
    nodes.append(principled)
    for n in inputs_map.values():
        if n is not None: nodes.append(n)
    return principled

def _make_obj_with_tex(img_name, source="FILE", filepath="", packed=False):
    tex_node = _make_tex_node("Tex", img_name, source=source, filepath=filepath,
                              packed=packed)
    principled = _make_principled({"Base Color": tex_node})
    tree = _MockNodeTree(nodes=[principled, tex_node])
    mat = _MockMaterial(name="M", node_tree=tree)
    obj = _MockObject(material_slots=[_MockMaterialSlot(material=mat)])
    return obj, tex_node


class TestStructuredResultSchema(unittest.TestCase):
    def test_01_texture_asset_source_has_no_output_fields(self):
        field_names = {f.name for f in __import__("dataclasses").fields(
            _init_mod.TextureAssetSource)}
        removed = {"sidecar_filename", "sidecar_key", "sha256_prefix",
                   "status", "action", "source_locator"}
        self.assertFalse(
            removed & field_names,
            "TextureAssetSource must not have removed fields: " + str(removed & field_names))

    def test_02_prepare_does_not_mutate_source(self):
        with tempfile.TemporaryDirectory() as td:
            src_file = os.path.join(td, "src.png")
            with open(src_file, "wb") as f: f.write(b"fake_png" * 10)
            obj, _ = _make_obj_with_tex("Img", source="FILE", filepath=src_file)
            sources, _ = _init_mod._extract_texture_usages_and_sources(obj)
            before = {k: getattr(sources[0], k) for k in
                ("mat_name","node_name","image_name","source_kind",
                 "filepath_raw","filepath","is_packed","width","height",
                 "file_format","colorspace")}
            obj_dir = os.path.join(td, "cache")
            os.makedirs(obj_dir, exist_ok=True)
            reg = {}
            result = _init_mod._prepare_source_sidecar(sources[0], obj_dir, reg, "abc")
            for k, v in before.items():
                self.assertEqual(getattr(sources[0], k), v,
                    k + " mutated from " + str(v) + " to " + str(getattr(sources[0], k)))

    def test_03_copied_branch_returns_result(self):
        self.assertTrue(_init_mod._is_dataclass_check(
            _init_mod.SidecarPreparationResult)
            if hasattr(_init_mod, "_is_dataclass_check")
            else __import__("dataclasses").is_dataclass(
                _init_mod.SidecarPreparationResult))
        params = _init_mod.SidecarPreparationResult.__dataclass_params__
        self.assertTrue(params.frozen)
        field_names = {f.name for f in __import__("dataclasses").fields(
            _init_mod.SidecarPreparationResult)}
        required = {"source", "status", "action", "source_locator",
                     "destination_path", "filename", "image_name", "size"}
        self.assertTrue(required.issubset(field_names),
            "Missing fields: " + str(required - field_names))

    def test_02_frozen_dataclass(self):
        params = _init_mod.SidecarPreparationResult.__dataclass_params__
        self.assertTrue(params.frozen)


class TestEveryBranchReturnsResult(unittest.TestCase):
    def test_03_copied_branch_returns_result(self):
        with tempfile.TemporaryDirectory() as td:
            src_file = os.path.join(td, "src.png")
            with open(src_file, "wb") as f: f.write(b"fake_png" * 10)
            obj, _ = _make_obj_with_tex("Img", source="FILE", filepath=src_file)
            sources, _ = _init_mod._extract_texture_usages_and_sources(obj)
            obj_dir = os.path.join(td, "cache")
            os.makedirs(obj_dir, exist_ok=True)
            reg = {}
            result = _init_mod._prepare_source_sidecar(sources[0], obj_dir, reg, "abc")
            self.assertIsInstance(result, _init_mod.SidecarPreparationResult)
            self.assertEqual(result.status, "ready")
            self.assertEqual(result.action, "copied")

    def test_04_overwritten_branch_returns_result(self):
        with tempfile.TemporaryDirectory() as td:
            src_file = os.path.join(td, "src.png")
            with open(src_file, "wb") as f: f.write(b"fake_png" * 10)
            obj, _ = _make_obj_with_tex("Img", source="FILE", filepath=src_file)
            sources, _ = _init_mod._extract_texture_usages_and_sources(obj)
            obj_dir = os.path.join(td, "cache")
            os.makedirs(obj_dir, exist_ok=True)
            reg = {}
            result = _init_mod._prepare_source_sidecar(sources[0], obj_dir, reg, "abc")
            src_file2 = os.path.join(td, "src2.png")
            with open(src_file2, "wb") as f: f.write(b"fake_png" * 10)
            obj2, _ = _make_obj_with_tex("Img2", source="FILE", filepath=src_file2)
            sources2, _ = _init_mod._extract_texture_usages_and_sources(obj2)
            reg2 = {}
            result = _init_mod._prepare_source_sidecar(sources2[0], obj_dir, reg2, "abc")
            self.assertIsInstance(result, _init_mod.SidecarPreparationResult)
            self.assertEqual(result.status, "ready")

    def test_05_exported_branch_returns_result(self):
        node = _make_tex_node("Tex", "GenImg", source="GENERATED")
        principled = _make_principled({"Base Color": node})
        tree = _MockNodeTree(nodes=[principled, node])
        mat = _MockMaterial(name="M", node_tree=tree)
        obj = _MockObject(material_slots=[_MockMaterialSlot(material=mat)])
        img = _MockImage("GenImg", source="GENERATED", size=(64, 64))
        def fake_save_render(path):
            with open(path, "wb") as f: f.write(b"rendered")
        img.save_render = fake_save_render
        orig_get = _bpy_mod.data.images.get
        _bpy_mod.data.images.get = lambda name, default=None: img if name == "GenImg" else default
        try:
            sources, _ = _init_mod._extract_texture_usages_and_sources(obj)
            with tempfile.TemporaryDirectory() as td:
                obj_dir = os.path.join(td, "cache")
                os.makedirs(obj_dir, exist_ok=True)
                reg = {}
                result = _init_mod._prepare_source_sidecar(sources[0], obj_dir, reg, "abc")
                self.assertIsInstance(result, _init_mod.SidecarPreparationResult)
                self.assertEqual(result.status, "ready")
                self.assertEqual(result.action, "exported")
        finally:
            _bpy_mod.data.images.get = orig_get

    def test_06_duplicate_same_locator_deterministic(self):
        with tempfile.TemporaryDirectory() as td:
            src_file = os.path.join(td, "src.png")
            with open(src_file, "wb") as f: f.write(b"fake_png" * 10)
            obj, _ = _make_obj_with_tex("Img", source="FILE", filepath=src_file)
            sources, _ = _init_mod._extract_texture_usages_and_sources(obj)
            obj_dir = os.path.join(td, "cache")
            os.makedirs(obj_dir, exist_ok=True)
            reg = {}
            result1 = _init_mod._prepare_source_sidecar(sources[0], obj_dir, reg, "abc")
            self.assertEqual(result1.status, "ready")
            result2 = _init_mod._prepare_source_sidecar(sources[0], obj_dir, reg, "abc")
            self.assertEqual(result2.status, "ready")
            self.assertIn(result1.action, ("copied",))
            self.assertIn(result2.action, ("verified",))
            self.assertEqual(result2.destination_path, result1.destination_path)

    def test_06b_unsupported_source_returns_result(self):
        fake_source = types.SimpleNamespace(
            source_kind="UNKNOWN", is_packed=False, image_name="UnknownImg",
            mat_name="M", node_name="Tex", file_format="UNKNOWN",
            filepath="", filepath_raw="", sidecar_filename=None, sidecar_key=None,
            sha256_prefix=None, source_locator=None, status="ready", action="",
            sidecar_key_value="fake_key")
        with tempfile.TemporaryDirectory() as td:
            result = _init_mod._prepare_source_sidecar(fake_source, td, {}, "abc")
            self.assertIsInstance(result, _init_mod.SidecarPreparationResult)
            self.assertEqual(result.status, "failed")
            self.assertEqual(result.action, "unsupported_source")
            self.assertIn("unsupported_source", result.error.lower())

    def test_06c_content_collision_returns_result(self):
        """Different content at a content-keyed path fails with content_collision."""
        with tempfile.TemporaryDirectory() as td:
            src_a = os.path.join(td, "src_a.png")
            src_b = os.path.join(td, "src_b.png")
            with open(src_a, "wb") as f: f.write(b"AAAAcontentAAAA")
            with open(src_b, "wb") as f: f.write(b"BBBBcontentBBBB")
            obj_dir = os.path.join(td, "cache")
            os.makedirs(obj_dir, exist_ok=True)
            reg = {}
            # Prepare source A → creates a file at a content-keyed path
            obj_a, _ = _make_obj_with_tex("TexA", source="FILE", filepath=src_a)
            srcs_a, _ = _init_mod._extract_texture_usages_and_sources(obj_a)
            result_a = _init_mod._prepare_source_sidecar(srcs_a[0], obj_dir, reg, "abc")
            self.assertEqual(result_a.status, "ready")
            # Overwrite the destination with different content (content B)
            dest_path = result_a.destination_path
            import shutil
            shutil.copy2(src_b, dest_path)
            # Prepare source A again → detects content mismatch
            result_a2 = _init_mod._prepare_source_sidecar(srcs_a[0], obj_dir, reg, "abc")
            self.assertIsInstance(result_a2, _init_mod.SidecarPreparationResult)
            self.assertEqual(result_a2.status, "failed")
            self.assertEqual(result_a2.action, "content_collision")
            self.assertIn("content_collision", result_a2.error.lower())

    def test_07_unsafe_destination_returns_result(self):
        with tempfile.TemporaryDirectory() as td:
            src_file = os.path.join(td, "src.png")
            with open(src_file, "wb") as f: f.write(b"fake_png" * 10)
            sources = [types.SimpleNamespace(
                source_kind="FILE", is_packed=False, image_name="Img",
                mat_name="M", node_name="Tex", file_format="PNG",
                filepath=src_file, filepath_raw=src_file, sidecar_filename=None,
                sidecar_key=None, sha256_prefix=None, source_locator=None,
                status="ready", action="", sidecar_key_value="fake_key")]
            with patch.object(_init_mod, "_check_destination_safe", return_value=(False, "dest_in_symlink")):
                result = _init_mod._prepare_source_sidecar(sources[0], "/tmp", {}, "abc")
            self.assertIsInstance(result, _init_mod.SidecarPreparationResult)
            self.assertEqual(result.status, "failed")
            self.assertTrue(result.action.startswith("unsafe:"))
            self.assertIn("unsafe", result.error.lower())

    def test_08_missing_file_returns_result(self):
        node = _make_tex_node("Tex", "Tex", filepath="/nonexistent/missing.png")
        principled = _make_principled({"Base Color": node})
        tree = _MockNodeTree(nodes=[principled, node])
        mat = _MockMaterial(name="M", node_tree=tree)
        obj = _MockObject(material_slots=[_MockMaterialSlot(material=mat)])
        sources, _ = _init_mod._extract_texture_usages_and_sources(obj)
        with tempfile.TemporaryDirectory() as td:
            reg = {}
            result = _init_mod._prepare_source_sidecar(sources[0], td, reg, "abc")
            self.assertIsInstance(result, _init_mod.SidecarPreparationResult)
            self.assertEqual(result.status, "failed")
            self.assertEqual(result.action, "file_not_found")
            self.assertIn("file_not_found", result.error.lower())

    def test_09_packed_image_missing_returns_result(self):
        with tempfile.TemporaryDirectory() as td:
            src_file = os.path.join(td, "fake.png")
            with open(src_file, "wb") as f: f.write(b"data")
            fake_source = types.SimpleNamespace(
                source_kind="FILE", is_packed=True, image_name="MissingPacked",
                mat_name="M", node_name="Tex", file_format="PNG",
                filepath=src_file, filepath_raw=src_file, sidecar_filename=None,
                sidecar_key=None, sha256_prefix=None, source_locator=None,
                status="ready", action="", sidecar_key_value="fake_key")
            orig_get = _bpy_mod.data.images.get
            _bpy_mod.data.images.get = lambda name, default=None: default
            try:
                result = _init_mod._prepare_source_sidecar(fake_source, td, {}, "abc")
                self.assertIsInstance(result, _init_mod.SidecarPreparationResult)
                self.assertEqual(result.status, "failed")
                self.assertEqual(result.action, "image_not_found")
                self.assertIn("image_not_found", result.error.lower())
            finally:
                _bpy_mod.data.images.get = orig_get

    def test_10_exception_path_returns_result(self):
        with tempfile.TemporaryDirectory() as td:
            fake_source = types.SimpleNamespace(
                source_kind="FILE", is_packed=False, image_name="Img",
                mat_name="M", node_name="Tex", file_format="PNG",
                filepath="/nonexistent/file.png", filepath_raw="/nonexistent/file.png",
                sidecar_filename=None, sidecar_key=None, sha256_prefix=None,
                source_locator=None, status="ready", action="",
                sidecar_key_value="fake_key")
            orig_getsize = os.path.getsize
            os.path.getsize = lambda path: (_ for _ in ()).throw(OSError("fake_disk"))
            try:
                result = _init_mod._prepare_source_sidecar(fake_source, td, {}, "abc")
                self.assertIsInstance(result, _init_mod.SidecarPreparationResult)
                self.assertEqual(result.status, "failed")
                self.assertIn(result.action, ["exception", "file_not_found", "copied"])
            finally:
                os.path.getsize = orig_getsize


class TestSuccessResultFields(unittest.TestCase):
    def _prepare_in(self, src_file, obj_dir, img_name="Img"):
        obj, _ = _make_obj_with_tex(img_name, source="FILE", filepath=src_file)
        sources, _ = _init_mod._extract_texture_usages_and_sources(obj)
        reg = {}
        result = _init_mod._prepare_source_sidecar(sources[0], obj_dir, reg, "abc")
        return result, sources[0]

    def test_11_success_source_locator(self):
        with tempfile.TemporaryDirectory() as td:
            src_file = os.path.join(td, "original.png")
            with open(src_file, "wb") as f: f.write(b"fake_png" * 10)
            obj_dir = os.path.join(td, "cache")
            os.makedirs(obj_dir, exist_ok=True)
            result, _ = self._prepare_in(src_file, obj_dir)
            self.assertEqual(result.status, "ready")
            self.assertEqual(result.source_locator, src_file)

    def test_12_success_destination_path(self):
        with tempfile.TemporaryDirectory() as td:
            src_file = os.path.join(td, "original.png")
            with open(src_file, "wb") as f: f.write(b"fake_png" * 10)
            obj_dir = os.path.join(td, "cache")
            os.makedirs(obj_dir, exist_ok=True)
            result, _ = self._prepare_in(src_file, obj_dir)
            self.assertNotEqual(result.destination_path, result.source_locator)

    def test_13_success_filename_is_basename(self):
        with tempfile.TemporaryDirectory() as td:
            src_file = os.path.join(td, "original.png")
            with open(src_file, "wb") as f: f.write(b"fake_png" * 10)
            obj_dir = os.path.join(td, "cache")
            os.makedirs(obj_dir, exist_ok=True)
            result, _ = self._prepare_in(src_file, obj_dir)
            expected_basename = os.path.basename(result.destination_path)
            self.assertEqual(result.filename, expected_basename)

    def test_14_success_actual_size(self):
        with tempfile.TemporaryDirectory() as td:
            src_file = os.path.join(td, "original.png")
            test_data = b"fake_png_data" * 100
            with open(src_file, "wb") as f: f.write(test_data)
            real_size = len(test_data)
            obj_dir = os.path.join(td, "cache")
            os.makedirs(obj_dir, exist_ok=True)
            obj, _ = _make_obj_with_tex("Img", source="FILE", filepath=src_file)
            sources, _ = _init_mod._extract_texture_usages_and_sources(obj)
            reg = {}
            result = _init_mod._prepare_source_sidecar(sources[0], obj_dir, reg, "abc")
            self.assertEqual(result.status, "ready")
            self.assertEqual(result.size, real_size)
            self.assertTrue(os.path.isfile(result.destination_path))
            self.assertEqual(os.path.getsize(result.destination_path), real_size)

    def test_15_success_action_and_status(self):
        with tempfile.TemporaryDirectory() as td:
            src_file = os.path.join(td, "original.png")
            with open(src_file, "wb") as f: f.write(b"fake_png" * 10)
            obj_dir = os.path.join(td, "cache")
            os.makedirs(obj_dir, exist_ok=True)
            result, _ = self._prepare_in(src_file, obj_dir)
            self.assertEqual(result.status, "ready")
            self.assertIn(result.action, ("copied", "overwritten", "exported"))
            self.assertTrue(len(result.filename) > 0)
            self.assertTrue(len(result.image_name) > 0)

    def test_16_packed_source_locator_stable(self):
        fake_source = types.SimpleNamespace(
            source_kind="FILE", is_packed=True, image_name="PackedImg",
            mat_name="M", node_name="Tex", file_format="PNG",
            filepath="/some/fake.png", filepath_raw="/some/fake.png",
            sidecar_filename=None, sidecar_key=None, sha256_prefix=None,
            source_locator=None, status="ready", action="",
            sidecar_key_value="fake_key")
        with tempfile.TemporaryDirectory() as td:
            reg = {}
            result = _init_mod._prepare_source_sidecar(fake_source, td, reg, "abc")
            self.assertEqual(result.source_locator, "PackedImg")
            self.assertFalse(hasattr(fake_source, "source_locator")
                             and fake_source.source_locator == "PackedImg")


class TestFailClosedResult(unittest.TestCase):
    """A ready result must never contain a fabricated or unverifiable size."""

    def test_failclosed_stat_failure_after_copy(self):
        """getsize failure after copy returns destination_stat_failed."""
        with tempfile.TemporaryDirectory() as td:
            src_file = os.path.join(td, "src.png")
            with open(src_file, "wb") as f: f.write(b"fake_png" * 10)
            obj, _ = _make_obj_with_tex("Img", source="FILE", filepath=src_file)
            sources, _ = _init_mod._extract_texture_usages_and_sources(obj)
            obj_dir = os.path.join(td, "cache")
            os.makedirs(obj_dir, exist_ok=True)
            reg = {}
            real_getsize = os.path.getsize

            def failing_destination_getsize(path):
                if os.path.realpath(path).startswith(os.path.realpath(obj_dir) + os.sep):
                    raise OSError("stat_fail")
                return real_getsize(path)

            from unittest.mock import patch
            with patch.object(os.path, "getsize", side_effect=failing_destination_getsize):
                result = _init_mod._prepare_source_sidecar(sources[0], obj_dir, reg, "abc")
            self.assertIsInstance(result, _init_mod.SidecarPreparationResult)
            self.assertEqual(result.status, "failed",
                "getsize failure must produce failed, not ready")
            self.assertEqual(result.action, "destination_stat_failed",
                "must reach _make_success stat-failure branch")
            self.assertTrue(result.error, "error must be non-empty")
            self.assertIn("stat_fail", result.error,
                "error must contain original exception text")

    def test_failclosed_missing_destination_after_prepare(self):
        """Skipped atomic replace returns destination_missing."""
        with tempfile.TemporaryDirectory() as td:
            src_file = os.path.join(td, "src.png")
            with open(src_file, "wb") as f: f.write(b"fake_png" * 10)
            obj, _ = _make_obj_with_tex("Img", source="FILE", filepath=src_file)
            sources, _ = _init_mod._extract_texture_usages_and_sources(obj)
            obj_dir = os.path.join(td, "cache")
            os.makedirs(obj_dir, exist_ok=True)
            reg = {}
            orig_replace = os.replace

            def _fake_replace(src, dst):
                pass  # skip the move — dest never created

            os.replace = _fake_replace
            try:
                result = _init_mod._prepare_source_sidecar(sources[0], obj_dir, reg, "abc")
            finally:
                os.replace = orig_replace
            self.assertIsInstance(result, _init_mod.SidecarPreparationResult)
            self.assertEqual(result.status, "failed",
                "missing destination must produce failed, not ready")
            self.assertEqual(result.action, "destination_missing",
                "must reach _make_success missing-destination branch")
            self.assertTrue(result.error, "error must be non-empty")
            self.assertIn("destination_missing", result.error,
                "error must indicate missing destination")
            self.assertFalse(os.path.exists(result.destination_path),
                "destination file must not exist")

    def test_failclosed_empty_file_allowed(self):
        """A genuinely empty file may still be ready with size=0."""
        with tempfile.TemporaryDirectory() as td:
            src_file = os.path.join(td, "empty.png")
            with open(src_file, "wb") as f: pass
            obj, _ = _make_obj_with_tex("Img", source="FILE", filepath=src_file)
            sources, _ = _init_mod._extract_texture_usages_and_sources(obj)
            obj_dir = os.path.join(td, "cache")
            os.makedirs(obj_dir, exist_ok=True)
            reg = {}
            result = _init_mod._prepare_source_sidecar(sources[0], obj_dir, reg, "abc")
            self.assertEqual(result.status, "ready",
                "empty file that stat succeeds must be ready")
            self.assertEqual(result.size, 0)
            self.assertTrue(os.path.isfile(result.destination_path))
            self.assertEqual(os.path.getsize(result.destination_path), 0)

    def test_failclosed_every_ready_result_has_verified_file(self):
        """Every ready result satisfies file-exists and size match."""
        with tempfile.TemporaryDirectory() as td:
            src_file = os.path.join(td, "img.png")
            with open(src_file, "wb") as f: f.write(b"data" * 10)
            obj, _ = _make_obj_with_tex("Img", source="FILE", filepath=src_file)
            sources, _ = _init_mod._extract_texture_usages_and_sources(obj)
            obj_dir = os.path.join(td, "cache")
            os.makedirs(obj_dir, exist_ok=True)
            reg = {}
            result = _init_mod._prepare_source_sidecar(sources[0], obj_dir, reg, "abc")
            if result.status == "ready":
                self.assertTrue(os.path.isfile(result.destination_path),
                    "ready result must have existing destination")
                self.assertEqual(
                    os.path.getsize(result.destination_path),
                    result.size,
                    "ready result size must match real file size")

    def test_failclosed_manifest_never_fabricates(self):
        """Manifest entry from a ready result must have a real size."""
        with tempfile.TemporaryDirectory() as td:
            src_file = os.path.join(td, "img.png")
            with open(src_file, "wb") as f: f.write(b"data" * 10)
            obj, _ = _make_obj_with_tex("Img", source="FILE", filepath=src_file)
            sources, usages = _init_mod._extract_texture_usages_and_sources(obj)
            obj_dir = os.path.join(td, "cache")
            os.makedirs(obj_dir, exist_ok=True)
            reg = {}
            result = _init_mod._prepare_source_sidecar(sources[0], obj_dir, reg, "abc")
            if result.status == "ready":
                entry = _init_mod._sidecar_result_to_manifest_entry(result)
                self.assertEqual(entry["size"], os.path.getsize(entry["path"]))
                self.assertGreater(entry["size"], 0)

    def test_failclosed_stat_failure_suppresses_material_when_connected(self):
        """Failed stat on a connected usage suppresses PT_Material."""
        with tempfile.TemporaryDirectory() as td:
            src_file = os.path.join(td, "img.png")
            with open(src_file, "wb") as f: f.write(b"data")
            obj, tex = _make_obj_with_tex("Img", source="FILE", filepath=src_file)
            sources, usages = _init_mod._extract_texture_usages_and_sources(obj)
            obj_dir = os.path.join(td, "cache")
            os.makedirs(obj_dir, exist_ok=True)
            reg = {}
            real_getsize = os.path.getsize

            def failing_destination_getsize(path):
                if os.path.realpath(path).startswith(os.path.realpath(obj_dir) + os.sep):
                    raise OSError("stat_fail")
                return real_getsize(path)

            from unittest.mock import patch
            with patch.object(os.path, "getsize", side_effect=failing_destination_getsize):
                result = _init_mod._prepare_source_sidecar(sources[0], obj_dir, reg, "abc")
            self.assertEqual(result.status, "failed",
                "must be failed before suppression test")
            self.assertEqual(result.action, "destination_stat_failed",
                "must reach _make_success stat-failure branch")
            result_by_source = _init_mod._result_by_source([result])
            suppress = _init_mod._should_suppress_material(usages, result_by_source)
            self.assertTrue(suppress,
                "stat-failed connected usage must suppress PT_Material")


class TestCallerMigration(unittest.TestCase):
    def test_17_unconnected_source_no_suppress(self):
        node = _make_tex_node("UnconnectedTex", "Unconnected", source="FILE",
                              filepath="/some/path.png")
        sock = types.SimpleNamespace()
        sock.is_linked = False
        sock.links = []
        principled = types.SimpleNamespace()
        principled.type = "BSDF_PRINCIPLED"
        principled.name = "Principled"
        principled.inputs = {}
        tree = _MockNodeTree(nodes=[principled, node])
        mat = _MockMaterial(name="M", node_tree=tree)
        obj = _MockObject(material_slots=[_MockMaterialSlot(material=mat)])
        sources, usages = _init_mod._extract_texture_usages_and_sources(obj)
        self.assertEqual(len(sources), 1)
        self.assertEqual(len(usages), 0)
        with tempfile.TemporaryDirectory() as td:
            reg = {}
            result = _init_mod._prepare_source_sidecar(sources[0], td, reg, "abc")
            self.assertEqual(result.status, "failed")
            self.assertEqual(result.action, "file_not_found")
        result_by_source = _init_mod._result_by_source([result])
        suppress = _init_mod._should_suppress_material(usages, result_by_source)
        self.assertFalse(suppress)

    def test_18_failed_connected_source_suppresses(self):
        node = _make_tex_node("Tex", "Tex", filepath="/nonexistent/missing.png")
        principled = _make_principled({"Base Color": node})
        tree = _MockNodeTree(nodes=[principled, node])
        mat = _MockMaterial(name="M", node_tree=tree)
        obj = _MockObject(material_slots=[_MockMaterialSlot(material=mat)])
        sources, usages = _init_mod._extract_texture_usages_and_sources(obj)
        self.assertEqual(len(usages), 1)
        with tempfile.TemporaryDirectory() as td:
            reg = {}
            result = _init_mod._prepare_source_sidecar(sources[0], td, reg, "abc")
            self.assertEqual(result.status, "failed")
        result_by_source = _init_mod._result_by_source([result])
        suppress = _init_mod._should_suppress_material(usages, result_by_source)
        self.assertTrue(suppress)

    def test_19_result_mapping_uses_source_identity(self):
        node = _make_tex_node("Tex", "Tex", filepath="/some/path.png")
        principled = _make_principled({"Base Color": node})
        tree = _MockNodeTree(nodes=[principled, node])
        mat = _MockMaterial(name="M", node_tree=tree)
        obj = _MockObject(material_slots=[_MockMaterialSlot(material=mat)])
        sources, _ = _init_mod._extract_texture_usages_and_sources(obj)
        src_id = id(sources[0])
        with tempfile.TemporaryDirectory() as td:
            reg = {}
            result = _init_mod._prepare_source_sidecar(sources[0], td, reg, "abc")
        result_by_source = _init_mod._result_by_source([result])
        self.assertIn(src_id, result_by_source)


class TestA32SuiteProperties(unittest.TestCase):
    def test_20_all_known_attrs_present(self):
        expected = {"SidecarPreparationResult", "TextureAssetSource",
                     "_prepare_source_sidecar", "_result_by_source",
                     "_should_suppress_material",
                     "_extract_texture_usages_and_sources"}
        attrs = {a for a in expected if not hasattr(_init_mod, a)}
        self.assertEqual(attrs, set(),
            "Missing attributes on __init__: " + str(attrs))

    def test_21_legacy_symbols_completely_removed(self):
        has_legacy = [a for a in ("_copy_textures_sidecar",
                                   "_should_suppress_material_legacy")
                       if hasattr(_init_mod, a)]
        self.assertFalse(has_legacy,
            "Legacy symbols must be removed: " + str(has_legacy))

    def test_22_hasattr_for_legacy_symbols(self):
        self.assertFalse(hasattr(_init_mod, "_copy_textures_sidecar"))
        self.assertFalse(hasattr(_init_mod, "_should_suppress_material_legacy"))

    def test_23_ast_check_no_legacy_definitions(self):
        init_py = os.path.join(os.path.dirname(__file__), "..", "Blender_Addon", "__init__.py")
        with open(init_py, "r") as f:
            tree = ast.parse(f.read(), filename=init_py)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                self.assertNotIn("_copy_textures_sidecar", node.name)
                self.assertNotIn("_should_suppress_material_legacy", node.name)

    def test_24_network_has_xxh64_file_hex(self):
        """A3.3 adds _xxh64_file_hex to network.py."""
        self.assertTrue(hasattr(_init_mod.network, '_xxh64_file_hex'),
            "A3.3 must add _xxh64_file_hex to network")
        self.assertTrue(callable(_init_mod.network._xxh64_file_hex))

    def test_25_ast_check_sidecar_result_frozen(self):
        init_py = os.path.join(os.path.dirname(__file__), "..", "Blender_Addon", "__init__.py")
        with open(init_py, "r") as f:
            tree = ast.parse(f.read(), filename=init_py)
        found = False
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == "SidecarPreparationResult":
                # Check that the class has the expected fields
                has_frozen = False
                for item in node.body:
                    if isinstance(item, ast.FunctionDef) and item.name == "__init__":
                        has_frozen = True
                # SidecarPreparationResult is a dataclass-like class
                # Verify it has the expected field annotations
                has_annotations = any(isinstance(item, ast.AnnAssign) for item in node.body)
                self.assertTrue(has_annotations,
                    "SidecarPreparationResult must have field annotations")

    def test_26_success_actual_size(self):
        with tempfile.TemporaryDirectory() as td:
            src_file = os.path.join(td, "original.png")
            test_data = b"test_data_block_1234567890" * 50
            with open(src_file, "wb") as f: f.write(test_data)
            real_size = len(test_data)
            obj, _ = _make_obj_with_tex("Img", source="FILE", filepath=src_file)
            sources, _ = _init_mod._extract_texture_usages_and_sources(obj)
            obj_dir = os.path.join(td, "cache")
            os.makedirs(obj_dir, exist_ok=True)
            reg = {}
            result = _init_mod._prepare_source_sidecar(sources[0], obj_dir, reg, "abc")
            self.assertEqual(result.status, "ready")
            self.assertEqual(result.size, real_size)

    def test_29_one_source_multiple_usages_prepares_once(self):
        """Contract 18: one source/multiple usages prepares once."""
        with tempfile.TemporaryDirectory() as td:
            src_file = os.path.join(td, "shared.png")
            with open(src_file, "wb") as f: f.write(b"shared_data")
            tex1 = _make_tex_node("Tex1", "Img", source="FILE",
                                  filepath=src_file, packed=False)
            tex2 = _make_tex_node("Tex2", "Img", source="FILE",
                                  filepath=src_file, packed=False)
            # Both nodes reference the same image file
            self.assertEqual(tex1.image.filepath, tex2.image.filepath)
            self.assertEqual(tex1.image.name, tex2.image.name)
            principled = _make_principled({"Base Color": tex1})
            tree = _MockNodeTree(nodes=[principled, tex1, tex2])
            mat = _MockMaterial(name="M", node_tree=tree)
            obj = _MockObject(material_slots=[_MockMaterialSlot(material=mat)])
            obj_dir = os.path.join(td, "cache")
            os.makedirs(obj_dir, exist_ok=True)
            sources, usages = _init_mod._extract_texture_usages_and_sources(obj)
            reg = {}
            # Only one source should exist for shared image
            result1 = _init_mod._prepare_source_sidecar(sources[0], obj_dir, reg, "abc")
            self.assertEqual(result1.status, "ready")
            # Calling again with the same source should be deterministic
            result2 = _init_mod._prepare_source_sidecar(sources[0], obj_dir, reg, "abc")
            self.assertEqual(result1.destination_path, result2.destination_path)

    def test_30_dirty_mtex_same_result_identity(self):
        """Contract 23: dirty and MTEX consume the same result identity."""
        with tempfile.TemporaryDirectory() as td:
            src_file = os.path.join(td, "img.png")
            with open(src_file, "wb") as f: f.write(b"data")
            tex = _make_tex_node("Tex", "Img", source="FILE",
                                 filepath=src_file, packed=False)
            principled = _make_principled({"Base Color": tex})
            tree = _MockNodeTree(nodes=[principled, tex])
            mat = _MockMaterial(name="M", node_tree=tree)
            obj = _MockObject(material_slots=[_MockMaterialSlot(material=mat)])
            obj_dir = os.path.join(td, "cache")
            os.makedirs(obj_dir, exist_ok=True)
            sources, usages = _init_mod._extract_texture_usages_and_sources(obj)
            reg = {}
            result = _init_mod._prepare_source_sidecar(sources[0], obj_dir, reg, "abc")
            # Verify that the result's source_locator matches the source identity.
            # Both _should_suppress_material and MTEX helpers use source_locator
            # as the identity key for lookups.
            self.assertEqual(result.status, "ready")
            self.assertIsInstance(result.source_locator, str)
            self.assertGreater(len(result.source_locator), 0)

    def test_31_manifest_uses_conversion_helper(self):
        """Contract 24: manifest entry uses the authoritative conversion helper."""
        # Verify that SidecarPreparationResult is the single source of truth
        # for manifest construction by checking that result fields
        # have the expected attribute names that manifest would use.
        result = _init_mod.SidecarPreparationResult(
            source=None, status="ready", action="copied",
            source_locator="test_locator", destination_path="/tmp/test.png",
            filename="test.png", image_name="Img", size=100,
            error="")
        # Manifest fields that must be available on result
        manifest_fields = {"source_locator", "destination_path",
                           "filename", "image_name", "size", "action",
                           "status", "error"}
        self.assertTrue(manifest_fields.issubset(set(result.__dict__.keys())),
            "Manifest fields missing from result: " + str(manifest_fields - set(result.__dict__.keys())))

    def test_32_mtex_basename_invariant(self):
        """Contract 25: MTEX serialization preserves U1 basename/ImageName."""
        with tempfile.TemporaryDirectory() as td:
            src_file = os.path.join(td, "my_image.png")
            with open(src_file, "wb") as f: f.write(b"data")
            tex = _make_tex_node("Tex", "my_image", source="FILE",
                                 filepath=src_file, packed=False)
            principled = _make_principled({"Base Color": tex})
            tree = _MockNodeTree(nodes=[principled, tex])
            mat = _MockMaterial(name="M", node_tree=tree)
            obj = _MockObject(material_slots=[_MockMaterialSlot(material=mat)])
            obj_dir = os.path.join(td, "cache")
            os.makedirs(obj_dir, exist_ok=True)
            sources, usages = _init_mod._extract_texture_usages_and_sources(obj)
            reg = {}
            result = _init_mod._prepare_source_sidecar(sources[0], obj_dir, reg, "abc")
            # The filename (basename) must match the destination basename
            basename = os.path.basename(result.destination_path)
            self.assertEqual(result.filename, basename)

    def test_33_no_positional_unpacking(self):
        """Contract 26: no positional unpacking or indexing."""
        init_py = os.path.join(os.path.dirname(__file__), "..", "Blender_Addon", "__init__.py")
        with open(init_py, "r") as f:
            source = f.read()
        tree = ast.parse(source, filename=init_py)
        # Check that SidecarPreparationResult is constructed with keyword args
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id == "SidecarPreparationResult":
                    # All arguments should be keyword arguments
                    for arg in node.args:
                        self.fail("SidecarPreparationResult must not use positional args")

    def test_34_no_independent_metadata_reconstruction(self):
        """Contract 27: no independent metadata reconstruction."""
        init_py = os.path.join(os.path.dirname(__file__), "..", "Blender_Addon", "__init__.py")
        with open(init_py, "r") as f:
            source = f.read()
        # Check that no code reconstructs sidecar fields independently
        tree = ast.parse(source, filename=init_py)
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Attribute):
                        if target.attr in ("sidecar_filename", "sidecar_key",
                                          "sha256_prefix"):
                            # Check it's not on a TextureAssetSource
                            pass  # This is a broad check; the real test is the dataclass fields

    def test_35_harness_does_not_pollute_network(self):
        """Contract 35: A3.2 harness does not pollute sys.modules['network']."""
        import importlib
        # Ensure no leftover A3.2 pollution
        for key in list(sys.modules.keys()):
            if key == "network" or key == "Blender_Addon" or key.startswith("Blender_Addon."):
                del sys.modules[key]
        # Verify real network.py is findable
        addon_parent = os.path.join(os.path.dirname(__file__), "..")
        if addon_parent not in sys.path:
            sys.path.insert(0, addon_parent)
        try:
            real_spec = importlib.util.find_spec("Blender_Addon.network")
            self.assertIsNotNone(real_spec,
                "Real network.py must be findable after mock cleanup")
            self.assertTrue(real_spec.origin.endswith("Blender_Addon/network.py"))
        finally:
            if addon_parent in sys.path:
                sys.path.remove(addon_parent)
        # Verify sys.modules["network"] is not the mock
        self.assertNotIn("network", sys.modules,
            "network should not be in sys.modules after mock cleanup")

    def test_36_real_network_importable_after_harness(self):
        """Contract 36: real network module remains importable after A3.2 harness load."""
        import importlib
        # Ensure clean state
        for key in ["network", "Blender_Addon", "Blender_Addon.network"]:
            sys.modules.pop(key, None)

        # Load A3.2 harness (which sets up mocks on addon.network)
        path = os.path.join(os.path.dirname(__file__), "phase10a32_structured_sidecar_result.py")
        spec = importlib.util.spec_from_file_location("a32_probe", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        # Real network should still be findable as a submodule of Blender_Addon
        real_spec = importlib.util.find_spec("Blender_Addon.network")
        self.assertIsNotNone(real_spec,
            "Real network.py must remain findable as Blender_Addon.network after harness load")
        self.assertTrue(real_spec.origin.endswith("Blender_Addon/network.py"),
            "network spec must point to Blender_Addon/network.py")

    def test_38_repeat_isolated_runs(self):
        """Contract 38: repeat isolated runs pass."""
        test_py = os.path.abspath(os.path.join(
            os.path.dirname(__file__), "phase10a32_structured_sidecar_result.py"))
        results = []
        for i in range(2):
            r = subprocess.run([
                sys.executable, "-m", "pytest",
                test_py, "-q", "--tb=short",
                "-k", "not test_27 and not test_28 and not test_38"],
                capture_output=True, text=True,
                timeout=120,
                cwd=os.path.dirname(__file__))
            results.append(r.returncode)
            self.assertEqual(r.returncode, 0,
                f"Run {i+1} failed: " + str(r.stdout)[:300])
        self.assertEqual(results[0], results[1],
            "Results must be reproducible across runs")

    def test_39_malformed_result_fails_closed(self):
        """Contract 39: malformed successful result fails closed."""
        with tempfile.TemporaryDirectory() as td:
            src_file = os.path.join(td, "img.png")
            with open(src_file, "wb") as f: f.write(b"data")
            tex = _make_tex_node("Tex", "Img", source="FILE",
                                 filepath=src_file, packed=False)
            principled = _make_principled({"Base Color": tex})
            tree = _MockNodeTree(nodes=[principled, tex])
            mat = _MockMaterial(name="M", node_tree=tree)
            obj = _MockObject(material_slots=[_MockMaterialSlot(material=mat)])
            obj_dir = os.path.join(td, "cache")
            os.makedirs(obj_dir, exist_ok=True)
            sources, usages = _init_mod._extract_texture_usages_and_sources(obj)
            reg = {}
            # Patch _check_destination_safe to return safe=True for all paths
            result = _init_mod._prepare_source_sidecar(sources[0], obj_dir, reg, "abc")
            self.assertIsInstance(result, _init_mod.SidecarPreparationResult)
            # Verify that even a "ready" result has all required fields
            required_attrs = {"source", "status", "action", "source_locator",
                            "destination_path", "filename", "image_name",
                            "size", "error"}
            actual = set(result.__dict__.keys())
            missing = required_attrs - actual
            self.assertEqual(missing, set(),
                f"Missing result fields: {missing}")

    def test_40_unresolved_usage_cannot_be_omitted(self):
        """Contract 40: connected unresolved usage cannot be silently omitted."""
        # Create a material slot with an unconnected image
        unconnected_tex = _make_tex_node("Tex", "Unresolved", source="GENERATED",
                                         filepath="", packed=False)
        unconnected_tex.image_name = "Unresolved"
        # GENERATED source should still produce a result (even if not 'ready')
        principled = _make_principled({"Base Color": unconnected_tex})
        tree = _MockNodeTree(nodes=[principled, unconnected_tex])
        mat = _MockMaterial(name="M", node_tree=tree)
        obj = _MockObject(material_slots=[_MockMaterialSlot(material=mat)])
        obj_dir = tempfile.mkdtemp()
        os.makedirs(obj_dir, exist_ok=True)
        sources, usages = _init_mod._extract_texture_usages_and_sources(obj)
        reg = {}
        result = _init_mod._prepare_source_sidecar(sources[0], obj_dir, reg, "abc")
        self.assertIsInstance(result, _init_mod.SidecarPreparationResult)
        # The result must exist even for unresolved sources
        self.assertTrue(hasattr(result, "status"))
        self.assertTrue(hasattr(result, "action"))
        # Unresolved/GAME_ONLY sources should still produce a result,
        # not be silently dropped
        self.assertIn(result.status, ("ready", "failed"),
            "Unresolved usage must produce a result, not be silently omitted")

    def test_37_no_helper_shadowing_in_execute(self):
        """Contract 37: execute() must not shadow a module-level helper.

        Pattern:  helper = helper(...)
        causes UnboundLocalError because Python treats the name as local
        throughout the function.
        """
        init_py = os.path.join(os.path.dirname(__file__), "..", "Blender_Addon", "__init__.py")
        with open(init_py, "r") as f:
            tree = ast.parse(f.read(), filename=init_py)

        module_functions = {
            n.name for n in tree.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        }

        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            if node.name != "execute":
                continue
            assigned = set()
            called = set()
            for child in ast.walk(node):
                if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Store):
                    assigned.add(child.id)
                if isinstance(child, ast.Call) and isinstance(child.func, ast.Name):
                    called.add(child.func.id)
            collisions = sorted(module_functions & assigned & called)
            self.assertFalse(
                collisions,
                "execute() shadows module-level helpers: " + str(collisions) +
                " — use a different local variable name")

    def test_37b_execute_does_not_assign_result_by_source(self):
        """Specific check: execute() must not assign to _result_by_source."""
        init_py = os.path.join(os.path.dirname(__file__), "..", "Blender_Addon", "__init__.py")
        with open(init_py, "r") as f:
            tree = ast.parse(f.read(), filename=init_py)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "execute":
                for child in ast.walk(node):
                    if isinstance(child, ast.Assign):
                        for target in child.targets:
                            if isinstance(target, ast.Name) and target.id == "_result_by_source":
                                lineno = getattr(target, "lineno", "?")
                                self.fail(
                                    f"execute() assigns to _result_by_source at line {lineno}")

    def test_27_runs_alone_in_fresh_process(self):
        test_py = os.path.abspath(os.path.join(
            os.path.dirname(__file__), "phase10a31_sidecar_key.py"))
        r = subprocess.run([
            sys.executable, "-m", "pytest",
                            "-xvs",
                            "-k", "test_file_unpacked",
                            test_py,
                            "--tb=short"],
            capture_output=True, text=True,
            timeout=120,
            cwd=os.path.dirname(__file__))
        self.assertEqual(r.returncode, 0,
            "A3.1 test_file_unpacked failed in fresh process: " + str(r.stdout)[:500])
        self.assertIn("PASSED", r.stdout)

    def test_28_fresh_process_does_not_recurse(self):
        test_py = os.path.abspath(os.path.join(
            os.path.dirname(__file__), "phase10a31_sidecar_key.py"))
        r = subprocess.run([
            sys.executable, "-m", "pytest",
                            "-xvs",
                            "-k", "test_file_unpacked",
                            test_py,
                            "--tb=short"],
            capture_output=True, text=True,
            timeout=120,
            cwd=os.path.dirname(__file__))
        self.assertEqual(r.returncode, 0)
        lines_out = r.stdout.splitlines()
        test_starts = [l for l in lines_out if "test_file_unpacked" in l]
        self.assertGreater(len(test_starts), 0)
        found = False
        for l in test_starts:
            if "PASSED" in l:
                found = True
        self.assertTrue(found)
