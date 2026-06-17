#!/usr/bin/env python3
"""Stage 10A.3 — BoolTrack Runtime Smoke Injector.

Sends PT_Keyframe packets with channels 9 and 10 (visibility bool)
to a running UE editor. Requires actor creation + Sequencer setup
via existing PT_Create and PT_SequencerOp packets.

Expected markers:
  [KEYFRAME][BOOL_TRACK_CREATE]  — when a bool track is created
  [KEYFRAME][BOOL_SECTION_CREATE] — when a bool section is created
  [KEYFRAME][BOOL_KEY]           — when a bool key is added
  [KEYFRAME][BOOL_APPLY]         — when a bool keyframe is applied
  [KEYFRAME][BOOL_UNSUPPORTED]   — when channel > 10 is sent
"""

import struct
import socket
import time
import uuid
import sys

UE_HOST = "127.0.0.1"
UE_PORT = 57000
LIVE_SYNC_MAGIC    = 0x4C56534D
LIVE_SYNC_VERSION  = 5

PT_CREATE       = 0x03
PT_KEYFRAME     = 0x17
PT_SEQUENCER_OP = 0x18

SEQUENCER_OP_CREATE_SEQUENCE = 0
SEQUENCER_OP_ADD_POSSESSABLE = 1

LSP_STATIC = 0x01

CHANNEL_HIDE_VIEWPORT = 9
CHANNEL_HIDE_RENDER   = 10
CHANNEL_UNSUPPORTED   = 99

_seq = 0


def pack_fguid(g):
    return struct.pack("<IIII", g.time_low, g.time_mid, g.time_hi_version,
                       g.clock_seq_hi_variant << 24 | g.clock_seq_low << 16 | (g.node >> 32) & 0xFFFF,
                       g.node & 0xFFFFFFFF)


def pack_fguid_full(g):
    d_a = g.time_low
    d_b = (g.time_mid << 16) | g.time_hi_version
    d_c = (g.clock_seq_hi_variant << 24
           | g.clock_seq_low << 16
           | (g.node >> 32) & 0xFFFF)
    d_d = g.node & 0xFFFFFFFF
    return struct.pack("<IIII", d_a, d_b, d_c, d_d)


def build_outer_header(ptype, payload_len, obj_count=1):
    global _seq
    _seq += 1
    header_size = 24
    packet_size = header_size + payload_len
    return struct.pack("<I H B B Q I I",
                       LIVE_SYNC_MAGIC, LIVE_SYNC_VERSION, ptype, 0,
                       _seq, packet_size, obj_count)


def build_create_object(guid_obj, primitive_type):
    transform = (0.0, 0.0, 200.0, 0.0, 0.0, 0.0, 1.0, 1.0, 1.0, 1.0)
    payload = bytearray()
    payload.extend(pack_fguid_full(guid_obj))
    payload.extend(struct.pack("<fff", transform[0], transform[1], transform[2]))
    payload.extend(struct.pack("<ffff", transform[3], transform[4], transform[5], transform[6]))
    payload.extend(struct.pack("<fff", transform[7], transform[8], transform[9]))
    payload.extend(struct.pack("<d", time.time()))
    payload.extend(b'\x00' * 16)  # no parent
    payload.append(primitive_type)
    pkt = build_outer_header(PT_CREATE, len(payload), obj_count=1)
    return pkt + bytes(payload)


def build_sequencer_op_create_sequence():
    hdr = struct.pack("<BBHId", SEQUENCER_OP_CREATE_SEQUENCE, 0, 0,
                      _seq + 1000, time.time())
    payload = struct.pack("<iiii", 0, 100, 30, 1)  # frame_start, end, fps_num, fps_den
    pkt = build_outer_header(PT_SEQUENCER_OP, len(hdr) + len(payload), obj_count=0)
    return pkt + hdr + payload


def build_sequencer_op_add_possessable(guid_obj):
    hdr = struct.pack("<BBHId", SEQUENCER_OP_ADD_POSSESSABLE, 0, 0,
                      _seq + 1000, time.time())
    payload = pack_fguid_full(guid_obj) + struct.pack("<B", 1)  # BindingType=1 (actor)
    pkt = build_outer_header(PT_SEQUENCER_OP, len(hdr) + len(payload), obj_count=0)
    return pkt + hdr + payload


def build_keyframe_packet(guid_bytes, entries):
    """entries: list of (frame_int, value_float, channel_uint8)"""
    hdr = struct.pack("<IdBB", _seq + 2000, time.time(), len(entries), 0)
    body = bytearray()
    for frame, value, channel in entries:
        body.extend(guid_bytes)
        body.extend(struct.pack("<ifB", frame, value, channel))
    pkt = build_outer_header(PT_KEYFRAME, len(hdr) + len(body), obj_count=0)
    return pkt + hdr + bytes(body)


def send_packet(sock, label, pkt):
    print(f"  [{label}] sending {len(pkt)} bytes")
    sock.sendall(pkt)
    time.sleep(0.15)


def main():
    guid_obj = uuid.uuid4()
    guid_bytes = pack_fguid_full(guid_obj)
    print(f"BoolTrack Runtime Smoke")
    print(f"  GUID = {guid_obj}")
    print()

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(5)
    try:
        sock.connect((UE_HOST, UE_PORT))
    except Exception as e:
        print(f"  FAIL: connect to {UE_HOST}:{UE_PORT}: {e}")
        sys.exit(1)
    sock.settimeout(None)
    print(f"  Connected to {UE_HOST}:{UE_PORT}")
    print()

    # Step 1: Create the level sequence
    send_packet(sock, "CREATE_SEQUENCE", build_sequencer_op_create_sequence())

    # Step 2: Create the static actor
    send_packet(sock, "CREATE_ACTOR", build_create_object(guid_obj, LSP_STATIC))

    # Step 3: Add possessable binding
    send_packet(sock, "ADD_POSSESSABLE", build_sequencer_op_add_possessable(guid_obj))

    # Step 4: Send bool keyframes — channel 9 (hide_viewport)
    bool_entries = [
        (1,  1.0, CHANNEL_HIDE_VIEWPORT),
        (10, 0.0, CHANNEL_HIDE_VIEWPORT),
        (20, 1.0, CHANNEL_HIDE_VIEWPORT),
    ]
    send_packet(sock, "BOOL_CH9", build_keyframe_packet(guid_bytes, bool_entries))

    # Step 5: Send bool keyframes — channel 10 (hide_render)
    bool_entries2 = [
        (1,  1.0, CHANNEL_HIDE_RENDER),
        (10, 0.0, CHANNEL_HIDE_RENDER),
        (20, 1.0, CHANNEL_HIDE_RENDER),
    ]
    send_packet(sock, "BOOL_CH10", build_keyframe_packet(guid_bytes, bool_entries2))

    # Step 6: Send an unsupported channel to verify it's handled safely
    unsupported_entries = [
        (1,  1.0, CHANNEL_UNSUPPORTED),
    ]
    send_packet(sock, "BOOL_UNSUPPORTED", build_keyframe_packet(guid_bytes, unsupported_entries))

    print()
    print("  All packets sent. Waiting 3s for game thread to process...")
    time.sleep(3.0)

    sock.close()
    print()
    print("  Done. Check UE log for expected markers:")
    print("    [KEYFRAME][BOOL_TRACK_CREATE]")
    print("    [KEYFRAME][BOOL_SECTION_CREATE]")
    print("    [KEYFRAME][BOOL_KEY]")
    print("    [KEYFRAME][BOOL_APPLY]")
    print("    [KEYFRAME][BOOL_UNSUPPORTED]")
    print()
    print("RESULT: INJECTOR_COMPLETE")


if __name__ == "__main__":
    main()
