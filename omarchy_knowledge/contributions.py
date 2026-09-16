"""Local-only drafts, exact-byte previews, consent notes, and inert audits."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Mapping, Sequence
import uuid
from datetime import datetime

from knowledge import parse_record, validate_corpus, validate_public_text


_UUID4 = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_REPOSITORY_ID = re.compile(r"^[1-9][0-9]{0,19}$")
_SLUG = re.compile(r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$")
_ROUTES = {"ledger", "toolkit", "upstream", "plugin"}


def _timestamp(value: Any) -> None:
    if not isinstance(value, str) or not value.endswith("Z") or len(value) > 40:
        raise ValueError("Invalid UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ValueError("Invalid UTC timestamp") from exc
    if parsed.utcoffset() is None or parsed.utcoffset().total_seconds() != 0:
        raise ValueError("Invalid UTC timestamp")


def _canonical(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise ValueError("Refusing to overwrite a symlink")
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        try: os.unlink(temporary)
        except FileNotFoundError: pass


def write_local_json(path: str | Path, value: Any) -> None:
    """Atomically write local workflow state without following a final symlink."""
    _atomic_write(Path(path), json.dumps(value, sort_keys=True, indent=2,
                                        ensure_ascii=False, allow_nan=False).encode("utf-8") + b"\n")


def save_draft(candidate: Mapping[str, Any], corpus: Sequence[Mapping[str, Any]], output: str | Path) -> dict[str, Any]:
    record = parse_record(_canonical(candidate))
    combined = [item for item in corpus if item.get("id") != record["id"]] + [record]
    validate_corpus(combined)
    _atomic_write(Path(output), json.dumps(record, sort_keys=True, indent=2,
                                           ensure_ascii=False).encode("utf-8") + b"\n")
    return {"id": record["id"], "type": record["type"], "output": str(output),
            "valid": True, "record": record}


def _routes(config: Mapping[str, Any]) -> Mapping[str, Any]:
    if not isinstance(config, Mapping) or set(config) != {"routing_version", "destinations"} \
            or config["routing_version"] != 1 or not isinstance(config["destinations"], Mapping) \
            or set(config["destinations"]) != _ROUTES:
        raise ValueError("Trusted routing configuration must define every separate destination")
    for name, destination in config["destinations"].items():
        if name in {'upstream', 'plugin'} and destination is None:
            continue
        if not isinstance(destination, Mapping) or set(destination) != {"provider", "repository_id", "slug"} \
                or destination["provider"] != "github" \
                or not isinstance(destination["repository_id"], str) \
                or not _REPOSITORY_ID.fullmatch(destination["repository_id"]) \
                or not isinstance(destination["slug"], str) or not _SLUG.fullmatch(destination["slug"]):
            raise ValueError(f"Invalid routing destination: {name}")
    return config["destinations"]


def _preview_text(label: str, text: Any, maximum: int) -> str:
    if not isinstance(text, str) or not 1 <= len(text.encode("utf-8")) <= maximum:
        raise ValueError(f"Invalid preview {label}")
    try:
        validate_public_text(text)
    except ValueError as exc:
        raise ValueError(f"Invalid preview {label}") from exc
    return text


def _validated_preview(preview: Mapping[str, Any], routing_config: Mapping[str, Any]) -> dict[str, Any]:
    required = {"preview_version", "destination_name", "destination", "title", "body", "attribution", "record"}
    if not isinstance(preview, Mapping) or set(preview) != required or preview["preview_version"] != 1 \
            or preview["destination_name"] not in _ROUTES:
        raise ValueError("Invalid publication preview")
    try:
        destinations = _routes(routing_config)
        parsed = parse_record(_canonical(preview["record"]))
        destination = destinations[preview["destination_name"]]
        if destination is None or preview["destination"] != destination:
            raise ValueError("Preview destination does not match trusted routing")
        title = _preview_text("title", preview["title"], 240)
        body = _preview_text("body", preview["body"], 20_000)
        attribution = _preview_text("attribution", preview["attribution"], 1000)
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("Invalid publication preview") from exc
    return {"preview_version": 1, "destination_name": preview["destination_name"],
            "destination": destination, "title": title, "body": body,
            "attribution": attribution, "record": parsed}


def build_preview(record: Mapping[str, Any], routing_config: Mapping[str, Any], *, destination: str,
                  title: str, body: str, attribution: str) -> dict[str, Any]:
    parsed = parse_record(_canonical(record))
    destinations = _routes(routing_config)
    if destination not in destinations or destinations[destination] is None:
        raise ValueError("Requested routing destination is unset")
    for label, text, maximum in (("title", title, 240), ("body", body, 20_000),
                                 ("attribution", attribution, 1000)):
        _preview_text(label, text, maximum)
    return {"preview_version": 1, "destination_name": destination,
            "destination": dict(destinations[destination]), "title": title, "body": body,
            "attribution": attribution, "record": parsed}


def publication_bytes(preview: Mapping[str, Any], routing_config: Mapping[str, Any]) -> bytes:
    return _canonical(_validated_preview(preview, routing_config))


def record_consent(preview: Mapping[str, Any], routing_config: Mapping[str, Any], *, approved_at: str,
                   receipt_id: str | None = None, explicit_local_command: bool = False) -> dict[str, Any]:
    if not explicit_local_command:
        raise ValueError("Consent notes require an explicit user-driven local command")
    content = publication_bytes(preview, routing_config)
    _timestamp(approved_at)
    identifier = receipt_id or str(uuid.uuid4())
    if not _UUID4.fullmatch(identifier):
        raise ValueError("Invalid consent receipt ID")
    return {"receipt_version": 1, "kind": "local-publication-consent-note",
            "receipt_id": identifier, "approved_at": approved_at,
            "publication_sha256": hashlib.sha256(content).hexdigest(),
            "scope": "exact-preview-bytes",
            "disclaimer": "Records a local explicit command; does not prove human consent or publication."}


def verify_consent(preview: Mapping[str, Any], routing_config: Mapping[str, Any],
                   receipt: Mapping[str, Any]) -> bool:
    required = {"receipt_version", "kind", "receipt_id", "approved_at", "publication_sha256", "scope", "disclaimer"}
    if not isinstance(receipt, Mapping) or set(receipt) != required \
            or receipt["receipt_version"] != 1 or receipt["kind"] != "local-publication-consent-note" \
            or not isinstance(receipt["publication_sha256"], str) \
            or not _SHA256.fullmatch(receipt["publication_sha256"]):
        return False
    try:
        digest = hashlib.sha256(publication_bytes(preview, routing_config)).hexdigest()
    except (KeyError, TypeError, ValueError):
        return False
    return digest == receipt["publication_sha256"]


def make_application_receipt(*, change_id: str, source_revision: str,
                             changed_files: Sequence[Mapping[str, str]], rollback: str,
                             recorded_at: str, receipt_id: str | None = None) -> dict[str, Any]:
    identifier = receipt_id or str(uuid.uuid4())
    if not _UUID4.fullmatch(identifier) or not _UUID4.fullmatch(change_id):
        raise ValueError("Invalid application receipt identity")
    if not isinstance(changed_files, Sequence) or isinstance(changed_files, (str, bytes)) \
            or not 1 <= len(changed_files) <= 24:
        raise ValueError("Application receipt requires bounded changed-file references")
    files = []
    for item in changed_files:
        if not isinstance(item, Mapping) or set(item) != {"reference", "observed_sha256"} \
                or not isinstance(item["reference"], str) or not item["reference"].startswith("{") \
                or not isinstance(item["observed_sha256"], str) or not _SHA256.fullmatch(item["observed_sha256"]):
            raise ValueError("Invalid changed-file reference")
        files.append(dict(item))
    if not isinstance(source_revision, str) or not 1 <= len(source_revision) <= 200 \
            or not isinstance(rollback, str) or not 1 <= len(rollback) <= 4000:
        raise ValueError("Application receipt requires source revision and rollback")
    _timestamp(recorded_at)
    return {"receipt_version": 1, "kind": "private-local-application",
            "receipt_id": identifier, "change_id": change_id, "source_revision": source_revision,
            "recorded_at": recorded_at, "changed_files": files, "rollback": rollback}


def audit_application_receipts(receipts: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    proposals = []
    for receipt in receipts:
        required = {"receipt_version", "kind", "receipt_id", "change_id", "source_revision",
                    "recorded_at", "changed_files", "rollback"}
        if not isinstance(receipt, Mapping) or set(receipt) != required \
                or receipt.get("receipt_version") != 1 or receipt.get("kind") != "private-local-application" \
                or not isinstance(receipt.get("receipt_id"), str) or not _UUID4.fullmatch(receipt["receipt_id"]) \
                or not isinstance(receipt.get("change_id"), str) or not _UUID4.fullmatch(receipt["change_id"]):
            raise ValueError("Audit accepts only private local application receipts")
        _timestamp(receipt["recorded_at"])
        # Reuse construction validation without changing or persisting anything.
        make_application_receipt(
            change_id=receipt["change_id"], source_revision=receipt["source_revision"],
            changed_files=receipt["changed_files"], rollback=receipt["rollback"],
            recorded_at=receipt["recorded_at"], receipt_id=receipt["receipt_id"],
        )
        proposals.append({"receipt_id": receipt.get("receipt_id"), "change_id": receipt.get("change_id"),
                          "action": "investigate-before-update-or-cleanup",
                          "changed_file_references": [item.get("reference") for item in receipt.get("changed_files", [])],
                          "rollback": receipt.get("rollback"), "source_revision": receipt.get("source_revision")})
    return {"executed": False, "deleted": False, "proposals": proposals,
            "disclaimer": "Audit proposes investigation only; it does not execute, modify, or delete configuration."}
