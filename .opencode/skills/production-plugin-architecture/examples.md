# Code Examples

## Example 1: Changelog (Real-World)

```markdown
# Changelog

## [Unreleased]

### Added
- `UE.LiveSync.Ping` console command — prints connected/queue/states counters
- Network thread watchdog: 30s inactivity triggers automatic restart

### Changed
- StopNetworkThread timing logged per-phase (stop/close/join/cleanup ms)
- Bounded queue warning threshold made configurable via `UE.LiveSync.QueueWarnThreshold`

### Fixed
- Socket double-close race on rapid reconnect (#41)
- Payload size validation for edge-case zero-object packets

## [1.2.0] — 2026-05-22

### Added
- Phase 3.6 validation suite: reconnect torture, hierarchy stress, snapshot correctness, long-session runtime
- Full-state snapshot burst on reconnect (`PF_FullSnapshot` flag)

### Changed
- Blender fallback port 5000 → 57000 (sync with UE default)
- Dedicated `LogLiveSync` category replaces `LogTemp` throughout
- Scale interpolation snaps directly to target (no linear lerp)

### Fixed
- GUID invariant doc (sync.py:106) matched actual implementation
- CVar defaults in protocol doc corrected to match runtime values
- Header alignment: replaced UB reinterpret_cast with FMemory::Memcpy

## [1.1.0] — 2026-05-15

### Added
- V3 protocol with type/flags fields, direct uint32 GUID, timestamp, parent GUID
- Heartbeat (type 0x07) with 15s timeout on UE side
- GUID collision detection for inherited GUIDs from `obj.copy()`
- O(1) stale object validation via ReferenceError

### Changed
- Scene scan from 100-frame full iteration → count-based O(1) detection
- UUID parsing cached as `UUID` objects in tracked_objects
- Heartbeat timing from frame-count to wall-clock (every 5s)

### Removed
- Legacy V1 protocol support
- Per-frame full bpy.data.objects scan

## [1.0.0] — 2026-04-01

### Added
- Initial release: V2 binary TCP protocol
- Blender ↔ UE5 real-time transform sync
- Basic reconnection support
- Operator UI panel in Blender 3D View sidebar
```

## Example 2: Phase Roadmap Document

```markdown
# Phase 4 — Production Hardening & Editor Tooling

**Status**: Phase 4A completed · Phase 4B–D pending
**Estimate**: 2–3 days · **Risk**: Low

## Goal

Close all remaining gaps between the current system and production
readiness. No new features — only polish, diagnostics, abuse
tolerance, and editor UX.

---

## Phase 4A — Stability Core ✅

### ✅ E1 — Fix CVar defaults in protocol doc

| File(s) | What |
|---------|------|
| `Docs/Architecture/05-network-protocol.md` | Fixed StateTTL=60.0, InterpSnap=0.1, Threshold.* defaults. Added port fallback note. |

### ✅ A1 — Fix port fallback

| File(s) | What |
|---------|------|
| `sync.py:698` | Port fallback changed from 5000→57000. |

### ✅ C1 — Dedicated log category

| File(s) | What |
|---------|------|
| `SyncTypes.h`, `UELiveSyncSubsystem.cpp`, `LiveSyncRunnable.cpp` | Replaced all LogTemp → LogLiveSync. |

---

## Phase 4B — Diagnostics 🚧

| Item | Status | Description |
|------|--------|-------------|
| D1 | 🚧 | Per-tick rate cap CVar |
| D2 | ⏳ | Metrics dashboard |
```

## Example 3: API Deprecation Window (Python)

```python
# __init__.py — v1.2.0 adds new API, deprecates old

import warnings

# Public API (stable)
def sync_start():
    """Start the sync engine. Replaces start_sync() (deprecated)."""
    ...

# Deprecated alias — will be removed in v2.0.0
def start_sync():
    """Deprecated: use sync_start() instead."""
    warnings.warn(
        "start_sync() is deprecated, use sync_start()",
        DeprecationWarning,
        stacklevel=2,
    )
    return sync_start()
```

## Example 4: Version Constant Strategy

```python
# Python / Blender addon — single source of truth
ADDON_VERSION = (1, 2, 0)

bl_info = {
    "name": "UE Live Sync",
    "version": ADDON_VERSION,
    "blender": (5, 0, 0),
    ...
}
```

```cpp
// UE5 C++ — single source of truth
// In SyncTypes.h or module header:
#define UE_LIVE_SYNC_VERSION_MAJOR 1
#define UE_LIVE_SYNC_VERSION_MINOR 2
#define UE_LIVE_SYNC_VERSION_PATCH 0
```

```json
// UE5 .uplugin — canonical version for UE module system
{
    "FileVersion": 3,
    "Version": 1,
    "VersionName": "1.2.0",
    ...
}
```

## Example 5: Release Tag Command Sequence

```bash
# Before tagging: verify everything
python3 tests/run_phase3.6_all.py
git status                    # clean?
git diff --cached             # only intended files?

# Tag and push
VERSION="1.2.0"
git tag -a "v$VERSION" -m "Release v$VERSION"
git push origin "v$VERSION"

# Create GitHub release (via gh CLI)
gh release create "v$VERSION" \
    --title "v$VERSION" \
    --notes "$(sed -n '/^## \[$VERSION\]/,/^## \[/p' CHANGELOG.md | head -n -2)"
```
