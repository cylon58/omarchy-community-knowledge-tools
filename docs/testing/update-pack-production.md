# From the update-pack experiment to the client

Status: implementation in progress, not deployed. The
[prototype comparison](proof-formats.md) and [follow-up](update-pack-variation.md)
measure experimental transport. They do not measure this production implementation.

## What changes for a user

A first download still fetches the full proof. When a user has the exact preceding
proof, the client can fetch a small manifest and a pack of changed objects instead.
It assembles the new proof locally, then performs the same complete checks as a
full download. If the local copy does not match, it uses the full download.
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
client/cache and publisher tests before integration. No production-client bandwidth,
capacity or rollout success is claimed yet.

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
omission and retained-pack target matching. The module performs no I/O and is not
yet connected to the production client or publisher.

Independent task review passed both specification and quality checks with no
findings. Complete canonical replay remains a required integration check in the
next task, not a guarantee supplied by this data-only decoder.

### Client transport and cache (reviewed)

The integrated client passed74 related regression tests (16.184 seconds); a fresh
controller run of its13 focused tests passed in6.620 seconds. This is behavioral
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
0.092 seconds), and28 related tests passed in6.766 seconds. The new regression
checks actual oversized reads, preserved fallback and rejection one byte short.
Astra's scoped re-review passed with no new findings. Payload and aggregate caps
stay unchanged. Publisher integration and large actual-client measurement remain
outstanding.

The remaining release gate includes an actual-client ten-record update, wrong-base
fallback, same-head reuse and exact evidence parity. Timing must distinguish
download/assembly from full validation. A matching-base saving is not an all-user
saving, and fewer downloaded bytes do not remove growing history-validation cost.
