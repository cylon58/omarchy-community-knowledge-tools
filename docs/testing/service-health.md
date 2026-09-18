# Testing unattended service health

Status: checker/projection candidate implemented and scoped review passed;
unattended workflow and deployment pending. This notebook records the
read-only checker separately from the service it observes. The
[design](../plans/service-health-design.md) and
[implementation sequence](../plans/service-health-implementation.md) define the
checks before measurement.

## Questions this test must answer

- Can a bounded anonymous check distinguish fresh publication, stale publication,
  failed intake, missing scheduled success, and unknown capacity measurements?
- Are repository identity, status bytes and distribution metadata checked together?
- Do errors and missing measurements stay visible rather than becoming reassuring
  zeros or a green overall result?
- Does the hosted watcher preserve both production and pilot reports even if the
  first deployment check fails?

The normal checker is planned to make six fixed reads per deployment, with no
arbitrary URL input or repeated pagination. The enforced maximum is eight attempts,
8 MiB charged responses and 60 seconds overall, with a 1 MiB body limit. Tests must
cover actual request adapters, redirects, overflow and failures, not just parsed
happy-path JSON.

## Predeclared interpretation

Publication older than four hours or no observed successful scheduled Pages-job
completion within two hours fails the corresponding check. Creation time and run
update time are not completion time. A bounded extra job-attempt read supplies the
explicitly labeled Pages-job completion timestamp. Age and future-time comparisons
use observation completion, so publication
during the check is not incorrectly rejected as coming from the future.

Capacity warnings require measured usage, not merely a known limit. Record counts
are not directory-entry counts or stored byte counts. Cursor position is not
backlog size or waiting time. Unknown upstream evidence does not by itself mean
the service is down.

The [observed schedule gaps](scheduled-service-observation.md) remain an unresolved
operational concern. Thresholds will not be relaxed merely to make those reports
green. Implementation passing tests, a successful manual hosted run, and successful
unattended operation are three different claims.

## Limits and privacy

This is a separate operator check, not a new prerequisite for each local search.
Cached user queries remain offline; the monitor must not add routine network reads
or provider-token costs to that path.

Only fixed public production/pilot state is read. No private machine inventory,
new paid provider, contributor-code execution or notification recipient is needed.
Reports contain sanitized measurements and fixed failure categories, not remote
response bodies or exception messages.

An Actions-hosted watcher cannot establish availability during an Actions-wide
outage. The same read-only CLI can be run independently, but this milestone does
not install an external monitoring service. Watcher notification opt-in is not
proof that an alert was delivered.

### Notification setup is separate from monitoring

GitHub's account notification settings offer web/email Actions notifications and
a failures-only option. See [the official setup instructions](https://docs.github.com/en/subscriptions-and-notifications/how-tos/managing-github-actions-notifications).
Watching the repository alone is not proof of delivery: scheduled notifications
also depend on who created, changed the schedule of, or re-enabled the workflow.
See [GitHub's scheduled-notification rules](https://docs.github.com/en/actions/concepts/workflows-and-actions/notifications-for-workflow-runs).
An operator should check that ownership and opt in themselves. This project has
not changed anyone's notification settings or demonstrated a delivered alert.

## Results

The first frozen candidate passed 69 focused health/status/distribution/publisher/
service tests in 7.317 seconds:

```sh
python -m unittest -q tests.test_health tests.test_fair_intake_adapters \
  tests.test_distribution tests.test_update_publisher tests.test_service
```

The 54-test transport/API-version/entrypoint run completed in 6.723 seconds with one
reported skip. Its initial module-mode invocation lacked the repository's test
import path and failed importing `test_records`; the corrected invocation was:

```sh
PYTHONPATH=tests python -m unittest -q tests.test_github_object_batch \
  tests.test_native_api_version tests.test_local_workflow
```

Use the prepared project environment. No dependencies were installed to hide the
invocation failure. Compilation and whitespace checks passed. This is local
candidate evidence, not a live health result or acceptance by independent review.

Regression development exposed and corrected several issues before freeze:

- Failed API reads initially recorded zero charged bytes; the checker now uses
  the charged path for identity/main reads too.
- A publication during observation required a separate end-of-check wall clock.
- Valid pending workflow state was initially treated as a malformed response.
- A truncated scan could lack a warning when its summary flag disagreed with its
  strict scan telemetry; both are now considered.
- Sanitized failures initially omitted nested fields; failure reports now retain
  fixed nullable fields.

The candidate public `build_health` version1 contains record/receipt counts,
bounded proof object/raw/compressed sizes, and precisely scoped builder request
attempts/charged bytes. `object_visits` stays null because its existing counter
resets across validation windows. Older recognized status remains readable without
inventing new measurements. The CLI is `omarchy-knowledge health --deployment
production` (or `pilot`), with exit0 for ok/warning and exit1 for required failure.

Frozen checker SHA-256:
`b38d189bcf0c50b539e322def28b440bc1eb8dfc13e4fb39aa911d333c6fd22b`.
Frozen health-test SHA-256:
`3956003ac97733a09971f9f223d6209a266041248a00c66ae54c0f3190b2b583`.
The six-read healthy fixture charged3,296 response bytes and described a2,233-byte
site; these tiny synthetic values are not production capacity measurements.

Independent review, installed-entrypoint proof, hosted workflow evidence and real
bounded observations remain pending. Later corrections will be recorded here.

### First independent review: changes required

The initial passing tests did not catch four parser/accounting defects:

- The version1 reader accepted numeric object visits despite the contract requiring
  null, allowing an unmeasured whole-build capacity claim.
- One successful and one failed job both named `pages` were treated as unambiguous;
  boolean attempt1 could also pass integer equality.
- Age calculations truncated fractional seconds before rejecting future values,
  so a publication0.9 seconds in the future appeared to have age zero.
- Near the byte cap, an API read reserved too little response capacity before
  transfer; the adapter could exceed its charged limit before rejecting the result.
  The normal six-read checker did not naturally reach this state, but the adapter
  still must enforce its declared bound.

These findings require scoped regression tests and correction before acceptance.
Review confirmed fixed origins, anonymous Pages, unchanged ordinary client limits,
and separation from canonical authority. Proof metrics add a bounded local decode,
not another canonical replay or network recovery; that overhead is not claimed to
be zero. No live service health was inferred from the fixture tests.

### First correction (re-review pending)

Scoped tests reproduced all four findings. The correction requires null-only
object visits and removes that unmeasured value from capacity calculations;
counts all exact-name `pages` jobs before accepting one; checks exact integer
run/attempt bindings; retains fractional age precision for decisions; and reserves
the full API response plus overflow sentinel before attempting a health read.

One fractional-age test initially used a job timestamp before its run was created,
violating a separate existing invariant. Correcting the fixture restored that
test without loosening the timestamp checks. The near-cap test now verifies no
connection or attempted request occurs when the response reservation cannot fit.

The corrected candidate passed14 health tests,72 focused producer/reader/service
tests in7.318 seconds, and22 native transport/version tests in0.729 seconds.
Compilation and whitespace checks passed. Frozen corrected checker SHA-256:
`bf0e70a3781b59af573cfdb30aa542b4b2c7c8289ec5712600c549ee08fae117`;
health-test SHA-256:
`d7c9cbeafca586d214941b8a7860968d5256234bc7f2a6b31b4a4ee0d123c20e`.
Re-review remains required; these checks do not establish deployment.

Scoped re-review passed all four corrections, with no open original finding or
new important regression. The original offline probes now reject each malformed
case, and the near-cap probe performs zero requests without changing counters.
This accepts the checker slice, not the pending unattended workflow or live rollout.
A fresh controller run of all14 health tests passed in0.172 seconds.
