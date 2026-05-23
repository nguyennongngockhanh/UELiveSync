---
name: ue5-live-sync
description: >-
  Use when engineering real-time Blender ↔ Unreal Engine 5.7
  synchronization systems, especially binary TCP protocol design,
  threaded network architecture, GUID-based object identity,
  transform interpolation, bounded MPSC queues, heartbeat/reconnect
  logic, UE WorldSubsystem plugins, and UE5 Sockets/Networking module
  patterns. Use ONLY for UE5 C++ sync infrastructure and companion
  Blender addon Python code. Not for general game development or
  unrelated UE5 features.
---

# UE5 Live Sync — Engineering Guidelines

## Core Principles

- **Thread safety first** — network I/O on dedicated thread, all `UObject`/world mutations on game thread. Never store raw `UObject*` across thread boundaries.
- **Protocol compatibility** — V3 is current (24-byte header, direct uint32 GUID). V2 must remain parseable. Protocol changes require version bump.
- **No blocking operations** — socket reads use `Wait(10ms)` with short timeout. Game thread never touches sockets directly.
- **Reconnect resilience** — background reconnection with exponential backoff. Full snapshot burst on reconnect to recover state.
- **Production-safe logging** — dedicated `LogLiveSync` category. Verbose logging gated behind `UE.LiveSync.Verbose` CVar (default off). Rate-limited to ≤1 per 300 frames in hot paths.
- **Low overhead** — bounded 128-entry MPSC queue with drop-oldest overflow. O(1) stale object detection. Per-frame per-GUID dedup.

## Architecture Overview

```
Blender (Python)                      UE5 (C++)
┌─────────────────────┐              ┌──────────────────────┐
│  Main Thread        │  TCP (:57000)│  Network Thread      │
│  scene iteration    │─────────────▶│  LiveSyncRunnable    │
│  diff detection     │              │  Wait(10ms) + Recv   │
│  serialization      │              │  validate magic/vers │
│  enqueue (non-block)│              │  enqueue FLiveSyncPkt│
│                     │              │                      │
│  Daemon Thread      │              │  Game Thread (Tick)  │
│  socket.sendall()   │              │  ProcessQueuedPkts() │
│  reconnect backoff  │              │  InterpolateTrans()  │
└─────────────────────┘              │  SetActorTransform() │
                                     └──────────────────────┘
```

## Key Source Files

| File | Role |
|------|------|
| `Blender_Addon/network.py` | TCP client, serialization, threaded sender (`LiveSyncClient`, `_sender_loop`, `_build_packet`) |
| `Blender_Addon/sync.py` | Scene iteration, transform diff, GUID assignment, heartbeat timer |
| `UE_Plugin/.../UELiveSyncSubsystem.cpp/h` | `UWorldSubsystem` — main game-thread orchestrator, `Tick()`, packet processing, interpolation |
| `UE_Plugin/.../LiveSyncRunnable.cpp/h` | `FRunnable` — dedicated network thread, socket I/O, header/payload parsing |
| `UE_Plugin/.../LiveSyncQueue.h` | Bounded MPSC `FLiveSyncQueue` (128 entry cap, drop-oldest, atomic count) |
| `UE_Plugin/.../SyncTypes.h` | `FSyncTransformState`, `FPacketHeaderV3`, `FPacketHeader` (V2), protocol constants, log category |

## Network Protocol

- **Transport**: TCP, little-endian binary packing. No packet reassembly layer — ordered/delivery assumed.
- **Port**: 57000 on both sides. Blender fallback in `sync.py:698`.
- **Magic**: `0x4C56534D` ("ULSM").
- **Header**: 24 bytes V3 (`<I H B B Q I I`): Magic, Version, PacketType, Flags, SequenceId, PacketSize, ObjectCount. V2 legacy is 22 bytes.
- **Object sizes**: V3 TRANSFORM/CREATE = 80 bytes (16 GUID + 12 loc + 16 rot + 12 scl + 8 timestamp + 16 parent GUID). V3 DELETE = 16 bytes (GUID only). V2 object = 56 bytes.
- **Packet types**: TRANSFORM(0x01), HIERARCHY(0x02), CREATE(0x03), DELETE(0x04), HEARTBEAT(0x07).
- **Flags**: `PF_HasLocalTransform(0x01)`, `PF_FullSnapshot(0x02)`.

### GUID Identity

- Generated as `uuid.uuid4().hex` (32-char hex) on Blender, stored as `obj["ue_guid"]` custom property.
- V3 transmits as 4× uint32 LE — no hex string roundtrip, zero allocation.
- Collision detection: `ensure_unique_guid()` regenerates GUID when `obj.copy()` inherits it.
- UE side: actors tagged with `LiveSync_GUID=<guid>`. `BuildActorCache()` scans world; `OnActorSpawned`/`OnActorDestroyed` maintains incrementally.

### Heartbeat & Reconnection

- Blender sends heartbeat (type 0x07, empty payload) every 5 seconds.
- UE: 15s heartbeat timeout (`UE.LiveSync.HeartbeatTimeout`). `LastHeartbeatTime` updated on 0x07. Disconnects on timeout.
- Reconnect: Blender background thread detects socket error → exponential backoff (0.5s–10s max) → reconnects → sends full snapshot `(PF_FullSnapshot)`.
- UE clears `TransformStates` on `PF_FullSnapshot` flag. No stale state survives reconnect.

## Threading Rules

- **Blender main thread only**: `bpy` API calls, scene iteration, diff detection, serialization → enqueue. Never block here.
- **Blender daemon thread**: `socket.sendall()` only. Dequeues from `queue.Queue(maxsize=256)`. Handles reconnect with exponential backoff.
- **UE network thread**: `LiveSyncRunnable::Run()`. `Socket->Wait(10ms)` + `Socket->Recv()` header/payload in chunks. Validates magic/version/size. Enqueues `FLiveSyncPacket` to bounded MPSC queue. Must not store or retain raw `UObject*` across frames. Sets `bThreadExited=true` on exit.
- **UE game thread**: `UUELiveSyncSubsystem::Tick()` via `FTSTicker`. Pipeline: `ProcessQueuedPackets()` → `EvictStaleTransformStates()` (60s TTL) → `InterpolateTransforms()` → `SetActorTransform()`. All `UObject` mutations here.
- **Shutdown order**: `Runnable->Stop()` → `Socket->Close()` (unblocks `Wait`/`Recv`) → `WaitForCompletion()` → delete runnable → `DestroySocket`. Measured <0.1ms total.

## Interpolation (UE Game Thread)

- **Mode 0** (direct-set): zero latency, no smoothing. `Current = Target` each frame.
- **Mode 1** (smooth, default): adaptive `VInterpTo` + `FQuat::Slerp`. Snap threshold `0.1cm` (`UE.LiveSync.InterpSnap`). Velocity-based prediction (12ms ahead). Adaptive speed 8–40 based on distance.
- **Scale snaps directly** to target (no lerp — scale is multiplicative, not additive).
- **Convergence check**: `KINDA_SMALL_NUMBER` skip if already converged. 0.5f distance snap.
- `UE.LiveSync.Threshold.*` CVars on UE side mirror Blender thresholds to filter noisy updates.

## Console Variables

| CVar | Default | Description |
|------|---------|-------------|
| `UE.LiveSync.Port` | 57000 | TCP listen port |
| `UE.LiveSync.HeartbeatTimeout` | 15.0 | Seconds without heartbeat before disconnect |
| `UE.LiveSync.StateTTL` | 60.0 | Seconds before orphaned transform state eviction |
| `UE.LiveSync.Verbose` | 0 | Enable verbose logging (1=on) |
| `UE.LiveSync.InterpMode` | 1 | 0=direct-set, 1=smooth |
| `UE.LiveSync.InterpSnap` | 0.1 | Snap distance cm |
| `UE.LiveSync.Threshold.Location` | 0.05 | Min location change cm |
| `UE.LiveSync.Threshold.Rotation` | 0.002 | Min rotation angular distance |
| `UE.LiveSync.Threshold.Scale` | 0.001 | Min scale change |
| `UE.LiveSync.MaxPacketRate` | 200 | Max packets processed per tick |

## Console Commands

| Command | Description |
|---------|-------------|
| `UE.LiveSync.DumpState` | Print all tracked GUIDs, actors, queue depth |
| `UE.LiveSync.Reset` | Full teardown and restart |
| `UE.LiveSync.Ping` | Print connected/queue/states counters |

## Production Patterns

- **Error scoping**: always `except Exception as e:` — never bare `except:` in Blender Python code.
- **Log category**: `DECLARE_LOG_CATEGORY_EXTERN(LogLiveSync, Log, All)` in `SyncTypes.h`; `DEFINE_LOG_CATEGORY(LogLiveSync)` in `UELiveSyncSubsystem.cpp`. Never use `LogTemp`.
- **Verbose rate limit**: `ShouldLogVerbose()` returns true at most once per 300 frames. Low-frequency events (deletes, metrics) use direct `bEnableVerboseSyncLogs`.
- **Packet rate cap**: `CVarLiveSyncMaxPacketRate` limits per-tick processing. Overflow remains queued for next tick.
- **Seen-this-tick dedup**: `TSet<FGuid> SeenThisTick` passed through `ProcessBinaryPacket()` to discard duplicate GUIDs within same packet batch.
- **Timestamps**: Use `FPlatformTime::Seconds()` for heartbeat, TTL, and metrics. Not `FApp::GetCurrentTime()` or `FDateTime`.
- **Bounded queue**: `FLiveSyncQueue` caps at 128 entries. `Count` is `std::atomic<int32>`. Drop-oldest on overflow. Warning logged at 64+ depth (`UE.LiveSync.QueueWarnThreshold`).
- **Thread watchdog**: `LastActivityTime` atomic set each loop iteration in `LiveSyncRunnable::Run()`. UE Tick checks 30s inactivity threshold and restarts.
- **Socket lifecycle**: socket created by `Accept()` on game thread, transferred to runnable by raw pointer, closed first on shutdown to unblock thread, destroyed only after `WaitForCompletion()`.

## Coordinate Conversion (Blender → UE)

```python
conversion = Matrix((
    (1,  0, 0, 0),
    (0, -1, 0, 0),
    (0,  0, 1, 0),
    (0,  0, 0, 1)
))
ue_matrix = conversion @ matrix_world @ conversion
# location *= 100.0 (meters → cm)
```

## See Also

- `patterns.md` — reusable protocol, threading, and lifecycle patterns
- `examples.md` — code examples for runnable, subsystem, serialization
