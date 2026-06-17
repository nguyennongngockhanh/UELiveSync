#!/usr/bin/env python3
"""
Phase 7E Stage 10A.5 — Real Blender Background Runtime Automation for Visibility BoolTrack.

Creates a probe object with visibility keyframes, extracts them via the
real addon's FCurve extraction path, then sends all packets via **direct
raw TCP socket** (bypassing the addon's _send_queue for deterministic
ordering).

Required UE markers:
    [KEYFRAME][BOOL_TRACK_CREATE]   >= 1
    [KEYFRAME][BOOL_SECTION_CREATE] >= 1
    [KEYFRAME][BOOL_KEY]            >= 6
    [KEYFRAME][BOOL_APPLY]          >= 6
    Signal11 = 0
    Signal6  = 0
    SceneOutliner = 0
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
OBJECT_NAME = "Stage10A5_VisibilityProbe"
GUID_HEX = "a1b2c3d4e5f60718293a4b5c6d7e8f90"  # stable test GUID
KEYFRAME_TIMES = [1, 10, 20]

# ---- Protocol Constants ----
LIVE_SYNC_MAGIC    = 0x4C56534D
LIVE_SYNC_VERSION  = 5

PT_CREATE          = 0x03
PT_KEYFRAME        = 0x17
PT_SEQUENCER_OP    = 0x18

SEQUENCER_OP_CREATE_SEQUENCE = 0
SEQUENCER_OP_ADD_POSSESSABLE = 1

LSP_STATIC = 0x01  # Primitive

CHANNEL_HIDE_VIEWPORT = 9
CHANNEL_HIDE_RENDER   = 10


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
    d_a = g.time_low
    d_b = (g.time_mid << 16) | g.time_hi_version
    d_c = (g.clock_seq_hi_variant << 24
           | g.clock_seq_low << 16
           | (g.node >> 32) & 0xFFFF)
    d_d = g.node & 0xFFFFFFFF
    return struct.pack("<IIII", d_a, d_b, d_c, d_d)


# Global packet sequence counter — each packet gets a unique increasing seq
_NEXT_PACKET_SEQ = 1000

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
    """FSequencerOpHeader + CREATE_SEQUENCE opcode payload."""
    # FSequencerOpHeader: opcode(1) + flags(1) + reserved(2) + sequence(4) + timestamp(8)
    seq_hdr = struct.pack("<BBHId",
                          SEQUENCER_OP_CREATE_SEQUENCE, 0, 0,
                          seq_id, ts)
    # Opcode payload: frame_start(4) + frame_end(4) + fps_num(4) + fps_den(4)
    op_payload = struct.pack("<IIII", 1, 20, 24, 1)
    total_payload = len(seq_hdr) + len(op_payload)
    pkt = build_outer_header(PT_SEQUENCER_OP, total_payload, obj_count=0)
    return pkt + seq_hdr + op_payload


def build_sequencer_op_add_possessable(seq_id, ts, guid_bytes):
    """FSequencerOpHeader + ADD_POSSESSABLE opcode payload (guid + binding_type)."""
    seq_hdr = struct.pack("<BBHId",
                          SEQUENCER_OP_ADD_POSSESSABLE, 0, 0,
                          seq_id, ts)
    # Opcode payload: object_guid(16) + binding_type(1)
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
      [64]  Parent GUID:    16 bytes (4×uint32)
      [80]  PrimitiveType:  1 byte (uint8)
    """
    payload = bytearray()
    payload.extend(guid_bytes)
    payload.extend(struct.pack("<fff", *loc))
    payload.extend(struct.pack("<ffff", *rot))  # FQuat: 4 floats
    payload.extend(struct.pack("<fff", *scale))
    payload.extend(struct.pack("<d", time.time()))
    payload.extend(b'\x00' * 16)  # no parent (uint128)
    payload.append(prim_type)
    pkt = build_outer_header(PT_CREATE, len(payload), obj_count=1)
    return pkt + bytes(payload)


def build_keyframe_packet(seq_id, ts, guid_bytes, entries):
    """
    PT_Keyframe packet.
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
    print("  Phase 7E Stage 10A.5 — Blender Visibility Runtime")
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

    guid_obj = uuid.UUID(GUID_HEX)
    guid_bytes = pack_fguid_full(guid_obj)
    print(f"  GUID = {guid_obj}")
    print()

    # ---- Phase A: Create probe object with keyframes in Blender ----
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for coll in list(bpy.data.collections):
        bpy.data.collections.remove(coll)
    bpy.data.collections.new("Collection")

    bpy.ops.mesh.primitive_cube_add(size=1, location=(0, 0, 0))
    probe = bpy.context.active_object
    probe.name = OBJECT_NAME
    probe.data.name = OBJECT_NAME + "_Mesh"

    if "ue_guid" not in probe:
        probe["ue_guid"] = GUID_HEX

    # Insert visibility keyframes
    probe.hide_viewport = False
    probe.hide_render = False
    probe.keyframe_insert(data_path="hide_viewport", frame=1)
    probe.keyframe_insert(data_path="hide_render", frame=1)

    probe.hide_viewport = True
    probe.hide_render = True
    probe.keyframe_insert(data_path="hide_viewport", frame=10)
    probe.keyframe_insert(data_path="hide_render", frame=10)

    probe.hide_viewport = False
    probe.hide_render = False
    probe.keyframe_insert(data_path="hide_viewport", frame=20)
    probe.keyframe_insert(data_path="hide_render", frame=20)

    print(f"  Object: {probe.name}")
    print()

    # ---- Phase B: Discover FCurves using real addon Blender 5.1 path ----
    discovered_fcurves = []
    if probe.animation_data and probe.animation_data.action:
        action = probe.animation_data.action
        if getattr(action, 'is_action_layered', False):
            for fc, _slot in _iter_action_fcurves_51(action):
                if fc.data_path in ("hide_viewport", "hide_render"):
                    discovered_fcurves.append((fc.data_path, fc))
        elif hasattr(action, 'fcurves'):
            for fc in action.fcurves:
                if fc.data_path in ("hide_viewport", "hide_render"):
                    discovered_fcurves.append((fc.data_path, fc))
    else:
        print("  [WARN] No animation_data.action on probe object")

    print("  Discovered FCurves:")
    for name, fc in discovered_fcurves:
        print(f"    {name}: {len(fc.keyframe_points)} keyframes")
    print()

    # ---- Phase C: Extract keyframes using real addon extraction path ----
    kf_entries = []
    ch9_count = 0
    ch10_count = 0

    for fname, fc in discovered_fcurves:
        channel_idx = _KEYFRAME_CHANNEL_MAP.get((fname, fc.array_index), -1)
        if channel_idx == -1:
            print(f"    [WARN] {fname} not in _KEYFRAME_CHANNEL_MAP")
            continue
        for kp in fc.keyframe_points:
            frame = round(kp.co.x)
            value = kp.co.y
            kf_entries.append((frame, value, channel_idx))
            if channel_idx == CHANNEL_HIDE_VIEWPORT:
                ch9_count += 1
            elif channel_idx == CHANNEL_HIDE_RENDER:
                ch10_count += 1

    print(f"  Extracted: {len(kf_entries)} keyframes")
    print(f"  Channel 9 (hide_viewport): {ch9_count}")
    print(f"  Channel 10 (hide_render): {ch10_count}")
    print()

    # ---- Phase D: Direct raw TCP socket transport ----
    # Bypass addon _send_queue — use deterministic raw socket for ordering.
    print("  Phase D: Direct raw TCP socket transport...")
    sock = None
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5)
        sock.connect((UE_HOST, UE_PORT))
        sock.settimeout(None)
        print(f"  Connected to {UE_HOST}:{UE_PORT}")

        ts = time.time()

        # 1. CREATE_SEQUENCE (seq=1001)
        pkt_seq = build_sequencer_op_create_sequence(seq_id=1001, ts=ts)
        send_all(sock, pkt_seq)
        print(f"  Sent CREATE_SEQUENCE: frames 1-20, 24fps")
        time.sleep(0.25)

        # 2. CREATE actor — must come before ADD_POSSESSABLE so the
        #    actor exists when the addon resolves the possessable binding.
        pkt_create = build_create_object(guid_bytes, loc=(0.0, 0.0, 200.0))
        send_all(sock, pkt_create)
        print(f"  Sent CREATE: {OBJECT_NAME}")
        time.sleep(0.25)

        # 3. ADD_POSSESSABLE — actor must exist so FindActorFast succeeds.
        pkt_poss = build_sequencer_op_add_possessable(seq_id=2001, ts=ts,
                                                        guid_bytes=guid_bytes)
        send_all(sock, pkt_poss)
        print(f"  Sent ADD_POSSESSABLE: guid={GUID_HEX[:16]}...")
        time.sleep(0.5)

        # 4. PT_Keyframe — channel 9 (hide_viewport)
        entries_ch9 = [(f, v, CHANNEL_HIDE_VIEWPORT) for f, v, ch in kf_entries
                       if ch == CHANNEL_HIDE_VIEWPORT]
        if entries_ch9:
            pkt_ch9 = build_keyframe_packet(seq_id=3001, ts=ts,
                                            guid_bytes=guid_bytes,
                                            entries=entries_ch9)
            send_all(sock, pkt_ch9)
            print(f"  Sent PT_Keyframe ch9: {len(entries_ch9)} entries")

        # 5. PT_Keyframe — channel 10 (hide_render)
        entries_ch10 = [(f, v, CHANNEL_HIDE_RENDER) for f, v, ch in kf_entries
                        if ch == CHANNEL_HIDE_RENDER]
        if entries_ch10:
            pkt_ch10 = build_keyframe_packet(seq_id=3002, ts=ts,
                                              guid_bytes=guid_bytes,
                                              entries=entries_ch10)
            send_all(sock, pkt_ch10)
            print(f"  Sent PT_Keyframe ch10: {len(entries_ch10)} entries")

        total_keys = len(entries_ch9) + len(entries_ch10)
        print()
        print(f"  All packets sent. Waiting 3s for game thread...")
        time.sleep(3.0)
        sock.close()
        sock = None
        print(f"  Total bool keys: {total_keys}")

    except Exception as e:
        print(f"  [ERROR] TCP phase failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        if sock:
            try:
                sock.close()
            except:
                pass

    # ---- Summary ----
    print()
    print("=" * 60)
    print("  Stage 10A.5 Blender side COMPLETE")
    print("  UE markers to verify:")
    print("    [KEYFRAME][BOOL_TRACK_CREATE]   >= 1")
    print("    [KEYFRAME][BOOL_SECTION_CREATE] >= 1")
    print("    [KEYFRAME][BOOL_KEY]            >= 6")
    print("    [KEYFRAME][BOOL_APPLY]          >= 6")
    print("=" * 60)


if __name__ == "__main__":
    main()
