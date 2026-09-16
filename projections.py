"""Conservative, offline projections over validated community records.

The functions in this module never upgrade a record's claim into authority.  Trust
is supplied explicitly by callers through the dedicated receipt/observation
arguments of the evidence and recommendation APIs.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import itertools
import json
import re
import subprocess
from typing import Any, Mapping, Sequence
from urllib.parse import urlsplit


MATCHES = "matches"
DOES_NOT_MATCH = "does-not-match"
INSUFFICIENT_INFORMATION = "insufficient-information"

MAX_TOPOLOGY_BINDINGS = 4096
ARCH_VERCMP = "/usr/bin/vercmp"
_ARCH_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+~:-]{0,199}$")
_SEMVER = re.compile(
    r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)
_UUID4 = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_DECIMAL_ID = re.compile(r"^[1-9][0-9]{0,19}$")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+:-]{0,127}$")
_OUTCOMES = ("success", "failure", "partial", "unknown")
_SOFTWARE_COMPONENTS = {
    "omarchy", "kernel", "compositor", "shell", "plugin", "package",
    "firmware", "terminal", "workspace", "other",
}
_CHANNELS = {"stable", "testing", "development", "rc", "edge", "unknown"}
_SCHEMES = {"arch", "semver", "upstream", "ancestry"}


@dataclass(frozen=True)
class ApplicabilityResult:
    """Three-state applicability with bounded, user-displayable explanations."""

    state: str
    reasons: tuple[str, ...] = ()
    missing: tuple[str, ...] = ()


@dataclass(frozen=True)
class EvidenceObservation:
    report_id: str
    outcome: str
    origin: str
    attribution: str
    actor_account_id: str | None
    intervention: str


@dataclass(frozen=True)
class EvidenceSummary:
    """Evidence vector for exactly one case/change cohort."""

    case_id: str
    change_id: str | None
    authenticated_account_counts: dict[str, int]
    authenticated_report_counts: dict[str, int]
    direct_report_counts: dict[str, int]
    adapted_report_counts: dict[str, int]
    journal_import_counts: dict[str, int]
    external_source_counts: dict[str, int]
    unattributed_claim_counts: dict[str, int]
    observations: tuple[EvidenceObservation, ...]
    rejected_receipt_count: int
    trust_basis: str = "caller-supplied-authenticated-ingestion-receipts"


@dataclass(frozen=True)
class Recommendation:
    """A conservative next step; never authority to mutate or remove local state."""

    action: str
    applicability: str
    workaround: str
    reasons: tuple[str, ...]
    upstream_state: str
    cleanup_requires_inspection_and_approval: bool
    rejected_observation_count: int
    trust_basis: str = "caller-supplied-authenticated-upstream-observations"


def _exact_keys(value: Mapping[str, Any], required: set[str], optional: set[str] = frozenset()) -> None:
    if not isinstance(value, Mapping) or set(value) - required - optional or not required.issubset(value):
        raise ValueError("Object does not match the strict projection contract")


def _utc_timestamp(value: Any) -> datetime:
    if not isinstance(value, str) or len(value) > 40 or not value.endswith("Z"):
        raise ValueError("Expected a bounded UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ValueError("Expected a valid UTC timestamp") from exc
    if parsed.utcoffset() is None or parsed.utcoffset().total_seconds() != 0:
        raise ValueError("Expected a UTC timestamp")
    return parsed


def _git_oid(value: Any) -> None:
    _exact_keys(value, {"algorithm", "hex"})
    expected_length = {"sha1": 40, "sha256": 64}.get(value["algorithm"])
    if expected_length is None or not isinstance(value["hex"], str) \
            or len(value["hex"]) != expected_length or not re.fullmatch(r"[0-9a-f]+", value["hex"]):
        raise ValueError("Invalid typed Git object ID")


def record_digest(record: Mapping[str, Any]) -> str:
    """Return the semantic record SHA-256, deliberately distinct from a Git blob OID.

    The digest input is UTF-8 JSON with sorted keys, no insignificant whitespace,
    no ASCII escaping, and no non-finite numbers.  It binds parsed record semantics,
    not raw file bytes or Git's ``blob <length>`` framing.
    """
    if not isinstance(record, Mapping):
        raise ValueError("Record digest input must be an object")
    try:
        canonical = json.dumps(record, sort_keys=True, separators=(",", ":"),
                               ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError("Record is not canonical JSON data") from exc
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def validate_ingestion_receipt(receipt: Mapping[str, Any]) -> Mapping[str, Any]:
    """Validate shape only; the caller remains responsible for receipt authenticity."""
    _exact_keys(receipt, {
        "receipt_version", "kind", "record_id", "record_sha256", "actor",
        "accepted_at", "policy_revision", "source",
    })
    if type(receipt["receipt_version"]) is not int or receipt["receipt_version"] not in {1, 2} or receipt["kind"] != "ingestion":
        raise ValueError("Unsupported ingestion receipt")
    if not isinstance(receipt["record_id"], str) or not _UUID4.fullmatch(receipt["record_id"]):
        raise ValueError("Invalid receipt record ID")
    if not isinstance(receipt["record_sha256"], str) or not _SHA256.fullmatch(receipt["record_sha256"]):
        raise ValueError("Invalid canonical record SHA-256")
    _exact_keys(receipt["actor"], {"provider", "account_id"})
    if receipt["actor"]["provider"] != "github" or not isinstance(receipt["actor"]["account_id"], str) \
            or not _DECIMAL_ID.fullmatch(receipt["actor"]["account_id"]):
        raise ValueError("Invalid authenticated actor identity")
    _utc_timestamp(receipt["accepted_at"])
    if not isinstance(receipt["policy_revision"], str) or not _IDENTIFIER.fullmatch(receipt["policy_revision"]):
        raise ValueError("Invalid receipt policy revision")
    source = receipt["source"]
    if receipt["receipt_version"] == 1:
        _exact_keys(source, {"repository_id", "pull_request", "merge_commit_oid", "record_blob_oid"})
        _git_oid(source["merge_commit_oid"])
    else:
        _exact_keys(source, {"repository_id", "pull_request", "accepted_commit_oid", "record_blob_oid",
                             "head_commit_oid", "head_repository_id", "method", "toolkit_revision"})
        if source["method"] != "coordinator-import" or not isinstance(source["head_repository_id"], str) \
                or not _DECIMAL_ID.fullmatch(source["head_repository_id"]):
            raise ValueError("Invalid coordinator source identity")
        _git_oid(source["accepted_commit_oid"])
        _git_oid(source["head_commit_oid"])
        if not isinstance(source["toolkit_revision"], str) or not re.fullmatch(r"[0-9a-f]{40}", source["toolkit_revision"]):
            raise ValueError("Invalid toolkit revision")
    if not isinstance(source["repository_id"], str) or not _DECIMAL_ID.fullmatch(source["repository_id"]):
        raise ValueError("Invalid source repository identity")
    if not isinstance(source["pull_request"], int) or isinstance(source["pull_request"], bool) \
            or not 1 <= source["pull_request"] <= 2_147_483_647:
        raise ValueError("Invalid pull request number")
    _git_oid(source["record_blob_oid"])
    return receipt


def _software_selector(value: Any) -> None:
    _exact_keys(value, {"kind", "component"}, {"name"})
    if value["kind"] != "software" or value["component"] not in _SOFTWARE_COMPONENTS:
        raise ValueError("Invalid software selector")
    name = value.get("name")
    if value["component"] in {"plugin", "package", "other"} and name is None:
        raise ValueError("Named software selector requires a name")
    if name is not None and (not isinstance(name, str) or not _IDENTIFIER.fullmatch(name)):
        raise ValueError("Invalid software selector name")


def _public_https_url(value: Any) -> None:
    if not isinstance(value, str) or len(value) > 1000:
        raise ValueError("Invalid public source URL")
    try:
        parsed = urlsplit(value)
    except ValueError as exc:
        raise ValueError("Invalid public source URL") from exc
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Invalid public source URL")
    host = parsed.hostname.rstrip(".").casefold()
    if host == "localhost" or host.endswith((".local", ".internal")):
        raise ValueError("Invalid public source URL")


def validate_upstream_observation(observation: Mapping[str, Any]) -> Mapping[str, Any]:
    """Validate an observation shape, not its authenticity or source truth.

    Only a caller that authenticated the adapter/control path may place an object
    in the trusted observation input of :func:`recommend_action`.
    """
    _exact_keys(observation, {
        "observation_version", "kind", "event_id", "event_sha256", "repository",
        "observed_at", "fresh_until", "relevance", "source_inclusion", "packages",
        "migration", "activation",
    })
    if observation["observation_version"] != 1 or observation["kind"] != "upstream-resolution":
        raise ValueError("Unsupported upstream observation")
    if not isinstance(observation["event_id"], str) or not _UUID4.fullmatch(observation["event_id"]):
        raise ValueError("Invalid observation event ID")
    if not isinstance(observation["event_sha256"], str) or not _SHA256.fullmatch(observation["event_sha256"]):
        raise ValueError("Invalid canonical event SHA-256")
    repository = observation["repository"]
    _exact_keys(repository, {"provider", "repository_id"})
    if repository["provider"] != "github" or not isinstance(repository["repository_id"], str) \
            or not _DECIMAL_ID.fullmatch(repository["repository_id"]):
        raise ValueError("Invalid upstream repository identity")
    observed_at = _utc_timestamp(observation["observed_at"])
    fresh_until = _utc_timestamp(observation["fresh_until"])
    if fresh_until < observed_at:
        raise ValueError("Observation freshness cannot predate observation")

    relevance = observation["relevance"]
    if not isinstance(relevance, Mapping):
        raise ValueError("Invalid relevance observation")
    basis = relevance.get("basis")
    if relevance.get("state") == "upstream-supported" and basis == "official-linked-context":
        _exact_keys(relevance, {"state", "basis", "source_url", "source_object_sha256"})
        _public_https_url(relevance["source_url"])
        if not isinstance(relevance["source_object_sha256"], str) \
                or not _SHA256.fullmatch(relevance["source_object_sha256"]):
            raise ValueError("Invalid official source object digest")
    elif relevance.get("state") == "upstream-supported" and basis == "authenticated-maintainer-declaration":
        _exact_keys(relevance, {
            "state", "basis", "actor_account_id", "authority_policy_revision", "declaration_sha256",
        })
        if not isinstance(relevance["actor_account_id"], str) \
                or not _DECIMAL_ID.fullmatch(relevance["actor_account_id"]):
            raise ValueError("Invalid maintainer identity")
        if not isinstance(relevance["authority_policy_revision"], str) \
                or not _IDENTIFIER.fullmatch(relevance["authority_policy_revision"]):
            raise ValueError("Invalid authority policy revision")
        if not isinstance(relevance["declaration_sha256"], str) \
                or not _SHA256.fullmatch(relevance["declaration_sha256"]):
            raise ValueError("Invalid declaration digest")
    elif relevance.get("state") in {"unsupported", "unknown"} and basis == "unknown":
        _exact_keys(relevance, {"state", "basis"})
    else:
        raise ValueError("Invalid conditional relevance evidence")

    inclusion = observation["source_inclusion"]
    _exact_keys(inclusion, {"state"}, {"commit_oid"})
    if inclusion["state"] not in {"merged", "included", "not-included", "partial", "reverted", "unknown"}:
        raise ValueError("Invalid source inclusion state")
    if inclusion["state"] in {"merged", "included", "partial", "reverted"}:
        if "commit_oid" not in inclusion:
            raise ValueError("Source inclusion fact requires a typed commit ID")
        _git_oid(inclusion["commit_oid"])
    elif "commit_oid" in inclusion:
        raise ValueError("Unknown or absent inclusion cannot claim a commit ID")

    packages = observation["packages"]
    if not isinstance(packages, list) or len(packages) > 24:
        raise ValueError("Invalid package observations")
    seen_packages = set()
    for package in packages:
        _exact_keys(package, {"selector", "scheme", "version", "channel", "architecture", "state"})
        _software_selector(package["selector"])
        if package["scheme"] not in _SCHEMES or not isinstance(package["version"], str) \
                or not 1 <= len(package["version"]) <= 200:
            raise ValueError("Invalid package version observation")
        if package["channel"] not in _CHANNELS or not isinstance(package["architecture"], str) \
                or not re.fullmatch(r"[a-z0-9_+-]{1,40}", package["architecture"]):
            raise ValueError("Invalid package distribution scope")
        if package["state"] not in {"available", "unavailable", "unknown"}:
            raise ValueError("Invalid package availability state")
        coordinates = {key: value for key, value in package.items() if key != "state"}
        identity = json.dumps(coordinates, sort_keys=True, separators=(",", ":"))
        if identity in seen_packages:
            raise ValueError("Duplicate or conflicting package observation")
        seen_packages.add(identity)
    _exact_keys(observation["migration"], {"required"})
    if observation["migration"]["required"] not in {"yes", "no", "unknown"}:
        raise ValueError("Invalid migration requirement")
    _exact_keys(observation["activation"], {"required"})
    if observation["activation"]["required"] not in {
        "none", "relogin", "reboot", "service-restart", "manual", "unknown",
    }:
        raise ValueError("Invalid activation requirement")
    return observation


def _evidence_outcome(result: str) -> str:
    return result if result in {"success", "failure", "partial"} else "unknown"


def _empty_counts() -> dict[str, int]:
    return {outcome: 0 for outcome in _OUTCOMES}


def summarize_evidence(reports: Sequence[Mapping[str, Any]],
                       trusted_receipts: Sequence[Mapping[str, Any]], *,
                       case_id: str, change_id: str | None) -> EvidenceSummary:
    """Summarize exactly one case/change cohort from caller-authenticated receipts.

    Merely loading JSON into ``trusted_receipts`` does not authenticate it.  The
    caller must establish the receipts' admission-channel provenance first.  A
    valid receipt authenticates its submitter account, not a distinct person or
    machine, and imports never become independent tests.
    """
    if not isinstance(case_id, str) or not _UUID4.fullmatch(case_id):
        raise ValueError("Invalid evidence case ID")
    if change_id is not None and (not isinstance(change_id, str) or not _UUID4.fullmatch(change_id)):
        raise ValueError("Invalid evidence change ID")
    cohort = [item for item in reports
              if item.get("type") == "report"
              and item.get("payload", {}).get("case_id") == case_id
              and item.get("payload", {}).get("change_id") == change_id]
    cohort_ids = {item["id"] for item in cohort}
    receipts_by_record: dict[str, list[Mapping[str, Any]]] = {}
    rejected = 0
    for candidate in trusted_receipts:
        try:
            validated = validate_ingestion_receipt(candidate)
        except (KeyError, TypeError, ValueError):
            rejected += 1
            continue
        if validated["record_id"] in cohort_ids:
            receipts_by_record.setdefault(validated["record_id"], []).append(validated)

    accounts = {outcome: set() for outcome in _OUTCOMES}
    authenticated_reports = _empty_counts()
    direct_reports = _empty_counts()
    adapted_reports = _empty_counts()
    journal_imports = _empty_counts()
    external_imports = _empty_counts()
    unattributed = _empty_counts()
    observations: list[EvidenceObservation] = []
    for item in cohort:
        payload = item["payload"]
        outcome = _evidence_outcome(payload["result"])
        origin_values = {item.get("provenance", {}).get("kind"), payload["environment"].get("origin")}
        if "journal-import" in origin_values:
            origin = "journal-import"
        elif "external-source" in origin_values:
            origin = "external-source"
        else:
            origin = "firsthand"
        intervention = "adapted" if payload.get("intervention_deviations") else "direct"
        matching = [candidate for candidate in receipts_by_record.get(item["id"], ())
                    if candidate["record_sha256"] == record_digest(item)]
        actors = {candidate["actor"]["account_id"] for candidate in matching}
        rejected += len(receipts_by_record.get(item["id"], ())) - len(matching)
        actor = next(iter(actors)) if len(actors) == 1 else None
        if len(actors) > 1:
            rejected += len(matching)
        attribution = "authenticated-account" if actor is not None else "unattributed-claim"

        if actor is None:
            unattributed[outcome] += 1
        if origin == "journal-import":
            journal_imports[outcome] += 1
        elif origin == "external-source":
            external_imports[outcome] += 1
        elif actor is not None:
            accounts[outcome].add(actor)
            authenticated_reports[outcome] += 1
            (adapted_reports if intervention == "adapted" else direct_reports)[outcome] += 1
        observations.append(EvidenceObservation(
            report_id=item["id"], outcome=outcome, origin=origin,
            attribution=attribution, actor_account_id=actor, intervention=intervention,
        ))
    return EvidenceSummary(
        case_id=case_id,
        change_id=change_id,
        authenticated_account_counts={outcome: len(accounts[outcome]) for outcome in _OUTCOMES},
        authenticated_report_counts=authenticated_reports,
        direct_report_counts=direct_reports,
        adapted_report_counts=adapted_reports,
        journal_import_counts=journal_imports,
        external_source_counts=external_imports,
        unattributed_claim_counts=unattributed,
        observations=tuple(observations),
        rejected_receipt_count=rejected,
    )


def _result(state: str, reason: str, missing: str | None = None) -> ApplicabilityResult:
    return ApplicabilityResult(state, (reason,), (missing,) if missing else ())


def _selector_matches(expected: Mapping[str, Any], observed: Mapping[str, Any]) -> bool:
    for key, value in expected.items():
        actual = observed.get(key)
        if key in {"vendor_id", "product_id"} and isinstance(actual, str) and isinstance(value, str):
            if actual.casefold() != value.casefold():
                return False
        elif actual != value:
            return False
    return True


def _compare_operator(comparison: int, operator: str) -> bool:
    return {
        "=": comparison == 0,
        "!=": comparison != 0,
        ">": comparison > 0,
        ">=": comparison >= 0,
        "<": comparison < 0,
        "<=": comparison <= 0,
    }[operator]


def _semver_key(value: str) -> tuple[tuple[int, int, int], tuple[tuple[int, int | str], ...] | None] | None:
    match = _SEMVER.fullmatch(value)
    if not match:
        return None
    core = tuple(int(match.group(index)) for index in range(1, 4))
    prerelease = match.group(4)
    if prerelease is None:
        return core, None
    identifiers: list[tuple[int, int | str]] = []
    for identifier in prerelease.split("."):
        if identifier.isdigit():
            if len(identifier) > 1 and identifier.startswith("0"):
                return None
            identifiers.append((0, int(identifier)))
        else:
            identifiers.append((1, identifier))
    return core, tuple(identifiers)


def _compare_semver(left: str, right: str) -> int | None:
    left_key, right_key = _semver_key(left), _semver_key(right)
    if left_key is None or right_key is None:
        return None
    if left_key[0] != right_key[0]:
        return (left_key[0] > right_key[0]) - (left_key[0] < right_key[0])
    left_pre, right_pre = left_key[1], right_key[1]
    if left_pre is None or right_pre is None:
        return (left_pre is None) - (right_pre is None)
    for left_identifier, right_identifier in itertools.zip_longest(left_pre, right_pre):
        if left_identifier is None:
            return -1
        if right_identifier is None:
            return 1
        if left_identifier == right_identifier:
            continue
        if left_identifier[0] != right_identifier[0]:
            return -1 if left_identifier[0] == 0 else 1
        return (left_identifier[1] > right_identifier[1]) - (left_identifier[1] < right_identifier[1])
    return 0


def _compare_arch(left: str, right: str) -> int | None:
    if not _ARCH_VERSION.fullmatch(left) or not _ARCH_VERSION.fullmatch(right):
        return None
    try:
        completed = subprocess.run(
            [ARCH_VERCMP, left, right],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=0.5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    output = completed.stdout.strip()
    if completed.returncode != 0 or output not in {"-1", "0", "1"}:
        return None
    return int(output)


def _compare_version(left: str, right: str, scheme: str) -> int | None:
    if scheme == "semver":
        return _compare_semver(left, right)
    if scheme == "arch":
        return _compare_arch(left, right)
    return None


def _observation_map(environment: Mapping[str, Any], field: str, key: str) -> dict[str, Any]:
    return {entry[key]: entry["value"] for entry in environment.get(field, ())}


def _match_component(predicate: Mapping[str, Any], environment: Mapping[str, Any]) -> ApplicabilityResult:
    selector = predicate["selector"]
    candidates = [component for component in environment.get("components", ())
                  if _selector_matches(selector, component["selector"])]
    present = bool(candidates)
    if predicate["presence"]:
        if present:
            return _result(MATCHES, "A component matches the complete selector.")
        return _result(INSUFFICIENT_INFORMATION, "The scoped environment does not establish component absence.",
                       "component presence")
    if present:
        return _result(DOES_NOT_MATCH, "An excluded component is present.")
    complete = any(entry.get("selector") == selector
                   for entry in environment.get("complete_component_observations", ()))
    if complete:
        return _result(MATCHES, "A scoped complete observation establishes component absence.")
    return _result(INSUFFICIENT_INFORMATION, "Component absence lacks scoped completeness evidence.",
                   "component absence")


def _match_software(predicate: Mapping[str, Any], environment: Mapping[str, Any]) -> ApplicabilityResult:
    selector = predicate["selector"]
    unknown_scope: list[str] = []
    if "channel" in predicate:
        channel = environment.get("channel")
        if channel is None or channel == "unknown":
            unknown_scope.append("channel")
        elif channel != predicate["channel"]:
            return _result(DOES_NOT_MATCH, "The environment channel differs.")
    if "architecture" in predicate:
        architecture = environment.get("architecture")
        if architecture is None or architecture == "unknown":
            unknown_scope.append("architecture")
        elif architecture != predicate["architecture"]:
            return _result(DOES_NOT_MATCH, "The environment architecture differs.")
    candidates = [component for component in environment.get("components", ())
                  if _selector_matches(selector, component["selector"])]
    if not candidates:
        return _result(INSUFFICIENT_INFORMATION, "No observed component matches the software selector.", "component")
    saw_unknown = False
    matched = False
    for component in candidates:
        version = component.get("version")
        scheme = component.get("version_scheme")
        if version is None or scheme != predicate["scheme"]:
            saw_unknown = True
            continue
        all_constraints = True
        for constraint in predicate["constraints"]:
            comparison = _compare_version(version, constraint["version"], scheme)
            if comparison is None:
                saw_unknown = True
                all_constraints = False
                break
            if not _compare_operator(comparison, constraint["op"]):
                all_constraints = False
                break
        if all_constraints:
            matched = True
            break
    if not matched and not saw_unknown:
        return _result(DOES_NOT_MATCH, "No single observed component satisfies every version constraint.")
    if unknown_scope:
        fields = " and ".join(unknown_scope)
        return ApplicabilityResult(
            INSUFFICIENT_INFORMATION,
            (f"The environment {fields} is unknown.",),
            tuple(unknown_scope),
        )
    if not matched:
        return _result(INSUFFICIENT_INFORMATION, "A matching component lacks a supported comparable version.",
                       "comparable version")
    return _result(MATCHES, "One observed component satisfies every version constraint.")


def _edge_matches(expected: Mapping[str, Any], observed: Mapping[str, Any], binding: Mapping[str, str]) -> bool:
    if observed.get("from") != binding[expected["from"]] or observed.get("to") != binding[expected["to"]]:
        return False
    for field in ("relation", "transport", "detail"):
        if field in expected and observed.get(field) != expected[field]:
            return False
    if "via" in expected:
        expected_via = [binding[alias] for alias in expected["via"]]
        if observed.get("via", []) != expected_via:
            return False
    return True


def _match_topology(predicate: Mapping[str, Any], environment: Mapping[str, Any], maximum: int) -> ApplicabilityResult:
    topology = predicate["topology"]
    observed_components = environment.get("components", ())
    choices: list[tuple[str, list[str]]] = []
    for selector_binding in topology["selectors"]:
        aliases = [component["alias"] for component in observed_components
                   if _selector_matches(selector_binding["selector"], component["selector"])]
        if not aliases:
            return _result(INSUFFICIENT_INFORMATION,
                           f"No observed component can bind topology alias {selector_binding['alias']}.",
                           f"topology alias {selector_binding['alias']}")
        choices.append((selector_binding["alias"], aliases))
    attempts = 0
    observed_edges = environment.get("topology", {}).get("edges", ())
    for assignment in itertools.product(*(aliases for _, aliases in choices)):
        attempts += 1
        if attempts > maximum:
            return _result(INSUFFICIENT_INFORMATION, "Topology binding search exceeded its resource bound.",
                           "bounded topology result")
        if len(set(assignment)) != len(assignment):
            continue
        binding = {choices[index][0]: assignment[index] for index in range(len(choices))}
        if all(any(_edge_matches(edge, observed, binding) for observed in observed_edges)
               for edge in topology["required_edges"]):
            return _result(MATCHES, "Observed aliases and edges satisfy the topology predicate.")
    return _result(DOES_NOT_MATCH, "Observed component aliases do not form the required topology.")


def _match_predicate(predicate: Mapping[str, Any], environment: Mapping[str, Any],
                     maximum: int) -> ApplicabilityResult:
    kind = predicate["kind"]
    if kind == "component":
        return _match_component(predicate, environment)
    if kind == "software":
        return _match_software(predicate, environment)
    if kind == "feature":
        observations = _observation_map(environment, "features", "feature")
        if predicate["feature"] not in observations:
            return _result(INSUFFICIENT_INFORMATION, "The required feature was not observed.", predicate["feature"])
        state = MATCHES if observations[predicate["feature"]] == predicate["value"] else DOES_NOT_MATCH
        return _result(state, "The observed feature value was compared exactly.")
    if kind == "setting":
        observations = _observation_map(environment, "settings", "setting")
        if predicate["setting"] not in observations:
            return _result(INSUFFICIENT_INFORMATION, "The required setting was not observed.", predicate["setting"])
        state = MATCHES if observations[predicate["setting"]] == predicate["value"] else DOES_NOT_MATCH
        return _result(state, "The observed setting value was compared exactly.")
    if kind == "topology":
        return _match_topology(predicate, environment, maximum)
    return _result(INSUFFICIENT_INFORMATION, "The predicate kind is unsupported.", "supported predicate")


def _and(results: Sequence[ApplicabilityResult]) -> str:
    if any(result.state == DOES_NOT_MATCH for result in results):
        return DOES_NOT_MATCH
    if any(result.state == INSUFFICIENT_INFORMATION for result in results):
        return INSUFFICIENT_INFORMATION
    return MATCHES


def _or(results: Sequence[ApplicabilityResult]) -> str:
    if any(result.state == MATCHES for result in results):
        return MATCHES
    if any(result.state == INSUFFICIENT_INFORMATION for result in results):
        return INSUFFICIENT_INFORMATION
    return DOES_NOT_MATCH


def _combine(results: Sequence[ApplicabilityResult], reducer) -> ApplicabilityResult:
    reasons = tuple(reason for result in results for reason in result.reasons)[:64]
    missing = tuple(dict.fromkeys(item for result in results for item in result.missing))[:64]
    return ApplicabilityResult(reducer(results), reasons, missing)


def match_applicability(applicability: Mapping[str, Any], environment: Mapping[str, Any], *,
                        max_topology_bindings: int = MAX_TOPOLOGY_BINDINGS) -> ApplicabilityResult:
    """Match a validated finite applicability claim against a scoped environment.

    Environment components are observations relevant to a report, not a complete
    machine inventory.  Therefore an omitted component never proves absence unless
    its exact selector has an entry in ``complete_component_observations``.
    """
    if not isinstance(max_topology_bindings, int) or isinstance(max_topology_bindings, bool) \
            or max_topology_bindings < 1:
        raise ValueError("Topology binding bound must be a positive integer")
    maximum = min(max_topology_bindings, MAX_TOPOLOGY_BINDINGS)
    required = [_match_predicate(item, environment, maximum)
                for item in applicability.get("requires", ())]
    alternatives = [_combine([
        _match_predicate(item, environment, maximum) for item in group
    ], _and) for group in applicability.get("requires_any", ())]
    exclusions = [_match_predicate(item, environment, maximum)
                  for item in applicability.get("excludes", ())]
    uncertain = [_match_predicate(item, environment, maximum)
                 for item in applicability.get("uncertain_conditions", ())]

    reasons = tuple(reason for result in (*required, *alternatives, *exclusions, *uncertain)
                    for reason in result.reasons)[:64]
    missing = tuple(dict.fromkeys(item for result in (*required, *alternatives, *exclusions, *uncertain)
                                  for item in result.missing))[:64]
    if any(result.state == MATCHES for result in exclusions):
        return ApplicabilityResult(DOES_NOT_MATCH, reasons, missing)
    requirement_state = _and(required)
    if alternatives:
        requirement_state = _and((ApplicabilityResult(requirement_state), ApplicabilityResult(_or(alternatives))))
    if requirement_state == DOES_NOT_MATCH:
        return ApplicabilityResult(DOES_NOT_MATCH, reasons, missing)
    if any(result.state == INSUFFICIENT_INFORMATION for result in exclusions):
        return ApplicabilityResult(INSUFFICIENT_INFORMATION, reasons, missing)
    if requirement_state == INSUFFICIENT_INFORMATION:
        return ApplicabilityResult(INSUFFICIENT_INFORMATION, reasons, missing)
    if any(result.state != DOES_NOT_MATCH for result in uncertain):
        return ApplicabilityResult(INSUFFICIENT_INFORMATION, reasons, missing)
    return ApplicabilityResult(MATCHES, reasons, missing)


def _event_targets_change(event: Mapping[str, Any], case_id: str, change_id: str) -> bool:
    if event.get("type") != "event" or event.get("payload", {}).get("event_kind") != "upstream-resolution":
        return False
    targets = {(target.get("type"), target.get("id"))
               for target in event["payload"].get("targets", ())}
    return {("case", case_id), ("change", change_id)}.issubset(targets)


def _fixed_in_applicability(event: Mapping[str, Any]) -> Mapping[str, Any]:
    alternatives = event["payload"]["resolution"]["fixed_in"]["any_of"]
    return {"requires": [], "requires_any": [item["all_of"] for item in alternatives]}


def _available_environment(observation: Mapping[str, Any], local_environment: Mapping[str, Any]) -> dict[str, Any]:
    architecture, channel = local_environment.get("architecture"), local_environment.get("channel")
    known_scope = architecture not in {None, "unknown"} and channel not in {None, "unknown"}
    relevant_packages = [package for package in observation["packages"]
                         if known_scope
                         and package["state"] == "available"
                         and package["architecture"] != "unknown"
                         and package["channel"] != "unknown"
                         and package["architecture"] == architecture
                         and package["channel"] == channel]
    hardware_components = [component for component in local_environment.get("components", ())
                           if component.get("selector", {}).get("kind") == "hardware"]
    package_components = [{
        "alias": f"available-{index}",
        "selector": package["selector"],
        "version": package["version"],
        "version_scheme": package["scheme"],
    } for index, package in enumerate(relevant_packages)]
    components = [*hardware_components, *package_components]
    return {
        "origin": "external-source",
        "architecture": architecture,
        "channel": channel,
        "components": components,
        "features": list(local_environment.get("features", ())),
        "settings": list(local_environment.get("settings", ())),
        "topology": {"nodes": [component["alias"] for component in components], "edges": []},
    }


def _recommendation(action: str, applicability: str, workaround: str, reasons: Sequence[str],
                    upstream_state: str, already_applied: bool, rejected: int) -> Recommendation:
    return Recommendation(
        action=action,
        applicability=applicability,
        workaround=workaround,
        reasons=tuple(reasons)[:64],
        upstream_state=upstream_state,
        cleanup_requires_inspection_and_approval=already_applied,
        rejected_observation_count=rejected,
    )


def recommend_action(case: Mapping[str, Any], change: Mapping[str, Any],
                     environment: Mapping[str, Any], events: Sequence[Mapping[str, Any]],
                     trusted_upstream_observations: Sequence[Mapping[str, Any]], *,
                     now: datetime | None = None,
                     workaround_already_applied: bool = False) -> Recommendation:
    """Recommend a bounded next step for one validated case/change pair.

    Community events contribute a claimed relationship only.  Relevance,
    inclusion, and package availability come exclusively from observations the
    caller already authenticated.  The function never recommends automatic
    workaround removal; ``workaround_already_applied`` only changes the displayed
    inspection/approval boundary.
    """
    if case.get("type") != "case" or change.get("type") != "change" \
            or change.get("payload", {}).get("case_id") != case.get("id"):
        raise ValueError("Recommendation requires a matching case/change pair")
    if not isinstance(workaround_already_applied, bool):
        raise ValueError("Applied workaround state must be explicit")
    if now is None:
        now = datetime.now(timezone.utc)
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Recommendation time must be timezone-aware")
    now = now.astimezone(timezone.utc)

    applicability = match_applicability(change["payload"]["applicability"], environment)
    base_workaround = "retain-existing" if workaround_already_applied else "eligible"
    if applicability.state == DOES_NOT_MATCH:
        return _recommendation("not-applicable", applicability.state, base_workaround,
                               applicability.reasons, "not-evaluated", workaround_already_applied, 0)
    if applicability.state == INSUFFICIENT_INFORMATION:
        return _recommendation("investigate", applicability.state, base_workaround,
                               applicability.reasons, "not-evaluated", workaround_already_applied, 0)
    if case["payload"]["intent"] == "optional" or change["payload"]["intent"] == "optional":
        return _recommendation("offer-optional", applicability.state, base_workaround,
                               ("Optional intent requires an explicit user choice.",),
                               "not-evaluated", workaround_already_applied, 0)
    if case["payload"]["intent"] != "corrective" or change["payload"]["intent"] != "corrective":
        return _recommendation("investigate", applicability.state, base_workaround,
                               ("Undetermined intent cannot default to corrective action.",),
                               "not-evaluated", workaround_already_applied, 0)

    relevant_events = {event["id"]: event for event in events
                       if _event_targets_change(event, case["id"], change["id"])}
    if not relevant_events:
        action = "retain-existing-workaround" if workaround_already_applied else "consider-workaround"
        return _recommendation(action, applicability.state, base_workaround,
                               ("No resolution event targets this exact case/change pair.",),
                               "none", workaround_already_applied, 0)

    candidates: list[tuple[datetime, Mapping[str, Any], Mapping[str, Any]]] = []
    rejected = 0
    for candidate in trusted_upstream_observations:
        try:
            observation = validate_upstream_observation(candidate)
        except (KeyError, TypeError, ValueError):
            rejected += 1
            continue
        event = relevant_events.get(observation["event_id"])
        if event is None or observation["event_sha256"] != record_digest(event):
            rejected += 1
            continue
        candidates.append((_utc_timestamp(observation["observed_at"]), observation, event))
    if not candidates:
        return _recommendation("investigate", applicability.state, base_workaround,
                               ("No trusted current observation is bound to the resolution event.",),
                               "unknown", workaround_already_applied, rejected)

    newest_time = max(item[0] for item in candidates)
    newest = [(observation, event) for observed_at, observation, event in candidates
              if observed_at == newest_time]
    if newest_time > now:
        return _recommendation("investigate", applicability.state, base_workaround,
                               ("The newest trusted upstream observation is dated in the future.",),
                               "future", workaround_already_applied, rejected)
    distinct = {json.dumps(observation, sort_keys=True, separators=(",", ":"))
                for observation, _ in newest}
    if len(distinct) != 1:
        return _recommendation("investigate", applicability.state, base_workaround,
                               ("Trusted observations at the newest time conflict.",),
                               "conflicting", workaround_already_applied, rejected)
    observation, event = newest[0]
    if now > _utc_timestamp(observation["fresh_until"]):
        return _recommendation("investigate", applicability.state, base_workaround,
                               ("The newest trusted upstream observation is stale.",),
                               "stale", workaround_already_applied, rejected)
    if observation["relevance"]["state"] != "upstream-supported":
        state = observation["relevance"]["state"]
        return _recommendation("investigate", applicability.state, base_workaround,
                               ("Trusted metadata does not establish upstream relevance.",),
                               state, workaround_already_applied, rejected)
    inclusion = observation["source_inclusion"]["state"]
    if inclusion != "included":
        return _recommendation("investigate", applicability.state, base_workaround,
                               ("A merge alone, partial inclusion, or revert is not a shipped fix.",),
                               inclusion, workaround_already_applied, rejected)

    fixed_in = _fixed_in_applicability(event)
    available = match_applicability(fixed_in, _available_environment(observation, environment))
    if available.state != MATCHES:
        return _recommendation("investigate", applicability.state, base_workaround,
                               ("No trusted package observation proves availability for this channel and architecture.",),
                               "unavailable" if available.state == DOES_NOT_MATCH else "availability-unknown",
                               workaround_already_applied, rejected)
    installed = match_applicability(fixed_in, environment)
    if installed.state == INSUFFICIENT_INFORMATION:
        return _recommendation("investigate", applicability.state, base_workaround,
                               ("The installed fixed-in state cannot be compared safely.",),
                               "available", workaround_already_applied, rejected)
    if installed.state == DOES_NOT_MATCH:
        workaround = "retain-existing" if workaround_already_applied else "defer-new"
        return _recommendation("prefer-update", applicability.state, workaround,
                               ("An upstream-supported applicable fix is available for this distribution scope.",),
                               "available", workaround_already_applied, rejected)

    migration = observation["migration"]["required"]
    activation = observation["activation"]["required"]
    if migration != "no":
        return _recommendation("verify-migration", applicability.state, base_workaround,
                               ("The fixed version is installed but migration completion is not established.",),
                               "installed", workaround_already_applied, rejected)
    if activation != "none":
        return _recommendation("verify-activation", applicability.state, base_workaround,
                               ("The fixed version is installed but activation completion is not established.",),
                               "installed", workaround_already_applied, rejected)
    if workaround_already_applied:
        return _recommendation("inspect-workaround-before-cleanup", applicability.state, "retain-existing",
                               ("Inspect ownership and later edits, request approval, then retest before cleanup.",),
                               "installed", True, rejected)
    return _recommendation("retest-installed-fix", applicability.state, "defer-new",
                           ("The fixed version appears installed; retesting is still required.",),
                           "installed", False, rejected)
