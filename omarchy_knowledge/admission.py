"""Offline exact-tree admission. Caller identity and bot grants are trusted inputs."""
from dataclasses import asdict, dataclass
import hashlib
import json
import re
import subprocess
import unicodedata

import knowledge
from projections import record_digest, validate_ingestion_receipt, validate_upstream_observation
from .git_objects import ObjectUnavailable, valid_oid
from .content_flags import dangerous_content_flags


UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}"
PATH = re.compile(r"(records/(cases|changes|reports|events)|provenance/(ingestion|upstream))/(" + UUID + r")\.json\Z")
PROFILES = {"community": "records/", "ingestion-receipt": "provenance/ingestion/",
            "upstream-observation": "provenance/upstream/"}
DIRECTORIES = {"records", "provenance", "records/cases", "records/changes", "records/reports",
               "records/events", "provenance/ingestion", "provenance/upstream"}
VALIDATOR_REVISION = "strict-corpus-provenance-content-v1"


@dataclass(frozen=True)
class TreeCandidateV1:
    repository_id: int
    subject_kind: str
    base_commit_oid: str
    head_commit_oid: str
    evaluated_commit_oid: str
    expected_tree_oid: str
    policy_revision: str
    profile: str


@dataclass(frozen=True)
class BotAuthorityGrant:
    """Only a future authenticated coordinator may supply this, never JSON from a PR."""
    repository_id: int
    pull_request: int
    actor_account_id: int
    actor_type: str
    head_repository_id: int
    head_commit_oid: str
    evaluated_commit_oid: str
    policy_revision: str
    profile: str
    additions: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class CoordinatorImportGrant:
    """Trusted native coordinator contract, never a grant read from user JSON.

    The coordinator authenticates the import through canonical single-parent
    history and fixed metadata, then binds exactly one generated receipt here.
    Constructing this dataclass is not itself authentication.
    """
    repository_id: int
    accepted_commit_oid: str
    policy_revision: str
    additions: tuple[tuple[str, str], ...]


class Rejected(Exception):
    pass


def _require(condition, code):
    if not condition:
        raise Rejected(code)


def _candidate_valid(candidate):
    return (type(candidate.repository_id) is int and 0 < candidate.repository_id <= 2**63 - 1
            and candidate.subject_kind in {"pull-head", "merge-group"}
            and candidate.profile in PROFILES
            and all(valid_oid(getattr(candidate, key)) for key in
                    ("base_commit_oid", "head_commit_oid", "evaluated_commit_oid", "expected_tree_oid"))
            and len({len(getattr(candidate, key)) for key in
                     ("base_commit_oid", "head_commit_oid", "evaluated_commit_oid", "expected_tree_oid")}) == 1
            and isinstance(candidate.policy_revision, str)
            and re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64}|sha256:[0-9a-f]{64})", candidate.policy_revision))


def _paths(entries):
    result, normalized = {}, set()
    for entry in entries:
        try:
            path = entry.path.decode("utf-8")
        except UnicodeDecodeError:
            raise Rejected("PATH_NOT_ALLOWED") from None
        if len(path) > 512 or len(path.split("/")) > 8:
            raise ObjectUnavailable()
        _require(unicodedata.normalize("NFC", path) == path and "\\" not in path
                 and all(p not in {"", ".", ".."} for p in path.split("/"))
                 and not any(unicodedata.category(c) in {"Cc", "Cf", "Cs"} for c in path), "PATH_NOT_ALLOWED")
        _require(path.casefold() not in normalized, "PATH_COLLISION")
        normalized.add(path.casefold())
        result[path] = entry
    return result


def _bound_json_depth(raw):
    depth, quoted, escaped = 0, False, False
    for byte in raw:
        if quoted:
            if escaped:
                escaped = False
            elif byte == 92:
                escaped = True
            elif byte == 34:
                quoted = False
        elif byte == 34:
            quoted = True
        elif byte in (91, 123):
            depth += 1
            if depth > 64:
                raise ObjectUnavailable()
        elif byte in (93, 125):
            depth -= 1


def check_tree(candidate, objects, *, trusted_bot_grant=None, trusted_import_grant=None):
    """Return bounded non-echoing report; errors and bounds never become acceptance.

    The caller must independently bind base/head/test-merge association and select
    the deployed policy revision. A local accept does not authorize a GitHub write.
    """
    report = {"version": 1, "decision": "indeterminate", "violations": [], "additions": []}
    try:
        if not isinstance(candidate, TreeCandidateV1) or not _candidate_valid(candidate):
            raise ObjectUnavailable()
        report.update(asdict(candidate))
        report["evaluated_tree_oid"] = candidate.expected_tree_oid
        report["validator_revision"] = VALIDATOR_REVISION
        _require(candidate.subject_kind != "merge-group" or candidate.head_commit_oid == candidate.evaluated_commit_oid,
                 "CANDIDATE_MISMATCH")
        objects.begin()
        base_tree = objects.commit(candidate.base_commit_oid)
        objects.commit(candidate.head_commit_oid)
        tree = objects.commit(candidate.evaluated_commit_oid)
        _require(tree == candidate.expected_tree_oid, "TREE_MISMATCH")
        base, current = _paths(objects.entries(base_tree)), _paths(objects.entries(tree))
        for path, old in base.items():
            _require(path in current, "NOT_ADDITIONS_ONLY")
            if old.kind != "tree":
                _require(old == current[path], "NOT_ADDITIONS_ONLY")
            else:
                _require(current[path].kind == "tree" and old.mode == current[path].mode, "NOT_ADDITIONS_ONLY")
        added = {path: entry for path, entry in current.items() if path not in base and entry.kind != "tree"}
        if len(added) > (10 if candidate.profile == "community" else 1):
            raise ObjectUnavailable()
        _require(bool(added), "NO_ADDITIONS")
        for path, entry in current.items():
            if entry.kind == "tree" and path not in base:
                _require(path in DIRECTORIES and any(p.startswith(path + "/") for p in added), "PATH_NOT_ALLOWED")
        for path, entry in added.items():
            _require(PATH.fullmatch(path) and path.startswith(PROFILES[candidate.profile]), "PATH_NOT_ALLOWED")
            _require(entry.mode == "100644" and entry.kind == "blob", "MODE_NOT_ALLOWED")
            if not 0 <= entry.size <= 65536:
                raise ObjectUnavailable()
        if candidate.profile != "community":
            native = trusted_import_grant
            if native is not None:
                _require(candidate.profile == "ingestion-receipt" and isinstance(native, CoordinatorImportGrant)
                         and native.repository_id == candidate.repository_id
                         and native.policy_revision == candidate.policy_revision
                         and valid_oid(native.accepted_commit_oid)
                         and native.additions == tuple(sorted((p, e.oid) for p, e in added.items())), "AUTHORITY_MISMATCH")
            grant = trusted_bot_grant
            _require(native is not None or isinstance(grant, BotAuthorityGrant), "AUTHORITY_REQUIRED")
            _require(native is not None or (all(type(getattr(grant, key)) is int and 0 < getattr(grant, key) <= 2**63 - 1
                         for key in ("repository_id", "pull_request", "actor_account_id", "head_repository_id"))
                     and grant.actor_type == "Bot" and grant.head_repository_id == candidate.repository_id
                     and all(getattr(grant, key) == getattr(candidate, key) for key in
                             ("repository_id", "head_commit_oid", "evaluated_commit_oid", "policy_revision", "profile"))
                     and grant.additions == tuple(sorted((p, e.oid) for p, e in added.items()))), "AUTHORITY_MISMATCH")
        records, provenance, by_id, total = [], [], {}, 0
        canonical = []
        for path, entry in sorted(current.items()):
            if entry.kind == "tree" or not path.startswith(("records/", "provenance/")):
                continue
            match = PATH.fullmatch(path)
            _require(match is not None, "PATH_NOT_ALLOWED")
            _require(entry.mode == "100644" and entry.kind == "blob", "MODE_NOT_ALLOWED")
            total += entry.size
            if not 0 <= entry.size <= 65536 or total > 16 * 1024 * 1024:
                raise ObjectUnavailable()
            raw = objects.blob(entry.oid, 65536)
            _bound_json_depth(raw)
            _require(not raw.startswith(b"version https://git-lfs.github.com/spec/v1"), "LFS_NOT_ALLOWED")
            identifier = match.group(4)
            _require(identifier not in by_id, "DUPLICATE_ID")
            if path.startswith("records/"):
                record = knowledge.parse_record(raw)
                _require(record["id"] == identifier and record["type"] + "s" == match.group(2), "TYPE_OR_ID_MISMATCH")
                _require(not dangerous_content_flags(record), "DANGEROUS_CONTENT")
                records.append(record)
            else:
                record = knowledge._parse_json(raw)
                if path.startswith("provenance/ingestion/"):
                    validate_ingestion_receipt(record)
                else:
                    validate_upstream_observation(record)
                _require(type(record.get("receipt_version", record.get("observation_version"))) is int,
                         "INVALID_PROVENANCE")
                knowledge._privacy(record)
                provenance.append((path, record))
            by_id[identifier] = (record, entry, path)
            canonical.append((path, entry.oid))
        knowledge.validate_corpus(records)
        for path, item in provenance:
            ingestion = path.startswith("provenance/ingestion/")
            target_id = item["record_id" if ingestion else "event_id"]
            _require(target_id in by_id, "PROVENANCE_TARGET_MISMATCH")
            target, entry, target_path = by_id[target_id]
            _require(target_path.startswith("records/") and (ingestion or
                     (target["type"] == "event" and target["payload"]["event_kind"] == "upstream-resolution")),
                     "PROVENANCE_TARGET_MISMATCH")
            _require(item["record_sha256" if ingestion else "event_sha256"] == record_digest(target), "PROVENANCE_TARGET_MISMATCH")
            if ingestion:
                if path in added and item["receipt_version"] == 2:
                    _require(trusted_import_grant is not None, "AUTHORITY_REQUIRED")
                _require(item["source"]["repository_id"] == str(candidate.repository_id)
                         and item["source"]["record_blob_oid"]["hex"] == entry.oid, "PROVENANCE_TARGET_MISMATCH")
                accepted = item["source"]["merge_commit_oid" if item["receipt_version"] == 1 else "accepted_commit_oid"]["hex"]
                if path in added and trusted_import_grant is not None:
                    _require(item["receipt_version"] == 2 and accepted == trusted_import_grant.accepted_commit_oid,
                             "AUTHORITY_MISMATCH")
                historical_tree = objects.commit(accepted)
                historical = _paths(objects.entries(historical_tree))
                _require(target_path in historical and historical[target_path] == entry, "PROVENANCE_TARGET_MISMATCH")
        report["corpus_digest"] = "sha256:" + hashlib.sha256(json.dumps(canonical, separators=(",", ":")).encode()).hexdigest()
        report["additions"] = [{"path_class": "record" if p.startswith("records/") else candidate.profile,
                                "blob_oid": e.oid, "size": e.size} for p, e in sorted(added.items())]
        report["decision"] = "accept"
    except Rejected as exc:
        report["decision"] = "reject"
        report["violations"] = [{"code": str(exc), "record_id": None}]
    except (ObjectUnavailable, OSError, subprocess.SubprocessError, RecursionError, UnicodeError):
        report["violations"] = [{"code": "OBJECT_OR_RESOURCE_UNAVAILABLE", "record_id": None}]
    except (ValueError, TypeError, KeyError, AttributeError):
        report["decision"] = "reject"
        report["violations"] = [{"code": "INVALID_CORPUS_OR_REQUEST", "record_id": None}]
    return report
