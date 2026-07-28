# 66 — Confirmed Root Cause: Rename Synchronization

## Status: FROZEN

Root cause below is **Root Cause Confirmed** — proven by direct runtime
evidence, not hypothesis.

---

## BUG-007: Rename Not Synchronized

**Symptom**: Renaming an object in Blender did not update the actor label
in UE Outliner. Actor remained named "Actor0" after rename.

---

### Root Cause

Blender sent rename using the legacy `PT_Rename` (0x0C) packet type via
`send_objects()`. However, the UE bridge only dispatches
`MsgType::OBJECT_RENAME` (0x23) — it does not register a handler for
`PT_Rename`. The packets were silently dropped at transport validation.

This is the same pattern as BUG-005 (Visibility): legacy PT_* packet
type vs MsgType mismatch.

---

### Evidence

**Code analysis**:
- `sync.py:2714` — Blender sends `PT_Rename` (0x0C) via `send_objects()`
- `LiveSyncProtocolBridge.h:174` — UE dispatches `MsgType::OBJECT_RENAME` (0x23)
- No `build_object_rename()` existed in `object_protocol.py`

**Runtime verification**:
- Before fix: Zero `RENAME` markers in UE log after rename action
- After fix: `[BRIDGE][OBJECT_RENAME]` received, `[RENAME][DIAG] Actor label changed` confirmed

---

### Fix

1. Added `build_object_rename()` to `object_protocol.py` — builds wire
   body (16-byte FGuid + utf8 name) matching C++ `BuildObjectRenameView`.
2. Replaced `send_objects(packet_type=PT_Rename)` with
   `transport.send_msg(MsgType.OBJECT_RENAME)` in `sync.py`.
3. Removed dead imports (`serialize_rename`, `PT_Rename`).

**Scope**: `Blender_Addon/object_protocol.py`, `Blender_Addon/sync.py`.
No UE plugin changes.

---

### Regression Tests

| Test | Scenario | Expected | Result |
|------|----------|----------|--------|
| A | Rename object in Blender | UE Outliner updates immediately | PASS |

---

### Files Modified

| File | Change |
|------|--------|
| `Blender_Addon/object_protocol.py` | Added `build_object_rename()` |
| `Blender_Addon/sync.py` | Replaced PT_Rename with MsgType OBJECT_RENAME |

---

### Related

This is the second instance of the Legacy Protocol vs MsgType pattern.
See `62-confirmed-root-cause-visibility-sync.md` for BUG-005 (Visibility).

**Pattern**: Blender sends legacy `PT_*` packet but UE bridge only
dispatches `MsgType::*`. Packets silently dropped.

---

**Commit**: TBD on `bugfix/BUG-007-rename-sync`
**Branch workflow**: `bugfix/BUG-007-rename-sync` → `--ff-only` merge → delete
