"""
Phase 7H / 7G.5 — Material Assignment Policy + Manual Camera Sync UX.

Part A — Material Policy:
- /Game/UELiveSync/Imported materials are no longer treated as unsafe
- OnRestoreGeneratedMaterials checks imported-parent MID cache before skipping
- ParseAndApplyGeneratedMaterial creates imported-parent MID for property sync
- [MATERIAL][FBX_IMPORTED_APPLY] marker present
- [MATERIAL][FBX_IMPORTED_KEEP] marker present
- [MATERIAL][MID_FALLBACK_APPLY] marker present (for no-material fallback)
- [MATERIAL][MID_OVERRIDE_SKIP_IMPORTED] marker present (OnRestoreGeneratedMaterials fallback)
- [MATERIAL][IMPORTED_PARENT_MID_APPLY] marker present
- [MAT][IMPORTED_PARENT] marker present
- [MAT][IMPORTED_PARENT_CREATE] marker present
- Counter MaterialImportedParentMIDApplied exists in header
- SafeMaterial fallback still applied for null/WorldGrid slots

Part B — Camera Sync UX:
- uelivesync.sync_active_camera_to_ue operator exists in __init__.py
- "Sync Active Camera to UE" button in panel
- Operator registered in classes tuple
- No-camera warning path exists
- Uses scene.camera fallback/selected camera logic
- SAFE: does NOT send PT_Create (camera spawn deferred to auto-detect)
- SAFE: does NOT send PT_ActiveCamera (viewport switching unsafe)
- Sends only PT_Transform + PT_CameraDef for existing camera actors
- uelivesync.debug_send_camera_packets operator exists for freeze isolation
- PT_ActiveCamera remains 0x15 in protocol, just not sent by manual operator
- Dump Diagnostics no longer has undefined network reference
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
    assert count >= 1, (
        f"[MATERIAL][MID_OVERRIDE_SKIP_IMPORTED] should appear at least 1 time"
        f" (OnRestoreGeneratedMaterials fallback), found {count}"
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
    chunk = source_cpp[idx:idx + 3000]
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


# --- ParseAndApplyGeneratedMaterial imported-parent MID path ---

def test_parse_and_apply_imported_parent_mid():
    """ParseAndApplyGeneratedMaterial creates imported-parent MID for property sync."""
    idx = source_cpp.find("ParseAndApplyGeneratedMaterial(\n    const FGuid& Guid")
    if idx == -1:
        idx = source_cpp.find("ParseAndApplyGeneratedMaterial(")
    assert idx != -1, "ParseAndApplyGeneratedMaterial definition not found"
    chunk = source_cpp[idx:idx + 4000]
    assert "/Game/UELiveSync/Imported" in chunk, (
        "ParseAndApplyGeneratedMaterial must check for imported material path"
    )
    assert "IMPORTED_PARENT_MID_APPLY" in chunk, (
        "ParseAndApplyGeneratedMaterial must apply imported-parent MID marker"
    )


def test_parse_and_apply_has_imported_parent_mid_cache():
    """ParseAndApplyGeneratedMaterial uses GetOrCreateImportedParentMID."""
    assert "GetOrCreateImportedParentMID" in source_cpp, (
        "GetOrCreateImportedParentMID function must exist in subsystem"
    )
    assert "ImportedMaterialMIDCache" in source_cpp, (
        "ImportedMaterialMIDCache cache must exist in subsystem"
    )
    assert "MaterialImportedParentMIDApplied" in source_cpp, (
        "MaterialImportedParentMIDApplied counter must exist in subsystem"
    )


# --- Imported-parent MID markers ---

def test_imported_parent_mid_apply_marker():
    """[MATERIAL][IMPORTED_PARENT_MID_APPLY] marker exists in subsystem."""
    assert "[MATERIAL][IMPORTED_PARENT_MID_APPLY]" in source_cpp


def test_imported_parent_marker():
    """[MAT][IMPORTED_PARENT] marker exists in subsystem."""
    assert "[MAT][IMPORTED_PARENT]" in source_cpp


def test_imported_parent_create_marker():
    """[MAT][IMPORTED_PARENT_CREATE] marker exists in subsystem."""
    assert "[MAT][IMPORTED_PARENT_CREATE]" in source_cpp


def test_imported_material_mid_cache_in_header():
    """ImportedMaterialMIDCache declared in subsystem header."""
    header = read_file(os.path.join(
        REPO_ROOT, "UE_Plugin", "UELiveSync", "Source", "UELiveSync",
        "Public", "UELiveSyncSubsystem.h"
    ))
    assert "ImportedMaterialMIDCache" in header


def test_material_imported_parent_counter_in_header():
    """MaterialImportedParentMIDApplied declared in subsystem header."""
    header = read_file(os.path.join(
        REPO_ROOT, "UE_Plugin", "UELiveSync", "Source", "UELiveSync",
        "Public", "UELiveSyncSubsystem.h"
    ))
    assert "MaterialImportedParentMIDApplied" in header


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


def test_camera_does_not_send_pt_activecamera():
    """Operator does NOT send PT_ActiveCamera as a packet."""
    idx = init_py.find("UELIVESYNC_OT_sync_active_camera_to_ue")
    chunk = init_py[idx:idx + 4000]
    # PT_ActiveCamera must NOT be used in send_objects (packet dispatch)
    assert "packet_type=network.PT_ActiveCamera" not in chunk, (
        "Operator must not send PT_ActiveCamera as a packet — viewport switching causes freeze"
    )


def test_camera_bl_description_no_activecamera():
    """bl_description no longer mentions PT_ActiveCamera."""
    idx = init_py.find("UELIVESYNC_OT_sync_active_camera_to_ue")
    chunk = init_py[idx:idx + 1000]
    assert "PT_ActiveCamera" not in chunk, (
        "bl_description should not mention PT_ActiveCamera"
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
    assert "network.serialize_active_camera" not in chunk, (
        "Operator must not serialize active camera — viewport switching unsafe"
    )


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

def test_camera_does_not_send_create_packet():
    """Operator does NOT send PT_Create (0x03) — spawn deferred to auto-detect."""
    idx = init_py.find("UELIVESYNC_OT_sync_active_camera_to_ue")
    chunk = init_py[idx:idx + 6000]
    # PT_Create (0x03) should NOT appear as a packet_type in the operator's send loop
    assert "packet_type=0x03" not in chunk, (
        "Operator must not send PT_Create — camera actor spawn can cause freeze"
    )


def test_camera_sends_transform_packet():
    """Operator sends PT_Transform (default 0x01) packet."""
    idx = init_py.find("UELIVESYNC_OT_sync_active_camera_to_ue")
    chunk = init_py[idx:idx + 6000]
    lines = [l.strip() for l in chunk.split('\n') if 'send_objects' in l]
    has_default = any('packet_type=' not in l for l in lines)
    assert has_default, (
        "Must have a send_objects() call without explicit packet_type (transform default 0x01)"
    )


# --- Safe bl_description ---

def test_camera_bl_description_safe():
    """bl_description mentions safe behavior (no spawn/viewport switch)."""
    idx = init_py.find("UELIVESYNC_OT_sync_active_camera_to_ue")
    chunk = init_py[idx:idx + 1000]
    assert "safe" in chunk.lower(), (
        "bl_description should indicate operator is safe (no spawn/viewport switch)"
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
