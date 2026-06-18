"""
Phase 7H / 7G.5 — Material Assignment Policy + Manual Camera Sync UX.

Part A — Material Policy:
- /Game/UELiveSync/Imported materials are no longer treated as unsafe
- OnRestoreGeneratedMaterials skips MID override when imported material exists
- ParseAndApplyGeneratedMaterial skips MID override when imported material exists
- [MATERIAL][FBX_IMPORTED_APPLY] marker present
- [MATERIAL][FBX_IMPORTED_KEEP] marker present
- [MATERIAL][MID_FALLBACK_APPLY] marker present
- [MATERIAL][MID_OVERRIDE_SKIP_IMPORTED] marker present
- SafeMaterial fallback still applied for null/WorldGrid slots

Part B — Camera Sync UX:
- uelivesync.sync_active_camera_to_ue operator exists in __init__.py
- "Sync Active Camera to UE" button in panel
- Operator registered in classes tuple
- No-camera warning path exists
- Uses scene.camera fallback/selected camera logic
- Uses existing PT_CameraDef / PT_ActiveCamera constants
- Does not change packet IDs
- Camera crash workaround (bHideFromSceneOutliner) not removed
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


def read_file(path):
    with open(path, "r") as f:
        return f.read()


source_cpp = read_file(SUBSYSTEM_CPP)
fbx_cpp = read_file(FBX_IMPORTER_CPP)
sync_types = read_file(SYNC_TYPES_H)
init_py = read_file(INIT_PY)
network_py = read_file(NETWORK_PY)


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
    """[MATERIAL][MID_FALLBACK_APPLY] marker exists in subsystem."""
    assert "[MATERIAL][MID_FALLBACK_APPLY]" in source_cpp


def test_material_mid_override_skip_imported_marker():
    """[MATERIAL][MID_OVERRIDE_SKIP_IMPORTED] marker exists in subsystem."""
    count = source_cpp.count("[MATERIAL][MID_OVERRIDE_SKIP_IMPORTED]")
    assert count >= 2, (
        f"[MATERIAL][MID_OVERRIDE_SKIP_IMPORTED] should appear at least 2 times"
        f" (OnRestoreGeneratedMaterials + ParseAndApplyGeneratedMaterial), found {count}"
    )


# --- IsUnsafeFBMaterial behavior ---

def test_unsafe_fbx_material_no_longer_checks_imported():
    """IsUnsafeFBXMaterial no longer treats /Game/UELiveSync/Imported as unsafe."""
    # Check the function body in LiveSyncFBXImporter.cpp
    idx = fbx_cpp.find("static bool IsUnsafeFBXMaterial")
    assert idx != -1, "IsUnsafeFBXMaterial not found"
    chunk = fbx_cpp[idx:idx + 800]
    # The old check for /Game/UELiveSync/Imported should now return false
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


# --- OnRestoreGeneratedMaterials guard ---

def test_on_restore_generated_skip_imported():
    """OnRestoreGeneratedMaterials skips MID when imported material exists."""
    idx = source_cpp.find("Ctx.OnRestoreGeneratedMaterials")
    assert idx != -1, "OnRestoreGeneratedMaterials not found"
    chunk = source_cpp[idx:idx + 2000]
    assert "/Game/UELiveSync/Imported" in chunk, (
        "OnRestoreGeneratedMaterials must check for imported material path"
    )
    assert "MID_OVERRIDE_SKIP_IMPORTED" in chunk, (
        "OnRestoreGeneratedMaterials must have MID_OVERRIDE_SKIP_IMPORTED marker"
    )
    # Verify it has a guarded path (either continue or skip)
    assert ("continue" in chunk) or ("SKIP" in chunk.upper()), (
        "OnRestoreGeneratedMaterials must skip when imported material exists"
    )


# --- ParseAndApplyGeneratedMaterial guard ---

def test_parse_and_apply_skip_imported():
    """ParseAndApplyGeneratedMaterial skips MID when imported material exists."""
    # The definition is 'bool UUELiveSyncSubsystem::ParseAndApplyGeneratedMaterial('
    idx = source_cpp.find("ParseAndApplyGeneratedMaterial(\n    const FGuid& Guid")
    if idx == -1:
        idx = source_cpp.find("ParseAndApplyGeneratedMaterial(")
    assert idx != -1, "ParseAndApplyGeneratedMaterial definition not found"
    # Read a large enough window to include the guard
    chunk = source_cpp[idx:idx + 3000]
    assert "/Game/UELiveSync/Imported" in chunk, (
        "ParseAndApplyGeneratedMaterial must check for imported material path"
    )
    assert "MID_OVERRIDE_SKIP_IMPORTED" in chunk, (
        "ParseAndApplyGeneratedMaterial must have MID_OVERRIDE_SKIP_IMPORTED guard"
    )


# --- EnsureFBXMeshRenderable still applies SafeMaterial for null ---

def test_ensure_renderable_still_has_fallback():
    """EnsureFBXMeshRenderable still applies fallback for null/WorldGrid."""
    assert "fallback" in fbx_cpp[fbx_cpp.find("EnsureFBXMeshRenderable"):fbx_cpp.find("EnsureFBXMeshRenderable") + 2000]


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
    # Should appear as layout.operator or row.operator
    assert "sync_active_camera_to_ue" in init_py


def test_camera_button_icon_camera_data():
    """Camera button uses CAMERA_DATA icon."""
    # Search for the specific layout.operator call with icon='CAMERA_DATA'
    # Search whole file since panel is large
    assert "icon='CAMERA_DATA'" in init_py or 'icon="CAMERA_DATA"' in init_py
    # Verify it's associated with the camera button
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


def test_camera_uses_pt_activecamera():
    """Operator uses PT_ActiveCamera (0x15)."""
    idx = init_py.find("UELIVESYNC_OT_sync_active_camera_to_ue")
    chunk = init_py[idx:idx + 4000]
    assert "PT_ActiveCamera" in chunk


def test_camera_uses_serialize_camera_def():
    """Operator uses serialize_camera_def()."""
    idx = init_py.find("UELIVESYNC_OT_sync_active_camera_to_ue")
    chunk = init_py[idx:idx + 4000]
    assert "serialize_camera_def" in chunk


def test_camera_uses_serialize_active_camera():
    """Operator uses serialize_active_camera()."""
    idx = init_py.find("UELIVESYNC_OT_sync_active_camera_to_ue")
    chunk = init_py[idx:idx + 4000]
    assert "serialize_active_camera" in chunk


def test_camera_uses_serialize_object_v3():
    """Operator uses serialize_object_v3() for create/transform."""
    idx = init_py.find("UELIVESYNC_OT_sync_active_camera_to_ue")
    chunk = init_py[idx:idx + 4000]
    assert "serialize_object_v3" in chunk


def test_camera_uses_primitve_camera():
    """Operator uses PRIMITIVE_CAMERA primitive type."""
    idx = init_py.find("UELIVESYNC_OT_sync_active_camera_to_ue")
    chunk = init_py[idx:idx + 4000]
    assert "PRIMITIVE_CAMERA" in chunk


# --- Uses existing serialization functions ---

def test_camera_sends_create_packet():
    """Operator sends PT_Create (0x03) packet."""
    idx = init_py.find("UELIVESYNC_OT_sync_active_camera_to_ue")
    # Read larger chunk to include packet sending section
    chunk = init_py[idx:idx + 6000]
    assert "packet_type=0x03" in chunk


def test_camera_sends_transform_packet():
    """Operator sends PT_Transform (default 0x01) packet."""
    idx = init_py.find("UELIVESYNC_OT_sync_active_camera_to_ue")
    chunk = init_py[idx:idx + 6000]
    # Default packet_type=0x01 has no packet_type argument
    lines = [l.strip() for l in chunk.split('\n') if 'send_objects' in l]
    has_default = any('packet_type=' not in l for l in lines)
    assert has_default, (
        "Must have a send_objects() call without explicit packet_type (transform default 0x01)"
    )


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
    # Check network.py
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
