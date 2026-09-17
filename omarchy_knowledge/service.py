"""Bounded workflow caller. Tokens never enter candidate data or dependency setup."""
import argparse
import json
import os
from pathlib import Path

from .coordinator import (MAX_PLAN, NativeRejected, Policy, canonical, load_plan,
                          object_id, pending, prepare, publish, reconcile, require)
from .github_native import GitHubRead, GitHubWriter, NativeUnavailable, strict_json
from .git_objects import ObjectUnavailable
from .snapshots import _read_regular

MAX_ATTEMPTS = 20


def guard_event(policy, environment, event):
    """Trusted event identity/ref gate, before token use or any API call."""
    require(environment.get('GITHUB_REPOSITORY') == policy.repository
            and environment.get('GITHUB_REPOSITORY_ID') == str(policy.repository_id)
            and environment.get('GITHUB_REF') == 'refs/heads/main')
    repo = event['repository']
    require(type(repo['id']) is int and repo['id'] == policy.repository_id
            and repo['full_name'] == policy.repository and repo['default_branch'] == 'main')
    name = environment.get('GITHUB_EVENT_NAME')
    if name == 'pull_request_target':
        require(event['action'] in {'opened', 'synchronize', 'reopened'})
        base = event['pull_request']['base']
        require(base['ref'] == 'main' and type(base['repo']['id']) is int
                and base['repo']['id'] == policy.repository_id and base['repo']['full_name'] == policy.repository)
        number = event['number']
        require(type(number) is int and 0 < number <= 2_147_483_647)
        return number
    require(name in {'schedule', 'workflow_dispatch'})
    return None


def plan_run(api, policy, *, pull_request=None, run_number=0, run_id=1, run_attempt=1):
    """At most 20 preparations, one admission plan. Rotate the bounded 200-PR window."""
    require(type(run_number) is int and run_number >= 0)
    require(type(run_id) is int and run_id > 0 and type(run_attempt) is int and run_attempt > 0)
    status = {'status': 'idle', 'outcomes': [], 'scanned': 1 if pull_request else 0, 'scan_truncated': False}
    plan = None
    try:
        if pull_request is not None:
            require(type(pull_request) is int and 0 < pull_request <= 2_147_483_647)
            numbers = [pull_request]
        else:
            selected = pending(api, policy, limit=200)
            status.update({k: selected[k] for k in ('scanned', 'scan_truncated')})
            numbers = selected['pull_requests']
            if numbers:
                offset = (run_number * MAX_ATTEMPTS) % len(numbers)
                numbers = numbers[offset:] + numbers[:offset]
        for number in numbers[:MAX_ATTEMPTS]:
            try:
                plan = prepare(api, policy, number)
            except NativeRejected:
                status['outcomes'].append({'status': 'rejected', 'pull_request': number})
            except (NativeUnavailable, ObjectUnavailable):
                status['outcomes'].append({'status': 'unavailable', 'pull_request': number})
            else:
                status['outcomes'].append({'status': 'planned', 'pull_request': number, 'head': plan['head']})
                break
        status['status'] = 'planned' if plan else 'unavailable' if numbers else 'idle'
    except (NativeUnavailable, ObjectUnavailable, KeyError, TypeError, ValueError, RecursionError):
        status['status'] = 'unavailable'
    batch = {'version': 1, 'run_id': run_id, 'run_attempt': run_attempt, 'plan': plan, 'status': safe_status(status)}
    require(len(canonical(batch)) <= MAX_PLAN)
    return batch


def publish_run(api, policy, batch, *, run_id=1, run_attempt=1):
    """One reconciled CAS attempt; pending or retry always terminates this run."""
    require(isinstance(batch, dict) and set(batch) == {'version', 'run_id', 'run_attempt', 'plan', 'status'})
    require(type(batch['version']) is int and batch['version'] == 1
            and type(batch['run_id']) is int and batch['run_id'] == run_id
            and type(batch['run_attempt']) is int and batch['run_attempt'] == run_attempt)
    status = safe_status(batch['status'])
    result = reconcile(api, policy)
    if result.status != 'complete':
        return {**status, 'status': 'retry'}
    if batch['plan'] is None:
        return status
    plan = load_plan(canonical(batch['plan']))
    result = publish(api, policy, plan)
    outcome = {'status': result.status, 'pull_request': plan['pull_request'], 'head': plan['head'],
               'accepted_commit_oid': result.accepted_commit_oid, 'head_changed': result.head_changed}
    return safe_status({**status, 'status': result.status,
                        'outcomes': [o for o in status['outcomes'] if o['status'] != 'planned'] + [outcome]})


def safe_status(value):
    """Public statuses contain only enums and validated numeric/immutable identities."""
    require(isinstance(value, dict) and set(value) <= {'status', 'outcomes', 'scanned', 'scan_truncated'})
    require(value.get('status') in {'idle', 'planned', 'accepted', 'receipt-pending', 'retry', 'unavailable', 'rejected'})
    outcomes = value.get('outcomes', [])
    require(isinstance(outcomes, list) and len(outcomes) <= 21)
    for item in outcomes:
        require(isinstance(item, dict) and set(item) <= {'status', 'pull_request', 'head', 'accepted_commit_oid', 'head_changed'})
        require(item.get('status') in {'planned', 'accepted', 'receipt-pending', 'retry', 'unavailable', 'rejected'})
        require(type(item.get('pull_request')) is int and 0 < item['pull_request'] <= 2_147_483_647)
        for field in ('head', 'accepted_commit_oid'):
            if item.get(field) is not None:
                object_id(item[field])
        if 'head_changed' in item:
            require(type(item['head_changed']) is bool)
    require(type(value.get('scanned', 0)) is int and 0 <= value.get('scanned', 0) <= 200)
    require(type(value.get('scan_truncated', False)) is bool)
    return value


def main(argv=None):
    parser = argparse.ArgumentParser(description='Trusted bounded native workflow service')
    parser.add_argument('command', choices=('plan', 'publish', 'build'))
    parser.add_argument('--deployment', choices=('production', 'pilot'), required=True)
    parser.add_argument('--policy-revision', required=True)
    parser.add_argument('--toolkit-revision', required=True)
    parser.add_argument('--input'); parser.add_argument('--output', required=True)
    args = parser.parse_args(argv)
    token = os.environ.pop('GITHUB_TOKEN', None)
    try:
        policy = Policy(args.policy_revision, args.toolkit_revision, args.deployment)
        event = strict_json(_read_regular(Path(os.environ['GITHUB_EVENT_PATH']), MAX_PLAN))
        number = guard_event(policy, os.environ, event)
        run_id, attempt = int(os.environ['GITHUB_RUN_ID']), int(os.environ['GITHUB_RUN_ATTEMPT'])
        require(run_id > 0 and attempt > 0)
        if args.command == 'plan':
            value = plan_run(GitHubRead(deployment=args.deployment, read_token=token), policy,
                             pull_request=number, run_number=int(os.environ['GITHUB_RUN_NUMBER']),
                             run_id=run_id, run_attempt=attempt)
            status = value['status']
        elif args.command == 'publish':
            value = publish_run(GitHubWriter(token, deployment=args.deployment), policy,
                                strict_json(_read_regular(Path(args.input), MAX_PLAN)), run_id=run_id, run_attempt=attempt)
            status = value
        else:
            from .canonical import read_canonical
            from .distribution import build_site
            from .resolution import refresh_canonical
            status = safe_status(strict_json(_read_regular(Path(args.input), 64 * 1024)))
            api = GitHubRead(deployment=args.deployment, read_token=token)
            data = refresh_canonical(read_canonical(api, policy))
            proof = api.objects.export_bundle(data['source']['data_revision'])
            value = build_site(data, args.output, status=status, proof_bundle=proof)
        if args.command != 'build':
            from .snapshots import _open_directory, _write_regular_at
            raw = canonical(value) + b'\n'
            require(len(raw) <= (MAX_PLAN if args.command == 'plan' else 64 * 1024))
            target = Path(args.output)
            directory = _open_directory(target.parent, 'Service output', create=True)
            try:
                _write_regular_at(directory, target.name, raw)
            finally:
                os.close(directory)
        # The only summary data are fixed enums, numeric PR IDs and full OIDs.
        summary = json.dumps(status, sort_keys=True, ensure_ascii=True)
        if 'GITHUB_STEP_SUMMARY' in os.environ:
            with open(os.environ['GITHUB_STEP_SUMMARY'], 'a', encoding='utf-8') as stream:
                stream.write('Snapshot intake (PRs remain open)\n\n```json\n' + summary + '\n```\n')
        print(summary)
        return 0
    except (NativeUnavailable, ObjectUnavailable, OSError, KeyError, TypeError, ValueError, RecursionError):
        print('{"status":"unavailable"}')
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
