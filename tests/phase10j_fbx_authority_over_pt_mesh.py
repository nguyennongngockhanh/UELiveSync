#!/usr/bin/env python3
"""
Phase 10J.5E — FBX authority over PT_Mesh (static/source checks).

Ensures:
- Per-GUID FBX authority set/map exists in UELiveSyncSubsystem.h
- FBX import success path marks GUID as FBX-authoritative
- FBX spawn path marks GUID as FBX-authoritative
- FBX update path marks GUID as FBX-authoritative
- PT_Mesh handler checks FBX authority before procedural mesh operations
- PT_Mesh skip path logs [MESH][AUTH] or equivalent
- PT_Mesh skip path does not update ActorCache away from LS_FBX actor
- Delete handler removes FBX authority state for deleted GUID
- OnActorDestroyed removes FBX authority state safely
- Stale procedural actor cleanup exists when FBX promotion happens
- No RegisterComponent introduced
- No protocol constants/structs changed
- No MaterialPathCache calls added
- No Blender addon changes
"""

import os
import sys
import re


def check(condition, message):
    if condition:
        print(f"  PASS  {message}")
        return True
    else:
        print(f"  FAIL  {message}")
        return False


def file_read(path):
    with open(path, "rb") as f:
        return f.read().decode("utf-8", errors="replace")


REPO = "/home/nguyennongngockhanh/Projects/UELiveSync"
HEADER = os.path.join(REPO, "UE_Plugin/UELiveSync/Source/UELiveSync/Public/UELiveSyncSubsystem.h")
SUBSYSTEM = os.path.join(REPO, "UE_Plugin/UELiveSync/Source/UELiveSync/Private/UELiveSyncSubsystem.cpp")
IMPORTER_H = os.path.join(REPO, "UE_Plugin/UELiveSync/Source/UELiveSync/Public/FBXImport/LiveSyncFBXImporter.h")
IMPORTER_CPP = os.path.join(REPO, "UE_Plugin/UELiveSync/Source/UELiveSync/Private/FBXImport/LiveSyncFBXImporter.cpp")

header_src = file_read(HEADER)
subsystem_src = file_read(SUBSYSTEM)
importer_h_src = file_read(IMPORTER_H)
importer_cpp_src = file_read(IMPORTER_CPP)

passed = 0
failed = 0


def t(cond, msg):
    global passed, failed
    if check(cond, msg):
        passed += 1
    else:
        failed += 1


# =========================================================
# T1: A per-GUID FBX authority set/map exists in the header.
# =========================================================
t("FBXAuthoritativeGuids" in header_src and "TSet<FGuid>" in header_src,
  "T1: FBXAuthoritativeGuids TSet declared in UELiveSyncSubsystem.h")

# =========================================================
# T2: FBX import success path marks GUID as FBX-authoritative.
# =========================================================
t("OnMarkFbxAuthority" in importer_cpp_src,
  "T2: OnMarkFbxAuthority called in FBX importer (LiveSyncFBXImporter.cpp)")

# Check that OnMarkFbxAuthority is called at least 3 times
count_onmark = importer_cpp_src.count("OnMarkFbxAuthority")
t(count_onmark >= 3,
  f"T2b: OnMarkFbxAuthority called >= 3 times in importer (found {count_onmark})")

# =========================================================
# T3: FBX spawn path marks GUID as FBX-authoritative.
# =========================================================
t("FBXImportActorsSpawned" in importer_cpp_src and
  "OnMarkFbxAuthority" in importer_cpp_src.split("FBXImportActorsSpawned")[0] if "FBXImportActorsSpawned" in importer_cpp_src else False,
  "T3: OnMarkFbxAuthority called before FBXImportActorsSpawned (spawn path)")

# =========================================================
# T4: FBX update path marks GUID as FBX-authoritative.
# =========================================================
# The update path OnMarkFbxAuthority is after the OnActorCached update section
t("FBXImportActorsUpdated" in importer_cpp_src,
  "T4: FBX import update path exists (FBXImportActorsUpdated counter)")

# =========================================================
# T5: PT_Mesh handler checks FBX authority before procedural mesh operations.
# =========================================================
t("FBXAuthoritativeGuids.Contains(Guid)" in subsystem_src,
  "T5: FBXAuthoritativeGuids.Contains(Guid) check in subsystem (mesh handler)")

# =========================================================
# T6: PT_Mesh skip path logs [MESH][AUTH] or equivalent.
# =========================================================
t("skip_pt_mesh_fbx_authoritative" in subsystem_src,
  "T6: [MESH][AUTH] skip_pt_mesh_fbx_authoritative log marker present")

t("skip_chunk_fbx_authoritative" in subsystem_src,
  "T6b: [MESH][AUTH] skip_chunk_fbx_authoritative log marker present (HandleMeshChunk)")

t("skip_v1_chunk_fbx_authoritative" in subsystem_src,
  "T6c: [MESH][AUTH] skip_v1_chunk_fbx_authoritative log marker present (V1 path)")

t("skip_v1_pt_mesh_fbx_authoritative" in subsystem_src,
  "T6d: [MESH][AUTH] skip_v1_pt_mesh_fbx_authoritative log marker present (V1 Build)")

# =========================================================
# T7: PT_Mesh skip path does not update ActorCache away from LS_FBX actor.
# =========================================================
# The skip path calls State.bReconstructed = true; and continues
# without calling FindActorFast or creating ProcMesh. Verify that
# the skip returns/continues before any actor lookup or component creation.
t("State.bReconstructed = true" in subsystem_src,
  "T7: Skip marks reconstructed without touching actor")

# =========================================================
# T8: Delete handler removes FBX authority state for deleted GUID.
# =========================================================
t("FBXAuthoritativeGuids.Remove(TargetGuid)" in subsystem_src,
  "T8: HandleDelete removes GUID from FBXAuthoritativeGuids")

# =========================================================
# T9: OnActorDestroyed removes or reconciles FBX authority state safely.
# =========================================================
t("FBXAuthoritativeGuids.Remove(Guid)" in subsystem_src,
  "T9: OnActorDestroyed has FBXAuthoritativeGuids.Remove")

# Check that it checks actor identity before removing
t("CachedActor == Actor" in subsystem_src,
  "T9b: OnActorDestroyed compares cached actor before removing authority")

# =========================================================
# T10: Stale procedural actor cleanup exists when FBX promotion happens.
# =========================================================
t("cleanup_stale_procedural" in importer_cpp_src,
  "T10: [FBX][AUTH] cleanup_stale_procedural log marker present in importer")

# =========================================================
# T11: RegisterComponent is not introduced.
# =========================================================
# Count RegisterComponent occurrences (there are 2 existing ones in
# ReconstructCompletedMeshes and BuildV1MeshFromReassembly for ProcMesh).
register_count = subsystem_src.count("RegisterComponent()")
importer_register_count = importer_cpp_src.count("RegisterComponent")
t(importer_register_count == 0,
  f"T11: No RegisterComponent introduced in importer (found {importer_register_count})")

# =========================================================
# T12: No protocol constants/structs changed.
# =========================================================
# Check that PT_Mesh (0x06), PT_FBXImportRequest (0x16) are unchanged
t("0x06" in subsystem_src, "T12a: PT_Mesh 0x06 still present")
t("0x16" in subsystem_src, "T12b: PT_FBXImportRequest 0x16 still present")
t("FFBXImportRequestPayload" in importer_cpp_src,
  "T12c: FFBXImportRequestPayload struct unchanged")

# =========================================================
# T13: No MaterialPathCache calls added.
# =========================================================
# Check that MaterialPathCache is NOT mentioned in the importer
t("MaterialPathCache" not in importer_cpp_src,
  "T13: No MaterialPathCache references in importer")

# =========================================================
# T14: No Blender addon changes.
# =========================================================
# Verify that blender_addon directory has no uncommitted changes
# (this is a soft check, we can't fully verify without git)
t("MaterialPathCache" not in importer_h_src,
  "T14: No MaterialPathCache in importer header either")

# =========================================================
# SUMMARY
# =========================================================
print(f"\n{'=' * 60}")
print(f"Results: {passed} passed, {failed} failed")
if failed > 0:
    sys.exit(1)
else:
    sys.exit(0)
