"""Read a public GitHub record tree without checking it out or inheriting Git state."""
import fcntl
import json
import os
from pathlib import Path
import re
import select
import subprocess
import tempfile
import time
from datetime import datetime, timezone

from .records import MAX_RECORD_CONTENT_BYTES, MAX_RECORD_BYTES, MAX_RECORDS, parse_record, validate_corpus

DEFAULT_CACHE = Path.home() / ".cache" / "omarchy-knowledge-next"
DEFAULT_REPOSITORY = "cylon58/omarchy-community-knowledge"
_REPOSITORY = re.compile(r"[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}\Z")
_RECORD_PATH = re.compile(
    r"records/(cases|changes|reports|events)/"
    r"([0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12})\.json\Z"
)
_OID = re.compile(r"[0-9a-f]{40}\Z")


def _safe_environment():
    """Do not pass ambient Git configuration, credentials, or hooks to Git."""
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


def _run(objects, arguments, deadline, *, input=None, test_transport=False):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise RuntimeError("Git operation exceeded 120 second deadline")
    settings = [
        "-c", "core.hooksPath=/dev/null", "-c", "credential.helper=", "-c", "core.useReplaceRefs=false",
        "-c", "transfer.fsckObjects=true", "-c", "fetch.fsckObjects=true", "-c", "protocol.allow=never",
        "-c", "protocol.https.allow=always", "-c", "protocol.file.allow=" + ("always" if test_transport else "never"),
    ]
    try:
        return subprocess.run(
            ["git", *settings, "--git-dir", str(objects), *arguments], input=input,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=_safe_environment(),
            timeout=remaining, check=True,
        ).stdout
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("Git operation exceeded 120 second deadline") from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError("Git operation failed: " + exc.stderr.decode("utf-8", "replace").strip()) from exc


def _check_deadline(deadline):
    if time.monotonic() >= deadline:
        raise RuntimeError("Git operation exceeded 120 second deadline")


def _stream_tree(objects, commit, deadline):
    """Read bounded NUL-delimited tree entries without materializing the tree output."""
    settings = ["-c", "core.hooksPath=/dev/null", "-c", "credential.helper=", "-c", "core.useReplaceRefs=false",
                "-c", "transfer.fsckObjects=true", "-c", "fetch.fsckObjects=true", "-c", "protocol.allow=never",
                "-c", "protocol.https.allow=always", "-c", "protocol.file.allow=never"]
    process = subprocess.Popen(["git", *settings, "--git-dir", str(objects), "ls-tree", "-r", "-z", commit,
                                "--", "records/"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                               env=_safe_environment(), bufsize=0)
    entries, partial = [], b""
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RuntimeError("Git operation exceeded 120 second deadline")
            readable, _, _ = select.select([process.stdout], [], [], remaining)
            if not readable:
                raise RuntimeError("Git operation exceeded 120 second deadline")
            chunk = os.read(process.stdout.fileno(), 8192)
            if not chunk:
                break
            partial += chunk
            complete = partial.split(b"\0")
            partial = complete.pop()
            if len(partial) > 512:
                raise ValueError("Git tree entry exceeds bound")
            for raw in complete:
                try:
                    header, name = raw.split(b"\t", 1)
                    mode, kind, oid = header.decode("ascii").split(" ")
                    path = name.decode("utf-8")
                except (UnicodeDecodeError, ValueError) as exc:
                    raise ValueError("Git tree entry is malformed") from exc
                match = _RECORD_PATH.fullmatch(path)
                if not match or mode != "100644" or kind != "blob" or not _OID.fullmatch(oid):
                    raise ValueError("Git tree contains a non-canonical record")
                entries.append((oid, path, match))
                if len(entries) > MAX_RECORDS:
                    raise ValueError("Record count exceeds bound")
        if partial:
            raise ValueError("Git tree entry is malformed")
        _check_deadline(deadline)
        if process.wait(timeout=max(0, deadline - time.monotonic())):
            raise RuntimeError("Git operation failed while listing records")
        return entries
    except Exception:
        if process.poll() is None:
            process.kill()
        process.wait()
        raise
    finally:
        process.stdout.close()


def _initialize_object_store(objects, deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise RuntimeError("Git operation exceeded 120 second deadline")
    try:
        subprocess.run(["git", "init", "--bare", str(objects)], stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE, env=_safe_environment(), timeout=remaining, check=True)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("Git operation exceeded 120 second deadline") from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError("Could not create Git object store") from exc


def _cache_file(cache):
    return Path(cache) / "accepted.json"


def load_cache(cache: Path) -> dict:
    """Return the last fully accepted snapshot; no refresh is attempted."""
    try:
        loaded = json.loads(_cache_file(cache).read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ValueError("No accepted cache snapshot") from None
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("Accepted cache is unreadable") from exc
    required = {"repository", "revision", "synced_at", "records"}
    if set(loaded) != required or not _REPOSITORY.fullmatch(loaded["repository"]) \
            or not _OID.fullmatch(loaded["revision"]) or not isinstance(loaded["synced_at"], str) \
            or not isinstance(loaded["records"], list):
        raise ValueError("Accepted cache has invalid metadata")
    loaded["records"] = validate_corpus(loaded["records"])
    return loaded


def status(cache: Path) -> dict:
    """Describe cache age separately from the most recent refresh failure."""
    try:
        accepted = load_cache(cache)
        result = {"revision": accepted["revision"], "synced_at": accepted["synced_at"],
                  "count": len(accepted["records"])}
    except ValueError:
        result = {"revision": None, "synced_at": None, "count": 0}
    try:
        error = json.loads((Path(cache) / "refresh-error.json").read_text(encoding="utf-8"))
        result["refresh_error"] = error["message"] if isinstance(error.get("message"), str) else "Refresh failed"
    except (OSError, ValueError, json.JSONDecodeError):
        result["refresh_error"] = None
    return result


def _write_json_atomically(path: Path, value: dict):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as temporary:
        json.dump(value, temporary, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        temporary.write("\n")
        temporary.flush()
        os.fsync(temporary.fileno())
        name = temporary.name
    os.chmod(name, 0o600)
    os.replace(name, path)


def _read_tree(objects, commit, deadline):
    blobs = _stream_tree(objects, commit, deadline)
    metadata = _run(objects, ["cat-file", "--batch-check"], deadline,
                    input=("".join(oid + "\n" for oid, _, _ in blobs)).encode())
    if len(metadata) > len(blobs) * 128:
        raise ValueError("Git blob metadata exceeds bound")
    sizes, total = {}, 0
    lines = metadata.splitlines()
    if len(lines) != len(blobs):
        raise ValueError("Git blob metadata is truncated")
    for (oid, _, _), line in zip(blobs, lines, strict=True):
        try:
            actual, kind, size = line.decode("ascii").split(" ")
            size = int(size)
        except (UnicodeDecodeError, ValueError) as exc:
            raise ValueError("Git blob metadata is malformed") from exc
        if actual != oid or kind != "blob" or size < 0 or size > MAX_RECORD_BYTES or total + size > MAX_RECORD_CONTENT_BYTES:
            raise ValueError("Git blob exceeds accepted bounds")
        sizes[oid] = size
        total += size
    rows = []
    for oid, path, match in blobs:
        _check_deadline(deadline)
        data = _run(objects, ["cat-file", "--batch"], deadline, input=(oid + "\n").encode())
        size = sizes[oid]
        header = (oid + " blob " + str(size) + "\n").encode()
        if len(data) != len(header) + size + 1 or not data.startswith(header) or not data.endswith(b"\n"):
            raise ValueError("Git blob response is malformed")
        record = parse_record(data[len(header):-1])
        if record["id"] != match.group(2) or record["type"] != {"cases": "case", "changes": "change", "reports": "report", "events": "event"}[match.group(1)]:
            raise ValueError("Git record does not match its canonical path")
        rows.append(record)
    _check_deadline(deadline)
    return rows


def sync(cache: Path, repository: str, *, _transport=None) -> dict:
    """Fetch and accept a complete validated GitHub main tree atomically.

    ``_transport`` is deliberately private and exists only for local Git fixtures.
    Public callers can select only a GitHub owner/name repository.
    """
    deadline = time.monotonic() + 120
    if not isinstance(repository, str) or not _REPOSITORY.fullmatch(repository):
        raise ValueError("Repository must be a GitHub owner/name selector")
    cache = Path(cache)
    cache.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (cache / ".lock").open("a+") as lock:
        # Until ownership is acquired, do not write refresh errors: an active
        # writer owns the cache state and an unlocked error write would race it.
        acquired = False
        while not acquired:
            _check_deadline(deadline)
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
            except BlockingIOError:
                time.sleep(min(0.05, max(0, deadline - time.monotonic())))
        try:
            with tempfile.TemporaryDirectory(prefix=".fetch-", dir=cache) as temporary:
                objects = Path(temporary) / "objects.git"
                _initialize_object_store(objects, deadline)
                origin = _transport if _transport is not None else "https://github.com/" + repository + ".git"
                _run(objects, ["fetch", "--depth=1", "--no-tags", "--no-recurse-submodules", "--no-auto-maintenance",
                               origin, "refs/heads/main"], deadline, test_transport=_transport is not None)
                commit = _run(objects, ["rev-parse", "--verify", "FETCH_HEAD^{commit}"], deadline).decode().strip()
                if not _OID.fullmatch(commit):
                    raise ValueError("Git returned an invalid revision")
                _check_deadline(deadline)
                records = validate_corpus(_read_tree(objects, commit, deadline))
            _check_deadline(deadline)
            accepted = {"repository": repository, "revision": commit,
                        "synced_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
                        "records": records}
            _write_json_atomically(_cache_file(cache), accepted)
            (cache / "refresh-error.json").unlink(missing_ok=True)
            return accepted
        except Exception as exc:
            _write_json_atomically(cache / "refresh-error.json", {"message": str(exc)})
            raise
        finally:
            if acquired:
                fcntl.flock(lock, fcntl.LOCK_UN)
