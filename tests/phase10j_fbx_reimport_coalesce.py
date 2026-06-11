"""
Phase 10J.5C.2 — Static/source check: FBX reimport semantic signature coalesce.

Verifies LiveSyncFBXImporter.cpp:
- FFBXImportSemanticSignature struct with equality operators (Timestamp/FileSize NOT in equality)
- GSemanticSignatureCache static TMap
- ComputeFBXSemanticSignature helper (still collects file timestamp/size for diagnostics)
- Skip check before import when semantic signature matches
- Refresh existing actor on skip path + OnActorCached callback
- FBXImportSkipped counter increment on skip
- Cache update after successful import
- [FBX][SKIP] log marker with same_semantic_signature reason
- [FBX][COALESCE] log markers for non-skip reasons
- No protocol packet constants/structs changed
- All existing transform path code preserved
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
SYNC_TYPES_PATH = os.path.join(
    REPO_ROOT,
    "UE_Plugin/UELiveSync/Source/UELiveSync/Public/SyncTypes.h",
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
            msg += f"  \u2014 {detail}"
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
    assert_file_exists(SYNC_TYPES_PATH)
    content = read_file(IMPORTER_PATH)
    sync_content = read_file(SYNC_TYPES_PATH)

    # =========================================================
    # T1: FFBXImportSemanticSignature struct defined with all
    #     fields (including diagnostic-only Timestamp/FileSize,
    #     and Phase 10J.5F GeometryHash).
    # =========================================================
    has_struct = "struct FFBXImportSemanticSignature" in content
    has_fbxpath = "FbxPath" in content
    has_timestamp = "Timestamp" in content
    has_filesize = "FileSize" in content
    has_vertcount = "VertCount" in content
    has_tricount = "TriCount" in content
    has_matslot = "MatSlotCount" in content
    has_objname = "ObjectName" in content
    has_geomhash = "GeometryHash" in content
    check(
        has_struct and has_fbxpath and has_timestamp and has_filesize
        and has_vertcount and has_tricount and has_matslot and has_objname
        and has_geomhash,
        "T1: FFBXImportSemanticSignature struct with all fields (incl. GeometryHash)",
        f"struct={has_struct} path={has_fbxpath} ts={has_timestamp}"
        f" size={has_filesize} vert={has_vertcount} tri={has_tricount}"
        f" mat={has_matslot} obj={has_objname} geom={has_geomhash}",
    )

    # =========================================================
    # T2: Equality operators defined.
    # =========================================================
    has_eq = "operator==" in content
    has_neq = "operator!=" in content
    check(
        has_eq and has_neq,
        "T2: operator== and operator!= defined on FFBXImportSemanticSignature",
        f"eq={has_eq} neq={has_neq}",
    )

    # =========================================================
    # T3: GSemanticSignatureCache static TMap exists.
    # =========================================================
    has_cache = "GSemanticSignatureCache" in content
    has_tmap = "TMap<FGuid, FFBXImportSemanticSignature>" in content
    check(
        has_cache and has_tmap,
        "T3: GSemanticSignatureCache static TMap<FGuid, FFBXImportSemanticSignature> defined",
        f"cache={has_cache} tmap={has_tmap}",
    )

    # =========================================================
    # T4: ComputeFBXSemanticSignature helper exists and still
    #     collects file timestamp/size for diagnostics.
    # =========================================================
    has_compute = "ComputeFBXSemanticSignature" in content
    has_ifile = "IFileManager" in content
    has_GetTimeStamp = "GetTimeStamp" in content
    has_FileSize = "FileSize" in content
    check(
        has_compute and has_ifile and has_GetTimeStamp and has_FileSize,
        "T4: ComputeFBXSemanticSignature uses IFileManager for diagnostic timestamp and size",
        f"compute={has_compute} ifile={has_ifile} ts={has_GetTimeStamp} size={has_FileSize}",
    )

    # =========================================================
    # T5: Skip check before import — semantic signature match branch.
    # =========================================================
    has_skip_comment = "same_semantic_signature" in content
    has_cached_find = "GSemanticSignatureCache.Find(Request.ObjectGUID)" in content
    has_if_cached = "*CachedSig == CurrentSig" in content
    has_geom_hash_guard = "CurrentSig.GeometryHash != 0" in content
    check(
        has_skip_comment and has_cached_find and has_if_cached and has_geom_hash_guard,
        "T5: Skip check compares cached vs current semantic signature (incl. GeometryHash) before import",
        f"comment={has_skip_comment} find={has_cached_find} if={has_if_cached} geom_guard={has_geom_hash_guard}",
    )

    # =========================================================
    # T6: Skip path refreshes existing actor via RefreshFBXStaticMeshComponent
    #     and calls OnActorCached callback.
    # =========================================================
    has_refresh_on_skip = "RefreshFBXStaticMeshComponent(SMC," in content
    has_find_actor = "Context.FindActor(Request.ObjectGUID)" in content
    has_sma_cast = "Cast<AStaticMeshActor>(ExistingActor)" in content
    has_onactorcached = "Context.OnActorCached(Request.ObjectGUID, SMA)" in content
    check(
        has_refresh_on_skip and has_find_actor and has_sma_cast and has_onactorcached,
        "T6: Skip path calls RefreshFBXStaticMeshComponent and OnActorCached on existing actor",
        f"refresh={has_refresh_on_skip} find={has_find_actor} cast={has_sma_cast} onactor={has_onactorcached}",
    )

    # =========================================================
    # T7: [FBX][SKIP] log marker present.
    # =========================================================
    has_skip_log = "[FBX][SKIP]" in content
    check(
        has_skip_log,
        "T7: [FBX][SKIP] log marker present",
        f"log={has_skip_log}",
    )

    # =========================================================
    # T8: FBXImportSkipped counter incremented on skip.
    # =========================================================
    has_counter_skip = "FBXImportSkipped.fetch_add" in content
    check(
        has_counter_skip,
        "T8: FBXImportSkipped.fetch_add called on skip",
        f"counter={has_counter_skip}",
    )

    # =========================================================
    # T9: FBXImportSkipped counter defined in SyncTypes.h.
    # =========================================================
    has_counter_def = "FBXImportSkipped" in sync_content
    check(
        has_counter_def,
        "T9: FBXImportSkipped counter defined in SyncTypes.h",
        f"defined={has_counter_def}",
    )

    # =========================================================
    # T10: Cache update after successful import.
    # =========================================================
    has_cache_add = "GSemanticSignatureCache.Add(Request.ObjectGUID," in content
    check(
        has_cache_add,
        "T10: GSemanticSignatureCache.Add called after successful import",
        f"cache_add={has_cache_add}",
    )

    # =========================================================
    # T11: ComputeFBXSemanticSignature also called before cache update.
    # =========================================================
    count_compute = content.count("ComputeFBXSemanticSignature(FbxPathStr, Request)")
    check(
        count_compute >= 2,
        "T11: ComputeFBXSemanticSignature called both before skip check and before cache update",
        f"count={count_compute}",
    )

    # =========================================================
    # T12: No protocol packet constants/structs changed.
    # =========================================================
    has_new_packet_type = "PT_FBX" in content and "0x" in content
    check(
        not has_new_packet_type,
        "T12: No new packet type constants added to importer",
        f"new_packet={has_new_packet_type}",
    )

    # =========================================================
    # T13: All existing transform path code preserved.
    # =========================================================
    has_actor_location = "ExistingLocation" in content
    has_actor_rotation = "ExistingRotation" in content
    has_actor_scale = "ExistingScale" in content
    check(
        has_actor_location and has_actor_rotation and has_actor_scale,
        "T13: Existing transform preservation code intact",
        f"loc={has_actor_location} rot={has_actor_rotation} scale={has_actor_scale}",
    )

    # =========================================================
    # T14: Spawn branch still present.
    # =========================================================
    has_spawn_branch = 'World->SpawnActor<AStaticMeshActor>' in content
    check(
        has_spawn_branch,
        "T14: Spawn branch still present",
        f"spawn={has_spawn_branch}",
    )

    # =========================================================
    # T15: No RegisterComponent added.
    # =========================================================
    has_register = "RegisterComponent" in content
    check(
        not has_register,
        "T15: No RegisterComponent added",
        f"register={has_register}",
    )

    # =========================================================
    # T16: No MaterialPathCache calls added.
    # =========================================================
    has_matpathcache = "MaterialPathCache" in content
    check(
        not has_matpathcache,
        "T16: No MaterialPathCache calls added",
        f"matpath={has_matpathcache}",
    )

    # =========================================================
    # T17: Phase 10J.5F comment present.
    # =========================================================
    has_phase_comment = "Phase 10J.5F" in content
    check(
        has_phase_comment,
        "T17: Phase 10J.5F comment present in importer",
        f"comment={has_phase_comment}",
    )

    # =========================================================
    # T18: HAL/FileManager.h included.
    # =========================================================
    has_hal_include = '#include "HAL/FileManager.h"' in content
    check(
        has_hal_include,
        "T18: HAL/FileManager.h include present",
        f"include={has_hal_include}",
    )

    # =========================================================
    # T19: [FBX][COALESCE] log marker present (non-skip reasons).
    # =========================================================
    has_coalesce_log = "[FBX][COALESCE]" in content
    has_actor_missing_reason = "reason=actor_missing" in content
    has_non_static_reason = "reason=non_static_actor" in content
    has_mesh_missing_reason = "reason=mesh_missing" in content
    has_signature_changed = "reason=signature_changed" in content
    has_no_cache_reason = "reason=no_cache" in content
    has_geom_hash_changed = "reason=geometry_hash_changed" in content
    has_geom_hash_missing = "reason=geometry_hash_missing" in content
    check(
        has_coalesce_log and has_actor_missing_reason
        and has_non_static_reason and has_mesh_missing_reason
        and has_signature_changed and has_no_cache_reason
        and has_geom_hash_changed and has_geom_hash_missing,
        "T19: [FBX][COALESCE] log markers present for non-skip reasons (incl. geometry_hash)",
        f"coalesce={has_coalesce_log} missing={has_actor_missing_reason}"
        f" nonsta={has_non_static_reason} mesh={has_mesh_missing_reason}"
        f" changed={has_signature_changed} nocache={has_no_cache_reason}"
        f" geom_hash_changed={has_geom_hash_changed} geom_hash_missing={has_geom_hash_missing}",
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
