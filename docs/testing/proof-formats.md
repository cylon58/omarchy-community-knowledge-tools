# Comparing warm proof downloads

Status: first measurement complete; reporting/deadline corrections passed scoped
independent review. No production transport selected or deployed by this result.

## What we measured

We reconstructed the exact previously tested500-record/100-import graph, checked
all400 saved commit identities, then added ten records and ten receipts through
two real local planning/publishing/build flows. The resulting510-record proof was
1,462,685 bytes. The base proof matched the earlier reference hash exactly.

Every candidate reconstructed the exact target object map, reproduced the ordinary
full proof, and passed the normal canonical replay. All existing resource limits
remained enabled. The local run took118.04 seconds, made no external requests and
reported zero fixture contract violations.

[Raw result](../../experiments/growth/results/proof-format-experiment-v1.json)
includes source hashes, fixture identities, per-import timings, request counts,
publication overhead and all candidates—not just the smallest one.

## Results for this ten-record update

| Format | Warm bytes, including manifest | Fraction of full proof | Download requests | Total published proof artifacts |
|---|---:|---:|---:|---:|
| Full download | 1,462,685 | 100% | 1 | 1,462,685 |
| 64 hash buckets | 942,800 | 64.46% | 32 | 3,251,363 |
| 256 hash buckets | 412,569 | 28.21% | 41 | 3,651,523 |
| One chunk per object | 323,939 | 22.15% | 41 | 4,328,770 |
| One update pack | 80,207 | 5.48% | 2 | 1,542,892 |

Requests here concern proof transport only; common GitHub identity/head checks are
not included. Published totals include the retained complete proof for cold and
older clients. Ratios use the actual full successor gzip as denominator, not the
larger sum of separately compressed chunks. Manifests are uncompressed in this
prototype; their costs are counted rather than assumed free.

Forty new objects and two removed object keys were needed. The update pack sends
those differences only. It is useful only when the client has the matching base;
a different or missing base requires a full download. There are no patch chains.
Removed keys describe in-memory proof assembly, not filesystem deletion.

The two bucket formats missed the declared25% transfer target in this fixture.
Per-object chunks met it but required41 requests and substantially more publication
storage. The update pack is the strongest candidate for follow-up, not yet an
approved production parser or a general95% bandwidth-saving guarantee.

## CPU and unchanged data

Normal proof decode/canonical replay still took roughly5.2–6.1 seconds per format
on this machine. Update-pack reconstruction plus full encoding took another0.43
seconds; this is primarily a bandwidth optimization, not elimination of validation
cost. Local timings are not remote network measurements or admission throughput.

Same-head tests needed zero proof chunks. The prototype still counted a manifest
request for candidate formats; the full-download control counted zero. A production
client could first use its authenticated head equality, but that shortcut has not
been implemented or measured here.

## Reproduce and limitations

```sh
python -m experiments.growth.proof_formats --output /new/path/result.json
```

Use the measured source hashes in the report; the experiment module was new and
uncommitted during the run, based on `ed3a58b`. The saved report and prerequisite
are hash-bound. Output must be new. The initial100-import admission timing run is
not repeated; only the two successor contributions use the full admission path.

One update scenario against one synthetic history cannot establish performance for
all record sizes or histories. Follow-up variation, independent review, production
threat modeling, corruption/fallback tests and atomic-cache integration remain
necessary before rollout. The public notebook will preserve revisions and any
failed follow-up rather than replacing this report.

## Independent review findings

The reviewer reconciled the saved result's source hashes, byte arithmetic, request
counts, full-proof denominator and parity checks. Two harness defects still block
acceptance: a later failed format would discard earlier measurement details, and
candidate creation sat outside the120-second successor deadline even though the
reported timing included it. The600-second outer deadline remained in force.

The correction must preserve completed/failed candidate evidence and place the
entire named successor scope inside its phase deadline. Focused injected-failure
tests will exercise these paths. The original successful report is retained;
correcting failure handling does not justify silently rerunning or replacing it.

The correction now has nine focused tests, including late-candidate failure and
candidate-creation deadline injection. Root's combined proof-format/baseline run
passed15 tests in6.16 seconds. Scoped re-review approved both corrections without
new findings. No large rerun was performed for these reporting corrections.

The [reverse archive patch](../../experiments/growth/variants/proof-formats-v1.patch)
restores the exact measured experiment script, hash
`f3bf40f5e1da5d1c5c1b380b955875fd32df9cf62da890ab4b859a2defa85b41`.
For historical reproduction, use a disposable checkout, restore
`experiments/growth/baseline.py` from `ed3a58b`, then apply that reverse patch to
the matching corrected experiment revision. The archive deliberately restores
known reporting defects; it is not a production patch. Running the corrected
script is a new experiment and should use a new output file.
