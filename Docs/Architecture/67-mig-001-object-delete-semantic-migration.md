# MIG-001: OBJECT_DELETE Semantic Migration

## Status: COMPLETE

## Summary

Migrated OBJECT_DELETE (0x22) from a 16-byte identity-only payload to a 28-byte semantic payload carrying `sequence_number` + `timestamp`. This activates the full Phase 6E delete semantics (stale-rejection, tombstone gate, child-detach cascade) that were implemented but unreachable from the MsgType bridge.

## Problem

OBJECT_DELETE was originally specified with a 16-byte body (persistent_id only) during Phase 1.3.2 MsgType design. Phase 6E later implemented `HandleDelete(Guid, SequenceNumber, Timestamp, Origin)` with three safety barriers (sequence check, tombstone gate, child-detach cascade). However, the bridge's `OnObjectDelete` called the simpler `HandleDeleteObject(Guid)` — bypassing all Phase 6E guarantees.

## Root Cause

The OBJECT_DELETE spec was frozen at 16 bytes before Phase 6E was fully integrated. The bridge was wired to `HandleDeleteObject` (the legacy V3 handler) instead of `HandleDelete` (the Phase 6E semantic handler). This was a bridge migration gap, not an intentional design decision.

## Solution

Extended OBJECT_DELETE body to carry `sequence_number` (uint32) + `timestamp` (float64), matching the V5 wire semantics. Updated the bridge to route to `HandleDelete`, activating all Phase 6E guarantees.

### Wire format change

| Field | Before | After |
|-------|--------|-------|
| persistent_id | UUID (16 bytes) | UUID (16 bytes) |
| sequence_number | — | uint32 LE (4 bytes) |
| timestamp | — | float64 LE (8 bytes) |
| **Body total** | **16 bytes** | **28 bytes** |
| **Frame total** | **34 bytes** | **46 bytes** |

### Files changed

**Protocol:**
- `Shared/Protocol/MessageTypes.yaml` — OBJECT_DELETE body extended
- `Shared/Serializer/livesync_serializer.h` — added `pack_float64`
- `Shared/Serializer/livesync_deserializer.h` — added `unpack_float64`, updated `deserialize_body_object_delete`
- `Shared/Serializer/livesync_messages.h` — updated `serialize_body_object_delete`

**UE Plugin:**
- `UE_Plugin/.../LiveSyncViews.h` — `ObjectDeleteView` gained `SequenceNumber` + `Timestamp`
- `UE_Plugin/.../LiveSyncProtocolBridge.h` — `BuildObjectDeleteView` extracts seq+ts
- `UE_Plugin/.../UELiveSyncSubsystem.cpp` — `OnObjectDelete` routes to `HandleDelete`

**Blender Addon:**
- `Blender_Addon/msg_transport.py` — added `pack_f64`
- `Blender_Addon/object_protocol.py` — `build_object_delete` serializes seq+ts, per-GUID sequence counter
- `Blender_Addon/sync.py` — delete flow uses `MsgType.OBJECT_DELETE` via `transport.send_msg()`

**Tests:**
- `Tests/Protocol/vectors/generate_vectors.py` — OBJECT_DELETE vector updated
- `Tests/Protocol/serializer/serializer.py` — added `float64` support
- `Shared/Serializer/test_property.cpp` — updated round-trip test
- `Shared/Serializer/test_fuzz.cpp` — added truncated body tests
- `Shared/Serializer/test_cross_language.cpp` — updated serializer call
- Test vectors regenerated (OBJECT_DELETE.bin: 34→46 bytes)

## Semantic Guarantees Activated

After this migration, OBJECT_DELETE activates:

1. **Stale-rejection** — `GDeleteSequences.IsStaleOrDuplicate()` rejects out-of-order or duplicate deletes
2. **Tombstone gate** — `GDeleteTombstoneMap` prevents re-creation of recently deleted objects
3. **Child-detach cascade** — all children of deleted parent are detached to root before destruction
4. **Sequence tracking** — `GDeleteSequences.Update()` maintains per-GUID sequence state

## Why OBJECT_DELETE Is Not 16 Bytes

The 16-byte payload was designed before Phase 6E semantics were integrated. Delete is semantically different from Rename or Visibility:

- **Rename/Visibility**: idempotent — applying twice has no side effect
- **Delete**: destructive — duplicate delete of an already-destroyed actor can crash or cause silent failures

Sequence numbers enable stale-rejection (out-of-order packets). Timestamps enable ordering guarantees. These are not optional optimizations — they are safety barriers that prevent race conditions in concurrent delete scenarios.

## Migration Reference

This migration is the reference pattern for Phase 1.5 (Legacy Protocol Elimination). Each MIG item should:

1. Identify semantic guarantees in the legacy PT_* path
2. Verify the MsgType equivalent carries the same or better guarantees
3. Wire the bridge to the correct semantic handler
4. Regenerate test vectors
5. Document why the payload is structured as it is

## Regression Test Matrix

| Scenario | Expected Behavior |
|----------|-------------------|
| Delete single object | Actor destroyed, caches cleaned |
| Delete parent with children | Children detached to root, parent destroyed |
| Duplicate delete (same GUID, same seq) | Rejected by stale-rejection barrier |
| Delete then recreate same GUID | Tombstone gate prevents immediate re-creation |
| Out-of-order delete packets | Stale-rejection barrier handles correctly |
