# Native service growth gates

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

A fresh25-test focused run after these measurements produced one failure in the
service-wrapper regression, despite exact proof parity. A direct one-import repeat
passed. A controlled status clock advancing one second per search reproduced the
failure: the six compact responses had ages93601..93606seconds. The comparison was
including live `age_seconds`, so otherwise identical cold/warm results could differ
at a second boundary. This is a harness defect, not evidence of a search regression.

The correction is to evaluate real status logic at one declared fixture time for
both searches, while leaving performance/deadline clocks real and retaining all
freshness and evidence checks. Failed query details must be saved before enforcing
the gate. Historical raw results above are unchanged; their full-data successes
were actual observations, but their comparison was susceptible to this false
negative. See the [regression brief](../plans/growth-search-clock.md).
