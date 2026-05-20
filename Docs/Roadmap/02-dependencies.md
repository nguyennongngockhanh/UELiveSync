# Phase Dependencies

## Dependency Graph

```
Phase 0 (Diagnostics)
   ↓
Phase 1: Quick Wins
   ├── 1a  Remove per-object UE_LOG      ← no dependencies
   ├── 1b  TCP_NODELAY (Blender)         ← no dependencies
   ├── 1c  TCP_NODELAY (UE)              ← no dependencies
   ├── 1d  Background thread (Blender)   ← no dependencies
   ├── 1e  Bounded queue (UE)            ← no dependencies
   └── 1f  Protocol version bump         ← after 1a (reduces noise)
        ↓
Phase 2: Core Fixes
   ├── 2a  Direct binary GUID            ← after 1f (protocol change)
   ├── 2b  Dedup in batch                ← after 1a (reduce noise)
   ├── 2c  Incremental actor cache       ← no dependencies
   ├── 2d  Simplify interpolation        ← no dependencies
   └── 2e  Optimized scene iteration     ← no dependencies
        ↓
Phase 3: Hierarchy
   ├── 3a  Local+World transform         ← after 2a (protocol change)
   ├── 3b  Parent-child reconstruction   ← after 3a
   ├── 3c  Create/Delete packets         ← after 3b
   └── 3d  Heartbeat                     ← no dependencies
        ↓
Phase 4: Hardening (can start parallel to Phase 3)
```

## Recommended Ordering

### Week 1: Phase 0 + Phase 1
```
Day 1  → Phase 0 (benchmark current state)
Day 2  → 1a, 1b, 1c (zero-risk perf wins)
Day 3  → 1d (background thread — biggest win)
Day 4-5 → 1e (bounded queue), 1f (version bump)
```

### Week 2: Phase 2
```
Day 1  → 2a (binary GUID — measure improvement)
Day 2  → 2b (dedup) + 2c (incremental cache)
Day 3-4 → 2d (interpolation decision)
Day 5  → 2e (scene iteration) + end-to-end testing
```

### Week 3: Phase 3 + Phase 4
```
Day 1  → 3a Local transform in packet
Day 2  → 3b Parent-child reconstruction
Day 3  → 3c Create/Delete
Day 4  → 3d Heartbeat + 4a Config
Day 5  → 4b-4d Polish, hardening, testing
```

## Backward Compatibility Strategy

- Version 2 packets continue to work after Phase 1
- Version 3 packets introduced in Phase 2 (2a)
- UE checks `Header.Version` to dispatch parser
- During Phase 2 migration, Blender can send V3, UE must handle both
- After full migration, V2 support can be dropped
