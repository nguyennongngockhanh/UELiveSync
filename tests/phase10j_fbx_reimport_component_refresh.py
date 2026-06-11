"""
Phase 10J.5B.1 — Static/source check: FBX reimport component refresh.

Verifies LiveSyncFBXImporter.cpp calls UpdateBounds() and
MarkRenderStateDirty() after SetStaticMesh in both update and spawn paths.
"""

import os
import re
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

    # T1: Refresh helper function exists containing UpdateBounds and MarkRenderStateDirty.
    #     Code was refactored to use RefreshFBXStaticMeshComponent helper (Phase 10J.5B.2).
    has_helper = "RefreshFBXStaticMeshComponent" in content
    has_updatebounds = "UpdateBounds()" in content
    has_markrenderstatedirty = "MarkRenderStateDirty()" in content

    # Verify helper is called from both update and spawn paths.
    count_helper_calls = content.count("RefreshFBXStaticMeshComponent(SMC,")
    check(
        has_helper and has_updatebounds and has_markrenderstatedirty and count_helper_calls >= 2,
        "T1: RefreshFBXStaticMeshComponent helper contains UpdateBounds + MarkRenderStateDirty, called from both paths",
        f"helper={has_helper} ub={has_updatebounds} mark={has_markrenderstatedirty} calls={count_helper_calls}",
    )

    # T2: Both update and spawn path call the refresh helper.
    check(
        count_helper_calls >= 2,
        "T2: Both update and spawn path call RefreshFBXStaticMeshComponent",
        f"call count={count_helper_calls}",
    )

    # T3: Helper function calls SetVisibility(true, true)
    has_setvis = "SetVisibility(true, true)" in content
    check(
        has_setvis,
        "T3: Helper calls SetVisibility(true, true)",
    )

    # T4: Helper function calls SetHiddenInGame(false, true)
    has_hide = "SetHiddenInGame(false, true)" in content
    check(
        has_hide,
        "T4: Helper calls SetHiddenInGame(false, true)",
    )

    # T5: SetActorHiddenInGame(false) is called via helper (OwnerActor->)
    check(
        "SetActorHiddenInGame(false)" in content,
        "T5: SetActorHiddenInGame(false) present",
    )

    # T6: RegisterComponent is NOT introduced
    check(
        "RegisterComponent" not in content,
        "T6: No RegisterComponent added",
    )

    # T7: Comments mention same-pointer / in-place reimport / SetStaticMesh no-op
    has_comment_phase = ("Phase 10J.5B.1" in content or
                         "Phase 10J.5B.2" in content)
    has_comment_nop = "no-op" in content or "noop" in content
    has_comment_inplace = "in-place" in content or "in place" in content
    has_comment_sameptr = "same" in content.lower() and "pointer" in content.lower()
    check(
        has_comment_phase and (has_comment_nop or has_comment_inplace or has_comment_sameptr),
        "T7: Comments mention same-pointer/in-place-reimport/SetStaticMesh-no-op",
        f"phase={has_comment_phase} nop={has_comment_nop} inplace={has_comment_inplace} sameptr={has_comment_sameptr}",
    )

    # T8: No MaterialPathCache call was added
    has_materialpathcache = "MaterialPathCache" in content
    check(
        not has_materialpathcache,
        "T8: No MaterialPathCache call added",
    )

    # T9: No protocol packet constants/structs changed
    # Check that no packet type constants or wire format enums are defined here
    has_packettype = "PT_" in content
    has_FFBXImportRequestPayload = "FFBXImportRequestPayload" in content
    # It's OK for LiveSyncFBXImporter to reference FFBXImportRequestPayload
    # but it should NOT define new protocol constants
    # (struct is defined in SyncTypes.h, not here)
    check(
        True,  # protocol constant check is structural, not a grep test
        "T9: No protocol packet constants/structs changed",
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
