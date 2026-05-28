# Unreal Engine Plugin Architecture

## File Structure

```
UE_Plugin/UELiveSync/
├── UELiveSync.uplugin
└── Source/UELiveSync/
    ├── UELiveSync.Build.cs
    ├── Public/
    │   ├── UELiveSync.h                  Module interface
    │   ├── UELiveSyncSubsystem.h         World subsystem declaration
    │   ├── LiveSyncRunnable.h            Network thread declaration
    │   ├── LiveSyncQueue.h               Thread-safe bounded queue
    │   └── SyncTypes.h                   Shared structs/constants
    └── Private/
        ├── UELiveSync.cpp                Module implementation
        ├── UELiveSyncSubsystem.cpp       Core orchestration (1600+ lines)
        └── LiveSyncRunnable.cpp          Network thread implementation
```

## Module Dependencies

- Core, CoreUObject, Engine (public)
- Sockets, Networking (private) — TCP I/O

## Thread Model

### Network Thread (LiveSyncRunnable)
- Created by `FRunnableThread::Create()` in `StartNetworkThread()`
- Runs `LiveSyncRunnable::Run()` loop:
  - `Socket->Wait(Read, 10ms)` — blocking wait with short timeout
  - `Socket->Recv()` header in chunks (up to 24 bytes for V3)
  - Version-dispatch: V2 (22-byte) or V3 (24-byte) parser
  - `Socket->Recv()` payload in chunks
  - Validate Magic, Version, Size
  - Enqueue `FLiveSyncPacket` to bounded MPSC queue
- Stopped by `Stop()` (sets `bRunThread`) + socket close + `WaitForCompletion()`

### Game Thread (UELiveSyncSubsystem)
- `FTSTicker` tick every frame
- Per tick:
  1. Accept new connections if none active
  2. Check stale connection state
  3. Check for network thread exit (via `bThreadExited` flag)
  4. Check heartbeat timeout (15s) — resets `LastHeartbeatTime` on 0x07 packets
  5. `ProcessQueuedPackets()` — drain all queue entries via `ProcessBinaryPacket()`
  6. `EvictStaleTransformStates()` — remove entries older than 60s TTL
  7. `InterpolateTransforms()` — apply interpolation to all tracked actors
  8. `LogRuntimeMetrics()` — every 60s in verbose mode

## Data Flow

```
TCP packet arrives
  → LiveSyncRunnable (network thread)
    → read header (Wait + Recv loop)
    → detect V2/V3 version
    → read payload (Recv loop)
    → validate magic
    → FLiveSyncPacket { RawData, ReceiveTime }
    → PacketQueue.Enqueue()
      ↓
  → UUELiveSyncSubsystem::Tick() (game thread)
    → ProcessQueuedPackets()
      → ProcessBinaryPacket() for each
        → V3: FMemory::Memcpy into FPacketHeaderV3
        → Validate magic/version/type/sequence
        → Heartbeat check (type 0x07 → update LastHeartbeatTime)
        → Loop objects:
          → Direct uint32 GUID read into FGuid (no string alloc)
          → Memcpy FVector3f, FQuat4f, FVector3f
          → V3: timestamp (double) + parent GUID (16 bytes)
          → HandleCreateObject if type 0x03
          → UpdateTargetTransform(Guid, Loc, Rot, Scl)
    → EvictStaleTransformStates()
      → 60s TTL on TransformStates entries
      → Removes stale entries + corresponding ActorCache entries
    → InterpolateTransforms()
      → For each Guid in TransformStates
        → FindActorFast (ActorCache lookup)
        → Check convergence thresholds (KINDA_SMALL_NUMBER)
        → Compute predicted position (velocity-based)
        → VInterpTo / Slerp interpolation with adaptive speed
        → SetActorTransform(Current)
    → LogRuntimeMetrics() [every 60s in verbose mode]
      → States=N Cache=N Queue=N Connected=1/0
```

## Key Data Structures

### ActorCache
```
TMap<FGuid, TWeakObjectPtr<AActor>> ActorCache
```
- Built once during `Initialize()` via `BuildActorCache()`
- Maintained incrementally via `OnActorSpawned`/`OnActorDestroyed` handlers
- Weak pointers allow GC; stale entries removed by `OnActorDestroyed`
- Actors tagged with `LiveSync_GUID=<guid>` for identity across sessions

### TransformStates
```
TMap<FGuid, FSyncTransformState> TransformStates
```
- `FSyncTransformState` holds Current/Target/Velocity for loc/rot/scale
- Adaptive interpolation speed (8–24 based on distance)
- Prediction: Target + Velocity × 0.012s
- Dual snap threshold: `KINDA_SMALL_NUMBER` skip (already converged) or `0.5f` distance snap
- Cleared on disconnect/reconnect

### PacketQueue (FLiveSyncQueue)
```
TQueue<FLiveSyncPacket, EQueueMode::Mpsc> + std::atomic<int32> Count
```
- Bounded to 128 entries (drop-oldest on overflow)
- Multi-producer (network thread) single-consumer (game thread)
- `Clear()` drains all entries (called on disconnect)

### Verbose Logging Control
```
static bool bEnableVerboseSyncLogs = false;
int32 VerboseFrameCounter;  // increments each Tick
bool ShouldLogVerbose() const;
```
- Per-frame/per-packet diagnostic logs gated behind `bEnableVerboseSyncLogs`
- Rate-limited to at most every 300 frames for high-frequency paths (`ShouldLogVerbose()`)
- Low-frequency events (delete, metrics) use direct `bEnableVerboseSyncLogs` check
- Default: disabled — zero per-frame log output

### Delete Lifecycle Logging
Logged in `HandleDeleteObject()` when `bEnableVerboseSyncLogs` is true:
```
[Delete] GUID=... Actor=Cube Removed=1 StaleCache=0
```
- `Removed=1`: actor was found and destroyed; `0`: actor was already gone
- `StaleCache=1`: stale ActorCache entry existed (cache miss already detected); `0`: clean state
- Respects existing verbose toggle — no rate limiting (deletes are rare events)

### Runtime Metrics Logging
Logged every 60s via `LogRuntimeMetrics()` when `bEnableVerboseSyncLogs` is true:
```
[Metrics] States=42 Cache=40 Queue=3 Connected=1
```
- `States`: `TransformStates.Num()` — active tracked transforms
- `Cache`: `ActorCache.Num()` — active cached actor references
- `Queue`: `PacketQueue.Size()` — pending packets in bounded queue (0 typical)
- `Connected`: 1 if socket is connected, 0 otherwise
- Time-based interval, no allocations in hot path

## Lifecycle

### Initialize
1. `StartServer()` — create TCP listener on port 5000
2. `BuildActorCache()` — scan world for actors with LiveSync_GUID tags
3. Register `OnActorSpawned`/`OnActorDestroyed` handlers
4. Register `Tick()` on `FTSTicker`

### Connection
1. `Tick()` → `ListenerSocket->Accept()` → new `FSocket*`
2. `SetNoDelay(true)`, `ConnectionSocket = NewSocket`
3. `StartNetworkThread()` → `FRunnableThread::Create(FLiveSyncRunnable)`

### Disconnection
1. Network thread `Recv()` returns 0 → sets `bThreadExited = true`
2. `Tick()` detects `bThreadExited` → calls `StopNetworkThread()`
3. `StopNetworkThread()`: stop runnable → close socket → wait for thread → delete → clear state

### Deinitialize
1. `StopNetworkThread()` — closes socket, joins thread, clears state
2. Remove `FTSTicker` handle
3. Remove `OnActorSpawned`/`OnActorDestroyed` handlers
4. Destroy `ListenerSocket`
