#!/usr/bin/env python3
"""Small offline service/publication proof for a candidate beyond row 200."""
from __future__ import annotations

from collections import Counter
import hashlib
from pathlib import Path
import tempfile

from experiments.growth import gates
from experiments.growth.baseline import FIXED_NOW, _cohort


ROOT = Path(__file__).resolve().parents[2]
SOURCES = (
    "experiments/growth/baseline.py",
    "experiments/growth/fair_intake_service.py",
    "experiments/growth/gates.py",
    "omarchy_knowledge/coordinator.py",
    "omarchy_knowledge/distribution.py",
    "omarchy_knowledge/fair_intake.py",
    "omarchy_knowledge/github_native.py",
    "omarchy_knowledge/intake_status.py",
    "omarchy_knowledge/service.py",
)


def _source_hashes():
    return {
        path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest()
        for path in SOURCES
    }


def _run_scheduled_entrypoints(fixture, policy, root, *, run_id, now,
                               expected_build_exit=0):
    return gates._scheduled_service_entrypoints(
        fixture, policy, root, run_id=run_id, now=now,
        expected_build_exit=expected_build_exit,
    )


def run_queue():
    """Exercise two scheduled publications and exactly one real admission."""
    from omarchy_knowledge.coordinator import MAX_PLAN, Policy
    from omarchy_knowledge.fair_intake import (
        MAX_EVALUATIONS, MAX_PAGE_FETCHES, MAX_RETURNED_ROWS,
    )
    from omarchy_knowledge.github_native import (
        MAX_BYTES, MAX_CALLS, MAX_RESPONSE, TOTAL_SECONDS, GitHubRead,
        strict_json,
    )
    from omarchy_knowledge.intake_status import validate_intake_status

    with tempfile.TemporaryDirectory(prefix="omarchy-fair-intake-service-") as temporary:
        root = Path(temporary)
        with gates._NativeFixture(root / "fixture") as fixture:
            policy = Policy("a" * 40, "b" * 40)
            bootstrap = gates._bootstrap_fixture_intake(fixture, policy, root)
            for number in range(1, 206):
                fixture.add_closed_pull(number)
            fixture.add_candidate(206, _cohort(206, 1))

            runs = []
            for index, now in enumerate((
                    "2026-09-17T17:00:00Z", "2026-09-17T18:00:00Z"), 1):
                request_start = len(fixture.requests)
                transaction = _run_scheduled_entrypoints(
                    fixture, policy, root / f"transaction-{index}",
                    run_id=index, now=now,
                )
                planned, published = (transaction["planned"],
                                      transaction["published"])
                if not published["pages_publishable"]:
                    raise RuntimeError("Fair-intake publication was withheld")
                proof, status = (transaction["proof"],
                                 transaction["public_status"])
                if index == 1:
                    if published["status"]["status"] != "idle":
                        raise RuntimeError("First fair-intake run was not exhausted")
                    fixture.publish_reconciliation(proof, status)
                else:
                    if published["status"]["status"] != "accepted":
                        raise RuntimeError("Later fair-intake candidate was not accepted")
                    fixture.publish_pages(proof, 206, status=status)
                requests = fixture.requests[request_start:]
                runs.append({
                    "run_id": index,
                    "scan_outcome": planned["scan_outcome"],
                    "stop_reason": planned["stop_reason"],
                    "counters": planned["counters"],
                    "selected_action": published["selected_action"],
                    "status": published["status"]["status"],
                    "plan_artifact_bytes": transaction["plan_artifact_bytes"],
                    "publish_artifact_bytes": transaction["publish_artifact_bytes"],
                    "public_status_bytes": len(status),
                    "emulated_https_requests": len(requests),
                    "request_methods": dict(sorted(Counter(
                        row["method"] for row in requests).items())),
                    "entrypoint_exit_codes": transaction["entrypoint_exit_codes"],
                    "jobs": transaction["jobs"],
                })

            final_reader = GitHubRead(
                read_token=gates.SYNTHETIC_TOKEN,
                connection_factory=fixture.connection_factory,
            )
            from omarchy_knowledge.canonical import read_canonical
            final_data = read_canonical(final_reader, policy, now=FIXED_NOW)
            public = validate_intake_status(
                strict_json(fixture.pages_state.status),
                repository=policy.repository,
                repository_id=policy.repository_id,
                deployment=policy.deployment,
            )
            methods = Counter(row["method"] for row in fixture.requests)
            return {
                "status": "success",
                "configuration": {
                    "lifetime_pull_requests": 206,
                    "closed_pull_requests": 205,
                    "eligible_pull_requests": 1,
                    "actual_imports": 1,
                },
                "limits": {
                    "page_fetches_per_run": MAX_PAGE_FETCHES,
                    "returned_rows_per_run": MAX_RETURNED_ROWS,
                    "candidate_evaluations_per_run": MAX_EVALUATIONS,
                    "plan_artifact_bytes": MAX_PLAN,
                    "publish_artifact_bytes": 64 * 1024,
                    "public_status_bytes": MAX_RESPONSE,
                    "adapter_calls_per_job": MAX_CALLS,
                    "adapter_response_bytes_per_job": MAX_BYTES,
                    "adapter_seconds_per_job": TOTAL_SECONDS,
                },
                "initial_legacy_bootstrap": bootstrap,
                "scheduled_runs": runs,
                "operations": {
                    "emulated_https_requests": len(fixture.requests),
                    "request_methods": dict(sorted(methods.items())),
                    "canonical_mutations": len(fixture.successful_mutations),
                    "pages_publications": {
                        "legacy_bootstrap": 1,
                        "cursor_only": 1,
                        "accepted_import": 1,
                    },
                },
                "final": {
                    "records": len(final_data["records"]),
                    "receipts": len(final_data["receipts"]),
                    "cursor_cycle": public.cursor.cycle,
                    "data_revision": final_data["source"]["data_revision"],
                    "status_sha256": hashlib.sha256(
                        fixture.pages_state.status).hexdigest(),
                },
                "fixture": {
                    "contract_violations": fixture.contract_violations,
                    "synthetic": True,
                    "real_network_requests": 0,
                },
                "source_sha256": _source_hashes(),
                "limitations": [
                    "The first 205 lifetime pull requests are modeled closed rows, not authenticated imports.",
                    "Exactly one candidate is prepared and imported; this is not the full 100-import growth gate.",
                    "All HTTPS is a strict in-process fixture; provider latency, quota, scheduler cadence, and deployment availability are not measured.",
                ],
            }
