/**
 * LiveSync Protocol — Cross-language verification helper.
 *
 * Reads golden vector .bin files, deserializes with C++,
 * and outputs JSON for comparison with Python's expected values.
 *
 * Also serializes from manifest fields and outputs .bin for Python to read.
 *
 * Usage:
 *   ./test_cross_language <vectors_dir> [--output-cpp-json <path>] [--output-cpp-bins <dir>]
 */

#include "livesync_serializer.h"
#include "livesync_messages.h"
#include "livesync_deserializer.h"
#include "serializer_utils.h"
#include "third_party/json.hpp"

#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <sstream>
#include <filesystem>
#include <vector>
#include <array>

using namespace livesync;
using json = nlohmann::json;

namespace fs = std::filesystem;

// ─── Helpers ───────────────────────────────────────────────────

static std::string uuid_to_hex(const std::array<uint8_t, 16>& bytes) {
    static const char* hex = "0123456789abcdef";
    std::string result;
    result.reserve(32);
    for (int i = 0; i < 16; i++) {
        result += hex[(bytes[i] >> 4) & 0x0F];
        result += hex[bytes[i] & 0x0F];
    }
    return result;
}

static std::string read_file(const fs::path& path) {
    std::ifstream f(path, std::ios::binary);
    if (!f) return "";
    std::ostringstream ss;
    ss << f.rdbuf();
    return ss.str();
}

// ─── Deserialize to JSON ───────────────────────────────────────

static json deserialize_to_json(const uint8_t* data, size_t size) {
    auto d = DeserializeFrame(data, size);
    json result;
    result["msg_type"] = static_cast<int>(d.msg_type);
    result["flags"] = d.flags;
    result["sequence_id"] = d.sequence_id;
    if (d.session_id.has_value()) {
        result["session_id"] = *d.session_id;
    } else {
        result["session_id"] = nullptr;
    }

    json body = json::object();
    for (auto& [key, val] : d.body) {
        if (std::holds_alternative<uint8_t>(val)) {
            body[key] = std::get<uint8_t>(val);
        } else if (std::holds_alternative<uint16_t>(val)) {
            body[key] = std::get<uint16_t>(val);
        } else if (std::holds_alternative<uint32_t>(val)) {
            body[key] = std::get<uint32_t>(val);
        } else if (std::holds_alternative<uint64_t>(val)) {
            body[key] = std::get<uint64_t>(val);
        } else if (std::holds_alternative<float>(val)) {
            body[key] = std::get<float>(val);
        } else if (std::holds_alternative<std::string>(val)) {
            body[key] = std::get<std::string>(val);
        } else if (std::holds_alternative<std::array<uint8_t, 16>>(val)) {
            body[key] = uuid_to_hex(std::get<std::array<uint8_t, 16>>(val));
        } else if (std::holds_alternative<std::vector<float>>(val)) {
            body[key] = std::get<std::vector<float>>(val);
        } else if (std::holds_alternative<std::vector<uint32_t>>(val)) {
            body[key] = std::get<std::vector<uint32_t>>(val);
        } else if (std::holds_alternative<std::vector<uint8_t>>(val)) {
            auto& bytes = std::get<std::vector<uint8_t>>(val);
            body[key] = json::array();
            for (auto b : bytes) body[key].push_back(b);
        }
    }
    result["body"] = body;
    return result;
}

// ─── Serialize from manifest fields ────────────────────────────
// Reads manifest.json, reconstructs each message, serializes to .bin

static json load_manifest(const fs::path& path) {
    std::ifstream f(path);
    if (!f) throw std::runtime_error("Cannot open: " + path.string());
    json m;
    f >> m;
    return m;
}

static std::vector<uint8_t> parse_python_bytes_literal(const std::string& s) {
    // Parse "b'\\x00\\x01...'" or "b'...'" format
    std::vector<uint8_t> result;
    size_t i = 0;
    // Skip "b'" prefix
    if (s.size() >= 2 && s[0] == 'b' && s[1] == '\'') i = 2;
    else if (s.size() >= 1 && s[0] == 'b') i = 1;

    while (i < s.size()) {
        if (s[i] == '\'' && i + 1 >= s.size()) break;  // closing quote
        if (s[i] == '\\' && i + 1 < s.size()) {
            char next = s[i + 1];
            if (next == 'x' && i + 3 < s.size()) {
                // Hex escape: \xHH
                char hex[3] = {s[i+2], s[i+3], 0};
                result.push_back(static_cast<uint8_t>(strtoul(hex, nullptr, 16)));
                i += 4;
            } else if (next == 'n') { result.push_back('\n'); i += 2; }
            else if (next == 'r') { result.push_back('\r'); i += 2; }
            else if (next == 't') { result.push_back('\t'); i += 2; }
            else if (next == '\\') { result.push_back('\\'); i += 2; }
            else if (next == '\'') { result.push_back('\''); i += 2; }
            else { result.push_back(next); i += 2; }
        } else {
            result.push_back(static_cast<uint8_t>(s[i]));
            i++;
        }
    }
    return result;
}

static std::vector<float> manifest_transform_to_vec(const json& tr) {
    // Manifest uses dict {"px": ..., "py": ..., ...} or array [px, py, ...]
    if (tr.is_array()) {
        return tr.get<std::vector<float>>();
    }
    return {tr["px"], tr["py"], tr["pz"],
            tr["rx"], tr["ry"], tr["rz"], tr["rw"],
            tr["sx"], tr["sy"], tr["sz"]};
}

static std::vector<uint8_t> serialize_from_manifest(const json& vec) {
    MsgType msg_type = static_cast<MsgType>(vec["msg_type"].get<int>());
    uint8_t flags = vec["flags"].get<uint32_t>();
    uint32_t seq = vec["sequence_id"].get<uint32_t>();
    std::optional<uint64_t> sid;
    if (!vec["session_id"].is_null()) {
        sid = vec["session_id"].get<uint64_t>();
    }
    auto& fields = vec["fields"];

    std::vector<uint8_t> body;

    switch (msg_type) {
        case MsgType::HELLO:
            body = serialize_body_hello(
                fields["protocol_version_major"],
                fields["protocol_version_minor"],
                fields["capabilities"]);
            break;
        case MsgType::HELLO_ACK:
            body = serialize_body_hello_ack(
                fields["protocol_version_major"],
                fields["protocol_version_minor"],
                fields["accepted_capabilities"],
                fields["max_chunk_size"],
                fields["session_id"]);
            break;
        case MsgType::REJECT:
            body = serialize_body_reject(
                fields["error_code"],
                fields["reason"],
                fields["min_version_major"],
                fields["min_version_minor"],
                fields["max_version_major"],
                fields["max_version_minor"]);
            break;
        case MsgType::HEARTBEAT:
        case MsgType::HEARTBEAT_ACK:
        case MsgType::DISCONNECT:
            body = serialize_body_empty();
            break;
        case MsgType::SCENE_HASH:
            body = serialize_body_scene_hash(fields["hash"], fields["object_count"]);
            break;
        case MsgType::SCENE_FULL:
        case MsgType::SCENE_DELTA:
            body = serialize_body_scene_count(fields["object_count"]);
            break;
        case MsgType::OBJECT_CREATE: {
            std::string pid_str = fields["persistent_id"].get<std::string>();
            std::string name = fields["name"].get<std::string>();
            std::string parent = fields.contains("parent_id") ?
                fields["parent_id"].get<std::string>() : "";
            uint8_t prim = fields["primitive_type"].get<uint8_t>();
            auto tr = manifest_transform_to_vec(fields["transform"]);
            uint32_t obj_seq = fields["sequence_number"].get<uint32_t>();
            double obj_ts = fields["timestamp"].get<double>();
            body = serialize_body_object_create(pid_str, name, parent, prim,
                tr[0], tr[1], tr[2], tr[3], tr[4], tr[5], tr[6],
                tr[7], tr[8], tr[9],
                obj_seq, obj_ts);
            break;
        }
        case MsgType::OBJECT_UPDATE: {
            auto tr = manifest_transform_to_vec(fields["transform"]);
            uint32_t obj_seq = fields["sequence_number"].get<uint32_t>();
            double obj_ts = fields["timestamp"].get<double>();
            body = serialize_body_object_update(
                fields["persistent_id"],
                true,
                tr[0], tr[1], tr[2], tr[3], tr[4], tr[5], tr[6],
                tr[7], tr[8], tr[9],
                true, fields["name"].get<std::string>(),
                true, fields["visibility"],
                obj_seq, obj_ts);
            break;
        }
        case MsgType::OBJECT_DELETE:
            body = serialize_body_object_delete(
                fields["persistent_id"],
                fields["sequence_number"].get<uint32_t>(),
                fields["timestamp"].get<double>());
            break;
        case MsgType::OBJECT_RENAME:
            body = serialize_body_object_rename(fields["persistent_id"], fields["new_name"]);
            break;
        case MsgType::OBJECT_REPARENT:
            body = serialize_body_object_reparent(fields["persistent_id"], fields["new_parent_id"]);
            break;
        case MsgType::OBJECT_VISIBILITY:
            body = serialize_body_object_visibility(fields["persistent_id"], fields["visible"]);
            break;
        case MsgType::MESH_START:
            body = serialize_body_mesh_start(fields["persistent_id"],
                fields["total_chunks"], fields["format_flags"]);
            break;
        case MsgType::MESH_CHUNK: {
            // data is stored as Python bytes literal "b'\\x00\\x01...'"
            std::string data_str = fields["data"].get<std::string>();
            auto data = parse_python_bytes_literal(data_str);
            body = serialize_body_mesh_chunk(fields["persistent_id"],
                fields["chunk_index"], fields["vertex_offset"],
                fields["vertex_count"], fields["index_count"], data);
            break;
        }
        case MsgType::MESH_END:
            body = serialize_body_mesh_end(fields["persistent_id"], fields["checksum"]);
            break;
        case MsgType::MESH_DATA: {
            uint32_t vc = fields["vertex_count"];
            uint32_t ic = fields["index_count"];
            auto verts = fields["vertices"].get<std::vector<float>>();
            auto norms = fields["normals"].get<std::vector<float>>();
            auto uvs = fields["uvs"].get<std::vector<float>>();
            auto inds = fields["indices"].get<std::vector<uint32_t>>();
            body = serialize_body_mesh_data(fields["persistent_id"],
                vc, ic, fields["format_flags"], verts, norms, uvs, inds);
            break;
        }
        case MsgType::MESH_DELTA: {
            uint32_t vc = fields["vertex_count"];
            auto verts = fields["vertices"].get<std::vector<float>>();
            auto norms = fields["normals"].get<std::vector<float>>();
            auto uvs = fields["uvs"].get<std::vector<float>>();
            body = serialize_body_mesh_delta(fields["persistent_id"],
                vc, fields["format_flags"], verts, norms, uvs);
            break;
        }
        case MsgType::MATERIAL_CREATE: {
            auto bc = fields["base_color"].get<std::vector<float>>();
            auto em = fields["emission"].get<std::vector<float>>();
            std::string tex = fields.contains("texture_path") ?
                fields["texture_path"].get<std::string>() : "";
            body = serialize_body_material_create(
                fields["material_id"], fields["name"],
                bc[0], bc[1], bc[2], bc[3],
                fields["metallic"], fields["roughness"],
                em[0], em[1], em[2], tex,
                fields["sequence_number"].get<uint32_t>(),
                fields["timestamp"].get<double>());
            break;
        }
        case MsgType::MATERIAL_UPDATE: {
            auto bc = fields["base_color"].get<std::vector<float>>();
            auto em = fields["emission"].get<std::vector<float>>();
            std::string tex = fields.contains("texture_path") ?
                fields["texture_path"].get<std::string>() : "";
            body = serialize_body_material_update(fields["material_id"],
                bc[0], bc[1], bc[2], bc[3],
                fields["metallic"], fields["roughness"],
                em[0], em[1], em[2], tex,
                fields["sequence_number"].get<uint32_t>(),
                fields["timestamp"].get<double>());
            break;
        }
        case MsgType::MATERIAL_ASSIGN:
            body = serialize_body_material_assign(
                fields["persistent_id"], fields["material_id"], fields["slot_index"],
                fields["sequence_number"].get<uint32_t>(),
                fields["timestamp"].get<double>());
            break;
        case MsgType::FBX_IMPORT_REQUEST:
            body = serialize_body_fbx_import_request(
                fields["persistent_id"].get<std::string>(),
                fields["version"].get<uint32_t>(),
                fields["fbx_path"].get<std::string>(),
                fields["object_name"].get<std::string>(),
                fields["vert_count"].get<uint32_t>(),
                fields["tri_count"].get<uint32_t>(),
                fields["mat_slot_count"].get<uint32_t>(),
                fields["geometry_hash"].get<uint64_t>(),
                fields["sequence_number"].get<uint32_t>(),
                fields["timestamp"].get<double>());
            break;
        case MsgType::CAMERA_CREATE: {
            auto tr = manifest_transform_to_vec(fields["transform"]);
            float cs = fields.value("clip_start", 0.1f);
            float ce = fields.value("clip_end", 1000.0f);
            float os = fields.value("ortho_scale", 6.0f);
            uint8_t cf = static_cast<uint8_t>(fields.value("camera_flags", 0));
            uint32_t seq = fields.contains("sequence_number")
                ? static_cast<uint32_t>(fields["sequence_number"]) : 0U;
            double ts = fields.value("timestamp", 0.0);
            std::string parent_id_str;
            if (fields.contains("parent_id")) {
                parent_id_str = fields["parent_id"].get<std::string>();
            }
            body = serialize_body_camera_create(fields["camera_id"], fields["name"], parent_id_str,
                tr[0], tr[1], tr[2], tr[3], tr[4], tr[5], tr[6],
                tr[7], tr[8], tr[9],
                fields["focal_length"], fields["sensor_width"], fields["sensor_height"],
                cs, ce, os, cf, seq, ts);
            break;
        }
        case MsgType::CAMERA_UPDATE: {
            auto tr = manifest_transform_to_vec(fields["transform"]);
            uint32_t seq = fields.contains("sequence_number")
                ? static_cast<uint32_t>(fields["sequence_number"]) : 0U;
            double ts = fields.value("timestamp", 0.0);
            body = serialize_body_camera_update(fields["camera_id"],
                tr[0], tr[1], tr[2], tr[3], tr[4], tr[5], tr[6],
                tr[7], tr[8], tr[9],
                fields["focal_length"], fields["sensor_width"], fields["sensor_height"],
                fields["clip_start"], fields["clip_end"], fields["ortho_scale"],
                static_cast<uint8_t>(fields.value("camera_flags", 0)),
                seq, ts);
            break;
        }
        case MsgType::CAMERASETACTIVE:
            body = serialize_body_camera_setactive(fields["camera_id"]);
            break;
        case MsgType::SYNC_ACK:
            body = serialize_body_sync_ack(fields["acked_seq"]);
            break;
        case MsgType::ERROR:
            body = serialize_body_error(fields["error_code"], fields["message"]);
            break;
        default:
            throw std::runtime_error("Unknown MsgType in manifest");
    }

    return PackFrame(msg_type, flags, seq, sid, body);
}

// ─── Main ──────────────────────────────────────────────────────

int main(int argc, char* argv[]) {
    if (argc < 2) {
        fprintf(stderr, "Usage: %s <vectors_dir> [--output-cpp-json <path>] [--output-cpp-bins <dir>]\n", argv[0]);
        return 1;
    }

    fs::path vectors_dir = argv[1];
    fs::path json_output = vectors_dir / "cpp_deserialized.json";
    fs::path cpp_bins_dir = vectors_dir / "cpp_serialized";

    // Parse optional args
    for (int i = 2; i < argc; i++) {
        std::string arg = argv[i];
        if (arg == "--output-cpp-json" && i + 1 < argc) {
            json_output = argv[++i];
        } else if (arg == "--output-cpp-bins" && i + 1 < argc) {
            cpp_bins_dir = argv[++i];
        }
    }

    // Load manifest
    auto manifest = load_manifest(vectors_dir / "manifest.json");
    int vector_count = manifest["vector_count"].get<int>();
    printf("Loaded manifest: %d vectors\n", vector_count);

    // ─── Part 1: Deserialize golden vectors → JSON ────────────
    printf("\n=== Part 1: C++ deserializes golden vectors → JSON ===\n");

    json cpp_results = json::array();
    int pass1 = 0, fail1 = 0;

    for (auto& vec : manifest["vectors"]) {
        std::string name = vec["name"].get<std::string>();
        std::string bin_file = vec["file"].get<std::string>();
        fs::path bin_path = vectors_dir / bin_file;

        std::string data = read_file(bin_path);
        if (data.empty()) {
            printf("  FAIL  %s: cannot read %s\n", name.c_str(), bin_file.c_str());
            fail1++;
            continue;
        }

        try {
            json result = deserialize_to_json(
                reinterpret_cast<const uint8_t*>(data.data()), data.size());
            result["name"] = name;
            cpp_results.push_back(result);
            printf("  PASS  %s (%zu bytes)\n", name.c_str(), data.size());
            pass1++;
        } catch (const std::exception& e) {
            printf("  FAIL  %s: %s\n", name.c_str(), e.what());
            fail1++;
        }
    }

    // Write C++ deserialized JSON
    {
        std::ofstream f(json_output);
        f << cpp_results.dump(2) << "\n";
    }
    printf("\nWrote C++ deserialized JSON: %s (%d vectors)\n",
           json_output.c_str(), pass1);

    // ─── Part 2: Serialize from manifest → .bin ───────────────
    printf("\n=== Part 2: C++ serializes from manifest → .bin ===\n");

    fs::create_directories(cpp_bins_dir);
    int pass2 = 0, fail2 = 0;
    json cpp_serialized_results = json::array();

    for (auto& vec : manifest["vectors"]) {
        std::string name = vec["name"].get<std::string>();

        try {
            auto frame = serialize_from_manifest(vec);

            // Save .bin
            fs::path out_bin = cpp_bins_dir / (name + ".bin");
            {
                std::ofstream f(out_bin, std::ios::binary);
                f.write(reinterpret_cast<const char*>(frame.data()), frame.size());
            }

            // Deserialize to verify and record
            json result = deserialize_to_json(frame.data(), frame.size());
            result["name"] = name;
            result["original_size"] = vec["size"].get<int>();
            result["cpp_size"] = static_cast<int>(frame.size());
            cpp_serialized_results.push_back(result);

            printf("  PASS  %s (%zu bytes, original %d bytes)\n",
                   name.c_str(), frame.size(), vec["size"].get<int>());
            pass2++;
        } catch (const std::exception& e) {
            printf("  FAIL  %s: %s\n", name.c_str(), e.what());
            fail2++;
        }
    }

    // Write C++ serialized-then-deserialized JSON
    {
        fs::path json2 = cpp_bins_dir / "cpp_serialized_deserialized.json";
        std::ofstream f(json2);
        f << cpp_serialized_results.dump(2) << "\n";
    }
    printf("\nWrote C++ serialized .bin files: %s/%d files\n",
           cpp_bins_dir.c_str(), pass2);

    // ─── Summary ──────────────────────────────────────────────
    printf("\n=== Summary ===\n");
    printf("Deserialize golden: %d/%d PASS\n", pass1, vector_count);
    printf("Serialize from manifest: %d/%d PASS\n", pass2, vector_count);

    if (fail1 > 0 || fail2 > 0) {
        printf("\nSOME TESTS FAILED\n");
        return 1;
    }
    printf("\nALL CROSS-LANGUAGE TESTS PASSED\n");
    return 0;
}
