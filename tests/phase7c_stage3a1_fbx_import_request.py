#!/usr/bin/env python3
"""
Phase 7C Stage 3A.1 — FBX Import Request tests.

Verifies:
  - PT_FBXImportRequest constant = 0x16
  - serialize_fbx_import_request() produces correct payload size
  - Fixed byte offsets for each field in the 680-byte payload
  - Path/name sanitization
  - Manifest field structure
  - UE kValidTypes includes 0x16 by source text check
  - Existing PT_Mesh path not removed or renamed
"""

import sys
import os
import struct
import time
from uuid import UUID

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "Blender_Addon"))
import network

PASS = 0
FAIL = 0


def test(name, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
    else:
        FAIL += 1
        msg = f"  FAIL: {name}"
        if detail:
            msg += f" — {detail}"
        print(msg)


def banner(title):
    width = 60
    print(f"\n{'=' * width}")
    print(f"  {title}")
    print(f"{'=' * width}")


# =============================================================
# T1: Packet type constant
# =============================================================

banner("T1 — PT_FBXImportRequest constant is 0x16")

test("T1.1: PT_FBXImportRequest == 0x16",
      network.PT_FBXImportRequest == 0x16,
      f"got {network.PT_FBXImportRequest}")

test("T1.2: constant is int",
      isinstance(network.PT_FBXImportRequest, int),
      f"type={type(network.PT_FBXImportRequest)}")


# =============================================================
# T2: Payload size constant
# =============================================================

banner("T2 — FBX_IMPORT_REQUEST_PAYLOAD_SIZE == 688")

test("T2.1: FBX_IMPORT_REQUEST_PAYLOAD_SIZE == 688",
      network.FBX_IMPORT_REQUEST_PAYLOAD_SIZE == 688,
      f"got {network.FBX_IMPORT_REQUEST_PAYLOAD_SIZE}")


# =============================================================
# T3: Serialize payload size and structure
# =============================================================

banner("T3 — serialize_fbx_import_request() payload structure")

test_guid = UUID("12345678-1234-1234-1234-123456789abc")
test_path = "/home/user/.cache/uelivesync/fbx/guid123/cube.fbx"
test_name = "MyCube"

payload = network.serialize_fbx_import_request(
    guid_obj=test_guid,
    fbx_path=test_path,
    object_name=test_name,
    vert_count=24,
    tri_count=12,
    mat_slot_count=1,
    timestamp=1234567890.0,
)

test("T3.1: payload is 688 bytes",
      len(payload) == 688,
      f"got {len(payload)}")

test("T3.2: payload is bytes",
      isinstance(payload, bytes),
      f"type={type(payload)}")

# Verify GUID at offset 0-15
guid_bytes = payload[0:16]
d_a, d_b, d_c, d_d = struct.unpack_from("<IIII", guid_bytes, 0)
expected_a = test_guid.time_low
expected_b = (test_guid.time_mid << 16) | test_guid.time_hi_version
expected_c = (test_guid.clock_seq_hi_variant << 24
              | test_guid.clock_seq_low << 16
              | (test_guid.node >> 32) & 0xFFFF)
expected_d = test_guid.node & 0xFFFFFFFF

test("T3.3: GUID time_low at offset 0",
      d_a == expected_a,
      f"got {d_a} expected {expected_a}")

test("T3.4: GUID time_mid/hi at offset 4",
      d_b == expected_b,
      f"got {d_b} expected {expected_b}")

test("T3.5: GUID clock_seq/node_hi at offset 8",
      d_c == expected_c,
      f"got {d_c} expected {expected_c}")

test("T3.6: GUID node_lo at offset 12",
      d_d == expected_d,
      f"got {d_d} expected {expected_d}")

# Verify Version at offset 16
version = struct.unpack_from("<I", payload, 16)[0]
test("T3.7: Version == 1 at offset 16",
      version == 1,
      f"got {version}")

# Verify FbxPath at offset 20 (size 512)
fbx_path_raw = payload[20:532]
fbx_path_str = fbx_path_raw.rstrip(b'\x00').decode('utf-8')
test("T3.8: FbxPath at offset 20",
      fbx_path_str == test_path,
      f"got '{fbx_path_str}' expected '{test_path}'")

test("T3.9: FbxPath field is 512 bytes",
      len(fbx_path_raw) == 512,
      f"got {len(fbx_path_raw)}")

# Verify ObjectName at offset 532 (size 128)
name_raw = payload[532:660]
name_str = name_raw.rstrip(b'\x00').decode('utf-8')
test("T3.10: ObjectName at offset 532",
      name_str == test_name,
      f"got '{name_str}' expected '{test_name}'")

test("T3.11: ObjectName field is 128 bytes",
      len(name_raw) == 128,
      f"got {len(name_raw)}")

# Verify VertCount at offset 660
vert_count = struct.unpack_from("<I", payload, 660)[0]
test("T3.12: VertCount == 24 at offset 660",
      vert_count == 24,
      f"got {vert_count}")

# Verify TriCount at offset 664
tri_count = struct.unpack_from("<I", payload, 664)[0]
test("T3.13: TriCount == 12 at offset 664",
      tri_count == 12,
      f"got {tri_count}")

# Verify MatSlotCount at offset 668
mat_count = struct.unpack_from("<I", payload, 668)[0]
test("T3.14: MatSlotCount == 1 at offset 668",
      mat_count == 1,
      f"got {mat_count}")

# Verify Timestamp at offset 672
ts = struct.unpack_from("<d", payload, 672)[0]
test("T3.15: Timestamp at offset 672",
      abs(ts - 1234567890.0) < 0.001,
      f"got {ts}")

# Verify GeometryHash at offset 680 (Phase 10J.5F)
geom_hash = struct.unpack_from("<Q", payload, 680)[0]
test("T3.16: GeometryHash == 0 at offset 680 (default/old protocol)",
      geom_hash == 0,
      f"got {geom_hash}")

test("T3.17: payload total size is 688 bytes",
      len(payload) == 688,
      f"got {len(payload)}")


# =============================================================
# T4: Path/name sanitization
# =============================================================

banner("T4 — FbxPath and ObjectName sanitization")

# Path with special chars
path_special = "/home/user/.cache/uelivesync/fbx/g123/my mesh (2).fbx"
name_special = "My Mesh (2) [test]"

payload2 = network.serialize_fbx_import_request(
    guid_obj=test_guid,
    fbx_path=path_special,
    object_name=name_special,
    vert_count=0, tri_count=0, mat_slot_count=0,
    timestamp=0.0,
)

fbx_raw2 = payload2[20:532].rstrip(b'\x00').decode('utf-8')
test("T4.1: FbxPath preserves special chars",
      fbx_raw2 == path_special,
      f"got '{fbx_raw2}'")

name_raw2 = payload2[532:660].rstrip(b'\x00').decode('utf-8')
test("T4.2: ObjectName preserves special chars",
      name_raw2 == name_special,
      f"got '{name_raw2}'")

# Truncation test — FbxPath > 512 bytes
long_path = "/x" * 300  # 600 bytes
payload3 = network.serialize_fbx_import_request(
    guid_obj=test_guid,
    fbx_path=long_path,
    object_name=test_name,
    vert_count=0, tri_count=0, mat_slot_count=0,
    timestamp=0.0,
)
fbx_raw3 = payload3[20:532]
test("T4.3: Long FbxPath truncated to 512 bytes",
      len(fbx_raw3) == 512,
      f"got {len(fbx_raw3)}")

# Truncation test — ObjectName > 128 bytes
long_name = "A" * 200
payload4 = network.serialize_fbx_import_request(
    guid_obj=test_guid,
    fbx_path=test_path,
    object_name=long_name,
    vert_count=0, tri_count=0, mat_slot_count=0,
    timestamp=0.0,
)
name_raw4 = payload4[532:660]
test("T4.4: Long ObjectName truncated to 128 bytes",
      len(name_raw4) == 128,
      f"got {len(name_raw4)}")

# Empty name should be encodable
payload5 = network.serialize_fbx_import_request(
    guid_obj=test_guid,
    fbx_path=test_path,
    object_name="",
    vert_count=0, tri_count=0, mat_slot_count=0,
    timestamp=0.0,
)
name_raw5 = payload5[532:660].rstrip(b'\x00').decode('utf-8')
test("T4.5: Empty ObjectName is encodable",
      name_raw5 == "",
      f"got '{name_raw5}'")

# Unicode in name
payload6 = network.serialize_fbx_import_request(
    guid_obj=test_guid,
    fbx_path=test_path,
    object_name="Café_Mesh",
    vert_count=0, tri_count=0, mat_slot_count=0,
    timestamp=0.0,
)
name_raw6 = payload6[532:660].rstrip(b'\x00').decode('utf-8')
test("T4.6: Unicode name preserved",
      name_raw6 == "Café_Mesh",
      f"got '{name_raw6}'")


# =============================================================
# T5: Protocol signature updated
# =============================================================

banner("T5 — Protocol signature includes FBX")

sig = network.LIVE_SYNC_PROTOCOL_SIG
test("T5.1: Protocol signature is uint32",
      isinstance(sig, int) and 0 <= sig <= 0xFFFFFFFF,
      f"got {sig}")


# =============================================================
# T6: UE kValidTypes includes 0x16 (source text check)
# =============================================================

banner("T6 — UE kValidTypes includes 0x16")

ue_cpp_path = os.path.join(
    os.path.dirname(__file__),
    "..",
    "UE_Plugin",
    "UELiveSync",
    "Source",
    "UELiveSync",
    "Private",
    "UELiveSyncSubsystem.cpp",
)
if os.path.isfile(ue_cpp_path):
    with open(ue_cpp_path, "r") as f:
        content = f.read()
    has_0x16 = "0x16" in content and "kValidTypes" in content
    test("T6.1: UELiveSyncSubsystem.cpp kValidTypes contains 0x16",
          has_0x16,
          "0x16 not found near kValidTypes")
else:
    test("T6.2: UELiveSyncSubsystem.cpp found for source check",
          False,
          f"not found at {ue_cpp_path}")


# =============================================================
# T7: UE EPacketType enum includes PT_FBXImportRequest (source text)
# =============================================================

banner("T7 — UE EPacketType includes PT_FBXImportRequest")

sync_types_h_path = os.path.join(
    os.path.dirname(__file__),
    "..",
    "UE_Plugin",
    "UELiveSync",
    "Source",
    "UELiveSync",
    "Public",
    "SyncTypes.h",
)
if os.path.isfile(sync_types_h_path):
    with open(sync_types_h_path, "r") as f:
        content = f.read()
    has_fbx_enum = "PT_FBXImportRequest" in content
    test("T7.1: EPacketType has PT_FBXImportRequest",
          has_fbx_enum,
          "PT_FBXImportRequest not found in SyncTypes.h")
    has_0x16_enum = "0x16" in content
    test("T7.2: EPacketType has 0x16 value",
          has_0x16_enum,
          "0x16 not found in SyncTypes.h")
else:
    test("T7.3: SyncTypes.h found",
          False,
          f"not found at {sync_types_h_path}")


# =============================================================
# T8: Existing PT_Mesh path not removed or renamed
# =============================================================

banner("T8 — Existing PT_Mesh path preserved")

test("T8.1: PT_Mesh constant still exists",
      hasattr(network, "PT_Mesh"),
      "PT_Mesh not found in network module")

test("T8.2: PT_Mesh == 0x06",
      network.PT_Mesh == 0x06,
      f"got {network.PT_Mesh}")

blender_init_path = os.path.join(
    os.path.dirname(__file__),
    "..",
    "Blender_Addon",
    "__init__.py",
)
if os.path.isfile(blender_init_path):
    with open(blender_init_path, "r") as f:
        content = f.read()
    has_old_op = "uelivesync.sync_selected_mesh_to_ue" in content
    has_new_op = "uelivesync.sync_selected_mesh_to_ue_fbx" in content
    has_old_btn = "uelivesync.sync_selected_mesh_to_ue\"" in content
    has_new_btn = "uelivesync.sync_selected_mesh_to_ue_fbx\"" in content
    test("T8.3: Old operator bl_idname preserved",
          has_old_op,
          "Old operator missing from __init__.py")
    test("T8.4: New FBX operator added",
          has_new_op,
          "New FBX operator not found")
    test("T8.5: Old UI button still exists",
          has_old_btn,
          "Old UI button missing")
    test("T8.6: New FBX UI button exists",
          has_new_btn,
          "New FBX UI button not found")
else:
    test("T8.7: __init__.py found",
          False,
          f"not found at {blender_init_path}")


# =============================================================
# T9: Stage 3A.2 — Component registration and actor lifecycle (source text)
# =============================================================

banner("T9 — Stage 3A.2: No redundant RegisterComponent in HandleFBXImport")

ue_importer_cpp_path = os.path.join(
    os.path.dirname(__file__),
    "..",
    "UE_Plugin",
    "UELiveSync",
    "Source",
    "UELiveSync",
    "Private",
    "FBXImport",
    "LiveSyncFBXImporter.cpp",
)

if os.path.isfile(ue_cpp_path) and os.path.isfile(ue_importer_cpp_path):
    with open(ue_cpp_path, "r") as f:
        subsystem_text = f.read()
    with open(ue_importer_cpp_path, "r") as f:
        importer_text = f.read()

    # Check both files for moved/remaining patterns
    # T9.1: No RegisterComponent immediately after SetStaticMesh in HandleFBXImport
    # Accept both direct call and local-variable call patterns.
    set_mesh_patterns = [
        "GetStaticMeshComponent()->SetStaticMesh",
        "SMC->SetStaticMesh",
    ]
    has_register_after_set = False
    for pattern in set_mesh_patterns:
        pos = importer_text.find(pattern)
        if pos >= 0:
            snippet = importer_text[pos:pos + 500]
            if "RegisterComponent" in snippet:
                has_register_after_set = True
                break
    test("T9.1: No RegisterComponent after SetStaticMesh in HandleFBXImport",
          not has_register_after_set,
          "RegisterComponent() still present after SetStaticMesh")

    # T9.2: SetStaticMesh still present in importer
    has_set_static_mesh = (
        "GetStaticMeshComponent()->SetStaticMesh" in importer_text
        or "SMC->SetStaticMesh" in importer_text
    )
    test("T9.2: SetStaticMesh still present in importer",
          has_set_static_mesh,
          "SetStaticMesh call not found in LiveSyncFBXImporter.cpp")

    # T9.3: ActorCache.Add with Request.ObjectGUID exists (via callback in subsystem)
    has_dispatch_add = "ActorCache.Add(G, A)" in subsystem_text
    has_importer_cached = "OnActorCached" in importer_text
    has_object_guid_importer = "Request.ObjectGUID" in importer_text
    test("T9.3: ActorCache callback dispatch + importer logic present",
          has_dispatch_add and has_importer_cached and has_object_guid_importer,
          "ActorCache callback or ObjectGUID not found")

    # T9.4: LiveSync_GUID tag logic still exists in importer
    has_guid_tag = "LiveSync_GUID=" in importer_text
    test("T9.4: LiveSync_GUID tag logic still present in importer",
          has_guid_tag,
          "LiveSync_GUID tag not found")
else:
    test("T9.5: UELiveSyncSubsystem.cpp and LiveSyncFBXImporter.cpp found",
          False,
          f"missing subsystem={os.path.isfile(ue_cpp_path)} importer={os.path.isfile(ue_importer_cpp_path)}")


# =============================================================
# T10: Stage 3A.4 — Helper extraction + log marker anchors
# =============================================================

banner("T10 — Stage 3A.4: Helper functions and log markers in LiveSyncFBXImporter")

if os.path.isfile(ue_importer_cpp_path):
    with open(ue_importer_cpp_path, "r") as f:
        importer_text = f.read()

    # T10.1–4: Helper function names exist
    test("T10.1: ValidatePayloadSize helper exists",
          "ValidatePayloadSize" in importer_text,
          "ValidatePayloadSize not found in LiveSyncFBXImporter.cpp")

    test("T10.2: ValidateVersion helper exists",
          "ValidateVersion" in importer_text,
          "ValidateVersion not found")

    test("T10.3: ValidatePathSecurity helper exists",
          "ValidatePathSecurity" in importer_text,
          "ValidatePathSecurity not found")

    test("T10.4: SanitizeObjectName helper exists",
          "SanitizeObjectName" in importer_text,
          "SanitizeObjectName not found")

    # T10.5–9: Rejection log markers preserved
    test("T10.5: [FBX] Truncated request log marker",
          "[FBX] Truncated request" in importer_text,
          "Truncated request log marker missing")

    test("T10.6: [FBX] Unsupported version log marker",
          "[FBX] Unsupported version" in importer_text,
          "Unsupported version log marker missing")

    test("T10.7: [FBX] File not found log marker",
          "[FBX] File not found" in importer_text,
          "File not found log marker missing")

    test("T10.8: [FBX] Path outside allowed root log marker",
          "[FBX] Path outside allowed root" in importer_text,
          "Path outside allowed root log marker missing")

    test("T10.9: [FBX] Path contains '..' log marker",
          "[FBX] Path contains '..'" in importer_text,
          "Path contains .. log marker missing")

    # T10.10: "Unnamed" fallback
    test("T10.10: 'Unnamed' fallback preserved",
          "Unnamed" in importer_text,
          "Unnamed fallback not found")

    # T10.11: /Game/UELiveSync/Imported destination
    test("T10.11: Asset destination /Game/UELiveSync/Imported",
          "/Game/UELiveSync/Imported" in importer_text,
          "Asset destination not found")

    # T10.12: Actor update branch (SetStaticMesh)
    has_set_mesh = (
        "GetStaticMeshComponent()->SetStaticMesh" in importer_text
        or "SMC->SetStaticMesh" in importer_text
    )
    test("T10.12: Actor update branch (SetStaticMesh)",
          has_set_mesh,
          "SetStaticMesh not found in importer")

    # T10.13: Actor spawn branch (!MeshActor)
    test("T10.13: Actor spawn branch (!MeshActor)",
          "if (!MeshActor)" in importer_text,
          "Spawn branch not found in importer")

    # T10.14: LiveSync_GUID tag
    test("T10.14: LiveSync_GUID tag logic",
          "LiveSync_GUID=" in importer_text,
          "LiveSync_GUID tag not found")
else:
    test("T10.15: LiveSyncFBXImporter.cpp found",
          False,
          f"not found at {ue_importer_cpp_path}")


# =============================================================
# T11: Stage 3B — FBX Material Slot Count Logging
# =============================================================

banner("T11 — Stage 3B: MatSlotCount referenced and logged in LiveSyncFBXImporter")

if os.path.isfile(ue_importer_cpp_path):
    with open(ue_importer_cpp_path, "r") as f:
        importer_text = f.read()

    # T11.1: Request.MatSlotCount referenced in importer
    test("T11.1: Request.MatSlotCount referenced in importer",
          "Request.MatSlotCount" in importer_text,
          "Request.MatSlotCount not found")

    # T11.2: Import success log includes mat slot count
    test("T11.2: Import success log includes mat slot count",
          "mat slots" in importer_text or "MatSlotCount" in importer_text,
          "Material slot count not referenced in log")

    # T11.3: PT_FBXImportRequest payload layout unchanged
    if os.path.isfile(sync_types_h_path):
        with open(sync_types_h_path, "r") as f:
            types_text = f.read()
        has_fbx_request = "FFBXImportRequestPayload" in types_text
        has_mat_slot = "MatSlotCount" in types_text
        has_680 = "680" in types_text  # static_assert size
        test("T11.3: FFBXImportRequestPayload layout unchanged",
              has_fbx_request and has_mat_slot and has_680,
              f"FBX request struct check failed: fbx_req={has_fbx_request} mat_slot={has_mat_slot} size680={has_680}")
    else:
        test("T11.3: SyncTypes.h found",
              False,
              f"not found at {sync_types_h_path}")

    # T11.4: No PT_Material handler modification in this file
    test("T11.4: No PT_Material handler changes in importer",
          "HandleMaterialDef" not in importer_text,
          "HandleMaterialDef should not be in LiveSyncFBXImporter.cpp")

    # T11.5: Existing /Game/UELiveSync/Imported destination unchanged
    test("T11.5: /Game/UELiveSync/Imported destination unchanged",
          "/Game/UELiveSync/Imported" in importer_text,
          "Asset destination missing from importer")

    # T11.6: Existing LiveSync_GUID tag behavior unchanged
    test("T11.6: LiveSync_GUID tag behavior unchanged",
          "LiveSync_GUID=" in importer_text,
          "LiveSync_GUID tag missing from importer")
else:
    test("T11.7: LiveSyncFBXImporter.cpp found",
          False,
          f"not found at {ue_importer_cpp_path}")


# =============================================================
# T12: Stage 4A — FBX Temp Import Lifecycle Diagnostics (10J.6)
# =============================================================

banner("T12 — Stage 4A: FBX temp import lifecycle diagnostics")

if os.path.isfile(ue_importer_cpp_path):
    with open(ue_importer_cpp_path, "r") as f:
        importer_text = f.read()

    # T12.1: TEMP_IMPORT marker present (replaced old [FBX] Created new imported asset)
    test("T12.1: [FBX][TEMP_IMPORT] log marker present",
          "[FBX][TEMP_IMPORT]" in importer_text,
          "TEMP_IMPORT log marker missing from LiveSyncFBXImporter.cpp")

    # T12.2: TEMP_ASSIGN marker present (replaced old [FBX] Replaced existing imported asset)
    test("T12.2: [FBX][TEMP_ASSIGN] log marker present",
          "[FBX][TEMP_ASSIGN]" in importer_text,
          "TEMP_ASSIGN log marker missing from LiveSyncFBXImporter.cpp")

    # T12.3: TEMP_CLEANUP marker present (temp mesh cleanup lifecycle)
    test("T12.3: [FBX][TEMP_CLEANUP] log marker present",
          "[FBX][TEMP_CLEANUP]" in importer_text,
          "TEMP_CLEANUP log marker missing from LiveSyncFBXImporter.cpp")

    # T12.4: Existing /Game/UELiveSync/Imported destination unchanged
    test("T12.4: /Game/UELiveSync/Imported destination unchanged",
          "/Game/UELiveSync/Imported" in importer_text,
          "Asset destination missing from importer")

    # T12.5: Existing import success marker unchanged
    test("T12.5: [FBX] Imported StaticMesh log marker unchanged",
          "[FBX] Imported StaticMesh" in importer_text,
          "Import success log marker missing")

    # T12.6: DeleteObject used for temp cleanup (intentional in 10J.6)
    # Temp mesh cleanup deletes rejected pending meshes via ObjectTools::DeleteObjects
    test("T12.6: DeleteObject present for temp cleanup lifecycle",
          "DeleteObject" in importer_text,
          "DeleteObject expected for temp cleanup in LiveSyncFBXImporter.cpp")

    # T12.7: No DeletePackage API added
    test("T12.7: No DeletePackage API added",
          "DeletePackage" not in importer_text,
          "DeletePackage should not appear in LiveSyncFBXImporter.cpp")

    # T12.8: ObjectTools::DeleteObjects used for temp cleanup (intentional in 10J.6)
    test("T12.8: ObjectTools::DeleteObjects present for temp cleanup",
          "ObjectTools::DeleteObjects" in importer_text,
          "ObjectTools::DeleteObjects expected for temp cleanup in LiveSyncFBXImporter.cpp")

    # T12.9: No CollectGarbage added
    test("T12.9: No CollectGarbage added",
          "CollectGarbage" not in importer_text,
          "CollectGarbage should not appear in LiveSyncFBXImporter.cpp")

    # T12.10: No FBXImportReplacedExisting counter
    test("T12.10: No FBXImportReplacedExisting counter added",
          "FBXImportReplacedExisting" not in importer_text,
          "FBXImportReplacedExisting counter should not be added")

    # T12.11: SyncTypes.h has no FBXImportReplacedExisting
    if os.path.isfile(sync_types_h_path):
        with open(sync_types_h_path, "r") as f:
            types_text = f.read()
        test("T12.11: SyncTypes.h has no FBXImportReplacedExisting",
              "FBXImportReplacedExisting" not in types_text,
              "FBXImportReplacedExisting should not appear in SyncTypes.h")
    else:
        test("T12.12: SyncTypes.h found",
              False,
              f"not found at {sync_types_h_path}")
else:
    test("T12.13: LiveSyncFBXImporter.cpp found",
          False,
          f"not found at {ue_importer_cpp_path}")


# =============================================================
# T13: Stage 4B — FBX Scene Unit Conversion (bConvertSceneUnit)
# =============================================================

banner("T13 — Stage 4B: FBX scene unit conversion")

init_py_path = os.path.join(
    os.path.dirname(__file__),
    "..", "Blender_Addon", "__init__.py")

if os.path.isfile(ue_importer_cpp_path):
    with open(ue_importer_cpp_path, "r") as f:
        importer_text = f.read()

    # T13.1: bConvertSceneUnit exists
    test("T13.1: bConvertSceneUnit exists in LiveSyncFBXImporter.cpp",
          "bConvertSceneUnit" in importer_text,
          "bConvertSceneUnit not found")

    # T13.2: StaticMeshImportData->bConvertSceneUnit = true
    test("T13.2: StaticMeshImportData->bConvertSceneUnit = true set",
          "StaticMeshImportData->bConvertSceneUnit = true" in importer_text,
          "bConvertSceneUnit = true not set on StaticMeshImportData")

    # T13.3: No bConvertSceneUnit = false
    test("T13.3: No bConvertSceneUnit = false in LiveSyncFBXImporter.cpp",
          "bConvertSceneUnit = false" not in importer_text,
          "bConvertSceneUnit = false should not appear")

    # T13.4: No ImportUniformScale change
    test("T13.4: No ImportUniformScale added",
          "ImportUniformScale" not in importer_text,
          "ImportUniformScale should not appear in LiveSyncFBXImporter.cpp")
else:
    test("T13.5: LiveSyncFBXImporter.cpp found",
          False,
          f"not found at {ue_importer_cpp_path}")

if os.path.isfile(init_py_path):
    with open(init_py_path, "r") as f:
        init_text = f.read()

    # T13.6: Blender still uses FBX_SCALE_UNITS
    test("T13.6: Blender __init__.py uses apply_scale_options='FBX_SCALE_UNITS'",
          "apply_scale_options='FBX_SCALE_UNITS'" in init_text,
          "FBX_SCALE_UNITS option missing from Blender export")
else:
    test("T13.7: Blender __init__.py found",
          False,
          f"not found at {init_py_path}")

if os.path.isfile(ue_importer_cpp_path):
    with open(ue_importer_cpp_path, "r") as f:
        importer_text = f.read()

    # T13.8: FbxStaticMeshImportData.h include added
    test("T13.8: FbxStaticMeshImportData.h included",
          '#include "Factories/FbxStaticMeshImportData.h"' in importer_text,
          "FbxStaticMeshImportData.h include missing")
else:
    test("T13.9: LiveSyncFBXImporter.cpp found (re-check)",
          False,
          f"not found at {ue_importer_cpp_path}")


# =============================================================
# T14: Stage 5 — Unique temp import path diagnostics (10J.5Q/10J.6)
# =============================================================

banner("T14 — Stage 5: Unique temp import path diagnostics")

if os.path.isfile(ue_importer_cpp_path):
    with open(ue_importer_cpp_path, "r") as f:
        importer_text = f.read()

    # T14.1: FIRST_IMPORT marker present (replaces old rename detection)
    test("T14.1: [FBX][FIRST_IMPORT] log marker present",
          "[FBX][FIRST_IMPORT]" in importer_text,
          "FIRST_IMPORT log marker missing from LiveSyncFBXImporter.cpp")

    # T14.2: COALESCE skip/reimport decision logic present
    test("T14.2: [FBX][COALESCE] skip/reimport decision present",
          "[FBX][COALESCE]" in importer_text,
          "COALESCE decision logic missing from LiveSyncFBXImporter.cpp")

    # T14.3: TEMP_CLEANUP lifecycle present (replaces orphaned warning)
    test("T14.3: [FBX][TEMP_CLEANUP] lifecycle present",
          "[FBX][TEMP_CLEANUP]" in importer_text,
          "TEMP_CLEANUP lifecycle missing from LiveSyncFBXImporter.cpp")

    # T14.4: Updated StaticMeshActor marker present (replaces old Created new asset)
    test("T14.4: [FBX] Updated StaticMeshActor log marker present",
          "[FBX] Updated StaticMeshActor" in importer_text,
          "Updated StaticMeshActor marker missing from LiveSyncFBXImporter.cpp")

    # T14.5: Spawned StaticMeshActor marker present (replaces old Replaced existing)
    test("T14.5: [FBX] Spawned StaticMeshActor log marker present",
          "[FBX] Spawned StaticMeshActor" in importer_text,
          "Spawned StaticMeshActor marker missing from LiveSyncFBXImporter.cpp")

    # T14.6: DeleteObject used for temp cleanup (intentional in 10J.6)
    text_after_handle = importer_text.split(
        "FLiveSyncFBXImporter::HandleImport")[1] if len(
            importer_text.split(
                "FLiveSyncFBXImporter::HandleImport")) > 1 else importer_text
    test("T14.6: DeleteObject present for temp cleanup lifecycle",
          "DeleteObject" in text_after_handle,
          "DeleteObject expected for temp cleanup in HandleImport")

    # T14.7: No DeletePackage API added
    test("T14.7: No DeletePackage API added",
          "DeletePackage" not in text_after_handle,
          "DeletePackage should not appear in LiveSyncFBXImporter.cpp")

    # T14.8: ObjectTools::DeleteObjects used for temp cleanup (intentional in 10J.6)
    test("T14.8: ObjectTools::DeleteObjects present for temp cleanup",
          "ObjectTools::DeleteObjects" in text_after_handle,
          "ObjectTools::DeleteObjects expected for temp cleanup in HandleImport")

    # T14.9: No CollectGarbage added
    test("T14.9: No CollectGarbage added",
          "CollectGarbage" not in text_after_handle,
          "CollectGarbage should not appear in LiveSyncFBXImporter.cpp")

    # T14.10: No FBXImportReplacedExisting counter
    test("T14.10: No FBXImportReplacedExisting counter",
          "FBXImportReplacedExisting" not in importer_text,
          "FBXImportReplacedExisting counter found")

else:
    test("T14.11: LiveSyncFBXImporter.cpp found",
          False,
          f"not found at {ue_importer_cpp_path}")


# =============================================================
# Summary
# =============================================================

print(f"\n{'=' * 60}")
print(f"  PASS: {PASS}  FAIL: {FAIL}")
print(f"{'=' * 60}")

if FAIL > 0:
    sys.exit(1)
else:
    sys.exit(0)
