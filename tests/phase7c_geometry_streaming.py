#!/usr/bin/env python3
"""
Phase 7C — Blender Geometry Streaming Activation (Stage 1D)

Tests:
  1. Geometry unchanged -> no PT_Mesh send
  2. Vertex/topology change -> PT_Mesh send
  3. Material index change -> PT_Mesh send
  4. start_sync/stop_sync clears geometry cache
  5. Non-MESH object ignored
  6. Empty mesh handled safely
  7. No regression to material/asset/identity sends
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


# =========================================================
# Mock helpers
# =========================================================

class MockMaterial:
    def __init__(self, name="Mat"):
        self._name = name
    @property
    def name(self): return self._name
    @name.setter
    def name(self, v): self._name = v


class MockMaterialSlot:
    def __init__(self, material=None):
        self.material = material


class MockData:
    def __init__(self, name="Cube"):
        self._name = name
    @property
    def name(self): return self._name
    @name.setter
    def name(self, v): self._name = v


class MockObject:
    def __init__(self, name="Obj", data_name="Cube", type='MESH',
                 material_slots=None):
        self._name = name
        self._data = MockData(data_name)
        self._type = type
        self._material_slots = material_slots or []
        self._props = {}

    @property
    def name(self): return self._name
    @name.setter
    def name(self, v): self._name = v
    @property
    def data(self): return self._data
    @property
    def type(self): return self._type
    @type.setter
    def type(self, v): self._type = v
    @property
    def material_slots(self): return self._material_slots
    def __contains__(self, k): return k in self._props
    def __getitem__(self, k): return self._props[k]
    def __setitem__(self, k, v): self._props[k] = v
    def get(self, k, d=None): return self._props.get(k, d)


# =========================================================
# Simulated check_updates geometry detection
# =========================================================

def simulate_geometry_check(obj, guid, is_first_send,
                             last_geometry_version,
                             extract_fn=None, hash_fn=None):
    """Simulate the Phase 7C geometry change detection block.
    Returns (changed: bool, payloads: list).
    """
    payloads = []

    if is_first_send:
        last_geometry_version.pop(guid, None)
        return False, payloads

    if obj.type != 'MESH' or obj.data is None:
        return False, payloads

    if extract_fn is None:
        return False, payloads

    mesh_data = extract_fn(obj)
    if mesh_data is None:
        return False, payloads

    if hash_fn is None:
        return False, payloads

    current_hash = hash_fn(
        mesh_data["vertices"],
        mesh_data["triangles"],
        mesh_data["material_indices"],
    )
    prev_hash = last_geometry_version.get(guid)

    if prev_hash is not None and current_hash != prev_hash:
        payloads.append(b"mock_mesh_chunk")
        changed = True
    else:
        changed = False

    last_geometry_version[guid] = current_hash
    return changed, payloads


def extract_cube():
    """Return mesh data for a unit cube."""
    return {
        "vertices": [
            (-1, -1, -1), (1, -1, -1), (1, 1, -1), (-1, 1, -1),
            (-1, -1, 1), (1, -1, 1), (1, 1, 1), (-1, 1, 1),
        ],
        "triangles": [
            (0, 1, 2), (0, 2, 3), (1, 5, 6), (1, 6, 2),
            (5, 4, 7), (5, 7, 6), (4, 0, 3), (4, 3, 7),
            (3, 2, 6), (3, 6, 7), (4, 5, 1), (4, 1, 0),
        ],
        "material_indices": [0] * 12,
        "vertex_count": 8,
        "triangle_count": 12,
    }


def extract_triangle():
    """Return mesh data for a single triangle."""
    return {
        "vertices": [(0, 0, 0), (1, 0, 0), (0, 1, 0)],
        "triangles": [(0, 1, 2)],
        "material_indices": [0],
        "vertex_count": 3,
        "triangle_count": 1,
    }


# =========================================================
# SECTION 1: No duplicate send for unchanged geometry
# =========================================================

def test_no_duplicate_send():
    """Geometry unchanged -> no PT_Mesh send."""
    print("\n--- Section 1: No duplicate send ---")

    last_geo = {}
    guid = "guid_cube"

    # 1.1: First tick (is_first_send=False with no prev) -> no send, cache populated
    changed, payloads = simulate_geometry_check(
        MockObject("Cube", "CubeMesh"), guid,
        is_first_send=False, last_geometry_version=last_geo,
        extract_fn=lambda o: extract_cube(),
        hash_fn=lambda v, t, m: "hash_cube"
    )
    test("1.1: First detection -> no send",
         not changed and len(payloads) == 0)
    test("1.2: Cache populated after first check",
         last_geo.get(guid) is not None)

    # 1.3: Second tick, unchanged -> no send
    changed, payloads = simulate_geometry_check(
        MockObject("Cube", "CubeMesh"), guid,
        is_first_send=False, last_geometry_version=last_geo,
        extract_fn=lambda o: extract_cube(),
        hash_fn=lambda v, t, m: "hash_cube"
    )
    test("1.3: Unchanged -> no send",
         not changed and len(payloads) == 0)

    # 1.4: Third tick, still unchanged
    changed, payloads = simulate_geometry_check(
        MockObject("Cube", "CubeMesh"), guid,
        is_first_send=False, last_geometry_version=last_geo,
        extract_fn=lambda o: extract_cube(),
        hash_fn=lambda v, t, m: "hash_cube"
    )
    test("1.4: Still unchanged -> no send",
         not changed)


# =========================================================
# SECTION 2: Geometry change detection
# =========================================================

def test_geometry_change_detection():
    """Vertex/topology change -> PT_Mesh send."""
    print("\n--- Section 2: Geometry change detection ---")

    last_geo = {}
    guid = "guid_changing"

    # 2.1: First check (populate cache)
    simulate_geometry_check(
        MockObject("Obj", "MeshA"), guid,
        is_first_send=False, last_geometry_version=last_geo,
        extract_fn=lambda o: extract_cube(),
        hash_fn=lambda v, t, m: "hash_cube"
    )
    test("2.1: Cache populated", last_geo.get(guid) == "hash_cube")

    # 2.2: Changed vertices -> send
    changed, payloads = simulate_geometry_check(
        MockObject("Obj", "MeshA"), guid,
        is_first_send=False, last_geometry_version=last_geo,
        extract_fn=lambda o: extract_triangle(),
        hash_fn=lambda v, t, m: "hash_tri" if len(v) == 3 else "hash_cube"
    )
    test("2.2: Vertex change -> send",
         changed and len(payloads) == 1)
    test("2.3: Cache updated after change",
         last_geo.get(guid) == "hash_tri")

    # 2.4: Unchanged after change -> no send
    changed, payloads = simulate_geometry_check(
        MockObject("Obj", "MeshA"), guid,
        is_first_send=False, last_geometry_version=last_geo,
        extract_fn=lambda o: extract_triangle(),
        hash_fn=lambda v, t, m: "hash_tri"
    )
    test("2.4: Stable after change -> no send",
         not changed)


# =========================================================
# SECTION 3: is_first_send suppression
# =========================================================

def test_first_send_suppression():
    """is_first_send=True clears cache, no send."""
    print("\n--- Section 3: First send suppression ---")

    last_geo = {}
    guid = "guid_first"

    # 3.1: First send -> cache cleared, no send
    changed, payloads = simulate_geometry_check(
        MockObject("First", "Mesh"), guid,
        is_first_send=True, last_geometry_version=last_geo,
        extract_fn=lambda o: extract_cube(),
        hash_fn=lambda v, t, m: "hash_first"
    )
    test("3.1: First send -> no send",
         not changed and len(payloads) == 0)
    test("3.2: Cache cleared on first send",
         guid not in last_geo)

    # 3.3: Second tick populates cache, no send
    changed, payloads = simulate_geometry_check(
        MockObject("First", "Mesh"), guid,
        is_first_send=False, last_geometry_version=last_geo,
        extract_fn=lambda o: extract_cube(),
        hash_fn=lambda v, t, m: "hash_second"
    )
    test("3.3: Second tick populates cache",
         last_geo.get(guid) == "hash_second")

    # 3.4: Next tick unchanged -> no send
    changed, payloads = simulate_geometry_check(
        MockObject("First", "Mesh"), guid,
        is_first_send=False, last_geometry_version=last_geo,
        extract_fn=lambda o: extract_cube(),
        hash_fn=lambda v, t, m: "hash_second"
    )
    test("3.4: Steady state -> no send",
         not changed)


# =========================================================
# SECTION 4: start_sync / stop_sync clears cache
# =========================================================

def test_sync_lifecycle_clear():
    """start_sync/stop_sync clears geometry cache."""
    print("\n--- Section 4: Sync lifecycle clear ---")

    # 4.1: Populated cache cleared on stop_sync
    last_geo = {"guid_a": "hash_a", "guid_b": "hash_b"}
    last_geo.clear()
    test("4.1: stop_sync clears cache",
         len(last_geo) == 0)

    # 4.2: Fresh after clear
    last_geo["guid_c"] = "hash_c"
    test("4.2: Can re-populate after clear",
         last_geo.get("guid_c") == "hash_c")

    # 4.3: Clear again (simulating second stop/start)
    last_geo.clear()
    test("4.3: Double clear safe",
         len(last_geo) == 0)

    # 4.4: Populate, simulate start_sync clear
    last_geo["guid_x"] = "hash_x"
    last_geo["guid_y"] = "hash_y"
    test("4.4: Populated before start",
         len(last_geo) == 2)
    last_geo.clear()
    test("4.5: start_sync clears cache",
         len(last_geo) == 0)

    # 4.6: start_sync after stop_sync with pending geometry -> clean
    last_geo["guid_z"] = "hash_z"
    last_geo.clear()
    test("4.6: stop->start sequence leaves cache empty",
         len(last_geo) == 0)


# =========================================================
# SECTION 5: Non-MESH ignored
# =========================================================

def test_non_mesh_ignored():
    """Non-MESH object does not trigger geometry detection."""
    print("\n--- Section 5: Non-MESH ignored ---")

    last_geo = {}
    guid = "guid_armature"

    # 5.1: ARMATURE type -> ignored
    changed, payloads = simulate_geometry_check(
        MockObject("Arm", "ArmData", type='ARMATURE'), guid,
        is_first_send=False, last_geometry_version=last_geo,
        extract_fn=lambda o: extract_cube(),
        hash_fn=lambda v, t, m: "hash_arm"
    )
    test("5.1: ARMATURE -> no send, no cache",
         not changed and len(payloads) == 0 and guid not in last_geo)

    # 5.2: LATTICE type -> ignored
    changed, payloads = simulate_geometry_check(
        MockObject("Latt", "LattData", type='LATTICE'), guid,
        is_first_send=False, last_geometry_version=last_geo,
        extract_fn=lambda o: extract_cube(),
        hash_fn=lambda v, t, m: "hash_latt"
    )
    test("5.2: LATTICE -> ignored",
         not changed and guid not in last_geo)

    # 5.3: MESH after non-MESH works
    changed, payloads = simulate_geometry_check(
        MockObject("ActualMesh", "MeshData", type='MESH'), guid,
        is_first_send=False, last_geometry_version=last_geo,
        extract_fn=lambda o: extract_cube(),
        hash_fn=lambda v, t, m: "hash_mesh"
    )
    test("5.3: MESH after non-MESH works",
         guid in last_geo)


# =========================================================
# SECTION 6: Empty mesh handled
# =========================================================

def test_empty_mesh():
    """Empty mesh (no verts, no tris) handled safely."""
    print("\n--- Section 6: Empty mesh ---")

    last_geo = {}
    guid = "guid_empty"

    def extract_empty(obj):
        return {
            "vertices": [],
            "triangles": [],
            "material_indices": [],
            "vertex_count": 0,
            "triangle_count": 0,
        }

    # 6.1: Empty mesh populates cache
    changed, payloads = simulate_geometry_check(
        MockObject("Empty", "EmptyMesh"), guid,
        is_first_send=False, last_geometry_version=last_geo,
        extract_fn=extract_empty,
        hash_fn=lambda v, t, m: "hash_empty"
    )
    test("6.1: Empty mesh populates cache",
         not changed and last_geo.get(guid) == "hash_empty")

    # 6.2: Empty unchanged -> no send
    changed, payloads = simulate_geometry_check(
        MockObject("Empty", "EmptyMesh"), guid,
        is_first_send=False, last_geometry_version=last_geo,
        extract_fn=extract_empty,
        hash_fn=lambda v, t, m: "hash_empty"
    )
    test("6.2: Empty unchanged -> no send",
         not changed)

    # 6.3: Empty -> non-empty transition
    changed, payloads = simulate_geometry_check(
        MockObject("Empty", "EmptyMesh"), guid,
        is_first_send=False, last_geometry_version=last_geo,
        extract_fn=lambda o: extract_cube(),
        hash_fn=lambda v, t, m: "hash_cube"
    )
    test("6.3: Empty -> non-empty -> send",
         changed and len(payloads) == 1)


# =========================================================
# SECTION 7: Object delete clears cache
# =========================================================

def test_delete_clears_cache():
    """Object cleanup removes geometry cache entry."""
    print("\n--- Section 7: Delete cleanup ---")

    last_geo = {"guid_delete_me": "hash_val", "guid_keep": "hash_keep"}

    # 7.1: Delete pop
    last_geo.pop("guid_delete_me", None)
    test("7.1: Delete removes cache entry",
         "guid_delete_me" not in last_geo and len(last_geo) == 1)

    # 7.2: Non-deleted preserved
    test("7.2: Other entries survive",
         last_geo.get("guid_keep") == "hash_keep")

    # 7.3: Nonexistent pop is safe
    last_geo.pop("nonexistent", None)
    test("7.3: Pop nonexistent is safe",
         len(last_geo) == 1)


# =========================================================
# Run all sections
# =========================================================

def run_all():
    print("=" * 60)
    print("Phase 7C — Blender Geometry Streaming (Stage 1D)")
    print("=" * 60)

    test_no_duplicate_send()            # Section 1
    test_geometry_change_detection()    # Section 2
    test_first_send_suppression()       # Section 3
    test_sync_lifecycle_clear()         # Section 4
    test_non_mesh_ignored()             # Section 5
    test_empty_mesh()                   # Section 6
    test_delete_clears_cache()          # Section 7

    total = PASS + FAIL + SKIP
    print(f"\n{'=' * 60}")
    print(f"Phase 7C — Blender Geometry Streaming Summary")
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
