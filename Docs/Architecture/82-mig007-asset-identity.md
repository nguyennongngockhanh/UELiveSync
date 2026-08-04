# MIG-007: OBJECT_ASSET_IDENTITY Semantic Path (0x08 PT_AssetDef)

## Status: COMPLETE (runtime PASS)

MIG-007 adds the `OBJECT_ASSET_IDENTITY` (0x26) semantic message path for asset
identity data, running alongside the legacy `PT_AssetDef` (0x08) wire packet in a
dual-emission configuration. Legacy is retained until the semantic path is proven and
a separate decommission ADR is written. Runtime acceptance: **PASS**.

## Summary

`PT_AssetDef` (0x08) serializes mesh-to-engine asset binding metadata (UUID, identity
hashes, primitive fallback, sequence, timestamp). MIG-007 creates a new semantic
`MsgType.OBJECT_ASSET_IDENTITY` (0x26) in `msg_transport.py` with a dedicated 45-byte
body (`build_object_asset_identity` in `object_protocol.py`), a per-sender
`_asset_identity_sequences` tracker for stale-rejection, and the full UE-side bridge →
sink → handler chain. The legacy `PT_AssetDef` path remains active in the same
if-block for backward compatibility; both paths fire identically on every asset event.
`HandleAssetDef` is the sole implementation — the bridge converts the semantic view
directly to the existing handler.

## Runtime Acceptance

```
Test boundary: 2026-08-04T10:58:10+07:00
UE PID: 97020 | Blender PID: 98842
Port 57000: LISTEN

Blender fresh evidence:
  [OBJ][MSGTYPE] OBJECT_ASSET_IDENTITY sent=2

UE fresh evidence:
  [BRIDGE][OBJECT_ASSET_IDENTITY] id=63e21a04-... identity_low=6828817566093408438 identity_high=0 primitive_fallback=0 seq=2 ts=1785815872.835
  [BRIDGE][OBJECT_ASSET_IDENTITY] id=95c9570d-... identity_low=4964002204185649927 identity_high=0 primitive_fallback=0 seq=2 ts=1785815872.835

Acceptance chain: OBJECT_ASSET sent → Bridge dispatch → OnObjectAssetIdentity → HandleAssetDef → ResolvePendingAssets → AssignStaticMesh → mesh visible ✓
User confirmation: mesh OK
Result: PASS
```

## What Was Built

### Blender addon (`Blender_Addon/`)

- `msg_transport.py`: `MsgType.OBJECT_ASSET_IDENTITY = 0x26` (wire type constant).
- `object_protocol.py`: `build_object_asset_identity(guid_obj, ...)` — 45-byte body
  (UUID16 16B + identity_low u64 8B + identity_high u64 8B + primitive_fallback u8
  1B + sequence_number u32 4B + timestamp f64 8B = 45B). `_asset_identity_sequences`
  per-sender sequence tracker. `clear_asset_identity_sequences()` for disconnect reset.
- `sync.py`: dual-emit block fires both `PT_AssetDef` legacy and
  `MsgType.OBJECT_ASSET_IDENTITY` semantic in the same if-block (sync.py:1955-1967),
  guaranteeing identical emission conditions. `is_first_send` gating included.

### Shared protocol (`Shared/`)

- `Protocol/MessageTypes.yaml`: 0x26 spec (45-byte body, field layout, dual-emit note).
- `Serializer/livesync_serializer.h`: `OBJECT_ASSET_IDENTITY = 0x26` enum entry.
- `Serializer/livesync_messages.h`: `serialize_body_object_asset_identity` function.
- `Serializer/livesync_deserializer.h`: body deserializer + dispatch case 0x26.

### UE plugin (`UE_Plugin/UELiveSync/Source/UELiveSync/`)

- `Public/LiveSyncViews.h`: `ObjectAssetIdentityView` struct.
- `Public/LiveSyncProtocolBridge.h`: `BuildObjectAssetIdentityView`,
  `LogObjectAssetIdentity`, `DispatchObjectAssetIdentity`, `ProcessObjectAssetIdentity`
  + `GetMessageTraits` 0x26 entry + dispatch switch case. Stat counters:
  `g_objectassetidentity_calls` / `g_objectassetidentity_bytes`.
- `Public/IGameplaySink.h`: `OnObjectAssetIdentity` virtual interface.
- `Public/SyncTypes.h`: `FAssetIdentitySequenceTracker` struct (after
  `FDeleteSequenceTracker`).
- `Public/UELiveSyncSubsystem.h` + `Private/UELiveSyncSubsystem.cpp`:
  - `GAssetIdentitySequences` global tracker (cpp:256).
  - Clear in `StopNetworkThread` / `ConsoleReset` block.
  - `OnObjectAssetIdentity` override: stale-reject via `GAssetIdentitySequences`,
    then delegates directly to `HandleAssetDef` (cpp:7690+).
  - `HandleAssetDef` (cpp:12231): asset metadata update → `ResolvePendingAssets` →
    `AssignStaticMesh` (cpp:12482) — the same handler used by legacy `PT_AssetDef`.

### Tests

- pytest: **58 passed**.
- `validate_protocol.py`: **10/10 PASS**.
- `cross_language_verify.py`: **33/33 PASS** (UUID_FIELDS updated for 0x26).
- `run_all_tests.sh`: **ALL 10 SUITES PASSED**.
- Vector files regenerated: `OBJECT_ASSET_IDENTITY.bin` + manifest + SHA256SUMS.
- C++ build: **SUCCEEDED** (14 actions, `libUnrealEditor-UELiveSync.so` artifacts).

## Wire-Order Verification

Blender serializes identity hashes as `low→high` (u64 LE each). UE deserializes into
`View.IdentityLow` and `View.IdentityHigh`, passing `(High, Low)` to
`HandleAssetDef(High, Low)`. GUID encoding uses `uuid_to_fguid_bytes` which decomposes
UUID16 into `(I, I, I, I)` little-endian, matching the legacy `PT_AssetDef` GUID field
exactly. Wire-order is verified correct before runtime launch.

## Design Decisions

- **D1** — `HandleAssetDef` is the sole implementation. The semantic bridge converts
  `ObjectAssetIdentityView` directly to the existing handler (no duplicate handler
  code). This was a user-approved contract point.
- **D2** — 45-byte body (not 44 or 46). The body contains exactly the 6 fields that
  `HandleAssetDef` reads: UUID16, identity_low, identity_high, primitive_fallback,
  sequence_number, timestamp. No padding, no reserved bytes.
- **D3** — Dedicated `_asset_identity_sequences` tracker (Blender) and
  `FAssetIdentitySequenceTracker` / `GAssetIdentitySequences` (UE). Separate from
  other trackers for independent stale-rejection and diagnostic clarity.
- **D4** — Dual-emission preserved in the same if-block. Both legacy and semantic paths
  fire under identical conditions, ensuring the legacy path remains a drop-in fallback
  until decommission.
- **D5** — OBJECT_ASSET_IDENTITY uses the general `OBJECT_ASSET` semantic family name
  (not `PT_ASSETDEF_IDENTITY`), consistent with the semantic naming convention.

## Invariants Preserved

- Legacy `PT_AssetDef` (0x08) path unchanged: `serialize_asset_identity`,
  `HandleAssetDef`, `kValidTypes` entry, dispatch block.
- `HandleAssetDef` is the single entry point for both paths — no handler duplication.
- FNV handshake, protocol signature, `kValidTypes`, `kValidFlags` all unchanged.
- `FAssetIdentitySequenceTracker` cleared on `StopNetworkThread` / `ConsoleReset`
  (consistent with `GDeleteSequences` / `GUpdateSequences` pattern).
- All existing tests pass unchanged (58 pytest + 10 validate + 33 cross-language).

## Next Steps

- Legacy `PT_AssetDef` decommission requires a separate ADR after:
  - Semantic path proven stable over multiple sessions.
  - Blender emitter switched to semantic-only.
  - `kValidTypes` / dispatcher dropped.
- MIG order: 007 Asset (done) → 008 Camera → 010 Material → 009 Mesh.
