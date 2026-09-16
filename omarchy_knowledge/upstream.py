"""Conservative source facts from bounded public snapshots, never update advice.

Provider snapshots are trusted observations, not self-authenticating JSON. These
facts deliberately cannot be passed directly into trusted resolution projections.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import re

from .github_public import PublicReadUnavailable, PublicSnapshot, _repo, _tag, _number
from .git_objects import valid_oid
from .package_facts import PackagePublicRead, PackageQueryV1, observe_package, validate_package_queries


@dataclass(frozen=True)
class OmarchyProbeV1:
    repository: str
    repository_id: int
    pull_request: int
    tags: tuple[str, ...] = ()
    backport_pull_requests: tuple[int, ...] = ()
    package_queries: tuple[PackageQueryV1, ...] = ()


def _timestamp(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z", value):
        raise ValueError("Invalid timestamp")
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _snapshot(snapshot, probe, now):
    if not isinstance(snapshot, PublicSnapshot) or snapshot.repository != probe.repository:
        raise ValueError("Source mismatch")
    if not isinstance(snapshot.response_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", snapshot.response_sha256):
        raise ValueError("Missing source digest")
    age = (now - _timestamp(snapshot.retrieved_at)).total_seconds()
    if not 0 <= age <= 3600 or not isinstance(snapshot.payload, dict):
        raise ValueError("Stale observation")
    return snapshot.payload, {"retrieved_at": snapshot.retrieved_at,
                              "fresh_until": (_timestamp(snapshot.retrieved_at) + timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
                              "response_sha256": snapshot.response_sha256}


ERRORS = (PublicReadUnavailable, OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError)


def _pull(probe, github, number, now):
    result = {"number": number, "merged": "unknown", "diagnostic": "SOURCE_UNAVAILABLE"}
    try:
        data, source = _snapshot(github.pull(probe.repository, number), probe, now)
        if (type(data["number"]) is not int or data["number"] != number
                or type(data["base"]["repo"]["id"]) is not int or data["base"]["repo"]["id"] != probe.repository_id
                or type(data["merged"]) is not bool or data["state"] not in {"open", "closed"}
                or not valid_oid(data["head"]["sha"])
                or not isinstance(data["base"]["ref"], str)
                or not re.fullmatch(r"[A-Za-z0-9._/-]{1,200}", data["base"]["ref"])):
            raise ValueError("Invalid PR snapshot")
        if data["merged"]:
            if not valid_oid(data["merge_commit_sha"]) or _timestamp(data["merged_at"]) > now or data["state"] != "closed":
                raise ValueError("Invalid merge identity")
        return {"number": number, "state": data["state"], "merged": data["merged"],
                "merged_at": data["merged_at"] if data["merged"] else None,
                "merge_commit_oid": data["merge_commit_sha"] if data["merged"] else None,
                "head_commit_oid": data["head"]["sha"], "base_ref": data["base"]["ref"],
                "evidence_url": f"https://github.com/{probe.repository}/pull/{number}", **source}
    except ERRORS:
        return result


def _ancestry(probe, github, base, head, now):
    if base == head:
        return {"ancestry": "yes", "method": "identical-commit", "history_complete": True}
    data, source = _snapshot(github.compare(probe.repository, base, head), probe, now)
    # A truncated page is never treated as complete history, even when its summary
    # hints that the source is an ancestor. No file-list inference is made.
    commits = data["commits"]
    complete = (isinstance(commits, list) and type(data["total_commits"]) is int
                and 0 <= data["total_commits"] <= 100 and len(commits) == data["total_commits"]
                and all(isinstance(c, dict) and valid_oid(c.get("sha")) for c in commits)
                and data["base_commit"]["sha"] == base
                and valid_oid(data["merge_base_commit"]["sha"]))
    state = "unknown"
    if complete and data["status"] == "ahead" and data["merge_base_commit"]["sha"] == base and commits and commits[-1]["sha"] == head:
        state = "yes"
    elif complete and data["merge_base_commit"]["sha"] != base and (
            (data["status"] == "behind" and not commits and data["merge_base_commit"]["sha"] == head)
            or (data["status"] == "diverged" and commits and commits[-1]["sha"] == head)):
        state = "no"
    return {"ancestry": state, "method": "bounded-github-compare", "history_complete": complete, **source}


def _release(probe, github, tag, now):
    try:
        data, source = _snapshot(github.release_by_tag(probe.repository, tag), probe, now)
        if data["tag_name"] != tag or type(data["draft"]) is not bool or type(data["prerelease"]) is not bool:
            raise ValueError("Invalid release")
        if data["published_at"] is not None and _timestamp(data["published_at"]) > now:
            raise ValueError("Future publication")
        return {"state": "published" if not data["draft"] and data["published_at"] else "unpublished",
                "draft": data["draft"], "prerelease": data["prerelease"], "published_at": data["published_at"], **source}
    except ERRORS:
        return {"state": "unknown", "diagnostic": "SOURCE_UNAVAILABLE"}


def _tag_fact(probe, github, tag, merged_oid, now):
    result = {"tag": tag, "ancestry": "unknown", "method": "unknown", "history_complete": False}
    try:
        data, source = _snapshot(github.ref(probe.repository, tag), probe, now)
        if data["ref"] != "refs/tags/" + tag:
            raise ValueError("Tag identity mismatch")
        obj = data["object"]
        result["ref_source"] = source
        result["ref_oid"] = obj["sha"]
        result["tag_objects"] = []
        seen = set()
        for _ in range(5):
            if not valid_oid(obj["sha"]):
                raise ValueError("Invalid tag OID")
            if obj["type"] == "commit":
                break
            if obj["type"] != "tag" or obj["sha"] in seen:
                raise ValueError("Unsupported tag chain")
            seen.add(obj["sha"])
            data, object_source = _snapshot(github.annotated_tag(probe.repository, obj["sha"]), probe, now)
            if data["sha"] != obj["sha"]:
                raise ValueError("Tag object identity mismatch")
            result["tag_objects"].append({"oid": obj["sha"], **object_source})
            obj = data["object"]
        else:
            raise ValueError("Tag chain limit")
        result["commit_oid"] = obj["sha"]
        if merged_oid:
            result.update(_ancestry(probe, github, merged_oid, obj["sha"], now))
        # A ref read is point-in-time evidence. Re-read to avoid binding a release
        # observed after a tag move to the old source identity.
        publication = _release(probe, github, tag, now)
        current, recheck_source = _snapshot(github.ref(probe.repository, tag), probe, now)
        if current["ref"] != "refs/tags/" + tag or current["object"]["sha"] != result["ref_oid"]:
            raise ValueError("Moved tag")
        result["publication"] = publication
        result["ref_recheck_source"] = recheck_source
    except ERRORS:
        result = {"tag": tag, "ancestry": "unknown", "method": "unknown", "history_complete": False,
                  "publication": {"state": "unknown"}, "diagnostic": "SOURCE_UNAVAILABLE"}
    return result


def observe_omarchy(probe, github, packages: PackagePublicRead | None = None, *, now=None):
    """Examine explicitly supplied tags/backports; never infer globally first fix.

    Ancestry proves commit inclusion only, not semantic effectiveness or absence
    of reverts. Package facts require an explicitly injected reviewed provider;
    the default has no live package extractor and returns unknown per selector.
    """
    now = now or datetime.now(timezone.utc)
    _repo(probe.repository)
    _number(probe.pull_request)
    if type(probe.repository_id) is not int or not 0 < probe.repository_id <= 2**63 - 1:
        raise ValueError("Numeric repository identity required")
    if len(probe.tags) > 8 or len(probe.backport_pull_requests) > 4:
        raise ValueError("Probe exceeds bounds")
    for tag in probe.tags:
        _tag(tag)
    for number in probe.backport_pull_requests:
        _number(number)
    validate_package_queries(probe.package_queries)
    pull = _pull(probe, github, probe.pull_request, now)
    return {"observation_version": 1, "kind": "upstream-source-facts",
            "repository": {"name": probe.repository, "repository_id": str(probe.repository_id)},
            "observed_at": now.isoformat().replace("+00:00", "Z"), "pull": pull,
            "tags": [(_tag_fact(probe, github, tag, pull.get("merge_commit_oid"), now)
                      if type(pull["merged"]) is bool else
                      {"tag": tag, "ancestry": "unknown", "method": "unknown", "history_complete": False,
                       "publication": {"state": "unknown"}, "diagnostic": "REPOSITORY_IDENTITY_UNCONFIRMED"})
                     for tag in probe.tags],
            "backports": [{"relationship_method": "explicit-pr", "semantic_equivalence": "unknown",
                           "pull": _pull(probe, github, number, now)} for number in probe.backport_pull_requests],
            "history_scope": "explicit-tags-only", "relevance": {"state": "unknown", "basis": "unknown"},
            "semantic_fix_state": "unknown", "package_availability": "unknown",
            "packages": [observe_package(probe.repository, probe.repository_id, query, packages, now=now)
                         for query in probe.package_queries],
            "diagnostics": (["PACKAGE_PROVIDER_UNSUPPORTED"] if packages is None else [])
                           + ["SEMANTIC_REVERT_ANALYSIS_UNSUPPORTED"]}
