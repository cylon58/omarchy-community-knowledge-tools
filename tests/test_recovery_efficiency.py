"""Recovery efficiency without weakening exact receipt authentication."""
import base64
from collections import Counter
import copy
from dataclasses import replace
import json
import unittest

from tests import test_admission as admission_tests
from tests.test_coordinator import FakeAPI
from tests.test_records import case, change, report


class CountingObjects:
    def __init__(self, objects):
        self.objects = objects
        self.entry_calls = Counter()

    def entries(self, tree):
        self.entry_calls[tree] += 1
        return self.objects.entries(tree)

    def __getattr__(self, name):
        return getattr(self.objects, name)


class RecoveryEfficiencyTests(admission_tests.TreeAdmission):
    def multi_record_api(self):
        api = FakeAPI(self)
        records = [case(), change(), report()]
        files = {f"records/{record['type']}s/{record['id']}.json":
                 ("100644", json.dumps(record).encode()) for record in records}
        api.head = self.commit(files, api.base)
        api.merge = self.git("commit-tree", self.reader.commit(api.head),
                             "-p", api.base, "-p", api.head,
                             data=b"GitHub test merge\n")
        return api

    def accepted_without_receipts(self, api, policy):
        from omarchy_knowledge.coordinator import _message, prepare
        plan = prepare(api, policy, 1)
        additions = {item["path"]: base64.b64decode(item["content"], validate=True)
                     for item in plan["additions"]}
        accepted = api.create_commit(plan["base"], additions, _message(plan))
        return plan, api.commit_info(accepted)

    def install_receipts(self, api, plan, info, indexes):
        additions = self.receipt_additions(plan, info, indexes)
        api.create_commit(api.base, additions, "Fixture partial receipt set")

    def receipt_additions(self, plan, info, indexes):
        from omarchy_knowledge.coordinator import (_receipt, _receipt_path,
                                                   canonical)
        additions = {}
        for index in indexes:
            item = plan["additions"][index]
            record = json.loads(base64.b64decode(item["content"], validate=True))
            path = _receipt_path(plan["repository_id"], info["oid"], record["id"])
            additions[path] = canonical(_receipt(plan, info, item, record)) + b"\n"
        return additions

    def receipt_candidate(self, additions, base, policy):
        from omarchy_knowledge.admission import TreeCandidateV1
        files = {entry.path.decode(): (entry.mode, self.reader.blob(entry.oid))
                 for entry in self.reader.entries(self.reader.commit(base))
                 if entry.kind != "tree"}
        files.update({path: ("100644", raw) for path, raw in additions.items()})
        head = self.commit(files, base)
        candidate = TreeCandidateV1(
            policy.repository_id, "merge-group", base, head, head,
            self.git("rev-parse", head + "^{tree}"), policy.policy_revision,
            "ingestion-receipt")
        exact = tuple(sorted((path, self.git("rev-parse", head + ":" + path))
                             for path in additions))
        return candidate, exact

    def assert_authenticated_source(self, api, policy, plan, accepted):
        from omarchy_knowledge.coordinator import authenticated_receipts
        receipts = authenticated_receipts(api, policy, api.base)
        self.assertEqual(len(receipts), len(plan["additions"]))
        self.assertEqual({item["source"]["accepted_commit_oid"]["hex"] for item in receipts},
                         {accepted})
        self.assertEqual({item["source"]["head_commit_oid"]["hex"] for item in receipts},
                         {plan["head"]})
        self.assertEqual({item["actor"]["account_id"] for item in receipts}, {"71"})

    def test_complete_multi_record_import_skips_repair_after_one_grouped_history_expansion(self):
        """Break caught: complete imports are revalidated/repaired once per addition."""
        from omarchy_knowledge.coordinator import Policy, prepare, publish, reconcile
        policy = Policy("a" * 40, "b" * 40)
        api = self.multi_record_api()
        plan = prepare(api, policy, 1)
        result = publish(api, policy, plan)
        self.assertEqual(result.status, "accepted")
        self.assertEqual(api.writes, 2)

        counted = CountingObjects(api.objects)
        api.objects = counted
        writes = api.writes
        self.assertEqual(reconcile(api, policy).status, "complete")
        self.assertEqual(api.writes, writes)
        accepted_tree = self.reader.commit(result.accepted_commit_oid)
        self.assertEqual(counted.entry_calls[accepted_tree], 1)
        self.assert_authenticated_source(api, policy, plan, result.accepted_commit_oid)

    def test_partial_coverage_is_repaired_in_one_cas_from_immutable_identity(self):
        """Break caught: each missing receipt is written by a separate mutation."""
        from omarchy_knowledge.coordinator import Policy, reconcile
        policy = Policy("a" * 40, "b" * 40)
        api = self.multi_record_api()
        plan, info = self.accepted_without_receipts(api, policy)
        self.install_receipts(api, plan, info, [0])
        writes = api.writes
        api.head = api.base
        api.pull = lambda _: (_ for _ in ()).throw(AssertionError("mutable PR consulted"))

        self.assertEqual(reconcile(api, policy).status, "complete")
        self.assertEqual(api.writes, writes + 1)
        self.assert_authenticated_source(api, policy, plan, info["oid"])

    def test_forged_receipt_and_changed_current_record_fail_closed_without_write(self):
        """Break caught: presence or historical-only validation is treated as coverage."""
        from omarchy_knowledge.coordinator import Policy, prepare, publish, reconcile
        policy = Policy("a" * 40, "b" * 40)
        for mutation in ("receipt", "record"):
            with self.subTest(mutation=mutation):
                api = self.multi_record_api()
                result = publish(api, policy, prepare(api, policy, 1))
                self.assertEqual(result.status, "accepted")
                files = {entry.path.decode(): (entry.mode, self.reader.blob(entry.oid))
                         for entry in self.reader.entries(self.reader.commit(api.base))
                         if entry.kind != "tree"}
                if mutation == "receipt":
                    path = next(path for path in files if path.startswith("provenance/ingestion/"))
                    forged = json.loads(files[path][1])
                    forged["actor"]["account_id"] = "72"
                    files[path] = ("100644", json.dumps(forged).encode())
                else:
                    changed = copy.deepcopy(case())
                    changed["payload"]["title"] = "Changed canonical bytes"
                    files[f"records/cases/{changed['id']}.json"] = (
                        "100644", json.dumps(changed).encode())
                api.base = self.commit(files, api.base)
                writes = api.writes
                current = api.base
                self.assertEqual(reconcile(api, policy).status, "retry")
                self.assertEqual((api.writes, api.base), (writes, current))

    def test_cas_conflict_is_not_retried_and_restart_repairs_the_whole_missing_set(self):
        """Break caught: a recovery CAS conflict triggers per-receipt retries."""
        from omarchy_knowledge.coordinator import Policy, reconcile
        policy = Policy("a" * 40, "b" * 40)
        api = self.multi_record_api()
        plan, info = self.accepted_without_receipts(api, policy)
        self.install_receipts(api, plan, info, [0])
        writes = api.writes

        def concurrent_change():
            files = {entry.path.decode(): (entry.mode, self.reader.blob(entry.oid))
                     for entry in self.reader.entries(self.reader.commit(api.base))
                     if entry.kind != "tree"}
            files["README.md"] = ("100644", b"Concurrent trusted maintenance\n")
            api.base = self.commit(files, api.base)

        api.before_write = concurrent_change
        self.assertEqual(reconcile(api, policy).status, "retry")
        self.assertEqual(api.writes, writes)
        self.assertEqual(reconcile(api, policy).status, "complete")
        self.assertEqual(api.writes, writes + 1)
        self.assert_authenticated_source(api, policy, plan, info["oid"])

    def test_ambiguous_batched_success_is_inspected_once_and_restart_is_idle(self):
        """Break caught: ambiguous recovery success duplicates or retries additions."""
        from omarchy_knowledge.coordinator import Policy, reconcile
        policy = Policy("a" * 40, "b" * 40)
        api = self.multi_record_api()
        plan, info = self.accepted_without_receipts(api, policy)
        self.install_receipts(api, plan, info, [0])
        writes = api.writes
        api.ambiguous = True

        self.assertEqual(reconcile(api, policy).status, "complete")
        self.assertEqual(api.writes, writes + 1)
        self.assert_authenticated_source(api, policy, plan, info["oid"])
        self.assertEqual(reconcile(api, policy).status, "complete")
        self.assertEqual(api.writes, writes + 1)

    def test_coordinator_import_grant_rejects_every_inexact_binding(self):
        """Break caught: a typed grant is mistaken for authenticated exact authority."""
        from omarchy_knowledge.admission import CoordinatorImportGrant, check_tree
        from omarchy_knowledge.coordinator import Policy
        policy = Policy("a" * 40, "b" * 40)
        api = self.multi_record_api()
        plan, info = self.accepted_without_receipts(api, policy)
        additions = self.receipt_additions(plan, info, [0, 1])
        candidate, exact = self.receipt_candidate(additions, api.base, policy)
        grant = CoordinatorImportGrant(policy.repository_id, info["oid"],
                                       policy.policy_revision, exact)
        self.assertEqual(check_tree(candidate, self.reader,
                                    trusted_import_grant=grant)["decision"], "accept")
        mismatches = [
            replace(grant, repository_id=policy.repository_id + 1),
            replace(grant, policy_revision="f" * 40),
            replace(grant, accepted_commit_oid=plan["base"]),
            replace(grant, additions=((exact[0][0], "f" * 40), exact[1])),
        ]
        for mismatched in mismatches:
            with self.subTest(grant=mismatched):
                result = check_tree(candidate, self.reader,
                                    trusted_import_grant=mismatched)
                self.assertEqual(result["decision"], "reject")
                self.assertEqual(result["violations"][0]["code"],
                                 "AUTHORITY_MISMATCH")

    def test_one_coordinator_grant_cannot_mix_accepted_commits(self):
        """Break caught: an exact path tuple batches receipts from different imports."""
        from omarchy_knowledge.admission import CoordinatorImportGrant, check_tree
        from omarchy_knowledge.coordinator import Policy, canonical
        policy = Policy("a" * 40, "b" * 40)
        api = self.multi_record_api()
        plan, info = self.accepted_without_receipts(api, policy)
        additions = self.receipt_additions(plan, info, [0, 1])
        second_path = sorted(additions)[1]
        second = json.loads(additions[second_path])
        other = self.git("commit-tree", self.reader.commit(info["oid"]),
                         "-p", info["oid"], data=b"Trusted maintenance\n")
        second["source"]["accepted_commit_oid"]["hex"] = other
        additions[second_path] = canonical(second) + b"\n"
        candidate, exact = self.receipt_candidate(additions, api.base, policy)
        grant = CoordinatorImportGrant(policy.repository_id, info["oid"],
                                       policy.policy_revision, exact)
        result = check_tree(candidate, self.reader, trusted_import_grant=grant)
        self.assertEqual(result["decision"], "reject")
        self.assertEqual(result["violations"][0]["code"], "AUTHORITY_MISMATCH")

    def test_multifile_bot_grant_does_not_gain_coordinator_batch_ceiling(self):
        """Break caught: the coordinator-only receipt cap increase reaches bot grants."""
        from omarchy_knowledge.admission import BotAuthorityGrant, check_tree
        from omarchy_knowledge.coordinator import Policy
        policy = Policy("a" * 40, "b" * 40)
        api = self.multi_record_api()
        plan, info = self.accepted_without_receipts(api, policy)
        additions = self.receipt_additions(plan, info, [0, 1])
        candidate, exact = self.receipt_candidate(additions, api.base, policy)
        grant = BotAuthorityGrant(
            policy.repository_id, 1, 71, "Bot", policy.repository_id,
            candidate.head_commit_oid, candidate.evaluated_commit_oid,
            policy.policy_revision, "ingestion-receipt", exact)
        result = check_tree(candidate, self.reader, trusted_bot_grant=grant)
        self.assertEqual(result["decision"], "indeterminate")
        self.assertEqual(result["violations"][0]["code"],
                         "OBJECT_OR_RESOURCE_UNAVAILABLE")

    def test_wrong_accepted_commit_and_receipt_path_fail_recovery(self):
        """Break caught: plausible receipt bytes or presence authorize complete coverage."""
        from omarchy_knowledge.coordinator import Policy, prepare, publish, reconcile
        policy = Policy("a" * 40, "b" * 40)
        for mutation in ("accepted", "path"):
            with self.subTest(mutation=mutation):
                api = self.multi_record_api()
                result = publish(api, policy, prepare(api, policy, 1))
                self.assertEqual(result.status, "accepted")
                files = {entry.path.decode(): (entry.mode, self.reader.blob(entry.oid))
                         for entry in self.reader.entries(self.reader.commit(api.base))
                         if entry.kind != "tree"}
                path = next(path for path in files
                            if path.startswith("provenance/ingestion/"))
                if mutation == "accepted":
                    receipt = json.loads(files[path][1])
                    receipt["source"]["accepted_commit_oid"]["hex"] = plan_base = (
                        self.git("rev-parse", result.accepted_commit_oid + "^"))
                    self.assertNotEqual(plan_base, result.accepted_commit_oid)
                    files[path] = ("100644", json.dumps(receipt).encode())
                else:
                    raw = files.pop(path)
                    wrong = "provenance/ingestion/00000000-0000-4000-8000-000000000000.json"
                    self.assertNotEqual(path, wrong)
                    files[wrong] = raw
                api.base = self.commit(files, api.base)
                writes, current = api.writes, api.base
                self.assertEqual(reconcile(api, policy).status, "retry")
                self.assertEqual((api.writes, api.base), (writes, current))


class WriterBoundaryTests(unittest.TestCase):
    def test_actual_writer_builds_one_expected_head_request_for_receipt_batch(self):
        """Break caught: coordinator batches pass fakes but production rejects them."""
        from omarchy_knowledge.github_native import GitHubWriter
        captured = []

        class Response:
            status = 200
            raw = (b'{"data":{"createCommitOnBranch":{"commit":{"oid":"'
                   + b"c" * 40 + b'"}}}}')

            def read(self, count):
                raw, self.raw = self.raw[:count], self.raw[count:]
                return raw

        class Connection:
            def __init__(self, host, timeout):
                self.host, self.timeout = host, timeout

            def request(self, method, path, body=None, headers=None):
                captured.append((method, path, body, headers))

            def getresponse(self):
                return Response()

            def close(self):
                pass

        paths = [
            f"provenance/ingestion/00000000-0000-4000-8000-{index:012d}.json"
            for index in reversed(range(10))
        ]
        writer = GitHubWriter("fixture-token", connection_factory=Connection)
        result = writer.create_commit("b" * 40,
                                      {path: json.dumps({"index": index}).encode()
                                       for index, path in enumerate(paths)},
                                      "Record source-bound ingestion receipt")
        self.assertEqual(result, "c" * 40)
        method, endpoint, body, headers = captured.pop()
        self.assertEqual((method, endpoint), ("POST", "/graphql"))
        request = json.loads(body)["variables"]["input"]
        self.assertEqual(request["expectedHeadOid"], "b" * 40)
        self.assertEqual(request["message"],
                         {"headline": "Record source-bound ingestion receipt"})
        self.assertEqual([item["path"] for item in
                          request["fileChanges"]["additions"]], sorted(paths))
        self.assertEqual(headers["Authorization"], "Bearer fixture-token")
        self.assertNotIn("fixture-token", body.decode())

    def test_receipt_batch_keeps_writer_expected_head_message_path_and_size_guards(self):
        """Break caught: batch support broadens the fixed mutation lane."""
        from omarchy_knowledge.github_native import GitHubWriter, NativeUnavailable

        def forbidden(*_args, **_kwargs):
            raise AssertionError("invalid mutation reached HTTP")

        writer = GitHubWriter("fixture-token", connection_factory=forbidden)
        receipt = "provenance/ingestion/00000000-0000-4000-8000-000000000000.json"
        additions = {receipt: b"{}"}
        invalid = [
            ("bad-base", additions, "Record source-bound ingestion receipt"),
            ("b" * 40, additions, "Record source-bound ingestion receipt\n\nbody"),
            ("b" * 40, additions, "Wrong mutation lane"),
            ("b" * 40,
             {"records/cases/00000000-0000-4000-8000-000000000000.json": b"{}"},
             "Record source-bound ingestion receipt"),
            ("b" * 40,
             {f"provenance/ingestion/00000000-0000-4000-8000-{index:012d}.json": b"{}"
              for index in range(11)},
             "Record source-bound ingestion receipt"),
        ]
        for expected, files, message in invalid:
            with self.subTest(expected=expected, count=len(files), message=message):
                with self.assertRaises(NativeUnavailable):
                    writer.create_commit(expected, files, message)


# Reuse the real Git fixture without inheriting its admission test suite.
for _name in list(vars(admission_tests.TreeAdmission)):
    if _name.startswith("test_"):
        setattr(RecoveryEfficiencyTests, _name, None)
