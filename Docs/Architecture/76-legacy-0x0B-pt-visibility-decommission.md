# Phase 1.5: Legacy PT_Visibility (0x0B) Decommission

## Status: COMPLETE

Implementation complete: the legacy `PT_Visibility = 0x0B` packet surface was removed
from both the Blender addon and the UE plugin. Build PASS, regression PASS. Runtime
acceptance not applicable (see Stage 5).

This is the fifth capability of Phase 1.5 (Legacy Protocol Elimination), following
ADR-72 (0x16), ADR-73 (0x03), ADR-74 (0x04), and ADR-75 (0x02). Cadence: Investigation →
Contract → Implementation → Build → Regression → Runtime → ADR → Commit.

## Summary

Phase 1.5 inventory classified `0x0B` as REMOVE (dead code). Visibility is fully
replicated today through the semantic `MsgType.OBJECT_VISIBILITY = 0x25` message
(`msg_transport.py:63`) → `build_object_visibility` → protocol bridge (`case
livesync::MsgType::OBJECT_VISIBILITY`, `LiveSyncProtocolBridge.h:176`/`:1550`) →
`HandleVisibility` (`UELiveSyncSubsystem.cpp:9949`). The legacy `PT_Visibility` packet
type had no Blender emitter, no UE dispatcher, and no `kValidTypes` entry.

## Proof of Dead Code (0x0B)

| Legacy | Replacement | Runtime proven | Production callers | Safe to remove |
|---|---|---|---|---|
| `PT_Visibility` (0x0B) | `OBJECT_VISIBILITY` (0x25) | yes (semantic path) | 0 — no emitter (Blender sends only `MsgType.OBJECT_VISIBILITY`), no dispatcher (not in `ProcessBinaryPacket`), not in `kValidTypes`, dead serializer `serialize_visibility` had no callers | YES |

## Problem

The legacy `PT_Visibility` surface was fully dead code:

- UE enum `PT_Visibility = 0x0B` (`SyncTypes.h`) had no consumer — `0x0B` is absent from
  `kValidTypes` and has no dispatch block, so any `0x0B` packet fell to "Unknown packet
  type".
- The two UE `case 0x0B` stats blocks (`UELiveSyncSubsystem_Phase6I.inl`,
  `UELiveSyncSubsystem_Phase6H.inl`) were unreachable.
- Blender `serialize_visibility()` (`network.py`) had **zero callers** — no production
  path ever emitted a `PT_Visibility` packet; visibility is sent as `OBJECT_VISIBILITY`.
- A standalone comment block in `SyncTypes.h` documented a 29-byte wire format for a
  packet that no longer exists on the wire.

## Stage 1 — Investigation

- Wire status: `0x0B` is **not** in `kValidTypes`
  (`{ 0x01, 0x05, 0x06, 0x07, 0x08, 0x09, 0x0A, 0x0F, 0x11, 0x12, 0x13, 0x14, 0x17, 0x18,
  0x19, 0x1A, 0x1B }`, `UELiveSyncSubsystem.cpp:3371`) and has no `ProcessBinaryPacket`
  dispatch block.
- Blender live path: `sync.py:1979-1985` detects visibility changes and emits
  `(MsgType.OBJECT_VISIBILITY, build_object_visibility(...))` — consumed at
  `sync.py:2776-2780`. No `PT_Visibility` packet emission anywhere. `rg PT_Visibility`
  in `Blender_Addon/` found only the constant and the serializer comment.
- `serialize_visibility` (`network.py:2778`): **no callers** — dead serializer with its
  own `_visibility_sequences` tracker dict.
- `ValidatePacketOrdering` (`UELiveSyncSubsystem_Phase6H.inl:64`) runs on every packet in
  the dispatch loop (`UELiveSyncSubsystem.cpp:2961`); its `case 0x0B` is unreachable
  because `0x0B` packets never arrive.
- Shared live machinery (KEPT): `FVisibilitySequenceTracker` + `GVisibilitySequences`
  (`SyncTypes.h`, `UELiveSyncSubsystem.cpp:246`) are **not** legacy — they are used by the
  semantic path: `HandleVisibility` (`UELiveSyncSubsystem.cpp:9993/10000/10035`) and
  `CheckVisibilityAuthority` (`UELiveSyncSubsystem_Phase6H.inl:271`).
- FNV protocol signature: both sides contain `0x0B`
  (`SyncTypes.h` signature block; `network.py:63` mirror list). Kept unchanged for
  handshake compatibility (ADR-72 D2) — removing the enum cannot affect the hash because
  the hash bytes are literal, not derived from the enum.
- `Tests/Protocol/Phase1.3_Protocol_Mapping.md` row 7 recorded
  `| 11 | PT_Visibility | 0x0B | OBJECT_VISIBILITY | 0x25 | 1:1 | MAPPED |`. `0x25` is
  verified accurate (`OBJECT_VISIBILITY = 0x25`, `msg_transport.py:63`).
- Stale-test residuals (outside the regression suite; recorded in the backlog):
  - `tests/phase7e_stage10a4_blender_visibility_e2e.py:139-141` asserts
    `"def serialize_visibility(" in source` — becomes stale after this capability.
  - `tests/phase7e_stage10a5a_reserved_packet_type_guard.py:89` lists literal `0x0B` —
    already stale at HEAD (0x03 removed in capability A).
  - `tests/phase6_visibility_validation.py:28` and `tests/phase6e_live_soak.py:60`
    define local `PT_Visibility = 0x0B` test fixtures that build packets directly —
    test-local constants, valid residual.

## Stage 2 — Contract

Approved contract, one packet = one capability:
- **Will change (5 files + ADR + AGENTS.md):**
  - `SyncTypes.h`: remove `PT_Visibility = 0x0B` enum entry + the standalone
    "VISIBILITY PACKET (Phase 6, PT_Visibility = 0x0B)" wire-format comment block.
  - `UELiveSyncSubsystem_Phase6I.inl`: remove unreachable `case 0x0B` stats block.
  - `UELiveSyncSubsystem_Phase6H.inl`: remove unreachable `case 0x0B` block in
    `ValidatePacketOrdering`.
  - `network.py`: remove `PT_Visibility` constant, the `serialize_visibility()`
    function + comment block + `_visibility_sequences`, and the disconnect reset block.
  - `Phase1.3_Protocol_Mapping.md`: row 7 `MAPPED` → `DECOMMISSIONED` (semantic
    OBJECT_VISIBILITY 0x25 only).
- **Will NOT change:** `FVisibilitySequenceTracker`/`GVisibilitySequences` + "VISIBILITY
  SEQUENCE TRACKER" comment (live via semantic `HandleVisibility`), `HandleVisibility`,
  the whole `OBJECT_VISIBILITY` 0x25 path, FNV signature (both sides), `kValidTypes` /
  `kValidFlags`, stats fields `VisibilityProcessed` / `PacketVisibilityBeforeCreate` /
  `Phase6IPerSecondVisibility` (fields kept, per capability A/B precedent), mapping doc
  residual, stale tests (backlog).
- **Acceptance** (refined per user): no production **emitter**, **dispatcher**,
  **packet serializer**, or **enum/spec** of legacy `PT_Visibility` remain. Allowed
  residual references: FNV handshake literal, test fixtures, documentation.
- **Runtime:** skipped by user decision — 0x0B has no emitter, no dispatcher, no
  validator; runtime traffic is `OBJECT_VISIBILITY` (0x25) only, so runtime cannot prove
  the removal. Build + regression are complete acceptance.

## Stage 3 — Implementation

Removed 8 items:
- `SyncTypes.h`: `PT_Visibility = 0x0B` enum line.
- `SyncTypes.h`: 19-line "VISIBILITY PACKET" comment block (wire-format spec of the dead
  packet; history preserved in this ADR instead of describing a non-existent wire format
  in source).
- `UELiveSyncSubsystem_Phase6I.inl`: `case 0x0B` per-second stats block.
- `UELiveSyncSubsystem_Phase6H.inl`: `case 0x0B` block in `ValidatePacketOrdering`.
- `network.py`: `PT_Visibility = 0x0B` constant.
- `network.py`: `serialize_visibility()` function + its comment block + `_visibility_sequences`.
- `network.py`: disconnect reset block for `_visibility_sequences`.
- `Phase1.3_Protocol_Mapping.md`: row 7 status → `DECOMMISSIONED`.

## Stage 4 — Build & Regression

- Build: `Build.sh ProjectTemplateEditor` — **SUCCEEDED** (23.91s, 14 actions, UBA local
  executor).
- `Tests/Protocol/tests/`: **56 passed**.
- `validate_protocol.py`: **10/10 PASS**.
- `cross_language_verify.py vectors/v1`: **32/32 PASS** (all three groups).
- Legacy `tests/` (root) suite: collection failure signature **byte-identical** at HEAD vs
  working tree (stash-verified): 0 regression.
- Acceptance: `rg "PT_Visibility|serialize_visibility|_visibility_sequences"` across
  `UE_Plugin/` + `Blender_Addon/` returns nothing. `0x0B` remains only in the FNV
  signature literals on both sides (handshake, kept by contract).

## Stage 5 — Runtime Acceptance: Not Applicable

`PT_Visibility` (0x0B) has no production emitter, no dispatcher, and no validator, and it
never participates in the current wire protocol — all visibility traffic uses the
semantic `OBJECT_VISIBILITY` (0x25) message. There is therefore no executable runtime
scenario specific to this capability; build and regression constitute complete acceptance.

## Design Decisions

- **D1** — Remove the enum entry and the standalone wire-format comment block together:
  the comment described only the decommissioned 29-byte packet format. Preserving that
  spec in source would document a wire format that no longer exists; this ADR retains the
  history.
- **D2** — Keep `FVisibilitySequenceTracker` / `GVisibilitySequences`: despite the
  "Phase 6" naming, they are exercised by the semantic `HandleVisibility` stale/duplicate
  rejection path, not by the legacy packet type. Removing them would break the live
  `OBJECT_VISIBILITY` flow.
- **D3** — Keep the FNV signature literals on both sides: they are handshake bytes
  (ADR-72 D2), not derived from the enum; changing them would break session
  compatibility.
- **D4** — Keep the per-second and ordering stats fields (`Phase6IPerSecondVisibility`,
  `PacketVisibilityBeforeCreate`) but remove only the unreachable `case 0x0B` blocks that
  incremented them — consistent with capabilities A and B.

## Invariants Preserved

- Semantic `OBJECT_VISIBILITY` (0x25) path unchanged: detection, serialization, bridge
  dispatch, `HandleVisibility`, sequence tracker.
- FNV protocol signature unchanged (both sides).
- `kValidTypes` / `kValidFlags` unchanged (`0x0B` was never in `kValidTypes`).
- No packet layout change; `0x0B` had no wire presence to remove.
- No regression in the tracked suite (0 new failures; 56+10+32 PASS; root `tests/`
  signature identical).

## Rollback

Single `git revert` of this capability's commit. No wire-format change, no compatibility
risk: the removed surface was dead code, and the semantic visibility path was untouched.

## Open Questions

- None introduced. Next Phase 1.5 capabilities (one capability = one packet type):
  - C2 — `0x0C` PT_Rename (Phase 6A)
  - C3 — `0x0D` PT_Hierarchy (Phase 6D)
  - C4 — `0x15` PT_ActiveCamera (camera domain)
  - C5 — `0x0E` PT_Delete_V5 (largest, needs dedicated investigation)
  - Then WAIT group: 0x05, 0x06, 0x08, 0x1B.
