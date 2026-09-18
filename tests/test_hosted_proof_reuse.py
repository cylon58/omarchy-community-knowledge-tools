"""Hosted reuse of a prior proof as an inert, hash-verified object cache."""
import base64
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


def git_hash(kind, raw):
    return hashlib.sha1(kind.encode() + b" " + str(len(raw)).encode() + b"\0" + raw).hexdigest()


class HostedProofReuse(unittest.TestCase):
    @staticmethod
    def tree(entries):
        raw = b"".join(mode.encode().lstrip(b"0") + b" " + name + b"\0" + bytes.fromhex(oid)
                       for mode, name, oid in sorted(entries, key=lambda item: item[1]))
        return git_hash("tree", raw), raw

    @staticmethod
    def commit(tree, message=b"fixture\n"):
        raw = (b"tree " + tree.encode()
               + b"\nauthor Fixture <fixture@example.invalid> 0 +0000"
               + b"\ncommitter Fixture <fixture@example.invalid> 0 +0000\n\n" + message)
        return git_hash("commit", raw), raw

    def adapter(self, bundle, *, pages_status=200):
        """A strict HTTP fixture for the production GitHubRead adapter."""
        blob_raw = b"current"
        blob = git_hash("blob", blob_raw)
        tree, tree_raw = self.tree([("100644", b"record", blob)])
        head, commit_raw = self.commit(tree)
        seen = []

        repository = {"id": 1373429914, "full_name": "cylon58/omarchy-community-knowledge",
                      "default_branch": "main"}
        branch = {"ref": "refs/heads/main", "object": {"type": "commit", "sha": head}}
        commit = {"sha": head, "tree": {"sha": tree}, "parents": [],
                  "author": {"name": "Fixture", "email": "fixture@example.invalid",
                             "date": "1970-01-01T00:00:00Z"},
                  "committer": {"name": "Fixture", "email": "fixture@example.invalid",
                                "date": "1970-01-01T00:00:00Z"},
                  "message": "fixture", "verification": {"payload": None, "signature": None}}
        tree_value = {"sha": tree, "truncated": False, "tree": [
            {"path": "record", "mode": "100644", "type": "blob", "sha": blob,
             "size": len(blob_raw)}]}
        blob_value = {"sha": blob, "encoding": "base64", "size": len(blob_raw),
                      "content": base64.b64encode(blob_raw).decode()}

        class Response:
            def __init__(self, status, raw):
                self.status, self.raw = status, raw

            def read(self, amount):
                chunk, self.raw = self.raw[:amount], self.raw[amount:]
                return chunk

        class Connection:
            def __init__(self, host, timeout):
                self.host = host
                seen.append(("connect", host, timeout))

            def request(self, method, path, body=None, headers=None):
                seen.append((self.host, method, path, body, headers))
                if self.host == "cylon58.github.io":
                    self.response = Response(pages_status, bundle)
                    return
                values = {
                    "/repos/cylon58/omarchy-community-knowledge": repository,
                    "/repos/cylon58/omarchy-community-knowledge/git/ref/heads/main": branch,
                    f"/repos/cylon58/omarchy-community-knowledge/git/commits/{head}": commit,
                    f"/repos/cylon58/omarchy-community-knowledge/git/trees/{tree}": tree_value,
                    f"/repos/cylon58/omarchy-community-knowledge/git/blobs/{blob}": blob_value,
                }
                self.response = Response(200, json.dumps(values[path], separators=(",", ":")).encode())

            def getresponse(self):
                return self.response

            def close(self):
                pass

        return Connection, seen, head, tree, blob, blob_raw

    def test_seed_decoder_exposes_old_head_without_weakening_strict_decoder(self):
        """Break caught: an old transport head becomes current-head authority."""
        from omarchy_knowledge.object_bundle import BundleUnavailable, decode, decode_seed, encode

        raw = (b"tree " + b"0" * 40
               + b"\nauthor Fixture <fixture@example.invalid> 0 +0000"
               + b"\ncommitter Fixture <fixture@example.invalid> 0 +0000\n\nold\n")
        old_head = git_hash("commit", raw)
        bundle = encode(old_head, {("commit", old_head): raw})

        embedded_head, objects = decode_seed(bundle)

        self.assertEqual(embedded_head, old_head)
        self.assertEqual(objects, {("commit", old_head): raw})
        with self.assertRaises(BundleUnavailable):
            decode(bundle, "f" * 40)

    def test_incomplete_seed_tree_is_omitted_and_api_restores_blob_sizes(self):
        """Break caught: a partial seed memoizes -1 for a direct blob size."""
        from omarchy_knowledge.github_native import APIObjects
        from omarchy_knowledge.object_bundle import encode

        present, present_raw = git_hash("blob", b"present"), b"present"
        missing, missing_raw = git_hash("blob", b"missing"), b"missing"
        tree, tree_raw = self.tree([
            ("100644", b"present", present),
            ("100644", b"missing", missing),
        ])
        head, commit_raw = self.commit(tree)
        seed = encode(head, {
            ("commit", head): commit_raw,
            ("tree", tree): tree_raw,
            ("blob", present): present_raw,
        })

        class API:
            tree_calls = 0

            def git_commit(self, _oid):
                raise AssertionError("seeded commit unexpectedly reached the object API")

            def git_tree(self, oid):
                self.tree_calls += 1
                self.assert_oid = oid
                return {"sha": tree, "truncated": False, "tree": [
                    {"path": "present", "mode": "100644", "type": "blob",
                     "sha": present, "size": len(present_raw)},
                    {"path": "missing", "mode": "100644", "type": "blob",
                     "sha": missing, "size": len(missing_raw)},
                ]}

            def git_blob(self, _oid):
                raise AssertionError("tree-size recovery does not fetch blobs")

        api = API()
        objects = APIObjects(api)
        self.assertEqual(objects.load_seed(seed), head)
        self.assertNotIn(("tree", tree), objects.cache)

        entries = objects.entries(objects.commit(head))

        self.assertEqual(api.tree_calls, 1)
        self.assertEqual({entry.path: entry.size for entry in entries},
                         {b"missing": len(missing_raw), b"present": len(present_raw)})

    def test_export_prunes_untouched_seed_objects_and_replays_offline(self):
        """Break caught: stale seed objects accumulate or hide an incomplete proof."""
        from omarchy_knowledge.github_native import APIObjects
        from omarchy_knowledge.object_bundle import decode, encode

        blob, blob_raw = git_hash("blob", b"current"), b"current"
        stale, stale_raw = git_hash("blob", b"unrelated-old-object"), b"unrelated-old-object"
        tree, tree_raw = self.tree([("100644", b"record", blob)])
        head, commit_raw = self.commit(tree)
        seed = encode(head, {
            ("commit", head): commit_raw,
            ("tree", tree): tree_raw,
            ("blob", blob): blob_raw,
            ("blob", stale): stale_raw,
        })

        class Offline:
            def git_commit(self, _oid):
                raise AssertionError("offline proof replay reached commit API")
            git_tree = git_commit
            git_blob = git_commit

        current = APIObjects(Offline())
        current.load_seed(seed)
        entries = current.entries(current.commit(head))
        self.assertEqual(current.blob(entries[0].oid), blob_raw)
        proof = current.export_bundle(head)

        exported = decode(proof, head)
        self.assertEqual(set(exported), {("commit", head), ("tree", tree), ("blob", blob)})
        self.assertNotIn(("blob", stale), exported)
        replay = APIObjects(Offline())
        replay.load_bundle(proof, head)
        replayed = replay.entries(replay.commit(head))
        self.assertEqual(replay.blob(replayed[0].oid), blob_raw)

    def test_real_adapter_uses_same_head_seed_after_separate_identity_reads(self):
        """Break caught: hosted reuse bypasses the production HTTP/object adapter."""
        from omarchy_knowledge.github_native import APIObjects, GitHubRead

        blob, blob_raw = git_hash("blob", b"current"), b"current"
        tree, tree_raw = self.tree([("100644", b"record", blob)])
        head, commit_raw = self.commit(tree)
        source = APIObjects(object())
        for kind, oid, raw in (("commit", head, commit_raw), ("tree", tree, tree_raw),
                               ("blob", blob, blob_raw)):
            source._save(kind, oid, raw)
        bundle = source.export_bundle(head)
        Connection, seen, expected, _, _, _ = self.adapter(bundle)
        self.assertEqual(expected, head)

        reader = GitHubRead(read_token="must-not-leak", connection_factory=Connection)
        self.assertTrue(reader.seed_canonical())
        self.assertEqual((reader.http.calls, reader.http.bytes), (1, len(bundle)))
        self.assertEqual(reader.repository()["id"], 1373429914)
        self.assertEqual(reader.branch(), head)
        entries = reader.objects.entries(reader.objects.commit(head))
        self.assertEqual(reader.objects.blob(entries[0].oid), blob_raw)

        object_requests = [row for row in seen if len(row) == 5 and "/git/" in row[2]]
        self.assertEqual(object_requests, [next(row for row in object_requests
                                                if row[2].endswith("/git/ref/heads/main"))])
        pages_request = next(row for row in seen if len(row) == 5 and row[0] == "cylon58.github.io")
        self.assertNotIn("Authorization", pages_request[4])

    def test_failed_seed_shares_request_and_byte_budget_with_cold_fallback(self):
        """Break caught: Pages failure creates a fresh API budget or trust mode."""
        from omarchy_knowledge.github_native import GitHubRead, NativeUnavailable
        from omarchy_knowledge.object_bundle import MAX_COMPRESSED_BUNDLE

        Connection, seen, head, _, _, blob_raw = self.adapter(b"missing", pages_status=404)
        reader = GitHubRead(connection_factory=Connection)

        self.assertFalse(reader.seed_canonical())
        self.assertEqual((reader.http.calls, reader.http.bytes), (1, MAX_COMPRESSED_BUNDLE + 1))
        self.assertEqual(reader.branch(), head)
        entries = reader.objects.entries(reader.objects.commit(head))
        self.assertEqual(reader.objects.blob(entries[0].oid), blob_raw)
        self.assertEqual(reader.http.calls, 5)

        exhausted = GitHubRead(connection_factory=Connection)
        exhausted.http.max_calls = 1
        self.assertFalse(exhausted.seed_canonical())
        self.assertEqual(
            (exhausted.http.calls, exhausted.http.bytes),
            (1, MAX_COMPRESSED_BUNDLE + 1),
        )
        seed_requests = list(seen)
        with self.assertRaises(NativeUnavailable):
            exhausted.branch()
        self.assertEqual(seen, seed_requests)
        self.assertEqual(exhausted.http.bytes, MAX_COMPRESSED_BUNDLE + 1)

    def test_invalid_or_over_capacity_seed_is_discarded_atomically(self):
        """Break caught: a partially installed seed consumes cache capacity."""
        from omarchy_knowledge.github_native import APIObjects, NativeUnavailable
        from omarchy_knowledge.object_bundle import encode

        first, first_raw = git_hash("blob", b"one"), b"one"
        tree, tree_raw = self.tree([("100644", b"one", first)])
        head, commit_raw = self.commit(tree)
        bundle = encode(head, {("commit", head): commit_raw, ("tree", tree): tree_raw,
                               ("blob", first): first_raw})
        target = APIObjects(object())
        with patch("omarchy_knowledge.object_bundle.MAX_SEED_OBJECTS", 2):
            with self.assertRaises(NativeUnavailable):
                target.load_seed(bundle)
        self.assertEqual((target.cache, target.cache_bytes, target.seeded), ({}, 0, set()))

        with patch("omarchy_knowledge.object_bundle.MAX_SEED_RAW_OBJECTS",
                   sum(len(raw) for raw in (commit_raw, tree_raw, first_raw)) - 1):
            with self.assertRaises(NativeUnavailable):
                target.load_seed(bundle)
        self.assertEqual((target.cache, target.cache_bytes, target.seeded), ({}, 0, set()))

        with self.assertRaises(NativeUnavailable):
            target.load_seed(bundle[:-1])
        self.assertEqual((target.cache, target.cache_bytes, target.seeded), ({}, 0, set()))

        malformed_tree_raw = b"not-a-git-tree"
        malformed_tree = git_hash("tree", malformed_tree_raw)
        malformed_head, malformed_commit = self.commit(malformed_tree)
        malformed = encode(malformed_head, {("commit", malformed_head): malformed_commit,
                                             ("tree", malformed_tree): malformed_tree_raw})
        with self.assertRaises(NativeUnavailable):
            target.load_seed(malformed)
        self.assertEqual((target.cache, target.cache_bytes, target.seeded), ({}, 0, set()))

        expired = APIObjects(object(), total_deadline=0)
        with self.assertRaises(NativeUnavailable):
            expired.load_seed(bundle)
        self.assertEqual((expired.cache, expired.cache_bytes, expired.seeded), ({}, 0, set()))

    def test_one_import_newer_seed_preserves_canonical_result_and_replays_pruned_proof(self):
        """Break caught: reuse changes evidence/attribution or publishes stale objects."""
        from experiments.growth.baseline import FIXED_NOW, _FixtureRepository, _SyntheticAPI, _cohort
        from omarchy_knowledge.canonical import read_canonical
        from omarchy_knowledge.coordinator import Policy, prepare, publish
        from omarchy_knowledge.object_bundle import decode, decode_seed, encode
        from omarchy_knowledge.service import _validated_proof

        with tempfile.TemporaryDirectory(prefix="omarchy-seed-test-") as temporary:
            repository = _FixtureRepository(Path(temporary))
            api = _SyntheticAPI(repository)
            policy = Policy("a" * 40, "b" * 40)
            tree_from_api = api.git_tree
            def permuted_tree(oid):
                value = tree_from_api(oid)
                value["tree"].reverse()
                return value
            api.git_tree = permuted_tree

            api.candidate(1, _cohort(1, 1))
            self.assertEqual(publish(api, policy, prepare(api, policy, 1)).status, "accepted")
            api.reset_objects()
            read_canonical(api, policy, now=FIXED_NOW)
            old_proof = api.objects.export_bundle(api.base)
            old_head, old_objects = decode_seed(old_proof)
            stale, stale_raw = git_hash("blob", b"old-unrelated"), b"old-unrelated"
            seeded_proof = encode(old_head, {**old_objects, ("blob", stale): stale_raw})

            api.candidate(2, _cohort(2, 1))
            self.assertEqual(publish(api, policy, prepare(api, policy, 2)).status, "accepted")
            api.reset_objects()
            api.objects.load_seed(seeded_proof)
            seeded_start = sum(api.raw_calls.values())
            seeded = read_canonical(api, policy, now=FIXED_NOW)
            seeded_calls = sum(api.raw_calls.values()) - seeded_start
            before_replay = dict(api.raw_calls)
            proof = _validated_proof(api, policy, seeded)
            self.assertEqual(dict(api.raw_calls), before_replay)

            api.reset_objects()
            cold_start = sum(api.raw_calls.values())
            cold = read_canonical(api, policy, now=FIXED_NOW)
            cold_calls = sum(api.raw_calls.values()) - cold_start

        self.assertEqual(seeded, cold)
        self.assertLess(seeded_calls, cold_calls)
        self.assertEqual(seeded["source"], cold["source"])
        self.assertEqual(seeded["receipts"], cold["receipts"])
        self.assertTrue(any(record["type"] == "report" and record["payload"]["result"] == "failure"
                            for record in seeded["records"]))
        self.assertNotIn(("blob", stale), decode(proof, seeded["source"]["data_revision"]))


if __name__ == "__main__":
    unittest.main()
