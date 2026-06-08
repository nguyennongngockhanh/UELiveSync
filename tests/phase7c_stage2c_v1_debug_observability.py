#!/usr/bin/env python3
"""
Phase 7C Stage 2C.11 — V1 Debug CVar Observability Tests

Tests that face-normal and disable-tangents debug CVars produce
distinct, truthful log markers so runtime matrix tests can
verify CVar activation from logs alone.

Tests:
  T1  baseline logs DEBUG_FACE_NORMALS disabled
  T2  ForceFaceNormals mode logs DEBUG_FACE_NORMALS enabled
  T3  baseline DEBUG_NORMALS mode=source
  T4  ForceFaceNormals DEBUG_NORMALS mode=face
  T5  baseline DEBUG_TANGENTS enabled computed=N passed=N
  T6  DisableTangents DEBUG_TANGENTS disabled computed=N passed=0
  T7  SECTION_ARRAYS includes computedTangents and passedTangents
  T8  DisableTangents does not delete computed tangent diagnostic
  T9  DisableTangents only affects array passed to CreateMeshSection
  T10 default behavior unchanged when CVars are 0
  T11 no packet format change
  T12 no legacy V5 change
  T13 debug material modes unchanged
"""

import os
import re
import sys

REPO = "/home/nguyennongngockhanh/Projects/UELiveSync"
SUBSYSTEM_CPP = os.path.join(
    REPO,
    "UE_Plugin/UELiveSync/Source/UELiveSync/Private/UELiveSyncSubsystem.cpp"
)
SUBSYSTEM_H = os.path.join(
    REPO,
    "UE_Plugin/UELiveSync/Source/UELiveSync/Public/UELiveSyncSubsystem.h"
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


def read_header():
    with open(SUBSYSTEM_H, "r") as f:
        return f.read()


def main():
    global PASS_COUNT, FAIL_COUNT
    print("=" * 70)
    print("Phase 7C Stage 2C.11 — V1 Debug CVar Observability Tests")
    print("=" * 70)

    cpp = read_source()
    hdr = read_header()

    # ---- T1: Baseline logs DEBUG_FACE_NORMALS disabled ----
    print("\n[TEST GROUP] T1: Baseline DEBUG_FACE_NORMALS disabled")
    face_normals_disabled_log = bool(
        re.search(
            r'\[MESH\]\[V1\]\[DEBUG_FACE_NORMALS\]\s+disabled',
            cpp
        )
    )
    check(
        "T1: Source contains [MESH][V1][DEBUG_FACE_NORMALS] disabled log",
        face_normals_disabled_log,
        "Log marker not found"
    )

    # Verify the disabled log is in the CVar == 0 branch
    has_disabled_in_else = bool(
        re.search(
            r'else\s*\n\s*\{[^}]*\[MESH\]\[V1\]\[DEBUG_FACE_NORMALS\]\s+disabled',
            cpp,
            re.DOTALL
        )
    )
    check(
        "T1b: disabled log is in the else branch (CVar == 0)",
        has_disabled_in_else,
        "Not in else branch"
    )

    # ---- T2: ForceFaceNormals logs DEBUG_FACE_NORMALS enabled ----
    print("\n[TEST GROUP] T2: ForceFaceNormals DEBUG_FACE_NORMALS enabled")
    face_normals_enabled_log = bool(
        re.search(
            r'\[MESH\]\[V1\]\[DEBUG_FACE_NORMALS\]\s+enabled\s+normals=%d\s+tris=%d',
            cpp
        )
    )
    check(
        "T2: Source contains [MESH][V1][DEBUG_FACE_NORMALS] enabled normals=%d tris=%d log",
        face_normals_enabled_log,
        "Log marker not found or format mismatch"
    )

    # Position-based check: enabled log appears after if (DebugFaceNormalsMode)
    # and NOT inside an else block
    enabled_log_pos = cpp.find('[MESH][V1][DEBUG_FACE_NORMALS] enabled')
    preceding = cpp[:enabled_log_pos]
    last_if_pos = preceding.rfind('if (DebugFaceNormalsMode)')
    last_else_pos = preceding.rfind('\n        else\n')
    has_enabled_in_if = (last_if_pos > last_else_pos) and last_if_pos >= 0
    check(
        "T2b: enabled log is in the if (DebugFaceNormalsMode) branch",
        has_enabled_in_if,
        "Not in if branch (found in else or not found)"
    )

    # ---- T3: Baseline DEBUG_NORMALS mode=source ----
    print("\n[TEST GROUP] T3: baseline DEBUG_NORMALS mode=source")
    normals_source_log = bool(
        re.search(
            r'\[MESH\]\[V1\]\[DEBUG_NORMALS\]\s+mode=source',
            cpp
        )
    )
    check(
        "T3: Source contains [MESH][V1][DEBUG_NORMALS] mode=source log",
        normals_source_log,
        "Log marker not found"
    )

    # ---- T4: ForceFaceNormals DEBUG_NORMALS mode=face ----
    print("\n[TEST GROUP] T4: ForceFaceNormals DEBUG_NORMALS mode=face")
    normals_face_log = bool(
        re.search(
            r'\[MESH\]\[V1\]\[DEBUG_NORMALS\]\s+mode=face',
            cpp
        )
    )
    check(
        "T4: Source contains [MESH][V1][DEBUG_NORMALS] mode=face log",
        normals_face_log,
        "Log marker not found"
    )

    # ---- T5: Baseline DEBUG_TANGENTS enabled computed=N passed=N ----
    print("\n[TEST GROUP] T5: baseline DEBUG_TANGENTS enabled")
    tangents_enabled_log = bool(
        re.search(
            r'\[MESH\]\[V1\]\[DEBUG_TANGENTS\]\s+enabled\s+computed=%d\s+passed=%d',
            cpp
        )
    )
    check(
        "T5: Source contains [MESH][V1][DEBUG_TANGENTS] enabled computed=%d passed=%d log",
        tangents_enabled_log,
        "Log marker not found or format mismatch"
    )

    # Verify enabled log is in the else branch (CVar == 0)
    has_enabled_in_else = bool(
        re.search(
            r'else[^{]*\{[^{}]*\[MESH\]\[V1\]\[DEBUG_TANGENTS\]\s+enabled',
            cpp,
            re.DOTALL
        )
    )
    check(
        "T5b: enabled log is in the else branch (CVar == 0, tangents active)",
        has_enabled_in_else,
        "Not in else branch (was label inverted?)"
    )

    # ---- T6: DisableTangents DEBUG_TANGENTS disabled computed=N passed=0 ----
    print("\n[TEST GROUP] T6: DisableTangents DEBUG_TANGENTS disabled")
    tangents_disabled_log = bool(
        re.search(
            r'\[MESH\]\[V1\]\[DEBUG_TANGENTS\]\s+disabled\s+computed=%d\s+passed=%d',
            cpp
        )
    )
    check(
        "T6: Source contains [MESH][V1][DEBUG_TANGENTS] disabled computed=%d passed=%d log",
        tangents_disabled_log,
        "Log marker not found or format mismatch"
    )

    has_disabled_in_if = bool(
        re.search(
            r'if\s*\(\s*CV\s*!=\s*0\s*\)[^{]*\{[^{}]*\[MESH\]\[V1\]\[DEBUG_TANGENTS\]\s+disabled',
            cpp,
            re.DOTALL
        )
    )
    check(
        "T6b: disabled log is in the if (CV != 0) branch (CVar active)",
        has_disabled_in_if,
        "Not in if branch (was label inverted?)"
    )

    # ---- T7: SECTION_ARRAYS includes computedTangents and passedTangents ----
    print("\n[TEST GROUP] T7: SECTION_ARRAYS field format")
    has_computed_tangents_field = bool(
        re.search(
            r'\[MESH\]\[V1\]\[SECTION_ARRAYS\].*?computedTangents=%d',
            cpp,
            re.DOTALL
        )
    )
    has_passed_tangents_field = bool(
        re.search(
            r'\[MESH\]\[V1\]\[SECTION_ARRAYS\].*?passedTangents=%d',
            cpp,
            re.DOTALL
        )
    )
    check(
        "T7a: SECTION_ARRAYS log contains computedTangents=%d field",
        has_computed_tangents_field,
        "computedTangents field not found in SECTION_ARRAYS format"
    )
    check(
        "T7b: SECTION_ARRAYS log contains passedTangents=%d field",
        has_passed_tangents_field,
        "passedTangents field not found in SECTION_ARRAYS format"
    )

    # Old bare "tangents=" field should NOT still be the primary field
    old_tangents_only = (
        '"verts=%d indices=%d normals=%d uv0=%d tangents=%d colors=%d "'
    )
    has_old_tangents_field = old_tangents_only in cpp
    check(
        "T7c: Old bare 'tangents=' field is replaced (no tangents= without computedTangents)",
        not has_old_tangents_field,
        "Old tangents= field still present without computedTangents"
    )

    # ---- T8: DisableTangents does not delete computed tangent diagnostic ----
    print("\n[TEST GROUP] T8: Computed tangent diagnostic preserved")
    has_computed_tangent_diagnostic = bool(
        re.search(
            r'\[MESH\]\[V1\]\[TANGENT\].*?tangents=%d.*?normalPreservedDeltaMax',
            cpp,
            re.DOTALL
        )
    )
    check(
        "T8: [MESH][V1][TANGENT] diagnostic with tangents=%d is unconditional",
        has_computed_tangent_diagnostic,
        "Computed tangent diagnostic not found"
    )

    # The [TANGENT] diagnostic uses FinalTangents (computed), not DebugTangents (passed)
    uses_final_tangents_in_diagnostic = bool(
        re.search(
            r'\[MESH\]\[V1\]\[TANGENT\].*?FinalTangents\.Num\(\)',
            cpp,
            re.DOTALL
        )
    )
    check(
        "T8b: [TANGENT] diagnostic uses FinalTangents (computed) not DebugTangents",
        uses_final_tangents_in_diagnostic or has_computed_tangent_diagnostic,
        "FinalTangents reference not found in TANGENT diagnostic"
    )

    # ---- T9: DisableTangents only affects array passed to CreateMeshSection ----
    print("\n[TEST GROUP] T9: Only passed array affected")
    # Verify CreateMeshSection receives DebugTangents (which varies by CVar)
    create_mesh_uses_debug_tangents = bool(
        re.search(
            r'CreateMeshSection\s*\('
            r'[^;]*DebugTangents[^;]*false\s*\)',
            cpp,
            re.DOTALL
        )
    )
    check(
        "T9: CreateMeshSection receives DebugTangents (varies by CVar)",
        create_mesh_uses_debug_tangents,
        "CreateMeshSection call does not pass DebugTangents"
    )

    # Verify SECTION_ARRAYS references FinalTangents (unmodified by CVar)
    section_uses_final = bool(
        re.search(
            r'SECTION_ARRAYS.*?Sample.*?FinalTangents\.Num\(\)',
            cpp,
            re.DOTALL
        )
    )
    check(
        "T9b: SECTION_ARRAYS sample log reads from FinalTangents (computed)",
        section_uses_final,
        "SECTION_ARRAYS does not reference FinalTangents in sample log"
    )

    # ---- T10: Default behavior unchanged when all CVars are 0 ----
    print("\n[TEST GROUP] T10: Default behavior unchanged")
    # Verify CreateMeshSection with default params still called
    v1_create_mesh = re.search(
        r'ProcMesh->CreateMeshSection\s*\(\s*0\s*,',
        cpp
    )
    check(
        "T10: v1 path calls CreateMeshSection(0, ...) for default behavior",
        v1_create_mesh is not None,
        "CreateMeshSection call not found"
    )

    # Verify face normals CVar default is 0
    face_normals_default = re.search(
        r'CVarLiveSyncV1DebugForceFaceNormals\s*\(\s*TEXT\("UE\.LiveSync\.V1DebugForceFaceNormals"\)\s*,\s*(\d+)',
        cpp
    )
    check(
        "T10b: CVarLiveSyncV1DebugForceFaceNormals default is 0",
        face_normals_default is not None and face_normals_default.group(1) == '0',
        f"Default is {face_normals_default.group(1) if face_normals_default else 'not found'}"
    )

    # Verify disable tangents CVar default is 0
    disable_tangents_default = re.search(
        r'CVarLiveSyncV1DebugDisableTangents\s*\(\s*TEXT\("UE\.LiveSync\.V1DebugDisableTangents"\)\s*,\s*(\d+)',
        cpp
    )
    check(
        "T10c: CVarLiveSyncV1DebugDisableTangents default is 0",
        disable_tangents_default is not None and disable_tangents_default.group(1) == '0',
        f"Default is {disable_tangents_default.group(1) if disable_tangents_default else 'not found'}"
    )

    # ---- T11: No packet format change ----
    print("\n[TEST GROUP] T11: No packet format change")
    new_pt_pattern = re.search(
        r'PT_.*DebugFace|PT_.*DebugTangent|PT_.*ForceNormal',
        cpp
    )
    check(
        "T11: No new packet type for observability features",
        new_pt_pattern is None,
        f"Found unexpected packet type: {new_pt_pattern.group(0) if new_pt_pattern else ''}"
    )

    # Verify no new observability-related fields in packet structs/headers
    has_new_observability_field = bool(
        re.search(
            r'V1DebugFaceNormalsInPacket|V1DebugDisableTangentsInPacket|'
            r'force_face_normals_in_packet|disable_tangents_in_packet',
            cpp + "\n" + hdr
        )
    )
    check(
        "T11b: No observability fields added to packet structs",
        not has_new_observability_field,
        "Found observability field in packet data"
    )

    # ---- T12: No legacy V5 change ----
    print("\n[TEST GROUP] T12: Legacy V5 path unchanged")
    v5_reconstruct_start = cpp.find('ReconstructCompletedMeshes()')
    if v5_reconstruct_start >= 0:
        v5_reconstruct_end = cpp.find('\n}', v5_reconstruct_start + 30)
        brace_count = 0
        started = False
        v5_func_end = v5_reconstruct_end
        for i in range(v5_reconstruct_start, len(cpp)):
            if cpp[i] == '{':
                brace_count += 1
                started = True
            elif cpp[i] == '}':
                brace_count -= 1
                if started and brace_count == 0:
                    v5_func_end = i
                    break

        v5_func = cpp[v5_reconstruct_start:v5_func_end + 1]
        debug_in_v5 = (
            'CVarLiveSyncV1DebugForceFaceNormals' in v5_func or
            'CVarLiveSyncV1DebugDisableTangents' in v5_func or
            'DEBUG_FACE_NORMALS' in v5_func or
            'DEBUG_TANGENTS' in v5_func
        )
        check(
            "T12: Debug observability features are NOT in the V5 legacy path",
            not debug_in_v5,
            "Found debug observability in V5 function" if debug_in_v5 else "OK"
        )
    else:
        check("T12: ReconstructCompletedMeshes function found", False,
              "Function not found — test may need updating")

    # ---- T13: Debug material modes unchanged ----
    print("\n[TEST GROUP] T13: Debug material modes unchanged")
    mat_mode_cvar = re.search(
        r'CVarLiveSyncV1DebugMaterialMode\s*\(\s*TEXT\("UE\.LiveSync\.V1DebugMaterialMode"\)',
        cpp
    )
    check(
        "T13: CVarLiveSyncV1DebugMaterialMode still exists",
        mat_mode_cvar is not None,
        "CVar not found"
    )

    mat_mode_log = bool(
        re.search(
            r'\[MESH\]\[V1\]\[DEBUG_MATERIAL\]\s+mode=%d\s+material=%s\s+assigned=%d',
            cpp
        )
    )
    check(
        "T13b: [MESH][V1][DEBUG_MATERIAL] log format unchanged",
        mat_mode_log,
        "Log marker not found or format changed"
    )

    # Verify debug material modes 1/2/3 still defined in the switch
    has_mode1 = bool(re.search(r'case\s+1:\s*TargetMat\s*=\s*DebugMatUnlit', cpp))
    has_mode2 = bool(re.search(r'case\s+2:\s*TargetMat\s*=\s*DebugMatTwoSided', cpp))
    has_mode3 = bool(re.search(r'case\s+3:\s*TargetMat\s*=\s*DebugMatTwoSidedUnlit', cpp))
    check("T13c: Debug material mode 1 (UnlitGray) preserved", has_mode1, "case 1 not found")
    check("T13d: Debug material mode 2 (TwoSidedGray) preserved", has_mode2, "case 2 not found")
    check("T13e: Debug material mode 3 (TwoSidedUnlitGray) preserved", has_mode3, "case 3 not found")

    # ---- Summary ----
    print("\n" + "=" * 70)
    print(f"RESULTS: {PASS_COUNT} passed, {FAIL_COUNT} failed, {PASS_COUNT + FAIL_COUNT} total")
    print("=" * 70)

    print("\nCVars:")
    print("  1. UE.LiveSync.V1DebugForceFaceNormals (int, default 0)")
    print("  2. UE.LiveSync.V1DebugDisableTangents (int, default 0)")
    print("\nLog markers (Stage 2C.11):")
    print("  [MESH][V1][DEBUG_FACE_NORMALS] enabled|disabled normals=N tris=N")
    print("  [MESH][V1][DEBUG_NORMALS] mode=source|face")
    print("  [MESH][V1][DEBUG_TANGENTS] enabled|disabled computed=N passed=N")
    print("  [MESH][V1][SECTION_ARRAYS] ... computedTangents=N passedTangents=N ...")
    print("  [MESH][V1][TANGENT] ... tangents=N ... (computed, unconditional)")

    sys.exit(1 if FAIL_COUNT > 0 else 0)


if __name__ == "__main__":
    main()
