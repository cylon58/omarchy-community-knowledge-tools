# Admission and upstream facts

The offline checker, native coordinator, pinned workflow renderer, static builder
and canonical API sync are implemented. Local tests do not prove production
activation; actual deployed workflows and live operational evidence are separate.
See [native deployment](deployment.md). The older `deployment/admission.disabled.json` describes the
disabled App/merge-queue deployment. The native protocol below replaces that
assumption for the owner-controlled personal repositories. The project does not
claim Omarchy endorsement. Maintainer authority is empty by default.

## Native snapshot import

The trusted workflow runs `python -m omarchy_knowledge.coordinator` with a command
(`prepare`, `publish`, `reconcile`, or `pending`), explicit immutable
`--policy-revision` and `--toolkit-revision`, and `--deployment production`.
`prepare --pull-request N` emits a bounded plan; `publish --plan FILE` revalidates
it independently. `reconcile` repairs receipts from committed import metadata.
`pending` returns at most 20 unimported snapshot candidates, `scanned`, and
`scan_truncated`; it examines at most 200 newest open PRs. The separate PR event
must handle candidates outside that scheduled window.
The Python `pending(..., limit=200)` option lets the bounded service rotate its
20 preparation attempts across that same window, avoiding starvation by a fixed
prefix of invalid PRs. It does not extend the scan beyond 200.
Completed snapshots are recognized from canonical v2 receipts even after their
import commits leave the 100-commit recovery window. Receipt source bindings,
accepted commit metadata, exact current/historical record bytes and deterministic
receipt paths are checked before suppressing a pending snapshot. Recent import
metadata also recognizes imports whose receipt repair is still pending. A new
head on an already imported PR remains new work; v1 receipts lack the source head
required for this deduplication.

Only two compiled deployment identities are supported: production
`cylon58/omarchy-community-knowledge` (1373429914), and the isolated operational
pilot `cylon58/omarchy-community-knowledge-pilot` (1373467908). The optional
`--deployment pilot` selects the latter; neither contributor JSON nor repository
names in PR content can choose a destination. The deployed workflow must guard
its own numeric repository, event and default ref and select these arguments
from trusted configuration. The coordinator is not an event authorization layer.

The Python contract is `Policy(policy_revision, toolkit_revision, deployment)`,
`prepare(api, policy, pull_request)`, `publish(api, policy, plan)`,
`reconcile(api, policy)` and `pending(api, policy)`. Use `GitHubRead` in a read-only
job and `GitHubWriter` in the serialized writer job. Both accept only the compiled
deployment selector. The CLI consumes `GITHUB_TOKEN`, removes it from the parent
environment, and supplies it only to the fixed HTTPS boundary. A read job needs
contents read; the writer needs contents write. No PR write authority is needed.

Prepare binds API-observed repository, PR and account IDs, base B, source head H,
the GitHub test merge with exactly parents `[B,H]`, evaluated tree T, exact file
bytes/blob OIDs, complete corpus digest and policy/toolkit revisions. A missing,
pending or stale test merge is unavailable; no locally guessed merge is accepted.
The writer re-creates this plan and proves the exact additions applied to B
construct T before calling GraphQL `createCommitOnBranch(expectedHeadOid: B)`.
The mutation contains fixed commit text and typed metadata, never PR titles,
bodies or branch names. Its returned parent/tree are checked as an audit.
GitHub's expected-head primitive rejects competing base updates atomically;
`force:false` and the ordinary PR merge endpoint are not used.
[GitHub commit mutation](https://docs.github.com/en/graphql/reference/commits#createcommitonbranch).

This imports snapshot H as a single-parent commit, rather than performing a native
PR merge. PRs remain open: a separate head check and close operation cannot be
atomic with the contributor's next push. Results expose `status` (`accepted`,
`receipt-pending`, `retry`), `accepted_commit_oid`, `head_changed`,
`source_pull_request` and `source_head` for downstream status display. `accepted` means
that snapshot and its receipts exist; it makes no claim that the PR's latest head
was merged. CLI exit 2 is retry/pending, never success. No rejected source text or
remote error bodies are printed.

Import commit metadata persists the original PR/account/head, B/T, additions and
policy/toolkit identities atomically with the records. This is the recovery
journal, under the trusted canonical-history boundary. It contains no machine
fingerprint. Receipts are separate additions, with deterministic UUIDs derived
from public repository/accepted commit/record identities. A timeout is inspected
against immutable history before any retry; a failed receipt is repaired without
re-reading today's PR author or head. Existing mismatched receipts fail closed.
Recovery scans the latest 100 first-parent commits and returns `complete` only
for that bounded window; a gap older than that needs explicit operator recovery.
Run reconciliation before any new import; any `retry` or `receipt-pending` halts
further imports until repaired, preventing a pending journal entry from aging out
through later automatic writes. Old policy/toolkit values are
retained as historical source facts when repairing after a toolkit upgrade.

Receipt v2 uses `source.method: coordinator-import`, `accepted_commit_oid`,
`head_commit_oid`, `head_repository_id`, `record_blob_oid`, source repository/PR
and toolkit revision. It binds exact accepted blob and semantic record digest.
The explicit `CoordinatorImportGrant` is separate from `BotAuthorityGrant` and
never accepted from CLI JSON. V2 additions require that native contract; v1
receipts remain readable with their historical `merge_commit_oid`. Full resulting
corpus validation runs before each receipt write. A missing receipt contributes
no authenticated evidence. Accounts do not establish people or machines; imported
reports retain their original evidence semantics.

### Retrieval and resource ceilings

The adapter fetches bounded nonrecursive GitHub object API responses, not forks
or packfiles. It reconstructs and hashes raw trees/blobs/commits before use.
Signed commits use exact signature/payload reconstruction; unsigned commits try
bounded quarter-hour UTC offsets because the API normalizes dates. Common equal
offsets are tried first. Extra headers, unusual encodings/signature layouts or
historical non-quarter-hour offsets that cannot reproduce the hash are unavailable.
This is an explicit supported-object restriction, never permission to trust the
API's claimed SHA without verification. The SHA-1 GitHub object format is used.
[GitHub Git database API](https://docs.github.com/en/rest/git).

HTTP allows only fixed `api.github.com` GET endpoint grammars and the one fixed
GraphQL mutation. Redirects, proxies, netrc, arbitrary hosts and generic queries
are not exposed. Scoped job tokens authenticate fixed GETs as well as CAS to avoid
anonymous API throughput limits. The trusted coordinator holds the token in
memory; the isolated network worker receives it on stdin, never argv/environment
or logs. No candidate config, module, hook, filter, template, shell command or
process is loaded. The worker uses isolated Python, an explicit trusted module
path and a two-variable environment. Anonymous reads pass no token at all.
[GitHub token scope](https://docs.github.com/en/actions/concepts/security/github_token).

Per invocation ceilings: 512 HTTP calls, 32 MiB response bytes, 180 seconds total;
1 MiB per response/plan/request, 15 seconds per HTTP exchange including DNS, and
3 seconds per unsigned commit reconstruction. Cached objects total at most 20 MiB.
Object retrieval warms the verified cache before the offline check's 20-second
budget. Existing 4096-entry/16-MiB corpus, 64-KiB blob and 10-addition admission
limits remain. The transport performs no automatic mutation retry. Every process
is killed and reaped on cancellation/timeout. These ceilings and GitHub rate
limits can make larger corpora unavailable; no unlimited throughput is promised.

## Exact-tree check

```sh
omarchy-knowledge admission-check --git-dir /trusted/local/objects.git \
  --request /trusted/local/request.json --output /trusted/local/report.json
```

The request has exactly these fields (all OIDs are full lowercase 40- or 64-digit
Git identities; a request uses one object algorithm):

```json
{
  "repository_id": 123,
  "subject_kind": "merge-group",
  "base_commit_oid": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "head_commit_oid": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
  "evaluated_commit_oid": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
  "expected_tree_oid": "cccccccccccccccccccccccccccccccccccccccc",
  "policy_revision": "dddddddddddddddddddddddddddddddddddddddd",
  "profile": "community"
}
```

These are illustrative identities for the offline checker's supported request
shape, not the native service's deployment configuration. The implemented native
coordinator creates pull-head requests from authenticated repository/ref
observations and the deployed immutable policy revision. Record content must never select the policy
or supply its own authority grant. `validator_revision` identifies the v1 validator
contract; `policy_revision` binds the independently pinned toolkit/policy revision.

Exit 0 is local content acceptance; 1 is deterministic rejection; 2 is
indeterminate. Neither 1 nor 2 permits a success check. Reports contain bound
identities, a digest over canonical corpus paths/blob OIDs, bounded additions, and
fixed diagnostic codes. They never contain rejected prose, file paths, or exception
messages. A result is not an authenticated certificate or permission to merge.

`check_tree(TreeCandidateV1, GitObjectReader, trusted_bot_grant=None)` compares the
entire base/evaluated tree, checks expected tree identity, and validates the full
candidate corpus with `knowledge.validate_corpus`. Records are additions only,
ordinary 100644 blobs, exact canonical UUIDv4 filenames, with matching body ID and
record type. Changes, deletions, modes, symlinks, submodules, LFS, path ambiguity,
mixed lanes and corpus/provenance reference failures reject. Count/size/depth/time
limits and unavailable/corrupt objects are indeterminate.

Admission additionally denies `DANGEROUS_CONTENT` when the separate
`dangerous_content_flags(record)` lint flags obvious remote-fetch/interpreter
pipelines or substitutions, encoded/eval execution, recursive deletion of broad
filesystem targets, or destructive block-device commands. It inspects procedure,
rollback, validation and activation text plus backtick snippets in other fields.
It never runs, decodes or expands those strings. Ordinary `sudo pacman` package
commands are not blanket-banned, and instruction injection remains inert data.
Fetch-to-execution patterns include common `&&` download-then-run sequences,
interpreter `-c` substitutions, and `sudo` options with operands such as `-u nobody`.
These are bounded text patterns, not a shell parser or a proof that two filenames
refer to the same object; even unrelated fetch/run examples can require review.

These flags are independent of schema validity and technical truth. Quoted warnings
and historical examples can be false positives. Obfuscated or novel hazards may
evade the narrow patterns, so an unflagged record is not proven safe. Exceptions
require governed review and a reviewed policy change; there is no automatic-lane
bypass or per-record approval flag in this MVP.

Bounds: 1–10 community additions; exactly one bot addition; 64 KiB per canonical
blob; 16 MiB aggregate canonical corpus bytes; 4096 entries in each complete tree
listing; 1 MiB listing/tree object output; 512-character paths; depth 8; JSON depth
64; 5000 Git invocations and 20 seconds per check. Large valid corpora may therefore
remain indeterminate. These are deliberate MVP limits, not bypassable settings.

The object directory, its local config, and any alternates must be created and
owned by trusted coordinator code, never copied from a contributor. Reads clear
inherited Git configuration/environment, disable replacement objects, lazy fetch,
protocols, hooks and fsmonitor, use fixed `/usr/bin/git` argv, and never checkout or
execute candidate content. Local bare-store configuration is trusted input. Every
read commit/tree/blob is rehashed under its Git object type to verify its OID.
The checker does not fetch objects or consume GitHub file-list/compare/tree APIs.

The CLI never accepts bot authority. A trusted embedding may supply
`BotAuthorityGrant`, binding repository, PR, immutable actor account ID/type,
same-repository bot branch identity, exact head/evaluated OIDs, profile, policy and
the entire sorted `(path, blob_oid)` addition set. The caller must authenticate
this grant; constructing the dataclass is not authentication. Both provenance
profiles use Task 2's strict interchange validators. Receipts bind an existing
record's canonical semantic digest and exact historical Git blob/tree; observations
bind an existing upstream-resolution event's semantic digest. Already admitted
base provenance remains part of the trusted coordinator's history, not a new
claim of authenticity by this checker.

## Read-only upstream adapter

`GitHubPublicRead` exposes only pull, tag ref, annotated tag, immutable commit/blob,
release-by-tag and immutable compare reads. Blob reads require a full OID, cap
decoded bytes at 64 KiB (or a smaller requested bound), check encoded size/type,
and verify the Git blob hash before returning inert bytes. Its only repository is
`omacom/omarchy`; its only origin is `https://api.github.com`. It sends anonymous
GET requests without proxy/netrc/cookie/token configuration, rejects all redirects,
caps responses at 1 MiB and uses a 15-second disposable-process deadline including
DNS/header reads. There is a 5-second socket timeout and 10-second body deadline.
Platforms without `fork` fail unavailable. There is no live-read CLI or automatic
network use. Tests inject a connection factory or public snapshot provider.

The API version is explicitly `2026-03-10`, the current supported version verified
against [GitHub's API version documentation](https://docs.github.com/en/rest/about-the-rest-api/api-versions)
on 2026-09-16. No live repository requests were used for the offline tests.

`observe_omarchy(OmarchyProbeV1, provider, now=...)` requires numeric repository
identity, one explicit PR, up to eight explicit tags and four explicit backport
PRs. It preserves source response digests, retrieval times and one-hour freshness.
It confirms repository numeric identity from the PR response before attaching tag
or publication facts. It peels up to four annotated tag objects, retains each
object's immutable OID, response digest and retrieval/freshness timestamps in
`tag_objects`, detects cycles and
rechecks the tag ref after release lookup; moved tags yield unknown. Lightweight
tags are handled directly. Compare ancestry requires a complete bounded page and
consistent immutable IDs; truncated/error/stale observations yield unknown.

The result kind is `upstream-source-facts`, intentionally separate from the trusted
`upstream-resolution` projection interchange. Merge status, tag ancestry and
release draft/prerelease/publication facts are distinct. Explicit backports remain
caller-selected candidate relationships; semantic equivalence remains unknown.
Ancestry is not proof that a fix is still effective after reverts. The adapter
does not claim a globally first fixing release or exhaustive history.

`OmarchyProbeV1.package_queries` accepts at most 24 distinct `PackageQueryV1`
selectors binding package name, channel, architecture and exact requested source
commit. An optional third `observe_omarchy` argument supplies the reviewed
`PackagePublicRead` provider port. No live package provider/extractor is enabled;
without that explicit injection, each selector returns unknown with an
unsupported-provider diagnostic.

`PackageSnapshotV1` is a strict immutable shape binding provider ID, source
repository name/numeric ID, exact package/channel/architecture, metadata and
retrieval timestamps, response digest, and `available|absent|unknown`. Available
requires version/scheme and an exact provider-proven source commit matching the
query; a version string alone is insufficient. Absent means the exact package is
absent from that fresh channel/architecture catalog and must not claim a version
or source commit. Both timestamps must be current, ordered and at most one hour
old. Wrong identity, missing source mappings, stale data and outages return
unknown. Later calls never reuse an earlier favorable snapshot. Per-package facts
do not establish semantic fix effectiveness; the aggregate availability field
remains unknown because no whole-channel completeness claim is made.

The adapter does not read or execute PKGBUILD,
infer availability from a source merge, silently equate patch hashes, detect
semantic reverts, authenticate official linked context, or generate trusted update
advice. A future live provider must be independently reviewed before injection;
returning a dataclass or a JSON authority claim does not authenticate provider code.

## Governed maintainer declarations

`validate_declaration(expected_identity, trusted_api_comment, AuthorityPolicy(),
event_id=..., event_sha256=..., now=...)` returns relevance only. The API snapshot
is a separate trusted input; no Git author, display name, body flag, or supplied
`official` field can prove authority. Its policy maps immutable repository IDs to
immutable GitHub account IDs under a reviewed immutable revision.
Policy repository/account collections must be explicit lists or tuples; strings,
mappings, duplicate repository/account IDs and oversized collections fail closed.

The strict expected identity contains `repository_id`, `pull_request`, `comment_id`,
`actor_account_id`, `body_sha256` and `updated_at`. The observed identity must match
every field exactly and add only `body`, `retrieved_at` and `deleted`. The caller
must have observed the comment in the exact PR/repository, not inferred membership
from its body. Snapshot freshness is at most one hour and future timestamps fail.
The body is strict JSON with `declaration_version: 1`, `kind: relevance`, the exact
`repository_id`, `pull_request`, `event_id`, `event_sha256`, and
`assertion: supports|revokes`. Unknown fields and duplicate JSON keys fail closed.

Edited/deleted comments, changed identity/digest, stale observations, revoked
assertions, and removal from policy invalidate derived support. A future reviewed
upstream refresh integration must reobserve and recompute support on these changes;
this parser cannot discover edits itself. Authenticated support establishes an
assertion of relevance only, never source inclusion or package availability.

## Current deployment and retired design

The native personal-repository deployment described above is the current contract:
fixed production/pilot identities, pinned trusted toolkit, separate read/write
Actions jobs, expected-head snapshot imports, source-bound receipts and explicit
Pages publication. It requires no App, organization merge queue or external bot.
The offline checker retains historical merge-group request support; that support
does not activate or require the retired App/merge-queue architecture.

`deployment/admission.disabled.json` is a marked historical example, not an active
policy or launch instruction. Install the generated native workflows only after
reviewing their actual immutable public toolkit/policy pins and passing the
[hosted activation checklist](launch-checklist.md). Review
[deployment permissions and bounds](deployment.md) and
[pause/recovery instructions](recovery.md) for the current operational contract.
