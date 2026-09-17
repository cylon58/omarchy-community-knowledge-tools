"""Deterministic bounded transport for independently verified raw Git objects."""
import gzip
import hashlib
import io
import struct
import zlib


MAGIC = b"OMARCHY-KNOWLEDGE-GIT-OBJECTS\x00\x01"
MAX_OBJECTS = 5000
MAX_RAW_OBJECTS = 20 * 1024 * 1024
MAX_SEED_OBJECTS = MAX_OBJECTS * 3 // 4
MAX_SEED_RAW_OBJECTS = MAX_RAW_OBJECTS * 3 // 4
MAX_BUNDLE = 24 * 1024 * 1024
MAX_COMPRESSED_BUNDLE = 16 * 1024 * 1024
KINDS = {"commit": b"C", "tree": b"T", "blob": b"B"}
BY_TAG = {value: key for key, value in KINDS.items()}
PER_KIND = {"commit": 65536, "tree": 1024 * 1024, "blob": 65536}


class BundleUnavailable(Exception):
    """The proof transport is stale, malformed, incomplete, or over a bound."""


def _require(condition):
    if not condition:
        raise BundleUnavailable()


def _hash(kind, raw):
    return hashlib.sha1(kind.encode() + b" " + str(len(raw)).encode() + b"\0" + raw).hexdigest()


def encode(head, objects):
    """Encode already verified raw objects; callers still reverify on import."""
    _require(isinstance(head, str) and len(head) == 40)
    try:
        bytes.fromhex(head)
    except ValueError as exc:
        raise BundleUnavailable() from exc
    rows = []
    for key, raw in objects.items():
        if not (isinstance(key, tuple) and len(key) == 2 and key[0] in KINDS
                and isinstance(key[1], str) and len(key[1]) == 40 and isinstance(raw, bytes)):
            continue  # APIObjects also caches parsed tree entries under a distinct key.
        kind, oid = key
        _require(len(raw) <= PER_KIND[kind] and _hash(kind, raw) == oid)
        rows.append((kind, oid, raw))
    rows.sort(key=lambda item: (item[0], item[1]))
    _require(1 <= len(rows) <= MAX_OBJECTS and sum(len(item[2]) for item in rows) <= MAX_RAW_OBJECTS)
    _require(any(kind == "commit" and oid == head for kind, oid, _ in rows))
    output = bytearray(MAGIC)
    output.extend(bytes.fromhex(head))
    output.extend(struct.pack(">I", len(rows)))
    for kind, oid, raw in rows:
        output.extend(KINDS[kind])
        output.extend(bytes.fromhex(oid))
        output.extend(struct.pack(">I", len(raw)))
        output.extend(raw)
        _require(len(output) <= MAX_BUNDLE)
    compressed_output = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", compresslevel=9,
                       fileobj=compressed_output, mtime=0) as stream:
        stream.write(output)
    compressed = compressed_output.getvalue()
    _require(len(compressed) <= MAX_COMPRESSED_BUNDLE)
    return compressed


def _inflate(value):
    _require(isinstance(value, bytes) and 1 <= len(value) <= MAX_COMPRESSED_BUNDLE)
    try:
        inflater = zlib.decompressobj(16 + zlib.MAX_WBITS)
        output = bytearray()
        for offset in range(0, len(value), 65536):
            remaining = MAX_BUNDLE + 1 - len(output)
            _require(remaining > 0)
            output.extend(inflater.decompress(value[offset:offset + 65536], remaining))
            _require(len(output) <= MAX_BUNDLE and not inflater.unconsumed_tail)
        remaining = MAX_BUNDLE + 1 - len(output)
        _require(remaining > 0)
        output.extend(inflater.flush(remaining))
        _require(inflater.eof and not inflater.unused_data and len(output) <= MAX_BUNDLE)
        return bytes(output)
    except zlib.error as exc:
        raise BundleUnavailable() from exc


def decode_seed(value):
    """Return the embedded head and verified objects without trusting that head."""
    raw = _inflate(value)
    header = len(MAGIC) + 20 + 4
    _require(len(raw) >= header and raw.startswith(MAGIC))
    offset = len(MAGIC)
    embedded_head = raw[offset:offset + 20].hex()
    offset += 20
    count = struct.unpack(">I", raw[offset:offset + 4])[0]
    offset += 4
    _require(1 <= count <= MAX_OBJECTS)
    objects, total = {}, 0
    for _ in range(count):
        _require(offset + 25 <= len(raw))
        tag = raw[offset:offset + 1]
        kind = BY_TAG.get(tag)
        _require(kind is not None)
        oid = raw[offset + 1:offset + 21].hex()
        size = struct.unpack(">I", raw[offset + 21:offset + 25])[0]
        offset += 25
        _require(size <= PER_KIND[kind] and offset + size <= len(raw))
        content = raw[offset:offset + size]
        offset += size
        total += size
        _require(total <= MAX_RAW_OBJECTS and _hash(kind, content) == oid)
        key = (kind, oid)
        _require(key not in objects)
        objects[key] = content
    _require(offset == len(raw) and ("commit", embedded_head) in objects)
    return embedded_head, objects


def decode(value, expected_head):
    """Return verified typed objects only when the bundle matches the API head."""
    _require(isinstance(expected_head, str) and len(expected_head) == 40)
    try:
        bytes.fromhex(expected_head)
    except ValueError as exc:
        raise BundleUnavailable() from exc
    embedded_head, objects = decode_seed(value)
    _require(embedded_head == expected_head)
    return objects
