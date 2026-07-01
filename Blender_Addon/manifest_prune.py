"""A3.6: Safe Orphan Sidecar Pruning.

After a manifest v3 is durably written and the FBX packet has been sent,
this module prunes orphan sidecar files from the object cache directory.

An orphan is a sidecar file whose destinationBasename appears in a valid
*prior* manifest ready asset record but does NOT appear in the *current*
manifest ready asset records.

Each deletion is guarded by strict TOCTOU-safe checks.

Pruning is authorized only when both prior and current manifest snapshots
are valid schema-v3 dicts and their GUIDs match.

This module must NOT import ``bpy``.  All Blender imports are lazy.
"""

from __future__ import annotations

import copy
import os
import stat
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

import manifest_v3
import network


# ================================================================
# Stable status constants
# ================================================================

STATUS_SUCCESS = "success"
STATUS_PARTIAL = "partial"
STATUS_SKIPPED = "skipped"

# ================================================================
# Stable item action constants
# ================================================================

ACTION_DELETED = "deleted"
ACTION_ALREADY_MISSING = "already_missing"
ACTION_RETAINED_CURRENT_REFERENCE = "retained_current_reference"
ACTION_SKIPPED_UNSAFE_BASENAME = "skipped_unsafe_basename"
ACTION_SKIPPED_PATH_ESCAPE = "skipped_path_escape"
ACTION_SKIPPED_SYMLINK = "skipped_symlink"
ACTION_SKIPPED_NOT_REGULAR = "skipped_not_regular"
ACTION_SKIPPED_IDENTITY_MISMATCH = "skipped_identity_mismatch"
ACTION_SKIPPED_CHANGED_BEFORE_DELETE = "skipped_changed_before_delete"
ACTION_UNLINK_FAILED = "unlink_failed"


# ================================================================
# Result dataclasses (immutable)
# ================================================================


@dataclass(frozen=True)
class PruneItemResult:
    """Result for one orphan candidate file.

    Fields:
        asset_id:   Prior manifest asset_id.
        filename:   Basename from prior asset record.
        path:       Full path under obj_dir.
        action:     Stable action constant (see ACTION_*).
        error:      Dynamic text for diagnostics (empty on success).
    """
    asset_id: str
    filename: str
    path: str
    action: str
    error: str = ""


@dataclass(frozen=True)
class PruneResult:
    """Overall result of an orphan-sidecar pruning pass.

    Fields:
        status:     STATUS_SUCCESS | STATUS_PARTIAL | STATUS_SKIPPED.
        items:      One PruneItemResult per prior ready asset considered.
        error:      Summary error when status is SKIPPED or PARTIAL.
    """
    status: str
    items: List[PruneItemResult] = field(default_factory=list)
    error: str = ""


@dataclass(frozen=True)
class TransactionSnapshot:
    """Validated prior and current manifest snapshots for pruning.

    Both snapshots are deep-copied at capture time and must be valid
    schema-v3 dicts with matching GUIDs.
    """
    prior_manifest: dict
    current_manifest: dict

    def prior_guid(self) -> str:
        return str(self.prior_manifest.get("guid", ""))

    def current_guid(self) -> str:
        return str(self.current_manifest.get("guid", ""))


# ================================================================
# Validation helpers
# ================================================================


def _validate_manifest_for_pruning(
    manifest: dict,
    label: str,
) -> Optional[str]:
    """Return an error string if *manifest* is not a valid schema-v3 dict.

    Uses ``manifest_v3.validate_manifest_v3_object``.
    Returns None when valid.
    """
    vr = manifest_v3.validate_manifest_v3_object(manifest)
    if not vr.valid:
        return f"{label}: {vr.error}"
    return None


# ================================================================
# Current-reference basename set
# ================================================================


def _collect_current_ready_basenames(current_manifest: dict) -> Set[str]:
    """Return the set of destinationBasename from every ready asset.

    An orphan is retained whenever its basename is present in this set,
    even if the asset_id or other record fields differ.
    """
    basenames: Set[str] = set()
    current_assets = current_manifest.get("assets")
    if not isinstance(current_assets, dict):
        return basenames
    for asset_id, asset_rec in current_assets.items():
        if not isinstance(asset_rec, dict):
            continue
        if asset_rec.get("status") != "ready":
            continue
        bn = asset_rec.get("destinationBasename")
        if isinstance(bn, str) and bn:
            basenames.add(bn)
    return basenames


# ================================================================
# Pure candidate pruning (no input validation assumptions)
# ================================================================


def _prune_candidates(
    prior_assets: Dict[str, dict],
    current_basenames: Set[str],
    obj_dir: str,
) -> PruneResult:
    """Pure deletion function operating on validated data.

    Args:
        prior_assets:     Prior manifest assets dict (ready records only).
        current_basenames: Basenames from current ready assets.
        obj_dir:          Authoritative object cache directory.

    Returns:
        PruneResult — never raises; never modifies input dicts.
    """
    items: List[PruneItemResult] = []
    had_partial = False

    for asset_id, asset_rec in prior_assets.items():
        basename = str(asset_rec["destinationBasename"])
        expected_size = int(asset_rec["destinationSize"])
        expected_hash = str(asset_rec["destinationHash"])
        full_path = os.path.join(obj_dir, basename)

        # ── Safe basename ──
        if not _safe_basename(basename):
            items.append(PruneItemResult(
                asset_id=asset_id, filename=basename, path=full_path,
                action=ACTION_SKIPPED_UNSAFE_BASENAME,
            ))
            continue

        # ── Path must resolve under obj_dir ──
        if not _contained_in(obj_dir, full_path):
            items.append(PruneItemResult(
                asset_id=asset_id, filename=basename, path=full_path,
                action=ACTION_SKIPPED_PATH_ESCAPE,
            ))
            continue

        # ── Retained by current reference? ──
        if basename in current_basenames:
            items.append(PruneItemResult(
                asset_id=asset_id, filename=basename, path=full_path,
                action=ACTION_RETAINED_CURRENT_REFERENCE,
            ))
            continue

        # ── lstat ──
        try:
            lst = os.lstat(full_path)
        except FileNotFoundError:
            items.append(PruneItemResult(
                asset_id=asset_id, filename=basename, path=full_path,
                action=ACTION_ALREADY_MISSING,
            ))
            continue
        except OSError as exc:
            items.append(PruneItemResult(
                asset_id=asset_id, filename=basename, path=full_path,
                action=ACTION_SKIPPED_NOT_REGULAR,
                error=str(exc),
            ))
            had_partial = True
            continue

        # ── Symlink rejection via lstat mode ──
        if stat.S_ISLNK(lst.st_mode):
            items.append(PruneItemResult(
                asset_id=asset_id, filename=basename, path=full_path,
                action=ACTION_SKIPPED_SYMLINK,
            ))
            continue

        # ── Require regular file ──
        if not stat.S_ISREG(lst.st_mode):
            items.append(PruneItemResult(
                asset_id=asset_id, filename=basename, path=full_path,
                action=ACTION_SKIPPED_NOT_REGULAR,
            ))
            continue

        # ── Size verification ──
        if lst.st_size != expected_size:
            items.append(PruneItemResult(
                asset_id=asset_id, filename=basename, path=full_path,
                action=ACTION_SKIPPED_IDENTITY_MISMATCH,
                error=f"size: expected={expected_size} actual={lst.st_size}",
            ))
            had_partial = True
            continue

        # ── Canonical xxHash64 verification ──
        actual_hash = network._xxh64_file_hex(full_path)
        if not actual_hash or actual_hash != expected_hash:
            items.append(PruneItemResult(
                asset_id=asset_id, filename=basename, path=full_path,
                action=ACTION_SKIPPED_IDENTITY_MISMATCH,
                error=f"hash: expected={expected_hash} actual={actual_hash or ''}",
            ))
            had_partial = True
            continue

        # ── Capture fingerprint before unlink ──
        fingerprint_before = {
            "st_dev": lst.st_dev,
            "st_ino": lst.st_ino,
            "st_size": lst.st_size,
            "st_mtime_ns": lst.st_mtime_ns,
        }

        # ── lstat again immediately before unlink ──
        try:
            lst_after = os.lstat(full_path)
        except OSError:
            items.append(PruneItemResult(
                asset_id=asset_id, filename=basename, path=full_path,
                action=ACTION_SKIPPED_CHANGED_BEFORE_DELETE,
                error="lstat_after_failed",
            ))
            had_partial = True
            continue

        fingerprint_after = {
            "st_dev": lst_after.st_dev,
            "st_ino": lst_after.st_ino,
            "st_size": lst_after.st_size,
            "st_mtime_ns": lst_after.st_mtime_ns,
        }

        if fingerprint_before != fingerprint_after:
            items.append(PruneItemResult(
                asset_id=asset_id, filename=basename, path=full_path,
                action=ACTION_SKIPPED_CHANGED_BEFORE_DELETE,
                error="fingerprint_changed",
            ))
            had_partial = True
            continue

        # ── Unlink ──
        try:
            os.unlink(full_path)
            items.append(PruneItemResult(
                asset_id=asset_id, filename=basename, path=full_path,
                action=ACTION_DELETED,
            ))
        except OSError as exc:
            items.append(PruneItemResult(
                asset_id=asset_id, filename=basename, path=full_path,
                action=ACTION_UNLINK_FAILED,
                error=str(exc),
            ))
            had_partial = True

    if had_partial:
        return PruneResult(status=STATUS_PARTIAL, items=items)
    return PruneResult(status=STATUS_SUCCESS, items=items)


# ================================================================
# Authorized entry point
# ================================================================


def prune_orphan_sidecars(
    prior_manifest: dict,
    current_manifest: dict,
    obj_dir: str,
) -> PruneResult:
    """Prune orphan sidecar files after a successful manifest write + send.

    Authorization gates:
        1. *prior_manifest* must be a valid schema-v3 dict.
        2. *current_manifest* must be a valid schema-v3 dict.
        3. GUIDs must match.
        4. Asset dicts must contain at least one ready record.

    Args:
        prior_manifest:   Snapshot of the manifest before the durable write.
        current_manifest: Snapshot of the manifest after the durable write.
        obj_dir:          Authoritative object cache directory.

    Returns:
        PruneResult — never raises.
    """
    # Gate 1: validate prior
    err = _validate_manifest_for_pruning(prior_manifest, "prior")
    if err is not None:
        return PruneResult(status=STATUS_SKIPPED, error=err)

    # Gate 2: validate current
    err = _validate_manifest_for_pruning(current_manifest, "current")
    if err is not None:
        return PruneResult(status=STATUS_SKIPPED, error=err)

    # Gate 3: GUID match
    prior_guid = str(prior_manifest.get("guid", ""))
    current_guid = str(current_manifest.get("guid", ""))
    if not prior_guid or prior_guid != current_guid:
        return PruneResult(
            status=STATUS_SKIPPED,
            error=f"GUID mismatch: prior={prior_guid} current={current_guid}",
        )

    # Extract prior ready assets
    prior_assets = prior_manifest.get("assets")
    if not isinstance(prior_assets, dict) or len(prior_assets) == 0:
        return PruneResult(status=STATUS_SKIPPED, error="no prior ready assets")

    # Filter to ready records with complete identity fields
    filtered_prior: Dict[str, dict] = {}
    for asset_id, asset_rec in prior_assets.items():
        if not isinstance(asset_rec, dict):
            continue
        if asset_rec.get("status") != "ready":
            continue
        bn = asset_rec.get("destinationBasename")
        sz = asset_rec.get("destinationSize")
        dh = asset_rec.get("destinationHash")
        if isinstance(bn, str) and bn and isinstance(sz, int) and sz > 0 and isinstance(dh, str) and dh:
            filtered_prior[asset_id] = {
                "destinationBasename": bn,
                "destinationSize": sz,
                "destinationHash": dh,
            }

    if len(filtered_prior) == 0:
        return PruneResult(status=STATUS_SKIPPED, error="no ready candidates in prior")

    # Collect current ready basenames
    current_basenames = _collect_current_ready_basenames(current_manifest)

    # Delegate to pure function
    return _prune_candidates(filtered_prior, current_basenames, obj_dir)


# ================================================================
# Authorization wrapper for __init__.py
# ================================================================


def prune_after_successful_send(
    manifest_result: manifest_v3.ManifestV3IntegrationResult,
    send_succeeded: bool,
    obj_dir: str,
) -> PruneResult:
    """Authorized pruning gate: only prune when manifest is durable and send succeeded.

    Args:
        manifest_result: Result from persist_manifest_v3 (contains prior/current snapshots).
        send_succeeded:  True if the FBX packet was transmitted successfully.
        obj_dir:         Authoritative object cache directory.

    Returns:
        PruneResult — STATUS_SKIPPED when gating rejects, otherwise delegate to
        prune_orphan_sidecars.
    """
    if manifest_result.status != "success" or manifest_result.action != "written":
        return PruneResult(
            status=STATUS_SKIPPED,
            error=f"manifest_not_durable: status={manifest_result.status} action={manifest_result.action}",
        )
    if not send_succeeded:
        return PruneResult(
            status=STATUS_SKIPPED,
            error="send_failed",
        )
    try:
        return prune_orphan_sidecars(
            prior_manifest=manifest_result.prior_manifest,
            current_manifest=manifest_result.current_manifest,
            obj_dir=obj_dir,
        )
    except Exception as exc:
        return PruneResult(
            status=STATUS_PARTIAL,
            items=[],
            error=f"prune_exception: {exc}",
        )


# ================================================================
# Helpers (shared with tests)
# ================================================================


def _safe_basename(basename: str) -> bool:
    if not basename:
        return False
    if basename == "manifest_v3.json":
        return False
    if basename.lower().endswith(".fbx"):
        return False
    if "/" in basename or "\\" in basename or basename in (".", ".."):
        return False
    if basename.startswith("."):
        return False
    return True


def _contained_in(obj_dir: str, full_path: str) -> bool:
    obj_dir_real = os.path.realpath(obj_dir)
    path_real = os.path.realpath(full_path)
    return path_real.startswith(obj_dir_real + os.sep) or path_real == obj_dir_real
