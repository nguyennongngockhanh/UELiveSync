#pragma once

/**
 * Shared utilities for serializer tests and message construction.
 */

#include <array>
#include <cstdint>
#include <string>
#include <stdexcept>
#include <cstdio>

namespace livesync {

/// Parse UUID string "00112233-4455-6677-8899-aabbccddeeff" → 16 bytes.
inline std::array<uint8_t, 16> parse_uuid(const std::string& s) {
    std::array<uint8_t, 16> result{};
    std::string hex;
    for (char c : s) {
        if (c != '-') hex += c;
    }
    if (hex.size() != 32) throw std::invalid_argument("Invalid UUID: " + s);
    for (int i = 0; i < 16; ++i) {
        unsigned int byte;
        std::sscanf(hex.c_str() + i * 2, "%02x", &byte);
        result[i] = static_cast<uint8_t>(byte);
    }
    return result;
}

} // namespace livesync
