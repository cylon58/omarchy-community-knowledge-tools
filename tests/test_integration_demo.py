"""Runnable all-synthetic integration demonstration."""
import json
from pathlib import Path
import subprocess
import sys
import unittest


class SyntheticIntegrationDemo(unittest.TestCase):
    def test_canonical_admission_cache_query_and_projection_scenarios(self):
        repository = Path(__file__).parents[1]
        result = subprocess.run(
            [sys.executable, "-m", "examples.synthetic_demo"],
            cwd=repository, text=True, capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        output = json.loads(result.stdout)
        self.assertTrue(output["synthetic_fixture_only"])
        self.assertEqual(output["admission"]["decision"], "accept")
        self.assertTrue(output["admission"]["immutable_tree_binding"])
        self.assertEqual(output["snapshot"]["data_revision"], output["admission"]["evaluated_commit_oid"])
        self.assertEqual(output["snapshot"]["record_count"], 9)
        self.assertEqual(output["query"]["default_intents"], ["corrective"])
        self.assertEqual(output["query"]["unknown_topology"], "insufficient-information")
        self.assertEqual(output["evidence"]["authenticated_account_counts"]["success"], 3)
        self.assertEqual(output["evidence"]["authenticated_account_counts"]["failure"], 1)
        self.assertEqual(output["upstream"]["matching_channel"]["action"], "prefer-update")
        self.assertEqual(output["upstream"]["stale"]["upstream_state"], "stale")
        self.assertEqual(output["upstream"]["reverted"]["upstream_state"], "reverted")
        self.assertIn("not real evidence", output["disclaimer"].lower())


if __name__ == "__main__":
    unittest.main()
