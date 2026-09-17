"""Offline tests for snapshots, local discovery, drafts, and consent receipts."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from test_records import PUBLIC_COMMIT_URL, case, change, event, report


NOW = datetime(2026, 9, 16, 16, 0, tzinfo=timezone.utc)


def write_records(directory, records):
    for number, record in enumerate(records):
        (Path(directory) / f"{number}-{record['type']}.json").write_text(
            json.dumps(record), encoding="utf-8"
        )


def write_canonical_records(directory, records):
    root = Path(directory)
    for record in records:
        record_type = record["type"] + "s"
        target = root / record_type
        target.mkdir(parents=True, exist_ok=True)
        (target / f"{record['id']}.json").write_text(json.dumps(record), encoding="utf-8")


def routes():
    def repository(repository_id, slug):
        return {"provider": "github", "repository_id": repository_id, "slug": slug}
    return {
        "routing_version": 1,
        "destinations": {
            "ledger": repository("101", "example/ledger"),
            "toolkit": repository("102", "example/toolkit"),
            "upstream": repository("103", "example/upstream"),
            "plugin": repository("104", "example/plugin"),
        },
    }


class SnapshotWorkflow(unittest.TestCase):
    def test_import_populated_cache_directory_on_repository_filesystem(self):
        from omarchy_knowledge.snapshots import build_snapshot, import_snapshot, load_cache

        # /tmp may be tmpfs while the real cache lives on Btrfs. Opening an empty
        # directory before populating it exposes a stale first scan on Btrfs.
        with tempfile.TemporaryDirectory(prefix='.cache-filesystem-test-', dir=Path(__file__).parents[1]) as temporary:
            root = Path(temporary)
            for count in (0, 1):
                source = root / f'source-{count}'; source.mkdir()
                if count:
                    write_records(source, [case()])
                candidate = root / f'snapshot-{count}'; cache = root / f'cache-{count}'
                build_snapshot(source, candidate, data_revision='fixture', toolkit_revision='fixture',
                               created_at='2026-09-16T16:00:00Z', source_updated_at='2026-09-16T16:00:00Z',
                               source_reference='filesystem-fixture')
                import_snapshot(candidate, cache)
                self.assertEqual(len(load_cache(cache).records), count)
                self.assertFalse(any(path.name.startswith('.candidate-') for path in (cache / 'snapshots').iterdir()))

    def test_directory_enumeration_stays_bound_to_open_inode_after_path_replacement(self):
        from omarchy_knowledge.snapshots import _bounded_directory_names, _open_directory

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); original = root / 'original'; original.mkdir()
            (original / 'expected.json').write_bytes(b'{}')
            descriptor = _open_directory(original, 'Fixture')
            try:
                original.rename(root / 'moved')
                foreign = root / 'foreign'; foreign.mkdir()
                (foreign / 'unexpected.json').write_bytes(b'{}')
                original.symlink_to(foreign, target_is_directory=True)
                self.assertEqual(_bounded_directory_names(descriptor, 1), ['expected.json'])
                with self.assertRaisesRegex(ValueError, 'bound'):
                    _bounded_directory_names(descriptor, 0)
            finally:
                os.close(descriptor)

    def test_canonical_records_layout_builds_the_same_snapshot_as_flat_fixtures(self):
        from omarchy_knowledge.snapshots import build_snapshot

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            flat = root / "flat"; flat.mkdir(); write_records(flat, [case(), change(), report(), event()])
            canonical = root / "repository" / "records"
            write_canonical_records(canonical, [case(), change(), report(), event()])
            options = dict(data_revision="fixture-v1", toolkit_revision="task6",
                           created_at="2026-09-16T16:00:00Z",
                           source_updated_at="2026-09-16T15:00:00Z",
                           source_reference="synthetic-test-fixture")
            build_snapshot(flat, root / "flat-snapshot", **options)
            build_snapshot(canonical, root / "canonical-snapshot", **options)
            for name in ("records.jsonl", "index.json"):
                self.assertEqual((root / "flat-snapshot" / name).read_bytes(),
                                 (root / "canonical-snapshot" / name).read_bytes())

    def test_canonical_layout_rejects_path_type_id_mismatches_and_nonrecord_content(self):
        from omarchy_knowledge.snapshots import build_snapshot

        options = dict(data_revision="fixture-v1", toolkit_revision="task6",
                       created_at="2026-09-16T16:00:00Z",
                       source_updated_at="2026-09-16T15:00:00Z", source_reference="fixture")
        mutations = ("wrong-directory", "wrong-id", "unknown-file", "provenance-directory")
        for mutation in mutations:
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary); source = root / "records"
                write_canonical_records(source, [case()])
                record_path = source / "cases" / f"{case()['id']}.json"
                if mutation == "wrong-directory":
                    (source / "changes").mkdir()
                    record_path.rename(source / "changes" / record_path.name)
                    (source / "cases").rmdir()
                elif mutation == "wrong-id":
                    record_path.rename(record_path.with_name("ffffffff-ffff-4fff-8fff-ffffffffffff.json"))
                elif mutation == "unknown-file":
                    (source / "README.md").write_text("not a record", encoding="utf-8")
                else:
                    (source / "provenance").mkdir()
                with self.assertRaises(ValueError):
                    build_snapshot(source, root / "snapshot", **options)

    def test_canonical_layout_rejects_nested_symlinks_mixed_layout_and_aggregate_overrun(self):
        import omarchy_knowledge.snapshots as snapshots

        options = dict(data_revision="fixture-v1", toolkit_revision="task6",
                       created_at="2026-09-16T16:00:00Z",
                       source_updated_at="2026-09-16T15:00:00Z", source_reference="fixture")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); source = root / "records"
            write_canonical_records(source, [case()])
            target = source / "cases" / f"{case()['id']}.json"
            linked = source / "cases" / "ffffffff-ffff-4fff-8fff-ffffffffffff.json"
            linked.symlink_to(target)
            with self.assertRaisesRegex(ValueError, "symlink"):
                snapshots.build_snapshot(source, root / "linked", **options)
            linked.unlink()
            (source / "loose.json").write_text(json.dumps(case()), encoding="utf-8")
            with self.assertRaises(ValueError):
                snapshots.build_snapshot(source, root / "mixed", **options)
            (source / "loose.json").unlink()
            with mock.patch.object(snapshots, "MAX_SNAPSHOT_BYTES", 1):
                with self.assertRaisesRegex(ValueError, "total size"):
                    snapshots.build_snapshot(source, root / "oversized", **options)

    def test_rebuild_is_deterministic_and_manifest_binds_bytes(self):
        from omarchy_knowledge.snapshots import build_snapshot, load_snapshot

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"; source.mkdir()
            write_records(source, [report(), case(), event(), change()])
            first, second = root / "first", root / "second"
            options = dict(data_revision="fixture-v1", toolkit_revision="85d996e",
                           created_at="2026-09-16T16:00:00Z",
                           source_updated_at="2026-09-16T15:00:00Z",
                           source_reference="synthetic-test-fixture")
            build_snapshot(source, first, **options)
            build_snapshot(source, second, **options)
            self.assertEqual((first / "records.jsonl").read_bytes(), (second / "records.jsonl").read_bytes())
            self.assertEqual((first / "index.json").read_bytes(), (second / "index.json").read_bytes())
            self.assertEqual((first / "manifest.json").read_bytes(), (second / "manifest.json").read_bytes())
            snapshot = load_snapshot(first)
            self.assertEqual([record["id"] for record in snapshot.records], sorted(record["id"] for record in snapshot.records))
            for name in ("records.jsonl", "index.json"):
                self.assertEqual(snapshot.manifest["files"][name]["sha256"],
                                 hashlib.sha256((first / name).read_bytes()).hexdigest())

    def test_corrupt_candidate_preserves_current_cache(self):
        from omarchy_knowledge.snapshots import build_snapshot, import_snapshot, load_cache

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); source = root / "source"; source.mkdir()
            write_records(source, [case()])
            good = root / "good"
            build_snapshot(source, good, data_revision="fixture-v1", toolkit_revision="85d996e",
                           created_at="2026-09-16T16:00:00Z", source_updated_at="2026-09-16T15:00:00Z",
                           source_reference="synthetic-test-fixture")
            cache = root / "cache"
            import_snapshot(good, cache)
            old = (cache / "CURRENT").read_text(encoding="ascii")
            (good / "records.jsonl").write_text("corrupt\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "integrity"):
                import_snapshot(good, cache)
            self.assertEqual((cache / "CURRENT").read_text(encoding="ascii"), old)
            self.assertEqual(load_cache(cache).manifest["data_revision"], "fixture-v1")

    def test_corrupt_existing_slot_cannot_capture_current_pointer(self):
        from omarchy_knowledge.snapshots import build_snapshot, import_snapshot

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); source = root / "source"; source.mkdir(); write_records(source, [case()])
            first = root / "first"; second = root / "second"; cache = root / "cache"
            common = dict(toolkit_revision="85d996e", created_at="2026-09-16T16:00:00Z",
                          source_updated_at="2026-09-16T15:00:00Z", source_reference="fixture")
            build_snapshot(source, first, data_revision="fixture-v1", **common)
            build_snapshot(source, second, data_revision="fixture-v2", **common)
            import_snapshot(first, cache)
            first_slot = (cache / "CURRENT").read_text().strip()
            import_snapshot(second, cache)
            current = (cache / "CURRENT").read_text()
            (cache / "snapshots" / first_slot / "records.jsonl").write_text("corrupt\n")
            with self.assertRaisesRegex(ValueError, "integrity"):
                import_snapshot(first, cache)
            self.assertEqual((cache / "CURRENT").read_text(), current)

    def test_import_rejects_symlinked_snapshots_component_without_external_write(self):
        from omarchy_knowledge.snapshots import build_snapshot, import_snapshot

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); source = root / "source"; source.mkdir(); write_records(source, [case()])
            candidate = root / "candidate"
            build_snapshot(source, candidate, data_revision="fixture-v1", toolkit_revision="726ca9c",
                           created_at="2026-09-16T16:00:00Z", source_updated_at="2026-09-16T15:00:00Z",
                           source_reference="fixture")
            cache = root / "cache"; cache.mkdir(); outside = root / "outside"; outside.mkdir()
            (cache / "snapshots").symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "symlink|directory"):
                import_snapshot(candidate, cache)
            self.assertEqual(list(outside.iterdir()), [])
            self.assertFalse((cache / "CURRENT").exists())

    def test_import_rejects_symlinked_cache_ancestor_before_creating_missing_child(self):
        from omarchy_knowledge.snapshots import build_snapshot, import_snapshot

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); source = root / "source"; source.mkdir(); write_records(source, [case()])
            candidate = root / "candidate"
            build_snapshot(source, candidate, data_revision="fixture-v1", toolkit_revision="eeb3324",
                           created_at="2026-09-16T16:00:00Z", source_updated_at="2026-09-16T15:00:00Z",
                           source_reference="fixture")
            outside = root / "outside"; outside.mkdir()
            alias = root / "alias"; alias.symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "symlink|directory"):
                import_snapshot(candidate, alias / "new-cache")
            self.assertEqual(list(outside.iterdir()), [])

    def test_cache_reads_reject_symlinked_root_and_snapshots_component(self):
        from omarchy_knowledge.snapshots import build_snapshot, import_snapshot, load_cache

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); source = root / "source"; source.mkdir(); write_records(source, [case()])
            candidate = root / "candidate"
            build_snapshot(source, candidate, data_revision="fixture-v1", toolkit_revision="726ca9c",
                           created_at="2026-09-16T16:00:00Z", source_updated_at="2026-09-16T15:00:00Z",
                           source_reference="fixture")
            cache = root / "cache"; import_snapshot(candidate, cache)
            alias = root / "cache-alias"; alias.symlink_to(cache, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "symlink|directory"):
                load_cache(alias)
            with self.assertRaisesRegex(ValueError, "symlink|directory"):
                import_snapshot(candidate, alias)
            outside = root / "outside"
            (cache / "snapshots").rename(outside)
            (cache / "snapshots").symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "symlink|directory"):
                load_cache(cache)

    def test_source_entry_count_is_bounded_before_record_reads(self):
        from omarchy_knowledge.snapshots import MAX_RECORD_FILES, build_snapshot

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); source = root / "source"; source.mkdir()
            for number in range(MAX_RECORD_FILES + 1):
                (source / f"{number:04}.json").touch()
            with self.assertRaisesRegex(ValueError, "count"):
                build_snapshot(source, root / "snapshot", data_revision="fixture-v1",
                               toolkit_revision="726ca9c", created_at="2026-09-16T16:00:00Z",
                               source_updated_at="2026-09-16T15:00:00Z", source_reference="fixture")

    def test_rejects_symlinks_unknown_manifest_fields_and_duplicate_json_keys(self):
        from omarchy_knowledge.snapshots import build_snapshot, load_snapshot

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); source = root / "source"; source.mkdir()
            target = source / "record.json"; target.write_text(json.dumps(case()), encoding="utf-8")
            (source / "linked.json").symlink_to(target)
            with self.assertRaisesRegex(ValueError, "symlink"):
                build_snapshot(source, root / "snapshot", data_revision="fixture-v1",
                               toolkit_revision="85d996e", created_at="2026-09-16T16:00:00Z",
                               source_updated_at="2026-09-16T15:00:00Z", source_reference="fixture")
            (source / "linked.json").unlink()
            snapshot = root / "snapshot"
            build_snapshot(source, snapshot, data_revision="fixture-v1", toolkit_revision="85d996e",
                           created_at="2026-09-16T16:00:00Z", source_updated_at="2026-09-16T15:00:00Z",
                           source_reference="fixture")
            manifest = json.loads((snapshot / "manifest.json").read_text())
            manifest["trusted"] = True
            (snapshot / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "manifest"):
                load_snapshot(snapshot)
            (snapshot / "manifest.json").write_text('{"manifest_version":1,"manifest_version":1}', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Duplicate"):
                load_snapshot(snapshot)

    def test_rejects_noncanonical_jsonl_even_when_attacker_rehashes_manifest(self):
        from omarchy_knowledge.snapshots import build_snapshot, load_snapshot

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); source = root / "source"; source.mkdir(); write_records(source, [case()])
            snapshot = root / "snapshot"
            build_snapshot(source, snapshot, data_revision="fixture-v1", toolkit_revision="85d996e",
                           created_at="2026-09-16T16:00:00Z", source_updated_at="2026-09-16T15:00:00Z",
                           source_reference="fixture")
            records = json.loads((snapshot / "records.jsonl").read_text())
            noncanonical = (json.dumps(records) + "\n").encode()
            (snapshot / "records.jsonl").write_bytes(noncanonical)
            manifest = json.loads((snapshot / "manifest.json").read_text())
            manifest["files"]["records.jsonl"] = {
                "sha256": hashlib.sha256(noncanonical).hexdigest(), "size": len(noncanonical),
            }
            (snapshot / "manifest.json").write_text(
                json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n"
            )
            with self.assertRaisesRegex(ValueError, "canonical"):
                load_snapshot(snapshot)

    def test_stale_status_discloses_offline_limits(self):
        from omarchy_knowledge.snapshots import build_snapshot, import_snapshot, cache_status

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); source = root / "source"; source.mkdir(); write_records(source, [case()])
            snapshot = root / "snapshot"
            build_snapshot(source, snapshot, data_revision="fixture-v1", toolkit_revision="85d996e",
                           created_at="2026-09-14T16:00:00Z", source_updated_at="2026-09-14T15:00:00Z",
                           source_reference="fixture")
            import_snapshot(snapshot, root / "cache")
            status = cache_status(root / "cache", now=NOW, stale_after_seconds=86400)
            self.assertTrue(status["stale"])
            self.assertEqual(status["age_seconds"], 172800)
            self.assertEqual(status["trust"], "integrity-only; authenticity-not-established")
            self.assertIn("cannot establish whether an upstream fix exists", status["offline_disclosure"])


class DiscoveryWorkflow(unittest.TestCase):
    def setUp(self):
        from omarchy_knowledge.snapshots import build_snapshot, import_snapshot

        self.temporary = tempfile.TemporaryDirectory(); self.root = Path(self.temporary.name)
        source = self.root / "source"; source.mkdir(); write_records(source, [case(), change(), report(), event()])
        snapshot = self.root / "snapshot"
        build_snapshot(source, snapshot, data_revision="fixture-v1", toolkit_revision="85d996e",
                       created_at="2026-09-16T16:00:00Z", source_updated_at="2026-09-16T15:00:00Z",
                       source_reference="fixture")
        self.cache = self.root / "cache"; import_snapshot(snapshot, self.cache)

    def tearDown(self):
        self.temporary.cleanup()

    def test_search_returns_ids_predicates_reasons_unknowns_and_untrusted_evidence(self):
        from omarchy_knowledge.discovery import search_snapshot

        environment = report()["payload"]["environment"]
        results = search_snapshot(self.cache, "keyboard", environment=environment, intent="corrective")
        self.assertEqual(results["results"][0]["case_id"], case()["id"])
        projection = results["results"][0]["changes"][0]
        self.assertEqual(projection["change_id"], change()["id"])
        self.assertIn("predicates", projection)
        self.assertIn("reasons", projection["applicability"])
        self.assertIn("missing", projection["applicability"])
        self.assertEqual(projection["evidence"]["trust_basis"], "no-authenticated-receipts-configured")
        self.assertEqual(projection["recommendation"]["action"], "investigate")

    def test_intent_selection_handles_optional_and_undetermined_explicitly(self):
        from omarchy_knowledge.snapshots import build_snapshot, import_snapshot
        from omarchy_knowledge.discovery import search_snapshot

        source = self.root / "alternate"; source.mkdir()
        optional = case(); optional["payload"]["intent"] = "optional"
        unknown = case("1d94c6f4-284c-41ec-9d03-754131ca66ea"); unknown["payload"]["intent"] = "undetermined"
        write_records(source, [optional, unknown])
        snapshot = self.root / "alternate-snapshot"
        build_snapshot(source, snapshot, data_revision="fixture-v2", toolkit_revision="85d996e",
                       created_at="2026-09-16T16:00:00Z", source_updated_at="2026-09-16T15:00:00Z",
                       source_reference="fixture")
        cache = self.root / "alternate-cache"; import_snapshot(snapshot, cache)
        self.assertEqual(len(search_snapshot(cache, "keyboard", intent="optional")["results"]), 1)
        self.assertEqual(len(search_snapshot(cache, "keyboard", intent="undetermined")["results"]), 1)
        self.assertEqual(len(search_snapshot(cache, "keyboard", intent="all")["results"]), 2)
        with self.assertRaisesRegex(ValueError, "intent"):
            search_snapshot(cache, "keyboard", intent="maybe")

    def test_matching_changes_rank_before_unknown_and_incompatible_changes_are_not_suggested(self):
        from omarchy_knowledge.snapshots import build_snapshot, import_snapshot
        from omarchy_knowledge.discovery import search_snapshot

        matching = change()
        unknown = deepcopy(matching); unknown["id"] = "2d94c6f4-284c-41ec-9d03-754131ca66ea"
        unknown["payload"]["applicability"] = {"requires": [{
            "kind": "component", "selector": {"kind": "software", "component": "shell", "name": "zsh"},
            "presence": True,
        }]}
        incompatible = deepcopy(matching); incompatible["id"] = "3d94c6f4-284c-41ec-9d03-754131ca66ea"
        incompatible["payload"]["applicability"] = {"requires": [{
            "kind": "feature", "feature": "suspend", "value": False,
        }]}
        source = self.root / "rank-source"; source.mkdir()
        write_records(source, [case(), matching, unknown, incompatible])
        snapshot = self.root / "rank-snapshot"
        build_snapshot(source, snapshot, data_revision="fixture-rank", toolkit_revision="85d996e",
                       created_at="2026-09-16T16:00:00Z", source_updated_at="2026-09-16T15:00:00Z",
                       source_reference="fixture")
        cache = self.root / "rank-cache"; import_snapshot(snapshot, cache)
        environment = report()["payload"]["environment"]
        environment["components"].append({
            "alias": "kernel", "selector": {"kind": "software", "component": "kernel"},
            "version": "6.12.1", "version_scheme": "semver",
        })
        environment["topology"]["nodes"].append("kernel")
        result = search_snapshot(cache, "keyboard", environment=environment)
        self.assertEqual([item["change_id"] for item in result["results"][0]["changes"]],
                         [matching["id"], unknown["id"]])
        self.assertEqual(result["results"][0]["incompatible_change_ids"], [incompatible["id"]])
        self.assertNotIn("confidence", json.dumps(result).casefold())


class ContributionWorkflow(unittest.TestCase):
    def test_drafts_of_all_four_types_validate_with_the_existing_corpus(self):
        from omarchy_knowledge.contributions import save_draft

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            records = [case(), change(), report(), event()]
            for record in records:
                output = root / f"{record['type']}.json"
                result = save_draft(record, records, output)
                self.assertEqual(result["type"], record["type"])
                self.assertEqual(result["record"], record)
                self.assertEqual(json.loads(output.read_text()), record)

    def test_invalid_draft_fails_without_writing_output(self):
        from omarchy_knowledge.contributions import save_draft

        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "bad.json"
            invalid = change(); invalid["payload"]["case_id"] = "ffffffff-ffff-4fff-8fff-ffffffffffff"
            with self.assertRaisesRegex(ValueError, "reference"):
                save_draft(invalid, [case()], output)
            self.assertFalse(output.exists())

    def test_routing_fails_closed_and_consent_binds_every_publication_byte(self):
        from omarchy_knowledge.contributions import build_preview, record_consent, verify_consent

        preview = build_preview(change(), routes(), destination="ledger", title="Fix dock input",
                                body="Synthetic contribution.", attribution="Submitted by account 42")
        receipt = record_consent(preview, routes(), approved_at="2026-09-16T16:00:00Z",
                                 receipt_id="de3e61da-3b3c-4e4e-9fba-970ef606672f",
                                 explicit_local_command=True)
        self.assertTrue(verify_consent(preview, routes(), receipt))
        for field, replacement in (("title", "Changed title"), ("body", "Changed body"),
                                   ("attribution", "Different attribution")):
            changed = deepcopy(preview); changed[field] = replacement
            self.assertFalse(verify_consent(changed, routes(), receipt))
        changed = deepcopy(preview); changed["destination"]["repository_id"] = "999"
        self.assertFalse(verify_consent(changed, routes(), receipt))
        config = routes(); del config["destinations"]["upstream"]
        with self.assertRaisesRegex(ValueError, "routing"):
            build_preview(event(), config, destination="upstream", title="Title", body="Body", attribution="Account 42")
        with self.assertRaisesRegex(ValueError, "explicit"):
            record_consent(preview, routes(), approved_at="2026-09-16T16:00:00Z")
        with self.assertRaisesRegex(ValueError, "timestamp"):
            record_consent(preview, routes(), approved_at="yesterday", explicit_local_command=True)

    def test_public_commit_reference_survives_record_parsing_and_preview(self):
        import knowledge
        from omarchy_knowledge.contributions import build_preview

        record = case()
        record["provenance"] = {"kind": "external-source", "sources": [PUBLIC_COMMIT_URL]}
        parsed = knowledge.parse_record(json.dumps(record))
        preview = build_preview(
            parsed, routes(), destination="ledger", title="Public source observation",
            body="Reviewed inert public evidence.", attribution="Submitted by @fixture-account",
        )
        self.assertEqual(preview["record"], record)

    def test_preview_privacy_covers_all_public_text_and_direct_consent_objects(self):
        from omarchy_knowledge.contributions import build_preview, publication_bytes, record_consent

        safe = build_preview(change(), routes(), destination="ledger", title="Fix dock input",
                             body="Synthetic contribution.", attribution="Submitted by @fixture-account")
        for field, unsafe in (
            ("title", "Credential api%5Fkey=short-value"),
            ("body", "Contact fixture-person@example.org"),
            ("attribution", "Submitted from /home/private-user/draft.json"),
        ):
            with self.subTest(field=field):
                kwargs = {"title": safe["title"], "body": safe["body"],
                          "attribution": safe["attribution"]}
                kwargs[field] = unsafe
                with self.assertRaises(ValueError) as caught:
                    build_preview(change(), routes(), destination="ledger", **kwargs)
                self.assertNotIn(unsafe, str(caught.exception))

                direct = deepcopy(safe); direct[field] = unsafe
                with self.assertRaises(ValueError) as caught:
                    publication_bytes(direct, routes())
                self.assertNotIn(unsafe, str(caught.exception))
                with self.assertRaises(ValueError):
                    record_consent(direct, routes(), approved_at="2026-09-16T16:00:00Z",
                                   explicit_local_command=True)

    def test_direct_preview_consent_requires_exact_trusted_routing(self):
        from omarchy_knowledge.contributions import build_preview, publication_bytes, record_consent

        preview = build_preview(change(), routes(), destination="ledger", title="Fix dock input",
                                body="Synthetic contribution.", attribution="Submitted by @fixture-account")
        for mutate in (
            lambda item: item["destination"].update({"repository_id": "999"}),
            lambda item: item.update({"destination_name": "upstream"}),
        ):
            with self.subTest(mutate=mutate):
                direct = deepcopy(preview); mutate(direct)
                with self.assertRaisesRegex(ValueError, "preview"):
                    publication_bytes(direct, routes())
                with self.assertRaisesRegex(ValueError, "preview"):
                    record_consent(direct, routes(), approved_at="2026-09-16T16:00:00Z",
                                   explicit_local_command=True)

    def test_preview_rejects_embedded_local_or_private_endpoints_without_echoing(self):
        from omarchy_knowledge.contributions import build_preview, publication_bytes

        preview = build_preview(change(), routes(), destination="ledger", title="Fix dock input",
                                body="Synthetic contribution.", attribution="Submitted by @fixture-account")
        for field in ("title", "body", "attribution"):
            unsafe = "Review http://localhost:8000/private before submitting"
            kwargs = {"title": preview["title"], "body": preview["body"],
                      "attribution": preview["attribution"]}
            kwargs[field] = unsafe
            with self.subTest(field=field), self.assertRaises(ValueError) as caught:
                build_preview(change(), routes(), destination="ledger", **kwargs)
            self.assertNotIn(unsafe, str(caught.exception))
            direct = deepcopy(preview); direct[field] = unsafe
            with self.assertRaises(ValueError) as caught:
                publication_bytes(direct, routes())
            self.assertNotIn(unsafe, str(caught.exception))

    def test_application_audit_only_proposes_investigation(self):
        from omarchy_knowledge.contributions import make_application_receipt, audit_application_receipts

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); changed = root / "input.conf"; changed.write_text("later edits", encoding="utf-8")
            receipt = make_application_receipt(
                change_id=change()["id"], source_revision="fixture-v1",
                changed_files=[{"reference": "{user_config}/hypr/input.conf",
                                "observed_sha256": "0" * 64}],
                rollback="Restore the prior setting.", recorded_at="2026-09-16T16:00:00Z",
                receipt_id="fe3e61da-3b3c-4e4e-9fba-970ef606672f")
            before = changed.read_bytes()
            audit = audit_application_receipts([receipt])
            self.assertEqual(audit["proposals"][0]["action"], "investigate-before-update-or-cleanup")
            self.assertFalse(audit["executed"])
            self.assertEqual(changed.read_bytes(), before)
            forged = dict(receipt); forged["trusted"] = True
            with self.assertRaisesRegex(ValueError, "receipt"):
                audit_application_receipts([forged])


class CliWorkflow(unittest.TestCase):
    def test_module_help_documents_required_commands(self):
        result = subprocess.run([sys.executable, "-m", "omarchy_knowledge", "--help"],
                                cwd=Path(__file__).parents[1], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        for command in ("validate", "index", "search", "show", "explain", "draft", "preview", "status", "audit"):
            self.assertIn(command, result.stdout)

    def test_knowledge_py_remains_a_compatible_entrypoint(self):
        with tempfile.TemporaryDirectory() as temporary:
            record = Path(temporary) / "case.json"; record.write_text(json.dumps(case()), encoding="utf-8")
            result = subprocess.run([sys.executable, "knowledge.py", "validate", str(record)],
                                    cwd=Path(__file__).parents[1], text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout), {"valid": 1})

    def test_compatibility_entrypoint_rejects_record_symlinks(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "case.json"; target.write_text(json.dumps(case()))
            linked = Path(temporary) / "linked.json"; linked.symlink_to(target)
            result = subprocess.run([sys.executable, "knowledge.py", "validate", str(linked)],
                                    cwd=Path(__file__).parents[1], text=True, capture_output=True)
            self.assertNotEqual(result.returncode, 0)

    def test_receipt_command_refuses_to_overwrite_a_symlink(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); target = root / "private.json"; target.write_text("keep")
            linked = root / "receipt.json"; linked.symlink_to(target)
            result = subprocess.run([
                sys.executable, "-m", "omarchy_knowledge", "record-application",
                "--change-id", change()["id"], "--source-revision", "fixture-v1",
                "--changed-file", "{user_config}/hypr/input.conf", "0" * 64,
                "--rollback", "Restore the prior setting.", "--output", str(linked),
            ], cwd=Path(__file__).parents[1], text=True, capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(target.read_text(), "keep")

    def test_clean_isolated_install_exposes_console_and_module_entrypoints(self):
        repository = Path(__file__).parents[1]
        with tempfile.TemporaryDirectory() as temporary:
            environment = Path(temporary) / "venv"
            created = subprocess.run(["/usr/bin/python3", "-m", "venv", str(environment)],
                                     text=True, capture_output=True)
            self.assertEqual(created.returncode, 0, created.stderr)
            installed = subprocess.run([
                str(environment / "bin" / "python"), "-m", "pip", "install",
                "--no-deps", "--no-build-isolation", str(repository),
            ], cwd=temporary, text=True, capture_output=True)
            self.assertEqual(installed.returncode, 0, installed.stdout + installed.stderr)
            metadata = subprocess.run([
                str(environment / "bin" / "python"), "-c",
                "from importlib.metadata import distribution; "
                "d=distribution('omarchy-community-knowledge-tools'); "
                "assert d.version == '0.1.0'; "
                "assert d.requires == ['jsonschema==4.26.0']; "
                "assert any(e.name == 'omarchy-knowledge' and e.value == 'omarchy_knowledge.cli:main' for e in d.entry_points); "
                "assert any(e.name == 'omarchy-knowledge-panel' and e.value == 'omarchy_knowledge.companion:panel_main' for e in d.entry_points); "
                "assert any(str(p) == 'schemas/v1/case.schema.json' for p in d.files); "
                "assert d.locate_file('schemas/v1/case.schema.json').is_file(); "
                "assert d.locate_file('omarchy_knowledge/skills/omarchy-knowledge-research/SKILL.md').is_file(); "
                "assert d.locate_file('omarchy_knowledge/skills/omarchy-knowledge-contribution/SKILL.md').is_file(); "
                "import omarchy_knowledge,sys; assert omarchy_knowledge.__version__ == '0.1.0'; "
                "from pathlib import Path; "
                "assert Path(omarchy_knowledge.__file__).is_relative_to(Path(sys.prefix)); "
                f"assert not any({str(repository)!r} in p for p in sys.path)",
            ], cwd=temporary, text=True, capture_output=True)
            self.assertEqual(metadata.returncode, 0, metadata.stderr)
            for command in ([str(environment / "bin" / "omarchy-knowledge"), "--help"],
                            [str(environment / "bin" / "python"), "-m", "omarchy_knowledge", "--help"],
                            [str(environment / "bin" / "omarchy-knowledge-panel"), "--help"]):
                result = subprocess.run(command, cwd=temporary, text=True, capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("local", result.stdout.lower())
            isolated_home = Path(temporary) / "home"
            isolated_home.mkdir()
            skill_environment = dict(os.environ, HOME=str(isolated_home))
            exposed = subprocess.run([
                str(environment / "bin" / "omarchy-knowledge"), "skills",
                "--agent", "generic", "--install",
            ], cwd=temporary, env=skill_environment, text=True, capture_output=True)
            self.assertEqual(exposed.returncode, 0, exposed.stderr)
            skill_link = isolated_home / ".agents/skills/omarchy-knowledge-research"
            self.assertTrue(skill_link.is_symlink())
            self.assertIn(str(environment / "lib"), os.readlink(skill_link))
            self.assertNotIn(str(repository), os.readlink(skill_link))
            removed = subprocess.run([
                str(environment / "bin" / "omarchy-knowledge"), "skills",
                "--agent", "generic", "--remove",
            ], cwd=temporary, env=skill_environment, text=True, capture_output=True)
            self.assertEqual(removed.returncode, 0, removed.stderr)
            self.assertFalse(skill_link.exists())
            hermes_profile = isolated_home / ".hermes/profiles/coder"
            hermes_profile.mkdir(parents=True)
            profiled = subprocess.run([
                str(environment / "bin" / "omarchy-knowledge"), "skills",
                "--hermes-profile", "coder", "--install",
            ], cwd=temporary, env=skill_environment, text=True, capture_output=True)
            self.assertEqual(profiled.returncode, 0, profiled.stderr)
            profile_link = hermes_profile / "skills/omarchy-knowledge-research"
            self.assertTrue(profile_link.is_symlink())
            self.assertFalse((isolated_home / ".hermes/skills").exists())
            profile_removed = subprocess.run([
                str(environment / "bin" / "omarchy-knowledge"), "skills",
                "--hermes-profile", "coder", "--remove",
            ], cwd=temporary, env=skill_environment, text=True, capture_output=True)
            self.assertEqual(profile_removed.returncode, 0, profile_removed.stderr)
            self.assertFalse(profile_link.exists())

    def test_opt_in_offline_wheelhouse_runs_installed_validate_index_and_search(self):
        wheelhouse_value = os.environ.get("OMARCHY_KNOWLEDGE_TEST_WHEELHOUSE")
        if not wheelhouse_value:
            self.skipTest("set OMARCHY_KNOWLEDGE_TEST_WHEELHOUSE for offline runtime smoke")
        wheelhouse = Path(wheelhouse_value)
        if not wheelhouse.is_dir():
            self.skipTest("configured offline wheelhouse is unavailable")
        repository = Path(__file__).parents[1]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); environment = root / "venv"
            created = subprocess.run(["/usr/bin/python3", "-m", "venv", str(environment)],
                                     text=True, capture_output=True)
            self.assertEqual(created.returncode, 0, created.stderr)
            installed = subprocess.run([
                str(environment / "bin" / "python"), "-m", "pip", "install", "--no-index",
                "--find-links", str(wheelhouse), "--no-build-isolation", str(repository),
            ], cwd=root, text=True, capture_output=True)
            self.assertEqual(installed.returncode, 0, installed.stdout + installed.stderr)
            records = root / "records"; records.mkdir(); write_records(records, [case()])
            executable = str(environment / "bin" / "omarchy-knowledge")
            validated = subprocess.run([executable, "validate", str(records / "0-case.json")],
                                       cwd=root, text=True, capture_output=True)
            self.assertEqual(validated.returncode, 0, validated.stderr)
            self.assertEqual(json.loads(validated.stdout), {"valid": 1})
            indexed = subprocess.run([
                executable, "index", "--source", str(records), "--output", str(root / "snapshot"),
                "--cache", str(root / "cache"), "--data-revision", "fixture-runtime",
                "--toolkit-revision", "726ca9c", "--created-at", "2026-09-16T16:00:00Z",
                "--source-updated-at", "2026-09-16T15:00:00Z", "--source-reference", "fixture",
            ], cwd=root, text=True, capture_output=True)
            self.assertEqual(indexed.returncode, 0, indexed.stderr)
            searched = subprocess.run([
                executable, "search", "--cache", str(root / "cache"), "--query", "keyboard", "--json",
            ], cwd=root, text=True, capture_output=True)
            self.assertEqual(searched.returncode, 0, searched.stderr)
            result = json.loads(searched.stdout)
            self.assertEqual(result["trust"], "attributed-claims-only")
            self.assertEqual(result["results"][0]["case_id"], case()["id"])
            isolated = subprocess.run([
                str(environment / "bin" / "python"), "-I", "-c",
                "import knowledge,projections,sys; "
                "from pathlib import Path; "
                "assert Path(knowledge.__file__).is_relative_to(Path(sys.prefix)); "
                "assert Path(projections.__file__).is_relative_to(Path(sys.prefix)); "
                f"assert not any({str(repository)!r} in p for p in sys.path)",
            ], cwd=root, text=True, capture_output=True)
            self.assertEqual(isolated.returncode, 0, isolated.stderr)

            rendered = subprocess.run([
                str(environment / 'bin' / 'python'), '-I', '-m', 'omarchy_knowledge.deployment',
                '--deployment', 'pilot', '--policy-revision', 'a' * 40,
                '--toolkit-revision', 'b' * 40, '--output', str(root / 'deployment'),
            ], cwd=root, text=True, capture_output=True)
            self.assertEqual(rendered.returncode, 0, rendered.stderr)
            self.assertTrue((root / 'deployment/intake.yml').is_file())
            canonical = subprocess.run([
                str(environment / 'bin' / 'python'), '-I', '-c',
                'import omarchy_knowledge.canonical, omarchy_knowledge.distribution, omarchy_knowledge.service',
            ], cwd=root, text=True, capture_output=True)
            self.assertEqual(canonical.returncode, 0, canonical.stderr)


if __name__ == "__main__":
    unittest.main()
