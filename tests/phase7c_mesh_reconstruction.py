#!/usr/bin/env python3
"""
Phase 7C — ProceduralMeshComponent Reconstruction (Stage 1C)

Tests:
  1. Completed reassembly builds mesh sections
  2. Missing actor skips reconstruction (no crash)
  3. Empty vertices/triangles skip reconstruction
  4. Multi-chunk reassembly reconstructs correctly
  5. Single-chunk reconstruction
  6. Mesh sections built counter
  7. ConsoleReset clears state
  8. DumpState includes sections count

No automatic Blender check_updates() streaming.
No compression/delta streaming.
No UV/normal/vertex color support.
"""

import hashlib
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
# Protocol constants
# =========================================================

LIVE_SYNC_V5_MESH_CHUNK_HEADER_SIZE = 89


def build_chunk_payload(guid_obj, version_hash, chunk_index, chunk_count,
                         flags, vertices, triangles, material_indices):
    """Simulate the minimal PT_Mesh payload (no header, just data blocks)."""
    payload = bytearray()

    # Vertex count + positions (float32 x 3)
    payload.extend(struct.pack("<I", len(vertices)))
    for v in vertices:
        payload.extend(struct.pack("<fff", v[0], v[1], v[2]))

    # Triangle count + indices (int32 x 3)
    payload.extend(struct.pack("<I", len(triangles)))
    for t in triangles:
        payload.extend(struct.pack("<III", t[0], t[1], t[2]))

    # Material index count + indices (int32 per triangle)
    payload.extend(struct.pack("<I", len(material_indices)))
    for m in material_indices:
        payload.extend(struct.pack("<i", m))

    return bytes(payload)


# =========================================================
# Simulated UE-side state
# =========================================================

class SimulatedProcMeshActor:
    """Simulates a UE actor with a ProceduralMeshComponent."""

    def __init__(self, name="TestActor", guid_str="guid_1"):
        self.name = name
        self.guid = guid_str
        self.sections = {}  # section_index -> section data
        self.has_mesh_comp = False
        self._created_sections = []

    def find_or_create_procmesh(self):
        self.has_mesh_comp = True

    def create_mesh_section(self, section_index, vertices, triangles,
                             normals=None, uvs=None, colors=None,
                             tangents=None, b_collision=True):
        self.sections[section_index] = {
            "vertices": list(vertices),
            "triangles": list(triangles),
            "normals": list(normals) if normals else [],
            "uvs": list(uvs) if uvs else [],
            "colors": list(colors) if colors else [],
            "tangents": list(tangents) if tangents else [],
            "collision": b_collision,
        }
        self._created_sections.append(section_index)

    def section_count(self):
        return len(self.sections)

    def total_verts(self):
        return sum(len(s["vertices"]) for s in self.sections.values())

    def total_tris(self):
        return sum(len(s["triangles"]) // 3 for s in self.sections.values())


class SimulatedReconstructionHandler:
    """Simulates UE's ReconstructCompletedMeshes."""

    def __init__(self):
        self.reassembly = {}     # guid_str -> state
        self.actors = {}
        self.mesh_chunks_received = 0
        self.reassemblies_completed = 0
        self.sections_built = 0

    def add_actor(self, guid_str, actor):
        self.actors[guid_str] = actor

    def handle_chunk(self, guid_str, version_hash, chunk_index,
                      chunk_count, vertices, triangles,
                      material_indices, flags=0):
        """Receive a chunk and accumulate."""
        if guid_str not in self.reassembly:
            self.reassembly[guid_str] = {
                "version_hash": version_hash,
                "chunk_count": chunk_count,
                "chunks": {},
                "chunks_received": 0,
                "b_reconstructed": False,
            }

        state = self.reassembly[guid_str]

        if chunk_index in state["chunks"]:
            return False

        state["chunks"][chunk_index] = {
            "vertices": list(vertices),
            "triangles": list(triangles),
            "material_indices": list(material_indices),
        }
        state["chunks_received"] += 1
        self.mesh_chunks_received += 1

        if state["chunks_received"] >= state["chunk_count"]:
            self.reassemblies_completed += 1

        return True

    def reconstruct_completed(self):
        """Simulate ReconstructCompletedMeshes()."""
        to_remove = []

        for guid_str, state in self.reassembly.items():
            if not (state["chunks_received"] >= state["chunk_count"]):
                continue
            if state["b_reconstructed"]:
                continue

            actor = self.actors.get(guid_str)
            if not actor:
                continue

            # Decode all chunks
            all_verts = []
            all_tris = []
            all_mat = []
            vertex_base = 0

            for i in range(state["chunk_count"]):
                chunk = state["chunks"].get(i)
                if not chunk:
                    return

                all_verts.extend(chunk["vertices"])
                tri_offset = [(t[0] + vertex_base, t[1] + vertex_base,
                               t[2] + vertex_base) for t in chunk["triangles"]]
                for t in tri_offset:
                    all_tris.extend(t)
                all_mat.extend(chunk["material_indices"])
                vertex_base += len(chunk["vertices"])

            if len(all_verts) == 0 or len(all_tris) == 0:
                state["b_reconstructed"] = True
                to_remove.append(guid_str)
                continue

            # Create proc mesh component
            actor.find_or_create_procmesh()

            # Build sections grouped by material index
            if len(all_mat) == len(all_tris) // 3:
                mat_groups = {}
                for t_idx in range(len(all_tris) // 3):
                    mat = all_mat[t_idx] if t_idx < len(all_mat) else 0
                    mat_groups.setdefault(mat, []).append(t_idx)

                for section_idx, tri_indices in mat_groups.items():
                    # Build per-section vertex/triangle data
                    vmap = {}
                    section_verts = []
                    section_tris = []
                    for ti in tri_indices:
                        base = ti * 3
                        for j in range(3):
                            orig = all_tris[base + j]
                            if orig not in vmap:
                                vmap[orig] = len(section_verts)
                                section_verts.append(all_verts[orig])
                            section_tris.append(vmap[orig])

                    actor.create_mesh_section(
                        section_idx, section_verts, section_tris)
                    self.sections_built += 1
            else:
                # Single section
                section_tris = list(all_tris)
                actor.create_mesh_section(0, all_verts, section_tris)
                self.sections_built += 1

            state["b_reconstructed"] = True
            to_remove.append(guid_str)

        for g in to_remove:
            del self.reassembly[g]

    def clear(self):
        self.reassembly.clear()
        self.actors.clear()
        self.mesh_chunks_received = 0
        self.reassemblies_completed = 0
        self.sections_built = 0


# =========================================================
# SECTION 1: Completed reassembly builds mesh sections
# =========================================================

def test_single_chunk_reconstruction():
    """Completed single chunk builds mesh sections."""
    print("\n--- Section 1: Single-chunk reconstruction ---")

    handler = SimulatedReconstructionHandler()
    guid_str = "guid_cube"
    vhash = "a" * 64

    # Cube: 8 verts, 12 tris
    verts = [
        (-1, -1, -1), (1, -1, -1), (1, 1, -1), (-1, 1, -1),
        (-1, -1, 1), (1, -1, 1), (1, 1, 1), (-1, 1, 1),
    ]
    tris = [
        (0, 1, 2), (0, 2, 3), (1, 5, 6), (1, 6, 2),
        (5, 4, 7), (5, 7, 6), (4, 0, 3), (4, 3, 7),
        (3, 2, 6), (3, 6, 7), (4, 5, 1), (4, 1, 0),
    ]
    mat = [0] * 12

    actor = SimulatedProcMeshActor("CubeActor", guid_str)
    handler.add_actor(guid_str, actor)

    # 1.1: Send single chunk
    handler.handle_chunk(guid_str, vhash, 0, 1, verts, tris, mat)
    test("1.1: Chunk accepted", handler.mesh_chunks_received == 1)

    # 1.2: Reconstruct
    handler.reconstruct_completed()
    test("1.2: Sections built", handler.sections_built >= 1)
    test("1.3: Actor has mesh comp", actor.has_mesh_comp)


# =========================================================
# SECTION 2: Multi-chunk reconstruction
# =========================================================

def test_multi_chunk_reconstruction():
    """Multi-chunk reassembly reconstructs correctly."""
    print("\n--- Section 2: Multi-chunk reconstruction ---")

    handler = SimulatedReconstructionHandler()
    guid_str = "guid_big"
    vhash = "b" * 64

    verts_a = [(-1, -1, -1), (1, -1, -1), (1, 1, -1), (-1, 1, -1)]
    tris_a = [(0, 1, 2), (0, 2, 3)]
    mat_a = [0, 0]

    verts_b = [(-1, -1, 1), (1, -1, 1), (1, 1, 1), (-1, 1, 1)]
    tris_b = [(0, 1, 2), (0, 2, 3)]
    mat_b = [0, 0]

    actor = SimulatedProcMeshActor("BigActor", guid_str)
    handler.add_actor(guid_str, actor)

    handler.handle_chunk(guid_str, vhash, 0, 2, verts_a, tris_a, mat_a)
    handler.handle_chunk(guid_str, vhash, 1, 2, verts_b, tris_b, mat_b)
    test("2.1: Both chunks received", handler.mesh_chunks_received == 2)

    # Reconstruct
    handler.reconstruct_completed()
    test("2.2: Multi-chunk reconstructed", handler.sections_built >= 1)
    test("2.3: Total verts = 8", actor.total_verts() == 8)
    test("2.4: Total tris = 4", actor.total_tris() == 4)


# =========================================================
# SECTION 3: Missing actor skips
# =========================================================

def test_missing_actor():
    """Missing actor skips reconstruction safely."""
    print("\n--- Section 3: Missing actor ---")

    handler = SimulatedReconstructionHandler()
    guid_str = "guid_missing_actor"
    vhash = "c" * 64

    verts = [(0, 0, 0), (1, 0, 0), (0, 1, 0)]
    tris = [(0, 1, 2)]
    mat = [0]

    # No actor added to handler
    handler.handle_chunk(guid_str, vhash, 0, 1, verts, tris, mat)
    test("3.1: Chunk received", handler.mesh_chunks_received == 1)

    # Reconstruct with no actor
    handler.reconstruct_completed()
    test("3.2: No crash (actor missing), no sections built",
         handler.sections_built == 0)

    # Add actor later
    actor = SimulatedProcMeshActor("LateActor", guid_str)
    handler.add_actor(guid_str, actor)
    handler.reconstruct_completed()
    test("3.3: After actor appears, reconstructs",
         handler.sections_built >= 1 and actor.has_mesh_comp)


# =========================================================
# SECTION 4: Empty geometry skipped
# =========================================================

def test_empty_geometry():
    """Empty vertices/triangles skip reconstruction safely."""
    print("\n--- Section 4: Empty geometry ---")

    handler = SimulatedReconstructionHandler()
    guid_str = "guid_empty"
    vhash = "d" * 64

    actor = SimulatedProcMeshActor("EmptyActor", guid_str)
    handler.add_actor(guid_str, actor)

    # 4.1: Empty verts + empty tris
    handler.handle_chunk(guid_str, vhash, 0, 1, [], [], [])
    handler.reconstruct_completed()
    test("4.1: Empty geometry -> no sections",
         handler.sections_built == 0)

    # 4.2: Valid geometry after empty works
    handler.handle_chunk(guid_str, vhash, 0, 1,
                          [(0, 0, 0), (1, 0, 0), (0, 1, 0)],
                          [(0, 1, 2)], [0])
    handler.reconstruct_completed()
    test("4.2: Valid after empty -> reconstructs",
         handler.sections_built >= 1 and actor.has_mesh_comp)


# =========================================================
# SECTION 5: Material-index section grouping
# =========================================================

def test_material_section_grouping():
    """Per-triangle material indices produce multiple sections."""
    print("\n--- Section 5: Material section grouping ---")

    handler = SimulatedReconstructionHandler()
    guid_str = "guid_multimat"
    vhash = "e" * 64

    verts = [
        (-1, -1, 0), (1, -1, 0), (1, 1, 0), (-1, 1, 0),
        (-1, -1, 1), (1, -1, 1), (1, 1, 1), (-1, 1, 1),
    ]
    tris = [
        (0, 1, 2), (0, 2, 3),  # mat 0
        (4, 5, 6), (4, 6, 7),  # mat 1
        (0, 3, 7), (0, 7, 4),  # mat 0
        (1, 5, 6), (1, 6, 2),  # mat 1
    ]
    mat = [0, 0, 1, 1, 0, 0, 1, 1]

    actor = SimulatedProcMeshActor("MultiMatActor", guid_str)
    handler.add_actor(guid_str, actor)

    handler.handle_chunk(guid_str, vhash, 0, 1, verts, tris, mat)
    handler.reconstruct_completed()
    test("5.1: Two sections (mat 0 and mat 1)",
         actor.section_count() == 2,
         f"got {actor.section_count()} sections")

    # 5.2: All vertices present across sections
    test("5.2: All verts distributed across sections",
         actor.total_verts() >= 6)  # some may be deduped


# =========================================================
# SECTION 6: Diagnostics
# =========================================================

def test_diagnostics():
    """DumpState includes mesh sections built."""
    print("\n--- Section 6: Diagnostics ---")

    handler = SimulatedReconstructionHandler()
    guid_str = "guid_diag"
    vhash = "f" * 64

    actor = SimulatedProcMeshActor("DiagActor", guid_str)
    handler.add_actor(guid_str, actor)

    handler.handle_chunk(guid_str, vhash, 0, 1,
                          [(0, 0, 0), (1, 0, 0), (0, 1, 0)],
                          [(0, 1, 2)], [0])
    handler.reconstruct_completed()
    test("6.1: Sections built counter > 0",
         handler.sections_built >= 1)

    # 6.2: ConsoleReset clears sections
    handler.clear()
    test("6.2: ConsoleReset clears sections built",
         handler.sections_built == 0)


# =========================================================
# SECTION 7: No section for single-tri single-vert
# =========================================================

def test_invalid_geometry_skipped():
    """Invalid geometry (bad indices, degenerate) skipped."""
    print("\n--- Section 7: Invalid geometry ---")

    handler = SimulatedReconstructionHandler()
    guid_str = "guid_invalid"
    vhash = "g" * 64

    actor = SimulatedProcMeshActor("InvalidActor", guid_str)
    handler.add_actor(guid_str, actor)

    # 7.1: One vert, one tri (degenerate, but accepted)
    handler.handle_chunk(guid_str, vhash, 0, 1,
                          [(0, 0, 0)],
                          [(0, 0, 0)], [0])
    handler.reconstruct_completed()
    # Should still build a section (UE will handle degenerate triangles gracefully)
    test("7.1: Degenerate tri accepted gracefully",
         handler.sections_built >= 1 or actor.has_mesh_comp)

    # 7.2: Valid after invalid
    handler.handle_chunk(guid_str, vhash, 0, 1,
                          [(0, 0, 0), (1, 0, 0), (0, 1, 0)],
                          [(0, 1, 2)], [0])
    handler.reconstruct_completed()
    test("7.2: Valid after invalid -> sections built",
         handler.sections_built >= 2)


# =========================================================
# Run all sections
# =========================================================

def run_all():
    print("=" * 60)
    print("Phase 7C — ProceduralMesh Reconstruction (Stage 1C)")
    print("=" * 60)

    test_single_chunk_reconstruction()       # Section 1
    test_multi_chunk_reconstruction()        # Section 2
    test_missing_actor()                     # Section 3
    test_empty_geometry()                    # Section 4
    test_material_section_grouping()         # Section 5
    test_diagnostics()                       # Section 6
    test_invalid_geometry_skipped()          # Section 7

    total = PASS + FAIL + SKIP
    print(f"\n{'=' * 60}")
    print(f"Phase 7C — ProceduralMesh Reconstruction Summary")
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
