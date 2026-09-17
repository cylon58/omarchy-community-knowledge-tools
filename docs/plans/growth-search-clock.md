# Deterministic search comparison in growth harness

## Evidence

Root fresh `python -m unittest tests.test_cold_postmortem tests.test_growth_gates -q`
on2026-09-17 failed1of25: wrapper regression returned recovery-distribution
RuntimeError despite cold/warm proof parity. A direct one-import repeat passed.
Controlled `discovery.snapshot_status` using real status calculation at sequential
seconds reproduced failure with ages93601..93606. Compact results contain
`age_seconds`, so crossing a wall-clock second can invalidate full-result equality
without any change in evidence/search semantics.

## Task1

Change only experiments/growth/gates.py and tests/test_growth_gates.py, plus a short
worker report. Use one explicit fixed status-evaluation time for all cold/warm query
calls through a narrow harness-only patch of discovery.snapshot_status that invokes
the real function with its existing `now` parameter. Do not freeze monotonic timing,
edit production search, drop freshness flags, or ignore differing safety/evidence.
Choose FIXED_NOW consistently with the existing fixture; document synthetic clock
scope. Keep full result equality including deterministic freshness/age values.

Record query results and timing into report before checking acceptance so failures
retain their evidence instead of leaving queries empty. Test deterministic status
time used for all six calls; test genuine result/evidence inequality still fails
and preserves query evidence. Use test-first focused checks; one-import smoke;
no500run, no productionedits, no commits/push/deploy/subagents.

Historical raw results remain unchanged. This is a measurement correction, not a
production search speedup. Include the bounded regression failure in public notes.
