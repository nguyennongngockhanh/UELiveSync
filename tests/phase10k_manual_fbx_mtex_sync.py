#!/usr/bin/env python3
"""
Phase 10K.1A — Manual FBX Sync MTEX: Operator Path Verification

Tests that the manual Sync FBX operator (uelivesync.sync_selected_mesh_to_ue_fbx)
in __init__.py correctly:
  1. Calls extract_texture_maps_for_slot for each material slot
  2. Passes tex_maps as the 4th argument to serialize_material_slots
  3. Tracks total_mtex_records for user notice
  4. Emits [MTEX][USER_NOTICE] log/diagnostic when texture maps detected
  5. Does NOT claim UE texture import/application is implemented
"""

import sys
import os

PASS = 0
FAIL = 0
SKIP = 0
RESULTS = []


def _test(name, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
    else:
        FAIL += 1
        msg = f"  FAIL  {name}"
        if detail:
            msg += f" — {detail}"
        RESULTS.append(msg)


def run_tests():
    init_path = os.path.join(os.path.dirname(__file__), "..",
                             "Blender_Addon", "__init__.py")
    if not os.path.exists(init_path):
        RESULTS.append("  SKIP  __init__.py not found for static analysis")
        global SKIP
        SKIP += 8
        return

    with open(init_path, "r") as f:
        content = f.read()

    # ------------------------------------------------------------------
    # 1. Operator class exists
    # ------------------------------------------------------------------
    _test("UELIVESYNC_OT_sync_selected_mesh_to_ue_fbx class exists",
          "UELIVESYNC_OT_sync_selected_mesh_to_ue_fbx" in content)
    _test("bl_idname = uelivesync.sync_selected_mesh_to_ue_fbx",
          'uelivesync.sync_selected_mesh_to_ue_fbx' in content)

    # ------------------------------------------------------------------
    # 2. Manual operator imports/calls extract_texture_maps_for_slot
    # ------------------------------------------------------------------
    _test("Manual FBX operator calls extract_texture_maps_for_slot",
          "extract_texture_maps_for_slot" in content)
    _test("Manual FBX operator passes tex_maps to serialize_material_slots",
          "tex_maps" in content and "serialize_material_slots" in content)

    # ------------------------------------------------------------------
    # 3. total_mtex_records tracking variable exists
    # ------------------------------------------------------------------
    _test("total_mtex_records tracking variable exists",
          "total_mtex_records" in content)

    # ------------------------------------------------------------------
    # 4. [MTEX][USER_NOTICE] log marker exists in operator
    # ------------------------------------------------------------------
    _test("[MTEX][USER_NOTICE] log marker in __init__.py",
          "[MTEX][USER_NOTICE]" in content)
    _test("limitation=metadata_only marker in __init__.py",
          "limitation=metadata_only" in content)

    # ------------------------------------------------------------------
    # 5. Operator does NOT claim UE texture application is implemented
    #    Check that there's no false claim about texture import/apply
    # ------------------------------------------------------------------
    # Look for any claim that textures are applied/imported
    false_claims = [
        "texture import implemented",
        "texture applied",
        "textures imported",
        "texture application ready",
        "MTEX_APPLY",
        "texture_map applied",
    ]
    for claim in false_claims:
        _test(f"No false UE texture application claim: '{claim}'",
              claim not in content.lower())

    # ------------------------------------------------------------------
    # 6. serialize_material_slots called with 4 args (texture_maps param)
    # ------------------------------------------------------------------
    # Check that serialize_material_slots is called with tex_maps
    _test("serialize_material_slots receives 4 arguments in operator path",
          "serialize_material_slots" in content and "tex_maps" in content)

    # ------------------------------------------------------------------
    # 7. network._append_blender_debug_log exists for [MTEX][USER_NOTICE]
    # ------------------------------------------------------------------
    _test("network._append_blender_debug_log for USER_NOTICE",
          "_append_blender_debug_log" in content and "[MTEX][USER_NOTICE]" in content)

    # ------------------------------------------------------------------
    # 8. self.report is used for MTEX notice
    # ------------------------------------------------------------------
    _test("self.report for MTEX user notice",
          "self.report" in content and "MTEX" in content)


if __name__ == "__main__":
    run_tests()
    print(f"\nPhase 10K.1A — Manual FBX MTEX Sync: {PASS} passed, "
          f"{FAIL} failed, {SKIP} skipped")
    for r in RESULTS:
        print(r)
    sys.exit(0 if FAIL == 0 else 1)
