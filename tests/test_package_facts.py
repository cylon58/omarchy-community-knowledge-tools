"""Package provider facts stay scoped and cannot imply source fixes."""
from dataclasses import replace
import unittest

from tests.test_upstream import A, NOW, FixtureGitHub


class PackageFacts(unittest.TestCase):
    def setUp(self):
        from omarchy_knowledge.package_facts import PackageQueryV1, PackageSnapshotV1
        self.query = PackageQueryV1("omarchy", "stable", "x86_64", A)
        self.snapshot = PackageSnapshotV1(
            "fixture-reviewed", "omacom/omarchy", 123, "omarchy", "stable", "x86_64", "available",
            "1.2.3-1", "arch", A, "2026-09-16T12:00:00Z", "2026-09-16T11:45:00Z", "d" * 64,
        )

    def observe(self, snapshot=None, *, outage=False, provider=True):
        from omarchy_knowledge.upstream import OmarchyProbeV1, observe_omarchy
        expected = self.query
        class FixturePackages:
            provider_id = "fixture-reviewed"
            def package(self, repository, repository_id, query):
                if isinstance(outage, Exception):
                    raise outage
                if outage:
                    raise OSError("private provider error")
                if (repository, repository_id, query) != ("omacom/omarchy", 123, expected):
                    raise AssertionError("Package provider received a mismatched query")
                return snapshot
        probe = OmarchyProbeV1("omacom/omarchy", 123, 7, package_queries=(self.query,))
        return observe_omarchy(probe, FixtureGitHub(), FixturePackages() if provider else None, now=NOW)

    def test_exact_fresh_available_absent_and_unsupported_are_separate(self):
        available = self.observe(self.snapshot)
        item = available["packages"][0]
        self.assertEqual(item["state"], "available")
        self.assertEqual(item["selector"], {"kind": "software", "component": "package", "name": "omarchy"})
        self.assertEqual(item["source_commit_oid"], A)
        self.assertEqual(item["fresh_until"], "2026-09-16T12:45:00Z")
        self.assertEqual(available["semantic_fix_state"], "unknown")
        absent = replace(self.snapshot, state="absent", version=None, version_scheme=None, source_commit_oid=None)
        self.assertEqual(self.observe(absent)["packages"][0]["state"], "absent")
        self.assertEqual(self.observe(provider=False)["packages"][0]["state"], "unknown")
        self.assertEqual(self.observe(outage=True)["packages"][0]["state"], "unknown")
        self.assertEqual(self.observe(outage=RuntimeError("provider failed"))["packages"][0]["state"], "unknown")

    def test_wrong_identity_stale_metadata_and_version_without_source_are_unknown(self):
        changes = [{"repository_id": 999}, {"repository": "attacker/repo"}, {"package_name": "kernel"},
                   {"channel": "rc"}, {"architecture": "aarch64"}, {"provider_id": "another-provider"},
                   {"source_commit_oid": "b" * 40}, {"source_commit_oid": None},
                   {"metadata_at": "2026-09-15T12:00:00Z"}, {"retrieved_at": "2026-09-17T12:00:00Z"},
                   {"response_sha256": "unverified"}, {"state": "available", "version": None}]
        for change in changes:
            with self.subTest(change=change):
                self.assertEqual(self.observe(replace(self.snapshot, **change))["packages"][0]["state"], "unknown")

    def test_later_absence_or_unknown_does_not_reuse_previous_available_fact(self):
        earlier = self.observe(self.snapshot)
        absent = replace(self.snapshot, state="absent", version=None, version_scheme=None, source_commit_oid=None)
        self.assertEqual(self.observe(absent)["packages"][0]["state"], "absent")
        self.assertEqual(self.observe(outage=True)["packages"][0]["state"], "unknown")
        self.assertEqual(earlier["packages"][0]["state"], "available")

    def test_malformed_or_duplicate_queries_are_rejected_before_provider_reads(self):
        from omarchy_knowledge.upstream import OmarchyProbeV1, observe_omarchy
        class NeverRead(FixtureGitHub):
            def pull(self, repo, number):
                raise AssertionError("Invalid queries must fail before provider reads")
        for queries in ((self.query, self.query), (replace(self.query, source_commit_oid="main"),),
                        (replace(self.query, channel="unknown"),), (self.query,) * 25, "omarchy"):
            with self.subTest(queries=queries):
                with self.assertRaises(ValueError):
                    observe_omarchy(OmarchyProbeV1("omacom/omarchy", 123, 7, package_queries=queries), NeverRead(), now=NOW)


if __name__ == "__main__":
    unittest.main()
