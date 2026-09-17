# Bounded fair intake candidate design

Status: candidate for pilot testing, not an unbounded fairness claim. This changes
no admission authority and adds no server or contributor-code execution path.

## Decision

Retain the direct `pull_request_target` path as the fast path. For scheduled
recovery, replace the newest-200-open-PR scan with a resumable traversal of

```text
state=all&sort=created&direction=asc&per_page=20&page=P
```

The cursor is a strictly validated, bounded scheduling hint copied through the
existing static `status.json`; it is not canonical evidence, a deduplication fact,
or permission to write. The fixed GitHub Pages origin is read anonymously without
redirects or credentials. Invalid, missing, or unavailable cursor state withholds
Pages publication rather than resetting unknown progress. A valid cursor from an
older data revision can still be resumed. A forged hint can repeat or
delay work, but cannot bypass `prepare`, writer re-preparation, complete corpus
validation, receipt recovery, or the expected-main-head CAS.

Bound the public status response separately from the small internal transition
envelope: at most 1 MiB, no redirects or credentials, strict JSON, and the existing
adapter's aggregate request/byte/deadline budget. Public status also contains
upstream facts, so do not accidentally reuse the internal 64-KiB artifact limit and
turn ordinary catalog growth into unavailable cursor state. Validate the expected
public shape and fixed source identity before extracting the scheduling hint.

Using all lifetime PRs makes open/closed transitions and deliberately open
accepted PRs retain their list positions. Created-ascending traversal makes new
arrivals append rather than shifting earlier pages. GitHub documents these sort
controls, but not an immutable tie-break contract, so exact anchors and drift
recovery are still required.

## Cursor and bounded scan

The public cursor contains only fixed-schema data such as:

```json
{
  "version": 1,
  "page": 1,
  "offset": 0,
  "after_pull_request": null,
  "cycle": 0,
  "last_full_cycle_at": null
}
```

Integers are bounded like existing PR identifiers. `offset` is 0 through 20.
When `offset > 0`, the scanner first verifies that item `offset - 1` is the stored
`after_pull_request`. On mismatch, reset traversal to page 1 / offset 0 and expose
`cursor_drift`; restarting only the current page can miss rows shifted across an
earlier page boundary. Restarting costs repeated work, so repeated drift must be
visible rather than presented as successful fair progress. After consuming a full
page, retain `page=P, offset=20`
until the next run verifies the anchor, then continue at `P+1`. This makes page
boundaries resumable without trusting an unverified page number alone.

One scheduled run may perform at most 10 list fetches / receive 200 list items and may
call `prepare` for at most 20 candidates. Closed, already-imported exact heads,
and other authenticated non-candidates advance the cursor without consuming a
preparation slot. Deterministic rejection or an authenticated candidate-local
not-ready/stale result also advances; it will be revisited on the next full cycle
or a new PR event. Stop after the first plan. Its proposed cursor is immediately
after that exact list item, so the next successful scheduled run resumes with the
following item rather than waiting a full cycle. A short page marks the tail;
stop, set the next cursor to page 1, increment `cycle`, and record a full-cycle
completion only after every returned row of that short page has been consumed.
Stopping at a planned item or preparation limit with later rows remaining must
retain the after-item cursor, even when that page is short. Anchor verification,
repeated-page reads and drift restarts all consume the same ten-fetch/200-returned-
item budget; no budget resets. Normal traversal is consecutive, with bounded drift
restart as the explicit exception. A repeated anchor page counts its entire response,
not only newly examined rows.

The implementation must distinguish these outcomes:

- `exhausted` / `no-eligible`: a successful bounded scan. Publish the advanced
  cursor even when every examined candidate was closed, imported, rejected, or
  deterministically not ready.
- `transport-unavailable`: network, quota, malformed response, object retrieval,
  or other uncertainty. Exit nonzero and publish no cursor progress.
- `planned`: carry both the before and proposed-after cursors in the same-run
  bounded artifact. Persist after writer revalidation/outcome and successful
  build/deploy; a failed pipeline leaves the older cursor and safely repeats work.

This distinction is necessary: the current broad `unavailable` result does not
distinguish a completed scan from uncertain transport. Inspection of the service
entrypoint confirms that status can currently be published with exit zero (unlike
the separate coordinator CLI); it does not inherently stop publication. The new
cursor path must permit progress after deterministic exhaustion but withhold it
after uncertain reads, rather than assuming existing exit behavior supplies that
guard. Otherwise invalid candidates can pin scanning or uncertainty can skip data.
Typed internal outcomes should be fixed enums and must not echo contributor text.

Only scheduled runs advance the cursor. Direct PR-event runs preserve it exactly.
If a direct run cannot recover the prior cursor, admission may still complete, but
that run must not deploy a reset cursor over the last good Pages state; distribution
can wait for reconciliation. This adds prior-Pages availability to immediate static
publication, not to admission safety. It is an explicit availability-contract
change and must be exercised in the pilot.

Bootstrap must distinguish a successful fixed-origin read of a legacy status
without a cursor from an unavailable/malformed response. A scheduled run may
initialize legacy state explicitly and report it; a direct event must not treat a
network failure as permission to erase established progress. Test initial rollout
as well as steady-state preservation.
The strict current public format requires `intake_cursor`, `cursor_health` and
`intake_scan`, each with its own version1; the exact recognized legacy format has
none. The scan projection includes lane, prior-state kind, selected action,
scan outcome, stop reason and the exact bounded scanner counters. A
partial set or unknown field/version is unavailable, not legacy. There is no
additional top-level status version for this step. Future health fields require
a coordinated strict producer/reader update and their own versioned payload.
The earlier adapter-only two-field format was never deployed; integration extends
its producer/reader together so current drift and scan costs are publicly visible.

The same withholding rule applies to scheduled prior-status failures. An earlier
design allowed a degraded page-1 reset; that could erase arbitrary established
progress during a temporary Pages outage. Only a successful, identity-bound,
recognized legacy status permits automatic bootstrap. Permanent missing/malformed
state needs an explicit governed bootstrap/repair, not a silent reset. This makes
Pages availability a dependency for persistent scan progress and static publication,
but not for admission authority. Last-good revision need not equal current main.

## Implementation boundary

Keep scheduling state separate from admission plans. A versioned batch carries a
strict cursor transition: lane, current/legacy/unavailable observation, before and
proposed cursors, advance/preserve/withhold action, and fixed-enum health counters.
None of these fields enters the exact snapshot plan, import grant or CAS authority.
Publishing selects the final cursor only after reconciliation and import outcome:
accepted or deterministic exhaustion may advance; recovery retry, receipt-pending
or undifferentiated writer retry preserves the old cursor. The final status carries
`intake_cursor` and `pages_publishable`; build refuses publication when false.

The final publication matrix is independent of whether admission wrote records:

| Lane / outcome | Prior cursor state | Cursor selection | Pages |
|---|---|---|---|
| Scheduled accepted or deterministic exhaustion | valid | proposed after | publishable after successful canonical build |
| Scheduled writer/recovery retry or receipt-pending | valid | before | publishable after successful canonical build, with degraded intake status |
| Direct PR event, any admission outcome | valid | before | publishable after successful canonical build |
| Either lane | unavailable or malformed | none | withhold |
| Scheduled | recognized legacy | explicit bootstrap, then ordinary outcome rules | publishable after successful canonical build |
| Direct PR event | recognized legacy | none | withhold until scheduled bootstrap |

Uncertain scheduled scans exit nonzero before producing a publishable transition.
Preserving a known cursor on retry does not claim intake is healthy; monitors must
still detect retry/receipt-pending and incomplete receipt coverage. A successful
canonical build remains required; this matrix cannot waive evidence validation.

Carry a strict internal envelope through publish-to-build, not just public status:
schema version, run ID/attempt, deployment, trusted lane and selected transition.
Build validates these bindings before creating a site, then projects only allowed
public fields. Existing same-run workflow artifact naming remains in force.
Derive lane from the already guarded event: `pull_request_target` is direct;
`schedule` and `workflow_dispatch` are scheduled traversal. No submitted field can
select a different lane or relax event/repository/ref guards.

Introduce a distinct fixed-code candidate-not-ready outcome only when a well-formed
authenticated PR response proves a local condition, such as draft/closed, old base,
explicitly pending merge, or a changed merge-parent pair. Missing fields, malformed
IDs, API/object failures and indeterminate responses remain unavailable. A scheduled
scan may advance past deterministic rejection/not-ready, but must abort on uncertain
reads instead of continuing to another candidate and publishing skipped progress.

Validate common response shape and numeric identity before classifying readiness;
validate all OIDs needed for the selected reason before using it. A pending merge
requires a present `merge_commit_sha: null` and a present `mergeable: null` or
`mergeable: false`, with otherwise valid open-candidate identity/base/head fields.
Missing keys, wrong types and inconsistent combinations remain unavailable. Valid
merge objects whose validated parent pair no longer matches current base/head are
not ready; malformed parent arrays/OIDs remain unavailable. An explicitly null
head repository may be a fixed `source-repository-unavailable` non-candidate only
after authenticating the PR number and target repository; missing/wrong-type head
repository is not equivalent. Closed list entries can be skipped using validated
list identity/state without fetching irrelevant deleted-fork details.

## Safety and capacity invariants

List results are scheduling input only. `prepare` still reads the individual PR,
binds exact repository/account, current base B, source head H, GitHub test merge
and tree T. The writer independently recreates the plan and the existing GraphQL
`expectedHeadOid` CAS handles stale main. A changed head is new work; receipt v2
and recent import history remain the only suppression facts.

The current shared non-cancelling writer lock remains. No cursor commit is added to
main, so routine scanning cannot consume the 100-commit canonical repair window.
The HTTP adapter should allow only the exact fixed pull-list grammar and bounded
page integers. Capture validated API call/byte counts and rate-limit headers where
available; do not retry blindly on quota or secondary-limit failures.

Fairness is conditional, not absolute. With stable created ordering, successful
scheduled persistence, and scan/admission throughput above arrivals, every finite
list position is reached and revisited. At most one snapshot is admitted per run,
so sustained valid arrivals above that rate create an unbounded real-world backlog.
Repeated schedule loss, Pages reset, or list reordering also weakens liveness. The
pilot must publish its tested corpus/history/backlog envelope rather than call this
starvation-free under unbounded arrivals.

## Recovery cost prerequisite

Widening discovery will not help if reconciliation spends its budget revalidating
every recent completed import. Safely optimize that separately:

1. Authenticate current v2 receipts once and return exact coverage keyed by
   accepted import commit and manifest addition. The current `(PR, head)` set is
   insufficient because one receipt does not prove all records in a multi-record
   import were receipted.
2. Skip `_repair` only when every manifest addition has its exact authenticated
   deterministic receipt. This is a no-write shortcut; later admission and static
   build retain their full corpus validation.
3. For an incomplete import, validate its immutable import once and add all of its
   missing receipts in one CAS. Permit up to 10 additions only for one exact
   `CoordinatorImportGrant`; never batch receipts across accepted import commits.
   Existing mismatches and ambiguous mutations still fail closed and are inspected.

This reduces repeated full-corpus checks, API reads, receipt commits, and pressure
on the 100-commit recovery window without weakening source binding.

## Minimum pilot tests

- More than 200 earlier accepted-but-open PRs do not hide a later candidate.
- Closed/reopened PRs and new tail arrivals do not silently move the cursor; an
  anchor mismatch resets earlier and reports drift.
- Two valid candidates on one page are admitted on successive successful runs,
  while 20 deterministic rejections cannot pin the next page.
- The scan never exceeds 10 pages, 200 items, or 20 preparations in one run.
- Exhaustion publishes progress; injected HTTP/object/quota uncertainty publishes
  none; a stale main/head never bypasses writer re-preparation or CAS.
- Missing/tampered/oversized/redirected Pages status withholds publication with a
  degraded signal; neither scheduled nor direct events erase unknown progress.
- Recognized legacy status bootstraps explicitly; a valid older-data cursor resumes
  without requiring its source revision to equal current main.
- A failed build/deploy repeats the prior cursor on the next run.
- Exact full receipt coverage skips repair; one missing or mismatched receipt takes
  the validated repair path, with at most one receipt CAS for that import.

## Externally visible health

Extend safe static status with aggregate, non-content fields:

- cursor source/validity, page, offset, cycle, reset/drift reason;
- last full-cycle time and age; pages/items scanned and prepare attempts this run;
- eligible, accepted, rejected, deferred, retry, and receipt-pending counts;
- API calls/bytes and validated primary-rate-limit remaining/reset when available;
- Pages generation time, canonical main revision, published data revision, and
  receipt coverage.

Do not publish an estimated total backlog unless it was actually measured. Cursor
position alone is not backlog size.

For stateless monitoring, keep a separate versioned `cursor_health` projection
alongside (not inside) the cursor: `last_progress_at` (UTC timestamp or null) and
`consecutive_drift_runs` (saturating nonnegative31-bit integer). This is scheduling
telemetry, never admission or receipt authority. The planning/publishing envelope
must preserve its prior value so direct events and failed/retry scheduled outcomes
cannot reset it. A permitted scheduled after-transition increments the drift
streak if that scan observed anchor drift; otherwise resets the streak to zero.
Advance `last_progress_at` only for a persisted non-drift cursor advance or completed
cycle, not merely a new publication timestamp or a reset to page1. Legacy status
starts with unknown progress time and zero observed consecutive drift runs; do not
invent historical progress. Failed deployment preserves the old public projection
just as it preserves the cursor. Test these semantics at the publication matrix,
not only in a standalone helper. The pure Task1 cursor remains unchanged.

An independent read-only monitor should poll public Pages status, main ref, and
the scheduled workflow-runs endpoint. Alert on no successful schedule for more
than two intervals, queued/pending age or cancellations, repeated cursor resets or
drift, lack of observed non-drift progress, receipt-pending/retry, low quota, stale
Pages generation, or main-to-Pages revision lag. A monitor running only in the
same Actions system cannot detect a platform-wide scheduler outage; broad launch
needs an independently hosted check or an explicit residual-risk statement.
Do not infer a universal full-cycle deadline from cursor position: backlog size and
successful scheduling cadence determine it. The health plan defines bounded
freshness/progress warnings without inventing a measured queue SLA.

## Primary references

Consulted 2026-09-17:

- [List pull requests](https://docs.github.com/en/rest/pulls/pulls#list-pull-requests)
  documents `state`, `sort`, `direction`, `per_page` (maximum 100), and `page`.
- [REST rate limits](https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api)
  documents 1,000 requests/hour/repository for Actions `GITHUB_TOKEN`, preferred
  response headers, and separately enforced secondary limits.
- [Workflow concurrency](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/control-workflow-concurrency)
  documents the 100-pending-run bound for `queue: max`.
- [Workflow troubleshooting](https://docs.github.com/en/actions/how-tos/troubleshoot-workflows)
  documents that schedules may be delayed and queued jobs may be dropped.
- [Workflow runs REST endpoint](https://docs.github.com/en/rest/actions/workflow-runs#list-workflow-runs-for-a-workflow)
  exposes public-repository run status, conclusion, and timestamps for monitoring.
