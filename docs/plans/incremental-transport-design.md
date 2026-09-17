# Incremental proof transport — candidate design

Status: design under investigation; not implemented or deployed.

## Trust contract

Retain two fixed GitHub identity/ref reads and all current record/receipt/history
validation. A static manifest is an untrusted transport hint, never an authority
checkpoint. No downloaded executable database, redirects, caller-selected origin,
or bearer token on Pages requests. Missing proof remains unavailable.

## Proposed format and flow

- Keep the existing complete proof bundle for cold clients and compatibility.
- Experiment with partitioning verified raw objects into stable buckets by Git
  object-ID prefix; compare 64 and 256 buckets before choosing a format.
  Each nonempty bucket becomes a deterministically encoded, compressed, bounded
  chunk addressed by SHA-256. The chunk's encoding is independent of main's head.
- A strict small manifest names the current head and exact chunk hashes/sizes.
  Head must equal the separately API-authenticated revision. Decode and verify
  every typed Git object; duplicates, excessive totals, missing root/references,
  malformed chunks and incomplete receipt evidence fail closed.
- A cold client can use the complete bundle, avoiding dozens of initial requests.
  A warm client reads a bounded previous local proof bundle, derives its chunk
  hashes, and downloads only changed chunks. Old bytes are reusable content, not
  authority; current graph/receipt checks still run in full.
- Publish the new local reusable bundle atomically only after successful canonical
  validation. Keep one bounded cache file, not an accumulating object directory.
  Use no-follow descriptor-relative reads and exclusive temporary file + replace.
- Bound aggregate download bytes, requests and wall time, not only each chunk.
  On an interrupted refresh retain the previous canonical CURRENT unchanged.

## Hosted path

The builder/intake can potentially seed an in-memory object cache from an older
public proof, then fetch missing objects from the canonical API. Every object is
hashed; the current main is still confirmed separately. This is a deliberate change
from the existing builder's no-Pages-cache rule and needs negative tests and review.
It must be optional acceleration with bounded fallback, not dependence on a fresh
Pages artifact to publish the next one. Export only objects needed by the current
validated graph so stale unused objects do not accumulate across generations.

## Limits deliberately not solved by transport

Full schema validation and receipt verification still cost CPU. Historical flat
Git trees grow with imports; transport partitioning does not fix that. Canonical
record-directory sharding and authenticated checkpoint summaries require separate
design if baseline data show those are necessary. Preserve existing limits until
measured/reviewed changes address the costs they bound.

## Acceptance checks

Exact cold/warm canonical result parity; same-head warm refresh downloads no proof
chunks; adding a few records downloads less than the full proof at a representative
size; tampered/missing/swapped/stale pieces never replace CURRENT; origin/token/
resource bounds preserved; old clients can still use complete bundles. Report
first-load, warm transfer and full verification costs separately.

## Pre-implementation design finding

The initial 64-bucket proposal may miss the 25% warm-transfer target. Even ten
new records plus their ten receipts introduce at least twenty new blobs. Under
uniform hash prefixes, twenty objects touch an expected `1-(63/64)^20`, or 27%,
of buckets, before commits and changed trees. This is an analytical warning, not
a measured byte ratio: bucket sizes differ and separate gzip streams can lose
compression compared with the complete bundle. Compare both bucket counts,
manifest overhead, requests, and compressed bytes against actual proof objects.
Do not silently redefine the denominator as the larger sum of chunk sizes.

Hosted old-proof reuse should be evaluated first because it can reduce API calls
without depending on the chunk format. It must retain API fallback for missing
objects, separately authenticate current main, and never make stale proof bytes
authoritative. Track the objects touched by current validation before export.
