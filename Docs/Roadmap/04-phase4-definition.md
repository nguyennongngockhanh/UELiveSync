# Phase 4 — Production Hardening & Editor Tooling

**Status**: Defined (not started) · **Estimate**: 2–3 days · **Risk**: Low

---

## Goal

Close all remaining gaps between the current system and production readiness. No new features — only polish, diagnostics, abuse tolerance, and editor UX.

---

## Phase 4A — Stability Core

Foundation work: doc accuracy, port hygiene, dedicated log category, console diagnostics, throttling, overflow protection, and protocol validation. Everything in 4A can be implemented, tested, and committed independently — no cross-file dependencies between items.

### E1 — Fix CVar defaults in protocol doc

| File(s) | What |
|---------|------|
| `Docs/Architecture/05-network-protocol.md` | Fix CVar defaults to match code: `StateTTL=60.0`, `InterpSnap=0.1`, `Threshold.Rotation=0.002`. Document Blender port fallback. |

### E2 — Fix stale GUID invariants doc

| File(s) | What |
|---------|------|
| `Docs/Architecture/08-guid-invariants.md` | Fix the stale `ensure_unique_guid` claim (it IS implemented — line 80 ref). Add port fallback note. |

### A1 — Fix port fallback

| File(s) | What |
|---------|------|
| `sync.py:698` | Fix port fallback from 5000→57000 (pref load failure leads to silent misconnect on wrong port) |

### C1 — Dedicated log category

| File(s) | What |
|---------|------|
| `UELiveSyncSubsystem.cpp`, `LiveSyncRunnable.cpp` | Replace all `LogTemp` → new `DEFINE_LOG_CATEGORY_STATIC(LogLiveSync, Log, All)` — clean separation from engine noise |

### C2 — Console commands

| File(s) | What |
|---------|------|
| `UELiveSyncSubsystem.cpp` | Register 3 console commands via `IConsoleManager::RegisterConsoleCommand`:
  - `UE.LiveSync.DumpState` — print all tracked GUIDs, bound actors, last-update timestamps, queue depth
  - `UE.LiveSync.Reset` — full teardown & restart (close socket, clear states, reinit)
  - `UE.LiveSync.Ping` — send echo packet to Blender, measure round-trip in ms |

### D1 — Per-tick rate cap

| File(s) | What |
|---------|------|
| `UELiveSyncSubsystem.cpp:Tick` | `int32 PacketsProcessedThisTick = 0; if (++PacketsProcessedThisTick > MaxPacketRate) break;` with new CVar `UE.LiveSync.MaxPacketRate` (default 200). Overflow stays in queue for next tick. |

### D2 — Queue overflow warning

| File(s) | What |
|---------|------|
| `LiveSyncQueue.h` | Log `WARNING` when dropping oldest packet (currently silent). Add CVar `UE.LiveSync.QueueWarnThreshold` (default 64). |

### D3 — Network thread watchdog

| File(s) | What |
|---------|------|
| `LiveSyncRunnable.cpp`, `UELiveSyncSubsystem.cpp` | Thread writes `LastActivityTime` each loop iteration; `Tick()` checks `Now - LastActivityTime > 30s` → log CRITICAL, teardown & restart thread |

### E3 — Packet size validation

| File(s) | What |
|---------|------|
| `LiveSyncRunnable.cpp` | Validate `PacketSize` matches `sizeof(FPacketHeaderV3) + ObjectCount * ObjectSize` before dispatching. Log WARNING + skip on mismatch. |

### E4 — Protocol type/flag validation

| File(s) | What |
|---------|------|
| `UELiveSyncSubsystem.cpp` | Add CVar `UE.LiveSync.ValidateProtocol` (default 1). Validates every packet's type byte is in `{0x01,0x03,0x04,0x07}` and flags in `{0x00,0x01,0x02,0x03}`. Log WARNING on invalid. |

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
