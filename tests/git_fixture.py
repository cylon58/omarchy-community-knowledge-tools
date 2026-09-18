"""Small local Git repositories for Git-store integration tests."""
import json
from pathlib import Path
import subprocess


def git(*args, cwd=None, env=None, input=None):
    return subprocess.run(
        ["git", *args], cwd=cwd, env=env, input=input, check=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )


class GitFixture:
    def __init__(self, root: Path):
        self.root = root
        self.work = root / "work"
        self.bare = root / "remote.git"
        git("init", "--bare", "--initial-branch=main", str(self.bare))
        git("init", "--initial-branch=main", str(self.work))
        git("config", "user.email", "fixture@example.test", cwd=self.work)
        git("config", "user.name", "Fixture", cwd=self.work)

    @property
    def transport(self):
        return str(self.bare)

    def commit_records(self, rows_by_path):
        records = self.work / "records"
        for name, row in rows_by_path.items():
            path = records / name
            path.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(row, bytes):
                path.write_bytes(row)
            else:
                path.write_text(json.dumps(row), encoding="utf-8")
        git("add", "records", cwd=self.work)
        git("commit", "-m", "records", cwd=self.work)
        git("push", "--force", str(self.bare), "main", cwd=self.work)

    def commit_symlink(self, name, target):
        path = self.work / "records" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.symlink_to(target)
        git("add", "records", cwd=self.work)
        git("commit", "-m", "symlink", cwd=self.work)
        git("push", "--force", str(self.bare), "main", cwd=self.work)

    def replace_main_with_traversal_shaped_tree(self, row):
        """Install a deliberately malformed raw tree for transport rejection tests."""
        def write_object(kind, raw):
            return git("--git-dir", str(self.bare), "hash-object", "--literally", "-t", kind,
                       "-w", "--stdin", input=raw).stdout.decode().strip()

        def entry(mode, name, object_id):
            return (mode + " " + name).encode() + b"\0" + bytes.fromhex(object_id)

        blob = write_object("blob", json.dumps(row).encode())
        leaf = write_object("tree", entry("100644", "outside.json", blob))
        cases = write_object("tree", entry("40000", "..", leaf))
        records = write_object("tree", entry("40000", "cases", cases))
        root = write_object("tree", entry("40000", "records", records))
        commit = git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.test",
                     "--git-dir", str(self.bare), "commit-tree", root, input=b"malformed tree\n").stdout.decode().strip()
        git("--git-dir", str(self.bare), "update-ref", "refs/heads/main", commit)
