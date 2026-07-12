# INV-2026-001: FBX Reconnect Import Failure

## Metadata

- **Status**: Suspended pending reproduction
- **Owner**: Khanh
- **Started**: 2026-07-03
- **Closed**: —
- **Classification**: Transport, Threading

## Problem

After Disconnect/Reconnect cycle, FBX import packet never reaches UE despite Blender `sendall()` succeeding.

## Symptoms

- Original session: `PT_FBXImportRequest (0x16)` never appeared in UE log
- Blender log showed `sendall()` returned success
- UE log showed no `FBX_IMPORT_BEGIN` marker
- Subsequent test sessions could not reproduce the failure

## Reproduction Steps

1. Connect Blender to UE
2. Import an FBX mesh (e.g. Chair)
3. Disconnect
4. Reconnect
5. Import another FBX mesh
6. Check UE log for `FBX_IMPORT_BEGIN`

## Hypotheses

| ID | Hypothesis | Status |
|----|-----------|--------|
| H1 | Sender thread dies after reconnect | Disproved — thread ownership verified |
| H2 | Socket ownership race | Disproved — socket fd/ownership correct |
| H3 | Packet not processed after SEND_RETURN | Inconclusive — original failure occurred before instrumentation; current sessions show full pipeline passing; failure domain unknown |
| H4 | UE parser rejects packet | Disproved — parser unchanged, works on fresh connect |
| H5 | Importer skipped due to state | Disproved — importer state clean after reconnect |

## Evidence Collected

| ID | Description | Source | Classification |
|----|-------------|--------|----------------|
| E1 | `QUEUE_PUSH tx=117` observed | Blender log | Supports H3 |
| E2 | `SEND_RETURN ret=20000 errno=0` observed | Blender log | Disproves H1, H2 |
| E3 | `QUEUE_POP` absent in UE log | UE log | Supports H3 |
| E4 | `SOCKET_RECV` absent in UE log | UE log | Supports H3 |
| E5 | Reconnect creates new socket fd=34 | UE log | Confirms new connection |
| E6 | Old socket fd=28 closed before reconnect | UE log | Confirms cleanup |
| E7 | 3 subsequent test sessions all passed | Test logs | Bug is intermittent |

## Decision Tree

**v3** (current)

```
User clicks Sync FBX
      │
      ▼
FBX_ENQUEUE?  →  Yes (E1)
      │
      ▼
QUEUE_PUSH?  →  Yes (E1)
      │
      ▼
SEND_RETURN?  →  Yes ret>0 errno=0 (E2)
      │
      ▼
SOCKET_RECV?  →  Unknown (no data from original failure session)
      │
      ▼
PACKET_DISPATCH?  →  Unknown
      │
      ▼
FBX_IMPORT?  →  Unknown
```

**Decision tree is immutable within this version. Update only with new evidence.**

## Root Cause

**Status**: Unknown

No reproducible failure. The original failure occurred before instrumentation was added, so no logs exist from the failing session. All subsequent sessions with instrumentation show the full pipeline passing (SEND_RETURN → SOCKET_RECV → QUEUE_POP → PACKET_DISPATCH → FBX_IMPORT_BEGIN → FBX_IMPORT_END). Failure domain cannot be determined because the failing session predates instrumentation. Root cause remains unknown.

**Confidence**: N/A — insufficient evidence to form a root cause hypothesis.

Investigation suspended pending reproduction.

## Fix

Not applicable. Investigation suspended.

## Regression

| Scenario | Result |
|----------|--------|
| Fresh connect + FBX import | PASS |
| Disconnect + Reconnect + FBX import | PASS (3x) |
| Transform sync after reconnect | PASS |

## Instrumentation Added

| File | Markers | Cost Level |
|------|---------|------------|
| network.py | QUEUE_PUSH/POP, SEND_BEGIN/RETURN, SOCKET_BIND/UNBIND, tx_id, thread, fd | 1-2 |
| LiveSyncRunnable.cpp | SOCKET_RECV with nanosecond timestamps | 1 |
| UELiveSyncSubsystem.cpp | QUEUE_POP, PACKET_DISPATCH, QUEUE_PROBE | 1-2 |
| manifest_v3.py | FBX_ENQUEUE, FBX_ENQUEUE_SENT, FBX_ENQUEUE_SKIP | 1 |
| __init__.py | FBX_SEND_DECISION, FBX_SEND_RESULT, FBX_EXPORT_DONE, FBX_OP_DONE | 1-2 |
| LiveSyncFBXImporter.cpp | FBX_SET_MESH, FBX_SPAWN, FBX_ACTOR_CACHED, FBX_RENDER_DIRTY | 1 |

## Remaining Unknowns

- Original failure location is unknown because no instrumentation existed during the failing session
- Whether the bug is truly gone or just intermittent cannot be determined without a new failure event
- No root cause hypothesis can be formed with current evidence

## Decision Log

| ID | Decision | Based on | Accepted | Rejected | Reason |
|----|----------|----------|----------|----------|--------|
| D1 | Suspend investigation | E1-E7 verified; bug not reproducing | Suspended pending reproduction | Continue code analysis (speculative) | No failure event captured; code analysis without reproduction leads to speculative debugging |

## Lessons Learned

- Transport instrumentation (tx_id, thread, fd, generation) was essential for ruling out ownership bugs
- Intermittent bugs require persistent markers across sessions, not one-shot investigation
- Two separate code paths produce FBX-related packets — must trace both
- Decision tree versioning prevents losing progress when hypotheses change
