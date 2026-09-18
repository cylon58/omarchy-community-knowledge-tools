# Native service growth gates

Measurement scope: this gate covers native admission/reconciliation, canonical
build/proof validation, recovery and search. Ordinary per-import builds call the
canonical builder directly; they do not execute optional update-pack generation
and publication. A pass must not be called a complete current-publisher lifecycle
test. That path has separate [actual-publisher500→510 evidence](update-pack-production.md).
The guarded queue/bootstrap tests and live pilot establish additional, distinct
workflow boundaries. Report these together without conflating their scopes.

## Final rerun preflight (pending corrections)

After the reviewed fair-intake integration, read-only preflight found three
measurement gaps to correct before another expensive 100-import run: the final
source map omitted its baseline generator, cold/warm recovery failures could lose
their adapter metrics, and the newly explicit bootstrap lacked independent phase
timing/failure accounting. Existing raw results remain unchanged. These are
measuring-instrument corrections, not proof of increased production capacity.
The final run also awaits the health implementation and a frozen-source calibration.

The correction candidate passed51 focused/related tests in12.747 seconds:

```sh
python -m unittest -q tests.test_growth_gates tests.test_update_pack_production \
  tests.test_fair_intake tests.test_fair_intake_service
```

New tests first reproduced missing generator provenance and missing bootstrap/cold
failure metrics. A warm-prefill injection initially targeted the wrong call; it
was corrected to the actual warm prefill. Another cold test initially threw before
making adapter calls; it now throws after a real canonical read so retained nonzero
request observations are tested. Unknown final canonical/parity facts remain null.

Each guarded bootstrap command now has its own120-second phase clamp against the
same absolute overall budget. Partial reports retain elapsed, seed, adapter,
artifact/exit and observed mutation/publication facts. The final source map includes
the baseline generator and executed build-health producer dependencies.

The same slice addresses three previously deferred minor checks: exact integer
comparison for the25% byte gate, explicit same-head semantic parity, and removal
of an unreachable scanner branch with before/after characterization tests.
Changing the same-head condition back to zero-download-only made its regression
test fail as expected. Historical raw output was not edited.

Frozen gate SHA-256:
`c99f5662ff9713ea42a0fcab28482797c53323813e20ef31314259ff7dba8592`.
Independent review is pending. No new ten-import calibration or full100-import
timing run is established by these focused tests.

Independent specification and quality review accepted all six corrections with no
new important regression. Small diagnostics checked failed-proof accounting, the
exact byte boundary and successive absolute-deadline clamps. A fresh controller
run of five targeted failure/parity/byte-gate tests passed in2.295 seconds. Health
corrections and combined CI still precede calibration and final-run authorization.

These runs exercise real request adapters and service planning/publishing wrappers
against a strict local HTTPS fixture. No public PR, paid model call or external
network request is generated. The [harness contract](../plans/native-growth-gates.md)
and [predeclared targets](../growth-readiness.md) define what passing means.

## Ten-import calibration

```sh
python -m experiments.growth.gates ten-imports \
  --output experiments/growth/results/native-ten-imports-v1.json
```

The output must be a new file. [Raw result](../../experiments/growth/results/native-ten-imports-v1.json)
was generated on Python 3.14.7 at clean revision `6c4a2b2`, with measured source
hashes embedded. Ten imports, fifty records and fifty authenticated receipts
completed. Total elapsed time was 13.414 seconds; worst planning/admission was
1.035 seconds; final recovery/distribution was 2.264 seconds. The worst warm
search across the declared three-query set was 0.033 seconds.

Cold and seeded current-proof validation yielded identical canonical data and
20,149-byte proofs, retaining all ten failure reports. Final cold validation used
194 emulated requests; seeded validation used three. The largest individual
planning/publish/build job used 39 requests. Total requests across the whole
experiment were 956, not a single job's use of its 512-request allowance.

The first missing Pages seed deliberately charges a conservative 16 MiB plus one
byte against each job's existing budget. Charged adapter bytes can therefore
exceed bytes actually returned by the fake server. Both are recorded separately.

This is calibration, not the 500-record gate. Provider latency, hourly quotas,
scheduler throughput and queue fairness remain unmeasured. The fixture now uses
one bounded Git object-reader process, so its wall time is not a direct production
speedup comparison against the earlier subprocess-heavy baseline.

## Review corrections retained

Before accepting larger measurements, independent review found that an early
harness omitted service reconciliation, conflated phase and overall deadlines,
lost known mutation/publication facts on interruption, tested only one favorable
query, and could overwrite a report. Those defects were corrected and tested.
A second focused review closed a publication/bookkeeping interruption window by
making fixture publication state atomic. Earlier one-import developer observations
are historical incomplete-workflow measurements, not the calibration above.

The corrected harness uses exact, broad symptom/domain and declared no-hit
queries, each with a fresh cold cache followed by a warm repeat. It records parity,
result counts and adverse-evidence visibility. Overall-budget exhaustion is
`incomplete`; an individual phase-budget failure is `failure`. Successful publish
returns, observed record mutations and completed proof publications are separate
facts. Unknown final canonical counts are not inferred from those counters.

Larger profiles are recorded separately; no result here establishes unlimited
capacity or independent community reproduction.

## Distributed 500-record / 100-import run: failed gate

```sh
python -m experiments.growth.gates distributed-500x100 \
  --output experiments/growth/results/native-distributed-500-v1.json
```

[Raw result](../../experiments/growth/results/native-distributed-500-v1.json) uses
clean source revision `e679511` and the same measured implementation hashes as
the calibration. The actual run additionally requested a new private artifact
directory; its export was never reached, and no final proof artifact was retained.

All 100 publish returns and build/publication completions were observed, but the
overall gate **failed** with `NativeUnavailable` in `recovery-distribution`.
Total time was 1,294.067 seconds; worst planning/admission was 22.496 seconds
(p95 20.483), and worst build was 16.352 seconds. No phase timeout was reported.
The largest ordinary job used 79 requests. The final cold/warm comparison,
search checks and final record/receipt counts were not completed: their null
values must not be rewritten as successes inferred from the import counters.

The raw report contains 11,291 total emulated requests and 10,779 requests across
completed per-import jobs, leaving exactly 512 in the failing final phase.
The native adapter refuses the next request beyond its 512-call limit. A subsequent
[exact-graph postmortem](cold-recovery-postmortem.md) reproduced that refusal.
No limit was increased. There were zero real
network requests and zero fixture contract violations.

This result separates useful progress from readiness: the seeded import/build
path completed the intended history, but cold recovery did not. It does not pass
the declared 500-record growth gate. The unmodified failure report is preserved.

## Concentrated evidence: passed local profile

```sh
python -m experiments.growth.gates concentrated-100-reports \
  --output experiments/growth/results/native-concentrated-100-v1.json
```

[Raw result](../../experiments/growth/results/native-concentrated-100-v1.json):
eleven imports, 104 records and 104 receipts completed in 55.258 seconds. The
fixture contains one case, one change, 100 reports, a resolution and a dispute.
All 25 failure reports were retained; cold/warm canonical results and 37,902-byte
proofs matched. The worst warm query was 0.034 seconds. Worst admission was
4.078 seconds; final recovery/distribution was 9.308 seconds. Cold validation
used 281 emulated requests; seeded validation used three.

Measured code hashes match the previous runs. The report honestly marks its
worktree dirty because the preceding result and a research note were uncommitted
when it started; production/harness code was unchanged. A new private diagnostic
proof artifact was requested and exported successfully; its path is not in the
public report. This profile passing does not cancel the distributed-history
cold-recovery failure, nor do synthetic reports establish independent users.

## Later regression: wall-clock comparison

A fresh 25-test focused run after these measurements produced one failure in the
service-wrapper regression, despite exact proof parity. A direct one-import repeat
passed. A controlled status clock advancing one second per search reproduced the
failure: the six compact responses had ages 93,601–93,606 seconds. The comparison was
including live `age_seconds`, so otherwise identical cold/warm results could differ
at a second boundary. This is a harness defect, not evidence of a search regression.

The candidate correction evaluates real status logic at one declared fixture time for
both searches, while leaving performance/deadline clocks real and retaining all
freshness and evidence checks. Failed query details are saved before enforcing
the gate. Historical raw results above are unchanged; their full-data successes
were actual observations, but their comparison was susceptible to this false
negative. See the [regression brief](../plans/growth-search-clock.md).

Two new regressions failed before the correction and passed afterward: one checks
all six calls use the same real status-evaluation time, and the other injects a
genuine evidence mismatch and requires both a failed gate and saved query details.
The focused harness module passed 20 tests, and the one-import smoke passed with
three queries. This remains a measurement correction, not a production speedup.
