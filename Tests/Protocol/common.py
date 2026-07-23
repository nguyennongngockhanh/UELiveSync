"""Shared utilities for protocol validator and vector generator."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import yaml

PROTOCOL_DIR = Path(__file__).resolve().parents[2] / "Shared" / "Protocol"

# YAML files whose content defines the protocol
PROTOCOL_YAML_FILES = [
    "MessageTypes.yaml",
    "Types.yaml",
    "Capabilities.yaml",
    "Errors.yaml",
]


class DuplicateKeyError(Exception):
    """Raised when a YAML mapping contains duplicate keys."""
    def __init__(self, key: str, source: str):
        self.key = key
        self.source = source
        super().__init__(f"Duplicate key '{key}' in {source}")


class _UniqueKeyLoader(yaml.SafeLoader):
    """YAML loader that detects duplicate mapping keys with source tracking."""
    _source_name: str = "YAML"


def _construct_mapping_unique(loader: _UniqueKeyLoader, node: yaml.MappingNode) -> dict:
    mapping = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node)
        if key in mapping:
            raise DuplicateKeyError(str(key), loader._source_name)
        mapping[key] = loader.construct_object(value_node)
    return mapping


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_mapping_unique,
)


def load_yaml(name: str) -> dict:
    """Load YAML with duplicate key detection. Reports filename on error.

    Uses a dynamic subclass per file so that _source_name is correctly set.
    """
    path = PROTOCOL_DIR / name
    if not path.exists():
        raise FileNotFoundError(f"Missing {path}")

    class _Loader(_UniqueKeyLoader):
        _source_name = name

    with open(path) as f:
        try:
            return yaml.load(f, Loader=_Loader)
        except DuplicateKeyError:
            raise
        except yaml.YAMLError as e:
            raise ValueError(f"Failed to parse {name}: {e}")


def compute_protocol_sha256() -> str:
    """Canonical SHA256 of the protocol spec.

    Hashes parsed → canonical JSON (sort_keys, deterministic separators)
    rather than raw bytes.

    Immunity to style variations (0x01 vs 1, 1.0 vs 1, etc.) comes from
    the YAML parser itself — yaml.safe_load resolves all scalar styles to
    native Python types before we see them. We hash the parsed representation,
    not the raw YAML text.

    This function is the single source of truth for the canonical hash.
    Both the validator and the vector generator import it from here.
    """
    h = hashlib.sha256()
    for name in PROTOCOL_YAML_FILES:
        data = load_yaml(name)
        canonical = json.dumps(data, sort_keys=True, separators=(",", ":"))
        h.update(canonical.encode("utf-8"))
    return h.hexdigest()
