"""Bounded workflow caller. Tokens never enter candidate data or dependency setup."""
import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import json
import os
from pathlib import Path

from .coordinator import (MAX_PLAN, NativeNotReady, NativeRejected, Policy,
                          authenticated_imported_heads, canonical, load_plan,
                          object_id, prepare, publish, reconcile,
                          require)
from .fair_intake import (CandidateEvaluation, EvaluationOutcome, IntakeCursor,
                          scan)
from .github_native import GitHubRead, GitHubWriter, NativeUnavailable, strict_json
from .git_objects import ObjectUnavailable
from .intake_status import (CursorHealth, IntakeScan, PublicStatusKind,
                            validate_safe_status)
from .snapshots import _read_regular

MAX_ATTEMPTS = 20
ENVELOPE_VERSION = 2
_COUNTER_FIELDS = (
    'page_fetches', 'rows_returned', 'rows_consumed', 'evaluations', 'closed',
    'imported', 'rejected', 'not_ready', 'plans', 'cursor_drifts',
)
_PLAN_FIELDS = {
    'version', 'stage', 'run_id', 'run_attempt', 'deployment', 'trusted_lane',
    'prior_state', 'before_cursor', 'proposed_cursor', 'prior_cursor_health',
    'selected_action', 'scan_outcome', 'stop_reason', 'scan_time', 'counters',
    'plan', 'status',
}
_PUBLISH_FIELDS = (_PLAN_FIELDS - {'plan'}) | {
    'pages_publishable', 'cursor', 'cursor_health',
}


def _zero_counters():
    return {field: 0 for field in _COUNTER_FIELDS}


def _timestamp(value):
    if not isinstance(value, str) or not value.endswith('Z') or len(value) > 40:
        return False
    try:
        parsed = datetime.fromisoformat(value[:-1] + '+00:00')
    except ValueError:
        return False
    return parsed.utcoffset() is not None and parsed.utcoffset().total_seconds() == 0


def _utc_now():
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def _observe_prior(api):
    try:
        prior = api.intake_status()
    except NativeUnavailable:
        return 'unavailable', None, None
    if prior.kind is PublicStatusKind.CURRENT:
        require(prior.cursor is not None and prior.cursor_health is not None)
        return 'current', prior.cursor, prior.cursor_health
    require(prior.kind is PublicStatusKind.LEGACY
            and prior.cursor is None and prior.cursor_health is None)
    return 'legacy', None, None


def _initial_cursor():
    return IntakeCursor(1, 1, 0, None, 0, None)


def _initial_health():
    return CursorHealth(1, None, 0)


def _validate_plan_envelope(value, *, policy, run_id, run_attempt,
                            trusted_lane, published=False):
    require(isinstance(value, dict) and set(value) == _PLAN_FIELDS)
    require(type(value['version']) is int and value['version'] == ENVELOPE_VERSION
            and value['stage'] == 'plan'
            and type(value['run_id']) is int and value['run_id'] == run_id
            and type(value['run_attempt']) is int
            and value['run_attempt'] == run_attempt
            and value['deployment'] == policy.deployment
            and value['trusted_lane'] == trusted_lane
            and trusted_lane in {'direct', 'scheduled'}
            and value['prior_state'] in {'current', 'legacy', 'unavailable'}
            and value['selected_action'] in {'after', 'before', 'withhold'}
            and (value['scan_time'] is None or _timestamp(value['scan_time'])))
    scan = IntakeScan.from_mapping({
        'version': 1,
        **{field: value[field] for field in (
            'trusted_lane', 'prior_state', 'selected_action', 'scan_outcome',
            'stop_reason', 'counters',
        )},
    })
    before = (None if value['before_cursor'] is None else
              IntakeCursor.from_mapping(value['before_cursor']))
    proposed = (None if value['proposed_cursor'] is None else
                IntakeCursor.from_mapping(value['proposed_cursor']))
    health = (None if value['prior_cursor_health'] is None else
              CursorHealth.from_mapping(value['prior_cursor_health']))
    if value['prior_state'] == 'current':
        require(before is not None and health is not None)
    elif value['prior_state'] == 'legacy' and trusted_lane == 'scheduled':
        require(before == _initial_cursor() and health == _initial_health())
    else:
        require(before is None and health is None)
    if trusted_lane == 'direct':
        require(proposed == before and value['scan_time'] is None
                and value['selected_action']
                == ('before' if value['prior_state'] == 'current' else 'withhold'))
    else:
        require(value['prior_state'] in {'current', 'legacy'}
                and before is not None and proposed is not None
                and health is not None and value['scan_time'] is not None
                and value['selected_action'] == 'after')
    status = validate_safe_status(value['status'])
    require(value['plan'] is None or isinstance(value['plan'], dict))
    if not published:
        require(set(status) == {
            'status', 'outcomes', 'scanned', 'scan_truncated',
        })
        if trusted_lane == 'scheduled':
            require(status['scanned'] == scan.counters['rows_consumed']
                    and status['scan_truncated']
                    == (scan.stop_reason in {
                        'fetch-limit', 'preparation-limit',
                    }))
            counts = {
                name: sum(item['status'] == name
                          for item in status['outcomes'])
                for name in ('rejected', 'not-ready', 'planned')
            }
            require(len(status['outcomes']) == scan.counters['evaluations']
                    and counts['rejected'] == scan.counters['rejected']
                    and counts['not-ready'] == scan.counters['not_ready']
                    and counts['planned'] == scan.counters['plans'])
            if scan.scan_outcome == 'planned':
                require(value['plan'] is not None
                        and status['status'] == 'planned'
                        and status['outcomes'][-1]['status'] == 'planned')
                loaded = load_plan(canonical(value['plan']))
                require(status['outcomes'][-1].get('pull_request')
                        == loaded['pull_request']
                        and status['outcomes'][-1].get('head')
                        == loaded['head'])
            else:
                require(value['plan'] is None and status['status'] == 'idle')
        else:
            require((value['plan'] is not None)
                    == (status['status'] == 'planned'))
    return value, scan, before, proposed, health


def _publish_envelope(planned, *, status, selected_action, cursor, health,
                      pages_publishable):
    return {
        **{field: planned[field] for field in _PLAN_FIELDS - {'plan', 'stage',
                                                              'selected_action',
                                                              'status'}},
        'version': ENVELOPE_VERSION,
        'stage': 'publish',
        'selected_action': selected_action,
        'status': safe_status(status),
        'pages_publishable': pages_publishable,
        'cursor': None if cursor is None else cursor.to_mapping(),
        'cursor_health': None if health is None else health.to_mapping(),
    }


def public_intake_scan(envelope):
    """Project only the strict safe scan telemetry carried to public status."""
    return IntakeScan.from_mapping({
        'version': 1,
        **{field: envelope[field] for field in (
            'trusted_lane', 'prior_state', 'selected_action', 'scan_outcome',
            'stop_reason', 'counters',
        )},
    }).to_mapping()


def _advanced_health(batch, before, proposed, prior_health):
    drifts = batch['counters']['cursor_drifts']
    if drifts:
        return CursorHealth(
            1, prior_health.last_progress_at,
            min(2_147_483_647, prior_health.consecutive_drift_runs + 1),
        )
    progressed = proposed != before
    return CursorHealth(
        1, batch['scan_time'] if progressed else prior_health.last_progress_at,
        0,
    )


def _validate_publish_envelope(value, *, policy, run_id, run_attempt,
                               trusted_lane):
    require(isinstance(value, dict) and set(value) == _PUBLISH_FIELDS)
    plan_shape = {
        **{field: value[field] for field in _PLAN_FIELDS - {'plan'}},
        'stage': 'plan', 'plan': None,
        'selected_action': ('after' if trusted_lane == 'scheduled' else
                            'before' if value.get('prior_state') == 'current'
                            else 'withhold'),
    }
    _planned, scan, before, proposed, prior_health = _validate_plan_envelope(
        plan_shape, policy=policy, run_id=run_id, run_attempt=run_attempt,
        trusted_lane=trusted_lane, published=True,
    )
    require(value['stage'] == 'publish'
            and type(value['pages_publishable']) is bool)
    status = safe_status(value['status'])
    selected = value['selected_action']
    cursor = (None if value['cursor'] is None else
              IntakeCursor.from_mapping(value['cursor']))
    health = (None if value['cursor_health'] is None else
              CursorHealth.from_mapping(value['cursor_health']))
    if status['status'] in {'accepted', 'receipt-pending'}:
        require(status.get('outcomes')
                and status['outcomes'][-1]['status'] == status['status']
                and set(status['outcomes'][-1]) == {
                    'status', 'pull_request', 'head', 'accepted_commit_oid',
                    'head_changed',
                }
                and status['outcomes'][-1]['accepted_commit_oid'] is not None)
    if selected == 'after':
        require(trusted_lane == 'scheduled'
                and value['prior_state'] in {'current', 'legacy'}
                and status['status'] in {'accepted', 'idle'}
                and ((scan.scan_outcome == 'planned'
                      and status['status'] == 'accepted')
                     or (scan.scan_outcome == 'no-eligible'
                         and status['status'] == 'idle'))
                and cursor == proposed
                and health == _advanced_health(value, before, proposed,
                                               prior_health)
                and value['pages_publishable'])
    elif selected == 'before':
        require(value['prior_state'] in {'current', 'legacy'}
                and cursor == before and health == prior_health
                and value['pages_publishable'])
        if trusted_lane == 'scheduled':
            require(status['status'] in {'retry', 'receipt-pending'}
                    and (scan.scan_outcome == 'planned'
                         or status['status'] == 'retry'))
    else:
        require(selected == 'withhold' and trusted_lane == 'direct'
                and value['prior_state'] in {'legacy', 'unavailable'}
                and cursor is None and health is None
                and not value['pages_publishable'])
    return value, cursor, health


def _validated_proof(api, policy, data):
    """Export only the current graph, then replay it with object I/O forbidden."""
    from .canonical import read_canonical
    from .github_native import APIObjects
    revision = object_id(data['source']['data_revision'])
    proof = api.objects.export_bundle(revision)

    class Replay:
        def repository(self):
            return {'id': policy.repository_id, 'full_name': policy.repository,
                    'default_branch': 'main'}

        def branch(self):
            return revision

        def git_commit(self, _oid):
            raise NativeUnavailable()

        git_tree = git_commit
        git_blob = git_commit

        def commit_info(self, oid):
            return self.objects.info(oid)

    replay = Replay()
    replay.objects = APIObjects(replay, total_deadline=api.objects.total_deadline)
    replay.objects.load_bundle(proof, revision)
    checked = read_canonical(replay, policy, now=data['source']['verified_at'])
    # REST tree arrays need not arrive in raw Git-tree order. Receipt order is
    # nonsemantic, but every receipt (including duplicates) must replay exactly.
    comparable = lambda value: {**value, 'receipts': sorted(value['receipts'], key=canonical)}
    require(comparable(checked) == comparable(data))
    return proof


def _publication_update(api, data, proof, previous_proof):
    """Build one direct update from the untouched preceding full proof."""
    from .object_bundle import BundleUnavailable, decode, decode_seed
    from .update_pack import (MAX_MANIFEST, UpdatePackUnavailable,
                              decode_manifest, generate,
                              validate_retained_target)

    try:
        target_head = object_id(data['source']['data_revision'])
        target_objects = decode(proof, target_head)
        base_head, base_objects = decode_seed(previous_proof)
        if base_head == target_head:
            manifest_raw = api._pages('canonical-update.json', MAX_MANIFEST)
            manifest = decode_manifest(
                manifest_raw,
                expected_deployment=api.deployment,
                expected_target_head=target_head,
            )
            pack = api._pages('canonical-update.bundle', manifest.pack_size)
            validate_retained_target(
                manifest_raw, pack,
                deployment=api.deployment,
                expected_target_head=target_head,
                target_objects=target_objects,
            )
            return manifest_raw, pack
        return generate(
            api.deployment, base_head, base_objects, target_head, target_objects)
    except (BundleUnavailable, UpdatePackUnavailable, NativeUnavailable,
            KeyError, TypeError, ValueError, RecursionError):
        return None


def _publisher_seed(api):
    """Warm from one fixed prior full proof while retaining its exact bounded bytes."""
    if not hasattr(api, '_pages'):
        api.seed_canonical()
        return None
    from .object_bundle import MAX_COMPRESSED_BUNDLE
    try:
        raw = api._pages('canonical-objects.bundle', MAX_COMPRESSED_BUNDLE)
    except (NativeUnavailable, OSError, TypeError, ValueError):
        return None
    try:
        api.objects.load_seed(raw)
    except (NativeUnavailable, OSError, TypeError, ValueError):
        pass
    return raw


def _publisher_build(api, policy, *, now=None):
    """Read, validate, then optionally accelerate one static publication."""
    from .canonical import read_canonical

    previous_proof = _publisher_seed(api)
    data = read_canonical(api, policy, now=now)
    proof = _validated_proof(api, policy, data)
    update = _publication_update(api, data, proof, previous_proof)
    return data, proof, update


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


def plan_run(api, policy, *, pull_request=None, run_number=0, run_id=1,
             run_attempt=1, now=None):
    """At most 20 preparations, one admission plan. Rotate the bounded 200-PR window."""
    require(type(run_number) is int and run_number >= 0)
    require(type(run_id) is int and run_id > 0 and type(run_attempt) is int and run_attempt > 0)
    prior_state, before, prior_health = _observe_prior(api)
    lane = 'direct' if pull_request is not None else 'scheduled'
    selected_action = ('after' if lane == 'scheduled' else
                       'before' if prior_state == 'current' else 'withhold')
    status = {'status': 'idle', 'outcomes': [],
              'scanned': 1 if lane == 'direct' else 0,
              'scan_truncated': False}
    plan = None
    transition = None
    if lane == 'scheduled':
        if prior_state == 'unavailable':
            raise NativeUnavailable()
        if prior_state == 'legacy':
            before, prior_health = _initial_cursor(), _initial_health()
        require(_timestamp(now))
        imported = authenticated_imported_heads(api, policy)

        def evaluate(row):
            nonlocal plan
            try:
                candidate = prepare(api, policy, row.pull_request)
            except NativeNotReady:
                status['outcomes'].append({
                    'status': 'not-ready',
                    'pull_request': row.pull_request,
                })
                return CandidateEvaluation(EvaluationOutcome.NOT_READY)
            except NativeRejected:
                status['outcomes'].append({
                    'status': 'rejected',
                    'pull_request': row.pull_request,
                })
                return CandidateEvaluation(EvaluationOutcome.REJECTED)
            plan = candidate
            status['outcomes'].append({
                'status': 'planned', 'pull_request': row.pull_request,
                'head': candidate['head'],
            })
            return CandidateEvaluation(EvaluationOutcome.PLAN, candidate)

        transition = scan(
            before.to_mapping(), imported_heads=imported,
            fetch_page=api.intake_page, evaluate=evaluate, now=now,
        )
        counters = asdict(transition.counters)
        status['scanned'] = transition.counters.rows_consumed
        status['scan_truncated'] = transition.stop_reason.value in {
            'fetch-limit', 'preparation-limit',
        }
        status['status'] = 'planned' if plan is not None else 'idle'
    else:
      try:
        require(type(pull_request) is int and 0 < pull_request <= 2_147_483_647)
        numbers = [pull_request]
        for number in numbers[:MAX_ATTEMPTS]:
            try:
                plan = prepare(api, policy, number)
            except NativeNotReady:
                status['outcomes'].append({'status': 'not-ready',
                                           'pull_request': number})
            except NativeRejected:
                status['outcomes'].append({'status': 'rejected', 'pull_request': number})
            except (NativeUnavailable, ObjectUnavailable):
                status['outcomes'].append({'status': 'unavailable', 'pull_request': number})
            else:
                status['outcomes'].append({'status': 'planned', 'pull_request': number, 'head': plan['head']})
                break
        status['status'] = ('planned' if plan else
                            status['outcomes'][-1]['status'])
      except (NativeUnavailable, ObjectUnavailable, KeyError, TypeError, ValueError, RecursionError):
        status['status'] = 'unavailable'
      counters = _zero_counters()
    batch = {
        'version': ENVELOPE_VERSION, 'stage': 'plan', 'run_id': run_id,
        'run_attempt': run_attempt, 'deployment': policy.deployment,
        'trusted_lane': lane, 'prior_state': prior_state,
        'before_cursor': None if before is None else before.to_mapping(),
        'proposed_cursor': (None if before is None else
                            (transition.proposed_after.to_mapping()
                             if transition is not None else before.to_mapping())),
        'prior_cursor_health': (None if prior_health is None
                                else prior_health.to_mapping()),
        'selected_action': selected_action,
        'scan_outcome': ('direct' if transition is None
                         else transition.outcome.value),
        'stop_reason': ('direct' if transition is None
                        else transition.stop_reason.value),
        'scan_time': now if transition is not None else None,
        'counters': counters, 'plan': plan,
        'status': safe_status(status),
    }
    require(len(canonical(batch)) <= MAX_PLAN)
    return batch


def publish_run(api, policy, batch, *, run_id=1, run_attempt=1,
                trusted_lane=None):
    """One reconciled CAS attempt; pending or retry always terminates this run."""
    batch, _scan, before, proposed, prior_health = _validate_plan_envelope(
        batch, policy=policy, run_id=run_id, run_attempt=run_attempt,
        trusted_lane=trusted_lane,
    )
    status = safe_status(batch['status'])
    result = reconcile(api, policy)
    if result.status != 'complete':
        status = {**status, 'status': 'retry'}
    elif batch['plan'] is not None:
        plan = load_plan(canonical(batch['plan']))
        result = publish(api, policy, plan)
        outcome = {'status': result.status,
                   'pull_request': plan['pull_request'], 'head': plan['head'],
                   'accepted_commit_oid': result.accepted_commit_oid,
                   'head_changed': result.head_changed}
        status = {**status, 'status': result.status,
                  'outcomes': [o for o in status['outcomes']
                               if o['status'] != 'planned'] + [outcome]}
    elif batch['trusted_lane'] == 'scheduled':
        require(batch['scan_outcome'] == 'no-eligible')
        status = {**status, 'status': 'idle'}
    if batch['trusted_lane'] == 'direct':
        selected_action = ('before' if batch['prior_state'] == 'current'
                           else 'withhold')
    elif status['status'] in {'retry', 'receipt-pending'}:
        selected_action = 'before'
    else:
        require(status['status'] in {'accepted', 'idle'})
        selected_action = 'after'
    cursor = (proposed if selected_action == 'after' else before
              if selected_action == 'before' else None)
    health = (_advanced_health(batch, before, proposed, prior_health)
              if selected_action == 'after' else prior_health
              if selected_action == 'before' else None)
    return _publish_envelope(
        batch, status=status, selected_action=selected_action,
        cursor=cursor, health=health,
        pages_publishable=selected_action != 'withhold',
    )


def safe_status(value):
    """Public statuses contain only enums and validated numeric/immutable identities."""
    try:
        return validate_safe_status(value)
    except ValueError as error:
        raise NativeUnavailable() from error


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
        trusted_lane = 'direct' if number is not None else 'scheduled'
        run_id, attempt = int(os.environ['GITHUB_RUN_ID']), int(os.environ['GITHUB_RUN_ATTEMPT'])
        require(run_id > 0 and attempt > 0)
        if args.command == 'plan':
            api = GitHubRead(deployment=args.deployment, read_token=token)
            api.seed_canonical()
            value = plan_run(api, policy,
                             pull_request=number, run_number=int(os.environ['GITHUB_RUN_NUMBER']),
                             run_id=run_id, run_attempt=attempt, now=_utc_now())
            status = value['status']
        elif args.command == 'publish':
            api = GitHubWriter(token, deployment=args.deployment)
            api.seed_canonical()
            value = publish_run(api, policy,
                                strict_json(_read_regular(Path(args.input), MAX_PLAN)),
                                run_id=run_id, run_attempt=attempt,
                                trusted_lane=trusted_lane)
            status = value['status']
        else:
            from .distribution import build_site
            from .resolution import refresh_canonical
            envelope, cursor, health = _validate_publish_envelope(
                strict_json(_read_regular(Path(args.input), 64 * 1024)),
                policy=policy, run_id=run_id, run_attempt=attempt,
                trusted_lane=trusted_lane,
            )
            require(envelope['pages_publishable'])
            status = envelope['status']
            api = GitHubRead(deployment=args.deployment, read_token=token)
            data, proof, update = _publisher_build(api, policy)
            data = refresh_canonical(data)
            manifest, pack = update if update is not None else (None, None)
            value = build_site(
                data, args.output, status=status, proof_bundle=proof,
                intake_cursor=cursor.to_mapping(),
                cursor_health=health.to_mapping(),
                intake_scan=public_intake_scan(envelope),
                update_manifest=manifest, update_bundle=pack,
            )
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
