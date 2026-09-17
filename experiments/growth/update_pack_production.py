#!/usr/bin/env python3
"""Measure the real publisher and client update path on one reconstructed graph."""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
import time
from unittest.mock import patch

from experiments.growth import cold_postmortem, gates, proof_formats
from experiments.growth.baseline import FIXED_NOW, _cohort


ROOT = Path(__file__).resolve().parents[2]
SOURCE_REPORT = proof_formats.SOURCE_REPORT
SOURCE_REPORT_SHA256 = proof_formats.SOURCE_REPORT_SHA256
PREREQUISITE_REPORT = proof_formats.PREREQUISITE_REPORT
PREREQUISITE_REPORT_SHA256 = proof_formats.PREREQUISITE_SHA256
PROFILE = "native-production-update-pack-500-plus-10"
SCHEMA_VERSION = 1
OVERALL_SECONDS = 600
PHASE_SECONDS = 120
COHORTS = (201, 202)
SOURCES = (
    "experiments/growth/update_pack_production.py",
    "experiments/growth/baseline.py",
    "experiments/growth/cold_postmortem.py",
    "experiments/growth/gates.py",
    "experiments/growth/proof_formats.py",
    "omarchy_knowledge/canonical.py",
    "omarchy_knowledge/coordinator.py",
    "omarchy_knowledge/distribution.py",
    "omarchy_knowledge/github_native.py",
    "omarchy_knowledge/github_object_batch.py",
    "omarchy_knowledge/object_bundle.py",
    "omarchy_knowledge/proof_cache.py",
    "omarchy_knowledge/resolution.py",
    "omarchy_knowledge/service.py",
    "omarchy_knowledge/snapshots.py",
    "omarchy_knowledge/update_pack.py",
)


def _sha256(raw):
    return hashlib.sha256(raw).hexdigest()


def _source_hashes():
    return {path: _sha256((ROOT / path).read_bytes()) for path in SOURCES}


def _load_source(path=SOURCE_REPORT, prerequisite=PREREQUISITE_REPORT):
    source, checked = proof_formats._load_inputs(Path(path), Path(prerequisite))
    if checked["digest"] != PREREQUISITE_REPORT_SHA256:
        raise ValueError("Unsupported prerequisite report")
    return source


def _transport(rows, adapter):
    identity_paths = {
        "/repos/" + gates._NativeFixture.REPOSITORY,
        "/repos/" + gates._NativeFixture.REPOSITORY + "/git/ref/heads/main",
    }
    groups = {name: [] for name in ("identity", "api_objects", "proof")}
    for row in rows:
        if row["host"] == gates._NativeFixture.PAGES_HOST:
            groups["proof"].append(row)
        elif row["path"] in identity_paths:
            groups["identity"].append(row)
        else:
            groups["api_objects"].append(row)
    result = {}
    for name, requests in groups.items():
        result[name] = {
            "requests": len(requests),
            "response_bytes": sum(row["response_bytes"] for row in requests),
        }
    result["proof"]["artifacts"] = [
        row["path"].rsplit("/", 1)[-1] for row in groups["proof"]
    ]
    result["adapter"] = {
        "calls": adapter.http.calls,
        "accounted_response_bytes": adapter.http.bytes,
        "graphql_calls": adapter.http.graphql_calls,
        "graphql_points": adapter.http.graphql_points,
    }
    return result


def _parity(snapshot, expected):
    from omarchy_knowledge.coordinator import canonical

    expected_receipts = sorted(expected["receipts"], key=canonical)
    actual_receipts = sorted(snapshot.canonical["receipts"], key=canonical)
    adverse_expected = sum(
        record["type"] == "report"
        and record["payload"]["result"] in {"failure", "partial"}
        for record in expected["records"]
    )
    adverse_actual = sum(
        record["type"] == "report"
        and record["payload"]["result"] in {"failure", "partial"}
        for record in snapshot.records
    )
    return {
        "records": ({record["id"]: record for record in snapshot.records}
                    == {record["id"]: record for record in expected["records"]}),
        "receipts": actual_receipts == expected_receipts,
        "source": snapshot.canonical["source"] == expected["source"],
        "upstream": snapshot.canonical["upstream"] == expected["upstream"],
        "adverse_evidence": adverse_actual == adverse_expected,
    }


def _sync_client(fixture, policy, cache, expected):
    from omarchy_knowledge.canonical import sync
    from omarchy_knowledge.github_native import GitHubRead
    from omarchy_knowledge.snapshots import load_cache

    request_start = len(fixture.requests)
    started = time.monotonic()
    adapter = GitHubRead(
        read_token=gates.SYNTHETIC_TOKEN,
        connection_factory=fixture.connection_factory,
    )
    with patch("omarchy_knowledge.resolution.refresh_canonical",
               side_effect=gates._refresh_offline):
        status = sync(adapter, policy, cache, now=FIXED_NOW)
    snapshot = load_cache(cache)
    rows = fixture.requests[request_start:]
    return {
        "status": status,
        "transport": _transport(rows, adapter),
        "parity": _parity(snapshot, expected),
        "elapsed_seconds": round(time.monotonic() - started, 6),
    }


def _wrong_base_proof():
    from omarchy_knowledge.object_bundle import encode

    tree_raw = b""
    tree = hashlib.sha1(b"tree 0\0").hexdigest()
    commit_raw = (
        b"tree " + tree.encode() +
        b"\nauthor Fixture <fixture@example.invalid> 0 +0000"
        b"\ncommitter Fixture <fixture@example.invalid> 0 +0000\n\nwrong base\n"
    )
    head = hashlib.sha1(
        b"commit " + str(len(commit_raw)).encode() + b"\0" + commit_raw
    ).hexdigest()
    return encode(head, {("tree", tree): tree_raw, ("commit", head): commit_raw})


def _publish_records(fixture, policy, number, records, cohorts, budget, alarm,
                     site, publication):
    from omarchy_knowledge.coordinator import canonical
    from omarchy_knowledge.distribution import build_site
    from omarchy_knowledge.github_native import GitHubRead, GitHubWriter, strict_json
    from omarchy_knowledge.service import _publisher_build, plan_run, publish_run

    mutation_start = len(fixture.successful_mutations)
    publication.update({
        "pull_request": number,
        "cohorts": list(cohorts),
        "records": len(records),
        "publish_completed_return": False,
        "known_mutation_acceptance": False,
        "full_build_completion": False,
        "mutation_audit": [],
        "jobs": {},
    })

    def capture_mutations():
        mutations = fixture.successful_mutations[mutation_start:]
        publication["mutation_audit"] = mutations
        publication["known_mutation_acceptance"] = any(
            row["headline"] == "Omarchy knowledge snapshot import"
            for row in mutations
        )
        return mutations

    def capture_failed_job(name, error):
        metrics = getattr(error, "_growth_job_metrics", None)
        if metrics is not None:
            publication["jobs"][name] = metrics

    started = time.monotonic()
    with gates._phase_deadline(budget, alarm, PHASE_SECONDS):
        fixture.add_candidate(number, records)
        planning = GitHubRead(
            read_token=gates.SYNTHETIC_TOKEN,
            connection_factory=fixture.connection_factory,
        )

        def plan(api):
            value = plan_run(
                api, policy, pull_request=number, run_number=number - 1,
                run_id=number, run_attempt=1,
            )
            return strict_json(canonical(value) + b"\n")

        try:
            batch, planning_metrics = gates._run_job(fixture, planning, plan)
        except BaseException as error:
            capture_failed_job("planning", error)
            capture_mutations()
            raise
        publication["jobs"]["planning"] = planning_metrics
        if batch["status"]["status"] != "planned" or batch["plan"] is None:
            raise RuntimeError("Contribution was not planned")
        writer = GitHubWriter(
            gates.SYNTHETIC_TOKEN,
            connection_factory=fixture.connection_factory,
        )
        try:
            status, publish_metrics = gates._run_job(
                fixture, writer,
                lambda api: publish_run(
                    api, policy, batch, run_id=number, run_attempt=1,
                ),
            )
        except BaseException as error:
            capture_failed_job("publish", error)
            capture_mutations()
            raise
        publication["jobs"]["publish"] = publish_metrics
        if status["status"] != "accepted":
            raise RuntimeError("Contribution was not accepted")
        publication["publish_completed_return"] = True
        capture_mutations()

        builder = GitHubRead(
            read_token=gates.SYNTHETIC_TOKEN,
            connection_factory=fixture.connection_factory,
        )
        build_request_start = len(fixture.requests)
        build_started = time.monotonic()
        try:
            data, proof, update = _publisher_build(builder, policy, now=FIXED_NOW)
        except BaseException:
            publication["jobs"]["build"] = gates._adapter_metrics(
                builder, seed_outcome=None,
                elapsed=time.monotonic() - build_started,
                completed_return=False, request_start=build_request_start,
                fixture=fixture,
            )
            raise
        if update is None:
            raise RuntimeError("Publisher omitted matching direct update")
        public_data = gates._refresh_offline(deepcopy(data))
        distribution = build_site(
            public_data, site, status=status, proof_bundle=proof,
            update_manifest=update[0], update_bundle=update[1],
        )
        build_metrics = gates._adapter_metrics(
            builder, seed_outcome=None,
            elapsed=time.monotonic() - build_started, completed_return=True,
            request_start=build_request_start, fixture=fixture,
        )
        publication["jobs"]["build"] = build_metrics
        fixture.publish_pages(proof, number, update=update)
        publication["full_build_completion"] = True
    mutations = capture_mutations()
    if ([row["addition_count"] for row in mutations]
            != [len(records), len(records)]
            or {row["headline"] for row in mutations} != {
                "Omarchy knowledge snapshot import",
                "Record source-bound ingestion receipt",
            }):
        raise RuntimeError("Contribution mutation audit failed")
    publication.update({
        "distribution": distribution,
        "elapsed_seconds": round(time.monotonic() - started, 6),
    })
    return data, proof, update


def _publish_ten(fixture, policy, number, budget, alarm, site, publication):
    records = [*_cohort(COHORTS[0], 1), *_cohort(COHORTS[1], 1)]
    return _publish_records(
        fixture, policy, number, records, COHORTS, budget, alarm, site,
        publication,
    )


def _run_skipped_intermediate_fixture(source, budget, alarm):
    """Exercise two ordinary publications from a cache held at their predecessor."""
    from omarchy_knowledge.coordinator import Policy
    from omarchy_knowledge.update_pack import decode_manifest

    with tempfile.TemporaryDirectory(prefix="omarchy-skipped-update-") as temporary:
        root = Path(temporary)
        with gates._NativeFixture(root / "fixture") as fixture:
            policy = Policy("a" * 40, "b" * 40)
            with gates._phase_deadline(budget, alarm, PHASE_SECONDS):
                cold_postmortem._reconstruct(fixture, source)
                base_data, base_proof, _native = proof_formats._native_proof(
                    fixture, policy,
                )
                fixture.publish_pages(base_proof, len(source["imports"]))
                expected_base = gates._refresh_offline(deepcopy(base_data))
            cache = root / "base-cache"
            with gates._phase_deadline(budget, alarm, PHASE_SECONDS):
                cold = _sync_client(fixture, policy, cache, expected_base)
            if (cold["transport"]["proof"]["artifacts"]
                    != ["canonical-objects.bundle"]
                    or not all(cold["parity"].values())):
                raise RuntimeError("Skipped-intermediate base cache gate failed")

            publications = []
            publication_heads = []
            target_data = None
            for offset, cohort in enumerate((301, 302), start=1):
                publication = {}
                target_data, _proof, _update = _publish_records(
                    fixture, policy, len(source["imports"]) + offset,
                    _cohort(cohort, 1), (cohort,), budget, alarm,
                    root / ("site-" + str(offset)), publication,
                )
                publications.append(publication)
                publication_heads.append(
                    target_data["source"]["data_revision"]
                )

            expected_target = gates._refresh_offline(deepcopy(target_data))
            retained = decode_manifest(
                fixture.pages_state.update_manifest,
                expected_deployment="production",
                expected_target_head=publication_heads[-1],
            )
            with gates._phase_deadline(budget, alarm, PHASE_SECONDS):
                skipped = _sync_client(
                    fixture, policy, cache, expected_target,
                )
            skipped["full_fallback"] = (
                skipped["transport"]["proof"]["artifacts"]
                == ["canonical-update.json", "canonical-objects.bundle"]
            )
            return {
                "base_records": len(base_data["records"]),
                "intermediate_records": len(base_data["records"]) + 5,
                "target_records": len(target_data["records"]),
                "publications": len(publications),
                "base_head": base_data["source"]["data_revision"],
                "intermediate_head": publication_heads[0],
                "target_head": publication_heads[-1],
                "retained_pack_base_head": retained.base_head,
                "transport": skipped["transport"],
                "parity": skipped["parity"],
                "full_fallback": skipped["full_fallback"],
                "fixture": {
                    "contract_violations": fixture.contract_violations,
                    "pages_publications": fixture.pages_publications,
                },
            }


def _run_fixture_measurement(source, budget, alarm, measurement, *,
                             require_ratio=False, checkpoint=None):
    from omarchy_knowledge.coordinator import Policy
    from omarchy_knowledge.object_bundle import decode
    from omarchy_knowledge.proof_cache import store

    checkpoint = checkpoint or (lambda _phase, _measurement: None)
    measurement.setdefault("clients", {})
    with tempfile.TemporaryDirectory(prefix="omarchy-production-update-") as temporary:
        root = Path(temporary)
        with gates._NativeFixture(root / "fixture") as fixture:
            policy = Policy("a" * 40, "b" * 40)
            try:
                measurement["active_phase"] = "base-reconstruction"
                with gates._phase_deadline(budget, alarm, PHASE_SECONDS):
                    reconstructed = cold_postmortem._reconstruct(fixture, source)
                    measurement["reconstruction"] = reconstructed
                    checkpoint("reconstruction-recorded", measurement)
                    base_data, base_proof, native = proof_formats._native_proof(
                        fixture, policy)
                    base_head = base_data["source"]["data_revision"]
                    base_objects = decode(base_proof, base_head)
                    measurement["base"] = {
                        "head": base_head,
                        "records": len(base_data["records"]),
                        "receipts": len(base_data["receipts"]),
                        "proof_bytes": len(base_proof),
                        "proof_sha256": _sha256(base_proof),
                        "typed_objects": len(base_objects),
                        "native_generation": native,
                    }
                    if not (
                        reconstructed["data_head"] == base_head
                        and reconstructed["all_recorded_mutation_identities_equal"] is True
                        and len(base_data["records"]) == sum(
                            item["record_count"] for item in source["imports"])
                    ):
                        raise RuntimeError("Reconstructed base identity mismatch")
                    base_imports = len(source["imports"])
                    measurement["history"] = {
                        "imports": base_imports + 1,
                        "base_imports": base_imports,
                        "contributions_in_final_pr": 2,
                    }
                    fixture.publish_pages(base_proof, base_imports)
                    expected_base = gates._refresh_offline(deepcopy(base_data))
                checkpoint("base-recorded", measurement)

                measurement["active_phase"] = "cold-base-client"
                cache = root / "matching-cache"
                wrong_cache = root / "wrong-cache"
                with gates._phase_deadline(budget, alarm, PHASE_SECONDS):
                    cold = _sync_client(fixture, policy, cache, expected_base)
                    measurement["clients"]["cold_base"] = cold
                    shutil.copytree(cache, wrong_cache)
                checkpoint("cold-base-client-recorded", measurement)
                if (cold["transport"]["proof"]["artifacts"]
                        != ["canonical-objects.bundle"]
                        or not all(cold["parity"].values())):
                    raise RuntimeError("Cold-base client gate failed")

                measurement["active_phase"] = "publication"
                number = base_imports + 1
                publication = {}
                measurement["publication"] = publication
                target_data, target_proof, update = _publish_ten(
                    fixture, policy, number, budget, alarm, root / "site",
                    publication,
                )
                expected_target = gates._refresh_offline(deepcopy(target_data))
                target_head = target_data["source"]["data_revision"]
                target_objects = decode(target_proof, target_head)
                measurement["target"] = {
                    "head": target_head,
                    "records": len(target_data["records"]),
                    "receipts": len(target_data["receipts"]),
                    "proof_bytes": len(target_proof),
                    "proof_sha256": _sha256(target_proof),
                    "typed_objects": len(target_objects),
                }
                publication.update({
                    "update_artifacts": [
                        "canonical-update.json", "canonical-update.bundle",
                    ],
                    "manifest_bytes": len(update[0]),
                    "pack_bytes": len(update[1]),
                })
                checkpoint("publication-recorded", measurement)

                measurement["active_phase"] = "matching-client"
                with gates._phase_deadline(budget, alarm, PHASE_SECONDS):
                    matching = _sync_client(
                        fixture, policy, cache, expected_target)
                    proof_bytes = matching["transport"]["proof"]["response_bytes"]
                    matching["warm_to_full_ratio"] = round(
                        proof_bytes / len(target_proof), 6,
                    )
                    measurement["clients"]["matching_base"] = matching
                checkpoint("matching-client-recorded", measurement)
                if (proof_bytes != len(update[0]) + len(update[1])
                        or (require_ratio and matching["warm_to_full_ratio"] > .25)
                        or not all(matching["parity"].values())):
                    raise RuntimeError("Matching-base client gate failed")

                measurement["active_phase"] = "same-head-client"
                with gates._phase_deadline(budget, alarm, PHASE_SECONDS):
                    same = _sync_client(fixture, policy, cache, expected_target)
                    measurement["clients"]["same_head"] = same
                checkpoint("same-head-client-recorded", measurement)
                if same["transport"]["proof"]["requests"] != 0:
                    raise RuntimeError("Same-head client downloaded proof artifacts")

                measurement["active_phase"] = "wrong-base-client"
                with gates._phase_deadline(budget, alarm, PHASE_SECONDS):
                    store(wrong_cache, _wrong_base_proof())
                    wrong = _sync_client(
                        fixture, policy, wrong_cache, expected_target)
                    wrong["full_fallback"] = (
                        wrong["transport"]["proof"]["artifacts"]
                        == ["canonical-update.json", "canonical-objects.bundle"]
                    )
                    measurement["clients"]["wrong_base"] = wrong
                checkpoint("wrong-base-client-recorded", measurement)
                if (not wrong["full_fallback"]
                        or not all(wrong["parity"].values())):
                    raise RuntimeError("Wrong-base full fallback gate failed")
                measurement["active_phase"] = None
            finally:
                measurement["fixture"] = {
                    "contract_violations": fixture.contract_violations,
                    "emulated_https_requests": len(fixture.requests),
                    "emulated_response_bytes": sum(
                        row["response_bytes"] for row in fixture.requests
                    ),
                    "git_commands": fixture.repository.commands,
                    "observed_main": fixture.main,
                    "observed_mutations": list(fixture.successful_mutations),
                    "object_reader": {
                        "cached_objects": len(fixture.reader.cache),
                        "cached_object_bytes": fixture.reader.bytes,
                    },
                }
    return measurement


def run_measurement(*, source_report=SOURCE_REPORT):
    started = time.monotonic()
    result = {
        "schema_version": SCHEMA_VERSION,
        "profile": PROFILE,
        "status": "failure",
        "failure_stage": "input-binding",
        "failure_kind": None,
        "deadline_scope": None,
        "configuration": {
            "base_records": 500,
            "records_added": 10,
            "final_prs": 1,
            "contributions_in_final_pr": 2,
            "overall_seconds": OVERALL_SECONDS,
            "phase_seconds": PHASE_SECONDS,
            "matching_base_max_fraction": .25,
        },
        "environment": {
            "python_version": sys.version.split()[0],
            "source_revision": gates._revision(),
            "source_sha256": _source_hashes(),
            "working_tree": gates._dirty_state(),
        },
        "network": {"real_network_requests": 0},
        "measurement": {},
        "limitations": [
            "The established 500-record synthetic graph is reconstructed from its hash-bound report; its 100-import admission timing pipeline is not repeated.",
            "The ten-record target is one normal PR containing two existing five-record cohorts, producing a 101-import history rather than an inapplicable two-step patch chain.",
            "Matching-base savings apply only to clients holding the exact direct predecessor; cold, skipped, and infrequent clients use the full proof.",
            "Complete canonical verification CPU is unchanged by reduced transfer bytes.",
            "All requests use the strict local HTTPS fixture; no credential, real network request, public record, write, or deployment is used.",
        ],
    }
    try:
        source = _load_source(source_report)
        result["source_report"] = {
            "path": "experiments/growth/results/native-distributed-500-v1.json",
            "sha256": source["digest"],
            "imports": len(source["value"]["imports"]),
        }
        result["failure_stage"] = "production-client-measurement"
        budget = gates._DeadlineBudget(OVERALL_SECONDS)
        with gates._alarm_handler(budget) as alarm:
            _run_fixture_measurement(
                source["value"], budget, alarm, result["measurement"],
                require_ratio=True,
            )
        result["status"] = "success"
        result["failure_stage"] = None
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as error:
        if isinstance(error, gates._DeadlineExpired):
            result["deadline_scope"] = error.scope
            result["status"] = "incomplete" if error.scope == "overall" else "failure"
        else:
            result["status"] = "failure"
        result["failure_kind"] = type(error).__name__
        result["failure_stage"] = (
            result["measurement"].get("active_phase") or result["failure_stage"]
        )
    finally:
        result["elapsed_seconds"] = round(time.monotonic() - started, 6)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-report", type=Path, default=SOURCE_REPORT)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args(argv)
    if arguments.output is not None:
        gates._validate_new_target(arguments.output)
    result = run_measurement(source_report=arguments.source_report)
    raw = json.dumps(result, sort_keys=True, indent=2, allow_nan=False) + "\n"
    if arguments.output is not None:
        gates._exclusive_write(arguments.output, raw.encode())
    print(raw, end="")
    return 0 if result["status"] == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
