"""Phase 10A.3.5 — Manifest-informed sidecar reuse decisions.

Decision logic is kept out of the Blender operator to keep the operator
thin and testable.  This module must NOT import ``bpy`` at import time.
"""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

# ──────────────────────────────────────────────────────────────────────
# Allowed decisions exactly
# ──────────────────────────────────────────────────────────────────────
_DECISION_REUSE = "reuse"
_DECISION_PREPARE = "prepare"
_DECISION_REJECT = "reject"

# ──────────────────────────────────────────────────────────────────────
# Allowed actions exactly
# ──────────────────────────────────────────────────────────────────────
_ACT_REUSE_ALLOWED = "reuse_allowed"
_ACT_MANIFEST_MISSING = "manifest_missing"
_ACT_MANIFEST_INVALID = "manifest_invalid"
_ACT_OCCURRENCE_MISSING = "occurrence_missing"
_ACT_OCCURRENCE_IDENTITY_MISMATCH = "occurrence_identity_mismatch"
_ACT_SOURCE_IDENTITY_CHANGED = "source_identity_changed"
_ACT_ASSET_MISSING = "asset_missing"
_ACT_ASSET_IDENTITY_INCONSISTENT = "asset_identity_inconsistent"
_ACT_OCCURRENCE_STATUS_NOT_READY = "occurrence_status_not_ready"
_ACT_ASSET_STATUS_NOT_READY = "asset_status_not_ready"
_ACT_DESTINATION_BASENAME_UNSAFE = "destination_basename_unsafe"
_ACT_DESTINATION_MISSING = "destination_missing"
_ACT_DESTINATION_NOT_REGULAR = "destination_not_regular"
_ACT_DESTINATION_SYMLINK_ESCAPE = "destination_symlink_escape"
_ACT_DESTINATION_SIZE_MISMATCH = "destination_size_mismatch"
_ACT_DESTINATION_HASH_MISMATCH = "destination_hash_mismatch"
_ACT_PREPARE_REQUIRED = "prepare_required"


@dataclass(frozen=True)
class ReuseDecision:
    """One structured decision per occurrence/source.

    Fields:
        decision: "reuse" | "prepare" | "reject"
        action: exact diagnostic reason string
        occurrence_id: computed occurrence ID (may be "")
        asset_id: prior asset ID (may be "")
        source_kind: "FILE" | "PACKED" | "GENERATED"
        destination_path: resolved destination path (may be "")
        error: human-readable error (may be "")
    """
    decision: str
    action: str
    occurrence_id: str
    asset_id: str
    source_kind: str
    destination_path: str
    error: str = ""


@dataclass(frozen=True)
class ReuseOutcome:
    """Outcome for the full object-level evaluation."""
    manifest_status: str        # "missing" | "invalid" | "valid"
    prior_manifest_eligible_for_generation: bool
    global_reuse_denied: bool
    decisions: Dict[int, ReuseDecision]  # src_id -> decision
    current_content_hexes: Dict[int, str]  # src_id -> hex
    current_assets: Dict[str, dict]  # asset_id -> asset dict (for reuse paths)
    prior_generation: int
    error: str = ""


# ──────────────────────────────────────────────────────────────────────
# Path and destination safety
# ──────────────────────────────────────────────────────────────────────

def is_safe_basename(basename: str) -> bool:
    """Return True only if *basename* is safe for a destination filename."""
    if not isinstance(basename, str) or not basename:
        return False
    if "/" in basename or "\\" in basename:
        return False
    if basename in (".", ".."):
        return False
    if os.path.isabs(basename):
        return False
    return True


def validate_path_safety(
    sidecar_dir: str,
    dest_path: str,
) -> tuple:
    """Validate that *dest_path* is safe within *sidecar_dir*."""
    if not os.path.isdir(sidecar_dir):
        return (False, "sidecar_dir_not_found")
    real_sidecar = os.path.realpath(sidecar_dir)
    real_dest = os.path.realpath(dest_path)
    try:
        common = os.path.commonpath([real_sidecar, real_dest])
    except ValueError:
        return (False, "path_incompatible")
    if common != real_sidecar:
        return (False, "path_escape_detected")
    return (True, "")


def compute_file_hash_hex(path: str, chunk_size: int = 1048576) -> str:
    """Compute xxh64 of file bytes, returning 16-char lowercase hex."""
    from . import network
    return network._xxh64_file_hex(path, chunk_size)


def compute_bytes_hash_hex(data: bytes) -> str:
    """Compute xxh64 of in-memory bytes, returning 16-char lowercase hex."""
    from . import network
    return format(network.xxh64(data), '016x')
# ──────────────────────────────────────────────────────────────────────
# Source-level: extract current content identity without materialization
# ──────────────────────────────────────────────────────────────────────

def extract_source_bytes_file(filepath_raw: str, filepath: str) -> Optional[bytes]:
    """Extract bytes from a FILE source. Returns None if file missing."""
    import bpy
    abs_path = bpy.path.abspath(filepath_raw or filepath)
    if not os.path.isfile(abs_path):
        return None
    with open(abs_path, 'rb') as fh:
        return fh.read()


def extract_source_bytes_packed(image_name: str) -> Optional[bytes]:
    """Extract packed bytes from a PACKED source. Returns None if missing."""
    import bpy
    img = bpy.data.images.get(image_name)
    if img is None:
        return None
    pf = getattr(img, "packed_file", None)
    if pf is None:
        return None
    return bytes(pf.data)


def extract_source_bytes_generated(
    image_name: str,
    dest_dir: str,
    file_format: str,
    guid_short: str = "?",
) -> tuple:
    """Extract bytes from a GENERATED source via render.

    Writes to a temp file in *dest_dir*, reads back bytes, then cleans up.
    Returns (bytes_or_None, temp_path_or_None).
    """
    import bpy
    import tempfile
    ext_map = {
        "PNG": ".png", "JPEG": ".jpg", "JPEG2000": ".jp2",
        "TARGA": ".tga", "TIFF": ".tif", "OPEN_EXR": ".exr",
        "BMP": ".bmp", "HDR": ".hdr",
    }
    ext = ext_map.get(file_format, ".png")
    img = bpy.data.images.get(image_name)
    if img is None:
        return (None, None)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            suffix=ext, delete=False, dir=dest_dir,
        ) as tf:
            temp_path = tf.name
        img.save_render(temp_path)
        with open(temp_path, 'rb') as fh:
            data = fh.read()
        return (data, temp_path)
    except Exception:
        if temp_path and os.path.isfile(temp_path):
            os.unlink(temp_path)
        return (None, None)


def compute_source_content_hex(
    source_kind: str,
    source,
    dest_dir: str,
    guid_short: str = "?",
) -> tuple:
    """Compute current content identity for a source WITHOUT materialization.

    Returns (content_hex_16char_or_None, raw_bytes_or_None).
    FILE: reads from disk. PACKED: extracts from Blender image.
    GENERATED: renders to temp, reads bytes, cleans up temp.
    """
    from . import network
    if source_kind == "FILE" and not source.is_packed:
        raw = extract_source_bytes_file(source.filepath_raw, source.filepath)
        if raw is None:
            return (None, None)
        return (format(network.xxh64(raw), '016x'), raw)
    elif source.is_packed:
        raw = extract_source_bytes_packed(source.image_name)
        if raw is None:
            return (None, None)
        return (format(network.xxh64(raw), '016x'), raw)
    elif source_kind == "GENERATED":
        raw, temp_path = extract_source_bytes_generated(
            source.image_name, dest_dir, source.file_format, guid_short,
        )
        if raw is None:
            return (None, None)
        return (format(network.xxh64(raw), '016x'), raw)
    return (None, None)
# ──────────────────────────────────────────────────────────────────────
# Manifest reading helpers (non-bpy)
# ──────────────────────────────────────────────────────────────────────

def read_prior_manifest(
    manifest_path: str,
    expected_guid: Optional[str] = None,
) -> tuple:
    """Read a prior manifest file. Returns (status, data_or_None)."""
    if not os.path.isfile(manifest_path):
        return ("missing", None)
    try:
        with open(manifest_path, 'r', encoding='utf-8') as fh:
            raw = fh.read()
        import json
        data = json.loads(raw)
        if not isinstance(data, dict):
            return ("invalid", None)
        return ("read", data)
    except (OSError, json.JSONDecodeError):
        return ("invalid", None)


def validate_prior_manifest_schema(
    data: dict,
    expected_guid: Optional[str] = None,
) -> tuple:
    """Validate prior manifest schema. Returns (is_valid, error_reason)."""
    from .manifest_v3 import (
        MANIFEST_V3_SCHEMA_VERSION,
        _MANIFEST_V3_TOP_KEYS,
        _validate_hex_lower,
        _validate_occurrence,
        _validate_asset,
        compute_semantic_digest,
    )
    if not isinstance(data, dict):
        return (False, "not_a_dict")
    if set(data.keys()) != _MANIFEST_V3_TOP_KEYS:
        return (False, "invalid_top_level_keys")
    sv = data.get("schemaVersion")
    if sv != MANIFEST_V3_SCHEMA_VERSION:
        return (False, "unknown_schema")
    guid = data.get("guid")
    if not isinstance(guid, str) or not guid:
        return (False, "missing_guid")
    if expected_guid is not None and guid != expected_guid:
        return (False, "guid_mismatch")
    gen = data.get("generation")
    if isinstance(gen, bool) or not isinstance(gen, int) or gen < 1:
        return (False, "invalid_generation")
    digest = data.get("semanticContentDigest")
    if not _validate_hex_lower(digest, 64):
        return (False, "invalid_semantic_digest")
    occurrences = data.get("occurrences")
    if not isinstance(occurrences, dict):
        return (False, "occurrences_not_dict")
    for occ_id, occ in occurrences.items():
        if not _validate_hex_lower(occ_id, 64):
            return (False, f"malformed_occurrence_id:{occ_id!r}")
        ok, err = _validate_occurrence(occ_id, occ, data.get("assets", {}))
        if not ok:
            return (False, err)
    assets = data.get("assets")
    if not isinstance(assets, dict):
        return (False, "assets_not_dict")
    for asset_id, asset in assets.items():
        ok, err = _validate_asset(asset_id, asset)
        if not ok:
            return (False, err)
    recomputed = compute_semantic_digest(
        data.get("guid", ""), occurrences, assets,
    )
    if recomputed != digest:
        return (False, "digest_mismatch")
    return (True, "")
# ──────────────────────────────────────────────────────────────────────
# Occurrence-level matching
# ──────────────────────────────────────────────────────────────────────

def compute_occurrence_id_for_current(
    guid_hex: str,
    slot_index: int,
    material_identity: str,
    node_identity: str,
    channel: int,
) -> str:
    """Compute the occurrence ID for the current object."""
    from .manifest_v3 import compute_occurrence_id
    return compute_occurrence_id(
        guid=guid_hex,
        slot_index=slot_index,
        material_identity=material_identity,
        node_identity=node_identity,
        channel=channel,
    )


def evaluate_occurrence_match(
    current_occ_id: str,
    slot_index: int,
    channel: int,
    material_identity: str,
    node_identity: str,
    source_kind: str,
    source_locator: str,
    colorspace: str,
    prior_occurrences: dict,
    assets: dict,
) -> ReuseDecision:
    """Evaluate a single current occurrence against prior manifest data."""
    occ = prior_occurrences.get(current_occ_id)
    if occ is None:
        return ReuseDecision(
            decision=_DECISION_PREPARE, action=_ACT_OCCURRENCE_MISSING,
            occurrence_id=current_occ_id, asset_id="", source_kind=source_kind,
            destination_path="", error="occurrence_missing",
        )

    # Check slotIndex
    if occ.get("slotIndex") != slot_index:
        return ReuseDecision(
            decision=_DECISION_PREPARE, action=_ACT_OCCURRENCE_IDENTITY_MISMATCH,
            occurrence_id=current_occ_id, asset_id="", source_kind=source_kind,
            destination_path="",
            error=f"slotIndex mismatch: {occ.get('slotIndex')} vs {slot_index}",
        )

    # Check channel
    if occ.get("channel") != channel:
        return ReuseDecision(
            decision=_DECISION_PREPARE, action=_ACT_OCCURRENCE_IDENTITY_MISMATCH,
            occurrence_id=current_occ_id, asset_id="", source_kind=source_kind,
            destination_path="",
            error=f"channel mismatch: {occ.get('channel')} vs {channel}",
        )

    # Check materialIdentity
    if occ.get("materialIdentity") != material_identity:
        return ReuseDecision(
            decision=_DECISION_PREPARE, action=_ACT_OCCURRENCE_IDENTITY_MISMATCH,
            occurrence_id=current_occ_id, asset_id="", source_kind=source_kind,
            destination_path="",
            error=f"materialIdentity mismatch: {occ.get('materialIdentity')!r} vs {material_identity!r}",
        )

    # Check nodeIdentity
    if occ.get("nodeIdentity") != node_identity:
        return ReuseDecision(
            decision=_DECISION_PREPARE, action=_ACT_OCCURRENCE_IDENTITY_MISMATCH,
            occurrence_id=current_occ_id, asset_id="", source_kind=source_kind,
            destination_path="",
            error=f"nodeIdentity mismatch: {occ.get('nodeIdentity')!r} vs {node_identity!r}",
        )

    # Check sourceKind
    if occ.get("sourceKind") != source_kind:
        return ReuseDecision(
            decision=_DECISION_PREPARE, action=_ACT_OCCURRENCE_IDENTITY_MISMATCH,
            occurrence_id=current_occ_id, asset_id="", source_kind=source_kind,
            destination_path="",
            error=f"sourceKind mismatch: {occ.get('sourceKind')!r} vs {source_kind!r}",
        )

    # Check colorspace
    if occ.get("colorspace") != colorspace:
        return ReuseDecision(
            decision=_DECISION_PREPARE, action=_ACT_OCCURRENCE_IDENTITY_MISMATCH,
            occurrence_id=current_occ_id, asset_id="", source_kind=source_kind,
            destination_path="",
            error=f"colorspace mismatch: {occ.get('colorspace')!r} vs {colorspace!r}",
        )

    # Check occurrence status
    prior_status = occ.get("status")
    if prior_status != "ready":
        return ReuseDecision(
            decision=_DECISION_PREPARE, action=_ACT_OCCURRENCE_STATUS_NOT_READY,
            occurrence_id=current_occ_id, asset_id="", source_kind=source_kind,
            destination_path="", error=f"occurrence status not ready: {prior_status!r}",
        )

    # Check assetId
    asset_id = occ.get("assetId")
    if not asset_id or not isinstance(asset_id, str):
        return ReuseDecision(
            decision=_DECISION_PREPARE, action=_ACT_ASSET_MISSING,
            occurrence_id=current_occ_id, asset_id="", source_kind=source_kind,
            destination_path="", error="no assetId in occurrence",
        )

    # Check asset exists
    asset = assets.get(asset_id)
    if asset is None:
        return ReuseDecision(
            decision=_DECISION_PREPARE, action=_ACT_ASSET_MISSING,
            occurrence_id=current_occ_id, asset_id=asset_id, source_kind=source_kind,
            destination_path="", error=f"asset {asset_id!r} not found",
        )

    # Check asset status
    if asset.get("status") != "ready":
        return ReuseDecision(
            decision=_DECISION_PREPARE, action=_ACT_ASSET_STATUS_NOT_READY,
            occurrence_id=current_occ_id, asset_id=asset_id, source_kind=source_kind,
            destination_path="", error=f"asset status not ready: {asset.get('status')!r}",
        )

    # Check asset key/contentHash consistency
    asset_content_hash = asset.get("contentHash", "")
    if asset_id != asset_content_hash:
        return ReuseDecision(
            decision=_DECISION_REJECT, action=_ACT_ASSET_IDENTITY_INCONSISTENT,
            occurrence_id=current_occ_id, asset_id=asset_id, source_kind=source_kind,
            destination_path="",
            error=f"asset key mismatch: key={asset_id!r} contentHash={asset_content_hash!r}",
        )

    # Occurrence valid and asset consistent — potential reuse
    return ReuseDecision(
        decision=_DECISION_REUSE, action=_ACT_REUSE_ALLOWED,
        occurrence_id=current_occ_id, asset_id=asset_id, source_kind=source_kind,
        destination_path="",
    )
# ──────────────────────────────────────────────────────────────────────
# Unique-asset reuse evaluation
# ──────────────────────────────────────────────────────────────────────

def evaluate_asset_reuse(
    current_content_hex: str,
    asset_id: str,
    asset: dict,
    sidecar_dir: str,
) -> ReuseDecision:
    """Evaluate a single unique asset for reuse.

    Checks:
        - asset key == contentHash
        - current content == destination content
        - destination exists, is regular file
        - destination size matches
        - destination hash matches
        - safe basename
        - contained path

    Returns ReuseDecision with decision="reuse" or "prepare".
    """
    # Asset key must equal contentHash
    asset_content_hash = asset.get("contentHash", "")
    if asset_id != asset_content_hash:
        return ReuseDecision(
            decision=_DECISION_REJECT, action=_ACT_ASSET_IDENTITY_INCONSISTENT,
            occurrence_id="", asset_id=asset_id, source_kind="",
            destination_path="",
            error=f"asset key != contentHash: key={asset_id!r} hash={asset_content_hash!r}",
        )

    # Current source content must match asset content hash
    if current_content_hex and current_content_hex != asset_content_hash:
        return ReuseDecision(
            decision=_DECISION_PREPARE, action=_ACT_SOURCE_IDENTITY_CHANGED,
            occurrence_id="", asset_id=asset_id, source_kind="",
            destination_path="",
            error=f"source content changed: current={current_content_hex} vs asset={asset_content_hash}",
        )

    # Check asset status
    if asset.get("status") != "ready":
        return ReuseDecision(
            decision=_DECISION_PREPARE, action=_ACT_ASSET_STATUS_NOT_READY,
            occurrence_id="", asset_id=asset_id, source_kind="",
            destination_path="", error=f"asset status not ready: {asset.get('status')!r}",
        )

    # Get destination info
    dest_basename = asset.get("destinationBasename", "")
    if not dest_basename or not is_safe_basename(dest_basename):
        return ReuseDecision(
            decision=_DECISION_REJECT, action=_ACT_DESTINATION_BASENAME_UNSAFE,
            occurrence_id="", asset_id=asset_id, source_kind="",
            destination_path="", error=f"unsafe basename: {dest_basename!r}",
        )

    dest_path = os.path.join(sidecar_dir, dest_basename)

    # Path containment
    safe, reason = validate_path_safety(sidecar_dir, dest_path)
    if not safe:
        if "escape" in reason:
            return ReuseDecision(
                decision=_DECISION_REJECT, action=_ACT_DESTINATION_SYMLINK_ESCAPE,
                occurrence_id="", asset_id=asset_id, source_kind="",
                destination_path="", error=f"path escape: {reason}",
            )
        return ReuseDecision(
            decision=_DECISION_PREPARE, action=_ACT_DESTINATION_MISSING,
            occurrence_id="", asset_id=asset_id, source_kind="",
            destination_path="", error=f"path safety failed: {reason}",
        )

    # Check destination exists
    if not os.path.exists(dest_path):
        return ReuseDecision(
            decision=_DECISION_PREPARE, action=_ACT_DESTINATION_MISSING,
            occurrence_id="", asset_id=asset_id, source_kind="",
            destination_path="", error="destination_file_missing",
        )

    # Check regular file
    st = os.stat(dest_path)
    if not stat.S_ISREG(st.st_mode):
        return ReuseDecision(
            decision=_DECISION_PREPARE, action=_ACT_DESTINATION_NOT_REGULAR,
            occurrence_id="", asset_id=asset_id, source_kind="",
            destination_path=dest_path, error="destination_not_regular",
        )

    # Check symlink
    if os.path.islink(dest_path):
        return ReuseDecision(
            decision=_DECISION_REJECT, action=_ACT_DESTINATION_SYMLINK_ESCAPE,
            occurrence_id="", asset_id=asset_id, source_kind="",
            destination_path=dest_path, error="destination_symlink_escape",
        )

    # Size check
    expected_size = asset.get("destinationSize", -1)
    if expected_size < 0:
        return ReuseDecision(
            decision=_DECISION_PREPARE, action=_ACT_DESTINATION_MISSING,
            occurrence_id="", asset_id=asset_id, source_kind="",
            destination_path=dest_path, error="missing destinationSize",
        )
    actual_size = st.st_size
    if actual_size != expected_size:
        return ReuseDecision(
            decision=_DECISION_PREPARE, action=_ACT_DESTINATION_SIZE_MISMATCH,
            occurrence_id="", asset_id=asset_id, source_kind="",
            destination_path=dest_path,
            error=f"size mismatch: expected={expected_size} actual={actual_size}",
        )

    # Hash check
    expected_hash = asset.get("destinationHash", "")
    if not expected_hash:
        return ReuseDecision(
            decision=_DECISION_PREPARE, action=_ACT_DESTINATION_MISSING,
            occurrence_id="", asset_id=asset_id, source_kind="",
            destination_path=dest_path, error="missing destinationHash",
        )
    actual_hash = compute_file_hash_hex(dest_path)
    if not actual_hash:
        return ReuseDecision(
            decision=_DECISION_PREPARE, action=_ACT_DESTINATION_HASH_MISMATCH,
            occurrence_id="", asset_id=asset_id, source_kind="",
            destination_path=dest_path, error="hash_computation_failed",
        )
    if actual_hash != expected_hash:
        return ReuseDecision(
            decision=_DECISION_PREPARE, action=_ACT_DESTINATION_HASH_MISMATCH,
            occurrence_id="", asset_id=asset_id, source_kind="",
            destination_path=dest_path,
            error=f"hash mismatch: expected={expected_hash} actual={actual_hash}",
        )

    # All checks passed — reuse allowed
    return ReuseDecision(
        decision=_DECISION_REUSE, action=_ACT_REUSE_ALLOWED,
        occurrence_id="", asset_id=asset_id, source_kind="",
        destination_path=dest_path,
    )
# ──────────────────────────────────────────────────────────────────────
# Normalized prepare output helper
# ──────────────────────────────────────────────────────────────────────

def _normalize_prepare_result(
    prepare_result,
    asset,
    current_assets,
    fallback_action,
) -> tuple:
    """Normalize prepare_fn output with safe defaults for None.

    Returns (decision, action, destination_path, asset_id, error).

    Mutates *current_assets* in-place when *asset* has a valid asset_id.

    Args:
        prepare_result: ReuseDecision or None from prepare_fn.
        asset: dict with optional "asset_id" key, or None.
        current_assets: dict of asset_id -> asset dict (mutated in-place).
        fallback_action: action string used when prepare_result is None.
    """
    # Extract asset_id from returned asset dict
    asset_id = ""
    if asset is not None:
        aid = asset.get("asset_id", "")
        if aid:
            current_assets[aid] = asset
            asset_id = aid

    if prepare_result is None:
        return (
            _DECISION_PREPARE,
            fallback_action,
            "",
            asset_id,
            "prepare_callback_returned_none",
        )

    # Valid ReuseDecision — preserve its fields
    return (
        prepare_result.decision,
        prepare_result.action,
        prepare_result.destination_path,
        asset_id,
        prepare_result.error,
    )

# ──────────────────────────────────────────────────────────────────────
# Object-level orchestration
# ──────────────────────────────────────────────────────────────────────

def evaluate_manifest_reuse(
    guid_hex: str,
    occurrence_descriptors: List[dict],
    sidecar_dir: str,
    manifest_path: str,
    collision_registry: dict,
    prepare_fn,
    guid_short: str = "?",
) -> ReuseOutcome:
    """Orchestrate manifest-informed reuse for one object.

    Stateless — no module-level mutable state.

    Args:
        guid_hex: Current object GUID.
        occurrence_descriptors: list of dicts with keys:
            slot_index, channel, material_identity, node_identity,
            source_kind, source_locator, colorspace, source, current_content_hex
        sidecar_dir: current object's sidecar directory.
        manifest_path: path to prior manifest_v3.json.
        collision_registry: shared collision registry.
        prepare_fn: callable(source_desc, sidecar_dir, collision_registry, guid_short)
                   -> (reuse_decision, asset_dict_or_None).
        guid_short: short GUID for logging.

    Returns:
        ReuseOutcome with structured results for every source.
    """
    # ── Step 1: Read prior manifest ──
    manifest_status, prior_data = read_prior_manifest(manifest_path, expected_guid=guid_hex)

    if manifest_status == "missing":
        return ReuseOutcome(
            manifest_status="missing",
            prior_manifest_eligible_for_generation=False,
            global_reuse_denied=True,
            decisions={},
            current_content_hexes={},
            current_assets={},
            prior_generation=0,
            error="manifest_missing",
        )

    # ── Step 2: Validate prior manifest schema ──
    if manifest_status == "invalid":
        return ReuseOutcome(
            manifest_status="invalid",
            prior_manifest_eligible_for_generation=False,
            global_reuse_denied=True,
            decisions={},
            current_content_hexes={},
            current_assets={},
            prior_generation=0,
            error="manifest_invalid",
        )

    # ── Step 3: Validate prior manifest structure ──
    is_valid, schema_error = validate_prior_manifest_schema(prior_data, guid_hex)
    if not is_valid:
        return ReuseOutcome(
            manifest_status="invalid",
            prior_manifest_eligible_for_generation=False,
            global_reuse_denied=True,
            decisions={},
            current_content_hexes={},
            current_assets={},
            prior_generation=0,
            error=f"schema_validation_failed:{schema_error}",
        )

    # Prior manifest is valid — eligible for later generation derivation
    prior_occurrences = prior_data.get("occurrences", {})
    prior_assets = prior_data.get("assets", {})
    prior_generation = prior_data.get("generation", 0)

    # ── Step 4: Compute current content identities and dedup sources ──
    # Group occurrence_descriptors by source identifier for dedup
    seen_sources: Dict[int, dict] = {}
    for occ_desc in occurrence_descriptors:
        src = occ_desc.get("source")
        if src is None:
            continue
        src_id = id(src)
        if src_id not in seen_sources:
            seen_sources[src_id] = occ_desc
        else:
            # Merge current_content_hex from the first occurrence of this source
            existing = seen_sources[src_id]
            if existing.get("current_content_hex") is None and occ_desc.get("current_content_hex") is not None:
                existing["current_content_hex"] = occ_desc["current_content_hex"]

    # ── Step 5: Evaluate per-source ──
    decisions: Dict[int, ReuseDecision] = {}
    current_content_hexes: Dict[int, str] = {}
    current_assets: Dict[str, dict] = {}
    global_rejection = False
    global_rejection_reason = ""

    # Pre-populate all content hexes for all sources
    for src_id, first_occ in seen_sources.items():
        content_hex = first_occ.get("current_content_hex")
        current_content_hexes[src_id] = content_hex or ""

    # ── Two-level grouping for unique-content dedup ──
    # Level 1: asset-work key = (source_kind, current_content_hex) for valid content
    # Level 2: source_id -> first_occ (preserved for decision keys)
    asset_work_groups: Dict[tuple, List[int]] = {}  # key -> [src_id, ...]
    for src_id, first_occ in seen_sources.items():
        content_hex = current_content_hexes[src_id]
        source_kind = first_occ["source_kind"]
        # Only group by content hash when content identity is valid
        if content_hex is not None and content_hex != "":
            work_key = (source_kind, content_hex)
        else:
            # Missing/invalid content → keep source-specific (no merge)
            work_key = None
        if work_key is not None:
            if work_key not in asset_work_groups:
                asset_work_groups[work_key] = []
            asset_work_groups[work_key].append(src_id)

    # ═══════════════════════════════════════════════════════════
    # Phase 1 — Evaluate every source independently (no fan-out)
    # ═══════════════════════════════════════════════════════════
    # For each work group, collect per-source intermediate results
    # BEFORE any decision is fanned or prepare_fn is called.
    #
    # Per-source intermediate result: (occurrence_eligible, source_kind,
    #     first_occ_result_or_None, global_rejection_flag, rejection_reason)
    #
    # occurrence_eligible == True  → all occurrences returned REUSE
    # occurrence_eligible == False → at least one returned PREPARE (not REJECT)
    # global_rejection == True     → a REJECT was found in this group

    # Intermediate evaluation results keyed by src_id
    eval_results: Dict[int, dict] = {}

    for src_id, first_occ in seen_sources.items():
        content_hex = current_content_hexes[src_id]
        source_kind = first_occ["source_kind"]

        if content_hex is None:
            # Cannot compute content identity — immediate prepare
            decisions[src_id] = ReuseDecision(
                decision=_DECISION_PREPARE, action=_ACT_PREPARE_REQUIRED,
                occurrence_id="", asset_id="", source_kind=source_kind,
                destination_path="", error="content_identity_unavailable",
            )
            eval_results[src_id] = None
            continue

        # ── Evaluate each occurrence belonging to this source ──
        all_occ_reuse = True
        first_global_reject = False
        first_reject_reason = ""
        for occ_desc in occurrence_descriptors:
            occ_source = occ_desc.get("source")
            if occ_source is not None and id(occ_source) == src_id:
                occ_result = evaluate_occurrence_match(
                    current_occ_id=occ_desc.get("occurrence_id", ""),
                    slot_index=occ_desc["slot_index"],
                    channel=occ_desc["channel"],
                    material_identity=occ_desc["material_identity"],
                    node_identity=occ_desc["node_identity"],
                    source_kind=occ_desc["source_kind"],
                    source_locator=occ_desc.get("source_locator", ""),
                    colorspace=occ_desc["colorspace"],
                    prior_occurrences=prior_occurrences,
                    assets=prior_assets,
                )
                if occ_result.decision == _DECISION_REJECT:
                    if not first_global_reject:
                        first_global_reject = True
                        first_reject_reason = occ_result.error
                    all_occ_reuse = False
                    break
                if occ_result.decision != _DECISION_REUSE:
                    all_occ_reuse = False

        # Evaluate the first valid occurrence's asset for potential reuse
        first_occ_result = None
        if all_occ_reuse:
            first_occ_result = evaluate_occurrence_match(
                current_occ_id=first_occ.get("occurrence_id", ""),
                slot_index=first_occ["slot_index"],
                channel=first_occ["channel"],
                material_identity=first_occ["material_identity"],
                node_identity=first_occ["node_identity"],
                source_kind=first_occ["source_kind"],
                source_locator=first_occ.get("source_locator", ""),
                colorspace=first_occ["colorspace"],
                prior_occurrences=prior_occurrences,
                assets=prior_assets,
            )

        eval_results[src_id] = {
            "all_occ_reuse": all_occ_reuse,
            "global_reject": first_global_reject,
            "reject_reason": first_reject_reason,
            "source_kind": source_kind,
            "content_hex": content_hex,
            "first_occ_result": first_occ_result,
        }

    # ═══════════════════════════════════════════════════════════
    # Phase 2 — Group-level decision (one action per work group)
    # ═══════════════════════════════════════════════════════════

    # First: check for global rejection across all groups
    for src_id, result in eval_results.items():
        if result is not None and result["global_reject"]:
            global_rejection = True
            global_rejection_reason = result["reject_reason"]
            break

    if global_rejection:
        for remaining_src_id, first_occ in seen_sources.items():
            if remaining_src_id not in decisions:
                decisions[remaining_src_id] = ReuseDecision(
                    decision=_DECISION_REJECT,
                    action=_ACT_ASSET_IDENTITY_INCONSISTENT,
                    occurrence_id="", asset_id="", source_kind="",
                    destination_path="", error=global_rejection_reason,
                )
        # Skip group processing
        pass
    else:
        # Process each work group
        processed_work_keys: set = set()
        for src_id, first_occ in seen_sources.items():
            if src_id in decisions:
                continue

            result = eval_results.get(src_id)
            if result is None:
                # Already decided (content_identity_unavailable)
                continue

            content_hex = result["content_hex"]
            source_kind = result["source_kind"]

            # Determine work_key for this source
            work_key = None
            if content_hex is not None and content_hex != "":
                work_key = (source_kind, content_hex)

            # If work_key was None (no valid content identity), decide individually
            if work_key is None:
                decisions[src_id] = ReuseDecision(
                    decision=_DECISION_PREPARE, action=_ACT_PREPARE_REQUIRED,
                    occurrence_id="", asset_id="", source_kind=source_kind,
                    destination_path="", error="content_identity_unavailable",
                )
                continue

            # Skip if this work group was already processed
            if work_key in processed_work_keys:
                continue

            # Gather all sources in this work group
            group_src_ids = asset_work_groups[work_key]

            # Collect per-source evaluation outcomes for this group
            group_all_reuse = True
            group_first_occ_result = None
            group_source_kind = None
            group_content_hex = None
            for gid in group_src_ids:
                gr = eval_results.get(gid)
                if gr is None:
                    continue
                if not gr["all_occ_reuse"]:
                    group_all_reuse = False
                if group_first_occ_result is None and gr["first_occ_result"] is not None:
                    group_first_occ_result = gr["first_occ_result"]
                if group_source_kind is None:
                    group_source_kind = gr["source_kind"]
                if group_content_hex is None:
                    group_content_hex = gr["content_hex"]

            # Check if any source in group has mismatched occurrence but same prior asset
            # If group_all_reuse is False, we need to prepare
            if not group_all_reuse:
                # Need to prepare — call prepare_fn once for the group
                # Use the first source with valid content for prepare_fn
                prepare_src_id = None
                for gid in group_src_ids:
                    gr = eval_results.get(gid)
                    if gr is not None and gr["content_hex"] is not None:
                        prepare_src_id = gid
                        break
                if prepare_src_id is None:
                    prepare_src_id = group_src_ids[0]

                prepare_src_first_occ = seen_sources[prepare_src_id]
                prepare_result, asset = prepare_fn(
                    prepare_src_first_occ, sidecar_dir, collision_registry, guid_short,
                )

                prep_dec, prep_act, prep_dest, prep_aid, prep_err = _normalize_prepare_result(
                    prepare_result, asset, current_assets, _ACT_PREPARE_REQUIRED,
                )

                # Fan the prepare result to all sources in the group
                # Each source gets its own occurrence_id
                for gid in group_src_ids:
                    if gid in decisions:
                        continue
                    gr = eval_results.get(gid)
                    if gr is None or gr["first_occ_result"] is None:
                        # No valid occurrence match — use prepare decision with generic occurrence_id
                        decisions[gid] = ReuseDecision(
                            decision=prep_dec,
                            action=prep_act,
                            occurrence_id="", asset_id=prep_aid,
                            source_kind=gr["source_kind"] if gr else source_kind,
                            destination_path=prep_dest,
                            error=prep_err,
                        )
                    else:
                        # Has a valid occurrence — use that occurrence_id
                        decisions[gid] = ReuseDecision(
                            decision=prep_dec,
                            action=prep_act,
                            occurrence_id=gr["first_occ_result"].occurrence_id,
                            asset_id=prep_aid,
                            source_kind=gr["source_kind"],
                            destination_path=prep_dest,
                            error=prep_err,
                        )

                processed_work_keys.add(work_key)
                continue

            # ── All sources in group are occurrence-eligible ──
            # Check if all sources point to the same prior asset ID
            group_asset_ids: set = set()
            for gid in group_src_ids:
                gr = eval_results.get(gid)
                if gr is not None and gr["first_occ_result"] is not None:
                    aid = gr["first_occ_result"].asset_id
                    if aid:
                        group_asset_ids.add(aid)

            if group_first_occ_result is None:
                # No valid occurrence found — prepare
                prepared_asset_id = ""
                prepare_src_id = group_src_ids[0]
                prepare_src_first_occ = seen_sources[prepare_src_id]
                prepare_result, asset = prepare_fn(
                    prepare_src_first_occ, sidecar_dir, collision_registry, guid_short,
                )
                if asset is not None:
                    aid = asset.get("asset_id", "")
                    if aid:
                        current_assets[aid] = asset
                        prepared_asset_id = aid
                for gid in group_src_ids:
                    if gid not in decisions:
                        decisions[gid] = ReuseDecision(
                            decision=_DECISION_PREPARE,
                            action=_ACT_ASSET_MISSING,
                            occurrence_id="", asset_id=prepared_asset_id,
                            source_kind=source_kind,
                            destination_path="", error="no assetId",
                        )
                processed_work_keys.add(work_key)
                continue

            # All sources point to the same prior asset?
            if len(group_asset_ids) == 1:
                prior_asset_id = group_asset_ids.pop()
                prior_asset = prior_assets.get(prior_asset_id)
                if prior_asset is None:
                    prepare_src_id = group_src_ids[0]
                    prepare_src_first_occ = seen_sources[prepare_src_id]
                    prepare_result, asset = prepare_fn(
                        prepare_src_first_occ, sidecar_dir, collision_registry, guid_short,
                    )
                    if asset is not None:
                        aid = asset.get("asset_id", "")
                        if aid:
                            current_assets[aid] = asset
                    for gid in group_src_ids:
                        if gid not in decisions:
                            decisions[gid] = ReuseDecision(
                                decision=_DECISION_PREPARE,
                                action=_ACT_ASSET_MISSING,
                                occurrence_id=group_first_occ_result.occurrence_id,
                                asset_id=prior_asset_id,
                                source_kind=source_kind,
                                destination_path="", error="asset not found",
                            )
                    processed_work_keys.add(work_key)
                    continue

                # Evaluate unique asset reuse (once for the group)
                # Use the first source's content_hex
                first_src_content_hex = None
                for gid in group_src_ids:
                    gr = eval_results.get(gid)
                    if gr and gr["content_hex"] is not None:
                        first_src_content_hex = gr["content_hex"]
                        break

                asset_result = evaluate_asset_reuse(
                    current_content_hex=first_src_content_hex or "",
                    asset_id=prior_asset_id,
                    asset=prior_asset,
                    sidecar_dir=sidecar_dir,
                )

                if asset_result.decision == _DECISION_REUSE:
                    current_assets[prior_asset_id] = prior_asset
                    # Fan reuse decision to all sources with their own occurrence_id
                    for gid in group_src_ids:
                        if gid in decisions:
                            continue
                        gr = eval_results.get(gid)
                        occ_result = None
                        for od in occurrence_descriptors:
                            od_src = od.get("source")
                            if od_src is not None and id(od_src) == gid:
                                occ_result = evaluate_occurrence_match(
                                    current_occ_id=od.get("occurrence_id", ""),
                                    slot_index=od["slot_index"],
                                    channel=od["channel"],
                                    material_identity=od["material_identity"],
                                    node_identity=od["node_identity"],
                                    source_kind=od["source_kind"],
                                    source_locator=od.get("source_locator", ""),
                                    colorspace=od["colorspace"],
                                    prior_occurrences=prior_occurrences,
                                    assets=prior_assets,
                                )
                                break
                        decisions[gid] = ReuseDecision(
                            decision=_DECISION_REUSE,
                            action=_ACT_REUSE_ALLOWED,
                            occurrence_id=occ_result.occurrence_id if occ_result else "",
                            asset_id=prior_asset_id,
                            source_kind=gr["source_kind"] if gr else source_kind,
                            destination_path=asset_result.destination_path,
                        )
                    processed_work_keys.add(work_key)
                    continue
                else:
                    # Asset reuse failed — prepare once for the group
                    prepare_src_id = group_src_ids[0]
                    prepare_src_first_occ = seen_sources[prepare_src_id]
                    prepare_result, new_asset = prepare_fn(
                        prepare_src_first_occ, sidecar_dir, collision_registry, guid_short,
                    )
                    prep_dec, prep_act, prep_dest, prep_aid, prep_err = _normalize_prepare_result(
                        prepare_result, new_asset, current_assets, _ACT_PREPARE_REQUIRED,
                    )
                    for gid in group_src_ids:
                        if gid not in decisions:
                            decisions[gid] = ReuseDecision(
                                decision=prep_dec,
                                action=prep_act,
                                occurrence_id=group_first_occ_result.occurrence_id,
                                asset_id=prep_aid,
                                source_kind=source_kind,
                                destination_path=prep_dest,
                                error=prep_err,
                            )
                    processed_work_keys.add(work_key)
                    continue
            else:
                # Different sources point to different prior assets — prepare for shared content
                prepared_asset_id = ""
                prepare_src_id = group_src_ids[0]
                prepare_src_first_occ = seen_sources[prepare_src_id]
                prepare_result, asset = prepare_fn(
                    prepare_src_first_occ, sidecar_dir, collision_registry, guid_short,
                )
                if asset is not None:
                    aid = asset.get("asset_id", "")
                    if aid:
                        current_assets[aid] = asset
                        prepared_asset_id = aid
                for gid in group_src_ids:
                    if gid not in decisions:
                        decisions[gid] = ReuseDecision(
                            decision=_DECISION_PREPARE,
                            action=_ACT_ASSET_IDENTITY_INCONSISTENT,
                            occurrence_id="", asset_id=prepared_asset_id,
                            source_kind=source_kind,
                            destination_path="", error="different_prior_assets",
                        )
                processed_work_keys.add(work_key)

    # ── Step 6: Fill any sources not yet decided (should not happen normally) ──
    for remaining_src_id, first_occ in seen_sources.items():
        if remaining_src_id not in decisions:
            decisions[remaining_src_id] = ReuseDecision(
                decision=_DECISION_PREPARE, action=_ACT_PREPARE_REQUIRED,
                occurrence_id="", asset_id="", source_kind=first_occ["source_kind"],
                destination_path="", error="no_occurrence_match",
            )

    return ReuseOutcome(
        manifest_status="valid",
        prior_manifest_eligible_for_generation=True,
        global_reuse_denied=global_rejection,
        decisions=decisions,
        current_content_hexes=current_content_hexes,
        current_assets=current_assets,
        prior_generation=prior_generation,
    )
