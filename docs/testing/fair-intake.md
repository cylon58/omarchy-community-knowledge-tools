# Testing fair contribution intake

Status: implementation in progress, not deployed. This notebook will separate
pure traversal tests, authenticated adapter checks, service publication tests and
live pilot evidence. Passing one layer does not prove the others.

## Why this change

The current bounded recovery scan can miss contributions outside its newest-200
window. Immediate PR events remain the fast path. Scheduled recovery will instead
remember its position in an oldest-first list of all PRs, including closed ones.
Closing a PR therefore need not shift every later position in the scan.

The position is only a scheduling hint. It cannot approve a contribution, fabricate
receipts or bypass the writer's independent checks. If the list changes beneath
the saved position, the scanner detects the mismatch, restarts within the same
work budget and reports the drift.

## Declared test layers

1. Pure local traversal: at least 1,000 lifetime PRs with mixed closed, imported,
   rejected, not-ready and eligible entries. Every run stays within ten page reads,
   200 returned rows and 20 candidate evaluations; the first plan stops the scan.
2. Authenticated inputs: exact GitHub response shapes, fixed URLs, strict anonymous
   Pages status, shared resource limits and deterministic versus uncertain failure.
3. Publication transaction: progress persists only after permitted writer outcomes
   and successful static publication. Direct events and retries preserve it;
   unknown previous state withholds publication rather than erasing progress.
4. Bounded pilot: recognized old status bootstraps explicitly, then the real hosted
   process preserves progress. No synthetic bulk submissions to public intake.

Commands, source versions, counts and results are added as each layer is tested.
The design and task sequence are in [fair-intake design](../plans/fair-intake-design.md)
and [implementation plan](../plans/fair-intake-implementation.md).

## Pure traversal result (reviewed)

```sh
python -m unittest -q tests.test_fair_intake
```

Fourteen tests passed in a fresh controller run (0.072 seconds). The deterministic
fixture has exactly 200 closed, 200 already-imported, 200 rejected, 200 not-ready
and 200 eligible PRs. Across 201 simulated runs it planned all 200 eligible heads
exactly once in order, then completed the cycle. It evaluated exactly 600 candidates
(the rejected, not-ready and eligible groups); closed/imported entries required no
evaluation. Per-run maxima were two page reads, 40 returned rows and three
evaluations. Every run also asserts the declared ten/200/20 bounds.

Separate tests exercise the actual limits, anchor drift, repeated-page rejection,
short/empty tails, malformed inputs, exceptions and stopping partway through a
page. One tightened test initially expected at most 20 returned rows and failed:
the full repeated anchor page must count too, making the correct observed maximum
40. The expectation was corrected; accounting was not reduced.

Measured module SHA-256:
`e20d76447b1599aa2dc094c29553b009ef27bc13841b28578738b86ac6f0d0ae`.
Test SHA-256:
`651cb5b831c882ecca86f8950ccc26c008e9faa2d0deed7fac8ed5b10150543a`.
This fixture is part of the test file, not a live GitHub load test or a claim that
201 real scheduled runs occurred. Adapter/service integration remains untested.
Independent specification and quality review passed. One minor cleanup remains:
an unreachable defensive evaluation-limit branch has a misleading comment. The
actual twentieth-evaluation stop is covered and works; final release review will
triage the redundant branch.
The scanner commit `f2f706b` passed Python 3.11 and 3.13 hosted checks in
[CI run 35278691788](https://github.com/cylon58/omarchy-community-knowledge-tools/actions/runs/35278691788).

## Authenticated input adapters (reviewed after two corrections)

The candidate adds fixed created-ascending all-state PR reads and a bounded
anonymous read of the known Pages status file. An older valid source revision may
resume; malformed or partial new state is never mistaken for legacy bootstrap.
Repository identity and required response shape are checked before classifying
draft, closed, stale-base, pending-merge or deleted-source conditions as not-ready.
The writer still performs its independent preparation before any acceptance.

```sh
python -m unittest -q tests.test_fair_intake_adapters tests.test_fair_intake \
  tests.test_update_client tests.test_coordinator tests.test_service \
  tests.test_native_api_version tests.test_growth_baseline tests.test_growth_gates \
  tests.test_cold_postmortem tests.test_update_pack_production
```

This set passed 122 tests in 74.042 seconds; a fresh controller run of the 13
adapter tests passed in 0.339 seconds. Initial tests failed on missing interfaces.
The new merge-readiness checks then exposed two growth fixtures that omitted the
real response's `mergeable` field: their 44-test run had 15 failures and seven
errors. Adding `mergeable: true` to valid synthetic PR responses restored the
44-test set (14.741 seconds), without weakening the new checks.

The [reverse fixture patch](../../experiments/growth/variants/fair-intake-task2-pre-fixtures.patch)
preserves exact pre-change `baseline.py` and `gates.py` bytes. In a disposable
checkout of this candidate, `patch -p1 < experiments/growth/variants/fair-intake-task2-pre-fixtures.patch`
restores their hashes respectively:

- `ee633468093328f0a3a56abd4c84daaefdb7952174b73dc0f763f0f88f5891ef`
- `ea3d0054c5705e3b80178c45acac083e69129e220ec32fff1c1ded62556440a2`

Historical raw results are unchanged. Restoring fixture files alone does not
restore every production dependency: use the measured published revision and
source hashes when reproducing an older complete experiment. This adapter stage
does not yet wire durable service progress or demonstrate live queue behavior.

Independent review rejected this first adapter candidate despite the passing tests:

- Readiness branches ran before all required candidate fields were validated.
  Combined cases such as a stale base plus a missing head incorrectly returned
  “not ready” instead of unavailable, which could later advance the cursor.
- The public reader accepted a boolean as an upstream format version and checked
  some nested arrays only as lists. Malformed upstream payloads could therefore
  be recognized as legacy status and authorize bootstrap.
- Two malformed-parent tests accidentally installed recursive mocks. They passed
  because recursion became unavailable, not because malformed parents were checked.

The correction is in progress: validate common individual-PR shape before readiness,
validate the real nested status producer grammar, and make the parent tests exercise
their intended cases. Closed list entries still need no deleted-fork details.
These findings block adapter completion; passing test counts above are historical
evidence, not acceptance of the rejected candidate.

The first correction passed 64 scoped adapter/coordinator/upstream tests in
49.953 seconds; a fresh run of the 16 adapter tests passed in 0.388 seconds.
New regressions combine malformed fields with each readiness reason and consume
actual producer-shaped refreshed, partial and unavailable reports. Re-review is
pending. Exact nested parsing increased the status validator from 205 to 593 lines;
that coupling is an explicit maintenance concern, not hidden as a cost-free fix.
The parser remains pure data validation, with no upstream decisions or network reads.

Re-review confirmed the malformed-field and recursive-test corrections, but found
two remaining issues. Contradictory merge fields could still take an earlier draft
or stale-base branch, and the new discovery parser rejected legitimate tag names
such as `nightly` by applying the stricter downstream version-selection rule.
A second scoped correction is required. This is why producer compatibility is
tested separately from whether a release is eligible to support update advice.

That second correction passed 39 adapter/upstream tests in 1.123 seconds and a
fresh controller run in 1.086 seconds. Scoped re-review marked both remaining
findings addressed, with no new important issues. Contradictory open-candidate
merge fields are now checked before readiness reasons, and the actual producer's
`nightly` discovery output is accepted. These are still candidate-code results,
not proof of deployed queue persistence. The original failures above remain part
of the record.

## Limits to keep visible

A finite successful traversal is not immunity to unlimited spam or arrival rates
above the admission rate. Scheduling delays, repeated list drift and unavailable
Pages can still delay work. Cursor position is not a measured backlog size or an
estimated waiting time. The tests must not claim either.

Preserving a last-good cursor also makes prior Pages availability a requirement
for immediate republication. Admission may succeed while publication is withheld.
This tradeoff is deliberate and must be tested, not hidden behind a green job.
