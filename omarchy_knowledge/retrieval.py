"""Local candidate ranking derived solely from supplied records."""
from collections import Counter
import re
import sqlite3

ALIASES = (("sleep", "suspend"), ("wake", "waking", "resume"),
           ("trackpad", "touchpad"), ("numpad", "keypad", "number pad"),
           ("hotkeys", "shortcuts", "bindings"), ("panel", "bar"),
           ("screen", "monitor", "display"), ("desktop", "workspace"),
           ("browser", "chromium"), ("refresh", "refreshed", "reload"),
           ("win", "super"), ("dock", "docking station"))
STOP = frozenset("a an the to from with while into after before and or is are was were when for of in on at but each separately my i it this that".split())


def _values(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for child in value.values():
            yield from _values(child)
    elif isinstance(value, list):
        for child in value:
            yield from _values(child)


def _tokens(query):
    if not isinstance(query, str) or len(query) > 512:
        raise ValueError("Query must be at most 512 characters")
    lowered = query.casefold()
    for aliases in ALIASES:
        for term in aliases:
            if " " in term:
                lowered = re.sub(r"\b" + re.escape(term) + r"\b", aliases[0], lowered)
    tokens = set(re.findall(r"\w+", lowered, flags=re.UNICODE)) - STOP
    if len(tokens) > 64:
        raise ValueError("Too many search terms")
    return sorted(tokens)


def _terms(token):
    return next((group for group in ALIASES if token in group), (token,))


def _case_text(records):
    cases = {record["id"]: record for record in records if record["type"] == "case"}
    text = {record_id: list(_values(record["payload"])) for record_id, record in cases.items()}
    for record in records:
        case_id = record["payload"].get("case_id")
        if case_id in text and record["type"] in {"change", "report"}:
            text[case_id].extend(_values(record["payload"].get("applicability", record["payload"].get("environment", {}))))
    return cases, text


def rank_cases(records, query, *, intent="all", broad=False):
    """Rank local cases; FTS5 failure is a labeled literal-search fallback."""
    if intent not in {"corrective", "optional", "undetermined", "all"}:
        raise ValueError("Unknown intent selection")
    cases, texts = _case_text(records)
    cases = {record_id: record for record_id, record in cases.items()
             if intent == "all" or record["payload"]["intent"] == intent}
    identifier = query.strip().casefold() if isinstance(query, str) else ""
    for record in records:
        if record["id"] == identifier:
            case_id = record["id"] if record["type"] == "case" else record["payload"].get("case_id")
            return [{"case_id": case_id, "coverage": 1.0, "basis": "exact-record-id"}] if case_id in cases else []
    tokens = _tokens(query)
    if not tokens:
        return [{"case_id": key, "coverage": None, "basis": "browse"} for key in sorted(cases)]
    try:
        db = sqlite3.connect(":memory:")
        try:
            db.execute("CREATE VIRTUAL TABLE docs USING fts5(id UNINDEXED, title, body, identifiers)")
            for record_id, record in cases.items():
                payload = record["payload"]
                body = " ".join(texts[record_id])
                db.execute("INSERT INTO docs VALUES (?,?,?,?)", (record_id, payload["title"], body, body))
            groups = ["(" + " OR ".join('"' + term + '"' for term in _terms(token)) + ")" for token in tokens]
            counts = Counter()
            for group in groups:
                counts.update(row[0] for row in db.execute("SELECT id FROM docs WHERE docs MATCH ?", (group,)))
            threshold = 0 if broad else 0.5
            expression = " OR ".join(groups)
            return [{"case_id": row[0], "coverage": round(counts[row[0]] / len(groups), 3), "basis": "local-fts5"}
                    for row in db.execute("SELECT id FROM docs WHERE docs MATCH ? ORDER BY bm25(docs, 0, 3, 1), id", (expression,))
                    if counts[row[0]] / len(groups) >= threshold]
        finally:
            db.close()
    except sqlite3.OperationalError:
        matches = []
        for record_id, record in cases.items():
            haystack = " ".join(texts[record_id]).casefold()
            matched = sum(any(term in haystack for term in _terms(token)) for token in tokens)
            if matched and (broad or matched / len(tokens) >= 0.5):
                matches.append({"case_id": record_id, "coverage": round(matched / len(tokens), 3),
                                "basis": "case-insensitive-literal-fallback"})
        return sorted(matches, key=lambda item: (-item["coverage"], item["case_id"]))
