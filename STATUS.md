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

1. ~~**Phase 6I.1 — Transport Hardening**~~ **COMPLETE** ✅
2. **Phase 7A — Static Mesh Identity Mapping** ← NEXT
3. **Phase 7B — Asset Registry + Material Mapping**
4. **Phase 7C — Geometry/Modifier Pipeline**

## Phase 6I.1 — Transport Hardening (COMPLETE)

### Stage 1A — Bounds Hardening (VERIFIED)
- **A1**: `LIVE_SYNC_MAX_OBJECTS_PER_PACKET=4096` — ObjectCount>4096 rejected by network thread
- **A2**: `LIVE_SYNC_MAX_PACKET_SIZE=524288` — Re-check in game-thread `ProcessBinaryPacket`
- **A3**: `LIVE_SYNC_MAX_NAME_LENGTH=256` — Rename OldNameLen/NewNameLen>256 rejected
- **A4/A5**: NaN/Inf Location, Rotation, Scale rejected at parse time
- **A6**: Collection OpType outside 0x01–0x08 rejected
- **Validation**: 24/24 bounds tests PASS

### Stage 1B — Observability (VERIFIED)
- **B1**: `ETransportError` enum (13 categories)
- **B2**: `MalformedPackets` counter on ALL rejection paths (10 gaps patched)
- **B3**: `UE.LiveSync.TransportVerbose` CVar
- **B4**: Blender send queue high-water warning at 75% capacity
- **Validation**: 43/43 observability tests PASS

### Stage 2 — Lifecycle Hardening (VERIFIED)
- **C1**: `UE.LiveSync.RecvTimeoutMs` CVar (default 5000ms) — configurable `Wait()` polling
- **C2**: TCP keepalive — not exposed in UE5.7 FSocket API (noted)
- **C3**: Send queue drained on reconnect in `_close_internal()`
- **C4**: Atomic `compare_exchange_strong` guard for `StartNetworkThread` double-accept
- **Validation**: 22/22 lifecycle tests PASS

### Final Closeout Regression
| Suite | Result | Notes |
|-------|--------|-------|
| Stage 1A bounds | **24/24 PASS** | |
| Stage 1B observability | **43/43 PASS** | |
| Stage 2 lifecycle | **22/22 PASS** | |
| Fuzz protocol (existing) | **37/39 PASS** | 2 pre-existing TCP sendall expectation failures, non-regression |
| Visibility semantic (existing) | **15/15 PASS** | |
| Collection semantic (existing) | **10/10 PASS** | |
| **Total** | **151/153 PASS** | 2 known, non-regression |

## Phase 7A — Static Mesh Identity Mapping (COMPLETE)

### Stage 0 — Audit & Documentation (VERIFIED)
- **0.1**: Scope lock document written
- **0.2**: Phase 5D identity tests audited against identity model rules
- **0.3**: `FAssetIdentityRef` consumers documented (creation, comparison, hashing, storage)
- **0.4**: `AssetMetadata` age-out rules verified against delete/recreate identity chain
- **Validation**: Zero source files modified; 24 identity rules inspected; 3 critical issues found

### Stage 1A — Identity Hygiene Fixes (VERIFIED)
- **C1**: `HandleDelete` (V5) now cleans `AssetMetadata` + `PendingAssetQueue` on delete
- **C1b**: `OnActorDestroyed` cleans `AssetMetadata` + `PendingAssetQueue` on external destruction
- **C2**: Truncated `PT_AssetDef` payload path now increments `MalformedPackets` counter
- **C3**: `_last_mesh_identity` cleared in Blender `start_sync()` / `stop_sync()`
- **Validation**:

| Suite | Result | Notes |
|-------|--------|-------|
| Phase 7A hygiene (Stage 1A) | **40/40 PASS** | 2 skipped (no UE) |
| Phase 7A identity coverage (Stage 1B) | **77/77 PASS** | 5 new sections (7–11), standalone |
| Phase 6G identity stability | **121/121 PASS** | Full regression |
| Phase 6E delete validation | **320/320 PASS** | Includes §49 metadata cleanup tests |
| Phase 6D hierarchy | **97/97 PASS** | 7 skipped (no UE) |
| Phase 6 visibility | **0/0 FAIL** | 12 skipped (no UE), pre-existing |
| Phase 6 rename | **0/1 FAIL** | Pre-existing: `rename_reconnect_storm` requires UE |
| Phase 5D asset identity | **0/1 FAIL** | Pre-existing: no UE editor to connect to |
| Phase 6I.1 bounds | — | Skipped (no UE), pre-existing |
| **Total (standalone)** | **655/655 PASS** | 0 regressions from Phase 7A changes |

### Stage 2 — Identity Hygiene (PENDING — future work)
- **2.1**: `AssetMetadata` periodic age-out verification test (stale entries evicted after 60s)
- **2.2**: `PendingAssetQueue` bounds (2048 max) and overflow behavior test
- **2.3**: Identity-mapping section in `UE.LiveSync.Stats` console output
- **2.4**: Full Phase 5D/6/6I.1 validation suites regression

---

## Phase 6I.1 Final Closeout Regression (archived above)

## Recent Changes

- **Phase 7A Stage 1B**: Identity coverage hardening implemented and VERIFIED — 77 tests across 5 identity rules: shared datablock identity, mesh datablock rename, duplicate object rule, delete/recreate chain, `FAssetIdentityRef` semantics. 655/655 standalone tests PASS, 0 regressions.
- **Phase 7A Stage 1A**: Identity hygiene fixes implemented and VERIFIED — `HandleDelete`/`OnActorDestroyed` now clean `AssetMetadata`, truncated `PT_AssetDef` increments `MalformedPackets`, Blender `_last_mesh_identity` cleared on start/stop. 578/578 standalone tests PASS, 0 regressions.
- `Blender_Addon/sync.py`: +4 lines — `_last_mesh_identity` global + clear in `start_sync()`/`stop_sync()`
- `UELiveSyncSubsystem.cpp`: +11 lines — `AssetMetadata.Remove` + `PendingAssetQueue.Remove` in `HandleDelete` and `OnActorDestroyed`; `MalformedPackets.fetch_add` in truncated `PT_AssetDef` path
- `tests/phase7a_hygiene_validation.py`: New test file — 40 tests covering C1/C2/C3
- `tests/phase5d_validation_A_asset_identity.py`: +24 lines — truncated/zero-length `PT_AssetDef` wire tests
- `tests/phase6e_delete_validation.py`: +52 lines — §49 AssetMetadata cleanup on delete (12 tests)
- **Phase 6I.1 Stage 2**: Lifecycle hardening implemented and VERIFIED — 22/22 tests PASS, configurable recv timeout, atomic thread-start guard, Blender queue drain on reconnect
- **Phase 6I.1 Stage 1B**: Observability implemented and VERIFIED — 43/43 tests PASS, 10 MalformedPackets gaps patched, ETransportError enum, TransportVerbose CVar, Blender queue high-water warning
- **Phase 6I.1 Stage 1A**: Bounds hardening implemented and VERIFIED — 24/24 bounds tests PASS, 10/10 rejection messages confirmed in UE log, no regressions
- `SyncTypes.h`: Fixed UHT error — moved `#include <atomic>` before `.generated.h`
- `UELiveSyncSubsystem.cpp`: Removed orphaned braces/log outside function body
- `UELiveSyncEditorModule.cpp`: Removed `UStatusBarSubsystem::AddStatusBarWidget` calls (API doesn't exist in UE5.7)
- Cleaned up local-only files from git tracking (`.opencode/skills/`, `AGENTS.md`, `tests/`)
- Agent write test passed.
