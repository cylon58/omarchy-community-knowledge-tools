# Recovery efficiency experiment — 2026-09-17

Status: measured candidate, not deployed and not a broad-launch pass.

## Change under test

Authenticate exact current/historical receipt bindings grouped by accepted import.
Skip recovery only for complete authenticated coverage. When receipts are missing,
validate the immutable import and write its exact missing receipt set in one
expected-base operation. Never combine different imports or use mutable PR identity
to reconstruct attribution.

This deliberately changes the trusted coordinator receipt batch ceiling from one
to at most ten receipts belonging to one accepted import. Community contributions
still cannot write provenance; corpus, object, network and time limits are unchanged.

## Reproduction and provenance

Run `python -m experiments.growth.baseline --imports N` for N = 3, 10, 30 with
the project's dependencies installed. Historical and candidate raw JSON are in
[`experiments/growth/results/`](../../experiments/growth/results/).
The [candidate provenance sidecar](../../experiments/growth/results/recovery-efficiency-provenance.json)
records the exact measured production-file hashes. The candidate was measured
before commit: its reported HEAD is the base revision, not the implementation.
The historical harness used a misleading `imports_accepted` label; its documented
meaning is completed successful publish returns, not an independently audited
canonical count after interruption. Original JSON has not been rewritten.

## Observations

Single local runs, Python 3.14.7; no network or public contributions:

| Workload / phase | Original | Candidate |
|---|---:|---:|
| 3 imports, full run | 4.705 s | 2.468 s |
| 10 imports, full run | 37.003 s | 21.065 s |
| 10 imports, aggregate setup/admission | 28.299 s | 14.846 s |
| 10 imports, reconciliation | 4.622 s | 2.677 s |
| 10 imports, canonical read/proof | 3.324 s | 2.869 s |
| 30 imports, completed returns before workload alarm | 18 | 22 |

Both 30-import runs hit the 120-second workload alarm during aggregate setup and
admission. Neither reached final canonical counts or search. This is not evidence
that one admission exceeds 120 seconds, or that a 30-import catalog cannot work;
it means this bounded full-run experiment did not establish that envelope. Cleanup
and reporting can add overhead beyond the workload alarm.

At ten imports, receipt batching reduced mutation calls from 60 to 20. Cold proof
construction still required 192 modeled raw-object reads, unchanged from the
baseline. That remaining API cost motivates testing immutable-object reuse next.
Modeled calls are not real requests; the fixture does not enforce HTTP quotas.
Import timings include local Git fixture construction and cannot be treated as
deployed Actions throughput. Seven synthetic account IDs are not seven users.

## Safety checks and remaining work

The new tests cover completed coverage, partial recovery, forged receipts, changed
current record bytes, immutable contributor attribution, concurrent writes,
ambiguous success and restart. The initial local full suite passed 297 tests with
one optional clean-wheel test skipped; independent review is a separate gate.

Independent review found a real adapter mismatch: the production GitHub writer
still required exactly one receipt file, while the local mutation fixture accepted
a batch. The timings above therefore measure the coordinator candidate, not a
working deployed batched writer. A production-writer boundary regression and
narrow writer fix are required before release. We retain these results rather
than presenting the passing fixture as proof that the hosted path worked.

The follow-up writer fix passed an eleven-test recovery suite and a 76-test
focused regression run. Tests now exercise ten receipt additions through the actual
GraphQL request builder, reject eleven and invalid lane/head/message inputs, and
reject mismatched or mixed-source authority grants. Independent scoped re-review
closed both findings. The timing files remain pre-adapter-fix observations; live
pilot and production deployment have not occurred.

The 500-record/100-import target, incremental-transfer target, concentrated evidence
case, fair-backlog traversal, hosted pilot and production rollout remain unproven.
No workload-alarm, corpus or network limit was raised to make these runs succeed.
