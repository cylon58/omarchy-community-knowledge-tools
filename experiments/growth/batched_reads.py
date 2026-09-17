#!/usr/bin/env python3
"""Bounded exact-graph comparison for authenticated GitHub object batching."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import tempfile
import time
import traceback

from experiments.growth import cold_postmortem, gates
from experiments.growth.baseline import FIXED_NOW


ROOT = Path(__file__).resolve().parents[2]
SOURCE_REPORT = ROOT / "experiments/growth/results/native-distributed-500-v1.json"
SOURCE_REPORT_SHA256 = "9a58c3bbfdc6e045e505b8e1759ba6f6d7b8943dac8a5e723079c1cff1b3f92a"
PROFILE = "native-distributed-500-batched-reads"
SCHEMA_VERSION = 1
OVERALL_SECONDS = 600
COLD_SECONDS = 120
SOURCES = (
    "experiments/growth/batched_reads.py",
    "experiments/growth/baseline.py",
    "experiments/growth/cold_postmortem.py",
    "experiments/growth/gates.py",
    "omarchy_knowledge/canonical.py",
    "omarchy_knowledge/coordinator.py",
    "omarchy_knowledge/github_native.py",
    "omarchy_knowledge/github_object_batch.py",
    "omarchy_knowledge/object_bundle.py",
    "omarchy_knowledge/service.py",
)


def _sha256(raw):
    return hashlib.sha256(raw).hexdigest()


def _source_hashes():
    return {path: _sha256((ROOT / path).read_bytes()) for path in SOURCES}


def _load_frozen_source(path):
    """Bind the immutable saved report before using its exact graph identities."""
    from omarchy_knowledge.snapshots import _read_regular

    raw = _read_regular(Path(path), cold_postmortem.MAX_SOURCE_BYTES)
    digest = _sha256(raw)
    if digest != SOURCE_REPORT_SHA256:
        raise ValueError("Unexpected source report identity")
    source = cold_postmortem._parse_source_report(raw)
    if (source.get("profile") != "distributed-500x100"
            or not isinstance(source.get("imports"), list)
            or len(source["imports"]) != 100):
        raise ValueError("Unexpected source report structure")
    return source, digest


def _sanitized_trace(error):
    result = []
    terminal = error
    while terminal.__cause__ is not None:
        terminal = terminal.__cause__
    for frame in traceback.extract_tb(terminal.__traceback__):
        path = Path(frame.filename).resolve()
        try:
            module = ".".join(path.relative_to(ROOT).with_suffix("").parts)
        except ValueError:
            module = path.stem
        result.append({"module": module, "function": frame.name, "line": frame.lineno})
    return result


def _write_json_output(path, value):
    target = gates._validate_new_target(Path(path))
    raw = json.dumps(value, sort_keys=True, indent=2, allow_nan=False).encode() + b"\n"
    gates._exclusive_write(target, raw)


def _comparable(value):
    from omarchy_knowledge.coordinator import canonical
    return {**value, "receipts": sorted(value["receipts"], key=canonical)}


def _request_counts(requests):
    counts = Counter()
    for request in requests:
        path = request["path"]
        if path == "/graphql":
            counts["graphql"] += 1
        elif path.endswith("/git/ref/heads/main"):
            counts["branch"] += 1
        elif "/git/commits/" in path:
            counts["commit"] += 1
        elif "/git/trees/" in path:
            counts["tree"] += 1
        elif "/git/blobs/" in path:
            counts["blob"] += 1
        elif path.startswith("/repos/"):
            counts["repository"] += 1
        else:
            counts["other"] += 1
    return {key: counts[key] for key in
            ("repository", "branch", "commit", "tree", "blob", "graphql", "other")}


def _cold_compare(fixture, policy):
    """Run the production cold path, proof replay, and bounded local reference."""
    from omarchy_knowledge.canonical import read_canonical
    from omarchy_knowledge.github_native import (GitHubRead, MAX_BYTES, MAX_CALLS,
                                                 MAX_GRAPHQL_CALLS,
                                                 MAX_GRAPHQL_POINTS, TOTAL_SECONDS)
    from omarchy_knowledge.service import _validated_proof

    adapter = GitHubRead(read_token=gates.SYNTHETIC_TOKEN,
                         connection_factory=fixture.connection_factory)
    request_start = len(fixture.requests)
    started = time.monotonic()
    error = None
    proof = None
    candidate = None
    reference = None
    reference_adapter = None
    try:
        candidate = read_canonical(adapter, policy, now=FIXED_NOW)
        proof = _validated_proof(adapter, policy, candidate)
        reference_adapter = cold_postmortem._LocalGitAdapter(fixture, policy)
        reference = read_canonical(reference_adapter, policy, now=FIXED_NOW)
        if _comparable(candidate) != _comparable(reference):
            raise RuntimeError("Canonical comparison failed")
    except (gates._DeadlineExpired, KeyboardInterrupt, SystemExit):
        raise
    except Exception as caught:
        error = caught
    requests = fixture.requests[request_start:]
    typed = {key for key in adapter.objects.cache
             if isinstance(key, tuple) and len(key) == 2
             and key[0] in {"commit", "tree", "blob"}}
    result = {
        "outcome": "failure" if error is not None else "success",
        "failure_kind": type(error).__name__ if error is not None else None,
        "calls": adapter.http.calls,
        "response_bytes": adapter.http.bytes,
        "graphql_calls": adapter.http.graphql_calls,
        "graphql_points": adapter.http.graphql_points,
        "graphql_remaining": adapter.http.graphql_remaining,
        "requests_by_kind": _request_counts(requests),
        "actual_emulated_requests": len(requests),
        "actual_emulated_response_bytes": sum(row["response_bytes"] for row in requests),
        "request_cap": MAX_CALLS,
        "response_byte_cap": MAX_BYTES,
        "graphql_call_cap": MAX_GRAPHQL_CALLS,
        "graphql_point_cap": MAX_GRAPHQL_POINTS,
        "adapter_deadline_seconds": TOTAL_SECONDS,
        "verified_object_count": len(typed),
        "verified_object_bytes": adapter.objects.cache_bytes,
        "object_visit_count": adapter.objects.visits,
        "object_visit_cap": 5000,
        "object_byte_cap": 20 * 1024 * 1024,
        "records": None,
        "receipts": None,
        "canonical_equal": None,
        "offline_proof_replay_equal": None,
        "proof_bytes": None,
        "proof_sha256": None,
        "reference_object_visit_count": None,
        "reference_verified_object_count": None,
        "adverse_failure_records": None,
        "elapsed_seconds": round(time.monotonic() - started, 6),
    }
    if error is not None:
        result["traceback"] = _sanitized_trace(error)
        return result
    result.update({
        "records": len(candidate["records"]),
        "receipts": len(candidate["receipts"]),
        "data_head": candidate["source"]["data_revision"],
        "canonical_equal": True,
        "offline_proof_replay_equal": True,
        "proof_bytes": len(proof),
        "proof_sha256": _sha256(proof),
        "reference_object_visit_count": reference_adapter.objects.visits,
        "reference_verified_object_count": len({
            key for key in reference_adapter.objects.cache
            if isinstance(key, tuple) and len(key) == 2
            and key[0] in {"commit", "tree", "blob"}
        }),
        "adverse_failure_records": sum(
            record["type"] == "report"
            and record["payload"]["result"] in {"failure", "partial"}
            for record in candidate["records"]),
    })
    return result


def run_comparison(*, source_report=SOURCE_REPORT):
    started = time.monotonic()
    report = {
        "schema_version": SCHEMA_VERSION,
        "profile": PROFILE,
        "status": "failure",
        "failure_stage": "configuration",
        "failure_kind": None,
        "configuration": {"records": 500, "receipts": 500,
                          "overall_seconds": OVERALL_SECONDS,
                          "cold_seconds": COLD_SECONDS},
        "source_report": {"required_sha256": SOURCE_REPORT_SHA256},
        "environment": {"source_sha256": _source_hashes(),
                        "source_revision": gates._revision(),
                        "working_tree": gates._dirty_state()},
        "reconstruction": {},
        "cold_comparison": {},
        "limitations": [
            "This reconstructs the saved synthetic graph and does not repeat admission.",
            "The strict HTTPS fixture opens no network connection and uses no real credential.",
            "This comparison is not the full admission growth gate or deployment approval.",
            "Provider latency, hourly quota sufficiency, queues, fairness, and outages are unmeasured.",
        ],
    }
    stage = "source-report"
    fixture = None
    try:
        budget = gates._DeadlineBudget(OVERALL_SECONDS)
        with gates._alarm_handler(budget) as alarm:
            source, source_digest = _load_frozen_source(Path(source_report))
            report["source_report"].update({"sha256": source_digest,
                                            "imports": len(source["imports"])})
            stage = "fixture-reconstruction"
            with tempfile.TemporaryDirectory(prefix="omarchy-batched-reads-") as temporary:
                fixture = gates._NativeFixture(Path(temporary))
                with fixture:
                    from omarchy_knowledge.coordinator import Policy
                    reconstruction_started = time.monotonic()
                    reconstructed = cold_postmortem._reconstruct(fixture, source)
                    reconstructed["elapsed_seconds"] = round(
                        time.monotonic() - reconstruction_started, 6)
                    checks = (reconstructed["predecessor_heads_checked"]
                              + reconstructed["mutation_heads_checked"])
                    reconstructed["recorded_oid_checks"] = checks
                    if (checks != 400
                            or reconstructed["predecessor_heads_checked"] != 200
                            or reconstructed["mutation_heads_checked"] != 200):
                        raise RuntimeError("Exact reconstruction checks failed")
                    report["reconstruction"] = reconstructed

                    stage = "native-cold-comparison"
                    with gates._phase_deadline(budget, alarm, COLD_SECONDS):
                        compared = _cold_compare(
                            fixture, Policy("a" * 40, "b" * 40))
                    report["cold_comparison"] = compared
                    if not (
                        compared["outcome"] == "success"
                        and compared["records"] == 500
                        and compared["receipts"] == 500
                        and compared["data_head"] == reconstructed["data_head"]
                        and compared["canonical_equal"]
                        and compared["offline_proof_replay_equal"]
                        and compared["object_visit_count"] <= 5000
                        and compared["verified_object_bytes"] <= 20 * 1024 * 1024
                        and compared["calls"] <= 512
                        and compared["graphql_calls"] <= 192
                        and compared["graphql_points"] <= 192
                        and compared["adverse_failure_records"] > 0
                    ):
                        raise RuntimeError("Cold comparison checks failed")
                    report["status"] = "success"
                    report["failure_stage"] = None
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as error:
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
                "contract_violations": fixture.contract_violations,
            }
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-report", type=Path, default=SOURCE_REPORT)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args(argv)
    if arguments.output is not None:
        gates._validate_new_target(arguments.output)
    result = run_comparison(source_report=arguments.source_report)
    raw = json.dumps(result, sort_keys=True, indent=2, allow_nan=False) + "\n"
    if arguments.output is not None:
        gates._exclusive_write(arguments.output, raw.encode())
    print(raw, end="")
    return 0 if result["status"] == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
