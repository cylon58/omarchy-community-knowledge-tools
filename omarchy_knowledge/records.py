"""Strict, inert community knowledge records; no network or code execution."""
import ipaddress
import itertools
import json
from math import log2
import os
from pathlib import Path
import re
import stat
import unicodedata
from urllib.parse import unquote, urlsplit

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource


MAX_RECORD_BYTES = 65536
MAX_RECORDS = 5000
MAX_RECORD_CONTENT_BYTES = 20 * 1024 * 1024
SCHEMA_DIRECTORY = Path(__file__).parent / "schemas" / "v1"
RECORD_TYPES = ("case", "change", "report", "event")
RECORD_DIRECTORIES = {"cases": "case", "changes": "change", "reports": "report", "events": "event"}
CANONICAL_FILENAME = re.compile(
    r"^([0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12})\.json$"
)
SECRET_OR_PII = re.compile(
    r"[\w.+-]+@[\w.-]+\.[a-z]{2,}|\b(?:\d{1,3}\.){3}\d{1,3}\b|"
    r"\b(?:[0-9a-f]{2}:){5}[0-9a-f]{2}\b|/(?:home|Users)/[^/\s]+|"
    r"\b(?:sk-or-|sk-proj-|ghp_|github_pat_|AKIA|xox[baprs]-)|"
    r"\b(?:password|api[_ -]?key|secret|private[ _-]?key|token|hostname|ssid|serial\s*(?:number)?)\s*[:=]",
    re.IGNORECASE,
)
TOKENISH = re.compile(r"[A-Za-z0-9_+/=-]{32,}")
IPV6_CANDIDATE = re.compile(r"(?<![0-9A-Fa-f:])(?:[0-9A-Fa-f]{0,4}:){2,}[0-9A-Fa-f:.%]*(?![0-9A-Fa-f:])")
EMBEDDED_URL = re.compile(r"https?://[^\s<>()\[\]{}\"'`]+", re.IGNORECASE)
GITHUB_SLUG_PART = re.compile(r"[A-Za-z0-9_.-]{1,100}")
FULL_GIT_OID = re.compile(r"[0-9a-f]{40}")
GITHUB_NUMBER = re.compile(r"[1-9][0-9]*")


def _load_validators():
    schemas, resources = {}, []
    for name in ("common", *RECORD_TYPES):
        path = SCHEMA_DIRECTORY / f"{name}.schema.json"
        schema = json.loads(path.read_text(encoding="utf-8"))
        schema["$id"] = path.resolve().as_uri()
        schemas[name] = schema
        resources.append((schema["$id"], Resource.from_contents(schema)))
    registry = Registry().with_resources(resources)
    return {name: Draft202012Validator(schemas[name], registry=registry, format_checker=FormatChecker())
            for name in RECORD_TYPES}


VALIDATORS = _load_validators()
# Kept for callers of the initial case-only prototype; new callers use VALIDATORS.
SCHEMA = json.loads((SCHEMA_DIRECTORY / "case.schema.json").read_text(encoding="utf-8"))
VALIDATOR = VALIDATORS["case"]
SENSITIVE = SECRET_OR_PII


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def _entropy(value):
    length = len(value)
    return -sum((value.count(char) / length) * log2(value.count(char) / length) for char in set(value))


def _public_https_url(value):
    try:
        parsed = urlsplit(value)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            return False
        host = parsed.hostname.rstrip(".").lower()
        if host == "localhost" or host.endswith(".local") or host.endswith(".internal"):
            return False
        try:
            address = ipaddress.ip_address(host)
            return not (address.is_private or address.is_loopback or address.is_link_local or address.is_reserved)
        except ValueError:
            return True
    except (TypeError, ValueError):
        return False


def _github_reference_parts(value):
    """Return semantic components only for bounded, recognizable GitHub references."""
    try:
        parsed = urlsplit(value)
    except (TypeError, ValueError):
        return None
    if parsed.scheme != "https" or parsed.netloc.lower() != "github.com":
        return None
    parts = parsed.path.removeprefix("/").split("/")
    if len(parts) < 4 or any(not part for part in parts):
        return None
    owner, repository, kind, target, *remainder = parts
    if any(part in {".", ".."} or not GITHUB_SLUG_PART.fullmatch(part)
           for part in (owner, repository)):
        return None
    if kind == "commit":
        recognized = not remainder and FULL_GIT_OID.fullmatch(target)
    elif kind in {"issues", "pull"}:
        recognized = not remainder and GITHUB_NUMBER.fullmatch(target)
    elif kind == "compare":
        revisions = target.split("...")
        recognized = not remainder and len(revisions) == 2 \
            and all(FULL_GIT_OID.fullmatch(revision) for revision in revisions)
    elif kind == "blob":
        recognized = bool(remainder) and FULL_GIT_OID.fullmatch(target)
    elif kind == "tree":
        recognized = FULL_GIT_OID.fullmatch(target)
    else:
        recognized = False
    if not recognized:
        return None
    return (*parts, parsed.query, parsed.fragment)


def _entropy_candidates(value):
    """Keep URL path delimiters from joining distinct public reference components."""
    cursor = 0
    for match in EMBEDDED_URL.finditer(value):
        yield from TOKENISH.findall(value[cursor:match.start()])
        raw_url = match.group()
        url = raw_url.rstrip(".,;:!?")
        parts = _github_reference_parts(url)
        if parts is None:
            yield from TOKENISH.findall(raw_url)
        else:
            for part in parts:
                yield from TOKENISH.findall(part)
        cursor = match.end()
    yield from TOKENISH.findall(value[cursor:])


def _privacy(value, key=""):
    if isinstance(value, dict):
        for child_key, child_value in value.items():
            _privacy(child_value, child_key)
        return
    if isinstance(value, list):
        for item in value:
            _privacy(item, key)
        return
    if not isinstance(value, str):
        return
    decoded = value
    for _ in range(3):
        expanded = unquote(decoded)
        if expanded == decoded:
            break
        decoded = expanded
    if SECRET_OR_PII.search(decoded):
        raise ValueError("Potential sensitive data; review locally")
    if any(not _public_https_url(url.rstrip(".,;:!?")) for url in EMBEDDED_URL.findall(decoded)):
        raise ValueError("Potential sensitive data; review locally")
    for candidate in IPV6_CANDIDATE.findall(decoded):
        try:
            ipaddress.IPv6Address(candidate.rstrip("."))
        except ValueError:
            continue
        raise ValueError("Potential sensitive data; review locally")
    if key in {"url", "upstream_url", "supporting_links", "sources"} and not _public_https_url(value):
        raise ValueError("Forbidden, credentialed, private, or local URL")
    if any(unicodedata.category(char) in {"Cf", "Cs", "Cc"} and char not in "\n\t" for char in value):
        raise ValueError("Hidden or control characters are not allowed")
    for token in _entropy_candidates(decoded):
        if len(token) >= 40 and _entropy(token) >= 4.0:
            raise ValueError("Potential high-entropy secret; review locally")


def validate_public_text(value):
    """Apply the bounded record privacy lint to one prospective public text value."""
    if not isinstance(value, str):
        raise ValueError("Public text must be a string")
    _privacy(value)
    return value


def _parse_json(raw):
    if isinstance(raw, bytes):
        if len(raw) > MAX_RECORD_BYTES:
            raise ValueError("Record exceeds 64 KiB")
        raw = raw.decode("utf-8")
    if not isinstance(raw, str):
        raise ValueError("Record input must be JSON text")
    if len(raw.encode("utf-8")) > MAX_RECORD_BYTES:
        raise ValueError("Record exceeds 64 KiB")
    return json.loads(raw, object_pairs_hook=_pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Non-finite JSON")))


def parse_record(raw):
    """Parse one bounded v1 record without coercion, fetching, or execution."""
    try:
        record = _parse_json(raw)
        if not isinstance(record, dict) or record.get("schema_version") != 1:
            raise ValueError("Unknown or unsupported schema version")
        record_type = record.get("type")
        if not isinstance(record_type, str) or record_type not in VALIDATORS:
            raise ValueError("Unknown record type")
        if not VALIDATORS[record_type].is_valid(record):
            raise ValueError("Record does not conform to its strict v1 schema")
        _privacy(record)
        return record
    except (RecursionError, UnicodeDecodeError) as exc:
        raise ValueError("Record nesting or encoding exceeds supported bounds") from exc


def parse_case(raw):
    """Compatibility entry point for callers that only accept case records."""
    record = parse_record(raw)
    if record["type"] != "case":
        raise ValueError("Expected a case record")
    return record


def _coerce_record(candidate):
    if isinstance(candidate, (str, bytes)):
        return parse_record(candidate)
    if isinstance(candidate, dict):
        try:
            return parse_record(json.dumps(candidate, ensure_ascii=False, allow_nan=False))
        except (TypeError, ValueError) as exc:
            raise ValueError("Invalid record object") from exc
    raise ValueError("Record must be a JSON object or JSON text")


def _validate_topology(report):
    environment = report["payload"]["environment"]
    for field, key in (("features", "feature"), ("settings", "setting")):
        keys = [entry[key] for entry in environment.get(field, [])]
        if len(keys) != len(set(keys)):
            raise ValueError(f"Duplicate environment {field}")
    aliases = [component["alias"] for component in environment["components"]]
    if len(aliases) != len(set(aliases)):
        raise ValueError("Duplicate environment component alias")
    topology, nodes = environment["topology"], set(environment["topology"]["nodes"])
    if not set(aliases).issubset(nodes):
        raise ValueError("Topology must include each declared component alias")
    containment = {}
    for edge in topology["edges"]:
        if edge["from"] == edge["to"] or edge["from"] not in nodes or edge["to"] not in nodes:
            raise ValueError("Invalid topology edge")
        if not set(edge.get("via", [])).issubset(nodes):
            raise ValueError("Topology edge has unknown local alias")
        if edge["relation"] == "contains":
            containment.setdefault(edge["from"], set()).add(edge["to"])
    visiting, visited = set(), set()

    def visit(node):
        if node in visiting:
            raise ValueError("Topology relation cycle")
        if node not in visited:
            visiting.add(node)
            for neighbor in containment.get(node, ()):
                visit(neighbor)
            visiting.remove(node)
            visited.add(node)

    for node in containment:
        visit(node)


def _validate_event_cycles(by_id):
    graph = {}
    for record in by_id.values():
        payload = record["payload"]
        if record["type"] == "event" and payload["event_kind"] == "supersession":
            relation = payload["relation"]
            graph.setdefault(relation["from"]["id"], set()).add(relation["to"]["id"])
    visiting, visited = set(), set()
    for identifier in graph:
        if identifier in visited:
            continue
        visiting.add(identifier)
        stack = [(identifier, iter(graph.get(identifier, ())))]
        while stack:
            node, targets = stack[-1]
            try:
                target = next(targets)
            except StopIteration:
                stack.pop()
                visiting.remove(node)
                visited.add(node)
                continue
            if target in visiting:
                raise ValueError("Event reference cycle")
            if target not in visited:
                visiting.add(target)
                stack.append((target, iter(graph.get(target, ()))))


def _validate_applicability_topologies(applicability):
    predicate_lists = [applicability.get(name, []) for name in ("requires", "excludes", "uncertain_conditions")]
    predicate_lists.extend(applicability.get("requires_any", []))
    for predicates in predicate_lists:
        for predicate in predicates:
            if predicate["kind"] != "topology":
                continue
            topology = predicate["topology"]
            aliases = [binding["alias"] for binding in topology["selectors"]]
            if len(aliases) != len(set(aliases)):
                raise ValueError("Duplicate topology predicate alias")
            known_aliases = set(aliases)
            for edge in topology["required_edges"]:
                if edge["from"] == edge["to"] or edge["from"] not in known_aliases or edge["to"] not in known_aliases:
                    raise ValueError("Topology predicate edge has unknown local alias")
                if not set(edge.get("via", [])).issubset(known_aliases):
                    raise ValueError("Topology predicate edge has unknown local alias")


def validate_corpus(records):
    """Validate cross-record IDs, typed references, intent, topology, and event graphs."""
    parsed, by_id = [], {}
    for candidate in records:
        record = _coerce_record(candidate)
        if record["id"] in by_id:
            raise ValueError("Duplicate record ID")
        parsed.append(record)
        by_id[record["id"]] = record
    for record in parsed:
        payload = record["payload"]
        if record["type"] == "change":
            case = by_id.get(payload["case_id"])
            if not case or case["type"] != "case":
                raise ValueError("Invalid case reference")
            if case["payload"]["intent"] == "optional" and payload["intent"] == "corrective":
                raise ValueError("Change cannot promote an optional case to corrective")
            _validate_applicability_topologies(payload["applicability"])
        elif record["type"] == "report":
            case = by_id.get(payload["case_id"])
            if not case or case["type"] != "case":
                raise ValueError("Invalid case reference")
            if "change_id" in payload:
                change = by_id.get(payload["change_id"])
                if not change or change["type"] != "change" or change["payload"]["case_id"] != case["id"]:
                    raise ValueError("Invalid change reference")
            _validate_topology(record)
        elif record["type"] == "event":
            target_ids = set()
            for target in payload["targets"]:
                referenced = by_id.get(target["id"])
                if not referenced or referenced["type"] != target["type"] or target["id"] == record["id"]:
                    raise ValueError("Invalid event target reference")
                if target["id"] in target_ids:
                    raise ValueError("Duplicate event target reference")
                target_ids.add(target["id"])
            for report_id in payload.get("supporting_reports", []):
                report = by_id.get(report_id)
                if not report or report["type"] != "report":
                    raise ValueError("Invalid supporting report reference")
            if payload["event_kind"] == "upstream-resolution" and not any(target["type"] == "case" for target in payload["targets"]):
                raise ValueError("Resolution event must target a case")
            if "relation" in payload:
                relation = payload["relation"]
                expected_relation = {"supersession": "supersedes", "correction": "corrects", "withdrawal": "withdraws", "dispute": "disputes"}
                if relation["kind"] != expected_relation[payload["event_kind"]]:
                    raise ValueError("Invalid directional event relation")
                endpoints = (relation["from"], relation["to"])
                if endpoints[0] == endpoints[1]:
                    raise ValueError("Event relation cannot target itself")
                if relation["kind"] in {"supersedes", "corrects"} and endpoints[0]["type"] != endpoints[1]["type"]:
                    raise ValueError("Supersession and correction endpoints must have compatible types")
                for endpoint in endpoints:
                    referenced = by_id.get(endpoint["id"])
                    if not referenced or referenced["type"] != endpoint["type"]:
                        raise ValueError("Invalid directional event reference")
                    if endpoint not in payload["targets"]:
                        raise ValueError("Directional event endpoint must be a target")
    _validate_event_cycles(by_id)
    return parsed


_DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


def _bounded_directory_names(directory_fd: int, maximum: int) -> list[str]:
    """Enumerate a stable directory descriptor without exceeding input bounds."""
    scan_fd = os.open(".", _DIRECTORY_FLAGS, dir_fd=directory_fd)
    try:
        with os.scandir(scan_fd) as entries:
            names = [entry.name for entry in itertools.islice(entries, maximum + 1)]
    finally:
        os.close(scan_fd)
    if len(names) > maximum:
        raise ValueError("Directory entry count exceeds bound")
    return sorted(names)


def _regular_record(directory_fd: int, name: str) -> bytes:
    """Read a regular, non-symlink record file bounded before parsing."""
    try:
        descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                             dir_fd=directory_fd)
    except OSError as exc:
        raise ValueError("Canonical record is inaccessible or a symlink") from exc
    with os.fdopen(descriptor, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError("Canonical records must be regular JSON files")
        raw = stream.read(MAX_RECORD_BYTES + 1)
    if len(raw) > MAX_RECORD_BYTES:
        raise ValueError("Record exceeds 64 KiB")
    return raw


def load_records(root: Path, *, existing=()) -> list[dict]:
    """Load bounded canonical records, validating references against explicit context."""
    if not isinstance(existing, (list, tuple)):
        raise ValueError("Existing corpus context must be a list or tuple")
    accepted_existing = validate_corpus(list(existing))
    try:
        root_fd = os.open(Path(root), _DIRECTORY_FLAGS)
    except OSError as exc:
        raise ValueError("Records root must be a real directory") from exc

    records, total = [], 0
    try:
        entries = _bounded_directory_names(root_fd, len(RECORD_DIRECTORIES))
        for name in entries:
            try:
                mode = os.stat(name, dir_fd=root_fd, follow_symlinks=False).st_mode
            except OSError as exc:
                raise ValueError("Records entry is inaccessible") from exc
            if stat.S_ISLNK(mode):
                raise ValueError("Records symlinks are rejected")
            if name not in RECORD_DIRECTORIES or not stat.S_ISDIR(mode):
                raise ValueError("Records root contains an unexpected path")
            try:
                directory_fd = os.open(name, _DIRECTORY_FLAGS, dir_fd=root_fd)
            except OSError as exc:
                raise ValueError("Canonical record directory is inaccessible or a symlink") from exc
            try:
                filenames = _bounded_directory_names(directory_fd, MAX_RECORDS - len(records))
                for filename in filenames:
                    if len(records) >= MAX_RECORDS:
                        raise ValueError("Record count exceeds bound")
                    match = CANONICAL_FILENAME.fullmatch(filename)
                    try:
                        mode = os.stat(filename, dir_fd=directory_fd, follow_symlinks=False).st_mode
                    except OSError as exc:
                        raise ValueError("Canonical record is inaccessible") from exc
                    if stat.S_ISLNK(mode):
                        raise ValueError("Records symlinks are rejected")
                    if match is None or not stat.S_ISREG(mode):
                        raise ValueError("Canonical records must be UUID-named regular JSON files")
                    raw = _regular_record(directory_fd, filename)
                    total += len(raw)
                    if total > MAX_RECORD_CONTENT_BYTES:
                        raise ValueError("Record content exceeds snapshot size bound")
                    record = parse_record(raw)
                    if record["id"] != match.group(1) or record["type"] != RECORD_DIRECTORIES[name]:
                        raise ValueError("Canonical record path, type, and ID do not align")
                    records.append(record)
            finally:
                os.close(directory_fd)
    finally:
        os.close(root_fd)
    combined = validate_corpus([*accepted_existing, *records])
    return combined[len(accepted_existing):]
