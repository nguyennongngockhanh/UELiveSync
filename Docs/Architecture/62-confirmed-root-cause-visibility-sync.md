# 62 — Confirmed Root Cause: Visibility Sync

## Status: FROZEN

Root cause below is **Root Cause Confirmed** — proven by direct runtime
evidence, not hypothesis.

---

## BUG-005: Visibility Changes Not Synchronized

**Symptom**: Hiding/unhiding objects in Blender during sync had no effect
in UE. Objects remained visible regardless of Blender visibility state.

---

### Root Cause 1 (Runtime Visibility)

Blender sent visibility changes using the legacy `PT_Visibility` (0x0B)
packet type via `send_objects(packet_type=PT_Visibility)`. However, the UE
bridge only dispatches `MsgType::OBJECT_VISIBILITY` (0x25) — it does not
register a handler for `PT_Visibility`. The packets were silently dropped
at transport validation.

**Evidence**: Blender log showed `PT_Visibility` being sent. UE log showed
no visibility handler invoked. UE bridge `kValidTypes` confirmed `PT_Visibility`
(0x0B) is not registered — only `MsgType::OBJECT_VISIBILITY` (0x25) is.

---

### Root Cause 2 (Initial State)

Objects hidden before Start Sync remained visible in UE. The first-tick
guard `if prev_vis is not None` prevented sending the initial visibility
state — `prev_vis` is `None` on first tick, so the condition was always
False for initial state.

**Evidence**: Blender log showed `prev_vis is None` on first tick, no
visibility packet sent. UE received no initial visibility message for
pre-hidden objects.

---

### Fix

**Blender Addon only.** No UE plugin changes.

1. Added `build_object_visibility()` to `object_protocol.py` — builds
   17-byte wire body (16-byte FGuid + 1-byte visible flag) matching
   C++ `BuildObjectVisibilityView`.

2. Replaced `send_objects(packet_type=PT_Visibility)` with
   `transport.send_msg(MsgType.OBJECT_VISIBILITY)` in `sync.py` —
   routes through the correct MsgType dispatch path.

3. Fixed first-tick guard: if `prev_vis is None` and `current_vis` is
   True (object is hidden), send initial visibility state. This ensures
   objects hidden before sync start are synchronized on first tick.

4. Removed dead imports: `serialize_visibility`, `PT_Visibility`.

**Scope**: `Blender_Addon/object_protocol.py`, `Blender_Addon/sync.py`.
No engine changes. No UE plugin changes.

---

### Regression Tests (A–D)

| Test | Scenario | Expected | Result |
|------|----------|----------|--------|
| A | Runtime Hide | UE hides object | PASS |
| B | Runtime Unhide | UE unhides object | PASS |
| C | Pre-sync hidden → Start Sync | UE spawns hidden | PASS |
| D | Unhide after TEST C | UE unhides object | PASS |

All 4 tests verified via fresh log evidence with boundary timestamps.

---

### Files Modified

| File | Change | Lines |
|------|--------|-------|
| `Blender_Addon/object_protocol.py` | Added `build_object_visibility()` | +16 |
| `Blender_Addon/sync.py` | Replaced PT_Visibility with MsgType, fixed first-tick guard | +21/-20 |

---

### Summary

| Bug | Root cause | Fix location | Engine change? |
|-----|-----------|-------------|----------------|
| BUG-005a | Legacy PT_Visibility not dispatched by UE bridge | `sync.py` send path | No |
| BUG-005b | First-tick guard prevented initial visibility sync | `sync.py` first-tick | No |

---

**Commit**: `887dd28` on `phase1.4-core-sync`
**Branch workflow**: `bugfix/visibility-sync` → `--ff-only` merge → delete
