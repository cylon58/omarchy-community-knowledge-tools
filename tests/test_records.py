"""Synthetic complete-record contract tests; records are always inert data."""
import json
import unittest
from copy import deepcopy

import knowledge


CASE_ID = "bd3e61da-3b3c-4e4e-9fba-970ef606672f"
CHANGE_ID = "0d94c6f4-284c-41ec-9d03-754131ca66ea"
CHANGE_TWO_ID = "1d94c6f4-284c-41ec-9d03-754131ca66ea"
REPORT_ID = "aa3e61da-3b3c-4e4e-9fba-970ef606672f"
EVENT_ID = "cc3e61da-3b3c-4e4e-9fba-970ef606672f"
EVENT_TWO_ID = "ec3e61da-3b3c-4e4e-9fba-970ef606672f"
EVENT_THREE_ID = "fc3e61da-3b3c-4e4e-9fba-970ef606672f"


def case(identifier=CASE_ID):
    return {
        "schema_version": 1, "id": identifier, "type": "case",
        "created_at": "2026-09-16T12:00:00Z",
        "provenance": {"kind": "firsthand"},
        "payload": {
            "title": "Dock keyboard stops after suspend", "intent": "corrective",
            "domains": ["input", "dock"],
            "expectation": {"basis": "previously-working", "text": "Input resumes"},
            "observed": "Input stops after resume",
        },
    }


def change():
    return {
        "schema_version": 1, "id": CHANGE_ID, "type": "change",
        "created_at": "2026-09-16T12:01:00Z",
        "provenance": {"kind": "firsthand"},
        "payload": {
            "case_id": CASE_ID, "intent": "corrective", "method": "configuration",
            "explanation": "Restore the compositor input setting.",
            "applicability": {"requires": [{"kind": "component", "selector": {"kind": "software", "component": "kernel"}, "presence": True}]},
            "procedure": {"kind": "instructions", "steps": ["Set the documented option."]},
            "risk": "low", "effects": "May alter key repeat.",
            "rollback": "Restore the prior setting.", "validation_plan": "Suspend and test input.",
            "requires_root": "unknown", "activation": {"required": "relogin", "details": "Reload the compositor."},
            "affected_files": ["{user_config}/hypr/input.conf"],
        },
    }


def report():
    return {
        "schema_version": 1, "id": REPORT_ID, "type": "report",
        "created_at": "2026-09-16T12:02:00Z",
        "provenance": {"kind": "firsthand"},
        "payload": {
            "case_id": CASE_ID, "change_id": CHANGE_ID,
            "observation_date": "2026-09-16", "environment": {
                "origin": "firsthand", "architecture": "x86_64", "channel": "rc",
                "features": [{"feature": "suspend", "value": True}], "settings": [{"setting": "num-lock", "value": False}],
                "components": [{"alias": "host", "selector": {"kind": "hardware", "role": "host", "vendor_id": "1234", "product_id": "abcd"}}],
                "topology": {"nodes": ["host"], "edges": []},
            },
            "test_method": "Suspend, resume, then type.", "baseline": "reproduced", "expected_result": "Input resumes.",
            "result": "success", "actual_result": "Input resumed.", "limitations": "One repeat only.",
            "provenance": "direct observation", "adverse_effects": "none observed",
            "activation": {"state": "active", "details": "Setting loaded after resume."},
            "root_cause": {"assessment": "unknown", "rationale": "Not isolated."},
        },
    }


def change_two():
    record = change()
    record["id"] = CHANGE_TWO_ID
    return record


def relation_event(identifier, source_id, target_id):
    return {
        "schema_version": 1, "id": identifier, "type": "event",
        "created_at": "2026-09-16T12:04:00Z", "provenance": {"kind": "firsthand"},
        "payload": {
            "event_kind": "supersession", "reason": "The newer intervention replaces the prior one.",
            "targets": [{"id": source_id, "type": "change"}, {"id": target_id, "type": "change"}],
            "relation": {"kind": "supersedes", "from": {"id": source_id, "type": "change"}, "to": {"id": target_id, "type": "change"}},
        },
    }


def event():
    return {
        "schema_version": 1, "id": EVENT_ID, "type": "event",
        "created_at": "2026-09-16T12:03:00Z",
        "provenance": {"kind": "external-source", "sources": ["https://github.com/omacom/omarchy/pull/1"]},
        "payload": {
            "event_kind": "upstream-resolution",
            "targets": [{"id": CASE_ID, "type": "case"}, {"id": CHANGE_ID, "type": "change"}],
            "reason": "A public upstream change may address this case.",
            "supporting_reports": [REPORT_ID],
            "resolution": {
                "relevance": "claimed", "upstream_url": "https://github.com/omacom/omarchy/pull/1",
                "fixed_in": {"any_of": [{"all_of": [{"kind": "software", "selector": {"kind": "software", "component": "omarchy"}, "scheme": "arch", "constraints": [{"op": ">=", "version": "4.1.2-1"}]}]}]},
            },
        },
    }


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
