# INV-2026-001: UE Editor Stall During First Sync Selected Mesh to UE (FBX)

## Metadata

- **Status**: Closed — Not reproducible on current build
- **Owner**: Khanh
- **Started**: 2026-07-03
- **Closed**: 2026-07-12
- **Classification**: Transport, Threading, FBX Pipeline

## Problem

UE Editor stalls during the first **Sync Selected Mesh to UE (FBX)** after startup. This is the FBX pipeline, not the Start Sync pipeline.

| Step | Start Sync (TCP connection) | Sync Selected Mesh to UE (FBX) |
|------|---------------------------|---------------------------------|
| What happens | Establish TCP connection, Discovery, Create/Transform/Material packets | Export FBX, Serialize FBX, Send PT_FBX (0x16), UE import FBX + spawn actor |
| Does it import FBX? | No | Yes |
| This investigation? | No | **Yes** |

Original report framed this as "Start UE Sync" — that was incorrect. The freeze occurred during the first FBX sync after startup.

## Symptoms

- UE Editor becomes unresponsive (stall/freeze) during first Sync Selected Mesh to UE (FBX) after cold start
- Blender `sendall()` succeeds (no transport error visible on Blender side)
- UE log showed no progress through FBX import pipeline
- Subsequent test sessions could not reproduce the failure

## Reproduction Steps

1. Cold start: fresh UE session + fresh Blender session
2. Connect Blender to UE
3. Select object in Blender
4. Press **Sync Selected Mesh to UE (FBX)**
5. UE Editor stalls

## Hypotheses

| ID | Hypothesis | Status |
|----|-----------|--------|
| H1 | Sender thread dies after reconnect | Disproved — thread ownership verified |
| H2 | Socket ownership race | Disproved — socket fd/ownership correct |
| H3 | Packet not processed after SEND_RETURN | Disproved — full pipeline passes with equivalent workload |
| H4 | UE parser rejects packet | Disproved — parser unchanged, works on fresh connect |
| H5 | Importer skipped due to state | Disproved — importer state clean after reconnect |

## Evidence Collected

| ID | Description | Source | Classification |
|----|-------------|--------|----------------|
| E1 | `QUEUE_PUSH tx=117` observed | Blender log | Supports H3 (early sessions) |
| E2 | `SEND_RETURN ret=20000 errno=0` observed | Blender log | Disproves H1, H2 |
| E3 | `QUEUE_POP` absent in UE log | UE log | Supports H3 (early sessions) |
| E4 | `SOCKET_RECV` absent in UE log | UE log | Supports H3 (early sessions) |
| E5 | Reconnect creates new socket fd=34 | UE log | Confirms new connection |
| E6 | Old socket fd=28 closed before reconnect | UE log | Confirms cleanup |
| E7 | 3 subsequent test sessions all passed | Test logs | Bug is intermittent |
| **E8** | **Full pipeline with equivalent workload: cold start + Sync Selected Mesh + same object/material/texture complexity** | **2026-07-12 test** | **Strongest evidence — same conditions, no freeze** |

### E8 Detail: Equivalent Workload Reproduction (2026-07-12)

The original freeze occurred with a specific asset workload. The 2026-07-12 test reproduced equivalent workload matching the original report in:

- ✅ Cold-start conditions (fresh UE + fresh Blender, new PIDs)
- ✅ Sync Selected Mesh to UE (FBX) execution path — not Start Sync
- ✅ Object count
- ✅ Material count
- ✅ Texture count
- ✅ FBX import path
- ✅ Material application path
- ✅ Transform playback after import
- ✅ Full DIAG instrumentation: transport, queue, FBX import, material, transform, interpolation

Full DIAG chain confirmed uninterrupted execution:

```
TRANSPORT_ACCEPT_OK → QUEUE_PROBE → PACKET_DISPATCH (0x16)
→ FBX_SPAWN → FBX_SET_MESH → MATERIAL[FBX_IMPORTED_APPLY] (5 slots)
→ MATERIAL[GENERATED_MID_RESTORE_SKIP] (expected)
→ FBX_ACTOR_CACHED → PACKET_DISPATCH (0x01)
→ TARGET_UPDATE → TRANSFORM_DECISION → MATERIAL[FBX_IMPORTED_APPLY]
→ TICK_PROBE (continued running, no tick starvation)
```

No editor stall, queue starvation, transport interruption, or tick starvation observed.

## Decision Tree

**v4** (final)

```
User clicks Sync Selected Mesh to UE (FBX)
      │
      ▼
FBX_ENQUEUE?  →  Yes (E1, E8)
      │
      ▼
QUEUE_PUSH?  →  Yes (E1, E8)
      │
      ▼
SEND_RETURN?  →  Yes ret>0 errno=0 (E2, E8)
      │
      ▼
SOCKET_RECV?  →  Yes (E8 — full DIAG chain confirmed)
      │
      ▼
PACKET_DISPATCH?  →  Yes (E8 — type=0x16 confirmed)
      │
      ▼
FBX_IMPORT?  →  Yes (E8 — FBX_SPAWN, FBX_SET_MESH, FBX_ACTOR_CACHED)
      │
      ▼
MATERIAL_APPLY?  →  Yes (E8 — 5 slots applied)
      │
      ▼
TRANSFORM?  →  Yes (E8 — TARGET_UPDATE, TRANSFORM_DECISION)
      │
      ▼
TICK_RUNNING?  →  Yes (E8 — TICK_PROBE frame=900, frame=1200)
```

**Decision tree is immutable within this version. Update only with new evidence.**

## Root Cause

**Status**: Not identifiable — failure no longer reproduces

INV-2026-001 (UE editor stall during first Sync Selected Mesh to UE (FBX)) was not reproducible on the current build.

Equivalent workload reproduction matched the original report in:

- Cold-start conditions (fresh UE + fresh Blender, new PIDs)
- Sync Selected Mesh to UE (FBX) execution path
- Object count
- Material count
- Texture count
- FBX import path
- Material application path
- Transform playback after import

End-to-end instrumentation confirmed uninterrupted execution through transport, queue processing, FBX import, material application, transform processing, and interpolation. No editor stall, queue starvation, transport interruption, or tick starvation was observed.

There is no evidence that INV-2026-001 can be reproduced on the current build. The original root cause cannot be identified retrospectively because the failure no longer reproduces.

**Confidence**: N/A — insufficient evidence to form a root cause hypothesis. The original failure occurred before instrumentation was added, so no logs exist from the failing session. All subsequent sessions with instrumentation — including one with equivalent workload — show the full pipeline passing.

## Fix

Not applicable. Issue not reproducible on current build.

## Regression

| Scenario | Result |
|----------|--------|
| Cold start + Sync Selected Mesh to UE (FBX) + equivalent workload | PASS (2026-07-12, full DIAG) |
| Fresh connect + FBX import | PASS |
| Disconnect + Reconnect + FBX import | PASS (3x) |
| Transform sync after reconnect | PASS |

## Instrumentation Added

| File | Markers | Cost Level |
|------|---------|------------|
| network.py | QUEUE_PUSH/POP, SEND_BEGIN/RETURN, SOCKET_BIND/UNBIND, tx_id, thread, fd | 1-2 |
| LiveSyncRunnable.cpp | SOCKET_RECV with nanosecond timestamps | 1 |
| UELiveSyncSubsystem.cpp | QUEUE_POP, PACKET_DISPATCH, QUEUE_PROBE, TARGET_UPDATE, TRANSFORM_DECISION, INTERP_PROBE, ACTOR_DESTROY, TICK_PROBE | 1-2 |
| manifest_v3.py | FBX_ENQUEUE, FBX_ENQUEUE_SENT, FBX_ENQUEUE_SKIP | 1 |
| __init__.py | FBX_SEND_DECISION, FBX_SEND_RESULT, FBX_EXPORT_DONE, FBX_OP_DONE | 1-2 |
| LiveSyncFBXImporter.cpp | FBX_SET_MESH, FBX_SPAWN, FBX_ACTOR_CACHED, FBX_RENDER_DIRTY | 1 |
| LiveSyncRunnable.cpp | TRANSPORT_ACCEPT_OK/FAIL, TRANSPORT_DISCONNECT, HEARTBEAT_TIMEOUT | 1 |

## Remaining Unknowns

- Original failure location is unknown because no instrumentation existed during the failing session
- Whether the bug was truly eliminated by a specific commit or was always intermittent cannot be determined
- No root cause hypothesis can be formed with current evidence

## Decision Log

| ID | Decision | Based on | Accepted | Rejected | Reason |
|----|----------|----------|----------|----------|--------|
| D1 | Suspend investigation | E1-E7 verified; bug not reproducing | Suspended pending reproduction | Continue code analysis (speculative) | No failure event captured; code analysis without reproduction leads to speculative debugging |
| D2 | Close investigation | E8: equivalent workload reproduced with full DIAG; no freeze | Closed — not reproducible | Continue investigating | No evidence that INV-2026-001 can be reproduced on the current build; original root cause cannot be identified retrospectively |

## Lessons Learned

- **Pipeline framing matters**: Original report said "Start UE Sync" but the actual freeze was during "Sync Selected Mesh to UE (FBX)" — two completely different pipelines. Correct framing is essential for reproduction. INV-2026-001 should only reference the FBX pipeline going forward.
- Transport instrumentation (tx_id, thread, fd, generation) was essential for ruling out ownership bugs
- Intermittent bugs require persistent markers across sessions, not one-shot investigation
- Two separate code paths produce FBX-related packets — must trace both
- Decision tree versioning prevents losing progress when hypotheses change
- **Equivalent workload reproduction is the strongest evidence**: Matching cold-start conditions, pipeline execution path, object/material/texture count, FBX import path, material application path, and transform playback — then observing no freeze with full instrumentation — is the strongest evidence that the issue is not reproducible on the current build
- **Investigation branch as archive**:保留完整 instrumentation branch (`debug/runtime-instrumentation-v2`) allows future investigators to cherry-pick diagnostic commits without rebuilding from scratch
