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


if __name__ == "__main__":
    print(f"Canonical Texture Identity: {PASS} passed, {FAIL} failed")
    for r in RESULTS:
        print(r)
    sys.exit(0 if FAIL == 0 else 1)
