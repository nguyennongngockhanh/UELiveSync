"""Manifest v3: two-table model, canonical serialization, atomic persistence.

This module implements the Manifest v3 specification for UELiveSync sidecar
asset tracking. It is purely a Blender-addon-side data layer — it does not
modify UE code, packet wire format, or any existing sidecar I/O behavior.

Top-level contract
==================

A manifest v3 document is a JSON object with this schema::

    {
        "schemaVersion": 3,
        "guid": "<normalized object GUID>",
        "generation": <int >= 1>,
        "semanticContentDigest": "<64-char lowercase hex SHA-256>",
        "occurrences": { "<occurrence_id>": { ... } },
        "assets":      { "<asset_id>":       { ... } }
    }

All field names are stable and documented below.

Occurrence record fields
------------------------
    slotIndex       int     — material slot index (0-based)
    channel         int     — MTEX channel number (1=BaseColor, etc.)
    materialIdentity str    — deterministic material identity string
    nodeIdentity    str     — deterministic node identity string
    sourceKind      str     — "FILE" | "PACKED" | other source kind
    sourceLocator   str     — canonicalized source locator (relative or image name)
    colorspace      str     — occurrence-level colorspace metadata
    assetId         str|None — 16-char asset_id if ready, None if failed
    status          str     — "ready" | "failed"

Asset record fields
-------------------
    sourceKind         str   — "FILE" | "PACKED"
    contentHash        str   — 16-char lowercase hex (equals asset key)
    destinationBasename str  — basename only, no absolute path
    destinationSize    int   — verified file size (>0 for ready)
    destinationHash    str   — must equal contentHash for ready
    status             str   — "ready" | "failed"

Canonical serialization
-----------------------
Canonical JSON uses:
    json.dumps(payload, sort_keys=True, separators=(",", ":"),
               ensure_ascii=True).encode("utf-8")

Semantic digest is SHA-256 of canonical JSON over a payload containing
schemaVersion, guid, occurrences, and assets (but NOT generation or
semanticContentDigest itself).

Generation policy
-----------------
    - no valid prior v3:                          generation = 1
    - valid prior v3, same semantic digest:       generation unchanged
    - valid prior v3, different semantic digest:  generation = prior + 1

Strict reader
-------------
Returns a frozen ManifestV3ReadResult dataclass with status "missing",
"valid", or "invalid" and an action field.

Atomic writer
-------------
Writes via tempfile.mkstemp → os.fdopen write → fsync → os.replace →
directory fsync, with full cleanup in finally.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import tempfile
import uuid
from dataclasses import dataclass, field, asdict
from typing import Any, Callable, Dict, List, Optional


# ================================================================
# Result dataclasses (immutable)
# ================================================================

@dataclass(frozen=True)
class ManifestV3ReadResult:
    """Strict read result for a manifest v3 file."""
    status: str          # "missing" | "valid" | "invalid"
    manifest: Optional[Dict[str, Any]]
    action: str          # "none" | "read" | "reject"
    error: str = ""


@dataclass(frozen=True)
class ManifestV3WriteResult:
    """Atomic write result."""
    status: str          # "success" | "failure" | "durability_uncertain"
    action: str          # "written" | "failed" | "replaced_directory_fsync_failed"
    manifest_path: str
    error: str = ""


@dataclass(frozen=True)
class ManifestV3ValidationResult:
    """Result from in-memory manifest validation."""
    valid: bool
    error: str = ""


@dataclass(frozen=True)
class ManifestV3IntegrationResult:
    """Structured result from the production manifest integration path.

    status:   "success" | "failure"
    action:   "written" | "conflict" | "generation_unchanged" | "failed"
    manifest_path: str
    generation: int
    semantic_digest: str
    prior_manifest: dict
    current_manifest: dict
    error: str = ""
    """
    status: str
    action: str
    manifest_path: str
    generation: int
    semantic_digest: str
    prior_manifest: dict = field(default_factory=dict)
    current_manifest: dict = field(default_factory=dict)
    error: str = ""


@dataclass(frozen=True)
class FBXPacketTransactionResult:
    """Structured result from an FBX packet transaction.

    status:   "success" | "failure" | "suppressed"
    action:   "sent" | "serialization_failed" | "send_failed" | "manifest_not_durable"
    sent:     True if the packet was actually transmitted
    error:    Human-readable error text (empty on success/suppressed)
    """
    status: str
    action: str
    sent: bool
    error: str = ""


# ================================================================
# Generation helper
# ================================================================


def derive_generation(
    prior_read_result: ManifestV3ReadResult,
    current_semantic_digest: str,
) -> int:
    """Derive the next generation number from prior state and current digest.

    Truth table:
        missing prior → 1
        invalid prior → 1
        valid prior, same digest → unchanged (prior_generation)
        valid prior, different digest → prior_generation + 1

    Parameters:
        prior_read_result: Result from read_manifest_v3().
        current_semantic_digest: Digest computed from current occurrences/assets.

    Returns:
        Integer generation >= 1.
    """
    if prior_read_result.status != "valid":
        return 1

    prior_manifest = prior_read_result.manifest
    prior_generation = prior_manifest["generation"]
    prior_digest = prior_manifest.get("semanticContentDigest", "")

    if prior_digest == current_semantic_digest:
        return prior_generation

    return prior_generation + 1


# ================================================================
# Constants
# ================================================================

MANIFEST_V3_SCHEMA_VERSION = 3
MANIFEST_V3_FILENAME = "manifest_v3.json"
SEMANTIC_DIGEST_LENGTH = 64  # SHA-256 hex length

# B: Exact preparation status allowlist
PREPARATION_STATUSES = frozenset({"ready", "failed"})

# C: Source-kind allowlist (shared across occurrence, asset, builder, reader)
VALID_SOURCE_KINDS = frozenset({"FILE", "PACKED", "GENERATED"})

# D: Exact nested field sets
OCCURRENCE_FIELDS = frozenset({
    "slotIndex", "channel", "materialIdentity",
    "nodeIdentity", "sourceKind", "sourceLocator",
    "colorspace", "assetId", "status",
})

ASSET_FIELDS = frozenset({
    "sourceKind", "contentHash", "destinationBasename",
    "destinationSize", "destinationHash", "status",
})


# ================================================================
# Helpers — deterministic occurrence ID
# ================================================================

def _canonical_tuple_bytes(
    guid: str,
    slot_index: int,
    material_identity: str,
    node_identity: str,
    channel: int,
) -> bytes:
    """Build a deterministic canonical tuple for occurrence-ID hashing.

    Format: length-prefixed fields separated by a null byte, all UTF-8.
    This avoids delimiter-collision attacks and handles Unicode safely.
    """
    parts: List[bytes] = []
    for val in (guid, str(slot_index), material_identity, node_identity, str(channel)):
        encoded = val.encode("utf-8")
        parts.append(len(encoded).to_bytes(4, "big"))
        parts.append(encoded)
    return b"\x00".join(parts)


def compute_occurrence_id(
    guid: str,
    slot_index: int,
    material_identity: str,
    node_identity: str,
    channel: int,
) -> str:
    """Compute a deterministic occurrence ID from semantic fields.

    Uses full SHA-256 hex digest (64 lowercase hex chars).
    """
    ct_bytes = _canonical_tuple_bytes(
        guid, slot_index, material_identity, node_identity, channel,
    )
    return hashlib.sha256(ct_bytes).hexdigest()


# ================================================================
# Helpers — canonical serialization
# ================================================================

def canonical_json_bytes(payload: Dict[str, Any]) -> bytes:
    """Return deterministic canonical JSON bytes for *payload*."""
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")


def compute_semantic_digest(
    guid: str,
    occurrences: Dict[str, Any],
    assets: Dict[str, Any],
) -> str:
    """Compute the semantic content digest over the semantic payload.

    Excludes generation and the digest itself.
    Returns 64-char lowercase hex.
    """
    semantic_payload = {
        "schemaVersion": MANIFEST_V3_SCHEMA_VERSION,
        "guid": guid,
        "occurrences": occurrences,
        "assets": assets,
    }
    canonical = canonical_json_bytes(semantic_payload)
    return hashlib.sha256(canonical).hexdigest()


def build_manifest_v3(
    guid: str,
    generation: int,
    *,
    occurrences: Optional[Dict[str, Any]] = None,
    assets: Optional[Dict[str, Any]] = None,
    semantic_digest: Optional[str] = None,
) -> Dict[str, Any]:
    """Build a complete manifest v3 dict (not yet serialized).

    ``occurrences`` and ``assets`` are keyword-only to prevent accidental
    positional mapping to ``semantic_digest``.

    If *semantic_digest* is None it is auto-computed from *occurrences*
    and *assets*.
    """
    if semantic_digest is not None:
        if not isinstance(semantic_digest, str):
            raise TypeError(
                f"semantic_digest must be a string, got {type(semantic_digest).__name__}"
            )
        if len(semantic_digest) != SEMANTIC_DIGEST_LENGTH:
            raise ValueError(
                f"semantic_digest must be {SEMANTIC_DIGEST_LENGTH} chars, "
                f"got {len(semantic_digest)}"
            )
    else:
        semantic_digest = compute_semantic_digest(
            guid, occurrences or {}, assets or {},
        )
    return {
        "schemaVersion": MANIFEST_V3_SCHEMA_VERSION,
        "guid": guid,
        "generation": generation,
        "semanticContentDigest": semantic_digest,
        "occurrences": occurrences,
        "assets": assets,
    }


def serialize_manifest_v3(manifest: Dict[str, Any]) -> str:
    """Serialize a manifest v3 dict to a deterministic UTF-8 JSON string.

    Canonical JSON: no indent, compact separators.
    """
    return canonical_json_bytes(manifest).decode("utf-8")


# ================================================================
# Strict reader / validator
# ================================================================

# Allowlisted top-level keys
_MANIFEST_V3_TOP_KEYS = frozenset({
    "schemaVersion", "guid", "generation",
    "semanticContentDigest", "occurrences", "assets",
})

# D: exact nested field sets (shared reference — avoid duplication)
# OCCURRENCE_FIELDS and ASSET_FIELDS are defined at module top


def _validate_hex_lower(s: str, length: int) -> bool:
    """Check if *s* is a lowercase hex string of exactly *length* chars."""
    if not isinstance(s, str):
        return False
    if len(s) != length:
        return False
    return all(c in "0123456789abcdef" for c in s)


def _is_safe_basename(basename: str) -> bool:
    """Check that a filename is safe (no separators, no absolute path)."""
    if not isinstance(basename, str) or not basename:
        return False
    if "/" in basename or "\\" in basename:
        return False
    # Reject dangerous separators in basename
    if ":" in basename:
        return False
    if basename in (".", ".."):
        return False
    return True


def is_safe_destination_basename(basename: str) -> bool:
    """Public check — is *basename* a safe destination filename?

    A safe basename must be:
      * nonempty string
      * free of / and \\
      * free of :
      * not `.` or `..`
    """
    return _is_safe_basename(basename)


def _validate_occurrence(
    occ_id: str, occ: Any, assets: Dict[str, Any],
) -> tuple:
    """Validate one occurrence record. Returns (ok, error_str)."""
    if not isinstance(occ, dict):
        return False, f"occurrence[{occ_id}] is not an object"

    # D: exact field set — reject both missing and extra keys
    if set(occ.keys()) != OCCURRENCE_FIELDS:
        return False, f"occurrence[{occ_id}] fields: {sorted(occ.keys())}"

    # slotIndex
    si = occ.get("slotIndex")
    if isinstance(si, bool) or not isinstance(si, int) or si < 0:
        return False, f"occurrence[{occ_id}].slotIndex invalid: {si!r}"

    # channel
    ch = occ.get("channel")
    if isinstance(ch, bool) or not isinstance(ch, int) or ch < 0:
        return False, f"occurrence[{occ_id}].channel invalid: {ch!r}"

    # materialIdentity
    mi = occ.get("materialIdentity")
    if not isinstance(mi, str):
        return False, f"occurrence[{occ_id}].materialIdentity not str"

    # nodeIdentity
    ni = occ.get("nodeIdentity")
    if not isinstance(ni, str):
        return False, f"occurrence[{occ_id}].nodeIdentity not str"

    # sourceKind
    sk = occ.get("sourceKind")
    if not isinstance(sk, str):
        return False, f"occurrence[{occ_id}].sourceKind not str"
    if sk not in VALID_SOURCE_KINDS:
        return False, f"occurrence[{occ_id}].sourceKind invalid: {sk!r}"

    # sourceLocator
    sl = occ.get("sourceLocator")
    if not isinstance(sl, str):
        return False, f"occurrence[{occ_id}].sourceLocator not str"

    # colorspace
    cs = occ.get("colorspace")
    if not isinstance(cs, str):
        return False, f"occurrence[{occ_id}].colorspace not str"

    # status
    status = occ.get("status")
    if status not in PREPARATION_STATUSES:
        return False, f"occurrence[{occ_id}].status invalid: {status!r}"

    # assetId
    asset_id = occ.get("assetId")
    if status == "ready":
        if asset_id is None:
            return False, f"occurrence[{occ_id}] ready with null assetId"
        if not isinstance(asset_id, str) or len(asset_id) != 16:
            return False, f"occurrence[{occ_id}] invalid assetId: {asset_id!r}"
        if asset_id not in assets:
            return False, f"occurrence[{occ_id}] ready assetId {asset_id} not in assets"
    elif status == "failed":
        if asset_id is not None:
            return False, f"occurrence[{occ_id}] failed with non-null assetId: {asset_id!r}"
    else:
        return False, f"occurrence[{occ_id}] unknown status: {status!r}"

    return True, ""


def _validate_asset(asset_id: str, asset: Any) -> tuple:
    """Validate one asset record. Returns (ok, error_str)."""
    if not isinstance(asset, dict):
        return False, f"asset[{asset_id}] is not an object"

    # D: exact field set — reject both missing and extra keys
    if set(asset.keys()) != ASSET_FIELDS:
        return False, f"asset[{asset_id}] fields: {sorted(asset.keys())}"

    # sourceKind
    sk = asset.get("sourceKind")
    if not isinstance(sk, str):
        return False, f"asset[{asset_id}].sourceKind not str"
    if sk not in VALID_SOURCE_KINDS:
        return False, f"asset[{asset_id}].sourceKind invalid: {sk!r}"

    # contentHash
    ch = asset.get("contentHash")
    if not _validate_hex_lower(ch, 16):
        return False, f"asset[{asset_id}].contentHash invalid"

    # key == contentHash
    if ch != asset_id:
        return False, f"asset[{asset_id}] key != contentHash"

    # destinationBasename
    db = asset.get("destinationBasename")
    if not isinstance(db, str):
        return False, f"asset[{asset_id}].destinationBasename not str"
    if not _is_safe_basename(db):
        return False, f"asset[{asset_id}] unsafe destinationBasename"

    # destinationSize
    ds = asset.get("destinationSize")
    if isinstance(ds, bool) or not isinstance(ds, int) or ds < 0:
        return False, f"asset[{asset_id}].destinationSize invalid"

    # destinationHash
    dh = asset.get("destinationHash")
    if not _validate_hex_lower(dh, 16):
        return False, f"asset[{asset_id}].destinationHash invalid"

    # For ready assets, destinationHash must equal contentHash
    status = asset.get("status", "")
    if status == "ready":
        if dh != ch:
            return False, f"asset[{asset_id}] ready with destinationHash != contentHash"

    # status
    if status not in PREPARATION_STATUSES:
        return False, f"asset[{asset_id}] invalid status: {status!r}"

    return True, ""


def validate_manifest_v3_object(
    manifest: Dict[str, Any],
    expected_guid: Optional[str] = None,
) -> ManifestV3ValidationResult:
    """E: validate an in-memory manifest dict against strict schema invariants.

    Returns ManifestV3ValidationResult — never raises.
    Used by write_manifest_v3 (before I/O) and read_manifest_v3 (after JSON parse).
    """
    if not isinstance(manifest, dict):
        return ManifestV3ValidationResult(False, "manifest is not a dict")
    if set(manifest.keys()) != _MANIFEST_V3_TOP_KEYS:
        return ManifestV3ValidationResult(
            False, f"top-level keys: {sorted(manifest.keys())}",
        )
    sv = manifest.get("schemaVersion")
    if sv != MANIFEST_V3_SCHEMA_VERSION:
        return ManifestV3ValidationResult(
            False, f"schemaVersion {sv!r} != {MANIFEST_V3_SCHEMA_VERSION}",
        )
    guid = manifest.get("guid")
    if not isinstance(guid, str) or not guid:
        return ManifestV3ValidationResult(False, "missing or invalid guid")
    if expected_guid is not None and guid != expected_guid:
        return ManifestV3ValidationResult(
            False, f"GUID mismatch: expected={expected_guid} got={guid}",
        )
    gen = manifest.get("generation")
    if isinstance(gen, bool) or not isinstance(gen, int) or gen < 1:
        return ManifestV3ValidationResult(False, f"generation invalid: {gen!r}")
    digest = manifest.get("semanticContentDigest")
    if not _validate_hex_lower(digest, SEMANTIC_DIGEST_LENGTH):
        return ManifestV3ValidationResult(False, "malformed semanticContentDigest")
    occurrences = manifest.get("occurrences")
    if not isinstance(occurrences, dict):
        return ManifestV3ValidationResult(False, "occurrences is not an object")
    for occ_id, occ in occurrences.items():
        if not _validate_hex_lower(occ_id, 64):
            return ManifestV3ValidationResult(
                False, f"malformed occurrence id: {occ_id!r}",
            )
        ok, err = _validate_occurrence(occ_id, occ, manifest.get("assets", {}))
        if not ok:
            return ManifestV3ValidationResult(False, err)
    assets = manifest.get("assets")
    if not isinstance(assets, dict):
        return ManifestV3ValidationResult(False, "assets is not an object")
    for asset_id, asset in assets.items():
        ok, err = _validate_asset(asset_id, asset)
        if not ok:
            return ManifestV3ValidationResult(False, err)
    recomputed = compute_semantic_digest(guid, occurrences, assets)
    if recomputed != digest:
        return ManifestV3ValidationResult(False, "digest mismatch after recomputation")
    return ManifestV3ValidationResult(True)


def read_manifest_v3(
    manifest_path: str,
    expected_guid: Optional[str] = None,
) -> ManifestV3ReadResult:
    """Strictly read and validate a manifest v3 file.

    Args:
        manifest_path: Path to the manifest file.
        expected_guid: If provided, GUID must match.

    Returns:
        ManifestV3ReadResult — never returns untyped truthy/falsy.
    """
    # Missing file
    if not os.path.isfile(manifest_path):
        return ManifestV3ReadResult(
            status="missing",
            manifest=None,
            action="none",
        )

    # Read JSON
    try:
        with open(manifest_path, "r", encoding="utf-8") as fh:
            raw = fh.read()
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError):
        return ManifestV3ReadResult(
            status="invalid",
            manifest=None,
            action="reject",
            error="malformed JSON or read error",
        )

    # Delegate to shared in-memory validator
    vr = validate_manifest_v3_object(data, expected_guid)
    if not vr.valid:
        return ManifestV3ReadResult(
            status="invalid",
            manifest=None,
            action="reject",
            error=vr.error,
        )
    return ManifestV3ReadResult(
        status="valid",
        manifest=dict(data),
        action="read",
    )


# ================================================================
# Atomic writer
# ================================================================

def write_manifest_v3(
    manifest_path: str,
    obj_dir: str,
    manifest: Dict[str, Any],
) -> ManifestV3WriteResult:
    """Atomically write a manifest v3 file.

    Write sequence:
        1. tempfile.mkstemp in obj_dir
        2. write UTF-8 deterministic JSON
        3. flush + fsync file
        4. close file
        5. os.replace(temp, manifest_path)
        6. fsync obj_dir
        7. cleanup temp on failure

    Returns ManifestV3WriteResult — never claims success on failure.
    """
    # E: validate manifest schema before touching disk
    vr = validate_manifest_v3_object(manifest)
    if not vr.valid:
        return ManifestV3WriteResult(
            status="failure",
            action="failed",
            manifest_path=manifest_path,
            error=vr.error,
        )

    fd = -1
    tmp_path = ""
    replaced = False
    primary_status = None
    primary_action = None
    primary_error = ""
    success_result = None
    cleanup_errors: list[str] = []
    try:
        fd, tmp_path = tempfile.mkstemp(
            dir=obj_dir,
            prefix="manifest_v3_",
            suffix=".tmp",
        )
        # Write canonical bytes directly in binary mode
        canonical = canonical_json_bytes(manifest)
        with os.fdopen(fd, "wb") as stream:
            fd = -1  # os.fdopen took ownership
            stream.write(canonical)
            stream.flush()
            os.fsync(stream.fileno())

        os.replace(tmp_path, manifest_path)
        replaced = True

        # fsync directory
        dir_fsync_ok = True
        try:
            dir_fd = os.open(obj_dir, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except OSError:
            dir_fsync_ok = False

        if dir_fsync_ok:
            success_result = ManifestV3WriteResult(
                status="success",
                action="written",
                manifest_path=manifest_path,
            )
        else:
            # os.replace() has already succeeded: the new manifest is visible.
            # But full durability is not confirmed.
            success_result = ManifestV3WriteResult(
                status="durability_uncertain",
                action="replaced_directory_fsync_failed",
                manifest_path=manifest_path,
                error="directory fsync failed after os.replace — manifest may be visible but durability not confirmed",
            )

    except Exception as exc:
        primary_status = "failure"
        primary_action = "failed"
        primary_error = str(exc)
    finally:
        # Always close fd if still open
        if fd >= 0:
            try:
                os.close(fd)
            except OSError as exc:
                cleanup_errors.append(f"fd_close: {exc}")
        # Clean up leaked temp
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError as exc:
                cleanup_errors.append(f"temp_unlink: {exc}")

    # Post-cleanup: construct the return value AFTER finally completes.
    if primary_status is not None:
        # Primary failure before successful replace.
        error_text = primary_error
        if cleanup_errors:
            error_text += "; cleanup_errors=" + "; ".join(cleanup_errors)
        return ManifestV3WriteResult(
            status=primary_status,
            action=primary_action,
            manifest_path=manifest_path,
            error=error_text,
        )

    # No primary failure: we have success_result.
    # Post-replace cleanup errors affect durability.
    if cleanup_errors:
        if replaced:
            # os.replace succeeded, but cleanup failed.
            # The manifest is visible but a temp may leak.
            return ManifestV3WriteResult(
                status="durability_uncertain",
                action="replaced_cleanup_failed",
                manifest_path=manifest_path,
                error="cleanup error after successful replace: " + "; ".join(cleanup_errors),
            )
        else:
            # Replace never reached; temp cleanup failed but manifest not visible.
            return ManifestV3WriteResult(
                status="durability_uncertain",
                action="failed_cleanup_failed",
                manifest_path=manifest_path,
                error="failure before replace; cleanup error: " + "; ".join(cleanup_errors),
            )

    return success_result


# ================================================================
# Two-table builder from A3.3 results
# ================================================================

def build_occurrence_record(
    slot_index: int,
    channel: int,
    material_identity: str,
    node_identity: str,
    source_kind: str,
    source_locator: str,
    colorspace: str,
    asset_id: Optional[str],
    status: str,
) -> Dict[str, Any]:
    """Build one deterministic occurrence record."""
    return {
        "slotIndex": slot_index,
        "channel": channel,
        "materialIdentity": material_identity,
        "nodeIdentity": node_identity,
        "sourceKind": source_kind,
        "sourceLocator": source_locator,
        "colorspace": colorspace,
        "assetId": asset_id,
        "status": status,
    }


def build_asset_record(
    source_kind: str,
    content_hash: str,
    destination_basename: str,
    destination_size: int,
    destination_hash: str,
    status: str,
) -> Dict[str, Any]:
    """Build one deterministic asset record."""
    return {
        "sourceKind": source_kind,
        "contentHash": content_hash,
        "destinationBasename": destination_basename,
        "destinationSize": destination_size,
        "destinationHash": destination_hash,
        "status": status,
    }


# ================================================================
# Conflict-safe table insertion helpers
# ================================================================


@dataclass(frozen=True)
class ConflictResult:
    """Result of a conflict-safe table insertion."""
    conflict: bool
    key: str
    detail: str = ""


def _record_canonical_tuple(record: Dict[str, Any]) -> tuple:
    """Return a deterministic canonical tuple for comparing two records.

    Values are sorted by key name; nested dicts are recursively canonicalized.
    Lists are compared element-by-element.
    """
    items = []
    for k in sorted(record.keys()):
        v = record[k]
        if isinstance(v, dict):
            items.append((k, _record_canonical_tuple(v)))
        elif isinstance(v, (list, tuple)):
            items.append((k, tuple(v)))
        else:
            items.append((k, v))
    return tuple(items)


def insert_occurrence_record(
    table: Dict[str, Any],
    occurrence_id: str,
    record: Dict[str, Any],
) -> ConflictResult:
    """Insert an occurrence record with conflict detection.

    Returns:
        ConflictResult(conflict=False, key=occurrence_id) on insert or duplicate.
        ConflictResult(conflict=True, key=occurrence_id, detail=...) on conflict.
    """
    if occurrence_id not in table:
        table[occurrence_id] = record
        return ConflictResult(conflict=False, key=occurrence_id)

    existing_canonical = _record_canonical_tuple(table[occurrence_id])
    new_canonical = _record_canonical_tuple(record)

    if existing_canonical == new_canonical:
        # Identical record — accept as duplicate
        return ConflictResult(conflict=False, key=occurrence_id)

    return ConflictResult(
        conflict=True,
        key=occurrence_id,
        detail=f"conflicting occurrence record for {occurrence_id}",
    )


def insert_asset_record(
    table: Dict[str, Any],
    asset_id: str,
    record: Dict[str, Any],
) -> ConflictResult:
    """Insert an asset record with conflict detection.

    Returns:
        ConflictResult(conflict=False, key=asset_id) on insert or duplicate.
        ConflictResult(conflict=True, key=asset_id, detail=...) on conflict.
    """
    if asset_id not in table:
        table[asset_id] = record
        return ConflictResult(conflict=False, key=asset_id)

    existing_canonical = _record_canonical_tuple(table[asset_id])
    new_canonical = _record_canonical_tuple(record)

    if existing_canonical == new_canonical:
        return ConflictResult(conflict=False, key=asset_id)

    return ConflictResult(
        conflict=True,
        key=asset_id,
        detail=f"conflicting asset record for {asset_id}",
    )


# ================================================================
# Validation helpers for ready results (Block I)
# ================================================================


def validate_ready_asset(asset_id: str, filename: str, size: int) -> tuple:
    """Validate a ready-sidecar result for manifest integration.

    Returns (ok, error_str). ok=False means the result is malformed and
    should be treated as failed, not ready.
    """
    # asset_id must be exactly 16 lowercase hex
    if not _validate_hex_lower(asset_id, 16):
        return False, f"invalid asset_id: {asset_id!r}"

    # filename must be a safe basename
    if not _is_safe_basename(filename):
        return False, f"unsafe destination filename: {filename!r}"

    # size must be a non-negative int (not bool)
    if isinstance(size, bool) or not isinstance(size, int) or size < 0:
        return False, f"invalid size: {size!r}"

    return True, ""


def validate_source_locator(locator: str) -> bool:
    """Check that a source locator is a valid semantic path.

    Returns True if the locator is a non-empty string.
    """
    return isinstance(locator, str) and len(locator) > 0


# ================================================================
# Production integration helper (Block G)
# ================================================================


def _validate_result_for_ready(result_any) -> bool:
    """Check that a sidecar result claims ready with valid fields.

    result_any is expected to be an object with:
        status, asset_id, filename, size, source_locator

    Returns True only if status is 'ready' AND all fields are valid.
    """
    if not hasattr(result_any, "status"):
        return False
    if result_any.status != "ready":
        return False
    # Validate asset_id
    aid = getattr(result_any, "asset_id", None)
    if not _validate_hex_lower(str(aid), 16):
        return False
    # Validate filename
    fn = getattr(result_any, "filename", None)
    if not fn or not _is_safe_basename(str(fn)):
        return False
    # Validate size
    sz = getattr(result_any, "size", None)
    if not isinstance(sz, int) or sz < 0:
        return False
    return True


def persist_manifest_v3(
    guid_hex: str,
    obj_dir: str,
    manifest_path: str,
    usages: Any,  # iterable of usage objects
    results_by_source: dict,  # id(source) -> result object
) -> ManifestV3IntegrationResult:
    """Full production manifest integration path.

    Performs: prior read, occurrence/asset construction, conflict detection,
    semantic digest, generation derivation, manifest build, atomic write.

    Args:
        guid_hex: Normalized object GUID.
        obj_dir: Authoritative object directory.
        manifest_path: Full path to manifest_v3.json.
        usages: Iterable of usage objects with fields: source, slot_index, channel.
        results_by_source: id(source) -> SidecarPreparationResult.

    Returns:
        ManifestV3IntegrationResult with structured outcome.
    """
    # 1. Read valid prior v3
    prior = read_manifest_v3(manifest_path, expected_guid=guid_hex)
    prior_manifest = (
        copy.deepcopy(prior.manifest)
        if prior.status == "valid" and prior.manifest is not None
        else {}
    )

    # 2. Build occurrences and assets
    occurrences: dict = {}
    assets: dict = {}
    conflicts: list = []

    for usage in usages:
        src = usage.source
        node_id = f"{src.mat_name}/{src.node_name}"
        occ_id = compute_occurrence_id(
            guid=guid_hex,
            slot_index=usage.slot_index,
            material_identity=src.mat_name,
            node_identity=node_id,
            channel=usage.channel,
        )

        result = results_by_source.get(id(src))

        # B: reject None and every unknown preparation status
        if result is not None and result.status not in PREPARATION_STATUSES:
            return ManifestV3IntegrationResult(
                status="failure",
                action="invalid_preparation_status",
                manifest_path=manifest_path,
                generation=0,
                semantic_digest="",
                prior_manifest=prior_manifest,
                current_manifest={},
                error=f"unknown preparation status: {result.status!r}",
            )

        asset_id = None
        status = "failed"

        # Block I: ready result with unsafe filename fails closed
        if result is not None and result.status == "ready":
            if not is_safe_destination_basename(str(result.filename)):
                return ManifestV3IntegrationResult(
                    status="failure",
                    action="invalid_ready_result",
                    manifest_path=manifest_path,
                    generation=0,
                    semantic_digest="",
                    prior_manifest=prior_manifest,
                    current_manifest={},
                    error=f"unsafe destination filename in ready result: {result.filename!r}",
                )

        if _validate_result_for_ready(result):
            asset_id = str(result.asset_id)
            status = "ready"
            # Build asset record
            asset_rec = build_asset_record(
                source_kind=src.source_kind,
                content_hash=asset_id,
                destination_basename=str(result.filename),
                destination_size=result.size,
                destination_hash=asset_id,
                status="ready",
            )
            asset_conflict = insert_asset_record(assets, asset_id, asset_rec)
            if asset_conflict.conflict:
                conflicts.append(asset_conflict)

        # Build occurrence record
        source_locator = ""
        if result is not None and hasattr(result, "source_locator"):
            source_locator = result.source_locator or ""
        if not source_locator and hasattr(src, "filepath_raw") and src.filepath_raw:
            source_locator = src.filepath_raw

        occ_rec = build_occurrence_record(
            slot_index=usage.slot_index,
            channel=usage.channel,
            material_identity=src.mat_name,
            node_identity=node_id,
            source_kind=src.source_kind,
            source_locator=source_locator,
            colorspace=getattr(src, "colorspace", ""),
            asset_id=asset_id,
            status=status,
        )
        occ_conflict = insert_occurrence_record(occurrences, occ_id, occ_rec)
        if occ_conflict.conflict:
            conflicts.append(occ_conflict)

    # 3. Check for conflicts
    if conflicts:
        conflict_details = "; ".join(c.detail for c in conflicts)
        return ManifestV3IntegrationResult(
            status="failure",
            action="conflict",
            manifest_path=manifest_path,
            generation=0,
            semantic_digest="",
            prior_manifest=prior_manifest,
            current_manifest={},
            error=conflict_details,
        )

    # 4. Compute semantic digest
    semantic_digest = compute_semantic_digest(guid_hex, occurrences, assets)

    # 5. Derive generation
    generation = derive_generation(prior, semantic_digest)

    # 6. Build manifest
    manifest = build_manifest_v3(
        guid=guid_hex,
        generation=generation,
        semantic_digest=semantic_digest,
        occurrences=occurrences,
        assets=assets,
    )
    manifest_snapshot = copy.deepcopy(manifest)

    # 7. Atomic write
    write_result = write_manifest_v3(manifest_path, obj_dir, manifest)

    if write_result.status == "success" and write_result.action == "written":
        return ManifestV3IntegrationResult(
            status="success",
            action="written",
            manifest_path=write_result.manifest_path,
            generation=generation,
            semantic_digest=semantic_digest,
            prior_manifest=prior_manifest,
            current_manifest=manifest_snapshot,
        )
    elif write_result.status == "durability_uncertain":
        # Post-replace fsync failed — manifest may be visible but
        # durability is not confirmed. Treat as failure (fail-closed).
        return ManifestV3IntegrationResult(
            status="failure",
            action="failed",
            manifest_path=write_result.manifest_path,
            generation=generation,
            semantic_digest=semantic_digest,
            prior_manifest=prior_manifest,
            current_manifest=manifest_snapshot,
            error=write_result.error,
        )
    else:
        # Pre-replace failure
        return ManifestV3IntegrationResult(
            status="failure",
            action="failed",
            manifest_path=write_result.manifest_path,
            generation=generation,
            semantic_digest=semantic_digest,
            prior_manifest=prior_manifest,
            current_manifest=manifest_snapshot,
            error=write_result.error,
        )


# ================================================================
# Production orchestration helper (preparation + persist + send)
# ================================================================


def run_manifest_pipeline(
    guid_hex: str,
    obj_dir: str,
    manifest_path: str,
    usages: list,
    results_by_source: dict,
    on_durable_success: Optional[Callable[[ManifestV3IntegrationResult], None]] = None,
) -> ManifestV3IntegrationResult:
    """Production manifest v3 pipeline.

    Persists manifest v3 from already-prepared sidecar results and
    optionally invokes *on_durable_success* when the write is fully durable
    (status="success", action="written").

    This is the single entry point called by the FBX operator.
    Preparation (sidecar copy/export) occurs before calling this function.

    Args:
        guid_hex: Normalized object GUID.
        obj_dir: Authoritative object directory.
        manifest_path: Full path to manifest_v3.json.
        usages: Iterable of usage objects (with source, slot_index, channel).
        results_by_source: dict[id(source)] -> SidecarPreparationResult.
        on_durable_success: Optional callback invoked on fully durable write.

    Returns:
        ManifestV3IntegrationResult — structured outcome.
    """
    integration_result = persist_manifest_v3(
        guid_hex, obj_dir, manifest_path, usages, results_by_source,
    )
    if (integration_result.status == "success"
            and integration_result.action == "written"
            and on_durable_success is not None):
        on_durable_success(integration_result)
    return integration_result


def run_prepare_and_persist_v3(
    sources,
    usages,
    obj_dir,
    guid_hex,
    collision_registry,
    prepare_source_fn,
    result_by_source_fn,
    guid_short="?",
):
    """F: production helper — prepare sources then persist manifest v3.

    Args:
        sources: Iterable of source objects (each passed to *prepare_source_fn*).
        usages: Usage list for manifest persistence.
        obj_dir: Authoritative object cache directory.
        guid_hex: Normalized object GUID.
        collision_registry: Shared dict passed to every prepare call.
        prepare_source_fn: Callable(source, obj_dir, collision_registry, guid_short)
                           -> SidecarPreparationResult.
        result_by_source_fn: Callable(list[SidecarPreparationResult])
                             -> dict mapping source id to result.
        guid_short: Short GUID for logging.

    Returns:
        Tuple (ManifestV3IntegrationResult, list[SidecarPreparationResult]).
    """
    results = [
        prepare_source_fn(src, obj_dir, collision_registry, guid_short)
        for src in sources
    ]
    results_by_source = result_by_source_fn(results)
    manifest_path = os.path.join(obj_dir, MANIFEST_V3_FILENAME)
    manifest_result = run_manifest_pipeline(
        guid_hex=guid_hex,
        obj_dir=obj_dir,
        manifest_path=manifest_path,
        usages=usages,
        results_by_source=results_by_source,
    )
    return manifest_result, results


def should_send_after_pipeline(manifest_result: ManifestV3IntegrationResult) -> bool:
    """G: return True only when the pipeline succeeded and the manifest was written.

    The operator calls this to decide whether to invoke ``network.send_objects``.
    """
    return manifest_result.status == "success" and manifest_result.action == "written"


def send_fbx_packet_if_manifest_durable(
    manifest_result: ManifestV3IntegrationResult,
    send_fn: Callable,
    payload: Any,
    *,
    packet_type: Any,
    version: int,
) -> bool:
    """D: send an FBX packet only when the manifest pipeline was fully durable.

    Args:
        manifest_result: Result from run_manifest_pipeline.
        send_fn: Callable accepting (payloads, packet_type, version).
        payload: Single payload object (wrapped in a list for send_fn).
        packet_type: Protocol packet type constant.
        version: Protocol version.

    Returns:
        True if the packet was sent, False if suppressed.
    """
    if not should_send_after_pipeline(manifest_result):
        return False
    send_fn(
        [payload],
        packet_type=packet_type,
        version=version,
    )
    return True


def serialize_and_send_fbx_request(
    *,
    manifest_result: ManifestV3IntegrationResult,
    serialize_fn: Callable,
    send_fn: Callable,
    guid_obj: str,
    fbx_path: str,
    object_name: str,
    vert_count: int,
    tri_count: int,
    mat_slot_count: int,
    timestamp: float,
    geometry_hash: int,
    packet_type: Any,
    version: int,
) -> FBXPacketTransactionResult:
    """C: serialize the FBX payload and send if the manifest pipeline was durable.

    Returns FBXPacketTransactionResult with exact outcome:
      - "suppressed"/"manifest_not_durable" when the pipeline did not succeed.
      - "failure"/"serialization_failed" when the serialize_fn raises.
      - "failure"/"send_failed" when the send_fn raises.
      - "success"/"sent" when the packet was transmitted.

    The operator calls this after all mesh evaluation and sidecar work.
    """
    from . import network as _net
    if not should_send_after_pipeline(manifest_result):
        _net._append_blender_debug_log(
            f"[FBX_ENQUEUE_SKIP] guid={str(guid_obj)[:8]} reason=manifest_not_durable"
        )
        return FBXPacketTransactionResult(
            status="suppressed",
            action="manifest_not_durable",
            sent=False,
        )

    try:
        payload = serialize_fn(
            guid_obj=guid_obj,
            fbx_path=fbx_path,
            object_name=object_name,
            vert_count=vert_count,
            tri_count=tri_count,
            mat_slot_count=mat_slot_count,
            timestamp=timestamp,
            geometry_hash=geometry_hash,
        )
        _net._append_blender_debug_log(
            f"[FBX_ENQUEUE] guid={str(guid_obj)[:8]} payload_bytes={len(payload)} "
            f"packet_type=0x{packet_type:02x} version={version}"
        )
    except Exception as exc:
        _net._append_blender_debug_log(
            f"[FBX_ENQUEUE_FAIL] guid={str(guid_obj)[:8]} reason=serialization_failed error={exc}"
        )
        return FBXPacketTransactionResult(
            status="failure",
            action="serialization_failed",
            sent=False,
            error=str(exc),
        )

    try:
        send_fn(
            [payload],
            packet_type=packet_type,
            version=version,
        )
        _net._append_blender_debug_log(
            f"[FBX_ENQUEUE_SENT] guid={str(guid_obj)[:8]} status=send_fn_returned"
        )
    except Exception as exc:
        _net._append_blender_debug_log(
            f"[FBX_ENQUEUE_FAIL] guid={str(guid_obj)[:8]} reason=send_failed error={exc}"
        )
        return FBXPacketTransactionResult(
            status="failure",
            action="send_failed",
            sent=False,
            error=str(exc),
        )

    return FBXPacketTransactionResult(
        status="success",
        action="sent",
        sent=True,
    )
