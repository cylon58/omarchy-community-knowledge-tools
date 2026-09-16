"""Inert official catalog observations, not exact-source PackageSnapshotV1 proof."""
from datetime import datetime, timedelta, timezone
import ctypes
import gzip
import hashlib
import http.client
import io
import multiprocessing
import re
import tarfile
import time

from .github_public import PublicReadUnavailable

CHANNELS = ('stable', 'rc')
ARCHITECTURES = ('x86_64',)
PACKAGES = ('omarchy', 'omarchy-settings')
MAX_COMPRESSED = 2 * 1024 * 1024
MAX_EXPANDED = 8 * 1024 * 1024
MAX_ENTRIES = 2048
DEADLINE_SECONDS = 15


def _scope(channel, architecture):
    if channel not in CHANNELS or architecture not in ARCHITECTURES:
        raise ValueError('Unsupported official catalog scope')
    return '/' + channel + '/' + architecture + '/omarchy.db'


def _expand(raw):
    if raw.startswith(b'\x28\xb5\x2f\xfd'):
        # Stable libzstd simple API: destination capacity is an enforced ceiling,
        # independent of attacker-controlled frame size. No shell/tool execution.
        lib = ctypes.CDLL('libzstd.so.1')
        lib.ZSTD_decompress.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p, ctypes.c_size_t]
        lib.ZSTD_decompress.restype = ctypes.c_size_t
        lib.ZSTD_isError.argtypes = [ctypes.c_size_t]
        lib.ZSTD_isError.restype = ctypes.c_uint
        destination = ctypes.create_string_buffer(MAX_EXPANDED)
        size = lib.ZSTD_decompress(destination, MAX_EXPANDED, raw, len(raw))
        if lib.ZSTD_isError(size) or size > MAX_EXPANDED:
            raise ValueError('Catalog zstd expansion bound or malformed data')
        return destination.raw[:size], 'zstd'
    with gzip.GzipFile(fileobj=io.BytesIO(raw)) as compressed:
        return compressed.read(MAX_EXPANDED + 1), 'gzip'


def parse_catalog(raw, channel, architecture, *, checked_at):
    """Bound decompression first, then inspect regular desc entries in memory.

    Built-at is historical metadata. Only retrieval establishes catalog freshness.
    Digests here are supplier catalog claims; no package signature is verified.
    """
    from .upstream import _timestamp
    endpoint = 'https://pkgs.omarchy.org' + _scope(channel, architecture)
    checked = _timestamp(checked_at)
    if not isinstance(raw, bytes) or len(raw) > MAX_COMPRESSED:
        raise ValueError('Catalog compressed bound')
    deadline = time.monotonic() + 3
    try:
        expanded, compression = _expand(raw)
        if len(expanded) > MAX_EXPANDED or time.monotonic() > deadline:
            raise ValueError('Catalog decompression bound')
        packages, paths = {}, set()
        with tarfile.open(fileobj=io.BytesIO(expanded), mode='r:') as archive:
            for index, entry in enumerate(archive):
                if index >= MAX_ENTRIES or time.monotonic() > deadline:
                    raise ValueError('Catalog entry/time bound')
                name = entry.name.rstrip('/')
                if (not re.fullmatch(r'[A-Za-z0-9@+_.:-]+(?:/(?:desc|depends|files))?', name)
                        or '..' in name or name in paths or entry.pax_headers):
                    raise ValueError('Catalog entry identity')
                paths.add(name)
                if entry.isdir() and '/' not in name:
                    continue
                if not entry.isfile() or not 0 <= entry.size <= 65536 or '/' not in name:
                    raise ValueError('Catalog entry type/size')
                if not name.endswith('/desc'):
                    continue
                data = archive.extractfile(entry).read(65537).decode('utf-8')
                fields = {}
                for section in data.strip().split('\n\n'):
                    lines = section.splitlines()
                    if not lines or not re.fullmatch(r'%[A-Z0-9]+%', lines[0]) or lines[0] in fields:
                        raise ValueError('Catalog duplicate/malformed field')
                    fields[lines[0]] = lines[1:]
                def scalar(key):
                    values = fields[key]
                    if len(values) != 1:
                        raise ValueError('Catalog scalar required')
                    return values[0]
                package = scalar('%NAME%')
                if package not in PACKAGES:
                    continue
                version, arch, digest = scalar('%VERSION%'), scalar('%ARCH%'), scalar('%SHA256SUM%')
                build = scalar('%BUILDDATE%')
                if (package in packages or name != package + '-' + version + '/desc'
                        or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._+~:-]{0,199}', version)
                        or arch not in {'any', architecture} or not re.fullmatch('[0-9a-f]{64}', digest)
                        or not re.fullmatch(r'[0-9]{1,11}', build)):
                    raise ValueError('Catalog package identity')
                built = datetime.fromtimestamp(int(build), timezone.utc)
                if built > checked:
                    raise ValueError('Future package build')
                packages[package] = {
                    'fact_version': 1, 'kind': 'package-catalog', 'name': package, 'version': version,
                    'scheme': 'arch', 'channel': channel, 'architecture': architecture,
                    'package_architecture': arch, 'package_sha256': digest,
                    'built_at': built.isoformat().replace('+00:00', 'Z'), 'catalog_checked_at': checked_at,
                }
        return {'status': 'observed', 'channel': channel, 'architecture': architecture,
                'url': endpoint, 'catalog_checked_at': checked_at,
                'fresh_until': (checked + timedelta(hours=1)).isoformat().replace('+00:00', 'Z'),
                'response_sha256': hashlib.sha256(raw).hexdigest(), 'method': 'bounded-' + compression + '-tar-desc-v1',
                'packages': [packages[key] for key in sorted(packages)]}
    except (OSError, EOFError, tarfile.TarError, UnicodeError, KeyError, OverflowError) as exc:
        raise ValueError('Malformed or unsupported catalog') from exc


class CatalogPublicRead:
    """Two fixed selectors; no redirects, credentials, proxies, or extraction."""
    def __init__(self, *, connection_factory=http.client.HTTPSConnection):
        self._connection_factory = connection_factory

    def catalog(self, channel, architecture):
        _scope(channel, architecture)
        if self._connection_factory is not http.client.HTTPSConnection:
            return self._once(channel, architecture)
        try:
            context = multiprocessing.get_context('fork')
            receive, send = context.Pipe(duplex=False)
            process = context.Process(target=_worker, args=(channel, architecture, send))
            process.start()
            send.close()
            try:
                if not receive.poll(DEADLINE_SECONDS):
                    raise PublicReadUnavailable()
                result = receive.recv()
                if not isinstance(result, dict):
                    raise PublicReadUnavailable()
                return result
            finally:
                receive.close()
                if process.is_alive():
                    process.terminate()
                process.join(timeout=1)
                if process.is_alive():
                    process.kill()
                    process.join(timeout=1)
        except (OSError, EOFError, ValueError) as exc:
            raise PublicReadUnavailable() from exc

    def _once(self, channel, architecture):
        connection = self._connection_factory('pkgs.omarchy.org', timeout=5)
        try:
            connection.request('GET', _scope(channel, architecture),
                               headers={'User-Agent': 'omarchy-community-knowledge-tools/0.1'})
            response = connection.getresponse()
            if response.status != 200:
                raise PublicReadUnavailable()
            raw, deadline = bytearray(), time.monotonic() + 10
            while True:
                if time.monotonic() > deadline:
                    raise PublicReadUnavailable()
                chunk = response.read(min(65536, MAX_COMPRESSED + 1 - len(raw)))
                raw.extend(chunk)
                if len(raw) > MAX_COMPRESSED:
                    raise PublicReadUnavailable()
                if not chunk:
                    break
            return parse_catalog(bytes(raw), channel, architecture,
                                 checked_at=datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'))
        except (OSError, ValueError, http.client.HTTPException) as exc:
            raise PublicReadUnavailable() from exc
        finally:
            connection.close()


def _worker(channel, architecture, pipe):
    try:
        pipe.send(CatalogPublicRead()._once(channel, architecture))
    except Exception:
        pipe.send(None)
    finally:
        pipe.close()
