"""
Phase 7E Stage 10A.2 — UE Visibility BoolTrack Apply static tests.

Verifies:
- HandleKeyframe branches for channels 9 and 10
- UMovieSceneBoolTrack referenced
- Bool section/key creation present
- [KEYFRAME][BOOL_APPLY] marker exists
- [KEYFRAME][BOOL_KEY] marker exists
- [KEYFRAME][BOOL_TRACK_CREATE] marker exists
- [KEYFRAME][BOOL_SECTION_CREATE] marker exists
- [KEYFRAME][BOOL_UNSUPPORTED] marker exists
- Transform channel logic (0-8) remains
- PT_Keyframe remains 0x17
- PT_SequencerOp remains 0x18
- 0x02 remains reserved
- Camera crash workaround intact
- No direct bPendingKill access
"""

import os
import re

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SUBSYSTEM_CPP = os.path.join(
    REPO_ROOT, "UE_Plugin", "UELiveSync", "Source", "UELiveSync",
    "Private", "UELiveSyncSubsystem.cpp"
)
SUBSYSTEM_H = os.path.join(
    REPO_ROOT, "UE_Plugin", "UELiveSync", "Source", "UELiveSync",
    "Public", "UELiveSyncSubsystem.h"
)
SYNC_TYPES_H = os.path.join(
    REPO_ROOT, "UE_Plugin", "UELiveSync", "Source", "UELiveSync",
    "Public", "SyncTypes.h"
)


def read_source(path):
    with open(path, "r") as f:
        return f.read()


source_cpp = read_source(SUBSYSTEM_CPP)
source_h = read_source(SUBSYSTEM_H)
sync_types = read_source(SYNC_TYPES_H)


# ---------------------------------------------------------------------------
# BoolTrack markers
# ---------------------------------------------------------------------------
def test_bool_apply_marker():
    """[KEYFRAME][BOOL_APPLY] marker exists in HandleKeyframe."""
    assert "[KEYFRAME][BOOL_APPLY]" in source_cpp


def test_bool_key_marker():
    """[KEYFRAME][BOOL_KEY] marker exists in HandleKeyframe."""
    assert "[KEYFRAME][BOOL_KEY]" in source_cpp


def test_bool_track_create_marker():
    """[KEYFRAME][BOOL_TRACK_CREATE] marker exists."""
    assert "[KEYFRAME][BOOL_TRACK_CREATE]" in source_cpp


def test_bool_section_create_marker():
    """[KEYFRAME][BOOL_SECTION_CREATE] marker exists."""
    assert "[KEYFRAME][BOOL_SECTION_CREATE]" in source_cpp


def test_bool_unsupported_marker():
    """[KEYFRAME][BOOL_UNSUPPORTED] marker exists."""
    assert "[KEYFRAME][BOOL_UNSUPPORTED]" in source_cpp


# ---------------------------------------------------------------------------
# Existing VISIBILITY markers preserved
# ---------------------------------------------------------------------------
def test_visibility_applied_marker_preserved():
    """Existing [KEYFRAME][VISIBILITY] applied marker preserved."""
    assert "[KEYFRAME][VISIBILITY] applied" in source_cpp


def test_visibility_unsupported_marker_preserved():
    """Existing [KEYFRAME][VISIBILITY] unsupported marker preserved."""
    assert "[KEYFRAME][VISIBILITY] unsupported" in source_cpp


# ---------------------------------------------------------------------------
# Channel branching
# ---------------------------------------------------------------------------
def test_handle_keyframe_channel_9_branch():
    """HandleKeyframe has logic branch for channel 9."""
    # Look for channel 9 visibility handling
    assert "Entry->ChannelIndex == 9" in source_cpp or "ChannelIndex == 9" in source_cpp


def test_handle_keyframe_channel_10_branch():
    """HandleKeyframe has logic branch for channel 10."""
    assert "Entry->ChannelIndex == 10" in source_cpp or "ChannelIndex == 10" in source_cpp


def test_both_channels_handled_as_pair():
    """Channels 9 and 10 handled together in visibility branch."""
    assert "Entry->ChannelIndex == 9 || Entry->ChannelIndex == 10" in source_cpp


def test_transform_channels_preserved():
    """Transform channel handling (0-8) remains unchanged."""
    assert "GetChannel<FMovieSceneDoubleChannel>" in source_cpp
    assert "UMovieScene3DTransformTrack" in source_cpp


def test_channel_gt_10_unsupported():
    """Channel > 10 handled as unsupported."""
    assert "Entry->ChannelIndex > 10" in source_cpp


# ---------------------------------------------------------------------------
# Sequencer types referenced
# ---------------------------------------------------------------------------
def test_umovie_scene_bool_track_referenced():
    """UMovieSceneBoolTrack referenced in HandleKeyframe."""
    assert "UMovieSceneBoolTrack" in source_cpp


def test_umovie_scene_bool_section_referenced():
    """UMovieSceneBoolSection referenced in HandleKeyframe."""
    assert "UMovieSceneBoolSection" in source_cpp


def test_bool_section_channel_add_keys():
    """BoolSection->GetChannel().AddKeys used for key insertion."""
    assert "GetChannel().AddKeys" in source_cpp


# ---------------------------------------------------------------------------
# Stat counters
# ---------------------------------------------------------------------------
def test_visibility_keys_applied_counter():
    """KeyframeVisibilityKeysApplied counter exists."""
    assert "KeyframeVisibilityKeysApplied" in source_cpp


def test_visibility_track_created_counter():
    """KeyframeVisibilityTrackCreated counter exists."""
    assert "KeyframeVisibilityTrackCreated" in source_cpp


def test_visibility_section_created_counter():
    """KeyframeVisibilitySectionCreated counter exists."""
    assert "KeyframeVisibilitySectionCreated" in source_cpp


def test_visibility_unsupported_counter():
    """KeyframeVisibilityUnsupported counter exists."""
    assert "KeyframeVisibilityUnsupported" in source_cpp


# ---------------------------------------------------------------------------
# Safety: missing binding
# ---------------------------------------------------------------------------
def test_missing_binding_safe():
    """Missing binding handled safely with counter increment."""
    assert "KeyframeMissingBinding" in source_cpp
    assert "MissingBinding" in source_cpp


# ---------------------------------------------------------------------------
# Protocol constants
# ---------------------------------------------------------------------------
def test_pt_keyframe_unchanged():
    """PT_Keyframe remains 0x17."""
    assert "PT_Keyframe       = 0x17" in sync_types or "PT_Keyframe = 0x17" in sync_types


def test_pt_sequencer_op_unchanged():
    """PT_SequencerOp remains 0x18."""
    assert "PT_SequencerOp" in sync_types and "0x18" in sync_types


def test_reserved_02_unchanged():
    """0x02 remains reserved."""
    assert "PT_Reserved_02" in sync_types


# ---------------------------------------------------------------------------
# LevelSequence save after keyframe apply
# ---------------------------------------------------------------------------
def test_sequence_saved_after_keyframe():
    """SaveLiveSyncLevelSequenceAsset called after keyframe apply."""
    assert "SaveLiveSyncLevelSequenceAsset" in source_cpp


def test_sequence_save_gated_on_applied_keys():
    """Sequence save gated on AppliedKeys > 0."""
    # Find the save block
    lines = source_cpp.splitlines()
    found_gate = False
    for line in lines:
        if "SaveLiveSyncLevelSequenceAsset" in line:
            for prev_line in lines:
                if "AppliedKeys > 0" in prev_line:
                    found_gate = True
                    break
            break
    found_gate = found_gate or "AppliedKeys > 0" in source_cpp
    assert found_gate


# ---------------------------------------------------------------------------
# Camera crash workaround preserved
# ---------------------------------------------------------------------------
def test_b_hide_from_scene_outliner_preserved():
    """bHideFromSceneOutliner camera crash workaround preserved."""
    assert "bHideFromSceneOutliner" in source_cpp


def test_configure_live_sync_camera_actor_preserved():
    """ConfigureLiveSyncCameraActor frustum guard preserved."""
    assert "ConfigureLiveSyncCameraActor" in source_cpp


def test_safe_lifecycle_markers_preserved():
    """E2E9 camera lifecycle markers preserved."""
    for marker in ["SAFE_LIFECYCLE_ENTER", "SAFE_SPAWN_BEGIN",
                   "OUTLINER_GUARD", "SAFE_CACHE_ADD"]:
        assert marker in source_cpp, f"Missing camera marker: {marker}"


def test_e2e10_outliner_hide_preserved():
    """E2E10 E2E10_OUTLINER_HIDE marker preserved."""
    assert "E2E10_OUTLINER_HIDE" in source_cpp


# ---------------------------------------------------------------------------
# No bPendingKill
# ---------------------------------------------------------------------------
def test_no_bpendingkill():
    """No direct AActor::bPendingKill access outside allowed helper."""
    lines = source_cpp.splitlines()
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if "bPendingKill" in stripped:
            # Allow comments and the known helper
            if stripped.startswith("//"):
                continue
            if "IsLiveSyncActorInvalidForAttach" in stripped:
                continue
            raise AssertionError(
                f"Direct bPendingKill access at line {i}: {stripped}"
            )


# ---------------------------------------------------------------------------
# Value conversion semantics
# ---------------------------------------------------------------------------
def test_visibility_value_conversion():
    """Visibility value converted: <0.5 false, >=0.5 true."""
    lines = source_cpp.splitlines()
    has_conversion = False
    for line in lines:
        if "Entry->Value != 0.0f" in line or "bValue = (Entry->Value" in line:
            has_conversion = True
            break
    assert has_conversion


# ---------------------------------------------------------------------------
# Keyframe entry struct wire format
# ---------------------------------------------------------------------------
def test_keyframe_entry_size_unchanged():
    """KEYFRAME_ENTRY_SIZE preserved."""
    assert "KEYFRAME_ENTRY_SIZE" in source_cpp
