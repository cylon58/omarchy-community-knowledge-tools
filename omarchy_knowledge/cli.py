"""Command line access to local records and the accepted Git cache."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

from .contributions import (
    check_pr,
    draft,
    fixed_in_text,
    preview,
    record_evidence_lines,
    render_sharing_note,
)
from .agents import AGENT_CHOICES
from .git_store import DEFAULT_CACHE, DEFAULT_REPOSITORY, load_cache, status, sync
from .records import load_records
from .research import related, release_claims, search, show
from . import plugin_catalog


def _plugins(options):
    try:
        if options.plugin_command == "status":
            value = {"source": plugin_catalog.status(options.cache)}
        elif options.plugin_command == "sync":
            value = {"source": plugin_catalog.sync(options.cache)}
        elif options.plugin_command == "show":
            value = plugin_catalog.show(options.cache, options.plugin_id, offline=options.offline)
        else:
            value = plugin_catalog.search(options.cache, options.query, limit=options.limit,
                                          offline=options.offline)
    except (ValueError, OSError, plugin_catalog.sqlite3.Error) as error:
        print("Plugin discovery failed: " + str(error), file=sys.stderr)
        return 1
    source = value["source"]
    if source.get("state") in {"stale", "offline"}:
        print("Using cached plugin catalog from " + source["generated_at"] +
              "; last successful check: " + source["checked_at"] +
              ("; refresh failed: " + source["refresh_error"] if source.get("refresh_error") else ""),
              file=sys.stderr)
    if options.json:
        print(json.dumps(value, ensure_ascii=False, sort_keys=True))
    else:
        print(f"Marketplace catalog: {source['count']} listings; generated {source['generated_at']}")
        print(f"Last checked: {source['checked_at']}; state: {source.get('state', 'cached-status')}")
        print("Source: " + source["url"])
        if source.get("refresh_error"):
            print("Last refresh error: " + source["refresh_error"])
        if source["warnings"]:
            print(f"Marketplace reports {len(source['warnings'])} warnings; use --json for details.")
        rows = value.get("results", [value["plugin"]] if "plugin" in value else [])
        for row in rows:
            print(f"{row['id']}: {row['name']}")
            print(f"  Author: {row['author'] or 'not specified'}; repository owner: @{row['github_owner']}")
            print("  " + row["repo"])
            print("  " + row["description"])
            print(f"  Marketplace status: {row['status']}; install available: {row['installAvailable']}")
        if "total_matches" in value:
            print(f"Showing {len(rows)} of {value['total_matches']} matches. Listings are not local test results.")
    return 1 if options.plugin_command == "sync" and source.get("state") == "stale" else 0


def _source(snapshot):
    return {"revision": snapshot["revision"], "synced_at": snapshot["synced_at"]}


def _print(value, as_json):
    if as_json:
        print(json.dumps(value, ensure_ascii=False, sort_keys=True))
        return
    source = value["source"]
    print("Source revision: " + source["revision"])
    print("Last successful sync: " + source["synced_at"])
    if "related" in value:
        detail = value["related"]
        records = [detail["case"], *detail["changes"], *detail["reports"], *detail["events"]]
        if detail["flags"]:
            print("Evidence warnings: " + ", ".join(detail["flags"]))
        for record in records:
            print(f"{record['id']} {record['type']}")
            for line in record_evidence_lines(record):
                print("  " + line)
        for claim in value["release_claims"]:
            print(f"Release claim {claim['event_id']}: community claim; current official check required")
            print("  Upstream URL: " + claim["upstream_url"])
            print("  Claimed fixed-in conditions: " + fixed_in_text(claim["fixed_in"]) + ".")
            if claim["links"]:
                print("  supporting links: " + ", ".join(claim["links"]))
            if claim["flags"]:
                print("  evidence warnings: " + ", ".join(claim["flags"]))
    elif "results" in value:
        for record in value["results"]:
            print(record["id"] + " " + record["payload"]["title"])
            print("  search method: " + record["search_method"])
            if record["evidence_flags"]:
                print("  evidence flags: " + ", ".join(record["evidence_flags"]))
    elif "record" in value:
        record = value["record"]
        print(record["id"] + " " + record["type"])
        print(json.dumps(record["payload"], ensure_ascii=False, sort_keys=True))
    else:
        print("Validated records: " + str(value["count"]))


def _snapshot_age(snapshot, *, now=None):
    try:
        synced = datetime.fromisoformat(snapshot["synced_at"].replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if synced.tzinfo is None:
        return None
    current = now or datetime.now(timezone.utc)
    return max(0.0, (current - synced).total_seconds())


def _age_text(snapshot):
    age = _snapshot_age(snapshot)
    if age is None:
        return "unknown age"
    hours = age / 3600
    return f"{hours:.1f} hours old"


def _search_snapshot(cache, repository, *, offline):
    try:
        snapshot = load_cache(cache)
    except ValueError:
        if offline:
            raise ValueError("No accepted cache snapshot is available for offline search") from None
        return sync(cache, repository)
    age = _snapshot_age(snapshot)
    if offline:
        warning = f"Offline search: using validated snapshot from {snapshot['synced_at']} ({_age_text(snapshot)})"
        refresh_error = status(cache).get("refresh_error")
        if refresh_error:
            warning += "; last refresh failed: " + refresh_error
        print(warning, file=sys.stderr)
        return snapshot
    if age is None or age >= 24 * 60 * 60:
        try:
            return sync(cache, repository)
        except (ValueError, RuntimeError) as error:
            print(
                f"Refresh failed; using validated snapshot from {snapshot['synced_at']} "
                f"({_age_text(snapshot)}): {error}",
                file=sys.stderr,
            )
    return snapshot


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="omarchy-knowledge")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("sync", "status"):
        command = commands.add_parser(name)
        command.add_argument("--cache", default=str(DEFAULT_CACHE))
        command.add_argument("--repository", default=DEFAULT_REPOSITORY)
        if name == "sync":
            command.add_argument("--plugins", action="store_true", help="also refresh the separate plugin index")
    plugins_command = commands.add_parser("plugins", help="search the marketplace with refresh on use")
    plugin_commands = plugins_command.add_subparsers(dest="plugin_command", required=True)
    for name in ("search", "show", "sync", "status"):
        command = plugin_commands.add_parser(name)
        command.add_argument("--cache", default=str(DEFAULT_CACHE))
        command.add_argument("--json", action="store_true")
        if name in {"search", "show"}:
            command.add_argument("--offline", action="store_true")
        if name == "search":
            command.add_argument("query", nargs="?", default="")
            command.add_argument("--limit", type=int, default=5)
        if name == "show":
            command.add_argument("plugin_id")
    search_command = commands.add_parser("search")
    search_command.add_argument("query")
    search_command.add_argument("--cache", default=str(DEFAULT_CACHE))
    search_command.add_argument("--repository", default=DEFAULT_REPOSITORY)
    search_command.add_argument("--offline", action="store_true")
    search_command.add_argument("--intent", default="all", choices=("all", "corrective", "optional", "undetermined"))
    search_command.add_argument("--limit", type=int, default=5)
    search_command.add_argument("--json", action="store_true")
    show_command = commands.add_parser("show")
    show_command.add_argument("record_id")
    show_command.add_argument("--cache", default=str(DEFAULT_CACHE))
    show_command.add_argument("--json", action="store_true")
    show_command.add_argument("--related", action="store_true", help="show all linked evidence for a case")
    validate_command = commands.add_parser("validate")
    validate_command.add_argument("records")
    validate_command.add_argument("--json", action="store_true")
    draft_command = commands.add_parser("draft")
    draft_command.add_argument("records", help="record directory containing cases/, changes/, reports/, and events/")
    draft_command.add_argument("output", help="new draft directory; existing paths are refused")
    draft_command.add_argument("--existing", required=True, help="validated existing record directory")
    draft_command.add_argument("--json", action="store_true")
    preview_command = commands.add_parser("preview")
    preview_command.add_argument("draft_dir")
    preview_command.add_argument("repository")
    preview_command.add_argument("--title", required=True)
    preview_command.add_argument("--body", required=True)
    preview_command.add_argument("--attribution", required=True)
    preview_command.add_argument("--existing", help="validated existing record directory for referenced records")
    preview_command.add_argument("--json", action="store_true")
    check_command = commands.add_parser("check-pr")
    check_command.add_argument("repository", help="local Git repository used only for validation")
    check_command.add_argument("base", help="full current-main commit ID")
    check_command.add_argument("head", help="full pull-request head commit ID")
    check_command.add_argument("--json", action="store_true")
    skills_command = commands.add_parser("skills")
    skills_command.add_argument("mode", choices=("dry-run", "install", "remove"))
    skills_command.add_argument(
        "--agent", default="auto", choices=AGENT_CHOICES
    )
    skills_command.add_argument("--home", type=Path, default=Path.home())
    options = parser.parse_args(argv)
    if options.command == "plugins":
        return _plugins(options)
    if options.command == "sync":
        if options.plugins:
            result, errors = {}, []
            try:
                value = sync(options.cache, options.repository)
                result["knowledge"] = {"source": _source(value), "count": len(value["records"])}
            except (ValueError, RuntimeError) as exc:
                errors.append("Knowledge refresh failed: " + str(exc))
            try:
                result["plugins"] = plugin_catalog.sync(options.cache)
                if result["plugins"].get("state") == "stale":
                    errors.append("Plugin refresh failed: " + result["plugins"]["refresh_error"])
            except (ValueError, OSError, plugin_catalog.sqlite3.Error) as exc:
                errors.append("Plugin refresh failed: " + str(exc))
            result["errors"] = errors
            print(json.dumps(result, ensure_ascii=False, sort_keys=True))
            return 1 if errors else 0
        try:
            value = sync(options.cache, options.repository)
        except (ValueError, RuntimeError) as exc:
            print("Refresh failed: " + str(exc), file=sys.stderr)
            return 1
    elif options.command == "status":
        value = status(options.cache)
        print(json.dumps(value, ensure_ascii=False, sort_keys=True))
        return 0
    elif options.command == "validate":
        try:
            records = load_records(options.records)
        except ValueError as exc:
            print("Validation failed: " + str(exc), file=sys.stderr)
            return 1
        _print({"source": {"revision": "local-record-tree", "synced_at": "not-synced"}, "count": len(records)}, options.json)
        return 0
    elif options.command == "draft":
        try:
            existing = load_records(Path(options.existing))
            records = load_records(Path(options.records), existing=existing)
            value = draft(records, existing, Path(options.output))
        except ValueError as exc:
            print("Draft failed: " + str(exc), file=sys.stderr)
            return 1
        print(json.dumps(value, ensure_ascii=False, sort_keys=True) if options.json
              else f"Prepared {value['record_count']} local record(s) in {value['output']}; nothing was published.")
        return 0
    elif options.command == "preview":
        try:
            existing = load_records(Path(options.existing)) if options.existing else []
            value = preview(
                Path(options.draft_dir), options.repository, title=options.title,
                body=options.body, attribution=options.attribution, existing=existing,
            )
        except ValueError as exc:
            print("Preview failed: " + str(exc), file=sys.stderr)
            return 1
        print(json.dumps(value, ensure_ascii=False, sort_keys=True) if options.json
              else render_sharing_note(value, existing=existing), end="\n" if options.json else "")
        return 0
    elif options.command == "check-pr":
        try:
            value = check_pr(Path(options.repository), options.base, options.head)
        except ValueError as exc:
            print("Pull request validation failed: " + str(exc), file=sys.stderr)
            return 1
        print(json.dumps(value, ensure_ascii=False, sort_keys=True) if options.json
              else f"Valid data-only contribution: {value['added_record_count']} new record(s).")
        return 0
    elif options.command == "skills":
        from .skill_exposure import (
            ExposureConflict, install_for_agent, plan_for_agent, remove_for_agent,
        )
        operation = {
            "dry-run": plan_for_agent,
            "install": install_for_agent,
            "remove": remove_for_agent,
        }[options.mode]
        try:
            value = operation(options.home, options.agent)
        except (ExposureConflict, FileNotFoundError, ValueError) as exc:
            print("Skill setup failed: " + str(exc), file=sys.stderr)
            return 1
        print(json.dumps(value, ensure_ascii=False, sort_keys=True))
        return 0
    else:
        try:
            snapshot = (
                _search_snapshot(options.cache, options.repository, offline=options.offline)
                if options.command == "search" else load_cache(options.cache)
            )
            source = _source(snapshot)
            if options.command == "search":
                value = {"source": source, "results": search(snapshot["records"], options.query,
                                                                 intent=options.intent, limit=options.limit)}
            elif options.related:
                value = {
                    "source": source,
                    "related": related(snapshot["records"], options.record_id),
                    "release_claims": release_claims(snapshot["records"], options.record_id),
                }
            else:
                value = {"source": source, "record": show(snapshot["records"], options.record_id)}
        except (ValueError, RuntimeError) as exc:
            print("Research failed: " + str(exc), file=sys.stderr)
            return 1
        _print(value, options.json)
    return 0
