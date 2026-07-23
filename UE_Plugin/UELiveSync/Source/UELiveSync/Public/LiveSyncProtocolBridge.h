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

inline void ResetAllCounters()
{
    g_hello_calls = 0;
    g_helloack_calls = 0;
    g_heartbeat_calls = 0;
    g_heartbeatack_calls = 0;
    g_objectcreate_calls = 0;
    g_objectupdate_calls = 0;
    g_objectdelete_calls = 0;
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
// Handlers — Phase 1.3.2b (object lifecycle)
// =========================================================
// Deserialize → Validate → Log. No actor spawn/destroy/modify.
// =========================================================

inline void HandleObjectCreate(const livesync::DeserializedMessage& msg)
{
#ifdef UELIVESYNC_BRIDGE_TESTING
    g_objectcreate_calls++;
#endif

    char id_str[37];
    auto& pid = std::get<std::array<uint8_t, 16>>(
        msg.body.at("persistent_id"));
    FormatUuid(pid, id_str, sizeof(id_str));

    const std::string& name = std::get<std::string>(
        msg.body.at("name"));

    char parent_str[37] = "none";
    auto pit = msg.body.find("parent_id");
    if (pit != msg.body.end())
    {
        FormatUuid(std::get<std::array<uint8_t, 16>>(pit->second),
            parent_str, sizeof(parent_str));
    }

    auto& tf = std::get<std::vector<float>>(
        msg.body.at("transform"));

    UE_LOG(LogLiveSync, Log,
        TEXT("[BRIDGE][OBJECT_CREATE] id=%hs name=%hs "
             "parent=%hs transform=[%.2f,%.2f,%.2f,...]"),
        id_str, name.c_str(), parent_str,
        tf.size() >= 3 ? tf[0] : 0.f,
        tf.size() >= 3 ? tf[1] : 0.f,
        tf.size() >= 3 ? tf[2] : 0.f);
}

inline void HandleObjectUpdate(const livesync::DeserializedMessage& msg)
{
#ifdef UELIVESYNC_BRIDGE_TESTING
    g_objectupdate_calls++;
#endif

    char id_str[37];
    auto& pid = std::get<std::array<uint8_t, 16>>(
        msg.body.at("persistent_id"));
    FormatUuid(pid, id_str, sizeof(id_str));

    auto it_t = msg.body.find("transform");
    if (it_t != msg.body.end())
    {
        auto& tf = std::get<std::vector<float>>(it_t->second);
        UE_LOG(LogLiveSync, Log,
            TEXT("[BRIDGE][OBJECT_UPDATE] id=%hs "
                 "transform=[%.2f,%.2f,%.2f,...]"),
            id_str,
            tf.size() >= 3 ? tf[0] : 0.f,
            tf.size() >= 3 ? tf[1] : 0.f,
            tf.size() >= 3 ? tf[2] : 0.f);
    }
    else
    {
        UE_LOG(LogLiveSync, Log,
            TEXT("[BRIDGE][OBJECT_UPDATE] id=%hs (no transform)"),
            id_str);
    }
}

inline void HandleObjectDelete(const livesync::DeserializedMessage& msg)
{
#ifdef UELIVESYNC_BRIDGE_TESTING
    g_objectdelete_calls++;
#endif

    char id_str[37];
    auto& pid = std::get<std::array<uint8_t, 16>>(
        msg.body.at("persistent_id"));
    FormatUuid(pid, id_str, sizeof(id_str));

    UE_LOG(LogLiveSync, Log,
        TEXT("[BRIDGE][OBJECT_DELETE] id=%hs"), id_str);
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
            HandleObjectCreate(msg);
            return EDispatchResult::Handled;

        case livesync::MsgType::OBJECT_UPDATE:
            HandleObjectUpdate(msg);
            return EDispatchResult::Handled;

        case livesync::MsgType::OBJECT_DELETE:
            HandleObjectDelete(msg);
            return EDispatchResult::Handled;

        default:
            UE_LOG(LogLiveSync, Log,
                TEXT("[BRIDGE][UNSUPPORTED] MsgType 0x%02x"),
                static_cast<int>(msg.msg_type));
            return EDispatchResult::Unsupported;
    }
}

} // namespace LiveSyncBridge
