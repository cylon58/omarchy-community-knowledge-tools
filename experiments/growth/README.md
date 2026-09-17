# Synthetic growth baseline

This experiment measures the core local data path without changing production
limits: real Git objects, admission, coordinator publication, canonical receipt
authentication, reconciliation, proof-bundle export, static distribution,
canonical cache sync, and cold/warm ranked search. Only the GitHub transport and
identity boundary is modeled locally. It performs zero network requests.

Each accepted import adds one linked cohort: a case, a change, one or more reports
(the first is a failure), an upstream-resolution claim, and a dispute of that
claim. Synthetic PR account IDs rotate across seven numeric IDs. These IDs are
not independent users, people, or machines, and Git author fields have no such
meaning either.

Activate the project virtual environment, then run from the repository root:

```sh
python -m experiments.growth.baseline --imports 3
python -m experiments.growth.baseline --imports 10
python -m experiments.growth.baseline --imports 30
```

`--imports` is required and bounded to 1–30. `--reports-per-case` defaults to 1
and is bounded to 1–6 so a cohort remains within the production ten-addition
limit. A 120-second alarm bounds the measured workload; temporary-directory
cleanup and JSON reporting can add small overhead, so this is not a hard process
kill at exactly 120 seconds. Failure output keeps the active stage and exception
class but omits exception messages.

Captured results are in `results/`. They identify source revision `2250d3c` and
the historical measured-harness SHA-256
`26aab87ee9ef7aa376149b683186c3d4bcfdb260fb0d722fd9c044e4d2d36b58`.
The current failure-accounting harness is
`fe20879bbf2084e0bd694ea176bc37879da54fa226eb31a5ef31547a76a4cc34`;
the historical JSON is intentionally unchanged. Its `imports_accepted` field
means completed successful `publish` returns observed by the harness, not an
independent count of canonical import commits. On this machine, 3 and 10 imports
completed; the 30-import workload alarm fired after 18 completed publish returns.
An interrupted publish could already have written canonical state, so its exact
accepted-import count is unknown. This is a bounded local result, not a large-scale
support claim. Local Git command counts include fixture construction,
while modeled boundary/raw-object counts describe calls through the synthetic
GitHub-shaped adapter. HTTP rate, request, and response limits outside
`APIObjects`, plus provider latency, concurrency, outages, and public queue
behavior, are not exercised. The harness creates a fresh `APIObjects` instance
for each synthetic import, reconciliation, canonical build, and sync boundary;
the local bare Git fixture persists across them. The import timing includes local
fixture Git construction and is therefore not deployed Actions throughput. The
harness calls `prepare`/`publish` directly and times `reconcile` separately; it
does not measure service event guards, `plan_run` artifact transfer, Actions queue
latency, or the complete hosted workflow.

## Native-boundary gates

`gates.py` is a separate successor harness; it does not change `run_baseline` or
reinterpret the historical results above. It drives the production `GitHubRead`
and `GitHubWriter` classes through a strict dynamic HTTPS fixture. The fixture
accepts only the fixed GitHub API and Pages hosts, exact native routes and
headers, and the production GraphQL expected-head mutation. It never opens a
network socket. Pages requests are anonymous, and the only accepted API token is
the inert literal `synthetic-gate-token`.

Each import uses three fresh adapters and object stores. Planning, publication,
and the current canonical build all receive the same preceding Pages proof; that
proof is replaced only after canonical validation, proof export/offline replay,
upstream-unknown refresh, and static build have completed. The local Git fixture
serves immutable objects through one bounded `git cat-file --batch` process and
checks type, size, framing, and SHA-1. Fixture subprocesses, cached bytes and hits
are reported separately from admission.

Planning uses production `plan_run`, canonicalizes its bounded batch exactly as
the workflow artifact does, reparses it with strict JSON, and gives that batch to
production `publish_run` with the same run ID and attempt. Consequently the
writer's reconciliation and admission share the writer adapter's seed, call,
byte, and deadline counters. Per-job elapsed time explicitly includes proof
seeding plus the production service wrapper.

Run only the contract profile during ordinary development:

```sh
python -m experiments.growth.gates one-import
```

The other declared profiles are `ten-imports`, `distributed-500x100` (100
five-record imports, 3,600-second overall budget), and
`concentrated-100-reports` (11 imports, 104 records, 900-second overall budget).
The overall alarm is armed before temporary workspace and repository setup.
Every measured phase is also clamped to the remaining overall deadline and a
120-second phase bound. Overall exhaustion is reported as `incomplete`; an
independent 120-second phase expiry is a gate `failure`. Neither asserts that an
interrupted publish did or did not mutate canonical state. Do not run the timing
profiles alongside code changes or other heavy tests.

Reports separate initial fixture setup from per-candidate preparation and contain
per-job production call/response-byte counters, planning/publication/build time,
completed-return status, observed mutations, successful publish returns, known
mutation acceptance, full build completion, final verified counts, cold/warm
canonical and proof parity, source hashes, dirty-worktree disclosure, and the
active failure stage. Interrupted runs preserve every fact observed so far while
leaving unverified canonical record/receipt counts unknown.

The fixture publishes Pages as one immutable state transition containing the
bundle and its import-history identity. Per-import and aggregate publication
facts are recovered from that state during cleanup, so an interruption
immediately after replacement cannot expose a new bundle with old counters.

Search uses a versioned representative set: the exact last case, a broad
symptom/domain query, and a declared no-hit query. Each query gets a fresh local
cache for its cold result and an immediate warm repeat. The report requires
per-query result parity, retains adverse-evidence visibility, reports the worst
warm time, and applies the one-second gate to that worst value.
Emulated response bytes are not total network wire bytes. Passing remains a local
model result, not evidence about GitHub latency, hourly quotas, Actions queues,
fairness, outage behavior, or independent community reproduction.

For a later local continuation experiment, a successful gate may retain only its
final bounded proof plus a completion manifest:

```sh
python -m experiments.growth.gates distributed-500x100 \
  --artifact-output /private/new-proof-directory
```

The explicit target must not already exist (including as a symlink), and its
parent must already be a real directory. Files are created exclusively with no
following or overwriting; `manifest.json` is written last and binds the profile,
revision, measured source hashes, proof size, and proof SHA-256. A failed export
does not report gate success and may leave a new partial directory without the
manifest completion marker. The runtime path is never embedded in the public
JSON report, and no record JSON is exported separately.

`--output` likewise requires a new file beneath an existing real directory. It
uses exclusive no-follow creation and rejects existing files and symlinks before
running the workload; reports are never silently overwritten.
