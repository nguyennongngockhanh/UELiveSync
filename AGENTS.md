# AGENTS.md

## System Overview

Two-component real-time sync system: **Blender addon** (Python, `Blender_Addon/`) → binary TCP → **UE5 plugin** (C++, `UE_Plugin/UELiveSync/`).

Key source files:
- `Blender_Addon/__init__.py` — registration, UI panel, operators
- `Blender_Addon/sync.py` — core sync loop, scene iteration, diff detection
- `Blender_Addon/network.py` — TCP client, binary serialization, threaded sender
- `UE_Plugin/.../UELiveSyncSubsystem.cpp/h` — main game-thread orchestrator
- `UE_Plugin/.../LiveSyncRunnable.cpp/h` — dedicated network receive thread
- `UE_Plugin/.../LiveSyncQueue.h` — bounded MPSC packet buffer
- `UE_Plugin/.../SyncTypes.h` — shared structs, protocol constants, log category

## Developer Commands

- **No package manager** — addon uses Blender's built-in `bpy`; UE plugin via standard UE5 module system (depends on Sockets, Networking)
- **Run all tests**: `python3 tests/run_phase3.6_all.py` — auto-detects runtimes
  - Tests A (reconnect), C (snapshot), D (long-session) require UE editor listening on `:57000`
  - Test B (hierarchy) requires Blender executable (flatpak or binary)
- **Individual tests**: `python3 tests/phase3.6_validation_*.py`
- **UE CVars**: `UE.LiveSync.Port` (57000), `UE.LiveSync.Verbose`, `UE.LiveSync.Threshold.*`, `UE.LiveSync.InterpMode`, `UE.LiveSync.DumpState` (console command)
- **Blender default port**: 57000 (fallback in `sync.py:698`)

## Architecture Gotchas

- **Coordinate conversion**: Y-axis flip matrix, scale ×100 (Blender meters → UE cm) — `sync.py:210-227`
- **GUID system**: `obj["ue_guid"]` custom property, `uuid.uuid4().hex`, collision detection via `ensure_unique_guid()` — critical when `obj.copy()` inherits GUID
- **Object filter**: Only `MESH` type objects are synced (no cameras/lights/armatures)
- **Scene scanning**: O(1) count-based detection; fallback every 300 frames; stale check via `ReferenceError` (O(1))
- **Heartbeat**: Every 5s Blender → 15s timeout UE
- **Protocol**: V3 binary TCP, magic `0x4C56534D`, 24-byte header, little-endian packing. Packet types: TRANSFORM(0x01), CREATE(0x03), DELETE(0x04), HEARTBEAT(0x07). V2 legacy coexists.
- **Queue**: Bounded 128-entry MPSC queue on UE side, drop-oldest on overflow
- **Common failure mode**: blocking Blender UI thread during reconnect or heartbeat recovery
- **Docs**: `Docs/Architecture/` has 8 files — start with `01-system-overview.md`

## Threading Rules

- **bpy API must only be accessed from Blender main thread**
- Blender main thread: scene iteration, diff detection, serialization → enqueue (non-blocking)
- Blender background daemon thread: `socket.sendall()` only
- **UE network thread must enqueue packets; all UObject/world mutation on game thread. Network thread must not store or retain raw UObject pointers across frames.**
- UE network thread: `Wait(10ms)` + `Recv()` → enqueue `FLiveSyncPacket`
- UE game thread (Tick): `ProcessQueuedPackets()` → `InterpolateTransforms()` → `SetActorTransform()`

## Critical Invariants (Do Not Break)

- Maintain V2 backward compatibility when modifying protocol parsing
- Never block Blender main thread with socket I/O
- bpy API must only be accessed from Blender main thread
- All UObject/world mutations must occur on UE game thread
- Preserve GUIDs across reconnects and incremental sync
- Blender is authoritative for transforms; UE interpolation is client-side only
- UE interpolation must never feed back into Blender state
- Protocol uses little-endian binary packing
- Header layout is fixed-size 24 bytes; struct packing changes require protocol version bump
- TCP transport assumes ordered/reliable delivery; no packet reassembly layer
- Network thread must not store or retain raw UObject pointers across frames
- Maintain port parity between Blender and UE defaults (57000)
- Keep bounded queue behavior (drop-oldest on overflow)
- Do not remove heartbeat timeout handling without replacement

## Preferred Workflow

1. Read `Docs/Architecture/01-system-overview.md` first
2. Inspect existing phase docs before changing architecture
3. Prefer additive changes over protocol rewrites
4. Keep verbose logging behind `UE.LiveSync.Verbose`
5. Preserve backward compatibility unless explicitly upgrading protocol version
