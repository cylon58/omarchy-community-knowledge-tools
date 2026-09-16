"""Deterministic, bounded local snapshots with integrity-only manifests."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import hmac
import itertools
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import stat
import tempfile
from typing import Any, Iterable

from knowledge import MAX_RECORD_BYTES, validate_corpus


MAX_RECORD_FILES = 4096
MAX_SNAPSHOT_BYTES = 32 * 1024 * 1024
MAX_MANIFEST_BYTES = 64 * 1024
_REVISION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+:/-]{0,199}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_MANIFEST_KEYS = {
    "manifest_version", "schema_version", "data_revision", "toolkit_revision",
    "created_at", "source_updated_at", "source", "record_count", "files",
}
_FILE_KEYS = {"sha256", "size"}
_SNAPSHOT_FILES = ("index.json", "records.jsonl")
_DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
_RECORD_DIRECTORIES = {"cases": "case", "changes": "change", "reports": "report", "events": "event"}
_UUID4_FILENAME = re.compile(r"^([0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12})\.json$")


@dataclass(frozen=True)
class Snapshot:
    path: Path
    manifest: dict[str, Any]
    records: tuple[dict[str, Any], ...]
    index: tuple[dict[str, Any], ...]
    canonical: dict[str, Any] | None = None


def _pairs(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("Duplicate JSON key")
        value[key] = item
    return value


def _json_loads(raw: bytes, *, label: str, maximum: int) -> Any:
    if len(raw) > maximum:
        raise ValueError(f"{label} exceeds size bound")
    try:
        return json.loads(raw, object_pairs_hook=_pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Non-finite JSON")))
    except (RecursionError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Invalid {label}") from exc


def _canonical(value: Any) -> bytes:
    try:
        return (json.dumps(value, sort_keys=True, separators=(",", ":"),
                           ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("Value is not canonical JSON data") from exc


def _timestamp(value: str) -> datetime:
    if not isinstance(value, str) or len(value) > 40 or not value.endswith("Z"):
        raise ValueError("Snapshot timestamp must be bounded UTC")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ValueError("Snapshot timestamp must be valid UTC") from exc
    if parsed.utcoffset() is None or parsed.utcoffset().total_seconds() != 0:
        raise ValueError("Snapshot timestamp must be UTC")
    return parsed


def _read_regular(path: Path, maximum: int) -> bytes:
    if path.is_symlink():
        raise ValueError("Snapshot and record symlinks are rejected")
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError as exc:
        raise ValueError("Snapshot file is inaccessible") from exc
    with os.fdopen(descriptor, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError("Snapshot input must be a regular file")
        raw = stream.read(maximum + 1)
    if len(raw) > maximum:
        raise ValueError("Snapshot file exceeds size bound")
    return raw


def _open_directory(path: str | Path, label: str, *, create: bool = False) -> int:
    absolute = Path(os.path.abspath(os.fspath(path)))
    descriptor = None
    try:
        descriptor = os.open(os.sep, _DIRECTORY_FLAGS)
        for component in absolute.parts[1:]:
            try:
                child = os.open(component, _DIRECTORY_FLAGS, dir_fd=descriptor)
            except FileNotFoundError:
                if not create:
                    raise
                try:
                    os.mkdir(component, 0o700, dir_fd=descriptor)
                except FileExistsError:
                    pass
                child = os.open(component, _DIRECTORY_FLAGS, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        return descriptor
    except OSError as exc:
        if descriptor is not None:
            os.close(descriptor)
        raise ValueError(f"{label} must be a real directory, not a symlink") from exc


def _open_directory_at(parent_fd: int, name: str, label: str) -> int:
    if not name or "/" in name or name in {".", ".."}:
        raise ValueError(f"Invalid {label} name")
    try:
        return os.open(name, _DIRECTORY_FLAGS, dir_fd=parent_fd)
    except OSError as exc:
        raise ValueError(f"{label} must be a real directory, not a symlink") from exc


def _read_regular_at(directory_fd: int, name: str, maximum: int) -> bytes:
    if not name or "/" in name or name in {".", ".."}:
        raise ValueError("Invalid snapshot filename")
    try:
        descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                             dir_fd=directory_fd)
    except OSError as exc:
        raise ValueError("Snapshot file is inaccessible or a symlink") from exc
    with os.fdopen(descriptor, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError("Snapshot input must be a regular file")
        raw = stream.read(maximum + 1)
    if len(raw) > maximum:
        raise ValueError("Snapshot file exceeds size bound")
    return raw


def _write_regular_at(directory_fd: int, name: str, content: bytes) -> None:
    descriptor = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o600, dir_fd=directory_fd)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())


def _bounded_directory_names(directory_fd: int, maximum: int) -> list[str]:
    # Btrfs can retain an empty enumeration view on a descriptor opened before
    # the directory was populated. Reopen the same inode, not its mutable path,
    # to obtain a fresh scan while retaining the no-symlink directory boundary.
    scan_fd = os.open('.', _DIRECTORY_FLAGS, dir_fd=directory_fd)
    try:
        with os.scandir(scan_fd) as entries:
            names = [entry.name for entry in itertools.islice(entries, maximum + 1)]
    finally:
        os.close(scan_fd)
    if len(names) > maximum:
        raise ValueError("Directory entry count exceeds bound")
    return names


def _source_records(source: Path) -> list[dict[str, Any]]:
    source_fd = _open_directory(source, "Snapshot source")
    records, total = [], 0

    def read_record(directory_fd: int, name: str) -> dict[str, Any]:
        nonlocal total
        raw = _read_regular_at(directory_fd, name, MAX_RECORD_BYTES)
        total += len(raw)
        if total > MAX_SNAPSHOT_BYTES:
            raise ValueError("Snapshot source total size exceeds bound")
        return _json_loads(raw, label="record", maximum=MAX_RECORD_BYTES)

    try:
        names = sorted(_bounded_directory_names(source_fd, MAX_RECORD_FILES))
        kinds = []
        for name in names:
            try:
                mode = os.stat(name, dir_fd=source_fd, follow_symlinks=False).st_mode
            except OSError as exc:
                raise ValueError("Snapshot source entry is inaccessible") from exc
            if stat.S_ISLNK(mode):
                raise ValueError("Snapshot source symlinks are rejected")
            kinds.append("directory" if stat.S_ISDIR(mode) else "file" if stat.S_ISREG(mode) else "other")

        if all(kind == "file" for kind in kinds):
            for name in names:
                if not name.endswith(".json"):
                    raise ValueError("Snapshot source may contain only regular JSON record files")
                records.append(read_record(source_fd, name))
        elif all(kind == "directory" for kind in kinds):
            if not set(names).issubset(_RECORD_DIRECTORIES):
                raise ValueError("Canonical snapshot source contains an unknown record directory")
            for directory_name in names:
                directory_fd = _open_directory_at(source_fd, directory_name, "canonical record directory")
                try:
                    remaining = MAX_RECORD_FILES - len(records)
                    filenames = sorted(_bounded_directory_names(directory_fd, remaining))
                    for filename in filenames:
                        match = _UUID4_FILENAME.fullmatch(filename)
                        if match is None:
                            raise ValueError("Canonical record filename must be its UUID")
                        try:
                            mode = os.stat(filename, dir_fd=directory_fd, follow_symlinks=False).st_mode
                        except OSError as exc:
                            raise ValueError("Canonical record is inaccessible") from exc
                        if stat.S_ISLNK(mode):
                            raise ValueError("Snapshot source symlinks are rejected")
                        if not stat.S_ISREG(mode):
                            raise ValueError("Canonical records must be regular JSON files")
                        record = read_record(directory_fd, filename)
                        if record.get("id") != match.group(1) \
                                or record.get("type") != _RECORD_DIRECTORIES[directory_name]:
                            raise ValueError("Canonical record path, type, and ID do not align")
                        records.append(record)
                finally:
                    os.close(directory_fd)
        else:
            raise ValueError("Snapshot source cannot mix flat files and canonical directories")
    finally:
        os.close(source_fd)
    return validate_corpus(records)


def _build_index(records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for record in records:
        payload = record["payload"]
        row = {"id": record["id"], "type": record["type"], "created_at": record["created_at"]}
        if record["type"] == "case":
            row.update({"title": payload["title"], "intent": payload["intent"],
                        "domains": payload["domains"],
                        "search_text": " ".join((payload["title"], payload["observed"],
                                                  payload["expectation"]["text"], *payload["domains"]))})
        elif record["type"] in {"change", "report"}:
            row["case_id"] = payload["case_id"]
            if "change_id" in payload:
                row["change_id"] = payload["change_id"]
        elif record["type"] == "event":
            row["target_ids"] = sorted(target["id"] for target in payload["targets"])
        rows.append(row)
    return sorted(rows, key=lambda row: row["id"])


def _write_snapshot(records: Iterable[dict[str, Any]], output: str | Path, *, data_revision: str,
                    toolkit_revision: str, created_at: str, source_updated_at: str,
                    source_kind: str, source_reference: str) -> dict[str, Any]:
    for revision in (data_revision, toolkit_revision):
        if not isinstance(revision, str) or not _REVISION.fullmatch(revision):
            raise ValueError("Invalid explicit snapshot revision")
    _timestamp(created_at); _timestamp(source_updated_at)
    if not isinstance(source_reference, str) or not 1 <= len(source_reference) <= 300:
        raise ValueError("Invalid local source reference")
    if any(ord(character) < 32 for character in source_reference):
        raise ValueError("Invalid local source reference")
    if source_kind not in {"local-directory", "admitted-git-tree"}:
        raise ValueError("Invalid snapshot source kind")
    records = sorted(validate_corpus(list(records)), key=lambda record: record["id"])
    record_bytes = b"".join(_canonical(record) for record in records)
    index_bytes = _canonical(_build_index(records))
    if len(record_bytes) + len(index_bytes) > MAX_SNAPSHOT_BYTES:
        raise ValueError("Snapshot exceeds total size bound")
    files = {
        "index.json": {"sha256": hashlib.sha256(index_bytes).hexdigest(), "size": len(index_bytes)},
        "records.jsonl": {"sha256": hashlib.sha256(record_bytes).hexdigest(), "size": len(record_bytes)},
    }
    manifest = {
        "manifest_version": 1, "schema_version": 1,
        "data_revision": data_revision, "toolkit_revision": toolkit_revision,
        "created_at": created_at, "source_updated_at": source_updated_at,
        "source": {"kind": source_kind, "reference": source_reference},
        "record_count": len(records), "files": files,
    }
    destination = Path(output)
    if destination.exists() or destination.is_symlink():
        raise ValueError("Snapshot output already exists")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{destination.name}-", dir=destination.parent))
    try:
        (temporary / "records.jsonl").write_bytes(record_bytes)
        (temporary / "index.json").write_bytes(index_bytes)
        (temporary / "manifest.json").write_bytes(_canonical(manifest))
        os.replace(temporary, destination)
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return manifest


def build_snapshot(source: str | Path, output: str | Path, *, data_revision: str,
                   toolkit_revision: str, created_at: str, source_updated_at: str,
                   source_reference: str) -> dict[str, Any]:
    """Build from a mutable directory; revision strings are caller labels, not inferred commits."""
    return _write_snapshot(
        _source_records(Path(source)), output, data_revision=data_revision,
        toolkit_revision=toolkit_revision, created_at=created_at,
        source_updated_at=source_updated_at, source_kind="local-directory",
        source_reference=source_reference,
    )


def build_snapshot_from_admitted_tree(candidate, objects, output: str | Path, *,
                                      toolkit_revision: str, created_at: str,
                                      source_updated_at: str) -> dict[str, Any]:
    """Check and snapshot exact immutable Git objects, never a mutable checkout.

    ``objects`` must be a trusted coordinator-owned object reader. The returned
    manifest's data revision is forced to the evaluated commit; callers cannot
    attach an arbitrary revision label to different working-directory bytes.
    """
    from .admission import PATH, check_tree
    from .git_objects import ObjectUnavailable

    admission = check_tree(candidate, objects)
    if admission.get("decision") != "accept":
        raise ValueError("Exact-tree admission did not accept the snapshot source")
    try:
        objects.begin()
        tree_oid = objects.commit(candidate.evaluated_commit_oid)
        if tree_oid != candidate.expected_tree_oid:
            raise ValueError("Admitted commit no longer binds the expected tree")
        records, total = [], 0
        for entry in sorted(objects.entries(tree_oid), key=lambda item: item.path):
            if entry.kind == "tree" or not entry.path.startswith(b"records/"):
                continue
            path = entry.path.decode("utf-8")
            match = PATH.fullmatch(path)
            if match is None or entry.mode != "100644" or entry.kind != "blob":
                raise ValueError("Admitted record path is invalid")
            raw = objects.blob(entry.oid, MAX_RECORD_BYTES)
            total += len(raw)
            if total > MAX_SNAPSHOT_BYTES:
                raise ValueError("Admitted snapshot source total size exceeds bound")
            record = _json_loads(raw, label="record", maximum=MAX_RECORD_BYTES)
            if record.get("id") != match.group(4) or record.get("type") + "s" != match.group(2):
                raise ValueError("Admitted record path, type, and ID do not align")
            records.append(record)
        manifest = _write_snapshot(
            records, output, data_revision=candidate.evaluated_commit_oid,
            toolkit_revision=toolkit_revision, created_at=created_at,
            source_updated_at=source_updated_at, source_kind="admitted-git-tree",
            source_reference=f"{candidate.evaluated_commit_oid}:{tree_oid}",
        )
    except (AttributeError, KeyError, ObjectUnavailable, TypeError, UnicodeError, ValueError) as exc:
        raise ValueError("Exact admitted Git tree is unavailable or inconsistent") from exc
    return {"admission": admission, "manifest": manifest}


def _validate_manifest(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != _MANIFEST_KEYS:
        raise ValueError("Snapshot manifest has unknown or missing fields")
    if value["manifest_version"] != 1 or value["schema_version"] != 1:
        raise ValueError("Snapshot manifest version is incompatible")
    for revision in (value["data_revision"], value["toolkit_revision"]):
        if not isinstance(revision, str) or not _REVISION.fullmatch(revision):
            raise ValueError("Snapshot manifest revision is invalid")
    _timestamp(value["created_at"]); _timestamp(value["source_updated_at"])
    if not isinstance(value["source"], dict) or set(value["source"]) != {"kind", "reference"} \
            or value["source"]["kind"] not in {"local-directory", "admitted-git-tree"} \
            or not isinstance(value["source"]["reference"], str) \
            or not 1 <= len(value["source"]["reference"]) <= 300 \
            or any(ord(character) < 32 for character in value["source"]["reference"]):
        raise ValueError("Snapshot manifest source is invalid")
    if not isinstance(value["record_count"], int) or isinstance(value["record_count"], bool) \
            or not 0 <= value["record_count"] <= MAX_RECORD_FILES:
        raise ValueError("Snapshot manifest record count is invalid")
    if not isinstance(value["files"], dict) or set(value["files"]) != set(_SNAPSHOT_FILES):
        raise ValueError("Snapshot manifest file set is invalid")
    for metadata in value["files"].values():
        if not isinstance(metadata, dict) or set(metadata) != _FILE_KEYS \
                or not isinstance(metadata["sha256"], str) or not _SHA256.fullmatch(metadata["sha256"]) \
                or not isinstance(metadata["size"], int) or isinstance(metadata["size"], bool) \
                or not 0 <= metadata["size"] <= MAX_SNAPSHOT_BYTES:
            raise ValueError("Snapshot manifest file metadata is invalid")
    return value


def _load_snapshot_directory(directory_fd: int, display_path: Path) -> Snapshot:
    entries = set(_bounded_directory_names(directory_fd, len(_SNAPSHOT_FILES) + 1))
    if entries != {"manifest.json", *_SNAPSHOT_FILES}:
        raise ValueError("Snapshot contains unknown or missing files")
    manifest_raw = _read_regular_at(directory_fd, "manifest.json", MAX_MANIFEST_BYTES)
    manifest = _validate_manifest(_json_loads(manifest_raw, label="manifest", maximum=MAX_MANIFEST_BYTES))
    if manifest_raw != _canonical(manifest):
        raise ValueError("Snapshot manifest is not canonical JSON")
    raw_files = {}
    for name in _SNAPSHOT_FILES:
        metadata = manifest["files"][name]
        raw = _read_regular_at(directory_fd, name, min(MAX_SNAPSHOT_BYTES, metadata["size"]))
        if len(raw) != metadata["size"] or hashlib.sha256(raw).hexdigest() != metadata["sha256"]:
            raise ValueError("Snapshot integrity mismatch")
        raw_files[name] = raw
    if len(raw_files["index.json"]) + len(raw_files["records.jsonl"]) > MAX_SNAPSHOT_BYTES:
        raise ValueError("Snapshot exceeds total size bound")
    lines = raw_files["records.jsonl"].splitlines(keepends=True)
    if not 0 <= len(lines) <= MAX_RECORD_FILES:
        raise ValueError("Snapshot record count exceeds bound")
    records = []
    for line in lines:
        if line in {b"", b"\n"}:
            raise ValueError("Invalid blank JSONL record")
        record = _json_loads(line, label="record", maximum=MAX_RECORD_BYTES)
        if line != _canonical(record):
            raise ValueError("Snapshot record is not canonical JSON")
        records.append(record)
    parsed = validate_corpus(records)
    if [record["id"] for record in parsed] != sorted(record["id"] for record in parsed):
        raise ValueError("Snapshot JSONL is not in deterministic canonical order")
    if len(parsed) != manifest["record_count"]:
        raise ValueError("Snapshot record count integrity mismatch")
    index = _json_loads(raw_files["index.json"], label="index", maximum=MAX_SNAPSHOT_BYTES)
    if raw_files["index.json"] != _canonical(index):
        raise ValueError("Snapshot index is not canonical JSON")
    if index != _build_index(parsed):
        raise ValueError("Snapshot index integrity mismatch")
    return Snapshot(display_path, manifest, tuple(parsed), tuple(index))


def load_snapshot(path: str | Path) -> Snapshot:
    directory = Path(path)
    descriptor = _open_directory(directory, "Snapshot")
    try:
        return _load_snapshot_directory(descriptor, directory)
    finally:
        os.close(descriptor)


def _open_cache(cache_path: Path, *, create: bool) -> int:
    return _open_directory(cache_path, "Cache root", create=create)


def _open_snapshots(cache_fd: int, *, create: bool) -> int:
    if create:
        try:
            os.mkdir("snapshots", 0o700, dir_fd=cache_fd)
        except FileExistsError:
            pass
    return _open_directory_at(cache_fd, "snapshots", "Cache snapshots component")


def _discard_slot(snapshots_fd: int, name: str) -> None:
    try:
        slot_fd = _open_directory_at(snapshots_fd, name, "Temporary cache slot")
    except ValueError:
        return
    try:
        for filename in _bounded_directory_names(slot_fd, len(_SNAPSHOT_FILES) + 1):
            os.unlink(filename, dir_fd=slot_fd)
    finally:
        os.close(slot_fd)
    os.rmdir(name, dir_fd=snapshots_fd)


def import_snapshot(candidate: str | Path, cache: str | Path) -> Snapshot:
    """Validate fully, copy into a content-addressed slot, then atomically switch CURRENT."""
    return _import_snapshot(candidate, cache)


def _import_snapshot(candidate, cache, *, canonical=None):
    """Internal canonical-sync seam. Only sync may supply authenticated provenance."""
    validated = load_snapshot(candidate)
    cache_path = Path(cache)
    identity = hashlib.sha256(_canonical(validated.manifest)).hexdigest()
    cache_fd = _open_cache(cache_path, create=True)
    snapshots_fd = None
    destination_fd = None
    try:
        snapshots_fd = _open_snapshots(cache_fd, create=True)
        try:
            destination_fd = _open_directory_at(snapshots_fd, identity, "Cache snapshot slot")
        except ValueError:
            temporary = f".candidate-{secrets.token_hex(16)}"
            os.mkdir(temporary, 0o700, dir_fd=snapshots_fd)
            moved = False
            try:
                temporary_fd = _open_directory_at(snapshots_fd, temporary, "Temporary cache slot")
                try:
                    for name in ("manifest.json", *_SNAPSHOT_FILES):
                        _write_regular_at(temporary_fd, name,
                                          _read_regular(validated.path / name, MAX_SNAPSHOT_BYTES))
                    _load_snapshot_directory(temporary_fd, cache_path / "snapshots" / temporary)
                finally:
                    os.close(temporary_fd)
                try:
                    os.rename(temporary, identity, src_dir_fd=snapshots_fd, dst_dir_fd=snapshots_fd)
                    moved = True
                except FileExistsError:
                    pass
            finally:
                if not moved:
                    _discard_slot(snapshots_fd, temporary)
            destination_fd = _open_directory_at(snapshots_fd, identity, "Cache snapshot slot")

        result = _load_snapshot_directory(destination_fd, cache_path / "snapshots" / identity)
        if hashlib.sha256(_canonical(result.manifest)).hexdigest() != identity:
            raise ValueError("Cache manifest identity mismatch")
        pointer = (identity + "\n").encode("ascii")
        if canonical is not None:
            try:
                _write_regular_at(cache_fd, ".canonical-key", secrets.token_bytes(32))
            except FileExistsError:
                pass
            key = _read_regular_at(cache_fd, ".canonical-key", 32)
            if len(key) != 32 or stat.S_IMODE(os.stat('.canonical-key', dir_fd=cache_fd,
                                                    follow_symlinks=False).st_mode) != 0o600:
                raise ValueError("Canonical key is unavailable or not private")
            payload = {"version": 1, "snapshot": identity, "provenance": canonical}
            pointer = _canonical({"payload": payload, "mac": hmac.digest(key, _canonical(payload), 'sha256').hex()})
            if len(pointer) > MAX_SNAPSHOT_BYTES:
                raise ValueError("Canonical provenance exceeds bound")
        pointer_name = f".CURRENT-{secrets.token_hex(16)}"
        try:
            _write_regular_at(cache_fd, pointer_name, pointer)
            os.replace(pointer_name, "CURRENT", src_dir_fd=cache_fd, dst_dir_fd=cache_fd)
        finally:
            try:
                os.unlink(pointer_name, dir_fd=cache_fd)
            except FileNotFoundError:
                pass
        return result
    finally:
        if destination_fd is not None:
            os.close(destination_fd)
        if snapshots_fd is not None:
            os.close(snapshots_fd)
        os.close(cache_fd)


def load_cache(cache: str | Path) -> Snapshot:
    cache_path = Path(cache)
    cache_fd = _open_cache(cache_path, create=False)
    snapshots_fd = None
    destination_fd = None
    try:
        snapshots_fd = _open_snapshots(cache_fd, create=False)
        try:
            raw = _read_regular_at(cache_fd, "CURRENT", MAX_SNAPSHOT_BYTES)
            provenance = None
            if raw.startswith(b'{'):
                envelope = _json_loads(raw, label='canonical pointer', maximum=MAX_SNAPSHOT_BYTES)
                if not isinstance(envelope, dict) or set(envelope) != {'payload', 'mac'} \
                        or not isinstance(envelope['payload'], dict) \
                        or set(envelope['payload']) != {'version', 'snapshot', 'provenance'} \
                        or not isinstance(envelope['mac'], str) or not _SHA256.fullmatch(envelope['mac']):
                    raise ValueError('Invalid canonical pointer')
                key = _read_regular_at(cache_fd, '.canonical-key', 32)
                if len(key) != 32 or stat.S_IMODE(os.stat('.canonical-key', dir_fd=cache_fd,
                                                        follow_symlinks=False).st_mode) != 0o600 \
                        or not hmac.compare_digest(
                        hmac.digest(key, _canonical(envelope['payload']), 'sha256').hex(), envelope['mac']):
                    raise ValueError('Invalid canonical seal')
                if envelope['payload']['version'] != 1:
                    raise ValueError('Invalid canonical pointer version')
                pointer = envelope['payload']['snapshot']
                provenance = envelope['payload']['provenance']
            else:
                pointer = raw.decode("ascii").strip()
        except UnicodeDecodeError as exc:
            raise ValueError("Cache pointer is invalid") from exc
        if not isinstance(pointer, str) or not _SHA256.fullmatch(pointer):
            raise ValueError("Cache pointer is invalid")
        destination_fd = _open_directory_at(snapshots_fd, pointer, "Cache snapshot slot")
        result = _load_snapshot_directory(destination_fd, cache_path / "snapshots" / pointer)
        if hashlib.sha256(_canonical(result.manifest)).hexdigest() != pointer:
            raise ValueError('Cache manifest identity mismatch')
        return Snapshot(result.path, result.manifest, result.records, result.index, provenance)
    finally:
        if destination_fd is not None:
            os.close(destination_fd)
        if snapshots_fd is not None:
            os.close(snapshots_fd)
        os.close(cache_fd)


def cache_status(cache: str | Path, *, now: datetime | None = None,
                 stale_after_seconds: int = 86400) -> dict[str, Any]:
    return snapshot_status(load_cache(cache), now=now, stale_after_seconds=stale_after_seconds)


def snapshot_status(snapshot: Snapshot, *, now: datetime | None = None,
                    stale_after_seconds: int = 86400) -> dict[str, Any]:
    """Status for the same already-loaded snapshot used by a query (no pointer race)."""
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    created = _timestamp(snapshot.manifest["created_at"])
    age = max(0, int((current - created).total_seconds()))
    return {
        "data_revision": snapshot.manifest["data_revision"],
        "toolkit_revision": snapshot.manifest["toolkit_revision"],
        "snapshot_created_at": snapshot.manifest["created_at"],
        "source_updated_at": snapshot.manifest["source_updated_at"],
        "checked_at": current.isoformat().replace("+00:00", "Z"),
        "age_seconds": age, "stale": age > stale_after_seconds,
        "trust": "canonical-api-receipts" if snapshot.canonical else "integrity-only; authenticity-not-established",
        "source": snapshot.canonical['source'] if snapshot.canonical else snapshot.manifest['source'],
        "offline_disclosure": "Offline snapshot age cannot establish whether an upstream fix exists or newer records are available.",
        "upstream": snapshot.canonical['upstream'] if snapshot.canonical else {
            'version': 1, 'status': 'not-refreshed', 'observations': []},
    }
