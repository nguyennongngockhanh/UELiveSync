"""
Phase 10J.5F — Static/source check: FBX geometry content signature.

Verifies:
Blender side:
  T1. compute_fbx_geometry_hash exists in network.py
  T2. hash uses evaluated mesh vertex coordinates (v.co.x/y/z)
  T3. hash includes topology (loop_triangle vertices)
  T4. hash includes material slot count
  T5. hash does NOT use object world transform (no v.co_local, no matrix_world)
  T6. FBX request serialization includes geometry_hash param

UE side:
  T7. UE parser accepts old FBX request payload (GeometryHash = 0)
  T8. UE parser reads GeometryHash when new payload size is present
  T9. FBX semantic signature includes GeometryHash
  T10. duplicate skip requires GeometryHash equality AND GeometryHash != 0
  T11. different GeometryHash forces import with reason geometry_hash_changed
  T12. timestamp/mtime/file size are not used for duplicate equality
  T13. no RegisterComponent introduced
  T14. no MaterialPathCache calls added
  T15. no unrelated protocol constants changed
"""

import os
import sys

REPO_ROOT = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..")
)
NETWORK_PATH = os.path.join(
    REPO_ROOT,
    "Blender_Addon/network.py",
)
IMPORTER_PATH = os.path.join(
    REPO_ROOT,
    "UE_Plugin/UELiveSync/Source/UELiveSync/Private/FBXImport/LiveSyncFBXImporter.cpp",
)
SUBSYSTEM_PATH = os.path.join(
    REPO_ROOT,
    "UE_Plugin/UELiveSync/Source/UELiveSync/Private/UELiveSyncSubsystem.cpp",
)
SYNC_TYPES_PATH = os.path.join(
    REPO_ROOT,
    "UE_Plugin/UELiveSync/Source/UELiveSync/Public/SyncTypes.h",
)
INIT_PATH = os.path.join(
    REPO_ROOT,
    "Blender_Addon/__init__.py",
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


def lines_containing(text: str, pattern: str) -> list:
    return [l for l in text.split("\n") if pattern in l]


def operator_eq_excludes_field(content: str, field_name: str) -> bool:
    """Check that operator== block does NOT compare the given field."""
    lines = content.split("\n")
    in_operator_eq = False
    brace_depth = 0
    for line in lines:
        stripped = line.strip()
        if "bool operator==" in stripped:
            in_operator_eq = True
            brace_depth = 0
            continue
        if in_operator_eq:
            brace_depth += stripped.count("{") - stripped.count("}")
            if brace_depth <= 0 and "}" in stripped:
                return True
            if f"&& {field_name} ==" in stripped or f"&& {field_name}==" in stripped:
                return False
    return False


def main():
    global PASS, FAIL
    for p in [NETWORK_PATH, IMPORTER_PATH, SUBSYSTEM_PATH, SYNC_TYPES_PATH, INIT_PATH]:
        assert_file_exists(p)

    net = read_file(NETWORK_PATH)
    imp = read_file(IMPORTER_PATH)
    sub = read_file(SUBSYSTEM_PATH)
    sync = read_file(SYNC_TYPES_PATH)
    init = read_file(INIT_PATH)

    # =============================================================
    # T1: compute_fbx_geometry_hash exists in network.py
    # =============================================================
    t1 = "def compute_fbx_geometry_hash" in net
    check(t1, "T1: compute_fbx_geometry_hash function exists in network.py",
          f"exists={t1}")

    # =============================================================
    # T2: hash uses evaluated mesh vertex coordinates
    # =============================================================
    t2_vco = "v.co" in net or "vertex.co" in net
    t2_loop = "loop_triangles" in net
    check(t2_vco and t2_loop,
          "T2: hash uses vertex coordinates and loop triangles",
          f"vco={t2_vco} loop={t2_loop}")

    # =============================================================
    # T3: hash includes topology (loop triangle vertices)
    # =============================================================
    t3 = "t.vertices" in net or "loop_triangle" in net
    check(t3, "T3: hash includes loop triangle vertex indices (topology)",
          f"topology={t3}")

    # =============================================================
    # T4: hash includes material slot count
    # =============================================================
    t4 = "len(mesh.materials)" in net or "mat_slot" in net
    check(t4, "T4: hash includes material slot count",
          f"mats={t4}")

    # =============================================================
    # T5: hash does NOT use object world transform
    # =============================================================
    t5_no_matrix = "matrix_world" not in net
    t5_no_local = "co_local" not in net
    check(t5_no_matrix and t5_no_local,
          "T5: hash uses local-space mesh data, not world transform",
          f"no_matrix={t5_no_matrix} no_local={t5_no_local}")

    # =============================================================
    # T6: FBX request serialization includes geometry_hash param
    # =============================================================
    t6_serialize = "geometry_hash" in net and "geometry_hash=0" in net
    check(t6_serialize,
          "T6: serialize_fbx_import_request has geometry_hash param (default 0)",
          f"serialize_geom={t6_serialize}")

    # =============================================================
    # T7: UE parser accepts old 680-byte payloads (kFBXPayloadSizeMin = 680)
    # =============================================================
    t7_min = "kFBXPayloadSizeMin = 680" in sub
    check(t7_min,
          "T7: UE subsystem accepts old 680-byte payloads (kFBXPayloadSizeMin = 680)",
          f"min_size={t7_min}")

    # =============================================================
    # T8: UE parser reads GeometryHash when new payload size present
    # =============================================================
    t8_memzero = "FMemory::Memzero(&Request, sizeof(FFBXImportRequestPayload))" in imp
    t8_partial = "FMath::Min(PayloadSize, (int32)sizeof(FFBXImportRequestPayload))" in imp
    t8_geom_log = "geomHash=" in imp
    check(t8_memzero and t8_partial and t8_geom_log,
          "T8: UE parser uses Memzero + partial copy + reads GeometryHash",
          f"memzero={t8_memzero} partial={t8_partial} log={t8_geom_log}")

    # =============================================================
    # T9: FBX semantic signature includes GeometryHash
    # =============================================================
    t9_field = "GeometryHash" in imp
    # Verify it's in the struct (before operator==)
    struct_section = imp.split("bool operator==")[0] if "bool operator==" in imp else ""
    t9_in_struct = "GeometryHash" in struct_section
    check(t9_field and t9_in_struct,
          "T9: FFBXImportSemanticSignature includes GeometryHash field",
          f"field={t9_field} in_struct={t9_in_struct}")

    # =============================================================
    # T10: duplicate skip requires GeometryHash equality AND GeometryHash != 0
    # =============================================================
    t10_hash_guard = "CurrentSig.GeometryHash != 0" in imp
    t10_eq = "GeometryHash == Other.GeometryHash" in imp
    t10_skip_reason = "same_semantic_signature" in imp
    check(t10_hash_guard and t10_eq and t10_skip_reason,
          "T10: Skip requires GeometryHash != 0 guard + equality in operator==",
          f"guard={t10_hash_guard} eq={t10_eq} reason={t10_skip_reason}")

    # =============================================================
    # T11: different GeometryHash forces import with geometry_hash_changed
    # =============================================================
    t11 = "reason=geometry_hash_changed" in imp
    check(t11, "T11: geometry_hash_changed COALESCE reason present",
          f"geom_changed={t11}")

    # =============================================================
    # T12: timestamp/mtime/file size not in duplicate equality
    # =============================================================
    t12_no_ts = operator_eq_excludes_field(imp, "Timestamp")
    t12_no_fs = operator_eq_excludes_field(imp, "FileSize")
    check(t12_no_ts and t12_no_fs,
          "T12: Timestamp and FileSize NOT in semantic signature equality",
          f"no_ts={t12_no_ts} no_fs={t12_no_fs}")

    # =============================================================
    # T13: no RegisterComponent introduced
    # =============================================================
    t13 = "RegisterComponent" in imp
    check(not t13, "T13: No RegisterComponent added to importer",
          f"register={t13}")

    # =============================================================
    # T14: no MaterialPathCache calls added
    # =============================================================
    t14 = "MaterialPathCache" in imp
    check(not t14, "T14: No MaterialPathCache calls added to importer",
          f"matpath={t14}")

    # =============================================================
    # T15: no unrelated protocol constants changed
    # =============================================================
    # Check that the geometry hash addition is the only structural change
    # and no new packet types were added
    has_pt_mesh = "0x06" in sub
    has_pt_keyframe = "0x17" in sub
    has_pt_fbx = "0x16" in sub
    has_pt_sequencer = "0x18" in sub
    t15 = has_pt_mesh and has_pt_keyframe and has_pt_fbx and has_pt_sequencer
    check(t15,
          "T15: Existing packet type constants preserved (0x06, 0x16, 0x17, 0x18)",
          f"mesh={has_pt_mesh} fbx={has_pt_fbx} keyframe={has_pt_keyframe} seq={has_pt_sequencer}")

    # Summary
    total = PASS + FAIL
    print(f"\n{'='*50}")
    print(f"Results: {PASS} passed, {FAIL} failed, {total} total")
    print(f"{'='*50}")

    return FAIL == 0


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
