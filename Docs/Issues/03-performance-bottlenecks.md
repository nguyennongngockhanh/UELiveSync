# Performance Bottleneck Analysis

> **ARCHIVED**: All identified hotspots were resolved in Phases 3.4–3.5.
> Preserved for historical reference only.

> Analysis originally written 2026-05-20. All four identified hotspots were resolved in Phases 3.4–3.5.

## Measurement Baselines

### Blender Side

| Metric | How to Measure | Expected Value |
|--------|---------------|----------------|
| `check_updates()` duration | `time.perf_counter()` around loop | ~0.5-1ms (no scan when scene unchanged) |
| `get_transform()` per object | `time.perf_counter()` per call | ~0.1ms per object |
| `send_objects()` duration | timer around send | <1ms on localhost (non-blocking enqueue) |
| Objects iterated per frame | `len(tracked_objects)` | Depends on active tracked set; full `bpy.data.objects` scan on count change only |

### UE Side

| Metric | How to Measure | Expected Value |
|--------|---------------|----------------|
| `ProcessBinaryPacket()` per object | `FScopeTimer` | <5μs per object (direct Memcpy, no string alloc) |
| `InterpolateTransforms()` per frame | `FScopeTimer` | <1ms for 100 actors |
| Queue depth | `Queue.Size()` | <5 entries steady state (bounded to 128) |
| Log write time | disable `bEnableVerboseSyncLogs` compare | Zero in production (all per-frame logs gated) |

## Known Hotspots

### Hotspot 1: GUID String Parsing (UE) — ✅ RESOLVED

**Status**: Fixed by V3 protocol (Phase 3.4). GUID sent as 4×uint32 LE, read directly into `FGuid` fields via `FMemory::Memcpy`. Zero string allocation.

**Before**: `FString::Printf` per byte → 16 allocations → `FGuid::ParseExact` — ~3-5μs per object.

**After**: 4× `uint32` Memcpy — <0.1μs per object.

### Hotspot 2: Per-Object Logging (UE) — ✅ RESOLVED

**Status**: Fixed (Phase 3.4). All per-frame/per-packet logging gated behind `bEnableVerboseSyncLogs` (default: `false`). Rate-limited to 1/300 frames via `ShouldLogVerbose()`.

**Low-frequency events** (delete, metrics snapshots) use direct `bEnableVerboseSyncLogs` check without rate limiting.

### Hotspot 3: Main thread network I/O (Blender) — ✅ RESOLVED

**Status**: Fixed (Phase 3.4). `sendall()` runs on a dedicated background sender thread (`LiveSyncClient._sender_loop`). Main thread only enqueues serialized packets via `queue.Queue.put_nowait()` — guaranteed non-blocking.

### Hotspot 4: Full scene iteration (Blender) — ✅ RESOLVED

**Status**: Fixed (Phase 3.5). Main loop iterates `tracked_objects` dict, not `bpy.data.objects`. Full `bpy.data.objects` scan only when:
- Object count changes (len comparison, O(1))
- 300-frame periodic safety net
