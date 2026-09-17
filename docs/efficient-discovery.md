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
weighted titles and relevant reported identifiers. The index is transient and
built from already-validated cached records, not downloaded executable database
content. No SQL, commands, extensions or model output from records is executed.
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
Persistent SQLite and vector artifacts are deferred until index-build costs or
measured retrieval quality justify the added distribution/security surface.
