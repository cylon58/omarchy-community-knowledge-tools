"""One local-Git story covering acceptance, reuse, and adverse follow-up evidence."""
from copy import deepcopy
import tempfile
import unittest
from pathlib import Path

from omarchy_knowledge.git_store import load_cache, sync
from omarchy_knowledge.intake import accept_pr
from omarchy_knowledge.research import related, release_claims, search
from tests.fixtures import case, change, event, report
from tests.test_intake import LocalGitHubAdapter
from tests.test_pr_validation import ContributionRepository, git


POST_UPDATE_REPORT_ID = "fa3e61da-3b3c-4e4e-9fba-970ef606672f"


class LocalReleaseStory(unittest.TestCase):
    """A broken acceptance/sync/evidence link anywhere in the loop must fail."""

    def test_two_acceptances_keep_both_failures_and_release_claim_uncertainty(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = ContributionRepository(root / "source", [])

            initial_failure = report()
            initial_failure["payload"]["result"] = "failure"
            initial_failure["payload"]["actual_result"] = "Input remained unavailable."
            source.branch("pr-1")
            source.commit_rows([case(), change(), initial_failure], "first observed failure")
            adapter = LocalGitHubAdapter(root / "hosted", source, {1: source.oid()})

            first = accept_pr("example/ledger", 1, _adapter=adapter)
            self.assertEqual(first["status"], "accepted")
            self.assertIs(first["published"], True)

            second_cache = root / "second-agent-cache"
            sync(second_cache, "example/ledger", _transport=str(adapter.remote))
            first_snapshot = load_cache(second_cache)
            matches = search(first_snapshot["records"], "dock input resume")
            self.assertEqual([row["id"] for row in matches], [case()["id"]])
            first_detail = related(first_snapshot["records"], case()["id"])
            self.assertEqual(
                [row["payload"]["result"] for row in first_detail["reports"]],
                ["failure"],
            )

            git(source.path, "fetch", str(adapter.remote), "main")
            git(source.path, "checkout", "-b", "pr-2", "FETCH_HEAD")
            claim = event()
            claim["payload"]["supporting_links"] = [
                "https://github.com/omacom/omarchy/releases/tag/v4.1.2"
            ]
            post_update = deepcopy(initial_failure)
            post_update["id"] = POST_UPDATE_REPORT_ID
            post_update["created_at"] = "2026-09-17T12:00:00Z"
            post_update["payload"]["actual_result"] = (
                "Input still failed after installing the claimed release."
            )
            post_update["payload"]["limitations"] = "One post-update retest only."
            source.commit_rows([claim, post_update], "release claim and unsuccessful retest")
            adapter.heads[2] = source.oid()
            git(source.path, "push", str(adapter.remote), "HEAD:refs/pull/2/head")

            second = accept_pr("example/ledger", 2, _adapter=adapter)
            self.assertEqual(second["status"], "accepted")
            self.assertIs(second["published"], True)

            sync(second_cache, "example/ledger", _transport=str(adapter.remote))
            final_records = load_cache(second_cache)["records"]
            final_detail = related(final_records, case()["id"])
            self.assertEqual(
                [row["payload"]["result"] for row in final_detail["reports"]],
                ["failure", "failure"],
            )
            claims = release_claims(final_records, case()["id"])
            self.assertEqual(claims[0]["claim_status"], "community-claim")
            self.assertTrue(claims[0]["needs_official_check"])
            self.assertEqual(
                claims[0]["links"],
                ["https://github.com/omacom/omarchy/releases/tag/v4.1.2"],
            )


if __name__ == "__main__":
    unittest.main()
