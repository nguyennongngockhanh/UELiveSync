# System Overview

UELiveSync is a real-time synchronization system between Blender and Unreal Engine 5.7 using TCP-based communication and GUID-based object identity mapping.

## Layers

```
BLENDER (Python)
┌─────────────────────────────────────────────────────────────┐
│  DATA CAPTURE LAYER  (sync.py)                              │
│  ├── Timer-driven scene polling                             │
│  ├── Object iteration + GUID assignment                     │
│  ├── Transform extraction + coordinate conversion           │
│  └── Change detection via threshold comparison              │
│                                                             │
│  SERIALIZATION LAYER  (network.py)                          │
│  ├── V3: struct.pack binary encoding per object             │
│  ├── V3: Packet header with type/flags fields               │
│  └── V2 legacy path preserved                               │
│                                                             │
│  TRANSPORT LAYER (network.py, BACKGROUND THREAD)            │
│  ├── Threaded sender loop (queue.Queue)                     │
│  ├── Non-blocking enqueue from main thread                  │
│  └── socket.sendall() on background thread                  │
└─────────────────────────────────────────────────────────────┘
                          │ TCP Stream (:5000)
                          ▼
UNREAL ENGINE 5.7 (C++)
┌─────────────────────────────────────────────────────────────┐
│  TRANSPORT LAYER (Network Thread)                           │
│  ├── LiveSyncRunnable::Run()                                │
│  ├── Socket::Wait(10ms) + Recv (header+payload)            │
│  ├── V3 header parsing (magic/version/type/flags)           │
│  ├── V2 legacy path preserved                               │
│  └── Enqueue FLiveSyncPacket → bounded MPSC queue            │
│                                                             │
│  PARSING LAYER (Game Thread)                                │
│  ├── ProcessBinaryPacket() — V3/V2 dispatch                │
│  ├── Direct binary GUID (4× uint32, no hex roundtrip)      │
│  ├── Heartbeat detection (type 0x07)                        │
│  └── UpdateTargetTransform()                                │
│                                                             │
│  GAME THREAD APPLICATION LAYER                              │
│  ├── InterpolateTransforms()                                │
│  ├── GUID → Actor resolution (incremental cache)           │
│  ├── Adaptive interp + prediction                           │
│  ├── Threshold-based convergence snap                       │
│  └── SetActorTransform(Current)                             │
│                                                             │
│  ACTOR CACHE (Game Thread)                                  │
│  ├── TMap<FGuid, TWeakObjectPtr<AActor>>, single build     │
│  ├── Incremental via OnActorSpawned/OnActorDestroyed        │
│  └── HandleCreateObject spawns actors with StaticMesh       │
└─────────────────────────────────────────────────────────────┘
```

## Key Files

| File | Role |
|------|------|
| `Blender_Addon/__init__.py` | Blender addon registration & UI operators |
| `Blender_Addon/sync.py` | Core sync logic, scene iteration, diff detection |
| `Blender_Addon/network.py` | TCP client, serialization, threaded send pipeline |
| `UE_Plugin/.../LiveSyncRunnable.cpp/h` | Dedicated network receive thread |
| `UE_Plugin/.../LiveSyncQueue.h` | Thread-safe bounded MPSC packet buffer |
| `UE_Plugin/.../UELiveSyncSubsystem.cpp/h` | Main orchestrator on Game Thread |
| `UE_Plugin/.../SyncTypes.h` | Shared data structures, protocol constants |

## Protocol

Binary TCP, port 5000. Dual-version: V2 (legacy, 22-byte header) and V3 (24-byte header with type/flags fields). V3 adds direct uint32 GUID (no hex roundtrip), CREATE/UPDATE/DELETE/HEARTBEAT packet types, timestamp field, and parent GUID for future hierarchy support.
