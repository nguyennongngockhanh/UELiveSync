# Issues Database

> All issues identified during architecture review (2026-05-20).
> Status updated 2026-05-21 — 13 of 18 issues resolved in Phases 3.4–3.5.

## Severity Levels

| Level | Meaning | Action Required |
|-------|---------|----------------|
| 🔴 Critical | Causes data loss, crash, or unacceptable latency | Fix before production |
| 🟠 High | Significant performance or correctness concern | Fix in Phase 1-2 |
| 🟡 Medium | Minor perf impact or missing feature | Fix in Phase 2-3 |
| 🟢 Low | Cosmetic or nice-to-have | Phase 4 or backlog |

---

## Critical Issues

### C1 — Blender main thread blocking on socket.sendall()

**Status: ✅ RESOLVED** (Phase 3.4 — background sender thread)

**File**: `Blender_Addon/network.py:218`

**Description**: `socket.sendall()` is called on the Blender main thread. If the network is slow or disconnected, the entire Blender UI freezes until the call completes or times out (potentially minutes). `reconnect()` compounds this with `time.sleep(0.5)`.

**Impact**: Blender UI freezes during network hiccups. In long sessions (>1h) with periodic disconnects, user experience degrades to unusable.

**Root Cause**: No network I/O thread. The addon uses the main thread for all work including blocking socket operations.

**Fix**: Move socket send to a `threading.Thread` with a `queue.Queue`. Main thread enqueues data; background thread drains and sends.

---

### C2 — GUID hex-string roundtrip on UE game thread

**Status: ✅ RESOLVED** (Phase 3.4 — V3 direct binary GUID)

**File**: `UELiveSyncSubsystem.cpp:489-510`

**Description**: UE receives 16 raw GUID bytes, then formats them into a hex FString character-by-character, then parses that FString back to FGuid. This is binary → string → binary conversion with dynamic allocation.

**Impact**: ~2-5μs overhead per object. At 60fps with 100 objects: 12-30ms CPU on game thread per second.

**Root Cause**: The GUID is stored as a hex string in Blender (`uuid.uuid4().hex`), sent as `bytes.fromhex()`, and UE reverses the process.

**Fix**: Send GUID as 4 × uint32 LE. Read directly into `FGuid` fields (`A, B, C, D`). Zero string allocation.

---

### C3 — UE_LOG(LogTemp, Warning) per object per packet

**Status: ✅ RESOLVED** (Phase 3.4 — behind `bEnableVerboseSyncLogs`)

**Files**: `UELiveSyncSubsystem.cpp:512-518, 873-878`, `LiveSyncRunnable.cpp:110-115`

**Description**: Every object in every packet generates a synchronous UE_LOG call at Warning severity. In development builds, this writes to disk (possibly with flush). At 60fps × 100 objects = 6000 log lines/second.

**Impact**: Synchronous disk I/O on game thread. Significant frame time consumption.

**Root Cause**: Debug logging not reduced for real-time performance.

**Fix**: Move to Verbose severity or rate-limited summary counters.

---

### C4 — Unbounded queue growth (memory leak)

**Status: ✅ RESOLVED** (Phase 3.4 — `MaxQueueSize=128`)

**Files**: `LiveSyncQueue.h`, `LiveSyncRunnable.cpp:277`

**Description**: `TQueue<Mpsc>` has no capacity limit. Network thread pushes unlimited packets. If game thread lags (GC, load, hitch), queue consumes memory proportional to backlog duration × packet rate × object count.

**Impact**: In long sessions (>1h), any game thread stutter causes unbounded memory growth. Potential OOM crash.

**Root Cause**: No backpressure or drop policy in queue design.

**Fix**: Bounded queue (128-256 entries) with drop-oldest on overflow.

---

### C5 — ActorCache full rebuild every 5 seconds

**Status: ✅ RESOLVED** (Phase 3.4 — incremental via event handlers)

**File**: `UELiveSyncSubsystem.cpp:891-974`

**Description**: `BuildActorCache()` calls `ActorCache.Empty()` then `TActorIterator<AActor>(World)`. During the rebuild window (between Empty and iterator completion), all GUID lookups in `InterpolateTransforms()` fail.

**Impact**: Every 5 seconds, all transforms are skipped for some number of frames. Visible as periodic sync interruption.

**Root Cause**: No incremental cache update mechanism.

**Fix**: Use `OnActorSpawned`/`OnActorDestroyed` callbacks for incremental updates. Remove periodic rebuild.

---

### C6 — Tiny read window in ActorCache rebuild

**Status: ✅ RESOLVED** (same incremental fix as C5 — no periodic rebuild exists)

**File**: `UELiveSyncSubsystem.cpp:239-245, 251-254`

**Description**: `BuildActorCache()` (line 241) runs in the same Tick() as `ProcessQueuedPackets()` (line 251) and `InterpolateTransforms()` (line 253). If rebuild happens between these calls, TransformStates are updated but ActorCache is fresh empty → all GUID lookups fail.

**Impact**: Lost transform updates for this frame. Desync until next packet arrives for those GUIDs.

**Root Cause**: Rebuild order and lack of atomic cache swap.

**Fix**: Build into a temporary map, then atomic-swap (or use incremental approach from C5).

---

## High Issues

### H1 — Full bpy.data.objects iteration every 16ms

**Status: ✅ RESOLVED** (Phase 3.5 — `tracked_objects` dict iteration)

**File**: `sync.py:177`

**Description**: Every timer callback iterates ALL objects in `bpy.data.objects`, calling `get_transform()` on each. For large scenes (1000+ objects), `matrix_world.copy()` and decomposition dominate tick time.

**Impact**: CPU spike per frame. Limits scene complexity.

**Root Cause**: No caching or acceleration structure for tracked objects.

**Fix**: Maintain tracked object list with scene change listeners.

---

### H2 — World-space transforms lose hierarchy

**Status: ⏳ PARTIALLY RESOLVED** — parent GUID sent in V3 payload, but UE does not reconstruct parent-child transforms

**File**: `sync.py:93-118`

**Description**: Uses `matrix_world` (world space). Child objects receive world transforms. UE has no parent relationship → cannot reconstruct hierarchy. Parent movement causes child to appear to double-move.

**Impact**: Phase 4 (hierarchy sync) impossible without protocol change. Currently broken for parented objects.

**Root Cause**: Only world transform extracted.

**Fix**: Send both world and local transforms in packet.

---

### H3 — No TCP_NODELAY

**Status: ✅ RESOLVED** (Phase 3.4)

**Files**: `network.py:106`, `UELiveSyncSubsystem.cpp:166-198`

**Description**: Nagle's algorithm buffers small sends to coalesce packets. For real-time sync (transform updates every 16ms), Nagle adds 40ms latency.

**Impact**: 40ms+ additional latency per packet, exceeding the 50ms target.

**Root Cause**: Socket default settings.

**Fix**: `sock.setsockopt(IPPROTO_TCP, TCP_NODELAY, 1)` on Blender; `Socket->SetNoDelay(true)` on UE.

---

### H4 — Interpolation always lags behind target

**Status: ⏳ PARTIALLY RESOLVED** — convergence snap at 0.5f added; full snap-on-convergence via `KINDA_SMALL_NUMBER` skip

**File**: `UELiveSyncSubsystem.cpp:829-863`

**Description**: `Actor->SetActorTransform(CurrentLocation)` where CurrentLocation is interpolated toward TargetLocation. With interp speed 12.0 × delta 0.016 = 0.192 fraction per frame, actor gets ~63% of the way in 5 frames. Always behind.

**Impact**: Visual lag of 3-5 frames (50-80ms). Contradicts the <50ms latency target.

**Root Cause**: Design decision to prioritize smoothness over latency.

**Fix**: Remove interpolation or snap to target when close.

---

### H5 — No dedup in ProcessQueuedPackets

**Status: ✅ RESOLVED** (Phase 3.4 — `SeenThisTick` TSet)

**File**: `UELiveSyncSubsystem.cpp:361-373`

**Description**: All queued packets are processed sequentially. If network thread queued 5 updates for the same GUID before game thread ticked, all 5 are processed — only the last matters.

**Impact**: Wasted CPU on game thread during backlog.

**Root Cause**: No deduplication pass.

**Fix**: Batch-collect latest state per GUID before applying.

---

### H6 — Blender timer double-registration

**Status: ✅ RESOLVED** (Phase 3.4 — `_timer_ref` guard)

**File**: `sync.py:264`

**Description**: `bpy.app.timers.register(lambda: check_updates())` creates a new lambda each call. `start_sync()` called twice → two timers. `timer_running` flag prevents work but timer still fires and returns 0.016.

**Impact**: Leaked timer registration; wasted callback invocations.

**Root Cause**: No guard against multiple registration.

**Fix**: Store timer reference, unregister old before registering new.

---

### H7 — reconnect() blocks 500ms

**Status: ✅ RESOLVED** (Phase 3.4 — reconnect runs on background thread)

**File**: `network.py:129-135`

**Description**: `time.sleep(0.5)` on main thread during reconnect attempt.

**Impact**: UI freeze on reconnect.

**Root Cause**: Blocking sleep in reconnect logic.

**Fix**: Move reconnect to background thread (already planned in C1 fix).

---

## Medium Issues

### M1 — No heartbeat/stale connection detection (Blender)

**Status: ✅ RESOLVED** (Phase 3.5 — time-based heartbeat every 5s, type 0x07)

**File**: `network.py`

**Description**: UE detects stale connections via connection state check. Blender never knows UE died until send fails or socket error occurs.

**Fix**: Add periodic heartbeat packet from Blender. UE responds with acknowledgment.

### M2 — Single connection only

**Status: ⏳ PENDING** — single `ConnectionSocket*`; reconnection on socket loss works, but no multi-connection support

**File**: `UELiveSyncSubsystem.cpp:111-112`

**Description**: `ConnectionSocket` is a single pointer. No support for Blender reconnection after UE restart.

**Fix**: Re-accept logic on connection loss.

### M3 — No packet type discriminator

**Status: ✅ RESOLVED** (Phase 3.4 — V3 header type byte)

**File**: `SyncTypes.h:85-118`

**Description**: Header has no type field. All packets treated as transform.

**Fix**: Add type byte to header in next protocol version.

### M4 — Scale interpolation uses linear lerp

**Status: ✅ RESOLVED** (Phase 3.5 — scale snaps directly)

**File**: `UELiveSyncSubsystem.cpp:853-863`

**Description**: `FMath::VInterpTo` is linear. Scale is multiplicative; linear interpolation on scale components causes visual artifacts during transition.

**Fix**: Use exponential interpolation or snap scale directly.

### M5 — Hardcoded thresholds

**Status: ⏳ PENDING** — `sync.py:55,66,81` thresholds (0.01/0.0001/0.001) and `UELiveSyncSubsystem.cpp` thresholds (0.05/0.002/0.001) remain hardcoded

**Files**: `sync.py:55,66,81`

**Description**: Location(0.01), Rotation(0.0001), Scale(0.001) are hardcoded.

**Fix**: Add as addon preferences.

### M6 — Silent send failure

**Status: ⏳ PENDING** — failure printed to console only; no UI indicator

**File**: `network.py:163-168`

**Description**: If reconnect fails, `send_packet` returns silently. No user feedback.

**Fix**: Status indicator in Blender UI (connected/disconnected).

### M7 — TransformStates grows unbounded

**Status: ✅ RESOLVED** (Phase 3.5 — `EvictStaleTransformStates()` 60s TTL)

**File**: `UELiveSyncSubsystem.cpp:770-883`

**Description**: Objects synced once remain in TransformStates forever. If Blender object is deleted, UE TransformState entry persists.

**Fix**: TTL-based eviction (remove states not updated in 60s).

---

## Low Issues

| ID | Description | File | Status |
|----|-------------|------|--------|
| L1 | Port 5000 conflicts with AirPlay on macOS | `network.py:86` | ⏳ Open |
| L2 | Missing FindActorFast stale cleanup | `UELiveSyncSubsystem.cpp:985-997` | ⏳ Partially addressed (OnActorDestroyed + EvictStale) |
| L3 | No initial full-state snapshot on connect | Implicit | ⏳ Open |
| L4 | Blender addon preferences UI missing | `__init__.py` | ⏳ Open |
| L5 | No error reporting operator feedback | `__init__.py:19-23` | ⏳ Open |
