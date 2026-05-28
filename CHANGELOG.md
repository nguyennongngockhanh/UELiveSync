# Changelog

## 2026-05-28 — Decouple Semantic Domains from Transform Gate

### Problem

All semantic event detections (rename, visibility, hierarchy) were inside `if transforms_different(...)` (`sync.py:1068-1180`). This meant these events only emitted when the object's transform also changed.

| Domain | Status Before | Status After |
|--------|--------------|--------------|
| Rename | Only detected on object move | Always detected |
| Visibility | Only detected on object move | Always detected |
| Hierarchy | Only detected on object move | Always detected |
| Collection | Already outside gate | Unchanged |

### Changes

**`Blender_Addon/sync.py`** — moved rename, visibility, hierarchy detection from inside `if transforms_different()` to independent indent-8 scope. Each domain now evaluates every tick:
- Visibility: `obj.hide_get()` diff against `_last_visibility_state`
- Rename: `obj.name` diff against `_last_object_names`
- Hierarchy: `get_parent_guid(obj)` diff against `_last_parent_guid`

Added `[DIAG]` logging for all domains.

**`UE_Plugin/.../UELiveSyncSubsystem.cpp`** — added diagnostic logging:
- `[VISIBILITY][DIAG]` post-apply (actor name + hidden state)
- `[COLLECTION][DIAG]` packet-received + post-apply (registry member count)

**`Docs/KNOWN_BAD_PATTERNS.md`** — added entry #11: "Transform-Gated Semantic Event Detection" documenting the anti-pattern.

### Invariants Preserved

- GI-1 (GUID stable across rename) — unchanged hash derivation
- TF-4/TF-5 (transform authority) — transform path unchanged
- RN-1 (GRenamePersistentLabel authority) — UE side untouched
- HI-1 (parent stable) — hierarchy detection still uses `get_parent_guid()`
- CL-1 (collection idempotent) — collection detection unchanged
- No replay divergence — Blender-side detection only
- No packet format changes
- No networking changes
