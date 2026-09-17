# Stabilize historical baseline query clock

Task: experimental harness only, not production freshness/search behavior.
Own `experiments/growth/baseline.py` and `tests/test_growth_baseline.py`.

CI run35265816419 on ed3a58b failed Python3.13 after363 tests: the cleanup-injection
test expected failure_stage=cleanup but got warm-ranked-query. Root reproduced
deterministically by wrapping discovery.snapshot_status and advancing its supplied
now by one second on each call: ages1 then2 cause full compact result inequality.
The native growth harness already controls this semantic clock; the older baseline
does not. This is a measuring-instrument defect, not a production freshness defect.

Apply the same narrow approach to baseline's cold/warm query pair: a fixed
FIXED_NOW snapshot-status clock while leaving real monotonic timing and full
response equality unchanged. Do not strip freshness/adverse fields, mock query
results, weaken cleanup tests, change search ranking or edit saved raw runs.
Add deterministic RED/GREEN regression that forces status-clock advancement when
now is omitted, then confirms explicit consistent now for both calls and preserved
full parity. Run focused baseline tests plus the existing cleanup-failure test.
No full growth reruns, network, provider calls or production module edits.

Record exact commands/failures/results and source hashes. Root will independently
review, run focused verification, document CI failure and publish the correction.
