#!/usr/bin/env python3
"""
Phase 7E Stage 10F — Multi-object Transform + Visibility Sequencer Runtime Validation.

Creates **two** Blender objects with independent transform keyframes and visibility
keyframes, extracts FCurves via the real addon's channelbag-safe path, then sends
all packets via **direct raw TCP socket** (bypassing the addon's _send_queue
for deterministic ordering).

Validation goal:
  - Both objects get unique bindings in the same LevelSequence.
  - Transform keys for Object A applied to Object A's track/section only.
  - Transform keys for Object B applied to Object B's track/section only.
  - Visibility bool keys for Object A applied correctly.
  - No cross-binding contamination.
  - No missing bindings, no unsupported channels.
  - UE process stable (no Signal 11, Signal 6, SceneOutliner crash, DrawFrustum crash).

Packet order (deterministic):
  1. CREATE_SEQUENCE
  2. CREATE object A
  3. CREATE object B
  4. ADD_POSSESSABLE object A
  5. ADD_POSSESSABLE object B
  6. PT_Keyframe packets for object A (transform + visibility)
  7. PT_Keyframe packets for object B (transform only)

Required UE markers:
    Object A transform keys:  [KEYFRAME] applied
    Object B transform keys:  [KEYFRAME] applied
    Object A visibility keys: [KEYFRAME][BOOL_APPLY]
    [KEYFRAME] missing_binding = 0
    [KEYFRAME] unsupported_channel = 0
    Signal11 = 0
    Signal6  = 0
    SceneOutliner crash = 0
    DrawFrustum crash = 0
"""

import bpy
import struct
import socket
import time
import uuid
import sys
import os

# ---- Configuration ----
UE_HOST = "127.0.0.1"
UE_PORT = 57000
OBJECT_A_NAME = "Stage10F_ObjectA"
OBJECT_B_NAME = "Stage10F_ObjectB"
GUID_A_HEX = "a1b2c3d4e5f60718293a4b5c6d7e8f91"   # unique GUID for Object A
GUID_B_HEX = "b2c3d4e5f6a70819304b5c6d7e8f9012"    # unique GUID for Object B
KEYFRAME_TIMES = [1, 10, 20]  # frames

# ---- Protocol Constants ----
LIVE_SYNC_MAGIC    = 0x4C56534D
LIVE_SYNC_VERSION  = 5

PT_CREATE          = 0x03
PT_KEYFRAME        = 0x17
PT_SEQUENCER_OP    = 0x18

SEQUENCER_OP_CREATE_SEQUENCE = 0
SEQUENCER_OP_ADD_POSSESSABLE = 1

LSP_STATIC = 0x01  # Primitive

# Transform channel mapping (matches _KEYFRAME_CHANNEL_MAP in sync.py):
#   location X = 0, Y = 1, Z = 2
#   rotation X = 3, Y = 4, Z = 5
#   scale X = 6, Y = 7, Z = 8


def get_blender_version():
    return bpy.app.version_string


def get_addon_path():
    paths = [
        "/home/nguyennongngockhanh/.var/app/org.blender.Blender/config/blender/5.1/scripts/addons/Blender_Addon",
        "/home/nguyennongngockhanh/Projects/UELiveSync/Blender_Addon",
    ]
    for p in paths:
        if os.path.isdir(p):
            return p
    return None


def add_sync_to_path(addon_path):
    if addon_path not in sys.path:
        sys.path.insert(0, addon_path)


# ---- Packet Builders (direct raw TCP, matching addon protocol) ----

def pack_fguid_full(g):
    """Pack UUID as 16-byte GUID for V4+ protocol."""
    d_a = g.time_low
    d_b = (g.time_mid << 16) | g.time_hi_version
    d_c = (g.clock_seq_hi_variant << 24
           | g.clock_seq_low << 16
           | (g.node >> 32) & 0xFFFF)
    d_d = g.node & 0xFFFFFFFF
    return struct.pack("<IIII", d_a, d_b, d_c, d_d)


def pack_guid_bytes(guid_hex_str):
    """Pack a hex GUID string (without braces) as 16 bytes LE uint32x4."""
    g = uuid.UUID(guid_hex_str)
    return pack_fguid_full(g)


def get_uuid_from_hex(guid_hex_str):
    return uuid.UUID(guid_hex_str)


# Global packet sequence counter — each packet gets a unique increasing seq
_NEXT_PACKET_SEQ = 4000  # Stage 10F — unique seq range to avoid stale rejection from Stage 10E (3000-3009)


def build_outer_header(ptype, payload_len, obj_count=1):
    """Build V5 protocol header matching addon's _build_packet.

    Each packet MUST have a unique, monotonically increasing sequence number.
    The addon enforces: if (SequenceId <= LastSequenceId) return;
    """
    global _NEXT_PACKET_SEQ
    header_size = 24
    packet_size = header_size + payload_len
    seq = _NEXT_PACKET_SEQ
    _NEXT_PACKET_SEQ += 1
    return struct.pack("<I H B B Q I I",
                       LIVE_SYNC_MAGIC, LIVE_SYNC_VERSION, ptype, 0,
                       seq, packet_size, obj_count)


def build_sequencer_op_create_sequence(seq_id, ts):
    """FSequencerOpHeader + CREATE_SEQUENCE opcode payload.

    Header: opcode(1) + flags(1) + reserved(2) + sequence(4) + timestamp(8) = 16 bytes
    Payload: frame_start(4) + frame_end(4) + fps_num(4) + fps_den(4) = 16 bytes
    """
    seq_hdr = struct.pack("<BBHId",
                          SEQUENCER_OP_CREATE_SEQUENCE, 0, 0,
                          seq_id, ts)
    op_payload = struct.pack("<IIII", 1, 30, 24, 1)  # frames 1-30, 24fps
    total_payload = len(seq_hdr) + len(op_payload)
    pkt = build_outer_header(PT_SEQUENCER_OP, total_payload, obj_count=0)
    return pkt + seq_hdr + op_payload


def build_sequencer_op_add_possessable(seq_id, ts, guid_bytes):
    """FSequencerOpHeader + ADD_POSSESSABLE opcode payload (guid + binding_type).

    Header: opcode(1) + flags(1) + reserved(2) + sequence(4) + timestamp(8) = 16 bytes
    Payload: object_guid(16) + binding_type(1) = 17 bytes
    """
    seq_hdr = struct.pack("<BBHId",
                          SEQUENCER_OP_ADD_POSSESSABLE, 0, 0,
                          seq_id, ts)
    op_payload = guid_bytes + struct.pack("<B", 1)  # binding_type=1 (actor)
    total_payload = len(seq_hdr) + len(op_payload)
    pkt = build_outer_header(PT_SEQUENCER_OP, total_payload, obj_count=0)
    return pkt + seq_hdr + op_payload


def build_create_object(guid_bytes, loc=(0.0, 0.0, 200.0), rot=(0.0, 0.0, 0.0, 1.0),
                        scale=(1.0, 1.0, 1.0), prim_type=LSP_STATIC):
    """PT_Create packet for a static actor.

    V4+ transform object layout (81 bytes):
      [0]   GUID:           16 bytes (4×uint32 LE)
      [16]  Location:       12 bytes (3×float)
      [28]  Rotation:       16 bytes (4×float = FQuat)
      [44]  Scale:          12 bytes (3×float)
      [56]  Timestamp:      8 bytes (double)
      [64]  Parent GUID:    16 bytes (4×uint128)
      [80]  PrimitiveType:  1 byte (uint8)
    """
    payload = bytearray()
    payload.extend(guid_bytes)
    payload.extend(struct.pack("<fff", *loc))
    payload.extend(struct.pack("<ffff", *rot))  # FQuat: 4 floats
    payload.extend(struct.pack("<fff", *scale))
    payload.extend(struct.pack("<d", time.time()))
    payload.extend(b'\x00' * 16)  # no parent
    payload.append(prim_type)
    pkt = build_outer_header(PT_CREATE, len(payload), obj_count=1)
    return pkt + bytes(payload)


def build_keyframe_packet(seq_id, ts, guid_bytes, entries):
    """PT_Keyframe packet for transform (0-8) and visibility (9-10) channels.

    entries: list of (frame_int, value_float, channel_uint8)
    Header: sequence(4) + timestamp(8) + key_count(1) + flags(1) = 14 bytes
    Entry: object_guid(16) + frame(4) + value(4) + channel(1) = 25 bytes
    """
    hdr = struct.pack("<IdBB", seq_id, ts, len(entries), 0)
    body = bytearray()
    for frame, value, channel in entries:
        body.extend(guid_bytes)
        body.extend(struct.pack("<ifB", frame, value, channel))
    total_payload = len(hdr) + len(body)
    pkt = build_outer_header(PT_KEYFRAME, total_payload, obj_count=0)
    return pkt + hdr + bytes(body)


def send_all(sock, pkt):
    """Deterministic send — no queue buffering."""
    sent = 0
    while sent < len(pkt):
        n = sock.send(pkt[sent:])
        if n == 0:
            raise RuntimeError("Socket disconnected during send")
        sent += n


def _iter_action_fcurves_51(action, obj=None):
    """Iterate FCurves from action using Blender 5.1 channelbag-safe path.

    Yields (fcurve, slot_handle) for each keyframe-bearing FCurve.
    """
    if not hasattr(action, 'layers'):
        return
    for strip in action.layers.strips:
        try:
            channelbags = list(strip.channelbags)
        except Exception:
            continue
        for cbag in channelbags:
            ch_slot_handle = getattr(cbag, 'slot_handle', None)
            if ch_slot_handle is None:
                continue
            # If targeting a specific object, skip non-matching bags
            if obj is not None:
                target_handle = getattr(obj, '_data_slot_handle', None)
                if target_handle is not None and ch_slot_handle != target_handle:
                    continue
            try:
                fcurves = list(cbag.fcurves)
            except Exception:
                continue
            for fcurve in fcurves:
                yield (fcurve, ch_slot_handle)


def extract_object_fcurves(obj, addon_path):
    """Extract transform + visibility FCurves from a single Blender object.

    Uses the addon's _KEYFRAME_CHANNEL_MAP and _iter_action_fcurves_51.
    Returns (fcurves_extracted_list, channel_count).
    """
    add_sync_to_path(addon_path)
    from sync import _KEYFRAME_CHANNEL_MAP, _iter_action_fcurves_51

    fcurves_extracted = []

    if obj.animation_data and obj.animation_data.action:
        action = obj.animation_data.action
        for fcurve, _slot_handle in _iter_action_fcurves_51(action, obj=obj):
            data_path = getattr(fcurve, 'data_path', '')
            array_index = getattr(fcurve, 'array_index', 0)
            kf_points = getattr(fcurve, 'keyframe_points', [])
            channel_key = (data_path, array_index)
            channel_idx = _KEYFRAME_CHANNEL_MAP.get(channel_key)

            if channel_idx is not None:
                for kp in kf_points:
                    fcurves_extracted.append({
                        'frame': int(kp.co.x),
                        'value': kp.co.y,
                        'channel': channel_idx,
                        'data_path': data_path,
                        'axis_index': array_index,
                    })

    return fcurves_extracted


def main():
    version = get_blender_version()
    addon_path = get_addon_path()

    print("=" * 60)
    print("  Phase 7E Stage 10F — Multi-object Transform + Visibility")
    print("=" * 60)
    print(f"  Blender version: {version}")
    print(f"  Addon path: {addon_path}")
    print(f"  Target: {UE_HOST}:{UE_PORT}")
    print()

    if not addon_path:
        print("[ERROR] Addon path not found. Cannot continue.")
        sys.exit(1)

    guid_a = get_uuid_from_hex(GUID_A_HEX)
    guid_b = get_uuid_from_hex(GUID_B_HEX)
    guid_a_bytes = pack_fguid_full(guid_a)
    guid_b_bytes = pack_fguid_full(guid_b)

    # ---- Step 1: Create probe objects with transform + visibility keyframes ----
    print("--- Step 1a: Creating Object A with transform + visibility keyframes ---")

    # Create Object A (cube at origin)
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=(0, 0, 0))
    obj_a = bpy.context.active_object
    obj_a.name = OBJECT_A_NAME
    obj_a.data.name = OBJECT_A_NAME + "_Data"

    if obj_a.animation_data is None:
        obj_a.animation_data_create()
    if obj_a.animation_data.action is None:
        action_a = bpy.data.actions.new(name=f"{OBJECT_A_NAME}_Action")
        obj_a.animation_data.action = action_a

    # Object A transform deltas per frame
    transform_deltas_a = {
        1:  ((1.0, 0.0, 0.0), (0.0, 0.0, 0.0), (1.0, 1.0, 1.0)),
        10: ((2.0, 1.0, 0.5), (0.5, 0.25, 0.0), (2.0, 1.5, 1.0)),
        20: ((3.0, 2.0, 1.0), (1.0, 0.5, 0.25), (3.0, 2.0, 1.5)),
    }

    # Object A visibility deltas per frame (0=visible, 1=hidden)
    # Object A: hide_viewport toggles (visible at frame 1, hidden at 10/20)
    visibility_deltas_a = {
        1:  (0.0, 0.0),   # visible
        10: (1.0, 0.0),   # hidden in viewport
        20: (1.0, 1.0),   # hidden in viewport + render
    }

    # Insert Object A transform keyframes
    for frame in KEYFRAME_TIMES:
        loc_delta = transform_deltas_a[frame][0]
        for axis_idx, axis_name in enumerate(['X', 'Y', 'Z']):
            obj_a.keyframe_insert(data_path='location', frame=frame, index=axis_idx)
            val = loc_delta[axis_idx]
            action = obj_a.animation_data.action
            if hasattr(action, 'layers') and hasattr(action.layers, 'strips'):
                for strip in action.layers.strips:
                    for cbag in getattr(strip, 'channelbags', []):
                        for fc in getattr(cbag, 'fcurves', []):
                            if getattr(fc, 'data_path', '').startswith('location') and getattr(fc, 'array_index', -1) == axis_idx:
                                kf = fc.keyframe_points
                                kf[-1].co = (frame, val)
                                kf[-1].interpolation = 'LINEAR'
                                break
            print(f"  ObjA Loc{axis_name} frame={frame} value={val}")

    for frame in KEYFRAME_TIMES:
        rot_delta = transform_deltas_a[frame][1]
        for axis_idx, axis_name in enumerate(['X', 'Y', 'Z']):
            obj_a.keyframe_insert(data_path='rotation_euler', frame=frame, index=axis_idx)
            val = rot_delta[axis_idx]
            action = obj_a.animation_data.action
            if hasattr(action, 'layers') and hasattr(action.layers, 'strips'):
                for strip in action.layers.strips:
                    for cbag in getattr(strip, 'channelbags', []):
                        for fc in getattr(cbag, 'fcurves', []):
                            if getattr(fc, 'data_path', '').startswith('rotation_euler') and getattr(fc, 'array_index', -1) == axis_idx:
                                kf = fc.keyframe_points
                                kf[-1].co = (frame, val)
                                kf[-1].interpolation = 'LINEAR'
                                break
            print(f"  ObjA Rot{axis_name} frame={frame} value={val}")

    for frame in KEYFRAME_TIMES:
        scale_delta = transform_deltas_a[frame][2]
        for axis_idx, axis_name in enumerate(['X', 'Y', 'Z']):
            obj_a.keyframe_insert(data_path='scale', frame=frame, index=axis_idx)
            val = scale_delta[axis_idx]
            action = obj_a.animation_data.action
            if hasattr(action, 'layers') and hasattr(action.layers, 'strips'):
                for strip in action.layers.strips:
                    for cbag in getattr(strip, 'channelbags', []):
                        for fc in getattr(cbag, 'fcurves', []):
                            if getattr(fc, 'data_path', '').startswith('scale') and getattr(fc, 'array_index', -1) == axis_idx:
                                kf = fc.keyframe_points
                                kf[-1].co = (frame, val)
                                kf[-1].interpolation = 'LINEAR'
                                break
            print(f"  ObjA Scale{axis_name} frame={frame} value={val}")

    # Insert Object A visibility keyframes (boolean props, no index param)
    # keyframe_insert on a boolean property captures the CURRENT property value.
    # So we must temporarily set the property, keyframe_insert, then restore.
    for frame in KEYFRAME_TIMES:
        vp_val, render_val = visibility_deltas_a[frame]
        # Set hide_viewport value for this keyframe
        obj_a.hide_viewport = bool(vp_val)
        obj_a.keyframe_insert(data_path='hide_viewport', frame=frame)
        # Restore hide_viewport for subsequent frames
        obj_a.hide_viewport = False
        # Set hide_render value for this keyframe
        obj_a.hide_render = bool(render_val)
        obj_a.keyframe_insert(data_path='hide_render', frame=frame)
        # Restore hide_render for subsequent frames
        obj_a.hide_render = False
        print(f"  ObjA hide_viewport frame={frame} value={vp_val}")
        print(f"  ObjA hide_render frame={frame} value={render_val}")

    print(f"  Object A total: 9 transform keyframes + 6 visibility keyframes = 15")

    # ---- Step 1b: Create Object B with only transform keyframes ----
    print("\n--- Step 1b: Creating Object B with transform keyframes only ---")

    # Create Object B (sphere at offset)
    bpy.ops.mesh.primitive_uv_sphere_add(radius=0.5, location=(5, 0, 0))
    obj_b = bpy.context.active_object
    obj_b.name = OBJECT_B_NAME
    obj_b.data.name = OBJECT_B_NAME + "_Data"

    if obj_b.animation_data is None:
        obj_b.animation_data_create()
    if obj_b.animation_data.action is None:
        action_b = bpy.data.actions.new(name=f"{OBJECT_B_NAME}_Action")
        obj_b.animation_data.action = action_b

    # Object B transform deltas per frame (different values to distinguish from A)
    transform_deltas_b = {
        1:  ((0.0, 1.0, 0.0), (0.0, 0.0, 0.0), (1.5, 1.0, 1.0)),
        10: ((1.0, 2.0, 0.5), (0.0, 0.0, 0.5), (1.0, 2.0, 1.5)),
        20: ((0.5, 3.0, 1.0), (0.5, 0.0, 0.0), (2.0, 1.0, 1.0)),
    }

    for frame in KEYFRAME_TIMES:
        loc_delta = transform_deltas_b[frame][0]
        for axis_idx, axis_name in enumerate(['X', 'Y', 'Z']):
            obj_b.keyframe_insert(data_path='location', frame=frame, index=axis_idx)
            val = loc_delta[axis_idx]
            action = obj_b.animation_data.action
            if hasattr(action, 'layers') and hasattr(action.layers, 'strips'):
                for strip in action.layers.strips:
                    for cbag in getattr(strip, 'channelbags', []):
                        for fc in getattr(cbag, 'fcurves', []):
                            if getattr(fc, 'data_path', '').startswith('location') and getattr(fc, 'array_index', -1) == axis_idx:
                                kf = fc.keyframe_points
                                kf[-1].co = (frame, val)
                                kf[-1].interpolation = 'LINEAR'
                                break
            print(f"  ObjB Loc{axis_name} frame={frame} value={val}")

    for frame in KEYFRAME_TIMES:
        rot_delta = transform_deltas_b[frame][1]
        for axis_idx, axis_name in enumerate(['X', 'Y', 'Z']):
            obj_b.keyframe_insert(data_path='rotation_euler', frame=frame, index=axis_idx)
            val = rot_delta[axis_idx]
            action = obj_b.animation_data.action
            if hasattr(action, 'layers') and hasattr(action.layers, 'strips'):
                for strip in action.layers.strips:
                    for cbag in getattr(strip, 'channelbags', []):
                        for fc in getattr(cbag, 'fcurves', []):
                            if getattr(fc, 'data_path', '').startswith('rotation_euler') and getattr(fc, 'array_index', -1) == axis_idx:
                                kf = fc.keyframe_points
                                kf[-1].co = (frame, val)
                                kf[-1].interpolation = 'LINEAR'
                                break
            print(f"  ObjB Rot{axis_name} frame={frame} value={val}")

    for frame in KEYFRAME_TIMES:
        scale_delta = transform_deltas_b[frame][2]
        for axis_idx, axis_name in enumerate(['X', 'Y', 'Z']):
            obj_b.keyframe_insert(data_path='scale', frame=frame, index=axis_idx)
            val = scale_delta[axis_idx]
            action = obj_b.animation_data.action
            if hasattr(action, 'layers') and hasattr(action.layers, 'strips'):
                for strip in action.layers.strips:
                    for cbag in getattr(strip, 'channelbags', []):
                        for fc in getattr(cbag, 'fcurves', []):
                            if getattr(fc, 'data_path', '').startswith('scale') and getattr(fc, 'array_index', -1) == axis_idx:
                                kf = fc.keyframe_points
                                kf[-1].co = (frame, val)
                                kf[-1].interpolation = 'LINEAR'
                                break
            print(f"  ObjB Scale{axis_name} frame={frame} value={val}")

    print(f"  Object B total: 9 transform keyframes (no visibility)")

    # ---- Step 2: Extract FCurves via addon path ----
    print("\n--- Step 2: Extracting FCurves via addon path ---")

    # Extract Object A FCurves
    fcurves_a = extract_object_fcurves(obj_a, addon_path)
    channels_a = set(fc['channel'] for fc in fcurves_a)
    channels_a_transform = [c for c in channels_a if c <= 8]
    channels_a_visibility = [c for c in channels_a if c >= 9]
    print(f"  Object A: {len(fcurves_a)} FCurves extracted, channels={sorted(channels_a)}")
    print(f"    Transform channels: {sorted(channels_a_transform)}")
    print(f"    Visibility channels: {sorted(channels_a_visibility)}")

    # Extract Object B FCurves
    fcurves_b = extract_object_fcurves(obj_b, addon_path)
    channels_b = set(fc['channel'] for fc in fcurves_b)
    channels_b_transform = [c for c in channels_b if c <= 8]
    print(f"  Object B: {len(fcurves_b)} FCurves extracted, channels={sorted(channels_b)}")
    print(f"    Transform channels: {sorted(channels_b_transform)}")
    print(f"    Visibility channels: {sorted([c for c in channels_b if c >= 9]) or 'none'}")

    # Group by frame for each object
    keyframe_entries_a = {}
    for fc in fcurves_a:
        frame = fc['frame']
        if frame not in keyframe_entries_a:
            keyframe_entries_a[frame] = []
        keyframe_entries_a[frame].append((frame, fc['value'], fc['channel']))

    keyframe_entries_b = {}
    for fc in fcurves_b:
        frame = fc['frame']
        if frame not in keyframe_entries_b:
            keyframe_entries_b[frame] = []
        keyframe_entries_b[frame].append((frame, fc['value'], fc['channel']))

    total_keys_a = sum(len(e) for e in keyframe_entries_a.values())
    total_keys_b = sum(len(e) for e in keyframe_entries_b.values())
    print(f"\n  Object A: {total_keys_a} key entries across frames {sorted(keyframe_entries_a.keys())}")
    print(f"  Object B: {total_keys_b} key entries across frames {sorted(keyframe_entries_b.keys())}")

    # ---- Step 3: Connect to UE and send packets ----
    print("\n--- Step 3: Connecting to UE and sending packets ---")

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(5.0)

    try:
        sock.connect((UE_HOST, UE_PORT))
        print(f"  Connected to {UE_HOST}:{UE_PORT}")
    except Exception as e:
        print(f"  [WARN] Connection to UE failed: {e}")
        print("  Sending packets to /dev/null instead (harness test mode)")
        sock = None

    ts = time.time()
    packet_count = 0

    # 1. CREATE_SEQUENCE
    pkt = build_sequencer_op_create_sequence(_NEXT_PACKET_SEQ, ts)
    if sock:
        send_all(sock, pkt)
        print(f"  [1] Sent CREATE_SEQUENCE (seq={_NEXT_PACKET_SEQ - 1})")
    packet_count += 1

    # 2. CREATE Object A
    pkt = build_create_object(guid_a_bytes, loc=(0, 0, 200), rot=(0, 0, 0, 1), scale=(1, 1, 1))
    if sock:
        send_all(sock, pkt)
        print(f"  [2] Sent CREATE Object A (seq={_NEXT_PACKET_SEQ - 1})")
    packet_count += 1

    # 3. CREATE Object B
    pkt = build_create_object(guid_b_bytes, loc=(5, 0, 150), rot=(0, 0, 0, 1), scale=(1, 1, 1))
    if sock:
        send_all(sock, pkt)
        print(f"  [3] Sent CREATE Object B (seq={_NEXT_PACKET_SEQ - 1})")
    packet_count += 1

    # 4. ADD_POSSESSABLE Object A
    pkt = build_sequencer_op_add_possessable(_NEXT_PACKET_SEQ, ts, guid_a_bytes)
    if sock:
        send_all(sock, pkt)
        print(f"  [4] Sent ADD_POSSESSABLE Object A (seq={_NEXT_PACKET_SEQ - 1})")
    packet_count += 1

    # 5. ADD_POSSESSABLE Object B
    pkt = build_sequencer_op_add_possessable(_NEXT_PACKET_SEQ, ts, guid_b_bytes)
    if sock:
        send_all(sock, pkt)
        print(f"  [5] Sent ADD_POSSESSABLE Object B (seq={_NEXT_PACKET_SEQ - 1})")
    packet_count += 1

    # 6. PT_Keyframe packets for Object A (transform + visibility)
    print("\n  Sending Object A keyframes:")
    for frame in sorted(keyframe_entries_a.keys()):
        entries = keyframe_entries_a[frame]
        pkt = build_keyframe_packet(_NEXT_PACKET_SEQ, ts, guid_a_bytes, entries)
        if sock:
            send_all(sock, pkt)
            entry_channels = [e[2] for e in entries]
            print(f"  [{packet_count+1}] Sent PT_Keyframe frame={frame} keys={len(entries)} channels={entry_channels} (seq={_NEXT_PACKET_SEQ - 1})")
        packet_count += 1

    # 7. PT_Keyframe packets for Object B (transform only)
    print("\n  Sending Object B keyframes:")
    for frame in sorted(keyframe_entries_b.keys()):
        entries = keyframe_entries_b[frame]
        pkt = build_keyframe_packet(_NEXT_PACKET_SEQ, ts, guid_b_bytes, entries)
        if sock:
            send_all(sock, pkt)
            entry_channels = [e[2] for e in entries]
            print(f"  [{packet_count+1}] Sent PT_Keyframe frame={frame} keys={len(entries)} channels={entry_channels} (seq={_NEXT_PACKET_SEQ - 1})")
        packet_count += 1

    sock.close()
    print(f"\n  Total packets sent: {packet_count}")
    print("\n--- Stage 10F Runtime Complete ---")

    # ---- Step 4: Print expected UE markers ----
    total_keys = total_keys_a + total_keys_b
    print("\nExpected UE markers to verify:")
    print(f"  Object A transform keys applied:    >= {total_keys_a - len([c for c in channels_a if c >= 9])}")
    print(f"  Object A visibility keys applied:   >= {total_keys_a - sum(1 for c in channels_a if c <= 8)}")
    print(f"  Object B transform keys applied:    >= {total_keys_b}")
    print(f"  [KEYFRAME] missing_binding = 0")
    print(f"  [KEYFRAME] unsupported_channel = 0")
    print(f"  Signal11 = 0")
    print(f"  Signal6  = 0")
    print(f"  SceneOutliner crash = 0")
    print(f"  DrawFrustum crash = 0")

    return {
        'total_packets': packet_count,
        'total_keys': total_keys,
        'keys_a': total_keys_a,
        'keys_b': total_keys_b,
        'channels_a': sorted(channels_a),
        'channels_b': sorted(channels_b),
    }


if __name__ == '__main__':
    main()
