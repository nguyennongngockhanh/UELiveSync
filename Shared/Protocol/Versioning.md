# Protocol Versioning Rules

LiveSync protocol follows semantic versioning: `MAJOR.MINOR.PATCH`.

## Version Format

```
protocol_version = MAJOR.MINOR
```

Carried in HELLO message (§5.4 of System Architecture).

## When to Bump

### MAJOR — Breaking wire format

Bump MAJOR when:
- Message header layout changes (e.g., adding/removing/reordering header fields)
- Existing message type codes are reassigned
- Payload encoding changes in a way that old receivers cannot parse
- Session ID format changes
- Length-prefix framing changes

MAJOR bump means: **Old implementations CANNOT communicate with new implementations.** HELLO with mismatched MAJOR → REJECT.

### MINOR — New optional capability

Bump MINOR when:
- New message type added (e.g., `MESH_DELTA = 0x31`)
- New capability bit defined (e.g., Bit 6: Supports volume sync)
- New optional field appended to existing message (receiver ignores unknown fields)
- New SCENE_HASH algorithm variant

MINOR bump means: **Old implementations CAN communicate with new implementations** via capability negotiation. New features are disabled for peers that don't announce them.

### PATCH — Documentation/test vectors only

Bump PATCH when:
- Test vectors updated
- Documentation corrected
- Schema validation rules clarified
- No wire format or behavior change

## Field Rules

### Appending fields
- **Allowed** in MINOR/PATCH bumps.
- Old receivers MUST ignore unknown fields (read length, skip).
- New receivers MUST treat missing fields as default/zero.

### Reordering fields
- **NOT allowed** without MAJOR bump.
- Reordering breaks binary deserialization.

### Removing fields
- **NOT allowed** without MAJOR bump.
- Old receivers expect the field to exist.

### Changing field type
- **NOT allowed** without MAJOR bump.
- Changing `uint32` to `uint64` breaks wire compatibility.

## Capability Bit Allocation

New capabilities use the next available bit in the bitmask (§5.4).

```
Bit 0: Supports mesh sync
Bit 1: Supports material sync
Bit 2: Supports camera sync
Bit 3: Supports mesh delta (vertex-only)
Bit 4: Supports chunked transfer
Bit 5: Supports texture UUID resolution
Bit 6–31: Reserved for future use
```

Rules:
- Once a bit is assigned, it CANNOT be reassigned.
- Deprecated capabilities keep their bit (marked as reserved).
- New capabilities MUST be announced in HELLO and checked in ACK.

## Reserved Bits

Bits 6–31 are reserved. Do not use without protocol version bump.

## Deprecation

When a capability is deprecated:
1. Keep the bit assigned (do not reuse).
2. Mark as "deprecated" in CapabilityBitmask.md.
3. New implementations SHOULD NOT announce it.
4. Old implementations that still announce it are handled gracefully (feature silently unused).
