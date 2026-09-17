"""Trusted native admission: immutable plans, exact CAS, canonical-history recovery.

No CLI JSON is an authority grant. Policy is deployed trusted configuration;
plans are untrusted and independently re-created by the writer before mutation.
"""
from dataclasses import dataclass
import base64
import hashlib
import json
import re
import uuid

from .admission import (CoordinatorImportGrant, TreeCandidateV1,
                        _paths, check_tree)
from .git_objects import ObjectUnavailable, TreeEntry
from .github_native import DEPLOYMENTS, NativeUnavailable
from projections import record_digest, validate_ingestion_receipt
import knowledge

REPOSITORY = "cylon58/omarchy-community-knowledge"
REPOSITORY_ID = 1373429914
MAX_PLAN = 1024 * 1024
MAX_HISTORY = 100
IMPORT_PREFIX = "Omarchy knowledge snapshot import\n\n"
RECEIPT_MESSAGE = "Record source-bound ingestion receipt"


class NativeRejected(NativeUnavailable):
    """Deterministic content rejection; messages never contain contributor text."""


class NativeNotReady(NativeUnavailable):
    """Authenticated candidate-local readiness result with one fixed reason."""
    REASONS = frozenset({
        "draft", "closed", "old-base", "explicit-pending-merge",
        "source-repository-unavailable", "changed-merge-parents",
    })

    def __init__(self, reason):
        if reason not in self.REASONS:
            raise ValueError("invalid native readiness reason")
        self.reason = reason
        super().__init__(reason)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def oid(kind, raw):
    return hashlib.sha1(kind.encode() + b" " + str(len(raw)).encode() + b"\0" + raw).hexdigest()


def require(condition):
    if not condition:
        raise NativeUnavailable()


def object_id(value):
    require(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{40}", value) is not None)
    return value


@dataclass(frozen=True)
class Policy:
    policy_revision: str
    toolkit_revision: str
    deployment: str = "production"

    def __post_init__(self):
        object_id(self.policy_revision)
        object_id(self.toolkit_revision)
        require(self.deployment in DEPLOYMENTS)

    @property
    def repository(self):
        return DEPLOYMENTS[self.deployment][0]

    @property
    def repository_id(self):
        return DEPLOYMENTS[self.deployment][1]


@dataclass(frozen=True)
class Result:
    status: str
    accepted_commit_oid: str | None = None
    head_changed: bool = False
    source_pull_request: int | None = None
    source_head: str | None = None


@dataclass(frozen=True)
class _ReceiptCoverage:
    pull_request: int
    head: str
    manifest_additions: tuple[tuple[str, str], ...]
    authenticated_additions: frozenset[tuple[str, str]]

    @property
    def complete(self):
        return self.authenticated_additions == frozenset(self.manifest_additions)


class Overlay:
    """In-memory Git objects derived only from verified base entries/exact bytes."""
    def __init__(self, objects):
        self.objects = objects
        self.trees = {}
        self.blobs = {}
        self.commits = {}

    def begin(self):
        self.objects.begin()

    def commit(self, commit):
        return self.commits[commit] if commit in self.commits else self.objects.commit(commit)

    def entries(self, tree):
        return self.trees[tree] if tree in self.trees else self.objects.entries(tree)

    def blob(self, blob, max_bytes=65536):
        if blob not in self.blobs:
            return self.objects.blob(blob, max_bytes)
        require(len(self.blobs[blob]) <= max_bytes)
        return self.blobs[blob]

    def add(self, base, additions):
        base_entries = _paths(self.entries(self.commit(base)))
        leaves = {p: e for p, e in base_entries.items() if e.kind != "tree"}
        for path, raw in additions.items():
            require(path not in base_entries and isinstance(raw, bytes) and len(raw) <= 65536)
            blob = oid("blob", raw)
            self.blobs[blob] = raw
            leaves[path] = TreeEntry(path.encode(), "100644", "blob", blob, len(raw))
        nested = {}
        for path, entry in leaves.items():
            node = nested
            bits = path.split("/")
            for part in bits[:-1]:
                require(not isinstance(node.get(part), TreeEntry))
                node = node.setdefault(part, {})
            require(bits[-1] not in node)
            node[bits[-1]] = entry

        def build(node, prefix=""):
            rows, entries = [], []
            for name, value in node.items():
                path = prefix + name
                if isinstance(value, dict):
                    child_oid, children = build(value, path + "/")
                    value = TreeEntry(path.encode(), "040000", "tree", child_oid, -1)
                    entries.extend(children)
                entries.append(value)
                key = name.encode() + (b"/" if value.kind == "tree" else b"")
                rows.append((key, value.mode.lstrip("0").encode() + b" " + name.encode()
                             + b"\0" + bytes.fromhex(value.oid)))
            tree = oid("tree", b"".join(row for _, row in sorted(rows)))
            self.trees[tree] = entries
            return tree, entries

        tree, _ = build(nested)
        raw = (f"tree {tree}\nparent {base}\nauthor Coordinator <coordinator@example.org> 0 +0000\n"
               "committer Coordinator <coordinator@example.org> 0 +0000\n\nEvaluation\n").encode()
        commit = oid("commit", raw)
        self.commits[commit] = tree
        return commit, tree


def _identity(api, policy):
    repo = api.repository()
    require(type(repo.get("id")) is int and repo["id"] == policy.repository_id
            and repo.get("full_name") == policy.repository and repo.get("default_branch") == "main")


def _pull(api, policy, number, base):
    require(type(number) is int and 1 <= number <= 2_147_483_647)
    pr = api.pull(number)
    require(isinstance(pr, dict)
            and type(pr.get("number")) is int and pr["number"] == number
            and pr.get("state") in {"open", "closed"}
            and type(pr.get("merged")) is bool
            and type(pr.get("draft")) is bool)
    target = pr.get("base")
    require(isinstance(target, dict) and isinstance(target.get("repo"), dict)
            and type(target["repo"].get("id")) is int
            and target["repo"]["id"] == policy.repository_id
            and target["repo"].get("full_name") == policy.repository
            and target.get("ref") == "main")
    candidate_base = object_id(target.get("sha"))
    head = pr.get("head")
    require(isinstance(head, dict) and "repo" in head)
    candidate_head = object_id(head.get("sha"))
    head_repository = head["repo"]
    require(head_repository is None or isinstance(head_repository, dict))
    if head_repository is not None:
        require(type(head_repository.get("id")) is int
                and 0 < head_repository["id"] <= 2**63 - 1)
    user = pr.get("user")
    require(isinstance(user, dict))
    require(type(user.get("id")) is int and 0 < user["id"] <= 2**63 - 1
            and user.get("type") in {"User", "Bot"})
    require("merge_commit_sha" in pr and "mergeable" in pr)
    evaluated = pr["merge_commit_sha"]
    mergeable = pr["mergeable"]
    require(evaluated is None or object_id(evaluated) == evaluated)
    require(mergeable is None or type(mergeable) is bool)

    if pr["state"] == "closed":
        raise NativeNotReady("closed")
    require(pr["merged"] is False)
    if evaluated is None:
        require(mergeable is None or mergeable is False)
    else:
        require(mergeable is True)
    if pr["draft"]:
        raise NativeNotReady("draft")
    if candidate_base != base:
        raise NativeNotReady("old-base")
    if head_repository is None:
        raise NativeNotReady("source-repository-unavailable")
    if evaluated is None:
        raise NativeNotReady("explicit-pending-merge")
    return pr


def _check(objects, policy, base, head, evaluated, tree, profile="community", grant=None):
    candidate = TreeCandidateV1(policy.repository_id, "pull-head", base, head, evaluated, tree,
                                policy.policy_revision, profile)
    report = check_tree(candidate, objects, trusted_import_grant=grant)
    if report['decision'] == 'reject':
        raise NativeRejected()
    require(report["decision"] == "accept")
    return report


def prepare(api, policy, pull_request):
    """Read-only, bounded plan. Raises NativeUnavailable on any uncertain state."""
    try:
        _identity(api, policy)
        base = object_id(api.branch())
        pr = _pull(api, policy, pull_request, base)
        head = pr["head"]["sha"]
        evaluated = object_id(pr["merge_commit_sha"])
        merge = api.commit_info(evaluated)
        require(isinstance(merge, dict) and object_id(merge.get("oid")) == evaluated
                and type(merge.get("parents")) is list
                and len(merge["parents"]) == 2)
        parents = [object_id(parent) for parent in merge["parents"]]
        if parents != [base, head]:
            raise NativeNotReady("changed-merge-parents")
        tree = object_id(merge["tree"])
        if hasattr(api.objects, "warm"):
            api.objects.warm([base, head, evaluated])
        report = _check(api.objects, policy, base, head, evaluated, tree)
        old = _paths(api.objects.entries(api.objects.commit(base)))
        current = _paths(api.objects.entries(tree))
        additions = {p: api.objects.blob(e.oid) for p, e in current.items()
                     if p not in old and e.kind != "tree"}
        source = _paths(api.objects.entries(api.objects.commit(head)))
        require(all(p in source and source[p] == current[p] for p in additions))
        # This is the pre-write proof for GraphQL's file addition semantics.
        overlay = Overlay(api.objects)
        _, computed = overlay.add(base, additions)
        require(computed == tree)
        plan = {"version": 1, "repository_id": policy.repository_id, "pull_request": pull_request,
                "actor_account_id": pr["user"]["id"], "head_repository_id": pr["head"]["repo"]["id"],
                "base": base, "head": head, "evaluated": evaluated, "tree": tree,
                "policy_revision": policy.policy_revision, "toolkit_revision": policy.toolkit_revision,
                "corpus_digest": report["corpus_digest"],
                "additions": [{"path": p, "blob": oid("blob", raw),
                               "content": base64.b64encode(raw).decode()} for p, raw in sorted(additions.items())]}
        require(len(canonical(plan)) <= MAX_PLAN)
        return plan
    except (KeyError, TypeError, ValueError, ObjectUnavailable, RecursionError) as exc:
        raise NativeUnavailable() from exc


def load_plan(raw):
    """Strict bounded serialization. This parses data; it grants no authority."""
    from .github_native import strict_json
    require(isinstance(raw, bytes) and len(raw) <= MAX_PLAN)
    plan = strict_json(raw)
    require(isinstance(plan, dict) and set(plan) == {
        "version", "repository_id", "pull_request", "actor_account_id", "head_repository_id",
        "base", "head", "evaluated", "tree", "policy_revision", "toolkit_revision", "corpus_digest", "additions"})
    require(type(plan["version"]) is int and plan["version"] == 1
            and type(plan["repository_id"]) is int and plan["repository_id"] in {v[1] for v in DEPLOYMENTS.values()})
    for field in ("base", "head", "evaluated", "tree", "policy_revision", "toolkit_revision"):
        object_id(plan[field])
    for field in ("pull_request", "actor_account_id", "head_repository_id"):
        require(type(plan[field]) is int and 0 < plan[field] <= 2**63 - 1)
    require(isinstance(plan["additions"], list) and 1 <= len(plan["additions"]) <= 10)
    paths = []
    from .admission import PATH
    for item in plan["additions"]:
        require(isinstance(item, dict) and set(item) == {"path", "blob", "content"})
        path = item["path"]
        require(isinstance(path, str) and PATH.fullmatch(path) is not None and path.startswith("records/"))
        require(isinstance(item["content"], str) and len(item["content"]) <= 87384)
        raw = base64.b64decode(item["content"], validate=True)
        require(len(raw) <= 65536 and oid("blob", raw) == item["blob"])
        paths.append(path)
    require(paths == sorted(set(paths)))
    return plan


def _manifest(plan):
    return {key: ([{"path": i["path"], "blob": i["blob"]} for i in value]
                  if key == "additions" else value) for key, value in plan.items()}


def _message(plan):
    return IMPORT_PREFIX + canonical(_manifest(plan)).decode()


def _history(api):
    if hasattr(api.objects, "retrieval"):
        api.objects.retrieval()
    current = object_id(api.branch())
    for _ in range(MAX_HISTORY):
        info = api.commit_info(current)
        require(info["oid"] == current)
        yield info
        if not info["parents"]:
            return
        require(len(info["parents"]) == 1)
        current = object_id(info["parents"][0])
    # A bounded recent window is deliberate; older gaps require explicit recovery.


def _accepted(api, plan):
    message = _message(plan)
    for info in _history(api):
        if info["message"] == message:
            require(info["parents"] == [plan["base"]] and info["tree"] == plan["tree"])
            return info
    return None


def _receipt(plan, info, addition, record):
    typed = lambda value: {"algorithm": "sha1", "hex": value}
    return {"receipt_version": 2, "kind": "ingestion", "record_id": record["id"],
            "record_sha256": record_digest(record),
            "actor": {"provider": "github", "account_id": str(plan["actor_account_id"])},
            "accepted_at": info["date"], "policy_revision": plan["policy_revision"],
            "source": {"repository_id": str(plan["repository_id"]), "pull_request": plan["pull_request"],
                       "accepted_commit_oid": typed(info["oid"]), "head_commit_oid": typed(plan["head"]),
                       "head_repository_id": str(plan["head_repository_id"]),
                       "record_blob_oid": typed(addition["blob"]), "method": "coordinator-import",
                       "toolkit_revision": plan["toolkit_revision"]}}


def _receipt_path(repository_id, accepted_commit, record_id):
    seed = f"{repository_id}:{accepted_commit}:{record_id}".encode()
    identifier = str(uuid.UUID(bytes=hashlib.sha256(seed).digest()[:16], version=4))
    return "provenance/ingestion/" + identifier + ".json"


def _import_plan(api, policy, info):
    """Reconstruct bounded immutable metadata from a canonical import commit."""
    from .github_native import strict_json
    require(info["message"].startswith(IMPORT_PREFIX))
    manifest = strict_json(info["message"][len(IMPORT_PREFIX):].encode())
    require(manifest["repository_id"] == policy.repository_id
            and isinstance(manifest["additions"], list) and 1 <= len(manifest["additions"]) <= 10)
    require(info["parents"] == [manifest["base"]] and info["tree"] == manifest["tree"])
    plan = dict(manifest)
    plan["additions"] = [{**item, "content": base64.b64encode(api.objects.blob(item["blob"])).decode()}
                         for item in manifest["additions"]]
    plan = load_plan(canonical(plan))
    require(_message(plan) == info["message"])
    return plan


def _receipted_snapshots(api, policy, *, base=None, receipt_sink=None, coverage_sink=None):
    """Canonical receipts retain completed imports beyond the recovery window.

    Validate receipt bytes against immutable import metadata and the exact current
    and accepted record. No unbounded history walk or mutable PR lookup is needed.
    """
    from .admission import PATH
    from .github_native import strict_json
    base = object_id(base if base is not None else api.branch())
    if hasattr(api.objects, "warm"):
        api.objects.warm([base])
    entries = _paths(api.objects.entries(api.objects.commit(base)))
    imported, total = set(), 0
    grouped_v1, grouped_v2, authenticated = {}, {}, []
    record_paths = {}
    for path, entry in entries.items():
        if entry.kind != "tree" and path.startswith("records/"):
            record_paths.setdefault(path.rsplit("/", 1)[1], []).append(path)
    for path, entry in entries.items():
        if entry.kind == "tree" or not path.startswith("provenance/ingestion/"):
            continue
        require(PATH.fullmatch(path) is not None and entry.kind == "blob" and entry.mode == "100644"
                and 0 <= entry.size <= 65536)
        raw = api.objects.blob(entry.oid)
        total += len(raw)
        require(len(raw) == entry.size and total <= 16 * 1024 * 1024)
        receipt = strict_json(raw)
        validate_ingestion_receipt(receipt)
        require(receipt["source"]["repository_id"] == str(policy.repository_id))
        if receipt["receipt_version"] != 2:
            if receipt_sink is not None:
                source = receipt['source']
                require(source['merge_commit_oid']['algorithm'] == 'sha1'
                        and source['record_blob_oid']['algorithm'] == 'sha1')
                accepted = object_id(source['merge_commit_oid']['hex'])
                grouped_v1.setdefault(accepted, []).append((path, raw, receipt))
            continue  # V1 does not assert a source head, so cannot deduplicate it.
        accepted = object_id(receipt["source"]["accepted_commit_oid"]["hex"])
        grouped_v2.setdefault(accepted, []).append((path, raw, receipt))

    for accepted, rows in grouped_v1.items():
        historical = _paths(api.objects.entries(api.objects.commit(accepted)))
        for _, _, receipt in rows:
            source = receipt["source"]
            matches = record_paths.get(receipt["record_id"] + ".json", [])
            require(len(matches) == 1)
            target = matches[0]
            require(target in historical and entries[target] == historical[target]
                    and entries[target].kind == "blob" and entries[target].mode == "100644"
                    and entries[target].oid == source["record_blob_oid"]["hex"])
            record = knowledge.parse_record(api.objects.blob(entries[target].oid))
            require(target == f"records/{record['type']}s/{record['id']}.json"
                    and record_digest(record) == receipt["record_sha256"])
            authenticated.append(receipt)

    for accepted, rows in grouped_v2.items():
        info = api.commit_info(accepted)
        require(info["oid"] == accepted)
        plan = _manifest(_import_plan(api, policy, info))
        historical = _paths(api.objects.entries(api.objects.commit(accepted)))
        by_name = {}
        for item in plan["additions"]:
            by_name.setdefault(item["path"].rsplit("/", 1)[1], []).append(item)
        covered = set()
        for path, raw, receipt in rows:
            matches = by_name.get(receipt["record_id"] + ".json", [])
            require(len(matches) == 1)
            item = matches[0]
            target = item["path"]
            require(target in entries and target in historical and entries[target] == historical[target]
                    and entries[target].kind == "blob" and entries[target].mode == "100644"
                    and entries[target].oid == item["blob"])
            record = knowledge.parse_record(api.objects.blob(item["blob"]))
            require(record["id"] == receipt["record_id"]
                    and target == f"records/{record['type']}s/{record['id']}.json")
            require(raw == canonical(_receipt(plan, info, item, record)) + b"\n"
                    and path == _receipt_path(policy.repository_id, accepted, record["id"]))
            covered.add((target, item["blob"]))
            authenticated.append(receipt)
        coverage = _ReceiptCoverage(
            plan["pull_request"], plan["head"],
            tuple((item["path"], item["blob"]) for item in plan["additions"]),
            frozenset(covered))
        if coverage_sink is not None:
            coverage_sink[accepted] = coverage
        if coverage.complete:
            imported.add((coverage.pull_request, coverage.head))
    if receipt_sink is not None:
        receipt_sink.extend(authenticated)
    return imported


def authenticated_receipts(api, policy, revision):
    """Authenticate canonical receipt/path/exact historical record bindings at revision.

    Caller authenticates the repository/ref first. V1 relies on trusted canonical
    history; V2 additionally reproduces coordinator import metadata and bytes.
    """
    receipts = []
    _receipted_snapshots(api, policy, base=object_id(revision), receipt_sink=receipts)
    return receipts


def _repair(api, policy, plan, info):
    # Revalidate the admitted complete corpus against the immutable predecessor.
    # Canonical import metadata, not today's mutable PR, supplies original identity.
    if hasattr(api.objects, "warm"):
        api.objects.warm([plan["base"], info["oid"], api.branch()])
    report = _check(api.objects, policy, plan["base"], info["oid"], info["oid"], plan["tree"])
    require(report["corpus_digest"] == plan["corpus_digest"])
    accepted = _paths(api.objects.entries(plan["tree"]))
    predecessor = _paths(api.objects.entries(api.objects.commit(plan["base"])))
    require(sorted((p, e.oid) for p, e in accepted.items() if p not in predecessor and e.kind != "tree")
            == [(i["path"], i["blob"]) for i in plan["additions"]])
    base = object_id(api.branch())
    existing = _paths(api.objects.entries(api.objects.commit(base)))
    missing = {}
    for item in plan["additions"]:
        path = item["path"]
        require(path in accepted and accepted[path].oid == item["blob"])
        raw = api.objects.blob(item["blob"])
        record = knowledge.parse_record(raw)
        receipt = _receipt(plan, info, item, record)
        validate_ingestion_receipt(receipt)
        destination = _receipt_path(plan["repository_id"], info["oid"], record["id"])
        content = canonical(receipt) + b"\n"
        if destination in existing:
            require(existing[destination].mode == "100644" and existing[destination].oid == oid("blob", content))
            continue
        missing[destination] = content
    if not missing:
        return
    overlay = Overlay(api.objects)
    evaluated, tree = overlay.add(base, missing)
    grant = CoordinatorImportGrant(
        policy.repository_id, info["oid"], policy.policy_revision,
        tuple(sorted((path, oid("blob", content)) for path, content in missing.items())))
    _check(overlay, policy, base, evaluated, evaluated, tree, "ingestion-receipt", grant)
    try:
        published = api.create_commit(base, missing, RECEIPT_MESSAGE)
    except NativeUnavailable:
        # The mutation may have succeeded. Inspect every exact path, never retry.
        current = _paths(api.objects.entries(api.objects.commit(api.branch())))
        require(all(path in current and current[path].oid == oid("blob", content)
                    and current[path].mode == "100644"
                    for path, content in missing.items()))
    else:
        audit = api.commit_info(published)
        require(audit["parents"] == [base] and audit["tree"] == tree)


def publish(api, policy, plan):
    """At most one import CAS; ambiguous outcomes are inspected before any retry.

    PRs are deliberately left open. Even a post-write head check cannot make a
    separate close mutation atomic with the contributor's next push.
    """
    accepted = None
    try:
        plan = load_plan(canonical(plan))
        require(plan["policy_revision"] == policy.policy_revision
                and plan["toolkit_revision"] == policy.toolkit_revision and plan["repository_id"] == policy.repository_id)
        _identity(api, policy)
        accepted = _accepted(api, plan)
        if accepted is None:
            require(api.branch() == plan["base"])
            fresh = prepare(api, policy, plan["pull_request"])
            require(fresh == plan)
            additions = {i["path"]: base64.b64decode(i["content"], validate=True) for i in plan["additions"]}
            try:
                published = api.create_commit(plan["base"], additions, _message(plan))
            except NativeUnavailable:
                accepted = _accepted(api, plan)
                require(accepted is not None)
            else:
                accepted = api.commit_info(published)
                require(accepted["parents"] == [plan["base"]] and accepted["tree"] == plan["tree"]
                        and accepted["message"] == _message(plan))
        _repair(api, policy, plan, accepted)
        changed = api.pull(plan["pull_request"])["head"]["sha"] != plan["head"]
        return Result("accepted", accepted["oid"], changed, plan["pull_request"], plan["head"])
    except (NativeUnavailable, ObjectUnavailable, ValueError, TypeError, KeyError, RecursionError):
        return Result("receipt-pending" if accepted is not None else "retry",
                      accepted["oid"] if accepted is not None else None,
                      source_pull_request=plan.get("pull_request") if accepted is not None else None,
                      source_head=plan.get("head") if accepted is not None else None)


def reconcile(api, policy):
    """Repair the latest 100 canonical commits without consulting mutable PR identity."""
    try:
        _identity(api, policy)
        coverage = {}
        _receipted_snapshots(api, policy, coverage_sink=coverage)
        imports = [i for i in _history(api) if i["message"].startswith(IMPORT_PREFIX)]
        for info in reversed(imports):
            if info["oid"] in coverage and coverage[info["oid"]].complete:
                continue
            plan = _import_plan(api, policy, info)
            _repair(api, policy, plan, info)
        return Result("complete")
    except (NativeUnavailable, ObjectUnavailable, ValueError, TypeError, KeyError, RecursionError):
        return Result("retry")


def authenticated_imported_heads(api, policy):
    """Return exact authenticated (PR, head) suppression facts only."""
    from .github_native import strict_json
    _identity(api, policy)
    imported = _receipted_snapshots(api, policy)
    for info in _history(api):
        if info["message"].startswith(IMPORT_PREFIX):
            manifest = strict_json(
                info["message"][len(IMPORT_PREFIX):].encode())
            imported.add((manifest["pull_request"], manifest["head"]))
    return frozenset(imported)


def pending(api, policy, *, limit=20):
    """Select up to limit (default 20, maximum 200) unimported heads in the 200-PR window.

    The separate PR event is the wakeup for snapshots beyond this bounded window.
    Exposes scan truncation so the scheduler cannot silently claim completeness.
    """
    require(type(limit) is int and 1 <= limit <= 200)
    imported = authenticated_imported_heads(api, policy)
    numbers, scanned = [], 0
    for page in range(1, 11):
        items = api.pending(page)
        require(isinstance(items, list) and len(items) <= 20)
        for item in items:
            require(type(item["number"]) is int and 0 < item["number"] <= 2_147_483_647)
            object_id(item["head"])
            scanned += 1
            if (item["number"], item["head"]) not in imported:
                numbers.append(item["number"])
                if len(numbers) == limit:
                    return {"pull_requests": numbers, "scan_truncated": True, "scanned": scanned}
        if len(items) < 20:
            return {"pull_requests": numbers, "scan_truncated": False, "scanned": scanned}
    return {"pull_requests": numbers, "scan_truncated": True, "scanned": scanned}


def main(argv=None):
    """Trusted workflow entrypoint. JSON plans are never policy or authority."""
    import argparse
    from dataclasses import asdict
    import os
    import stat
    import sys
    from .github_native import GitHubRead, GitHubWriter
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "publish", "reconcile", "pending"))
    parser.add_argument("--policy-revision", required=True)
    parser.add_argument("--toolkit-revision", required=True)
    parser.add_argument("--deployment", choices=sorted(DEPLOYMENTS), default="production")
    parser.add_argument("--pull-request", type=int)
    parser.add_argument("--plan")
    args = parser.parse_args(argv)
    try:
        policy = Policy(args.policy_revision, args.toolkit_revision, args.deployment)
        token = os.environ.pop("GITHUB_TOKEN", None)
        if args.command in {"publish", "reconcile"}:
            require(token is not None)
            api = GitHubWriter(token, deployment=args.deployment)
        else:
            api = GitHubRead(deployment=args.deployment, read_token=token)
        if args.command == "prepare":
            output = prepare(api, policy, args.pull_request)
        elif args.command == "pending":
            output = pending(api, policy)
        elif args.command == "reconcile":
            output = asdict(reconcile(api, policy))
        else:
            require(args.plan is not None)
            fd = os.open(args.plan, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd, "rb") as stream:
                metadata = os.fstat(stream.fileno())
                require(stat.S_ISREG(metadata.st_mode) and metadata.st_size <= MAX_PLAN)
                plan = load_plan(stream.read(MAX_PLAN + 1))
            output = asdict(publish(api, policy, plan))
        print(json.dumps(output, sort_keys=True))
        return 2 if output.get("status") in {"retry", "receipt-pending"} else 0
    except (NativeUnavailable, ObjectUnavailable, ValueError, TypeError, KeyError, OSError, RecursionError):
        print('{"status":"retry"}')
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
