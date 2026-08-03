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

/// Convert RFC 4122 16 bytes to the FGuid LE layout used for Object-GUID
/// references (MIG-006): each 4-byte group is stored as a little-endian uint32.
inline std::array<uint8_t, 16> fguid_from_rfc(const std::array<uint8_t, 16>& bytes) {
    std::array<uint8_t, 16> out{};
    for (int i = 0; i < 16; i += 4) {
        out[i] = bytes[i + 3];
        out[i + 1] = bytes[i + 2];
        out[i + 2] = bytes[i + 1];
        out[i + 3] = bytes[i];
    }
    return out;
}

} // namespace livesync
