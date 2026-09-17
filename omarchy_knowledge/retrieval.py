"""Dependency-free local candidate ranking, never an applicability decision.

SQLite is a local index derived only from validated records. No downloaded
database, SQL, extensions, model, credentials or network access is involved.
"""
from collections import Counter
import re
import sqlite3


ALIASES = (
    ('sleep', 'suspend'), ('wake', 'waking', 'resume'),
    ('trackpad', 'touchpad'), ('numpad', 'keypad', 'number pad'),
    ('hotkeys', 'shortcuts', 'bindings'), ('panel', 'bar'),
    ('screen', 'monitor', 'display'), ('desktop', 'workspace'),
    ('browser', 'chromium'), ('refresh', 'refreshed', 'reload'),
    ('win', 'super'), ('dock', 'docking station'),
)
STOP = frozenset('a an the to from with while into after before and or is are was were when for of in on at but each separately my i it this that'.split())


def _groups(query):
    lowered = query.casefold()
    for aliases in ALIASES:
        for term in aliases:
            if ' ' in term:
                lowered = re.sub(r'\b' + re.escape(term) + r'\b', aliases[0], lowered)
    tokens = set(re.findall(r'\w+', lowered, flags=re.UNICODE)) - STOP
    if len(tokens) > 64:
        raise ValueError('Too many search terms')
    groups = set()
    for term in tokens:
        aliases = next((group for group in ALIASES if term in group), (term,))
        # Tokens and fixed aliases cannot contain FTS operators or quotes.
        groups.add('(' + ' OR '.join('"' + alias + '"' for alias in aliases) + ')')
    return sorted(groups)


def _values(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for child in value.values():
            yield from _values(child)
    elif isinstance(value, list):
        for child in value:
            yield from _values(child)


def build_index(db, records):
    cases = {r['id']: r for r in records if r['type'] == 'case'}
    extra = {key: [] for key in cases}
    for record in records:
        payload = record['payload']
        key = payload.get('case_id')
        if key in extra:
            if record['type'] == 'change':
                extra[key].extend(_values(payload['applicability']))
            elif record['type'] == 'report':
                extra[key].extend(_values(payload['environment'].get('components', [])))
    # Separate corpora preserve the original intent-filtered BM25 statistics.
    for intent in ('all', 'corrective', 'optional', 'undetermined'):
        table = 'docs_' + intent
        db.execute(f"CREATE VIRTUAL TABLE {table} USING fts5(id UNINDEXED, title, body, identifiers, tokenize='porter unicode61')")
        for key, case in cases.items():
            payload = case['payload']
            if intent != 'all' and payload['intent'] != intent:
                continue
            body = ' '.join((payload['observed'], payload['expectation']['text'], *payload['domains']))
            db.execute(f'INSERT INTO {table} VALUES (?,?,?,?)',
                       (key, payload['title'], body, ' '.join(extra[key])))


def rank_cases(records, query, *, intent='corrective', broad=False, local_index=None):
    """Return ranked IDs and lexical coverage; unknown conditions remain eligible.

    Half the meaningful concept groups must match by default. This is a search
    heuristic, not a calibrated confidence score. Broad mode admits any match.
    """
    if not isinstance(query, str) or len(query) > 512:
        raise ValueError('Query must be at most 512 characters')
    if intent not in {'corrective', 'optional', 'undetermined', 'all'}:
        raise ValueError('Unknown intent selection')
    cases = {r['id']: r for r in records if r['type'] == 'case'
             and (intent == 'all' or r['payload']['intent'] == intent)}
    identifier = query.strip().casefold()
    for record in records:
        if record['id'] == identifier:
            case_id = record['id'] if record['type'] == 'case' else record['payload'].get('case_id')
            return [{'case_id': case_id, 'coverage': 1.0, 'basis': 'exact-record-id'}] if case_id in cases else []
    if not query.strip():
        return [{'case_id': key, 'coverage': None, 'basis': 'browse'} for key in sorted(cases)]
    groups = _groups(query)
    if not groups:
        return []
    db = sqlite3.connect(':memory:')
    try:
        if local_index is None:
            build_index(db, records)
        else:
            db.deserialize(local_index)
            db.execute('PRAGMA query_only=ON')
        table = 'docs_' + intent
        counts = Counter()
        for group in groups:
            counts.update(row[0] for row in db.execute(f'SELECT id FROM {table} WHERE {table} MATCH ?', (group,)))
        threshold = 0 if broad else 0.5
        expression = ' OR '.join(groups)
        return [{'case_id': key, 'coverage': round(counts[key] / len(groups), 3), 'basis': 'local-lexical-candidate'}
                for key, _ in db.execute(f'SELECT id,bm25({table},0,3,1,2) AS rank FROM {table} WHERE {table} MATCH ? ORDER BY rank,id', (expression,))
                if counts[key] / len(groups) >= threshold]
    except sqlite3.OperationalError as exc:
        raise ValueError('Local SQLite FTS5 unavailable; use substring search') from exc
    finally:
        db.close()
