#pragma once

/**
 * LiveSync Protocol — Binary deserializer.
 *
 * Deserializes wire-format bytes into message structs.
 * Mirrors the Python deserializer in Tests/Protocol/serializer/deserializer.py.
 */

#include "livesync_serializer.h"
#include "serializer_utils.h"
#include <variant>
#include <unordered_map>
#include <stdexcept>

namespace livesync {

// ─── Deserialized Message ───────────────────────────────────────

struct DeserializedMessage {
    MsgType msg_type;
    uint8_t flags;
    uint32_t sequence_id;
    std::optional<uint64_t> session_id;
    std::unordered_map<std::string, std::variant<
        uint8_t, uint16_t, uint32_t, uint64_t,
        float, double, std::string,
        std::array<uint8_t, 16>,
        std::vector<float>,
        std::vector<uint32_t>,
        std::vector<uint8_t>
    >> body;
    size_t total_size; // length_prefix + header + body
};

// ─── Unpack Primitives ──────────────────────────────────────────

struct UnpackState {
    const uint8_t* data;
    size_t size;
    size_t offset;

    UnpackState(const uint8_t* data, size_t size)
        : data(data), size(size), offset(0) {}

    uint8_t unpack_uint8() {
        if (offset + 1 > size) throw std::runtime_error("Truncated uint8");
        uint8_t v = data[offset];
        offset += 1;
        return v;
    }

    uint16_t unpack_uint16() {
        if (offset + 2 > size) throw std::runtime_error("Truncated uint16");
        uint16_t v;
        std::memcpy(&v, data + offset, 2);
        offset += 2;
        return v;
    }

    uint32_t unpack_uint32() {
        if (offset + 4 > size) throw std::runtime_error("Truncated uint32");
        uint32_t v;
        std::memcpy(&v, data + offset, 4);
        offset += 4;
        return v;
    }

    uint64_t unpack_uint64() {
        if (offset + 8 > size) throw std::runtime_error("Truncated uint64");
        uint64_t v;
        std::memcpy(&v, data + offset, 8);
        offset += 8;
        return v;
    }

    float unpack_float32() {
        if (offset + 4 > size) throw std::runtime_error("Truncated float32");
        float v;
        std::memcpy(&v, data + offset, 4);
        offset += 4;
        return v;
    }

    double unpack_float64() {
        if (offset + 8 > size) throw std::runtime_error("Truncated float64");
        double v;
        std::memcpy(&v, data + offset, 8);
        offset += 8;
        return v;
    }

    std::array<uint8_t, 16> unpack_uuid() {
        if (offset + 16 > size) throw std::runtime_error("Truncated UUID");
        std::array<uint8_t, 16> v;
        std::memcpy(v.data(), data + offset, 16);
        offset += 16;
        return v;
    }

    std::string unpack_utf8_string() {
        uint16_t len = unpack_uint16();
        if (offset + len > size) throw std::runtime_error("Truncated UTF-8 string");
        std::string v(reinterpret_cast<const char*>(data + offset), len);
        offset += len;
        return v;
    }

    std::vector<float> unpack_transform3d() {
        if (offset + 40 > size) throw std::runtime_error("Truncated transform3d");
        std::vector<float> v(10);
        std::memcpy(v.data(), data + offset, 40);
        offset += 40;
        return v;
    }

    std::vector<float> unpack_f32_array(size_t count) {
        size_t bytes = count * 4;
        if (offset + bytes > size) throw std::runtime_error("Truncated f32_array");
        std::vector<float> v(count);
        std::memcpy(v.data(), data + offset, bytes);
        offset += bytes;
        return v;
    }

    std::vector<uint32_t> unpack_u32_array(size_t count) {
        size_t bytes = count * 4;
        if (offset + bytes > size) throw std::runtime_error("Truncated u32_array");
        std::vector<uint32_t> v(count);
        std::memcpy(v.data(), data + offset, bytes);
        offset += bytes;
        return v;
    }

    std::vector<uint8_t> unpack_raw_bytes() {
        uint32_t len = unpack_uint32();
        if (offset + len > size) throw std::runtime_error("Truncated raw_bytes");
        std::vector<uint8_t> v(data + offset, data + offset + len);
        offset += len;
        return v;
    }
};

// ─── Header Deserialization ─────────────────────────────────────

inline void deserialize_header(UnpackState& state, MsgType msg_type,
                               uint8_t& flags, uint32_t& sequence_id,
                               std::optional<uint64_t>& session_id) {
    flags = state.unpack_uint8();
    sequence_id = state.unpack_uint32();
    session_id = std::nullopt;
    if (!is_pre_session(msg_type)) {
        session_id = state.unpack_uint64();
    }
}

// ─── Body Deserialization (per message type) ────────────────────

inline void deserialize_body_hello(UnpackState& s, DeserializedMessage& msg) {
    msg.body["protocol_version_major"] = s.unpack_uint8();
    msg.body["protocol_version_minor"] = s.unpack_uint8();
    msg.body["capabilities"] = s.unpack_uint64();
}

inline void deserialize_body_hello_ack(UnpackState& s, DeserializedMessage& msg) {
    msg.body["protocol_version_major"] = s.unpack_uint8();
    msg.body["protocol_version_minor"] = s.unpack_uint8();
    msg.body["accepted_capabilities"] = s.unpack_uint64();
    msg.body["max_chunk_size"] = s.unpack_uint32();
    msg.body["session_id"] = s.unpack_uint64();
}

inline void deserialize_body_reject(UnpackState& s, DeserializedMessage& msg) {
    msg.body["error_code"] = s.unpack_uint16();
    msg.body["reason"] = s.unpack_utf8_string();
    msg.body["min_version_major"] = s.unpack_uint8();
    msg.body["min_version_minor"] = s.unpack_uint8();
    msg.body["max_version_major"] = s.unpack_uint8();
    msg.body["max_version_minor"] = s.unpack_uint8();
}

inline void deserialize_body_scene_hash(UnpackState& s, DeserializedMessage& msg) {
    msg.body["hash"] = s.unpack_uint64();
    msg.body["object_count"] = s.unpack_uint32();
}

inline void deserialize_body_scene_count(UnpackState& s, DeserializedMessage& msg) {
    msg.body["object_count"] = s.unpack_uint32();
}

inline void deserialize_body_object_create(UnpackState& s, DeserializedMessage& msg,
                                            size_t body_end) {
    msg.body["persistent_id"] = s.unpack_uuid();
    msg.body["name"] = s.unpack_utf8_string();
    // parent_id is optional — if 69+ bytes remain (16 uuid + 1 uint8 + 40 transform + 4 uint32 + 8 float64), read it
    if (body_end - s.offset >= 69) {
        msg.body["parent_id"] = s.unpack_uuid();
    }
    msg.body["primitive_type"] = s.unpack_uint8();
    msg.body["transform"] = s.unpack_transform3d();
    msg.body["sequence_number"] = s.unpack_uint32();
    msg.body["timestamp"] = s.unpack_float64();
}

inline void deserialize_body_object_update(UnpackState& s, DeserializedMessage& msg) {
    msg.body["persistent_id"] = s.unpack_uuid();
    // Optional fields — read based on what's available
    // This is tricky for the generic deserializer
    // For now, read all fields (caller checks which are present)
    msg.body["transform"] = s.unpack_transform3d();
    msg.body["name"] = s.unpack_utf8_string();
    msg.body["visibility"] = s.unpack_uint8();
    msg.body["sequence_number"] = s.unpack_uint32();
    msg.body["timestamp"] = s.unpack_float64();
}

inline void deserialize_body_object_delete(UnpackState& s, DeserializedMessage& msg) {
    msg.body["persistent_id"] = s.unpack_uuid();
    msg.body["sequence_number"] = s.unpack_uint32();
    msg.body["timestamp"] = s.unpack_float64();
}

inline void deserialize_body_object_rename(UnpackState& s, DeserializedMessage& msg) {
    msg.body["persistent_id"] = s.unpack_uuid();
    msg.body["new_name"] = s.unpack_utf8_string();
}

inline void deserialize_body_object_reparent(UnpackState& s, DeserializedMessage& msg) {
    msg.body["persistent_id"] = s.unpack_uuid();
    msg.body["new_parent_id"] = s.unpack_uuid();
}

inline void deserialize_body_object_visibility(UnpackState& s, DeserializedMessage& msg) {
    msg.body["persistent_id"] = s.unpack_uuid();
    msg.body["visible"] = s.unpack_uint8();
}

inline void deserialize_body_sync_ack(UnpackState& s, DeserializedMessage& msg) {
    msg.body["acked_seq"] = s.unpack_uint32();
}

inline void deserialize_body_error(UnpackState& s, DeserializedMessage& msg) {
    msg.body["error_code"] = s.unpack_uint16();
    msg.body["message"] = s.unpack_utf8_string();
}

// ─── Frame Deserialization ──────────────────────────────────────

/// Deserialize a complete wire frame from raw bytes.
/// Input: [4-byte LE length][header][body]
/// Returns: DeserializedMessage with all fields populated.
inline DeserializedMessage DeserializeFrame(const uint8_t* data, size_t size) {
    UnpackState state(data, size);

    // Read length prefix
    uint32_t payload_length = state.unpack_uint32();

    // Read MsgType
    uint8_t msg_type_val = state.unpack_uint8();
    MsgType msg_type;
    try {
        msg_type = static_cast<MsgType>(msg_type_val);
    } catch (...) {
        throw std::runtime_error("Invalid message opcode: 0x" +
            std::to_string(msg_type_val));
    }

    // Read rest of header
    uint8_t flags;
    uint32_t sequence_id;
    std::optional<uint64_t> session_id;
    deserialize_header(state, msg_type, flags, sequence_id, session_id);

    // Validate header invariant
    if (is_pre_session(msg_type) && session_id.has_value()) {
        throw std::runtime_error("Pre-session message contains SessionId");
    }

    // Create message
    DeserializedMessage msg;
    msg.msg_type = msg_type;
    msg.flags = flags;
    msg.sequence_id = sequence_id;
    msg.session_id = session_id;
    msg.total_size = 4 + payload_length;

    // Dispatch to body deserializer
    switch (msg_type) {
        case MsgType::HELLO:
            deserialize_body_hello(state, msg);
            break;
        case MsgType::HELLO_ACK:
            deserialize_body_hello_ack(state, msg);
            break;
        case MsgType::REJECT:
            deserialize_body_reject(state, msg);
            break;
        case MsgType::HEARTBEAT:
        case MsgType::HEARTBEAT_ACK:
        case MsgType::DISCONNECT:
            break; // empty body
        case MsgType::SCENE_HASH:
            deserialize_body_scene_hash(state, msg);
            break;
        case MsgType::SCENE_FULL:
        case MsgType::SCENE_DELTA:
            deserialize_body_scene_count(state, msg);
            break;
        case MsgType::OBJECT_CREATE:
            deserialize_body_object_create(state, msg, 4 + payload_length);
            break;
        case MsgType::OBJECT_UPDATE:
            deserialize_body_object_update(state, msg);
            break;
        case MsgType::OBJECT_DELETE:
            deserialize_body_object_delete(state, msg);
            break;
        case MsgType::OBJECT_RENAME:
            deserialize_body_object_rename(state, msg);
            break;
        case MsgType::OBJECT_REPARENT:
            deserialize_body_object_reparent(state, msg);
            break;
        case MsgType::OBJECT_VISIBILITY:
            deserialize_body_object_visibility(state, msg);
            break;
        case MsgType::MESH_START:
            msg.body["persistent_id"] = state.unpack_uuid();
            msg.body["total_chunks"] = state.unpack_uint16();
            msg.body["format_flags"] = state.unpack_uint8();
            break;
        case MsgType::MESH_CHUNK:
            msg.body["persistent_id"] = state.unpack_uuid();
            msg.body["chunk_index"] = state.unpack_uint16();
            msg.body["vertex_offset"] = state.unpack_uint16();
            msg.body["vertex_count"] = state.unpack_uint32();
            msg.body["index_count"] = state.unpack_uint32();
            msg.body["data"] = state.unpack_raw_bytes();
            break;
        case MsgType::MESH_END:
            msg.body["persistent_id"] = state.unpack_uuid();
            msg.body["checksum"] = state.unpack_uint32();
            break;
        case MsgType::MESH_DATA:
            msg.body["persistent_id"] = state.unpack_uuid();
            msg.body["vertex_count"] = state.unpack_uint32();
            msg.body["index_count"] = state.unpack_uint32();
            msg.body["format_flags"] = state.unpack_uint8();
            {
                uint32_t vc = std::get<uint32_t>(msg.body["vertex_count"]);
                uint32_t ic = std::get<uint32_t>(msg.body["index_count"]);
                msg.body["vertices"] = state.unpack_f32_array(vc * 3);
                msg.body["normals"] = state.unpack_f32_array(vc * 3);
                msg.body["uvs"] = state.unpack_f32_array(vc * 2);
                // u32_array has uint32 length prefix
                uint32_t idx_len = state.unpack_uint32();
                msg.body["indices"] = state.unpack_u32_array(idx_len);
            }
            break;
        case MsgType::MESH_DELTA:
            msg.body["persistent_id"] = state.unpack_uuid();
            msg.body["vertex_count"] = state.unpack_uint32();
            msg.body["format_flags"] = state.unpack_uint8();
            {
                uint32_t vc = std::get<uint32_t>(msg.body["vertex_count"]);
                msg.body["vertices"] = state.unpack_f32_array(vc * 3);
                msg.body["normals"] = state.unpack_f32_array(vc * 3);
                msg.body["uvs"] = state.unpack_f32_array(vc * 2);
            }
            break;
        case MsgType::MATERIAL_CREATE:
            msg.body["material_id"] = state.unpack_uuid();
            msg.body["name"] = state.unpack_utf8_string();
            msg.body["base_color"] = state.unpack_f32_array(4);
            msg.body["metallic"] = state.unpack_float32();
            msg.body["roughness"] = state.unpack_float32();
            msg.body["emission"] = state.unpack_f32_array(3);
            // texture_path is optional
            if (state.offset < msg.total_size) {
                msg.body["texture_path"] = state.unpack_utf8_string();
            }
            break;
        case MsgType::MATERIAL_UPDATE:
            msg.body["material_id"] = state.unpack_uuid();
            msg.body["base_color"] = state.unpack_f32_array(4);
            msg.body["metallic"] = state.unpack_float32();
            msg.body["roughness"] = state.unpack_float32();
            msg.body["emission"] = state.unpack_f32_array(3);
            // texture_path is optional — read if data remains
            if (state.offset < msg.total_size) {
                msg.body["texture_path"] = state.unpack_utf8_string();
            }
            break;
        case MsgType::MATERIAL_ASSIGN:
            msg.body["persistent_id"] = state.unpack_uuid();
            msg.body["material_id"] = state.unpack_uuid();
            msg.body["slot_index"] = state.unpack_uint8();
            break;
        case MsgType::CAMERA_CREATE: {
            msg.body["camera_id"] = state.unpack_uuid();
            msg.body["name"] = state.unpack_utf8_string();
            // parent_id is optional. Remaining bytes after name with parent_id:
            // 16 (uuid) + 40 (transform) + 24 (6 floats) + 1 (flags) + 4 (seq) + 8 (ts) = 93
            // Without parent_id: 77
            const size_t body_end = msg.total_size;
            if (body_end - state.offset >= 93) {
                msg.body["parent_id"] = state.unpack_uuid();
            }
            msg.body["transform"] = state.unpack_transform3d();
            msg.body["focal_length"] = state.unpack_float32();
            msg.body["sensor_width"] = state.unpack_float32();
            msg.body["sensor_height"] = state.unpack_float32();
            msg.body["clip_start"] = state.unpack_float32();
            msg.body["clip_end"] = state.unpack_float32();
            msg.body["ortho_scale"] = state.unpack_float32();
            msg.body["camera_flags"] = state.unpack_uint8();
            msg.body["sequence_number"] = state.unpack_uint32();
            msg.body["timestamp"] = state.unpack_float64();
            break;
        }
        case MsgType::CAMERA_UPDATE:
            msg.body["camera_id"] = state.unpack_uuid();
            msg.body["transform"] = state.unpack_transform3d();
            msg.body["focal_length"] = state.unpack_float32();
            msg.body["sensor_width"] = state.unpack_float32();
            msg.body["sensor_height"] = state.unpack_float32();
            msg.body["clip_start"] = state.unpack_float32();
            msg.body["clip_end"] = state.unpack_float32();
            msg.body["ortho_scale"] = state.unpack_float32();
            msg.body["camera_flags"] = state.unpack_uint8();
            msg.body["sequence_number"] = state.unpack_uint32();
            msg.body["timestamp"] = state.unpack_float64();
            break;
        case MsgType::CAMERASETACTIVE:
            msg.body["camera_id"] = state.unpack_uuid();
            break;
        case MsgType::SYNC_ACK:
            msg.body["acked_seq"] = state.unpack_uint32();
            break;
        case MsgType::ERROR:
            msg.body["error_code"] = state.unpack_uint16();
            msg.body["message"] = state.unpack_utf8_string();
            break;
        default:
            throw std::runtime_error("No body deserializer for MsgType::0x" +
                std::to_string(static_cast<int>(msg_type)));
    }

    return msg;
}

} // namespace livesync
