/**
 * LiveSync Protocol — Property tests.
 *
 * For each message type, generates random valid instances,
 * serializes → deserializes → compares fields.
 *
 * Also includes edge-case tests for boundary values.
 */

#include "livesync_serializer.h"
#include "livesync_messages.h"
#include "livesync_deserializer.h"

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <cmath>
#include <vector>
#include <array>
#include <string>
#include <random>
#include <functional>
#include <typeindex>
#include <typeinfo>

using namespace livesync;

// ─── RNG ───────────────────────────────────────────────────────

static std::mt19937 rng(42);  // deterministic seed

static uint8_t  rand_u8()  { return std::uniform_int_distribution<int>(0, 255)(rng); }
static uint16_t rand_u16() { return std::uniform_int_distribution<int>(0, 65535)(rng); }
static uint32_t rand_u32() { return std::uniform_int_distribution<uint32_t>(0, UINT32_MAX)(rng); }
static uint64_t rand_u64() {
    uint32_t lo = rand_u32();
    uint32_t hi = rand_u32();
    return static_cast<uint64_t>(hi) << 32 | lo;
}
static float rand_float() {
    // Avoid NaN/Inf — generate in [-1000, 1000]
    return std::uniform_real_distribution<float>(-1000.0f, 1000.0f)(rng);
}
static bool rand_bool() { return std::uniform_int_distribution<int>(0, 1)(rng) == 1; }

static std::string rand_string() {
    int len = std::uniform_int_distribution<int>(0, 20)(rng);
    std::string s;
    s.reserve(len);
    for (int i = 0; i < len; i++) {
        s += static_cast<char>(std::uniform_int_distribution<int>(32, 126)(rng));
    }
    return s;
}

static std::string uuid_bytes_to_string_local(const uint8_t* bytes) {
    static const char* hex = "0123456789abcdef";
    std::string result;
    result.reserve(36);
    int pos = 0;
    for (int i = 0; i < 16; i++) {
        if (pos == 3 || pos == 5 || pos == 7 || pos == 9) result += '-';
        result += hex[(bytes[i] >> 4) & 0x0F];
        result += hex[bytes[i] & 0x0F];
        pos += 2;
    }
    return result;
}

static std::string rand_uuid_str() {
    std::array<uint8_t, 16> bytes;
    for (auto& b : bytes) b = rand_u8();
    return uuid_bytes_to_string_local(bytes.data());
}

static std::vector<float> rand_f32_array(int n) {
    std::vector<float> v(n);
    for (auto& f : v) f = rand_float();
    return v;
}

static std::vector<uint32_t> rand_u32_array(int n) {
    std::vector<uint32_t> v(n);
    for (auto& u : v) u = rand_u32();
    return v;
}

static std::vector<uint8_t> rand_raw_bytes() {
    int len = std::uniform_int_distribution<int>(0, 100)(rng);
    std::vector<uint8_t> v(len);
    for (auto& b : v) b = rand_u8();
    return v;
}

// ─── Helpers ───────────────────────────────────────────────────

static int pass_count = 0;
static int fail_count = 0;

static void check(bool cond, const char* msg) {
    if (cond) {
        pass_count++;
    } else {
        fail_count++;
        printf("  FAIL: %s\n", msg);
    }
}

template<typename T>
static T get_field(const DeserializedMessage& msg, const std::string& key) {
    return std::get<T>(msg.body.at(key));
}

static bool has_field(const DeserializedMessage& msg, const std::string& key) {
    return msg.body.find(key) != msg.body.end();
}

// Compare two floats with epsilon for canonicalized values
static bool floats_eq(float a, float b) {
    if (std::isnan(a) || std::isnan(b)) return false;
    if (a == b) return true;
    return std::abs(a - b) < 1e-6f;
}

// Compare two float vectors
static bool fvec_eq(const std::vector<float>& a, const std::vector<float>& b) {
    if (a.size() != b.size()) return false;
    for (size_t i = 0; i < a.size(); i++) {
        if (!floats_eq(a[i], b[i])) return false;
    }
    return true;
}

// Compare two u32 vectors
static bool uvec_eq(const std::vector<uint32_t>& a, const std::vector<uint32_t>& b) {
    return a == b;
}

// Compare two uuid arrays
static bool uuid_eq(const std::array<uint8_t, 16>& a, const std::array<uint8_t, 16>& b) {
    return a == b;
}

// Canonicalize quaternion to match serializer exactly: normalize via float64, then canonicalize_float each component
static void canonicalize_quat(float& x, float& y, float& z, float& w) {
    double dx = x, dy = y, dz = z, dw = w;
    double mag = std::sqrt(dx*dx + dy*dy + dz*dz + dw*dw);
    if (mag < 1e-7) {
        x = 0.0f; y = 0.0f; z = 0.0f; w = 1.0f;
        return;
    }
    // canonicalize_float: reject NaN, canonicalize -0 to +0
    auto cf = [](float v) -> float {
        if (v != v) return 0.0f;  // NaN → 0
        if (v == 0.0f) return 0.0f;
        return v;
    };
    x = cf(static_cast<float>(dx / mag));
    y = cf(static_cast<float>(dy / mag));
    z = cf(static_cast<float>(dz / mag));
    w = cf(static_cast<float>(dw / mag));
}

// Session ID for post-session messages
static constexpr uint64_t DEFAULT_SID = 0xDEADBEEF12345678ULL;

// Helper: PackFrame with mandatory session_id for post-session messages
static std::vector<uint8_t> pack_post_session(
    MsgType msg_type, uint8_t flags, uint32_t seq, std::span<const uint8_t> body) {
    printf("  [DEBUG] pack_post_session 0x%02X seq=%u sid=%lu body=%zu\n",
           (unsigned)msg_type, seq, (unsigned long)DEFAULT_SID, body.size());
    return PackFrame(msg_type, flags, seq, DEFAULT_SID, body);
}

// ─── Property Tests ────────────────────────────────────────────
// For each message type: serialize random → deserialize → compare fields

static void test_property_hello() {
    for (int i = 0; i < 100; i++) {
        uint8_t vmaj = rand_u8();
        uint8_t vmin = rand_u8();
        uint64_t caps = rand_u64();
        uint32_t seq = rand_u32();

        auto body = serialize_body_hello(vmaj, vmin, caps);
        auto frame = PackFrame(MsgType::HELLO, 0, seq, std::nullopt, body);
        auto d = DeserializeFrame(frame.data(), frame.size());

        check(d.msg_type == MsgType::HELLO, "hello msg_type");
        check(d.sequence_id == seq, "hello seq");
        check(!d.session_id.has_value(), "hello no session");
        check(get_field<uint8_t>(d, "protocol_version_major") == vmaj, "hello vmaj");
        check(get_field<uint8_t>(d, "protocol_version_minor") == vmin, "hello vmin");
        check(get_field<uint64_t>(d, "capabilities") == caps, "hello caps");
    }
}

static void test_property_hello_ack() {
    for (int i = 0; i < 100; i++) {
        uint8_t vmaj = rand_u8();
        uint8_t vmin = rand_u8();
        uint64_t acaps = rand_u64();
        uint32_t mcs = rand_u32();
        uint64_t sid = rand_u64();
        uint32_t seq = rand_u32();

        auto body = serialize_body_hello_ack(vmaj, vmin, acaps, mcs, sid);
        auto frame = PackFrame(MsgType::HELLO_ACK, 0, seq, std::nullopt, body);
        auto d = DeserializeFrame(frame.data(), frame.size());

        check(d.msg_type == MsgType::HELLO_ACK, "hello_ack msg_type");
        check(d.sequence_id == seq, "hello_ack seq");
        // HELLO_ACK is pre-session: no session_id in header, but body contains it
        check(!d.session_id.has_value(), "hello_ack no header session");
        check(get_field<uint8_t>(d, "protocol_version_major") == vmaj, "hello_ack vmaj");
        check(get_field<uint8_t>(d, "protocol_version_minor") == vmin, "hello_ack vmin");
        check(get_field<uint64_t>(d, "accepted_capabilities") == acaps, "hello_ack acaps");
        check(get_field<uint32_t>(d, "max_chunk_size") == mcs, "hello_ack mcs");
        check(get_field<uint64_t>(d, "session_id") == sid, "hello_ack sid");
    }
}

static void test_property_reject() {
    for (int i = 0; i < 100; i++) {
        uint16_t ec = rand_u16();
        std::string reason = rand_string();
        uint8_t mminmaj = rand_u8(), mminmin = rand_u8();
        uint8_t mmaxmaj = rand_u8(), mmaxmin = rand_u8();
        uint32_t seq = rand_u32();

        auto body = serialize_body_reject(ec, reason, mminmaj, mminmin, mmaxmaj, mmaxmin);
        auto frame = PackFrame(MsgType::REJECT, 0, seq, std::nullopt, body);
        auto d = DeserializeFrame(frame.data(), frame.size());

        check(d.msg_type == MsgType::REJECT, "reject msg_type");
        check(get_field<uint16_t>(d, "error_code") == ec, "reject ec");
        check(get_field<std::string>(d, "reason") == reason, "reject reason");
        check(get_field<uint8_t>(d, "min_version_major") == mminmaj, "reject mminmaj");
        check(get_field<uint8_t>(d, "min_version_minor") == mminmin, "reject mminmin");
        check(get_field<uint8_t>(d, "max_version_major") == mmaxmaj, "reject mmaxmaj");
        check(get_field<uint8_t>(d, "max_version_minor") == mmaxmin, "reject mmaxmin");
    }
}

static void test_property_empty_messages() {
    uint32_t seq = rand_u32();
    uint64_t sid = rand_u64();

    // HEARTBEAT (post-session)
    auto body = serialize_body_empty();
    auto frame = PackFrame(MsgType::HEARTBEAT, 0, seq, DEFAULT_SID, body);
    auto d = DeserializeFrame(frame.data(), frame.size());
    check(d.msg_type == MsgType::HEARTBEAT, "heartbeat msg_type");
    check(d.body.empty(), "heartbeat empty body");

    // HEARTBEAT_ACK (post-session)
    frame = PackFrame(MsgType::HEARTBEAT_ACK, 0, seq, DEFAULT_SID, body);
    d = DeserializeFrame(frame.data(), frame.size());
    check(d.msg_type == MsgType::HEARTBEAT_ACK, "heartbeat_ack msg_type");

    // DISCONNECT
    frame = PackFrame(MsgType::DISCONNECT, 0, seq, DEFAULT_SID, body);
    d = DeserializeFrame(frame.data(), frame.size());
    check(d.msg_type == MsgType::DISCONNECT, "disconnect msg_type");
}

static void test_property_scene_hash() {
    for (int i = 0; i < 100; i++) {
        uint64_t hash = rand_u64();
        uint32_t oc = rand_u32();
        uint32_t seq = rand_u32();

        auto body = serialize_body_scene_hash(hash, oc);
        auto frame = PackFrame(MsgType::SCENE_HASH, 0, seq, DEFAULT_SID, body);
        auto d = DeserializeFrame(frame.data(), frame.size());

        check(d.msg_type == MsgType::SCENE_HASH, "scene_hash msg_type");
        check(get_field<uint64_t>(d, "hash") == hash, "scene_hash hash");
        check(get_field<uint32_t>(d, "object_count") == oc, "scene_hash oc");
    }
}

static void test_property_scene_full_delta() {
    for (int i = 0; i < 100; i++) {
        uint32_t oc = rand_u32();
        uint32_t seq = rand_u32();

        // SCENE_FULL
        auto body = serialize_body_scene_count(oc);
        auto frame = PackFrame(MsgType::SCENE_FULL, 0, seq, DEFAULT_SID, body);
        auto d = DeserializeFrame(frame.data(), frame.size());
        check(d.msg_type == MsgType::SCENE_FULL, "scene_full msg_type");
        check(get_field<uint32_t>(d, "object_count") == oc, "scene_full oc");

        // SCENE_DELTA
        frame = PackFrame(MsgType::SCENE_DELTA, 0, seq, DEFAULT_SID, body);
        d = DeserializeFrame(frame.data(), frame.size());
        check(d.msg_type == MsgType::SCENE_DELTA, "scene_delta msg_type");
        check(get_field<uint32_t>(d, "object_count") == oc, "scene_delta oc");
    }
}

static void test_property_object_create() {
    for (int i = 0; i < 100; i++) {
        std::string pid = rand_uuid_str();
        std::string name = rand_string();
        std::string parent = rand_bool() ? rand_uuid_str() : "";
        uint8_t prim = rand_u8();
        float px = rand_float(), py = rand_float(), pz = rand_float();
        float rx = rand_float(), ry = rand_float(), rz = rand_float(), rw = rand_float();
        float sx = rand_float(), sy = rand_float(), sz = rand_float();
        uint32_t seq = rand_u32();
        double ts = 1700000000.0 + (i * 0.1);

        auto body = serialize_body_object_create(pid, name, parent, prim,
            px, py, pz, rx, ry, rz, rw, sx, sy, sz,
            seq, ts);
        auto frame = PackFrame(MsgType::OBJECT_CREATE, 0, seq, DEFAULT_SID, body);
        auto d = DeserializeFrame(frame.data(), frame.size());

        check(d.msg_type == MsgType::OBJECT_CREATE, "object_create msg_type");

        // persistent_id
        auto dpid = get_field<std::array<uint8_t, 16>>(d, "persistent_id");
        auto opid = parse_uuid(pid);
        check(uuid_eq(dpid, opid), "object_create pid");

        // name
        check(get_field<std::string>(d, "name") == name, "object_create name");

        // parent_id (optional)
        if (!parent.empty()) {
            check(has_field(d, "parent_id"), "object_create has parent");
            auto dpar = get_field<std::array<uint8_t, 16>>(d, "parent_id");
            auto opar = parse_uuid(parent);
            check(uuid_eq(dpar, opar), "object_create parent");
        }

        // primitive_type
        check(get_field<uint8_t>(d, "primitive_type") == prim, "object_create primitive_type");

        // transform
        auto tr = get_field<std::vector<float>>(d, "transform");
        check(tr.size() == 10, "object_create transform size");
        check(floats_eq(tr[0], px), "object_create px");
        check(floats_eq(tr[1], py), "object_create py");
        check(floats_eq(tr[2], pz), "object_create pz");
        // Quaternion is canonicalized (normalized, w >= 0)
        canonicalize_quat(rx, ry, rz, rw);
        check(floats_eq(tr[3], rx), "object_create rx");
        check(floats_eq(tr[4], ry), "object_create ry");
        check(floats_eq(tr[5], rz), "object_create rz");
        check(floats_eq(tr[6], rw), "object_create rw");
        check(floats_eq(tr[7], sx), "object_create sx");
        check(floats_eq(tr[8], sy), "object_create sy");
        check(floats_eq(tr[9], sz), "object_create sz");

        // sequence_number, timestamp
        check(get_field<uint32_t>(d, "sequence_number") == seq, "object_create seq");
        check(get_field<double>(d, "timestamp") == ts, "object_create ts");
    }
}

static void test_property_object_update() {
    for (int i = 0; i < 100; i++) {
        std::string pid = rand_uuid_str();
        float px = rand_float(), py = rand_float(), pz = rand_float();
        float rx = rand_float(), ry = rand_float(), rz = rand_float(), rw = rand_float();
        float sx = rand_float(), sy = rand_float(), sz = rand_float();
        std::string name = rand_string();
        uint8_t vis = rand_u8();
        uint32_t seq = rand_u32();
        double ts = 1700000000.0 + (i * 0.1);

        auto body = serialize_body_object_update(pid, true,
            px, py, pz, rx, ry, rz, rw, sx, sy, sz,
            true, name, true, vis,
            seq, ts);
        auto frame = PackFrame(MsgType::OBJECT_UPDATE, 0, seq, DEFAULT_SID, body);
        auto d = DeserializeFrame(frame.data(), frame.size());

        check(d.msg_type == MsgType::OBJECT_UPDATE, "object_update msg_type");

        auto dpid = get_field<std::array<uint8_t, 16>>(d, "persistent_id");
        auto opid = parse_uuid(pid);
        check(uuid_eq(dpid, opid), "object_update pid");

        auto tr = get_field<std::vector<float>>(d, "transform");
        check(tr.size() == 10, "object_update transform size");
        check(floats_eq(tr[0], px), "object_update px");
        canonicalize_quat(rx, ry, rz, rw);
        check(floats_eq(tr[6], rw), "object_update rw");

        check(get_field<std::string>(d, "name") == name, "object_update name");
        check(get_field<uint8_t>(d, "visibility") == vis, "object_update vis");
        check(get_field<uint32_t>(d, "sequence_number") == seq, "object_update seq");
        check(get_field<double>(d, "timestamp") == ts, "object_update ts");
    }
}

static void test_property_object_delete() {
    for (int i = 0; i < 100; i++) {
        std::string pid = rand_uuid_str();
        uint32_t del_seq = rand_u32();
        double del_ts = 1700000000.0 + (i * 0.1);
        uint32_t seq = rand_u32();

        auto body = serialize_body_object_delete(pid, del_seq, del_ts);
        auto frame = PackFrame(MsgType::OBJECT_DELETE, 0, seq, DEFAULT_SID, body);
        auto d = DeserializeFrame(frame.data(), frame.size());

        check(d.msg_type == MsgType::OBJECT_DELETE, "object_delete msg_type");
        auto dpid = get_field<std::array<uint8_t, 16>>(d, "persistent_id");
        auto opid = parse_uuid(pid);
        check(uuid_eq(dpid, opid), "object_delete pid");
        check(get_field<uint32_t>(d, "sequence_number") == del_seq, "object_delete seq");
        check(get_field<double>(d, "timestamp") == del_ts, "object_delete ts");
    }
}

static void test_property_object_rename() {
    for (int i = 0; i < 100; i++) {
        std::string pid = rand_uuid_str();
        std::string name = rand_string();
        uint32_t seq = rand_u32();

        auto body = serialize_body_object_rename(pid, name);
        auto frame = PackFrame(MsgType::OBJECT_RENAME, 0, seq, DEFAULT_SID, body);
        auto d = DeserializeFrame(frame.data(), frame.size());

        check(d.msg_type == MsgType::OBJECT_RENAME, "object_rename msg_type");
        auto dpid = get_field<std::array<uint8_t, 16>>(d, "persistent_id");
        auto opid = parse_uuid(pid);
        check(uuid_eq(dpid, opid), "object_rename pid");
        check(get_field<std::string>(d, "new_name") == name, "object_rename name");
    }
}

static void test_property_object_reparent() {
    for (int i = 0; i < 100; i++) {
        std::string pid = rand_uuid_str();
        std::string ppid = rand_uuid_str();
        uint32_t seq = rand_u32();

        auto body = serialize_body_object_reparent(pid, ppid);
        auto frame = PackFrame(MsgType::OBJECT_REPARENT, 0, seq, DEFAULT_SID, body);
        auto d = DeserializeFrame(frame.data(), frame.size());

        check(d.msg_type == MsgType::OBJECT_REPARENT, "object_reparent msg_type");
        auto dpid = get_field<std::array<uint8_t, 16>>(d, "persistent_id");
        auto opid = parse_uuid(pid);
        check(uuid_eq(dpid, opid), "object_reparent pid");
        auto dppid = get_field<std::array<uint8_t, 16>>(d, "new_parent_id");
        auto oppid = parse_uuid(ppid);
        check(uuid_eq(dppid, oppid), "object_reparent parent");
    }
}

static void test_property_object_visibility() {
    for (int i = 0; i < 100; i++) {
        std::string pid = rand_uuid_str();
        uint8_t vis = rand_u8();
        uint32_t seq = rand_u32();

        auto body = serialize_body_object_visibility(pid, vis);
        auto frame = PackFrame(MsgType::OBJECT_VISIBILITY, 0, seq, DEFAULT_SID, body);
        auto d = DeserializeFrame(frame.data(), frame.size());

        check(d.msg_type == MsgType::OBJECT_VISIBILITY, "object_vis msg_type");
        auto dpid = get_field<std::array<uint8_t, 16>>(d, "persistent_id");
        auto opid = parse_uuid(pid);
        check(uuid_eq(dpid, opid), "object_vis pid");
        check(get_field<uint8_t>(d, "visible") == vis, "object_vis vis");
    }
}

static void test_property_mesh_start() {
    for (int i = 0; i < 100; i++) {
        std::string pid = rand_uuid_str();
        uint16_t tc = rand_u16();
        uint8_t ff = rand_u8();
        uint32_t seq = rand_u32();

        auto body = serialize_body_mesh_start(pid, tc, ff);
        auto frame = PackFrame(MsgType::MESH_START, 0, seq, DEFAULT_SID, body);
        auto d = DeserializeFrame(frame.data(), frame.size());

        check(d.msg_type == MsgType::MESH_START, "mesh_start msg_type");
        auto dpid = get_field<std::array<uint8_t, 16>>(d, "persistent_id");
        auto opid = parse_uuid(pid);
        check(uuid_eq(dpid, opid), "mesh_start pid");
        check(get_field<uint16_t>(d, "total_chunks") == tc, "mesh_start tc");
        check(get_field<uint8_t>(d, "format_flags") == ff, "mesh_start ff");
    }
}

static void test_property_mesh_chunk() {
    for (int i = 0; i < 100; i++) {
        std::string pid = rand_uuid_str();
        uint16_t ci = rand_u16();
        uint16_t vo = rand_u16();
        uint32_t vc = rand_u32();
        uint32_t ic = rand_u32();
        auto data = rand_raw_bytes();
        uint32_t seq = rand_u32();

        auto body = serialize_body_mesh_chunk(pid, ci, vo, vc, ic, data);
        auto frame = PackFrame(MsgType::MESH_CHUNK, 0, seq, DEFAULT_SID, body);
        auto d = DeserializeFrame(frame.data(), frame.size());

        check(d.msg_type == MsgType::MESH_CHUNK, "mesh_chunk msg_type");
        auto dpid = get_field<std::array<uint8_t, 16>>(d, "persistent_id");
        auto opid = parse_uuid(pid);
        check(uuid_eq(dpid, opid), "mesh_chunk pid");
        check(get_field<uint16_t>(d, "chunk_index") == ci, "mesh_chunk ci");
        check(get_field<uint16_t>(d, "vertex_offset") == vo, "mesh_chunk vo");
        check(get_field<uint32_t>(d, "vertex_count") == vc, "mesh_chunk vc");
        check(get_field<uint32_t>(d, "index_count") == ic, "mesh_chunk ic");
        auto dd = get_field<std::vector<uint8_t>>(d, "data");
        check(dd == data, "mesh_chunk data");
    }
}

static void test_property_mesh_end() {
    for (int i = 0; i < 100; i++) {
        std::string pid = rand_uuid_str();
        uint32_t cs = rand_u32();
        uint32_t seq = rand_u32();

        auto body = serialize_body_mesh_end(pid, cs);
        auto frame = PackFrame(MsgType::MESH_END, 0, seq, DEFAULT_SID, body);
        auto d = DeserializeFrame(frame.data(), frame.size());

        check(d.msg_type == MsgType::MESH_END, "mesh_end msg_type");
        auto dpid = get_field<std::array<uint8_t, 16>>(d, "persistent_id");
        auto opid = parse_uuid(pid);
        check(uuid_eq(dpid, opid), "mesh_end pid");
        check(get_field<uint32_t>(d, "checksum") == cs, "mesh_end cs");
    }
}

static void test_property_mesh_data() {
    for (int i = 0; i < 50; i++) {
        std::string pid = rand_uuid_str();
        uint32_t vc = std::uniform_int_distribution<uint32_t>(1, 20)(rng);
        uint32_t ic = std::uniform_int_distribution<uint32_t>(1, 20)(rng);
        uint8_t ff = rand_u8();
        auto verts = rand_f32_array(vc * 3);
        auto norms = rand_f32_array(vc * 3);
        auto uvs = rand_f32_array(vc * 2);
        auto inds = rand_u32_array(ic);
        uint32_t seq = rand_u32();

        auto body = serialize_body_mesh_data(pid, vc, ic, ff, verts, norms, uvs, inds);
        auto frame = PackFrame(MsgType::MESH_DATA, 0, seq, DEFAULT_SID, body);
        auto d = DeserializeFrame(frame.data(), frame.size());

        check(d.msg_type == MsgType::MESH_DATA, "mesh_data msg_type");
        auto dpid = get_field<std::array<uint8_t, 16>>(d, "persistent_id");
        auto opid = parse_uuid(pid);
        check(uuid_eq(dpid, opid), "mesh_data pid");
        check(get_field<uint32_t>(d, "vertex_count") == vc, "mesh_data vc");
        check(get_field<uint32_t>(d, "index_count") == ic, "mesh_data ic");
        check(get_field<uint8_t>(d, "format_flags") == ff, "mesh_data ff");
        check(fvec_eq(get_field<std::vector<float>>(d, "vertices"), verts), "mesh_data verts");
        check(fvec_eq(get_field<std::vector<float>>(d, "normals"), norms), "mesh_data norms");
        check(fvec_eq(get_field<std::vector<float>>(d, "uvs"), uvs), "mesh_data uvs");
        check(uvec_eq(get_field<std::vector<uint32_t>>(d, "indices"), inds), "mesh_data inds");
    }
}

static void test_property_mesh_delta() {
    for (int i = 0; i < 50; i++) {
        std::string pid = rand_uuid_str();
        uint32_t vc = std::uniform_int_distribution<uint32_t>(1, 20)(rng);
        uint8_t ff = rand_u8();
        auto verts = rand_f32_array(vc * 3);
        auto norms = rand_f32_array(vc * 3);
        auto uvs = rand_f32_array(vc * 2);
        uint32_t seq = rand_u32();

        auto body = serialize_body_mesh_delta(pid, vc, ff, verts, norms, uvs);
        auto frame = PackFrame(MsgType::MESH_DELTA, 0, seq, DEFAULT_SID, body);
        auto d = DeserializeFrame(frame.data(), frame.size());

        check(d.msg_type == MsgType::MESH_DELTA, "mesh_delta msg_type");
        auto dpid = get_field<std::array<uint8_t, 16>>(d, "persistent_id");
        auto opid = parse_uuid(pid);
        check(uuid_eq(dpid, opid), "mesh_delta pid");
        check(get_field<uint32_t>(d, "vertex_count") == vc, "mesh_delta vc");
        check(get_field<uint8_t>(d, "format_flags") == ff, "mesh_delta ff");
        check(fvec_eq(get_field<std::vector<float>>(d, "vertices"), verts), "mesh_delta verts");
        check(fvec_eq(get_field<std::vector<float>>(d, "normals"), norms), "mesh_delta norms");
        check(fvec_eq(get_field<std::vector<float>>(d, "uvs"), uvs), "mesh_delta uvs");
    }
}

static void test_property_material_create() {
    for (int i = 0; i < 100; i++) {
        std::string mid = rand_uuid_str();
        std::string name = rand_string();
        float bcr = rand_float(), bcg = rand_float(), bcb = rand_float(), bca = rand_float();
        float met = rand_float(), rou = rand_float();
        float er = rand_float(), eg = rand_float(), eb = rand_float();
        std::string tex = rand_bool() ? rand_string() : "";
        uint32_t seq = rand_u32();
        double ts = rand_float() * 1000.0;

        auto body = serialize_body_material_create(mid, name,
            bcr, bcg, bcb, bca, met, rou, er, eg, eb, tex, seq, ts);
        auto frame = PackFrame(MsgType::MATERIAL_CREATE, 0, seq, DEFAULT_SID, body);
        auto d = DeserializeFrame(frame.data(), frame.size());

        check(d.msg_type == MsgType::MATERIAL_CREATE, "material_create msg_type");
        auto dmid = get_field<std::array<uint8_t, 16>>(d, "material_id");
        auto omid = parse_uuid(mid);
        check(uuid_eq(dmid, omid), "material_create mid");
        check(get_field<std::string>(d, "name") == name, "material_create name");

        auto bc = get_field<std::vector<float>>(d, "base_color");
        check(bc.size() == 4, "material_create bc size");
        check(floats_eq(bc[0], bcr), "material_create bcr");
        check(floats_eq(bc[1], bcg), "material_create bcg");
        check(floats_eq(bc[2], bcb), "material_create bcb");
        check(floats_eq(bc[3], bca), "material_create bca");

        check(floats_eq(get_field<float>(d, "metallic"), met), "material_create met");
        check(floats_eq(get_field<float>(d, "roughness"), rou), "material_create rou");

        auto em = get_field<std::vector<float>>(d, "emission");
        check(em.size() == 3, "material_create em size");
        check(floats_eq(em[0], er), "material_create er");
        check(floats_eq(em[1], eg), "material_create eg");
        check(floats_eq(em[2], eb), "material_create eb");

        if (!tex.empty()) {
            check(has_field(d, "texture_path"), "material_create has tex");
            check(get_field<std::string>(d, "texture_path") == tex, "material_create tex");
        }
        check(get_field<uint32_t>(d, "sequence_number") == seq, "material_create seq");
        check(floats_eq(get_field<double>(d, "timestamp"), ts), "material_create ts");
    }
}

static void test_property_material_update() {
    for (int i = 0; i < 100; i++) {
        std::string mid = rand_uuid_str();
        float bcr = rand_float(), bcg = rand_float(), bcb = rand_float(), bca = rand_float();
        float met = rand_float(), rou = rand_float();
        float er = rand_float(), eg = rand_float(), eb = rand_float();
        std::string tex = rand_bool() ? rand_string() : "";
        uint32_t seq = rand_u32();
        double ts = rand_float() * 1000.0;

        auto body = serialize_body_material_update(mid,
            bcr, bcg, bcb, bca,
            met, rou,
            er, eg, eb,
            tex, seq, ts);
        auto frame = PackFrame(MsgType::MATERIAL_UPDATE, 0, seq, DEFAULT_SID, body);
        auto d = DeserializeFrame(frame.data(), frame.size());

        check(d.msg_type == MsgType::MATERIAL_UPDATE, "material_update msg_type");
        auto dmid = get_field<std::array<uint8_t, 16>>(d, "material_id");
        auto omid = parse_uuid(mid);
        check(uuid_eq(dmid, omid), "material_update mid");

        auto bc = get_field<std::vector<float>>(d, "base_color");
        check(bc.size() == 4, "material_update bc size");
        check(floats_eq(bc[0], bcr), "material_update bcr");

        check(floats_eq(get_field<float>(d, "metallic"), met), "material_update met");
        check(floats_eq(get_field<float>(d, "roughness"), rou), "material_update rou");

        auto em = get_field<std::vector<float>>(d, "emission");
        check(em.size() == 3, "material_update em size");
        check(floats_eq(em[0], er), "material_update er");

        if (!tex.empty()) {
            check(has_field(d, "texture_path"), "material_update has tex");
            check(get_field<std::string>(d, "texture_path") == tex, "material_update tex");
        }
        check(get_field<uint32_t>(d, "sequence_number") == seq, "material_update seq");
        check(floats_eq(get_field<double>(d, "timestamp"), ts), "material_update ts");
    }
}

static void test_property_material_assign() {
    for (int i = 0; i < 100; i++) {
        std::string pid = rand_uuid_str();
        std::string mid = rand_uuid_str();
        uint8_t slot = rand_u8();
        uint32_t seq = rand_u32();
        double ts = rand_float() * 1000.0;

        auto body = serialize_body_material_assign(pid, mid, slot, seq, ts);
        auto frame = PackFrame(MsgType::MATERIAL_ASSIGN, 0, seq, DEFAULT_SID, body);
        auto d = DeserializeFrame(frame.data(), frame.size());

        check(d.msg_type == MsgType::MATERIAL_ASSIGN, "material_assign msg_type");
        auto dpid = get_field<std::array<uint8_t, 16>>(d, "persistent_id");
        auto opid = parse_uuid(pid);
        check(uuid_eq(dpid, opid), "material_assign pid");
        auto dmid = get_field<std::array<uint8_t, 16>>(d, "material_id");
        auto omid = parse_uuid(mid);
        check(uuid_eq(dmid, omid), "material_assign mid");
        check(get_field<uint8_t>(d, "slot_index") == slot, "material_assign slot");
        check(get_field<uint32_t>(d, "sequence_number") == seq, "material_assign seq");
        check(floats_eq(get_field<double>(d, "timestamp"), ts), "material_assign ts");
    }
}

static void test_property_camera_create() {
    for (int i = 0; i < 100; i++) {
        std::string cid = rand_uuid_str();
        std::string name = rand_string();
        float px = rand_float(), py = rand_float(), pz = rand_float();
        float rx = rand_float(), ry = rand_float(), rz = rand_float(), rw = rand_float();
        float sx = rand_float(), sy = rand_float(), sz = rand_float();
        float fl = rand_float(), sw = rand_float(), sh = rand_float();
        float cs = rand_float(), ce = rand_float(), os = rand_float();
        uint8_t cf = static_cast<uint8_t>(rand_u32() & 0xFF);
        uint32_t seq = rand_u32();
        double ts = rand_float() * 1000.0;

        auto body = serialize_body_camera_create(cid, name, "",
            px, py, pz, rx, ry, rz, rw, sx, sy, sz,
            fl, sw, sh, cs, ce, os, cf, seq, ts);
        auto frame = PackFrame(MsgType::CAMERA_CREATE, 0, seq, DEFAULT_SID, body);
        auto d = DeserializeFrame(frame.data(), frame.size());

        check(d.msg_type == MsgType::CAMERA_CREATE, "camera_create msg_type");
        auto dcid = get_field<std::array<uint8_t, 16>>(d, "camera_id");
        auto ocid = parse_uuid(cid);
        check(uuid_eq(dcid, ocid), "camera_create cid");
        check(get_field<std::string>(d, "name") == name, "camera_create name");

        auto tr = get_field<std::vector<float>>(d, "transform");
        check(tr.size() == 10, "camera_create transform size");
        check(floats_eq(tr[0], px), "camera_create px");
        canonicalize_quat(rx, ry, rz, rw);
        check(floats_eq(tr[6], rw), "camera_create rw");

        check(floats_eq(get_field<float>(d, "focal_length"), fl), "camera_create fl");
        check(floats_eq(get_field<float>(d, "sensor_width"), sw), "camera_create sw");
        check(floats_eq(get_field<float>(d, "sensor_height"), sh), "camera_create sh");
        check(floats_eq(get_field<float>(d, "clip_start"), cs), "camera_create cs");
        check(floats_eq(get_field<float>(d, "clip_end"), ce), "camera_create ce");
        check(floats_eq(get_field<float>(d, "ortho_scale"), os), "camera_create os");
        check(get_field<uint8_t>(d, "camera_flags") == cf, "camera_create cf");
        check(get_field<uint32_t>(d, "sequence_number") == seq, "camera_create seq");
        check(floats_eq(get_field<double>(d, "timestamp"), ts), "camera_create ts");
    }
}

static void test_property_camera_update() {
    for (int i = 0; i < 100; i++) {
        std::string cid = rand_uuid_str();
        float px = rand_float(), py = rand_float(), pz = rand_float();
        float rx = rand_float(), ry = rand_float(), rz = rand_float(), rw = rand_float();
        float sx = rand_float(), sy = rand_float(), sz = rand_float();
        float fl = rand_float(), sw = rand_float(), sh = rand_float();
        float cs = rand_float(), ce = rand_float(), os = rand_float();
        uint8_t cf = static_cast<uint8_t>(rand_u32() & 0xFF);
        uint32_t seq = rand_u32();
        double ts = rand_float() * 1000.0;

        auto body = serialize_body_camera_update(cid,
            px, py, pz, rx, ry, rz, rw, sx, sy, sz,
            fl, sw, sh, cs, ce, os, cf, seq, ts);
        auto frame = PackFrame(MsgType::CAMERA_UPDATE, 0, seq, DEFAULT_SID, body);
        auto d = DeserializeFrame(frame.data(), frame.size());

        check(d.msg_type == MsgType::CAMERA_UPDATE, "camera_update msg_type");
        auto dcid = get_field<std::array<uint8_t, 16>>(d, "camera_id");
        auto ocid = parse_uuid(cid);
        check(uuid_eq(dcid, ocid), "camera_update cid");

        auto tr = get_field<std::vector<float>>(d, "transform");
        check(tr.size() == 10, "camera_update transform size");
        check(floats_eq(tr[0], px), "camera_update px");
        canonicalize_quat(rx, ry, rz, rw);
        check(floats_eq(tr[6], rw), "camera_update rw");

        check(floats_eq(get_field<float>(d, "focal_length"), fl), "camera_update fl");
        check(floats_eq(get_field<float>(d, "sensor_width"), sw), "camera_update sw");
        check(floats_eq(get_field<float>(d, "sensor_height"), sh), "camera_update sh");
        check(floats_eq(get_field<float>(d, "clip_start"), cs), "camera_update cs");
        check(floats_eq(get_field<float>(d, "clip_end"), ce), "camera_update ce");
        check(floats_eq(get_field<float>(d, "ortho_scale"), os), "camera_update os");
        check(get_field<uint8_t>(d, "camera_flags") == cf, "camera_update cf");
        check(get_field<uint32_t>(d, "sequence_number") == seq, "camera_update seq");
        check(floats_eq(get_field<double>(d, "timestamp"), ts), "camera_update ts");
    }
}

static void test_property_camera_setactive() {
    for (int i = 0; i < 100; i++) {
        std::string cid = rand_uuid_str();
        uint32_t seq = rand_u32();

        auto body = serialize_body_camera_setactive(cid);
        auto frame = PackFrame(MsgType::CAMERASETACTIVE, 0, seq, DEFAULT_SID, body);
        auto d = DeserializeFrame(frame.data(), frame.size());

        check(d.msg_type == MsgType::CAMERASETACTIVE, "camera_setactive msg_type");
        auto dcid = get_field<std::array<uint8_t, 16>>(d, "camera_id");
        auto ocid = parse_uuid(cid);
        check(uuid_eq(dcid, ocid), "camera_setactive cid");
    }
}

static void test_property_sync_ack() {
    for (int i = 0; i < 100; i++) {
        uint32_t as = rand_u32();
        uint32_t seq = rand_u32();
        uint64_t sid = rand_u64();

        auto body = serialize_body_sync_ack(as);
        auto frame = PackFrame(MsgType::SYNC_ACK, 0, seq, sid, body);
        auto d = DeserializeFrame(frame.data(), frame.size());

        check(d.msg_type == MsgType::SYNC_ACK, "sync_ack msg_type");
        check(get_field<uint32_t>(d, "acked_seq") == as, "sync_ack as");
    }
}

static void test_property_error() {
    for (int i = 0; i < 100; i++) {
        uint16_t ec = rand_u16();
        std::string msg = rand_string();
        uint32_t seq = rand_u32();

        auto body = serialize_body_error(ec, msg);
        auto frame = PackFrame(MsgType::ERROR, 0, seq, DEFAULT_SID, body);
        auto d = DeserializeFrame(frame.data(), frame.size());

        check(d.msg_type == MsgType::ERROR, "error msg_type");
        check(get_field<uint16_t>(d, "error_code") == ec, "error ec");
        check(get_field<std::string>(d, "message") == msg, "error msg");
    }
}

// ─── Edge Cases ────────────────────────────────────────────────

static void test_edge_uuid_all_zeros() {
    std::string pid(36, '0');
    // Format as UUID string: 00000000-0000-0000-0000-000000000000
    pid = "00000000-0000-0000-0000-000000000000";
    auto body = serialize_body_object_delete(pid, 1, 0.0);
    auto frame = PackFrame(MsgType::OBJECT_DELETE, 0, 1, DEFAULT_SID, body);
    auto d = DeserializeFrame(frame.data(), frame.size());

    auto dpid = get_field<std::array<uint8_t, 16>>(d, "persistent_id");
    bool all_zero = true;
    for (auto b : dpid) { if (b != 0) all_zero = false; }
    check(all_zero, "edge uuid all zeros");
}

static void test_edge_uuid_all_ff() {
    std::string pid = "FFFFFFFF-FFFF-FFFF-FFFF-FFFFFFFFFFFF";
    auto body = serialize_body_object_delete(pid, 1, 0.0);
    auto frame = PackFrame(MsgType::OBJECT_DELETE, 0, 1, DEFAULT_SID, body);
    auto d = DeserializeFrame(frame.data(), frame.size());

    auto dpid = get_field<std::array<uint8_t, 16>>(d, "persistent_id");
    bool all_ff = true;
    for (auto b : dpid) { if (b != 0xFF) all_ff = false; }
    check(all_ff, "edge uuid all ff");
}

static void test_edge_empty_string() {
    std::string pid = "00000000-0000-0000-0000-000000000000";
    auto body = serialize_body_object_rename(pid, "");
    auto frame = PackFrame(MsgType::OBJECT_RENAME, 0, 1, DEFAULT_SID, body);
    auto d = DeserializeFrame(frame.data(), frame.size());
    check(get_field<std::string>(d, "new_name").empty(), "edge empty string");
}

static void test_edge_max_uint32() {
    auto body = serialize_body_sync_ack(UINT32_MAX);
    auto frame = PackFrame(MsgType::SYNC_ACK, 0, UINT32_MAX, DEFAULT_SID, body);
    auto d = DeserializeFrame(frame.data(), frame.size());
    check(get_field<uint32_t>(d, "acked_seq") == UINT32_MAX, "edge max u32");
    check(d.sequence_id == UINT32_MAX, "edge seq max u32");
}

static void test_edge_max_uint64() {
    auto body = serialize_body_scene_hash(UINT64_MAX, UINT32_MAX);
    auto frame = PackFrame(MsgType::SCENE_HASH, 0, UINT32_MAX, DEFAULT_SID, body);
    auto d = DeserializeFrame(frame.data(), frame.size());
    check(get_field<uint64_t>(d, "hash") == UINT64_MAX, "edge max u64");
    check(get_field<uint32_t>(d, "object_count") == UINT32_MAX, "edge max u32 in scene_hash");
}

static void test_edge_float_zero() {
    std::string pid = "00000000-0000-0000-0000-000000000000";
    auto body = serialize_body_camera_create(pid, "", "", 0,0,0, 0,0,0,1, 1,1,1, 0,0,0, 0,0,0, 0, 0, 0.0);
    auto frame = PackFrame(MsgType::CAMERA_CREATE, 0, 1, DEFAULT_SID, body);
    auto d = DeserializeFrame(frame.data(), frame.size());
    check(get_field<float>(d, "focal_length") == 0.0f, "edge float zero");
}

static void test_edge_float_negative_zero() {
    std::string pid = "00000000-0000-0000-0000-000000000000";
    // pack_float32 canonicalizes -0 to +0
    auto body = serialize_body_camera_create(pid, "", "", 0,0,0, 0,0,0,1, 1,1,1, -0.0f, 0, 0, 0, 0, 0, 0, 0, 0.0);
    auto frame = PackFrame(MsgType::CAMERA_CREATE, 0, 1, DEFAULT_SID, body);
    auto d = DeserializeFrame(frame.data(), frame.size());
    float fl = get_field<float>(d, "focal_length");
    check(fl == 0.0f, "edge neg zero → pos zero");
    // Verify it's actually +0 (not -0)
    uint32_t bits;
    std::memcpy(&bits, &fl, 4);
    check((bits & 0x80000000) == 0, "edge neg zero sign bit cleared");
}

static void test_edge_large_float() {
    std::string pid = "00000000-0000-0000-0000-000000000000";
    float big = 1e30f;
    auto body = serialize_body_camera_create(pid, "", "", 0,0,0, 0,0,0,1, 1,1,1, big, 0, 0, 0, 0, 0, 0, 0, 0.0);
    auto frame = PackFrame(MsgType::CAMERA_CREATE, 0, 1, DEFAULT_SID, body);
    auto d = DeserializeFrame(frame.data(), frame.size());
    check(floats_eq(get_field<float>(d, "focal_length"), big), "edge large float");
}

static void test_edge_negative_float() {
    std::string pid = "00000000-0000-0000-0000-000000000000";
    float neg = -123.456f;
    auto body = serialize_body_camera_create(pid, "", "", neg,0,0, 0,0,0,1, 1,1,1, 0, 0, 0, 0, 0, 0, 0, 0, 0.0);
    auto frame = PackFrame(MsgType::CAMERA_CREATE, 0, 1, DEFAULT_SID, body);
    auto d = DeserializeFrame(frame.data(), frame.size());
    auto tr = get_field<std::vector<float>>(d, "transform");
    check(floats_eq(tr[0], neg), "edge negative float");
}

static void test_edge_compressed_flag() {
    auto body = serialize_body_sync_ack(42);
    auto frame = PackFrame(MsgType::SYNC_ACK, 0x01, 1, DEFAULT_SID, body);  // compressed
    auto d = DeserializeFrame(frame.data(), frame.size());
    check(d.flags == 0x01, "edge compressed flag preserved");
}

static void test_edge_sequence_wraparound() {
    auto body = serialize_body_sync_ack(1);
    auto frame = PackFrame(MsgType::SYNC_ACK, 0, UINT32_MAX, DEFAULT_SID, body);
    auto d = DeserializeFrame(frame.data(), frame.size());
    check(d.sequence_id == UINT32_MAX, "edge seq wraparound");
    // Next sequence would be 0 (wrap)
    auto body2 = serialize_body_sync_ack(2);
    auto frame2 = PackFrame(MsgType::SYNC_ACK, 0, 0, DEFAULT_SID, body2);
    auto d2 = DeserializeFrame(frame2.data(), frame2.size());
    check(d2.sequence_id == 0, "edge seq wraparound to 0");
}

// ─── Main ──────────────────────────────────────────────────────

int main() {
    printf("=== Property Tests ===\n\n");

    printf("Randomized (100 iterations each):\n");
    test_property_hello();
    test_property_hello_ack();
    test_property_reject();
    test_property_empty_messages();
    test_property_scene_hash();
    test_property_scene_full_delta();
    test_property_object_create();
    test_property_object_update();
    test_property_object_delete();
    test_property_object_rename();
    test_property_object_reparent();
    test_property_object_visibility();
    test_property_mesh_start();
    test_property_mesh_chunk();
    test_property_mesh_end();
    test_property_mesh_data();
    test_property_mesh_delta();
    test_property_material_create();
    test_property_material_update();
    test_property_material_assign();
    test_property_camera_create();
    test_property_camera_update();
    test_property_camera_setactive();
    test_property_sync_ack();
    test_property_error();

    printf("  (all randomized tests passed)\n");

    printf("\nEdge cases:\n");
    test_edge_uuid_all_zeros();
    test_edge_uuid_all_ff();
    test_edge_empty_string();
    test_edge_max_uint32();
    test_edge_max_uint64();
    test_edge_float_zero();
    test_edge_float_negative_zero();
    test_edge_large_float();
    test_edge_negative_float();
    test_edge_compressed_flag();
    test_edge_sequence_wraparound();

    printf("\n==================================================\n");
    printf("PROPERTY:  %d passed, %d failed\n", pass_count, fail_count);
    printf("TOTAL:     %d checks\n", pass_count + fail_count);
    printf("==================================================\n");

    if (fail_count > 0) {
        printf("\nSOME TESTS FAILED\n");
        return 1;
    }
    printf("\nALL PROPERTY TESTS PASSED\n");
    return 0;
}
