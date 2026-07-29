#pragma once

/**
 * LiveSync Protocol — Per-message-type body serialization.
 *
 * Each serialize_body_*() function serializes the body fields for one
 * message type, in YAML definition order.
 *
 * PackFrame() wraps body into complete wire frame: [length][header][body].
 */

#include "livesync_serializer.h"
#include "serializer_utils.h"

namespace livesync {

// ─── Body Serialization Per Message Type ────────────────────────

inline std::vector<uint8_t> serialize_body_hello(
    uint8_t ver_major, uint8_t ver_minor, uint64_t capabilities)
{
    std::vector<uint8_t> body;
    pack_uint8(body, ver_major);
    pack_uint8(body, ver_minor);
    pack_uint64(body, capabilities);
    return body;
}

inline std::vector<uint8_t> serialize_body_hello_ack(
    uint8_t ver_major, uint8_t ver_minor,
    uint64_t accepted_caps, uint32_t max_chunk_size, uint64_t session_id)
{
    std::vector<uint8_t> body;
    pack_uint8(body, ver_major);
    pack_uint8(body, ver_minor);
    pack_uint64(body, accepted_caps);
    pack_uint32(body, max_chunk_size);
    pack_uint64(body, session_id);
    return body;
}

inline std::vector<uint8_t> serialize_body_reject(
    uint16_t error_code, const std::string& reason,
    uint8_t min_ver_major, uint8_t min_ver_minor,
    uint8_t max_ver_major, uint8_t max_ver_minor)
{
    std::vector<uint8_t> body;
    pack_uint16(body, error_code);
    pack_utf8_string(body, reason);
    pack_uint8(body, min_ver_major);
    pack_uint8(body, min_ver_minor);
    pack_uint8(body, max_ver_major);
    pack_uint8(body, max_ver_minor);
    return body;
}

inline std::vector<uint8_t> serialize_body_empty() {
    return {};
}

inline std::vector<uint8_t> serialize_body_scene_hash(uint64_t hash, uint32_t object_count) {
    std::vector<uint8_t> body;
    pack_uint64(body, hash);
    pack_uint32(body, object_count);
    return body;
}

inline std::vector<uint8_t> serialize_body_scene_count(uint32_t object_count) {
    std::vector<uint8_t> body;
    pack_uint32(body, object_count);
    return body;
}

inline std::vector<uint8_t> serialize_body_object_create(
    const std::string& persistent_id, const std::string& name,
    const std::string& parent_id,
    uint8_t primitive_type,
    float px, float py, float pz,
    float rx, float ry, float rz, float rw,
    float sx, float sy, float sz,
    uint32_t sequence_number, double timestamp)
{
    std::vector<uint8_t> body;
    auto pid = parse_uuid(persistent_id);
    pack_uuid(body, pid.data());
    pack_utf8_string(body, name);
    if (!parent_id.empty()) {
        auto parent = parse_uuid(parent_id);
        pack_uuid(body, parent.data());
    }
    pack_uint8(body, primitive_type);
    pack_transform3d(body, px, py, pz, rx, ry, rz, rw, sx, sy, sz);
    pack_uint32(body, sequence_number);
    pack_float64(body, timestamp);
    return body;
}

inline std::vector<uint8_t> serialize_body_object_update(
    const std::string& persistent_id,
    bool has_transform,
    float px, float py, float pz,
    float rx, float ry, float rz, float rw,
    float sx, float sy, float sz,
    bool has_name, const std::string& name,
    bool has_visibility, uint8_t visibility,
    uint32_t sequence_number, double timestamp)
{
    std::vector<uint8_t> body;
    auto pid = parse_uuid(persistent_id);
    pack_uuid(body, pid.data());
    if (has_transform) {
        pack_transform3d(body, px, py, pz, rx, ry, rz, rw, sx, sy, sz);
    }
    if (has_name) {
        pack_utf8_string(body, name);
    }
    if (has_visibility) {
        pack_uint8(body, visibility);
    }
    pack_uint32(body, sequence_number);
    pack_float64(body, timestamp);
    return body;
}

inline std::vector<uint8_t> serialize_body_object_delete(
    const std::string& persistent_id, uint32_t sequence_number, double timestamp)
{
    std::vector<uint8_t> body;
    auto pid = parse_uuid(persistent_id);
    pack_uuid(body, pid.data());
    pack_uint32(body, sequence_number);
    pack_float64(body, timestamp);
    return body;
}

inline std::vector<uint8_t> serialize_body_object_rename(
    const std::string& persistent_id, const std::string& new_name)
{
    std::vector<uint8_t> body;
    auto pid = parse_uuid(persistent_id);
    pack_uuid(body, pid.data());
    pack_utf8_string(body, new_name);
    return body;
}

inline std::vector<uint8_t> serialize_body_object_reparent(
    const std::string& persistent_id, const std::string& new_parent_id)
{
    std::vector<uint8_t> body;
    auto pid = parse_uuid(persistent_id);
    pack_uuid(body, pid.data());
    auto parent = parse_uuid(new_parent_id);
    pack_uuid(body, parent.data());
    return body;
}

inline std::vector<uint8_t> serialize_body_object_visibility(
    const std::string& persistent_id, uint8_t visible)
{
    std::vector<uint8_t> body;
    auto pid = parse_uuid(persistent_id);
    pack_uuid(body, pid.data());
    pack_uint8(body, visible);
    return body;
}

inline std::vector<uint8_t> serialize_body_camera_create(
    const std::string& camera_id, const std::string& name,
    float px, float py, float pz,
    float rx, float ry, float rz, float rw,
    float sx, float sy, float sz,
    float focal_length, float sensor_width, float sensor_height)
{
    std::vector<uint8_t> body;
    auto cid = parse_uuid(camera_id);
    pack_uuid(body, cid.data());
    pack_utf8_string(body, name);
    pack_transform3d(body, px, py, pz, rx, ry, rz, rw, sx, sy, sz);
    pack_float32(body, focal_length);
    pack_float32(body, sensor_width);
    pack_float32(body, sensor_height);
    return body;
}

inline std::vector<uint8_t> serialize_body_camera_update(
    const std::string& camera_id,
    bool has_transform,
    float px, float py, float pz,
    float rx, float ry, float rz, float rw,
    float sx, float sy, float sz,
    bool has_focal_length, float focal_length,
    bool has_sensor_width, float sensor_width,
    bool has_sensor_height, float sensor_height)
{
    std::vector<uint8_t> body;
    auto cid = parse_uuid(camera_id);
    pack_uuid(body, cid.data());
    if (has_transform) {
        pack_transform3d(body, px, py, pz, rx, ry, rz, rw, sx, sy, sz);
    }
    if (has_focal_length) {
        pack_float32(body, focal_length);
    }
    if (has_sensor_width) {
        pack_float32(body, sensor_width);
    }
    if (has_sensor_height) {
        pack_float32(body, sensor_height);
    }
    return body;
}

inline std::vector<uint8_t> serialize_body_camera_setactive(
    const std::string& camera_id)
{
    std::vector<uint8_t> body;
    auto cid = parse_uuid(camera_id);
    pack_uuid(body, cid.data());
    return body;
}

inline std::vector<uint8_t> serialize_body_material_create(
    const std::string& material_id, const std::string& name,
    float bc_r, float bc_g, float bc_b, float bc_a,
    float metallic, float roughness,
    float em_r, float em_g, float em_b,
    const std::string& texture_path)
{
    std::vector<uint8_t> body;
    auto mid = parse_uuid(material_id);
    pack_uuid(body, mid.data());
    pack_utf8_string(body, name);
    pack_float32(body, bc_r);
    pack_float32(body, bc_g);
    pack_float32(body, bc_b);
    pack_float32(body, bc_a);
    pack_float32(body, metallic);
    pack_float32(body, roughness);
    pack_float32(body, em_r);
    pack_float32(body, em_g);
    pack_float32(body, em_b);
    if (!texture_path.empty()) {
        pack_utf8_string(body, texture_path);
    }
    return body;
}

inline std::vector<uint8_t> serialize_body_material_update(
    const std::string& material_id,
    bool has_base_color, float bc_r, float bc_g, float bc_b, float bc_a,
    bool has_metallic, float metallic,
    bool has_roughness, float roughness,
    bool has_emission, float em_r, float em_g, float em_b,
    bool has_texture_path, const std::string& texture_path)
{
    std::vector<uint8_t> body;
    auto mid = parse_uuid(material_id);
    pack_uuid(body, mid.data());
    if (has_base_color) {
        pack_float32(body, bc_r);
        pack_float32(body, bc_g);
        pack_float32(body, bc_b);
        pack_float32(body, bc_a);
    }
    if (has_metallic) {
        pack_float32(body, metallic);
    }
    if (has_roughness) {
        pack_float32(body, roughness);
    }
    if (has_emission) {
        pack_float32(body, em_r);
        pack_float32(body, em_g);
        pack_float32(body, em_b);
    }
    if (has_texture_path) {
        pack_utf8_string(body, texture_path);
    }
    return body;
}

inline std::vector<uint8_t> serialize_body_material_assign(
    const std::string& persistent_id, const std::string& material_id,
    uint8_t slot_index)
{
    std::vector<uint8_t> body;
    auto pid = parse_uuid(persistent_id);
    pack_uuid(body, pid.data());
    auto mid = parse_uuid(material_id);
    pack_uuid(body, mid.data());
    pack_uint8(body, slot_index);
    return body;
}

inline std::vector<uint8_t> serialize_body_mesh_start(
    const std::string& persistent_id, uint16_t total_chunks,
    uint8_t format_flags)
{
    std::vector<uint8_t> body;
    auto pid = parse_uuid(persistent_id);
    pack_uuid(body, pid.data());
    pack_uint16(body, total_chunks);
    pack_uint8(body, format_flags);
    return body;
}

inline std::vector<uint8_t> serialize_body_mesh_chunk(
    const std::string& persistent_id, uint16_t chunk_index,
    uint16_t vertex_offset, uint32_t vertex_count,
    uint32_t index_count, std::span<const uint8_t> data)
{
    std::vector<uint8_t> body;
    auto pid = parse_uuid(persistent_id);
    pack_uuid(body, pid.data());
    pack_uint16(body, chunk_index);
    pack_uint16(body, vertex_offset);
    pack_uint32(body, vertex_count);
    pack_uint32(body, index_count);
    pack_raw_bytes(body, data);
    return body;
}

inline std::vector<uint8_t> serialize_body_mesh_end(
    const std::string& persistent_id, uint32_t checksum)
{
    std::vector<uint8_t> body;
    auto pid = parse_uuid(persistent_id);
    pack_uuid(body, pid.data());
    pack_uint32(body, checksum);
    return body;
}

inline std::vector<uint8_t> serialize_body_mesh_data(
    const std::string& persistent_id,
    uint32_t vertex_count, uint32_t index_count,
    uint8_t format_flags,
    std::span<const float> vertices,
    std::span<const float> normals,
    std::span<const float> uvs,
    std::span<const uint32_t> indices)
{
    std::vector<uint8_t> body;
    auto pid = parse_uuid(persistent_id);
    pack_uuid(body, pid.data());
    pack_uint32(body, vertex_count);
    pack_uint32(body, index_count);
    pack_uint8(body, format_flags);
    pack_f32_array(body, vertices);
    pack_f32_array(body, normals);
    pack_f32_array(body, uvs);
    pack_u32_array(body, indices);
    return body;
}

inline std::vector<uint8_t> serialize_body_mesh_delta(
    const std::string& persistent_id,
    uint32_t vertex_count,
    uint8_t format_flags,
    std::span<const float> vertices,
    std::span<const float> normals,
    std::span<const float> uvs)
{
    std::vector<uint8_t> body;
    auto pid = parse_uuid(persistent_id);
    pack_uuid(body, pid.data());
    pack_uint32(body, vertex_count);
    pack_uint8(body, format_flags);
    pack_f32_array(body, vertices);
    pack_f32_array(body, normals);
    pack_f32_array(body, uvs);
    return body;
}

inline std::vector<uint8_t> serialize_body_sync_ack(uint32_t acked_seq) {
    std::vector<uint8_t> body;
    pack_uint32(body, acked_seq);
    return body;
}

inline std::vector<uint8_t> serialize_body_error(uint16_t error_code, const std::string& message) {
    std::vector<uint8_t> body;
    pack_uint16(body, error_code);
    pack_utf8_string(body, message);
    return body;
}

// ─── Frame Assembly ─────────────────────────────────────────────

/// Wrap body into complete wire frame: [4-byte LE length][header][body].
/// This is NOT a message serializer — it frames an already-serialized body.
inline std::vector<uint8_t> PackFrame(
    MsgType msg_type,
    uint8_t flags,
    uint32_t sequence_id,
    const std::optional<uint64_t>& session_id,
    std::span<const uint8_t> body)
{
    return pack_frame(msg_type, flags, sequence_id, session_id, body);
}

} // namespace livesync
