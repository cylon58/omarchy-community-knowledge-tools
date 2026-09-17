"""Fixed-repository bounded GitHub reads and one restricted GraphQL CAS mutation.

No git fetch, checkout, candidate subprocess, redirects, arbitrary URLs, helpers,
netrc, proxy configuration, or inherited credentials. Scoped tokens stay in the
fixed GET/CAS HTTPS boundary. Objects are independently hashed before use.
"""
import base64
from datetime import datetime, timezone
import hashlib
import http.client
import itertools
import json
from pathlib import Path
import subprocess
import sys
import re
import time

from .git_objects import TreeEntry

REPOSITORY = "cylon58/omarchy-community-knowledge"
REPOSITORY_ID = 1373429914
DEPLOYMENTS = {
    "production": (REPOSITORY, REPOSITORY_ID),
    "pilot": ("cylon58/omarchy-community-knowledge-pilot", 1373467908),
}
MAX_RESPONSE = 1024 * 1024
MAX_BYTES = 32 * 1024 * 1024
MAX_CALLS = 512
TOTAL_SECONDS = 180
MAX_GRAPHQL_CALLS = 192
MAX_GRAPHQL_POINTS = 192
GRAPHQL_POINT_RESERVE = 100
MAX_CACHE_OBJECTS = 5000
MAX_CACHE_BYTES = 20 * 1024 * 1024
PAGES = {
    "production": ("cylon58.github.io", "/omarchy-community-knowledge/"),
    "pilot": ("cylon58.github.io", "/omarchy-community-knowledge-pilot/"),
}
PAGE_ARTIFACTS = frozenset({
    "canonical-objects.bundle",
    "canonical-update.json",
    "canonical-update.bundle",
})
PAGES_ATTEMPT_SECONDS = 15
# Admission requires merge_commit_sha to bind the exact GitHub test merge to B/H.
# 2026-03-10 removes that field; use the supported contract, not an absent-field
# fallback. 2022-11-28 is supported until at least 2028-03-12, 24 months after the
# 2026-03-12 announcement of the newer version.
# https://docs.github.com/en/rest/about-the-rest-api/breaking-changes
# https://github.blog/changelog/2026-03-12-rest-api-version-2026-03-10-is-now-available/
API_VERSION = "2022-11-28"


class NativeUnavailable(Exception):
    """Fixed non-echoing error, including ambiguous mutation outcomes."""


def require(condition):
    if not condition:
        raise NativeUnavailable()


def _oid(value):
    require(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{40}", value) is not None)
    return value


def _number(value):
    require(type(value) is int and 1 <= value <= 2_147_483_647)
    return str(value)


def _hash(kind, raw):
    return hashlib.sha1(kind.encode() + b" " + str(len(raw)).encode() + b"\0" + raw).hexdigest()


def strict_json(raw):
    from .admission import _bound_json_depth
    from .git_objects import ObjectUnavailable
    def pairs(items):
        out = {}
        for key, value in items:
            require(key not in out)
            out[key] = value
        return out
    try:
        require(isinstance(raw, bytes) and len(raw) <= MAX_RESPONSE)
        _bound_json_depth(raw)
        return json.loads(raw, object_pairs_hook=pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(NativeUnavailable()))
    except (ValueError, TypeError, RecursionError, ObjectUnavailable) as exc:
        raise NativeUnavailable() from exc


def _exchange(factory, method, path, body, token):
    connection = factory("api.github.com", timeout=5)
    try:
        headers = {"Accept": "application/vnd.github+json", "User-Agent": "omarchy-knowledge-native/1",
                   "X-GitHub-Api-Version": API_VERSION}
        if token is not None:
            headers["Authorization"] = "Bearer " + token
        if body is not None:
            headers["Content-Type"] = "application/json"
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        require(response.status == 200 or (method == "POST" and response.status == 201))
        raw, deadline = bytearray(), time.monotonic() + 10
        while True:
            require(time.monotonic() < deadline)
            chunk = response.read(min(65536, MAX_RESPONSE + 1 - len(raw)))
            raw.extend(chunk)
            require(len(raw) <= MAX_RESPONSE)
            if not chunk:
                break
        return bytes(raw)
    except (OSError, http.client.HTTPException, ValueError) as exc:
        raise NativeUnavailable() from exc
    finally:
        connection.close()


def _stdio_worker():
    """Fixed GET/CAS HTTP boundary; scoped tokens arrive only through stdin."""
    try:
        data = json.loads(sys.stdin.buffer.read(2 * MAX_RESPONSE + 1))
        body = base64.b64decode(data["body"]) if data["body"] is not None else None
        raw = _exchange(http.client.HTTPSConnection, data["method"], data["path"], body, data["token"])
        sys.stdout.buffer.write(raw)
    except Exception:
        pass


def _pages_exchange(factory, deployment, artifact, maximum, deadline=None):
    """Read one enum-selected fixed-origin artifact under a caller-supplied cap."""
    from .object_bundle import MAX_COMPRESSED_BUNDLE
    from .update_pack import MAX_MANIFEST
    require(deployment in PAGES and artifact in PAGE_ARTIFACTS
            and type(maximum) is int)
    if artifact == "canonical-update.json":
        require(maximum == MAX_MANIFEST)
    elif artifact == "canonical-objects.bundle":
        require(maximum == MAX_COMPRESSED_BUNDLE)
    else:
        require(type(maximum) is int and 0 < maximum <= MAX_COMPRESSED_BUNDLE)
    host, prefix = PAGES[deployment]
    path = prefix + artifact
    deadline = time.monotonic() + 10 if deadline is None else min(deadline, time.monotonic() + 10)
    connection = factory(host, timeout=min(5, max(.001, deadline - time.monotonic())))
    try:
        headers = {"Accept": "application/octet-stream",
                   "User-Agent": "omarchy-knowledge-native/1"}
        connection.request("GET", path, body=None, headers=headers)
        response = connection.getresponse()
        require(response.status == 200)
        raw = bytearray()
        while True:
            require(time.monotonic() < deadline)
            chunk = response.read(min(65536, maximum + 1 - len(raw)))
            raw.extend(chunk)
            require(len(raw) <= maximum)
            if not chunk:
                break
        return bytes(raw)
    except (OSError, http.client.HTTPException, ValueError) as exc:
        raise NativeUnavailable() from exc
    finally:
        connection.close()


def _pages_stdio_worker():
    """Fixed anonymous Pages GET boundary; accepts only deployment/artifact enums."""
    try:
        request = json.loads(sys.stdin.buffer.read(256))
        require(type(request) is dict and set(request) == {"deployment", "artifact", "maximum"})
        sys.stdout.buffer.write(_pages_exchange(
            http.client.HTTPSConnection,
            request["deployment"], request["artifact"], request["maximum"],
        ))
    except Exception:
        pass


class _HTTP:
    def __init__(self, *, deployment="production", connection_factory=http.client.HTTPSConnection):
        require(deployment in DEPLOYMENTS)
        self.repository = DEPLOYMENTS[deployment][0]
        self.factory = connection_factory
        self.calls = 0
        self.bytes = 0
        self.graphql_calls = 0
        self.graphql_points = 0
        self.graphql_remaining = None
        self.deadline = time.monotonic() + TOTAL_SECONDS

    def reserve_pages(self, maximum, *, reserve_calls=0, reserve_bytes=0,
                      reserve_seconds=0):
        """Charge one attempt while preserving explicitly reserved fallback budget."""
        require(all(type(value) is int and value >= 0 for value in (
            maximum, reserve_calls, reserve_bytes, reserve_seconds,
        )))
        now = time.monotonic()
        enforced = maximum + 1  # one-byte sentinel detects an oversized body
        require(maximum > 0
                and self.calls + 1 + reserve_calls <= MAX_CALLS
                and self.bytes + enforced + reserve_bytes <= MAX_BYTES
                and now < self.deadline
                and (reserve_seconds == 0
                     or now + PAGES_ATTEMPT_SECONDS + reserve_seconds
                     < self.deadline))
        self.calls += 1

    def charge_pages(self, amount):
        require(type(amount) is int and amount >= 0)
        self.bytes += amount
        require(self.bytes <= MAX_BYTES and time.monotonic() < self.deadline)

    def request(self, method, path, body=None, token=None, *, charge_unknown=False):
        prefix = "/repos/" + self.repository
        suffix = path[len(prefix):] if path.startswith(prefix) else None
        allowed_read = suffix is not None and (suffix in {"", "/git/ref/heads/main"}
            or re.fullmatch(r"/git/(?:commits|trees|blobs)/[0-9a-f]{40}", suffix)
            or re.fullmatch(r"/pulls/[1-9][0-9]{0,9}", suffix)
            or re.fullmatch(r"/pulls\?state=open&sort=created&direction=desc&per_page=20&page=(?:[1-9]|10)", suffix))
        require((method == "GET" and allowed_read and body is None)
                or (method == "POST" and path == "/graphql" and body is not None))
        self.calls += 1
        require(self.calls <= MAX_CALLS and self.bytes < MAX_BYTES and time.monotonic() < self.deadline)
        require(body is None or len(body) <= MAX_RESPONSE)
        try:
            if self.factory is not http.client.HTTPSConnection:
                raw = _exchange(self.factory, method, path, body, token)
            else:
                # Hard wall deadline covers DNS, TLS and response headers as well.
                root = str(Path(__file__).resolve().parent.parent)
                code = "import sys;sys.path.insert(0," + repr(root) + ");from omarchy_knowledge.github_native import _stdio_worker;_stdio_worker()"
                env = {"PATH": "/usr/bin:/bin", "LC_ALL": "C"}
                payload = json.dumps({"method": method, "path": path, "token": token,
                                      "body": base64.b64encode(body).decode() if body is not None else None}).encode()
                with subprocess.Popen([sys.executable, "-I", "-c", code], stdin=subprocess.PIPE,
                                      stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env, cwd="/") as process:
                    try:
                        raw, _ = process.communicate(payload, timeout=min(15, max(.001, self.deadline - time.monotonic())))
                        require(process.returncode == 0 and 0 < len(raw) <= MAX_RESPONSE)
                    except BaseException:
                        process.kill()
                        process.wait()
                        raise
        except (NativeUnavailable, OSError, subprocess.SubprocessError) as exc:
            if charge_unknown:
                self.bytes += MAX_RESPONSE + 1
                require(self.bytes <= MAX_BYTES and time.monotonic() < self.deadline)
            if isinstance(exc, NativeUnavailable):
                raise
            raise NativeUnavailable() from exc
        self.bytes += len(raw)
        require(self.bytes <= MAX_BYTES and time.monotonic() < self.deadline)
        return strict_json(raw)


class GitHubRead:
    """Fixed reads; optional caller-supplied scoped token, never environment lookup."""
    def __init__(self, *, deployment="production", read_token=None, connection_factory=http.client.HTTPSConnection):
        require(deployment in DEPLOYMENTS)
        self.repository_name = DEPLOYMENTS[deployment][0]
        self.deployment = deployment
        self.connection_factory = connection_factory
        self.http = _HTTP(deployment=deployment, connection_factory=connection_factory)
        self.objects = APIObjects(self, total_deadline=self.http.deadline)
        require(read_token is None or (isinstance(read_token, str) and 1 <= len(read_token) <= 4096
                                       and not any(c.isspace() for c in read_token)))
        self.__read_token = read_token
        self.__batch_failed = False

    def _object_batch_configured(self):
        return self.__read_token is not None

    def _get(self, suffix):
        try:
            return self.http.request("GET", "/repos/" + self.repository_name + suffix, token=self.__read_token)
        except (OSError, subprocess.SubprocessError) as exc:
            raise NativeUnavailable() from exc

    def repository(self):
        return self._get("")

    def branch(self):
        data = self._get("/git/ref/heads/main")
        require(data["ref"] == "refs/heads/main" and data["object"]["type"] == "commit")
        return _oid(data["object"]["sha"])

    def pull(self, number):
        return self._get("/pulls/" + _number(number))

    def pending(self, page=1):
        require(type(page) is int and 1 <= page <= 10)
        data = self._get("/pulls?state=open&sort=created&direction=desc&per_page=20&page=" + str(page))
        require(isinstance(data, list) and len(data) <= 20)
        numbers = []
        for item in data:
            _number(item["number"])
            numbers.append({"number": item["number"], "head": _oid(item["head"]["sha"])})
        require(len({item["number"] for item in numbers}) == len(numbers))
        return numbers

    def git_commit(self, oid):
        return self._get("/git/commits/" + _oid(oid))

    def git_tree(self, oid):
        return self._get("/git/trees/" + _oid(oid))

    def git_blob(self, oid):
        return self._get("/git/blobs/" + _oid(oid))

    def _read_object_batch(self, kind, oids):
        """Attempt one fixed authenticated batch; failure leaves REST authoritative."""
        if self.__read_token is None or self.__batch_failed or self.objects.bundle_only:
            return None
        from .github_object_batch import build_request, decode_response
        try:
            body = build_request(self.deployment, kind, oids)
        except ValueError as exc:
            raise NativeUnavailable() from exc
        require(self.http.graphql_calls < MAX_GRAPHQL_CALLS
                and self.http.calls < MAX_CALLS and self.http.bytes < MAX_BYTES
                and time.monotonic() < self.http.deadline)
        self.http.graphql_calls += 1
        try:
            data = self.http.request(
                "POST", "/graphql", body, self.__read_token, charge_unknown=True)
        except NativeUnavailable:
            self.__batch_failed = True
            require(self.http.bytes <= MAX_BYTES and time.monotonic() < self.http.deadline)
            return None
        try:
            decoded, cost, remaining = decode_response(
                self.deployment, kind, oids, data)
        except (KeyError, TypeError, ValueError, RecursionError):
            self.__batch_failed = True
            return None
        self.http.graphql_points += cost
        self.http.graphql_remaining = remaining
        if self.http.graphql_points > MAX_GRAPHQL_POINTS:
            self.__batch_failed = True
            raise NativeUnavailable()
        if remaining < GRAPHQL_POINT_RESERVE:
            self.__batch_failed = True
            return None
        return decoded

    def commit_info(self, oid):
        return self.objects.info(oid)

    def _pages(self, artifact, maximum, *, reserve_calls=0, reserve_bytes=0,
               reserve_seconds=0):
        """Use the adapter's aggregate budget for one fixed anonymous Pages read."""
        self.http.reserve_pages(
            maximum,
            reserve_calls=reserve_calls,
            reserve_bytes=reserve_bytes,
            reserve_seconds=reserve_seconds,
        )
        try:
            if self.connection_factory is not http.client.HTTPSConnection:
                raw = _pages_exchange(
                    self.connection_factory, self.deployment, artifact, maximum,
                    self.http.deadline,
                )
            else:
                root = str(Path(__file__).resolve().parent.parent)
                code = ("import sys;sys.path.insert(0," + repr(root)
                        + ");from omarchy_knowledge.github_native import _pages_stdio_worker;_pages_stdio_worker()")
                env = {"PATH": "/usr/bin:/bin", "LC_ALL": "C"}
                payload = json.dumps({
                    "deployment": self.deployment,
                    "artifact": artifact,
                    "maximum": maximum,
                }, separators=(",", ":")).encode()
                with subprocess.Popen(
                    [sys.executable, "-I", "-c", code],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL, env=env, cwd="/",
                ) as process:
                    try:
                        raw, _ = process.communicate(
                            payload,
                            timeout=min(PAGES_ATTEMPT_SECONDS,
                                        max(.001, self.http.deadline - time.monotonic())),
                        )
                        require(process.returncode == 0 and 0 < len(raw) <= maximum)
                    except BaseException:
                        process.kill()
                        process.wait()
                        raise
            require(0 < len(raw) <= maximum)
        except (NativeUnavailable, OSError, subprocess.SubprocessError) as exc:
            # The parent cannot observe partial child bytes reliably. Charge the
            # complete enforced cap, including status, timeout and worker errors.
            self.http.charge_pages(maximum + 1)
            if isinstance(exc, NativeUnavailable):
                raise
            raise NativeUnavailable() from exc
        self.http.charge_pages(len(raw))
        return raw

    def canonical_proofs(self, revision, cached):
        """Yield at most one local/update candidate, then one full fallback proof.

        Every yielded value is an inert, typed-hash-verified complete object map
        encoded as an object bundle. The caller must still canonically replay it.
        """
        from .object_bundle import (BundleUnavailable, MAX_COMPRESSED_BUNDLE,
                                    decode, decode_seed, encode)
        from .update_pack import (MAX_MANIFEST, UpdatePackUnavailable, apply,
                                  decode_manifest)
        revision = _oid(revision)
        base_head = None
        base_objects = None
        if isinstance(cached, bytes):
            try:
                base_head, base_objects = decode_seed(cached)
                if base_head == revision:
                    decode(cached, revision)
                    yield cached
                    base_head = None  # replay failure permits only full fallback
            except BundleUnavailable:
                base_head = base_objects = None

        if base_head is not None and base_head != revision:
            try:
                manifest_raw = self._pages(
                    "canonical-update.json", MAX_MANIFEST,
                    reserve_calls=1, reserve_bytes=MAX_COMPRESSED_BUNDLE + 1,
                    reserve_seconds=PAGES_ATTEMPT_SECONDS,
                )
                manifest = decode_manifest(
                    manifest_raw,
                    expected_deployment=self.deployment,
                    expected_target_head=revision,
                )
                require(manifest.base_head == base_head)
                pack = self._pages(
                    "canonical-update.bundle", manifest.pack_size,
                    reserve_calls=1, reserve_bytes=MAX_COMPRESSED_BUNDLE + 1,
                    reserve_seconds=PAGES_ATTEMPT_SECONDS,
                )
                target = apply(
                    manifest_raw, pack,
                    deployment=self.deployment,
                    base_head=base_head,
                    base_objects=base_objects,
                    expected_target_head=revision,
                )
                yield encode(revision, target)
            except (BundleUnavailable, UpdatePackUnavailable, NativeUnavailable):
                pass

        raw = self._pages("canonical-objects.bundle", MAX_COMPRESSED_BUNDLE)
        try:
            decode(raw, revision)
        except BundleUnavailable as exc:
            raise NativeUnavailable() from exc
        yield raw

    def prefill_canonical(self, revision):
        """Load inert Pages bytes only after the caller authenticates main via API."""
        from .object_bundle import MAX_COMPRESSED_BUNDLE
        raw = self._pages("canonical-objects.bundle", MAX_COMPRESSED_BUNDLE)
        self.objects.load_bundle(raw, _oid(revision))

    def seed_canonical(self):
        """Optionally load the preceding fixed-origin proof as an inert cache."""
        try:
            from .object_bundle import MAX_COMPRESSED_BUNDLE
            raw = self._pages("canonical-objects.bundle", MAX_COMPRESSED_BUNDLE)
            self.objects.load_seed(raw)
            return True
        except (NativeUnavailable, ValueError):
            return False


class APIObjects:
    """Hash-verified raw Git objects reconstructed from bounded GitHub API values.

    Commit identity never rests on the API's sha label alone. For unsigned commits
    normalized dates require bounded recovery of quarter-hour timezone offsets.
    Unknown headers/encodings/signature layouts fail unavailable, not trusted.
    """
    def __init__(self, api, *, total_deadline=None):
        self.api = api
        self.cache = {}
        self.cache_bytes = 0
        self.seeded = set()
        self.touched = set()
        self.total_deadline = (time.monotonic() + TOTAL_SECONDS
                               if total_deadline is None else total_deadline)
        self.bundle_only = False
        self.retrieval()

    def retrieval(self):
        self.deadline = self.total_deadline
        self.visits = 0

    def begin(self):
        self.deadline = min(self.total_deadline, time.monotonic() + 20)
        self.visits = 0

    def warm(self, commits):
        """Bounded retrieval before the offline check's shorter parsing deadline."""
        from projections import validate_ingestion_receipt
        self.retrieval()
        if self.bundle_only:
            return
        if (hasattr(self.api, "_read_object_batch")
                and self.api._object_batch_configured()):
            roots = []
            for commit in set(commits):
                raw = self._commit_raw(commit, prefetch=True)
                first = raw.partition(b"\n")[0]
                require(first.startswith(b"tree "))
                roots.append(_oid(first[5:].decode("ascii")))
            current = self._prefetch_trees(roots)
            if current is None:
                return
            relevant = {}
            for path, entry in current:
                if entry.kind != "blob" or not path.startswith((b"records/", b"provenance/")):
                    continue
                require(0 <= entry.size <= 65536)
                if entry.oid in relevant:
                    require(relevant[entry.oid] == entry.size)
                relevant[entry.oid] = entry.size
            missing = [(oid, size) for oid, size in relevant.items()
                       if ("blob", oid) not in self.cache]
            for batch in self._blob_batches(missing):
                decoded = self.api._read_object_batch("blob", [oid for oid, _size in batch])
                if decoded is None:
                    return
                self._install_batch("blob", decoded)
            historical = set()
            for path, entry in current:
                if entry.kind != "blob" or not path.startswith(b"provenance/ingestion/"):
                    continue
                raw = self.cache.get(("blob", entry.oid))
                require(isinstance(raw, bytes) and len(raw) == entry.size)
                receipt = strict_json(raw)
                validate_ingestion_receipt(receipt)
                key = ("merge_commit_oid" if receipt["receipt_version"] == 1
                       else "accepted_commit_oid")
                historical.add(receipt["source"][key]["hex"])
            historical_roots = []
            for commit in historical:
                raw = self._commit_raw(commit, prefetch=True)
                first = raw.partition(b"\n")[0]
                require(first.startswith(b"tree "))
                historical_roots.append(_oid(first[5:].decode("ascii")))
            if historical_roots:
                self._prefetch_trees(historical_roots, collect=False)
            return

        # Non-native adapters and anonymous readers retain the original REST path.
        historical = set()
        for commit in set(commits):
            for entry in self.entries(self.commit(commit)):
                if entry.kind != "blob" or not entry.path.startswith((b"records/", b"provenance/")):
                    continue
                raw = self.blob(entry.oid)
                require(len(raw) == entry.size)
                if entry.path.startswith(b"provenance/ingestion/"):
                    receipt = strict_json(raw)
                    validate_ingestion_receipt(receipt)
                    key = "merge_commit_oid" if receipt["receipt_version"] == 1 else "accepted_commit_oid"
                    historical.add(receipt["source"][key]["hex"])
        for commit in historical:
            self.entries(self.commit(commit))

    @staticmethod
    def _blob_batches(rows):
        """Bound response JSON for worst-case escaping, with at most 32 blobs."""
        batches, batch, estimated = [], [], 8192
        for oid, size in rows:
            require(_oid(oid) == oid and type(size) is int and 0 <= size <= 65536)
            item = 6 * size + 512
            require(8192 + item < MAX_RESPONSE)
            if batch and (len(batch) >= 32 or estimated + item >= MAX_RESPONSE):
                batches.append(batch)
                batch, estimated = [], 8192
            batch.append((oid, size))
            estimated += item
        if batch:
            batches.append(batch)
        return batches

    def _prefetch_trees(self, roots, *, collect=True):
        """Load a bounded tree frontier without consuming validation visits/touches."""
        roots = list(dict.fromkeys(map(_oid, roots)))
        require(not collect or len(roots) <= 3)
        frontier = list(roots)
        seen = set()
        depth = 0
        while frontier:
            require(depth <= 8)
            level = list(dict.fromkeys(oid for oid in frontier if oid not in seen))
            seen.update(level)
            require(len(seen) <= MAX_CACHE_OBJECTS)
            missing = [oid for oid in level if ("tree", oid) not in self.cache]
            for offset in range(0, len(missing), 8):
                batch = missing[offset:offset + 8]
                decoded = self.api._read_object_batch("tree", batch)
                if decoded is None:
                    return None
                self._install_batch("tree", decoded)
            following = []
            for oid in level:
                key = ("entries", oid)
                if key not in self.cache:
                    self.cache[key] = self._tree_entries(self.cache[("tree", oid)])
                following.extend(entry.oid for entry in self.cache[key]
                                 if entry.kind == "tree" and entry.oid not in seen)
            frontier = following
            depth += 1

        if not collect:
            return []
        combined = []
        for root in roots:
            result = []
            def walk(oid, prefix, nested):
                require(nested <= 8)
                for entry in self.cache[("entries", oid)]:
                    path = prefix + entry.path
                    require(len(path) <= 512 and len(result) < 4096)
                    result.append((path, entry))
                    if entry.kind == "tree":
                        walk(entry.oid, path + b"/", nested + 1)
            walk(root, b"", 0)
            combined.extend(result)
        return combined

    def _visit(self):
        self.visits += 1
        require(self.visits <= 5000 and time.monotonic() < self.deadline)

    def _save(self, kind, requested, raw, *, touched=True):
        require(_hash(kind, raw) == requested)
        if (kind, requested) in self.cache:
            require(self.cache[(kind, requested)] == raw)
            if touched:
                self.touched.add((kind, requested))
            return raw
        typed_count = sum(
            isinstance(key, tuple) and len(key) == 2
            and key[0] in {"commit", "tree", "blob"}
            for key in self.cache
        )
        require(typed_count < MAX_CACHE_OBJECTS
                and self.cache_bytes + len(raw) <= MAX_CACHE_BYTES)
        self.cache[(kind, requested)] = raw
        self.cache_bytes += len(raw)
        if touched:
            self.touched.add((kind, requested))
        return raw

    def _install_batch(self, kind, decoded):
        """Atomically install a fully decoded batch without validation side effects."""
        require(kind in {"tree", "blob"} and isinstance(decoded, dict))
        additions = {}
        entry_rows = {}
        for oid, value in decoded.items():
            require(_oid(oid) == oid and isinstance(value.raw, bytes)
                    and _hash(kind, value.raw) == oid)
            key = (kind, oid)
            if key in self.cache:
                require(self.cache[key] == value.raw)
            else:
                additions[key] = value.raw
            if kind == "tree":
                require(value.entries is not None)
                entry_rows[("entries", oid)] = list(value.entries)
            else:
                require(value.entries is None)
        typed_count = sum(
            isinstance(key, tuple) and len(key) == 2
            and key[0] in {"commit", "tree", "blob"}
            for key in self.cache
        )
        require(typed_count + len(additions) <= MAX_CACHE_OBJECTS
                and self.cache_bytes + sum(map(len, additions.values())) <= MAX_CACHE_BYTES)
        self.cache.update(additions)
        self.cache.update(entry_rows)
        self.cache_bytes += sum(map(len, additions.values()))

    def _commit_raw(self, requested, *, prefetch=False):
        if not prefetch:
            self._visit()
        requested = _oid(requested)
        if ("commit", requested) in self.cache:
            if not prefetch:
                self.touched.add(("commit", requested))
            return self.cache[("commit", requested)]
        require(not self.bundle_only)
        data = self.api.git_commit(requested)
        require(data["sha"] == requested)
        verification = data.get("verification") or {}
        payload, signature = verification.get("payload"), verification.get("signature")
        if isinstance(payload, str) and isinstance(signature, str):
            raw = payload.encode()
            require(len(raw) <= 65536 and len(signature.encode()) <= 16384)
            head, separator, message = raw.partition(b"\n\n")
            require(separator == b"\n\n")
            lines = head.split(b"\n")
            require(len(lines) <= 64)
            # Git inserts the gpgsig header; support exact known line endings only.
            for sig in (signature, signature.rstrip("\n")):
                field = b"gpgsig " + sig.encode().replace(b"\n", b"\n ")
                for offset in range(1, len(lines) + 1):
                    candidate = b"\n".join(lines[:offset] + [field] + lines[offset:]) + b"\n\n" + message
                    if _hash("commit", candidate) == requested:
                        return self._save("commit", requested, candidate, touched=not prefetch)
        tree = _oid(data["tree"]["sha"])
        parents = data["parents"]
        require(isinstance(parents, list) and len(parents) <= 8)
        prefix = f"tree {tree}\n" + "".join("parent " + _oid(p["sha"]) + "\n" for p in parents)
        zones = [0] + list(range(-720, 0, 15)) + list(range(15, 841, 15))
        def identities(key):
            value = data[key]
            require(all(isinstance(value[k], str) for k in ("name", "email", "date")))
            require(len(value["name"]) <= 1024 and len(value["email"]) <= 1024
                    and not any(c in value["name"] + value["email"] for c in "\n\r\0<>"))
            date = datetime.fromisoformat(value["date"].replace("Z", "+00:00"))
            require(date.tzinfo is not None)
            epoch = int(date.timestamp())
            result = []
            for zone in zones:
                sign = "+" if zone >= 0 else "-"
                minutes = abs(zone)
                result.append(f"{key} {value['name']} <{value['email']}> {epoch} {sign}{minutes // 60:02d}{minutes % 60:02d}\n")
            return result
        authors, committers = identities("author"), identities("committer")
        message = data["message"]
        require(isinstance(message, str) and len(message.encode()) <= 60000)
        deadline = min(self.deadline, time.monotonic() + 3)
        # Common same-timezone commits first; then independent author/committer.
        for attempt, (a, c) in enumerate(itertools.chain(zip(authors, committers), itertools.product(authors, committers))):
            if attempt % 64 == 0:
                require(time.monotonic() < deadline)
            for ending in ("", "\n"):
                raw = (prefix + a + c + "\n" + message + ending).encode()
                if _hash("commit", raw) == requested:
                    return self._save("commit", requested, raw, touched=not prefetch)
        raise NativeUnavailable()

    def info(self, requested):
        raw = self._commit_raw(requested)
        headers, _, message = raw.partition(b"\n\n")
        lines = headers.split(b"\n")
        require(lines[0].startswith(b"tree "))
        tree = _oid(lines[0][5:].decode("ascii"))
        parents = [_oid(line[7:].decode("ascii")) for line in lines if line.startswith(b"parent ")]
        committers = [line for line in lines if line.startswith(b"committer ")]
        require(len(committers) == 1)
        stamp = int(committers[0].rsplit(b" ", 2)[1])
        return {"oid": requested, "tree": tree, "parents": parents,
                "message": message.decode().rstrip("\n"),
                "date": datetime.fromtimestamp(stamp, timezone.utc).isoformat().replace("+00:00", "Z")}

    def commit(self, requested):
        return self.info(requested)["tree"]

    def _tree(self, requested):
        self._visit()
        if ("tree", requested) in self.cache:
            if ("entries", requested) not in self.cache:
                self.cache[("entries", requested)] = self._tree_entries(self.cache[("tree", requested)])
            self.touched.add(("tree", requested))
            return self.cache[("entries", requested)]
        require(not self.bundle_only)
        data = self.api.git_tree(_oid(requested))
        require(data["sha"] == requested and data.get("truncated") is False
                and isinstance(data["tree"], list) and len(data["tree"]) <= 4096)
        rows, entries, names = [], [], set()
        for item in data["tree"]:
            name, mode, kind = item["path"], item["mode"], item["type"]
            require(isinstance(name, str) and len(name.encode()) <= 512 and name not in names
                    and name not in {"", ".", ".."} and "/" not in name and "\0" not in name)
            names.add(name)
            require((mode, kind) in {("040000", "tree"), ("100644", "blob"), ("100755", "blob"),
                                    ("120000", "blob"), ("160000", "commit")})
            entry_oid = _oid(item["sha"])
            size = item.get("size", -1)
            require(type(size) is int and (size >= 0 if kind == "blob" else size == -1))
            entries.append(TreeEntry(name.encode(), mode, kind, entry_oid, size))
            rows.append((name.encode() + (b"/" if kind == "tree" else b""),
                         mode.lstrip("0").encode() + b" " + name.encode() + b"\0" + bytes.fromhex(entry_oid)))
        raw = b"".join(row for _, row in sorted(rows))
        require(len(raw) <= MAX_RESPONSE)
        self._save("tree", requested, raw)
        self.cache[("entries", requested)] = entries
        return entries

    def _tree_entries(self, raw, cache=None):
        cache = self.cache if cache is None else cache
        entries, names, offset, previous = [], set(), 0, None
        modes = {b"40000": ("040000", "tree"), b"100644": ("100644", "blob"),
                 b"100755": ("100755", "blob"), b"120000": ("120000", "blob"),
                 b"160000": ("160000", "commit")}
        while offset < len(raw):
            space = raw.find(b" ", offset)
            nul = raw.find(b"\0", space + 1 if space >= 0 else offset)
            require(space > offset and nul > space + 1 and nul + 21 <= len(raw))
            mode = raw[offset:space]
            require(mode in modes)
            normalized, kind = modes[mode]
            name = raw[space + 1:nul]
            require(len(name) <= 512 and name not in names and name not in {b"", b".", b".."}
                    and b"/" not in name and b"\0" not in name)
            names.add(name)
            sort_key = name + (b"/" if kind == "tree" else b"")
            require(previous is None or previous < sort_key)
            previous = sort_key
            oid = raw[nul + 1:nul + 21].hex()
            size = len(cache[("blob", oid)]) if kind == "blob" and ("blob", oid) in cache else -1
            entries.append(TreeEntry(name, normalized, kind, oid, size))
            require(len(entries) <= 4096)
            offset = nul + 21
        require(offset == len(raw))
        return entries

    def entries(self, requested):
        result = []
        def walk(tree, prefix, depth):
            require(depth <= 8)
            for entry in self._tree(tree):
                path = prefix + entry.path
                require(len(path) <= 512 and len(result) < 4096)
                result.append(TreeEntry(path, entry.mode, entry.kind, entry.oid, entry.size))
                if entry.kind == "tree":
                    walk(entry.oid, path + b"/", depth + 1)
        walk(_oid(requested), b"", 0)
        return result

    def blob(self, requested, max_bytes=65536):
        self._visit()
        require(type(max_bytes) is int and 0 <= max_bytes <= 65536)
        requested = _oid(requested)
        if ("blob", requested) in self.cache:
            raw = self.cache[("blob", requested)]
            require(len(raw) <= max_bytes)
            self.touched.add(("blob", requested))
            return raw
        require(not self.bundle_only)
        data = self.api.git_blob(requested)
        require(data["sha"] == requested and data["encoding"] == "base64"
                and type(data["size"]) is int and 0 <= data["size"] <= max_bytes
                and isinstance(data["content"], str) and len(data["content"]) <= 2 * max_bytes + 4)
        raw = base64.b64decode(data["content"].replace("\n", ""), validate=True)
        require(len(raw) == data["size"])
        return self._save("blob", requested, raw)

    def export_bundle(self, revision):
        from .object_bundle import BundleUnavailable, encode
        try:
            require(time.monotonic() < self.total_deadline)
            objects = {key: raw for key, raw in self.cache.items()
                       if key not in self.seeded or key in self.touched}
            result = encode(_oid(revision), objects)
            require(time.monotonic() < self.total_deadline)
            return result
        except BundleUnavailable as exc:
            raise NativeUnavailable() from exc

    def load_seed(self, raw):
        """Atomically install an old proof as inert, capacity-reserved objects."""
        from .object_bundle import (BundleUnavailable, MAX_SEED_OBJECTS,
                                    MAX_SEED_RAW_OBJECTS, decode_seed)
        require(not self.cache and not self.bundle_only)
        try:
            require(time.monotonic() < self.total_deadline)
            head, decoded = decode_seed(raw)
            require(len(decoded) <= MAX_SEED_OBJECTS
                    and sum(len(content) for content in decoded.values()) <= MAX_SEED_RAW_OBJECTS)
            retained = dict(decoded)
            for key, content in decoded.items():
                if key[0] != "tree":
                    continue
                entries = self._tree_entries(content, decoded)
                if any(entry.kind == "blob" and entry.size < 0 for entry in entries):
                    retained.pop(key)
            require(time.monotonic() < self.total_deadline)
            self.cache = retained
            self.cache_bytes = sum(len(content) for content in retained.values())
            self.seeded = set(retained)
            return head
        except BundleUnavailable as exc:
            raise NativeUnavailable() from exc

    def load_bundle(self, raw, revision):
        from .object_bundle import BundleUnavailable, decode
        require(not self.cache and not self.bundle_only)
        try:
            require(time.monotonic() < self.total_deadline)
            objects = decode(raw, _oid(revision))
            require(time.monotonic() < self.total_deadline)
            for (kind, oid), content in objects.items():
                self._save(kind, oid, content, touched=False)
            self.bundle_only = True
        except BundleUnavailable as exc:
            raise NativeUnavailable() from exc


class GitHubWriter(GitHubRead):
    """Scoped job token stays in the fixed GET/CAS HTTPS boundary, passed on stdin."""
    def __init__(self, token, *, deployment="production", connection_factory=http.client.HTTPSConnection):
        super().__init__(deployment=deployment, read_token=token, connection_factory=connection_factory)
        require(isinstance(token, str) and 1 <= len(token) <= 4096 and not any(c.isspace() for c in token))
        self.__token = token

    def create_commit(self, expected_base, additions, message):
        from .admission import PATH
        require(isinstance(additions, dict) and 1 <= len(additions) <= 10)
        files = []
        for path, raw in sorted(additions.items()):
            require(isinstance(path, str) and PATH.fullmatch(path) is not None
                    and isinstance(raw, bytes) and len(raw) <= 65536)
            require(path.startswith(("records/", "provenance/ingestion/")))
            files.append({"path": path, "contents": base64.b64encode(raw).decode()})
        require(isinstance(message, str) and len(message.encode()) <= 8192)
        headline, separator, body = message.partition("\n\n")
        require(headline in {"Omarchy knowledge snapshot import", "Record source-bound ingestion receipt"})
        if headline == "Record source-bound ingestion receipt":
            require(all(item["path"].startswith("provenance/ingestion/") for item in files)
                    and not separator)
        else:
            require(all(item["path"].startswith("records/") for item in files) and bool(separator))
        inputs = {"branch": {"repositoryNameWithOwner": self.repository_name, "branchName": "main"},
                  "expectedHeadOid": _oid(expected_base), "message": {"headline": headline},
                  "fileChanges": {"additions": files}}
        if separator:
            inputs["message"]["body"] = body
        query = "mutation($input:CreateCommitOnBranchInput!){createCommitOnBranch(input:$input){commit{oid}}}"
        payload = json.dumps({"query": query, "variables": {"input": inputs}}, separators=(",", ":")).encode()
        try:
            data = self.http.request("POST", "/graphql", payload, self.__token)
        except (OSError, subprocess.SubprocessError) as exc:
            raise NativeUnavailable() from exc
        require(isinstance(data, dict) and not data.get("errors"))
        try:
            return _oid(data["data"]["createCommitOnBranch"]["commit"]["oid"])
        except (KeyError, TypeError) as exc:
            raise NativeUnavailable() from exc
