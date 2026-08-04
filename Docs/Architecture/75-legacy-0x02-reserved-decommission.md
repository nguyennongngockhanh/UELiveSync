# Phase 1.5: Legacy PT_Reserved_02 (0x02) Decommission

## Status: COMPLETE

Implementation complete: the legacy `PT_Reserved_02 = 0x02` enum entry (a Phase 3-era
placeholder that never carried a packet) was removed from `SyncTypes.h`. Build PASS,
regression PASS. Runtime acceptance not applicable (see Stage 5).

This is the fourth capability of Phase 1.5 (Legacy Protocol Elimination), following
ADR-72 (0x16), ADR-73 (0x03), and ADR-74 (0x04). Cadence: Investigation → Contract →
Implementation → Build → Regression → Runtime → ADR → Commit.

## Summary

Phase 1.5 inventory classified `0x02` as REMOVE (dead code). Unlike the other
decommissioned legacy types, `0x02` has no semantic replacement because it never was a
real protocol packet: `PT_Reserved_02` was a reserved placeholder (originally
`PT_Hierarchy` in early Phase 3, later superseded by the semantic `PT_Hierarchy` at
`0x0D`). It had no Blender emitter, no UE dispatcher, and no `kValidTypes` entry.

## Proof of Dead Code (0x02)

| Legacy | Replacement | Runtime proven | Production callers | Safe to remove |
|---|---|---|---|---|
| `PT_Reserved_02` (0x02), enum-only placeholder | — (none; 0x02 never carried a packet) | — | 0 — no emitter, no dispatcher, not in `kValidTypes`, not in FNV signature | YES |

## Problem

The `EPacketType` enum in `SyncTypes.h` still declared `PT_Reserved_02 = 0x02` with a
historical comment, but `0x02` was never used on the wire: it is absent from
`kValidTypes`, has no `ProcessBinaryPacket` dispatch, and no Blender code emits
`packet_type=0x02`. It was documentation-only dead weight. Phase 1.5 eliminates the
remaining legacy protocol surface.

## Stage 1 — Investigation

- Wire status: `0x02` is **not** in `kValidTypes` (`UELiveSyncSubsystem.cpp:3371`) and has
  no dispatch block in `ProcessBinaryPacket`. Blender: no `PT_Reserved_02` constant, no
  `packet_type=0x02` emission.
- The only production reference was the enum entry `SyncTypes.h:208` with its comment
  `Legacy — was PT_Hierarchy in early Phase 3; unused`. The semantic `PT_Hierarchy`
  (`0x0D`) is a different, active packet and is untouched.
- FNV protocol signature: verified **no `0x02`** entry → removing the enum cannot affect
  the handshake hash.
- Namespace separation: the bare value `0x02` also appears as `PF_FullSnapshot`,
  `ReplayValidate`, `COLLECTION_OP_REMOVE`, and in `kValidFlags` — all distinct
  flag/namespace values, unrelated to the packet-type enum. None are touched.
- `Tests/Protocol/Phase1.3_Protocol_Mapping.md` row 2 already records
  `| 2 | PT_Reserved_02 | 0x02 | -- | -- | None | DEAD CODE |` — the mapping document is
  the living protocol spec and this entry remains accurate as a spec reference.
- Root legacy test `tests/phase7e_stage10a5a_reserved_packet_type_guard.py` references
  literal `0x02` (asserts it is not in `kValidTypes`) — already stale at HEAD
  (`test_0x03_is_valid_create` fails since 0x03 was decommissioned in capability A);
  outside the regression suite, recorded in the backlog.

## Stage 2 — Contract

Approved contract, one packet = one capability:
- **Will change (1 file + ADR):** `SyncTypes.h` (remove `PT_Reserved_02 = 0x02` enum
  entry and its inline comment).
- **Will NOT change:** `kValidFlags` (contains `0x02` as a *flag* value), `PF_FullSnapshot`
  (`0x02`, flags namespace), `ReplayValidate` (`0x02`, replay namespace),
  `COLLECTION_OP_REMOVE` (`0x02`, collection namespace), FNV signature (no 0x02 present),
  mapping doc row 2 (spec), guard test (stale backlog).
- **Acceptance:** no `PT_Reserved_02` enum in production code; build PASS; regression
  PASS; no wire-protocol change.

## Stage 3 — Implementation

`-1` line in one file:
- `SyncTypes.h`: removed `PT_Reserved_02 = 0x02,  // Legacy — was PT_Hierarchy in early
  Phase 3; unused`. The `EPacketType` enum now reads `PT_Transform = 0x01,` then
  `PT_Material = 0x05,` (0x03 and 0x04 were already removed by capabilities A and B).

## Stage 4 — Build & Regression

- Build: `Build.sh ProjectTemplateEditor` — **SUCCEEDED** (23.76s).
- `Tests/Protocol/tests/`: **56 passed**.
- `validate_protocol.py`: **10/10 PASS**.
- `cross_language_verify.py vectors/v1`: **32/32 PASS** (all three groups).
- Legacy `tests/` (root) suite: collection failure signature **byte-identical** at HEAD vs
  working tree (stash-verified): 0 regression.
- Acceptance: `rg PT_Reserved_02` in UE source returns nothing; residual refs are the
  mapping doc row and git history only.

## Stage 5 — Runtime Acceptance: Not Applicable

`PT_Reserved_02` (0x02) has no production emitter, validator, or dispatcher, and has
never participated in the runtime wire protocol. Therefore there is no executable runtime
scenario specific to this capability; build and regression constitute complete acceptance.

## Design Decisions

- **D1** — Remove the enum entry and its inline comment together: the comment only
  described the removed placeholder (no separate provenance to preserve; the mapping doc
  row 2 retains the historical classification).
- **D2** — Leave `kValidFlags` and the other `0x02` namespaces untouched: they are
  distinct flag/namespace values, not packet types; removing them would be out of scope.
- **D3** — Leave the mapping doc row 2 as-is: it already correctly records `DEAD CODE`
  and is the living protocol specification (allowed residual).

## Invariants Preserved

- No packet layout change; `0x02` never had one.
- FNV protocol signature unchanged (no 0x02 entry to remove).
- `kValidTypes` / `kValidFlags` unchanged.
- No regression in the tracked suite (0 new failures; 56+10+32 PASS; root `tests/`
  signature identical).

## Rollback

Single `git revert` of this capability's commit. No wire-format change, no compatibility
risk: the removed enum was never used by any code path.

## Open Questions

- None introduced. Next Phase 1.5 capabilities (splitting the 0x0B–0x0E + 0x15 group into
  one capability each per decision):
  - C1 — `0x0B` PT_Visibility (Phase 6B history)
  - C2 — `0x0C` PT_Rename (Phase 6A)
  - C3 — `0x0D` PT_Hierarchy (Phase 6D)
  - C4 — `0x15` PT_ActiveCamera (camera domain)
  - C5 — `0x0E` PT_Delete_V5 (largest, needs dedicated investigation)
  - Then WAIT group: 0x05, 0x06, 0x08, 0x1B.
