"""
Phase 10A.3.3 — Content-Based Sidecar Asset Identity.

Tests:
- _xxh64_file_hex streaming file-hash helper
- make_sidecar_key content-hash-keyed filename
- Content-keyed _prepare_source_sidecar flow
- asset_id on SidecarPreparationResult
- Content collision detection ("verified" vs "content_collision")
- _make_success destination validation
- Safety and regression preserved

Pure Python + mock bpy — no Blender runtime required.
"""

import ast
import builtins
import importlib
import importlib.util as _importlib_util
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import types
import unittest

_sys_path_dir = os.path.join(os.path.dirname(__file__), "..")
if _sys_path_dir not in sys.path:
    sys.path.insert(0, _sys_path_dir)

_A33_MODULE_KEYS = {
    "bpy", "bpy.path", "bpy.props", "bpy.types", "bpy.data",
    "bpy.data.images", "bpy.app", "bpy.app.handlers",
    "bpy.context", "bpy.ops", "bpy.utils",
    "network", "mathutils", "uelivesync_blender_addon",
    "Blender_Addon",
}


def _restore_a33_modules(saved):
    for key in list(sys.modules.keys()):
        if key in _A33_MODULE_KEYS and key not in saved:
            del sys.modules[key]
    for key, val in saved.items():
        if key in sys.modules:
            sys.modules[key] = val
    if "Blender_Addon" not in saved:
        sys.modules.pop("Blender_Addon", None)
        sys.modules.pop("Blender_Addon.network", None)


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
        self.subtype = kw.get("subtype", "NONE")
        self.default = kw.get("default", "")

_bpy_mod.props.BoolProperty = lambda **kw: _BoolProp(**kw)
_bpy_mod.props.IntProperty = lambda **kw: _IntProp(**kw)
_bpy_mod.props.FloatProperty = lambda **kw: _FloatProp(**kw)
_bpy_mod.props.StringProperty = lambda **kw: _StringProp(**kw)
_bpy_mod.props.EnumProperty = lambda items=None, **kw: type("EnumProp", (), {"items": items or []})()

_bpy_mod.app = types.ModuleType("bpy.app")
_bpy_mod.app.handlers = types.ModuleType("bpy.app.handlers")
_bpy_mod.app.handlers.load_post = []
_bpy_mod.app.handlers.save_post = []
_bpy_mod.app.handlers.persistent = lambda fn: fn

_bpy_mod.context = types.ModuleType("bpy.context")
_bpy_mod.context.scene = types.ModuleType("bpy.context.scene")
_bpy_mod.context.scene.render = None
_bpy_mod.context.scene.render = None

_bpy_mod.types = types.ModuleType("bpy.types")
_bpy_mod.types.AddonPreferences = type("AddonPreferences", (), {})
_bpy_mod.types.Operator = type("Operator", (), {})
_bpy_mod.types.Panel = type("Panel", (), {})
_bpy_mod.types.PropertyGroup = type("PropertyGroup", (), {})
_bpy_mod.types.Image = type("Image", (), {"name": "", "filepath": "", "size": [0, 0], "source": "FILE", "packed_file": None})
_bpy_mod.types.Material = type("Material", (), {"name": "", "node_tree": None})
_bpy_mod.types.Mesh = type("Mesh", (), {"name": "", "materials": []})

_bpy_mod.data = types.ModuleType("bpy.data")

class _MockImageData:
    def __init__(self):
        self._images = {}

    def set_image(self, name, render_bytes):
        img = types.SimpleNamespace(
            name=name,
            save_render=lambda p: open(p, "wb").write(render_bytes),
        )
        self._images[name] = img

    def get(self, name, default=None):
        return self._images.get(name, default)

_bpy_mod.data.images = _MockImageData()

_bpy_mod.utils = types.ModuleType("bpy.utils")
_bpy_mod.utils.register_class = lambda cls: None
_bpy_mod.utils.unregister_class = lambda cls: None

_bpy_mod.ops = types.ModuleType("bpy.ops")

_saved_modules = {}
for key in _A33_MODULE_KEYS:
    _saved_modules[key] = sys.modules.get(key)
sys.modules["bpy"] = _bpy_mod
sys.modules["bpy.path"] = _bpy_mod.path
sys.modules["bpy.props"] = _bpy_mod.props
sys.modules["bpy.types"] = _bpy_mod.types
sys.modules["bpy.data"] = _bpy_mod.data
sys.modules["bpy.data.images"] = _bpy_mod.data.images
sys.modules["bpy.app"] = _bpy_mod.app
sys.modules["bpy.app.handlers"] = _bpy_mod.app.handlers
sys.modules["bpy.context"] = _bpy_mod.context
sys.modules["bpy.ops"] = _bpy_mod.ops
sys.modules["bpy.utils"] = _bpy_mod.utils


_mathutils_mod = types.ModuleType("mathutils")

class _MockMatrix:
    def __init__(self, *a, **kw): pass
    def copy(self): return _MockMatrix()
    def __mul__(self, other): return _MockMatrix()
    def __matmul__(self, other): return _MockMatrix()
    def invert(self): pass
    def invert_safe(self): pass
    def decompose(self): return (None, None, None)
    def to_quaternion(self): return None
    def to_euler(self): return None
    def to_translation(self): return None
    def to_scale(self): return None
    @staticmethod
    def Identity(*a, **kw): return _MockMatrix()
    @staticmethod
    def Translation(*a, **kw): return _MockMatrix()
    @staticmethod
    def Rotation(*a, **kw): return _MockMatrix()
    @staticmethod
    def Scale(*a, **kw): return _MockMatrix()
    @staticmethod
    def LocRotScale(*a, **kw): return _MockMatrix()

_mathutils_mod.Matrix = _MockMatrix
_mathutils_mod.Vector = lambda *a, **kw: type("Vector", (), {"x": 0, "y": 0, "z": 0})()
_mathutils_mod.Euler = lambda *a, **kw: type("Euler", (), {"x": 0, "y": 0, "z": 0})()
_mathutils_mod.Quaternion = lambda *a, **kw: type("Quaternion", (), {"w": 1, "x": 0, "y": 0, "z": 0})()
sys.modules["mathutils"] = _mathutils_mod

_uelivesync_mod = types.ModuleType("uelivesync_blender_addon")
sys.modules["uelivesync_blender_addon"] = _uelivesync_mod

_SAVED_BPY_TYPES_OPERATOR = getattr(_bpy_mod.types, "Operator", None)
_bpy_mod.types.Operator = type("Op", (), {
    "bl_idname": "test", "bl_label": "test",
    "bl_description": "test", "__module__": "test",
})

_SRC = os.path.join(os.path.dirname(__file__), "..", "Blender_Addon")
_init_spec = _importlib_util.spec_from_file_location(
    "uelivesync_blender_addon", os.path.join(_SRC, "__init__.py"))
_init_mod = _importlib_util.module_from_spec(_init_spec)
_init_mod.__package__ = "uelivesync_blender_addon"

# Import real network module and attach it to the addon module.
# A3.3 tests use the actual production _xxh64_file_hex, xxh64, and
# make_sidecar_key — no SHA-256 substitutes.
import Blender_Addon.network as _real_network
_init_mod.network = _real_network
sys.modules["uelivesync_blender_addon"] = _init_mod

try:
    _init_spec.loader.exec_module(_init_mod)
finally:
    if _SAVED_BPY_TYPES_OPERATOR is not None:
        _bpy_mod.types.Operator = _SAVED_BPY_TYPES_OPERATOR

def _reset_xxh64_cache():
    pass


class _MockImage:
    def __init__(self, name="", source="FILE", filepath="", is_packed=False,
                 file_format="PNG", colorspace_settings=None):
        self.name = name
        self.source = source
        self.filepath = filepath
        self.filepath_raw = filepath
        self.is_packed = is_packed
        self.packed_file = is_packed
        self.size = [512, 512]
        self.file_format = file_format
        self.colorspace_settings = colorspace_settings or type("CS", (), {"name": "sRGB"})()


def _make_tex_node(name, img_name, source="FILE", filepath="",
                   packed=False, file_format="PNG"):
    cs = types.SimpleNamespace(name="sRGB")
    img = _MockImage(name=img_name, source=source, filepath=filepath,
                     is_packed=packed, file_format=file_format,
                     colorspace_settings=cs)
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
    return principled


def _make_obj_with_tex(img_name, source="FILE", filepath="", packed=False):
    tex_node = _make_tex_node("Tex", img_name, source=source, filepath=filepath,
                              packed=packed)
    principled = _make_principled({"Base Color": tex_node})
    tree = _MockNodeTree(nodes=[principled, tex_node])
    mat = _MockMaterial(name="M", node_tree=tree)
    obj = _MockObject(material_slots=[_MockMaterialSlot(material=mat)])
    return obj, tex_node


class _MockNodeTree:
    def __init__(self, nodes=None):
        self.nodes = nodes or []


class _MockMaterialSlot:
    def __init__(self, material=None):
        self.material = material


class _MockMaterial:
    def __init__(self, name="M", node_tree=None):
        self.name = name
        self.use_nodes = True
        self.node_tree = node_tree


class _MockObject:
    def __init__(self, name="Cube", material_slots=None):
        self.name = name
        self.material_slots = material_slots or []


_saved_a33_modules = {}


def pytest_sessionstart(session):
    global _saved_a33_modules
    _saved_a33_modules = {}
    for key in _A33_MODULE_KEYS:
        if key in sys.modules:
            _saved_a33_modules[key] = sys.modules[key]


def pytest_configure(config):
    _restore_a33_modules(_saved_a33_modules)
    keys_to_delete = [k for k in sys.modules if k.startswith("Blender_Addon") or
                      k in _A33_MODULE_KEYS]
    for k in keys_to_delete:
        del sys.modules[k]


def pytest_collection_modifyitems(session, config, items):
    a33_tests = {id(item) for item in items if item.fspath.basename == "phase10a33_content_asset_identity.py"}
    other_tests = [item for item in items if id(item) not in a33_tests]
    if other_tests:
        _restore_a33_modules(_saved_a33_modules)
        keys_to_delete = [k for k in sys.modules if k in _A33_MODULE_KEYS or
                         k.startswith("Blender_Addon")]
        for k in keys_to_delete:
            del sys.modules[k]


def pytest_sessionfinish(session):
    _restore_a33_modules(_saved_a33_modules)
    keys_to_delete = [k for k in sys.modules if k in _A33_MODULE_KEYS]
    for k in keys_to_delete:
        del sys.modules[k]


class TestXXH64FileHex(unittest.TestCase):

    def setUp(self):
        _reset_xxh64_cache()

    def test_01_valid_file_returns_16_char_hex(self):
        with tempfile.TemporaryDirectory() as td:
            fp = os.path.join(td, "test.bin")
            with open(fp, "wb") as f: f.write(b"hello" * 100)
            h = _init_mod.network._xxh64_file_hex(fp)
            self.assertIsInstance(h, str)
            self.assertEqual(len(h), 16)
            self.assertTrue(all(c in "0123456789abcdef" for c in h))

    def test_02_empty_file_returns_valid_hash(self):
        with tempfile.TemporaryDirectory() as td:
            fp = os.path.join(td, "empty.bin")
            with open(fp, "wb") as f: pass
            h = _init_mod.network._xxh64_file_hex(fp)
            self.assertIsInstance(h, str)
            self.assertEqual(len(h), 16)

    # -- exact known-vector assertions (Step B) -----------------------

    def test_02b_xxh64_known_vector_empty(self):
        self.assertEqual(f"{_init_mod.network.xxh64(b''):016x}",
                         "d13ef17427015cf0")

    def test_02c_xxh64_known_vector_hello(self):
        self.assertEqual(f"{_init_mod.network.xxh64(b'hello'):016x}",
                         "585c0f045fe46f16")

    def test_02d_xxh64_known_vector_three_bytes(self):
        three = bytes([0, 1, 2])
        self.assertEqual(f"{_init_mod.network.xxh64(three):016x}",
                         "3552a9e1da350e63")

    def test_02e_file_hex_known_vector_empty(self):
        with tempfile.TemporaryDirectory() as td:
            fp = os.path.join(td, "empty.bin")
            with open(fp, "wb") as f: pass
            self.assertEqual(_init_mod.network._xxh64_file_hex(fp),
                             "d13ef17427015cf0")

    def test_02f_file_hex_known_vector_hello(self):
        with tempfile.TemporaryDirectory() as td:
            fp = os.path.join(td, "hello.bin")
            with open(fp, "wb") as f: f.write(b"hello")
            self.assertEqual(_init_mod.network._xxh64_file_hex(fp),
                             "585c0f045fe46f16")

    def test_02g_file_hex_known_vector_three_bytes(self):
        with tempfile.TemporaryDirectory() as td:
            fp = os.path.join(td, "three.bin")
            with open(fp, "wb") as f: f.write(b"\x00\x01\x02")
            self.assertEqual(_init_mod.network._xxh64_file_hex(fp),
                             "3552a9e1da350e63")

    def test_03_nonexistent_file_returns_empty(self):
        h = _init_mod.network._xxh64_file_hex("/nonexistent/path/file.bin")
        self.assertEqual(h, "")

    def test_04_deterministic_hash(self):
        with tempfile.TemporaryDirectory() as td:
            fp = os.path.join(td, "test.bin")
            with open(fp, "wb") as f: f.write(b"data" * 50)
            h1 = _init_mod.network._xxh64_file_hex(fp)
            h2 = _init_mod.network._xxh64_file_hex(fp)
            self.assertEqual(h1, h2,
                "Hash must be deterministic for same file content")

    def test_05_different_content_different_hash(self):
        with tempfile.TemporaryDirectory() as td:
            fp1 = os.path.join(td, "a.bin")
            fp2 = os.path.join(td, "b.bin")
            with open(fp1, "wb") as f: f.write(b"content_a")
            with open(fp2, "wb") as f: f.write(b"content_b")
            h1 = _init_mod.network._xxh64_file_hex(fp1)
            h2 = _init_mod.network._xxh64_file_hex(fp2)
            self.assertNotEqual(h1, h2,
                "Different content must produce different hashes")

    def test_06_directory_path_returns_empty(self):
        with tempfile.TemporaryDirectory() as td:
            h = _init_mod.network._xxh64_file_hex(td)
            self.assertEqual(h, "")




class TestMakeSidecarKeyContent(unittest.TestCase):

    def test_10_accepts_content_hash_hex(self):
        with tempfile.TemporaryDirectory() as td:
            result = _init_mod.network.make_sidecar_key("MyTex", "aabbccddee001122", ".png", td)
            self.assertIsInstance(result, tuple)
            self.assertEqual(len(result), 3)
            dst, key_basename, content_hash = result
            self.assertIsInstance(dst, str)
            self.assertIn("aabbccddee001122", dst)

    def test_11_suffix_is_content_hash_not_locator(self):
        with tempfile.TemporaryDirectory() as td:
            dst, key_basename, content_hash = _init_mod.network.make_sidecar_key(
                "MyTex", "1234567890abcdef", ".png", td)
            basename = os.path.basename(dst)
            name_part, ext = os.path.splitext(basename)
            self.assertEqual(ext, ".png")
            suffix = name_part.split("__")[-1]
            self.assertEqual(len(suffix), 16,
                "Suffix must be 16-char content hash")
            self.assertEqual(suffix, "1234567890abcdef")
            self.assertEqual(content_hash, "1234567890abcdef")

    def test_12_filename_preserves_display_prefix(self):
        with tempfile.TemporaryDirectory() as td:
            dst, _, _ = _init_mod.network.make_sidecar_key(
                "MyTex", "aabbccddee001122", ".png", td)
            basename = os.path.basename(dst)
            self.assertIn("MyTex", basename)
            self.assertIn("aabbccddee001122", basename)

    def test_13_special_chars_in_prefix_sanitized(self):
        with tempfile.TemporaryDirectory() as td:
            dst, _, _ = _init_mod.network.make_sidecar_key(
                "Bad/Name:Tex", "aabbccddee001122", ".png", td)
            basename = os.path.basename(dst)
            self.assertNotIn("/", basename)
            self.assertNotIn(":", basename)


class _BaseSidecarTest(unittest.TestCase):

    def setUp(self):
        _reset_xxh64_cache()

    def _make_source(self, image_name="TestImg", source="FILE", filepath="",
                     packed=False):
        obj, tex = _make_obj_with_tex(
            img_name=image_name, source=source,
            filepath=filepath, packed=packed)
        sources, usages = _init_mod._extract_texture_usages_and_sources(obj)
        return sources[0]

    def _prepare(self, source, dest_dir, reg=None):
        return _init_mod._prepare_source_sidecar(
            source, dest_dir, reg or {}, "abc_asset")

    def _register_generated_image(self, name, content_bytes):
        _bpy_mod.data.images.set_image(name, content_bytes)


class TestContentAssetIdentity(_BaseSidecarTest):

    def test_20_asset_id_present_on_file_success(self):
        with tempfile.TemporaryDirectory() as td:
            src_file = os.path.join(td, "src.png")
            with open(src_file, "wb") as f: f.write(b"texture_data" * 100)
            source = self._make_source(filepath=src_file)
            result = self._prepare(source, td)
            self.assertIsInstance(result, _init_mod.SidecarPreparationResult)
            self.assertTrue(hasattr(result, "asset_id"))
            self.assertIsInstance(result.asset_id, str)
            self.assertEqual(len(result.asset_id), 16,
                "asset_id must be 16-char hex")

    def test_21_asset_id_matches_content_hash(self):
        with tempfile.TemporaryDirectory() as td:
            src_file = os.path.join(td, "src.png")
            with open(src_file, "wb") as f: f.write(b"texture_data" * 100)
            expected_hash = _init_mod.network._xxh64_file_hex(src_file)
            source = self._make_source(filepath=src_file)
            result = self._prepare(source, td)
            self.assertEqual(result.asset_id, expected_hash,
                "asset_id must match xxh64 of source content")

    def test_22_same_content_same_hash_suffix(self):
        with tempfile.TemporaryDirectory() as td:
            content = b"same_content" * 50
            src1 = os.path.join(td, "src1.png")
            src2 = os.path.join(td, "src2.png")
            with open(src1, "wb") as f: f.write(content)
            with open(src2, "wb") as f: f.write(content)
            source1 = self._make_source(filepath=src1, image_name="Img1")
            source2 = self._make_source(filepath=src2, image_name="Img2")
            r1 = self._prepare(source1, td)
            r2 = self._prepare(source2, td)
            # Filenames differ because prefixes differ, but hash suffix is same
            n1 = os.path.basename(r1.destination_path)
            n2 = os.path.basename(r2.destination_path)
            h1 = n1.split("_")[-1].replace(".png", "")
            h2 = n2.split("_")[-1].replace(".png", "")
            self.assertEqual(h1, h2,
                "Same content must produce same hash suffix in filename")

    def test_23_different_content_different_filename(self):
        with tempfile.TemporaryDirectory() as td:
            src1 = os.path.join(td, "src1.png")
            src2 = os.path.join(td, "src2.png")
            with open(src1, "wb") as f: f.write(b"content_a" * 50)
            with open(src2, "wb") as f: f.write(b"content_b" * 50)
            source1 = self._make_source(filepath=src1)
            source2 = self._make_source(filepath=src2)
            r1 = self._prepare(source1, td)
            r2 = self._prepare(source2, td)
            self.assertNotEqual(
                os.path.basename(r1.destination_path),
                os.path.basename(r2.destination_path),
                "Different content must produce different filenames")

    def test_24_existing_matching_content_returns_verified(self):
        with tempfile.TemporaryDirectory() as td:
            src_file = os.path.join(td, "src.png")
            with open(src_file, "wb") as f: f.write(b"texture_data" * 100)
            source = self._make_source(filepath=src_file)
            # First prepare copies the sidecar
            r1 = self._prepare(source, td)
            self.assertEqual(r1.status, "ready")
            self.assertEqual(r1.action, "copied")
            # Second prepare with same content should find match
            r2 = self._prepare(source, td)
            self.assertEqual(r2.status, "ready",
                "Matching existing content should be ready")
            self.assertEqual(r2.action, "verified",
                "Matching existing content should return 'verified'")
            self.assertEqual(r2.destination_path, r1.destination_path)

    def test_25_existing_mismatching_content_returns_content_collision(self):
        with tempfile.TemporaryDirectory() as td:
            src_file = os.path.join(td, "src.png")
            with open(src_file, "wb") as f: f.write(b"original" * 50)
            source = self._make_source(filepath=src_file)
            # First prepare creates dest with content hash of "original"
            r1 = self._prepare(source, td)
            self.assertEqual(r1.status, "ready")
            # Overwrite dest with different content
            with open(r1.destination_path, "wb") as f: f.write(b"different" * 50)
            # Second prepare should detect mismatch
            r2 = self._prepare(source, td)
            self.assertEqual(r2.status, "failed",
                "Mismatching content should fail")
            self.assertEqual(r2.action, "content_collision",
                "Should detect content collision")
            self.assertTrue(r2.error,
                "Error should describe collision")

    def test_26_asset_id_empty_on_failure(self):
        with tempfile.TemporaryDirectory() as td:
            src_file = os.path.join(td, "src.png")
            with open(src_file, "wb") as f: f.write(b"data" * 50)
            source = self._make_source(filepath=src_file)
            # First prepare creates dest
            r1 = self._prepare(source, td)
            self.assertTrue(r1.asset_id, "asset_id must be set on success")
            # Corrupt dest
            with open(r1.destination_path, "wb") as f: f.write(b"other" * 50)
            r2 = self._prepare(source, td)
            self.assertEqual(r2.status, "failed",
                "Collision must be failed")
            self.assertEqual(r2.asset_id, "",
                "asset_id must be empty on failure")

    def test_27_asset_id_not_set_on_missing_dest(self):
        with tempfile.TemporaryDirectory() as td:
            src_file = os.path.join(td, "src.png")
            with open(src_file, "wb") as f: f.write(b"data" * 50)
            source = self._make_source(filepath=src_file)
            orig_replace = os.replace
            def _no_replace(s, d):
                pass
            os.replace = _no_replace
            try:
                result = self._prepare(source, td)
            finally:
                os.replace = orig_replace
            self.assertEqual(result.status, "failed")
            self.assertEqual(result.asset_id, "",
                "asset_id must be empty when destination write fails")

    def test_28_generated_source_deterministic_asset_id(self):
        with tempfile.TemporaryDirectory() as td:
            self._register_generated_image("GenImg", b"generated_render_bytes" * 50)
            source = self._make_source(image_name="GenImg", source="GENERATED")
            r1 = self._prepare(source, td)
            self.assertEqual(r1.status, "ready",
                "GENERATED source with mock save_render must produce ready")
            expected_id = _init_mod.network._xxh64_file_hex(r1.destination_path)
            self.assertEqual(r1.asset_id, expected_id,
                "ready generated result destination hash must equal asset_id")
            # Same content, different name -> same asset_id
            self._register_generated_image("GenImg2", b"generated_render_bytes" * 50)
            source2 = self._make_source(image_name="GenImg2", source="GENERATED")
            r2 = self._prepare(source2, td)
            self.assertEqual(r2.asset_id, r1.asset_id,
                "Same rendered bytes, different names -> same asset_id")
            # Different content, same name -> different asset_id
            self._register_generated_image("GenImg", b"different_bytes" * 50)
            source3 = self._make_source(image_name="GenImg", source="GENERATED")
            r3 = self._prepare(source3, td)
            self.assertNotEqual(r3.asset_id, r1.asset_id,
                "Different rendered bytes -> different asset_id")

    def test_29_missing_dest_dir_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            src_file = os.path.join(td, "src.png")
            with open(src_file, "wb") as f: f.write(b"data" * 50)
            source = self._make_source(filepath=src_file)
            missing_dir = os.path.join(td, "does_not_exist")
            result = self._prepare(source, missing_dir)
            self.assertEqual(result.status, "failed",
                "Missing dest dir must be rejected")
            self.assertIn("unsafe", result.action.lower(),
                "Error must indicate unsafe condition")

    def test_30_asset_id_deterministic_across_runs(self):
        with tempfile.TemporaryDirectory() as td:
            src_file = os.path.join(td, "src.png")
            with open(src_file, "wb") as f: f.write(b"stable" * 50)
            source = self._make_source(filepath=src_file)
            run1 = os.path.join(td, "run1")
            run2 = os.path.join(td, "run2")
            os.makedirs(run1, exist_ok=True)
            os.makedirs(run2, exist_ok=True)
            r1 = self._prepare(source, run1)
            r2 = self._prepare(source, run2)
            self.assertEqual(r1.asset_id, r2.asset_id,
                "Same content must produce same asset_id across separate runs")
            self.assertEqual(r1.action, "copied")
            self.assertEqual(r2.action, "copied")

    def test_31_destination_hash_mismatch(self):
        with tempfile.TemporaryDirectory() as td:
            src_file = os.path.join(td, "src.png")
            with open(src_file, "wb") as f: f.write(b"texture_data" * 100)
            source = self._make_source(filepath=src_file)
            result = self._prepare(source, td)
            self.assertIn(result.action,
                ("copied", "verified", "destination_hash_mismatch"))

    def test_32_sidecar_key_registry_not_required(self):
        with tempfile.TemporaryDirectory() as td:
            src_file = os.path.join(td, "src.png")
            with open(src_file, "wb") as f: f.write(b"data" * 50)
            source = self._make_source(filepath=src_file)
            # Passing empty registry should not affect content-keyed flow
            result = self._prepare(source, td, reg={})
            self.assertIsInstance(result, _init_mod.SidecarPreparationResult)
            self.assertTrue(hasattr(result, "asset_id"))

    def test_33_content_collision_not_mutated_on_verify(self):
        with tempfile.TemporaryDirectory() as td:
            src_file = os.path.join(td, "src.png")
            with open(src_file, "wb") as f: f.write(b"data" * 50)
            source = self._make_source(filepath=src_file)
            r1 = self._prepare(source, td)
            dest_before = os.path.getmtime(r1.destination_path)
            import time
            time.sleep(0.01)
            r2 = self._prepare(source, td)
            dest_after = os.path.getmtime(r1.destination_path)
            self.assertEqual(r2.action, "verified",
                "Second prepare should verify, not re-copy")
            self.assertEqual(dest_before, dest_after,
                "Verified destination must not be modified")

    def test_34_generated_content_collision(self):
        with tempfile.TemporaryDirectory() as td:
            self._register_generated_image("GenImg", b"original_render" * 50)
            source = self._make_source(image_name="GenImg", source="GENERATED")
            r1 = self._prepare(source, td)
            self.assertEqual(r1.status, "ready", "First GENERATED prepare must succeed")
            # Tamper the destination
            with open(r1.destination_path, "wb") as f:
                f.write(b"tampered" * 50)
            r2 = self._prepare(source, td)
            self.assertEqual(r2.status, "failed", "Tampered dest must be failed")
            self.assertEqual(r2.action, "content_collision",
                "Tampered GENERATED dest must be content_collision")

    def test_35_filename_unique_per_content_across_prefixes(self):
        with tempfile.TemporaryDirectory() as td:
            content = b"shared_content" * 50
            src1 = os.path.join(td, "src1.png")
            src2 = os.path.join(td, "src2.png")
            with open(src1, "wb") as f: f.write(content)
            with open(src2, "wb") as f: f.write(content)
            source1 = self._make_source(filepath=src1, image_name="TexA")
            source2 = self._make_source(filepath=src2, image_name="TexB")
            r1 = self._prepare(source1, td)
            r2 = self._prepare(source2, td)
            n1 = os.path.basename(r1.destination_path)
            n2 = os.path.basename(r2.destination_path)
            self.assertNotEqual(n1, n2,
                "Same content, different prefix must produce different filenames")
            # But both should have same hash in their filename
            h1 = n1.split("_")[-1].replace(".png", "")
            h2 = n2.split("_")[-1].replace(".png", "")
            self.assertEqual(h1, h2,
                "Both filenames must contain same content hash")

    # -- Section G: failure flow exact assertions -----------------------

    def test_36_rendered_save_failure_action(self):
        with tempfile.TemporaryDirectory() as td:
            class _FailingImage:
                name = "FailImg"
                def save_render(self, path):
                    raise RuntimeError("render failure")
            _bpy_mod.data.images._images["FailImg"] = _FailingImage()
            source = self._make_source(image_name="FailImg", source="GENERATED")
            result = self._prepare(source, td)
            self.assertEqual(result.status, "failed")
            self.assertIn("exception", result.action)

    def test_37_rendered_hash_failure_action(self):
        with tempfile.TemporaryDirectory() as td:
            self._register_generated_image("GenImg37", b"render_bytes")
            orig_hex = _init_mod.network._xxh64_file_hex
            _call_count_37 = [0]
            def _broken_hex(path):
                _call_count_37[0] += 1
                if _call_count_37[0] == 1:
                    return ""
                return orig_hex(path)
            _init_mod.network._xxh64_file_hex = _broken_hex
            try:
                source = self._make_source(image_name="GenImg37", source="GENERATED")
                result = self._prepare(source, td)
                self.assertEqual(result.status, "failed")
                self.assertEqual(result.action, "rendered_hash_failed")
            finally:
                _init_mod.network._xxh64_file_hex = orig_hex

    def test_38_temporary_hash_mismatch_action(self):
        with tempfile.TemporaryDirectory() as td:
            src_file = os.path.join(td, "src.png")
            with open(src_file, "wb") as f:
                f.write(b"original_content" * 50)
            orig_hex = _init_mod.network._xxh64_file_hex
            _call_count_38 = [0]
            def _tamper_hex(path):
                _call_count_38[0] += 1
                if _call_count_38[0] == 2:
                    return "0000000000000000"
                return orig_hex(path)
            _init_mod.network._xxh64_file_hex = _tamper_hex
            try:
                source = self._make_source(filepath=src_file)
                result = self._prepare(source, td)
                self.assertEqual(result.status, "failed")
                self.assertEqual(result.action, "temporary_hash_mismatch")
                self.assertIn("temp_hash:", result.error)
            finally:
                _init_mod.network._xxh64_file_hex = orig_hex

    def test_39_os_replace_failure_no_leaked_temp(self):
        with tempfile.TemporaryDirectory() as td:
            src_file = os.path.join(td, "src.png")
            with open(src_file, "wb") as f:
                f.write(b"data" * 50)
            orig_replace = os.replace
            def _failing_replace(src, dst):
                os.unlink(src)
                raise RuntimeError("replace failed")
            os.replace = _failing_replace
            try:
                source = self._make_source(filepath=src_file)
                result = self._prepare(source, td)
                self.assertEqual(result.status, "failed")
                self.assertEqual(result.action, "exception")
                # Verify temp file cleanup
                td_files = os.listdir(td)
                temp_files = [f for f in td_files if f != "src.png"]
                self.assertEqual(len(temp_files), 0,
                    f"No leaked temp files: found {temp_files}")
            finally:
                os.replace = orig_replace

    def test_39b_final_destination_hash_mismatch_action(self):
        with tempfile.TemporaryDirectory() as td:
            src_file = os.path.join(td, "src.png")
            with open(src_file, "wb") as f:
                f.write(b"content" * 50)
            orig_hex = _init_mod.network._xxh64_file_hex
            _call_count_39b = [0]
            def _dest_mismatch(path):
                _call_count_39b[0] += 1
                if _call_count_39b[0] == 3:
                    return "deadbeefdeadbeef"
                return orig_hex(path)
            _init_mod.network._xxh64_file_hex = _dest_mismatch
            try:
                source = self._make_source(filepath=src_file)
                result = self._prepare(source, td)
                self.assertEqual(result.status, "failed")
                self.assertEqual(result.action, "destination_hash_mismatch")
            finally:
                _init_mod.network._xxh64_file_hex = orig_hex

    def test_39c_existing_corrupt_content_keyed_destination(self):
        """Existing corrupt content-keyed destination returns content_collision."""
        with tempfile.TemporaryDirectory() as td:
            src_file = os.path.join(td, "src.png")
            with open(src_file, "wb") as f:
                f.write(b"valid_content" * 50)
            source = self._make_source(filepath=src_file)
            r1 = self._prepare(source, td)
            self.assertEqual(r1.status, "ready")
            # Corrupt the dest with different content
            with open(r1.destination_path, "wb") as f:
                f.write(b"corrupted_content" * 50)
            r2 = self._prepare(source, td)
            self.assertEqual(r2.status, "failed")
            self.assertEqual(r2.action, "content_collision")


class TestSidecarResultAssetIdContract(unittest.TestCase):

    def test_40_ast_asset_id_field_annotated(self):
        init_py = os.path.join(os.path.dirname(__file__), "..", "Blender_Addon", "__init__.py")
        with open(init_py, "r") as f:
            tree = ast.parse(f.read(), filename=init_py)
        found = False
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == "SidecarPreparationResult":
                for item in node.body:
                    if isinstance(item, ast.AnnAssign) and hasattr(item.target, "id"):
                        if item.target.id == "asset_id":
                            found = True
                            self.assertEqual(item.annotation.id, "str",
                                "asset_id must be str")
        self.assertTrue(found,
            "SidecarPreparationResult must have asset_id: str annotation")

    def test_41_asset_id_default_is_empty_string(self):
        default = _init_mod.SidecarPreparationResult(
            source=None,
            status="failed",
            action="no_op",
            source_locator="test",
            destination_path="",
            filename="",
            image_name="",
            size=0,
        )
        self.assertEqual(default.asset_id, "",
            "asset_id default must be empty string")

    def test_42_asset_id_accepts_custom_value(self):
        result = _init_mod.SidecarPreparationResult(
            source=None,
            status="ready",
            action="copied",
            source_locator="test",
            destination_path="/fake/path.png",
            filename="path.png",
            image_name="Test",
            size=1024,
            asset_id="aabbccddee001122",
        )
        self.assertEqual(result.asset_id, "aabbccddee001122")

    def test_43_network_adds_xxh64_file_hex(self):
        import Blender_Addon.network as net
        self.assertTrue(hasattr(net, "_xxh64_file_hex"),
            "network module must export _xxh64_file_hex")
        self.assertTrue(callable(net._xxh64_file_hex))

    def test_44_make_sidecar_key_signature_accepts_content_hash(self):
        import Blender_Addon.network as net
        import inspect
        try:
            sig = inspect.signature(net.make_sidecar_key)
        except (ValueError, TypeError):
            sig = None
        if sig is not None:
            param_names = list(sig.parameters.keys())
            self.assertIn("content_hash_hex", param_names,
                "make_sidecar_key must accept content_hash_hex parameter")

    def test_45_no_wire_format_change(self):
        """A3.3 does not modify protocol constants or packet format."""
        import Blender_Addon.network as net
        self.assertTrue(hasattr(net, "PT_Keyframe"),
            "PT_KEYFRAME constant must exist")
        self.assertTrue(hasattr(net, "PT_FBXImportRequest"),
            "PT_FBXImportRequest must exist")

    def test_46_no_ue_code_change(self):
        """A3.3 does not modify UE plugin source."""
        ue_src = os.path.join(os.path.dirname(__file__), "..", "UE_Plugin",
                              "UELiveSync", "Source", "UELiveSync")
        if os.path.isdir(ue_src):
            r = subprocess.run(
                ["git", "diff", "HEAD", "--", ue_src],
                capture_output=True, text=True,
                cwd=os.path.join(os.path.dirname(__file__), ".."))
            self.assertEqual(r.stdout, "",
                "A3.3 must not modify UE plugin source: " + r.stdout[:200])


class TestContentAssetIdentityRegression(_BaseSidecarTest):

    def test_50_source_type_file_works(self):
        with tempfile.TemporaryDirectory() as td:
            src_file = os.path.join(td, "src.png")
            with open(src_file, "wb") as f: f.write(b"data" * 50)
            source = self._make_source(filepath=src_file, source="FILE")
            result = self._prepare(source, td)
            self.assertIsInstance(result, _init_mod.SidecarPreparationResult)
            self.assertEqual(result.status, "ready",
                "FILE source with valid content must be ready")

    def test_51_source_type_generated_works(self):
        with tempfile.TemporaryDirectory() as td:
            self._register_generated_image("GenImg51", b"generated_data" * 50)
            source = self._make_source(image_name="GenImg51", source="GENERATED")
            result = self._prepare(source, td)
            self.assertEqual(result.status, "ready",
                "GENERATED source with valid mock save_render must be ready")

    def test_52_sidecar_result_struct_preserved(self):
        """SidecarPreparationResult fields preserved from A3.2."""
        with tempfile.TemporaryDirectory() as td:
            src_file = os.path.join(td, "src.png")
            with open(src_file, "wb") as f: f.write(b"data" * 50)
            source = self._make_source(filepath=src_file)
            result = self._prepare(source, td)
            expected_fields = {
                "source", "status", "action", "source_locator",
                "destination_path", "error", "asset_id",
            }
            actual = {f for f in expected_fields if hasattr(result, f)}
            self.assertEqual(actual, expected_fields,
                f"Missing fields: {expected_fields - actual}")

    def test_53_destination_exists_after_successful_prepare(self):
        with tempfile.TemporaryDirectory() as td:
            src_file = os.path.join(td, "src.png")
            with open(src_file, "wb") as f: f.write(b"data" * 50)
            source = self._make_source(filepath=src_file)
            result = self._prepare(source, td)
            if result.status == "ready":
                self.assertTrue(os.path.exists(result.destination_path),
                    "Destination file must exist after successful prepare")

    def test_54_unsupported_source_type_fails(self):
        with tempfile.TemporaryDirectory() as td:
            source = self._make_source(source="MOVIE", filepath="/fake.mov")
            result = self._prepare(source, td)
            self.assertEqual(result.status, "failed",
                "Unsupported source type must fail")

    def test_55_missing_source_file_fails(self):
        with tempfile.TemporaryDirectory() as td:
            source = self._make_source(source="FILE", filepath="/nonexistent.png")
            result = self._prepare(source, td)
            self.assertEqual(result.status, "failed",
                "Missing source file must fail")


class TestContentIdentityIsolatedProcess(unittest.TestCase):

    def test_60_fresh_process_xxh64_works(self):
        test_py = os.path.abspath(__file__)
        r = subprocess.run([
            sys.executable, "-m", "pytest",
            test_py, "-q", "--tb=short",
            "-k", "test_01_valid_file_returns_16_char_hex and not fresh_process"],
            capture_output=True, text=True,
            timeout=60,
            cwd=os.path.dirname(__file__))
        self.assertEqual(r.returncode, 0,
            "test_01 failed in fresh process: " + str(r.stdout)[:300])

    def test_61_fresh_process_content_identity_works(self):
        test_py = os.path.abspath(__file__)
        r = subprocess.run([
            sys.executable, "-m", "pytest",
            test_py, "-q", "--tb=short",
            "-k", "test_20_asset_id_present_on_file_success and not fresh_process"],
            capture_output=True, text=True,
            timeout=60,
            cwd=os.path.dirname(__file__))
        self.assertEqual(r.returncode, 0,
            "test_20 failed in fresh process: " + str(r.stdout)[:300])


class TestXxh64StreamCompatibility(unittest.TestCase):
    """Section C: streaming xxh64 matches one-shot for all required lengths."""

    CHUNK_SIZE = 1048576  # same as _xxh64_file_hex default

    def _check(self, payload, seed=0):
        expected = '{:016x}'.format(_init_mod.network.xxh64(payload, seed=seed))
        s = _init_mod.network._Xxh64Stream(seed=seed)
        s.update(payload)
        self.assertEqual(s.hexdigest(), expected)

    def _check_split(self, payload, splits, seed=0):
        expected = '{:016x}'.format(_init_mod.network.xxh64(payload, seed=seed))
        s = _init_mod.network._Xxh64Stream(seed=seed)
        pos = 0
        for sl in splits:
            take = min(sl, len(payload) - pos)
            if take > 0:
                s.update(payload[pos:pos+take])
                pos += take
        self.assertEqual(s.hexdigest(), expected)

    # -- known vectors ---------------------------------------------------
    def test_c00_empty(self):
        self._check(b"")

    def test_c01_hello(self):
        self._check(b"hello")

    def test_c02_three_bytes(self):
        self._check(b"\x00\x01\x02")

    # -- boundary lengths ------------------------------------------------
    def test_c03_len_0(self):
        self._check(b"")

    def test_c04_len_1(self):
        self._check(b"a")

    def test_c05_len_3(self):
        self._check(b"abc")

    def test_c06_len_4(self):
        self._check(b"abcd")

    def test_c07_len_7(self):
        self._check(b"abcdefg")

    def test_c08_len_8(self):
        self._check(b"abcdefgh")

    def test_c09_len_15(self):
        self._check(b"A" * 15)

    def test_c10_len_16(self):
        self._check(b"A" * 16)

    def test_c11_len_31(self):
        self._check(b"A" * 31)

    def test_c12_len_32(self):
        self._check(b"A" * 32)

    def test_c13_len_33(self):
        self._check(b"A" * 33)

    def test_c14_len_63(self):
        self._check(b"A" * 63)

    def test_c15_len_64(self):
        self._check(b"A" * 64)

    def test_c16_len_65(self):
        self._check(b"A" * 65)

    def test_c17_chunk_minus_1(self):
        n = self.CHUNK_SIZE - 1
        self._check(b"A" * n)

    def test_c18_chunk(self):
        n = self.CHUNK_SIZE
        self._check(b"A" * n)

    def test_c19_chunk_plus_1(self):
        n = self.CHUNK_SIZE + 1
        self._check(b"A" * n)

    def test_c20_2chunk_plus_17(self):
        n = 2 * self.CHUNK_SIZE + 17
        self._check(b"A" * n)

    # -- split update patterns -------------------------------------------
    def test_c30_one_byte_per_update(self):
        payload = b"split update test with single bytes"
        self._check_split(payload, [1] * len(payload))

    def test_c31_irregular_chunks(self):
        payload = b"irregular chunk sizes for streaming xxh64 verification"
        # Ensure full consumption by repeating pattern to EOF
        self._check_split(payload, [1, 7, 13, 31, 32, 33])
        payload = b"A" * (self.CHUNK_SIZE * 3 + 7)
        self._check_split(payload, [self.CHUNK_SIZE] * 4)

    def test_c33_multi_seed(self):
        for seed in [0, 42, 0x9E3779B1, 0xFFFFFFFFFFFFFFFF]:
            self._check(b"multi-seed payload", seed=seed)

    def test_c34_empty_split(self):
        payload = b"non-empty payload"
        expected = '{:016x}'.format(_init_mod.network.xxh64(payload))
        s = _init_mod.network._Xxh64Stream(seed=0)
        s.update(b"")
        s.update(payload)
        s.update(b"")
        self.assertEqual(s.hexdigest(), expected)

    def test_c35_memoryview_input(self):
        payload = memoryview(b"memoryview input works with streaming")
        expected = '{:016x}'.format(_init_mod.network.xxh64(bytes(payload)))
        s = _init_mod.network._Xxh64Stream(seed=0)
        s.update(payload)
        self.assertEqual(s.hexdigest(), expected)


class TestBoundedFileReads(unittest.TestCase):
    """Section D: _xxh64_file_hex reads are bounded and correct."""

    def test_d00_single_read_small_file(self):
        with tempfile.TemporaryDirectory() as td:
            fp = os.path.join(td, "small.bin")
            content = b"small content"
            with open(fp, "wb") as f:
                f.write(content)
            expected = '{:016x}'.format(_init_mod.network.xxh64(content))
            result = _init_mod.network._xxh64_file_hex(fp, chunk_size=64)
            self.assertEqual(result, expected)

    def test_d01_multi_read_big_file(self):
        with tempfile.TemporaryDirectory() as td:
            fp = os.path.join(td, "big.bin")
            content = b"A" * 200
            with open(fp, "wb") as f:
                f.write(content)
            expected = '{:016x}'.format(_init_mod.network.xxh64(content))
            result = _init_mod.network._xxh64_file_hex(fp, chunk_size=7)
            self.assertEqual(result, expected)

    def test_d02_every_read_bounded(self):
        """Verify every read() call uses an explicit positive size <= chunk_size."""
        from unittest import mock
        with tempfile.TemporaryDirectory() as td:
            fp = os.path.join(td, "bounded.bin")
            content = b"A" * 100
            with open(fp, "wb") as f:
                f.write(content)
            chunk_size = 13
            recorded = []
            real_open = builtins.open
            def _recording_open(path, mode='rb', **kw):
                f = real_open(path, mode, **kw)
                class _RecordingFile:
                    def __init__(self, fh):
                        self._fh = fh
                    def read(self, n=-1):
                        recorded.append(n)
                        return self._fh.read(n)
                    def close(self):
                        self._fh.close()
                    def __enter__(self):
                        return self
                    def __exit__(self, *a):
                        self.close()
                return _RecordingFile(f)
            with mock.patch('builtins.open', _recording_open):
                _init_mod.network._xxh64_file_hex(fp, chunk_size=chunk_size)
            self.assertGreaterEqual(len(recorded), 2,
                f"Expected >=2 reads, got {len(recorded)}")
            for n in recorded:
                self.assertIsNot(n, None, "read() received None")
                self.assertNotEqual(n, -1, "read(-1) is not allowed")
                self.assertGreater(n, 0, f"read({n}) must be positive")
                self.assertLessEqual(n, chunk_size, f"read({n}) exceeds chunk_size {chunk_size}")

    def test_d03_eof_read_bounded(self):
        """Last read returning empty bytes from a bounded call is ok."""
        with tempfile.TemporaryDirectory() as td:
            fp = os.path.join(td, "eof.bin")
            with open(fp, "wb") as f:
                f.write(b"data")
            result = _init_mod.network._xxh64_file_hex(fp, chunk_size=4)
            self.assertEqual(len(result), 16)

    def test_d04_nonexistent_path(self):
        result = _init_mod.network._xxh64_file_hex("/nonexistent/file.xyz")
        self.assertEqual(result, "")

    def test_d05_directory_path(self):
        with tempfile.TemporaryDirectory() as td:
            result = _init_mod.network._xxh64_file_hex(td)
            self.assertEqual(result, "")

    def test_d06_empty_file(self):
        with tempfile.TemporaryDirectory() as td:
            fp = os.path.join(td, "empty.bin")
            with open(fp, "wb") as f:
                pass
            result = _init_mod.network._xxh64_file_hex(fp)
            expected = '{:016x}'.format(_init_mod.network.xxh64(b""))
            self.assertEqual(result, expected)

    def test_d07_read_error_returns_empty(self):
        """Deterministic injected read failure (no chmod/filesystem)."""
        record = []
        real_open = builtins.open
        def _failing_open(path, mode='rb', **kw):
            f = real_open(path, mode, **kw)
            read_count = [0]
            orig_read = f.read
            def _injecting_read(n=-1):
                read_count[0] += 1
                record.append(n)
                if read_count[0] == 3:
                    raise OSError("injected read failure")
                return orig_read(n)
            f.read = _injecting_read
            return f
        saved_open = builtins.open
        builtins.open = _failing_open
        try:
            with tempfile.TemporaryDirectory() as td:
                fp = os.path.join(td, "failing.bin")
                with open(fp, "wb") as f:
                    f.write(b"data" * 50)
                result = _init_mod.network._xxh64_file_hex(fp, chunk_size=16)
                self.assertEqual(result, "")
                self.assertGreaterEqual(len(record), 3,
                    "Must have performed >=3 reads before injection")
        finally:
            builtins.open = saved_open


class TestXxh64StreamTailInvariant(unittest.TestCase):
    """Step D: retained-tail invariant after every update call.

    Every split pattern cycles until the ENTIRE payload is consumed.
    After every update() the tail is asserted <= 31.
    Final digest is verified against one-shot xxh64.
    """

    TAIL_LENGTHS = [0, 1, 31, 32, 33, 63, 64, 65,
                    1024 * 1024 - 1, 1024 * 1024, 1024 * 1024 + 1]

    # Each pattern cycles repeatedly until the full payload is fed.
    SPLIT_PATTERNS = [
        [1],                         # single bytes
        [1, 1],                      # two single-byte (redundant but explicit)
        [32],                        # 32-byte chunks
        [33],                        # 33-byte chunks
        [1, 7, 13, 31, 32, 33],    # irregular repeating
    ]

    def _feed_pattern(self, stream, payload, pattern):
        """Feed payload using compact repeating pattern. Assert tail after each."""
        pos = 0
        pat_idx = 0
        while pos < len(payload):
            requested = pattern[pat_idx % len(pattern)]
            self.assertGreater(requested, 0,
                f"pattern[{pat_idx}] = {requested} must be > 0")
            take = min(requested, len(payload) - pos)
            stream.update(payload[pos:pos + take])
            pos += take
            pat_idx += 1
            self.assertLessEqual(len(stream._buf), 31,
                f"After update #{pat_idx} (pattern_idx={pat_idx-1}, "
                f"pattern={pattern}, take={take}): tail {len(stream._buf)} > 31")
        self.assertEqual(pos, len(payload),
            f"Pattern {pattern} must consume full payload: consumed={pos}, "
            f"length={len(payload)}")
        return pos

    def _check_tail(self, length, seed=0):
        payload = bytes([i & 0xFF for i in range(length)])
        # single-update (all at once)
        stream = _init_mod.network._Xxh64Stream(seed=seed)
        stream.update(payload)
        self.assertLessEqual(len(stream._buf), 31,
            f"len={length}, all-at-once: tail {len(stream._buf)} > 31")
        # verify digest matches one-shot
        expected = '{:016x}'.format(_init_mod.network.xxh64(payload, seed=seed))
        self.assertEqual(stream.hexdigest(), expected,
            f"len={length} digest mismatch for seed={seed}")
        # cycle through split patterns
        for pattern in self.SPLIT_PATTERNS:
            stream2 = _init_mod.network._Xxh64Stream(seed=seed)
            consumed = self._feed_pattern(stream2, payload, pattern)
            self.assertEqual(consumed, len(payload))
            # final digest must match one-shot for the complete payload
            expected2 = '{:016x}'.format(_init_mod.network.xxh64(payload, seed=seed))
            self.assertEqual(stream2.hexdigest(), expected2,
                f"pattern={pattern}, len={length} digest mismatch for seed={seed}")

    def test_tail_00_empty(self):
        self._check_tail(0)

    def test_tail_01_len_1(self):
        self._check_tail(1)

    def test_tail_02_len_31(self):
        self._check_tail(31)

    def test_tail_03_len_32(self):
        self._check_tail(32)

    def test_tail_04_len_33(self):
        self._check_tail(33)

    def test_tail_05_len_63(self):
        self._check_tail(63)

    def test_tail_06_len_64(self):
        self._check_tail(64)

    def test_tail_07_len_65(self):
        self._check_tail(65)

    def test_tail_08_len_1M_minus_1(self):
        self._check_tail(1024 * 1024 - 1)

    def test_tail_09_len_1M(self):
        self._check_tail(1024 * 1024)

    def test_tail_10_len_1M_plus_1(self):
        self._check_tail(1024 * 1024 + 1)

    def test_tail_11_seed_variants(self):
        for seed in [0, 42, 0x9E3779B1]:
            self._check_tail(33, seed=seed)
            self._check_tail(1024 * 1024, seed=seed)

    def test_tail_large_payload_full_pattern_consumption(self):
        """Prove [1] and [32] patterns fully consume a 1 MiB payload."""
        payload = bytes([i & 0xFF for i in range(1024 * 1024)])
        for pattern in self.SPLIT_PATTERNS:
            stream = _init_mod.network._Xxh64Stream(seed=0)
            consumed = self._feed_pattern(stream, payload, pattern)
            self.assertEqual(consumed, len(payload))
            expected = '{:016x}'.format(_init_mod.network.xxh64(payload))
            self.assertEqual(stream.hexdigest(), expected,
                f"pattern={pattern} full-1MiB digest mismatch")


class TestXxh64StreamFullConsumptionRegression(unittest.TestCase):
    """Gate D: regression proving every split pattern consumes the ENTIRE payload.

    This test must fail against the previous helper that processed each pattern
    element only once (where [1] with a large payload would process only one byte).
    """

    LARGE_PAYLOAD_LEN = 1024 * 1024 + 1  # 1 MiB + 1
    LARGE_PAYLOAD = bytes([i & 0xFF for i in range(LARGE_PAYLOAD_LEN)])

    PATTERNS = [[1], [32], [33], [1, 7, 13, 31, 32, 33]]

    def test_full_consumption_and_digest(self):
        for pattern in self.PATTERNS:
            payload = self.LARGE_PAYLOAD
            chunk_lengths = []

            class _RecordingStream(_init_mod.network._Xxh64Stream):
                def update(self, data):
                    chunk_lengths.append(len(data))
                    super().update(data)

            stream = _RecordingStream(seed=0)
            pos = 0
            pat_idx = 0
            while pos < len(payload):
                requested = pattern[pat_idx % len(pattern)]
                self.assertGreater(requested, 0,
                    f"pattern[{pat_idx}] = {requested} must be > 0")
                take = min(requested, len(payload) - pos)
                chunk = payload[pos:pos + take]
                stream.update(chunk)
                pos += take
                pat_idx += 1

            # Proof: sum of submitted chunk lengths equals the payload length
            self.assertEqual(sum(chunk_lengths), len(payload),
                f"pattern={pattern}: sum(chunk_lengths)={sum(chunk_lengths)} != "
                f"payload_len={len(payload)}")

            # Proof: more than one update call occurred
            self.assertGreater(len(chunk_lengths), 1,
                f"pattern={pattern}: must have >1 update, got {len(chunk_lengths)}")

            # Proof: no submitted chunk is empty
            for i, cl in enumerate(chunk_lengths):
                self.assertGreater(cl, 0,
                    f"pattern={pattern}: chunk[{i}] length {cl} must be > 0")

            # Proof: no chunk exceeds the pattern request except the final remainder
            pat_idx2 = 0
            running = 0
            for i, cl in enumerate(chunk_lengths):
                requested = pattern[pat_idx2 % len(pattern)]
                is_last = (running + cl == len(payload))
                if not is_last:
                    self.assertLessEqual(cl, requested,
                        f"pattern={pattern}: chunk[{i}] length {cl} > requested {requested}")
                running += cl
                pat_idx2 += 1

            # Proof: final digest matches one-shot xxh64
            expected = '{:016x}'.format(
                _init_mod.network.xxh64(self.LARGE_PAYLOAD))
            self.assertEqual(stream.hexdigest(), expected,
                f"pattern={pattern}: digest mismatch on full "
                f"{len(self.LARGE_PAYLOAD)}B payload")


class TestXxh64UpdateSourceRegression(unittest.TestCase):
    """Step D: AST regression — reject old anti-patterns in update()."""

    def test_update_no_self_buf_assignment_slice(self):
        """Reject `self._buf = self._buf[32:]` inside update()."""
        nw_path = os.path.join(os.path.dirname(__file__), "..",
                               "Blender_Addon", "network.py")
        with open(nw_path) as f:
            tree = ast.parse(f.read())
        update_node = None
        for node in ast.walk(tree):
            if (isinstance(node, ast.FunctionDef) and node.name == "update"
                    and isinstance(getattr(node, 'body', None), list)):
                for ancestor in ast.walk(tree):
                    if isinstance(ancestor, ast.ClassDef) and ancestor.name == "_Xxh64Stream":
                        if node in ast.walk(ancestor):
                            update_node = node
                            break
                if update_node:
                    break
        self.assertIsNotNone(update_node,
            "Could not find _Xxh64Stream.update in network.py")
        old_patterns = []
        for n in ast.walk(update_node):
            if isinstance(n, ast.Assign):
                for t in n.targets:
                    if (isinstance(t, ast.Attribute)
                            and isinstance(t.value, ast.Attribute)
                            and t.value.attr == "_buf"
                            and isinstance(t.value.value, ast.Name)
                            and t.value.value.id == "self"
                            and isinstance(n.value, ast.Subscript)
                            and isinstance(n.value.value, ast.Attribute)
                            and n.value.value.attr == "_buf"
                            and isinstance(n.value.value.value, ast.Name)
                            and n.value.value.value.id == "self"
                            and isinstance(n.value.slice, ast.Slice)
                            and n.value.slice.lower is not None):
                        old_patterns.append(
                            f"self._buf = self._buf[{ast.unparse(n.value.slice)}]")
            if (isinstance(n, ast.Call)
                    and isinstance(n.func, ast.Name)
                    and n.func.id == "bytes"
                    and n.args
                    and isinstance(n.args[0], ast.Subscript)
                    and isinstance(n.args[0].value, ast.Attribute)
                    and n.args[0].value.attr == "_buf"
                    and isinstance(n.args[0].value.value, ast.Name)
                    and n.args[0].value.value.id == "self"):
                old_patterns.append("bytes(self._buf[:32])")
        self.assertEqual(len(old_patterns), 0,
            f"Found old anti-patterns in _Xxh64Stream.update: {old_patterns}")

    def test_update_uses_clear_and_extend(self):
        """Confirm update() uses .clear() and .extend() — not slice re-assign."""
        nw_path = os.path.join(os.path.dirname(__file__), "..",
                               "Blender_Addon", "network.py")
        with open(nw_path) as f:
            content = f.read()
        self.assertIn("self._buf.clear()", content,
            "Expected self._buf.clear() in _Xxh64Stream.update")
        self.assertIn("self._buf.extend(", content,
            "Expected self._buf.extend() in _Xxh64Stream.update")


if __name__ == "__main__":
    unittest.main()
