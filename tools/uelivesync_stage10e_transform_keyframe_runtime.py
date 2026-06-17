#!/usr/bin/env python3
"""
Phase 7E Stage 10E — Full Transform Keyframe E2E Runtime Validation.

Creates a probe object with transform keyframes (location/rotation/scale),
extracts them via the real addon's FCurve extraction path, then sends all
packets via **direct raw TCP socket** (bypassing the addon's _send_queue
for deterministic ordering).

Required UE markers:
    [KEYFRAME][TRACK_CREATE]  >= 1    (UMovieScene3DTransformTrack created)
    [KEYFRAME][SECTION_CREATE]>= 1    (UMovieScene3DTransformSection created)
    [KEYFRAME][KEY]           >= 24   (transform keys: 3 axes × 3 frames × 3 transforms)
    [KEYFRAME][APPLY]         >= 24   (keys applied to transform channels)
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
OBJECT_NAME = "Stage10E_TransformProbe"
GUID_HEX = "e1f2a3b4c5d60718293a4b5c6d7e8f90"  # stable test GUID
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
_NEXT_PACKET_SEQ = 2000


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
    """PT_Keyframe packet for transform channels 0-8.

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


def main():
    version = get_blender_version()
    addon_path = get_addon_path()

    print("=" * 60)
    print("  Phase 7E Stage 10E — Blender Transform Keyframe Runtime")
    print("=" * 60)
    print(f"  Blender version: {version}")
    print(f"  Addon path: {addon_path}")
    print(f"  Target: {UE_HOST}:{UE_PORT}")
    print()

    if not addon_path:
        print("[ERROR] Addon path not found. Cannot continue.")
        sys.exit(1)

    # Import addon extraction helpers
    add_sync_to_path(addon_path)
    from sync import _KEYFRAME_CHANNEL_MAP, _iter_action_fcurves_51

    guid_obj = get_uuid_from_hex(GUID_HEX)
    guid_bytes = pack_fguid_full(guid_obj)

    # ---- Step 1: Create probe object with transform keyframes ----
    print("--- Step 1: Creating probe object with transform keyframes ---")

    # Create object
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=(0, 0, 0))
    obj = bpy.context.active_object
    obj.name = OBJECT_NAME
    obj.data.name = OBJECT_NAME + "_Data"

    # Blender 5.1+: must create action first before keyframing
    if obj.animation_data is None:
        obj.animation_data_create()
    if obj.animation_data.action is None:
        action = bpy.data.actions.new(name=f"{OBJECT_NAME}_Action")
        obj.animation_data.action = action

    # Set keyframe times
    frames = KEYFRAME_TIMES

    # Define transform deltas per frame (relative to default cube)
    # Each frame: (loc_delta, rot_euler_delta, scale_delta)
    transform_deltas = {
        1:  ((1.0, 0.0, 0.0), (0.0, 0.0, 0.0), (1.0, 1.0, 1.0)),
        10: ((2.0, 1.0, 0.5), (0.5, 0.25, 0.0), (2.0, 1.5, 1.0)),
        20: ((3.0, 2.0, 1.0), (1.0, 0.5, 0.25), (3.0, 2.0, 1.5)),
    }

    # Insert location keyframes (channels 0, 1, 2)
    for frame in frames:
        loc_delta = transform_deltas[frame][0]
        for axis_idx, axis_name in enumerate(['X', 'Y', 'Z']):
            obj.keyframe_insert(data_path='location', frame=frame, index=axis_idx)
            val = loc_delta[axis_idx]
            # Blender 5.1+: access keyframe via action layer strips
            action = obj.animation_data.action
            if hasattr(action, 'layers') and hasattr(action.layers, 'strips'):
                for strip in action.layers.strips:
                    for cbag in getattr(strip, 'channelbags', []):
                        for fc in getattr(cbag, 'fcurves', []):
                            if getattr(fc, 'data_path', '').startswith('location') and getattr(fc, 'array_index', -1) == axis_idx:
                                kf = fc.keyframe_points
                                kf[-1].co = (frame, val)
                                kf[-1].interpolation = 'LINEAR'
                                break
            print(f"  Loc{axis_name} frame={frame} value={val}")

    # Insert rotation keyframes (channels 3, 4, 5) — Euler
    for frame in frames:
        rot_delta = transform_deltas[frame][1]
        for axis_idx, axis_name in enumerate(['X', 'Y', 'Z']):
            obj.keyframe_insert(data_path='rotation_euler', frame=frame, index=axis_idx)
            val = rot_delta[axis_idx]
            action = obj.animation_data.action
            if hasattr(action, 'layers') and hasattr(action.layers, 'strips'):
                for strip in action.layers.strips:
                    for cbag in getattr(strip, 'channelbags', []):
                        for fc in getattr(cbag, 'fcurves', []):
                            if getattr(fc, 'data_path', '').startswith('rotation_euler') and getattr(fc, 'array_index', -1) == axis_idx:
                                kf = fc.keyframe_points
                                kf[-1].co = (frame, val)
                                kf[-1].interpolation = 'LINEAR'
                                break
            print(f"  Rot{axis_name} frame={frame} value={val}")

    # Insert scale keyframes (channels 6, 7, 8)
    for frame in frames:
        scale_delta = transform_deltas[frame][2]
        for axis_idx, axis_name in enumerate(['X', 'Y', 'Z']):
            obj.keyframe_insert(data_path='scale', frame=frame, index=axis_idx)
            val = scale_delta[axis_idx]
            action = obj.animation_data.action
            if hasattr(action, 'layers') and hasattr(action.layers, 'strips'):
                for strip in action.layers.strips:
                    for cbag in getattr(strip, 'channelbags', []):
                        for fc in getattr(cbag, 'fcurves', []):
                            if getattr(fc, 'data_path', '').startswith('scale') and getattr(fc, 'array_index', -1) == axis_idx:
                                kf = fc.keyframe_points
                                kf[-1].co = (frame, val)
                                kf[-1].interpolation = 'LINEAR'
                                break
            print(f"  Scale{axis_name} frame={frame} value={val}")

    print(f"  Total keyframes inserted: {len(frames) * 9} (9 axes × {len(frames)} frames)")

    # ---- Step 2: Extract FCurves via addon path ----
    print("\n--- Step 2: Extracting FCurves via addon path ---")

    fcurves_extracted = []

    if obj.animation_data and obj.animation_data.action:
        action = obj.animation_data.action
        channelbags_map = {}

        for fcurve, slot_handle in _iter_action_fcurves_51(action, obj=obj):
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

    else:
        # Fallback: direct iteration (Blender < 5.1 compatibility)
        if hasattr(obj, 'animation_data') and obj.animation_data and obj.animation_data.fcurves:
            for fcurve in obj.animation_data.fcurves:
                data_path = fcurve.data_path
                array_index = fcurve.array_index
                channel_key = (data_path, array_index)
                channel_idx = _KEYFRAME_CHANNEL_MAP.get(channel_key)

                if channel_idx is not None:
                    for kp in fcurve.keyframe_points:
                        fcurves_extracted.append({
                            'frame': int(kp.co.x),
                            'value': kp.co.y,
                            'channel': channel_idx,
                            'data_path': data_path,
                            'axis_index': array_index,
                        })

    # Group by frame for packet batching
    keyframe_entries_by_frame = {}
    for fc in fcurves_extracted:
        frame = fc['frame']
        if frame not in keyframe_entries_by_frame:
            keyframe_entries_by_frame[frame] = []
        keyframe_entries_by_frame[frame].append((frame, fc['value'], fc['channel']))

    print(f"  FCurves extracted: {len(fcurves_extracted)}")
    print(f"  Frames with keys: {sorted(keyframe_entries_by_frame.keys())}")
    for frame in sorted(keyframe_entries_by_frame.keys()):
        entries = keyframe_entries_by_frame[frame]
        channels = [e[2] for e in entries]
        print(f"    Frame {frame}: channels {channels}")

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

    # Packet order: CREATE_SEQUENCE → CREATE → ADD_POSSESSABLE → PT_Keyframe
    ts = time.time()

    # 1. CREATE_SEQUENCE
    pkt1 = build_sequencer_op_create_sequence(_NEXT_PACKET_SEQ, ts)
    if sock:
        send_all(sock, pkt1)
        print(f"  Sent CREATE_SEQUENCE (seq={_NEXT_PACKET_SEQ - 1})")

    # 2. CREATE (with FQuat rotation)
    pkt2 = build_create_object(guid_bytes, loc=(0, 0, 200), rot=(0, 0, 0, 1), scale=(1, 1, 1))
    if sock:
        send_all(sock, pkt2)
        print(f"  Sent CREATE (seq={_NEXT_PACKET_SEQ - 1})")

    # 3. ADD_POSSESSABLE
    pkt3 = build_sequencer_op_add_possessable(_NEXT_PACKET_SEQ, ts, guid_bytes)
    if sock:
        send_all(sock, pkt3)
        print(f"  Sent ADD_POSSESSABLE (seq={_NEXT_PACKET_SEQ - 1})")

    # 4. PT_Keyframe packets (one per frame)
    for frame in sorted(keyframe_entries_by_frame.keys()):
        entries = keyframe_entries_by_frame[frame]
        pkt = build_keyframe_packet(_NEXT_PACKET_SEQ, ts, guid_bytes, entries)
        if sock:
            send_all(sock, pkt)
            print(f"  Sent PT_Keyframe frame={frame} keys={len(entries)} (seq={_NEXT_PACKET_SEQ - 1})")

    sock.close()
    print("\n--- Stage 10E Runtime Complete ---")

    # ---- Step 4: Print expected UE markers ----
    total_keys = sum(len(e) for e in keyframe_entries_by_frame.values())
    print("\nExpected UE markers to verify:")
    print(f"  [KEYFRAME][TRACK_CREATE]  >= 1")
    print(f"  [KEYFRAME][SECTION_CREATE]>= 1")
    print(f"  [KEYFRAME][KEY]           >= {total_keys}")
    print(f"  [KEYFRAME][APPLY]         >= {total_keys}")
    print(f"  [KEYFRAME] missing_binding = 0")
    print(f"  [KEYFRAME] unsupported_channel = 0")
    print(f"  Signal11 = 0")
    print(f"  Signal6  = 0")
    print(f"  SceneOutliner crash = 0")
    print(f"  DrawFrustum crash = 0")

    return total_keys


if __name__ == '__main__':
    main()
