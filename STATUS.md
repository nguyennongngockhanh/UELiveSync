# UE Live Sync — Status

**Version** 0.1.0 — Two-component real-time sync system (Blender ↔ UE5).

## Components

| Side | Language | Role |
|------|----------|------|
| `Blender_Addon/` | Python (bpy) | Scene iteration, diff detection, binary serialization |
| `UE_Plugin/UELiveSync/` | C++ (UE5.7) | Game-thread transform interpolation, network receiver |

## Architecture

- **Protocol**: V3 binary TCP, magic `0x4C56534D`, 24-byte header, little-endian
- **Transport**: Ordered reliable TCP, no reassembly layer
- **Object identity**: GUID-based (`obj["ue_guid"]`), collision-safe via `ensure_unique_guid()`
- **Threading**: Blender main thread serializes; background daemon sends. UE network thread enqueues; game thread interpolates.
- **Queue**: Bounded 128-entry MPSC (drop-oldest on overflow)
- **Heartbeat**: 5s Blender → 15s UE timeout

## Files

| Blender | Lines | UE Plugin | Lines |
|---------|-------|-----------|-------|
| `__init__.py` | 447 | `UELiveSyncSubsystem.cpp/h` | 3128 |
| `sync.py` | core sync | `LiveSyncRunnable.cpp/h` | network thread |
| `network.py` | TCP/serialization | `LiveSyncQueue.h` | MPSC buffer |
| | | `SyncTypes.h` | structs/protocol |
| | | `UELiveSyncEditor/` | status widget |

## Phase Progress

- **Phase 3.4–3.5**: Performance, stabilization, protocol cleanup (completed)
- **Phase 3.6**: Robustness & validation tests (completed)
- **Phase 4** (completed):
  - **4A Stability Core** — watchdog, reconnect, heartbeat hardening
  - **4B Refinements** — adaptive interpolation, diagnostics
  - **4C Editor Tooling** — status widget, stats
  - **4D Validation** — stress tests, edge cases
- **Phase 5** (completed):
  - **5A–5E** — Protocol evolution, asset identity, stress testing, observability
- **Phase 6** (in progress):
  - **Rename** (0x0C) — STABILIZED
  - **Visibility** (0x0B) — 15/15 PASS
  - **Collection** (0x0F) — IMPLEMENTED: 10/10 PASS, parser, handler, sequence tracking, replay recording, ConsoleReset lifecycle
  - **Hierarchy** (0x0D) — IN PROGRESS

## Current Roadmap

1. **Phase 6I.1 — Transport Hardening** (Stage 1A: VERIFIED · Stage 1B: VERIFIED · Stage 2: VERIFIED)
2. **Phase 7A — Static Mesh Identity Mapping**
3. **Phase 7B — Asset Registry + Material Mapping**
4. **Phase 7C — Geometry/Modifier Pipeline**

## Stage 1A — Bounds Hardening (VERIFIED)

- **A1**: `LIVE_SYNC_MAX_OBJECTS_PER_PACKET=4096` — ObjectCount>4096 rejected by network thread
- **A2**: `LIVE_SYNC_MAX_PACKET_SIZE=524288` — Re-check in game-thread `ProcessBinaryPacket`
- **A3**: `LIVE_SYNC_MAX_NAME_LENGTH=256` — Rename OldNameLen/NewNameLen>256 rejected
- **A4/A5**: NaN/Inf Location, Rotation, Scale rejected at parse time
- **A6**: Collection OpType outside 0x01–0x08 rejected
- **Validation**: 24/24 bounds tests PASS, 37/39 fuzz tests PASS (2 pre-existing TCP sendall expectation failures, non-regression)
- **UE log**: All 10 bounds rejection messages confirmed. No crash/assert/check failure.

## Stage 1B — Observability (VERIFIED)

- **B1**: `ETransportError` enum added to `SyncTypes.h` (13 error categories)
- **B2**: `MalformedPackets` counter now increments on ALL rejection paths (10 newly patched gaps: truncated header, magic mismatch, V3/V2 header truncation, V3/V2 size violation, unsupported version, invalid type, invalid flags, unknown type)
- **B3**: `UE.LiveSync.TransportVerbose` CVar declared + wired into CVar refresh
- **B4**: Blender send queue high-water warning at 75% capacity (192/256)
- **Validation**: 43/43 observability tests PASS, 24/24 bounds tests PASS (no regression), 37/39 fuzz tests PASS (same 2 pre-existing TCP sendall failures)
- **UE log**: All newly-patched paths confirmed to produce log output. No crash/assert/check failure.

## Stage 2 — Lifecycle Hardening (VERIFIED)

- **C1**: Socket recv timeout — `UE.LiveSync.RecvTimeoutMs` CVar (default 5000ms), configurable  `Wait()` polling in network thread
- **C2**: TCP keepalive — not exposed in UE5.7 FSocket API; skipped (noted in scope doc)
- **C3**: Blender send queue drained on reconnect — stale packets cleared from `_send_queue` in `_close_internal()`
- **C4**: `StartNetworkThread` double-accept guard — atomic `compare_exchange_strong` prevents concurrent entry; socket preserved across restart
- **Validation**: 22/22 lifecycle tests PASS, 24/24 Stage 1A PASS, 43/43 Stage 1B PASS, 37/39 fuzz PASS (no regressions)
- **UE log**: 17 connection events handled without crash. Guard not triggered (normal sequential connection pattern).

## Recent Changes

- **Phase 6I.1 Stage 2**: Lifecycle hardening implemented and VERIFIED — 22/22 tests PASS, configurable recv timeout, atomic thread-start guard, Blender queue drain on reconnect
- **Phase 6I.1 Stage 1B**: Observability implemented and VERIFIED — 43/43 tests PASS, 10 MalformedPackets gaps patched, ETransportError enum, TransportVerbose CVar, Blender queue high-water warning
- **Phase 6I.1 Stage 1A**: Bounds hardening implemented and VERIFIED — 24/24 bounds tests PASS, 10/10 rejection messages confirmed in UE log, no regressions
- `SyncTypes.h`: Fixed UHT error — moved `#include <atomic>` before `.generated.h`
- `UELiveSyncSubsystem.cpp`: Removed orphaned braces/log outside function body
- `UELiveSyncEditorModule.cpp`: Removed `UStatusBarSubsystem::AddStatusBarWidget` calls (API doesn't exist in UE5.7)
- Cleaned up local-only files from git tracking (`.opencode/skills/`, `AGENTS.md`, `tests/`)
- Agent write test passed.
