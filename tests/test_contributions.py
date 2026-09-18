import json
import io
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from omarchy_knowledge.cli import main
from omarchy_knowledge.contributions import draft, preview, render_sharing_note
from tests.fixtures import case, change, event, report


class ContributionDraftTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def test_multi_record_draft_preserves_exact_public_files(self):
        rows = [case(), change(), report()]
        result = draft(rows, [], self.root / "draft")
        self.assertEqual(result["record_count"], 3)
        expected = json.dumps(case(), ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        path = self.root / "draft" / "records" / "cases" / f"{case()['id']}.json"
        self.assertEqual(path.read_text(encoding="utf-8"), expected)
        self.assertEqual(result["files"][0]["path"], f"records/cases/{case()['id']}.json")

    def test_draft_rejects_duplicate_existing_id_and_occupied_output(self):
        with self.assertRaisesRegex(ValueError, "Duplicate record ID"):
            draft([case()], [case()], self.root / "duplicate")
        occupied = self.root / "occupied"
        occupied.mkdir()
        marker = occupied / "mine"
        marker.write_text("preserve", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "already exists"):
            draft([case()], [], occupied)
        self.assertEqual(marker.read_text(encoding="utf-8"), "preserve")

    def test_record_ids_are_allowed_but_device_uuid_in_text_is_rejected(self):
        row = case()
        row["payload"]["observed"] = "Device UUID 550e8400-e29b-41d4-a716-446655440000 stopped responding"
        with self.assertRaisesRegex(ValueError, "unique device identifier"):
            draft([row], [], self.root / "private")
        clean = draft([case()], [], self.root / "clean")
        self.assertEqual(clean["record_count"], 1)

    def test_private_values_in_settings_and_urls_are_rejected(self):
        setting = report()
        setting["payload"]["environment"]["settings"][0]["value"] = "serial=ABC123456789"
        with self.assertRaises(ValueError):
            draft([case(), change(), setting], [], self.root / "setting")
        linked = event()
        linked["payload"]["supporting_links"] = ["https://example.test/devices/550e8400-e29b-41d4-a716-446655440000"]
        with self.assertRaises(ValueError):
            draft([case(), change(), report(), linked], [], self.root / "url")

    def test_percent_encoded_device_uuid_in_record_url_is_rejected(self):
        linked = event()
        linked["payload"]["supporting_links"] = [
            "https://github.com/omacom/omarchy/releases/tag/v4.1.2",
            "https://example.test/devices/550e8400%2De29b%2D41d4%2Da716%2D446655440000"
        ]
        with self.assertRaisesRegex(ValueError, "unique device identifier"):
            draft([case(), change(), report(), linked], [], self.root / "encoded-url")

    def test_new_release_claim_needs_official_release_link(self):
        with self.assertRaisesRegex(ValueError, "official release URL"):
            draft([event()], [case(), change(), report()], self.root / "claim")
        released = event()
        released["payload"]["supporting_links"] = [
            "https://github.com/omacom/omarchy/releases/tag/v4.1.2"
        ]
        self.assertEqual(
            draft([released], [case(), change(), report()], self.root / "released")["record_count"],
            1,
        )

    def test_preview_is_exact_and_note_preserves_failure_uncertainty_and_details(self):
        outcome = report()
        outcome["provenance"] = {
            "kind": "journal-import",
            "sources": ["https://github.com/example/public/issues/7"],
        }
        outcome["payload"]["environment"]["origin"] = "journal-import"
        outcome["payload"]["environment"]["components"] = [
            {
                "alias": "dock",
                "selector": {
                    "kind": "hardware",
                    "role": "dock",
                    "vendor": "Acme",
                    "model": "Dock 7",
                },
                "version": "2.3.4",
                "version_scheme": "vendor",
            }
        ]
        outcome["payload"]["environment"]["topology"] = {"nodes": ["dock"], "edges": []}
        outcome["payload"]["result"] = "failure"
        outcome["payload"]["actual_result"] = "Input still failed after `hyprctl reload`."
        outcome["payload"]["root_cause"] = {
            "assessment": "suspected",
            "rationale": "The dock/keyboard interaction may be involved; it is not proven.",
        }
        outcome["payload"]["limitations"] = "Only version 2.3.4 was tried."
        draft([case(), change(), outcome], [], self.root / "draft")
        result = preview(
            self.root / "draft", "example/ledger", title="Dock input observation",
            body="Observed after resume.", attribution="@example",
        )
        exact = (self.root / "draft" / "records" / "reports" / f"{outcome['id']}.json").read_text()
        self.assertEqual(result["repository"], "example/ledger")
        self.assertFalse(result["publication_performed"])
        report_file = next(item for item in result["files"] if "/reports/" in item["path"])
        self.assertEqual(report_file["content"], exact)
        note = render_sharing_note(result)
        for heading in (
            "What happened", "What we tried", "What happened afterward",
            "Where this will be shared", "Public name", "Privacy check",
        ):
            self.assertIn(heading, note)
        for fact in (
            "failure", "suspected", "not proven", "journal-import", "Acme", "Dock 7",
            "2.3.4", "hyprctl reload", "https://github.com/example/public/issues/7",
        ):
            self.assertIn(fact, note)
        self.assertIn("exact JSON records", note)
        self.assertIn("agent must inspect", note)
        self.assertNotIn("all personal information has been removed", note.lower())
        self.assertNotIn(outcome["id"], note)
        self.assertNotIn(case()["id"], note)
        self.assertNotIn("root cause / assessment", note.lower())
        self.assertNotIn("case_id", note)
        self.assertLess(len(note.split()), 700)

    def test_optional_preference_is_not_described_as_a_fix(self):
        optional_case = case()
        optional_case["payload"]["intent"] = "optional"
        optional_case["payload"]["expectation"] = {"basis": "user-requested", "text": "Use faster repeat"}
        optional_change = change()
        optional_change["payload"]["intent"] = "optional"
        draft([optional_case, optional_change], [], self.root / "optional")
        result = preview(
            self.root / "optional", "example/ledger", title="Input preference",
            body="Optional preference.", attribution="@example",
        )
        note = render_sharing_note(result)
        self.assertIn("optional preference", note.lower())
        self.assertNotIn("confirmed fix", note.lower())

    def test_preview_rejects_private_pr_text_and_keeps_public_model_identifiers(self):
        draft([case(), change(), report()], [], self.root / "draft")
        with self.assertRaises(ValueError):
            preview(
                self.root / "draft", "example/ledger", title="Contact me at person@example.test",
                body="Observed after resume.", attribution="@example",
            )
        result = preview(
            self.root / "draft", "example/ledger", title="Dock 1234:abcd",
            body="Public USB product identifiers are relevant.", attribution="@example",
        )
        self.assertIn("1234", render_sharing_note(result))

    def test_cli_draft_and_preview_remain_local(self):
        source = self.root / "source"
        draft([case(), change(), report()], [], source)
        existing = self.root / "existing"
        existing.mkdir()
        target = self.root / "cli-draft"
        output = io.StringIO()
        with redirect_stdout(output):
            code = main(["draft", str(source / "records"), str(target), "--existing", str(existing), "--json"])
        self.assertEqual(code, 0)
        self.assertTrue(target.is_dir())
        with redirect_stdout(output):
            code = main([
                "preview", str(target), "example/ledger", "--title", "Dock input",
                "--body", "Observed after resume.", "--attribution", "@example",
            ])
        self.assertEqual(code, 0)
        self.assertIn("This preview did not publish", output.getvalue())

    def test_cli_drafts_and_previews_one_report_against_explicit_existing_context(self):
        accepted = self.root / "accepted"
        draft([case(), change()], [], accepted)
        candidate = self.root / "candidate"
        failed = report()
        failed["payload"]["result"] = "failure"
        failed["payload"]["actual_result"] = "Input still failed after resume."
        draft([failed], [case(), change()], candidate)
        target = self.root / "report-draft"

        output = io.StringIO()
        with redirect_stdout(output):
            code = main([
                "draft", str(candidate / "records"), str(target),
                "--existing", str(accepted / "records"), "--json",
            ])

        self.assertEqual(code, 0)
        drafted = json.loads(output.getvalue())
        expected_path = f"records/reports/{failed['id']}.json"
        self.assertEqual(drafted["record_count"], 1)
        self.assertEqual([item["path"] for item in drafted["files"]], [expected_path])
        self.assertEqual(
            [path.relative_to(target).as_posix() for path in target.rglob("*.json")],
            [expected_path],
        )

        output = io.StringIO()
        with redirect_stdout(output):
            code = main([
                "preview", str(target), "example/ledger",
                "--existing", str(accepted / "records"),
                "--title", "Dock report", "--body", "Observed after resume.",
                "--attribution", "@example", "--json",
            ])

        self.assertEqual(code, 0)
        shown = json.loads(output.getvalue())
        self.assertEqual([item["path"] for item in shown["files"]], [expected_path])
        self.assertEqual([record["type"] for record in shown["records"]], ["report"])

        output = io.StringIO()
        with redirect_stdout(output):
            code = main([
                "preview", str(target), "example/ledger",
                "--existing", str(accepted / "records"),
                "--title", "Dock report", "--body", "Observed after resume.",
                "--attribution", "@example",
            ])

        rendered = output.getvalue()
        self.assertEqual(code, 0)
        self.assertIn("Result: failure.", rendered)
        self.assertIn("Actual outcome: Input still failed after resume.", rendered)
        self.assertNotIn(case()["payload"]["title"], rendered)
        self.assertNotIn(change()["payload"]["explanation"], rendered)

        for context_arguments in ((), ("--existing", str(self.root / "empty"))):
            with self.subTest(context_arguments=context_arguments):
                (self.root / "empty").mkdir(exist_ok=True)
                errors = io.StringIO()
                with redirect_stderr(errors):
                    code = main([
                        "preview", str(target), "example/ledger", *context_arguments,
                        "--title", "Dock report", "--body", "Observed after resume.",
                        "--attribution", "@example", "--json",
                    ])
                self.assertEqual(code, 1)
                self.assertIn("Invalid case reference", errors.getvalue())


if __name__ == "__main__":
    unittest.main()
