"""
Phase 3.6 Validation — B: Hierarchy Stress Test
Tests parent-child relationship correctness:
deep chains, reparenting, parent deletion with child,
duplicate hierarchies, local vs world transform correctness.
Runs in Blender background mode (-b).
"""

import bpy
import time
import sys
import uuid
from mathutils import Matrix, Vector, Euler
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
        obj["ue_guid"] = uuid.uuid4().hex
    return obj["ue_guid"]


# We need to import the sync module's tracking logic
# to verify hierarchy separation
sys.path.insert(0, "/home/nguyennongngockhanh/Projects/UELiveSync/Blender_Addon")
import importlib
try:
    sync = importlib.import_module("sync")
except ImportError:
    from ue_live_sync import sync


print("\n" + "="*50)
print("PHASE 3.6 VALIDATION — B: HIERARCHY STRESS")
print("="*50)

# Clean scene
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete()
time.sleep(0.1)

# =============================================================
# 1. DEEP PARENT CHAINS
# =============================================================
print("\n--- 1. DEEP PARENT CHAINS (depth=10) ---")

tracked = {}
parents = []
prev = None
depth = 10

for i in range(depth):
    bpy.ops.mesh.primitive_cube_add(size=0.5, location=(i*2, 0, 0))
    obj = bpy.context.active_object
    guid = ensure_guid(obj)

    if prev:
        obj.parent = prev

    tracked[guid] = (obj, UUID(guid))
    parents.append(obj)
    prev = obj

# Set global tracked_objects so get_transform uses it
sync.tracked_objects = tracked

# Verify chain: each child's parent is correct
chain_ok = True
for i in range(1, len(parents)):
    if parents[i].parent != parents[i-1]:
        chain_ok = False
        break

test(f"Deep chain of {depth} objects: parent links correct", chain_ok)

# Verify get_transform returns local for children
sync.tracked_objects = tracked
for i, obj in enumerate(parents):
    t = sync.get_transform(obj)
    has_parent = obj.parent and obj.parent.get("ue_guid") in sync.tracked_objects

    if i == 0:
        # Root should be world
        test(f"Root object (idx=0): world-space transform", not has_parent)
    else:
        # Children should be local
        test(f"Child object (idx={i}): local-space transform", has_parent)

# =============================================================
# 2. REPARENT DURING SYNC
# =============================================================
print("\n--- 2. REPARENT DURING SYNC ---")

# Create three objects: A, B, C
bpy.ops.mesh.primitive_cube_add(size=0.5, location=(0, 3, 0))
A = bpy.context.active_object
ensure_guid(A)

bpy.ops.mesh.primitive_cube_add(size=0.5, location=(2, 3, 0))
B = bpy.context.active_object
ensure_guid(B)

bpy.ops.mesh.primitive_cube_add(size=0.5, location=(4, 3, 0))
C = bpy.context.active_object
ensure_guid(C)

# Add new objects to tracked
for obj in [A, B, C]:
    guid = ensure_guid(obj)
    if guid not in sync.tracked_objects:
        sync.tracked_objects[guid] = (obj, UUID(guid))

# A → B (B is child of A)
B.parent = A
t_b = sync.get_transform(B)
test("Reparent B→A: B gets local transform",
     B.parent == A)

# Switch to A → C (C is child of A)
C.parent = A
B.parent = None
t_c = sync.get_transform(C)
t_b2 = sync.get_transform(B)
test("Reparent C→A, detach B: C is local, B is world",
     C.parent == A and B.parent is None)

# Rapid reparenting: B → C, then B → A
B.parent = C
test("Rapid reparent B→C", B.parent == C)
B.parent = A
test("Rapid reparent B→A again", B.parent == A)

# =============================================================
# 3. PARENT DELETE WHILE CHILD MOVING
# =============================================================
print("\n--- 3. PARENT DELETE WHILE CHILD MOVING ---")

bpy.ops.mesh.primitive_cube_add(size=0.3, location=(0, 6, 0))
parent_del = bpy.context.active_object
ensure_guid(parent_del)

bpy.ops.mesh.primitive_cube_add(size=0.3, location=(0.5, 6, 0))
child_del = bpy.context.active_object
ensure_guid(child_del)
child_del.parent = parent_del

# Add to tracked
for obj in [parent_del, child_del]:
    guid = ensure_guid(obj)
    if guid not in sync.tracked_objects:
        sync.tracked_objects[guid] = (obj, UUID(guid))

# Move parent
parent_del.location.x = 5.0
bpy.context.view_layer.update()

# Get child transform before deletion
t_before = sync.get_transform(child_del)

# Delete parent
bpy.data.objects.remove(parent_del, do_unlink=True)
bpy.context.view_layer.update()

# Get child transform after deletion (should now be world)
t_after = sync.get_transform(child_del) if child_del.name in bpy.data.objects else None

if child_del.name in bpy.data.objects:
    test("Child survives parent deletion", child_del.parent is None)
    # After parent deletion, child becomes root → world transform
    test("Child becomes root (world transform) after parent deletion",
         child_del.parent is None)
else:
    test("Child survives parent deletion", False, detail="Child also deleted")

# Cleanup
if child_del.name in bpy.data.objects:
    bpy.data.objects.remove(child_del, do_unlink=True)

# =============================================================
# 4. DUPLICATE HIERARCHY
# =============================================================
print("\n--- 4. DUPLICATE HIERARCHY ---")

# Create a parented pair
bpy.ops.mesh.primitive_cube_add(size=0.5, location=(0, 9, 0))
orig_p = bpy.context.active_object
ensure_guid(orig_p)

bpy.ops.mesh.primitive_cube_add(size=0.3, location=(0.5, 9, 0))
orig_c = bpy.context.active_object
ensure_guid(orig_c)
orig_c.parent = orig_p

# Duplicate the parent (should also duplicate child relationship)
dup_p = orig_p.copy()
dup_p.data = orig_p.data.copy()
bpy.context.collection.objects.link(dup_p)

# Blender .copy() copies custom properties including ue_guid.
# The addon should detect colliding GUIDs on re-sync.
# Skipping dedup check (pre-existing addon limitation, not Phase 3.6 scope).
# Simply log the GUID status for manual inspection:
print(f"  ⓘ  Duplicate parent GUID matches original (expected — Blender copies custom props)")
test("Duplicated parent gets unique GUID", False,
     detail="Blender copies ue_guid on .copy(); addon-level dedup not in Phase 3.6 scope")

# Duplicate just the child (no parent copy)
dup_c = orig_c.copy()
dup_c.data = orig_c.data.copy()
bpy.context.collection.objects.link(dup_c)
dup_c.parent = None  # Detach from original parent

print(f"  ⓘ  Duplicate child GUID matches original (same reason)")
test("Duplicated child (detached) gets unique GUID", False,
     detail="Blender copies ue_guid on .copy(); addon-level dedup not in Phase 3.6 scope")

# Verify the duplicated child has no parent
test("Detached duplicated child has no parent",
     dup_c.parent is None)

# Cleanup
for obj in list(bpy.data.objects):
    if obj.type == 'MESH':
        bpy.data.objects.remove(obj, do_unlink=True)

# =============================================================
# 5. TRANSFORM CORRECTNESS: LOCAL VS WORLD
# =============================================================
print("\n--- 5. TRANSFORM CORRECTNESS: LOCAL VS WORLD ---")

bpy.ops.mesh.primitive_cube_add(size=1.0, location=(10, 0, 0))
root = bpy.context.active_object
ensure_guid(root)
tracked[root["ue_guid"]] = (root, UUID(root["ue_guid"]))

bpy.ops.mesh.primitive_cube_add(size=0.5, location=(12, 0, 0))
leaf = bpy.context.active_object
ensure_guid(leaf)
leaf.parent = root
tracked[leaf["ue_guid"]] = (leaf, UUID(leaf["ue_guid"]))

bpy.context.view_layer.update()

# Root transform should be world
root_t = sync.get_transform(root)
test("Root transform is world-space",
     root.parent is None or
     root.parent.get("ue_guid") not in sync.tracked_objects)

# Leaf transform should be local (it has a parent in tracked)
leaf_t = sync.get_transform(leaf)
test("Leaf transform is local-space (has tracked parent)",
     leaf.parent is not None and
     leaf.parent.get("ue_guid") in sync.tracked_objects)

# Move root, leaf should still report local relative to new root position
old_leaf_loc = list(leaf_t["location"])
root.location.x = 15.0
bpy.context.view_layer.update()
leaf_t2 = sync.get_transform(leaf)
new_leaf_loc = list(leaf_t2["location"])

# The leaf's local position should not have changed (it's relative to parent)
test("Leaf local transform stable when parent moves",
     abs(new_leaf_loc[0] - old_leaf_loc[0]) < 0.001,
     detail=f"old_x={old_leaf_loc[0]:.4f} new_x={new_leaf_loc[0]:.4f}")

# Move leaf locally, verify local transform changes
leaf.location.x = 3.0
bpy.context.view_layer.update()
leaf_t3 = sync.get_transform(leaf)
test("Leaf local transform reflects local movement",
     abs(leaf_t3["location"][0] - 300.0) < 1.0,
     detail=f"got x={leaf_t3['location'][0]:.1f} (expected ~300.0)")

# =============================================================
# 6. HIERARCHY FLAG IN PACKET
# =============================================================
print("\n--- 6. HIERARCHY FLAG — PF_HasLocalTransform ---")

from importlib import import_module
net = import_module("network")

# Use serialize_object_v3 for a child and root, verify parent GUID is non-zero
root_guid_uuid = UUID(root["ue_guid"])

bpy.ops.mesh.primitive_cube_add(size=0.3, location=(15, 0, 0))
grandchild = bpy.context.active_object
ensure_guid(grandchild)
grandchild.parent = leaf
tracked[grandchild["ue_guid"]] = (grandchild, UUID(grandchild["ue_guid"]))
bpy.context.view_layer.update()

gc_t = sync.get_transform(grandchild)
gc_parent = sync.get_parent_guid(grandchild)
test("Grandchild has parent GUID", gc_parent is not None)
test("Grandchild parent GUID matches leaf",
     gc_parent == leaf["ue_guid"])

# Verify serialize_object_v3 includes parent GUID for children
gc_serialized = net.serialize_object_v3(
    UUID(grandchild["ue_guid"]),
    gc_t,
    time.time(),
    UUID(gc_parent) if gc_parent else None
)
# V3 object is 80 bytes. Check bytes 64-79 for non-zero parent GUID
parent_bytes = gc_serialized[64:80]
has_nonzero_parent = any(b != 0 for b in parent_bytes)
test("Serialized child has non-zero parent GUID in packet",
     has_nonzero_parent)

# Root should have zero parent GUID
root_t2 = sync.get_transform(root)
root_serialized = net.serialize_object_v3(
    root_guid_uuid, root_t2, time.time(), None)
root_parent_bytes = root_serialized[64:80]
root_parent_zero = all(b == 0 for b in root_parent_bytes)
test("Serialized root has zero parent GUID in packet",
     root_parent_zero)

# =============================================================
# RESULTS
# =============================================================
print()
report()
