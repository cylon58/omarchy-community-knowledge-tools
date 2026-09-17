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

### Safe seed contract to test

Old proof objects are inert cache entries, not an old authority checkpoint. Decode
and hash-check them atomically with the existing size/object bounds; do not repeat
old-head record/schema/receipt validation merely to reuse a correctly hashed blob.
Install only a bounded seed that leaves explicit room for new objects. Oversized
or malformed seeds are ignored, not partially trusted.

Raw Git trees do not encode blob sizes. The current tree decoder obtains sizes
from cached blobs and can memoize `-1` when one is absent. A partial seed therefore
needs special handling: omit any seeded tree whose direct blob children are not
all present, so normal API tree retrieval supplies sizes. Never preserve a parsed
blob entry with unknown size and assume a later blob fetch repairs it. Missing
child subtrees may be fetched normally. Add an explicit regression for this case.

Seed loading must not mark objects as used by the current graph. Track current
commit/tree/blob accesses, including cache hits, and export only that set. Replay
the pruned output with object-network reads forbidden to establish complete proof
and record/receipt parity. Any fallback shares the original absolute deadline and
API call/byte budget; constructing a new adapter must not reset those limits.

### Staged format experiment

First obtain one successful 500-record/100-import graph and a successor with ten
new records and receipts. Compare complete gzip, 64/256 prefix chunks, per-object
gzip and a best-case update pack. Measure actual required objects from canonical
validation, not all objects retained by Git history. Report manifest plus changed
bytes relative to the complete successor gzip, changed requests, total published
bytes, decode/validation cost, and hosted cold versus seeded API calls. Same-head
reuse should transfer no proof chunks. Preserve failed formats in the notebook.

Reject poor formats before repeating expensive fixtures. For a viable format,
expand deterministic content/UUID variation rather than declaring capacity from
one favorable hash distribution. Keep the existing one-file cold download. Do not
raise resource limits or silently substitute a larger denominator to pass the
25% target. Security cases include missing/tampered/swapped chunks, stale head,
partial seed, cache pressure, interrupted refresh, and unavailable Pages.
