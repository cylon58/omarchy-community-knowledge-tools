"""API-authenticated immutable ledger reads; static exports alone confer no trust.

The local seal records that this client verified GitHub's identity/ref/object
boundary. It trusts the local user and cannot defend against code with that
user's filesystem access. It is not a GitHub cryptographic signature.
"""
from datetime import datetime, timezone
import tempfile
from pathlib import Path

import knowledge
from .admission import DIRECTORIES, PATH, Rejected, _bound_json_depth, _paths
from .coordinator import _identity, authenticated_receipts, object_id, require
from .github_native import NativeUnavailable, strict_json
from .snapshots import _import_snapshot, _write_snapshot, _timestamp
from projections import record_digest, validate_ingestion_receipt, validate_upstream_observation


def read_canonical(api, policy, *, now=None):
    """Read main once, then exclusively immutable objects under its trusted identity."""
    try:
        return _read_canonical(api, policy, now=now)
    except (Rejected, KeyError, TypeError, ValueError, RecursionError) as exc:
        raise NativeUnavailable() from exc


def _read_canonical(api, policy, *, now):
    _identity(api, policy)
    revision = object_id(api.branch())
    if hasattr(api.objects, 'warm'):
        api.objects.warm([revision])
    tree = object_id(api.objects.commit(revision))
    info = api.commit_info(revision)
    require(info['oid'] == revision and info['tree'] == tree)
    entries = _paths(api.objects.entries(tree))
    require(len(entries) <= 4096)
    records, observations, identifiers, total = [], [], set(), 0
    for path, entry in sorted(entries.items()):
        if path not in {'records', 'provenance'} and not path.startswith(('records/', 'provenance/')):
            continue
        if entry.kind == 'tree':
            require(path in DIRECTORIES and entry.mode == '040000')
            continue
        match = PATH.fullmatch(path)
        require(match is not None and entry.kind == 'blob' and entry.mode == '100644')
        raw = api.objects.blob(entry.oid)
        total += len(raw)
        require(len(raw) == entry.size and total <= 16 * 1024 * 1024 and match.group(4) not in identifiers)
        identifiers.add(match.group(4))
        _bound_json_depth(raw)
        if path.startswith('records/'):
            record = knowledge.parse_record(raw)
            require(path == f"records/{record['type']}s/{record['id']}.json")
            records.append(record)
        else:
            value = strict_json(raw)
            knowledge._privacy(value)
            if path.startswith('provenance/ingestion/'):
                validate_ingestion_receipt(value)
            else:
                validate_upstream_observation(value)
                observations.append(value)
    records = knowledge.validate_corpus(records)
    by_id = {r['id']: r for r in records}
    for value in observations:
        event = by_id.get(value['event_id'])
        require(event is not None and event['type'] == 'event'
                and event['payload']['event_kind'] == 'upstream-resolution'
                and value['event_sha256'] == record_digest(event))
    # Stored observations are validated inert source data, not live refresh authority.
    stamp = now or datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
    _timestamp(stamp); _timestamp(info['date'])
    receipts = authenticated_receipts(api, policy, revision)
    return {'records': records, 'receipts': receipts, 'source': {
        'repository': policy.repository, 'repository_id': policy.repository_id,
        'deployment': policy.deployment, 'ref': 'refs/heads/main',
        'data_revision': revision, 'tree_revision': tree,
        'toolkit_revision': policy.toolkit_revision, 'policy_revision': policy.policy_revision,
        'verified_at': stamp, 'source_updated_at': info['date'],
    }, 'upstream': {'version': 1, 'status': 'not-refreshed', 'observations': []}}


def snapshot_data(data, output):
    source = data['source']
    return _write_snapshot(data['records'], output, data_revision=source['data_revision'],
                           toolkit_revision=source['toolkit_revision'], created_at=source['verified_at'],
                           source_updated_at=source['source_updated_at'], source_kind='admitted-git-tree',
                           source_reference=source['repository'] + ':' + source['tree_revision'])


def sync(api, policy, cache, *, now=None):
    data = read_canonical(api, policy, now=now)
    with tempfile.TemporaryDirectory(prefix='omarchy-canonical-') as temporary:
        output = Path(temporary) / 'snapshot'
        manifest = snapshot_data(data, output)
        _import_snapshot(output, cache, canonical={k: v for k, v in data.items() if k != 'records'})
    return {**data['source'], 'record_count': manifest['record_count'],
            'receipt_count': len(data['receipts']), 'trust': 'canonical-api-receipts'}
