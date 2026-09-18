"""Synthetic complete-record contract tests; records are always inert data."""
import json
from pathlib import Path
import tempfile
import unittest
from copy import deepcopy
from unittest.mock import patch
import uuid

import omarchy_knowledge.records as knowledge
from omarchy_knowledge.records import parse_record, validate_corpus
from tests.fixtures import *


class RecordContract(unittest.TestCase):
    def test_linked_reports_keep_failed_outcomes(self):
        failed = report()
        failed['payload']['result'] = 'failure'
        failed['payload']['actual_result'] = 'Input remained unavailable.'
        rows = [parse_record(json.dumps(x).encode())
                for x in [case(), change(), failed]]
        self.assertEqual(validate_corpus(rows)[2]['payload']['result'], 'failure')

    def test_unknown_fields_are_rejected(self):
        row = case()
        row['serial_number'] = 'example-sensitive-value'
        with self.assertRaises(ValueError):
            parse_record(json.dumps(row).encode())


class CanonicalRecordLoading(unittest.TestCase):
    def test_load_records_reads_only_canonical_type_uuid_paths(self):
        row = case()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            directory = root / "cases"
            directory.mkdir()
            (directory / f"{row['id']}.json").write_text(json.dumps(row), encoding="utf-8")
            self.assertEqual(knowledge.load_records(root), [row])

    def test_load_records_rejects_unexpected_paths(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "notes.txt").write_text("not a record", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "unexpected"):
                knowledge.load_records(root)

    def test_load_records_rejects_a_directory_swapped_to_a_symlink(self):
        row = case()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "records"
            directory = root / "cases"
            outside = Path(temporary) / "outside"
            directory.mkdir(parents=True)
            outside.mkdir()
            (outside / f"{row['id']}.json").write_text(json.dumps(row), encoding="utf-8")
            original_stat = knowledge.os.stat

            def swap_after_directory_check(path, *args, **kwargs):
                result = original_stat(path, *args, **kwargs)
                if path == "cases" and kwargs.get("dir_fd") is not None:
                    directory.rmdir()
                    directory.symlink_to(outside, target_is_directory=True)
                return result

            with patch.object(knowledge.os, "stat", side_effect=swap_after_directory_check):
                with self.assertRaisesRegex(ValueError, "symlink|directory"):
                    knowledge.load_records(root)

    def test_load_records_rejects_leaf_symlink_size_and_path_id_mismatches(self):
        row = case()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            directory = root / "cases"
            directory.mkdir()
            target = root / "record.json"
            target.write_text(json.dumps(row), encoding="utf-8")
            (directory / f"{row['id']}.json").symlink_to(target)
            with self.assertRaisesRegex(ValueError, "symlink"):
                knowledge.load_records(root)
            (directory / f"{row['id']}.json").unlink()
            (directory / f"{row['id']}.json").write_bytes(b" " * (knowledge.MAX_RECORD_BYTES + 1))
            with self.assertRaisesRegex(ValueError, "64 KiB"):
                knowledge.load_records(root)
            (directory / f"{row['id']}.json").write_text(json.dumps(row), encoding="utf-8")
            other_id = str(uuid.uuid4())
            (directory / f"{other_id}.json").write_text(json.dumps(row), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "align"):
                knowledge.load_records(root)


class EventCycleScaling(unittest.TestCase):
    def test_valid_supersession_chain_does_not_depend_on_recursion_depth(self):
        records = [case()]
        changes = []
        for index in range(1_100):
            record = change()
            record["id"] = str(uuid.UUID(int=index, version=4))
            changes.append(record)
        records.extend(changes)
        for index in range(len(changes) - 1):
            source, target = changes[index], changes[index + 1]
            event_record = {
                "schema_version": 1,
                "id": str(uuid.UUID(int=2_000 + index, version=4)),
                "type": "event",
                "created_at": "2026-09-16T12:04:00Z",
                "provenance": {"kind": "firsthand"},
                "payload": {
                    "event_kind": "supersession",
                    "reason": "The next change replaces the preceding change.",
                    "targets": [
                        {"id": source["id"], "type": "change"},
                        {"id": target["id"], "type": "change"},
                    ],
                    "relation": {
                        "kind": "supersedes",
                        "from": {"id": source["id"], "type": "change"},
                        "to": {"id": target["id"], "type": "change"},
                    },
                },
            }
            records.append(event_record)
        self.assertEqual(validate_corpus(records), records)

class CompleteRecords(unittest.TestCase):
    def test_exposes_complete_record_parser_and_corpus_validator(self):
        self.assertTrue(hasattr(knowledge, "parse_record"))
        self.assertTrue(hasattr(knowledge, "validate_corpus"))

    def test_parses_each_strict_record_type_and_validates_their_links(self):
        records = [case(), change(), report(), event()]
        self.assertEqual(knowledge.parse_record(json.dumps(records[1])), records[1])
        self.assertEqual(knowledge.validate_corpus(records), records)

    def test_rejects_reference_with_a_declared_wrong_type(self):
        records = [case(), change()]
        records[1]["payload"]["case_id"] = CHANGE_ID
        with self.assertRaisesRegex(ValueError, "reference"):
            knowledge.validate_corpus(records)

    def test_rejects_report_that_claims_an_unknown_change(self):
        records = [case(), report()]
        records[1]["payload"]["change_id"] = CHANGE_ID
        with self.assertRaisesRegex(ValueError, "reference"):
            knowledge.validate_corpus(records)

    def test_rejects_sensitive_url_credentials_and_authority_claims(self):
        record = event()
        record["payload"]["resolution"]["upstream_url"] = "https://token@example.org/fix"
        with self.assertRaises(ValueError):
            knowledge.parse_record(json.dumps(record))
        record = event()
        record["payload"]["resolution"]["official"] = True
        with self.assertRaises(ValueError):
            knowledge.parse_record(json.dumps(record))

    def test_rejects_named_secret_in_an_otherwise_public_url(self):
        record = event()
        record["payload"]["resolution"]["upstream_url"] = "https://example.org/fix?api_key=short-secret"
        with self.assertRaisesRegex(ValueError, "sensitive"):
            knowledge.parse_record(json.dumps(record))

    def test_rejects_percent_encoded_url_secret_keys_and_ipv6_literals(self):
        record = event()
        record["payload"]["resolution"]["upstream_url"] = (
            "https://example.org/fix?api%5Fkey=short-value"
        )
        with self.assertRaisesRegex(ValueError, "sensitive"):
            knowledge.parse_record(json.dumps(record))

        record = case()
        record["payload"]["observed"] = "The local endpoint was 2001:db8:85a3::8a2e:370:7334."
        with self.assertRaisesRegex(ValueError, "sensitive"):
            knowledge.parse_record(json.dumps(record))

    def test_accepts_exact_public_github_references_without_joining_path_entropy(self):
        record = case()
        record["provenance"] = {"kind": "external-source", "sources": [
            PUBLIC_COMMIT_URL,
            "https://github.com/example-community/example-plugin-marketplace/issues/7308",
            (
                "https://github.com/example-contributor/example-plugin/compare/"
                "1111111111111111111111111111111111111111..."
                "2222222222222222222222222222222222222222"
            ),
            (
                "https://github.com/example-contributor/example-plugin/blob/"
                "1111111111111111111111111111111111111111/src/plugin"
            ),
            (
                "https://github.com/example-contributor/example-plugin/tree/"
                "1111111111111111111111111111111111111111/src/components"
            ),
        ]}
        self.assertEqual(knowledge.parse_record(json.dumps(record)), record)

    def test_github_reference_paths_do_not_exempt_url_secret_bypasses(self):
        unsafe_sources = {
            "credentials": PUBLIC_COMMIT_URL.replace("github.com", "account@github.com"),
            "private-host": PUBLIC_COMMIT_URL.replace("github.com", "github.internal"),
            "encoded-secret-marker": PUBLIC_COMMIT_URL + "?api%5Fkey=short-value",
            "percent-encoded-entropy": PUBLIC_COMMIT_URL + "?context=" + PERCENT_ENCODED_HIGH_ENTROPY_VALUE,
            "high-entropy-query": PUBLIC_COMMIT_URL + "?context=" + HIGH_ENTROPY_VALUE,
            "high-entropy-fragment": PUBLIC_COMMIT_URL + "#" + HIGH_ENTROPY_VALUE,
            "unexpected-commit-suffix": PUBLIC_COMMIT_URL + "/" + HIGH_ENTROPY_VALUE,
            "high-entropy-blob-path": (
                "https://github.com/example-contributor/example-plugin/blob/"
                "1111111111111111111111111111111111111111/" + HIGH_ENTROPY_VALUE
            ),
            "unknown-host": PUBLIC_COMMIT_URL.replace("github.com", "code.example.org"),
            "lookalike-host": PUBLIC_COMMIT_URL.replace("github.com", "github.com.example.org"),
            "explicit-port": PUBLIC_COMMIT_URL.replace("github.com", "github.com:443"),
        }
        for label, source in unsafe_sources.items():
            with self.subTest(label=label):
                record = case()
                record["provenance"] = {"kind": "external-source", "sources": [source]}
                with self.assertRaises(ValueError):
                    knowledge.parse_record(json.dumps(record))

    def test_github_reference_does_not_exempt_adjacent_prose_or_sensitive_markers(self):
        for label, observed in {
            "high-entropy-prose": PUBLIC_COMMIT_URL + " evidence " + HIGH_ENTROPY_VALUE,
            "forbidden-marker": PUBLIC_COMMIT_URL + " private_key=short-value",
        }.items():
            with self.subTest(label=label):
                record = case()
                record["payload"]["observed"] = observed
                with self.assertRaises(ValueError):
                    knowledge.parse_record(json.dumps(record))

    def test_rejects_non_string_type_as_invalid_input(self):
        record = case()
        record["type"] = {"case": True}
        with self.assertRaises(ValueError):
            knowledge.parse_record(json.dumps(record))

    def test_environment_records_typed_hardware_and_named_package_with_rc_channel(self):
        record = report()
        record["payload"]["environment"]["components"].append({
            "alias": "wlroots", "selector": {"kind": "software", "component": "package", "name": "wlroots"},
            "version": "0.18.2-1", "version_scheme": "arch",
        })
        record["payload"]["environment"]["topology"]["nodes"].append("wlroots")
        self.assertEqual(knowledge.parse_record(json.dumps(record)), record)

    def test_rejects_incoherent_component_and_version_without_scheme(self):
        record = report()
        record["payload"]["environment"]["components"][0]["selector"]["component"] = "kernel"
        with self.assertRaises(ValueError):
            knowledge.parse_record(json.dumps(record))
        record = report()
        record["payload"]["environment"]["components"][0]["version"] = "1.0"
        with self.assertRaises(ValueError):
            knowledge.parse_record(json.dumps(record))

    def test_change_requires_inert_activation_root_and_affected_file_contract(self):
        record = change()
        record["payload"]["procedure"] = {"kind": "artifact-reference", "reference": {
            "artifact_kind": "plugin", "identifier": "hypr-gesture-plugin", "version": "unknown",
            "revision": "unknown", "source_description": "Community-supplied public source.", "url": "https://github.com/example/plugin",
        }}
        self.assertEqual(knowledge.parse_record(json.dumps(record)), record)
        del record["payload"]["requires_root"]
        with self.assertRaises(ValueError):
            knowledge.parse_record(json.dumps(record))

    def test_allows_cross_relation_topology_but_rejects_containment_cycle(self):
        records = [case(), change(), report()]
        environment = records[2]["payload"]["environment"]
        environment["components"].append({"alias": "shell", "selector": {"kind": "software", "component": "shell", "name": "zsh"}})
        environment["topology"] = {"nodes": ["host", "shell"], "edges": [
            {"from": "host", "to": "shell", "relation": "contains"},
            {"from": "shell", "to": "host", "relation": "runs_on"},
        ]}
        self.assertEqual(knowledge.validate_corpus(records), records)
        environment["topology"]["edges"].append({"from": "shell", "to": "host", "relation": "contains"})
        with self.assertRaisesRegex(ValueError, "cycle"):
            knowledge.validate_corpus(records)

    def test_relationship_events_require_direction_and_cannot_cycle_replaced_records(self):
        forward = relation_event(EVENT_TWO_ID, CHANGE_TWO_ID, CHANGE_ID)
        backward = relation_event(EVENT_THREE_ID, CHANGE_ID, CHANGE_TWO_ID)
        with self.assertRaisesRegex(ValueError, "cycle"):
            knowledge.validate_corpus([case(), change(), change_two(), forward, backward])
        unordered = deepcopy(forward)
        del unordered["payload"]["relation"]
        with self.assertRaises(ValueError):
            knowledge.parse_record(json.dumps(unordered))

    def test_untrusted_resolution_can_only_make_claimed_relevance(self):
        record = event()
        record["payload"]["resolution"]["relevance"] = "upstream-supported"
        with self.assertRaises(ValueError):
            knowledge.parse_record(json.dumps(record))

    def test_topology_predicate_binds_its_local_aliases_to_typed_selectors(self):
        record = change()
        record["payload"]["applicability"] = {"requires": [{
            "kind": "topology", "topology": {
                "selectors": [
                    {"alias": "input-device", "selector": {"kind": "hardware", "role": "keyboard", "vendor_id": "1234", "product_id": "abcd"}},
                    {"alias": "machine", "selector": {"kind": "hardware", "role": "host"}},
                ],
                "required_edges": [{"from": "input-device", "to": "machine", "relation": "delivers_input_to", "transport": "usb"}],
            },
        }]}
        self.assertEqual(knowledge.parse_record(json.dumps(record)), record)

    def test_rejects_topology_predicate_edge_outside_its_local_aliases(self):
        record = change()
        record["payload"]["applicability"] = {"requires": [{
            "kind": "topology", "topology": {
                "selectors": [{"alias": "machine", "selector": {"kind": "hardware", "role": "host"}}],
                "required_edges": [{"from": "machine", "to": "unknown", "relation": "connected_to"}],
            },
        }]}
        with self.assertRaisesRegex(ValueError, "alias"):
            knowledge.validate_corpus([case(), record])

    def test_rejects_duplicate_environment_features_and_settings_even_when_values_match(self):
        for field, key in (("features", "feature"), ("settings", "setting")):
            for value in (False, True):
                with self.subTest(field=field, value=value):
                    records = [case(), change(), report()]
                    duplicate = deepcopy(records[2]["payload"]["environment"][field][0])
                    duplicate["value"] = value
                    records[2]["payload"]["environment"][field].append(duplicate)
                    with self.assertRaisesRegex(ValueError, "Duplicate environment"):
                        knowledge.validate_corpus(records)

    def test_rejects_supersession_and_correction_between_different_record_types(self):
        for event_kind, relation_kind in (("supersession", "supersedes"), ("correction", "corrects")):
            with self.subTest(event_kind=event_kind):
                record = relation_event(EVENT_TWO_ID, CHANGE_ID, CASE_ID)
                record["payload"]["event_kind"] = event_kind
                record["payload"]["relation"]["kind"] = relation_kind
                record["payload"]["targets"] = [{"id": CHANGE_ID, "type": "change"}, {"id": CASE_ID, "type": "case"}]
                record["payload"]["relation"]["to"] = {"id": CASE_ID, "type": "case"}
                with self.assertRaisesRegex(ValueError, "compatible"):
                    knowledge.validate_corpus([case(), change(), record])

    def test_rejects_artifact_status_as_a_contributor_authority_claim(self):
        record = change()
        record["payload"]["procedure"] = {"kind": "artifact-reference", "reference": {
            "artifact_kind": "plugin", "identifier": "hypr-gesture-plugin", "version": "unknown",
            "revision": "unknown", "repository_status": "official", "url": "https://github.com/example/plugin",
        }}
        with self.assertRaises(ValueError):
            knowledge.parse_record(json.dumps(record))


if __name__ == "__main__":
    unittest.main()
