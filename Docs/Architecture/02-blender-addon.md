# Blender Addon Architecture

## File Structure

```
Blender_Addon/
├── __init__.py     Addon registration, operators, UI panel
├── sync.py         Core sync logic (scene iteration, transform extraction, diff detection, GUID system)
├── network.py      TCP client, binary serialization, send pipeline
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
      → timer_running = True
      → bpy.app.timers.register(check_updates)   [16ms interval]
```

### Per-Tick (every ~16ms)
```
check_updates() called by Blender timer
  → if not timer_running: return 0.016 (reschedule)
  → for each obj in bpy.data.objects:
      → skip if obj.type != 'MESH'
      → guid = ensure_guid(obj)        [custom property "ue_guid"]
      → transform = get_transform(obj)
        → matrix_world.copy()
        → Blender→UE coordinate conversion matrix
        → decompose to loc/rot/scale
        → scale x100 (cm conversion)
      → if transforms_different(transform, last_sent):
        → serialize_object(guid, transform)
          → struct.pack binary payload
        → append to objects_to_send
        → cache new transform
  → if objects_to_send:
      → network.send_objects(objects_to_send)
        → _client.send_packet(objects)
          → build header + payload
          → socket.sendall()
```

## GUID System

- UUID4 hex string stored as Blender custom property (`obj["ue_guid"]`)
- 32 hex characters, persisted in .blend file via custom properties
- Created lazily on first sync (`ensure_guid()`)

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

## Current Limitations

1. **Main thread networking**: socket.sendall() blocks Blender UI
2. **Full scene iteration**: bpy.data.objects iterated every frame
3. **World-space only**: matrix_world bakes parent transforms, no hierarchy support
4. **MESH-only filter**: cameras, lights, armatures excluded
5. **No initial snapshot**: only sends changed transforms, no full sync on connect
