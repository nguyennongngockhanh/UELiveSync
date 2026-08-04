# Phase 1.5: Legacy PT_Create (0x03) Decommission

## Status: COMPLETE

Implementation complete: the legacy `PT_Create = 0x03` surface was removed — the dead
accumulation lists and `rebind_all()` emitter in the Blender addon (plus its UI operator
and panel button), the `PT_Create` enum entry in the UE plugin, and the unreachable
`case 0x03` stats handler. The semantic `OBJECT_CREATE` (0x20) message (MIG-002) is now
the only object-create path. Build PASS, regression PASS. Runtime smoke intentionally
omitted (see Stage 5).

This is the second capability of Phase 1.5 (Legacy Protocol Elimination), following
ADR-72 (0x16). Cadence: Investigation → Contract → Implementation → Build → Regression →
Runtime → ADR → Commit.

## Summary

Phase 1.5 inventory classified `0x03` PT_Create as REMOVE. Unlike 0x16 (which had a
semantic replacement that was runtime-proven), `PT_Create` was already **unreachable on
the wire**: UE has no dispatch for `PacketType == 0x03` (falls through to "Unknown packet
type"), and `0x03` is not in `kValidTypes`. The production surface was dead code whose
only live emitter (`rebind_all()`) produced packets UE drops.

## Proof of Semantic Replacement (0x03 → 0x20)

| Legacy | Replacement | Runtime proven | Production callers | Safe to remove |
|---|---|---|---|---|
| `PT_Create` (0x03), dead emitter `rebind_all()` | `OBJECT_CREATE` (0x20) via `object_create_msgs_to_send` (MIG-002) | MIG-002 Phase 5 (6/6), ADR-68 | 0 — UE never dispatches 0x03; no production emitter after removing `rebind_all()` | YES |

## Problem

MIG-002 migrated object creation to the semantic `OBJECT_CREATE` (0x20) message, but the
legacy 0x03 surface survived as dead weight: Blender accumulated `create_objects` /
`children_create` lists that were appended but never consumed, and exposed a broken
"Rebind All" operator whose 0x03 emission UE silently dropped. UE kept an unused `PT_Create`
enum entry and an unreachable `case 0x03` stats counter. Phase 1.5 eliminates the remaining
legacy protocol surface once the semantic replacement is proven.

## Stage 1 — Investigation

- Wire status: `0x03` is **not** in `kValidTypes` (`UELiveSyncSubsystem.cpp:3371`) and has
  **no** `PacketType == 0x03` dispatch block in `ProcessBinaryPacket` → any 0x03 packet
  falls to "Unknown packet type" (`UELiveSyncSubsystem.cpp:4890`). Only `0x01` reaches the
  object loop.
- Blender addon: no `PT_Create` constant. Dead accumulation: `create_objects` /
  `children_create` (`sync.py`) appended in the first-send branches but never consumed.
- **`rebind_all()`** (`sync.py`) was the only live 0x03 emitter (`packet_type=0x03`). It had
  a UI button "Rebind All" (`__init__.py` panel, icon `UV_SYNC_SELECT`) and registered
  operator `UELIVESYNC_OT_rebind_all`. Currently broken: UE drops its 0x03 creates → a
  no-op / UX lie.
- UE surface: enum `PT_Create = 0x03` (`SyncTypes.h`) + `Lifecycle` comment; unreachable
  `case 0x03` stats (`Phase6I.inl`); `Phase6IPerSecondCreates` counter (kept, reads 0);
  historical comments (`UELiveSyncSubsystem.cpp:9157`, `:13197`, `:13205` — left in place
  per contract).
- **World-replay markers literal `0x03`** (`UELiveSyncSubsystem.cpp:9682`,
  `Replay.inl:897`, `Phase6H.inl:447`): UE-internal replay domain markers, **not** wire
  packets → KEEP.
- `HandleCreateObject` (`SyncTypes.h` / `.cpp`) is shared with semantic `OBJECT_CREATE`
  (0x20) → KEEP.
- Regression-safe: `Tests/Protocol` mapping doc row 3 is the only ref there; root `tests/`
  define their own literal `PT_Create = 0x03` (no import of addon symbols) → no breakage.

## Stage 2 — Contract

Approved contract (user-adjusted), one packet = one capability:
- **Will change (5 files):** `Blender_Addon/sync.py` (lists + dead appends + `rebind_all()`
  + unused `PT_BeginSnapshot`/`PT_EndSnapshot` imports), `Blender_Addon/__init__.py`
  (operator class + panel button + classes-tuple entry), `SyncTypes.h` (enum + comment),
  `UELiveSyncSubsystem_Phase6I.inl` (unreachable `case 0x03`),
  `Tests/Protocol/Phase1.3_Protocol_Mapping.md` (row 3).
- **Will NOT change:** `OBJECT_CREATE` (0x20) path, `HandleCreateObject`, snapshot/reconnect
  logic, world-replay `0x03` markers, `kValidTypes`/`kValidFlags`,
  `Phase6IPerSecondCreates` counter field (kept, reads 0), historical comments not adjacent
  to removed code (`UELiveSyncSubsystem.cpp:9157`, `:13197`, `:13205`,
  `Replay.inl:896`, `__init__.py:3025`, `:3052`).
- **Acceptance (user-revised):** no production **emitter, dispatcher, or enum** of
  `PT_Create` (0x03) remains. Residual refs allowed: mapping doc row, historical provenance
  comments, world-replay markers, stale-test refs recorded in the backlog.

## Stage 3 — Implementation

`+4/-177` lines across 5 files:
- `sync.py`: removed `create_objects` / `children_create` declarations, the two dead
  `.append(serialized)` calls in the first-send branches (comments trimmed to the semantic
  `OBJECT_CREATE` message), the entire `rebind_all()` function, and the now-unused
  `PT_BeginSnapshot` / `PT_EndSnapshot` imports (only consumer was `rebind_all()`).
- `__init__.py`: removed `UELIVESYNC_OT_rebind_all` operator class, its panel button, and
  its classes-tuple entry (register/unregister loop handles the rest unchanged).
- `SyncTypes.h`: removed enum `PT_Create = 0x03`; trimmed `Lifecycle` comment to
  `PT_Delete, PT_Delete_V5`.
- `UELiveSyncSubsystem_Phase6I.inl`: removed unreachable `case 0x03` stats block.
- `Phase1.3_Protocol_Mapping.md`: row 3 → `MAPPED (Phase 1.5: legacy 0x03 DECOMMISSIONED;
  semantic OBJECT_CREATE only)`.

## Stage 4 — Build & Regression

- Build: `Build.sh ProjectTemplateEditor` — **SUCCEEDED** (14 actions, plugin linked, 32.80s).
- `Tests/Protocol/tests/`: **56 passed**.
- `validate_protocol.py`: **10/10 PASS**.
- `cross_language_verify.py vectors/v1`: **32/32 PASS** (all three groups).
- Legacy `tests/` (root) suite: collection failure signature **byte-identical** at HEAD vs
  working tree (stash-verified): 0 regression. No root test imports the removed symbols
  (`rebind_all` / `create_objects` / `children_create`); the `create_objects` identifier in
  `tests/phase6b_soak_test.py` is a local test variable, not an addon reference.
- Acceptance: `rg` confirms no production emitter/dispatcher/enum of `PT_Create` remains;
  residual `PT_Create`/`0x03` references are mapping-doc, historical comments, or
  world-replay markers only.

## Stage 5 — Runtime Acceptance (intentionally omitted)

Runtime smoke was not run for this capability.

Rationale: this capability removes unreachable legacy production surface only and
introduces no runtime behavior change. Build and regression suites provide sufficient
evidence — the removed paths were proven dead by code inspection (no `kValidTypes` entry,
no `ProcessBinaryPacket` dispatch for `0x03`, dead accumulation never consumed), and
`OBJECT_CREATE` (0x20) spawn behavior is already covered by MIG-002 Phase 5 runtime
acceptance and the current regression suites.

## Design Decisions

- **D1** — Remove the whole legacy surface in one capability (lists + emitter + operator +
  UI + enum + stats case), not incrementally. The semantic replacement is proven
  (MIG-002/ADR-68); there is no live 0x03 consumer left.
- **D2** — Delete `rebind_all()` outright rather than migrate it to `OBJECT_CREATE`: it is a
  broken feature (UE drops its creates) with no real UI value, and snapshot emission via
  semantic messages is planned separately as a future MIG ("Snapshot Semantic Reconnect").
  The name/logic will not be resurrected under a different packet type.
- **D3** — Keep the world-replay literal `0x03` markers (`UELiveSyncSubsystem.cpp:9682`,
  `Replay.inl:897`, `Phase6H.inl:447`): UE-internal replay domains, functionally unrelated
  to the legacy wire packet.
- **D4** — Keep `Phase6IPerSecondCreates` counter field and its reporting: harmless,
  reads 0, and removal would touch unrelated stats plumbing outside this capability's scope.
- **D5** — `Tests/Protocol/Phase1.3_Protocol_Mapping.md` keeps the legacy name in the
  mapping row (living protocol mapping document); status field records DECOMMISSIONED,
  following the row-21 (0x16) precedent.

## Invariants Preserved

- Wire format of `OBJECT_CREATE` (0x20) unchanged; no packet layout change.
- `HandleCreateObject` shared path unchanged.
- Snapshot / reconnect logic unchanged.
- `kValidTypes` / `kValidFlags` unchanged.
- No regression in the tracked suite (0 new failures; 56+10+32 PASS; root `tests/`
  signature identical).

## Rollback

Single `git revert` of this capability's commit. No wire-format change, no compatibility
risk: the removed symbols were never on a working production path (UE dropped 0x03).

## Open Questions

- None introduced. Future related work (deferred, not blocking):
  - "Snapshot Semantic Reconnect" MIG — emit snapshot via semantic messages (replaces the
    `rebind_all` concept properly).
  - Next Phase 1.5 capability: `0x04` PT_Delete.
