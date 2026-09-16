"""Synthetic authorities only; live refresh never promotes contributor JSON."""
from copy import deepcopy
from datetime import datetime, timezone
import gzip
import hashlib
import io
import json
import tarfile
import unittest
from unittest.mock import patch

from tests.test_projections import case, change, environment, hardware, software, resolution_event
from tests.test_upstream import FixtureGitHub
from projections import record_digest, recommend_action
from omarchy_knowledge.declarations import AuthorityPolicy

NOW = datetime(2026, 9, 16, 12, tzinfo=timezone.utc)
STAMP = '2026-09-16T12:00:00Z'
REPO_ID = 994093166
AUTHORITY = AuthorityPolicy('d' * 40, ((str(REPO_ID), ('42',)),))


def event():
    value = resolution_event()
    value['payload']['resolution']['upstream_url'] = 'https://github.com/omacom/omarchy/pull/7'
    predicates = value['payload']['resolution']['fixed_in']['any_of'][0]['all_of']
    predicates[0]['constraints'][0]['version'] = '4.0.4-1'
    predicates.append({**deepcopy(predicates[0]), 'selector': {
        'kind': 'software', 'component': 'package', 'name': 'omarchy-settings'}})
    return value


def declaration(value):
    return {'declaration_version': 2, 'kind': 'resolution', 'repository_id': str(REPO_ID),
            'pull_request': 7, 'event_id': value['id'], 'event_sha256': record_digest(value),
            'assertion': 'supports', 'release': {'tag': 'v4.0.4', 'fix_state': 'included'},
            'fixed_packages': [{'name': name, 'scheme': 'arch', 'minimum_version': '4.0.4-1',
                                'maximum_exclusive': None} for name in ('omarchy', 'omarchy-settings')],
            'channels': ['stable'], 'architectures': ['x86_64'], 'migration': 'unknown', 'activation': 'reboot'}


def database(names=('omarchy', 'omarchy-settings'), *, version='4.0.4-1', bad=False):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode='w') as archive:
        for name in names:
            raw = (f'%NAME%\n{name}\n\n%VERSION%\n{version}\n\n%ARCH%\nany\n\n'
                   '%BUILDDATE%\n1600000000\n\n%SHA256SUM%\n' + 'a' * 64 + '\n\n').encode()
            info = tarfile.TarInfo(('../' if bad else '') + name + '-' + version + '/desc')
            info.size = len(raw)
            archive.addfile(info, io.BytesIO(raw))
    return gzip.compress(stream.getvalue())


class GitHub(FixtureGitHub):
    def __init__(self, value):
        super().__init__()
        self.body = declaration(value)
        self.actor = 42
        self.deleted = False
        self.conflict = False
        self.truncated = False
        self.prerelease = False
        self.draft = False
        self.future = False
        self.wrong_repo = False
        self.moving = False
        self.reads = 0

    def repository(self, repo):
        return self.snapshot({'id': 1 if self.wrong_repo else REPO_ID, 'full_name': repo})

    def pull(self, repo, number):
        result = super().pull(repo, number)
        result.payload['base']['repo']['id'] = REPO_ID
        return result

    def releases(self, repo, page):
        return self.snapshot({'items': [self.release_by_tag(repo, 'v4.0.4').payload]})

    def release_by_tag(self, repo, tag):
        return self.snapshot({'id': 100, 'tag_name': tag, 'draft': self.draft,
                              'prerelease': self.prerelease,
                              'published_at': '2027-01-01T00:00:00Z' if self.future else '2026-09-15T12:00:00Z'})

    def ref(self, repo, tag):
        value = super().ref(repo, tag)
        self.reads += 1
        if self.moving and self.reads % 2 == 0:
            value.payload['object']['sha'] = 'e' * 40
        return value

    def comments(self, repo, number, page):
        items = [] if self.deleted else [self.comment(repo, 99).payload]
        if self.conflict:
            other = deepcopy(items[0])
            other.update(id=100, body=json.dumps({**self.body, 'assertion': 'revokes'}))
            items.append(other)
        if self.truncated:
            items = [dict(items[0], id=1000 * page + i) for i in range(30)]
        return self.snapshot({'items': items})

    def comment(self, repo, number):
        body = {**self.body, 'assertion': 'revokes'} if number == 100 else self.body
        return self.snapshot({'id': number, 'user': {'id': self.actor}, 'body': json.dumps(body),
                              'updated_at': '2026-09-16T11:00:00Z',
                              'issue_url': 'https://api.github.com/repos/omacom/omarchy/issues/7'})


class Catalogs:
    names = ('omarchy', 'omarchy-settings')
    outage = False
    def catalog(self, channel, architecture):
        from omarchy_knowledge.catalog import parse_catalog
        if self.outage:
            raise OSError('private transport failure')
        return parse_catalog(database(self.names), channel, architecture, checked_at=STAMP)


class LiveResolution(unittest.TestCase):
    def setUp(self):
        self.event = event()
        self.github = GitHub(self.event)
        self.catalogs = Catalogs()

    def refresh(self, authority=AUTHORITY):
        from omarchy_knowledge.resolution import refresh_upstream
        return refresh_upstream([self.event], github=self.github, catalogs=self.catalogs,
                                authority=authority, now=NOW)

    def action(self, result, version='4.0.3-1'):
        env = environment(hardware('host', 'host'), software('runtime', 'omarchy', version, 'arch'),
                          software('settings', 'package', version, 'arch', 'omarchy-settings'))
        return recommend_action(case(), change(), env, [self.event], result['observations'], now=NOW).action

    def test_authenticated_complete_chain_proposes_update_and_preserves_obligations(self):
        result = self.refresh()
        self.assertEqual(self.action(result), 'prefer-update')
        self.assertEqual(result['resolutions'][0]['inclusion_basis'], 'authenticated-maintainer-release-assertion')
        self.assertEqual(self.action(result, '4.0.4-1'), 'verify-migration')
        self.github.body['migration'] = 'no'
        self.assertEqual(self.action(self.refresh(), '4.0.4-1'), 'verify-activation')

    def test_empty_real_authority_and_wrong_bindings_cannot_recommend(self):
        self.assertEqual(self.action(self.refresh(AuthorityPolicy())), 'investigate')
        for key, wrong in [('actor', 43), ('wrong_repo', True), ('deleted', True), ('conflict', True),
                           ('truncated', True), ('outage', True), ('moving', True),
                           ('prerelease', True), ('draft', True), ('future', True)]:
            with self.subTest(key=key):
                self.github = GitHub(self.event)
                setattr(self.github, key, wrong)
                self.assertEqual(self.action(self.refresh()), 'investigate')

    def test_changed_revoked_wrong_event_and_unsupported_scope_cannot_recommend(self):
        for field, value in [('event_sha256', 'f' * 64), ('repository_id', '123'), ('pull_request', 8),
                             ('assertion', 'revokes'), ('channels', ['rc']), ('architectures', ['aarch64']),
                             ('kind', 'relevance')]:
            with self.subTest(field=field):
                self.github = GitHub(self.event)
                self.github.body[field] = value
                self.assertEqual(self.action(self.refresh()), 'investigate')

    def test_exact_and_bounds_prevent_community_ranges_gaining_authority(self):
        for mutation in ('extra', 'broad', 'upper', 'alternative'):
            self.event = event()
            predicates = self.event['payload']['resolution']['fixed_in']['any_of'][0]['all_of']
            if mutation == 'extra':
                predicates.append({**deepcopy(predicates[0]), 'selector': {'kind': 'software', 'component': 'kernel'}})
            elif mutation == 'broad':
                predicates[0]['constraints'][0]['version'] = '1.0-1'
            elif mutation == 'upper':
                predicates[0]['constraints'].append({'op': '<', 'version': '5.0-1'})
            else:
                self.event['payload']['resolution']['fixed_in']['any_of'].append({'all_of': [predicates[0]]})
            self.github = GitHub(self.event)
            self.assertEqual(self.action(self.refresh()), 'investigate', mutation)

    def test_partial_pair_missing_comparator_and_fresh_outage_replace_positive(self):
        self.assertEqual(self.action(self.refresh()), 'prefer-update')
        self.catalogs.names = ('omarchy',)
        self.assertEqual(self.action(self.refresh()), 'investigate')
        self.catalogs.names = ('omarchy', 'omarchy-settings')
        with patch('projections.ARCH_VERCMP', '/no-such-comparator'):
            self.assertEqual(self.action(self.refresh()), 'investigate')
        self.catalogs.outage = True
        result = self.refresh()
        self.assertEqual(self.action(result), 'investigate')
        self.assertNotIn('private transport', json.dumps(result))

    def test_changed_comment_on_independent_recheck_vetoes(self):
        original = self.github.comment
        reads = 0
        def changed(repo, number):
            nonlocal reads
            reads += 1
            value = original(repo, number)
            if reads > 1:
                value.payload['body'] += '\n'
            return value
        self.github.comment = changed
        self.assertEqual(self.action(self.refresh()), 'investigate')

    def test_existing_relevance_v1_support_can_coexist_with_resolution_v2(self):
        original = self.github.comments
        def with_relevance(repo, number, page):
            value = original(repo, number, page)
            other = deepcopy(value.payload['items'][0])
            body = {key: self.github.body[key] for key in ('repository_id', 'pull_request', 'event_id', 'event_sha256', 'assertion')}
            body.update(declaration_version=1, kind='relevance')
            other.update(id=100, body=json.dumps(body))
            value.payload['items'].append(other)
            return value
        self.github.comments = with_relevance
        self.assertEqual(self.action(self.refresh()), 'prefer-update')

    def test_rc_scope_and_exact_upper_bounds_are_enforced(self):
        for p in self.event['payload']['resolution']['fixed_in']['any_of'][0]['all_of']:
            p['channel'] = 'rc'
            p['constraints'].append({'op': '<', 'version': '4.1-1'})
        self.github = GitHub(self.event)
        self.github.body['channels'] = ['rc']
        self.github.prerelease = True
        for p in self.github.body['fixed_packages']:
            p['maximum_exclusive'] = '4.1-1'
        result = self.refresh()
        env = environment(hardware('host', 'host'), software('runtime', 'omarchy', '4.0.3-1', 'arch'),
                          software('settings', 'package', '4.0.3-1', 'arch', 'omarchy-settings'), channel='rc')
        self.assertEqual(recommend_action(case(), change(), env, [self.event], result['observations'], now=NOW).action,
                         'prefer-update')
        env['channel'] = 'stable'
        self.assertEqual(recommend_action(case(), change(), env, [self.event], result['observations'], now=NOW).action,
                         'investigate')

    def test_update_requires_older_installed_pair_without_any_downgrade(self):
        for p in self.event['payload']['resolution']['fixed_in']['any_of'][0]['all_of']:
            p['constraints'].append({'op': '<', 'version': '4.1-1'})
        self.github = GitHub(self.event)
        for p in self.github.body['fixed_packages']:
            p['maximum_exclusive'] = '4.1-1'
        result = self.refresh()
        for runtime, settings, expected in [
            ('4.0.3-1', '4.0.3-1', 'prefer-update'), ('4.0.3-1', '4.0.4-1', 'prefer-update'),
            ('4.1-1', '4.1-1', 'investigate'), ('4.2-1', '4.2-1', 'investigate'),
            ('4.0.3-1', '4.1-1', 'investigate'), ('4.1-1', '4.0.3-1', 'investigate'),
            ('4.0.3-1', '4.0.5-1', 'investigate'), ('4.0.3-1', None, 'investigate'),
        ]:
            with self.subTest(runtime=runtime, settings=settings):
                env = environment(hardware('host', 'host'), software('runtime', 'omarchy', runtime, 'arch'),
                                  software('settings', 'package', settings, 'arch', 'omarchy-settings'))
                self.assertEqual(recommend_action(case(), change(), env, [self.event], result['observations'], now=NOW).action,
                                 expected)

    def test_full_line_enumeration_distinguishes_first_from_earliest(self):
        result = self.refresh()
        facts = result['resolutions'][0]['source_facts']
        self.assertEqual(facts['earliest_observed_release'], 'v4.0.4')
        self.assertEqual(facts['first_published_containing_release']['tag'], 'v4.0.4')
        original = self.github.releases
        def many(repo, page):
            value = original(repo, page)
            value.payload['items'] = [dict(value.payload['items'][0], tag_name=f'v4.0.{40 - (page - 1) * 20 - i}', id=i + page * 20)
                                      for i in range(20)]
            return value
        self.github.releases = many
        result = self.refresh()
        facts = result['resolutions'][0]['source_facts']
        self.assertEqual(facts['earliest_observed_release'], 'v4.0.4')
        self.assertIsNone(facts['first_published_containing_release'])
        self.assertTrue(result['scan']['incomplete'])

    def test_unsupported_record_url_never_drives_arbitrary_requests(self):
        self.event['payload']['resolution']['upstream_url'] = 'https://attacker.invalid/omacom/omarchy/pull/7'
        self.github.pull = lambda *args: self.fail('Unsupported link drove PR request')
        self.github.comments = lambda *args: self.fail('Unsupported link drove comments request')
        self.assertEqual(self.action(self.refresh()), 'investigate')

    def test_supplier_backport_assertion_does_not_claim_original_ancestry_or_equivalence(self):
        self.github.status = 'behind'
        result = self.refresh()
        self.assertEqual(self.action(result), 'prefer-update')
        fact = result['resolutions'][0]['source_facts']
        self.assertEqual(fact['tags'][0]['ancestry'], 'no')
        self.assertEqual(fact['semantic_fix_state'], 'unknown')
        self.assertIsNone(fact['first_published_containing_release'])

    def test_empty_event_scan_still_observes_real_catalog_health(self):
        from omarchy_knowledge.resolution import refresh_upstream
        result = refresh_upstream([], github=self.github, catalogs=self.catalogs, now=NOW)
        self.assertEqual(result['observations'], [])
        self.assertEqual(result['scan']['events'], 0)
        self.assertEqual(result['catalogs'][0]['packages'][0]['catalog_checked_at'], STAMP)

    def test_live_collection_accepts_observations_taken_after_run_started(self):
        from omarchy_knowledge.resolution import refresh_upstream
        class CurrentGitHub(GitHub):
            def snapshot(self, payload):
                value = super().snapshot(payload)
                from omarchy_knowledge.github_public import PublicSnapshot
                return PublicSnapshot(value.repository, datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
                                      value.response_sha256, payload)
        class CurrentCatalogs(Catalogs):
            def catalog(self, channel, architecture):
                from omarchy_knowledge.catalog import parse_catalog
                return parse_catalog(database(), channel, architecture,
                                     checked_at=datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'))
        result = refresh_upstream([self.event], github=CurrentGitHub(self.event), catalogs=CurrentCatalogs(), authority=AUTHORITY)
        self.assertEqual(result['observations'][0]['source_inclusion']['state'], 'included')


class CatalogParsing(unittest.TestCase):
    def test_zstandard_current_catalog_and_expansion_bomb(self):
        import ctypes
        from omarchy_knowledge.catalog import parse_catalog
        lib = ctypes.CDLL('libzstd.so.1')
        lib.ZSTD_compress.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int]
        lib.ZSTD_compress.restype = ctypes.c_size_t
        def compress(raw):
            output = ctypes.create_string_buffer(len(raw) + 1024)
            size = lib.ZSTD_compress(output, len(output), raw, len(raw), 1)
            return output.raw[:size]
        raw = compress(gzip.decompress(database()))
        self.assertEqual(len(parse_catalog(raw, 'stable', 'x86_64', checked_at=STAMP)['packages']), 2)
        with self.assertRaises(ValueError):
            parse_catalog(compress(b'\0' * (8 * 1024 * 1024 + 1)), 'stable', 'x86_64', checked_at=STAMP)
        with patch('ctypes.CDLL', side_effect=OSError('unsupported library')), self.assertRaises(ValueError):
            parse_catalog(raw, 'stable', 'x86_64', checked_at=STAMP)

    def test_old_build_any_arch_and_fresh_catalog_are_separate(self):
        from omarchy_knowledge.catalog import parse_catalog
        result = parse_catalog(database(), 'stable', 'x86_64', checked_at=STAMP)
        package = result['packages'][0]
        self.assertEqual(package['catalog_checked_at'], STAMP)
        self.assertEqual(package['built_at'], '2020-09-13T12:26:40Z')
        self.assertEqual(package['package_architecture'], 'any')
        self.assertEqual(package['architecture'], 'x86_64')

    def test_malformed_traversal_decompression_and_entry_limits_fail_closed(self):
        from omarchy_knowledge.catalog import parse_catalog
        for raw in (b'bad', database(bad=True), gzip.compress(b'\0' * (8 * 1024 * 1024 + 1))):
            with self.subTest(size=len(raw)), self.assertRaises(ValueError):
                parse_catalog(raw, 'stable', 'x86_64', checked_at=STAMP)
        with self.assertRaises(ValueError):
            parse_catalog(database(), 'edge', 'x86_64', checked_at=STAMP)
        with patch('omarchy_knowledge.catalog.MAX_ENTRIES', 1), self.assertRaises(ValueError):
            parse_catalog(database(), 'stable', 'x86_64', checked_at=STAMP)

    def test_catalog_transport_redirects_and_compressed_limit_fail_closed(self):
        from omarchy_knowledge.catalog import CatalogPublicRead, MAX_COMPRESSED
        from omarchy_knowledge.github_public import PublicReadUnavailable
        class Connection:
            raw = database()
            status = 200
            def __init__(inner, host, timeout):
                self.assertEqual(host, 'pkgs.omarchy.org')
            def request(inner, method, path, headers):
                self.assertEqual((method, path), ('GET', '/stable/x86_64/omarchy.db'))
                self.assertEqual(set(headers), {'User-Agent'})
            def getresponse(inner):
                response = io.BytesIO(inner.raw)
                response.status = inner.status
                return response
            def close(inner):
                pass
        reader = CatalogPublicRead(connection_factory=Connection)
        self.assertEqual(reader.catalog('stable', 'x86_64')['status'], 'observed')
        for status, raw in [(302, b''), (200, b'x' * (MAX_COMPRESSED + 1))]:
            Connection.status, Connection.raw = status, raw
            with self.assertRaises(PublicReadUnavailable):
                reader.catalog('stable', 'x86_64')


class ResolutionDeclaration(unittest.TestCase):
    def test_current_body_digest_actor_event_and_update_are_all_bound(self):
        from omarchy_knowledge.declarations import IDENTITY, validate_resolution_declaration
        value = event()
        raw = json.dumps(declaration(value))
        observed = {'repository_id': str(REPO_ID), 'pull_request': 7, 'comment_id': '99',
                    'actor_account_id': '42', 'body_sha256': hashlib.sha256(raw.encode()).hexdigest(),
                    'updated_at': '2026-09-16T11:00:00Z', 'retrieved_at': STAMP, 'deleted': False, 'body': raw}
        identity = {key: observed[key] for key in IDENTITY}
        arguments = {'event_id': value['id'], 'event_sha256': record_digest(value), 'now': NOW}
        self.assertIsNotNone(validate_resolution_declaration(identity, observed, AUTHORITY, **arguments))
        for key, replacement in [('body', raw + ' '), ('updated_at', STAMP), ('deleted', True),
                                 ('actor_account_id', '43'), ('repository_id', '1'), ('comment_id', '100'),
                                 ('retrieved_at', '2026-09-15T12:00:00Z')]:
            self.assertIsNone(validate_resolution_declaration(identity, {**observed, key: replacement}, AUTHORITY, **arguments), key)


class PublicAdapterContract(unittest.TestCase):
    def test_supported_pr_contract_and_fixed_paginated_endpoints(self):
        from omarchy_knowledge.github_public import GitHubPublicRead
        from omarchy_knowledge.upstream import OmarchyProbeV1, observe_omarchy
        class Connection:
            def __init__(inner, host, timeout):
                self.assertEqual(host, 'api.github.com')
            def request(inner, method, path, headers):
                self.assertEqual(method, 'GET')
                inner.path, inner.version = path, headers['X-GitHub-Api-Version']
            def getresponse(inner):
                if inner.path == '/repos/omacom/omarchy/pulls/7':
                    payload = GitHub(event()).pull('omacom/omarchy', 7).payload
                    if inner.version != '2022-11-28':
                        payload.pop('merge_commit_sha')
                elif inner.path in ('/repos/omacom/omarchy/releases?per_page=20&page=1',
                                    '/repos/omacom/omarchy/issues/7/comments?per_page=30&page=2'):
                    payload = []
                elif inner.path == '/repos/omacom/omarchy/issues/comments/9000000000':
                    payload = {'id': 9000000000}
                else:
                    self.fail('Unexpected public endpoint: ' + inner.path)
                response = io.BytesIO(json.dumps(payload).encode())
                response.status = 200
                return response
            def close(inner):
                pass
        reader = GitHubPublicRead(connection_factory=Connection)
        result = observe_omarchy(OmarchyProbeV1('omacom/omarchy', REPO_ID, 7), reader)
        self.assertIs(result['pull']['merged'], True)
        self.assertEqual(reader.releases('omacom/omarchy', 1).payload, {'items': []})
        self.assertEqual(reader.comments('omacom/omarchy', 7, 2).payload, {'items': []})
        self.assertEqual(reader.comment('omacom/omarchy', 9000000000).payload['id'], 9000000000)
        with self.assertRaises(ValueError):
            reader.releases('omacom/omarchy', 3)


class CanonicalRefresh(unittest.TestCase):
    from tests.test_admission import TreeAdmission
    setUp = TreeAdmission.setUp
    git = TreeAdmission.git
    commit = TreeAdmission.commit

    def test_sync_seals_fresh_resolution_then_outage_replaces_it_offline(self):
        from tests.test_coordinator import FakeAPI
        from omarchy_knowledge.canonical import sync
        from omarchy_knowledge.coordinator import Policy
        from omarchy_knowledge.discovery import explain_record, search_snapshot
        from omarchy_knowledge.snapshots import load_cache, import_snapshot
        from omarchy_knowledge import resolution
        value = event()
        for predicate in value['payload']['resolution']['fixed_in']['any_of'][0]['all_of']:
            predicate['constraints'].append({'op': '<', 'version': '4.1-1'})
        self.base = self.commit({f"records/{r['type']}s/{r['id']}.json": ('100644', json.dumps(r).encode())
                                 for r in (case(), change(), value)}, self.base)
        api = FakeAPI(self)
        github, catalogs = GitHub(value), Catalogs()
        for package in github.body['fixed_packages']:
            package['maximum_exclusive'] = '4.1-1'
        env = environment(hardware('host', 'host'), software('runtime', 'omarchy', '4.0.3-1', 'arch'),
                          software('settings', 'package', '4.0.3-1', 'arch', 'omarchy-settings'))
        original = resolution.refresh_upstream
        def refresh(records):
            return original(records, github=github, catalogs=catalogs, authority=AUTHORITY, now=NOW)
        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return cls(2026, 9, 16, 12, tzinfo=timezone.utc)
        cache = self.root / 'cache'
        with patch.object(resolution, 'refresh_upstream', side_effect=refresh), patch.object(resolution, 'AUTHORITY', AUTHORITY), \
                patch('projections.datetime', Clock):
            sync(api, Policy('a' * 40, 'b' * 40), cache)
            self.assertEqual(explain_record(cache, change()['id'], environment=env)['projections'][0]
                             ['recommendation']['action'], 'prefer-update')
            for runtime, settings in [('4.1-1', '4.1-1'), ('4.2-1', '4.2-1'), ('4.0.3-1', '4.2-1')]:
                newer = environment(hardware('host', 'host'), software('runtime', 'omarchy', runtime, 'arch'),
                                    software('settings', 'package', settings, 'arch', 'omarchy-settings'))
                self.assertEqual(explain_record(cache, change()['id'], environment=newer)['projections'][0]
                                 ['recommendation']['action'], 'investigate')
                self.assertEqual(search_snapshot(cache, 'input', environment=newer)['results'][0]['changes'][0]
                                 ['recommendation']['action'], 'investigate')
            self.assertEqual(load_cache(cache).canonical['upstream']['version'], 2)
            from omarchy_knowledge.snapshots import cache_status
            old = cache_status(cache, now=datetime(2026, 9, 16, 14, tzinfo=timezone.utc))
            self.assertTrue(old['upstream_stale'])
            github.outage = True
            sync(api, Policy('a' * 40, 'b' * 40), cache)
            self.assertEqual(explain_record(cache, change()['id'], environment=env)['projections'][0]
                             ['recommendation']['action'], 'investigate')
            self.assertEqual(load_cache(cache).canonical['upstream']['status'], 'unknown')
            github.outage = False
            sync(api, Policy('a' * 40, 'b' * 40), cache)
            with patch.object(resolution, 'AUTHORITY', AuthorityPolicy()):
                self.assertEqual(explain_record(cache, change()['id'], environment=env)['projections'][0]
                                 ['recommendation']['action'], 'investigate')
            snapshot = load_cache(cache)
            import_snapshot(snapshot.path, cache)
            self.assertEqual(explain_record(cache, change()['id'], environment=env)['projections'][0]
                             ['recommendation']['action'], 'investigate')


if __name__ == '__main__':
    unittest.main()
