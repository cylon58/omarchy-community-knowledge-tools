#!/usr/bin/env python3
"""Run the local MVP flow with synthetic fixture-only records and trust inputs."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import tempfile

from omarchy_knowledge.admission import TreeCandidateV1, check_tree
from omarchy_knowledge.discovery import search_snapshot
from omarchy_knowledge.git_objects import GitObjectReader
from omarchy_knowledge.snapshots import build_snapshot_from_admitted_tree, import_snapshot, load_snapshot
from projections import record_digest, recommend_action, summarize_evidence


CASE_ID = "a1111111-1111-4111-8111-111111111111"
CHANGE_ID = "b1111111-1111-4111-8111-111111111111"
REPORT_IDS = tuple(f"c{number}111111-1111-4111-8111-111111111111" for number in range(1, 5))
EVENT_ID = "d1111111-1111-4111-8111-111111111111"
OPTIONAL_CASE_ID = "e1111111-1111-4111-8111-111111111111"
OPTIONAL_CHANGE_ID = "f1111111-1111-4111-8111-111111111111"
CREATED = "2026-09-16T12:00:00Z"


def _envelope(identifier, record_type, payload, *, provenance="firsthand"):
    return {"schema_version": 1, "id": identifier, "type": record_type,
            "created_at": CREATED, "provenance": {"kind": provenance}, "payload": payload}


def _topology_environment(*, direct=False, version="4.1.0"):
    components = [
        {"alias": "host", "selector": {"kind": "hardware", "role": "host"}},
        {"alias": "keyboard", "selector": {"kind": "hardware", "role": "keyboard"}},
        {"alias": "omarchy", "selector": {"kind": "software", "component": "omarchy"},
         "version": version, "version_scheme": "semver"},
    ]
    edges = [{"from": "keyboard", "to": "host", "relation": "delivers_input_to", "transport": "usb"}]
    if not direct:
        components.insert(1, {"alias": "dock", "selector": {"kind": "hardware", "role": "dock"}})
        edges = [
            {"from": "keyboard", "to": "dock", "relation": "delivers_input_to", "transport": "usb"},
            {"from": "dock", "to": "host", "relation": "connected_to", "transport": "usb-c"},
        ]
    return {"origin": "firsthand", "architecture": "x86_64", "channel": "stable",
            "components": components, "topology": {
                "nodes": [component["alias"] for component in components], "edges": edges,
            }}


def _records():
    case = _envelope(CASE_ID, "case", {
        "title": "Synthetic dock input recovery", "intent": "corrective",
        "domains": ["input", "dock"],
        "expectation": {"basis": "previously-working", "text": "Synthetic input resumes."},
        "observed": "Synthetic input remains unavailable after resume.",
    })
    topology = {
        "selectors": [
            {"alias": "input", "selector": {"kind": "hardware", "role": "keyboard"}},
            {"alias": "dock", "selector": {"kind": "hardware", "role": "dock"}},
            {"alias": "machine", "selector": {"kind": "hardware", "role": "host"}},
        ],
        "required_edges": [
            {"from": "input", "to": "dock", "relation": "delivers_input_to", "transport": "usb"},
            {"from": "dock", "to": "machine", "relation": "connected_to", "transport": "usb-c"},
        ],
    }
    change = _envelope(CHANGE_ID, "change", {
        "case_id": CASE_ID, "intent": "corrective", "method": "workaround",
        "explanation": "A synthetic recovery used only to exercise local projections.",
        "applicability": {"requires": [{"kind": "topology", "topology": topology}]},
        "procedure": {"kind": "instructions", "steps": ["Inspect the synthetic fixture state."]},
        "risk": "low", "effects": "No real system is involved.",
        "rollback": "Discard the temporary synthetic workspace.",
        "validation_plan": "Evaluate the fixture result without executing record text.",
        "requires_root": "no", "activation": {"required": "none", "details": "Fixture only."},
        "affected_files": [],
    })
    reports = []
    for index, identifier in enumerate(REPORT_IDS):
        success = index < 3
        reports.append(_envelope(identifier, "report", {
            "case_id": CASE_ID, "change_id": CHANGE_ID, "observation_date": "2026-09-16",
            "environment": _topology_environment(direct=not success),
            "test_method": "Run a fully synthetic fixture observation.", "baseline": "reproduced",
            "expected_result": "Synthetic input resumes.", "result": "success" if success else "failure",
            "actual_result": "Synthetic recovery succeeded." if success else "Synthetic direct topology still failed.",
            "limitations": "Fixture-only observation; it is not evidence about a real machine.",
            "provenance": "Synthetic direct-observation-shaped data.",
            "adverse_effects": "None represented.",
            "activation": {"state": "active", "details": "Synthetic fixture state."},
            "root_cause": {"assessment": "unknown", "rationale": "No real cause was investigated."},
        }))
    event = _envelope(EVENT_ID, "event", {
        "event_kind": "upstream-resolution",
        "targets": [{"id": CASE_ID, "type": "case"}, {"id": CHANGE_ID, "type": "change"}],
        "reason": "A synthetic upstream release is linked for projection testing.",
        "supporting_reports": list(REPORT_IDS),
        "resolution": {
            "relevance": "claimed", "upstream_url": "https://github.com/example/project/pull/42",
            "fixed_in": {"any_of": [{"all_of": [{
                "kind": "software", "selector": {"kind": "software", "component": "omarchy"},
                "scheme": "semver", "constraints": [{"op": ">=", "version": "4.2.0"}],
                "channel": "stable", "architecture": "x86_64",
            }]}]},
        },
    }, provenance="external-source")
    event["provenance"]["sources"] = ["https://github.com/example/project/pull/42"]
    optional_case = _envelope(OPTIONAL_CASE_ID, "case", {
        "title": "Synthetic optional pointer preference", "intent": "optional", "domains": ["input"],
        "expectation": {"basis": "user-requested", "text": "Use a preferred synthetic direction."},
        "observed": "The fixture records a personal preference, not a defect.",
    })
    optional_change = _envelope(OPTIONAL_CHANGE_ID, "change", {
        "case_id": OPTIONAL_CASE_ID, "intent": "optional", "method": "configuration",
        "explanation": "Represent an optional preference that default search must exclude.",
        "applicability": {"requires": []},
        "procedure": {"kind": "instructions", "steps": ["Choose only after an explicit user request."]},
        "risk": "low", "effects": "Changes only a synthetic preference.",
        "rollback": "Restore the previous synthetic value.", "validation_plan": "Inspect both directions.",
        "requires_root": "no", "activation": {"required": "none", "details": "Fixture only."},
        "affected_files": ["{user_config}/synthetic-input.conf"],
    })
    return [case, change, *reports, event, optional_case, optional_change]


def _git(repository: Path, *arguments: str) -> str:
    environment = dict(os.environ, GIT_AUTHOR_NAME="Synthetic Fixture",
                       GIT_AUTHOR_EMAIL="fixture@example.invalid",
                       GIT_COMMITTER_NAME="Synthetic Fixture",
                       GIT_COMMITTER_EMAIL="fixture@example.invalid")
    result = subprocess.run(["/usr/bin/git", "-C", str(repository), *arguments],
                            env=environment, check=True, text=True, capture_output=True)
    return result.stdout.strip()


def _receipt(record, account_id):
    return {
        "receipt_version": 1, "kind": "ingestion", "record_id": record["id"],
        "record_sha256": record_digest(record),
        "actor": {"provider": "github", "account_id": str(account_id)},
        "accepted_at": "2026-09-16T12:10:00Z", "policy_revision": "synthetic-policy-v1",
        "source": {"repository_id": "123456", "pull_request": 42,
                   "merge_commit_oid": {"algorithm": "sha1", "hex": "a" * 40},
                   "record_blob_oid": {"algorithm": "sha1", "hex": "b" * 40}},
    }


def _upstream_observation(event, *, inclusion="included", observed_at="2026-09-16T12:10:00Z",
                          fresh_until="2026-09-16T14:10:00Z"):
    return {
        "observation_version": 1, "kind": "upstream-resolution", "event_id": event["id"],
        "event_sha256": record_digest(event),
        "repository": {"provider": "github", "repository_id": "123456"},
        "observed_at": observed_at, "fresh_until": fresh_until,
        "relevance": {"state": "upstream-supported", "basis": "official-linked-context",
                      "source_url": "https://github.com/example/project/releases/tag/v4.2.0",
                      "source_object_sha256": "c" * 64},
        "source_inclusion": {"state": inclusion,
                             "commit_oid": {"algorithm": "sha1", "hex": "d" * 40}},
        "packages": [{"selector": {"kind": "software", "component": "omarchy"},
                      "scheme": "semver", "version": "4.2.0", "channel": "stable",
                      "architecture": "x86_64", "state": "available"}],
        "migration": {"required": "no"}, "activation": {"required": "none"},
    }


def run_demo():
    records = _records()
    by_type = {name: [] for name in ("cases", "changes", "reports", "events")}
    for record in records:
        by_type[record["type"] + "s"].append(record)
    with tempfile.TemporaryDirectory(prefix="omarchy-knowledge-synthetic-") as temporary:
        root = Path(temporary); repository = root / "repository"; repository.mkdir()
        _git(repository, "init", "-q")
        (repository / "README.md").write_text("Synthetic fixture repository.\n", encoding="utf-8")
        _git(repository, "add", "README.md"); _git(repository, "commit", "-q", "-m", "synthetic base")
        base = _git(repository, "rev-parse", "HEAD")
        for directory_name, items in by_type.items():
            directory = repository / "records" / directory_name; directory.mkdir(parents=True)
            for record in items:
                (directory / f"{record['id']}.json").write_text(
                    json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
        _git(repository, "add", "records"); _git(repository, "commit", "-q", "-m", "synthetic candidate")
        head = _git(repository, "rev-parse", "HEAD")
        tree = _git(repository, "rev-parse", "HEAD^{tree}")
        candidate = TreeCandidateV1(123456, "merge-group", base, head, head, tree, "e" * 40, "community")
        initial_admission = check_tree(candidate, GitObjectReader(repository / ".git"))
        if initial_admission["decision"] != "accept":
            raise RuntimeError("synthetic fixture admission failed")
        snapshot_path, cache_path = root / "snapshot", root / "cache"
        changed_checkout_record = deepcopy(next(record for record in records if record["id"] == OPTIONAL_CASE_ID))
        changed_checkout_record["payload"]["title"] = "Mutated valid checkout title after admission candidate creation"
        changed_checkout_path = repository / "records" / "cases" / f"{OPTIONAL_CASE_ID}.json"
        changed_checkout_path.write_text(
            json.dumps(changed_checkout_record, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        built = build_snapshot_from_admitted_tree(
            candidate, GitObjectReader(repository / ".git"), snapshot_path,
            toolkit_revision="local-task6", created_at="2026-09-16T13:00:00Z",
            source_updated_at="2026-09-16T12:30:00Z",
        )
        admission, manifest = built["admission"], built["manifest"]
        admitted_optional = next(
            record for record in load_snapshot(snapshot_path).records if record["id"] == OPTIONAL_CASE_ID
        )
        immutable_tree_binding = (
            admitted_optional["payload"]["title"] != changed_checkout_record["payload"]["title"]
            and manifest["data_revision"] == head
            and manifest["source"]["kind"] == "admitted-git-tree"
            and admission["corpus_digest"] == initial_admission["corpus_digest"]
        )
        import_snapshot(snapshot_path, cache_path)
        unknown_environment = {"origin": "firsthand", "architecture": "x86_64", "channel": "stable",
                               "components": [{"alias": "host", "selector": {"kind": "hardware", "role": "host"}}],
                               "topology": {"nodes": ["host"], "edges": []}}
        default_query = search_snapshot(cache_path, "synthetic", environment=unknown_environment)
        all_query = search_snapshot(cache_path, "synthetic", environment=unknown_environment, intent="all")

        reports = [record for record in records if record["type"] == "report"]
        receipts = [_receipt(record, 1001 + index) for index, record in enumerate(reports)]
        evidence = summarize_evidence(reports, receipts, case_id=CASE_ID, change_id=CHANGE_ID)
        case = next(record for record in records if record["id"] == CASE_ID)
        change = next(record for record in records if record["id"] == CHANGE_ID)
        event = next(record for record in records if record["id"] == EVENT_ID)
        environment = _topology_environment()
        now = datetime(2026, 9, 16, 13, 0, tzinfo=timezone.utc)
        current = recommend_action(case, change, environment, [event], [_upstream_observation(event)], now=now)
        stale = recommend_action(case, change, environment, [event], [
            _upstream_observation(event, fresh_until="2026-09-16T12:30:00Z")], now=now)
        reverted = recommend_action(case, change, environment, [event], [
            _upstream_observation(event, observed_at="2026-09-16T12:20:00Z"),
            _upstream_observation(event, inclusion="reverted", observed_at="2026-09-16T12:30:00Z")], now=now)
        unknown_projection = default_query["results"][0]["changes"][0]["applicability"]["state"]
        return {
            "synthetic_fixture_only": True,
            "admission": {"decision": admission["decision"],
                          "validator_revision": admission["validator_revision"],
                          "evaluated_commit_oid": admission["evaluated_commit_oid"],
                          "immutable_tree_binding": immutable_tree_binding},
            "snapshot": {"record_count": manifest["record_count"], "data_revision": manifest["data_revision"]},
            "query": {"default_intents": sorted({item["intent"] for item in default_query["results"]}),
                      "all_intents": sorted({item["intent"] for item in all_query["results"]}),
                      "unknown_topology": unknown_projection},
            "evidence": asdict(evidence),
            "upstream": {"matching_channel": asdict(current), "stale": asdict(stale),
                         "reverted": asdict(reverted)},
            "disclaimer": "Synthetic fixture-only trust inputs are not real evidence or deployment authority.",
        }


if __name__ == "__main__":
    print(json.dumps(run_demo(), sort_keys=True, indent=2))
