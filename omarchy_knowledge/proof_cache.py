"""Private inert cache for one complete canonical Git-object proof."""
import os
import secrets

from .object_bundle import MAX_COMPRESSED_BUNDLE
from .snapshots import _open_directory, _read_regular_at, _write_regular_at


FILENAME = "canonical-proof.bundle"
MAX_PROOF_BYTES = MAX_COMPRESSED_BUNDLE


def load(cache):
    """Return bounded regular sidecar bytes, or ``None`` for every cache miss."""
    descriptor = None
    try:
        descriptor = _open_directory(cache, "Canonical proof cache")
        return _read_regular_at(descriptor, FILENAME, MAX_PROOF_BYTES)
    except (OSError, ValueError):
        return None
    finally:
        if descriptor is not None:
            os.close(descriptor)


def store(cache, proof):
    """Atomically replace the sidecar after a successful CURRENT update."""
    if not isinstance(proof, bytes) or not 0 < len(proof) <= MAX_PROOF_BYTES:
        raise ValueError("Canonical proof exceeds size bound")
    descriptor = _open_directory(cache, "Canonical proof cache")
    temporary = f".{FILENAME}-{secrets.token_hex(16)}"
    try:
        try:
            _write_regular_at(descriptor, temporary, proof)
            os.replace(temporary, FILENAME, src_dir_fd=descriptor, dst_dir_fd=descriptor)
            os.fsync(descriptor)
        finally:
            try:
                os.unlink(temporary, dir_fd=descriptor)
            except FileNotFoundError:
                pass
    finally:
        os.close(descriptor)
