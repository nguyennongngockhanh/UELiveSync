#!/usr/bin/env python3
"""
Phase 10K.6 — Canonical Texture Identity Tests (A1 slice).

Tests get_texture_identity_name() and get_texture_canonical_key()
from network.py. Pure Python only, no Blender dependency.

Required behavior:
  - FILE + unpacked: normalize \\ and /, basename, strip final extension
  - Packed/generated: use image_name
  - Empty filepath falls back to image_name
  - Preserve case for identity, lowercase for canonical
  - Empty input returns ""
  - No exception on malformed input
"""
import sys
import os

sys.path.insert(0,
    os.path.join(os.path.dirname(__file__), "..", "Blender_Addon"))

from network import (
    get_texture_identity_name,
    get_texture_canonical_key,
    MTEX_MAGIC,
    MTEX_VERSION,
    MTEX_CHANNEL_BASECOLOR,
    MTEX_CHANNEL_ROUGHNESS,
    MTEX_CHANNEL_METALLIC,
    MTEX_CHANNEL_ALPHA,
    MTEX_CHANNEL_NORMAL,
    MTEX_FLAG_PATH_ABSOLUTE,
    MTEX_FLAG_IMAGE_PACKED,
    MTEX_FLAG_COLORSPACE_SRGB,
    MTEX_FLAG_COLORSPACE_NON_COLOR,
    MTEX_MAX_PATH_LEN,
    MTEX_MAX_IMAGE_NAME_LEN,
)

PASS = 0
FAIL = 0
RESULTS = []


def _test(name, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        RESULTS.append(f"  PASS  {name}")
    else:
        FAIL += 1
        msg = f"  FAIL  {name}"
        if detail:
            msg += f"  ({detail})"
        RESULTS.append(msg)


# =========================================================
# Tests for get_texture_identity_name
# =========================================================

# A. Unix absolute
result = get_texture_identity_name("FILE", False, "/abs/path/MyTex.png", "dummy")
_test("A Unix absolute identity", result == "MyTex", f"got={result!r}")

result = get_texture_canonical_key("FILE", False, "/abs/path/MyTex.png", "dummy")
_test("A Unix absolute canonical", result == "mytex", f"got={result!r}")

# B. Blender relative
result = get_texture_identity_name("FILE", False, "//textures/MyTex.png", "dummy")
_test("B Blender relative identity", result == "MyTex", f"got={result!r}")

# C. Windows drive backslash
result = get_texture_identity_name("FILE", False, "C:\\textures\\MyTex.png", "dummy")
_test("C Windows drive backslash identity", result == "MyTex", f"got={result!r}")

# D. Windows drive forward slash
result = get_texture_identity_name("FILE", False, "C:/textures/MyTex.png", "dummy")
_test("D Windows drive forward identity", result == "MyTex", f"got={result!r}")

# E. UNC backslash
result = get_texture_identity_name("FILE", False, "\\\\server\\share\\MyTex.png", "dummy")
_test("E UNC backslash identity", result == "MyTex", f"got={result!r}")

# F. UNC forward slash
result = get_texture_identity_name("FILE", False, "//server/share/MyTex.png", "dummy")
_test("F UNC forward identity", result == "MyTex", f"got={result!r}")

# G. Relative backslash
result = get_texture_identity_name("FILE", False, "relative\\folder\\MyTex.png", "dummy")
_test("G Relative backslash identity", result == "MyTex", f"got={result!r}")

# H. Multiple dots
result = get_texture_identity_name("FILE", False, "/tex/my.texture.v2.png", "dummy")
_test("H Multiple dots identity", result == "my.texture.v2", f"got={result!r}")

# I. Empty filepath
result = get_texture_identity_name("FILE", False, "", "DataBlock")
_test("I Empty filepath identity", result == "DataBlock", f"got={result!r}")

# J. Packed with non-empty filepath
result = get_texture_identity_name("FILE", True, "//old/LegacyName.png", "WoodColor")
_test("J Packed nonempty fp identity", result == "WoodColor", f"got={result!r}")

# K. Packed image_name with extension
result = get_texture_identity_name("FILE", True, "", "WoodColor.png")
_test("K Packed image_name ext identity", result == "WoodColor", f"got={result!r}")

# L. Generated
result = get_texture_identity_name("GENERATED", False, "", "GenImage")
_test("L Generated identity", result == "GenImage", f"got={result!r}")

# M. Empty values
result = get_texture_identity_name("", False, "", "")
_test("M1 Empty source+fp identity", result == "", f"got={result!r}")

result = get_texture_identity_name("FILE", False, "", "")
_test("M2 FILE empty both identity", result == "", f"got={result!r}")

# N. Case preservation
result = get_texture_identity_name("FILE", False, "/path/MyTex.png", "dummy")
_test("N1 Identity preserves case", result == "MyTex", f"got={result!r}")

result = get_texture_canonical_key("FILE", False, "/path/MyTex.png", "dummy")
_test("N2 Canonical lowercases", result == "mytex", f"got={result!r}")

result = get_texture_identity_name("FILE", True, "", "UPPER_CASE")
_test("N3 Packed identity preserves case", result == "UPPER_CASE", f"got={result!r}")

result = get_texture_canonical_key("FILE", True, "", "UPPER_CASE")
_test("N4 Packed canonical lowercases", result == "upper_case", f"got={result!r}")

# O. Collision contract (basename-only limitation)
a = get_texture_canonical_key("FILE", False, "/wood/albedo.png", "dummy")
b = get_texture_canonical_key("FILE", False, "/marble/albedo.png", "dummy")
_test("O1 Collision: same basename same canonical", a == b, f"a={a!r} b={b!r}")
_test("O2 Collision: both are 'albedo'", a == "albedo", f"got={a!r}")

# P. Protocol/source invariants
_test("P1 MTEX_MAGIC unchanged", MTEX_MAGIC == 0x4D544558)
_test("P2 MTEX_VERSION unchanged", MTEX_VERSION == 1)
_test("P3 MTEX_CHANNEL_BASECOLOR unchanged", MTEX_CHANNEL_BASECOLOR == 1)
_test("P4 MTEX_CHANNEL_ROUGHNESS unchanged", MTEX_CHANNEL_ROUGHNESS == 2)
_test("P5 MTEX_CHANNEL_METALLIC unchanged", MTEX_CHANNEL_METALLIC == 3)
_test("P6 MTEX_CHANNEL_ALPHA unchanged", MTEX_CHANNEL_ALPHA == 4)
_test("P7 MTEX_CHANNEL_NORMAL unchanged", MTEX_CHANNEL_NORMAL == 5)
_test("P8 MTEX_FLAG_PATH_ABSOLUTE unchanged", MTEX_FLAG_PATH_ABSOLUTE == 0x01)
_test("P9 MTEX_FLAG_IMAGE_PACKED unchanged", MTEX_FLAG_IMAGE_PACKED == 0x02)
_test("P10 MTEX_FLAG_COLORSPACE_SRGB unchanged", MTEX_FLAG_COLORSPACE_SRGB == 0x04)
_test("P11 MTEX_FLAG_COLORSPACE_NON_COLOR unchanged", MTEX_FLAG_COLORSPACE_NON_COLOR == 0x08)
_test("P12 MTEX_MAX_PATH_LEN unchanged", MTEX_MAX_PATH_LEN == 2048)
_test("P13 MTEX_MAX_IMAGE_NAME_LEN unchanged", MTEX_MAX_IMAGE_NAME_LEN == 255)

# Additional edge cases
result = get_texture_identity_name("FILE", False, "/path/NoExt", "dummy")
_test("Q1 No extension identity", result == "NoExt", f"got={result!r}")

result = get_texture_canonical_key("FILE", False, "/path/NoExt", "dummy")
_test("Q2 No extension canonical", result == "noext", f"got={result!r}")

result = get_texture_identity_name("FILE", False, "/path/.hidden", "dummy")
_test("Q3 Dotfile identity preserves leading dot", result == ".hidden", f"got={result!r}")

result = get_texture_identity_name("UNKNOWN", False, "/path/Foo.png", "Fallback")
_test("Q4 Unknown source falls back to image_name", result == "Fallback", f"got={result!r}")

result = get_texture_identity_name("FILE", True, "/path/Foo.png", "PackedName")
_test("Q5 Packed ignores filepath", result == "PackedName", f"got={result!r}")

result = get_texture_identity_name("FILE", False, "/path/tar.gz", "dummy")
_test("Q6 tar.gz strips only final ext", result == "tar", f"got={result!r}")

result = get_texture_canonical_key("FILE", False, "/path/Foo.PNG", "dummy")
_test("Q7 Canonical lowercases extension-stripped name", result == "foo", f"got={result!r}")

# S. Scope invariant: verify A2 symbols NOT imported
scope_failures = []
for sym in ("_collect", "_suppress_summary", "material_verbose_logging",
            "SYNC_TIMING_BLENDER", "manifest", "fingerprint"):
    if sym in dir():
        scope_failures.append(sym)
_test("S1 No A2 scope symbols imported", len(scope_failures) == 0,
      f"unexpected={scope_failures}")

# T. Test that the helper is the real implementation (not a stub)
import inspect
src = inspect.getsource(get_texture_identity_name)
_test("T1 get_texture_identity_name has replace('\\\\\\\\')",
      "replace" in src and "os.path.basename" in src)
_test("T2 get_texture_identity_name checks source + is_packed",
      "source == 'FILE' and not is_packed" in src)

src_canon = inspect.getsource(get_texture_canonical_key)
_test("T3 get_texture_canonical_key calls identity",
      "get_texture_identity_name" in src_canon)


# =========================================================
# A2a: Shared canonical-key wrapper (extracted from __init__.py)
# =========================================================

import ast

# Ensure the network module is available for A2a wrapper extraction
import network

_init_path = os.path.join(
    os.path.dirname(__file__), "..", "Blender_Addon", "__init__.py")
_init_src = open(_init_path).read()
_init_tree = ast.parse(_init_src)

_wrapper_node = None
for _node in ast.walk(_init_tree):
    if isinstance(_node, ast.FunctionDef) and \
       _node.name == "_get_texture_canonical_key":
        _wrapper_node = _node
        break

_wrapper_mod = ast.Module([_wrapper_node], type_ignores=[])
_wrapper_code = compile(_wrapper_mod, "<a2a_wrapper>", "exec")
_ns = {"network": network}
exec(_wrapper_code, _ns)
_get_texture_canonical_key = _ns["_get_texture_canonical_key"]

_wrapper_source = ast.get_source_segment(_init_src, _wrapper_node)


class _FakeImage:
    """Minimal stand-in for a Blender bpy.types.Image."""
    def __init__(self, name):
        self.name = name


# A2a-A: Unix path
_img = _FakeImage("DataBlock")
result = _get_texture_canonical_key(_img, "/textures/MyTex.png", "FILE", False)
_test("A2a-A Unix path", result == "mytex", f"got={result!r}")

# A2a-B: Windows backslash
result = _get_texture_canonical_key(_img, "C:\\textures\\MyTex.png", "FILE", False)
_test("A2a-B Windows backslash", result == "mytex", f"got={result!r}")

# A2a-C: Windows UNC
result = _get_texture_canonical_key(_img, "\\\\server\\share\\MyTex.png", "FILE", False)
_test("A2a-C Windows UNC", result == "mytex", f"got={result!r}")

# A2a-D: Blender relative
_img2 = _FakeImage("DataBlock")
result = _get_texture_canonical_key(_img2, "//textures/MyTex.png", "FILE", False)
_test("A2a-D Blender relative", result == "mytex", f"got={result!r}")

# A2a-E: Packed with non-empty filepath
_img3 = _FakeImage("WoodColor")
result = _get_texture_canonical_key(_img3, "//old/LegacyName.png", "FILE", True)
_test("A2a-E Packed nonempty fp", result == "woodcolor", f"got={result!r}")

# A2a-F: Generated
_img4 = _FakeImage("Generated.Image.png")
result = _get_texture_canonical_key(_img4, "", "GENERATED", False)
_test("A2a-F Generated", result == "generated.image", f"got={result!r}")

# A2a-G: Empty filepath fallback
_img5 = _FakeImage("FallbackTex")
result = _get_texture_canonical_key(_img5, "", "FILE", False)
_test("A2a-G Empty filepath", result == "fallbacktex", f"got={result!r}")

# A2a-H: Multiple dots
_img6 = _FakeImage("DataBlock")
result = _get_texture_canonical_key(_img6, "/textures/my.texture.v2.png", "FILE", False)
_test("A2a-H Multiple dots", result == "my.texture.v2", f"got={result!r}")

# A2a-I: Delegation spy — prove all args forwarded and return propagated
class _NetworkSpy:
    def __init__(self):
        self.source = None
        self.is_packed = None
        self.filepath = None
        self.image_name = None
        self.return_value = "SPY_RETURN_VALUE"
    def get_texture_canonical_key(self, source, is_packed, filepath, image_name):
        self.source = source
        self.is_packed = is_packed
        self.filepath = filepath
        self.image_name = image_name
        return self.return_value

_spy_ns = {"network": _NetworkSpy()}
exec(_wrapper_code, _spy_ns)
_spy_func = _spy_ns["_get_texture_canonical_key"]
_spy_img = _FakeImage("ForwardedImg")
_spy_result = _spy_func(_spy_img, "/actual/path/tex.png", "FILE", True)
_spy = _spy_ns["network"]

_test("A2a-I spy source forwarded", _spy.source == "FILE",
      f"got={_spy.source!r}")
_test("A2a-I spy is_packed forwarded", _spy.is_packed == True,
      f"got={_spy.is_packed!r}")
_test("A2a-I spy filepath forwarded", _spy.filepath == "/actual/path/tex.png",
      f"got={_spy.filepath!r}")
_test("A2a-I spy image_name forwarded",
      _spy.image_name == "ForwardedImg", f"got={_spy.image_name!r}")
_test("A2a-I spy return propagated",
      _spy_result == "SPY_RETURN_VALUE", f"got={_spy_result!r}")

# A2a-J: Scope invariants — verify wrapper body has no A2b+ code
_fobidden_patterns = [
    "fingerprint_map",
    "_compute_texture_fingerprints",
    "_compute_fingerprint_metadata_digest",
    "texture_manifest",
    "TEXTURE_MANIFEST_FILENAME",
    "sidecar_state",
    "SIDECAR_STATE_FILENAME",
    "mtex",
    "mt_basic",
    "SYNC_TIMING_BLENDER",
    "_collect=False",
    "_suppress_summary",
]
_scope_issues = []
for _pat in _fobidden_patterns:
    if _pat in _wrapper_source:
        _scope_issues.append(_pat)
_test("A2a-J No A2b+ code in wrapper body", len(_scope_issues) == 0,
      f"forbidden_in_wrapper={_scope_issues}")

# A2a-K: Production reachability invariant.
# At least one production call-site must exist in the candidate tree.
# If zero, the wrapper would be dead code.
_init_callers = 0
for _node in ast.walk(_init_tree):
    if isinstance(_node, ast.Call):
        _func = _node.func
        if isinstance(_func, ast.Name) and _func.id == '_get_texture_canonical_key':
            _init_callers += 1
        elif isinstance(_func, ast.Attribute) and _func.attr == '_get_texture_canonical_key':
            _init_callers += 1
_test("A2a-K production callers >= 1", _init_callers >= 1,
      f"callers={_init_callers}")


if __name__ == "__main__":
    print(f"Canonical Texture Identity: {PASS} passed, {FAIL} failed")
    for r in RESULTS:
        print(r)
    sys.exit(0 if FAIL == 0 else 1)
