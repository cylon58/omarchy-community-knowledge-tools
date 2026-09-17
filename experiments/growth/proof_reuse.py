#!/usr/bin/env python3
"""Small reproducible cold-versus-seeded hosted proof measurement.

All Git data and adapter calls are local. Counts model calls at the production
adapter's semantic boundaries; they are not claims about actual HTTP traffic.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time

from experiments.growth.baseline import FIXED_NOW, _FixtureRepository, _SyntheticAPI, _cohort
from omarchy_knowledge.canonical import read_canonical
from omarchy_knowledge.coordinator import Policy, prepare, publish
from omarchy_knowledge.service import _validated_proof


ROOT = Path(__file__).resolve().parents[2]
SOURCES = (
    "experiments/growth/baseline.py",
    "experiments/growth/proof_reuse.py",
    "omarchy_knowledge/github_native.py",
    "omarchy_knowledge/object_bundle.py",
    "omarchy_knowledge/service.py",
)


def _revision():
    try:
        return subprocess.run(["/usr/bin/git", "rev-parse", "HEAD"], cwd=ROOT,
                              check=True, text=True, capture_output=True).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "unavailable"


def _sha256(path):
    return hashlib.sha256((ROOT / path).read_bytes()).hexdigest()


def _stage(api, adapter_bytes, action, *, seed_requests=0, seed_bytes=0):
    calls_before = Counter(api.calls)
    raw_before = Counter(api.raw_calls)
    bytes_before = Counter(adapter_bytes)
    started = time.process_time_ns()
    value = action()
    cpu_seconds = round((time.process_time_ns() - started) / 1_000_000_000, 6)
    calls = Counter(api.calls) - calls_before
    raw_calls = Counter(api.raw_calls) - raw_before
    byte_counts = Counter(adapter_bytes) - bytes_before
    identity_requests = calls["repository"] + calls["branch"]
    object_requests = sum(raw_calls.values())
    identity_bytes = byte_counts["identity_ref"]
    object_bytes = byte_counts["object"]
    return value, {
        "modeled_seed_requests": seed_requests,
        "modeled_identity_ref_requests": identity_requests,
        "modeled_object_requests": object_requests,
        "modeled_total_requests": seed_requests + identity_requests + object_requests,
        "seed_bundle_bytes": seed_bytes,
        "modeled_identity_ref_bytes": identity_bytes,
        "modeled_object_response_bytes": object_bytes,
        "modeled_total_bytes": seed_bytes + identity_bytes + object_bytes,
        "modeled_object_requests_by_kind": dict(sorted(raw_calls.items())),
        "cpu_seconds": cpu_seconds,
    }


def _total(stages):
    numeric = ("modeled_seed_requests", "modeled_identity_ref_requests",
               "modeled_object_requests", "modeled_total_requests",
               "seed_bundle_bytes", "modeled_identity_ref_bytes",
               "modeled_object_response_bytes", "modeled_total_bytes")
    result = {key: sum(stage[key] for stage in stages.values()) for key in numeric}
    result["cpu_seconds"] = round(sum(stage["cpu_seconds"] for stage in stages.values()), 6)
    return result


def run_measurement():
    with tempfile.TemporaryDirectory(prefix="omarchy-proof-reuse-") as temporary:
        repository = _FixtureRepository(Path(temporary))
        api = _SyntheticAPI(repository)
        policy = Policy("a" * 40, "b" * 40)
        adapter_bytes = Counter()

        for name in ("repository", "branch"):
            original = getattr(api, name)
            def counted_identity(*args, _original=original, **kwargs):
                value = _original(*args, **kwargs)
                adapter_bytes["identity_ref"] += len(json.dumps(
                    value, sort_keys=True, separators=(",", ":")).encode())
                return value
            setattr(api, name, counted_identity)
        for kind in ("commit", "tree", "blob"):
            original = getattr(api, "git_" + kind)
            def counted_object(oid, *, _original=original):
                value = _original(oid)
                adapter_bytes["object"] += len(json.dumps(
                    value, sort_keys=True, separators=(",", ":")).encode())
                return value
            setattr(api, "git_" + kind, counted_object)

        api.candidate(1, _cohort(1, 1))
        if publish(api, policy, prepare(api, policy, 1)).status != "accepted":
            raise RuntimeError("Prior fixture import failed")
        api.reset_objects()
        read_canonical(api, policy, now=FIXED_NOW)
        old_proof = api.objects.export_bundle(api.base)

        api.candidate(2, _cohort(2, 1))
        if publish(api, policy, prepare(api, policy, 2)).status != "accepted":
            raise RuntimeError("Current fixture import failed")

        results = {}
        canonical = {}
        for mode in ("cold", "seeded"):
            api.reset_objects()
            stages = {}
            if mode == "seeded":
                _, stages["seed_decode"] = _stage(
                    api, adapter_bytes, lambda: api.objects.load_seed(old_proof),
                    seed_requests=1, seed_bytes=len(old_proof))
            else:
                _, stages["seed_decode"] = _stage(api, adapter_bytes, lambda: None)
            canonical[mode], stages["canonical_validation"] = _stage(
                api, adapter_bytes, lambda: read_canonical(api, policy, now=FIXED_NOW))
            proof, stages["export_and_offline_replay"] = _stage(
                api, adapter_bytes,
                lambda: _validated_proof(api, policy, canonical[mode]))
            stages["export_and_offline_replay"]["published_proof_bytes"] = len(proof)
            stages["total"] = _total({key: value for key, value in stages.items()
                                      if key != "total"})
            results[mode] = stages

        if canonical["cold"] != canonical["seeded"]:
            raise RuntimeError("Cold and seeded canonical results differ")
        return {
            "schema_version": 1,
            "status": "success",
            "configuration": {"prior_imports": 1, "current_imports": 2,
                              "reports_per_import": 1},
            "environment": {"python_version": sys.version.split()[0],
                            "code_revision": _revision(),
                            "source_sha256": {path: _sha256(path) for path in SOURCES}},
            "network": {
                "actual_requests": 0,
                "counting_contract": (
                    "Modeled total requests are seed requests plus repository/ref adapter reads "
                    "plus raw commit/tree/blob adapter reads. Byte totals are the compressed seed "
                    "bundle plus compact serialized adapter return values; they are not observed HTTP bytes."),
            },
            "fixture": {"prior_proof_bytes": len(old_proof)},
            "parity": {
                "canonical_equal": True,
                "records": len(canonical["cold"]["records"]),
                "receipts": len(canonical["cold"]["receipts"]),
                "source_equal": canonical["cold"]["source"] == canonical["seeded"]["source"],
                "adverse_failure_records": sum(
                    1 for record in canonical["cold"]["records"]
                    if record["type"] == "report" and record["payload"]["result"] == "failure"),
            },
            "results": results,
            "limitations": [
                "Small synthetic local Git fixture; no GitHub or Pages request occurred.",
                "Single-run CPU timings are descriptive, not production capacity evidence.",
                "Modeled adapter payload bytes do not include HTTP headers, TLS, latency, throttling, or retries.",
                "Existing corpus, object, history, request, byte, visit, and deadline caps remain unchanged.",
            ],
        }


def main():
    print(json.dumps(run_measurement(), sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
