"""Static safety contract for the ordinary unprivileged toolkit workflow."""
from pathlib import Path
import unittest


class ContinuousIntegrationWorkflow(unittest.TestCase):
    def test_workflow_is_read_only_pinned_and_runs_tests_plus_synthetic_demo(self):
        workflow = (Path(__file__).parents[1] / ".github/workflows/validate.yml").read_text(encoding="utf-8")
        self.assertIn("permissions:\n  contents: read", workflow)
        self.assertIn("actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1", workflow)
        self.assertIn("actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97", workflow)
        self.assertIn("persist-credentials: false", workflow)
        self.assertIn('python-version: ["3.11", "3.13"]', workflow)
        self.assertIn("python-version: ${{ matrix.python-version }}", workflow)
        self.assertIn("python -m venv", workflow)
        self.assertIn("python -m unittest discover -s tests -q", workflow)
        self.assertIn("python -m examples.synthetic_demo", workflow)
        for forbidden in ("pull_request_target:", "schedule:", "workflow_run:", "secrets.",
                          "cache:", "upload-artifact", "release:", "deploy"):
            self.assertNotIn(forbidden, workflow.casefold())


if __name__ == "__main__":
    unittest.main()
