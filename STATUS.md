# UE Live Sync — Status

**Version** 0.1.0 — Two-component real-time sync system (Blender ↔ UE5).

## Components

| Side | Language | Role |
|------|----------|------|
| `Blender_Addon/` | Python (bpy) | Scene iteration, diff detection, binary serialization |
| `UE_Plugin/UELiveSync/` | C++ (UE5.7) | Game-thread transform interpolation, network receiver |

## Architecture

- **Protocol**: V3 binary TCP, magic `0x4C56534D`, 24-byte header, little-endian
- **Transport**: Ordered reliable TCP, no reassembly layer
- **Object identity**: GUID-based (`obj["ue_guid"]`), collision-safe via `ensure_unique_guid()`
- **Threading**: Blender main thread serializes; background daemon sends. UE network thread enqueues; game thread interpolates.
- **Queue**: Bounded 128-entry MPSC (drop-oldest on overflow)
- **Heartbeat**: 5s Blender → 15s UE timeout

## Files

| Blender | Lines | UE Plugin | Lines |
|---------|-------|-----------|-------|
| `__init__.py` | 447 | `UELiveSyncSubsystem.cpp/h` | 3128 |
| `sync.py` | core sync | `LiveSyncRunnable.cpp/h` | network thread |
| `network.py` | TCP/serialization | `LiveSyncQueue.h` | MPSC buffer |
| | | `SyncTypes.h` | structs/protocol |
| | | `UELiveSyncEditor/` | status widget |

## Phase Progress

- **Phase 3.4–3.5**: Performance, stabilization, protocol cleanup (completed)
- **Phase 3.6**: Robustness & validation tests (completed)
- **Phase 4** (in progress):
  - **4A Stability Core** — watchdog, reconnect, heartbeat hardening (completed)
  - **4B Refinements** — adaptive interpolation, diagnostics (completed)
  - **4C Editor Tooling** — status widget, stats (completed)
  - **4D Validation** — stress tests, edge cases (completed)

## Recent Changes

- `SyncTypes.h`: Fixed UHT error — moved `#include <atomic>` before `.generated.h`
- `UELiveSyncSubsystem.cpp`: Removed orphaned braces/log outside function body
- `UELiveSyncEditorModule.cpp`: Removed `UStatusBarSubsystem::AddStatusBarWidget` calls (API doesn't exist in UE5.7)
- Cleaned up local-only files from git tracking (`.opencode/skills/`, `AGENTS.md`, `tests/`)
