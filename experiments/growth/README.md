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
