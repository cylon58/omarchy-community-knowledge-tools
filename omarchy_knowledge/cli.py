"""Command-line dispatch for local validation, discovery, and contribution drafts."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import stat
import sys
from typing import Any

MAX_JSON_BYTES = 64 * 1024


def _pairs(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("Duplicate JSON key")
        value[key] = item
    return value


def _json_file(path: str | Path, maximum: int = MAX_JSON_BYTES) -> Any:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError("Only regular JSON files are supported")
        raw = stream.read(maximum + 1)
    if len(raw) > maximum:
        raise ValueError("JSON input exceeds size bound")
    return json.loads(raw, object_pairs_hook=_pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Non-finite JSON")))


def _records(paths):
    import knowledge
    return [knowledge.parse_record(json.dumps(_json_file(path), ensure_ascii=False, allow_nan=False))
            for path in paths]


def _print(value):
    print(json.dumps(value, sort_keys=True, indent=2, ensure_ascii=True))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="omarchy-knowledge",
        description="Local inert knowledge search and draft workflow; never applies or publishes changes.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser('routes', help='print owned canonical routes; external projects remain unselected')

    sync = commands.add_parser('sync', help='verify canonical GitHub main objects and atomically cache receipts')
    sync.add_argument('--cache', required=True)
    sync.add_argument('--config', help='rendered deployment.json with reviewed immutable toolkit/policy pins')
    sync.add_argument('--deployment', choices=('production', 'pilot'))
    sync.add_argument('--toolkit-revision')
    sync.add_argument('--policy-revision')

    validate = commands.add_parser("validate", help="validate record files as one corpus")
    validate.add_argument("files", nargs="+")

    admission = commands.add_parser("admission-check", help="offline exact-tree content check; never posts or merges")
    admission.add_argument("--git-dir", required=True)
    admission.add_argument("--request", required=True)
    admission.add_argument("--output")

    index = commands.add_parser("index", help="build deterministic snapshot or legacy case index")
    index.add_argument("files", nargs="*")
    index.add_argument("--source"); index.add_argument("--output")
    index.add_argument("--cache")
    index.add_argument("--data-revision"); index.add_argument("--toolkit-revision")
    index.add_argument("--created-at"); index.add_argument("--source-updated-at")
    index.add_argument("--source-reference", default="local-directory")

    search = commands.add_parser("search", help="search a cache with explicit intent and environment")
    search.add_argument("files", nargs="*")
    search.add_argument("--cache"); search.add_argument("--query", default="")
    search.add_argument("--environment")
    search.add_argument("--intent", choices=("corrective", "optional", "undetermined", "all"), default="corrective")
    search.add_argument("--include-optional", action="store_true", help="legacy file-search compatibility")
    search.add_argument("--json", action="store_true", help="emit stable JSON (the default for script safety)")
    search.add_argument('--full', action='store_true', help='full diagnostic projections instead of a shortlist')
    search.add_argument('--limit', type=int, default=5, help='maximum shortlist cases (1–50; default 5)')
    search.add_argument('--method', choices=('ranked', 'substring'), default='ranked', help='local ranking or original literal search')
    search.add_argument('--broad', action='store_true', help='allow low-coverage lexical candidates; never implies applicability')

    show = commands.add_parser("show", help="show one canonical record from a cache")
    show.add_argument("identifier"); show.add_argument("--cache", required=True); show.add_argument("--json", action="store_true")
    show.add_argument('--full', action='store_true', help='include full upstream diagnostic catalog')
    explain = commands.add_parser("explain", help="explain applicability and canonical evidence; ordinary imports remain claims")
    explain.add_argument("identifier"); explain.add_argument("--cache", required=True)
    explain.add_argument("--environment", required=True); explain.add_argument("--json", action="store_true")
    explain.add_argument('--full', action='store_true', help='include full upstream diagnostic catalog')

    draft = commands.add_parser("draft", help="validate and save a local draft of any record type")
    draft.add_argument("candidate"); draft.add_argument("--corpus", nargs="*", default=[])
    draft.add_argument("--output", required=True)

    preview = commands.add_parser("preview", help="preview exact local publication bytes; never publish")
    preview.add_argument("draft"); preview.add_argument("--config", required=True)
    preview.add_argument("--destination", choices=tuple(sorted(("ledger", "toolkit", "upstream", "plugin"))), required=True)
    preview.add_argument("--title", required=True); preview.add_argument("--body", required=True)
    preview.add_argument("--attribution", required=True); preview.add_argument("--approve", action="store_true")
    preview.add_argument("--receipt")

    status = commands.add_parser("status", help="report cache age and offline/trust limitations")
    status.add_argument("--cache", required=True); status.add_argument("--stale-after", type=int, default=86400)
    status.add_argument("--json", action="store_true")

    audit = commands.add_parser("audit", help="propose investigation for private application receipts; execute nothing")
    audit.add_argument("receipts", nargs="+"); audit.add_argument("--json", action="store_true")

    receipt = commands.add_parser("record-application", help="explicitly record a private local application receipt")
    receipt.add_argument("--change-id", required=True); receipt.add_argument("--source-revision", required=True)
    receipt.add_argument("--changed-file", action="append", nargs=2, metavar=("REFERENCE", "SHA256"), required=True)
    receipt.add_argument("--rollback", required=True); receipt.add_argument("--output", required=True)

    skills = commands.add_parser("skills", help="plan or explicitly change portable skill exposure")
    skills.add_argument(
        "--agent", action="append", default=[],
        choices=("generic", "codex", "claude", "pi", "hermes", "antigravity"),
        help="agent profile to expose; repeat for each selected profile",
    )
    skills.add_argument(
        "--hermes-profile", action="append", default=[], metavar="NAME",
        help="existing lowercase named Hermes profile to expose; repeat to select more",
    )
    mode = skills.add_mutually_exclusive_group()
    mode.add_argument("--install", action="store_true", help="create tracked links without overwriting")
    mode.add_argument("--remove", action="store_true", help="remove only matching tracked links")
    return parser


def _dispatch(args) -> Any:
    if args.command == 'routes':
        from .routing import routes
        return routes()
    if args.command == "skills":
        from .skill_exposure import expose_skills
        mode = "install" if args.install else "remove" if args.remove else "dry-run"
        return expose_skills(
            Path.home(), agents=tuple(args.agent),
            hermes_profiles=tuple(args.hermes_profile), mode=mode,
        )

    import knowledge
    from .contributions import (audit_application_receipts, build_preview,
                                make_application_receipt, record_consent, save_draft,
                                write_local_json)
    from .discovery import compact_cache_status, explain_record, search_snapshot, show_record
    from .snapshots import build_snapshot, cache_status, import_snapshot, load_snapshot

    if args.command == 'sync':
        from .canonical import sync
        from .coordinator import Policy
        from .deployment import TOOLKIT, TOOLKIT_ID
        from .github_native import GitHubRead
        if args.config:
            if args.toolkit_revision or args.policy_revision:
                raise ValueError('Use either deployment config or explicit revisions')
            config = _json_file(args.config, 4096)
            if set(config) != {'version', 'deployment', 'repository', 'repository_id', 'toolkit_repository',
                               'toolkit_repository_id', 'toolkit_revision', 'policy_revision'}:
                raise ValueError('Invalid deployment config')
            policy = Policy(config['policy_revision'], config['toolkit_revision'], config['deployment'])
            if args.deployment is not None and args.deployment != policy.deployment:
                raise ValueError('Explicit deployment disagrees with config')
            if type(config['version']) is not int or config['version'] != 1 \
                    or type(config['repository_id']) is not int or config['repository_id'] != policy.repository_id \
                    or config['repository'] != policy.repository or config['toolkit_repository'] != TOOLKIT \
                    or type(config['toolkit_repository_id']) is not int or config['toolkit_repository_id'] != TOOLKIT_ID:
                raise ValueError('Invalid compiled deployment identity')
        else:
            policy = Policy(args.policy_revision, args.toolkit_revision, args.deployment or 'production')
        # Deliberately anonymous client reads: no account credential discovery.
        return sync(GitHubRead(deployment=policy.deployment), policy, args.cache)

    if args.command == "validate":
        return {"valid": len(knowledge.validate_corpus(_records(args.files)))}
    if args.command == "index":
        if args.source or args.output:
            required = (args.source, args.output, args.data_revision, args.toolkit_revision,
                        args.created_at, args.source_updated_at)
            if not all(required) or args.files:
                raise ValueError("Snapshot index requires source/output/revisions/timestamps and no record arguments")
            build_snapshot(args.source, args.output, data_revision=args.data_revision,
                           toolkit_revision=args.toolkit_revision, created_at=args.created_at,
                           source_updated_at=args.source_updated_at, source_reference=args.source_reference)
            if args.cache:
                import_snapshot(args.output, args.cache)
            return load_snapshot(args.output).manifest
        if not args.files:
            raise ValueError("Legacy index requires record files")
        return knowledge.build_index(_records(args.files))
    if args.command == "search":
        if args.cache:
            if args.files:
                raise ValueError("Cache search does not accept loose record files")
            environment = _json_file(args.environment) if args.environment else None
            intent = "all" if args.include_optional else args.intent
            return search_snapshot(args.cache, args.query, environment=environment, intent=intent,
                                   compact=not args.full, limit=args.limit, method=args.method, broad=args.broad)
        if not args.files:
            raise ValueError("Search requires either --cache or record files")
        return knowledge.search(knowledge.build_index(_records(args.files)), args.query, args.include_optional)
    if args.command == "show":
        result = show_record(args.cache, args.identifier)
        if not args.full:
            result['cache_status'] = compact_cache_status(result['cache_status'])
        return result
    if args.command == "explain":
        result = explain_record(args.cache, args.identifier, environment=_json_file(args.environment))
        if not args.full:
            result['cache_status'] = compact_cache_status(result['cache_status'])
        return result
    if args.command == "draft":
        candidate = _json_file(args.candidate)
        corpus = _records(args.corpus)
        return save_draft(candidate, corpus, args.output)
    if args.command == "preview":
        routing_config = _json_file(args.config, 256 * 1024)
        preview = build_preview(_json_file(args.draft), routing_config,
                                destination=args.destination, title=args.title, body=args.body,
                                attribution=args.attribution)
        result = {"preview": preview, "publication_mode": "local-handoff-only", "published": False}
        if args.approve:
            if not args.receipt:
                raise ValueError("Explicit approval requires --receipt")
            receipt = record_consent(
                preview, routing_config,
                approved_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                explicit_local_command=True,
            )
            write_local_json(args.receipt, receipt)
            result["consent_receipt"] = receipt
        elif args.receipt:
            raise ValueError("A receipt path is only valid with explicit --approve")
        return result
    if args.command == "status":
        if args.stale_after < 0:
            raise ValueError("Stale threshold cannot be negative")
        return cache_status(args.cache, stale_after_seconds=args.stale_after)
    if args.command == "audit":
        return audit_application_receipts([_json_file(path, 256 * 1024) for path in args.receipts])
    if args.command == "record-application":
        receipt = make_application_receipt(
            change_id=args.change_id, source_revision=args.source_revision,
            changed_files=[{"reference": reference, "observed_sha256": digest}
                           for reference, digest in args.changed_file],
            rollback=args.rollback,
            recorded_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        )
        write_local_json(args.output, receipt)
        return {"recorded": True, "output": args.output, "receipt": receipt}
    raise ValueError("Unknown command")


def main(argv=None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.command == "admission-check":
        from .admission import TreeCandidateV1, check_tree
        from .git_objects import GitObjectReader
        from .contributions import write_local_json
        try:
            report = check_tree(TreeCandidateV1(**_json_file(args.request)), GitObjectReader(args.git_dir))
            if args.output:
                write_local_json(args.output, report)
        except (OSError, ValueError, TypeError, KeyError, RecursionError):
            report = {"version": 1, "decision": "indeterminate", "additions": [],
                      "violations": [{"code": "INVALID_REQUEST_OR_OUTPUT", "record_id": None}]}
        _print(report)
        return {"accept": 0, "reject": 1, "indeterminate": 2}[report["decision"]]
    from .github_native import NativeUnavailable
    from .git_objects import ObjectUnavailable
    try:
        _print(_dispatch(args))
        return 0
    except (NativeUnavailable, ObjectUnavailable, KeyError, OSError, RecursionError, TypeError, UnicodeDecodeError,
            ValueError, json.JSONDecodeError):
        print("Rejected input: invalid, inaccessible, oversized, unsafe, or inconsistent local data.", file=sys.stderr)
        return 1
