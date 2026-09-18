"""Offline research keeps adverse evidence and release claims visible."""
import io
import json
from pathlib import Path
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

from tests.fixtures import CASE_ID, CHANGE_ID, EVENT_ID, REPORT_ID, case, change, event, report


class ResearchTests(unittest.TestCase):
    def _write_cache(self, cache, *, synced_at="2000-01-01T00:00:00Z", revision="a" * 40):
        (cache / "accepted.json").write_text(json.dumps({
            "repository": "example/ledger", "revision": revision,
            "synced_at": synced_at, "records": [case()],
        }), encoding="utf-8")

    def test_search_honors_optional_intent_and_aliases(self):
        optional = case()
        optional["payload"]["intent"] = "optional"
        optional["payload"]["title"] = "Number pad hotkeys lost"
        from omarchy_knowledge.research import search
        self.assertEqual(search([optional], "keypad shortcuts", intent="optional")[0]["id"], CASE_ID)

    def test_snapshot_without_timezone_is_treated_as_unknown_age(self):
        from omarchy_knowledge.cli import _snapshot_age

        try:
            age = _snapshot_age({"synced_at": "2026-09-18T12:00:00"})
        except TypeError:
            age = "raised"
        self.assertIsNone(age)

    def test_searches_hardware_identifiers(self):
        from omarchy_knowledge.research import search
        self.assertEqual(search([case(), change(), report()], "1234 abcd")[0]["id"], CASE_ID)

    def test_search_returns_no_useless_matches(self):
        from omarchy_knowledge.research import search
        self.assertEqual(search([case()], "unrelated bluetooth printer"), [])

    def test_literal_fallback_is_labeled_when_fts_is_unavailable(self):
        from omarchy_knowledge.research import search
        with patch("omarchy_knowledge.retrieval.sqlite3.connect", side_effect=__import__("sqlite3").OperationalError):
            found = search([case()], "dock")
        self.assertEqual(found[0]["search_method"], "case-insensitive-literal-fallback")

    def test_related_keeps_case_reports_and_negative_results(self):
        failed = report()
        failed["payload"].pop("change_id")
        failed["payload"]["result"] = "failure"
        from omarchy_knowledge.research import related
        detail = related([case(), change(), failed], CASE_ID)
        self.assertEqual(detail["reports"][0]["payload"]["result"], "failure")
        self.assertIn("NEGATIVE_REPORT_RESULT", detail["flags"])

    def test_search_keeps_negative_evidence_flag_on_displayed_case(self):
        failed = report()
        failed["payload"]["result"] = "failure"
        from omarchy_knowledge.research import search
        found = search([case(), change(), failed], "dock", limit=1)
        self.assertIn("NEGATIVE_REPORT_RESULT", found[0]["evidence_flags"])

    def test_readable_search_prints_evidence_flags(self):
        record = case()
        record["search_method"] = "local-fts5"
        record["evidence_flags"] = ["NEGATIVE_REPORT_RESULT", "REMOTE_INTERPRETER_EXECUTION"]
        from omarchy_knowledge.cli import _print
        output = io.StringIO()
        with redirect_stdout(output):
            _print({"source": {"revision": "a" * 40, "synced_at": "2026-09-18T00:00:00Z"},
                    "results": [record]}, False)
        self.assertIn("evidence flags: NEGATIVE_REPORT_RESULT, REMOTE_INTERPRETER_EXECUTION", output.getvalue())

    def test_related_follows_events_targeting_reports_and_events_beyond_shortlist(self):
        correction = event()
        correction["id"] = "dc3e61da-3b3c-4e4e-9fba-970ef606672f"
        correction["payload"] = {
            "event_kind": "correction", "reason": "The report was corrected.",
            "targets": [{"id": REPORT_ID, "type": "report"}],
            "relation": {"kind": "corrects", "from": {"id": REPORT_ID, "type": "report"}, "to": {"id": REPORT_ID, "type": "report"}},
        }
        # A correction cannot relate a record to itself under the validated schema;
        # use a second report as its source while retaining the report target.
        source = report(); source["id"] = "ba3e61da-3b3c-4e4e-9fba-970ef606672f"
        correction["payload"]["targets"].append({"id": source["id"], "type": "report"})
        correction["payload"]["relation"]["from"]["id"] = source["id"]
        # The dispute has no case/change/report target. It can only be reached
        # after the correction event is included.
        followup = event()
        followup["id"] = "bc3e61da-3b3c-4e4e-9fba-970ef606672f"
        followup["payload"] = {
            "event_kind": "dispute", "reason": "The correction is disputed.",
            "targets": [{"id": correction["id"], "type": "event"}, {"id": EVENT_ID, "type": "event"}],
            "relation": {"kind": "disputes", "from": {"id": correction["id"], "type": "event"}, "to": {"id": EVENT_ID, "type": "event"}},
        }
        withdrawal = event()
        withdrawal["id"] = "ac3e61da-3b3c-4e4e-9fba-970ef606672f"
        withdrawal["payload"] = {
            "event_kind": "withdrawal", "reason": "The disputed conclusion was withdrawn.",
            "targets": [{"id": followup["id"], "type": "event"}, {"id": correction["id"], "type": "event"}],
            "relation": {"kind": "withdraws", "from": {"id": followup["id"], "type": "event"}, "to": {"id": correction["id"], "type": "event"}},
        }
        from omarchy_knowledge.research import related
        detail = related([case(), change(), report(), source, correction, event(), followup, withdrawal], CASE_ID)
        self.assertEqual([row["id"] for row in detail["events"]], [correction["id"], EVENT_ID, followup["id"], withdrawal["id"]])
        self.assertIn("ADVERSE_EVENT:correction", detail["flags"])
        self.assertIn("ADVERSE_EVENT:dispute", detail["flags"])
        self.assertIn("ADVERSE_EVENT:withdrawal", detail["flags"])

    def test_release_claims_are_not_automatic_updates(self):
        from omarchy_knowledge.research import release_claims
        claim = release_claims([case(), change(), report(), event()], CASE_ID)[0]
        self.assertEqual(claim["event_id"], EVENT_ID)
        self.assertEqual(claim["claim_status"], "community-claim")
        self.assertTrue(claim["needs_official_check"])
        self.assertIn("INCOMPLETE_DIRECT_RELEASE_EVIDENCE", claim["flags"])

    def test_release_claims_require_a_matching_official_release_link(self):
        from omarchy_knowledge.research import release_claims
        link_cases = (
            (["https://github.com/omacom/omarchy/pull/1"], True),
            (["https://github.com/example/other/releases/tag/v4.1.2"], True),
            (["https://github.com/omacom/omarchy/releases/tag/v4.1.2"], False),
        )
        for links, incomplete in link_cases:
            with self.subTest(links=links):
                release_event = event()
                release_event["payload"]["supporting_links"] = links

                claim = release_claims([case(), change(), report(), release_event], CASE_ID)[0]

                self.assertEqual(
                    "INCOMPLETE_DIRECT_RELEASE_EVIDENCE" in claim["flags"],
                    incomplete,
                )

    def test_cli_related_show_returns_failures_and_release_claims(self):
        from omarchy_knowledge.cli import main
        failed = report()
        failed["payload"]["result"] = "failure"
        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary)
            (cache / "accepted.json").write_text(json.dumps({
                "repository": "example/ledger", "revision": "a" * 40,
                "synced_at": "2026-09-18T00:00:00Z",
                "records": [case(), change(), failed, event()],
            }), encoding="utf-8")
            output = io.StringIO()
            with redirect_stdout(output):
                code = main(["show", CASE_ID, "--cache", str(cache), "--related", "--json"])
        value = json.loads(output.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(value["related"]["reports"][0]["payload"]["result"], "failure")
        self.assertTrue(value["release_claims"][0]["needs_official_check"])

    def test_cli_related_show_explains_substantive_evidence_in_plain_text(self):
        from omarchy_knowledge.cli import main
        failed = report()
        failed["payload"]["result"] = "failure"
        failed["payload"]["actual_result"] = "Input still failed after resume."
        failed["payload"]["limitations"] = "Tested on one dock only."
        source = report()
        source["id"] = "ba3e61da-3b3c-4e4e-9fba-970ef606672f"
        correction = event()
        correction["id"] = "dc3e61da-3b3c-4e4e-9fba-970ef606672f"
        correction["payload"] = {
            "event_kind": "correction",
            "reason": "The original success result was corrected after another test.",
            "targets": [
                {"id": source["id"], "type": "report"},
                {"id": REPORT_ID, "type": "report"},
            ],
            "relation": {
                "kind": "corrects",
                "from": {"id": source["id"], "type": "report"},
                "to": {"id": REPORT_ID, "type": "report"},
            },
        }
        release_event = event()
        release_event["payload"]["supporting_links"] = [
            "https://github.com/omacom/omarchy/releases/tag/v4.1.2"
        ]
        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary)
            (cache / "accepted.json").write_text(json.dumps({
                "repository": "example/ledger", "revision": "a" * 40,
                "synced_at": "2026-09-18T00:00:00Z",
                "records": [case(), change(), failed, source, correction, release_event],
            }), encoding="utf-8")
            output = io.StringIO()
            with redirect_stdout(output):
                code = main(["show", CASE_ID, "--cache", str(cache), "--related"])

        rendered = output.getvalue()
        self.assertEqual(code, 0)
        self.assertIn("Proposed change: Restore the compositor input setting.", rendered)
        self.assertIn("Actual outcome: Input still failed after resume.", rendered)
        self.assertIn("Environment: x86_64, rc; observation source: firsthand.", rendered)
        self.assertIn("Limitations: Tested on one dock only.", rendered)
        self.assertIn("Event reason: The original success result was corrected after another test.", rendered)
        self.assertIn("Relationship: corrects", rendered)
        self.assertIn("Upstream URL: https://github.com/omacom/omarchy/pull/1", rendered)
        self.assertIn("Claimed fixed-in conditions: omarchy version >= 4.1.2-1 (arch).", rendered)

    def test_stale_search_refreshes_and_uses_the_new_snapshot(self):
        from omarchy_knowledge.cli import main

        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary)
            self._write_cache(cache)
            refreshed = {
                "repository": "example/ledger", "revision": "b" * 40,
                "synced_at": "2026-09-18T12:00:00Z", "records": [case()],
            }
            output = io.StringIO()
            with patch("omarchy_knowledge.cli.sync", return_value=refreshed) as refresh, redirect_stdout(output):
                try:
                    code = main([
                        "search", "dock", "--cache", str(cache),
                        "--repository", "example/ledger", "--json",
                    ])
                except SystemExit as error:
                    code = error.code

        self.assertEqual(code, 0)
        refresh.assert_called_once_with(str(cache), "example/ledger")
        self.assertEqual(json.loads(output.getvalue())["source"]["revision"], "b" * 40)

    def test_failed_stale_refresh_uses_prior_snapshot_with_visible_warning(self):
        from omarchy_knowledge.cli import main

        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary)
            self._write_cache(cache)
            output, errors = io.StringIO(), io.StringIO()
            with patch("omarchy_knowledge.cli.sync", side_effect=RuntimeError("offline")), \
                    redirect_stdout(output), redirect_stderr(errors):
                try:
                    code = main([
                        "search", "dock", "--cache", str(cache),
                        "--repository", "example/ledger", "--json",
                    ])
                except SystemExit as error:
                    code = error.code

        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue())["source"]["revision"], "a" * 40)
        self.assertIn("using validated snapshot", errors.getvalue())
        self.assertIn("offline", errors.getvalue())
        self.assertIn("old", errors.getvalue())

    def test_offline_search_bypasses_refresh_and_reports_snapshot_age(self):
        from omarchy_knowledge.cli import main

        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary)
            self._write_cache(cache)
            output, errors = io.StringIO(), io.StringIO()
            with patch("omarchy_knowledge.cli.sync") as refresh, redirect_stdout(output), redirect_stderr(errors):
                try:
                    code = main(["search", "dock", "--cache", str(cache), "--offline", "--json"])
                except SystemExit as error:
                    code = error.code

        self.assertEqual(code, 0)
        refresh.assert_not_called()
        self.assertEqual(json.loads(output.getvalue())["source"]["revision"], "a" * 40)
        self.assertIn("Offline search", errors.getvalue())
        self.assertIn("old", errors.getvalue())


if __name__ == "__main__":
    unittest.main()
