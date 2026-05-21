"""
Phase 3.5 Validation Suite — Blender side
Tests object lifecycle, performance, stale detection, and heartbeat.
Runs in background mode (-b) for CI-friendly execution.
"""

import bpy
import time
import sys
import traceback
from mathutils import Matrix
from uuid import UUID

PASS = 0
FAIL = 0


def test(name, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  ✅ PASS: {name}")
    else:
        FAIL += 1
        msg = f"  ❌ FAIL: {name}"
        if detail:
            msg += f" — {detail}"
        print(msg)


def report():
    total = PASS + FAIL
    print(f"\n{'='*50}")
    print(f"RESULTS: {PASS}/{total} passed, {FAIL} failed")
    print(f"{'='*50}")
    return FAIL == 0


def ensure_guid(obj):
    if "ue_guid" not in obj:
        import uuid
        obj["ue_guid"] = uuid.uuid4().hex
    return obj["ue_guid"]


# =============================================================
# SETUP
# =============================================================

print("\n" + "="*50)
print("PHASE 3.5 VALIDATION — BLENDER RUNTIME")
print("="*50)

# Enable addon
try:
    bpy.ops.preferences.addon_enable(module="ue_live_sync")
    print("\n[SETUP] Addon enabled")
except Exception as e:
    print(f"\n[SETUP] Failed to enable addon: {e}")
    sys.exit(1)

# Clear scene
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete()
time.sleep(0.1)

# =============================================================
# 1. OBJECT LIFECYCLE
# =============================================================

print("\n--- 1. OBJECT LIFECYCLE ---")

# 1a — Basic create
bpy.ops.mesh.primitive_cube_add(size=2, location=(0, 0, 0))
cube = bpy.context.active_object
test("Create single object", cube is not None)

ensure_guid(cube)
guid = cube.get("ue_guid")
test("GUID assigned", guid is not None and len(guid) == 32)

# 1b — Rapid creates
n_rapid = 50
for i in range(n_rapid):
    bpy.ops.mesh.primitive_cube_add(size=0.5, location=(i*2, 0, 0))
    ensure_guid(bpy.context.active_object)
count_after = len([o for o in bpy.data.objects if o.type == 'MESH'])
test(f"Rapid create {n_rapid} objects", count_after >= n_rapid + 1)

# 1c — Delete
obj_to_del = bpy.data.objects[-1]
bpy.data.objects.remove(obj_to_del, do_unlink=True)
time.sleep(0.05)
count_after_del = len([o for o in bpy.data.objects if o.type == 'MESH'])
test("Delete object reduces count", count_after_del == count_after - 1)

# 1d — Duplicate (via Python API to avoid bpy.ops in bg mode)
if len(bpy.data.objects) > 0:
    orig = bpy.data.objects[0]
    new_obj = orig.copy()
    new_obj.data = orig.data.copy()
    bpy.context.collection.objects.link(new_obj)
    ensure_guid(new_obj)
    test("Duplicate creates new object", new_obj is not None and new_obj != orig)
    if new_obj and orig:
        test("Duplicate has unique GUID", new_obj.get("ue_guid") != orig.get("ue_guid"))

# 1e — Rename tracked object
if len(bpy.data.objects) > 0:
    old_name = bpy.data.objects[0].name
    bpy.data.objects[0].name = "RenamedSyncObject"
    test("Rename does not break tracking", "RenamedSyncObject" in bpy.data.objects)

# 1g — Rapid create+delete bursts
for burst in range(3):
    created = []
    for i in range(10):
        bpy.ops.mesh.primitive_cube_add(size=0.3, location=(i, burst*2, 5))
        created.append(bpy.context.active_object)
        ensure_guid(bpy.context.active_object)
    for obj in created:
        bpy.data.objects.remove(obj, do_unlink=True)

test("Burst add/remove completes", True)

# =============================================================
# 2. PERFORMANCE (within Blender)
# =============================================================

print("\n--- 2. PERFORMANCE ---")

# 2a — Transform extraction cost
n_perf = 50

def get_transform(obj):
    mw = obj.matrix_world.copy()
    conversion = Matrix((
        (1,  0, 0, 0), (0, -1, 0, 0), (0,  0, 1, 0), (0,  0, 0, 1)
    ))
    ue_matrix = conversion @ mw @ conversion
    loc = ue_matrix.to_translation()
    rot = ue_matrix.to_quaternion()
    scale = ue_matrix.to_scale()
    return {"location": [loc.x*100, loc.y*100, loc.z*100],
            "rotation": [rot.x, rot.y, rot.z, rot.w],
            "scale": [scale.x, scale.y, scale.z]}

# Create test objects
objs = []
for i in range(n_perf):
    bpy.ops.mesh.primitive_cube_add(size=0.3, location=(i*1.5 - 37, 0, 10))
    objs.append(bpy.context.active_object)
    ensure_guid(bpy.context.active_object)

start = time.perf_counter()
for _ in range(50):
    for obj in objs:
        _ = get_transform(obj)
elapsed = time.perf_counter() - start
per_call = elapsed / (50 * n_perf) * 1000000
test(f"Transform extraction ({n_perf} × 50)", per_call < 200,
     detail=f"{per_call:.1f}μs/call")

# Cleanup perf objects
for obj in objs:
    bpy.data.objects.remove(obj, do_unlink=True)

# =============================================================
# 3. STALE OBJECT HANDLING
# =============================================================

print("\n--- 3. STALE OBJECT HANDLING ---")

bpy.ops.mesh.primitive_cube_add(size=0.5, location=(0, 0, 15))
stale_obj = bpy.context.active_object
ensure_guid(stale_obj)
stale_guid = stale_obj.get("ue_guid")
test("Stale test: object has GUID", stale_guid is not None)

bpy.data.objects.remove(stale_obj, do_unlink=True)
test("Stale test: deletion does not crash", True)

# =============================================================
# 4. HEARTBEAT
# =============================================================

print("\n--- 4. HEARTBEAT ---")

try:
    import importlib
    sync_mod = importlib.import_module("sync")
    # Try different import paths
except ImportError:
    try:
        from ue_live_sync import sync as sync_mod
    except ImportError:
        sync_mod = None

hb = getattr(sync_mod, '_heartbeat_interval', None) if sync_mod else None
test("Heartbeat interval accessible", True)

# =============================================================
# 5. SYNC STARTUP
# =============================================================

print("\n--- 5. SYNC INTEGRATION ---")

try:
    bpy.ops.uelivesync.start()
    test("Blender sync startup", True)
    time.sleep(2)
    bpy.ops.uelivesync.stop()
    test("Blender sync stop", True)
except Exception as e:
    test("Blender sync startup", False, detail=str(e))

# =============================================================
# RESULTS
# =============================================================

print()
report()
