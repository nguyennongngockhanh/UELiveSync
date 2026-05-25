# Phase 5E — Stress Testing & Observability

**Canonical Phase**: Late Phase 5 (Protocol Evolution & Runtime Stabilization)

## Overview

Phase 5E hardened the runtime system against regressions, added profiling
readiness, and validated long-term stability under sustained load. This is
NOT Phase 6 (Live Editing) or Phase 7 (Animation Sync) — it is the final
stabilization subphase of Phase 5 before live-editing workflows begin.

All changes are additive protection, observability, and testing — no
architecture redesign was performed.

## Roadmap Clarification

### Why this work belongs to Phase 5

The canonical roadmap defines:

- **Phase 5**: Protocol Evolution & Runtime Stabilization
- **Phase 6**: Live Editing System (create/delete, rename, duplicates,
  collections, visibility)
- **Phase 7**: Animation & Sequencer Sync

The following work items all fit within Phase 5 because they concern
the **transport, parsing, and runtime pipeline** — not editor-side
live-editing workflows:

| Work Item | Rationale |
|---|---|
| Protocol hardening | Core transport reliability — prerequisite for all later phases |
| Reconnect handling | Runtime pipeline robustness — not editor workflow |
| Fuzz testing | Protocol parsing safety — transport layer |
| Runtime freeze fixes | Game-thread pipeline stability |
| Stress infrastructure | Validates existing pipeline under load |
| Observability/profiling | Debugging infrastructure for all phases |
| Queue safety | Backpressure management — transport layer |
| Hierarchy validation | Existing feature hardening, not new feature |

### When does Phase 6 begin?

Phase 6 begins when **full editor-side live editing workflows** start
implementation. Specifically:

- Rename replication (Blender → UE object name sync)
- Collection/folder structure sync
- Visibility/hidden state sync
- Duplicate detection and handling
- Object create/delete lifecycle management from editor

Until those features are actively implemented, all protocol and pipeline
work remains in Phase 5.

## Current Status

**Late Phase 5 — Protocol Evolution & Runtime Stabilization**

### Completed (Phase 5A–5E)

| Subphase | Work |
|---|---|
| 5A | Snapshot foundations |
| 5B | Protocol compatibility (V3/V4/V5, backward compat) |
| 5C | Protocol hardening & fuzzing |
| 5D | Runtime freeze fixes, reconnect isolation |
| 5E | Stress testing, observability, profiling, queue/hierarchy safety |

### Not Started Yet

- **Phase 6**: Rename replication, collection sync, visibility sync
- **Phase 7**: Animation sync, timeline sync, sequencer integration
- **Phase 8**: High-performance streaming, compression, interest management
- **Phase 9**: Installer, auto-discovery, version compatibility, UI polish

## Changes Summary

### 1. Tick Pipeline Regression Protection

**Files**: `UELiveSyncSubsystem.cpp`

A large warning comment was added above the Tick pipeline stages to
prevent accidental reordering or removal:

```
// CRITICAL:
// The network thread ONLY enqueues packets.
// ALL runtime processing occurs in the Tick pipeline below.
// Removing or bypassing these stages will stall the entire sync system.
```

The entire pipeline is wrapped in `TRACE_CPUPROFILER_EVENT_SCOPE`
scopes for Unreal Insights profiling:

| Scope Name | Coverage |
|---|---|
| `UELiveSync_TickPipeline` | Entire pipeline block |
| `UELiveSync_ProcessQueuedPackets` | Packet dequeue + binary parse |
| `UELiveSync_InterpolateTransforms` | All transform interpolation |
| `UELiveSync_ResolvePendingAttachments` | Deferred attachment retry |
| `UELiveSync_RecoverMissingActors` | Missing actor re-spawn |
| `UELiveSync_ResolvePendingAssets` | Late mesh resolution |
| `UELiveSync_ProcessBinaryPacket` | Per-packet binary parsing |
| `UELiveSync_UpdateTargetTransform` | Transform state update |
| `UELiveSync_HandleCreateObject` | Actor spawn + component register |
| `UELiveSync_ValidateHierarchy` | Hierarchy safety scan |

### 2. Unreal Insights Profiling Support

Added `#include "ProfilingDebugging/Trace.h"` and
`TRACE_CPUPROFILER_EVENT_SCOPE` to every pipeline function entry point.
Allows frame-by-frame CPU profiling in Unreal Insights for:
- Per-stage timing breakdown
- Stall detection (5s freeze guard augmented with trace context)
- Hot-spot identification under heavy scenes

### 3. Hierarchy Safety Validation

New `ValidateHierarchy()` method runs every ~300 ticks (~5s) when
connected. Validates:
- **Self-parenting**: object is its own parent → detach + warning
- **Invalid parent GUID**: empty or zeroed parent → detach + warning
- **Circular chains**: parent ancestry contains child → detach + error
- **Excessive depth**: >128 levels → detach + error

All violations log the offending GUID chain and abort the attachment
safely without risk of recursion/stall.

### 4. Queue Safety Guards

Added to `TickSafetyMonitors()`:

- **Packet Age Watchdog**: estimates age of oldest queued packet based on
  queue depth × (avg process time + 16ms frame budget). Warns at
  `PacketAgeWarnThreshold` (5s), flushes queue at `PacketAgeHardLimit`
  (30s) to prevent backlog stall.
- **Queue Depth Spike Warning**: logs warning when queue exceeds 90%
  capacity (115/128), indicating Tick pipeline saturation.
- **Log cooldown**: rate-limited to 1 message per 10s / 5s respectively.

### 5. Runtime Metrics Dashboard

New `LogRuntimeMetricsVerbose()` method logs a compact dashboard every
30s when `UE.LiveSync.Verbose` is enabled:

```
=== LiveSync Stats Dashboard ===
  Connection: Connected | Objects: 150 | Queue: 3/128 | Assets: 0
  Packets: 45000 recv / 44980 proc / 20 drop / 0 malformed
  Rates: 150 pkt/s (peak 420) | 0.03 ms/pkt (peak 0.15) | 12000 B/s
  Safety: FloodW=0 QPress=0 Reconn=2 Overflows=1
=== End Dashboard ===
```

### 6. Validation Test Suites

Three new stress tests in `tests/`:

#### `phase5e_stress_long_duration.py`
- 30+ minute continuous sync
- Continuous transform updates (every ~0.5s)
- Periodic object creation/deletion cycles (every ~10s)
- Periodic reconnects (every ~120s)
- Heartbeat activity (every ~5s)
- Snapshot begin/end cycles (every ~30s)
- Validates: no freezes, no queue explosion, monotonic counters

#### `phase5e_stress_large_scene.py`
- 1000 root objects + 1500 hierarchy objects
- Mixed primitive types (Cube, Sphere, Cylinder, Plane, Empty)
- Rapid transform bursts (3000-5000 objects)
- Packet batching alignment verification (81-byte object alignment)
- Mixed CREATE/DELETE/TRANSFORM workload

#### `phase5e_stress_reconnect_storm.py`
- 50 rapid connect/disconnect cycles
- Disconnect during continuous transform burst
- Disconnect during actor creation burst
- Reconnect during heartbeat activity
- Blender-restart simulation (10 cycles)
- Combined burst + disconnect + reconnect stress (20 cycles)

#### `run_phase5e_all.py`
- Consolidated runner with `--quick` flag to skip 30-min test
- Standard exit code reporting

## Tick Pipeline Lifecycle

1. **ProcessQueuedPackets** — Dequeue from FLiveSyncQueue (max 200/tick
   via `MaxPacketRate`). Parse binary headers, dispatch by version (V2-V5)
   and packet type (TRANSFORM/CREATE/DELETE/HEARTBEAT/ASSETDEF/
   BEGINSNAPSHOT/ENDSNAPSHOT). Update FSyncTransformState entries.

2. **EvictStaleTransformStates** — Remove entries past TTL (default 60s).

3. **InterpolateTransforms** — Drive actor transforms toward targets.
   Direct-set, snap, or smooth interpolation. Attached children use
   local-space path; root actors use world-space path.

4. **ResolvePendingAttachments** — Retry deferred parent-child
   attachments. Exponential retry up to 60 attempts or 5s timeout.

5. **RecoverMissingActors** — Re-spawn actors with stored transform
   state when actor cache misses. Max 3 recovery attempts.

6. **ResolvePendingAssets** — Late-binding mesh resolution via
   `PendingAssetQueue`. Exponential backoff retry (1s→2s→4s→8s→16s,
   max 5 attempts). Falls back to primitive on exhaustion.

7. **ValidateHierarchy** (every ~300 ticks) — Self-parent, circular
   chain, invalid GUID, excessive depth detection.

## Network Thread Responsibilities

- ONLY enqueues packets to `FLiveSyncQueue` (bounded MPSC, 128 max).
- NEVER performs game-thread operations (spawning, transforms, etc).
- Read loop: `Wait(10ms)` → header recv (24 bytes) → version dispatch
  → payload recv → enqueue.
- Exits on socket error, stop signal, or peer disconnect.

## Game Thread Responsibilities

- ALL runtime processing: parsing, spawning, transforms,
  attachments, recovery, asset resolution, validation.
- CVar sync every tick.
- Metrics EMA update every tick.
- Safety monitors (flood detection, queue pressure, age watchdog).

## Queue Flow

```
Blender TCP → LiveSyncRunnable::Run() → Enqueue() → FLiveSyncQueue
                                                          ↓
                                            ProcessQueuedPackets()
                                                          ↓
                                              Tick Pipeline (game thread)
```

- Bounded at 128 entries (drop-oldest on overflow).
- Processed at up to `MaxPacketRate` (200) per tick.
- Overflow tracked via `FOverflowEvent` history (32 entries max).
- Packet age watchdog estimates oldest-packet age and warns/flushes.

## Reconnect Lifecycle

1. Socket error or heartbeat timeout detected in Tick()
2. `StopNetworkThread()` called:
   - `Runnable->Stop()` sets atomic flag
   - `Socket->Shutdown(ReadWrite)` unblocks network thread (critical on Linux)
   - `Socket->Close()` OS socket release
   - `WaitForCompletion()` thread join
   - Delete runnable + thread
   - `ISocketSubsystem::DestroySocket()` final cleanup
3. State reset: queue, transforms, pending attachments, missing actors
4. Listener socket remains active for new connections
5. Next Tick() accepts new connection via `HasPendingConnection()`
6. `BuildActorCache()` scans world for existing LiveSync-tagged actors
7. `StartNetworkThread()` creates new runnable + thread

## Known Failure Modes

- **Port conflict**: ListenerSocket creation fails → 5s retry loop in Tick()
- **Stale thread**: Network thread exits but flag not caught → next Tick()
  detects `bThreadExited` and calls `StopNetworkThread()`
- **Heartbeat timeout**: 15s default → `StopNetworkThread()` triggered
- **Snapshot timeout**: 5s without EndSnapshot → `AbortSnapshot()`
- **Queue overflow**: >128 packets → drop-oldest with warning
- **Packet age >30s**: Queue flushed, packets counted as dropped
- **Circular hierarchy**: Caught by ValidateHierarchy() or AttachToParent()
- **Missing actor**: 30 frames → 3 recovery attempts → eviction

## Debugging Workflow

1. Set `UE.LiveSync.Verbose 1` for detailed logging
2. Set `UE.LiveSync.VerboseSyncLogs 1` for per-packet tracing
3. Set `UE.LiveSync.DebugDraw 1` for on-screen status overlay
4. Use console commands:
   - `UE.LiveSync.DumpState` — all tracked objects
   - `UE.LiveSync.Stats` — runtime metrics
   - `UE.LiveSync.Ping` — connectivity check
   - `UE.LiveSync.Reset` — full teardown/restart
5. Profile with Unreal Insights (TRACE_CPUPROFILER_EVENT_SCOPE active)
6. Use isolation CVars to narrow issues:
   - `UE.LiveSync.DisableSpawning 1`
   - `UE.LiveSync.DisableTransformApply 1`
   - `UE.LiveSync.DisableAttachment 1`

## Stress Test Procedures

### Quick Smoke Test
```bash
python3 tests/run_phase5e_all.py --quick
```
Runs large-scene (1000+ objects) + reconnect storm (~5 min).

### Full Validation
```bash
python3 tests/run_phase5e_all.py
```
Includes 30-min long-duration test. Requires UE editor listening on
`127.0.0.1:57000`.

### Individual Tests
```bash
python3 tests/phase5e_stress_large_scene.py
python3 tests/phase5e_stress_reconnect_storm.py
python3 tests/phase5e_stress_long_duration.py
```

## Runtime Stability Evidence

Validated in a 6h38m continuous editor session against UE 5.7.4
(Linux, `-opengl4` + `LIBGL_ALWAYS_SOFTWARE=1`):

| Metric | Value |
|---|---|
| Consecutive Tick frames | 46,400 — all complete |
| Pipeline balance | 232,000 BEGIN = 232,000 END (perfect) |
| SetActorTransform calls | 14,268 — 0 missing END traces |
| InterpolateTransforms | 46,400 calls, 46,400 complete |
| HandleCreateObject | 70 — all succeeded in <0.1ms |
| Stress test transforms | 5,412 packets (108,240 object transforms) |
| Instant burst | 1,000 packets (20 objects each) in <1s |
| Queue overflow events | 1 — recovered gracefully |
| Malformed packet recovery | 5,243 partial packets handled |
| CEF GPU crashes | 3 (at editor startup, auto-recovered) |
| Plugin crashes | 0 |
| Editor freezes/hangs | 0 |

## Validation Targets

The system must survive:

| Scenario | Target | Metric |
|---|---|---|
| 30-min runtime | Continuous sync | No freeze/deadlock |
| 1000+ objects | Hierarchy chains | No hierarchy corruption |
| Reconnect storm | 50 cycles | No socket leaks |
| Transform bursts | 5000/tick | No queue explosion |
| Blender restart | 10 cycles | No stale threads |
| Malformed packets | Injected | No crash |
| Abrupt disconnect | Mid-processing | No queue corruption |
| Packet burst | >128 instant | No queue overflow crash |

Without: freezes, deadlocks, queue runaway, hierarchy corruption,
socket leaks, stale threads, or transform desync.

## Final Freeze Root-Cause Classification

### Original Symptom
The original reported "freeze" was a **crash** (not a UI freeze or
deadlock). The editor process terminated with SIGABRT inside
`FPendingAssetQueue::Dequeue` → `TSet::Remove`, triggered by
SparseSet internal assertion failure.

**Stack trace** (from `ProjectTemplate-backup-2026.05.24-15.14.16.log`):
```
FPendingAssetQueue::Dequeue(FGuid&)
    [SparseSet.h.inl:865]
UUELiveSyncSubsystem::ResolvePendingAssets()
    [UELiveSyncSubsystem.cpp:4284]
UUELiveSyncSubsystem::Tick(float)
```

**Sequence**: Heavy sustained load → packet queue overflow (128
depth exhausted) → peer disconnect → `ResolvePendingAssets` dequeues
from pending asset queue → SparseSet assertion failure.

### Two Distinct Fixes Applied

| # | Issue | Fix | File |
|---|-------|-----|------|
| 1 | SIGABRT on `TSet::Remove` if `EntrySet` drifts out of sync with `Entries` | `Contains()` guard before `EntrySet.Remove(OutGuid)` in `Dequeue()` | `PendingAssetQueue.h:51` |
| 2 | Infinite loop in `ResolvePendingAssets()` when all dequeued GUIDs hit the "re-enqueue (NextRetryTime not yet reached)" path | Move `ResolvedThisTick++` to top of while(body) so iterations are always bounded by `MAX_ASSET_RESOLUTIONS_PER_TICK (=8)` | `UELiveSyncSubsystem.cpp:4708` |

### Fix Validation

Both fixes validated with comprehensive test suite (5/5 passing):
- Reconnect storm (20 rapid cycles)
- Queue overflow (300 instant packets)
- Abrupt disconnect during CREATE + ASSETDEF processing
- Malformed packet burst (truncated headers, garbage data, wrong MAGIC, zero-size packets, over-claiming objects)
- Pipeline health (Tick continuity, BEGIN/END balance)

## Known Environment Issues

### Linux — CEF/Vulkan GPU Subprocess

The Unreal Editor's Chromium Embedded Framework GPU subprocess is
unstable on this Linux system due to Vulkan/ANGLE initialization
failures:

- **Vulkan error**: `VK_ERROR_INITIALIZATION_FAILED (-3)` during
  `vkCreateInstance` or `eglInitialize`
- **CEF crash**: `GPU process exited unexpectedly: exit_code=256`
  (SIGTRAP in `libcef.so`)
- **Impact**: Repeated CEF GPU process crashes. CEF auto-recovers
  after 3 restart attempts. In extreme cases, the editor process
  terminates with SIGABRT.
- **Scope**: These crashes occur at editor startup regardless of
  plugin activity. The UELiveSync runtime pipeline does NOT trigger
  or contribute to them.

### Workaround

```
LIBGL_ALWAYS_SOFTWARE=1 ./UnrealEditor <project>.uproject -opengl4
```

Forces OpenGL 4.x software rasterization, bypassing Vulkan entirely.
CEF subprocess crashes stop, but the editor runs at 1–2 fps.
Sufficient for pipeline validation; not suitable for production use.

### Exculpation

The `libcef.so SIGTRAP` and `GPU process crashes` observed during
development are **external engine/environment issues** on this
specific Linux host. They are NOT caused by the UELiveSync runtime
pipeline, do NOT correlate with plugin activity, and reproduce
with or without the plugin loaded.

## Memory & Resource Usage

The plugin's steady-state memory overhead is negligible:
- `FSyncTransformState`: ~80 bytes per tracked GUID
- `FPendingAssetQueue`: 2048-entry max, ~32KB
- `FLiveSyncQueue`: 128-entry max, ~10KB
- `AssetMetadata`: variable, proportional to received ASSETDEF count

In a 6h38m session with continuous transform data:
- Editor RSS: ~5 GB (engine baseline + content, NOT plugin)
- No detectable memory leak from plugin structures
- Queue depth stabilizes below 80% even under load

## Release Readiness: v0.5.0-stabilized

### Milestone Scope

The v0.5.0-stabilized marks the completion of Phase 5E and the end
of Phase 5 (Protocol Evolution & Runtime Stabilization). It is the
last release before Phase 6 (Live Editing System) begins.

**Included**:
- Protocol evolution: V2–V5 backward compatibility
- Full object lifecycle: CREATE, TRANSFORM, DELETE, HEARTBEAT,
  SNAPSHOT (BEGIN/END), ASSETDEF
- Hierarchical transform: parent-child attachment with local-space
  interpolation
- Reconnect handling: socket teardown, state reset, listener
  persistence, actor cache rebuild
- Snapshot batching: deferred hierarchy + transform freeze during
  bulk operations
- Asset identity: xxHash64 deterministic identity, deferred mesh
  resolution with exponential backoff retry
- Diagnostics: runtime metrics dashboard, debug overlay, verbose
  logging, console commands (DumpState/Stats/Ping/Reset)
- Safety monitors: flood detection, queue pressure, packet age
  watchdog, hierarchy validation
- Crash resilience: PendingAssetQueue `Contains()` guard,
  ResolvePendingAssets iteration bounding
- Stress infrastructure: long-duration test, large-scene test,
  reconnect storm test, malformed packet test
- Profiling: Unreal Insights TRACE_CPUPROFILER_EVENT_SCOPE at
  every pipeline stage

**Not Included** (Phase 6+):
- Rename replication
- Collection/folder sync
- Visibility/hidden state sync
- Duplicate detection
- Animation sync
- Sequencer integration
- Compression or delta serialization
- Auto-discovery or installer

### Release Tagging

```
v0.5.0-stabilized
Protocol: V5 (wire), V2/V3/V4 backward compatible
UE Version: 5.7.4
Blender: 5.x (tested with 5.2-5.4)
```

### Known Limitations

- Mesh resolution is deferred (non-blocking). New objects use
  fallback primitives until asset path resolves.
- Pending asset resolution uses max 8 iterations per tick. In
  scenes with hundreds of unresolved assets, resolution may lag
  by several seconds.
- Queue overflow (>128 packets in 10ms) drops oldest packets.
  This is a hard backpressure limit; sustained rates above
  processing capacity lose data.
- Snapshot mode requires explicit BEGIN/END. An orphaned
  BeginSnapshot (no matching EndSnapshot) times out after 5s.
- The `UE.LiveSync.DisableSpawning` CVar prevents actor creation.
  Objects received while it is enabled are silently dropped.
- Heartbeat timeout (default 15s) triggers full teardown.
  Transient network blips longer than 15s cause reconnect.

### Environment Caveats

- **Linux CEF/Vulkan**: See "Known Environment Issues" above.
- **Software rendering**: Using `-opengl4` + `LIBGL_ALWAYS_SOFTWARE=1`
  limits frame rate to 1–2 fps. Pipeline validation works but
  real-time interactivity requires hardware Vulkan rendering.
- **Blender flatpak**: When running Blender as flatpak, the
  default port (57000) must be accessible. Add `--socket=tcp`
  or use `flatpak-spawn` for host networking.
- **UE 5.7.4**: The `Trace.h` umbrella header was removed.
  Use `ProfilingDebugging/CpuProfilerTrace.h` if including UE
  trace headers directly.

### Phase Consistency

```
Phase 1-4:   Foundations
Phase 5:     Protocol Evolution & Runtime Stabilization ← COMPLETE
  └─ 5A: Snapshot Foundations
  └─ 5B: Protocol Compatibility
  └─ 5C: Protocol Hardening & Fuzzing
  └─ 5D: Runtime Stability & Freeze Fixes
  └─ 5E: Stress Testing & Observability ← YOU ARE HERE
Phase 6:     Live Editing System ← NOT STARTED
Phase 7:     Animation & Sequencer Sync ← NOT STARTED
Phase 8:     High Performance Streaming ← NOT STARTED
Phase 9:     Production Ecosystem ← NOT STARTED
```

Phase 6 begins when full editor-side live editing workflows
(rename replication, collection/folder sync, visibility sync,
duplicate detection) start implementation. Protocol parsing
changes, queue safety, reconnect handling, validation infrastructure,
and runtime stabilization ALL belong to Phase 5.

## Canonical Roadmap Reference

```
Phase 1-4: Foundations
Phase 5:   Protocol Evolution & Runtime Stabilization ← YOU ARE HERE
  └─ 5A: Snapshot Foundations
  └─ 5B: Protocol Compatibility
  └─ 5C: Protocol Hardening & Fuzzing
  └─ 5D: Runtime Stability & Freeze Fixes
  └─ 5E: Stress Testing & Observability
Phase 6:   Live Editing System
  └─ Object create/delete, rename replication,
     duplicate detection, collection/folder sync,
     visibility sync
Phase 7:   Animation & Sequencer Sync
  └─ Timeline sync, playback sync, keyframe replication,
     camera sync, sequencer integration
Phase 8:   High Performance Streaming
  └─ Binary compression, packet batching, delta serialization,
     multi-thread replication, interest management
Phase 9:   Production Ecosystem
  └─ Installer, auto-discovery, version compatibility layer,
     UI polish, preset system, project templates,
     crash recovery
```
