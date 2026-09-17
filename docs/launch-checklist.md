# Hosted activation checklist

This toolkit implements a deployable service; the presence of code or passing
local tests is not evidence of a live hosted run. Complete and record these checks
for the isolated pilot before enabling production.

- Confirm production `cylon58/omarchy-community-knowledge` (1373429914), pilot
  `cylon58/omarchy-community-knowledge-pilot` (1373467908), and toolkit
  `cylon58/omarchy-community-knowledge-tools` (1373429982).
- Publish the reviewed public toolkit snapshot with MIT license and public docs.
  Keep private development notes and scratch outside the public snapshot.
  Record its real immutable SHA and the reviewed immutable policy SHA.
- Render the native workflows using those SHAs. Review every action pin, runtime
  dependency pin, repository/event/ref guard, permission and artifact name.
  Never deploy placeholder pins or a moving toolkit branch.
- Keep data licensed CC BY 4.0 with original-source attribution and the correction/
  optional invitation policy. An empty ledger is valid; never fabricate launch
  records or publish private drafts merely to populate a page.
- Confirm owner-governed workflow/toolkit changes, main history protection where
  available, and pause/recovery authority. The personal-account design trusts the
  owner and GitHub; it does not require an App, PAT, custom bot or self-hosted runner.
- Configure Pages to use Actions and its `github-pages` environment to allow only
  main. Enable BOTH native workflows only after their reviewed installation.
- Prove a real fork PR imports exact H, receives bound receipts, stays open, and
  appears in safe run/site status. Test changed-head/base races, invalid content,
  receipt failure/reconciliation and an empty canonical build.
- Verify one shared `queue: max`, `cancel-in-progress: false` group across both
  entrypoints; verify event/ref rejection, same-run artifact selection and token
  permission boundaries. Neither event delivery nor the 100-pending queue is
  unlimited. Confirm the hourly schedule and manual dispatch fallback.
- Demonstrate Pages deployment from the same run after native token writes;
  do not rely on an ordinary push trigger. Sync via fixed API identity/object
  checks, query offline, and verify age/staleness plus claims-only local imports.
- Confirm private reporting works at the
  [ledger](https://github.com/cylon58/omarchy-community-knowledge/security/advisories/new)
  and [toolkit](https://github.com/cylon58/omarchy-community-knowledge-tools/security/advisories/new)
  security forms; rehearse pausing intake/Pages and governed privacy removal.
  Exposed credentials must be revoked at the provider; copies cannot be recalled.
- Keep upstream authority empty until reviewed numeric identity bindings exist.
  Live upstream refresh is a separate reviewed slice; source facts do not establish
  semantic effectiveness or authorize update advice.
- Run the ordinary toolkit CI on its public commit and preserve run URLs.
  Test optional panel installation only when explicitly requested.

Record actual hosted evidence in the release/operator log. Do not substitute
local fixtures, synthetic reports, generated YAML, or this checklist for hosted
proof. Production has no promise of Omarchy endorsement, indefinite free hosting,
or zero exceptional governance. See [deployment](deployment.md) and
[recovery](recovery.md).

## Growth-readiness before broad promotion

Hosted activation is not a scalability claim. Before promoting the service broadly:

- Meet or explicitly report failures against the predeclared
  [growth gates](growth-readiness.md), including distributed and concentrated evidence.
- Preserve reproducible harnesses, raw measurements, source hashes, corrections and
  failed runs in the [testing notebook](testing/README.md).
- Exercise production request adapters and per-job bounds in local simulations;
  report provider latency, quotas and scheduling separately from simulated results.
- Measure actual unattended scheduled-run cadence and publication age, not only
  the cron expression or the last green run. The [initial live observation](testing/scheduled-service-observation.md)
  found gaps longer than the configured interval; its cause remains unestablished.
- Demonstrate bounded refresh, backlog traversal beyond the newest-200 window,
  adverse-evidence parity and visible stale/unavailable service status.
- Independently review infrastructure changes, run pinned hosted CI, then perform
  a bounded pilot before updating production or the installed toolkit.
- Publish the supported envelope and remaining limits. Never call synthetic
  accounts independent users or describe a finite test as unlimited scalability.

These are pending gates, not boxes checked by the presence of this document.
