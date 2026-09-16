import json
from pathlib import Path
import sys
import tempfile
import textwrap
import unittest


class CompanionCommandTests(unittest.TestCase):
    def test_builds_only_reviewed_cli_argv(self):
        from omarchy_knowledge.companion import build_cli_argv

        self.assertEqual(
            build_cli_argv(
                "search",
                {"cache": "/tmp/cache", "query": "dock; rm -rf nope", "intent": "optional"},
            ),
            [
                "omarchy-knowledge", "search", "--cache", "/tmp/cache", "--query",
                "dock; rm -rf nope", "--intent", "optional", "--json",
            ],
        )
        with self.assertRaises(ValueError):
            build_cli_argv("apply", {"command": "anything"})
        with self.assertRaises(ValueError):
            build_cli_argv("search", {"cache": "/tmp/cache", "query": "x", "intent": "bogus"})
        with self.assertRaises(ValueError):
            build_cli_argv("search", {"cache": "/tmp/cache", "query": 42, "intent": "corrective"})

    def test_preview_builds_an_argv_array_without_shell_interpolation(self):
        from omarchy_knowledge.companion import build_cli_argv

        argv = build_cli_argv(
            "preview",
            {
                "draft": "/tmp/draft.json",
                "config": "/tmp/routes.json",
                "destination": "plugin",
                "title": "title $(touch nope)",
                "body": "body; false",
                "attribution": "local review",
            },
        )
        self.assertEqual(argv[0:2], ["omarchy-knowledge", "preview"])
        self.assertIn("title $(touch nope)", argv)
        self.assertNotIn("--approve", argv)

    def test_rejects_malformed_and_oversized_cli_output(self):
        from omarchy_knowledge.companion import render_cli_json

        malformed = render_cli_json("search", b"not json")
        oversized = render_cli_json("search", b"{" + b" " * 65536 + b"}")

        self.assertFalse(malformed["ok"])
        self.assertIn("valid JSON", malformed["status"])
        self.assertFalse(oversized["ok"])
        self.assertIn("size limit", oversized["status"])

        nonfinite = render_cli_json(
            "search",
            b'{"query":"q","intent":"corrective","data_revision":"r",'
            b'"trust":NaN,"results":[]}',
        )
        self.assertFalse(nonfinite["ok"])

    def test_search_rendering_is_plain_bounded_and_exposes_trust(self):
        from omarchy_knowledge.companion import render_cli_json

        payload = {
            "query": "keyboard",
            "intent": "corrective",
            "data_revision": "local-1",
            "trust": "attributed-claims-only",
            "results": [{
                "case_id": "case-1",
                "title": "<b>Key mapping</b>",
                "intent": "corrective",
                "domains": ["input"],
                "changes": [],
                "incompatible_change_ids": ["change-old"],
            }],
        }
        rendered = render_cli_json("search", json.dumps(payload).encode())

        self.assertTrue(rendered["ok"])
        self.assertIn("attributed-claims-only", rendered["display"])
        self.assertIn("<b>Key mapping</b>", rendered["display"])
        self.assertLessEqual(len(rendered["display"]), 12000)

    def test_process_runner_stops_on_output_and_duration_bounds(self):
        from omarchy_knowledge.companion import ProcessLimitError, run_bounded_process

        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / "emit.py"
            script.write_text("import sys\nsys.stdout.write('x' * 10000)\n", encoding="utf-8")
            with self.assertRaises(ProcessLimitError):
                run_bounded_process([sys.executable, str(script)], timeout_seconds=1, output_limit=128)

            script.write_text("import time\ntime.sleep(2)\n", encoding="utf-8")
            with self.assertRaises(ProcessLimitError):
                run_bounded_process([sys.executable, str(script)], timeout_seconds=0.05, output_limit=128)

    def test_missing_toolkit_is_a_helpful_panel_status(self):
        from omarchy_knowledge.companion import run_panel_request

        result = run_panel_request(
            "status", {"cache": "/tmp/missing"}, executable="definitely-not-installed-here"
        )
        self.assertFalse(result["ok"])
        self.assertIn("Install the Omarchy knowledge toolkit", result["status"])


if __name__ == "__main__":
    unittest.main()
