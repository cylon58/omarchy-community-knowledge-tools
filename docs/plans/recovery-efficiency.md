# Recovery Efficiency Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development and task review.

**Goal:** Avoid re-repairing already complete imports and write missing receipt sets
atomically without changing attribution or admission authority.
**Architecture:** Reuse exact canonical v2 receipt authentication, keyed by accepted
commit and manifest addition; batch only one immutable import's missing receipts.
**Tech Stack:** Python >=3.11, existing coordinator/Overlay/CAS and unittest fixtures.
**Spec:** `docs/plans/recovery-efficiency-design.md`, `docs/growth-readiness.md`.

## Global Constraints

No new authority source, no untrusted-code execution, no schema/privacy bypass,
no capacity-limit changes, no credential or external write by the worker.
Keep single-case public APIs/backward compatibility and failure semantics.
Never derive source actor/head from a mutable PR during recovery.
Only root publishes/deploys after review and pilot proof.

### Task 1: Exact coverage and batched missing receipts

Files: `omarchy_knowledge/coordinator.py`, narrowly necessary grant checks in
`omarchy_knowledge/admission.py`, new `tests/test_recovery_efficiency.py`.
Read candidate design first. Preserve all existing exact-base/receipt checks.

- [ ] Write and run failing tests for a completed multi-record import causing no
  redundant repair and for partial coverage being repaired in one receipt CAS.
  Assert final authenticated receipts and immutable source identity, not only spies.
- [ ] Extend the internal receipt authentication result to expose exact coverage
  by accepted commit and manifest addition. Preserve existing return callers.
- [ ] Avoid repeatedly expanding the same historical tree once per receipt in a
  multi-record import. Group bounded validated receipts by accepted commit and
  process one historical tree at a time, or use an equally bounded exact-path
  lookup. Retain every exact current/history/path/blob comparison; do not hold an
  unbounded collection of expanded historical trees. Measure object-visit counts
  and preserve their existing caps.
- [ ] `reconcile` skips `_repair` only for imports with complete authenticated
  coverage; partial coverage always uses the immutable validation/repair path.
- [ ] In `_repair`, compute existing/missing receipt bytes for the import, validate
  mismatches before any write, form one Overlay and exact grant for all missing
  receipts, perform one expected-base CAS. Re-inspect exact paths/blob hashes after
  ambiguous success; no uncontrolled retry and no cross-import receipt batches.
- [ ] Add regressions for forged/missing/mismatched receipts, current-blob mismatch,
  changed source PR, CAS conflict, ambiguous success and partial-recovery restart.
  Existing community contributions still cannot write provenance.
- [ ] Run focused coordinator/service/admission/canonical tests and the growth
  baseline at matching prior sizes. Record per-stage before/after, not just total
  setup time; leave failed baseline artifacts unchanged.
- [ ] Write report with red/green commands/output, exact changed files, measured
  benefit and residual limits. No commit/push/install by worker.

## Review focus

Receipt presence is not authentication. One receipt is not full coverage. A claim
that a pair `(PR, head)` was imported is not permission to skip missing additions.
Skipping repair must retain the canonical reader's exact current/history bindings.
