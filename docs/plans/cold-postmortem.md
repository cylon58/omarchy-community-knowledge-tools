# Cold-recovery postmortem — bounded experiment

## Goal

Diagnose the failed native 500-record run without repeating its 21-minute import
pipeline. Preserve the old report unchanged. No production changes, limit changes,
network access, credentials or public synthetic contributions.

The saved native report records every accepted import/receipt mutation head.
Reconstruct the deterministic synthetic Git history directly in a temporary local
fixture, using the original cohorts, candidate metadata, manifest and receipt
formats. Require every predecessor and resulting mutation OID to match the saved
native run. A mismatch aborts: never assume an approximately similar graph is the
same experiment. This is fixture construction, not a replacement admission test.
Do not monkeypatch production validation or call the result a growth-gate pass.

Use a separate `experiments/growth/cold_postmortem.py`, dedicated tests, and saved
raw diagnostic JSON. Existing gate code/results stay unchanged. Reuse bounded
native fixture/object helpers where appropriate; production code remains untouched.

## Investigation and artifacts

1. Validate the saved report/profile and measured fixture provenance. Generate the
   exact graph, checking all recorded commit identities. Bound imports to 100,
   records to 500, and overall run to 600 seconds; arm deadline before fixture work.
2. Run unmodified native cold `read_canonical` and capture failure/success, adapter
   calls/bytes/deadline, actual emulated requests, object counts, and sanitized
   traceback module/function/line. Distinguish refused request 513 from 512 requests
   actually sent; do not expose local paths or arbitrary exception text.
3. Separately validate/replay the same graph through a local Git-object adapter
   with existing object/byte/deadline bounds. It is explicitly not a native HTTP
   capacity test. This may produce the diagnostic full proof, with exact 500 record/
   receipt counts and adverse evidence recorded if validation succeeds.
4. Optionally export that bounded proof to a new explicit private directory using
   exclusive/no-follow writes and manifest-last completion. Mark its purpose as
   postmortem, original growth gate failed, source report SHA-256, exact data head,
   current source hashes, and proof hash/size. Never overwrite outputs or old runs.

This artifact can support later transport comparisons; it does not repair the
service, waive cold limits, establish a passing gate or authorize production use.
All ordinary evidence validation must remain enabled for steps 2/3.

## Verification

Test-first: reconstruct a small actual native run and match all heads; tampered
saved head must fail; malformed/unsupported report rejected; counters distinguish
attempted/sent requests; output/artifact no-overwrite and path sanitation. Then run
the saved 500-record postmortem once, preserving raw output and source hashes. If exact
reconstruction proves infeasible, stop and report the concrete discrepancy rather
than weakening the matching requirement or re-running 100 imports without direction.
