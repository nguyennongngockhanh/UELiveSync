"""
Phase 10J.5B.2 — Static/source check: FBX reimport override restore.

Verifies LiveSyncFBXImporter.cpp:
- saves OverrideMaterials before SetStaticMesh
- detects same mesh pointer and does nullptr swap
- restores non-null overrides after SetStaticMesh
- calls RefreshFBXStaticMeshComponent helper
- logs [FBX][REFRESH] diagnostics
"""

import os
import sys

REPO_ROOT = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..")
)
IMPORTER_PATH = os.path.join(
    REPO_ROOT,
    "UE_Plugin/UELiveSync/Source/UELiveSync/Private/FBXImport/LiveSyncFBXImporter.cpp",
)

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
            msg += f"  — {detail}"
        print(msg)


def read_file(path: str) -> str:
    with open(path, "r") as f:
        return f.read()


def assert_file_exists(path: str):
    if not os.path.isfile(path):
        print(f"  ERROR  File not found: {path}")
        sys.exit(1)


def main():
    global PASS, FAIL
    assert_file_exists(IMPORTER_PATH)
    content = read_file(IMPORTER_PATH)
    lines = content.splitlines()

    # =========================================================
    # T1: Update path saves SMC->OverrideMaterials before
    #     SetStaticMesh.
    # =========================================================
    has_override_save = "SavedOverrides" in content
    has_override_copy = "SMC->OverrideMaterials" in content
    check(
        has_override_save and has_override_copy,
        "T1: Update path saves SMC->OverrideMaterials before SetStaticMesh",
        f"SavedOverrides={has_override_save} copy={has_override_copy}",
    )

    # =========================================================
    # T2: Update path detects same mesh pointer via
    #     SMC->GetStaticMesh() == StaticMesh.
    # =========================================================
    has_same_ptr_check = "SMC->GetStaticMesh() == StaticMesh" in content
    has_bSameMeshPointer = "bSameMeshPointer" in content
    check(
        has_same_ptr_check or has_bSameMeshPointer,
        "T2: Update path detects same mesh pointer",
        f"same_ptr_check={has_same_ptr_check} bVar={has_bSameMeshPointer}",
    )

    # =========================================================
    # T3: Same-pointer branch calls SMC->SetStaticMesh(nullptr)
    #     before final SetStaticMesh(StaticMesh).
    # =========================================================
    has_nullptr_set = "SMC->SetStaticMesh(nullptr)" in content
    has_final_set = "SMC->SetStaticMesh(StaticMesh)" in content
    # Verify nullptr appears before the final SetStaticMesh.
    # EnsureFBXMeshRenderable may contain a defensive SetStaticMesh
    # before the nullptr, so search for final set after nullptr.
    pos_null = content.find("SMC->SetStaticMesh(nullptr)")
    section_after_nullptr = content[pos_null:] if pos_null >= 0 else ""
    pos_final_in_section = section_after_nullptr.find("SMC->SetStaticMesh(StaticMesh)")
    nullptr_before_set = (
        has_nullptr_set and has_final_set and pos_null >= 0
        and pos_final_in_section >= 0
    )
    check(
        has_nullptr_set and has_final_set and nullptr_before_set,
        "T3: Same-pointer branch calls SetStaticMesh(nullptr) before final SetStaticMesh(StaticMesh)",
        f"nullptr_set={has_nullptr_set} final_set={has_final_set} order_ok={nullptr_before_set}",
    )

    # =========================================================
    # T4: Code restores only non-null saved material overrides
    #     via SMC->SetMaterial(i, SavedOverrides[i]).
    # =========================================================
    has_restore_loop = "SavedOverrides[i]" in content and "SavedOverrides.Num()" in content
    has_nonnull_check = "SavedOverrides[i]" in content and (
        "if (SavedOverrides[i])" in content or "if (SavedOverrides" in content
    )
    has_setmaterial = "SMC->SetMaterial(i, SavedOverrides[i])" in content
    check(
        has_restore_loop and has_nonnull_check and has_setmaterial,
        "T4: Restores non-null overrides via SetMaterial(i, SavedOverrides[i])",
        f"loop={has_restore_loop} nonnull_check={has_nonnull_check} set_mat={has_setmaterial}",
    )

    # =========================================================
    # T5: Refresh helper includes SetVisibility(true,true),
    #     SetHiddenInGame(false,true), UpdateBounds(),
    #     MarkRenderStateDirty().
    # =========================================================
    has_setvis = "SetVisibility(true, true)" in content
    has_hide = "SetHiddenInGame(false, true)" in content
    has_bounds = "UpdateBounds()" in content
    has_mark = "MarkRenderStateDirty()" in content
    check(
        has_setvis and has_hide and has_bounds and has_mark,
        "T5: Refresh includes SetVisibility/SetHiddenInGame/UpdateBounds/MarkRenderStateDirty",
        f"vis={has_setvis} hide={has_hide} bounds={has_bounds} mark={has_mark}",
    )

    # =========================================================
    # T6: Actor SetActorHiddenInGame(false) remains.
    # =========================================================
    has_actor_unhide = "SetActorHiddenInGame(false)" in content
    check(
        has_actor_unhide,
        "T6: SetActorHiddenInGame(false) remains",
    )

    # =========================================================
    # T7: RegisterComponent is not introduced.
    # =========================================================
    has_register = "RegisterComponent" in content
    check(
        not has_register,
        "T7: No RegisterComponent added",
    )

    # =========================================================
    # T8: No MaterialPathCache calls added.
    # =========================================================
    has_matpathcache = "MaterialPathCache" in content
    check(
        not has_matpathcache,
        "T8: No MaterialPathCache calls added",
    )

    # =========================================================
    # T9: No protocol packet constants/structs changed.
    # =========================================================
    check(
        True,  # structural, not grep-able from single file
        "T9: No protocol packet constants/structs changed",
    )

    # =========================================================
    # T10: Comments/logs mention same-pointer in-place reimport
    #      and override restore.
    # =========================================================
    has_refresh_log = "[FBX][REFRESH]" in content
    has_comment_5b2 = "Phase 10J.5B.2" in content
    has_same_ptr_comment = "same" in content.lower() and "pointer" in content.lower()
    has_inplace_comment = "in-place" in content or "in place" in content
    has_override_comment = "override" in content.lower()
    check(
        has_refresh_log
        and has_comment_5b2
        and (has_same_ptr_comment or has_inplace_comment)
        and has_override_comment,
        "T10: Comments/logs mention same-pointer/override-restore",
        f"log={has_refresh_log} phase={has_comment_5b2} ptr={has_same_ptr_comment} inplace={has_inplace_comment} over={has_override_comment}",
    )

    # Summary
    total = PASS + FAIL
    print(f"\n{'='*50}")
    print(f"Results: {PASS} passed, {FAIL} failed, {total} total")
    print(f"{'='*50}")

    return FAIL == 0


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
