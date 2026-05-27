# Phase 6 — Invariant Checklist

> **Last verified**: 2026-05-27  
> **Status**: ALL VERIFIED ✅  
> **Context**: Phase 6 Stabilization Freeze Checkpoint (`36-phase6-stabilization-freeze-checkpoint.md`)

This is the canonical invariant checklist for Phase 6 semantic lanes.
Each invariant is a single-fact assertion verifiable by code inspection,
unit test, integration test, or runtime audit.

---

## A. Structural Invariants (Build-Time)

| ID | Invariant | Verification Method | Status |
|----|-----------|-------------------|--------|
| A-001 | Each semantic lane has exactly one packet type code | Code inspection | ✅ VERIFIED |
| A-002 | Each packet type code is unique and unused by another lane | Code inspection | ✅ VERIFIED |
| A-003 | Each lane's packet type appears in kValidTypes | Code inspection | ✅ VERIFIED |
| A-004 | Each lane's packet type is hashed in LIVE_SYNC_PROTOCOL_SIG | Code inspection | ✅ VERIFIED |
| A-005 | Each lane has its own sequence tracker type | Code inspection | ✅ VERIFIED |
| A-006 | Each sequence tracker is bounded at 2048 entries | Code inspection | ✅ VERIFIED |
| A-007 | Each lane has its own atomic counters in FLiveSyncStats | Code inspection | ✅ VERIFIED |
| A-008 | Each lane has its own CPU profiler scope(s) | Code inspection | ✅ VERIFIED |
| A-009 | Each lane has its own log prefix | Code inspection | ✅ VERIFIED |
| A-010 | Each lane has its own RAII suppression guard | Code inspection | ✅ VERIFIED |
| A-011 | No lane's data is stored in FSyncTransformState | Code inspection | ✅ VERIFIED |
| A-012 | No lane modifies frozen Tick pipeline ordering | Code inspection | ✅ VERIFIED |
| A-013 | No lane modifies FLiveSyncQueue | Code inspection | ✅ VERIFIED |
| A-014 | No lane modifies LiveSyncRunnable thread lifecycle | Code inspection | ✅ VERIFIED |
| A-015 | All freeze banners in frozen files remain intact | Code inspection | ✅ VERIFIED |
| A-016 | All packet payloads are fixed-size (except variable-length rename) | Code inspection | ✅ VERIFIED |
| A-017 | Suppression guards are scoped RAII — no manual Enter/Exit | Code inspection | ✅ VERIFIED |
| A-018 | Suppression guards are per-lane — no shared suppression state | Code inspection | ✅ VERIFIED |

## B. Runtime Invariants (Test-Time)

| ID | Invariant | Verification Method | Status |
|----|-----------|-------------------|--------|
| B-001 | Stale sequence rejection: seq ≤ last accepted → rejected | Unit test | ✅ VERIFIED |
| B-002 | Duplicate sequence rejection: same seq received twice → 2nd rejected | Unit test | ✅ VERIFIED |
| B-003 | Out-of-order acceptance: only strictly increasing seq accepted | Unit test | ✅ VERIFIED |
| B-004 | Cross-GUID isolation: same seq on different GUIDs not rejected | Unit test | ✅ VERIFIED |
| B-005 | Tracker cleared on StopNetworkThread | Unit test / audit | ✅ VERIFIED |
| B-006 | Tracker cleared on ConsoleReset | Unit test / audit | ✅ VERIFIED |
| B-007 | Snapshot replay uses EChangeOrigin::Replay | Code inspection | ✅ VERIFIED |
| B-008 | Deterministic replay: same input → same output | Integration test | ✅ VERIFIED |
| B-009 | Tombstone blocks all semantic operations on deleted GUID | Integration test | ✅ VERIFIED |
| B-010 | Tombstone blocks re-delete of same GUID | Integration test | ✅ VERIFIED |
| B-011 | Tombstone cleared on StopNetworkThread | Integration test | ✅ VERIFIED |
| B-012 | Tombstone cleared on ConsoleReset | Integration test | ✅ VERIFIED |
| B-013 | Delete evicts pending hierarchy entries for deleted GUID | Integration test | ✅ VERIFIED |
| B-014 | Malformed packet → error log + no crash | Fuzz test | ✅ VERIFIED |
| B-015 | Invalid packet type → error log + no crash | Fuzz test | ✅ VERIFIED |
| B-016 | Protocol version mismatch → error log + no crash | Fuzz test | ✅ VERIFIED |
| B-017 | Invalid magic → error log + no crash | Fuzz test | ✅ VERIFIED |
| B-018 | Packet boundary check failure → error log + no crash | Unit test | ✅ VERIFIED |

## C. Cross-Lane Invariants (Integration)

| ID | Invariant | Verification Method | Status |
|----|-----------|-------------------|--------|
| C-001 | Rename + Visibility on same object: both applied independently | Integration test | ✅ VERIFIED |
| C-002 | Rename + Hierarchy on same object: both applied independently | Integration test | ✅ VERIFIED |
| C-003 | Rename + Delete on same object: delete supersedes; tombstone blocks re-rename | Integration test | ✅ VERIFIED |
| C-004 | Visibility + Hierarchy on same object: both applied independently | Integration test | ✅ VERIFIED |
| C-005 | Visibility + Delete on same object: delete supersedes; tombstone blocks re-visibility | Integration test | ✅ VERIFIED |
| C-006 | Hierarchy + Delete on same object: delete evicts deferred hierarchy; tombstone blocks re-attach | Integration test | ✅ VERIFIED |
| C-007 | All 5 lanes (Transform, Rename, Visibility, Hierarchy, Delete) run simultaneously | Soak test | ✅ VERIFIED |
| C-008 | 4 reconnect cycles with 5-lane simultaneous traffic — no state corruption | Soak test | ✅ VERIFIED |
| C-009 | Delete during snapshot build deferred until EndSnapshot | Integration test | ✅ VERIFIED |
| C-010 | Hierarchy pending queue cleaned on delete of referenced GUID | Integration test | ✅ VERIFIED |
| C-011 | Tombstone persistence: tombstone survives across multiple semantic operations | Integration test | ✅ VERIFIED |
| C-012 | No tombstone persistence across reconnect | Integration test | ✅ VERIFIED |

## D. Observability Invariants

| ID | Invariant | Verification Method | Status |
|----|-----------|-------------------|--------|
| D-001 | All semantic lane counters appear in FLiveSyncStats | Code inspection | ✅ VERIFIED |
| D-002 | Counters use std::memory_order_relaxed | Code inspection | ✅ VERIFIED |
| D-003 | Counters are visible in UE.LiveSync.Stats | Code inspection | ✅ VERIFIED |
| D-004 | All counters are uint64 (no signed, no wrapping concern) | Code inspection | ✅ VERIFIED |
| D-005 | Profiler scopes exist for all packet processing paths | Code inspection | ✅ VERIFIED |
| D-006 | Profiler scopes exist for replay paths (ProcessRenames, ProcessVisibility, etc.) | Code inspection | ✅ VERIFIED |
| D-007 | Event histories bounded at 32 entries | Code inspection | ✅ VERIFIED |
| D-008 | DumpState outputs all lane tracker sizes | Code inspection | ✅ VERIFIED |

## E. Blender-Side Invariants

| ID | Invariant | Verification Method | Status |
|----|-----------|-------------------|--------|
| E-001 | Blender's _known_guids diff correctly detects new/adjusted/deleted objects | Unit test | ✅ VERIFIED |
| E-002 | Delete detection uses _deleted_guids_this_tick set diff | Unit test | ✅ VERIFIED |
| E-003 | Each semantic lane has its own per-GUID monotonic sequence counter | Code inspection | ✅ VERIFIED |
| E-004 | Network.py serializes each type with correct fixed payload | Code inspection | ✅ VERIFIED |
| E-005 | Rename serialization includes old name, new name, seq, ts | Code inspection | ✅ VERIFIED |
| E-006 | Visibility serialization includes bHidden, seq, ts | Code inspection | ✅ VERIFIED |
| E-007 | Hierarchy serialization includes child GUID, parent GUID, seq, ts | Code inspection | ✅ VERIFIED |
| E-008 | Delete serialization includes GUID, seq, ts (28 bytes) | Code inspection | ✅ VERIFIED |
| E-009 | Blender-side sequences are monotonic per-GUID | Unit test | ✅ VERIFIED |
| E-010 | Blender GUID cleanup occurs on delete | Unit test | ✅ VERIFIED |

---

## Summary

| Category | Total Invariants | Verified | Not Verified |
|----------|-----------------|----------|-------------|
| A. Structural (Build-Time) | 18 | 18 | 0 |
| B. Runtime (Test-Time) | 18 | 18 | 0 |
| C. Cross-Lane (Integration) | 12 | 12 | 0 |
| D. Observability | 8 | 8 | 0 |
| E. Blender-Side | 10 | 10 | 0 |
| **Total** | **66** | **66** | **0** |

**ALL 66 INVARIANTS VERIFIED** ✅ — Freeze checkpoint confirmed.
