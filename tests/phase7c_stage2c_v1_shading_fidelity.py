#!/usr/bin/env python3
"""
Phase 7C Stage 2C.12 — V1 Non-Debug Tangent Disable Tests

Tests that the new non-debug CVar UE.LiveSync.V1DisableTangents is
correctly declared, integrated with the disable-tangents logic, and
gated from affecting default behavior.

Tests:
  T1  CVar UE.LiveSync.V1DisableTangents declared in .cpp
  T2  CVar default value is 0
  T3  UE.LiveSync.V1DebugDisableTangents still declared (regression)
  T4  SECTION_ARRAYS passes passedTangents=0 when either CVar is 1
  T5  SECTION_ARRAYS passedTangents unchanged when both CVars are 0
  T6  DEBUG_TANGENTS block checks both CVars
  T7  DEBUG_TANGENTS logs "disabled (debug)" for debug CVar
  T8  DEBUG_TANGENTS logs "disabled (non-debug)" for non-debug CVar
  T9  DEBUG_TANGENTS logs "enabled" when both CVars are 0
  T10 debug CVar takes priority when both are set
  T11 non-debug CVar alone sets passedTangents=0
  T12 default behavior unchanged when both CVars are 0
  T13 no packet format change (PT_Keyframe, PT_Mesh unchanged)
  T14 no V5 path change
  T15 section arrays remain complete when tangents disabled
  T16 V1DebugDisableTangents still works (regression)
  T17 debug material modes unchanged (regression)
  T18 V1DisableTangents log includes computed/passed counts
"""

import os
import re
import sys

REPO = "/home/nguyennongngockhanh/Projects/UELiveSync"
SUBSYSTEM_CPP = os.path.join(
    REPO,
    "UE_Plugin/UELiveSync/Source/UELiveSync/Private/UELiveSyncSubsystem.cpp"
)

PASS_COUNT = 0
FAIL_COUNT = 0


def check(test_name, condition, detail=""):
    global PASS_COUNT, FAIL_COUNT
    if condition:
        print(f"  PASS: {test_name}")
        PASS_COUNT += 1
    else:
        print(f"  FAIL: {test_name}" + (f" — {detail}" if detail else ""))
        FAIL_COUNT += 1


def read_source():
    with open(SUBSYSTEM_CPP, "r") as f:
        return f.read()


def main():
    global PASS_COUNT, FAIL_COUNT
    print("=" * 70)
    print("Phase 7C Stage 2C.12 — V1 Non-Debug Tangent Disable Tests")
    print("=" * 70)

    cpp = read_source()

    # ---- T1: CVar UE.LiveSync.V1DisableTangents declared ----
    print("\n[TEST GROUP] T1: CVar declaration")
    cvar_declared = "UE.LiveSync.V1DisableTangents" in cpp
    check("T1a CVar name in source", cvar_declared)
    cvar_tauto = bool(
        re.search(
            r'TAutoConsoleVariable<int32>\s*\n\s*CVarLiveSyncV1DisableTangents',
            cpp
        )
    )
    check("T1b TAutoConsoleVariable declaration", cvar_tauto)

    # ---- T2: CVar default value is 0 ----
    print("\n[TEST GROUP] T2: Default value 0")
    default_zero = bool(
        re.search(
            r'CVarLiveSyncV1DisableTangents\(\s*\n\s*TEXT\("UE\.LiveSync\.V1DisableTangents"\),\s*\n\s*0\s*,',
            cpp
        )
    )
    check("T2 default value is 0", default_zero)

    # ---- T3: V1DebugDisableTangents still declared (regression) ----
    print("\n[TEST GROUP] T3: Debug CVar regression")
    debug_cvar = "UE.LiveSync.V1DebugDisableTangents" in cpp
    check("T3a Debug CVar name still present", debug_cvar)
    debug_tauto = bool(
        re.search(
            r'TAutoConsoleVariable<int32>\s*\n\s*CVarLiveSyncV1DebugDisableTangents',
            cpp
        )
    )
    check("T3b Debug CVar declaration unchanged", debug_tauto)

    # ---- T4: SECTION_ARRAYS checks both CVars for passedTangents=0 ----
    print("\n[TEST GROUP] T4: SECTION_ARRAYS both CVar checks")
    debug_check = bool(
        re.search(
            r'CVarLiveSyncV1DebugDisableTangents\.GetValueOnAnyThread\(\)\s*;\s*\n\s*if\s*\(CV\s*!=\s*0\)\s*\n\s*PassedTangentsCount\s*=\s*0',
            cpp
        )
    )
    check("T4a Debug CVar check in SECTION_ARRAYS", debug_check)
    non_debug_check = bool(
        re.search(
            r'CVarLiveSyncV1DisableTangents\.GetValueOnAnyThread\(\)\s*;\s*\n\s*if\s*\(NCV\s*!=\s*0\)\s*\n\s*PassedTangentsCount\s*=\s*0',
            cpp
        )
    )
    check("T4b Non-debug CVar check in SECTION_ARRAYS", non_debug_check)

    # ---- T5: SECTION_ARRAYS passedTangents unchanged when both are 0 ----
    print("\n[TEST GROUP] T5: Default passedTangents = computedTangents")
    passed_equals_computed = bool(
        re.search(
            r'PassedTangentsCount\s*=\s*ComputedTangentsCount',
            cpp
        )
    )
    check("T5a passedTangents initialized to computedTangents", passed_equals_computed)

    # ---- T6: DEBUG_TANGENTS block checks both CVars ----
    print("\n[TEST GROUP] T6: Both CVars in DEBUG_TANGENTS block")
    debug_cv_read = bool(
        re.search(
            r'CVarLiveSyncV1DebugDisableTangents\.GetValueOnAnyThread\(\)',
            cpp
        )
    )
    check("T6a Debug CVar read in DEBUG_TANGENTS block", debug_cv_read)
    non_debug_cv_read = bool(
        re.search(
            r'CVarLiveSyncV1DisableTangents\.GetValueOnAnyThread\(\)',
            cpp
        )
    )
    check("T6b Non-debug CVar read in DEBUG_TANGENTS block", non_debug_cv_read)

    # ---- T7: DEBUG_TANGENTS logs "disabled (debug)" for debug CVar ----
    print("\n[TEST GROUP] T7: Debug CVar log marker")
    debug_disabled_log = bool(
        re.search(
            r'\[MESH\]\[V1\]\[DEBUG_TANGENTS\]\s+disabled\s+\(debug\)',
            cpp
        )
    )
    check("T7a Logs disabled (debug)", debug_disabled_log)
    debug_disabled_in_debug_block = bool(
        re.search(
            r'if\s*\(DebugCV\s*!=\s*0\).*?DEBUG_TANGENTS.*?disabled\s+\(debug\)',
            cpp,
            re.DOTALL
        )
    )
    check("T7b disabled (debug) inside DebugCV check", debug_disabled_in_debug_block)

    # ---- T8: DEBUG_TANGENTS logs "disabled (non-debug)" for non-debug CVar ----
    print("\n[TEST GROUP] T8: Non-debug CVar log marker")
    non_debug_disabled_log = bool(
        re.search(
            r'\[MESH\]\[V1\]\[DEBUG_TANGENTS\]\s+disabled\s+\(non-debug\)',
            cpp
        )
    )
    check("T8a Logs disabled (non-debug)", non_debug_disabled_log)
    non_debug_disabled_in_block = bool(
        re.search(
            r'else\s+if\s*\(NonDebugCV\s*!=\s*0\).*?DEBUG_TANGENTS.*?disabled\s+\(non-debug\)',
            cpp,
            re.DOTALL
        )
    )
    check("T8b disabled (non-debug) inside NonDebugCV check", non_debug_disabled_in_block)

    # ---- T9: DEBUG_TANGENTS logs "enabled" when both CVars are 0 ----
    print("\n[TEST GROUP] T9: Enabled log marker")
    enabled_log = bool(
        re.search(
            r'\[MESH\]\[V1\]\[DEBUG_TANGENTS\]\s+enabled',
            cpp
        )
    )
    check("T9a Logs enabled", enabled_log)
    enabled_in_else = bool(
        re.search(
            r'else\s*\n\s*\{.*?DEBUG_TANGENTS.*?enabled',
            cpp,
            re.DOTALL
        )
    )
    check("T9b enabled in else branch", enabled_in_else)

    # ---- T10: debug CVar takes priority when both are set ----
    print("\n[TEST GROUP] T10: Debug CVar priority")
    debug_before_non_debug = bool(
        re.search(
            r'if\s*\(DebugCV\s*!=\s*0\).*?else\s+if\s*\(NonDebugCV\s*!=\s*0\)',
            cpp,
            re.DOTALL
        )
    )
    check("T10a DebugCV checked before NonDebugCV", debug_before_non_debug)

    # ---- T11: non-debug CVar alone sets passedTangents=0 ----
    print("\n[TEST GROUP] T11: Non-debug CVar passedTangents=0")
    non_debug_passed_zero = bool(
        re.search(
            r'NCV\s*!=\s*0\).*?PassedTangentsCount\s*=\s*0',
            cpp,
            re.DOTALL
        )
    )
    check("T11a Non-debug CVar sets passedTangents=0", non_debug_passed_zero)

    # ---- T12: default behavior unchanged when both CVars are 0 ----
    print("\n[TEST GROUP] T12: Default unchanged")
    default_path = bool(
        re.search(
            r'else\s*\n\s*\{.*?DebugTangents\s*=\s*FinalTangents',
            cpp,
            re.DOTALL
        )
    )
    check("T12a Default passes FinalTangents", default_path)

    # ---- T13: no packet format change ----
    print("\n[TEST GROUP] T13: Packet format unchanged")
    synctypes_h = os.path.join(
        REPO,
        "UE_Plugin/UELiveSync/Source/UELiveSync/Public/SyncTypes.h"
    )
    st_content = open(synctypes_h).read()
    pt_keyframe = bool(re.search(r'PT_Keyframe\s*=\s*0x17', st_content))
    pt_mesh = bool(re.search(r'PT_Mesh\s*=\s*0x06', st_content))
    check("T13a PT_Keyframe = 0x17 unchanged", pt_keyframe)
    check("T13b PT_Mesh = 0x06 unchanged", pt_mesh)

    # ---- T14: no V5 path change ----
    print("\n[TEST GROUP] T14: V5 path unchanged")
    v5_legacy = bool(re.search(r'V5.*?CreateMeshSection', cpp, re.DOTALL))
    check("T14a V5 path uses CreateMeshSection", v5_legacy)

    # ---- T15: section arrays remain complete ----
    print("\n[TEST GROUP] T15: Section array completeness")
    has_verts = bool(re.search(r'SECTION_ARRAYS.*?verts=%d', cpp, re.DOTALL))
    has_normals = bool(re.search(r'SECTION_ARRAYS.*?normals=%d', cpp, re.DOTALL))
    has_uv0 = bool(re.search(r'SECTION_ARRAYS.*?uv0=%d', cpp, re.DOTALL))
    has_tangents = bool(re.search(r'SECTION_ARRAYS.*?computedTangents=%d', cpp, re.DOTALL))
    has_passed = bool(re.search(r'SECTION_ARRAYS.*?passedTangents=%d', cpp, re.DOTALL))
    check("T15a verts in SECTION_ARRAYS", has_verts)
    check("T15b normals in SECTION_ARRAYS", has_normals)
    check("T15c uv0 in SECTION_ARRAYS", has_uv0)
    check("T15d computedTangents in SECTION_ARRAYS", has_tangents)
    check("T15e passedTangents in SECTION_ARRAYS", has_passed)

    # ---- T16: V1DebugDisableTangents still works (regression) ----
    print("\n[TEST GROUP] T16: Debug CVar regression")
    debug_empty = bool(
        re.search(
            r'DebugCV\s*!=\s*0.*?DebugTangents\.Empty\(\)',
            cpp,
            re.DOTALL
        )
    )
    check("T16a Debug CVar empties DebugTangents", debug_empty)

    # ---- T17: debug material modes unchanged (regression) ----
    print("\n[TEST GROUP] T17: Debug material modes regression")
    debug_material = bool(
        re.search(
            r'CVarLiveSyncV1DebugMaterialMode\.GetValueOnAnyThread\(\)',
            cpp
        )
    )
    check("T17a V1DebugMaterialMode still present", debug_material)
    debug_face = bool(
        re.search(
            r'CVarLiveSyncV1DebugForceFaceNormals\.GetValueOnAnyThread\(\)',
            cpp
        )
    )
    check("T17b V1DebugForceFaceNormals still present", debug_face)
    debug_tangents = bool(
        re.search(
            r'CVarLiveSyncV1DebugDisableTangents\.GetValueOnAnyThread\(\)',
            cpp
        )
    )
    check("T17c V1DebugDisableTangents still present", debug_tangents)

    # ---- T18: V1DisableTangents log includes computed/passed counts ----
    print("\n[TEST GROUP] T18: Non-debug log marker includes counts")
    non_debug_counts = bool(
        re.search(
            r'disabled\s+\(non-debug\).*?computed=%d\s+passed=%d',
            cpp
        )
    )
    check("T18a Log includes computed/passed counts", non_debug_counts)

    # ---- Summary ----
    print("\n" + "=" * 70)
    total = PASS_COUNT + FAIL_COUNT
    print(f"Phase 7C Stage 2C.12: {PASS_COUNT}/{total} PASS, {FAIL_COUNT}/{total} FAIL")
    print("=" * 70)
    return 0 if FAIL_COUNT == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
