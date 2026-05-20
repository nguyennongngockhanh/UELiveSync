# Performance Bottleneck Analysis

## Measurement Baselines

Before optimizing, measure these metrics:

### Blender Side

| Metric | How to Measure | Expected Value |
|--------|---------------|----------------|
| `check_updates()` duration | `time.perf_counter()` around loop | <5ms for 100 objects |
| `get_transform()` per object | `time.perf_counter()` per call | ~0.1ms per object |
| `send_objects()` duration | timer around send | <1ms on localhost |
| Objects iterated per frame | `len(bpy.data.objects)` | Depends on scene |

### UE Side

| Metric | How to Measure | Expected Value |
|--------|---------------|----------------|
| `ProcessBinaryPacket()` per object | `FScopeTimer` | <5μs per object |
| `InterpolateTransforms()` per frame | `FScopeTimer` | <1ms for 100 actors |
| Queue depth | `Queue.Size()` | <5 entries steady state |
| Log write time | disable UE_LOG compare | Significant at Warning level |

## Known Hotspots

### Hotspot 1: GUID String Parsing (UE)

**Location**: `UELiveSyncSubsystem.cpp:489-510`

**Operations per object**:
1. Loop 16 bytes → `FString::Printf(TEXT("%02x"))` → 16 allocations
2. `FString::Printf` each char (16 heap allocations)
3. `FGuid::ParseExact` parses string back to 4 uint32

**Cost**: ~3-5μs per object at 100 objects = 300-500μs per frame

**Fix**: Direct binary read into FGuid fields.

### Hotspot 2: Per-Object Logging (UE)

**Location**: `UELiveSyncSubsystem.cpp:512-518, 873-878`

**Cost**: `UE_LOG` at Warning severity in Development build writes to disk. Each call takes ~10-50μs due to formatting + mutex + write.

**Impact**: 100 objects × 2 logs × 50μs = 10ms per frame. This alone can cause frame drops.

**Fix**: Use Verbose severity or rate-limited summary.

### Hotspot 3: main thread network I/O (Blender)

**Location**: `network.py:218`

**Cost**: `socket.sendall()` blocks until data is acknowledged by receiver's TCP stack. On localhost this is fast (<1ms), but any network issue → blocks indefinitely.

**Impact**: Unbounded latency on main thread.

**Fix**: Background thread.

### Hotspot 4: Full scene iteration (Blender)

**Location**: `sync.py:177`

**Cost**: `bpy.data.objects` returns all objects. For each, `matrix_world.copy()` forces Blender to evaluate the depsgraph and compute the world matrix.

**Impact**: O(N) per frame with constant factor of matrix computation and Python overhead.

**Fix**: Track objects with change listeners.
