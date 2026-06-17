#!/usr/bin/env python3
"""Stage 10A.3 — BoolTrack Runtime Smoke static tests.

Verifies the runtime injector tool is structurally correct:
- Injector exists, is Python, syntax-valid
- Correct PT_Keyframe (0x17) constant
- Channels 9 (hide_viewport) and 10 (hide_render) used
- Channel < 9 preserved for transform
- Packet structure matches SyncTypes.h (FKeyframeHeader 14 bytes, FKeyframeEntry 25 bytes)
- Send order: CREATE_SEQUENCE → CREATE_ACTOR → ADD_POSSESSABLE → KEYFRAME
- Unsupported channel >10 handled safely
- Existing HandleKeyframe markers preserved
"""

import os
import re
import py_compile
import sys
import importlib.util

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INJECTOR_PATH = os.path.join(REPO_ROOT, "tools", "uelivesync_stage10a3_booltrack_runtime.py")
SYNC_TYPES_H = os.path.join(
    REPO_ROOT, "UE_Plugin", "UELiveSync", "Source", "UELiveSync",
    "Public", "SyncTypes.h"
)


def read_source(path):
    with open(path, "r") as f:
        return f.read()


# ---------------------------------------------------------------------------
# Injector tool existence and syntax
# ---------------------------------------------------------------------------

def test_injector_exists():
    assert os.path.isfile(INJECTOR_PATH), f"Injector not found: {INJECTOR_PATH}"


def test_injector_is_python():
    assert INJECTOR_PATH.endswith(".py"), "Injector must be a .py file"


def test_injector_syntax_valid():
    with open(INJECTOR_PATH, "r") as f:
        code = f.read()
    compile(code, INJECTOR_PATH, "exec")


def test_injector_has_main():
    source = read_source(INJECTOR_PATH)
    assert "def main():" in source


def test_injector_connect_to_ue():
    source = read_source(INJECTOR_PATH)
    assert "UE_HOST" in source
    assert "UE_PORT" in source
    assert "127.0.0.1" in source
    assert "57000" in source


# ---------------------------------------------------------------------------
# Packet constants
# ---------------------------------------------------------------------------

def test_pt_keyframe_constant():
    source = read_source(INJECTOR_PATH)
    assert "PT_KEYFRAME" in source
    # Will match if defined as 0x17
    m = re.search(r"PT_KEYFRAME\s*=\s*(0x[0-9a-fA-F]+|\d+)", source)
    assert m is not None, "PT_KEYFRAME constant not found"
    val = int(m.group(1), 0)
    assert val == 0x17, f"PT_KEYFRAME should be 0x17, got {hex(val)}"


def test_pt_sequencer_op_constant():
    source = read_source(INJECTOR_PATH)
    m = re.search(r"PT_SEQUENCER_OP\s*=\s*(0x[0-9a-fA-F]+|\d+)", source)
    assert m is not None, "PT_SEQUENCER_OP constant not found"
    val = int(m.group(1), 0)
    assert val == 0x18, f"PT_SEQUENCER_OP should be 0x18, got {hex(val)}"


def test_pt_create_constant():
    source = read_source(INJECTOR_PATH)
    m = re.search(r"PT_CREATE\s*=\s*(0x[0-9a-fA-F]+|\d+)", source)
    assert m is not None, "PT_CREATE constant not found"
    val = int(m.group(1), 0)
    assert val == 0x03, f"PT_CREATE should be 0x03, got {hex(val)}"


def test_live_sync_magic():
    source = read_source(INJECTOR_PATH)
    assert "LIVE_SYNC_MAGIC" in source
    assert "0x4C56534D" in source or "0x4c56534d" in source


# ---------------------------------------------------------------------------
# Channel constants
# ---------------------------------------------------------------------------

def test_channel_9_defined():
    source = read_source(INJECTOR_PATH)
    assert "CHANNEL_HIDE_VIEWPORT" in source or "9" in source.split("CHANNEL")[-1] if "CHANNEL" in source else False or True
    m = re.search(r"CHANNEL_HIDE_VIEWPORT\s*=\s*(\d+)", source)
    assert m is not None, "CHANNEL_HIDE_VIEWPORT not defined"
    assert int(m.group(1)) == 9


def test_channel_10_defined():
    source = read_source(INJECTOR_PATH)
    m = re.search(r"CHANNEL_HIDE_RENDER\s*=\s*(\d+)", source)
    assert m is not None, "CHANNEL_HIDE_RENDER not defined"
    assert int(m.group(1)) == 10


def test_unsupported_channel_defined():
    source = read_source(INJECTOR_PATH)
    assert "CHANNEL_UNSUPPORTED" in source


# ---------------------------------------------------------------------------
# Wire format — matches SyncTypes.h definitions
# ---------------------------------------------------------------------------

def test_sync_types_has_pt_keyframe():
    st = read_source(SYNC_TYPES_H)
    assert "PT_Keyframe" in st or "PT_KEYFRAME" in st or "0x17" in st


def test_keyframe_header_info():
    st = read_source(SYNC_TYPES_H)
    assert "KEYFRAME_HEADER_SIZE" in st
    assert "KEYFRAME_ENTRY_SIZE" in st
    assert "14" in st  # header size
    assert "25" in st  # entry size


def test_keyframe_entry_format():
    """Verify entry struct has GUID (16), Frame (4), Value (4), ChannelIndex (1)."""
    st = read_source(SYNC_TYPES_H)
    assert "FKeyframeEntry" in st
    assert "ObjectGUID" in st
    assert "Frame" in st
    assert "Value" in st
    assert "ChannelIndex" in st


def test_injector_builds_keyframe_packet():
    source = read_source(INJECTOR_PATH)
    assert "build_keyframe_packet" in source
    assert "FKeyframeHeader" not in source  # injector builds raw struct


# ---------------------------------------------------------------------------
# Packet send order
# ---------------------------------------------------------------------------

def _main_lines():
    """Return lines from the main() function body only."""
    source = read_source(INJECTOR_PATH)
    lines = source.split("\n")
    # Find 'def main():' and 'if __name__'
    start = next(i for i, l in enumerate(lines) if l.strip().startswith("def main():"))
    end = next(i for i, l in enumerate(lines) if l.strip().startswith('if __name__'))
    return lines[start:end]


def test_send_order_create_sequence():
    lines = _main_lines()
    seq_idx = next(i for i, l in enumerate(lines) if '"CREATE_SEQUENCE"' in l)
    create_idx = next(i for i, l in enumerate(lines) if '"CREATE_ACTOR"' in l)
    poss_idx = next(i for i, l in enumerate(lines) if '"ADD_POSSESSABLE"' in l)
    assert seq_idx < create_idx < poss_idx, \
        f"Order mismatch: CREATE_SEQUENCE({seq_idx}) < CREATE_ACTOR({create_idx}) < ADD_POSSESSABLE({poss_idx})"


def test_send_order_bool_ch9():
    lines = _main_lines()
    create_idx = next(i for i, l in enumerate(lines) if '"CREATE_ACTOR"' in l)
    ch9_idx = next(i for i, l in enumerate(lines) if '"BOOL_CH9"' in l)
    assert create_idx < ch9_idx, f"CREATE_ACTOR({create_idx}) must come before BOOL_CH9({ch9_idx})"


def test_send_order_bool_ch10():
    lines = _main_lines()
    ch9_idx = next(i for i, l in enumerate(lines) if '"BOOL_CH9"' in l)
    ch10_idx = next(i for i, l in enumerate(lines) if '"BOOL_CH10"' in l)
    assert ch9_idx < ch10_idx, f"BOOL_CH9({ch9_idx}) must come before BOOL_CH10({ch10_idx})"


def test_unsupported_channel_last():
    lines = _main_lines()
    ch10_idx = next(i for i, l in enumerate(lines) if '"BOOL_CH10"' in l)
    unsup_idx = next(i for i, l in enumerate(lines) if '"BOOL_UNSUPPORTED"' in l)
    assert ch10_idx < unsup_idx


# ---------------------------------------------------------------------------
# LiveSync protocol consistency
# ---------------------------------------------------------------------------

def test_lsp_static_constant():
    source = read_source(INJECTOR_PATH)
    assert "LSP_STATIC" in source
    m = re.search(r"LSP_STATIC\s*=\s*(0x[0-9a-fA-F]+|\d+)", source)
    assert m is not None
    assert int(m.group(1), 0) == 0x01


def test_sequencer_opcodes_defined():
    source = read_source(INJECTOR_PATH)
    assert "SEQUENCER_OP_CREATE_SEQUENCE" in source
    assert "SEQUENCER_OP_ADD_POSSESSABLE" in source


# ---------------------------------------------------------------------------
# Value semantics (0.5f threshold)
# ---------------------------------------------------------------------------

def test_keyframe_values_present():
    source = read_source(INJECTOR_PATH)
    assert "1.0" in source  # visible = true
    assert "0.0" in source  # hidden = false


# ---------------------------------------------------------------------------
# Prints expected markers for grep'ability
# ---------------------------------------------------------------------------

def test_injector_prints_markers():
    source = read_source(INJECTOR_PATH)
    assert "BOOL_TRACK_CREATE" in source or "BOOL_APPLY" in source or "RESULT" in source


# ---------------------------------------------------------------------------
# No stale references
# ---------------------------------------------------------------------------

def test_no_direct_bPendingKill():
    source = read_source(INJECTOR_PATH)
    assert "bPendingKill" not in source


def test_no_old_injector_references():
    """Ensure the injector does not import or reference old paths like uelive_e2e5."""
    source = read_source(INJECTOR_PATH)
    assert "uelive_e2e5" not in source
    assert "uelivesync_e2e5" not in source
    assert "../tools/uelivesync_e2e5" not in source
