"""Bounded client update transport and inert proof-sidecar behavior."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


def git_hash(kind, raw):
    return hashlib.sha1(
        kind.encode() + b" " + str(len(raw)).encode() + b"\0" + raw
    ).hexdigest()


def commit(tree, *, parent=None, message="fixture"):
    parent_line = "" if parent is None else f"parent {parent}\n"
    raw = (
        f"tree {tree}\n{parent_line}author Fixture <fixture@example.invalid> 0 +0000\n"
        f"committer Fixture <fixture@example.invalid> 0 +0000\n\n{message}\n"
    ).encode()
    return git_hash("commit", raw), raw


class PagesFixture:
    def __init__(self, repository, head, artifacts):
        self.repository = repository
        self.head = head
        self.artifacts = artifacts
        self.requests = []

    def connection(self):
        fixture = self

        class Response:
            def __init__(self, status, raw):
                self.status = status
                self.raw = raw

            def read(self, amount):
                chunk, self.raw = self.raw[:amount], self.raw[amount:]
                return chunk

        class Connection:
            def __init__(self, host, timeout):
                self.host = host
                fixture.requests.append(("connect", host, timeout))

            def request(self, method, path, body=None, headers=None):
                fixture.requests.append((self.host, method, path, body, headers))
                if self.host == "cylon58.github.io":
                    name = path.rsplit("/", 1)[-1]
                    value = fixture.artifacts.get(name)
                    self.response = Response(404 if value is None else 200, value or b"")
                    return
                values = {
                    "/repos/cylon58/omarchy-community-knowledge": fixture.repository,
                    "/repos/cylon58/omarchy-community-knowledge/git/ref/heads/main": {
                        "ref": "refs/heads/main",
                        "object": {"type": "commit", "sha": fixture.head},
                    },
                }
                self.response = Response(
                    200, json.dumps(values[path], separators=(",", ":")).encode()
                )

            def getresponse(self):
                return self.response

            def close(self):
                pass

        return Connection


class UpdateClientTests(unittest.TestCase):
    def setUp(self):
        self.repository = {
            "id": 1373429914,
            "full_name": "cylon58/omarchy-community-knowledge",
            "default_branch": "main",
        }
        self.tree = git_hash("tree", b"")
        self.base, self.base_raw = commit(self.tree, message="base")
        self.target, self.target_raw = commit(
            self.tree, parent=self.base, message="target"
        )
        self.base_objects = {
            ("commit", self.base): self.base_raw,
            ("tree", self.tree): b"",
        }
        # Retain the reusable base commit as a legitimate extra object.
        self.target_objects = {
            **self.base_objects,
            ("commit", self.target): self.target_raw,
        }

    def bundles(self):
        from omarchy_knowledge.object_bundle import encode
        from omarchy_knowledge.update_pack import generate

        base = encode(self.base, self.base_objects)
        full = encode(self.target, self.target_objects)
        manifest, pack = generate(
            "production", self.base, self.base_objects,
            self.target, self.target_objects,
        )
        return base, full, manifest, pack

    def pages_requests(self, fixture):
        return [row for row in fixture.requests
                if len(row) == 5 and row[0] == "cylon58.github.io"]

    def test_actual_reader_uses_exact_base_update_and_retains_complete_map(self):
        """Break caught: update transport accepts paths or prunes reusable extras."""
        from omarchy_knowledge.github_native import GitHubRead
        from omarchy_knowledge.object_bundle import decode

        base, _full, manifest, pack = self.bundles()
        fixture = PagesFixture(self.repository, self.target, {
            "canonical-update.json": manifest,
            "canonical-update.bundle": pack,
        })
        reader = GitHubRead(connection_factory=fixture.connection())

        proofs = reader.canonical_proofs(self.target, base)
        proof = next(proofs)

        self.assertEqual(decode(proof, self.target), self.target_objects)
        requests = self.pages_requests(fixture)
        self.assertEqual([row[2].rsplit("/", 1)[-1] for row in requests], [
            "canonical-update.json", "canonical-update.bundle",
        ])
        self.assertTrue(all(row[1] == "GET" and row[3] is None for row in requests))
        self.assertTrue(all("Authorization" not in row[4] for row in requests))

    def test_same_head_uses_local_proof_without_pages(self):
        """Break caught: a reusable same-head proof still spends a Pages request."""
        from omarchy_knowledge.github_native import GitHubRead
        from omarchy_knowledge.object_bundle import encode

        local = encode(self.target, self.target_objects)
        fixture = PagesFixture(self.repository, self.target, {})
        reader = GitHubRead(connection_factory=fixture.connection())

        self.assertEqual(next(reader.canonical_proofs(self.target, local)), local)
        self.assertEqual(self.pages_requests(fixture), [])

    def test_corrupt_pack_gets_one_full_fallback_on_the_shared_budget(self):
        """Break caught: failed update resets counters or retries optional artifacts."""
        from omarchy_knowledge.github_native import GitHubRead

        base, full, manifest, _pack = self.bundles()
        fixture = PagesFixture(self.repository, self.target, {
            "canonical-update.json": manifest,
            "canonical-update.bundle": b"broken",
            "canonical-objects.bundle": full,
        })
        reader = GitHubRead(connection_factory=fixture.connection())

        self.assertEqual(list(reader.canonical_proofs(self.target, base)), [full])
        self.assertEqual([row[2].rsplit("/", 1)[-1]
                          for row in self.pages_requests(fixture)], [
            "canonical-update.json", "canonical-update.bundle",
            "canonical-objects.bundle",
        ])
        self.assertEqual(reader.http.calls, 3)
        self.assertGreaterEqual(reader.http.bytes, len(manifest) + len(full))

    def test_pack_attempt_is_skipped_unless_declared_cap_and_full_fallback_fit(self):
        """Break caught: malicious partial pack consumes capacity reserved for full proof."""
        from dataclasses import replace
        from omarchy_knowledge.github_native import GitHubRead, MAX_BYTES
        from omarchy_knowledge.object_bundle import MAX_COMPRESSED_BUNDLE
        from omarchy_knowledge.update_pack import decode_manifest, encode_manifest

        base, full, manifest, _pack = self.bundles()
        value = decode_manifest(
            manifest, expected_deployment="production",
            expected_target_head=self.target,
        )
        manifest = encode_manifest(replace(
            value, pack_size=MAX_COMPRESSED_BUNDLE, pack_sha256="0" * 64,
        ))
        fixture = PagesFixture(self.repository, self.target, {
            "canonical-update.json": manifest,
            "canonical-update.bundle": b"broken",
            "canonical-objects.bundle": full,
        })
        reader = GitHubRead(connection_factory=fixture.connection())
        reader.http.bytes = (MAX_BYTES - (MAX_COMPRESSED_BUNDLE + 1)
                             - (1024 * 1024 + 1))

        self.assertEqual(list(reader.canonical_proofs(self.target, base)), [full])
        self.assertEqual([row[2].rsplit("/", 1)[-1]
                          for row in self.pages_requests(fixture)], [
            "canonical-update.json", "canonical-objects.bundle",
        ])

    def test_oversized_small_pack_charges_sentinel_and_preserves_full_sentinel(self):
        """Break caught: the byte beyond a pack cap consumes fallback capacity."""
        from omarchy_knowledge.github_native import (
            GitHubRead, MAX_BYTES, NativeUnavailable, PAGES_ATTEMPT_SECONDS,
        )
        from omarchy_knowledge.object_bundle import MAX_COMPRESSED_BUNDLE

        _base, full, _manifest, _pack = self.bundles()
        fixture = PagesFixture(self.repository, self.target, {
            "canonical-update.bundle": b"12345",
            "canonical-objects.bundle": full,
        })
        fallback_bound = MAX_COMPRESSED_BUNDLE + 1
        reader = GitHubRead(connection_factory=fixture.connection())
        reader.http.bytes = MAX_BYTES - 5 - fallback_bound

        with self.assertRaises(NativeUnavailable):
            reader._pages(
                "canonical-update.bundle", 4,
                reserve_calls=1, reserve_bytes=fallback_bound,
                reserve_seconds=PAGES_ATTEMPT_SECONDS,
            )

        self.assertEqual(reader.http.bytes, MAX_BYTES - fallback_bound)
        self.assertEqual(reader.http.calls, 1)
        self.assertEqual(
            reader._pages("canonical-objects.bundle", MAX_COMPRESSED_BUNDLE),
            full,
        )
        self.assertEqual(reader.http.bytes,
                         MAX_BYTES - fallback_bound + len(full))

        blocked_fixture = PagesFixture(self.repository, self.target, {
            "canonical-update.bundle": b"12345",
        })
        blocked = GitHubRead(connection_factory=blocked_fixture.connection())
        blocked.http.bytes = MAX_BYTES - 5 - fallback_bound + 1
        with self.assertRaises(NativeUnavailable):
            blocked._pages(
                "canonical-update.bundle", 4,
                reserve_calls=1, reserve_bytes=fallback_bound,
                reserve_seconds=PAGES_ATTEMPT_SECONDS,
            )
        self.assertEqual((blocked.http.calls, self.pages_requests(blocked_fixture)),
                         (0, []))

    def test_sync_replays_bad_typed_update_then_one_full_fallback(self):
        """Break caught: typed hashes substitute for complete canonical replay."""
        from omarchy_knowledge.canonical import sync
        from omarchy_knowledge.coordinator import Policy
        from omarchy_knowledge.object_bundle import encode
        from omarchy_knowledge.update_pack import generate

        valid_tree = self.tree
        valid_head, valid_commit = commit(valid_tree, parent=self.base, message="valid")
        target_objects = {**self.base_objects, ("commit", valid_head): valid_commit}
        # Rebuild a typed-valid but canonically incomplete update for the same
        # authenticated head; the full proof supplies its missing empty tree.
        incomplete = {("commit", valid_head): valid_commit}
        manifest, pack = generate(
            "production", self.base, self.base_objects, valid_head, incomplete,
        )
        full = encode(valid_head, target_objects)
        base = encode(self.base, self.base_objects)
        fixture = PagesFixture(self.repository, valid_head, {
            "canonical-update.json": manifest,
            "canonical-update.bundle": pack,
            "canonical-objects.bundle": full,
        })

        with tempfile.TemporaryDirectory(prefix="update-client-") as temporary:
            cache = Path(temporary) / "cache"
            cache.mkdir()
            from omarchy_knowledge.proof_cache import store
            store(cache, base)
            reader = __import__("omarchy_knowledge.github_native", fromlist=["GitHubRead"]).GitHubRead(
                connection_factory=fixture.connection()
            )
            result = sync(reader, Policy("a" * 40, "b" * 40), cache,
                          now="2026-09-17T00:00:00Z")

        self.assertEqual(result["data_revision"], valid_head)
        self.assertEqual([row[2].rsplit("/", 1)[-1]
                          for row in self.pages_requests(fixture)], [
            "canonical-update.json", "canonical-update.bundle",
            "canonical-objects.bundle",
        ])

    def test_real_sync_cold_full_warm_update_and_same_head(self):
        """Break caught: sync reauthenticates, misses its cache, or reloads same head."""
        from omarchy_knowledge.canonical import sync
        from omarchy_knowledge.coordinator import Policy
        from omarchy_knowledge.github_native import GitHubRead
        from omarchy_knowledge.object_bundle import decode, encode
        from omarchy_knowledge.proof_cache import load
        from omarchy_knowledge.update_pack import generate

        policy = Policy("a" * 40, "b" * 40)
        base_bundle = encode(self.base, self.base_objects)
        cold = PagesFixture(self.repository, self.base, {
            "canonical-objects.bundle": base_bundle,
        })
        with tempfile.TemporaryDirectory(prefix="update-sync-") as temporary:
            cache = Path(temporary) / "cache"
            first = GitHubRead(connection_factory=cold.connection())
            self.assertEqual(sync(first, policy, cache, now="2026-09-17T00:00:00Z")
                             ["data_revision"], self.base)
            self.assertEqual(first.http.calls, 3)  # repository, main, full proof

            manifest, pack = generate(
                "production", self.base, self.base_objects,
                self.target, self.target_objects,
            )
            warm = PagesFixture(self.repository, self.target, {
                "canonical-update.json": manifest,
                "canonical-update.bundle": pack,
            })
            second = GitHubRead(connection_factory=warm.connection())
            self.assertEqual(sync(second, policy, cache, now="2026-09-17T00:00:01Z")
                             ["data_revision"], self.target)
            self.assertEqual(second.http.calls, 4)  # identity/head + manifest/pack
            self.assertEqual(decode(load(cache), self.target), self.target_objects)

            same = PagesFixture(self.repository, self.target, {})
            third = GitHubRead(connection_factory=same.connection())
            self.assertEqual(sync(third, policy, cache, now="2026-09-17T00:00:02Z")
                             ["data_revision"], self.target)
            self.assertEqual(third.http.calls, 2)
            self.assertEqual(self.pages_requests(same), [])

    def test_malformed_missing_stale_and_wrong_base_updates_are_cache_misses(self):
        """Break caught: unauthenticated update metadata blocks the one full fallback."""
        from dataclasses import replace
        from omarchy_knowledge.github_native import GitHubRead
        from omarchy_knowledge.update_pack import decode_manifest, encode_manifest

        base, full, manifest, pack = self.bundles()
        decoded = decode_manifest(
            manifest, expected_deployment="production",
            expected_target_head=self.target,
        )
        variants = {
            "missing": {},
            "malformed": {"canonical-update.json": b"{"},
            "stale-target": {"canonical-update.json": encode_manifest(replace(
                decoded, target_head="f" * 40,
            ))},
            "wrong-base": {"canonical-update.json": encode_manifest(replace(
                decoded, base_head="e" * 40,
            ))},
            "missing-pack": {"canonical-update.json": manifest},
        }
        for name, artifacts in variants.items():
            with self.subTest(name=name):
                fixture = PagesFixture(self.repository, self.target, {
                    **artifacts, "canonical-objects.bundle": full,
                })
                reader = GitHubRead(connection_factory=fixture.connection())
                self.assertEqual(list(reader.canonical_proofs(self.target, base)), [full])
                names = [row[2].rsplit("/", 1)[-1]
                         for row in self.pages_requests(fixture)]
                self.assertEqual(names[-1], "canonical-objects.bundle")
                self.assertLessEqual(names.count("canonical-update.json"), 1)
                self.assertLessEqual(names.count("canonical-update.bundle"), 1)

    def test_call_and_time_reservations_skip_update_but_leave_one_full_attempt(self):
        """Break caught: optional reads consume the final call or wall-time slot."""
        import time
        from omarchy_knowledge.github_native import GitHubRead, MAX_CALLS

        base, full, manifest, pack = self.bundles()
        for constrained in ("calls", "time"):
            fixture = PagesFixture(self.repository, self.target, {
                "canonical-update.json": manifest,
                "canonical-update.bundle": pack,
                "canonical-objects.bundle": full,
            })
            reader = GitHubRead(connection_factory=fixture.connection())
            if constrained == "calls":
                reader.http.calls = MAX_CALLS - 1
            else:
                reader.http.deadline = time.monotonic() + 16
            self.assertEqual(list(reader.canonical_proofs(self.target, base)), [full])
            self.assertEqual([row[2].rsplit("/", 1)[-1]
                              for row in self.pages_requests(fixture)], [
                "canonical-objects.bundle",
            ])

    def test_empty_partial_pages_result_charges_its_enforced_bound(self):
        """Break caught: hidden/partial worker failure is charged as zero bytes."""
        from omarchy_knowledge.github_native import GitHubRead
        from omarchy_knowledge.object_bundle import MAX_COMPRESSED_BUNDLE

        fixture = PagesFixture(self.repository, self.target, {
            "canonical-objects.bundle": b"",
        })
        reader = GitHubRead(connection_factory=fixture.connection())
        self.assertFalse(reader.seed_canonical())
        self.assertEqual((reader.http.calls, reader.http.bytes),
                         (1, MAX_COMPRESSED_BUNDLE + 1))

    def test_full_seed_keeps_existing_remaining_deadline_eligibility(self):
        """Break caught: shared helper requires a fresh 15 seconds for a full seed."""
        import time
        from omarchy_knowledge.github_native import GitHubRead
        from omarchy_knowledge.object_bundle import encode

        proof = encode(self.target, self.target_objects)
        fixture = PagesFixture(self.repository, self.target, {
            "canonical-objects.bundle": proof,
        })
        reader = GitHubRead(connection_factory=fixture.connection())
        reader.http.deadline = time.monotonic() + 1
        self.assertTrue(reader.seed_canonical())


class ProofCacheTests(unittest.TestCase):
    def test_no_follow_bounded_load_and_atomic_private_store(self):
        """Break caught: sidecar follows links, accepts devices, or writes in place."""
        from omarchy_knowledge.object_bundle import MAX_COMPRESSED_BUNDLE
        from omarchy_knowledge.proof_cache import FILENAME, load, store

        with tempfile.TemporaryDirectory(prefix="proof-cache-") as temporary:
            cache = Path(temporary) / "cache"
            cache.mkdir()
            outside = Path(temporary) / "outside"
            outside.write_bytes(b"secret")
            (cache / FILENAME).symlink_to(outside)
            self.assertIsNone(load(cache))
            (cache / FILENAME).unlink()
            os.mkfifo(cache / FILENAME)
            self.assertIsNone(load(cache))
            (cache / FILENAME).unlink()
            with patch("omarchy_knowledge.proof_cache.MAX_PROOF_BYTES", 3):
                (cache / FILENAME).write_bytes(b"four")
                self.assertIsNone(load(cache))
            (cache / FILENAME).unlink()
            store(cache, b"proof")
            self.assertEqual(load(cache), b"proof")
            self.assertEqual((cache / FILENAME).stat().st_mode & 0o777, 0o600)
            self.assertLessEqual(len(load(cache)), MAX_COMPRESSED_BUNDLE)
            self.assertEqual(outside.read_bytes(), b"secret")

    def test_post_current_sidecar_failure_preserves_successful_sync(self):
        """Break caught: an acceleration-cache write rolls back authenticated CURRENT."""
        from omarchy_knowledge.canonical import sync
        from omarchy_knowledge.coordinator import Policy
        from omarchy_knowledge.github_native import GitHubRead
        from omarchy_knowledge.object_bundle import encode
        from omarchy_knowledge.snapshots import load_cache

        tree = git_hash("tree", b"")
        head, raw = commit(tree)
        proof = encode(head, {("commit", head): raw, ("tree", tree): b""})
        repository = {"id": 1373429914,
                      "full_name": "cylon58/omarchy-community-knowledge",
                      "default_branch": "main"}
        fixture = PagesFixture(repository, head, {"canonical-objects.bundle": proof})
        with tempfile.TemporaryDirectory(prefix="proof-cache-sync-") as temporary, \
                patch("omarchy_knowledge.proof_cache.store", side_effect=OSError("disk")):
            cache = Path(temporary) / "cache"
            result = sync(GitHubRead(connection_factory=fixture.connection()),
                          Policy("a" * 40, "b" * 40), cache,
                          now="2026-09-17T00:00:00Z")
            self.assertEqual(result["data_revision"], head)
            self.assertTrue((cache / "CURRENT").is_file())
            self.assertEqual(load_cache(cache).manifest["data_revision"], head)

    def test_failed_refresh_keeps_previous_current_and_proof(self):
        """Break caught: pre-CURRENT refresh failure replaces the prior proof sidecar."""
        from omarchy_knowledge.canonical import sync
        from omarchy_knowledge.coordinator import Policy
        from omarchy_knowledge.github_native import GitHubRead
        from omarchy_knowledge.object_bundle import encode
        from omarchy_knowledge.proof_cache import FILENAME

        tree = git_hash("tree", b"")
        first_head, first_raw = commit(tree, message="first")
        second_head, second_raw = commit(tree, parent=first_head, message="second")
        first_proof = encode(first_head, {
            ("commit", first_head): first_raw, ("tree", tree): b"",
        })
        second_proof = encode(second_head, {
            ("commit", first_head): first_raw,
            ("commit", second_head): second_raw,
            ("tree", tree): b"",
        })
        repository = {"id": 1373429914,
                      "full_name": "cylon58/omarchy-community-knowledge",
                      "default_branch": "main"}
        policy = Policy("a" * 40, "b" * 40)
        with tempfile.TemporaryDirectory(prefix="refresh-retain-") as temporary:
            cache = Path(temporary) / "cache"
            first = PagesFixture(repository, first_head, {
                "canonical-objects.bundle": first_proof,
            })
            sync(GitHubRead(connection_factory=first.connection()), policy, cache,
                 now="2026-09-17T00:00:00Z")
            old_current = (cache / "CURRENT").read_bytes()
            old_proof = (cache / FILENAME).read_bytes()
            second = PagesFixture(repository, second_head, {
                "canonical-objects.bundle": second_proof,
            })
            with patch("omarchy_knowledge.resolution.refresh_canonical",
                       side_effect=OSError("interrupted")), self.assertRaises(OSError):
                sync(GitHubRead(connection_factory=second.connection()), policy, cache,
                     now="2026-09-17T00:00:01Z")
            self.assertEqual((cache / "CURRENT").read_bytes(), old_current)
            self.assertEqual((cache / FILENAME).read_bytes(), old_proof)


if __name__ == "__main__":
    unittest.main()
