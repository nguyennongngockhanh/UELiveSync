#!/usr/bin/env python3
"""Minimal TCP client to inject the Stage 10B.2 active sequence flow into UE.

Sends:
1. PT_SequencerOp CREATE_SEQUENCE  (wire-correct)
2. PT_Create (0x03)                (81B V4 object)
3. PT_SequencerOp ADD_POSSESSABLE  (wire-correct)
4. PT_Transform (0x01)             (81B V4 object)
5. PT_Keyframe (0x17)              (14B header + 11 x 25B entries)

After injection, reads UE log for [SEQ] and [KEYFRAME] markers.

Protocol references:
  - FSequencerOpHeader: 16B (opcode+flags+reserved+sequence+timestamp)
  - CREATE_SEQUENCE payload: 16B (frame_start+frame_end+fps_num+fps_den)
  - ADD_POSSESSABLE payload: 17B (FGuid + binding_type)
  - V4 object: 81B (GUID + loc + rot(quat) + scale + timestamp + parent + prim)
  - FGuid wire: 4 x uint32 LE (decomposed from Python UUID)
"""

import struct
import socket
import time
import sys
import uuid

# =========================================================
# Constants
# =========================================================
LIVE_SYNC_MAGIC = 0x4C56534D
PT_SEQUENCER_OP = 0x18
PT_CREATE = 0x03
PT_TRANSFORM = 0x01
PT_KEYFRAME = 0x17
LIVE_SYNC_VERSION_V4 = 4
LIVE_SYNC_VERSION_V5 = 5

# Sequencer opcodes
SEQUENCER_OP_CREATE_SEQUENCE = 0
SEQUENCER_OP_ADD_POSSESSABLE = 1

UE_HOST = "127.0.0.1"
UE_PORT = 57000
_seq_counter = 0


# =========================================================
# GUID helpers
# =========================================================
def pack_ue_fguid(guid_obj):
    """Pack a UUID into 16 bytes matching UE FGuid wire layout (4 x uint32 LE)."""
    d_a = guid_obj.time_low
    d_b = (guid_obj.time_mid << 16) | guid_obj.time_hi_version
    d_c = (guid_obj.clock_seq_hi_variant << 24
           | guid_obj.clock_seq_low << 16
           | (guid_obj.node >> 32) & 0xFFFF)
    d_d = guid_obj.node & 0xFFFFFFFF
    return struct.pack("<IIII", d_a, d_b, d_c, d_d)


# =========================================================
# Packet builder
# =========================================================
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


# =========================================================
# SequencerOp helpers
# =========================================================
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


def send_sequencer_op_create_sequence(sock, sequence, timestamp, frame_start, frame_end, fps_num, fps_den):
    """Send PT_SequencerOp CREATE_SEQUENCE.

    Total payload: 16 (common header) + 16 (create payload) = 32 bytes.
    """
    common = build_sequencer_op_common(
        SEQUENCER_OP_CREATE_SEQUENCE, sequence, timestamp)
    payload = common + struct.pack(
        "<iiii",
        frame_start, frame_end, fps_num, fps_den)
    pkt = build_packet(PT_SEQUENCER_OP, payload, version=LIVE_SYNC_VERSION_V4)
    sock.sendall(pkt)
    print(f"  [CREATE_SEQUENCE] payload_len={len(payload)} total_packet_len={len(pkt)}")
    return pkt


def send_sequencer_op_add_possessable(sock, sequence, timestamp, guid_obj, binding_type):
    """Send PT_SequencerOp ADD_POSSESSABLE.

    Total payload: 16 (common header) + 17 (guid + binding_type) = 33 bytes.
    """
    common = build_sequencer_op_common(
        SEQUENCER_OP_ADD_POSSESSABLE, sequence, timestamp)
    guid_bytes = pack_ue_fguid(guid_obj)
    payload = common + struct.pack(
        "<16sB",
        guid_bytes,
        binding_type & 0xFF,
    )
    pkt = build_packet(PT_SEQUENCER_OP, payload, version=LIVE_SYNC_VERSION_V4)
    sock.sendall(pkt)
    print(f"  [ADD_POSSESSABLE] guid={guid_obj.hex} seq={sequence}")
    return pkt


# =========================================================
# V4 object helpers
# =========================================================
def build_v4_object(guid_obj, transform, timestamp):
    """Build an 81-byte V4 object payload.

    Layout:
      guid:         16 bytes  (4 x uint32 LE)
      location:     12 bytes  (3 x float)
      rotation:     16 bytes  (4 x float quaternion)
      scale:        12 bytes  (3 x float)
      timestamp:     8 bytes  (double)
      parent_guid:  16 bytes  (4 x uint32 LE, all zero = no parent)
      prim_type:     1 byte   (0 = other)
    """
    payload = bytearray()

    # GUID
    payload.extend(pack_ue_fguid(guid_obj))

    # Location
    payload.extend(struct.pack("<fff", *transform[:3]))

    # Rotation (quaternion)
    payload.extend(struct.pack("<ffff", *transform[3:7]))

    # Scale
    payload.extend(struct.pack("<fff", *transform[7:10]))

    # Timestamp (double)
    payload.extend(struct.pack("<d", timestamp))

    # Parent GUID (all zeros = no parent)
    payload.extend(struct.pack("<IIII", 0, 0, 0, 0))

    # Primitive type
    payload.extend(b'\x00')

    return bytes(payload)


def send_v4_object(sock, ptype, guid_obj, timestamp, transform):
    """Send a V4 packet (PT_Create or PT_Transform) with 81-byte object payload."""
    payload = build_v4_object(guid_obj, transform, timestamp)
    pkt = build_packet(ptype, payload, version=LIVE_SYNC_VERSION_V4)
    sock.sendall(pkt)
    name = "PT_Create" if ptype == PT_CREATE else "PT_Transform"
    print(f"  [{name}] guid={guid_obj.hex} payload_len={len(payload)} total_packet_len={len(pkt)}")
    return pkt


# =========================================================
# Keyframe helper
# =========================================================
def send_keyframe(sock, sequence, timestamp, entries):
    """Send PT_Keyframe (0x17) with entries.

    Keyframe header: 14 bytes
      Sequence  uint32 (4)
      Timestamp double (8)
      KeyCount  uint8  (1)
      Flags     uint8  (1)

    Each entry: 25 bytes
      guid    16 bytes (4 x uint32 LE)
      value   4 bytes  (float)
      frame   4 bytes  (uint32)
      channel 1 byte   (uint8)
    """
    entry_payload = b''
    for guid_obj, frame, value, channel in entries:
        # FKeyframeEntry: FGuid(16) + Frame(int32, 4) + Value(float, 4) + ChannelIndex(u8, 1)
        entry_payload += pack_ue_fguid(guid_obj) + struct.pack(
            "<i f B", frame, value, channel)

    payload = struct.pack(
        "<I d B B", sequence, timestamp, len(entries), 0) + entry_payload
    pkt = build_packet(PT_KEYFRAME, payload, version=LIVE_SYNC_VERSION_V5)
    sock.sendall(pkt)
    print(f"  [PT_Keyframe] seq={sequence} entries={len(entries)} payload_len={len(payload)}")
    return pkt


# =========================================================
# UE log reader
# =========================================================
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


# =========================================================
# Main
# =========================================================
def main():
    print("=" * 60)
    print("Stage 10B.2 — TCP Packet Injector (Blender addon-free)")
    print("=" * 60)

    # Check UE
    print("\n[0] Checking UE availability...")
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(3)
        sock.connect((UE_HOST, UE_PORT))
        sock.settimeout(None)
        print(f"  Connected to UE on {UE_HOST}:{UE_PORT} v")
    except Exception as e:
        print(f"  Cannot connect to UE: {e}")
        return 1

    # Generate test GUID and identity transform (quaternion identity)
    test_guid = uuid.uuid4()
    seq_num = 1
    # transform = (lx, ly, lz,  rx, ry, rz, rw,  sx, sy, sz)
    transform = (0.0, 0.0, 0.0,  0.0, 0.0, 0.0, 1.0,  1.0, 1.0, 1.0)

    # =====================================================
    # 1. CREATE_SEQUENCE
    # =====================================================
    print("\n--- [1] CREATE_SEQUENCE ---")
    send_sequencer_op_create_sequence(sock, seq_num, time.time(), 1, 20, 24, 1)
    time.sleep(0.5)

    # =====================================================
    # 2. PT_Create (0x03) — 81-byte V4 object
    # =====================================================
    print("\n--- [2] PT_Create ---")
    send_v4_object(sock, PT_CREATE, test_guid, time.time(), transform)
    time.sleep(0.3)

    # =====================================================
    # 3. ADD_POSSESSABLE
    # =====================================================
    print("\n--- [3] ADD_POSSESSABLE ---")
    send_sequencer_op_add_possessable(sock, seq_num + 1, time.time(), test_guid, 1)
    time.sleep(0.3)

    # =====================================================
    # 4. PT_Transform (0x01) — 81-byte V4 object
    # =====================================================
    print("\n--- [4] PT_Transform ---")
    send_v4_object(sock, PT_TRANSFORM, test_guid, time.time(), transform)
    time.sleep(0.3)

    # =====================================================
    # 5. PT_Keyframe (0x17) — 11 entries
    # =====================================================
    print("\n--- [5] PT_Keyframe ---")
    KEYFRAME_CHANNEL_VISIBILITY_VIEWPORT = 9
    KEYFRAME_CHANNEL_VISIBILITY_RENDER = 10
    CH_X, CH_Y, CH_Z = 0, 1, 2

    entries = [
        (test_guid, 1, 0.0, CH_X),
        (test_guid, 1, 0.0, CH_Y),
        (test_guid, 1, 0.0, CH_Z),
        (test_guid, 1, 0.0, KEYFRAME_CHANNEL_VISIBILITY_VIEWPORT),
        (test_guid, 1, 0.0, KEYFRAME_CHANNEL_VISIBILITY_RENDER),
        (test_guid, 10, 1.0, CH_X),
        (test_guid, 10, 1.0, KEYFRAME_CHANNEL_VISIBILITY_VIEWPORT),
        (test_guid, 10, 1.0, KEYFRAME_CHANNEL_VISIBILITY_RENDER),
        (test_guid, 20, 2.0, CH_X),
        (test_guid, 20, 0.0, KEYFRAME_CHANNEL_VISIBILITY_VIEWPORT),
        (test_guid, 20, 0.0, KEYFRAME_CHANNEL_VISIBILITY_RENDER),
    ]
    send_keyframe(sock, seq_num + 2, time.time(), entries)
    time.sleep(3)  # Let UE game thread process

    # =====================================================
    # 6. Check UE logs
    # =====================================================
    print("\n--- [6] Checking UE logs ---")

    seq_logs = read_ue_log_lines("[SEQ]")
    kf_logs = read_ue_log_lines("[KEYFRAME]")

    print(f"\n  [SEQ] messages: {len(seq_logs)}")
    for line in seq_logs:
        print(f"    {line.strip()}")

    print(f"\n  [KEYFRAME] messages: {len(kf_logs)}")
    for line in kf_logs:
        print(f"    {line.strip()}")

    # Parse SEQ markers
    asset_load_or_create = any("[SEQ][ASSET_LOAD]" in l or "[SEQ][ASSET_CREATE]" in l for l in seq_logs)
    asset_ready = any("[SEQ][ASSET_READY]" in l for l in seq_logs)
    reset_found = any("[SEQ][RESET]" in l for l in seq_logs)

    # Parse KEYFRAME applied/miss/unsupp
    kf_applied_val = None
    kf_miss_val = None
    kf_unsupp_val = None
    for line in kf_logs:
        if "applied=" in line and "miss=" in line and "unsupp=" in line:
            try:
                parts = line.strip().split()
                for p in parts:
                    if p.startswith("applied="):
                        kf_applied_val = int(p.split("=")[1])
                    elif p.startswith("miss="):
                        kf_miss_val = int(p.split("=")[1])
                    elif p.startswith("unsupp="):
                        kf_unsupp_val = int(p.split("=")[1])
            except Exception:
                pass
            break

    # =====================================================
    # 7. Results
    # =====================================================
    print("\n" + "=" * 60)
    print("  VALIDATION RESULTS")
    print("=" * 60)

    all_pass = True

    if asset_load_or_create:
        print("  PASS: [SEQ][ASSET_LOAD] or [SEQ][ASSET_CREATE] found")
    else:
        print("  FAIL: No [SEQ][ASSET_LOAD] or [SEQ][ASSET_CREATE] in log")
        all_pass = False

    if asset_ready:
        print("  PASS: [SEQ][ASSET_READY] found")
    else:
        print("  FAIL: No [SEQ][ASSET_READY] in log")
        all_pass = False

    if reset_found:
        print("  PASS: [SEQ][RESET] found")
    else:
        print("  FAIL: No [SEQ][RESET] in log")
        all_pass = False

    kf_ok = (kf_applied_val is not None and kf_applied_val == 11
             and kf_miss_val == 0 and kf_unsupp_val == 0)
    if kf_ok:
        print(f"  PASS: [KEYFRAME] applied={kf_applied_val} miss={kf_miss_val} unsupp={kf_unsupp_val}")
    else:
        print(f"  FAIL: [KEYFRAME] applied={kf_applied_val} miss={kf_miss_val} unsupp={kf_unsupp_val}")
        all_pass = False

    print("\n" + "=" * 60)
    if all_pass:
        print("  v ALL CHECKS PASSED")
    else:
        print("  x SOME CHECKS FAILED")
    print("=" * 60)

    sock.close()
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
