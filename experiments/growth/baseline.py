#!/usr/bin/env python3
"""Bounded synthetic growth baseline over the production local pipeline."""
from __future__ import annotations

import argparse
import base64
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import time


MAX_IMPORTS = 30
MAX_REPORTS_PER_CASE = 6
TIMEOUT_SECONDS = 120
FIXED_NOW = "2026-09-16T16:00:00Z"


class _DeadlineExpired(Exception):
    pass


@contextmanager
def _bounded_runtime():
    """Alarm the measured workload; cleanup and JSON reporting may add overhead."""
    previous = signal.getsignal(signal.SIGALRM)
    previous_timer = signal.setitimer(signal.ITIMER_REAL, TIMEOUT_SECONDS)

    def expired(_signum, _frame):
        raise _DeadlineExpired()

    signal.signal(signal.SIGALRM, expired)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, *previous_timer)
        signal.signal(signal.SIGALRM, previous)


@contextmanager
def _workspace():
    with tempfile.TemporaryDirectory(prefix="omarchy-growth-") as temporary:
        yield temporary


def _record_id(number: int, suffix: int) -> str:
    return f"{number:08x}-0000-4000-8000-{suffix:012x}"


def _cohort(number: int, reports_per_case: int) -> list[dict]:
    case_id = _record_id(number, 1)
    change_id = _record_id(number, 2)
    resolution_id = _record_id(number, 3)
    dispute_id = _record_id(number, 4)
    report_ids = [_record_id(number, 0x100 + index) for index in range(reports_per_case)]
    case = {
        "schema_version": 1, "id": case_id, "type": "case",
        "created_at": "2026-09-16T12:00:00Z", "provenance": {"kind": "firsthand"},
        "payload": {
            "title": f"Synthetic growth case {number:08d}", "intent": "corrective",
            "domains": ["input", "dock"],
            "expectation": {"basis": "previously-working", "text": "Synthetic input resumes."},
            "observed": "Synthetic input remains unavailable after resume.",
        },
    }
    change = {
        "schema_version": 1, "id": change_id, "type": "change",
        "created_at": "2026-09-16T12:01:00Z", "provenance": {"kind": "firsthand"},
        "payload": {
            "case_id": case_id, "intent": "corrective", "method": "workaround",
            "explanation": "A synthetic workaround used only for bounded pipeline measurement.",
            "applicability": {"requires": [{"kind": "component",
                "selector": {"kind": "hardware", "role": "host"}, "presence": True}]},
            "procedure": {"kind": "instructions", "steps": ["Inspect the synthetic fixture state."]},
            "risk": "low", "effects": "No real system is involved.",
            "rollback": "Discard the temporary fixture.",
            "validation_plan": "Evaluate the inert synthetic report.", "requires_root": "no",
            "activation": {"required": "none", "details": "Fixture only."}, "affected_files": [],
        },
    }
    reports = []
    for index, identifier in enumerate(report_ids):
        reports.append({
            "schema_version": 1, "id": identifier, "type": "report",
            "created_at": "2026-09-16T12:02:00Z", "provenance": {"kind": "firsthand"},
            "payload": {
                "case_id": case_id, "change_id": change_id, "observation_date": "2026-09-16",
                "environment": {"origin": "firsthand", "architecture": "x86_64", "channel": "stable",
                    "components": [{"alias": "host", "selector": {"kind": "hardware", "role": "host"}}],
                    "topology": {"nodes": ["host"], "edges": []}},
                "test_method": "Exercise an inert synthetic observation.", "baseline": "reproduced",
                "expected_result": "Synthetic input resumes.",
                "result": "failure" if index == 0 else "success",
                "actual_result": "The synthetic observation retained the failure." if index == 0
                                 else "The synthetic observation succeeded.",
                "limitations": "Fixture-only observation; not evidence about a real machine.",
                "provenance": "Synthetic direct-observation-shaped data.",
                "adverse_effects": "None represented.",
                "activation": {"state": "active", "details": "Fixture state only."},
                "root_cause": {"assessment": "unknown", "rationale": "No real cause was investigated."},
            },
        })
    resolution = {
        "schema_version": 1, "id": resolution_id, "type": "event",
        "created_at": "2026-09-16T12:03:00Z",
        "provenance": {"kind": "external-source", "sources": ["https://github.com/example/project/pull/42"]},
        "payload": {"event_kind": "upstream-resolution",
            "targets": [{"id": case_id, "type": "case"}, {"id": change_id, "type": "change"}],
            "reason": "A synthetic upstream resolution claim for pipeline measurement.",
            "supporting_reports": report_ids,
            "resolution": {"relevance": "claimed",
                "upstream_url": "https://github.com/example/project/pull/42",
                "fixed_in": {"any_of": [{"all_of": [{"kind": "software",
                    "selector": {"kind": "software", "component": "omarchy"}, "scheme": "arch",
                    "constraints": [{"op": ">=", "version": "4.2.0-1"}],
                    "channel": "stable", "architecture": "x86_64"}]}]}}},
    }
    dispute = {
        "schema_version": 1, "id": dispute_id, "type": "event",
        "created_at": "2026-09-16T12:04:00Z", "provenance": {"kind": "journal-import"},
        "payload": {"event_kind": "dispute",
            "reason": "Synthetic failure evidence disputes the resolution claim.",
            "supporting_reports": [report_ids[0]],
            "targets": [{"id": change_id, "type": "change"}, {"id": resolution_id, "type": "event"}],
            "relation": {"kind": "disputes", "from": {"id": change_id, "type": "change"},
                         "to": {"id": resolution_id, "type": "event"}}},
    }
    return [case, change, *reports, resolution, dispute]


class _FixtureRepository:
    """Synthetic local Git store; author metadata never denotes independent users."""

    def __init__(self, root: Path):
        self.git_dir = root / "objects.git"
        subprocess.run(["/usr/bin/git", "init", "--bare", str(self.git_dir)], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.commands = 1
        self.sequence = 0
        self.blobs: dict[bytes, str] = {}
        self.base = self.commit({}, (), "Synthetic baseline root")

    def git(self, *arguments: str, data: bytes | None = None) -> bytes:
        self.commands += 1
        environment = {**os.environ, "GIT_AUTHOR_NAME": "Synthetic Fixture",
            "GIT_AUTHOR_EMAIL": "fixture@example.invalid", "GIT_COMMITTER_NAME": "Synthetic Fixture",
            "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
            "GIT_AUTHOR_DATE": f"{1789574400 + self.sequence} +0000",
            "GIT_COMMITTER_DATE": f"{1789574400 + self.sequence} +0000"}
        return subprocess.run(["/usr/bin/git", "--git-dir=" + str(self.git_dir), *arguments],
            input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=environment, check=True).stdout

    def raw(self, kind: str, oid: str) -> bytes:
        if self.git("cat-file", "-t", oid).decode().strip() != kind:
            raise ValueError("Synthetic Git object type mismatch")
        return self.git("cat-file", kind, oid)

    def _blob(self, raw: bytes) -> str:
        if raw not in self.blobs:
            self.blobs[raw] = self.git("hash-object", "-w", "--stdin", data=raw).decode().strip()
        return self.blobs[raw]

    def leaves(self, commit: str) -> dict[str, tuple[str, str]]:
        result = {}
        for row in self.git("ls-tree", "-r", "-z", "--full-tree", commit).split(b"\0"):
            if row:
                header, path = row.split(b"\t", 1)
                mode, kind, oid = header.split()
                if kind == b"blob":
                    result[path.decode()] = (mode.decode(), oid.decode())
        return result

    def _tree(self, leaves: dict[str, tuple[str, str]]) -> str:
        nested: dict = {}
        for path, value in leaves.items():
            node = nested
            parts = path.split("/")
            for part in parts[:-1]:
                node = node.setdefault(part, {})
            node[parts[-1]] = value

        def build(node: dict) -> str:
            rows = []
            for name, value in sorted(node.items()):
                if isinstance(value, dict):
                    mode, kind, oid = "040000", "tree", build(value)
                else:
                    mode, oid = value
                    kind = "blob"
                rows.append(f"{mode} {kind} {oid}\t{name}".encode() + b"\0")
            return self.git("mktree", "-z", data=b"".join(rows)).decode().strip()
        return build(nested)

    def commit(self, additions: dict[str, bytes], parents: tuple[str, ...], message: str) -> str:
        leaves = self.leaves(parents[0]) if parents else {}
        for path, raw in additions.items():
            if path in leaves:
                raise ValueError("Synthetic fixture is append-only")
            leaves[path] = ("100644", self._blob(raw))
        tree = self._tree(leaves)
        arguments = ["commit-tree", tree]
        for parent in parents:
            arguments.extend(("-p", parent))
        self.sequence += 1
        return self.git(*arguments, data=(message.rstrip("\n") + "\n").encode()).decode().strip()

    def merge(self, base: str, head: str) -> str:
        tree = self.git("rev-parse", head + "^{tree}").decode().strip()
        self.sequence += 1
        return self.git("commit-tree", tree, "-p", base, "-p", head,
                        data=b"Synthetic merge candidate\n").decode().strip()


class _SyntheticAPI:
    """GitHub-shaped identity/object boundary backed only by the local fixture."""

    def __init__(self, repository: _FixtureRepository):
        from omarchy_knowledge.github_native import APIObjects
        self.fixture = repository
        self.base = repository.base
        self.calls = Counter()
        self.raw_calls = Counter()
        self.proof_bundle: bytes | None = None
        self.pull_number = 0
        self.head = self.base
        self.merge_commit = self.base
        self.actor_id = 100001
        self.objects = APIObjects(self)

    def reset_objects(self):
        from omarchy_knowledge.github_native import APIObjects
        self.objects = APIObjects(self)

    def candidate(self, number: int, records: list[dict]):
        additions = {f"records/{record['type']}s/{record['id']}.json":
            json.dumps(record, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
            for record in records}
        self.pull_number = number
        self.actor_id = 100001 + ((number - 1) % 7)
        self.head = self.fixture.commit(additions, (self.base,), f"Synthetic candidate {number}")
        self.merge_commit = self.fixture.merge(self.base, self.head)
        self.reset_objects()

    def repository(self):
        self.calls["repository"] += 1
        return {"id": 1373429914, "full_name": "cylon58/omarchy-community-knowledge",
                "default_branch": "main"}

    def branch(self):
        self.calls["branch"] += 1
        return self.base

    def pull(self, number):
        self.calls["pull"] += 1
        return {"number": number, "state": "open", "draft": False, "merged": False,
            "user": {"id": self.actor_id, "type": "User"},
            "base": {"sha": self.base, "ref": "main", "repo": self.repository()},
            "head": {"sha": self.head, "repo": {"id": 900000 + number}},
            "merge_commit_sha": self.merge_commit, "mergeable": True}

    def commit_info(self, oid):
        self.calls["commit_info"] += 1
        return self.objects.info(oid)

    def create_commit(self, expected_base, additions, message):
        from omarchy_knowledge.github_native import NativeUnavailable
        self.calls["create_commit"] += 1
        if self.base != expected_base:
            raise NativeUnavailable()
        self.base = self.fixture.commit(additions, (self.base,), message)
        return self.base

    def git_commit(self, oid):
        self.raw_calls["commit"] += 1
        raw = self.fixture.raw("commit", oid)
        headers, _, message = raw.partition(b"\n\n")
        lines = headers.splitlines()

        def identity(label: bytes):
            line = next(item for item in lines if item.startswith(label + b" "))
            match = re.fullmatch(label + rb" (.*) <([^<>]*)> ([0-9]+) ([+-][0-9]{4})", line)
            if match is None:
                raise ValueError("Unsupported synthetic identity")
            stamp = datetime.fromtimestamp(int(match.group(3)), timezone.utc)
            return {"name": match.group(1).decode(), "email": match.group(2).decode(),
                    "date": stamp.isoformat().replace("+00:00", "Z")}
        return {"sha": oid, "tree": {"sha": lines[0][5:].decode()},
            "parents": [{"sha": row[7:].decode()} for row in lines if row.startswith(b"parent ")],
            "author": identity(b"author"), "committer": identity(b"committer"),
            "message": message.decode().rstrip("\n"), "verification": {"payload": None, "signature": None}}

    def git_tree(self, oid):
        self.raw_calls["tree"] += 1
        raw = self.fixture.raw("tree", oid)
        rows = []
        offset = 0
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
                item["size"] = len(self.fixture.raw("blob", child))
            rows.append(item)
            offset = nul + 21
        return {"sha": oid, "truncated": False, "tree": rows}

    def git_blob(self, oid):
        self.raw_calls["blob"] += 1
        raw = self.fixture.raw("blob", oid)
        return {"sha": oid, "encoding": "base64", "size": len(raw),
                "content": base64.b64encode(raw).decode()}

    def prefill_canonical(self, revision):
        self.calls["proof_bundle_fetch"] += 1
        if self.proof_bundle is None:
            raise ValueError("Synthetic proof bundle unavailable")
        self.objects.load_bundle(self.proof_bundle, revision)


class _OfflineUpstream:
    def __getattr__(self, _name):
        def unavailable(*_args, **_kwargs):
            raise OSError("synthetic offline boundary")
        return unavailable


def _revision() -> str:
    try:
        return subprocess.run(["/usr/bin/git", "rev-parse", "HEAD"], check=True, text=True,
                              capture_output=True).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "unavailable"


def _directory_bytes(path: Path) -> int:
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def _metric_shell(imports: int, reports_per_case: int) -> dict:
    harness_sha256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    return {"schema_version": 1, "status": "failure", "failure_stage": "configuration",
        "failure_kind": None,
        "configuration": {"max_imports": MAX_IMPORTS, "workload_alarm_seconds": TIMEOUT_SECONDS,
                          "reports_per_case": reports_per_case},
        "environment": {"python_version": sys.version.split()[0], "code_revision": _revision(),
                        "harness_sha256": harness_sha256},
        "counts": {"imports_requested": imports, "imports_completed": 0, "records": None,
                   "receipts": None, "synthetic_account_ids": None},
        "timings_seconds": {}, "artifacts_bytes": {},
        "network": {"actual_requests": 0, "modeled_boundary_calls": {},
                    "modeled_raw_object_requests": {}, "modeled_boundary_calls_by_stage": {},
                    "modeled_raw_object_requests_by_stage": {}, "local_git_commands": 0},
        "final_query": None,
        "limitations": [
            "All records, accounts, Git history, and observations are synthetic fixtures.",
            "Distinct account IDs are not evidence of distinct people or machines.",
            "The GitHub identity/object boundary is modeled locally; actual network requests are zero.",
            "HTTP request/rate/response caps outside APIObjects are not exercised by the local adapter.",
            "Provider latency, throttling, concurrency, outages, and public queue behavior are not measured.",
            "Timings are single local wall-clock observations, not model/provider performance claims."]}


def run_baseline(imports, reports_per_case=1):
    """Run a bounded real admission-to-ranked-query roundtrip and return JSON-safe metrics."""
    metrics = _metric_shell(imports, reports_per_case)
    if (type(imports) is not int or not 1 <= imports <= MAX_IMPORTS
            or type(reports_per_case) is not int or not 1 <= reports_per_case <= MAX_REPORTS_PER_CASE):
        metrics["failure_kind"] = "InvalidConfiguration"
        return metrics
    started = time.monotonic()
    stage_started = started
    stage = "synthetic-imports"
    api = None
    repository = None
    completed = 0
    pipeline_complete = False
    raw_start = Counter()
    boundary_start = Counter()
    recorded_stages = set()

    def finish_stage():
        if stage in recorded_stages:
            return
        metrics["timings_seconds"][stage] = round(time.monotonic() - stage_started, 6)
        if api is not None:
            metrics["network"]["modeled_raw_object_requests_by_stage"][stage] = dict(sorted(
                (api.raw_calls - raw_start).items()))
            metrics["network"]["modeled_boundary_calls_by_stage"][stage] = dict(sorted(
                (api.calls - boundary_start).items()))
        recorded_stages.add(stage)

    def begin_stage(name):
        nonlocal stage, stage_started, raw_start, boundary_start
        finish_stage()
        stage = name
        stage_started = time.monotonic()
        raw_start = Counter(api.raw_calls) if api is not None else Counter()
        boundary_start = Counter(api.calls) if api is not None else Counter()
    try:
        with _bounded_runtime(), _workspace() as temporary:
            root = Path(temporary)
            repository = _FixtureRepository(root)
            api = _SyntheticAPI(repository)
            from omarchy_knowledge.coordinator import Policy, prepare, publish, reconcile
            policy = Policy("a" * 40, "b" * 40)
            for number in range(1, imports + 1):
                api.candidate(number, _cohort(number, reports_per_case))
                result = publish(api, policy, prepare(api, policy, number))
                if result.status != "accepted":
                    raise RuntimeError("Synthetic import was not accepted")
                completed += 1
            begin_stage("reconcile")
            api.reset_objects()
            if reconcile(api, policy).status != "complete":
                raise RuntimeError("Synthetic reconciliation did not complete")
            begin_stage("canonical-read-and-proof")
            api.reset_objects()
            from omarchy_knowledge.canonical import read_canonical
            data = read_canonical(api, policy, now=FIXED_NOW)
            proof = api.objects.export_bundle(api.base)
            begin_stage("static-distribution")
            from omarchy_knowledge.resolution import refresh_upstream
            data["upstream"] = refresh_upstream(data["records"], github=_OfflineUpstream(),
                catalogs=_OfflineUpstream(), now=datetime(2026, 9, 16, 16, tzinfo=timezone.utc))
            from omarchy_knowledge.distribution import build_site
            site = root / "site"
            distribution = build_site(data, site, status={"status": "idle", "outcomes": []},
                                      proof_bundle=proof)
            begin_stage("canonical-sync")
            api.proof_bundle = proof
            api.reset_objects()
            from unittest.mock import patch
            from omarchy_knowledge.canonical import sync
            cache = root / "cache"
            with patch("omarchy_knowledge.resolution.GitHubPublicRead", _OfflineUpstream), \
                    patch("omarchy_knowledge.resolution.CatalogPublicRead", _OfflineUpstream):
                synced = sync(api, policy, cache, now=FIXED_NOW)
            query = f"synthetic growth case {imports:08d}"
            from omarchy_knowledge import discovery
            fixed_status_now = datetime.fromisoformat(FIXED_NOW.replace("Z", "+00:00"))
            real_snapshot_status = discovery.snapshot_status

            def fixed_snapshot_status(snapshot, *, now=None,
                                      stale_after_seconds=86400):
                return real_snapshot_status(
                    snapshot, now=fixed_status_now,
                    stale_after_seconds=stale_after_seconds)

            with patch.object(discovery, "snapshot_status", fixed_snapshot_status):
                begin_stage("cold-ranked-query")
                found = discovery.search_snapshot(cache, query, method="ranked", compact=True)
                begin_stage("warm-ranked-query")
                warm = discovery.search_snapshot(cache, query, method="ranked", compact=True)
            finish_stage()
            if found != warm:
                raise RuntimeError("Cold and warm ranked results differ")

            account_ids = {receipt["actor"]["account_id"] for receipt in data["receipts"]}
            metrics["counts"] = {"imports_requested": imports, "imports_completed": completed,
                "records": len(data["records"]), "receipts": len(data["receipts"]),
                "synthetic_account_ids": len(account_ids)}
            metrics["artifacts_bytes"] = {"proof_bundle": len(proof),
                "static_distribution": distribution["bytes"],
                "records_jsonl": (site / "records.jsonl").stat().st_size,
                "canonical_cache": _directory_bytes(cache)}
            metrics["final_query"] = {"query": query,
                "case_ids": [item["case_id"] for item in found["results"]],
                "result_count": len(found["results"]), "trust": found["trust"],
                "failure_or_partial_visible": bool(found["results"]
                    and found["results"][0]["safety"]["has_failure_or_partial_reports"]),
                "community_event_review_required": bool(found["results"]
                    and found["results"][0]["safety"]["community_event_review_required"])}
            if synced["record_count"] != len(data["records"]) or synced["receipt_count"] != len(data["receipts"]):
                raise RuntimeError("Canonical sync counts differ")
            pipeline_complete = True
            begin_stage("cleanup")
        finish_stage()
        if pipeline_complete:
            metrics["status"] = "success"
            metrics["failure_stage"] = None
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException as error:
        finish_stage()
        metrics["status"] = "failure"
        metrics["failure_stage"] = stage
        metrics["failure_kind"] = "DeadlineExpired" if isinstance(error, _DeadlineExpired) else type(error).__name__
    finally:
        metrics["counts"]["imports_completed"] = completed
        if api is not None:
            metrics["network"]["modeled_boundary_calls"] = dict(sorted(api.calls.items()))
            metrics["network"]["modeled_raw_object_requests"] = dict(sorted(api.raw_calls.items()))
        if repository is not None:
            metrics["network"]["local_git_commands"] = repository.commands
        metrics["timings_seconds"]["total"] = round(time.monotonic() - started, 6)
    return metrics


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--imports", required=True, type=int,
                        help=f"synthetic accepted imports (1..{MAX_IMPORTS})")
    parser.add_argument("--reports-per-case", type=int, default=1,
                        help=f"synthetic reports per case (1..{MAX_REPORTS_PER_CASE})")
    arguments = parser.parse_args(argv)
    result = run_baseline(arguments.imports, arguments.reports_per_case)
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0 if result["status"] == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
