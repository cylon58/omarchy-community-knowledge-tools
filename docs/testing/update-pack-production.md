# From the update-pack experiment to the client

Status: implementation in progress, not deployed. The
[prototype comparison](proof-formats.md) and [follow-up](update-pack-variation.md)
measure experimental transport. They do not measure this production implementation.

## What changes for a user

A first download still fetches the full proof. When a user has the exact preceding
proof, the client can fetch a small manifest and a pack of changed objects instead.
It assembles the new proof locally, then performs the same complete checks as a
full download. If the local copy does not match, it uses the full download.
In particular, missing even one intervening data publication can put a client
outside this single-predecessor window. A busy ledger's infrequent clients may
therefore usually download the full proof. Routine unchanged builds preserve a
valid pack, but do not broaden its base window. Local indexed search and compact
agent responses provide token savings independently of this download optimization.
An unchanged catalog can reuse local proof bytes while still checking GitHub's
current repository identity and revision.

This is download acceleration, not a new source of authority. The manifest cannot
declare a workaround safe, manufacture confirmations or mark a fix released.
Record/receipt validation still decides what evidence belongs to the repository.

## Deliberate differences from the prototype

- Reuse the existing bounded Git-object bundle decoder for pack payloads.
- Bind the exact base and target object sets independently of gzip encoding.
- Keep one complete reusable local proof and one direct-predecessor pack; no chains.
- Count optional downloads and full fallback within the same existing budget.
- Commit the canonical snapshot before saving its optional acceleration cache.
  A failed cache write loses speed, not the successful canonical update.
- Keep full artifacts for older clients and clients that miss the update window.
- Retain a useful pack across unchanged builds only after its target matches the
  current proof; otherwise omit it.

## Checks and results

The [implementation plan](../plans/update-pack-production.md) declares decoder,
client/cache and publisher tests before integration. The actual-client measurement
below exercises production code on a local native fixture; it is not a deployed
bandwidth, live capacity or rollout claim.

### Inert decoder (reviewed)

Twelve new focused tests plus seven existing object-bundle tests passed in a fresh
controller run (19 tests, 1.268 seconds). Initial tests failed because the module
did not exist. A later self-review test exposed an unhashable deployment value
escaping as `TypeError`; explicit type validation now rejects it through the
codec's fixed error path. No existing bundle limits were raised.

```sh
python -m unittest tests.test_update_pack tests.test_object_bundle -q
```

Coverage includes fixed digest framing, compression-independent matching, exact
round trips, strict JSON/fields/versions, wrong contexts/hashes/sizes, invalid
removals/additions, malformed compressed payloads, existing bounds, rollback
omission and retained-pack target matching. The module performs no I/O; client
and publisher integration are separate stages described below.

Independent task review passed both specification and quality checks with no
findings. Complete canonical replay is an integration requirement, not a guarantee
supplied by this data-only decoder.

### Client transport and cache (reviewed)

The integrated client passed 74 related regression tests (16.184 seconds); a fresh
controller run of its 13 focused tests passed in 6.620 seconds. This is behavioral
verification on small local fixtures, not the larger transfer benchmark.

```sh
python -m unittest tests.test_update_client tests.test_update_pack \
  tests.test_object_bundle tests.test_hosted_proof_reuse tests.test_batched_reads \
  tests.test_distribution tests.test_live_resolution
```

Tests exercise actual cold/full, warm/update and unchanged-head sync, corrupt and
wrong-base candidates, typed-valid but canonically incomplete proofs, isolated full
fallback, fixed anonymous origins, shared resource limits and safe cache writes.
The previous valid CURRENT and proof remain after a failed refresh; failure to save
the optional proof after CURRENT commits does not turn a successful sync into failure.

Self-review found two regressions before handoff: empty worker output initially
charged zero bytes despite unknown partial transfer, and optional hosted seeding
was wrongly skipped when less than a fresh worker interval remained. Separate
failing tests captured both; fixes passed before the related regression run.
Independent review then caught a one-byte overflow-accounting defect: detecting
an oversized response can read one byte beyond the payload limit, while the
candidate reserved and charged only the payload limit. A four-byte-cap reproducer
read five bytes but charged four, slightly overstating remaining fallback capacity.
The correction includes that sentinel in reservations and failed-read accounting,
including the full fallback. Four focused tests passed (fresh controller run:
0.092 seconds), and 28 related tests passed in 6.766 seconds. The new regression
checks actual oversized reads, preserved fallback and rejection one byte short.
Astra's scoped re-review passed with no new findings. Payload and aggregate caps
stay unchanged. Publisher review and the larger actual-client measurement are
separate gates below.

### Publisher and measurement preparation

The frozen publisher slice passed 12 focused tests in 0.201 seconds and independent
specification/quality review with no findings. It reuses one bounded previous-proof
download, validates the current full proof before generating a pack, retains an
unchanged-head pack only when its target matches, and omits both optional files
together if invalid or too large. The full proof remains available.

```sh
python -m unittest tests.test_update_publisher
```

The small actual-client smoke initially exposed two incorrect harness assumptions:
its tiny update was about 89.8% of the full proof (not a failure of the predeclared
500-record, 25% gate), and equivalent corpora used different outer record ordering.
The smoke now checks exact ID-to-record mappings, all receipts and source/adverse
evidence. The size gate remains enabled for the larger declared fixture.

Before authorizing that larger run, controller inspection found that the harness
wrongly demanded a successful original baseline, even though our exact preserved
baseline deliberately records failed final cold recovery after 100 completed
imports. It also assembled results only at the end, risking loss of completed
stages after a later failure. Corrections must retain the pinned baseline and
400 immutable-identity checks, preserve incremental facts (including successful
publishing before a build failure), and enforce phase versus overall deadlines.
No 500-record measurement was spent on these known harness defects.

The remaining release gate includes an actual-client ten-record update, wrong-base
fallback, same-head reuse and exact evidence parity. Timing must distinguish
download/assembly from full validation. A matching-base saving is not an all-user
saving, and fewer downloaded bytes do not remove growing history-validation cost.

### Actual client and publisher measurement

The single authorized run succeeded in 68.001576 seconds. Independent review passed
the experimental addition and the publisher production slice. Two low-severity
harness-hardening findings remain recorded below; neither invalidates the saved run.
[Raw result](../../experiments/growth/results/update-pack-production-v1.json)
SHA-256: `f59444dfe7a60c7e6b25b7b9657a60049ae7fc1cc4d4bd59726bfe898ce5341a`.

```sh
python -m unittest tests.test_update_pack_production tests.test_proof_formats \
  tests.test_update_pack_variants
python -m experiments.growth.update_pack_production \
  --output /tmp/update-pack-production-new-result.json
```

Choose a previously nonexistent output path; the harness refuses overwrites.
The 25 focused tests passed in 10.830 seconds before the timed run. The script's
measured SHA-256 is `ceb95da8abf6db42e1dc40d4f3d9b27cd943086cc6217cd5eb24adfd2d321b27`;
the raw report includes all measured participant hashes and runtime information.
A fresh controller run of the publisher and actual-client harness tests passed
21 tests in 6.010 seconds after measurement, without repeating the large run.
The full suite then passed 427 tests in 109.906 seconds on Python 3.14.7, with
`OMARCHY_KNOWLEDGE_TEST_WHEELHOUSE` set to a prepared offline wheelhouse so the
installed-package smoke ran rather than skipped. Hosted Python 3.11/3.13 CI is a
separate gate; these local times are not platform-wide performance guarantees.
The reviewed publisher/experiment commit `bc43f8d` subsequently passed both hosted
versions in [CI run 35277753201](https://github.com/cylon58/omarchy-community-knowledge-tools/actions/runs/35277753201).

The fixture reconstructs the original 500-record/100-import history and checks
all 400 saved Git identities. One ordinary ten-record contribution produces 510
records, 510 receipts and 101 imports. This differs deliberately from the earlier
prototype's two five-record imports: two ordinary publications leave an intermediate
base, which the old client cache cannot use. A separate small regression exercised
exactly that missed-window lifecycle and observed manifest-plus-full fallback.

| Client state | Proof requests | Proof bytes | Client elapsed |
|---|---:|---:|---:|
| Cold, 500-record base | 1 full | 1,377,903 | 6.363 s |
| Exact predecessor, 510-record target | 2: manifest + pack | 42,709 | 7.811 s |
| Same target revision | 0 | 0 | 6.035 s |
| Wrong base | 2: manifest + full | 1,433,370 | 7.335 s |

Every client also made two repository-identity/current-ref requests (192 fixture
response bytes), and zero object-API requests. The target full proof is 1,432,816
bytes. Matching transfer includes the 554-byte manifest and 42,155-byte pack:
2.9808% of full bytes, below the predeclared 25% threshold. This is approximately
97% fewer proof bytes, **not** 97% less CPU time, tokens or end-to-end latency.
Full validation still ran; the matching client was not faster in this local test.

All four paths preserved records, receipts, source, upstream and adverse evidence
exactly. The strict fixture reported zero contract violations and no real network
requests. Across its several modeled jobs it counted 343 HTTPS exchanges and
16,683,301 response bytes; these are not a single job's budget or live GitHub load.
The run kept the existing limits, with 600 seconds overall and 120 per phase.
It reconstructed history rather than rerunning the full 100-import pipeline;
that final-candidate gate remains separate.

For earlier experiments, the fixture changed to support the optional pair. The
[reverse patch](../../experiments/growth/variants/update-pack-production-pre-fixture.patch)
restores the exact historical fixture hash
`75d59e5da64d41d14b1c4d6e09e5394914fbd754a19cc652e6a2754af76ce95a`:

```sh
patch --silent --output=- experiments/growth/gates.py \
  < experiments/growth/variants/update-pack-production-pre-fixture.patch | sha256sum
```

Raw historical outcomes remain unchanged. Later source changes require checking
out the measured revision or applying the documented historical reconstruction;
a current checkout is not automatically an exact reproduction of old timings.

Review found two future-reporting edge cases: the same-head path records parity
but its success gate currently checks only zero proof downloads, and the 25% gate
compares a six-decimal rounded ratio. The saved same-head parity fields are all true
and its matching ratio is far below the boundary. Final release review will triage
explicit parity gating and an exact integer threshold; these are not erased from
the experimental record or treated as reasons to repeat a successful large run.
