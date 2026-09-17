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
