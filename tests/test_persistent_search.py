"""Local derivations never replace snapshot integrity or provenance checks."""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import os
import sqlite3
import json
from importlib.metadata import PackageNotFoundError
import tempfile
import unittest
from unittest import mock

from test_records import case, change, report, change_two, relation_event, EVENT_ID
from test_local_workflow import write_records
from omarchy_knowledge import snapshots, discovery
from omarchy_knowledge import persistent, retrieval


class PersistentSearch(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.cache = self.root / 'cache'
        source = self.root / 'source'; source.mkdir()
        other = case('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa')
        write_records(source, [case(), change(), report(), other])
        snapshots.build_snapshot(source, self.root / 'snapshot', data_revision='fixture',
            toolkit_revision='fixture', created_at='2026-09-16T16:00:00Z',
            source_updated_at='2026-09-16T16:00:00Z', source_reference='fixture')
        snapshots.import_snapshot(self.root / 'snapshot', self.cache)

    def test_warm_reuse_skips_corpus_validation_but_preserves_claims_only(self):
        cold = snapshots.load_cache(self.cache)
        with mock.patch.object(snapshots, 'validate_corpus', side_effect=AssertionError('revalidated')):
            warm = snapshots.load_cache(self.cache)
        self.assertEqual(warm.records, cold.records)
        self.assertIsNone(warm.canonical)

    def test_compact_selects_before_projecting_and_preserves_total(self):
        original = discovery.summarize_evidence
        seen = []
        def count(*args, **kwargs):
            seen.append(kwargs['case_id'])
            return original(*args, **kwargs)
        with mock.patch.object(discovery, 'summarize_evidence', side_effect=count):
            short = discovery.search_snapshot(self.cache, 'dock', compact=True, limit=1)
        self.assertEqual(short['total_matches'], 2)
        self.assertTrue(short['truncated'])
        self.assertEqual(set(seen), {'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'})

    def test_warm_ranked_search_does_not_rebuild_fts(self):
        cold = discovery.search_snapshot(self.cache, 'dock', method='ranked', compact=True)
        with mock.patch.object(retrieval, 'build_index', side_effect=AssertionError('rebuilt')):
            warm = discovery.search_snapshot(self.cache, 'dock', method='ranked', compact=True)
        self.assertEqual(warm, cold)

    def test_source_tamper_with_preserved_mtime_fails_closed(self):
        cold = snapshots.load_cache(self.cache)
        path = cold.path / 'records.jsonl'
        info = path.stat()
        path.write_bytes(path.read_bytes().replace(b'dock', b'DOCK'))
        os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns))
        with self.assertRaisesRegex(ValueError, 'integrity mismatch'):
            snapshots.load_cache(self.cache)

    def test_tampered_and_truncated_images_revalidate_and_rebuild(self):
        expected = snapshots.load_cache(self.cache).records
        path = self.cache / '.derived-index'
        for raw in (b'not sqlite', path.read_bytes()[:-1]):
            path.write_bytes(raw)
            with mock.patch.object(snapshots, 'validate_corpus', wraps=snapshots.validate_corpus) as validate:
                result = snapshots.load_cache(self.cache)
            self.assertEqual(result.records, expected)
            self.assertEqual(validate.call_count, 1)
            self.assertNotEqual(path.read_bytes(), raw)

    def test_code_fingerprint_change_invalidates_validation_receipt(self):
        snapshots.load_cache(self.cache)
        with mock.patch.object(persistent, 'fingerprint', return_value=b'new validator schema version'):
            with mock.patch.object(snapshots, 'validate_corpus', wraps=snapshots.validate_corpus) as validate:
                result = snapshots.load_cache(self.cache)
        self.assertEqual(validate.call_count, 1)
        self.assertEqual(len(result.records), 4)

    def test_revision_change_cannot_reuse_old_receipt(self):
        snapshots.load_cache(self.cache)
        snapshots.build_snapshot(self.root / 'source', self.root / 'next', data_revision='next',
            toolkit_revision='fixture', created_at='2026-09-16T16:00:00Z',
            source_updated_at='2026-09-16T16:00:00Z', source_reference='fixture')
        snapshots.import_snapshot(self.root / 'next', self.cache)
        with mock.patch.object(snapshots, 'validate_corpus', wraps=snapshots.validate_corpus) as validate:
            result = snapshots.load_cache(self.cache)
        self.assertEqual(validate.call_count, 1)
        self.assertEqual(result.manifest['data_revision'], 'next')

    def test_symlink_and_hardlink_keys_are_untouched_and_fall_back(self):
        target = self.root / 'target'; target.write_bytes(b'X' * 32); target.chmod(0o600)
        key = self.cache / '.derived-key'
        for link in ('symlink', 'hardlink'):
            if link == 'symlink':
                key.symlink_to(target)
            else:
                os.link(target, key)
            result = snapshots.load_cache(self.cache)
            self.assertEqual(len(result.records), 4)
            self.assertIsNone(result.local_index)
            self.assertEqual(target.read_bytes(), b'X' * 32)
            key.unlink()

    def test_symlink_image_is_not_written_through(self):
        target = self.root / 'target'; target.write_bytes(b'keep')
        (self.cache / '.derived-index').symlink_to(target)
        self.assertEqual(len(snapshots.load_cache(self.cache).records), 4)
        self.assertEqual(target.read_bytes(), b'keep')

    def test_read_only_storage_does_not_break_search(self):
        with mock.patch.object(persistent, '_write_regular_at', side_effect=PermissionError('read only')):
            result = discovery.search_snapshot(self.cache, 'dock', compact=True, method='ranked')
        self.assertEqual(result['total_matches'], 2)
        self.assertFalse((self.cache / '.derived-index').exists())

    def test_interrupted_publication_keeps_previous_image(self):
        snapshots.load_cache(self.cache)
        image = (self.cache / '.derived-index').read_bytes()
        with mock.patch.object(persistent, 'fingerprint', return_value=b'next version'):
            with mock.patch.object(persistent.os, 'replace', side_effect=OSError('interrupted')):
                self.assertEqual(len(snapshots.load_cache(self.cache).records), 4)
        self.assertEqual((self.cache / '.derived-index').read_bytes(), image)
        self.assertEqual(sorted(p.name for p in self.cache.glob('.derived-*')), ['.derived-index', '.derived-key'])

    def test_concurrent_builders_publish_complete_reusable_image(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            records = list(pool.map(lambda _: snapshots.load_cache(self.cache).records, range(8)))
        self.assertTrue(all(records[0] == value for value in records))
        with mock.patch.object(snapshots, 'validate_corpus', side_effect=AssertionError('revalidated')):
            self.assertEqual(snapshots.load_cache(self.cache).records, records[0])

    def test_no_fts_keeps_substring_and_explicit_ranked_error(self):
        with mock.patch.object(retrieval, 'build_index', side_effect=sqlite3.OperationalError('no FTS5')):
            result = discovery.search_snapshot(self.cache, 'dock', compact=True)
            self.assertEqual(result['total_matches'], 2)
            with self.assertRaisesRegex(ValueError, 'FTS5 unavailable'):
                discovery.search_snapshot(self.cache, 'dock', method='ranked')

    def test_missing_serialization_support_uses_transient_ranking(self):
        with mock.patch.object(persistent, 'supported', return_value=False, create=True):
            result = discovery.search_snapshot(self.cache, 'dock', compact=True, method='ranked')
        self.assertEqual(result['total_matches'], 2)
        self.assertFalse((self.cache / '.derived-index').exists())

    def test_warm_provenance_comes_from_fresh_pointer_and_import_downgrades(self):
        candidate = self.root / 'snapshot'
        snapshots._import_snapshot(candidate, self.cache, canonical={'marker': 'first'})
        first = snapshots.load_cache(self.cache)
        self.assertEqual(first.canonical, {'marker': 'first'})
        snapshots._import_snapshot(candidate, self.cache, canonical={'marker': 'second'})
        with mock.patch.object(snapshots, 'validate_corpus', side_effect=AssertionError('revalidated')):
            self.assertEqual(snapshots.load_cache(self.cache).canonical, {'marker': 'second'})
        original = (self.cache / 'CURRENT').read_bytes()
        envelope = json.loads(original)
        envelope['payload']['provenance'] = {'marker': 'forged'}
        (self.cache / 'CURRENT').write_text(json.dumps(envelope))
        with self.assertRaisesRegex(ValueError, 'canonical seal'):
            snapshots.load_cache(self.cache)
        (self.cache / 'CURRENT').write_bytes(original)
        snapshots.import_snapshot(candidate, self.cache)
        with mock.patch.object(snapshots, 'validate_corpus', side_effect=AssertionError('revalidated')):
            self.assertIsNone(snapshots.load_cache(self.cache).canonical)

    def test_shortlist_retains_selected_case_full_safety_cohort(self):
        failed = report(); failed['payload']['result'] = 'failure'
        newer = change_two()
        supersession = relation_event(EVENT_ID, newer['id'], change()['id'])
        extra = case('ffffffff-ffff-4fff-8fff-ffffffffffff')
        selected = case(); selected['payload']['title'] = 'Dock dock dock dock'
        extra['payload']['title'] = 'Unrelated symptom'
        extra['payload']['observed'] = 'dock ' + 'unrelated ' * 100
        records = [selected, change(), newer, failed, supersession, extra]
        source = self.root / 'cohort'; source.mkdir(); write_records(source, records)
        snapshots.build_snapshot(source, self.root / 'cohort-snapshot', data_revision='cohort',
            toolkit_revision='fixture', created_at='2026-09-16T16:00:00Z',
            source_updated_at='2026-09-16T16:00:00Z', source_reference='fixture')
        snapshots.import_snapshot(self.root / 'cohort-snapshot', self.cache)
        env = failed['payload']['environment']
        for method in ('substring', 'ranked'):
            full = discovery.search_snapshot(self.cache, 'dock', method=method, environment=env)
            short = discovery.search_snapshot(self.cache, 'dock', method=method, environment=env,
                                              compact=True, limit=1)
            self.assertEqual(short, discovery.compact_search(full, limit=1))
            item = short['results'][0]
            self.assertTrue(item['safety']['has_failure_or_partial_reports'])
            self.assertTrue(item['safety']['community_event_review_required'])
            self.assertTrue(short['cache_status']['upstream_stale'])

    def test_cleanup_permission_error_cannot_break_cache_fallback(self):
        with mock.patch.object(persistent, '_write_regular_at', side_effect=PermissionError('read only')):
            with mock.patch.object(persistent.os, 'unlink', side_effect=PermissionError('read only')):
                self.assertEqual(len(snapshots.load_cache(self.cache).records), 4)

    def test_missing_dependency_metadata_uses_normal_validation(self):
        with mock.patch.object(persistent, 'version', side_effect=PackageNotFoundError('jsonschema')):
            self.assertEqual(len(snapshots.load_cache(self.cache).records), 4)
        self.assertFalse((self.cache / '.derived-index').exists())
