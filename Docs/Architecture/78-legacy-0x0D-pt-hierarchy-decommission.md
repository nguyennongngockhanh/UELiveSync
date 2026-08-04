# Phase 1.5: Legacy PT_Hierarchy (0x0D) Decommission

## Status: COMPLETE

Implementation complete: the legacy `PT_Hierarchy = 0x0D` packet surface was removed
from both the Blender addon and the UE plugin. Build PASS, regression PASS. Runtime
acceptance not applicable (see Stage 5).

This is the seventh capability of Phase 1.5 (Legacy Protocol Elimination), following
ADR-72 (0x16), ADR-73 (0x03), ADR-74 (0x04), ADR-75 (0x02), ADR-76 (0x0B), and ADR-77
(0x0C). Cadence: Investigation → Contract → Implementation → Build → Regression →
Runtime → ADR → Commit.

## Summary

Phase 1.5 inventory classified `0x0D` as REMOVE (dead code). Parent/child attachment is
fully replicated today through the semantic `MsgType.OBJECT_REPARENT = 0x24` message
(`msg_transport.py:62`) → `build_object_reparent` (`object_protocol.py:217`) → protocol
bridge → the semantic reparent handler (`UELiveSyncSubsystem.cpp` ~10100+). The legacy
`PT_Hierarchy` packet type had no Blender emitter, no UE dispatcher, and no
`kValidTypes` entry.

## Proof of Dead Code (0x0D)

| Legacy | Replacement | Runtime proven | Production callers | Safe to remove |
|---|---|---|---|---|
| `PT_Hierarchy` (0x0D) | `OBJECT_REPARENT` (0x24) | yes (semantic path) | 0 — no emitter (Blender sends only `MsgType.OBJECT_REPARENT`), no dispatcher (not in `ProcessBinaryPacket`), not in `kValidTypes`, dead serializer `serialize_hierarchy` had no callers | YES |

## Problem

The legacy `PT_Hierarchy` surface was fully dead code:

- UE enum `PT_Hierarchy = 0x0D` (`SyncTypes.h`) had no consumer — `0x0D` is absent from
  `kValidTypes` and has no dispatch block, so any `0x0D` packet fell to "Unknown packet
  type".
- The two UE `case 0x0D` stats blocks (`UELiveSyncSubsystem_Phase6I.inl`,
  `UELiveSyncSubsystem_Phase6H.inl`) were unreachable.
- `ValidatePacketOrdering` contained a dedicated "Duplicate attach detection
  (PT_Hierarchy)" logic block gated on `PktType == 0x0D` — unreachable because `0x0D`
  packets never arrive (the semantic path dedups via `GHierarchySequences` instead).
- Blender `serialize_hierarchy()` (`network.py`) had **zero callers** — no production
  path ever emitted a `PT_Hierarchy` packet; attachment is sent as `OBJECT_REPARENT`.
- A standalone comment block in `SyncTypes.h` documented a 44-byte wire format for a
  packet that no longer exists on the wire.

## Stage 1 — Investigation

- Wire status: `0x0D` is **not** in `kValidTypes`
  (`{ 0x01, 0x05, 0x06, 0x07, 0x08, 0x09, 0x0A, 0x0F, 0x11, 0x12, 0x13, 0x14, 0x17, 0x18,
  0x19, 0x1A, 0x1B }`, `UELiveSyncSubsystem.cpp:3371`) and has no `ProcessBinaryPacket`
  dispatch block.
- Blender live path: `sync.py:2025` emits `(MsgType.OBJECT_REPARENT, build_object_reparent(...))`
  — the semantic attachment detection (with the provenance comment at `sync.py:2023`:
  "replaces legacy PT_Hierarchy which UE removed in Phase 1.3.5a"). `OBJECT_REPARENT =
  0x24` (`msg_transport.py:62`).
- `serialize_hierarchy` (`network.py`): **no callers** — dead serializer with its own
  `_hierarchy_sequences` tracker dict.
- `ValidatePacketOrdering` "Duplicate attach detection (PT_Hierarchy)" block
  (`UELiveSyncSubsystem_Phase6H.inl`): gates on `PktType == 0x0D`, which never arrives;
  the semantic path performs stale/duplicate rejection via `GHierarchySequences`
  (`UELiveSyncSubsystem.cpp:10113`).
- Shared live machinery (KEPT): `FHierarchySequenceTracker` + `GHierarchySequences`
  (`SyncTypes.h`, `UELiveSyncSubsystem.cpp:249`) are used by the semantic reparent
  handler (`cpp:10113/10121/10188/10360/10396/10420`) and replay playback
  (`cpp:11222/11233/11337`), and cleared in `Diagnostics.inl:1478` /
  `UELiveSyncSubsystem.cpp:2740`.
- No world-replay `PacketType = 0x0D` marker exists (only 0x03/0x0C/0x0E markers in the
  world-replay recorder) and `EWorldReplayDomain` has no Hierarchy domain — nothing to
  keep or remove on that axis.
- FNV protocol signature: both sides contain `0x0D`
  (`SyncTypes.h` signature block; `network.py:63` mirror list). Kept unchanged for
  handshake compatibility (ADR-72 D2).
- Historical provenance comments (KEPT): `UELiveSyncSubsystem.cpp:3597` ("PT_Rename,
  PT_Hierarchy, PT_Delete_V5 removed in Phase 1.3.5a ..."), `sync.py:2023`.
- `Tests/Protocol/Phase1.3_Protocol_Mapping.md` row 9 recorded
  `| 13 | PT_Hierarchy | 0x0D | OBJECT_REPARENT | 0x24 | 1:1 | MAPPED |`. `0x24` is
  verified accurate (`OBJECT_REPARENT = 0x24`, `msg_transport.py:62`).
- Scope guard (C5 isolation): the dead hierarchy serializer in `network.py` is
  interleaved with the **live delete (V5) surface** — `_delete_sequences` and
  `serialize_delete()` (Phase 6E, capability C5). These were recorded as out of scope
  and verified untouched after the edit.
- Stale-test residuals (outside the regression suite; recorded in the backlog):
  `tests/phase7e_stage10a5a_reserved_packet_type_guard.py:91` (0x0D list, already
  stale at HEAD), `tests/e2e10_sceneoutliner_camera_workaround.py:141`
  ("PT_Hierarchy" in a protocol-symbol list), `tests/manual_e2e_scene_outliner_isolation.py:326`
  ("--hierarchy-confirm must send PT_Hierarchy"). Test-local fixtures
  `tests/phase6e_live_soak.py:62` and `tests/phase6d_hierarchy_validation.py:44` build
  packets directly and are valid residual.

## Stage 2 — Contract

Approved contract, one packet = one capability:
- **Will change (5 files + ADR + AGENTS.md):**
  - `SyncTypes.h`: remove `PT_Hierarchy = 0x0D` enum entry together with the orphaned
    "Phase 6: Semantic editor-event replication" comment (its only members, visibility
    and rename, were decommissioned in C1/C2) and the "Phase 6D: Hierarchy replication"
    comment; remove the "HIERARCHY PACKET (Phase 6D, PT_Hierarchy = 0x0D)" wire-format
    comment block.
  - `UELiveSyncSubsystem_Phase6I.inl`: remove unreachable `case 0x0D` stats block.
  - `UELiveSyncSubsystem_Phase6H.inl`: remove unreachable `case 0x0D` block and the
    entire "Duplicate attach detection (PT_Hierarchy)" logic block.
  - `network.py`: remove `PT_Hierarchy` constant, `serialize_hierarchy()` + comment
    block + `_hierarchy_sequences`, and the disconnect reset block.
  - `Phase1.3_Protocol_Mapping.md`: row 9 `MAPPED` → `DECOMMISSIONED` (semantic
    OBJECT_REPARENT 0x24 only).
- **Will NOT change:** semantic `OBJECT_REPARENT` (0x24) path, `build_object_reparent`,
  the semantic reparent handler, `FHierarchySequenceTracker`/`GHierarchySequences` +
  "HIERARCHY SEQUENCE TRACKER" comment, FNV signature (both sides), `kValidTypes` /
  `kValidFlags`, stats fields, historical provenance comments, mapping doc residual,
  stale tests, and critically **`serialize_delete()` / `_delete_sequences` (capability
  C5 surface)** — verified byte-identical after the edit.
- **Acceptance** (per user): no production **emitter**, **dispatcher**, **packet
  serializer**, or **enum/spec** of legacy `PT_Hierarchy` remain; semantic
  `OBJECT_REPARENT` (0x24) unchanged; **no diff in `serialize_delete()`** (proving C3
  does not affect C5). Allowed residual references: FNV handshake literals, historical
  provenance comments, documentation/tests.
- **Runtime:** skipped by user decision — 0x0D has no emitter, no dispatcher, no
  validator; hierarchy traffic is `OBJECT_REPARENT` (0x24) only, so runtime cannot prove
  the removal. Build + regression are complete acceptance.

## Stage 3 — Implementation

Removed 10 items:
- `SyncTypes.h`: `PT_Hierarchy = 0x0D` enum line + orphaned Phase 6 / Phase 6D comment
  blocks.
- `SyncTypes.h`: "HIERARCHY PACKET" comment block (wire-format spec of the dead packet).
- `UELiveSyncSubsystem_Phase6I.inl`: `case 0x0D` per-second stats block.
- `UELiveSyncSubsystem_Phase6H.inl`: `case 0x0D` before-create ordering block.
- `UELiveSyncSubsystem_Phase6H.inl`: "Duplicate attach detection (PT_Hierarchy)" logic
  block (`if (PktType == 0x0D ...)`).
- `network.py`: `PT_Hierarchy = 0x0D` constant.
- `network.py`: HIERARCHY SERIALIZATION comment block + `_hierarchy_sequences`.
- `network.py`: `serialize_hierarchy()` function.
- `network.py`: disconnect reset block for `_hierarchy_sequences`.
- `Phase1.3_Protocol_Mapping.md`: row 9 status → `DECOMMISSIONED`.

## Stage 4 — Build & Regression

- Build: `Build.sh ProjectTemplateEditor` — **SUCCEEDED** (24.45s, 14 actions, UBA local
  executor).
- `Tests/Protocol/tests/`: **56 passed**.
- `validate_protocol.py`: **10/10 PASS**.
- `cross_language_verify.py vectors/v1`: **32/32 PASS** (all three groups).
- Legacy `tests/` (root) suite: collection failure signature **byte-identical** at HEAD vs
  working tree (stash-verified): 0 regression.
- Acceptance: `rg "PT_Hierarchy|serialize_hierarchy|_hierarchy_sequences"` across
  `UE_Plugin/` + `Blender_Addon/` returns only the allowed residuals (`sync.py:2023`
  provenance comment, `SyncTypes.h:1048` dead stat field comment, `cpp:3597` provenance
  comment). `0x0D` remains only in the FNV signature literals on both sides.
- C5 isolation: `git diff Blender_Addon/network.py` shows **no** added/removed lines
  matching `serialize_delete|_delete_sequences`; `rg` confirms `serialize_delete()`,
  `_delete_sequences`, and the delete reset block are intact at their prior locations.

## Stage 5 — Runtime Acceptance: Not Applicable

`PT_Hierarchy` (0x0D) has no production emitter, no dispatcher, and no validator, and it
never participates in the current wire protocol — all attachment traffic uses the
semantic `OBJECT_REPARENT` (0x24) message. There is therefore no executable runtime
scenario specific to this capability; build and regression constitute complete
acceptance.

## Design Decisions

- **D1** — Remove the enum with the orphaned "Phase 6" and "Phase 6D" comment blocks:
  after C1/C2 the "Phase 6: Semantic editor-event replication" banner had no remaining
  members, and the "Phase 6D" banner described only `PT_Hierarchy`. Leaving orphaned
  banners would mislead future readers.
- **D2** — Remove the entire "Duplicate attach detection (PT_Hierarchy)" logic block
  from `ValidatePacketOrdering`: it only runs when `PktType == 0x0D`, which is never
  dispatched; the semantic path dedups via `GHierarchySequences`. Keeping it would leave
  dead code that misleads readers into thinking `0x0D` is still meaningful.
- **D3** — Keep `FHierarchySequenceTracker` / `GHierarchySequences`: they are exercised
  by the semantic reparent handler and replay playback, not by the legacy packet type.
- **D4** — Keep the FNV signature literals on both sides: handshake bytes (ADR-72 D2).
- **D5** — Keep `serialize_delete()` / `_delete_sequences` untouched: they belong to the
  Phase 6E delete (V5) surface, capability C5. Verified byte-identical in the diff.
- **D6** — Keep dead stats fields (`HierarchyPackets`, `HierarchyPacketsPerSecond`,
  `PacketHierarchyBeforeCreate`, `Phase6IPerSecondHierarchy`,
  `PacketDuplicateDetachDetected`, `PacketDuplicateAttachDetected`) as declarations:
  consistent with capabilities A–C2 (fields kept, unreachable increments removed).

## Invariants Preserved

- Semantic `OBJECT_REPARENT` (0x24) path unchanged: detection, serialization, bridge
  dispatch, reparent handler, sequence tracker.
- Delete (V5, 0x0E) surface unchanged (`serialize_delete`, `_delete_sequences`).
- FNV protocol signature unchanged (both sides).
- `kValidTypes` / `kValidFlags` unchanged (`0x0D` was never in `kValidTypes`).
- No packet layout change; `0x0D` had no wire presence to remove.
- No regression in the tracked suite (0 new failures; 56+10+32 PASS; root `tests/`
  signature identical).

## Rollback

Single `git revert` of this capability's commit. No wire-format change, no compatibility
risk: the removed surface was dead code, and the semantic reparent path was untouched.

## Open Questions

- None introduced. Next Phase 1.5 capabilities (one capability = one packet type):
  - C4 — `0x15` PT_ActiveCamera (camera domain)
  - C5 — `0x0E` PT_Delete_V5 (largest, needs dedicated investigation)
  - Then WAIT group: 0x05, 0x06, 0x08, 0x1B.
