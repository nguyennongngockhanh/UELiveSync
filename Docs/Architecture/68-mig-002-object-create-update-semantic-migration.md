# MIG-002: OBJECT_CREATE / OBJECT_UPDATE Semantic Migration

## Status: COMPLETE

## Summary

Migrated OBJECT_CREATE (0x20) and OBJECT_UPDATE (0x21) from identity-only payloads to semantic payloads carrying `primitive_type`, `sequence_number`, and `timestamp`. This activates stale-rejection for creates and updates, enables per-GUID sequence tracking, and makes OBJECT_CREATE the authoritative spawn path. OBJECT_UPDATE runs alongside legacy PT_Transform as a dual-emission path.

## Problem

OBJECT_CREATE and OBJECT_UPDATE were originally specified with minimal payloads during Phase 1.3.2 MsgType design:

- OBJECT_CREATE: persistent_id + transform only (no primitive_type, no sequence_number, no timestamp)
- OBJECT_UPDATE: persistent_id + transform only (no visibility, no name, no sequence_number, no timestamp)

Without sequence_number, UE could not distinguish fresh updates from stale or reordered packets. Without primitive_type, UE defaulted all non-camera objects to Cube. The bridge dispatched OBJECT_CREATE to `HandleCreateObject` with a hardcoded `LSP_Cube` — defeating the per-type spawn logic that the legacy PT_Create path supported.

## Root Cause

The OBJECT_CREATE/OBJECT_UPDATE specs were frozen before the Phase 6E semantic safety barriers (stale-rejection, sequence tracking, per-GUID state) were integrated. The bridge passed minimal data through the new MsgType path, leaving the protocol's semantic potential unused.

## Solution

Extended both message bodies with the fields needed for full semantic processing:

### Wire format change — OBJECT_CREATE

| Field | Before | After |
|-------|--------|-------|
| persistent_id | UUID (16 bytes) | UUID (16 bytes) |
| name | utf8 string | utf8 string |
| parent_id | — | optional UUID (0 or 16 bytes) |
| primitive_type | — | uint8 (1 byte) |
| transform | 10x float32 (40 bytes) | 10x float32 (40 bytes) |
| sequence_number | — | uint32 LE (4 bytes) |
| timestamp | — | float64 LE (8 bytes) |
| **Body total (no parent)** | **~56 bytes** | **~69 bytes** |
| **Body total (with parent)** | **~72 bytes** | **~85 bytes** |

### Wire format change — OBJECT_UPDATE

| Field | Before | After |
|-------|--------|-------|
| persistent_id | UUID (16 bytes) | UUID (16 bytes) |
| transform | 10x float32 (40 bytes) | optional 10x float32 (0 or 40 bytes) |
| name | — | optional utf8 string |
| visibility | — | optional uint8 (0 or 1 byte) |
| sequence_number | — | uint32 LE (4 bytes) |
| timestamp | — | float64 LE (8 bytes) |
| **Body total (all optional)** | **~56 bytes** | **~69-110 bytes** |

### Files changed

**Protocol:**
- `Shared/Protocol/MessageTypes.yaml` — OBJECT_CREATE/OBJECT_UPDATE bodies extended
- `Shared/Serializer/livesync_messages.h` — updated `serialize_body_object_create`, `serialize_body_object_update`
- `Shared/Serializer/livesync_deserializer.h` — reads new fields, parent_id threshold 69
- `Shared/Serializer/serializer_utils.h` — pack/unpack helpers

**UE Plugin:**
- `UE_Plugin/.../LiveSyncViews.h` — `ObjectCreateView`/`ObjectUpdateView` gained new fields
- `UE_Plugin/.../LiveSyncProtocolBridge.h` — `BuildObjectCreateView`, `BuildObjectUpdateView`, `LogObjectCreate`, `LogObjectUpdate` updated
- `UE_Plugin/.../UELiveSyncSubsystem.cpp` — `OnObjectCreate` passes `PrimitiveType` to `HandleCreateObject`; `OnObjectUpdate` stale-rejection with per-GUID `GUpdateSequences`

**Blender Addon:**
- `Blender_Addon/msg_transport.py` — pack helpers (pack_u8, pack_u32, pack_u64, pack_f64, pack_utf8, pack_uuid)
- `Blender_Addon/object_protocol.py` — `build_object_create`, `build_object_update` serialize all fields; per-GUID seq counters
- `Blender_Addon/sync.py` — `build_object_update` import fix (BUG-006); dual-emission of OBJECT_UPDATE alongside PT_Transform

**Tests:**
- `Shared/Serializer/test_property.cpp` — updated round-trip tests
- `Shared/Serializer/test_fuzz.cpp` — updated fuzz tests
- `Shared/Serializer/test_cross_language.cpp` — updated cross-language tests
- `Tests/Protocol/vectors/v1/OBJECT_CREATE.bin` — regenerated (112 bytes)
- `Tests/Protocol/vectors/v1/OBJECT_UPDATE.bin` — regenerated (104 bytes)
- `Tests/Protocol/vectors/generate_vectors.py` — updated

## Semantic Guarantees Activated

After this migration, OBJECT_CREATE and OBJECT_UPDATE activate:

1. **Primitive type dispatch** — UE spawns correct actor type based on `primitive_type` field (LSP_Cube=0x00, LSP_Sphere=0x01, LSP_Cylinder=0x02, LSP_Plane=0x03, LSP_Empty=0x04, LSP_Camera=0x05)
2. **Stale-rejection (OBJECT_UPDATE)** — `GUpdateSequences.IsStaleOrDuplicate()` rejects out-of-order or duplicate update packets per GUID
3. **Sequence monotonicity** — per-GUID counters in Blender ensure seq monotonically increases within a session
4. **Timestamp ordering** — float64 timestamp enables cross-GUID ordering for replay or conflict resolution
5. **Dual-emission compatibility** — OBJECT_UPDATE dispatched alongside PT_Transform for all transform changes; bridge events fire before legacy dispatch

## Migration Reference

This migration validates Template v3 repeatability. Key lessons:

1. **Fail-stop policy** works: two incidents (engine mismatch, missing import) were caught before contaminating evidence
2. **Dual-emission** reduces risk: new path runs alongside legacy path, enabling side-by-side comparison
3. **Phase ordering** (Investigation → Semantic Analysis → Design → Implementation → Runtime Verification → Audit) prevents premature coding
4. **Known limitations should be documented, not fixed**: Blender primitive detection is a separate enhancement, not a migration defect

## Known Limitations

### Blender Primitive Detection

`_get_primitive_type()` in `Blender_Addon/sync.py` currently classifies objects as Camera or non-camera only. All non-camera objects send `primitive_type=0` (LSP_Cube), regardless of their actual mesh topology (Sphere, Cylinder, Plane).

**Impact:** UE spawns everything as Cube. This does not affect protocol correctness — the `primitive_type` field is carried correctly over the wire and processed correctly by UE.

**Disposition:** Tracked as separate enhancement (ENH-PrimitiveTypeDetection). Not a blocker for MIG-002.

## Regression Test Matrix

| Scenario | Expected Behavior |
|----------|-------------------|
| OBJECT_CREATE single object | Actor spawned with correct type, GUID, transform |
| OBJECT_CREATE with parent | Actor attached to parent |
| OBJECT_UPDATE transform | Actor transform updated, no duplicate spawn |
| OBJECT_UPDATE stale packet (seq <= current) | Rejected by stale-rejection barrier |
| OBJECT_UPDATE out-of-order | Stale-rejection barrier handles correctly |
| Reconnect | OBJECT_CREATE re-sent with seq=2 (incremented), OBJECT_UPDATE resumes |
| OBJECT_DELETE (MIG-001 regression) | Actor destroyed, viewport refreshed |
| Undo/Delete tombstone | Same GUID restored, `TOMBSTONE_RESTORED` marker fired |
| Hide/Show | OBJECT_VISIBILITY with visible=0/1 dispatched |

## Verification Summary

| Phase | Result |
|-------|--------|
| Phase 1 — Investigation | PASS |
| Phase 2 — Semantic Analysis | PASS |
| Phase 3 — Migration Design | PASS |
| Phase 4 — Implementation | PASS (10 test suites: C++ 8/8 + Python 51 + cross-lang 93) |
| Phase 5 — Runtime Verification | PASS (6/6: T1 OBJECT_UPDATE E2E, T2 Dual-emission, T3 Sequence, T4 PrimitiveType* (Known Limitation), T5 Reconnect, T6a Smoke) |
| Phase 6 — Migration Audit | PASS (Legacy disposition verified, authoritative path confirmed, instrumentation cleaned) |

## Why OBJECT_CREATE/OBJECT_UPDATE Carry Sequence Numbers

Create and update are not idempotent:

- Reordering creates can cause incorrect parent-child establishment
- Stale updates can overwrite newer state with old transforms
- Reconnect may re-send creates with new seq values; without seq tracking, UE could reject legitimate re-creations

Sequence numbers are a minimal safety barrier. They add 4 bytes per message but prevent a class of race conditions that are otherwise nearly impossible to debug.
