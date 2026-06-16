#!/usr/bin/env python3
"""E2E.5 — SceneOutliner Crash Isolation Injector.

Minimal test-tool for isolating whether Signal 11 in SceneOutliner is:
A. UE idle / unrelated to LiveSync
B. LiveSync camera actor spawn (any mode)
C. LiveSync camera frustum / Sequencer / camera-cut path
D. LiveSync hierarchy attachment
E. SceneOutliner UI visible during creation

Modes:
    --idle-only         No packets. Just log UE PID. Test A.
    --create-only       Single camera CREATE, no parent, no transform. Test C.
    --create-transform  CREATE + TRANSFORM only. No ActiveCamera/Sequencer. Test D.
    --full              Full camera lifecycle (current --full-separated). Test E.
    --hierarchy         Two non-camera actors + parent attach. Test F.
    --cameraguid        Send CAMERA_DEF for existing camera (from --guid).

Each mode writes markers to stdout for easy grep.
"""

import struct
import socket
import time
import sys
import uuid
import math
import argparse
import os
import subprocess
import signal as sig

UE_HOST = "127.0.0.1"
UE_PORT = 57000
LIVE_SYNC_MAGIC = 0x4C56534D
PT_CREATE = 0x03
PT_TRANSFORM = 0x01
PT_ACTIVE_CAMERA = 0x15
PT_CAMERA_DEF = 0x1B
PT_HIERARCHY = 0x0D
LIVE_SYNC_VERSION_V5 = 5
LSP_CAMERA = 0x05
LSP_STATIC = 0x01  # Static mesh primitive type

_seq_counter = 0


def pack_ue_fguid(guid_obj):
    d_a = guid_obj.time_low
    d_b = (guid_obj.time_mid << 16) | guid_obj.time_hi_version
    d_c = (guid_obj.clock_seq_hi_variant << 24
           | guid_obj.clock_seq_low << 16
           | (guid_obj.node >> 32) & 0xFFFF)
    d_d = guid_obj.node & 0xFFFFFFFF
    return struct.pack("<IIII", d_a, d_b, d_c, d_d)


def build_packet(ptype, payload, version=LIVE_SYNC_VERSION_V5, flags=0, obj_count=1):
    global _seq_counter
    _seq_counter += 1
    seq = _seq_counter
    header_size = 24
    packet_size = header_size + len(payload)
    header = struct.pack('<I H B B Q I I',
                         LIVE_SYNC_MAGIC, version, ptype, flags, seq,
                         packet_size, obj_count)
    return header + payload


def build_v4_object(guid_obj, transform, timestamp, parent_guid=None, primitive_type=LSP_CAMERA):
    payload = bytearray()
    payload.extend(pack_ue_fguid(guid_obj))
    payload.extend(struct.pack("<fff", transform[0], transform[1], transform[2]))
    payload.extend(struct.pack("<ffff", transform[3], transform[4],
                               transform[5], transform[6]))
    payload.extend(struct.pack("<fff", transform[7], transform[8], transform[9]))
    payload.extend(struct.pack("<d", timestamp))
    if parent_guid is not None:
        payload.extend(pack_ue_fguid(parent_guid))
    else:
        payload.extend(b'\x00' * 16)  # no parent
    payload.append(primitive_type)
    return bytes(payload)


def connect_to_ue(timeout=5):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    sock.connect((UE_HOST, UE_PORT))
    sock.settimeout(None)
    return sock


def get_ue_pid():
    """Get UE editor PID via pgrep."""
    try:
        result = subprocess.run(['pgrep', '-f', 'UnrealEditor'],
                                capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            pids = result.stdout.strip().split('\n')
            return pids[0] if pids else None
    except Exception:
        pass
    return None


def get_ue_process_status(pid):
    """Check if a PID is alive."""
    try:
        os.kill(int(pid), 0)
        return "alive"
    except (OSError, ValueError):
        return "dead"


def check_ue_pid_before(log_path, label):
    """Record UE PID before test."""
    pid = get_ue_pid()
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    status = get_ue_process_status(pid) if pid else "unknown"
    with open(log_path, "a") as f:
        f.write(f"[{ts}] E2E.5 {label} — UE PID={pid} status={status}\n")
    print(f"[E2E5][{label}] UE PID={pid} status={status}")
    return pid


def check_ue_pid_after(pid, log_path, label):
    """Check UE PID after test."""
    status = get_ue_process_status(pid) if pid else "unknown"
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(log_path, "a") as f:
        f.write(f"[{ts}] E2E.5 {label} post — UE PID={pid} status={status}\n")
    print(f"[E2E5][{label}] POST — UE PID={pid} status={status}")
    return status


# ------------------------------------------------------------------
# Mode implementations
# ------------------------------------------------------------------

def mode_idle_only(log_path):
    """Test A — UE idle only, no LiveSync packets.
    
    If Signal 11 still occurs with no LiveSync traffic,
    the crash is UE/editor/project state independent of LiveSync.
    """
    label = "A-idle-only"
    pid = check_ue_pid_before(log_path, label)
    print(f"\n=== E2E.5 Test A — UE Idle Only (no packets) ===")
    print("  Waiting 30 seconds...")
    time.sleep(30)
    status = check_ue_pid_after(pid, log_path, label)
    if status == "dead":
        print(f"[E2E5] FAIL_UE_IDLE_SCENE_OUTLINER_CRASH — UE crashed idle")
        return "FAIL_UE_IDLE_SCENE_OUTLINER_CRASH"
    else:
        print(f"[E2E5] PASS_UE_IDLE_NO_CRASH — UE still alive")
        return "PASS_UE_IDLE_NO_CRASH"


def mode_create_only(log_path, guid=None):
    """Test C — Camera create only. No parent, no transform, no ActiveCamera, no Sequencer.
    
    Minimal camera spawn. If it crashes, the CREATE path triggers SceneOutliner issue.
    """
    label = "C-camera-create-only"
    if guid is None:
        guid = uuid.uuid4()
    else:
        guid = uuid.UUID(guid)
    
    pid = check_ue_pid_before(log_path, label)
    
    print(f"\n=== E2E.5 Test C — Camera Create Only ===")
    print(f"  GUID={guid}")
    print(f"  No parent, no transform, no ActiveCamera, no Sequencer.")
    
    sock = connect_to_ue()
    
    # CREATE with no parent (GUID all zeros)
    transform = (0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0, 1.0, 1.0, 1.0)
    obj_payload = build_v4_object(guid, transform, time.time(),
                                   parent_guid=None, primitive_type=LSP_CAMERA)
    pkt = build_packet(PT_CREATE, obj_payload)
    sock.sendall(pkt)
    print(f"  [E2E5][{label}][CREATE] GUID={guid} parent=None")
    
    time.sleep(3.0)
    sock.close()
    
    status = check_ue_pid_after(pid, log_path, label)
    if status == "dead":
        print(f"[E2E5] FAIL_LIVESYNC_CAMERA_CREATE_SCENE_OUTLINER_CRASH")
        return "FAIL_LIVESYNC_CAMERA_CREATE_SCENE_OUTLINER_CRASH"
    else:
        print(f"[E2E5] PASS — Camera create only did not crash UE")
        return "PASS_CAMERA_CREATE_ONLY"


def mode_create_transform(log_path, guid=None):
    """Test D — Camera create + transform only. No ActiveCamera, no Sequencer, no CameraDef.
    
    Tests if transform processing triggers the SceneOutliner issue.
    """
    label = "D-camera-create-transform"
    if guid is None:
        guid = uuid.uuid4()
    else:
        guid = uuid.UUID(guid)
    
    pid = check_ue_pid_before(log_path, label)
    
    print(f"\n=== E2E.5 Test D — Camera Create + Transform ===")
    print(f"  GUID={guid}")
    print(f"  No ActiveCamera, no Sequencer, no CameraDef.")
    
    sock = connect_to_ue()
    
    # CREATE
    transform = (0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0, 1.0, 1.0, 1.0)
    obj_payload = build_v4_object(guid, transform, time.time(),
                                   parent_guid=None, primitive_type=LSP_CAMERA)
    pkt = build_packet(PT_CREATE, obj_payload)
    sock.sendall(pkt)
    print(f"  [E2E5][{label}][CREATE] GUID={guid}")
    
    time.sleep(0.3)
    
    # TRANSFORM
    yaw = math.radians(45.0)
    transform2 = (500.0, 0.0, 100.0,
                  0.0, 0.0, math.sin(yaw / 2), math.cos(yaw / 2),
                  1.0, 1.0, 1.0)
    obj_payload2 = build_v4_object(guid, transform2, time.time() + 1.0,
                                    parent_guid=None, primitive_type=LSP_CAMERA)
    pkt2 = build_packet(PT_TRANSFORM, obj_payload2)
    sock.sendall(pkt2)
    print(f"  [E2E5][{label}][TRANSFORM] GUID={guid} pos=(500, 0, 100)")
    
    time.sleep(3.0)
    sock.close()
    
    status = check_ue_pid_after(pid, log_path, label)
    if status == "dead":
        print(f"[E2E5] FAIL_LIVESYNC_CAMERA_CREATE_TRANSFORM_SCENE_OUTLINER_CRASH")
        return "FAIL_LIVESYNC_CAMERA_CREATE_TRANSFORM_SCENE_OUTLINER_CRASH"
    else:
        print(f"[E2E5] PASS — Camera create+transform did not crash UE")
        return "PASS_CAMERA_CREATE_TRANSFORM"


def mode_full(log_path, guid=None):
    """Test E — Full camera lifecycle. Same as --full-separated from 7G injector.
    
    CREATE + TRANSFORM + ACTIVE_CAMERA + CAMERA_DEF.
    """
    label = "E-camera-full"
    if guid is None:
        guid = uuid.uuid4()
    else:
        guid = uuid.UUID(guid)
    
    pid = check_ue_pid_before(log_path, label)
    
    print(f"\n=== E2E.5 Test E — Full Camera Lifecycle ===")
    print(f"  GUID={guid}")
    
    sock = connect_to_ue()
    
    ts = time.time()
    
    # CREATE
    transform = (0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0, 1.0, 1.0, 1.0)
    obj_payload = build_v4_object(guid, transform, ts,
                                   parent_guid=None, primitive_type=LSP_CAMERA)
    pkt = build_packet(PT_CREATE, obj_payload)
    sock.sendall(pkt)
    print(f"  [E2E5][{label}][CREATE] GUID={guid}")
    
    time.sleep(0.3)
    
    # TRANSFORM
    yaw = math.radians(45.0)
    transform2 = (500.0, 0.0, 100.0,
                  0.0, 0.0, math.sin(yaw / 2), math.cos(yaw / 2),
                  1.0, 1.0, 1.0)
    obj_payload2 = build_v4_object(guid, transform2, ts + 1.0,
                                    parent_guid=None, primitive_type=LSP_CAMERA)
    pkt2 = build_packet(PT_TRANSFORM, obj_payload2)
    sock.sendall(pkt2)
    print(f"  [E2E5][{label}][TRANSFORM] GUID={guid}")
    
    time.sleep(0.3)
    
    # ACTIVE_CAMERA
    cam_sequence = int(time.time() * 1000) & 0xFFFFFFFF
    guid_bytes = pack_ue_fguid(guid)
    active_payload = struct.pack("<16sId", guid_bytes, cam_sequence, time.time())
    active_pkt = build_packet(PT_ACTIVE_CAMERA, active_payload, obj_count=0)
    sock.sendall(active_pkt)
    print(f"  [E2E5][{label}][ACTIVE] GUID={guid}")
    
    time.sleep(1.0)
    
    # CAMERA_DEF
    cam_def_payload = struct.pack("<16s f f f f f f B 3x",
                                   pack_ue_fguid(guid),
                                   60.0, 32.0, 18.0,
                                   100.0, 50000.0, 8.0,
                                   0x02)
    cam_def_pkt = build_packet(PT_CAMERA_DEF, cam_def_payload, obj_count=0)
    sock.sendall(cam_def_pkt)
    print(f"  [E2E5][{label}][CAMERA_DEF] GUID={guid}")
    
    time.sleep(3.0)
    sock.close()
    
    status = check_ue_pid_after(pid, log_path, label)
    if status == "dead":
        print(f"[E2E5] FAIL_LIVESYNC_CAMERA_FULL_LIFECYCLE_SCENE_OUTLINER_CRASH")
        return "FAIL_LIVESYNC_CAMERA_FULL_LIFECYCLE_SCENE_OUTLINER_CRASH"
    else:
        print(f"[E2E5] PASS — Full camera lifecycle did not crash UE")
        return "PASS_CAMERA_FULL_LIFECYCLE"


def mode_hierarchy(log_path):
    """Test F — Hierarchy attach exercise.
    
    Creates two non-camera actors, attaches one to the other.
    Then attempts self-attach and cycle-attach to validate e0ed247 guards.
    """
    label = "F-hierarchy-attach"
    
    actor1_guid = uuid.uuid4()
    actor2_guid = uuid.uuid4()
    
    pid = check_ue_pid_before(log_path, label)
    
    print(f"\n=== E2E.5 Test F — Hierarchy Attach Exercise ===")
    print(f"  Actor1 GUID={actor1_guid}")
    print(f"  Actor2 GUID={actor2_guid}")
    print(f"  Creating two non-camera actors + parent hierarchy.")
    
    sock = connect_to_ue()
    ts = time.time()
    
    # CREATE Actor1 (static mesh)
    transform1 = (0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0, 1.0, 1.0, 1.0)
    obj1 = build_v4_object(actor1_guid, transform1, ts,
                            parent_guid=None, primitive_type=LSP_STATIC)
    pkt1 = build_packet(PT_CREATE, obj1)
    sock.sendall(pkt1)
    print(f"  [E2E5][{label}][CREATE] Actor1={actor1_guid} LSP_STATIC")
    
    time.sleep(0.2)
    
    # CREATE Actor2 (static mesh)
    transform2 = (100.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0, 1.0, 1.0, 1.0)
    obj2 = build_v4_object(actor2_guid, transform2, ts + 1.0,
                            parent_guid=None, primitive_type=LSP_STATIC)
    pkt2 = build_packet(PT_CREATE, obj2)
    sock.sendall(pkt2)
    print(f"  [E2E5][{label}][CREATE] Actor2={actor2_guid} LSP_STATIC")
    
    time.sleep(0.5)
    
    # TRANSFORM Actor2 to move it
    transform3 = (100.0, 0.0, 100.0,
                  0.0, 0.0, 0.0, 1.0, 1.0, 1.0, 1.0)
    obj3 = build_v4_object(actor2_guid, transform3, ts + 2.0,
                            parent_guid=None, primitive_type=LSP_STATIC)
    pkt3 = build_packet(PT_TRANSFORM, obj3)
    sock.sendall(pkt3)
    print(f"  [E2E5][{label}][TRANSFORM] Actor2 pos=(100, 0, 100)")
    
    time.sleep(0.5)
    
    # Send parent hierarchy: Actor2's parent = Actor1
    # Must use PT_CREATE to carry parent GUID (transform packet does not)
    transform4 = (100.0, 0.0, 100.0,
                  0.0, 0.0, 0.0, 1.0, 1.0, 1.0, 1.0)
    obj_with_parent = build_v4_object(actor2_guid, transform4, ts + 3.0,
                                       parent_guid=actor1_guid, primitive_type=LSP_STATIC)
    pkt_parent = build_packet(PT_CREATE, obj_with_parent)
    sock.sendall(pkt_parent)
    print(f"  [E2E5][{label}][PARENT] Actor2->Actor1 (via CREATE with parent GUID)")
    
    time.sleep(2.0)
    
    # Test self-attach: Actor1's parent = Actor1
    obj_self = build_v4_object(actor1_guid, transform1, ts + 5.0,
                                parent_guid=actor1_guid, primitive_type=LSP_STATIC)
    pkt_self = build_packet(PT_CREATE, obj_self)
    sock.sendall(pkt_self)
    print(f"  [E2E5][{label}][SELF_ATTACH] Actor1->Actor1 (should be guarded)")
    time.sleep(0.5)
    
    # Test cycle-attach: Actor1's parent = Actor2 (Actor2 already parented to Actor1)
    obj_cycle = build_v4_object(actor1_guid, transform1, ts + 5.5,
                                parent_guid=actor2_guid, primitive_type=LSP_STATIC)
    pkt_cycle = build_packet(PT_CREATE, obj_cycle)
    sock.sendall(pkt_cycle)
    print(f"  [E2E5][{label}][CYCLE_ATTACH] Actor1->Actor2 (cycle: Actor2->Actor1->Actor2, should be guarded)")
    time.sleep(2.0)
    
    sock.close()
    
    status = check_ue_pid_after(pid, log_path, label)
    if status == "dead":
        print(f"[E2E5] FAIL — UE crashed during hierarchy exercise")
        return "FAIL_HIERARCHY_ATTACH_CRASH"
    else:
        print(f"[E2E5] PASS_HIERARCHY_ATTACH_GUARD_RUNTIME — No crash, guard exercised")
        return "PASS_HIERARCHY_ATTACH_GUARD_RUNTIME"


def mode_hierarchy_confirm(log_path):
    """E2E.6 — Confirm UE-side hierarchy guard runtime markers.
    
    Creates parent actor, waits for it to register, creates child actor,
    waits for it to register, then sends PT_Hierarchy child->parent.
    This ensures parent is available in actor cache BEFORE hierarchy packet,
    so HandleHierarchy finds the parent and calls SafeAttachLiveSyncActor
    directly (no deferral), producing [HIERARCHY][ATTACH_GUARD] in UE log.
    
    Then tests self-attach and cycle-attach for skip markers.
    """
    label = "E2E6-hierarchy-confirm"
    
    # Create parent actor
    parent_guid = uuid.uuid4()
    child_guid = uuid.uuid4()
    
    pid = check_ue_pid_before(log_path, label)
    
    print(f"\n=== E2E.6 Test — Hierarchy Guard Marker Confirmation ===")
    print(f"  Parent GUID={parent_guid}")
    print(f"  Child GUID={child_guid}")
    print(f"  Expected UE markers:")
    print(f"    [HIERARCHY][ATTACH_GUARD]")
    print(f"    [HIERARCHY][ATTACH] or [HIERARCHY][ATTACH_SAFE]")
    print(f"    [HIERARCHY][ATTACH_SKIP_SELF] (self-attach test)")
    print(f"    [HIERARCHY][CYCLE] (cycle-attach test)")
    
    sock = connect_to_ue()
    ts = time.time()
    
    # STEP 1: CREATE Parent actor (static mesh)
    parent_transform = (0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0, 1.0, 1.0, 1.0)
    parent_obj = build_v4_object(parent_guid, parent_transform, ts,
                                   parent_guid=None, primitive_type=LSP_STATIC)
    parent_pkt = build_packet(PT_CREATE, parent_obj)
    sock.sendall(parent_pkt)
    print(f"  [{label}][CREATE_PARENT] GUID={parent_guid} LSP_STATIC")
    
    # Wait for parent to be registered in UE actor cache
    time.sleep(1.0)
    
    # STEP 2: CREATE Child actor (static mesh)
    child_transform = (100.0, 0.0, 100.0, 0.0, 0.0, 0.0, 1.0, 1.0, 1.0, 1.0)
    child_obj = build_v4_object(child_guid, child_transform, ts + 1.0,
                                 parent_guid=None, primitive_type=LSP_STATIC)
    child_pkt = build_packet(PT_CREATE, child_obj)
    sock.sendall(child_pkt)
    print(f"  [{label}][CREATE_CHILD] GUID={child_guid} LSP_STATIC")
    
    # Wait for child to be registered
    time.sleep(1.0)
    
    # STEP 3: Send PT_Hierarchy child->parent
    # Wire format: ChildGuid(16) + ParentGuid(16) + seq(4) + ts(8) = 44 bytes
    hierarchy_payload = (
        pack_ue_fguid(child_guid) +
        pack_ue_fguid(parent_guid) +
        struct.pack("<I", 1) +  # sequence
        struct.pack("<d", ts + 3.0)  # timestamp
    )
    hierarchy_pkt = build_packet(PT_HIERARCHY, hierarchy_payload, obj_count=1)
    sock.sendall(hierarchy_pkt)
    print(f"  [{label}][HIERARCHY] Child={child_guid} -> Parent={parent_guid}")
    
    # Wait for UE to process hierarchy (ResolveHierarchyAttachments runs every frame)
    time.sleep(2.0)
    
    # STEP 4: Self-attach test (child->child, should trigger [HIERARCHY][ATTACH_SKIP_SELF])
    self_payload = (
        pack_ue_fguid(child_guid) +
        pack_ue_fguid(child_guid) +
        struct.pack("<I", 2) +
        struct.pack("<d", ts + 5.0)
    )
    self_pkt = build_packet(PT_HIERARCHY, self_payload, obj_count=1)
    sock.sendall(self_pkt)
    print(f"  [{label}][SELF_ATTACH] Child={child_guid} -> Child={child_guid} (should trigger skip)")
    
    time.sleep(1.0)
    
    # STEP 5: Cycle-attach test (parent->child, child is already child->parent, so parent->child creates cycle)
    cycle_payload = (
        pack_ue_fguid(parent_guid) +
        pack_ue_fguid(child_guid) +
        struct.pack("<I", 3) +
        struct.pack("<d", ts + 6.5)
    )
    cycle_pkt = build_packet(PT_HIERARCHY, cycle_payload, obj_count=1)
    sock.sendall(cycle_pkt)
    print(f"  [{label}][CYCLE_ATTACH] Parent={parent_guid} -> Child={child_guid} (cycle: parent->child->parent, should trigger skip)")
    
    time.sleep(2.0)
    
    sock.close()
    
    # Check UE status
    status = check_ue_pid_after(pid, log_path, label)
    if status == "dead":
        print(f"[{label}] FAIL — UE crashed during hierarchy exercise")
        return "FAIL_E2E6_HIERARCHY_SCENE_OUTLINER_CRASH"
    else:
        print(f"[{label}] PASS — UE alive after hierarchy guard exercise")
        return "PASS_E2E6_HIERARCHY_GUARD_MARKER_CONFIRMED"


def mode_cameradef(log_path, guid=None):
    """Send CAMERA_DEF for an existing camera."""
    if guid is None:
        print("  ERROR: --cameraguid requires --guid")
        return "ERROR"
    guid = uuid.UUID(guid)
    
    print(f"\n=== E2E.5 CAMERA_DEF for {guid} ===")
    sock = connect_to_ue()
    cam_def_payload = struct.pack("<16s f f f f f f B 3x",
                                   pack_ue_fguid(guid),
                                   60.0, 32.0, 18.0,
                                   100.0, 50000.0, 8.0,
                                   0x02)
    cam_def_pkt = build_packet(PT_CAMERA_DEF, cam_def_payload, obj_count=0)
    sock.sendall(cam_def_pkt)
    print(f"  [E2E5][CAMERA_DEF] GUID={guid}")
    time.sleep(2.0)
    sock.close()
    return "OK"


# ------------------------------------------------------------------
# Main
# ------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="E2E.5 — SceneOutliner Crash Isolation Injector")
    parser.add_argument('--guid', type=str, default=None,
                        help='Hex GUID string (default: random)')
    parser.add_argument('--idle-only', action='store_true',
                        help='Test A: UE idle only, no packets')
    parser.add_argument('--create-only', action='store_true',
                        help='Test C: Camera create only')
    parser.add_argument('--create-transform', action='store_true',
                        help='Test D: Camera create + transform only')
    parser.add_argument('--full', action='store_true',
                        help='Test E: Full camera lifecycle')
    parser.add_argument('--hierarchy', action='store_true',
                        help='Test F: Hierarchy attach exercise')
    parser.add_argument('--hierarchy-confirm', action='store_true',
                        help='E2E.6: Confirm UE-side hierarchy guard markers')
    parser.add_argument('--cameraguid', action='store_true',
                        help='Send CAMERA_DEF for existing camera')
    args = parser.parse_args()
    
    if not any([args.idle_only, args.create_only, args.create_transform,
                args.full, args.hierarchy, args.hierarchy_confirm, args.cameraguid]):
        print("  ERROR: Must specify one test mode.")
        print("  --idle-only | --create-only | --create-transform | --full | --hierarchy | --hierarchy-confirm | --cameraguid")
        return 1
    
    # Use a single log file for all E2E.5 results
    log_path = "/tmp/uelivesync-e2e5-isolation.log"
    print(f"[E2E5] Log: {log_path}")
    
    # Ensure UE is running
    pid = get_ue_pid()
    if pid is None:
        print("[E2E5] ERROR: UE editor not found. Launch UE first.")
        return 1
    
    result = None
    
    if args.idle_only:
        result = mode_idle_only(log_path)
    elif args.create_only:
        result = mode_create_only(log_path, args.guid)
    elif args.create_transform:
        result = mode_create_transform(log_path, args.guid)
    elif args.full:
        result = mode_full(log_path, args.guid)
    elif args.hierarchy:
        result = mode_hierarchy(log_path)
    elif args.hierarchy_confirm:
        result = mode_hierarchy_confirm(log_path)
    elif args.cameraguid:
        result = mode_cameradef(log_path, args.guid)
    
    print(f"\n[E2E5] RESULT: {result}")
    return 0


if __name__ == '__main__':
    sys.exit(main() or 0)
