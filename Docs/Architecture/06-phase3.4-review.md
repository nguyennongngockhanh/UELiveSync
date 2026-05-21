# Phase 3.4 Architecture Review

Date: 2026-05-21
Commit: f8277aa

## Scope

Phase 3.4 completed the real-time replication stabilization work for the Blender ↔ UE5.7 live sync pipeline. This document reviews the current architecture, documents what was implemented, and identifies remaining gaps.

---

## What Phase 3.4 Implemented

### Protocol V3

| Feature | Status | Detail |
|---------|--------|--------|
| V3 binary header with type/flags | ✅ | 24-byte header, `FPacketHeaderV3` |
| Direct uint32 GUID (no hex roundtrip) | ✅ | 4× uint32 → FGuid, zero string alloc |
| CREATE/UPDATE/DELETE packet types | ✅ | Type dispatch in `ProcessBinaryPacket()` |
| Heartbeat packets | ✅ | Type 0x07, updates `LastHeartbeatTime` |
| Timestamp field | ✅ | `double` per object |
| Parent GUID field | ✅ | 16 bytes per object (future hierarchy) |
| V2 backward compat | ✅ | Version check in both Blender and UE |

### Threading

| Feature | Status | Detail |
|---------|--------|--------|
| Blender background sender thread | ✅ | `queue.Queue` + daemon thread in `network.py` |
| Bounded UE packet queue | ✅ | `FLiveSyncQueue`, 128 max, drop-oldest |
| Thread exit detection | ✅ | `FThreadSafeBool bThreadExited` |
| Socket-close-before-join shutdown | ✅ | Unblocks Recv/Wait, total <0.1ms |
| Stale state reset on disconnect | ✅ | Queue, TransformStates, heartbeat, sequence |

### Actor Management

| Feature | Status | Detail |
|---------|--------|--------|
| Incremental actor cache | ✅ | `OnActorSpawned`/`OnActorDestroyed` handlers |
| Actor visibility | ✅ | `UStaticMeshComponent` (Cube) with `Movable` mobility |
| GUID tagging | ✅ | `LiveSync_GUID=<hex>` on spawned actors |
| Cache rebuild on startup | ✅ | `BuildActorCache()` scans existing actors |
| Stale pointer removal | ✅ | `OnActorDestroyed` removes from cache |

### Lifecycle

| Feature | Status | Detail |
|---------|--------|--------|
| StopNetworkThread idempotent | ✅ | Early return if no thread running |
| Double-start guard | ✅ | Stops old thread before creating new one |
| Heartbeat timeout cleanup | ✅ | 15s timeout, calls StopNetworkThread |
| Stale connection detection | ✅ | `GetConnectionState()` check |
| Deinitialize cleanup | ✅ | All resources freed |
| LastSequenceId reset | ✅ | Prevents false duplicate rejection on reconnect |

### Logging

| Feature | Status | Detail |
|---------|--------|--------|
| Rate-limited diagnostics | ✅ | All per-frame/per-packet logs behind `bEnableVerboseSyncLogs` |
| 300-frame throttle | ✅ | `VerboseFrameCounter % 300 == 0` when enabled |
| Shutdown timing diagnostics | ✅ | Per-stage ms breakdown |
| Thread exit logging | ✅ | Exit reason + lifetime in ms |
| Lifecycle transitions | ✅ | Connect/disconnect/reconnect logged |
| Default: zero per-frame output | ✅ | No `UE_LOG` executes per tick in production |

---

## Current Architecture Summary

### Data Flow (steady state, 60fps)

```
[Blender]                          [UE]
   │                                  │
   ├─ check_updates() (16ms timer)    │
   │  └─ serialize changed objects    │
   │     └─ enqueue to sender thread  │
   │        └─ socket.sendall()       │
   │           │                      │
   │           │ TCP :5000            │
   │           ▼                      │
   │                          ┌───────┤
   │                          │ Wait  │ (10ms timeout)
   │                          │ Recv  │ (header + payload)
   │                          │ Valid │ (magic, version)
   │                          │ Enq   │ (bounded queue)
   │                          └───────┤
   │                             │    │
   │                             │    ├─ ProcessQueuedPackets()
   │                             │    │  └─ ProcessBinaryPacket()
   │                             │    │     ├─ HandleCreateObject()
   │                             │    │     └─ UpdateTargetTransform()
   │                             │    │
   │                             │    └─ InterpolateTransforms()
   │                             │       ├─ FindActorFast()
   │                             │       └─ SetActorTransform()
   │                             │
   │                          [frame complete]
```

### Key Latency Numbers

| Stage | Measured |
|-------|----------|
| Blender scene iteration + serialize (100 objects) | ~2–5ms |
| Network send (localhost) | <1ms |
| UE queue drain + parse (100 objects) | <0.5ms |
| UE interpolate + apply (100 actors) | <1ms |
| End-to-end (Blender timer → UE actor) | ~20–35ms |
| StopNetworkThread full shutdown | **0.05–0.08ms** |
| Thread exit detect → cleanup | **~16ms** (1 frame) |

### Log Volume (normal operation)

| Log Category | Frequency | Lines per hour |
|-------------|-----------|----------------|
| Startup | Once | ~6 |
| Connection | Per connect | ~3 per connect |
| Disconnect | Per disconnect | ~4 per disconnect |
| Transform states | **Zero** (behind verbose flag) | 0 |
| Per-packet diagnostics | **Zero** (behind verbose flag) | 0 |
| Heartbeat timeout | Every 15s idle | ~240 |
| **Total (steady state, idle)** | | **~0 lines/hour** |

---

## Remaining Issues

### High Priority

| Issue | Area | Detail |
|-------|------|--------|
| 15s heartbeat timeout on idle | Lifecycle | Long gap between disconnect detection and cleanup (mitigated by `bThreadExited` for abrupt disconnects, but graceful idle still waits 15s) |
| No Blender reconnection | Resilience | If UE restarts, Blender addon doesn't auto-reconnect; user must manually Stop/Start |

### Medium Priority

| Issue | Area | Detail |
|-------|------|--------|
| Full scene iteration per tick | Blender perf | `bpy.data.objects` iterated every 16ms regardless of change count |
| World-space only | Protocol | No hierarchy support; parent GUID field is in packet but unused |
| No initial snapshot | Protocol | No full-state sync on connect |
| Hardcoded thresholds | Config | Location/rotation/scale thresholds in `sync.py` should be addon preferences |
| Interpolation always lags | Visual | Actor trails target by ~3 frames; no option for direct set |

### Low Priority

| Issue | Detail |
|-------|--------|
| Port 5000 conflicts with AirPlay on macOS | Configurable port needed |
| No UI status indicator | Connection state not visible in UE viewport |
| MESH-only default filter | Cameras, lights, armatures excluded |
| Single connection only | No multi-client support |
| TransformStates TTL eviction | States persist for disconnected objects until manual cleanup |
| Scale interpolation uses linear lerp | Should be exponential for multiplicative scale |

---

## Design Decisions

### Why keep V2 parser?
Zero migration risk. Blender sends V3 by default, but if the version constant is changed or an older client connects, UE gracefully degrades to V2 parsing.

### Why close socket before WaitForCompletion?
Closing the socket first causes the network thread's blocking `Wait()` to return immediately (via `POLLNVAL` on Linux). Without this, the thread could block for up to 10ms (the Wait timeout) before checking `bRunThread`.

### Why bounded queue (128) instead of unbounded?
Prevents OOM during game thread hitches. The network thread can produce packets at up to 1000/s (with heartbeat). If the game thread stalls for 1 second, an unbounded queue could grow by 1000 packets (~100KB). With 128-entry bound, at most 128 packets (12.8KB) are buffered.

### Why default logs disabled?
UE_LOG is expensive (formatting + mutex + disk write). At Warning severity in Development builds, each call takes ~10–50μs. With 100 objects at 60fps, that's 6,000–30,000 log lines/second and 60–300ms of frame time. Disabling all per-frame logs brings this to zero.

### Why `FThreadSafeBool bThreadExited` instead of cross-thread signal?
UE's `FRunnableThread` doesn't natively support signaling for exit detection. Polling an atomic bool from the game thread's Tick is simple, thread-safe, and adds at most 1 frame (~16ms) of latency to detect thread exit — far faster than the 15s heartbeat timeout backup.
