# Tested beta envelope and remaining launch gates

This is a bounded evidence statement, not an unlimited-capacity promise.

Current rollout progress: manual pilot transition, authenticated recovery and a
hosted watcher run passed. Production promotion remains pending actual scheduled
execution of the upgraded pilot. See [live evidence](growth-pilot-rollout.md).

## What has been demonstrated locally

- The native admission/reconciliation/build/recovery/search path passed 500
  synthetic records across 100 accepted imports, preserving 500 authenticated
  receipts and 100 adverse reports. Worst warm search was 0.160 seconds in the
  declared three-query set. See [full methods and raw results](native-growth-gates.md).
- The separate actual publisher/client experiment covered a 500→510-record
  update. A matching predecessor transferred 2.98% of the full proof bytes;
  an older or wrong predecessor fell back safely. This is a transfer saving,
  not elimination of full validation CPU or a guarantee for every update.
  See [publisher measurement](update-pack-production.md).
- The pure scanner reached every eligible unchanged head in its stable mixed
  1,000-PR fixture. Guarded service tests demonstrated progress beyond 205 closed
  entries and retention/resume after failed publication. These are not hosted
  arrival-rate, fairness-under-continuous-churn or waiting-time guarantees.
  See [queue evidence](fair-intake.md).
- The concentrated-evidence profile covered 104 records with 100 reports on one
  case, including adverse evidence. That differs from the 100-import distributed
  history; neither substitutes for arbitrary history shapes.

All load was synthetic and local. No synthetic public intake load or independent
community confirmation is claimed. Python 3.11/3.13 CI checks correctness; recorded
local growth timings used Python 3.14.7 and are not hosted latency measurements.

## Limits that still matter

Canonical layout, raw bytes, proof objects, history recovery, per-job API calls,
response bytes and deadlines remain bounded. Record count alone cannot predict
capacity: many small imports or deeper history can exhaust a different bound
first. The 500-record test is a demonstrated history shape, not a safe promise
that any 500 records—or thousands of imports—will fit.

Full proof replay remains necessary. A single-predecessor update pack does not
accelerate clients that missed that predecessor; they download the full proof.
Cached local queries remain offline and do not require Jev or any paid provider.
Broader relevance quality and real-world applicability need contributed evidence,
not just performance measurements.

The health report exposes measured quantities, warns at 80% of measured bounds,
and leaves unmeasured values unknown. In particular, v1 object-visit usage is
unknown, not zero. Near-bound operation should trigger a separately reviewed
verification/layout design before growth, not automatic limit increases or
weakened validation. Historical-proof checkpoints would change the trust model
and require their own design.

## Before promotion or a broad launch

1. Accept independent final evidence review and green CI at immutable source.
2. Render and review the pilot workflow pins, preserve rollback state, and
   perform a bounded real pilot transition without synthetic contributions.
3. Confirm current-format publication, source agreement, complete receipts,
   bounded capacity and safe authenticated client sync after Pages settles.
4. Observe an actual unattended scheduled pilot publication within the accepted
   freshness window. A manual success cannot replace this evidence.
5. Review pilot evidence before production promotion; check production after
   deployment. Change the installed client/journal only when installation occurs.

The first live pre-rollout health snapshot found production fresh but pilot
scheduled completion older than the two-hour limit despite fresh page content.
Earlier schedule gaps were longer. Their cause remains undiagnosed. See
[health results](service-health.md) and [schedule observation](scheduled-service-observation.md).
An Actions-only watcher also cannot detect an Actions-wide outage, and alert
delivery has not been demonstrated. These limitations stay visible after a manual
pilot succeeds. Broad marketplace promotion is not part of these test results.
