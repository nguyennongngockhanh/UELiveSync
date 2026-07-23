# Bridge Architecture v1

**Status:** Certified (Phase 1.3.2g, commit `53a8053`)
**File:** `UE_Plugin/UELiveSync/Source/UELiveSync/Public/LiveSyncProtocolBridge.h`

---

## Pipeline

Every incoming MsgType packet flows through this 4-layer pipeline:

```
DeserializeFrame()
    ↓
ValidateExtraInvariants()      ← raw msg.body access allowed here
    ↓
ProcessXXX(msg)                ← orchestration, returns EDispatchResult
    ↓
BuildXXXView(msg)              ← pure function, raw msg.body access allowed here
    ↓
DispatchXXX(view)              ← const View&, fan-out only
        ├── LogXXX(view)       ← format & log
        └── GameplaySink(view) ← Phase 1.3.3+
```

**Entry point:** `DispatchMsgTypePacket(Data, DataSize)` in `LiveSyncBridge` namespace.

---

## Architecture Rules

### Rule 1 — Builder is pure

Builder functions (`BuildXXXView`) must:
- Only call `GetField()` / `TryGetField()` / `HasField()` to extract data
- Not call UE API, log, or mutate any state
- Not include Engine, World, Actor, UObject, or Gameplay headers

### Rule 2 — View is immutable

View structs must:
- Contain only primitive data (`uint8_t`, `uint16_t`, `uint32_t`, `uint64_t`, `float`, `std::string`, `std::vector<>`, `std::array<uint8_t, 16>`)
- Not contain UE pointers (`AActor*`, `UObject*`, `TWeakObjectPtr`)
- Not own UE resources

### Rule 3 — One struct per MsgType

Each message type gets exactly one View struct. No `std::variant` in views. No optional fields in the struct layout — use `bool HasX; T X;` pattern for optional fields.

### Rule 4 — Raw message access only in Validate and Build

`DeserializedMessage` field access (`msg.body.at()`, `msg.body.find()`, `GetField`, `TryGetField`) is allowed ONLY in:
- `ValidateExtraInvariants()` — validation layer
- `BuildXXXView()` — builder layer

All other layers (Process, Dispatch, Log) use View objects exclusively.

### Rule 5 — Serialization boundary

Gameplay code must not include `livesync_serializer.h`, `livesync_deserializer.h`, or `LiveSyncProtocolBridge.h`. Gameplay interacts only through View structs.

### Rule 6 — Builder is data conversion only

Builders only convert wire data to View structs. They do not:
- Look up Actors, Assets, Materials, Worlds, or Caches
- Call any UE API
- Perform any I/O

### Rule 7 — View does not own UE resources

View structs are protocol-level DTOs. They never hold UE handles, pointers, or references. The translation from View to UE objects happens in Gameplay (Phase 1.3.3+).

### Rule 8 — Dispatch is fan-out only

Dispatch functions receive `const View&` and fan out to consumers:
- `LogXXX(view)` — always called
- `GameplaySink(view)` — Phase 1.3.3+

Dispatch must never modify the View.

### Rule 9 — One entry point per MsgType

Each message type has exactly one `ProcessXXX()` function. The switch statement in `DispatchMsgTypePacket` contains only `return ProcessXXX(msg)` lines — no inline logic.

---

## Layer Responsibilities

| Layer | Reads | Writes | Includes |
|-------|-------|--------|----------|
| `ValidateExtraInvariants()` | `msg.body`, `msg.msg_type` | Returns `EDispatchResult` | Serializer headers |
| `ProcessXXX()` | `DeserializedMessage` (passed to builder) | Calls Build + Dispatch | Serializer headers |
| `BuildXXXView()` | `msg.body` via GetField/TryGetField | Returns View struct | Serializer headers |
| `DispatchXXX()` | `const View&` | Calls Log + Gameplay | View header only |
| `LogXXX()` | `const View&` | UE_LOG | View header only |
| Gameplay (Phase 1.3.3) | `const View&` | UE objects | View header only |

---

## Adding a New MsgType

### 1. Define in YAML

Add the message to `Shared/Protocol/MessageTypes.yaml` with fields, opcode, and post/pre-session requirement.

### 2. Regenerate serializer

Run the serializer generator to update `livesync_messages.h` (C++) and `serializer.py` (Python).

### 3. Add to MessageTraits

In `LiveSyncProtocolBridge.h`, add the new `MsgType` to `GetMessageTraits()`:
- Pre-session: `{false, false}` (HELLO, HELLO_ACK, REJECT only)
- Post-session: `{true, true}` (everything else)

### 4. Create View struct

```cpp
struct FooView
{
    std::array<uint8_t, 16> Id;
    std::string Name;
    // ... only primitive types
};
```

### 5. Create builder

```cpp
inline FooView BuildFooView(
    const livesync::DeserializedMessage& msg)
{
    FooView v;
    v.Id = GetField<std::array<uint8_t, 16>>(msg, "id");
    v.Name = GetField<std::string>(msg, "name");
    return v;
}
```

### 6. Create Log function

```cpp
inline void LogFoo(const FooView& v)
{
    char id_str[37];
    FormatUuid(v.Id, id_str, sizeof(id_str));
    UE_LOG(LogLiveSync, Log,
        TEXT("[BRIDGE][FOO] id=%hs name=%hs"),
        id_str, v.Name.c_str());
}
```

### 7. Create Dispatch function

```cpp
inline void DispatchFoo(const FooView& v)
{
    LogFoo(v);
    // Phase 1.3.3: GameplaySinkFoo(v);
}
```

### 8. Create Process function

```cpp
inline EDispatchResult ProcessFoo(
    const livesync::DeserializedMessage& msg)
{
#ifdef UELIVESYNC_BRIDGE_TESTING
    g_foo_calls++;
#endif
    auto view = BuildFooView(msg);
    DispatchFoo(view);
    return EDispatchResult::Handled;
}
```

### 9. Add to switch

```cpp
case livesync::MsgType::FOO:
    return ProcessFoo(msg);
```

### 10. Add test counter (standalone testing)

```cpp
#ifdef UELIVESYNC_BRIDGE_TESTING
inline int g_foo_calls = 0;
#endif
```

And add to `ResetAllCounters()`.

### 11. Write test

In `test_bridge_dispatch.cpp`, add a test that:
1. Serializes a FOO message
2. Calls `DispatchMsgTypePacket()`
3. Asserts `g_foo_calls == 1`
4. Asserts result is `EDispatchResult::Handled`

---

## Message Family Summary

| Family | Messages | Process functions |
|--------|----------|-------------------|
| Handshake | HELLO, HELLO_ACK, HEARTBEAT, HEARTBEAT_ACK | ProcessHello, ProcessHelloAck, ProcessHeartbeat, ProcessHeartbeatAck |
| Object | OBJECT_CREATE, OBJECT_UPDATE, OBJECT_DELETE, OBJECT_RENAME, OBJECT_REPARENT, OBJECT_VISIBILITY | ProcessObjectCreate/Update/Delete/Rename/Visibility/Reparent |
| Material | MATERIAL_CREATE, MATERIAL_UPDATE, MATERIAL_ASSIGN | ProcessMaterialCreate/Update/Assign |
| Mesh | MESH_START, MESH_CHUNK, MESH_END, MESH_DATA, MESH_DELTA | ProcessMeshStart/Chunk/End/Data/Delta |
| Camera | CAMERA_CREATE, CAMERA_UPDATE, CAMERASETACTIVE | ProcessCameraCreate/Update/SetActive |
| Scene | SCENE_HASH, SCENE_FULL, SCENE_DELTA | Deferred (Phase 1.3.4) |
| System | SYNC_ACK, ERROR, DISCONNECT | Deferred (Phase 1.3.4) |

**Total migrated:** 17/28 message types
**Deferred:** 11 message types (SCENE_*, SYNC_ACK, ERROR, DISCONNECT, REJECT)
