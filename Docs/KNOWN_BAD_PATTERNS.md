# Known Bad Patterns — Do Not Do This

Each entry documents a configuration or code pattern that **must not be used** with LiveSync.

---

## A — Using `-RenderOffScreen` (Headless Editor Mode)

UELiveSync requires the editor tick loop to be active for ingress. Use `-RenderOffScreen` instead of `-NullRHI` for headless/server scenarios:

| Attribute | Value |
|-----------|-------|
| **Purpose** | Run UE editor without a physical display |
| **Recommended** | `-RenderOffScreen` — runs the renderer on a headless framebuffer, keeps the tick loop alive |
| **Verification** | `[TICK][HEARTBEAT] Tick is executing (frame=N)` appears every ~5s in the UE log |
| **Detection (blocked)** | A startup guard in `UELiveSyncSubsystem.cpp` detects `-NullRHI` and logs `[LIFECYCLE][ERROR] NullRHI editor mode DETECTED.` — the engine will error out before tick loop starts |

**`-NullRHI` is detected and rejected at plugin startup.** Do not use it. Use `-RenderOffScreen` instead.

---

## B — StopNetworkThread Without Shutdown Before Close

| Attribute | Value |
|-----------|-------|
| **Severity** | Critical — game thread deadlock |
| **Root Cause** | On Linux, `close()` alone does NOT wake a blocked `recv()`/`poll()` in another thread. Only `shutdown(SHUT_RDWR)` sends TCP FIN/RST. |
| **Fix** | Always call `Shutdown(ReadWrite)` before `Close()`. See `StopNetworkThread()` in `UELiveSyncSubsystem.cpp` for correct order. |
| **Protected** | `LiveSyncRunnable.h` freeze banner warns of this. |

---

## C — obj.name in GUID Owner Hash

| Attribute | Value |
|-----------|-------|
| **Severity** | High — GUID churn on rename |
| **Root Cause** | `_compute_owner_hash()` must depend ONLY on `obj.data.name`. Adding `obj.name` causes GUID to change when the user renames the object. |
| **Fix** | See `sync.py:_compute_owner_hash()`. The fix was applied in Phase 6G. |
| **See Also** | `Docs/CRITICAL_INVARIANTS.md` §A (GI-1 through GI-5) |

---

## D — Non-Deterministic Iteration in Replay Hash

| Attribute | Value |
|-----------|-------|
| **Severity** | High — replay divergence |
| **Root Cause** | Hashing `TMap`/`TSet` entries without sorting by GUID produces non-deterministic hashes across runs. |
| **Fix** | Always sort GUIDs before hashing. See `ComputeCollectionStateHash()` and `ComputeWorldStateHash()`. |

---

## E — Omitting a Domain from World Snapshot Export

| Attribute | Value |
|-----------|-------|
| **Severity** | High — state loss on rebuild |
| **Root Cause** | `ExportWorldSnapshot()` must include ALL authoritative domains (rename, hierarchy, collection, lifecycle, transform). Missing a domain causes `RebuildWorldFromSnapshot()` to produce an incomplete state. |
| **Fix** | See `ExportWorldSnapshot()` and `RebuildWorldFromSnapshot()` for the canonical domain list. |

---

## F — Running Diagnostics Every Frame

| Attribute | Value |
|-----------|-------|
| **Severity** | Medium — performance regression |
| **Root Cause** | Diagnostic functions that run every frame (instead of every ~300 frames) cause measurable hitches. |
| **Fix** | Gate diagnostics behind `VerboseFrameCounter % 300 == 1` or similar cadence. See `TickPhase6H`, `TickSafetyMonitors`. |
