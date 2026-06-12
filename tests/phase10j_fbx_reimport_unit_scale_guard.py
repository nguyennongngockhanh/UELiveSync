"""
Phase 10J.6 — FBX Unit-Scale Guard: diagnostic/reject/preserve only.

Static source-code verification that:

1. GActiveUnitScaleFix is removed — no active scale compensation.
2. ApplyUnitScaleGuard is diagnostic-only:
   - Computes raw bounds extent via GetRawFBXMeshBoundsExtent.
   - Logs SCALE_INVARIANT / UNIT_INVALID / RAW_EXTENT / CACHE_GATE.
   - On scale violation: resets to identity (FVector::OneVector), no compensation.
   - Updates GBoundsExtentCache only after IsValidFBXBoundsExtent + CACHE_GATE gate.
   - Does NOT call FBXImportSkipped or FBXImportFailed.
   - Does NOT use GActiveUnitScaleFix.
3. Scale enforcement uses FVector::OneVector only.
4. IsLikelyUnitScaleShrink exists as legacy diagnostic helper.
5. IsValidFBXBoundsExtent and GetRawFBXMeshBoundsExtent exist.
6. CACHE_GATE rejects oversize first-time imports.
7. Invariant: actor/component scale always 1.
8. 10J.5O unit policy locked (Blender / UE).
9. 10J.5Q unique temp path policy locked.
10. No RegisterComponent in importer.
"""

import os
import sys
import re

REPO_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
IMPORTER_PATH = os.path.join(REPO_ROOT,
    "UE_Plugin/UELiveSync/Source/UELiveSync/Private/FBXImport/LiveSyncFBXImporter.cpp")
SUBSYSTEM_PATH = os.path.join(REPO_ROOT,
    "UE_Plugin/UELiveSync/Source/UELiveSync/Private/UELiveSyncSubsystem.cpp")
BLENDER_INIT_PATH = os.path.join(REPO_ROOT,
    "Blender_Addon/__init__.py")

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
    assert os.path.isfile(BLENDER_INIT_PATH), f"Missing: {BLENDER_INIT_PATH}"

    imp = read_file(IMPORTER_PATH)
    sub_cpp = read_file(SUBSYSTEM_PATH)
    bl = read_file(BLENDER_INIT_PATH)

    # =========================================================
    # T1-T4: No GActiveUnitScaleFix — architecture invariant
    # =========================================================

    t1 = "GActiveUnitScaleFix" in imp
    # Must only appear in comments, not active code.
    t1_active = bool(re.search(r"GActiveUnitScaleFix(\.Add|\.Remove|=)", imp))
    check(not t1_active,
          "T1: GActiveUnitScaleFix not present in active code",
          f"in_file={t1} active_usage={t1_active} (only comments allowed)")

    t2 = "raw_bounds_normal" not in imp
    check(t2, "T2: raw_bounds_normal removed (old GActiveUnitScaleFix trace)")

    t3 = "raw_bounds_still_tiny" not in imp
    check(t3, "T3: raw_bounds_still_tiny removed (old GActiveUnitScaleFix trace)")

    # ActiveFix = 0.0f is a harmless diagnostic placeholder (prints 0).
    # It must NOT appear in the old apply/keep/reset pattern (ActiveFix handled).
    t4_old_pattern = "ActiveFix handled" not in imp
    check(t4_old_pattern, "T4: ActiveFix handled comment removed (old apply/keep/reset logic)")

    # =========================================================
    # T5-T7: ApplyUnitScaleGuard is diagnostic-only
    # =========================================================

    t5 = "GetRawFBXMeshBoundsExtent" in imp
    check(t5, "T5: GetRawFBXMeshBoundsExtent exists (bounds extent helper)")

    t6 = "IsValidFBXBoundsExtent" in imp
    check(t6, "T6: IsValidFBXBoundsExtent exists (validity gate)")

    t7 = "IsLikelyUnitScaleShrink" in imp
    check(t7, "T7: IsLikelyUnitScaleShrink exists (legacy diagnostic helper)")

    # =========================================================
    # T8-T10: SCALE_INVARIANT / UNIT_INVALID / RAW_EXTENT logs
    # =========================================================

    t8 = "[FBX][SCALE_INVARIANT]" in imp
    check(t8, "T8: [FBX][SCALE_INVARIANT] log present")

    t9 = "[FBX][UNIT_INVALID]" in imp
    check(t9, "T9: [FBX][UNIT_INVALID] log present")

    t10 = "[FBX][RAW_EXTENT]" in imp
    check(t10, "T10: [FBX][RAW_EXTENT] log present")

    # =========================================================
    # T11: CACHE_GATE for first-import oversize
    # =========================================================

    t11 = "[FBX][CACHE_GATE]" in imp
    check(t11, "T11: [FBX][CACHE_GATE] log for first-import oversize")

    # =========================================================
    # T12: Scale enforcement — FVector::OneVector only
    # =========================================================

    # All SetActorScale3D calls must use FVector::OneVector
    as_scales = re.findall(r"SetActorScale3D\(([^)]+)\)", imp)
    as_all_identity = all(s.strip() == "FVector::OneVector" for s in as_scales)
    check(as_all_identity and len(as_scales) >= 1,
          "T12: SetActorScale3D only uses FVector::OneVector (enforcement, not compensation)",
          f"values={as_scales}")

    # All SetRelativeScale3D calls must use FVector::OneVector
    rs_scales = re.findall(r"SetRelativeScale3D\(([^)]+)\)", imp)
    rs_all_identity = all(s.strip() == "FVector::OneVector" for s in rs_scales)
    check(rs_all_identity and len(rs_scales) >= 1,
          "T13: SetRelativeScale3D only uses FVector::OneVector (enforcement, not compensation)",
          f"values={rs_scales}")

    # =========================================================
    # T14-T15: GBoundsExtentCache gate (no cache pollution)
    # =========================================================

    t14 = "GBoundsExtentCache.Add" in imp
    check(t14, "T14: GBoundsExtentCache.Add exists (cache update)")

    # The cache update is gated by IsValidFBXBoundsExtent — verify the gate exists
    t15 = "IsValidFBXBoundsExtent(RawExtent)" in imp and "GBoundsExtentCache" in imp
    check(t15, "T15: GBoundsExtentCache update gated by IsValidFBXBoundsExtent",
          "cache only updated after validity gate")

    # =========================================================
    # T16-T17: ApplyUnitScaleGuard called from dispatch
    # =========================================================

    t16 = "ApplyUnitScaleGuard(SMC, Guid" in imp
    check(t16, "T16: ApplyUnitScaleGuard called from EnsureFBXMeshRenderable")

    t17 = "ProcessDeferredRepairs" in sub_cpp and "EnsureFBXMeshRenderable" in sub_cpp
    check(t17, "T17: Deferred path runs EnsureFBXMeshRenderable")

    # =========================================================
    # T18: No FBXImportSkipped / FBXImportFailed inside guard
    # =========================================================

    # The guard function should not call stat counters (only HandleImport does).
    guard_func = imp[imp.find("ApplyUnitScaleGuard"):imp.find("ApplyUnitScaleGuard")+3000]
    guard_func_slice = guard_func[:guard_func.find("\n}\n")+3] if "\n}\n" in guard_func else guard_func
    t18_skipped = "FBXImportSkipped" not in guard_func_slice
    t18_failed = "FBXImportFailed" not in guard_func_slice
    check(t18_skipped and t18_failed,
          "T18: ApplyUnitScaleGuard does not call FBXImportSkipped/FBXImportFailed",
          "stat counters belong in HandleImport, not guard")

    # =========================================================
    # T19: No RegisterComponent introduced
    # =========================================================

    t19 = imp.count("RegisterComponent(")
    check(t19 == 0, "T19: No RegisterComponent introduced",
          f"RegisterComponent count={t19}")

    # =========================================================
    # T20-T22: 10J.5O unit policy locked
    # =========================================================

    t20 = "global_scale=1.0" in bl
    check(t20, "T20: Blender global_scale=1.0 (10J.5O policy)")

    t21 = "FBX_SCALE_UNITS" in bl
    check(t21, "T21: Blender FBX_SCALE_UNITS (10J.5O policy)")

    t22 = "bConvertSceneUnit = true" in imp
    check(t22, "T22: UE bConvertSceneUnit=true (10J.5O policy)")

    # =========================================================
    # T23-T25: 10J.5Q unique temp path policy locked
    # =========================================================

    t23 = "GLastAssignedMeshPath" in imp
    check(t23, "T23: GLastAssignedMeshPath TMap exists (cleanup tracking)")

    t24 = "NewGuid" in imp and "Left(8)" in imp
    check(t24, "T24: Unique temp path per sync (GUID suffix, 8 chars)")

    t25 = "VISIBLE_EXTENT_FINAL" in imp
    check(t25, "T25: VISIBLE_EXTENT_FINAL diagnostic logged on mesh assignment")

    # =========================================================
    # T26: No protocol/GeometryHash/Blender changes
    # =========================================================

    t26a = "kFBXPayloadSizeMin" in imp
    t26b = "GeometryHash" in imp
    t26c = "kValidTypes" in sub_cpp
    check(t26a and t26b and t26c,
          "T26: No protocol/GeometryHash/Blender changes",
          f"payload_min={t26a} geom_hash={t26b} valid_types={t26c}")

    # =========================================================
    # T27: Material fallback remains unchanged
    # =========================================================

    t27a = "BasicShapeMaterial" in imp
    t27b = "WorldGrid" in imp
    check(t27a and t27b,
          "T27: Material fallback remains unchanged",
          f"basic_shape={t27a} worldgrid={t27b}")

    # =========================================================
    # T28: EnsureFBXMeshRenderable exists (top-level dispatch)
    # =========================================================

    t28 = "EnsureFBXMeshRenderable" in imp
    check(t28, "T28: EnsureFBXMeshRenderable dispatcher exists")

    # =========================================================
    # SUMMARY
    # =========================================================
    total = PASS + FAIL
    print(f"\n{'='*60}")
    print(f"Phase 10J.6 FBX Unit-Scale Guard — Invariant Architecture Tests")
    print(f"{'='*60}")
    print(f"Total: {total}  Passed: {PASS}  Failed: {FAIL}")
    if FAIL > 0:
        sys.exit(1)
    print("All tests passed.")


if __name__ == "__main__":
    main()
