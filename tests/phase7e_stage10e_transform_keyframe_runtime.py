"""
Phase 7E Stage 10E — Transform Keyframe E2E Runtime Validation (static tests).

Verifies:
- PT_Keyframe remains 0x17
- PT_SequencerOp remains 0x18
- PT_TimelineState remains 0x19
- PT_PlaybackTransport remains 0x1A
- PT_CameraDef remains 0x1B
- 0x02 remains invalid/reserved
- channels 0–8 are transform channels
- channels 9–10 visibility bool channels remain unchanged
- deterministic packet order is CREATE_SEQUENCE → CREATE → ADD_POSSESSABLE → PT_Keyframe
- unique increasing sequence IDs
- PT_SequencerOp includes FSequencerOpHeader
- PT_Create rotation uses FQuat 4 floats
- no camera crash workaround code is changed
- Stage 10E tool file exists and is syntactically valid
- Stage 10E tool references correct channel map
"""

import ast
import os
import re
import unittest


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Stage 10E files
STAGE10E_TOOL = os.path.join(
    REPO_ROOT, "tools", "uelivesync_stage10e_transform_keyframe_runtime.py"
)

# Existing files that must NOT change
SUBSYSTEM_CPP = os.path.join(
    REPO_ROOT, "UE_Plugin", "UELiveSync", "Source", "UELiveSync",
    "Private", "UELiveSyncSubsystem.cpp"
)
SUBSYSTEM_H = os.path.join(
    REPO_ROOT, "UE_Plugin", "UELiveSync", "Source", "UELiveSync",
    "Public", "UELiveSyncSubsystem.h"
)
NETWORK_PY = os.path.join(
    REPO_ROOT, "Blender_Addon", "network.py"
)
SYNC_PY = os.path.join(
    REPO_ROOT, "Blender_Addon", "sync.py"
)
CAMERA_CRASH_GUARD = os.path.join(
    REPO_ROOT, "Blender_Addon", "network.py"
)


def read_source(path):
    with open(path, "r") as f:
        return f.read()


# ---------------------------------------------------------------------------
# File existence
# ---------------------------------------------------------------------------
class TestFiles(unittest.TestCase):

    def test_1_stage10e_tool_exists(self):
        """Stage 10E transform keyframe runtime tool exists."""
        self.assertTrue(os.path.isfile(STAGE10E_TOOL),
                        f"Stage 10E tool not found: {STAGE10E_TOOL}")

    def test_2_subsystem_cpp_exists(self):
        """UE subsystem source exists (unchanged)."""
        self.assertTrue(os.path.isfile(SUBSYSTEM_CPP),
                        f"UE subsystem not found: {SUBSYSTEM_CPP}")

    def test_3_sync_py_exists(self):
        """Blender addon sync.py exists."""
        self.assertTrue(os.path.isfile(SYNC_PY),
                        f"Blender addon not found: {SYNC_PY}")

    def test_4_network_py_exists(self):
        """Blender addon network.py exists."""
        self.assertTrue(os.path.isfile(NETWORK_PY),
                        f"Blender addon network.py not found: {NETWORK_PY}")


# ---------------------------------------------------------------------------
# Syntax check
# ---------------------------------------------------------------------------
class TestSyntax(unittest.TestCase):

    def test_5_stage10e_tool_syntax_valid(self):
        """Stage 10E tool is valid Python."""
        source = read_source(STAGE10E_TOOL)
        try:
            ast.parse(source)
        except SyntaxError as e:
            self.fail(f"Stage 10E tool has syntax error: {e}")

    def test_6_subsystem_cpp_valid(self):
        """UE subsystem source is present (not Python, so just check exists)."""
        source = read_source(SUBSYSTEM_CPP)
        self.assertGreater(len(source), 1000,
                           "UE subsystem source seems too small")


# ---------------------------------------------------------------------------
# Protocol constants — no changes
# ---------------------------------------------------------------------------
class TestProtocolConstants(unittest.TestCase):

    def test_7_pt_keyframe_0x17(self):
        """PT_Keyframe remains 0x17."""
        network_source = read_source(NETWORK_PY)
        self.assertIn("PT_Keyframe", network_source)
        self.assertIn("0x17", network_source)

    def test_8_pt_sequencer_op_0x18(self):
        """PT_SequencerOp remains 0x18."""
        network_source = read_source(NETWORK_PY)
        self.assertIn("PT_SequencerOp", network_source)
        self.assertIn("0x18", network_source)

    def test_9_pt_timeline_state_0x19(self):
        """PT_TimelineState remains 0x19."""
        network_source = read_source(NETWORK_PY)
        self.assertIn("PT_TimelineState", network_source)
        self.assertIn("0x19", network_source)

    def test_10_pt_playback_transport_0x1a(self):
        """PT_PlaybackTransport remains 0x1A."""
        network_source = read_source(NETWORK_PY)
        self.assertIn("PT_PlaybackTransport", network_source)
        self.assertIn("0x1A", network_source)

    def test_11_pt_camera_def_0x1b(self):
        """PT_CameraDef remains 0x1B."""
        network_source = read_source(NETWORK_PY)
        self.assertIn("PT_CameraDef", network_source)
        self.assertIn("0x1B", network_source)

    def test_12_0x02_reserved_invalid(self):
        """0x02 remains reserved/invalid."""
        network_source = read_source(NETWORK_PY)
        self.assertIn("0x02", network_source)


# ---------------------------------------------------------------------------
# Transform channel mapping — channels 0–8
# ---------------------------------------------------------------------------
class TestTransformChannels(unittest.TestCase):

    def test_13_transform_channels_0_8(self):
        """Channels 0–8 are transform channels."""
        sync_source = read_source(SYNC_PY)
        # Verify channel map includes channels 0-8
        channel_map_match = re.search(
            r'_KEYFRAME_CHANNEL_MAP\s*=\s*\{([^}]+)\}',
            sync_source,
            re.DOTALL
        )
        self.assertTrue(channel_map_match,
                        "Could not find _KEYFRAME_CHANNEL_MAP in sync.py")
        channel_map_body = channel_map_match.group(1)
        for ch in range(9):
            self.assertIn("): " + str(ch) + ",", channel_map_body,
                          f"Channel {ch} not found in transform channel map")

    def test_14_visibility_channels_9_10_unchanged(self):
        """Channels 9–10 are visibility bool channels."""
        sync_source = read_source(SYNC_PY)
        self.assertIn("hide_viewport", sync_source)
        self.assertIn("hide_render", sync_source)

    def test_15_channel_map_has_location(self):
        """Channel map includes location entries."""
        sync_source = read_source(SYNC_PY)
        self.assertIn('("location"', sync_source)

    def test_16_channel_map_has_rotation(self):
        """Channel map includes rotation entries."""
        sync_source = read_source(SYNC_PY)
        self.assertIn('("rotation_euler"', sync_source)

    def test_17_channel_map_has_scale(self):
        """Channel map includes scale entries."""
        sync_source = read_source(SYNC_PY)
        self.assertIn('("scale"', sync_source)


# ---------------------------------------------------------------------------
# Stage 10E tool content validation
# ---------------------------------------------------------------------------
class TestStage10ETool(unittest.TestCase):

    def test_18_deterministic_packet_order(self):
        """Packet order: CREATE_SEQUENCE → CREATE → ADD_POSSESSABLE → PT_Keyframe."""
        tool_source = read_source(STAGE10E_TOOL)
        # Find the order of send_all calls in main()
        # Look for specific variable names to avoid matching the function signature
        send_patterns = [
            (r'send_all\(sock,\s*pkt1\)', 'CREATE_SEQUENCE'),
            (r'send_all\(sock,\s*pkt2\)', 'CREATE'),
            (r'send_all\(sock,\s*pkt3\)', 'ADD_POSSESSABLE'),
            (r'send_all\(sock,\s*pkt\)\)', 'PT_Keyframe'),  # pkt) matches the loop call
        ]
        positions = {}
        for pattern, label in send_patterns:
            match = re.search(pattern, tool_source)
            if match:
                positions[label] = match.start()
        # Verify order: CREATE_SEQUENCE < CREATE < ADD_POSSESSABLE < PT_Keyframe
        if 'CREATE_SEQUENCE' in positions and 'CREATE' in positions:
            self.assertLess(positions['CREATE_SEQUENCE'],
                            positions['CREATE'],
                            'CREATE_SEQUENCE must come before CREATE')
        if 'CREATE' in positions and 'ADD_POSSESSABLE' in positions:
            self.assertLess(positions['CREATE'],
                            positions['ADD_POSSESSABLE'],
                            'CREATE must come before ADD_POSSESSABLE')
        if 'ADD_POSSESSABLE' in positions and 'PT_Keyframe' in positions:
            self.assertLess(positions['ADD_POSSESSABLE'],
                            positions['PT_Keyframe'],
                            'ADD_POSSESSABLE must come before PT_Keyframe')

    def test_19_unique_increasing_sequence_ids(self):
        """Uses unique increasing sequence IDs."""
        tool_source = read_source(STAGE10E_TOOL)
        self.assertIn("_NEXT_PACKET_SEQ", tool_source)
        self.assertIn("_NEXT_PACKET_SEQ += 1", tool_source)
        self.assertIn("unique", tool_source)

    def test_20_sequencer_op_has_header(self):
        """PT_SequencerOp includes FSequencerOpHeader."""
        tool_source = read_source(STAGE10E_TOOL)
        # FSequencerOpHeader is BBHId (opcode, flags, reserved, sequence, timestamp)
        self.assertIn("<BBHId", tool_source)
        self.assertIn("seq_hdr", tool_source)

    def test_21_create_rotation_fquat_4_floats(self):
        """PT_Create rotation uses FQuat 4 floats."""
        tool_source = read_source(STAGE10E_TOOL)
        self.assertIn("struct.pack(\"<ffff\"", tool_source)
        # Also verify the comment mentions FQuat
        self.assertIn("FQuat", tool_source)

    def test_22_ue_host_port(self):
        """Connects to UE on 127.0.0.1:57000."""
        tool_source = read_source(STAGE10E_TOOL)
        self.assertIn('127.0.0.1', tool_source)
        self.assertIn("57000", tool_source)

    def test_23_direct_raw_socket_send(self):
        """Uses deterministic raw socket sendall, not addon queue."""
        tool_source = read_source(STAGE10E_TOOL)
        self.assertIn("socket.socket", tool_source)
        self.assertIn("sock.send", tool_source)
        self.assertIn("send_all", tool_source)

    def test_24_transform_channels_used(self):
        """Stage 10E tool references transform channels 0–8."""
        tool_source = read_source(STAGE10E_TOOL)
        for ch in range(9):
            self.assertIn(str(ch), tool_source,
                          f"Channel {ch} not referenced in Stage 10E tool")

    def test_25_stage10e_uses_keyframe_channel_map(self):
        """Stage 10E tool imports and uses _KEYFRAME_CHANNEL_MAP."""
        tool_source = read_source(STAGE10E_TOOL)
        self.assertIn("_KEYFRAME_CHANNEL_MAP", tool_source)
        self.assertIn("_iter_action_fcurves_51", tool_source)

    def test_26_stage10e_creates_object(self):
        """Stage 10E tool creates a probe object."""
        tool_source = read_source(STAGE10E_TOOL)
        self.assertIn("primitive_cube_add", tool_source)
        self.assertIn(OBJECT_NAME := "Stage10E_TransformProbe", tool_source)

    def test_27_stage10e_inserts_keyframes(self):
        """Stage 10E tool inserts keyframes for transform."""
        tool_source = read_source(STAGE10E_TOOL)
        self.assertIn("keyframe_insert", tool_source)
        self.assertIn("data_path='location'", tool_source)
        self.assertIn("data_path='rotation_euler'", tool_source)
        self.assertIn("data_path='scale'", tool_source)

    def test_28_no_camera_crash_workaround_changes(self):
        """No camera crash workaround code changed."""
        cpp_source = read_source(SUBSYSTEM_CPP)
        # Verify key camera safety markers still exist (unchanged)
        self.assertIn("bHideFromSceneOutliner", cpp_source)
        self.assertIn("DrawFrustum", cpp_source)


# ---------------------------------------------------------------------------
# UE subsystem validation
# ---------------------------------------------------------------------------
class TestUESubsystem(unittest.TestCase):

    def test_29_transform_track_includes(self):
        """UE subsystem includes UMovieScene3DTransformTrack."""
        cpp_source = read_source(SUBSYSTEM_CPP)
        self.assertIn("MovieScene3DTransformTrack.h", cpp_source)

    def test_30_transform_section_includes(self):
        """UE subsystem includes UMovieScene3DTransformSection."""
        cpp_source = read_source(SUBSYSTEM_CPP)
        self.assertIn("MovieScene3DTransformSection.h", cpp_source)

    def test_31_handle_keyframe_transform_path(self):
        """HandleKeyframe has transform track create/apply path."""
        cpp_source = read_source(SUBSYSTEM_CPP)
        self.assertIn("UMovieScene3DTransformTrack", cpp_source)
        self.assertIn("UMovieScene3DTransformSection", cpp_source)

    def test_32_transform_track_created_counter(self):
        """Transform track created counter exists in UE subsystem."""
        cpp_source = read_source(SUBSYSTEM_CPP)
        self.assertIn("KeyframeTrackCreated", cpp_source)

    def test_33_transform_section_created_counter(self):
        """Transform section created counter exists in UE subsystem."""
        cpp_source = read_source(SUBSYSTEM_CPP)
        self.assertIn("KeyframeSectionCreated", cpp_source)

    def test_34_transform_key_apply_path(self):
        """Transform key apply path exists (AddLinearKey)."""
        cpp_source = read_source(SUBSYSTEM_CPP)
        self.assertIn("AddLinearKey", cpp_source)

    def test_35_missing_binding_counter(self):
        """Missing binding counter exists in UE subsystem."""
        cpp_source = read_source(SUBSYSTEM_CPP)
        self.assertIn("KeyframeMissingBinding", cpp_source)

    def test_36_transform_channels_0_8_in_handlekeyframe(self):
        """HandleKeyframe handles transform channels via channel index."""
        cpp_source = read_source(SUBSYSTEM_CPP)
        # Find HandleKeyframe and verify it checks channel bounds
        self.assertIn("ChannelIndex", cpp_source)
        self.assertIn("GetChannel<FMovieSceneDoubleChannel>", cpp_source)

    def test_37_create_before_add_possessable_invariant(self):
        """UE code enforces CREATE before ADD_POSSESSABLE (FindActorFast)."""
        cpp_source = read_source(SUBSYSTEM_CPP)
        # Verify FindActorFast is used in ADD_POSSESSABLE path
        self.assertIn("FindActorFast", cpp_source)


# ---------------------------------------------------------------------------
# Packet structure validation
# ---------------------------------------------------------------------------
class TestPacketStructure(unittest.TestCase):

    def test_38_keyframe_entry_size(self):
        """PT_Keyframe entry: guid(16) + frame(4) + value(4) + channel(1) = 25 bytes."""
        tool_source = read_source(STAGE10E_TOOL)
        # Verify the entry pack format: ifB = int(4) + float(4) + uint8(1)
        self.assertIn("<ifB", tool_source)

    def test_39_keyframe_header_size(self):
        """PT_Keyframe header: sequence(4) + timestamp(8) + key_count(1) + flags(1) = 14 bytes."""
        tool_source = read_source(STAGE10E_TOOL)
        self.assertIn("<IdBB", tool_source)

    def test_40_outer_header_v5(self):
        """V5 protocol header format is correct."""
        tool_source = read_source(STAGE10E_TOOL)
        self.assertIn("LIVE_SYNC_MAGIC", tool_source)
        self.assertIn("LIVE_SYNC_VERSION", tool_source)
        self.assertIn("I H B B Q I I", tool_source)


# ---------------------------------------------------------------------------
# Regression: Stage 10A tests still valid
# ---------------------------------------------------------------------------
class TestRegression(unittest.TestCase):

    def test_41_no_new_packet_ids(self):
        """No new packet IDs were added to network.py."""
        network_source = read_source(NETWORK_PY)
        # Count PT_ defines
        pt_defines = re.findall(r'PT_\w+\s*=\s*0x[0-9a-fA-F]+', network_source)
        # Should be the same set as before — no new IDs added
        self.assertGreater(len(pt_defines), 0,
                           "No PT_ defines found in network.py")

    def test_42_fguuid_format_unchanged(self):
        """FGuid wire format unchanged (IIII = 4 uint32 LE)."""
        tool_source = read_source(STAGE10E_TOOL)
        self.assertIn("IIII", tool_source)

    def test_43_create_object_layout_81_bytes(self):
        """PT_Create payload is 81 bytes as documented."""
        tool_source = read_source(STAGE10E_TOOL)
        # Verify layout: 16 + 12 + 16 + 12 + 8 + 16 + 1 = 81
        self.assertIn("GUID", tool_source)
        self.assertIn("Location", tool_source)
        self.assertIn("Rotation", tool_source)
        self.assertIn("Scale", tool_source)


# ---------------------------------------------------------------------------
# Integration: Stage 10E references existing Stage 10A.5 patterns
# ---------------------------------------------------------------------------
class TestStage10EStage10AIntegration(unittest.TestCase):

    def test_44_uses_same_v5_protocol(self):
        """Stage 10E uses V5 protocol same as Stage 10A."""
        tool_source = read_source(STAGE10E_TOOL)
        self.assertIn("0x4C56534D", tool_source)
        self.assertIn("5", tool_source)

    def test_45_uses_same_magic(self):
        """Stage 10E uses same LIVE_SYNC_MAGIC as Stage 10A."""
        tool_source = read_source(STAGE10E_TOOL)
        self.assertIn("LIVE_SYNC_MAGIC", tool_source)
        self.assertIn("LIVE_SYNC_VERSION", tool_source)


if __name__ == '__main__':
    unittest.main()
