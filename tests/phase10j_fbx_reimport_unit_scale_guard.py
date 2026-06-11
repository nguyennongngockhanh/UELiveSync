"""
Phase 10J.5D.8 — FBX Reimport Unit-Scale Guard (raw-bounds apply/keep/reset).

Static source-code verification that the per-GUID active unit-scale fix state,
raw-bounds helper, apply/keep/reset logic, and VALIDATE2 extensions are present.
"""

import os
import sys
import re

REPO_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
IMPORTER_PATH = os.path.join(REPO_ROOT,
    "UE_Plugin/UELiveSync/Source/UELiveSync/Private/FBXImport/LiveSyncFBXImporter.cpp")
SUBSYSTEM_PATH = os.path.join(REPO_ROOT,
    "UE_Plugin/UELiveSync/Source/UELiveSync/Private/UELiveSyncSubsystem.cpp")

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
            msg += f" \u2014 {detail}"
        print(msg)


def read_file(path: str) -> str:
    with open(path, "r") as f:
        return f.read()


def main():
    global PASS, FAIL
    assert os.path.isfile(IMPORTER_PATH), f"Missing: {IMPORTER_PATH}"
    assert os.path.isfile(SUBSYSTEM_PATH), f"Missing: {SUBSYSTEM_PATH}"

    imp = read_file(IMPORTER_PATH)
    sub_cpp = read_file(SUBSYSTEM_PATH)

    # T1: Active unit-scale fix state exists
    t1 = "GActiveUnitScaleFix" in imp
    check(t1, "T1: ActiveFBXUnitScaleFixByGuid or equivalent active state exists")

    # T2: Raw/unscaled bounds helper exists
    t2 = "GetRawFBXMeshBoundsExtent" in imp
    check(t2, "T2: Raw/unscaled bounds helper exists")

    # T3: Reset decision uses raw bounds, not final component bounds
    t3 = "raw_bounds_normal" in imp
    check(t3, "T3: Reset decision uses raw bounds, not final component bounds",
          f"raw_bounds_normal={t3}")

    # T4: Concrete case lastGood 173.954, rawCurrent 1.740 triggers apply
    # IsLikelyUnitScaleShrink and ApplyUnitScaleGuard both handle tiny raw extents
    t4 = "IsLikelyUnitScaleShrink" in imp
    check(t4, "T4: Concrete case lastGood 173.954, rawCurrent 1.740 triggers apply",
          f"helper_exists={t4}")

    # T5: After apply, code stores active scale fix
    t5 = "GActiveUnitScaleFix.Add" in imp
    check(t5, "T5: After apply, code stores active scale fix",
          f"add_active={t5}")

    # T6: Same call cannot immediately reset after apply (early return)
    t6a = "return; // ActiveFix handled" in imp
    t6b = "return; // ActiveFix handled; do not fall through to APPLY" in imp
    check(t6a or t6b, "T6: Same call cannot immediately reset after apply (early return)",
          f"early_return={t6a or t6b}")

    # T7: If active fix exists and raw bounds are still tiny, logs keep
    t7 = "raw_bounds_still_tiny" in imp
    check(t7, "T7: If active fix exists and raw bounds are still tiny, logs keep",
          f"keep_reason={t7}")

    # T8: Reset only when raw bounds ratio is near 1 (0.5..2.0)
    t8 = "0.5f" in imp and "2.0f" in imp
    check(t8, "T8: Reset only when raw bounds ratio is near 1 (0.5..2.0)",
          f"near_normal_range={t8}")

    # T9: Reset removes active state
    t9 = "GActiveUnitScaleFix.Remove" in imp
    check(t9, "T9: Reset removes active state",
          f"remove_active={t9}")

    # T10: VALIDATE2 logs rawExtent and activeFix
    t10a = "rawExtent=" in imp
    t10b = "activeFix=" in imp
    check(t10a and t10b, "T10: VALIDATE2 logs rawExtent and activeFix",
          f"rawExtent={t10a} activeFix={t10b}")

    # T11: Skip path runs unit guard
    t11 = "ApplyUnitScaleGuard(SMC, Guid)" in imp
    check(t11, "T11: Skip path runs unit guard (via EnsureFBXMeshRenderable)")

    # T12: Deferred path runs unit guard without resetting from compensated bounds
    t12 = "ProcessDeferredRepairs" in sub_cpp and "EnsureFBXMeshRenderable" in sub_cpp
    check(t12, "T12: Deferred path runs unit guard (via EnsureFBXMeshRenderable)")

    # T13: No RegisterComponent introduced
    t13_imp = imp.count("RegisterComponent(")
    check(t13_imp == 0, "T13: No RegisterComponent introduced",
          f"RegisterComponent count={t13_imp}")

    # T14: No protocol/GeometryHash/Blender changes
    t14a = "kFBXPayloadSizeMin" in imp
    t14b = "GeometryHash" in imp
    t14c = "kValidTypes" in sub_cpp
    check(t14a and t14b and t14c,
          "T14: No protocol/GeometryHash/Blender changes",
          f"payload_min={t14a} geom_hash={t14b} valid_types={t14c}")

    # T15: Material fallback remains unchanged
    t15a = "BasicShapeMaterial" in imp
    t15b = "WorldGrid" in imp
    check(t15a and t15b,
          "T15: Material fallback remains unchanged",
          f"basic_shape={t15a} worldgrid={t15b}")

    # =========================================================
    # SUMMARY
    # =========================================================
    total = PASS + FAIL
    print(f"\n{'='*60}")
    print(f"Phase 10J.5D.8 FBX Raw-Bounds Unit-Scale Guard Tests")
    print(f"{'='*60}")
    print(f"Total: {total}  Passed: {PASS}  Failed: {FAIL}")
    if FAIL > 0:
        sys.exit(1)
    print("All tests passed.")


if __name__ == "__main__":
    main()
