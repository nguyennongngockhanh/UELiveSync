# INV-2026-019 — First Sync Lost (0x60 FBX Import Request Never Reaches Import)

**Status**: CLOSED
**Priority**: P0 (blocking — MIG-009 WS-1 end-to-end acceptance blocked)
**Owner**: Khanh
**Started**: 2026-08-05
**Closed**: 2026-08-05 (root cause proven + fix verified PASS)

## Symptom

When the user presses "Sync Selected Mesh to UE (FBX)", the FIRST sync attempt does not
run (no import in UE), but a SECOND attempt succeeds. Observed in runtime tests:

| TRY | Result | Evidence |
|-----|--------|----------|
| TRY-1 (08.15.14, conn=1→2) | FAIL | Blender enqueued 0x60; UE never received it (disconnect 08.15.18 / accept 08.15.20) |
| TRY-2 (08.15.21, conn=3) | PASS | 0x60 received, import ran |
| TRY-3 (15:49, conn=1→2) | FAIL | Blender `send_msg_enqueued` 08:50:02.9; UE `TRANSPORT_DISCONNECT conn=1` 08:50:02.587, `Accept conn=2` 08:50:03.589; 0x60 never received |
| TRY-4 (15:52, conn=2) | PASS | 0x60 received, import ran (returned=78) |

In every FAIL case the 0x60 was handed to the Blender transport during a connection
transition (conn=1→conn=2).

## Root Cause (CONFIRMED)

The 0x60 does not "die in a transition". It dies **because UE closes the socket during
the Blender export**, before Blender even enqueues the 0x60:

1. `check_updates()` (Blender main-loop timer, `sync.py`) was the sole sender of the
   0x07 heartbeat, every `heartbeat_interval` (5 s).
2. `UELIVESYNC_OT_sync_selected_mesh_to_ue_fbx.execute()` runs **synchronously** on the
   Blender main thread and calls `bpy.ops.export_scene.fbx` (`__init__.py:2210 → 891`),
   blocking the whole main loop for the export duration.
3. During the block, no heartbeat was enqueued → UE's `LastHeartbeatTime` (updated only
   by 0x07, `UELiveSyncSubsystem.cpp:3435`) froze.
4. UE game-thread tick check (`UELiveSyncSubsystem.cpp:1968-1996`): when
   `Now - LastHeartbeatTime > UE.LiveSync.HeartbeatTimeout (15 s)` it called
   `ProcessQueuedPackets()` then `StopNetworkThread()`.
5. Silence = time since last heartbeat + export duration. TRY-3: last heartbeat
   `08:49:47.580` (seq=15), export `08:49:49.5 → 08:50:03.005` (13.5 s), timeout fired
   `08:50:02.587` (`secondsSince=15.01`) — 0.4 s before export completed → conn=1 killed.
6. Blender enqueued the 0x60 at ~08:50:02.9 into the dead socket → never reached UE.
7. A second attempt succeeds because the fresh connection resets `LastHeartbeatTime`, so
   the silence across a subsequent export (12.3 s in TRY-4) stays under the timeout.

Survival margin was only ~1.5–3.5 s (export 13.5 s vs timeout 15 s), so the failure was
timing-dependent and intermittent.

### Evidence timeline (TRY-3, `ProjectTemplate.log`)

```
08:49:47.580  [PACKET_DISPATCH] type=0x07 seq=15   (last heartbeat → LastHeartbeatTime)
~08:49:49.5   user presses Sync → bpy.ops.export_scene.fbx starts (blocks main loop)
08:50:02.587  [HEARTBEAT_TIMEOUT] secondsSince=15.01 timeout=15.00 → StopNetworkThread()
~08:50:02.9   Blender [FBX_ENQUEUE] 0x60 handed to dead socket
08:50:03.005  [DIAG][FBX_OP_DONE] totalMs=13502.4
08:50:03.589  TRANSPORT_ACCEPT_OK generation=2 (conn=2)
```

TRY-4 contrast: seq=50 last heartbeat `08:52:23.816`, FBX_IMPORT_REQUEST `08:52:35.525`
→ 12.3 s silence < 15 s → survived.

## Hypotheses Resolution

- **H1 — Packet lost on socket during connection transition: CONFIRMED.** The
  "transition" is UE's heartbeat-timeout disconnect fired mid-export. Root cause is the
  heartbeat starvation, not a queue/socket race.
- **H2 — Packet reaches UE but discarded in the pipeline: ELIMINATED.** The 0x60 never
  reached UE's socket (connection dead before Blender enqueued it; no recv log for it).
- **H3 — Import runs before sidecars ready, skipped: ELIMINATED.** No
  FBX_IMPORT_REQUEST was ever dispatched on the dead connection.

## Fix

Two-step (per user decision, 2026-08-05):

**Step 1 — Hotfix (mitigation, UE):** raised `UE.LiveSync.HeartbeatTimeout` default
from 15 s to 30 s (`UELiveSyncSubsystem.cpp:612`). Worst-case measured export 13.5 s;
30 s = export + heartbeat interval + margin. Documented in the CVar tooltip as INV-2026-019
hotfix. This alone is NOT the root-cause fix (a 35–40 s export would still break, and
dead-connection detection becomes slower).

**Step 2 — Root-cause fix (Blender):** heartbeat no longer depends on the Blender main
thread.
- `network.py`: added `_heartbeat_interval` + `set_heartbeat_interval()`; `LiveSyncClient`
  now starts a dedicated daemon thread `_heartbeat_loop` that enqueues 0x07 every
  `heartbeat_interval` seconds via the existing thread-safe `send_packet`/`_send_queue`
  path (socket writes already happen on the daemon sender thread, so a blocked main loop
  can never starve the heartbeat). Logs `[DIAG][HB_THREAD]`. `stop()` joins the thread.
- `sync.py`: removed the 0x07 send block from `check_updates()` (main-loop callback);
  `check_updates()` now forwards the user-configured `heartbeat_interval` to the transport
  via `_network_set_heartbeat_interval()`.

## Verification (2026-08-05, fresh session, user-launched apps)

- Session: UE PID 110603, Blender PID 112420, port 57000 LISTEN, boundary 16:18:46.
- Addon reloaded: `[DIAG][HB_THREAD] sent ... interval=5.0` present in
  `uelivesync_blender_debug.log`; UE heartbeat cadence seq=1..15 every ~5 s, no
  `HEARTBEAT_TIMEOUT`.
- Sync test: export `totalMs=13639.8` (13.6 s). HB_THREAD during export window:
  ts=143.705 / 149.013 / 155.061 (gaps 5.3–6.0 s, continuous). No
  `HEARTBEAT_TIMEOUT`, no `TRANSPORT_DISCONNECT`.
- UE received the 0x60 on the FIRST attempt: `[BRIDGE][FBX_IMPORT_REQUEST]`
  `09.19.17:188` (ts matches FBX_OP_DONE `1785921557.185`), import ran
  (`[MATERIAL][FBX_IMPORTED_APPLY]` slots 0/1).
- Result: **PASS** — first sync no longer lost on a 13.6 s export.

## Invariants (unchanged)

- Wire protocol 0x60 frozen.
- No retry / reconnect mitigation added (heartbeat thread is the root-cause fix, not a
  symptom patch).
- `manifest_v3.py`, `fbx_protocol.py`, `MessageTypes.yaml`, addon __init__.py untouched.
- UE change: CVar default only (no control-flow change to networking).

## Remaining work

- The `import_assets_returned_zero` warning seen during the verification import is
  **Bug B** (canonical-name mismatch in texture matching, INV-2026-018): UE sanitizes
  `.` → `_` in asset names but matching uses `FPaths::GetBaseFilename` keeping the dot.
  Textures ARE imported (returned=78, in-memory only); the warning is misleading.
  Tracked separately; fix pending after this INV.
