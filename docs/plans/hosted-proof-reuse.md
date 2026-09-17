# Hosted immutable proof reuse — implementation brief

Status: queued after recovery optimization and review, not implemented.

## Goal and contract

Reduce repeated hosted GitHub object reads using the preceding static proof as
an optional, inert object cache. Current repository/ref identity and all existing
admission/canonical checks remain authoritative. No resource-limit increases.
See `incremental-transport-design.md` for the seed-size/tree-size hazards.

## Scope

`omarchy_knowledge/github_native.py`, `object_bundle.py`, narrow service wiring,
and dedicated tests. Keep current strict client `load_bundle` behavior intact:
client full-proof reads must still match separately authenticated current main and
must not silently fall back to anonymous per-object requests.

Introduce a distinct hosted seed operation. Download only from fixed deployment
Pages origin, anonymously without redirects, with bounded size/time. Decode the
old head as transport metadata; verify every object hash, type and size. Malformed,
missing or too-large cache falls back to ordinary bounded API retrieval. Do not
use the old head to select current data or accept old source attribution.

Seed only an empty object store, leave explicit capacity reserve, and omit raw
trees whose direct blob sizes cannot be determined from available verified blobs.
Track objects touched by the current traversal separately from loaded seed objects.
Export only touched raw objects and replay the result without object-network reads
before publishing it. Keep one outer deadline and API-call/byte budget; no reset on
fallback. No repeated old-head schema/evidence validation is needed for inert bytes.

Wire the optional acceleration into hosted planning, recovery/publish and build
without accepting an arbitrary source URL or adding credentials to Pages requests.
Check behavior when a read-only planning API becomes a separately scoped writer;
each job must independently verify its current identity and bytes.

## Test-first checks

- Cold unchanged API behavior and strict client bundle-only behavior remain intact.
- Same-head and one-import-newer seed reduce raw GitHub object reads, while exact
  records, receipts, attribution and adverse-evidence results remain identical.
- Missing or unavailable Pages uses bounded cold retrieval, not a new trust mode.
- Tampered/oversized/incomplete seed cannot authorize acceptance or poison memoized
  blob sizes; missing direct blobs trigger ordinary API tree fallback.
- Old unrelated objects are excluded from the published proof; pruned proof replay
  succeeds with all object API methods forbidden.
- Failed seed and fallback share budgets; seed/cache pressure fails closed rather
  than increasing existing limits.
- Update the existing builder-no-Pages test deliberately: this is an availability
  contract change, not an unnoticed test deletion.

Measure cold versus seeded modeled object requests and CPU separately using the
same synthetic graph. Record residual HTTP/quota/whole-history limits. No deployment
until independent security review and bounded pilot evidence.
