"""Strict bounded decoding for authenticated GitHub object batches."""
import base64
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


def git_hash(kind, raw):
    return hashlib.sha1(
        kind.encode() + b" " + str(len(raw)).encode() + b"\0" + raw
    ).hexdigest()


class GitHubObjectBatchCodecTests(unittest.TestCase):
    def test_query_is_fixed_to_deployment_repository_and_exact_oid_variables(self):
        """Break caught: callers can smuggle a host, repository, expression, or query."""
        from omarchy_knowledge.github_object_batch import build_request

        first, second = "1" * 40, "2" * 40
        value = json.loads(build_request("production", "tree", [first, second]))

        self.assertEqual(value["variables"], {"o0": first, "o1": second})
        self.assertEqual(
            value["query"],
            'query($o0:GitObjectID!,$o1:GitObjectID!){repository(owner:"cylon58",'
            'name:"omarchy-community-knowledge"){databaseId '
            'o0:object(oid:$o0){__typename oid ...on Tree{entries{nameRaw mode type oid size}}}'
            'o1:object(oid:$o1){__typename oid ...on Tree{entries{nameRaw mode type oid size}}}'
            '}rateLimit{cost remaining}}',
        )
        for invalid in ([], [first] * 2, ["main"], ["1" * 64], [first] * 17):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                build_request("production", "tree", invalid)

    def test_tree_reconstruction_uses_raw_names_git_sorting_and_exact_modes(self):
        """Break caught: decoded names or response order change the authenticated tree."""
        from omarchy_knowledge.github_object_batch import decode_response

        blob = "1" * 40
        subtree = "2" * 40
        executable = "3" * 40
        symlink = "4" * 40
        gitlink = "5" * 40
        rows = [
            (b"z", 33188, "blob", blob, 7),
            (b"a", 16384, "tree", subtree, 0),
            (b"a-raw-\xff", 33261, "blob", executable, 9),
            (b"link", 40960, "blob", symlink, 4),
            (b"module", 57344, "commit", gitlink, 0),
        ]
        ordered = sorted(rows, key=lambda row: row[0] + (b"/" if row[2] == "tree" else b""))
        raw = b"".join(
            format(mode, "o").encode() + b" " + name + b"\0" + bytes.fromhex(oid)
            for name, mode, _kind, oid, _size in ordered
        )
        tree = git_hash("tree", raw)
        response_rows = [
            {"nameRaw": base64.b64encode(name).decode(), "mode": mode,
             "type": kind, "oid": oid, "size": size}
            for name, mode, kind, oid, size in reversed(rows)
        ]
        response = {
            "data": {
                "repository": {"databaseId": 1373429914, "o0": {
                    "__typename": "Tree", "oid": tree, "entries": response_rows,
                }},
                "rateLimit": {"cost": 1, "remaining": 4999},
            }
        }

        decoded, cost, remaining = decode_response("production", "tree", [tree], response)

        self.assertEqual((cost, remaining), (1, 4999))
        self.assertEqual(decoded[tree].raw, raw)
        self.assertEqual(
            [(entry.path, entry.mode, entry.kind, entry.oid, entry.size)
             for entry in decoded[tree].entries],
            [(base64.b64decode(item["nameRaw"]),
              {16384: "040000", 33188: "100644", 33261: "100755",
               40960: "120000", 57344: "160000"}[item["mode"]],
              item["type"], item["oid"],
              item["size"] if item["type"] == "blob" else -1)
             for item in response_rows],
        )

    def test_tree_nonblob_size_requires_live_nonnull_zero_shape(self):
        """Break caught: the decoder rejects GitHub's integer-zero subtree size."""
        from omarchy_knowledge.github_object_batch import decode_response

        child = "2" * 40
        raw = b"40000 nested\0" + bytes.fromhex(child)
        oid = git_hash("tree", raw)
        response = {"data": {
            "repository": {"databaseId": 1373429914, "o0": {
                "__typename": "Tree", "oid": oid, "entries": [{
                    "nameRaw": base64.b64encode(b"nested").decode(),
                    "mode": 16384, "type": "tree", "oid": child, "size": 0,
                }]}},
            "rateLimit": {"cost": 1, "remaining": 200},
        }}

        decoded, _cost, _remaining = decode_response(
            "production", "tree", [oid], response)

        self.assertEqual(decoded[oid].entries[0].size, -1)
        for invalid in (None, False, -1, 1):
            changed = copy.deepcopy(response)
            changed["data"]["repository"]["o0"]["entries"][0]["size"] = invalid
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                decode_response("production", "tree", [oid], changed)

    def test_blob_reconstruction_requires_exact_utf8_bytes_size_and_hash(self):
        """Break caught: a GraphQL hash label authorizes altered or truncated text."""
        from omarchy_knowledge.github_object_batch import decode_response

        raw = "snowman ☃\n".encode()
        oid = git_hash("blob", raw)
        response = {
            "data": {
                "repository": {"databaseId": 1373429914, "o0": {
                    "__typename": "Blob", "oid": oid, "byteSize": len(raw),
                    "isBinary": False, "isTruncated": False, "text": raw.decode(),
                }},
                "rateLimit": {"cost": 2, "remaining": 101},
            }
        }

        decoded, cost, remaining = decode_response("production", "blob", [oid], response)

        self.assertEqual((decoded[oid].raw, decoded[oid].entries), (raw, None))
        self.assertEqual((cost, remaining), (2, 101))

    def test_tree_rejects_invalid_names_modes_types_sizes_hashes_and_partial_data(self):
        """Break caught: a malformed tree batch partly becomes trusted cache state."""
        from omarchy_knowledge.github_object_batch import decode_response

        child = "1" * 40
        raw = b"100644 valid\0" + bytes.fromhex(child)
        oid = git_hash("tree", raw)
        entry = {"nameRaw": base64.b64encode(b"valid").decode(), "mode": 33188,
                 "type": "blob", "oid": child, "size": 3}
        valid = {"data": {"repository": {"databaseId": 1373429914, "o0": {
            "__typename": "Tree", "oid": oid, "entries": [entry]}},
            "rateLimit": {"cost": 1, "remaining": 200}}}
        mutations = []
        for field, value in (("nameRaw", "***"), ("nameRaw", base64.b64encode(b"a/b").decode()),
                             ("mode", 33189), ("type", "tree"), ("size", -1),
                             ("oid", "f" * 40)):
            changed = copy.deepcopy(valid)
            changed["data"]["repository"]["o0"]["entries"][0][field] = value
            mutations.append(changed)
        duplicate = copy.deepcopy(valid)
        duplicate["data"]["repository"]["o0"]["entries"].append(copy.deepcopy(entry))
        mutations.append(duplicate)
        wrong_hash = copy.deepcopy(valid)
        wrong_hash["data"]["repository"]["o0"]["oid"] = "e" * 40
        mutations.append(wrong_hash)
        errors = copy.deepcopy(valid)
        errors["errors"] = [{"message": "partial"}]
        mutations.append(errors)
        missing = copy.deepcopy(valid)
        missing["data"]["repository"]["o0"] = None
        mutations.append(missing)

        for changed in mutations:
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                decode_response("production", "tree", [oid], changed)

    def test_blob_rejects_null_binary_truncated_size_hash_and_rate_metadata(self):
        """Break caught: unusable text or unknown quota is accepted as a Git blob."""
        from omarchy_knowledge.github_object_batch import decode_response

        raw = b"plain"
        oid = git_hash("blob", raw)
        valid = {"data": {"repository": {"databaseId": 1373429914, "o0": {
            "__typename": "Blob", "oid": oid, "byteSize": len(raw),
            "isBinary": False, "isTruncated": False, "text": "plain"}},
            "rateLimit": {"cost": 1, "remaining": 100}}}
        mutations = []
        for field, value in (("text", None), ("isBinary", True),
                             ("isTruncated", True), ("byteSize", 4),
                             ("oid", "e" * 40)):
            changed = copy.deepcopy(valid)
            changed["data"]["repository"]["o0"][field] = value
            mutations.append(changed)
        for field, value in (("cost", None), ("cost", 0), ("remaining", None),
                             ("remaining", -1)):
            changed = copy.deepcopy(valid)
            changed["data"]["rateLimit"][field] = value
            mutations.append(changed)

        for changed in mutations:
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                decode_response("production", "blob", [oid], changed)


class GitHubObjectBatchAdapterTests(unittest.TestCase):
    @staticmethod
    def _empty_tree_response(oid, *, cost=1, remaining=5000):
        return json.dumps({"data": {
            "repository": {"databaseId": 1373429914, "o0": {
                "__typename": "Tree", "oid": oid, "entries": []}},
            "rateLimit": {"cost": cost, "remaining": remaining},
        }}, separators=(",", ":")).encode()

    @staticmethod
    def _connection(response=None, error=None, seen=None):
        seen = [] if seen is None else seen

        class Response:
            status = 200

            def __init__(self, raw):
                self.raw = raw

            def read(self, amount):
                chunk, self.raw = self.raw[:amount], self.raw[amount:]
                return chunk

        class Connection:
            def __init__(self, host, timeout):
                seen.append(("connect", host, timeout))

            def request(self, method, path, body=None, headers=None):
                seen.append((method, path, body, headers))
                if error is not None:
                    raise error
                self.response = Response(response)

            def getresponse(self):
                return self.response

            def close(self):
                pass

        return Connection, seen

    def test_authenticated_batch_uses_fixed_origin_token_and_reports_quota(self):
        """Break caught: batch reads escape the fixed authenticated HTTPS boundary."""
        from omarchy_knowledge.github_native import GitHubRead

        oid = git_hash("tree", b"")
        seen = []
        connection, _ = self._connection(self._empty_tree_response(oid), seen=seen)
        reader = GitHubRead(read_token="scoped-read-token", connection_factory=connection)

        decoded = reader._read_object_batch("tree", [oid])

        self.assertEqual(decoded[oid].raw, b"")
        self.assertEqual((reader.http.calls, reader.http.bytes), (1, len(self._empty_tree_response(oid))))
        self.assertEqual((reader.http.graphql_calls, reader.http.graphql_points,
                          reader.http.graphql_remaining), (1, 1, 5000))
        method, path, body, headers = seen[1]
        self.assertEqual((method, path), ("POST", "/graphql"))
        self.assertEqual(headers["Authorization"], "Bearer scoped-read-token")
        self.assertNotIn(b"scoped-read-token", body)
        self.assertEqual(json.loads(body)["variables"], {"o0": oid})

    def test_anonymous_and_bundle_only_paths_never_batch(self):
        """Break caught: optional acceleration creates anonymous or offline network I/O."""
        from omarchy_knowledge.github_native import GitHubRead

        oid = git_hash("tree", b"")
        connection, seen = self._connection(self._empty_tree_response(oid))
        reader = GitHubRead(connection_factory=connection)
        self.assertIsNone(reader._read_object_batch("tree", [oid]))
        reader.objects.bundle_only = True
        self.assertIsNone(reader._read_object_batch("tree", [oid]))
        reader.objects.warm(["1" * 40])
        self.assertEqual((seen, reader.http.calls, reader.http.graphql_calls), ([], 0, 0))

    def test_failed_transport_charges_unknown_bytes_once_and_disables_batches(self):
        """Break caught: a failed response is free or recursively retried in smaller batches."""
        from omarchy_knowledge.github_native import GitHubRead, MAX_RESPONSE

        oid = git_hash("tree", b"")
        connection, seen = self._connection(error=OSError("secret transport detail"))
        reader = GitHubRead(read_token="token", connection_factory=connection)

        self.assertIsNone(reader._read_object_batch("tree", [oid]))
        self.assertIsNone(reader._read_object_batch("tree", [oid]))

        self.assertEqual((reader.http.calls, reader.http.graphql_calls), (1, 1))
        self.assertEqual(reader.http.bytes, MAX_RESPONSE + 1)
        self.assertEqual(len([row for row in seen if row[0] == "POST"]), 1)

    def test_limits_deadlines_and_interrupts_are_not_swallowed_as_fallback(self):
        """Break caught: acceleration resets resource limits or hides process stops."""
        from omarchy_knowledge.github_native import (GitHubRead, MAX_BYTES,
                                                     MAX_RESPONSE, NativeUnavailable)

        oid = git_hash("tree", b"")
        failed, _ = self._connection(error=OSError("failure"))
        reader = GitHubRead(read_token="token", connection_factory=failed)
        reader.http.bytes = MAX_BYTES - MAX_RESPONSE
        with self.assertRaises(NativeUnavailable):
            reader._read_object_batch("tree", [oid])

        expired = GitHubRead(read_token="token", connection_factory=failed)
        expired.http.deadline = 0
        with self.assertRaises(NativeUnavailable):
            expired._read_object_batch("tree", [oid])

        interrupted, _ = self._connection(error=KeyboardInterrupt())
        stopping = GitHubRead(read_token="token", connection_factory=interrupted)
        with self.assertRaises(KeyboardInterrupt):
            stopping._read_object_batch("tree", [oid])

    def test_malformed_quota_low_reserve_and_point_exhaustion_disable_acceleration(self):
        """Break caught: unknown or depleted provider quota keeps issuing read batches."""
        from omarchy_knowledge.github_native import GitHubRead

        oid = git_hash("tree", b"")
        cases = [
            self._empty_tree_response(oid, remaining=99),
            self._empty_tree_response(oid, cost=2, remaining=5000),
            b'{"data":{"repository":null,"rateLimit":null}}',
        ]
        for index, response in enumerate(cases):
            with self.subTest(index=index):
                connection, _ = self._connection(response)
                reader = GitHubRead(read_token="token", connection_factory=connection)
                if index == 1:
                    reader.http.graphql_points = 191
                if index == 1:
                    with self.assertRaises(Exception) as caught:
                        reader._read_object_batch("tree", [oid])
                    from omarchy_knowledge.github_native import NativeUnavailable
                    self.assertIs(type(caught.exception), NativeUnavailable)
                else:
                    self.assertIsNone(reader._read_object_batch("tree", [oid]))
                self.assertIsNone(reader._read_object_batch("tree", [oid]))
                self.assertEqual(reader.http.graphql_calls, 1)

    def test_batch_cache_installation_is_atomic_untouched_and_not_double_charged(self):
        """Break caught: one invalid/capacity object partly mutates proof cache accounting."""
        from omarchy_knowledge.github_native import APIObjects, NativeUnavailable
        from omarchy_knowledge.github_object_batch import DecodedObject

        first_raw, second_raw = b"first", b"second"
        first = git_hash("blob", first_raw)
        second = git_hash("blob", second_raw)
        objects = APIObjects(object())
        decoded = {
            first: DecodedObject(first_raw, None),
            second: DecodedObject(second_raw, None),
        }
        objects._install_batch("blob", decoded)
        self.assertEqual(objects.cache_bytes, len(first_raw) + len(second_raw))
        self.assertEqual(objects.touched, set())
        objects._install_batch("blob", decoded)
        self.assertEqual(objects.cache_bytes, len(first_raw) + len(second_raw))

        third_raw = b"third"
        third = git_hash("blob", third_raw)
        before = (dict(objects.cache), objects.cache_bytes, set(objects.touched))
        with patch("omarchy_knowledge.github_native.MAX_CACHE_BYTES", objects.cache_bytes + 1):
            with self.assertRaises(NativeUnavailable):
                objects._install_batch("blob", {third: DecodedObject(third_raw, None)})
        self.assertEqual((objects.cache, objects.cache_bytes, objects.touched), before)

    def test_blob_batches_bound_count_and_worst_case_json_escaping(self):
        """Break caught: valid blob text can expand a batch beyond the 1 MiB response cap."""
        from omarchy_knowledge.github_native import APIObjects, MAX_RESPONSE

        small = [(f"{index:040x}", 1) for index in range(40)]
        large = [(f"{index + 100:040x}", 65536) for index in range(5)]
        small_batches = APIObjects._blob_batches(small)
        large_batches = APIObjects._blob_batches(large)
        batches = [*small_batches, *large_batches]

        self.assertEqual([len(batch) for batch in small_batches], [32, 8])
        self.assertTrue(all(len(batch) <= 32 for batch in batches))
        self.assertTrue(all(
            8192 + sum(6 * size + 512 for _oid, size in batch) < MAX_RESPONSE
            for batch in batches
        ))

    def test_tree_prefetch_applies_entry_cap_per_independent_root(self):
        """Break caught: independently bounded current or historical roots share one cap."""
        from omarchy_knowledge.git_objects import TreeEntry
        from omarchy_knowledge.github_native import APIObjects, NativeUnavailable

        first, second = "1" * 40, "2" * 40
        objects = APIObjects(object())
        for root, prefix in ((first, b"a"), (second, b"b")):
            objects.cache[("tree", root)] = b""
            objects.cache[("entries", root)] = [
                TreeEntry(prefix + f"{index:04d}".encode(), "100644", "blob",
                          f"{index + 100:040x}", 1)
                for index in range(3000)
            ]

        self.assertEqual(len(objects._prefetch_trees([first, second])), 6000)
        self.assertEqual(objects._prefetch_trees([first, second], collect=False), [])
        extra = ["3" * 40, "4" * 40]
        for root in extra:
            objects.cache[("tree", root)] = b""
            objects.cache[("entries", root)] = []
        with self.assertRaises(NativeUnavailable):
            objects._prefetch_trees([first, second, *extra])

    def test_tree_frontier_deduplicates_shared_subtree_without_collapsing_paths(self):
        """Break caught: two paths to one missing subtree abort the whole warm batch."""
        from omarchy_knowledge.git_objects import TreeEntry
        from omarchy_knowledge.github_native import APIObjects, NativeUnavailable
        from omarchy_knowledge.github_object_batch import DecodedObject

        blob_raw = b"leaf"
        blob = git_hash("blob", blob_raw)
        shared_raw = b"100644 leaf\0" + bytes.fromhex(blob)
        shared = git_hash("tree", shared_raw)
        root_raw = (b"40000 a\0" + bytes.fromhex(shared)
                    + b"40000 b\0" + bytes.fromhex(shared))
        root = git_hash("tree", root_raw)
        decoded = {
            root: DecodedObject(root_raw, (
                TreeEntry(b"a", "040000", "tree", shared, -1),
                TreeEntry(b"b", "040000", "tree", shared, -1),
            )),
            shared: DecodedObject(shared_raw, (
                TreeEntry(b"leaf", "100644", "blob", blob, len(blob_raw)),
            )),
        }

        class API:
            def __init__(self):
                self.calls = []

            def _read_object_batch(self, kind, oids):
                if len(oids) != len(set(oids)):
                    raise NativeUnavailable()
                self.calls.append((kind, list(oids)))
                return {oid: decoded[oid] for oid in oids}

        api = API()
        objects = APIObjects(api)

        objects._prefetch_trees([root])

        self.assertEqual(api.calls, [("tree", [root]), ("tree", [shared])])
        self.assertEqual(
            [(entry.path, entry.kind) for entry in objects.entries(root)],
            [(b"a", "tree"), (b"a/leaf", "blob"),
             (b"b", "tree"), (b"b/leaf", "blob")],
        )

    def test_partial_batch_falls_back_to_rest_once_without_partial_cache(self):
        """Break caught: partial GraphQL data is installed or repeatedly retried."""
        from experiments.growth.gates import _NativeFixture, SYNTHETIC_TOKEN
        from omarchy_knowledge.github_native import GitHubRead

        record_path = "records/cases/00000000-0000-4000-8000-000000000002.json"
        with tempfile.TemporaryDirectory(prefix="batch-fallback-test-") as temporary:
            with _NativeFixture(Path(temporary)) as fixture:
                fixture.main = fixture.repository.commit(
                    {record_path: b"{}"}, (fixture.main,), "Synthetic partial batch")
                original = fixture._read_query

                def partial(body):
                    value, metadata = original(body)
                    value["errors"] = [{"type": "SYNTHETIC_PARTIAL"}]
                    return value, metadata

                fixture._read_query = partial
                reader = GitHubRead(read_token=SYNTHETIC_TOKEN,
                                    connection_factory=fixture.connection_factory)
                reader.objects.warm([fixture.main])
                self.assertFalse(any(key[0] == "tree" for key in reader.objects.cache))

                tree = reader.objects.commit(fixture.main)
                entries = reader.objects.entries(tree)
                selected = next(entry for entry in entries
                                if entry.path.decode() == record_path)
                self.assertEqual(reader.objects.blob(selected.oid), b"{}")

                graphql = [row for row in fixture.requests if row["path"] == "/graphql"]
                rest_trees = [row for row in fixture.requests if "/git/trees/" in row["path"]]
                rest_blobs = [row for row in fixture.requests if "/git/blobs/" in row["path"]]
                self.assertEqual(len(graphql), 1)
                self.assertTrue(rest_trees)
                self.assertEqual(len(rest_blobs), 1)

    def test_native_fixture_warm_batches_without_visits_or_unrelated_blobs(self):
        """Break caught: speculative warming changes validation visits/touches or fetches README."""
        from experiments.growth.gates import _NativeFixture, SYNTHETIC_TOKEN
        from omarchy_knowledge.github_native import GitHubRead

        record_path = "records/cases/00000000-0000-4000-8000-000000000001.json"
        with tempfile.TemporaryDirectory(prefix="batch-warm-test-") as temporary:
            with _NativeFixture(Path(temporary)) as fixture:
                fixture.main = fixture.repository.commit(
                    {record_path: b"{}", "README.md": b"unrelated"},
                    (fixture.main,), "Synthetic batch warm")
                leaves = fixture.repository.leaves(fixture.main)
                record_oid = leaves[record_path][1]
                unrelated_oid = leaves["README.md"][1]
                reader = GitHubRead(read_token=SYNTHETIC_TOKEN,
                                    connection_factory=fixture.connection_factory)

                reader.objects.warm([fixture.main])

                self.assertEqual((reader.objects.visits, reader.objects.touched), (0, set()))
                self.assertIn(("blob", record_oid), reader.objects.cache)
                self.assertNotIn(("blob", unrelated_oid), reader.objects.cache)
                graph = [row for row in fixture.requests
                         if row["method"] == "POST" and row["path"] == "/graphql"]
                self.assertTrue(graph)
                self.assertTrue(all(row["object_count"] <= 8
                                    if row["object_kind"] == "tree"
                                    else row["object_count"] <= 32 for row in graph))

                tree = reader.objects.commit(fixture.main)
                entries = reader.objects.entries(tree)
                selected = next(entry for entry in entries if entry.path.decode() == record_path)
                self.assertEqual(reader.objects.blob(selected.oid), b"{}")
                self.assertGreater(reader.objects.visits, 0)
                self.assertIn(("blob", record_oid), reader.objects.touched)

    def test_native_fixture_emits_real_nonblob_zero_size_shape(self):
        """Break caught: the independent fixture models non-blob size as null."""
        from experiments.growth.gates import _NativeFixture
        from omarchy_knowledge.github_object_batch import build_request

        with tempfile.TemporaryDirectory(prefix="batch-shape-test-") as temporary:
            with _NativeFixture(Path(temporary)) as fixture:
                fixture.main = fixture.repository.commit(
                    {"records/cases/shape.json": b"{}"},
                    (fixture.main,), "Synthetic nested tree shape")
                root = fixture._commit(fixture.main)["tree"]["sha"]
                response, metadata = fixture._read_query(
                    build_request("production", "tree", [root]))
                rows = response["data"]["repository"]["o0"]["entries"]
                nonblobs = [row for row in rows if row["type"] != "blob"]

                self.assertEqual(metadata, {"object_kind": "tree", "object_count": 1})
                self.assertTrue(nonblobs)
                self.assertTrue(all(type(row["size"]) is int and row["size"] == 0
                                    for row in nonblobs))

    def test_one_import_reports_batched_read_counts_and_points(self):
        """Break caught: native jobs silently use REST-only reads or omit quota evidence."""
        from experiments.growth.gates import run_gate

        result = run_gate("one-import")

        self.assertEqual(result["status"], "success", result)
        jobs = [job for item in result["imports"] for job in item["jobs"].values()]
        self.assertTrue(any(job["graphql_calls"] > 0 for job in jobs))
        self.assertTrue(all(0 <= job["graphql_calls"] <= 192 for job in jobs))
        self.assertTrue(all(0 <= job["graphql_points"] <= 192 for job in jobs))


if __name__ == "__main__":
    unittest.main()
