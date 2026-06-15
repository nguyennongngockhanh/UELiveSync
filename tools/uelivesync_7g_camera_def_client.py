#!/usr/bin/env python3
"""Phase 7G Stage 3 — Camera Def TCP Injector.

Sends PT_CameraDef (0x1B) with camera parameters (focal, sensor,
clip, ortho, flags) to validate UE-side camera definition apply.

Usage:
    python3 tools/uelivesync_7g_camera_def_client.py [--guid GUID] [options]
"""

import struct
import socket
import time
import sys
import uuid
import argparse

LIVE_SYNC_MAGIC = 0x4C56534D
PT_CAMERA_DEF = 0x1B
PT_ACTIVE_CAMERA = 0x15
LIVE_SYNC_VERSION_V5 = 5

UE_HOST = "127.0.0.1"
UE_PORT = 57000
_seq_counter = 0

# Flags
CAMERA_DEF_FLAG_IS_ORTHO = 0x01
CAMERA_DEF_FLAG_HAS_CAMERA_DEF = 0x02


def pack_ue_fguid(guid_obj):
    d_a = guid_obj.time_low
    d_b = (guid_obj.time_mid << 16) | guid_obj.time_hi_version
    d_c = (guid_obj.clock_seq_hi_variant << 24
           | guid_obj.clock_seq_low << 16
           | (guid_obj.node >> 32) & 0xFFFF)
    d_d = guid_obj.node & 0xFFFFFFFF
    return struct.pack("<IIII", d_a, d_b, d_c, d_d)


def build_packet(ptype, payload, version=LIVE_SYNC_VERSION_V5, flags=0, obj_count=0):
    global _seq_counter
    _seq_counter += 1
    seq = _seq_counter
    header_size = 24
    packet_size = header_size + len(payload)
    header = struct.pack('<I H B B Q I I',
                         LIVE_SYNC_MAGIC, version, ptype, flags, seq, packet_size, obj_count)
    return header + payload


def build_active_camera_payload(guid_obj, cam_sequence, timestamp):
    """Build 28-byte PT_ActiveCamera payload."""
    guid_bytes = pack_ue_fguid(guid_obj)
    return struct.pack("<16s I d", guid_bytes, cam_sequence & 0xFFFFFFFF, timestamp)


def build_camera_def_payload(guid_obj, focal_length_mm=50.0,
                              sensor_width_mm=36.0, sensor_height_mm=24.0,
                              clip_start=10.0, clip_end=100000.0,
                              ortho_scale=6.0, flags=0x02):
    """Build 44-byte PT_CameraDef payload.

    Layout: guid(16) + focal(4) + sensor_w(4) + sensor_h(4)
            + clip_start(4) + clip_end(4) + ortho_scale(4)
            + flags(1) + reserved(3)
    """
    guid_bytes = pack_ue_fguid(guid_obj)
    fmt = "<16s f f f f f f B 3x"
    return struct.pack(fmt,
                       guid_bytes,
                       focal_length_mm,
                       sensor_width_mm,
                       sensor_height_mm,
                       clip_start,
                       clip_end,
                       ortho_scale,
                       flags & 0xFF)


def main():
    parser = argparse.ArgumentParser(
        description="Inject PT_CameraDef packets to UE")
    parser.add_argument("--guid", type=str, default=None,
                        help="GUID for the camera (UUID format)")
    parser.add_argument("--focal", type=float, default=35.0,
                        help="Focal length in mm (default: 35.0)")
    parser.add_argument("--sensor-width", type=float, default=36.0,
                        help="Sensor width in mm (default: 36.0)")
    parser.add_argument("--sensor-height", type=float, default=24.0,
                        help="Sensor height in mm (default: 24.0)")
    parser.add_argument("--clip-start", type=float, default=0.1,
                        help="Near clip plane (default: 0.1)")
    parser.add_argument("--clip-end", type=float, default=1000.0,
                        help="Far clip plane (default: 1000.0)")
    parser.add_argument("--ortho-scale", type=float, default=10.0,
                        help="Orthographic scale (default: 10.0)")
    parser.add_argument("--ortho", action="store_true",
                        help="Set IS_ORTHO flag")
    parser.add_argument("--host", type=str, default=UE_HOST)
    parser.add_argument("--port", type=int, default=UE_PORT)
    args = parser.parse_args()

    guid = uuid.UUID(args.guid) if args.guid else uuid.uuid4()

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(10)
    try:
        sock.connect((args.host, args.port))
        print(f"Connected to {args.host}:{args.port}")
    except Exception as e:
        print(f"ERROR: Could not connect to {args.host}:{args.port} — {e}")
        sys.exit(1)

    # Step 1: Send PT_ActiveCamera with unique sequence (timestamp-based)
    # HandleActiveCamera will auto-spawn ACameraActor if GUID not in cache.
    cam_sequence = int(time.time() * 1000) & 0xFFFFFFFF
    payload = build_active_camera_payload(guid, cam_sequence, time.time())
    pkt = build_packet(PT_ACTIVE_CAMERA, payload, obj_count=1)
    sock.sendall(pkt)
    print(f"  [ACTIVE_CAMERA] GUID={guid} seq={cam_sequence}")
    time.sleep(0.3)

    # Step 2: Send PT_CameraDef with specified parameters
    flags = CAMERA_DEF_FLAG_HAS_CAMERA_DEF
    if args.ortho:
        flags |= CAMERA_DEF_FLAG_IS_ORTHO
    camdef_payload = build_camera_def_payload(
        guid, args.focal, args.sensor_width, args.sensor_height,
        args.clip_start, args.clip_end, args.ortho_scale, flags)
    camdef_pkt = build_packet(PT_CAMERA_DEF, camdef_payload, obj_count=0)
    sock.sendall(camdef_pkt)
    print(f"  [CAMERA_DEF] focal={args.focal} sensor={args.sensor_width}x{args.sensor_height} "
          f"clip=({args.clip_start},{args.clip_end}) ortho={args.ortho_scale} flags={flags}")
    time.sleep(0.5)

    print()
    print("Done. Check UE log for [CAMERA][DEF_RECV] and [CAMERA][DEF] markers.")

    sock.close()


if __name__ == '__main__':
    main()
