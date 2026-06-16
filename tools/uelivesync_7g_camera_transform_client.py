#!/usr/bin/env python3
"""Phase 7G Stage 4 — Camera Transform Sync TCP Injector.

Stage 7G.4 validates CREATE + TRANSFORM + ACTIVE_CAMERA on separate ticks
to avoid SeenThisTick dedup skipping TRANSFORM after CREATE for same GUID
in the same UE tick.

CameraDef (0x1B) belongs to Stage 7G.3 and is tested via --cameradef-only.

Modes:
    --create-transform-active   CREATE + TRANSFORM + ACTIVE (separate ticks,
                                one connection, 0.2s sleep between packets)
    --cameradef-only            Send PT_CameraDef=0x1B for an existing camera GUID
    --full-separated            --create-transform-active + 3s wait + --cameradef-only

Lifecycle note:
    ProcessQueuedPackets uses SeenThisTick dedup: if CREATE and TRANSFORM for
    the same GUID arrive in the same UE tick, the TRANSFORM is skipped.
    This is expected dedup behavior, not a UE plugin bug.
    The injector must send CREATE and TRANSFORM in separate ticks.

Usage:
    python3 tools/uelivesync_7g_camera_transform_client.py --create-transform-active
    python3 tools/uelivesync_7g_camera_transform_client.py --cameradef-only
    python3 tools/uelivesync_7g_camera_transform_client.py --full-separated
    python3 tools/uelivesync_7g_camera_transform_client.py --guid <hex>
"""

import struct
import socket
import time
import sys
import uuid
import math
import argparse

LIVE_SYNC_MAGIC = 0x4C56534D
PT_CREATE       = 0x03
PT_TRANSFORM    = 0x01
PT_ACTIVE_CAMERA= 0x15
PT_CAMERA_DEF   = 0x1B
LIVE_SYNC_VERSION_V5 = 5

# Camera primitive type
LSP_CAMERA = 0x05

# Flags
CAMERA_DEF_FLAG_HAS_CAMERA_DEF = 0x02
CAMERA_DEF_FLAG_IS_ORTHO       = 0x01

UE_HOST = "127.0.0.1"
UE_PORT = 57000
_seq_counter = 0


def pack_ue_fguid(guid_obj):
    """Pack a Python uuid.UUID as UE FGuid (16 bytes)."""
    d_a = guid_obj.time_low
    d_b = (guid_obj.time_mid << 16) | guid_obj.time_hi_version
    d_c = (guid_obj.clock_seq_hi_variant << 24
           | guid_obj.clock_seq_low << 16
           | (guid_obj.node >> 32) & 0xFFFF)
    d_d = guid_obj.node & 0xFFFFFFFF
    return struct.pack("<IIII", d_a, d_b, d_c, d_d)


def build_packet(ptype, payload, version=LIVE_SYNC_VERSION_V5, flags=0, obj_count=1):
    """Build a LiveSync packet header + payload."""
    global _seq_counter
    _seq_counter += 1
    seq = _seq_counter
    header_size = 24
    packet_size = header_size + len(payload)
    header = struct.pack('<I H B B Q I I',
                         LIVE_SYNC_MAGIC, version, ptype, flags, seq,
                         packet_size, obj_count)
    return header + payload


# ------------------------------------------------------------------
# V4 Object payload (used by PT_Create and PT_Transform)
# ------------------------------------------------------------------

def build_v4_object(guid_obj, transform, timestamp):
    """Build V4 object payload:
    guid(16) + loc(3x4) + rot(4x4) + scale(3x4) + timestamp(8)
    + parent_guid(16) + primitive_type(1)
    """
    payload = bytearray()
    payload.extend(pack_ue_fguid(guid_obj))
    payload.extend(struct.pack("<fff", transform[0], transform[1], transform[2]))
    payload.extend(struct.pack("<ffff", transform[3], transform[4],
                               transform[5], transform[6]))
    payload.extend(struct.pack("<fff", transform[7], transform[8], transform[9]))
    payload.extend(struct.pack("<d", timestamp))
    payload.extend(b'\x00' * 16)
    payload.append(LSP_CAMERA)
    return bytes(payload)


def connect_to_ue(timeout=5):
    """Connect to UE LiveSync port."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    sock.connect((UE_HOST, UE_PORT))
    sock.settimeout(None)
    return sock


def send_create_camera(sock, guid_obj, timestamp):
    """Send PT_Create (0x03) with LSP_Camera = 0x05."""
    transform = (0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0, 1.0, 1.0, 1.0)
    obj_payload = build_v4_object(guid_obj, transform, timestamp)
    pkt = build_packet(PT_CREATE, obj_payload)
    sock.sendall(pkt)
    print(f"  [CREATE] PT_Create(0x03) LSP_Camera=0x05 GUID={guid_obj}")


def send_transform(sock, guid_obj, timestamp, transform):
    """Send PT_Transform (0x01) for same GUID with given transform."""
    obj_payload = build_v4_object(guid_obj, transform, timestamp)
    pkt = build_packet(PT_TRANSFORM, obj_payload)
    sock.sendall(pkt)
    loc = transform[:3]
    print(f"  [TRANSFORM] PT_Transform(0x01) GUID={guid_obj} "
          f"loc=({loc[0]:.1f}, {loc[1]:.1f}, {loc[2]:.1f}) "
          f"rot=({transform[3]:.3f}, {transform[4]:.3f}, "
          f"{transform[5]:.3f}, {transform[6]:.3f})")


def send_active_camera(sock, guid_obj):
    """Send PT_ActiveCamera (0x15) with 28-byte payload (obj_count=0)."""
    cam_sequence = int(time.time() * 1000) & 0xFFFFFFFF
    guid_bytes = pack_ue_fguid(guid_obj)
    payload = struct.pack("<16sId", guid_bytes, cam_sequence, time.time())
    pkt = build_packet(PT_ACTIVE_CAMERA, payload, obj_count=0)
    sock.sendall(pkt)
    print(f"  [ACTIVE_CAMERA] PT_ActiveCamera(0x15) GUID={guid_obj} "
          f"seq={cam_sequence}")


def send_camera_def(sock, guid_obj, focal=50.0, sensor_w=36.0, sensor_h=24.0,
                    clip_start=10.0, clip_end=100000.0, ortho_scale=6.0,
                    flags=CAMERA_DEF_FLAG_HAS_CAMERA_DEF):
    """Send PT_CameraDef (0x1B) with 44-byte payload (obj_count=0)."""
    guid_bytes = pack_ue_fguid(guid_obj)
    fmt = "<16s f f f f f f B 3x"
    payload = struct.pack(fmt,
                          guid_bytes,
                          focal, sensor_w, sensor_h,
                          clip_start, clip_end, ortho_scale,
                          flags & 0xFF)
    pkt = build_packet(PT_CAMERA_DEF, payload, obj_count=0)
    sock.sendall(pkt)
    print(f"  [CAMERA_DEF] PT_CameraDef(0x1B) GUID={guid_obj} "
          f"focal={focal} sensor={sensor_w}x{sensor_h} "
          f"clip=({clip_start},{clip_end}) ortho={ortho_scale}")


def read_ue_log_lines(pattern, max_lines=5000):
    """Read all UE log files and return matching lines."""
    import glob
    log_dir = "/home/nguyennongngockhanh/Documents/Unreal " \
              "Projects/ProjectTemplate/Saved/Logs/"
    all_lines = []
    for log_path in glob.glob(log_dir + "ProjectTemplate*.log"):
        try:
            with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
            all_lines.extend(lines[-max_lines:])
        except Exception as e:
            print(f"  ERROR reading log {log_path}: {e}")
    all_lines = all_lines[-max_lines:]
    return [l for l in all_lines if pattern in l]


# ------------------------------------------------------------------
# Stage 7G.4 validation markers
# ------------------------------------------------------------------

STAGE7G4_MARKERS = [
    "[CAMERA][CREATE]",
    "[CAMERA][TRANSFORM_APPLY]",
    "[CAMERA][TRANSFORM_CONVERGED]",
    "[CAMERA][ACTIVE_RECV]",
    "[CAMERA][VIEW_TARGET]",
]

# ------------------------------------------------------------------
# Stage 7G.3 CameraDef markers
# ------------------------------------------------------------------

STAGE7G3_CAMERA_DEF_MARKERS = [
    "[CAMERA][DEF_RECV]",
    "[CAMERA][DEF_APPLY]",
]


def check_logs(stage4_only=True):
    """Check UE log for required diagnostic markers.

    Args:
        stage4_only: If True, check only Stage 7G.4 markers.
                     If False, also check Stage 7G.3 CameraDef markers.

    Returns:
        True if all checked markers found.
    """
    print("\n--- Log Check ---")
    if stage4_only:
        markers_to_check = {m: False for m in STAGE7G4_MARKERS}
    else:
        markers_to_check = {m: False for m in STAGE7G4_MARKERS}
        for m in STAGE7G3_CAMERA_DEF_MARKERS:
            markers_to_check[m] = False

    all_pass = True
    for marker in markers_to_check:
        found_lines = read_ue_log_lines(marker)
        markers_to_check[marker] = bool(found_lines)
        status = "FOUND" if found_lines else "MISSING"
        if not found_lines:
            all_pass = False
        print(f"  [{marker:30s}] {status} ({len(found_lines)})")
        for line in found_lines[-5:]:
            print(f"    {line.strip()}")

    # Check for malformed warnings
    malformed = read_ue_log_lines("[CAMERA][MALFORMED]")
    if malformed:
        print("\n  WARN: [CAMERA][MALFORMED] present — unexpected!")
        for line in malformed[-3:]:
            print(f"    {line.strip()}")
        all_pass = False

    if all_pass and stage4_only:
        print("\n  PASS_CAMERA_TRANSFORM_APPLY")
    elif all_pass:
        print("\n  PASS: All markers found")
    else:
        print("\n  PARTIAL — some markers missing in UE log")

    return all_pass


# ------------------------------------------------------------------
# Mode implementations
# ------------------------------------------------------------------

def mode_create_transform_active(guid, timestamp):
    """Send CREATE + TRANSFORM + ACTIVE_CAMERA on ONE connection with sleeps.

    One connection keeps the network thread alive across all sends.
    0.2s sleeps between packets ensure they arrive in different UE ticks,
    avoiding SeenThisTick dedup skipping TRANSFORM after CREATE.

    IMPORTANT: Do NOT use fresh sockets per packet. UE's network thread
    exits when a connection closes (StopNetworkThread), preventing
    subsequent connections from being accepted. One connection with
    inter-packet sleeps is the reliable approach.
    """
    print("\n=== Mode: --create-transform-active ===")
    print("  One connection with 0.2s inter-packet sleeps...")

    sock = connect_to_ue()
    print(f"  Connected to UE on {UE_HOST}:{UE_PORT}")

    # CREATE
    send_create_camera(sock, guid, timestamp)
    print("  [SLEEP] 0.2s before TRANSFORM...")
    time.sleep(0.2)

    # TRANSFORM (non-default position)
    yaw = math.radians(45.0)
    transform = (
        500.0, 0.0, 100.0,
        0.0, 0.0, math.sin(yaw / 2), math.cos(yaw / 2),
        1.0, 1.0, 1.0
    )
    send_transform(sock, guid, timestamp + 1.0, transform)
    print("  [SLEEP] 0.2s before ACTIVE_CAMERA...")
    time.sleep(0.2)

    # ACTIVE_CAMERA
    send_active_camera(sock, guid)

    sock.close()
    print("  Connection closed. Packets sent with inter-packet delay.")
    return guid


def mode_cameradef_only(guid, timestamp):
    """Send PT_CameraDef for an existing camera GUID on a fresh connection."""
    print("\n=== Mode: --cameradef-only ===")
    print(f"  Waiting for camera to exist on UE side...")

    # Poll until we can connect (camera should already exist from previous run)
    max_retries = 20
    for i in range(max_retries):
        try:
            sock = connect_to_ue()
            print(f"  Connected (attempt {i+1}). Sending CAMERA_DEF...")
            break
        except Exception as e:
            if i < max_retries - 1:
                print(f"  Not connected yet ({e}). Waiting 1s...")
                time.sleep(1)
            else:
                print(f"  ERROR: Could not connect after {max_retries} attempts")
                return None

    send_camera_def(sock, guid,
                    focal=60.0, sensor_w=32.0, sensor_h=18.0,
                    clip_start=100.0, clip_end=50000.0, ortho_scale=8.0,
                    flags=CAMERA_DEF_FLAG_HAS_CAMERA_DEF)
    sock.close()
    print("  CAMERA_DEF sent. Connection closed.")
    return guid


def mode_full_separated(guid, timestamp):
    """Run --create-transform-active, wait 3s, then --cameradef-only."""
    # Step 1: create-transform-active
    sent_guid = mode_create_transform_active(guid, timestamp)
    if not sent_guid:
        print("  ERROR: create-transform-active failed")
        return None

    # Wait for UE to process
    print(f"\n  Waiting 3s for UE to process camera...")
    time.sleep(3)

    # Step 2: cameradef-only
    mode_cameradef_only(sent_guid, timestamp + 4)
    return sent_guid


# ------------------------------------------------------------------
# Main
# ------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Phase 7G Stage 4 — Camera Transform TCP Injector")
    parser.add_argument('--guid', type=str, default=None,
                        help='Hex GUID string (default: random)')
    parser.add_argument('--create-transform-active', action='store_true',
                        help='Send CREATE + TRANSFORM + ACTIVE in one burst')
    parser.add_argument('--cameradef-only', action='store_true',
                        help='Send only PT_CameraDef for existing camera')
    parser.add_argument('--full-separated', action='store_true',
                        help='Run create-transform-active + cameradef-only with 3s pause')
    args = parser.parse_args()

    # Parse GUID
    if args.guid:
        try:
            guid = uuid.UUID(args.guid)
        except ValueError:
            print(f"  ERROR: invalid GUID '{args.guid}'")
            return 1
    else:
        guid = uuid.uuid4()

    ts = time.time()

    if args.create_transform_active:
        mode_create_transform_active(guid, ts)
        time.sleep(3.0)
        ok = check_logs(stage4_only=True)
        print("\n" + "=" * 60)
        if ok:
            print("  PASS_CAMERA_TRANSFORM_APPLY")
        else:
            print("  PARTIAL: Stage 7G.4 markers missing")
        print("=" * 60)
        return 0 if ok else 1

    elif args.cameradef_only:
        # CameraDef needs a GUID that already exists in UE
        if not args.guid:
            # Try to find the camera GUID from logs
            import glob
            log_dir = "/home/nguyennongngockhanh/Documents/Unreal " \
                      "Projects/ProjectTemplate/Saved/Logs/"
            for log_path in glob.glob(log_dir + "ProjectTemplate*.log"):
                try:
                    with open(log_path, "r", encoding="utf-8",
                              errors="replace") as f:
                        content = f.read()
                    # Look for camera GUID in CREATE log
                    import re
                    match = re.search(r'\[CAMERA\]\[CREATE\].*GUID=(\w{8}-\w{4}-\w{4}-\w{4}-\w{12})',
                                      content)
                    if match:
                        guid = uuid.UUID(match.group(1))
                        print(f"  Found existing camera GUID: {guid}")
                        break
                except Exception as e:
                    print(f"  Could not read log for GUID: {e}")
                    break
            else:
                print("  ERROR: No --guid provided and no existing camera found.")
                print("  Run --create-transform-active first, then --cameradef-only.")
                return 1
        mode_cameradef_only(guid, ts)
        time.sleep(3.0)
        ok = check_logs(stage4_only=False)
        print("\n" + "=" * 60)
        if ok:
            print("  PASS: All markers found")
        else:
            print("  PARTIAL: Some markers missing")
        print("=" * 60)
        return 0 if ok else 1

    elif args.full_separated:
        sent_guid = mode_full_separated(guid, ts)
        if not sent_guid:
            print("  ERROR: Full lifecycle failed")
            return 1
        time.sleep(3.0)
        ok = check_logs(stage4_only=False)
        print("\n" + "=" * 60)
        if ok:
            print("  PASS: All markers found")
        else:
            print("  PARTIAL: Some markers missing")
        print("=" * 60)
        return 0 if ok else 1

    else:
        # Default: send create-transform-active (non-hanging default)
        print("\n  No mode specified. Running --create-transform-active (default).")
        print("  Run --cameradef-only or --full-separated for full validation.")
        mode_create_transform_active(guid, ts)
        time.sleep(3.0)
        ok = check_logs(stage4_only=True)
        print("\n" + "=" * 60)
        if ok:
            print("  PASS_CAMERA_TRANSFORM_APPLY")
        else:
            print("  PARTIAL: Stage 7G.4 markers missing")
        print("=" * 60)
        return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main() or 0)
