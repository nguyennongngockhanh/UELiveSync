#pragma once

// =========================================================
// LiveSyncProtocolBridge.h — MsgType Protocol Dispatcher
// =========================================================
// Phase 1.3.2a–1.3.2g: Bridge Architecture v1
//
// Pipeline (4-layer):
//   ProcessBinaryPacket()
//     -> DispatchMsgTypePacket() -> EDispatchResult
//          |-- DeserializeFrame()
//          |-- GetMessageTraits() — session requirements
//          |-- ValidateExtraInvariants() — per-type checks
//          +-- switch(msg_type) -> ProcessXXX(msg)
//                 |-- BuildXXXView(msg)     [pure builder]
//                 +-- DispatchXXX(view)     [fan-out: Log, Gameplay]
//                      +-- LogXXX(view)     [format & log]
//
// Raw DeserializedMessage field access is allowed ONLY in:
//   - ValidateExtraInvariants()
//   - BuildXXXView()
// All other layers (Dispatch, Log, Process orchestration) use
// View objects exclusively.
//
// Design rules:
//   - One MsgType → one ProcessXXX() entry point.
//   - Builder = pure function (no log, no UE API, no mutation).
//   - View = immutable DTO (primitive data, no UE pointers).
//   - Dispatch = fan-out only (const View&).
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
#include "LiveSyncViews.h"
#include "DispatchContext.h"
#include "IGameplaySink.h"

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

inline void DispatchCameraCreate(
    const CameraCreateView& v,
    const DispatchContext& ctx)
{
    LogCameraCreate(v);
    if (ctx.Gameplay) ctx.Gameplay->OnCameraCreate(v);
}

inline void DispatchCameraUpdate(
    const CameraUpdateView& v,
    const DispatchContext& ctx)
{
    LogCameraUpdate(v);
    if (ctx.Gameplay) ctx.Gameplay->OnCameraUpdate(v);
}

inline void DispatchCameraSetActive(
    const CameraSetActiveView& v,
    const DispatchContext& ctx)
{
    LogCameraSetActive(v);
    if (ctx.Gameplay) ctx.Gameplay->OnCameraSetActive(v);
}

// =========================================================
// Process functions — Camera (orchestration)
// =========================================================

inline EDispatchResult ProcessCameraCreate(
    const livesync::DeserializedMessage& msg,
    const DispatchContext& ctx)
{
#ifdef UELIVESYNC_BRIDGE_TESTING
    g_cameracreate_calls++;
#endif
    auto view = BuildCameraCreateView(msg);
    DispatchCameraCreate(view, ctx);
    return EDispatchResult::Handled;
}

inline EDispatchResult ProcessCameraUpdate(
    const livesync::DeserializedMessage& msg,
    const DispatchContext& ctx)
{
#ifdef UELIVESYNC_BRIDGE_TESTING
    g_cameraupdate_calls++;
#endif
    auto view = BuildCameraUpdateView(msg);
    DispatchCameraUpdate(view, ctx);
    return EDispatchResult::Handled;
}

inline EDispatchResult ProcessCameraSetActive(
    const livesync::DeserializedMessage& msg,
    const DispatchContext& ctx)
{
#ifdef UELIVESYNC_BRIDGE_TESTING
    g_camerasetactive_calls++;
#endif
    auto view = BuildCameraSetActiveView(msg);
    DispatchCameraSetActive(view, ctx);
    return EDispatchResult::Handled;
}

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
    v.PrimitiveType = GetField<uint8_t>(msg, "primitive_type");
    v.Transform = GetField<std::vector<float>>(msg, "transform");
    v.SequenceNumber = GetField<uint32_t>(msg, "sequence_number");
    v.Timestamp = GetField<double>(msg, "timestamp");
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
    v.SequenceNumber = GetField<uint32_t>(msg, "sequence_number");
    v.Timestamp = GetField<double>(msg, "timestamp");
    return v;
}

inline ObjectDeleteView BuildObjectDeleteView(
    const livesync::DeserializedMessage& msg)
{
    ObjectDeleteView v;
    v.PersistentId = GetField<std::array<uint8_t, 16>>(msg, "persistent_id");
    v.SequenceNumber = GetField<uint32_t>(msg, "sequence_number");
    v.Timestamp = GetField<double>(msg, "timestamp");
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
        TEXT("[BRIDGE][OBJECT_CREATE] id=%hs name=%hs parent=%hs "
             "primitive_type=%u seq=%u ts=%.3f"),
        id_str, v.Name.c_str(), parent_str,
        static_cast<unsigned>(v.PrimitiveType),
        v.SequenceNumber, v.Timestamp);
}

inline void LogObjectUpdate(const ObjectUpdateView& v)
{
    char id_str[37];
    FormatUuid(v.PersistentId, id_str, sizeof(id_str));
    UE_LOG(LogLiveSync, Log,
        TEXT("[BRIDGE][OBJECT_UPDATE] id=%hs has_transform=%d "
             "seq=%u ts=%.3f"),
        id_str, static_cast<int>(v.HasTransform),
        v.SequenceNumber, v.Timestamp);
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

inline void DispatchObjectCreate(
    const ObjectCreateView& v,
    const DispatchContext& ctx)
{
    LogObjectCreate(v);
    if (ctx.Gameplay) ctx.Gameplay->OnObjectCreate(v);
}

inline void DispatchObjectUpdate(
    const ObjectUpdateView& v,
    const DispatchContext& ctx)
{
    LogObjectUpdate(v);
    if (ctx.Gameplay) ctx.Gameplay->OnObjectUpdate(v);
}

inline void DispatchObjectDelete(
    const ObjectDeleteView& v,
    const DispatchContext& ctx)
{
    LogObjectDelete(v);
    if (ctx.Gameplay) ctx.Gameplay->OnObjectDelete(v);
}

inline void DispatchObjectRename(
    const ObjectRenameView& v,
    const DispatchContext& ctx)
{
    LogObjectRename(v);
    if (ctx.Gameplay) ctx.Gameplay->OnObjectRename(v);
}

inline void DispatchObjectVisibility(
    const ObjectVisibilityView& v,
    const DispatchContext& ctx)
{
    LogObjectVisibility(v);
    if (ctx.Gameplay) ctx.Gameplay->OnObjectVisibility(v);
}

inline void DispatchObjectReparent(
    const ObjectReparentView& v,
    const DispatchContext& ctx)
{
    LogObjectReparent(v);
    if (ctx.Gameplay) ctx.Gameplay->OnObjectReparent(v);
}

// =========================================================
// Process functions — Object (orchestration)
// =========================================================

inline EDispatchResult ProcessObjectCreate(
    const livesync::DeserializedMessage& msg,
    const DispatchContext& ctx)
{
#ifdef UELIVESYNC_BRIDGE_TESTING
    g_objectcreate_calls++;
#endif
    auto view = BuildObjectCreateView(msg);
    DispatchObjectCreate(view, ctx);
    return EDispatchResult::Handled;
}

inline EDispatchResult ProcessObjectUpdate(
    const livesync::DeserializedMessage& msg,
    const DispatchContext& ctx)
{
#ifdef UELIVESYNC_BRIDGE_TESTING
    g_objectupdate_calls++;
#endif
    auto view = BuildObjectUpdateView(msg);
    DispatchObjectUpdate(view, ctx);
    return EDispatchResult::Handled;
}

inline EDispatchResult ProcessObjectDelete(
    const livesync::DeserializedMessage& msg,
    const DispatchContext& ctx)
{
#ifdef UELIVESYNC_BRIDGE_TESTING
    g_objectdelete_calls++;
#endif
    auto view = BuildObjectDeleteView(msg);
    DispatchObjectDelete(view, ctx);
    return EDispatchResult::Handled;
}

inline EDispatchResult ProcessObjectRename(
    const livesync::DeserializedMessage& msg,
    const DispatchContext& ctx)
{
#ifdef UELIVESYNC_BRIDGE_TESTING
    g_objectrename_calls++;
#endif
    auto view = BuildObjectRenameView(msg);
    DispatchObjectRename(view, ctx);
    return EDispatchResult::Handled;
}

inline EDispatchResult ProcessObjectVisibility(
    const livesync::DeserializedMessage& msg,
    const DispatchContext& ctx)
{
#ifdef UELIVESYNC_BRIDGE_TESTING
    g_objectvisibility_calls++;
#endif
    auto view = BuildObjectVisibilityView(msg);
    DispatchObjectVisibility(view, ctx);
    return EDispatchResult::Handled;
}

inline EDispatchResult ProcessObjectReparent(
    const livesync::DeserializedMessage& msg,
    const DispatchContext& ctx)
{
#ifdef UELIVESYNC_BRIDGE_TESTING
    g_objectreparent_calls++;
#endif
    auto view = BuildObjectReparentView(msg);
    DispatchObjectReparent(view, ctx);
    return EDispatchResult::Handled;
}

// =========================================================
// Builders — Handshake (pure functions)
// =========================================================

inline HelloView BuildHelloView(
    const livesync::DeserializedMessage& msg)
{
    HelloView v;
    v.ProtocolVersionMajor = GetField<uint8_t>(msg, "protocol_version_major");
    v.ProtocolVersionMinor = GetField<uint8_t>(msg, "protocol_version_minor");
    v.Capabilities = GetField<uint64_t>(msg, "capabilities");
    return v;
}

inline HelloAckView BuildHelloAckView(
    const livesync::DeserializedMessage& msg)
{
    HelloAckView v;
    v.ProtocolVersionMajor = GetField<uint8_t>(msg, "protocol_version_major");
    v.ProtocolVersionMinor = GetField<uint8_t>(msg, "protocol_version_minor");
    v.AcceptedCapabilities = GetField<uint64_t>(msg, "accepted_capabilities");
    v.MaxChunkSize = GetField<uint32_t>(msg, "max_chunk_size");
    v.SessionId = GetField<uint64_t>(msg, "session_id");
    return v;
}

inline HeartbeatView BuildHeartbeatView(
    const livesync::DeserializedMessage& msg)
{
    HeartbeatView v;
    v.SequenceId = msg.sequence_id;
    v.HasSessionId = msg.session_id.has_value();
    v.SessionId = v.HasSessionId ? msg.session_id.value() : 0;
    return v;
}

inline HeartbeatAckView BuildHeartbeatAckView(
    const livesync::DeserializedMessage& msg)
{
    HeartbeatAckView v;
    v.SequenceId = msg.sequence_id;
    v.HasSessionId = msg.session_id.has_value();
    v.SessionId = v.HasSessionId ? msg.session_id.value() : 0;
    return v;
}

// =========================================================
// Log functions — Handshake
// =========================================================

inline void LogHello(const HelloView& v)
{
    UE_LOG(LogLiveSync, Log,
        TEXT("[BRIDGE][HELLO] version=%u.%u capabilities=0x%llx"),
        v.ProtocolVersionMajor, v.ProtocolVersionMinor,
        (unsigned long long)v.Capabilities);
}

inline void LogHelloAck(const HelloAckView& v)
{
    UE_LOG(LogLiveSync, Log,
        TEXT("[BRIDGE][HELLO_ACK] version=%u.%u accepted_caps=0x%llx "
             "max_chunk=%u session=0x%llx"),
        v.ProtocolVersionMajor, v.ProtocolVersionMinor,
        (unsigned long long)v.AcceptedCapabilities,
        v.MaxChunkSize, (unsigned long long)v.SessionId);
}

inline void LogHeartbeat(const HeartbeatView& v)
{
    UE_LOG(LogLiveSync, Log,
        TEXT("[BRIDGE][HEARTBEAT] seq=%u session=0x%llx"),
        v.SequenceId, (unsigned long long)v.SessionId);
}

inline void LogHeartbeatAck(const HeartbeatAckView& v)
{
    UE_LOG(LogLiveSync, Log,
        TEXT("[BRIDGE][HEARTBEAT_ACK] seq=%u session=0x%llx"),
        v.SequenceId, (unsigned long long)v.SessionId);
}

// =========================================================
// Dispatch functions — Handshake (fan-out only)
// =========================================================

inline void DispatchHello(
    const HelloView& v,
    const DispatchContext& ctx)
{
    LogHello(v);
    (void)ctx;
}

inline void DispatchHelloAck(
    const HelloAckView& v,
    const DispatchContext& ctx)
{
    LogHelloAck(v);
    (void)ctx;
}

inline void DispatchHeartbeat(
    const HeartbeatView& v,
    const DispatchContext& ctx)
{
    LogHeartbeat(v);
    (void)ctx;
}

inline void DispatchHeartbeatAck(
    const HeartbeatAckView& v,
    const DispatchContext& ctx)
{
    LogHeartbeatAck(v);
    (void)ctx;
}

// =========================================================
// Process functions — Handshake (orchestration)
// =========================================================

inline EDispatchResult ProcessHello(
    const livesync::DeserializedMessage& msg,
    const DispatchContext& ctx)
{
#ifdef UELIVESYNC_BRIDGE_TESTING
    g_hello_calls++;
#endif
    auto view = BuildHelloView(msg);
    DispatchHello(view, ctx);
    return EDispatchResult::Handled;
}

inline EDispatchResult ProcessHelloAck(
    const livesync::DeserializedMessage& msg,
    const DispatchContext& ctx)
{
#ifdef UELIVESYNC_BRIDGE_TESTING
    g_helloack_calls++;
#endif
    auto view = BuildHelloAckView(msg);
    DispatchHelloAck(view, ctx);
    return EDispatchResult::Handled;
}

inline EDispatchResult ProcessHeartbeat(
    const livesync::DeserializedMessage& msg,
    const DispatchContext& ctx)
{
#ifdef UELIVESYNC_BRIDGE_TESTING
    g_heartbeat_calls++;
#endif
    auto view = BuildHeartbeatView(msg);
    DispatchHeartbeat(view, ctx);
    return EDispatchResult::Handled;
}

inline EDispatchResult ProcessHeartbeatAck(
    const livesync::DeserializedMessage& msg,
    const DispatchContext& ctx)
{
#ifdef UELIVESYNC_BRIDGE_TESTING
    g_heartbeatack_calls++;
#endif
    auto view = BuildHeartbeatAckView(msg);
    DispatchHeartbeatAck(view, ctx);
    return EDispatchResult::Handled;
}

// =========================================================
// Builders — Material (pure functions)
// =========================================================

inline MaterialCreateView BuildMaterialCreateView(
    const livesync::DeserializedMessage& msg)
{
    MaterialCreateView v;
    v.MaterialId = GetField<std::array<uint8_t, 16>>(msg, "material_id");
    v.Name = GetField<std::string>(msg, "name");
    v.BaseColor = GetField<std::vector<float>>(msg, "base_color");
    v.Metallic = GetField<float>(msg, "metallic");
    v.Roughness = GetField<float>(msg, "roughness");
    v.Emission = GetField<std::vector<float>>(msg, "emission");
    auto* tex = TryGetField<std::string>(msg, "texture_path");
    v.HasTexturePath = (tex != nullptr);
    v.TexturePath = tex ? *tex : std::string{};
    return v;
}

inline MaterialUpdateView BuildMaterialUpdateView(
    const livesync::DeserializedMessage& msg)
{
    MaterialUpdateView v;
    v.MaterialId = GetField<std::array<uint8_t, 16>>(msg, "material_id");
    v.BaseColor = GetField<std::vector<float>>(msg, "base_color");
    v.Metallic = GetField<float>(msg, "metallic");
    v.Roughness = GetField<float>(msg, "roughness");
    v.Emission = GetField<std::vector<float>>(msg, "emission");
    auto* tex = TryGetField<std::string>(msg, "texture_path");
    v.HasTexturePath = (tex != nullptr);
    v.TexturePath = tex ? *tex : std::string{};
    return v;
}

inline MaterialAssignView BuildMaterialAssignView(
    const livesync::DeserializedMessage& msg)
{
    MaterialAssignView v;
    v.PersistentId = GetField<std::array<uint8_t, 16>>(msg, "persistent_id");
    v.MaterialId = GetField<std::array<uint8_t, 16>>(msg, "material_id");
    v.SlotIndex = GetField<uint8_t>(msg, "slot_index");
    return v;
}

// =========================================================
// Log functions — Material
// =========================================================

inline void LogMaterialCreate(const MaterialCreateView& v)
{
    char mid_str[37];
    FormatUuid(v.MaterialId, mid_str, sizeof(mid_str));
    UE_LOG(LogLiveSync, Log,
        TEXT("[BRIDGE][MATERIAL_CREATE] id=%hs name=%hs "
             "metallic=%.2f roughness=%.2f"),
        mid_str, v.Name.c_str(), v.Metallic, v.Roughness);
    if (v.HasTexturePath)
    {
        UE_LOG(LogLiveSync, Log,
            TEXT("[BRIDGE][MATERIAL_CREATE] texture=%hs"),
            v.TexturePath.c_str());
    }
}

inline void LogMaterialUpdate(const MaterialUpdateView& v)
{
    char mid_str[37];
    FormatUuid(v.MaterialId, mid_str, sizeof(mid_str));
    UE_LOG(LogLiveSync, Log,
        TEXT("[BRIDGE][MATERIAL_UPDATE] id=%hs "
             "metallic=%.2f roughness=%.2f"),
        mid_str, v.Metallic, v.Roughness);
    if (v.HasTexturePath)
    {
        UE_LOG(LogLiveSync, Log,
            TEXT("[BRIDGE][MATERIAL_UPDATE] texture=%hs"),
            v.TexturePath.c_str());
    }
}

inline void LogMaterialAssign(const MaterialAssignView& v)
{
    char oid_str[37];
    FormatUuid(v.PersistentId, oid_str, sizeof(oid_str));
    char mid_str[37];
    FormatUuid(v.MaterialId, mid_str, sizeof(mid_str));
    UE_LOG(LogLiveSync, Log,
        TEXT("[BRIDGE][MATERIAL_ASSIGN] object=%hs material=%hs slot=%u"),
        oid_str, mid_str, static_cast<unsigned>(v.SlotIndex));
}

// =========================================================
// Dispatch functions — Material (fan-out only)
// =========================================================

inline void DispatchMaterialCreate(
    const MaterialCreateView& v,
    const DispatchContext& ctx)
{
    LogMaterialCreate(v);
    if (ctx.Gameplay) ctx.Gameplay->OnMaterialCreate(v);
}

inline void DispatchMaterialUpdate(
    const MaterialUpdateView& v,
    const DispatchContext& ctx)
{
    LogMaterialUpdate(v);
    if (ctx.Gameplay) ctx.Gameplay->OnMaterialUpdate(v);
}

inline void DispatchMaterialAssign(
    const MaterialAssignView& v,
    const DispatchContext& ctx)
{
    LogMaterialAssign(v);
    if (ctx.Gameplay) ctx.Gameplay->OnMaterialAssign(v);
}

// =========================================================
// Process functions — Material (orchestration)
// =========================================================

inline EDispatchResult ProcessMaterialCreate(
    const livesync::DeserializedMessage& msg,
    const DispatchContext& ctx)
{
#ifdef UELIVESYNC_BRIDGE_TESTING
    g_materialcreate_calls++;
#endif
    auto view = BuildMaterialCreateView(msg);
    DispatchMaterialCreate(view, ctx);
    return EDispatchResult::Handled;
}

inline EDispatchResult ProcessMaterialUpdate(
    const livesync::DeserializedMessage& msg,
    const DispatchContext& ctx)
{
#ifdef UELIVESYNC_BRIDGE_TESTING
    g_materialupdate_calls++;
#endif
    auto view = BuildMaterialUpdateView(msg);
    DispatchMaterialUpdate(view, ctx);
    return EDispatchResult::Handled;
}

inline EDispatchResult ProcessMaterialAssign(
    const livesync::DeserializedMessage& msg,
    const DispatchContext& ctx)
{
#ifdef UELIVESYNC_BRIDGE_TESTING
    g_materialassign_calls++;
#endif
    auto view = BuildMaterialAssignView(msg);
    DispatchMaterialAssign(view, ctx);
    return EDispatchResult::Handled;
}

// =========================================================
// Builders — Mesh (pure functions)
// =========================================================

inline MeshStartView BuildMeshStartView(
    const livesync::DeserializedMessage& msg)
{
    MeshStartView v;
    v.PersistentId = GetField<std::array<uint8_t, 16>>(msg, "persistent_id");
    v.TotalChunks = GetField<uint16_t>(msg, "total_chunks");
    v.FormatFlags = GetField<uint8_t>(msg, "format_flags");
    return v;
}

inline MeshChunkView BuildMeshChunkView(
    const livesync::DeserializedMessage& msg)
{
    MeshChunkView v;
    v.PersistentId = GetField<std::array<uint8_t, 16>>(msg, "persistent_id");
    v.ChunkIndex = GetField<uint16_t>(msg, "chunk_index");
    v.VertexOffset = GetField<uint16_t>(msg, "vertex_offset");
    v.VertexCount = GetField<uint32_t>(msg, "vertex_count");
    v.IndexCount = GetField<uint32_t>(msg, "index_count");
    v.Data = GetField<std::vector<uint8_t>>(msg, "data");
    return v;
}

inline MeshEndView BuildMeshEndView(
    const livesync::DeserializedMessage& msg)
{
    MeshEndView v;
    v.PersistentId = GetField<std::array<uint8_t, 16>>(msg, "persistent_id");
    v.Checksum = GetField<uint32_t>(msg, "checksum");
    return v;
}

inline MeshDataView BuildMeshDataView(
    const livesync::DeserializedMessage& msg)
{
    MeshDataView v;
    v.PersistentId = GetField<std::array<uint8_t, 16>>(msg, "persistent_id");
    v.VertexCount = GetField<uint32_t>(msg, "vertex_count");
    v.IndexCount = GetField<uint32_t>(msg, "index_count");
    v.FormatFlags = GetField<uint8_t>(msg, "format_flags");
    auto* verts = TryGetField<std::vector<float>>(msg, "vertices");
    v.Vertices = verts ? *verts : std::vector<float>{};
    auto* norms = TryGetField<std::vector<float>>(msg, "normals");
    v.Normals = norms ? *norms : std::vector<float>{};
    auto* uvs = TryGetField<std::vector<float>>(msg, "uvs");
    v.Uvs = uvs ? *uvs : std::vector<float>{};
    auto* indices = TryGetField<std::vector<uint32_t>>(msg, "indices");
    v.Indices = indices ? *indices : std::vector<uint32_t>{};
    return v;
}

inline MeshDeltaView BuildMeshDeltaView(
    const livesync::DeserializedMessage& msg)
{
    MeshDeltaView v;
    v.PersistentId = GetField<std::array<uint8_t, 16>>(msg, "persistent_id");
    v.VertexCount = GetField<uint32_t>(msg, "vertex_count");
    v.FormatFlags = GetField<uint8_t>(msg, "format_flags");
    auto* verts = TryGetField<std::vector<float>>(msg, "vertices");
    v.Vertices = verts ? *verts : std::vector<float>{};
    auto* norms = TryGetField<std::vector<float>>(msg, "normals");
    v.Normals = norms ? *norms : std::vector<float>{};
    auto* uvs = TryGetField<std::vector<float>>(msg, "uvs");
    v.Uvs = uvs ? *uvs : std::vector<float>{};
    return v;
}

// =========================================================
// Log functions — Mesh
// =========================================================

inline void LogMeshStart(const MeshStartView& v)
{
    char id_str[37];
    FormatUuid(v.PersistentId, id_str, sizeof(id_str));
    UE_LOG(LogLiveSync, Log,
        TEXT("[BRIDGE][MESH_START] id=%hs total_chunks=%u format=0x%02x"),
        id_str, static_cast<unsigned>(v.TotalChunks),
        static_cast<unsigned>(v.FormatFlags));
}

inline void LogMeshChunk(const MeshChunkView& v)
{
    char id_str[37];
    FormatUuid(v.PersistentId, id_str, sizeof(id_str));
    UE_LOG(LogLiveSync, Log,
        TEXT("[BRIDGE][MESH_CHUNK] id=%hs chunk=%u vert_off=%u "
             "vert=%u idx=%u data=%zu bytes"),
        id_str,
        static_cast<unsigned>(v.ChunkIndex),
        static_cast<unsigned>(v.VertexOffset),
        v.VertexCount, v.IndexCount, v.Data.size());
}

inline void LogMeshEnd(const MeshEndView& v)
{
    char id_str[37];
    FormatUuid(v.PersistentId, id_str, sizeof(id_str));
    UE_LOG(LogLiveSync, Log,
        TEXT("[BRIDGE][MESH_END] id=%hs checksum=0x%08x"),
        id_str, v.Checksum);
}

inline void LogMeshData(const MeshDataView& v)
{
    char id_str[37];
    FormatUuid(v.PersistentId, id_str, sizeof(id_str));
    UE_LOG(LogLiveSync, Log,
        TEXT("[BRIDGE][MESH_DATA] id=%hs verts=%u idx=%u fmt=0x%02x "
             "vert_buf=%zu norm_buf=%zu uv_buf=%zu idx_buf=%zu"),
        id_str, v.VertexCount, v.IndexCount,
        static_cast<unsigned>(v.FormatFlags),
        v.Vertices.size(), v.Normals.size(),
        v.Uvs.size(), v.Indices.size());
}

inline void LogMeshDelta(const MeshDeltaView& v)
{
    char id_str[37];
    FormatUuid(v.PersistentId, id_str, sizeof(id_str));
    UE_LOG(LogLiveSync, Log,
        TEXT("[BRIDGE][MESH_DELTA] id=%hs verts=%u fmt=0x%02x "
             "vert_buf=%zu norm_buf=%zu uv_buf=%zu"),
        id_str, v.VertexCount,
        static_cast<unsigned>(v.FormatFlags),
        v.Vertices.size(), v.Normals.size(), v.Uvs.size());
}

// =========================================================
// Dispatch functions — Mesh (fan-out only)
// =========================================================

inline void DispatchMeshStart(
    const MeshStartView& v,
    const DispatchContext& ctx)
{
    LogMeshStart(v);
    if (ctx.Gameplay) ctx.Gameplay->OnMeshStart(v);
}

inline void DispatchMeshChunk(
    const MeshChunkView& v,
    const DispatchContext& ctx)
{
    LogMeshChunk(v);
    if (ctx.Gameplay) ctx.Gameplay->OnMeshChunk(v);
}

inline void DispatchMeshEnd(
    const MeshEndView& v,
    const DispatchContext& ctx)
{
    LogMeshEnd(v);
    if (ctx.Gameplay) ctx.Gameplay->OnMeshEnd(v);
}

inline void DispatchMeshData(
    const MeshDataView& v,
    const DispatchContext& ctx)
{
    LogMeshData(v);
    if (ctx.Gameplay) ctx.Gameplay->OnMeshData(v);
}

inline void DispatchMeshDelta(
    const MeshDeltaView& v,
    const DispatchContext& ctx)
{
    LogMeshDelta(v);
    if (ctx.Gameplay) ctx.Gameplay->OnMeshDelta(v);
}

// =========================================================
// Process functions — Mesh (orchestration)
// =========================================================

inline EDispatchResult ProcessMeshStart(
    const livesync::DeserializedMessage& msg,
    const DispatchContext& ctx)
{
#ifdef UELIVESYNC_BRIDGE_TESTING
    g_meshstart_calls++;
#endif
    auto view = BuildMeshStartView(msg);
    DispatchMeshStart(view, ctx);
    return EDispatchResult::Handled;
}

inline EDispatchResult ProcessMeshChunk(
    const livesync::DeserializedMessage& msg,
    const DispatchContext& ctx)
{
#ifdef UELIVESYNC_BRIDGE_TESTING
    g_meshchunk_calls++;
#endif
    auto view = BuildMeshChunkView(msg);
    DispatchMeshChunk(view, ctx);
    return EDispatchResult::Handled;
}

inline EDispatchResult ProcessMeshEnd(
    const livesync::DeserializedMessage& msg,
    const DispatchContext& ctx)
{
#ifdef UELIVESYNC_BRIDGE_TESTING
    g_meshend_calls++;
#endif
    auto view = BuildMeshEndView(msg);
    DispatchMeshEnd(view, ctx);
    return EDispatchResult::Handled;
}

inline EDispatchResult ProcessMeshData(
    const livesync::DeserializedMessage& msg,
    const DispatchContext& ctx)
{
#ifdef UELIVESYNC_BRIDGE_TESTING
    g_meshdata_calls++;
#endif
    auto view = BuildMeshDataView(msg);
    DispatchMeshData(view, ctx);
    return EDispatchResult::Handled;
}

inline EDispatchResult ProcessMeshDelta(
    const livesync::DeserializedMessage& msg,
    const DispatchContext& ctx)
{
#ifdef UELIVESYNC_BRIDGE_TESTING
    g_meshdelta_calls++;
#endif
    auto view = BuildMeshDeltaView(msg);
    DispatchMeshDelta(view, ctx);
    return EDispatchResult::Handled;
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
// 4. switch(msg_type) -> ProcessXXX(msg)
// =========================================================

inline EDispatchResult DispatchMsgTypePacket(
    const uint8* Data,
    int32 DataSize,
    const DispatchContext& ctx)
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
                return ProcessHello(msg, ctx);

            case livesync::MsgType::HELLO_ACK:
                return ProcessHelloAck(msg, ctx);

            case livesync::MsgType::HEARTBEAT:
                return ProcessHeartbeat(msg, ctx);

            case livesync::MsgType::HEARTBEAT_ACK:
                return ProcessHeartbeatAck(msg, ctx);

        case livesync::MsgType::OBJECT_CREATE:
            return ProcessObjectCreate(msg, ctx);

        case livesync::MsgType::OBJECT_UPDATE:
            return ProcessObjectUpdate(msg, ctx);

        case livesync::MsgType::OBJECT_DELETE:
            return ProcessObjectDelete(msg, ctx);

        case livesync::MsgType::OBJECT_RENAME:
            return ProcessObjectRename(msg, ctx);

        case livesync::MsgType::OBJECT_VISIBILITY:
            return ProcessObjectVisibility(msg, ctx);

        case livesync::MsgType::OBJECT_REPARENT:
            return ProcessObjectReparent(msg, ctx);

        case livesync::MsgType::MATERIAL_CREATE:
            return ProcessMaterialCreate(msg, ctx);

        case livesync::MsgType::MATERIAL_UPDATE:
            return ProcessMaterialUpdate(msg, ctx);

        case livesync::MsgType::MATERIAL_ASSIGN:
            return ProcessMaterialAssign(msg, ctx);

        case livesync::MsgType::MESH_START:
            return ProcessMeshStart(msg, ctx);

        case livesync::MsgType::MESH_CHUNK:
            return ProcessMeshChunk(msg, ctx);

        case livesync::MsgType::MESH_END:
            return ProcessMeshEnd(msg, ctx);

        case livesync::MsgType::MESH_DATA:
            return ProcessMeshData(msg, ctx);

        case livesync::MsgType::MESH_DELTA:
            return ProcessMeshDelta(msg, ctx);

        case livesync::MsgType::CAMERA_CREATE:
            return ProcessCameraCreate(msg, ctx);

        case livesync::MsgType::CAMERA_UPDATE:
            return ProcessCameraUpdate(msg, ctx);

        case livesync::MsgType::CAMERASETACTIVE:
            return ProcessCameraSetActive(msg, ctx);

        default:
            UE_LOG(LogLiveSync, Log,
                TEXT("[BRIDGE][UNSUPPORTED] MsgType 0x%02x"),
                static_cast<int>(msg.msg_type));
            return EDispatchResult::Unsupported;
    }
}

} // namespace LiveSyncBridge
