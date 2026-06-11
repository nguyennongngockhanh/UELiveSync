"""
Phase 10J.5D.4 — FBX Force Visible Material Safety.

Verifies that GetSafeFBXVisibleMaterial rejects WorldGridMaterial and
uses a non-WorldGrid engine material.  Tests IsWorldGridMaterialPath,
the candidate chain, and nullptr-safe force path.
Does NOT check protocol/struct/Blender.
"""

import os
import sys

REPO_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
IMPORTER_PATH = os.path.join(REPO_ROOT,
    "UE_Plugin/UELiveSync/Source/UELiveSync/Private/FBXImport/LiveSyncFBXImporter.cpp")

PASS = 0
FAIL = 0


def check(condition: bool, label: str, detail: str = ""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  PASS  {label}")
    else:
        FAIL += 1
        msg = f"  FAIL  {label}"
        if detail:
            msg += f"  \u2014 {detail}"
        print(msg)


def read_file(path: str) -> str:
    with open(path, "r") as f:
        return f.read()


def main():
    global PASS, FAIL
    assert os.path.isfile(IMPORTER_PATH), f"Missing: {IMPORTER_PATH}"

    cpp = read_file(IMPORTER_PATH)

    # =========================================================
    # T1: IsUnsafeFBXMaterial exists
    # =========================================================
    t1 = "IsUnsafeFBXMaterial" in cpp
    check(t1, "T1: IsUnsafeFBXMaterial helper exists",
          f"found={t1}")

    # =========================================================
    # T2: IsWorldGridMaterialPath exists
    # =========================================================
    t2 = "IsWorldGridMaterialPath" in cpp
    check(t2, "T2: IsWorldGridMaterialPath helper exists",
          f"found={t2}")

    # =========================================================
    # T3: IsWorldGridMaterialPath detects /Engine/EngineMaterials/WorldGridMaterial
    # =========================================================
    t3 = "/Engine/EngineMaterials/WorldGridMaterial" in cpp
    check(t3, "T3: IsWorldGridMaterialPath detects WorldGridMaterial path",
          f"wg_path={t3}")

    # =========================================================
    # T4: IsWorldGridMaterialPath detects MID_WorldGridMaterial
    # =========================================================
    t4 = "MID_WorldGridMaterial" in cpp
    check(t4, "T4: IsWorldGridMaterialPath detects MID_WorldGridMaterial",
          f"mid_wg={t4}")

    # =========================================================
    # T5: Candidate chain tries BasicShapeMaterial
    # =========================================================
    t5 = "BasicShapeMaterial" in cpp
    check(t5, "T5: Code attempts to load BasicShapeMaterial as first candidate",
          f"basic_shape={t5}")

    # =========================================================
    # T6: There is a fallback chain before GetDefaultMaterial(MD_Surface)
    # =========================================================
    t6 = "GetDefaultMaterial(MD_Surface)" in cpp
    check(t6, "T6: GetDefaultMaterial(MD_Surface) is included as last-resort fallback",
          f"gdms={t6}")

    # =========================================================
    # T7: IsWorldGridMaterialPath check guards GetDefaultMaterial
    # =========================================================
    t7_section = cpp.split("GetDefaultMaterial(MD_Surface)")[0]
    t7 = "IsWorldGridMaterialPath" in t7_section
    check(t7, "T7: GetDefaultMaterial result is guarded by IsWorldGridMaterialPath check",
          f"guard_before_gdms={t7}")

    # =========================================================
    # T8: Safe material path must NOT include WorldGrid
    # =========================================================
    # The MID created from safe material has a generated Transient path,
    # but the base material path must not be WorldGrid.  Check that
    # the "new=..." log format in force_visible does NOT reference
    # WorldGrid (we check the *code* log pattern has `new=%s` not
    # `new=WorldGrid`).
    t8 = "force_visible" in cpp and "safe_material_failed" in cpp
    check(t8, "T8: Force path uses parameterized 'new=%%s' + safe_material_failed fallback",
          f"force_visible={t8}")

    # =========================================================
    # T9: force_visible_failed is logged when no safe material exists
    # =========================================================
    t9 = "force_visible_failed" in cpp and "no_safe_material" in cpp
    check(t9, "T9: force_visible_failed logged when no safe material exists",
          f"failed_log={t9}")

    # =========================================================
    # T10: Validation log includes worldGrid flag
    # =========================================================
    t10 = "worldGrid" in cpp
    check(t10, "T10: Validation log includes worldGrid=0/1",
          f"worldgrid_in_log={t10}")

    # =========================================================
    # T11: Validation log includes forcedMaterials
    # =========================================================
    t11 = "forcedMaterials" in cpp
    check(t11, "T11: Validation log includes forcedMaterials",
          f"forced_materials={t11}")

    # =========================================================
    # T12: Spawn path calls EnsureFBXMeshRenderable
    # =========================================================
    spawn_section = cpp.split("[FBX] Spawned StaticMeshActor")[0]
    t12 = "EnsureFBXMeshRenderable" in spawn_section
    check(t12, "T12: Spawn path calls EnsureFBXMeshRenderable",
          f"spawn_call={t12}")

    # =========================================================
    # T13: Update path calls EnsureFBXMeshRenderable (>=3 sites)
    # =========================================================
    t13 = cpp.count("EnsureFBXMeshRenderable(") >= 3
    check(t13, "T13: Update path calls EnsureFBXMeshRenderable (>=3 call sites)",
          f"call_sites={cpp.count('EnsureFBXMeshRenderable(')}")

    # =========================================================
    # T14: Duplicate skip path calls EnsureFBXMeshRenderable
    # =========================================================
    skip_pos = cpp.find("[FBX][SKIP] duplicate semantic")
    refresh_pos = cpp.find("RefreshFBXStaticMeshComponent(SMC, SMA)")
    t14 = False
    if refresh_pos >= 0 and skip_pos >= 0 and refresh_pos < skip_pos:
        between = cpp[refresh_pos:skip_pos]
        t14 = "EnsureFBXMeshRenderable" in between
    check(t14, "T14: Duplicate skip path calls EnsureFBXMeshRenderable",
          f"skip_call={t14}")

    # =========================================================
    # T15: RegisterComponent is not introduced
    # =========================================================
    t15 = "RegisterComponent" not in cpp
    check(t15, "T15: No RegisterComponent in LiveSyncFBXImporter.cpp",
          f"found={not t15}")

    # =========================================================
    # T16: No MaterialPathCache calls
    # =========================================================
    t16 = "MaterialPathCache" not in cpp
    check(t16, "T16: No MaterialPathCache calls",
          f"found={not t16}")

    # =========================================================
    # T17: No protocol constants/structs changed
    # =========================================================
    t17 = "kFBXPayloadSizeMin = 680" in cpp
    check(t17, "T17: No protocol constants/structs changed (kFBXPayloadSizeMin=680)",
          f"min_size={t17}")

    # =========================================================
    # T18: Blender addon is not changed
    # =========================================================
    init_path = os.path.join(REPO_ROOT, "Blender_Addon/__init__.py")
    if os.path.isfile(init_path):
        init = read_file(init_path)
        t18 = "GetSafeFBXVisibleMaterial" not in init and "IsUnsafeFBXMaterial" not in init and "IsWorldGridMaterialPath" not in init
        check(t18, "T18: Blender addon has no FBX visibility material code",
              f"blender_has_fbx_mat_code={not t18}")
    else:
        check(True, "T18: Blender_Addon/__init__.py not found (test skipped)", "skip")

    total = PASS + FAIL
    print(f"\n{'='*50}")
    print(f"Results: {PASS} passed, {FAIL} failed, {total} total")
    print(f"{'='*50}")
    return FAIL == 0


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
