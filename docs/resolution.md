# Live upstream evidence and supplier declarations

`sync` and the scheduled build automatically refresh the canonical ledger's
resolution events. Search/explain uses the resulting locally sealed observations;
public JSON, source-fact output, and contributor trust flags cannot supply advice.
The service does not execute source code, recipes, package contents or declarations.
No Omarchy maintainer account is configured initially. Real observed facts remain
useful, but their unauthenticated resolution claims yield `investigate`.

## Supported scope and evidence

The provider fixes GitHub repository `omacom/omarchy`, numeric ID **994093166**,
and package host `pkgs.omarchy.org`. Recognized canonical event links identify PRs
in that repository; historical `basecamp/omarchy` links are aliases only after
numeric canonical identity verification. Other links are displayed as community
references and never fetched by this provider.

Only `omarchy` and `omarchy-settings`, stable/rc, x86_64 are supported. Configuration
was reviewed at Omarchy commit
[`9c5482c58dbe4974de337450754885083c91eada`](https://github.com/omacom/omarchy/tree/9c5482c58dbe4974de337450754885083c91eada/default/pacman):
stable selects `/stable/$arch`, rc selects `/rc/$arch`;
[`omarchy-channel-set`](https://github.com/omacom/omarchy/blob/9c5482c58dbe4974de337450754885083c91eada/bin/omarchy-channel-set)
selects the release package pair. The packaging repository's
[`helpers/paths.sh`](https://github.com/omacom/omarchy-pkgs/blob/b8cdc38609d400a9f27e188c1ac0b6d8d33f118a/helpers/paths.sh)
defaults its published architecture list to x86_64. These are reviewed configuration
pins, not executed recipes or mutable remote policy. New mappings need code review.
ARM, edge/dev, kernels, third-party packages and plugins remain unsupported here.

The source facts distinguish merged PRs, commit ancestry, publication, observed
catalog contents, and semantic repair. Tags are peeled and re-read to detect a
move; drafts, future publication and stable prereleases cannot support advice.
Numeric release ordering uses `vMAJOR.MINOR.PATCH[rcN]`. `earliest_observed_release`
means source ancestry among examined tags. `first_published_containing_release`
requires complete bounded release enumeration and known negative ancestry before
that version within its major/minor line. Neither claims first semantic repair.
Incomplete comparisons and unproven backport equivalence remain unknown. An
authenticated supplier declaration can report a backport fixed without claiming
the original merge is an ancestor or its patch is semantically equivalent.

Catalog facts bind retrieval time, response digest, package name/version, package
digest as reported by the catalog, channel and architecture. Package `any`
architecture is preserved separately from the catalog's x86_64 scope. Build times
can be old while the catalog was freshly retrieved. These are supplier catalog
observations, not signature verification or independently proven compilation.
The existing exact-source `PackageSnapshotV1` contract is unchanged.

`inclusion_basis: authenticated-maintainer-release-assertion` means a governed
upstream account reports the scoped fix shipped, independently joined with a
currently published release/tag and actual channel packages. It does not certify
semantic correctness. A supported assertion must exactly match the event digest
and every predicate in its single AND package alternative, including upper bounds.
Additional alternatives, non-package predicates and broader community boundaries
remain unsupported. Arch comparisons use `/usr/bin/vercmp`, including epoch and
pkgrel; there is no lexical/SemVer fallback.

## Governance before enabling advice

`omarchy_knowledge.resolution.AUTHORITY` is an empty `AuthorityPolicy`. It cannot be
changed by a record, deployment JSON, CLI flag or fetched declaration. Enabling it
requires an explicit Omarchy-approved authority decision, independent verification
of exact numeric account/repository IDs, review by the toolkit policy owner, an
immutable approval-policy revision and a new reviewed toolkit publication. Record
the approval's public source, scope and revocation process with that policy change.
Do not infer authority from usernames, Git authors, PR ownership, collaborator
association, account popularity or an actor's own declaration.

Authority removal changes the policy digest and invalidates earlier sealed advice,
even before its ordinary expiry. Remove a compromised actor through the same
governed process and publish the updated toolkit. Clients must update trusted code
and resync; offline old installations cannot know a policy changed remotely.
No upstream outreach, authority grant or deployment is performed by these docs.

## Copyable declaration procedure for an authorized maintainer

1. Inspect the exact canonical event with `omarchy-knowledge show EVENT_UUID --cache
   CACHE`; inspect its targeted case/change and supported `fixed_in` predicates.
   Compute its semantic SHA-256 using `projections.record_digest(record)`, not the
   Git blob ID or a hash of pretty-printed JSON.
2. On the official PR linked by that event, post one issue comment whose entire
   body is the JSON below, replacing the illustrative UUID, digest, PR and release/
   package conditions with reviewed values. The posting account must already have
   its numeric ID bound by the governed policy. This toolkit does not post it.
3. Keep the declaration current. To revoke, retain the event/digest/scope fields
   and change `assertion` to `revokes`. Correct an assertion by editing its comment;
   it will be re-read and its new body hash/update timestamp bound on the next sync.

Illustrative data only; this is not a real event, assertion or authority grant:

```json
{
  "declaration_version": 2,
  "kind": "resolution",
  "repository_id": "994093166",
  "pull_request": 7,
  "event_id": "11111111-1111-4111-8111-111111111111",
  "event_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "assertion": "supports",
  "release": {"tag": "v4.0.4", "fix_state": "included"},
  "fixed_packages": [
    {"name": "omarchy", "scheme": "arch", "minimum_version": "4.0.4-1", "maximum_exclusive": null},
    {"name": "omarchy-settings", "scheme": "arch", "minimum_version": "4.0.4-1", "maximum_exclusive": null}
  ],
  "channels": ["stable"],
  "architectures": ["x86_64"],
  "migration": "unknown",
  "activation": "reboot"
}
```

All fields are mandatory, including for revocation. Packages are an AND requirement;
one or two supported package names may be specified. The event must use exactly
the same minimum and optional exclusive maximum, channel and architecture.
`migration` is `yes`, `no` or `unknown`; `activation` is `none`, `relogin`, `reboot`,
`service-restart`, `manual` or `unknown`. The outer observation binds the exact PR,
comment ID, API-observed numeric actor, body SHA-256, update/retrieval timestamps
and authority revision. No actor identity supplied in body text grants authority.
Existing relevance-v1 support remains compatible, but adds no package conditions.

Complete current comment scans are required. Deletion of supporting comments,
edits, extant revocations, conflicting or malformed authorized assertions for the
event, missing publication, partial package availability and fresh source failures
prevent positive advice. Supporting comments are independently re-read before use.
This is a current-object policy: stateless refresh cannot remember a deleted
revocation when a separate older support still exists. Administrators remain
trusted; durable revocation requires removing authority or retaining the revocation.

## Freshness, bounds and local action

The current envelope replaces the previous envelope wholesale, including unknown
outage results. It is a current cache, not a durable observation audit archive;
canonical Git record/receipt history remains separate. Cached query commands are
offline. Observations expire after at most one hour, bounded by the earliest
relevant source freshness. A failed canonical ledger read cannot replace the
cache; its older upstream observations still expire. Failed site builds/deployments
retain the previous generation timestamp, not a claim of current health.

Each run scans at most eight resolution events, forty release objects (two pages),
eight candidate tags per event and sixty PR comments (two pages). Excess events,
pages or incomplete facts are disclosed in `scan.incomplete` and per-event scans.
No evidence for skipped events is carried forward. Every GitHub reader has a
96-call/120-second budget; individual anonymous GETs allow at most 1 MiB and a
15-second worker deadline. Catalogs are fixed-origin GETs with no redirects,
2 MiB compressed, 8 MiB expanded, 2048 entries, 64 KiB per entry, a three-second
parse check and a 15-second worker deadline. Tar data is inspected in memory;
symlinks, traversal and filesystem extraction are rejected. Zstandard decoding
uses the bounded destination-buffer [libzstd API](https://facebook.github.io/zstd/zstd_manual.html).
No package binaries are downloaded. Empty event scans are normal and still publish
catalog/release health. API quotas may reduce coverage below these bounds.

GitHub PR reads explicitly request the supported `2022-11-28` contract because
[`2026-03-10` removes `merge_commit_sha`](https://docs.github.com/en/rest/about-the-rest-api/breaking-changes).
GitHub states the previous version stays supported for at least 24 months after
the [March 2026 release](https://github.blog/changelog/2026-03-12-rest-api-version-2026-03-10-is-now-available/).
Review/migrate this contract before March 2028. Missing fields fail unknown.

`prefer-update` requires a known installed package set to move monotonically to
one applicable available target: at least one package is older and no paired
package would be downgraded. Failing an upper bound, an unknown/ambiguous installed
version or a mixed pair requiring a downgrade yields `investigate`, not an update.
The caller must
verify effective package servers, repository precedence, ignored packages and
installed versions; mixed/custom repositories or incomplete local facts require
investigation. It grants no system mutation authority. Unknown migration/activation
does not block proposing an update, but installed versions alone do not prove the
repair is active. Verify migrations/restarts and retest. Retain existing workarounds
until inspecting ownership/later edits and obtaining explicit cleanup approval.
