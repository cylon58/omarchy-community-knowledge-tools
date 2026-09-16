# Pause, recovery and offline operation

Production is [cylon58/omarchy-community-knowledge](https://github.com/cylon58/omarchy-community-knowledge)
(ID 1373429914). The isolated [pilot](https://github.com/cylon58/omarchy-community-knowledge-pilot)
has ID 1373467908. Toolkit code is
[cylon58/omarchy-community-knowledge-tools](https://github.com/cylon58/omarchy-community-knowledge-tools)
(ID 1373429982). The repository owner governs exceptional recovery.
Toolkit: MIT. Data: CC BY 4.0. No Omarchy endorsement.

## Emergency pause

Disable BOTH generated native workflows in the affected repository and cancel
queued/running instances when appropriate. Disable Pages publication as well if
sensitive or invalid derived data could be exposed. Do not merely disable a push
workflow: imports use native tokens and publication runs explicitly in the intake
workflow. Preserve relevant run IDs, exact commit/tree IDs, and bounded status
artifacts; never copy secrets or raw private records into an issue.

For leaked credentials, revoke them at their provider first. Report privately via
the [ledger](https://github.com/cylon58/omarchy-community-knowledge/security/advisories/new)
or [toolkit](https://github.com/cylon58/omarchy-community-knowledge-tools/security/advisories/new)
security form. Privacy removal is an owner-governed exception to append-mostly data.
Remove affected derived Pages output and refresh caches after reviewed correction.
GitHub history/caches, forks and downloaded copies may retain public content;
deletion cannot recall those copies.

## Reconcile before resuming

Inspect the accepted import commit's persisted source metadata and canonical
receipts. Receipts must match their deterministic path, accepted blob, semantic
record hash, PR/account/head identity, policy and toolkit revision. Do not infer
original authorship or H from today's mutable PR.

The generated manual reconciliation entrypoint first repairs the last 100
first-parent commits, then attempts at most one new snapshot. A retry or
receipt-pending result halts new imports. If a missing receipt is older than the
window, keep intake paused and perform a separately reviewed recovery from its
exact immutable import metadata. Never enlarge the window or discard conflicting
receipts as a quick workaround. The trusted coordinator CLI's `reconcile`
command can repair only its documented bounded window.

Re-enable the workflows only after the canonical corpus validates, all relevant
receipts reconcile, and the reviewed toolkit/policy/action pins and repository
guards are restored. Use the pilot for regression proof before production
activation. No normal PR edits toolkit/workflow policy.

## Retry, backlog and stale outputs

For artifact-related recovery, **rerun all jobs** or start a **new reconciliation
dispatch on main**. Artifact names bind both run ID and run attempt. Rerunning
only failed jobs can leave a successful producer's artifact on the previous
attempt while the consumer requests the new attempt's name; it fails safely and
does not reuse that older artifact. This also applies to build-only or Pages-only
retries, and artifacts expire after one day. Keep the strict artifact boundary;
restart the complete producer chain instead of renaming or copying old artifacts.

A GitHub test merge can be pending or stale. CAS can lose a base race. Both are
safe retry-later results. Replanning against today's immutable B/H/T is required;
do not replay old serialized plans as authority. Accepted means snapshot H was
imported; PRs stay open, and newer heads remain new work.

Each run scans at most 200 newest open PRs, rotates up to 20 preparation attempts,
and imports at most one valid snapshot. Invalid candidates are skipped. Both
entrypoints share a non-cancelling `queue: max` concurrency group, capped by GitHub
at 100 pending runs. This is a bounded queue, not guaranteed event delivery.
Scheduled/manual scans recover missed work in their window; older PRs outside it
may need a reviewed explicit wakeup or operator handling. Scan truncation is public.

The schedule is minute 17 of every hour. GitHub can delay or stop schedules.
Artifacts retain one day; plans/status remain bounded, and every published site
identifies its canonical revision and generation time. Quotas, network failures,
unsupported Git object encodings and the 180-second API budget can yield
unavailable. A failed build/deploy leaves the prior site visibly aging; inspect
Actions as well as site status.

## Local cache recovery

`omarchy-knowledge status --cache cache` reports the verified revision, source and
age. Search/explain remain offline. A missing, modified or inaccessible canonical
key/seal fails closed. Ordinary `index --cache` replacement is claims-only.

Do not manually copy a canonical trust flag or key from an untrusted snapshot.
Re-run `sync --config deployment.json --cache NEW_CACHE` with the reviewed installed
toolkit and inspect its source before using the fresh cache. Retain the old cache
until the replacement succeeds. A newer sync replaces the entire upstream
envelope; it never merges an old favorable observation into a new unknown state.

No cache record or recovery command applies a desktop change. Private local
application receipts can be inspected with `audit`; proposed cleanup still needs
the user's decision.
