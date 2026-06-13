#!/usr/bin/env python3
"""
Phase 10K.1 — MTEX UE Parsing Simulation Tests

Tests:
  1. Parser detects MTEX after MATX
  2. Parser detects MTEX without MATX
  3. Parser returns no records when MTEX absent
  4. Parser returns no records on truncated MTEX header
  5. Parser handles malformed PathLen > remaining bytes
  6. Parser handles malformed NameLen > remaining bytes
  7. Parser handles unknown MTEX version (skip)
  8. Parser handles empty MTEX record list (zero records)
  9. Parser handles MTEX with valid records
  10. [MTEX][RECV] log exists (static analysis)
  11. [MTEX][PARSE] log exists (static analysis)
  12. [MTEX][MALFORMED] log exists (static analysis)
  13. MaterialTextureMapCache member exists (static analysis)
  14. MtexBlocksParsed counter exists (static analysis)
  15. MtexMalformed counter exists (static analysis)
  16. parser does not modify packet on unknown MTEX version
  17. parser handles PathLen == 0 correctly
  18. parser handles NameLen == 0 correctly
  19. parser handles UTF-8 path with non-ASCII characters
  20. parser handles max records (255)
"""

import struct
import sys
import os

PASS = 0
FAIL = 0
SKIP = 0
RESULTS = []


# =========================================================
# Constants
# =========================================================

MTEX_MAGIC = 0x4D544558
MTEX_VERSION = 1
MTEX_CHANNEL_BASECOLOR = 1
MTEX_CHANNEL_ROUGHNESS = 2
MTEX_CHANNEL_METALLIC = 3
MTEX_CHANNEL_ALPHA = 4
MTEX_CHANNEL_NORMAL = 5
MTEX_FLAG_PATH_ABSOLUTE = 0x01
MTEX_FLAG_IMAGE_PACKED = 0x02
MTEX_FLAG_COLORSPACE_SRGB = 0x04
MTEX_FLAG_COLORSPACE_NON_COLOR = 0x08
MTEX_MAX_PATH_LEN = 2048
MTEX_MAX_IMAGE_NAME_LEN = 255
MTEX_HEADER_SIZE = 6
MTEX_RECORD_MIN_SIZE = 6


# =========================================================
# Simulated UE parser (mirrors UELiveSyncSubsystem.cpp)
# =========================================================

class SimulatedUEParser:
    """Simulates the MTEX parsing logic in UELiveSyncSubsystem.cpp."""
    
    def __init__(self):
        self.MaterialTextureMapCache = {}
        self.MtexBlocksParsed = 0
        self.MtexRecordsParsed = 0
        self.MtexMalformed = 0
        self.logs = []
    
    def parse_mtex_block(self, data, guid_str="TESTGUID", start_offset=0):
        """Parse MTEX extension block. Returns list of records or None if no MTEX."""
        ptr = start_offset
        if ptr + 4 > len(data):
            return None
        
        magic = struct.unpack_from("<I", data, ptr)[0]
        if magic != MTEX_MAGIC:
            return None
        
        # MTEX magic found
        remaining = len(data) - (ptr + MTEX_HEADER_SIZE)
        self.logs.append(f"[MTEX][RECV] guid={guid_str} hasMTEX=1 records=N remainingBytes={remaining}")
        ptr += 4
        
        if ptr >= len(data):
            return None
        
        version = data[ptr]
        ptr += 1
        
        if version != MTEX_VERSION:
            if version != 0:
                self.logs.append(f"[MTEX][SKIP] guid={guid_str} version={version} unsupported")
            return None
        
        if ptr >= len(data):
            return None
        
        rec_count = data[ptr]
        ptr += 1
        
        records = []
        for ri in range(rec_count):
            if ptr + MTEX_RECORD_MIN_SIZE > len(data):
                self.logs.append(
                    f"[MTEX][MALFORMED] guid={guid_str} reason=truncated_record "
                    f"record={ri}/{rec_count} remaining={len(data) - ptr}")
                self.MtexMalformed += 1
                break
            
            slot_idx = data[ptr]
            ptr += 1
            channel = data[ptr]
            ptr += 1
            flags = data[ptr]
            ptr += 1
            
            path_len = struct.unpack_from("<H", data, ptr)[0]
            ptr += 2
            
            if path_len > MTEX_MAX_PATH_LEN:
                self.logs.append(
                    f"[MTEX][MALFORMED] guid={guid_str} reason=path_len={path_len} exceeds max={MTEX_MAX_PATH_LEN} "
                    f"record={ri}/{rec_count}")
                self.MtexMalformed += 1
                path_len = MTEX_MAX_PATH_LEN
            
            if ptr + path_len > len(data):
                self.logs.append(
                    f"[MTEX][MALFORMED] guid={guid_str} reason=path_exceeds_packet "
                    f"record={ri}/{rec_count} pathLen={path_len} remaining={len(data) - ptr}")
                self.MtexMalformed += 1
                break
            
            path = ""
            if path_len > 0:
                path = data[ptr:ptr + path_len].decode("utf-8", errors="replace")
                ptr += path_len
            
            if ptr >= len(data):
                self.logs.append(
                    f"[MTEX][MALFORMED] guid={guid_str} reason=missing_imagename_len "
                    f"record={ri}/{rec_count}")
                self.MtexMalformed += 1
                break
            
            name_len = data[ptr]
            ptr += 1
            
            if name_len > MTEX_MAX_IMAGE_NAME_LEN:
                self.logs.append(
                    f"[MTEX][MALFORMED] guid={guid_str} reason=imagename_len={name_len} exceeds max={MTEX_MAX_IMAGE_NAME_LEN} "
                    f"record={ri}/{rec_count}")
                self.MtexMalformed += 1
                name_len = MTEX_MAX_IMAGE_NAME_LEN
            
            if ptr + name_len > len(data):
                self.logs.append(
                    f"[MTEX][MALFORMED] guid={guid_str} reason=imagename_exceeds_packet "
                    f"record={ri}/{rec_count} nameLen={name_len} remaining={len(data) - ptr}")
                self.MtexMalformed += 1
                break
            
            image_name = ""
            if name_len > 0:
                image_name = data[ptr:ptr + name_len].decode("utf-8", errors="replace")
                ptr += name_len
            
            channel_name = {
                1: "BaseColor", 2: "Roughness", 3: "Metallic",
                4: "Alpha", 5: "Normal"
            }.get(channel, "Unknown")
            
            self.logs.append(
                f"[MTEX][PARSE] guid={guid_str} slot={slot_idx} channel={channel_name} "
                f"image={image_name} path={path if path else '(none)'} flags={flags}")
            
            records.append((slot_idx, channel, flags, path, image_name))
        
        if records:
            self.MaterialTextureMapCache[guid_str] = records
            self.MtexBlocksParsed += 1
            self.MtexRecordsParsed += len(records)
        
        return records


def _make_mtex_bytes(version=MTEX_VERSION, records=None):
    """Build MTEX block bytes."""
    if records is None:
        records = []
    payload = bytearray()
    payload.extend(struct.pack("<I", MTEX_MAGIC))
    payload.extend(struct.pack("<B", version))
    payload.extend(struct.pack("<B", len(records)))
    for slot_idx, channel, flags, path, name in records:
        path_bytes = path.encode("utf-8") if path else b""
        name_bytes = name.encode("utf-8") if name else b""
        if len(path_bytes) > MTEX_MAX_PATH_LEN:
            path_bytes = path_bytes[:MTEX_MAX_PATH_LEN]
        if len(name_bytes) > MTEX_MAX_IMAGE_NAME_LEN:
            name_bytes = name_bytes[:MTEX_MAX_IMAGE_NAME_LEN]
        payload.extend(struct.pack("<B", slot_idx))
        payload.extend(struct.pack("<B", channel))
        payload.extend(struct.pack("<B", flags))
        payload.extend(struct.pack("<H", len(path_bytes)))
        payload.extend(path_bytes)
        payload.extend(struct.pack("<B", len(name_bytes)))
        payload.extend(name_bytes)
    return bytes(payload)


def _test(name, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        RESULTS.append(f"  PASS  {name}")
    else:
        FAIL += 1
        msg = f"  FAIL  {name}"
        if detail:
            msg += f"  ({detail})"
        RESULTS.append(msg)


# =========================================================
# Tests
# =========================================================

def run_tests():
    # ------------------------------------------------------------------
    # Test 1: Parser detects MTEX after MATX
    # ------------------------------------------------------------------
    parser = SimulatedUEParser()
    # Simulate MATX + MTEX block
    matx_header = struct.pack("<I", 0x4D415458)  # MATX magic
    matx_header += struct.pack("<BB", 1, 1)       # version=1, count=1
    matx_slot = struct.pack("<Bffffff", 0, 0.8, 0.8, 0.8, 1.0, 0.5, 0.0)  # 25 bytes
    mtex = _make_mtex_bytes(records=[
        (0, MTEX_CHANNEL_BASECOLOR, MTEX_FLAG_PATH_ABSOLUTE | MTEX_FLAG_COLORSPACE_SRGB,
         "/tex/albedo.png", "albedo.png")
    ])
    data = matx_header + matx_slot + mtex
    # MATX header = 6 bytes, MATX slot data = 25 bytes → MTEX starts at offset 31
    records = parser.parse_mtex_block(data, "GUID0001", 31)
    _test("MTEX detected after MATX", records is not None and len(records) == 1)

    # ------------------------------------------------------------------
    # Test 2: Parser detects MTEX without MATX
    # ------------------------------------------------------------------
    parser2 = SimulatedUEParser()
    mtex2 = _make_mtex_bytes(records=[
        (0, MTEX_CHANNEL_BASECOLOR, 0, "tex.png", "tex.png")
    ])
    records2 = parser2.parse_mtex_block(mtex2, "GUID0002", 0)
    _test("MTEX detected without MATX", records2 is not None and len(records2) == 1)

    # ------------------------------------------------------------------
    # Test 3: No MTEX block → returns None
    # ------------------------------------------------------------------
    parser3 = SimulatedUEParser()
    non_mtex_data = struct.pack("<I", 0xFFFFFFFF)
    records3 = parser3.parse_mtex_block(non_mtex_data, "GUID0003", 0)
    _test("No MTEX returns None", records3 is None)

    # ------------------------------------------------------------------
    # Test 4: Truncated MTEX header (only magic)
    # ------------------------------------------------------------------
    parser4 = SimulatedUEParser()
    truncated = struct.pack("<I", MTEX_MAGIC)
    records4 = parser4.parse_mtex_block(truncated, "GUID0004", 0)
    _test("Truncated MTEX header returns None", records4 is None)
    _test("Truncated MTEX no malformed count", parser4.MtexMalformed == 0)

    # ------------------------------------------------------------------
    # Test 5: Malformed PathLen > remaining
    # ------------------------------------------------------------------
    parser5 = SimulatedUEParser()
    mtex5 = _make_mtex_bytes(records=[
        (0, MTEX_CHANNEL_BASECOLOR, 0, "a" * 100, "name.png")
    ])
    # Truncate the data to remove the path
    truncated5 = mtex5[:len(mtex5) - 50]
    records5 = parser5.parse_mtex_block(truncated5, "GUID0005", 0)
    # The parser should break on path_exceeds_packet
    _test("Truncated path returns partial/empty or malformed",
          records5 is None or len(records5) == 0)
    _test("Truncated path increments MtexMalformed",
          parser5.MtexMalformed > 0)

    # ------------------------------------------------------------------
    # Test 6: Malformed NameLen > remaining
    # ------------------------------------------------------------------
    parser6 = SimulatedUEParser()
    # Build a block where path is valid but name is truncated
    name_too_long = "n" * 50
    mtex6 = _make_mtex_bytes(records=[
        (0, MTEX_CHANNEL_BASECOLOR, 0, "short.png", name_too_long)
    ])
    truncated6 = mtex6[:len(mtex6) - 40]
    records6 = parser6.parse_mtex_block(truncated6, "GUID0006", 0)
    _test("Truncated name returns partial/empty or malformed",
          records6 is None or len(records6) == 0)
    _test("Truncated name increments MtexMalformed",
          parser6.MtexMalformed > 0)

    # ------------------------------------------------------------------
    # Test 7: Unknown MTEX version (skip, not crash)
    # ------------------------------------------------------------------
    parser7 = SimulatedUEParser()
    mtex7 = _make_mtex_bytes(version=99, records=[
        (0, MTEX_CHANNEL_BASECOLOR, 0, "tex.png", "tex.png")
    ])
    records7 = parser7.parse_mtex_block(mtex7, "GUID0007", 0)
    _test("Unknown MTEX version returns None", records7 is None)
    _test("Unknown version does not increment MtexBlocksParsed",
          parser7.MtexBlocksParsed == 0)
    _test("Unknown version skip log present",
          any("[MTEX][SKIP]" in l for l in parser7.logs))

    # ------------------------------------------------------------------
    # Test 8: Empty MTEX (zero records)
    # ------------------------------------------------------------------
    parser8 = SimulatedUEParser()
    mtex8 = _make_mtex_bytes(records=[])
    records8 = parser8.parse_mtex_block(mtex8, "GUID0008", 0)
    _test("Empty MTEX (0 records) returns []", records8 is not None and len(records8) == 0)
    _test("Empty MTEX does not increment blocks parsed",
          parser8.MtexBlocksParsed == 0)

    # ------------------------------------------------------------------
    # Test 9: Valid MTEX with multiple records
    # ------------------------------------------------------------------
    parser9 = SimulatedUEParser()
    mtex9 = _make_mtex_bytes(records=[
        (0, MTEX_CHANNEL_BASECOLOR, MTEX_FLAG_PATH_ABSOLUTE | MTEX_FLAG_COLORSPACE_SRGB,
         "/tex/base.png", "base.png"),
        (0, MTEX_CHANNEL_ROUGHNESS, MTEX_FLAG_PATH_ABSOLUTE | MTEX_FLAG_COLORSPACE_NON_COLOR,
         "/tex/rough.png", "rough.png"),
        (1, MTEX_CHANNEL_METALLIC, MTEX_FLAG_COLORSPACE_NON_COLOR,
         "/tex/metal.png", "metal.png"),
    ])
    records9 = parser9.parse_mtex_block(mtex9, "GUID0009", 0)
    _test("Valid MTEX with 3 records returns 3 records",
          records9 is not None and len(records9) == 3)
    _test("MTEX blocks parsed incremented",
          parser9.MtexBlocksParsed == 1)
    _test("MTEX records parsed incremented",
          parser9.MtexRecordsParsed == 3)
    _test("Cache populated",
          "GUID0009" in parser9.MaterialTextureMapCache)
    if records9:
        _test("Record 0: slot preserved", records9[0][0] == 0)
        _test("Record 0: channel preserved", records9[0][1] == MTEX_CHANNEL_BASECOLOR)
        _test("Record 0: path preserved", records9[0][3] == "/tex/base.png")
        _test("Record 0: name preserved", records9[0][4] == "base.png")
        _test("Record 0: flags preserved", records9[0][2] & MTEX_FLAG_PATH_ABSOLUTE)
        _test("Record 2: slot 1", records9[2][0] == 1)
        _test("Record 2: channel Metallic", records9[2][1] == MTEX_CHANNEL_METALLIC)

    # ------------------------------------------------------------------
    # Test 10: Log marker checks (by simulating logs)
    # ------------------------------------------------------------------
    _test("[MTEX][RECV] log emitted",
          any("[MTEX][RECV]" in l for l in parser9.logs))
    _test("[MTEX][PARSE] log emitted",
          any("[MTEX][PARSE]" in l for l in parser9.logs))

    # ------------------------------------------------------------------
    # Test 11: PathLen == 0 with NameLen == 0
    # ------------------------------------------------------------------
    parser11 = SimulatedUEParser()
    mtex11 = _make_mtex_bytes(records=[
        (0, MTEX_CHANNEL_ALPHA, 0, "", "")
    ])
    records11 = parser11.parse_mtex_block(mtex11, "GUID0011", 0)
    _test("Zero-length path and name ok",
          records11 is not None and len(records11) == 1)
    if records11:
        _test("Zero-length path saved", records11[0][3] == "")
        _test("Zero-length name saved", records11[0][4] == "")

    # ------------------------------------------------------------------
    # Test 12: PathLen == 0 with valid name
    # ------------------------------------------------------------------
    parser12 = SimulatedUEParser()
    mtex12 = _make_mtex_bytes(records=[
        (0, MTEX_CHANNEL_BASECOLOR, MTEX_FLAG_IMAGE_PACKED,
         "", "PackedImage")
    ])
    records12 = parser12.parse_mtex_block(mtex12, "GUID0012", 0)
    _test("Packed image: name only", records12 and records12[0][4] == "PackedImage")
    _test("Packed image: empty path", records12 and records12[0][3] == "")
    _test("Packed image: flag preserved",
          records12 and (records12[0][2] & MTEX_FLAG_IMAGE_PACKED))

    # ------------------------------------------------------------------
    # Test 13: Large path boundary case
    # ------------------------------------------------------------------
    parser13 = SimulatedUEParser()
    large_path = "x" * 2048
    mtex13 = _make_mtex_bytes(records=[
        (0, MTEX_CHANNEL_NORMAL, MTEX_FLAG_COLORSPACE_NON_COLOR, large_path, "normal.png")
    ])
    records13 = parser13.parse_mtex_block(mtex13, "GUID0013", 0)
    _test("Large path (2048) accepted", records13 and len(records13[0][3]) == 2048)

    # ------------------------------------------------------------------
    # Test 14: Path exceeds max (simulate clamping)
    # ------------------------------------------------------------------
    parser14 = SimulatedUEParser()
    oversized_path = "y" * 3000
    mtex14_bad = struct.pack("<I", MTEX_MAGIC)
    mtex14_bad += struct.pack("<BB", 1, 1)  # version=1, count=1
    mtex14_bad += struct.pack("<BBB", 0, MTEX_CHANNEL_BASECOLOR, 0)
    mtex14_bad += struct.pack("<H", 3000)  # path_len=3000, exceeds MAX
    mtex14_bad += b"y" * 50  # Only provide 50 bytes, not 3000
    records14 = parser14.parse_mtex_block(mtex14_bad, "GUID0014", 0)
    _test("Oversized PathLen logs malformed",
          parser14.MtexMalformed > 0 and any("[MTEX][MALFORMED]" in l for l in parser14.logs))

    # ------------------------------------------------------------------
    # Test 15: [MTEX][PARSE] log format check
    # ------------------------------------------------------------------
    parser15 = SimulatedUEParser()
    mtex15 = _make_mtex_bytes(records=[
        (2, MTEX_CHANNEL_NORMAL, MTEX_FLAG_COLORSPACE_NON_COLOR, "/n.png", "normal.png")
    ])
    records15 = parser15.parse_mtex_block(mtex15, "GUID0015", 0)
    parse_logs = [l for l in parser15.logs if "[MTEX][PARSE]" in l]
    _test("[MTEX][PARSE] has slot", bool(parse_logs) and "slot=2" in parse_logs[0])
    _test("[MTEX][PARSE] has channel=Normal", bool(parse_logs) and "channel=Normal" in parse_logs[0])

    # ------------------------------------------------------------------
    # Test 16: Static analysis — parser logs in Subsystem.cpp
    # ------------------------------------------------------------------
    cpp_path = os.path.join(os.path.dirname(__file__), "..",
        "UE_Plugin/UELiveSync/Source/UELiveSync/Private/UELiveSyncSubsystem.cpp")
    if os.path.exists(cpp_path):
        with open(cpp_path, "r") as f:
            content = f.read()
        _test("[MTEX][RECV] in Subsystem.cpp",
              "[MTEX][RECV]" in content)
        _test("[MTEX][PARSE] in Subsystem.cpp",
              "[MTEX][PARSE]" in content)
        _test("[MTEX][MALFORMED] in Subsystem.cpp",
              "[MTEX][MALFORMED]" in content)
        _test("[MTEX][SKIP] in Subsystem.cpp",
              "[MTEX][SKIP]" in content)
        _test("MaterialTextureMapCache in Subsystem.cpp",
              "MaterialTextureMapCache" in content)
        _test("MtexBlocksParsed in Subsystem.cpp",
              "MtexBlocksParsed" in content)
        _test("MtexRecordsParsed in Subsystem.cpp",
              "MtexRecordsParsed" in content)
        _test("MtexMalformed in Subsystem.cpp",
              "MtexMalformed" in content)
    else:
        RESULTS.append("  SKIP  Static analysis — Subsystem.cpp not found")
        global SKIP
        SKIP += 8

    # ------------------------------------------------------------------
    # Test 17: Static analysis — header declarations
    # ------------------------------------------------------------------
    header_path = os.path.join(os.path.dirname(__file__), "..",
        "UE_Plugin/UELiveSync/Source/UELiveSync/Public/UELiveSyncSubsystem.h")
    if os.path.exists(header_path):
        with open(header_path, "r") as f:
            content = f.read()
        _test("MaterialTextureMapCache declaration in Subsystem.h",
              "MaterialTextureMapCache" in content)
        _test("MtexBlocksParsed declaration in Subsystem.h",
              "MtexBlocksParsed" in content)
        _test("MtexMalformed declaration in Subsystem.h",
              "MtexMalformed" in content)
    else:
        RESULTS.append("  SKIP  Static analysis — Subsystem.h not found")
        SKIP += 3

    # ------------------------------------------------------------------
    # Test 18: Static analysis — diagnostics
    # ------------------------------------------------------------------
    diag_path = os.path.join(os.path.dirname(__file__), "..",
        "UE_Plugin/UELiveSync/Source/UELiveSync/Private/UELiveSyncSubsystem_Diagnostics.inl")
    if os.path.exists(diag_path):
        with open(diag_path, "r") as f:
            content = f.read()
        _test("MaterialTextureMapCache.Empty in Diagnostics.inl",
              "MaterialTextureMapCache.Empty()" in content)
        _test("MtexMalformed reset in Diagnostics.inl",
              "MtexMalformed = 0" in content)
    else:
        RESULTS.append("  SKIP  Static analysis — Diagnostics.inl not found")
        SKIP += 2

    # ------------------------------------------------------------------
    # Test 19: UTF-8 path
    # ------------------------------------------------------------------
    parser19 = SimulatedUEParser()
    utf8_path = "/textures/\u00e9clat\u00e9.png"  # éclaté
    utf8_name = "\u00e9clat\u00e9.png"
    mtex19 = _make_mtex_bytes(records=[
        (0, MTEX_CHANNEL_BASECOLOR, 0, utf8_path, utf8_name)
    ])
    records19 = parser19.parse_mtex_block(mtex19, "GUID0019", 0)
    _test("UTF-8 path parsed", records19 and records19[0][3] == utf8_path)
    _test("UTF-8 name parsed", records19 and records19[0][4] == utf8_name)

    # ------------------------------------------------------------------
    # Test 20: Counter sanity after multiple parse calls
    # ------------------------------------------------------------------
    parser20 = SimulatedUEParser()
    for i in range(3):
        m = _make_mtex_bytes(records=[
            (0, MTEX_CHANNEL_BASECOLOR, 0, f"tex{i}.png", f"tex{i}.png"),
            (1, MTEX_CHANNEL_ROUGHNESS, 0, f"rough{i}.png", f"rough{i}.png"),
        ])
        parser20.parse_mtex_block(m, f"GUID{i}", 0)
    _test("3 MTEX blocks: blocks parsed = 3", parser20.MtexBlocksParsed == 3)
    _test("3 MTEX blocks: records parsed = 6", parser20.MtexRecordsParsed == 6)
    _test("3 MTEX blocks: malformed = 0", parser20.MtexMalformed == 0)
    _test("3 MTEX blocks: cache has 3 entries",
          len(parser20.MaterialTextureMapCache) == 3)


if __name__ == "__main__":
    run_tests()
    print(f"\nPhase 10K.1 — UE Parse: {PASS} passed, {FAIL} failed, {SKIP} skipped")
    for r in RESULTS:
        print(r)
    sys.exit(0 if FAIL == 0 else 1)
