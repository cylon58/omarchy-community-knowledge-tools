"""Bounded, synthetic full-path growth baseline."""
import unittest


class GrowthBaselineTests(unittest.TestCase):
    def test_tiny_roundtrip_reports_real_counts_and_ranked_result(self):
        """Break caught: a stage is stubbed or its accepted artifacts are omitted."""
        from experiments.growth.baseline import run_baseline

        result = run_baseline(1)

        self.assertEqual(result["status"], "success", result)
        self.assertIsNone(result["failure_stage"])
        self.assertEqual(result["counts"], {
            "imports_requested": 1,
            "imports_accepted": 1,
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


if __name__ == "__main__":
    unittest.main()
