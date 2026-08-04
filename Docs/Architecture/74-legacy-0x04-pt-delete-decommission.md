# Phase 1.5: Legacy PT_Delete (0x04) Decommission

## Status: COMPLETE — Runtime acceptance PASS

Implementation complete: the legacy **`PT_Delete = 0x04`** surface was removed. The
semantic `OBJECT_DELETE` (0x22) message (MIG-001, Phase 6E) is now the only object-delete
path. Build PASS, regression PASS, runtime acceptance PASS.

> **Naming note:** this capability decommissions legacy `PT_Delete` (0x04) only. The
> distinct `PT_Delete_V5` (0x0E) surface — `serialize_delete` serializer, Phase 6E
> tracker, `DeletePackets` counter, `case 0x0E` stats, V5 wire layout — is **kept
> intact** and belongs to a later Phase 1.5 capability.

This is the third capability of Phase 1.5 (Legacy Protocol Elimination), following
ADR-72 (0x16) and ADR-73 (0x03). Cadence: Investigation → Contract → Implementation →
Build → Regression → Runtime → ADR → Commit.

## Summary

Phase 1.5 inventory classified `0x04` PT_Delete as REMOVE. Like 0x03, `PT_Delete` was
already **unreachable on the wire**: `0x04` is not in `kValidTypes` and has no dispatch
in `ProcessBinaryPacket` (falls through to "Unknown packet type"). The Blender addon had
a dead accumulation list (`deletes_to_send`) and a legacy serializer
(`serialize_delete_v3`) that were never consumed.

## Proof of Semantic Replacement (0x04 → 0x22)

| Legacy | Replacement | Runtime proven | Production callers | Safe to remove |
|---|---|---|---|---|
| `PT_Delete` (0x04), bare-GUID serializer `serialize_delete_v3`, dead list `deletes_to_send` | `OBJECT_DELETE` (0x22) via `build_object_delete` + `LiveSyncProtocolBridge` → Phase 6E handlers (seq + ts + tombstone) | MIG-001, Phase 1.5 runtime (below) | 0 — UE never dispatches 0x04; Blender never sends it (`deletes_to_send` was never consumed) | YES |

## Problem

MIG-001 migrated object deletion to the semantic `OBJECT_DELETE` (0x22) message with full
Phase 6E semantics (stale-rejection, tombstone gate, child-detach cascade), but the legacy
0x04 surface survived as dead weight: the Blender addon still declared a `deletes_to_send`
list, appended legacy 16-byte-GUID payloads to it via `serialize_delete_v3`, and never sent
them (the list was never consumed). UE kept an unused `PT_Delete` enum entry and an
unreachable `case 0x04` stats counter. Phase 1.5 eliminates the remaining legacy protocol
surface once the semantic replacement is proven.

## Stage 1 — Investigation

- Wire status: `0x04` is **not** in `kValidTypes` (`UELiveSyncSubsystem.cpp:3371`) and has
  **no** `PacketType == 0x04` dispatch block in `ProcessBinaryPacket`. The dispatch chain
  handles `0x01` (object loop) plus `0x05,0x06,0x07,0x0F,0x11,0x12,0x13,0x14,0x17,0x18,0x19,0x1B`
  — `0x04` falls to "Unknown packet type".
- Blender addon: no `PT_Delete` constant. Dead accumulation: `deletes_to_send` declared
  (`sync.py`), appended `serialize_delete_v3(guid_obj)` on delete, **never consumed**
  (no `send_objects(deletes_to_send, ...)` call exists). `serialize_delete_v3`
  (`network.py`) emits a bare 16-byte GUID (V3-era 0x04 format); its only callers were the
  dead append and two import lines.
- The live delete path is `delete_msgs_to_send` → `(MsgType.OBJECT_DELETE,
  build_object_delete(guid_obj))` → `transport.send_msg`, dispatched in UE by
  `LiveSyncProtocolBridge.h` (MsgType dispatcher) → Phase 6E handlers.
- `deletes_to_send_scan` (`sync.py`) captured `scan_scene()`'s returned stale-count but was
  never read — a dead variable serving only the old list.
- UE surface: enum `PT_Delete = 0x04` (`SyncTypes.h`), `Lifecycle` comment listing
  `PT_Delete`, unreachable `case 0x04` stats (`Phase6I.inl`, shares the block with
  `case 0x0E`). `Phase6IPerSecondDeletes` counter kept (still counts 0x0E).
- **Kept (out of scope):** world-replay marker `Replay.inl:902` (`Entry.PacketType == 0x04`)
  and comment `:901` — UE-internal replay domain, not wire; historical comments
  (`UELiveSyncSubsystem.cpp:3597`, `sync.py` Phase 1.5 provenance comment); **all** of the
  `PT_Delete_V5` (0x0E) surface.
- Regression-safe: root `tests/` define their own `PT_Delete = 0x04` literal (no addon
  imports); `Tests/Protocol` mapping doc row 4 is the only ref there.

## Stage 2 — Contract

Approved contract (user-adjusted), one packet = one capability:
- **Will change (6 files):** `Blender_Addon/sync.py` (`deletes_to_send` declaration +
  dead append + `deletes_to_send_scan` dead variable + `serialize_delete_v3` imports,
  both try/except blocks), `Blender_Addon/network.py` (`serialize_delete_v3` function),
  `SyncTypes.h` (enum `PT_Delete = 0x04` + `Lifecycle` comment trim),
  `UELiveSyncSubsystem_Phase6I.inl` (`case 0x04`), `Tests/Protocol/Phase1.3_Protocol_Mapping.md`
  (row 4), AGENTS.md (Phase 1.5 status).
- **Will NOT change:** `OBJECT_DELETE` (0x22) path via `LiveSyncProtocolBridge` + Phase 6E
  handlers, `PT_Delete_V5` (0x0E) surface (serializer, tracker, counter, V5 layout,
  `case 0x0E`), FNV protocol signature (contains `0x04` — handshake compat, per ADR-72 D2;
  same policy as 0x03 kept in capability A), world-replay markers, historical comments,
  `kValidTypes`/`kValidFlags`.
- **Acceptance:** no production emitter, dispatcher, or enum of `PT_Delete` (0x04) remains;
  runtime smoke (create + delete, OBJECT_DELETE works, no 0x04 on wire); build + regression
  PASS.

## Stage 3 — Implementation

`+6/-45` lines across 5 code files (plus AGENTS.md):
- `sync.py`: removed `deletes_to_send` declaration, the dead
  `deletes_to_send.append(serialize_delete_v3(guid_obj))` call (comment trimmed to
  "emit OBJECT_DELETE via MsgType"), the unused `deletes_to_send_scan` variable (the
  `scan_scene()` call itself kept — functional), and the `serialize_delete_v3` imports in
  both try/except import blocks.
- `network.py`: removed the `serialize_delete_v3` function (bare 16-byte GUID serializer).
- `SyncTypes.h`: removed enum `PT_Delete = 0x04`; trimmed `Lifecycle` comment to
  `PT_Delete_V5`.
- `UELiveSyncSubsystem_Phase6I.inl`: removed unreachable `case 0x04` stats block
  (kept `case 0x0E: // PT_Delete_V5`).
- `Phase1.3_Protocol_Mapping.md`: row 4 → `MAPPED (Phase 1.5: legacy 0x04 DECOMMISSIONED;
  semantic OBJECT_DELETE 0x22 only)`.
- AGENTS.md: Phase 1.5 capability list — 0x16 (07ca0f0, ADR-72) and 0x03 (0d49d18,
  ADR-73) COMPLETE, current is 0x04.

## Stage 4 — Build & Regression

- Build: `Build.sh ProjectTemplateEditor` — **SUCCEEDED** (14 actions, plugin linked, 23.92s).
- `Tests/Protocol/tests/`: **56 passed**.
- `validate_protocol.py`: **10/10 PASS**.
- `cross_language_verify.py vectors/v1`: **32/32 PASS** (all three groups).
- Legacy `tests/` (root) suite: collection failure signature **byte-identical** at HEAD vs
  working tree (stash-verified): 0 regression. No root test imports the removed symbols;
  root tests define their own `PT_Delete = 0x04` literal.
- Acceptance: `rg` confirms no production emitter/dispatcher/enum of `PT_Delete` (0x04)
  remains; residual refs are historical comments and the FNV signature value only.

## Stage 5 — Runtime Acceptance

User-launched session, fresh log boundaries per AGENTS.md (UE PID 22242, Blender PID 23925,
connection boundary 2026-08-04 09:02:49+07, feature boundary 09:03:17+07). Evidence saved
to `.evidence/runtime/cap-b-0x04-delete/`.

Connection (PASS):
- Port 57000 LISTEN; UE log `[PACKET_DISPATCH] type=0x07` heartbeats flowing (seq 6→8).
- Blender `[DIAG][CAP] CapabilityResponse received mask=0x000007F0`;
  `[OBJ][MSGTYPE] OBJECT_CREATE sent=2` for the pre-existing scene objects; UE
  `[MESH] Promoted ProcMesh to root` for the chair GUID.

Feature test (PASS) — create Cube.001, delete Cube.001:
- Create — Blender: `[DISCOVER] guid=38c7e9cd... name=Cube.001`,
  `[SPAWN-TRACE][SEND]`, `[OBJ][MSGTYPE] OBJECT_CREATE sent=1`. UE:
  `[BRIDGE][OBJECT_CREATE] id=cde9c738-... name=Cube.001 primitive_type=0 seq=1`,
  `[SPAWN-TRACE][CREATE] guid=38C7E9CD7D9A4FB0BA1630DEE27DE585 prim=0x00`.
- Delete — Blender: `[DELETE][MSGTYPE] OBJECT_DELETE sent=1`. UE:
  `[BRIDGE][OBJECT_DELETE] id=cde9c738-...`,
  `[DELETE][APPLY] Destroying actor GUID=38C7E9CD7D9A4FB0BA1630DEE27DE585 Name=Actor_2 Seq=1`.
- No `0x04` on wire: dispatched packet types in the fresh UE slice were only
  `0x06` (mesh), `0x07` (heartbeat), `0x08` (asset def). No `PT_Delete` /
  `serialize_delete_v3` / `deletes_to_send` in either fresh slice.

Result: **PASS** — OBJECT_CREATE and OBJECT_DELETE semantic paths both work; no legacy
0x04 traffic.

## Design Decisions

- **D1** — Remove the whole legacy 0x04 surface in one capability (dead list + dead var +
  serializer + imports + enum + stats case). The semantic replacement is runtime-proven
  (MIG-001, Phase 1.5 Stage 5); there is no live 0x04 consumer left.
- **D2** — Explicitly separate 0x04 from 0x0E: this capability removes only `PT_Delete`
  (0x04). `PT_Delete_V5` (0x0E) is a distinct, still-active surface owned by a later
  Phase 1.5 capability; the `Lifecycle` comment and `case 0x0E` stats keep their 0x0E
  references.
- **D3** — Keep the FNV protocol signature (contains `0x04`): it is a wire-handshake
  compatibility hash, not a runtime dispatch dependency (same policy as ADR-72 D2 for 0x16
  and ADR-73 for 0x03, kept byte-identical).
- **D4** — Keep the world-replay literal `0x04` marker (`Replay.inl:902`): UE-internal
  replay domain, functionally unrelated to the legacy wire packet.
- **D5** — Keep `Phase6IPerSecondDeletes` counter field and reporting: still counts 0x0E;
  removal would touch unrelated stats plumbing.
- **D6** — `Tests/Protocol/Phase1.3_Protocol_Mapping.md` keeps the legacy name in the
  mapping row (living protocol mapping document); status field records DECOMMISSIONED,
  following the row-3 (0x03) and row-21 (0x16) precedents.

## Invariants Preserved

- Wire format of `OBJECT_DELETE` (0x22) unchanged; no packet layout change.
- `PT_Delete_V5` (0x0E) surface unchanged (serializer, tracker, counter, V5 layout).
- `LiveSyncProtocolBridge` / Phase 6E delete handlers unchanged.
- FNV protocol signature unchanged.
- `kValidTypes` / `kValidFlags` unchanged.
- No regression in the tracked suite (0 new failures; 56+10+32 PASS; root `tests/`
  signature identical).

## Rollback

Single `git revert` of this capability's commit. No wire-format change, no compatibility
risk: the removed symbols were never on a working production path (UE dropped 0x04).

## Open Questions

- None introduced. Next Phase 1.5 capability (per AGENTS.md ordering):
  - Capability C: 0x0B–0x0E + 0x15 group (includes removing `PT_Delete_V5` 0x0E,
    `PT_Visibility` 0x0B, `PT_Rename` 0x0C, `PT_Hierarchy` 0x0D, `PT_ActiveCamera` 0x15).
  - Capability D: `0x02` Reserved.
  - WAIT group afterwards: 0x05, 0x06, 0x08, 0x1B.
