# Issues by Phase

> Status: 2026-05-21 — 13 of 18 issues resolved.

## Phase 1 — Must Fix (All Resolved)

| ID | Issue | Status | Resolution |
|----|-------|--------|------------|
| C1 | Main thread blocking on socket.sendall() | ✅ Fixed | Background sender thread (Phase 3.4) |
| C2 | GUID hex-string roundtrip | ✅ Fixed | V3 direct binary (Phase 3.4) |
| C3 | UE_LOG per object per packet | ✅ Fixed | Behind `bEnableVerboseSyncLogs` (Phase 3.4) |
| C4 | Unbounded queue growth | ✅ Fixed | `MaxQueueSize=128` (Phase 3.4) |
| C5 | ActorCache full rebuild | ✅ Fixed | Incremental via event handlers (Phase 3.4) |
| H3 | No TCP_NODELAY | ✅ Fixed | `SetNoDelay(true)` both sides (Phase 3.4) |
| H6 | Timer double-registration | ✅ Fixed | `_timer_ref` guard (Phase 3.4) |
| H7 | reconnect() blocks 500ms | ✅ Fixed | Background thread reconnect (Phase 3.4) |

## Phase 2 — Should Fix (Mostly Resolved)

| ID | Issue | Status | Resolution |
|----|-------|--------|------------|
| C6 | ActorCache rebuild race | ✅ Fixed | No periodic rebuild exists (Phase 3.4) |
| H1 | Full scene iteration every 16ms | ✅ Fixed | `tracked_objects` dict (Phase 3.5) |
| H2 | World-space only (no hierarchy) | ⏳ Partial | Parent GUID sent, no UE hierarchy reconstruction |
| H4 | Interpolation lag | ⏳ Partial | Snap at 0.5f distance added |
| H5 | No dedup in process queue | ✅ Fixed | `SeenThisTick` TSet (Phase 3.4) |

## Phase 3+ — Nice to Have (Mostly Resolved)

| ID | Issue | Status | Resolution |
|----|-------|--------|------------|
| M1 | No heartbeat | ✅ Fixed | Time-based 5s heartbeat (Phase 3.5) |
| M2 | Single connection only | ⏳ Open | Single-socket; reconnection works, multi-connect pending |
| M3 | No packet type field | ✅ Fixed | V3 header `PacketType` byte (Phase 3.4) |
| M4 | Scale interpolation | ✅ Fixed | Scale snaps directly (Phase 3.5) |
| M5 | Hardcoded thresholds | ⏳ Open | Still hardcoded in `sync.py` and `UELiveSyncSubsystem.cpp` |
| M6 | Silent send failure | ⏳ Open | Console-only; no UI indicator |
| M7 | TransformStates unbounded | ✅ Fixed | 60s TTL eviction (Phase 3.5) |
| L1–L5 | Various low items | ⏳ Open | See issues database |

## Remaining Open Items for Phase 3.6+

| ID | Issue | Area |
|----|-------|------|
| H2 | World-space only / hierarchy | Protocol + UE actor tree |
| H4 | Interpolation lag / smoothness tuning | UE interpolation |
| M2 | Single connection / re-accept | UE networking |
| M5 | Hardcoded thresholds | Blender addon preferences |
| M6 | Silent send failure / UI status | Blender addon UI |
| L1 | Port conflict on macOS | Network config |
| L2 | FindActorFast stale cleanup | UE cache management |
| L3 | No initial snapshot | Protocol |
| L4 | Preferences UI | Blender addon |
| L5 | Error reporting operator | Blender addon |
