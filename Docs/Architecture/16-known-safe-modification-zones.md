# Known Safe Modification Zones

> Zones in the codebase ranked by risk level for modification.
> Use this document to plan Phase 6 features without destabilizing
> the Phase 5 runtime core.

---

## RISK LEVELS

| Level | Label | Guideline |
|-------|-------|-----------|
| 🟢 SAFE | Can modify freely | New features, UI, tooling |
| 🟡 CAUTION | Modify with care | Well-understood subsystems isolated from core pipeline |
| 🔴 HIGH RISK | Avoid unless critical bug | Core pipeline, parser, queue ownership, threading |
| ⛔ FROZEN | Do not modify | Wire format, queue ownership, thread safety invariants |

---

## 🟢 SAFE — Editor-Side Replication Features

| Area | Files | Description |
|------|-------|-------------|
| UE→Blender TCP channel | New files | Adding UE→Blender socket sender for editor-side replication. New infrastructure, not modifying existing |
| Rename replication | New packets, new Tick stage | Can add new packet type and Tick stage for rename without modifying existing pipeline order |
| Collection/folder sync | New packets, new Tick stage | Same pattern as rename — new packet type, new Tick stage after PurgeStaleActors |
| Visibility sync | New packets, new Tick stage | Same pattern |
| Managed actor tag logic | New utility functions | Tag/untag, filtering, detection — no core pipeline impact |
| Actor class whitelist | New config/CVar | Only affects actor filtering, not packet processing |
| Late-join snapshot trigger | New Blender feature | Snapshot generation on Blender side (PT_BEGINSNAPSHOT/ENDSNAPSHOT already defined) |

### SAFE Preconditions
- ✅ Uses new packet types (PT_* constants not yet defined)
- ✅ Adds new Tick stages AFTER existing stages (not reordering or inserting into existing stages)
- ✅ Does not modify FSyncTransformState layout or header layout
- ✅ Does not change queue ownership or thread access patterns
- ✅ Does not modify existing Parser functions
- ✅ Does not add new fields to existing wire format

---

## 🟢 SAFE — UI / Tooling

| Area | Files | Description |
|------|-------|-------------|
| Diagnostics panel | SLiveSyncDiagnosticsWidget.cpp/h | UI refresh, new stats, visual improvements |
| Status indicator | SLiveSyncStatusWidget.cpp/h | Same — cosmetic changes only |
| Editor tab registration | UELiveSyncEditorModule.cpp/h | New UI elements, menu entries |
| Console commands | UELiveSyncSubsystem.cpp | New CVars, `Exec()` handlers |
| Metrics display | FLiveSyncStats | New display-only counters |

### SAFE Preconditions
- ✅ Uses existing FLiveSyncStats counters with `std::memory_order_relaxed`
- ✅ Does not change counter update paths in the hot Tick pipeline
- ✅ Adds new counters via existing pattern (no new thread-safety requirements)

---

## 🟢 SAFE — Replication Policies

| Area | Files | Description |
|------|-------|-------------|
| Flood detection thresholds | LiveSyncQueue.h / CVars | Tuning MaxPacketRate, QueueWarnThreshold |
| Interpolation mode | CVar UE.LiveSync.InterpMode | Changing interpolation mode switch |
| Rate limiting | LiveSyncQueue.h | Adding new rate limits (rename rate, etc.) |
| Coalescing timers | New | Rename coalescing, change batching |

### SAFE Preconditions
- ✅ Uses CVar gates that already exist or adds new CVars on the same pattern
- ✅ Does not change queue capacity or drop policy
- ✅ Does not change pipeline ordering

---

## 🟡 CAUTION — With Care

| Area | Risk | Mitigation |
|------|------|------------|
| Adding new packet types | New PT_* constant + parsing branch in ProcessBinaryPacket | Must NOT modify existing branch paths; must add new `case` after existing dispatch |
| Adding new Tick stage | New stage inserted AFTER PurgeStaleActors | Must NOT reorder existing stages; must add at end of pipeline |
| Changing Blender-side serialization | Add new fields to sync payload | Must gate behind new protocol version; must keep backward compat with V4/V5 |
| Snapshot generation (Blender) | Must not block main thread; must handle large scenes | Follow existing snapshot infrastructure; throttle packet rate |
| Diagnostics metrics expansion | Adding new counters | Use `std::memory_order_relaxed` only; O(1) update; no allocation |
| Debug draw changes | New visual overlays | Must gate behind `UE.LiveSync.DebugDraw` CVar; zero overhead when disabled |

### CAUTION Invariants
- Any new packet type must go through ProcessBinaryPacket dispatch (no bypass)
- Any new Tick stage must have paired BEGIN/END UE_LOG trace markers
- Any new Blender serialization must use little-endian `struct.pack('<...')`
- Any new metric must be O(1) update and use `std::memory_order_relaxed`

---

## 🔴 HIGH RISK — Avoid Unless Critical Bug

| Area | Reason | What Could Go Wrong |
|------|--------|---------------------|
| **Packet parser** | Core wire format; version dispatch; backward compat | Malformed packet handling, crash on unexpected data, backward compat breakage |
| **Tick pipeline ordering** | Every stage depends on previous stage's output | Transform applied before spawn; asset resolution before actor exists; BEGIN/END imbalance |
| **Queue ownership** | Thread-safe enqueue/dequeue inviolate | Data races, use-after-free, queue corruption |
| **Network thread lifecycle** | Deadlock-vulnerable shutdown sequence | Game thread deadlock on reconnect/disconnect |
| **FSyncTransformState layout** | Wire format match | Binary protocol incompatibility between Blender and UE versions |
| **Heartbeat timeout** | Connection state machine | False disconnect, reconnect storm |
| **GUID persistence (UE side)** | Session identity | Double-spawn, missing actors, duplicate GUIDs |

### HIGH RISK Rules
- No modifications to ProcessBinaryPacket unless a specific crash scenario is reproduced
- No reordering of Tick pipeline stages
- No changes to FLiveSyncQueue or FLiveSyncPendingAssetQueue ownership model
- No changes to StopNetworkThread shutdown order
- No changes to FSyncTransformState struct fields
- No changes to 24-byte header layout

---

## ⛔ FROZEN — Do Not Modify

| Item | Rationale |
|------|-----------|
| Packet magic `0x4C56534D` | Wire protocol identity; changing would break all existing connections |
| 24-byte header layout | All parsers depend on this layout; all protocol versions share it |
| V4+ 81-byte object layout with primitive byte at offset 80 | Matches wire format; changing requires V6 |
| Thread ownership: network thread enqueue only | Core safety invariant |
| Thread ownership: game thread Tick processing only | Core safety invariant |
| StopNetworkThread shutdown order | Linux deadlock prevention |
| BEGIN/END tracing at every Tick stage | Diagnostic invariant; removing would blind future debugging |
| Queue capacity: FLiveSyncQueue = 128 | Validated against MaxPacketRate; changing requires re-validation |
| Queue capacity: FLiveSyncPendingAssetQueue = 2048 | Validated; changing requires re-validation |

---

## Summary Table

| Zone | Risk Level | Phase 6 Action |
|------|-----------|----------------|
| Editor replication features | 🟢 SAFE | Add new files, packets, stages |
| UI / Tooling | 🟢 SAFE | Modify existing or new files |
| Replication policies | 🟢 SAFE | CVar tuning, new rate limits |
| New packet types | 🟡 CAUTION | Add with care, don't touch existing dispatch |
| New Tick stages | 🟡 CAUTION | Append after PurgeStaleActors |
| Blender serialization | 🟡 CAUTION | New protocol version, keep backward compat |
| Packet parser | 🔴 HIGH RISK | Do not touch |
| Tick pipeline order | 🔴 HIGH RISK | Do not reorder |
| Queue ownership | 🔴 HIGH RISK | Do not change |
| Network thread lifecycle | 🔴 HIGH RISK | Do not change |
| FSyncTransformState | ⛔ FROZEN | Do not modify |
| Header layout | ⛔ FROZEN | Do not modify |
| Thread ownership rules | ⛔ FROZEN | Do not change |
| BEGIN/END tracing | ⛔ FROZEN | Do not remove |
