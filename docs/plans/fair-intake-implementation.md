# Fair intake implementation sequence

Design authority: [fair intake design](fair-intake-design.md). This is a staged
implementation plan, not a claim that any stage is deployed. Existing admission,
receipt, object, HTTP, artifact, event and CAS checks remain binding. Do not raise
limits or turn scheduling hints into authorization. Use test-first implementation
and independent task review; one implementation worker at a time.

## Task 1: pure bounded traversal

Create a small scheduling module and focused tests. Own cursor validation and a
scanner accepting bounded page-fetch and candidate-evaluation callbacks; no token,
network, filesystem, service or writer implementation in this module. Use the
exact cursor fields, bounds, tail and drift rules in the design. Return a fixed-
schema transition and counters, not contributor-derived prose. Preserve before
and proposed-after states separately. Network/shape uncertainty must propagate as
failure without a usable after transition. A completed bounded scan is not an
unavailable result. A candidate plan stops scanning immediately after that item.

Count every fetched page and every returned row, including anchors and restarts,
against ten fetches /200 rows; at most20 candidate evaluations. Prior offset20
requires anchor validation before moving to the next page. A short page completes
the cycle only after consuming all its rows. Stable already-imported/closed rows
advance without candidate-evaluation cost. No claim about unbounded arrival rates.

Test exact boundaries, two candidates on one short page, all-invalid pages,
anchor drift, repeated pages, short tail, empty tail, callback uncertainty, and
malformed cursors/list rows. Add a deterministic1,000-lifetime-PR fixture with
mixed closed/imported/rejected/not-ready/eligible states, successive persisted
transitions, and exact eventual inspection of every unchanged eligible head.
Keep traversal tests local and cheap; no real public PRs or full import benchmark.

## Task 2: authenticated input adapters

Extend fixed GitHub list reads with the design's created-ascending all-state
grammar and bounded page parameter. Keep old callers working until Task3. Add
fixed anonymous Pages status retrieval under existing aggregate budgets, with
1MiB response bound, no redirects/credentials and strict source/shape validation.
Recognize valid cursor, explicitly recognized legacy status and unavailable state
separately. Do not require an older cursor's revision to equal current main.

Introduce a fixed-code candidate-not-ready exception distinct from uncertainty.
Validate PR/target identity and required shape before using draft/closed/stale/
merge-pending/deleted-source reasons. Follow the design's exact presence/type rules
for merge_commit_sha, mergeable, head repository and merge parents. Malformed or
missing fields remain unavailable. Preserve writer re-preparation: a scheduling
classification never authorizes acceptance. Reuse authenticated receipt/import
facts for exact-head suppression; scheduling state cannot manufacture them.

Test response shape and reason matrix, malformed-identity ordering, fixed URL and
credentials boundary, response/deadline limits, legacy versus unavailable status,
older valid revision and strict list parsing. Extend independent native fixtures
only with the real API response shape required by these tests.

## Task 3: service integration and publication transaction

Connect Task1 traversal and Task2 adapters to plan/publish/build. Derive the lane
from guarded event identity; no submitted lane selector. Direct events preserve
cursor, scheduled events alone propose progress. Follow every row of the design's
publication matrix, including legacy bootstrap and withhold on unknown prior
state. Uncertain scheduled scans exit nonzero before a publishable artifact.

Keep cursor data outside admission plan/import/CAS authority. Carry versioned,
bounded run-ID/attempt/deployment/lane envelopes through both artifact boundaries;
validate them again at build. Select after only following allowed admission/
recovery outcome. Retry/receipt-pending preserves before with degraded health.
Build refuses pages_publishable=false before creating a site. Canonical validation
and successful deployment remain necessary for persisted progress.

Update service fixtures and growth wrappers to use the new exact envelope contract,
without disabling their production-boundary assertions or original saved-report
reconstruction. Preserve historical result files. Test direct/scheduled identity,
run replay, unknown fields, retry and uncertainty, accepted record but withheld
Pages, failed build/deploy repeating prior progress, and successful explicit legacy
bootstrap. Publish one bounded end-to-end queue result and its limits. The final
full100-import growth run remains a later final-candidate gate, not a per-task test.

## Handoff

After all three reviewed tasks, the separate health task consumes only the safe
public projection. Pilot rollout must test old deployed status bootstrap before
production pins change. No cursor commits to canonical main and no synthetic load
against public intake. Public notes must include failure cases and operational
tradeoffs, especially the new Pages availability dependency for publication.
