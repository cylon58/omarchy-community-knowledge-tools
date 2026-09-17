#!/usr/bin/env python3
"""Bounded exact-graph postmortem for the failed native 500-record cold read.

This diagnostic reconstructs the already-recorded synthetic graph.  It does not
repeat admission, change production limits, or establish a passing growth gate.
"""
from __future__ import annotations

import argparse
import base64
from collections import Counter
import hashlib
import json
from pathlib import Path
import tempfile
import time
import traceback

from experiments.growth import gates
from experiments.growth.baseline import FIXED_NOW, _cohort


ROOT = Path(__file__).resolve().parents[2]
SOURCE_REPORT = ROOT / "experiments/growth/results/native-distributed-500-v1.json"
PROFILE = "native-distributed-500-cold-postmortem"
SCHEMA_VERSION = 1
MAX_SOURCE_BYTES = 1024 * 1024
MAX_IMPORTS = 100
MAX_RECORDS = 500
OVERALL_SECONDS = 600
SOURCES = ("experiments/growth/cold_postmortem.py",
           "experiments/growth/baseline.py", *gates.MEASURED_SOURCES)


class IdentityMismatch(ValueError):
    """The reconstructed graph is not the graph recorded by the source report."""


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _source_hashes() -> dict[str, str]:
    return {path: _sha256((ROOT / path).read_bytes()) for path in SOURCES}


def _parse_source_report(raw: bytes) -> dict:
    from omarchy_knowledge.github_native import NativeUnavailable, strict_json

    if not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_SOURCE_BYTES:
        raise ValueError("Invalid source report")
    try:
        value = strict_json(raw)
    except (NativeUnavailable, TypeError, ValueError, RecursionError) as exc:
        raise ValueError("Invalid source report") from exc
    if not isinstance(value, dict):
        raise ValueError("Invalid source report")
    return value


def _load_source_report(path: Path) -> tuple[dict, str]:
    from omarchy_knowledge.snapshots import _read_regular

    raw = _read_regular(Path(path), MAX_SOURCE_BYTES)
    return _validate_source_report(_parse_source_report(raw)), _sha256(raw)


def _validate_source_report(report: dict) -> dict:
    """Accept only the saved failed distributed profile this postmortem diagnoses."""
    try:
        configuration = report["configuration"]
        counts = report["counts"]
        network = report["network"]
        imports = report["imports"]
        environment = report["environment"]
        valid = (
            report["schema_version"] == 1
            and report["profile_version"] == 1
            and report["profile"] == "distributed-500x100"
            and report["status"] == "failure"
            and report["failure_stage"] == "recovery-distribution"
            and report["failure_kind"] == "NativeUnavailable"
            and configuration["imports"] == MAX_IMPORTS
            and configuration["records"] == MAX_RECORDS
            and configuration["overall_seconds"] == 3600
            and configuration["phase_seconds"] == 120
            and counts["imports_requested"] == MAX_IMPORTS
            and counts["imports_completed"] == MAX_IMPORTS
            and counts["successful_publish_returns"] == MAX_IMPORTS
            and counts["known_mutation_acceptances"] == MAX_IMPORTS
            and counts["full_build_completions"] == MAX_IMPORTS
            and counts["records"] is None
            and counts["receipts"] is None
            and network["real_network_requests"] == 0
            and network["contract_violations"] == 0
            and network["per_invocation_call_cap"] == 512
            and network["per_invocation_response_byte_cap"] == 32 * 1024 * 1024
            and isinstance(imports, list) and len(imports) == MAX_IMPORTS
            and environment["source_sha256"] == {
                path: _sha256((ROOT / path).read_bytes())
                for path in gates.MEASURED_SOURCES
            }
        )
        if not valid:
            raise ValueError("Unsupported source report")
        for number, item in enumerate(imports, 1):
            if not (
                isinstance(item, dict)
                and item["number"] == number
                and item["record_count"] == 5
                and item["publish_completed_return"] is True
                and item["known_mutation_acceptance"] is True
                and item["full_build_completion"] is True
                and isinstance(item["mutations"], list)
                and len(item["mutations"]) == 2
                and set(item["jobs"]) == {"planning", "publish", "build"}
            ):
                raise ValueError("Unsupported source report")
            for job in item["jobs"].values():
                if (type(job["emulated_requests"]) is not int
                        or not 0 <= job["emulated_requests"] <= 512):
                    raise ValueError("Unsupported source report")
        completed = sum(
            job["emulated_requests"]
            for item in imports for job in item["jobs"].values()
        )
        if (type(network["emulated_https_requests"]) is not int
                or network["emulated_https_requests"] - completed != 512):
            raise ValueError("Unsupported source report")
    except (KeyError, TypeError, ValueError, IndexError) as exc:
        if isinstance(exc, ValueError) and str(exc) == "Unsupported source report":
            raise
        raise ValueError("Unsupported source report") from exc
    return report


def _record_additions(records: list[dict]) -> dict[str, bytes]:
    from omarchy_knowledge.coordinator import canonical

    return {
        f"records/{record['type']}s/{record['id']}.json": canonical(record)
        for record in records
    }


def _corpus_digest(leaves: dict[str, tuple[str, str]]) -> str:
    canonical_pairs = sorted(
        (path, value[1]) for path, value in leaves.items()
        if path.startswith(("records/", "provenance/"))
    )
    raw = json.dumps(canonical_pairs, separators=(",", ":")).encode()
    return "sha256:" + _sha256(raw)


def _require_mutation(expected: dict, actual: dict, number: int, lane: str) -> None:
    required = {"expected_head", "result_head", "headline", "addition_count", "paths"}
    if (not isinstance(expected, dict) or set(expected) != required
            or expected != actual):
        raise IdentityMismatch(
            f"Exact {lane} mutation mismatch at import {number}")


def _reconstruct(fixture: gates._NativeFixture, source: dict) -> dict:
    """Rebuild recorded commits directly and require every saved mutation identity."""
    from omarchy_knowledge.coordinator import (
        Policy, RECEIPT_MESSAGE, _message, _receipt, _receipt_path, canonical, oid,
    )

    policy = Policy("a" * 40, "b" * 40)
    result_checks = 0
    predecessor_checks = 0
    receipt_count = 0
    for number, saved in enumerate(source["imports"], 1):
        records = _cohort(number, 1)
        by_id = {record["id"]: record for record in records}
        fixture.add_candidate(number, records)
        pull = fixture.pulls[number]
        base = fixture.main
        additions = _record_additions(records)
        leaves = fixture.repository.leaves(pull["merge_commit_sha"])
        tree = fixture.repository.git(
            "rev-parse", pull["merge_commit_sha"] + "^{tree}").decode().strip()
        plan = {
            "version": 1,
            "repository_id": policy.repository_id,
            "pull_request": number,
            "actor_account_id": pull["user"]["id"],
            "head_repository_id": pull["head"]["repo"]["id"],
            "base": base,
            "head": pull["head"]["sha"],
            "evaluated": pull["merge_commit_sha"],
            "tree": tree,
            "policy_revision": policy.policy_revision,
            "toolkit_revision": policy.toolkit_revision,
            "corpus_digest": _corpus_digest(leaves),
            "additions": [
                {"path": path, "blob": oid("blob", raw),
                 "content": base64.b64encode(raw).decode()}
                for path, raw in sorted(additions.items())
            ],
        }
        imported = fixture.repository.commit(additions, (base,), _message(plan))
        import_audit = {
            "expected_head": base,
            "result_head": imported,
            "headline": "Omarchy knowledge snapshot import",
            "addition_count": len(additions),
            "paths": sorted(additions),
        }
        _require_mutation(saved["mutations"][0], import_audit, number, "import")
        predecessor_checks += 1
        result_checks += 1
        fixture.main = imported

        commit = fixture._commit(imported)
        info = {"oid": imported, "date": commit["committer"]["date"]}
        receipts = {}
        for addition in plan["additions"]:
            identifier = addition["path"].rsplit("/", 1)[1][:-5]
            record = by_id[identifier]
            receipt = _receipt(plan, info, addition, record)
            path = _receipt_path(policy.repository_id, imported, record["id"])
            receipts[path] = canonical(receipt) + b"\n"
        receipted = fixture.repository.commit(
            receipts, (imported,), RECEIPT_MESSAGE)
        receipt_audit = {
            "expected_head": imported,
            "result_head": receipted,
            "headline": RECEIPT_MESSAGE,
            "addition_count": len(receipts),
            "paths": sorted(receipts),
        }
        _require_mutation(saved["mutations"][1], receipt_audit, number, "receipt")
        predecessor_checks += 1
        result_checks += 1
        receipt_count += len(receipts)
        fixture.main = receipted

    return {
        "outcome": "success",
        "imports": len(source["imports"]),
        "records": sum(item["record_count"] for item in source["imports"]),
        "receipts": receipt_count,
        "predecessor_heads_checked": predecessor_checks,
        "mutation_heads_checked": result_checks,
        "all_recorded_mutation_identities_equal": True,
        "data_head": fixture.main,
    }


def _sanitized_trace(error: BaseException) -> list[dict]:
    terminal = error
    while terminal.__cause__ is not None:
        terminal = terminal.__cause__
    result = []
    for frame in traceback.extract_tb(terminal.__traceback__):
        path = Path(frame.filename).resolve()
        try:
            relative = path.relative_to(ROOT)
            module = ".".join(relative.with_suffix("").parts)
        except ValueError:
            module = path.stem
        result.append({"module": module, "function": frame.name, "line": frame.lineno})
    return result


def _route_counts(requests: list[dict]) -> dict[str, int]:
    result = Counter()
    for request in requests:
        path = request["path"]
        if path.endswith("/git/ref/heads/main"):
            result["branch"] += 1
        elif "/git/commits/" in path:
            result["commit"] += 1
        elif "/git/trees/" in path:
            result["tree"] += 1
        elif "/git/blobs/" in path:
            result["blob"] += 1
        elif path == "/repos/" + gates._NativeFixture.REPOSITORY:
            result["repository"] += 1
        else:
            result["other"] += 1
    return {key: result[key] for key in
            ("repository", "branch", "commit", "tree", "blob", "other")}


def _native_failure_metrics(adapter, fixture, request_start: int,
                            started: float, error: BaseException | None) -> dict:
    from omarchy_knowledge.github_native import MAX_BYTES, MAX_CALLS, TOTAL_SECONDS

    requests = fixture.requests[request_start:]
    attempted = adapter.http.calls
    sent = len(requests)
    typed_objects = {
        key for key in adapter.objects.cache
        if isinstance(key, tuple) and len(key) == 2
        and key[0] in {"commit", "tree", "blob"}
    }
    return {
        "outcome": "failure" if error is not None else "success",
        "failure_kind": type(error).__name__ if error is not None else None,
        "adapter_calls_attempted": attempted,
        "actual_emulated_requests_sent": sent,
        "refused_before_send": attempted - sent,
        "response_bytes": adapter.http.bytes,
        "actual_emulated_response_bytes": sum(row["response_bytes"] for row in requests),
        "requests_by_kind": _route_counts(requests),
        "verified_object_count": len(typed_objects),
        "verified_object_bytes": adapter.objects.cache_bytes,
        "request_cap": MAX_CALLS,
        "response_byte_cap": MAX_BYTES,
        "adapter_deadline_seconds": TOTAL_SECONDS,
        "elapsed_seconds": round(time.monotonic() - started, 6),
        "traceback": _sanitized_trace(error) if error is not None else [],
    }


def _native_cold(fixture: gates._NativeFixture, policy) -> dict:
    from omarchy_knowledge.canonical import read_canonical
    from omarchy_knowledge.github_native import GitHubRead

    adapter = GitHubRead(read_token=gates.SYNTHETIC_TOKEN,
                         connection_factory=fixture.connection_factory)
    request_start = len(fixture.requests)
    started = time.monotonic()
    error = None
    try:
        read_canonical(adapter, policy, now=FIXED_NOW)
    except (gates._DeadlineExpired, KeyboardInterrupt, SystemExit):
        raise
    except Exception as caught:
        error = caught
    return _native_failure_metrics(
        adapter, fixture, request_start, started, error)


class _LocalGitAdapter:
    """APIObjects adapter over the exact local fixture, without an HTTP call cap."""

    def __init__(self, fixture: gates._NativeFixture, policy):
        from omarchy_knowledge.github_native import APIObjects

        self.fixture = fixture
        self.policy = policy
        self.calls = Counter()
        self.response_bytes = Counter()
        self.objects = APIObjects(self)

    def repository(self):
        self.calls["repository"] += 1
        return {"id": self.policy.repository_id, "full_name": self.policy.repository,
                "default_branch": "main"}

    def branch(self):
        self.calls["branch"] += 1
        return self.fixture.main

    def _record(self, kind: str, value: dict) -> dict:
        self.calls[kind] += 1
        self.response_bytes[kind] += len(json.dumps(
            value, sort_keys=True, separators=(",", ":")).encode())
        return value

    def git_commit(self, oid: str):
        return self._record("commit", self.fixture._commit(oid))

    def git_tree(self, oid: str):
        return self._record("tree", self.fixture._tree(oid))

    def git_blob(self, oid: str):
        return self._record("blob", self.fixture._blob(oid))

    def commit_info(self, oid: str):
        return self.objects.info(oid)


def _local_replay(fixture: gates._NativeFixture, policy) -> dict:
    from omarchy_knowledge.canonical import read_canonical
    from omarchy_knowledge.service import _validated_proof

    adapter = _LocalGitAdapter(fixture, policy)
    started = time.monotonic()
    data = read_canonical(adapter, policy, now=FIXED_NOW)
    proof = _validated_proof(adapter, policy, data)
    calls = {key: adapter.calls[key] for key in
             ("repository", "branch", "commit", "tree", "blob")}
    object_calls = {key: calls[key] for key in ("commit", "tree", "blob")}
    return {
        "outcome": "success",
        "transport_scope": "local-git-object-adapter-not-native-http",
        "records": len(data["records"]),
        "receipts": len(data["receipts"]),
        "adverse_failure_records": sum(
            record["type"] == "report"
            and record["payload"]["result"] in {"failure", "partial"}
            for record in data["records"]),
        "data_head": data["source"]["data_revision"],
        "offline_proof_replay_equal": True,
        "adapter_calls": calls,
        "object_calls": object_calls,
        "object_response_bytes": sum(adapter.response_bytes.values()),
        "verified_object_count": len({
            key for key in adapter.objects.cache
            if isinstance(key, tuple) and len(key) == 2
            and key[0] in {"commit", "tree", "blob"}
        }),
        "verified_object_bytes": adapter.objects.cache_bytes,
        "object_visit_count": adapter.objects.visits,
        "object_visit_cap": 5000,
        "object_byte_cap": 20 * 1024 * 1024,
        "adapter_deadline_seconds": 180,
        "proof_bytes": len(proof),
        "proof_sha256": _sha256(proof),
        "elapsed_seconds": round(time.monotonic() - started, 6),
        "_proof": proof,
    }


def _export_postmortem_artifact(output: Path, proof: bytes, *,
                                source_report_sha256: str, data_head: str,
                                source_sha256: dict[str, str]) -> dict:
    return gates._export_artifact(Path(output), proof, {
        "profile": PROFILE,
        "purpose": "cold-recovery-postmortem",
        "original_growth_gate_status": "failure",
        "establishes_growth_gate_pass": False,
        "source_report_sha256": source_report_sha256,
        "data_head": data_head,
        "source_sha256": source_sha256,
    })


def _write_json_output(path: Path, value: dict) -> None:
    target = gates._validate_new_target(Path(path))
    raw = json.dumps(value, sort_keys=True, indent=2, allow_nan=False).encode() + b"\n"
    gates._exclusive_write(target, raw)


def run_postmortem(*, source_report: Path = SOURCE_REPORT,
                   artifact_output: Path | None = None) -> dict:
    source, source_report_sha256 = _load_source_report(Path(source_report))
    source_hashes = _source_hashes()
    completed_job_requests = sum(
        job["emulated_requests"]
        for item in source["imports"] for job in item["jobs"].values()
    )
    recorded_final_requests = (
        source["network"]["emulated_https_requests"] - completed_job_requests)
    report = {
        "schema_version": SCHEMA_VERSION,
        "profile": PROFILE,
        "status": "failure",
        "failure_stage": "configuration",
        "failure_kind": None,
        "configuration": {
            "max_imports": MAX_IMPORTS,
            "max_records": MAX_RECORDS,
            "overall_seconds": OVERALL_SECONDS,
        },
        "source_report": {
            "sha256": source_report_sha256,
            "recorded_status": source["status"],
            "recorded_failure_stage": source["failure_stage"],
            "recorded_failure_kind": source["failure_kind"],
            "recorded_total_emulated_requests": source["network"]["emulated_https_requests"],
            "completed_job_requests": completed_job_requests,
            "recorded_final_stage_requests": recorded_final_requests,
        },
        "environment": {"source_sha256": source_hashes},
        "reconstruction": {},
        "native_cold": {},
        "local_replay": {},
        "artifact": {"requested": artifact_output is not None, "exported": False},
        "conclusion": {
            "original_growth_gate_passed": False,
            "native_cold_capacity_passed": False,
            "local_replay_is_native_capacity_evidence": False,
        },
        "limitations": [
            "This reconstructs the recorded synthetic graph; it does not repeat admission.",
            "The local Git-object replay is not a native HTTP capacity test.",
            "The original growth gate remains failed and no production limit is changed or waived.",
            "No network request, credential, public contribution, or production write is used.",
        ],
    }
    stage = "fixture-reconstruction"
    started = time.monotonic()
    budget = gates._DeadlineBudget(OVERALL_SECONDS)
    fixture = None
    try:
        with gates._alarm_handler(budget), \
                tempfile.TemporaryDirectory(prefix="omarchy-cold-postmortem-") as temporary:
            fixture = gates._NativeFixture(Path(temporary))
            with fixture:
                from omarchy_knowledge.coordinator import Policy

                policy = Policy("a" * 40, "b" * 40)
                reconstruction_started = time.monotonic()
                report["reconstruction"] = _reconstruct(fixture, source)
                report["reconstruction"]["elapsed_seconds"] = round(
                    time.monotonic() - reconstruction_started, 6)

                stage = "native-cold-read"
                report["native_cold"] = _native_cold(fixture, policy)

                stage = "local-object-replay"
                replay = _local_replay(fixture, policy)
                proof = replay.pop("_proof")
                report["local_replay"] = replay
                exact_counts = replay["records"] == MAX_RECORDS and replay["receipts"] == MAX_RECORDS
                native_cap = (
                    report["native_cold"]["outcome"] == "failure"
                    and report["native_cold"]["failure_kind"] == "NativeUnavailable"
                    and report["native_cold"]["adapter_calls_attempted"] == 513
                    and report["native_cold"]["actual_emulated_requests_sent"] == 512
                    and report["native_cold"]["refused_before_send"] == 1
                )
                if not (exact_counts and native_cap
                        and report["reconstruction"]["all_recorded_mutation_identities_equal"]
                        and replay["data_head"] == report["reconstruction"]["data_head"]):
                    raise RuntimeError("Postmortem result did not satisfy its exact checks")
                report["conclusion"].update({
                    "native_failure_cause": "per-invocation-request-cap",
                    "precise_failure": (
                        "The unmodified native cold read attempted request 513 after "
                        "512 requests were sent; the adapter refused that call before fixture exchange."
                    ),
                    "recorded_request_delta_matches_reproduction": recorded_final_requests == 512,
                    "exact_500_record_500_receipt_local_proof": True,
                })

                if artifact_output is not None:
                    stage = "artifact-output"
                    manifest = _export_postmortem_artifact(
                        Path(artifact_output), proof,
                        source_report_sha256=source_report_sha256,
                        data_head=replay["data_head"], source_sha256=source_hashes)
                    report["artifact"].update({
                        "exported": True,
                        "files": ["canonical-objects.bundle", "manifest.json"],
                        "manifest_sha256": _sha256(json.dumps(
                            manifest, sort_keys=True, separators=(",", ":"),
                            allow_nan=False).encode() + b"\n"),
                    })
                report["status"] = "success"
                report["failure_stage"] = None
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException as error:
        report["status"] = "incomplete" if isinstance(error, gates._DeadlineExpired) else "failure"
        report["failure_stage"] = stage
        report["failure_kind"] = type(error).__name__
        report["failure_traceback"] = _sanitized_trace(error)
    finally:
        report["elapsed_seconds"] = round(time.monotonic() - started, 6)
        if fixture is not None:
            report["fixture"] = {
                "git_commands": fixture.repository.commands,
                "object_reader": {
                    "cached_objects": len(fixture.reader.cache),
                    "cached_object_bytes": fixture.reader.bytes,
                    "cache_hits": fixture.reader.cache_hits,
                    "object_cap": fixture.reader._max_objects,
                    "byte_cap": fixture.reader._max_bytes,
                },
            }
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-report", type=Path, default=SOURCE_REPORT)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--artifact-output", type=Path)
    arguments = parser.parse_args(argv)
    if arguments.output is not None:
        gates._validate_new_target(arguments.output)
    if arguments.artifact_output is not None:
        gates._validate_new_target(arguments.artifact_output)
    result = run_postmortem(source_report=arguments.source_report,
                            artifact_output=arguments.artifact_output)
    raw = json.dumps(result, sort_keys=True, indent=2, allow_nan=False) + "\n"
    if arguments.output is not None:
        gates._exclusive_write(arguments.output, raw.encode())
    print(raw, end="")
    return 0 if result["status"] == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
