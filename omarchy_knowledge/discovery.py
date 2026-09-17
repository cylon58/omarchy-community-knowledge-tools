"""Stable structured search/show/explain projections over a local snapshot."""
from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping

from projections import match_applicability, recommend_action, summarize_evidence, record_digest, _event_targets_change
from .snapshots import load_cache, snapshot_status
from .resolution import authenticated_observations


_INTENTS = {"corrective", "optional", "undetermined", "all"}
_RELATION_KINDS = {'correction', 'withdrawal', 'dispute', 'supersession'}
_MAX_RELATED_EVENTS = 64


def _corpus(cache: str | Path):
    snapshot = load_cache(cache)
    records = list(snapshot.records)
    return snapshot, records, {record["id"]: record for record in records}


def _record_scope(record, records):
    """Direct case/change cohort and its resolution assertions, no graph closure."""
    scope = {record['id']}
    if record['type'] == 'case':
        scope.update(r['id'] for r in records if r['type'] in {'change', 'report'}
                     and r['payload']['case_id'] == record['id'])
    elif record['type'] == 'change':
        scope.add(record['payload']['case_id'])
        scope.update(r['id'] for r in records if r['type'] == 'report'
                     and r['payload']['case_id'] == record['payload']['case_id']
                     and r['payload'].get('change_id') in {None, record['id']})
    if record['type'] == 'change':
        # Share recommendation relevance: additional targeted pairs do not make
        # a resolution (or a dispute of it) irrelevant to this pair.
        scope.update(r['id'] for r in records
                     if _event_targets_change(r, record['payload']['case_id'], record['id']))
    elif record['type'] == 'case':
        scope.update(r['id'] for r in records if r['type'] == 'event'
                     and r['payload']['event_kind'] == 'upstream-resolution'
                     and {'type': 'case', 'id': record['id']} in r['payload']['targets'])
    return scope


def _related_events(records, scope, trusted_receipts):
    """Display attributed directional claims; incoming claims request inspection.

    The entire already-bounded corpus supplies the safety signal. The display cap
    never turns a later omitted warning into permission for automatic advice.
    Receipts authenticate account attribution only, not authority to withdraw data.
    """
    selected = []
    requires_review = False
    for event in records:
        if event['type'] != 'event' or event['payload']['event_kind'] not in _RELATION_KINDS:
            continue
        payload = event['payload']
        context = sorted(scope.intersection(t['id'] for t in payload['targets']))
        if not context:
            continue
        affected = [payload['relation']['to']['id']] if payload['relation']['to']['id'] in scope else []
        requires_review |= bool(affected)
        selected.append((event, context, affected))
    selected.sort(key=lambda item: (item[0]['created_at'], item[0]['id']), reverse=True)
    displayed = []
    for event, context, affected in selected[:_MAX_RELATED_EVENTS]:
        payload = event['payload']
        digest = record_digest(event)
        actors = {r['actor']['account_id'] for r in trusted_receipts
                  if r['record_id'] == event['id'] and r['record_sha256'] == digest}
        actor = next(iter(actors)) if len(actors) == 1 else None
        displayed.append({'event_id': event['id'], 'event_kind': payload['event_kind'],
                          'created_at': event['created_at'], 'reason': payload['reason'],
                          'relation': payload['relation'], 'targets': payload['targets'],
                          'supporting_reports': payload.get('supporting_reports', []),
                          'supporting_links': payload.get('supporting_links', []),
                          'provenance': event['provenance'], 'claim_status': 'community-claim',
                          'attribution': 'authenticated-account' if actor else 'unattributed-claim',
                          'actor_account_id': actor, 'context_record_ids': context, 'affected_record_ids': affected})
    return {'events': displayed, 'total': len(selected), 'truncated': len(selected) > _MAX_RELATED_EVENTS,
            'requires_review': requires_review}


def _event_review(item, related):
    item['related_events'] = related
    if related['requires_review']:
        recommendation = item['recommendation']
        recommendation['community_event_review_required'] = True
        recommendation['reasons'] = ['Inspect linked community correction, dispute, withdrawal or supersession claims before acting; these claims do not establish authoritative withdrawal.',
                                     *recommendation.get('reasons', ())][:64]
        if recommendation['action'] != 'not-applicable':
            recommendation['action'] = 'investigate'
            if recommendation.get('workaround') != 'retain-existing':
                recommendation['workaround'] = 'defer-new'
    return item


def _projection(case, change, records, environment, canonical=None):
    result = match_applicability(change["payload"]["applicability"], environment)
    evidence = asdict(summarize_evidence(
        records, canonical['receipts'] if canonical else [], case_id=case["id"], change_id=change["id"]
    ))
    evidence["trust_basis"] = "canonical-api-receipts" if canonical else "no-authenticated-receipts-configured"
    observations = authenticated_observations(canonical)
    recommendation = asdict(recommend_action(
        case, change, environment,
        [record for record in records if record["type"] == "event"], observations,
    ))
    recommendation["trust_basis"] = ("canonical-live-upstream-envelope" if observations
                                     else "no-authenticated-upstream-observations-configured")
    recommendation['inclusion_basis'] = ('authenticated-maintainer-release-assertion'
        if recommendation['upstream_state'] in {'available', 'installed'} else 'unknown')
    return _event_review({
        "change_id": change["id"], "intent": change["payload"]["intent"],
        "predicates": change["payload"]["applicability"],
        "applicability": asdict(result), "evidence": evidence,
        "recommendation": recommendation,
    }, _related_events(records, _record_scope(change, records), canonical['receipts'] if canonical else []))


def search_snapshot(cache: str | Path, query: str, *, environment: Mapping[str, Any] | None = None,
                    intent: str = "corrective", compact: bool = False,
                    limit: int = 5, method: str = 'substring', broad: bool = False) -> dict[str, Any]:
    if intent not in _INTENTS:
        raise ValueError("Unknown intent selection")
    if type(limit) is not int or not 1 <= limit <= 50:
        raise ValueError('Search limit must be between 1 and 50')
    if method not in {'substring', 'ranked'} or (broad and method != 'ranked'):
        raise ValueError('Invalid retrieval method/broad combination')
    if not isinstance(query, str) or len(query) > 512:
        raise ValueError('Query must be at most 512 characters')
    snapshot, records, _ = _corpus(cache)
    terms = query.casefold().split()
    cases = [record for record in records if record["type"] == "case"]
    changes = [record for record in records if record["type"] == "change"]
    ranking = None
    if method == 'ranked':
        from .retrieval import rank_cases
        ranking = {row['case_id']: (position, row) for position, row in enumerate(
            rank_cases(records, query, intent=intent, broad=broad, local_index=snapshot.local_index))}
    # Bound expensive projection to the chosen cases, retaining the whole
    # validated corpus for every selected case's evidence and event guards.
    candidates = []
    for case in cases:
        payload = case['payload']
        if intent != 'all' and payload['intent'] != intent:
            continue
        search_text = ' '.join((payload['title'], payload['observed'],
                               payload['expectation']['text'], *payload['domains'])).casefold()
        if (case['id'] in ranking if ranking is not None
                else all(term in search_text for term in terms)):
            candidates.append(case)
    candidates.sort(key=lambda case: ranking[case['id']][0] if ranking is not None else case['id'])
    total_matches = len(candidates)
    cases = candidates[:limit] if compact else candidates
    results = []
    for case in cases:
        payload = case["payload"]
        projected, incompatible = [], []
        for change in changes:
            if change["payload"]["case_id"] != case["id"]:
                continue
            if environment is None:
                projected.append(_event_review({
                    "change_id": change["id"], "intent": change["payload"]["intent"],
                    "predicates": change["payload"]["applicability"],
                    "applicability": {"state": "not-evaluated", "reasons": [],
                                      "missing": ["local environment"]},
                    "evidence": {**asdict(summarize_evidence(records, snapshot.canonical['receipts'] if snapshot.canonical else [],
                                                            case_id=case['id'], change_id=change['id'])),
                                 "trust_basis": "canonical-api-receipts" if snapshot.canonical else "no-authenticated-receipts-configured"},
                    "recommendation": {"action": "investigate", "trust_basis":
                                       "no-authenticated-upstream-observations-configured"},
                }, _related_events(records, _record_scope(change, records), snapshot.canonical['receipts'] if snapshot.canonical else [])))
                continue
            item = _projection(case, change, records, environment, snapshot.canonical)
            if item["applicability"]["state"] == "does-not-match":
                incompatible.append(change["id"])
            else:
                projected.append(item)
        rank = {"matches": 0, "insufficient-information": 1, "not-evaluated": 2}
        projected.sort(key=lambda item: (rank[item["applicability"]["state"]], item["change_id"]))
        results.append({"case_id": case["id"], "title": payload["title"],
                        "intent": payload["intent"], "domains": payload["domains"],
                        'case_evidence': asdict(summarize_evidence(records,
                            snapshot.canonical['receipts'] if snapshot.canonical else [],
                            case_id=case['id'], change_id=None)),
                        'has_failure_or_partial_reports': any(r['type'] == 'report'
                            and r['payload']['case_id'] == case['id']
                            and r['payload']['result'] in {'failure', 'partial'} for r in records),
                        'related_events': _related_events(records, _record_scope(case, records),
                                                          snapshot.canonical['receipts'] if snapshot.canonical else []),
                        "changes": projected, "incompatible_change_ids": sorted(incompatible)})
        results[-1]['case_evidence']['trust_basis'] = (
            'canonical-api-receipts' if snapshot.canonical else 'no-authenticated-receipts-configured')
        if ranking is not None:
            results[-1]['retrieval'] = {key: value for key, value in ranking[case['id']][1].items() if key != 'case_id'}
    results.sort(key=lambda item: ranking[item['case_id']][0] if ranking is not None else item['case_id'])
    response = {"query": query, "intent": intent, "data_revision": snapshot.manifest["data_revision"],
            "cache_status": snapshot_status(snapshot),
            "results": results,
            "trust": "canonical-api-receipts" if snapshot.canonical else "attributed-claims-only"}
    response['retrieval'] = {'method': method, 'broad': broad,
        'meaning': 'Candidate relevance only; lexical coverage is not evidence or applicability. No matches does not establish that no solution exists.'}
    if compact:
        short = compact_search(response, limit=limit)
        short['total_matches'] = total_matches
        short['truncated'] = total_matches > limit
        return short
    return response


def _compact_events(related):
    return {key: related[key] for key in ('total', 'truncated', 'requires_review')} | {
        'event_ids': [event['event_id'] for event in related['events'][:8]],
        'event_ids_truncated': related['total'] > 8,
    }


def compact_cache_status(status: Mapping[str, Any]) -> dict[str, Any]:
    """Keep freshness/authority limits visible; full catalogs belong in status."""
    upstream = status['upstream']
    result = {key: status[key] for key in (
        'snapshot_created_at', 'source_updated_at', 'age_seconds', 'stale', 'upstream_stale',
        'offline_disclosure')}
    result['upstream'] = {key: upstream[key] for key in (
        'status', 'observed_at', 'fresh_until', 'authority_configured') if key in upstream}
    result['full_details_command'] = 'status --cache CACHE'
    return result


def compact_search(response: Mapping[str, Any], *, limit: int = 5) -> dict[str, Any]:
    """An advisory shortlist, not an abbreviated authorization to apply a change.

    Preserve aggregate failure/partial counts and recommendation guards. Bulky
    individual observations, predicates and catalogs remain available via the
    existing show/explain/status commands; the summary explicitly requires them.
    """
    if type(limit) is not int or not 1 <= limit <= 50:
        raise ValueError('Search limit must be between 1 and 50')
    compact_status = compact_cache_status(response['cache_status'])
    results = []
    for result in response['results'][:limit]:
        item = {key: result[key] for key in ('case_id', 'title', 'intent', 'domains')}
        if 'retrieval' in result:
            item['retrieval'] = result['retrieval']
        item['related_events'] = _compact_events(result['related_events'])
        item['incompatible_change_ids'] = result['incompatible_change_ids'][:8]
        item['incompatible_change_count'] = len(result['incompatible_change_ids'])
        item['changes_total'] = len(result['changes'])
        item['changes_truncated'] = len(result['changes']) > 5
        item['case_evidence'] = ({key: value for key, value in result['case_evidence'].items()
                                 if key not in {'observations', 'case_id', 'change_id'}}
                                if result['case_evidence']['observations'] else {'report_count': 0})
        item['safety'] = {
            'has_failure_or_partial_reports': result['has_failure_or_partial_reports'] or any(
                counts.get(outcome, 0) > 0
                for change in result['changes']
                for key, counts in change['evidence'].items()
                if key.endswith('_counts') and isinstance(counts, dict)
                for outcome in ('failure', 'partial')),
            'community_event_review_required': result['related_events']['requires_review'] or any(
                change['related_events']['requires_review'] for change in result['changes']),
            'recommendation_actions': sorted({change['recommendation']['action'] for change in result['changes']}),
        }
        item['changes'] = []
        for change in result['changes'][:5]:
            summary = {key: change[key] for key in ('change_id', 'intent', 'applicability', 'recommendation')}
            summary['evidence'] = {key: value for key, value in change['evidence'].items()
                                   if key not in {'observations', 'case_id', 'change_id'}}
            summary['related_events'] = _compact_events(change['related_events'])
            item['changes'].append(summary)
        results.append(item)
    return {key: response[key] for key in ('query', 'intent', 'data_revision', 'trust')} | {
        'retrieval': response.get('retrieval', {'method': 'substring'}),
        'view': 'shortlist', 'detail_required_before_action': True,
        'next_step': 'Use show for the selected case/change and explain with local environment before acting; status contains full upstream scan details.',
        'cache_status': compact_status, 'total_matches': len(response['results']),
        'truncated': len(response['results']) > limit, 'limit': limit, 'results': results,
    }


def show_record(cache: str | Path, identifier: str) -> dict[str, Any]:
    snapshot, records, by_id = _corpus(cache)
    if identifier not in by_id:
        raise ValueError("Unknown canonical record ID")
    return {"data_revision": snapshot.manifest["data_revision"], "record": by_id[identifier],
            'linked_report_ids': sorted(record['id'] for record in records if record['type'] == 'report'
                and (record['payload']['case_id'] == identifier or record['payload'].get('change_id') == identifier)),
            'related_events': _related_events(records, _record_scope(by_id[identifier], records),
                                              snapshot.canonical['receipts'] if snapshot.canonical else []),
            "cache_status": snapshot_status(snapshot),
            "trust": "canonical-api-receipts" if snapshot.canonical else "attributed-claims-only"}


def explain_record(cache: str | Path, identifier: str, *, environment: Mapping[str, Any]) -> dict[str, Any]:
    snapshot, records, by_id = _corpus(cache)
    target = by_id.get(identifier)
    if target is None:
        raise ValueError("Unknown canonical record ID")
    if target["type"] == "case":
        changes = [item for item in records if item["type"] == "change"
                   and item["payload"]["case_id"] == identifier]
        projections = [_projection(target, item, records, environment, snapshot.canonical) for item in changes]
    elif target["type"] == "change":
        case = by_id[target["payload"]["case_id"]]
        projections = [_projection(case, target, records, environment, snapshot.canonical)]
    else:
        projections = []
    return {"record_id": identifier, "record_type": target["type"],
            'related_events': _related_events(records, _record_scope(target, records),
                                              snapshot.canonical['receipts'] if snapshot.canonical else []),
            "cache_status": snapshot_status(snapshot),
            "data_revision": snapshot.manifest["data_revision"], "projections": projections,
            "trust": "canonical-api-receipts" if snapshot.canonical else "attributed-claims-only"}
