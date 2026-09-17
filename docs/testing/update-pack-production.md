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

The remaining release gate includes an actual-client ten-record update, wrong-base
fallback, same-head reuse and exact evidence parity. Timing must distinguish
download/assembly from full validation. A matching-base saving is not an all-user
saving, and fewer downloaded bytes do not remove growing history-validation cost.
