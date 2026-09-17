"""Publisher-side direct update generation and retention behavior."""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


def git_object(kind, raw):
    oid = hashlib.sha1(
        kind.encode() + b" " + str(len(raw)).encode() + b"\0" + raw
    ).hexdigest()
    return (kind, oid), raw


class PublisherFixture:
    deployment = "production"

    def __init__(self, artifacts=None):
        self.artifacts = artifacts or {}
        self.requests = []

    def _pages(self, artifact, maximum, **_reserved):
        from omarchy_knowledge.github_native import NativeUnavailable

        self.requests.append((artifact, maximum))
        try:
            return self.artifacts[artifact]
        except KeyError as exc:
            raise NativeUnavailable() from exc


class SeedObjects:
    def __init__(self, error=None):
        self.loaded = []
        self.error = error

    def load_seed(self, raw):
        self.loaded.append(raw)
        if self.error is not None:
            raise self.error


class UpdatePublisherTests(unittest.TestCase):
    def objects(self):
        tree = git_object("tree", b"")
        base = git_object("commit", b"tree " + tree[0][1].encode() + b"\n\nbase\n")
        target = git_object(
            "commit",
            b"tree " + tree[0][1].encode() + b"\nparent "
            + base[0][1].encode() + b"\n\ntarget\n",
        )
        base_objects = dict((tree, base))
        target_objects = {**base_objects, target[0]: target[1]}
        return base[0][1], base_objects, target[0][1], target_objects

    def empty_data(self):
        return {
            "records": [],
            "receipts": [],
            "source": {
                "repository": "cylon58/omarchy-community-knowledge",
                "repository_id": 1373429914,
                "deployment": "production",
                "ref": "refs/heads/main",
                "data_revision": "a" * 40,
                "tree_revision": "b" * 40,
                "toolkit_revision": "c" * 40,
                "policy_revision": "d" * 40,
                "verified_at": "2026-09-17T00:00:00Z",
                "source_updated_at": "2026-09-17T00:00:00Z",
            },
            "upstream": {"version": 1, "status": "not-refreshed", "observations": []},
        }

    def test_changed_head_generates_from_exact_original_full_map(self):
        """Break caught: publisher generates from its pruned warming cache."""
        from omarchy_knowledge.object_bundle import decode, encode
        from omarchy_knowledge.service import _publication_update
        from omarchy_knowledge.update_pack import apply

        base_head, base_objects, target_head, target_objects = self.objects()
        previous = encode(base_head, base_objects)
        proof = encode(target_head, target_objects)
        fixture = PublisherFixture()

        update = _publication_update(
            fixture,
            {"source": {"data_revision": target_head}},
            proof,
            previous,
        )

        self.assertIsNotNone(update)
        manifest, pack = update
        self.assertEqual(
            apply(
                manifest,
                pack,
                deployment="production",
                base_head=base_head,
                base_objects=decode(previous, base_head),
                expected_target_head=target_head,
            ),
            target_objects,
        )
        self.assertEqual(fixture.requests, [])

    def test_unchanged_head_retains_only_a_target_consistent_useful_pack(self):
        """Break caught: an unchanged build replaces or blindly copies its useful pack."""
        from omarchy_knowledge.object_bundle import encode
        from omarchy_knowledge.service import _publication_update
        from omarchy_knowledge.update_pack import generate

        base_head, base_objects, target_head, target_objects = self.objects()
        retained = generate(
            "production", base_head, base_objects, target_head, target_objects,
        )
        proof = encode(target_head, target_objects)
        fixture = PublisherFixture({
            "canonical-update.json": retained[0],
            "canonical-update.bundle": retained[1],
        })

        self.assertEqual(
            _publication_update(
                fixture,
                {"source": {"data_revision": target_head}},
                proof,
                proof,
            ),
            retained,
        )
        self.assertEqual(
            [name for name, _maximum in fixture.requests],
            ["canonical-update.json", "canonical-update.bundle"],
        )

    def test_wrong_target_missing_or_invalid_retention_is_omitted(self):
        """Break caught: stale or incomplete retained artifacts become current output."""
        from omarchy_knowledge.object_bundle import encode
        from omarchy_knowledge.service import _publication_update
        from omarchy_knowledge.update_pack import generate

        base_head, base_objects, target_head, target_objects = self.objects()
        proof = encode(target_head, target_objects)
        other = git_object(
            "commit",
            b"tree " + next(key[1] for key in target_objects if key[0] == "tree").encode()
            + b"\nparent " + target_head.encode() + b"\n\nother\n",
        )
        other_objects = {**target_objects, other[0]: other[1]}
        stale = generate(
            "production", target_head, target_objects, other[0][1], other_objects,
        )
        variants = ({}, {"canonical-update.json": b"{"}, {
            "canonical-update.json": stale[0],
            "canonical-update.bundle": stale[1],
        })

        for artifacts in variants:
            with self.subTest(artifacts=sorted(artifacts)):
                self.assertIsNone(_publication_update(
                    PublisherFixture(artifacts),
                    {"source": {"data_revision": target_head}},
                    proof,
                    proof,
                ))

    def test_seed_keeps_exact_raw_when_warming_is_ineligible(self):
        """Break caught: the 75% warming threshold discards a valid generation base."""
        from omarchy_knowledge.github_native import NativeUnavailable
        from omarchy_knowledge.service import _publisher_seed

        raw = b"exact-prior-full-proof"
        fixture = PublisherFixture({"canonical-objects.bundle": raw})
        fixture.objects = SeedObjects(NativeUnavailable())

        self.assertEqual(_publisher_seed(fixture), raw)
        self.assertEqual(fixture.objects.loaded, [raw])
        self.assertEqual([name for name, _maximum in fixture.requests],
                         ["canonical-objects.bundle"])

    def test_current_proof_is_validated_before_update_generation(self):
        """Break caught: optional generation runs before offline canonical replay."""
        from omarchy_knowledge.service import _publisher_build

        events = []
        api = PublisherFixture({"canonical-objects.bundle": b"prior"})
        api.objects = SeedObjects()
        policy = object()
        data = {"source": {"data_revision": "a" * 40}}

        with patch("omarchy_knowledge.canonical.read_canonical",
                   side_effect=lambda *_args, **_kwargs: events.append("canonical") or data), \
                patch("omarchy_knowledge.service._validated_proof",
                      side_effect=lambda *_args: events.append("validated") or b"proof"), \
                patch("omarchy_knowledge.service._publication_update",
                      side_effect=lambda *_args: events.append("update") or None):
            built = _publisher_build(api, policy, now="2026-09-17T00:00:00Z")

        self.assertEqual(events, ["canonical", "validated", "update"])
        self.assertEqual(built, (data, b"proof", None))

    def test_failed_full_seed_charges_the_oversize_sentinel(self):
        """Break caught: publisher seed failure gets a free byte-budget reset."""
        from omarchy_knowledge.github_native import GitHubRead
        from omarchy_knowledge.object_bundle import MAX_COMPRESSED_BUNDLE
        from omarchy_knowledge.service import _publisher_seed
        from tests.test_update_client import PagesFixture

        repository = {
            "id": 1373429914,
            "full_name": "cylon58/omarchy-community-knowledge",
            "default_branch": "main",
        }
        fixture = PagesFixture(repository, "a" * 40, {
            "canonical-objects.bundle": b"",
        })
        reader = GitHubRead(connection_factory=fixture.connection())

        self.assertIsNone(_publisher_seed(reader))
        self.assertEqual((reader.http.calls, reader.http.bytes),
                         (1, MAX_COMPRESSED_BUNDLE + 1))

    def test_failed_retained_pack_charges_declared_cap_plus_sentinel(self):
        """Break caught: publisher retention undercounts the byte proving oversize."""
        from dataclasses import replace
        from experiments.growth.gates import _NativeFixture
        from omarchy_knowledge.github_native import GitHubRead
        from omarchy_knowledge.object_bundle import encode
        from omarchy_knowledge.service import _publication_update
        from omarchy_knowledge.update_pack import (decode_manifest, encode_manifest,
                                                   generate)

        base_head, base_objects, target_head, target_objects = self.objects()
        proof = encode(target_head, target_objects)
        original, _pack = generate(
            "production", base_head, base_objects, target_head, target_objects,
        )
        decoded = decode_manifest(
            original, expected_deployment="production",
            expected_target_head=target_head,
        )
        manifest = encode_manifest(replace(
            decoded, pack_size=4,
            pack_sha256=hashlib.sha256(b"1234").hexdigest(),
        ))
        with tempfile.TemporaryDirectory(prefix="retained-pack-sentinel-") as temporary:
            with _NativeFixture(Path(temporary)) as fixture:
                fixture.publish_pages(
                    proof, 1, update=(manifest, b"12345"),
                )
                reader = GitHubRead(connection_factory=fixture.connection_factory)
                result = _publication_update(
                    reader, {"source": {"data_revision": target_head}},
                    proof, proof,
                )

        self.assertIsNone(result)
        self.assertEqual((reader.http.calls, reader.http.bytes),
                         (2, len(manifest) + 5))

    def test_distribution_hashes_optional_pair_and_omits_it_atomically_over_cap(self):
        """Break caught: one optional file survives or escapes the 32 MiB total."""
        from omarchy_knowledge import distribution

        status = {"status": "idle", "outcomes": []}
        with tempfile.TemporaryDirectory(prefix="update-distribution-") as temporary:
            root = Path(temporary)
            included = root / "included"
            result = distribution.build_site(
                self.empty_data(), included, status=status,
                proof_bundle=b"full-proof",
                update_manifest=b"manifest",
                update_bundle=b"pack",
            )
            metadata = json.loads((included / "distribution.json").read_bytes())
            for name in ("canonical-objects.bundle", "canonical-update.json",
                         "canonical-update.bundle"):
                raw = (included / name).read_bytes()
                self.assertEqual(metadata["files"][name], {
                    "sha256": hashlib.sha256(raw).hexdigest(), "size": len(raw),
                })

            baseline = root / "baseline"
            base_result = distribution.build_site(
                self.empty_data(), baseline, status=status,
                proof_bundle=b"full-proof",
            )
            omitted = root / "omitted"
            with patch.object(distribution, "MAX_SNAPSHOT_BYTES",
                              base_result["bytes"] + 1):
                capped = distribution.build_site(
                    self.empty_data(), omitted, status=status,
                    proof_bundle=b"full-proof",
                    update_manifest=b"manifest",
                    update_bundle=b"pack",
                )

            self.assertTrue((omitted / "canonical-objects.bundle").exists())
            self.assertFalse((omitted / "canonical-update.json").exists())
            self.assertFalse((omitted / "canonical-update.bundle").exists())

        self.assertLessEqual(result["bytes"], 32 * 1024 * 1024)
        self.assertEqual(capped["bytes"], base_result["bytes"])

    def test_distribution_omits_missing_or_oversize_optional_pair(self):
        """Break caught: invalid optional inputs block old full-only publication."""
        from omarchy_knowledge.distribution import build_site
        from omarchy_knowledge.update_pack import MAX_MANIFEST

        variants = ((b"manifest", None), (None, b"pack"),
                    (b"x" * (MAX_MANIFEST + 1), b"pack"))
        status = {"status": "idle", "outcomes": []}
        with tempfile.TemporaryDirectory(prefix="update-distribution-invalid-") as temporary:
            root = Path(temporary)
            for number, (manifest, pack) in enumerate(variants):
                site = root / str(number)
                build_site(
                    self.empty_data(), site, status=status,
                    proof_bundle=b"full-proof",
                    update_manifest=manifest,
                    update_bundle=pack,
                )
                self.assertTrue((site / "canonical-objects.bundle").exists())
                self.assertFalse((site / "canonical-update.json").exists())
                self.assertFalse((site / "canonical-update.bundle").exists())
            site = root / "oversize-pack"
            with patch("omarchy_knowledge.object_bundle.MAX_COMPRESSED_BUNDLE", 3):
                build_site(
                    self.empty_data(), site, status=status,
                    proof_bundle=b"ok", update_manifest=b"manifest",
                    update_bundle=b"1234",
                )
            self.assertTrue((site / "canonical-objects.bundle").exists())
            self.assertFalse((site / "canonical-update.json").exists())
            self.assertFalse((site / "canonical-update.bundle").exists())

    def test_build_entrypoint_forwards_validated_full_and_optional_artifacts(self):
        """Break caught: production build validates an update but publishes full-only."""
        from omarchy_knowledge import service

        class API:
            pass

        data = self.empty_data()
        update = (b"manifest", b"pack")
        captured = {}
        with tempfile.TemporaryDirectory(prefix="update-service-") as temporary:
            root = Path(temporary)
            event = root / "event.json"
            event.write_text(json.dumps({"repository": {
                "id": 1373429914,
                "full_name": "cylon58/omarchy-community-knowledge",
                "default_branch": "main",
            }}))
            status = root / "status.json"
            status.write_text(json.dumps({
                "version": 2, "stage": "publish", "run_id": 1,
                "run_attempt": 1, "deployment": "production",
                "trusted_lane": "scheduled", "prior_state": "legacy",
                "before_cursor": {"version": 1, "page": 1, "offset": 0,
                                  "after_pull_request": None, "cycle": 0,
                                  "last_full_cycle_at": None},
                "proposed_cursor": {"version": 1, "page": 1, "offset": 0,
                                    "after_pull_request": None, "cycle": 1,
                                    "last_full_cycle_at": "2026-09-17T00:00:00Z"},
                "prior_cursor_health": {"version": 1,
                                        "last_progress_at": None,
                                        "consecutive_drift_runs": 0},
                "selected_action": "after", "scan_outcome": "no-eligible",
                "stop_reason": "cycle-complete",
                "scan_time": "2026-09-17T00:00:00Z",
                "counters": {"page_fetches": 1, "rows_returned": 0,
                             "rows_consumed": 0, "evaluations": 0,
                             "closed": 0, "imported": 0, "rejected": 0,
                             "not_ready": 0, "plans": 0,
                             "cursor_drifts": 0},
                "status": {"status": "idle", "outcomes": []},
                "pages_publishable": True,
                "cursor": {"version": 1, "page": 1, "offset": 0,
                           "after_pull_request": None, "cycle": 1,
                           "last_full_cycle_at": "2026-09-17T00:00:00Z"},
                "cursor_health": {"version": 1,
                                  "last_progress_at": "2026-09-17T00:00:00Z",
                                  "consecutive_drift_runs": 0},
            }))
            output = root / "site"
            environment = {
                "GITHUB_REPOSITORY": "cylon58/omarchy-community-knowledge",
                "GITHUB_REPOSITORY_ID": "1373429914",
                "GITHUB_REF": "refs/heads/main",
                "GITHUB_EVENT_NAME": "workflow_dispatch",
                "GITHUB_EVENT_PATH": str(event),
                "GITHUB_RUN_ID": "1",
                "GITHUB_RUN_ATTEMPT": "1",
                "GITHUB_TOKEN": "fixture-token",
            }

            def build_site(*args, **kwargs):
                captured.update(kwargs)
                return {"bytes": 1, "distribution_sha256": "0" * 64}

            with patch.dict(os.environ, environment), \
                    patch.object(service, "GitHubRead", return_value=API()), \
                    patch.object(service, "_publisher_build",
                                 return_value=(data, b"full", update)) as publisher, \
                    patch("omarchy_knowledge.resolution.refresh_canonical",
                          side_effect=lambda value: value), \
                    patch("omarchy_knowledge.distribution.build_site",
                          side_effect=build_site), \
                    contextlib.redirect_stdout(io.StringIO()):
                result = service.main([
                    "build", "--deployment", "production",
                    "--policy-revision", "a" * 40,
                    "--toolkit-revision", "b" * 40,
                    "--input", str(status), "--output", str(output),
                ])

        self.assertEqual(result, 0)
        publisher.assert_called_once()
        self.assertEqual(captured["proof_bundle"], b"full")
        self.assertEqual(captured["update_manifest"], b"manifest")
        self.assertEqual(captured["update_bundle"], b"pack")
        self.assertEqual(captured["intake_scan"]["selected_action"], "after")

    def test_native_pages_fixture_serves_exact_artifacts_atomically(self):
        """Break caught: native evidence bypasses fixed update routes or partial state."""
        from experiments.growth.gates import _NativeFixture

        headers = {
            "Accept": "application/octet-stream",
            "User-Agent": "omarchy-knowledge-native/1",
        }
        with tempfile.TemporaryDirectory(prefix="update-native-pages-") as temporary:
            with _NativeFixture(Path(temporary)) as fixture:
                original = fixture.pages_state
                with self.assertRaises(ValueError):
                    fixture.publish_pages(
                        b"full", 1, update=(b"manifest", None),
                    )
                self.assertIs(fixture.pages_state, original)

                fixture.publish_pages(
                    b"full", 1, update=(b"manifest", b"pack"),
                )
                expected = {
                    "canonical-objects.bundle": b"full",
                    "canonical-update.json": b"manifest",
                    "canonical-update.bundle": b"pack",
                }
                for name, raw in expected.items():
                    connection = fixture.connection_factory(
                        "cylon58.github.io", timeout=5,
                    )
                    connection.request(
                        "GET", "/omarchy-community-knowledge/" + name,
                        body=None, headers=headers,
                    )
                    response = connection.getresponse()
                    self.assertEqual((response.status, response.read(100)), (200, raw))

    def test_native_pages_fixture_retains_full_only_compatibility(self):
        """Break caught: older full-only publication fabricates optional artifacts."""
        from experiments.growth.gates import _NativeFixture

        with tempfile.TemporaryDirectory(prefix="update-native-full-only-") as temporary:
            with _NativeFixture(Path(temporary)) as fixture:
                fixture.publish_pages(b"full", 1)
                connection = fixture.connection_factory(
                    "cylon58.github.io", timeout=5,
                )
                connection.request(
                    "GET", "/omarchy-community-knowledge/canonical-update.json",
                    body=None, headers={
                        "Accept": "application/octet-stream",
                        "User-Agent": "omarchy-knowledge-native/1",
                    },
                )
                response = connection.getresponse()
                self.assertEqual((response.status, response.read(100)), (404, b""))


if __name__ == "__main__":
    unittest.main()
