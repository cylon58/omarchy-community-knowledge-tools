# Efficient local discovery

The toolkit downloads a verified catalog, then searches it locally. User queries
and environment files are not uploaded. Git remains canonical; no database server
or model account is needed.

## Small answers first

`search --cache CACHE --query QUERY` returns a ranked shortlist (five cases by
default), not every record and the complete upstream catalog. It includes intent,
applicability, evidence counts, adverse-report/review flags and freshness. These
are candidate results, not permission to apply a workaround.

Use `show` for the selected records and `explain --environment ENV.json` for their
applicability and complete linked evidence projections. Check linked reports and
events. Truncated changes require full case explanation before choosing an action.
`status --cache CACHE` retains the full upstream diagnostics. `--full` restores
diagnostic output on search/show/explain; the Python discovery API retains its
full-output default. CLI consumers needing the earlier search representation
should explicitly select `--full --method substring`.

## Ranking

The default is local SQLite FTS5/BM25 with a small reviewed alias vocabulary,
weighted titles and relevant reported identifiers. A disposable local SQLite index
is built from validated cached records and reused between queries. It is not a
downloaded database. No SQL, commands, extensions or model output from records is executed.
SQLite FTS5 must be available in Python's standard SQLite library. The literal
`--method substring` fallback does not require FTS5.

By default, at least half the meaningful query concept groups must match. This
heuristic reduces unrelated results that merely mention the same component.
`--broad` admits any matching group for exploratory searches. Neither coverage
nor ranking establishes applicability, independent reproduction or safety.
An empty query browses the selected intent; no useful result means refine the
query, not that the problem has never been solved. Queries are limited to 512
characters and 64 meaningful groups; use focused symptoms and identifiers.

Unknown applicability remains visible. Preferences still require explicit optional
intent. Case-level adverse-report flags include reports associated with excluded
incompatible changes: those flags request inspection, not a claim that all failures
apply to the current system. Report/account counts are not independent-machine
counts. Upstream and ledger freshness remain separate.

## Reusing work safely

Every query still checks the snapshot's source bytes and hashes. A separately
sealed local index also binds the relevant installed code and schemas. When that
binding is valid, the client can skip repeated whole-corpus schema validation and
index construction. Missing, altered or incompatible derived data triggers full
validation and rebuilding, or the uncached path if storage is unavailable.

The derived index does not contain authority: canonical provenance is checked
separately, and upstream recommendations are computed afresh. Ordinary imports
remain claims-only. This protects against accidental corruption and untrusted
imports, not malicious software running as the same local user.

Compact search selects its case shortlist before expensive evidence projections.
Each selected case still uses the complete validated evidence cohort, including
adverse reports and relevant disputes. Omitted cases are counted explicitly;
`--full` still projects all matches.

This improves repeated local queries, not unlimited service capacity. The existing
4,096-record snapshot cap and separate proof, byte and API bounds remain enforced.
Larger deployments need a separately reviewed incremental-sync/checkpoint and
partitioning design; increasing those limits alone is not a scaling plan.

## Optional experiments

Local embedding/hybrid retrieval and Jev-assisted ranking are experimental, not
runtime dependencies. The initial private evaluation uses synthetic queries over
a small public corpus and cannot establish general accuracy. Adding a model is
justified only by representative retrieval results, false matches, latency,
download/installation cost and actual model usage—not marketing benchmarks.

Jev may eventually help classify public discoveries once for all users, or rerank
explicitly approved public candidate sets. Its answers must remain advisory,
separate from deterministic intent, privacy, consent and release-authority checks.
Core local search continues working without Jev, credentials, or a network.

Code graphs do not replace structured case/change/report/event relationships.
Existing typed links remain the source for failures, disputes and supersessions.
Downloaded SQLite/vector artifacts remain deferred. A locally derived persistent
index avoids introducing those artifacts into the distribution trust boundary.
