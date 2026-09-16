"""Bounded immutable reads from a coordinator-owned local bare object store.

The directory (including its config and alternates) must be trusted, never supplied
by a PR. No refs, worktree operations, filters, hooks, or fetches are used.
"""
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
import selectors
import subprocess
import time


OID = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")


class ObjectUnavailable(Exception):
    """Absent, mistyped, malformed, or resource-bounded object read."""


def valid_oid(value):
    return isinstance(value, str) and OID.fullmatch(value) is not None


@dataclass(frozen=True)
class TreeEntry:
    path: bytes
    mode: str
    kind: str
    oid: str
    size: int


class GitObjectReader:
    def __init__(self, git_dir):
        self.git_dir = str(Path(git_dir).absolute())
        self.begin()

    def begin(self):
        self.deadline = time.monotonic() + 20
        self.calls = 0

    def _run(self, args, maximum):
        self.calls += 1
        if self.calls > 5000 or time.monotonic() >= self.deadline:
            raise ObjectUnavailable()
        env = {"PATH": "/usr/bin:/bin", "LC_ALL": "C", "GIT_CONFIG_NOSYSTEM": "1",
               "GIT_CONFIG_SYSTEM": "/dev/null", "GIT_CONFIG_GLOBAL": "/dev/null",
               "GIT_TERMINAL_PROMPT": "0", "GIT_NO_LAZY_FETCH": "1",
               "GIT_OPTIONAL_LOCKS": "0", "GIT_CONFIG_COUNT": "0",
               "GIT_ALLOW_PROTOCOL": "", "GIT_ATTR_NOSYSTEM": "1"}
        argv = ["/usr/bin/git", "--no-replace-objects", "--git-dir=" + self.git_dir,
                "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false",
                "-c", "protocol.allow=never", *args]
        output = bytearray()
        with subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                              stdin=subprocess.DEVNULL, env=env, cwd="/") as process:
            try:
                with selectors.DefaultSelector() as selector:
                    selector.register(process.stdout, selectors.EVENT_READ)
                    while True:
                        remaining = self.deadline - time.monotonic()
                        if remaining <= 0 or not selector.select(remaining):
                            raise ObjectUnavailable()
                        chunk = os.read(process.stdout.fileno(), min(65536, maximum + 1 - len(output)))
                        if not chunk:
                            break
                        output.extend(chunk)
                        if len(output) > maximum:
                            raise ObjectUnavailable()
                if process.wait(timeout=max(.001, self.deadline - time.monotonic())):
                    raise ObjectUnavailable()
            except BaseException:
                process.kill()
                process.wait()
                raise
        return bytes(output)

    def _typed(self, oid, kind, maximum):
        if not valid_oid(oid) or self._run(["cat-file", "-t", oid], 16).strip() != kind.encode():
            raise ObjectUnavailable()
        raw = self._run(["cat-file", kind, oid], maximum)
        digest = hashlib.new("sha1" if len(oid) == 40 else "sha256")
        digest.update(kind.encode() + b" " + str(len(raw)).encode() + b"\0" + raw)
        if digest.hexdigest() != oid:
            raise ObjectUnavailable()
        return raw

    def commit(self, oid):
        raw = self._typed(oid, "commit", 65536)
        first = raw.split(b"\n", 1)[0]
        if not first.startswith(b"tree "):
            raise ObjectUnavailable()
        tree = first[5:].decode("ascii")
        if not valid_oid(tree):
            raise ObjectUnavailable()
        return tree

    def entries(self, tree_oid):
        self._typed(tree_oid, "tree", 1024 * 1024)
        raw = self._run(["ls-tree", "-r", "-t", "-l", "-z", "--full-tree", tree_oid], 1024 * 1024)
        rows = raw.split(b"\0")
        if rows[-1] or len(rows) > 4097:
            raise ObjectUnavailable()
        entries = []
        for row in rows[:-1]:
            header, path = row.split(b"\t", 1)
            mode, kind, oid, size = header.split()
            entries.append(TreeEntry(path, mode.decode("ascii"), kind.decode("ascii"),
                                     oid.decode("ascii"), -1 if size == b"-" else int(size)))
        for oid in {entry.oid for entry in entries if entry.kind == "tree"}:
            self._typed(oid, "tree", 1024 * 1024)
        return entries

    def blob(self, oid, max_bytes=65536):
        return self._typed(oid, "blob", min(max_bytes, 65536))
