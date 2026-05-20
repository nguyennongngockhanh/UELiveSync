# System Overview

UELiveSync is a real-time synchronization system between Blender and Unreal Engine 5.7 using TCP-based communication and GUID-based object identity mapping.

## Layers

```
BLENDER (Python)
┌─────────────────────────────────────────────────────────────┐
│  DATA CAPTURE LAYER  (sync.py)                              │
│  ├── Timer-driven scene polling (0.016s interval)           │
│  ├── Object iteration + GUID assignment                     │
│  ├── Transform extraction + coordinate conversion           │
│  └── Change detection via threshold comparison              │
│                                                             │
│  SERIALIZATION LAYER  (network.py)                          │
│  ├── struct.pack binary encoding per object                 │
│  └── Packet header assembly                                 │
│                                                             │
│  TRANSPORT LAYER (network.py, MAIN THREAD)                  │
│  └── socket.sendall()  [⚠ blocking]                         │
└─────────────────────────────────────────────────────────────┘
                          │ TCP Stream (:5000)
                          ▼
UNREAL ENGINE 5.7 (C++)
┌─────────────────────────────────────────────────────────────┐
│  TRANSPORT LAYER (Network Thread)                           │
│  ├── LiveSyncRunnable::Run()                                │
│  ├── Socket::Wait + Recv (header+payload)                   │
│  └── Enqueue FLiveSyncPacket → Mpsc Queue                   │
│                                                             │
│  PARSING LAYER (Game Thread)                                │
│  ├── ProcessBinaryPacket()                                  │
│  ├── GUID hex-string roundtrip [⚠ overhead]                 │
│  └── UpdateTargetTransform()                                │
│                                                             │
│  GAME THREAD APPLICATION LAYER                              │
│  ├── InterpolateTransforms()                                │
│  ├── Guid → Actor resolution                                │
│  ├── Adaptive interp + prediction                           │
│  └── SetActorTransform(Current)                             │
│                                                             │
│  ACTOR CACHE (Game Thread)                                  │
│  └── TMap<FGuid, TWeakObjectPtr<AActor>>, rebuilt every 5s │
└─────────────────────────────────────────────────────────────┘
```

## Key Files

| File | Role |
|------|------|
| `Blender_Addon/__init__.py` | Blender addon registration & UI |
| `Blender_Addon/sync.py` | Core sync logic, scene iteration, diff detection |
| `Blender_Addon/network.py` | TCP client, serialization, send pipeline |
| `UE_Plugin/.../LiveSyncRunnable.cpp/h` | Dedicated network thread |
| `UE_Plugin/.../LiveSyncQueue.h` | Thread-safe packet buffer |
| `UE_Plugin/.../UELiveSyncSubsystem.cpp/h` | Main orchestrator on Game Thread |
| `UE_Plugin/.../SyncTypes.h` | Shared data structures |

## Protocol

Binary TCP, port 5000. Header: Magic(4)+Ver(2)+Seq(8)+Size(4)+Count(4) = 22 bytes. Per object: GUID(16)+Loc(12)+Rot(16)+Scl(12) = 56 bytes.
