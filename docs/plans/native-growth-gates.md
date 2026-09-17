# Native-boundary growth gates — implementation brief

Status: harness implemented and independently task-reviewed, including two fix
rounds for measurement fidelity. Larger gate measurements are pending.

## Goal

Test the actual native request adapters and per-job bounds, not only coordinator
logic behind a permissive fake. Preserve `run_baseline` and all old result files.
Use a separate `experiments/growth/gates.py` with a small `run_gate(profile)`
interface, focused tests and public raw results. No production-limit changes.

## Fixture boundary

Implement one narrow dynamic fake HTTPS server via `connection_factory`, backing
real `GitHubRead` and `GitHubWriter` instances. Accept only the fixed API/Pages
hosts and the exact methods/paths needed for repository/ref/pull/raw-object reads,
GraphQL expected-head writes and proof downloads. Existing synthetic fixture
helpers may supply server responses, but must not act as a parallel workflow
client. Only repository state and the last successfully published proof are shared.

For each import, use three fresh job adapters/object stores:

1. Read adapter seeds the preceding proof, then runs real planning.
2. Writer seeds the same proof, then runs real publish/reconciliation.
3. Read adapter seeds the still-old proof, builds and checks current canonical
   distribution, then replaces the fake Pages bundle only on complete success.

Use actual production request building, strict JSON, route/count/byte/deadline
bounds, object reconstruction, receipt checks and proof export/replay. API requests
may contain only a synthetic test token; Pages requests must never contain it.
No network requests, real credentials, live PRs or external writes. The CLI/event
environment layer and provider latency/hourly quotas remain separate limitations.

Count actual emulated requests and each adapter's `http.calls`/`http.bytes`.
The 512-call bound is per invocation, not aggregated across the whole experiment.
Response-byte accounting is not total network wire size. Fake-server helper calls
are fixture work, not additional GitHub requests. A native cap failure is a failure,
not permission to replace that adapter with a less constrained fake.

## Remove fixture process overhead only

Use a private, bounded immutable Git-object reader for the new harness, ideally one
`git cat-file --batch` process. Check object kind, declared size, framing and hash;
bound cached objects/bytes by existing proof limits. Record cache hits, bytes and
local subprocesses separately. Close/reap the child on success, failure and alarm.
Do not skip production object/schema/receipt checks or alter modeled request counts.
Keep fixture preparation timing separate from admission. Results from this new
fixture are not directly comparable wall-clock speedups over the old harness.

## Staged profiles and gates

First implement a one-import contract regression: exact routes/headers, expected
head CAS, request shape, independent job budgets, complete records/receipts, and
proof publication only after successful build. Include invalid/mixed receipt lanes,
eleven additions, stale head and failed publication; tests must assert behavior.

Then run ten imports as calibration before attempting `distributed-500x100`:
exactly 100 imports with five mixed-evidence records each. Use an explicit
3,600-second overall workload budget; exhausting it reports `incomplete`, not proof
that any individual phase failed. Each combined planning/admission operation and
final recovery/distribution phase retains an independent 120-second gate, bounded
also by the remaining overall time. Never restore a stale relative timer that
extends the overall budget. Cleanup/report overhead is reported honestly.

Record per import: fixture time; planning/publish/build times; each job's calls,
response bytes, seed outcome and mutations; completed-return status; and verified
final counts where reached. Include worst/p50/p95 admission times, cold/warm search
and its one-second warm gate, and the active failure stage. Do not infer unknown
canonical acceptance from interrupted returns. Preserve adverse evidence and source
identity across cold/warm paths. Record overall totals as context, not per-job caps.

Later reuse this seam for `concentrated-100-reports`: eleven imports, one case and
change, one hundred reports, resolution and dispute (104 records). The first batch
has eight reports plus case/change, nine further batches add ten reports each,
and the last adds two reports and two events. Keep resolution `supporting_reports`
within its existing 24-reference ceiling; every report still links to the case and
change and remains available to safety projection. Allow at most 900 seconds total
with the same phase gates. This is not part of the initial one-import regression.

## Evidence and review

Every result embeds profile/schema version, source revision, harness and measured
production-file hashes, dirty-code disclosure, runtime and zero-real-network
assertion. Write new files; preserve previous failed runs and their old semantics.
Do not run timing workloads concurrently with production edits or heavy tests.
Review the fake-server boundary and negative tests before claiming native-path
coverage. Passing this local model is not proof of GitHub latency, hourly quota,
scheduler throughput, fairness or independent community reproduction.

An optional explicit local artifact directory may preserve the successful final
proof for subsequent transport experiments without rerunning the full history.
The target must be new, with an existing parent; never overwrite or follow a
symlink. Write only bounded proof bytes and a final completion manifest binding
profile, revision, measured source hashes, size and SHA-256. Exclusive directory/
file creation with the manifest last is sufficient; a failed write may leave a
clearly incomplete directory and must not report success. Keep local paths out of
public result JSON and report export overhead separately. Default counts and
profiles remain unchanged; no synthetic production contribution is authorized.
