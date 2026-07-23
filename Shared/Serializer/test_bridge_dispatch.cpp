/**
 * Bridge dispatch tests for LiveSync protocol.
 *
 * Tests DispatchMsgTypePacket() with golden vectors:
 *   4 handshake messages -> Handled
 *   1 unimplemented MsgType -> Unsupported
 *
 * Build: g++ -std=c++20 -O2 -DUELIVESYNC_BRIDGE_TESTING -I. \
 *        -o test_bridge_dispatch test_bridge_dispatch.cpp
 * Run:   ./test_bridge_dispatch <vectors_dir>
 */

// UELIVESYNC_BRIDGE_TESTING is set via -D flag in compilation
#include "../../UE_Plugin/UELiveSync/Source/UELiveSync/Public/LiveSyncProtocolBridge.h"

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <vector>
#include <string>
#include <fstream>

using namespace LiveSyncBridge;

static int passed = 0;
static int failed = 0;

static std::vector<uint8> read_file(const char* path)
{
    std::ifstream f(path, std::ios::binary | std::ios::ate);
    if (!f.is_open()) return {};
    std::streamsize sz = f.tellg();
    if (sz <= 0) return {};
    f.seekg(0, std::ios::beg);
    std::vector<uint8> buf(static_cast<size_t>(sz));
    if (!f.read(reinterpret_cast<char*>(buf.data()), sz)) return {};
    return buf;
}

static void check_result(
    const char* test_name,
    EDispatchResult got,
    EDispatchResult expected,
    int expected_calls,
    int actual_calls)
{
    if (got == expected && actual_calls == expected_calls)
    {
        printf("  PASS  %s -> %s (calls=%d)\n",
            test_name, DispatchResultToString(got), actual_calls);
        passed++;
    }
    else
    {
        printf("  FAIL  %s\n", test_name);
        printf("    expected result: %s\n",
            DispatchResultToString(expected));
        printf("    got result:      %s\n",
            DispatchResultToString(got));
        printf("    expected calls:  %d\n", expected_calls);
        printf("    actual calls:    %d\n", actual_calls);
        failed++;
    }
}

static void check_no_violation(
    const char* test_name,
    EDispatchResult got)
{
    if (got != EDispatchResult::ParseError &&
        got != EDispatchResult::ProtocolViolation)
    {
        printf("  PASS  %s -> no parse error or violation (%s)\n",
            test_name, DispatchResultToString(got));
        passed++;
    }
    else
    {
        printf("  FAIL  %s -> unexpected %s\n",
            test_name, DispatchResultToString(got));
        failed++;
    }
}

int main(int argc, char** argv)
{
    if (argc < 2)
    {
        fprintf(stderr, "Usage: %s <vectors_dir>\n", argv[0]);
        return 1;
    }

    std::string dir = argv[1];
    if (dir.back() != '/') dir += '/';

    printf("=== Bridge Dispatch Tests ===\n\n");

    // ── Test 1: HELLO -> Handled ──────────────────────────
    {
        auto buf = read_file((dir + "HELLO.bin").c_str());
        if (buf.empty()) { printf("  SKIP  HELLO.bin not found\n"); }
        else
        {
            ResetAllCounters();
            auto r = DispatchMsgTypePacket(buf.data(),
                static_cast<int32>(buf.size()));
            check_result("HELLO", r,
                EDispatchResult::Handled, 1, g_hello_calls);
        }
    }

    // ── Test 2: HELLO_ACK -> Handled ──────────────────────
    {
        auto buf = read_file((dir + "HELLO_ACK.bin").c_str());
        if (buf.empty()) { printf("  SKIP  HELLO_ACK.bin not found\n"); }
        else
        {
            ResetAllCounters();
            auto r = DispatchMsgTypePacket(buf.data(),
                static_cast<int32>(buf.size()));
            check_result("HELLO_ACK", r,
                EDispatchResult::Handled, 1, g_helloack_calls);
        }
    }

    // ── Test 3: HEARTBEAT -> Handled ──────────────────────
    {
        auto buf = read_file((dir + "HEARTBEAT.bin").c_str());
        if (buf.empty()) { printf("  SKIP  HEARTBEAT.bin not found\n"); }
        else
        {
            ResetAllCounters();
            auto r = DispatchMsgTypePacket(buf.data(),
                static_cast<int32>(buf.size()));
            check_result("HEARTBEAT", r,
                EDispatchResult::Handled, 1, g_heartbeat_calls);
        }
    }

    // ── Test 4: HEARTBEAT_ACK -> Handled ──────────────────
    {
        auto buf = read_file((dir + "HEARTBEAT_ACK.bin").c_str());
        if (buf.empty()) { printf("  SKIP  HEARTBEAT_ACK.bin not found\n"); }
        else
        {
            ResetAllCounters();
            auto r = DispatchMsgTypePacket(buf.data(),
                static_cast<int32>(buf.size()));
            check_result("HEARTBEAT_ACK", r,
                EDispatchResult::Handled, 1, g_heartbeatack_calls);
        }
    }

    // ── Test 5: OBJECT_CREATE -> Handled ──────────────────
    {
        auto buf = read_file((dir + "OBJECT_CREATE.bin").c_str());
        if (buf.empty()) { printf("  SKIP  OBJECT_CREATE.bin not found\n"); }
        else
        {
            ResetAllCounters();
            auto r = DispatchMsgTypePacket(buf.data(),
                static_cast<int32>(buf.size()));
            check_result("OBJECT_CREATE", r,
                EDispatchResult::Handled, 1, g_objectcreate_calls);
            check_no_violation("OBJECT_CREATE (no violation)", r);
        }
    }

    // ── Test 6: OBJECT_UPDATE -> Handled ──────────────────
    {
        auto buf = read_file((dir + "OBJECT_UPDATE.bin").c_str());
        if (buf.empty()) { printf("  SKIP  OBJECT_UPDATE.bin not found\n"); }
        else
        {
            ResetAllCounters();
            auto r = DispatchMsgTypePacket(buf.data(),
                static_cast<int32>(buf.size()));
            check_result("OBJECT_UPDATE", r,
                EDispatchResult::Handled, 1, g_objectupdate_calls);
            check_no_violation("OBJECT_UPDATE (no violation)", r);
        }
    }

    // ── Test 7: OBJECT_DELETE -> Handled ──────────────────
    {
        auto buf = read_file((dir + "OBJECT_DELETE.bin").c_str());
        if (buf.empty()) { printf("  SKIP  OBJECT_DELETE.bin not found\n"); }
        else
        {
            ResetAllCounters();
            auto r = DispatchMsgTypePacket(buf.data(),
                static_cast<int32>(buf.size()));
            check_result("OBJECT_DELETE", r,
                EDispatchResult::Handled, 1, g_objectdelete_calls);
            check_no_violation("OBJECT_DELETE (no violation)", r);
        }
    }

    // ── Test 8: Empty buffer -> ParseError ────────────────
    {
        uint8 tiny[2] = {0x00, 0x00};
        auto r = DispatchMsgTypePacket(tiny, 2);
        check_result("tiny_buffer", r,
            EDispatchResult::ParseError, 0, 0);
    }

    // ── Test 9: Zero bytes -> ParseError ──────────────────
    {
        auto r = DispatchMsgTypePacket(nullptr, 0);
        check_result("zero_size", r,
            EDispatchResult::ParseError, 0, 0);
    }

    // ── Summary ───────────────────────────────────────────
    printf("\n=== Summary ===\n");
    int total = passed + failed;
    if (failed == 0)
    {
        printf("ALL %d TESTS PASSED\n", total);
    }
    else
    {
        printf("%d/%d TESTS FAILED\n", failed, total);
    }

    return failed;
}
