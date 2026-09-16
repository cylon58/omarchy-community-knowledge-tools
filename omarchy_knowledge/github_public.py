"""Anonymous fixed-origin GET-only GitHub reads; no generic URL input or redirects."""
from dataclasses import dataclass
from datetime import datetime, timezone
import base64
import binascii
import hashlib
import http.client
import json
import multiprocessing
import re
import time
from urllib.parse import quote

from .git_objects import valid_oid


REPOSITORIES = frozenset({"omacom/omarchy"})
API_VERSION = "2026-03-10"
PUBLIC_READ_DEADLINE_SECONDS = 15


class PublicReadUnavailable(Exception):
    pass


@dataclass(frozen=True)
class PublicSnapshot:
    repository: str
    retrieved_at: str
    response_sha256: str
    payload: dict


def _repo(repo):
    if repo not in REPOSITORIES:
        raise ValueError("Repository is not allowlisted")
    return repo


def _tag(tag):
    if not isinstance(tag, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}", tag) or ".." in tag:
        raise ValueError("Unsupported tag")
    return quote(tag, safe="")


def _oid(oid):
    if not valid_oid(oid):
        raise ValueError("Full immutable object ID required")
    return oid


def _number(number):
    if type(number) is not int or not 1 <= number <= 2_147_483_647:
        raise ValueError("Invalid PR number")
    return str(number)


def _pairs(pairs):
    output = {}
    for key, value in pairs:
        if key in output:
            raise PublicReadUnavailable()
        output[key] = value
    return output


class GitHubPublicRead:
    """Explicit read methods only. A connection factory is the offline transport seam.

    HTTPSConnection ignores proxy, netrc, cookie and token environments. Responses
    have a 1 MiB cap, 5-second socket timeout and 10-second body-read deadline.
    Production reads run in a disposable process with a 15-second total deadline,
    including DNS and headers. Platforms without fork fail unavailable.
    Redirects (including same-origin redirects) are errors, never followed.
    """
    def __init__(self, *, connection_factory=http.client.HTTPSConnection):
        self._connection_factory = connection_factory

    def _read(self, repo, suffix):
        _repo(repo)
        # A supplied factory is a trusted offline test transport, never PR input.
        if self._connection_factory is not http.client.HTTPSConnection:
            return self._read_once(repo, suffix)
        try:
            context = multiprocessing.get_context("fork")
            receive, send = context.Pipe(duplex=False)
            process = context.Process(target=_public_worker, args=(repo, suffix, send))
            process.start()
            send.close()
            try:
                if not receive.poll(PUBLIC_READ_DEADLINE_SECONDS):
                    raise PublicReadUnavailable()
                result = receive.recv()
                if not isinstance(result, PublicSnapshot):
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

    def _read_once(self, repo, suffix):
        repo = _repo(repo)
        connection = self._connection_factory("api.github.com", timeout=5)
        try:
            connection.request("GET", "/repos/" + repo + suffix,
                               headers={"Accept": "application/vnd.github+json",
                                        "User-Agent": "omarchy-community-knowledge-tools/0.1",
                                        "X-GitHub-Api-Version": API_VERSION})
            response = connection.getresponse()
            if response.status != 200:
                raise PublicReadUnavailable()
            raw, deadline = bytearray(), time.monotonic() + 10
            while True:
                if time.monotonic() > deadline:
                    raise PublicReadUnavailable()
                chunk = response.read(min(65536, 1024 * 1024 + 1 - len(raw)))
                raw.extend(chunk)
                if len(raw) > 1024 * 1024:
                    raise PublicReadUnavailable()
                if not chunk:
                    break
            data = json.loads(raw, object_pairs_hook=_pairs,
                              parse_constant=lambda _: (_ for _ in ()).throw(PublicReadUnavailable()))
            if not isinstance(data, dict):
                raise PublicReadUnavailable()
            return PublicSnapshot(repo, datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                                  hashlib.sha256(raw).hexdigest(), data)
        except (OSError, http.client.HTTPException, ValueError, RecursionError) as exc:
            raise PublicReadUnavailable() from exc
        finally:
            connection.close()

    def pull(self, repo, number):
        return self._read(repo, "/pulls/" + _number(number))

    def ref(self, repo, tag):
        return self._read(repo, "/git/ref/tags/" + _tag(tag))

    def annotated_tag(self, repo, oid):
        return self._read(repo, "/git/tags/" + _oid(oid))

    def commit(self, repo, oid):
        return self._read(repo, "/commits/" + _oid(oid))

    def blob(self, repo, oid, max_bytes=65536):
        """Read a bounded immutable blob as inert bytes; never execute its content."""
        _oid(oid)
        if type(max_bytes) is not int or not 0 < max_bytes <= 65536:
            raise ValueError("Invalid blob byte bound")
        data = self._read(repo, "/git/blobs/" + oid).payload
        try:
            if (data["sha"] != oid or data["encoding"] != "base64" or type(data["size"]) is not int
                    or not 0 <= data["size"] <= max_bytes or not isinstance(data["content"], str)
                    or len(data["content"]) > 2 * max_bytes + 4):
                raise PublicReadUnavailable()
            raw = base64.b64decode(data["content"].replace("\n", ""), validate=True)
            if len(raw) != data["size"]:
                raise PublicReadUnavailable()
            digest = hashlib.new("sha1" if len(oid) == 40 else "sha256")
            digest.update(b"blob " + str(len(raw)).encode() + b"\0" + raw)
            if digest.hexdigest() != oid:
                raise PublicReadUnavailable()
            return raw
        except (ValueError, TypeError, KeyError, binascii.Error) as exc:
            raise PublicReadUnavailable() from exc

    def release_by_tag(self, repo, tag):
        return self._read(repo, "/releases/tags/" + _tag(tag))

    def compare(self, repo, base_oid, head_oid):
        return self._read(repo, "/compare/" + _oid(base_oid) + "..." + _oid(head_oid) + "?per_page=100&page=1")


def _public_worker(repo, suffix, pipe):
    try:
        pipe.send(GitHubPublicRead()._read_once(repo, suffix))
    except Exception:
        pipe.send(None)
    finally:
        pipe.close()
