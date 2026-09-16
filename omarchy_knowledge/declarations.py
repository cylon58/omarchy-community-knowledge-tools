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


def validate_declaration(declaration, trusted_api_comment, policy, *, event_id, event_sha256, now):
    """Return projection-compatible relevance only; package/source state is absent.

    Empty authority is default. The API snapshot must be at most one hour old.
    The expected comment identity and exact raw body digest come from trusted code.
    """
    unknown = {"state": "unknown", "basis": "unknown"}
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
        body = knowledge._parse_json(raw)
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
