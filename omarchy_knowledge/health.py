"""Fixed-origin, read-only public service health observation."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import http.client
import re
from typing import Any

from .github_native import (DEPLOYMENTS, HealthRead, MAX_BYTES, MAX_CALLS,
                            MAX_RESPONSE, NativeUnavailable, strict_json)
from .intake_status import PublicStatusKind, validate_intake_status
from .object_bundle import MAX_COMPRESSED_BUNDLE, MAX_OBJECTS, MAX_RAW_OBJECTS
from .snapshots import MAX_SNAPSHOT_BYTES


_OID = re.compile(r"[0-9a-f]{40}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
GENERATION_MAX_AGE = 4 * 60 * 60
SCHEDULE_MAX_AGE = 2 * 60 * 60


def _require(condition):
    if not condition:
        raise NativeUnavailable()


def _timestamp(value: Any) -> datetime:
    _require(isinstance(value, str) and value.endswith("Z") and len(value) <= 40)
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise NativeUnavailable() from exc
    _require(parsed.utcoffset() is not None
             and parsed.utcoffset().total_seconds() == 0)
    return parsed


def _oid(value):
    _require(isinstance(value, str) and _OID.fullmatch(value) is not None)
    return value


def _at(value):
    return _timestamp(value).isoformat().replace("+00:00", "Z")


def _age(now, value):
    seconds = (now - _timestamp(value)).total_seconds()
    _require(seconds >= 0)
    return seconds


def _display_age(seconds):
    return int(seconds)


def _measurement(used, limit, scope):
    _require(used is None or (type(used) is int and 0 <= used <= limit))
    return {"used": used, "limit": limit, "scope": scope}


def _transport(reader):
    return {
        "attempts": {"used": reader.http.calls,
                     "limit": HealthRead.MAX_ATTEMPTS},
        "charged_response_bytes": {
            "used": reader.http.bytes,
            "limit": HealthRead.MAX_CHARGED_BYTES,
        },
        "body_limit_bytes": MAX_RESPONSE,
        "deadline_seconds": HealthRead.DEADLINE_SECONDS,
    }


def _failure_report(deployment, checked_at, reader):
    return {
        "version": 1,
        "deployment": deployment,
        "checked_at": checked_at,
        "overall": "failure",
        "source": None,
        "availability": {"status": "failure", "reason": "unavailable"},
        "intake": {
            "status": "unknown",
            "format": None,
            "publication_status": None,
            "receipt_coverage": None,
            "scan_truncated": None,
        },
        "capacity": {"status": "unknown", "measurements": {}},
        "evidence_freshness": {
            "status": "unknown",
            "generation": None,
            "scheduled_pages": None,
            "revision_match": None,
            "upstream_status": None,
        },
        "warnings": [],
        "failures": ["availability"],
        "unknowns": ["intake", "capacity", "evidence_freshness"],
        "transport": _transport(reader),
    }


def _validate_repository(value, repository, repository_id):
    _require(isinstance(value, dict)
             and type(value.get("id")) is int
             and value["id"] == repository_id
             and value.get("full_name") == repository
             and value.get("default_branch") == "main")


def _validate_main(value):
    _require(isinstance(value, dict)
             and value.get("ref") == "refs/heads/main")
    obj = value.get("object")
    _require(isinstance(obj, dict) and obj.get("type") == "commit")
    return _oid(obj.get("sha"))


def _select_run(value, repository, repository_id):
    _require(isinstance(value, dict)
             and type(value.get("total_count")) is int
             and value["total_count"] >= 0)
    rows = value.get("workflow_runs")
    _require(type(rows) is list and len(rows) <= 20
             and value["total_count"] >= len(rows))
    eligible = []
    identities = set()
    for row in rows:
        _require(isinstance(row, dict))
        run_id, attempt = row.get("id"), row.get("run_attempt")
        _require(type(run_id) is int and 0 < run_id <= 2**63 - 1
                 and run_id not in identities
                 and type(attempt) is int and 0 < attempt <= 2_147_483_647
                 and row.get("event") == "schedule"
                 and row.get("head_branch") == "main")
        identities.add(run_id)
        repo = row.get("repository")
        _require(isinstance(repo, dict) and repo.get("id") == repository_id
                 and repo.get("full_name") == repository)
        _oid(row.get("head_sha"))
        _at(row.get("created_at"))
        _require(row.get("status") in {
            "queued", "in_progress", "completed", "waiting", "requested",
            "pending",
        })
        if row["status"] == "completed" and row.get("conclusion") == "success":
            eligible.append(row)
    return eligible[0] if eligible else None


def _pages_job(value, run):
    _require(isinstance(value, dict)
             and type(value.get("total_count")) is int)
    jobs = value.get("jobs")
    _require(type(jobs) is list and len(jobs) <= 20
             and value["total_count"] == len(jobs))
    identities = set()
    pages = []
    for job in jobs:
        _require(isinstance(job, dict)
                 and type(job.get("id")) is int and job["id"] > 0
                 and job["id"] not in identities
                 and type(job.get("run_id")) is int
                 and job.get("run_id") == run["id"]
                 and type(job.get("run_attempt")) is int
                 and job.get("run_attempt") == run["run_attempt"]
                 and job.get("head_sha") == run["head_sha"]
                 and isinstance(job.get("name"), str))
        identities.add(job["id"])
        if job["name"] == "pages":
            pages.append(job)
    _require(len(pages) == 1)
    page = pages[0]
    _require(page.get("status") == "completed"
             and page.get("conclusion") == "success")
    started, completed = (_timestamp(page.get("started_at")),
                          _timestamp(page.get("completed_at")))
    _require(_timestamp(run["created_at"]) <= started <= completed)
    return page


def _distribution(raw, status_raw, source):
    value = strict_json(raw)
    _require(isinstance(value, dict)
             and set(value) == {"version", "source", "files"}
             and type(value["version"]) is int and value["version"] == 1
             and value["source"] == source
             and isinstance(value["files"], dict)
             and 1 <= len(value["files"]) <= 32)
    total = len(raw)
    for name, metadata in value["files"].items():
        _require(isinstance(name, str) and len(name) <= 128
                 and isinstance(metadata, dict)
                 and set(metadata) == {"sha256", "size"}
                 and isinstance(metadata["sha256"], str)
                 and _SHA256.fullmatch(metadata["sha256"]) is not None
                 and type(metadata["size"]) is int
                 and 0 <= metadata["size"] <= MAX_SNAPSHOT_BYTES)
        total += metadata["size"]
    status_metadata = value["files"].get("status.json")
    _require(status_metadata == {
        "sha256": hashlib.sha256(status_raw).hexdigest(),
        "size": len(status_raw),
    } and total <= MAX_SNAPSHOT_BYTES)
    return total


def check(deployment, *, connection_factory=http.client.HTTPSConnection,
          now=None, wall_clock=None):
    """Observe one enum-selected deployment; never mutate or follow remote URLs."""
    _require(deployment in DEPLOYMENTS)
    reader = HealthRead(deployment=deployment,
                        connection_factory=connection_factory)
    clock = wall_clock or (
        lambda: datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    )
    # The monotonic transport deadline starts in HealthRead. Wall time is read
    # separately and comparisons use the end of the observation.
    if now is None:
        _timestamp(clock())
    checked = now
    try:
        repository, repository_id = DEPLOYMENTS[deployment]
        repo = reader.repository()
        _validate_repository(repo, repository, repository_id)
        main_revision = reader.branch()
        runs = reader.scheduled_runs()
        status_raw = reader.health_artifact("status.json")
        distribution_raw = reader.health_artifact("distribution.json")
        status_value = strict_json(status_raw)
        public = validate_intake_status(
            status_value, repository=repository,
            repository_id=repository_id, deployment=deployment,
        )
        run = _select_run(runs, repository, repository_id)
        job = None if run is None else reader.run_attempt_jobs(
            run["id"], run["run_attempt"])
        pages = None if run is None else _pages_job(job, run)
        distribution_bytes = _distribution(
            distribution_raw, status_raw, status_value["source"])

        checked = now or clock()
        observed_now = _timestamp(checked)
        generated_at = status_value["source"]["verified_at"]
        generation_age = _age(observed_now, generated_at)
        if run is not None:
            _age(observed_now, run["created_at"])
        pages_age = (None if pages is None else
                     _age(observed_now, pages["completed_at"]))

        warnings, failures, unknowns = [], [], []
        if generation_age > GENERATION_MAX_AGE:
            failures.append("stale_generation")
        if pages is None:
            failures.append("missing_successful_scheduled_pages_job")
        elif pages_age > SCHEDULE_MAX_AGE:
            failures.append("stale_successful_scheduled_pages_job")
        if main_revision != public.source_revision:
            warnings.append("main_pages_revision_mismatch")

        coverage = status_value["receipt_coverage"]
        if coverage["records"] != coverage["receipted_records"]:
            failures.append("incomplete_receipt_coverage")
        if status_value["status"] in {
                "unavailable", "retry", "receipt-pending"}:
            failures.append("unhealthy_intake")
        scan_truncated = (status_value.get("scan_truncated", False)
                          or (public.intake_scan is not None
                              and public.intake_scan.stop_reason in {
                                  "fetch-limit", "preparation-limit",
                              }))
        if scan_truncated:
            warnings.append("scan_truncated")
        if public.intake_scan is not None \
                and public.intake_scan.counters["cursor_drifts"] > 0:
            warnings.append("current_cursor_drift")
        if public.cursor_health is None:
            unknowns.append("cursor_health")
        else:
            if public.cursor_health.consecutive_drift_runs >= 3:
                warnings.append("persistent_cursor_drift")
            if public.cursor_health.last_progress_at is None:
                unknowns.append("cursor_progress")
            elif _age(observed_now,
                      public.cursor_health.last_progress_at) > GENERATION_MAX_AGE:
                warnings.append("stale_cursor_progress")

        health = public.build_health
        measurements = {
            "distribution_bytes": _measurement(
                distribution_bytes, MAX_SNAPSHOT_BYTES,
                "published-site-files-plus-raw-manifest",
            ),
        }
        if health is None:
            unknowns.append("build_health")
        else:
            proof = health["proof"]
            builder = health["canonical_builder"]
            measurements.update({
                "record_count": _measurement(
                    health["record_count"], 4096, "published-records"),
                "receipt_count": _measurement(
                    health["receipt_count"], 4096,
                    "authenticated-ingestion-receipts"),
                "proof_object_count": _measurement(
                    proof["object_count"], MAX_OBJECTS,
                    "validated-public-proof"),
                "proof_raw_bytes": _measurement(
                    proof["raw_bytes"], MAX_RAW_OBJECTS,
                    "validated-public-proof"),
                "proof_compressed_bytes": _measurement(
                    proof["compressed_bytes"], MAX_COMPRESSED_BUNDLE,
                    "published-full-proof"),
                "canonical_builder_request_attempts": _measurement(
                    builder["request_attempts"], MAX_CALLS,
                    builder["scope"]),
                "canonical_builder_charged_response_bytes": _measurement(
                    builder["charged_response_bytes"], MAX_BYTES,
                    builder["scope"]),
            })
            unknowns.append("canonical_builder_object_visits")
        capacity_warnings = []
        for name, measurement in measurements.items():
            used = measurement["used"]
            if used is not None and used * 5 >= measurement["limit"] * 4:
                capacity_warnings.append(name)
        warnings.extend("capacity:" + name for name in capacity_warnings)

        upstream_status = status_value["upstream"]["status"]
        if upstream_status in {"unknown", "partial", "not-refreshed"}:
            unknowns.append("upstream")
        overall = "failure" if failures else "warning" if warnings else "ok"
        return {
            "version": 1,
            "deployment": deployment,
            "checked_at": checked,
            "overall": overall,
            "source": {
                "repository": repository,
                "repository_id": repository_id,
                "main_revision": main_revision,
                "pages_revision": public.source_revision,
                "generated_at": generated_at,
            },
            "availability": {"status": "ok", "reason": None},
            "intake": {
                "status": ("failure" if any(item in failures for item in (
                    "incomplete_receipt_coverage", "unhealthy_intake")) else "ok"),
                "format": ("current-health-v1" if health is not None else
                           "current" if public.kind is PublicStatusKind.CURRENT
                           else "legacy"),
                "publication_status": status_value["status"],
                "receipt_coverage": dict(coverage),
                "scan_truncated": scan_truncated,
            },
            "capacity": {
                "status": "warning" if capacity_warnings else "ok",
                "measurements": measurements,
            },
            "evidence_freshness": {
                "status": ("failure" if any(item in failures for item in (
                    "stale_generation", "missing_successful_scheduled_pages_job",
                    "stale_successful_scheduled_pages_job")) else "ok"),
                "generation": {
                    "observed_at": generated_at,
                    "age_seconds": _display_age(generation_age),
                    "max_age_seconds": GENERATION_MAX_AGE,
                },
                "scheduled_pages": (None if pages is None else {
                    "run_id": run["id"],
                    "run_attempt": run["run_attempt"],
                    "run_created_at": run["created_at"],
                    "pages_job_completed_at": pages["completed_at"],
                    "age_seconds": _display_age(pages_age),
                    "max_age_seconds": SCHEDULE_MAX_AGE,
                }),
                "revision_match": main_revision == public.source_revision,
                "upstream_status": upstream_status,
            },
            "warnings": sorted(set(warnings)),
            "failures": sorted(set(failures)),
            "unknowns": sorted(set(unknowns)),
            "transport": _transport(reader),
        }
    except (KeyError, NativeUnavailable, TypeError, ValueError, RecursionError):
        checked = checked or now or clock()
        return _failure_report(deployment, checked, reader)
