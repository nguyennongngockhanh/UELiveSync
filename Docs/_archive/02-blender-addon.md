# Blender Addon Architecture

## File Structure

```
Blender_Addon/
├── __init__.py     Addon registration, operators, UI panel
├── sync.py         Core sync logic (scene iteration, transform extraction, diff detection, GUID system)
├── network.py      TCP client, binary serialization, threaded send pipeline
└── requirements.txt
```

## Execution Flow

### Startup
```
User presses "Start UE Sync"
  → UELIVESYNC_OT_start.execute()
    → sync.start_sync()
      → last_sent_transforms.clear()
      → tracked_objects.clear()
      → _last_object_count = len(bpy.data.objects)
      → for each MESH object in bpy.data.objects:
          → guid = ensure_unique_guid(obj, tracked_objects)
          → tracked_objects[guid] = (obj, UUID(guid))
      → network.connect()
        → LiveSyncClient(host="127.0.0.1", port=5000)
          → socket.socket() → socket.connect()
          → starts background sender thread
      → timer_running = True
      → unregister existing timer if any
      → bpy.app.timers.register(lambda: check_updates())
```

### Per-Tick
```
check_updates() called by Blender timer (every ~16ms)
  → if not timer_running: return
  → if scene object count changed:
      → scan_scene()  [detect new/deleted objects]
  → else every 300 frames:
      → scan_scene()  [periodic edge-case safety net]
  → for each (guid, (obj, guid_obj)) in tracked_objects:
      → try: obj.name                  [O(1) stale check via ReferenceError]
      → if ReferenceError:             [object was deleted]
          → tracked_objects.pop(guid)
          → append serialize_delete_v3(guid_obj) to deletes_to_send
          → continue
      → transform = get_transform(obj)
        → matrix_world.copy()
        → Blender→UE coordinate conversion
        → decompose to loc/rot/scale
        → scale ×100 (cm conversion)
      → if transforms_different(transform, last_sent):
          → is_first_send? → append to create_objects (type 0x03)
          → else → append to objects_to_send (type 0x01)
          → cache new transform
  → if deletes_to_send:  send_objects(deletes_to_send, packet_type=0x04)
  → if create_objects:   send_objects(create_objects, packet_type=0x03)
  → if objects_to_send:  send_objects(objects_to_send)
  → if heartbeat due:    send_objects([], packet_type=0x07)
```

## GUID System

- UUID4 stored as Blender custom property (`obj["ue_guid"]`)
- 32 hex characters, persisted in .blend file via custom properties
- Created eagerly on sync start (`ensure_guid()` in `start_sync()` / `scan_scene()`)
- **Duplicate prevention**: `ensure_unique_guid()` checks collision against `tracked_objects`; regenerates on inherited-GUID conflict (e.g. `obj.copy()`)
- V3 protocol transmits GUID as 4 × uint32 (direct binary, no hex roundtrip)

## Coordinate Conversion

```
Conversion matrix:
    [ 1  0  0  0 ]
    [ 0 -1  0  0 ]
    [ 0  0  1  0 ]
    [ 0  0  0  1 ]

UE Matrix = Conversion @ Blender World @ Conversion
```

Result: location scaled ×100 (Blender meters → UE cm).

## Packet Types

| Type | Value | Purpose |
|------|-------|---------|
| TRANSFORM | 0x01 | Per-frame object transform update |
| HIERARCHY | 0x02 | Parent-child relationship |
| CREATE | 0x03 | New object creation |
| DELETE | 0x04 | Object removal |
| HEARTBEAT | 0x07 | Connection keepalive |

## Threading

- **Main thread**: scene iteration, transform extraction, serialization → non-blocking enqueue
- **Background sender thread**: dequeues serialized packets, calls `socket.sendall()`

## Current Limitations

1. **World-space only**: `matrix_world` bakes parent transforms, no hierarchy support (parent GUID is sent, but UE does not reconstruct hierarchy)
2. **MESH-only default filter**: cameras, lights, armatures excluded
3. **No initial snapshot**: no full-state burst on connect — actors are created incrementally as first transforms arrive
4. **No user-facing status indicator**: connection state tracked internally only; no UI icon/color

## Resolved Limitations (Phase 3.4–3.5)

| Limitation | Resolution |
|-----------|-----------|
| Full scene iteration per frame | ✅ Iterates `tracked_objects` dict; full scan only on count change or every 300 frames |
| Main thread blocking on send | ✅ `sendall()` runs on background sender thread via `queue.Queue` |
| No reconnection | ✅ `_reconnect_internal()` in background thread on socket error (0.5s delay) |
| No heartbeat | ✅ Heartbeat (type 0x07) every 5s via wall-clock interval |
| No dedup | ✅ UE side: `SeenThisTick` TSet per tick |
| GUID hex roundtrip | ✅ V3 sends 4×uint32, zero string allocation |
| Unbounded queue | ✅ `FLiveSyncQueue` bounded to 128 entries, drop-oldest on overflow |
| Scale linear lerp | ✅ Scale snaps directly to target in UE |
| TransformStates leak | ✅ `EvictStaleTransformStates()` 60s TTL |
