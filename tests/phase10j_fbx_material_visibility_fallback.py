"""
Phase 10J.5D / 10J.5D.2 / 10J.5D.3 — FBX Material Visibility Fallback + Force Visible + WorldGrid Safety.

Verifies that LiveSyncFBXImporter.cpp contains EnsureFBXMeshRenderable
with proper null-material fallback, imported material forcing, WorldGrid
detection, and post-import validation.  Also checks spawn, update,
skip paths, and IsUnsafeFBXMaterial helper.  No protocol/struct changes.
"""

import os
import sys

REPO_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
IMPORTER_PATH = os.path.join(REPO_ROOT,
    "UE_Plugin/UELiveSync/Source/UELiveSync/Private/FBXImport/LiveSyncFBXImporter.cpp")
IMPORTER_H_PATH = os.path.join(REPO_ROOT,
    "UE_Plugin/UELiveSync/Source/UELiveSync/Public/FBXImport/LiveSyncFBXImporter.h")

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
    for p in [IMPORTER_PATH, IMPORTER_H_PATH]:
        assert os.path.isfile(p), f"Missing: {p}"

    cpp = read_file(IMPORTER_PATH)
    hdr = read_file(IMPORTER_H_PATH)

    # =========================================================
    # T1: EnsureFBXMeshRenderable helper exists
    # =========================================================
    t1 = "EnsureFBXMeshRenderable" in cpp
    check(t1, "T1: LiveSyncFBXImporter.cpp contains EnsureFBXMeshRenderable",
          f"found={t1}")

    # =========================================================
    # T2: Helper checks null material via GetMaterial
    # =========================================================
    t2 = "GetMaterial" in cpp and "null_material" in cpp
    check(t2, "T2: Helper checks null material via SMC->GetMaterial",
          f"GetMaterial={t2}")

    # =========================================================
    # T3: Helper uses IsUnsafeFBXMaterial to detect unsafe materials
    # =========================================================
    t3 = "IsUnsafeFBXMaterial(" in cpp
    check(t3, "T3: Helper uses IsUnsafeFBXMaterial to detect unsafe materials",
          f"IsUnsafeFBXMaterial={t3}")

    # =========================================================
    # T4: Helper creates SafeVisibleMaterial via MID from default material
    # =========================================================
    t4_mid = "UMaterialInstanceDynamic::Create" in cpp
    t4_default = "UMaterial::GetDefaultMaterial(MD_Surface)" in cpp
    check(t4_mid and t4_default,
          "T4: Helper creates SafeVisibleMaterial via MID from UMaterial::GetDefaultMaterial(MD_Surface)",
          f"mid={t4_mid} default={t4_default}")

    # =========================================================
    # T5: Helper handles zero material slots
    # =========================================================
    t5 = "fallback_zero_slots" in cpp
    check(t5, "T5: Helper handles zero material slots (fallback_zero_slots log)",
          f"zero_slots_log={t5}")

    # =========================================================
    # T6: Helper forces SafeMaterial on unsafe materials (WorldGrid + imported)
    # =========================================================
    t6_force = "force_visible" in cpp
    t6_worldgrid = "WorldGrid" in cpp and "worldgrid" in cpp
    t6_unsafe_or_imported = "unsafe_or_imported" in cpp
    check(t6_force and t6_worldgrid and t6_unsafe_or_imported,
          "T6: Helper forces SafeMaterial on unsafe materials (WorldGrid + imported)",
          f"force={t6_force} worldgrid={t6_worldgrid} unsafe_or_imported={t6_unsafe_or_imported}")

    # =========================================================
    # T7: Update path calls EnsureFBXMeshRenderable
    # =========================================================
    t7 = cpp.count("EnsureFBXMeshRenderable(") >= 3
    check(t7, "T7: Update path calls EnsureFBXMeshRenderable after refresh",
          f"call_sites={cpp.count('EnsureFBXMeshRenderable(')}")

    # =========================================================
    # T8: Spawn path calls EnsureFBXMeshRenderable
    # =========================================================
    spawn_section = cpp.split("[FBX] Spawned StaticMeshActor")[0]
    t8 = "EnsureFBXMeshRenderable" in spawn_section
    check(t8, "T8: Spawn path calls EnsureFBXMeshRenderable after SetStaticMesh",
          f"spawn_call={t8}")

    # =========================================================
    # T9: Duplicate skip path calls EnsureFBXMeshRenderable
    # =========================================================
    skip_pos = cpp.find("[FBX][SKIP] duplicate semantic")
    refresh_pos = cpp.find("RefreshFBXStaticMeshComponent(SMC, SMA)")
    t9 = False
    if refresh_pos >= 0 and skip_pos >= 0 and refresh_pos < skip_pos:
        between = cpp[refresh_pos:skip_pos]
        t9 = "EnsureFBXMeshRenderable" in between
    check(t9, "T9: Duplicate skip path calls EnsureFBXMeshRenderable after Refresh",
          f"skip_call={t9}")

    # =========================================================
    # T10: Helper preserves visibility
    # =========================================================
    t10_vis = "SetVisibility(true, true)" in cpp
    t10_hidden = "SetHiddenInGame(false, true)" in cpp
    t10_actor = "SetActorHiddenInGame(false)" in cpp
    check(t10_vis and t10_hidden and t10_actor,
          "T10: Helper sets SetVisibility(true,true) + SetHiddenInGame(false,true) + SetActorHiddenInGame(false)",
          f"vis={t10_vis} hidden={t10_hidden} actor={t10_actor}")

    # =========================================================
    # T11: Helper refreshes render state
    # =========================================================
    t11_bounds = "UpdateBounds" in cpp
    t11_mark = "MarkRenderStateDirty" in cpp
    check(t11_bounds and t11_mark,
          "T11: Helper calls UpdateBounds and MarkRenderStateDirty",
          f"bounds={t11_bounds} mark={t11_mark}")

    # =========================================================
    # T12: Validation log includes forced materials, material0, and worldGrid
    # =========================================================
    t12_validate = "[FBX][VALIDATE]" in cpp
    t12_forced = "forcedMaterials" in cpp
    t12_mat0 = "material0" in cpp
    t12_worldGrid = "worldGrid" in cpp
    check(t12_validate and t12_forced and t12_mat0 and t12_worldGrid,
          "T12: Validation log includes forcedMaterials=N material0=Name worldGrid=0/1",
          f"validate={t12_validate} forced={t12_forced} mat0={t12_mat0} worldGrid={t12_worldGrid}")

    # =========================================================
    # T13: Fallback/force log [FBX][MAT] exists
    # =========================================================
    t13 = "[FBX][MAT]" in cpp
    check(t13, "T13: Fallback/force log [FBX][MAT] exists",
          f"found={t13}")

    # =========================================================
    # T14: RegisterComponent is not introduced
    # =========================================================
    t14 = "RegisterComponent" not in cpp
    check(t14, "T14: No RegisterComponent in LiveSyncFBXImporter.cpp",
          f"found={not t14}")

    # =========================================================
    # T15: No MaterialPathCache calls added
    # =========================================================
    t15 = "MaterialPathCache" not in cpp
    check(t15, "T15: No MaterialPathCache calls in LiveSyncFBXImporter.cpp",
          f"found={not t15}")

    # =========================================================
    # T16: No protocol constants/structs changed
    # =========================================================
    t16 = "kFBXPayloadSizeMin = 680" in cpp
    check(t16,
          "T16: No protocol constants/structs changed (kFBXPayloadSizeMin still 680)",
          f"min_size={t16}")

    # =========================================================
    # T17: GeometryHash code remains unchanged
    # =========================================================
    t17_geom = "GeometryHash" in cpp and "geomHash=%llu" in cpp
    t17_old = "geomHash=0 (old protocol)" in cpp
    check(t17_geom and t17_old,
          "T17: GeometryHash code remains unchanged (geomHash=%llu + old protocol log)",
          f"new_hash={t17_geom} old={t17_old}")

    # =========================================================
    # T18: IsUnsafeFBXMaterial exists
    # =========================================================
    t18 = "IsUnsafeFBXMaterial" in cpp
    check(t18, "T18: IsUnsafeFBXMaterial helper exists",
          f"found={t18}")

    # =========================================================
    # T19: WorldGridMaterial path is checked in IsUnsafeFBXMaterial
    # =========================================================
    t19_wg = "WorldGridMaterial" in cpp and "/Engine/EngineMaterials/WorldGridMaterial" in cpp
    check(t19_wg,
          "T19: IsUnsafeFBXMaterial checks for /Engine/EngineMaterials/WorldGridMaterial",
          f"worldgrid_check={t19_wg}")

    # =========================================================
    # T20: Safe visible material is a MID (not raw default material)
    # =========================================================
    t20 = "AddToRoot" in cpp
    check(t20, "T20: Safe visible material uses AddToRoot to prevent GC",
          f"addtoroot={t20}")

    # =========================================================
    # T21: Helper creates SafeVisibleMaterial only once (static cache)
    # =========================================================
    t21 = "static UMaterialInterface" in cpp
    check(t21, "T21: Safe visible material is cached in static variable",
          f"static_cache={t21}")

    total = PASS + FAIL
    print(f"\n{'='*50}")
    print(f"Results: {PASS} passed, {FAIL} failed, {total} total")
    print(f"{'='*50}")
    return FAIL == 0


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
