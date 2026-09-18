"""Hosted-only automatic acceptance for validated one-commit data pull requests."""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .contributions import (
    _OID,
    _REPOSITORY,
    _check_unique_device_text,
    _git,
    _git_environment,
    _validate_candidate,
)
from .records import validate_public_text


_REPOSITORY_ID = re.compile(r"[1-9][0-9]{0,19}\Z")
MAX_ATTEMPTS = 3


def _result(status, *, published=False, reason=None, attempts=0, head=None,
            accepted_commit=None, already_accepted=False, pr_merged=False):
    value = {
        "status": status,
        "published": published,
        "attempts": attempts,
        "head": head,
        "accepted_commit": accepted_commit,
        "already_accepted": already_accepted,
        "pr_merged": pr_merged,
        "pr_status": "merged" if pr_merged else "unconfirmed",
    }
    if reason is not None:
        value["reason"] = reason
    return value


class GitHubHostedAdapter:
    """Narrow GitHub API/Git boundary using only the native workflow token."""

    def __init__(self):
        if os.environ.get("GITHUB_ACTIONS") != "true":
            raise RuntimeError("Automatic acceptance is available only in the hosted workflow")
        self.configured_repository_id = os.environ.get("OMARCHY_KNOWLEDGE_REPOSITORY_ID", "")
        self._token = os.environ.get("GITHUB_TOKEN", "")
        if not _REPOSITORY_ID.fullmatch(self.configured_repository_id):
            raise RuntimeError("Configured repository identity must be numeric")
        if not self._token:
            raise RuntimeError("The native GitHub job token is required")
        self._api = os.environ.get("GITHUB_API_URL", "https://api.github.com").rstrip("/")

    def _request(self, path):
        request = Request(
            self._api + path,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": "Bearer " + self._token,
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "omarchy-community-knowledge-intake",
            },
        )
        try:
            with urlopen(request, timeout=30) as response:
                value = json.load(response)
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise RuntimeError("GitHub metadata request failed") from exc
        if not isinstance(value, dict):
            raise RuntimeError("GitHub metadata response was not an object")
        return value

    def repository_metadata(self, repository):
        return self._request("/repos/" + repository)

    def pull_metadata(self, repository, number):
        return self._request(f"/repos/{repository}/pulls/{number}")

    def _git_env(self):
        environment = _git_environment()
        credential = base64.b64encode(("x-access-token:" + self._token).encode()).decode("ascii")
        environment.update({
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "http.https://github.com/.extraheader",
            "GIT_CONFIG_VALUE_0": "AUTHORIZATION: basic " + credential,
        })
        return environment

    def _remote_git(self, objects, arguments, *, check=True):
        settings = [
            "-c", "core.hooksPath=/dev/null",
            "-c", "credential.helper=",
            "-c", "core.useReplaceRefs=false",
            "-c", "commit.gpgSign=false",
            "-c", "transfer.fsckObjects=true",
            "-c", "fetch.fsckObjects=true",
            "-c", "protocol.allow=never",
            "-c", "protocol.https.allow=always",
            "-c", "protocol.file.allow=never",
        ]
        try:
            completed = subprocess.run(
                ["git", *settings, "--git-dir", str(objects), *arguments],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=self._git_env(),
                timeout=120,
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            error = str(exc).replace(self._token, "[redacted]")
            raise RuntimeError("Hosted Git operation failed: " + error) from exc
        if check and completed.returncode:
            error = completed.stderr.decode("utf-8", "replace").replace(self._token, "[redacted]")
            raise RuntimeError("Hosted Git operation failed: " + error.strip())
        return completed

    def _origin(self, repository):
        return "https://github.com/" + repository + ".git"

    def fetch_main(self, objects, repository):
        self._remote_git(
            objects,
            ["fetch", "--no-tags", "--no-recurse-submodules", "--no-auto-maintenance",
             self._origin(repository), "refs/heads/main"],
        )
        value = self._remote_git(objects, ["rev-parse", "--verify", "FETCH_HEAD^{commit}"]).stdout.decode().strip()
        if not _OID.fullmatch(value):
            raise RuntimeError("GitHub main did not resolve to a commit")
        return value

    def fetch_refs(self, objects, repository, number):
        base = self.fetch_main(objects, repository)
        self._remote_git(
            objects,
            ["fetch", "--no-tags", "--no-recurse-submodules", "--no-auto-maintenance",
             self._origin(repository), f"refs/pull/{number}/head"],
        )
        head = self._remote_git(objects, ["rev-parse", "--verify", "FETCH_HEAD^{commit}"]).stdout.decode().strip()
        if not _OID.fullmatch(head):
            raise RuntimeError("GitHub pull request head did not resolve to a commit")
        return base, head

    def push_main(self, objects, repository, merge_commit):
        completed = self._remote_git(
            objects,
            ["push", self._origin(repository), f"{merge_commit}:refs/heads/main"],
            check=False,
        )
        return completed.returncode == 0


def _initialize_store(objects):
    completed = subprocess.run(
        ["git", "init", "--bare", str(objects)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=_git_environment(), timeout=30,
    )
    if completed.returncode:
        raise RuntimeError("Could not create isolated hosted Git object store")


def _validate_repository(adapter, repository):
    configured = getattr(adapter, "configured_repository_id", "")
    if not isinstance(configured, str) or not _REPOSITORY_ID.fullmatch(configured):
        raise ValueError("Configured repository identity must be numeric")
    metadata = adapter.repository_metadata(repository)
    if not isinstance(metadata, dict) or metadata.get("id") != int(configured):
        raise ValueError("Configured numeric repository identity does not match")
    full_name = metadata.get("full_name")
    if not isinstance(full_name, str) or full_name.lower() != repository.lower():
        raise ValueError("Configured repository name does not match")


def _validated_pull(metadata, expected_number, *, require_open=True):
    if not isinstance(metadata, dict) or metadata.get("number") != expected_number:
        raise ValueError("Pull request metadata does not match the requested number")
    if require_open and metadata.get("state") != "open":
        raise ValueError("Pull request must be open")
    if metadata.get("state") not in {"open", "closed"}:
        raise ValueError("Pull request state is invalid")
    if metadata.get("draft") is not False:
        raise ValueError("Draft pull requests are not automatically accepted")
    base = metadata.get("base")
    head = metadata.get("head")
    if not isinstance(base, dict) or base.get("ref") != "main":
        raise ValueError("Pull request must target main")
    if not isinstance(head, dict) or not isinstance(head.get("sha"), str) or not _OID.fullmatch(head["sha"]):
        raise ValueError("Pull request head must be an immutable full object ID")
    title = metadata.get("title")
    body = metadata.get("body")
    if not isinstance(title, str) or not 1 <= len(title.encode("utf-8")) <= 240:
        raise ValueError("Pull request title is invalid")
    if body is None:
        body = ""
    if not isinstance(body, str) or len(body.encode("utf-8")) > 20_000:
        raise ValueError("Pull request body is invalid")
    for value in (title, body):
        if value:
            validate_public_text(value)
            _check_unique_device_text(value)
    return head["sha"]


def _is_ancestor(repository, ancestor, descendant):
    completed = _git(repository, ["merge-base", "--is-ancestor", ancestor, descendant], check=False)
    if completed.returncode not in (0, 1):
        raise RuntimeError("Could not inspect published ancestry")
    return completed.returncode == 0


def _commit_merge(objects, tree, base, head, repository, pull_number):
    message = (
        "Accept validated data contribution\n\n"
        f"Pull request: https://github.com/{repository}/pull/{pull_number}\n"
        f"Validated head: {head}\n"
    ).encode("utf-8")
    environment = _git_environment()
    environment.update({
        "GIT_AUTHOR_NAME": "Omarchy Knowledge Intake",
        "GIT_AUTHOR_EMAIL": "41898282+github-actions[bot]@users.noreply.github.com",
        "GIT_COMMITTER_NAME": "Omarchy Knowledge Intake",
        "GIT_COMMITTER_EMAIL": "41898282+github-actions[bot]@users.noreply.github.com",
    })
    settings = [
        "-c", "core.hooksPath=/dev/null", "-c", "credential.helper=",
        "-c", "core.useReplaceRefs=false", "-c", "commit.gpgSign=false",
        "-c", "protocol.allow=never",
    ]
    completed = subprocess.run(
        ["git", *settings, "--git-dir", str(objects), "commit-tree", tree,
         "-p", base, "-p", head],
        input=message, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env=environment, timeout=30,
    )
    merge_commit = completed.stdout.decode("ascii", "strict").strip() if completed.returncode == 0 else ""
    if not _OID.fullmatch(merge_commit):
        raise RuntimeError("Could not create the validated merge commit")
    return merge_commit


def validate_pr(repository: str, pull_number: int, *, _adapter=None) -> dict:
    """Hosted read-only validation job entry point; never creates or pushes a commit."""
    adapter = _adapter or GitHubHostedAdapter()
    if not isinstance(repository, str) or not _REPOSITORY.fullmatch(repository):
        raise ValueError("Repository must be an owner/name selector")
    if isinstance(pull_number, bool) or not isinstance(pull_number, int) or pull_number < 1:
        raise ValueError("Pull request number must be a positive integer")
    try:
        _validate_repository(adapter, repository)
        metadata = adapter.pull_metadata(repository, pull_number)
        expected_head = _validated_pull(metadata, pull_number, require_open=False)
        with tempfile.TemporaryDirectory(prefix="omarchy-intake-validate-") as temporary:
            objects = Path(temporary) / "objects.git"
            _initialize_store(objects)
            base, head = adapter.fetch_refs(objects, repository, pull_number)
            if head != expected_head:
                return _result("pending", reason="Pull request head changed while fetching", head=expected_head)
            if _is_ancestor(objects, head, base):
                return _result(
                    "accepted", published=True, head=head, accepted_commit=base,
                    already_accepted=True,
                )
            if metadata["state"] != "open":
                raise ValueError("Pull request must be open")
            checked = _validate_candidate(objects, base, head)
            return {
                **_result("valid", head=head),
                "base": base,
                "tree": checked["tree"],
                "added_record_count": len(checked["added_records"]),
            }
    except ValueError as exc:
        return _result("rejected", reason=str(exc))


def accept_pr(repository: str, pull_number: int, *, _adapter=None) -> dict:
    """Validate and normally push one merge commit; unavailable outside hosted execution."""
    adapter = _adapter or GitHubHostedAdapter()
    if not isinstance(repository, str) or not _REPOSITORY.fullmatch(repository):
        raise ValueError("Repository must be an owner/name selector")
    if isinstance(pull_number, bool) or not isinstance(pull_number, int) or pull_number < 1:
        raise ValueError("Pull request number must be a positive integer")
    try:
        _validate_repository(adapter, repository)
    except ValueError as exc:
        return _result("rejected", reason=str(exc))

    with tempfile.TemporaryDirectory(prefix="omarchy-intake-write-") as temporary:
        objects = Path(temporary) / "objects.git"
        _initialize_store(objects)
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                metadata = adapter.pull_metadata(repository, pull_number)
                expected_head = _validated_pull(metadata, pull_number, require_open=False)
                base, head = adapter.fetch_refs(objects, repository, pull_number)
                if head != expected_head:
                    return _result(
                        "pending", reason="Pull request head changed while fetching",
                        attempts=attempt, head=expected_head,
                    )
                if _is_ancestor(objects, head, base):
                    try:
                        current = adapter.pull_metadata(repository, pull_number)
                        current_head = _validated_pull(current, pull_number, require_open=False)
                    except (RuntimeError, ValueError) as exc:
                        return _result(
                            "accepted", published=True,
                            reason="Accepted ancestry was verified; pull request status is unavailable: " + str(exc),
                            attempts=attempt, head=head, accepted_commit=base,
                            already_accepted=True,
                        )
                    if current_head != head:
                        return _result(
                            "accepted", published=True,
                            reason="Accepted the validated head; a later pull request head is a new candidate",
                            attempts=attempt, head=head, accepted_commit=base,
                            already_accepted=True,
                        )
                    merged = current.get("merged") is True and current_head == head
                    return _result(
                        "accepted", published=True, attempts=attempt, head=head, accepted_commit=base,
                        already_accepted=True, pr_merged=merged,
                    )
                if metadata["state"] != "open":
                    raise ValueError("Pull request must be open")
                checked = _validate_candidate(objects, base, head)
                current = adapter.pull_metadata(repository, pull_number)
                current_head = _validated_pull(current, pull_number)
                if current_head != head:
                    return _result(
                        "pending", reason="Pull request head changed after validation",
                        attempts=attempt, head=head,
                    )
                merge_commit = _commit_merge(
                    objects, checked["tree"], base, head, repository, pull_number,
                )
                try:
                    push_succeeded = adapter.push_main(objects, repository, merge_commit)
                except RuntimeError as exc:
                    return _result(
                        "unknown", published=None,
                        reason="Push outcome is unknown and must be checked: " + str(exc),
                        attempts=attempt, head=head, accepted_commit=merge_commit,
                    )
                if not push_succeeded:
                    try:
                        current_base = adapter.fetch_main(objects, repository)
                    except RuntimeError as exc:
                        return _result(
                            "unknown", published=None,
                            reason="Push failed and current main could not be verified: " + str(exc),
                            attempts=attempt, head=head, accepted_commit=merge_commit,
                        )
                    if _is_ancestor(objects, head, current_base):
                        accepted_commit = (
                            merge_commit
                            if _is_ancestor(objects, merge_commit, current_base)
                            else current_base
                        )
                        try:
                            final_metadata = adapter.pull_metadata(repository, pull_number)
                            final_head = _validated_pull(final_metadata, pull_number, require_open=False)
                            merged = final_metadata.get("merged") is True and final_head == head
                        except (RuntimeError, ValueError) as exc:
                            return _result(
                                "accepted", published=True,
                                reason="Accepted ancestry was verified; pull request status is unavailable: " + str(exc),
                                attempts=attempt, head=head, accepted_commit=accepted_commit,
                            )
                        return _result(
                            "accepted", published=True, attempts=attempt, head=head,
                            accepted_commit=accepted_commit, pr_merged=merged,
                        )
                    if current_base != base:
                        if attempt < MAX_ATTEMPTS:
                            continue
                        return _result(
                            "pending", reason="Main changed during all three validation attempts",
                            attempts=attempt, head=head,
                        )
                    return _result(
                        "pending", reason="Ordinary non-force push failed without a base race",
                        attempts=attempt, head=head,
                    )
                try:
                    published = adapter.fetch_main(objects, repository)
                except RuntimeError as exc:
                    return _result(
                        "unknown", published=None,
                        reason="Push succeeded but published main could not be verified: " + str(exc),
                        attempts=attempt, head=head, accepted_commit=merge_commit,
                    )
                if not _is_ancestor(objects, merge_commit, published) or not _is_ancestor(objects, head, published):
                    return _result(
                        "unknown", published=None, reason="Published main does not confirm the pushed commit",
                        attempts=attempt, head=head, accepted_commit=merge_commit,
                    )
                try:
                    final_metadata = adapter.pull_metadata(repository, pull_number)
                    final_head = _validated_pull(final_metadata, pull_number, require_open=False)
                except (RuntimeError, ValueError) as exc:
                    return _result(
                        "accepted", published=True,
                        reason="Accepted commit was verified; pull request status is unavailable: " + str(exc),
                        attempts=attempt, head=head, accepted_commit=merge_commit,
                    )
                merged = final_metadata.get("merged") is True and final_head == head
                result = _result(
                    "accepted", published=True, attempts=attempt, head=head,
                    accepted_commit=merge_commit, pr_merged=merged,
                )
                if final_head != head:
                    result["reason"] = "Accepted the validated head; a later pull request head is a new candidate"
                return result
            except ValueError as exc:
                return _result("rejected", reason=str(exc), attempts=attempt)
            except RuntimeError as exc:
                return _result("pending", reason=str(exc), attempts=attempt)
    raise AssertionError("unreachable")
