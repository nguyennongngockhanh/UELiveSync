# Phase 1.5: Legacy PT_Rename (0x0C) Decommission

## Status: COMPLETE

Implementation complete: the legacy `PT_Rename = 0x0C` packet surface was removed from
both the Blender addon and the UE plugin. Build PASS, regression PASS. Runtime acceptance
not applicable (see Stage 5).

This is the sixth capability of Phase 1.5 (Legacy Protocol Elimination), following
ADR-72 (0x16), ADR-73 (0x03), ADR-74 (0x04), ADR-75 (0x02), and ADR-76 (0x0B). Cadence:
Investigation → Contract → Implementation → Build → Regression → Runtime → ADR → Commit.

## Summary

Phase 1.5 inventory classified `0x0C` as REMOVE (dead code). Rename is fully replicated
today through the semantic `MsgType.OBJECT_RENAME = 0x23` message (`msg_transport.py:61`)
→ `build_object_rename` (`object_protocol.py:189`) → protocol bridge → `HandleRename`
(`UELiveSyncSubsystem.cpp:9809`). The legacy `PT_Rename` packet type had no Blender
emitter, no UE dispatcher, and no `kValidTypes` entry.

## Proof of Dead Code (0x0C)

| Legacy | Replacement | Runtime proven | Production callers | Safe to remove |
|---|---|---|---|---|
| `PT_Rename` (0x0C) | `OBJECT_RENAME` (0x23) | yes (semantic path) | 0 — no emitter (Blender sends only `MsgType.OBJECT_RENAME`), no dispatcher (not in `ProcessBinaryPacket`), not in `kValidTypes`, dead serializer `serialize_rename` had no callers | YES |

## Problem

The legacy `PT_Rename` surface was fully dead code:

- UE enum `PT_Rename = 0x0C` (`SyncTypes.h`) had no consumer — `0x0C` is absent from
  `kValidTypes` and has no dispatch block, so any `0x0C` packet fell to "Unknown packet
  type".
- `struct FLiveSyncRenamePacket` (`SyncTypes.h`) had **zero usages** repo-wide (declared
  but never referenced) — dead payload struct alongside a dead wire-format comment block.
- The two UE `case 0x0C` stats blocks (`UELiveSyncSubsystem_Phase6I.inl`,
  `UELiveSyncSubsystem_Phase6H.inl`) were unreachable.
- Blender `serialize_rename()` (`network.py`) had **zero callers** — no production path
  ever emitted a `PT_Rename` packet; rename is sent as `OBJECT_RENAME`.

## Stage 1 — Investigation

- Wire status: `0x0C` is **not** in `kValidTypes`
  (`{ 0x01, 0x05, 0x06, 0x07, 0x08, 0x09, 0x0A, 0x0F, 0x11, 0x12, 0x13, 0x14, 0x17, 0x18,
  0x19, 0x1A, 0x1B }`, `UELiveSyncSubsystem.cpp:3371`) and has no `ProcessBinaryPacket`
  dispatch block.
- Blender live path: `sync.py:1999` emits `(MsgType.OBJECT_RENAME, build_object_rename(...))`
  — consumed at `sync.py:2759` (SEND RENAME). `OBJECT_RENAME = 0x23`
  (`msg_transport.py:61`). No `PT_Rename` packet emission anywhere.
- `serialize_rename` (`network.py:2729`): **no callers** — dead serializer with its own
  `_rename_sequences` tracker dict.
- `FLiveSyncRenamePacket` (`SyncTypes.h:1552`): declared, never used.
- `ValidatePacketOrdering` (`UELiveSyncSubsystem_Phase6H.inl:64`) runs on every packet in
  the dispatch loop; its `case 0x0C` is unreachable because `0x0C` packets never arrive.
- Shared live machinery (KEPT): `FRenameSequenceTracker` + `GRenameSequences`
  (`SyncTypes.h`, `UELiveSyncSubsystem.cpp:236`) are **not** legacy — they are used by the
  semantic path: `HandleRename` (`UELiveSyncSubsystem.cpp:9854/9861/9907`) and cleared in
  `Diagnostics.inl:1453` / `UELiveSyncSubsystem.cpp:2734`.
- World-replay (KEPT): `EWorldReplayDomain::Rename` is used by `Replay.inl:917/1297`,
  `UELiveSyncSubsystem_Phase6H.inl:442/744`, and the replay recording site
  `UELiveSyncSubsystem.cpp:9934-9935` sets `Domain = Rename` with an informational
  `PacketType = 0x0C` — a UE-internal replay marker (same rationale as the 0x03/0x04
  markers kept in capabilities A and B), not wire protocol.
- FNV protocol signature: both sides contain `0x0C`
  (`SyncTypes.h` signature block; `network.py:63` mirror list). Kept unchanged for
  handshake compatibility (ADR-72 D2).
- Historical provenance comments (KEPT): `UELiveSyncSubsystem.cpp:121` (rename vertical
  slice `PT_Rename = 0x0C`), `UELiveSyncSubsystem.cpp:3597` ("PT_Rename, PT_Hierarchy,
  PT_Delete_V5 removed in Phase 1.3.5a ... now flow exclusively through Bridge"),
  `UELiveSyncSubsystem.h:318` ("default=false preserves old PT_Rename path").
- `Tests/Protocol/Phase1.3_Protocol_Mapping.md` row 8 recorded
  `| 12 | PT_Rename | 0x0C | OBJECT_RENAME | 0x23 | 1:1 | MAPPED |`. `0x23` is verified
  accurate (`OBJECT_RENAME = 0x23`, `msg_transport.py:61`).
- Stale-test residual (outside the regression suite; recorded in the backlog):
  `tests/phase6b_runtime_audit.py:269-318` asserts `PT_Rename` is defined in
  `SyncTypes.h`, present in `kValidTypes`, and has an early return in
  `ProcessBinaryPacket` — goes stale after this capability. The root `tests/` suite
  already fails collection at HEAD (module-level `sys.exit(1)`), so this does not affect
  the tracked regression suite.

## Stage 2 — Contract

Approved contract, one packet = one capability:
- **Will change (5 files + ADR + AGENTS.md):**
  - `SyncTypes.h`: remove `PT_Rename = 0x0C` enum entry; remove the "RENAME PACKET
    (Phase 6, PT_Rename = 0x0C)" wire-format comment block; remove the unused
    `FLiveSyncRenamePacket` struct; trim `Rename = 3, // PT_Rename` to
    `Rename = 3, // OBJECT_RENAME (semantic replay)`.
  - `UELiveSyncSubsystem_Phase6I.inl`: remove unreachable `case 0x0C` stats block.
  - `UELiveSyncSubsystem_Phase6H.inl`: remove unreachable `case 0x0C` block in
    `ValidatePacketOrdering`.
  - `network.py`: remove `PT_Rename` constant, `serialize_rename()` + comment block +
    `_rename_sequences`, and the disconnect reset block.
  - `Phase1.3_Protocol_Mapping.md`: row 8 `MAPPED` → `DECOMMISSIONED` (semantic
    OBJECT_RENAME 0x23 only).
- **Will NOT change:** `FRenameSequenceTracker`/`GRenameSequences` + "RENAME SEQUENCE
  TRACKER" comment (live via semantic `HandleRename`), `HandleRename`, the whole
  `OBJECT_RENAME` 0x23 path, `EWorldReplayDomain::Rename` value + world-replay recording
  marker (`cpp:9934-9935`), `ReplayDomainRenameHash` stat, FNV signature (both sides),
  `kValidTypes` / `kValidFlags`, stats fields `Phase6IPerSecondRenames` /
  `PacketRenameBeforeCreate` (fields kept, per capability A/B precedent), historical
  provenance comments, mapping doc residual, stale tests (backlog).
- **Acceptance** (per user): no production **emitter**, **dispatcher**, **packet
  serializer**, or **enum/spec** of legacy `PT_Rename` remain. Allowed residual
  references: FNV handshake literals, world-replay markers/domains, historical
  provenance comments, documentation/tests.
- **Runtime:** skipped by user decision — 0x0C has no emitter, no dispatcher, no
  validator; rename traffic is `OBJECT_RENAME` (0x23) only, so runtime cannot prove the
  removal. Build + regression are complete acceptance.

## Stage 3 — Implementation

Removed 9 items:
- `SyncTypes.h`: `PT_Rename = 0x0C` enum line.
- `SyncTypes.h`: "RENAME PACKET" comment block (wire-format spec of the dead packet).
- `SyncTypes.h`: `struct FLiveSyncRenamePacket` (never used).
- `SyncTypes.h`: `EWorldReplayDomain::Rename` comment → `// OBJECT_RENAME (semantic
  replay)` (no longer a legacy packet).
- `UELiveSyncSubsystem_Phase6I.inl`: `case 0x0C` per-second stats block.
- `UELiveSyncSubsystem_Phase6H.inl`: `case 0x0C` block in `ValidatePacketOrdering`.
- `network.py`: `PT_Rename = 0x0C` constant.
- `network.py`: `serialize_rename()` function + its comment block + `_rename_sequences`.
- `network.py`: disconnect reset block for `_rename_sequences`.
- `Phase1.3_Protocol_Mapping.md`: row 8 status → `DECOMMISSIONED`.

## Stage 4 — Build & Regression

- Build: `Build.sh ProjectTemplateEditor` — **SUCCEEDED** (24.03s, 14 actions, UBA local
  executor).
- `Tests/Protocol/tests/`: **56 passed**.
- `validate_protocol.py`: **10/10 PASS**.
- `cross_language_verify.py vectors/v1`: **32/32 PASS** (all three groups).
- Legacy `tests/` (root) suite: collection failure signature **byte-identical** at HEAD vs
  working tree (stash-verified): 0 regression.
- Acceptance: `rg "PT_Rename|serialize_rename|_rename_sequences"` across `UE_Plugin/` +
  `Blender_Addon/` returns only the three allowed historical provenance comments
  (`cpp:121`, `cpp:3597`, `Subsystem.h:318`). `0x0C` remains only in the FNV signature
  literals (both sides, handshake) and the world-replay recording marker (`cpp:9935`,
  internal).

## Stage 5 — Runtime Acceptance: Not Applicable

`PT_Rename` (0x0C) has no production emitter, no dispatcher, and no validator, and it
never participates in the current wire protocol — all rename traffic uses the semantic
`OBJECT_RENAME` (0x23) message. There is therefore no executable runtime scenario
specific to this capability; build and regression constitute complete acceptance.

## Design Decisions

- **D1** — Remove the enum, the wire-format comment block, and the unused
  `FLiveSyncRenamePacket` struct together: the struct existed only to describe the dead
  packet and had no callers; this ADR preserves the history.
- **D2** — Keep `FRenameSequenceTracker` / `GRenameSequences`: they are exercised by the
  semantic `HandleRename` stale/duplicate rejection path, not by the legacy packet type.
  Removing them would break the live `OBJECT_RENAME` flow.
- **D3** — Trim the `EWorldReplayDomain::Rename` comment to reference `OBJECT_RENAME`:
  the domain now represents semantic rename replay, not a `PT_Rename` packet; the enum
  value and all replay logic are untouched.
- **D4** — Keep the FNV signature literals on both sides: they are handshake bytes
  (ADR-72 D2), not derived from the enum.
- **D5** — Keep the world-replay recording marker `PacketType = 0x0C` (`cpp:9935`): a
  UE-internal replay entry field, same class as the 0x03 markers kept in capability A.
- **D6** — Keep historical provenance comments (`cpp:121`, `cpp:3597`, `Subsystem.h:318`):
  they document why these packet types no longer flow on the wire (Phase 1.3.5a
  migration) and the parameter-default rationale; allowed residual per acceptance.

## Invariants Preserved

- Semantic `OBJECT_RENAME` (0x23) path unchanged: detection, serialization, bridge
  dispatch, `HandleRename`, sequence tracker.
- World-replay rename domain unchanged: enum value, recording, playback, ordering
  validation, drift stats.
- FNV protocol signature unchanged (both sides).
- `kValidTypes` / `kValidFlags` unchanged (`0x0C` was never in `kValidTypes`).
- No packet layout change; `0x0C` had no wire presence to remove.
- No regression in the tracked suite (0 new failures; 56+10+32 PASS; root `tests/`
  signature identical).

## Rollback

Single `git revert` of this capability's commit. No wire-format change, no compatibility
risk: the removed surface was dead code, and the semantic rename path was untouched.

## Open Questions

- None introduced. Next Phase 1.5 capabilities (one capability = one packet type):
  - C3 — `0x0D` PT_Hierarchy (Phase 6D)
  - C4 — `0x15` PT_ActiveCamera (camera domain)
  - C5 — `0x0E` PT_Delete_V5 (largest, needs dedicated investigation)
  - Then WAIT group: 0x05, 0x06, 0x08, 0x1B.
