"""Required PR fields are tied to the supported, explicit REST API contract."""
import copy
import io
import json
import unittest

from tests import test_admission as admission_tests
from tests.test_coordinator import FakeAPI
from omarchy_knowledge.coordinator import Policy, prepare
from omarchy_knowledge.github_native import GitHubRead, NativeUnavailable


class NativeAPIContractTests(unittest.TestCase):
    def setUp(self):
        self.fixture = admission_tests.TreeAdmission()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.api = FakeAPI(self.fixture)
        self.policy = Policy("a" * 40, "b" * 40)
        self.headers = []

    def use_pr_transport(self, payload, *, omit_merge=False):
        observed = self.headers

        class Connection:
            def __init__(self, host, timeout):
                assert host == "api.github.com"

            def request(self, method, path, body=None, headers=None):
                assert method == "GET"
                assert path == "/repos/cylon58/omarchy-community-knowledge/pulls/1"
                assert body is None
                observed.append(headers)
                result = copy.deepcopy(payload)
                # GitHub's documented 2026-03-10 removal, also observed on pilot PR1.
                if headers["X-GitHub-Api-Version"] != "2022-11-28" or omit_merge:
                    result.pop("merge_commit_sha", None)
                self.response = io.BytesIO(json.dumps(result).encode())

            def getresponse(self):
                self.response.status = 200
                return self.response

            def close(self):
                self.response.close()

        self.api.pull = GitHubRead(connection_factory=Connection).pull

    def test_supported_header_allows_required_merge_field_and_exact_plan(self):
        self.use_pr_transport(self.api.pull(1))
        plan = prepare(self.api, self.policy, 1)
        self.assertEqual(self.headers[-1]["X-GitHub-Api-Version"], "2022-11-28")
        self.assertEqual(plan["base"], self.fixture.base)
        self.assertEqual(plan["head"], self.fixture.head)
        self.assertEqual(plan["tree"], self.fixture.reader.commit(self.fixture.head))
        self.assertEqual(len(plan["additions"]), 1)
        self.assertEqual(self.api.writes, 0)

    def test_missing_merge_field_still_fails_closed(self):
        self.use_pr_transport(self.api.pull(1), omit_merge=True)
        with self.assertRaises(NativeUnavailable):
            prepare(self.api, self.policy, 1)
        self.assertEqual(self.api.base, self.fixture.base)
        self.assertEqual(self.api.writes, 0)

    def test_supported_header_does_not_relax_exact_merge_parent_binding(self):
        payload = self.api.pull(1)
        payload["merge_commit_sha"] = self.fixture.head  # Has only B, not [B,H].
        self.use_pr_transport(payload)
        with self.assertRaises(NativeUnavailable):
            prepare(self.api, self.policy, 1)
        self.assertEqual(self.api.base, self.fixture.base)
        self.assertEqual(self.api.writes, 0)


if __name__ == "__main__":
    unittest.main()
