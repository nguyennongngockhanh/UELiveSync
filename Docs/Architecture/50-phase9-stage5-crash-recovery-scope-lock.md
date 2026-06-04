# Phase 9 Stage 5A — Crash / Session Recovery Scope Lock

## Failure Mode Matrix

| # | Failure | Trigger | Current Behaviour | Severity |
|---|---------|---------|-------------------|----------|
| F1 | Blender process crash | Segfault, OOM, unhandled exception | UE continues running. Actors stay alive. No heartbeat timeout triggers because UE receives nothing. **Stale actors remain indefinitely.** | High |
| F2 | Blender restart (intentional) | User restarts Blender, saves scene | Same as F1. On reconnect, full snapshot is sent. Actors may duplicate if old actors weren't cleaned up. | High |
| F3 | UE process crash | GPF, engine assert, GPU hang | Blender socket send fails → `_reconnect_internal()` → exponential backoff. On reconnect, tries to connect to dead port → `ConnectionRefusedError`. Reconnects when UE restarts. **No UE-side actors to clean up (process restarted).** | Medium |
| F4 | TCP disconnect (network blip) | Cable unplugged, WiFi dropout, firewall | Blender: send fails → `_reconnect_internal()`. Both sides detect: Blender via send error, UE via `RecvTimeoutMs` (5s default). UE: heartbeat timeout → `ReconstructCompletedMeshes` continues. **On reconnect, full snapshot via PT_BeginSnapshot/EndSnapshot. No data loss.** | Low |
| F5 | UE editor stops listening | Level unload, PIE stop, network thread crash | Blender: connect refused or timeout → exponential backoff. No data loss. | Low |
| F6 | Capability negotiation after restart | Blender or UE restarts mid-session | Session GUID regenerates (Blender restart). UE stores old SessionGUID. New announce arrives with new GUID. **No mechanism to detect session change on UE side — old actors are not cleaned up.** | High |
| F7 | Asset cache staleness | Long-running session with no disconnects | `AssetMetadata`, `MaterialMetadata`, `ActorCache` grow monotonically. No eviction policy. | Medium |
| F8 | Module reload (Blender) | User toggles addon off/on | `unregister()` calls, then `register()` calls. All module-level state (`_client`, `_dirty_flags`, `_session_guid`) is reset. Network thread stops. **Clean. But Blender-side `tracked_objects` set may be stale on re-register.** | Low |

## Recovery Strategy Matrix

| Failure | Current Recovery | Gap | Proposed Fix |
|---------|-----------------|-----|-------------|
| F1 + F2 (Blender crash/restart) | None on UE side | Old actors orphaned in UE world | Session GUID tracking on UE. On Blender reconnect, if `SessionGUID` changed: delete all actors from old session, then rebuild from snapshot. |
| F3 (UE crash) | Exponential backoff reconnect | Good enough | Clear `PendingMeshReassembly` on disconnect. Reset `RemoteCapabilityFlags`. |
| F4 (TCP blip) | Snapshot re-sent, heartbeat timeout | Good enough | No change needed. |
| F5 (UE stopped) | Exponential backoff | Good enough | No change needed. |
| F6 (Capability after restart) | `_connect_internal` resets caps, sends new announce | Session GUID mismatch not detected | UE: store `SessionGUID` from announce. On session change → cleanup old actors. |
| F7 (Cache staleness) | None | Growing memory | Add `CacheTTL` to AssetMetadata/MaterialMetadata, evict stale entries. Configurable via CVar. |
| F8 (Module reload) | Full reset | Clean | No change needed. |

## State Ownership Analysis

### Blender-side state that must be RESET on reconnect/disconnect

| State | Location | Reset Action |
|-------|----------|-------------|
| `_session_guid` | `network.py` global | `_regenerate_session_guid()` already called in `_reconnect_internal()` |
| `_remote_capabilities` | `LiveSyncClient` | Reset to 0 in `_connect_internal()` (already done) |
| `_capability_response_received` | `LiveSyncClient` | Reset to False in `_connect_internal()` (already done) |
| `_capability_timeout` | `LiveSyncClient` | Reset to False in `_connect_internal()` (already done) |
| `_capability_announce_sent` | `LiveSyncClient` | Reset to 0 in `_connect_internal()` (already done) |
| `_last_ack` | `LiveSyncClient` | Cleared by `_clear_ack_state()` (already done) |
| `_collection_replay_stream` | `network.py` global | Cleared by `clear_collection_replay_stream()` or on start_sync |
| `_dirty_flags` | `sync.py` global | Cleared every tick + on disconnect |
| `last_sent_transforms` | `sync.py` global | Should be cleared on reconnect (currently NOT done) |
| `_last_mesh_identity` | `sync.py` global | Should be cleared on reconnect (currently NOT done) |
| `_known_guids` | `sync.py` global | Kept (populated by scene scan). Should be preserved across reconnect. |

### Blender-side state that MAY be preserved across reconnect

| State | Location | Rationale |
|-------|----------|-----------|
| `tracked_objects` | `sync.py` global | Scene is unchanged — objects still exist |
| `_known_guids` | `sync.py` global | Valid GUIDs from current scene |
| `_full_scan_counter` | `sync.py` global | Ticks elapsed — unrelated to connection |
| `_dirty_stats` | `sync.py` global | Diagnostic counters — preserve for post-mortem |

### UE-side state that must be RESET on session change (new SessionGUID)

| State | Location | Reset Action |
|-------|----------|-------------|
| `ActorCache` | `UELiveSyncSubsystem.h:485` | Clear all entries where `SessionGUID != CurrentSessionGUID` |
| `TransformStates` | `UELiveSyncSubsystem.h:495` | Clear on session change or full snapshot |
| `AssetMetadata` | (subsystem member) | Clear on session change |
| `AssetPathCache` | (subsystem member) | Clear on session change |
| `MaterialMetadata` | (subsystem member) | Clear on session change |
| `MaterialPathCache` | (subsystem member) | Clear on session change |
| `PendingMeshReassembly` | (subsystem member) | Clear on disconnect or session change |
| `MissingActorTracker` | (subsystem member) | Clear on session change |
| `LastHeartbeatTime` | (subsystem member) | Reset on connect |
| `RemoteCapabilityFlags` | (subsystem member) | Reset on disconnect |
| `SessionGUID[16]` | (subsystem member) | Updated on new announce |
| `LastSequenceId` | (subsystem member) | Reset on full snapshot flag |
| `SeenThisTick` | (subsystem member) | Cleared at start of ProcessQueuedPackets |

### UE-side state that may be preserved across reconnect (same session)

| State | Rationale |
|-------|-----------|
| `OverflowHistory` | Diagnostic — preserve |
| `ReconnectHistory` | Diagnostic — preserve |
| `Stats.*` | Diagnostic counters — preserve |
| `MeshChunksReceived` | Counter — preserve |
| `MeshReassembliesCompleted` | Counter — preserve |

## Session GUID Lifecycle

```
Blender start_sync()
  └→ _regenerate_session_guid()
  └→ Send announce with SessionGUID [16 bytes]

UE receives announce
  └→ Store SessionGUID in member
  └→ Compare with previous SessionGUID (if any)
  └→ If different: clear per-session state (ActorCache, TransformStates, etc.)
  └→ Send response

Blender reconnect
  └→ _regenerate_session_guid()         ← NEW GUID
  └→ Send announce with new SessionGUID

UE receives new announce
  └→ SessionGUID != stored → clear per-session state
  └→ Store new SessionGUID
```

## Recovery UX States

| State | Blender Panel | UE Log |
|-------|---------------|--------|
| **Connected** | Green dot, stats | `[RECV]`, `[CAP]`, tick pipeline |
| **Disconnected (Blender side)** | Red dot + "Disconnected — will auto-reconnect" | Heartbeat timeout warning |
| **Disconnected (UE side)** | Blender: exponential backoff message | UE: `Connection reset by peer` |
| **Reconnecting** | Yellow dot + "Reconnecting (attempt N)" | N/A |
| **Reconnected** | Green dot + "Reconnected" + uptime reset | `[CAP] Received announce: caps=...` |
| **Session changed** | Green dot + "Connected (new session)" | `[CAP] Session changed — clearing stale actors` |
| **Backoff saturated** | Red dot + "Reconnect failed — retrying in 30s" | N/A |

## Acceptance Criteria

| # | Criterion | Verification |
|---|-----------|-------------|
| A1 | Blender restart → old UE actors cleaned up, new ones created | Kill Blender → restart → verify no duplicate actors in UE |
| A2 | UE restart → Blender reconnects with exponential backoff, sends full snapshot | Kill UE → Blender shows backoff → restart UE → verify reconnect within 30s |
| A3 | TCP blip → both sides resume without data loss | Unplug cable for 3s → reconnect → verify transforms continue |
| A4 | Session GUID changes on Blender reconnect | Verify `_regenerate_session_guid()` called in `_reconnect_internal()` |
| A5 | UE detects session GUID change and clears stale state | Send announce with new GUID → verify `ActorCache.Empty()` called |
| A6 | Capability negotiation re-runs after restart | Verify `_capability_response_received = False` before reconnect announce |
| A7 | Pending mesh reassembly cleared on disconnect | Kill Blender → wait → verify `PendingMeshReassembly` empty |
| A8 | Asset/Material metadata caches survive same-session reconnect | Same GUID → same metadata reused. Different GUID → cleared. |
| A9 | No stale capabilities survive reconnect | `RemoteCapabilityFlags = 0` before announce. Reset after response. |

## Remaining Gaps (Not Yet Addressed)

| Gap | Risk | Priority |
|-----|------|----------|
| `last_sent_transforms` not cleared on reconnect | Stale transform data may interfere with first-send detection | Low |
| `_last_mesh_identity` not cleared on reconnect | Stale mesh version hash may suppress needed mesh re-send on reconnect | Low |
| `LastSequenceId` not reset on session change | Sequence check may reject first packets after reconnect | Medium (would cause 1 packet drop on reconnect) |
| `SeenThisTick` not cleared on session change | Brief dedup window could skip objects | Low (cleared every tick) |
| UE heartbeat timeout not wired to session cleanup | Actor cleanup doesn't trigger on heartbeat loss — only on explicit session change | Medium |
| No Blender-side `tracked_objects` cache rebuild after module reload | `register()` may not re-scan the scene | Low (user can trigger scan manually) |

## Implementation Status

### Stage 5B Complete (2026-06-01)

- UE-side session change detection added to `PT_CapabilityAnnounce` handler
- `bSessionInitialized` flag tracks whether any announce has been received
- 16-byte GUID comparison: match = same-session, mismatch = new session
- On new session: clears `ActorCache`, `TransformStates`, `AssetMetadata`, `AssetPathCache`, `MaterialMetadata`, `MaterialPathCache`, `PendingMeshReassembly`, `LastSequenceId`
- Preserves: `PacketQueue`, `Stats`, `OverflowHistory`, `ReconnectHistory`
- Logging: `[CAP] Session initialized: guid=...` / `[CAP] Same-session reconnect — state preserved` / `[CAP] New session detected — clearing per-session state`
- Tests: `tests/phase9_stage5b_session_change.py` — 17/17 PASS
- Existing regression: 238/238 PASS

## Implementation Status

### Stage 5C Complete (2026-06-01)

- UE: `PendingMeshReassembly.Empty()` in `StopNetworkThread()` — clears incomplete mesh builds on any disconnect
- Blender: `_last_mesh_identity.clear()`, `_last_material_identity.clear()`, `_last_geometry_version.clear()` added to reconnect path in `check_updates()`
- ActorCache, AssetMetadata, MaterialMetadata survive simple disconnect (not in StopNetworkThread cleanup)
- Tests: `tests/phase9_stage5c_state_cleanup.py` — 6/6 PASS

## Implementation Status

### Stage 5D Complete (2026-06-01)

- `_reconnecting` flag set on `_reconnect_internal()`, cleared on successful `_connect_internal()`
- `_next_reconnect_time` computed using exponential backoff
- Fields exposed in `get_runtime_stats()`: `is_reconnecting`, `reconnect_attempt`, `reconnect_delay`, `next_reconnect_time`
- Blender sidebar shows reconnect status when disconnected and reconnecting
- Tests: `tests/phase9_stage5d_reconnect_ui.py` — 11/11 PASS

## Remaining Implementation Order

1. ~~**Session GUID comparison on UE** — done (Stage 5B)~~
2. ~~**Clear `PendingMeshReassembly` on disconnect** — done (Stage 5C)~~
3. ~~**Clear transient caches on reconnect** — done (Stage 5C)~~
4. ~~**Reconnect backoff UI** — done (Stage 5D)~~
4. **Exponential backoff saturation logging** — user feedback in UI (Stage 5D)
5. **Stale actor cache eviction by heartbeat timeout** — UE side (Stage 5E)
6. **Cache TTL for asset/material metadata** — prevent unbounded growth (Stage 5F)

No protocol version bump required. Session GUID is already in the `FCapabilityAnnouncePayload` — UE receives it but currently doesn't act on it.
