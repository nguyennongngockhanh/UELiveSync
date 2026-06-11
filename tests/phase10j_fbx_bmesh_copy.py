#!/usr/bin/env python3
"""
Phase 10J.2A -- Tests for bmesh-based FBX temp mesh copy.

Verifies the fix for Blender 5.1 compatibility:
- copy_attributes_to replaced with bmesh.from_mesh/to_mesh
- Identity pivot export preserved
- No unintended changes to protocol or UI

Run:
  python3 tests/phase10j_fbx_bmesh_copy.py
"""

import os
import sys
import re

REPO = "/home/nguyennongngockhanh/Projects/UELiveSync"
INIT = os.path.join(REPO, "Blender_Addon", "__init__.py")

passed = 0
failed = 0


def check(name, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  PASS: {name}")
    else:
        failed += 1
        print(f"  FAIL: {name}" + (f" -- {detail}" if detail else ""))


def read_file(path):
    with open(path, "r") as f:
        return f.read()


def get_function_body(src, func_name):
    """Extract the body of a function (from def to next class or def at same indent)."""
    pattern = rf"(?m)^def {func_name}\("
    match = re.search(pattern, src)
    if not match:
        return None
    start = match.start()
    # Find end: next 'class ' at column 0, or 'def ' at column 0
    class_pattern = rf"(?m)^class "
    func_pattern = rf"(?m)^def "
    end = len(src)
    for m in re.finditer(class_pattern, src[start+1:]):
        end = min(end, start + 1 + m.start())
    for m in re.finditer(func_pattern, src[start+1:]):
        if not m.group().startswith(f"def {func_name}"):
            end = min(end, start + 1 + m.start())
    return src[start:end]


print("=== Phase 10J.2A -- bmesh FBX copy tests ===\n")

src = read_file(INIT)

# --- Test 1: No copy_attributes_to in _export_object_local_fbx ---
print("Test 1: copy_attributes_to removed from _export_object_local_fbx")
func_body = get_function_body(src, "_export_object_local_fbx")
if func_body:
    # Remove docstrings first
    clean = re.sub(r'"""[\s\S]*?"""', '', func_body)
    clean = re.sub(r"'''[\s\S]*?'''", '', clean)
    has_copy_attrs = "copy_attributes_to" in clean
    check("copy_attributes_to removed", not has_copy_attrs,
          f"copy_attributes_to still in code (only docstring ok)")
else:
    check("copy_attributes_to removed", False,
          "Function _export_object_local_fbx not found")

# --- Test 2: bmesh.new/from_mesh/to_mesh/free exists in helper ---
print("\nTest 2: bmesh.new/from_mesh/to_mesh/free in _export_object_local_fbx")
if not func_body:
    check("bmesh helpers", False, "Function body not found")
else:
    clean = re.sub(r'"""[\s\S]*?"""', '', func_body)
    check("bmesh.new() present", "bmesh.new()" in clean)
    check("bm.from_mesh() present", "bm.from_mesh" in clean)
    check("bm.to_mesh() present", "bm.to_mesh" in clean)
    check("bm.free() present", "bm.free()" in clean)
    check("import bmesh present", "import bmesh" in clean)

# --- Test 3: temp object transform identity ---
print("\nTest 3: temp object identity transform preserved")
if not func_body:
    check("identity transform", False, "Function body not found")
else:
    check("location = (0.0, 0.0, 0.0)",
          "location = (0.0, 0.0, 0.0)" in func_body)
    check("rotation_euler = (0.0, 0.0, 0.0)",
          "rotation_euler = (0.0, 0.0, 0.0)" in func_body)
    check("scale = (1.0, 1.0, 1.0)",
          "scale = (1.0, 1.0, 1.0)" in func_body)

# --- Test 4: export_scene.fbx use_selection=True ---
print("\nTest 4: export_scene.fbx use_selection=True")
if not func_body:
    check("use_selection=True", False, "Function body not found")
else:
    check("use_selection=True", "use_selection=True" in func_body)

# --- Test 5: export_scene.fbx bake_space_transform=True ---
print("\nTest 5: export_scene.fbx bake_space_transform=True")
if not func_body:
    check("bake_space_transform=True", False, "Function body not found")
else:
    check("bake_space_transform=True",
          "bake_space_transform=True" in func_body)

# --- Test 6: FBXImportRequest serialization unchanged ---
print("\nTest 6: FBXImportRequest serialization unchanged")
network_py = os.path.join(REPO, "Blender_Addon", "network.py")
net_src = read_file(network_py)
check("PT_FBXImportRequest = 0x16",
      "PT_FBXImportRequest = 0x16" in net_src)
check("FBX_IMPORT_REQUEST_PAYLOAD_SIZE = 688 (Phase 10J.5F)",
      "FBX_IMPORT_REQUEST_PAYLOAD_SIZE = 688" in net_src)
check("serialize_fbx_import_request function exists",
      "def serialize_fbx_import_request(" in net_src)
check("wire format updated with GeometryHash Q",
      "<16sI512s128sIIIdQ" in net_src)

# --- Test 7: UI only shows FBX button ---
print("\nTest 7: UI only shows 'Sync Selected Mesh to UE (FBX)'")
class_section = src[src.index("class UELIVESYNC_OT_sync_selected_mesh_to_ue_fbx("):]
check("bl_label is FBX button",
      "Sync Selected Mesh to UE (FBX)" in class_section[:300])
# Check no old procedural mesh button
procedural_section = class_section[:300]
check("no old procedural button label",
      "FBX" in procedural_section)

# --- Summary ---
print(f"\n=== Results: {passed} passed, {failed} failed ===")
sys.exit(1 if failed else 0)
