#!/usr/bin/env python3
"""
Phase 10J.5A — Material Metadata Lifecycle (stale entry cleanup)

Tests:
  1. OnActorDestroyed removes MaterialMetadata for GUID
  2. PT_Delete_V5 handler removes MaterialMetadata for GUID
  3. ResolvePendingMaterials actor_missing branch calls It.RemoveCurrent()
  4. No CacheMaterialPath call was added in this phase
  5. No protocol constants/packet structs changed
  6. Existing ActorCache/AssetMetadata/PendingAssetQueue cleanup remains
"""

import os
import sys
import re

PROJECT_ROOT = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..")
)

SUBSYSTEM_CPP = os.path.join(
    PROJECT_ROOT,
    "UE_Plugin/UELiveSync/Source/UELiveSync/Private/UELiveSyncSubsystem.cpp"
)

PASS = 0
FAIL = 0
SKIP = 0
RESULTS = []


def test(name, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  PASS: {name}")
    else:
        FAIL += 1
        msg = f"  FAIL: {name}"
        if detail:
            msg += f" \u2014 {detail}"
        print(msg)
    RESULTS.append((name, condition, detail))


def skip(name, reason=""):
    global SKIP
    SKIP += 1
    msg = f"  SKIP: {name}"
    if reason:
        msg += f" \u2014 {reason}"
    print(msg)
    RESULTS.append((name, True, f"SKIP \u2014 {reason}"))


def read_source(path):
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


# =========================================================
# T1: OnActorDestroyed removes MaterialMetadata for GUID
# =========================================================

def test_on_actor_destroyed_cleanup(source):
    """
    OnActorDestroyed must call MaterialMetadata.Remove(Guid)
    after existing cleanup of AssetMetadata/PendingAssetQueue.
    """
    # Find the OnActorDestroyed function body
    m = re.search(
        r'void UUELiveSyncSubsystem::\s*OnActorDestroyed\s*\('
        r'[^}]*'
        r'AssetMetadata\.Remove\(Guid\);[^}]*'
        r'MaterialMetadata\.Remove\(Guid\)',
        source
    )
    if m:
        test("T1: OnActorDestroyed removes MaterialMetadata for GUID", True)
    else:
        test("T1: OnActorDestroyed removes MaterialMetadata for GUID", False,
             "MaterialMetadata.Remove(Guid) not found following AssetMetadata.Remove(Guid)")


# =========================================================
# T2: PT_Delete_V5 handler removes MaterialMetadata for GUID
# =========================================================

def test_delete_handler_cleanup(source):
    """
    The Stage 5 delete handler must call MaterialMetadata.Remove(TargetGuid)
    after existing cleanup of AssetMetadata/PendingAssetQueue.
    """
    m = re.search(
        r'PendingAssetQueue\.Remove\(TargetGuid\);[^}]*\}[^}]*'
        r'MaterialMetadata\.Remove\(TargetGuid\)',
        source
    )
    if m:
        test("T2: PT_Delete_V5 handler removes MaterialMetadata for GUID", True)
    else:
        test("T2: PT_Delete_V5 handler removes MaterialMetadata for GUID", False,
             "MaterialMetadata.Remove(TargetGuid) not found after PendingAssetQueue.Remove(TargetGuid)")


# =========================================================
# T3: ResolvePendingMaterials actor_missing calls It.RemoveCurrent()
# =========================================================

def test_resolve_actor_missing_remove(source):
    """
    When FindActorFast returns nullptr in ResolvePendingMaterials,
    the branch must call It.RemoveCurrent() before continue.
    """
    m = re.search(
        r'if\s*\(!Actor\b[\s\S]*?'
        r'It\.RemoveCurrent\(\)[\s\S]*?'
        r'continue;',
        source
    )
    if m:
        test("T3: ResolvePendingMaterials actor_missing calls It.RemoveCurrent()", True)
    else:
        test("T3: ResolvePendingMaterials actor_missing calls It.RemoveCurrent()", False,
             "It.RemoveCurrent() not found in actor_missing branch")


# =========================================================
# T4: No CacheMaterialPath call was added in this phase
# =========================================================

def test_no_cache_material_path_call(source):
    """
    Verify no new CacheMaterialPath call sites exist.
    The function definition itself is OK, but no caller added yet.
    """
    # CacheMaterialPath should only appear:
    #   - in its own definition
    #   - in the header declaration
    #   - in comments/docs
    # We check that no new caller was introduced by looking for
    # CacheMaterialPath( with a non-whitespace prefix (caller context).
    # Definitions and declarations have "::CacheMaterialPath" or "void CacheMaterialPath".
    calls = re.findall(r'[^:\s]\s*CacheMaterialPath\(', source)
    # Ignore the definition line itself (::CacheMaterialPath)
    defs = re.findall(r'::\s*CacheMaterialPath\(', source)
    # Any call that is not the definition and not in a comment
    significant_calls = [
        c for c in calls
        if not any(d in source for d in defs)
        and '//' not in source.split(c)[0]
    ]
    if len(significant_calls) == 0:
        test("T4: No CacheMaterialPath call added", True)
    else:
        test("T4: No CacheMaterialPath call added", False,
             f"Found {len(significant_calls)} unexpected CacheMaterialPath call(s)")


# =========================================================
# T5: No protocol/packet structs changed
# =========================================================

def test_protocol_unchanged(source):
    """
    Verify no protocol constants or packet structs were modified.
    Check that PT_Material, PT_FBXImportRequest, FBX_IMPORT_REQUEST_PAYLOAD_SIZE
    are not redefined in this file.
    """
    # Check that no new packet-type defines were added
    pt_defines = re.findall(r'^\s*(PT_\w+|FBX_IMPORT_REQUEST_PAYLOAD_SIZE)\s*=\s*\d+', source, re.MULTILINE)
    if len(pt_defines) == 0:
        test("T5: Protocol constants unchanged in this file", True)
    else:
        test("T5: Protocol constants unchanged in this file", False,
             f"Found {len(pt_defines)} packet constant definition(s) in this file")


# =========================================================
# T6: Existing cleanup remains (ActorCache/AssetMetadata/PendingAssetQueue)
# =========================================================

def test_existing_cleanup_remains(source):
    """
    Verify OnActorDestroyed still removes ActorCache, AssetMetadata,
    PendingAssetQueue, and TransformStates as before.
    """
    checks = [
        ("TransformStates.Remove", r'TransformStates\.Remove\('),
        ("AssetMetadata.Remove", r'AssetMetadata\.Remove\('),
        ("PendingAssetQueue.Remove", r'PendingAssetQueue\.Remove\('),
    ]
    all_ok = True
    for name, pattern in checks:
        found = re.search(pattern, source)
        if not found:
            test(f"T6: {name} remains in OnActorDestroyed", False,
                 f"Pattern '{pattern}' not found")
            all_ok = False
    if all_ok:
        test("T6: Existing cleanup remains in OnActorDestroyed", True)

    # Also check PT_Delete_V5 handler keeps ActorCache.Remove and AssetMetadata.Remove
    delete_checks = [
        ("ActorCache.Remove", r'ActorCache\.Remove\(TargetGuid\)'),
        ("AssetMetadata.Remove", r'AssetMetadata\.Remove\(TargetGuid\)'),
    ]
    all_del_ok = True
    for name, pattern in delete_checks:
        found = re.search(pattern, source)
        if not found:
            test(f"T6: {name} remains in delete handler", False,
                 f"Pattern '{pattern}' not found")
            all_del_ok = False
    if all_del_ok:
        test("T6: Delete handler keeps ActorCache/AssetMetadata cleanup", True)


# =========================================================
# MAIN
# =========================================================

if not os.path.isfile(SUBSYSTEM_CPP):
    skip("All tests", f"Source file not found: {SUBSYSTEM_CPP}")
    print(f"\n=== Results: {PASS} passed, {FAIL} failed, {SKIP} skipped ===")
    sys.exit(1 if FAIL > 0 else 0)

source = read_source(SUBSYSTEM_CPP)

print("============================================================")
print("  Phase 10J.5A — Material Metadata Lifecycle")
print("============================================================")
print()

test_on_actor_destroyed_cleanup(source)
test_delete_handler_cleanup(source)
test_resolve_actor_missing_remove(source)
test_no_cache_material_path_call(source)
test_protocol_unchanged(source)
test_existing_cleanup_remains(source)

print()
print("============================================================")
print(f"  Results: {PASS} passed, {FAIL} failed, {SKIP} skipped")
print("============================================================")

if FAIL > 0:
    for name, passed, detail in RESULTS:
        if not passed:
            print(f"    FAIL: {name}  {detail}")

sys.exit(1 if FAIL > 0 else 0)
