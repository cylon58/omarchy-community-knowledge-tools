"""Bounded end-to-end fair-intake service evidence."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


class FairIntakeServiceEvidence(unittest.TestCase):
    def test_one_candidate_beyond_two_hundred_closed_rows_is_published_boundedly(self):
        """The service persists progress, then admits one later real candidate."""
        from experiments.growth.fair_intake_service import run_queue

        result = run_queue()

        self.assertEqual(result["status"], "success", result)
        self.assertEqual(result["configuration"], {
            "lifetime_pull_requests": 206,
            "closed_pull_requests": 205,
            "eligible_pull_requests": 1,
            "actual_imports": 1,
        })
        self.assertEqual(result["limits"], {
            "page_fetches_per_run": 10,
            "returned_rows_per_run": 200,
            "candidate_evaluations_per_run": 20,
            "plan_artifact_bytes": 1024 * 1024,
            "publish_artifact_bytes": 64 * 1024,
            "public_status_bytes": 1024 * 1024,
            "adapter_calls_per_job": 512,
            "adapter_response_bytes_per_job": 32 * 1024 * 1024,
            "adapter_seconds_per_job": 180,
        })
        first, second = result["scheduled_runs"]
        self.assertEqual((first["scan_outcome"], first["stop_reason"]),
                         ("no-eligible", "fetch-limit"))
        self.assertEqual(first["counters"], {
            "page_fetches": 10, "rows_returned": 200,
            "rows_consumed": 200, "evaluations": 0, "closed": 200,
            "imported": 0, "rejected": 0, "not_ready": 0, "plans": 0,
            "cursor_drifts": 0,
        })
        self.assertEqual((second["scan_outcome"], second["stop_reason"]),
                         ("planned", "plan"))
        self.assertEqual(second["counters"], {
            "page_fetches": 2, "rows_returned": 26,
            "rows_consumed": 6, "evaluations": 1, "closed": 5,
            "imported": 0, "rejected": 0, "not_ready": 0, "plans": 1,
            "cursor_drifts": 0,
        })
        for run in (first, second):
            self.assertLessEqual(run["plan_artifact_bytes"],
                                 result["limits"]["plan_artifact_bytes"])
            self.assertLessEqual(run["publish_artifact_bytes"],
                                 result["limits"]["publish_artifact_bytes"])
            self.assertLessEqual(run["public_status_bytes"],
                                 result["limits"]["public_status_bytes"])
            self.assertEqual(run["entrypoint_exit_codes"], {
                "plan": 0, "publish": 0, "build": 0,
            })
            self.assertEqual(set(run["jobs"]), {
                "planning", "publish", "build",
            })
            for job in run["jobs"].values():
                self.assertLessEqual(
                    job["adapter_calls"],
                    result["limits"]["adapter_calls_per_job"],
                )
                self.assertLessEqual(
                    job["adapter_response_bytes"],
                    result["limits"]["adapter_response_bytes_per_job"],
                )
        self.assertEqual(result["operations"]["canonical_mutations"], 2)
        self.assertEqual(result["operations"]["pages_publications"], {
            "legacy_bootstrap": 1, "cursor_only": 1, "accepted_import": 1,
        })
        bootstrap = result["initial_legacy_bootstrap"]
        self.assertEqual(bootstrap["modeled_prior"],
                         "validated-legacy-from-known-initial-canonical")
        self.assertEqual((bootstrap["canonical_mutations"],
                          bootstrap["imported_records"],
                          bootstrap["pages_publications"]), (0, 0, 1))
        self.assertEqual(set(bootstrap["jobs"]), {
            "planning", "publish", "build",
        })
        for job in bootstrap["jobs"].values():
            self.assertLessEqual(
                job["adapter_calls"],
                result["limits"]["adapter_calls_per_job"],
            )
            self.assertLessEqual(
                job["adapter_response_bytes"],
                result["limits"]["adapter_response_bytes_per_job"],
            )
        self.assertEqual(result["final"]["records"], 5)
        self.assertEqual(result["final"]["receipts"], 5)
        self.assertEqual(result["final"]["cursor_cycle"], 2)
        self.assertEqual(result["fixture"]["contract_violations"], 0)
        self.assertEqual(set(result["source_sha256"]), {
            "experiments/growth/baseline.py",
            "experiments/growth/fair_intake_service.py",
            "experiments/growth/gates.py",
            "omarchy_knowledge/coordinator.py",
            "omarchy_knowledge/distribution.py",
            "omarchy_knowledge/fair_intake.py",
            "omarchy_knowledge/github_native.py",
            "omarchy_knowledge/intake_status.py",
            "omarchy_knowledge/service.py",
        })
        self.assertTrue(all(len(value) == 64
                            for value in result["source_sha256"].values()))

    def test_failed_build_or_pre_replace_deploy_keeps_complete_prior_projection(self):
        """Proposed progress is retried from the last atomically published state."""
        from experiments.growth import gates
        from experiments.growth.fair_intake_service import (
            _run_scheduled_entrypoints,
        )
        from omarchy_knowledge.coordinator import Policy

        with tempfile.TemporaryDirectory(
                prefix="fair-intake-failure-") as temporary:
            root = Path(temporary)
            with gates._NativeFixture(root / "fixture") as fixture:
                policy = Policy("a" * 40, "b" * 40)
                gates._bootstrap_fixture_intake(fixture, policy, root)
                for number in range(1, 22):
                    fixture.add_closed_pull(number)
                prior = deepcopy(fixture.pages_state)

                with patch(
                        "omarchy_knowledge.distribution.build_site",
                        side_effect=ValueError("synthetic build failure")):
                    failed_build = _run_scheduled_entrypoints(
                        fixture, policy, root / "failed-build", run_id=901,
                        now="2026-09-17T19:00:00Z", expected_build_exit=2,
                    )
                self.assertEqual(failed_build["published"]["selected_action"],
                                 "after")
                self.assertNotEqual(failed_build["published"]["cursor"],
                                    failed_build["published"]["before_cursor"])
                self.assertEqual(fixture.pages_state, prior)

                retry_after_build = _run_scheduled_entrypoints(
                    fixture, policy, root / "retry-build", run_id=902,
                    now="2026-09-17T19:01:00Z",
                )
                self.assertEqual(retry_after_build["planned"]["before_cursor"],
                                 failed_build["published"]["before_cursor"])

                with patch.object(
                        fixture, "publish_reconciliation",
                        side_effect=OSError("synthetic deploy failure")):
                    with self.assertRaises(OSError):
                        fixture.publish_reconciliation(
                            retry_after_build["proof"],
                            retry_after_build["public_status"],
                        )
                self.assertEqual(fixture.pages_state, prior)

                retry_after_deploy = _run_scheduled_entrypoints(
                    fixture, policy, root / "retry-deploy", run_id=903,
                    now="2026-09-17T19:02:00Z",
                )
                self.assertEqual(retry_after_deploy["planned"]["before_cursor"],
                                 retry_after_build["published"]["before_cursor"])
                self.assertEqual(fixture.pages_state, prior)


if __name__ == "__main__":
    unittest.main()
