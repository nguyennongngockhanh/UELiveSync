# Phase 5A — Workflow & Protocol Hardening

**Date**: 2026-05-23
**Tag**: `phase5A-stable`
**Protocol Version**: V4 (wire), V3 backward compatible

---

## Implemented Systems

### A1 — Actor Creation UI
- `default_primitive` EnumProperty in addon preferences (Cube/Sphere/Cylinder/Plane/Empty)
- Sidebar panel dropdown under "Actor Spawn Settings"
- UE `HandleCreateObject()` dispatches to five primitive types via `/Engine/BasicShapes/`
- Empty type (0x04) spawns root-only actor with no mesh component

### A2 — GUID Hardening
- `_compute_owner_hash()` uses `hashlib.sha256` of `{obj.name}|{obj.data.name}` for deterministic object identity
- `_reconcile_guids_on_load()` detects stale/dangling GUIDs and regenerates on hash mismatch
- `ensure_unique_guid()` detects in-memory collisions (e.g., `obj.copy()`)
- Owner hash stored as `obj["ue_guid_owner_hash"]` custom property

### A3 — Snapshot Batching
- `PT_BeginSnapshot` (0x09) enters accumulation mode
- `PT_EndSnapshot` (0x0A) flushes deferred hierarchy and resumes interpolation
- During snapshot build: transforms are not interpolated, deletes are deferred
- Auto-abort on timeout (5s) or disconnect

### A4 — Missing Actor Recovery
- Frame-count escalation: warn at 10 frames, respawn at 30 frames, evict at 60 frames
- Max 3 recovery attempts with per-attempt logging
- Deferred attachment retry queue with fast window (10 frames) then throttled (every 5th tick)
- 60-frame / 5s timeout for orphan attachments

### A5 — Primitive Enum Validation
- Unknown values (> PRIMITIVE_Empty) default to Cube with warning log
- Version-aware parsing: byte only read for V4+ packets

### A6 — Pending Attachment Cleanup
- `HandleDeleteObject()` removes matching `PendingAttachments` and `MissingActorTracker` entries
- `StopNetworkThread()` and `ConsoleReset()` fully clear all deferred/snapshot state
- No stale retries or infinite pending queue growth

---

## Protocol Additions

| Addition | Type | Value |
|----------|------|-------|
| PrimitiveType byte | CREATE-only payload field | 1 byte after parent GUID |
| PRIMITIVE_Cube | enum | 0x00 |
| PRIMITIVE_Sphere | enum | 0x01 |
| PRIMITIVE_Cylinder | enum | 0x02 |
| PRIMITIVE_Plane | enum | 0x03 |
| PRIMITIVE_Empty | enum | 0x04 |
| PT_BeginSnapshot | packet type | 0x09 |
| PT_EndSnapshot | packet type | 0x0A |
| LIVE_SYNC_VERSION_V4 | wire version | 4 |

V3 CREATE: 80 bytes (no primitive byte) — G+L+R+S+T+P
V4 CREATE: 81 bytes — G+L+R+S+T+P+Prim

---

## Validation Coverage

- `tests/phase5_validation_A_workflow.py` (29 tests):
  - 19 Blender-side: owner hash determinism, primitive mapping, serialization format, GUID collision detection
  - 10 UE-side sections: primitive spawn, invalid enum, snapshot batching, snapshot abort, deferred attachment, orphan timeout, missing actor recovery, disconnect-in-snapshot, protocol validation, delete-in-snapshot

---

## Major Bug Fixes

| Bug | Impact | Fix |
|-----|--------|-----|
| `_compute_owner_hash()` contained `uuid.uuid4()` | Every GUID regenerated on every `_reconcile_guids_on_load()` | Removed random component for deterministic hash |
| No snapshot timeout guard | `bInSnapshotBuild` stuck forever if EndSnapshot never arrives | Added `SnapshotStartTime` + 5s auto-abort |
| Invalid primitive byte (0xFF) treated as Empty | Silent wrong actor type spawned | Guard clamped to PRIMITIVE_Cube with warning |
| PendingAttachments never cleaned up on delete/disconnect | Stale retries, unbounded growth | `RemoveAll` in HandleDeleteObject, `Empty` in StopNetworkThread |
| Missing actor recovery would retry infinitely | Log spam and wasteful respawn cycles | Added `RecoveryAttempts` counter capped at 3 |
| Primitive byte read unconditionally on V3 packets | Corrupt parsing if missing byte | Guarded with `Version >= LIVE_SYNC_VERSION_V4` |

---

## Architectural Decisions

| Decision | Rationale |
|----------|-----------|
| 1-byte enum (not string path) for primitive type | Minimal overhead, no hot-path string allocation |
| SHA-256 ([...:16]) for owner hashing | Deterministic, collision-resistant, survives Blender restart |
| PT_BeginSnapshot/PT_EndSnapshot as marker types (no payload) | Simple state machine; no payload parsing needed |
| Last-writer-wins connection model | Simple, single-socket; multi-connection deferred to Phase 5E |
| V3 backward compatibility via version-check | Phase 5F will formalize version negotiation |
| Deferred attachment retry with fast-window + throttle | Balances responsiveness with CPU budget |

---

## Known Deferred Items

- ACK handshake and Blender receive thread → Phase 6
- Mesh asset path streaming → Phase 5D
- Multi-connection architecture → Phase 5E
- Protocol version negotiation → Phase 5F
- Deterministic tick simulation → Future Backlog
- Packet capture/replay → Future Backlog
- Skeletal animation sync → Future Backlog
