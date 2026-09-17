"""Bounded, synthetic full-path growth baseline."""
from contextlib import contextmanager, redirect_stdout
import io
import json
import unittest
from unittest.mock import patch


class GrowthBaselineTests(unittest.TestCase):
    def test_tiny_roundtrip_reports_real_counts_and_ranked_result(self):
        """Break caught: a stage is stubbed or its accepted artifacts are omitted."""
        from experiments.growth.baseline import run_baseline

        result = run_baseline(1)

        self.assertEqual(result["status"], "success", result)
        self.assertIsNone(result["failure_stage"])
        self.assertEqual(result["counts"], {
            "imports_requested": 1,
            "imports_completed": 1,
            "records": 5,
            "receipts": 5,
            "synthetic_account_ids": 1,
        })
        self.assertEqual(result["final_query"]["query"], "synthetic growth case 00000001")
        self.assertEqual(result["final_query"]["case_ids"], [
            "00000001-0000-4000-8000-000000000001",
        ])
        self.assertEqual(result["final_query"]["trust"], "canonical-api-receipts")
        self.assertTrue(result["final_query"]["failure_or_partial_visible"])
        self.assertTrue(result["final_query"]["community_event_review_required"])
        self.assertEqual(result["network"]["actual_requests"], 0)
        self.assertGreater(result["artifacts_bytes"]["proof_bundle"], 0)
        self.assertGreater(result["artifacts_bytes"]["static_distribution"], 0)

    def test_cleanup_failure_overrides_provisional_success_and_cli_fails(self):
        """Break caught: successful work hides a failed cleanup/reporting boundary."""
        from experiments.growth import baseline

        original = baseline.tempfile.TemporaryDirectory

        @contextmanager
        def cleanup_failure():
            with original(prefix="omarchy-growth-") as temporary:
                yield temporary
            raise OSError("private cleanup fixture detail")

        output = io.StringIO()
        with patch.object(baseline, "_workspace", cleanup_failure, create=True), redirect_stdout(output):
            exit_status = baseline.main(["--imports", "1"])
        result = json.loads(output.getvalue())

        self.assertEqual(exit_status, 1)
        self.assertEqual(result["status"], "failure")
        self.assertEqual(result["failure_stage"], "cleanup")
        self.assertEqual(result["failure_kind"], "OSError")
        self.assertNotIn("private cleanup fixture detail", output.getvalue())

    def test_late_deadline_overrides_completed_work(self):
        """Break caught: an alarm raised while contexts exit leaves status successful."""
        from experiments.growth import baseline

        @contextmanager
        def deadline_after_work():
            yield
            raise baseline._DeadlineExpired()

        with patch.object(baseline, "_bounded_runtime", deadline_after_work):
            result = baseline.run_baseline(1)

        self.assertEqual(result["status"], "failure")
        self.assertEqual(result["failure_stage"], "cleanup")
        self.assertEqual(result["failure_kind"], "DeadlineExpired")

    def test_interrupted_publish_counts_only_observed_completed_returns(self):
        """Break caught: an interrupted write is claimed as exact canonical acceptance."""
        from experiments.growth import baseline
        from omarchy_knowledge import coordinator

        original = coordinator.publish

        def interrupt_after_return(*args, **kwargs):
            result = original(*args, **kwargs)
            if result.status == "accepted":
                raise baseline._DeadlineExpired()
            return result

        with patch.object(coordinator, "publish", interrupt_after_return):
            result = baseline.run_baseline(1)

        self.assertEqual(result["status"], "failure")
        self.assertEqual(result["failure_stage"], "synthetic-imports")
        self.assertEqual(result["counts"]["imports_completed"], 0)
        self.assertNotIn("imports_accepted", result["counts"])
        self.assertIsNone(result["counts"]["records"])
        self.assertIsNone(result["counts"]["receipts"])


if __name__ == "__main__":
    unittest.main()
