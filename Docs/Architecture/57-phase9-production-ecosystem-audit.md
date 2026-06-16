# Phase 9 — Production Ecosystem Closeout Audit

**Date:** 2026-06-16
**Baseline:** `phase7-closeout-audit-stable`
**Audit Commit:** (created by this audit)

## Overview

Phase 9 was scoped to implement a production ecosystem including capability
negotiation, TCP discovery scan, crash recovery/resilience, stale session
cleanup, and diagnostics export. This audit compares the scope-lock document
claims against the current codebase to produce an accurate inventory of what
is actually implemented.

## Scope Lock Claims vs. Codebase Reality

The scope-lock documents at:
- `Docs/Architecture/48-phase9-production-ecosystem-scope-lock.md`
- `Docs/Architecture/49-phase9-stage3-autodiscovery-scope-lock.md`
- `Docs/Architecture/50-phase9-stage5-crash-recovery-scope-lock.md`

describe an architecture where almost all stages are marked COMPLETE.
Source audit reveals many were never implemented.

### Implemented Stages

| Stage | Component | Code Evidence |
|-------|-----------|---------------|
| Stage 2B | Capability constants | `network.py:480-484`: 5 flags (TIMELINE_SYNC, KEYFRAME_REPLICATION, ACTIVE_CAMERA_SYNC, SEQUENCER_OPS, CAMERA_DEF_SYNC) |
| Stage 2C | Capability announce (0x11) | `network.py:141-152`: `_send_announce()` packs `_local_capabilities` as uint32, called on connect |
| Stage 2D | Capability response (0x12) | `network.py:155-198`: `_try_recv_capability_response()` non-blocking recv, sets `_remote_capabilities` and `_capability_response_received` |
| Stage 2E | Effective gating | `network.py:112-123` (`is_timeline_effective`), `201-218` (`is_active_camera_effective`), `233-244` (`is_sequencer_ops_effective`), `269-307` (`is_keyframe_effective`) |
| Stage 2F | Compatibility matrix | `tests/phase9_stage2f_compat_matrix.py` (62 tests, rewritten for actual API) |
| Stage 5A | Reconnect infrastructure | `network.py:2762-2967`: `_connect_internal`, `_reconnect_internal`, exponential backoff, 30s timeout |
| Stage 5E | Stale session audit | `tests/phase9_stage5e_stale_session_cleanup.py` (22 tests, rewritten) |
| Stage 6B | Diagnostics | `sync.py:2580-2641`: `dump_diagnostics()` with Status/Objects/Network/Playback/Timeline/Active Camera/Health/Runtime Config sections |
| Stage 6B | Diagnostics UI | `__init__.py:384-394`: `UELIVESYNC_OT_dump_diagnostics` operator; `__init__.py:1299`: menu item |
| Stage 7C | Sequence ID validation | UE: SequenceId monotonic check in HandleKeyframe |

### UE-Side Implemented

| Feature | Code Evidence |
|---------|---------------|
| PT_CapabilityAnnounce handler | Inline in `ProcessBinaryPacket` (0x11): stores `RemoteCapabilities`, sends response |
| PT_CapabilityResponse handler | Inline in `ProcessBinaryPacket` (0x12): stores response flags |
| UE_LOCAL_CAPABILITIES | Constant sent in capability response |
| DumpStateToConsole | `UELiveSyncSubsystem_Diagnostics.inl:554+`: [DIAG] markers, counters, ingress health |
| IsIngressHealthy | `UELiveSyncSubsystem_Diagnostics.inl:489-546` |
| StopNetworkThread / StartNetworkThread | Full lifecycle management |
| [NET] stopping thread markers | Emitted during network teardown |

### Stages With No Code Evidence (scope-lock doc overstates)

| Stage | Claimed | Actual | Analysis |
|-------|---------|--------|----------|
| Stage 1 (various) | COMPLETE | **NOT IMPLEMENTED** | No backpressure ACK, no adaptive throttle, no mesh compression, no dirty-flag interest management (same as Phase 8 audit) |
| Stage 3A | COMPLETE | **NOT IMPLEMENTED** (original) → **IMPLEMENTED** (Stage 3B) | TCP discovery scan — `discover_servers()` implemented in `network.py`. Probes 127.0.0.1, localhost, configured host via TCP connect. Diagnostics markers: [DISCOVERY][START/PROBE/FOUND/MISS/DONE]. No UDP broadcast. |
| Stage 3B | COMPLETE | **REWRITTEN** for actual implementation | Discovery scan tests — rewritten to test `discover_servers()` with dummy TCP listener validation (46 tests) |
| Stage 3C | COMPLETE | **IMPLEMENTED** (Discovery Auto-fill / Connect UX) | `get_best_discovery_result()` / `apply_discovery_result()` helpers. "Use Discovered Server" and "Discover & Connect" operators. [DISCOVERY][APPLY/CONNECT] markers. 38 tests PASS with dummy TCP listener. `PASS_DISCOVERY_CONNECT_UX` |
| Stage 5B | COMPLETE | **NOT IMPLEMENTED** | Session change test file `phase9_stage5b_session_change.py` does NOT exist on disk |
| Stage 5C | COMPLETE | **NOT IMPLEMENTED** | State cleanup test file `phase9_stage5c_state_cleanup.py` does NOT exist on disk |
| Stage 5D | COMPLETE | **NOT IMPLEMENTED** | Reconnect UI test file `phase9_stage5d_reconnect_ui.py` does NOT exist on disk |
| Stage 6A | COMPLETE | **NOT IMPLEMENTED** | Support bundle export — zero code. No `export_support_bundle()` in Blender or UE |
| Stage 7A | COMPLETE | **NOT IMPLEMENTED** | Session GUID exchange — no `_session_guid`, no `_regenerate_session_guid()` in network.py |
| Stage 7B | COMPLETE | **NOT IMPLEMENTED** | Stale session heartbeat — UE `ClearStaleSessionState` function exists but no Blender-side heartbeat timeout wiring |

### Discrepancies Found

1. **Stage 2F test file** (`phase9_stage2f_compat_matrix.py`): Referenced nonexistent constants (`CAP_SUPPORTS_BACKPRESSURE_ACK`, `CAP_SUPPORTS_MESH_COMPRESSION`, `CAP_SUPPORTS_SESSION_GUID`, `CAP_SUPPORTS_DIRTY_ITERATION`) and nonexistent functions (`is_compression_effective`, `is_backpressure_effective`, `get_suggested_interval`). Would crash on import. **Fixed** — rewritten against actual API.

2. **Stage 3B test file** (`phase9_stage3b_discovery_scan.py`): Originally referenced nonexistent `discover_ues()`, etc. **Rewritten** — first to audit absence (22 tests), later to test actual `discover_servers()` implementation with dummy TCP listener (46 tests).

3. **Stage 5B/5C/5D test files**: Referenced in scope-lock doc and in `phase9_stage5e_stale_session_cleanup.py` but do NOT exist on disk. Stage 5E test **fixed** — removed false PASS references, documented as scope-lock doc mismatch.

4. **Stage 6B test file** (`phase9_stage6b_diagnostics_export.py`): Called nonexistent `network.export_support_bundle()`. Checked `plugin_version == "0.1.0"`. **Fixed** — rewritten to test actual `get_runtime_stats()`, `is_connected()`, `get_status_detail()`.

5. **`CAP_SUPPORTS_CAMERA_SEQ_BIND`**: Defined in `sync.py:155` but NOT in `network.py`. Not included in `_local_capabilities`. Not a regression for this audit (Phase 7E scope).

6. **Scope-lock document** (`48-phase9-production-ecosystem-scope-lock.md`): Lists stale capability constants and stages that were never implemented. Audit doc serves as the corrected record.

## Implementation Truth Table

| Feature | Blender | UE | Status |
|---------|---------|----|--------|
| CapabilityAnnounce (0x11) | `_send_announce()` | ProcessBinaryPacket handler | **IMPLEMENTED** |
| CapabilityResponse (0x12) | `_try_recv_capability_response()` | ProcessBinaryPacket handler | **IMPLEMENTED** |
| Effective gating | 4 gates (timeline, keyframe, active_camera, sequencer_ops) | N/A | **IMPLEMENTED** |
| Reconnect / backoff | `_reconnect_internal()` 0.5-10s exp backoff, 30s timeout | StopNetworkThread/StartNetworkThread | **IMPLEMENTED** |
| Diagnostics console | `dump_diagnostics()` 8 sections | DumpStateToConsole, [DIAG] markers | **IMPLEMENTED** |
| Discovery scan | NOT IMPLEMENTED | **IMPLEMENTED** (Stage 3B + Stage 3C) | `discover_servers()` TCP connect probe (46 tests). `get_best_discovery_result()` / `apply_discovery_result()` auto-fill helpers. "Use Discovered Server"/"Discover & Connect" operators (38 tests). `PASS_DISCOVERY_LOCALHOST_SCAN` + `PASS_DISCOVERY_CONNECT_UX` |
| Support bundle export | NOT IMPLEMENTED | NOT IMPLEMENTED | **ABSENT** |
| BackpressureACK (0x10) | NOT IMPLEMENTED | NOT IMPLEMENTED (not in kValidTypes) | **ABSENT** |
| Adaptive throttle | NOT IMPLEMENTED | NOT IMPLEMENTED | **ABSENT** |
| Mesh compression | NOT IMPLEMENTED | NOT IMPLEMENTED | **ABSENT** |
| Session GUID | NOT IMPLEMENTED | NOT IMPLEMENTED | **ABSENT** |
| Dirty-flag interest | NOT IMPLEMENTED | NOT IMPLEMENTED | **ABSENT** |

## Packet Registry

- `0x11` (PT_CapabilityAnnounce) — in `kValidTypes` (UE), defined in Blender
- `0x12` (PT_CapabilityResponse) — in `kValidTypes` (UE), defined in Blender
- `0x10` (PT_BackpressureAck) — **NOT** in `kValidTypes`
- `0x02` — **NOT** in `kValidTypes`; `PF_FullSnapshot` is a flag only

## Test Totals

| Test File | Tests | Status |
|-----------|-------|--------|
| `phase9_stage2f_compat_matrix.py` | 62 | ✅ ALL PASS |
| `phase9_stage3b_discovery_scan.py` | 46 | ✅ ALL PASS (dummy TCP listener) `PASS_DISCOVERY_LOCALHOST_SCAN` |
| `phase9_stage3c_discovery_connect_ux.py` | 38 | ✅ ALL PASS (dummy TCP listener) `PASS_DISCOVERY_CONNECT_UX` |
| `phase9_stage5e_stale_session_cleanup.py` | 22 | ✅ ALL PASS |
| `phase9_stage6b_diagnostics_export.py` | 32 | ✅ ALL PASS |
| `phase9_production_ecosystem_audit.py` | 71 | ✅ ALL PASS |
| **Total** | **247** | **✅ ALL PASS** |

## Runtime Classification

**PASS_PHASE9_AUDIT_ONLY** — no live UE instance available for end-to-end
capability announce/response validation. All Blender-side source-text
invariants verified (71 tests). UE-side behavior verified by code review
(ProcessBinaryPacket inline handlers, DumpStateToConsole, reconnect
lifecycle). If runtime announce/response logs are later validated against
a live UE instance, reclassify to **PASS_PHASE9_RUNTIME_CAPABILITY**.

## Audit Tags

- `phase9-audit-stable`

## Files Changed

### Rewritten test files (4):
- `tests/phase9_stage2f_compat_matrix.py`
- `tests/phase9_stage3b_discovery_scan.py`
- `tests/phase9_stage5e_stale_session_cleanup.py`
- `tests/phase9_stage6b_diagnostics_export.py`

### New test file (1):
- `tests/phase9_production_ecosystem_audit.py`

### New audit doc (1):
- `Docs/Architecture/57-phase9-production-ecosystem-audit.md`
