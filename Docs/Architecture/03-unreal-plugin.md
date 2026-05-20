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
    │   ├── LiveSyncQueue.h               Thread-safe queue wrapper
    │   └── SyncTypes.h                   Shared structs/constants
    └── Private/
        ├── UELiveSync.cpp                Module implementation
        ├── UELiveSyncSubsystem.cpp       Core orchestration
        └── LiveSyncRunnable.cpp          Network thread implementation
```

## Module Dependencies

- Core, CoreUObject, Engine (public)
- Sockets, Networking (private) — TCP I/O
- Json, JsonUtilities (private) — future use

## Thread Model

### Thread 1 — Network Thread (LiveSyncRunnable)
- Created by `FRunnableThread::Create()` in `StartNetworkThread()`
- Runs `LiveSyncRunnable::Run()` loop:
  - `Socket->Wait(Read, 100ms)` — blocking wait with timeout
  - `Socket->Recv()` header + payload in chunks
  - Validate Magic, Version, Size, Alignment
  - Enqueue `FLiveSyncPacket` to Mpsc `TQueue`
- Stopped by `Stop()` (sets `bRunThread=false`) then `WaitForCompletion()`

### Thread 2 — Game Thread (UELiveSyncSubsystem)
- FTSTicker tick at 0.0f (every frame)
- Per tick:
  1. Accept new connections if none active
  2. Check stale connection state
  3. Rebuild ActorCache every 5s
  4. `ProcessQueuedPackets()` — drain all queue entries
  5. `InterpolateTransforms()` — apply to actors

## Data Flow

```
TCP packet arrives
  → LiveSyncRunnable (network thread)
    → read header + payload
    → validate
    → FLiveSyncPacket { RawData, ReceiveTime }
    → PacketQueue.Enqueue()
      ↓
  → UUELiveSyncSubsystem::Tick() (game thread)
    → ProcessQueuedPackets()
      → ProcessBinaryPacket() for each
        → Memcpy header
        → Validate magic/version/sequence
        → Loop objects:
          → Parse GUID (hex string roundtrip)
          → Memcpy FVector/FQuat
          → UpdateTargetTransform(Guid, Loc, Rot, Scl)
    → InterpolateTransforms()
      → For each Guid in TransformStates
        → FindActorFast (ActorCache lookup)
        → Compute predicted position
        → VInterpTo / Slerp interpolation
        → Actor->SetActorTransform()
```

## Key Data Structures

### ActorCache
```
TMap<FGuid, TWeakObjectPtr<AActor>> ActorCache
```
- Built by scanning `TActorIterator<AActor>` for tags matching `LiveSync_GUID=...`
- Rebuilt every 5 seconds (full .Empty() + rebuild)
- Weak pointers allow GC without stale entries (though entries remain in map)

### TransformStates
```
TMap<FGuid, FSyncTransformState> TransformStates
```
- FSyncTransformState holds Current/Target/Velocity for loc/rot/scale
- Adaptive interpolation speed (8-24 based on distance)
- Prediction: Target + Velocity * 0.012s

### PacketQueue
```
TQueue<FLiveSyncPacket, EQueueMode::Mpsc> PacketQueue
```
- Multi-producer (network thread) single-consumer (game thread)
- No capacity limit — unbounded growth risk

## Current Limitations

1. **Heavy logging**: UE_LOG(LogTemp, Warning) per object per packet
2. **GUID roundtrip**: Binary → hex FString → FGuid::ParseExact
3. **Unbounded queue**: No backpressure or drop policy
4. **Cache rebuild**: Full TActorIterator every 5s
5. **Interpolation lag**: Actor trails target by 3-5 frames
6. **No dedup**: All queue entries processed even if GUID already updated
