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

// =========================================================
// Shared FakeGameplaySink fixture — covers all migrated messages
// =========================================================

struct FakeGameplaySink : IGameplaySink
{
    // OBJECT_CREATE
    int ObjectCreateCalls = 0;
    ObjectCreateView LastObjectCreate{};

    // OBJECT_DELETE
    int ObjectDeleteCalls = 0;
    ObjectDeleteView LastObjectDelete{};

    // OBJECT_UPDATE
    int ObjectUpdateCalls = 0;
    ObjectUpdateView LastObjectUpdate{};

    // OBJECT_RENAME
    int ObjectRenameCalls = 0;
    ObjectRenameView LastObjectRename{};

    // OBJECT_REPARENT
    int ObjectReparentCalls = 0;
    ObjectReparentView LastObjectReparent{};

    // OBJECT_VISIBILITY
    int ObjectVisibilityCalls = 0;
    ObjectVisibilityView LastObjectVisibility{};

    // CAMERA_SETACTIVE
    int CameraSetActiveCalls = 0;
    CameraSetActiveView LastCameraSetActive{};

    // CAMERA_UPDATE
    int CameraUpdateCalls = 0;
    CameraUpdateView LastCameraUpdate{};

    // MESH_START
    int MeshStartCalls = 0;
    MeshStartView LastMeshStart{};

    // MESH_CHUNK
    int MeshChunkCalls = 0;
    MeshChunkView LastMeshChunk{};

    // MESH_END
    int MeshEndCalls = 0;
    MeshEndView LastMeshEnd{};

    // MESH_DATA
    int MeshDataCalls = 0;
    MeshDataView LastMeshData{};

    // MESH_DELTA
    int MeshDeltaCalls = 0;
    MeshDeltaView LastMeshDelta{};

    void OnObjectCreate(const ObjectCreateView& View) override
    {
        ++ObjectCreateCalls;
        LastObjectCreate = View;
    }

    void OnObjectDelete(const ObjectDeleteView& View) override
    {
        ++ObjectDeleteCalls;
        LastObjectDelete = View;
    }

    void OnObjectUpdate(const ObjectUpdateView& View) override
    {
        ++ObjectUpdateCalls;
        LastObjectUpdate = View;
    }

    void OnObjectRename(const ObjectRenameView& View) override
    {
        ++ObjectRenameCalls;
        LastObjectRename = View;
    }

    void OnObjectReparent(const ObjectReparentView& View) override
    {
        ++ObjectReparentCalls;
        LastObjectReparent = View;
    }

    void OnObjectVisibility(const ObjectVisibilityView& View) override
    {
        ++ObjectVisibilityCalls;
        LastObjectVisibility = View;
    }

    void OnCameraSetActive(const CameraSetActiveView& View) override
    {
        ++CameraSetActiveCalls;
        LastCameraSetActive = View;
    }

    void OnCameraUpdate(const CameraUpdateView& View) override
    {
        ++CameraUpdateCalls;
        LastCameraUpdate = View;
    }

    void OnMeshStart(const MeshStartView& View) override
    {
        ++MeshStartCalls;
        LastMeshStart = View;
    }

    void OnMeshChunk(const MeshChunkView& View) override
    {
        ++MeshChunkCalls;
        LastMeshChunk = View;
    }

    void OnMeshEnd(const MeshEndView& View) override
    {
        ++MeshEndCalls;
        LastMeshEnd = View;
    }

    void OnMeshData(const MeshDataView& View) override
    {
        ++MeshDataCalls;
        LastMeshData = View;
    }

    void OnMeshDelta(const MeshDeltaView& View) override
    {
        ++MeshDeltaCalls;
        LastMeshDelta = View;
    }
};

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
                static_cast<int32>(buf.size()),
                DispatchContext{});
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
                static_cast<int32>(buf.size()),
                DispatchContext{});
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
                static_cast<int32>(buf.size()),
                DispatchContext{});
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
                static_cast<int32>(buf.size()),
                DispatchContext{});
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
                static_cast<int32>(buf.size()),
                DispatchContext{});
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
                static_cast<int32>(buf.size()),
                DispatchContext{});
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
                static_cast<int32>(buf.size()),
                DispatchContext{});
            check_result("OBJECT_DELETE", r,
                EDispatchResult::Handled, 1, g_objectdelete_calls);
            check_no_violation("OBJECT_DELETE (no violation)", r);
        }
    }

    // ── Test 8: OBJECT_RENAME -> Handled ──────────────────
    {
        auto buf = read_file((dir + "OBJECT_RENAME.bin").c_str());
        if (buf.empty()) { printf("  SKIP  OBJECT_RENAME.bin not found\n"); }
        else
        {
            ResetAllCounters();
            auto r = DispatchMsgTypePacket(buf.data(),
                static_cast<int32>(buf.size()),
                DispatchContext{});
            check_result("OBJECT_RENAME", r,
                EDispatchResult::Handled, 1, g_objectrename_calls);
            check_no_violation("OBJECT_RENAME (no violation)", r);
        }
    }

    // ── Test 9: OBJECT_VISIBILITY -> Handled ──────────────
    {
        auto buf = read_file((dir + "OBJECT_VISIBILITY.bin").c_str());
        if (buf.empty()) { printf("  SKIP  OBJECT_VISIBILITY.bin not found\n"); }
        else
        {
            ResetAllCounters();
            auto r = DispatchMsgTypePacket(buf.data(),
                static_cast<int32>(buf.size()),
                DispatchContext{});
            check_result("OBJECT_VISIBILITY", r,
                EDispatchResult::Handled, 1, g_objectvisibility_calls);
            check_no_violation("OBJECT_VISIBILITY (no violation)", r);
        }
    }

    // ── Test 10: OBJECT_REPARENT -> Handled ───────────────
    {
        auto buf = read_file((dir + "OBJECT_REPARENT.bin").c_str());
        if (buf.empty()) { printf("  SKIP  OBJECT_REPARENT.bin not found\n"); }
        else
        {
            ResetAllCounters();
            auto r = DispatchMsgTypePacket(buf.data(),
                static_cast<int32>(buf.size()),
                DispatchContext{});
            check_result("OBJECT_REPARENT", r,
                EDispatchResult::Handled, 1, g_objectreparent_calls);
            check_no_violation("OBJECT_REPARENT (no violation)", r);
        }
    }

    // ── Test 11: MATERIAL_CREATE -> Handled ───────────────
    {
        auto buf = read_file((dir + "MATERIAL_CREATE.bin").c_str());
        if (buf.empty()) { printf("  SKIP  MATERIAL_CREATE.bin not found\n"); }
        else
        {
            ResetAllCounters();
            auto r = DispatchMsgTypePacket(buf.data(),
                static_cast<int32>(buf.size()),
                DispatchContext{});
            check_result("MATERIAL_CREATE", r,
                EDispatchResult::Handled, 1, g_materialcreate_calls);
            check_no_violation("MATERIAL_CREATE (no violation)", r);
        }
    }

    // ── Test 12: MATERIAL_UPDATE -> Handled ───────────────
    {
        auto buf = read_file((dir + "MATERIAL_UPDATE.bin").c_str());
        if (buf.empty()) { printf("  SKIP  MATERIAL_UPDATE.bin not found\n"); }
        else
        {
            ResetAllCounters();
            auto r = DispatchMsgTypePacket(buf.data(),
                static_cast<int32>(buf.size()),
                DispatchContext{});
            check_result("MATERIAL_UPDATE", r,
                EDispatchResult::Handled, 1, g_materialupdate_calls);
            check_no_violation("MATERIAL_UPDATE (no violation)", r);
        }
    }

    // ── Test 13: MATERIAL_ASSIGN -> Handled ───────────────
    {
        auto buf = read_file((dir + "MATERIAL_ASSIGN.bin").c_str());
        if (buf.empty()) { printf("  SKIP  MATERIAL_ASSIGN.bin not found\n"); }
        else
        {
            ResetAllCounters();
            auto r = DispatchMsgTypePacket(buf.data(),
                static_cast<int32>(buf.size()),
                DispatchContext{});
            check_result("MATERIAL_ASSIGN", r,
                EDispatchResult::Handled, 1, g_materialassign_calls);
            check_no_violation("MATERIAL_ASSIGN (no violation)", r);
        }
    }

    // ── Test 14: MESH_START -> Handled ────────────────────
    {
        auto buf = read_file((dir + "MESH_START.bin").c_str());
        if (buf.empty()) { printf("  SKIP  MESH_START.bin not found\n"); }
        else
        {
            ResetAllCounters();
            auto r = DispatchMsgTypePacket(buf.data(),
                static_cast<int32>(buf.size()),
                DispatchContext{});
            check_result("MESH_START", r,
                EDispatchResult::Handled, 1, g_meshstart_calls);
            check_no_violation("MESH_START (no violation)", r);
        }
    }

    // ── Test 15: MESH_CHUNK -> Handled ────────────────────
    {
        auto buf = read_file((dir + "MESH_CHUNK.bin").c_str());
        if (buf.empty()) { printf("  SKIP  MESH_CHUNK.bin not found\n"); }
        else
        {
            ResetAllCounters();
            auto r = DispatchMsgTypePacket(buf.data(),
                static_cast<int32>(buf.size()),
                DispatchContext{});
            check_result("MESH_CHUNK", r,
                EDispatchResult::Handled, 1, g_meshchunk_calls);
            check_no_violation("MESH_CHUNK (no violation)", r);
        }
    }

    // ── Test 16: MESH_END -> Handled ──────────────────────
    {
        auto buf = read_file((dir + "MESH_END.bin").c_str());
        if (buf.empty()) { printf("  SKIP  MESH_END.bin not found\n"); }
        else
        {
            ResetAllCounters();
            auto r = DispatchMsgTypePacket(buf.data(),
                static_cast<int32>(buf.size()),
                DispatchContext{});
            check_result("MESH_END", r,
                EDispatchResult::Handled, 1, g_meshend_calls);
            check_no_violation("MESH_END (no violation)", r);
        }
    }

    // ── Test 17: MESH_DATA -> Handled (rich) ──────────────
    {
        auto buf = read_file((dir + "MESH_DATA.bin").c_str());
        if (buf.empty()) { printf("  SKIP  MESH_DATA.bin not found\n"); }
        else
        {
            ResetAllCounters();
            auto r = DispatchMsgTypePacket(buf.data(),
                static_cast<int32>(buf.size()),
                DispatchContext{});
            check_result("MESH_DATA", r,
                EDispatchResult::Handled, 1, g_meshdata_calls);
            check_no_violation("MESH_DATA (no violation)", r);

            // Rich assertion: deserialize and check fields
            if (r == EDispatchResult::Handled)
            {
                auto msg = livesync::DeserializeFrame(
                    buf.data(), buf.size());
                uint32_t vc = std::get<uint32_t>(
                    msg.body.at("vertex_count"));
                uint32_t ic = std::get<uint32_t>(
                    msg.body.at("index_count"));
                auto& verts = std::get<std::vector<float>>(
                    msg.body.at("vertices"));
                auto& indices = std::get<std::vector<uint32_t>>(
                    msg.body.at("indices"));

                bool ok = (verts.size() == vc * 3) &&
                          (indices.size() == ic);
                printf("  %s  MESH_DATA (rich) verts=%u idx=%u "
                       "vert_buf=%zu idx_buf=%zu\n",
                    ok ? "PASS" : "FAIL",
                    vc, ic, verts.size(), indices.size());
                if (ok) passed++; else failed++;
            }
        }
    }

    // ── Test 18: MESH_DELTA -> Handled (rich) ─────────────
    {
        auto buf = read_file((dir + "MESH_DELTA.bin").c_str());
        if (buf.empty()) { printf("  SKIP  MESH_DELTA.bin not found\n"); }
        else
        {
            ResetAllCounters();
            auto r = DispatchMsgTypePacket(buf.data(),
                static_cast<int32>(buf.size()),
                DispatchContext{});
            check_result("MESH_DELTA", r,
                EDispatchResult::Handled, 1, g_meshdelta_calls);
            check_no_violation("MESH_DELTA (no violation)", r);

            // Rich assertion: deserialize and check fields
            if (r == EDispatchResult::Handled)
            {
                auto msg = livesync::DeserializeFrame(
                    buf.data(), buf.size());
                uint32_t vc = std::get<uint32_t>(
                    msg.body.at("vertex_count"));
                auto& verts = std::get<std::vector<float>>(
                    msg.body.at("vertices"));
                auto& norms = std::get<std::vector<float>>(
                    msg.body.at("normals"));

                bool ok = (verts.size() == vc * 3) &&
                          (norms.size() == vc * 3);
                printf("  %s  MESH_DELTA (rich) verts=%u "
                       "vert_buf=%zu norm_buf=%zu\n",
                    ok ? "PASS" : "FAIL",
                    vc, verts.size(), norms.size());
                if (ok) passed++; else failed++;
            }
        }
    }

    // ── Test 19: CAMERA_CREATE -> Handled ──────────────────
    {
        auto buf = read_file((dir + "CAMERA_CREATE.bin").c_str());
        if (buf.empty()) { printf("  SKIP  CAMERA_CREATE.bin not found\n"); }
        else
        {
            ResetAllCounters();
            auto r = DispatchMsgTypePacket(buf.data(),
                static_cast<int32>(buf.size()),
                DispatchContext{});
            check_result("CAMERA_CREATE", r,
                EDispatchResult::Handled, 1, g_cameracreate_calls);
            check_no_violation("CAMERA_CREATE (no violation)", r);

            if (r == EDispatchResult::Handled)
            {
                auto msg = livesync::DeserializeFrame(
                    buf.data(), buf.size());
                auto view = BuildCameraCreateView(msg);
                bool ok = (view.FocalLength > 0.0f) &&
                          (view.SensorWidth > 0.0f) &&
                          (view.SensorHeight > 0.0f) &&
                          !view.Name.empty();
                printf("  %s  CAMERA_CREATE (builder) "
                       "name=%s focal=%.1f\n",
                    ok ? "PASS" : "FAIL",
                    view.Name.c_str(), view.FocalLength);
                if (ok) passed++; else failed++;
            }
        }
    }

    // ── Test 20: CAMERA_UPDATE -> Handled ──────────────────
    {
        auto buf = read_file((dir + "CAMERA_UPDATE.bin").c_str());
        if (buf.empty()) { printf("  SKIP  CAMERA_UPDATE.bin not found\n"); }
        else
        {
            ResetAllCounters();
            auto r = DispatchMsgTypePacket(buf.data(),
                static_cast<int32>(buf.size()),
                DispatchContext{});
            check_result("CAMERA_UPDATE", r,
                EDispatchResult::Handled, 1, g_cameraupdate_calls);
            check_no_violation("CAMERA_UPDATE (no violation)", r);

            if (r == EDispatchResult::Handled)
            {
                auto msg = livesync::DeserializeFrame(
                    buf.data(), buf.size());
                auto view = BuildCameraUpdateView(msg);
                bool ok = view.HasFocalLength &&
                          (view.FocalLength > 0.0f);
                printf("  %s  CAMERA_UPDATE (builder) "
                       "has_focal=%d focal=%.1f\n",
                    ok ? "PASS" : "FAIL",
                    static_cast<int>(view.HasFocalLength),
                    view.FocalLength);
                if (ok) passed++; else failed++;
            }
        }
    }

    // ── Test 21: CAMERASETACTIVE -> Handled ────────────────
    {
        auto buf = read_file((dir + "CAMERASETACTIVE.bin").c_str());
        if (buf.empty()) { printf("  SKIP  CAMERASETACTIVE.bin not found\n"); }
        else
        {
            ResetAllCounters();
            auto r = DispatchMsgTypePacket(buf.data(),
                static_cast<int32>(buf.size()),
                DispatchContext{});
            check_result("CAMERASETACTIVE", r,
                EDispatchResult::Handled, 1, g_camerasetactive_calls);
            check_no_violation("CAMERASETACTIVE (no violation)", r);

            if (r == EDispatchResult::Handled)
            {
                auto msg = livesync::DeserializeFrame(
                    buf.data(), buf.size());
                auto view = BuildCameraSetActiveView(msg);
                char id_str[37];
                FormatUuid(view.CameraId, id_str, sizeof(id_str));
                bool ok = (id_str[0] != '\0');
                printf("  %s  CAMERASETACTIVE (builder) "
                       "id=%s\n",
                    ok ? "PASS" : "FAIL", id_str);
                if (ok) passed++; else failed++;
            }
        }
    }

    // ── Test 22: Empty buffer -> ParseError ───────────────
    {
        uint8 tiny[2] = {0x00, 0x00};
        auto r = DispatchMsgTypePacket(tiny, 2, DispatchContext{});
        check_result("tiny_buffer", r,
            EDispatchResult::ParseError, 0, 0);
    }

    // ── Test 23: Zero bytes -> ParseError ─────────────────
    {
        auto r = DispatchMsgTypePacket(nullptr, 0, DispatchContext{});
        check_result("zero_size", r,
            EDispatchResult::ParseError, 0, 0);
    }

    // ── Test 24: FakeGameplaySink receives OBJECT_CREATE ──
    {
        FakeGameplaySink sink;
        DispatchContext ctx;
        ctx.Gameplay = &sink;

        auto buf = read_file((dir + "OBJECT_CREATE.bin").c_str());
        if (buf.empty()) { printf("  SKIP  OBJECT_CREATE.bin not found\n"); }
        else
        {
            ResetAllCounters();
            auto r = DispatchMsgTypePacket(buf.data(),
                static_cast<int32>(buf.size()), ctx);
            check_result("DI_OBJECT_CREATE", r,
                EDispatchResult::Handled, 1, g_objectcreate_calls);
            if (sink.ObjectCreateCalls == 1)
            {
                printf("  PASS  FakeGameplaySink received OnObjectCreate\n");
                ++passed;
            }
            else
            {
                printf("  FAIL  FakeGameplaySink calls=%d (expected 1)\n",
                    sink.ObjectCreateCalls);
                ++failed;
            }
        }
    }

    // ── Test 25: FakeGameplaySink null — no crash ─────────
    {
        auto buf = read_file((dir + "OBJECT_CREATE.bin").c_str());
        if (buf.empty()) { printf("  SKIP  OBJECT_CREATE.bin not found\n"); }
        else
        {
            ResetAllCounters();
            DispatchContext ctx;
            ctx.Gameplay = nullptr;
            auto r = DispatchMsgTypePacket(buf.data(),
                static_cast<int32>(buf.size()), ctx);
            check_result("DI_null_OBJECT_CREATE", r,
                EDispatchResult::Handled, 1, g_objectcreate_calls);
        }
    }

    // ── Test 26: FakeGameplaySink receives OBJECT_DELETE ──
    {
        FakeGameplaySink sink;
        DispatchContext ctx;
        ctx.Gameplay = &sink;

        auto buf = read_file((dir + "OBJECT_DELETE.bin").c_str());
        if (buf.empty()) { printf("  SKIP  OBJECT_DELETE.bin not found\n"); }
        else
        {
            ResetAllCounters();
            auto r = DispatchMsgTypePacket(buf.data(),
                static_cast<int32>(buf.size()), ctx);
            check_result("DI_OBJECT_DELETE", r,
                EDispatchResult::Handled, 1, g_objectdelete_calls);
            if (sink.ObjectDeleteCalls == 1)
            {
                printf("  PASS  FakeGameplaySink received OnObjectDelete\n");
                ++passed;
            }
            else
            {
                printf("  FAIL  FakeGameplaySink calls=%d (expected 1)\n",
                    sink.ObjectDeleteCalls);
                ++failed;
            }
        }
    }

    // ── Test 27: FakeGameplaySink receives OBJECT_RENAME ──
    {
        FakeGameplaySink sink;
        DispatchContext ctx;
        ctx.Gameplay = &sink;

        auto buf = read_file((dir + "OBJECT_RENAME.bin").c_str());
        if (buf.empty()) { printf("  SKIP  OBJECT_RENAME.bin not found\n"); }
        else
        {
            ResetAllCounters();
            auto r = DispatchMsgTypePacket(buf.data(),
                static_cast<int32>(buf.size()), ctx);
            check_result("DI_OBJECT_RENAME", r,
                EDispatchResult::Handled, 1, g_objectrename_calls);
            if (sink.ObjectRenameCalls == 1)
            {
                printf("  PASS  FakeGameplaySink received OnObjectRename\n");
                ++passed;
            }
            else
            {
                printf("  FAIL  FakeGameplaySink calls=%d (expected 1)\n",
                    sink.ObjectRenameCalls);
                ++failed;
            }
        }
    }

    // ── Test 28: FakeGameplaySink receives OBJECT_VISIBILITY ──
    {
        FakeGameplaySink sink;
        DispatchContext ctx;
        ctx.Gameplay = &sink;

        auto buf = read_file((dir + "OBJECT_VISIBILITY.bin").c_str());
        if (buf.empty()) { printf("  SKIP  OBJECT_VISIBILITY.bin not found\n"); }
        else
        {
            ResetAllCounters();
            auto r = DispatchMsgTypePacket(buf.data(),
                static_cast<int32>(buf.size()), ctx);
            check_result("DI_OBJECT_VISIBILITY", r,
                EDispatchResult::Handled, 1, g_objectvisibility_calls);
            if (sink.ObjectVisibilityCalls == 1)
            {
                printf("  PASS  FakeGameplaySink received OnObjectVisibility\n");
                ++passed;
            }
            else
            {
                printf("  FAIL  FakeGameplaySink calls=%d (expected 1)\n",
                    sink.ObjectVisibilityCalls);
                ++failed;
            }
        }
    }

    // ── Test 29: FakeGameplaySink receives CAMERASETACTIVE ──
    {
        FakeGameplaySink sink;
        DispatchContext ctx;
        ctx.Gameplay = &sink;

        auto buf = read_file((dir + "CAMERASETACTIVE.bin").c_str());
        if (buf.empty()) { printf("  SKIP  CAMERASETACTIVE.bin not found\n"); }
        else
        {
            ResetAllCounters();
            auto r = DispatchMsgTypePacket(buf.data(),
                static_cast<int32>(buf.size()), ctx);
            check_result("DI_CAMERASETACTIVE", r,
                EDispatchResult::Handled, 1, g_camerasetactive_calls);
            if (sink.CameraSetActiveCalls == 1)
            {
                printf("  PASS  FakeGameplaySink received OnCameraSetActive\n");
                ++passed;
            }
            else
            {
                printf("  FAIL  FakeGameplaySink calls=%d (expected 1)\n",
                    sink.CameraSetActiveCalls);
                ++failed;
            }
        }
    }

    // ── Test 30: FakeGameplaySink — wrong callback not called ──
    {
        FakeGameplaySink sink;
        DispatchContext ctx;
        ctx.Gameplay = &sink;

        auto buf = read_file((dir + "OBJECT_DELETE.bin").c_str());
        if (buf.empty()) { printf("  SKIP  OBJECT_DELETE.bin not found\n"); }
        else
        {
            ResetAllCounters();
            auto r = DispatchMsgTypePacket(buf.data(),
                static_cast<int32>(buf.size()), ctx);
            check_result("DI_DELETE_no_cross_callback", r,
                EDispatchResult::Handled, 1, g_objectdelete_calls);
            if (sink.ObjectDeleteCalls == 1 &&
                sink.ObjectCreateCalls == 0 &&
                sink.ObjectRenameCalls == 0 &&
                sink.ObjectVisibilityCalls == 0 &&
                sink.CameraSetActiveCalls == 0 &&
                sink.CameraUpdateCalls == 0)
            {
                printf("  PASS  Only OnObjectDelete called, no cross-callbacks\n");
                ++passed;
            }
            else
            {
                printf("  FAIL  Cross-callback leak: Create=%d Delete=%d Rename=%d Vis=%d CamActive=%d CamUpdate=%d\n",
                    sink.ObjectCreateCalls, sink.ObjectDeleteCalls,
                    sink.ObjectRenameCalls, sink.ObjectVisibilityCalls,
                    sink.CameraSetActiveCalls, sink.CameraUpdateCalls);
                ++failed;
            }
        }
    }

    // ── Test 31: FakeGameplaySink receives CAMERA_UPDATE ──
    {
        FakeGameplaySink sink;
        DispatchContext ctx;
        ctx.Gameplay = &sink;

        auto buf = read_file((dir + "CAMERA_UPDATE.bin").c_str());
        if (buf.empty()) { printf("  SKIP  CAMERA_UPDATE.bin not found\n"); }
        else
        {
            ResetAllCounters();
            auto r = DispatchMsgTypePacket(buf.data(),
                static_cast<int32>(buf.size()), ctx);
            check_result("DI_CAMERA_UPDATE", r,
                EDispatchResult::Handled, 1, g_cameraupdate_calls);
            if (sink.CameraUpdateCalls == 1)
            {
                printf("  PASS  FakeGameplaySink received OnCameraUpdate\n");
                ++passed;
            }
            else
            {
                printf("  FAIL  FakeGameplaySink calls=%d (expected 1)\n",
                    sink.CameraUpdateCalls);
                ++failed;
            }
        }
    }

    // ── Test 32: FakeGameplaySink receives OBJECT_UPDATE ──
    {
        FakeGameplaySink sink;
        DispatchContext ctx;
        ctx.Gameplay = &sink;

        auto buf = read_file((dir + "OBJECT_UPDATE.bin").c_str());
        if (buf.empty()) { printf("  SKIP  OBJECT_UPDATE.bin not found\n"); }
        else
        {
            ResetAllCounters();
            auto r = DispatchMsgTypePacket(buf.data(),
                static_cast<int32>(buf.size()), ctx);
            check_result("DI_OBJECT_UPDATE", r,
                EDispatchResult::Handled, 1, g_objectupdate_calls);
            if (sink.ObjectUpdateCalls == 1)
            {
                printf("  PASS  FakeGameplaySink received OnObjectUpdate\n");
                ++passed;
            }
            else
            {
                printf("  FAIL  FakeGameplaySink calls=%d (expected 1)\n",
                    sink.ObjectUpdateCalls);
                ++failed;
            }
        }
    }

    // ── Test 33: FakeGameplaySink receives OBJECT_REPARENT ──
    {
        FakeGameplaySink sink;
        DispatchContext ctx;
        ctx.Gameplay = &sink;

        auto buf = read_file((dir + "OBJECT_REPARENT.bin").c_str());
        if (buf.empty()) { printf("  SKIP  OBJECT_REPARENT.bin not found\n"); }
        else
        {
            ResetAllCounters();
            auto r = DispatchMsgTypePacket(buf.data(),
                static_cast<int32>(buf.size()), ctx);
            check_result("DI_OBJECT_REPARENT", r,
                EDispatchResult::Handled, 1, g_objectreparent_calls);
            if (sink.ObjectReparentCalls == 1)
            {
                printf("  PASS  FakeGameplaySink received OnObjectReparent\n");
                ++passed;
            }
            else
            {
                printf("  FAIL  FakeGameplaySink calls=%d (expected 1)\n",
                    sink.ObjectReparentCalls);
                ++failed;
            }
        }
    }

    // ── Test 34: FakeGameplaySink receives MESH_DATA ──
    {
        FakeGameplaySink sink;
        DispatchContext ctx;
        ctx.Gameplay = &sink;

        auto buf = read_file((dir + "MESH_DATA.bin").c_str());
        if (buf.empty()) { printf("  SKIP  MESH_DATA.bin not found\n"); }
        else
        {
            ResetAllCounters();
            auto r = DispatchMsgTypePacket(buf.data(),
                static_cast<int32>(buf.size()), ctx);
            check_result("DI_MESH_DATA", r,
                EDispatchResult::Handled, 1, g_meshdata_calls);
            if (sink.MeshDataCalls == 1)
            {
                printf("  PASS  FakeGameplaySink received OnMeshData\n");
                ++passed;
            }
            else
            {
                printf("  FAIL  FakeGameplaySink calls=%d (expected 1)\n",
                    sink.MeshDataCalls);
                ++failed;
            }
        }
    }

    // ── Test 35: FakeGameplaySink receives MESH_DELTA ──
    {
        FakeGameplaySink sink;
        DispatchContext ctx;
        ctx.Gameplay = &sink;

        auto buf = read_file((dir + "MESH_DELTA.bin").c_str());
        if (buf.empty()) { printf("  SKIP  MESH_DELTA.bin not found\n"); }
        else
        {
            ResetAllCounters();
            auto r = DispatchMsgTypePacket(buf.data(),
                static_cast<int32>(buf.size()), ctx);
            check_result("DI_MESH_DELTA", r,
                EDispatchResult::Handled, 1, g_meshdelta_calls);
            if (sink.MeshDeltaCalls == 1)
            {
                printf("  PASS  FakeGameplaySink received OnMeshDelta\n");
                ++passed;
            }
            else
            {
                printf("  FAIL  FakeGameplaySink calls=%d (expected 1)\n",
                    sink.MeshDeltaCalls);
                ++failed;
            }
        }
    }

    // ── Test 36: FakeGameplaySink receives MESH_START ──
    {
        FakeGameplaySink sink;
        DispatchContext ctx;
        ctx.Gameplay = &sink;

        auto buf = read_file((dir + "MESH_START.bin").c_str());
        if (buf.empty()) { printf("  SKIP  MESH_START.bin not found\n"); }
        else
        {
            ResetAllCounters();
            auto r = DispatchMsgTypePacket(buf.data(),
                static_cast<int32>(buf.size()), ctx);
            check_result("DI_MESH_START", r,
                EDispatchResult::Handled, 1, g_meshstart_calls);
            if (sink.MeshStartCalls == 1)
            {
                printf("  PASS  FakeGameplaySink received OnMeshStart\n");
                ++passed;
            }
            else
            {
                printf("  FAIL  FakeGameplaySink calls=%d (expected 1)\n",
                    sink.MeshStartCalls);
                ++failed;
            }
        }
    }

    // ── Test 37: FakeGameplaySink receives MESH_CHUNK ──
    {
        FakeGameplaySink sink;
        DispatchContext ctx;
        ctx.Gameplay = &sink;

        auto buf = read_file((dir + "MESH_CHUNK.bin").c_str());
        if (buf.empty()) { printf("  SKIP  MESH_CHUNK.bin not found\n"); }
        else
        {
            ResetAllCounters();
            auto r = DispatchMsgTypePacket(buf.data(),
                static_cast<int32>(buf.size()), ctx);
            check_result("DI_MESH_CHUNK", r,
                EDispatchResult::Handled, 1, g_meshchunk_calls);
            if (sink.MeshChunkCalls == 1)
            {
                printf("  PASS  FakeGameplaySink received OnMeshChunk\n");
                ++passed;
            }
            else
            {
                printf("  FAIL  FakeGameplaySink calls=%d (expected 1)\n",
                    sink.MeshChunkCalls);
                ++failed;
            }
        }
    }

    // ── Test 38: FakeGameplaySink receives MESH_END ──
    {
        FakeGameplaySink sink;
        DispatchContext ctx;
        ctx.Gameplay = &sink;

        auto buf = read_file((dir + "MESH_END.bin").c_str());
        if (buf.empty()) { printf("  SKIP  MESH_END.bin not found\n"); }
        else
        {
            ResetAllCounters();
            auto r = DispatchMsgTypePacket(buf.data(),
                static_cast<int32>(buf.size()), ctx);
            check_result("DI_MESH_END", r,
                EDispatchResult::Handled, 1, g_meshend_calls);
            if (sink.MeshEndCalls == 1)
            {
                printf("  PASS  FakeGameplaySink received OnMeshEnd\n");
                ++passed;
            }
            else
            {
                printf("  FAIL  FakeGameplaySink calls=%d (expected 1)\n",
                    sink.MeshEndCalls);
                ++failed;
            }
        }
    }

    // ── Test 39: MESH_DATA cross-callback isolation ──
    {
        FakeGameplaySink sink;
        DispatchContext ctx;
        ctx.Gameplay = &sink;

        auto buf = read_file((dir + "MESH_DATA.bin").c_str());
        if (buf.empty()) { printf("  SKIP  MESH_DATA.bin not found\n"); }
        else
        {
            ResetAllCounters();
            auto r = DispatchMsgTypePacket(buf.data(),
                static_cast<int32>(buf.size()), ctx);
            check_result("DI_MESH_DATA_no_cross", r,
                EDispatchResult::Handled, 1, g_meshdata_calls);
            if (sink.MeshDataCalls == 1 &&
                sink.ObjectCreateCalls == 0 &&
                sink.ObjectDeleteCalls == 0 &&
                sink.MeshStartCalls == 0 &&
                sink.MeshDeltaCalls == 0)
            {
                printf("  PASS  Only OnMeshData called, no cross-callbacks\n");
                ++passed;
            }
            else
            {
                printf("  FAIL  Cross-callback leak: Data=%d Create=%d "
                       "Delete=%d Start=%d Delta=%d\n",
                    sink.MeshDataCalls, sink.ObjectCreateCalls,
                    sink.ObjectDeleteCalls, sink.MeshStartCalls,
                    sink.MeshDeltaCalls);
                ++failed;
            }
        }
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
