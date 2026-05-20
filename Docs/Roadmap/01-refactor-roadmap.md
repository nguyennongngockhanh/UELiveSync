# Roadmap

## Phase Overview

| Phase | Focus | Timeline | Risk |
|-------|-------|----------|------|
| 0 | Diagnostics & Measurement | 1-2 days | None |
| 1 | Quick Wins (no protocol change) | 2-3 days | Low |
| 2 | Core Performance Fixes | 3-5 days | Medium |
| 3 | Hierarchy Sync (Phase 4 port) | 3-5 days | High |
| 4 | Hardening & Polish | 2-3 days | Low |

---

## Phase 0 — Diagnostics

**Goal**: Establish baseline metrics before making changes.

### Tasks

- [ ] Add Blender-side timing: measure `check_updates()` duration per tick
- [ ] Add UE-side scope timers in `ProcessBinaryPacket()` and `InterpolateTransforms()`
- [ ] Measure end-to-end latency: packet receive → transform applied
- [ ] Measure queue depth over time (is backlog growing?)
- [ ] Profile with 10, 100, 500, 1000 objects in scene
- [ ] Measure log I/O overhead (disable UE_LOG and compare frame times)

### Output

- Baseline latency numbers (p50, p95, p99)
- Bottleneck identification from data, not intuition
- Decision: which optimization targets matter most

---

## Phase 1 — Quick Wins

**Goal**: Immediate latency and safety improvements without protocol changes.

### 1a — Remove Per-Object UE_LOG(Warning)

**Files**: `UELiveSyncSubsystem.cpp:512-518, 873-878`

```cpp
// Before (every object, every packet):
UE_LOG(LogTemp, Warning, TEXT("Received GUID=%s"), ...);
UE_LOG(LogTemp, Warning, TEXT("Applying Transform To %s"), ...);

// After: rate-limited summary log:
static int PacketCount = 0, ObjectCount = 0;
PacketCount++; ObjectCount += Header.ObjectCount;
if (PacketCount % 60 == 0)
    UE_LOG(LogTemp, Log, TEXT("Sync: %d packets, %d objects/sec"), PacketCount, ObjectCount);
```

**Risk**: None. Pure performance improvement.

### 1b — TCP_NODELAY on Blender Socket

**File**: `network.py:106`

```python
self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
```

**Risk**: Low. Standard optimization for real-time protocols.

### 1c — TCP_NODELAY on UE Socket

**File**: `UELiveSyncSubsystem.cpp:166-198`

```cpp
FSocket* NewSocket = ListenerSocket->Accept(...);
if (NewSocket) {
    NewSocket->SetNoDelay(true);
    // ... rest of connection handling
}
```

**Risk**: Low.

### 1d — Move Blender Network I/O to Background Thread

**File**: `network.py`

```python
import threading, queue

class LiveSyncClient:
    def __init__(self):
        self.send_queue = queue.Queue(maxsize=256)
        self.thread = threading.Thread(target=self._sender_loop, daemon=True)
        self.thread.start()

    def _sender_loop(self):
        while True:
            data = self.send_queue.get()
            if data is None:  # shutdown sentinel
                break
            try:
                self.sock.sendall(data)
            except:
                self.reconnect()

    def send_packet(self, objects_data):
        packet = self._build_packet(objects_data)
        try:
            self.send_queue.put_nowait(packet)
        except queue.Full:
            pass  # drop oldest, or log warning
```

**Risk**: Medium. Thread safety on `_client` singleton, socket reconnect race.
- Use `threading.Lock` around connect/close
- Sentinel value for clean shutdown

### 1e — Bounded Packet Queue in UE

**File**: `LiveSyncQueue.h`

```cpp
class FLiveSyncQueue {
    static constexpr int32 MaxQueueSize = 128;
    void Enqueue(const FLiveSyncPacket& Packet) {
        if (Queue.Size() >= MaxQueueSize) {
            FLiveSyncPacket Dummy;
            Queue.Dequeue(Dummy);  // drop oldest
        }
        Queue.Enqueue(Packet);
    }
};
```

**Risk**: Low. Prevents memory leak.

### 1f — Bump Protocol Version

**File**: `network.py:11`, `SyncTypes.h:142-144`

Add type byte to header for future extensibility. Keep backward compat by checking version.

**Risk**: Low — unused field until Phase 3.

---

## Phase 2 — Core Performance Fixes

**Goal**: Fix fundamental CPU waste in UE processing pipeline.

### 2a — Direct Binary GUID Parsing

**Files**: `network.py:33-35`, `UELiveSyncSubsystem.cpp:489-510`

**Problem**: Blender sends hex bytes, UE converts bytes → hex FString → FGuid::ParseExact.

**Solution**: Transmit GUID as 4 × uint32 LE. Read directly into FGuid struct fields.

```python
# Blender: send as 4 uint32
guid = uuid.uuid4()
payload.extend(struct.pack("<IIII",
    guid.time_low, guid.time_mid, guid.time_hi_and_version, guid.clock_seq_hi_variant))
```

```cpp
// UE: read directly
uint32 GuidParts[4];
FMemory::Memcpy(GuidParts, Ptr, 16);
FGuid Guid(GuidParts[0], GuidParts[1], GuidParts[2], GuidParts[3]);
Ptr += 16;
```

**Risk**: Medium. Must match byte order exactly. Both sides are LE.

### 2b — Last-Write-Wins Dedup in Batch

**File**: `UELiveSyncSubsystem.cpp:361-373`

```cpp
void ProcessQueuedPackets() {
    TMap<FGuid, FTransformUpdate> LatestUpdates;
    FLiveSyncPacket Packet;
    while (PacketQueue.Dequeue(Packet)) {
        // Extract all GUIDs and transforms
        // LatestUpdates[Guid] = Transform  (overwrites older)
    }
    for (auto& [Guid, Transform] : LatestUpdates) {
        UpdateTargetTransform(Guid, Transform);
    }
}
```

**Risk**: Low. Only the last update per GUID per tick matters.

### 2c — Incremental Actor Cache

**File**: `UELiveSyncSubsystem.cpp:891-974`

**Problem**: Full `.Empty()` + `TActorIterator` rebuild every 5s drops all lookups temporarily.

**Solution**: Use world lifecycle callbacks.

```cpp
void BuildActorCache() {
    // Initial build only
    for (TActorIterator<AActor> It(World); It; ++It) {
        TryCacheActor(*It);
    }
    // Subscribe to spawn/destroy
    World->AddOnActorSpawnedHandler(FOnActorSpawned::CreateUObject(this, &OnActorSpawned));
    OnActorDestroyed_Handle = World->OnActorDestroyed.AddUObject(this, &OnActorDestroyed);
}

void OnActorSpawned(AActor* Actor) { TryCacheActor(Actor); }
void OnActorDestroyed(AActor* Actor) {
    // Find and remove stale entry
}
```

**Risk**: Medium. Need to handle level transitions, world tearing.

### 2d — Simplify Interpolation

**File**: `UELiveSyncSubsystem.cpp:763-883`

**Problem**: Actor trails target by 3-5 frames due to interpolation.

**Option A** (recommended): Remove interpolation. Set actor directly:

```cpp
void ApplyTransform(const FGuid& Guid, const FVector& Loc, const FQuat& Rot, const FVector& Scl) {
    AActor* Actor = FindActorFast(Guid);
    if (!Actor) return;
    Actor->SetActorTransform(FTransform(Rot, Loc, Scl));
}
```

**Option B**: Keep interpolation with threshold snapping:

```cpp
if (FVector::Dist(Current, Target) < 0.1f) {
    Current = Target;  // snap
}
```

**Risk**: High for option A (visual behavior change). Test with slow/fast movement.

### 2e — Optimized Blender Scene Iteration

**File**: `sync.py:177`

**Problem**: Full `bpy.data.objects` scan every 16ms.

**Solution**: Maintain tracked object list and update on scene changes via handlers:

```python
def check_updates():
    # Use instead of bpy.data.objects:
    tracked_objects = get_tracked_objects()  # cached list
    changed = detect_changes(tracked_objects)
    if changed:
        send_objects([serialize(g, t) for g, t in changed])
```

Or use `bpy.app.handlers.depsgraph_update_post` instead of timer polling.

**Risk**: Medium. Depsgraph approach may miss non-deforming transforms.

---

## Phase 3 — Hierarchy Sync

**Goal**: Parent-child relationship preservation.

### 3a — Local + World Transform in Packet

**Files**: `sync.py:93-155`, `UELiveSyncSubsystem.cpp:380-604`

Send both `matrix_world` (world) and `matrix_local` (relative to parent) for each object.

### 3b — Parent-Child Reconstruction

**UE**: On receiving hierarchy info:
1. Sort objects by hierarchy depth (parents before children)
2. Apply world transform to root objects
3. Apply local transform to children relative to their parent's world transform

### 3c — Create/Delete Packets

Blender sends `CREATE` packet when tagged object appears, `DELETE` when removed. UE spawns/destroys corresponding actors.

### 3d — Heartbeat

Periodic heartbeat packets (every 5s) to detect stale connections.

**Risk**: High. Edge cases: cyclic hierarchies, missing parents, level transition during sync.

---

## Phase 4 — Hardening

- Configurable port, thresholds via Blender addon preferences
- UE-side UI for connection status
- Clean stale TransformStates (TTL-based eviction)
- Multi-connection support (re-accept after disconnect)
- Full initial state snapshot on connect
- Crash recovery: reconnection with state re-sync
