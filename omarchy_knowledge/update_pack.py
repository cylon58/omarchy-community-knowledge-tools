"""Inert, bounded codec for one direct-predecessor canonical-object update.

This module only validates and transforms in-memory proof-object maps. It does not
fetch, publish, cache, authorize, or canonically interpret any object.
"""
from dataclasses import asdict, dataclass
import hashlib
import json
import re
import struct

from . import object_bundle
from .github_native import DEPLOYMENTS, NativeUnavailable, strict_json


OBJECT_SET_DOMAIN = b"OMARCHY-KNOWLEDGE-OBJECT-SET\0\1"
MAX_MANIFEST = 1024 * 1024
_MANIFEST_FIELDS = {
    "version", "deployment", "base_head", "target_head",
    "base_object_set_sha256", "target_object_set_sha256",
    "pack_sha256", "pack_size", "removed",
}
_OID = re.compile(r"[0-9a-f]{40}")
_SHA256 = re.compile(r"[0-9a-f]{64}")


class UpdatePackUnavailable(ValueError):
    """An update manifest, payload, or object map failed closed."""


def _require(condition):
    if not condition:
        raise UpdatePackUnavailable()


def _identity(value, pattern):
    _require(isinstance(value, str) and pattern.fullmatch(value) is not None)
    return value


def _deployment(value):
    _require(isinstance(value, str) and value in DEPLOYMENTS)
    return value


def _git_hash(kind, raw):
    return hashlib.sha1(
        kind.encode() + b" " + str(len(raw)).encode() + b"\0" + raw
    ).hexdigest()


def _object_rows(objects):
    """Validate a complete proof-object map and return its canonical rows."""
    _require(type(objects) is dict)
    _require(1 <= len(objects) <= object_bundle.MAX_OBJECTS)
    rows = []
    total = 0
    for key, raw in objects.items():
        _require(
            type(key) is tuple
            and len(key) == 2
            and isinstance(key[0], str)
            and key[0] in object_bundle.KINDS
            and isinstance(key[1], str)
            and _OID.fullmatch(key[1]) is not None
            and isinstance(raw, bytes)
        )
        kind, oid = key
        _require(len(raw) <= object_bundle.PER_KIND[kind])
        total += len(raw)
        _require(total <= object_bundle.MAX_RAW_OBJECTS)
        _require(_git_hash(kind, raw) == oid)
        rows.append((kind, oid, raw))
    rows.sort(key=lambda row: (row[0], row[1]))
    return rows


def object_set_sha256(objects):
    """Return the normalized digest of one bounded, hash-verified object map."""
    rows = _object_rows(objects)
    digest = hashlib.sha256()
    digest.update(OBJECT_SET_DOMAIN)
    digest.update(struct.pack(">I", len(rows)))
    for kind, oid, raw in rows:
        digest.update(object_bundle.KINDS[kind])
        digest.update(bytes.fromhex(oid))
        digest.update(struct.pack(">I", len(raw)))
        digest.update(raw)
    return digest.hexdigest()


@dataclass(frozen=True)
class Manifest:
    version: int
    deployment: str
    base_head: str
    target_head: str
    base_object_set_sha256: str
    target_object_set_sha256: str
    pack_sha256: str
    pack_size: int
    removed: tuple[tuple[str, str], ...]


def _validate_manifest(manifest):
    _require(type(manifest) is Manifest)
    _require(type(manifest.version) is int and manifest.version == 1)
    _deployment(manifest.deployment)
    _identity(manifest.base_head, _OID)
    _identity(manifest.target_head, _OID)
    _require(manifest.base_head != manifest.target_head)
    _identity(manifest.base_object_set_sha256, _SHA256)
    _identity(manifest.target_object_set_sha256, _SHA256)
    _identity(manifest.pack_sha256, _SHA256)
    _require(
        type(manifest.pack_size) is int
        and 1 <= manifest.pack_size <= object_bundle.MAX_COMPRESSED_BUNDLE
    )
    _require(
        type(manifest.removed) is tuple
        and len(manifest.removed) <= object_bundle.MAX_OBJECTS
    )
    previous = None
    for row in manifest.removed:
        _require(
            type(row) is tuple
            and len(row) == 2
            and isinstance(row[0], str)
            and row[0] in object_bundle.KINDS
            and isinstance(row[1], str)
            and _OID.fullmatch(row[1]) is not None
            and (previous is None or previous < row)
        )
        previous = row
    return manifest


def encode_manifest(manifest):
    """Encode one validated v1 manifest as canonical strict JSON."""
    manifest = _validate_manifest(manifest)
    value = asdict(manifest)
    value["removed"] = [list(row) for row in manifest.removed]
    try:
        raw = json.dumps(
            value, sort_keys=True, separators=(",", ":"), allow_nan=False,
        ).encode()
    except (TypeError, ValueError) as exc:
        raise UpdatePackUnavailable() from exc
    _require(1 <= len(raw) <= MAX_MANIFEST)
    return raw


def decode_manifest(raw, *, expected_deployment, expected_target_head):
    """Decode a v1 manifest bound to independently supplied deployment/head."""
    _require(
        isinstance(raw, bytes)
        and 1 <= len(raw) <= MAX_MANIFEST
    )
    _deployment(expected_deployment)
    _identity(expected_target_head, _OID)
    try:
        value = strict_json(raw)
    except (NativeUnavailable, TypeError, ValueError, RecursionError) as exc:
        raise UpdatePackUnavailable() from exc
    _require(type(value) is dict and set(value) == _MANIFEST_FIELDS)
    _require(type(value["removed"]) is list)
    removed = []
    for row in value["removed"]:
        _require(type(row) is list and len(row) == 2)
        removed.append(tuple(row))
    try:
        manifest = Manifest(
            version=value["version"],
            deployment=value["deployment"],
            base_head=value["base_head"],
            target_head=value["target_head"],
            base_object_set_sha256=value["base_object_set_sha256"],
            target_object_set_sha256=value["target_object_set_sha256"],
            pack_sha256=value["pack_sha256"],
            pack_size=value["pack_size"],
            removed=tuple(removed),
        )
        _validate_manifest(manifest)
    except (KeyError, TypeError) as exc:
        raise UpdatePackUnavailable() from exc
    _require(
        manifest.deployment == expected_deployment
        and manifest.target_head == expected_target_head
    )
    return manifest


def _require_head(objects, head):
    _identity(head, _OID)
    _require(("commit", head) in objects)


def _decode_pack(manifest, pack):
    _require(
        isinstance(pack, bytes)
        and len(pack) == manifest.pack_size
        and hashlib.sha256(pack).hexdigest() == manifest.pack_sha256
    )
    try:
        additions = object_bundle.decode(pack, manifest.target_head)
    except object_bundle.BundleUnavailable as exc:
        raise UpdatePackUnavailable() from exc
    _object_rows(additions)
    return additions


def generate(deployment, base_head, base_objects, target_head, target_objects):
    """Return canonical ``(manifest, pack)`` bytes, or ``None`` for fallback."""
    _deployment(deployment)
    _object_rows(base_objects)
    _object_rows(target_objects)
    _require_head(base_objects, base_head)
    _require_head(target_objects, target_head)
    for key in base_objects.keys() & target_objects.keys():
        _require(base_objects[key] == target_objects[key])
    if ("commit", target_head) in base_objects:
        return None
    additions = {
        key: raw for key, raw in target_objects.items() if key not in base_objects
    }
    removed = tuple(sorted(base_objects.keys() - target_objects.keys()))
    try:
        pack = object_bundle.encode(target_head, additions)
    except object_bundle.BundleUnavailable as exc:
        raise UpdatePackUnavailable() from exc
    manifest = Manifest(
        version=1,
        deployment=deployment,
        base_head=base_head,
        target_head=target_head,
        base_object_set_sha256=object_set_sha256(base_objects),
        target_object_set_sha256=object_set_sha256(target_objects),
        pack_sha256=hashlib.sha256(pack).hexdigest(),
        pack_size=len(pack),
        removed=removed,
    )
    return encode_manifest(manifest), pack


def apply(manifest_raw, pack, *, deployment, base_head, base_objects,
          expected_target_head):
    """Apply a pack to its exact verified base and return a complete target map."""
    manifest = decode_manifest(
        manifest_raw,
        expected_deployment=deployment,
        expected_target_head=expected_target_head,
    )
    _object_rows(base_objects)
    _require_head(base_objects, base_head)
    _require(
        base_head == manifest.base_head
        and object_set_sha256(base_objects) == manifest.base_object_set_sha256
    )
    additions = _decode_pack(manifest, pack)
    _require(not (additions.keys() & base_objects.keys()))
    _require(all(key in base_objects for key in manifest.removed))
    target = dict(base_objects)
    for key in manifest.removed:
        del target[key]
    target.update(additions)
    _require_head(target, manifest.target_head)
    _require(object_set_sha256(target) == manifest.target_object_set_sha256)
    return target


def validate_retained_target(manifest_raw, pack, *, deployment,
                             expected_target_head, target_objects):
    """Validate a retained pack against a complete current target without its base."""
    manifest = decode_manifest(
        manifest_raw,
        expected_deployment=deployment,
        expected_target_head=expected_target_head,
    )
    _object_rows(target_objects)
    _require_head(target_objects, expected_target_head)
    _require(
        object_set_sha256(target_objects) == manifest.target_object_set_sha256
    )
    additions = _decode_pack(manifest, pack)
    _require(all(target_objects.get(key) == raw for key, raw in additions.items()))
    _require(all(key not in target_objects for key in manifest.removed))
    return manifest
