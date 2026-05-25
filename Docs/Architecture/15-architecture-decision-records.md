# Architecture Decision Records

> Lightweight ADR-style records for major finalized decisions
> from Phase 5. Preserves architectural reasoning before Phase 6
> complexity begins.

---

## ADR-001: Direct TCP Replication

| Field | Value |
|-------|-------|
| Status | **Accepted** (Phase 1) |
| Context | Need real-time transform sync between Blender and UE5 |
| Decision | Use direct TCP sockets (no message broker, no WebSocket, no REST) |
| Rationale | Lowest latency; simplest deployment (no broker); ordered/delivered guaranteed by TCP; no additional infrastructure dependency |
| Consequences | Must handle reconnect manually; must handle blocking I/O on Blender main thread via dedicated daemon thread; must use fixed-size binary protocol to avoid framing issues |
| Risk | TCP head-of-line blocking under high packet loss; on Linux, `close()` alone does not wake blocked `recv()` — requires `Shutdown(ReadWrite)` before `Close()` in StopNetworkThread |

---

## ADR-002: Binary Binary Protocol (not JSON/MessagePack/Protobuf)

| Field | Value |
|-------|-------|
| Status | **Accepted** (Phase 1) |
| Context | Need a serialization format for the sync protocol |
| Decision | Use hand-rolled little-endian binary packing (`struct.pack('<...')`) |
| Rationale | Minimal per-packet overhead (24-byte header + fixed-size 81-byte payload); no serialization library dependency; trivially parseable in both Python and C++; deterministic wire format |
| Consequences | Must manually manage backward compatibility (version byte, V2/V3/V4/V5 dispatch); no schema evolution (Protocol Buffers, etc.); struct layout changes require version bump |
| Risk | Protocol version sprawl; current 4 active versions (V2, V3, V4, V5) |

---

## ADR-003: Tick-Driven Processing Model (not Event-Driven)

| Field | Value |
|-------|-------|
| Status | **Accepted** (Phase 1) |
| Context | UE must process incoming sync packets and apply transforms to actors |
| Decision | Use Tick-driven dequeue + process (not event-driven callback on packet arrival) |
| Rationale | Guarantees game-thread execution for all UObject mutations; avoids cross-thread synchronization complexity; naturally rate-limited by Tick rate; decouples network I/O from world mutation |
| Consequences | One Tick of latency between packet arrival and visual update; must batch process for performance; Tick pipeline stages must be strictly ordered |
| Risk | Tick pipeline ordering errors cause frames where transforms are applied before spawns complete |

---

## ADR-004: Authoritative GUID Model from Blender

| Field | Value |
|-------|-------|
| Status | **Accepted** (Phase 1) |
| Context | Need to uniquely identify objects across Blender ↔ UE sessions |
| Decision | GUID is generated on the Blender side via `uuid.uuid4().hex`, stored as `obj["ue_guid"]` custom property |
| Rationale | GUID survives Blender file save/load; deterministic across sessions for same object; Blender controls identity lifecycle (object create/delete = GUID create/delete) |
| Consequences | UE must never generate GUIDs; GUID collision detection must be in Blender (`ensure_unique_guid()`); `obj.copy()` inherits GUID → must be detected and replaced; copy-on-duplicate must produce new GUID |
| Risk | GUID collision on `obj.copy()` is a known failure mode — mitigated by `ensure_unique_guid()` |

---

## ADR-005: Network Thread Enqueue Only

| Field | Value |
|-------|-------|
| Status | **Accepted** (Phase 1) |
| Context | UE network receive thread must interact with game-thread systems |
| Decision | Network thread is strictly an **enqueue path** — no UObject access, no actor mutations, no world access |
| Rationale | Simplifies threading model; eliminates risk of game-thread data races; enforces clean separation of concerns |
| Consequences | All packet processing is deferred to the game thread Tick; network thread can block on recv() without stalling game thread; requires bounded queue for backpressure |
| Risk | Queue overflow → packet loss; 128-entry FLiveSyncQueue capacity must be validated against MaxPacketRate CVar |

---

## ADR-006: Game Thread Processing Only

| Field | Value |
|-------|-------|
| Status | **Accepted** (Phase 1) |
| Context | Where/when packets are processed and applied to actors |
| Decision | All UObject mutations happen exclusively on the game thread in the Tick pipeline |
| Rationale | UE threading rules require UObject access from game thread only; avoids latent crashes from cross-thread UObject use |
| Consequences | Network thread cannot spawn/destroy/modify actors; all deferred processing adds Tick latency; game thread must not block (excessive pipeline operations cause frame drops) |
| Risk | If Tick processing takes >33ms (30fps budget), frame rate drops; mitigated by batch-processing bounded per Tick (MAX_ASSET_RESOLUTIONS_PER_TICK = 8, etc.) |

---

## ADR-007: Bounded MPSC Queue with Drop-Oldest on Overflow

| Field | Value |
|-------|-------|
| Status | **Accepted** (Phase 2) |
| Context | Need thread-safe packet transfer from network thread to game thread |
| Decision | Use a fixed-size (128-entry) multi-producer single-consumer queue with atomic head/tail; drop oldest entry on overflow |
| Rationale | Bounded memory allocation; O(1) enqueue/dequeue; drop-oldest is safe because transform updates are idempotent (dropping an old transform is better than blocking or crashing) |
| Consequences | Under sustained overload (>128 batched transforms per Tick), oldest transforms are silently dropped; must set MaxPacketRate CVar low enough to prevent sustained overload |
| Risk | Under packet storm, all queued packets could be from the same object — dropping old transforms means skipping intermediate states, which is acceptable (interpolation handles frame skips) |

---

## ADR-008: Fixed-Size 24-Byte Header

| Field | Value |
|-------|-------|
| Status | **Accepted** (Phase 1) |
| Context | Need to frame variable-length packets over TCP stream |
| Decision | Every packet starts with a fixed 24-byte header containing Magic, Version, Type, PayloadSize, GuidCount, Timestamp, SequenceNum, Flags, Checksum |
| Rationale | Minimal framing overhead; allows parser to pre-allocate buffer; checksum provides integrity; sequence number enables flood detection |
| Consequences | All parsing must read header first (minimum 24 bytes); PayloadSize field bounds the read; version byte enables backward compat dispatch |

---

## ADR-009: Blender → UE Only for Transforms (Unidirectional)

| Field | Value |
|-------|-------|
| Status | **Accepted** (Phase 1) |
| Context | Which direction transform data flows |
| Decision | Blender → UE only for transforms, creation, deletion. UE never sends transforms back. |
| Rationale | Blender is the authoring tool; UE is the visualization/rendering target. UE interpolation is client-side and must never feed back into Blender. |
| Consequences | UE actor transforms are overwritten by Blender on every update; UE-side transform manipulation is lost on next Blender sync; Phase 6 must add UE→Blender channel for editor operations |

---

## ADR-010: Heartbeat-Based Disconnect Detection

| Field | Value |
|-------|-------|
| Status | **Accepted** (Phase 1) |
| Context | Need to detect silent disconnection (Blender crash, network drop, etc.) |
| Decision | Blender sends PT_HEARTBEAT (0x07) every 5 seconds; UE detects disconnect after 15s of no heartbeat |
| Rationale | No application-level ACK needed; heartbeat is a single lightweight packet; 3-miss window avoids false positives from transient network drops |
| Consequences | Adds 15s delay before disconnect is detected; heartbeat must be sent from Blender daemon thread (not main thread) to avoid blocking during UI freezes |
| Risk | If Blender main thread freezes but daemon thread continues, heartbeat still flows — false sense of connection health |

---

## ADR-011: V4+ Objects Always 81 Bytes (including Primitive Type)

| Field | Value |
|-------|-------|
| Status | **Accepted** (Phase 4) |
| Context | Need primitive type identification for proper UE actor spawning (cube vs sphere vs mesh vs skeletal mesh) |
| Decision | V4+ packets use fixed 81-byte object payloads; primitive type byte at offset 80 |
| Rationale | Fixed-size simplifies parsing; backward compat with V2/V3 (72 bytes, no primitive byte); V4 and V5 both use 81 bytes |
| Consequences | All V4+ objects are 9 bytes larger than V2/V3; parser must dispatch to parser V2/V3 or V4/V5 based on version byte |
| Risk | Adding new fields to object layout requires V6 protocol version |

---

## ADR-012: Asset Identity via xxHash64 (V5)

| Field | Value |
|-------|-------|
| Status | **Accepted** (Phase 5D) |
| Context | Need deterministic mesh/skeletal mesh identity from Blender datablocks |
| Decision | Use xxHash64 hash of datablock properties (name, asset path, vertex count, material count) as 8-byte asset identity |
| Rationale | Deterministic across Blender sessions; small wire footprint (8 bytes); fast to compute; no external asset database needed |
| Consequences | Asset identity depends on datablock state (is NOT instance-dependent); renaming a datablock changes identity; fallback primitive assignment is TEMPORARY — mesh is live-swapped on late resolution |
| Risk | xxHash64 collisions are theoretically possible (2^-64 probability) — considered acceptable for this use case |

---

## ADR-013: Asset Resolution with Exponential Backoff (not blocking)

| Field | Value |
|-------|-------|
| Status | **Accepted** (Phase 5D) |
| Context | Assets (meshes, skeletal meshes) may not be loaded when first referenced |
| Decision | Defer resolution to a bounded queue (2048 entries); retry with exponential backoff: 1s → 2s → 4s → 8s → 16s, max 5 attempts; missing assets do NOT block transform replication |
| Rationale | Transforms are time-sensitive; blocking asset resolution would stall all sync; background resolution allows transforms to flow while assets load asynchronously |
| Consequences | Actor may start with TEMPORARY primitive mesh and live-swap when asset resolves; out-of-order reso-lution is normal; attempted 6th time = abandon with warning |

---

## ADR-014: StopNetworkThread Shutdown Order (Shutdown(ReadWrite) before Close)

| Field | Value |
|-------|-------|
| Status | **Accepted** (Phase 5D) |
| Context | On Linux, `close()` alone does NOT wake a blocked `recv()`/`poll()` in another thread |
| Decision | Shutdown order: `Runnable->Stop()` → `Socket->Shutdown(ReadWrite)` → `Socket->Close()` → `WaitForCompletion()` → delete → DestroySocket |
| Rationale | `Shutdown()` sends TCP FIN/RST to unblock the blocked recv; without it, `WaitForCompletion()` deadlocks the game thread |
| Consequences | This order is MANDATORY on all platforms (not just Linux) for consistency; code comment documents this invariant |

---

## ADR-015: Phase 5 Freeze Investigation — SIGABRT Root Cause Analysis

| Field | Value |
|-------|-------|
| Status | **Accepted** (Phase 5E) |
| Context | User reported "editor freeze" during heavy sync load + disconnect |
| Decision | Classified as SIGABRT crash in FPendingAssetQueue::Dequeue (TSet::Remove assertion). Two fixes applied: Contains() guard in Dequeue(), ResolvedThisTick++ moved to top of while loop body. |
| Rationale | Original crash was NOT an infinite loop — was SparseSet assertion in UE TSet internals. The ResolvedThisTick bug would have caused an infinite loop under specific conditions (all dequeued GUIDs in retry state). Two separate bugs with similar observable symptom. |
| Consequences | Plugin pipeline validated under 6h38m sustained runtime with 46K Tick frames, 232K balanced BEGIN/END traces, 14K SetActorTransform calls, 0 crashes. 5/5 validation tests pass. |
