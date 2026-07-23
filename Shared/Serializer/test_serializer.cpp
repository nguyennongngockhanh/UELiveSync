/**
 * Golden vector test harness for C++ serializer.
 *
 * Reads manifest.json + .bin files, serializes each message using the
 * library's PackFrame(), and compares byte-for-byte against golden.
 *
 * Build: g++ -std=c++20 -O2 -I.. -I../.. -o test_serializer test_serializer.cpp
 * Run:   ./test_serializer <vectors_dir>
 */

#include "tests/support/manifest_loader.h"

#include <fstream>
#include <iostream>
#include <sstream>
#include <vector>
#include <string>

using json = nlohmann::json;
using namespace livesync;

static std::vector<uint8_t> read_file(const std::string& path) {
    std::ifstream f(path, std::ios::binary);
    if (!f) throw std::runtime_error("Cannot open: " + path);
    return std::vector<uint8_t>(
        std::istreambuf_iterator<char>(f),
        std::istreambuf_iterator<char>()
    );
}

static std::string read_text(const std::string& path) {
    std::ifstream f(path);
    if (!f) throw std::runtime_error("Cannot open: " + path);
    std::ostringstream ss;
    ss << f.rdbuf();
    return ss.str();
}

int main(int argc, char* argv[]) {
    if (argc < 2) {
        std::cerr << "Usage: " << argv[0] << " <vectors_dir>\n";
        return 1;
    }

    std::string vectors_dir = argv[1];
    json manifest = json::parse(read_text(vectors_dir + "/manifest.json"));

    int passed = 0, failed = 0;

    for (const auto& vec : manifest["vectors"]) {
        std::string name = vec["name"].get<std::string>();
        std::string file = vec["file"].get<std::string>();

        try {
            auto golden = read_file(vectors_dir + "/" + file);

            // Build body via test support (manifest → library functions)
            MsgType msg_type = static_cast<MsgType>(vec["msg_type"].get<uint8_t>());
            auto body = test::build_body_from_manifest(msg_type, vec["fields"]);

            // Pack frame via library
            std::optional<uint64_t> session_id;
            if (!vec["session_id"].is_null()) {
                session_id = vec["session_id"].get<uint64_t>();
            }
            auto computed = PackFrame(
                msg_type,
                vec["flags"].get<uint8_t>(),
                vec["sequence_id"].get<uint32_t>(),
                session_id,
                body);

            // Compare byte-for-byte
            if (computed.size() != golden.size()) {
                std::cerr << "  FAIL  " << name
                          << " — size: computed " << computed.size()
                          << " vs golden " << golden.size() << "\n";
                failed++;
                continue;
            }
            if (std::memcmp(computed.data(), golden.data(), golden.size()) != 0) {
                std::cerr << "  FAIL  " << name << " — byte mismatch\n";
                for (size_t i = 0; i < golden.size(); ++i) {
                    if (computed[i] != golden[i]) {
                        std::cerr << "    diff at byte " << i
                                  << ": 0x" << std::hex << (int)computed[i]
                                  << " vs 0x" << (int)golden[i]
                                  << std::dec << "\n";
                        break;
                    }
                }
                failed++;
                continue;
            }

            std::cout << "  PASS  " << name << " (" << golden.size() << " bytes)\n";
            passed++;

        } catch (const std::exception& e) {
            std::cerr << "  FAIL  " << name << " — " << e.what() << "\n";
            failed++;
        }
    }

    std::cout << "\n" << std::string(50, '=') << "\n";
    if (failed == 0) {
        std::cout << "ALL " << passed << " VECTORS PASSED\n";
        return 0;
    } else {
        std::cout << "FAILED: " << failed << "/" << (passed + failed) << "\n";
        return 1;
    }
}
