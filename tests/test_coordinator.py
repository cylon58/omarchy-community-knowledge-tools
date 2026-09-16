"""Native coordinator behavior over real local Git objects and an offline API."""
import copy
import json
import unittest
from unittest.mock import patch

from tests import test_admission as admission_tests


class CoordinatorTests(admission_tests.TreeAdmission):
    # Reuse only the real Git fixture, not its inherited tests.
    def test_native_import_receipt_and_idempotent_recovery(self):
        from omarchy_knowledge.coordinator import Policy, prepare, publish, reconcile
        policy = Policy("a" * 40, "b" * 40)
        api = FakeAPI(self)
        plan = prepare(api, policy, 1)
        result = publish(api, policy, plan)
        self.assertEqual(result.status, "accepted")
        entries = {e.path.decode(): e for e in self.reader.entries(self.reader.commit(api.base))}
        receipts = [e for p, e in entries.items() if p.startswith("provenance/ingestion/") and e.kind == "blob"]
        self.assertEqual(len(receipts), 1)
        receipt = json.loads(self.reader.blob(receipts[0].oid))
        self.assertEqual(receipt["receipt_version"], 2)
        self.assertEqual(receipt["source"]["head_commit_oid"]["hex"], self.head)
        self.assertEqual(receipt["actor"]["account_id"], "71")
        self.assertEqual(receipt["source"]["accepted_commit_oid"]["hex"], result.accepted_commit_oid)
        writes = api.writes
        self.assertEqual(publish(api, policy, plan).status, "accepted")
        self.assertEqual(reconcile(api, policy).status, "complete")
        self.assertEqual(api.writes, writes)

    def test_stale_base_and_changed_head_do_not_write(self):
        from omarchy_knowledge.coordinator import Policy, prepare, publish
        policy = Policy("a" * 40, "b" * 40)
        for race in ("base", "head", "cas"):
            api = FakeAPI(self)
            plan = prepare(api, policy, 1)
            new = self.commit({"README.md": ("100644", b"Trusted update")}, self.base)
            if race == "base":
                api.base = new
            elif race == "head":
                api.head = new
            else:
                api.before_write = lambda: setattr(api, "base", new)
            self.assertEqual(publish(api, policy, plan).status, "retry")
            self.assertEqual(api.writes, 0)

    def test_receipt_failure_and_changed_source_recovery_uses_persisted_identity(self):
        from omarchy_knowledge.coordinator import Policy, prepare, publish, reconcile
        policy = Policy("a" * 40, "b" * 40)
        api = FakeAPI(self)
        plan = prepare(api, policy, 1)
        api.fail_receipt = True
        result = publish(api, policy, plan)
        self.assertEqual(result.status, "receipt-pending")
        self.assertEqual(api.writes, 1)
        api.head = self.base
        api.fail_receipt = False
        # Recovery may not infer author/head from current PR state.
        api.pull = lambda _: (_ for _ in ()).throw(AssertionError("Mutable PR consulted"))
        self.assertEqual(reconcile(api, policy).status, "complete")
        self.assertEqual(api.writes, 2)

    def test_ambiguous_success_does_not_duplicate_import(self):
        from omarchy_knowledge.coordinator import Policy, prepare, publish
        policy = Policy("a" * 40, "b" * 40)
        api = FakeAPI(self)
        plan = prepare(api, policy, 1)
        api.ambiguous = True
        api.before_write = lambda: setattr(api, "head", self.base)
        result = publish(api, policy, plan)
        self.assertEqual(result.status, "accepted")
        self.assertTrue(result.head_changed)
        self.assertEqual(api.writes, 2)

    def test_crafted_plans_are_revalidated_before_writes(self):
        from omarchy_knowledge.coordinator import Policy, prepare, publish
        policy = Policy("a" * 40, "b" * 40)
        api = FakeAPI(self)
        original = prepare(api, policy, 1)
        for field, value in [("actor_account_id", 72), ("repository_id", 1), ("head_repository_id", 1),
                             ("head", self.base), ("tree", "f" * 40), ("corpus_digest", "accept"),
                             ("policy_revision", "f" * 40), ("toolkit_revision", "e" * 40)]:
            plan = copy.deepcopy(original)
            plan[field] = value
            self.assertEqual(publish(api, policy, plan).status, "retry", field)
        plan = copy.deepcopy(original)
        plan["additions"][0]["path"] = ".github/workflows/write.yml"
        self.assertEqual(publish(api, policy, plan).status, "retry")
        self.assertEqual(api.writes, 0)

    def test_native_plan_rejects_all_noncommunity_changes(self):
        from omarchy_knowledge.coordinator import Policy, prepare
        from omarchy_knowledge.github_native import NativeUnavailable
        policy = Policy("a" * 40, "b" * 40)
        for path, mode, raw in [(".github/workflows/run.yml", "100644", b"inert"),
                                (self.path, "120000", b"/etc/passwd"),
                                ("provenance/ingestion/" + self.path.rsplit("/", 1)[1], "100644", b"{}"),
                                ("schemas/v1/case.schema.json", "100644", b"{}"),
                                (self.path, "100644", b"{}")]:
            api = FakeAPI(self)
            api.head = self.commit({path: (mode, raw)}, self.base)
            api.merge = self.git("commit-tree", self.reader.commit(api.head), "-p", self.base,
                                 "-p", api.head, data=b"test merge\n")
            with self.assertRaises(NativeUnavailable):
                prepare(api, policy, 1)
            self.assertEqual(api.writes, 0)

    def test_object_api_reconstructs_ordinary_non_utc_commit_and_tree(self):
        from omarchy_knowledge.github_native import APIObjects
        raw = ("tree " + self.reader.commit(self.head) + "\nparent " + self.base +
               "\nauthor Fixture <fixture@example.org> 1000000000 -0400\n"
               "committer Fixture <fixture@example.org> 1000000000 +0530\n\nSynthetic\n").encode()
        commit = self.git("hash-object", "-t", "commit", "-w", "--stdin", data=raw)
        payload = {"sha": commit, "tree": {"sha": self.reader.commit(self.head)}, "parents": [{"sha": self.base}],
                   "author": {"name": "Fixture", "email": "fixture@example.org", "date": "2001-09-09T01:46:40Z"},
                   "committer": {"name": "Fixture", "email": "fixture@example.org", "date": "2001-09-09T01:46:40Z"},
                   "message": "Synthetic", "verification": {"payload": None, "signature": None}}
        class Read:
            def git_commit(self, requested):
                return payload
        objects = APIObjects(Read())
        self.assertEqual(objects.commit(commit), self.reader.commit(self.head))
        payload["message"] = "Forged"
        from omarchy_knowledge.github_native import NativeUnavailable
        with self.assertRaises(NativeUnavailable):
            APIObjects(Read()).commit(commit)

    def test_cli_serialization_cannot_supply_authority(self):
        from omarchy_knowledge.coordinator import load_plan, Policy, prepare
        from omarchy_knowledge.github_native import NativeUnavailable
        plan = prepare(FakeAPI(self), Policy("a" * 40, "b" * 40), 1)
        plan["trusted_import_grant"] = {"repository_id": 1373429914}
        with self.assertRaises(NativeUnavailable):
            load_plan(json.dumps(plan).encode())
        with self.assertRaises(NativeUnavailable):
            load_plan(b" " * (1024 * 1024 + 1))

    def test_missing_objects_and_api_repository_identity_fail_closed(self):
        from omarchy_knowledge.coordinator import Policy, prepare
        from omarchy_knowledge.github_native import NativeUnavailable
        policy = Policy("a" * 40, "b" * 40)
        for mutation in (lambda api: setattr(api, "head", "f" * 40),
                         lambda api: setattr(api, "repository", lambda: {"id": 1, "full_name": "attacker/repo", "default_branch": "main"})):
            api = FakeAPI(self)
            mutation(api)
            with self.assertRaises(NativeUnavailable):
                prepare(api, policy, 1)
            self.assertEqual(api.writes, 0)

    def test_duplicate_record_ids_fail_native_corpus_check(self):
        from omarchy_knowledge.coordinator import Policy, prepare
        from omarchy_knowledge.github_native import NativeUnavailable
        from tests.test_records import change
        same_id = change()
        same_id["id"] = self.path.rsplit("/", 1)[1][:-5]
        api = FakeAPI(self)
        # Include the valid case plus a different canonical record with its UUID.
        from tests.test_records import case
        api.head = self.commit({self.path: ("100644", json.dumps(case()).encode()),
                                self.path.replace("cases", "changes"): ("100644", json.dumps(same_id).encode())}, self.base)
        api.merge = self.git("commit-tree", self.reader.commit(api.head), "-p", self.base, "-p", api.head, data=b"merge\n")
        with self.assertRaises(NativeUnavailable):
            prepare(api, Policy("a" * 40, "b" * 40), 1)

    def test_receipt_source_mismatch_is_not_overwritten(self):
        from omarchy_knowledge.coordinator import Policy, prepare, publish, reconcile
        policy = Policy("a" * 40, "b" * 40)
        api = FakeAPI(self)
        self.assertEqual(publish(api, policy, prepare(api, policy, 1)).status, "accepted")
        entries = self.reader.entries(self.reader.commit(api.base))
        files = {e.path.decode(): (e.mode, self.reader.blob(e.oid)) for e in entries if e.kind != "tree"}
        path = next(p for p in files if p.startswith("provenance/"))
        receipt = json.loads(files[path][1])
        receipt["actor"]["account_id"] = "72"
        files[path] = ("100644", json.dumps(receipt).encode())
        api.base = self.commit(files, api.base)
        before = api.base
        self.assertEqual(reconcile(api, policy).status, "retry")
        self.assertEqual(api.base, before)

    def test_already_imported_open_snapshots_do_not_starve_new_pending_prs(self):
        from omarchy_knowledge.coordinator import Policy, prepare, publish, pending
        policy = Policy("a" * 40, "b" * 40)
        api = FakeAPI(self)
        self.assertEqual(publish(api, policy, prepare(api, policy, 1)).status, "accepted")
        api.pending = lambda page=1: ([{"number": 1, "head": self.head}, {"number": 2, "head": self.base}] if page == 1 else [])
        self.assertEqual(pending(api, policy)["pull_requests"], [2])

    def age_canonical_history(self, api):
        tree = self.reader.commit(api.base)
        for _ in range(100):
            api.base = self.git("commit-tree", tree, "-p", api.base, data=b"Trusted maintenance\n")

    def test_receipted_import_remains_not_pending_after_history_window(self):
        from omarchy_knowledge.coordinator import Policy, prepare, publish, pending, reconcile
        api = FakeAPI(self)
        policy = Policy("a" * 40, "b" * 40)
        self.assertEqual(publish(api, policy, prepare(api, policy, 1)).status, "accepted")
        api.pending = lambda page=1: [{"number": 1, "head": self.head}]
        self.age_canonical_history(api)
        self.assertEqual(reconcile(api, policy).status, "complete")
        self.assertEqual(pending(api, policy)["pull_requests"], [])
        # A different head in the same PR remains new work.
        api.pending = lambda page=1: [{"number": 1, "head": self.base}]
        self.assertEqual(pending(api, policy)["pull_requests"], [1])

    def test_twenty_aged_imports_do_not_hide_later_pending_work(self):
        import uuid
        from tests.test_records import case
        from omarchy_knowledge.coordinator import Policy, prepare, publish, pending
        api = FakeAPI(self)
        policy = Policy("a" * 40, "b" * 40)
        imported = []
        for number in range(2, 22):
            files = {e.path.decode(): (e.mode, self.reader.blob(e.oid))
                     for e in self.reader.entries(self.reader.commit(api.base)) if e.kind != "tree"}
            record = case()
            record["id"] = str(uuid.UUID(int=number, version=4))
            files[f"records/cases/{record['id']}.json"] = ("100644", json.dumps(record).encode())
            api.head = self.commit(files, api.base)
            api.merge = self.git("commit-tree", self.reader.commit(api.head), "-p", api.base,
                                 "-p", api.head, data=b"GitHub test merge\n")
            self.assertEqual(publish(api, policy, prepare(api, policy, number)).status, "accepted")
            imported.append({"number": number, "head": api.head})
        self.age_canonical_history(api)
        api.pending = lambda page=1: imported if page == 1 else [{"number": 1, "head": self.head}]
        result = pending(api, policy)
        self.assertEqual(result["pull_requests"], [1])
        self.assertEqual(result["scanned"], 21)
        self.assertFalse(result["scan_truncated"])

    def test_durable_pending_dedup_rejects_mismatched_receipt_sources(self):
        from omarchy_knowledge.coordinator import Policy, prepare, publish, pending
        from omarchy_knowledge.github_native import NativeUnavailable
        api = FakeAPI(self)
        policy = Policy("a" * 40, "b" * 40)
        self.assertEqual(publish(api, policy, prepare(api, policy, 1)).status, "accepted")
        api.pending = lambda page=1: [{"number": 1, "head": self.head}]
        original_base = api.base
        files = {e.path.decode(): (e.mode, self.reader.blob(e.oid))
                 for e in self.reader.entries(self.reader.commit(api.base)) if e.kind != "tree"}
        receipt_path = next(path for path in files if path.startswith("provenance/ingestion/"))
        original = json.loads(files[receipt_path][1])
        for field, value in (("repository_id", "1373467908"), ("pull_request", 2),
                             ("head_commit_oid", {"algorithm": "sha1", "hex": self.base}),
                             ("record_blob_oid", {"algorithm": "sha1", "hex": "f" * 40})):
            receipt = copy.deepcopy(original)
            receipt["source"][field] = value
            tampered = {**files, receipt_path: ("100644", json.dumps(receipt, sort_keys=True,
                                                                   separators=(",", ":")).encode() + b"\n")}
            api.base = self.commit(tampered, original_base)
            with self.subTest(field=field), self.assertRaises(NativeUnavailable):
                pending(api, policy)

    def test_pending_keeps_recent_import_without_receipts_out_of_new_work(self):
        from omarchy_knowledge.coordinator import Policy, prepare, publish, pending
        api = FakeAPI(self)
        policy = Policy("a" * 40, "b" * 40)
        api.fail_receipt = True
        self.assertEqual(publish(api, policy, prepare(api, policy, 1)).status, "receipt-pending")
        api.pending = lambda page=1: [{"number": 1, "head": self.head}]
        self.assertEqual(pending(api, policy)["pull_requests"], [])

    def test_api_tree_and_blob_hashes_are_independent_of_claimed_identity(self):
        import base64
        from omarchy_knowledge.github_native import APIObjects, NativeUnavailable
        fixture = self
        class Read:
            corrupt = False
            def git_tree(self, requested):
                entries = fixture.reader.entries(requested)
                return {"sha": requested, "truncated": False,
                        "tree": [{"path": e.path.decode(), "mode": e.mode, "type": e.kind,
                                  "sha": e.oid, **({"size": e.size} if e.kind == "blob" else {})}
                                 for e in entries if b"/" not in e.path]}
            def git_blob(self, requested):
                raw = fixture.reader.blob(requested)
                if self.corrupt:
                    raw = b"forged"
                return {"sha": requested, "encoding": "base64", "size": len(raw),
                        "content": base64.b64encode(raw).decode()}
        read = Read()
        objects = APIObjects(read)
        tree = self.reader.commit(self.head)
        entries = objects.entries(tree)
        self.assertEqual(sorted(entries, key=lambda e: e.path), sorted(self.reader.entries(tree), key=lambda e: e.path))
        blob = next(e.oid for e in entries if e.kind == "blob")
        self.assertEqual(objects.blob(blob), self.reader.blob(blob))
        read.corrupt = True
        with self.assertRaises(NativeUnavailable):
            APIObjects(read).blob(blob)
        original = read.git_tree
        read.git_tree = lambda requested: {**original(requested), "truncated": True}
        with self.assertRaises(NativeUnavailable):
            APIObjects(read).entries(tree)

    def test_signed_payload_commit_is_rehashed_not_trusted_by_sha_label(self):
        from omarchy_knowledge.github_native import APIObjects, NativeUnavailable
        tree = self.reader.commit(self.head)
        payload = (f"tree {tree}\nparent {self.base}\nauthor Fixture <fixture@example.org> 1000000000 -0400\n"
                   "committer Fixture <fixture@example.org> 1000000000 -0400\n\nSynthetic\n")
        signature = "-----BEGIN PGP SIGNATURE-----\nsynthetic-only\n-----END PGP SIGNATURE-----\n"
        headers, message = payload.split("\n\n", 1)
        raw = (headers + "\ngpgsig " + signature.replace("\n", "\n ") + "\n\n" + message).encode()
        commit = self.git("hash-object", "-t", "commit", "-w", "--stdin", data=raw)
        class Read:
            def git_commit(self, requested):
                return {"sha": requested, "verification": {"payload": payload, "signature": signature}}
        self.assertEqual(APIObjects(Read()).commit(commit), tree)

    def test_warm_object_cache_happens_before_offline_validation_deadline(self):
        from omarchy_knowledge.coordinator import Policy, prepare
        api = FakeAPI(self)
        original = api.objects
        class Cold:
            warmed = False
            def warm(self, commits):
                self.warmed = True
                self.commits = commits
            def __getattr__(self, key):
                return getattr(original, key)
            def begin(self):
                if not self.warmed:
                    raise AssertionError("Offline validation started before bounded retrieval")
                original.begin()
        api.objects = Cold()
        prepare(api, Policy("a" * 40, "b" * 40), 1)
        self.assertEqual(set(api.objects.commits), {api.base, api.head, api.merge})

    def test_recovery_retains_original_policy_after_toolkit_upgrade(self):
        from omarchy_knowledge.coordinator import Policy, prepare, publish, reconcile
        api = FakeAPI(self)
        old = Policy("a" * 40, "b" * 40)
        api.fail_receipt = True
        self.assertEqual(publish(api, old, prepare(api, old, 1)).status, "receipt-pending")
        api.fail_receipt = False
        self.assertEqual(reconcile(api, Policy("c" * 40, "d" * 40)).status, "complete")
        path = next(e for e in self.reader.entries(self.reader.commit(api.base))
                    if e.path.startswith(b"provenance/ingestion/") and e.kind == "blob")
        self.assertEqual(json.loads(self.reader.blob(path.oid))["policy_revision"], "a" * 40)

    def test_pilot_requires_exact_separate_numeric_repository_identity(self):
        from omarchy_knowledge.coordinator import Policy, prepare, publish
        from omarchy_knowledge.github_native import NativeUnavailable
        api = FakeAPI(self)
        pilot = Policy("a" * 40, "b" * 40, "pilot")
        with self.assertRaises(NativeUnavailable):
            prepare(api, pilot, 1)
        api.repository = lambda: {"id": 1373467908, "full_name": "cylon58/omarchy-community-knowledge-pilot", "default_branch": "main"}
        plan = prepare(api, pilot, 1)
        self.assertEqual(plan["repository_id"], 1373467908)
        self.assertEqual(publish(api, Policy("a" * 40, "b" * 40), plan).status, "retry")
        self.assertEqual(api.writes, 0)
        self.assertEqual(publish(api, pilot, plan).status, "accepted")


class HTTPTests(unittest.TestCase):
    def test_fixed_graphql_cas_and_no_credentials_in_read_headers(self):
        from omarchy_knowledge.github_native import GitHubRead, GitHubWriter
        captured = []
        class Response:
            status = 200
            def read(self, count):
                raw, self.raw = self.raw[:count], self.raw[count:]
                return raw
        class Connection:
            def __init__(self, host, timeout):
                assert host == "api.github.com"
            def request(self, method, path, body=None, headers=None):
                captured.append((method, path, body, headers))
            def getresponse(self):
                response = Response()
                response.raw = b'{"data":{"createCommitOnBranch":{"commit":{"oid":"' + b"c" * 40 + b'"}}}}'
                return response
            def close(self):
                pass
        api = GitHubRead(connection_factory=Connection)
        api.repository()
        self.assertNotIn("Authorization", captured[-1][3])
        writer = GitHubWriter("test-token-not-real", connection_factory=Connection)
        writer.repository()
        self.assertEqual(captured[-1][3]["Authorization"], "Bearer test-token-not-real")
        self.assertEqual(writer.create_commit("b" * 40, {"provenance/ingestion/00000000-0000-4000-8000-000000000000.json": b"{}"},
                                              "Record source-bound ingestion receipt"), "c" * 40)
        method, path, body, headers = captured[-1]
        self.assertEqual((method, path), ("POST", "/graphql"))
        variables = json.loads(body)["variables"]["input"]
        self.assertEqual(variables["expectedHeadOid"], "b" * 40)
        self.assertEqual(variables["branch"], {"repositoryNameWithOwner": "cylon58/omarchy-community-knowledge", "branchName": "main"})
        self.assertNotIn("test-token", body.decode())

    def test_transport_rejects_redirect_oversize_duplicate_json_and_excess_calls(self):
        from omarchy_knowledge.github_native import GitHubRead, NativeUnavailable, strict_json
        for raw in (b'{"id":1,"id":2}', b'{"id":NaN}', b'[' * 65 + b'0' + b']' * 65):
            with self.assertRaises(NativeUnavailable):
                strict_json(raw)
        class Connection:
            def __init__(self, *a, **kw):
                pass
            def request(self, *a, **kw):
                pass
            def getresponse(self):
                return self
            status = 302
            def close(self):
                pass
            def read(self, count):
                return b" " * count
        with self.assertRaises(NativeUnavailable):
            GitHubRead(connection_factory=Connection).repository()
        Connection.status = 200
        with self.assertRaises(NativeUnavailable):
            GitHubRead(connection_factory=Connection).repository()

    def test_http_worker_has_token_free_environment_and_read_stdin(self):
        from omarchy_knowledge.github_native import GitHubRead
        captured = {}
        class Process:
            returncode = 0
            def __init__(self, argv, **kwargs):
                captured.update(kwargs)
                captured["argv"] = argv
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
            def communicate(self, payload, timeout):
                captured["payload"] = payload
                return b'{"id":1373429914}', b""
        with patch.dict("os.environ", {"GITHUB_TOKEN": "must-not-inherit", "GIT_CONFIG_COUNT": "999", "PYTHONPATH": "/evil"}), \
                patch("omarchy_knowledge.github_native.subprocess.Popen", Process):
            self.assertEqual(GitHubRead().repository()["id"], 1373429914)
        self.assertEqual(captured["env"], {"PATH": "/usr/bin:/bin", "LC_ALL": "C"})
        self.assertIsNone(json.loads(captured["payload"])["token"])
        self.assertNotIn("must-not-inherit", repr(captured))

    def test_http_read_scope_rejects_other_repo_and_counts_calls(self):
        from omarchy_knowledge.github_native import GitHubRead, NativeUnavailable
        api = GitHubRead()
        for path in ("https://evil.example", "/repos/attacker/repo/git/ref/heads/main", "/repos/cylon58/omarchy-community-knowledge/issues"):
            with self.assertRaises(NativeUnavailable):
                api.http.request("GET", path)
        api.http.calls = 512
        with self.assertRaises(NativeUnavailable):
            api.repository()

    def test_mutation_transport_rejects_mixed_lanes_before_http(self):
        from omarchy_knowledge.github_native import GitHubWriter, NativeUnavailable
        def forbidden_connection(*args, **kwargs):
            raise AssertionError("Invalid mutation reached HTTP")
        writer = GitHubWriter("fixture-token", connection_factory=forbidden_connection)
        with self.assertRaises(NativeUnavailable):
            writer.create_commit("b" * 40, {
                "records/cases/00000000-0000-4000-8000-000000000000.json": b"{}",
                "provenance/ingestion/00000000-0000-4000-8000-000000000001.json": b"{}",
            }, "Record source-bound ingestion receipt")

    def test_http_timeout_kills_and_reaps_without_disclosing_token(self):
        import subprocess
        from omarchy_knowledge.github_native import GitHubRead, NativeUnavailable
        class Process:
            killed = waited = False
            def __init__(self, *args, **kwargs):
                pass
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
            def communicate(self, *args, **kwargs):
                raise subprocess.TimeoutExpired("fixed-worker", 15)
            def kill(self):
                Process.killed = True
            def wait(self):
                Process.waited = True
        with patch("omarchy_knowledge.github_native.subprocess.Popen", Process):
            with self.assertRaises(NativeUnavailable) as result:
                GitHubRead(read_token="never-print-fixture").repository()
        self.assertTrue(Process.killed and Process.waited)
        self.assertNotIn("never-print-fixture", str(result.exception))


class FakeAPI:
    """The external boundary only; all candidate/tree/receipt validation is real."""
    def __init__(self, fixture):
        self.fixture = fixture
        self.objects = fixture.reader
        self.base = fixture.base
        self.head = fixture.head
        self.merge = fixture.git("commit-tree", fixture.reader.commit(self.head), "-p", self.base,
                                 "-p", self.head, data=b"GitHub test merge\n")
        self.writes = 0
        self.fail_receipt = False
        self.ambiguous = False
        self.before_write = None

    def repository(self):
        return {"id": 1373429914, "full_name": "cylon58/omarchy-community-knowledge", "default_branch": "main"}

    def branch(self):
        return self.base

    def pull(self, number):
        return {"number": number, "state": "open", "draft": False, "merged": False,
                "user": {"id": 71, "type": "User"},
                "base": {"sha": self.base, "ref": "main", "repo": self.repository()},
                "head": {"sha": self.head, "repo": {"id": 909}},
                "merge_commit_sha": self.merge}

    def pending(self):
        return [1]

    def commit_info(self, oid):
        raw = self.fixture.git("cat-file", "commit", oid)
        headers, message = raw.split("\n\n", 1)
        return {"oid": oid, "tree": self.objects.commit(oid),
                "parents": [line[7:] for line in headers.splitlines() if line.startswith("parent ")],
                "message": message.rstrip("\n"), "date": "2026-09-16T00:00:00Z"}

    def create_commit(self, expected_base, additions, message):
        from omarchy_knowledge.github_native import NativeUnavailable
        if self.before_write:
            action, self.before_write = self.before_write, None
            action()
        if self.base != expected_base:
            raise NativeUnavailable()
        if self.fail_receipt and any(p.startswith("provenance/") for p in additions):
            raise NativeUnavailable()
        files = {e.path.decode(): (e.mode, self.objects.blob(e.oid))
                 for e in self.objects.entries(self.objects.commit(self.base)) if e.kind != "tree"}
        files.update({p: ("100644", raw) for p, raw in additions.items()})
        tree_commit = self.fixture.commit(files, self.base)
        self.base = self.fixture.git("commit-tree", self.objects.commit(tree_commit), "-p", self.base,
                                     data=message.encode())
        self.writes += 1
        if self.ambiguous:
            self.ambiguous = False
            raise NativeUnavailable()
        return self.base


# Keep this suite focused without inheriting the admission suite a second time.
for _name in list(vars(admission_tests.TreeAdmission)):
    if _name.startswith("test_"):
        setattr(CoordinatorTests, _name, None)


if __name__ == "__main__":
    unittest.main()
