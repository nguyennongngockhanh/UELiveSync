# Issues by Phase

## Must Fix Before Production (Phase 1)

| ID | Issue | Effort | Impact | Dependencies |
|----|-------|--------|--------|-------------|
| C1 | Main thread blocking on socket.sendall() | 1 day | UI freeze | None |
| C2 | GUID hex-string roundtrip | 0.5 day | CPU waste | C1, protocol change |
| C3 | UE_LOG per object per packet | 0.25 day | Frame drops | None |
| C4 | Unbounded queue growth | 0.25 day | Memory leak | None |
| C5 | ActorCache full rebuild | 0.5 day | Sync interruption | None |
| H3 | No TCP_NODELAY | 0.1 day | Latency +40ms | None |
| H6 | Timer double-registration | 0.1 day | Leaked timer | None |
| H7 | reconnect() blocks 500ms | 0.1 day | UI freeze | C1 (fixed by same change) |

## Should Fix (Phase 2)

| ID | Issue | Effort | Impact |
|----|-------|--------|--------|
| C6 | ActorCache rebuild race | 0.5 day | Lost transforms |
| H1 | Full scene iteration every 16ms | 1 day | CPU waste |
| H2 | World-space only (no hierarchy) | 2 days | Hierarchy broken |
| H4 | Interpolation lag | 1 day | Visual lag |
| H5 | No dedup in process queue | 0.5 day | CPU waste |

## Nice to Have (Phase 3+)

| ID | Issue | Effort |
|----|-------|--------|
| M1 | No heartbeat | 1 day |
| M2 | Single connection only | 1 day |
| M3 | No packet type field | 0.5 day |
| M4 | Scale interpolation | 0.25 day |
| M5 | Hardcoded thresholds | 0.5 day |
| M6 | Silent send failure | 0.5 day |
| M7 | TransformStates unbounded | 0.5 day |
| L1-L5 | Various low-priority items | 0.25 day each |

## Timeline View

### Week 1: Phase 1 (Critical Fixes)
```
C3 (logging) ── 0.25d
H3 (TCP_NODELAY) ── 0.1d
H6 (timer guard) ── 0.1d
C4 (bounded queue) ── 0.25d
C1 + H7 (background thread + reconnect) ── 1d
C2 (direct GUID) ── 0.5d
C5 + C6 (incremental cache) ── 0.5d
─── Total: ~3 days ───
```

### Week 2: Phase 2 (Performance)
```
H5 (dedup) ── 0.5d
H4 (interpolation decision) ── 1d
H1 (optimized scene iteration) ── 1d
H2 (local transform) ── 2d
─── Total: ~4.5 days ───
```

### Week 3+: Phase 3
```
M3 (packet type) ── 0.5d
M1 (heartbeat) ── 1d
M2 (multi-connection) ── 1d
M4-M7, L1-L5 ── 2d
─── Total: ~4.5 days ───
```
