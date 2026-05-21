"""
Stress test: duplicate GUID prevention.
Verifies ensure_unique_guid prevents inherited GUID collisions.
"""

import bpy
import sys

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

sys.path.insert(0, "/home/nguyennongngockhanh/Projects/UELiveSync/Blender_Addon")
import importlib
sync = importlib.import_module("sync")

bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete()

print("\n=== DUPLICATE GUID STRESS TEST ===\n")

# Create original
bpy.ops.mesh.primitive_cube_add(size=2)
original = bpy.context.active_object
sync.ensure_guid(original)

tracked = {}
tracked[original["ue_guid"]] = (original, None)

# Duplicate single object 5×
for i in range(5):
    dup = original.copy()
    dup.data = original.data.copy()
    bpy.context.collection.objects.link(dup)

    guid = sync.ensure_unique_guid(dup, tracked)
    tracked[guid] = (dup, None)

    collisions = [k for k, v in tracked.items() if v[0] != dup and v[0].name == dup.name and dup["ue_guid"] == k]
    test(f"Single dup {i+1}: unique GUID", guid != original["ue_guid"])

# Bulk duplicate 100×
for i in range(100):
    src = list(bpy.data.objects)[i % len(bpy.data.objects)]
    dup = src.copy()
    dup.data = src.data.copy()
    bpy.context.collection.objects.link(dup)

    guid = sync.ensure_unique_guid(dup, tracked)
    tracked[guid] = (dup, None)

# Validate
all_guids = [k for k in tracked.keys()]
n_unique = len(set(all_guids))
n_total = len(all_guids)
test("No GUID collisions in tracking dict", n_unique == n_total,
     detail=f"{n_total} entries, {n_total - n_unique} duplicates")

mesh_objs = [o for o in bpy.data.objects if o.type == 'MESH']
scene_guids = [o["ue_guid"] for o in mesh_objs if "ue_guid" in o]
n_scene_guid_unique = len(set(scene_guids))
n_scene_guid_total = len(scene_guids)
test("All scene GUIDs unique", n_scene_guid_unique == n_scene_guid_total,
     detail=f"{n_scene_guid_total} scene GUIDs, {n_scene_guid_total - n_scene_guid_unique} dupes")

# Cleanup
for obj in list(bpy.data.objects):
    if obj.type == 'MESH':
        bpy.data.objects.remove(obj, do_unlink=True)

total = PASS + FAIL
print(f"\n{'='*50}")
print(f"RESULTS: {PASS}/{total} passed, {FAIL} failed")
print(f"{'='*50}")
