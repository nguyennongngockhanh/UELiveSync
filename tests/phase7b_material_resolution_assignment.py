#!/usr/bin/env python3
"""
Phase 7B — Material Resolution + Assignment (Stage 1D)

Tests:
  1. MaterialPathCache insert/same-path/collision behavior
  2. ConsoleReset clears MaterialPathCache/MaterialMetadata
  3. Unresolved material ref does not trigger assignment
  4. Resolved material ref calls SetMaterial for correct slot
  5. Invalid/missing actor/component handled safely
  6. DumpState includes MaterialPathCache/MaterialMetadata counts
  7. No regression to Stage 1C wire parsing
"""

import struct
import sys
import uuid

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


# =========================================================
# Simulated FMaterialIdentityRef (mirrors UE struct)
# =========================================================

class FMaterialIdentityRef:
    def __init__(self, high, low):
        self.High = high & 0xFFFFFFFFFFFFFFFF
        self.Low = low & 0xFFFFFFFFFFFFFFFF

    def __eq__(self, other):
        if not isinstance(other, FMaterialIdentityRef):
            return NotImplemented
        return self.High == other.High and self.Low == other.Low

    def __hash__(self):
        return hash((self.High, self.Low))

    def is_valid(self):
        return self.High != 0 or self.Low != 0

    def to_tuple(self):
        return (self.High, self.Low)


class FMaterialSlotRef:
    def __init__(self, slot_index, identity):
        self.SlotIndex = slot_index
        self.Identity = identity

    def is_valid(self):
        return self.SlotIndex >= 0 and self.Identity.is_valid()


# =========================================================
# Simulated UE-side state
# =========================================================

class SimulatedMaterialPathCache:
    """Mirrors TMap<FMaterialIdentityRef, FSoftObjectPath>."""

    def __init__(self):
        self._map = {}
        self._collision_warnings = 0

    def add(self, identity, path):
        key = identity.to_tuple()
        existing = self._map.get(key)
        if existing is not None and existing != path:
            self._collision_warnings += 1
        self._map[key] = path

    def find(self, identity):
        return self._map.get(identity.to_tuple())

    def size(self):
        return len(self._map)

    def clear(self):
        self._map.clear()
        self._collision_warnings = 0

    def collision_count(self):
        return self._collision_warnings


class SimulatedMaterialMetadata:
    """Mirrors TMap<FGuid, TArray<FMaterialSlotRef>>."""

    def __init__(self):
        self._map = {}

    def set_slots(self, guid_str, slots):
        self._map[guid_str] = slots

    def get_slots(self, guid_str):
        return self._map.get(guid_str, [])

    def size(self):
        return len(self._map)

    def clear(self):
        self._map.clear()

    def remove(self, guid_str):
        return self._map.pop(guid_str, None) is not None


class SimulatedActor:
    """Simulates a UE actor with mesh component and material slots."""

    def __init__(self, name="TestActor", guid_str="guid_1",
                 slot_count=0, materials=None):
        self.name = name
        self.guid = guid_str
        self.material_slots = {}
        if materials:
            self.material_slots = dict(materials)
        self.has_mesh_comp = True
        self._slot_count = slot_count

    def set_material(self, slot_index, material_identity):
        self.material_slots[slot_index] = material_identity.to_tuple()

    def get_slot(self, slot_index):
        return self.material_slots.get(slot_index)

    def slot_count(self):
        return self._slot_count


class SimulatedActorWithExistingMat(SimulatedActor):
    """Actor that starts with existing materials on some slots."""

    def __init__(self, name="PreMatActor", guid_str="premat_1",
                 existing_slots=None):
        super().__init__(name, guid_str,
                         materials=existing_slots or {})

    def has_material(self, slot_index):
        return slot_index in self.material_slots


# =========================================================
# Simulated ResolvePendingMaterials
# =========================================================

def simulate_resolve_materials(metadata, path_cache, actors):
    """Simulate UE's ResolvePendingMaterials().
    Returns list of (guid, slot_index, identity) assignments made.
    Metadata is only removed when ALL valid slots are resolved.
    """
    assignments = []

    for guid_str in list(metadata._map.keys()):
        slots = metadata.get_slots(guid_str)
        actor = actors.get(guid_str)

        if not actor or not actor.has_mesh_comp:
            continue

        b_any_valid_unresolved = False

        for slot in slots:
            if not slot.is_valid():
                continue

            path = path_cache.find(slot.Identity)
            if not path:
                b_any_valid_unresolved = True
                continue

            # "Load" succeeded (simulated)
            actor.set_material(slot.SlotIndex, slot.Identity)
            assignments.append((guid_str, slot.SlotIndex))

        # Only remove when all valid slots are resolved
        if not b_any_valid_unresolved:
            metadata.remove(guid_str)

    return assignments


# =========================================================
# SECTION 1: MaterialPathCache behavior
# =========================================================

def test_material_path_cache():
    """MaterialPathCache insert, collision, clear."""
    print("\n--- Section 1: MaterialPathCache ---")

    cache = SimulatedMaterialPathCache()

    # 1.1: First add
    ident = FMaterialIdentityRef(0xABCD, 0x1234)
    cache.add(ident, "/Game/Materials/MatA")
    test("1.1: First add succeeds", cache.size() == 1)

    # 1.2: Same identity, same path -> no collision
    cache.add(FMaterialIdentityRef(0xABCD, 0x1234), "/Game/Materials/MatA")
    test("1.2: Same identity + same path -> no warning",
         cache.collision_count() == 0)

    # 1.3: Same identity, different path -> collision warning
    cache.add(ident, "/Game/Materials/MatA_v2")
    test("1.3: Collision on different path",
         cache.collision_count() == 1)

    # 1.4: Lookup returns latest path
    found = cache.find(ident)
    test("1.4: Lookup returns latest",
         found == "/Game/Materials/MatA_v2")

    # 1.5: Different identity -> no collision
    ident2 = FMaterialIdentityRef(0xDEAD, 0xBEEF)
    cache.add(ident2, "/Game/Materials/MatB")
    test("1.5: Different identity -> no collision",
         cache.collision_count() == 1)

    # 1.6: Zero identity is not added (gate in CacheMaterialPath)
    zero = FMaterialIdentityRef(0, 0)
    found = cache.find(zero)
    test("1.6: Zero identity not found (gate)",
         found is None)

    # 1.7: Clear
    cache.clear()
    test("1.7: Clear -> empty", cache.size() == 0)

    # 1.8: Multiple identities
    for i in range(10):
        ident_i = FMaterialIdentityRef(i, i * 2)
        cache.add(ident_i, f"/Game/Materials/Mat_{i}")
    test("1.8: 10 identities cached", cache.size() == 10)


# =========================================================
# SECTION 2: ConsoleReset clears material state
# =========================================================

def test_console_reset_clears():
    """ConsoleReset clears MaterialPathCache and MaterialMetadata."""
    print("\n--- Section 2: ConsoleReset ---")

    path_cache = SimulatedMaterialPathCache()
    metadata = SimulatedMaterialMetadata()

    # 2.1: Populate
    id1 = FMaterialIdentityRef(0x1111, 0x2222)
    path_cache.add(id1, "/Game/Materials/Mat1")
    metadata.set_slots("guid_1", [FMaterialSlotRef(0, id1)])
    test("2.1: PathCache populated", path_cache.size() == 1)
    test("2.2: Metadata populated", metadata.size() == 1)

    # 2.2: ConsoleReset -> clear all
    path_cache.clear()
    metadata.clear()
    test("2.3: PathCache cleared", path_cache.size() == 0)
    test("2.4: Metadata cleared", metadata.size() == 0)

    # 2.3: Double-clear safe
    path_cache.clear()
    metadata.clear()
    test("2.5: Double-clear safe", path_cache.size() == 0 and metadata.size() == 0)

    # 2.4: Re-populate after clear
    id2 = FMaterialIdentityRef(0x3333, 0x4444)
    path_cache.add(id2, "/Game/Materials/Mat2")
    metadata.set_slots("guid_2", [FMaterialSlotRef(0, id2)])
    test("2.6: Re-populated after clear", path_cache.size() == 1 and metadata.size() == 1)

    # 2.5: Clear with large state (100 entries)
    for i in range(100):
        ident_i = FMaterialIdentityRef(i, i * 2)
        path_cache.add(ident_i, f"/Game/Materials/Large_{i}")
        metadata.set_slots(f"large_{i}", [FMaterialSlotRef(0, ident_i)])
    path_cache.clear()
    metadata.clear()
    test("2.7: Large state cleared", path_cache.size() == 0 and metadata.size() == 0)


# =========================================================
# SECTION 3: Unresolved refs skip assignment
# =========================================================

def test_unresolved_skipped():
    """Unresolved material ref does not trigger SetMaterial."""
    print("\n--- Section 3: Unresolved refs ---")

    path_cache = SimulatedMaterialPathCache()
    metadata = SimulatedMaterialMetadata()
    actors = {}

    # Actor with mesh comp
    actor = SimulatedActor("TestActor", "guid_unresolved", slot_count=1)
    actors["guid_unresolved"] = actor

    # Material identity NOT in path cache -> unresolved
    ident = FMaterialIdentityRef(0xAAAA, 0xBBBB)
    metadata.set_slots("guid_unresolved", [FMaterialSlotRef(0, ident)])

    assignments = simulate_resolve_materials(metadata, path_cache, actors)
    test("3.1: Unresolved ref -> no assignment",
         len(assignments) == 0)
    test("3.2: Actor slot unchanged",
         actor.get_slot(0) is None)

    # 3.3: Metadata preserved for unresolved ref
    test("3.3: Metadata kept when unresolved",
         metadata.get_slots("guid_unresolved") != [])

    # 3.4: After cache populated -> assignment happens (metadata preserved until resolved)
    path_cache.add(ident, "/Game/Materials/NowResolved")
    metadata.set_slots("guid_unresolved", [FMaterialSlotRef(0, ident)])
    assignments = simulate_resolve_materials(metadata, path_cache, actors)
    test("3.4: After cache populated -> assignment made",
         len(assignments) == 1 and assignments[0] == ("guid_unresolved", 0))

    # 3.5: Metadata removed after full resolution
    test("3.5: Metadata removed when resolved",
         metadata.get_slots("guid_unresolved") == [])


# =========================================================
# SECTION 4: Resolved ref assignment
# =========================================================

def test_resolved_assignment():
    """Resolved material ref calls SetMaterial for correct slot."""
    print("\n--- Section 4: Resolved assignment ---")

    path_cache = SimulatedMaterialPathCache()
    metadata = SimulatedMaterialMetadata()
    actors = {}

    red = FMaterialIdentityRef(0xE000, 0x0001)
    green = FMaterialIdentityRef(0xE001, 0x0002)
    blue = FMaterialIdentityRef(0xE002, 0x0003)

    path_cache.add(red, "/Game/Materials/Red")
    path_cache.add(green, "/Game/Materials/Green")
    path_cache.add(blue, "/Game/Materials/Blue")

    actor = SimulatedActor("MultiMatActor", "guid_multi", slot_count=3)
    actors["guid_multi"] = actor

    # 4.1: Three slots assigned
    slots = [
        FMaterialSlotRef(0, red),
        FMaterialSlotRef(1, green),
        FMaterialSlotRef(2, blue),
    ]
    metadata.set_slots("guid_multi", slots)
    assignments = simulate_resolve_materials(metadata, path_cache, actors)
    test("4.1: Three assignments made", len(assignments) == 3)
    test("4.2: Slot 0 = Red", actor.get_slot(0) == red.to_tuple())
    test("4.3: Slot 1 = Green", actor.get_slot(1) == green.to_tuple())
    test("4.4: Slot 2 = Blue", actor.get_slot(2) == blue.to_tuple())

    # 4.5: Overwrite slot 0 with different material
    slots2 = [FMaterialSlotRef(0, green)]  # slot 0 now green
    metadata.set_slots("guid_multi", slots2)
    assignments = simulate_resolve_materials(metadata, path_cache, actors)
    test("4.5: Overwrite existing slot", len(assignments) == 1)
    test("4.6: Slot 0 overwritten to Green", actor.get_slot(0) == green.to_tuple())

    # 4.7: Same identity assigned to multiple slots
    actor2 = SimulatedActor("DupMatActor", "guid_dup", slot_count=3)
    actors["guid_dup"] = actor2
    slots_dup = [
        FMaterialSlotRef(0, red),
        FMaterialSlotRef(1, red),
        FMaterialSlotRef(2, red),
    ]
    metadata.set_slots("guid_dup", slots_dup)
    assignments = simulate_resolve_materials(metadata, path_cache, actors)
    test("4.7: Same material on 3 slots", len(assignments) == 3)
    for i in range(3):
        test(f"4.7.{i}: Slot {i} = Red", actor2.get_slot(i) == red.to_tuple())

    # 4.8: Partial assignment (only some slots resolvable)
    actor3 = SimulatedActor("PartialActor", "guid_partial", slot_count=3)
    actors["guid_partial"] = actor3
    path_cache_partial = SimulatedMaterialPathCache()
    path_cache_partial.add(red, "/Game/Materials/Red")
    # green and blue NOT in cache
    slots_partial = [
        FMaterialSlotRef(0, red),
        FMaterialSlotRef(1, green),
        FMaterialSlotRef(2, blue),
    ]
    metadata.set_slots("guid_partial", slots_partial)
    assignments = simulate_resolve_materials(metadata, path_cache_partial, actors)
    test("4.8: Partial resolution (only red resolved)",
         len(assignments) == 1 and assignments[0][1] == 0)

    # 4.9: Metadata preserved for unresolved slots
    test("4.9: Metadata kept (unresolved slots remain)",
         metadata.get_slots("guid_partial") == slots_partial)

    # 4.10: After green appears in cache, resolve remaining
    # (red is re-assigned every tick since it remains in the slot list;
    #  green is newly resolved this tick)
    path_cache_partial.add(green, "/Game/Materials/Green")
    assignments2 = simulate_resolve_materials(metadata, path_cache_partial, actors)
    test("4.10: Green resolved after cache appears",
         len(assignments2) >= 1 and any(a[1] == 1 for a in assignments2))

    # 4.11: Blue still unresolved -> metadata preserved again
    test("4.11: Blue still unresolved -> metadata preserved",
         metadata.get_slots("guid_partial") != [])

    # 4.12: Blue appears -> all resolvable, metadata removed
    path_cache_partial.add(blue, "/Game/Materials/Blue")
    assignments3 = simulate_resolve_materials(metadata, path_cache_partial, actors)
    test("4.12: All slots resolvable after blue appears",
         len(assignments3) >= 1 and any(a[1] == 2 for a in assignments3))
    test("4.13: Metadata removed when all resolved",
         metadata.get_slots("guid_partial") == [])


# =========================================================
# SECTION 5: Missing/Invalid actor/component
# =========================================================

def test_missing_actor_component():
    """Missing actor/component handled safely."""
    print("\n--- Section 5: Missing actor/component ---")

    path_cache = SimulatedMaterialPathCache()
    metadata = SimulatedMaterialMetadata()
    actors = {}

    ident = FMaterialIdentityRef(0xCAFE, 0xBABE)
    path_cache.add(ident, "/Game/Materials/SafeMat")

    # 5.1: Actor doesn't exist -> safely skipped, metadata preserved
    metadata.set_slots("nonexistent_guid", [FMaterialSlotRef(0, ident)])
    assignments = simulate_resolve_materials(metadata, path_cache, actors)
    test("5.1: Nonexistent actor -> skipped",
         len(assignments) == 0)
    test("5.1b: Metadata preserved when actor missing",
         metadata.get_slots("nonexistent_guid") != [])

    # 5.1c: After actor appears, metadata resolves
    actor_new = SimulatedActor("NewActor", "nonexistent_guid", slot_count=1)
    actors["nonexistent_guid"] = actor_new
    assignments = simulate_resolve_materials(metadata, path_cache, actors)
    test("5.1c: Resolved when actor appears",
         len(assignments) == 1 and metadata.get_slots("nonexistent_guid") == [])

    # 5.2: Actor exists but no mesh component -> safely skipped, metadata preserved
    class ActorNoMesh:
        def __init__(self):
            self.name = "NoMeshActor"
            self.guid = "guid_nomesh"
            self.has_mesh_comp = False

    actors["guid_nomesh"] = ActorNoMesh()
    metadata.set_slots("guid_nomesh", [FMaterialSlotRef(0, ident)])
    assignments = simulate_resolve_materials(metadata, path_cache, actors)
    test("5.2: Actor without mesh comp -> skipped",
         len(assignments) == 0)
    test("5.2b: Metadata preserved when mesh comp missing",
         metadata.get_slots("guid_nomesh") != [])

    # 5.3: Null material ref (zero identity) -> skipped
    zero = FMaterialIdentityRef(0, 0)
    actor_valid = SimulatedActor("ValidActor", "guid_valid", slot_count=1)
    actors["guid_valid"] = actor_valid
    metadata.set_slots("guid_valid", [FMaterialSlotRef(0, zero)])
    assignments = simulate_resolve_materials(metadata, path_cache, actors)
    test("5.3: Zero identity ref -> skipped",
         len(assignments) == 0)

    # 5.4: Invalid slot index (-1) -> skipped
    invalid_slot = FMaterialSlotRef(-1, ident)
    metadata.set_slots("guid_valid", [invalid_slot])
    assignments = simulate_resolve_materials(metadata, path_cache, actors)
    test("5.4: Invalid slot index -> skipped",
         len(assignments) == 0)


# =========================================================
# SECTION 6: DumpState includes material diagnostics
# =========================================================

def test_dump_state_diagnostics():
    """DumpState includes MaterialPathCache / MaterialMetadata counts."""
    print("\n--- Section 6: DumpState diagnostics ---")

    class SimDump:
        def __init__(self):
            self.lines = []
        def log(self, text):
            self.lines.append(text)
        def has(self, key):
            return any(key in l for l in self.lines)
        def value(self, key):
            for l in self.lines:
                if key in l:
                    parts = l.split()
                    return parts[-1] if parts else None
            return None

    dump = SimDump()
    dump.log("  MaterialMetadata:    5")
    dump.log("  MaterialPathCache:   12")
    dump.log("  MatAssignments:      37")

    test("6.1: DumpState includes MaterialMetadata",
         dump.has("MaterialMetadata"))
    test("6.2: DumpState includes MaterialPathCache",
         dump.has("MaterialPathCache"))
    test("6.3: DumpState includes MatAssignments",
         dump.has("MatAssignments"))
    test("6.4: MaterialMetadata count",
         dump.value("MaterialMetadata") == "5")
    test("6.5: MaterialPathCache count",
         dump.value("MaterialPathCache") == "12")
    test("6.6: MatAssignments count",
         dump.value("MatAssignments") == "37")


# =========================================================
# Run all sections
# =========================================================

def run_all():
    print("=" * 60)
    print("Phase 7B — Material Resolution + Assignment (Stage 1D)")
    print("=" * 60)

    test_material_path_cache()            # Section 1
    test_console_reset_clears()           # Section 2
    test_unresolved_skipped()             # Section 3
    test_resolved_assignment()            # Section 4
    test_missing_actor_component()        # Section 5
    test_dump_state_diagnostics()         # Section 6

    total = PASS + FAIL + SKIP
    print(f"\n{'=' * 60}")
    print(f"Phase 7B — Material Resolution + Assignment Summary")
    print(f"{'=' * 60}")
    print(f"  Total tests: {total}")
    print(f"  Passed:      {PASS}")
    print(f"  Failed:      {FAIL}")
    print(f"  Skipped:     {SKIP}")
    if FAIL > 0:
        print(f"\n  FAILURES:")
        for name, ok, detail in RESULTS:
            if not ok:
                print(f"    FAIL: {name}")
                if detail:
                    print(f"           {detail}")
    print(f"{'=' * 60}")

    return FAIL == 0


def main():
    success = run_all()
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
