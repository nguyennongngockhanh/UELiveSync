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
│  ├── socket.sendall()  [⚠ BLOCKS]   │
│  └── reconnect() [time.sleep(0.5)]   │
└──────────────────────────────────────┘

UNREAL ENGINE
┌──────────────────────────────────────┐
│  NETWORK THREAD                      │
│  │ LiveSyncRunnable::Run()           │
│  │ Socket::Wait/Recv                 │
│  │ Validate → Enqueue                │
│  └────────────────────────────────    │
│                                      │
│  GAME THREAD                         │
│  │ UUELiveSyncSubsystem::Tick()      │
│  │ ├── Accept connection             │
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

| Rule | Status | Detail |
|------|--------|--------|
| No UE object mutation on network thread | ✅ Compliant | Network thread only reads socket, writes to queue |
| No blocking operations on Game Thread | ✅ Compliant | All socket I/O on network thread |
| Queue must be thread-safe | ✅ Compliant | TQueue<Mpsc> is lock-free |
| Socket must not be used after thread termination | ⚠️ Fragile | Stop() → WaitForCompletion() → close socket. If Recv hangs, thread doesn't exit |
| No concurrent access to ActorCache | ⚠️ Exists | .Empty() in BuildActorCache while Tick iterates it |

## Critical Section: Socket Ownership

The `ConnectionSocket` `FSocket*` is:
1. Created by `Accept()` on the game thread
2. Passed to `FLiveSyncRunnable` constructor (raw pointer)
3. Used by network thread in `Run()` for `Wait()` and `Recv()`
4. Closed/destroyed by game thread in `StopNetworkThread()`

**Stop sequence**: `NetworkRunnable->Stop()` → `NetworkThread->WaitForCompletion()` → `delete NetworkThread` → `delete NetworkRunnable` → `ConnectionSocket->Close()` → `DestroySocket(ConnectionSocket)`.

This is correct as long as `WaitForCompletion()` completes — but if `Recv()` is blocked indefinitely on a broken socket, the thread never exits.

## Proposed Improvement

Move socket close to a shutdown callback triggered from the network thread itself, or use socket shutdown signals (`shutdown(SHUT_RDWR)`) before closing.
