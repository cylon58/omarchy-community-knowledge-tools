"""Bounded static Git-object transport for anonymous canonical sync."""
import hashlib
import gzip
import base64
from datetime import datetime, timezone
import json
import re
import struct
import unittest
from unittest.mock import patch

from tests import test_admission
from tests.test_coordinator import FakeAPI


class ObjectBundle(unittest.TestCase):
    def setUp(self):
        from unittest.mock import patch
        test_admission.TreeAdmission.setUp(self)
        for target in ('omarchy_knowledge.github_public.GitHubPublicRead.repository',
                       'omarchy_knowledge.catalog.CatalogPublicRead.catalog'):
            stub = patch(target, side_effect=OSError('offline fixture'))
            stub.start()
            self.addCleanup(stub.stop)
    git = test_admission.TreeAdmission.git
    commit = test_admission.TreeAdmission.commit
    def _published(self):
        from omarchy_knowledge.coordinator import Policy, prepare, publish
        api = FakeAPI(self)
        policy = Policy("a" * 40, "b" * 40)
        self.assertEqual(publish(api, policy, prepare(api, policy, 1)).status, "accepted")
        # FakeAPI deliberately reports a fixed date. A real APIObjects reader
        # derives it from the raw commit, so normalize this fixture's receipt to
        # exercise the same raw-object path used in production.
        files = {entry.path.decode(): (entry.mode, api.objects.blob(entry.oid))
                 for entry in api.objects.entries(api.objects.commit(api.base)) if entry.kind != "tree"}
        receipt_path = next(path for path in files if path.startswith("provenance/ingestion/"))
        receipt = json.loads(files[receipt_path][1])
        accepted = receipt["source"]["accepted_commit_oid"]["hex"]
        raw = self.git("cat-file", "commit", accepted)
        committer = next(line for line in raw.splitlines() if line.startswith("committer "))
        from datetime import datetime, timezone
        receipt["accepted_at"] = datetime.fromtimestamp(int(committer.rsplit(" ", 2)[1]), timezone.utc).isoformat().replace("+00:00", "Z")
        files[receipt_path] = ("100644", json.dumps(receipt, sort_keys=True, separators=(",", ":")).encode() + b"\n")
        api.base = self.commit(files, api.base)
        return api, policy

    def _cached_objects(self, api):
        from omarchy_knowledge.github_native import APIObjects
        objects = APIObjects(object())
        for row in self.git("rev-list", "--objects", api.base).splitlines():
            oid = row.split(" ", 1)[0]
            kind = self.git("cat-file", "-t", oid).strip()
            if kind in {"commit", "tree", "blob"}:
                objects._save(kind, oid, api.objects._typed(oid, kind, 1024 * 1024))
        return objects

    def _native_api(self):
        """GitHub-shaped transport over raw fixture objects, including real dates."""
        from omarchy_knowledge.github_native import APIObjects
        fixture = self
        class NativeAPI(FakeAPI):
            def __init__(self):
                super().__init__(fixture)
                self.objects = APIObjects(self)
                self.object_calls = 0
            def git_commit(self, oid):
                self.object_calls += 1
                raw = fixture.reader._typed(oid, "commit", 65536)
                headers, _, message = raw.partition(b"\n\n")
                lines = headers.splitlines()
                def identity(label):
                    line = next(row for row in lines if row.startswith(label + b" "))
                    match = re.fullmatch(label + rb" (.*) <([^<>]*)> ([0-9]+) [+-][0-9]{4}", line)
                    if match is None:
                        raise AssertionError("unsupported test identity")
                    return {"name": match.group(1).decode(), "email": match.group(2).decode(),
                            "date": datetime.fromtimestamp(int(match.group(3)), timezone.utc).isoformat().replace("+00:00", "Z")}
                return {"sha": oid, "tree": {"sha": lines[0][5:].decode()},
                        "parents": [{"sha": row[7:].decode()} for row in lines if row.startswith(b"parent ")],
                        "author": identity(b"author"), "committer": identity(b"committer"),
                        "message": message.decode().rstrip("\n"),
                        "verification": {"payload": None, "signature": None}}
            def git_tree(self, oid):
                self.object_calls += 1
                entries = [entry for entry in fixture.reader.entries(oid) if b"/" not in entry.path]
                return {"sha": oid, "truncated": False, "tree": [
                    {"path": entry.path.decode(), "mode": entry.mode, "type": entry.kind,
                     "sha": entry.oid, **({"size": entry.size} if entry.kind == "blob" else {})}
                    for entry in entries]}
            def git_blob(self, oid):
                self.object_calls += 1
                raw = fixture.reader.blob(oid)
                return {"sha": oid, "encoding": "base64", "size": len(raw),
                        "content": base64.b64encode(raw).decode()}
            def commit_info(self, oid):
                return self.objects.info(oid)
            def prefill_canonical(self, revision):
                raise AssertionError("hosted builder must not consume its Pages bundle")
        return NativeAPI()

    def test_anonymous_sync_uses_two_api_reads_and_bundle_capacity_does_not_add_api_reads(self):
        """Break caught: sync falls back to REST after loading a growing proof."""
        from omarchy_knowledge.canonical import sync
        from omarchy_knowledge.github_native import APIObjects

        from omarchy_knowledge.coordinator import Policy, prepare, publish
        source = self._native_api(); policy = Policy("a" * 40, "b" * 40)
        self.assertEqual(publish(source, policy, prepare(source, policy, 1)).status, "accepted")
        # This is the hosted builder path: authenticate/validate via APIObjects,
        # then export exactly the cache that read_canonical populated.
        from omarchy_knowledge.github_native import APIObjects
        source.objects = APIObjects(source)
        from omarchy_knowledge.canonical import read_canonical
        read_canonical(source, policy)
        source_objects = source.objects

        # Add 65 independently hash-checked historical commit/tree pairs to the
        # same transport. They model growth in receipt-referenced imports without
        # making this test's setup repeatedly run the admission coordinator.
        parent = source.base
        for number in range(65):
            tree_raw = b""
            tree = hashlib.sha1(b"tree 0\0").hexdigest()
            source_objects._save("tree", tree, tree_raw)
            raw = (f"tree {tree}\nparent {parent}\nauthor Fixture <fixture@example.org> {number} +0000\n"
                   f"committer Fixture <fixture@example.org> {number} +0000\n\nImport {number}\n").encode()
            commit = hashlib.sha1(b"commit " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
            source_objects._save("commit", commit, raw)
            parent = commit
        bundle = source_objects.export_bundle(source.base)

        class Client:
            def __init__(self):
                self.calls = 0
                self.object_calls = 0
                self.objects = APIObjects(self)
            def repository(self):
                self.calls += 1
                return source.repository()
            def branch(self):
                self.calls += 1
                return source.base
            def prefill_canonical(self, revision):
                self.objects.load_bundle(bundle, revision)
            def git_commit(self, oid):
                self.object_calls += 1
                raise AssertionError("bundle-only sync used the REST object API")
            git_tree = git_commit
            git_blob = git_commit
            def commit_info(self, oid):
                return self.objects.info(oid)

        client = Client()
        result = sync(client, policy, self.root / "cache")
        self.assertEqual(result["record_count"], 1)
        self.assertEqual(client.calls, 2)
        self.assertEqual(client.object_calls, 0)
        # The proof really carries the additional history, not merely the head.
        self.assertEqual(client.objects.info(parent)["message"], "Import 64")
        current = (self.root / "cache/CURRENT").read_bytes()
        stale = Client()
        stale.branch = lambda: "f" * 40
        from omarchy_knowledge.github_native import NativeUnavailable
        with self.assertRaises(NativeUnavailable):
            sync(stale, policy, self.root / "cache")
        self.assertEqual((self.root / "cache/CURRENT").read_bytes(), current)
        from omarchy_knowledge.snapshots import load_cache
        self.assertEqual(len(load_cache(self.root / "cache").records), 1)

    def test_bundle_rejects_stale_tampered_and_incomplete_proofs_without_fallback(self):
        """Break caught: unanchored or incomplete static data silently reaches REST."""
        from omarchy_knowledge.github_native import APIObjects, NativeUnavailable

        source, policy = self._published()
        source_objects = self._cached_objects(source)
        bundle = source_objects.export_bundle(source.base)

        class Offline:
            calls = 0
            def git_commit(self, oid):
                self.calls += 1
                raise AssertionError("missing bundle object fell back to REST")
            git_tree = git_commit
            git_blob = git_commit

        for candidate, expected in ((bundle, "f" * 40), (bundle[:-1], source.base),
                                    (b"not-a-gzip-stream", source.base)):
            target = APIObjects(Offline())
            with self.subTest(expected=expected), self.assertRaises(NativeUnavailable):
                target.load_bundle(candidate, expected)

        # A valid but deliberately incomplete bundle is sealed bundle-only after
        # loading, so later traversal fails closed rather than spending API calls.
        target = APIObjects(Offline())
        target.cache[("commit", source.base)] = source_objects.cache[("commit", source.base)]
        incomplete = target.export_bundle(source.base)
        consumer = APIObjects(Offline())
        consumer.load_bundle(incomplete, source.base)
        with self.assertRaises(NativeUnavailable):
            consumer.entries(consumer.commit(source.base))
        self.assertEqual(consumer.api.calls, 0)

    def test_bundle_rejects_object_mutation_duplicate_unknown_kind_and_bounds(self):
        """Break caught: adversarial bundle framing bypasses typed Git hashes or limits."""
        from omarchy_knowledge.github_native import APIObjects, NativeUnavailable
        from omarchy_knowledge.object_bundle import MAGIC, MAX_OBJECTS

        source, _ = self._published()
        objects = self._cached_objects(source)
        bundle = objects.export_bundle(source.base)
        plain = bytearray(gzip.decompress(bundle))
        first = len(MAGIC) + 20 + 4
        size = struct.unpack(">I", plain[first + 21:first + 25])[0]
        entry = bytes(plain[first:first + 25 + size])

        mutations = []
        changed = bytearray(plain)
        changed[first + 25] ^= 1
        mutations.append(gzip.compress(bytes(changed), mtime=0))
        duplicate = bytearray(plain)
        duplicate[len(MAGIC) + 20:len(MAGIC) + 24] = struct.pack(">I", struct.unpack(">I", duplicate[len(MAGIC) + 20:len(MAGIC) + 24])[0] + 1)
        duplicate.extend(entry)
        mutations.append(gzip.compress(bytes(duplicate), mtime=0))
        unknown = bytearray(plain); unknown[first] = ord("X")
        mutations.append(gzip.compress(bytes(unknown), mtime=0))
        excessive = bytearray(plain)
        excessive[len(MAGIC) + 20:len(MAGIC) + 24] = struct.pack(">I", MAX_OBJECTS + 1)
        mutations.append(gzip.compress(bytes(excessive), mtime=0))
        for number, mutation in enumerate(mutations):
            with self.subTest(number=number), self.assertRaises(NativeUnavailable):
                APIObjects(object()).load_bundle(mutation, source.base)
        with patch("omarchy_knowledge.object_bundle.MAX_BUNDLE", len(plain) - 1):
            with self.assertRaises(NativeUnavailable):
                APIObjects(object()).load_bundle(bundle, source.base)

    def test_pages_transport_is_fixed_origin_bounded_and_sends_no_credentials(self):
        """Break caught: public proof download follows URLs or leaks API credentials."""
        from omarchy_knowledge.github_native import APIObjects, GitHubRead

        raw = b"tree " + b"0" * 40 + b"\nauthor A <a@example.org> 0 +0000\ncommitter A <a@example.org> 0 +0000\n\nx\n"
        head = hashlib.sha1(b"commit " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
        objects = APIObjects(object())
        objects._save("commit", head, raw)
        bundle = objects.export_bundle(head)
        seen = []

        class Response:
            status = 200
            def __init__(self): self.offset = 0
            def read(self, amount):
                chunk = bundle[self.offset:self.offset + amount]
                self.offset += len(chunk)
                return chunk
        class Connection:
            def __init__(self, host, timeout): seen.append(("host", host, timeout))
            def request(self, method, path, body=None, headers=None):
                seen.append((method, path, body, headers))
            def getresponse(self): return Response()
            def close(self): pass

        reader = GitHubRead(read_token="must-not-leak", connection_factory=Connection)
        reader.prefill_canonical(head)
        request = seen[1]
        self.assertEqual(seen[0], ("host", "cylon58.github.io", 5))
        self.assertEqual(request[0:3], ("GET", "/omarchy-community-knowledge/canonical-objects.bundle", None))
        self.assertNotIn("Authorization", request[3])
        self.assertEqual(reader.objects.info(head)["message"], "x")

        pilot_seen = []
        class PilotConnection(Connection):
            def __init__(self, host, timeout): pilot_seen.append((host, timeout))
            def request(self, method, path, body=None, headers=None): pilot_seen.append(path)
        GitHubRead(deployment="pilot", connection_factory=PilotConnection).prefill_canonical(head)
        self.assertEqual(pilot_seen, [("cylon58.github.io", 5),
                                     "/omarchy-community-knowledge-pilot/canonical-objects.bundle"])

    def test_pages_transport_rejects_redirect_and_oversize_body(self):
        """Break caught: static transport follows redirects or buffers past its cap."""
        from omarchy_knowledge.github_native import GitHubRead, NativeUnavailable
        class Connection:
            status = 302
            def __init__(self, *args, **kwargs): pass
            def request(self, *args, **kwargs): pass
            def getresponse(self): return self
            def read(self, amount): return b"x" * amount
            def close(self): pass
        with self.assertRaises(NativeUnavailable):
            GitHubRead(connection_factory=Connection).prefill_canonical("a" * 40)
        Connection.status = 200
        with patch("omarchy_knowledge.object_bundle.MAX_COMPRESSED_BUNDLE", 8):
            with self.assertRaises(NativeUnavailable):
                GitHubRead(connection_factory=Connection).prefill_canonical("a" * 40)

    def test_bundle_tree_parser_rejects_noncanonical_entry_order(self):
        """Break caught: raw tree framing accepts an order Git itself would not create."""
        from omarchy_knowledge.github_native import APIObjects, NativeUnavailable
        contents = [(b"a", b"first"), (b"z", b"last")]
        blobs = [(name, hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest(), raw)
                 for name, raw in contents]
        tree_raw = b"".join(b"100644 " + name + b"\0" + bytes.fromhex(oid)
                            for name, oid, _ in reversed(blobs))
        tree = hashlib.sha1(b"tree " + str(len(tree_raw)).encode() + b"\0" + tree_raw).hexdigest()
        commit_raw = (f"tree {tree}\nauthor A <a@example.org> 0 +0000\n"
                      f"committer A <a@example.org> 0 +0000\n\nroot\n").encode()
        head = hashlib.sha1(b"commit " + str(len(commit_raw)).encode() + b"\0" + commit_raw).hexdigest()
        source = APIObjects(object())
        source._save("commit", head, commit_raw); source._save("tree", tree, tree_raw)
        for _, oid, raw in blobs: source._save("blob", oid, raw)
        consumer = APIObjects(object())
        consumer.load_bundle(source.export_bundle(head), head)
        with self.assertRaises(NativeUnavailable):
            consumer.entries(consumer.commit(head))

    def test_pages_worker_has_hard_deadline_and_token_free_environment(self):
        """Break caught: DNS/TLS/header stalls outlive the worker or inherit credentials."""
        import os
        import subprocess
        from omarchy_knowledge.github_native import GitHubRead, NativeUnavailable
        captured = {}
        class Process:
            returncode = None
            killed = waited = False
            def __init__(self, argv, **kwargs): captured.update(kwargs); captured["argv"] = argv
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def communicate(self, payload, timeout):
                captured["payload"] = payload; captured["timeout"] = timeout
                raise subprocess.TimeoutExpired("fixed-pages-worker", timeout)
            def kill(self): Process.killed = True
            def wait(self): Process.waited = True
        with patch.dict(os.environ, {"GITHUB_TOKEN": "must-not-inherit", "HTTPS_PROXY": "https://evil"}), \
                patch("omarchy_knowledge.github_native.subprocess.Popen", Process):
            with self.assertRaises(NativeUnavailable) as result:
                GitHubRead(read_token="must-not-leak").prefill_canonical("a" * 40)
        self.assertEqual(captured["env"], {"PATH": "/usr/bin:/bin", "LC_ALL": "C"})
        self.assertEqual(captured["payload"], b"production")
        self.assertEqual(captured["timeout"], 15)
        self.assertTrue(Process.killed and Process.waited)
        self.assertNotIn("must-not", str(result.exception))


if __name__ == "__main__":
    unittest.main()
