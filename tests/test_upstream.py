"""Read-only source facts from injected synthetic provider snapshots."""
from datetime import datetime, timezone
import hashlib
import base64
import io
import json
import multiprocessing
import signal
import time
import unittest
from unittest.mock import patch

NOW = datetime(2026, 9, 16, 12, tzinfo=timezone.utc)
A, B = "a" * 40, "b" * 40


class FixtureGitHub:
    def __init__(self):
        self.merged = True
        self.complete = True
        self.status = "ahead"
        self.annotated = False
        self.stale = False
        self.outage = False

    def snapshot(self, payload):
        from omarchy_knowledge.github_public import PublicSnapshot
        if self.outage:
            raise OSError("sensitive error")
        return PublicSnapshot("omacom/omarchy", "2026-09-15T12:00:00Z" if self.stale else "2026-09-16T12:00:00Z",
                              "c" * 64, payload)

    def pull(self, repo, number):
        return self.snapshot({"number": number, "state": "closed" if self.merged else "open", "merged": self.merged,
                              "merged_at": "2026-09-15T12:00:00Z" if self.merged else None,
                              "merge_commit_sha": A if self.merged else None,
                              "base": {"ref": "dev", "repo": {"id": 123}}, "head": {"sha": B}})

    def ref(self, repo, tag):
        return self.snapshot({"ref": "refs/tags/" + tag, "object": {"type": "tag" if self.annotated else "commit", "sha": B}})

    def annotated_tag(self, repo, oid):
        return self.snapshot({"sha": oid, "object": {"type": "commit", "sha": B}})

    def compare(self, repo, base, head):
        commits = [] if self.status == "behind" else [{"sha": head}]
        return self.snapshot({"status": self.status, "base_commit": {"sha": base},
                              "merge_base_commit": {"sha": base if self.status == "ahead" else B},
                              "commits": commits, "total_commits": len(commits) if self.complete else 500})

    def release_by_tag(self, repo, tag):
        return self.snapshot({"tag_name": tag, "draft": False, "prerelease": True, "published_at": "2026-09-15T12:00:00Z"})


class UpstreamFacts(unittest.TestCase):
    def test_merge_tag_release_and_package_remain_distinct(self):
        from omarchy_knowledge.upstream import OmarchyProbeV1, observe_omarchy
        result = observe_omarchy(OmarchyProbeV1("omacom/omarchy", 123, 7, ("v1.2.0",)), FixtureGitHub(), now=NOW)
        self.assertEqual(result["pull"]["merged"], True)
        self.assertEqual(result["tags"][0]["ancestry"], "yes")
        self.assertEqual(result["tags"][0]["publication"]["prerelease"], True)
        self.assertEqual(result["package_availability"], "unknown")
        self.assertEqual(result["relevance"], {"state": "unknown", "basis": "unknown"})
        self.assertNotIn("first_fixing_release", result)

    def test_stale_outage_incomplete_history_and_unmerged_are_unknown(self):
        from omarchy_knowledge.upstream import OmarchyProbeV1, observe_omarchy
        probe = OmarchyProbeV1("omacom/omarchy", 123, 7, ("v1.2.0",))
        for flag in ("stale", "outage"):
            fixture = FixtureGitHub()
            setattr(fixture, flag, True)
            result = observe_omarchy(probe, fixture, now=NOW)
            self.assertEqual(result["pull"]["merged"], "unknown")
            self.assertEqual(result["tags"][0]["ancestry"], "unknown")
            self.assertNotIn("sensitive", json.dumps(result))
        fixture = FixtureGitHub()
        fixture.complete = False
        self.assertEqual(observe_omarchy(probe, fixture, now=NOW)["tags"][0]["ancestry"], "unknown")
        fixture.merged = False
        self.assertIsNone(observe_omarchy(probe, fixture, now=NOW)["pull"]["merge_commit_oid"])

    def test_annotated_tag_and_explicit_backport_do_not_claim_equivalence(self):
        from omarchy_knowledge.upstream import OmarchyProbeV1, observe_omarchy
        fixture = FixtureGitHub()
        fixture.annotated = True
        result = observe_omarchy(OmarchyProbeV1("omacom/omarchy", 123, 7, ("v1.2.0",), (8,)), fixture, now=NOW)
        self.assertEqual(result["tags"][0]["commit_oid"], B)
        self.assertEqual(result["backports"][0]["semantic_equivalence"], "unknown")
        self.assertEqual(result["semantic_fix_state"], "unknown")

    def test_each_annotated_tag_object_retains_its_evidence(self):
        from omarchy_knowledge.upstream import OmarchyProbeV1, observe_omarchy
        from omarchy_knowledge.github_public import PublicSnapshot
        class NestedTag(FixtureGitHub):
            annotated = True
            def annotated_tag(self, repo, oid):
                target = {"type": "tag", "sha": "d" * 40} if oid == B else {"type": "commit", "sha": A}
                return PublicSnapshot(repo, "2026-09-16T11:59:00Z", "e" * 64 if oid == B else "f" * 64,
                                      {"sha": oid, "object": target})
        fixture = NestedTag()
        fixture.annotated = True
        result = observe_omarchy(OmarchyProbeV1("omacom/omarchy", 123, 7, ("v1.2.0",)), fixture, now=NOW)
        chain = result["tags"][0]["tag_objects"]
        self.assertEqual([entry["oid"] for entry in chain], [B, "d" * 40])
        self.assertEqual([entry["response_sha256"] for entry in chain], ["e" * 64, "f" * 64])
        self.assertEqual(chain[0]["retrieved_at"], "2026-09-16T11:59:00Z")

    def test_cherry_pick_trailer_and_revert_text_do_not_fabricate_semantic_proof(self):
        from omarchy_knowledge.upstream import OmarchyProbeV1, observe_omarchy
        class ClaimedHistory(FixtureGitHub):
            def pull(self, repo, number):
                value = super().pull(repo, number)
                value.payload["body"] = "(cherry picked from commit " + A + ")"
                return value
            def compare(self, repo, base, head):
                value = super().compare(repo, base, head)
                value.payload["commits"][0]["commit"] = {"message": "Revert previous change; claim equivalent patch"}
                return value
        result = observe_omarchy(OmarchyProbeV1("omacom/omarchy", 123, 7, ("v1.2.0",), (8,)), ClaimedHistory(), now=NOW)
        self.assertEqual(result["tags"][0]["ancestry"], "yes")
        self.assertEqual(result["semantic_fix_state"], "unknown")
        self.assertEqual(result["backports"][0]["relationship_method"], "explicit-pr")
        self.assertEqual(result["backports"][0]["semantic_equivalence"], "unknown")

    def test_corrected_release_snapshot_does_not_reuse_earlier_publication(self):
        from omarchy_knowledge.upstream import OmarchyProbeV1, observe_omarchy
        class CorrectableRelease(FixtureGitHub):
            corrected = False
            def release_by_tag(self, repo, tag):
                value = super().release_by_tag(repo, tag)
                if self.corrected:
                    value.payload.update(draft=True, published_at=None)
                return value
        provider = CorrectableRelease()
        probe = OmarchyProbeV1("omacom/omarchy", 123, 7, ("v1.2.0",))
        earlier = observe_omarchy(probe, provider, now=NOW)
        provider.corrected = True
        later = observe_omarchy(probe, provider, now=NOW)
        self.assertEqual(earlier["tags"][0]["publication"]["state"], "published")
        self.assertEqual(later["tags"][0]["publication"]["state"], "unpublished")
        self.assertEqual(later["semantic_fix_state"], "unknown")

    def test_blob_read_requires_full_identity_and_checks_decoded_bounds(self):
        from omarchy_knowledge.github_public import GitHubPublicRead, PublicReadUnavailable
        raw = b"inert fixture"
        oid = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
        class Connection:
            payload = {"sha": oid, "size": len(raw), "encoding": "base64", "content": base64.b64encode(raw).decode()}
            def __init__(self, host, timeout):
                pass
            def request(self, method, path, headers):
                self.path = path
                if method != "GET" or path != "/repos/omacom/omarchy/git/blobs/" + oid:
                    raise AssertionError("Unexpected blob endpoint")
            def getresponse(self):
                response = io.BytesIO(json.dumps(self.payload).encode())
                response.status = 200
                return response
            def close(self):
                pass
        client = GitHubPublicRead(connection_factory=Connection)
        self.assertEqual(client.blob("omacom/omarchy", oid, 100), raw)
        with self.assertRaises(PublicReadUnavailable):
            client.blob("omacom/omarchy", oid, 5)
        with self.assertRaises(ValueError):
            client.blob("omacom/omarchy", "main", 100)
        Connection.payload = {**Connection.payload, "content": base64.b64encode(b"false content").decode()}
        with self.assertRaises(PublicReadUnavailable):
            client.blob("omacom/omarchy", oid, 100)

    def test_complete_negative_ancestry_and_numeric_identity_mismatch(self):
        from omarchy_knowledge.upstream import OmarchyProbeV1, observe_omarchy
        fixture = FixtureGitHub()
        fixture.status = "behind"
        result = observe_omarchy(OmarchyProbeV1("omacom/omarchy", 123, 7, ("v1.2.0",)), fixture, now=NOW)
        self.assertEqual(result["tags"][0]["ancestry"], "no")
        result = observe_omarchy(OmarchyProbeV1("omacom/omarchy", 999, 7, ("v1.2.0",)), fixture, now=NOW)
        self.assertEqual(result["pull"]["merged"], "unknown")
        self.assertEqual(result["tags"][0]["publication"]["state"], "unknown")

    def test_moved_tag_discards_prior_inclusion_and_release_binding(self):
        from omarchy_knowledge.upstream import OmarchyProbeV1, observe_omarchy
        class MovingTag(FixtureGitHub):
            reads = 0
            def ref(self, repo, tag):
                self.reads += 1
                value = super().ref(repo, tag)
                if self.reads > 1:
                    value.payload["object"]["sha"] = "d" * 40
                return value
        result = observe_omarchy(OmarchyProbeV1("omacom/omarchy", 123, 7, ("v1.2.0",)), MovingTag(), now=NOW)
        self.assertEqual(result["tags"][0]["ancestry"], "unknown")
        self.assertEqual(result["tags"][0]["publication"]["state"], "unknown")

    def test_transport_is_get_only_anonymous_fixed_origin_bounded_and_no_redirects(self):
        from omarchy_knowledge.github_public import GitHubPublicRead, PublicReadUnavailable
        calls = []
        class Connection:
            status = 200
            raw = b'{"number":7}'
            def __init__(self, host, timeout):
                calls.append((host, timeout))
            def request(self, method, path, headers):
                calls.append((method, path, headers))
            def getresponse(self):
                response = io.BytesIO(self.raw)
                response.status = self.status
                return response
            def close(self):
                pass
        client = GitHubPublicRead(connection_factory=Connection)
        self.assertEqual(client.pull("omacom/omarchy", 7).payload, {"number": 7})
        self.assertEqual(calls[0], ("api.github.com", 5))
        self.assertEqual(calls[1][0:2], ("GET", "/repos/omacom/omarchy/pulls/7"))
        self.assertEqual(set(calls[1][2]), {"Accept", "User-Agent", "X-GitHub-Api-Version"})
        for status, raw in ((302, b""), (403, b""), (200, b" " * (1024 * 1024 + 1)), (200, b'{"x":1,"x":2}')):
            Connection.status, Connection.raw = status, raw
            with self.assertRaises(PublicReadUnavailable):
                client.pull("omacom/omarchy", 7)
        with self.assertRaises(ValueError):
            client.pull("attacker/repo", 7)
        with self.assertRaises(ValueError):
            client.ref("omacom/omarchy", "../../private")

    @unittest.skipUnless("fork" in multiprocessing.get_all_start_methods(), "Production deadline requires fork")
    def test_production_deadline_stops_stalled_worker_and_reaps_children(self):
        from omarchy_knowledge import github_public
        context = multiprocessing.get_context("fork")
        original_children = {child.pid for child in multiprocessing.active_children()}

        def clean_survivors():
            for child in multiprocessing.active_children():
                if child.pid not in original_children:
                    child.kill()
                    child.join(timeout=2)
        self.addCleanup(clean_survivors)

        for ignore_termination in (False, True):
            with self.subTest(ignore_termination=ignore_termination):
                started = context.Event()

                def stalled_worker(repo, suffix, pipe):
                    # No HTTP/DNS code is reachable in this fixture worker.
                    if ignore_termination:
                        signal.signal(signal.SIGTERM, signal.SIG_IGN)
                    started.set()
                    while True:
                        signal.pause()

                with patch.object(github_public, "_public_worker", stalled_worker), \
                        patch.object(github_public, "PUBLIC_READ_DEADLINE_SECONDS", .2):
                    before = time.monotonic()
                    with self.assertRaises(github_public.PublicReadUnavailable):
                        github_public.GitHubPublicRead().pull("omacom/omarchy", 7)
                    elapsed = time.monotonic() - before
                self.assertTrue(started.is_set(), "The production fork worker must actually start")
                self.assertGreaterEqual(elapsed, .15)
                self.assertLess(elapsed, 3, "Deadline plus bounded process cleanup must return promptly")
                self.assertEqual({child.pid for child in multiprocessing.active_children()} - original_children, set())


class MaintainerDeclaration(unittest.TestCase):
    def test_default_authority_empty_and_exact_observed_identity_required(self):
        from omarchy_knowledge.declarations import AuthorityPolicy, validate_declaration
        body = {"declaration_version": 1, "kind": "relevance", "repository_id": "123",
                "pull_request": 7, "event_id": "cc3e61da-3b3c-4e4e-9fba-970ef606672f",
                "event_sha256": "e" * 64, "assertion": "supports"}
        raw = json.dumps(body)
        declaration = {"repository_id": "123", "pull_request": 7, "comment_id": "99", "actor_account_id": "42",
                       "body_sha256": hashlib.sha256(raw.encode()).hexdigest(), "updated_at": "2026-09-16T11:00:00Z"}
        observed = {**declaration, "body": raw, "retrieved_at": "2026-09-16T12:00:00Z", "deleted": False}
        args = {"event_id": body["event_id"], "event_sha256": body["event_sha256"], "now": NOW}
        self.assertEqual(validate_declaration(declaration, observed, AuthorityPolicy(), **args)["state"], "unknown")
        policy = AuthorityPolicy("d" * 40, (("123", ("42",)),))
        self.assertEqual(validate_declaration(declaration, observed, policy, **args)["state"], "upstream-supported")
        for malformed_accounts in ("42", {"42": True}, ("42", "42")):
            malformed_policy = AuthorityPolicy("d" * 40, (("123", malformed_accounts),))
            actor = "4" if isinstance(malformed_accounts, str) else "42"
            identity = {**declaration, "actor_account_id": actor}
            api_comment = {**observed, "actor_account_id": actor}
            with self.subTest(malformed_accounts=malformed_accounts):
                self.assertEqual(validate_declaration(identity, api_comment, malformed_policy, **args)["state"], "unknown")
        for key, value in (("actor_account_id", "43"), ("comment_id", "100"), ("body", raw + " "),
                           ("updated_at", "2026-09-16T11:30:00Z"), ("deleted", True)):
            with self.subTest(key=key):
                self.assertEqual(validate_declaration(declaration, {**observed, key: value}, policy, **args)["state"], "unknown")
        self.assertEqual(validate_declaration(declaration, {**observed, "retrieved_at": "2026-09-15T12:00:00Z"}, policy, **args)["state"], "unknown")
        revoked_raw = json.dumps({**body, "assertion": "revokes"})
        revoked = {**declaration, "body_sha256": hashlib.sha256(revoked_raw.encode()).hexdigest()}
        revoked_observation = {**observed, **revoked, "body": revoked_raw}
        self.assertEqual(validate_declaration(revoked, revoked_observation, policy, **args)["state"], "unsupported")


if __name__ == "__main__":
    unittest.main()
