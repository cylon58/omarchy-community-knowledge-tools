"""Stable structured search/show/explain projections over a local snapshot."""
from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping

from projections import match_applicability, recommend_action, summarize_evidence
from .snapshots import load_cache, snapshot_status


_INTENTS = {"corrective", "optional", "undetermined", "all"}


def _corpus(cache: str | Path):
    snapshot = load_cache(cache)
    records = list(snapshot.records)
    return snapshot, records, {record["id"]: record for record in records}


def _projection(case, change, records, environment, canonical=None):
    result = match_applicability(change["payload"]["applicability"], environment)
    evidence = asdict(summarize_evidence(
        records, canonical['receipts'] if canonical else [], case_id=case["id"], change_id=change["id"]
    ))
    evidence["trust_basis"] = "canonical-api-receipts" if canonical else "no-authenticated-receipts-configured"
    recommendation = asdict(recommend_action(
        case, change, environment,
        [record for record in records if record["type"] == "event"], [],
    ))
    recommendation["trust_basis"] = "no-authenticated-upstream-observations-configured"
    return {
        "change_id": change["id"], "intent": change["payload"]["intent"],
        "predicates": change["payload"]["applicability"],
        "applicability": asdict(result), "evidence": evidence,
        "recommendation": recommendation,
    }


def search_snapshot(cache: str | Path, query: str, *, environment: Mapping[str, Any] | None = None,
                    intent: str = "corrective") -> dict[str, Any]:
    if intent not in _INTENTS:
        raise ValueError("Unknown intent selection")
    snapshot, records, _ = _corpus(cache)
    terms = query.casefold().split()
    cases = [record for record in records if record["type"] == "case"]
    changes = [record for record in records if record["type"] == "change"]
    results = []
    for case in cases:
        payload = case["payload"]
        if intent != "all" and payload["intent"] != intent:
            continue
        search_text = " ".join((payload["title"], payload["observed"],
                                payload["expectation"]["text"], *payload["domains"])).casefold()
        if not all(term in search_text for term in terms):
            continue
        projected, incompatible = [], []
        for change in changes:
            if change["payload"]["case_id"] != case["id"]:
                continue
            if environment is None:
                projected.append({
                    "change_id": change["id"], "intent": change["payload"]["intent"],
                    "predicates": change["payload"]["applicability"],
                    "applicability": {"state": "not-evaluated", "reasons": [],
                                      "missing": ["local environment"]},
                    "evidence": {**asdict(summarize_evidence(records, snapshot.canonical['receipts'] if snapshot.canonical else [],
                                                            case_id=case['id'], change_id=change['id'])),
                                 "trust_basis": "canonical-api-receipts" if snapshot.canonical else "no-authenticated-receipts-configured"},
                    "recommendation": {"action": "investigate", "trust_basis":
                                       "no-authenticated-upstream-observations-configured"},
                })
                continue
            item = _projection(case, change, records, environment, snapshot.canonical)
            if item["applicability"]["state"] == "does-not-match":
                incompatible.append(change["id"])
            else:
                projected.append(item)
        rank = {"matches": 0, "insufficient-information": 1, "not-evaluated": 2}
        projected.sort(key=lambda item: (rank[item["applicability"]["state"]], item["change_id"]))
        results.append({"case_id": case["id"], "title": payload["title"],
                        "intent": payload["intent"], "domains": payload["domains"],
                        "changes": projected, "incompatible_change_ids": sorted(incompatible)})
    return {"query": query, "intent": intent, "data_revision": snapshot.manifest["data_revision"],
            "cache_status": snapshot_status(snapshot),
            "results": sorted(results, key=lambda item: item["case_id"]),
            "trust": "canonical-api-receipts" if snapshot.canonical else "attributed-claims-only"}


def show_record(cache: str | Path, identifier: str) -> dict[str, Any]:
    snapshot, _, by_id = _corpus(cache)
    if identifier not in by_id:
        raise ValueError("Unknown canonical record ID")
    return {"data_revision": snapshot.manifest["data_revision"], "record": by_id[identifier],
            "cache_status": snapshot_status(snapshot),
            "trust": "canonical-api-receipts" if snapshot.canonical else "attributed-claims-only"}


def explain_record(cache: str | Path, identifier: str, *, environment: Mapping[str, Any]) -> dict[str, Any]:
    snapshot, records, by_id = _corpus(cache)
    target = by_id.get(identifier)
    if target is None:
        raise ValueError("Unknown canonical record ID")
    if target["type"] == "case":
        changes = [item for item in records if item["type"] == "change"
                   and item["payload"]["case_id"] == identifier]
        projections = [_projection(target, item, records, environment, snapshot.canonical) for item in changes]
    elif target["type"] == "change":
        case = by_id[target["payload"]["case_id"]]
        projections = [_projection(case, target, records, environment, snapshot.canonical)]
    else:
        projections = []
    return {"record_id": identifier, "record_type": target["type"],
            "cache_status": snapshot_status(snapshot),
            "data_revision": snapshot.manifest["data_revision"], "projections": projections,
            "trust": "canonical-api-receipts" if snapshot.canonical else "attributed-claims-only"}
