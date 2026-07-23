/**
 * Raw-input correctness tests for C++ serializer primitives.
 *
 * These tests verify individual packers against known byte sequences,
 * independent of the manifest or golden vectors.
 *
 * Build: g++ -std=c++20 -O2 -o test_primitives test_primitives.cpp
 * Run:   ./test_primitives
 */

#include "livesync_serializer.h"

#include <cstdio>
#include <cstring>
#include <cmath>

using namespace livesync;

static int passed = 0;
static int failed = 0;

static void check(const char* name, const std::vector<uint8_t>& got, const std::vector<uint8_t>& expected) {
    if (got == expected) {
        printf("  PASS  %s\n", name);
        passed++;
    } else {
        printf("  FAIL  %s\n", name);
        printf("    expected %zu bytes: ", expected.size());
        for (auto b : expected) printf("%02x ", b);
        printf("\n    got      %zu bytes: ", got.size());
        for (auto b : got) printf("%02x ", b);
        printf("\n");
        failed++;
    }
}

int main() {
    std::vector<uint8_t> buf;

    // ── uint8 ──
    buf.clear();
    pack_uint8(buf, 0x00);
    check("uint8(0x00)", buf, {0x00});

    buf.clear();
    pack_uint8(buf, 0xFF);
    check("uint8(0xFF)", buf, {0xFF});

    // ── uint16 LE ──
    buf.clear();
    pack_uint16(buf, 0x0102);
    check("uint16(0x0102) LE", buf, {0x02, 0x01});

    buf.clear();
    pack_uint16(buf, 0xABCD);
    check("uint16(0xABCD) LE", buf, {0xCD, 0xAB});

    // ── uint32 LE ──
    buf.clear();
    pack_uint32(buf, 0x01020304);
    check("uint32(0x01020304) LE", buf, {0x04, 0x03, 0x02, 0x01});

    // ── uint64 LE ──
    buf.clear();
    pack_uint64(buf, 0x0102030405060708ULL);
    check("uint64(0x01..08) LE", buf, {0x08, 0x07, 0x06, 0x05, 0x04, 0x03, 0x02, 0x01});

    // ── float32 LE canonicalization ──
    // +1.0 = 0x3F800000 → bytes 00 00 80 3F
    buf.clear();
    pack_float32(buf, 1.0f);
    check("float32(1.0) LE", buf, {0x00, 0x00, 0x80, 0x3F});

    // -0.0 → +0.0 = 0x00000000 → bytes 00 00 00 00
    buf.clear();
    pack_float32(buf, -0.0f);
    check("float32(-0.0) → +0.0", buf, {0x00, 0x00, 0x00, 0x00});

    // NaN → rejected
    buf.clear();
    bool nan_rejected = false;
    try {
        pack_float32(buf, std::nanf(""));
    } catch (const std::invalid_argument&) {
        nan_rejected = true;
    }
    check("float32(NaN) rejected", {static_cast<uint8_t>(nan_rejected ? 1 : 0)}, {0x01});

    // ── UUID ──
    buf.clear();
    uint8_t uuid_bytes[16] = {
        0x00, 0x11, 0x22, 0x33, 0x44, 0x55, 0x66, 0x77,
        0x88, 0x99, 0xAA, 0xBB, 0xCC, 0xDD, 0xEE, 0xFF
    };
    pack_uuid(buf, uuid_bytes);
    check("UUID 00112233-...", buf,
          {0x00, 0x11, 0x22, 0x33, 0x44, 0x55, 0x66, 0x77,
           0x88, 0x99, 0xAA, 0xBB, 0xCC, 0xDD, 0xEE, 0xFF});

    // ── utf8_string ──
    buf.clear();
    pack_utf8_string(buf, "Hi");
    // 'H' = 0x48, 'i' = 0x69, length = 2 → LE: 02 00
    check("utf8_string(\"Hi\")", buf, {0x02, 0x00, 0x48, 0x69});

    buf.clear();
    pack_utf8_string(buf, "");
    check("utf8_string(\"\") empty", buf, {0x00, 0x00});

    // ── transform3d (identity) ──
    buf.clear();
    pack_transform3d(buf,
        0.0f, 0.0f, 0.0f,   // position
        0.0f, 0.0f, 0.0f, 1.0f,  // quaternion (identity)
        1.0f, 1.0f, 1.0f);  // scale
    // 10 × float32 LE = 40 bytes
    // position: 00 00 00 00 × 3
    // quaternion: 00 00 00 00 × 3, 00 00 80 3F (w=1.0)
    // scale: 00 00 80 3F × 3
    check("transform3d(identity) size", {static_cast<uint8_t>(buf.size())}, {40});
    check("transform3d(identity) bytes", buf,
          {0x00,0x00,0x00,0x00, 0x00,0x00,0x00,0x00, 0x00,0x00,0x00,0x00,  // px,py,pz
           0x00,0x00,0x00,0x00, 0x00,0x00,0x00,0x00, 0x00,0x00,0x00,0x00, 0x00,0x00,0x80,0x3F,  // rx,ry,rz,rw
           0x00,0x00,0x80,0x3F, 0x00,0x00,0x80,0x3F, 0x00,0x00,0x80,0x3F}); // sx,sy,sz

    // ── f32_array ──
    buf.clear();
    float vals[] = {1.0f, 2.0f, 3.0f};
    pack_f32_array(buf, vals);
    check("f32_array(1.0,2.0,3.0)", buf,
          {0x00,0x00,0x80,0x3F, 0x00,0x00,0x00,0x40, 0x00,0x00,0x40,0x40});

    // ── u32_array ──
    buf.clear();
    uint32_t uvals[] = {100, 200};
    pack_u32_array(buf, uvals);
    // length = 2 → LE: 02 00 00 00
    check("u32_array(100,200)", buf,
          {0x02,0x00,0x00,0x00, 0x64,0x00,0x00,0x00, 0xC8,0x00,0x00,0x00});

    // ── raw_bytes ──
    buf.clear();
    uint8_t raw[] = {0xDE, 0xAD, 0xBE, 0xEF};
    pack_raw_bytes(buf, raw);
    // length = 4 → LE: 04 00 00 00
    check("raw_bytes(4 bytes)", buf,
          {0x04,0x00,0x00,0x00, 0xDE,0xAD,0xBE,0xEF});

    // ── Header: pre-session ──
    buf.clear();
    pack_header(buf, MsgType::HELLO, 0x00, 0, std::nullopt);
    // MsgType=0x10, Flags=0x00, SequenceId=0x00000000 LE
    check("header(HELLO) 6 bytes", buf,
          {0x10, 0x00, 0x00,0x00,0x00,0x00});

    // ── Header: post-session ──
    buf.clear();
    pack_header(buf, MsgType::HEARTBEAT, 0x00, 1, uint64_t(0xDEADBEEFCAFEBABE));
    // MsgType=0x00, Flags=0x00, SeqId=1, SessionId=0xDEADBEEFCAFEBABE LE
    check("header(HEARTBEAT) 14 bytes", buf,
          {0x00, 0x00, 0x01,0x00,0x00,0x00, 0xBE,0xBA,0xFE,0xCA,0xEF,0xBE,0xAD,0xDE});

    // ── Frame: HELLO ──
    buf.clear();
    std::vector<uint8_t> hello_body = {0x02, 0x00, 0x07,0x00,0x00,0x00,0x00,0x00,0x00,0x00};
    auto frame = pack_frame(MsgType::HELLO, 0, 0, std::nullopt, hello_body);
    // length = 6 + 10 = 16 → LE: 10 00 00 00
    check("frame(HELLO) length prefix", {static_cast<uint8_t>(frame[0]), frame[1], frame[2], frame[3]},
          {0x10, 0x00, 0x00, 0x00});

    // ── Quaternion normalization ──
    // Unnormalized (0, 0, 0, 2) → should normalize to (0, 0, 0, 1)
    buf.clear();
    pack_transform3d(buf, 0,0,0, 0,0,0,2, 1,1,1);
    // rw should be 0x3F800000 (1.0) at offset 24
    uint32_t rw_bits;
    std::memcpy(&rw_bits, &buf[24], 4);
    check("quaternion normalize (0,0,0,2)→rw=1.0", {static_cast<uint8_t>(rw_bits == 0x3F800000 ? 1 : 0)}, {0x01});

    // Degenerate quaternion (0,0,0,0) → identity (0,0,0,1)
    buf.clear();
    pack_transform3d(buf, 5,6,7, 0,0,0,0, 1,1,1);
    // rx=offset12 should be 0, rw=offset24 should be 1.0
    uint32_t rx_bits;
    std::memcpy(&rx_bits, &buf[12], 4);
    std::memcpy(&rw_bits, &buf[24], 4);
    check("quaternion degenerate→identity rx=0", {static_cast<uint8_t>(rx_bits == 0x00000000 ? 1 : 0)}, {0x01});
    check("quaternion degenerate→identity rw=1", {static_cast<uint8_t>(rw_bits == 0x3F800000 ? 1 : 0)}, {0x01});

    // Position should be preserved (5,6,7)
    float px;
    std::memcpy(&px, &buf[0], 4);
    check("transform preserves position px=5.0", {static_cast<uint8_t>(px == 5.0f ? 1 : 0)}, {0x01});

    // ── Summary ──
    printf("\n==================================================\n");
    if (failed == 0) {
        printf("ALL %d PRIMITIVE TESTS PASSED\n", passed);
        return 0;
    } else {
        printf("FAILED: %d/%d tests failed\n", failed, passed + failed);
        return 1;
    }
}
