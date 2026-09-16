"""Offline maintainer declarations against caller-authenticated API observations.

The caller must obtain the comment from the exact repository/PR via authenticated
provider identity, and revalidate after edits/deletions/policy changes. Nothing in
the comment, Git author strings, or this parser authenticates its own input.
"""
from dataclasses import dataclass
import hashlib
import re

import knowledge
from .upstream import _timestamp


@dataclass(frozen=True)
class AuthorityPolicy:
    revision: str = ""
    repository_accounts: tuple[tuple[str, tuple[str, ...]], ...] = ()


IDENTITY = {"repository_id", "pull_request", "comment_id", "actor_account_id", "body_sha256", "updated_at"}


def _id(value):
    return isinstance(value, str) and re.fullmatch(r"[1-9][0-9]{0,19}", value)


def _digest(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value)


def _authenticated_body(declaration, trusted_api_comment, policy, *, now):
    unknown = None
    try:
        if not isinstance(policy, AuthorityPolicy) or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", policy.revision):
            return unknown
        authorities = {}
        if type(policy.repository_accounts) not in (tuple, list) or len(policy.repository_accounts) > 64:
            return unknown
        for repo, accounts in policy.repository_accounts:
            if (not _id(repo) or repo in authorities or type(accounts) not in (tuple, list)
                    or not 1 <= len(accounts) <= 64 or not all(_id(a) for a in accounts)
                    or len(set(accounts)) != len(accounts)):
                return unknown
            authorities[repo] = frozenset(accounts)
        if set(declaration) != IDENTITY or set(trusted_api_comment) != IDENTITY | {"body", "retrieved_at", "deleted"}:
            return unknown
        if not all(declaration[k] == trusted_api_comment[k] for k in IDENTITY):
            return unknown
        if not all(_id(declaration[k]) for k in ("repository_id", "comment_id", "actor_account_id")):
            return unknown
        if (type(declaration["pull_request"]) is not int or not 1 <= declaration["pull_request"] <= 2_147_483_647
                or declaration["actor_account_id"] not in authorities.get(declaration["repository_id"], ())
                or trusted_api_comment["deleted"] is not False):
            return unknown
        retrieved = _timestamp(trusted_api_comment["retrieved_at"])
        if not 0 <= (now - retrieved).total_seconds() <= 3600 or _timestamp(declaration["updated_at"]) > retrieved:
            return unknown
        raw = trusted_api_comment["body"]
        if not isinstance(raw, str) or len(raw.encode("utf-8")) > 65536:
            return unknown
        if not _digest(declaration["body_sha256"]) or hashlib.sha256(raw.encode()).hexdigest() != declaration["body_sha256"]:
            return unknown
        return knowledge._parse_json(raw)
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError):
        return unknown


def validate_declaration(declaration, trusted_api_comment, policy, *, event_id, event_sha256, now):
    """Relevance v1 only, against exact caller-authenticated current API identity."""
    unknown = {"state": "unknown", "basis": "unknown"}
    try:
        body = _authenticated_body(declaration, trusted_api_comment, policy, now=now)
        if set(body) != {"declaration_version", "kind", "repository_id", "pull_request", "event_id", "event_sha256", "assertion"}:
            return unknown
        if (type(body["declaration_version"]) is not int or body["declaration_version"] != 1 or body["kind"] != "relevance"
                or body["repository_id"] != declaration["repository_id"] or type(body["pull_request"]) is not int
                or body["pull_request"] != declaration["pull_request"] or body["event_id"] != event_id
                or not re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}", event_id)
                or not _digest(event_sha256) or body["event_sha256"] != event_sha256):
            return unknown
        if body["assertion"] == "revokes":
            return {"state": "unsupported", "basis": "unknown"}
        if body["assertion"] != "supports":
            return unknown
        return {"state": "upstream-supported", "basis": "authenticated-maintainer-declaration",
                "actor_account_id": declaration["actor_account_id"], "authority_policy_revision": policy.revision,
                "declaration_sha256": declaration["body_sha256"]}
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError):
        return unknown


def validate_resolution_declaration(declaration, trusted_api_comment, policy, *, event_id, event_sha256, now):
    """Resolution v2 supplier assertion. No release/package proof is supplied here.

    All fields are mandatory, including for revocations. The coordinator treats
    any authorized malformed candidate for the event as unknown/conflicting.
    """
    try:
        body = _authenticated_body(declaration, trusted_api_comment, policy, now=now)
        if not isinstance(body, dict) or set(body) != {
                'declaration_version', 'kind', 'repository_id', 'pull_request', 'event_id', 'event_sha256',
                'assertion', 'release', 'fixed_packages', 'channels', 'architectures', 'migration', 'activation'}:
            return None
        if (type(body['declaration_version']) is not int or body['declaration_version'] != 2
                or body['kind'] != 'resolution' or body['repository_id'] != declaration['repository_id']
                or type(body['pull_request']) is not int or body['pull_request'] != declaration['pull_request']
                or body['event_id'] != event_id or not isinstance(event_id, str)
                or not re.fullmatch(r'[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}', event_id)
                or not _digest(event_sha256) or body['event_sha256'] != event_sha256
                or body['assertion'] not in {'supports', 'revokes'}):
            return None
        release = body['release']
        if (set(release) != {'tag', 'fix_state'} or release['fix_state'] != 'included'
                or not isinstance(release['tag'], str)
                or not re.fullmatch(r'v[0-9]{1,5}\.[0-9]{1,5}\.[0-9]{1,5}(?:rc[0-9]{1,5})?', release['tag'])):
            return None
        for field, allowed in [('channels', {'stable', 'rc'}), ('architectures', {'x86_64'})]:
            values = body[field]
            if (type(values) is not list or not values or len(values) > len(allowed)
                    or not all(type(v) is str and v in allowed for v in values) or len(set(values)) != len(values)):
                return None
        if body['migration'] not in {'yes', 'no', 'unknown'} or body['activation'] not in {
                'none', 'relogin', 'reboot', 'service-restart', 'manual', 'unknown'}:
            return None
        packages = body['fixed_packages']
        if type(packages) is not list or not 1 <= len(packages) <= 2:
            return None
        names = set()
        for package in packages:
            if (set(package) != {'name', 'scheme', 'minimum_version', 'maximum_exclusive'}
                    or package['name'] not in {'omarchy', 'omarchy-settings'} or package['name'] in names
                    or package['scheme'] != 'arch'):
                return None
            names.add(package['name'])
            for key in ('minimum_version', 'maximum_exclusive'):
                value = package[key]
                if key == 'maximum_exclusive' and value is None:
                    continue
                if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._+~:-]{0,199}', value):
                    return None
        return {'body': body, 'identity': dict(declaration), 'authority_policy_revision': policy.revision}
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError):
        return None
