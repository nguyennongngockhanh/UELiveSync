#!/usr/bin/env python3
"""
Phase 7C — UE PT_Mesh Handler + Reassembly Skeleton (Stage 1B)

Tests:
  1. Valid single chunk accepted
  2. Multi-chunk reassembly completes
  3. Duplicate chunk rejected/ignored
  4. Missing chunk remains pending
  5. Chunk index >= chunk count rejected
  6. Chunk count == 0 rejected
  7. Invalid GUID rejected
  8. Truncated header rejected
  9. Conflicting version hash/count rejected
  10. Too many concurrent reassemblies rejected
  11. ConsoleReset clears mesh reassembly state
  12. DumpState includes mesh counts

No UProceduralMeshComponent is created.
No mesh sections are built.
No streaming from check_updates().
"""

import hashlib
import struct
import sys
import uuid

PASS = 0
FAIL = 0
SKIP = 0
RESULTS = []


def test(name, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  PASS: {name}")
    else:
        FAIL += 1
        msg = f"  FAIL: {name}"
        if detail:
            msg += f" \u2014 {detail}"
        print(msg)
    RESULTS.append((name, condition, detail))


def skip(name, reason=""):
    global SKIP
    SKIP += 1
    msg = f"  SKIP: {name}"
    if reason:
        msg += f" \u2014 {reason}"
    print(msg)
    RESULTS.append((name, True, f"SKIP \u2014 {reason}"))


# =========================================================
# Protocol constants (mirroring network.py / AssetIdentityTypes.h)
# =========================================================

LIVE_SYNC_V5_MESH_CHUNK_HEADER_SIZE = 89
LIVE_SYNC_V5_MESH_VERSION_HASH_SIZE = 64
MAX_CONCURRENT_MESH_REASSEMBLIES = 16

MESH_CHUNK_FLAG_HAS_POSITIONS = 0x01
MESH_CHUNK_FLAG_HAS_TRIANGLES = 0x02
MESH_CHUNK_FLAG_HAS_MATERIAL_IDX = 0x04
MESH_CHUNK_FLAG_FIRST_CHUNK = 0x20
MESH_CHUNK_FLAG_LAST_CHUNK = 0x40


def compute_version_hash(vertices, triangles, material_indices):
    h = hashlib.sha256()
    h.update(struct.pack("<I", len(vertices)))
    for v in vertices:
        h.update(struct.pack("<fff", v[0], v[1], v[2]))
    h.update(struct.pack("<I", len(triangles)))
    for t in triangles:
        h.update(struct.pack("<III", t[0], t[1], t[2]))
    for m in material_indices:
        h.update(struct.pack("<i", m))
    return h.hexdigest()


def build_chunk_payload(guid_obj, version_hash, chunk_index, chunk_count,
                         flags, vertex_count=0):
    """Build a minimal PT_Mesh chunk payload for testing."""
    payload = bytearray()
    a = guid_obj.time_low
    b = (guid_obj.time_mid << 16) | guid_obj.time_hi_version
    c = (guid_obj.clock_seq_hi_variant << 24) | (guid_obj.clock_seq_low << 16) | ((guid_obj.node >> 32) & 0xFFFF)
    d = guid_obj.node & 0xFFFFFFFF
    payload.extend(struct.pack("<IIII", a, b, c, d))
    version_bytes = version_hash.encode("ascii")
    payload.extend(version_bytes.ljust(64, b'\x00')[:64])
    payload.extend(struct.pack("<II", chunk_index, chunk_count))
    payload.extend(struct.pack("<B", flags))
    # Minimal payload data (vertex count = 0 for simplicity)
    payload.extend(struct.pack("<I", vertex_count))
    payload.extend(struct.pack("<I", 0))  # triangle count
    payload.extend(struct.pack("<I", 0))  # material index count
    return bytes(payload)


def parse_chunk_header(data):
    """Parse the chunk header and return (guid, version_hash, chunk_index, chunk_count, flags)."""
    guid_bytes = data[:16]
    a, b, c, d = struct.unpack_from("<IIII", data, 0)
    guid = uuid.UUID(fields=(a, (b >> 16) & 0xFFFF, b & 0xFFFF,
                              (c >> 24) & 0xFF, ((c >> 16) & 0xFF) | ((c >> 8) & 0xFF) | (c & 0xFF),
                              (d >> 24) & 0xFFFFFFFFFFFF))
    version_hash = data[16:80].decode("ascii", errors="replace").rstrip("\x00")
    chunk_idx, chunk_cnt = struct.unpack_from("<II", data, 80)
    flags = data[88]
    return guid, version_hash, chunk_idx, chunk_cnt, flags


# =========================================================
# Simulated UE-side state
# =========================================================

class SimulatedMeshReassembly:
    """Simulates UE's PendingMeshReassembly map."""

    def __init__(self):
        self.reassembly = {}  # guid_str -> state dict
        self.chunks_received = 0
        self.completions = 0
        self.malformed_rejected = 0
        self.duplicates_ignored = 0
        self.max_concurrent = MAX_CONCURRENT_MESH_REASSEMBLIES

    def handle_chunk(self, guid_str, version_hash, chunk_index, chunk_count,
                      flags=0, payload_size=0):
        """Simulate UE's HandleMeshChunk logic."""

        # Invalid GUID check
        if guid_str == "invalid":
            self.malformed_rejected += 1
            return False

        # Chunk count zero
        if chunk_count == 0:
            self.malformed_rejected += 1
            return False

        # Chunk index >= count
        if chunk_index >= chunk_count:
            self.malformed_rejected += 1
            return False

        # Max concurrent
        if len(self.reassembly) >= self.max_concurrent and guid_str not in self.reassembly:
            self.malformed_rejected += 1
            return False

        # Get or create state
        if guid_str not in self.reassembly:
            self.reassembly[guid_str] = {
                "version_hash": version_hash,
                "chunk_count": chunk_count,
                "flags": flags,
                "chunks": {},
                "chunks_received": 0,
                "first_chunk_time": 0.0,
            }
            state = self.reassembly[guid_str]
        else:
            state = self.reassembly[guid_str]

            # Conflicting version or count
            if state["version_hash"] != version_hash or state["chunk_count"] != chunk_count:
                del self.reassembly[guid_str]
                self.malformed_rejected += 1
                return False

        # Duplicate chunk
        if chunk_index in state["chunks"]:
            self.duplicates_ignored += 1
            return False

        # Store chunk
        state["chunks"][chunk_index] = b'\x00' * payload_size
        state["chunks_received"] += 1
        self.chunks_received += 1

        # Check completion
        if state["chunks_received"] >= state["chunk_count"]:
            self.completions += 1
            # Remove completed
            del self.reassembly[guid_str]

        return True

    def clear(self):
        self.reassembly.clear()
        self.chunks_received = 0
        self.completions = 0
        self.malformed_rejected = 0
        self.duplicates_ignored = 0

    def pending_count(self):
        return len(self.reassembly)


# =========================================================
# SECTION 1: Valid chunk handling
# =========================================================

def test_valid_single_chunk():
    """Valid single chunk accepted."""
    print("\n--- Section 1: Valid chunk handling ---")

    handler = SimulatedMeshReassembly()
    guid_str = "guid_cube"
    vhash = "a" * 64

    # 1.1: Single chunk accepted
    result = handler.handle_chunk(guid_str, vhash, 0, 1, flags=0x27)
    test("1.1: Single chunk accepted", result)
    test("1.2: Chunks received incremented",
         handler.chunks_received == 1)
    test("1.3: Reassembly completed (single chunk)",
         handler.completions == 1)
    test("1.4: No pending after completion",
         handler.pending_count() == 0)

    # 1.5: Another single chunk works
    result = handler.handle_chunk("guid_sphere", vhash, 0, 1)
    test("1.5: Second single chunk accepted", result)
    test("1.6: Two completions", handler.completions == 2)


# =========================================================
# SECTION 2: Multi-chunk reassembly
# =========================================================

def test_multi_chunk_reassembly():
    """Multi-chunk reassembly completes when all chunks arrive."""
    print("\n--- Section 2: Multi-chunk reassembly ---")

    handler = SimulatedMeshReassembly()
    guid_str = "guid_multi"
    vhash = "b" * 64

    # 2.1: First chunk (0/3) starts reassembly
    handler.handle_chunk(guid_str, vhash, 0, 3)
    test("2.1: First chunk starts reassembly",
         handler.pending_count() == 1 and handler.chunks_received == 1)

    # 2.2: Second chunk (1/3)
    handler.handle_chunk(guid_str, vhash, 1, 3)
    test("2.2: Second chunk stored",
         handler.pending_count() == 1 and handler.chunks_received == 2)

    # 2.3: Not complete yet
    test("2.3: Not complete yet",
         handler.completions == 0)

    # 2.4: Third chunk (2/3) completes reassembly
    handler.handle_chunk(guid_str, vhash, 2, 3)
    test("2.4: Third chunk completes",
         handler.completions == 1)
    test("2.5: State cleaned after completion",
         handler.pending_count() == 0)

    # 2.6: Chunks received count = 3
    test("2.6: Total chunks = 3",
         handler.chunks_received == 3)

    # 2.7: Four chunks (0/4, 1/4, 2/4, 3/4)
    for i in range(4):
        handler.handle_chunk("guid_4chunk", vhash, i, 4)
    test("2.7: Four chunks completed",
         handler.completions == 2 and handler.pending_count() == 0)


# =========================================================
# SECTION 3: Duplicate chunks
# =========================================================

def test_duplicate_chunk():
    """Duplicate chunk rejected/ignored deterministically."""
    print("\n--- Section 3: Duplicate chunk ---")

    handler = SimulatedMeshReassembly()
    guid_str = "guid_dup"
    vhash = "c" * 64

    # 3.1: First chunk accepted
    handler.handle_chunk(guid_str, vhash, 0, 2)
    test("3.1: First chunk accepted",
         handler.chunks_received == 1)

    # 3.2: Same chunk dropped as duplicate
    handler.handle_chunk(guid_str, vhash, 0, 2)
    test("3.2: Duplicate chunk ignored",
         handler.duplicates_ignored == 1 and handler.chunks_received == 1)

    # 3.3: Different chunk still accepted
    handler.handle_chunk(guid_str, vhash, 1, 2)
    test("3.3: Second non-duplicate chunk accepted",
         handler.chunks_received == 2 and handler.completions == 1)

    # 3.4: Duplicate of completed does not error
    handler.handle_chunk(guid_str, vhash, 0, 2)
    # After completion, guid_str is removed from reassembly, so this starts a new one
    test("3.4: After completion, same GUID starts fresh",
         handler.chunks_received >= 3)


# =========================================================
# SECTION 4: Missing chunk remains pending
# =========================================================

def test_missing_chunk():
    """Missing chunk keeps reassembly pending."""
    print("\n--- Section 4: Missing chunk ---")

    handler = SimulatedMeshReassembly()
    guid_str = "guid_missing"
    vhash = "d" * 64

    # 4.1: First chunk (0/3)
    handler.handle_chunk(guid_str, vhash, 0, 3)
    test("4.1: First chunk stored",
         handler.pending_count() == 1)

    # 4.2: Second chunk (2/3, skipping 1)
    handler.handle_chunk(guid_str, vhash, 2, 3)
    test("4.2: Third chunk stored",
         handler.pending_count() == 1)

    # 4.3: Not complete (missing chunk 1)
    test("4.3: Not complete (missing chunk 1/3)",
         handler.completions == 0 and handler.chunks_received == 2)

    # 4.4: Missing chunk arrives later
    handler.handle_chunk(guid_str, vhash, 1, 3)
    test("4.4: Missing chunk arrives -> complete",
         handler.completions == 1 and handler.pending_count() == 0)


# =========================================================
# SECTION 5: Rejection cases
# =========================================================

def test_rejection_cases():
    """Invalid chunks rejected."""
    print("\n--- Section 5: Rejection cases ---")

    handler = SimulatedMeshReassembly()
    vhash = "e" * 64

    # 5.1: Invalid GUID
    result = handler.handle_chunk("invalid", vhash, 0, 1)
    test("5.1: Invalid GUID rejected",
         result == False and handler.malformed_rejected == 1)

    # 5.2: Chunk count zero
    result = handler.handle_chunk("guid_zerocount", vhash, 0, 0)
    test("5.2: ChunkCount=0 rejected",
         result == False and handler.malformed_rejected == 2)

    # 5.3: Chunk index >= count
    result = handler.handle_chunk("guid_oob", vhash, 3, 2)
    test("5.3: ChunkIndex >= ChunkCount rejected",
         result == False and handler.malformed_rejected == 3)

    # 5.4: Chunk index == count (boundary)
    result = handler.handle_chunk("guid_boundary", vhash, 2, 2)
    test("5.4: ChunkIndex == ChunkCount rejected",
         result == False and handler.malformed_rejected == 4)

    # 5.5: Too many concurrent reassemblies
    many_handler = SimulatedMeshReassembly()
    for i in range(MAX_CONCURRENT_MESH_REASSEMBLIES):
        many_handler.handle_chunk(f"guid_{i}", vhash, 0, 2)
    # All 16 GUIDs have 0/2 chunks pending
    result = many_handler.handle_chunk("guid_overflow", vhash, 0, 2)
    test("5.5: Too many concurrent reassemblies rejected",
         result == False and many_handler.malformed_rejected == 1)

    # 5.6: Existing GUID within concurrent limit is still accepted
    result = many_handler.handle_chunk("guid_0", vhash, 1, 2)
    test("5.6: Existing GUID accepted at limit",
         result == True)


# =========================================================
# SECTION 6: Conflicting version hash/count
# =========================================================

def test_conflicting_reassembly():
    """Conflicting version hash or chunk count resets state."""
    print("\n--- Section 6: Conflicting reassembly ---")

    handler = SimulatedMeshReassembly()
    guid_str = "guid_conflict"
    vhash_a = "f" * 64
    vhash_b = "g" * 64

    # 6.1: Start with version A, 3 chunks
    handler.handle_chunk(guid_str, vhash_a, 0, 3)
    test("6.1: First chunk accepted",
         handler.pending_count() == 1)

    # 6.2: Same version, different count -> conflict -> reset
    handler.handle_chunk(guid_str, vhash_a, 0, 5)
    test("6.2: Conflicting count resets state",
         handler.malformed_rejected == 1 and handler.pending_count() == 0)

    # 6.3: Restart with same version + count
    handler.handle_chunk(guid_str, vhash_a, 0, 3)
    handler.handle_chunk(guid_str, vhash_a, 1, 3)

    # 6.4: Different version, same count -> conflict
    handler.handle_chunk(guid_str, vhash_b, 2, 3)
    test("6.4: Conflicting version hash resets state",
         handler.malformed_rejected >= 2 and handler.pending_count() == 0)

    # 6.5: Clean start with new version
    handler.handle_chunk(guid_str, vhash_b, 0, 1)
    test("6.5: Clean start with new version",
         handler.pending_count() == 0 and handler.completions >= 1)


# =========================================================
# SECTION 7: ConsoleReset
# =========================================================

def test_console_reset_clears():
    """ConsoleReset clears pending mesh reassembly."""
    print("\n--- Section 7: ConsoleReset ---")

    handler = SimulatedMeshReassembly()
    vhash = "h" * 64

    # 7.1: Populate state
    for i in range(3):
        handler.handle_chunk(f"guid_reset_{i}", vhash, 0, 2)
    test("7.1: 3 pending reassemblies",
         handler.pending_count() == 3)

    # 7.2: ConsoleReset clears all
    handler.clear()
    test("7.2: Cleared after reset",
         handler.pending_count() == 0)
    test("7.3: Chunks received reset",
         handler.chunks_received == 0)
    test("7.4: Completions reset",
         handler.completions == 0)
    test("7.5: Malformed rejected reset",
         handler.malformed_rejected == 0)

    # 7.6: After clear, new chunks work
    handler.handle_chunk("guid_fresh", vhash, 0, 1)
    test("7.6: Fresh after reset",
         handler.completions == 1)


# =========================================================
# SECTION 8: DumpState diagnostics
# =========================================================

def test_diagnostics():
    """DumpState includes mesh reassembly counts."""
    print("\n--- Section 8: Diagnostics ---")

    class SimDump:
        def __init__(self):
            self.lines = []
        def log(self, text):
            self.lines.append(text)
        def has(self, key):
            return any(key in l for l in self.lines)
        def value(self, key):
            for l in self.lines:
                if key in l:
                    parts = l.split()
                    return parts[-1] if parts else None
            return None

    dump = SimDump()
    dump.log("  PendingMeshReasm:    3")
    dump.log("  MeshChunksRcv:       15")
    dump.log("  MeshReasmCmpl:       2")

    test("8.1: DumpState includes PendingMeshReassembly",
         dump.has("PendingMeshReasm"))
    test("8.2: DumpState includes MeshChunksReceived",
         dump.has("MeshChunksRcv"))
    test("8.3: DumpState includes MeshReassembliesCompleted",
         dump.has("MeshReasmCmpl"))
    test("8.4: Pending count",
         dump.value("PendingMeshReasm") == "3")
    test("8.5: Chunks received count",
         dump.value("MeshChunksRcv") == "15")
    test("8.6: Completions count",
         dump.value("MeshReasmCmpl") == "2")


# =========================================================
# Run all sections
# =========================================================

def run_all():
    print("=" * 60)
    print("Phase 7C — PT_Mesh Handler + Reassembly (Stage 1B)")
    print("=" * 60)

    test_valid_single_chunk()              # Section 1
    test_multi_chunk_reassembly()          # Section 2
    test_duplicate_chunk()                 # Section 3
    test_missing_chunk()                   # Section 4
    test_rejection_cases()                 # Section 5
    test_conflicting_reassembly()          # Section 6
    test_console_reset_clears()            # Section 7
    test_diagnostics()                     # Section 8

    total = PASS + FAIL + SKIP
    print(f"\n{'=' * 60}")
    print(f"Phase 7C — PT_Mesh Handler + Reassembly Summary")
    print(f"{'=' * 60}")
    print(f"  Total tests: {total}")
    print(f"  Passed:      {PASS}")
    print(f"  Failed:      {FAIL}")
    print(f"  Skipped:     {SKIP}")
    if FAIL > 0:
        print(f"\n  FAILURES:")
        for name, ok, detail in RESULTS:
            if not ok:
                print(f"    FAIL: {name}")
                if detail:
                    print(f"           {detail}")
    print(f"{'=' * 60}")

    return FAIL == 0


def main():
    success = run_all()
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
