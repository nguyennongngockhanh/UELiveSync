# Phase 1.5: Legacy PT_ActiveCamera (0x15) Decommission

## Status: COMPLETE

Implementation complete: the legacy `PT_ActiveCamera = 0x15` wire packet surface was
removed from both the Blender addon and the UE plugin, while the now-semantic
`FActiveCameraPayload` storage struct was retained. Build PASS, regression PASS. Runtime
acceptance not applicable (see Stage 5).

This is the eighth capability of Phase 1.5 (Legacy Protocol Elimination), following
ADR-72 (0x16), ADR-73 (0x03), ADR-74 (0x04), ADR-75 (0x02), ADR-76 (0x0B), ADR-77 (0x0C),
and ADR-78 (0x0D). Cadence: Investigation → Contract → Implementation → Build →
Regression → Runtime → ADR → Commit.

## Summary

Investigation initially assumed `FActiveCameraPayload` belonged to the legacy
`PT_ActiveCamera` packet (it was born as the 0x15 wire payload in Phase 7D). Production
inspection showed it is now the payload type used by the **semantic CAMERASETACTIVE
bridge** — `OnCameraSetActive` (`UELiveSyncSubsystem.cpp:7865`) builds an
`FActiveCameraPayload` and feeds `HandleActiveCamera` (`cpp:12997`). The struct is live
and is **retained**; only the legacy wire packet surface (enum, Blender constant, dead
serializer, stale wire-format spec) is removed. This is the first capability in the C
group that transitions from "remove packet" to "retain semantic storage".

## Proof of Dead Code (0x15 wire surface)

| Legacy | Replacement | Runtime proven | Production callers | Safe to remove |
|---|---|---|---|---|
| `PT_ActiveCamera` (0x15) wire packet | `CAMERASETACTIVE` (0x52) + `FActiveCameraPayload` storage | yes (semantic bridge) | 0 — dead serializer `serialize_active_camera` had **zero callers**; no emitter (Blender sends `MsgType.CAMERASETACTIVE`); no dispatcher (0x15 not in `ProcessBinaryPacket`); not in `kValidTypes` | YES |

## Problem

The legacy `PT_ActiveCamera` surface was fully dead on the wire, but its payload struct
survived as a live semantic type:

- UE enum `PT_ActiveCamera = 0x15` (`SyncTypes.h`) had no consumer — `0x15` is absent
  from `kValidTypes` and has no dispatch block; the sole UE text reference at
  `UELiveSyncSubsystem.cpp:3969` is a provenance comment ("PT_ActiveCamera (0x15) removed
  — routed via Bridge → CAMERASETACTIVE").
- Blender `serialize_active_camera()` (`network.py`) had **zero production callers** —
  no production path ever emits a `0x15` packet; active-camera selection travels as
  `MsgType.CAMERASETACTIVE` (`msg_transport.py:77`) → `build_camera_setactive`.
- The `FActiveCameraPayload` comment block (`SyncTypes.h`) documented a legacy 28-byte
  wire layout ("PT_ActiveCamera (0x15) fixed-size payload ... Wire format ...") for a
  packet that no longer exists on the wire, while the struct itself is fed by the
  semantic bridge.

## Stage 1 — Investigation

- Wire status: `0x15` is **not** in `kValidTypes`
  (`{ 0x01, 0x05, 0x06, 0x07, 0x08, 0x09, 0x0A, 0x0F, 0x11, 0x12, 0x13, 0x14, 0x17, 0x18,
  0x19, 0x1A, 0x1B }`, `UELiveSyncSubsystem.cpp:3371`) and has no `ProcessBinaryPacket`
  dispatch block (`rg "0x15"` over `UELiveSync/Source/UELiveSync/Private/` returns only
  the provenance comment at `cpp:3969`).
- `serialize_active_camera` (`network.py:1869`): **zero production callers** (only the
  definition, STATUS.md docs, and stale session files) — dead serializer removed.
  `NULL_CAMERA_GUID` (defined immediately above it) is live via `sync.py:3130` and kept.
- **Key finding — `FActiveCameraPayload` is LIVE semantic storage**: `OnCameraSetActive`
  (Bridge `IGameplaySink` override, fed from CAMERASETACTIVE) builds an
  `FActiveCameraPayload` (comment "New protocol does not carry Sequence or Timestamp",
  `cpp:7871`) and calls `HandleActiveCamera(Payload)` (`cpp:12997`), which stores
  `LastActiveCameraGUID/Sequence/Timestamp` and applies the viewport target when
  `UE.LiveSync.ActiveCamera.ApplyToViewport` is enabled. `EnsureCameraSequencerBinding`
  (`Subsystem.h:825`) is called from `HandleActiveCamera`. The struct, both 28-byte
  `static_assert`s, and the FNV `H = fnv(H, 28)` byte are retained unchanged.
- Live Blender semantic path: `sync.py:3132-3160` — the "Phase 1.5: legacy
  PT_ActiveCamera (0x15) packet emission removed" provenance comment sits above code that
  sends `build_camera_setactive` → CAMERASETACTIVE, plus the dual-emission of
  CAMERA_CREATE and PT_CameraDef (0x1B, WAIT group). `serialize_camera_def` /
  `PT_CameraDef` / `CAMERA_DEF_*` flags are the 0x1B surface (KEPT, WAIT group).
- Capability flag `CAP_SUPPORTS_ACTIVE_CAMERA_SYNC = 0x40` (Bit 6): **live wire
  negotiation** — present in `_local_capabilities` (`network.py:464`) and the UE local
  mask (`SyncTypes.h`); Bit 6 is set in the observed capability mask `0x000007F0`. The
  flag advertises active-camera sync, which is a **real semantic capability**
  (CAMERASETACTIVE), so the flag and both masks are KEPT; only the comments referencing
  the dead packet were trimmed. `supports_active_camera()` (`network.py:234`) mirrors the
  capability API family and is kept.
- FNV protocol signature: both sides contain `0x15`
  (`SyncTypes.h` signature block; `network.py:63` mirror list). Kept unchanged for
  handshake compatibility (ADR-72 D2).
- Historical provenance comments (KEPT): `UELiveSyncSubsystem.cpp:3969`,
  `sync.py:3132-3135`, `__init__.py:3036` ("PT_ActiveCamera is NOT sent (viewport
  switching unsafe)").
- `Tests/Protocol/Phase1.3_Protocol_Mapping.md` row 20 recorded
  `| 20 | PT_ActiveCamera | 0x15 | CAMERASETACTIVE | 0x52 | 1:1 | MAPPED |`. `0x52` is
  verified accurate (`CAMERASETACTIVE = 0x52`, `msg_transport.py:77`).
- Stale diagnostic tools (outside the addon/plugin, recorded in the backlog):
  `tools/uelivesync_7g_camera_def_client.py` and
  `tools/uelivesync_7g_camera_transform_client.py` define `PT_ACTIVE_CAMERA = 0x15`
  inline and emit the legacy packet. They are self-contained manual injection clients
  (they also still emit decommissioned 0x03/0x04), are not part of the regression suite,
  and were **not touched** in this capability (consistent with prior capabilities) —
  cleanup deferred to the stale-tools hygiene cycle.
- Stale-test residuals (outside the regression suite; recorded in the backlog):
  `tests/phase7d_stage1_active_camera_wire.py:78-79` (`network.PT_ActiveCamera` /
  `network.serialize_active_camera`), `tests/phase7d_stage2_camera_detection.py`,
  `tests/phase7d_stage3_ue_handler_validation.py`, `tests/phase7d_stage4_viewport_apply.py`,
  `tests/phase7h_material_policy_camera_ux.py`, `tests/phase7e_stage10b_pack_ue_fguid.py`
  (reference `serialize_active_camera`); `tests/phase7g_stage2_reserved_packet_guard.py`,
  `tests/e2e9_camera_sceneoutliner_safe_lifecycle.py` (reference `PT_ActiveCamera`).
  These never run: root `tests/` collection aborts at HEAD on `phase7c_stage2c`
  `sys.exit(1)`.

## Stage 2 — Contract

Approved contract, one packet = one capability:
- **REMOVE (4 items):**
  - `SyncTypes.h`: `PT_ActiveCamera = 0x15` enum entry + its "Phase 7D" comment block.
  - `network.py`: `PT_ActiveCamera = 0x15` constant.
  - `network.py`: `serialize_active_camera()` (dead — zero callers).
  - `Phase1.3_Protocol_Mapping.md`: row 20 `MAPPED` → `DECOMMISSIONED` (semantic
    CAMERASETACTIVE 0x52 only).
- **TRIM comments (4 items, behavior-preserving):**
  - `SyncTypes.h` `FActiveCameraPayload` banner: replace the legacy "PT_ActiveCamera
    (0x15) fixed-size payload / Wire format" spec with a statement that the struct is the
    storage payload for the semantic CAMERASETACTIVE bridge (user-suggested wording:
    "Not a legacy packet wire layout"). Struct, static_assert, and "See
    Docs/Architecture/53-phase7d-camera-sync-scope-lock.md" kept.
  - `SyncTypes.h` stat comment for `ActiveCameraPacketsReceived` (drop the dead packet
    name).
  - Cap-flag comments both sides (`SyncTypes.h`, `network.py`): "Bit 6: active camera
    sync supported (CAMERASETACTIVE)".
  - `sync.py:3147`: "Send CameraDef alongside active-camera selection" (drop the dead
    packet name from a live comment).
- **KEEP:** `FActiveCameraPayload` struct + both static_asserts; FNV `fnv(H, 0x15)` and
  `fnv(H, 28)` literals both sides; `CAP_SUPPORTS_ACTIVE_CAMERA_SYNC = 0x40` flag + both
  local masks + `supports_active_camera()`; `OnCameraSetActive` / `HandleActiveCamera` /
  `EnsureCameraSequencerBinding`; the whole semantic CAMERASETACTIVE path and the
  `serialize_camera_def` / `PT_CameraDef` (0x1B) surface; `NULL_CAMERA_GUID`; provenance
  comments; dead stat fields; stale tests and tools (backlog).
- **Runtime:** skipped by user decision — `serialize_active_camera()` is proven to have
  zero production callers, `0x15` has no `kValidTypes` entry and no dispatcher, and
  active-camera traffic runs exclusively through the semantic CAMERASETACTIVE bridge, so
  a runtime smoke would only confirm the bridge, not the removal. Build + regression are
  complete acceptance.

## Stage 3 — Implementation

Removed 4 items:
- `SyncTypes.h`: `PT_ActiveCamera = 0x15` enum line + comment block.
- `network.py`: `PT_ActiveCamera = 0x15` constant.
- `network.py`: `serialize_active_camera()` function (28-byte legacy serializer).
- `Phase1.3_Protocol_Mapping.md`: row 20 status → `DECOMMISSIONED`.

Trimmed 4 comments (no behavior change): `FActiveCameraPayload` banner → semantic storage
wording; `ActiveCameraPacketsReceived` stat comment; cap-flag comments both sides; `sync.py:3147`.

## Stage 4 — Build & Regression

- Build: `Build.sh ProjectTemplateEditor` — **SUCCEEDED** (23.77s, 14 actions, UBA local
  executor).
- `Tests/Protocol/tests/`: **56 passed**.
- `validate_protocol.py`: **10/10 PASS**.
- `cross_language_verify.py vectors/v1`: **32/32 PASS** (all three groups).
- Legacy `tests/` (root) suite: A/B stash comparison (run with C4 applied vs C4 reverted)
  shows **no change** in results — the only differing lines are the two mechanical
  dirty-tree audit flags (T9/T16 "No Blender addon files modified", which report the
  presence of uncommitted `network.py`/`sync.py` edits during development and fail for
  every in-progress capability). 0 regression.
- Acceptance: `rg "PT_ActiveCamera|serialize_active_camera"` across `UE_Plugin/` +
  `Blender_Addon/` returns only allowed residuals: provenance comments
  (`SyncTypes.h:493` trimmed banner, `SyncTypes.h` FNV comment, `cpp:3969`,
  `sync.py:3132-3133`, `__init__.py:3036`) and the `network.py:63` FNV mirror list.
  `0x15` remains only in the FNV signature literals on both sides.

## Stage 5 — Runtime Acceptance: Not Applicable

Legacy `PT_ActiveCamera` (0x15) has no production emitter (`serialize_active_camera` has
zero callers), no validator, no dispatcher, and no `kValidTypes` entry, and the capability
flag it once described is retained because it advertises the still-real semantic
capability. Active-camera synchronization is exercised exclusively through the semantic
CAMERASETACTIVE bridge, so build and regression constitute complete acceptance for this
capability.

## Design Decisions

- **D1** — Retain `FActiveCameraPayload` (struct + static_asserts + FNV `fnv(H, 28)`):
  it is no longer a legacy packet payload but the storage type of the semantic
  CAMERASETACTIVE bridge (`OnCameraSetActive` → `HandleActiveCamera`). Removing it would
  break the live semantic path. Only its wire-format comment was updated. Recorded as an
  architectural decision because investigation initially mis-scoped it as legacy.
- **D2** — Retain `CAP_SUPPORTS_ACTIVE_CAMERA_SYNC = 0x40` and both local capability
  masks: the bit participates in wire capability negotiation and still truthfully
  advertises active-camera sync (now via CAMERASETACTIVE). Removing it would change the
  negotiated mask (a wire change) and falsely advertise no support.
- **D3** — Remove the dead `serialize_active_camera()`: zero production callers, and its
  28-byte wire layout has no on-wire counterpart after the enum/constant removal.
- **D4** — Keep the FNV signature literals on both sides (`0x15`, `28`): handshake bytes
  (ADR-72 D2).
- **D5** — Trim stale packet-name references in live comments (`sync.py:3147`,
  cap-flag comments, stat comment) so readers do not infer the dead packet still exists,
  without touching the surrounding behavior.
- **D6** — Leave stale diagnostic tools (`tools/uelivesync_7g_camera_*_client.py`) and
  stale camera tests (`tests/phase7d_*`, `phase7g_stage2`, `phase7e_stage10b`,
  `phase7h_material_policy_camera_ux`, `e2e9_camera_sceneoutliner_safe_lifecycle`) in
  place: they are outside the addon/plugin production surface, do not run in the
  regression suite (root collection aborts at HEAD on `phase7c`), and are recorded in the
  backlog hygiene cycle, consistent with capabilities A–C3.

## Invariants Preserved

- Semantic CAMERASETACTIVE (0x52) path unchanged: detection, `build_camera_setactive`,
  bridge dispatch, `OnCameraSetActive`, `HandleActiveCamera`, sequencer binding.
- `FActiveCameraPayload` storage struct unchanged (28 bytes) — only its comment changed.
- Capability negotiation mask unchanged on both sides.
- FNV protocol signature unchanged (both sides).
- `kValidTypes` / `kValidFlags` unchanged (`0x15` was never in `kValidTypes`).
- PT_CameraDef (0x1B) / CAMERA_CREATE / CAMERA_UPDATE semantic surface untouched (WAIT group).
- No packet layout change; `0x15` had no wire presence to remove.
- No regression in the tracked suite (56+10+32 PASS; root `tests/` A/B identical apart
  from the mechanical dirty-tree audit flags).

## Rollback

Single `git revert` of this capability's commit. No wire-format change, no capability-mask
change, no compatibility risk: the removed surface was dead code, the FNV signature is
untouched, and the semantic CAMERASETACTIVE path was preserved.

## Open Questions

- None introduced. Next Phase 1.5 capabilities (one capability = one packet type):
  - C5 — `0x0E` PT_Delete_V5 (largest, needs dedicated investigation;
    `serialize_delete` / `_delete_sequences` surface)
  - Then WAIT group: 0x05, 0x06, 0x08, 0x1B.
