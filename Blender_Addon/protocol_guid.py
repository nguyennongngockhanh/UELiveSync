"""
Shared UUID wire encoders for the semantic protocol.

Two byte layouts exist on the wire:

- FGuid/LE — canonical for Object-GUID references (actor identity).
  16 bytes packed as four little-endian uint32 (A, B, C, D) matching UE's
  FGuid internal layout. After UE's raw ``FMemory::Memcpy`` into ``FGuid``,
  these bytes render as the actor's ``LiveSync_GUID`` and are the actor-cache
  key used by ``FindActorFast``.

- RFC 4122 — canonical for material-namespace GUID references (``material_id``).
  16 raw bytes in network (big-endian) order, equal to ``uuid.UUID.bytes``.

MIG-006: ``FBX_IMPORT_REQUEST.persistent_id`` and
``MATERIAL_ASSIGN.persistent_id`` (Object-GUID references) previously used
RFC 4122 and therefore did not resolve the actor spawned by the OBJECT channel
(byte-swapped FGuid). They now use the FGuid/LE encoder like every other
Object-GUID reference.

See ``Docs/Investigations/INV-2026-016-fbx-uuid-encoding-divergence.md``.
"""

import struct
import uuid


def _coerce_uuid(value) -> uuid.UUID:
    """Accept a uuid.UUID or a UUID string; raise a clear error otherwise.

    Raw 16 bytes are NOT accepted here: pre-encoded bytes are already in a
    concrete wire layout and are passed through by the builders themselves.
    """
    if isinstance(value, uuid.UUID):
        return value
    if isinstance(value, str):
        return uuid.UUID(value)
    raise TypeError(
        "expected uuid.UUID or UUID string, got %s" % type(value).__name__)


def uuid_to_fguid_bytes(value) -> bytes:
    """Convert a UUID to 16 bytes in UE FGuid internal LE layout.

    FGuid stores A, B, C, D as uint32 in native (LE) byte order.
    ``FMemory::Memcpy`` reads these bytes directly, so we pack in the same
    layout as the legacy serializer (``struct.pack('<IIII', ...)``).

    This is DIFFERENT from ``uuid.UUID.bytes`` (big-endian RFC 4122). Using
    ``uuid.UUID.bytes`` produces a byte-swapped GUID that will not match actors
    spawned by the OBJECT channel.
    """
    u = _coerce_uuid(value)
    a = u.time_low
    b = (u.time_mid << 16) | u.time_hi_version
    c = ((u.clock_seq_hi_variant << 24) |
         (u.clock_seq_low << 16) |
         ((u.node >> 32) & 0xFFFF))
    d = u.node & 0xFFFFFFFF
    return struct.pack('<IIII', a, b, c, d)


def uuid_to_rfc4122_bytes(value) -> bytes:
    """Convert a UUID to 16 raw bytes in RFC 4122 / network byte order."""
    return _coerce_uuid(value).bytes
