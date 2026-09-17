# Update-pack follow-up

Status: measurements and harness/report corrections passed independent review.
No production transport deployed.

We repeated only the promising update-pack candidate against three deterministic
updates of the same500-record base. Each update added ten records and ten receipts
through two real local contribution flows, producing510 records/receipts. The
scenarios did not accumulate into530 records; each started from the captured base.

| Synthetic cohort pair | Warm bytes | Full proof bytes | Warm/full | Proof transport requests |
|---|---:|---:|---:|---:|
| 201 / 202 | 78,401 | 1,458,847 | 5.37% | 2 |
| 301 / 302 | 78,015 | 1,460,301 | 5.34% | 2 |
| 401 / 402 | 70,526 | 1,440,103 | 4.90% | 2 |

All three preserved the exact target object map, ordinary full proof, canonical
data and source. The first comparison's5.48% result was not the only small pack,
but these are still synthetic updates using one history and one template family.
This is not a guarantee for arbitrary record sizes, cold clients or old caches.

The single authorized run completed in176.67 seconds with zero real network calls
and zero fixture contract violations. Its790 emulated requests and36,643,678 response
bytes are cumulative across multiple independently bounded jobs, not one adapter's
budget. The private fixture's final Pages history has three entries because its
baseline state is restored between scenarios; `fixture.pages_publications` is that
final history length, not a cumulative count of all publications in the experiment.

[Raw result](../../experiments/growth/results/update-pack-variation-v1.json)
records source hashes, all scenarios, exact base/target identities, per-phase
timings and fixture totals. Shared immutable fixture caches remain between scenarios,
so these timings are not independent cold admission-throughput measurements.

## Reproduce

```sh
python -m experiments.growth.update_pack_variants --output /new/path/result.json
```

The experiment was based on `0dd1be3`, with the new module's measured hash recorded
in the result. The original graph, prerequisite and first format result are hash-
bound. Output must be new. No full100-import timing pipeline was repeated, and no
failed run was retried. A small smoke preceded this run; root subsequently ran the
seven focused variation tests successfully in3.80 seconds.

Production will use a separately reviewed codec and real client/cache integration,
not install this experimental module. Current-head validation, full fallback and
atomic canonical cache updates remain required regardless of the transfer ratio.

## Review corrections and historical reproduction

The review found that the overwrite-safety test exercised a helper rather than
the CLI, and that an inherited scenario field omitted the known base-proof size.
The corrected harness tests the actual CLI and supplies that field for future
runs. The successful raw result remains unchanged: its base block contains the
1,377,903-byte count, while each historical scenario's corresponding field is null.
The warm/full ratios use the successor's full proof and are unaffected.

The [reverse source patch](../../experiments/growth/variants/update-pack-variation-v1.patch)
reconstructs the exact measured script from the corrected version. In a disposable
checkout, apply it before reproducing the historical experiment. Its expected
script SHA-256 is `dfa144d4592d0d2d001d085261f75a5bf2992801261214cfb8aeb3b4ea26caa1`.
The unchanged result SHA-256 is
`753df999eb7e2d695736ab282b5701cfaa7bbf5866634cbeeb90955c4b8ff194`.
No large run was repeated to make these reporting/test corrections.

```sh
patch --silent --output=- experiments/growth/update_pack_variants.py \
  < experiments/growth/variants/update-pack-variation-v1.patch | sha256sum
python -m unittest tests.test_update_pack_variants
```

The corrected seven-test suite passed in 3.446 seconds. The added base-size
assertion first failed (`None != 3338`) on the small fixture, then passed after
the field correction. The actual-CLI test passed against existing safe behavior.
