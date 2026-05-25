# Phase 6 Readiness Checklist

> Required conditions before officially starting Phase 6
> (Live Editing System) implementation.

---

## Condition Status

| # | Condition | Status | Evidence |
|---|-----------|--------|----------|
| 1 | Runtime stable | ✅ COMPLETE | 6h38m validated session: 46,400 Tick frames, 232K balanced BEGIN/END traces, 14,268 SetActorTransform calls, 0 crashes |
| 2 | Reconnect stable | ✅ COMPLETE | Reconnect storm test (20 cycles) PASS; thread shutdown order validated; contains(`Contains()` guard) |
| 3 | Fuzz tests passing | ✅ COMPLETE | `phase5c_fuzz_protocol.py` PASS — malformed headers, garbage payloads, zero-size, over-claiming, wrong MAGIC |
| 4 | Queue tests passing | ✅ COMPLETE | Overflow test (300 instant packets) PASS — drop-oldest works, queue recovers, no crash |
| 5 | Roadmap frozen | ✅ COMPLETE | `00-consolidated-roadmap.md` frozen — Phase 5 = COMPLETE, Phase 6 = NOT STARTED, Phase 7+8+9 = NOT STARTED |
| 6 | Authority model documented | ✅ COMPLETE | `13-phase6-design-constraints.md` — authority questions documented for rename, visibility, hierarchy, duplicate |
| 7 | Runtime invariants documented | ✅ COMPLETE | `12-core-runtime-invariants.md` — packet lifecycle, thread ownership, queue ownership, Tick ordering, parser invariants, object layout, reconnect guarantees |
| 8 | Editor sync safety rules documented | ✅ COMPLETE | `14-editor-sync-safety.md` — replication suppression rules, feedback loop prevention, rename storm prevention, stale GUID recovery |
| 9 | Architecture decisions documented | ✅ COMPLETE | `15-architecture-decision-records.md` — 15 ADRs covering protocol, threading, queue, pipeline, shutdown |
| 10 | Safe modification zones documented | ✅ COMPLETE | `16-known-safe-modification-zones.md` — SAFE / CAUTION / HIGH RISK / FROZEN zones |
| 11 | Core source frozen | ✅ COMPLETE | Freeze banners added to: `UELiveSyncSubsystem.cpp`, `PendingAssetQueue.h`, `LiveSyncQueue.h`, `SyncTypes.h`, `LiveSyncRunnable.h` |
| 12 | Release tag created | ✅ COMPLETE | `v0.5.0-stabilized` tagged locally at commit `4219d80` |
| 13 | No stale Phase 6/7 references | ✅ COMPLETE | All test files renamed to phase5*; doc references corrected |
| 14 | Profiling/debug infrastructure preserved | ✅ COMPLETE | Freeze comments warn against removing TRACE_CPUPROFILER_EVENT_SCOPE, BEGIN/END tracing, runtime metrics, diagnostics CVars |

---

## Outcome: All 14 conditions COMPLETE

**Phase 6 is clear to begin** when development resources and feature scope are defined.

---

## Phase 6 First Actions (when started)

1. Establish UE→Blender TCP channel (new socket sender on UE game thread, new listener thread on Blender)
2. Implement `UELiveSync_Managed` actor tag scheme
3. Implement change origin tagging (`EChangeOrigin::BlenderSync` / `User` / `UESync`)
4. Implement rename replication (new packet type, new Tick stage)
5. Implement collection/folder sync (new packet type, new Tick stage)
6. Implement visibility sync (new packet type, new Tick stage)
7. Implement late-join snapshot trigger on Blender side
8. Add actor class whitelist for editor-only filtering
9. Add undo/redo transaction suppression
10. Add PIE mode suppression

> See `13-phase6-design-constraints.md` for unresolved design questions
> that must be answered before implementing items 2-10.
