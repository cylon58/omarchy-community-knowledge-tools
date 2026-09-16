"""Native service boundaries exercised through offline GitHub API fixtures."""
import json
import unittest
from tests import test_admission
from tests.test_coordinator import FakeAPI


class Service(unittest.TestCase):
    setUp = test_admission.TreeAdmission.setUp
    git = test_admission.TreeAdmission.git
    commit = test_admission.TreeAdmission.commit

    def test_invalid_candidates_are_skipped_and_rotation_reaches_later_valid_pr(self):
        from omarchy_knowledge.service import plan_run
        from omarchy_knowledge.coordinator import Policy
        api = FakeAPI(self)
        api.pending = lambda page: [{'number': n, 'head': self.head} for n in range((page-1)*20+1, min(page*20+1, 42))]
        real_pull = api.pull
        def pull(number):
            value = real_pull(number)
            if number <= 20:
                value['merge_commit_sha'] = self.base
            return value
        api.pull = pull
        batch = plan_run(api, Policy('a' * 40, 'b' * 40), run_number=1)
        self.assertEqual(batch['plan']['pull_request'], 21)
        self.assertEqual(batch['status']['scanned'], 41)
        self.assertLessEqual(len(batch['status']['outcomes']), 20)
        batch = plan_run(api, Policy('a' * 40, 'b' * 40), run_number=0)
        self.assertIsNone(batch['plan'])
        self.assertEqual(len(batch['status']['outcomes']), 20)

    def test_publish_reconciles_first_and_halts_on_receipt_pending(self):
        from omarchy_knowledge.service import plan_run, publish_run
        from omarchy_knowledge.coordinator import Policy
        api = FakeAPI(self); policy = Policy('a' * 40, 'b' * 40)
        batch = plan_run(api, policy, pull_request=1)
        api.fail_receipt = True
        status = publish_run(api, policy, batch)
        self.assertEqual(status['status'], 'receipt-pending')
        self.assertEqual(status['outcomes'][-1]['head'], self.head)
        self.assertEqual(status['outcomes'][-1]['pull_request'], 1)
        self.assertEqual(api.writes, 1)
        self.assertEqual(publish_run(api, policy, batch)['status'], 'retry')
        self.assertEqual(api.writes, 1)

    def test_stale_or_wrong_run_plan_cannot_write(self):
        from omarchy_knowledge.service import plan_run, publish_run
        from omarchy_knowledge.coordinator import Policy
        from omarchy_knowledge.github_native import NativeUnavailable
        api = FakeAPI(self); policy = Policy('a' * 40, 'b' * 40)
        batch = plan_run(api, policy, pull_request=1, run_id=44, run_attempt=1)
        with self.assertRaises(NativeUnavailable):
            publish_run(api, policy, batch, run_id=45, run_attempt=1)
        api.base = self.commit({'README.md': ('100644', b'update')}, self.base)
        self.assertEqual(publish_run(api, policy, batch, run_id=44, run_attempt=1)['status'], 'retry')
        self.assertEqual(api.writes, 0)

    def test_event_guard_rejects_fork_repository_and_nonmain_context(self):
        from omarchy_knowledge.service import guard_event
        from omarchy_knowledge.coordinator import Policy
        from omarchy_knowledge.github_native import NativeUnavailable
        policy = Policy('a' * 40, 'b' * 40)
        repo = FakeAPI(self).repository()
        env = {'GITHUB_REPOSITORY': policy.repository, 'GITHUB_REPOSITORY_ID': str(policy.repository_id),
               'GITHUB_REF': 'refs/heads/main', 'GITHUB_EVENT_NAME': 'pull_request_target'}
        event = {'repository': repo, 'action': 'opened', 'number': 1,
                 'pull_request': {'base': {'ref': 'main', 'repo': repo}}}
        self.assertEqual(guard_event(policy, env, event), 1)
        for key, value in [('GITHUB_REF', 'refs/pull/1/merge'), ('GITHUB_REPOSITORY_ID', '1'),
                           ('GITHUB_EVENT_NAME', 'pull_request'), ('GITHUB_REPOSITORY', 'attacker/fork')]:
            with self.assertRaises(NativeUnavailable):
                guard_event(policy, {**env, key: value}, event)
        event['pull_request']['base']['ref'] = 'other'
        with self.assertRaises(NativeUnavailable):
            guard_event(policy, env, event)

    def test_rejected_content_is_safe_status_and_later_candidate_can_plan(self):
        from omarchy_knowledge.service import plan_run, safe_status
        from omarchy_knowledge.coordinator import Policy
        from omarchy_knowledge.github_native import NativeUnavailable
        api = FakeAPI(self)
        bad = self.commit({'secret-private-name.txt': ('100644', b'raw bad prose')}, self.base)
        bad_merge = self.git('commit-tree', self.reader.commit(bad), '-p', self.base, '-p', bad, data=b'test merge\n')
        actual_pull = api.pull
        def pull(number):
            value = actual_pull(number)
            if number == 1:
                value['head']['sha'] = bad
                value['merge_commit_sha'] = bad_merge
            return value
        api.pull = pull
        api.pending = lambda page: [{'number': 1, 'head': bad}, {'number': 2, 'head': self.head}]
        batch = plan_run(api, Policy('a' * 40, 'b' * 40))
        self.assertEqual(batch['plan']['pull_request'], 2)
        self.assertEqual(batch['status']['outcomes'][0], {'status': 'rejected', 'pull_request': 1})
        self.assertNotIn('secret-private', json.dumps(batch['status']))
        self.assertNotIn('raw bad prose', json.dumps(batch['status']))
        with self.assertRaises(NativeUnavailable):
            safe_status({'status': 'accepted', 'outcomes': [{'status': 'accepted', 'pull_request': 1, 'head': '<script>'}]})

    def test_schedule_and_manual_guards_accept_only_main(self):
        from omarchy_knowledge.service import guard_event
        from omarchy_knowledge.coordinator import Policy
        from omarchy_knowledge.github_native import NativeUnavailable
        policy = Policy('a' * 40, 'b' * 40)
        env = {'GITHUB_REPOSITORY': policy.repository, 'GITHUB_REPOSITORY_ID': str(policy.repository_id),
               'GITHUB_REF': 'refs/heads/main'}
        event = {'repository': FakeAPI(self).repository()}
        for name in ('schedule', 'workflow_dispatch'):
            self.assertIsNone(guard_event(policy, {**env, 'GITHUB_EVENT_NAME': name}, event))
            with self.assertRaises(NativeUnavailable):
                guard_event(policy, {**env, 'GITHUB_EVENT_NAME': name, 'GITHUB_REF': 'refs/heads/other'}, event)

    def test_real_entrypoints_produce_bounded_plan_status_and_empty_site(self):
        import contextlib
        import io
        import os
        from unittest.mock import patch
        from omarchy_knowledge.service import main
        api = FakeAPI(self); api.pending = lambda page: []
        event = self.root / 'event.json'; event.write_text(json.dumps({'repository': api.repository()}))
        env = {'GITHUB_REPOSITORY': 'cylon58/omarchy-community-knowledge', 'GITHUB_REPOSITORY_ID': '1373429914',
               'GITHUB_REF': 'refs/heads/main', 'GITHUB_EVENT_NAME': 'workflow_dispatch',
               'GITHUB_EVENT_PATH': str(event), 'GITHUB_RUN_ID': '55', 'GITHUB_RUN_ATTEMPT': '1',
               'GITHUB_RUN_NUMBER': '1', 'GITHUB_TOKEN': 'fixture-token'}
        common = ['--deployment', 'production', '--policy-revision', 'a' * 40, '--toolkit-revision', 'b' * 40]
        plan = self.root / 'plan/plan.json'; status = self.root / 'status/status.json'; site = self.root / 'site'
        for command, arguments in [('plan', ['--output', str(plan)]),
                                   ('publish', ['--input', str(plan), '--output', str(status)]),
                                   ('build', ['--input', str(status), '--output', str(site)])]:
            with patch.dict(os.environ, env), patch('omarchy_knowledge.service.GitHubRead', return_value=api), \
                    patch('omarchy_knowledge.service.GitHubWriter', return_value=api), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main([command, *common, *arguments]), 0)
                self.assertNotIn('GITHUB_TOKEN', os.environ)
        self.assertLessEqual(plan.stat().st_size, 1024 * 1024)
        self.assertLessEqual(status.stat().st_size, 64 * 1024)
        self.assertEqual(json.loads(status.read_bytes())['status'], 'idle')
        self.assertEqual((site / 'records.jsonl').read_bytes(), b'')
        self.assertEqual(api.writes, 0)


if __name__ == '__main__':
    unittest.main()
