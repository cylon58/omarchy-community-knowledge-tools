"""Authenticated, bounded adapters for resumable fair intake."""
from copy import deepcopy
import io
import json
import time
import unittest


class APIConnection:
    def __init__(self, payload):
        self.payload = payload
        self.requests = []

    def factory(self, host, timeout):
        fixture = self

        class Connection:
            def __init__(self):
                fixture.requests.append(("connect", host, timeout))

            def request(self, method, path, body=None, headers=None):
                fixture.requests.append((method, path, body, headers))

            def getresponse(self):
                response = io.BytesIO(fixture.payload)
                response.status = 200
                return response

            def close(self):
                pass

        return Connection()


class PagesConnection:
    def __init__(self, raw, *, status=200):
        self.raw = raw
        self.status = status
        self.requests = []

    def factory(self, host, timeout):
        fixture = self

        class Connection:
            def __init__(self):
                fixture.requests.append(("connect", host, timeout))

            def request(self, method, path, body=None, headers=None):
                fixture.requests.append((host, method, path, body, headers))

            def getresponse(self):
                response = io.BytesIO(fixture.raw)
                response.status = fixture.status
                return response

            def close(self):
                pass

        return Connection()


def legacy_status():
    return {
        "status": "idle",
        "outcomes": [],
        "scanned": 0,
        "scan_truncated": False,
        "source": {
            "repository": "cylon58/omarchy-community-knowledge",
            "repository_id": 1373429914,
            "deployment": "production",
            "ref": "refs/heads/main",
            "data_revision": "a" * 40,
            "tree_revision": "b" * 40,
            "toolkit_revision": "c" * 40,
            "policy_revision": "d" * 40,
            "verified_at": "2026-09-17T10:00:00Z",
            "source_updated_at": "2026-09-17T09:00:00Z",
        },
        "receipt_coverage": {"records": 2, "receipted_records": 2},
        "upstream": {
            "version": 1,
            "status": "not-refreshed",
            "observations": [],
        },
        "pr_behavior": "snapshots-imported-prs-remain-open",
    }


def current_status():
    value = legacy_status()
    value["source"]["data_revision"] = "e" * 40
    value["intake_cursor"] = {
        "version": 1,
        "page": 9,
        "offset": 2,
        "after_pull_request": 42,
        "cycle": 3,
        "last_full_cycle_at": "2026-09-16T00:00:00Z",
    }
    value["cursor_health"] = {
        "version": 1,
        "last_progress_at": "2026-09-17T08:00:00Z",
        "consecutive_drift_runs": 1,
    }
    return value


class FairIntakeListAdapterTests(unittest.TestCase):
    def test_created_ascending_all_state_page_is_fixed_and_normalized(self):
        """Break caught: discovery uses shifting open-desc pages or leaks raw fields."""
        from omarchy_knowledge.github_native import GitHubRead

        fixture = APIConnection(json.dumps([
            {"number": 7, "state": "closed", "head": None, "title": "ignored"},
            {"number": 8, "state": "open", "head": {"sha": "a" * 40},
             "title": "ignored"},
        ]).encode())
        reader = GitHubRead(read_token="fixture-token", connection_factory=fixture.factory)

        self.assertEqual(reader.intake_page(2), [
            {"pull_request": 7, "state": "closed"},
            {"pull_request": 8, "state": "open", "head": "a" * 40},
        ])
        method, path, body, headers = fixture.requests[-1]
        self.assertEqual((method, path, body), (
            "GET",
            "/repos/cylon58/omarchy-community-knowledge/pulls?state=all&sort=created&direction=asc&per_page=20&page=2",
            None,
        ))
        self.assertEqual(headers["Authorization"], "Bearer fixture-token")

    def test_page_is_an_exact_positive_31_bit_integer(self):
        """Break caught: bool, zero, overflow, or submitted URL syntax reaches HTTP."""
        from omarchy_knowledge.github_native import GitHubRead, NativeUnavailable

        fixture = APIConnection(b"[]")
        reader = GitHubRead(connection_factory=fixture.factory)
        for page in (True, 0, -1, 2_147_483_648, "1", "1&state=open"):
            with self.subTest(page=page), self.assertRaises(NativeUnavailable):
                reader.intake_page(page)
        self.assertEqual(fixture.requests, [])


class FairIntakeStatusAdapterTests(unittest.TestCase):
    def test_recognizes_exact_legacy_and_current_status_separately(self):
        """Break caught: a legacy rollout is confused with a valid persisted cursor."""
        from omarchy_knowledge.intake_status import (
            PublicStatusKind, validate_intake_status,
        )

        legacy = validate_intake_status(
            legacy_status(),
            repository="cylon58/omarchy-community-knowledge",
            repository_id=1373429914,
            deployment="production",
        )
        self.assertIs(legacy.kind, PublicStatusKind.LEGACY)
        self.assertEqual(legacy.source_revision, "a" * 40)
        self.assertIsNone(legacy.cursor)
        self.assertIsNone(legacy.cursor_health)

        current = validate_intake_status(
            current_status(),
            repository="cylon58/omarchy-community-knowledge",
            repository_id=1373429914,
            deployment="production",
        )
        self.assertIs(current.kind, PublicStatusKind.CURRENT)
        self.assertEqual(current.source_revision, "e" * 40)
        self.assertEqual(current.cursor.to_mapping(), current_status()["intake_cursor"])
        self.assertEqual(current.cursor_health.last_progress_at,
                         "2026-09-17T08:00:00Z")
        self.assertEqual(current.cursor_health.consecutive_drift_runs, 1)

    def test_older_valid_source_revision_does_not_need_current_main(self):
        """Break caught: resumption incorrectly compares public source to live main."""
        from omarchy_knowledge.github_native import GitHubRead
        from omarchy_knowledge.intake_status import PublicStatusKind

        fixture = PagesConnection(json.dumps(current_status()).encode())
        reader = GitHubRead(read_token="must-not-reach-pages",
                            connection_factory=fixture.factory)
        result = reader.intake_status()

        self.assertIs(result.kind, PublicStatusKind.CURRENT)
        self.assertEqual(result.source_revision, "e" * 40)
        pages = [row for row in fixture.requests if len(row) == 5]
        self.assertEqual(len(pages), 1)
        host, method, path, body, headers = pages[0]
        self.assertEqual((host, method, path, body), (
            "cylon58.github.io", "GET",
            "/omarchy-community-knowledge/status.json", None,
        ))
        self.assertEqual(headers, {
            "Accept": "application/json",
            "User-Agent": "omarchy-knowledge-native/1",
        })
        self.assertEqual((reader.http.calls, reader.http.bytes),
                         (1, len(fixture.raw)))

    def test_unknown_partial_or_malformed_current_shape_is_not_legacy(self):
        """Break caught: unknown fields or one cursor component authorize bootstrap."""
        from omarchy_knowledge.intake_status import validate_intake_status

        variants = []
        value = legacy_status(); value["unknown"] = 1; variants.append(value)
        value = legacy_status(); value["intake_cursor"] = current_status()["intake_cursor"]; variants.append(value)
        value = legacy_status(); value["cursor_health"] = current_status()["cursor_health"]; variants.append(value)
        value = current_status(); value["intake_cursor"] = {**value["intake_cursor"], "version": 2}; variants.append(value)
        value = current_status(); value["cursor_health"] = {**value["cursor_health"], "unknown": 0}; variants.append(value)
        value = current_status(); value["cursor_health"] = {**value["cursor_health"], "version": True}; variants.append(value)
        value = current_status(); value["cursor_health"] = {**value["cursor_health"], "consecutive_drift_runs": True}; variants.append(value)
        value = current_status(); value["source"] = {**value["source"], "repository_id": 1}; variants.append(value)
        value = current_status(); value["receipt_coverage"] = {"records": 1, "receipted_records": 2}; variants.append(value)
        for value in variants:
            with self.subTest(fields=set(value)), self.assertRaises(ValueError):
                validate_intake_status(
                    value,
                    repository="cylon58/omarchy-community-knowledge",
                    repository_id=1373429914,
                    deployment="production",
                )

    def test_actual_v2_producer_success_partial_and_failure_shapes_are_recognized(self):
        """Break caught: strict status parsing rejects a real refresh producer variant."""
        from omarchy_knowledge.intake_status import PublicStatusKind, validate_intake_status
        from omarchy_knowledge.resolution import refresh_upstream
        from tests.test_live_resolution import AUTHORITY, Catalogs, GitHub, NOW, event

        source_event = event()
        github, catalogs = GitHub(source_event), Catalogs()
        success = refresh_upstream(
            [source_event], github=github, catalogs=catalogs,
            authority=AUTHORITY, now=NOW,
        )
        partial_catalogs = Catalogs()
        partial_catalogs.outage = True
        partial = refresh_upstream(
            [source_event], github=GitHub(source_event), catalogs=partial_catalogs,
            authority=AUTHORITY, now=NOW,
        )
        github.outage = True
        failure = refresh_upstream(
            [source_event], github=github, catalogs=catalogs,
            authority=AUTHORITY, now=NOW,
        )
        for upstream in (success, partial, failure):
            value = legacy_status()
            value["upstream"] = upstream
            observed = validate_intake_status(
                value,
                repository="cylon58/omarchy-community-knowledge",
                repository_id=1373429914,
                deployment="production",
            )
            self.assertIs(observed.kind, PublicStatusKind.LEGACY)

    def test_nested_v2_producer_mutations_are_not_recognized_legacy(self):
        """Break caught: arbitrary nested dictionaries survive the status boundary."""
        from omarchy_knowledge.intake_status import validate_intake_status
        from omarchy_knowledge.resolution import refresh_upstream
        from tests.test_live_resolution import AUTHORITY, Catalogs, GitHub, NOW, event

        source_event = event()
        github, catalogs = GitHub(source_event), Catalogs()
        success = refresh_upstream(
            [source_event], github=github, catalogs=catalogs,
            authority=AUTHORITY, now=NOW,
        )
        github.outage = True
        failure = refresh_upstream(
            [source_event], github=github, catalogs=catalogs,
            authority=AUTHORITY, now=NOW,
        )
        variants = []
        value = legacy_status(); value["upstream"]["version"] = True; variants.append(value)
        changed = deepcopy(success); changed["version"] = True
        value = legacy_status(); value["upstream"] = changed; variants.append(value)
        for mutate in (
            lambda value: value["catalogs"].__setitem__(0, None),
            lambda value: value["observations"].__setitem__(0, None),
            lambda value: value["resolutions"].__setitem__(0, None),
            lambda value: value.__setitem__("repository_source", None),
            lambda value: value.__setitem__("release_discovery", 42),
            lambda value: value["catalogs"][0].__setitem__("unknown", True),
            lambda value: value["observations"][0].__setitem__("observation_version", True),
            lambda value: value["resolutions"][0]["diagnostics"].append(None),
            lambda value: value["catalogs"][0]["packages"][0].__setitem__("unknown", True),
            lambda value: value["catalogs"][0]["packages"][0].__setitem__("fact_version", True),
            lambda value: value["release_discovery"]["sources"][0].__setitem__("unknown", True),
            lambda value: value["resolutions"][0]["source_facts"].__setitem__("unknown", True),
            lambda value: value["resolutions"][0]["declaration_scan"]["sources"].__setitem__(0, None),
            lambda value: value["resolutions"][0]["declaration"]["body"].__setitem__("unknown", True),
        ):
            changed = deepcopy(success)
            mutate(changed)
            value = legacy_status(); value["upstream"] = changed; variants.append(value)
        changed = deepcopy(failure); changed["diagnostic"] = None
        value = legacy_status(); value["upstream"] = changed; variants.append(value)
        for value in variants:
            with self.subTest(upstream=value["upstream"]), self.assertRaises(ValueError):
                validate_intake_status(
                    value,
                    repository="cylon58/omarchy-community-knowledge",
                    repository_id=1373429914,
                    deployment="production",
                )

    def test_actual_v2_producer_with_nightly_discovery_tag_is_recognized(self):
        """Break caught: discovery grammar is narrowed to resolution-eligible tags."""
        from omarchy_knowledge.intake_status import PublicStatusKind, validate_intake_status
        from omarchy_knowledge.resolution import refresh_upstream
        from tests.test_live_resolution import AUTHORITY, Catalogs, GitHub, NOW, event

        source_event = event()
        github = GitHub(source_event)
        original_releases = github.releases

        def with_nightly(repo, page):
            snapshot = original_releases(repo, page)
            release = dict(snapshot.payload["items"][0], id=101, tag_name="nightly")
            snapshot.payload["items"].append(release)
            return snapshot

        github.releases = with_nightly
        upstream = refresh_upstream(
            [source_event], github=github, catalogs=Catalogs(),
            authority=AUTHORITY, now=NOW,
        )
        self.assertEqual(upstream["status"], "refreshed")
        value = legacy_status()
        value["upstream"] = upstream
        observed = validate_intake_status(
            value,
            repository="cylon58/omarchy-community-knowledge",
            repository_id=1373429914,
            deployment="production",
        )
        self.assertIs(observed.kind, PublicStatusKind.LEGACY)

    def test_status_transport_maps_invalid_json_status_and_oversize_to_unavailable(self):
        """Break caught: a failed Pages read becomes recognized legacy state."""
        from omarchy_knowledge.github_native import (
            GitHubRead, MAX_RESPONSE, NativeUnavailable,
        )

        fixtures = [
            PagesConnection(b"{"),
            PagesConnection(json.dumps(current_status()).encode(), status=302),
            PagesConnection(b" " * (MAX_RESPONSE + 1)),
        ]
        for fixture, charged in zip(fixtures, (1, MAX_RESPONSE + 1,
                                               MAX_RESPONSE + 1)):
            with self.subTest(status=fixture.status, size=len(fixture.raw)):
                reader = GitHubRead(connection_factory=fixture.factory)
                with self.assertRaises(NativeUnavailable):
                    reader.intake_status()
                self.assertEqual(reader.http.calls, 1)
                self.assertEqual(reader.http.bytes, charged)

    def test_status_read_obeys_existing_deadline_without_attempt(self):
        """Break caught: status retrieval creates a fresh deadline budget."""
        from omarchy_knowledge.github_native import GitHubRead, NativeUnavailable

        fixture = PagesConnection(json.dumps(legacy_status()).encode())
        reader = GitHubRead(connection_factory=fixture.factory)
        reader.http.deadline = time.monotonic() - 1
        with self.assertRaises(NativeUnavailable):
            reader.intake_status()
        self.assertEqual(fixture.requests, [])


class CandidateReadinessTests(unittest.TestCase):
    def setUp(self):
        from tests import test_admission
        from tests.test_coordinator import FakeAPI

        self.fixture = test_admission.TreeAdmission()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.api = FakeAPI(self.fixture)
        self._base_pull = self.api.pull
        self.policy = __import__(
            "omarchy_knowledge.coordinator", fromlist=["Policy"]
        ).Policy("a" * 40, "b" * 40)

    def pull(self, **changes):
        value = self._base_pull(1)
        value["mergeable"] = True
        value.update(changes)
        return value

    def assert_not_ready(self, expected, value, *, commit_info=None):
        from omarchy_knowledge.coordinator import NativeNotReady, prepare

        self.api.pull = lambda _number: value
        if commit_info is not None:
            self.api.commit_info = commit_info
        with self.assertRaises(NativeNotReady) as raised:
            prepare(self.api, self.policy, 1)
        self.assertEqual(raised.exception.reason, expected)
        self.assertEqual(str(raised.exception), expected)
        self.assertEqual(self.api.writes, 0)

    def test_fixed_reason_matrix(self):
        """Break caught: deterministic local states are collapsed into uncertainty."""
        cases = {
            "draft": self.pull(draft=True),
            "closed": self.pull(state="closed"),
            "old-base": self.pull(base={
                **self.pull()["base"], "sha": "f" * 40,
            }),
            "explicit-pending-merge-null": self.pull(
                merge_commit_sha=None, mergeable=None,
            ),
            "explicit-pending-merge-false": self.pull(
                merge_commit_sha=None, mergeable=False,
            ),
        }
        expected = {
            "draft": "draft",
            "closed": "closed",
            "old-base": "old-base",
            "explicit-pending-merge-null": "explicit-pending-merge",
            "explicit-pending-merge-false": "explicit-pending-merge",
        }
        for name, value in cases.items():
            with self.subTest(name=name):
                self.assert_not_ready(expected[name], value)

        deleted = self.pull()
        deleted["head"] = {**deleted["head"], "repo": None}
        self.assert_not_ready("source-repository-unavailable", deleted)

        changed = self.pull()
        actual = self.api.commit_info
        self.assert_not_ready(
            "changed-merge-parents", changed,
            commit_info=lambda oid: {
                **actual(oid), "parents": [self.api.base, "f" * 40],
            },
        )

    def test_identity_is_authenticated_before_any_not_ready_reason(self):
        """Break caught: attacker-controlled draft/closed/deleted fields choose outcome."""
        from omarchy_knowledge.coordinator import NativeNotReady, prepare
        from omarchy_knowledge.github_native import NativeUnavailable

        variants = []
        value = self.pull(draft=True, number=2); variants.append(value)
        value = self.pull(state="closed"); value["base"]["repo"]["id"] = 1; variants.append(value)
        value = self.pull(); value["head"]["repo"] = None; value["base"]["repo"]["full_name"] = "attacker/repo"; variants.append(value)
        for value in variants:
            with self.subTest(value=value):
                self.api.pull = lambda _number, value=value: value
                with self.assertRaises(NativeUnavailable) as raised:
                    prepare(self.api, self.policy, 1)
                self.assertNotIsInstance(raised.exception, NativeNotReady)

    def test_missing_wrong_type_and_inconsistent_fields_stay_unavailable(self):
        """Break caught: incomplete PR or merge data advances the scheduling cursor."""
        from omarchy_knowledge.coordinator import NativeNotReady, prepare
        from omarchy_knowledge.github_native import NativeUnavailable

        real_commit_info = self.api.commit_info
        variants = []
        value = self.pull(); del value["mergeable"]; variants.append((value, None))
        value = self.pull(merge_commit_sha=None, mergeable=True); variants.append((value, None))
        value = self.pull(); del value["head"]["repo"]; variants.append((value, None))
        value = self.pull(); value["head"]["repo"] = "deleted"; variants.append((value, None))
        value = self.pull(); value["base"]["sha"] = "bad"; variants.append((value, None))
        variants.append((self.pull(), lambda oid: {
            **real_commit_info(oid), "parents": [self.api.base],
        }))
        variants.append((self.pull(), lambda oid: {
            **real_commit_info(oid), "parents": [self.api.base, "BAD"],
        }))
        for value, commit_info in variants:
            with self.subTest(value=value, commit_info=commit_info is not None):
                self.api.pull = lambda _number, value=value: value
                original = self.api.commit_info
                if commit_info is not None:
                    self.api.commit_info = commit_info
                try:
                    with self.assertRaises(NativeUnavailable) as raised:
                        prepare(self.api, self.policy, 1)
                    self.assertNotIsInstance(raised.exception, NativeNotReady)
                finally:
                    self.api.commit_info = original

    def test_common_required_shape_precedes_every_local_readiness_reason(self):
        """Break caught: a local-looking reason hides incomplete PR response data."""
        from omarchy_knowledge.coordinator import NativeNotReady, prepare
        from omarchy_knowledge.github_native import NativeUnavailable

        variants = []
        value = self.pull(base={**self.pull()["base"], "sha": "f" * 40}); del value["head"]; variants.append(value)
        value = self.pull(draft=True); value["base"]["sha"] = "bad"; variants.append(value)
        value = self.pull(); value["head"] = {"repo": None}; variants.append(value)
        value = self.pull(state="closed"); del value["user"]; variants.append(value)
        value = self.pull(state="closed"); del value["mergeable"]; variants.append(value)
        for value in variants:
            with self.subTest(value=value):
                self.api.pull = lambda _number, value=value: value
                with self.assertRaises(NativeUnavailable) as raised:
                    prepare(self.api, self.policy, 1)
                self.assertNotIsInstance(raised.exception, NativeNotReady)

    def test_inconsistent_merge_combination_precedes_local_readiness_reasons(self):
        """Break caught: draft/base/source reasons hide contradictory merge state."""
        from omarchy_knowledge.coordinator import NativeNotReady, prepare
        from omarchy_knowledge.github_native import NativeUnavailable

        variants = []
        value = self.pull(draft=True, merge_commit_sha=None, mergeable=True)
        variants.append(value)
        value = self.pull(
            base={**self.pull()["base"], "sha": "f" * 40}, mergeable=None,
        )
        variants.append(value)
        value = self.pull(mergeable=False)
        value["head"] = {**value["head"], "repo": None}
        variants.append(value)
        for value in variants:
            with self.subTest(value=value):
                self.api.pull = lambda _number, value=value: value
                with self.assertRaises(NativeUnavailable) as raised:
                    prepare(self.api, self.policy, 1)
                self.assertNotIsInstance(raised.exception, NativeNotReady)

    def test_valid_candidate_still_uses_full_prepare_and_not_ready_is_old_catch_compatible(self):
        """Break caught: preflight classification replaces admission or escapes old callers."""
        from omarchy_knowledge.coordinator import NativeNotReady, prepare
        from omarchy_knowledge.github_native import NativeUnavailable

        self.api.pull = lambda _number: self.pull()
        plan = prepare(self.api, self.policy, 1)
        self.assertEqual((plan["base"], plan["head"], plan["evaluated"]),
                         (self.fixture.base, self.fixture.head, self.api.merge))
        self.assertTrue(issubclass(NativeNotReady, NativeUnavailable))


class FairIntakeListValidationTests(unittest.TestCase):
    def test_malformed_list_or_needed_row_fields_are_unavailable(self):
        """Break caught: malformed identity/state/head enters the pure scanner."""
        from omarchy_knowledge.github_native import GitHubRead, NativeUnavailable

        variants = [
            {},
            [{}],
            [{"number": True, "state": "closed"}],
            [{"number": 1, "state": "merged"}],
            [{"number": 1, "state": "open", "head": None}],
            [{"number": 1, "state": "open", "head": {}}],
            [{"number": 1, "state": "open", "head": {"sha": "A" * 40}}],
            [{"number": 1, "state": "closed"}, {"number": 1, "state": "closed"}],
            [{"number": number, "state": "closed"} for number in range(1, 22)],
        ]
        for value in variants:
            with self.subTest(value=value):
                fixture = APIConnection(json.dumps(value).encode())
                with self.assertRaises(NativeUnavailable):
                    GitHubRead(connection_factory=fixture.factory).intake_page(1)

    def test_list_read_uses_existing_response_and_aggregate_limits(self):
        """Break caught: fair-list reads bypass shared byte or deadline accounting."""
        from omarchy_knowledge.github_native import (
            GitHubRead, MAX_BYTES, MAX_RESPONSE, NativeUnavailable,
        )

        oversized = APIConnection(b" " * (MAX_RESPONSE + 1))
        with self.assertRaises(NativeUnavailable):
            GitHubRead(connection_factory=oversized.factory).intake_page(1)

        fixture = APIConnection(b"[]")
        reader = GitHubRead(connection_factory=fixture.factory)
        reader.http.bytes = MAX_BYTES
        with self.assertRaises(NativeUnavailable):
            reader.intake_page(1)
        self.assertEqual(fixture.requests, [])

        reader = GitHubRead(connection_factory=fixture.factory)
        reader.http.deadline = time.monotonic() - 1
        with self.assertRaises(NativeUnavailable):
            reader.intake_page(1)
        self.assertEqual(fixture.requests, [])


if __name__ == "__main__":
    unittest.main()
