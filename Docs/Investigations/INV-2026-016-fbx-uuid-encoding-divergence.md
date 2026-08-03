# INV-2026-016: FBX Actor Ownership Mismatch — UUID Encoding Divergence (LE/FGuid vs RFC 4122)

> **This investigation covers the UUID wire-encoding divergence between the OBJECT
> channel (LE/FGuid layout) and the FBX/MATERIAL channels (RFC 4122 network order).**
> Scope: evidence collection and root cause isolation for the FBX actor-ownership
> mismatch. The material channel is **separately investigated** (see Remaining Unknowns).

## Metadata

- **Status**: RESOLVED via MIG-006 (LE/FGuid normalization for FBX + MATERIAL object side)
- **Owner**: Khanh
- **Started**: 2026-08-03
- **Classification**: Protocol Serialization, Object Identity / GUID Mapping
- **Depends-on**: MIG-002 / MIG-005 (semantic MsgType pipeline), Phase 1.5 smoke runtime

## Problem

The same Blender UUID is encoded with **two different byte orders** across semantic
message channels. On the UE side this yields **two different FGuid values** for the
same object. The FBX_IMPORT_REQUEST channel therefore fails `FindActorFast` and
spawns a duplicate StaticMeshActor instead of reusing the actor created by
OBJECT_CREATE.

## Symptoms

- After Start Sync, `Sync Selected Mesh to UE (FBX)` spawns a **second** actor
  `LS_FBX_BD9065F1...` instead of reusing the existing actor
  `LS_FBX_F16590BD...` created by OBJECT_CREATE (see E6).
- The new FBX actor never receives the transform binding that the OBJECT channel
  applies, so it diverges from the Blender object's live transform.
- Same-UUID material assignments (MATERIAL_ASSIGN object side) are statically
  predicted to hit the same actor-not-found path (E9); not yet runtime-proven.

## Reproduction Steps

1. Open Blender (flatpak 5.2) with the uelivesync addon loaded from the repo
   symlink chain, and open Unreal Editor (UE5.8-debug).
2. Start Sync; confirm TCP on port 57000 and a fresh connection marker.
3. In Blender, create/pin a Cube and let OBJECT_CREATE + PT_Transform emit.
4. Press **Sync Selected Mesh to UE (FBX)** for the same Cube.
5. Observe in UE: `[FBX][COALESCE] reason=actor_missing` and a new
   `LS_FBX_<byte-swapped-guid>` actor (E6), while the original actor exists under
   the LE-decoded FGuid (E4).

## Hypotheses

| ID | Hypothesis | Status |
|----|-----------|--------|
| H-A | OBJECT/CAMERA/Transform encode persistent_id as LE/FGuid; FBX and MATERIAL encode the same UUID as RFC 4122 → two distinct FGuid identities on the wire | **CONFIRMED** (E1–E5, E7–E9) |
| H-B | FBX channel's decoded FGuid does not match the actor cache key built from the OBJECT channel → `FindActorFast` MISS → spawn path | **CONFIRMED** (E6) |
| H-C | MATERIAL_ASSIGN object-side lookup (`FindActorFast(ObjectGuid)`) is affected by the same divergence | **CONFIRMED** (static: E9; runtime: E11) |

## Evidence Collected

| ID | Description | Source | Classification |
|----|-------------|--------|----------------|
| E1 | `_uuid_to_fguid_bytes` packs `struct.pack('<IIII', time_low, time_mid<<16\|time_hi, ...)` | `object_protocol.py:22-39` | SUPPORTS H-A |
| E2 | `serialize_object_v3` packs the same `<IIII` LE fields | `network.py:2644-2673` | SUPPORTS H-A |
| E3 | `_uuid_to_raw` returns `uuid.UUID.bytes` (RFC 4122 / network order) | `fbx_protocol.py:42-44`, `material_protocol.py:67-69` | SUPPORTS H-A |
| E4 | OBJECT channel wire bytes + FGuid: Blender `[SPAWN-TRACE][SEND] guid=f16590bd-e3b2-457d-8726-f587b973676e` → UE `[SPAWN-TRACE][CREATE] guid=F16590BDE3B2457D8726F587B973676E` | smoke logs (2026-08-03) | SUPPORTS H-A |
| E5 | UE handler decodes by raw `FMemory::Memcpy(&Guid, View.PersistentId.data(), 16)` — byte order is exactly what Blender sent | `UELiveSyncSubsystem.cpp:7578-7605` (OnObjectCreate), `8725-8726` (OnFbxImportRequest) | SUPPORTS H-A |
| E6 | FBX channel: `[FBX][COALESCE] import guid=BD9065F17D45B2E387F526876E6773B9 reason=actor_missing` → `[FBX_SPAWN] guid=BD9065F1... actor=LS_FBX_BD9065F1` → `[FBX] Spawned StaticMeshActor: LS_FBX_BD9065F1`. The actor cached under F16590BD... is never found. | smoke logs (2026-08-03) | CONFIRMS H-B |
| E7 | Spawn/update decision keyed off `Context.FindActor(Request.ObjectGUID)`; spawn path names actor `LS_FBX_<GuidShort>` | `LiveSyncFBXImporter.cpp:2321-2330`, `2537-2543` | CONFIRMS H-B |
| E8 | ActorCache keys are FGuid strings built from actor tags `LiveSync_GUID=<Digits>`; the cache key therefore follows the OBJECT channel's LE-decoded FGuid | `UELiveSyncSubsystem.cpp:6906-6990` | SUPPORTS H-B |
| E9 | `AssignMaterial` performs `FindActorFast(ObjectGuid)` where ObjectGuid comes from the same `View.PersistentId` decoded via memcpy — for a MATERIAL_ASSIGN emitted with RFC-4122 bytes this is the byte-swapped FGuid (BD9065F1...) and will MISS the LE-keyed actor | `UELiveSyncSubsystem.cpp:8956-8970`; Blender call site `sync.py:2407-2413` uses the **same** `guid_obj` as `serialize_object_v3` | SUPPORTS H-C |
| E10 | Inventory of every UUID encoder in the semantic protocol (see below) — the divergence is exactly two encoder groups | source inventory (2026-08-03) | SUPPORTS H-A |
| E11 | Runtime (session 2026-08-03 12:07, UE PID 76289 / Blender PID 77974, Cube UUID `1dfb38b4-1227-41ce-9d3c-2dbe4953ac8a`, material slot 0): Blender sent MATERIAL_CREATE + MATERIAL_ASSIGN (`sent=1` each); UE dispatched `[BRIDGE][MATERIAL_ASSIGN]` but **no `[MATERIAL][ASSIGN] ... OK`** (Log level, non-rate-limited) appeared; material applied only via legacy PT_Material (0x05) with LE guid=1DFB38B4...; OBJECT wire `b438fb1d...` vs MATERIAL wire `1dfb38b4...` → FGuids `1DFB38B4122741CE9D3C2DBE4953AC8A` (actor key) vs `B438FB1DCE412712BE2D3C9D8AAC5349` (assign lookup) | `ue_fresh_1.log` / `blender_fresh_1.log` (2026-08-03, /tmp/uelivesync-current-test/) | CONFIRMS H-C |

### E4/E6 Hex Evidence (smoke run 2026-08-03, session boundary 09:45, UE PID/blender PID per boundary.txt)

Object `Cube`, Blender UUID `f16590bd-e3b2-457d-8726-f587b973676e`.

**OBJECT_CREATE (LE/FGuid):**

```
Blender  [SPAWN-TRACE][SEND] guid=f16590bd-e3b2-457d-8726-f587b973676e name=Cube loc=(300.0,0.0,0.0)
UE wire  (bridge view)        id=bd9065f1-7d45-b2e3-87f5-26876e6773b9   ← 16 raw LE bytes, hex-rendered
UE FGuid (FGuid::ToString)    F16590BDE3B2457D8726F587B973676E
```

Wire bytes (`_uuid_to_fguid_bytes` output):

```
bd 90 65 f1 | 7d 45 b2 e3 | 87 f5 26 87 | 6e 67 73 b9
```

FGuid fields after memcpy (x86 LE): `A=0xF16590BD B=0xE3B2457D C=0x8726F587 D=0xB973676E`.

**FBX_IMPORT_REQUEST (RFC 4122):**

```
Blender  [FBX_ENQUEUE] guid=f16590bd payload_bytes=149 packet_type=0x60
UE       [BRIDGE][FBX_IMPORT_REQUEST] id=f16590bd-e3b2-457d-8726-f587b973676e ...
UE       [FBX][COALESCE]   import guid=BD9065F17D45B2E387F526876E6773B9 reason=actor_missing
UE       [FBX_SPAWN]       guid=BD9065F17D45B2E387F526876E6773B9 actor=LS_FBX_BD9065F1
UE       [FBX][VALIDATE]   guid=BD9065F1... actor=LS_FBX_BD9065F1 mesh=Cube_BD9065F1_019FC584 ... meshValid=1
```

Wire bytes (`_uuid_to_raw` output):

```
f1 65 90 bd | e3 b2 45 7d | 87 26 f5 87 | b9 73 67 6e
```

FGuid fields after memcpy (x86 LE): `A=0xBD9065F1 B=0x7D45B2E3 C=0x87F52687 D=0x6E6773B9`.

Same UUID `f16590bd-e3b2-457d-8726-f587b973676e` ⇒ two FGuid identities:
`F16590BDE3B2457D8726F587B973676E` (OBJECT) vs `BD9065F17D45B2E387F526876E6773B9` (FBX).

### Runtime Verification (Material) — E11 CONFIRMED

Session 2026-08-03 12:07 (UE PID 76289, Blender PID 77974), Object `Cube`,
Blender UUID `1dfb38b4-1227-41ce-9d3c-2dbe4953ac8a`, material slot 0.

| # | Evidence (`ue_fresh_1.log` timestamps 05.08.34.140–151, frame 627–628) | Channel |
|---|-----------------------------------------------------------------------|---------|
| 1 | Blender `[MAT][MSGTYPE] MATERIAL_CREATE sent=1` + `MATERIAL_ASSIGN sent=1` (`blender_fresh_1.log`) | Blender |
| 2 | `[BRIDGE][MATERIAL_CREATE] id=00000000-0000-0000-1d82-7b3fadd03b00` → `[MATERIAL][CREATE] id=00000000000000003F7B821D003BD0AD` → `RegisterDefinition` populates `MaterialDatabase` (`UELiveSyncSubsystem.cpp:8567`) | MATERIAL |
| 3 | `[BRIDGE][MATERIAL_ASSIGN] object=1dfb38b4-1227-41ce-9d3c-2dbe4953ac8a material=00000000-0000-0000-1d82-7b3fadd03b00 slot=0` → `OnMaterialAssign` (`UELiveSyncSubsystem.cpp:8664`) | MATERIAL |
| 4 | **No `[MATERIAL][ASSIGN] ... OK`** (Log level, non-rate-limited, `:8985`) anywhere after dispatch → semantic assign did **not** complete | MATERIAL |
| 5 | Material actually applied via **legacy PT_Material (0x05)**: `[MAT][RECV] guid=1DFB38B4122741CE9D3C2DBE4953AC8A` → `[MATERIAL][MATX_FULL_SNAPSHOT_APPLY] effectiveSlots=1 appliedSlots=1` | legacy |
| 6 | Wire bytes, same UUID, two encodings (bridge hex): OBJECT `b438fb1d-ce41-2712-be2d-3c9d8aac5349` (LE) vs MATERIAL_ASSIGN `1dfb38b4-1227-41ce-9d3c-2dbe4953ac8a` (RFC) | both |

Wire math for `1dfb38b4-1227-41ce-9d3c-2dbe4953ac8a`:

```
OBJECT    b4 38 fb 1d | ce 41 27 12 | be 2d 3c 9d | 8a ac 53 49   →  FGuid 1DFB38B4122741CE9D3C2DBE4953AC8A  (actor key, SPAWN-TRACE)
MATERIAL  1d fb 38 b4 | 12 27 41 ce | 9d 3c 2d be | 49 53 ac 8a   →  FGuid B438FB1DCE412712BE2D3C9D8AAC5349  (assign lookup)
```

Chain: MATERIAL_ASSIGN dispatched → stale-check passes (first assign) → `Resolve(00000000000000003F7B821D003BD0AD)` is **deterministic-success** (DB entry from `RegisterDefinition`, master material present `[MAT][MASTER] load_existing`, `UMaterialInstanceDynamic::Create` succeeds) → `AssignMaterial` → `FindActorFast(B438FB1DCE412712BE2D3C9D8AAC5349)` **MISS** (actor keyed `1DFB38B4...`) → assignment dropped. The literal "actor not found" marker is Verbose and rate-limited (`ShouldLogVerbose`, `:1110-1116`), so it is not captured; the drop itself is directly evidenced by the absent Log-level OK.

**Confidence**: High. The alternative (`Resolve` null) is eliminated by construction: the material definition is registered into the **same** `MaterialDefinitionDatabase` before the assign, and the factory (`GetOrCreateLiveSyncMasterMaterial` + `Create`) is deterministic-success. The only consistent bail is `FindActorFast` MISS — the byte-swap consequence of H-C. Mirrors the FBX result exactly (E6/E11 use different UUIDs but identical byte-order structure).

## UUID Encoder Inventory (whole semantic protocol)

| Message | Builder | Encoder | Byte order |
|---------|---------|---------|-----------|
| OBJECT_CREATE | `build_object_create` `object_protocol.py:67,74` | `_uuid_to_fguid_bytes` | LE/FGuid |
| OBJECT_UPDATE | `build_object_update` `:116` | `_uuid_to_fguid_bytes` | LE/FGuid |
| OBJECT_DELETE | `build_object_delete` `:190` | `_uuid_to_fguid_bytes` | LE/FGuid |
| OBJECT_RENAME | `build_object_rename` `:215` | `_uuid_to_fguid_bytes` | LE/FGuid |
| OBJECT_VISIBILITY | `build_object_visibility` `:231` | `_uuid_to_fguid_bytes` | LE/FGuid |
| OBJECT_REPARENT | `build_object_reparent` `:247,249` | `_uuid_to_fguid_bytes` | LE/FGuid |
| PT_Transform (snapshot) | `serialize_object_v3` `network.py:2667-2673` | inline `<IIII` | LE/FGuid |
| CAMERA_CREATE | `build_camera_create` `:319,322` | `_uuid_to_fguid_bytes` | LE/FGuid |
| CAMERA_UPDATE | `build_camera_update` `:370` | `_uuid_to_fguid_bytes` | LE/FGuid |
| CAMERA_SETACTIVE | `build_camera_setactive` `:396` | `_uuid_to_fguid_bytes` | LE/FGuid |
| MATERIAL_CREATE | `build_material_create` `material_protocol.py:96` | `_uuid_to_raw` | RFC 4122 |
| MATERIAL_UPDATE | `build_material_update` `:143` | `_uuid_to_raw` | RFC 4122 |
| MATERIAL_ASSIGN (object side) | `build_material_assign` `:184` | `_uuid_to_raw` | RFC 4122 |
| MATERIAL_ASSIGN (material side) | `build_material_assign` `:190` | `_uuid_to_raw` | RFC 4122 |
| FBX_IMPORT_REQUEST | `build_fbx_import_request` `fbx_protocol.py:66` | `_uuid_to_raw` | RFC 4122 |

Helper notes:

- `network.pack_ue_fguid` `network.py:2977` and `_pack_guid` `:2995` — LE/FGuid (legacy serializer helpers).
- `network.serialize_object` `:2592-2602` — raw `bytes.fromhex(guid_hex)` (RFC-layout hex). **No production callers remain**; the production object path is `serialize_object_v3` (LE).
- `material_protocol._uuid_to_raw_from_hex` `:72-75` — RFC 4122 (material hash side).

### Material channel self-consistency (partial — see E9 caveat)

- `OnMaterialCreate` `UELiveSyncSubsystem.cpp:8541` stores `MaterialCreateStorage.Add(MaterialGuid, ...)`; `MaterialGuid` is decoded from the same RFC bytes on the wire, so create/update storage is self-consistent.
- `MaterialRegistry::Resolve(FGuid)` `MaterialRegistry.cpp:11-30` keys `Cache`/`Database` by that same FGuid — no actor lookup by material GUID.
- **Caveat**: `MATERIAL_ASSIGN` object side carries the object's `persistent_id` RFC-encoded, then `AssignMaterial` does `FindActorFast(ObjectGuid)` `UELiveSyncSubsystem.cpp:8963`. The object GUID is a **cross-channel reference into the OBJECT channel's LE/FGuid namespace** — statically the same divergence as FBX (E9). Runtime verification is pending (see Remaining Unknowns).

## Cross-Channel GUID Contract Inventory

Which messages reference another channel's GUID, and does the encoding match?

### Per-message GUID usage on UE

| Msg | GUID field(s) | Encoding | UE consumer | GUID role | Actor lookup? |
|-----|---------------|----------|-------------|-----------|---------------|
| OBJECT_CREATE | persistent_id, parent_id | LE | `OnObjectCreate` → `HandleCreateObject` | actor identity (spawn, tag, cache key) | creates identity |
| OBJECT_UPDATE | persistent_id | LE | `OnObjectUpdate` → `UpdateTargetTransform` | actor ref | `FindActorFast` (:8065) |
| OBJECT_DELETE | persistent_id | LE | `HandleDelete` | actor ref | `FindActorFast` (:8313) |
| OBJECT_RENAME | persistent_id | LE | `HandleRename` | actor ref | `FindActorFast` (:8456) |
| OBJECT_VISIBILITY | persistent_id | LE | `HandleVisibility` | actor ref | `FindActorFast` |
| OBJECT_REPARENT | persistent_id, new_parent_id | LE | `HandleReparent` | actor + parent refs | `FindActorFast` (:10096, :10207) |
| PT_Transform | guid_obj, parent_guid_obj | LE | `UpdateTargetTransform` | actor + parent refs | `FindActorFast` (:5361, :5551) |
| CAMERA_CREATE | camera_id, parent_id | LE | `OnCameraCreate` | camera actor identity | `FindActorFast` (:7706, :7790) |
| CAMERA_UPDATE | camera_id | LE | `OnCameraUpdate` | camera actor ref | `FindActorFast` (:7901) |
| CAMERA_SETACTIVE | camera_id | LE | `OnCameraSetActive` → `HandleActiveCamera` | camera ref | `FindActorFast` (:7239, :7253) |
| MATERIAL_CREATE | material_id | RFC | `OnMaterialCreate` | material identity (storage key) | no (storage/registry) |
| MATERIAL_UPDATE | material_id | RFC | `OnMaterialUpdate` | material identity | no (storage/registry) |
| MATERIAL_ASSIGN | persistent_id (**object**), material_id | RFC | `OnMaterialAssign` → `AssignMaterial` | **object ref (cross-channel)** + material ref | **`FindActorFast(ObjectGuid)` (:8963)** |
| FBX_IMPORT_REQUEST | persistent_id (**object**) | RFC | `OnFbxImportRequest` → `FLiveSyncFBXImporter` | **object ref (cross-channel)** | **`FindActorFast` (:8785, importer:2321)** |

### Cross-channel contract

| Contract | Producer fields | Encoding pair | Must match? | Status |
|----------|-----------------|---------------|-------------|--------|
| Object GUID across OBJECT → FBX | `OBJECT_CREATE.persistent_id` ⇔ `FBX_IMPORT_REQUEST.persistent_id` | LE vs RFC | **Yes — actor identity** | **MISMATCH (confirmed, E6)** |
| Object GUID across OBJECT → MATERIAL_ASSIGN | `OBJECT_CREATE.persistent_id` ⇔ `MATERIAL_ASSIGN.persistent_id` | LE vs RFC | **Yes — actor identity** | **MISMATCH (confirmed, E11)** |
| Object GUID across OBJECT → CAMERA | `OBJECT_CREATE.persistent_id` ⇔ `CAMERA_CREATE.camera_id` | LE vs LE | Yes — actor identity | MATCH (smoke: camera reused, "already exists — updating in place") |
| Object GUID across OBJECT → parent refs | `OBJECT_CREATE.persistent_id` ⇔ `OBJECT_REPARENT.new_parent_id` / `PT_Transform.parent_guid_obj` | LE vs LE | Yes — parent actor | MATCH |
| Material GUID across MATERIAL → MATERIAL_ASSIGN | `MATERIAL_CREATE.material_id` ⇔ `MATERIAL_ASSIGN.material_id` | RFC vs RFC | Yes — material identity | MATCH |
| Material GUID → object reverse apply | `ReapplyMaterialAssignments` keyed by material | RFC (key) | n/a — matches by **pointer**, not GUID | not affected by divergence |

### Contract conclusion

Only **two** messages reference an Object GUID across channels with a **mismatched**
encoding: `FBX_IMPORT_REQUEST` (confirmed broken, E6) and `MATERIAL_ASSIGN` object side
(runtime-confirmed broken, E11). Every object-namespace-internal
reference (REPARENT parent, CAMERA-as-object) shares the OBJECT channel's LE
encoding, and every material-namespace-internal reference shares the material
channel's RFC encoding.

Implication for the future normalize MIG:
- Case A is **runtime-confirmed** (E11), so minimal patch = normalize the
  Object-GUID reference in `MATERIAL_ASSIGN` + `FBX_IMPORT_REQUEST` to the
  LE/FGuid layout used by the object namespace.
- Material CREATE/UPDATE (`material_id`) never cross into the object namespace and
  can remain RFC without behavioral impact.

## Decision Log

| ID | Decision | Based on | Accepted | Rejected | Reason |
|----|----------|----------|----------|----------|--------|
| D1 | Isolate root cause at wire-encoding layer, not actor cache | E4/E6 hex evidence | Byte-order divergence between encoder groups | ActorCache build/`FindActorFast` bug | The two FGuid values prove the divergence precedes the cache; cache behavior is a downstream symptom |
| D2 | Do not open a fix (MIG) yet | User directive (2026-08-03) | Complete investigation record + Material lifecycle investigation first | Immediate normalize patch | Canonical wire representation is an architectural decision not yet settled; evidence incomplete for Material |
| D3 | Do not record "canonical = LE" as a conclusion | User directive | Record inventory + hex + confirmed FBX root cause only | Asserting LE as canonical | Canonical selection is a design decision; the inventory shows a 2-group split (10 LE vs 5 RFC) and Material behavior is not yet fully proven |

## Decision Tree

**v1**

```
Blender UUID f16590bd-e3b2-457d-8726-f587b973676e
      │
      ▼
Which encoder does each channel use?
      │
      ├── OBJECT / CAMERA / PT_Transform → _uuid_to_fguid_bytes / <IIII>  → LE bytes  bd9065f1...  → FGuid F16590BD... (E4)
      │
      └── FBX / MATERIAL                → _uuid_to_raw (uuid.bytes)       → RFC bytes f16590bd...  → FGuid BD9065F1... (E6)
                                               │
                                               ▼
      FBX channel: FindActorFast(FGuid=BD9065F1...) ?
            │
            ├── Yes → update existing actor (not observed)
            │
            └── No  → [FBX][COALESCE] reason=actor_missing → spawn LS_FBX_BD9065F1 (E6)   ← CONFIRMED
                        │
                        ▼
      MATERIAL_ASSIGN object side: FindActorFast(ObjectGuid=BD9065F1...)?
            │
            └── No → [MATERIAL][ASSIGN] actor not found → assignment dropped (runtime-confirmed, E11)   ← CONFIRMED
```

## Root Cause (FBX) — CONFIRMED

Two UUID encoder groups exist in the semantic protocol:

1. **LE/FGuid group** (Object, Camera, PT_Transform): `_uuid_to_fguid_bytes`
   / `serialize_object_v3` pack `struct.pack('<IIII', time_low, ...)` — produces
   the byte sequence that UE's `FGuid` (memcpy'd raw) renders as the canonical
   `FGuid::ToString` value. Actors spawned by OBJECT_CREATE are cached/tagged under
   this value (`LiveSync_GUID=<Digits>` tag → `BuildActorCache`).

2. **RFC 4122 group** (FBX_IMPORT_REQUEST, MATERIAL_*): `_uuid_to_raw` returns
   `uuid.UUID.bytes` (network order). After UE's raw memcpy, the resulting FGuid is
   byte-swapped relative to group 1.

Because `FbxRequestGuid` (group 2) ≠ object's FGuid (group 1), the FBX handler's
`Context.FindActor(Request.ObjectGUID)` (`LiveSyncFBXImporter.cpp:2321`) misses, and
the spawn path creates a new `LS_FBX_<byte-swapped>` StaticMeshActor
(`:2524-2543`). The transform pipeline then operates on the LE-keyed actor, which
is a **different actor** from the one the FBX mesh was applied to.

**Confidence**: High — direct wire hex evidence (E4/E6) + handler code path (E5/E7).

## Why Existing Tests Missed It

- Protocol unit/vector tests verify byte layout per builder against expected
  vectors, but each channel's expected vector was generated from its own encoder —
  the tests never assert that **the same UUID produces one identical 16-byte
  sequence across channels**.
- Runtime smoke (Phase 1.5, 6/6) exercises OBJECT/CAMERA/FBX paths independently;
  it confirmed import success (`meshValid=1`) but did not assert
  *actor identity reuse* (that the FBX mesh lands on the same actor the OBJECT
  channel created). The duplicate actor was present but not flagged as a failure.

## Fix

**Implemented by MIG-006** (see `Docs/Architecture/71-mig-006-uuid-normalization-semantic-protocol.md`,
Stage 3 complete, Stage 5 runtime acceptance pending). The canonical Object-GUID
wire encoding is LE/FGuid via the shared `Blender_Addon/protocol_guid.py`
`uuid_to_fguid_bytes` encoder; the two cross-channel object-GUID references
(`FBX_IMPORT_REQUEST.persistent_id`, `MATERIAL_ASSIGN` object side) now emit the
same 16-byte sequence as the OBJECT channel, so `FindActorFast` resolves the same
actor. Note: this guarantees **actor identity resolution**, not actor instance
preservation — the importer may still destroy a procedural actor and spawn a
`StaticMeshActor` (single-actor invariant, see ADR Stage 5 cases 1–2).

## Regression

Not applicable at investigation time (no code change then). Post-MIG-006: full
suite PASS (10/10) + cross-channel GUID contract test
(`Tests/Protocol/tests/test_cross_channel_guid_contract.py`).

## Remaining Unknowns

- **Material end-to-end** — **RESOLVED (E11)**: in a runtime scenario with a material
  slot (Cube, slot 0, 2026-08-03), MATERIAL_ASSIGN reached `OnMaterialAssign` →
  `AssignMaterial`, and the semantic assign was **dropped**: no Log-level
  `[MATERIAL][ASSIGN] ... OK` after dispatch, while the wire bytes show the object
  GUID is RFC-encoded (`B438FB1D...` after memcpy) vs the actor key (`1DFB38B4...`,
  OBJECT LE). `Resolve` is deterministic-success (DB entry from `RegisterDefinition`,
  master material present), so the bail is `FindActorFast` MISS. Material answers
  still required:
  - Where does UE use the material `persistent_id` after receive? (Storage +
    Registry + Resolve only — no actor lookup by material GUID confirmed.)
  - Any map keyed by material GUID that crosses object ownership? (None found.)
  - Any `memcmp` of GUIDs between Object and Material channels? (None found.)
  - Is the material GUID used only as internal identity, or for ownership?
  - Same question for the legacy `_uuid_to_raw_from_hex` material-hash side.
- **Canonical representation decision** — evidence inventory shows 10 LE/FGuid
  messages vs 5 RFC 4122 messages, but canonical choice is an architectural
  decision, not an evidence conclusion (D3).

## Investigation Retrospective

- **What worked**: runtime smoke log correlation (fresh boundaries per AGENTS.md)
  produced the exact hex pairs needed; the FGuid ↔ wire-byte mapping was verifiable
  from both channels for the same UUID.
- **What wasted time**: none significant; the inventory step (whole-protocol
  encoder audit) surfaced the Material caveat that a narrower FBX-only audit would
  have missed.
- **Assumption corrected**: the earlier claim that "MATERIAL_ASSIGN ObjectGuid is
  used only for stale-rejection, not actor lookup" was **wrong** — `AssignMaterial`
  does `FindActorFast(ObjectGuid)` (E9). Runtime confirmation followed (E11): the
  semantic assign is dropped exactly as the byte-swap predicts, so the normalize
  MIG must cover both `FBX_IMPORT_REQUEST` and `MATERIAL_ASSIGN` object side.
