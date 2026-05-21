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
      → network.connect()
        → LiveSyncClient(host="127.0.0.1", port=5000)
          → socket.socket() → socket.connect()
          → starts background sender thread
      → timer_running = True
      → bpy.app.timers.register(check_updates)
```

### Per-Tick
```
check_updates() called by Blender timer
  → if not timer_running: return
  → full_scan: for each obj in bpy.data.objects:
      → skip if obj.type not in allowed types
      → guid = ensure_guid(obj)        [custom property "ue_guid"]
      → transform = get_transform(obj)
        → matrix_world.copy()
        → Blender→UE coordinate conversion
        → decompose to loc/rot/scale
        → scale ×100 (cm conversion)
      → if transforms_different(transform, last_sent):
        → serialize_object_v3(guid, transform)
          → struct.pack binary payload (V3 format)
        → append to objects_to_send
        → cache new transform
  → if objects_to_send:
      → network.send_objects(objects_to_send, packet_type)
        → _client.send_packet(objects, packet_type)
          → build V3 header + payload
          → enqueue to background thread
          → (immediate return, no blocking)
```

## GUID System

- UUID4 stored as Blender custom property (`obj["ue_guid"]`)
- 32 hex characters, persisted in .blend file via custom properties
- Created lazily on first sync (`ensure_guid()`)
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

1. **Full scene iteration**: `bpy.data.objects` iterated every frame
2. **World-space only**: `matrix_world` bakes parent transforms, no hierarchy support
3. **MESH-only default filter**: cameras, lights, armatures excluded
4. **No initial snapshot**: no full-state sync on connect
5. **No reconnection**: single connect attempt, no auto-retry
