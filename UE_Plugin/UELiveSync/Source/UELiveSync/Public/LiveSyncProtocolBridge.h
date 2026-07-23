#pragma once

// =========================================================
// LiveSyncProtocolBridge.h — MsgType Protocol Dispatcher
// =========================================================
// Phase 1.3.2a: Bridge between legacy PT_* protocol and new
// MsgType protocol.
//
// Architecture:
//   ProcessBinaryPacket()
//     -> DispatchMsgTypePacket() -> EDispatchResult
//          |-- DeserializeFrame()
//          |-- Validate invariants (via MessageTraits)
//          +-- switch(msg_type) -> HandleXXX(msg)
//
// DETECTION METHOD:
//   Legacy PT_* packets start with Magic 0x4C56534D (4 bytes).
//   New MsgType packets start with a uint32 LE length prefix
//   (small value like 14-1000), which is never the Magic value.
//
// Design rules:
//   - MessageTraits only contains UE dispatcher policy.
//     No opcode, field list, or payload layout.
//   - Handlers receive const DeserializedMessage&.
//     They never deserialize or re-validate.
//   - DispatchMsgTypePacket only routes. No UE actions.
// =========================================================

// =========================================================
// Mode: UE production vs standalone test
// =========================================================

#ifdef UELIVESYNC_BRIDGE_TESTING
  #include <cstdint>
  #include <cstring>
  #include <cstdio>
  #include <cstdlib>
  #include <stdexcept>

  typedef uint8_t uint8;
  typedef uint32_t uint32;
  typedef int32_t int32;

  namespace FMemory {
      inline void* Memcpy(void* Dst, const void* Src, size_t Count) {
          return memcpy(Dst, Src, Count);
      }
  }

  #define TEXT(x) x
  #define UE_LOG(cat, lvl, fmt, ...) ((void)0)

  struct FLogCategoryLogTemp {};
  static FLogCategoryLogTemp LogLiveSync_stub;
  #define LogLiveSync LogLiveSync_stub
#else
  #include "CoreMinimal.h"
#endif

// =========================================================
// Protocol headers (from Shared/Serializer)
// =========================================================

#include "livesync_serializer.h"
#include "livesync_deserializer.h"

// =========================================================
// LiveSyncBridge namespace
// =========================================================

namespace LiveSyncBridge
{

// =========================================================
// Protocol Detection
// =========================================================

static constexpr uint32 LEGACY_MAGIC = 0x4C56534D;

inline bool IsLegacyPacket(const uint8* Data, int32 DataSize)
{
    if (DataSize < static_cast<int32>(sizeof(uint32)))
    {
        return false;
    }

    uint32 Magic;
    FMemory::Memcpy(&Magic, Data, sizeof(uint32));
    return Magic == LEGACY_MAGIC;
}

inline bool IsMsgTypePacket(const uint8* Data, int32 DataSize)
{
    if (DataSize < static_cast<int32>(sizeof(uint32)))
    {
        return false;
    }

    uint32 LengthPrefix;
    FMemory::Memcpy(&LengthPrefix, Data, sizeof(uint32));
    return LengthPrefix < LEGACY_MAGIC;
}

// =========================================================
// EDispatchResult
// =========================================================

enum class EDispatchResult
{
    Legacy,
    Handled,
    Unsupported,
    ParseError,
    ProtocolViolation,
};

inline const char* DispatchResultToString(EDispatchResult R)
{
    switch (R)
    {
        case EDispatchResult::Legacy:            return "Legacy";
        case EDispatchResult::Handled:           return "Handled";
        case EDispatchResult::Unsupported:       return "Unsupported";
        case EDispatchResult::ParseError:        return "ParseError";
        case EDispatchResult::ProtocolViolation: return "ProtocolViolation";
        default:                                 return "Unknown";
    }
}

// =========================================================
// MessageTraits — UE dispatcher policy only
// =========================================================
// RequiresSession: post-session messages MUST have session_id
//                  in the wire header.
// AllowsSession:   pre-session messages MUST NOT have session_id.
//
// This table does NOT contain opcode, field list, or payload
// layout.  Those live in MessageTypes.yaml and the
// serializer/deserializer.
// =========================================================

struct MessageTraits
{
    bool RequiresSession;
    bool AllowsSession;
};

inline const MessageTraits GetMessageTraits(livesync::MsgType Type)
{
    switch (Type)
    {
        // Pre-session: no session_id in wire header
        case livesync::MsgType::HELLO:
        case livesync::MsgType::HELLO_ACK:
        case livesync::MsgType::REJECT:
            return {false, false};

        // Post-session: session_id required in wire header
        case livesync::MsgType::HEARTBEAT:
        case livesync::MsgType::HEARTBEAT_ACK:
        case livesync::MsgType::SCENE_HASH:
        case livesync::MsgType::SCENE_FULL:
        case livesync::MsgType::SCENE_DELTA:
        case livesync::MsgType::OBJECT_CREATE:
        case livesync::MsgType::OBJECT_UPDATE:
        case livesync::MsgType::OBJECT_DELETE:
        case livesync::MsgType::OBJECT_RENAME:
        case livesync::MsgType::OBJECT_REPARENT:
        case livesync::MsgType::OBJECT_VISIBILITY:
        case livesync::MsgType::MESH_DATA:
        case livesync::MsgType::MESH_DELTA:
        case livesync::MsgType::MESH_START:
        case livesync::MsgType::MESH_CHUNK:
        case livesync::MsgType::MESH_END:
        case livesync::MsgType::MATERIAL_CREATE:
        case livesync::MsgType::MATERIAL_UPDATE:
        case livesync::MsgType::MATERIAL_ASSIGN:
        case livesync::MsgType::CAMERA_CREATE:
        case livesync::MsgType::CAMERA_UPDATE:
        case livesync::MsgType::CAMERASETACTIVE:
        case livesync::MsgType::SYNC_ACK:
        case livesync::MsgType::ERROR:
        case livesync::MsgType::DISCONNECT:
            return {true, true};

        default:
            return {true, true};
    }
}

// =========================================================
// Version range (UE-supported)
// =========================================================

static constexpr uint8 UE_MIN_PROTOCOL_MAJOR = 1;
static constexpr uint8 UE_MIN_PROTOCOL_MINOR = 0;
static constexpr uint8 UE_MAX_PROTOCOL_MAJOR = 255;
static constexpr uint8 UE_MAX_PROTOCOL_MINOR = 255;

// =========================================================
// Test counters (compile-time hook)
// =========================================================

#ifdef UELIVESYNC_BRIDGE_TESTING
inline int g_hello_calls = 0;
inline int g_helloack_calls = 0;
inline int g_heartbeat_calls = 0;
inline int g_heartbeatack_calls = 0;
inline int g_objectcreate_calls = 0;
inline int g_objectupdate_calls = 0;
inline int g_objectdelete_calls = 0;
inline int g_objectrename_calls = 0;
inline int g_objectvisibility_calls = 0;
inline int g_objectreparent_calls = 0;
inline int g_materialcreate_calls = 0;
inline int g_materialupdate_calls = 0;
inline int g_materialassign_calls = 0;
inline int g_meshstart_calls = 0;
inline int g_meshchunk_calls = 0;
inline int g_meshend_calls = 0;
inline int g_meshdata_calls = 0;
inline int g_meshdelta_calls = 0;
inline int g_cameracreate_calls = 0;
inline int g_cameraupdate_calls = 0;
inline int g_camerasetactive_calls = 0;

inline void ResetAllCounters()
{
    g_hello_calls = 0;
    g_helloack_calls = 0;
    g_heartbeat_calls = 0;
    g_heartbeatack_calls = 0;
    g_objectcreate_calls = 0;
    g_objectupdate_calls = 0;
    g_objectdelete_calls = 0;
    g_objectrename_calls = 0;
    g_objectvisibility_calls = 0;
    g_objectreparent_calls = 0;
    g_materialcreate_calls = 0;
    g_materialupdate_calls = 0;
    g_materialassign_calls = 0;
    g_meshstart_calls = 0;
    g_meshchunk_calls = 0;
    g_meshend_calls = 0;
    g_meshdata_calls = 0;
    g_meshdelta_calls = 0;
    g_cameracreate_calls = 0;
    g_cameraupdate_calls = 0;
    g_camerasetactive_calls = 0;
}
#endif

// =========================================================
// Helpers — UUID formatting for log output
// =========================================================

inline void FormatUuid(
    const std::array<uint8_t, 16>& uuid,
    char* buf, size_t buf_size)
{
    if (buf_size < 37) { buf[0] = '\0'; return; }
    snprintf(buf, buf_size,
        "%02x%02x%02x%02x-%02x%02x-%02x%02x-%02x%02x-%02x%02x%02x%02x%02x%02x",
        uuid[0], uuid[1], uuid[2], uuid[3],
        uuid[4], uuid[5], uuid[6], uuid[7],
        uuid[8], uuid[9], uuid[10], uuid[11],
        uuid[12], uuid[13], uuid[14], uuid[15]);
}

// =========================================================
// Helpers — optional field access
// =========================================================

inline bool HasField(
    const livesync::DeserializedMessage& msg,
    const std::string& name)
{
    return msg.body.find(name) != msg.body.end();
}

template<typename T>
inline const T* TryGetField(
    const livesync::DeserializedMessage& msg,
    const std::string& name)
{
    auto it = msg.body.find(name);
    if (it == msg.body.end()) return nullptr;
    auto* p = std::get_if<T>(&it->second);
    return p;
}

template<typename T>
inline const T& GetField(
    const livesync::DeserializedMessage& msg,
    const std::string& name)
{
    return std::get<T>(msg.body.at(name));
}

// =========================================================
// View structs — Camera
// =========================================================
// Immutable data objects. No UE runtime dependencies.
// =========================================================

struct CameraCreateView
{
    std::array<uint8_t, 16> CameraId;
    std::string Name;
    struct { float X, Y, Z, Rx, Ry, Rz, Rw, Sx, Sy, Sz; } Transform;
    float FocalLength;
    float SensorWidth;
    float SensorHeight;
};

struct CameraUpdateView
{
    std::array<uint8_t, 16> CameraId;
    bool HasTransform;
    struct { float X, Y, Z, Rx, Ry, Rz, Rw, Sx, Sy, Sz; } Transform;
    bool HasFocalLength;
    float FocalLength;
    bool HasSensorWidth;
    float SensorWidth;
    bool HasSensorHeight;
    float SensorHeight;
};

struct CameraSetActiveView
{
    std::array<uint8_t, 16> CameraId;
};

// =========================================================
// Builders — Camera (pure functions)
// =========================================================
// Only field extraction via GetField/TryGetField.
// No UE API, no logging, no state mutation.
// =========================================================

inline CameraCreateView BuildCameraCreateView(
    const livesync::DeserializedMessage& msg)
{
    CameraCreateView v;
    v.CameraId = GetField<std::array<uint8_t, 16>>(msg, "camera_id");
    v.Name = GetField<std::string>(msg, "name");
    auto& t = GetField<std::vector<float>>(msg, "transform");
    v.Transform = {t[0], t[1], t[2], t[3], t[4], t[5], t[6], t[7], t[8], t[9]};
    v.FocalLength = GetField<float>(msg, "focal_length");
    v.SensorWidth = GetField<float>(msg, "sensor_width");
    v.SensorHeight = GetField<float>(msg, "sensor_height");
    return v;
}

inline CameraUpdateView BuildCameraUpdateView(
    const livesync::DeserializedMessage& msg)
{
    CameraUpdateView v;
    v.CameraId = GetField<std::array<uint8_t, 16>>(msg, "camera_id");
    auto* t = TryGetField<std::vector<float>>(msg, "transform");
    v.HasTransform = (t != nullptr);
    if (t) v.Transform = {(*t)[0], (*t)[1], (*t)[2], (*t)[3], (*t)[4], (*t)[5], (*t)[6], (*t)[7], (*t)[8], (*t)[9]};
    else v.Transform = {};
    auto* fl = TryGetField<float>(msg, "focal_length");
    v.HasFocalLength = (fl != nullptr);
    v.FocalLength = fl ? *fl : 0.0f;
    auto* sw = TryGetField<float>(msg, "sensor_width");
    v.HasSensorWidth = (sw != nullptr);
    v.SensorWidth = sw ? *sw : 0.0f;
    auto* sh = TryGetField<float>(msg, "sensor_height");
    v.HasSensorHeight = (sh != nullptr);
    v.SensorHeight = sh ? *sh : 0.0f;
    return v;
}

inline CameraSetActiveView BuildCameraSetActiveView(
    const livesync::DeserializedMessage& msg)
{
    CameraSetActiveView v;
    v.CameraId = GetField<std::array<uint8_t, 16>>(msg, "camera_id");
    return v;
}

// =========================================================
// Log functions — Camera
// =========================================================

inline void LogCameraCreate(const CameraCreateView& v)
{
    char id_str[37];
    FormatUuid(v.CameraId, id_str, sizeof(id_str));
    UE_LOG(LogLiveSync, Log,
        TEXT("[BRIDGE][CAMERA_CREATE] id=%hs name=%hs "
             "focal=%.1f sensor=%.1fx%.1f"),
        id_str, v.Name.c_str(),
        v.FocalLength, v.SensorWidth, v.SensorHeight);
}

inline void LogCameraUpdate(const CameraUpdateView& v)
{
    char id_str[37];
    FormatUuid(v.CameraId, id_str, sizeof(id_str));
    UE_LOG(LogLiveSync, Log,
        TEXT("[BRIDGE][CAMERA_UPDATE] id=%hs "
             "has_transform=%d focal=%hs%.1f"),
        id_str,
        static_cast<int>(v.HasTransform),
        v.HasFocalLength ? "" : "not_set=",
        v.HasFocalLength ? v.FocalLength : 0.0f);
}

inline void LogCameraSetActive(const CameraSetActiveView& v)
{
    char id_str[37];
    FormatUuid(v.CameraId, id_str, sizeof(id_str));
    UE_LOG(LogLiveSync, Log,
        TEXT("[BRIDGE][CAMERA_SETACTIVE] id=%hs"), id_str);
}

// =========================================================
// Dispatch functions — Camera (fan-out only)
// =========================================================
// Receives const View&. Does NOT modify view.
// Current: Log only. Phase 1.3.3: add GameplaySink.
// =========================================================

inline void DispatchCameraCreate(const CameraCreateView& v)
{
    LogCameraCreate(v);
}

inline void DispatchCameraUpdate(const CameraUpdateView& v)
{
    LogCameraUpdate(v);
}

inline void DispatchCameraSetActive(const CameraSetActiveView& v)
{
    LogCameraSetActive(v);
}

// =========================================================
// View structs — Object
// =========================================================

struct ObjectCreateView
{
    std::array<uint8_t, 16> PersistentId;
    std::string Name;
    bool HasParentId;
    std::array<uint8_t, 16> ParentId;
    std::vector<float> Transform;
};

struct ObjectUpdateView
{
    std::array<uint8_t, 16> PersistentId;
    bool HasTransform;
    std::vector<float> Transform;
    bool HasName;
    std::string Name;
    bool HasVisibility;
    uint8_t Visibility;
};

struct ObjectDeleteView
{
    std::array<uint8_t, 16> PersistentId;
};

struct ObjectRenameView
{
    std::array<uint8_t, 16> PersistentId;
    std::string NewName;
};

struct ObjectVisibilityView
{
    std::array<uint8_t, 16> PersistentId;
    uint8_t Visible;
};

struct ObjectReparentView
{
    std::array<uint8_t, 16> PersistentId;
    std::array<uint8_t, 16> NewParentId;
};

// =========================================================
// Builders — Object (pure functions)
// =========================================================

inline ObjectCreateView BuildObjectCreateView(
    const livesync::DeserializedMessage& msg)
{
    ObjectCreateView v;
    v.PersistentId = GetField<std::array<uint8_t, 16>>(msg, "persistent_id");
    v.Name = GetField<std::string>(msg, "name");
    auto* pid = TryGetField<std::array<uint8_t, 16>>(msg, "parent_id");
    v.HasParentId = (pid != nullptr);
    v.ParentId = pid ? *pid : std::array<uint8_t, 16>{};
    v.Transform = GetField<std::vector<float>>(msg, "transform");
    return v;
}

inline ObjectUpdateView BuildObjectUpdateView(
    const livesync::DeserializedMessage& msg)
{
    ObjectUpdateView v;
    v.PersistentId = GetField<std::array<uint8_t, 16>>(msg, "persistent_id");
    auto* t = TryGetField<std::vector<float>>(msg, "transform");
    v.HasTransform = (t != nullptr);
    v.Transform = t ? *t : std::vector<float>{};
    auto* n = TryGetField<std::string>(msg, "name");
    v.HasName = (n != nullptr);
    v.Name = n ? *n : std::string{};
    auto* vis = TryGetField<uint8_t>(msg, "visibility");
    v.HasVisibility = (vis != nullptr);
    v.Visibility = vis ? *vis : 0;
    return v;
}

inline ObjectDeleteView BuildObjectDeleteView(
    const livesync::DeserializedMessage& msg)
{
    ObjectDeleteView v;
    v.PersistentId = GetField<std::array<uint8_t, 16>>(msg, "persistent_id");
    return v;
}

inline ObjectRenameView BuildObjectRenameView(
    const livesync::DeserializedMessage& msg)
{
    ObjectRenameView v;
    v.PersistentId = GetField<std::array<uint8_t, 16>>(msg, "persistent_id");
    v.NewName = GetField<std::string>(msg, "new_name");
    return v;
}

inline ObjectVisibilityView BuildObjectVisibilityView(
    const livesync::DeserializedMessage& msg)
{
    ObjectVisibilityView v;
    v.PersistentId = GetField<std::array<uint8_t, 16>>(msg, "persistent_id");
    v.Visible = GetField<uint8_t>(msg, "visible");
    return v;
}

inline ObjectReparentView BuildObjectReparentView(
    const livesync::DeserializedMessage& msg)
{
    ObjectReparentView v;
    v.PersistentId = GetField<std::array<uint8_t, 16>>(msg, "persistent_id");
    v.NewParentId = GetField<std::array<uint8_t, 16>>(msg, "new_parent_id");
    return v;
}

// =========================================================
// Log functions — Object
// =========================================================

inline void LogObjectCreate(const ObjectCreateView& v)
{
    char id_str[37];
    FormatUuid(v.PersistentId, id_str, sizeof(id_str));
    char parent_str[37] = "none";
    if (v.HasParentId)
        FormatUuid(v.ParentId, parent_str, sizeof(parent_str));
    UE_LOG(LogLiveSync, Log,
        TEXT("[BRIDGE][OBJECT_CREATE] id=%hs name=%hs parent=%hs"),
        id_str, v.Name.c_str(), parent_str);
}

inline void LogObjectUpdate(const ObjectUpdateView& v)
{
    char id_str[37];
    FormatUuid(v.PersistentId, id_str, sizeof(id_str));
    UE_LOG(LogLiveSync, Log,
        TEXT("[BRIDGE][OBJECT_UPDATE] id=%hs has_transform=%d"),
        id_str, static_cast<int>(v.HasTransform));
}

inline void LogObjectDelete(const ObjectDeleteView& v)
{
    char id_str[37];
    FormatUuid(v.PersistentId, id_str, sizeof(id_str));
    UE_LOG(LogLiveSync, Log,
        TEXT("[BRIDGE][OBJECT_DELETE] id=%hs"), id_str);
}

inline void LogObjectRename(const ObjectRenameView& v)
{
    char id_str[37];
    FormatUuid(v.PersistentId, id_str, sizeof(id_str));
    UE_LOG(LogLiveSync, Log,
        TEXT("[BRIDGE][OBJECT_RENAME] id=%hs new_name=%hs"),
        id_str, v.NewName.c_str());
}

inline void LogObjectVisibility(const ObjectVisibilityView& v)
{
    char id_str[37];
    FormatUuid(v.PersistentId, id_str, sizeof(id_str));
    UE_LOG(LogLiveSync, Log,
        TEXT("[BRIDGE][OBJECT_VISIBILITY] id=%hs visible=%u"),
        id_str, static_cast<unsigned>(v.Visible));
}

inline void LogObjectReparent(const ObjectReparentView& v)
{
    char id_str[37];
    FormatUuid(v.PersistentId, id_str, sizeof(id_str));
    char parent_str[37];
    FormatUuid(v.NewParentId, parent_str, sizeof(parent_str));
    UE_LOG(LogLiveSync, Log,
        TEXT("[BRIDGE][OBJECT_REPARENT] id=%hs new_parent=%hs"),
        id_str, parent_str);
}

// =========================================================
// Dispatch functions — Object (fan-out only)
// =========================================================

inline void DispatchObjectCreate(const ObjectCreateView& v)
{
    LogObjectCreate(v);
}

inline void DispatchObjectUpdate(const ObjectUpdateView& v)
{
    LogObjectUpdate(v);
}

inline void DispatchObjectDelete(const ObjectDeleteView& v)
{
    LogObjectDelete(v);
}

inline void DispatchObjectRename(const ObjectRenameView& v)
{
    LogObjectRename(v);
}

inline void DispatchObjectVisibility(const ObjectVisibilityView& v)
{
    LogObjectVisibility(v);
}

inline void DispatchObjectReparent(const ObjectReparentView& v)
{
    LogObjectReparent(v);
}

// =========================================================
// Process functions — Object (orchestration)
// =========================================================
// Only ProcessXXX() returns EDispatchResult.
// =========================================================

inline EDispatchResult ProcessObjectCreate(
    const livesync::DeserializedMessage& msg)
{
#ifdef UELIVESYNC_BRIDGE_TESTING
    g_objectcreate_calls++;
#endif
    auto view = BuildObjectCreateView(msg);
    DispatchObjectCreate(view);
    return EDispatchResult::Handled;
}

inline EDispatchResult ProcessObjectUpdate(
    const livesync::DeserializedMessage& msg)
{
#ifdef UELIVESYNC_BRIDGE_TESTING
    g_objectupdate_calls++;
#endif
    auto view = BuildObjectUpdateView(msg);
    DispatchObjectUpdate(view);
    return EDispatchResult::Handled;
}

inline EDispatchResult ProcessObjectDelete(
    const livesync::DeserializedMessage& msg)
{
#ifdef UELIVESYNC_BRIDGE_TESTING
    g_objectdelete_calls++;
#endif
    auto view = BuildObjectDeleteView(msg);
    DispatchObjectDelete(view);
    return EDispatchResult::Handled;
}

inline EDispatchResult ProcessObjectRename(
    const livesync::DeserializedMessage& msg)
{
#ifdef UELIVESYNC_BRIDGE_TESTING
    g_objectrename_calls++;
#endif
    auto view = BuildObjectRenameView(msg);
    DispatchObjectRename(view);
    return EDispatchResult::Handled;
}

inline EDispatchResult ProcessObjectVisibility(
    const livesync::DeserializedMessage& msg)
{
#ifdef UELIVESYNC_BRIDGE_TESTING
    g_objectvisibility_calls++;
#endif
    auto view = BuildObjectVisibilityView(msg);
    DispatchObjectVisibility(view);
    return EDispatchResult::Handled;
}

inline EDispatchResult ProcessObjectReparent(
    const livesync::DeserializedMessage& msg)
{
#ifdef UELIVESYNC_BRIDGE_TESTING
    g_objectreparent_calls++;
#endif
    auto view = BuildObjectReparentView(msg);
    DispatchObjectReparent(view);
    return EDispatchResult::Handled;
}

// =========================================================
// Handlers — Phase 1.3.2a (handshake only)
// =========================================================
// Each handler receives a pre-validated DeserializedMessage.
// Handlers do NOT call DeserializeFrame or re-check invariants.
// =========================================================

inline void HandleHello(const livesync::DeserializedMessage& msg)
{
#ifdef UELIVESYNC_BRIDGE_TESTING
    g_hello_calls++;
#endif

    uint8_t maj = std::get<uint8_t>(msg.body.at("protocol_version_major"));
    uint8_t min = std::get<uint8_t>(msg.body.at("protocol_version_minor"));
    uint64_t caps = std::get<uint64_t>(msg.body.at("capabilities"));

    UE_LOG(LogLiveSync, Log,
        TEXT("[BRIDGE][HELLO] version=%u.%u capabilities=0x%llx"),
        maj, min, (unsigned long long)caps);
}

inline void HandleHelloAck(const livesync::DeserializedMessage& msg)
{
#ifdef UELIVESYNC_BRIDGE_TESTING
    g_helloack_calls++;
#endif

    uint8_t maj = std::get<uint8_t>(msg.body.at("protocol_version_major"));
    uint8_t min = std::get<uint8_t>(msg.body.at("protocol_version_minor"));
    uint64_t caps = std::get<uint64_t>(msg.body.at("accepted_capabilities"));
    uint32_t chunk = std::get<uint32_t>(msg.body.at("max_chunk_size"));
    uint64_t sid = std::get<uint64_t>(msg.body.at("session_id"));

    UE_LOG(LogLiveSync, Log,
        TEXT("[BRIDGE][HELLO_ACK] version=%u.%u accepted_caps=0x%llx "
             "max_chunk=%u session=0x%llx"),
        maj, min, (unsigned long long)caps, chunk,
        (unsigned long long)sid);
}

inline void HandleHeartbeat(const livesync::DeserializedMessage& msg)
{
#ifdef UELIVESYNC_BRIDGE_TESTING
    g_heartbeat_calls++;
#endif

    UE_LOG(LogLiveSync, Log,
        TEXT("[BRIDGE][HEARTBEAT] seq=%u session=0x%llx"),
        msg.sequence_id,
        msg.session_id.has_value()
            ? (unsigned long long)msg.session_id.value()
            : 0ULL);
}

inline void HandleHeartbeatAck(const livesync::DeserializedMessage& msg)
{
#ifdef UELIVESYNC_BRIDGE_TESTING
    g_heartbeatack_calls++;
#endif

    UE_LOG(LogLiveSync, Log,
        TEXT("[BRIDGE][HEARTBEAT_ACK] seq=%u session=0x%llx"),
        msg.sequence_id,
        msg.session_id.has_value()
            ? (unsigned long long)msg.session_id.value()
            : 0ULL);
}

// =========================================================
// Handlers — Phase 1.3.2d (materials)
// =========================================================

inline void HandleMaterialCreate(const livesync::DeserializedMessage& msg)
{
#ifdef UELIVESYNC_BRIDGE_TESTING
    g_materialcreate_calls++;
#endif

    char mid_str[37];
    auto& mid = GetField<std::array<uint8_t, 16>>(msg, "material_id");
    FormatUuid(mid, mid_str, sizeof(mid_str));

    auto& name = GetField<std::string>(msg, "name");
    auto& bc = GetField<std::vector<float>>(msg, "base_color");
    float metallic = GetField<float>(msg, "metallic");
    float roughness = GetField<float>(msg, "roughness");
    auto& emission = GetField<std::vector<float>>(msg, "emission");

    auto* tex = TryGetField<std::string>(msg, "texture_path");

    UE_LOG(LogLiveSync, Log,
        TEXT("[BRIDGE][MATERIAL_CREATE] id=%hs name=%hs "
             "base_color=[%.2f,%.2f,%.2f,%.2f] "
             "metallic=%.2f roughness=%.2f emission=[%.2f,%.2f,%.2f]"),
        mid_str, name.c_str(),
        bc[0], bc[1], bc[2], bc[3],
        metallic, roughness,
        emission[0], emission[1], emission[2]);

    if (tex)
    {
        UE_LOG(LogLiveSync, Log,
            TEXT("[BRIDGE][MATERIAL_CREATE] texture=%hs"),
            tex->c_str());
    }
}

inline void HandleMaterialUpdate(const livesync::DeserializedMessage& msg)
{
#ifdef UELIVESYNC_BRIDGE_TESTING
    g_materialupdate_calls++;
#endif

    char mid_str[37];
    auto& mid = GetField<std::array<uint8_t, 16>>(msg, "material_id");
    FormatUuid(mid, mid_str, sizeof(mid_str));

    auto& bc = GetField<std::vector<float>>(msg, "base_color");
    float metallic = GetField<float>(msg, "metallic");
    float roughness = GetField<float>(msg, "roughness");
    auto& emission = GetField<std::vector<float>>(msg, "emission");

    auto* tex = TryGetField<std::string>(msg, "texture_path");

    UE_LOG(LogLiveSync, Log,
        TEXT("[BRIDGE][MATERIAL_UPDATE] id=%hs "
             "base_color=[%.2f,%.2f,%.2f,%.2f] "
             "metallic=%.2f roughness=%.2f emission=[%.2f,%.2f,%.2f]"),
        mid_str,
        bc[0], bc[1], bc[2], bc[3],
        metallic, roughness,
        emission[0], emission[1], emission[2]);

    if (tex)
    {
        UE_LOG(LogLiveSync, Log,
            TEXT("[BRIDGE][MATERIAL_UPDATE] texture=%hs"),
            tex->c_str());
    }
}

inline void HandleMaterialAssign(const livesync::DeserializedMessage& msg)
{
#ifdef UELIVESYNC_BRIDGE_TESTING
    g_materialassign_calls++;
#endif

    char oid_str[37];
    auto& oid = GetField<std::array<uint8_t, 16>>(msg, "persistent_id");
    FormatUuid(oid, oid_str, sizeof(oid_str));

    char mid_str[37];
    auto& mid = GetField<std::array<uint8_t, 16>>(msg, "material_id");
    FormatUuid(mid, mid_str, sizeof(mid_str));

    uint8_t slot = GetField<uint8_t>(msg, "slot_index");

    UE_LOG(LogLiveSync, Log,
        TEXT("[BRIDGE][MATERIAL_ASSIGN] object=%hs material=%hs slot=%u"),
        oid_str, mid_str, static_cast<unsigned>(slot));
}

// =========================================================
// Handlers — Phase 1.3.2e (mesh)
// =========================================================
// Decomposed into small helpers for readability.
// Each handler logs identity + summary, not raw data.
// =========================================================

inline void LogMeshIdentity(
    const livesync::DeserializedMessage& msg,
    const char* label)
{
    char id_str[37];
    auto& pid = GetField<std::array<uint8_t, 16>>(msg, "persistent_id");
    FormatUuid(pid, id_str, sizeof(id_str));
    UE_LOG(LogLiveSync, Log,
        TEXT("[BRIDGE][%hs] id=%hs"), label, id_str);
}

inline void HandleMeshStart(const livesync::DeserializedMessage& msg)
{
#ifdef UELIVESYNC_BRIDGE_TESTING
    g_meshstart_calls++;
#endif

    LogMeshIdentity(msg, "MESH_START");

    uint16_t total = GetField<uint16_t>(msg, "total_chunks");
    uint8_t fmt = GetField<uint8_t>(msg, "format_flags");

    UE_LOG(LogLiveSync, Log,
        TEXT("[BRIDGE][MESH_START] total_chunks=%u format=0x%02x"),
        static_cast<unsigned>(total),
        static_cast<unsigned>(fmt));
}

inline void HandleMeshChunk(const livesync::DeserializedMessage& msg)
{
#ifdef UELIVESYNC_BRIDGE_TESTING
    g_meshchunk_calls++;
#endif

    LogMeshIdentity(msg, "MESH_CHUNK");

    uint16_t ci = GetField<uint16_t>(msg, "chunk_index");
    uint16_t vo = GetField<uint16_t>(msg, "vertex_offset");
    uint32_t vc = GetField<uint32_t>(msg, "vertex_count");
    uint32_t ic = GetField<uint32_t>(msg, "index_count");
    auto& data = GetField<std::vector<uint8_t>>(msg, "data");

    UE_LOG(LogLiveSync, Log,
        TEXT("[BRIDGE][MESH_CHUNK] chunk=%u vert_off=%u "
             "vert=%u idx=%u data=%zu bytes"),
        static_cast<unsigned>(ci),
        static_cast<unsigned>(vo),
        vc, ic, data.size());
}

inline void HandleMeshEnd(const livesync::DeserializedMessage& msg)
{
#ifdef UELIVESYNC_BRIDGE_TESTING
    g_meshend_calls++;
#endif

    LogMeshIdentity(msg, "MESH_END");

    uint32_t cs = GetField<uint32_t>(msg, "checksum");

    UE_LOG(LogLiveSync, Log,
        TEXT("[BRIDGE][MESH_END] checksum=0x%08x"), cs);
}

inline void HandleMeshData(const livesync::DeserializedMessage& msg)
{
#ifdef UELIVESYNC_BRIDGE_TESTING
    g_meshdata_calls++;
#endif

    LogMeshIdentity(msg, "MESH_DATA");

    uint32_t vc = GetField<uint32_t>(msg, "vertex_count");
    uint32_t ic = GetField<uint32_t>(msg, "index_count");
    uint8_t fmt = GetField<uint8_t>(msg, "format_flags");

    auto* verts = TryGetField<std::vector<float>>(msg, "vertices");
    auto* norms = TryGetField<std::vector<float>>(msg, "normals");
    auto* uvs = TryGetField<std::vector<float>>(msg, "uvs");
    auto* indices = TryGetField<std::vector<uint32_t>>(msg, "indices");

    UE_LOG(LogLiveSync, Log,
        TEXT("[BRIDGE][MESH_DATA] verts=%u idx=%u fmt=0x%02x "
             "vert_buf=%zu norm_buf=%zu uv_buf=%zu idx_buf=%zu"),
        vc, ic, static_cast<unsigned>(fmt),
        verts ? verts->size() : 0,
        norms ? norms->size() : 0,
        uvs ? uvs->size() : 0,
        indices ? indices->size() : 0);
}

inline void HandleMeshDelta(const livesync::DeserializedMessage& msg)
{
#ifdef UELIVESYNC_BRIDGE_TESTING
    g_meshdelta_calls++;
#endif

    LogMeshIdentity(msg, "MESH_DELTA");

    uint32_t vc = GetField<uint32_t>(msg, "vertex_count");
    uint8_t fmt = GetField<uint8_t>(msg, "format_flags");

    auto* verts = TryGetField<std::vector<float>>(msg, "vertices");
    auto* norms = TryGetField<std::vector<float>>(msg, "normals");
    auto* uvs = TryGetField<std::vector<float>>(msg, "uvs");

    UE_LOG(LogLiveSync, Log,
        TEXT("[BRIDGE][MESH_DELTA] verts=%u fmt=0x%02x "
             "vert_buf=%zu norm_buf=%zu uv_buf=%zu"),
        vc, static_cast<unsigned>(fmt),
        verts ? verts->size() : 0,
        norms ? norms->size() : 0,
        uvs ? uvs->size() : 0);
}

// =========================================================
// ValidateExtraInvariants — per-message-type checks
// =========================================================
// Called AFTER DeserializeFrame succeeds and traits check passes.
// Returns ProtocolViolation on failure, Handled if OK.
// =========================================================

inline EDispatchResult ValidateExtraInvariants(
    const livesync::DeserializedMessage& msg)
{
    if (msg.msg_type == livesync::MsgType::HELLO_ACK)
    {
        auto it = msg.body.find("session_id");
        if (it == msg.body.end())
        {
            UE_LOG(LogLiveSync, Warning,
                TEXT("[BRIDGE][VIOLATION] HELLO_ACK missing "
                     "body session_id"));
            return EDispatchResult::ProtocolViolation;
        }

        auto maj_it = msg.body.find("protocol_version_major");
        auto min_it = msg.body.find("protocol_version_minor");
        if (maj_it != msg.body.end() && min_it != msg.body.end())
        {
            uint8_t maj = std::get<uint8_t>(maj_it->second);
            uint8_t min = std::get<uint8_t>(min_it->second);

            if (maj < UE_MIN_PROTOCOL_MAJOR ||
                (maj == UE_MIN_PROTOCOL_MAJOR &&
                 min < UE_MIN_PROTOCOL_MINOR))
            {
                UE_LOG(LogLiveSync, Warning,
                    TEXT("[BRIDGE][VIOLATION] HELLO_ACK version "
                         "%u.%u below minimum %u.%u"),
                    maj, min,
                    UE_MIN_PROTOCOL_MAJOR, UE_MIN_PROTOCOL_MINOR);
                return EDispatchResult::ProtocolViolation;
            }
        }
    }

    return EDispatchResult::Handled;
}

// =========================================================
// DispatchMsgTypePacket — routing only
// =========================================================
// 1. DeserializeFrame()
// 2. Validate invariants via MessageTraits
// 3. Validate per-message invariants
// 4. switch(msg_type) -> HandleXXX(msg)
// =========================================================

inline EDispatchResult DispatchMsgTypePacket(
    const uint8* Data,
    int32 DataSize)
{
    livesync::DeserializedMessage msg;

    try
    {
        msg = livesync::DeserializeFrame(
            reinterpret_cast<const uint8_t*>(Data),
            static_cast<size_t>(DataSize));
    }
    catch (const std::exception& e)
    {
        UE_LOG(LogLiveSync, Warning,
            TEXT("[BRIDGE][PARSE_ERROR] %hs"), e.what());
        return EDispatchResult::ParseError;
    }
    catch (...)
    {
        UE_LOG(LogLiveSync, Warning,
            TEXT("[BRIDGE][PARSE_ERROR] unknown exception"));
        return EDispatchResult::ParseError;
    }

    const MessageTraits Traits = GetMessageTraits(msg.msg_type);

    if (Traits.RequiresSession && !msg.session_id.has_value())
    {
        UE_LOG(LogLiveSync, Warning,
            TEXT("[BRIDGE][VIOLATION] MsgType 0x%02x requires "
                 "session_id but header has none"),
            static_cast<int>(msg.msg_type));
        return EDispatchResult::ProtocolViolation;
    }

    if (!Traits.AllowsSession && msg.session_id.has_value())
    {
        UE_LOG(LogLiveSync, Warning,
            TEXT("[BRIDGE][VIOLATION] MsgType 0x%02x is "
                 "pre-session but header has session_id"),
            static_cast<int>(msg.msg_type));
        return EDispatchResult::ProtocolViolation;
    }

    EDispatchResult Extra = ValidateExtraInvariants(msg);
    if (Extra != EDispatchResult::Handled)
    {
        return Extra;
    }

    switch (msg.msg_type)
    {
        case livesync::MsgType::HELLO:
            HandleHello(msg);
            return EDispatchResult::Handled;

        case livesync::MsgType::HELLO_ACK:
            HandleHelloAck(msg);
            return EDispatchResult::Handled;

        case livesync::MsgType::HEARTBEAT:
            HandleHeartbeat(msg);
            return EDispatchResult::Handled;

        case livesync::MsgType::HEARTBEAT_ACK:
            HandleHeartbeatAck(msg);
            return EDispatchResult::Handled;

        case livesync::MsgType::OBJECT_CREATE:
            return ProcessObjectCreate(msg);

        case livesync::MsgType::OBJECT_UPDATE:
            return ProcessObjectUpdate(msg);

        case livesync::MsgType::OBJECT_DELETE:
            return ProcessObjectDelete(msg);

        case livesync::MsgType::OBJECT_RENAME:
            return ProcessObjectRename(msg);

        case livesync::MsgType::OBJECT_VISIBILITY:
            return ProcessObjectVisibility(msg);

        case livesync::MsgType::OBJECT_REPARENT:
            return ProcessObjectReparent(msg);

        case livesync::MsgType::MATERIAL_CREATE:
            HandleMaterialCreate(msg);
            return EDispatchResult::Handled;

        case livesync::MsgType::MATERIAL_UPDATE:
            HandleMaterialUpdate(msg);
            return EDispatchResult::Handled;

        case livesync::MsgType::MATERIAL_ASSIGN:
            HandleMaterialAssign(msg);
            return EDispatchResult::Handled;

        case livesync::MsgType::MESH_START:
            HandleMeshStart(msg);
            return EDispatchResult::Handled;

        case livesync::MsgType::MESH_CHUNK:
            HandleMeshChunk(msg);
            return EDispatchResult::Handled;

        case livesync::MsgType::MESH_END:
            HandleMeshEnd(msg);
            return EDispatchResult::Handled;

        case livesync::MsgType::MESH_DATA:
            HandleMeshData(msg);
            return EDispatchResult::Handled;

        case livesync::MsgType::MESH_DELTA:
            HandleMeshDelta(msg);
            return EDispatchResult::Handled;

        case livesync::MsgType::CAMERA_CREATE:
        {
#ifdef UELIVESYNC_BRIDGE_TESTING
            g_cameracreate_calls++;
#endif
            auto view = BuildCameraCreateView(msg);
            DispatchCameraCreate(view);
            return EDispatchResult::Handled;
        }

        case livesync::MsgType::CAMERA_UPDATE:
        {
#ifdef UELIVESYNC_BRIDGE_TESTING
            g_cameraupdate_calls++;
#endif
            auto view = BuildCameraUpdateView(msg);
            DispatchCameraUpdate(view);
            return EDispatchResult::Handled;
        }

        case livesync::MsgType::CAMERASETACTIVE:
        {
#ifdef UELIVESYNC_BRIDGE_TESTING
            g_camerasetactive_calls++;
#endif
            auto view = BuildCameraSetActiveView(msg);
            DispatchCameraSetActive(view);
            return EDispatchResult::Handled;
        }

        default:
            UE_LOG(LogLiveSync, Log,
                TEXT("[BRIDGE][UNSUPPORTED] MsgType 0x%02x"),
                static_cast<int>(msg.msg_type));
            return EDispatchResult::Unsupported;
    }
}

} // namespace LiveSyncBridge
