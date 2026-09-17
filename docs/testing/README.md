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

The reviewed baseline/reporting revision `c0aeb0f` passed both Python versions in
[GitHub Actions run 35245682114](https://github.com/cylon58/omarchy-community-knowledge-tools/actions/runs/35245682114).
This validates the suite and synthetic smoke flow, not live service capacity.

Local-only scaling probe: `python -m experiments.local_search --cases 1000`.
[Recorded run](local-search-2026-09-17.json) includes the source revision, harness
hash, cold/warm timings and limitations. It is not the hosted growth baseline.
That historical JSON is a manually summarized run, not verbatim harness stdout:
the source-hash mapping was reduced to the harness hash and explanatory limitations
were expanded. The command reproduces the experiment, not the exact report shape.
Future recorded runs should preserve stdout and place commentary alongside it.

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
| Core data-path growth baseline | 3/10 imports completed; 30-import run hit its 120-second workload alarm after 18 completed publish returns | [Harness, methods and results](../../experiments/growth/README.md); interrupted acceptance count unknown; real Git/admission/receipts/build/sync/search with a local API boundary, not live Actions throughput |
| Batched receipt recovery | Candidate cut the ten-import run from 37.0 to 21.1 seconds; thirty-import run still hit its alarm | [Comparison, raw results and limitations](recovery-efficiency.md); not yet deployed or a larger-capacity claim |
| Hosted previous-proof reuse | Small two-import fixture reduced modeled requests from 42 to 24 with exact evidence parity | [Reproducer, raw results and corrections](hosted-proof-reuse.md); native HTTP caps and larger history remain separate gates |
| Native service growth gates | Ten-import and concentrated-evidence profiles passed; 500-record run completed imports/builds but failed final cold recovery | [Methods, raw successes/failure and review corrections](native-growth-gates.md); full growth gate remains unmet |
| Exact cold-failure reconstruction | Reproduced refusal of request 513 after 512 sent requests; separate local replay preserved all evidence | [Postmortem and reproducer](cold-recovery-postmortem.md); original native gate remains failed |
| GitHub GraphQL field probe | Two public GitHub reads; one tree and one text blob reconstructed to exact Git hashes | [Probe and limitations](graphql-field-probe.md); compatibility sample, not a scale benchmark |
| Live reconciliation cadence | Hourly configuration did not correspond to hourly recent production runs | [Timestamped read-only observation](scheduled-service-observation.md); cause undiagnosed, unattended cadence still a launch check |
| Authenticated object batching | First candidate failed traversal; corrected exact500 comparison passed with 238 requests and exact proof parity | [Raw failure/success and limitations](batched-object-reads.md); narrower than full pipeline, no deployment |
| Live native batch compatibility | Initial tree rejected on directory-size mismatch; reviewed correction passed two live object/hash checks | [Live failure, repeat and reproduction](live-native-batch.md); tiny compatibility sample, not capacity |
| Warm proof format comparison | Single update pack used5.48% of full proof bytes in the first ten-record update; all formats preserved exact data | [All candidates, reviewed reporting fixes and limitations](proof-formats.md); no production format adopted |
| Update-pack variation | Three same-base updates used4.90–5.37% of full bytes with exact proof/data parity | [Raw scenarios, reviewed corrections and scope](update-pack-variation.md); still one synthetic history, not deployed-client measurements |
| Production update-pack integration | Reviewed actual-client500→510 run: matching proof transfer2.98% of full; same-head0downloads; wrong-base fallback and exact parity | [Integration notebook, raw result and two deferred harness-hardening findings](update-pack-production.md); not deployed, matching-window only, final pipeline gate separate |
| Fair intake traversal | Reviewed pure scanner reached all200 eligible heads in a mixed1,000-PR fixture within per-run bounds | [Fixture, test correction and limits](fair-intake.md); authenticated adapters and service persistence still in progress |

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

CI [run35265816419](https://github.com/cylon58/omarchy-community-knowledge-tools/actions/runs/35265816419)
later exposed a separate older-baseline clock defect on Python3.13: the test that
injects a cleanup failure encountered a cold/warm query mismatch first. Root
reproduced it with snapshot ages1 and2 seconds on successive searches. Full response
comparison included legitimately advancing freshness metadata. The newer native
harness already fixed its clock; the historical baseline still needed the same
narrow correction. Production freshness checks must remain live and unchanged.

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
