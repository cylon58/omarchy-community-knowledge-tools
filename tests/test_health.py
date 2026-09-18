"""Bounded public service-health projection and checker tests."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


class FixedResponses:
    def __init__(self, responses):
        self.responses = responses
        self.requests = []

    def factory(self, host, timeout):
        fixture = self

        class Connection:
            def request(self, method, path, body=None, headers=None):
                fixture.requests.append((host, method, path, body, headers))
                self.path = path

            def getresponse(self):
                status, raw = fixture.responses[(host, self.path)]
                response = io.BytesIO(raw)
                response.status = status
                return response

            def close(self):
                pass

        return Connection()


def health_status(*, verified_at="2026-09-17T11:30:00Z"):
    from tests.test_fair_intake_adapters import current_status

    value = current_status()
    value["source"]["data_revision"] = "a" * 40
    value["source"]["verified_at"] = verified_at
    value["receipt_coverage"] = {"records": 2, "receipted_records": 2}
    value["build_health"] = {
        "version": 1,
        "record_count": 2,
        "receipt_count": 2,
        "proof": {
            "object_count": 10,
            "raw_bytes": 1000,
            "compressed_bytes": 500,
        },
        "canonical_builder": {
            "scope": "successful-build-canonical-adapter",
            "request_attempts": 12,
            "charged_response_bytes": 3456,
            "object_visits": None,
        },
    }
    value["cursor_health"] = {
        "version": 1,
        "last_progress_at": "2026-09-17T11:00:00Z",
        "consecutive_drift_runs": 0,
    }
    value["intake_scan"]["stop_reason"] = "cycle-complete"
    value["intake_scan"]["counters"] = {
        "page_fetches": 1, "rows_returned": 0, "rows_consumed": 0,
        "evaluations": 0, "closed": 0, "imported": 0, "rejected": 0,
        "not_ready": 0, "plans": 0, "cursor_drifts": 0,
    }
    return value


def healthy_responses():
    status = json.dumps(health_status(), sort_keys=True, separators=(",", ":")).encode()
    source = health_status()["source"]
    distribution = json.dumps({
        "version": 1,
        "source": source,
        "files": {
            "status.json": {
                "size": len(status),
                "sha256": hashlib.sha256(status).hexdigest(),
            },
            "records.jsonl": {"size": 17, "sha256": "0" * 64},
        },
    }, sort_keys=True, separators=(",", ":")).encode()
    repo = "cylon58/omarchy-community-knowledge"
    api = "api.github.com"
    pages = "cylon58.github.io"
    run_id = 35262441490
    return {
        (api, f"/repos/{repo}"): (200, json.dumps({
            "id": 1373429914, "full_name": repo, "default_branch": "main",
        }).encode()),
        (api, f"/repos/{repo}/git/ref/heads/main"): (200, json.dumps({
            "ref": "refs/heads/main",
            "object": {"type": "commit", "sha": "a" * 40},
        }).encode()),
        (api, f"/repos/{repo}/actions/workflows/reconcile.yml/runs?event=schedule&per_page=20&page=1"): (200, json.dumps({
            "total_count": 1,
            "workflow_runs": [{
                "id": run_id, "run_attempt": 1, "event": "schedule",
                "status": "completed", "conclusion": "success",
                "head_branch": "main", "head_sha": "a" * 40,
                "created_at": "2026-09-17T11:20:00Z",
                "repository": {"id": 1373429914, "full_name": repo},
            }],
        }).encode()),
        (api, f"/repos/{repo}/actions/runs/{run_id}/attempts/1/jobs?per_page=20&page=1"): (200, json.dumps({
            "total_count": 2,
            "jobs": [
                {"id": 1, "run_id": run_id, "run_attempt": 1,
                 "head_sha": "a" * 40, "name": "test", "status": "completed",
                 "conclusion": "success", "started_at": "2026-09-17T11:20:10Z",
                 "completed_at": "2026-09-17T11:21:00Z"},
                {"id": 2, "run_id": run_id, "run_attempt": 1,
                 "head_sha": "a" * 40, "name": "pages", "status": "completed",
                 "conclusion": "success", "started_at": "2026-09-17T11:21:01Z",
                 "completed_at": "2026-09-17T11:22:00Z"},
            ],
        }).encode()),
        (pages, "/omarchy-community-knowledge/status.json"): (200, status),
        (pages, "/omarchy-community-knowledge/distribution.json"): (200, distribution),
    }


def replace_status(responses, value):
    responses = dict(responses)
    status_key = ("cylon58.github.io",
                  "/omarchy-community-knowledge/status.json")
    distribution_key = ("cylon58.github.io",
                         "/omarchy-community-knowledge/distribution.json")
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    distribution = json.loads(responses[distribution_key][1])
    distribution["source"] = value["source"]
    distribution["files"]["status.json"] = {
        "size": len(raw), "sha256": hashlib.sha256(raw).hexdigest(),
    }
    responses[status_key] = (200, raw)
    responses[distribution_key] = (
        200,
        json.dumps(distribution, sort_keys=True, separators=(",", ":")).encode(),
    )
    return responses


class HealthProjectionTests(unittest.TestCase):
    def test_build_projects_measured_health_and_strict_reader_accepts_it(self):
        """Break caught: successful builds omit measured proof and adapter usage."""
        from omarchy_knowledge.distribution import build_health_projection, build_site
        from omarchy_knowledge.intake_status import PublicStatusKind, validate_intake_status
        from omarchy_knowledge.object_bundle import encode

        raw_commit = b"tree " + b"0" * 40 + b"\n\na\n"
        oid = hashlib.sha1(
            b"commit " + str(len(raw_commit)).encode() + b"\0" + raw_commit
        ).hexdigest()
        proof = encode(oid, {("commit", oid): raw_commit})
        data = {
            "records": [],
            "receipts": [],
            "source": {
                "repository": "cylon58/omarchy-community-knowledge",
                "repository_id": 1373429914,
                "deployment": "production",
                "ref": "refs/heads/main",
                "data_revision": oid,
                "tree_revision": "b" * 40,
                "toolkit_revision": "c" * 40,
                "policy_revision": "d" * 40,
                "verified_at": "2026-09-17T10:00:00Z",
                "source_updated_at": "2026-09-17T09:00:00Z",
            },
            "upstream": {"version": 1, "status": "not-refreshed", "observations": []},
        }
        projection = build_health_projection(
            data, proof,
            api_calls=12, response_bytes=3456,
        )
        self.assertEqual(projection, {
            "version": 1,
            "record_count": 0,
            "receipt_count": 0,
            "proof": {
                "object_count": 1,
                "raw_bytes": len(raw_commit),
                "compressed_bytes": len(proof),
            },
            "canonical_builder": {
                "scope": "successful-build-canonical-adapter",
                "request_attempts": 12,
                "charged_response_bytes": 3456,
                "object_visits": None,
            },
        })
        with tempfile.TemporaryDirectory(prefix="health-projection-") as temporary:
            site = Path(temporary) / "site"
            build_site(
                data, site, status={"status": "idle", "outcomes": []},
                proof_bundle=proof, build_health=projection,
                intake_cursor={
                    "version": 1, "page": 1, "offset": 0,
                    "after_pull_request": None, "cycle": 1,
                    "last_full_cycle_at": "2026-09-17T10:00:00Z",
                },
                cursor_health={
                    "version": 1,
                    "last_progress_at": "2026-09-17T10:00:00Z",
                    "consecutive_drift_runs": 0,
                },
                intake_scan={
                    "version": 1, "trusted_lane": "scheduled",
                    "prior_state": "current", "selected_action": "after",
                    "scan_outcome": "no-eligible",
                    "stop_reason": "cycle-complete",
                    "counters": {
                        "page_fetches": 1, "rows_returned": 0,
                        "rows_consumed": 0, "evaluations": 0, "closed": 0,
                        "imported": 0, "rejected": 0, "not_ready": 0,
                        "plans": 0, "cursor_drifts": 0,
                    },
                },
            )
            public = json.loads((site / "status.json").read_bytes())
        observed = validate_intake_status(
            public,
            repository="cylon58/omarchy-community-knowledge",
            repository_id=1373429914,
            deployment="production",
        )
        self.assertEqual(observed.kind, PublicStatusKind.CURRENT)
        self.assertEqual(observed.build_health, projection)

    def test_service_projection_uses_actual_scoped_adapter_counters(self):
        """Break caught: builder counters are guessed or taken from another job."""
        from omarchy_knowledge.service import _successful_build_health
        from omarchy_knowledge.object_bundle import encode

        raw_commit = b"tree " + b"0" * 40 + b"\n\na\n"
        oid = hashlib.sha1(
            b"commit " + str(len(raw_commit)).encode() + b"\0" + raw_commit
        ).hexdigest()
        proof = encode(oid, {("commit", oid): raw_commit})
        data = {
            "records": [{"id": "one"}], "receipts": [{"record_id": "one"}],
            "source": {"data_revision": oid},
        }

        class HTTP:
            calls = 7
            bytes = 901

        class Objects:
            visits = 11

        class API:
            http = HTTP()
            objects = Objects()

        projected = _successful_build_health(API(), data, proof)
        self.assertEqual(projected["canonical_builder"]["request_attempts"], 7)
        self.assertEqual(projected["canonical_builder"]["charged_response_bytes"], 901)
        self.assertIsNone(projected["canonical_builder"]["object_visits"])

    def test_health_extension_requires_complete_current_shape_and_matching_counts(self):
        from omarchy_knowledge.intake_status import validate_intake_status

        common = {
            "repository": "cylon58/omarchy-community-knowledge",
            "repository_id": 1373429914,
            "deployment": "production",
        }
        value = health_status()
        validate_intake_status(value, **common)
        variants = []
        partial = deepcopy(value); del partial["build_health"]["proof"]
        variants.append(partial)
        partial = dict(value); del partial["intake_scan"]
        variants.append(partial)
        mismatch = health_status()
        mismatch["build_health"]["record_count"] = 1
        variants.append(mismatch)
        numeric_visits = health_status()
        numeric_visits["build_health"]["canonical_builder"]["object_visits"] = 1
        variants.append(numeric_visits)
        without_trio = health_status()
        for field in ("intake_cursor", "cursor_health", "intake_scan"):
            del without_trio[field]
        variants.append(without_trio)
        for invalid in variants:
            with self.subTest(fields=sorted(invalid)):
                with self.assertRaises(ValueError):
                    validate_intake_status(invalid, **common)


class HealthCheckerTests(unittest.TestCase):
    def test_healthy_check_uses_six_fixed_reads_and_reports_exact_scopes(self):
        """Break caught: a green report bypasses the bounded real request adapter."""
        from omarchy_knowledge.health import check

        fixture = FixedResponses(healthy_responses())
        report = check(
            "production", connection_factory=fixture.factory,
            now="2026-09-17T12:00:00Z",
        )

        self.assertEqual(report["overall"], "ok")
        self.assertEqual(report["availability"], {"status": "ok", "reason": None})
        self.assertEqual(report["intake"]["status"], "ok")
        self.assertEqual(report["evidence_freshness"]["scheduled_pages"], {
            "run_id": 35262441490,
            "run_attempt": 1,
            "run_created_at": "2026-09-17T11:20:00Z",
            "pages_job_completed_at": "2026-09-17T11:22:00Z",
            "age_seconds": 2280,
            "max_age_seconds": 7200,
        })
        # The manifest's own raw length is part of the site total.
        distribution_raw = healthy_responses()[(
            "cylon58.github.io",
            "/omarchy-community-knowledge/distribution.json",
        )][1]
        manifest = json.loads(distribution_raw)
        expected_distribution = len(distribution_raw) + sum(
            item["size"] for item in manifest["files"].values()
        )
        self.assertEqual(
            report["capacity"]["measurements"]["distribution_bytes"]["used"],
            expected_distribution,
        )
        self.assertNotIn("canonical_builder_object_visits",
                         report["capacity"]["measurements"])
        self.assertIn("canonical_builder_object_visits", report["unknowns"])
        self.assertEqual(report["transport"]["attempts"], {"used": 6, "limit": 8})
        self.assertEqual(len(fixture.requests), 6)
        self.assertEqual(
            [request[2] for request in fixture.requests],
            [
                "/repos/cylon58/omarchy-community-knowledge",
                "/repos/cylon58/omarchy-community-knowledge/git/ref/heads/main",
                "/repos/cylon58/omarchy-community-knowledge/actions/workflows/reconcile.yml/runs?event=schedule&per_page=20&page=1",
                "/omarchy-community-knowledge/status.json",
                "/omarchy-community-knowledge/distribution.json",
                "/repos/cylon58/omarchy-community-knowledge/actions/runs/35262441490/attempts/1/jobs?per_page=20&page=1",
            ],
        )

    def test_stale_future_and_revision_drift_are_distinct(self):
        from omarchy_knowledge.health import check

        stale = health_status(verified_at="2026-09-17T07:59:59Z")
        report = check(
            "production",
            connection_factory=FixedResponses(
                replace_status(healthy_responses(), stale)
            ).factory,
            now="2026-09-17T12:00:00Z",
        )
        self.assertEqual(report["overall"], "failure")
        self.assertIn("stale_generation", report["failures"])
        self.assertEqual(report["availability"]["status"], "ok")

        future = health_status(verified_at="2026-09-17T12:00:01Z")
        report = check(
            "production",
            connection_factory=FixedResponses(
                replace_status(healthy_responses(), future)
            ).factory,
            now="2026-09-17T12:00:00Z",
        )
        self.assertEqual(report["availability"], {
            "status": "failure", "reason": "unavailable",
        })

        responses = healthy_responses()
        ref = ("api.github.com",
               "/repos/cylon58/omarchy-community-knowledge/git/ref/heads/main")
        payload = json.loads(responses[ref][1])
        payload["object"]["sha"] = "f" * 40
        responses[ref] = (200, json.dumps(payload).encode())
        report = check(
            "production", connection_factory=FixedResponses(responses).factory,
            now="2026-09-17T12:00:00Z",
        )
        self.assertEqual(report["overall"], "warning")
        self.assertEqual(report["warnings"], ["main_pages_revision_mismatch"])

    def test_fractional_future_and_age_thresholds_use_full_precision(self):
        from omarchy_knowledge.health import check

        value = health_status(verified_at="2026-09-17T12:00:00.900000Z")
        report = check(
            "production",
            connection_factory=FixedResponses(
                replace_status(healthy_responses(), value)
            ).factory,
            now="2026-09-17T12:00:00.000000Z",
        )
        self.assertEqual(report["availability"]["status"], "failure")

        value = health_status(verified_at="2026-09-17T07:59:59.100000Z")
        report = check(
            "production",
            connection_factory=FixedResponses(
                replace_status(healthy_responses(), value)
            ).factory,
            now="2026-09-17T12:00:00.000000Z",
        )
        self.assertIn("stale_generation", report["failures"])

        responses = healthy_responses()
        run_key = next(key for key in responses if "/workflows/" in key[1])
        runs = json.loads(responses[run_key][1])
        runs["workflow_runs"][0]["created_at"] = "2026-09-17T09:50:00Z"
        responses[run_key] = (200, json.dumps(runs).encode())
        job_key = next(key for key in responses if "/jobs?" in key[1])
        jobs = json.loads(responses[job_key][1])
        jobs["jobs"][-1]["started_at"] = "2026-09-17T09:59:00Z"
        jobs["jobs"][-1]["completed_at"] = "2026-09-17T09:59:59.100000Z"
        responses[job_key] = (200, json.dumps(jobs).encode())
        report = check(
            "production", connection_factory=FixedResponses(responses).factory,
            now="2026-09-17T12:00:00.000000Z",
        )
        self.assertIn("stale_successful_scheduled_pages_job",
                      report["failures"])

        value = health_status()
        value["cursor_health"]["last_progress_at"] = \
            "2026-09-17T07:59:59.100000Z"
        report = check(
            "production",
            connection_factory=FixedResponses(
                replace_status(healthy_responses(), value)
            ).factory,
            now="2026-09-17T12:00:00.000000Z",
        )
        self.assertIn("stale_cursor_progress", report["warnings"])

    def test_publication_during_fetch_uses_end_of_observation_wall_time(self):
        from omarchy_knowledge.health import check

        value = health_status(verified_at="2026-09-17T12:00:30Z")
        readings = iter(("2026-09-17T12:00:00Z", "2026-09-17T12:01:00Z"))
        report = check(
            "production",
            connection_factory=FixedResponses(
                replace_status(healthy_responses(), value)
            ).factory,
            wall_clock=lambda: next(readings),
        )
        self.assertEqual(report["checked_at"], "2026-09-17T12:01:00Z")
        self.assertEqual(report["availability"]["status"], "ok")
        self.assertNotIn("stale_generation", report["failures"])

    def test_malformed_redirect_oversize_and_api_failure_fail_sanitized(self):
        from omarchy_knowledge.health import check

        status_key = ("cylon58.github.io",
                      "/omarchy-community-knowledge/status.json")
        repo_key = ("api.github.com",
                    "/repos/cylon58/omarchy-community-knowledge")
        variants = []
        value = health_status(); value["source"]["repository_id"] = 1
        variants.append(replace_status(healthy_responses(), value))
        responses = healthy_responses(); responses[status_key] = (302, b"")
        variants.append(responses)
        responses = healthy_responses(); responses[status_key] = (
            200, b"x" * (1024 * 1024 + 1))
        variants.append(responses)
        responses = healthy_responses(); responses[repo_key] = (503, b"private detail")
        variants.append(responses)
        responses = healthy_responses(); responses[status_key] = (200, b"{")
        variants.append(responses)

        for responses in variants:
            with self.subTest(response=list(responses.values())[0][0]):
                report = check(
                    "production",
                    connection_factory=FixedResponses(responses).factory,
                    now="2026-09-17T12:00:00Z",
                )
                self.assertEqual(report["overall"], "failure")
                self.assertEqual(report["failures"], ["availability"])
                self.assertEqual(set(report["intake"]), {
                    "status", "format", "publication_status",
                    "receipt_coverage", "scan_truncated",
                })
                self.assertEqual(set(report["evidence_freshness"]), {
                    "status", "generation", "scheduled_pages",
                    "revision_match", "upstream_status",
                })
                self.assertNotIn("private detail", json.dumps(report))
                self.assertLessEqual(report["transport"]["attempts"]["used"], 8)
                self.assertLessEqual(
                    report["transport"]["charged_response_bytes"]["used"],
                    8 * 1024 * 1024,
                )

        responses = healthy_responses()
        responses[repo_key] = (503, b"unknown partial body")
        report = check(
            "production",
            connection_factory=FixedResponses(responses).factory,
            now="2026-09-17T12:00:00Z",
        )
        self.assertEqual(
            report["transport"]["charged_response_bytes"]["used"],
            1024 * 1024 + 1,
        )

    def test_missing_or_ambiguous_pages_job_is_not_run_completion(self):
        from omarchy_knowledge.health import check

        run_key = ("api.github.com",
                   "/repos/cylon58/omarchy-community-knowledge/actions/workflows/reconcile.yml/runs?event=schedule&per_page=20&page=1")
        responses = healthy_responses()
        runs = json.loads(responses[run_key][1])
        runs["workflow_runs"][0]["conclusion"] = "failure"
        responses[run_key] = (200, json.dumps(runs).encode())
        fixture = FixedResponses(responses)
        report = check("production", connection_factory=fixture.factory,
                       now="2026-09-17T12:00:00Z")
        self.assertIn("missing_successful_scheduled_pages_job", report["failures"])
        self.assertEqual(report["transport"]["attempts"]["used"], 5)
        self.assertIsNone(report["evidence_freshness"]["scheduled_pages"])

        responses = healthy_responses()
        runs = json.loads(responses[run_key][1])
        runs["workflow_runs"][0]["status"] = "pending"
        runs["workflow_runs"][0]["conclusion"] = None
        responses[run_key] = (200, json.dumps(runs).encode())
        report = check(
            "production",
            connection_factory=FixedResponses(responses).factory,
            now="2026-09-17T12:00:00Z",
        )
        self.assertEqual(report["availability"]["status"], "ok")
        self.assertIn("missing_successful_scheduled_pages_job",
                      report["failures"])

        responses = healthy_responses()
        job_key = next(key for key in responses if "/jobs?" in key[1])
        jobs = json.loads(responses[job_key][1])
        duplicate = dict(jobs["jobs"][-1]); duplicate["id"] = 3
        jobs["jobs"].append(duplicate); jobs["total_count"] = 3
        responses[job_key] = (200, json.dumps(jobs).encode())
        report = check("production",
                       connection_factory=FixedResponses(responses).factory,
                       now="2026-09-17T12:00:00Z")
        self.assertEqual(report["availability"]["status"], "failure")

    def test_all_pages_jobs_are_unique_and_job_run_bindings_are_exact_ints(self):
        from omarchy_knowledge.health import check

        for mutation in ("failed-pages", "boolean-attempt"):
            responses = healthy_responses()
            job_key = next(key for key in responses if "/jobs?" in key[1])
            jobs = json.loads(responses[job_key][1])
            if mutation == "failed-pages":
                jobs["jobs"].append({
                    "id": 3, "run_id": 35262441490, "run_attempt": 1,
                    "head_sha": "a" * 40, "name": "pages",
                    "status": "completed", "conclusion": "failure",
                    "started_at": "2026-09-17T11:20:30Z",
                    "completed_at": "2026-09-17T11:20:40Z",
                })
                jobs["total_count"] = 3
            else:
                jobs["jobs"][-1]["run_attempt"] = True
            responses[job_key] = (200, json.dumps(jobs).encode())
            report = check(
                "production",
                connection_factory=FixedResponses(responses).factory,
                now="2026-09-17T12:00:00Z",
            )
            with self.subTest(mutation=mutation):
                self.assertEqual(report["availability"]["status"], "failure")

    def test_health_api_reserves_full_body_and_sentinel_before_transfer(self):
        from omarchy_knowledge.github_native import (
            HealthRead, MAX_RESPONSE, NativeUnavailable,
        )

        fixture = FixedResponses(healthy_responses())
        reader = HealthRead(
            deployment="production", connection_factory=fixture.factory)
        reader.http.bytes = reader.MAX_CHARGED_BYTES - MAX_RESPONSE
        with self.assertRaises(NativeUnavailable):
            reader.repository()
        self.assertEqual(reader.http.calls, 0)
        self.assertEqual(fixture.requests, [])

    def test_capacity_intake_cursor_and_unknown_evidence_policy(self):
        from omarchy_knowledge.health import check

        value = health_status()
        value["build_health"]["proof"]["object_count"] = 4000
        value["cursor_health"] = {
            "version": 1, "last_progress_at": "2026-09-17T07:00:00Z",
            "consecutive_drift_runs": 3,
        }
        value["intake_scan"]["counters"]["cursor_drifts"] = 1
        value["intake_scan"]["counters"]["page_fetches"] = 2
        value["intake_scan"]["counters"]["rows_returned"] = 0
        report = check(
            "production",
            connection_factory=FixedResponses(
                replace_status(healthy_responses(), value)
            ).factory,
            now="2026-09-17T12:00:00Z",
        )
        self.assertEqual(report["overall"], "warning")
        self.assertIn("capacity:proof_object_count", report["warnings"])
        self.assertIn("persistent_cursor_drift", report["warnings"])
        self.assertIn("stale_cursor_progress", report["warnings"])
        self.assertIn("current_cursor_drift", report["warnings"])
        self.assertIn("canonical_builder_object_visits", report["unknowns"])
        self.assertIn("upstream", report["unknowns"])

        value = health_status()
        value["receipt_coverage"]["receipted_records"] = 1
        value["status"] = "retry"
        report = check(
            "production",
            connection_factory=FixedResponses(
                replace_status(healthy_responses(), value)
            ).factory,
            now="2026-09-17T12:00:00Z",
        )
        self.assertEqual(report["intake"]["status"], "failure")
        self.assertEqual(set(report["failures"]), {
            "incomplete_receipt_coverage", "unhealthy_intake",
        })

        value = health_status()
        value["scan_truncated"] = False
        value["intake_scan"]["stop_reason"] = "fetch-limit"
        value["intake_scan"]["counters"].update({
            "page_fetches": 10, "rows_returned": 200,
            "rows_consumed": 200, "closed": 200,
        })
        report = check(
            "production",
            connection_factory=FixedResponses(
                replace_status(healthy_responses(), value)
            ).factory,
            now="2026-09-17T12:00:00Z",
        )
        self.assertIn("scan_truncated", report["warnings"])

    def test_recognized_old_status_keeps_new_capacity_explicitly_unknown(self):
        from omarchy_knowledge.health import check

        value = health_status()
        del value["build_health"]
        report = check(
            "production",
            connection_factory=FixedResponses(
                replace_status(healthy_responses(), value)
            ).factory,
            now="2026-09-17T12:00:00Z",
        )
        self.assertEqual(report["overall"], "ok")
        self.assertEqual(report["intake"]["format"], "current")
        self.assertIn("build_health", report["unknowns"])

        for field in ("intake_cursor", "cursor_health", "intake_scan"):
            del value[field]
        report = check(
            "production",
            connection_factory=FixedResponses(
                replace_status(healthy_responses(), value)
            ).factory,
            now="2026-09-17T12:00:00Z",
        )
        self.assertEqual(report["intake"]["format"], "legacy")
        self.assertIn("cursor_health", report["unknowns"])

    def test_cli_emits_report_and_exit_reflects_required_failures(self):
        from omarchy_knowledge.cli import main

        for overall, expected in (("ok", 0), ("warning", 0), ("failure", 1)):
            output = io.StringIO()
            report = {"version": 1, "deployment": "production",
                      "overall": overall}
            with patch("omarchy_knowledge.health.check", return_value=report), \
                    patch("sys.stdout", output):
                self.assertEqual(main([
                    "health", "--deployment", "production",
                ]), expected)
            self.assertEqual(json.loads(output.getvalue()), report)


if __name__ == "__main__":
    unittest.main()
