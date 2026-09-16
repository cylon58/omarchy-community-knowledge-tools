"""Offline behavioral tests for applicability and derived projections."""
from copy import deepcopy
from datetime import datetime, timezone
import json
import unittest
from unittest.mock import patch

import knowledge
from projections import (
    INSUFFICIENT_INFORMATION,
    MATCHES,
    DOES_NOT_MATCH,
    match_applicability,
    recommend_action,
    record_digest,
    summarize_evidence,
    validate_ingestion_receipt,
    validate_upstream_observation,
)


CASE_ID = "bd3e61da-3b3c-4e4e-9fba-970ef606672f"
CHANGE_ID = "0d94c6f4-284c-41ec-9d03-754131ca66ea"
EVENT_ID = "cc3e61da-3b3c-4e4e-9fba-970ef606672f"


def environment(*components, architecture="x86_64", channel="stable", edges=(), features=(), settings=(), complete=()):
    return {
        "origin": "firsthand",
        "architecture": architecture,
        "channel": channel,
        "components": list(components),
        "features": list(features),
        "settings": list(settings),
        "complete_component_observations": [
            {"selector": selector, "basis": "direct-enumeration"} for selector in complete
        ],
        "topology": {
            "nodes": [component["alias"] for component in components],
            "edges": list(edges),
        },
    }


def software(alias, component, version=None, scheme=None, name=None):
    selector = {"kind": "software", "component": component}
    if name is not None:
        selector["name"] = name
    result = {"alias": alias, "selector": selector}
    if version is not None:
        result["version"] = version
        result["version_scheme"] = scheme
    return result


def hardware(alias, role, vendor_id=None, product_id=None):
    selector = {"kind": "hardware", "role": role}
    if vendor_id is not None:
        selector["vendor_id"] = vendor_id
    if product_id is not None:
        selector["product_id"] = product_id
    return {"alias": alias, "selector": selector}


def report(identifier, result="success", origin="firsthand", change_id=CHANGE_ID):
    payload = {
        "case_id": CASE_ID,
        "observation_date": "2026-09-16",
        "environment": environment(hardware("host", "host")),
        "test_method": "Exercise the relevant behavior.",
        "baseline": "reproduced",
        "expected_result": "The behavior works.",
        "result": result,
        "actual_result": "Observed result.",
        "limitations": "Synthetic fixture.",
        "provenance": "Synthetic direct observation.",
        "adverse_effects": "None observed.",
        "activation": {"state": "active", "details": "No additional activation."},
        "root_cause": {"assessment": "unknown", "rationale": "Not investigated."},
    }
    payload["environment"]["origin"] = origin
    if change_id is not None:
        payload["change_id"] = change_id
    return {
        "schema_version": 1,
        "id": identifier,
        "type": "report",
        "created_at": "2026-09-16T12:00:00Z",
        "provenance": {"kind": origin},
        "payload": payload,
    }


def receipt(record, account_id):
    return {
        "receipt_version": 1,
        "kind": "ingestion",
        "record_id": record["id"],
        "record_sha256": record_digest(record),
        "actor": {"provider": "github", "account_id": str(account_id)},
        "accepted_at": "2026-09-16T12:10:00Z",
        "policy_revision": "policy-v1",
        "source": {
            "repository_id": "123456",
            "pull_request": 42,
            "merge_commit_oid": {"algorithm": "sha1", "hex": "a" * 40},
            "record_blob_oid": {"algorithm": "sha1", "hex": "b" * 40},
        },
    }


def case(intent="corrective"):
    return {
        "schema_version": 1, "id": CASE_ID, "type": "case",
        "created_at": "2026-09-16T12:00:00Z", "provenance": {"kind": "firsthand"},
        "payload": {
            "title": "Synthetic input issue", "intent": intent, "domains": ["input"],
            "expectation": {"basis": "previously-working", "text": "Input works."},
            "observed": "Input does not work.",
        },
    }


def change(intent="corrective"):
    return {
        "schema_version": 1, "id": CHANGE_ID, "type": "change",
        "created_at": "2026-09-16T12:01:00Z", "provenance": {"kind": "firsthand"},
        "payload": {
            "case_id": CASE_ID, "intent": intent, "method": "workaround",
            "explanation": "Synthetic workaround.",
            "applicability": {"requires": [{
                "kind": "component", "selector": {"kind": "hardware", "role": "host"}, "presence": True,
            }]},
            "procedure": {"kind": "instructions", "steps": ["Inspect before changing anything."]},
            "risk": "low", "effects": "Synthetic effect.", "rollback": "Restore the prior value.",
            "validation_plan": "Retest input.", "requires_root": "no",
            "activation": {"required": "none", "details": "Already active."}, "affected_files": [],
        },
    }


def resolution_event():
    return {
        "schema_version": 1, "id": EVENT_ID, "type": "event",
        "created_at": "2026-09-16T12:02:00Z",
        "provenance": {"kind": "external-source", "sources": ["https://github.com/example/project/pull/1"]},
        "payload": {
            "event_kind": "upstream-resolution",
            "targets": [{"id": CASE_ID, "type": "case"}, {"id": CHANGE_ID, "type": "change"}],
            "reason": "A community member links an upstream change.",
            "resolution": {
                "relevance": "claimed", "upstream_url": "https://github.com/example/project/pull/1",
                "fixed_in": {"any_of": [{"all_of": [{
                    "kind": "software",
                    "selector": {"kind": "software", "component": "omarchy"},
                    "scheme": "arch", "constraints": [{"op": ">=", "version": "4.2.0-1"}],
                    "channel": "stable", "architecture": "x86_64",
                }]}]},
            },
        },
    }


def upstream_observation(event, *, inclusion="included", availability="available",
                         observed_at="2026-09-16T12:10:00Z", fresh_until="2026-09-17T12:10:00Z",
                         migration="no", activation="none"):
    return {
        "observation_version": 1,
        "kind": "upstream-resolution",
        "event_id": event["id"],
        "event_sha256": record_digest(event),
        "repository": {"provider": "github", "repository_id": "123456"},
        "observed_at": observed_at,
        "fresh_until": fresh_until,
        "relevance": {
            "state": "upstream-supported", "basis": "official-linked-context",
            "source_url": "https://github.com/example/project/releases/tag/v4.2.0",
            "source_object_sha256": "c" * 64,
        },
        "source_inclusion": {
            "state": inclusion,
            "commit_oid": {"algorithm": "sha1", "hex": "d" * 40},
        },
        "packages": [{
            "selector": {"kind": "software", "component": "omarchy"},
            "scheme": "arch", "version": "4.2.0-1", "channel": "stable",
            "architecture": "x86_64", "state": availability,
        }],
        "migration": {"required": migration},
        "activation": {"required": activation},
    }


class ApplicabilityProjection(unittest.TestCase):
    def test_version_constraints_must_hold_on_the_same_component(self):
        applicability = {"requires": [{
            "kind": "software",
            "selector": {"kind": "software", "component": "package", "name": "mesa"},
            "scheme": "semver",
            "constraints": [{"op": ">=", "version": "2.0.0"}, {"op": "<", "version": "3.0.0"}],
        }]}
        observed = environment(
            software("mesa-old", "package", "1.5.0", "semver", "mesa"),
            software("mesa-new", "package", "3.5.0", "semver", "mesa"),
        )

        self.assertEqual(match_applicability(applicability, observed).state, DOES_NOT_MATCH)

    def test_topology_aliases_bind_to_observed_component_aliases(self):
        applicability = {"requires": [{
            "kind": "topology",
            "topology": {
                "selectors": [
                    {"alias": "input", "selector": {"kind": "hardware", "role": "keyboard"}},
                    {"alias": "machine", "selector": {"kind": "hardware", "role": "host"}},
                ],
                "required_edges": [{"from": "input", "to": "machine", "relation": "delivers_input_to", "transport": "usb"}],
            },
        }]}
        observed = environment(
            hardware("keyboard-1", "keyboard"),
            hardware("host-1", "host"),
            edges=[{"from": "keyboard-1", "to": "host-1", "relation": "connected_to", "transport": "usb"}],
        )

        self.assertEqual(match_applicability(applicability, observed).state, DOES_NOT_MATCH)
        observed["topology"]["edges"][0]["relation"] = "delivers_input_to"
        self.assertEqual(match_applicability(applicability, observed).state, MATCHES)

    def test_missing_version_and_unsupported_comparator_are_unknown(self):
        selector = {"kind": "software", "component": "kernel"}
        observed = environment(software("kernel", "kernel"))
        missing = {"requires": [{"kind": "software", "selector": selector, "scheme": "arch",
                                  "constraints": [{"op": ">=", "version": "6.10.0-1"}]}]}
        unsupported = {"requires": [{"kind": "software", "selector": selector, "scheme": "upstream",
                                      "constraints": [{"op": ">=", "version": "6.10"}]}]}

        self.assertEqual(match_applicability(missing, observed).state, INSUFFICIENT_INFORMATION)
        versioned = environment(software("kernel", "kernel", "6.11", "upstream"))
        self.assertEqual(match_applicability(unsupported, versioned).state, INSUFFICIENT_INFORMATION)

    def test_unknown_architecture_is_not_treated_as_a_definite_mismatch(self):
        applicability = {"requires": [{
            "kind": "software", "selector": {"kind": "software", "component": "kernel"},
            "scheme": "arch", "constraints": [{"op": ">=", "version": "6.10.0-1"}],
            "architecture": "x86_64",
        }]}
        observed = environment(
            software("kernel", "kernel", "6.11.0-1", "arch"), architecture="unknown"
        )
        self.assertEqual(match_applicability(applicability, observed).state,
                         INSUFFICIENT_INFORMATION)

    def test_missing_fixed_arch_comparator_is_unknown_without_lexical_fallback(self):
        selector = {"kind": "software", "component": "kernel"}
        applicability = {"requires": [{"kind": "software", "selector": selector, "scheme": "arch",
                                        "constraints": [{"op": ">=", "version": "6.10.0-1"}]}]}
        observed = environment(software("kernel", "kernel", "6.11.0-1", "arch"))

        with patch("projections.ARCH_VERCMP", "/definitely/missing/vercmp"):
            self.assertEqual(match_applicability(applicability, observed).state,
                             INSUFFICIENT_INFORMATION)

    def test_absence_requires_explicit_scoped_completeness_evidence(self):
        selector = {"kind": "software", "component": "plugin", "name": "example-plugin"}
        applicability = {"requires": [{"kind": "component", "selector": selector, "presence": False}]}

        self.assertEqual(match_applicability(applicability, environment()).state, INSUFFICIENT_INFORMATION)
        self.assertEqual(match_applicability(applicability, environment(complete=[selector])).state, MATCHES)

    def test_scoped_completeness_evidence_is_part_of_the_validated_environment_contract(self):
        observed = report("ee3e61da-3b3c-4e4e-9fba-970ef606672f")
        selector = {"kind": "software", "component": "plugin", "name": "example-plugin"}
        observed["payload"]["environment"]["complete_component_observations"] = [{
            "selector": selector, "basis": "direct-enumeration",
        }]
        self.assertEqual(knowledge.parse_record(json.dumps(observed)), observed)

    def test_semver_prerelease_precedes_release_and_build_does_not_change_precedence(self):
        selector = {"kind": "software", "component": "package", "name": "demo"}
        applicability = {"requires": [{"kind": "software", "selector": selector, "scheme": "semver",
                                        "constraints": [{"op": "=", "version": "1.0.0+expected"}]}]}

        release = environment(software("demo", "package", "1.0.0+local", "semver", "demo"))
        prerelease = environment(software("demo", "package", "1.0.0-rc.1", "semver", "demo"))
        self.assertEqual(match_applicability(applicability, release).state, MATCHES)
        self.assertEqual(match_applicability(applicability, prerelease).state, DOES_NOT_MATCH)

    def test_requires_any_is_or_of_and_and_excludes_is_or(self):
        applicability = {
            "requires": [{"kind": "feature", "feature": "suspend", "value": True}],
            "requires_any": [
                [{"kind": "setting", "setting": "input-layout", "value": "us"},
                 {"kind": "setting", "setting": "repeat-rate", "value": 30}],
                [{"kind": "setting", "setting": "input-layout", "value": "de"}],
            ],
            "excludes": [{"kind": "feature", "feature": "lid-state", "value": "closed"}],
        }
        observed = environment(
            features=[{"feature": "suspend", "value": True}, {"feature": "lid-state", "value": "open"}],
            settings=[{"setting": "input-layout", "value": "us"}, {"setting": "repeat-rate", "value": 30}],
        )
        self.assertEqual(match_applicability(applicability, observed).state, MATCHES)
        observed["features"][1]["value"] = "closed"
        self.assertEqual(match_applicability(applicability, observed).state, DOES_NOT_MATCH)

    def test_bounded_topology_search_returns_unknown_when_exhausted(self):
        components = [hardware(f"keyboard-{number}", "keyboard") for number in range(3)]
        components += [hardware(f"host-{number}", "host") for number in range(3)]
        applicability = {"requires": [{"kind": "topology", "topology": {
            "selectors": [
                {"alias": "input", "selector": {"kind": "hardware", "role": "keyboard"}},
                {"alias": "machine", "selector": {"kind": "hardware", "role": "host"}},
            ],
            "required_edges": [{"from": "input", "to": "machine", "relation": "connected_to"}],
        }}]}

        result = match_applicability(applicability, environment(*components), max_topology_bindings=4)
        self.assertEqual(result.state, INSUFFICIENT_INFORMATION)
        self.assertIn("bound", " ".join(result.reasons).lower())

    def test_noninjective_topology_assignments_consume_the_search_budget(self):
        components = [hardware(f"device-{number}", "other") for number in range(3)]
        selectors = [
            {"alias": f"role-{number}", "selector": {"kind": "hardware", "role": "other"}}
            for number in range(4)
        ]
        applicability = {"requires": [{"kind": "topology", "topology": {
            "selectors": selectors,
            "required_edges": [{"from": "role-0", "to": "role-1", "relation": "connected_to"}],
        }}]}

        result = match_applicability(applicability, environment(*components), max_topology_bindings=4)
        self.assertEqual(result.state, INSUFFICIENT_INFORMATION)
        self.assertIn("bound", " ".join(result.reasons).lower())

    def test_known_scope_mismatch_overrides_an_unknown_and_dimension(self):
        applicability = {"requires": [{
            "kind": "software", "selector": {"kind": "software", "component": "kernel"},
            "scheme": "arch", "constraints": [{"op": ">=", "version": "6.10.0-1"}],
            "channel": "stable", "architecture": "x86_64",
        }]}
        observed = environment(
            software("kernel", "kernel", "6.11.0-1", "arch"),
            channel="unknown", architecture="aarch64",
        )

        self.assertEqual(match_applicability(applicability, observed).state, DOES_NOT_MATCH)

    def test_requires_any_preserves_branch_reasons_and_missing_facts(self):
        applicability = {"requires": [], "requires_any": [
            [{"kind": "feature", "feature": "suspend", "value": True}],
            [{"kind": "setting", "setting": "num-lock", "value": True}],
        ]}

        result = match_applicability(applicability, environment())
        self.assertEqual(result.state, INSUFFICIENT_INFORMATION)
        self.assertEqual(set(result.missing), {"suspend", "num-lock"})
        self.assertTrue(any("feature" in reason for reason in result.reasons))
        self.assertTrue(any("setting" in reason for reason in result.reasons))


class EvidenceProjection(unittest.TestCase):
    def test_counts_three_authenticated_accounts_and_retains_failure(self):
        reports = [
            report("aa3e61da-3b3c-4e4e-9fba-970ef606672f"),
            report("ab3e61da-3b3c-4e4e-9fba-970ef606672f"),
            report("ac3e61da-3b3c-4e4e-9fba-970ef606672f"),
            report("ad3e61da-3b3c-4e4e-9fba-970ef606672f", "failure"),
        ]

        summary = summarize_evidence(reports, [
            receipt(reports[0], 101), receipt(reports[1], 102),
            receipt(reports[2], 103), receipt(reports[3], 104),
        ], case_id=CASE_ID, change_id=CHANGE_ID)

        self.assertEqual(summary.authenticated_account_counts,
                         {"success": 3, "failure": 1, "partial": 0, "unknown": 0})
        self.assertEqual(summary.trust_basis, "caller-supplied-authenticated-ingestion-receipts")

    def test_repeated_reports_from_one_account_do_not_inflate_account_counts(self):
        reports = [
            report("ba3e61da-3b3c-4e4e-9fba-970ef606672f"),
            report("bb3e61da-3b3c-4e4e-9fba-970ef606672f"),
        ]

        summary = summarize_evidence(
            reports, [receipt(reports[0], 101), receipt(reports[1], 101)],
            case_id=CASE_ID, change_id=CHANGE_ID,
        )

        self.assertEqual(summary.authenticated_account_counts["success"], 1)
        self.assertEqual(summary.authenticated_report_counts["success"], 2)

    def test_imported_reports_are_separate_and_not_independent_tests(self):
        journal = report("ca3e61da-3b3c-4e4e-9fba-970ef606672f", origin="journal-import")
        thread = report("cb3e61da-3b3c-4e4e-9fba-970ef606672f", origin="external-source")

        summary = summarize_evidence(
            [journal, thread], [receipt(journal, 101), receipt(thread, 102)],
            case_id=CASE_ID, change_id=CHANGE_ID,
        )

        self.assertEqual(summary.authenticated_account_counts["success"], 0)
        self.assertEqual(summary.journal_import_counts["success"], 1)
        self.assertEqual(summary.external_source_counts["success"], 1)

    def test_unreceipted_import_remains_an_unattributed_claim(self):
        journal = report("ce3e61da-3b3c-4e4e-9fba-970ef606672f", origin="journal-import")

        summary = summarize_evidence(
            [journal], [], case_id=CASE_ID, change_id=CHANGE_ID
        )

        self.assertEqual(summary.journal_import_counts["success"], 1)
        self.assertEqual(summary.unattributed_claim_counts["success"], 1)

    def test_one_account_is_preserved_once_in_each_distinct_outcome(self):
        reports = [
            report("cf3e61da-3b3c-4e4e-9fba-970ef606672f", "success"),
            report("d03e61da-3b3c-4e4e-9fba-970ef606672f", "failure"),
            report("d13e61da-3b3c-4e4e-9fba-970ef606672f", "partial"),
            report("d23e61da-3b3c-4e4e-9fba-970ef606672f", "inconclusive"),
        ]
        summary = summarize_evidence(
            reports, [receipt(item, 101) for item in reports],
            case_id=CASE_ID, change_id=CHANGE_ID,
        )

        self.assertEqual(summary.authenticated_account_counts,
                         {"success": 1, "failure": 1, "partial": 1, "unknown": 1})

    def test_forged_content_hash_does_not_attribute_the_report(self):
        observed = report("da3e61da-3b3c-4e4e-9fba-970ef606672f")
        forged = receipt(observed, 101)
        forged["record_sha256"] = "0" * 64

        summary = summarize_evidence(
            [observed], [forged], case_id=CASE_ID, change_id=CHANGE_ID
        )

        self.assertEqual(summary.authenticated_account_counts["success"], 0)
        self.assertEqual(summary.unattributed_claim_counts["success"], 1)
        self.assertEqual(summary.rejected_receipt_count, 1)

    def test_receipt_shape_distinguishes_semantic_digest_from_git_blob_oid(self):
        observed = report("ea3e61da-3b3c-4e4e-9fba-970ef606672f")
        valid = receipt(observed, 101)
        self.assertEqual(validate_ingestion_receipt(valid), valid)
        forged_shape = deepcopy(valid)
        forged_shape["record_sha256"] = forged_shape["source"]["record_blob_oid"]
        with self.assertRaises(ValueError):
            validate_ingestion_receipt(forged_shape)

    def test_requires_an_exact_case_and_change_cohort_and_marks_adaptations(self):
        direct = report("fa3e61da-3b3c-4e4e-9fba-970ef606672f")
        adapted = report("fb3e61da-3b3c-4e4e-9fba-970ef606672f")
        adapted["payload"]["intervention_deviations"] = "Used a different setting value."
        unrelated = report("fc3e61da-3b3c-4e4e-9fba-970ef606672f",
                           change_id="1d94c6f4-284c-41ec-9d03-754131ca66ea")
        summary = summarize_evidence(
            [direct, adapted, unrelated],
            [receipt(direct, 101), receipt(adapted, 102), receipt(unrelated, 103)],
            case_id=CASE_ID, change_id=CHANGE_ID,
        )

        self.assertEqual(summary.authenticated_report_counts["success"], 2)
        self.assertEqual(summary.direct_report_counts["success"], 1)
        self.assertEqual(summary.adapted_report_counts["success"], 1)


class RecommendationProjection(unittest.TestCase):
    def setUp(self):
        self.case = case()
        self.change = change()
        self.event = resolution_event()
        self.environment = environment(
            hardware("host", "host"),
            software("omarchy", "omarchy", "4.1.0-1", "arch"),
        )
        self.now = datetime(2026, 9, 16, 13, 0, tzinfo=timezone.utc)

    def recommendation(self, observations, **kwargs):
        return recommend_action(
            self.case, self.change, self.environment, [self.event], observations,
            now=self.now, **kwargs,
        )

    def test_merged_only_pr_does_not_suppress_the_workaround(self):
        result = self.recommendation([upstream_observation(self.event, inclusion="merged")])
        self.assertEqual(result.action, "investigate")
        self.assertEqual(result.workaround, "eligible")

    def test_community_resolution_claim_alone_does_not_suppress_the_workaround(self):
        result = self.recommendation([])
        self.assertEqual(result.action, "investigate")
        self.assertEqual(result.workaround, "eligible")

    def test_unavailable_channel_does_not_recommend_an_update(self):
        result = self.recommendation([upstream_observation(self.event, availability="unavailable")])
        self.assertEqual(result.action, "investigate")
        self.assertEqual(result.workaround, "eligible")

    def test_released_applicable_update_is_preferred_to_a_fresh_workaround(self):
        result = self.recommendation([upstream_observation(self.event)])
        self.assertEqual(result.action, "prefer-update")
        self.assertEqual(result.workaround, "defer-new")

    def test_fixed_version_installed_with_unknown_migration_requires_verification(self):
        self.environment["components"][1]["version"] = "4.2.0-1"
        result = self.recommendation([upstream_observation(self.event, migration="unknown")])
        self.assertEqual(result.action, "verify-migration")
        self.assertNotEqual(result.action, "remove-workaround")

    def test_newer_revert_overrides_an_older_favorable_observation(self):
        older = upstream_observation(self.event, observed_at="2026-09-16T11:00:00Z")
        newer = upstream_observation(self.event, inclusion="reverted", observed_at="2026-09-16T12:00:00Z")
        result = self.recommendation([older, newer])
        self.assertEqual(result.action, "investigate")
        self.assertEqual(result.upstream_state, "reverted")

    def test_partial_inclusion_remains_investigative(self):
        result = self.recommendation([upstream_observation(self.event, inclusion="partial")])
        self.assertEqual(result.action, "investigate")
        self.assertEqual(result.upstream_state, "partial")

    def test_stale_observation_requires_investigation(self):
        stale = upstream_observation(self.event, fresh_until="2026-09-16T12:30:00Z")
        result = self.recommendation([stale])
        self.assertEqual(result.action, "investigate")
        self.assertEqual(result.upstream_state, "stale")

    def test_newer_stale_observation_is_not_replaced_by_older_favorable_state(self):
        older = upstream_observation(self.event, observed_at="2026-09-16T10:00:00Z")
        newer_stale = upstream_observation(
            self.event, observed_at="2026-09-16T12:00:00Z", fresh_until="2026-09-16T12:30:00Z"
        )
        result = self.recommendation([older, newer_stale])
        self.assertEqual(result.action, "investigate")
        self.assertEqual(result.upstream_state, "stale")

    def test_mismatched_event_hash_is_not_trusted(self):
        forged = upstream_observation(self.event)
        forged["event_sha256"] = "0" * 64
        result = self.recommendation([forged])
        self.assertEqual(result.action, "investigate")
        self.assertEqual(result.rejected_observation_count, 1)

    def test_same_time_conflicting_observations_are_unknown(self):
        included = upstream_observation(self.event)
        reverted = upstream_observation(self.event, inclusion="reverted")
        result = self.recommendation([included, reverted])
        self.assertEqual(result.action, "investigate")
        self.assertEqual(result.upstream_state, "conflicting")

    def test_optional_intent_is_only_offered(self):
        self.case["payload"]["intent"] = "optional"
        result = self.recommendation([upstream_observation(self.event)])
        self.assertEqual(result.action, "offer-optional")

    def test_undetermined_intent_never_defaults_to_corrective_action(self):
        self.change["payload"]["intent"] = "undetermined"
        result = self.recommendation([upstream_observation(self.event)])
        self.assertEqual(result.action, "investigate")

    def test_unknown_local_channel_cannot_receive_an_update_recommendation(self):
        self.environment["channel"] = "unknown"
        result = self.recommendation([upstream_observation(self.event)])
        self.assertEqual(result.action, "investigate")

    def test_already_applied_workaround_is_retained_without_cleanup_authority(self):
        result = self.recommendation([], workaround_already_applied=True)
        self.assertEqual(result.action, "investigate")
        self.assertEqual(result.workaround, "retain-existing")
        self.assertTrue(result.cleanup_requires_inspection_and_approval)

    def test_trusted_observation_contract_supports_conditional_relevance_sources(self):
        official = upstream_observation(self.event)
        self.assertEqual(validate_upstream_observation(official), official)
        declaration = deepcopy(official)
        declaration["relevance"] = {
            "state": "upstream-supported", "basis": "authenticated-maintainer-declaration",
            "actor_account_id": "101", "authority_policy_revision": "authority-v1",
            "declaration_sha256": "e" * 64,
        }
        self.assertEqual(validate_upstream_observation(declaration), declaration)
        declaration["relevance"]["source_url"] = "https://example.org/not-allowed-in-this-variant"
        with self.assertRaises(ValueError):
            validate_upstream_observation(declaration)

    def test_conflicting_package_states_for_the_same_coordinates_are_rejected(self):
        observation = upstream_observation(self.event)
        unavailable = deepcopy(observation["packages"][0])
        unavailable["state"] = "unavailable"
        observation["packages"].append(unavailable)

        with self.assertRaises(ValueError):
            validate_upstream_observation(observation)

    def test_package_identity_must_match_fixed_in_selector(self):
        observation = upstream_observation(self.event)
        observation["packages"][0]["selector"] = {
            "kind": "software", "component": "package", "name": "unrelated"
        }
        result = self.recommendation([observation])
        self.assertEqual(result.action, "investigate")

    def test_unknown_distribution_scope_cannot_prove_package_availability(self):
        predicate = self.event["payload"]["resolution"]["fixed_in"]["any_of"][0]["all_of"][0]
        del predicate["channel"]
        del predicate["architecture"]
        self.environment["channel"] = "unknown"
        self.environment["architecture"] = "unknown"
        observation = upstream_observation(self.event)
        observation["packages"][0]["channel"] = "unknown"
        observation["packages"][0]["architecture"] = "unknown"
        observation["event_sha256"] = record_digest(self.event)

        result = self.recommendation([observation])
        self.assertEqual(result.action, "investigate")

    def test_future_observation_cannot_drive_current_authoritative_advice(self):
        future = upstream_observation(
            self.event,
            observed_at="2026-09-16T14:00:00Z",
            fresh_until="2026-09-17T14:00:00Z",
        )

        result = self.recommendation([future])
        self.assertEqual(result.action, "investigate")
        self.assertEqual(result.upstream_state, "future")


if __name__ == "__main__":
    unittest.main()
