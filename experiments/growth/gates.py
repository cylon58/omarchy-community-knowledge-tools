#!/usr/bin/env python3
"""Bounded growth gates through the production native GitHub adapters.

The network boundary is a strict in-process HTTPS fixture.  GitHubRead and
GitHubWriter still build, validate, count, and decode every production request.
"""
from __future__ import annotations

import argparse
import base64
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import sys
import tempfile
import time
from unittest.mock import patch

from experiments.growth.baseline import (FIXED_NOW, _FixtureRepository,
                                          _OfflineUpstream, _cohort, _record_id)


ROOT = Path(__file__).resolve().parents[2]
SYNTHETIC_TOKEN = "synthetic-gate-token"
PHASE_SECONDS = 120
WARM_SEARCH_SECONDS = 1
SCHEMA_VERSION = 1
PROFILE_VERSION = 1
PROFILES = {
    "one-import": {"overall_seconds": 120},
    "ten-imports": {"overall_seconds": 1200},
    "distributed-500x100": {"overall_seconds": 3600},
    "concentrated-100-reports": {"overall_seconds": 900},
}
MEASURED_SOURCES = (
    "experiments/growth/gates.py",
    "omarchy_knowledge/canonical.py",
    "omarchy_knowledge/coordinator.py",
    "omarchy_knowledge/discovery.py",
    "omarchy_knowledge/distribution.py",
    "omarchy_knowledge/github_native.py",
    "omarchy_knowledge/github_object_batch.py",
    "omarchy_knowledge/object_bundle.py",
    "omarchy_knowledge/service.py",
)


class _DeadlineExpired(Exception):
    """A declared phase or overall wall-clock budget expired."""

    def __init__(self, scope="overall"):
        if scope not in {"overall", "phase"}:
            raise ValueError("Invalid deadline scope")
        self.scope = scope
        super().__init__(scope)


class _DeadlineBudget:
    """One absolute workload deadline from which every phase is clamped."""

    def __init__(self, seconds: float, *, clock=time.monotonic):
        if not isinstance(seconds, (int, float)) or seconds <= 0:
            raise ValueError("Deadline must be positive")
        self.total_seconds = float(seconds)
        self._clock = clock
        self._deadline = clock() + self.total_seconds

    def current(self, phase_seconds: float = PHASE_SECONDS) -> float:
        remaining = self._deadline - self._clock()
        if remaining <= 0:
            raise _DeadlineExpired("overall")
        return min(float(phase_seconds), remaining)

    @property
    def remaining(self) -> float:
        return max(0.0, self._deadline - self._clock())


class _Alarm:
    def __init__(self):
        self.scope = "overall"

    def arm(self, seconds, scope):
        self.scope = scope
        if hasattr(signal, "SIGALRM"):
            signal.setitimer(signal.ITIMER_REAL, seconds)


@contextmanager
def _alarm_handler(budget):
    if not hasattr(signal, "SIGALRM"):
        yield _Alarm()
        return
    previous_handler = signal.getsignal(signal.SIGALRM)
    previous_timer = signal.setitimer(signal.ITIMER_REAL, 0)
    alarm = _Alarm()

    def expired(_signum, _frame):
        raise _DeadlineExpired(alarm.scope)

    signal.signal(signal.SIGALRM, expired)
    alarm.arm(budget.current(budget.total_seconds), "overall")
    try:
        yield alarm
    finally:
        signal.setitimer(signal.ITIMER_REAL, *previous_timer)
        signal.signal(signal.SIGALRM, previous_handler)


@contextmanager
def _phase_deadline(budget: _DeadlineBudget, alarm=None, seconds: float = PHASE_SECONDS):
    alarm = alarm or _Alarm()
    remaining = budget.remaining
    if remaining <= 0:
        raise _DeadlineExpired("overall")
    duration = min(float(seconds), remaining)
    alarm.arm(duration, "overall" if remaining <= seconds else "phase")
    try:
        yield
    finally:
        # Restore the current absolute overall budget, never a stale relative
        # timer captured before this phase did work.
        alarm.arm(budget.current(budget.total_seconds), "overall")


def _concentrated_records() -> list[dict]:
    first = _cohort(1, 1)
    case, change = first[:2]
    case = json.loads(json.dumps(case))
    change = json.loads(json.dumps(change))
    case["payload"]["title"] = "Synthetic concentrated evidence case"
    reports = []
    for index in range(100):
        report = json.loads(json.dumps(first[2]))
        report["id"] = _record_id(1, 0x100 + index)
        report["payload"]["result"] = "failure" if index % 4 == 0 else "success"
        report["payload"]["actual_result"] = (
            "The concentrated synthetic observation retained the failure."
            if index % 4 == 0 else
            "The concentrated synthetic observation succeeded."
        )
        reports.append(report)
    resolution = json.loads(json.dumps(first[-2]))
    resolution["payload"]["supporting_reports"] = [report["id"] for report in reports[:24]]
    dispute = json.loads(json.dumps(first[-1]))
    dispute["payload"]["supporting_reports"] = [reports[0]["id"]]
    return [case, change, *reports, resolution, dispute]


def _profile_batches(profile: str) -> list[list[dict]]:
    if profile not in PROFILES:
        raise ValueError("Unknown growth gate profile")
    if profile == "concentrated-100-reports":
        records = _concentrated_records()
        return [records[:10], *[records[10 + index * 10:20 + index * 10]
                                for index in range(9)], records[100:]]
    count = {"one-import": 1, "ten-imports": 10,
             "distributed-500x100": 100}[profile]
    return [_cohort(number, 1) for number in range(1, count + 1)]


def _git_hash(kind: str, raw: bytes) -> str:
    return hashlib.sha1(kind.encode() + b" " + str(len(raw)).encode() + b"\0" + raw).hexdigest()


class _BatchObjectReader:
    """Bounded immutable-object reader backed by one ``cat-file --batch`` child."""

    def __init__(self, git_dir: Path):
        from omarchy_knowledge.object_bundle import MAX_OBJECTS, MAX_RAW_OBJECTS
        self._max_objects = MAX_OBJECTS
        self._max_bytes = MAX_RAW_OBJECTS
        self._process = subprocess.Popen(
            ["/usr/bin/git", "--git-dir=" + str(git_dir), "cat-file", "--batch"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"}, cwd="/",
        )
        self.cache: dict[tuple[str, str], bytes] = {}
        self.cache_hits = 0
        self.bytes = 0
        self.local_subprocesses = 1

    def raw(self, kind: str, oid: str) -> bytes:
        from omarchy_knowledge.object_bundle import PER_KIND
        key = (kind, oid)
        if key in self.cache:
            self.cache_hits += 1
            return self.cache[key]
        if (kind not in PER_KIND or re.fullmatch(r"[0-9a-f]{40}", oid) is None
                or self._process.stdin is None or self._process.stdout is None):
            raise ValueError("Invalid fixture object request")
        self._process.stdin.write(oid.encode("ascii") + b"\n")
        self._process.stdin.flush()
        header = self._process.stdout.readline()
        match = re.fullmatch(rb"([0-9a-f]{40}) (commit|tree|blob) ([0-9]+)\n", header)
        if match is None:
            raise ValueError("Missing fixture object")
        actual_oid, actual_kind, size_raw = match.groups()
        size = int(size_raw)
        if (actual_oid.decode() != oid or actual_kind.decode() != kind
                or size > PER_KIND[kind] or len(self.cache) >= self._max_objects
                or self.bytes + size > self._max_bytes):
            raise ValueError("Fixture object bound or kind mismatch")
        raw = self._process.stdout.read(size)
        framing = self._process.stdout.read(1)
        if len(raw) != size or framing != b"\n" or _git_hash(kind, raw) != oid:
            raise ValueError("Fixture object framing or hash mismatch")
        self.cache[key] = raw
        self.bytes += size
        return raw

    def close(self):
        process = self._process
        if process.poll() is None:
            if process.stdin is not None:
                process.stdin.close()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        if process.stdout is not None:
            process.stdout.close()


class _Response:
    def __init__(self, status: int, raw: bytes):
        self.status = status
        self._raw = raw

    def read(self, amount: int) -> bytes:
        chunk, self._raw = self._raw[:amount], self._raw[amount:]
        return chunk


@dataclass(frozen=True)
class _PagesState:
    bundle: bytes | None
    published_imports: tuple[int, ...]


class _Connection:
    def __init__(self, fixture, host: str, timeout: float):
        self.fixture = fixture
        self.host = host
        self.timeout = timeout
        self.response = None

    def request(self, method, path, body=None, headers=None):
        self.response = self.fixture.exchange(self.host, method, path, body, headers or {})

    def getresponse(self):
        if self.response is None:
            raise AssertionError("response requested before request")
        return self.response

    def close(self):
        pass


class _NativeFixture:
    """Strict dynamic fake HTTPS server over a local immutable Git object store."""

    API_HOST = "api.github.com"
    PAGES_HOST = "cylon58.github.io"
    REPOSITORY = "cylon58/omarchy-community-knowledge"
    REPOSITORY_ID = 1373429914
    PAGES_PATH = "/omarchy-community-knowledge/canonical-objects.bundle"

    def __init__(self, root: Path):
        self.repository = _FixtureRepository(root)
        self.main = self.repository.base
        self.pages_state = _PagesState(None, ())
        self.pulls: dict[int, dict] = {}
        self.requests: list[dict] = []
        self.successful_mutations: list[dict] = []
        self.contract_violations = 0
        self.reader = _BatchObjectReader(self.repository.git_dir)

    def __enter__(self):
        return self

    def __exit__(self, _kind, _value, _traceback):
        self.reader.close()

    def connection_factory(self, host, timeout=5):
        if host not in {self.API_HOST, self.PAGES_HOST}:
            self.contract_violations += 1
            raise AssertionError("Unexpected HTTPS host")
        if not isinstance(timeout, (int, float)) or not 0 < timeout <= 5:
            self.contract_violations += 1
            raise AssertionError("Unexpected HTTPS timeout")
        return _Connection(self, host, timeout)

    def add_candidate(self, number: int, records: list[dict]):
        additions = {
            f"records/{record['type']}s/{record['id']}.json":
                json.dumps(record, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
            for record in records
        }
        base = self.main
        head = self.repository.commit(additions, (base,), f"Synthetic native candidate {number}")
        merge = self.repository.merge(base, head)
        self.pulls[number] = {
            "number": number, "state": "open", "draft": False, "merged": False,
            "user": {"id": 100001 + ((number - 1) % 7), "type": "User"},
            "base": {"sha": base, "ref": "main", "repo": self._repository_value()},
            "head": {"sha": head, "repo": {"id": 900000 + number}},
            "merge_commit_sha": merge,
        }

    def advance_main_for_test(self):
        self.main = self.repository.commit({}, (self.main,), "Synthetic competing update")

    @property
    def pages_bundle(self):
        return self.pages_state.bundle

    @property
    def pages_publications(self):
        return len(self.pages_state.published_imports)

    def publish_pages(self, proof: bytes, import_number: int):
        previous = self.pages_state
        if (not isinstance(proof, bytes) or not proof
                or type(import_number) is not int or import_number <= 0
                or import_number in previous.published_imports):
            raise ValueError("Invalid proof publication")
        # Bundle and publication identity/history become visible together.
        self.pages_state = _PagesState(
            proof, previous.published_imports + (import_number,))

    def _repository_value(self):
        return {"id": self.REPOSITORY_ID, "full_name": self.REPOSITORY,
                "default_branch": "main"}

    @staticmethod
    def _identity(raw: bytes, label: bytes):
        line = next(row for row in raw.partition(b"\n\n")[0].splitlines()
                    if row.startswith(label + b" "))
        match = re.fullmatch(label + rb" (.*) <([^<>]*)> ([0-9]+) ([+-][0-9]{4})", line)
        if match is None:
            raise ValueError("Unsupported fixture identity")
        stamp = datetime.fromtimestamp(int(match.group(3)), timezone.utc)
        return {"name": match.group(1).decode(), "email": match.group(2).decode(),
                "date": stamp.isoformat().replace("+00:00", "Z")}

    def _commit(self, oid: str):
        raw = self.reader.raw("commit", oid)
        headers, _, message = raw.partition(b"\n\n")
        lines = headers.splitlines()
        return {"sha": oid, "tree": {"sha": lines[0][5:].decode()},
                "parents": [{"sha": row[7:].decode()} for row in lines
                            if row.startswith(b"parent ")],
                "author": self._identity(raw, b"author"),
                "committer": self._identity(raw, b"committer"),
                "message": message.decode().rstrip("\n"),
                "verification": {"payload": None, "signature": None}}

    def _tree(self, oid: str):
        raw = self.reader.raw("tree", oid)
        rows, offset = [], 0
        while offset < len(raw):
            space = raw.index(b" ", offset)
            nul = raw.index(b"\0", space)
            mode = raw[offset:space].decode()
            name = raw[space + 1:nul].decode()
            child = raw[nul + 1:nul + 21].hex()
            kind = "tree" if mode == "40000" else "blob"
            item = {"path": name, "mode": "040000" if kind == "tree" else mode,
                    "type": kind, "sha": child}
            if kind == "blob":
                item["size"] = len(self.reader.raw("blob", child))
            rows.append(item)
            offset = nul + 21
        return {"sha": oid, "truncated": False, "tree": rows}

    def _blob(self, oid: str):
        raw = self.reader.raw("blob", oid)
        return {"sha": oid, "encoding": "base64", "size": len(raw),
                "content": base64.b64encode(raw).decode()}

    def _read_query(self, body: bytes):
        """Decode only the production read grammar and render from raw Git bytes."""
        def pairs(items):
            result = {}
            for key, item in items:
                if key in result:
                    raise AssertionError("Duplicate GraphQL request field")
                result[key] = item
            return result
        try:
            value = json.loads(
                body, object_pairs_hook=pairs,
                parse_constant=lambda _value: (_ for _ in ()).throw(
                    AssertionError("Invalid GraphQL request value")),
            )
        except (TypeError, ValueError, UnicodeDecodeError) as exc:
            raise AssertionError("Invalid GraphQL read JSON") from exc
        if (not isinstance(value, dict) or set(value) != {"query", "variables"}
                or not isinstance(value["query"], str)
                or not isinstance(value["variables"], dict)):
            raise AssertionError("Unexpected GraphQL read request shape")
        variables = value["variables"]
        count = len(variables)
        expected_names = [f"o{index}" for index in range(count)]
        if (not 1 <= count <= 32 or list(variables) != expected_names
                or any(re.fullmatch(r"[0-9a-f]{40}", oid) is None
                       for oid in variables.values())):
            raise AssertionError("Unexpected GraphQL read variables")
        declarations = ",".join(f"$o{index}:GitObjectID!" for index in range(count))
        if "...on Tree" in value["query"]:
            kind = "tree"
            if count > 16:
                raise AssertionError("Oversized GraphQL tree batch")
            selection = "...on Tree{entries{nameRaw mode type oid size}}"
        elif "...on Blob" in value["query"]:
            kind = "blob"
            selection = "...on Blob{byteSize isBinary isTruncated text}"
        else:
            raise AssertionError("Unexpected GraphQL read selection")
        objects = "".join(
            f"o{index}:object(oid:$o{index}){{__typename oid {selection}}}"
            for index in range(count)
        )
        expected = (
            f'query({declarations}){{repository(owner:"cylon58",'
            f'name:"omarchy-community-knowledge"){{databaseId {objects}}}'
            'rateLimit{cost remaining}}'
        )
        if value["query"] != expected:
            raise AssertionError("Unexpected GraphQL read grammar")

        repository = {"databaseId": self.REPOSITORY_ID}
        for index, oid in enumerate(variables.values()):
            raw = self.reader.raw(kind, oid)
            if kind == "blob":
                try:
                    text = raw.decode("utf-8")
                except UnicodeDecodeError:
                    text = None
                repository[f"o{index}"] = {
                    "__typename": "Blob", "oid": oid, "byteSize": len(raw),
                    "isBinary": text is None, "isTruncated": False, "text": text,
                }
                continue
            rows, offset = [], 0
            while offset < len(raw):
                space = raw.index(b" ", offset)
                nul = raw.index(b"\0", space + 1)
                mode_raw = raw[offset:space]
                name = raw[space + 1:nul]
                child = raw[nul + 1:nul + 21].hex()
                kinds = {b"40000": "tree", b"100644": "blob", b"100755": "blob",
                         b"120000": "blob", b"160000": "commit"}
                if mode_raw not in kinds:
                    raise AssertionError("Unsupported fixture tree mode")
                child_kind = kinds[mode_raw]
                rows.append({
                    "nameRaw": base64.b64encode(name).decode(),
                    "mode": int(mode_raw, 8), "type": child_kind, "oid": child,
                    "size": len(self.reader.raw("blob", child))
                            if child_kind == "blob" else 0,
                })
                offset = nul + 21
            repository[f"o{index}"] = {
                "__typename": "Tree", "oid": oid, "entries": rows,
            }
        return ({"data": {"repository": repository,
                          "rateLimit": {"cost": 1, "remaining": 5000}}},
                {"object_kind": kind, "object_count": count})

    def _mutation(self, body: bytes):
        from omarchy_knowledge.github_native import strict_json
        value = strict_json(body)
        expected_query = ("mutation($input:CreateCommitOnBranchInput!){"
                          "createCommitOnBranch(input:$input){commit{oid}}}")
        if (not isinstance(value, dict) or set(value) != {"query", "variables"}
                or value["query"] != expected_query
                or not isinstance(value["variables"], dict)
                or set(value["variables"]) != {"input"}):
            raise AssertionError("Unexpected GraphQL request shape")
        data = value["variables"]["input"]
        if set(data) != {"branch", "expectedHeadOid", "message", "fileChanges"}:
            raise AssertionError("Unexpected GraphQL input")
        if data["branch"] != {"repositoryNameWithOwner": self.REPOSITORY,
                              "branchName": "main"}:
            raise AssertionError("Unexpected GraphQL branch")
        expected = data["expectedHeadOid"]
        if expected != self.main:
            return {"errors": [{"type": "EXPECTED_HEAD_OID_MISMATCH"}]}
        additions = data["fileChanges"].get("additions")
        if not isinstance(additions, list) or not 1 <= len(additions) <= 10:
            raise AssertionError("Unexpected mutation addition count")
        message_value = data["message"]
        headline = message_value.get("headline") if isinstance(message_value, dict) else None
        if headline == "Record source-bound ingestion receipt":
            if set(message_value) != {"headline"}:
                raise AssertionError("Receipt mutation has a body")
            prefix = "provenance/ingestion/"
            message = headline
        elif headline == "Omarchy knowledge snapshot import":
            if set(message_value) != {"headline", "body"}:
                raise AssertionError("Import mutation lacks a body")
            prefix = "records/"
            message = headline + "\n\n" + message_value["body"]
        else:
            raise AssertionError("Unexpected mutation lane")
        decoded = {}
        paths = []
        for item in additions:
            if not isinstance(item, dict) or set(item) != {"path", "contents"}:
                raise AssertionError("Unexpected mutation addition")
            path = item["path"]
            if not isinstance(path, str) or not path.startswith(prefix) or path in decoded:
                raise AssertionError("Mixed or duplicate mutation lane")
            try:
                raw = base64.b64decode(item["contents"], validate=True)
            except (TypeError, ValueError) as exc:
                raise AssertionError("Invalid mutation base64") from exc
            if len(raw) > 65536:
                raise AssertionError("Mutation object too large")
            decoded[path] = raw
            paths.append(path)
        if paths != sorted(paths):
            raise AssertionError("Mutation additions are not sorted")
        committed = self.repository.commit(decoded, (self.main,), message)
        self.main = committed
        audit = {"expected_head": expected, "result_head": committed,
                 "headline": headline, "addition_count": len(decoded),
                 "paths": paths}
        self.successful_mutations.append(audit)
        return {"data": {"createCommitOnBranch": {"commit": {"oid": committed}}}}

    def exchange(self, host, method, path, body, headers):
        try:
            if host == self.PAGES_HOST:
                if (method != "GET" or path != self.PAGES_PATH or body is not None
                        or headers != {"Accept": "application/octet-stream",
                                       "User-Agent": "omarchy-knowledge-native/1"}):
                    raise AssertionError("Unexpected Pages request")
                pages = self.pages_state
                self.requests.append({"host": host, "method": method, "path": path,
                                      "request_bytes": 0,
                                      "response_bytes": len(pages.bundle or b"")})
                return _Response(200 if pages.bundle is not None else 404,
                                 pages.bundle or b"")
            expected_headers = {
                "Accept": "application/vnd.github+json",
                "User-Agent": "omarchy-knowledge-native/1",
                "X-GitHub-Api-Version": "2022-11-28",
                "Authorization": "Bearer " + SYNTHETIC_TOKEN,
            }
            if method == "POST":
                expected_headers["Content-Type"] = "application/json"
            if headers != expected_headers:
                raise AssertionError("Unexpected API headers")
            if isinstance(body, bytes) and SYNTHETIC_TOKEN.encode() in body:
                raise AssertionError("Synthetic token leaked into request body")
            prefix = "/repos/" + self.REPOSITORY
            metadata = {}
            if method == "POST" and path == "/graphql" and isinstance(body, bytes):
                if b'"query":"query(' in body:
                    value, metadata = self._read_query(body)
                else:
                    value = self._mutation(body)
            elif method == "GET" and body is None and path == prefix:
                value = self._repository_value()
            elif method == "GET" and body is None and path == prefix + "/git/ref/heads/main":
                value = {"ref": "refs/heads/main",
                         "object": {"type": "commit", "sha": self.main}}
            elif method == "GET" and body is None and re.fullmatch(
                    re.escape(prefix) + r"/pulls/[1-9][0-9]{0,9}", path):
                number = int(path.rsplit("/", 1)[1])
                value = self.pulls[number]
            elif method == "GET" and body is None and re.fullmatch(
                    re.escape(prefix) + r"/git/commits/[0-9a-f]{40}", path):
                value = self._commit(path.rsplit("/", 1)[1])
            elif method == "GET" and body is None and re.fullmatch(
                    re.escape(prefix) + r"/git/trees/[0-9a-f]{40}", path):
                value = self._tree(path.rsplit("/", 1)[1])
            elif method == "GET" and body is None and re.fullmatch(
                    re.escape(prefix) + r"/git/blobs/[0-9a-f]{40}", path):
                value = self._blob(path.rsplit("/", 1)[1])
            else:
                raise AssertionError("Unexpected API method or route")
            raw = json.dumps(value, sort_keys=True, separators=(",", ":"),
                             allow_nan=False).encode()
            self.requests.append({"host": host, "method": method, "path": path,
                                  "request_bytes": len(body or b""),
                                  "response_bytes": len(raw), **metadata})
            return _Response(200, raw)
        except AssertionError:
            self.contract_violations += 1
            raise


def _adapter_metrics(adapter, *, seed_outcome, elapsed, completed_return,
                     request_start, fixture: _NativeFixture):
    requests = fixture.requests[request_start:]
    return {
        "seed_outcome": seed_outcome,
        "calls": adapter.http.calls,
        "response_bytes": adapter.http.bytes,
        "elapsed_seconds": round(elapsed, 6),
        "elapsed_scope": "seed-plus-production-wrapper",
        "completed_return": completed_return,
        "emulated_requests": len(requests),
        "graphql_calls": adapter.http.graphql_calls,
        "graphql_points": adapter.http.graphql_points,
        "graphql_remaining": adapter.http.graphql_remaining,
        "methods": {method: sum(row["method"] == method for row in requests)
                    for method in ("GET", "POST")},
    }


def _run_job(fixture, adapter, action):
    request_start = len(fixture.requests)
    started = time.monotonic()
    seed = adapter.seed_canonical()
    completed = False
    try:
        value = action(adapter)
        completed = True
        return value, _adapter_metrics(
            adapter, seed_outcome=seed, elapsed=time.monotonic() - started,
            completed_return=completed, request_start=request_start, fixture=fixture)
    except BaseException as error:
        error._growth_job_metrics = _adapter_metrics(  # type: ignore[attr-defined]
            adapter, seed_outcome=seed, elapsed=time.monotonic() - started,
            completed_return=completed, request_start=request_start, fixture=fixture)
        raise


def _source_hashes():
    return {path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest()
            for path in MEASURED_SOURCES}


def _revision():
    try:
        return subprocess.run(["/usr/bin/git", "rev-parse", "HEAD"], cwd=ROOT,
                              check=True, text=True, capture_output=True).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "unavailable"


def _dirty_state():
    try:
        rows = subprocess.run(["/usr/bin/git", "status", "--porcelain"], cwd=ROOT,
                              check=True, text=True, capture_output=True).stdout.splitlines()
        return {"dirty": bool(rows), "paths": [row[3:] for row in rows]}
    except (OSError, subprocess.SubprocessError):
        return {"dirty": None, "paths": []}


def _exclusive_write(path: Path, raw: bytes):
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        os.close(descriptor)


def _validate_new_target(path: Path):
    path = Path(path)
    try:
        os.lstat(path)
    except FileNotFoundError:
        pass
    else:
        raise FileExistsError("Output target already exists")
    parent_status = os.stat(path.parent, follow_symlinks=False)
    if not stat.S_ISDIR(parent_status.st_mode):
        raise ValueError("Output parent must be a real directory")
    return path


def _export_artifact(output: Path, proof: bytes, manifest: dict):
    """Exclusively create a private proof directory; manifest is the completion marker.

    A failed write deliberately leaves the new partial directory for diagnosis. It
    is never mistaken for complete because ``manifest.json`` is written last.
    """
    from omarchy_knowledge.object_bundle import MAX_COMPRESSED_BUNDLE
    output = _validate_new_target(output)
    if (not isinstance(proof, bytes) or not 0 < len(proof) <= MAX_COMPRESSED_BUNDLE
            or not isinstance(manifest, dict)):
        raise ValueError("Invalid retained proof artifact")
    os.mkdir(output, 0o700)
    _exclusive_write(output / "canonical-objects.bundle", proof)
    completed = {"schema_version": SCHEMA_VERSION, **manifest,
                 "proof": {"bytes": len(proof),
                           "sha256": hashlib.sha256(proof).hexdigest()}}
    _exclusive_write(output / "manifest.json",
                     json.dumps(completed, sort_keys=True, separators=(",", ":"),
                                allow_nan=False).encode() + b"\n")
    return completed


def _shell(profile: str, batches: list[list[dict]]):
    return {
        "schema_version": SCHEMA_VERSION,
        "profile_version": PROFILE_VERSION,
        "profile": profile,
        "status": "failure",
        "failure_stage": "configuration",
        "failure_kind": None,
        "deadline_scope": None,
        "configuration": {
            "overall_seconds": PROFILES[profile]["overall_seconds"],
            "phase_seconds": PHASE_SECONDS,
            "warm_search_seconds": WARM_SEARCH_SECONDS,
            "imports": len(batches),
            "records": sum(map(len, batches)),
        },
        "environment": {
            "python_version": sys.version.split()[0],
            "source_revision": _revision(),
            "source_sha256": _source_hashes(),
            "working_tree": _dirty_state(),
        },
        "counts": {"imports_requested": len(batches), "imports_completed": 0,
                   "successful_publish_returns": 0,
                   "known_mutation_acceptances": 0,
                   "full_build_completions": 0,
                   "records": None, "receipts": None},
        "imports": [],
        "timings_seconds": {},
        "network": {"real_network_requests": 0, "emulated_https_requests": 0,
                    "emulated_response_bytes": 0, "contract_violations": 0,
                    "per_invocation_call_cap": 512,
                    "per_invocation_response_byte_cap": 32 * 1024 * 1024},
        "fixture": {"initial_setup_seconds": None,
                    "candidate_preparation_seconds": 0},
        "proof": {"published_after_complete_build": False, "pages_publications": 0,
                  "published_imports": [],
                  "cold_warm_canonical_equal": None, "cold_warm_proof_equal": None,
                  "source_equal": None, "adverse_failure_records": None},
        "recovery": {},
        "search": {"query_set_version": 1, "cold_warm_equal": None,
                   "cold_cache_scope": "fresh-per-query", "queries": [],
                   "worst_warm_seconds": None,
                   "warm_gate_seconds": WARM_SEARCH_SECONDS,
                   "status_evaluation_at": FIXED_NOW},
        "artifact": {"requested": False, "exported": False,
                     "export_seconds": None},
        "limitations": [
            "All repositories, pull requests, accounts, records, and observations are synthetic fixtures.",
            "No real network request, credential, pull request, write, or deployment is used.",
            "The strict HTTPS fixture measures production adapter request/response accounting, not wire bytes.",
            "Provider latency, hourly quotas, scheduler throughput, fairness, and outages are not measured.",
            "The CLI/event environment layer and independent community reproduction remain unmeasured.",
            "Fixture preparation and local Git object-serving work are reported outside admission timing.",
            "Search freshness is evaluated at one synthetic fixture time; elapsed and deadline clocks remain live.",
        ],
    }


def _refresh_offline(data):
    from omarchy_knowledge.resolution import refresh_upstream
    data["upstream"] = refresh_upstream(
        data["records"], github=_OfflineUpstream(), catalogs=_OfflineUpstream(),
        now=datetime(2026, 9, 16, 16, tzinfo=timezone.utc))
    return data


def _install_cache(data, cache: Path):
    from omarchy_knowledge.canonical import snapshot_data
    from omarchy_knowledge.snapshots import _import_snapshot
    with tempfile.TemporaryDirectory(prefix="omarchy-native-snapshot-") as temporary:
        snapshot = Path(temporary) / "snapshot"
        snapshot_data(data, snapshot)
        _import_snapshot(snapshot, cache,
                         canonical={key: value for key, value in data.items()
                                    if key != "records"})


def _percentile(values: list[float], fraction: float):
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int((len(ordered) - 1) * fraction + .999999)))
    return round(ordered[index], 6)


def _query_specs(profile: str, batches: list[list[dict]]):
    cases = [record for batch in batches for record in batch if record["type"] == "case"]
    exact = cases[-1]["payload"]["title"]
    return [
        {"name": "exact-case", "query": exact, "method": "ranked", "broad": False},
        {"name": "symptom-domain", "query": "synthetic input dock",
         "method": "ranked", "broad": True},
        {"name": "no-hit", "query": "unrepresented quantum printer",
         "method": "substring", "broad": False},
    ]


def run_gate(profile: str, *, artifact_output: Path | None = None):
    """Run one declared native-boundary profile and return a JSON-safe report."""
    if profile not in PROFILES:
        raise ValueError("Unknown growth gate profile")
    batches = _profile_batches(profile)
    report = _shell(profile, batches)
    started = time.monotonic()
    budget = _DeadlineBudget(PROFILES[profile]["overall_seconds"])
    stage = "initial-fixture"
    fixture = None
    fixture_setup_started = time.monotonic()
    final_proof = None
    final_revision = None
    report["artifact"]["requested"] = artifact_output is not None
    try:
        with _alarm_handler(budget) as alarm, \
                tempfile.TemporaryDirectory(prefix="omarchy-native-gate-") as temporary:
            root = Path(temporary)
            fixture = _NativeFixture(root)
            report["fixture"]["initial_setup_seconds"] = round(
                time.monotonic() - fixture_setup_started, 6)
            with fixture:
                from omarchy_knowledge.canonical import read_canonical
                from omarchy_knowledge.coordinator import Policy, canonical
                from omarchy_knowledge.distribution import build_site
                from omarchy_knowledge.github_native import GitHubRead, GitHubWriter, strict_json
                from omarchy_knowledge.service import (_validated_proof, plan_run,
                                                       publish_run)

                policy = Policy("a" * 40, "b" * 40)
                for number, records in enumerate(batches, 1):
                    item = {"number": number, "record_count": len(records),
                            "candidate_preparation_seconds": None,
                            "admission_seconds": None, "plan_artifact_bytes": None,
                            "publish_completed_return": False,
                            "known_mutation_acceptance": False,
                            "full_build_completion": False,
                            "jobs": {}, "mutations": []}
                    report["imports"].append(item)
                    fixture_started = time.monotonic()
                    fixture.add_candidate(number, records)
                    item["candidate_preparation_seconds"] = round(
                        time.monotonic() - fixture_started, 6)
                    mutation_start = len(fixture.successful_mutations)
                    run_id, run_attempt = number, 1
                    try:
                        # Planning and admission share one 120-second wall gate;
                        # individual elapsed metrics do not reset it.
                        stage = "planning"
                        with _phase_deadline(budget, alarm):
                            planning = GitHubRead(
                                read_token=SYNTHETIC_TOKEN,
                                connection_factory=fixture.connection_factory)

                            def planning_job(api):
                                value = plan_run(
                                    api, policy, pull_request=number,
                                    run_number=number - 1, run_id=run_id,
                                    run_attempt=run_attempt)
                                raw = canonical(value) + b"\n"
                                return strict_json(raw), len(raw)

                            planned, item["jobs"]["planning"] = _run_job(
                                fixture, planning, planning_job)
                            batch, item["plan_artifact_bytes"] = planned
                            if batch["status"]["status"] != "planned" or batch["plan"] is None:
                                raise RuntimeError("Synthetic native import was not planned")

                            stage = "publish"
                            writer = GitHubWriter(
                                SYNTHETIC_TOKEN,
                                connection_factory=fixture.connection_factory)
                            publish_status, item["jobs"]["publish"] = _run_job(
                                fixture, writer,
                                lambda api: publish_run(
                                    api, policy, batch, run_id=run_id,
                                    run_attempt=run_attempt))
                            if publish_status["status"] != "accepted":
                                raise RuntimeError("Synthetic native import was not accepted")
                            item["publish_completed_return"] = True

                        stage = "build"
                        with _phase_deadline(budget, alarm):
                            builder = GitHubRead(
                                read_token=SYNTHETIC_TOKEN,
                                connection_factory=fixture.connection_factory)

                            def build(api):
                                current = read_canonical(api, policy, now=FIXED_NOW)
                                proof = _validated_proof(api, policy, current)
                                data = _refresh_offline(current)
                                site = root / "sites" / str(number)
                                distribution = build_site(
                                    data, site, status=publish_status,
                                    proof_bundle=proof)
                                return current, proof, distribution

                            built, item["jobs"]["build"] = _run_job(
                                fixture, builder, build)
                            current, proof, distribution = built
                            # Pages changes only after the canonical read, offline
                            # replay, refresh, and full static build return.
                            item["distribution_bytes"] = distribution["bytes"]
                            fixture.publish_pages(proof, number)
                    finally:
                        item["mutations"] = fixture.successful_mutations[mutation_start:]
                        item["known_mutation_acceptance"] = any(
                            row["headline"] == "Omarchy knowledge snapshot import"
                            for row in item["mutations"])
                        item["full_build_completion"] = (
                            number in fixture.pages_state.published_imports)
                        if {"planning", "publish"} <= set(item["jobs"]):
                            item["admission_seconds"] = round(sum(
                                item["jobs"][name]["elapsed_seconds"]
                                for name in ("planning", "publish")), 6)

                stage = "recovery-distribution"
                recovery_started = time.monotonic()
                with _phase_deadline(budget, alarm):
                    cold = GitHubRead(read_token=SYNTHETIC_TOKEN,
                                      connection_factory=fixture.connection_factory)
                    cold_request_start = len(fixture.requests)
                    cold_started = time.monotonic()
                    cold_data = read_canonical(cold, policy, now=FIXED_NOW)
                    cold_proof = _validated_proof(cold, policy, cold_data)
                    cold_seconds = time.monotonic() - cold_started
                    report["recovery"]["cold"] = _adapter_metrics(
                        cold, seed_outcome=None, elapsed=cold_seconds,
                        completed_return=True, request_start=cold_request_start,
                        fixture=fixture)

                    warm = GitHubRead(read_token=SYNTHETIC_TOKEN,
                                      connection_factory=fixture.connection_factory)
                    warm_request_start = len(fixture.requests)
                    warm_started = time.monotonic()
                    warm_seed = warm.seed_canonical()
                    warm_data = read_canonical(warm, policy, now=FIXED_NOW)
                    warm_proof = _validated_proof(warm, policy, warm_data)
                    warm_proof_seconds = time.monotonic() - warm_started
                    report["recovery"]["warm"] = _adapter_metrics(
                        warm, seed_outcome=warm_seed, elapsed=warm_proof_seconds,
                        completed_return=True, request_start=warm_request_start,
                        fixture=fixture)
                    if not warm_seed:
                        raise RuntimeError("Published proof was not reusable")

                    comparable = lambda value: {**value, "receipts": sorted(
                        value["receipts"], key=lambda row: json.dumps(row, sort_keys=True))}
                    canonical_equal = comparable(cold_data) == comparable(warm_data)
                    proof_equal = cold_proof == warm_proof
                    if not canonical_equal or not proof_equal:
                        raise RuntimeError("Cold and warm canonical proof paths differ")
                    final_proof = warm_proof
                    final_revision = warm_data["source"]["data_revision"]
                    report["proof"].update({
                        "published_after_complete_build": bool(
                            report["proof"]["published_imports"]),
                        "pages_publications": fixture.pages_publications,
                        "cold_warm_canonical_equal": canonical_equal,
                        "cold_warm_proof_equal": proof_equal,
                        "source_equal": cold_data["source"] == warm_data["source"],
                        "adverse_failure_records": sum(
                            record["type"] == "report"
                            and record["payload"]["result"] in {"failure", "partial"}
                            for record in cold_data["records"]),
                        "cold_seconds": round(cold_seconds, 6),
                        "warm_seconds": round(warm_proof_seconds, 6),
                        "bytes": len(warm_proof),
                    })

                    cached_data = _refresh_offline(warm_data)
                    from omarchy_knowledge import discovery
                    fixed_status_now = datetime.fromisoformat(
                        FIXED_NOW.replace("Z", "+00:00"))
                    real_snapshot_status = discovery.snapshot_status

                    def fixed_snapshot_status(snapshot, *, now=None,
                                              stale_after_seconds=86400):
                        return real_snapshot_status(
                            snapshot, now=fixed_status_now,
                            stale_after_seconds=stale_after_seconds)

                    query_results = []
                    with patch.object(discovery, "snapshot_status",
                                      fixed_snapshot_status):
                        for query_number, spec in enumerate(
                                _query_specs(profile, batches), 1):
                            cache = root / ("cache-" + str(query_number))
                            _install_cache(cached_data, cache)
                            query_started = time.monotonic()
                            cold_result = discovery.search_snapshot(
                                cache, spec["query"], method=spec["method"],
                                broad=spec["broad"], compact=True)
                            query_cold = time.monotonic() - query_started
                            query_started = time.monotonic()
                            warm_result = discovery.search_snapshot(
                                cache, spec["query"], method=spec["method"],
                                broad=spec["broad"], compact=True)
                            query_warm = time.monotonic() - query_started
                            query_results.append({**spec,
                                "cold_seconds": round(query_cold, 6),
                                "warm_seconds": round(query_warm, 6),
                                "cold_warm_equal": cold_result == warm_result,
                                "result_count": len(warm_result["results"]),
                                "adverse_evidence_visible": any(
                                    row["safety"]["has_failure_or_partial_reports"]
                                    for row in warm_result["results"]),
                            })
                    worst_warm = max(row["warm_seconds"] for row in query_results)
                    cold_warm_equal = all(
                        row["cold_warm_equal"] for row in query_results)
                    report["search"].update({
                        "cold_warm_equal": cold_warm_equal,
                        "queries": query_results,
                        "worst_warm_seconds": worst_warm,
                    })
                    if (not cold_warm_equal
                            or worst_warm >= WARM_SEARCH_SECONDS
                            or not query_results[0]["adverse_evidence_visible"]
                            or query_results[1]["result_count"] == 0
                            or query_results[2]["result_count"] != 0):
                        raise RuntimeError("Warm search gate failed")

                    report["counts"].update({
                        "records": len(warm_data["records"]),
                        "receipts": len(warm_data["receipts"]),
                    })
                report["timings_seconds"]["recovery-distribution"] = round(
                    time.monotonic() - recovery_started, 6)
                if artifact_output is not None:
                    stage = "artifact-output"
                    artifact_started = time.monotonic()
                    with _phase_deadline(budget, alarm):
                        _export_artifact(Path(artifact_output), final_proof, {
                            "profile": profile,
                            "profile_version": PROFILE_VERSION,
                            "source_revision": report["environment"]["source_revision"],
                            "source_sha256": report["environment"]["source_sha256"],
                            "data_revision": final_revision,
                        })
                    report["artifact"].update({
                        "exported": True,
                        "export_seconds": round(time.monotonic() - artifact_started, 6),
                        "files": ["canonical-objects.bundle", "manifest.json"],
                    })
                report["status"] = "success"
                report["failure_stage"] = None
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException as error:
        if isinstance(error, _DeadlineExpired):
            report["deadline_scope"] = error.scope
            report["status"] = "incomplete" if error.scope == "overall" else "failure"
            report["failure_kind"] = ("OverallDeadlineExpired" if error.scope == "overall"
                                      else "PhaseDeadlineExpired")
        else:
            report["status"] = "failure"
            report["failure_kind"] = type(error).__name__
        report["failure_stage"] = stage
        metrics = getattr(error, "_growth_job_metrics", None)
        if metrics is not None and report["imports"]:
            report["imports"][-1]["jobs"][stage] = metrics
    finally:
        if report["fixture"]["initial_setup_seconds"] is None:
            report["fixture"]["initial_setup_seconds"] = round(
                time.monotonic() - fixture_setup_started, 6)
        successful = sum(item["publish_completed_return"] for item in report["imports"])
        known = sum(item["known_mutation_acceptance"] for item in report["imports"])
        built = sum(item["full_build_completion"] for item in report["imports"])
        report["counts"].update({
            "imports_completed": built,
            "successful_publish_returns": successful,
            "known_mutation_acceptances": known,
            "full_build_completions": built,
        })
        report["timings_seconds"]["total"] = round(time.monotonic() - started, 6)
        admissions = [item["admission_seconds"] for item in report["imports"]
                      if item["admission_seconds"] is not None]
        report["timings_seconds"]["admission"] = {
            "worst": max(admissions) if admissions else None,
            "p50": _percentile(admissions, .50),
            "p95": _percentile(admissions, .95),
        }
        if fixture is not None:
            report["network"].update({
                "emulated_https_requests": len(fixture.requests),
                "emulated_response_bytes": sum(row["response_bytes"]
                                               for row in fixture.requests),
                "contract_violations": fixture.contract_violations,
            })
            report["fixture"].update({
                "candidate_preparation_seconds": round(sum(
                    item["candidate_preparation_seconds"] or 0
                    for item in report["imports"]), 6),
                "git_commands": fixture.repository.commands,
                "object_reader": {
                    "local_subprocesses": fixture.reader.local_subprocesses,
                    "cached_objects": len(fixture.reader.cache),
                    "cached_object_bytes": fixture.reader.bytes,
                    "cache_hits": fixture.reader.cache_hits,
                },
            })
            published = list(fixture.pages_state.published_imports)
            report["proof"].update({
                "pages_publications": len(published),
                "published_imports": published,
                "published_after_complete_build": bool(published),
            })
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("profile", choices=tuple(PROFILES))
    parser.add_argument("--output", type=Path)
    parser.add_argument("--artifact-output", type=Path,
                        help="new private directory for the final proof and completion manifest")
    arguments = parser.parse_args(argv)
    output = (_validate_new_target(arguments.output)
              if arguments.output is not None else None)
    result = run_gate(arguments.profile, artifact_output=arguments.artifact_output)
    raw = json.dumps(result, sort_keys=True, indent=2) + "\n"
    if output is not None:
        _exclusive_write(output, raw.encode())
    print(raw, end="")
    return 0 if result["status"] == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
