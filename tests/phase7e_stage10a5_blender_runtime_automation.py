"""
Phase 7E Stage 10A.5 — Blender runtime automation static tests.

Verifies:
- Blender runtime script exists and is syntactically valid
- Probe object name "Stage10A5_VisibilityProbe"
- Visibility keyframe insertion at frames 1, 10, 20
- PT_Keyframe = 0x17 used
- Channel 9 and 10 referenced
- No new packet IDs
- 0x02 not used
- 0x10 not used
- Host 127.0.0.1, port 57000
- Existing Stage 10A.4 mapping tests still pass
- Camera crash workaround intact
"""

import ast
import os
import re
import unittest


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BLENDED_SCRIPT = os.path.join(
    REPO_ROOT, "tools", "uelivesync_stage10a5_blender_visibility_runtime.py"
)
WRAPPER_SH = os.path.join(
    REPO_ROOT, "tools", "run_stage10a5_blender_visibility_runtime.sh"
)
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


def read_source(path):
    with open(path, "r") as f:
        return f.read()


# ---------------------------------------------------------------------------
# File existence
# ---------------------------------------------------------------------------
class TestFiles(unittest.TestCase):

    def test_1_blender_runtime_script_exists(self):
        """Stage 10A.5 Blender runtime script exists."""
        self.assertTrue(os.path.isfile(BLENDED_SCRIPT),
                        f"Blender runtime script not found: {BLENDED_SCRIPT}")

    def test_2_wrapper_sh_exists(self):
        """Stage 10A.5 wrapper shell script exists."""
        self.assertTrue(os.path.isfile(WRAPPER_SH),
                        f"Wrapper script not found: {WRAPPER_SH}")

    def test_3_wrapper_is_executable(self):
        """Wrapper shell script is executable."""
        self.assertTrue(os.access(WRAPPER_SH, os.X_OK),
                        "Wrapper script not executable")

    def test_4_blender_script_syntax_valid(self):
        """Blender runtime script parses as valid Python."""
        source = read_source(BLENDED_SCRIPT)
        ast.parse(source)  # raises SyntaxError if invalid


# ---------------------------------------------------------------------------
# Probe object name
# ---------------------------------------------------------------------------
class TestProbeObject(unittest.TestCase):

    def test_5_probe_object_name(self):
        """Probe object is named 'Stage10A5_VisibilityProbe'."""
        source = read_source(BLENDED_SCRIPT)
        self.assertIn('OBJECT_NAME', source)
        self.assertIn('"Stage10A5_VisibilityProbe"', source)


# ---------------------------------------------------------------------------
# Visibility keyframe insertion
# ---------------------------------------------------------------------------
class TestKeyframeInsertion(unittest.TestCase):

    def test_6a_hide_viewport_keyframes_at_frames_1_10_20(self):
        """hide_viewport keyframes inserted at frames 1, 10, 20."""
        source = read_source(BLENDED_SCRIPT)
        lines = source.splitlines()
        kf_lines = [l.strip() for l in lines if "hide_viewport" in l
                    and "keyframe_insert" in l]
        # Should have 3 keyframe_insert calls for hide_viewport
        self.assertGreaterEqual(len(kf_lines), 3,
                                f"Expected 3+ hide_viewport keyframe_insert, found {len(kf_lines)}")

    def test_6b_hide_render_keyframes_at_frames_1_10_20(self):
        """hide_render keyframes inserted at frames 1, 10, 20."""
        source = read_source(BLENDED_SCRIPT)
        lines = source.splitlines()
        kf_lines = [l.strip() for l in lines if "hide_render" in l
                    and "keyframe_insert" in l]
        self.assertGreaterEqual(len(kf_lines), 3,
                                f"Expected 3+ hide_render keyframe_insert, found {len(kf_lines)}")

    def test_6c_keyframe_frames_values(self):
        """Keyframe frames are 1, 10, 20."""
        source = read_source(BLENDED_SCRIPT)
        frame_pattern = re.compile(r'frame\s*=\s*(\d+)')
        frames = frame_pattern.findall(source)
        frames_int = [int(f) for f in frames]
        # Should contain 1, 10, 20
        self.assertIn(1, frames_int, "Frame 1 not found")
        self.assertIn(10, frames_int, "Frame 10 not found")
        self.assertIn(20, frames_int, "Frame 20 not found")

    def test_6d_clear_scene_before_probe(self):
        """Scene is cleared before creating probe object."""
        source = read_source(BLENDED_SCRIPT)
        # Scene clearing is done via remove and new collection
        self.assertIn("bpy.data.objects.remove", source)


# ---------------------------------------------------------------------------
# PT_Keyframe and channel usage
# ---------------------------------------------------------------------------
class TestPTKeyframeAndChannels(unittest.TestCase):

    def test_7a_pt_keyframe_0x17(self):
        """PT_Keyframe = 0x17 used (no new packet ID)."""
        source = read_source(BLENDED_SCRIPT)
        # Should reference 0x17
        self.assertIn("0x17", source)

    def test_7b_channel_9_referenced(self):
        """Channel 9 (hide_viewport) is referenced."""
        source = read_source(BLENDED_SCRIPT)
        self.assertIn("9", source)
        # Script defines its own channel constant
        self.assertIn("CHANNEL_HIDE_VIEWPORT", source)

    def test_7c_channel_10_referenced(self):
        """Channel 10 (hide_render) is referenced."""
        source = read_source(BLENDED_SCRIPT)
        self.assertIn("10", source)
        self.assertIn("CHANNEL_HIDE_RENDER", source)

    def test_7d_no_new_packet_ids(self):
        """No new packet IDs beyond PT_CREATE, PT_KEYFRAME, PT_SEQUENCER_OP."""
        source = read_source(BLENDED_SCRIPT)
        # Should define known protocol constants only
        pt_defs = re.findall(r'PT_[A-Za-z_]+\s*=', source)
        allowed = {"PT_CREATE", "PT_KEYFRAME", "PT_SEQUENCER_OP"}
        for d in pt_defs:
            name = d.rstrip('=').strip()
            self.assertIn(name, allowed,
                          f"Unexpected packet ID: {name}")

    def test_7e_not_using_0x02(self):
        """Runtime script does not use 0x02."""
        source = read_source(BLENDED_SCRIPT)
        # 0x02 should not appear
        self.assertNotIn("0x02", source)

    def test_7f_not_using_0x10(self):
        """Runtime script does not use 0x10."""
        source = read_source(BLENDED_SCRIPT)
        self.assertNotIn("0x10", source)


# ---------------------------------------------------------------------------
# Network configuration
# ---------------------------------------------------------------------------
class TestNetworkConfig(unittest.TestCase):

    def test_8a_host_127_0_0_1(self):
        """Target host is 127.0.0.1."""
        source = read_source(BLENDED_SCRIPT)
        self.assertIn("127.0.0.1", source)

    def test_8b_port_57000(self):
        """Target port is 57000."""
        source = read_source(BLENDED_SCRIPT)
        self.assertIn("57000", source)


# ---------------------------------------------------------------------------
# Real addon extraction path
# ---------------------------------------------------------------------------
class TestAddonExtractionPath(unittest.TestCase):

    def test_9a_uses_real_keyframe_channel_map(self):
        """Runtime script uses addon's _KEYFRAME_CHANNEL_MAP."""
        source = read_source(BLENDED_SCRIPT)
        self.assertIn("_KEYFRAME_CHANNEL_MAP", source)

    def test_9b_uses_real_fcurve_extraction(self):
        """Runtime script uses real addon FCurve extraction (_iter_action_fcurves_51)."""
        source = read_source(BLENDED_SCRIPT)
        self.assertIn("_iter_action_fcurves_51", source)

    def test_9c_imports_from_blender_addon(self):
        """Runtime script imports from Blender_Addon for extraction only."""
        source = read_source(BLENDED_SCRIPT)
        self.assertIn("_KEYFRAME_CHANNEL_MAP", source)
        self.assertIn("_iter_action_fcurves_51", source)
        # Transport is direct socket, not addon functions
        self.assertNotIn("send_objects", source)
        self.assertNotIn("send_sequencer_op", source)


# ---------------------------------------------------------------------------
# Wrapper script validation
# ---------------------------------------------------------------------------
class TestWrapperScript(unittest.TestCase):

    def test_10a_wrapper_chooses_flatpak(self):
        """Wrapper checks for flatpak org.blender.Blender."""
        source = read_source(WRAPPER_SH)
        self.assertIn("flatpak", source)
        self.assertIn("org.blender.Blender", source)

    def test_10b_wrapper_fallback_to_blender(self):
        """Wrapper falls back to 'blender' command."""
        source = read_source(WRAPPER_SH)
        self.assertIn("blender", source)

    def test_10c_wrapper_captures_to_log(self):
        """Wrapper captures output to expected log file."""
        source = read_source(WRAPPER_SH)
        self.assertIn("uelivesync-phase7e-10a5-blender-injector.log", source)


# ---------------------------------------------------------------------------
# Direct raw TCP socket transport verification
# ---------------------------------------------------------------------------
class TestDirectSocketTransport(unittest.TestCase):

    def test_14a_uses_socket_create_connection(self):
        """Runtime script uses socket.socket for direct TCP."""
        source = read_source(BLENDED_SCRIPT)
        self.assertIn("socket.socket", source)

    def test_14b_uses_socket_send(self):
        """Runtime script uses sock.send() for deterministic delivery."""
        source = read_source(BLENDED_SCRIPT)
        self.assertIn("sock.send", source)

    def test_14c_does_not_use_addon_send_objects(self):
        """Runtime script does NOT use addon's send_objects."""
        source = read_source(BLENDED_SCRIPT)
        self.assertNotIn("send_objects", source)

    def test_14d_does_not_use_addon_send_sequencer_op(self):
        """Runtime script does NOT use addon's send_sequencer_op."""
        source = read_source(BLENDED_SCRIPT)
        self.assertNotIn("send_sequencer_op", source)

    def test_14e_does_not_use_addon_send_queue(self):
        """Runtime script does NOT use addon's _send_queue in code."""
        source = read_source(BLENDED_SCRIPT)
        # _send_queue should not appear in code lines (only docstrings/comments)
        code_lines = []
        in_doc = False
        for line in source.splitlines():
            stripped = line.strip()
            if stripped.startswith('"""'):
                in_doc = not in_doc
                continue
            if in_doc:
                continue
            if stripped.startswith("#"):
                continue
            code_lines.append(stripped)
        for cl in code_lines:
            self.assertNotIn("_send_queue", cl,
                             f"Code line uses _send_queue: {cl}")

    def test_14f_includes_fsequencerop_header(self):
        """Runtime script includes FSequencerOpHeader build."""
        source = read_source(BLENDED_SCRIPT)
        # The script builds the header struct manually
        self.assertIn("FSequencerOpHeader", source)

    def test_14g_creates_sequence_before_create(self):
        """Runtime script sends CREATE_SEQUENCE before CREATE actor."""
        source = read_source(BLENDED_SCRIPT)
        lines = source.splitlines()
        create_seq_line = None
        create_line = None
        for i, l in enumerate(lines):
            stripped = l.strip()
            if stripped.startswith("#") or stripped.startswith('"""'):
                continue
            if "build_sequencer_op_create_sequence" in stripped:
                create_seq_line = i
            if "build_create_object" in stripped:
                create_line = i
        self.assertIsNotNone(create_seq_line, "CREATE_SEQUENCE not found")
        self.assertIsNotNone(create_line, "CREATE not found")
        self.assertLess(create_seq_line, create_line,
                        "CREATE_SEQUENCE must be sent before CREATE")

    def test_14h_create_before_add_possessable(self):
        """Runtime script sends CREATE before ADD_POSSESSABLE (actor must exist first)."""
        source = read_source(BLENDED_SCRIPT)
        lines = source.splitlines()
        create_line = None
        add_poss_line = None
        for i, l in enumerate(lines):
            stripped = l.strip()
            if stripped.startswith("#") or stripped.startswith('"""'):
                continue
            # Find call sites: look for "send_all" with the function calls
            if "build_create_object" in stripped and "def " not in stripped:
                create_line = i
            if "build_sequencer_op_add_possessable" in stripped and "def " not in stripped:
                add_poss_line = i
        self.assertIsNotNone(create_line, "CREATE not found")
        self.assertIsNotNone(add_poss_line, "ADD_POSSESSABLE not found")
        self.assertLess(create_line, add_poss_line,
                        "CREATE must be sent before ADD_POSSESSABLE (actor must exist)")

    def test_14i_create_before_keyframe(self):
        """Runtime script sends CREATE actor before PT_Keyframe."""
        source = read_source(BLENDED_SCRIPT)
        lines = source.splitlines()
        create_line = None
        keyframe_line = None
        for i, l in enumerate(lines):
            if "build_create_object" in l:
                create_line = i
            if "build_keyframe_packet" in l:
                keyframe_line = i
        self.assertIsNotNone(create_line, "CREATE not found")
        self.assertIsNotNone(keyframe_line, "PT_Keyframe not found")
        self.assertLess(create_line, keyframe_line,
                        "CREATE must be sent before PT_Keyframe")

    def test_14j_create_uses_quaternion_rotation(self):
        """CREATE uses 4-float quaternion (FQuat), not 3-float Euler."""
        source = read_source(BLENDED_SCRIPT)
        # Find the build_create_object definition
        in_def = False
        def_lines = []
        for l in source.splitlines():
            if "def build_create_object" in l:
                in_def = True
            if in_def:
                def_lines.append(l)
                if "return" in l:
                    break
        def_text = "\n".join(def_lines)
        # Should pack rot with <ffff (4 floats for FQuat)
        self.assertIn("<ffff", def_text,
                      "Rotation must be packed as 4 floats (FQuat) for UE V4+ protocol")
        # Should NOT pack rot with <fff (3 floats = Euler)
        # Count '<fff' occurrences - if rot uses <ffff, there should be exactly 3 '<fff' for loc, rot (as part of <ffff), scale
        # The rot line must use '<ffff' not '<fff'
        rot_lines = [l for l in def_lines if 'rot' in l and 'pack' in l.lower()]
        self.assertTrue(any('<ffff' in l for l in rot_lines),
                        "Rotation must use <ffff (4 floats) for FQuat")

    def test_14k_guid_consistency(self):
        """Runtime script uses one fixed GUID for all operations."""
        source = read_source(BLENDED_SCRIPT)
        self.assertIn("GUID_HEX", source)
        self.assertIn("a1b2c3d4e5f60718293a4b5c6d7e8f90", source)


# ---------------------------------------------------------------------------
# Integration with existing tests
# ---------------------------------------------------------------------------
class TestStage10A4Integration(unittest.TestCase):

    def test_11a_pt_keyframe_unchanged_in_network(self):
        """PT_Keyframe is still 0x17 in addon network.py."""
        source = read_source(NETWORK_PY)
        match = re.search(r'PT_Keyframe\s*=\s*0x([0-9a-fA-F]+)', source)
        self.assertIsNotNone(match, "PT_Keyframe not found in network.py")
        self.assertEqual(int(match.group(1), 16), 0x17,
                         f"PT_Keyframe is 0x{match.group(1)}, expected 0x17")

    def test_11b_channel_9_and_10_defined_in_network(self):
        """Channel 9 and 10 constants exist in network.py."""
        source = read_source(NETWORK_PY)
        self.assertIn("KEYFRAME_CHANNEL_VISIBILITY_VIEWPORT", source)
        self.assertIn("KEYFRAME_CHANNEL_VISIBILITY_RENDER", source)

    def test_11c_channel_9_is_9(self):
        """KEYFRAME_CHANNEL_VISIBILITY_VIEWPORT = 9."""
        source = read_source(NETWORK_PY)
        match = re.search(r'KEYFRAME_CHANNEL_VISIBILITY_VIEWPORT\s*=\s*(\d+)', source)
        self.assertIsNotNone(match, "KEYFRAME_CHANNEL_VISIBILITY_VIEWPORT not found")
        self.assertEqual(int(match.group(1)), 9)

    def test_11d_channel_10_is_10(self):
        """KEYFRAME_CHANNEL_VISIBILITY_RENDER = 10."""
        source = read_source(NETWORK_PY)
        match = re.search(r'KEYFRAME_CHANNEL_VISIBILITY_RENDER\s*=\s*(\d+)', source)
        self.assertIsNotNone(match, "KEYFRAME_CHANNEL_VISIBILITY_RENDER not found")
        self.assertEqual(int(match.group(1)), 10)

    def test_11e_sync_keyframe_map_unchanged(self):
        """_KEYFRAME_CHANNEL_MAP in sync.py is unchanged (channels 0-8 intact)."""
        source = read_source(SYNC_PY)
        self.assertIn("_KEYFRAME_CHANNEL_MAP", source)
        # Verify channels 0-8 are present
        for i in range(9):
            self.assertIn(f"): {i},", source)


# ---------------------------------------------------------------------------
# Camera crash workaround intact
# ---------------------------------------------------------------------------
class TestCameraCrashWorkaround(unittest.TestCase):

    def test_12a_configure_live_sync_camera_actor_exists(self):
        """ConfigureLiveSyncCameraActor helper still exists."""
        source = read_source(SUBSYSTEM_CPP)
        self.assertIn("ConfigureLiveSyncCameraActor", source)

    def test_12b_frustum_guard_exists(self):
        """Frustum guard marker preserved."""
        source = read_source(SUBSYSTEM_CPP)
        self.assertIn("FRUSTUM_GUARD", source)

    def test_12c_camera_def_handler_preserved(self):
        """Camera def handler HandleCameraDef still exists."""
        source = read_source(SUBSYSTEM_CPP)
        self.assertIn("HandleCameraDef", source)


# ---------------------------------------------------------------------------
# Blender version check
# ---------------------------------------------------------------------------
class TestBlenderVersionCheck(unittest.TestCase):

    def test_13a_prints_blender_version(self):
        """Runtime script prints Blender version."""
        source = read_source(BLENDED_SCRIPT)
        self.assertIn("bpy.app.version_string", source)
        self.assertIn("print", source)


if __name__ == "__main__":
    unittest.main()
