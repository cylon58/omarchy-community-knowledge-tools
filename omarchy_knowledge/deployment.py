"""Render JSON-form YAML workflows after the reviewed public toolkit is published.

No embedded moving branch or guessed self-revision. Outputs are deployment data;
this command performs no GitHub writes and reads no credentials.
"""
import argparse
import json
import os

from .coordinator import Policy
from .github_native import NativeUnavailable
from .snapshots import _open_directory, _write_regular_at

TOOLKIT = 'cylon58/omarchy-community-knowledge-tools'
TOOLKIT_ID = 1373429982
PINS = {
    'checkout': '3d3c42e5aac5ba805825da76410c181273ba90b1',
    'setup-python': '5fda3b95a4ea91299a34e894583c3862153e4b97',
    'upload-artifact': '043fb46d1a93c77aae656e7c1c64a875d1fc6a0a',
    'download-artifact': '3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c',
    'upload-pages-artifact': 'fc324d3547104276b827a68afc52ff2a11cc49c9',
    'deploy-pages': '368f82528645a54fb793d4d04e342629a3f51346',
}
RUNTIME = ('jsonschema==4.26.0 attrs==26.1.0 jsonschema-specifications==2025.9.1 '
           'referencing==0.37.0 rpds-py==2026.6.3')
VERCMP_SETUP = {
    'name': 'Provide official Ubuntu Arch version comparator for read-only refresh',
    'run': '. /etc/os-release\n'
           'test "$ID" = ubuntu && test "$VERSION_ID" = 24.04\n'
           'sudo apt-get update\n'
           'sudo apt-get install --no-install-recommends -y makepkg libzstd1\n'
           "dpkg-query -W -f='${Package} ${Version}\\n' makepkg libzstd1\n"
           'test "$(/usr/bin/vercmp 1:1.0-1 1.0-1)" = 1\n'
           'test "$(/usr/bin/vercmp 1.0-2 1.0-1)" = 1\n',
}


def _action(action, **inputs):
    return {'uses': 'actions/' + action + '@' + PINS[action], 'with': inputs}


def workflows(policy):
    """Fixed trusted policy only. Both entrypoints share a non-cancelling writer lock."""
    def setup():
        return [
            _action('checkout', repository=TOOLKIT, ref=policy.toolkit_revision,
                    path='toolkit', **{'persist-credentials': False, 'fetch-depth': 1}),
            _action('setup-python', **{'python-version': '3.13'}),
            {'name': 'Install fixed trusted runtime without a job token in the environment',
             'run': 'python -m venv "$RUNNER_TEMP/runtime"\n'
                    '"$RUNNER_TEMP/runtime/bin/python" -m pip install --disable-pip-version-check '
                    '--only-binary=:all: --no-deps ' + RUNTIME + '\n'
                    '"$RUNNER_TEMP/runtime/bin/python" -m pip install --disable-pip-version-check '
                    '--no-build-isolation --no-deps ./toolkit\n'},
        ]
    def artifact(lane):
        return 'knowledge-' + lane + '-${{ github.run_id }}-${{ github.run_attempt }}'
    def run(command, arguments):
        return {'name': command.capitalize() + ' canonical snapshot',
                'run': '"$RUNNER_TEMP/runtime/bin/python" -I -m omarchy_knowledge.service ' + command +
                       ' --deployment ' + policy.deployment + ' --policy-revision ' + policy.policy_revision +
                       ' --toolkit-revision ' + policy.toolkit_revision + ' ' + arguments,
                'env': {'GITHUB_TOKEN': '${{ github.token }}'}}
    def upload(lane, path):
        return _action('upload-artifact', name=artifact(lane), path=path,
                       **{'retention-days': 1, 'if-no-files-found': 'error', 'compression-level': 6})
    def download(lane):
        # No token/run-id: download-artifact selects only this workflow run.
        return _action('download-artifact', name=artifact(lane), path='${{ runner.temp }}/' + lane)

    output = {}
    for intake in (True, False):
        event = ("github.event_name == 'pull_request_target' && "
                 "github.event.pull_request.base.ref == 'main' && "
                 f"github.event.pull_request.base.repo.id == {policy.repository_id} && "
                 f"github.event.pull_request.base.repo.full_name == '{policy.repository}'"
                 if intake else "(github.event_name == 'schedule' || github.event_name == 'workflow_dispatch')")
        guard = ("${{ github.repository_id == '" + str(policy.repository_id) + "' && "
                 "github.repository == '" + policy.repository + "' && "
                 "github.ref == 'refs/heads/main' && github.event.repository.default_branch == 'main' && " + event + ' }}')
        common = {'if': guard, 'runs-on': 'ubuntu-24.04', 'timeout-minutes': 15}
        jobs = {
            'plan': {**common, 'permissions': {'contents': 'read', 'pull-requests': 'read'}, 'steps': setup() + [
                run('plan', '--output "$RUNNER_TEMP/plan/plan.json"'),
                upload('plan', '${{ runner.temp }}/plan/plan.json')]},
            'publish': {**common, 'needs': 'plan', 'permissions': {'contents': 'write', 'pull-requests': 'read'},
                        'steps': setup() + [download('plan'),
                run('publish', '--input "$RUNNER_TEMP/plan/plan.json" --output "$RUNNER_TEMP/status/status.json"'),
                upload('status', '${{ runner.temp }}/status/status.json')]},
            'build': {**common, 'needs': 'publish', 'permissions': {'contents': 'read'}, 'steps': setup() + [
                VERCMP_SETUP, download('status'), run('build', '--input "$RUNNER_TEMP/status/status.json" --output "$RUNNER_TEMP/site"'),
                _action('upload-pages-artifact', name=artifact('pages'), path='${{ runner.temp }}/site',
                        **{'retention-days': 1})]},
            'pages': {**common, 'needs': 'build',
                      'permissions': {'contents': 'read', 'pages': 'write', 'id-token': 'write'},
                      'environment': {'name': 'github-pages', 'url': '${{ steps.deployment.outputs.page_url }}'},
                      'steps': [{'id': 'deployment', **_action('deploy-pages', artifact_name=artifact('pages'))}]},
        }
        output['intake.yml' if intake else 'reconcile.yml'] = {
            'name': 'Native snapshot intake' if intake else 'Native reconciliation and distribution',
            'on': {'pull_request_target': {'types': ['opened', 'synchronize', 'reopened']}} if intake else
                  {'schedule': [{'cron': '17 * * * *'}], 'workflow_dispatch': {}},
            'permissions': {'contents': 'read'},
            'concurrency': {'group': 'omarchy-knowledge-native-writer', 'cancel-in-progress': False, 'queue': 'max'},
            'jobs': jobs,
        }
    return output


def render(policy, output):
    descriptor = _open_directory(output, 'Deployment output', create=True)
    try:
        config = {'version': 1, 'deployment': policy.deployment, 'repository': policy.repository,
                  'repository_id': policy.repository_id, 'toolkit_repository': TOOLKIT,
                  'toolkit_repository_id': TOOLKIT_ID, 'toolkit_revision': policy.toolkit_revision,
                  'policy_revision': policy.policy_revision}
        for name, value in {**workflows(policy), 'deployment.json': config}.items():
            _write_regular_at(descriptor, name, (json.dumps(value, indent=2, sort_keys=True) + '\n').encode())
    finally:
        os.close(descriptor)
    return config


def main(argv=None):
    parser = argparse.ArgumentParser(description='Render local pinned native workflows; no deployment occurs')
    parser.add_argument('--deployment', choices=('production', 'pilot'), required=True)
    parser.add_argument('--toolkit-revision', required=True)
    parser.add_argument('--policy-revision', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args(argv)
    try:
        print(json.dumps(render(Policy(args.policy_revision, args.toolkit_revision, args.deployment), args.output)))
        return 0
    except (NativeUnavailable, OSError, ValueError):
        print('Deployment rendering unavailable; full immutable revisions and new output files required.')
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
