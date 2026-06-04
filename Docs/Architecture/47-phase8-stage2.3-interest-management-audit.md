# Phase 8 Stage 2.3 — Interest Management via Dirty Flags (Audit)

## Problem

`check_updates()` iterates ALL `tracked_objects` every tick. For each object, it
performs: transform extraction + diff, mesh depsgraph eval + hash, material slot
read, visibility read, rename check, hierarchy check, and collection state read.
At 60 Hz, the cost is O(N) per tick where N = tracked object count.

## Current Per-Object Cost Per Tick

| Operation | Lines | CPU Cost | Always Runs? |
|-----------|-------|----------|-------------|
| `ReferenceError` guard | 1084–1088 | Negligible | Yes |
| `get_transform(obj)` | 1110 | Medium (matrix math) | Yes |
| `transforms_different()` | 1116–1119 | Low (dict compare) | Yes |
| `serialize_object_v3()` | 1132–1138 | Medium+ | Only on change |
| `get_mesh_identity_hash()` | 1200 | Low (FNV hash) | Yes |
| `obj.hide_get()` | 1234 | Low | Yes |
| `obj.name` | 1248 | Negligible | Yes |
| `get_parent_guid(obj)` | 1268 | Low | Yes |
| `get_object_material_slots()` | 1299 | Low | Yes |
| `extract_evaluated_mesh_data()` | 1332 | **High** (depsgraph eval) | Only non-first-send |
| `compute_geometry_version_hash()` | 1334 | Medium (SHA-256) | Only non-first-send |
| Collection state | (Phase 6F) | Low | Yes |

## Proposed Architecture: Dirty-Flag Interest Management

### Core Idea

Replace the full per-tick O(N) scan with a per-tick O(D) scan where D = number
of dirty objects. Objects become dirty when:

1. **Transform changed** — via depsgraph update handler
2. **Mesh data changed** — via depsgraph update handler
3. **Material slots changed** — via depsgraph update handler
4. **Visibility changed** — via depsgraph update handler
5. **Renamed** — via Blender `name` property callback (limited support)
6. **Parent changed** — via depsgraph update handler
7. **New object** — detected by `tracked_objects`
8. **Deleted object** — detected by ReferenceError or scan

Objects that are NOT dirty skip all per-tick work (transform extraction, mesh
eval, material read, visibility check, etc.).

### Implementation Structure

```
Blender depsgraph handler (depsgraph_update_post):
  for each updated node:
    if node.type == OBJECT:
      mark guid as DIRTY_TRANSFORM | DIRTY_MESH | DIRTY_MATERIAL ...
      store the update type in a set or bitfield
    elif node.type == ...:

check_updates():
  dirty_set = get_and_clear_dirty_flags()

  # Full scan (rare — periodic catch-up)
  if tick_counter % FULL_SCAN_INTERVAL == 0:
    dirty_set = set(tracked_objects.keys())

  # Only iterate dirty objects
  for guid in dirty_set:
    if guid not in tracked_objects:
      continue  # handled by delete detection below

    obj = tracked_objects[guid][0]
    check_transform(obj, guid, dirty_flags)
    check_visibility(obj, guid, dirty_flags)
    check_material(obj, guid, dirty_flags)
    check_mesh(obj, guid, dirty_flags)
    check_hierarchy(obj, guid, dirty_flags)
```

### Depsgraph Handler Registration

```python
@persistent
def on_depsgraph_update(scene, depsgraph):
    for update in depsgraph.updates:
        if not isinstance(update.id, bpy.types.Object):
            continue
        obj = update.id
        guid = obj.get("ue_guid", None)
        if guid is None:
            continue
        flags = 0
        if update.is_updated_transform:
            flags |= DIRTY_TRANSFORM
        if update.is_updated_geometry:
            flags |= DIRTY_MESH | DIRTY_MATERIAL
        if update.is_updated_shading:
            flags |= DIRTY_MATERIAL
        _mark_dirty(guid, flags)
```

### Edge Cases

| Edge Case | Handling |
|-----------|----------|
| **Initial state (reconnect/startup)** | Full scan on first tick; all objects are dirty |
| **Periodic catch-up** | Full scan every N ticks (configurable, default 300 = 5s at 60 Hz) |
| **Depsgraph miss** (e.g., script changes name property) | Fall back to full scan; periodic catch-up catches it |
| **Object deletion** | ReferenceError during dirty iteration; `_known_guids` diff still runs |
| **New object** | Added to `tracked_objects` by `scan_scene()`; marked dirty on next tick |
| **Backpressure + dirty** | If Blender is throttled (ACK says slow down), dirty flags accumulate. On next tick, process all accumulated dirt at the target rate. |
| **Rapid changes** | Multiple updates to the same object before next tick: OR the flags, process once. |

### Risks

| Risk | Severity | Mitigation |
|------|----------|-----------|
| Depsgraph miss (change not detected) | Medium | Periodic full-scan catch-up |
| Depsgraph handler timeout | Low | Handler is lightweight (set flag only) |
| Memory leak from dirty set | Low | Cleared every tick |
| Thread safety (depsgraph runs on main thread) | None | Same thread as check_updates |
| False negatives on mesh data | Low | Depsgraph `is_updated_geometry` covers mesh edits |

### Implementation Stages

| Stage | Scope | Files | Est. |
|-------|-------|-------|------|
| **2.3.1** | Add dirty-flag infrastructure: `_dirty_flags` dict, `_mark_dirty()`, `DIRTY_*` constants, depsgraph handler registration | `sync.py` | +40 lines |
| **2.3.2** | Modify `check_updates()` main loop: iterate dirty objects instead of full `tracked_objects`; add periodic full-scan fallback | `sync.py` | +30 lines |
| **2.3.3** | Move per-object checks into helper functions: `check_transform()`, `check_visibility()`, etc. | `sync.py` | ~20 lines refactor |
| **2.3.4** | Validation: existing tests must pass; add test for dirty-flag marking | tests | +50 lines |
| **2.3.5** | Smart batching: collect dirty objects for ~N ms before sending (optional, lower priority) | `sync.py` | +20 lines |

### Validation Plan

| Test | Method |
|------|--------|
| **No regression** | Existing Phase 7C standalone tests |
| **Dirty flag marks all objects on startup** | Log comparison: all objects should be dirty on first tick |
| **Dirty flag clears after processing** | No stale flags after check_updates completes |
| **Transform change detected** | Move object → verify DIRTY_TRANSFORM set |
| **Mesh change detected** | Edit mesh → verify DIRTY_MESH set |
| **Periodic full scan** | Verify all objects processed every N ticks |
| **Backpressure + dirty accumulation** | With ACK active, dirty flags accumulate correctly |

### Recommendation

**Implement 2.3.1–2.3.3 as a single stage.** The depsgraph handler and the
dirty-flag iteration are tightly coupled. Splitting them adds risk of applying
incomplete code. Total estimated effort: ~90 lines, ~0.5 day.
