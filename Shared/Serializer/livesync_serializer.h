#pragma once

/**
 * LiveSync Protocol Serializer — Primitives and frame layout.
 *
 * Wire format: [4-byte LE length][header][body]
 * All multi-byte fields are little-endian.
 *
 * This header provides the building blocks for message serialization.
 * Per-message-type serialization is built on top of these primitives.
 */

#include <cstdint>
#include <cstring>
#include <cmath>
#include <vector>
#include <optional>
#include <string>
#include <span>
#include <stdexcept>

namespace livesync {

// ─── Message Opcodes ────────────────────────────────────────────

enum class MsgType : uint8_t {
    HEARTBEAT       = 0x00,
    HEARTBEAT_ACK   = 0x01,
    SCENE_HASH      = 0x02,
    SCENE_FULL      = 0x03,
    SCENE_DELTA     = 0x04,
    OBJECT_CREATE   = 0x20,
    OBJECT_UPDATE   = 0x21,
    OBJECT_DELETE   = 0x22,
    OBJECT_RENAME   = 0x23,
    OBJECT_REPARENT = 0x24,
    OBJECT_VISIBILITY = 0x25,
    OBJECT_ASSET_IDENTITY = 0x26,
    MESH_DATA       = 0x30,
    MESH_DELTA      = 0x31,
    MESH_START      = 0x32,
    MESH_CHUNK      = 0x33,
    MESH_END        = 0x34,
    MATERIAL_CREATE = 0x40,
    MATERIAL_UPDATE = 0x41,
    MATERIAL_ASSIGN = 0x42,
    CAMERA_CREATE   = 0x50,
    CAMERA_UPDATE   = 0x51,
    CAMERASETACTIVE = 0x52,
    FBX_IMPORT_REQUEST = 0x60,
    HELLO           = 0x10,
    HELLO_ACK       = 0x11,
    REJECT          = 0x12,
    SYNC_ACK        = 0xF0,
    ERROR           = 0xFE,
    DISCONNECT      = 0xFF,
};

// ─── Header Constants ───────────────────────────────────────────

constexpr size_t HEADER_BEFORE_SESSION_SIZE = 6;
constexpr size_t HEADER_AFTER_SESSION_SIZE  = 14;
constexpr size_t LENGTH_PREFIX_SIZE         = 4;

inline bool is_pre_session(MsgType t) {
    return t == MsgType::HELLO || t == MsgType::HELLO_ACK || t == MsgType::REJECT;
}

// ─── Float Canonicalization ─────────────────────────────────────

inline float canonicalize_float(float v) {
    if (v != v) {
        throw std::invalid_argument("NaN value rejected by canonical float rules");
    }
    if (v == 0.0f) {
        return 0.0f;
    }
    return v;
}

inline void canonicalize_quaternion(float& x, float& y, float& z, float& w) {
    double dx = x, dy = y, dz = z, dw = w;
    double mag = std::sqrt(dx*dx + dy*dy + dz*dz + dw*dw);
    if (mag < 1e-7) {
        x = 0.0f; y = 0.0f; z = 0.0f; w = 1.0f;
        return;
    }
    x = canonicalize_float(static_cast<float>(dx / mag));
    y = canonicalize_float(static_cast<float>(dy / mag));
    z = canonicalize_float(static_cast<float>(dz / mag));
    w = canonicalize_float(static_cast<float>(dw / mag));
}

// ─── Primitive Packers ──────────────────────────────────────────

inline void pack_uint8(std::vector<uint8_t>& out, uint8_t v) {
    out.push_back(v);
}

inline void pack_uint16(std::vector<uint8_t>& out, uint16_t v) {
    out.push_back(static_cast<uint8_t>(v & 0xFF));
    out.push_back(static_cast<uint8_t>((v >> 8) & 0xFF));
}

inline void pack_uint32(std::vector<uint8_t>& out, uint32_t v) {
    out.push_back(static_cast<uint8_t>(v & 0xFF));
    out.push_back(static_cast<uint8_t>((v >> 8) & 0xFF));
    out.push_back(static_cast<uint8_t>((v >> 16) & 0xFF));
    out.push_back(static_cast<uint8_t>((v >> 24) & 0xFF));
}

inline void pack_uint64(std::vector<uint8_t>& out, uint64_t v) {
    for (int i = 0; i < 8; ++i) {
        out.push_back(static_cast<uint8_t>((v >> (i * 8)) & 0xFF));
    }
}

inline void pack_float32(std::vector<uint8_t>& out, float v) {
    float c = canonicalize_float(v);
    uint32_t bits;
    std::memcpy(&bits, &c, sizeof(bits));
    pack_uint32(out, bits);
}

inline void pack_float64(std::vector<uint8_t>& out, double v) {
    uint64_t bits;
    std::memcpy(&bits, &v, sizeof(bits));
    pack_uint64(out, bits);
}

// ─── Composite Packers ──────────────────────────────────────────

inline void pack_uuid(std::vector<uint8_t>& out, const uint8_t bytes[16]) {
    out.insert(out.end(), bytes, bytes + 16);
}

// FGuid (mixed-endian) LE layout — mirrors the production Object-GUID channel
// (Blender_Addon/protocol_guid.py:uuid_to_fguid_bytes).  Each 4-byte group of the
// RFC 4122 bytes is stored as a little-endian uint32 (A,B,C,D).  Only
// Object-GUID reference fields must use this layout (MIG-006); material-namespace
// uuids stay RFC via pack_uuid().
inline void pack_uuid_fguid(std::vector<uint8_t>& out, const uint8_t bytes[16]) {
    for (int i = 0; i < 16; i += 4) {
        out.push_back(bytes[i + 3]);
        out.push_back(bytes[i + 2]);
        out.push_back(bytes[i + 1]);
        out.push_back(bytes[i]);
    }
}

inline void pack_transform3d(
    std::vector<uint8_t>& out,
    float px, float py, float pz,
    float rx, float ry, float rz, float rw,
    float sx, float sy, float sz)
{
    canonicalize_quaternion(rx, ry, rz, rw);
    pack_float32(out, px);
    pack_float32(out, py);
    pack_float32(out, pz);
    pack_float32(out, rx);
    pack_float32(out, ry);
    pack_float32(out, rz);
    pack_float32(out, rw);
    pack_float32(out, sx);
    pack_float32(out, sy);
    pack_float32(out, sz);
}

inline void pack_utf8_string(std::vector<uint8_t>& out, const std::string& v) {
    if (v.size() > 0xFFFF) {
        throw std::invalid_argument("String too long");
    }
    pack_uint16(out, static_cast<uint16_t>(v.size()));
    out.insert(out.end(), v.begin(), v.end());
}

inline void pack_f32_array(std::vector<uint8_t>& out, std::span<const float> values) {
    for (float v : values) {
        pack_float32(out, v);
    }
}

inline void pack_u32_array(std::vector<uint8_t>& out, std::span<const uint32_t> values) {
    pack_uint32(out, static_cast<uint32_t>(values.size()));
    for (uint32_t v : values) {
        pack_uint32(out, v);
    }
}

inline void pack_raw_bytes(std::vector<uint8_t>& out, std::span<const uint8_t> data) {
    pack_uint32(out, static_cast<uint32_t>(data.size()));
    out.insert(out.end(), data.begin(), data.end());
}

// ─── Header + Frame ─────────────────────────────────────────────

inline void pack_header(
    std::vector<uint8_t>& out,
    MsgType msg_type,
    uint8_t flags,
    uint32_t sequence_id,
    const std::optional<uint64_t>& session_id)
{
    pack_uint8(out, static_cast<uint8_t>(msg_type));
    pack_uint8(out, flags);
    pack_uint32(out, sequence_id);

    if (is_pre_session(msg_type)) {
        if (session_id.has_value()) {
            throw std::invalid_argument("Pre-session message must not contain SessionId");
        }
    } else {
        if (!session_id.has_value()) {
            throw std::invalid_argument("Post-session message must contain SessionId");
        }
        pack_uint64(out, session_id.value());
    }
}

inline std::vector<uint8_t> pack_frame(
    MsgType msg_type,
    uint8_t flags,
    uint32_t sequence_id,
    const std::optional<uint64_t>& session_id,
    std::span<const uint8_t> body)
{
    size_t header_size = is_pre_session(msg_type)
        ? HEADER_BEFORE_SESSION_SIZE
        : HEADER_AFTER_SESSION_SIZE;

    std::vector<uint8_t> out;
    out.reserve(LENGTH_PREFIX_SIZE + header_size + body.size());

    uint32_t payload_length = static_cast<uint32_t>(header_size + body.size());
    pack_uint32(out, payload_length);
    pack_header(out, msg_type, flags, sequence_id, session_id);
    out.insert(out.end(), body.begin(), body.end());

    return out;
}

} // namespace livesync
