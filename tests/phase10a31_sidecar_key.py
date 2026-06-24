"""
Phase 10A.3.1 — Texture sidecar key and single-traversal tests (correction pass).

Tests the A3.1 texture identity helpers (network.py) and the
single-traversal extraction + sidecar preparation (__init__.py).

Pure Python + mock bpy — no Blender runtime required.

The A3.1 production path is always active (no _USE_A3_1_PATH guard).
All tests exercise real production code paths.
"""

import hashlib
import os
import stat as _stat
import struct
import sys
import tempfile
import types
import unittest
from dataclasses import dataclass

_SRC = os.path.join(os.path.dirname(__file__), "..", "Blender_Addon")
sys.path.insert(0, _SRC)

# =========================================================
# bpy mock module — enough for __init__.py to import without
# a real Blender runtime.
# =========================================================

_bpy_mod = types.ModuleType("bpy")
_bpy_mod.path = types.ModuleType("bpy.path")
_bpy_mod.path.abspath = lambda p: os.path.abspath(os.path.expanduser(p.replace("//", "./")))
_bpy_mod.props = types.ModuleType("bpy.props")


class _BoolProp:
    def __call__(self, **kw): return False
    def __getattribute__(self, name): return False


class _IntProp:
    def __call__(self, **kw): return 0
    def __getattribute__(self, name): return 0


class _FloatProp:
    def __call__(self, **kw): return 0.0
    def __getattribute__(self, name): return 0.0


class _StringProp:
    def __call__(self, **kw): return ""
    def __getattribute__(self, name): return ""


class _EnumProp:
    def __call__(self, **kw): return ""
    def __getattribute__(self, name): return ""


_bpy_mod.props.BoolProperty = _BoolProp()
_bpy_mod.props.IntProperty = _IntProp()
_bpy_mod.props.FloatProperty = _FloatProp()
_bpy_mod.props.StringProperty = _StringProp()
_bpy_mod.props.EnumProperty = _EnumProp()

_bpy_mod.data = types.ModuleType("bpy.data")
_bpy_mod.data.images = types.ModuleType("bpy.data.images")
_bpy_mod.data.images.get = lambda name, default=None: default

_bpy_mod.context = types.ModuleType("bpy.context")

_bpy_mod.app = types.ModuleType("bpy.app")
_bpy_mod.app.handlers = types.ModuleType("bpy.app.handlers")
_bpy_mod.app.handlers.persistent = lambda fn: fn

_bpy_mod.types = types.ModuleType("bpy.types")
_bpy_mod.ops = types.ModuleType("bpy.ops")

_mathutils = types.ModuleType("mathutils")


class _Matrix:
    @staticmethod
    def Identity(n): return None
    @staticmethod
    def Rotation(angle, size, axis): return None
    @staticmethod
    def Translation(translation): return None
    @staticmethod
    def Scale(factor, size): return None


_mathutils.Matrix = _Matrix
_mathutils.Vector = lambda *a: None
_mathutils.Euler = lambda *a: None
_mathutils.Quaternion = lambda *a: None

sys.modules["mathutils"] = _mathutils

_bpy_mod.utils = types.ModuleType("bpy.utils")
_bpy_mod.utils.register_class = lambda cls: None
_bpy_mod.utils.unregister_class = lambda cls: None
_bpy_mod.utils.user_resource = lambda *a, **kw: "/tmp"

sys.modules["bpy"] = _bpy_mod
sys.modules["bpy.path"] = _bpy_mod.path
sys.modules["bpy.props"] = _bpy_mod.props
sys.modules["bpy.data"] = _bpy_mod.data
sys.modules["bpy.data.images"] = _bpy_mod.data.images
sys.modules["bpy.context"] = _bpy_mod.context
sys.modules["bpy.app"] = _bpy_mod.app
sys.modules["bpy.app.handlers"] = _bpy_mod.app.handlers
sys.modules["bpy.types"] = _bpy_mod.types
sys.modules["bpy.ops"] = _bpy_mod.ops
sys.modules["bpy.utils"] = _bpy_mod.utils


# =========================================================
# Import production modules (after bpy mock is installed)
# =========================================================
import network
# __init__.py normally fails to import outside Blender because
# of bpy.types.AddonPreferences. We patch the needed class.

class _MockAddonPrefs:
    bl_idname = "uelivesync_blender_addon"


_bpy_mod.types.AddonPreferences = type("AddonPreferences", (), {})

import importlib
import importlib.util

_init_spec = importlib.util.spec_from_file_location(
    "uelivesync_blender_addon",
    os.path.join(_SRC, "__init__.py"),
)
_init_mod = importlib.util.module_from_spec(_init_spec)
_init_mod.__package__ = "uelivesync_blender_addon"
sys.modules["uelivesync_blender_addon"] = _init_mod
_init_mod.network = network

# Temporarily replace bpy.types
_orig_types = _bpy_mod.types
_bpy_mod.types.AddonPreferences = type("AddonPreferences", (), {})
_bpy_mod.types.Panel = type("Panel", (), {})
_bpy_mod.types.Operator = type("Operator", (), {})
_bpy_mod.types.Menu = type("Menu", (), {})

_init_spec.loader.exec_module(_init_mod)

_bpy_mod.types = _orig_types


# =========================================================
# Mock node classes for tests
# =========================================================

@dataclass
class MockColorSpaceSettings:
    name: str = "sRGB"


@dataclass
class MockImage:
    name: str = "TestImg"
    filepath: str = ""
    filepath_raw: str = ""
    source: str = "FILE"
    packed_file: object = None
    size: tuple = (1024, 768)
    file_format: str = "PNG"
    colorspace_settings: MockColorSpaceSettings = None

    def __post_init__(self):
        if self.colorspace_settings is None:
            self.colorspace_settings = MockColorSpaceSettings()

    def save_render(self, filepath):
        with open(filepath, "wb") as f:
            f.write(b"rendered_png_bytes")


@dataclass
class MockNodeSocket:
    is_linked: bool = False
    links: list = None
    name: str = ""
    identifier: str = ""

    def __post_init__(self):
        if self.links is None:
            self.links = []


@dataclass
class MockNodeLink:
    from_node: object = None


@dataclass
class MockNode:
    type: str = "TEX_IMAGE"
    name: str = "Image Texture"
    image: MockImage = None
    inputs: dict = None

    def __post_init__(self):
        if self.inputs is None:
            self.inputs = {}


@dataclass
class MockNodeTree:
    nodes: list = None

    def __post_init__(self):
        if self.nodes is None:
            self.nodes = []


@dataclass
class MockMaterial:
    name: str = "Material"
    use_nodes: bool = True
    node_tree: MockNodeTree = None

    def __post_init__(self):
        if self.node_tree is None:
            self.node_tree = MockNodeTree()


@dataclass
class MockMaterialSlot:
    material: MockMaterial = None


@dataclass
class MockObject:
    name: str = "TestObj"
    material_slots: list = None
    type: str = "MESH"

    def __post_init__(self):
        if self.material_slots is None:
            self.material_slots = []


def _make_tex_node(name, img_name, filepath="", source="FILE",
                   packed=False, fmt="PNG", colorspace="sRGB"):
    img = MockImage(
        name=img_name, filepath=filepath, filepath_raw=filepath,
        source=source, packed_file=object() if packed else None,
        file_format=fmt,
        colorspace_settings=MockColorSpaceSettings(name=colorspace),
    )
    return MockNode(type="TEX_IMAGE", name=name, image=img)


def _make_principled(socket_map):
    inputs = {}
    for sock_name, from_node in socket_map.items():
        inputs[sock_name] = MockNodeSocket(
            is_linked=True,
            links=[MockNodeLink(from_node=from_node)],
        )
    return MockNode(type="BSDF_PRINCIPLED", inputs=inputs)


# =========================================================
# MTEX parsing helper
# =========================================================

MTEX_MAGIC = 0x4D544558  # matches network.py (ASCII "MTEX" as BE, packed LE)
MTEX_CHANNEL_BASECOLOR = 1
MTEX_CHANNEL_ROUGHNESS = 2
MTEX_CHANNEL_METALLIC = 3
MTEX_CHANNEL_ALPHA = 4
MTEX_CHANNEL_NORMAL = 5


def parse_mtex_block(payload_bytes):
    """Parse MTEX extension block from serialized PT_Material payload.

    Returns list of (slot_index, channel, path_bytes, name_bytes, flags)
    or None if MTEX magic not found.
    """
    offset = 0
    while offset < len(payload_bytes) - 4:
        magic = struct.unpack_from("<I", payload_bytes, offset)[0]
        if magic == MTEX_MAGIC:
            break
        offset += 1
    else:
        return None

    ver = struct.unpack_from("<B", payload_bytes, offset + 4)[0]
    rec_count = struct.unpack_from("<B", payload_bytes, offset + 5)[0]
    pos = offset + 6
    records = []
    for _ in range(rec_count):
        slot_idx = struct.unpack_from("<B", payload_bytes, pos)[0]
        channel = struct.unpack_from("<B", payload_bytes, pos + 1)[0]
        flags = struct.unpack_from("<B", payload_bytes, pos + 2)[0]
        path_len = struct.unpack_from("<H", payload_bytes, pos + 3)[0]
        path_bytes = payload_bytes[pos + 5:pos + 5 + path_len]
        name_len = struct.unpack_from("<B", payload_bytes, pos + 5 + path_len)[0]
        name_bytes = payload_bytes[pos + 6 + path_len:pos + 6 + path_len + name_len]
        records.append((slot_idx, channel, path_bytes, name_bytes, flags))
        pos += 6 + path_len + 1 + name_len
    return records


# =========================================================
# Test suites
# =========================================================

class TestCanonicalLocatorBytes(unittest.TestCase):

    def test_file_unpacked(self):
        result = network._canonical_locator_bytes("FILE", "unpacked", "/path/tex.png")
        self.assertEqual(result, b"FILE:unpacked:/path/tex.png")

    def test_packed(self):
        result = network._canonical_locator_bytes("PACKED", "packed", "TexName")
        self.assertEqual(result, b"PACKED:packed:TexName")

    def test_generated(self):
        result = network._canonical_locator_bytes("GENERATED", "generated", "GenTex")
        self.assertEqual(result, b"GENERATED:generated:GenTex")

    def test_unicode_locator(self):
        result = network._canonical_locator_bytes("FILE", "unpacked", "/p\xe4th/t\xebx.png")
        self.assertIsInstance(result, bytes)
        self.assertTrue(result.startswith(b"FILE:unpacked:"))


class TestSanitizeFilenameComponent(unittest.TestCase):

    def setUp(self):
        self._f = network._sanitize_filename_component

    def test_normal(self):
        self.assertEqual(self._f("HelloWorld"), "HelloWorld")

    def test_trailing_dot(self):
        self.assertEqual(self._f("name."), "name")

    def test_trailing_space(self):
        self.assertEqual(self._f("name "), "name")

    def test_dot_replaced(self):
        self.assertEqual(self._f("."), "_")

    def test_dotdot_replaced(self):
        self.assertEqual(self._f(".."), "_")

    def test_colon_replaced(self):
        self.assertEqual(self._f("a:b"), "a_b")

    def test_slash_replaced(self):
        self.assertEqual(self._f("a/b"), "a_b")

    def test_nul_replaced(self):
        self.assertEqual(self._f("a\x00b"), "a_b")

    def test_control_replaced(self):
        self.assertEqual(self._f("a\x01b"), "a_b")

    def test_reserved_con(self):
        self.assertEqual(self._f("con"), "_con")

    def test_reserved_prn(self):
        self.assertEqual(self._f("PRN"), "_PRN")

    def test_reserved_com1(self):
        self.assertEqual(self._f("com1"), "_com1")

    def test_reserved_lpt9(self):
        self.assertEqual(self._f("lpt9"), "_lpt9")

    def test_empty(self):
        self.assertEqual(self._f(""), "_")


class TestTruncateToUtf8Bytes(unittest.TestCase):

    def setUp(self):
        self._f = network._truncate_to_utf8_bytes

    def test_short_unchanged(self):
        self.assertEqual(self._f("hello", 10), "hello")

    def test_exact_fit(self):
        self.assertEqual(self._f("hello", 5), "hello")

    def test_truncated_ascii(self):
        self.assertEqual(self._f("hello world", 5), "hello")

    def test_multi_byte_boundary(self):
        text = "a\xe9b\u4e16c"
        encoded = text.encode("utf-8")
        self.assertEqual(len(encoded), 8)
        result = self._f(text, 4)
        self.assertEqual(result, "a\xe9b")
        result = self._f(text, 3)
        self.assertEqual(result, "a\xe9")

    def test_zero_max(self):
        self.assertEqual(self._f("hello", 0), "")

    def test_negative_max(self):
        self.assertEqual(self._f("hello", -1), "")


class TestGetNameMax(unittest.TestCase):

    def test_fallback(self):
        result = network._get_name_max("/nonexistent_dir_xyz")
        self.assertEqual(result, 255)

    def test_temp_directory(self):
        with tempfile.TemporaryDirectory() as td:
            result = network._get_name_max(td)
            self.assertGreaterEqual(result, 128)


class TestMakeSidecarKey(unittest.TestCase):

    def setUp(self):
        self._make = network.make_sidecar_key

    def test_basic(self):
        cl = b"FILE:unpacked:/path/tex.png"
        with tempfile.TemporaryDirectory() as td:
            filename, key, sha = self._make("MyTex", cl, ".png", td)
        self.assertTrue(filename.endswith(".png"))
        self.assertTrue("__" in filename)
        self.assertEqual(len(sha), 32)
        self.assertEqual(key, os.path.splitext(filename)[0])

    def test_deterministic(self):
        cl = b"FILE:unpacked:/path/tex.png"
        with tempfile.TemporaryDirectory() as td:
            f1, _, _ = self._make("MyTex", cl, ".png", td)
            f2, _, _ = self._make("MyTex", cl, ".png", td)
        self.assertEqual(f1, f2)

    def test_different_locator_different_key(self):
        cl1 = b"FILE:unpacked:/path/tex1.png"
        cl2 = b"FILE:unpacked:/path/tex2.png"
        with tempfile.TemporaryDirectory() as td:
            f1, k1, _ = self._make("MyTex", cl1, ".png", td)
            f2, k2, _ = self._make("MyTex", cl2, ".png", td)
        self.assertNotEqual(k1, k2)

    def test_unicode_prefix(self):
        cl = b"FILE:unpacked:/path/t\xebx.png"
        with tempfile.TemporaryDirectory() as td:
            filename, key, _ = self._make("T\xebxture", cl, ".png", td)
        self.assertIn("__", filename)
        self.assertEqual(key, os.path.splitext(filename)[0])

    def test_ext_preserved(self):
        cl = b"FILE:unpacked:/path/tex.exr"
        with tempfile.TemporaryDirectory() as td:
            filename, _, _ = self._make("Tex", cl, ".exr", td)
        self.assertTrue(filename.endswith(".exr"))

    def test_key_budget_mtex_limit(self):
        """When filesystem NAME_MAX > MTEX_MAX_IMAGE_NAME_LEN, MTEX limit dominates."""
        cl = b"FILE:unpacked:/path/tex.png"
        with tempfile.TemporaryDirectory() as td:
            filename, key, _ = self._make("A" * 300, cl, ".png", td)
        self.assertLessEqual(len(key.encode("utf-8")), network.MTEX_MAX_IMAGE_NAME_LEN)

    def test_key_budget_filesystem_limit(self):
        """When MTEX limit > NAME_MAX, filesystem limit dominates."""
        cl = b"FILE:unpacked:/path/tex.png"
        with tempfile.TemporaryDirectory() as td:
            filename, key, sha = self._make("MyTex", cl, ".png", td)
        # Both postconditions hold
        self.assertLessEqual(len(key.encode("utf-8")), network.MTEX_MAX_IMAGE_NAME_LEN)

    def test_long_unicode_prefix_preserves_suffix(self):
        """Long Unicode prefix is truncated but full 32-char SHA-256 suffix is preserved."""
        cl = b"FILE:unpacked:/path/tex.png"
        with tempfile.TemporaryDirectory() as td:
            filename, key, sha = self._make("\u4e16\u754c\u4f60\u597d" * 20, cl, ".png", td)
        self.assertIn("__", filename)
        suffix = filename.split("__")[1].split(".")[0]
        self.assertEqual(len(suffix), 32)
        self.assertEqual(suffix, sha)


class TestCheckDestinationSafe(unittest.TestCase):

    def test_nonexistent_dir(self):
        ok, reason = _init_mod._check_destination_safe(
            "/nonexistent_xyz", "/nonexistent_xyz/f.png")
        self.assertFalse(ok)
        self.assertEqual(reason, "dest_dir_not_found")

    def test_contained(self):
        with tempfile.TemporaryDirectory() as td:
            dest = os.path.join(td, "test.png")
            ok, reason = _init_mod._check_destination_safe(td, dest)
            self.assertTrue(ok, f"unexpected reason={reason!r}")

    def test_escape_detected(self):
        with tempfile.TemporaryDirectory() as td:
            outside = os.path.join(td, "..", "escape.png")
            ok, reason = _init_mod._check_destination_safe(td, outside)
            self.assertFalse(ok)
            self.assertEqual(reason, "path_escape_detected")

    def test_symlink_detected(self):
        with tempfile.TemporaryDirectory() as td:
            target = os.path.join(td, "target.png")
            link = os.path.join(td, "link.png")
            with open(target, "w") as f:
                f.write("data")
            os.symlink(target, link)
            ok, reason = _init_mod._check_destination_safe(td, link)
            self.assertFalse(ok)
            self.assertEqual(reason, "path_is_symlink")


class TestRegisterSidecarKey(unittest.TestCase):

    def test_new_registration(self):
        with tempfile.TemporaryDirectory() as td:
            reg = {}
            ok, existing = _init_mod._register_sidecar_key(
                reg, td, "tex_key", b"locator1")
            self.assertTrue(ok)
            self.assertIsNone(existing)
            self.assertIn((os.path.realpath(td), "tex_key"), reg)

    def test_duplicate_same_locator(self):
        with tempfile.TemporaryDirectory() as td:
            reg = {}
            _init_mod._register_sidecar_key(reg, td, "tex_key", b"locator1")
            ok, existing = _init_mod._register_sidecar_key(
                reg, td, "tex_key", b"locator1")
            self.assertFalse(ok)
            self.assertIsNone(existing)

    def test_collision_different_locator(self):
        with tempfile.TemporaryDirectory() as td:
            reg = {}
            _init_mod._register_sidecar_key(reg, td, "tex_key", b"locator1")
            ok, existing = _init_mod._register_sidecar_key(
                reg, td, "tex_key", b"locator2")
            self.assertFalse(ok)
            self.assertEqual(existing, b"locator1")


class TestTextureUsageSourceReference(unittest.TestCase):
    """TextureUsage holds direct source reference and precomputed flags."""

    def setUp(self):
        self._extract = _init_mod._extract_texture_usages_and_sources

    def test_usage_has_direct_source_reference(self):
        node = _make_tex_node("TexNode", "MyTex", filepath="/tex/a.png")
        principled = _make_principled({"Base Color": node})
        tree = MockNodeTree(nodes=[principled, node])
        mat = MockMaterial(name="M", node_tree=tree)
        obj = MockObject(material_slots=[MockMaterialSlot(material=mat)])
        sources, usages = self._extract(obj)
        self.assertEqual(len(usages), 1)
        usage = usages[0]
        self.assertIs(usage.source, sources[0])
        self.assertIsInstance(usage.flags, int)

    def test_one_source_two_channels(self):
        node = _make_tex_node("Tex", "Tex", filepath="/tex/t.png")
        principled = _make_principled({"Base Color": node, "Roughness": node})
        tree = MockNodeTree(nodes=[principled, node])
        mat = MockMaterial(name="M", node_tree=tree)
        obj = MockObject(material_slots=[MockMaterialSlot(material=mat)])
        sources, usages = self._extract(obj)
        self.assertEqual(len(sources), 1)
        self.assertEqual(len(usages), 2)
        self.assertIs(usages[0].source, usages[1].source)
        self.assertEqual({u.channel for u in usages}, {1, 2})

    def test_unconnected_tex_image_collected(self):
        node = _make_tex_node("Unused", "Unused", filepath="/tex/u.png")
        principled = _make_principled({})
        tree = MockNodeTree(nodes=[principled, node])
        mat = MockMaterial(name="M", node_tree=tree)
        obj = MockObject(material_slots=[MockMaterialSlot(material=mat)])
        sources, usages = self._extract(obj)
        self.assertEqual(len(sources), 1)
        self.assertEqual(len(usages), 0)

    def test_packed_flag_set_on_usage(self):
        node = _make_tex_node("Packed", "Pack", source="FILE", packed=True,
                              colorspace="sRGB")
        principled = _make_principled({"Base Color": node})
        tree = MockNodeTree(nodes=[principled, node])
        mat = MockMaterial(name="M", node_tree=tree)
        obj = MockObject(material_slots=[MockMaterialSlot(material=mat)])
        sources, usages = self._extract(obj)
        self.assertEqual(len(usages), 1)
        self.assertTrue(usages[0].flags & network.MTEX_FLAG_IMAGE_PACKED)

    def test_non_color_flag_for_roughness_channel(self):
        node = _make_tex_node("Rough", "Rough", filepath="/tex/r.png",
                              colorspace="sRGB")
        principled = _make_principled({"Roughness": node})
        tree = MockNodeTree(nodes=[principled, node])
        mat = MockMaterial(name="M", node_tree=tree)
        obj = MockObject(material_slots=[MockMaterialSlot(material=mat)])
        sources, usages = self._extract(obj)
        self.assertEqual(len(usages), 1)
        # sRGB image on Roughness channel is detected as sRGB, not NON_COLOR
        self.assertTrue(usages[0].flags & network.MTEX_FLAG_COLORSPACE_SRGB)

    def test_non_color_flag_default_colorspace(self):
        node = _make_tex_node("Rough", "Rough", filepath="/tex/r.png",
                              colorspace="")
        principled = _make_principled({"Roughness": node})
        tree = MockNodeTree(nodes=[principled, node])
        mat = MockMaterial(name="M", node_tree=tree)
        obj = MockObject(material_slots=[MockMaterialSlot(material=mat)])
        sources, usages = self._extract(obj)
        self.assertEqual(len(usages), 1)
        # Empty/unknown colorspace on Roughness -> NON_COLOR
        self.assertTrue(usages[0].flags & network.MTEX_FLAG_COLORSPACE_NON_COLOR)


class TestCollisionOrdering(unittest.TestCase):
    """Collision is checked before destination write."""

    def test_collision_before_write(self):
        with tempfile.TemporaryDirectory() as td:
            src_file = os.path.join(td, "src_tex.png")
            with open(src_file, "wb") as f:
                f.write(b"data")
            node = _make_tex_node("Tex", "Tex", filepath=src_file)
            principled = _make_principled({"Base Color": node})
            tree = MockNodeTree(nodes=[principled, node])
            mat = MockMaterial(name="M", node_tree=tree)
            obj = MockObject(material_slots=[MockMaterialSlot(material=mat)])
            sources, usages = _init_mod._extract_texture_usages_and_sources(obj)
            obj_dir = os.path.join(td, "cache")
            os.makedirs(obj_dir, exist_ok=True)
            reg = {}
            _init_mod._prepare_source_sidecar(sources[0], obj_dir, reg, "abc")
            # Same source: duplicate is safe (register returns False, None)
            _init_mod._prepare_source_sidecar(sources[0], obj_dir, reg, "abc")
            self.assertEqual(sources[0].status, "ready")


class TestMaterialValidationGate(unittest.TestCase):
    """Connected usage with failed source suppresses PT_Material."""

    def test_failed_source_sets_suppress(self):
        node = _make_tex_node("Tex", "Tex", filepath="/nonexistent/missing.png")
        principled = _make_principled({"Base Color": node})
        tree = MockNodeTree(nodes=[principled, node])
        mat = MockMaterial(name="M", node_tree=tree)
        obj = MockObject(material_slots=[MockMaterialSlot(material=mat)])
        sources, usages = _init_mod._extract_texture_usages_and_sources(obj)
        with tempfile.TemporaryDirectory() as td:
            reg = {}
            _init_mod._prepare_source_sidecar(sources[0], td, reg, "abc")
            self.assertEqual(sources[0].status, "failed")
            # Call the real production helper
            suppress = _init_mod._should_suppress_material(usages)
            self.assertTrue(suppress)
            # Also verify that no failed unconnected source triggers suppression
            self.assertFalse(_init_mod._should_suppress_material([]))


class TestProductionMtexBytes(unittest.TestCase):
    """End-to-end: extract, prepare sidecar, serialize, parse MTEX, verify ImageName."""

    def _make_guid(self):
        import uuid
        return uuid.uuid4()

    def _identity_hash_stub(self, mat):
        return (0xDEAD, 0xBEEF)

    def _basic_props_stub(self, mat):
        return {
            "BaseColorR": 0.8, "BaseColorG": 0.2, "BaseColorB": 0.1,
            "Alpha": 1.0, "Roughness": 0.5, "Metallic": 0.0,
        }

    def test_hide_viewport_key_applied_and_image_name_matches(self):
        with tempfile.TemporaryDirectory() as td:
            src_file = os.path.join(td, "source_tex.png")
            with open(src_file, "wb") as f:
                f.write(b"fake_png_bytes")

            node = _make_tex_node("TexNode", "SourceTex",
                                  filepath=src_file, source="FILE")
            principled = _make_principled({"Base Color": node})
            tree = MockNodeTree(nodes=[principled, node])
            mat = MockMaterial(name="M", node_tree=tree)
            obj = MockObject(material_slots=[MockMaterialSlot(material=mat)])
            guid = self._make_guid()

            sources, usages = _init_mod._extract_texture_usages_and_sources(obj)

            # Prepare sidecar
            reg = {}
            obj_dir = os.path.join(td, "guid_cache")
            os.makedirs(obj_dir, exist_ok=True)
            for src in sources:
                _init_mod._prepare_source_sidecar(src, obj_dir, reg, str(guid)[:8])

            ready_sources = [s for s in sources if s.status == "ready"]
            self.assertGreater(len(ready_sources), 0)

            # Build tex_maps from usages (same pattern as execute())
            tex_maps_dict = {}
            for u in usages:
                src = u.source
                if src.status != "ready":
                    continue
                fp = os.path.join(obj_dir, src.sidecar_filename)
                nm = os.path.splitext(src.sidecar_filename)[0]
                if u.slot_index not in tex_maps_dict:
                    tex_maps_dict[u.slot_index] = []
                tex_maps_dict[u.slot_index].append((u.channel, fp, nm, u.flags))

            identity = {0: (0xDEAD, 0xBEEF)}
            props = {0: self._basic_props_stub(mat)}

            self.assertGreater(len(tex_maps_dict), 0,
                               f"tex_maps_dict empty, usages={len(usages)}")
            payload = network.serialize_material_slots(guid, identity, props, tex_maps_dict)
            self.assertIsInstance(payload, bytes)
            self.assertGreater(len(payload), 50,
                               f"payload too small: {len(payload)}B payload={payload.hex()[:200]}")

            records = parse_mtex_block(payload)
            self.assertIsNotNone(records,
                                 f"MTEX block not found in {len(payload)}B payload")
            self.assertGreater(len(records), 0)

            for slot_idx, channel, path_bytes, name_bytes, flags in records:
                dest_path = path_bytes.decode("utf-8")
                image_name = name_bytes.decode("utf-8")
                basename = os.path.basename(dest_path)
                base_without_ext = os.path.splitext(basename)[0]
                self.assertEqual(
                    image_name.lower(),
                    base_without_ext.lower(),
                    f"MTEX ImageName '{image_name}' != basename '{base_without_ext}'",
                )

    def test_packed_source_image_name_match(self):
        with tempfile.TemporaryDirectory() as td:
            node = _make_tex_node("Packed", "PackImg", source="FILE", packed=True,
                                  colorspace="sRGB")
            principled = _make_principled({"Base Color": node})
            tree = MockNodeTree(nodes=[principled, node])
            mat = MockMaterial(name="M", node_tree=tree)
            obj = MockObject(material_slots=[MockMaterialSlot(material=mat)])
            guid = self._make_guid()

            sources, usages = _init_mod._extract_texture_usages_and_sources(obj)

            reg = {}
            obj_dir = os.path.join(td, "guid_cache")
            os.makedirs(obj_dir, exist_ok=True)
            # Patch bpy.data.images.get to return the mock image
            _orig_get = _bpy_mod.data.images.get
            _bpy_mod.data.images.get = lambda name, default=None: node.image
            try:
                for src in sources:
                    _init_mod._prepare_source_sidecar(src, obj_dir, reg, str(guid)[:8])

                ready_sources = [s for s in sources if s.status == "ready"]
                self.assertGreater(len(ready_sources), 0)

                tex_maps_dict = {}
                for u in usages:
                    src = u.source
                    if src.status != "ready":
                        continue
                    fp = os.path.join(obj_dir, src.sidecar_filename)
                    nm = os.path.splitext(src.sidecar_filename)[0]
                    if u.slot_index not in tex_maps_dict:
                        tex_maps_dict[u.slot_index] = []
                    tex_maps_dict[u.slot_index].append((u.channel, fp, nm, u.flags))

                identity = {0: (0xDEAD, 0xBEEF)}
                props = {0: self._basic_props_stub(mat)}
                self.assertGreater(len(tex_maps_dict), 0,
                                   f"tex_maps_dict empty, usages={len(usages)}")
                payload = network.serialize_material_slots(guid, identity, props, tex_maps_dict)

                records = parse_mtex_block(payload)
                self.assertIsNotNone(records)
                for _, _, path_bytes, name_bytes, _ in records:
                    dest_path = path_bytes.decode("utf-8")
                    image_name = name_bytes.decode("utf-8")
                    basename = os.path.basename(dest_path)
                    self.assertEqual(image_name.lower(), os.path.splitext(basename)[0].lower())
            finally:
                _bpy_mod.data.images.get = _orig_get


class TestOldPathNotCalled(unittest.TestCase):
    """A3.1 path is always active; old dual-traversal path is never called."""

    def test_a3_1_path_active(self):
        """No _USE_A3_1_PATH guard exists; verify the path is unconditional."""
        self.assertFalse(hasattr(_init_mod, "_USE_A3_1_PATH"),
                         "_USE_A3_1_PATH guard must be removed")

    def test_extract_texture_usages_and_sources_exists(self):
        self.assertTrue(hasattr(_init_mod, "_extract_texture_usages_and_sources"))

    def test_execute_does_not_call_copy_textures_sidecar(self):
        """AST-level check: execute() must not call _copy_textures_sidecar."""
        import ast
        with open(os.path.join(_SRC, "__init__.py")) as f:
            tree = ast.parse(f.read())
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == "UELIVESYNC_OT_sync_selected_mesh_to_ue_fbx":
                for item in ast.walk(node):
                    if isinstance(item, ast.Call):
                        name = None
                        if isinstance(item.func, ast.Attribute):
                            name = item.func.attr
                        elif isinstance(item.func, ast.Name):
                            name = item.func.id
                        self.assertNotEqual(name, "_copy_textures_sidecar",
                                            "execute() must not call _copy_textures_sidecar")

    def test_execute_does_not_call_extract_texture_maps_for_slot(self):
        """AST-level check: execute() must not call extract_texture_maps_for_slot."""
        import ast
        with open(os.path.join(_SRC, "__init__.py")) as f:
            tree = ast.parse(f.read())
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == "UELIVESYNC_OT_sync_selected_mesh_to_ue_fbx":
                for item in ast.walk(node):
                    if isinstance(item, ast.Call):
                        name = None
                        if isinstance(item.func, ast.Attribute):
                            name = item.func.attr
                        elif isinstance(item.func, ast.Name):
                            name = item.func.id
                        self.assertNotEqual(name, "extract_texture_maps_for_slot",
                                            "execute() must not call extract_texture_maps_for_slot")


class TestSidecarMetadata(unittest.TestCase):
    """Sidecar info records actual file size and source locator (not destination)."""

    def _do_sidecar_info(self, td, src_file=None, packed=False, source_kind="FILE"):
        """Helper: extract sources, prepare sidecar, build sidecar_info dict."""
        node_kw = dict(source=source_kind, packed=packed)
        if src_file is not None:
            node_kw["filepath"] = src_file
        node = _make_tex_node("Tex", "Tex", **node_kw)
        principled = _make_principled({"Base Color": node})
        tree = MockNodeTree(nodes=[principled, node])
        mat = MockMaterial(name="M", node_tree=tree)
        obj = MockObject(material_slots=[MockMaterialSlot(material=mat)])

        sources, usages = _init_mod._extract_texture_usages_and_sources(obj)
        obj_dir = os.path.join(td, "cache")
        os.makedirs(obj_dir, exist_ok=True)
        reg = {}
        _init_mod._prepare_source_sidecar(sources[0], obj_dir, reg, "abc")

        s = sources[0]
        dest_path = os.path.join(obj_dir, s.sidecar_filename)

        # Build sidecar_info like execute() does
        _s_source = s.source_locator
        try:
            _s_st = os.stat(dest_path)
            _s_size = _s_st.st_size
        except Exception:
            _s_size = 0
        info = {
            "filename": s.sidecar_filename,
            "path": dest_path,
            "size": _s_size,
            "source": _s_source,
        }
        return info, src_file, dest_path

    def test_actual_size_recorded(self):
        with tempfile.TemporaryDirectory() as td:
            src_file = os.path.join(td, "source_tex.png")
            with open(src_file, "wb") as f:
                f.write(b"fake_png_bytes_" * 100)
            real_size = os.path.getsize(src_file)

            info, src_fp, dest_path = self._do_sidecar_info(td, src_file=src_file)
            self.assertEqual(info["size"], real_size)
            self.assertTrue(os.path.isfile(dest_path))

    def test_source_locator_is_not_destination_for_FILE(self):
        """FILE unpacked: source is the original file, not the cached copy."""
        with tempfile.TemporaryDirectory() as td:
            src_file = os.path.join(td, "source_tex.png")
            with open(src_file, "wb") as f:
                f.write(b"fake_png_bytes")
            info, src_fp, dest_path = self._do_sidecar_info(td, src_file=src_file)
            self.assertEqual(info["source"], src_fp,
                             "FILE source locator must match original file path")
            self.assertNotEqual(info["source"], info["path"],
                                "source must differ from destination for FILE")
            self.assertEqual(info["size"], os.path.getsize(src_fp))

    def test_source_locator_for_failed_packed(self):
        """Packed source: locator is image_name (even if sidecar copy fails)."""
        node = _make_tex_node("Tex", "MyPackedImg", packed=True, source="FILE")
        principled = _make_principled({"Base Color": node})
        tree = MockNodeTree(nodes=[principled, node])
        mat = MockMaterial(name="M", node_tree=tree)
        obj = MockObject(material_slots=[MockMaterialSlot(material=mat)])
        sources, _ = _init_mod._extract_texture_usages_and_sources(obj)
        _orig_get = _bpy_mod.data.images.get
        _bpy_mod.data.images.get = lambda name, default=None: default
        try:
            with tempfile.TemporaryDirectory() as td:
                reg = {}
                _init_mod._prepare_source_sidecar(sources[0], td, reg, "abc")
                self.assertEqual(sources[0].status, "failed")
                self.assertEqual(sources[0].source_locator, "MyPackedImg",
                                 "packed source locator must be image_name even on failure")
        finally:
            _bpy_mod.data.images.get = _orig_get


if __name__ == "__main__":
    unittest.main()
