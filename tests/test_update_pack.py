"""Tests for the inert, bounded direct-predecessor update-pack codec."""
import gzip
import hashlib
import json
import unittest
from unittest.mock import patch


def git_object(kind, raw):
    oid = hashlib.sha1(
        kind.encode() + b" " + str(len(raw)).encode() + b"\0" + raw
    ).hexdigest()
    return (kind, oid), raw


class UpdatePackTests(unittest.TestCase):
    def setUp(self):
        self.base_commit = git_object("commit", b"base commit")
        self.target_commit = git_object("commit", b"target commit")
        self.shared = git_object("blob", b"shared")
        self.removed = git_object("blob", b"removed")
        self.added = git_object("blob", b"added")
        self.base = dict((self.base_commit, self.shared, self.removed))
        self.target = dict((self.target_commit, self.shared, self.added))
        self.base_head = self.base_commit[0][1]
        self.target_head = self.target_commit[0][1]

    def _generate(self):
        from omarchy_knowledge.update_pack import generate

        result = generate(
            "production", self.base_head, self.base,
            self.target_head, self.target,
        )
        self.assertIsNotNone(result)
        return result

    @staticmethod
    def _json(raw):
        return json.loads(raw)

    @staticmethod
    def _encoded(value):
        return json.dumps(
            value, sort_keys=True, separators=(",", ":"), allow_nan=False,
        ).encode()

    def test_object_set_digest_has_exact_sorted_framing(self):
        """Break caught: set digests depend on insertion order or omit framing."""
        from omarchy_knowledge.update_pack import object_set_sha256

        commit = git_object(
            "commit", b"tree " + b"0" * 40 + b"\n\nmessage\n",
        )
        blob = git_object("blob", b"hello")
        expected = "5083509509c326b5b594ba7dd828a53a1e63bd957d9abb77940aedf3cd79298d"
        self.assertEqual(object_set_sha256(dict((commit, blob))), expected)
        self.assertEqual(object_set_sha256(dict((blob, commit))), expected)

    def test_object_set_digest_is_independent_of_gzip_bytes(self):
        """Break caught: normalized identity accidentally binds compressor output."""
        from omarchy_knowledge.object_bundle import encode
        from omarchy_knowledge.update_pack import object_set_sha256

        objects = dict((self.target_commit, self.added))
        bundle = encode(self.target_head, objects)
        recompressed = gzip.compress(gzip.decompress(bundle), compresslevel=1, mtime=7)
        self.assertNotEqual(bundle, recompressed)
        self.assertEqual(
            object_set_sha256(objects),
            "2414a2502214349bde0e98df490565776b18f583193c1fb044fadb7d609f2f6a",
        )

    def test_generate_manifest_decode_and_apply_round_trip(self):
        """Break caught: a valid direct successor cannot be reconstructed exactly."""
        from omarchy_knowledge.update_pack import apply, decode_manifest

        manifest_raw, pack = self._generate()
        manifest = decode_manifest(
            manifest_raw,
            expected_deployment="production",
            expected_target_head=self.target_head,
        )
        self.assertEqual(manifest.version, 1)
        self.assertEqual(manifest.base_head, self.base_head)
        self.assertEqual(manifest.target_head, self.target_head)
        self.assertEqual(
            manifest.removed,
            tuple(sorted((self.base_commit[0], self.removed[0]))),
        )
        self.assertEqual(manifest.pack_size, len(pack))
        self.assertEqual(manifest.pack_sha256, hashlib.sha256(pack).hexdigest())
        self.assertEqual(
            apply(
                manifest_raw, pack,
                deployment="production",
                base_head=self.base_head,
                base_objects=self.base,
                expected_target_head=self.target_head,
            ),
            self.target,
        )

    def test_manifest_encoding_is_canonical_and_decode_is_strict(self):
        """Break caught: manifests accept alternate schemas, identities, or integers."""
        from omarchy_knowledge.update_pack import (
            Manifest, UpdatePackUnavailable, decode_manifest, encode_manifest,
        )

        manifest_raw, _ = self._generate()
        manifest = decode_manifest(
            manifest_raw,
            expected_deployment="production",
            expected_target_head=self.target_head,
        )
        self.assertEqual(encode_manifest(manifest), manifest_raw)
        self.assertEqual(manifest_raw, self._encoded(self._json(manifest_raw)))

        original = self._json(manifest_raw)
        mutations = []
        for key in original:
            value = dict(original)
            value.pop(key)
            mutations.append(value)
        value = dict(original); value["url"] = "https://example.invalid/pack"
        mutations.append(value)
        for key, replacement in (
            ("version", True), ("deployment", "staging"),
            ("base_head", "A" * 40), ("target_head", self.base_head),
            ("base_object_set_sha256", "A" * 64),
            ("target_object_set_sha256", "0" * 63),
            ("pack_sha256", "g" * 64), ("pack_size", True),
            ("removed", [["blob", "A" * 40]]),
        ):
            value = dict(original); value[key] = replacement
            mutations.append(value)
        value = dict(original); value["removed"] = [list(self.removed[0])] * 2
        mutations.append(value)
        value = dict(original)
        value["removed"] = list(reversed(value["removed"]))
        mutations.append(value)

        for number, value in enumerate(mutations):
            with self.subTest(number=number), self.assertRaises(UpdatePackUnavailable):
                decode_manifest(
                    self._encoded(value),
                    expected_deployment="production",
                    expected_target_head=self.target_head,
                )

        with self.assertRaises(UpdatePackUnavailable):
            encode_manifest(Manifest(**{**manifest.__dict__, "pack_size": 0}))

    def test_manifest_rejects_wrong_context_duplicate_keys_and_malformed_json(self):
        """Break caught: an untrusted manifest escapes strict JSON/context binding."""
        from omarchy_knowledge.update_pack import UpdatePackUnavailable, decode_manifest

        manifest_raw, _ = self._generate()
        for deployment, target in (
            ("pilot", self.target_head),
            ("production", "f" * 40),
        ):
            with self.subTest(deployment=deployment, target=target), \
                    self.assertRaises(UpdatePackUnavailable):
                decode_manifest(
                    manifest_raw,
                    expected_deployment=deployment,
                    expected_target_head=target,
                )
        duplicate = manifest_raw[:-1] + b',"version":1}'
        for raw in (b"{", b"[]", duplicate, b"x" * (1024 * 1024 + 1)):
            with self.subTest(raw=raw[:20]), self.assertRaises(UpdatePackUnavailable):
                decode_manifest(
                    raw,
                    expected_deployment="production",
                    expected_target_head=self.target_head,
                )

    def test_public_interfaces_fail_closed_for_unhashable_deployment_values(self):
        """Break caught: malformed enum inputs leak TypeError past codec rejection."""
        from omarchy_knowledge.update_pack import (
            Manifest, UpdatePackUnavailable, decode_manifest, encode_manifest,
            generate,
        )

        manifest_raw, _ = self._generate()
        with self.assertRaises(UpdatePackUnavailable):
            decode_manifest(
                manifest_raw,
                expected_deployment=[],
                expected_target_head=self.target_head,
            )
        with self.assertRaises(UpdatePackUnavailable):
            generate(
                [], self.base_head, self.base, self.target_head, self.target,
            )
        manifest = self._json(manifest_raw)
        with self.assertRaises(UpdatePackUnavailable):
            encode_manifest(Manifest(
                version=manifest["version"], deployment=[],
                base_head=manifest["base_head"], target_head=manifest["target_head"],
                base_object_set_sha256=manifest["base_object_set_sha256"],
                target_object_set_sha256=manifest["target_object_set_sha256"],
                pack_sha256=manifest["pack_sha256"], pack_size=manifest["pack_size"],
                removed=(),
            ))

    def test_apply_rejects_wrong_base_digest_and_pack_hash_or_size(self):
        """Break caught: application accepts the wrong predecessor or payload bytes."""
        from omarchy_knowledge.update_pack import UpdatePackUnavailable, apply

        manifest_raw, pack_bytes = self._generate()
        cases = []
        cases.append((manifest_raw, pack_bytes, "f" * 40, self.base))
        wrong_base = dict(self.base)
        wrong_base.pop(self.removed[0])
        cases.append((manifest_raw, pack_bytes, self.base_head, wrong_base))
        value = self._json(manifest_raw); value["pack_size"] += 1
        cases.append((self._encoded(value), pack_bytes, self.base_head, self.base))
        value = self._json(manifest_raw); value["pack_sha256"] = "0" * 64
        cases.append((self._encoded(value), pack_bytes, self.base_head, self.base))
        for number, (raw, payload, head, objects) in enumerate(cases):
            with self.subTest(number=number), self.assertRaises(UpdatePackUnavailable):
                apply(
                    raw, payload, deployment="production", base_head=head,
                    base_objects=objects, expected_target_head=self.target_head,
                )

    def test_generate_and_apply_reject_invalid_map_removals_and_additions(self):
        """Break caught: filtered keys, divergent common bytes, or overlaps are trusted."""
        from omarchy_knowledge.object_bundle import encode
        from omarchy_knowledge.update_pack import (
            UpdatePackUnavailable, apply, generate, object_set_sha256,
        )

        malformed = dict(self.target); malformed["parsed-cache-key"] = object()
        with self.assertRaises(UpdatePackUnavailable):
            generate(
                "production", self.base_head, self.base,
                self.target_head, malformed,
            )
        divergent = dict(self.target); divergent[self.shared[0]] = b"different"
        with self.assertRaises(UpdatePackUnavailable):
            generate(
                "production", self.base_head, self.base,
                self.target_head, divergent,
            )

        manifest_raw, pack_bytes = self._generate()
        value = self._json(manifest_raw)
        absent = git_object("blob", b"absent")[0]
        value["removed"] = sorted(value["removed"] + [list(absent)])
        with self.assertRaises(UpdatePackUnavailable):
            apply(
                self._encoded(value), pack_bytes, deployment="production",
                base_head=self.base_head, base_objects=self.base,
                expected_target_head=self.target_head,
            )

        overlap_objects = dict((self.target_commit, self.shared))
        overlap_pack = encode(self.target_head, overlap_objects)
        intended = dict(self.base)
        intended.pop(self.base_commit[0]); intended.pop(self.removed[0])
        intended[self.target_commit[0]] = self.target_commit[1]
        value = self._json(manifest_raw)
        value["pack_size"] = len(overlap_pack)
        value["pack_sha256"] = hashlib.sha256(overlap_pack).hexdigest()
        value["target_object_set_sha256"] = object_set_sha256(intended)
        with self.assertRaises(UpdatePackUnavailable):
            apply(
                self._encoded(value), overlap_pack, deployment="production",
                base_head=self.base_head, base_objects=self.base,
                expected_target_head=self.target_head,
            )

    def test_apply_rejects_truncated_bomb_and_trailing_payload(self):
        """Break caught: bundle parser limits or exact gzip framing are bypassed."""
        from omarchy_knowledge.update_pack import UpdatePackUnavailable, apply

        manifest_raw, pack_bytes = self._generate()
        payloads = (pack_bytes[:-1], pack_bytes + b"trailing")
        for payload in payloads:
            value = self._json(manifest_raw)
            value["pack_size"] = len(payload)
            value["pack_sha256"] = hashlib.sha256(payload).hexdigest()
            with self.subTest(length=len(payload)), self.assertRaises(UpdatePackUnavailable):
                apply(
                    self._encoded(value), payload, deployment="production",
                    base_head=self.base_head, base_objects=self.base,
                    expected_target_head=self.target_head,
                )
        with patch("omarchy_knowledge.object_bundle.MAX_BUNDLE", 8), \
                self.assertRaises(UpdatePackUnavailable):
            apply(
                manifest_raw, pack_bytes, deployment="production",
                base_head=self.base_head, base_objects=self.base,
                expected_target_head=self.target_head,
            )

    def test_object_maps_enforce_hash_per_kind_count_and_raw_limits(self):
        """Break caught: normalized maps bypass the established proof bounds."""
        from omarchy_knowledge.update_pack import UpdatePackUnavailable, object_set_sha256

        wrong_hash = {("blob", "0" * 40): b"content"}
        over_kind = dict((git_object("blob", b"x" * 65537),))
        for objects in ({}, wrong_hash, over_kind):
            with self.subTest(objects=len(objects)), self.assertRaises(UpdatePackUnavailable):
                object_set_sha256(objects)
        three = dict(git_object("blob", bytes([number])) for number in range(3))
        with patch("omarchy_knowledge.object_bundle.MAX_OBJECTS", 2), \
                self.assertRaises(UpdatePackUnavailable):
            object_set_sha256(three)
        with patch("omarchy_knowledge.object_bundle.MAX_RAW_OBJECTS", 2), \
                self.assertRaises(UpdatePackUnavailable):
            object_set_sha256({self.shared[0]: self.shared[1]})

    def test_generate_omits_pack_when_target_commit_is_already_in_base(self):
        """Break caught: no-op, rollback, or layout changes emit ambiguous deltas."""
        from omarchy_knowledge.update_pack import generate

        rollback_base = dict(self.base)
        rollback_base[self.target_commit[0]] = self.target_commit[1]
        self.assertIsNone(generate(
            "production", self.base_head, rollback_base,
            self.target_head, self.target,
        ))

    def test_retained_target_requires_current_target_and_matching_delta(self):
        """Break caught: retention republishes a pack unrelated to the current proof."""
        from omarchy_knowledge.update_pack import (
            UpdatePackUnavailable, object_set_sha256, validate_retained_target,
        )

        manifest_raw, pack_bytes = self._generate()
        manifest = validate_retained_target(
            manifest_raw, pack_bytes, deployment="production",
            expected_target_head=self.target_head, target_objects=self.target,
        )
        self.assertEqual(manifest.target_head, self.target_head)

        missing_addition = dict(self.target); missing_addition.pop(self.added[0])
        value = self._json(manifest_raw)
        value["target_object_set_sha256"] = object_set_sha256(missing_addition)
        with self.assertRaises(UpdatePackUnavailable):
            validate_retained_target(
                self._encoded(value), pack_bytes, deployment="production",
                expected_target_head=self.target_head,
                target_objects=missing_addition,
            )

        retained_removal = dict(self.target); retained_removal[self.removed[0]] = self.removed[1]
        value = self._json(manifest_raw)
        value["target_object_set_sha256"] = object_set_sha256(retained_removal)
        with self.assertRaises(UpdatePackUnavailable):
            validate_retained_target(
                self._encoded(value), pack_bytes, deployment="production",
                expected_target_head=self.target_head,
                target_objects=retained_removal,
            )


if __name__ == "__main__":
    unittest.main()
