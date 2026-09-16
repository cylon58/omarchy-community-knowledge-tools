"""Structural and semantic contracts for rendered production/pilot workflows."""
import json
import unittest


class Deployment(unittest.TestCase):
    def test_rendered_jobs_keep_tokens_pins_events_and_artifacts_in_separate_lanes(self):
        from omarchy_knowledge.deployment import workflows
        from omarchy_knowledge.coordinator import Policy
        workflows = workflows(Policy('a' * 40, 'b' * 40, 'pilot'))
        self.assertEqual(set(workflows), {'intake.yml', 'reconcile.yml'})
        for name, workflow in workflows.items():
            self.assertEqual(workflow['permissions'], {'contents': 'read'})
            self.assertEqual(workflow['concurrency'], {'group': 'omarchy-knowledge-native-writer', 'cancel-in-progress': False, 'queue': 'max'})
            self.assertEqual(set(workflow['jobs']), {'plan', 'publish', 'build', 'pages'})
            self.assertEqual(workflow['jobs']['plan']['permissions'], {'contents': 'read', 'pull-requests': 'read'})
            self.assertEqual(workflow['jobs']['publish']['permissions'], {'contents': 'write', 'pull-requests': 'read'})
            self.assertEqual(workflow['jobs']['build']['permissions'], {'contents': 'read'})
            self.assertEqual(workflow['jobs']['pages']['permissions'], {'contents': 'read', 'pages': 'write', 'id-token': 'write'})
            self.assertEqual(workflow['jobs']['pages']['environment']['name'], 'github-pages')
            encoded = json.dumps(workflow)
            for forbidden in ('workflow_run', 'self-hosted', 'pull_request.head', 'pull_request.merge', 'actions/cache', 'secrets.'):
                self.assertNotIn(forbidden, encoded)
            self.assertIn('1373467908', encoded)
            self.assertIn('cylon58/omarchy-community-knowledge-pilot', encoded)
            self.assertIn('refs/heads/main', encoded)
            for job in workflow['jobs'].values():
                self.assertLessEqual(job['timeout-minutes'], 15)
                self.assertIn('github.repository_id', job['if'])
                for step in job['steps']:
                    if 'uses' in step:
                        self.assertRegex(step['uses'], r'^actions/[a-z-]+@[a-f0-9]{40}$')
                    if step.get('uses', '').startswith('actions/checkout@'):
                        self.assertEqual(step['with']['ref'], 'b' * 40)
                        self.assertFalse(step['with']['persist-credentials'])
                        self.assertEqual(step['with']['repository'], 'cylon58/omarchy-community-knowledge-tools')
                    if 'pip install' in step.get('run', ''):
                        self.assertNotIn('GITHUB_TOKEN', step.get('env', {}))
                        self.assertIn('--no-deps', step['run'])
                        self.assertIn('referencing==0.37.0', step['run'])
                    if step.get('uses', '').startswith('actions/download-artifact@'):
                        self.assertNotIn('run-id', step['with'])
                        self.assertNotIn('github-token', step['with'])
                        self.assertIn('github.run_id', step['with']['name'])
                        self.assertIn('github.run_attempt', step['with']['name'])
            if name == 'intake.yml':
                self.assertEqual(workflow['on'], {'pull_request_target': {'types': ['opened', 'synchronize', 'reopened']}})
            else:
                self.assertEqual(workflow['on']['schedule'], [{'cron': '17 * * * *'}])

    def test_renderer_requires_immutable_pins(self):
        from omarchy_knowledge.coordinator import Policy
        from omarchy_knowledge.github_native import NativeUnavailable
        for pin in ('main', 'HEAD', 'PUBLIC_SHA', '1234567'):
            with self.assertRaises(NativeUnavailable):
                Policy('a' * 40, pin)


if __name__ == '__main__':
    unittest.main()
