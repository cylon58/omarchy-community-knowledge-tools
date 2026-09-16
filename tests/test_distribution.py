"""Canonical distribution through real immutable Git objects; no network."""
import json
from pathlib import Path
import unittest
from tests.test_records import case, change, report

from tests import test_admission
from tests.test_coordinator import FakeAPI


class Distribution(unittest.TestCase):
    setUp = test_admission.TreeAdmission.setUp
    git = test_admission.TreeAdmission.git
    commit = test_admission.TreeAdmission.commit

    def test_default_routes_have_real_owned_destinations_and_require_upstream_selection(self):
        from omarchy_knowledge.routing import routes
        from omarchy_knowledge.contributions import build_preview
        config = routes()
        preview = build_preview(case(), config, destination='ledger', title='Observation',
                                body='A community observation.', attribution='@contributor')
        self.assertEqual(preview['destination']['repository_id'], '1373429914')
        self.assertEqual(config['destinations']['toolkit']['repository_id'], '1373429982')
        for destination in ('upstream', 'plugin'):
            with self.assertRaises(ValueError):
                build_preview(case(), config, destination=destination, title='Observation',
                              body='A community observation.', attribution='@contributor')

    def test_empty_canonical_sync_is_searchable_offline(self):
        from omarchy_knowledge.canonical import sync
        from omarchy_knowledge.coordinator import Policy
        from omarchy_knowledge.discovery import search_snapshot
        from omarchy_knowledge.snapshots import cache_status
        api = FakeAPI(self)
        result = sync(api, Policy('a' * 40, 'b' * 40), self.root / 'cache', now='2026-09-16T16:00:00Z')
        self.assertEqual(result['record_count'], 0)
        self.assertEqual(result['data_revision'], self.base)
        found = search_snapshot(self.root / 'cache', '')
        self.assertEqual(found['results'], [])
        self.assertEqual(found['trust'], 'canonical-api-receipts')
        self.assertEqual(cache_status(self.root / 'cache')['source']['repository_id'], 1373429914)

    def admitted(self):
        from omarchy_knowledge.coordinator import Policy, prepare, publish
        self.head = self.commit({f"records/{r['type']}s/{r['id']}.json": ('100644', json.dumps(r).encode())
                                 for r in (case(), change(), report())}, self.base)
        api = FakeAPI(self)
        policy = Policy('a' * 40, 'b' * 40)
        self.assertEqual(publish(api, policy, prepare(api, policy, 1)).status, 'accepted')
        return api, policy

    def test_admitted_receipts_reach_offline_explain_and_local_import_clears_trust(self):
        from omarchy_knowledge.canonical import sync
        from omarchy_knowledge.discovery import explain_record
        from omarchy_knowledge.snapshots import load_cache, import_snapshot
        api, policy = self.admitted()
        cache = self.root / 'cache'
        result = sync(api, policy, cache)
        self.assertEqual(result['receipt_count'], 3)
        snapshot = load_cache(cache)
        environment = report()['payload']['environment']
        explained = explain_record(cache, change()['id'], environment=environment)
        evidence = explained['projections'][0]['evidence']
        self.assertEqual(evidence['trust_basis'], 'canonical-api-receipts')
        self.assertEqual(evidence['observations'][0]['actor_account_id'], '71')
        import_snapshot(snapshot.path, cache)
        self.assertIsNone(load_cache(cache).canonical)
        self.assertEqual(explain_record(cache, change()['id'], environment=environment)
                         ['projections'][0]['evidence']['trust_basis'], 'no-authenticated-receipts-configured')

    def test_forged_provenance_missing_key_and_mutated_snapshot_fail_closed(self):
        from omarchy_knowledge.canonical import sync
        from omarchy_knowledge.snapshots import load_cache
        api, policy = self.admitted()
        cache = self.root / 'cache'
        sync(api, policy, cache)
        original = (cache / 'CURRENT').read_bytes()
        pointer = json.loads(original)
        pointer['payload']['provenance']['source']['repository_id'] = 1
        (cache / 'CURRENT').write_text(json.dumps(pointer))
        with self.assertRaises(ValueError):
            load_cache(cache)
        (cache / 'CURRENT').write_bytes(original)
        snapshot = load_cache(cache)
        (snapshot.path / 'records.jsonl').write_bytes(b'')
        with self.assertRaises(ValueError):
            load_cache(cache)
        (cache / '.canonical-key').unlink()
        with self.assertRaises(ValueError):
            load_cache(cache)

    def test_empty_local_snapshot_remains_claims_only_and_symlink_key_is_rejected(self):
        from omarchy_knowledge.canonical import sync
        from omarchy_knowledge.coordinator import Policy
        from omarchy_knowledge.snapshots import build_snapshot, import_snapshot, load_cache
        source = self.root / 'empty'; source.mkdir()
        candidate = self.root / 'snapshot'; cache = self.root / 'cache'
        build_snapshot(source, candidate, data_revision='local', toolkit_revision='local',
                       created_at='2026-09-16T16:00:00Z', source_updated_at='2026-09-16T16:00:00Z',
                       source_reference='fixture')
        import_snapshot(candidate, cache)
        self.assertEqual(load_cache(cache).records, ())
        self.assertIsNone(load_cache(cache).canonical)
        foreign = self.root / 'foreign-key'; foreign.write_bytes(b'x' * 32)
        (cache / '.canonical-key').symlink_to(foreign)
        with self.assertRaises(ValueError):
            sync(FakeAPI(self), Policy('a' * 40, 'b' * 40), cache)
        self.assertEqual(foreign.read_bytes(), b'x' * 32)
        self.assertIsNone(load_cache(cache).canonical)

    def test_canonical_paths_modes_and_receipt_type_binding_fail_closed(self):
        from omarchy_knowledge.canonical import read_canonical
        from omarchy_knowledge.coordinator import Policy
        from omarchy_knowledge.github_native import NativeUnavailable
        api = FakeAPI(self); policy = Policy('a' * 40, 'b' * 40)
        for path, mode, raw in [('records', '120000', b'/outside'),
                                ('records/cases/wrong.json', '100644', json.dumps(case()).encode()),
                                (self.path, '100755', json.dumps(case()).encode()),
                                (self.path.replace('/cases/', '/reports/'), '100644', json.dumps(case()).encode()),
                                ('provenance/unknown/data.json', '100644', b'{}')]:
            api.base = self.commit({path: (mode, raw)}, self.base)
            with self.subTest(path=path), self.assertRaises((ValueError, NativeUnavailable)):
                read_canonical(api, policy)
    def test_wrong_repository_receipt_binding_and_record_path_are_rejected(self):
        from omarchy_knowledge.canonical import read_canonical
        from omarchy_knowledge.github_native import NativeUnavailable
        api, policy = self.admitted()
        original_repo = api.repository
        api.repository = lambda: {**original_repo(), 'id': 1}
        with self.assertRaises(NativeUnavailable):
            read_canonical(api, policy)
        api.repository = original_repo
        files = {e.path.decode(): (e.mode, self.reader.blob(e.oid))
                 for e in self.reader.entries(self.reader.commit(api.base)) if e.kind == 'blob'}
        receipt_path = next(p for p in files if p.startswith('provenance/ingestion/'))
        receipt = json.loads(files[receipt_path][1]); receipt['record_sha256'] = '0' * 64
        files[receipt_path] = ('100644', json.dumps(receipt).encode())
        api.base = self.commit(files, api.base)
        with self.assertRaises((ValueError, NativeUnavailable)):
            read_canonical(api, policy)

    def test_static_site_is_bounded_escaped_and_hashes_every_component(self):
        import hashlib
        from omarchy_knowledge.canonical import read_canonical
        from omarchy_knowledge.distribution import build_site
        from omarchy_knowledge.coordinator import Policy
        malicious = case(); malicious['payload']['title'] = '<script>alert("x")</script>'
        api = FakeAPI(self)
        api.base = self.commit({self.path: ('100644', json.dumps(malicious).encode())}, self.base)
        result = build_site(read_canonical(api, Policy('a' * 40, 'b' * 40)), self.root / 'site',
                            status={'status': 'idle', 'outcomes': []})
        site = self.root / 'site'
        html = (site / 'index.html').read_text()
        self.assertNotIn('<script>', html)
        self.assertIn('&lt;script&gt;', html)
        self.assertIn('No Omarchy endorsement', html)
        manifest = json.loads((site / 'distribution.json').read_bytes())
        self.assertEqual(set(manifest['files']), {'index.html', 'manifest.json', 'index.json',
                                                'records.jsonl', 'canonical.json', 'status.json'})
        for name, metadata in manifest['files'].items():
            self.assertEqual(hashlib.sha256((site / name).read_bytes()).hexdigest(), metadata['sha256'])
        self.assertLess(result['bytes'], 32 * 1024 * 1024)
        self.assertEqual(json.loads((site / 'status.json').read_bytes())['receipt_coverage'],
                         {'records': 1, 'receipted_records': 0})

    def test_sync_cli_validates_config_then_calls_only_compiled_api_source(self):
        import contextlib
        import io
        from unittest.mock import patch
        from omarchy_knowledge.cli import main
        from omarchy_knowledge.coordinator import Policy
        from omarchy_knowledge.deployment import render
        api = FakeAPI(self)
        output = self.root / 'deploy'
        config = render(Policy('a' * 40, 'b' * 40), output)
        with patch('omarchy_knowledge.github_native.GitHubRead', return_value=api) as reader, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(['sync', '--config', str(output / 'deployment.json'), '--cache', str(self.root / 'cache')]), 0)
            reader.assert_called_once_with(deployment='production')
        config['repository_id'] = 1
        (output / 'deployment.json').write_text(json.dumps(config))
        with patch('omarchy_knowledge.github_native.GitHubRead') as reader, contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(['sync', '--config', str(output / 'deployment.json'), '--cache', str(self.root / 'cache')]), 1)
            reader.assert_not_called()

    def test_source_staleness_and_upstream_unknown_are_disclosed_offline(self):
        from datetime import datetime, timezone
        from omarchy_knowledge.canonical import sync
        from omarchy_knowledge.coordinator import Policy
        from omarchy_knowledge.snapshots import cache_status
        api = FakeAPI(self)
        cache = self.root / 'cache'
        sync(api, Policy('a' * 40, 'b' * 40), cache, now='2026-09-16T00:00:00Z')
        status = cache_status(cache, now=datetime(2026, 9, 18, tzinfo=timezone.utc))
        self.assertTrue(status['stale'])
        self.assertEqual(status['age_seconds'], 172800)
        self.assertEqual(status['upstream'], {'version': 1, 'status': 'not-refreshed', 'observations': []})


if __name__ == '__main__':
    unittest.main()
