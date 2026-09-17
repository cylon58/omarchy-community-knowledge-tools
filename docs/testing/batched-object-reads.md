# Batched immutable-object reads

Status: candidate reviewed, corrected comparison passed, not deployed. This experiment addresses the
[cold-recovery request limit](cold-recovery-postmortem.md), not search ranking.

## First candidate run: failed, preserved

[Raw result](../../experiments/growth/results/native-batched-reads-v1.json)
reconstructed the original graph with all 400 predecessor/result-head comparisons
matching. No contribution pipeline was rerun and no real network was used.

The candidate fetched 1,902 hash-checked Git objects in 238 emulated requests:
101 REST commit reads, 135 GraphQL batches, and two repository/ref reads. Response
bytes were 9,296,371, below the existing 32-MiB allowance. The fixture modeled one
point per GraphQL request and a non-exhausted quota; those values are not a live
Actions-token measurement or proof of hourly provider capacity.

The run then failed before completing canonical validation. The new prefetch
traversal incorrectly applied a per-root 4,096-entry bound to a combined flattened
list of historical roots. That list was unnecessary for historical prefetching.
This is a candidate implementation defect, not a reason to raise the bound.
Record/receipt totals, adverse-evidence parity and proof fields remain null in the
failed comparison. Fetching the objects did not establish a passing cold gate.

The [archived traversal patch](../../experiments/growth/variants/native-batched-reads-v1-traversal.patch)
maps the fixed candidate back to the measured failing module. Its reconstructed
module hash was checked against the v1 report. It is an experimental reproducer,
not a fix to install; use only in a disposable checkout of its matching source.

The entire diagnostic took 19.29 seconds; reconstruction took 16.52 seconds and
the failing cold phase 2.55 seconds. These are local fixture timings, not wire
latency. The raw report includes the uncommitted candidate's code hashes and dirty
paths. It is preserved unchanged while a focused regression and review address
the defect. A later corrected result must use a different output file.

## Reproduction and acceptance

### Corrected comparison

[V2 raw result](../../experiments/growth/results/native-batched-reads-v2.json),
measured on clean commit `784f7e363ad7c97a83436866e2b911d97ac3fc5c`, passed:

| Measurement | Result |
|---|---:|
| Canonical records / receipts | 500 / 500 |
| Adverse reports retained | 100 |
| Emulated requests | 238 of 512 |
| GraphQL requests / reported points | 135 / 135 |
| Response bytes | 9,296,371 of 33,554,432 |
| Verified objects / raw bytes | 1,902 / 4,402,758 |
| Native logical object visits | 2,507 of 5,000 |
| Full compressed proof | 1,377,903 bytes |
| Cold comparison including replay/reference | 11.78 seconds |
| Entire experiment including reconstruction | 23.89 seconds |

All 400 graph-identity checks matched. Canonical data and offline replay matched
the local reference; the proof SHA-256 remained
`a5f81730b40229fe646c52ecf6cc89d0eb469e76895bedb681e32ea2e461621c`.
There were no fixture contract violations. Native visit accounting is lower than
the reference's 4,414 visits because prefetch does not charge speculative lookups
as logical traversals; raw object and proof contents remain identical.

This clears the exact failing cold-recovery case, not the entire launch gate.
The fixture does not measure network latency, actual hourly Actions-token quota,
queue fairness, outages, or unbounded history growth. The original full-pipeline
failure and first candidate failure remain published unchanged.

```sh
python -m experiments.growth.batched_reads --output /new/path/comparison.json
```

Use the source revision identified by the selected result, accounting for its
recorded candidate changes; running a later corrected revision is a new experiment.
The harness binds the original distributed report by SHA-256 and verifies every
reconstructed mutation identity. Output must be new. All production validation and
request/object/byte/deadline limits remain enabled.

The corrected comparison must complete canonical validation, preserve all 500
records/receipts and 100 adverse reports, and match the local reference and offline
proof replay. Even success there is narrower than the final full-pipeline gate.
No automatic deployment follows from a local result.

The comparison's elapsed time includes native canonical reading, offline proof
replay and the local reference check. It is not a pure network/native-read timing.

## Review evidence

The pre-review candidate passed 66 focused tests and a one-import native smoke.
A broader local run passed 360 tests in 92.94 seconds, with one optional wheel-
integration test skipped. Independent review nevertheless found a missing shared-
subtree case: duplicate OIDs within one tree frontier could be rejected before a
read batch. A stable per-frontier deduplication correction and shared-subtree
regression passed scoped independent re-review. Root then ran the two focused
modules: 21 tests passed in 1.35 seconds. The regression checks that both logical
paths remain traversed even though the shared tree is fetched only once. Passing
the existing suite was not treated as proof that every valid graph was supported.
