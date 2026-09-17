# Optional direct update packs: production integration

Status: implementation brief, conditional on the bounded variation result. This
does not authorize deployment. Design authority: [incremental transport](incremental-transport-design.md)
and the user-approved growth milestone. Keep existing full downloads, current
GitHub identity/head anchoring and all canonical/receipt checks. No remote code,
new server, arbitrary origin, credential on Pages, patch chains or cap increases.

## Preflight decisions

The current anonymous full prefill bypasses `_HTTP` request/byte accounting and
uses a fresh15-second subprocess timeout. Do not reuse that unchanged as the
new fallback. All static attempts must share the existing outer HTTP budget.

CURRENT and a separate proof file cannot be replaced simultaneously with the
existing layout. Keep the existing CURRENT payload/seal unchanged. Commit CURRENT
first, then independently atomically update one inert proof sidecar. A crash can
leave a stale/missing sidecar: that loses acceleration, not canonical correctness.
Post-commit cache-write failure must not report an otherwise completed sync failed.

Bind normalized object sets, not gzip bytes. Different runtimes may rebuild
equivalent full proofs with different compression bytes. A digest is a transport
consistency check, never a substitute for Git hashes or canonical validation.

Prefer the existing `object_bundle` codec for pack payloads. It verifies typed raw
objects and requires the declared head commit, but does not require a complete
graph until later validation. New append-only target heads are in the additions.
If a target head is already present in the base, omit the optional pack and use
the full artifact rather than weakening duplicate/addition rules. Administrative
rollbacks or changed validation layouts need not receive incremental acceleration.
Do not install the experimental head-independent chunk parser in production.

## Task 1: inert production delta codec

Create a focused production module and tests, without networking or cache changes.
Provide strict object-set digest, manifest encode/decode, pack generation/application
and retention-validation interfaces. Reuse `object_bundle` for payload compression
and typed-object decoding; reject malformed map entries rather than relying on its
encoder's parsed-cache-key filtering behavior.

Digest framing: domain bytes `OMARCHY-KNOWLEDGE-OBJECT-SET\0\1`, big-endian32-bit
object count, then sorted `(ASCII kind, lowercase40-hex OID)` rows. Each row is the
existing one-byte kind tag,20-byte OID, big-endian32-bit raw length and raw bytes.
Exclude gzip and head; manifest binds the latter separately. Verify every typed
hash, unique key, per-kind limit and existing5000-object/20MiB raw limits.

Manifest v1 has exactly: version, deployment, base_head, target_head,
base_object_set_sha256, target_object_set_sha256, pack_sha256, pack_size, removed.
Use strict JSON, bounded to1MiB, integer version1, fixed deployment enum,
lowercase40/64-hex identities, positive pack size within the existing16MiB compressed
limit, and sorted unique removed `[kind, oid]` pairs within5000. No URLs or paths.
Require distinct heads and pack target head equal the separately expected head.

Generate only absent target objects; require no byte disagreement for common keys.
Apply only with exact base head/set digest, valid removals present in base, additions
absent from base, exact pack size/hash and typed hashes, and resulting target set
digest. All operations are in memory. Return a complete target map; callers still
perform ordinary full canonical replay. Reject missing references there, not by
pretending the manifest authorizes records. Keep all existing object/proof limits.

Retention validation can establish a pack's target consistency without retaining
its old base: target head/set digest match the current full map, every added object
equals its current target entry, removed keys are absent, and all manifest/payload
checks pass. Base fields remain untrusted applicability hints that clients verify.

Tests: deterministic framing/known digest, compressor independence, exact generation/
apply round trip, wrong base/target/deployment, missing/duplicate/extra keys, malformed
JSON, wrong hashes/sizes, invalid removals/additions, truncated/bomb/trailing payload,
existing bounds, rollback omission and retained-pack target mismatch. No production
format is enabled by merely adding the module.

## Task 2: bounded client transport and one proof sidecar

Introduce a fixed-artifact Pages reader, preferably in a focused module rather than
duplicating HTTP workers. Accepted inputs are deployment and a fixed artifact enum
(full proof, update manifest, update pack), never a URL or contributor path. Use
the existing API adapter's calls/bytes/absolute deadline; no redirects, cookies,
proxy/netrc discovery or credentials. Count unsuccessful/partial attempts too;
when a subprocess hides consumed bytes, conservatively charge the artifact bound.

Reserve enough remaining aggregate bytes for the complete full-proof fallback
before attempting optional manifest/pack reads. If that cannot be reserved, skip
the optional path. Two maximum16MiB artifacts plus metadata exceed32MiB, so an
attempted maximum-size pack cannot promise full fallback under unchanged bounds.
Respect remaining time and request limits in worker I/O and subprocess timeout.

Authenticate repository and current main exactly once. Add a private canonical
validation seam for an already authenticated revision; it must retain every
record/corpus/observation/receipt check. Try matching local proof first; same head
may avoid Pages but still runs canonical validation. Otherwise try at most one
manifest/pack assembly, then at most one full fallback. Validate each candidate
using isolated APIObjects with the original outer deadline and unchanged per-proof
bounds; discard failed candidate state before fallback. Never re-anchor to a newer
head mid-attempt, reset HTTP budgets or allow object-network reads during replay.

Store one full reusable proof outside snapshot slots using descriptor-relative
no-follow primitives, exclusive private temporary writes and atomic replacement.
Only save a complete successfully validated map, after CURRENT commits. Treat
missing/corrupt/wrong-base caches as misses. Keep existing cache seals and snapshot
file layouts backward compatible; the sidecar grants no authenticity by itself.

Tests: actual cold/full, warm/update and same-head flows; malformed/missing/stale
manifest/pack, wrong base, canonical replay failure after valid typed hashes,
bounded single fallback, partial worker failures and shared quotas/deadlines,
anonymous headers/fixed origins, cache symlink/nonregular/oversize input, interrupted
refresh retaining CURRENT, and post-CURRENT sidecar failure preserving successful
sync. Preserve old callers through default full behavior and explicit adapter seams.

## Task 3: publisher, retention and end-to-end evidence

Retain the prior validated full proof as a bounded optional source for generating
one predecessor pack. Do not turn old Pages bytes into authority or change hosted
seed eligibility. Generate optional artifacts only after current canonical proof
validation. Publish fixed `canonical-update.json` and `canonical-update.bundle`
alongside the unchanged full proof; include their hashes/sizes in distribution
metadata and the existing total publication cap. Omit optional files when too large.

On unchanged builds, retain the last useful pack only after fixed-origin retrieval
and Task1 target-consistency validation against the exact current full object set.
If unavailable or invalid, omit it and publish the full proof; no no-op replacement,
chain or accumulating archive. All retention reads share the existing job budget.

Extend the independent native fixture to model these exact fixed artifacts, and
test old-status/full-only deployment compatibility. Measure actual new client
transport for a ten-record update at the established base size, including manifest,
pack, fallback and identity requests separately. Require full data/adverse-evidence
parity and the declared25% matching-base transfer target. Same-head needs no proof
downloads; wrong-base must safely take full. Record misses as well as savings.

Run focused tests/review before committing, then integrate this with the final
growth gate and pilot rollout. No public synthetic load. A matching-base benchmark
does not claim savings for cold or infrequently syncing clients. Further growth in
full verification cost remains a separate capacity constraint.
