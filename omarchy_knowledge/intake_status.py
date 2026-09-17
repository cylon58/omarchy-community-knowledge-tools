"""Pure validation for public fair-intake scheduling state.

Public status is an availability input only.  Validation authenticates its fixed
deployment identity and shape; neither a cursor nor its telemetry grants admission.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
import re
from typing import Any, Mapping

from .fair_intake import IntakeCursor


MAX_INT = 2_147_483_647
_OID = re.compile(r"[0-9a-f]{40}\Z")
_GIT_OID = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_DECIMAL_ID = re.compile(r"[1-9][0-9]{0,19}\Z")
_UUID4 = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\Z"
)
_TAG = re.compile(r"v[0-9]{1,5}\.[0-9]{1,5}\.[0-9]{1,5}(?:rc[0-9]{1,5})?\Z")
_VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+~:-]{0,199}\Z")
_SAFE_FIELDS = {"status", "outcomes", "scanned", "scan_truncated"}
_PUBLIC_FIELDS = {"source", "receipt_coverage", "upstream", "pr_behavior"}
_SCAN_COUNTER_FIELDS = {
    "page_fetches", "rows_returned", "rows_consumed", "evaluations",
    "closed", "imported", "rejected", "not_ready", "plans",
    "cursor_drifts",
}
_SOURCE_FIELDS = {
    "repository", "repository_id", "deployment", "ref", "data_revision",
    "tree_revision", "toolkit_revision", "policy_revision", "verified_at",
    "source_updated_at",
}


def _require(condition: bool) -> None:
    if not condition:
        raise ValueError("invalid public intake status")


def _oid(value: Any) -> str:
    _require(isinstance(value, str) and _OID.fullmatch(value) is not None)
    return value


def _timestamp(value: Any) -> bool:
    if not isinstance(value, str) or not value.endswith("Z") or len(value) > 40:
        return False
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        return False
    return parsed.utcoffset() is not None and parsed.utcoffset().total_seconds() == 0


def _exact(value: Any, fields: set[str]) -> Mapping[str, Any]:
    _require(isinstance(value, Mapping) and set(value) == fields)
    return value


def _sha256(value: Any) -> None:
    _require(isinstance(value, str) and _SHA256.fullmatch(value) is not None)


def _git_oid(value: Any) -> None:
    _require(isinstance(value, str) and _GIT_OID.fullmatch(value) is not None)


def _decimal_id(value: Any) -> None:
    _require(isinstance(value, str) and _DECIMAL_ID.fullmatch(value) is not None)


def _source(value: Any) -> None:
    value = _exact(value, {"retrieved_at", "fresh_until", "response_sha256"})
    _require(_timestamp(value["retrieved_at"]) and _timestamp(value["fresh_until"]))
    _sha256(value["response_sha256"])


def _repository(value: Any) -> None:
    value = _exact(value, {"name", "repository_id"})
    _require(value == {"name": "omacom/omarchy", "repository_id": "994093166"})


def _validate_catalog(value: Any, channel: str) -> None:
    _require(isinstance(value, Mapping)
             and value.get("channel") == channel
             and value.get("architecture") == "x86_64")
    if value.get("status") == "unknown":
        _exact(value, {
            "channel", "architecture", "status", "observed_at", "packages",
            "diagnostic",
        })
        _require(_timestamp(value["observed_at"])
                 and value["packages"] == []
                 and value["diagnostic"] == "CATALOG_UNAVAILABLE")
        return
    _exact(value, {
        "status", "channel", "architecture", "url", "catalog_checked_at",
        "fresh_until", "response_sha256", "method", "packages",
    })
    _require(value["status"] == "observed"
             and value["url"] == f"https://pkgs.omarchy.org/{channel}/x86_64/omarchy.db"
             and _timestamp(value["catalog_checked_at"])
             and _timestamp(value["fresh_until"])
             and value["method"] in {
                 "bounded-gzip-tar-desc-v1", "bounded-zstd-tar-desc-v1",
             })
    _sha256(value["response_sha256"])
    packages = value["packages"]
    _require(type(packages) is list and len(packages) <= 2)
    names = []
    for package in packages:
        package = _exact(package, {
            "fact_version", "kind", "name", "version", "scheme", "channel",
            "architecture", "package_architecture", "package_sha256", "built_at",
            "catalog_checked_at",
        })
        _require(type(package["fact_version"]) is int
                 and package["fact_version"] == 1
                 and package["kind"] == "package-catalog"
                 and package["name"] in {"omarchy", "omarchy-settings"}
                 and isinstance(package["version"], str)
                 and _VERSION.fullmatch(package["version"]) is not None
                 and package["scheme"] == "arch"
                 and package["channel"] == channel
                 and package["architecture"] == "x86_64"
                 and package["package_architecture"] in {"any", "x86_64"}
                 and _timestamp(package["built_at"])
                 and package["catalog_checked_at"] == value["catalog_checked_at"])
        _sha256(package["package_sha256"])
        names.append(package["name"])
    _require(names == sorted(set(names)))


def _validate_observation(value: Any) -> None:
    _require(isinstance(value, Mapping)
             and type(value.get("observation_version")) is int
             and value["observation_version"] == 1)
    from projections import validate_upstream_observation
    validate_upstream_observation(value)


def _validate_release_discovery(value: Any) -> None:
    _require(isinstance(value, Mapping))
    sources = value.get("sources")
    _require(type(sources) is list and len(sources) <= 2)
    for source in sources:
        _source(source)
    if value.get("status") == "unknown":
        _exact(value, {"status", "releases", "sources", "complete", "diagnostic"})
        _require(value["releases"] == [] and value["complete"] is False
                 and value["diagnostic"] == "RELEASE_SCAN_UNAVAILABLE")
        return
    _exact(value, {"status", "releases", "sources", "complete", "enumeration"})
    _require(value["status"] == "observed"
             and type(value["complete"]) is bool
             and value["enumeration"] == "github-published-release-objects-max40")
    releases = value["releases"]
    _require(type(releases) is list and len(releases) <= 40)
    tags = set()
    for release in releases:
        release = _exact(release, {
            "id", "tag_name", "draft", "prerelease", "published_at",
        })
        _require(type(release["id"]) is int and release["id"] > 0
                 and isinstance(release["tag_name"], str)
                 and type(release["draft"]) is bool
                 and type(release["prerelease"]) is bool
                 and (release["published_at"] is None
                      or _timestamp(release["published_at"])))
        _require(release["tag_name"] not in tags)
        tags.add(release["tag_name"])


def _validate_pull_fact(value: Any) -> None:
    _require(isinstance(value, Mapping))
    _require(type(value.get("number")) is int and 1 <= value["number"] <= MAX_INT)
    if value.get("merged") == "unknown":
        _exact(value, {"number", "merged", "diagnostic"})
        _require(value["diagnostic"] == "SOURCE_UNAVAILABLE")
        return
    _exact(value, {
        "number", "state", "merged", "merged_at", "merge_commit_oid",
        "head_commit_oid", "base_ref", "evidence_url", "retrieved_at",
        "fresh_until", "response_sha256",
    })
    _require(value["state"] in {"open", "closed"}
             and type(value["merged"]) is bool
             and (value["merged_at"] is None or _timestamp(value["merged_at"]))
             and (value["merge_commit_oid"] is None
                  or isinstance(value["merge_commit_oid"], str))
             and isinstance(value["base_ref"], str)
             and re.fullmatch(r"[A-Za-z0-9._/-]{1,200}", value["base_ref"])
             and value["evidence_url"]
                 == f"https://github.com/omacom/omarchy/pull/{value['number']}")
    _git_oid(value["head_commit_oid"])
    if value["merged"]:
        _require(value["state"] == "closed" and value["merged_at"] is not None)
        _git_oid(value["merge_commit_oid"])
    else:
        _require(value["merged_at"] is None and value["merge_commit_oid"] is None)
    _source({key: value[key] for key in ("retrieved_at", "fresh_until", "response_sha256")})


def _validate_publication(value: Any) -> None:
    _require(isinstance(value, Mapping))
    if value.get("state") == "unknown":
        _exact(value, {"state", "diagnostic"})
        _require(value["diagnostic"] == "SOURCE_UNAVAILABLE")
        return
    _exact(value, {
        "state", "draft", "prerelease", "published_at", "release_id",
        "retrieved_at", "fresh_until", "response_sha256",
    })
    _require(value["state"] in {"published", "unpublished"}
             and type(value["draft"]) is bool
             and type(value["prerelease"]) is bool
             and (value["published_at"] is None or _timestamp(value["published_at"]))
             and type(value["release_id"]) is int and value["release_id"] > 0)
    _require((value["state"] == "published")
             == (not value["draft"] and value["published_at"] is not None))
    _source({key: value[key] for key in ("retrieved_at", "fresh_until", "response_sha256")})


def _validate_tag_fact(value: Any) -> None:
    _require(isinstance(value, Mapping)
             and isinstance(value.get("tag"), str)
             and _TAG.fullmatch(value["tag"]) is not None)
    if "diagnostic" in value:
        _exact(value, {
            "tag", "ancestry", "method", "history_complete", "publication",
            "diagnostic",
        })
        _require(value["ancestry"] == "unknown" and value["method"] == "unknown"
                 and value["history_complete"] is False
                 and value["publication"] == {"state": "unknown"}
                 and value["diagnostic"] in {
                     "SOURCE_UNAVAILABLE", "REPOSITORY_IDENTITY_UNCONFIRMED",
                 })
        return
    base = {
        "tag", "ancestry", "method", "history_complete", "ref_source", "ref_oid",
        "tag_objects", "commit_oid", "publication", "ref_recheck_source",
    }
    ancestry_source = {"retrieved_at", "fresh_until", "response_sha256"}
    _require(frozenset(value) in {frozenset(base), frozenset(base | ancestry_source)})
    _require(value["ancestry"] in {"yes", "no", "unknown"}
             and value["method"] in {"unknown", "identical-commit", "bounded-github-compare"}
             and type(value["history_complete"]) is bool)
    has_ancestry_source = ancestry_source <= set(value)
    _require(has_ancestry_source == (value["method"] == "bounded-github-compare"))
    if value["method"] == "identical-commit":
        _require(value["ancestry"] == "yes" and value["history_complete"] is True)
    elif value["method"] == "unknown":
        _require(value["ancestry"] == "unknown" and value["history_complete"] is False)
    _source(value["ref_source"])
    _source(value["ref_recheck_source"])
    _git_oid(value["ref_oid"])
    _git_oid(value["commit_oid"])
    objects = value["tag_objects"]
    _require(type(objects) is list and len(objects) <= 5)
    for item in objects:
        item = _exact(item, {"oid", "retrieved_at", "fresh_until", "response_sha256"})
        _git_oid(item["oid"])
        _source({key: item[key] for key in ("retrieved_at", "fresh_until", "response_sha256")})
    _validate_publication(value["publication"])
    if has_ancestry_source:
        _source({key: value[key] for key in ancestry_source})


def _validate_source_facts(value: Any) -> None:
    value = _exact(value, {
        "observation_version", "kind", "repository", "observed_at", "pull", "tags",
        "backports", "history_scope", "relevance", "semantic_fix_state",
        "package_availability", "packages", "diagnostics", "earliest_observed_release",
        "first_published_containing_release",
    })
    _require(type(value["observation_version"]) is int
             and value["observation_version"] == 1
             and value["kind"] == "upstream-source-facts"
             and _timestamp(value["observed_at"])
             and value["backports"] == []
             and value["history_scope"] == "bounded-discovered-releases"
             and value["relevance"] == {"state": "unknown", "basis": "unknown"}
             and value["semantic_fix_state"] == "unknown"
             and value["package_availability"] == "unknown"
             and value["packages"] == []
             and value["diagnostics"] == [
                 "PACKAGE_PROVIDER_UNSUPPORTED", "SEMANTIC_REVERT_ANALYSIS_UNSUPPORTED",
             ])
    _repository(value["repository"])
    _validate_pull_fact(value["pull"])
    tags = value["tags"]
    _require(type(tags) is list and len(tags) <= 8)
    for tag in tags:
        _validate_tag_fact(tag)
    earliest = value["earliest_observed_release"]
    _require(earliest is None or (isinstance(earliest, str)
                                  and _TAG.fullmatch(earliest) is not None))
    first = value["first_published_containing_release"]
    if first is not None:
        first = _exact(first, {
            "tag", "line", "basis", "ordering", "earlier_checked",
            "enumeration_complete",
        })
        _require(isinstance(first["tag"], str) and _TAG.fullmatch(first["tag"])
                 and isinstance(first["line"], str)
                 and re.fullmatch(r"[0-9]{1,5}\.[0-9]{1,5}", first["line"])
                 and first["basis"] == "source-ancestry-only"
                 and first["ordering"] == "numeric-major-minor-patch-rc"
                 and type(first["earlier_checked"]) is list
                 and len(first["earlier_checked"]) <= 40
                 and all(isinstance(tag, str) and _TAG.fullmatch(tag)
                         for tag in first["earlier_checked"])
                 and first["enumeration_complete"] is True)


def _validate_declaration(value: Any, resolution: Mapping[str, Any]) -> None:
    value = _exact(value, {"body", "identity", "authority_policy_revision"})
    revision = value["authority_policy_revision"]
    _require(isinstance(revision, str) and _GIT_OID.fullmatch(revision) is not None)
    identity = _exact(value["identity"], {
        "repository_id", "pull_request", "comment_id", "actor_account_id",
        "body_sha256", "updated_at",
    })
    _require(identity["repository_id"] == "994093166"
             and type(identity["pull_request"]) is int
             and 1 <= identity["pull_request"] <= MAX_INT
             and _timestamp(identity["updated_at"]))
    for field in ("comment_id", "actor_account_id"):
        _decimal_id(identity[field])
    _sha256(identity["body_sha256"])
    body = _exact(value["body"], {
        "declaration_version", "kind", "repository_id", "pull_request", "event_id",
        "event_sha256", "assertion", "release", "fixed_packages", "channels",
        "architectures", "migration", "activation",
    })
    _require(type(body["declaration_version"]) is int and body["declaration_version"] == 2
             and body["kind"] == "resolution"
             and body["repository_id"] == identity["repository_id"]
             and body["pull_request"] == identity["pull_request"]
             and body["event_id"] == resolution["event_id"]
             and body["event_sha256"] == resolution["event_sha256"]
             and body["assertion"] == "supports"
             and body["migration"] in {"yes", "no", "unknown"}
             and body["activation"] in {
                 "none", "relogin", "reboot", "service-restart", "manual", "unknown",
             })
    release = _exact(body["release"], {"tag", "fix_state"})
    _require(release["fix_state"] == "included"
             and isinstance(release["tag"], str) and _TAG.fullmatch(release["tag"]))
    for field, allowed in (("channels", {"stable", "rc"}),
                           ("architectures", {"x86_64"})):
        items = body[field]
        _require(type(items) is list and 1 <= len(items) <= len(allowed)
                 and all(type(item) is str and item in allowed for item in items)
                 and len(set(items)) == len(items))
    packages = body["fixed_packages"]
    _require(type(packages) is list and 1 <= len(packages) <= 2)
    names = set()
    for package in packages:
        package = _exact(package, {
            "name", "scheme", "minimum_version", "maximum_exclusive",
        })
        _require(package["name"] in {"omarchy", "omarchy-settings"}
                 and package["name"] not in names and package["scheme"] == "arch")
        names.add(package["name"])
        for field in ("minimum_version", "maximum_exclusive"):
            candidate = package[field]
            if field == "maximum_exclusive" and candidate is None:
                continue
            _require(isinstance(candidate, str) and _VERSION.fullmatch(candidate))


def _validate_resolution(value: Any) -> None:
    _require(isinstance(value, Mapping))
    base = {"event_id", "event_sha256", "inclusion_basis", "observation", "diagnostics"}
    group = {"declaration_scan", "source_facts", "tags_scan_complete"}
    _require(frozenset(value) in {
        frozenset(base), frozenset(base | group), frozenset(base | group | {"declaration"}),
    })
    _require(isinstance(value["event_id"], str) and _UUID4.fullmatch(value["event_id"])
             and value["inclusion_basis"] in {
                 "unknown", "authenticated-maintainer-release-assertion",
             })
    _sha256(value["event_sha256"])
    _validate_observation(value["observation"])
    _require(value["observation"]["event_id"] == value["event_id"]
             and value["observation"]["event_sha256"] == value["event_sha256"])
    diagnostics = value["diagnostics"]
    allowed = {
        "UNSUPPORTED_UPSTREAM_LINK", "CURRENT_DECLARATION_OR_MERGE_UNSUPPORTED",
        "BOUNDARY_OR_PUBLISHED_RELEASE_UNSUPPORTED", "PRERELEASE_NOT_STABLE",
        "CATALOG_UNAVAILABLE", "PACKAGE_BOUNDARY_UNAVAILABLE_OR_COMPARATOR_UNSUPPORTED",
        "RESOLUTION_UNAVAILABLE",
    }
    _require(type(diagnostics) is list and len(diagnostics) <= 1
             and all(type(item) is str and item in allowed for item in diagnostics))
    if group <= set(value):
        scan = _exact(value["declaration_scan"], {"complete", "sources", "state"})
        _require(type(scan["complete"]) is bool
                 and type(scan["sources"]) is list and len(scan["sources"]) <= 62
                 and scan["state"] in {"unknown", "revoked-or-conflicting", "supported"})
        for source in scan["sources"]:
            _source(source)
        _validate_source_facts(value["source_facts"])
        _require(type(value["tags_scan_complete"]) is bool)
    if "declaration" in value:
        _require(value["declaration_scan"]["state"] == "supported")
        _validate_declaration(value["declaration"], value)
    if value["inclusion_basis"] == "authenticated-maintainer-release-assertion":
        _require(diagnostics == [] and "declaration" in value)
    else:
        _require(len(diagnostics) == 1)


class PublicStatusKind(str, Enum):
    LEGACY = "legacy"
    CURRENT = "current"


@dataclass(frozen=True)
class CursorHealth:
    version: int
    last_progress_at: str | None
    consecutive_drift_runs: int

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "CursorHealth":
        _require(isinstance(value, Mapping) and set(value) == {
            "version", "last_progress_at", "consecutive_drift_runs",
        })
        _require(type(value["version"]) is int and value["version"] == 1)
        progress = value["last_progress_at"]
        _require(progress is None or _timestamp(progress))
        drift = value["consecutive_drift_runs"]
        _require(type(drift) is int and 0 <= drift <= MAX_INT)
        return cls(1, progress, drift)

    def to_mapping(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "last_progress_at": self.last_progress_at,
            "consecutive_drift_runs": self.consecutive_drift_runs,
        }


@dataclass(frozen=True)
class IntakeScan:
    version: int
    trusted_lane: str
    prior_state: str
    selected_action: str
    scan_outcome: str
    stop_reason: str
    counters: dict[str, int]

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "IntakeScan":
        value = _exact(value, {
            "version", "trusted_lane", "prior_state", "selected_action",
            "scan_outcome", "stop_reason", "counters",
        })
        _require(type(value["version"]) is int and value["version"] == 1
                 and value["trusted_lane"] in {"direct", "scheduled"}
                 and value["prior_state"] in {"current", "legacy", "unavailable"}
                 and value["selected_action"] in {"after", "before", "withhold"}
                 and value["scan_outcome"] in {"direct", "planned", "no-eligible"}
                 and value["stop_reason"] in {
                     "direct", "plan", "preparation-limit", "fetch-limit",
                     "cycle-complete",
                 })
        counters = _exact(value["counters"], _SCAN_COUNTER_FIELDS)
        limits = {
            "page_fetches": 10, "rows_returned": 200, "rows_consumed": 200,
            "evaluations": 20, "closed": 200, "imported": 200,
            "rejected": 20, "not_ready": 20, "plans": 1,
            "cursor_drifts": 10,
        }
        _require(all(type(counters[field]) is int
                     and 0 <= counters[field] <= limit
                     for field, limit in limits.items()))
        _require(counters["rows_consumed"]
                 == counters["closed"] + counters["imported"]
                 + counters["evaluations"]
                 and counters["evaluations"]
                 == counters["rejected"] + counters["not_ready"]
                 + counters["plans"]
                 and counters["rows_consumed"] <= counters["rows_returned"])
        if value["trusted_lane"] == "direct":
            _require(value["scan_outcome"] == "direct"
                     and value["stop_reason"] == "direct"
                     and all(count == 0 for count in counters.values()))
        else:
            _require(value["scan_outcome"] != "direct"
                     and value["stop_reason"] != "direct"
                     and counters["page_fetches"] >= 1
                     and counters["rows_returned"]
                         <= counters["page_fetches"] * 20
                     and counters["cursor_drifts"] <= 1
                     and (counters["cursor_drifts"] == 0
                          or counters["page_fetches"] >= 2)
                     and (value["scan_outcome"] == "planned")
                     == (value["stop_reason"] == "plan")
                     and (value["scan_outcome"] == "planned")
                     == (counters["plans"] == 1))
            if value["stop_reason"] == "fetch-limit":
                _require(counters["page_fetches"] == 10)
            elif value["stop_reason"] == "preparation-limit":
                _require(counters["evaluations"] == 20)
            elif value["stop_reason"] == "cycle-complete":
                _require(counters["rows_returned"]
                         < counters["page_fetches"] * 20)
        return cls(
            1, value["trusted_lane"], value["prior_state"],
            value["selected_action"], value["scan_outcome"],
            value["stop_reason"], dict(counters),
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "trusted_lane": self.trusted_lane,
            "prior_state": self.prior_state,
            "selected_action": self.selected_action,
            "scan_outcome": self.scan_outcome,
            "stop_reason": self.stop_reason,
            "counters": dict(self.counters),
        }


@dataclass(frozen=True)
class PublicIntakeStatus:
    kind: PublicStatusKind
    source_revision: str
    cursor: IntakeCursor | None
    cursor_health: CursorHealth | None
    intake_scan: IntakeScan | None


def validate_safe_status(value: Mapping[str, Any]) -> dict[str, Any]:
    """Return one exact, bounded public run summary with no contributor prose."""
    _require("status" in value and isinstance(value["status"], str)
             and value["status"] in {
        "idle", "planned", "accepted", "receipt-pending", "retry",
        "unavailable", "rejected", "not-ready",
    })
    _require(set(value) <= _SAFE_FIELDS)
    outcomes = value.get("outcomes", [])
    _require(type(outcomes) is list and len(outcomes) <= 21)
    for item in outcomes:
        _require(isinstance(item, Mapping) and set(item) <= {
            "status", "pull_request", "head", "accepted_commit_oid",
            "head_changed",
        })
        _require(isinstance(item.get("status"), str) and item["status"] in {
            "planned", "accepted", "receipt-pending", "retry", "unavailable",
            "rejected", "not-ready",
        })
        number = item.get("pull_request")
        _require(type(number) is int and 1 <= number <= MAX_INT)
        for field in ("head", "accepted_commit_oid"):
            if item.get(field) is not None:
                _oid(item[field])
        if "head_changed" in item:
            _require(type(item["head_changed"]) is bool)
    scanned = value.get("scanned", 0)
    _require(type(scanned) is int and 0 <= scanned <= 200)
    _require(type(value.get("scan_truncated", False)) is bool)
    return dict(value)


def _validate_safe_status(value: Mapping[str, Any]) -> None:
    validate_safe_status({field: value[field] for field in _SAFE_FIELDS
                          if field in value})


def _validate_source(value: Any, *, repository: str,
                     repository_id: int, deployment: str) -> str:
    _require(isinstance(value, Mapping) and set(value) == _SOURCE_FIELDS)
    _require(value["repository"] == repository
             and type(value["repository_id"]) is int
             and value["repository_id"] == repository_id
             and value["deployment"] == deployment
             and value["ref"] == "refs/heads/main")
    for field in ("data_revision", "tree_revision", "toolkit_revision",
                  "policy_revision"):
        _oid(value[field])
    _require(_timestamp(value["verified_at"])
             and _timestamp(value["source_updated_at"]))
    return value["data_revision"]


def _validate_upstream(value: Any) -> None:
    _require(isinstance(value, Mapping))
    if type(value.get("version")) is int and value["version"] == 1:
        _require(set(value) == {"version", "status", "observations"}
                 and value["status"] == "not-refreshed"
                 and value["observations"] == [])
        return
    required = {
        "version", "status", "observed_at", "fresh_until", "provider_revision",
        "authority_policy_sha256", "authority_configured", "repository",
        "catalogs", "observations", "resolutions", "scan",
    }
    success_fields = required | {"repository_source", "release_discovery"}
    failure_fields = required | {"diagnostic"}
    _require(type(value.get("version")) is int and value["version"] == 2
             and frozenset(value) in {frozenset(success_fields), frozenset(failure_fields)}
             and _timestamp(value["observed_at"])
             and _timestamp(value["fresh_until"])
             and value["provider_revision"] == "omarchy-resolution-supplier-v2.1"
             and type(value["authority_configured"]) is bool)
    _sha256(value["authority_policy_sha256"])
    _repository(value["repository"])
    catalogs = value["catalogs"]
    _require(type(catalogs) is list and len(catalogs) == 2)
    for catalog, channel in zip(catalogs, ("stable", "rc")):
        _validate_catalog(catalog, channel)
    observations = value["observations"]
    resolutions = value["resolutions"]
    _require(type(observations) is list and len(observations) <= 8
             and type(resolutions) is list and len(resolutions) <= 8)
    for observation in observations:
        _validate_observation(observation)
    for resolution in resolutions:
        _validate_resolution(resolution)
    scan = value["scan"]
    _require(isinstance(scan, Mapping)
             and set(scan) == {"events", "event_total", "incomplete"}
             and type(scan["events"]) is int and 0 <= scan["events"] <= 8
             and type(scan["event_total"]) is int and scan["event_total"] >= scan["events"]
             and type(scan["incomplete"]) is bool)
    _require(len(observations) == scan["events"])
    if set(value) == success_fields:
        _require(value["status"] in {"partial", "refreshed"}
                 and len(resolutions) == scan["events"]
                 and observations == [item["observation"] for item in resolutions])
        _source(value["repository_source"])
        _validate_release_discovery(value["release_discovery"])
        expected = ("partial" if scan["incomplete"]
                    or any(item["status"] != "observed" for item in catalogs)
                    else "refreshed")
        _require(value["status"] == expected)
    else:
        _require(value["status"] == "unknown"
                 and value["diagnostic"] == "OFFICIAL_REPOSITORY_UNAVAILABLE"
                 and resolutions == [] and scan["incomplete"] is True)


def validate_intake_status(
    value: Mapping[str, Any], *, repository: str,
    repository_id: int, deployment: str,
) -> PublicIntakeStatus:
    """Validate one recognized legacy/current public status without live reads."""
    _require(isinstance(value, Mapping)
             and isinstance(repository, str)
             and type(repository_id) is int
             and deployment in {"production", "pilot"})
    fields = set(value)
    legacy_fields = fields - _SAFE_FIELDS
    if legacy_fields == _PUBLIC_FIELDS:
        kind = PublicStatusKind.LEGACY
    elif legacy_fields == _PUBLIC_FIELDS | {
            "intake_cursor", "cursor_health", "intake_scan"}:
        kind = PublicStatusKind.CURRENT
    else:
        raise ValueError("invalid public intake status")
    _validate_safe_status(value)
    source_revision = _validate_source(
        value["source"], repository=repository,
        repository_id=repository_id, deployment=deployment,
    )
    coverage = value["receipt_coverage"]
    _require(isinstance(coverage, Mapping)
             and set(coverage) == {"records", "receipted_records"})
    records, receipted = coverage["records"], coverage["receipted_records"]
    _require(type(records) is int and type(receipted) is int
             and 0 <= receipted <= records <= 4096)
    _require(value["pr_behavior"] == "snapshots-imported-prs-remain-open")
    _validate_upstream(value["upstream"])
    if kind is PublicStatusKind.LEGACY:
        return PublicIntakeStatus(kind, source_revision, None, None, None)
    cursor = IntakeCursor.from_mapping(value["intake_cursor"])
    health = CursorHealth.from_mapping(value["cursor_health"])
    intake_scan = IntakeScan.from_mapping(value["intake_scan"])
    return PublicIntakeStatus(kind, source_revision, cursor, health, intake_scan)
