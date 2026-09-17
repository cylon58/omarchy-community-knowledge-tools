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

## Limits to keep visible

A finite successful traversal is not immunity to unlimited spam or arrival rates
above the admission rate. Scheduling delays, repeated list drift and unavailable
Pages can still delay work. Cursor position is not a measured backlog size or an
estimated waiting time. The tests must not claim either.

Preserving a last-good cursor also makes prior Pages availability a requirement
for immediate republication. Admission may succeed while publication is withheld.
This tradeoff is deliberate and must be tested, not hidden behind a green job.
