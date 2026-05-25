# Core Runtime Invariants

> Phase 5 Complete — v0.5.0-stabilized
>
> Breaking any invariant documented here risks destabilizing the runtime core.
> Modification requires critical-bug justification and peer review.

---

## 1. Packet Lifecycle

```
Blender Main Thread          Network Thread (Blender)    TCP Wire          Network Thread (UE)       Game Thread (UE)
       │                              │                    │                      │                        │
       │  serialize → enqueue          │                    │                      │                        │
       │──────────────────────────────>│                    │                      │                        │
       │                              │  socket.sendall()   │                      │                        │
       │                              │────────────────────>│                      │                        │
       │                              │                    │  recv()               │                        │
       │                              │                    │─────────────────────>│                        │
       │                              │                    │                      │  ProcessQueuedPackets() │
       │                              │                    │                      │────────────────────────>│
       │                              │                    │                      │                        │
```

### Packet Flow Invariants

| Stage | Component | Thread | Invariant |
|-------|-----------|--------|-----------|
| 1 | Blender scene scan | Blender main | Must not block with socket I/O; bpy access only |
| 2 | Serialize | Blender main | Little-endian, fixed-size 24-byte header, 81-byte V4+ payload |
| 3 | Enqueue to Blender send queue | Blender main | Non-blocking enqueue |
| 4 | Socket send | Blender daemon thread | `socket.sendall()` only; no bpy access |
| 5 | Socket receive | UE network thread | Wait(10ms) + Recv(); no UObject access |
| 6 | Enqueue to FLiveSyncQueue | UE network thread | Non-blocking; drop-oldest on overflow |
| 7 | Dequeue + process | UE game thread | ProcessQueuedPackets → InterpolateTransforms → ... |

---

## 2. Thread Ownership Rules

### Blender

| Thread | Allowed Operations | Forbidden Operations |
|--------|-------------------|---------------------|
| Main thread | bpy API (scene iteration, diff detection, serialization) | Socket send (blocking I/O) |
| Daemon thread | `socket.sendall()`, socket connect/reconnect | bpy API |

### UE

| Thread | Allowed Operations | Forbidden Operations |
|--------|-------------------|---------------------|
| Game thread | All UObject/world mutations, Tick pipeline, SetActorTransform, SpawnActor, DestroyActor | Socket recv/send |
| Network thread | Socket recv, queue enqueue, heartbeat timeout detection | Any UObject pointer access, game-thread state mutation |

---

## 3. Queue Ownership Rules

### FLiveSyncQueue (bounded 128, transform packets)

| Operation | Thread | Notes |
|-----------|--------|-------|
| Enqueue | UE network thread | Non-blocking, atomic index update |
| DequeueBatch | UE game thread | Drains entire batch per Tick |
| Overflow | N/A | Drop-oldest; validated invariant against MaxPacketRate CVar (60 Hz × 2 = 120 safe upper bound vs 128 capacity) |

### FLiveSyncPendingAssetQueue (bounded 2048, GUID resolution)

| Operation | Thread | Notes |
|-----------|--------|-------|
| Enqueue | UE network thread | Asset identity packets from network |
| Dequeue | UE game thread | ResolvePendingAssets Tick stage |
| Remove | UE game thread | Explicit remove on resolution success |
| Retry | UE game thread | Exponential backoff: 1s → 2s → 4s → 8s → 16s, max 5 attempts |

### Blender Send Queue (unbounded Python list)

| Operation | Thread | Notes |
|-----------|--------|-------|
| Enqueue | Blender main | Append, non-blocking |
| Dequeue | Blender daemon | Pop entire batch, socket.sendall |

---

## 4. Game-Thread-Only Systems

The following subsystems execute exclusively on the UE game thread, in this order, every Tick:

1. **ProcessQueuedPackets** — Dequeues FLiveSyncQueue batch; calls ProcessBinaryPacket for each; applies Create/Transform/Delete/Heartbeat/AssetDef/BeginSnapshot/EndSnapshot
2. **InterpolateTransforms** — Applies interpolated transforms to tracked actors (or snap if interpolation disabled)
3. **ResolvePendingAttachments** — Re-parents actors whose parent GUID was deferred
4. **RecoverMissingActors** — Re-spawns actors whose base component was lost
5. **ResolvePendingAssets** — Processes FLiveSyncPendingAssetQueue; retries missing assets with exponential backoff
6. **PurgeStaleActors** — Removes actors whose Blender-side GUID has been deleted

**Invariant**: No game-thread Tick function enqueues to the network, blocks on I/O, or writes to a socket.

---

## 5. Network-Thread-Only Systems

The following subsystems execute exclusively on the UE network receive thread (FLiveSyncRunnable):

1. **Socket recv** — `Wait(10ms)` + `Recv()`, partial-read assembly
2. **Magic validation** — Verify `MAGIC = 0x4C56534D`
3. **Header parse** — Fixed 24-byte header: Magic(4) + Version(1) + Type(1) + PayloadSize(2) + GuidCount(2) + Timestamp(8) + SequenceNum(2) + Flags(2) + Checksum(2)
4. **Queue enqueue** — Enqueue FLiveSyncPacket or FLiveSyncPendingAsset into respective queues
5. **Heartbeat timeout** — Monitors last-received timestamp; flags disconnect if 15s without heartbeat
6. **Reconnect logic** — StopNetworkThread / StartNetworkThread sequence when peer is detected dead

**Invariant**: No network-thread function calls UObject API, mutates actor state, or accesses any UWorld/ULevel.

---

## 6. Tick Ordering Guarantees

The Tick pipeline executes in a strict order each frame:

```
BEGIN scope
├── ProcessQueuedPackets()       ← inbound packet processing
├── InterpolateTransforms()      ← visual transform application
├── ResolvePendingAttachments()  ← parent-child linking
├── RecoverMissingActors()       ← actor re-creation
├── ResolvePendingAssets()       ← asset mesh/skeletal mesh resolution
├── PurgeStaleActors()           ← deleted-guid cleanup
├── UpdateDiagnostics()          ← metrics + stats panel refresh
END scope
```

**Invariant**: No function reorders these stages. Adding a new stage must preserve the relative ordering of the existing stages.

---

## 7. Parser Invariants

| Rule | Description |
|------|-------------|
| Magic | First 4 bytes must equal `0x4C56534D`; reject packet if mismatch |
| Header size | 24 bytes, fixed, regardless of protocol version |
| Version dispatch | Read version byte; dispatch to V2/V3/V4/V5 parser; V4+ objects always 81 bytes with primitive type byte at offset 80 |
| Little-endian | All multi-byte fields are little-endian (struct.pack('<...')) |
| Checksum | Last 2 bytes of header; simple additive checksum over payload; packet dropped on mismatch |
| Sequence number | Monotonically increasing per-connection; used for flood detection (2-second window, never blocks or allocates) |
| PayloadSize | uint16, maximum `MAX_PACKET_SIZE`; reject oversized |
| GuidCount | uint16, must match actual GUID count in payload; reject mismatch |

### Supported Protocol Versions

| Version | Object Size | Primitive Byte | Status |
|---------|-------------|----------------|--------|
| V2 | 72 bytes | No | Backward compatible |
| V3 | 72 bytes | No | Backward compatible |
| V4 | 81 bytes | Yes (offset 80) | Default |
| V5 | 81 bytes | Yes (offset 80) | Stable (asset identity) |

---

## 8. Object Layout Invariants

### FSyncTransformState (POD-only — no FString or UObject members)

```
FGuid ActorGuid           (16 bytes) — unique per-object GUID
FVector Location           (12 bytes) — UE-space location (Blender × 100, Y-flipped)
FQuat Rotation             (16 bytes) — UE-space rotation
FVector Scale              (12 bytes) — UE-space scale
double Timestamp           (8 bytes)  — Blender-side timestamp
uint8 PrimitiveType        (1 byte)   — V4+: 0=Unknown, 1=Cube, 2=Sphere, 3=Plane, 4=Cylinder, 5=Cone, 6=Torus, 7=Mesh, 8=Metaball, 255=SkeletalMesh
```

**Total**: 65 bytes (POD struct, no constructor/destructor).
**Invariant**: Must remain POD-only. Asset metadata stored separately in `TMap<FGuid, FAssetMetadata>`.

### Packet Header (24 bytes, fixed)

| Offset | Size | Field |
|--------|------|-------|
| 0 | 4 | MAGIC (0x4C56534D) |
| 4 | 1 | ProtocolVersion |
| 5 | 1 | PacketType |
| 6 | 2 | PayloadSize |
| 8 | 2 | GuidCount |
| 10 | 8 | Timestamp |
| 18 | 2 | SequenceNum |
| 20 | 2 | Flags |
| 22 | 2 | Checksum |

---

## 9. Reconnect Guarantees

| Behaviour | Description |
|-----------|-------------|
| GUID preservation | Object GUIDs persist across reconnects via `ue_guid` custom property; no double-spawn on reconnect |
| Heartbeat timeout | 15s without heartbeat → UE flags connection dead → StopNetworkThread |
| Reconnect trigger | Blender detects broken socket → reconnect thread → re-establish + heartbeat |
| State resync | On reconnect, no full state dump (Phase 6 feature); incremental sync continues from current state |
| Thread safety | StopNetworkThread order: Runnable->Stop() → Socket->Shutdown(ReadWrite) → Socket->Close() → WaitForCompletion() → delete → DestroySocket. On Linux, Shutdown() before Close() is mandatory to wake blocked recv(). |

---

## 10. Queue Safety Guarantees

| Queue | Capacity | Overflow | Thread-safe | Notes |
|-------|----------|----------|-------------|-------|
| FLiveSyncQueue | 128 | Drop oldest | Yes (atomic head/tail) | Transform packets only |
| FLiveSyncPendingAssetQueue | 2048 | Drop newest | Yes (lock-free entry map) | Asset GUID resolution |
| Blender send queue | Unbounded | N/A (Python list) | No (main thread enqueue, daemon dequeue via mutex) | Must not grow unbounded |
| PendingAttachmentQueue | Unbounded | N/A | No (game thread only) | Deferred parent-child linking |

---

## 11. Hierarchy Safety Guarantees

| Rule | Description |
|------|-------------|
| Parent GUID | Each object carries an optional parent GUID (part of V3+ protocol) |
| Parent-first spawn | Actor is spawned only after its parent has been spawned or a deferred parent link is queued |
| Deferred resolution | If parent GUID not yet resolved, actor is added to PendingAttachmentQueue |
| Cycle prevention | Parent must be a different GUID than child; no self-referencing |
| Reparenting | Existing actors are re-parented in ResolvePendingAttachments if parent GUID changes |

---

## 12. Metrics and Diagnostics Invariants

| Rule | Description |
|------|-------------|
| EMA update | O(1), never allocates |
| Event history | Bounded at 32 entries per category |
| Flood detection | 2-second sliding window; never blocks or allocates during detection |
| Debug draw | Zero overhead when disabled (CVar gate + static bool check) |
| Diagnostics panel refresh | Maximum 250ms interval; never ticks every frame |
| All diagnostics counters | `std::memory_order_relaxed` (display values only) |
| BEGIN/END tracing | Every Tick pipeline stage has paired UE_LOG trace markers; must remain balanced |

---

## 13. Summary: Risk of Breaking Invariants

Modifying any of the following without reproducing the original failure scenario risks silent data corruption, deadlocks, or crashes:

- **Packet parser** → malformed packet handling, backward compat breakage
- **Tick pipeline ordering** → transform before spawn, asset resolution starvation
- **Queue ownership** → data races on enqueue/dequeue paths
- **Network thread lifecycle** → game thread deadlock via WaitForCompletion
- **FSyncTransformState layout** → binary protocol incompatibility
- **Heartbeat timeout** → connection state machine desync
- **GUID persistence** → double-spawn or GUID collision on reconnect
- **Blender main thread blocking** → UI freeze on reconnect
