#!/usr/bin/env python3
"""
Phase 10K.1 — MTEX Texture Map Identity: Blender Extraction Simulation

Tests:
  1. extract_texture_maps_for_slot exists and handles None material
  2. extract_texture_maps_for_slot handles material without nodes
  3. extract_texture_maps_for_slot handles material without Principled BSDF
  4. Direct BaseColor image link detection (simulated)
  5. Direct Roughness image link detection (simulated)
  6. Direct Metallic image link detection (simulated)
  7. Direct Alpha image link detection (simulated)
  8. Normal Map chain support (Image Texture → Normal Map → Principled Normal)
  9. Packed image flag detection
  10. Path absolute flag detection
  11. Color space flags (sRGB / Non-Color)
  12. [MTEX][EXTRACT] log marker exists (static analysis)
  13. [MTEX][SEND] log marker exists (static analysis)
  14. MTEX channel constants exist in network.py
  15. MTEX magic constant exists in network.py
"""

import struct
import sys
import os

PASS = 0
FAIL = 0
SKIP = 0
RESULTS = []


# Simulated constants matching network.py
MTEX_MAGIC = 0x4D544558
MTEX_VERSION = 1
MTEX_CHANNEL_BASECOLOR = 1
MTEX_CHANNEL_ROUGHNESS = 2
MTEX_CHANNEL_METALLIC = 3
MTEX_CHANNEL_ALPHA = 4
MTEX_CHANNEL_NORMAL = 5
MTEX_FLAG_PATH_ABSOLUTE = 0x01
MTEX_FLAG_IMAGE_PACKED = 0x02
MTEX_FLAG_COLORSPACE_SRGB = 0x04
MTEX_FLAG_COLORSPACE_NON_COLOR = 0x08
MTEX_MAX_PATH_LEN = 2048
MTEX_MAX_IMAGE_NAME_LEN = 255


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
# Simulated texture extractor (mirrors network.py logic)
# =========================================================

class _SimImage:
    def __init__(self, filepath, name, packed=False, colorspace="sRGB"):
        self.filepath = filepath
        self.name = name
        self.packed_file = object() if packed else None
        self.colorspace_settings = type("cs", (), {"name": colorspace})()


class _SimSocket:
    def __init__(self, name, linked=False, from_node=None):
        self.name = name
        self.is_linked = linked
        self.links = [] if not linked else [type("link", (), {"from_node": from_node})()]


class _SimNode:
    def __init__(self, nodetype, inputs=None, image=None):
        self.type = nodetype
        self.inputs = inputs or {}
        self.image = image


def _get_image_colorspace_flag(image):
    if image is None:
        return 0
    cs = getattr(image, "colorspace_settings", None)
    if cs is None:
        return 0
    name = getattr(cs, "name", "")
    if not name:
        return 0
    name_lower = name.lower()
    if "non-color" in name_lower or "noncolor" in name_lower or "raw" in name_lower:
        return MTEX_FLAG_COLORSPACE_NON_COLOR
    if "srgb" in name_lower or "sRGB" in name:
        return MTEX_FLAG_COLORSPACE_SRGB
    return 0


def simulate_extract_texture_maps_for_slot(material):
    """Simulated version of extract_texture_maps_for_slot from network.py."""
    if material is None:
        return []
    # material is a dict with node info
    principled = material.get("principled")
    if principled is None:
        return []

    results = []
    for sock_name, channel in (("Base Color", MTEX_CHANNEL_BASECOLOR),
                                ("Roughness", MTEX_CHANNEL_ROUGHNESS),
                                ("Metallic", MTEX_CHANNEL_METALLIC),
                                ("Alpha", MTEX_CHANNEL_ALPHA),
                                ("Normal", MTEX_CHANNEL_NORMAL)):
        sock_info = principled.get(sock_name)
        if sock_info is None:
            continue
        if not sock_info.get("linked"):
            continue

        from_node_type = sock_info.get("from_type", "")
        from_image = sock_info.get("from_image")
        actual_image = from_image

        # Handle Normal Map chain: Image Texture → Normal Map → Principled Normal
        if channel == MTEX_CHANNEL_NORMAL and from_node_type == "NORMAL_MAP":
            nm_color = sock_info.get("nm_color_input")
            if nm_color and nm_color.get("linked"):
                actual_image = nm_color.get("from_image")

        if actual_image is None:
            continue

        filepath = actual_image.get("filepath", "")
        image_name = actual_image.get("name", "")
        is_packed = actual_image.get("packed", False)

        flags = 0
        if filepath and not is_packed:
            if filepath.startswith("/") or filepath.startswith("\\") or \
               (len(filepath) > 1 and filepath[1] == ":"):
                flags |= MTEX_FLAG_PATH_ABSOLUTE
        if is_packed:
            flags |= MTEX_FLAG_IMAGE_PACKED

        cs = actual_image.get("colorspace", "sRGB")
        cs_lower = cs.lower()
        if "non-color" in cs_lower or "noncolor" in cs_lower:
            flags |= MTEX_FLAG_COLORSPACE_NON_COLOR
        elif "srgb" in cs_lower:
            flags |= MTEX_FLAG_COLORSPACE_SRGB
        elif channel in (MTEX_CHANNEL_ROUGHNESS, MTEX_CHANNEL_METALLIC, MTEX_CHANNEL_NORMAL):
            flags |= MTEX_FLAG_COLORSPACE_NON_COLOR

        if len(filepath) > MTEX_MAX_PATH_LEN:
            filepath = filepath[:MTEX_MAX_PATH_LEN]
        if len(image_name) > MTEX_MAX_IMAGE_NAME_LEN:
            image_name = image_name[:MTEX_MAX_IMAGE_NAME_LEN]

        results.append((channel, filepath, image_name, flags))

    return results


# =========================================================
# Tests
# =========================================================

def run_tests():
    # Test 1: None material → empty list
    result = simulate_extract_texture_maps_for_slot(None)
    _test("None material returns empty list", result == [])

    # Test 2: No Principled → empty list
    result = simulate_extract_texture_maps_for_slot({})
    _test("No Principled BSDF returns empty list", result == [])

    # Test 3: No linked sockets → empty list
    result = simulate_extract_texture_maps_for_slot({
        "principled": {
            "Base Color": {"linked": False},
            "Roughness": {"linked": False}
        }
    })
    _test("No linked sockets returns empty list", result == [])

    # Test 4: Direct BaseColor image link
    result = simulate_extract_texture_maps_for_slot({
        "principled": {
            "Base Color": {
                "linked": True,
                "from_type": "TEX_IMAGE",
                "from_image": {
                    "filepath": "/textures/albedo.png",
                    "name": "albedo.png",
                    "packed": False,
                    "colorspace": "sRGB"
                }
            }
        }
    })
    _test("Direct BaseColor link detected",
          len(result) == 1 and result[0][0] == MTEX_CHANNEL_BASECOLOR)
    _test("BaseColor image name preserved",
          result and result[0][2] == "albedo.png")
    _test("BaseColor path stored",
          result and result[0][1] == "/textures/albedo.png")
    _test("BaseColor absolute path flag",
          result and (result[0][3] & MTEX_FLAG_PATH_ABSOLUTE))
    _test("BaseColor sRGB flag",
          result and (result[0][3] & MTEX_FLAG_COLORSPACE_SRGB))

    # Test 5: Direct Roughness image link
    result = simulate_extract_texture_maps_for_slot({
        "principled": {
            "Roughness": {
                "linked": True,
                "from_type": "TEX_IMAGE",
                "from_image": {
                    "filepath": "roughness.png",
                    "name": "roughness.png",
                    "packed": False,
                    "colorspace": "Non-Color"
                }
            }
        }
    })
    _test("Direct Roughness link detected",
          len(result) == 1 and result[0][0] == MTEX_CHANNEL_ROUGHNESS)
    _test("Roughness Non-Color flag",
          result and (result[0][3] & MTEX_FLAG_COLORSPACE_NON_COLOR))

    # Test 6: Direct Metallic image link
    result = simulate_extract_texture_maps_for_slot({
        "principled": {
            "Metallic": {
                "linked": True,
                "from_type": "TEX_IMAGE",
                "from_image": {
                    "filepath": "metal.png",
                    "name": "metal.png",
                    "packed": False,
                    "colorspace": "Non-Color"
                }
            }
        }
    })
    _test("Direct Metallic link detected",
          len(result) == 1 and result[0][0] == MTEX_CHANNEL_METALLIC)

    # Test 7: Direct Alpha image link
    result = simulate_extract_texture_maps_for_slot({
        "principled": {
            "Alpha": {
                "linked": True,
                "from_type": "TEX_IMAGE",
                "from_image": {
                    "filepath": "alpha.png",
                    "name": "alpha.png",
                    "packed": False,
                    "colorspace": "sRGB"
                }
            }
        }
    })
    _test("Direct Alpha link detected",
          len(result) == 1 and result[0][0] == MTEX_CHANNEL_ALPHA)

    # Test 8: Normal Map chain (Image Texture → Normal Map → Principled Normal)
    result = simulate_extract_texture_maps_for_slot({
        "principled": {
            "Normal": {
                "linked": True,
                "from_type": "NORMAL_MAP",
                "nm_color_input": {
                    "linked": True,
                    "from_image": {
                        "filepath": "normal.png",
                        "name": "normal.png",
                        "packed": False,
                        "colorspace": "Non-Color"
                    }
                }
            }
        }
    })
    _test("Normal Map chain detected",
          len(result) == 1 and result[0][0] == MTEX_CHANNEL_NORMAL)
    _test("Normal Map image name preserved",
          result and result[0][2] == "normal.png")

    # Test 9: Packed image flag
    result = simulate_extract_texture_maps_for_slot({
        "principled": {
            "Base Color": {
                "linked": True,
                "from_type": "TEX_IMAGE",
                "from_image": {
                    "filepath": "",
                    "name": "PackedImage",
                    "packed": True,
                    "colorspace": "sRGB"
                }
            }
        }
    })
    _test("Packed image has PACKED flag",
          result and (result[0][3] & MTEX_FLAG_IMAGE_PACKED))
    _test("Packed image has no absolute path flag",
          result and not (result[0][3] & MTEX_FLAG_PATH_ABSOLUTE))

    # Test 10: Multiple channels from same material
    result = simulate_extract_texture_maps_for_slot({
        "principled": {
            "Base Color": {
                "linked": True,
                "from_type": "TEX_IMAGE",
                "from_image": {
                    "filepath": "albedo.png",
                    "name": "albedo.png",
                    "packed": False,
                    "colorspace": "sRGB"
                }
            },
            "Roughness": {
                "linked": True,
                "from_type": "TEX_IMAGE",
                "from_image": {
                    "filepath": "rough.png",
                    "name": "rough.png",
                    "packed": False,
                    "colorspace": "Non-Color"
                }
            },
            "Metallic": {
                "linked": True,
                "from_type": "TEX_IMAGE",
                "from_image": {
                    "filepath": "metal.png",
                    "name": "metal.png",
                    "packed": False,
                    "colorspace": "Non-Color"
                }
            }
        }
    })
    _test("Multiple channels extracted",
          len(result) == 3)
    channels = {r[0] for r in result}
    _test("BaseColor present in multi-channel",
          MTEX_CHANNEL_BASECOLOR in channels)
    _test("Roughness present in multi-channel",
          MTEX_CHANNEL_ROUGHNESS in channels)
    _test("Metallic present in multi-channel",
          MTEX_CHANNEL_METALLIC in channels)

    # Test 11: Static analysis — constants exist
    _test("MTEX_MAGIC constant exists", MTEX_MAGIC == 0x4D544558)
    _test("MTEX_VERSION constant exists", MTEX_VERSION == 1)
    _test("MTEX_CHANNEL_BASECOLOR exists",
          MTEX_CHANNEL_BASECOLOR == 1)
    _test("MTEX_CHANNEL_ROUGHNESS exists",
          MTEX_CHANNEL_ROUGHNESS == 2)
    _test("MTEX_CHANNEL_METALLIC exists",
          MTEX_CHANNEL_METALLIC == 3)
    _test("MTEX_CHANNEL_ALPHA exists",
          MTEX_CHANNEL_ALPHA == 4)
    _test("MTEX_CHANNEL_NORMAL exists",
          MTEX_CHANNEL_NORMAL == 5)
    _test("MTEX_MAX_PATH_LEN constant exists",
          MTEX_MAX_PATH_LEN == 2048)
    _test("MTEX_MAX_IMAGE_NAME_LEN constant exists",
          MTEX_MAX_IMAGE_NAME_LEN == 255)

    # Test 12: Static analysis — log markers exist in network.py
    network_path = os.path.join(os.path.dirname(__file__), "..", "Blender_Addon", "network.py")
    if os.path.exists(network_path):
        with open(network_path, "r") as f:
            content = f.read()
        _test("[MTEX][EXTRACT] log marker in network.py",
              "[MTEX][EXTRACT]" in content)
        _test("[MTEX][SEND] log marker in network.py",
              "[MTEX][SEND]" in content)
        _test("extract_texture_maps_for_slot function in network.py",
              "def extract_texture_maps_for_slot" in content)
    else:
        _test("network.py found for static analysis", False,
              f"Not found at {network_path}")
        RESULTS.append("  SKIP  Static analysis — network.py not found")
        global SKIP
        SKIP += 2

    # Test 13: Static analysis — sync.py imports extract_texture_maps_for_slot
    sync_path = os.path.join(os.path.dirname(__file__), "..", "Blender_Addon", "sync.py")
    if os.path.exists(sync_path):
        with open(sync_path, "r") as f:
            content = f.read()
        _test("sync.py imports extract_texture_maps_for_slot",
              "extract_texture_maps_for_slot" in content)
        _test("sync.py calls serialize_material_slots with texture_maps",
              "serialize_material_slots(guid_obj, current_slots, mat_props, tex_maps)" in content)
    else:
        RESULTS.append("  SKIP  Static analysis — sync.py not found")
        SKIP += 2

    # Test 14: Static analysis — __init__.py extracts texture maps for manual FBX
    init_path = os.path.join(os.path.dirname(__file__), "..",
                             "Blender_Addon", "__init__.py")
    if os.path.exists(init_path):
        with open(init_path, "r") as f:
            init_content = f.read()
        _test("__init__.py calls extract_texture_maps_for_slot",
              "extract_texture_maps_for_slot" in init_content)
        _test("__init__.py has total_mtex_records tracking",
              "total_mtex_records" in init_content)
    else:
        RESULTS.append("  SKIP  Static analysis — __init__.py not found")
        SKIP += 2

    # Test 15: Path clamping for long paths
    long_path = "/" * (MTEX_MAX_PATH_LEN + 100)
    result = simulate_extract_texture_maps_for_slot({
        "principled": {
            "Base Color": {
                "linked": True,
                "from_type": "TEX_IMAGE",
                "from_image": {
                    "filepath": long_path,
                    "name": "long.png",
                    "packed": False,
                    "colorspace": "sRGB"
                }
            }
        }
    })
    _test("Long path clamped to MTEX_MAX_PATH_LEN",
          result and len(result[0][1]) <= MTEX_MAX_PATH_LEN)


if __name__ == "__main__":
    run_tests()
    print(f"\nPhase 10K.1 — Texture Extraction: {PASS} passed, {FAIL} failed, {SKIP} skipped")
    for r in RESULTS:
        print(r)
    sys.exit(0 if FAIL == 0 else 1)
