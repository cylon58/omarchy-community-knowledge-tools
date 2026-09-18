"""Synthetic complete-record contract tests; records are always inert data."""
import json
import unittest
from copy import deepcopy

CASE_ID = "bd3e61da-3b3c-4e4e-9fba-970ef606672f"
CHANGE_ID = "0d94c6f4-284c-41ec-9d03-754131ca66ea"
CHANGE_TWO_ID = "1d94c6f4-284c-41ec-9d03-754131ca66ea"
REPORT_ID = "aa3e61da-3b3c-4e4e-9fba-970ef606672f"
EVENT_ID = "cc3e61da-3b3c-4e4e-9fba-970ef606672f"
EVENT_TWO_ID = "ec3e61da-3b3c-4e4e-9fba-970ef606672f"
EVENT_THREE_ID = "fc3e61da-3b3c-4e4e-9fba-970ef606672f"
PUBLIC_COMMIT_URL = (
    "https://github.com/example-contributor/omarchy-community-layout-plugin/commit/"
    "0123456789012345678901234567890123456789"
)
HIGH_ENTROPY_VALUE = "A7b9C2d4E6f8G1h3J5k7L9m2N4p6Q8r1S3t5U7v9W2x4"
PERCENT_ENCODED_HIGH_ENTROPY_VALUE = "".join(
    f"%{ord(character):02X}" for character in HIGH_ENTROPY_VALUE
)


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
