"""Bounded canonical-event refresh and supplier-assertion join.

Only the reviewed canonical reader calls this lane before sealing provenance.
Public envelopes are display data; passing one to this module does not authenticate
it. Source ancestry and supplier assertions deliberately retain separate meanings.
"""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re

import knowledge
import projections
from .catalog import ARCHITECTURES, CHANNELS, PACKAGES, CatalogPublicRead
from .declarations import AuthorityPolicy, IDENTITY, validate_resolution_declaration
from .github_public import GitHubPublicRead
from .upstream import ERRORS, OmarchyProbeV1, _snapshot, _timestamp, observe_omarchy

REPOSITORY = 'omacom/omarchy'
REPOSITORY_ID = 994093166
MAX_EVENTS = 8
# Governed code, never a CLI/config/record-controlled list. No real actor is bound.
AUTHORITY = AuthorityPolicy()
PROVIDER_REVISION = 'omarchy-resolution-supplier-v2.1'
_LINK = re.compile(r'https://github\.com/(?:omacom|basecamp)/omarchy/pull/([1-9][0-9]{0,9})(?:#issuecomment-[1-9][0-9]{0,19})?')
_VERSION = re.compile(r'v([0-9]{1,5})\.([0-9]{1,5})\.([0-9]{1,5})(?:rc([0-9]{1,5}))?')


def _stamp(now):
    return now.isoformat().replace('+00:00', 'Z')


def _at(now):
    return now or datetime.now(timezone.utc)


def _policy_digest(authority):
    return hashlib.sha256(json.dumps({'provider': PROVIDER_REVISION, 'revision': authority.revision,
                                     'accounts': authority.repository_accounts}, sort_keys=True).encode()).hexdigest()


def _unknown(event, now):
    now = _at(now)
    return {'observation_version': 1, 'kind': 'upstream-resolution', 'event_id': event['id'],
            'event_sha256': projections.record_digest(event),
            'repository': {'provider': 'github', 'repository_id': str(REPOSITORY_ID)},
            'observed_at': _stamp(now), 'fresh_until': _stamp(now + timedelta(hours=1)),
            'relevance': {'state': 'unknown', 'basis': 'unknown'}, 'source_inclusion': {'state': 'unknown'},
            'packages': [], 'migration': {'required': 'unknown'}, 'activation': {'required': 'unknown'}}


def _release_order(tag):
    match = _VERSION.fullmatch(tag)
    if not match:
        raise ValueError('Unsupported release version grammar')
    major, minor, patch, rc = match.groups()
    return (int(major), int(minor), int(patch), 1 if rc is None else 0, int(rc or 0))


def _releases(github, probe, now):
    releases, sources, complete = {}, [], False
    try:
        for page in (1, 2):
            data, source = _snapshot(github.releases(REPOSITORY, page), probe, now)
            sources.append(source)
            items = data['items']
            if type(items) is not list or len(items) > 20:
                raise ValueError('Release page shape')
            for item in items:
                tag = item['tag_name']
                if (type(item['id']) is not int or item['id'] <= 0 or not isinstance(tag, str)
                        or tag in releases or type(item['draft']) is not bool or type(item['prerelease']) is not bool):
                    raise ValueError('Release identity')
                if item['published_at'] is not None and _timestamp(item['published_at']) > _at(now):
                    raise ValueError('Future release')
                releases[tag] = {key: item[key] for key in ('id', 'tag_name', 'draft', 'prerelease', 'published_at')}
            if len(items) < 20:
                complete = True
                break
        return {'status': 'observed', 'releases': list(releases.values()), 'sources': sources,
                'complete': complete, 'enumeration': 'github-published-release-objects-max40'}
    except ERRORS:
        return {'status': 'unknown', 'releases': [], 'sources': sources, 'complete': False,
                'diagnostic': 'RELEASE_SCAN_UNAVAILABLE'}


def _comment(snapshot, probe, number, now):
    data, source = _snapshot(snapshot, probe, now)
    if (type(data['id']) is not int or not 0 < data['id'] <= 2**63 - 1
            or type(data['user']['id']) is not int or not 0 < data['user']['id'] <= 2**63 - 1
            or data['issue_url'] != f'https://api.github.com/repos/{REPOSITORY}/issues/{number}'
            or not isinstance(data['body'], str) or len(data['body'].encode()) > 65536
            or _timestamp(data['updated_at']) > _timestamp(source['retrieved_at'])):
        raise ValueError('Comment identity/body shape')
    return {'repository_id': str(REPOSITORY_ID), 'pull_request': number, 'comment_id': str(data['id']),
            'actor_account_id': str(data['user']['id']), 'body': data['body'], 'updated_at': data['updated_at'],
            'body_sha256': hashlib.sha256(data['body'].encode()).hexdigest(),
            'retrieved_at': source['retrieved_at'], 'deleted': False}, source


def _declarations(github, probe, event, authority, now):
    """Complete bounded comment scan; conflicting/revoked/edited candidates veto."""
    from .github_public import PublicSnapshot
    candidates, sources, seen, complete, invalid = [], [], set(), False, False
    try:
        accounts = dict(authority.repository_accounts).get(str(REPOSITORY_ID), ())
        for page in (1, 2):
            snapshot = github.comments(REPOSITORY, probe.pull_request, page)
            data, source = _snapshot(snapshot, probe, now)
            sources.append(source)
            items = data['items']
            if type(items) is not list or len(items) > 30:
                raise ValueError('Comment page shape')
            for item in items:
                observed, _ = _comment(PublicSnapshot(REPOSITORY, snapshot.retrieved_at,
                                                     snapshot.response_sha256, item), probe, probe.pull_request, now)
                if observed['comment_id'] in seen:
                    raise ValueError('Duplicate comment across scan')
                seen.add(observed['comment_id'])
                if observed['actor_account_id'] not in accounts:
                    continue
                try:
                    body = knowledge._parse_json(observed['body'])
                except (ValueError, TypeError, RecursionError):
                    # Non-declaration prose is inert. An edited sole declaration
                    # consequently supplies no current supporting candidate.
                    continue
                if not isinstance(body, dict) or body.get('event_id') != event['id']:
                    continue
                if body.get('declaration_version') == 1 and body.get('kind') == 'relevance' and body.get('assertion') == 'supports':
                    # Relevance-only support supplies no resolution conditions.
                    # A revocation remains a conservative veto for this event.
                    continue
                identity = {key: observed[key] for key in IDENTITY}
                accepted = validate_resolution_declaration(identity, observed, authority,
                    event_id=event['id'], event_sha256=projections.record_digest(event), now=_at(now))
                if accepted is None:
                    invalid = True
                else:
                    candidates.append((accepted, observed))
            if len(items) < 30:
                complete = True
                break
        if not complete or invalid or not candidates:
            return None, {'complete': complete, 'sources': sources, 'state': 'unknown'}
        # Any revocation or disagreement vetoes; no last-writer-wins authority.
        bodies = {json.dumps(item[0]['body'], sort_keys=True) for item in candidates}
        if len(bodies) != 1 or candidates[0][0]['body']['assertion'] != 'supports':
            return None, {'complete': complete, 'sources': sources, 'state': 'revoked-or-conflicting'}
        for accepted, prior in candidates:
            current, source = _comment(github.comment(REPOSITORY, int(prior['comment_id'])), probe, probe.pull_request, now)
            sources.append(source)
            if any(current[key] != prior[key] for key in IDENTITY):
                raise ValueError('Declaration changed during refresh')
        return candidates[0][0], {'complete': True, 'sources': sources, 'state': 'supported'}
    except ERRORS:
        return None, {'complete': False, 'sources': sources, 'state': 'unknown'}


def _boundaries(event, body):
    """Exact one AND alternative only. Unsupported alternatives cannot piggyback."""
    alternatives = event['payload']['resolution']['fixed_in']['any_of']
    if len(alternatives) != 1:
        return None
    predicates = alternatives[0]['all_of']
    if len(predicates) != len(body['fixed_packages']):
        return None
    bindings = {}
    for predicate in predicates:
        if set(predicate) != {'kind', 'selector', 'scheme', 'constraints', 'channel', 'architecture'}:
            return None
        selector = predicate['selector']
        if selector == {'kind': 'software', 'component': 'omarchy'}:
            name = 'omarchy'
        elif selector.get('kind') == 'software' and selector.get('component') == 'package' and set(selector) == {'kind', 'component', 'name'}:
            name = selector['name']
        else:
            return None
        package = next((p for p in body['fixed_packages'] if p['name'] == name), None)
        if package is None or name in bindings or predicate['kind'] != 'software' or predicate['scheme'] != 'arch':
            return None
        expected = [{'op': '>=', 'version': package['minimum_version']}]
        if package['maximum_exclusive'] is not None:
            expected.append({'op': '<', 'version': package['maximum_exclusive']})
        if (sorted(predicate['constraints'], key=lambda p: p['op']) != sorted(expected, key=lambda p: p['op'])
                or predicate['channel'] not in body['channels'] or predicate['architecture'] not in body['architectures']):
            return None
        bindings[name] = predicate
    scopes = {(p['channel'], p['architecture']) for p in bindings.values()}
    return bindings if len(scopes) == 1 else None


def _first_release(facts, discovery):
    # First SOURCE-containing release in a major/minor line, never first semantic
    # repair. Unknown earlier ancestry or incomplete discovery precludes first.
    tags = facts['tags']
    positive = [t for t in tags if t['ancestry'] == 'yes' and t.get('publication', {}).get('state') == 'published']
    earliest = min(positive, key=lambda t: _release_order(t['tag'])) if positive else None
    facts['earliest_observed_release'] = earliest['tag'] if earliest else None
    facts['first_published_containing_release'] = None
    facts['history_scope'] = 'bounded-discovered-releases'
    if earliest is None or not discovery['complete']:
        return
    line = _release_order(earliest['tag'])[:2]
    eligible = [r for r in discovery['releases'] if _VERSION.fullmatch(r['tag_name'])
                and _release_order(r['tag_name'])[:2] == line and not r['draft'] and r['published_at']]
    checked = {t['tag']: t for t in tags}
    before = [r['tag_name'] for r in eligible if _release_order(r['tag_name']) < _release_order(earliest['tag'])]
    if (earliest['tag'] in {r['tag_name'] for r in eligible}
            and all(tag in checked and checked[tag]['ancestry'] == 'no'
                    and checked[tag].get('publication', {}).get('state') == 'published' for tag in before)):
        facts['first_published_containing_release'] = {'tag': earliest['tag'], 'line': f'{line[0]}.{line[1]}',
            'basis': 'source-ancestry-only', 'ordering': 'numeric-major-minor-patch-rc', 'earlier_checked': before,
            'enumeration_complete': True}


def _resolve(event, github, catalogs, discovery, authority, now):
    observation = _unknown(event, now)
    result = {'event_id': event['id'], 'event_sha256': observation['event_sha256'],
              'inclusion_basis': 'unknown', 'observation': observation, 'diagnostics': []}
    link = _LINK.fullmatch(event['payload']['resolution']['upstream_url'])
    if link is None or int(link[1]) > 2_147_483_647:
        result['diagnostics'] = ['UNSUPPORTED_UPSTREAM_LINK']
        return result
    probe = OmarchyProbeV1(REPOSITORY, REPOSITORY_ID, int(link[1]))
    declaration, scan = _declarations(github, probe, event, authority, now)
    result['declaration_scan'] = scan
    tags = sorted([r['tag_name'] for r in discovery['releases'] if _VERSION.fullmatch(r['tag_name'])],
                  key=_release_order, reverse=True)
    if declaration:
        tag = declaration['body']['release']['tag']
        tags = [tag] + [t for t in tags if t != tag]
    probe = OmarchyProbeV1(REPOSITORY, REPOSITORY_ID, probe.pull_request, tuple(tags[:8]))
    facts = observe_omarchy(probe, github, now=now)
    _first_release(facts, discovery)
    result['source_facts'] = facts
    result['tags_scan_complete'] = len(tags) <= 8 and discovery['complete']
    if not declaration or facts['pull'].get('merged') is not True:
        result['diagnostics'] = ['CURRENT_DECLARATION_OR_MERGE_UNSUPPORTED']
        return result
    body = declaration['body']
    result['declaration'] = declaration
    bindings = _boundaries(event, body)
    tag = next((t for t in facts['tags'] if t['tag'] == body['release']['tag']), {})
    publication = tag.get('publication', {})
    if (not bindings or publication.get('state') != 'published'
            or type(publication.get('release_id')) is not int or publication['release_id'] <= 0):
        result['diagnostics'] = ['BOUNDARY_OR_PUBLISHED_RELEASE_UNSUPPORTED']
        return result
    packages, freshness = [], [observation['fresh_until'], facts['pull']['fresh_until']]
    freshness.extend(source['fresh_until'] for source in scan['sources'])
    for name, predicate in bindings.items():
        channel, architecture = predicate['channel'], predicate['architecture']
        if channel == 'stable' and (publication['prerelease'] or _VERSION.fullmatch(tag['tag'])[4] is not None):
            result['diagnostics'] = ['PRERELEASE_NOT_STABLE']
            return result
        catalog = next((c for c in catalogs if c['channel'] == channel and c['architecture'] == architecture), {})
        if catalog.get('status') != 'observed':
            result['diagnostics'] = ['CATALOG_UNAVAILABLE']
            return result
        current = next((p for p in catalog['packages'] if p['name'] == name), None)
        comparisons = [(projections._compare_arch(current['version'], c['version']), c['op'])
                       for c in predicate['constraints']] if current else []
        if not comparisons or not all(value is not None and projections._compare_operator(value, op)
                                      for value, op in comparisons):
            result['diagnostics'] = ['PACKAGE_BOUNDARY_UNAVAILABLE_OR_COMPARATOR_UNSUPPORTED']
            return result
        freshness.append(catalog['fresh_until'])
        packages.append({'selector': predicate['selector'], 'scheme': 'arch', 'version': current['version'],
                         'channel': channel, 'architecture': architecture, 'state': 'available'})
    freshness.extend(tag[key]['fresh_until'] for key in ('ref_source', 'ref_recheck_source'))
    freshness.append(publication['fresh_until'])
    observation.update(relevance={'state': 'upstream-supported', 'basis': 'authenticated-maintainer-declaration',
        'actor_account_id': declaration['identity']['actor_account_id'], 'authority_policy_revision': authority.revision,
        'declaration_sha256': declaration['identity']['body_sha256']},
        source_inclusion={'state': 'included', 'commit_oid': {'algorithm': 'sha1' if len(tag['commit_oid']) == 40 else 'sha256',
                                                           'hex': tag['commit_oid']}}, packages=packages,
        migration={'required': body['migration']}, activation={'required': body['activation']},
        fresh_until=min(freshness, key=_timestamp))
    observation['observed_at'] = _stamp(_at(now))
    projections.validate_upstream_observation(observation)
    result['inclusion_basis'] = 'authenticated-maintainer-release-assertion'
    return result


def refresh_upstream(records, *, github=None, catalogs=None, authority=AUTHORITY, now=None):
    """Fresh bounded scan of canonical records; never accepts public observations.

    Injected readers and authority are trusted offline test/operator seams. The
    public sync and workflow expose neither as user input. A failed read generates
    fresh unknown status; this function never receives previous favorable data.
    """
    started = _at(now)
    github = github if github is not None else GitHubPublicRead()
    catalogs = catalogs if catalogs is not None else CatalogPublicRead()
    result = {'version': 2, 'status': 'unknown', 'observed_at': _stamp(started),
              'fresh_until': _stamp(started + timedelta(hours=1)), 'provider_revision': PROVIDER_REVISION,
              'authority_policy_sha256': _policy_digest(authority), 'authority_configured': bool(authority.repository_accounts),
              'repository': {'name': REPOSITORY, 'repository_id': str(REPOSITORY_ID)},
              'catalogs': [], 'observations': [], 'resolutions': []}
    events = [r for r in records if r['type'] == 'event' and r['payload']['event_kind'] == 'upstream-resolution']
    result['scan'] = {'events': min(len(events), MAX_EVENTS), 'event_total': len(events), 'incomplete': len(events) > MAX_EVENTS}
    for channel in CHANNELS:
        for architecture in ARCHITECTURES:
            try:
                catalog = catalogs.catalog(channel, architecture)
                if (catalog['channel'] != channel or catalog['architecture'] != architecture
                        or catalog['status'] != 'observed'
                        or not 0 <= (_at(now) - _timestamp(catalog['catalog_checked_at'])).total_seconds() <= 3600):
                    raise ValueError('Catalog source scope/freshness')
                result['catalogs'].append(catalog)
            except ERRORS:
                result['catalogs'].append({'channel': channel, 'architecture': architecture, 'status': 'unknown',
                                           'observed_at': _stamp(_at(now)), 'packages': [], 'diagnostic': 'CATALOG_UNAVAILABLE'})
    probe = OmarchyProbeV1(REPOSITORY, REPOSITORY_ID, 1)
    try:
        identity, source = _snapshot(github.repository(REPOSITORY), probe, now)
        if type(identity['id']) is not int or identity['id'] != REPOSITORY_ID or identity['full_name'] != REPOSITORY:
            raise ValueError('Official numeric repository identity')
        result['repository_source'] = source
        discovery = _releases(github, probe, now)
        result['release_discovery'] = discovery
        for event in events[:MAX_EVENTS]:
            try:
                resolution = _resolve(event, github, result['catalogs'], discovery, authority, now)
            except ERRORS:
                resolution = {'event_id': event['id'], 'event_sha256': projections.record_digest(event),
                              'inclusion_basis': 'unknown', 'observation': _unknown(event, now),
                              'diagnostics': ['RESOLUTION_UNAVAILABLE']}
            result['resolutions'].append(resolution)
            resolution['observation']['fresh_until'] = min(resolution['observation']['fresh_until'],
                                                           source['fresh_until'], key=_timestamp)
            result['observations'].append(resolution['observation'])
            facts = resolution.get('source_facts', {})
            if facts.get('pull', {}).get('merged') == 'unknown' or any(t.get('diagnostic') for t in facts.get('tags', [])):
                result['scan']['incomplete'] = True
        result['scan']['incomplete'] |= not discovery['complete'] or any(
            not r.get('declaration_scan', {}).get('complete', True) or not r.get('tags_scan_complete', True)
            for r in result['resolutions'])
        result['status'] = 'partial' if result['scan']['incomplete'] or any(c['status'] != 'observed' for c in result['catalogs']) else 'refreshed'
    except ERRORS:
        result['scan']['incomplete'] = True
        result['observations'] = [_unknown(e, now) for e in events[:MAX_EVENTS]]
        result['diagnostic'] = 'OFFICIAL_REPOSITORY_UNAVAILABLE'
    return result


def refresh_canonical(data):
    """Internal sync/build hook, called only after canonical ledger verification."""
    data['upstream'] = refresh_upstream(data['records'])
    return data


def authenticated_observations(canonical):
    """Use only a locally sealed canonical envelope, under the CURRENT code policy.

    Authority removal or provider-policy changes invalidate even a fresh older seal.
    The caller must have loaded canonical via load_cache; ordinary JSON is inert.
    """
    if not canonical:
        return []
    upstream = canonical['upstream']
    if (upstream.get('version') != 2 or upstream.get('provider_revision') != PROVIDER_REVISION
            or upstream.get('authority_policy_sha256') != _policy_digest(AUTHORITY)):
        return []
    return upstream['observations']
