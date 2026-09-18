import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from omarchy_knowledge.intake import GitHubHostedAdapter, accept_pr, validate_pr
from omarchy_knowledge.records import load_records
from tests.fixtures import CASE_ID, case, change, report
from tests.test_pr_validation import ContributionRepository, git, record_path


SECOND_CASE_ID = "dd3e61da-3b3c-4e4e-9fba-970ef606672f"
THIRD_CASE_ID = "ee3e61da-3b3c-4e4e-9fba-970ef606672f"


class LocalGitHubAdapter:
    """Fake only hosted metadata/transport; acceptance still uses real local Git."""

    configured_repository_id = "42"

    def __init__(self, root, source, heads):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.source = source
        self.remote = self.root / "remote.git"
        git(self.root, "init", "--bare", "--initial-branch=main", str(self.remote))
        git(self.source.path, "push", str(self.remote), "main")
        self.heads = dict(heads)
        for number, head in self.heads.items():
            git(self.source.path, "push", str(self.remote), f"{head}:refs/pull/{number}/head")
        self.pull_calls = 0
        self.fetch_calls = 0
        self.push_calls = 0
        self.repository_id = 42
        self.base_ref = "main"
        self.state = "open"
        self.draft = False
        self.merged = False
        self.changed_head_on_call = None
        self.close_merged_on_call = None
        self.fail_pull_on_call = None
        self.fail_fetch_main_on_call = None
        self.fetch_main_calls = 0
        self.fail_push = False
        self.lose_push_ack = False
        self.race_rows = []
        self.after_push_rows = []

    def repository_metadata(self, repository):
        return {"id": self.repository_id, "full_name": repository}

    def pull_metadata(self, repository, number):
        self.pull_calls += 1
        if self.fail_pull_on_call == self.pull_calls:
            raise RuntimeError("synthetic metadata outage")
        head = self.heads[number]
        if self.changed_head_on_call == self.pull_calls:
            head = "f" * 40
        state = self.state
        merged = self.merged
        if self.close_merged_on_call == self.pull_calls:
            state = "closed"
            merged = True
        return {
            "number": number,
            "state": state,
            "draft": self.draft,
            "base": {"ref": self.base_ref},
            "head": {"sha": head},
            "title": "Synthetic data contribution",
            "body": "Adds a synthetic observation.",
            "user": {"login": "fixture-contributor"},
            "html_url": f"https://github.com/{repository}/pull/{number}",
            "merged": merged,
        }

    def fetch_refs(self, objects, repository, number):
        self.fetch_calls += 1
        base = self.fetch_main(objects, repository)
        subprocess.run(
            ["git", "--git-dir", str(objects), "fetch", "--no-tags", str(self.remote), f"refs/pull/{number}/head"],
            check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        head = subprocess.run(
            ["git", "--git-dir", str(objects), "rev-parse", "FETCH_HEAD^{commit}"],
            check=True, stdout=subprocess.PIPE,
        ).stdout.decode().strip()
        return base, head

    def fetch_main(self, objects, repository):
        self.fetch_main_calls += 1
        if self.fail_fetch_main_on_call == self.fetch_main_calls:
            raise RuntimeError("synthetic post-push fetch outage")
        subprocess.run(
            ["git", "--git-dir", str(objects), "fetch", "--no-tags", str(self.remote), "refs/heads/main"],
            check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        return subprocess.run(
            ["git", "--git-dir", str(objects), "rev-parse", "FETCH_HEAD^{commit}"],
            check=True, stdout=subprocess.PIPE,
        ).stdout.decode().strip()

    def _advance_main(self, row):
        clone = self.root / f"race-{self.push_calls}"
        git(self.root, "clone", str(self.remote), str(clone))
        git(clone, "config", "user.name", "Concurrent Fixture")
        git(clone, "config", "user.email", "456+concurrent@users.noreply.github.com")
        path = clone / record_path(row)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(row, sort_keys=True), encoding="utf-8")
        git(clone, "add", "records")
        git(clone, "commit", "-m", "concurrent record")
        git(clone, "push", str(self.remote), "main")

    def push_main(self, objects, repository, merge_commit):
        self.push_calls += 1
        if self.race_rows:
            self._advance_main(self.race_rows.pop(0))
        if self.fail_push:
            return False
        completed = subprocess.run(
            ["git", "--git-dir", str(objects), "push", str(self.remote),
             f"{merge_commit}:refs/heads/main"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        if completed.returncode == 0 and self.after_push_rows:
            self._advance_main(self.after_push_rows.pop(0))
        if completed.returncode == 0 and self.lose_push_ack:
            return False
        return completed.returncode == 0

    def main_oid(self):
        return git(self.remote, "rev-parse", "refs/heads/main").stdout.decode().strip()

    def main_records(self):
        checkout = self.root / f"published-{self.push_calls}-{self.fetch_calls}"
        git(self.root, "clone", str(self.remote), str(checkout))
        return load_records(checkout / "records")


class HostedIntakeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def one_commit_pull(self, number=1, added=None):
        source = ContributionRepository(self.root / f"source-{number}", [case()])
        source.branch(f"pr-{number}")
        source.commit_rows(added or [change(), report()])
        adapter = LocalGitHubAdapter(self.root / f"hosted-{number}", source, {number: source.oid()})
        return source, adapter

    def test_accepts_with_merge_commit_and_reports_delayed_pr_status_honestly(self):
        source, adapter = self.one_commit_pull()
        old_main = git(source.path, "rev-parse", "main").stdout.decode().strip()
        result = accept_pr("example/ledger", 1, _adapter=adapter)
        self.assertEqual(result["status"], "accepted")
        self.assertTrue(result["published"])
        self.assertFalse(result["pr_merged"])
        self.assertEqual(result["pr_status"], "unconfirmed")
        parents = git(adapter.remote, "rev-list", "--parents", "-n", "1", "main").stdout.decode().split()
        self.assertEqual(parents[1:], [old_main, source.oid()])
        self.assertEqual({row["type"] for row in adapter.main_records()}, {"case", "change", "report"})

    def test_read_only_validation_job_never_publishes(self):
        _source, adapter = self.one_commit_pull()
        before = adapter.main_oid()
        result = validate_pr("example/ledger", 1, _adapter=adapter)
        self.assertEqual(result["status"], "valid")
        self.assertEqual(result["added_record_count"], 2)
        self.assertEqual(adapter.push_calls, 0)
        self.assertEqual(adapter.main_oid(), before)

    def test_reports_merged_only_when_github_confirms_same_head(self):
        _source, adapter = self.one_commit_pull()
        adapter.close_merged_on_call = 3
        result = accept_pr("example/ledger", 1, _adapter=adapter)
        self.assertEqual(result["status"], "accepted")
        self.assertTrue(result["published"])
        self.assertTrue(result["pr_merged"])
        self.assertEqual(result["pr_status"], "merged")

    def test_changed_head_stays_pending_without_publication(self):
        source, adapter = self.one_commit_pull()
        before = adapter.main_oid()
        adapter.changed_head_on_call = 2
        result = accept_pr("example/ledger", 1, _adapter=adapter)
        self.assertEqual(result["status"], "pending")
        self.assertIn("head changed", result["reason"].lower())
        self.assertEqual(adapter.main_oid(), before)
        self.assertEqual(adapter.push_calls, 0)

    def test_two_stale_valid_additions_are_merged_without_overwriting_history(self):
        source = ContributionRepository(self.root / "source", [case()])
        base = source.oid()
        source.branch("pr-1")
        second = case(SECOND_CASE_ID)
        source.commit_rows([second])
        head_one = source.oid()
        git(source.path, "checkout", "-b", "pr-2", base)
        third = case(THIRD_CASE_ID)
        source.commit_rows([third])
        head_two = source.oid()
        adapter = LocalGitHubAdapter(self.root / "hosted", source, {1: head_one, 2: head_two})
        first = accept_pr("example/ledger", 1, _adapter=adapter)
        first_main = adapter.main_oid()
        second_result = accept_pr("example/ledger", 2, _adapter=adapter)
        self.assertEqual((first["status"], second_result["status"]), ("accepted", "accepted"))
        self.assertEqual({row["id"] for row in adapter.main_records()}, {CASE_ID, SECOND_CASE_ID, THIRD_CASE_ID})
        self.assertEqual(git(adapter.remote, "merge-base", "--is-ancestor", first_main, "main").returncode, 0)

    def test_acceptance_verification_allows_a_newer_descendant_of_its_merge(self):
        _source, adapter = self.one_commit_pull()
        adapter.after_push_rows = [case(SECOND_CASE_ID)]
        result = accept_pr("example/ledger", 1, _adapter=adapter)
        self.assertEqual(result["status"], "accepted")
        self.assertTrue(result["published"])
        self.assertEqual({row["id"] for row in adapter.main_records()}, {CASE_ID, SECOND_CASE_ID, change()["id"], report()["id"]})

    def test_base_race_retries_and_revalidates_complete_corpus(self):
        source, adapter = self.one_commit_pull(added=[case(SECOND_CASE_ID)])
        adapter.race_rows = [case(THIRD_CASE_ID)]
        result = accept_pr("example/ledger", 1, _adapter=adapter)
        self.assertEqual(result["status"], "accepted")
        self.assertEqual(result["attempts"], 2)
        self.assertEqual(adapter.fetch_calls, 2)
        self.assertEqual({row["id"] for row in adapter.main_records()}, {CASE_ID, SECOND_CASE_ID, THIRD_CASE_ID})

    def test_changed_main_is_fully_revalidated_before_retry_push(self):
        source, adapter = self.one_commit_pull(added=[case(SECOND_CASE_ID)])
        invalid = change()
        invalid["payload"]["case_id"] = "ff3e61da-3b3c-4e4e-9fba-970ef606672f"
        adapter.race_rows = [invalid]
        result = accept_pr("example/ledger", 1, _adapter=adapter)
        self.assertEqual(result["status"], "rejected")
        self.assertFalse(result["published"])
        self.assertEqual(adapter.fetch_calls, 2)
        paths = git(adapter.remote, "ls-tree", "-r", "--name-only", "main").stdout.decode()
        self.assertNotIn(SECOND_CASE_ID, paths)

    def test_base_races_stop_after_three_complete_attempts(self):
        source, adapter = self.one_commit_pull(added=[case(SECOND_CASE_ID)])
        adapter.race_rows = [
            case(f"{number:08x}-0000-4000-8000-{number:012x}")
            for number in (10, 11, 12)
        ]
        result = accept_pr("example/ledger", 1, _adapter=adapter)
        self.assertEqual(result["status"], "pending")
        self.assertEqual(result["attempts"], 3)
        self.assertEqual(adapter.push_calls, 3)
        self.assertNotIn(SECOND_CASE_ID, {row["id"] for row in adapter.main_records()})

    def test_policy_conflict_and_failed_push_never_publish(self):
        for variant in ("identity", "configured-identity", "base", "draft", "push"):
            with self.subTest(variant=variant), tempfile.TemporaryDirectory(dir=self.root) as child:
                source = ContributionRepository(Path(child) / "source", [case()])
                source.branch()
                source.commit_rows([change()])
                adapter = LocalGitHubAdapter(Path(child) / "hosted", source, {1: source.oid()})
                before = adapter.main_oid()
                if variant == "identity":
                    adapter.repository_id = 43
                elif variant == "configured-identity":
                    adapter.configured_repository_id = "not-numeric"
                elif variant == "base":
                    adapter.base_ref = "develop"
                elif variant == "draft":
                    adapter.draft = True
                else:
                    adapter.fail_push = True
                result = accept_pr("example/ledger", 1, _adapter=adapter)
                self.assertIn(result["status"], {"rejected", "pending"})
                self.assertFalse(result["published"])
                self.assertEqual(adapter.main_oid(), before)

    def test_merge_conflict_does_not_publish(self):
        source, adapter = self.one_commit_pull(added=[case(SECOND_CASE_ID)])
        conflicting = case(SECOND_CASE_ID)
        conflicting["payload"]["observed"] = "A different concurrent observation"
        adapter.race_rows = [conflicting]
        result = accept_pr("example/ledger", 1, _adapter=adapter)
        self.assertEqual(result["status"], "rejected")
        self.assertFalse(result["published"])
        self.assertEqual(next(row for row in adapter.main_records() if row["id"] == SECOND_CASE_ID), conflicting)

    def test_duplicate_delivery_is_idempotent(self):
        source, adapter = self.one_commit_pull()
        first = accept_pr("example/ledger", 1, _adapter=adapter)
        first_main = adapter.main_oid()
        second = accept_pr("example/ledger", 1, _adapter=adapter)
        self.assertEqual(first["status"], "accepted")
        self.assertEqual(second["status"], "accepted")
        self.assertTrue(second["already_accepted"])
        self.assertIs(second["published"], True)
        self.assertEqual(adapter.main_oid(), first_main)

    def test_read_only_validation_reports_verified_existing_ancestry_as_published(self):
        _source, adapter = self.one_commit_pull()
        accepted = accept_pr("example/ledger", 1, _adapter=adapter)
        published_main = adapter.main_oid()

        result = validate_pr("example/ledger", 1, _adapter=adapter)

        self.assertEqual(accepted["status"], "accepted")
        self.assertEqual(result["status"], "accepted")
        self.assertIs(result["published"], True)
        self.assertTrue(result["already_accepted"])
        self.assertEqual(result["accepted_commit"], published_main)

    def test_read_only_validation_recognizes_a_closed_already_accepted_pull(self):
        _source, adapter = self.one_commit_pull()
        accepted = accept_pr("example/ledger", 1, _adapter=adapter)
        adapter.state = "closed"
        adapter.merged = True

        result = validate_pr("example/ledger", 1, _adapter=adapter)

        self.assertEqual(accepted["status"], "accepted")
        self.assertEqual(result["status"], "accepted")
        self.assertIs(result["published"], True)
        self.assertTrue(result["already_accepted"])

    def test_duplicate_acceptance_recognizes_a_closed_already_accepted_pull(self):
        _source, adapter = self.one_commit_pull()
        first = accept_pr("example/ledger", 1, _adapter=adapter)
        published_main = adapter.main_oid()
        adapter.state = "closed"
        adapter.merged = True

        second = accept_pr("example/ledger", 1, _adapter=adapter)

        self.assertEqual(first["status"], "accepted")
        self.assertEqual(second["status"], "accepted")
        self.assertIs(second["published"], True)
        self.assertTrue(second["already_accepted"])
        self.assertEqual(second["accepted_commit"], published_main)
        self.assertEqual(adapter.main_oid(), published_main)

    def test_closed_unpublished_pull_is_rejected_after_ancestry_check_without_push(self):
        for number, operation in enumerate((validate_pr, accept_pr), start=1):
            with self.subTest(operation=operation.__name__):
                _source, adapter = self.one_commit_pull(number=number)
                before = adapter.main_oid()
                adapter.state = "closed"

                result = operation("example/ledger", number, _adapter=adapter)

                self.assertEqual(result["status"], "rejected")
                self.assertIs(result["published"], False)
                self.assertEqual(adapter.fetch_calls, 1)
                self.assertEqual(adapter.push_calls, 0)
                self.assertEqual(adapter.main_oid(), before)

    def test_verified_existing_ancestry_stays_accepted_when_pr_head_changes_later(self):
        source, adapter = self.one_commit_pull()
        first = accept_pr("example/ledger", 1, _adapter=adapter)
        published_main = adapter.main_oid()
        adapter.changed_head_on_call = adapter.pull_calls + 2

        result = accept_pr("example/ledger", 1, _adapter=adapter)

        self.assertEqual(first["status"], "accepted")
        self.assertEqual(result["status"], "accepted")
        self.assertIs(result["published"], True)
        self.assertTrue(result["already_accepted"])
        self.assertEqual(result["head"], source.oid())
        self.assertEqual(result["accepted_commit"], published_main)
        self.assertIn("new candidate", result["reason"])

    def test_verified_existing_ancestry_survives_status_api_outage(self):
        _source, adapter = self.one_commit_pull()
        first = accept_pr("example/ledger", 1, _adapter=adapter)
        self.assertEqual(first["status"], "accepted")
        adapter.fail_pull_on_call = adapter.pull_calls + 2
        second = accept_pr("example/ledger", 1, _adapter=adapter)
        self.assertEqual(second["status"], "accepted")
        self.assertIs(second["published"], True)
        self.assertTrue(second["already_accepted"])
        self.assertEqual(second["pr_status"], "unconfirmed")

    def test_lost_push_ack_is_recognized_from_published_candidate_ancestry(self):
        _source, adapter = self.one_commit_pull()
        adapter.lose_push_ack = True
        result = accept_pr("example/ledger", 1, _adapter=adapter)
        self.assertEqual(result["status"], "accepted")
        self.assertIs(result["published"], True)
        self.assertRegex(result["accepted_commit"], r"^[0-9a-f]{40}$")
        self.assertEqual(adapter.push_calls, 1)

    def test_final_retry_lost_ack_does_not_report_verified_publication_as_false(self):
        _source, adapter = self.one_commit_pull(added=[case(SECOND_CASE_ID)])
        adapter.race_rows = [
            case(f"{number:08x}-0000-4000-8000-{number:012x}")
            for number in (10, 11)
        ]
        adapter.lose_push_ack = True
        result = accept_pr("example/ledger", 1, _adapter=adapter)
        self.assertEqual(result["status"], "accepted")
        self.assertIs(result["published"], True)
        self.assertEqual(result["attempts"], 3)
        self.assertEqual(adapter.push_calls, 3)

    def test_remote_git_normalizes_timeout_and_os_errors_without_token_leakage(self):
        adapter = object.__new__(GitHubHostedAdapter)
        adapter._token = "native-secret-value"
        failures = (
            subprocess.TimeoutExpired(["git", "push"], 120),
            OSError("transport failed with native-secret-value"),
        )
        for failure in failures:
            with self.subTest(failure=type(failure).__name__):
                with patch("omarchy_knowledge.intake.subprocess.run", side_effect=failure):
                    with self.assertRaises(RuntimeError) as raised:
                        adapter._remote_git(Path("/trusted/objects.git"), ["push", "origin", "a:b"])
                self.assertIn("Hosted Git operation failed", str(raised.exception))
                self.assertNotIn("native-secret-value", str(raised.exception))

    def test_real_remote_git_timeout_becomes_unknown_push_outcome(self):
        _source, adapter = self.one_commit_pull()
        hosted = object.__new__(GitHubHostedAdapter)
        hosted._token = "native-secret-value"

        def timeout_push(objects, repository, merge_commit):
            with patch(
                "omarchy_knowledge.intake.subprocess.run",
                side_effect=subprocess.TimeoutExpired(["git", "push"], 120),
            ):
                return hosted._remote_git(objects, ["push", repository, merge_commit])

        adapter.push_main = timeout_push
        result = accept_pr("example/ledger", 1, _adapter=adapter)
        self.assertEqual(result["status"], "unknown")
        self.assertIsNone(result["published"])
        self.assertRegex(result["accepted_commit"], r"^[0-9a-f]{40}$")
        self.assertNotIn("native-secret-value", result["reason"])

    def test_malicious_workflow_is_rejected_and_api_text_is_never_shell_code(self):
        marker = self.root / "api-metadata-executed"
        source = ContributionRepository(self.root / "source", [case()])
        base = source.oid()
        source.branch()
        workflow = source.path / ".github" / "workflows" / "attack.yml"
        workflow.parent.mkdir(parents=True)
        workflow.write_text("name: attack\n", encoding="utf-8")
        git(source.path, "add", ".github")
        git(source.path, "commit", "-m", "workflow")
        adapter = LocalGitHubAdapter(self.root / "hosted", source, {1: source.oid()})
        original = adapter.pull_metadata

        def malicious(repository, number):
            value = original(repository, number)
            value["title"] = f"$(touch {marker})"
            value["user"] = {"login": f"; touch {marker};"}
            return value

        adapter.pull_metadata = malicious
        result = accept_pr("example/ledger", 1, _adapter=adapter)
        self.assertEqual(result["status"], "rejected")
        self.assertFalse(marker.exists())
        self.assertEqual(adapter.main_oid(), base)

    def test_private_identifier_in_pull_request_text_is_rejected(self):
        source, adapter = self.one_commit_pull()
        before = adapter.main_oid()
        original = adapter.pull_metadata

        def private_text(repository, number):
            value = original(repository, number)
            value["body"] = "Device UUID 550e8400-e29b-41d4-a716-446655440000"
            return value

        adapter.pull_metadata = private_text
        result = accept_pr("example/ledger", 1, _adapter=adapter)
        self.assertEqual(result["status"], "rejected")
        self.assertFalse(result["published"])
        self.assertEqual(adapter.main_oid(), before)

    def test_percent_encoded_private_identifier_in_pull_request_text_is_rejected(self):
        _source, adapter = self.one_commit_pull()
        before = adapter.main_oid()
        original = adapter.pull_metadata

        def private_text(repository, number):
            value = original(repository, number)
            value["body"] = "Device%2520UUID%2520550e8400%252De29b%252D41d4%252Da716%252D446655440000"
            return value

        adapter.pull_metadata = private_text
        result = accept_pr("example/ledger", 1, _adapter=adapter)
        self.assertEqual(result["status"], "rejected")
        self.assertIs(result["published"], False)
        self.assertEqual(adapter.main_oid(), before)

    def test_successful_push_with_failed_verification_fetch_reports_unknown_publication(self):
        _source, adapter = self.one_commit_pull()
        adapter.fail_fetch_main_on_call = 2
        result = accept_pr("example/ledger", 1, _adapter=adapter)
        self.assertEqual(result["status"], "unknown")
        self.assertIsNone(result["published"])
        self.assertRegex(result["accepted_commit"], r"^[0-9a-f]{40}$")
        self.assertEqual(
            git(adapter.remote, "merge-base", "--is-ancestor", result["accepted_commit"], "main").returncode,
            0,
        )

    def test_verified_push_retains_acceptance_when_final_api_status_is_unavailable(self):
        _source, adapter = self.one_commit_pull()
        adapter.fail_pull_on_call = 3
        result = accept_pr("example/ledger", 1, _adapter=adapter)
        self.assertEqual(result["status"], "accepted")
        self.assertIs(result["published"], True)
        self.assertRegex(result["accepted_commit"], r"^[0-9a-f]{40}$")
        self.assertFalse(result["pr_merged"])
        self.assertEqual(result["pr_status"], "unconfirmed")
        self.assertIn("status", result["reason"].lower())

    def test_multi_commit_branch_with_removed_private_file_is_rejected(self):
        source = ContributionRepository(self.root / "source", [case()])
        base = source.oid()
        source.branch()
        private = source.path / "private.txt"
        private.write_text("private transient history", encoding="utf-8")
        git(source.path, "add", "private.txt")
        git(source.path, "commit", "-m", "private")
        private.unlink()
        source.write_rows([change()])
        git(source.path, "add", "-A")
        git(source.path, "commit", "-m", "apparently clean tip")
        adapter = LocalGitHubAdapter(self.root / "hosted", source, {1: source.oid()})
        result = accept_pr("example/ledger", 1, _adapter=adapter)
        self.assertEqual(result["status"], "rejected")
        self.assertIn("one clean data commit", result["reason"])
        self.assertEqual(adapter.main_oid(), base)


if __name__ == "__main__":
    unittest.main()
