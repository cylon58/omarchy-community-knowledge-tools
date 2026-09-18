"""Offline presentation of local community evidence, never an update decision."""
from copy import deepcopy

from .content_flags import dangerous_content_flags
from .contributions import is_official_release_url
from .retrieval import rank_cases
from .records import validate_corpus


def search(records, query, *, intent="all", limit=5):
    """Return locally ranked case records, labeled with the search method."""
    if not isinstance(limit, int) or limit < 1:
        raise ValueError("Search limit must be positive")
    rows = validate_corpus(records)
    by_id = {row["id"]: row for row in rows}
    result = []
    for candidate in rank_cases(rows, query, intent=intent):
        row = deepcopy(by_id[candidate["case_id"]])
        row["search_method"] = candidate["basis"]
        row["coverage"] = candidate["coverage"]
        # The shortlist is only case rows, so surface adverse linked evidence here.
        row["evidence_flags"] = related(rows, candidate["case_id"])["flags"]
        result.append(row)
    return result[:limit]


def show(records, record_id):
    """Return one validated inert record by exact ID."""
    for row in validate_corpus(records):
        if row["id"] == record_id:
            return deepcopy(row)
    raise ValueError("Unknown record ID")


def related(records, case_id):
    """Collect all case evidence by IDs, including events aimed at related events."""
    rows = validate_corpus(records)
    by_id = {row["id"]: row for row in rows}
    case = by_id.get(case_id)
    if not case or case["type"] != "case":
        raise ValueError("Unknown case ID")
    included = {case_id}
    for row in rows:
        if row["type"] in {"change", "report"} and row["payload"].get("case_id") == case_id:
            included.add(row["id"])
    # Each pass adds a previously unseen event; bounded by the validated corpus.
    for _ in range(len(rows)):
        additions = {row["id"] for row in rows if row["type"] == "event"
                     and any(target["id"] in included for target in row["payload"]["targets"])} - included
        if not additions:
            break
        included.update(additions)
    selected = [row for row in rows if row["id"] in included]
    flags = set()
    for row in selected:
        flags.update(dangerous_content_flags(row))
        if row["type"] == "report" and row["payload"]["result"] in {"failure", "inconclusive"}:
            flags.add("NEGATIVE_REPORT_RESULT")
        if row["type"] == "event" and row["payload"]["event_kind"] in {"correction", "withdrawal", "dispute"}:
            flags.add("ADVERSE_EVENT:" + row["payload"]["event_kind"])
    return {"case": deepcopy(case), "changes": [deepcopy(row) for row in selected if row["type"] == "change"],
            "reports": [deepcopy(row) for row in selected if row["type"] == "report"],
            "events": [deepcopy(row) for row in selected if row["type"] == "event"], "flags": sorted(flags)}


def release_claims(records, case_id):
    """Expose upstream-resolution statements as claims requiring an official check."""
    detail = related(records, case_id)
    claims = []
    for event in detail["events"]:
        payload = event["payload"]
        if payload["event_kind"] != "upstream-resolution":
            continue
        resolution = payload["resolution"]
        links = list(payload.get("supporting_links", []))
        upstream_url = resolution["upstream_url"]
        flags = []
        if not any(is_official_release_url(link, upstream_url) for link in links):
            flags.append("INCOMPLETE_DIRECT_RELEASE_EVIDENCE")
        claims.append({"event_id": event["id"], "claim_status": "community-claim", "links": links,
                       "upstream_url": upstream_url, "fixed_in": deepcopy(resolution["fixed_in"]),
                       "needs_official_check": True, "flags": flags})
    return claims
