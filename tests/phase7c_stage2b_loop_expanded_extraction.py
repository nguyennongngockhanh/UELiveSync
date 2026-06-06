#!/usr/bin/env python3
"""
Phase 7C Stage 2B.1 — Blender loop-expanded extraction tests (Blender-side only).

Tests the render-vertex extraction logic for loop-expanded full-attribute mesh data.

No bpy import.  No actual Blender run required.
Pure mock data structures (plain dicts/lists).

Render vertex layout (VertexV1):
  stride=32: pos(12) + normal(12) + uv0(8)
  stride=48: pos(12) + normal(12) + uv0(8) + color0(16)

Each render vertex represents one triangle corner (one loop).
vertex_count == 3 * triangle_count (always).
Local indices per chunk: 0..vertex_count-1, no split triangles.
No tangent in Stage 2B.1.
"""

import struct
import sys

# =========================================================
# Constants
# =========================================================

TRIANGLES_PER_CHUNK = 8192
MESH_CHUNK_FLAG_FULL_ATTR = 0x80

LIVE_SYNC_V5_MESH_CHUNK_HEADER_SIZE = 89

# =========================================================
# Mock data structures
# =========================================================

def make_loop_vertex(co=(0, 0, 0), no=(0, 0, 1), uv=(0, 0), color=None):
    """One loop corner (render vertex candidate)."""
    return {
        "co": co,
        "no": no,
        "uv": uv,
        "color": color,  # None or (r,g,b,a)
    }


def make_loop_triangle(loop_verts, material_index=0):
    """
    One loop triangle.
    loop_verts: list of 3 loop vertex dicts [corner_A, corner_B, corner_C]
    """
    return {
        "loop_indices": [0, 1, 2],  # indices into loop_triangles loop layer
        "material_index": material_index,
        "loops": loop_verts,  # reference to loop layer
        # We store the loop vertices directly for easy access in extraction.
        # In real Blender this would be loop_triangles[i].loops[loop_idx].
    }


# =========================================================
# Extraction helpers (inline, not from Blender_Addon)
# =========================================================

def extract_render_vertices(loop_triangles_data):
    """
    Loop-expanded extraction for a single mesh.

    loop_triangles_data: list of dicts with keys:
        'loops': list of 3 dicts [corner_A, corner_B, corner_C], each:
            'co': (x,y,z)
            'no': (x,y,z)
            'uv': (u,v)
            'color': (r,g,b,a) or None
        'material_index': int

    Returns (render_vertices, stride, uv0_fallback, diagnostics).
    Each render vertex is a dict:
        position  (x,y,z)
        normal    (x,y,z)
        uv0       (u,v)
        color0    (r,g,b,a) or None
        source_loop_idx  int  (debug/test only)
        material_index    int
    """
    render_vertices = []
    has_color_layer = False

    # First pass: determine color presence
    for lt in loop_triangles_data:
        for corner in lt["loops"]:
            if corner["color"] is not None:
                has_color_layer = True
                break
        if has_color_layer:
            break

    stride = 48 if has_color_layer else 32
    uv0_fallback = 0
    diagnostics = []

    # Second pass: build render vertices
    render_idx = 0
    for lt in loop_triangles_data:
        tri_mat = lt["material_index"]
        for corner in lt["loops"]:
            # UV0: fallback if no UV data
            uv0 = corner["uv"]
            if uv0 is None or uv0 == (0, 0) and not _has_uv_data(loop_triangles_data):
                uv0 = (0.0, 0.0)
                uv0_fallback = 1
                if not any(True for _lt in loop_triangles_data
                           for _c in _lt["loops"]
                           if _c["uv"] and _c["uv"] != (0, 0)):
                    diagnostics.append("[MESH][ATTR] uv0Fallback=1")

            cv = corner["color"]
            render_vertices.append({
                "position": tuple(corner["co"]),
                "normal": tuple(corner["no"]),
                "uv0": tuple(uv0),
                "color0": tuple(cv) if cv else None,
                "source_loop_idx": render_idx,
                "material_index": tri_mat,
            })
            render_idx += 1

    return render_vertices, stride, uv0_fallback, diagnostics


def _has_uv_data(loop_triangles_data):
    """Check if any loop corner has non-fallback UV data."""
    for lt in loop_triangles_data:
        for corner in lt["loops"]:
            uv = corner["uv"]
            if uv and uv != (0, 0):
                return True
    return False


def chunk_mesh(render_vertices, triangle_count, stride):
    """
    Split render vertices into triangle-range chunks.

    Returns list of chunk dicts:
        'vertex_count', 'vertex_count', 'triangle_count',
        'vertices', 'indices', 'vertex_stride',
        'local_indices_offset', 'chunk_index'
    """
    num_chunks = max(1, (triangle_count + TRIANGLES_PER_CHUNK - 1) // TRIANGLES_PER_CHUNK)
    chunks = []
    vertex_base = 0
    vc_start = 0

    for ci in range(num_chunks):
        tri_start = ci * TRIANGLES_PER_CHUNK
        tri_end = min(tri_start + TRIANGLES_PER_CHUNK, triangle_count)
        tri_in_chunk = tri_end - tri_start
        vc = tri_in_chunk * 3  # vertex_count == 3 * triangles_in_chunk

        chunk_verts = render_vertices[vc_start:vc_start + vc]
        vc_start = vc_start + vc

        chunk_indices = []
        for ti in range(tri_in_chunk):
            base = ti * 3
            chunk_indices.extend([base, base + 1, base + 2])

        chunks.append({
            "vertex_count": vc,
            "triangle_count": tri_in_chunk,
            "vertices": chunk_verts,
            "indices": chunk_indices,
            "vertex_stride": stride,
            "vertex_base": vertex_base,
            "chunk_index": ci,
        })
        vertex_base += vc

    return chunks


# =========================================================
# Test helpers
# =========================================================

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
# T1: Single-triangle mesh -> 3 render vertices
# =========================================================

def test_t1_single_triangle():
    print("\n--- T1: Single-triangle mesh -> 3 render vertices ---")

    loop_tri = {
        "loops": [
            make_loop_vertex(co=(0, 0, 0), no=(0, 0, 1), uv=(0, 0)),
            make_loop_vertex(co=(1, 0, 0), no=(0, 0, 1), uv=(1, 0)),
            make_loop_vertex(co=(0, 1, 0), no=(0, 0, 1), uv=(0, 1)),
        ],
        "material_index": 0,
    }
    loop_data = [loop_tri]
    render_verts, stride, uv0_fb, diags = extract_render_vertices(loop_data)

    test("T1a: 3 render vertices", len(render_verts) == 3)
    test("T1b: vertex_count == 3 * 1", len(render_verts) == 3 * 1)
    test("T1c: stride == 32 (no color)", stride == 32)
    test("T1d: all positions stored",
         all(rv["position"] is not None for rv in render_verts))
    test("T1e: all normals stored",
         all(rv["normal"] is not None for rv in render_verts))
    test("T1f: all uv0 stored",
         all(rv["uv0"] is not None for rv in render_verts))
    test("T1g: material_index == 0 on all",
         all(rv["material_index"] == 0 for rv in render_verts))
    test("T1h: no color0 present (stride=32)",
         all(rv["color0"] is None for rv in render_verts))


# =========================================================
# T2: Two triangles sharing source vertices but different UVs
# -> duplicated render vertices, not collapsed
# =========================================================

def test_t2_shared_source_different_uvs():
    print("\n--- T2: Shared source vertices, different UVs ---")

    # Two triangles: (0,1,2) and (1,3,2)
    # Source vertex 1 is shared: corner B of tri0, corner A of tri1
    # With different UVs, they should NOT collapse
    loop_data = [
        {
            "loops": [
                make_loop_vertex(co=(0, 0, 0), no=(0, 0, 1), uv=(0, 0)),
                make_loop_vertex(co=(1, 0, 0), no=(0, 0, 1), uv=(0.5, 0)),
                make_loop_vertex(co=(1, 1, 0), no=(0, 0, 1), uv=(0.5, 0.5)),
            ],
            "material_index": 0,
        },
        {
            "loops": [
                make_loop_vertex(co=(1, 0, 0), no=(0, 0, 1), uv=(0.9, 0.1)),  # different UV!
                make_loop_vertex(co=(2, 0, 0), no=(0, 0, 1), uv=(1, 0)),
                make_loop_vertex(co=(2, 1, 0), no=(0, 0, 1), uv=(1, 1)),
            ],
            "material_index": 0,
        },
    ]
    render_verts, stride, uv0_fb, diags = extract_render_vertices(loop_data)

    test("T2a: 6 render vertices (3*2 triangles)", len(render_verts) == 6)
    # Source vertex at (1,0,0) has UV=(0.5,0) in tri0 but UV=(0.9,0.1) in tri1
    test("T2b: Source(1,0,0) has different UVs per triangle",
         render_verts[1]["uv0"] != render_verts[3]["uv0"],
         f"tri0 UV={render_verts[1]['uv0']}, tri1 UV={render_verts[3]['uv0']}")
    test("T2c: Positions preserved for both corners",
         render_verts[1]["position"] == (1, 0, 0),
         f"got {render_verts[1]['position']}")
    test("T2d: No collapse of shared source vertex",
         len(render_verts) == 6,
         "Shared source vertex produced 2 distinct render vertices")


# =========================================================
# T3: Hard-edge / split-normal case
# -> same source vertex, different normal per corner
# =========================================================

def test_t3_split_normal():
    print("\n--- T3: Hard-edge / split-normal ---")

    # Corner at (1,0,0) shared between two triangles with different face normals
    loop_data = [
        {
            "loops": [
                make_loop_vertex(co=(0, 0, 0), no=(0, 0, 1), uv=(0, 0)),
                make_loop_vertex(co=(1, 0, 0), no=(0, 0, 1), uv=(0, 0)),
                make_loop_vertex(co=(0, 1, 0), no=(0, 0, 1), uv=(0, 1)),
            ],
            "material_index": 0,
        },
        {
            "loops": [
                make_loop_vertex(co=(1, 0, 0), no=(0, 1, 0), uv=(1, 0)),  # different normal!
                make_loop_vertex(co=(2, 0, 0), no=(0, 1, 0), uv=(1, 0)),
                make_loop_vertex(co=(2, 1, 0), no=(0, 1, 0), uv=(1, 1)),
            ],
            "material_index": 0,
        },
    ]
    render_verts, stride, uv0_fb, diags = extract_render_vertices(loop_data)

    test("T3a: 6 render vertices (3*2)", len(render_verts) == 6)
    # Source vertex (1,0,0) has different normals
    test("T3b: Same position, different normals",
         render_verts[1]["position"] == render_verts[3]["position"] == (1, 0, 0),
         f"positions: {render_verts[1]['position']} vs {render_verts[3]['position']}")
    test("T3c: Normals differ",
         render_verts[1]["normal"] != render_verts[3]["normal"],
         f"normals: {render_verts[1]['normal']} vs {render_verts[3]['normal']}")


# =========================================================
# T4: No UV layer -> all uv0=(0,0), uv0Fallback=1
# =========================================================

def test_t4_no_uv_layer():
    print("\n--- T4: No UV layer ---")

    loop_data = [
        {
            "loops": [
                make_loop_vertex(co=(0, 0, 0), no=(0, 0, 1), uv=None),
                make_loop_vertex(co=(1, 0, 0), no=(0, 0, 1), uv=None),
                make_loop_vertex(co=(0, 1, 0), no=(0, 0, 1), uv=None),
            ],
            "material_index": 0,
        },
    ]
    render_verts, stride, uv0_fb, diags = extract_render_vertices(loop_data)

    test("T4a: All uv0 are (0,0)",
         all(rv["uv0"] == (0.0, 0.0) for rv in render_verts))
    test("T4b: uv0Fallback == 1", uv0_fb == 1)
    test("T4c: Diagnostic uv0Fallback=1 present",
         any("uv0Fallback=1" in d for d in diags))
    test("T4d: 3 render vertices", len(render_verts) == 3)


# =========================================================
# T5: UV layer present -> per-corner UV preserved
# =========================================================

def test_t5_uv_layer_present():
    print("\n--- T5: UV layer present -> per-corner UV ---")

    loop_data = [
        {
            "loops": [
                make_loop_vertex(co=(0, 0, 0), no=(0, 0, 1), uv=(0.0, 0.0)),
                make_loop_vertex(co=(1, 0, 0), no=(0, 0, 1), uv=(1.0, 0.0)),
                make_loop_vertex(co=(1, 1, 0), no=(0, 0, 1), uv=(1.0, 1.0)),
            ],
            "material_index": 0,
        },
        {
            "loops": [
                make_loop_vertex(co=(0, 0, 0), no=(0, 0, 1), uv=(0.0, 0.0)),
                make_loop_vertex(co=(1, 1, 0), no=(0, 0, 1), uv=(1.0, 1.0)),
                make_loop_vertex(co=(0, 1, 0), no=(0, 0, 1), uv=(0.0, 1.0)),
            ],
            "material_index": 0,
        },
    ]
    render_verts, stride, uv0_fb, diags = extract_render_vertices(loop_data)

    test("T5a: 6 render vertices", len(render_verts) == 6)
    test("T5b: First corner UV=(0,0)", render_verts[0]["uv0"] == (0.0, 0.0))
    test("T5c: Second corner UV=(1,0)", render_verts[1]["uv0"] == (1.0, 0.0))
    test("T5d: Third corner UV=(1,1)", render_verts[2]["uv0"] == (1.0, 1.0))
    # Tri1 corners
    test("T5e: Tri1 corner A UV=(0,0)", render_verts[3]["uv0"] == (0.0, 0.0))
    test("T5f: Tri1 corner B UV=(1,1)", render_verts[4]["uv0"] == (1.0, 1.0))
    test("T5g: Tri1 corner C UV=(0,1)", render_verts[5]["uv0"] == (0.0, 1.0))
    test("T5h: uv0Fallback == 0", uv0_fb == 0)
    test("T5i: Per-corner UV preserved (not averaged)",
         render_verts[0]["uv0"] == (0.0, 0.0) and
         render_verts[3]["uv0"] == (0.0, 0.0))


# =========================================================
# T6: No color layer -> stride=32
# =========================================================

def test_t6_no_color():
    print("\n--- T6: No color layer -> stride=32 ---")

    loop_data = [
        {
            "loops": [
                make_loop_vertex(co=(0, 0, 0), no=(0, 0, 1), uv=(0, 0), color=None),
                make_loop_vertex(co=(1, 0, 0), no=(0, 0, 1), uv=(1, 0), color=None),
                make_loop_vertex(co=(0, 1, 0), no=(0, 0, 1), uv=(0, 1), color=None),
            ],
            "material_index": 0,
        },
    ]
    render_verts, stride, uv0_fb, diags = extract_render_vertices(loop_data)

    test("T6a: stride == 32", stride == 32)
    test("T6b: No color0 on any render vertex",
         all(rv["color0"] is None for rv in render_verts))
    test("T6c: stride bytes == 32", stride == 32)
    test("T6d: Render vertex without color has no color key set to non-None",
         not any(rv.get("color0") is not None for rv in render_verts))


# =========================================================
# T7: Color layer present -> stride=48, color0 stored
# =========================================================

def test_t7_color_layer():
    print("\n--- T7: Color layer present -> stride=48 ---")

    loop_data = [
        {
            "loops": [
                make_loop_vertex(co=(0, 0, 0), no=(0, 0, 1), uv=(0, 0),
                                   color=(1.0, 0.0, 0.0, 1.0)),
                make_loop_vertex(co=(1, 0, 0), no=(0, 0, 1), uv=(1, 0),
                                   color=(0.0, 1.0, 0.0, 1.0)),
                make_loop_vertex(co=(0, 1, 0), no=(0, 0, 1), uv=(0, 1),
                                   color=(0.0, 0.0, 1.0, 1.0)),
            ],
            "material_index": 0,
        },
    ]
    render_verts, stride, uv0_fb, diags = extract_render_vertices(loop_data)

    test("T7a: stride == 48", stride == 48)
    test("T7b: color0 present on all render vertices",
         all(rv["color0"] is not None for rv in render_verts))
    test("T7c: First vertex color=(1,0,0,1)", render_verts[0]["color0"] == (1.0, 0.0, 0.0, 1.0))
    test("T7d: Second vertex color=(0,1,0,1)", render_verts[1]["color0"] == (0.0, 1.0, 0.0, 1.0))
    test("T7e: Third vertex color=(0,0,1,1)", render_verts[2]["color0"] == (0.0, 0.0, 1.0, 1.0))
    # Stride is the only truth: stride 48 implies color present
    test("T7f: Stride 48 matches color presence",
         stride == 48 and all(rv["color0"] is not None for rv in render_verts))


# =========================================================
# T8: material_index preserved per triangle
# =========================================================

def test_t8_material_index():
    print("\n--- T8: material_index per triangle ---")

    loop_data = [
        {
            "loops": [
                make_loop_vertex(co=(0, 0, 0), no=(0, 0, 1), uv=(0, 0)),
                make_loop_vertex(co=(1, 0, 0), no=(0, 0, 1), uv=(1, 0)),
                make_loop_vertex(co=(0, 1, 0), no=(0, 0, 1), uv=(0, 1)),
            ],
            "material_index": 0,
        },
        {
            "loops": [
                make_loop_vertex(co=(1, 0, 0), no=(0, 0, 1), uv=(1, 0)),
                make_loop_vertex(co=(2, 0, 0), no=(0, 0, 1), uv=(2, 0)),
                make_loop_vertex(co=(2, 1, 0), no=(0, 0, 1), uv=(2, 1)),
            ],
            "material_index": 2,
        },
    ]
    render_verts, stride, uv0_fb, diags = extract_render_vertices(loop_data)

    test("T8a: Tri0 corners have material_index=0",
         render_verts[0]["material_index"] == 0 and
         render_verts[1]["material_index"] == 0 and
         render_verts[2]["material_index"] == 0)
    test("T8b: Tri1 corners have material_index=2",
         render_verts[3]["material_index"] == 2 and
         render_verts[4]["material_index"] == 2 and
         render_verts[5]["material_index"] == 2)
    test("T8c: No cross-contamination",
         render_verts[2]["material_index"] != render_verts[3]["material_index"])


# =========================================================
# T9: Triangle-range chunking: every chunk has complete triangles only
# =========================================================

def test_t9_triangle_range_chunking():
    print("\n--- T9: Triangle-range chunking ---")

    loop_data = []
    for ti in range(5):
        base = ti * 3
        loop_data.append({
            "loops": [
                make_loop_vertex(co=(float(base), 0, 0), no=(0, 0, 1), uv=(float(base), 0)),
                make_loop_vertex(co=(float(base + 1), 0, 0), no=(0, 0, 1), uv=(float(base + 1), 0)),
                make_loop_vertex(co=(float(base + 2), 1, 0), no=(0, 0, 1), uv=(float(base + 2), 1)),
            ],
            "material_index": 0,
        })

    render_verts, stride, uv0_fb, diags = extract_render_vertices(loop_data)

    # Chunk with small limit for test: use 2 triangles per chunk
    global TRIANGLES_PER_CHUNK
    old_chunk_limit = TRIANGLES_PER_CHUNK
    TRIANGLES_PER_CHUNK = 2

    chunks = chunk_mesh(render_verts, len(loop_data), stride)
    TRIANGLES_PER_CHUNK = old_chunk_limit

    test("T9a: 5 triangles -> 3 chunks (ceil(5/2))",
         len(chunks) == 3,
         f"got {len(chunks)} chunks")
    # Each chunk: vertex_count == index_count == 3 * triangles_in_chunk
    for i, chunk in enumerate(chunks):
        tri = chunk["triangle_count"]
        vc = chunk["vertex_count"]
        ic = len(chunk["indices"])
        test(f"T9b: Chunk {i} vc==ic==3*tri ({vc}=={ic}==3*{tri})",
             vc == ic == 3 * tri,
             f"vc={vc}, ic={ic}, tri={tri}")
    # No split triangles: all indices are complete triplets
    for i, chunk in enumerate(chunks):
        all_complete = True
        for ti in range(chunk["triangle_count"]):
            base = ti * 3
            a, b, c = chunk["indices"][base], chunk["indices"][base + 1], chunk["indices"][base + 2]
            if a != base or b != base + 1 or c != base + 2:
                all_complete = False
        test(f"T9c: Chunk {i} has complete triangle indices (no split)",
             all_complete)


# =========================================================
# T10: Local chunk indices offset model
# =========================================================

def test_t10_local_chunk_indices():
    print("\n--- T10: Local chunk indices offset model ---")

    loop_data = []
    for ti in range(6):
        base = ti * 3
        loop_data.append({
            "loops": [
                make_loop_vertex(co=(float(base), 0, 0), no=(0, 0, 1), uv=(0, 0)),
                make_loop_vertex(co=(float(base + 1), 0, 0), no=(0, 0, 1), uv=(0, 0)),
                make_loop_vertex(co=(float(base + 2), 1, 0), no=(0, 0, 1), uv=(0, 0)),
            ],
            "material_index": 0,
        })

    render_verts, stride, uv0_fb, diags = extract_render_vertices(loop_data)

    global TRIANGLES_PER_CHUNK
    old_chunk_limit = TRIANGLES_PER_CHUNK
    TRIANGLES_PER_CHUNK = 4  # 6 tris / 4 = 2 chunks

    chunks = chunk_mesh(render_verts, len(loop_data), stride)
    TRIANGLES_PER_CHUNK = old_chunk_limit

    test("T10a: 6 triangles / 4 per chunk -> 2 chunks",
         len(chunks) == 2)

    # Chunk 0: 4 triangles -> 12 verts, local indices 0..11
    chunk0 = chunks[0]
    expected_chunk0_indices = list(range(12))
    test("T10b: Chunk 0 local indices are 0..11",
         chunk0["indices"] == expected_chunk0_indices,
         f"got {chunk0['indices']}")
    test("T10c: Chunk 0 vertex_base = 0", chunk0["vertex_base"] == 0)
    test("T10c2: Chunk 0 vertex_count=12", chunk0["vertex_count"] == 12)

    # Chunk 1: 2 triangles -> 6 verts, local indices 0..5, global = local + 12
    chunk1 = chunks[1]
    expected_chunk1_indices = [0, 1, 2, 3, 4, 5]  # 2 tris × 3 indices each
    test("T10d: Chunk 1 local indices are 0..5",
         chunk1["indices"] == expected_chunk1_indices,
         f"got {chunk1['indices']}")
    test("T10e: Chunk 1 vertex_base = 12", chunk1["vertex_base"] == 12)
    test("T10e2: Chunk 1 vertex_count=6", chunk1["vertex_count"] == 6)
    # UE reassembly: global = local + vertex_base
    for idx in chunk1["indices"]:
        global_idx = idx + chunk1["vertex_base"]
        test(f"T10f: Global index {idx} + {chunk1['vertex_base']} = {global_idx}",
             global_idx >= 12 and global_idx < 18,
             f"expected global in [12,18)" )


# =========================================================
# T11: No tangent field in render vertex
# =========================================================

def test_t11_no_tangent():
    print("\n--- T11: No tangent field in Stage 2B.1 ---")

    loop_data = [
        {
            "loops": [
                make_loop_vertex(co=(0, 0, 0), no=(0, 0, 1), uv=(0, 0)),
                make_loop_vertex(co=(1, 0, 0), no=(0, 0, 1), uv=(1, 0)),
                make_loop_vertex(co=(0, 1, 0), no=(0, 0, 1), uv=(0, 1)),
            ],
            "material_index": 0,
        },
    ]
    render_verts, stride, uv0_fb, diags = extract_render_vertices(loop_data)

    for rv in render_verts:
        has_tangent = "tangent" in rv
        has_handedness = "handedness" in rv
        if has_tangent or has_handedness:
            test("T11a: No tangent field in render vertex", False,
                 f"render vertex has tangent={rv.get('tangent')} or handedness={rv.get('handedness')}")
            break
    else:
        test("T11a: No tangent field in render vertex", True)
        test("T11b: No handedness field in render vertex", True)
    # Also verify render vertex dict keys are the expected ones
    expected_keys = {"position", "normal", "uv0", "color0", "source_loop_idx", "material_index"}
    for rv in render_verts:
        actual_keys = set(rv.keys())
        test("T11c: Render vertex keys are expected subset",
             actual_keys.issubset(expected_keys),
             f"got keys={actual_keys}")
        break


# =========================================================
# T12: Render vertex count always equals 3 * tri_count
# =========================================================

def test_t12_vertex_count_invariant():
    print("\n--- T12: vertex_count == 3 * triangle_count invariant ---")

    # Test with varying triangle counts
    for tri_count in [1, 2, 3, 5, 10, 100]:
        loop_data = []
        for ti in range(tri_count):
            base = ti * 3
            loop_data.append({
                "loops": [
                    make_loop_vertex(co=(float(base), 0, 0), no=(0, 0, 1), uv=(0, 0)),
                    make_loop_vertex(co=(float(base + 1), 0, 0), no=(0, 0, 1), uv=(0, 0)),
                    make_loop_vertex(co=(float(base + 2), 1, 0), no=(0, 0, 1), uv=(0, 0)),
                ],
                "material_index": 0,
            })
        render_verts, stride, uv0_fb, diags = extract_render_vertices(loop_data)
        expected = tri_count * 3
        test(f"T12a: tri={tri_count} -> vc={len(render_verts)} == {expected}",
             len(render_verts) == expected,
             f"expected {expected}, got {len(render_verts)}")

    # Chunking invariant: per-chunk vertex_count == 3 * triangles_in_chunk
    loop_data = []
    for ti in range(7):
        base = ti * 3
        loop_data.append({
            "loops": [
                make_loop_vertex(co=(float(base), 0, 0), no=(0, 0, 1), uv=(0, 0)),
                make_loop_vertex(co=(float(base + 1), 0, 0), no=(0, 0, 1), uv=(0, 0)),
                make_loop_vertex(co=(float(base + 2), 1, 0), no=(0, 0, 1), uv=(0, 0)),
            ],
            "material_index": 0,
        })
    render_verts, stride, uv0_fb, diags = extract_render_vertices(loop_data)
    global TRIANGLES_PER_CHUNK
    old_chunk_limit = TRIANGLES_PER_CHUNK
    TRIANGLES_PER_CHUNK = 3  # 7 tris / 3 = 3 chunks (3+3+1)

    chunks = chunk_mesh(render_verts, len(loop_data), stride)
    TRIANGLES_PER_CHUNK = old_chunk_limit

    total_vc = sum(c["vertex_count"] for c in chunks)
    test("T12b: Total chunk vertex_count == 3 * total triangles",
         total_vc == 3 * 7,
         f"total_vc={total_vc}, expected={21}")

    for i, chunk in enumerate(chunks):
        test(f"T12c: Chunk {i} vc == 3 * chunk_tri",
             chunk["vertex_count"] == 3 * chunk["triangle_count"],
             f"vc={chunk['vertex_count']}, tri={chunk['triangle_count']}")


# =========================================================
# Run all tests
# =========================================================

def run_all():
    print("=" * 60)
    print("Phase 7C Stage 2B.1 — Loop-Expanded Extraction Tests")
    print("=" * 60)

    test_t1_single_triangle()
    test_t2_shared_source_different_uvs()
    test_t3_split_normal()
    test_t4_no_uv_layer()
    test_t5_uv_layer_present()
    test_t6_no_color()
    test_t7_color_layer()
    test_t8_material_index()
    test_t9_triangle_range_chunking()
    test_t10_local_chunk_indices()
    test_t11_no_tangent()
    test_t12_vertex_count_invariant()

    total = PASS + FAIL + SKIP
    print(f"\n{'=' * 60}")
    print(f"Phase 7C Stage 2B.1 — Loop-Expanded Extraction Summary")
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
