# Testing notebook

This directory records what we tested, what failed, what changed, and what remains
unproven. A passing unit test is not a production capacity claim. Synthetic accounts
and agent sessions are not independent community confirmations.

## Reproduce the current checks

Use the pinned runtime described in `docs/deployment.md`, then run:

```sh
python -m unittest discover -s tests -q
```

The wheel-install integration test needs an explicitly prepared offline wheelhouse;
without it, that test reports a skip. CI covers Python 3.11 and 3.13. See workflow
logs for the exact dependency setup and runtime; do not assume your local timings
will match ours.

Local-only scaling probe: `python -m experiments.local_search --cases 1000`.
[Recorded run](local-search-2026-09-17.json) includes the source revision, harness
hash, cold/warm timings and limitations. It is not the hosted growth baseline.

## Experiment register

| Experiment | Outcome / decision | Scope and evidence |
|---|---|---|
| Anonymous canonical sync | Replaced per-object anonymous REST reads with an API-anchored static Git proof bundle | `tests/test_object_bundle.py`; two identity/ref reads plus static transport, not zero network or unlimited capacity |
| Compact agent responses | Shortlist first, selected detail afterward; retain failure/dispute flags | `tests/test_compact_discovery.py`, `tests/test_event_discovery.py` |
| Local lexical ranking | FTS5, reviewed aliases, intent-specific ranking; broad mode remains explicit | `tests/test_retrieval.py`; lexical relevance is not applicability |
| Local persistent indexing | Reuse independently sealed local derivations; validate source hashes on every load | `tests/test_persistent_search.py`; code/schema/runtime invalidation, corruption, fallback, safety-cohort parity |
| Local embeddings/hybrid exploration | Not adopted; small exploratory fixture did not justify added model/download dependency | Private exploratory run, not independently reproducible from this register yet; no general vector-search performance conclusion |
| Code-graph alternative | Kept explicit case/change/report/event links rather than introducing a code graph | Architectural comparison, not a measured experiment; graph navigation does not replace applicability and adverse-evidence checks |
| Optional TypeSafe/Jev input experiments | Compared text/named fields, pair/batch questions and richer context; no mandatory cloud dependency added | Synthetic/public-only inputs with user approval. Provider performance outputs withheld pending publication permission; not evidence of production reliability |
| Full-pipeline growth baseline | In progress | `../growth-readiness.md`; executable fixture and measured results will be linked here when complete |

The public repository contains the regression tests named above. Historical private
exploration is explicitly labeled; it is not presented as a public reproducible
benchmark. Future experiment reports should include the commands, source revision,
fixture definition, result files, failures, runtime environment and limitations.

## Corrected assumptions

An installed smoke test initially assumed `dock Ethernet packet loss` would return
no candidates from the small public catalog. It returned a candidate at the lexical
coverage threshold. Running the prior reviewed ranker against the same records
returned the same result: this was an incorrect test expectation, not a persistent
index regression. The narrower `ethernet packet loss` query was empty in both.
We did not retune ranking during the performance update. This illustrates why
candidate relevance and technical applicability remain separate checks.

## Reporting rules

- Publish aggregate runtime details, never usernames, hostnames, credentials or
  private machine inventories. Use synthetic fixture IDs and fixed public examples.
- Keep cold start, warm query, refresh and server build measurements separate.
- Record failed runs and changed assumptions. Never silently loosen a security bound
  or retune a held-out threshold to make a result look better.
- Separate emulated API request counts from real network latency and live limits.
- State the tested corpus and history shape, not just the number of records.
- Pin result provenance to the measured implementation and fixture versions.
- Provider-specific publication restrictions apply even when testing cost is tiny.
