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
scheduled builds or incomplete recovery. Warn on current main/publication drift
and measured persistent cursor reset; do not invent a grace-period duration from
one sample. Use bounded response sizes and hard wall deadlines.

GitHub run failures can notify repository watchers who enable those notifications.
Document that setup rather than claiming an alert was delivered. Keep a standalone
CLI suitable for an independently operated monitor: a checker on GitHub Actions
alone cannot detect an Actions-wide scheduler outage. Do not introduce a paid
monitoring service or external notification recipient without separate approval.

## Tests

Compare timestamps against wall time at the end of the observation, while enforcing
the hard request deadline from its monotonic start. A legitimate publication during
the fetch interval must not be rejected as future-dated merely because it happened
after the check began. Test this boundary with a controlled clock.

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

## Implementation preflight: measured versus inferred

Use fixed production/pilot status and distribution manifests plus fixed main-ref
and scheduled reconcile-run API reads. Authenticate the fixed repository's numeric
identity with its repository API response before interpreting main/run facts;
matching a name in a Pages document is not a replacement for that check.
Cross-check the fetched status bytes against
the distribution manifest and require matching source identities. These checks
establish an operational observation, not canonical evidence authority.

Completion-time preflight correction: workflow-run listings do not supply a
documented run-completion timestamp. Do not reinterpret `created_at` or `updated_at`.
For the selected completed/successful scheduled run, make one additional fixed
request to its numeric run/attempt jobs endpoint (`per_page=20`). Require a complete
bounded job list with matching run/head, and exactly one successful completed
`pages` job with valid start/completion timestamps. Report its timestamp explicitly
as `pages_job_completed_at`, not workflow completion. Apply the two-hour successful
publication-age check to this evidence. Missing, skipped or ambiguous Pages jobs
leave that check unavailable; do not chase earlier runs or arbitrary returned URLs.
Normal successful checks now use six reads (five if no eligible run exists), still
within the original eight-attempt/8-MiB/60-second bounds. A rerun's fresh publication
does not prove a fresh scheduler start; report run creation and attempt separately.
This narrows the claim without relaxing the age threshold.

The endpoint and field were checked against
[GitHub's job-attempt API](https://docs.github.com/en/rest/actions/workflow-jobs#list-jobs-for-a-workflow-run-attempt)
and one bounded public production response on 2026-09-17. Its `pages` job completed
at `2026-09-17T19:05:07Z` for run `35262441490`, attempt1. That sample establishes
field compatibility, not current service health or continued scheduled cadence.

Record count, receipt count/coverage, compressed proof bytes and distribution bytes
are directly available. The builder can expose object count/raw bytes from its
already validated proof. A builder adapter's calls/bytes or object visits must be
labeled as that adapter's scope, never all jobs or all upstream requests. Do not
compute exact canonical corpus bytes from reserialized records: stored JSON bytes
can differ. Leave usage unknown unless measured at its enforcement boundary.
Likewise, record count is not the canonical tree-entry count, which also includes
receipts and directories. A denominator alone is not a capacity measurement.

A single sample can report revision mismatch, stale generation, missing successful
scheduled runs, current retry/pending state and measured resource pressure. It
cannot establish how long a mismatch persisted or whether a cursor repeatedly
stalled/reset. Those require previous samples or explicitly persisted progress
metadata. Until available, report unknown rather than inventing duration or a
backlog estimate. A long full-cycle age is not by itself a missed SLA; no universal
full-cycle deadline has been established.

Initial policy decisions for implementation: generation older than four hours and
no successful scheduled run for more than two configured intervals are failures.
Warn at80% of a genuinely measured hard resource bound. Immediate main/Pages drift,
scan truncation and current cursor drift are warnings; unknown/partial upstream
facts are separate evidence-freshness information, not proof of service outage.
Incomplete receipt coverage and current unavailable/retry/receipt-pending intake
must remain visibly unhealthy. More precise persistent-drift and queue-time alarms
need measured pilot data or a defined previous-sample contract, not guessed ages.

The fair-intake integration will persist separate `cursor_health` monitoring
metadata under its publication transaction. Once present and strictly validated,
warn on three consecutive persisted drift scans or more than four hours since
recorded non-drift cursor progress. Unknown legacy progress stays unknown. These
are conservative operational warnings, not a proof of unfair scheduling or an
estimated contributor wait time. Direct-event publications must not refresh these
fields. Immediate main/Pages mismatch remains a warning without an invented
duration; no cross-run download/cache service is required for this first monitor.

Only install after reviewed health code and immutable workflow pins exist. Test
pilot and production reads without creating synthetic public contributions. Record
the workflow/run URLs, notification limitations and how to disable the monitor.
