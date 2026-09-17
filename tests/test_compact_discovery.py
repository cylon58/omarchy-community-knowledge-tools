"""Shortlists reduce context without turning omitted detail into clearance."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from test_records import case, change, report, change_two, relation_event, EVENT_ID
from test_local_workflow import write_records
from omarchy_knowledge.discovery import search_snapshot
from omarchy_knowledge.snapshots import build_snapshot, import_snapshot
from omarchy_knowledge.cli import _parser, _dispatch


class CompactDiscovery(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.cache = self.root / 'cache'

    def prepare(self, records):
        source = self.root / 'source'; source.mkdir()
        write_records(source, records)
        build_snapshot(source, self.root / 'snapshot', data_revision='fixture',
                       toolkit_revision='fixture', created_at='2026-09-16T16:00:00Z',
                       source_updated_at='2026-09-16T16:00:00Z', source_reference='fixture')
        import_snapshot(self.root / 'snapshot', self.cache)

    def test_cli_defaults_to_shortlist_and_full_remains_available(self):
        self.prepare([case(), change(), report()])
        args = _parser().parse_args(['search', '--cache', str(self.cache), '--query', 'dock'])
        short = _dispatch(args)
        self.assertEqual(short.get('view'), 'shortlist')
        self.assertNotIn('predicates', short['results'][0]['changes'][0])
        self.assertTrue(short['detail_required_before_action'])
        args = _parser().parse_args(['search', '--cache', str(self.cache), '--query', 'dock', '--full'])
        full = _dispatch(args)
        self.assertIn('predicates', full['results'][0]['changes'][0])

    def test_limit_reports_omitted_cases_and_does_not_change_legacy_api(self):
        other = case('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa')
        self.prepare([case(), other])
        full = search_snapshot(self.cache, 'dock')
        self.assertEqual(len(full['results']), 2)
        short = search_snapshot(self.cache, 'dock', compact=True, limit=1)
        self.assertEqual(len(short['results']), 1)
        self.assertEqual(short['total_matches'], 2)
        self.assertTrue(short['truncated'])
        for limit in (0, -1, 51, True):
            with self.assertRaises(ValueError):
                search_snapshot(self.cache, 'dock', compact=True, limit=limit)

    def test_failure_and_supersession_survive_compaction(self):
        failed = report(); failed['payload']['result'] = 'failure'
        newer = change_two()
        supersession = relation_event(EVENT_ID, newer['id'], change()['id'])
        self.prepare([case(), change(), newer, failed, supersession])
        short = search_snapshot(self.cache, 'dock', compact=True)
        hit = next(c for c in short['results'][0]['changes'] if c['change_id'] == change()['id'])
        self.assertEqual(hit['evidence']['unattributed_claim_counts']['failure'], 1)
        self.assertTrue(hit['related_events']['requires_review'])
        self.assertEqual(hit['recommendation']['action'], 'investigate')
        self.assertIn(EVENT_ID, hit['related_events']['event_ids'])
        self.assertTrue(short['cache_status']['upstream_stale'])
        self.assertEqual(short['cache_status']['upstream']['status'], 'not-refreshed')
        self.assertNotIn('observations', short['cache_status']['upstream'])

    def test_optional_exclusion_and_unknown_applicability_unchanged(self):
        optional = case('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa')
        optional['payload']['intent'] = 'optional'
        optional['payload']['expectation']['basis'] = 'user-requested'
        self.prepare([case(), change(), optional])
        short = search_snapshot(self.cache, 'dock', compact=True)
        self.assertEqual([r['case_id'] for r in short['results']], [case()['id']])
        self.assertEqual(short['results'][0]['changes'][0]['applicability']['state'], 'not-evaluated')

    def test_compact_retains_source_freshness_without_full_upstream_catalog(self):
        from omarchy_knowledge.discovery import compact_search
        self.prepare([case(), change(), report()])
        full = search_snapshot(self.cache, 'dock')
        full['cache_status']['upstream'] = {
            'version': 2, 'status': 'partial', 'observed_at': '2026-09-17T12:00:00Z',
            'fresh_until': '2026-09-18T12:00:00Z', 'observations': [{'large': 'x' * 20000}],
            'authority_configured': False,
        }
        short = compact_search(full, limit=5)
        self.assertEqual(short['cache_status']['upstream']['status'], 'partial')
        self.assertEqual(short['cache_status']['upstream']['fresh_until'], '2026-09-18T12:00:00Z')
        self.assertLess(len(json.dumps(short)), len(json.dumps(full)) / 3)
        self.assertEqual(full['cache_status']['upstream']['observations'][0]['large'], 'x' * 20000)

    def test_omitted_change_cannot_hide_adverse_evidence_or_update_advice(self):
        from omarchy_knowledge.discovery import compact_search
        self.prepare([case(), change(), report()])
        full = search_snapshot(self.cache, 'dock')
        changes = full['results'][0]['changes']
        changes.extend(deepcopy(changes[0]) for _ in range(5))
        changes[-1]['evidence']['unattributed_claim_counts']['failure'] = 1
        changes[-1]['recommendation']['action'] = 'prefer-update'
        short = compact_search(full)
        item = short['results'][0]
        self.assertEqual(len(item['changes']), 5)
        self.assertTrue(item['changes_truncated'])
        self.assertTrue(item['safety']['has_failure_or_partial_reports'])
        self.assertIn('prefer-update', item['safety']['recommendation_actions'])

    def test_show_default_keeps_record_but_not_repeated_upstream_catalog(self):
        self.prepare([case(), change()])
        args = _parser().parse_args(['show', case()['id'], '--cache', str(self.cache)])
        result = _dispatch(args)
        self.assertEqual(result['record'], case())
        self.assertNotIn('observations', result['cache_status']['upstream'])
        args = _parser().parse_args(['show', case()['id'], '--cache', str(self.cache), '--full'])
        self.assertIn('observations', _dispatch(args)['cache_status']['upstream'])

    def test_cli_ranked_aliases_and_explicit_substring_fallback(self):
        record = case(); record['payload']['title'] = 'Numpad bindings lost'
        self.prepare([record])
        argv = ['search', '--cache', str(self.cache), '--query', 'number pad hotkeys']
        ranked = _dispatch(_parser().parse_args(argv))
        self.assertEqual(ranked['results'][0]['case_id'], record['id'])
        self.assertEqual(ranked['retrieval']['method'], 'ranked')
        self.assertEqual(ranked['results'][0]['retrieval']['basis'], 'local-lexical-candidate')
        legacy = _dispatch(_parser().parse_args(argv + ['--method', 'substring']))
        self.assertEqual(legacy['results'], [])

    def test_case_only_failure_without_a_change_remains_visible(self):
        failed = report(); failed['payload'].pop('change_id'); failed['payload']['result'] = 'failure'
        self.prepare([case(), failed])
        item = search_snapshot(self.cache, 'dock', compact=True)['results'][0]
        self.assertTrue(item['safety']['has_failure_or_partial_reports'])
        self.assertEqual(item['case_evidence']['unattributed_claim_counts']['failure'], 1)
        from omarchy_knowledge.discovery import show_record
        detail = show_record(self.cache, case()['id'])
        self.assertIn(failed['id'], detail['linked_report_ids'])

    def test_incompatible_change_cannot_hide_failure(self):
        failed = report(); failed['payload']['result'] = 'failure'
        self.prepare([case(), change(), failed])
        from unittest.mock import patch
        # Supply a complete deterministic applicability result; no network seam.
        import omarchy_knowledge.discovery as discovery
        original = discovery._projection
        def incompatible(*args, **kwargs):
            item = original(*args, **kwargs)
            item['applicability']['state'] = 'does-not-match'
            return item
        with patch.object(discovery, '_projection', side_effect=incompatible):
            item = search_snapshot(self.cache, 'dock', compact=True,
                                   environment=report()['payload']['environment'])['results'][0]
        self.assertEqual(item['changes'], [])
        self.assertTrue(item['safety']['has_failure_or_partial_reports'])
