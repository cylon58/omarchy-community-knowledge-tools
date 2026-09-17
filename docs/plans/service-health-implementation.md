# Service health implementation sequence

Authority: [health design](service-health-design.md) and the reviewed fair-intake
publication contract. No new server, paid provider, notification recipient,
contributor-code execution or authority shortcut. Operational status is not
canonical evidence. Implement after transport and fair intake stabilize.

## Task 1: measured public projection and bounded checker

Expose versioned aggregate health metadata from the successful build: exact
receipt count, proof object count/raw bytes, and precisely scoped canonical-builder
adapter calls/response bytes/object visits when measured. Reuse validated proof
objects; do not add a second network recovery. Keep absent measurements null.
Do not label record count as tree-entry count or reserialization as stored corpus
bytes. Read distribution payload sizes plus the manifest's own byte length for
the same total-site definition as the builder. Preserve existing publication cap.

Update the strict public-status reader to accept this explicit version while
retaining recognized old deployment compatibility. All producer/reader/version
changes travel together; unknown malformed formats remain unavailable, not an
excuse to reset cursor state. The separate cursor_health projection follows the
fair-intake transaction and never refreshes solely because a direct build ran.

Add a focused health module and CLI command using fixed deployment enums only.
Per deployment check: at most8 attempted requests,8MiB charged response bytes,
60 seconds overall and1MiB per body (with overflow/failure accounting). Normal
operation uses five fixed reads, no retries or pagination beyond the20-run page.
Bound parser work to that page; absence from it is unknown/missing evidence, not
proof no historical successful run exists. Use the reviewed anonymous Pages
helper for fixed status/distribution artifacts, and fixed GitHub repository/main/
scheduled-reconcile-run reads. No arbitrary URLs/workflows/repos or redirects,
tokens on Pages, proxy/netrc discovery or remote executable artifacts. Authenticate
the expected numeric repository identity. Status bytes must match their size/hash
in distribution metadata and source envelopes must agree. Mismatch during a
publication race is unavailable, not authority or permission to retry forever.

Return fixed-schema sanitized JSON with separate availability/intake/capacity/
evidence-freshness results and explicit unknowns. Use the design's four-hour
generation, two-hour successful-schedule,80% measured-capacity and cursor-warning
rules. Immediate revision mismatch warns without asserting duration. Upstream
unknown/partial remains evidence information, not a service-outage claim. Reject
future timestamps, malformed identities, invalid counts and bounds. Exit nonzero
for required-check failure/unavailability; warnings are visible but distinguished.

TDD: fresh healthy, stale/future, invalid JSON/hash/identity, redirect/oversize,
API outage, revision mismatch, old status, exact capacity denominators, partial
receipt coverage, failed/missing schedule, cursor unknown/progress/drift and
upstream partial. Exercise actual fixed request adapters with local responses,
not only mocked already-parsed reports. No real network during unit tests.

## Task 2: unattended workflow and real read-only verification

Add a separate toolkit workflow for schedule/manual checks on trusted main, with
contents-read only, immutable official action pins, short timeout and independent
cancel-in-progress health concurrency. No artifact from a PR, model provider,
secrets, mutation token, issue writes or cache executable. Run the same checker
for production and pilot and preserve both results even if one fails. Expose
failure through workflow conclusion; warnings and unknowns remain in the report.

Test the actual CLI exit/report contract and workflow identity/permission guards.
Verify the reviewed installed/package entrypoint in a disposable environment.
After review and CI, perform bounded real read-only checks and record the run,
source, timestamps, results and limits. The currently deployed old service may
legitimately report not-yet-supported metrics before final rollout; do not invent
new values or call that proof of new deployment.

Document watcher notification opt-in and how to run the CLI independently. Do not
claim any alert was delivered. An Actions-only watcher cannot detect an Actions-
wide scheduling outage; retain that residual risk explicitly. No new external
monitoring provider or local background service is installed by this plan.

## Integration gate

Keep the original observed long schedule gaps visible. Do not relax thresholds to
make the live report green. Code completion, manually successful runs and actual
unattended cadence are separate evidence. Final deployment remains gated on broad
review, immutable CI/pins, bounded pilot and documented supported limits.
