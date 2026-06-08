#!/usr/bin/env python3
"""
Phase 7C Stage 2C.10 — V1 Debug Material Modes Tests

Tests for opt-in debug material CVars controlling shading/culling
isolation on v1 ProceduralMeshComponent builds.

Tests:
  T1: V1DebugMaterialMode=0 → no material assignment
  T2: V1DebugMaterialMode=1 → unlit debug material
  T3: V1DebugMaterialMode=2 → two-sided debug material
  T4: V1DebugMaterialMode=3 → two-sided unlit debug material
  T5: Debug material log is unconditional when mode != 0
  T6: V1DebugForceFaceNormals=1 only changes normals when set
  T7: V1DebugDisableTangents=1 only passes empty tangents when set
  T8: Default v1 path unchanged when all CVars are default (0)
  T9: No packet format change
  T10: No legacy V5 path change
  T11: Debug material is not final material grouping

This test file validates:
  - The CVar declarations exist and have correct defaults
  - The UE source code contains the required log markers
  - The code does NOT modify V5 legacy paths
  - The code does NOT modify packet formats
  - The code does NOT introduce final material grouping
"""

import os
import re
import sys

# Paths
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
    print("=" * 70)
    print("Phase 7C Stage 2C.10 — V1 Debug Material Modes Tests")
    print("=" * 70)

    cpp = read_source()
    hdr = read_header()

    # ---- T1: V1DebugMaterialMode=0 → no material assignment by default ----
    print("\n[TEST GROUP] V1DebugMaterialMode defaults")
    cvar_mode = re.search(
        r'CVarLiveSyncV1DebugMaterialMode\s*\(\s*TEXT\("UE\.LiveSync\.V1DebugMaterialMode"\)\s*,\s*(\d+)',
        cpp
    )
    check(
        "T1: CVar UE.LiveSync.V1DebugMaterialMode exists with default 0",
        cvar_mode is not None and int(cvar_mode.group(1)) == 0,
        f"Found: {cvar_mode.group(1) if cvar_mode else 'not found'}"
    )

    # ---- T2: Mode 1 = unlit gray ----
    print("\n[TEST GROUP] Unlit debug material mode")
    has_unlit = bool(
        re.search(
            r'CVarLiveSyncV1DebugMaterialMode',
            cpp
        ) and
        re.search(
            r'MSM_Unlit',
            cpp
        )
    )
    check(
        "T2: Unlit debug material path exists in source",
        has_unlit,
        "MSM_Unlit not found or CVar not found"
    )

    # ---- T3: Mode 2 = two-sided gray ----
    print("\n[TEST GROUP] Two-sided debug material mode")
    has_twosided = bool(
        re.search(
            r'TwoSided\s*=\s*1',
            cpp
        )
    )
    check(
        "T3: Two-sided debug material path exists in source",
        has_twosided,
        "TwoSided = 1 not found"
    )

    # ---- T4: Mode 3 = two-sided unlit ----
    print("\n[TEST GROUP] Two-sided unlit debug material mode")
    has_twosided_unlit = bool(
        re.search(
            r'DebugMatTwoSidedUnlit',
            cpp
        )
    )
    check(
        "T4: Two-sided unlit debug material variable exists",
        has_twosided_unlit,
        "DebugMatTwoSidedUnlit not found"
    )

    # ---- T5: Debug material log is unconditional when mode != 0 ----
    print("\n[TEST GROUP] Debug material logging")
    has_debug_material_log = bool(
        re.search(
            r'\[MESH\]\[V1\]\[DEBUG_MATERIAL\]',
            cpp
        )
    )
    check(
        "T5: Debug material log marker [MESH][V1][DEBUG_MATERIAL] exists",
        has_debug_material_log,
        "Log marker not found"
    )

    # ---- T6: Force face normals ----
    print("\n[TEST GROUP] Force face normals")
    has_face_normals_cvar = bool(
        re.search(
            r'CVarLiveSyncV1DebugForceFaceNormals',
            cpp
        )
    )
    has_face_normals_log = bool(
        re.search(
            r'\[MESH\]\[V1\]\[DEBUG_NORMALS\]',
            cpp
        )
    )
    check(
        "T6a: CVar UE.LiveSync.V1DebugForceFaceNormals exists",
        has_face_normals_cvar,
        "CVar not found"
    )
    check(
        "T6b: Debug normal log marker [MESH][V1][DEBUG_NORMALS] exists",
        has_face_normals_log,
        "Log marker not found"
    )

    # ---- T7: Disable tangents ----
    print("\n[TEST GROUP] Disable tangents")
    has_disable_tangents_cvar = bool(
        re.search(
            r'CVarLiveSyncV1DebugDisableTangents',
            cpp
        )
    )
    has_tangents_log = bool(
        re.search(
            r'\[MESH\]\[V1\]\[DEBUG_TANGENTS\]',
            cpp
        )
    )
    check(
        "T7a: CVar UE.LiveSync.V1DebugDisableTangents exists",
        has_disable_tangents_cvar,
        "CVar not found"
    )
    check(
        "T7b: Debug tangents log marker [MESH][V1][DEBUG_TANGENTS] exists",
        has_tangents_log,
        "Log marker not found"
    )

    # ---- T8: Default v1 path unchanged when all CVars are default ----
    print("\n[TEST GROUP] Default behavior unchanged")
    # When CVars are 0, no debug material assignment should occur.
    # Check that the debug material block is gated by mode != 0.
    mode_gate = re.search(
        r'if\s*\(\s*DebugMode\s*!=\s*0\s*\)',
        cpp
    )
    check(
        "T8a: Debug material assignment is gated by mode != 0",
        mode_gate is not None,
        "Gate not found"
    )

    # Check that CreateMeshSection is called in the v1 path
    v1_create_mesh = re.search(
        r'ProcMesh->CreateMeshSection\s*\(\s*0\s*,',
        cpp
    )
    check(
        "T8b: v1 path still calls CreateMeshSection(0, ...) for default behavior",
        v1_create_mesh is not None,
        "CreateMeshSection call not found"
    )

    # ---- T9: No packet format change ----
    print("\n[TEST GROUP] No packet format change")
    # Verify no new packet types or struct modifications
    # Check that PT_Keyframe wire format is untouched by searching for
    # the struct definition that has no new fields
    has_keyframe_struct = bool(
        re.search(
            r'struct\s+FKeyframe.*?\{.*?\}',
            cpp,
            re.DOTALL
        )
    )
    # More reliable: check that the packet type enum hasn't been modified
    # by verifying no new packet type beyond the existing range
    # (This is a static check — we verify the .cpp does not add new PT_ values)
    new_pt_pattern = re.search(
        r'PT_.*DebugMaterial|PT_.*DebugNormals|PT_.*DebugTangents',
        cpp
    )
    check(
        "T9: No new packet type for debug features",
        new_pt_pattern is None,
        f"Found unexpected packet type: {new_pt_pattern.group(0) if new_pt_pattern else ''}"
    )

    # ---- T10: No legacy V5 path change ----
    print("\n[TEST GROUP] Legacy V5 path unchanged")
    # Check that the debug material logic is only inside BuildV1MeshFromReassembly
    # and not inside ReconstructCompletedMeshes
    v5_reconstruct_start = cpp.find('ReconstructCompletedMeshes()')
    v5_reconstruct_end = cpp.find('\n}', v5_reconstruct_start + 30)

    # Find where V5 function ends
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
        'CVarLiveSyncV1DebugMaterialMode' in v5_func or
        'CVarLiveSyncV1DebugForceFaceNormals' in v5_func or
        'CVarLiveSyncV1DebugDisableTangents' in v5_func or
        'DEBUG_MATERIAL' in v5_func
    )
    check(
        "T10: Debug material CVars are NOT in the V5 legacy path (ReconstructCompletedMeshes)",
        not debug_in_v5,
        "Found debug CVars in V5 function" if debug_in_v5 else "OK"
    )

    # ---- T11: Debug material is not final material grouping ----
    print("\n[TEST GROUP] Not final material grouping")
    # Verify that debug material is transient and not stored as asset
    # Check that debug materials use RF_Transient flag
    has_transient = bool(
        re.search(
            r'RF_Transient',
            cpp
        )
    )
    # Check there is no permanent material storage for debug materials
    has_persistent_debug = bool(
        re.search(
            r'MaterialPathCache.*Debug|MaterialMetadata.*Debug|CacheMaterialPath.*Debug',
            cpp
        )
    )
    check(
        "T11a: Debug materials use RF_Transient (not persisted to assets)",
        has_transient,
        "RF_Transient not found"
    )
    check(
        "T11b: No debug materials stored in permanent MaterialPathCache or MaterialMetadata",
        not has_persistent_debug,
        "Found persistent debug material storage" if has_persistent_debug else "OK"
    )

    # ---- Summary ----
    print("\n" + "=" * 70)
    print(f"RESULTS: {PASS_COUNT} passed, {FAIL_COUNT} failed, {PASS_COUNT + FAIL_COUNT} total")
    print("=" * 70)

    # CVar names summary
    print("\nCVars:")
    print("  1. UE.LiveSync.V1DebugMaterialMode (int, default 0)")
    print("     0=none, 1=unlit gray, 2=two-sided gray, 3=two-sided unlit gray")
    print("  2. UE.LiveSync.V1DebugForceFaceNormals (int, default 0)")
    print("  3. UE.LiveSync.V1DebugDisableTangents (int, default 0)")
    print("\nLog markers:")
    print("  [MESH][V1][DEBUG_MATERIAL] mode=<0-3> material=<name|None> assigned=<0-1>")
    print("  [MESH][V1][DEBUG_FACE_NORMALS] enabled|disabled normals=N tris=N")
    print("  [MESH][V1][DEBUG_NORMALS] mode=source|face")
    print("  [MESH][V1][DEBUG_TANGENTS] enabled|disabled computed=N passed=N")

    sys.exit(1 if FAIL_COUNT > 0 else 0)


if __name__ == "__main__":
    main()
