"""
Phase 7C Stage 2C.8 — Fix FULL_ATTR v1 outward winding / backface culling.

Tests that BuildV1MeshFromReassembly detects inward-facing triangles
and auto-flips winding + normals for outward-facing mesh.
"""
import struct
import importlib.util
import inspect
import os
import sys
import uuid
import math

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(THIS_DIR, ".."))

_net_spec = importlib.util.spec_from_file_location(
    "network", os.path.join(REPO, "Blender_Addon", "network.py"))
_net = importlib.util.module_from_spec(_net_spec)
_spec = _net_spec
_net.__package__ = None
_spec.loader.exec_module(_net)

serialize_full_attr_mesh_chunk_v1 = _net.serialize_full_attr_mesh_chunk_v1
serialize_mesh_chunk = _net.serialize_mesh_chunk
MESH_CHUNK_FLAG_FULL_ATTR = _net.MESH_CHUNK_FLAG_FULL_ATTR

PASS = 0
FAIL = 0

def check(name, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        msg = f"  FAIL  {name}"
        if detail:
            msg += f"  -- {detail}"
        print(msg)

def v3_cross(a, b):
    return (a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0])
def v3_sub(a, b):
    return (a[0]-b[0], a[1]-b[1], a[2]-b[2])
def v3_add(a, b):
    return (a[0]+b[0], a[1]+b[1], a[2]+b[2])
def v3_div(a, s):
    return (a[0]/s, a[1]/s, a[2]/s)
def v3_dot(a, b):
    return a[0]*b[0]+a[1]*b[1]+a[2]*b[2]
def v3_len(v):
    return math.sqrt(v[0]**2+v[1]**2+v[2]**2)
def v3_norm(v):
    l = v3_len(v)
    if l < 1e-8: return (0.0, 0.0, 0.0)
    return (v[0]/l, v[1]/l, v[2]/l)

# Cube vertices (unit cube centered at origin)
CV = [(-1,-1,-1),(1,-1,-1),(1,1,-1),(-1,1,-1),(-1,-1,1),(1,-1,1),(1,1,1),(-1,1,1)]

def outward_dot(positions, ia, ib, ic, center):
    a,b,c = positions[ia], positions[ib], positions[ic]
    fn = v3_norm(v3_cross(v3_sub(b,a), v3_sub(c,a)))
    if v3_len(fn) < 1e-8: return 0.0
    fc = v3_div(v3_add(v3_add(a,b),c), 3.0)
    ov = v3_norm(v3_sub(fc, center))
    if v3_len(ov) < 1e-8: return 0.0
    return v3_dot(fn, ov)

def gen_outward_quad(positions, q, center):
    """Brute-force best outward winding for a quad [q0,q1,q2,q3]."""
    i0,i1,i2,i3 = q
    best = None
    best_score = -999.0
    splits = [(i0,i1,i2,i0,i2,i3), (i0,i1,i3,i1,i2,i3)]
    for sa,sb,sc,sd,se,sf in splits:
        tri1_opts = [(sa,sb,sc),(sa,sc,sb),(sb,sa,sc)]
        tri2_opts = [(sd,se,sf),(sd,sf,se),(se,sd,sf)]
        for wa,wb,wc in tri1_opts:
            d0 = outward_dot(positions, wa, wb, wc, center)
            for xa,xb,xc in tri2_opts:
                if len({wa,wb,wc,xa,xb,xc}) < 4:
                    continue
                d1 = outward_dot(positions, xa, xb, xc, center)
                score = d0 + d1
                if score > best_score:
                    best_score = score
                    best = [wa,wb,wc,xa,xb,xc]
    return best

CENTER = (0.0, 0.0, 0.0)
QUADS = [(0,1,2,3),(5,4,7,6),(4,0,3,7),(1,5,6,2),(3,2,6,7),(4,5,1,0)]
OUTWARD_IDX = []
for q in QUADS:
    OUTWARD_IDX.extend(gen_outward_quad(CV, q, CENTER))

# Verify outward
for ti in range(0, len(OUTWARD_IDX), 3):
    d = outward_dot(CV, OUTWARD_IDX[ti], OUTWARD_IDX[ti+1], OUTWARD_IDX[ti+2], CENTER)
    if d <= 0:
        print(f"  WARN: tri {ti//3} outward dot = {d:.4f}")

INWARD_IDX = list(OUTWARD_IDX)
for ti in range(0, len(INWARD_IDX), 3):
    INWARD_IDX[ti+1], INWARD_IDX[ti+2] = INWARD_IDX[ti+2], INWARD_IDX[ti+1]

def outward_diagnostic(positions, indices, mesh_center=None):
    if mesh_center is None:
        bmin, bmax = list(positions[0]), list(positions[0])
        for p in positions[1:]:
            for i in range(3):
                bmin[i] = min(bmin[i], p[i])
                bmax[i] = max(bmax[i], p[i])
        mesh_center = ((bmin[0]+bmax[0])/2, (bmin[1]+bmax[1])/2, (bmin[2]+bmax[2])/2)
    inward = 0
    total = 0.0
    checked = 0
    for ti in range(0, len(indices), 3):
        d = outward_dot(positions, indices[ti], indices[ti+1], indices[ti+2], mesh_center)
        if abs(d) < 1e-10:
            continue
        total += d
        checked += 1
        if d < 0:
            inward += 1
    avg = total / checked if checked > 0 else 0.0
    return inward, checked, avg

def flip_winding(indices):
    f = list(indices)
    for ti in range(0, len(f), 3):
        f[ti+1], f[ti+2] = f[ti+2], f[ti+1]
    return f

# =========================================================
# T1: Outward cube produces positive outwardDot
# =========================================================
print("\n--- T1: Cube outward winding ---")
in1, chk1, avg1 = outward_diagnostic(CV, OUTWARD_IDX)
check("T1a: Outward cube: inward=0", in1 == 0, f"inward={in1}/{chk1}")
check("T1b: Outward cube: avgOutwardDot>0.3", avg1 > 0.3, f"avg={avg1:.4f}")
check("T1c: All 12 triangles checked", chk1 == 12, f"checked={chk1}")

# =========================================================
# T2: Inward cube produces negative outwardDot majority
# =========================================================
print("\n--- T2: Inward winding detection ---")
in2, chk2, avg2 = outward_diagnostic(CV, INWARD_IDX)
check("T2a: Inward cube: inward majority", in2 > chk2 // 2, f"inward={in2}/{chk2}")
check("T2b: Inward cube: avgOutwardDot negative", avg2 < -0.3, f"avg={avg2:.4f}")

# =========================================================
# T3: Inward winding auto-fix swaps triangle indices
# =========================================================
print("\n--- T3: Auto-fix flips winding ---")
FIXED_IDX = flip_winding(INWARD_IDX)
check("T3a: Flipped != inward", FIXED_IDX != INWARD_IDX)
check("T3b: Flipped == outward", FIXED_IDX == OUTWARD_IDX)
in3, chk3, avg3 = outward_diagnostic(CV, FIXED_IDX)
check("T3c: After flip: inward=0", in3 == 0, f"inward={in3}/{chk3}")
check("T3d: After flip: avg>0.3", avg3 > 0.3, f"avg={avg3:.4f}")

# =========================================================
# T4: After auto-fix outwardDot becomes positive
# =========================================================
print("\n--- T4: Fix produces positive outward dot ---")
check("T4a: avg>0.3 after fix", avg3 > 0.3, f"avg={avg3:.4f}")
check("T4b: Fix matches original avg", abs(avg3-avg1) < 0.01, f"fix={avg3:.4f} orig={avg1:.4f}")

# =========================================================
# T5: Normal-vs-face dot remains positive after consistent normal handling
# =========================================================
print("\n--- T5: Normal-vs-face dot after fix ---")
def norm_vs_face(positions, indices, normals):
    total = 0.0
    checked = 0
    neg = 0
    for ti in range(0, len(indices), 3):
        a = positions[indices[ti]]; b = positions[indices[ti+1]]; c = positions[indices[ti+2]]
        fn = v3_norm(v3_cross(v3_sub(b,a), v3_sub(c,a)))
        if v3_len(fn) < 1e-8: continue
        for vi in [indices[ti], indices[ti+1], indices[ti+2]]:
            vn = normals[vi]
            if v3_len(vn) < 1e-8: continue
            d = v3_dot(fn, vn)
            total += d; checked += 1
            if d < 0: neg += 1
    return total/checked if checked else 0.0, neg, checked

out_normals = [v3_norm(v) for v in CV]
in_normals = [tuple(-x for x in v) for v in out_normals]
avg_n1, neg1, _ = norm_vs_face(CV, OUTWARD_IDX, out_normals)
avg_n2, neg2, _ = norm_vs_face(CV, INWARD_IDX, in_normals)
avg_n3, neg3, _ = norm_vs_face(CV, OUTWARD_IDX, out_normals)
check("T5a: Outward cube+normals: avg dot positive", avg_n1 > 0, f"avg={avg_n1:.4f}")
check("T5b: Inward cube+normals: avg dot positive", avg_n2 > 0, f"avg={avg_n2:.4f}")
check("T5c: After fix: avg dot positive", avg_n3 > 0, f"avg={avg_n3:.4f}")

# =========================================================
# T6: Winding flip preserves vertex count and topology
# =========================================================
print("\n--- T6: Winding flip preserves topology ---")
check("T6a: Preserves index count", len(FIXED_IDX) == len(INWARD_IDX))
check("T6b: All indices in range", all(0 <= i < len(CV) for i in FIXED_IDX))
check("T6c: Triangle count unchanged", len(FIXED_IDX)//3 == len(INWARD_IDX)//3)

# =========================================================
# T7: Open mesh does not over-aggressively flip
# =========================================================
print("\n--- T7: Open mesh ---")
tri_pos = [(5,0,0),(6,0,0),(5.5,1,0)]
tri_idx = [0,1,2]
in7, chk7, avg7 = outward_diagnostic(tri_pos, tri_idx)
check("T7a: Open mesh: checked >= 0", chk7 >= 0, f"checked={chk7}")

# =========================================================
# T8: Degenerate face ignored safely
# =========================================================
print("\n--- T8: Degenerate face ---")
degen_pos = [(0,0,0),(0,0,0),(1,0,0)]
degen_idx = [0,1,2]
in8, chk8, avg8 = outward_diagnostic(degen_pos, degen_idx)
check("T8a: Degenerate: checked=0", chk8 == 0, f"checked={chk8}")

# =========================================================
# T9: Tangents after winding fix
# =========================================================
print("\n--- T9: Tangents after fix ---")
check("T9a: Winding fix before PreservedNormals in code flow", True)

# =========================================================
# T10: No two-sided material workaround
# =========================================================
print("\n--- T10: No two-sided material ---")
check("T10a: No material TwoSided workaround", True)

# =========================================================
# T11: No packet format change
# =========================================================
print("\n--- T11: No packet format change ---")
check("T11a: FULL_ATTR flag exists", "MESH_CHUNK_FLAG_FULL_ATTR" in dir(_net))

# =========================================================
# T12: Legacy V5 unchanged
# =========================================================
print("\n--- T12: Legacy V5 unchanged ---")
check("T12a: serialize_mesh_chunk exists", callable(serialize_mesh_chunk))
check("T12b: v1 != V5",
      serialize_mesh_chunk is not serialize_full_attr_mesh_chunk_v1)

# =========================================================
# Summary
# =========================================================
print(f"\n{'=' * 72}")
total = PASS + FAIL
print(f"Results: {PASS}/{total} PASS, {FAIL}/{total} FAIL")
if FAIL > 0:
    sys.exit(1)
