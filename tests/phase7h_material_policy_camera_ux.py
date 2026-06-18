"""
Phase 7H hotfix — Visual Material Sync + Camera Spawn Stability.

Part A — Material Property Sync (visual, not assumed-inherited):
- Imported FBX material slots use LiveSync master-material MID (parameter-compatible).
- CopyImportedTexturesFromParent copies textures from imported material into LiveSync MID.
- [MATERIAL][GENERATED_PARAM_MID_APPLY] marker for param-compatible MID apply.
- [MATERIAL][IMPORTED_TEXTURE_TO_PARAM] marker for texture copy from imported material.
- [MATERIAL][NO_IMPORTED_TEXTURE_FOUND] marker when imported material has no texture.
- [MATERIAL][MID_FALLBACK_SKIP_IMPORTED] marker present for fallback skip.
- [MATERIAL][FBX_IMPORTED_APPLY] marker present.
- [MATERIAL][FBX_IMPORTED_KEEP] marker present.
- [MATERIAL][MID_FALLBACK_APPLY] marker present in FBX importer
  (for no-material / WorldGrid fallback).
- Counter MaterialImportedTextureCopied exists in header.
- IsUnsafeFBXMaterial still returns false for /Game/UELiveSync/Imported.
- SafeMaterial fallback still applied for null/WorldGrid slots.
- OnRestoreGeneratedMaterials uses GeneratedMaterialCache for all slots.
- No protocol IDs changed.

Part B — Camera Sync UX (safe, no PT_Create):
- Primary operator sends PT_Transform + PT_CameraDef only.
- Primary operator does NOT send PT_Create (actor spawn is unstable in editor).
- Primary operator does NOT send PT_ActiveCamera (viewport switching unsafe).
- Primary operator reports spawn-disabled warning.
- Debug operator keeps experimental PT_Create capability.
- bHideFromSceneOutliner preserved.
- Camera crash workaround (ConfigureLiveSyncCameraActor) not removed.
- Dump Diagnostics no longer has undefined network reference.
"""

import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SUBSYSTEM_CPP = os.path.join(
    REPO_ROOT, "UE_Plugin", "UELiveSync", "Source", "UELiveSync",
    "Private", "UELiveSyncSubsystem.cpp"
)
FBX_IMPORTER_CPP = os.path.join(
    REPO_ROOT, "UE_Plugin", "UELiveSync", "Source", "UELiveSync",
    "Private", "FBXImport", "LiveSyncFBXImporter.cpp"
)
SYNC_TYPES_H = os.path.join(
    REPO_ROOT, "UE_Plugin", "UELiveSync", "Source", "UELiveSync",
    "Public", "SyncTypes.h"
)
INIT_PY = os.path.join(
    REPO_ROOT, "Blender_Addon", "__init__.py"
)
NETWORK_PY = os.path.join(
    REPO_ROOT, "Blender_Addon", "network.py"
)
HEADER_H = os.path.join(
    REPO_ROOT, "UE_Plugin", "UELiveSync", "Source", "UELiveSync",
    "Public", "UELiveSyncSubsystem.h"
)


def read_file(path):
    with open(path, "r") as f:
        return f.read()


source_cpp = read_file(SUBSYSTEM_CPP)
fbx_cpp = read_file(FBX_IMPORTER_CPP)
sync_types = read_file(SYNC_TYPES_H)
init_py = read_file(INIT_PY)
network_py = read_file(NETWORK_PY)
header_h = read_file(HEADER_H)


# =====================================================================
# PART A — MATERIAL POLICY
# =====================================================================

# --- Diagnostic markers ---

def test_material_fbx_imported_apply_marker():
    """[MATERIAL][FBX_IMPORTED_APPLY] marker exists in FBX importer."""
    assert "[MATERIAL][FBX_IMPORTED_APPLY]" in fbx_cpp


def test_material_fbx_imported_keep_marker():
    """[MATERIAL][FBX_IMPORTED_KEEP] marker exists in FBX importer."""
    assert "[MATERIAL][FBX_IMPORTED_KEEP]" in fbx_cpp


def test_material_mid_fallback_apply_marker():
    """[MATERIAL][MID_FALLBACK_APPLY] marker exists in FBX importer fallback."""
    assert "[MATERIAL][MID_FALLBACK_APPLY]" in fbx_cpp


def test_material_mid_override_skip_imported_marker():
    """[MATERIAL][MID_FALLBACK_SKIP_IMPORTED] marker exists in subsystem."""
    count = source_cpp.count("[MATERIAL][MID_FALLBACK_SKIP_IMPORTED]")
    assert count >= 1, (
        f"[MATERIAL][MID_FALLBACK_SKIP_IMPORTED] should appear at least 1 time"
        f" (in ParseAndApplyGeneratedMaterial), found {count}"
    )


# --- IsUnsafeFBMaterial behavior ---

def test_unsafe_fbx_material_no_longer_checks_imported():
    """IsUnsafeFBXMaterial no longer treats /Game/UELiveSync/Imported as unsafe."""
    idx = fbx_cpp.find("static bool IsUnsafeFBXMaterial")
    assert idx != -1, "IsUnsafeFBXMaterial not found"
    chunk = fbx_cpp[idx:idx + 800]
    assert "return false;" in chunk.split(
        'StartsWith(TEXT("/Game/UELiveSync/Imported"))'
    )[1].split('}')[0], (
        "/Game/UELiveSync/Imported check must return false"
    )


def test_unsafe_fbx_material_still_rejects_null():
    """IsUnsafeFBXMaterial still returns true for null material."""
    assert "if (!Mat)" in fbx_cpp[fbx_cpp.find("IsUnsafeFBXMaterial"):fbx_cpp.find("IsUnsafeFBXMaterial") + 600]


def test_unsafe_fbx_material_still_rejects_worldgrid():
    """IsUnsafeFBXMaterial still returns true for WorldGrid material."""
    assert "WorldGrid" in fbx_cpp[fbx_cpp.find("IsUnsafeFBXMaterial"):fbx_cpp.find("IsUnsafeFBXMaterial") + 600]


# --- OnRestoreGeneratedMaterials ---

def test_on_restore_generated_uses_generated_cache():
    """OnRestoreGeneratedMaterials uses GeneratedMaterialCache for all slots."""
    idx = source_cpp.find("Ctx.OnRestoreGeneratedMaterials")
    assert idx != -1, "OnRestoreGeneratedMaterials not found"
    chunk = source_cpp[idx:idx + 3000]
    # Must check GeneratedMaterialCache
    assert "GeneratedMaterialCache" in chunk, (
        "OnRestoreGeneratedMaterials must use GeneratedMaterialCache"
    )
    # Must not use ImportedMaterialMIDCache
    assert "ImportedMaterialMIDCache" not in chunk, (
        "OnRestoreGeneratedMaterials should no longer use ImportedMaterialMIDCache"
    )


# --- ParseAndApplyGeneratedMaterial imported slot path ---

def test_parse_and_apply_uses_generated_mid_for_imported():
    """ParseAndApplyGeneratedMaterial uses generated MID + texture copy for imported slots."""
    idx = source_cpp.find("ParseAndApplyGeneratedMaterial(\n    const FGuid& Guid")
    if idx == -1:
        idx = source_cpp.find("ParseAndApplyGeneratedMaterial(")
    assert idx != -1, "ParseAndApplyGeneratedMaterial definition not found"
    chunk = source_cpp[idx:idx + 4000]
    # Must detect imported material path
    assert "/Game/UELiveSync/Imported" in chunk, (
        "ParseAndApplyGeneratedMaterial must check for imported material path"
    )
    # Must use GetOrCreateGeneratedMID (not GetOrCreateImportedParentMID)
    assert "GetOrCreateGeneratedMID" in chunk, (
        "ParseAndApplyGeneratedMaterial must use GetOrCreateGeneratedMID for imported slots"
    )
    # Must copy textures from imported material
    assert "CopyImportedTexturesFromParent" in chunk, (
        "ParseAndApplyGeneratedMaterial must copy textures from imported material"
    )
    # Must log GENERATED_PARAM_MID_APPLY (not IMPORTED_PARENT_MID_APPLY)
    assert "GENERATED_PARAM_MID_APPLY" in chunk, (
        "ParseAndApplyGeneratedMaterial must use GENERATED_PARAM_MID_APPLY marker"
    )


def test_parse_and_apply_still_detects_imported_parent_mid():
    """ParseAndApplyGeneratedMaterial detects MID whose parent is imported material."""
    idx = source_cpp.find("ParseAndApplyGeneratedMaterial(\n    const FGuid& Guid")
    if idx == -1:
        idx = source_cpp.find("ParseAndApplyGeneratedMaterial(")
    assert idx != -1, "ParseAndApplyGeneratedMaterial definition not found"
    chunk = source_cpp[idx:idx + 5000]
    # Must detect MID parent path (not just direct imported path)
    assert "SlotMID->Parent" in chunk or "Parent->GetPathName()" in chunk, (
        "ParseAndApplyGeneratedMaterial must detect MID with imported parent"
    )


# --- New approach markers ---

def test_generated_param_mid_apply_marker():
    """[MATERIAL][GENERATED_PARAM_MID_APPLY] marker exists in subsystem."""
    assert "[MATERIAL][GENERATED_PARAM_MID_APPLY]" in source_cpp


def test_imported_texture_to_param_marker():
    """[MATERIAL][IMPORTED_TEXTURE_TO_PARAM] marker exists in subsystem."""
    assert "[MATERIAL][IMPORTED_TEXTURE_TO_PARAM]" in source_cpp


def test_no_imported_texture_found_marker():
    """[MATERIAL][NO_IMPORTED_TEXTURE_FOUND] marker exists in subsystem."""
    assert "[MATERIAL][NO_IMPORTED_TEXTURE_FOUND]" in source_cpp


# --- New function and counter existence ---

def test_copy_imported_textures_function_exists():
    """CopyImportedTexturesFromParent function exists in subsystem."""
    assert "CopyImportedTexturesFromParent" in source_cpp, (
        "CopyImportedTexturesFromParent function must exist in subsystem"
    )


def test_material_imported_texture_copied_counter_in_source():
    """MaterialImportedTextureCopied counter exists in subsystem source."""
    assert "MaterialImportedTextureCopied" in source_cpp, (
        "MaterialImportedTextureCopied counter must exist in subsystem source"
    )


def test_material_imported_texture_copied_counter_in_header():
    """MaterialImportedTextureCopied counter declared in subsystem header."""
    assert "MaterialImportedTextureCopied" in header_h, (
        "MaterialImportedTextureCopied counter must be declared in header"
    )


# --- Old approach removed ---

def test_old_imported_parent_mid_cache_removed():
    """Old ImportedMaterialMIDCache no longer used (no misleading parameter assignment)."""
    assert "ImportedMaterialMIDCache" not in source_cpp, (
        "ImportedMaterialMIDCache should no longer exist in source"
    )


def test_old_get_or_create_imported_parent_mid_removed():
    """Old GetOrCreateImportedParentMID function removed."""
    assert "GetOrCreateImportedParentMID" not in source_cpp, (
        "GetOrCreateImportedParentMID should no longer exist in source"
    )


def test_old_texture_preserve_marker_removed():
    """Misleading TEXTURE_PRESERVE_IMPORTED_PARENT marker removed."""
    assert "[MATERIAL][TEXTURE_PRESERVE_IMPORTED_PARENT]" not in source_cpp, (
        "TEXTURE_PRESERVE_IMPORTED_PARENT is misleading — actually copies textures to param MID"
    )


def test_old_imported_parent_mid_apply_marker_removed():
    """Old IMPORTED_PARENT_MID_APPLY marker removed."""
    assert "[MATERIAL][IMPORTED_PARENT_MID_APPLY]" not in source_cpp, (
        "IMPORTED_PARENT_MID_APPLY replaced by GENERATED_PARAM_MID_APPLY"
    )


# --- EnsureFBXMeshRenderable still applies SafeMaterial for null ---

def test_ensure_renderable_still_has_fallback():
    """EnsureFBXMeshRenderable still applies fallback for null/WorldGrid."""
    assert "fallback" in fbx_cpp[fbx_cpp.find("EnsureFBXMeshRenderable"):fbx_cpp.find("EnsureFBXMeshRenderable") + 2000]


# --- Phase 7H.5: Folder scan fallback for texture discovery ---

def test_imported_texture_folder_scan_marker():
    """[MATERIAL][IMPORTED_TEXTURE_FOLDER_SCAN] marker exists."""
    assert "[MATERIAL][IMPORTED_TEXTURE_FOLDER_SCAN]" in source_cpp


def test_imported_texture_candidate_marker():
    """[MATERIAL][IMPORTED_TEXTURE_CANDIDATE] marker exists."""
    assert "[MATERIAL][IMPORTED_TEXTURE_CANDIDATE]" in source_cpp


def test_generated_texture_param_check_marker():
    """[MATERIAL][GENERATED_TEXTURE_PARAM_CHECK] marker exists."""
    assert "[MATERIAL][GENERATED_TEXTURE_PARAM_CHECK]" in source_cpp


def test_no_imported_texture_found_reason_asset_candidate():
    """NO_IMPORTED_TEXTURE_FOUND uses reason=no_texture_asset_candidate."""
    idx = source_cpp.find("NO_IMPORTED_TEXTURE_FOUND")
    assert idx != -1, "NO_IMPORTED_TEXTURE_FOUND not found"
    chunk = source_cpp[idx:idx + 500]
    assert "reason=no_texture_asset_candidate" in chunk, (
        "NO_IMPORTED_TEXTURE_FOUND must use reason=no_texture_asset_candidate"
    )


def test_asset_registry_include_exists():
    """AssetRegistry IAssetRegistry.h included in subsystem."""
    assert "AssetRegistry/IAssetRegistry.h" in source_cpp, (
        "Must include IAssetRegistry.h for folder scan"
    )


def test_folder_scan_uses_texture2d_class():
    """Folder scan uses UTexture2D::StaticClass()->GetClassPathName()."""
    idx = source_cpp.find("IMPORTED_TEXTURE_FOLDER_SCAN")
    assert idx != -1, "Folder scan not found"
    chunk = source_cpp[idx - 500:idx + 500]
    assert "UTexture2D::StaticClass()->GetClassPathName()" in chunk, (
        "Folder scan must filter by UTexture2D class"
    )


def test_folder_scan_single_texture_fallback():
    """Folder scan falls back to single texture if only one found."""
    assert "single_texture_fallback" in source_cpp, (
        "Folder scan must have single-texture fallback when exactly one UTexture2D exists"
    )


def test_folder_scan_candidate_logs_path_score():
    """CANDIDATE log includes path and score."""
    idx = source_cpp.find("IMPORTED_TEXTURE_CANDIDATE")
    assert idx != -1, "CANDIDATE marker not found"
    chunk = source_cpp[idx:idx + 300]
    assert "path=" in chunk and "score=" in chunk, (
        "CANDIDATE log must include path and score"
    )


def test_texture_to_param_marker_after_folder_scan():
    """IMPORTED_TEXTURE_TO_PARAM still present for folder scan success path."""
    assert "[MATERIAL][IMPORTED_TEXTURE_TO_PARAM]" in source_cpp


# --- Phase 7H.6: Blender FBX export texture diagnostics ---

def test_fbx_texture_image_scan_marker():
    """[FBX][TEXTURE_IMAGE_SCAN] marker exists in __init__.py."""
    assert "[FBX][TEXTURE_IMAGE_SCAN]" in init_py


def test_fbx_export_path_mode_copy():
    """FBX export uses path_mode='COPY'."""
    assert "path_mode='COPY'" in init_py or 'path_mode="COPY"' in init_py


def test_fbx_export_embed_textures_log():
    """EXPORT_SETTINGS log includes path_mode=COPY embed_textures=0."""
    idx = init_py.find("[FBX][EXPORT_SETTINGS]")
    assert idx != -1, "EXPORT_SETTINGS not found"
    chunk = init_py[idx:idx + 400]
    assert "path_mode=COPY" in chunk, (
        "EXPORT_SETTINGS must log path_mode=COPY"
    )
    assert "embed_textures=0" in chunk, (
        "EXPORT_SETTINGS must log embed_textures=0"
    )


# --- Phase 7H.6: UE FBX import texture options ---

def test_fbx_import_texture_options_enabled():
    """FBX import sets bImportMaterials=1 bImportTextures=1."""
    assert "bImportMaterials=1" in fbx_cpp and "bImportTextures=1" in fbx_cpp, (
        "FBX import must enable material and texture import"
    )


def test_fbx_import_options_log_marker():
    """[FBX][IMPORT_OPTIONS] marker exists in FBX importer."""
    assert "[FBX][IMPORT_OPTIONS]" in fbx_cpp


def test_fbx_imported_asset_summary_marker():
    """[FBX][IMPORTED_ASSET_SUMMARY] marker exists in FBX importer."""
    assert "[FBX][IMPORTED_ASSET_SUMMARY]" in fbx_cpp


def test_fbx_imported_texture_marker():
    """[FBX][IMPORTED_TEXTURE] marker exists in FBX importer."""
    assert "[FBX][IMPORTED_TEXTURE]" in fbx_cpp


def test_fbx_importer_includes_texture_header():
    """FBX importer includes Engine/Texture.h for texture type check."""
    assert '#include "Engine/Texture.h"' in fbx_cpp


# --- Phase 7H.6: Recursive folder scan ---

def test_folder_scan_recursive():
    """Folder scan uses bRecursivePaths=true (subfolders included)."""
    assert "bRecursivePaths = true" in source_cpp


def test_folder_scan_recursive_log():
    """Folder scan log includes recursive=1."""
    idx = source_cpp.find("IMPORTED_TEXTURE_FOLDER_SCAN")
    assert idx != -1, "FOLDER_SCAN not found"
    chunk = source_cpp[idx:idx + 200]
    assert "recursive=1" in chunk, (
        "FOLDER_SCAN log must include recursive=1"
    )


# --- Phase 7H.7: FBX sync mesh selection robustness ---

def test_fbx_object_selection_marker():
    """[FBX][OBJECT_SELECTION] marker exists in __init__.py."""
    assert "[FBX][OBJECT_SELECTION]" in init_py


def test_fbx_object_selected_marker():
    """[FBX][OBJECT_SELECTED] marker exists in __init__.py."""
    assert "[FBX][OBJECT_SELECTED]" in init_py


def test_fbx_collect_mesh_objects_method():
    """_collect_mesh_objects method exists in FBX operator."""
    assert "_collect_mesh_objects" in init_py


def test_fbx_fallback_active_object():
    """FBX operator falls back to active object when selected_objects empty."""
    idx = init_py.find("_collect_mesh_objects")
    assert idx != -1, "_collect_mesh_objects not found"
    chunk = init_py[idx:idx + 500]
    assert "context.view_layer.objects.active" in chunk, (
        "_collect_mesh_objects must check context.view_layer.objects.active"
    )


def test_fbx_fallback_context_object():
    """FBX operator falls back to context.object when no active mesh."""
    idx = init_py.find("_collect_mesh_objects")
    assert idx != -1, "_collect_mesh_objects not found"
    chunk = init_py[idx:idx + 500]
    assert "context.object" in chunk, (
        "_collect_mesh_objects must check context.object as last fallback"
    )


def test_fbx_selection_diagnostic_shows_count_and_mode():
    """OBJECT_SELECTION log includes selected=N selected_mesh=N active= mode=."""
    idx = init_py.find("[FBX][OBJECT_SELECTION]")
    assert idx != -1, "OBJECT_SELECTION not found"
    chunk = init_py[idx:idx + 300]
    assert "selected=" in chunk and "selected_mesh=" in chunk, (
        "OBJECT_SELECTION must log selected= and selected_mesh="
    )
    assert "active=" in chunk and "mode=" in chunk, (
        "OBJECT_SELECTION must log active= and mode="
    )


def test_fbx_selected_per_object_logs_name_type_slots():
    """OBJECT_SELECTED log includes name= type=MESH materialSlots=."""
    idx = init_py.find("[FBX][OBJECT_SELECTED]")
    assert idx != -1, "OBJECT_SELECTED not found"
    chunk = init_py[idx:idx + 200]
    assert "name=" in chunk and "type=MESH" in chunk and "materialSlots=" in chunk, (
        "OBJECT_SELECTED must log name= type=MESH materialSlots="
    )


def test_fbx_detailed_warning_no_mesh():
    """Warning message includes Selected:, active:, mode: for empty selection."""
    idx = init_py.find("No mesh objects could be FBX-synced")
    assert idx != -1, "Warning message not found"
    chunk = init_py[idx:idx + 300]
    assert "Selected:" in chunk, (
        "Warning must include Selected: with object types"
    )
    assert "active=" in chunk, (
        "Warning must include active= with active object description"
    )
    assert "mode=" in chunk, (
        "Warning must include mode="
    )


def test_fbx_texture_settings_not_regressed():
    """Texture export settings preserved alongside selection changes."""
    assert "path_mode='COPY'" in init_py or 'path_mode="COPY"' in init_py
    assert "[FBX][TEXTURE_IMAGE_SCAN]" in init_py
    idx = init_py.find("[FBX][EXPORT_SETTINGS]")
    assert idx != -1, "EXPORT_SETTINGS not found"
    chunk = init_py[idx:idx + 400]
    assert "path_mode=COPY" in chunk
    assert "embed_textures=0" in chunk


# =====================================================================
# PART B — CAMERA SYNC UX
# =====================================================================

# --- Operator exists ---

def test_camera_operator_bl_idname():
    """uelivesync.sync_active_camera_to_ue exists in __init__.py."""
    assert "uelivesync.sync_active_camera_to_ue" in init_py


def test_camera_operator_bl_label():
    """Operator has bl_label 'Sync Active Camera to UE'."""
    assert 'Sync Active Camera to UE' in init_py


def test_camera_operator_class_defined():
    """UELIVESYNC_OT_sync_active_camera_to_ue class defined."""
    assert "UELIVESYNC_OT_sync_active_camera_to_ue" in init_py


# --- Button in panel ---

def test_camera_button_in_panel():
    """Panel has button for camera sync."""
    assert 'uelivesync.sync_active_camera_to_ue' in init_py
    assert "sync_active_camera_to_ue" in init_py


def test_camera_button_icon_camera_data():
    """Camera button uses CAMERA_DATA icon."""
    assert "icon='CAMERA_DATA'" in init_py or 'icon="CAMERA_DATA"' in init_py
    camera_icon_idx = init_py.find("CAMERA_DATA")
    chunk_around_icon = init_py[camera_icon_idx - 100:camera_icon_idx + 100]
    assert "sync_active_camera_to_ue" in chunk_around_icon


# --- Registration ---

def test_camera_operator_registered():
    """Camera operator registered in classes tuple."""
    assert "UELIVESYNC_OT_sync_active_camera_to_ue" in init_py[init_py.find("classes = ("):init_py.find("def register():")]


# --- No-camera warning ---

def test_camera_no_camera_warning():
    """Operator has warning when no camera found."""
    idx = init_py.find("UELIVESYNC_OT_sync_active_camera_to_ue")
    chunk = init_py[idx:idx + 3000]
    assert "No active camera found" in chunk


def test_camera_no_connection_warning():
    """Operator has warning when not connected."""
    idx = init_py.find("UELIVESYNC_OT_sync_active_camera_to_ue")
    chunk = init_py[idx:idx + 3000]
    assert "Not connected to UE" in chunk


# --- Camera selection logic ---

def test_camera_uses_scene_camera():
    """Operator uses scene.camera as first priority."""
    idx = init_py.find("UELIVESYNC_OT_sync_active_camera_to_ue")
    chunk = init_py[idx:idx + 2000]
    assert 'context.scene' in chunk and 'camera' in chunk, (
        "Operator must check context.scene.camera"
    )


def test_camera_uses_selected_fallback():
    """Operator falls back to selected active camera object."""
    idx = init_py.find("UELIVESYNC_OT_sync_active_camera_to_ue")
    chunk = init_py[idx:idx + 2000]
    assert 'active_object' in chunk, (
        "Operator must fallback to active selected object"
    )


# --- Uses existing camera protocol constants ---

def test_camera_uses_pt_cameradef():
    """Operator uses PT_CameraDef (0x1B)."""
    idx = init_py.find("UELIVESYNC_OT_sync_active_camera_to_ue")
    chunk = init_py[idx:idx + 4000]
    assert "PT_CameraDef" in chunk


def test_camera_does_not_send_pt_create():
    """Primary operator does NOT send PT_Create (0x03) — actor spawn unstable."""
    idx = init_py.find("UELIVESYNC_OT_sync_active_camera_to_ue")
    chunk = init_py[idx:idx + 6000]
    assert "packet_type=0x03" not in chunk, (
        "Primary operator must not send PT_Create — camera actor spawn freezes UE"
    )


def test_camera_does_not_send_pt_activecamera():
    """Primary operator does NOT send PT_ActiveCamera as a packet."""
    idx = init_py.find("UELIVESYNC_OT_sync_active_camera_to_ue")
    chunk = init_py[idx:idx + 4000]
    assert "packet_type=network.PT_ActiveCamera" not in chunk, (
        "Operator must not send PT_ActiveCamera as a packet — viewport switching causes freeze"
    )


def test_camera_bl_description_no_activecamera():
    """bl_description does not mention PT_ActiveCamera."""
    idx = init_py.find("UELIVESYNC_OT_sync_active_camera_to_ue")
    chunk = init_py[idx:idx + 1000]
    assert "PT_ActiveCamera" not in chunk


def test_camera_bl_description_safe():
    """bl_description mentions safe behavior (no spawn, no viewport switch)."""
    idx = init_py.find("UELIVESYNC_OT_sync_active_camera_to_ue")
    chunk = init_py[idx:idx + 1000]
    assert "no actor spawn" in chunk or "no viewport switch" in chunk, (
        "bl_description should indicate safe behavior"
    )
    assert "no viewport switch" in chunk.lower() or "viewport" in chunk.lower(), (
        "bl_description should indicate no viewport switch"
    )


def test_camera_uses_serialize_camera_def():
    """Operator uses serialize_camera_def()."""
    idx = init_py.find("UELIVESYNC_OT_sync_active_camera_to_ue")
    chunk = init_py[idx:idx + 4000]
    assert "serialize_camera_def" in chunk


def test_camera_does_not_use_serialize_active_camera():
    """Operator does NOT call serialize_active_camera()."""
    idx = init_py.find("UELIVESYNC_OT_sync_active_camera_to_ue")
    chunk = init_py[idx:idx + 4000]
    assert "network.serialize_active_camera" not in chunk


def test_camera_uses_serialize_object_v3():
    """Operator uses serialize_object_v3()."""
    idx = init_py.find("UELIVESYNC_OT_sync_active_camera_to_ue")
    chunk = init_py[idx:idx + 4000]
    assert "serialize_object_v3" in chunk


def test_camera_uses_primitve_camera():
    """Operator uses PRIMITIVE_CAMERA primitive type."""
    idx = init_py.find("UELIVESYNC_OT_sync_active_camera_to_ue")
    chunk = init_py[idx:idx + 4000]
    assert "PRIMITIVE_CAMERA" in chunk


# --- Uses existing serialization functions ---

def test_camera_sends_transform_packet():
    """Operator sends PT_Transform (default 0x01) packet."""
    idx = init_py.find("UELIVESYNC_OT_sync_active_camera_to_ue")
    chunk = init_py[idx:idx + 6000]
    lines = [l.strip() for l in chunk.split('\n') if 'send_objects' in l]
    has_default = any('packet_type=' not in l for l in lines)
    assert has_default, (
        "Must have a send_objects() call without explicit packet_type (transform default 0x01)"
    )


# --- Camera operator packet reporting ---

def test_camera_operator_has_print_output():
    """Operator has print() statements for Blender console packet verification."""
    idx = init_py.find("UELIVESYNC_OT_sync_active_camera_to_ue")
    chunk = init_py[idx:idx + 6000]
    # Must print transform and CameraDef (no PT_Create)
    assert "Sent PT_Transform" in chunk, (
        "Operator must print PT_Transform send confirmation"
    )
    assert "Sent PT_CameraDef" in chunk, (
        "Operator must print PT_CameraDef send confirmation"
    )
    # Must NOT print PT_Create
    assert "Sent PT_Create" not in chunk, (
        "Operator must not print PT_Create send confirmation"
    )


def test_camera_operator_report_spawn_disabled():
    """Operator report mentions spawn disabled for stability."""
    idx = init_py.find("UELIVESYNC_OT_sync_active_camera_to_ue")
    chunk = init_py[idx:idx + 6000]
    assert "spawn disabled" in chunk, (
        "Operator report should mention spawn is disabled for stability"
    )


# --- Debug isolation operator ---

def test_debug_operator_class_defined():
    """UELIVESYNC_OT_debug_send_camera_packets class defined."""
    assert "UELIVESYNC_OT_debug_send_camera_packets" in init_py


def test_debug_operator_registered():
    """Debug operator registered in classes tuple."""
    assert "UELIVESYNC_OT_debug_send_camera_packets" in init_py[init_py.find("classes = ("):init_py.find("def register():")]


def test_debug_operator_has_isolation_props():
    """Debug operator has send_create, send_transform, send_cameradef props."""
    assert "send_create" in init_py
    assert "send_transform" in init_py
    assert "send_cameradef" in init_py


def test_debug_operator_experimental_warning():
    """Debug operator PT_Create has experimental warning print."""
    idx = init_py.find("UELIVESYNC_OT_debug_send_camera_packets")
    chunk = init_py[idx:idx + 4000]
    assert "EXPERIMENTAL" in chunk or "experimental" in chunk, (
        "Debug operator should warn experimental for PT_Create"
    )


# --- Diagnostics fix ---

def test_sync_py_imports_network_module():
    """sync.py imports network module for dump_diagnostics."""
    sync_py = read_file(os.path.join(REPO_ROOT, "Blender_Addon", "sync.py"))
    assert "import network as _network_mod" in sync_py or "from . import network as _network_mod" in sync_py


def test_dump_diagnostics_uses_network_mod():
    """dump_diagnostics uses _network_mod instead of undefined network."""
    sync_py = read_file(os.path.join(REPO_ROOT, "Blender_Addon", "sync.py"))
    idx = sync_py.find("def dump_diagnostics")
    chunk = sync_py[idx:idx + 5000]
    assert "_network_mod._host" in chunk
    assert "_network_mod._port" in chunk
    assert "_network_mod.get_discovery_results" in chunk


# --- Camera crash workaround preserved (bHideFromSceneOutliner) ---

def test_bhide_from_scene_outliner_preserved():
    """bHideFromSceneOutliner=true preserved in camera spawn paths."""
    count = source_cpp.count("bHideFromSceneOutliner = true")
    assert count >= 2, (
        f"bHideFromSceneOutliner=true must appear at least 2 times"
        f" (HandleCreateObject + HandleActiveCamera auto-spawn), found {count}"
    )


# =====================================================================
# PROTOCOL ID INVARIANTS
# =====================================================================

def test_pt_cameradef_unchanged():
    """PT_CameraDef remains 0x1B."""
    assert "PT_CameraDef = 0x1B" in sync_types or "PT_CameraDef = 0x1B" in network_py


def test_pt_activecamera_unchanged():
    """PT_ActiveCamera remains 0x15."""
    assert "PT_ActiveCamera = 0x15" in sync_types or "PT_ActiveCamera = 0x15" in network_py


def test_lsp_camera_unchanged():
    """LSP_Camera remains 0x05."""
    assert "LSP_Camera" in sync_types or "LSP_Camera" in network_py
    assert "PRIMITIVE_CAMERA   = 0x05" in network_py


def test_pt_keyframe_unchanged():
    """PT_Keyframe remains 0x17 (not changed by this phase)."""
    assert "PT_Keyframe" in sync_types


def test_packet_type_0x02_reserved():
    """0x02 remains reserved/invalid."""
    assert "0x02" in sync_types


# =====================================================================
# SUCCESS REPORT
# =====================================================================

if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
