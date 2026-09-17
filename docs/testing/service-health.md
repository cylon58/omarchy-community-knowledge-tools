# Testing unattended service health

Status: planned, not implemented or deployed. This notebook will record the
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

Only fixed public production/pilot state is read. No private machine inventory,
new paid provider, contributor-code execution or notification recipient is needed.
Reports contain sanitized measurements and fixed failure categories, not remote
response bodies or exception messages.

An Actions-hosted watcher cannot establish availability during an Actions-wide
outage. The same read-only CLI can be run independently, but this milestone does
not install an external monitoring service. Watcher notification opt-in is not
proof that an alert was delivered.

## Results

Pending. Commands, source hashes, raw reports, failures, corrections and hosted run
links will be added after the corresponding work is reviewed and executed.
