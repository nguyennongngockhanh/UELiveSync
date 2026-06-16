# Phase 8 — High Performance Streaming Closeout Audit

**Date:** 2026-06-16
**Baseline:** `fbx-test-hygiene-stable` (f76cbe1)
**Audit Commit:** (created by this audit)

## Overview

Phase 8 was scoped to optimize high-performance streaming of mesh data
between Blender and UE LiveSync. This audit compares the scope-lock
document claims against the current codebase to produce an accurate
inventory of what is actually implemented.

## Scope Lock Claims vs. Codebase Reality

The design document at `Docs/Architecture/46-phase8-high-performance-streaming-scope-lock.md`
(Section "Stage Completion Status", lines 498–513) marks 10 of 13 stages
as COMPLETE. Source audit reveals most were never implemented.

### Stages With Code Evidence

| Stage | Claimed | Actual | Code Evidence |
|-------|---------|--------|---------------|
| Stage 1A | COMPLETE | **PARTIAL** | `UE.LiveSync.MeshReassemblyTimeoutSec` CVar exists (`UELiveSyncSubsystem.cpp`), eviction scan in `ReconstructCompletedMeshes`. `MeshStaleEvictions` counter declared. |
| Stage 1B | COMPLETE | **PARTIAL** | Queue diagnostics: `QueueDepthCurrent`/`QueueDepthPeak` (`SyncTypes.h:1034-1035`), `PacketsDropped` atomic (`SyncTypes.h:1028`), `FOverflowEvent` with 32-entry history (`SyncTypes.h:1377-1387`). No `[Queue Stats]` DumpState output, no `[QUEUE]` health log. |
| Stage 1E | PENDING | **PENDING** | Cross-tick coalescing — never implemented. Aligns with doc. |
| Stage 1F | PENDING | **PENDING** | Orphaned mesh timeout stress test — never implemented. Aligns with doc. |
| Stage 2.4 | DEFERRED | **DEFERRED** | Mesh decode offload — deferred indefinitely. Aligns with doc. |
| Stage 3 | PARTIAL | **PARTIAL** | Large scene benchmark completed (50/100/250/500 objects). Evidence in `.opencode/evidence/`. Stress harness not runnable. |

### Stages With No Code Evidence (scope-lock doc overstates)

| Stage | Claimed | Actual | Analysis |
|-------|---------|--------|----------|
| Stage 1C | COMPLETE | **NOT IMPLEMENTED** | Backpressure ACK transport — zero code. No `PT_BackpressureACK` (0x10), no `HandleBackpressureACK`, no `EnableBackpressureACK` CVar, no `_ack_lock`, no ACK recv in Blender sender loop. |
| Stage 1D | COMPLETE | **NOT IMPLEMENTED** | ACK policy / adaptive throttling — zero code. No `get_suggested_interval()`, no `MIN_SEND_INTERVAL`/`MAX_SEND_INTERVAL`, `check_updates()` returns hardcoded `0.016` (`sync.py:2413`). |
| Stage 2.1B | COMPLETE | **NOT IMPLEMENTED** | Mesh compression constants — zero code. `MESH_CHUNK_FLAG_COMPRESSED` does not exist. No `MeshCompression` CVar. |
| Stage 2.1C | COMPLETE | **NOT IMPLEMENTED** | Mesh zlib compression/decompression — zero code. No `import zlib` in Blender addon. No `FCompression::UncompressMemory` in UE source. |
| Stage 2.2C | COMPLETE | **NOT IMPLEMENTED** | Section builder optimization — zero code. No `Reserve()` calls, no pre-sizing of TArrays/TMaps in section building. |
| Stage 2.2D | COMPLETE | **NOT IMPLEMENTED** | Remove MaterialGroups — zero code. Original MaterialGroups map structure not refactored. |
| Stage 2.3A | COMPLETE | **NOT IMPLEMENTED** | Dirty-flag interest management — zero code. No depsgraph handler, no `_dirty_guids`, no `[DIRTY]` diagnostics in sync.py. |

### Conclusion

The scope-lock document describes an aspirational architecture that was
**never implemented.** Only 3 stages have partial code: Stage 1A (orphan
timeout), Stage 1B (queue diagnostics), and the burst packet counting
(part of Stage 1 scope). The remaining 7 stages marked COMPLETE have
zero code evidence.

**Phase 8 overall status should be reclassified as:**

> **DESIGN ONLY — MINIMAL CODE EXISTS**
> 
> Only burst packet counting (Blender), queue depth tracking (UE), and
> mesh reassembly timeout (UE) are implemented. The backpressure ACK,
> adaptive throttling, mesh compression, section optimization, and
> dirty-flag interest management stages were scoped but never coded.
> The large scene benchmark was run on the unoptimized pipeline.

## Packet Registry

- `0x10` is **NOT** in `kValidTypes` (`UELiveSyncSubsystem.cpp:2988-2989`).
  `PT_BackpressureACK` was proposed but never registered.
- `0x02` is **NOT** in `kValidTypes` — remains reserved/invalid.
  `PT_Reserved_02` is defined at `SyncTypes.h:208` as unused legacy.
  `0x02` appears only in `kValidFlags` as `PF_FullSnapshot` (`SyncTypes.h:720`).
- `kValidTypes` has 25 entries: `{0x01, 0x03-0x0F, 0x11-0x1B}`.

## What Actually Exists

### Blender Addon — Burst Packet Diagnostics (`sync.py`)

- `_burst_packet_count` per-tick counter at 24 increment sites
- `_runtime_stats["burst_packet_count"]` and `"burst_packet_count_peak"`
- Peak tracking via `max(burst_packet_count_peak, _burst_packet_count)`
- Comment marker: `# Phase 8 Stage 1: per-tick burst packet count` (`sync.py:2406`)
- Test: `tests/phase8_burst_packet_diagnostics.py` (10 tests)

### Blender Addon — Send Queue (`network.py`)

- `self._send_queue = queue.Queue(maxsize=256)` (`network.py:2627-2629`)
- Sender thread with non-blocking dequeue (`network.py:2662-2756`)
- High-water warning at 75% (192/256), logged every 5s (`network.py:3245-3256`)
- Queue full drop handling with `queue.Full` exception (`network.py:3264-3283`)
- `get_queue_depth()` returns `_send_queue.qsize()` (`network.py:3476-3490`)
- Queue displayed in UI as "Queue: {queue_depth}" (`__init__.py:1187-1231`)

### UE Plugin — Queue Diagnostics

- `QueueDepthCurrent`, `QueueDepthPeak` in `FLiveSyncStats` (`SyncTypes.h:1034-1035`)
- `PacketsDropped` atomic counter (`SyncTypes.h:1028`)
- `FOverflowEvent` struct with `Timestamp` and `QueueDepth` (`SyncTypes.h:1377-1381`)
- `MAX_OVERFLOW_HISTORY = 32` (`SyncTypes.h:1383-1387`)
- Overflow detection on new drops (`UELiveSyncSubsystem.cpp:2545-2565`)
- Hard-limit flush when packet age exceeds 30s (`UELiveSyncSubsystem.cpp:11831-11836`)
- Per-tick rate capping via `CVarLiveSyncMaxPacketRate` (default 200, `UELiveSyncSubsystem.cpp:702-707`)

### UE Plugin — Mesh Reassembly Timeout

- `UE.LiveSync.MeshReassemblyTimeoutSec` CVar
- `MeshStaleEvictions` counter
- Eviction scan in `ReconstructCompletedMeshes`

### Large Scene Benchmark (Phase 8 Stage 2)

- 50/100/250/500 objects benchmarked on unoptimized pipeline
- Burst packet peak: 3 (create), 1 (move) — constant across all counts
- Queue depth: 0, dropped packets: 0
- Evidence: `.opencode/evidence/phase8_stage2_large_scene_load/`

## Test Inventory

| Test File | Tests | Type | Status |
|-----------|-------|------|--------|
| `tests/phase8_burst_packet_diagnostics.py` | 10 | Static source-text | PASS |
| `tests/phase8_performance_streaming_audit.py` | (new) | Static source-text | ADDED |

No runtime Phase 8 tests exist. The stress harness at `/tmp/stress_phase8_v2.py`
referenced in scope-lock doc is not present.

## Classification

`PASS_PHASE8_AUDIT_ONLY` — Source audit completed. No runtime validation
of unimplemented features possible. Existing implemented diagnostics tested
via source-text assertions.

## Known Limitations

1. Backpressure ACK was designed but never coded. `0x10` remains unused.
2. Adaptive throttling was designed but never coded. Send rate is hardcoded 0.016s.
3. Mesh compression was designed but never coded. No zlib anywhere.
4. Section builder optimization was designed but never coded.
5. Dirty-flag interest management was designed but never coded.
6. Cross-tick coalescing (Stage 1E) remains pending.
7. Orphaned mesh timeout stress test (Stage 1F) remains pending.
8. Stress harness (`/tmp/stress_phase8_v2.py`) does not exist.
9. Large scene benchmark was run on unoptimized pipeline; optimized comparison unavailable.

## Recommended Next Stage

Close Phase 8 as **DESIGN COMPLETE, MINIMAL IMPLEMENTATION.** Do not
defer pending stages to future phases unless streaming performance is
measured as a bottleneck. The large scene benchmark (500 objects at
16ms tick) showed no queue depth or packet drops on the unoptimized
pipeline — suggesting optimization is not currently required.

Begin Phase 9 if the production ecosystem (installer, preferences,
discovery, diagnostics) is the next priority.
