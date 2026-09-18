# Bounded growth pilot rollout

Status: manual pilot transition and authenticated client recovery passed;
new-release unattended scheduling and production promotion remain pending.

## Reviewed inputs and rollback

Toolkit and separately approved policy revision:
`1a15dceac62520b6f26d634f93261630f89a548a`.
[CI35292848928](https://github.com/cylon58/omarchy-community-knowledge-tools/actions/runs/35292848928)
passed before rollout. Core, workflow and final growth evidence received separate
scoped review. Toolkit main advanced by an ordinary fast-forward from `2250d3c`;
history-protection rules were preserved.

The clean pilot started at `5490d2390db8ff54a0cb86071f4e90f49a04d72b`, using
toolkit/policy `9720575ed4d73b19549ef118f442cf611e009526`. The renderer produced
exactly three files. Independent comparison confirmed that changing the new SHA
back to the old SHA made each byte-identical to its prior version: no permissions,
guards, action/runtime pins, event routing or concurrency changes were introduced
in this rollout. The exact original files remain recoverable from that Git commit.

Reproduction of rendering (creates new files, does not deploy):

```sh
python -m omarchy_knowledge.deployment --deployment pilot \
  --toolkit-revision 1a15dceac62520b6f26d634f93261630f89a548a \
  --policy-revision 1a15dceac62520b6f26d634f93261630f89a548a \
  --output /new/rendered-directory
```

Rendered SHA-256:

```text
6bd3bc01bad513b2481907ae6728d1723d37de8c3918e6dc03961c208fdc8500  deployment.json
f097fe864ce8eb1f56809f81d64f7bf71ccc1dc74b213b5ad7990fd373aed72e  intake.yml
1a1b129d090387f5ed9eefd65ef4807df7103f70b371a9b726803f650a79860c  reconcile.yml
```

## Live sequence

The [pre-upgrade health report](../../experiments/growth/results/growth-pilot-health-before.json)
was healthy: four records/four receipts, matching main/Pages, recognized legacy
format. Scheduled run35291239613 had completed its Pages job at00:29:04UTC on
2026-09-18. This later completion does not erase the earlier stale-schedule result
in the [health notebook](service-health.md).

Both pilot workflows were briefly disabled, and the run list showed no active
writer. Only the three reviewed files were changed in pilot commit
`cb36db69e7231a33cb461af11bc000b22d0791e6`. Reconciliation was re-enabled first;
one full manual dispatch started. No synthetic contribution was created.

[Manual run35293081037](https://github.com/cylon58/omarchy-community-knowledge-pilot/actions/runs/35293081037)
passed plan, publish, build and Pages. Its [plan](../../experiments/growth/results/growth-pilot-manual-plan.json)
recognized legacy state, consumed six PR rows (four closed, two already imported),
found no new eligible work, and completed one traversal. The
[publish envelope](../../experiments/growth/results/growth-pilot-manual-publish.json)
permitted publication of cycle1/progress metadata. Main remained at the workflow
commit: no record or receipt commit was added.

The [published status](../../experiments/growth/results/growth-pilot-status-after-manual.json)
contains the complete current cursor/health/scan trio and build-health v1. Its
canonical builder measured nine requests and17,174 charged bytes; proof size was
5,396 compressed bytes,28objects and13,987 raw bytes. Object-visit usage remains
unknown. These are measurements of this tiny live corpus, not hosted scale proof.

The [post-manual health report](../../experiments/growth/results/growth-pilot-health-after-manual.json)
returned exit0, overallok, full four-record receipt coverage and matching source.
The [authenticated sync](../../experiments/growth/results/growth-pilot-sync-after-manual.json)
also returned exit0 with four records/four receipts and `canonical-api-receipts`
trust. It used the reviewed checkout's console-entry function and a new isolated
cache; the user's installed client was not changed. Both pilot workflows were
then confirmed active again.

## Hosted health watcher

[Manual watcher run35293265135](https://github.com/cylon58/omarchy-community-knowledge-tools/actions/runs/35293265135)
passed on the same toolkit main revision. Its same-run artifact retained both
[production](../../experiments/growth/results/growth-hosted-health-production.json)
and [pilot](../../experiments/growth/results/growth-hosted-health-pilot.json)
reports plus [exit codes](../../experiments/growth/results/growth-hosted-health-exit-codes.json),
both zero. Each report made six bounded anonymous reads. Production still reports
legacy metrics as unknown; pilot reports current measured capacity with object
visits and upstream completeness unknown. This demonstrates hosted installation,
capture, artifact upload and final gating for a successful pair. Synthetic tests
cover failure capture; no artificial live outage was introduced.

## What is not established yet

The health report's recent scheduled-success field still references the old
deployment's scheduled run. A successful manual transition is not evidence of
unattended execution of the new release. Production remains on the old pinned
service, pending a real new-release scheduled pilot publication and evidence review.
The watcher's manual success does not prove its future schedule cadence. No alert
delivery, public intake load, independent users or unlimited capacity is claimed.

If rollback is needed, pause both workflows, preserve run/artifact/source facts,
and restore the three original files in a new commit—not a force push/reset.
Restoring the old service after current-format publication will republish legacy
status and discard cursor telemetry, so that regression requires explicit review.
See [recovery procedures](../recovery.md). No rollback was required for this manual run.
