# Growth Baseline Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development task review.

**Goal:** Publish an honest reproducible full-path baseline before changing capacity.
**Architecture:** Synthetic real Git history; production admission/proof/distribution
and local search; emulate the GitHub network boundary, never the validation logic.
**Tech Stack:** Python >=3.11, unittest, existing Git fixture utilities.
**Spec:** `docs/growth-readiness.md`.

## Global Constraints

No credentials, paid APIs, private records, public load generation, limit changes,
production code changes, or model performance publication in this baseline task.
All fixture identities are synthetic; Git author fields are not independent users.
Default run bounded to at most 30 synthetic accepted imports and 120 seconds.
Larger explicit runs may be used locally with a declared timeout; never silently
remove production bounds. Existing validation and receipt authentication stay real.

### Task 1: Reproducible growth baseline

Files: new `experiments/growth/baseline.py`, `experiments/growth/README.md`,
`tests/test_growth_baseline.py`; optional fixture module in the same experiment dir.
No edits outside those files; root owns overarching docs and deployment decisions.

- [ ] Write a focused failing test for a tiny real synthetic roundtrip, asserting
  actual record/receipt counts and the final query, not that mocked methods ran.
- [ ] Run it red; implement `run_baseline(imports, reports_per_case=1)` returning a
  JSON-safe metric object with success/failure stage, records/receipts/import count,
  timings, generated artifact sizes and explicit measurement limitations.
- [ ] Exercise real prepare/publish and canonical receipt verification using local
  Git fixtures and a synthetic API identity boundary. Use a counting raw-object API
  adapter where feasible so reads are visible; never label LocalGit reads as live
  GitHub API calls. Distinguish modeled call counts from actual network requests.
- [ ] Build a real proof bundle and static distribution, import/sync to a temporary
  cache with canonical source, and run a compact ranked query. Include several
  distinct synthetic account IDs, linked failures and resolution/dispute evidence
  where existing valid fixtures permit. Do not claim these are real independent users.
- [ ] Expose bounded CLI JSON output with explicit `--imports`, and document exact
  commands and fixture semantics. Preserve failure-stage output without raw secrets.
- [ ] Run small tests and at least baseline3/10/30imports, publishing sanitized JSON
  results via apply_patch. If a size fails, preserve failure and explain; don't tune
  production limits. Record code revision, Python version, local-vs-network caveats.
- [ ] Write a report with red/green evidence and measured bottleneck; no commit/push
  by worker. Root reviews and commits scoped files after independent review.

## Subsequent plans

Transport, queue and health implementation briefs will be written after this
baseline identifies actual costs. This is a staged measurement plan, not a promise
that the current architecture supports a particular larger corpus.
