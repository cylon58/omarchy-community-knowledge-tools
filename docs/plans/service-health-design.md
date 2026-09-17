# Service health — candidate design

Status: design; no monitor is installed by this document.

## What to measure

Separate availability, capacity and evidence freshness. A running service with no
authenticated upstream resolution is not equivalent to a failed service, and a
successful Actions job is not proof that the static catalog is current.
The current service can publish an `unavailable`/`retry` intake status while its
entrypoint exits zero. Inspect public intake status and timestamps independently
of the workflow conclusion; a green job alone is not healthy intake.

Public build health should include record/receipt counts, proof object/byte totals,
distribution bytes, source revision, build time, intake outcomes and queue traversal
progress. Show warning thresholds relative to actual enforced bounds. Unknown
backlog size stays unknown until the tail is observed; do not report a partial scan
as the full queue.

Do not derive a universal full-cycle deadline from the hourly cron alone. Scheduled
recovery admits at most one eligible snapshot per run; direct PR events provide
the ordinary fast path. A backlog with many eligible entries can take many runs,
even if every run works correctly. Report time since progress, cycle age and the
tested backlog shape separately. A healthy scanner is not a promise of short wait
times under arrivals above its throughput. Do not hide this by calling a cursor
position an estimated queue length.

## Unattended checker

A separately scheduled read-only toolkit workflow can check the fixed production
and pilot Pages endpoints plus public workflow-run state. No credentials on Pages,
no arbitrary URL argument, no publication/issue-write token and no remote code.
It should fail visibly for a catalog older than four hours, prolonged failed/missing
scheduled builds, main/publication drift beyond a grace period, persistent cursor
reset or incomplete recovery. Use bounded response sizes and hard wall deadlines.

GitHub run failures can notify repository watchers who enable those notifications.
Document that setup rather than claiming an alert was delivered. Keep a standalone
CLI suitable for an independently operated monitor: a checker on GitHub Actions
alone cannot detect an Actions-wide scheduler outage. Do not introduce a paid
monitoring service or external notification recipient without separate approval.

## Tests

Use captured synthetic responses: healthy fresh publication, stale timestamp,
future timestamp, malformed response, redirect, oversized body, API outage, delayed
publication, failed workflow, partial upstream facts and queue-reset stagnation.
Assertions concern health output and exit status, never merely YAML/source strings.
Actual live read-only verification must be recorded separately from fixture tests.

## Deployment gate

A [read-only live observation](../testing/scheduled-service-observation.md) found
roughly five-hour gaps between recent production scheduled runs despite hourly
configuration. The cause is not diagnosed. Keep missed-run/freshness checks and
the independent-monitor limitation explicit; do not call the configured cron an
observed hourly service guarantee.

Only install after reviewed health code and immutable workflow pins exist. Test
pilot and production reads without creating synthetic public contributions. Record
the workflow/run URLs, notification limitations and how to disable the monitor.
