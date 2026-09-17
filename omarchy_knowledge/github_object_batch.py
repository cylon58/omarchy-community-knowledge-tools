"""Strict codec for fixed-repository, authenticated GitHub object batches."""
from dataclasses import dataclass
import base64
import hashlib
import json
import re

from .git_objects import TreeEntry


_DEPLOYMENTS = {
    "production": ("cylon58", "omarchy-community-knowledge", 1373429914),
    "pilot": ("cylon58", "omarchy-community-knowledge-pilot", 1373467908),
}
_LIMITS = {"tree": 16, "blob": 32}
_MODES = {
    0o40000: ("040000", "tree"),
    0o100644: ("100644", "blob"),
    0o100755: ("100755", "blob"),
    0o120000: ("120000", "blob"),
    0o160000: ("160000", "commit"),
}
_OID = re.compile(r"[0-9a-f]{40}\Z")


@dataclass(frozen=True)
class DecodedObject:
    raw: bytes
    entries: tuple[TreeEntry, ...] | None


def _require(condition):
    if not condition:
        raise ValueError("invalid object batch")


def _hash(kind, raw):
    return hashlib.sha1(
        kind.encode() + b" " + str(len(raw)).encode() + b"\0" + raw
    ).hexdigest()


def build_request(deployment, kind, oids):
    """Build the sole supported read query; callers supply only exact object IDs."""
    _require(deployment in _DEPLOYMENTS and kind in _LIMITS)
    _require(isinstance(oids, (list, tuple)) and 1 <= len(oids) <= _LIMITS[kind])
    _require(len(set(oids)) == len(oids) and all(
        isinstance(oid, str) and _OID.fullmatch(oid) for oid in oids
    ))
    owner, name, _repository_id = _DEPLOYMENTS[deployment]
    declarations = ",".join(f"$o{index}:GitObjectID!" for index in range(len(oids)))
    if kind == "tree":
        selection = "...on Tree{entries{nameRaw mode type oid size}}"
    else:
        selection = "...on Blob{byteSize isBinary isTruncated text}"
    objects = "".join(
        f"o{index}:object(oid:$o{index}){{__typename oid {selection}}}"
        for index in range(len(oids))
    )
    query = (
        f'query({declarations}){{repository(owner:"{owner}",name:"{name}")'
        f"{{databaseId {objects}}}rateLimit{{cost remaining}}}}"
    )
    variables = {f"o{index}": oid for index, oid in enumerate(oids)}
    return json.dumps(
        {"query": query, "variables": variables}, separators=(",", ":")
    ).encode()


def _decode_tree(value, requested):
    _require(isinstance(value, dict) and set(value) == {"__typename", "oid", "entries"})
    _require(value["__typename"] == "Tree" and value["oid"] == requested)
    rows = value["entries"]
    _require(isinstance(rows, list) and len(rows) <= 4096)
    names = set()
    encoded = []
    entries = []
    for item in rows:
        _require(isinstance(item, dict) and set(item) == {"nameRaw", "mode", "type", "oid", "size"})
        _require(isinstance(item["nameRaw"], str) and len(item["nameRaw"]) <= 688)
        try:
            name = base64.b64decode(item["nameRaw"], validate=True)
        except (ValueError, TypeError):
            raise ValueError("invalid object batch") from None
        _require(0 < len(name) <= 512 and name not in {b".", b".."}
                 and b"/" not in name and b"\0" not in name and name not in names)
        names.add(name)
        mode = item["mode"]
        _require(type(mode) is int and mode in _MODES)
        normalized, expected_kind = _MODES[mode]
        _require(item["type"] == expected_kind)
        child = item["oid"]
        _require(isinstance(child, str) and _OID.fullmatch(child) is not None)
        size = item["size"]
        if expected_kind == "blob":
            _require(type(size) is int and 0 <= size <= 65536)
            entry_size = size
        else:
            _require(size is None)
            entry_size = -1
        entries.append(TreeEntry(name, normalized, expected_kind, child, entry_size))
        encoded.append((
            name + (b"/" if expected_kind == "tree" else b""),
            format(mode, "o").encode() + b" " + name + b"\0" + bytes.fromhex(child),
        ))
    raw = b"".join(row for _key, row in sorted(encoded))
    _require(len(raw) <= 1024 * 1024 and _hash("tree", raw) == requested)
    return DecodedObject(raw, tuple(entries))


def _decode_blob(value, requested):
    _require(isinstance(value, dict) and set(value) == {
        "__typename", "oid", "byteSize", "isBinary", "isTruncated", "text",
    })
    _require(value["__typename"] == "Blob" and value["oid"] == requested
             and value["isBinary"] is False and value["isTruncated"] is False
             and isinstance(value["text"], str))
    try:
        raw = value["text"].encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError("invalid object batch") from None
    size = value["byteSize"]
    _require(type(size) is int and 0 <= size <= 65536 and len(raw) == size
             and _hash("blob", raw) == requested)
    return DecodedObject(raw, None)


def decode_response(deployment, kind, oids, value):
    """Validate a complete response before returning any reconstructed object."""
    _require(deployment in _DEPLOYMENTS and kind in _LIMITS)
    _require(isinstance(oids, (list, tuple)) and 1 <= len(oids) <= _LIMITS[kind]
             and len(set(oids)) == len(oids)
             and all(isinstance(oid, str) and _OID.fullmatch(oid) for oid in oids))
    _require(isinstance(value, dict) and set(value) == {"data"})
    data = value["data"]
    _require(isinstance(data, dict) and set(data) == {"repository", "rateLimit"})
    rate = data["rateLimit"]
    _require(isinstance(rate, dict) and set(rate) == {"cost", "remaining"})
    cost, remaining = rate["cost"], rate["remaining"]
    _require(type(cost) is int and 1 <= cost <= 192
             and type(remaining) is int and remaining >= 0)
    repository = data["repository"]
    _require(isinstance(repository, dict))
    aliases = {f"o{index}" for index in range(len(oids))}
    _require(set(repository) == {"databaseId", *aliases}
             and repository["databaseId"] == _DEPLOYMENTS[deployment][2])
    decoder = _decode_tree if kind == "tree" else _decode_blob
    decoded = {}
    for index, oid in enumerate(oids):
        decoded[oid] = decoder(repository[f"o{index}"], oid)
    return decoded, cost, remaining
