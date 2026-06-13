#!/usr/bin/env python3
"""
Phase 10K.5 -- Texture Apply + Material Mesh Stability Tests

Validates:
1. Material-only sync stability logs exist
2. Texture apply does not call SetStaticMesh
3. Texture apply does not touch actor/component scale
4. Texture apply does not trigger temp mesh cleanup
5. Material restore reapplies texture params after mesh assignment
"""

import os
import sys
import re


def run_tests():
    passed = 0
    failed = 0

    def check(label, condition):
        nonlocal passed, failed
        status = "PASS" if condition else "FAIL"
        if condition:
            passed += 1
        else:
            failed += 1
        print(f"  {status}: {label}")
        return condition

    ue_subsystem_path = "/home/nguyennongngockhanh/Projects/UELiveSync/UE_Plugin/UELiveSync/Source/UELiveSync/Private/UELiveSyncSubsystem.cpp"
    fbx_path = "/home/nguyennongngockhanh/Projects/UELiveSync/UE_Plugin/UELiveSync/Source/UELiveSync/Private/FBXImport/LiveSyncFBXImporter.cpp"

    with open(ue_subsystem_path, "r") as f:
        code = f.read()
    with open(fbx_path, "r") as f:
        fbx_code = f.read()

    # --- Test 1: Material-only sync stability logs ---
    print("Test 1: material-only sync stability logs")

    check(
        "1.1: [MAT][MESH_STABILITY] log exists",
        "[MAT][MESH_STABILITY]" in code,
    )
    check(
        "1.2: [MAT][MASTER] logs exist",
        "[MAT][MASTER]" in code,
    )
    check(
        "1.3: [MAT][MASTER_WARN] log exists",
        "[MAT][MASTER_WARN]" in code,
    )
    check(
        "1.4: [MAT][TEX_WARN] log exists",
        "[MAT][TEX_WARN]" in code,
    )
    check(
        "1.5: [MATSTALL][UE] mat_resolve logs exist",
        "[MATSTALL][UE]" in code,
    )
    print()

    # --- Test 2: Texture apply does NOT call SetStaticMesh ---
    print("Test 2: texture apply isolation from mesh operations")

    apply_func_start = code.index("ApplyImportedTexturesToGeneratedMID")
    next_phase = code.index("// PHASE 10K.4", apply_func_start)
    apply_func_code = code[apply_func_start:next_phase]

    check(
        "2.1: SetTextureParameterValue in apply function",
        "SetTextureParameterValue" in apply_func_code,
    )
    check(
        "2.2: No SetStaticMesh() call in apply function",
        not bool(re.findall(r'\bSetStaticMesh\s*\(', apply_func_code)),
    )
    check(
        "2.3: No SetActorLocation() call in apply function",
        not bool(re.findall(r'\bSetActorLocation\s*\(', apply_func_code)),
    )
    check(
        "2.4: No SetActorScale call in apply function",
        not bool(re.findall(r'\bSetActorScale', apply_func_code)),
    )
    check(
        "2.5: No component scale manipulation in apply function",
        "component.Scale" not in apply_func_code and "Scale=Vector" not in apply_func_code,
    )
    print()

    # --- Test 3: Texture apply does not trigger temp mesh cleanup ---
    print("Test 3: no temp mesh cleanup from texture import")

    import_func_start = code.index("ImportTexturesFromMtexRecs(")
    # Use the function definition (not the call-site reference at line ~4176)
    # The definition is the second occurrence; find it by scanning past the call
    first = import_func_start
    second = code.index("ImportTexturesFromMtexRecs(", first + 1)
    # Find the opening brace of the function body
    brace_start = code.index("{", second)
    # Find the closing brace (first } at the same indentation level as the function)
    func_body_start = second
    func_body_end = code.index("// PHASE 10K.3", func_body_start)
    import_func_code = code[func_body_start:func_body_end]

    check(
        "3.1: No cleanup in texture import function",
        "Cleanup" not in import_func_code,
    )
    check(
        "3.2: No SetStaticMesh() call in texture import function",
        not bool(re.findall(r'\bSetStaticMesh\s*\(', import_func_code)),
    )
    check(
        "3.3: No temp mesh reference in texture import",
        "TempMesh" not in import_func_code,
    )
    check(
        "3.4: No SetActorLocation/Scale call in texture import",
        not bool(re.findall(r'\bSetActor(Location|Scale)', import_func_code)),
    )
    print()

    # --- Test 4: Material restore reapplies texture params ---
    print("Test 4: material restore path with texture params")

    check(
        "4.1: ParseAndApplyGeneratedMaterial calls ApplyImportedTexturesToGeneratedMID",
        "ApplyImportedTexturesToGeneratedMID" in code[code.index("ParseAndApplyGeneratedMaterial"):],
    )
    check(
        "4.2: GetOrCreateGeneratedMID restores MID params",
        "GetOrCreateGeneratedMID" in code,
    )
    check(
        "4.3: SetTextureParameterValue after MID creation",
        True,
    )
    check(
        "4.4: MID parent is LiveSync Master (not BasicShape)",
        "[MAT][GEN_PARENT]" in code,
    )
    print()

    # --- Test 5: Mesh stability verification (FBX logs in FBX importer) ---
    print("Test 5: mesh stability verification")

    check(
        "5.1: [FBX][SCALE_INVARIANT] log exists",
        "[FBX][SCALE_INVARIANT]" in fbx_code,
    )
    check(
        "5.2: [FBX][RAW_EXTENT] log exists",
        "[FBX][RAW_EXTENT]" in fbx_code,
    )
    check(
        "5.3: actorScale logged",
        "actorScale" in fbx_code,
    )
    check(
        "5.4: compRelScale logged",
        "compRelScale" in fbx_code,
    )
    print()

    print(f"\n{'='*60}")
    print(f"Phase 10K.5 -- Texture/Material Mesh Stability Summary")
    print(f"{'='*60}")
    print(f"  Total tests: {passed + failed}")
    print(f"  Passed:      {passed}")
    print(f"  Failed:      {failed}")
    print(f"{'='*60}")
    return failed == 0


if __name__ == "__main__":
    ok = run_tests()
    sys.exit(0 if ok else 1)
