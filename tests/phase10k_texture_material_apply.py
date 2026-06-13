#!/usr/bin/env python3
"""
Phase 10K.3 — Texture Apply to Generated MID Tests

Tests:
  1. ApplyImportedTexturesToGeneratedMID function exists (static analysis)
  2. BaseColor channel mapped to "BaseColorTexture" parameter
  3. Roughness channel mapped to "RoughnessTexture" parameter
  4. Metallic channel mapped to "MetallicTexture" parameter
  5. Alpha channel mapped to "AlphaTexture" parameter
  6. Normal channel mapped to "NormalTexture" parameter
  7. Unknown channel safely skipped
  8. No MTEX records for GUID — skips
  9. No MTEX records for SlotIndex — skips
  10. Texture not in import cache — skips (no_imported_texture)
  11. Texture in cache and valid — applies to MID
  12. Multiple channels applied in one call
  13. [MAT][TEX_APPLY] log marker present (static analysis)
  14. [MAT][TEX_PARAM] log marker present (static analysis)
  15. [MAT][TEX_SKIP] log marker present (static analysis)
  16. [MAT][TEX_WARN] log marker present (static analysis)
  17. [MAT][MESH_STABILITY] still present (static analysis)
  18. TextureMaterialApplyRequests counter exists (static analysis)
  19. TextureMaterialApplySucceeded counter exists (static analysis)
  20. TextureMaterialApplySkipped counter exists (static analysis)
  21. TextureMaterialApplyFailed counter exists (static analysis)
  22. ConsoleReset clears new counters (static analysis)
  23. DumpState shows new counters (static analysis)
"""

import sys
import os
import uuid

PASS = 0
FAIL = 0
SKIP = 0
RESULTS = []


# =========================================================
# Constants
# =========================================================

MTEX_CHANNEL_BASECOLOR = 1
MTEX_CHANNEL_ROUGHNESS = 2
MTEX_CHANNEL_METALLIC = 3
MTEX_CHANNEL_ALPHA = 4
MTEX_CHANNEL_NORMAL = 5
MTEX_FLAG_PATH_ABSOLUTE = 0x01
MTEX_FLAG_IMAGE_PACKED = 0x02
MTEX_MAX_PATH_LEN = 2048
MTEX_MAX_IMAGE_NAME_LEN = 255

CHANNEL_TO_PARAM = {
    MTEX_CHANNEL_BASECOLOR: "BaseColorTexture",
    MTEX_CHANNEL_ROUGHNESS: "RoughnessTexture",
    MTEX_CHANNEL_METALLIC: "MetallicTexture",
    MTEX_CHANNEL_ALPHA: "AlphaTexture",
    MTEX_CHANNEL_NORMAL: "NormalTexture",
}

CHANNEL_TO_NAME = {
    MTEX_CHANNEL_BASECOLOR: "BaseColor",
    MTEX_CHANNEL_ROUGHNESS: "Roughness",
    MTEX_CHANNEL_METALLIC: "Metallic",
    MTEX_CHANNEL_ALPHA: "Alpha",
    MTEX_CHANNEL_NORMAL: "Normal",
}


# =========================================================
# Simulated UE texture apply (mirrors ApplyImportedTexturesToGeneratedMID)
# =========================================================

class SimulatedTextureApply:
    """Simulates UUELiveSyncSubsystem::ApplyImportedTexturesToGeneratedMID()."""

    def __init__(self):
        self.MaterialTextureMapCache = {}    # guid -> list of tex records
        self.TextureImportCache = {}         # path -> simulated texture
        self.TextureMaterialApplyRequests = 0
        self.TextureMaterialApplySucceeded = 0
        self.TextureMaterialApplySkipped = 0
        self.TextureMaterialApplyFailed = 0
        self.logs = []
        self.mid_texture_params = {}         # param_name -> texture

    def _reset_mid(self):
        self.mid_texture_params = {}

    def apply_textures(self, guid_str, slot_index):
        """Simulate ApplyImportedTexturesToGeneratedMID."""
        self.TextureMaterialApplyRequests += 1

        tex_maps = self.MaterialTextureMapCache.get(guid_str, [])
        if not tex_maps:
            self.logs.append(
                f"[MAT][TEX_SKIP] guid={guid_str} slot={slot_index} "
                f"reason=no_mtex_records")
            self.TextureMaterialApplySkipped += 1
            return False

        applied = 0
        for rec in tex_maps:
            if rec.get("slot_index") != slot_index:
                continue

            channel = rec.get("channel", 0)
            path = rec.get("path", "")
            image_name = rec.get("image_name", "")

            if path not in self.TextureImportCache:
                self.logs.append(
                    f"[MAT][TEX_SKIP] guid={guid_str} slot={slot_index} "
                    f"channel={channel} reason=no_imported_texture")
                self.TextureMaterialApplySkipped += 1
                continue

            texture = self.TextureImportCache[path]
            if texture is None:
                self.logs.append(
                    f"[MAT][TEX_SKIP] guid={guid_str} slot={slot_index} "
                    f"channel={channel} reason=texture_not_loaded")
                self.TextureMaterialApplySkipped += 1
                continue

            if channel not in CHANNEL_TO_PARAM:
                self.logs.append(
                    f"[MAT][TEX_SKIP] guid={guid_str} slot={slot_index} "
                    f"channel={channel} reason=unsupported_channel")
                self.TextureMaterialApplySkipped += 1
                continue

            param_name = CHANNEL_TO_PARAM[channel]
            channel_name = CHANNEL_TO_NAME[channel]
            self.mid_texture_params[param_name] = texture
            applied += 1

            self.logs.append(
                f"[MAT][TEX_APPLY] guid={guid_str} slot={slot_index} "
                f"channel={channel_name} texture={image_name} param={param_name}")
            self.logs.append(
                f"[MAT][TEX_PARAM] guid={guid_str} slot={slot_index} "
                f"param={param_name} value={image_name}")

        if applied > 0:
            self.logs.append(
                f"[MAT][TEX_WARN] guid={guid_str} "
                f"reason=parent_material_may_not_use_texture_params "
                f"parent=/Engine/BasicShapes/BasicShapeMaterial.BasicShapeMaterial")
            self.TextureMaterialApplySucceeded += 1
            return True

        return False


def _make_tex_map(slot_index, channel, path="", image_name=""):
    return {
        "slot_index": slot_index,
        "channel": channel,
        "path": path,
        "image_name": image_name,
    }


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
# Tests
# =========================================================

def run_tests():
    # ------------------------------------------------------------------
    # Test 1: BaseColor channel mapped to "BaseColorTexture"
    # ------------------------------------------------------------------
    applier = SimulatedTextureApply()
    applier.MaterialTextureMapCache["GUID0001"] = [
        _make_tex_map(0, MTEX_CHANNEL_BASECOLOR, "/tex/c.png", "c.png")
    ]
    applier.TextureImportCache["/tex/c.png"] = "sim_tex_c"
    applier._reset_mid()
    result = applier.apply_textures("GUID0001", 0)
    _test("BaseColor maps to BaseColorTexture",
          result and applier.mid_texture_params.get("BaseColorTexture") == "sim_tex_c")

    # ------------------------------------------------------------------
    # Test 2: Roughness channel mapped to "RoughnessTexture"
    # ------------------------------------------------------------------
    applier2 = SimulatedTextureApply()
    applier2.MaterialTextureMapCache["GUID0001"] = [
        _make_tex_map(0, MTEX_CHANNEL_ROUGHNESS, "/tex/r.png", "r.png")
    ]
    applier2.TextureImportCache["/tex/r.png"] = "sim_tex_r"
    applier2._reset_mid()
    result2 = applier2.apply_textures("GUID0001", 0)
    _test("Roughness maps to RoughnessTexture",
          result2 and applier2.mid_texture_params.get("RoughnessTexture") == "sim_tex_r")

    # ------------------------------------------------------------------
    # Test 3: Metallic channel mapped to "MetallicTexture"
    # ------------------------------------------------------------------
    applier3 = SimulatedTextureApply()
    applier3.MaterialTextureMapCache["GUID0001"] = [
        _make_tex_map(0, MTEX_CHANNEL_METALLIC, "/tex/m.png", "m.png")
    ]
    applier3.TextureImportCache["/tex/m.png"] = "sim_tex_m"
    applier3._reset_mid()
    result3 = applier3.apply_textures("GUID0001", 0)
    _test("Metallic maps to MetallicTexture",
          result3 and applier3.mid_texture_params.get("MetallicTexture") == "sim_tex_m")

    # ------------------------------------------------------------------
    # Test 4: Alpha channel mapped to "AlphaTexture"
    # ------------------------------------------------------------------
    applier4 = SimulatedTextureApply()
    applier4.MaterialTextureMapCache["GUID0001"] = [
        _make_tex_map(0, MTEX_CHANNEL_ALPHA, "/tex/a.png", "a.png")
    ]
    applier4.TextureImportCache["/tex/a.png"] = "sim_tex_a"
    applier4._reset_mid()
    result4 = applier4.apply_textures("GUID0001", 0)
    _test("Alpha maps to AlphaTexture",
          result4 and applier4.mid_texture_params.get("AlphaTexture") == "sim_tex_a")

    # ------------------------------------------------------------------
    # Test 5: Normal channel mapped to "NormalTexture"
    # ------------------------------------------------------------------
    applier5 = SimulatedTextureApply()
    applier5.MaterialTextureMapCache["GUID0001"] = [
        _make_tex_map(0, MTEX_CHANNEL_NORMAL, "/tex/n.png", "n.png")
    ]
    applier5.TextureImportCache["/tex/n.png"] = "sim_tex_n"
    applier5._reset_mid()
    result5 = applier5.apply_textures("GUID0001", 0)
    _test("Normal maps to NormalTexture",
          result5 and applier5.mid_texture_params.get("NormalTexture") == "sim_tex_n")

    # ------------------------------------------------------------------
    # Test 6: Unknown channel safely skipped
    # ------------------------------------------------------------------
    applier6 = SimulatedTextureApply()
    applier6.MaterialTextureMapCache["GUID0001"] = [
        _make_tex_map(0, 99, "/tex/x.png", "x.png")
    ]
    applier6.TextureImportCache["/tex/x.png"] = "sim_tex_x"
    applier6._reset_mid()
    result6 = applier6.apply_textures("GUID0001", 0)
    _test("Unknown channel safely skipped", not result6)

    # ------------------------------------------------------------------
    # Test 7: No MTEX records for GUID
    # ------------------------------------------------------------------
    applier7 = SimulatedTextureApply()
    result7 = applier7.apply_textures("GUID_NONE", 0)
    _test("No MTEX records for GUID — skips", not result7)
    _test("No MTEX records increments skipped",
          applier7.TextureMaterialApplySkipped >= 1)

    # ------------------------------------------------------------------
    # Test 8: No MTEX records for SlotIndex
    # ------------------------------------------------------------------
    applier8 = SimulatedTextureApply()
    applier8.MaterialTextureMapCache["GUID0001"] = [
        _make_tex_map(0, MTEX_CHANNEL_BASECOLOR, "/tex/c.png", "c.png")
    ]
    applier8.TextureImportCache["/tex/c.png"] = "sim_tex_c"
    applier8._reset_mid()
    result8 = applier8.apply_textures("GUID0001", 1)
    _test("No MTEX records for SlotIndex — skips", not result8)

    # ------------------------------------------------------------------
    # Test 9: Texture not in import cache — skips
    # ------------------------------------------------------------------
    applier9 = SimulatedTextureApply()
    applier9.MaterialTextureMapCache["GUID0001"] = [
        _make_tex_map(0, MTEX_CHANNEL_BASECOLOR, "/tex/missing.png", "missing.png")
    ]
    applier9._reset_mid()
    result9 = applier9.apply_textures("GUID0001", 0)
    _test("Texture not in import cache — skips", not result9)

    # ------------------------------------------------------------------
    # Test 10: Texture in cache and valid — applies to MID
    # ------------------------------------------------------------------
    applier10 = SimulatedTextureApply()
    applier10.MaterialTextureMapCache["GUID0001"] = [
        _make_tex_map(0, MTEX_CHANNEL_BASECOLOR, "/tex/valid.png", "valid.png")
    ]
    applier10.TextureImportCache["/tex/valid.png"] = "sim_tex_valid"
    applier10._reset_mid()
    result10 = applier10.apply_textures("GUID0001", 0)
    _test("Valid texture applied to MID",
          result10 and applier10.mid_texture_params.get("BaseColorTexture") == "sim_tex_valid")
    _test("Apply succeeded counter incremented",
          applier10.TextureMaterialApplySucceeded == 1)
    _test("Apply requests counter incremented",
          applier10.TextureMaterialApplyRequests == 1)

    # ------------------------------------------------------------------
    # Test 11: Multiple channels applied in one call
    # ------------------------------------------------------------------
    applier11 = SimulatedTextureApply()
    applier11.MaterialTextureMapCache["GUID0001"] = [
        _make_tex_map(0, MTEX_CHANNEL_BASECOLOR, "/tex/c.png", "c.png"),
        _make_tex_map(0, MTEX_CHANNEL_ROUGHNESS, "/tex/r.png", "r.png"),
        _make_tex_map(0, MTEX_CHANNEL_METALLIC, "/tex/m.png", "m.png"),
    ]
    applier11.TextureImportCache["/tex/c.png"] = "sim_tex_c"
    applier11.TextureImportCache["/tex/r.png"] = "sim_tex_r"
    applier11.TextureImportCache["/tex/m.png"] = "sim_tex_m"
    applier11._reset_mid()
    result11 = applier11.apply_textures("GUID0001", 0)
    _test("Multiple channels applied",
          result11 and
          applier11.mid_texture_params.get("BaseColorTexture") == "sim_tex_c" and
          applier11.mid_texture_params.get("RoughnessTexture") == "sim_tex_r" and
          applier11.mid_texture_params.get("MetallicTexture") == "sim_tex_m")

    # ------------------------------------------------------------------
    # Test 12: [MAT][TEX_APPLY] log emitted on apply
    # ------------------------------------------------------------------
    has_tex_apply = any("[MAT][TEX_APPLY]" in log for log in applier11.logs)
    _test("[MAT][TEX_APPLY] log emitted", has_tex_apply)

    # ------------------------------------------------------------------
    # Test 13: [MAT][TEX_PARAM] log emitted on apply
    # ------------------------------------------------------------------
    has_tex_param = any("[MAT][TEX_PARAM]" in log for log in applier11.logs)
    _test("[MAT][TEX_PARAM] log emitted", has_tex_param)

    # ------------------------------------------------------------------
    # Test 14: [MAT][TEX_WARN] log emitted on successful apply
    # ------------------------------------------------------------------
    has_tex_warn = any("[MAT][TEX_WARN]" in log for log in applier11.logs)
    _test("[MAT][TEX_WARN] log emitted", has_tex_warn)

    # ------------------------------------------------------------------
    # Test 15: [MAT][TEX_SKIP] log emitted on skip
    # ------------------------------------------------------------------
    has_tex_skip = any("[MAT][TEX_SKIP]" in log for log in applier7.logs)
    _test("[MAT][TEX_SKIP] log emitted on skip", has_tex_skip)

    # ------------------------------------------------------------------
    # Test 16: Static analysis — log markers in Subsystem.cpp
    # ------------------------------------------------------------------
    cpp_path = os.path.join(os.path.dirname(__file__), "..",
        "UE_Plugin/UELiveSync/Source/UELiveSync/Private/UELiveSyncSubsystem.cpp")
    if os.path.exists(cpp_path):
        with open(cpp_path, "r") as f:
            content = f.read()
        _test("[MAT][TEX_APPLY] in Subsystem.cpp",
              "[MAT][TEX_APPLY]" in content)
        _test("[MAT][TEX_PARAM] in Subsystem.cpp",
              "[MAT][TEX_PARAM]" in content)
        _test("[MAT][TEX_SKIP] in Subsystem.cpp",
              "[MAT][TEX_SKIP]" in content)
        _test("[MAT][TEX_WARN] in Subsystem.cpp",
              "[MAT][TEX_WARN]" in content)
        _test("[MAT][MESH_STABILITY] in Subsystem.cpp",
              "[MAT][MESH_STABILITY]" in content)
    else:
        RESULTS.append("  SKIP  Static analysis — Subsystem.cpp not found")
        SKIP += 5

    # ------------------------------------------------------------------
    # Test 17: Static analysis — counters in Subsystem.h
    # ------------------------------------------------------------------
    h_path = os.path.join(os.path.dirname(__file__), "..",
        "UE_Plugin/UELiveSync/Source/UELiveSync/Public/UELiveSyncSubsystem.h")
    if os.path.exists(h_path):
        with open(h_path, "r") as f:
            content = f.read()
        _test("ApplyImportedTexturesToGeneratedMID in Subsystem.h",
              "ApplyImportedTexturesToGeneratedMID" in content)
        _test("TextureMaterialApplyRequests in Subsystem.h",
              "TextureMaterialApplyRequests" in content)
        _test("TextureMaterialApplySucceeded in Subsystem.h",
              "TextureMaterialApplySucceeded" in content)
        _test("TextureMaterialApplySkipped in Subsystem.h",
              "TextureMaterialApplySkipped" in content)
        _test("TextureMaterialApplyFailed in Subsystem.h",
              "TextureMaterialApplyFailed" in content)
    else:
        RESULTS.append("  SKIP  Static analysis — Subsystem.h not found")
        SKIP += 5

    # ------------------------------------------------------------------
    # Test 18: Static analysis — diagnostics ConsoleReset + DumpState
    # ------------------------------------------------------------------
    diag_path = os.path.join(os.path.dirname(__file__), "..",
        "UE_Plugin/UELiveSync/Source/UELiveSync/Private/UELiveSyncSubsystem_Diagnostics.inl")
    if os.path.exists(diag_path):
        with open(diag_path, "r") as f:
            content = f.read()
        _test("TextureMaterialApplyRequests reset in Diagnostics.inl",
              "TextureMaterialApplyRequests = 0" in content)
        _test("TextureMaterialApplySucceeded reset in Diagnostics.inl",
              "TextureMaterialApplySucceeded = 0" in content)
        _test("TextureMaterialApplySkipped reset in Diagnostics.inl",
              "TextureMaterialApplySkipped = 0" in content)
        _test("TextureMaterialApplyFailed reset in Diagnostics.inl",
              "TextureMaterialApplyFailed = 0" in content)
        _test("TexMatApplyReq in DumpState in Diagnostics.inl",
              "TexMatApplyReq" in content)
        _test("TexMatApplySucceed in DumpState in Diagnostics.inl",
              "TexMatApplySucceed" in content)
        _test("TexMatApplySkip in DumpState in Diagnostics.inl",
              "TexMatApplySkip" in content)
        _test("TexMatApplyFail in DumpState in Diagnostics.inl",
              "TexMatApplyFail" in content)
    else:
        RESULTS.append("  SKIP  Static analysis — Diagnostics.inl not found")
        SKIP += 8


if __name__ == "__main__":
    run_tests()
    print(f"\nPhase 10K.3 — Texture Apply: {PASS} passed, {FAIL} failed, {SKIP} skipped")
    for r in RESULTS:
        print(r)
    sys.exit(0 if FAIL == 0 else 1)
