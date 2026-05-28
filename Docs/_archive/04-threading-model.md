# Threading Model

## Current Thread Layout

```
BLENDER
┌──────────────────────────────────────┐
│  MAIN THREAD                         │
│  ├── bpy.app.timers callback         │
│  ├── scene iteration (bpy.data)      │
│  ├── transform extraction            │
│  ├── serialization                   │
│  └── socket.send() — enqueue only ⚡│
│                                      │
│  SENDER THREAD (daemon)              │
│  ├── queue.Queue.get() — blocking    │
│  ├── socket.sendall()                │
│  └── shutdown on None sentinel       │
└──────────────────────────────────────┘

UNREAL ENGINE
┌──────────────────────────────────────┐
│  NETWORK THREAD                      │
│  │ LiveSyncRunnable::Run()           │
│  │ Socket::Wait(10ms)/Recv           │
│  │ Validate → Enqueue (bounded)      │
│  │ Set bThreadExited on exit         │
│  └────────────────────────────────    │
│                                      │
│  GAME THREAD                         │
│  │ UUELiveSyncSubsystem::Tick()      │
│  │ ├── Accept connection             │
│  │ ├── Detect thread exit            │
│  │ ├── Heartbeat timeout             │
│  │ ├── ProcessQueuedPackets()        │
│  │ ├── InterpolateTransforms()       │
│  │ └── SetActorTransform()           │
│  └────────────────────────────────    │
│                                      │
│  RENDER THREAD                       │
│  │ Implicit UE rendering pipeline    │
│  └────────────────────────────────    │
└──────────────────────────────────────┘
```

## Thread Safety Rules

| Rule | Status | Mechanism |
|------|--------|-----------|
| No UE object mutation on network thread | ✅ | Network thread only reads socket, writes to queue |
| No blocking operations on Game Thread | ✅ | All socket I/O on network thread |
| Queue must be thread-safe | ✅ | `TQueue<Mpsc>` is lock-free + bounded |
| Socket not used after thread termination | ✅ | Close socket → thread exits → WaitForCompletion → destroy |
| No concurrent ActorCache access | ✅ | ActorCache only accessed from game thread |
| No concurrent TransformStates access | ✅ | TransformStates only accessed from game thread (Tick) |
| Thread exit detection | ✅ | `FThreadSafeBool bThreadExited` set in Run() epilogue |

## Game Thread Call Chains

All packet handlers and state mutations run on the game thread via `Tick()`:

```
Tick()
├── Accept connection              (ListenerSocket->Accept)
├── Stale connection check         (GetConnectionState)
├── Thread exit detection          (bThreadExited)
├── Heartbeat timeout              (FPlatformTime check)
├── ProcessQueuedPackets()
│   └── ProcessBinaryPacket()
│       ├── HandleCreateObject()   (World->SpawnActor)
│       ├── HandleDeleteObject()   (Actor->Destroy + cache removal)
│       ├── UpdateTargetTransform() (TransformStates update)
│       └── [logging]             (bEnableVerboseSyncLogs gate)
├── EvictStaleTransformStates()    (60s TTL removal)
├── InterpolateTransforms()        (Actor->SetActorTransform)
└── LogRuntimeMetrics()            (every 60s, verbose only)
```

## Critical Section: Socket Ownership

The `ConnectionSocket` `FSocket*` lifecycle:

1. **Created**: `ListenerSocket->Accept()` on game thread
2. **Transferred**: Raw pointer passed to `FLiveSyncRunnable` constructor
3. **Used**: Network thread calls `Wait()` and `Recv()` exclusively
4. **Closed**: Game thread calls `Socket->Close()` in `StopNetworkThread()`
5. **Destroyed**: `ISocketSubsystem::DestroySocket()` after thread has exited

## Shutdown Sequence

```
StopNetworkThread() called from game thread:
  │
  ├── 1. NetworkRunnable->Stop()
  │       └── bRunThread = false (atomic)
  │
  ├── 2. ConnectionSocket->Close()
  │       └── Unblocks Wait()/Recv() on network thread
  │          (network thread exits within microseconds)
  │
  ├── 3. NetworkThread->WaitForCompletion()
  │       └── Returns immediately (thread already exiting)
  │
  ├── 4. delete NetworkThread
  │
  ├── 5. delete NetworkRunnable
  │
  ├── 6. DestroySocket(ConnectionSocket)
  │
  └── 7. PacketQueue.Clear()
         TransformStates.Empty()
         LastHeartbeatTime = 0.0
         LastSequenceId = 0
```

**Total shutdown time: <0.1ms** (verified: 0.05–0.08ms measured)

## Thread Exit Paths

The network thread can exit via three paths:

| Path | Condition | Latency |
|------|-----------|---------|
| `Recv()` returns 0 | Peer closed connection | Immediate |
| `Recv()` returns error | Socket closed or error | Immediate |
| `bRunThread` check fails | `Stop()` called + Wait(10ms) timeout | Up to 10ms |

All paths set `bThreadExited = true` before returning.

## Reconnection Safety

On reconnect, all stale state is cleared:
- `PacketQueue.Clear()` — no stale packets from old connection
- `TransformStates.Empty()` — no stale transforms
- `LastHeartbeatTime = 0.0` — no false heartbeat timeout
- `LastSequenceId = 0` — new connection sequences accepted
- `ConnectionSocket = nullptr` → old socket destroyed, new socket accepted
