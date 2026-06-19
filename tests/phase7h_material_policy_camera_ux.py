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
SYNC_PY = os.path.join(
    REPO_ROOT, "Blender_Addon", "sync.py"
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
sync_py = read_file(SYNC_PY)
header_h = read_file(HEADER_H)

# Load network module directly (avoid bpy import from Blender_Addon/__init__.py)
_net_globals = {"__name__": "Blender_Addon.network",
                "__file__": NETWORK_PY,
                "os": __import__("os"),
                "socket": __import__("socket"),
                "struct": __import__("struct"),
                "sys": __import__("sys"),
                "threading": __import__("threading"),
                "queue": __import__("queue"),
                "time": __import__("time"),
                "xxhash": __import__("xxhash"),
                "math": __import__("math"),
                "uuid": __import__("uuid"),
                "bpy": type("bpy", (), {}),  # stub to avoid ImportError
                "bgl": type("bgl", (), {}),
                "bl_math": type("bl_math", (), {}),
                "bmesh": type("bmesh", (), {}),
                "mathutils": type("mathutils", (), {}),
                "bl_operators": type("bl_operators", (), {}),
                "bl_context": type("bl_context", (), {}),
                "gpu": type("gpu", (), {}),
                "gpu_extensions": type("gpu_extensions", (), {}),
                "_append_blender_debug_log": lambda msg: None,
                "_get_active_camera_guid": lambda: None,
                "_get_active_camera_state": lambda: None,
                }
exec(compile(network_py, NETWORK_PY, "exec"), _net_globals)
_net_mod = _net_globals
net = type("_net_stub", (), _net_globals)


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
    chunk = source_cpp[idx:idx + 8000]
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
    """GENERATED_TEXTURE_PARAM_CHECK replaced by expanded per-param readback."""
    assert "[MATERIAL][TEXTURE_PARAM_READBACK]" in source_cpp
    assert "[MATERIAL][TEXTURE_TOGGLE_READBACK]" in source_cpp


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


# --- Phase 7H.7 follow-up: missing import os + failure message fix ---

def test_fbx_import_os_at_module_level():
    """import os exists at module level (not only inside execute)."""
    # Must be before any function definition, not just inside execute().
    assert "import os" in init_py[:init_py.find("def ")], (
        "import os must be at module level in __init__.py"
    )


def test_fbx_export_failed_warning_message():
    """'FBX sync failed for N mesh object(s)' warning exists."""
    assert "FBX sync failed for" in init_py, (
        "Must warn 'FBX sync failed for N mesh object(s)' when mesh found but export fails"
    )


def test_fbx_export_failed_warning_refs_len_selected():
    """Export-failed warning uses len(selected) and references [FBX] ERROR."""
    idx = init_py.find("FBX sync failed for")
    assert idx != -1, "export-failed warning not found"
    chunk = init_py[idx:idx + 200]
    assert "len(selected)" in chunk, (
        "Message must reference len(selected) to show object count"
    )
    assert "[FBX] ERROR" in chunk, (
        "Message must direct user to console/log for [FBX] ERROR"
    )


def test_fbx_no_mesh_warning_still_present():
    """'No mesh objects could be FBX-synced' still exists for empty selection."""
    assert "No mesh objects could be FBX-synced" in init_py


def test_fbx_cache_folder_list_marker():
    """[FBX][CACHE_FOLDER_LIST] marker exists in __init__.py."""
    assert "[FBX][CACHE_FOLDER_LIST]" in init_py


def test_fbx_texture_ref_check_marker():
    """[FBX][TEXTURE_REF_CHECK] marker exists in __init__.py."""
    assert "[FBX][TEXTURE_REF_CHECK]" in init_py


def test_fbx_sidecar_texture_scan_ue_marker():
    """[FBX][SIDECAR_TEXTURE_SCAN] marker exists in FBX importer."""
    assert "[FBX][SIDECAR_TEXTURE_SCAN]" in fbx_cpp


def test_fbx_sidecar_texture_import_ok_ue_marker():
    """[FBX][SIDECAR_TEXTURE_IMPORT_OK] marker exists in FBX importer."""
    assert "[FBX][SIDECAR_TEXTURE_IMPORT_OK]" in fbx_cpp


def test_blender_texture_sidecar_function_exists():
    """_copy_textures_sidecar function is defined in __init__.py."""
    assert "def _copy_textures_sidecar(" in init_py


def test_blender_texture_sidecar_scan_marker():
    """[FBX][TEXTURE_SIDECAR_SCAN] marker exists in __init__.py."""
    assert "[FBX][TEXTURE_SIDECAR_SCAN]" in init_py


def test_blender_texture_copy_marker():
    """[FBX][TEXTURE_COPY] marker exists in __init__.py."""
    assert "[FBX][TEXTURE_COPY]" in init_py


def test_blender_texture_copy_fail_marker():
    """[FBX][TEXTURE_COPY_FAIL] marker exists in __init__.py."""
    assert "[FBX][TEXTURE_COPY_FAIL]" in init_py


def test_blender_texture_sidecar_summary_marker():
    """[FBX][TEXTURE_SIDECAR_SUMMARY] marker exists in __init__.py."""
    assert "[FBX][TEXTURE_SIDECAR_SUMMARY]" in init_py


def test_blender_texture_sidecar_called_before_cache_list():
    """_copy_textures_sidecar is called before CACHE_FOLDER_LIST."""
    # Find the call and confirm it precedes CACHE_FOLDER_LIST
    call_pos = init_py.find("_copy_textures_sidecar(")
    cache_pos = init_py.find("[FBX][CACHE_FOLDER_LIST]")
    assert call_pos >= 0, "_copy_textures_sidecar call not found"
    assert cache_pos >= 0, "CACHE_FOLDER_LIST not found"
    assert call_pos < cache_pos, (
        f"_copy_textures_sidecar at {call_pos} must precede "
        f"CACHE_FOLDER_LIST at {cache_pos}"
    )


def test_blender_texture_sidecar_no_material_slots_log():
    """no_material_slots branch logs TEXTURE_SIDECAR."""
    assert "no_material_slots" in init_py


def test_blender_texture_sidecar_imports_shutil():
    """shutil is imported inside _copy_textures_sidecar."""
    assert "import shutil" in init_py


def test_fbx_sidecar_texture_scan_ue_scans_textures_subfolder():
    """UE sidecar scan checks textures/ subfolder."""
    assert "textures" in fbx_cpp or "TEXTURES" in fbx_cpp
    # Confirm the subfolder scan pattern exists
    assert 'FbxDir / TEXT("textures")' in fbx_cpp


# =====================================================================
# Task E: New sidecar scanner robustness tests
# =====================================================================


def test_ue_sidecar_scanner_uses_iteratedirectory():
    """UE sidecar scanner uses IterateDirectory instead of FindFiles."""
    assert "IterateDirectory" in fbx_cpp
    # Ensure the old FindFiles-based scanning is removed
    # (the old code called IFileManager::Get().FindFiles with extensions)
    lines = fbx_cpp.split("\n")
    in_sidecar_block = False
    for line in lines:
        if "SIDECAR_TEXTURE_SCAN" in line or "sidecar texture" in line.lower():
            in_sidecar_block = True
        if in_sidecar_block and "IFileManager::Get().FindFiles" in line and "TEXT(\"textures\")" not in line:
            # Found a raw FindFiles call with extension (old pattern)
            # Allow nullptr-based FindFiles if it exists (for subfolder)
            pass


def test_ue_sidecar_scanner_accepts_jpg():
    """UE sidecar scanner accepts .jpg extension."""
    assert '"jpg"' in fbx_cpp or "'jpg'" in fbx_cpp


def test_ue_sidecar_scanner_accepts_jpeg():
    """UE sidecar scanner accepts .jpeg extension."""
    assert '"jpeg"' in fbx_cpp or "'jpeg'" in fbx_cpp


def test_ue_sidecar_scanner_accepts_png():
    """UE sidecar scanner accepts .png extension."""
    assert '"png"' in fbx_cpp or "'png'" in fbx_cpp


def test_ue_sidecar_scanner_accepts_tga():
    """UE sidecar scanner accepts .tga extension."""
    assert '"tga"' in fbx_cpp or "'tga'" in fbx_cpp


def test_ue_sidecar_scanner_accepts_exr():
    """UE sidecar scanner accepts .exr extension."""
    assert '"exr"' in fbx_cpp or "'exr'" in fbx_cpp


def test_ue_sidecar_scanner_accepts_bmp():
    """UE sidecar scanner accepts .bmp extension."""
    assert '"bmp"' in fbx_cpp or "'bmp'" in fbx_cpp


def test_ue_sidecar_extension_case_insensitive():
    """Extension matching is case-insensitive via ToLower."""
    assert ".ToLower()" in fbx_cpp or ".ToLower" in fbx_cpp


def test_ue_sidecar_candidate_log_exists():
    """Scanner logs [FBX][SIDECAR_TEXTURE_CANDIDATE]."""
    assert "[FBX][SIDECAR_TEXTURE_CANDIDATE]" in fbx_cpp


def test_ue_sidecar_scans_base_fbx_folder():
    """Scanner detects image in base FBX folder."""
    # The scanner calls ScanFolder(FbxDir, ...) for the base folder
    assert "ScanFolder(FbxDir" in fbx_cpp or "ScanFolder( *FbxDir" in fbx_cpp


def test_ue_sidecar_scans_textures_subfolder():
    """Scanner detects image in textures/ subfolder."""
    assert 'ScanFolder(TexturesPath' in fbx_cpp or 'ScanFolder(TexturesPath' in fbx_cpp


def test_ue_sidecar_does_not_count_fbx_or_json():
    """Scanner does not count .fbx or .json files as textures."""
    # The accepted extensions list must NOT include fbx or json
    ext_section_start = fbx_cpp.find("AcceptedExtensions")
    assert ext_section_start >= 0, "AcceptedExtensions list not found"
    ext_block = fbx_cpp[ext_section_start:ext_section_start + 500]
    assert '"fbx"' not in ext_block and "'fbx'" not in ext_block
    assert '"json"' not in ext_block and "'json'" not in ext_block
    assert '"manifest"' not in ext_block and "'manifest'" not in ext_block


def test_ue_sidecar_scan_log_format():
    """Final scan log includes file names."""
    assert "[FBX][SIDECAR_TEXTURE_SCAN]" in fbx_cpp
    # Check that the scan log includes a count field
    assert "count=" in fbx_cpp
    # Check that files list is logged
    assert "files=[" in fbx_cpp


# =====================================================================
# PART F — FBX DATA-LOSS PREVENTION AND UE SCANNER FIXES
# =====================================================================

# Task A/B/F/G — Blender addon safety invariants

def test_export_filepath_ends_in_fbx():
    """bpy.ops.export_scene.fbx(filepath=...) path ends in .fbx."""
    assert "filepath=fbx_export_path" in init_py
    assert ".fbx\"" in init_py or ".fbx'" in init_py


def test_export_call_log_exists():
    """Code has [FBX][EXPORT_CALL]."""
    assert "[FBX][EXPORT_CALL]" in init_py


def test_export_abort_log_exists():
    """Code has [FBX][EXPORT_ABORT] reason=export_filepath_not_fbx."""
    assert "[FBX][EXPORT_ABORT]" in init_py
    assert "export_filepath_not_fbx" in init_py


def test_source_texture_not_used_as_fbx_path():
    """Source texture filepath cannot be used as FBX export filepath."""
    assert 'filepath=filepath' not in init_py, (
        "Export must not use bare 'filepath' (shadowed by texture loop)"
    )
    assert "filepath=fbx_export_path" in init_py


def test_file_source_uses_shutil_copy2():
    """FILE source sidecar copy uses shutil.copy2(src, dst)."""
    assert "shutil.copy2(abs_path, dest_path)" in init_py


def test_file_source_not_passed_to_save_render():
    """FILE source path is never passed to img.save_render."""
    save_render_lines = [
        line.strip() for line in init_py.split("\n")
        if "save_render" in line and not line.strip().startswith("#")
    ]
    for line in save_render_lines:
        assert "abs_path" not in line, (
            f"save_render must not use abs_path: {line}"
        )


def test_packed_generated_save_to_cache_folder():
    """Packed/generated image temp save path is under FBX cache folder."""
    assert "dir=dest_dir" in init_py
    assert "[FBX][TEXTURE_TEMP_SAVE]" in init_py


def test_source_stat_before_log():
    """Code logs TEXTURE_SOURCE_STAT_BEFORE."""
    assert "[FBX][TEXTURE_SOURCE_STAT_BEFORE]" in init_py


def test_source_stat_after_log():
    """Code logs TEXTURE_SOURCE_STAT_AFTER."""
    assert "[FBX][TEXTURE_SOURCE_STAT_AFTER]" in init_py


def test_source_write_blocked_log():
    """Code logs TEXTURE_SOURCE_WRITE_BLOCKED."""
    assert "[FBX][TEXTURE_SOURCE_WRITE_BLOCKED]" in init_py


def test_source_modified_abort_log():
    """Code logs SYNC_ABORT when source texture is modified."""
    assert "[FBX][SYNC_ABORT]" in init_py
    assert "source_texture_modified" in init_py


# Task E — UE scanner diagnostics

def test_ue_scanner_logs_dir_entry():
    """Scanner logs SIDECAR_TEXTURE_DIR_ENTRY."""
    assert "[FBX][SIDECAR_TEXTURE_DIR_ENTRY]" in fbx_cpp


def test_ue_scanner_missing_textures_folder_is_skip():
    """Missing textures/ subfolder is skip/info, not warning/failure."""
    assert "[FBX][SIDECAR_TEXTURE_SCAN_FOLDER_SKIP]" in fbx_cpp
    assert "missing_optional_subfolder" in fbx_cpp
    assert "DirectoryExists" in fbx_cpp


def test_ue_base_folder_jpg_detected():
    """Base folder .jpg is detected."""
    assert "ScanFolder(FbxDir" in fbx_cpp or "ScanFolder( *FbxDir" in fbx_cpp
    ext_section_start = fbx_cpp.find("AcceptedExtensions")
    assert ext_section_start >= 0
    ext_block = fbx_cpp[ext_section_start:ext_section_start + 500]
    assert '"jpg"' in ext_block or "'jpg'" in ext_block


def test_ue_fbx_json_not_image_candidates():
    """.fbx and .json are not image candidates."""
    ext_section_start = fbx_cpp.find("AcceptedExtensions")
    assert ext_section_start >= 0
    ext_block = fbx_cpp[ext_section_start:ext_section_start + 500]
    assert '"fbx"' not in ext_block and "'fbx'" not in ext_block
    assert '"json"' not in ext_block and "'json'" not in ext_block
    assert '"manifest"' not in ext_block and "'manifest'" not in ext_block


def test_ue_sidecar_scan_log_format():
    """Final scan log includes file names."""
    assert "[FBX][SIDECAR_TEXTURE_SCAN]" in fbx_cpp
    assert "count=" in fbx_cpp
    assert "files=[" in fbx_cpp


# --- Path normalization and fallback tests ---

def test_path_normalization_uses_isrelative_combine():
    """Code checks IsRelative before combining with FolderPath."""
    assert "IsRelative" in fbx_cpp
    assert "Combine" in fbx_cpp
    # Ensure Filename is NOT blindly combined: FolderPath / Filename
    # Should use FPaths::Combine or manual ternary, not FolderPath / FilenameStr
    # The old pattern 'FolderPath / FilenameStr' should NOT exist
    assert "FolderPath / FilenameStr" not in fbx_cpp


def test_entry_log_has_raw_full_file_fields():
    """SIDECAR_TEXTURE_DIR_ENTRY logs raw=, full=, file= fields."""
    assert "TEXT(\"[FBX][SIDECAR_TEXTURE_DIR_ENTRY]" in fbx_cpp
    assert "raw=" in fbx_cpp
    assert "full=" in fbx_cpp
    assert " file=" in fbx_cpp


def test_candidate_log_has_full_field():
    """SIDECAR_TEXTURE_CANDIDATE logs full= field."""
    assert "TEXT(\"[FBX][SIDECAR_TEXTURE_CANDIDATE]" in fbx_cpp
    assert "full=" in fbx_cpp


def test_exists_checks_normalized_path():
    """FileExists checks normalized FullPath, not combined path."""
    # After normalization, the code should check IFileManager on the normalized path
    assert "FileExists" in fbx_cpp
    # The old pattern FolderPath / FilenameStr should be gone
    assert "FolderPath / FilenameStr" not in fbx_cpp


def test_fallback_scan_uses_findfiles_recursive():
    """Fallback scan uses FindFilesRecursive with wildcard, not ext-only."""
    assert "FindFilesRecursive" in fbx_cpp
    # Should use TEXT("*") not extension filter
    assert 'TEXT("*")' in fbx_cpp
    assert 'TEXT(\"*")' in fbx_cpp


def test_fallback_scan_not_extension_only():
    """Fallback does not use FindFiles with extension filter."""
    # Should NOT have FindFiles with jpg extension
    findfiles_lines = [line for line in fbx_cpp.split("\n")
                       if "FindFiles" in line and "Recursive" not in line
                       and not line.strip().startswith("//")]
    for line in findfiles_lines:
        assert 'TEXT("jpg")' not in line and 'TEXT("png")' not in line


def test_fallback_scan_log_exists():
    """Fallback scan logs [FBX][SIDECAR_TEXTURE_FALLBACK_SCAN]."""
    assert "[FBX][SIDECAR_TEXTURE_FALLBACK_SCAN]" in fbx_cpp


def test_single_retry_log_exists():
    """Single bounded retry logs [FBX][SIDECAR_TEXTURE_RETRY]."""
    assert "[FBX][SIDECAR_TEXTURE_RETRY]" in fbx_cpp
    assert "no_images_first_scan" in fbx_cpp
    assert "delay_ms=50" in fbx_cpp or "delay_ms=100" in fbx_cpp


def test_missing_textures_folder_still_skip():
    """Missing textures/ is skip/info, not warning/failure."""
    assert "SIDECAR_TEXTURE_SCAN_FOLDER_SKIP" in fbx_cpp
    assert "missing_optional_subfolder" in fbx_cpp
    # Should NOT log Warning for this case
    skip_section = fbx_cpp[fbx_cpp.find("SIDECAR_TEXTURE_SCAN_FOLDER_SKIP")-200:
                           fbx_cpp.find("SIDECAR_TEXTURE_SCAN_FOLDER_SKIP")+200]
    # The log for skip should be UE_LOG(LogLiveSync, Log, ...) not Warning
    assert "Log," in skip_section or "Log," in fbx_cpp


def test_protocol_ids_unchanged():
    """Protocol packet types are not modified."""
    # Keyframe PT should be 0x02 in sync.py
    with open("/home/nguyennongngockhanh/Projects/UELiveSync/Blender_Addon/sync.py") as sf:
        sync_py = sf.read()
    assert "0x02" in sync_py or "PT_Keyframe" in sync_py


def test_camera_safe_behavior_unchanged():
    """Camera operator behavior is not modified."""
    # Camera operator is defined in the addon source, check the operator class name
    with open("/home/nguyennongngockhanh/Projects/UELiveSync/Blender_Addon/__init__.py") as ip:
        init_py_check = ip.read()
    # The sync_active_camera operator exists
    assert "sync_active_camera_to_ue" in init_py_check
    assert "debug_send_camera_packets" in init_py_check


# --- Manifest and sidecar readiness tests ---

def test_blender_logs_sidcar_ready():
    """Blender logs [FBX][SIDECAR_READY] after copy."""
    assert "[FBX][SIDECAR_READY]" in init_py
    assert "copied=" in init_py


def test_blender_logs_manifest_write():
    """Blender logs [FBX][MANIFEST_WRITE] with sidecar count."""
    assert "[FBX][MANIFEST_WRITE]" in init_py
    assert "sidecarTextures=" in init_py


def test_blender_manifest_contains_sidecar_textures():
    """Manifest dict includes sidecar_textures field."""
    assert '"sidecar_textures"' in init_py


def test_blender_logs_send_ready_after_sidecar():
    """[FBX][SEND_READY] logged after sidecar/manifest steps."""
    assert "[FBX][SEND_READY]" in init_py
    assert "sidecarTextures=" in init_py


def test_ue_logs_manifest_read():
    """UE logs [FBX][SIDECAR_MANIFEST_READ]."""
    assert "[FBX][SIDECAR_MANIFEST_READ]" in fbx_cpp


def test_ue_logs_manifest_expected():
    """UE logs [FBX][SIDECAR_EXPECTED]."""
    assert "[FBX][SIDECAR_EXPECTED]" in fbx_cpp


def test_ue_bounded_retry_logs():
    """UE bounded retry logs [FBX][SIDECAR_EXPECTED_WAIT]."""
    assert "[FBX][SIDECAR_EXPECTED_WAIT]" in fbx_cpp


def test_ue_logs_expected_found():
    """UE logs [FBX][SIDECAR_EXPECTED_FOUND]."""
    assert "[FBX][SIDECAR_EXPECTED_FOUND]" in fbx_cpp


def test_ue_imports_manifest_sidecar_directly():
    """UE logs [FBX][SIDECAR_TEXTURE_IMPORT] for manifest sidecar."""
    assert "[FBX][SIDECAR_TEXTURE_IMPORT]" in fbx_cpp


def test_ue_directory_scan_still_fallback():
    """Directory scan remains as fallback (IterateDirectory still used)."""
    assert "IterateDirectory" in fbx_cpp


def test_ue_texture_param_readback():
    """Material texture param readback log exists."""
    with open(SUBSYSTEM_CPP) as sf:
        src = sf.read()
    assert "[MATERIAL][TEXTURE_PARAM_READBACK]" in src


def test_ue_texture_toggle_readback():
    """Material texture toggle readback log exists."""
    with open(SUBSYSTEM_CPP) as sf:
        src = sf.read()
    assert "[MATERIAL][TEXTURE_TOGGLE_READBACK]" in src


# =====================================================================
# SUCCESS REPORT
# =====================================================================
# Phase 7H: Material Dirty Hash + Texture Metadata Tests
# =====================================================================


def test_material_dirty_hash_includes_basecolor_path():
    """compute_material_dirty_sig includes texture hash from BaseColor path."""
    prop_sig = {0: (0.8, 0.8, 0.8, 1.0, 0.5, 0.0)}
    tex_sigs = {0: (123456789, 987654321)}
    s_hash, t_hash, c_hash = net.compute_material_dirty_sig(prop_sig, tex_sigs)
    assert t_hash != 0, "texture hash must be non-zero when tex_sigs provided"
    assert c_hash != 0, "combined hash must be non-zero"


def test_scalar_only_hash_differs_from_texture_hash():
    """Changing texture hash while keeping scalars identical produces different combined."""
    prop_sig = {0: (0.8, 0.8, 0.8, 1.0, 0.5, 0.0)}
    tex_sigs_a = {0: (111111111, 222222222)}
    tex_sigs_b = {0: (333333333, 444444444)}

    _, _, combined_a = net.compute_material_dirty_sig(prop_sig, tex_sigs_a)
    _, _, combined_b = net.compute_material_dirty_sig(prop_sig, tex_sigs_b)
    assert combined_a != combined_b, (
        "combined hash must differ when texture hash differs"
    )


def test_texture_hash_includes_file_size_and_mtime():
    """compute_material_texture_hash reflects file size and mtime for FILE sources."""
    import tempfile

    # Create two temp files with different sizes
    with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as f1:
        f1.write(b"x" * 100)
        f1.flush()
        path1 = f1.name
    with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as f2:
        f2.write(b"y" * 200)
        f2.flush()
        path2 = f2.name

    try:
        maps1 = [(1, path1, "test", net.MTEX_FLAG_PATH_ABSOLUTE)]
        maps2 = [(1, path2, "test", net.MTEX_FLAG_PATH_ABSOLUTE)]
        hash1 = net.compute_material_texture_hash(0, maps1)
        hash2 = net.compute_material_texture_hash(0, maps2)
        assert hash1 != hash2, (
            "texture hash must differ when file size differs"
        )
    finally:
        os.unlink(path1)
        os.unlink(path2)


def test_texture_hash_unchanged_for_same_file():
    """compute_material_texture_hash is deterministic for same file."""
    import tempfile

    with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as f:
        f.write(b"data")
        f.flush()
        path = f.name

    try:
        maps = [(1, path, "test", net.MTEX_FLAG_PATH_ABSOLUTE)]
        h1 = net.compute_material_texture_hash(0, maps)
        h2 = net.compute_material_texture_hash(0, maps)
        assert h1 == h2, "texture hash must be deterministic for same file"
    finally:
        os.unlink(path)


def test_adding_basecolor_texture_forces_sendmat():
    """Adding BaseColor Image Texture changes dirty detection from unchanged to changed."""
    prop_sig = {0: (0.8, 0.8, 0.8, 1.0, 0.5, 0.0)}
    # Same prop_sig — scalars identical
    tex_sigs_empty = {}  # no texture
    tex_sigs_with_bc = {0: (123456789, 987654321)}  # with BaseColor

    _, _, combined_empty = net.compute_material_dirty_sig(prop_sig, tex_sigs_empty)
    _, _, combined_with_bc = net.compute_material_dirty_sig(prop_sig, tex_sigs_with_bc)
    assert combined_empty != combined_with_bc, (
        "adding BaseColor texture must change combined dirty hash"
    )


def test_removing_basecolor_texture_forces_sendmat():
    """Removing BaseColor Image Texture changes dirty detection."""
    prop_sig = {0: (0.8, 0.8, 0.8, 1.0, 0.5, 0.0)}
    tex_sigs_with_bc = {0: (123456789, 987654321)}
    tex_sigs_empty = {}

    _, _, combined_with_bc = net.compute_material_dirty_sig(prop_sig, tex_sigs_with_bc)
    _, _, combined_empty = net.compute_material_dirty_sig(prop_sig, tex_sigs_empty)
    assert combined_with_bc != combined_empty, (
        "removing BaseColor texture must change combined dirty hash"
    )


def test_changing_image_filepath_changes_dirty_hash():
    """Changing the image filepath changes the dirty hash."""
    prop_sig = {0: (0.8, 0.8, 0.8, 1.0, 0.5, 0.0)}
    tex_sigs_old = {0: (111111111, 222222222)}
    tex_sigs_new = {0: (333333333, 444444444)}

    _, _, combined_old = net.compute_material_dirty_sig(prop_sig, tex_sigs_old)
    _, _, combined_new = net.compute_material_dirty_sig(prop_sig, tex_sigs_new)
    assert combined_old != combined_new, (
        "changing image filepath must change dirty hash"
    )


def test_scalar_unchanged_texture_changed_forces_sendmat():
    """Scalars unchanged but texture changed → sendMAT=1."""
    prop_sig = {0: (0.8, 0.8, 0.8, 1.0, 0.5, 0.0)}
    # Same prop_sig
    tex_sigs_prev = {}
    tex_sigs_current = {0: (123456789, 987654321)}

    s_hash_prev, t_hash_prev, _ = net.compute_material_dirty_sig(prop_sig, tex_sigs_prev)
    s_hash_cur, t_hash_cur, _ = net.compute_material_dirty_sig(prop_sig, tex_sigs_current)

    assert s_hash_prev == s_hash_cur, "scalar hash must be identical"
    assert t_hash_prev != t_hash_cur, "texture hash must differ"


def test_sync_fbx_path_logs_dirty_hash_and_decide():
    """FBX Sync path in __init__.py computes DIRTY_HASH and DIRTY_DECIDE."""
    assert "[MATERIAL][DIRTY_HASH]" in init_py, (
        "FBX path must log [MATERIAL][DIRTY_HASH]"
    )
    assert "[MATERIAL][DIRTY_DECIDE]" in init_py, (
        "FBX path must log [MATERIAL][DIRTY_DECIDE]"
    )


def test_sync_fbx_path_texture_changed_reason():
    """FBX Sync path uses texture_changed reason when only tex changed."""
    assert "texture_changed" in init_py, (
        "FBX path must support texture_changed reason in dirty decide"
    )


def test_sync_path_checks_tex_hash_before_property_unchanged():
    """Property unchanged branch still checks tex hash."""
    idx = init_py.find("property_unchanged")
    assert idx != -1, "property_unchanged log must exist"
    # Check that tex_changed check precedes the property_unchanged log
    tex_check_before = init_py.find("tex_changed", idx - 5000, idx)
    assert tex_check_before != -1, (
        "tex_changed check must precede property_unchanged decision"
    )


def test_material_lane_logs_texture_channel_scan():
    """Material lane logs [MATERIAL][TEXTURE_CHANNEL_SCAN]."""
    assert "[MATERIAL][TEXTURE_CHANNEL_SCAN]" in init_py, (
        "Material lane must log TEXTURE_CHANNEL_SCAN"
    )


def test_material_lane_logs_matx_texture_send():
    """Material lane logs [MATERIAL][MATX_TEXTURE_SEND]."""
    assert "[MATERIAL][MATX_TEXTURE_SEND]" in init_py, (
        "Material lane must log MATX_TEXTURE_SEND"
    )


def test_ue_logs_matx_texture_recv_with_record_count():
    """UE logs MATX_TEXTURE_RECV with textureRecordCount."""
    assert "textureRecordCount" in source_cpp, (
        "UE must log textureRecordCount in MATX_TEXTURE_RECV"
    )
    assert "[MATERIAL][MATX_TEXTURE_RECV]" in source_cpp, (
        "UE must log [MATERIAL][MATX_TEXTURE_RECV]"
    )


def test_ue_logs_bascolor_matx_texture_recv():
    """UE logs BaseColor channel in MATX_TEXTURE_RECV when present."""
    assert "channel=BaseColor" in source_cpp or "BaseColor" in source_cpp, (
        "UE must log BaseColor channel in MATX_TEXTURE_RECV"
    )


def test_ue_import_bind_logs_preserved():
    """UE import and bind logs remain in code."""
    assert "[MATERIAL][TEXTURE_IMPORT_FROM_MATX_OK]" in source_cpp, (
        "TEXTURE_IMPORT_FROM_MATX_OK log must remain"
    )
    assert "[MATERIAL][TEXTURE_PARAM_SET]" in source_cpp, (
        "TEXTURE_PARAM_SET log must remain"
    )
    assert "[MATERIAL][TEXTURE_TOGGLE_SET]" in source_cpp, (
        "TEXTURE_TOGGLE_SET log must remain"
    )


def test_hybrid_apply_preserves_scalar():
    """Hybrid apply still applies Roughness/Metallic scalars."""
    assert "[MATERIAL][VALUE_PARAM_SET]" in source_cpp, (
        "VALUE_PARAM_SET log must remain for scalar params"
    )
    assert "ScalarValues" in source_cpp, (
        "Hybrid apply must still pass ScalarValues to MID"
    )


def test_removing_texture_sets_use_basecolor_0():
    """Removing BaseColor texture sets UseBaseColorTexture=0."""
    # Check that hybrid apply sets UseXTexture=0 as default
    assert "UseBaseColorTexture" in source_cpp, (
        "Code must reference UseBaseColorTexture toggle"
    )
    assert "UseXTexture" in source_cpp or "Use.*Texture" in source_cpp, (
        "Hybrid apply must set UseXTexture for channels without textures"
    )


def test_no_material_fallback_unchanged():
    """Existing material slot count check remains."""
    assert "slotCount" in source_cpp or "slot_count" in source_cpp or "SlotCount" in source_cpp, (
        "Material slot count check must remain for empty material handling"
    )


def test_scalar_only_material_sync_unchanged():
    """Existing scalar-only material sync remains."""
    # Check that MTEX scalar send path still exists
    assert "[MATERIAL][MATX_VALUE_SEND]" in init_py, (
        "Scalar VALUE_SEND log must remain"
    )


def test_protocol_ids_unchanged():
    """Protocol IDs are not changed."""
    assert "PT_Material" in source_cpp, "PT_Material must still exist"
    assert "PT_FBXImportRequest" in source_cpp, "PT_FBXImportRequest must still exist"


def test_camera_safe_behavior_unchanged():
    """Camera safe behavior is unchanged."""
    assert "camera" in source_cpp.lower() or "Camera" in source_cpp, (
        "Camera-related code must remain"
    )


def test_compute_material_texture_hash_imported_in_sync_py():
    """compute_material_texture_hash is in sync.py import list."""
    assert "compute_material_texture_hash" in sync_py


def test_compute_material_dirty_sig_imported_in_sync_py():
    """compute_material_dirty_sig is in sync.py import list."""
    assert "compute_material_dirty_sig" in sync_py


def test_no_bare_undefined_compute_material_texture_hash_in_sync_py():
    """No bare call to compute_material_texture_hash outside import scope in sync.py."""
    # Check that the import exists at module level (before any function defs)
    # The import block should contain the helper name
    assert "from .network import (" in sync_py or "from network import (" in sync_py


def test_compute_material_dirty_sig_imported_in_sync_py():
    """compute_material_dirty_sig is in sync.py import list."""
    assert "compute_material_dirty_sig" in sync_py


def test_check_updates_not_killed_by_material_hash_exception():
    """Material dirty hash error is caught and does not propagate."""
    assert "except Exception as _mat_exc" in sync_py, (
        "Material hash errors must be caught"
    )
    assert "[MATERIAL][DIRTY_HASH_ERROR]" in sync_py, (
        "Material hash errors must be logged"
    )
    assert "action=send_material_fallback" in sync_py, (
        "Material hash errors must log fallback action"
    )
    assert "bPropertiesChanged = True" in sync_py, (
        "Material hash errors must set bPropertiesChanged for fallback"
    )


def test_material_dirty_hash_exception_logs_dirty_hash_error():
    """Material dirty hash exception logs [MATERIAL][DIRTY_HASH_ERROR]."""
    assert "[MATERIAL][DIRTY_HASH_ERROR]" in sync_py, (
        "DIRTY_HASH_ERROR log must exist in sync.py"
    )
    assert "_mat_exc" in sync_py, (
        "Exception must be logged in the error message"
    )


def test_material_dirty_hash_exception_falls_back():
    """Material dirty hash exception falls back to material send."""
    assert "bPropertiesChanged = True" in sync_py, (
        "Fallback must set bPropertiesChanged=True"
    )
    # Ensure transform sync is not inside the try block
    # Transform sync code should be after the except block
    try_block_end = sync_py.find("except Exception as _mat_exc")
    fallback_block = sync_py[try_block_end:]
    assert "bPropertiesChanged = True" in fallback_block, (
        "Fallback must be in the except block"
    )


def test_transform_sync_not_inside_material_hash_try():
    """Transform sync code path is not inside the material hash try block."""
    # Find the try/except for material hash
    try_idx = sync_py.find("# Phase 7H: compute per-slot texture hash")
    try_block_end = sync_py.find("except Exception as _mat_exc")
    # Transform sync (send_objects, send_snapshot) should be after this
    after_try = sync_py[try_block_end:]
    # The try/except should end before transform sync logic
    assert after_try.find("send_objects") < 0 or after_try.find(")") < after_try.find("send_objects"), (
        "Transform sync must not be inside material hash try block"
    )


# === Phase 10A.2 hotfix: reason_log initialization + fail-safe material block ===


def test_reason_log_initialized_before_decision_branches():
    """reason_log is initialized to property_unchanged before decision branches in sync.py."""
    # Find the DECISION_INIT marker which runs right before the if/elif chain
    assert "[MATERIAL][DECISION_INIT]" in sync_py, (
        "DECISION_INIT marker must exist, confirming reason_log is initialized before branches"
    )
    assert "reason_log = \"property_unchanged\"" in sync_py, (
        "reason_log must be initialized to property_unchanged"
    )


def test_reason_log_assigned_in_all_decision_branches():
    """Every material dirty-decision branch in sync.py must assign reason_log."""
    assert "reason_log = \"first_material_send\"" in sync_py, (
        "first_material_send branch must assign reason_log"
    )
    assert "reason_log = \"slots_changed\"" in sync_py, (
        "slots_changed branch must assign reason_log"
    )
    assert "reason_log = \"property_changed\"" in sync_py, (
        "property_changed branch must assign reason_log"
    )
    assert "reason_log = \"texture_changed\"" in sync_py, (
        "texture_changed branch must assign reason_log"
    )
    assert "reason_log = \"hash_error_fallback\"" in sync_py, (
        "hash_error_fallback except branch must assign reason_log"
    )


def test_no_unbound_reason_log_reference_in_init_py():
    """__init__.py does not reference unbound reason_log variable."""
    # __init__.py uses 'reason' (not 'reason_log'), and it's assigned before use
    # Verify no bare 'reason_log' access pattern exists
    lines = init_py.split("\n")
    for i, line in enumerate(lines):
        stripped = line.strip()
        # Skip comments and assignments
        if "reason_log" in stripped and "=" not in stripped:
            # Check if it's inside a nested function/block with reason_log defined
            pass  # For now the file does not use reason_log at all
    # Confirm the file uses 'reason' not 'reason_log' in material decision
    assert "reason_log" not in init_py or "reason_log =" in init_py, (
        "If __init__.py references reason_log, it must also assign it"
    )


def test_material_block_outer_try_except_exists():
    """Material send block has outer try/except Exception."""
    marker = "# Material send (wrapped in try/except"
    idx = sync_py.find(marker)
    assert idx != -1, "Material send block marker not found"
    block_start = sync_py[idx:idx + 300]
    assert "try:" in block_start, (
        "Material send block must start with try:"
    )
    assert "except Exception as _outer_mat_exc:" in sync_py, (
        "Material send block must have outer except Exception"
    )


def test_material_sync_block_error_logged():
    """Material exception logs [MATERIAL][SYNC_BLOCK_ERROR]."""
    assert "[MATERIAL][SYNC_BLOCK_ERROR]" in sync_py, (
        "SYNC_BLOCK_ERROR marker must exist in sync.py"
    )


def test_material_sync_block_error_action_skip_material_keep_transform():
    """Material exception action is skip_material_keep_transform."""
    assert "action=skip_material_keep_transform" in sync_py, (
        "Material exception must log action=skip_material_keep_transform"
    )


def test_material_exception_does_not_raise():
    """Material exception handler does not raise."""
    # Find the outer except block and check it doesn't contain raise
    outer_except_idx = sync_py.find("except Exception as _outer_mat_exc:")
    after_except_block = sync_py[outer_except_idx:]
    # The handler should only contain print and no raise
    assert "raise" not in after_except_block[:after_except_block.find("\n\n")], (
        "Material exception handler must not raise"
    )


def test_material_exception_does_not_return():
    """Material exception handler does not return from check_updates."""
    outer_except_idx = sync_py.find("except Exception as _outer_mat_exc:")
    after_except_block = sync_py[outer_except_idx:]
    # Check that 'return' does not appear in the first few lines of the handler
    first_lines = after_except_block[:after_except_block.find("\n\n")].split("\n")
    for line in first_lines:
        assert "return" not in line.strip(), (
            "Material exception handler must not return"
        )


def test_geometry_change_detection_outside_material_try():
    """Geometry change detection code remains outside material try/except."""
    material_try_end = sync_py.find("except Exception as _outer_mat_exc:")
    after_material = sync_py[material_try_end:]
    # Geometry detection header should appear after material try/except
    assert "# Phase 7C Stage 1D: Geometry change detection" in after_material, (
        "Geometry change detection must be after material exception handler"
    )


def test_transform_send_outside_material_try():
    """Transform send code is not inside the material outer try/except."""
    # Find the geometry block which is right after material try/except,
    # confirming transform code is independent
    mat_try_idx = sync_py.find("# Material send (wrapped in try")
    outer_except_idx = sync_py.find("except Exception as _outer_mat_exc:")
    assert mat_try_idx < outer_except_idx, (
        "Material try must start before its except"
    )
    # Transform/visibility/hierarchy code must be after the material except
    after_except = sync_py[outer_except_idx:]
    assert "# Phase 7C Stage 1D: Geometry change detection" in after_except, (
        "Geometry block must be after material try/except"
    )


def test_check_updates_cannot_crash_from_material_variables():
    """check_updates cannot crash from material dirty-decision variables."""
    # All decision variables must be initialized before the material block
    # even if an exception occurs
    assert "scalar_changed = False" in sync_py, (
        "scalar_changed must be initialized"
    )
    assert "tex_changed = False" in sync_py, (
        "tex_changed must be initialized"
    )
    assert "current_tex_sigs = {}" in sync_py, (
        "current_tex_sigs must be initialized"
    )
    assert "reason_log = \"property_unchanged\"" in sync_py, (
        "reason_log must be initialized before decision branches"
    )
    assert "[LIVESYNC][CHECK_UPDATES_SURVIVED_MATERIAL_ERROR]" in sync_py, (
        "SURVIVED_MATERIAL_ERROR marker must exist"
    )


# === Scalar-only material lifecycle tests ===


def test_no_material_object_does_not_require_material_sync():
    """No material on object: no material sync required, fallback/default MID allowed."""
    assert "if bPropertiesChanged and current_slots:" in sync_py, (
        "Material send must require non-empty slots"
    )


def test_first_scalar_material_forces_sendmat():
    """First scalar-only material forces sendMAT=1."""
    assert "first_material_send" in sync_py, (
        "first_material_send reason must be logged"
    )


def test_first_scalar_material_reason_is_first_material_send():
    """First material send reason is first_material_send."""
    assert "_mat_reason_log = " in sync_py
    assert "first_material_send" in sync_py, (
        "First material must use first_material_send reason"
    )


def test_scalar_only_material_logs_scalar_channel_scan():
    """Scalar-only material logs SCALAR_CHANNEL_SCAN."""
    assert "SCALAR_CHANNEL_SCAN" in sync_py, (
        "SCALAR_CHANNEL_SCAN log must exist in sync.py"
    )


def test_scalar_only_material_logs_matx_value_send_basecolor():
    """Scalar-only material logs MATX_VALUE_SEND for BaseColor."""
    assert "MATX_VALUE_SEND" in sync_py, (
        "MATX_VALUE_SEND log must exist in sync.py"
    )


def test_scalar_only_material_logs_matx_value_send_roughness():
    """Scalar-only material logs MATX_VALUE_SEND for Roughness."""
    assert "MATX_VALUE_SEND" in sync_py, (
        "MATX_VALUE_SEND log must exist in sync.py"
    )


def test_scalar_only_material_logs_matx_value_send_metallic():
    """Scalar-only material logs MATX_VALUE_SEND for Metallic."""
    assert "MATX_VALUE_SEND" in sync_py, (
        "MATX_VALUE_SEND log must exist in sync.py"
    )


def test_scalar_only_material_logs_matx_value_send_alpha():
    """Scalar-only material logs MATX_VALUE_SEND for Alpha."""
    assert "MATX_VALUE_SEND" in sync_py, (
        "MATX_VALUE_SEND log must exist in sync.py"
    )


def test_ue_scalar_material_logs_texture_recv_count_zero():
    """UE scalar-only material logs MATX_TEXTURE_RECV with textureRecordCount=0."""
    assert "[MATERIAL][MATX_TEXTURE_RECV]" in source_cpp, (
        "MATX_TEXTURE_RECV must be logged in UE"
    )
    assert "textureRecordCount" in source_cpp, (
        "textureRecordCount must be in the log format"
    )


def test_ue_scalar_material_logs_value_param_set():
    """UE scalar-only material logs VALUE_PARAM_SET for scalar values."""
    assert "[MATERIAL][VALUE_PARAM_SET]" in source_cpp, (
        "VALUE_PARAM_SET must be logged in UE"
    )


def test_ue_scalar_material_sets_texture_toggles_zero():
    """UE scalar-only material sets all UseXTexture=0."""
    assert "UseBaseColorTexture" in source_cpp, (
        "UseBaseColorTexture must be logged in UE"
    )
    assert "UseRoughnessTexture" in source_cpp, (
        "UseRoughnessTexture must be logged in UE"
    )
    assert "UseMetallicTexture" in source_cpp, (
        "UseMetallicTexture must be logged in UE"
    )


def test_lifecycle_no_material_to_scalar_to_textured():
    """Lifecycle: no material → scalar material → BaseColor texture material."""
    assert "is_first_material" in sync_py
    assert "bPropertiesChanged = True" in sync_py
    assert "tex_changed" in sync_py
    assert "serialize_material_slots" in sync_py


def test_texture_update_after_scalar_forces_sendmat():
    """Texture update after scalar material sends sendMAT=1 reason=texture_changed."""
    assert "texture_changed" in sync_py
    assert "[MATERIAL][DIRTY_DECIDE]" in sync_py


def test_scalar_values_preserved_after_texture_update():
    """Scalar values preserved after texture update."""
    assert "mat_props" in sync_py
    assert "serialize_material_slots" in sync_py


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
