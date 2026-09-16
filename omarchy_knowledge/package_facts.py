"""Exact package observations from an explicitly injected, reviewed provider.

There is deliberately no live package extractor. Provider code, not record JSON,
must establish source-revision mappings without executing remote build recipes.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta
import re
from typing import Protocol

from .git_objects import valid_oid
from .github_public import _repo


@dataclass(frozen=True)
class PackageQueryV1:
    package_name: str
    channel: str
    architecture: str
    source_commit_oid: str


@dataclass(frozen=True)
class PackageSnapshotV1:
    provider_id: str
    repository: str
    repository_id: int
    package_name: str
    channel: str
    architecture: str
    state: str
    version: str | None
    version_scheme: str | None
    source_commit_oid: str | None
    retrieved_at: str
    metadata_at: str
    response_sha256: str


class PackagePublicRead(Protocol):
    """Caller-authenticated provider; returning JSON cannot authenticate itself."""
    provider_id: str

    def package(self, repository: str, repository_id: int, query: PackageQueryV1) -> PackageSnapshotV1: ...


def _matches(pattern, value):
    return isinstance(value, str) and re.fullmatch(pattern, value) is not None


def validate_package_queries(queries):
    if type(queries) not in (tuple, list) or len(queries) > 24:
        raise ValueError("Invalid package query collection")
    seen = set()
    for query in queries:
        if (type(query) is not PackageQueryV1 or not _matches(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,127}", query.package_name)
                or query.channel not in {"stable", "testing", "development", "rc", "edge"}
                or not _matches(r"[a-z0-9_+-]{1,40}", query.architecture) or query.architecture == "unknown"
                or not valid_oid(query.source_commit_oid) or query in seen):
            raise ValueError("Invalid or duplicate package query")
        seen.add(query)


def _time(value):
    if not _matches(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z", value):
        raise ValueError("Invalid package timestamp")
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def observe_package(repository, repository_id, query, provider, *, now):
    """Available requires exact source proof; absent is scoped catalog absence.

    Availability is independent of fix semantics. A version string alone cannot
    prove source inclusion. Every call evaluates its own snapshot, with no reuse
    of older favorable facts after an outage, correction, or disappearance.
    """
    _repo(repository)
    if type(repository_id) is not int or not 0 < repository_id <= 2**63 - 1:
        raise ValueError("Invalid package source repository identity")
    validate_package_queries((query,))
    result = {"selector": {"kind": "software", "component": "package", "name": query.package_name},
              "repository": repository, "repository_id": str(repository_id), "channel": query.channel,
              "architecture": query.architecture, "requested_source_commit_oid": query.source_commit_oid,
              "state": "unknown", "diagnostic": "PACKAGE_PROVIDER_UNSUPPORTED"}
    if provider is None:
        return result
    result["diagnostic"] = "PACKAGE_SOURCE_UNAVAILABLE"
    try:
        provider_id = provider.provider_id
        snapshot = provider.package(repository, repository_id, query)
    except Exception:
        # Provider failures are observation failures, never evidence of absence.
        return result
    try:
        if (type(snapshot) is not PackageSnapshotV1
                or not _matches(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", provider_id)
                or snapshot.provider_id != provider_id or snapshot.repository != repository
                or type(snapshot.repository_id) is not int or snapshot.repository_id != repository_id
                or any(getattr(snapshot, key) != getattr(query, key) for key in ("package_name", "channel", "architecture"))
                or not _matches(r"[0-9a-f]{64}", snapshot.response_sha256)):
            return result
        retrieved, metadata = _time(snapshot.retrieved_at), _time(snapshot.metadata_at)
        if not (metadata <= retrieved <= now and 0 <= (now - metadata).total_seconds() <= 3600):
            return result
        if snapshot.state == "available":
            if (snapshot.source_commit_oid != query.source_commit_oid
                    or not _matches(r"[A-Za-z0-9][A-Za-z0-9._+~:-]{0,199}", snapshot.version)
                    or snapshot.version_scheme not in {"arch", "semver", "upstream"}):
                return result
        elif snapshot.state in {"absent", "unknown"}:
            if any(value is not None for value in (snapshot.version, snapshot.version_scheme, snapshot.source_commit_oid)):
                return result
        else:
            return result
        result.update({"state": snapshot.state, "version": snapshot.version, "scheme": snapshot.version_scheme,
                       "source_commit_oid": snapshot.source_commit_oid, "provider_id": snapshot.provider_id,
                       "retrieved_at": snapshot.retrieved_at, "metadata_at": snapshot.metadata_at,
                       "fresh_until": (metadata + timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
                       "response_sha256": snapshot.response_sha256})
        result.pop("diagnostic")
        return result
    except (OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError):
        return result
