# MIG-006: UUID Normalization Across Semantic Protocol

## Status: IMPLEMENTED — Stage 5 runtime acceptance PASS (FBX cases)

Implementation complete: production builders now encode both cross-channel
Object-GUID references with the shared LE/FGuid encoder; reference serializers
mirrored; vectors regenerated; FGuid-aware comparison added; C++ test
expectations updated; cross-channel regression test added. All protocol test
suites PASS (C++ 8/8 + Python 56 + cross-language 32).

Stage 5 runtime acceptance (user-launched session 2026-08-03 20:42, UE PID 18234,
Blender PID 20274): **both FBX actor-identity cases PASS** — evidence slices in
`.evidence/runtime/mig-006-stage5/`:
- Case 1 (first FBX import): `[FBX][COALESCE] reason=non_static_actor` (HIT) →
  exactly one `[FBX] Spawned StaticMeshActor: LS_FBX_54356298` →
  `[FBX][AUTH] cleanup_stale_procedural actor=Actor_0` (destroy) →
  `[FBX_ACTOR_CACHED] actor=LS_FBX_54356298` → `meshValid=1`; no duplicate,
  no `actor_missing`, no byte-swapped GUID.
- Case 2 (re-import, unchanged geometry): HIT StaticMeshActor →
  `[FBX][SKIP] duplicate semantic ... same_semantic_signature`, 0 spawn.
- Case 2B (re-import, geometry changed): `[FBX][COALESCE] reason=geometry_hash_changed`
  → `[FBX_SET_MESH] path=update` → `[FBX] Updated StaticMeshActor: LS_FBX_54356298`, 0 spawn.
- Transform continuation: `OBJECT_UPDATE` (transform=1) for the same GUID →
  `[TRANSFORM][VIEWPORT] 1 transforms applied`, resolved via
  `FindActorFast`/ActorCache to `LS_FBX_54356298`.

Remaining follow-ups (out of MIG-006 scope): material-assign runtime slice
(`[MATERIAL][ASSIGN] object=<correct FGuid> slot=0 material=... OK`) and legacy
PT_Material decommission.

## Summary

Normalize the Object-GUID wire encoding of the two cross-channel references that
currently diverge from the object namespace: `FBX_IMPORT_REQUEST.persistent_id`
and `MATERIAL_ASSIGN.persistent_id`. Both are Object-GUID references into the
object/actor identity namespace but are serialized with the RFC 4122 encoder
(`_uuid_to_raw`), while the object namespace and actor cache use the LE/FGuid
encoder (`_uuid_to_fguid_bytes`). The result is two distinct FGuid identities per
object on the wire (INV-2026-016, root cause CONFIRMED for FBX via E6 and for
Material via E11).

This MIG is **not** "fix the FBX actor lookup" — it normalizes the GUID contract
between semantic channels so the actor identity resolves everywhere. The lookup
misses are symptoms of the encoding divergence, not the cause.

Evidence: `Docs/Investigations/INV-2026-016-fbx-uuid-encoding-divergence.md`.

## Problem

Two UUID encoder groups exist in the semantic protocol:

1. **LE/FGuid group** (`protocol_guid.uuid_to_fguid_bytes`,
   `struct.pack('<IIII', ...)`): used by OBJECT_*, CAMERA-as-object, REPARENT,
   PT_Transform. After UE's raw `FMemory::Memcpy`
   into `FGuid`, this layout yields the actor cache key
   (`LiveSync_GUID=<FGuid::ToString Digits>` tag → `BuildActorCache`).
2. **RFC 4122 group** (`fbx_protocol._uuid_to_raw`, `fbx_protocol.py:42-44`;
   `material_protocol._uuid_to_raw`, `material_protocol.py:67-69`): used by
   FBX_IMPORT_REQUEST and MATERIAL_*. After the same raw memcpy, the FGuid is
   byte-swapped relative to group 1.

Runtime proof (INV-2026-016, E11, session 2026-08-03): object `Cube`,
UUID `1dfb38b4-1227-41ce-9d3c-2dbe4953ac8a`.

```
OBJECT (LE):    b4 38 fb 1d | ce 41 27 12 | be 2d 3c 9d | 8a ac 53 49   →  FGuid 1DFB38B4122741CE9D3C2DBE4953AC8A  (actor key, SPAWN-TRACE)
MATERIAL (RFC): 1d fb 38 b4 | 12 27 41 ce | 9d 3c 2d be | 49 53 ac 8a   →  FGuid B438FB1DCE412712BE2D3C9D8AAC5349  (assign lookup, MISS)
```

- FBX: `FindActorFast` misses → duplicate `LS_FBX_<byte-swapped>` actor spawned.
- MATERIAL_ASSIGN: `OnMaterialAssign` → `AssignMaterial` →
  `FindActorFast(B438FB1D...)` MISS → semantic assign dropped (material still
  applies only via the legacy PT_Material 0x05 path).

## Stage 1 — Contract (canonical wire encoding decision)

**Proposed canonical:**
- **Object-namespace GUID references** (the "gameplay GUID" / actor identity):
  wire = **LE/FGuid** (`_uuid_to_fguid_bytes` layout). This is what the object
  channel already emits and what the actor cache / `FindActorFast` key on.
  Every message that references an object identity — including cross-channel
  references — MUST use this layout.
- **Material-namespace GUID references** (`material_id`): wire = **RFC 4122**
  (unchanged). Material identity is internal to the material channel; it never
  crosses into the object namespace and stays as-is.

This is a scoped contract decision for the cross-channel Object-GUID contract.
It does **not** assert "canonical = LE" as an evidence conclusion (INV-2026-016
D3); it is the design decision the MIG proposes, grounded in the fact that the
actor identity namespace and 10/15 LE-encoded messages already define the layout.

**Channel-by-channel contract (post-MIG):**

| Message / field | Encoding | Change |
|-----------------|----------|--------|
| OBJECT_CREATE / UPDATE / DELETE / RENAME / VISIBILITY `.persistent_id` | LE/FGuid | none (already LE) |
| OBJECT_REPARENT `.new_parent_id` | LE/FGuid | none |
| CAMERA_CREATE / UPDATE `.camera_id` (+ camera-as-object refs) | LE/FGuid | none |
| PT_Transform `.parent_guid_obj` | LE/FGuid | none |
| **FBX_IMPORT_REQUEST `.persistent_id`** | LE/FGuid | **RFC → LE** |
| **MATERIAL_ASSIGN `.persistent_id`** | LE/FGuid | **RFC → LE** |
| MATERIAL_CREATE / UPDATE `.material_id` | RFC | none |
| MATERIAL_ASSIGN `.material_id` | RFC | none |

## Stage 2 — Inventory & Impact

### Production (Blender addon) — the fix

| File | Anchor | Change |
|------|--------|--------|
| `Blender_Addon/fbx_protocol.py` | `build_fbx_import_request` `:47-66`, `persistent_id` `:62-66` | encode `persistent_id` with the LE/FGuid encoder |
| `Blender_Addon/material_protocol.py` | `build_material_assign` `:170-201`, `persistent_id` `:180-184` | encode `persistent_id` with the LE/FGuid encoder; keep `material_id` `:186-190` RFC |
| `Blender_Addon/fbx_protocol.py` / `material_protocol.py` | `_uuid_to_raw` `:42-44` / `:67-69` | remove if no longer used, or keep only for `material_id`/unused legacy |

Shared encoder: `Blender_Addon/protocol_guid.py` (`uuid_to_fguid_bytes` +
`uuid_to_rfc4122_bytes`), used by `object_protocol.py`, `fbx_protocol.py`, and
`material_protocol.py` (no import cycle: all three import only from
`msg_transport` and `protocol_guid`). Do **not** duplicate the encoder.

Call sites pass `uuid.UUID` objects (verified: `sync.py:2407-2413`,
`__init__.py:2450-2459`), so only the builder bodies change.

### UE plugin — NO wire change

Handlers decode by raw memcpy and need no change:
- `UELiveSyncSubsystem.cpp:8669-8670` (OnMaterialAssign memcpy ObjectGuid),
  `:8960-8963` (AssignMaterial FindActorFast), `:8725-8726` (OnFbxImportRequest
  memcpy FbxRequestGuid).
- BRIDGE logs render bytes via `FormatUuid`
  (`LiveSyncProtocolBridge.h:1038,1380`); after the fix they will render the LE
  form — cosmetically consistent with OBJECT_CREATE's bridge lines. No handler
  logic depends on RFC rendering.

### Test reference serializers — MUST mirror production to keep byte-parity

| File | Anchor | Change |
|------|--------|--------|
| `Tests/Protocol/serializer/serializer.py` | `pack_uuid` `:70-77` (RFC); uuid field packing `:134-135` | add FGuid-order packing for `FBX_IMPORT_REQUEST.persistent_id` and `MATERIAL_ASSIGN.persistent_id` |
| `Shared/Serializer/livesync_messages.h` | `serialize_body_material_assign` `:291-305`, `serialize_body_fbx_import_request` `:307-328` | pack those two `persistent_id` fields in FGuid order |

### Test vectors / expectations — regenerate

- `Tests/Protocol/vectors/v1/FBX_IMPORT_REQUEST.bin`, `MATERIAL_ASSIGN.bin`,
  `cpp_serialized/` counterparts, `cpp_deserialized.json`, `SHA256SUMS`,
  `generate_vectors.py`.
- `Tests/Protocol/cross_language_verify.py` — `UUID_FIELDS` already lists
  `persistent_id` for both (`:186-187`); the byte-order comparison
  (`compare_uuid_format` `:134-142`) must become FGuid-aware for these two
  fields (LE bytes → canonical uuid), matching how OBJECT fields are compared
  against the manifest's RFC uuid strings.
- `Shared/Serializer/tests/support/manifest_loader.h:215,223`,
  `reserialize.h:270,279`, `test_cross_language.cpp:305,311`,
  `test_property.cpp:696` — update expected persistent_id byte references.

### Known pre-existing test-fidelity gap (documented, OUT of scope)

The reference vectors encode **all** uuid fields as RFC 4122 — including
`OBJECT_CREATE.persistent_id` — which does **not** match the production object
channel (LE). This is why the suite never caught INV-2026-016: it is internally
RFC-consistent and never asserted cross-channel byte identity, and its uuid
layout does not match production for the object channel. MIG-006 keeps scope to
the two normalizing messages; reconciling the whole reference vector set with
production LE is a separate follow-up.

## Stage 3 — Implementation

Follow the MIG-001..005 commit order: protocol refs → Blender → tests → docs.

1. `Blender_Addon/fbx_protocol.py`, `material_protocol.py` — the two builder
   changes (LE/FGuid for `persistent_id`).
2. `Tests/Protocol/serializer/serializer.py`, `Shared/Serializer/livesync_messages.h`
   — mirror in the reference serializers.
3. Regenerate vectors + `cpp_deserialized.json` + `SHA256SUMS`; update
   `cross_language_verify.py` FGuid-aware comparison and C++ test expectations.
4. **New regression test**: cross-channel GUID contract — assert the **same
   uuid.UUID produces byte-identical persistent_id sequences** in
   OBJECT_CREATE, FBX_IMPORT_REQUEST, and MATERIAL_ASSIGN (the test that would
   have caught INV-2026-016). Placed in `Tests/Protocol/` alongside the vector
   tests.
5. Docs — this ADR + INV-2026-016 already updated.

## Stage 4 — Regression

| Scenario | Expected |
|----------|----------|
| FBX_IMPORT_REQUEST for an existing object | `FindActorFast(ObjectGUID)` HIT → update path, **no** `LS_FBX_*` duplicate actor |
| FBX_IMPORT_REQUEST for a new object | spawn path, named `LS_FBX_<guid>` from the correct FGuid |
| MATERIAL_ASSIGN object side | `FindActorFast(ObjectGUID)` HIT → `[MATERIAL][ASSIGN] ... OK`, `MaterialAssignmentsSucceeded++` |
| MATERIAL_CREATE / UPDATE (`material_id`) | unchanged (RFC) — material identity, Resolve, storage unaffected |
| OBJECT/CAMERA/REPARENT/PT_Transform channels | unchanged (LE already) |
| Legacy PT_Material (0x05) | unchanged during MIG; see Runtime Acceptance for its decommissioning |
| Protocol vector suite | all regenerated vectors pass; addon↔reference byte-parity holds |
| Cross-language | C++ deserialize of new LE vectors agrees with manifest (FGuid-aware) |

## Stage 5 — Runtime Acceptance (user-launched, fresh log boundaries per AGENTS.md)

> **Scope note**: MIG-006 guarantees **actor identity resolution**, not actor
> instance preservation. The FBX importer remains free to destroy a procedural
> actor and replace it with a `StaticMeshActor` (its design decision, fixed in
> INV-2026-016). "No duplicate actor" is the invariant, not "never spawn a new
> actor". Acceptance is therefore split into two cases below.

1. **FBX actor identity — Case 1 (first FBX import)**: in a user-launched
   Blender 5.1 + UE5.8 session, a mesh synced to UE for the first time must:
   - `FindActorFast(ObjectGUID)` HIT the OBJECT channel's procedural actor
     (the FBX byte order resolves the correct actor);
   - `[FBX][AUTH] cleanup_stale_procedural` destroys that actor;
   - exactly **one** `[FBX] Spawned StaticMeshActor: LS_FBX_<guid>` appears;
   - after import only one actor exists (no duplicate);
   - subsequent transform (`PT_Transform`) continues to update the surviving actor.
2. **FBX actor identity — Case 2 (re-import)**: re-syncing the same object must:
   - `FindActorFast(ObjectGUID)` HIT the existing `StaticMeshActor`;
   - `[FBX_SET_MESH] ... path=update` + `[FBX] Updated StaticMeshActor: <name>`
     appear;
   - **no** new `LS_FBX_*` spawn for that GUID.
3. **Material assign semantic**: with a material slot on the object, the fresh
   UE slice must show `[MATERIAL][ASSIGN] object=<correct FGuid> slot=0 material=... OK`.
4. **Legacy PT_Material decommission** (post-MIG, follow-up): after semantic
   MATERIAL_* PASS, disable the legacy 0x05 path per the MIG-001..004 pattern and
   confirm `[MATERIAL][MATX_FULL_SNAPSHOT_APPLY]` no longer appears in fresh
   slices (or is confirmed superseded by the semantic path).
5. No regression in OBJECT/CAMERA channels in the same session.

## Design Decisions

- **D1** — Canonical Object-GUID wire encoding = LE/FGuid (`protocol_guid.py`
  `uuid_to_fguid_bytes` layout); `material_id` stays RFC. Rationale: actor
  identity namespace + actor cache already key on the LE-decoded FGuid;
  normalizing the two cross-channel refs to LE makes every channel resolve the
  same object.
- **D2** — Fix at the producer (Blender builders), not the consumer. UE already
  memcpys raw bytes; re-encoding on UE would only add a second divergent view of
  the contract.
- **D3** — Only the two cross-channel Object-GUID references change. Material
  CREATE/UPDATE and `material_id` are internal and untouched.
- **D4** — Single shared encoder: `Blender_Addon/protocol_guid.py`
  (`uuid_to_fguid_bytes` + `uuid_to_rfc4122_bytes`), imported by
  `object_protocol.py`, `fbx_protocol.py`, `material_protocol.py` (no
  duplication, no import cycle, no bpy dependency).

## Invariants Preserved

- `FBX_IMPORT_REQUEST` and `MATERIAL_ASSIGN` field sets, opcodes, and trailing
  `sequence_number`/`timestamp` unchanged — only the `persistent_id` byte layout.
- UE handler code, actor cache, authority bookkeeping unchanged.
- Material identity (`material_id`) and Registry/Resolve unchanged.
- Legacy PT_Material behavior unchanged during this MIG.

## Rollback

- Single commit per change; rollback = `git revert` of the commit(s). The two
  builder changes are independent of the test-vector regeneration; revert
  production first, then tests, then docs.

## Open Questions

- Whether the pre-existing RFC-encoded reference vectors for the object channel
  (OBJECT_CREATE etc.) should also be migrated to LE in a follow-up
  (test-fidelity cleanup, OUT of scope here).
- Legacy PT_Material decommission timing (Stage 5 item 3) — separate MIG or
  folded into this one after runtime acceptance.
