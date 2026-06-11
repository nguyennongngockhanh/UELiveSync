"""
Phase 10J.5F.hotfix2 — End-to-end wire verification for FBX GeometryHash.

Verifies exact operator path, payload bytes, and UE parser alignment.
"""

import os
import sys
import struct

REPO_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
NETWORK_PATH = os.path.join(REPO_ROOT, "Blender_Addon/network.py")
INIT_PATH = os.path.join(REPO_ROOT, "Blender_Addon/__init__.py")
IMPORTER_PATH = os.path.join(REPO_ROOT,
    "UE_Plugin/UELiveSync/Source/UELiveSync/Private/FBXImport/LiveSyncFBXImporter.cpp")
SUBSYSTEM_PATH = os.path.join(REPO_ROOT,
    "UE_Plugin/UELiveSync/Source/UELiveSync/Private/UELiveSyncSubsystem.cpp")
SYNC_TYPES_PATH = os.path.join(REPO_ROOT,
    "UE_Plugin/UELiveSync/Source/UELiveSync/Public/SyncTypes.h")

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


def main():
    global PASS, FAIL
    for p in [NETWORK_PATH, INIT_PATH, IMPORTER_PATH, SUBSYSTEM_PATH, SYNC_TYPES_PATH]:
        assert os.path.isfile(p), f"Missing: {p}"

    net = read_file(NETWORK_PATH)
    init = read_file(INIT_PATH)
    imp = read_file(IMPORTER_PATH)
    sub = read_file(SUBSYSTEM_PATH)
    sync = read_file(SYNC_TYPES_PATH)

    # =========================================================
    # T1: UI label maps to the expected operator
    # =========================================================
    t1_label = '"Sync Selected Mesh to UE (FBX)"' in init
    t1_idname = '"uelivesync.sync_selected_mesh_to_ue_fbx"' in init
    check(t1_label and t1_idname,
          "T1: UI button 'Sync Selected Mesh to UE (FBX)' maps to uelivesync.sync_selected_mesh_to_ue_fbx",
          f"label={t1_label} idname={t1_idname}")

    # =========================================================
    # T2: operator calls compute_fbx_geometry_hash
    # =========================================================
    t2 = "compute_fbx_geometry_hash" in init
    check(t2, "T2: FBX operator calls compute_fbx_geometry_hash",
          f"found={t2}")

    # =========================================================
    # T3: operator passes geometry_hash to serialize
    # =========================================================
    t3 = "geometry_hash=geometry_hash" in init
    check(t3, "T3: FBX operator passes geometry_hash to serialize_fbx_import_request",
          f"passed={t3}")

    # =========================================================
    # T4: serialize_fbx_import_request packs uint64 GeometryHash
    # =========================================================
    t4_fmt = "'<16sI512s128sIIIdQ'" in net or '"<16sI512s128sIIIdQ"' in net
    t4_param = "geometry_hash" in net.split("def serialize_fbx_import_request")[1].split("\n")[0] if "def serialize_fbx_import_request" in net else False
    check(t4_fmt,
          "T4: serialize_fbx_import_request packs uint64 GeometryHash (Q in format)",
          f"fmt_Q={t4_fmt} param={t4_param}")

    # =========================================================
    # T5: Payload with 0x1122334455667788 contains exact bytes
    # =========================================================
    # Build a test payload using the actual serialization
    sys.path.insert(0, os.path.join(REPO_ROOT, "Blender_Addon"))
    try:
        from network import serialize_fbx_import_request, FBX_IMPORT_REQUEST_PAYLOAD_SIZE
        import uuid
        test_guid = uuid.uuid4()
        test_payload = serialize_fbx_import_request(
            guid_obj=test_guid,
            fbx_path="/tmp/test.fbx",
            object_name="Test",
            vert_count=100,
            tri_count=200,
            mat_slot_count=2,
            timestamp=1234567890.0,
            geometry_hash=0x1122334455667788,
        )
        expected_bytes = bytes([0x88, 0x77, 0x66, 0x55, 0x44, 0x33, 0x22, 0x11])
        t5_bytes = (test_payload[680:688] == expected_bytes)
        t5_size = (len(test_payload) == FBX_IMPORT_REQUEST_PAYLOAD_SIZE)
        check(t5_bytes and t5_size,
              "T5: Payload with hash 0x1122334455667788 contains LE bytes 88 77 66 55 44 33 22 11 at offset 680",
              f"bytes_match={t5_bytes} size={len(test_payload)} expected={FBX_IMPORT_REQUEST_PAYLOAD_SIZE}")
    except Exception as e:
        check(False, "T5: Payload exact byte test failed", str(e))

    # =========================================================
    # T6: New payload size (688) matches UE expected size
    # =========================================================
    t6_blender = "FBX_IMPORT_REQUEST_PAYLOAD_SIZE = 688" in net
    t6_ue = "static_assert" in sync and "688" in sync
    t6_ue_comment = "688 bytes" in sync
    check(t6_blender and t6_ue and t6_ue_comment,
          "T6: Blender payload size (688) matches UE static_assert (688)",
          f"blender={t6_blender} ue_assert={t6_ue} ue_comment={t6_ue_comment}")

    # =========================================================
    # T7: No legacy FBX serializer used by operator
    # =========================================================
    # The operator should NOT use any serializer other than serialize_fbx_import_request
    # Check there's no old-style FBX serialization in the operator path
    t7_old_fmt = "'<16sI512s128sIIId'" in init
    check(not t7_old_fmt,
          "T7: No legacy (680-byte) serializer used by FBX operator",
          f"old_fmt_in_operator={t7_old_fmt}")

    # =========================================================
    # T8: UE parser reads GeometryHash when payload >= new size
    # =========================================================
    t8_memzero = "FMemory::Memzero" in imp
    t8_mincopy = "FMath::Min(PayloadSize" in imp
    t8_read = "Request.GeometryHash" in imp
    t8_log_new = "geomHash=%llu" in imp
    check(t8_memzero and t8_mincopy and t8_read and t8_log_new,
          "T8: UE parser reads GeometryHash (Memzero + Min copy + field read + new log)",
          f"memzero={t8_memzero} mincopy={t8_mincopy} read={t8_read} newlog={t8_log_new}")

    # =========================================================
    # T9: Old protocol log for old payload
    # =========================================================
    t9 = "geomHash=0 (old protocol)" in imp
    check(t9, "T9: Old protocol log for old payload size",
          f"old_log={t9}")

    # =========================================================
    # T10: New payload path does NOT log old protocol
    # =========================================================
    # The new path branches based on Request.GeometryHash != 0
    t10_branch = "Request.GeometryHash != 0" in imp
    check(t10_branch,
          "T10: New payload path branches on GeometryHash != 0",
          f"branch={t10_branch}")

    # =========================================================
    # T11: No RegisterComponent in FBX code paths
    # =========================================================
    t11_imp = "RegisterComponent" in imp
    fbx_section = sub.split("PacketType == 0x16")[1].split("PacketType == 0x17")[0] if "PacketType == 0x16" in sub and "PacketType == 0x17" in sub else ""
    t11_fbx_sub = "RegisterComponent" in fbx_section
    t11_ok = not t11_imp and not t11_fbx_sub
    check(t11_ok, "T11: No RegisterComponent introduced in FBX code paths",
          f"importer={t11_imp} fbx_dispatch={t11_fbx_sub}")

    # =========================================================
    # T12: No MaterialPathCache calls in FBX code paths
    # =========================================================
    t12_imp = "MaterialPathCache" in imp
    t12_fbx_sub = "MaterialPathCache" in fbx_section
    t12_ok = not t12_imp and not t12_fbx_sub
    check(t12_ok, "T12: No MaterialPathCache calls added to FBX code paths",
          f"importer={t12_imp} fbx_dispatch={t12_fbx_sub}")

    # =========================================================
    # T13: operator path has 'import struct' for fallback
    # =========================================================
    t13 = "import struct" in init
    check(t13, "T13: __init__.py operator imports struct for fallback",
          f"struct_import={t13}")

    # =========================================================
    # T14: operator has zero-hash fallback
    # =========================================================
    t14 = "if geometry_hash == 0" in init
    check(t14, "T14: __init__.py operator has zero-hash fallback",
          f"fallback={t14}")

    # =========================================================
    # T15: [FBX][BLENDER] log present before send
    # =========================================================
    t15 = "[FBX][BLENDER]" in init
    check(t15, "T15: [FBX][BLENDER] log present in operator path",
          f"log={t15}")

    total = PASS + FAIL
    print(f"\n{'='*50}")
    print(f"Results: {PASS} passed, {FAIL} failed, {total} total")
    print(f"{'='*50}")
    return FAIL == 0


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
