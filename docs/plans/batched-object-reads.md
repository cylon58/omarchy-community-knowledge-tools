# Bounded authenticated object batching

## Goal and authority

The exact 500-record cold graph exhausted 512 HTTP requests. Test an authenticated
hosted-read accelerator, not a new authority model. Keep the API-anchored current
head, canonical validation, receipt authentication, offline proof replay and all
existing resource limits. Anonymous clients continue using the complete proof.
No production deployment, paid calls or public synthetic contributions in this task.

## Task 1: implementation and bounded measurement

Own `omarchy_knowledge/github_native.py`, a small new object-batch codec module if
helpful, focused new tests, narrow native-fixture query support in
`experiments/growth/gates.py`, and a new `experiments/growth/batched_reads.py`.
Preserve all saved reports. Do not edit schemas, policy, workflows or client skill.

1. Add an internal read-only fixed GraphQL query for validated exact object OIDs in
   the fixed deployment repository. No caller query, revision expression, host or
   token source. Only adapters with an explicit read token use it; no new option
   required from users. Preserve the existing fixed mutation path separately.
2. Reconstruct trees using strict base64 raw names, supported integer modes,
   type/mode correspondence, bounded entries, unique valid names and Git sorting.
   Require exact typed Git hash. Blob text must be nonbinary, untruncated, non-null
   UTF-8, exact byte size and typed hash. Keep REST commit reconstruction unchanged.
   Hash labels alone never authorize an object. Validate a whole batch before
   installing any of it. Keep actual blob-size checks even though tree size
   metadata is not covered by the tree hash.
3. Warm missing tree frontiers in bounded batches (initial default eight, hard
   maximum sixteen); gather current relevant blobs and prefetch in size-budgeted
   batches of at most32 (worst-case JSON escaping plus framing below1MiB). Then
   gather receipt-referenced historical commits using REST and batch their trees.
   Prefetch must not consume additional logical validation visits or mark an
   unused object touched. Limit prefetch unique objects to5000, raw bytes20MiB,
   depth8 and tree entry bounds; no unlimited BFS. Existing offline validation
   still enforces its visits/deadline and must succeed without object network.
4. GraphQL uses the same adapter HTTP512calls/32MiB/180second budget and1MiB
   response limit. Add192 GraphQL requests and192 reported points as additional
   per-adapter ceilings; reject missing/malformed cost/remaining metadata and stop
   acceleration when remaining points<100. These bounds do not promise that the
   hourly provider quota supports arbitrary arrival rates. Report counts/cost.
5. Unsupported blob encodings or unusable/partial GraphQL responses may fall back
   once to existing REST reads, within the original budget. No recursive splitting
   or reset. For a transport failure whose consumed response size is unknown,
   conservatively charge MAX_RESPONSE+1 before fallback; never perform a retry
   with uncharged failed bytes. Stop acceleration for that adapter after a failed
   batch to avoid repeated failing calls. Resource-limit exhaustion must not be
   swallowed or reset. Oversized/null/error responses must not populate cache.
6. Preserve cache seeding/pruning and parsed-tree size behavior. Avoid double
   charging duplicate cached object bytes. Bundle-only paths cannot make requests.
   Do not fetch blobs unrelated to records/provenance merely to warm a tree.

## Test-first verification

Test exact tree/blob reconstruction; mode/name/size/hash/null/truncation/binary/
partial-error rejection; atomic cache insertion; token/origin/query constraints;
shared deadlines/calls/bytes and conservative failed-byte accounting; bounded
fallback; no anonymous acceleration; no bundle-only network; seed parity; adverse
evidence retained. Test malformed rate-limit metadata and exhausted reserve.
Extend the strict native fixture with an independent read-query decoder that
accepts only the production query grammar and renders values from real Git bytes.
Do not derive expected decoded raw bytes from the production decoder under test.

Run focused tests and one-import native service smoke before task review.
Then one bounded exact500-graph cold comparison, not another100-import timing run:
load the saved distributed report with its known SHA256
`9a58c3bbfdc6e045e505b8e1759ba6f6d7b8943dac8a5e723079c1cff1b3f92a`, reconstruct
using the postmortem helper and require all400head comparisons. Do not weaken the
old postmortem's historical source checks; the new candidate report records its
own code hashes and original report hash. Bound overall600seconds and cold phase
120seconds. Run the real native cold reader, export/replay proof, compare complete
canonical data with a bounded local reference (normalize receipt ordering only).
Capture failure, calls, bytes, GraphQL points, proof bytes/hash, object/visit count,
elapsed time and adverse evidence. Newly created outputs only; sanitize paths and
errors; no credentials/network. Preserve failures too. A passing cold comparison
is not the full admission growth gate; root will schedule full gates later.

## Tradeoffs and follow-up

The optional accelerator adds decoder and quota-handling complexity. GraphQL
availability/rate limits can still force unavailable recovery; fallback is bounded,
not guaranteed. A subsequent real read-only compatibility check and reviewed pilot
are required before deployment. No claim of large-scale readiness from emulation.
Historical tree growth, full verification CPU, fairness and warm client transfer
remain separate gates. Tests and report become public; restricted provider data
and private machine facts do not.
