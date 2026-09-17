# Hosted proof reuse: bounded experiment

Date: 2026-09-17. Candidate implementation, not deployed.

The hosted service can reuse the preceding published proof as an optional cache
of hash-checked Git objects. It still authenticates current repository/main and
validates the current records and receipt history. This does not change client
downloads or make old data authoritative.

## Reproduce

```sh
python -m experiments.growth.proof_reuse
python -m unittest tests.test_hosted_proof_reuse tests.test_object_bundle tests.test_service
```

[Saved raw output](../../experiments/growth/results/proof-reuse-2.json) includes
source hashes and runtime details. The fixture accepts two synthetic imports:
the first produces a cached proof; the second supplies the current graph.
There are no real network requests, paid calls or public contributions.

| Measurement | Cold | Previous-proof seed |
|---|---:|---:|
| Repository/ref reads | 2 | 2 |
| Raw Git object reads | 40 | 21 |
| Seed downloads | 0 | 1 |
| Total modeled requests | 42 | 24 |
| Modeled response bytes | 33,070 | 21,077 |
| Local validation/export/replay seconds | 0.153445 | 0.141840 |
| Published proof bytes | 4,920 | 4,920 |

Both paths retain ten records, ten receipts and two failure reports, with the
same source identity. Exported proof is replayed without object-network access.
These are single-run local timings, not a statistically established speedup.
Request counts are adapter calls; byte counts use compact serialized adapter
results plus the compressed seed. They do not exercise real HTTP limits or
measure GitHub latency. The next native-boundary growth test addresses that gap.

## Failures and corrections

- An initial manual table omitted two repository/ref reads from its totals.
  The saved reproducer now counts identity, objects and seed separately. The
  earlier labels of 40 cold / 22 seeded were not total request counts.
- Replay initially compared receipt list order, although valid API tree ordering
  can differ. Comparison now normalizes only receipt order, preserving all fields
  and duplicates; a regression exercises permuted valid tree ordering.
- Seeded raw trees cannot supply blob sizes when direct blob children are absent.
  Such trees are omitted from the cache and fetched normally, rather than caching
  unknown sizes.

## Limits retained

Seed objects must fit within 75% of existing raw-byte/object limits, reserving
space for current changes. Failed downloads consume the original request/byte
budget conservatively; fallback does not reset it. Each hosted job seeds its own
cache and shares its original deadline through validation and offline replay.
The strict client remains current-head-matching and bundle-only.

This reduces transport work, not full-history verification complexity. It does
not establish the 500-record growth gate, queue fairness, live throughput or
unbounded capacity. Independent review and pilot evidence precede deployment.
