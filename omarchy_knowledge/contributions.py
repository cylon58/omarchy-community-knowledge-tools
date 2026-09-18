"""Prepare exact local drafts and validate untrusted contributions as inert data."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tempfile
from contextlib import contextmanager
from urllib.parse import unquote, urlsplit

from .content_flags import dangerous_content_flags
from .records import (
    MAX_RECORD_BYTES,
    MAX_RECORD_CONTENT_BYTES,
    MAX_RECORDS,
    RECORD_DIRECTORIES,
    load_records,
    parse_record,
    validate_corpus,
    validate_public_text,
)


MAX_PR_RECORDS = 25
MAX_PR_RECORD_CONTENT_BYTES = 1_638_400
_REPOSITORY = re.compile(r"[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}\Z")
_OID = re.compile(r"[0-9a-f]{40}\Z")
_UUID = re.compile(r"(?<![0-9A-Fa-f])[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}(?![0-9A-Fa-f])")
_DEVICE_LABEL = re.compile(
    r"\b(?:device[ _-]?uuid|machine[ _-]?id|serial(?:[ _-]?number)?)\b\s*[:=#-]?\s*[A-Za-z0-9][A-Za-z0-9._:-]{5,}",
    re.IGNORECASE,
)
_RECORD_PATH = re.compile(
    r"records/(cases|changes|reports|events)/"
    r"([0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12})\.json\Z"
)
_DIRECTORY_BY_TYPE = {value: key for key, value in RECORD_DIRECTORIES.items()}
_STRUCTURAL_ID_KEYS = {"id", "case_id", "change_id", "supporting_reports"}
_NOREPLY = re.compile(r"(?:[0-9]+\+)?[A-Za-z0-9_.-]+@users\.noreply\.github\.com\Z", re.IGNORECASE)


def _canonical(record: dict) -> str:
    return json.dumps(record, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n"


def _walk_strings(value, path=()):
    if isinstance(value, str):
        yield path, value
    elif isinstance(value, dict):
        for key, child in value.items():
            yield from _walk_strings(child, (*path, key))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _walk_strings(child, (*path, index))


def _is_structural_id(path, value):
    key = next((item for item in reversed(path) if isinstance(item, str)), "")
    return key in _STRUCTURAL_ID_KEYS and _UUID.fullmatch(value) is not None


def _check_unique_device_text(value, *, path=()):
    decoded = value
    for _ in range(3):
        expanded = unquote(decoded)
        if expanded == decoded:
            break
        decoded = expanded
    if _DEVICE_LABEL.search(decoded):
        raise ValueError("Potential unique device identifier; remove it before sharing")
    if _UUID.search(decoded) and not _is_structural_id(path, decoded):
        raise ValueError("Potential unique device identifier; remove it before sharing")


def _check_record_privacy(record):
    for path, value in _walk_strings(record):
        _check_unique_device_text(value, path=path)


def is_official_release_url(value, upstream):
    try:
        parsed = urlsplit(value)
        upstream_parsed = urlsplit(upstream)
    except (TypeError, ValueError):
        return False
    path = parsed.path.strip("/").split("/")
    upstream_path = upstream_parsed.path.strip("/").split("/")
    return (
        parsed.scheme == "https"
        and parsed.netloc.lower() == "github.com"
        and upstream_parsed.scheme == "https"
        and upstream_parsed.netloc.lower() == "github.com"
        and len(path) >= 5
        and len(upstream_path) >= 2
        and path[:2] == upstream_path[:2]
        and path[2:4] == ["releases", "tag"]
        and bool(path[4])
        and not parsed.username
        and not parsed.password
    )


def _check_new_release_claim(record):
    payload = record["payload"]
    if record["type"] == "event" and payload.get("event_kind") == "upstream-resolution":
        upstream = payload["resolution"]["upstream_url"]
        if not any(is_official_release_url(url, upstream) for url in payload.get("supporting_links", ())):
            raise ValueError("A new release claim requires a direct official release URL")


def _safe_new_output(output):
    output = Path(output)
    if output.exists() or output.is_symlink():
        raise ValueError("Draft output already exists")
    parent = output.parent
    parent.mkdir(parents=True, exist_ok=True)
    current = parent
    while True:
        if current.is_symlink() or not current.is_dir():
            raise ValueError("Draft output path has an unsafe parent")
        if current == current.parent:
            break
        current = current.parent
    return output


def draft(records: list[dict], existing: list[dict], output: Path) -> dict:
    """Validate and atomically create a new directory of canonical record files."""
    if not isinstance(records, list) or not records:
        raise ValueError("Draft requires at least one record")
    if not isinstance(existing, list):
        raise ValueError("Existing corpus must be a list")
    accepted_existing = validate_corpus(existing)
    combined = validate_corpus([*accepted_existing, *records])
    new = combined[len(accepted_existing):]
    for record in new:
        _check_record_privacy(record)
        _check_new_release_claim(record)
    output = _safe_new_output(output)
    temporary = Path(tempfile.mkdtemp(prefix=f".{output.name}-", dir=output.parent))
    files = []
    try:
        for record in sorted(new, key=lambda item: (_DIRECTORY_BY_TYPE[item["type"]], item["id"])):
            relative = Path("records") / _DIRECTORY_BY_TYPE[record["type"]] / f"{record['id']}.json"
            path = temporary / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            content = _canonical(record)
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            files.append({"path": relative.as_posix(), "content": content})
        os.replace(temporary, output)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return {
        "output": str(output),
        "record_count": len(new),
        "files": files,
        "publication_performed": False,
    }


def _preview_text(label, value, maximum):
    if not isinstance(value, str) or not 1 <= len(value.encode("utf-8")) <= maximum:
        raise ValueError(f"Invalid preview {label}")
    validate_public_text(value)
    _check_unique_device_text(value)
    return value


def preview(draft_dir: Path, repository: str, *, title: str, body: str, attribution: str,
            existing=()) -> dict:
    """Return the exact prospective payload without publishing or recording consent."""
    if not isinstance(repository, str) or not _REPOSITORY.fullmatch(repository):
        raise ValueError("Repository must be an owner/name destination")
    title = _preview_text("title", title, 240)
    body = _preview_text("body", body, 20_000)
    attribution = _preview_text("attribution", attribution, 1_000)
    draft_dir = Path(draft_dir)
    records = load_records(draft_dir / "records", existing=existing)
    if not records:
        raise ValueError("Draft contains no records")
    files = []
    for record in sorted(records, key=lambda item: (_DIRECTORY_BY_TYPE[item["type"]], item["id"])):
        _check_record_privacy(record)
        _check_new_release_claim(record)
        relative = Path("records") / _DIRECTORY_BY_TYPE[record["type"]] / f"{record['id']}.json"
        path = draft_dir / relative
        mode = path.lstat().st_mode
        if not stat.S_ISREG(mode) or stat.S_ISLNK(mode):
            raise ValueError("Draft records must be regular files")
        content = path.read_text(encoding="utf-8")
        if parse_record(content) != record:
            raise ValueError("Draft record changed while previewing")
        files.append({"path": relative.as_posix(), "content": content})
    return {
        "repository": repository,
        "title": title,
        "body": body,
        "attribution": attribution,
        "files": files,
        "records": records,
        "publication_performed": False,
        "privacy_reviewed": False,
        "privacy_lint": "passed",
    }


def _selector_text(selector):
    if selector["kind"] == "software":
        description = selector["component"]
        if "name" in selector:
            description += f" ({selector['name']})"
        return description
    parts = [selector["role"]]
    for key in ("vendor", "product", "model"):
        if key in selector:
            parts.append(selector[key])
    identifiers = [selector[key] for key in ("vendor_id", "product_id") if key in selector]
    if identifiers:
        parts.append("public product IDs " + ":".join(identifiers))
    return " ".join(parts)


def _edge_text(edge):
    text = f"{edge['from']} {edge['relation'].replace('_', ' ')} {edge['to']}"
    if "transport" in edge:
        text += f" over {edge['transport']}"
    if edge.get("via"):
        text += " via " + ", ".join(edge["via"])
    if "detail" in edge:
        text += f" ({edge['detail']} detail)"
    return text


def _predicate_text(predicate):
    kind = predicate["kind"]
    if kind == "software":
        constraints = ", ".join(
            constraint["op"] + " " + constraint["version"]
            for constraint in predicate["constraints"]
        )
        text = f"{_selector_text(predicate['selector'])} version {constraints} ({predicate['scheme']})"
        if "channel" in predicate:
            text += f", {predicate['channel']} channel"
        if "architecture" in predicate:
            text += f", {predicate['architecture']}"
        return text
    if kind == "component":
        return ("has " if predicate["presence"] else "does not have ") + _selector_text(predicate["selector"])
    if kind in {"feature", "setting"}:
        return f"{kind} {predicate[kind]} is {predicate['value']}"
    topology = predicate["topology"]
    equipment = ", ".join(
        f"{binding['alias']} ({_selector_text(binding['selector'])})"
        for binding in topology["selectors"]
    )
    interactions = "; ".join(_edge_text(edge) for edge in topology["required_edges"])
    return f"equipment {equipment}, interacting as {interactions}"


def _provenance_lines(record):
    provenance = record["provenance"]
    lines = [f"Evidence origin: {provenance['kind']}."]
    if provenance.get("sources"):
        lines.append("Source links: " + ", ".join(provenance["sources"]) + ".")
    return lines


def _case_lines(record):
    payload = record["payload"]
    intent = "optional preference" if payload["intent"] == "optional" else f"{payload['intent']} observation"
    lines = [
        f"{payload['title']} ({intent}).",
        f"Observed: {payload['observed']}",
        f"Expected ({payload['expectation']['basis'].replace('-', ' ')}): {payload['expectation']['text']}",
        "Relevant areas: " + ", ".join(payload["domains"]) + ".",
    ]
    if "introduced_by" in payload:
        lines.append("First noticed after: " + payload["introduced_by"])
    lines.extend(_provenance_lines(record))
    return lines


def _change_lines(record):
    payload = record["payload"]
    intent = "optional preference" if payload["intent"] == "optional" else payload["intent"] + " change"
    article = "an" if intent[0].lower() in "aeiou" else "a"
    lines = [f"Proposed change: {payload['explanation']} This is {article} {intent} using {payload['method']}."]
    procedure = payload["procedure"]
    if procedure["kind"] == "instructions":
        lines.append("Steps or commands: " + " ".join(procedure["steps"]))
    else:
        reference = procedure["reference"]
        detail = (
            f"Referenced {reference['artifact_kind']} {reference['identifier']} version {reference['version']} "
            f"at revision {reference['revision']}: {reference['source_description']}"
        )
        if "url" in reference:
            detail += "; link " + reference["url"]
        if "path" in reference:
            detail += "; path " + reference["path"]
        lines.append(detail + ".")
    applicability = payload["applicability"]
    if applicability["requires"]:
        lines.append("Applies when: " + "; ".join(_predicate_text(item) for item in applicability["requires"]) + ".")
    for group in applicability.get("requires_any", ()):
        lines.append("Also requires one of: " + "; or ".join(_predicate_text(item) for item in group) + ".")
    if applicability.get("excludes"):
        lines.append("Does not apply when: " + "; ".join(_predicate_text(item) for item in applicability["excludes"]) + ".")
    if applicability.get("uncertain_conditions"):
        lines.append("Applicability remains uncertain when: " + "; ".join(
            _predicate_text(item) for item in applicability["uncertain_conditions"]
        ) + ".")
    lines.extend((
        f"Risk: {payload['risk']}. Expected effects: {payload['effects']}",
        f"Rollback: {payload['rollback']}",
        f"How to check it: {payload['validation_plan']}",
        f"Root access: {payload['requires_root']}. Activation: {payload['activation']['required']} — {payload['activation']['details']}",
    ))
    if payload["affected_files"]:
        lines.append("Configuration paths affected: " + ", ".join(payload["affected_files"]) + ".")
    lines.extend(_provenance_lines(record))
    return lines


def _environment_lines(environment):
    context = [environment["architecture"]]
    for key in ("channel", "runtime_mode"):
        if key in environment:
            context.append(environment[key].replace("-", " "))
    lines = ["Environment: " + ", ".join(context) + f"; observation source: {environment['origin']}."]
    for component in environment["components"]:
        description = _selector_text(component["selector"])
        if component["alias"] != component["selector"].get("role"):
            description = component["alias"] + " — " + description
        text = "Equipment or software: " + description
        if "version" in component:
            text += f", version {component['version']} ({component['version_scheme']})"
        lines.append(text + ".")
    for feature in environment.get("features", ()):
        value = "yes" if feature["value"] is True else "no" if feature["value"] is False else feature["value"]
        lines.append(f"Feature observed: {feature['feature']} = {value}.")
    for setting in environment.get("settings", ()):
        value = "yes" if setting["value"] is True else "no" if setting["value"] is False else setting["value"]
        lines.append(f"Setting retained: {setting['setting']} = {value}.")
    for observation in environment.get("complete_component_observations", ()):
        lines.append(
            f"Component inventory: {_selector_text(observation['selector'])}, based on "
            f"{observation['basis'].replace('-', ' ')}."
        )
    for edge in environment["topology"]["edges"]:
        lines.append("Equipment interaction: " + _edge_text(edge) + ".")
    return lines


def _report_lines(record):
    payload = record["payload"]
    root = payload["root_cause"]
    lines = [
        f"Result: {payload['result']}.",
        f"Actual outcome: {payload['actual_result']}",
        f"Tested on {payload['observation_date']}: {payload['test_method']} Baseline: {payload['baseline']}; expected: {payload['expected_result']}",
        f"Limitations: {payload['limitations']}",
        f"Adverse effects: {payload['adverse_effects']}",
        f"Activation afterward: {payload['activation']['state']} — {payload['activation']['details']}",
        f"Root cause is {root['assessment']}: {root['rationale']}",
        f"Observation attribution: {payload['provenance']}.",
    ]
    if "duration" in payload:
        lines.append("Test duration: " + payload["duration"] + ".")
    if "repetitions" in payload:
        lines.append(f"Number of repetitions: {payload['repetitions']}.")
    if "intervention_deviations" in payload:
        lines.append("Differences from the proposed change: " + payload["intervention_deviations"])
    if "workaround_absent" in payload:
        lines.append("The workaround was absent: " + ("yes." if payload["workaround_absent"] else "no."))
    lines.extend(_environment_lines(payload["environment"]))
    lines.extend(_provenance_lines(record))
    return lines


def _event_lines(record):
    payload = record["payload"]
    lines = [
        f"Community event: {payload['event_kind'].replace('-', ' ')}.",
        f"Event reason: {payload['reason']}",
    ]
    if payload.get("supporting_reports"):
        lines.append(f"Supporting observations referenced: {len(payload['supporting_reports'])}.")
    if payload.get("supporting_links"):
        lines.append("Supporting links: " + ", ".join(payload["supporting_links"]) + ".")
    if "relation" in payload:
        lines.append("Relationship: " + payload["relation"]["kind"] + ".")
    if "resolution" in payload:
        resolution = payload["resolution"]
        lines.append(f"Release status remains {resolution['relevance']}.")
        lines.append("Upstream URL: " + resolution["upstream_url"])
        lines.append("Claimed fixed-in conditions: " + fixed_in_text(resolution["fixed_in"]) + ".")
        if "migration_required" in resolution:
            lines.append("Migration required: " + ("yes." if resolution["migration_required"] else "no."))
        if "restart_required" in resolution:
            lines.append("Restart required: " + resolution["restart_required"] + ".")
    lines.extend(_provenance_lines(record))
    return lines


def fixed_in_text(fixed_in):
    """Render validated alternative release conditions in ordinary language."""
    return "; or ".join(
        " and ".join(_predicate_text(item) for item in option["all_of"])
        for option in fixed_in["any_of"]
    )


def record_evidence_lines(record):
    """Render one validated inert record without contribution workflow wording."""
    renderers = {
        "case": _case_lines,
        "change": _change_lines,
        "report": _report_lines,
        "event": _event_lines,
    }
    return renderers[record["type"]](record)


def render_sharing_note(preview: dict, *, existing=()) -> str:
    """Render a faithful ordinary-language account; never manufacture privacy assurance."""
    required = {
        "repository", "title", "body", "attribution", "files", "records",
        "publication_performed", "privacy_reviewed", "privacy_lint",
    }
    if not isinstance(preview, dict) or set(preview) != required or preview["publication_performed"] is not False:
        raise ValueError("Invalid sharing preview")
    repository = preview["repository"]
    if not _REPOSITORY.fullmatch(repository):
        raise ValueError("Invalid sharing destination")
    if not isinstance(existing, (list, tuple)):
        raise ValueError("Existing corpus context must be a list or tuple")
    accepted_existing = validate_corpus(list(existing))
    combined = validate_corpus([*accepted_existing, *preview["records"]])
    records = combined[len(accepted_existing):]
    sections = {"What happened": [preview["body"]], "What we tried": [], "What happened afterward": []}
    for record in records:
        if record["type"] == "case":
            sections["What happened"].extend(_case_lines(record))
        elif record["type"] == "change":
            sections["What we tried"].extend(_change_lines(record))
        elif record["type"] == "report":
            sections["What happened afterward"].extend(_report_lines(record))
        else:
            sections["What happened afterward"].extend(_event_lines(record))
    lines = [preview["title"]]
    for heading, values in sections.items():
        lines.extend(("", heading))
        lines.extend(f"- {value}" for value in values or ["No record of this kind is included."])
    lines.extend((
        "", "Where this will be shared", f"- Public repository: {repository}",
        "- This preview did not publish, push, post, fork, or merge anything.",
        "", "Public name", f"- Public attribution: {preview['attribution']}",
        "", "Privacy check",
        "- Automated checks looked for credentials, contact/network details, private paths, hostnames, serial numbers, and unique device identifiers.",
        "- Relevant public make/model, software, settings, commands, versions, interactions, and links remain because they explain the observation.",
        "- A passing heuristic check is not a privacy guarantee. An agent must inspect every exact record, the PR title/body, links, and Git attribution before adding any reviewed privacy assurance or asking for approval.",
        "", "Exact technical detail",
        f"- {len(preview['files'])} exact JSON records—not only this note—will be public if submitted.",
        "- Use the JSON preview to inspect their structural record IDs, canonical paths, and exact contents before approval.",
    ))
    return "\n".join(lines) + "\n"


def _git_environment():
    return {
        "PATH": os.defpath,
        "LANG": "C",
        "LC_ALL": "C",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_ASKPASS": "/bin/false",
        "GIT_OPTIONAL_LOCKS": "0",
    }


def _git(repository, arguments, *, input=None, check=True):
    settings = [
        "-c", "core.hooksPath=/dev/null",
        "-c", "credential.helper=",
        "-c", "core.useReplaceRefs=false",
        "-c", "commit.gpgSign=false",
        "-c", "protocol.allow=never",
    ]
    try:
        return subprocess.run(
            ["git", *settings, "-C", str(repository), *arguments],
            input=input, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=_git_environment(), check=check, timeout=120,
        )
    except subprocess.CalledProcessError as exc:
        message = exc.stderr.decode("utf-8", "replace").strip()
        raise ValueError("Git validation failed" + (f": {message}" if message else "")) from exc


@contextmanager
def _isolated_object_view(repository):
    """Expose source objects through a new bare repo with no source configuration."""
    repository = Path(repository)
    if not repository.exists() or repository.is_symlink():
        raise ValueError("Repository must be a real local Git repository")
    discovery = subprocess.run(
        ["git", "-c", "core.hooksPath=/dev/null", "-C", str(repository),
         "rev-parse", "--path-format=absolute", "--git-common-dir"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=_git_environment(), timeout=30,
    )
    if discovery.returncode:
        raise ValueError("Repository is not a readable Git repository")
    try:
        common_text = discovery.stdout.decode("utf-8").strip()
    except UnicodeDecodeError as exc:
        raise ValueError("Git repository path is not UTF-8") from exc
    if not common_text or "\n" in common_text or "\r" in common_text:
        raise ValueError("Git repository path is unsafe")
    source_objects = Path(common_text) / "objects"
    if source_objects.is_symlink() or not source_objects.is_dir():
        raise ValueError("Git object directory must be a real directory")
    source_text = str(source_objects.resolve())
    if "\n" in source_text or "\r" in source_text:
        raise ValueError("Git object directory path is unsafe")
    with tempfile.TemporaryDirectory(prefix="omarchy-pr-check-") as temporary:
        trusted = Path(temporary) / "objects.git"
        initialized = subprocess.run(
            ["git", "init", "--bare", str(trusted)], stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, env=_git_environment(), timeout=30,
        )
        if initialized.returncode:
            raise ValueError("Could not create isolated Git validation store")
        alternates = trusted / "objects" / "info" / "alternates"
        alternates.write_text(source_text + "\n", encoding="utf-8")
        yield trusted


def _require_oid(value, label):
    if not isinstance(value, str) or not _OID.fullmatch(value):
        raise ValueError(f"{label} must be a full Git object ID")
    return value


def _diff_entries(repository, old, new):
    output = _git(repository, ["diff", "--raw", "--abbrev=40", "-z", "--no-renames", old, new, "--"]).stdout
    chunks = output.split(b"\0")
    if chunks and chunks[-1] == b"":
        chunks.pop()
    if len(chunks) % 2:
        raise ValueError("Git returned a malformed contribution diff")
    entries = []
    for index in range(0, len(chunks), 2):
        try:
            metadata = chunks[index].decode("ascii").split()
            path = chunks[index + 1].decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("Git contribution path is not UTF-8") from exc
        if len(metadata) != 5 or not metadata[0].startswith(":"):
            raise ValueError("Git returned a malformed contribution diff")
        old_mode = metadata[0][1:]
        new_mode, old_oid, new_oid, status_code = metadata[1:]
        entries.append((old_mode, new_mode, old_oid, new_oid, status_code, path))
    return entries


def _validate_additions(repository, old, new, *, enforce_pr_bounds, reject_flags):
    entries = _diff_entries(repository, old, new)
    rows, paths, total, flags = [], [], 0, set()
    limit = MAX_PR_RECORDS if enforce_pr_bounds else MAX_RECORDS
    byte_limit = MAX_PR_RECORD_CONTENT_BYTES if enforce_pr_bounds else MAX_RECORD_CONTENT_BYTES
    for old_mode, new_mode, old_oid, new_oid, status_code, path in entries:
        match = _RECORD_PATH.fullmatch(path)
        if (
            status_code != "A" or old_mode != "000000" or new_mode != "100644"
            or old_oid != "0" * 40 or not _OID.fullmatch(new_oid) or match is None
        ):
            raise ValueError("Contributions may only add canonical regular record files")
        if len(rows) >= limit:
            if enforce_pr_bounds:
                raise ValueError("A contribution may add at most 25 records")
            raise ValueError("Record count exceeds bound")
        raw = _git(repository, ["cat-file", "blob", new_oid]).stdout
        total += len(raw)
        if len(raw) > MAX_RECORD_BYTES or total > byte_limit:
            raise ValueError("Contribution record content exceeds size limit")
        record = parse_record(raw)
        expected_type = RECORD_DIRECTORIES[match.group(1)]
        if record["id"] != match.group(2) or record["type"] != expected_type:
            raise ValueError("Record filename, type, and ID do not align")
        _check_record_privacy(record)
        _check_new_release_claim(record)
        flags.update(dangerous_content_flags(record))
        rows.append(record)
        paths.append(path)
    if enforce_pr_bounds and not rows:
        raise ValueError("Contribution contains no new records")
    if reject_flags and flags:
        raise ValueError("Automatic intake rejected dangerous content: " + ",".join(sorted(flags)))
    return rows, paths, sorted(flags)


def _tree_records(repository, tree):
    output = _git(repository, ["ls-tree", "-r", "-z", tree, "--", "records/"]).stdout
    chunks = output.split(b"\0")
    if chunks and chunks[-1] == b"":
        chunks.pop()
    rows, total = [], 0
    for raw_entry in chunks:
        try:
            header, path_raw = raw_entry.split(b"\t", 1)
            mode, kind, oid = header.decode("ascii").split()
            path = path_raw.decode("utf-8")
        except (UnicodeDecodeError, ValueError) as exc:
            raise ValueError("Git tree contains a malformed record entry") from exc
        match = _RECORD_PATH.fullmatch(path)
        if mode != "100644" or kind != "blob" or not _OID.fullmatch(oid) or match is None:
            raise ValueError("Git tree contains a non-canonical record")
        if len(rows) >= MAX_RECORDS:
            raise ValueError("Record count exceeds bound")
        content = _git(repository, ["cat-file", "blob", oid]).stdout
        total += len(content)
        if len(content) > MAX_RECORD_BYTES or total > MAX_RECORD_CONTENT_BYTES:
            raise ValueError("Git record content exceeds snapshot bounds")
        record = parse_record(content)
        if record["id"] != match.group(2) or record["type"] != RECORD_DIRECTORIES[match.group(1)]:
            raise ValueError("Git record does not match its canonical path")
        rows.append(record)
    return validate_corpus(rows)


def _one_clean_commit(repository, merge_base, head):
    ancestry = _git(repository, ["rev-list", "--parents", "-n", "1", head]).stdout.decode().split()
    count = _git(repository, ["rev-list", "--count", f"{merge_base}..{head}"]).stdout.decode().strip()
    if len(ancestry) != 2 or ancestry[0] != head or ancestry[1] != merge_base or count != "1":
        raise ValueError("Prepare one clean data commit from the public base; private or multi-commit ancestry is rejected")


def _safe_commit_attribution(repository, head):
    raw = _git(repository, ["show", "-s", "--format=%ae%x00%ce%x00%an%x00%cn", head]).stdout
    values = raw.rstrip(b"\n").split(b"\0")
    if len(values) != 4:
        raise ValueError("commit attribution is malformed")
    try:
        author_email, committer_email, author_name, committer_name = (
            value.decode("utf-8") for value in values
        )
    except UnicodeDecodeError as exc:
        raise ValueError("commit attribution is malformed") from exc
    if not _NOREPLY.fullmatch(author_email) or not _NOREPLY.fullmatch(committer_email):
        raise ValueError("commit attribution must use an approved GitHub no-reply address")
    for value in (author_name, committer_name):
        validate_public_text(value)
        _check_unique_device_text(value)


def _validate_candidate(repository, base, head):
    repository = Path(repository)
    if not repository.exists() or repository.is_symlink():
        raise ValueError("Repository must be a real local Git repository")
    base = _require_oid(base, "base")
    head = _require_oid(head, "head")
    for oid in (base, head):
        resolved = _git(repository, ["rev-parse", "--verify", f"{oid}^{{commit}}"]).stdout.decode().strip()
        if resolved != oid:
            raise ValueError("Git reference did not resolve to the supplied commit")
    merge_bases = _git(repository, ["merge-base", "--all", base, head]).stdout.decode().splitlines()
    if len(merge_bases) != 1 or not _OID.fullmatch(merge_bases[0]):
        raise ValueError("Contribution and main need one unambiguous common base")
    ancestor = merge_bases[0]
    _one_clean_commit(repository, ancestor, head)
    _safe_commit_attribution(repository, head)
    additions, paths, flags = _validate_additions(
        repository, ancestor, head, enforce_pr_bounds=True, reject_flags=True,
    )
    merge = _git(repository, ["merge-tree", "--write-tree", "--no-messages", base, head], check=False)
    if merge.returncode != 0:
        raise ValueError("Contribution conflicts with current main")
    tree_lines = merge.stdout.decode("ascii", "strict").splitlines()
    if len(tree_lines) != 1 or not _OID.fullmatch(tree_lines[0]):
        raise ValueError("Git did not produce one prospective merge tree")
    tree = tree_lines[0]
    _validate_additions(repository, base, tree, enforce_pr_bounds=False, reject_flags=False)
    prospective = _tree_records(repository, tree)
    return {
        "base": base,
        "head": head,
        "merge_base": ancestor,
        "tree": tree,
        "added_records": additions,
        "added_paths": paths,
        "prospective_records": prospective,
        "diagnostic_codes": flags,
    }


def check_pr(repository: Path, base: str, head: str) -> dict:
    """Validate one complete, addition-only PR and its fully merged prospective corpus."""
    with _isolated_object_view(repository) as trusted:
        result = _validate_candidate(trusted, base, head)
    return {
        "base": result["base"],
        "head": result["head"],
        "merge_base": result["merge_base"],
        "tree": result["tree"],
        "added_paths": result["added_paths"],
        "added_record_count": len(result["added_records"]),
        "prospective_record_count": len(result["prospective_records"]),
        "diagnostic_codes": result["diagnostic_codes"],
        "valid": True,
    }
