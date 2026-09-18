# Testing fair contribution intake

Status: local implementation and scoped reviews complete, not deployed. This notebook separates
pure traversal tests, authenticated adapter checks, service publication tests and
live pilot evidence. Passing one layer does not prove the others. The chronology
below preserves rejected candidates and then their corrections; the latest local
result is in [Guarded queue result](#guarded-queue-result-reviewed).

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
That branch was subsequently removed with before/after characterization tests and
independent review in the [final harness preflight](native-growth-gates.md#final-rerun-preflight-pending-corrections).
The original scanner result and source hashes above remain historical evidence.
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
The corrected adapter commit `e9f17c3` passed both hosted Python versions in
[CI run 35282117546](https://github.com/cylon58/omarchy-community-knowledge-tools/actions/runs/35282117546).

## Service publication transaction (review pending)

The frozen service slice passed 98 focused tests in 55.886 seconds. Its version-2
handoffs bind the exact run, attempt, deployment and guarded event lane. The writer
selects whether to advance or preserve progress from its actual outcome; the build
checks the handoff before creating a reader or site. Public status adds a bounded
scan summary alongside cursor and progress-health fields, with a paired strict reader.

```sh
python -m unittest -q tests.test_service tests.test_fair_intake_adapters \
  tests.test_fair_intake tests.test_coordinator tests.test_distribution \
  tests.test_deployment tests.test_update_publisher
```

The tests cover accepted scheduled progress, deterministic not-ready outcomes,
legacy bootstrap, unknown prior state, direct admission with withheld publication,
receipt-pending/retry preservation, drift saturation, timestamp preservation and
invalid build handoffs rejected before I/O. The fixture/growth integration and the
small beyond-200 queue result are separate pending evidence. No service deployment
is established by this test set.

Independent review requested changes despite these passing tests. It found that
an inconsistent no-plan envelope could claim acceptance and advance progress,
legacy envelopes did not require the exact initial cursor and empty progress
history, and scan counters admitted impossible fetch/row/drift combinations.
The bounded queue experiment is on hold while these validation boundaries receive
regression tests and correction. These findings do not establish unauthorized
record admission: the existing authenticated writer remains the admission boundary.

The first bounded queue capture completed before the worker received the hold
message, without the required explicit controller run approval. Its raw output is
being preserved as superseded pre-fix evidence, not accepted release validation.
Separate fixture review found that it called planning and publication directly,
then built the site without the guarded build entrypoint. It also declared the
publish-artifact limit without measuring the serialized publish envelope. The
fixture must exercise those actual handoffs before its result can support a claim
about the complete hosted transaction. No real network or public load was used.

The first core correction passed 100 focused tests in 55.995 seconds; re-review is
pending. New regression tests first reproduced forged planless acceptance,
invented legacy cursor/history, and six unreachable scan-counter/stop combinations.
The correction requires coherent plan and terminal writer outcomes, normalizes
deterministic exhausted scans to idle, constrains bootstrap to its exact initial
state, and checks fetch/row/drift/stop relationships. Passing this set does not yet
accept the separate queue fixture or establish deployment.

Re-review confirmed all three original core findings were addressed, but caught
a new regression: the no-plan writer branch required the scheduled lane even for
a valid direct-event rejection/not-ready/unavailable result. That would fail the
run instead of preserving known progress (or withholding publication when prior
state is unknown). A second scoped correction is required: scheduled exhaustion
becomes idle; direct no-plan outcomes retain their own validated status.

The second correction first reproduced nine failing combinations: direct
not-ready/rejected/unavailable outcomes against current/legacy/unavailable prior
state. The regression test then passed all nine, and the full focused core set
passed 101 tests in 55.777 seconds. Scoped re-review is pending. No-plan direct
results cannot call admission; known state is preserved and unknown/legacy state
withholds publication.

Scoped re-review passed the second correction with all original findings closed
and no new important regression. A fresh controller run of the three targeted
envelope/direct-matrix regression tests passed in 0.395 seconds. Service-core
acceptance does not yet accept the separately reviewed end-to-end fixture.

The corrected fixture passed 22 growth/queue tests in 4.497 seconds and 34
proof/update/cold-wrapper tests in 16.361 seconds. It now calls the actual guarded
service entrypoints, measures serialized plan and publish artifacts, records the
baseline generator's hash, and tests scheduled build/deploy failure retention and
resumption. The wrapper correction moved one failure injection past the newly
guarded bootstrap so it still tests the intended later publication failure.
Experimental re-review and an explicitly approved post-fix raw capture are pending.

The superseded capture is preserved as
[raw pre-review output](../../experiments/growth/results/fair-intake-queue-v1-pre-review.json)
with a [source/reproduction manifest](../../experiments/growth/results/fair-intake-queue-v1-pre-review-source.json)
and [source archive](../../experiments/growth/variants/fair-intake-queue-v1-pre-review-source.patch).
Use a disposable checkout at the manifest's base revision and obtain the archive
from the later published revision before applying it. Do not apply experimental
source archives over an installed toolkit. The raw result's own missing baseline
hash is not rewritten; its companion manifest supplies that dependency explicitly.

## Guarded queue result (reviewed)

Experimental re-review accepted all three fixture corrections and independently
reconstructed all nine archived pre-review source identities. One authorized
post-fix capture then succeeded using the project's prepared Python environment:

```sh
python -c 'import json; from experiments.growth.fair_intake_service import run_queue; print(json.dumps(run_queue(), sort_keys=True, separators=(",", ":")))'
```

Use the prepared project virtual environment, not an unprovisioned system Python.
The first capture launch used system Python and stopped at a missing `jsonschema`
import before executing the fixture. That [launch failure](../../experiments/growth/results/fair-intake-queue-v2-launch-failure.json)
is separate from the [successful raw result](../../experiments/growth/results/fair-intake-queue-v2.json).
No source or dependency change was needed; the controller authorized one retry
with the existing environment. This is not a performance benchmark.

- The fixture models 205 closed entries and one eligible PR; it performs one
  authenticated synthetic import, not 205 imports or independent user reports.
- Explicit legacy bootstrap imports nothing and makes no canonical mutation.
- First scheduled scan consumes 200 closed rows in ten fetches, then publishes
  its cursor at the fetch limit.
- Second scan returns 26 rows including the repeated anchor page, consumes five
  remaining closed entries, evaluates the eligible PR and imports it.
- All nine guarded plan/publish/build command invocations exit zero. Serialized
  publish artifacts are 1,080/1,115/1,276 bytes, below the 65,536-byte limit.
- The complete fixture uses 100 emulated HTTPS requests, two canonical mutations
  (import and receipt), and three Pages replacements. It makes no real network
  requests and records zero fixture-contract violations.
- Final independently read canonical data contains five records and five receipts;
  the cursor has completed cycle2. Separate failure tests retain the entire prior
  public state and resume from it after build or pre-replacement deploy failure.

Raw SHA-256:
`75db803bc880c80fde4f2a5150d6ae50b1a678bd488744dd938b4384a5cfcb07`.
The raw source map identifies the measured implementation, including the baseline
generator. A fresh controller run of both queue/failure tests passed in 0.473
seconds. These results do not repeat the full 100-import growth gate, re-execute
the historical 400-OID reconstruction, or establish live hosted scheduling.

The reviewed integration commit `f0a1035` passed Python3.11 and3.13 hosted checks
in [CI run35286929382](https://github.com/cylon58/omarchy-community-knowledge-tools/actions/runs/35286929382).

## Limits to keep visible

A finite successful traversal is not immunity to unlimited spam or arrival rates
above the admission rate. Scheduling delays, repeated list drift and unavailable
Pages can still delay work. Cursor position is not a measured backlog size or an
estimated waiting time. The tests must not claim either.

Preserving a last-good cursor also makes prior Pages availability a requirement
for immediate republication. Admission may succeed while publication is withheld.
This tradeoff is deliberate and must be tested, not hidden behind a green job.
