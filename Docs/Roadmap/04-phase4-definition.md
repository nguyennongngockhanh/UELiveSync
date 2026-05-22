# Phase 4 — Production Hardening & Editor Tooling

**Status**: Phase 4A completed · Phase 4B–D pending · **Estimate**: 2–3 days · **Risk**: Low

---

## Goal

Close all remaining gaps between the current system and production readiness. No new features — only polish, diagnostics, abuse tolerance, and editor UX.

---

## Phase 4A — Stability Core ✅

Foundation work: doc accuracy, port hygiene, dedicated log category, console diagnostics, throttling, overflow protection, and protocol validation. All items completed in commit `c5ec811`.

### ✅ E1 — Fix CVar defaults in protocol doc

| File(s) | What |
|---------|------|
| `Docs/Architecture/05-network-protocol.md` | Fixed `StateTTL=60.0`, `InterpSnap=0.1`, `InterpMode=1`, `Threshold.Location=0.05`, `Threshold.Rotation=0.002`. Added port fallback note. |

### ✅ E2 — Fix stale GUID invariants doc

| File(s) | What |
|---------|------|
| `Docs/Architecture/08-guid-invariants.md` | Fixed the stale `ensure_unique_guid` claim — confirmed implemented at `sync.py:106`. |

### ✅ A1 — Fix port fallback

| File(s) | What |
|---------|------|
| `sync.py:698` | Port fallback changed from 5000→57000. |

### ✅ C1 — Dedicated log category

| File(s) | What |
|---------|------|
| `SyncTypes.h`, `UELiveSyncSubsystem.cpp`, `LiveSyncRunnable.cpp` | Replaced all `LogTemp` → `LogLiveSync`. Declared in `SyncTypes.h` via `DECLARE_LOG_CATEGORY_EXTERN`, defined in `UELiveSyncSubsystem.cpp`. |

### ✅ C2 — Console commands

| File(s) | What |
|---------|------|
| `UELiveSyncSubsystem.cpp/.h` | Registered 3 commands via `IConsoleManager::RegisterConsoleCommand`:
  - `UE.LiveSync.DumpState` — prints all tracked GUIDs, actors, queue depth
  - `UE.LiveSync.Reset` — full teardown & restart
  - `UE.LiveSync.Ping` — prints connected/queue/states counters |

### ✅ D1 — Per-tick rate cap

| File(s) | What |
|---------|------|
| `UELiveSyncSubsystem.cpp` | Added `CVarLiveSyncMaxPacketRate` (default 200). `ProcessQueuedPackets()` caps dequeue per tick; overflow stays in queue. Warning logged when cap exceeded. |

### ✅ D2 — Queue overflow warning

| File(s) | What |
|---------|------|
| `LiveSyncQueue.h`, `UELiveSyncSubsystem.cpp` | `Enqueue()` logs `WARNING` when dropping oldest packet. CVar `UE.LiveSync.QueueWarnThreshold` (default 64). |

### ✅ D3 — Network thread watchdog

| File(s) | What |
|---------|------|
| `LiveSyncRunnable.h/.cpp`, `UELiveSyncSubsystem.cpp` | `LastActivityTime` atomic updated each loop iteration. `Tick()` checks 30s inactivity → log Error + `StopNetworkThread()`. |

### ✅ E3 — Packet size validation

| File(s) | What |
|---------|------|
| `LiveSyncRunnable.cpp` | V2: exact match `PayloadSize == ObjectCount × 56`. V3: minimum `PayloadSize >= ObjectCount × 16`. Logs WARNING on mismatch, skips packet. |

### ✅ E4 — Protocol type/flag validation

| File(s) | What |
|---------|------|
| `UELiveSyncSubsystem.cpp` | `CVarLiveSyncValidateProtocol` (default 1). Validates type ∈ {0x01,0x03,0x04,0x07} and flags ∈ {0x00,0x01,0x02,0x03}. Logs WARNING + skips on invalid. |

---

## Phase 4B — Runtime Controls

User-facing addon configuration and diagnostics. Depends on 4A for stable log category and console infrastructure.

### A2 — Expose heartbeat/scan intervals as prefs

| File(s) | What |
|---------|------|
| `__init__.py` (prefs), `sync.py:57,63` | Expose `heartbeat_interval` and `scan_interval` as addon `IntProperty`/`FloatProperty` (currently hardcoded globals). Read from prefs in the tick loop. |

### A3 — Sidebar panel counters

| File(s) | What |
|---------|------|
| `__init__.py:196–304` | Add sidebar counters: tracked object count, packets queued, reconnection count, uptime. Bubbled up from `sync.py`/`network.py` state. |

### A4 — Expand CRITICAL severity triggers

| File(s) | What |
|---------|------|
| `network.py:406–410` | Expand beyond "port in use": add protocol mismatch, deserialization failure, persistent reconnect failure (>30s). |

### C4 — Blender diagnostic dump

| File(s) | What |
|---------|------|
| `sync.py`, `__init__.py` | Add operator that prints `tracked_objects` count, reconnect stats, queue depth to Blender console. |

---

## Phase 4C — Editor Tooling

UE editor-side Slate widget for connection visibility. Depends on 4A (C1 log category, C2 console commands) and 4B (metrics state).

### B1 — Slate widget

| File(s) | What |
|---------|------|
| `Public/UELiveSyncEditorWidget.h`, `Private/UELiveSyncEditorWidget.cpp` | Widget showing: connection status (green/red dot), connected-since timestamp, objects tracked, queue depth, last packet time |

### B2 — Expose runtime state

| File(s) | What |
|---------|------|
| `UELiveSyncSubsystem.cpp/.h` | Expose runtime state as `FText` properties for widget to poll each tick |

### B3 — Register status bar icon

| File(s) | What |
|---------|------|
| `UELiveSyncEditorModule.cpp` | Register status bar icon or standalone window (Window → Developer Tools → LiveSync Status) |

### B4 — Editor module build deps

| File(s) | What |
|---------|------|
| `UELiveSync.Build.cs` | Add `"EditorWidgets"`, `"StatusBar"`, `"ToolMenus"` deps (editor-only module, wrap in `WITH_EDITOR`) |

**Design principle**: Zero UI when everything is fine. Green dot in status bar. Red dot + tooltip when disconnected.

---

## Phase 4D — Validation

Test suite for every Phase 4 item. Depends on all prior phases being implemented.

| Item | File(s) | What |
|------|---------|------|
| **F1** | `tests/phase4_validation_A_prefs.py` | Blender-side: load addon, change each pref, verify thresholds reflected in `transforms_different()`, verify port change affects connection target |
| **F2** | `tests/phase4_validation_B_overflow.py` | UE-side: flood 500 packets in one tick, verify 200 processed (rate cap), warning logged, remaining queued |
| **F3** | `tests/phase4_validation_C_diagnostics.py` | UE-side: send `DumpState` + `Ping`, verify expected output |
| **F4** | `tests/phase4_validation_D_watchdog.py` | Simulate thread hang, verify watchdog triggers restart within 30s |
| **F5** | `tests/phase4_validation_E_protocol.py` | UE-side: send invalid type byte `0xFF`, invalid flags, mismatched PacketSize — verify each is rejected with WARNING log |

---

## Risk Assessment

| Risk | Probability | Impact | Mitigation |
|------|------------|--------|------------|
| Slate widget causes editor startup crash | Low | High | Wrap in `WITH_EDITOR` ifdef; unit-test in standalone |
| Watchdog false-positive during heavy load | Low | Medium | 30s threshold is generous (normal recv timeout is 10ms) |
| Rate cap drops legitimate traffic during normal operation | Low | Low | 200 pkts/tick = 12000 pkts/sec, well above expected 100–300/sec |
| Console command crashes if called mid-disconnect | Medium | Medium | Guard all commands with `if (ConnectionSocket \|\| NetworkRunnable)` checks |

---

## Ordering

| Step | Phase | Why |
|------|-------|-----|
| 1 | **4A** first | Doc fixes, port hygiene, logging infra, throttling, and protocol validation — pure stability, no features |
| 2 | **4B** second | Addon prefs + diagnostics depend on stable infra from 4A |
| 3 | **4C** third | Editor UI depends on C1 (log cat) and metrics state from 4B |
| 4 | **4D** last | Validation tests for all of the above |
