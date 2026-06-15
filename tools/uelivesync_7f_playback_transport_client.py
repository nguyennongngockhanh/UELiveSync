#!/usr/bin/env python3
"""Phase 7F Stage 2 — Playback Transport TCP Injector.

Sends PT_PlaybackTransport (0x1A) and optionally the full
Sequencer flow to validate playback transport command apply in UE.
"""

import struct
import socket
import time
import sys
import uuid

LIVE_SYNC_MAGIC = 0x4C56534D
PT_PLAYBACK_TRANSPORT = 0x1A
PT_TIMELINE_STATE = 0x19
PT_SEQUENCER_OP = 0x18
PT_CREATE = 0x03
PT_TRANSFORM = 0x01
PT_KEYFRAME = 0x17
LIVE_SYNC_VERSION_V4 = 4
LIVE_SYNC_VERSION_V5 = 5

SEQUENCER_OP_CREATE_SEQUENCE = 0
SEQUENCER_OP_ADD_POSSESSABLE = 1

PLAYBACK_TRANSPORT_SET_FRAME = 0
PLAYBACK_TRANSPORT_PLAY = 1
PLAYBACK_TRANSPORT_PAUSE = 2
PLAYBACK_TRANSPORT_STOP = 3

UE_HOST = "127.0.0.1"
UE_PORT = 57000
_seq_counter = 0


def pack_ue_fguid(guid_obj):
    d_a = guid_obj.time_low
    d_b = (guid_obj.time_mid << 16) | guid_obj.time_hi_version
    d_c = (guid_obj.clock_seq_hi_variant << 24
           | guid_obj.clock_seq_low << 16
           | (guid_obj.node >> 32) & 0xFFFF)
    d_d = guid_obj.node & 0xFFFFFFFF
    return struct.pack("<IIII", d_a, d_b, d_c, d_d)


def build_packet(ptype, payload, version=LIVE_SYNC_VERSION_V4, flags=0, obj_count=1):
    global _seq_counter
    _seq_counter += 1
    seq = _seq_counter
    header_size = 24
    packet_size = header_size + len(payload)
    header = struct.pack('<I H B B Q I I',
                         LIVE_SYNC_MAGIC, version, ptype, flags, seq, packet_size, obj_count)
    return header + payload


def build_sequencer_op_common(opcode, sequence, timestamp, flags=0):
    return struct.pack(
        "<BBHI d",
        opcode & 0xFF,
        flags & 0xFF,
        0,
        sequence & 0xFFFFFFFF,
        timestamp,
    )


def send_playback_transport(sock, command, frame_current, flags=0):
    """Send PT_PlaybackTransport (0x1A) — 6-byte payload with obj_count=0."""
    payload = struct.pack("<BiB", command & 0xFF, frame_current, flags & 0xFF)
    pkt = build_packet(PT_PLAYBACK_TRANSPORT, payload, obj_count=0)
    sock.sendall(pkt)
    cmd_names = {0: "SetFrame", 1: "Play", 2: "Pause", 3: "Stop"}
    cname = cmd_names.get(command, f"Unknown({command})")
    print(f"  [PLAYBACK_TRANSPORT] cmd={cname} frame={frame_current} flags={flags}")


def send_timeline_state(sock, frame_start, frame_end, frame_current, fps_num, fps_den):
    payload = struct.pack("<iiiii", frame_start, frame_end, frame_current, fps_num, fps_den)
    pkt = build_packet(PT_TIMELINE_STATE, payload)
    sock.sendall(pkt)
    print(f"  [TIMELINE_STATE] frames=[{frame_start}-{frame_end}] fps={fps_num}/{fps_den}")


def send_sequencer_op_create_sequence(sock, sequence, timestamp, frame_start, frame_end, fps_num, fps_den):
    common = build_sequencer_op_common(SEQUENCER_OP_CREATE_SEQUENCE, sequence, timestamp)
    payload = common + struct.pack("<iiii", frame_start, frame_end, fps_num, fps_den)
    pkt = build_packet(PT_SEQUENCER_OP, payload)
    sock.sendall(pkt)
    print(f"  [CREATE_SEQUENCE]")


def send_sequencer_op_add_possessable(sock, sequence, timestamp, guid_obj, binding_type):
    common = build_sequencer_op_common(SEQUENCER_OP_ADD_POSSESSABLE, sequence, timestamp)
    guid_bytes = pack_ue_fguid(guid_obj)
    payload = common + struct.pack("<16sB", guid_bytes, binding_type & 0xFF)
    pkt = build_packet(PT_SEQUENCER_OP, payload)
    sock.sendall(pkt)
    print(f"  [ADD_POSSESSABLE]")


def build_v4_object(guid_obj, transform, timestamp):
    payload = bytearray()
    payload.extend(pack_ue_fguid(guid_obj))
    payload.extend(struct.pack("<fff", *transform[:3]))
    payload.extend(struct.pack("<ffff", *transform[3:7]))
    payload.extend(struct.pack("<fff", *transform[7:10]))
    payload.extend(struct.pack("<d", timestamp))
    payload.extend(struct.pack("<IIII", 0, 0, 0, 0))
    payload.extend(b'\x00')
    return bytes(payload)


def send_v4_object(sock, ptype, guid_obj, timestamp, transform):
    payload = build_v4_object(guid_obj, transform, timestamp)
    pkt = build_packet(ptype, payload)
    sock.sendall(pkt)
    name = "PT_Create" if ptype == PT_CREATE else "PT_Transform"
    print(f"  [{name}]")


def send_keyframe(sock, sequence, timestamp, entries):
    entry_payload = b''
    for guid_obj, frame, value, channel in entries:
        entry_payload += pack_ue_fguid(guid_obj) + struct.pack(
            "<i f B", frame, value, channel)
    payload = struct.pack("<I d B B", sequence, timestamp, len(entries), 0) + entry_payload
    pkt = build_packet(PT_KEYFRAME, payload, version=LIVE_SYNC_VERSION_V5)
    sock.sendall(pkt)
    print(f"  [PT_Keyframe] entries={len(entries)}")


def read_ue_log_lines(pattern, max_lines=5000):
    log_path = "/home/nguyennongngockhanh/Documents/Unreal Projects/ProjectTemplate/Saved/Logs/ProjectTemplate.log"
    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        lines = lines[-max_lines:]
        return [l for l in lines if pattern in l]
    except Exception as e:
        print(f"  ERROR reading log: {e}")
        return []


def check_logs():
    print("\n--- Log Check ---")
    pb_logs = read_ue_log_lines("[PLAYBACK]")
    tl_logs = read_ue_log_lines("[TIMELINE]")
    kf_logs = read_ue_log_lines("[KEYFRAME]")

    print(f"  [PLAYBACK] messages: {len(pb_logs)}")
    for line in pb_logs[-10:]:
        print(f"    {line.strip()}")

    print(f"  [TIMELINE] messages: {len(tl_logs)}")
    for line in tl_logs[-3:]:
        print(f"    {line.strip()}")

    print(f"  [KEYFRAME] messages: {len(kf_logs)}")
    for line in kf_logs[-3:]:
        print(f"    {line.strip()}")

    pb_recv = any("[PLAYBACK][RECV]" in l for l in pb_logs)
    pb_apply = any("[PLAYBACK][APPLY]" in l for l in pb_logs)
    pb_malformed = any("[PLAYBACK][MALFORMED]" in l for l in pb_logs)

    all_pass = True
    if pb_recv and pb_apply:
        print("\n  PASS: [PLAYBACK][RECV] and [PLAYBACK][APPLY] found")
    else:
        print(f"\n  PARTIAL: recv={pb_recv} apply={pb_apply}")
        all_pass = False

    if pb_malformed:
        print("  WARN: [PLAYBACK][MALFORMED] present — unexpected for valid packets")

    return all_pass


def run_phase1(sock):
    """Phase 1: PT_TimelineState + PT_PlaybackTransport commands."""
    print("\n--- Phase 1: Timeline + Playback Transport ---")
    send_timeline_state(sock, 1, 120, 24, 24, 1)
    time.sleep(0.3)
    send_playback_transport(sock, PLAYBACK_TRANSPORT_SET_FRAME, 48, 0)
    time.sleep(0.3)
    send_playback_transport(sock, PLAYBACK_TRANSPORT_PLAY, 48, 0)
    time.sleep(0.3)
    send_playback_transport(sock, PLAYBACK_TRANSPORT_PAUSE, 72, 0)
    time.sleep(0.3)
    send_playback_transport(sock, PLAYBACK_TRANSPORT_STOP, 1, 0)
    time.sleep(0.5)


def run_phase2(sock):
    """Phase 2: Full 5-packet keyframe flow + playback transport."""
    test_guid = uuid.uuid4()
    seq_num = 1
    transform = (0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0, 1.0, 1.0, 1.0)

    print("\n--- Phase 2: Full 5-packet flow + Playback Transport ---")
    send_sequencer_op_create_sequence(sock, seq_num, time.time(), 1, 120, 24, 1)
    time.sleep(0.3)
    send_v4_object(sock, PT_CREATE, test_guid, time.time(), transform)
    time.sleep(0.3)
    send_sequencer_op_add_possessable(sock, seq_num + 1, time.time(), test_guid, 1)
    time.sleep(0.3)
    send_v4_object(sock, PT_TRANSFORM, test_guid, time.time(), transform)
    time.sleep(0.3)

    KEYFRAME_CHANNEL_VISIBILITY_VIEWPORT = 9
    KEYFRAME_CHANNEL_VISIBILITY_RENDER = 10
    CH_X, CH_Y, CH_Z = 0, 1, 2
    entries = [
        (test_guid, 1,   0.0, CH_X),
        (test_guid, 1,   0.0, CH_Y),
        (test_guid, 1,   0.0, CH_Z),
        (test_guid, 1,   0.0, KEYFRAME_CHANNEL_VISIBILITY_VIEWPORT),
        (test_guid, 1,   0.0, KEYFRAME_CHANNEL_VISIBILITY_RENDER),
        (test_guid, 10,  1.0, CH_X),
        (test_guid, 10,  1.0, KEYFRAME_CHANNEL_VISIBILITY_VIEWPORT),
        (test_guid, 10,  1.0, KEYFRAME_CHANNEL_VISIBILITY_RENDER),
        (test_guid, 20,  2.0, CH_X),
        (test_guid, 20,  0.0, KEYFRAME_CHANNEL_VISIBILITY_VIEWPORT),
        (test_guid, 20,  0.0, KEYFRAME_CHANNEL_VISIBILITY_RENDER),
    ]
    send_keyframe(sock, seq_num + 2, time.time(), entries)
    time.sleep(1)
    send_playback_transport(sock, PLAYBACK_TRANSPORT_SET_FRAME, 48, 0)
    time.sleep(1)


def main():
    print("=" * 60)
    print("Phase 7F Stage 2 — PT_PlaybackTransport TCP Injector")
    print("=" * 60)

    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(3)
        sock.connect((UE_HOST, UE_PORT))
        sock.settimeout(None)
        print(f"  Connected to UE on {UE_HOST}:{UE_PORT}")
    except Exception as e:
        print(f"  Cannot connect to UE: {e}")
        return 1

    phase = sys.argv[1] if len(sys.argv) > 1 else "all"
    if phase == "transport":
        run_phase1(sock)
    elif phase == "full":
        run_phase2(sock)
    else:
        run_phase1(sock)
        run_phase2(sock)

    check_logs()
    print("\n" + "=" * 60)
    print("  Injector complete")
    print("=" * 60)
    sock.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
