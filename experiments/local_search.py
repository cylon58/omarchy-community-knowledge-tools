"""Offline synthetic local-search measurement, not production capacity proof.

Run from the repository root: python -m experiments.local_search --cases 1000
All fixtures live in an automatically removed temporary directory.
"""
import argparse
import hashlib
import json
from pathlib import Path
import platform
import signal
import subprocess
import tempfile
import time
from uuid import UUID

from tests.test_records import case, change, report
from omarchy_knowledge.snapshots import _write_snapshot, import_snapshot, load_cache
from omarchy_knowledge.discovery import search_snapshot


def run(cases):
    if type(cases) is not int or not 1 <= cases <= 1300:
        raise ValueError('Use 1–1300 cases; production bounds remain unchanged')
    records = []
    for i in range(cases):
        c, h, r = case(), change(), report()
        ids = [str(UUID(int=i * 3 + j + 1, version=4)) for j in range(3)]
        c['id'], h['id'], r['id'] = ids
        h['payload']['case_id'] = ids[0]
        r['payload']['case_id'] = ids[0]
        r['payload']['change_id'] = ids[1]
        records.extend((c, h, r))
    with tempfile.TemporaryDirectory(prefix='knowledge-local-scale-') as temporary:
        root = Path(temporary)
        started = time.perf_counter()
        _write_snapshot(records, root / 'snapshot', data_revision='synthetic-scale',
                        toolkit_revision='synthetic-scale', created_at='2026-09-17T15:00:00Z',
                        source_updated_at='2026-09-17T15:00:00Z', source_kind='local-directory',
                        source_reference='synthetic-scale-only')
        import_snapshot(root / 'snapshot', root / 'cache')
        setup = time.perf_counter() - started
        samples = []
        for _ in range(3):
            started = time.perf_counter()
            result = search_snapshot(root / 'cache', 'dock keyboard', method='ranked', compact=True)
            samples.append(round(time.perf_counter() - started, 4))
            if (result['total_matches'] != cases or len(result['results']) != min(cases, 5)
                    or result['trust'] != 'attributed-claims-only'):
                raise AssertionError('Unexpected search counts or trust')
        started = time.perf_counter()
        load_cache(root / 'cache')
        load = time.perf_counter() - started
    checkout = Path(__file__).resolve().parents[1]
    revision = subprocess.check_output(['git', '-C', str(checkout), 'rev-parse', 'HEAD'], text=True).strip()
    fingerprint_files = ['experiments/local_search.py', 'tests/test_records.py',
                         'knowledge.py', 'projections.py', 'omarchy_knowledge/snapshots.py',
                         'omarchy_knowledge/discovery.py', 'omarchy_knowledge/retrieval.py',
                         'omarchy_knowledge/persistent.py']
    return {'cases': cases, 'records': len(records), 'python': platform.python_version(),
            'platform': platform.system(), 'source_revision': revision,
            'source_sha256': {p: hashlib.sha256((checkout / p).read_bytes()).hexdigest()
                              for p in fingerprint_files},
            'setup_seconds': round(setup, 4), 'search_samples_seconds': samples,
            'final_cache_load_seconds': round(load, 4),
            'limitations': 'Identical broad matches; ordinary local import, no canonical receipts, '
                          'no network; not relevance accuracy, hosted throughput or production capacity proof.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cases', type=int, default=100)
    args = parser.parse_args()
    def timeout(*_):
        raise TimeoutError('Whole experiment exceeded 180 seconds')
    signal.signal(signal.SIGALRM, timeout)
    signal.alarm(180)
    try:
        print(json.dumps(run(args.cases), sort_keys=True))
    finally:
        signal.alarm(0)


if __name__ == '__main__':
    main()
