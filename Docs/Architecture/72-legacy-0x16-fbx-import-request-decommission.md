# Phase 1.5: Legacy PT_FBXImportRequest (0x16) Decommission

## Status: COMPLETE — Runtime acceptance PASS

Implementation complete: the legacy `PT_FBXImportRequest = 0x16` constant (Blender +
UE) and the dead `network.serialize_fbx_import_request` serializer were removed. The
semantic `FBX_IMPORT_REQUEST` (0x60) message (MIG-005) is now the only FBX import path.
Build PASS, regression PASS, runtime acceptance PASS.

This is the first capability of Phase 1.5 (Legacy Protocol Elimination). Cadence:
Investigation → Contract → Implementation → Build → Regression → Runtime → ADR → Commit.

## Summary

Phase 1.5 inventory (read-only) classified all legacy `PT_*` packet types:
- REMOVE (9): `0x02` Reserved_02, `0x03` PT_Create, `0x04` PT_Delete, `0x0B` PT_Visibility,
  `0x0C` PT_Rename, `0x0D` PT_Hierarchy, `0x0E` PT_Delete_V5, `0x15` PT_ActiveCamera, `0x16`.
- KEEP (13): `0x01, 0x07, 0x09, 0x0A, 0x0F, 0x11, 0x12, 0x13, 0x14, 0x17, 0x18, 0x19, 0x1A`.
- WAIT (4): `0x05` PT_Material (needs runtime verification), `0x06` PT_Mesh (FBX handoff
  transition), `0x08` PT_AssetDef (possible redundancy with OBJECT_CREATE `primitive_type`),
  `0x1B` PT_CameraDef (MIG-003 dual-emit, wait for camera semantic stability).

No ADR was written for the inventory itself (decision-free survey); this ADR records the
first decommission decision.

## Proof of Semantic Replacement (0x16 → 0x60)

| Legacy | Replacement | Runtime proven | Production callers | Safe to remove |
|---|---|---|---|---|
| `PT_FBXImportRequest` (0x16), 688B fixed payload + `serialize_fbx_import_request` | `FBX_IMPORT_REQUEST` (0x60) via `fbx_protocol.build_fbx_import_request` | MIG-005, MIG-006 Stage 5, Phase 1.5 runtime (below) | 0 (grep before removal: only stale tests, Docs/, .recovery/, git history) | YES |

## Problem

MIG-005 migrated the production FBX import path to the semantic `FBX_IMPORT_REQUEST`
(0x60) message and removed the legacy 0x16 production path in the UE plugin, but the
legacy 0x16 surface survived as dead weight in the Blender addon (`PT_FBXImportRequest`
constant, `serialize_fbx_import_request` serializer) and as the enum entry + comments in
the UE plugin. Phase 1.5 exists to eliminate the remaining legacy protocol surface once a
semantic replacement is runtime-proven.

## Stage 1 — Investigation

- Grep inventory: `PT_FBXImportRequest` / `serialize_fbx_import_request` references were
  confined to `Tests/Protocol/Phase1.3_Protocol_Mapping.md` (mapping table, intentional),
  `Docs/Architecture/70-mig-005*` (history), `.recovery/`, stale `tests/` (root) files, and
  a historical provenance comment in `UELiveSyncSubsystem.cpp:8709`.
- Production callers of the legacy serializer: **0**. The live FBX path is
  `__init__.py` → `_fbxp.build_fbx_import_request` → `transport.send_msg(FBX_IMPORT_REQUEST)`
  (0x60). `manifest_v3.serialize_and_send_fbx_request(serialize_fn)` retained "for
  measurements" per `__init__.py:2432` is not the active emission path (active path uses
  the fbx_protocol builder; runtime messages are always 0x60).

## Stage 2 — Contract

Approved contract, one packet = one capability:
- **Will change (6 files):** `Blender_Addon/network.py` (const + serializer), `Blender_Addon/fbx_protocol.py`
  (docstring), `UE_Plugin/.../Public/SyncTypes.h` (enum + comment), `UELiveSyncSubsystem.h`
  (comment), `LiveSyncViews.h` (comment), `Tests/Protocol/Phase1.3_Protocol_Mapping.md` (row).
- **Will NOT change:** `FFBXImportRequestPayload` + static_assert 688 (SyncTypes.h),
  `FbxImportRequestView` (LiveSyncViews.h), importer (`LiveSyncFBXImporter.cpp`), gameplay
  sink, FNV signature (`network.py` / SyncTypes.h — kept byte-identical for handshake compat),
  `kValidTypes`, `pack_ue_fguid`, provenance comment `UELiveSyncSubsystem.cpp:8709`, Docs/.recovery.
- **Acceptance:** (1) `rg` 0 production references; (2) py_compile + Tests/Protocol suite;
  (3) build `ProjectTemplateEditor` clean; (4) runtime smoke minimal (FBX import works,
  spawn/update correct, no regression); (5) `git grep serialize_fbx_import_request` /
  `PT_FBXImportRequest` only in `Docs/`, `.recovery/`, git history.

## Stage 3 — Implementation

`+7/-67` lines across 6 files:
- `network.py`: removed `PT_FBXImportRequest = 0x16` constant and `serialize_fbx_import_request`.
- `fbx_protocol.py`: docstring → "Represents the FBX_IMPORT_REQUEST (0x60) semantic message."
- `SyncTypes.h`: removed enum `PT_FBXImportRequest = 0x16` + comment; payload comment →
  `FBX_IMPORT_REQUEST (0x60) fixed-size payload: 688 bytes`.
- `UELiveSyncSubsystem.h`: comment → `FBX_IMPORT_REQUEST and PT_Mesh`.
- `LiveSyncViews.h`: comment → semantic FBX_IMPORT_REQUEST (0x60) message.
- `Phase1.3_Protocol_Mapping.md`: row 21 → `MAPPED (Phase 1.5: legacy 0x16 DECOMMISSIONED;
  semantic 0x60 only)`.

## Stage 4 — Build & Regression

- Build: `Build.sh ProjectTemplateEditor` — **SUCCEEDED** (14 actions, plugin linked, 31.99s).
- `Tests/Protocol/tests/`: **56 passed** (incl. cross-channel GUID contract 3/3).
- `validate_protocol.py`: **10/10 PASS**.
- `cross_language_verify.py v1`: **32/32 PASS** (incl. FBX_IMPORT_REQUEST vectors).
- Legacy `tests/` (root) suite: 20 failed / 294 passed — **byte-identical failure set at
  HEAD** (stash-verified): 0 regression. The 20 failures are stale tests of the
  pre-MIG-005 `manifest_v3.serialize_and_send_fbx_request` path and are out of this
  capability's scope; they reference `serialize_fbx_import_request` and are deferred to a
  separate cleanup capability (per decision, not recorded as a limitation here).
- Residual refs after removal (acceptance 5): `PT_FBXImportRequest` → mapping doc row +
  provenance comment only; `serialize_fbx_import_request` → `Docs/70*`, `.recovery/`,
  `.handoff/`, and the stale `tests/` files above.

## Stage 5 — Runtime Acceptance

User-launched session, fresh log boundaries per AGENTS.md (UE PID 48391, Blender PID 50219,
feature boundary 2026-08-03 21:40:57+07, object guid `0FFB1A0324194666BFBB640177CD7DED`).

Round 1 (object `7877C50BCDFB4163A68DBE9ED759D49A`, 21:28:44+07 boundary) was **BLOCKED**:
Blender exported + enqueued 0x60 (225B) but UE never received it. Direct evidence chain:
`HEARTBEAT_TIMEOUT secondsSince=15.00` (21:29:06) → `TRANSPORT_DISCONNECT conn=1` → 0x60
enqueued ~2s later → reconnect `Accept conn=2` (21:29:09) → materials received,
`[MATERIAL][MATX_DEFER] reason=mesh_slot_count_not_ready`, no `[FBX]` importer markers.

Round 2 (21:40:57+07 boundary, single button press) — **PASS**:
- Blender: `[FBX][EXPORT] seq=14 ... verts=7505 tris=14518 mats=2`;
  `[FBX_SEND_DECISION] send_fbx=1 reason=geometry_changed`;
  `[FBX_ENQUEUE] payload_bytes=225 packet_type=0x60`; `[FBX_OP_DONE] totalMs=13456.3`.
- UE: `[FBX][AUTH] mark_pending reason=fbx_request_received` → `[FBX_SPAWN] actor=LS_FBX_0FFB1A03`
  → `[FBX_SET_MESH]` → `[FBX] Spawned StaticMeshActor: LS_FBX_0FFB1A03` →
  `[FBX][VALIDATE] meshValid=1 visible=1 slots=2` → materials applied both slots
  (`FBX_IMPORTED_APPLY` + `PERSISTENT_SLOT_OK`). No legacy 0x16 signs in either fresh slice.
- A heartbeat timeout occurred again (21:41:22.588) but the import completed ~0.6s before it;
  connection recovered (`Accept conn=2` 21:41:32).

## INV-2026-017 (independent transport investigation)

The round-1 loss was investigated as INV-2026-017 — "FBX request lost across heartbeat
disconnect" — and concluded (evidence-based, read-only):
- UE disconnect occurred ~2s **before** Blender's `send_msg` (Blender unaware; socket idle).
- `send_msg` only enqueues (`msg_transport.py:334`); the background sender thread does
  `sock.sendall` on a best-effort basis (`network.py:3295`); "enqueued" ≠ "delivered".
- Reconnect (`network.py:3501` / `sync.py:1491`) does **not** replay the FBX request
  (only capability announce + transform/material snapshot); UE sends no FBX ack.
- The 0x16 decommission did not touch the send path (`msg_transport.py`, `sync.py`, sender/
  queue/connect/heartbeat all unchanged — git diff scope) → the loss is pre-existing
  best-effort transport behavior, independent of this capability.
- Held as an independent investigation and a future input for a transport-reliability
  capability (ACK/replay or FBX resend); **not** part of Phase 1.5 decommission 0x16.

## Design Decisions

- **D1** — Remove the whole legacy surface in one capability (const + serializer + enum +
  comments), not incrementally. The semantic replacement is runtime-proven (MIG-005/006/
  Phase 1.5 Stage 5), satisfying the Phase 1.5 decommission gate.
- **D2** — Keep the FNV `LIVE_SYNC_PROTOCOL_SIG` (includes legacy 0x16/680) byte-identical:
  it is a wire-handshake compatibility hash, not a runtime dispatch dependency; changing it
  would needlessly break the handshake.
- **D3** — Preserve the historical provenance comment `UELiveSyncSubsystem.cpp:8709`
  ("Replaces the legacy PT_FBXImportRequest (0x16) inline block") — migration history,
  matching MIG-001..006 practice.
- **D4** — `Tests/Protocol/Phase1.3_Protocol_Mapping.md` keeps the legacy name in the
  mapping row (it is the living protocol mapping document); status field records the
  DECOMMISSIONED state.

## Invariants Preserved

- Wire format of `FBX_IMPORT_REQUEST` (0x60) unchanged; no packet layout change.
- `FFBXImportRequestPayload` (688B static_assert) and importer behavior unchanged.
- Handshake signature (`LIVE_SYNC_PROTOCOL_SIG` = 0x01D50692) unchanged.
- No regression in the tracked suite (0 new failures; 56+10+32 PASS).

## Rollback

Single `git revert` of this capability's commit. No wire-format change, no compatibility
risk: the removed symbols were not on the production send path.

## Open Questions

- Transport reliability for FBX requests (INV-2026-017) — to be scheduled as a future
  capability: longer heartbeat grace during export, or ACK/replay/resend for FBX.
- Stale `tests/` (root) manifest_v3 tests referencing the removed serializer — deferred to a
  cleanup capability.
