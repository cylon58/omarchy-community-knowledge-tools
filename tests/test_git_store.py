import fcntl
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
import uuid

from omarchy_knowledge.git_store import _run, _safe_environment, load_cache, status, sync
from tests.fixtures import case
from tests.git_fixture import GitFixture


class GitStoreContract(unittest.TestCase):
    def test_sync_requires_an_explicit_test_transport_for_a_local_fixture(self):
        """Removing public-origin validation must make this fail."""
        with tempfile.TemporaryDirectory() as temporary:
            fixture = GitFixture(Path(temporary) / "source")
            fixture.commit_records({"cases/%s.json" % case()["id"]: case()})
            with self.assertRaises(ValueError):
                sync(Path(temporary) / "cache", "https://example.test/not-a-selector")

    def test_successful_sync_caches_validated_records(self):
        """Removing validated Git blob ingestion must make this fail."""
        with tempfile.TemporaryDirectory() as temporary:
            fixture = GitFixture(Path(temporary) / "source")
            row = case()
            fixture.commit_records({"cases/%s.json" % row["id"]: row})
            accepted = sync(Path(temporary) / "cache", "example/ledger", _transport=fixture.transport)
            self.assertEqual(accepted["repository"], "example/ledger")
            self.assertEqual(accepted["records"], [row])
            self.assertEqual(load_cache(Path(temporary) / "cache")["records"], [row])

    def test_successful_sync_discards_its_owned_fetch_store(self):
        """Keeping fetched Git objects under the accepted cache must make this fail."""
        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary) / "cache"
            fixture = GitFixture(Path(temporary) / "source")
            row = case()
            fixture.commit_records({"cases/%s.json" % row["id"]: row})

            sync(cache, "example/ledger", _transport=fixture.transport)

            self.assertFalse((cache / "objects.git").exists())
            self.assertEqual(list(cache.glob(".fetch-*")), [])

    def test_sync_accepts_multiple_bounded_tree_entries_in_one_read(self):
        """Rejecting a normal multi-entry ls-tree read as oversized must fail."""
        with tempfile.TemporaryDirectory() as temporary:
            fixture = GitFixture(Path(temporary) / "source")
            rows = []
            for _ in range(8):
                row = case()
                row["id"] = str(uuid.uuid4())
                rows.append(row)
            fixture.commit_records({"cases/%s.json" % row["id"]: row for row in rows})
            self.assertEqual(sync(Path(temporary) / "cache", "example/ledger", _transport=fixture.transport)["records"],
                             sorted(rows, key=lambda row: row["id"]))

    def test_rejected_refresh_keeps_the_prior_accepted_snapshot(self):
        """Replacing accepted.json before corpus validation must make this fail."""
        with tempfile.TemporaryDirectory() as temporary:
            root, cache = Path(temporary), Path(temporary) / "cache"
            fixture = GitFixture(root / "source")
            row = case()
            fixture.commit_records({"cases/%s.json" % row["id"]: row})
            sync(cache, "example/ledger", _transport=fixture.transport)
            before = load_cache(cache)
            fixture.commit_records({"cases/not-a-uuid.json": row})
            with self.assertRaises(ValueError):
                sync(cache, "example/ledger", _transport=fixture.transport)
            after = load_cache(cache)
            self.assertEqual(after["revision"], before["revision"])
            self.assertEqual(after["records"], before["records"])

    def test_unavailable_transport_preserves_cache_and_records_error(self):
        """Dropping refresh-error handling must make this fail."""
        with tempfile.TemporaryDirectory() as temporary:
            root, cache = Path(temporary), Path(temporary) / "cache"
            fixture = GitFixture(root / "source")
            row = case()
            fixture.commit_records({"cases/%s.json" % row["id"]: row})
            sync(cache, "example/ledger", _transport=fixture.transport)
            with self.assertRaises(RuntimeError):
                sync(cache, "example/ledger", _transport=str(root / "missing.git"))
            self.assertEqual(load_cache(cache)["records"], [row])
            self.assertIn("Git operation failed", status(cache)["refresh_error"])

    def test_rejects_symlink_records(self):
        """Accepting non-canonical Git tree entries must make this fail."""
        with tempfile.TemporaryDirectory() as temporary:
            fixture = GitFixture(Path(temporary) / "source")
            fixture.commit_symlink("cases/00000000-0000-4000-8000-000000000001.json", "outside.json")
            with self.assertRaises(ValueError):
                sync(Path(temporary) / "cache", "example/ledger", _transport=fixture.transport)

    def test_rejects_malformed_record_filename_from_a_real_git_tree(self):
        """Accepting a non-canonical filename from Git must make this fail."""
        with tempfile.TemporaryDirectory() as temporary:
            fixture = GitFixture(Path(temporary) / "source")
            fixture.commit_records({"cases/not-a-uuid.json": case()})
            with self.assertRaises(ValueError):
                sync(Path(temporary) / "cache", "example/ledger", _transport=fixture.transport)

    def test_traversal_shaped_raw_git_tree_is_rejected_without_replacing_cache(self):
        """Accepting records/cases/../outside.json must make this fail."""
        with tempfile.TemporaryDirectory() as temporary:
            root, cache = Path(temporary), Path(temporary) / "cache"
            fixture = GitFixture(root / "source")
            row = case()
            fixture.commit_records({"cases/%s.json" % row["id"]: row})
            sync(cache, "example/ledger", _transport=fixture.transport)
            before = load_cache(cache)
            fixture.replace_main_with_traversal_shaped_tree(row)
            outside = root / "outside.json"
            with self.assertRaises((RuntimeError, ValueError)):
                sync(cache, "example/ledger", _transport=fixture.transport)
            self.assertFalse(outside.exists())
            self.assertEqual(load_cache(cache), before)

    def test_child_git_environment_omits_ambient_credentials_and_config(self):
        """Forwarding an ambient Git credential/config variable must make this fail."""
        old = os.environ.get("GIT_CONFIG_COUNT")
        os.environ["GIT_CONFIG_COUNT"] = "1"
        try:
            environment = _safe_environment()
        finally:
            if old is None:
                del os.environ["GIT_CONFIG_COUNT"]
            else:
                os.environ["GIT_CONFIG_COUNT"] = old
        self.assertNotIn("GIT_CONFIG_COUNT", environment)
        self.assertEqual(environment["GIT_CONFIG_GLOBAL"], os.devnull)
        self.assertEqual(environment["GIT_TERMINAL_PROMPT"], "0")

    def test_sync_ignores_ambient_url_and_credential_configuration(self):
        """Allowing ambient URL rewrite config to redirect the child Git must fail."""
        with tempfile.TemporaryDirectory() as temporary:
            root, cache = Path(temporary), Path(temporary) / "cache"
            fixture = GitFixture(root / "source")
            row = case()
            fixture.commit_records({"cases/%s.json" % row["id"]: row})
            global_config = root / "hostile.gitconfig"
            global_config.write_text(
                '[url "file:///definitely-missing"]\n\tinsteadOf = %s\n'
                '[credential]\n\thelper = !exit 99\n' % fixture.transport,
                encoding="utf-8",
            )
            with patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": str(global_config)}, clear=False):
                accepted = sync(cache, "example/ledger", _transport=fixture.transport)
            self.assertEqual(accepted["records"], [row])

    def test_real_child_git_overrides_a_configured_hook_path(self):
        """Removing the command-level hooks override must make this fail."""
        with tempfile.TemporaryDirectory() as temporary:
            objects = Path(temporary) / "objects.git"
            marker = Path(temporary) / "hook-ran"
            hook_path = Path(temporary) / "hooks"
            hook_path.mkdir()
            (hook_path / "post-checkout").write_text("#!/bin/sh\ntouch %s\n" % marker, encoding="utf-8")
            os.chmod(hook_path / "post-checkout", 0o755)
            from tests.git_fixture import git
            git("init", "--bare", str(objects))
            git("--git-dir", str(objects), "config", "core.hooksPath", str(hook_path))
            self.assertEqual(_run(objects, ["config", "--get", "core.hooksPath"], time.monotonic() + 1).strip(), b"/dev/null")
            self.assertFalse(marker.exists())

    def test_busy_writer_lock_obeys_the_total_deadline(self):
        """A blocking writer lock outside the deadline must make this fail."""
        with tempfile.TemporaryDirectory() as temporary:
            def locked(_lock, operation):
                if operation != fcntl.LOCK_UN:
                    raise BlockingIOError
            with patch("omarchy_knowledge.git_store.fcntl.flock", side_effect=locked), \
                    patch("omarchy_knowledge.git_store.time.monotonic", side_effect=[0, 0, 0, 121]), \
                    patch("omarchy_knowledge.git_store.time.sleep"):
                with self.assertRaisesRegex(RuntimeError, "deadline"):
                    sync(Path(temporary) / "cache", "example/ledger", _transport="unused")

    def test_sync_never_creates_a_checkout(self):
        """Adding a working-tree checkout during refresh must make this fail."""
        with tempfile.TemporaryDirectory() as temporary:
            root, cache = Path(temporary), Path(temporary) / "cache"
            fixture = GitFixture(root / "source")
            row = case()
            fixture.commit_records({"cases/%s.json" % row["id"]: row})
            sync(cache, "example/ledger", _transport=fixture.transport)
            self.assertFalse((cache / "records").exists())

    def test_status_reports_empty_cache(self):
        """Returning a fabricated revision for no accepted snapshot must make this fail."""
        with tempfile.TemporaryDirectory() as temporary:
            self.assertEqual(status(Path(temporary) / "cache"), {
                "revision": None, "synced_at": None, "count": 0, "refresh_error": None,
            })
