"""Private, disposable local derivations; never a provenance authority.

Only authenticated locally created byte images are deserialized. Source bytes
are independently checked on every read by snapshots. A separate private key
seals the image and its source/code binding; it confers no canonical trust.
"""
from __future__ import annotations

import hashlib
import hmac
import os
from pathlib import Path
import secrets
import sqlite3
import stat
import sys
from importlib.metadata import PackageNotFoundError, version

from .snapshots import MAX_SNAPSHOT_BYTES, _read_regular_at, _write_regular_at

_MAGIC = b'OMARCHY-LOCAL-INDEX-1\n'
_IMAGE = '.derived-index'
_KEY = '.derived-key'


def supported():
    return all(hasattr(sqlite3.Connection, name) for name in ('serialize', 'deserialize'))


def fingerprint():
    """Content binding, not timestamps or a manually maintained version string."""
    import knowledge
    root = Path(__file__).parent
    paths = [Path(knowledge.__file__), root / 'snapshots.py', root / 'retrieval.py',
             root / 'persistent.py', root / 'discovery.py']
    paths.extend(knowledge.SCHEMA_DIRECTORY / (name + '.schema.json')
                 for name in ('common', *knowledge.RECORD_TYPES))
    try:
        versions = (sqlite3.sqlite_version, sys.version, version('jsonschema'), version('referencing'))
    except PackageNotFoundError as exc:
        raise ValueError('Cannot bind local derivation to dependency versions') from exc
    digest = hashlib.sha256(b'local-derivation-v1\0' + repr(versions).encode())
    for path in paths:
        digest.update(path.name.encode() + b'\0' + path.read_bytes())
    return digest.digest()


def _key(directory_fd, *, create=False):
    if create:
        try:
            _write_regular_at(directory_fd, _KEY, secrets.token_bytes(32))
        except FileExistsError:
            pass
    # Read metadata and bytes through the same no-follow descriptor. Hardlinked,
    # non-private or foreign-owned key files are not accepted or overwritten.
    fd = os.open(_KEY, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600
                or info.st_nlink != 1 or info.st_uid != os.getuid()):
            raise ValueError('Local derivation key must be private')
        key = stream.read(33)
    if len(key) != 32:
        raise ValueError('Invalid local derivation key')
    return key


def read(directory_fd, binding):
    if not supported():
        return None
    try:
        key = _key(directory_fd)
        raw = _read_regular_at(directory_fd, _IMAGE, MAX_SNAPSHOT_BYTES)
        prefix = _MAGIC + binding
        if not raw.startswith(prefix):
            return None
        mac, image = raw[len(prefix):len(prefix) + 32], raw[len(prefix) + 32:]
        if not hmac.compare_digest(mac, hmac.digest(key, prefix + image, 'sha256')):
            return None
        # Verify that an interrupted or incompatible serialization is never used.
        db = connect(image)
        try:
            if db.execute('PRAGMA quick_check').fetchone() != ('ok',):
                return None
            if db.execute('SELECT value FROM local_meta WHERE name=?', ('binding',)).fetchone() != (binding.hex(),):
                return None
        finally:
            db.close()
        return image
    except (OSError, ValueError, sqlite3.Error, AttributeError):
        return None


def connect(image):
    db = sqlite3.connect(':memory:')
    try:
        db.deserialize(image)
        db.execute('PRAGMA query_only=ON')
        return db
    except BaseException:
        db.close()
        raise


def write(directory_fd, binding, records):
    """Atomic single-image publication; unavailable storage is only a cache miss."""
    if not supported():
        return None
    temporary = '.derived-' + secrets.token_hex(16)
    db = None
    try:
        key = _key(directory_fd, create=True)
        from .retrieval import build_index
        db = sqlite3.connect(':memory:')
        db.execute('CREATE TABLE local_meta (name TEXT PRIMARY KEY, value TEXT)')
        db.execute('INSERT INTO local_meta VALUES (?,?)', ('binding', binding.hex()))
        # FTS5 may be unavailable: validation caching and substring search still
        # work. Ranked search retains its explicit FTS-unavailable error.
        try:
            build_index(db, records)
        except sqlite3.OperationalError:
            pass
        db.commit()
        image = db.serialize()
        prefix = _MAGIC + binding
        raw = prefix + hmac.digest(key, prefix + image, 'sha256') + image
        if len(raw) > MAX_SNAPSHOT_BYTES:
            return None
        _write_regular_at(directory_fd, temporary, raw)
        os.replace(temporary, _IMAGE, src_dir_fd=directory_fd, dst_dir_fd=directory_fd)
        os.fsync(directory_fd)
        return image
    except (OSError, ValueError, sqlite3.Error, AttributeError):
        return None
    finally:
        if db is not None:
            db.close()
        try:
            os.unlink(temporary, dir_fd=directory_fd)
        except OSError:
            pass
