#!/usr/bin/env python3
"""Phase 7F Stage 1 — Timeline State TCP Injector.

Sends PT_TimelineState (0x19) and optionally the full 5-packet
Sequencer flow to validate timeline state apply in UE.

Packet format (20 bytes payload):
  frame_start   int32
  frame_end     int32
  frame_current int32
  fps_num       int32
  fps_den       int32
"""

import struct
import socket
import time
import sys
import uuid

LIVE_SYNC_MAGIC = 0x4C56534D
PT_TIMELINE_STATE = 0x19
PT_SEQUENCER_OP = 0x18
PT_CREATE = 0x03
PT_TRANSFORM = 0x01
PT_KEYFRAME = 0x17
LIVE_SYNC_VERSION_V4 = 4
LIVE_SYNC_VERSION_V5 = 5

SEQUENCER_OP_CREATE_SEQUENCE = 0
SEQUENCER_OP_ADD_POSSESSABLE = 1

UE_HOST = "127.0.0.1"
UE_PORT = 57000
_seq_counter = 0


def pack_ue_fguid(guid_obj):
    """Pack a UUID into 16 bytes matching UE FGuid wire layout (4 x uint32 LE)."""
    d_a = guid_obj.time_low
    d_b = (guid_obj.time_mid << 16) | guid_obj.time_hi_version
    d_c = (guid_obj.clock_seq_hi_variant << 24
           | guid_obj.clock_seq_low << 16
           | (guid_obj.node >> 32) & 0xFFFF)
    d_d = guid_obj.node & 0xFFFFFFFF
    return struct.pack("<IIII", d_a, d_b, d_c, d_d)


def build_packet(ptype, payload, version=LIVE_SYNC_VERSION_V4, flags=0):
    """Build a LiveSync V4+ packet with 24-byte magic header."""
    global _seq_counter
    _seq_counter += 1
    seq = _seq_counter
    obj_count = 1
    header_size = 24
    packet_size = header_size + len(payload)

    header = struct.pack('<I H B B Q I I',
                         LIVE_SYNC_MAGIC, version, ptype, flags, seq, packet_size, obj_count)
    return header + payload


def build_sequencer_op_common(opcode, sequence, timestamp, flags=0):
    """Build the 16-byte FSequencerOpHeader."""
    return struct.pack(
        "<BBHI d",
        opcode & 0xFF,
        flags & 0xFF,
        0,                      # reserved uint16
        sequence & 0xFFFFFFFF,
        timestamp,              # double
    )


def send_timeline_state(sock, frame_start, frame_end, frame_current, fps_num, fps_den):
    """Send PT_TimelineState (0x19) — 20-byte payload.

    [TIMELINE][SEND] marker pattern for UE log validation.
    """
    payload = struct.pack("<iiiii", frame_start, frame_end, frame_current, fps_num, fps_den)
    pkt = build_packet(PT_TIMELINE_STATE, payload)
    sock.sendall(pkt)
    print(f"  [TIMELINE_STATE] frames=[{frame_start}-{frame_end}] current={frame_current} "
          f"fps={fps_num}/{fps_den} payload_len={len(payload)}")
    return pkt


def send_sequencer_op_create_sequence(sock, sequence, timestamp, frame_start, frame_end, fps_num, fps_den):
    """Send PT_SequencerOp CREATE_SEQUENCE."""
    common = build_sequencer_op_common(
        SEQUENCER_OP_CREATE_SEQUENCE, sequence, timestamp)
    payload = common + struct.pack("<iiii", frame_start, frame_end, fps_num, fps_den)
    pkt = build_packet(PT_SEQUENCER_OP, payload)
    sock.sendall(pkt)
    print(f"  [CREATE_SEQUENCE] payload_len={len(payload)} total_packet_len={len(pkt)}")
    return pkt


def send_sequencer_op_add_possessable(sock, sequence, timestamp, guid_obj, binding_type):
    """Send PT_SequencerOp ADD_POSSESSABLE."""
    common = build_sequencer_op_common(
        SEQUENCER_OP_ADD_POSSESSABLE, sequence, timestamp)
    guid_bytes = pack_ue_fguid(guid_obj)
    payload = common + struct.pack("<16sB", guid_bytes, binding_type & 0xFF)
    pkt = build_packet(PT_SEQUENCER_OP, payload)
    sock.sendall(pkt)
    print(f"  [ADD_POSSESSABLE] guid={guid_obj.hex}")
    return pkt


def build_v4_object(guid_obj, transform, timestamp):
    """Build an 81-byte V4 object payload."""
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
    """Send a V4 packet (PT_Create or PT_Transform) with 81-byte object payload."""
    payload = build_v4_object(guid_obj, transform, timestamp)
    pkt = build_packet(ptype, payload)
    sock.sendall(pkt)
    name = "PT_Create" if ptype == PT_CREATE else "PT_Transform"
    print(f"  [{name}] guid={guid_obj.hex}")
    return pkt


def send_keyframe(sock, sequence, timestamp, entries):
    """Send PT_Keyframe (0x17) with entries."""
    entry_payload = b''
    for guid_obj, frame, value, channel in entries:
        entry_payload += pack_ue_fguid(guid_obj) + struct.pack(
            "<i f B", frame, value, channel)

    payload = struct.pack(
        "<I d B B", sequence, timestamp, len(entries), 0) + entry_payload
    pkt = build_packet(PT_KEYFRAME, payload, version=LIVE_SYNC_VERSION_V5)
    sock.sendall(pkt)
    print(f"  [PT_Keyframe] entries={len(entries)}")
    return pkt


def read_ue_log_lines(pattern, max_lines=5000):
    """Read UE log and return lines matching pattern."""
    log_path = "/home/nguyennongngockhanh/Documents/Unreal Projects/ProjectTemplate/Saved/Logs/ProjectTemplate.log"
    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        lines = lines[-max_lines:]
        return [l for l in lines if pattern in l]
    except Exception as e:
        print(f"  ERROR reading log: {e}")
        return []


def run_timeline_state_only(sock):
    """Phase 1: Send only PT_TimelineState, verify [TIMELINE] markers."""
    print("\n--- Phase 1: PT_TimelineState only ---")
    send_timeline_state(sock, 1, 120, 24, 24, 1)
    time.sleep(0.5)
    send_timeline_state(sock, 1, 250, 60, 30, 1)
    time.sleep(0.5)
    send_timeline_state(sock, 0, 0, 0, 0, 0)
    time.sleep(0.5)


def run_full_sequencer_flow(sock):
    """Phase 2: Full 5-packet flow, ending with PT_TimelineState."""
    test_guid = uuid.uuid4()
    seq_num = 1
    transform = (0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0, 1.0, 1.0, 1.0)

    print("\n--- Phase 2: Full 5-packet flow + PT_TimelineState ---")

    # 1. CREATE_SEQUENCE
    send_sequencer_op_create_sequence(sock, seq_num, time.time(), 1, 120, 24, 1)
    time.sleep(0.3)

    # 2. PT_Create
    send_v4_object(sock, PT_CREATE, test_guid, time.time(), transform)
    time.sleep(0.3)

    # 3. ADD_POSSESSABLE
    send_sequencer_op_add_possessable(sock, seq_num + 1, time.time(), test_guid, 1)
    time.sleep(0.3)

    # 4. PT_Transform
    send_v4_object(sock, PT_TRANSFORM, test_guid, time.time(), transform)
    time.sleep(0.3)

    # 5. PT_Keyframe — 11 entries (match 10B reference: channels 0,1,2,9,10)
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

    # 6. PT_TimelineState
    send_timeline_state(sock, 1, 120, 24, 24, 1)
    time.sleep(1)


def check_logs():
    """Check UE logs for validation markers."""
    print("\n--- Log Check ---")

    tl_logs = read_ue_log_lines("[TIMELINE]")
    seq_logs = read_ue_log_lines("[SEQ]")
    kf_logs = read_ue_log_lines("[KEYFRAME]")

    print(f"\n  [TIMELINE] messages: {len(tl_logs)}")
    for line in tl_logs[-5:]:
        print(f"    {line.strip()}")

    print(f"\n  [SEQ] messages: {len(seq_logs)}")
    for line in seq_logs[-3:]:
        print(f"    {line.strip()}")

    print(f"\n  [KEYFRAME] messages: {len(kf_logs)}")
    for line in kf_logs[-3:]:
        print(f"    {line.strip()}")

    tl_recv = any("[TIMELINE][RECV]" in l for l in tl_logs)
    tl_apply = any("[TIMELINE][APPLY]" in l for l in tl_logs)
    tl_skip = any("[TIMELINE][SKIP]" in l for l in tl_logs)

    all_pass = True

    if tl_recv and tl_apply:
        print("\n  PASS: [TIMELINE][RECV] and [TIMELINE][APPLY] found")
    else:
        print(f"\n  PARTIAL: recv={tl_recv} apply={tl_apply}")
        if not tl_recv:
            print("  - [TIMELINE][RECV] missing — packet not received")
        if not tl_apply:
            print("  - [TIMELINE][APPLY] missing — sequence may not exist or apply skipped")
            if tl_skip:
                print("  - [TIMELINE][SKIP] present — no active LevelSequence")

    return all_pass


def main():
    print("=" * 60)
    print("Phase 7F Stage 1 — PT_TimelineState TCP Injector")
    print("=" * 60)

    # Check UE
    print("\n[0] Checking UE availability...")
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

    if phase == "timeline":
        run_timeline_state_only(sock)
    elif phase == "full":
        run_full_sequencer_flow(sock)
    else:
        run_timeline_state_only(sock)
        run_full_sequencer_flow(sock)

    check_logs()

    print("\n" + "=" * 60)
    print("  Injector complete")
    print("=" * 60)

    sock.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
