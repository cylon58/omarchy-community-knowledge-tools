# Native deployment contract

Deployment state: production still uses toolkit/policy revision
`9720575ed4d73b19549ef118f442cf611e009526`. The pilot has completed a manual
upgrade to `1a15dceac62520b6f26d634f93261630f89a548a`; unattended new-release
scheduling remains a promotion gate. See the [rollout evidence](testing/growth-pilot-rollout.md).
The baseline queue description below therefore remains relevant to production.
See [candidate changes](#growth-candidate-not-yet-deployed) before rendering this
branch; local rendering alone does not change either hosted service.

The renderer is local and requires the full immutable public toolkit and reviewed
policy commit IDs. Publish the sanitized toolkit first; the public SHA cannot be
the development commit's guessed self-reference. Use the resulting actual IDs:

```sh
python -m omarchy_knowledge.deployment --deployment pilot \
  --toolkit-revision "$PUBLIC_TOOLKIT_SHA" --policy-revision "$REVIEWED_POLICY_SHA" \
  --output rendered-pilot
```

The operator supplies both variables from reviewed published commits. The command
rejects branch names, short revisions and placeholder words. It emits
`intake.yml`, `reconcile.yml`, and `deployment.json`. The workflows use JSON-form
YAML, which preserves string/event keys without a YAML library. Review/install
the two workflow files under the selected data repository's `.github/workflows/`;
publish `deployment.json` for clients. Rendering does not deploy anything.
Use `--deployment production` only after pilot proof. There is no arbitrary
repository argument. The old `deployment/admission.disabled.json` is a retired
App/merge-queue example, not the native service configuration.

| Job | Permissions | Trusted input/output |
| --- | --- | --- |
| plan | contents read, pull-requests read | fixed identity/event/ref, one bounded plan artifact |
| publish | contents write, pull-requests read | exact same-run plan; reconcile, independently revalidate, one CAS import |
| build | contents read | exact same-run status; fresh canonical immutable objects; bounded regular site files |
| pages | contents read, pages write, id-token write | exact same-run Pages artifact; main-only github-pages environment |

Workflow defaults are contents read. Native job tokens are passed only to the
trusted service step; no personal tokens, model providers, cloud keys or secrets
are configured. Dependencies are provisioned before that environment variable
exists. All checkout/setup/upload/download/deploy actions are official immutable
commit pins. The trusted toolkit checkout is a full reviewed SHA with persisted
credentials disabled. No PR head, merge checkout, data repository module, record
URL, mutable cache or fork artifact is executed.

The service runtime is Python 3.13 on `ubuntu-24.04`, isolated in a fresh venv.
It installs binary wheels with dependency solving disabled for exactly
jsonschema 4.26.0, attrs 26.1.0, jsonschema-specifications 2025.9.1,
referencing 0.37.0 and rpds-py 2026.6.3. Their Python requirements support 3.13.
Referencing's additional typing-extensions dependency applies only below 3.13.
The toolkit's ordinary Python 3.11 installation/CI remains separate. These version
pins trust PyPI, package publishers and GitHub's runner/tool distribution; they are
not vendored wheels or an independent package signature system. Review upgrades.

The read-only build job checks Ubuntu 24.04, provisions the official Ubuntu
`makepkg` and `libzstd1` packages, prints their installed versions, and verifies
`/usr/bin/vercmp` with epoch and pkgrel comparisons. Provisioning has no job token
in its environment. Only the comparator and Zstandard library are used; installing
makepkg is not permission to execute PKGBUILDs. The ordinary validation job uses
the same prerequisites. Unsupported local comparators/libraries produce unknown.

Both workflow entrypoints require exact numeric/name identity, main, and allowed
events. Python checks the event again before API use. PR-target accepts opened,
synchronize and reopened for main; reconciliation accepts schedule/manual on main.
Each job has a 15-minute timeout. Both entrypoints use the same workflow-level
concurrency group, `cancel-in-progress: false`, and `queue: max` (at most 100 pending
runs; see [GitHub concurrency](https://docs.github.com/en/actions/concepts/workflows-and-actions/concurrency)).
This avoids replacing the single default pending run, but is still a bounded queue.

The schedule is minute 17 hourly. A run examines at most 200 newest open PRs,
rotates at most 20 preparation attempts, and admits at most one snapshot. Invalid
PRs do not stop later candidates within the window. Every import starts with
reconciliation; retry/pending stops this run. The writer independently re-creates
the plan and uses the existing expected-head CAS. Base/head races safely retry
on a later run. Recovery covers only 100 recent first-parent commits.

Plans are at most 1 MiB, statuses 64 KiB, sites 32 MiB. Artifact names include
the exact run ID and attempt; download uses only the current run and exact name,
never a caller-selected run, wildcard, or cross-run token. Retention is one day.
The static builder emits only generated ordinary files in an empty safe directory
before the pinned Pages packager runs. Pages receives the exact same-run artifact
name and needs a main-only deployment environment. Native token commits do not
trigger ordinary push workflows, so publication is explicit in this run.

The 4096-entry/16-MiB corpus ceiling is a validation bound, not a throughput
promise. Each API adapter instance permits at most 512 calls, 32 MiB of responses,
180 seconds, and a 20-MiB verified-object cache. The hosted builder must retrieve record
blobs, current trees and receipt-linked historical trees/commits; pending selection
and recovery also inspect up to 100 commits. Many small imports or deep trees can
exhaust these limits well below 4096 records. Shared object caching helps but does
not remove the cost. The anonymous CLI instead uses two canonical API reads and a
bounded static proof bundle, so its canonical API request count does not grow per
record or import. This does not remove hosted API/resource ceilings or client
object/byte limits, and upstream refresh requests are separate. Safe status reports
unavailable/scan-truncated rather than claiming a complete fresh result. The native
unavailable outcome intentionally does not echo remote error bodies and does not
distinguish every quota, timeout or unsupported-object cause. Hosted capacity must
be measured with the pilot before growth; the service is not unlimited maintenance.

The site contains `index.json`, `records.jsonl`, the compatible snapshot
`manifest.json`, `canonical.json` (receipt/source/upstream envelope), `status.json`,
escaped `index.html`, and `canonical-objects.bundle` (raw Git-object proof).
The bundle is deterministic gzip-compressed typed binary data, not an archive to
extract: at most 16 MiB compressed, 24 MiB decoded, 5,000 objects and 20 MiB of raw
object bytes. Individual commits/blobs are at most 64 KiB; trees at most 1 MiB.
`distribution.json` binds each component's SHA-256 and
size, plus repository/tree/data/toolkit/policy and timestamps. Its own digest is
returned by `build_site`; no manifest claims to hash itself. The whole action
artifact also has GitHub's same-run artifact boundary. An empty ledger produces
valid empty arrays/JSONL and zero receipt coverage. No public query fetch is needed
for the HTML's ordinary browser Find or data links.

The CLI `sync` authenticates the fixed GitHub API repository/main boundary, then
hash-verifies a static raw-Git-object proof bundle against that exact main revision.
The bundle is transport, not an authority source: it must contain the current
objects and the historical objects required to authenticate receipts. It adds no
application server, secret, or executable contribution path. If main advances
before Pages has deployed the matching bundle, sync fails closed and retains the
previous cache; retry after the successful Pages build. There is no fallback that
silently trusts static assertions or consumes dozens of anonymous API calls.
Configuration pins label
the reviewed matching installed client; arbitrary configuration does not prove a
different installed executable's revision. See [canonical trust](security.md#canonical-cache-trust).
There is no static-JSON authority shortcut. Sync and build independently invoke
the reviewed [upstream refresh](resolution.md) after validating canonical events.
They replace the entire upstream envelope, including on fresh outages. Search and
explain consume only observations in a locally sealed canonical cache and reject
changed authority/provider policy. The low-level ledger reader still returns a
`not-refreshed` hook before its caller runs refresh. Static files and ordinary
imports cannot assert this authority. Cached queries perform no network requests.

Safe run/site statuses expose PR number, H, accepted commit, head-changed flag,
and accepted/receipt-pending/retry/rejected/unavailable state. They never print raw
rejected values. PRs intentionally remain open. A failing build/deploy leaves the
prior site's generation timestamp unchanged; consult Actions for current failures.
Disabling both workflows and Pages pauses the service; see [recovery](recovery.md).

## Growth release (pilot only; production promotion pending)

The candidate replaces scheduled newest-200 selection with a persisted oldest-first
all-state traversal. Each scan is bounded by ten page fetches, 200 returned rows and
20 candidate evaluations, and stops at its first plan. Closed entries still consume
the traversal budget. A saved cursor is a scheduling hint, never admission evidence.
Direct events remain the fast path. Unknown prior public state withholds publication;
only recognized legacy state permits scheduled bootstrap. Writer outcomes and a
successful publication govern whether proposed progress becomes public. See the
[fair-intake design](plans/fair-intake-design.md) and its
[test record](testing/fair-intake.md) for the matrix and unresolved review findings.

Reviewed candidate transport also supports an optional single-predecessor update
pack. Clients still authenticate repository identity and current main, reconstruct
the exact Git objects and replay canonical validation. A matching cached base can
save proof-transfer bytes; a missed intermediate publication falls back to the
full proof. This does not eliminate validation CPU work or raise resource caps.
The publisher emits the optional manifest/pack together or omits both. See the
[actual-client measurements](testing/update-pack-production.md), including the
slower local matching-update timing despite its smaller transfer.

Health reporting and the final complete pipeline gate remain pending. None of these
candidate results demonstrates current hosted throughput or reliable hourly cadence.
