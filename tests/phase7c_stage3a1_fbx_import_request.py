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

banner("T2 — FBX_IMPORT_REQUEST_PAYLOAD_SIZE == 680")

test("T2.1: FBX_IMPORT_REQUEST_PAYLOAD_SIZE == 680",
      network.FBX_IMPORT_REQUEST_PAYLOAD_SIZE == 680,
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

test("T3.1: payload is 680 bytes",
      len(payload) == 680,
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

if os.path.isfile(ue_cpp_path):
    with open(ue_cpp_path, "r") as f:
        lines = f.readlines()

    full_text = "".join(lines)

    # T9.1: No RegisterComponent immediately after SetStaticMesh in HandleFBXImport
    set_mesh_pos = full_text.find("GetStaticMeshComponent()->SetStaticMesh")
    has_register_after_set = False
    if set_mesh_pos >= 0:
        snippet = full_text[set_mesh_pos:set_mesh_pos + 500]
        has_register_after_set = "RegisterComponent" in snippet
    test("T9.1: No RegisterComponent after SetStaticMesh in HandleFBXImport",
          not has_register_after_set,
          "RegisterComponent() still present after SetStaticMesh")

    # T9.2: SetStaticMesh still present in HandleFBXImport
    has_set_static_mesh = "GetStaticMeshComponent()->SetStaticMesh" in full_text
    test("T9.2: SetStaticMesh still present in subsystem",
          has_set_static_mesh,
          "SetStaticMesh call not found in UELiveSyncSubsystem.cpp")

    # T9.3: ActorCache.Add with Request.ObjectGUID exists (spans multiple lines)
    has_actor_cache_add = "ActorCache.Add(" in full_text
    has_object_guid = "Request.ObjectGUID" in full_text
    test("T9.3: ActorCache.Add with Request.ObjectGUID still present",
          has_actor_cache_add and has_object_guid,
          "ActorCache.Add or ObjectGUID not found")

    # T9.4: LiveSync_GUID tag logic still exists
    has_guid_tag = "LiveSync_GUID=" in full_text
    test("T9.4: LiveSync_GUID tag logic still present",
          has_guid_tag,
          "LiveSync_GUID tag not found")
else:
    test("T9.5: UELiveSyncSubsystem.cpp found for source check",
          False,
          f"not found at {ue_cpp_path}")


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
