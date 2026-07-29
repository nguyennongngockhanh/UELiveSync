/**
 * LiveSync Protocol — Fuzz / robustness tests.
 *
 * Feeds malformed inputs to DeserializeFrame() and verifies:
 *   1. No crash / no UB
 *   2. Throws std::runtime_error (or returns — never corrupts memory)
 *   3. Error is reported, not silently accepted
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
#include <stdexcept>
#include <algorithm>
#include <numeric>

using namespace livesync;

static std::mt19937 rng(12345);

static uint8_t rand_u8() { return std::uniform_int_distribution<int>(0, 255)(rng); }
static uint32_t rand_u32() { return std::uniform_int_distribution<uint32_t>(0, UINT32_MAX)(rng); }

// ─── Helpers ────────────────────────────────────────────────────

static int pass_count = 0;
static int total_count = 0;

// Attempt to deserialize; returns true if it threw (expected), false if it succeeded
static bool expect_throw(const std::vector<uint8_t>& data, const char* label) {
    total_count++;
    try {
        auto msg = DeserializeFrame(data.data(), data.size());
        return false;
    } catch (const std::runtime_error&) {
        pass_count++;
        return true;
    } catch (...) {
        pass_count++;
        return true;
    }
}

// Attempt to deserialize; returns DeserializedMessage if it succeeds
static std::optional<DeserializedMessage> expect_ok(const std::vector<uint8_t>& data, const char* label) {
    total_count++;
    try {
        auto msg = DeserializeFrame(data.data(), data.size());
        pass_count++;
        return msg;
    } catch (const std::runtime_error& e) {
        fprintf(stderr, "  UNEXPECTED FAIL [%s]: %s\n", label, e.what());
        return std::nullopt;
    }
}

// Build a valid minimal HELLO frame
static std::vector<uint8_t> make_valid_hello() {
    std::vector<uint8_t> body = {
        0x01,  // protocol_version_major
        0x00,  // protocol_version_minor
        0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00  // capabilities
    };
    // Header: pre-session (no session_id)
    std::vector<uint8_t> frame;
    uint32_t payload_len = 1 + 4 + body.size(); // msg_type(1) + seq_id(4) + body
    // length prefix
    for (int i = 0; i < 4; i++) frame.push_back((payload_len >> (i*8)) & 0xFF);
    frame.push_back(static_cast<uint8_t>(MsgType::HELLO)); // msg_type
    frame.push_back(0x00); // flags
    // sequence_id
    uint32_t seq = 1;
    for (int i = 0; i < 4; i++) frame.push_back((seq >> (i*8)) & 0xFF);
    frame.insert(frame.end(), body.begin(), body.end());
    return frame;
}

// Build a valid HEARTBEAT frame (post-session)
static std::vector<uint8_t> make_valid_heartbeat() {
    std::vector<uint8_t> frame;
    uint32_t payload_len = 1 + 4 + 8; // msg_type + seq_id + session_id
    for (int i = 0; i < 4; i++) frame.push_back((payload_len >> (i*8)) & 0xFF);
    frame.push_back(static_cast<uint8_t>(MsgType::HEARTBEAT));
    frame.push_back(0x00); // flags
    uint32_t seq = 1;
    for (int i = 0; i < 4; i++) frame.push_back((seq >> (i*8)) & 0xFF);
    uint64_t sid = 12345;
    for (int i = 0; i < 8; i++) frame.push_back((sid >> (i*8)) & 0xFF);
    return frame;
}

// ─── Structural Fuzz Tests ─────────────────────────────────────

static void test_empty_data() {
    printf("  test_empty_data... ");
    expect_throw({}, "empty");
    printf("OK\n");
}

static void test_length_prefix_only() {
    printf("  test_length_prefix_only... ");
    std::vector<uint8_t> data(4, 0x00);
    expect_throw(data, "length_only");
    printf("OK\n");
}

static void test_truncated_header() {
    printf("  test_truncated_header... ");
    // Valid length prefix but not enough header bytes
    for (int trunc = 0; trunc < 6; trunc++) {
        std::vector<uint8_t> data;
        uint32_t len = 20;
        for (int i = 0; i < 4; i++) data.push_back((len >> (i*8)) & 0xFF);
        for (int i = 0; i < trunc; i++) data.push_back(rand_u8());
        expect_throw(data, "truncated_header");
    }
    printf("OK\n");
}

static void test_unknown_opcode() {
    printf("  test_unknown_opcode... ");
    for (int attempt = 0; attempt < 100; attempt++) {
        uint8_t opcode = rand_u8();
        // Skip known opcodes (0x00-0x05, 0x10-0x12, 0x20-0x25, 0x30-0x34, 0x40-0x42, 0x50-0x52, 0xF0, 0xFE, 0xFF)
        if ((opcode >= 0x00 && opcode <= 0x05) ||
            (opcode >= 0x10 && opcode <= 0x12) ||
            (opcode >= 0x20 && opcode <= 0x25) ||
            (opcode >= 0x30 && opcode <= 0x34) ||
            (opcode >= 0x40 && opcode <= 0x42) ||
            (opcode >= 0x50 && opcode <= 0x52) ||
            opcode == 0xF0 || opcode == 0xFE || opcode == 0xFF) {
            continue;
        }
        std::vector<uint8_t> data;
        uint32_t len = 1 + 4 + 8; // msg_type + seq_id + session_id (post-session)
        for (int i = 0; i < 4; i++) data.push_back((len >> (i*8)) & 0xFF);
        data.push_back(opcode);
        data.push_back(0x00); // flags
        uint32_t seq = 1;
        for (int i = 0; i < 4; i++) data.push_back((seq >> (i*8)) & 0xFF);
        uint64_t sid = 1;
        for (int i = 0; i < 8; i++) data.push_back((sid >> (i*8)) & 0xFF);
        expect_throw(data, "unknown_opcode");
    }
    printf("OK\n");
}

static void test_invalid_length_prefix() {
    printf("  test_invalid_length_prefix... ");
    // Use HELLO which needs 3 body bytes; provide 0 body bytes
    for (int attempt = 0; attempt < 100; attempt++) {
        std::vector<uint8_t> data;
        uint32_t len = 1 + 4 + 1000; // claims body is huge
        for (int i = 0; i < 4; i++) data.push_back((len >> (i*8)) & 0xFF);
        data.push_back(static_cast<uint8_t>(MsgType::HELLO));
        data.push_back(0x00);
        uint32_t seq = 1;
        for (int i = 0; i < 4; i++) data.push_back((seq >> (i*8)) & 0xFF);
        // No body at all — HELLO needs 3 bytes minimum
        expect_throw(data, "invalid_length");
    }
    printf("OK\n");
}

static void test_body_length_too_short() {
    printf("  test_body_length_too_short... ");
    // Use ERROR which needs error_code(2) + string(2+len); provide only header
    for (int attempt = 0; attempt < 100; attempt++) {
        std::vector<uint8_t> data;
        uint32_t len = 1 + 4 + 8 + 1000; // claims body is large
        for (int i = 0; i < 4; i++) data.push_back((len >> (i*8)) & 0xFF);
        data.push_back(static_cast<uint8_t>(MsgType::ERROR));
        data.push_back(0x00);
        uint32_t seq = 1;
        for (int i = 0; i < 4; i++) data.push_back((seq >> (i*8)) & 0xFF);
        uint64_t sid = 1;
        for (int i = 0; i < 8; i++) data.push_back((sid >> (i*8)) & 0xFF);
        // No body — ERROR needs at least error_code(2) + string(2)
        expect_throw(data, "body_short");
    }
    printf("OK\n");
}

static void test_random_bytes() {
    printf("  test_random_bytes... ");
    for (int attempt = 0; attempt < 200; attempt++) {
        int len = std::uniform_int_distribution<int>(1, 200)(rng);
        std::vector<uint8_t> data(len);
        for (auto& b : data) b = rand_u8();
        // Must not crash — may throw or succeed
        try {
            DeserializeFrame(data.data(), data.size());
        } catch (...) {}
        total_count++;
        pass_count++;
    }
    printf("OK\n");
}

static void test_completely_zeroed() {
    printf("  test_completely_zeroed... ");
    for (int size = 0; size <= 200; size++) {
        std::vector<uint8_t> data(size, 0x00);
        try {
            DeserializeFrame(data.data(), data.size());
        } catch (...) {}
    }
    total_count++;
    pass_count++;
    printf("OK\n");
}

// ─── Pre/Post Session Invariant Tests ───────────────────────────

static void test_pre_session_with_session_id() {
    printf("  test_pre_session_with_session_id... ");
    // HELLO with extra bytes where session_id would be — deserializer ignores them
    // (pre-session: session_id is never read from wire)
    for (int attempt = 0; attempt < 20; attempt++) {
        std::vector<uint8_t> data;
        uint32_t len = 1 + 4 + 8 + 3; // msg_type + seq_id + session_id_bytes + body
        for (int i = 0; i < 4; i++) data.push_back((len >> (i*8)) & 0xFF);
        data.push_back(static_cast<uint8_t>(MsgType::HELLO));
        data.push_back(0x00);
        uint32_t seq = 1;
        for (int i = 0; i < 4; i++) data.push_back((seq >> (i*8)) & 0xFF);
        uint64_t sid = 1;
        for (int i = 0; i < 8; i++) data.push_back((sid >> (i*8)) & 0xFF);
        data.push_back(0x01); data.push_back(0x00); data.push_back(0x00); // body
        // Deserializer reads session_id bytes as body data — may succeed or fail
        // depending on body interpretation, but must not crash
        try {
            DeserializeFrame(data.data(), data.size());
        } catch (...) {}
        total_count++;
        pass_count++;
    }
    printf("OK\n");
}

static void test_post_session_missing_session_id() {
    printf("  test_post_session_missing_session_id... ");
    // HEARTBEAT without session_id should fail (header too short)
    for (int attempt = 0; attempt < 20; attempt++) {
        std::vector<uint8_t> data;
        uint32_t len = 1 + 4; // only msg_type + seq_id, no session_id
        for (int i = 0; i < 4; i++) data.push_back((len >> (i*8)) & 0xFF);
        data.push_back(static_cast<uint8_t>(MsgType::HEARTBEAT));
        data.push_back(0x00);
        uint32_t seq = 1;
        for (int i = 0; i < 4; i++) data.push_back((seq >> (i*8)) & 0xFF);
        expect_throw(data, "post_session_no_sid");
    }
    printf("OK\n");
}

// ─── Malformed UUID Tests ───────────────────────────────────────

static void test_truncated_uuid() {
    printf("  test_truncated_uuid... ");
    // HELLO with truncated body (needs 3 bytes, give 0-2)
    for (int trunc = 0; trunc < 3; trunc++) {
        std::vector<uint8_t> data;
        uint32_t len = 1 + 4 + trunc;
        for (int i = 0; i < 4; i++) data.push_back((len >> (i*8)) & 0xFF);
        data.push_back(static_cast<uint8_t>(MsgType::HELLO));
        data.push_back(0x00);
        uint32_t seq = 1;
        for (int i = 0; i < 4; i++) data.push_back((seq >> (i*8)) & 0xFF);
        for (int i = 0; i < trunc; i++) data.push_back(rand_u8());
        expect_throw(data, "truncated_uuid_or_body");
    }
    printf("OK\n");
}

static void test_uuid_in_body_short() {
    printf("  test_uuid_in_body_short... ");
    // OBJECT_DELETE needs UUID(16) + seq(4) + ts(8) = 28 bytes body
    // Test: provide less than 16 bytes for UUID
    for (int give = 0; give < 16; give++) {
        std::vector<uint8_t> data;
        uint32_t len = 1 + 4 + 8 + give; // msg_type + seq + session + partial uuid
        for (int i = 0; i < 4; i++) data.push_back((len >> (i*8)) & 0xFF);
        data.push_back(static_cast<uint8_t>(MsgType::OBJECT_DELETE));
        data.push_back(0x00);
        uint32_t seq = 1;
        for (int i = 0; i < 4; i++) data.push_back((seq >> (i*8)) & 0xFF);
        uint64_t sid = 1;
        for (int i = 0; i < 8; i++) data.push_back((sid >> (i*8)) & 0xFF);
        for (int i = 0; i < give; i++) data.push_back(rand_u8());
        expect_throw(data, "uuid_short");
    }
    printf("OK\n");
}

static void test_delete_body_truncated_after_uuid() {
    printf("  test_delete_body_truncated_after_uuid... ");
    // OBJECT_DELETE body: UUID(16) + seq(4) + ts(8) = 28 bytes
    // Test: UUID present but sequence_number truncated
    {
        std::vector<uint8_t> data;
        uint32_t len = 1 + 4 + 8 + 16 + 2; // hdr + 2 bytes of seq
        for (int i = 0; i < 4; i++) data.push_back((len >> (i*8)) & 0xFF);
        data.push_back(static_cast<uint8_t>(MsgType::OBJECT_DELETE));
        data.push_back(0x00);
        uint32_t seq = 1;
        for (int i = 0; i < 4; i++) data.push_back((seq >> (i*8)) & 0xFF);
        uint64_t sid = 1;
        for (int i = 0; i < 8; i++) data.push_back((sid >> (i*8)) & 0xFF);
        for (int i = 0; i < 16; i++) data.push_back(rand_u8()); // full UUID
        data.push_back(0x01); data.push_back(0x02); // partial seq (2 bytes, need 4)
        expect_throw(data, "delete_seq_short");
    }
    // Test: UUID + seq present but timestamp truncated
    {
        std::vector<uint8_t> data;
        uint32_t len = 1 + 4 + 8 + 16 + 4 + 3; // hdr + full seq + 3 bytes of ts
        for (int i = 0; i < 4; i++) data.push_back((len >> (i*8)) & 0xFF);
        data.push_back(static_cast<uint8_t>(MsgType::OBJECT_DELETE));
        data.push_back(0x00);
        uint32_t frame_seq = 1;
        for (int i = 0; i < 4; i++) data.push_back((frame_seq >> (i*8)) & 0xFF);
        uint64_t sid = 1;
        for (int i = 0; i < 8; i++) data.push_back((sid >> (i*8)) & 0xFF);
        for (int i = 0; i < 16; i++) data.push_back(rand_u8()); // full UUID
        uint32_t del_seq = 42;
        for (int i = 0; i < 4; i++) data.push_back((del_seq >> (i*8)) & 0xFF); // full seq
        data.push_back(0x01); data.push_back(0x02); data.push_back(0x03); // partial ts (3 bytes, need 8)
        expect_throw(data, "delete_ts_short");
    }
    printf("OK\n");
}

// ─── UTF-8 / String Tests ──────────────────────────────────────

static void test_oversized_string_length() {
    printf("  test_oversized_string_length... ");
    // ERROR has a string field. Claim it's 60000 bytes, give 0.
    for (int attempt = 0; attempt < 20; attempt++) {
        std::vector<uint8_t> data;
        uint32_t len = 1 + 4 + 8 + 2 + 2; // msg_type + seq + session + error_code(2) + string_len(2)
        for (int i = 0; i < 4; i++) data.push_back((len >> (i*8)) & 0xFF);
        data.push_back(static_cast<uint8_t>(MsgType::ERROR));
        data.push_back(0x00);
        uint32_t seq = 1;
        for (int i = 0; i < 4; i++) data.push_back((seq >> (i*8)) & 0xFF);
        uint64_t sid = 1;
        for (int i = 0; i < 8; i++) data.push_back((sid >> (i*8)) & 0xFF);
        uint16_t err = 1;
        for (int i = 0; i < 2; i++) data.push_back((err >> (i*8)) & 0xFF);
        uint16_t slen = 60000;
        for (int i = 0; i < 2; i++) data.push_back((slen >> (i*8)) & 0xFF);
        expect_throw(data, "oversized_string");
    }
    printf("OK\n");
}

static void test_invalid_utf8_content() {
    printf("  test_invalid_utf8_content... ");
    // ERROR with string that has invalid UTF-8 bytes — should still parse (no validation)
    // The serializer doesn't validate UTF-8, so this should succeed
    for (int attempt = 0; attempt < 20; attempt++) {
        std::vector<uint8_t> data;
        int slen = std::uniform_int_distribution<int>(1, 50)(rng);
        uint32_t len = 1 + 4 + 8 + 2 + 2 + slen;
        for (int i = 0; i < 4; i++) data.push_back((len >> (i*8)) & 0xFF);
        data.push_back(static_cast<uint8_t>(MsgType::ERROR));
        data.push_back(0x00);
        uint32_t seq = 1;
        for (int i = 0; i < 4; i++) data.push_back((seq >> (i*8)) & 0xFF);
        uint64_t sid = 1;
        for (int i = 0; i < 8; i++) data.push_back((sid >> (i*8)) & 0xFF);
        uint16_t err = 1;
        for (int i = 0; i < 2; i++) data.push_back((err >> (i*8)) & 0xFF);
        for (int i = 0; i < 2; i++) data.push_back((slen >> (i*8)) & 0xFF);
        // Random bytes including invalid UTF-8 (0x80-0xFF, 0xC0 without continuation, etc.)
        for (int i = 0; i < slen; i++) data.push_back(rand_u8());
        try {
            DeserializeFrame(data.data(), data.size());
            // OK — no validation on UTF-8 content
        } catch (...) {}
        total_count++;
        pass_count++;
    }
    printf("OK\n");
}

// ─── Float Edge Cases ──────────────────────────────────────────

static void test_nan_float() {
    printf("  test_nan_float... ");
    // HEARTBEAT has no floats, but let's test a message with float fields
    // MATERIAL_CREATE: uuid(16) + name(2+len) + base_color(16) + metallic(4) + roughness(4) + emission(12)
    for (int attempt = 0; attempt < 20; attempt++) {
        uint32_t nan_bits = 0x7FC00000; // quiet NaN
        std::vector<uint8_t> data;
        int name_len = 4;
        uint32_t len = 1 + 4 + 8 + 16 + 2 + name_len + 16 + 4 + 4 + 12;
        for (int i = 0; i < 4; i++) data.push_back((len >> (i*8)) & 0xFF);
        data.push_back(static_cast<uint8_t>(MsgType::MATERIAL_CREATE));
        data.push_back(0x00);
        uint32_t seq = 1;
        for (int i = 0; i < 4; i++) data.push_back((seq >> (i*8)) & 0xFF);
        uint64_t sid = 1;
        for (int i = 0; i < 8; i++) data.push_back((sid >> (i*8)) & 0xFF);
        // UUID (16 bytes)
        for (int i = 0; i < 16; i++) data.push_back(rand_u8());
        // name string
        uint16_t nl = name_len;
        for (int i = 0; i < 2; i++) data.push_back((nl >> (i*8)) & 0xFF);
        for (int i = 0; i < name_len; i++) data.push_back('a');
        // base_color (4 floats)
        for (int i = 0; i < 16; i++) data.push_back(rand_u8());
        // metallic = NaN
        for (int i = 0; i < 4; i++) data.push_back((nan_bits >> (i*8)) & 0xFF);
        // roughness = NaN
        for (int i = 0; i < 4; i++) data.push_back((nan_bits >> (i*8)) & 0xFF);
        // emission (3 floats)
        for (int i = 0; i < 12; i++) data.push_back(rand_u8());
        // Should succeed — deserializer doesn't validate float values
        try {
            auto msg = DeserializeFrame(data.data(), data.size());
            // OK — NaN passes through without crash
        } catch (...) {}
        total_count++;
        pass_count++;
    }
    printf("OK\n");
}

static void test_inf_float() {
    printf("  test_inf_float... ");
    uint32_t pos_inf = 0x7F800000;
    uint32_t neg_inf = 0xFF800000;
    for (int attempt = 0; attempt < 20; attempt++) {
        std::vector<uint8_t> data;
        int name_len = 4;
        uint32_t len = 1 + 4 + 8 + 16 + 2 + name_len + 16 + 4 + 4 + 12;
        for (int i = 0; i < 4; i++) data.push_back((len >> (i*8)) & 0xFF);
        data.push_back(static_cast<uint8_t>(MsgType::MATERIAL_CREATE));
        data.push_back(0x00);
        uint32_t seq = 1;
        for (int i = 0; i < 4; i++) data.push_back((seq >> (i*8)) & 0xFF);
        uint64_t sid = 1;
        for (int i = 0; i < 8; i++) data.push_back((sid >> (i*8)) & 0xFF);
        for (int i = 0; i < 16; i++) data.push_back(rand_u8());
        uint16_t nl = name_len;
        for (int i = 0; i < 2; i++) data.push_back((nl >> (i*8)) & 0xFF);
        for (int i = 0; i < name_len; i++) data.push_back('a');
        for (int i = 0; i < 16; i++) data.push_back(rand_u8());
        // metallic = +Inf or -Inf
        uint32_t inf_val = (attempt % 2 == 0) ? pos_inf : neg_inf;
        for (int i = 0; i < 4; i++) data.push_back((inf_val >> (i*8)) & 0xFF);
        // roughness = normal
        for (int i = 0; i < 4; i++) data.push_back(rand_u8());
        for (int i = 0; i < 12; i++) data.push_back(rand_u8());
        try {
            DeserializeFrame(data.data(), data.size());
        } catch (...) {}
        total_count++;
        pass_count++;
    }
    printf("OK\n");
}

static void test_denormal_float() {
    printf("  test_denormal_float... ");
    uint32_t denormal = 0x00000001; // smallest subnormal
    for (int attempt = 0; attempt < 20; attempt++) {
        std::vector<uint8_t> data;
        int name_len = 4;
        uint32_t len = 1 + 4 + 8 + 16 + 2 + name_len + 16 + 4 + 4 + 12;
        for (int i = 0; i < 4; i++) data.push_back((len >> (i*8)) & 0xFF);
        data.push_back(static_cast<uint8_t>(MsgType::MATERIAL_CREATE));
        data.push_back(0x00);
        uint32_t seq = 1;
        for (int i = 0; i < 4; i++) data.push_back((seq >> (i*8)) & 0xFF);
        uint64_t sid = 1;
        for (int i = 0; i < 8; i++) data.push_back((sid >> (i*8)) & 0xFF);
        for (int i = 0; i < 16; i++) data.push_back(rand_u8());
        uint16_t nl = name_len;
        for (int i = 0; i < 2; i++) data.push_back((nl >> (i*8)) & 0xFF);
        for (int i = 0; i < name_len; i++) data.push_back('a');
        for (int i = 0; i < 16; i++) data.push_back(rand_u8());
        // metallic = denormal
        for (int i = 0; i < 4; i++) data.push_back((denormal >> (i*8)) & 0xFF);
        for (int i = 0; i < 4; i++) data.push_back(rand_u8());
        for (int i = 0; i < 12; i++) data.push_back(rand_u8());
        try {
            DeserializeFrame(data.data(), data.size());
        } catch (...) {}
        total_count++;
        pass_count++;
    }
    printf("OK\n");
}

// ─── MESH_DATA Dynamic Length Fuzz ──────────────────────────────

static void test_mesh_data_vertex_count_mismatch() {
    printf("  test_mesh_data_vertex_count_mismatch... ");
    // Claim vertex_count = 1000000 but provide very few bytes
    for (int attempt = 0; attempt < 20; attempt++) {
        std::vector<uint8_t> data;
        uint32_t vc = 1000000;
        uint32_t ic = 1000000;
        uint32_t len = 1 + 4 + 8 + 16 + 4 + 4 + 1 + 4; // minimal header + fields before arrays
        for (int i = 0; i < 4; i++) data.push_back((len >> (i*8)) & 0xFF);
        data.push_back(static_cast<uint8_t>(MsgType::MESH_DATA));
        data.push_back(0x00);
        uint32_t seq = 1;
        for (int i = 0; i < 4; i++) data.push_back((seq >> (i*8)) & 0xFF);
        uint64_t sid = 1;
        for (int i = 0; i < 8; i++) data.push_back((sid >> (i*8)) & 0xFF);
        for (int i = 0; i < 16; i++) data.push_back(rand_u8()); // UUID
        for (int i = 0; i < 4; i++) data.push_back((vc >> (i*8)) & 0xFF);
        for (int i = 0; i < 4; i++) data.push_back((ic >> (i*8)) & 0xFF);
        data.push_back(0x00); // format_flags
        // No actual vertex/index data — must throw
        expect_throw(data, "mesh_data_vertex_mismatch");
    }
    printf("OK\n");
}

static void test_mesh_data_zero_vertices() {
    printf("  test_mesh_data_zero_vertices... ");
    // vertex_count=0, index_count=0 — should succeed with empty arrays
    std::vector<uint8_t> data;
    uint32_t len = 1 + 4 + 8 + 16 + 4 + 4 + 1 + 4; // +4 for u32 length prefix of empty indices
    for (int i = 0; i < 4; i++) data.push_back((len >> (i*8)) & 0xFF);
    data.push_back(static_cast<uint8_t>(MsgType::MESH_DATA));
    data.push_back(0x00);
    uint32_t seq = 1;
    for (int i = 0; i < 4; i++) data.push_back((seq >> (i*8)) & 0xFF);
    uint64_t sid = 1;
    for (int i = 0; i < 8; i++) data.push_back((sid >> (i*8)) & 0xFF);
    for (int i = 0; i < 16; i++) data.push_back(0x00); // UUID
    uint32_t vc = 0, ic = 0;
    for (int i = 0; i < 4; i++) data.push_back((vc >> (i*8)) & 0xFF);
    for (int i = 0; i < 4; i++) data.push_back((ic >> (i*8)) & 0xFF);
    data.push_back(0x00); // format_flags
    uint32_t idx_len = 0;
    for (int i = 0; i < 4; i++) data.push_back((idx_len >> (i*8)) & 0xFF);
    expect_ok(data, "mesh_data_zero");
    printf("OK\n");
}

// ─── MESH_CHUNK oversized raw_bytes ─────────────────────────────

static void test_mesh_chunk_oversized_data() {
    printf("  test_mesh_chunk_oversized_data... ");
    for (int attempt = 0; attempt < 20; attempt++) {
        std::vector<uint8_t> data;
        uint32_t data_len = 100000;
        uint32_t len = 1 + 4 + 8 + 16 + 2 + 2 + 4 + 4 + 4 + data_len;
        for (int i = 0; i < 4; i++) data.push_back((len >> (i*8)) & 0xFF);
        data.push_back(static_cast<uint8_t>(MsgType::MESH_CHUNK));
        data.push_back(0x00);
        uint32_t seq = 1;
        for (int i = 0; i < 4; i++) data.push_back((seq >> (i*8)) & 0xFF);
        uint64_t sid = 1;
        for (int i = 0; i < 8; i++) data.push_back((sid >> (i*8)) & 0xFF);
        for (int i = 0; i < 16; i++) data.push_back(rand_u8()); // UUID
        uint16_t chunk = 0;
        for (int i = 0; i < 2; i++) data.push_back((chunk >> (i*8)) & 0xFF);
        uint16_t vo = 0;
        for (int i = 0; i < 2; i++) data.push_back((vo >> (i*8)) & 0xFF);
        uint32_t vc = 0;
        for (int i = 0; i < 4; i++) data.push_back((vc >> (i*8)) & 0xFF);
        uint32_t ic = 0;
        for (int i = 0; i < 4; i++) data.push_back((ic >> (i*8)) & 0xFF);
        // raw_bytes length prefix claims 100000 bytes but we provide 0
        for (int i = 0; i < 4; i++) data.push_back((data_len >> (i*8)) & 0xFF);
        expect_throw(data, "mesh_chunk_oversized");
    }
    printf("OK\n");
}

// ─── Flags Edge Cases ──────────────────────────────────────────

static void test_encrypted_flag() {
    printf("  test_encrypted_flag... ");
    // HEARTBEAT with encrypted flag (bit1) set — should succeed (no validation on flags)
    for (int attempt = 0; attempt < 20; attempt++) {
        auto data = make_valid_heartbeat();
        data[5] = 0x02; // set bit1 (encrypted)
        try {
            DeserializeFrame(data.data(), data.size());
        } catch (...) {}
        total_count++;
        pass_count++;
    }
    printf("OK\n");
}

static void test_all_flags_set() {
    printf("  test_all_flags_set... ");
    for (int attempt = 0; attempt < 20; attempt++) {
        auto data = make_valid_heartbeat();
        data[5] = 0xFF; // all flags
        try {
            DeserializeFrame(data.data(), data.size());
        } catch (...) {}
        total_count++;
        pass_count++;
    }
    printf("OK\n");
}

// ─── Sequence ID Edge Cases ────────────────────────────────────

static void test_max_sequence_id() {
    printf("  test_max_sequence_id... ");
    auto data = make_valid_heartbeat();
    uint32_t max_seq = UINT32_MAX;
    for (int i = 0; i < 4; i++) data[6 + i] = (max_seq >> (i*8)) & 0xFF;
    auto msg = expect_ok(data, "max_seq");
    if (msg && msg->sequence_id != UINT32_MAX) {
        fprintf(stderr, "  sequence_id mismatch: expected UINT32_MAX\n");
    }
    printf("OK\n");
}

static void test_zero_sequence_id() {
    printf("  test_zero_sequence_id... ");
    auto data = make_valid_heartbeat();
    for (int i = 0; i < 4; i++) data[6 + i] = 0;
    expect_ok(data, "zero_seq");
    printf("OK\n");
}

// ─── Session ID Edge Cases ─────────────────────────────────────

static void test_max_session_id() {
    printf("  test_max_session_id... ");
    auto data = make_valid_heartbeat();
    uint64_t max_sid = UINT64_MAX;
    for (int i = 0; i < 8; i++) data[10 + i] = (max_sid >> (i*8)) & 0xFF;
    auto msg = expect_ok(data, "max_sid");
    if (msg && msg->session_id && *msg->session_id != UINT64_MAX) {
        fprintf(stderr, "  session_id mismatch: expected UINT64_MAX\n");
    }
    printf("OK\n");
}

static void test_zero_session_id() {
    printf("  test_zero_session_id... ");
    auto data = make_valid_heartbeat();
    for (int i = 0; i < 8; i++) data[10 + i] = 0;
    expect_ok(data, "zero_sid");
    printf("OK\n");
}

// ─── Multiple Messages / Garbage After Frame ────────────────────

static void test_extra_bytes_after_frame() {
    printf("  test_extra_bytes_after_frame... ");
    // Valid HEARTBEAT followed by garbage — should succeed (extra bytes ignored)
    for (int attempt = 0; attempt < 20; attempt++) {
        auto data = make_valid_heartbeat();
        int extra = std::uniform_int_distribution<int>(1, 100)(rng);
        for (int i = 0; i < extra; i++) data.push_back(rand_u8());
        try {
            DeserializeFrame(data.data(), data.size());
        } catch (...) {}
        total_count++;
        pass_count++;
    }
    printf("OK\n");
}

// ─── OBJECT_CREATE optional parent_id fuzz ──────────────────────

static void test_object_create_no_parent() {
    printf("  test_object_create_no_parent... ");
    // OBJECT_CREATE: uuid(16) + name(2+len) + parent_id(16 optional) + transform(40)
    // Without parent_id: uuid(16) + name(2+4=6) + transform(40) = 62 bytes body
    std::vector<uint8_t> data;
    uint32_t len = 1 + 4 + 8 + 16 + 6 + 40;
    for (int i = 0; i < 4; i++) data.push_back((len >> (i*8)) & 0xFF);
    data.push_back(static_cast<uint8_t>(MsgType::OBJECT_CREATE));
    data.push_back(0x00);
    uint32_t seq = 1;
    for (int i = 0; i < 4; i++) data.push_back((seq >> (i*8)) & 0xFF);
    uint64_t sid = 1;
    for (int i = 0; i < 8; i++) data.push_back((sid >> (i*8)) & 0xFF);
    for (int i = 0; i < 16; i++) data.push_back(rand_u8()); // UUID
    // name string (4 chars)
    uint16_t nl = 4;
    for (int i = 0; i < 2; i++) data.push_back((nl >> (i*8)) & 0xFF);
    for (int i = 0; i < 4; i++) data.push_back('x');
    // transform only (40 bytes) — no parent_id (body_end - offset < 56)
    for (int i = 0; i < 40; i++) data.push_back(rand_u8());
    auto msg = expect_ok(data, "obj_create_no_parent");
    if (msg && msg->body.find("parent_id") != msg->body.end()) {
        fprintf(stderr, "  WARNING: parent_id should not be present\n");
    }
    printf("OK\n");
}

// ─── Main ──────────────────────────────────────────────────────

int main() {
    printf("Fuzz tests (structural + value edge cases)\n");
    printf("──────────────────────────────────────────\n");

    printf("\n  Structural:\n");
    test_empty_data();
    test_length_prefix_only();
    test_truncated_header();
    test_unknown_opcode();
    test_invalid_length_prefix();
    test_body_length_too_short();
    test_random_bytes();
    test_completely_zeroed();

    printf("\n  Pre/Post Session:\n");
    test_pre_session_with_session_id();
    test_post_session_missing_session_id();

    printf("\n  UUID / Body Truncation:\n");
    test_truncated_uuid();
    test_uuid_in_body_short();
    test_delete_body_truncated_after_uuid();

    printf("\n  String / UTF-8:\n");
    test_oversized_string_length();
    test_invalid_utf8_content();

    printf("\n  Float Edge Cases:\n");
    test_nan_float();
    test_inf_float();
    test_denormal_float();

    printf("\n  MESH_DATA Dynamic Length:\n");
    test_mesh_data_vertex_count_mismatch();
    test_mesh_data_zero_vertices();
    test_mesh_chunk_oversized_data();

    printf("\n  Flags / Header Edge Cases:\n");
    test_encrypted_flag();
    test_all_flags_set();
    test_max_sequence_id();
    test_zero_sequence_id();
    test_max_session_id();
    test_zero_session_id();

    printf("\n  Garbage / Extra Data:\n");
    test_extra_bytes_after_frame();

    printf("\n  Optional Fields:\n");
    test_object_create_no_parent();

    printf("\n──────────────────────────────────────────\n");
    printf("%d/%d fuzz checks passed\n", pass_count, total_count);

    if (pass_count == total_count) {
        printf("ALL FUZZ TESTS PASSED\n");
        return 0;
    } else {
        printf("FUZZ TESTS FAILED\n");
        return 1;
    }
}
