# Phase 1.5 — Legacy Protocol Elimination: Completion Summary

## Status: COMPLETE (scope: packets replaced by semantic messages)

Phase 1.5 is complete for its defined scope: every legacy packet type that had been
replaced by a working semantic `MsgType` path has been decommissioned (9 capabilities).
The four WAIT-group packets (`0x05` PT_Material, `0x06` PT_Mesh, `0x08` PT_AssetDef,
`0x1B` PT_CameraDef) are retained as **production protocol**: the W0 investigation
proved none of them has a working semantic replacement, so they cannot be removed under
the Phase 1.5 acceptance criterion ("no production references remain" + semantic path
proven).

This ADR closes Phase 1.5. It records the completed capabilities, the per-packet status
of the WAIT group with evidence, and the criteria that will later flip a WAIT packet to
either DECOMMISSIONED (after a completed MIG) or PERMANENT (by a dedicated ADR).

## Completed Capabilities

All nine decommissioned packets followed the same cadence — Investigation → Contract →
Implementation → Build → Regression → (Runtime N/A) → ADR → Commit — and preserved the
semantic `MsgType` path, bridge handlers, replay machinery, FNV handshake, capability
masks, provenance comments, and world-replay markers.

| Capability | Packet | ADR | Commit | Runtime |
|---|---|---|---|---|
| A | 0x16 PT_FBXImportRequest | ADR-72 | `07ca0f0` | N/A |
| A | 0x03 PT_Create | ADR-73 | `0d49d18` | N/A |
| B | 0x04 PT_Delete | ADR-74 | `00d8545` | PASS (explicit runtime) |
| D | 0x02 PT_Reserved_02 | ADR-75 | `54238d0` | N/A (no wire presence) |
| C1 | 0x0B PT_Visibility | ADR-76 | `f1bf49b` | N/A |
| C2 | 0x0C PT_Rename | ADR-77 | `5f75d2a` | N/A |
| C3 | 0x0D PT_Hierarchy | ADR-78 | `fb01967` | N/A |
| C4 | 0x15 PT_ActiveCamera | ADR-79 | `1ae2ba5` | N/A |
| C5 | 0x0E PT_Delete_V5 | ADR-80 | `c71ecb9` | N/A |

C1–C5 introduced the "retain semantic storage" pattern: the packet surface (enum,
constant, dead serializer) is removed, while the storage struct the packet once owned
(`FVisibilitySequenceTracker`, `FRenameSequenceTracker`, `FHierarchySequenceTracker`,
`FActiveCameraPayload`, `FDeleteSequenceTracker`) is retained because it is live storage
for the semantic path.

## WAIT Group — Per-Packet Status

W0 investigation findings (`kValidTypes` = `UELiveSyncSubsystem.cpp:3367`, includes all
four; dispatchers verified in `ProcessBinaryPacket`):

| Packet | kValidTypes | Dispatcher (UE) | Emitter (Blender) | Semantic replacement | Status / Rationale |
|---|---|---|---|---|---|
| 0x05 PT_Material | ✓ | cpp:4100 → `HandleMaterialDef` (cpp:17106) | sync.py:2873 (slot identity) **+ semantic dual-emit** sync.py:2300/2355 | MATERIAL_CREATE/UPDATE/ASSIGN (0x40/41/42) exist and are emitted (MIG-004A/B) | **WAIT** — semantic MATERIAL_* is live but not runtime-validated as a full replacement (AGENTS known issue: "MATERIAL_UPDATE/MATERIAL_ASSIGN runtime verification blocked until mesh pipeline fully works"); legacy PT_Material still carries slot identity |
| 0x06 PT_Mesh | ✓ | cpp:4477 → `HandleMeshChunk` (cpp:17555) + bridge `OnMeshChunk` (cpp:8160) | sync.py:2933, `serialize_mesh_chunk` (network.py:2211) | MESH_DATA/DELTA/START/CHUNK/END (0x30–34) defined in `msg_transport.py` but **no Blender sender exists** | **WAIT** — semantic MESH_* exists only on paper; PT_Mesh is the only working mesh channel. Strategic future: FBX handoff |
| 0x08 PT_AssetDef | ✓ | cpp:3539 → `HandleAssetDef` (cpp:12231) | sync.py:2753, `serialize_asset_identity` (network.py:737) | **None** — no `ASSET_*` MsgType exists (mapping row 8 = MISSING) | **WAIT** — no semantic replacement exists at all |
| 0x1B PT_CameraDef | ✓ | cpp:3916 → `HandleCameraDef` (cpp:13220) | sync.py:3172 (dual-emit CAMERA_CREATE, MIG-003) + 3211/3251 standalone | CAMERA_CREATE/UPDATE (0x50/51) PARTIAL — 4 fields missing (clip planes, ortho; mapping row 26) | **WAIT** — semantic camera has not reached parity; PT_CameraDef is the supplement channel for the missing schema |

## Why Not PERMANENT

"PERMANENT" is a long-term architectural decision and is not supported by current
evidence. At least two packets are visibly incomplete migrations rather than settled
protocol:

- `0x05` — the semantic MATERIAL_* path already exists and is emitted (dual-path); it is
  blocked on runtime validation, not on missing design.
- `0x06` — the semantic MESH_* family already exists in the protocol spec; it is simply
  not wired into Blender/UE yet.

`0x08` and `0x1B` have no working semantic replacement (or an incomplete one), but that
is an absence of migration, not an architectural statement that they must remain forever.
Flipping them to PERMANENT now would freeze the protocol without a decision.

## Decommission Criteria (future)

A WAIT packet transitions to decommission only after:

1. A MIG completes a semantic path that reaches **parity** (all fields covered) and
   passes runtime acceptance (the AGENTS runtime checklist), and
2. The Blender emitter is switched to semantic-only (legacy serializer has zero
   production callers), and
3. The UE dispatcher/`kValidTypes` entry can be dropped.

Then the Phase 1.5 capability cadence re-applies (investigation → contract → build →
regression → ADR → commit). Candidate tracks recorded from W0:

- **0x1B**: MIG to extend CAMERA_CREATE/UPDATE with the 4 missing fields (clip planes,
  ortho) — closes the known "Protocol schema gap" (AGENTS Known issues).
- **0x06**: wire the existing MESH_* (0x30–34) family into Blender/UE, or await the FBX
  handoff mesh path.
- **0x05**: finish MATERIAL_* runtime validation, then fold slot identity in or drop the
  legacy packet.
- **0x08**: requires a new semantic ASSET design (no MsgType exists today).

## If a Packet Is Never Replaced

If the project decides a specific WAIT packet will never be replaced, a **separate ADR**
records that decision and flips the packet's status to PERMANENT. This keeps the
architectural commitment explicit and evidence-driven instead of defaulting.

## Rollback

None — this is a documentation-only closure ADR. No production code changed.
