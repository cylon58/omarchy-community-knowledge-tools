# Trust and security

The ledger accepts attributed claims, not executable instructions or proven truth.
Git administrators retain control; history is neither immutable nor trustless.

## Separate authorities

- Community: add bounded records; propose applicability and resolution claims.
- Trusted toolkit: schema, parsers, adapters, agent instructions and code; governed
  review required. A record cannot change these rules.
- Ingestion service: attests GitHub actor/object identity and accepted blob hashes.
  Git authors, display names and contributor-supplied identity fields do not count.
- Upstream authority: an explicitly governed binding to immutable GitHub actor IDs
  scoped to upstream repositories. No initial claim of Omarchy endorsement.
- Local user: decides system changes and approves exact publication payloads.

These are separate facts. Automated admission does not certify a fix; a merge does
not prove a release; a release does not prove local migrations ran; popularity
does not make a preference required.

## Execution boundary

No ledger command executes community code. Snippets are inert strings. Agents
inspect, explain and adapt them using their normal tools only with the user's
authorization. Distributable executable fixes belong in separately reviewed
plugins/packages/upstream code. Treat sources and model output as untrusted data,
including instructions embedded in log messages, references and issue comments.

Automatic admission separately flags obvious fetch-to-interpreter, encoded/eval,
broad recursive deletion, and block-device destruction patterns without executing
or decoding submitted examples. Benign package-manager commands are not blanket
banned. The matcher has both false positives and evasions; flags deny the automatic
data lane and require governed manual review. No per-record bypass or manual
exception mechanism is implemented in this MVP.

Use fixed argv, parameterized comparisons, escaped/plain-text rendering, bounded
inputs and local-only schema resolution. Never import contributor modules, install
their dependencies, source PKGBUILDs or check out/run their workflows in privileged
jobs. Native `pull_request_target` entrypoints may execute only the pinned trusted
toolkit while treating fork bytes as inert input; they must never check out or
run the contributor's code. Event/ref authorization remains a workflow boundary.

## Public deployment gate

The native coordinator implements bounded API identity/object validation, exact
base CAS imports and separate source-bound receipts. Implementation and offline
tests do not establish deployment. Before activation, verify owner-only repository
writes, protected monotonic history, immutable reviewed workflows/toolkit, scoped
job tokens, default-ref/event checks, serialized writers, pause/recovery controls,
and live positive/negative admission behavior. This personal-account architecture
trusts the owner, trusted workflows and GitHub; it provides no independent check
source against a compromised owner or privileged workflow. It does not require a
custom App or organization merge queue. GitHub tokens are not path-scoped, so the
reviewed coordinator is the canonical-path mutation boundary.

The fixed HTTPS worker may receive the scoped token on stdin for allowlisted GETs
and the one CAS mutation. Its argv and environment contain no credentials. The
trusted parent holds credentials in memory; this is not a claim of process-memory
isolation from trusted coordinator code. Contributor content is never imported,
checked out or executed, and no parser/Git process receives a token. Native import
receipt authority is an explicit typed contract, not a claimed bot identity in
CLI JSON. The accepted commit persists original source bindings for receipt
recovery even if the PR changes later. PRs remain open to avoid a close/head race.
See [protocol and resource bounds](admission.md#native-snapshot-import).

Schedules can fail or stop; stale/missing results block admission, never bypass
it. A separate PR wakeup is required. These are liveness limits, not an SLA.

No routine technical curator is required by the design. Security incidents,
credential expiry, abuse and privacy removals still require occasional governance.
Fail closed on ambiguity and keep cached read access available during outages.

Automatic upstream reasoning remains intentionally incomplete: no merge, tag,
version string, or current machine inventory proves historical applicability,
semantic equivalence, lack of a revert, distribution availability, migration, or
activation. Authority policy and provider authentication must be supplied by a
reviewed upstream adapter; a structurally valid receipt is not self-authenticating.

## Canonical cache trust

`sync` authenticates only the compiled production/pilot numeric repository and
main ref through two reads of GitHub's fixed HTTPS API. A bounded raw-Git-object
bundle from the deployment's fixed Pages location transports the remaining bytes.
The client verifies immutable commit/tree/blob hashes and full corpus/receipt
bindings, anchored to that API-confirmed revision. A bundle cannot choose a different
head, repository, policy, contributor identity, or upstream authority. No archive
is extracted and no downloaded code is executed. GitHub, repository owners,
the reviewed installed toolkit, and the local user are trusted. Static HTTPS JSON
and self-asserted hashes alone cannot establish this provenance.

The bundle download carries no token, follows no redirects, and does not discover
credentials, proxies or arbitrary source URLs. Stale, missing, malformed, oversized
or incomplete proof fails closed before replacing the cache. In particular, a
Pages deployment lag is an availability failure, not permission to accept an old
head as current or bypass receipt authentication. Previously verified cached
queries remain available with their original age. The hosted publisher still reads
canonical objects through its scoped API adapter; it does not consume its own
Pages bundle to generate the next bundle.

After a successful sync, a per-cache random key seals one atomic CURRENT envelope.
The seal binds the snapshot manifest digest (which binds index and records), every
receipt, deployment/repository/ref/commit/tree/toolkit/policy identity, timestamps
and the entire upstream envelope. The key is a private 0600 file under the safely
opened cache root, excluded from snapshots, static exports and status. This is
local provenance bookkeeping, not a GitHub signature and not protection against
arbitrary same-user code that can read the key. It prevents an ordinary imported
JSON flag or forged receipt envelope from becoming authority.

Cache paths/files reject symlinks and use bounded descriptor-relative reads and
atomic pointers. Missing/invalid keys, seals or modified snapshot hashes fail
closed. Ordinary snapshot imports write a plain claims-only pointer, invalidating
canonical provenance even when the imported bytes match a previous snapshot.
Offline canonical receipts persist with source and age/stale disclosure; this is
not a claim of current upstream facts. Every sync replaces the upstream envelope;
no favorable observation is silently carried into a newer unknown result.

## Disposable local search index

The optional local derivation cache uses a separate private key to authenticate a
bounded SQLite byte image. Its seal binds the verified manifest and relevant
installed validator, schema and index code. Source file hashes are checked on every
load; only then may a valid local derivation avoid repeated corpus validation.
Authenticated bytes are deserialized into an in-memory database, not reopened by
path. This is local bookkeeping, not a signature from upstream or protection from
same-user code that can read the key. Missing or invalid derivations fall back to
full validation. No derivation supplies canonical receipts, freshness or release
authority; these remain separately checked and projected for each query.

## Sensitive reports and development

Use the private [ledger](https://github.com/cylon58/omarchy-community-knowledge/security/advisories/new)
or [toolkit](https://github.com/cylon58/omarchy-community-knowledge-tools/security/advisories/new)
security form. Revoke leaked credentials first; pause both intake workflows and
derived publication while governed correction/removal proceeds. Public copies
cannot be recalled. The [recovery guide](recovery.md) describes the process.

Development agents and model providers are not service dependencies. The hosted
service runs deterministic pinned code without model keys. Multiple agent
sessions do not count as independent contributors or evidence.
