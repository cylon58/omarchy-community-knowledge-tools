"""Append-only relationship claims remain visible without gaining edit authority."""
from copy import deepcopy
import json
import unittest
from unittest.mock import patch

from tests import test_admission
from tests.test_coordinator import FakeAPI
from tests.test_projections import case, change, environment, hardware, report, resolution_event
from omarchy_knowledge.discovery import search_snapshot, explain_record, show_record

EVENT = 'ee3e61da-3b3c-4e4e-9fba-970ef606672f'
SECOND = 'fd3e61da-3b3c-4e4e-9fba-970ef606672f'
REPORT = 'ed3e61da-3b3c-4e4e-9fba-970ef606672f'


def relation(kind, source, target):
    return {'schema_version': 1, 'id': EVENT, 'type': 'event',
            'created_at': '2026-09-16T12:30:00Z', 'provenance': {'kind': 'journal-import'},
            'payload': {'event_kind': kind, 'targets': [source, target], 'reason': 'Observed data loss.',
                        'relation': {'kind': {'withdrawal': 'withdraws', 'correction': 'corrects',
                                             'supersession': 'supersedes', 'dispute': 'disputes'}[kind],
                                     'from': source, 'to': target}}}


class EventDiscovery(unittest.TestCase):
    setUp = test_admission.TreeAdmission.setUp
    git = test_admission.TreeAdmission.git
    commit = test_admission.TreeAdmission.commit

    def cache(self, records, canonical=False):
        from omarchy_knowledge.canonical import sync
        from omarchy_knowledge.coordinator import Policy, prepare, publish
        from omarchy_knowledge.snapshots import _write_snapshot, import_snapshot
        cache = self.root / 'cache'
        if canonical:
            self.head = self.commit({f"records/{r['type']}s/{r['id']}.json": ('100644', json.dumps(r).encode())
                                     for r in records}, self.base)
            api, policy = FakeAPI(self), Policy('a' * 40, 'b' * 40)
            self.assertEqual(publish(api, policy, prepare(api, policy, 1)).status, 'accepted')
            with patch('omarchy_knowledge.github_public.GitHubPublicRead.repository', side_effect=OSError), \
                    patch('omarchy_knowledge.catalog.CatalogPublicRead.catalog', side_effect=OSError):
                sync(api, policy, cache)
        else:
            output = self.root / 'snapshot'
            _write_snapshot(records, output, data_revision='fixture', toolkit_revision='fixture',
                            created_at='2026-09-16T12:00:00Z', source_updated_at='2026-09-16T12:00:00Z',
                            source_kind='local-directory', source_reference='fixture')
            import_snapshot(output, cache)
        return cache

    def test_local_withdrawal_is_visible_and_requires_inspection_with_or_without_environment(self):
        value = relation('withdrawal', {'type': 'case', 'id': case()['id']}, {'type': 'change', 'id': change()['id']})
        cache = self.cache([case(), change(), value])
        env = environment(hardware('host', 'host'))
        for local in (None, env):
            with self.subTest(environment=local):
                found = search_snapshot(cache, 'input', environment=local)['results'][0]['changes'][0]
                self.assertEqual(found['recommendation']['action'], 'investigate')
                self.assertEqual(found['recommendation']['workaround'], 'defer-new')
                claim = found['related_events']['events'][0]
                self.assertEqual(claim['event_id'], EVENT)
                self.assertEqual(claim['reason'], 'Observed data loss.')
                self.assertEqual(claim['relation'], value['payload']['relation'])
                self.assertEqual(claim['attribution'], 'unattributed-claim')
                self.assertEqual(claim['provenance'], {'kind': 'journal-import'})
                self.assertEqual(claim['claim_status'], 'community-claim')
        explanation = explain_record(cache, change()['id'], environment=env)
        self.assertTrue(explanation['projections'][0]['related_events']['requires_review'])
        self.assertEqual(explanation['related_events']['events'][0]['event_id'], EVENT)
        self.assertEqual(show_record(cache, change()['id'])['record'], change())

    def test_canonical_report_correction_preserves_attribution_and_evidence_counts(self):
        old, new = report(REPORT), report(SECOND)
        value = relation('correction', {'type': 'report', 'id': SECOND}, {'type': 'report', 'id': REPORT})
        cache = self.cache([case(), change(), old, new, value], canonical=True)
        env = environment(hardware('host', 'host'))
        found = search_snapshot(cache, 'input', environment=env)['results'][0]['changes'][0]
        self.assertEqual(found['recommendation']['action'], 'investigate')
        event = found['related_events']['events'][0]
        self.assertEqual(event['attribution'], 'authenticated-account')
        self.assertEqual(event['actor_account_id'], '71')
        self.assertEqual(event['claim_status'], 'community-claim')
        self.assertEqual(found['evidence']['authenticated_account_counts']['success'], 1)
        self.assertEqual(found['evidence']['authenticated_report_counts']['success'], 2)
        direct = explain_record(cache, REPORT, environment=env)
        self.assertEqual(direct['related_events']['events'][0]['affected_record_ids'], [REPORT])
        self.assertEqual(show_record(cache, REPORT)['record'], old)

    def test_supersession_direction_does_not_mark_replacement_withdrawn(self):
        replacement = deepcopy(change())
        replacement['id'] = SECOND
        value = relation('supersession', {'type': 'change', 'id': SECOND}, {'type': 'change', 'id': change()['id']})
        cache = self.cache([case(), change(), replacement, value])
        env = environment(hardware('host', 'host'))
        old = explain_record(cache, change()['id'], environment=env)['projections'][0]
        new = explain_record(cache, SECOND, environment=env)['projections'][0]
        self.assertEqual(old['recommendation']['action'], 'investigate')
        self.assertEqual(new['recommendation']['action'], 'consider-workaround')
        self.assertFalse(new['related_events']['requires_review'])
        self.assertEqual(new['related_events']['events'][0]['relation']['from']['id'], SECOND)

    def test_optional_dispute_remains_optional_and_does_not_grant_withdrawal_authority(self):
        value = relation('dispute', {'type': 'case', 'id': case()['id']}, {'type': 'change', 'id': change()['id']})
        cache = self.cache([case('optional'), change('optional'), value])
        found = search_snapshot(cache, 'input', intent='optional', environment=environment(hardware('host', 'host')))
        item = found['results'][0]['changes'][0]
        self.assertEqual(item['intent'], 'optional')
        self.assertEqual(item['recommendation']['action'], 'investigate')
        self.assertIn('community', ' '.join(item['recommendation']['reasons']).lower())
        self.assertEqual(item['related_events']['events'][0]['claim_status'], 'community-claim')

    def test_resolution_dispute_is_visible_only_on_its_direct_change_cohort(self):
        upstream = resolution_event()
        other = deepcopy(change())
        other['id'] = SECOND
        value = relation('dispute', {'type': 'change', 'id': change()['id']},
                         {'type': 'event', 'id': upstream['id']})
        cache = self.cache([case(), change(), other, upstream, value])
        env = environment(hardware('host', 'host'))
        affected = explain_record(cache, change()['id'], environment=env)['projections'][0]
        self.assertTrue(affected['recommendation']['community_event_review_required'])
        self.assertEqual(affected['related_events']['events'][0]['affected_record_ids'], [upstream['id']])
        unaffected = explain_record(cache, SECOND, environment=env)['projections'][0]
        self.assertEqual(unaffected['recommendation']['action'], 'consider-workaround')
        self.assertFalse(unaffected['related_events']['requires_review'])

    def test_shared_multi_case_multi_change_resolution_dispute_gates_every_targeted_pair(self):
        from datetime import datetime, timezone
        from tests.test_live_resolution import event, GitHub, Catalogs, AUTHORITY, NOW
        from tests.test_projections import software
        from omarchy_knowledge import resolution
        second_change, second_case, third_change, unrelated_case, unrelated_change = (
            deepcopy(change()), deepcopy(case()), deepcopy(change()), deepcopy(case()), deepcopy(change()))
        second_change['id'] = SECOND
        second_case['id'] = REPORT
        third_change['id'] = '12345678-1234-4234-9234-123456789012'
        third_change['payload']['case_id'] = second_case['id']
        unrelated_case['id'] = '12345678-1234-4234-9234-123456789013'
        unrelated_change['id'] = '12345678-1234-4234-9234-123456789014'
        unrelated_change['payload']['case_id'] = unrelated_case['id']
        upstream = event()
        upstream['payload']['targets'].extend({'type': r['type'], 'id': r['id']}
                                             for r in (second_change, second_case, third_change))
        value = relation('dispute', {'type': 'change', 'id': change()['id']},
                         {'type': 'event', 'id': upstream['id']})
        records = [case(), change(), second_change, second_case, third_change,
                   unrelated_case, unrelated_change, upstream, value]
        original = resolution.refresh_upstream
        def refresh(records):
            return original(records, github=GitHub(upstream), catalogs=Catalogs(), authority=AUTHORITY, now=NOW)
        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return cls(2026, 9, 16, 12, tzinfo=timezone.utc)
        env = environment(hardware('host', 'host'), software('runtime', 'omarchy', '4.0.3-1', 'arch'),
                          software('settings', 'package', '4.0.3-1', 'arch', 'omarchy-settings'))
        with patch.object(resolution, 'refresh_upstream', side_effect=refresh), \
                patch.object(resolution, 'AUTHORITY', AUTHORITY), patch('projections.datetime', Clock):
            cache = self.cache(records, canonical=True)
            for target in (case(), change(), second_change, second_case, third_change):
                with self.subTest(target=target['id']):
                    result = explain_record(cache, target['id'], environment=env)
                    self.assertTrue(result['related_events']['requires_review'])
                    for item in result['projections']:
                        self.assertEqual(item['recommendation']['action'], 'investigate')
                        self.assertTrue(item['recommendation']['community_event_review_required'])
                        self.assertEqual(item['recommendation']['upstream_state'], 'available')
                        self.assertEqual(item['related_events']['events'][0]['event_id'], EVENT)
            found = search_snapshot(cache, 'input', environment=env)
            for result in found['results']:
                if result['case_id'] == unrelated_case['id']:
                    self.assertFalse(result['related_events']['requires_review'])
                    self.assertEqual(result['changes'][0]['recommendation']['action'], 'consider-workaround')
                else:
                    self.assertTrue(result['related_events']['requires_review'])
                    self.assertTrue(all(c['recommendation']['action'] == 'investigate' for c in result['changes']))

    def test_display_cap_does_not_hide_a_later_incoming_safety_claim(self):
        from omarchy_knowledge.discovery import _related_events
        records = []
        for index in range(65):
            value = relation('dispute', {'type': 'change', 'id': change()['id']}, {'type': 'change', 'id': SECOND})
            value['id'] = f'{index:08x}-3b3c-4e4e-9fba-970ef606672f'
            records.append(value)
        records[-1]['payload']['relation']['from'], records[-1]['payload']['relation']['to'] = (
            records[-1]['payload']['relation']['to'], records[-1]['payload']['relation']['from'])
        records[-1]['created_at'] = '2026-09-15T12:30:00Z'
        result = _related_events(records, {change()['id']}, [])
        self.assertTrue(result['truncated'])
        self.assertLessEqual(len(result['events']), 64)
        self.assertTrue(all(not e['affected_record_ids'] for e in result['events']))
        self.assertTrue(result['requires_review'])

    def test_unknown_topology_exclusion_stays_investigation_in_search_and_explain(self):
        value = change()
        value['payload']['applicability']['excludes'] = [{'kind': 'topology', 'topology': {
            'selectors': [{'alias': 'input', 'selector': {'kind': 'hardware', 'role': 'keyboard'}},
                          {'alias': 'machine', 'selector': {'kind': 'hardware', 'role': 'host'}}],
            'required_edges': [{'from': 'input', 'to': 'machine', 'relation': 'connected_to', 'transport': 'usb'}]}}]
        cache = self.cache([case(), value])
        env = environment(hardware('keyboard', 'keyboard'), hardware('host', 'host'),
                          edges=[{'from': 'keyboard', 'to': 'host', 'relation': 'connected_to',
                                  'transport': 'unknown', 'detail': 'summarized'}])
        found = search_snapshot(cache, 'input', environment=env)['results'][0]
        self.assertEqual(found['incompatible_change_ids'], [])
        for item in (found['changes'][0], explain_record(cache, value['id'], environment=env)['projections'][0]):
            self.assertEqual(item['applicability']['state'], 'insufficient-information')
            self.assertEqual(item['recommendation']['action'], 'investigate')


if __name__ == '__main__':
    unittest.main()
