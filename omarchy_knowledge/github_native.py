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


class _HTTP:
    def __init__(self, *, deployment="production", connection_factory=http.client.HTTPSConnection):
        require(deployment in DEPLOYMENTS)
        self.repository = DEPLOYMENTS[deployment][0]
        self.factory = connection_factory
        self.calls = 0
        self.bytes = 0
        self.deadline = time.monotonic() + TOTAL_SECONDS

    def request(self, method, path, body=None, token=None):
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
        self.bytes += len(raw)
        require(self.bytes <= MAX_BYTES and time.monotonic() < self.deadline)
        return strict_json(raw)


class GitHubRead:
    """Fixed reads; optional caller-supplied scoped token, never environment lookup."""
    def __init__(self, *, deployment="production", read_token=None, connection_factory=http.client.HTTPSConnection):
        require(deployment in DEPLOYMENTS)
        self.repository_name = DEPLOYMENTS[deployment][0]
        self.http = _HTTP(deployment=deployment, connection_factory=connection_factory)
        self.objects = APIObjects(self)
        require(read_token is None or (isinstance(read_token, str) and 1 <= len(read_token) <= 4096
                                       and not any(c.isspace() for c in read_token)))
        self.__read_token = read_token

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

    def commit_info(self, oid):
        return self.objects.info(oid)


class APIObjects:
    """Hash-verified raw Git objects reconstructed from bounded GitHub API values.

    Commit identity never rests on the API's sha label alone. For unsigned commits
    normalized dates require bounded recovery of quarter-hour timezone offsets.
    Unknown headers/encodings/signature layouts fail unavailable, not trusted.
    """
    def __init__(self, api):
        self.api = api
        self.cache = {}
        self.cache_bytes = 0
        self.total_deadline = time.monotonic() + TOTAL_SECONDS
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

    def _visit(self):
        self.visits += 1
        require(self.visits <= 5000 and time.monotonic() < self.deadline)

    def _save(self, kind, requested, raw):
        require(_hash(kind, raw) == requested)
        self.cache_bytes += len(raw)
        require(self.cache_bytes <= 20 * 1024 * 1024)
        self.cache[(kind, requested)] = raw
        return raw

    def _commit_raw(self, requested):
        self._visit()
        requested = _oid(requested)
        if ("commit", requested) in self.cache:
            return self.cache[("commit", requested)]
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
                        return self._save("commit", requested, candidate)
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
                    return self._save("commit", requested, raw)
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
            return self.cache[("entries", requested)]
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
            return raw
        data = self.api.git_blob(requested)
        require(data["sha"] == requested and data["encoding"] == "base64"
                and type(data["size"]) is int and 0 <= data["size"] <= max_bytes
                and isinstance(data["content"], str) and len(data["content"]) <= 2 * max_bytes + 4)
        raw = base64.b64decode(data["content"].replace("\n", ""), validate=True)
        require(len(raw) == data["size"])
        return self._save("blob", requested, raw)


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
            require(len(files) == 1 and files[0]["path"].startswith("provenance/ingestion/") and not separator)
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
