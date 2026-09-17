# Cold recovery: contingency research

Research date: 2026-09-17. Not implemented or selected. The larger native growth
run is still pending when this note is written; this is not a diagnosis of it.

## Candidate: bounded batched object reads

GitHub supports repository object lookup by exact OID. Fixed aliases and validated
OID variables could request several objects at once, without accepting a supplied
query or revision expression. [Repository schema](https://docs.github.com/en/graphql/reference/repos)

The Git schema exposes raw base64 tree-entry names, mode, type, OID and size.
Blob fields distinguish binary/truncated content and expose byte size and nullable
UTF-8 text. These fields suggest a hash-checked reconstruction accelerator, not a
general raw-object replacement. The integer mode representation needs a small
read-only compatibility probe; nullable entries/text and encoding are potential
availability failures. [Git schema](https://docs.github.com/en/graphql/reference/git)

Our proposed constraints, if this option is tested:

- Keep REST commits; do not assume formatted actor/date fields reproduce every
  raw commit header, signature or encoding.
- Reconstruct tree/blob bytes and require exact typed Git OID equality before
  caching. Only explicit supported modes; validate raw names and existing sizes.
- Blob text must be nonbinary, untruncated, non-null, exact-length UTF-8 and
  hash-matching. Otherwise use bounded REST base64 fallback.
- Reject partial GraphQL errors, wrong types, malformed fields and oversized
  responses. No endless retry/splitting; fallback consumes the original budget.
- Compare tree batches of 8/16 and size-budgeted blob batches, rather than assume
  that batching is faster. Cap aliases, JSON expansion, requests, bytes and time.
- Preserve current repository/ref authentication, canonical/receipt validation,
  anonymous client behavior and all existing resource caps.

GraphQL has its own point/resource limits: Actions' repository token normally has
1,000 points per hour, and requests can time out or return partial results under
resource pressure. Fewer HTTP calls do not establish unlimited provider capacity.
Measure query cost and respect a reserve; any local GraphQL ceiling would be an
additional bound, not an increase to existing ones.
[GitHub query limits](https://docs.github.com/en/graphql/overview/rate-limits-and-query-limits-for-the-graphql-api)

This would be a token-authenticated hosted optimization, not a new requirement for
anonymous users. [Authentication documentation](https://docs.github.com/en/graphql/guides/forming-calls-with-graphql)

## Alternatives and decision gate

An API-anchored full static proof remains the simplest cold **client** transport:
its bytes are untrusted until all hashes and canonical rules pass. Hosted recovery
when that artifact is absent is a separate case. A Git smart-protocol/pack parser
would add configuration, decompression, disk and delta-parsing risks; it is not the
smallest next experiment. An authority checkpoint is a different trust design and
must not be introduced under the name of a cache.

Wait for the actual baseline. If object request volume is the limiting factor,
compare the same immutable graph using batched reads. Require identical bytes and
decisions, unchanged bounds, actual request/response accounting, and negative
encoding/partial-response tests before adoption. Record failed variants too.
