# MIG-005: FBX Import Semantic Migration

## Status: COMPLETE

## Summary

Migrated the production FBX mesh import path from the legacy `PT_FBXImportRequest` (0x16) packet to the semantic protocol message `FBX_IMPORT_REQUEST` (0x60). The semantic message represents the **same import capability** as the legacy packet — no new options, no behavioral changes to the importer — and the legacy 0x16 production path was removed in the same migration. `PT_FBXImportRequest` survives only as migration history in the enum and comments.

Baseline: `phase1.4-post-mig004b` (`7418257`). Target tag: `phase1.4-post-mig005`.

## Problem

The FBX import handoff (Blender exports mesh to FBX cache → UE imports a StaticMesh from that file) was one of the last production paths still running on the legacy packet protocol. It bypassed the semantic pipeline established by MIG-001..004 (`sequence_number`/`timestamp` trailing fields, View → Payload → handler dispatch via `IGameplaySink`), leaving two divergent ways to move data from Blender to UE.

## Solution

Four commits, in the fixed MIG-003/004 order: protocol → UE gameplay → Blender → docs.

### 1. Protocol (`cba7def`)

- `MessageTypes.yaml`: `FBX_IMPORT_REQUEST = 0x60` (B→U, session_required), 10 fields, parity with legacy 0x16:
  `persistent_id` (uuid), `version` (u32), `fbx_path` (utf8_string), `object_name` (utf8_string), `vert_count` (u32), `tri_count` (u32), `mat_slot_count` (u32), `geometry_hash` (u64), `sequence_number` (u32), `timestamp` (f64). Trailing seq/ts pattern from MIG-003.
- `livesync_serializer.h`/`livesync_messages.h`/`livesync_deserializer.h`: enum 0x60 + `serialize_body_fbx_import_request` + deserialize case for all 10 fields.
- Test vectors: `FBX_IMPORT_REQUEST.bin` (150B) + `cpp_serialized/FBX_IMPORT_REQUEST.bin`; manifest `vector_count=32`; SHA256SUMS/cpp_deserialized.json regenerated; `generate_vectors.py` FBX vector; `protocol.py` enum 0x60.
- Test infra fixes: `cross_language_verify.py` UUID_FIELDS + FBX `persistent_id`; `validate_protocol.py` added `"float64"` to PRIMITIVE_TYPES (pre-existing gap, FIXED_SIZE unchanged); `test_serialization.py` FBX defaults; `test_cross_language.cpp` FBX case; `run_all_tests.sh` reads `vector_count` from manifest instead of a hardcoded count.

### 2. UE Gameplay (`4a373ea`)

- `LiveSyncViews.h`: `FbxImportRequestView` (PersistentId, Version, FbxPath, ObjectName, VertCount, TriCount, MatSlotCount, GeometryHash, SequenceNumber, Timestamp).
- `IGameplaySink.h`: `OnFbxImportRequest(const FbxImportRequestView&) {}` next to the OnMesh* sink methods.
- `LiveSyncProtocolBridge.h`: traits case (merged with CAMERA/SYNC_ACK), `BuildFbxImportRequestView`, `LogFbxImportRequest` (uses `FormatUuid` → %hs), `DispatchFbxImportRequest`, `ProcessFbxImportRequest`, switch-case, `g_fbximportrequest_calls` counter + reset.
- `UELiveSyncSubsystem.cpp`: `OnFbxImportRequest` handler after `OnMaterialAssign` — stale-rejection via `static TMap<FGuid, uint32> GFbxImportSequences` (mirrors `GMaterialCreateSequences`), builds `FFBXImportRequestPayload` from the View (persistent_id → ObjectGUID, strings truncated into `FbxPath[512]`/`ObjectName[128]`), then calls `FLiveSyncFBXImporter::HandleImport(Payload, Ctx)`. Context passthrough verbatim: FBXPendingGuids / OnMarkFbxAuthority / OnScheduleRepair / OnRestoreGeneratedMaterials / OnSidecarTextureImported. **Legacy 0x16 block removed** (~168 lines); 0x16 dropped from `kValidTypes`.
- `LiveSyncFBXImporter.h/.cpp`: `HandleImport(FFBXImportRequestPayload, FFBXImportContext)`; removed dead `ValidatePayloadSize` and old `FMemory::Memzero/Memcpy`.
- `SyncTypes.h`: removed `fnv(H, 680)` + `fnv(H, 0x16)` from `LIVE_SYNC_PROTOCOL_SIG`; counters comment → total FBX_IMPORT_REQUEST packets received. Enum `PT_FBXImportRequest = 0x16` kept as migration history (MIG-001..004 pattern); remaining references are comments only.
- `test_bridge_dispatch.cpp`: FakeGameplaySink `FbxImportRequestCalls`/`LastFbxImportRequest` + override; Test 43 (handled + builder, 8 field asserts against the golden vector) + Test 44 (sink receive). 87/87 PASS.

### 3. Blender (`fbe8787`)

- `msg_transport.py`: `FBX_IMPORT_REQUEST = 0x60` opcode.
- `fbx_protocol.py` (new): `build_fbx_import_request(...)` producing the exact wire body (uuid 16B, u32/u64 LE, `pack_utf8`, `pack_f64`), `_next_fbx_import_sequence(persistent_id)` per-object counter, `clear_fbx_sequences()`.
- `__init__.py`: `UELIVESYNC_OT_sync_selected_mesh_to_ue_fbx` now frames `FBX_IMPORT_REQUEST` via `MsgTransport.send_msg(MsgType.FBX_IMPORT_REQUEST, body)` under the same manifest-durability gate; the operator result preserves the `suppressed` / `serialization_failed` / `send_failed` / `sent` contract.
- `sync.py`: `clear_fbx_sequences()` on reconnect alongside `clear_material_sequences()`.
- Legacy `network.serialize_fbx_import_request` and `manifest_v3.serialize_and_send_fbx_request` retained but dead (referenced by `_measure_9b5.py`).

**Byte-parity verified**: the addon `fbx_protocol.build_fbx_import_request` output is byte-identical to the reference test serializer (`serialize_body`) for the golden 150B FBX_IMPORT_REQUEST vector (UUID_A, `/home/user/.cache/uelivesync/fbx/00112233445566778899aabbccddeeff.fbx`, "Cabinet", 846/1528/2, geometry_hash 0x123456789ABCDEF0, seq 44, ts 1700000044.0).

### 4. Docs

- This ADR (`Docs/Architecture/70-mig-005-fbx-import-semantic-migration.md`).
- `Tests/Protocol/Phase1.3_Protocol_Mapping.md`: `PT_FBXImportRequest` row MAPPED → `FBX_IMPORT_REQUEST` 0x60; removed from the missing-types table; summary counts updated (15 mapped / 9 missing).

## Files Changed

**Protocol:**
- `MessageTypes.yaml`
- `UE_Plugin/.../LiveSyncProtocol/livesync_serializer.h`, `livesync_messages.h`, `livesync_deserializer.h`
- `Tests/Protocol/serializer/protocol.py`, `generate_vectors.py`, `tests/test_serialization.py`
- `Tests/Protocol/vectors/v1/FBX_IMPORT_REQUEST.bin`, `cpp_serialized/FBX_IMPORT_REQUEST.bin`, manifest/SHA256SUMS/cpp_deserialized.json
- `Tests/Protocol/cross_language_verify.py`, `validate_protocol.py`, `test_cross_language.cpp`, `run_all_tests.sh`

**UE Plugin:**
- `Public/LiveSyncViews.h`, `Public/IGameplaySink.h`, `Public/LiveSyncProtocolBridge.h`
- `Public/UELiveSyncSubsystem.h`, `Private/UELiveSyncSubsystem.cpp`
- `Public/LiveSyncFBXImporter.h`, `Private/LiveSyncFBXImporter.cpp`
- `Public/SyncTypes.h`
- `tests/test_bridge_dispatch.cpp`

**Blender Addon:**
- `fbx_protocol.py` (new), `msg_transport.py`, `__init__.py`, `sync.py`

**Docs:**
- `Docs/Architecture/70-mig-005-fbx-import-semantic-migration.md` — this ADR
- `Tests/Protocol/Phase1.3_Protocol_Mapping.md`

## Design Decisions

- **D1** — Opcode slot: `FBX_IMPORT_REQUEST = 0x60`, an empty slot verified by grep across the repo. No collision with existing semantic opcodes.
- **D2** — Importer is a black box: only the entry path (View → Payload → `HandleImport`) was refactored; the importer body and its authority/repair/sidecar callbacks are untouched.
- **D3** — Legacy 0x16 production path is removed in this same migration. 0x16 survives only as migration history in the enum and comments, matching the MIG-001..004 pattern.

## Invariants Preserved

- `FBX_IMPORT_REQUEST` expresses the same capability as legacy 0x16 — no new import options.
- Acceptance G: binary behavior parity — same ObjectGUID, importer path, StaticMeshActor lifecycle, authority bookkeeping.
- UE still uses file mtime; the payload `timestamp` is not read by the runtime (trailing seq/ts pattern per MIG-003).
- Legacy 0x16 references remaining in source are comments only.

## Regression Test Matrix

| Scenario | Expected Behavior |
|----------|-------------------|
| FBX_IMPORT_REQUEST stale packet (seq <= current) | Rejected by `GFbxImportSequences` before `HandleImport` |
| Valid FBX_IMPORT_REQUEST | View built with all 10 fields; `HandleImport` called with matching Payload |
| Sink contract | FakeGameplaySink receives the FbxImportRequestView |
| Cross-language | Python/C++ serialize + C++ deserialize agree on the 150B vector |
| Addon emission | `fbx_protocol` body byte-identical to the reference serializer |
| Legacy 0x16 packet | No longer a valid wire type; dropped from `kValidTypes` |

## Verification Summary

| Phase | Result |
|-------|--------|
| Protocol suite | PASS — `run_all_tests.sh` 10/10 suites (bridge 87/87, cross-language 32) |
| UE build | PASS — `ProjectTemplateEditor` build Succeeded after plugin sync (SRC `UE_Plugin/UELiveSync` → DST `ProjectTemplate/Plugins/UELiveSync`) |
| Blender | PASS — byte-parity check + all modified addon modules py_compile clean |
| Runtime (user-launched) | PENDING — binary behavior parity (Acceptance G) requires the user-launched Blender + UE session |

## Acceptance Criteria

1. `FBX_IMPORT_REQUEST` carries the same ObjectGUID as legacy 0x16 for the same source object.
2. Import path (`HandleImport`) is unchanged — importer remains a black box.
3. StaticMeshActor lifecycle + authority bookkeeping unchanged.
4. Stale/reordered FBX import packets rejected.
5. Legacy 0x16 is no longer a valid production packet.

Criteria 1–5 verified at code/regression level; viewport verification of the spawned StaticMeshActor is PENDING until the next user-launched runtime session.
