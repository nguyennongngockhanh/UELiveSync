#!/usr/bin/env blender-python
"""Stage 10A.5 - Active LevelSequence Runtime Validation

Connects to UE, creates a LevelSequence, adds a possessable object,
sends transform + visibility keyframes (channels 0-10).
"""
import sys, os, uuid, time

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ADDON_DIR = os.path.join(SCRIPT_DIR, "Blender_Addon")
if ADDON_DIR not in sys.path:
    sys.path.insert(0, ADDON_DIR)

print("=" * 60)
print("Stage 10A.5 - Active LevelSequence Runtime Validation")
print("=" * 60)
print(f"Addon dir: {ADDON_DIR}, exists={os.path.isdir(ADDON_DIR)}")

# Import network module directly (bypasses addon __init__ import chain)
import network as net

# Enable features
net.set_sequencer_op_enabled(True)
net.set_keyframe_enabled(True)
try: net.disconnect()
except Exception: pass

# Connect to UE
print("\n--- [TASK2] Connecting to UE ---")
net.connect("127.0.0.1", 57000)
connected = net.is_connected()
print(f"[Connect] is_connected={connected}")
if not connected: print("[10A.5] ERROR: cannot connect"); sys.exit(1)

# CREATE_SEQUENCE
print("\n--- [TASK3] Creating LevelSequence ---")
seq_num = 1
csb = net.serialize_sequencer_op_create_sequence(seq_num, time.time(), 1, 20, 24, 1)
print(f"[CREATE_SEQUENCE] payload_len={len(csb)}")
ok = net.send_sequencer_op(csb)
print(f"[CREATE_SEQUENCE] sent={ok} seq={net._sequencer_op_sequence} pkts={net._sequencer_op_packets_sent}")
time.sleep(0.5)

# Create object
print("\n--- [TASK4] Creating test object ---")
import bpy
from mathutils import Vector, Matrix

mesh = bpy.data.meshes.new("LS_VisibilityRuntime_10A5_Mesh")
mesh.from_pydata(
    [(1,1,-1),(-1,1,-1),(-1,-1,-1),(1,-1,-1),(1,1,1),(-1,1,1),(-1,-1,1),(1,-1,1)],
    [], [(0,1,2,3),(4,7,6,5),(0,4,5,1),(1,5,6,2),(2,6,7,3),(0,3,7,4)])
mesh.update()
cube = bpy.data.objects.new("LS_VisibilityRuntime_10A5", mesh)
bpy.context.collection.objects.link(cube)
bpy.context.view_layer.objects.active = cube
cube.select_set(True)
og = uuid.uuid4()
print(f"[Object] name={cube.name} GUID={og.hex}")

# Object create (PT_Create = 0x03) — must come BEFORE ADD_POSSESSABLE
print("\n--- [TASK4] Object create ---")
import sync
xf = sync.get_transform(cube)
oc = sync.serialize_object_v3(og, xf, time.time(), None, 0)
net.send_objects([oc], packet_type=0x03, version=4)
print(f"[ObjectCreate] payload_len={len(oc)}")
time.sleep(0.3)

# ADD_POSSESSABLE — after actor is created/cached
print("\n--- [TASK4] ADD_POSSESSABLE ---")
apb = net.serialize_sequencer_op_add_possessable(seq_num+1, time.time(), og, 1)
ok = net.send_sequencer_op(apb)
print(f"[ADD_POSSESSABLE] sent={ok} seq={net._sequencer_op_sequence} pkts={net._sequencer_op_packets_sent}")
time.sleep(0.3)

# Transform (PT_Transform = 0x01)
print("\n--- [TASK4] Transform ---")
ot = sync.serialize_object_v3(og, xf, time.time(), None, 0)
net.send_objects([ot], packet_type=0x01, version=4)
print(f"[ObjectTransform] payload_len={len(ot)}")
time.sleep(0.5)

# Keyframes
print("\n--- [TASK4] Building keyframes ---")
from sync import serialize_keyframe
from network import KEYFRAME_CHANNEL_VISIBILITY_VIEWPORT
from network import KEYFRAME_CHANNEL_VISIBILITY_RENDER

CH_X, CH_Y, CH_Z = 0, 1, 2

entries = [
    (og, 1, 0.0, CH_X),
    (og, 1, 0.0, CH_Y),
    (og, 1, 0.0, CH_Z),
    (og, 1, 0.0, KEYFRAME_CHANNEL_VISIBILITY_VIEWPORT),
    (og, 1, 0.0, KEYFRAME_CHANNEL_VISIBILITY_RENDER),
    (og, 10, 1.0, CH_X),
    (og, 10, 1.0, KEYFRAME_CHANNEL_VISIBILITY_VIEWPORT),
    (og, 10, 1.0, KEYFRAME_CHANNEL_VISIBILITY_RENDER),
    (og, 20, 2.0, CH_X),
    (og, 20, 0.0, KEYFRAME_CHANNEL_VISIBILITY_VIEWPORT),
    (og, 20, 0.0, KEYFRAME_CHANNEL_VISIBILITY_RENDER),
]

kfb = serialize_keyframe(seq_num+2, time.time(), entries)
net.send_objects([kfb], packet_type=0x17, version=5)
print(f"[PT_Keyframe] seq={seq_num+2} entries={len(entries)} payload_len={len(kfb)}")
print(f"[Keyframe] packets_sent=1 keyframes_sent={len(entries)}")

ch_summary = {}
for e in entries:
    guid_obj, frame, value, ch = e
    ch_summary.setdefault(ch, []).append((frame, value))
print("[Keyframe] channel_summary:")
names = {0:"CH0_LOC_X",1:"CH1_LOC_Y",2:"CH2_LOC_Z",
         9:"CH9_HIDE_VIEWPORT",10:"CH10_HIDE_RENDER"}
for ch in sorted(ch_summary.keys()):
    vals = ch_summary[ch]
    print(f"  channel={ch} ({names.get(ch,'?')}) frames={[(v[0],v[1]) for v in vals]}")

print("\n--- SUMMARY ---")
print(f"Addon path: {ADDON_DIR}")
print(f"Object GUID: {og.hex}")
print(f"prefs.keyframe_sync: True")
print(f"prefs.sequencer_ops: True")
print(f"keyframes_sent: {len(entries)}")
print(f"keyframe_packets_sent: 1")
print(f"channel_summary: {ch_summary}")
time.sleep(3)
print("\n--- Waiting for packet flush ---")
# Wait for the send queue to drain
for i in range(60):
    qd = net.get_queue_depth()
    if qd == 0:
        print(f"[Flush] send queue drained after {i}s")
        break
    if i % 10 == 0:
        print(f"[Flush] queue_depth={qd}... waiting {60-i}s")
    time.sleep(1)
else:
    print(f"[Flush] queue still has {net.get_queue_depth()} items after 60s")

print("\nDone. Check UE logs for apply evidence.")
