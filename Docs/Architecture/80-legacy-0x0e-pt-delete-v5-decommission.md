# Phase 1.5: Legacy PT_Delete_V5 (0x0E) Decommission

## Status: COMPLETE

Implementation complete: the legacy `PT_Delete_V5 = 0x0E` wire packet surface was
removed from both the Blender addon and the UE plugin, while the semantic
`FDeleteSequenceTracker` / `GDeleteSequences` storage was retained. Build PASS,
regression PASS. Runtime acceptance not applicable (see Stage 5).

This is the ninth capability of Phase 1.5 (Legacy Protocol Elimination), following
ADR-72 (0x16), ADR-73 (0x03), ADR-74 (0x04), ADR-75 (0x02), ADR-76 (0x0B), ADR-77 (0x0C),
ADR-78 (0x0D), and ADR-79 (0x15). Cadence: Investigation → Contract → Implementation →
Build → Regression → Runtime → ADR → Commit.

## Summary

`PT_Delete_V5` (0x0E) was the last legacy packet type with a live-looking wire surface:
it had a serializer (`serialize_delete`, 28-byte fixed payload) and per-GUID sequence
tracker (`_delete_sequences`) in the Blender addon, and an enum + layout spec in the UE
plugin. Investigation proved the entire legacy wire path is dead and the delete feature
runs exclusively through the semantic `OBJECT_DELETE` (0x22) pipeline: Blender never
emits a 0x0E packet (`serialize_delete` has zero production callers), and UE never
dispatches one (0x0E is absent from `kValidTypes` and `ProcessBinaryPacket`, and the
`CVarLiveSyncValidateProtocol` gate rejects it before dispatch). The semantic delete
storage — `FDeleteSequenceTracker` / `GDeleteSequences`, fed by `OnObjectDelete` →
`HandleDelete` from the OBJECT_DELETE bridge — is live and retained, matching the
C4 (0x15) "retain semantic storage" pattern.

## Proof of Dead Code (0x0E wire surface)

| Legacy | Replacement | Runtime proven | Production callers | Safe to remove |
|---|---|---|---|---|
| `PT_Delete_V5` (0x0E) wire packet | `OBJECT_DELETE` (0x22) + `FDeleteSequenceTracker` storage | yes (semantic bridge) | 0 — dead serializer `serialize_delete` had **zero callers**; no emitter (Blender sends `MsgType.OBJECT_DELETE`); no dispatcher (0x0E not in `ProcessBinaryPacket`); not in `kValidTypes` | YES |

## Problem

The legacy `PT_Delete_V5` surface was fully dead on the wire, but its sequence-tracker
struct survived as a live semantic type:

- UE enum `PT_Delete_V5 = 0x0E` (`SyncTypes.h`) had no consumer — `0x0E` is absent
  from `kValidTypes` (`{ 0x01, 0x05, 0x06, 0x07, 0x08, 0x09, 0x0A, 0x0F, 0x11, 0x12,
  0x13, 0x14, 0x17, 0x18, 0x19, 0x1A, 0x1B }`) and has no `ProcessBinaryPacket` dispatch
  block; the only 0x0E references are the world-replay internal tag (`cpp:11945`,
  `Replay.inl:903`) and provenance comments.
- Blender `serialize_delete()` (`network.py`) and its private tracker
  `_delete_sequences` had **zero production callers** — no production path emits a
  0x0E packet; deletion travels as `MsgType.OBJECT_DELETE` (0x22,
  `msg_transport.py:60`) → `build_object_delete` (`object_protocol.py:158`, the
  semantic 28-byte body: persistent_id + sequence + timestamp) → bridge
  `BuildObjectDeleteView` (`LiveSyncProtocolBridge.h:542`).
- The `FDeleteSequenceTracker` comment block (`SyncTypes.h`) referenced the dead packet
  name ("DELETE SEQUENCE TRACKER (Phase 6E, PT_Delete_V5 = 0x0E)") while the struct
  itself is the live storage of the semantic delete path.

## Stage 1 — Investigation

- Wire status: `0x0E` is **not** in `kValidTypes` and has no `ProcessBinaryPacket`
  dispatch block (the chain of `if` branches covers `{ 0x07, 0x11, 0x12, 0x09, 0x0A,
  0x08, 0x0F, 0x14, 0x13, 0x19, 0x1A, 0x1B, 0x18, 0x05, 0x06, 0x17, 0x01 }`).
  `CVarLiveSyncValidateProtocol` defaults to 1 (`cpp:690`), so a hypothetical 0x0E
  packet is rejected as "Invalid packet type" at the validate gate — **before**
  `TrackPerDomainPacket` (`cpp:3507`) — making the `case 0x0E` in
  `UELiveSyncSubsystem_Phase6I.inl:288` unreachable (removed, consistent with the
  removed 0x0B/0x0C/0x0D cases in earlier capabilities).
- `serialize_delete` (`network.py:2694`): **zero production callers** (only the
  definition) — dead serializer removed. Its private tracker `_delete_sequences`
  (`network.py:2692`) is **serializer-private state**: repo-wide `rg "_delete_sequences"`
  returns exactly (a) declaration, (b) reads/writes inside `serialize_delete` only,
  (c) the disconnect reset block (`network.py:3406-3409`); the reset block is dead too
  (it only clears state belonging to the removed serializer). The **semantic** tracker
  `_delete_sequences` lives in `object_protocol.py:125` (used by `build_object_delete`
  at :175-176, cleared by `clear_delete_sequences`, imported into `sync.py:20` from
  `object_protocol`) — that one is live and kept.
- **Key finding — `FDeleteSequenceTracker` / `GDeleteSequences` are LIVE semantic
  storage**: `OnObjectDelete` (the OBJECT_DELETE bridge sink, `cpp:7632`) is the sole
  caller of `HandleDelete` (`cpp:11800`), which does the three-barrier stale rejection
  via `GDeleteSequences` (`cpp:252`): sequence tracker (`IsStaleOrDuplicate`,
  `cpp:11813`), tombstone map, and ActorCache existence check; `GDeleteSequences.Update`
  runs at `cpp:11935`. It is cleared on `StopNetworkThread` / `ConsoleReset` (`cpp:2745`,
  `Diagnostics.inl:1497`). The struct and tracker are retained unchanged.
- Live Blender semantic path: `sync.py:1712/1755` send
  `(MsgType.OBJECT_DELETE, build_object_delete(guid_obj))` (the 28-byte body:
  persistent_id + sequence + timestamp — the same layout the legacy comment called
  "V5+ DELETE"). `BuildObjectDeleteView` (`LiveSyncProtocolBridge.h:542`) deserializes
  it on the UE side.
- World-replay residuals (KEPT, UE-internal, not wire): `cpp:11945`
  `WorldEntry.PacketType = 0x0E` records a delete event in the `EWorldReplayDomain::Lifecycle`
  domain (sibling tags 0x03 create, 0x0C rename); `Replay.inl:895-905` matches
  0x03/0x04/0x0E tags during playback to reconstruct created/removed state. These are
  replay-domain tags, not dispatched wire packets.
- `LiveSyncRunnable.cpp:596` `LIVE_SYNC_V3_DELETE_SIZE` (16) is the **generic**
  `PacketVersion` validation for V3 delete payloads — not a 0x0E dispatcher — kept.
- FNV protocol signature: both sides contain `0x0E`
  (`SyncTypes.h:2084` signature block; `network.py:63` mirror list) and the 28-byte
  delete body size (`fnv(H, 28)`, `SyncTypes.h:2061`). Kept unchanged for handshake
  compatibility (ADR-72 D2).
- Historical provenance comments (KEPT): `cpp:3597` ("PT_Rename, PT_Hierarchy,
  PT_Delete_V5 removed"), `Replay.inl:901`, `sync.py:2684`.
- `Tests/Protocol/Phase1.3_Protocol_Mapping.md` row 14 recorded
  `| 14 | PT_Delete_V5 | 0x0E | OBJECT_DELETE | 0x22 | 2:1 (with V3) | MAPPED |` — updated
  to `DECOMMISSIONED`.
- Stale-test residuals (outside the regression suite; recorded in the backlog):
  `tests/current_state_roadmap_audit.py`, `tests/phase7e_stage10a5a_reserved_packet_type_guard.py`,
  `tests/phase6b_runtime_audit.py`, `tests/phase10j_material_metadata_lifecycle.py`,
  `tests/phase6b_failure_injection.py`, `tests/phase6h_semantic_consistency.py`,
  `tests/phase7c_mesh_protocol_extraction.py`, `tests/phase7e_stage3_sequencer_op_wire.py`,
  `tests/phase7b_material_wire_handler.py`, `tests/phase7a_hygiene_validation.py`
  (reference `serialize_delete` / `PT_Delete_V5` / `0x0E`). These never run: root
  `tests/` collection aborts at HEAD on `phase7c_stage2c` `sys.exit(1)`.

## Stage 2 — Contract

Approved contract (user-approved with one adjustment — see Design Decisions), one
packet = one capability:
- **REMOVE (6 items):**
  - `SyncTypes.h`: `PT_Delete_V5 = 0x0E` enum entry + its "Phase 6E" comment block.
  - `network.py`: `PT_Delete_V5 = 0x0E` constant.
  - `network.py`: `serialize_delete()` (dead — zero callers) **and** its private
    tracker `_delete_sequences` (proven serializer-private state: declaration,
    serializer reads/writes, and the dead reset block are its only references).
  - `network.py`: the disconnect reset block for the removed `_delete_sequences`.
  - `UELiveSyncSubsystem_Phase6I.inl`: `case 0x0E` (unreachable — rejected at the
    validate gate before `TrackPerDomainPacket`).
  - `Phase1.3_Protocol_Mapping.md`: row 14 `MAPPED` → `DECOMMISSIONED`.
- **TRIM comments (4 items, behavior-preserving):**
  - `SyncTypes.h` `FDeleteSequenceTracker` banner: "DELETE SEQUENCE TRACKER (Phase 6E,
    PT_Delete_V5 = 0x0E)" → "DELETE SEQUENCE TRACKER (Phase 6E, semantic OBJECT_DELETE)".
    The three-barrier stale-rejection body below the banner is kept verbatim — it
    accurately describes the live semantic machinery.
  - `SyncTypes.h` stat comment `DeletePackets` → "Total legacy PT_Delete_V5 packets
    received" (dead field kept, packet name retained as provenance).
  - `SyncTypes.h` `EWorldReplayDomain::Lifecycle` → "OBJECT_DELETE (semantic replay)".
  - `SyncTypes.h` "V5+ DELETE (PT_Delete_V5) OBJECT LAYOUT" banner → "OBJECT_DELETE
    (0x22) BODY LAYOUT (28 bytes)" (the layout is the current semantic OBJECT_DELETE
    body spec; wire-format wording → semantic body layout wording).
- **KEEP:** `FDeleteSequenceTracker` struct + `GDeleteSequences` tracker;
  `OnObjectDelete` / `HandleDelete` / tombstone map / ActorCache check;
  `BuildObjectDeleteView` (`LiveSyncProtocolBridge.h:542`) and the whole semantic
  OBJECT_DELETE (0x22) path; `object_protocol._delete_sequences` /
  `clear_delete_sequences` / `build_object_delete`; world-replay 0x0E tag (`cpp:11945`)
  + `Replay.inl:895-905`; `LiveSyncRunnable.cpp:596` V3 size check; FNV `fnv(H, 0x0E)`
  and `fnv(H, 28)` literals both sides; provenance comments; live delete stats
  (`DeleteProcessed`, `DeleteReplayApplied`, `DeleteReplaySkipped`,
  `DeleteStaleRejections`, `DeleteTombstoneRejections`, `DeleteMissingActor`,
  `DeleteDeferredDuringSnapshot`, `CreateTombstoneRestored`) and the dead
  `DeletePackets` field; `Phase6IPerSecondDeletes` field + `DeletesPerSecond`
  diagnostics plumbing (now always 0 — kept per the dead-stat-field pattern); stale
  tests and tools (backlog).
- **Runtime:** skipped by user decision — `serialize_delete()` is proven to have zero
  production callers, 0x0E has no `kValidTypes` entry, no dispatcher, and the
  CVar validate gate (default on) rejects it before any handling, and delete traffic
  runs exclusively through the semantic OBJECT_DELETE bridge. Build + regression are
  complete acceptance.

## Stage 3 — Implementation

Removed 6 items:
- `SyncTypes.h`: `PT_Delete_V5 = 0x0E` enum line + comment block.
- `network.py`: `PT_Delete_V5 = 0x0E` constant.
- `network.py`: `serialize_delete()` + `_delete_sequences` (28-byte legacy serializer +
  its private tracker).
- `network.py`: disconnect reset block for `_delete_sequences`.
- `UELiveSyncSubsystem_Phase6I.inl`: `case 0x0E` (unreachable).
- `Phase1.3_Protocol_Mapping.md`: row 14 status → `DECOMMISSIONED`.

Trimmed 4 comments (no behavior change): `FDeleteSequenceTracker` banner →
semantic OBJECT_DELETE; `DeletePackets` stat comment; `Lifecycle` replay-domain
comment; OBJECT_DELETE body-layout banner.

## Stage 4 — Build & Regression

- Build: `Build.sh ProjectTemplateEditor` — **SUCCEEDED** (24.60s, 8 actions, UBA local
  executor).
- `Tests/Protocol/tests/`: **56 passed**.
- `validate_protocol.py`: **10/10 PASS**.
- `cross_language_verify.py vectors/v1`: **32/32 PASS** (all three groups).
- Legacy `tests/` (root) suite: A/B stash comparison (run with C5 applied vs C5 reverted)
  shows **no change** in results — the only differing lines are the two mechanical
  dirty-tree audit flags (T9/T16 "No Blender addon files modified", which report the
  presence of uncommitted `network.py` edits during development and fail for every
  in-progress capability). 0 regression.
- Acceptance: `rg "PT_Delete_V5|serialize_delete|_delete_sequences"` across
  `UE_Plugin/UELiveSync/Source/` + `Blender_Addon/` returns only allowed residuals:
  the `DeletePackets` provenance stat comment, the `network.py:63` FNV mirror list,
  world-replay/provenance comments (`cpp:3597`, `cpp:11945`, `Replay.inl:901`), the
  `fnv(H, 0x0E)` handshake literal, and the **semantic** `_delete_sequences` in
  `object_protocol.py` (live).

## Stage 5 — Runtime Acceptance: Not Applicable

Legacy `PT_Delete_V5` (0x0E) has no production emitter (`serialize_delete` has zero
callers), no validator entry (`kValidTypes`), no dispatcher, and the
`CVarLiveSyncValidateProtocol` gate (default on) rejects any 0x0E byte before
`TrackPerDomainPacket`. Delete synchronization is exercised exclusively through the
semantic OBJECT_DELETE (0x22) bridge, so build and regression constitute complete
acceptance for this capability.

## Design Decisions

- **D1** — Retain `FDeleteSequenceTracker` / `GDeleteSequences`: they are the live
  storage of the semantic OBJECT_DELETE path (`OnObjectDelete` → `HandleDelete` →
  three-barrier stale rejection). Removing them would break live delete replication.
  Only the packet-name banner comment was updated. Recorded as an architectural
  decision because investigation initially mis-scoped the surface as legacy.
- **D2** — Remove `serialize_delete()` AND `_delete_sequences` (`network.py`): the
  tracker was proven serializer-private state — repo-wide `rg "_delete_sequences"`
  shows declaration, reads/writes inside `serialize_delete`, and the dead reset block
  as its only references (distinct from the live semantic tracker of the same name in
  `object_protocol.py`). User-approved adjustment to the contract: removal conditioned
  on this repo-wide proof.
- **D3** — Keep the FNV signature literals on both sides (`0x0E`, `28`): handshake
  bytes (ADR-72 D2).
- **D4** — Trim stale packet-name references in live comments (tracker banner, stat
  comment, replay-domain comment, body-layout banner) so readers do not infer the dead
  packet still exists, without touching the surrounding behavior.
- **D5** — Keep `Phase6IPerSecondDeletes` + `DeletesPerSecond` diagnostics plumbing
  (now always 0) and the dead `DeletePackets` stat field: consistent with the dead
  stat-field retention pattern from earlier capabilities.
- **D6** — Leave stale delete tests (`tests/phase6b_*`, `tests/phase6h_*`,
  `tests/phase7a_*`, `tests/phase7b_*`, `tests/phase7c_*`, `tests/phase7e_stage3_*`,
  `tests/phase10j_*`, `tests/current_state_roadmap_audit.py`) in place: they are
  outside the addon/plugin production surface, do not run in the regression suite
  (root collection aborts at HEAD on `phase7c`), and are recorded in the backlog
  hygiene cycle, consistent with capabilities A–C4.

## Invariants Preserved

- Semantic OBJECT_DELETE (0x22) path unchanged: detection, `build_object_delete`,
  bridge dispatch, `OnObjectDelete`, `HandleDelete`, `FDeleteSequenceTracker`
  stale-rejection, tombstone map, ActorCache check.
- `FDeleteSequenceTracker` storage unchanged — only its banner comment changed.
- World-replay Lifecycle domain and 0x0E/0x03/0x04 replay tags unchanged (UE-internal).
- `LiveSyncRunnable.cpp` V3 16-byte delete validation unchanged.
- FNV protocol signature unchanged (both sides).
- `kValidTypes` / `kValidFlags` unchanged (`0x0E` was never in `kValidTypes`).
- No packet layout change; `0x0E` had no wire presence to remove.
- No regression in the tracked suite (56+10+32 PASS; root `tests/` A/B identical apart
  from the mechanical dirty-tree audit flags).

## Rollback

Single `git revert` of this capability's commit. No wire-format change, no capability-mask
change, no compatibility risk: the removed surface was dead code, the FNV signature is
untouched, and the semantic OBJECT_DELETE path was preserved.

## Open Questions

- None introduced. Next Phase 1.5 capabilities (one capability = one packet type):
  - WAIT group: 0x05 (PT_Material), 0x06 (PT_Mesh), 0x08 (PT_AssetDef), 0x1B
    (PT_CameraDef).
