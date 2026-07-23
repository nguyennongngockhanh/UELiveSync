/**
 * Golden round-trip tests for C++ serializer/deserializer.
 *
 * Test 1: Golden bytes → deserialize → reserialize → memcmp(original)
 * Test 2: Golden bytes → D1 → bytes → D2 → D1 == D2 (semantic stability)
 *
 * Build: g++ -std=c++20 -O2 -I.. -o test_roundtrip test_roundtrip.cpp
 * Run:   ./test_roundtrip <vectors_dir>
 */

#include "livesync_deserializer.h"
#include "tests/support/manifest_loader.h"
#include "tests/support/reserialize.h"

#include <fstream>
#include <iostream>
#include <sstream>
#include <vector>
#include <string>
#include <cmath>

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

static void hex_dump_diff(const std::vector<uint8_t>& expected,
                           const std::vector<uint8_t>& actual,
                           size_t offset) {
    std::cerr << "    First difference at offset " << offset << ":\n";
    size_t start = (offset >= 8) ? offset - 8 : 0;
    size_t end = std::min({offset + 8, expected.size(), actual.size()});

    for (size_t i = start; i < end; ++i) {
        const char* marker = (i == offset) ? ">>>" : "   ";
        std::cerr << "    " << marker << " [" << i << "] expected 0x"
                  << std::hex << std::setw(2) << std::setfill('0')
                  << static_cast<int>(expected[i]) << std::dec
                  << "  actual 0x"
                  << std::hex << std::setw(2) << std::setfill('0')
                  << static_cast<int>(actual[i]) << std::dec << "\n";
    }
}

/// Compare DeserializedMessages for semantic equality.
static bool messages_equal(const DeserializedMessage& a,
                           const DeserializedMessage& b) {
    if (a.msg_type != b.msg_type) return false;
    if (a.flags != b.flags) return false;
    if (a.sequence_id != b.sequence_id) return false;
    if (a.session_id != b.session_id) return false;
    if (a.body.size() != b.body.size()) return false;

    for (auto& [key, val_a] : a.body) {
        auto it = b.body.find(key);
        if (it == b.body.end()) return false;
        if (val_a != it->second) return false;
    }
    return true;
}

int main(int argc, char* argv[]) {
    if (argc < 2) {
        std::cerr << "Usage: " << argv[0] << " <vectors_dir>\n";
        return 1;
    }

    std::string vectors_dir = argv[1];
    json manifest = json::parse(read_text(vectors_dir + "/manifest.json"));

    int passed = 0, failed = 0;

    std::cout << "=== Test 1: Golden → Deserialize → Reserialize → Compare Bytes ===\n\n";

    for (const auto& vec : manifest["vectors"]) {
        std::string name = vec["name"].get<std::string>();
        std::string file = vec["file"].get<std::string>();

        try {
            auto golden = read_file(vectors_dir + "/" + file);

            // Deserialize
            auto msg = DeserializeFrame(golden.data(), golden.size());

            // Reserialize
            auto reserialized = test::reserialize_frame(msg);

            // Compare sizes
            if (reserialized.size() != golden.size()) {
                std::cerr << "  FAIL  " << name
                          << " — size mismatch: expected " << golden.size()
                          << " got " << reserialized.size() << "\n";
                failed++;
                continue;
            }

            // Compare bytes
            if (std::memcmp(golden.data(), reserialized.data(), golden.size()) != 0) {
                // Find first differing byte
                size_t diff = 0;
                for (size_t i = 0; i < golden.size(); ++i) {
                    if (golden[i] != reserialized[i]) {
                        diff = i;
                        break;
                    }
                }
                std::cerr << "  FAIL  " << name
                          << " — byte mismatch at offset " << diff << "\n";
                hex_dump_diff(golden, reserialized, diff);
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

    std::cout << "\n=== Test 2: Golden → D1 → Bytes → D2 → D1 == D2 ===\n\n";

    int stable = 0, unstable = 0;

    for (const auto& vec : manifest["vectors"]) {
        std::string name = vec["name"].get<std::string>();
        std::string file = vec["file"].get<std::string>();

        try {
            auto golden = read_file(vectors_dir + "/" + file);

            // D1 = deserialize(golden)
            auto d1 = DeserializeFrame(golden.data(), golden.size());

            // bytes = reserialize(D1)
            auto bytes = test::reserialize_frame(d1);

            // D2 = deserialize(bytes)
            auto d2 = DeserializeFrame(bytes.data(), bytes.size());

            // Compare D1 == D2
            if (!messages_equal(d1, d2)) {
                std::cerr << "  FAIL  " << name
                          << " — semantic mismatch (D1 != D2)\n";
                unstable++;
                continue;
            }

            std::cout << "  PASS  " << name << "\n";
            stable++;

        } catch (const std::exception& e) {
            std::cerr << "  FAIL  " << name << " — " << e.what() << "\n";
            unstable++;
        }
    }

    std::cout << "\n" << std::string(60, '=') << "\n";
    std::cout << "ROUND-TRIP:    " << passed << "/" << (passed + failed) << " PASSED\n";
    std::cout << "SEMANTIC:      " << stable << "/" << (stable + unstable) << " STABLE\n";

    if (failed == 0 && unstable == 0) {
        std::cout << "ALL ROUND-TRIP TESTS PASSED\n";
        return 0;
    } else {
        if (failed > 0)
            std::cout << "ROUND-TRIP FAILURES: " << failed << "\n";
        if (unstable > 0)
            std::cout << "SEMANTIC INSTABILITY: " << unstable << "\n";
        return 1;
    }
}
