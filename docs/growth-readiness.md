# Growth readiness: design and release gates

Status: in progress, not a large-scale support claim. Baseline client: `2250d3c`.

## Approved direction

Keep Git canonical, deterministic data-only admission, static distribution and
offline local search. Measure the entire path before raising limits. Improve
incremental distribution, contribution fairness and unattended health reporting.
Publish reproducible synthetic tests, unsuccessful experiments and remaining
limits alongside successful results. Never publish private machine material,
credentials, or provider performance results restricted by provider terms.

## Architecture choices

1. Raise existing limits only: rejected; amplifies repeated history/API work.
2. Content-addressed incremental transport and bounded fair intake: preferred.
   Reuse immutable bytes, but retain current canonical identity and evidence checks.
3. Operate a database/search server: deferred; adds operational obligations before
   demonstrating that static artifacts and local indexing are insufficient.

The first transport improvement should preserve the current trust model: a
GitHub-authenticated current commit anchors hash-checked objects. Reusable chunks
are transport, not authority. Any future checkpoint that replaces historical
receipt proof requires a distinct security design and explicit authority contract;
it must not be smuggled in as a performance cache.

## Work sequence

1. Reproducible end-to-end synthetic baseline: real Git objects, real admission,
   receipt authentication, static build, sync and search; emulate only the network.
2. Use the measured bottlenecks to implement immutable-object reuse/partitioned
   distribution and safer queue traversal, with negative security tests.
3. Add freshness/capacity/backlog health signals and independent outage detection.
4. Review, run bounded pilot checks, document supported envelope, publish results.

## Evidence so far

The [testing notebook](testing/README.md) is the experiment register. Reviewed
candidate work on this branch includes batched receipt recovery and optional hosted
proof reuse; neither is a claim that the whole growth milestone has passed.
The initial baseline exposed both fixture subprocess overhead and repeated
recovery work. Its thirty-import timeout remains recorded. A later small proof
reuse comparison reduced modeled requests while preserving adverse evidence.

The native-boundary benchmark measures the real service wrappers,
including reconciliation, and enforce the production per-job bounds. Its review
caught an initial omission of reconciliation, ambiguous timeout/interruption
accounting, narrow search coverage, and overwriteable report output before larger
results were accepted. Fixing the measuring instrument is part of the experiment,
not evidence that production capacity improved. See the
[native-gate brief](plans/native-growth-gates.md) for the declared contract.

The ten-import calibration and concentrated 100-report profile passed. The larger
500-record/100-import run completed all imports/builds but failed final cold
recovery. Exact graph reconstruction reproduced exhaustion of the 512-request cap;
the separate local replay is not a native capacity pass. See the
[cold postmortem](testing/cold-recovery-postmortem.md). Bounded authenticated object
batching is the next candidate; fair intake, incremental client transfer and health
reporting remain unfinished. None of these branch changes is deployed yet.

The 500-record local postmortem also used 4,414 of 5,000 logical object visits. Batched
network reads cannot remove that validation cost. Treat the target as a bounded
beta envelope, not an assertion that thousands of historical imports will fit.
Capacity monitoring must expose proximity to this bound; further growth will need
a separately measured verification/layout improvement, not just a faster query
engine or raised caps. Any replacement of authenticated history with a checkpoint
would require its own explicit trust design.

## Predeclared gates

Initial beta targets (targets, not achieved measurements): at least 500 synthetic
records across 100 accepted imports with mixed evidence; each individual admission,
recovery and distribution phase below 120 seconds on the documented test runtime;
warm local search below one second for the declared representative query set.
Warm proof transfer after a ten-record update should be at most 25% of a full proof
at that size. Exercise at least 1,000 lifetime PRs with mixed open/closed/imported/
invalid states and verify eventual eligibility inspection for every unchanged open
head after a complete traversal under a stable backlog. Report external network
latency and API budgets separately. These targets are intentionally recorded before
the end-to-end baseline; failure blocks the corresponding readiness claim rather
than causing the target to be silently lowered.

- No hidden limit increases or weakened validation to make a benchmark pass.
- Exact source/provenance and adverse-evidence parity across cold and warm paths.
- Corruption, missing pieces, stale publication and interrupted refresh retain the
  previous valid cache and do not authorize action.
- Report first-load separately from warm refresh/search; report bytes, API calls,
  elapsed time, corpus size and history shape, not just a favorable average.
- Exercise cases with many reports/events, not only one case/change/report triplet.
  Include concentrated evidence on a single case as well as evenly distributed
  cases; the initial five-record-per-import baseline does not prove that envelope.
- Measure queue reachability beyond the current newest-200 window; disclose the
  finite tested backlog and behavior under changing lists, rejection and outages.
- No synthetic load against public contribution intake. Live pilot only uses
  deliberately bounded reviewed test operations.
- Broad launch remains blocked until the tested supported envelope and outstanding
  risks are documented. A failed test is a result to preserve, not erase.

## Current primary-source references

GitHub documents explicit PR pagination and ordering, and finite Actions limits:
[PR listing](https://docs.github.com/en/rest/pulls/pulls#list-pull-requests),
[Actions limits](https://docs.github.com/en/actions/reference/limits).
Consulted 2026-09-17; our own code limits can be substantially tighter.
