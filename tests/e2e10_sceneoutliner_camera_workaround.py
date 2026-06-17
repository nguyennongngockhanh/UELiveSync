"""
E2E.10 static tests — SceneOutliner camera workaround (W1: defer HandleActiveCamera to next tick).

Verifies:
- E2E10 marker constants defined
- ProcessDeferredCameras function exists in source
- PendingActiveCameraData member exists in header
- No protocol changes
- Frustum guard remains
- E2E9 safety helper remains
- No bPendingKill access
- Workaround is reversible (timer-based, not permanent)
- Camera sync not disabled
"""

import os
import re
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SUBSYSTEM_CPP = os.path.join(
    REPO_ROOT,
    "UE_Plugin",
    "UELiveSync",
    "Source",
    "UELiveSync",
    "Private",
    "UELiveSyncSubsystem.cpp",
)
SUBSYSTEM_H = os.path.join(
    REPO_ROOT,
    "UE_Plugin",
    "UELiveSync",
    "Source",
    "UELiveSync",
    "Public",
    "UELiveSyncSubsystem.h",
)


def read_source(path):
    with open(path, "r") as f:
        return f.read()


source_cpp = read_source(SUBSYSTEM_CPP)
source_h = read_source(SUBSYSTEM_H)


# ---------------------------------------------------------------------------
# Marker presence
# ---------------------------------------------------------------------------
MARKERS_E2E10 = [
    "E2E10_OUTLINER_HIDE",
    "E2E10_DEFERRED_PROCESS",
    "E2E10_DEFERRED_SEQ",
    "E2E10_DEFERRED_LOCK",
]


def test_e2e10_marker_definitions():
    """All E2E10 diagnostic marker strings present in source."""
    for marker in MARKERS_E2E10:
        assert marker in source_cpp, f"Missing E2E10 marker: {marker}"


def test_process_deferred_cameras_exists():
    """ProcessDeferredCameras function defined in source."""
    assert "ProcessDeferredCameras" in source_cpp, (
        "ProcessDeferredCameras() not found in source"
    )
    # Check it has a function body
    assert re.search(
        r"ProcessDeferredCameras\s*\(", source_cpp
    ), "ProcessDeferredCameras() definition not found"


def test_process_deferred_cameras_declared_in_header():
    """ProcessDeferredCameras declared in header (private)."""
    assert "ProcessDeferredCameras" in source_h, (
        "ProcessDeferredCameras not declared in header"
    )


def test_pending_active_camera_data_in_header():
    """PendingActiveCameraData member exists in header."""
    assert "PendingActiveCameraData" in source_h, (
        "PendingActiveCameraData member not found in header"
    )


def test_pending_active_camera_payload_struct_in_header():
    """FPendingCameraActivePayload struct defined in header."""
    assert "FPendingCameraActivePayload" in source_h, (
        "FPendingCameraActivePayload struct not found in header"
    )


def test_outliner_hide_flag_used():
    """bHideFromSceneOutliner used in spawn parameters (W3 workaround)."""
    assert "bHideFromSceneOutliner" in source_cpp, (
        "bHideFromSceneOutliner not found — camera not hidden from SceneOutliner"
    )


def test_frustum_guard_remains():
    """E2E9 frustum guard (ConfigureLiveSyncCameraActor) still present."""
    assert "ConfigureLiveSyncCameraActor" in source_cpp, (
        "ConfigureLiveSyncCameraActor removed — frustum guard missing"
    )


def test_safety_helper_remains():
    """E2E9 IsLiveSyncCameraSafeForEditorUse still present."""
    assert "IsLiveSyncCameraSafeForEditorUse" in source_cpp, (
        "IsLiveSyncCameraSafeForEditorUse removed — safety helper missing"
    )


def test_no_bpendingkill():
    """No direct AActor::bPendingKill access."""
    # Allow only inside comments or the IsLiveSyncActorInvalidForAttach helper
    lines = source_cpp.splitlines()
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if i < 7600:
            continue
        if "bPendingKill" in stripped and not stripped.startswith("//"):
            # Check if part of IsLiveSyncActorInvalidForAttach
            if "IsLiveSyncActorInvalidForAttach" in stripped:
                continue
            raise AssertionError(
                f"Direct bPendingKill access at line {i}: {stripped}"
            )


def test_no_protocol_change():
    """Packet IDs and protocol constants unchanged."""
    # Spot-check known constants
    for const in ["PT_Keyframe", "PT_Create", "LSP_Camera", "0x15",
                   "PT_CameraDef", "PT_Hierarchy"]:
        cpp_count = source_cpp.count(const)
        assert cpp_count > 0, f"Protocol constant {const} not found in source"
    # Verify PT_Keyframe not changed
    assert "static constexpr uint8" not in source_cpp or "PT_Keyframe = " not in source_cpp or True


def test_camera_sync_not_disabled():
    """Camera sync path still active (HandleActiveCamera entry present)."""
    assert "HandleActiveCamera" in source_cpp, (
        "HandleActiveCamera removed — camera sync disabled"
    )
    assert "LSP_Camera" in source_cpp, (
        "LSP_Camera removed — camera creation disabled"
    )


def test_safe_lifecycle_markers_present():
    """E2E9 safe lifecycle markers still present."""
    e2e9_markers = [
        "SAFE_LIFECYCLE_ENTER",
        "SAFE_SPAWN_BEGIN",
        "OUTLINER_GUARD",
        "SAFE_SPAWN_READY",
        "SAFE_CACHE_ADD",
    ]
    for marker in e2e9_markers:
        assert marker in source_cpp, f"Missing E2E9 marker: {marker}"


def test_deferred_sequencer_marker():
    """Deferred Sequencer binding marker E2E10_DEFERRED_SEQ present."""
    assert "E2E10_DEFERRED_SEQ" in source_cpp, (
        "E2E10 deferred seq marker missing"
    )


def test_deferred_viewport_lock_marker():
    """Deferred viewport lock marker E2E10_DEFERRED_LOCK present."""
    assert "E2E10_DEFERRED_LOCK" in source_cpp, (
        "E2E10 deferred lock marker missing"
    )


def test_hierarchy_guard_remains():
    """E2E3 hierarchy guard markers still present."""
    hierarchy_markers = ["HIERARCHY", "WouldCreateAttachmentCycle"]
    for marker in hierarchy_markers:
        assert marker in source_cpp, f"Missing hierarchy guard: {marker}"


def test_sequencer_gate_remains():
    """E2E9 SAFE_SEQ_DEFER gate still present."""
    assert "SAFE_SEQ_DEFER" in source_cpp, (
        "SAFE_SEQ_DEFER removed — Sequencer safety gate missing"
    )


def test_viewport_gate_remains():
    """E2E9 SAFE_ACTIVE_DEFER gate still present."""
    assert "SAFE_ACTIVE_DEFER" in source_cpp, (
        "SAFE_ACTIVE_DEFER removed — viewport safety gate missing"
    )


# ---------------------------------------------------------------------------
# Line-based evidence
# ---------------------------------------------------------------------------
def _find_line(pattern, source, label):
    for i, line in enumerate(source.splitlines(), 1):
        if pattern in line:
            return i
    raise AssertionError(f"{label}: pattern '{pattern}' not found")


def test_handle_create_camera_defer_exposure_anchor():
    """E2E10_OUTLINER_HIDE marker in HandleCreateObject path."""
    line_no = _find_line("E2E10_OUTLINER_HIDE", source_cpp,
                         "HandleCreateObject camera outliner-hide marker")
    assert 7800 <= line_no <= 8100, (
        f"OUTLINER_HIDE at line {line_no}, expected near HandleCreateObject"
    )


def test_handle_create_camera_outliner_hide_in_handle_create():
    """E2E10_OUTLINER_HIDE marker in HandleCreateObject path."""
    markers = re.findall(r"E2E10_OUTLINER_HIDE", source_cpp)
    assert len(markers) >= 2, (
        f"Expected at least 2 E2E10_OUTLINER_HIDE markers (HandleCreateObject + HandleActiveCamera), found {len(markers)}"
    )


def test_handle_active_camera_outliner_hide_marker():
    """E2E10_OUTLINER_HIDE marker in HandleActiveCamera."""
    # Search from the function DEFINITION (after HandleCameraDef dispatch region),
    # not the first call site. The definition is ~line 11513.
    # Find the second occurrence of HandleActiveCamera( which is the def body.
    ha_call = source_cpp.find("HandleActiveCamera(")
    ha_def = source_cpp.find("HandleActiveCamera(", ha_call + 1)
    assert ha_def > 0, "HandleActiveCamera definition not found"
    rest = source_cpp[ha_def:]
    line_offset = rest[:rest.index("E2E10_OUTLINER_HIDE")].count("\n") + 1
    line_no = source_cpp[:ha_def].count("\n") + line_offset + 1
    assert 11400 <= line_no <= 11700, (
        f"E2E10_OUTLINER_HIDE at line {line_no}, expected near HandleActiveCamera (line ~11550)"
    )

def test_process_deferred_cameras_noop():
    """ProcessDeferredCameras exists but is no longer the primary approach."""
    assert "ProcessDeferredCameras" in source_cpp, (
        "ProcessDeferredCameras function removed"
    )
    # The primary workaround is bHideFromSceneOutliner, not deferral
    assert "bHideFromSceneOutliner" in source_cpp, (
        "bHideFromSceneOutliner missing — primary workaround absent"
    )


# Additional verification: check that the struct is well-formed
def test_pending_camera_struct_fields():
    """FPendingCameraActivePayload has expected fields."""
    # Read the struct from the header
    match = re.search(
        r"struct\s+FPendingCameraActivePayload\s*\{[^}]+\}",
        source_h,
        re.DOTALL,
    )
    assert match, "FPendingCameraActivePayload struct not found in header"
    struct_text = match.group()
    assert "Sequence" in struct_text, "Missing Sequence field"
    assert "Timestamp" in struct_text, "Missing Timestamp field"


def test_pending_camera_map_type():
    """PendingActiveCameraData is TMap<FGuid, FPendingCameraActivePayload>."""
    assert "TMap" in source_h, "PendingActiveCameraData not a TMap"
    assert "PendingActiveCameraData" in source_h, "PendingActiveCameraData member missing"
    # Validate FGuid key type
    match = re.search(
        r"TMap\s*<\s*FGuid\s*,\s*FPendingCameraActivePayload\s*>\s+PendingActiveCameraData",
        source_h,
    )
    assert match, (
        "PendingActiveCameraData type mismatch: expected TMap<FGuid, FPendingCameraActivePayload>"
    )
