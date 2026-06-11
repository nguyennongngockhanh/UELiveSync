#!/usr/bin/env python3
"""
Phase 10I — FBX Actor Transform Continuity and UI fix tests.

Verifies:
  1. LiveSyncFBXImporter.cpp calls OnActorCached in update path.
  2. LiveSyncFBXImporter.cpp applies existing transform on spawn.
  3. LiveSyncFBXImporter.cpp destroys old non-FBX actor for same GUID.
  4. Blender UI panel no longer draws the non-FBX mesh sync button.
  5. FBX button still present.
  6. Old operator class still registered (for compatibility).
  7. Protocol unchanged (no new packet type, no format change).
"""

import sys
import os

PASS = 0
FAIL = 0


def test(name, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
    else:
        FAIL += 1
        msg = f"  FAIL: {name}"
        if detail:
            msg += f" — {detail}"
        print(msg)


def banner(title):
    width = 60
    print(f"\n{'=' * width}")
    print(f"  {title}")
    print(f"{'=' * width}")


repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# =============================================================
# T1: OnActorCached called in update path (was missing before)
# =============================================================

banner("T1 — OnActorCached called in update path")

importer_cpp = os.path.join(
    repo_root, "UE_Plugin", "UELiveSync",
    "Source", "UELiveSync", "Private",
    "FBXImport", "LiveSyncFBXImporter.cpp"
)

if os.path.isfile(importer_cpp):
    with open(importer_cpp, "r") as f:
        content = f.read()

    # OnActorCached should appear at least twice: once in spawn path,
    # and once in the new update-path fix block.
    on_actor_cached_count = content.count("OnActorCached(Request.ObjectGUID, MeshActor)")
    test("T1.1: OnActorCached called in update path (>= 2 occurrences)",
          on_actor_cached_count >= 2,
          f"found {on_actor_cached_count} calls")

    # Check that OnActorCached is called BEFORE the spawn branch
    # (i.e., it's in a shared block after the if/else for update vs spawn)
    # Look for a block where OnActorCached is called unconditionally on MeshActor
    has_unconditional_cached = False
    lines = content.split('\n')
    for i, line in enumerate(lines):
        if 'if (MeshActor && Context.OnActorCached)' in line:
            has_unconditional_cached = True
            break

    test("T1.2: OnActorCached called unconditionally on MeshActor (update fix)",
          has_unconditional_cached,
          "No unconditional OnActorCached block found")
else:
    test("T1.3: LiveSyncFBXImporter.cpp found",
          False,
          f"not found at {importer_cpp}")


# =============================================================
# T2: Existing transform applied on spawn (fix for 0,0,0)
# =============================================================

banner("T2: Existing transform applied on spawn")

if os.path.isfile(importer_cpp):
    with open(importer_cpp, "r") as f:
        content = f.read()

    test("T2.1: bHasExistingTransform variable present",
          "bHasExistingTransform" in content,
          "bHasExistingTransform not found")

    test("T2.2: SetActorLocation called in FBX importer",
          "SetActorLocation" in content,
          "SetActorLocation not found")

    test("T2.3: SetActorRotation called in FBX importer",
          "SetActorRotation" in content,
          "SetActorRotation not found")

    test("T2.4: SetActorScale3D called in FBX importer",
          "SetActorScale3D" in content,
          "SetActorScale3D not found")

    test("T2.5: Pre-existing transform saved via GetActorLocation",
          "GetActorLocation" in content,
          "GetActorLocation not found")

    test("T2.6: Pre-existing transform saved via GetActorRotation",
          "GetActorRotation" in content,
          "GetActorRotation not found")

    test("T2.7: Pre-existing transform saved via GetActorScale3D",
          "GetActorScale3D" in content,
          "GetActorScale3D not found")

    test("T2.8: Transform applied only if bHasExistingTransform",
          "if (bHasExistingTransform)" in content,
          "Conditional transform application not found")

    test("T2.9: Applied transform log marker",
          "Applied existing transform to spawned actor" in content,
          "Log marker missing")
else:
    test("T2.10: LiveSyncFBXImporter.cpp found",
          False,
          f"not found at {importer_cpp}")


# =============================================================
# T3: Old non-FBX actor destroyed to prevent double ownership
# =============================================================

banner("T3: Old actor cleanup for same GUID")

if os.path.isfile(importer_cpp):
    with open(importer_cpp, "r") as f:
        content = f.read()

    test("T3.1: ActorToDestroy variable present",
          "ActorToDestroy" in content,
          "ActorToDestroy not found")

    test("T3.2: ActorToDestroy set from PreExistingActor when not update",
          "ActorToDestroy = PreExistingActor" in content,
          "ActorToDestroy assignment not found")

    test("T3.3: ActorToDestroy condition excludes MeshActor",
          "ActorToDestroy != MeshActor" in content,
          "Self-check missing")

    test("T3.4: Destroy() called on ActorToDestroy",
          "ActorToDestroy->Destroy()" in content,
          "Destroy() not found")

    test("T3.5: Destroy log marker present",
          "cleanup_stale_procedural" in content,
          "Log marker missing")
else:
    test("T3.6: LiveSyncFBXImporter.cpp found",
          False,
          f"not found at {importer_cpp}")


# =============================================================
# T4: Blender UI — non-FBX mesh sync button removed
# =============================================================

banner("T4 — Blender UI panel: non-FBX button removed")

init_py = os.path.join(repo_root, "Blender_Addon", "__init__.py")

if os.path.isfile(init_py):
    with open(init_py, "r") as f:
        content = f.read()

    # Check that the old button is NOT drawn in the panel's draw method.
    # The old operator class may still be registered but should not appear
    # in a layout.operator() call within the draw method.
    # Use the specific panel class to find the correct draw method.
    panel_class_marker = "class UELIVESYNC_PT_panel"
    panel_draw_start = content.find("def draw(self, context):", content.find(panel_class_marker))
    # Find the next class definition or end of file
    panel_draw_end = content.find("\n# ", panel_draw_start + 1)
    if panel_draw_end < 0:
        panel_draw_end = len(content)

    draw_section = content[panel_draw_start:panel_draw_end]

    test("T4.1: Old mesh sync button NOT in panel draw",
          '"uelivesync.sync_selected_mesh_to_ue"' not in draw_section,
          "Old button still in draw() method")

    # The old operator class bl_idname should still exist (for compatibility
    # but just not drawn). Check registration list still has it.
    has_old_op_class = "uelivesync.sync_selected_mesh_to_ue" in content
    test("T4.2: Old operator class still registered (compatibility)",
          has_old_op_class,
          "Old operator class removed from __init__.py")

    test("T4.3: FBX mesh sync button still in panel draw",
          '"uelivesync.sync_selected_mesh_to_ue_fbx"' in draw_section,
          "FBX button missing from draw() method")

    test("T4.4: FBX operator class still in classes list",
          "UELIVESYNC_OT_sync_selected_mesh_to_ue_fbx" in content,
          "FBX operator class not in classes tuple")
else:
    test("T4.5: __init__.py found",
          False,
          f"not found at {init_py}")


# =============================================================
# T5: Protocol unchanged — no new packet type or format change
# =============================================================

banner("T5 — Protocol unchanged")

network_py = os.path.join(repo_root, "Blender_Addon", "network.py")
sync_types_h = os.path.join(
    repo_root, "UE_Plugin", "UELiveSync", "Source",
    "UELiveSync", "Public", "SyncTypes.h"
)

if os.path.isfile(sync_types_h):
    with open(sync_types_h, "r") as f:
        content = f.read()

    test("T5.1: PT_FBXImportRequest still 0x16",
          "PT_FBXImportRequest = 0x16" in content,
          "PT_FBXImportRequest value changed")

    test("T5.2: FFBXImportRequestPayload size now 688 (Phase 10J.5F)",
          "sizeof(FFBXImportRequestPayload) == 688" in content,
          "Payload size not updated")

    # No new transform fields in payload struct
    has_transform_fields = "Location" in content and "Rotation" in content
    # But Location/Rotation may appear elsewhere in the file, so be precise
    # Check if the struct itself has transform fields
    payload_struct_start = content.find("struct FFBXImportRequestPayload")
    payload_struct_end = content.find("};", payload_struct_start)
    payload_struct = content[payload_struct_start:payload_struct_end + 1]
    has_loc_in_struct = "Location" in payload_struct
    has_rot_in_struct = "Rotation" in payload_struct
    test("T5.3: No Location field in FFBXImportRequestPayload struct",
          not has_loc_in_struct,
          "Location field added to payload struct")
    test("T5.4: No Rotation field in FFBXImportRequestPayload struct",
          not has_rot_in_struct,
          "Rotation field added to payload struct")
else:
    test("T5.5: SyncTypes.h found",
          False,
          f"not found at {sync_types_h}")


# =============================================================
# Summary
# =============================================================

print(f"\n{'=' * 60}")
print(f"  PASS: {PASS}  FAIL: {FAIL}")
print(f"{'=' * 60}")

if FAIL > 0:
    sys.exit(1)
else:
    sys.exit(0)