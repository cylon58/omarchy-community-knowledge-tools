#!/usr/bin/env python3
"""Repeat the experimental update-pack measurement across fixed successors."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import tempfile
import time

from experiments.growth import cold_postmortem, gates, proof_formats


ROOT = Path(__file__).resolve().parents[2]
SOURCE_REPORT = proof_formats.SOURCE_REPORT
SOURCE_REPORT_SHA256 = proof_formats.SOURCE_REPORT_SHA256
PREREQUISITE_REPORT = proof_formats.PREREQUISITE_REPORT
PREREQUISITE_REPORT_SHA256 = proof_formats.PREREQUISITE_SHA256
FIRST_RESULT = ROOT / "experiments/growth/results/proof-format-experiment-v1.json"
FIRST_RESULT_SHA256 = "e53b469b7d7ee60d99f1b914b7d3b14f6ccd7487f69d9abbb622b1fa577aafde"
SCENARIOS = ((201, 202), (301, 302), (401, 402))
PROFILE = "native-distributed-500-update-pack-variation"
SCHEMA_VERSION = 1
OVERALL_SECONDS = 600
SUCCESSOR_SECONDS = proof_formats.SUCCESSOR_SECONDS
SOURCES = ("experiments/growth/update_pack_variants.py", *proof_formats.SOURCES)


def _load_inputs(source_report, prerequisite_report, first_result):
    source, prerequisite = proof_formats._load_inputs(
        source_report, prerequisite_report
    )
    first = proof_formats._load_report(first_result, FIRST_RESULT_SHA256)
    formats = first["value"].get("measurement", {}).get("formats", [])
    update_packs = [item for item in formats if item.get("family") == "update-pack"]
    if not (
        first["value"].get("profile") == proof_formats.PROFILE
        and first["value"].get("status") == "success"
        and first["value"].get("source_report", {}).get("sha256")
            == SOURCE_REPORT_SHA256
        and len(update_packs) == 1
        and update_packs[0].get("status", "success") == "success"
    ):
        raise ValueError("Unsupported first measurement")
    return source, prerequisite, first


def _measure_update_pack(base_head, base_objects, base_proof, successor_head,
                         successor_objects, successor_proof, successor_data, policy):
    """Measure only the reviewed update-pack family."""
    result = proof_formats._measure_family(
        "update-pack", base_head, base_objects, successor_head,
        successor_objects, successor_proof, successor_data, policy,
    )
    result["base_full_proof_bytes"] = len(base_proof)
    return result


def _refresh_ratio_range(measurement):
    ratios = [
        item["warm_to_full_ratio"]
        for item in measurement["scenarios"]
        if item["status"] == "success"
    ]
    if ratios:
        measurement["warm_to_full_ratio_range"] = {
            "completed_scenarios": len(ratios),
            "minimum": min(ratios),
            "maximum": max(ratios),
        }


def _measure_scenarios(fixture, baseline, context, measurement, *, run_scenario):
    """Measure fixed successors from one captured fixture baseline."""
    measurement.setdefault("scenarios", [])
    for numbers in SCENARIOS:
        fixture.main = baseline["main"]
        fixture.pages_state = baseline["pages_state"]
        try:
            result = run_scenario(fixture, numbers, context)
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception as error:
            measurement["scenarios"].append({
                "numbers": list(numbers),
                "status": "failure",
                "failure_kind": type(error).__name__,
                "failure_traceback": proof_formats._sanitized_trace(error),
            })
            _refresh_ratio_range(measurement)
            raise
        else:
            measurement["scenarios"].append({
                "numbers": list(numbers),
                "status": "success",
                **result,
            })
            _refresh_ratio_range(measurement)


def _run_one_scenario(fixture, numbers, context):
    from omarchy_knowledge.object_bundle import decode

    started = time.monotonic()
    request_start = len(fixture.requests)
    successor_data, successor_proof, imports = proof_formats._append_successors(
        fixture,
        context["policy"],
        numbers,
        context["budget"],
        context["alarm"],
    )
    successor_head = successor_data["source"]["data_revision"]
    successor_objects = decode(successor_proof, successor_head)
    if not (
        len(imports) == 2
        and [item["number"] for item in imports] == list(numbers)
        and len(successor_data["records"]) == context["base_records"] + 10
        and len(successor_data["receipts"]) == context["base_receipts"] + 10
    ):
        raise RuntimeError("Successor scenario count mismatch")
    result = _measure_update_pack(
        context["base_head"],
        context["base_objects"],
        context["base_proof"],
        successor_head,
        successor_objects,
        successor_proof,
        successor_data,
        context["policy"],
    )
    return {
        **result,
        "base_head": context["base_head"],
        "head": successor_head,
        "records": len(successor_data["records"]),
        "receipts": len(successor_data["receipts"]),
        "records_added": 10,
        "receipts_added": 10,
        "full_proof_sha256": proof_formats._sha256(successor_proof),
        "imports": imports,
        "emulated_https_requests": len(fixture.requests) - request_start,
        "elapsed_seconds": round(time.monotonic() - started, 6),
        "elapsed_scope": (
            "Two production plan/publish/build successors, update-pack construction, "
            "exact object reconstruction, full-proof encoding, and canonical replay."
        ),
    }


def _run_fixture_variants(source, budget, alarm, measurement=None):
    """Reconstruct one base, then measure the three fixed update-pack successors."""
    from omarchy_knowledge.coordinator import Policy
    from omarchy_knowledge.object_bundle import decode

    measurement = {} if measurement is None else measurement
    with tempfile.TemporaryDirectory(prefix="omarchy-update-pack-variants-") as temporary:
        root = Path(temporary)
        with gates._NativeFixture(root / "fixture") as fixture:
            policy = Policy("a" * 40, "b" * 40)
            reconstruction_started = time.monotonic()
            reconstructed = cold_postmortem._reconstruct(fixture, source)
            reconstructed["elapsed_seconds"] = round(
                time.monotonic() - reconstruction_started, 6
            )
            measurement["reconstruction"] = reconstructed
            base_data, base_proof, native = proof_formats._native_proof(fixture, policy)
            base_head = base_data["source"]["data_revision"]
            base_objects = decode(base_proof, base_head)
            expected_records = sum(item["record_count"] for item in source["imports"])
            if not (
                reconstructed["data_head"] == base_head
                and reconstructed["all_recorded_mutation_identities_equal"] is True
                and len(base_data["records"]) == expected_records
                and len(base_data["receipts"]) == expected_records
                and fixture.contract_violations == 0
            ):
                raise RuntimeError("Base reconstruction count or identity mismatch")
            measurement["base"] = {
                "head": base_head,
                "records": len(base_data["records"]),
                "receipts": len(base_data["receipts"]),
                "full_proof_bytes": len(base_proof),
                "full_proof_sha256": proof_formats._sha256(base_proof),
                "typed_objects": len(base_objects),
                "native_generation": native,
            }
            fixture.publish_pages(base_proof, len(source["imports"]))
            measurement["intake_bootstrap"] = gates._bootstrap_fixture_intake(
                fixture, policy, root)
            baseline = {"main": fixture.main, "pages_state": fixture.pages_state}
            context = {
                "policy": policy,
                "budget": budget,
                "alarm": alarm,
                "base_head": base_head,
                "base_objects": base_objects,
                "base_proof": base_proof,
                "base_records": len(base_data["records"]),
                "base_receipts": len(base_data["receipts"]),
            }
            measurement["scenarios"] = []
            try:
                _measure_scenarios(
                    fixture,
                    baseline,
                    context,
                    measurement,
                    run_scenario=_run_one_scenario,
                )
            finally:
                measurement["fixture"] = proof_formats._fixture_metrics(fixture)
            return measurement


def _source_hashes():
    return {
        path: proof_formats._sha256((ROOT / path).read_bytes())
        for path in SOURCES
    }


def run_variation(*, source_report=SOURCE_REPORT,
                  prerequisite_report=PREREQUISITE_REPORT,
                  first_result=FIRST_RESULT):
    started = time.monotonic()
    result = {
        "schema_version": SCHEMA_VERSION,
        "profile": PROFILE,
        "status": "failure",
        "failure_stage": "input-binding",
        "failure_kind": None,
        "configuration": {
            "base_records": proof_formats.BASE_RECORDS,
            "successor_scenarios": len(SCENARIOS),
            "cohort_numbers": [list(numbers) for numbers in SCENARIOS],
            "successor_imports_per_scenario": 2,
            "records_per_successor": 5,
            "added_records_per_scenario": proof_formats.ADDED_RECORDS,
            "overall_seconds": OVERALL_SECONDS,
            "successor_phase_seconds": SUCCESSOR_SECONDS,
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
            "The three deterministic successors share one synthetic history, fixture, immutable-object cache, and record-template family; they are not independent histories or varied real hardware inventories.",
            "Timings include warm shared fixture state and must not be interpreted as independent cold admission measurements.",
            "The original 100-import admission timing pipeline was not rerun; its immutable graph was reconstructed from the hash-bound saved report.",
            "The first 5.48% measurement motivated this bounded follow-up but is not a general savings claim; every scenario is retained, including any result above 25%.",
            "All payloads and HTTPS exchanges are inert local fixtures; no external request, credential, public record, or production write occurred.",
            "Warm ratios compare manifest plus update-pack bytes against that scenario's compressed complete successor proof.",
            "The reviewed codec remains experimental tooling, not an accepted production format or deployment change.",
        ],
    }
    try:
        source, prerequisite, first = _load_inputs(
            source_report, prerequisite_report, first_result
        )
        result["source_report"] = {
            "path": "experiments/growth/results/native-distributed-500-v1.json",
            "sha256": source["digest"],
            "imports": len(source["value"]["imports"]),
        }
        result["prerequisite"] = {
            "path": "experiments/growth/results/native-batched-reads-v2.json",
            "sha256": prerequisite["digest"],
            "status": prerequisite["value"]["status"],
        }
        prior_update_pack = next(
            item
            for item in first["value"]["measurement"]["formats"]
            if item["family"] == "update-pack"
        )
        result["first_measurement"] = {
            "path": "experiments/growth/results/proof-format-experiment-v1.json",
            "sha256": first["digest"],
            "status": first["value"]["status"],
            "warm_to_full_ratio": prior_update_pack["warm_to_full_ratio"],
        }
        result["failure_stage"] = "fixture-variants"
        budget = gates._DeadlineBudget(OVERALL_SECONDS)
        with gates._alarm_handler(budget) as alarm:
            _run_fixture_variants(
                source["value"], budget, alarm, result["measurement"]
            )
        result["status"] = "success"
        result["failure_stage"] = None
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as error:
        result["status"] = (
            "incomplete" if isinstance(error, gates._DeadlineExpired) else "failure"
        )
        result["failure_kind"] = type(error).__name__
        result["failure_traceback"] = proof_formats._sanitized_trace(error)
    finally:
        result["elapsed_seconds"] = round(time.monotonic() - started, 6)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-report", type=Path, default=SOURCE_REPORT)
    parser.add_argument("--prerequisite-report", type=Path, default=PREREQUISITE_REPORT)
    parser.add_argument("--first-result", type=Path, default=FIRST_RESULT)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args(argv)
    if arguments.output is not None:
        gates._validate_new_target(arguments.output)
    result = run_variation(
        source_report=arguments.source_report,
        prerequisite_report=arguments.prerequisite_report,
        first_result=arguments.first_result,
    )
    raw = json.dumps(result, sort_keys=True, indent=2, allow_nan=False) + "\n"
    if arguments.output is not None:
        gates._exclusive_write(arguments.output, raw.encode())
    print(raw, end="")
    return 0 if result["status"] == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
