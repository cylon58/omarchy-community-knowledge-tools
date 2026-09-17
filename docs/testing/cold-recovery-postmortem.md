# Cold recovery: why the 500-record gate failed

The original [native growth gate](native-growth-gates.md) remains a failure.
All 100 imports and builds completed, but final recovery without a preceding
static proof exhausted the native adapter's request allowance.

We reconstructed the exact synthetic Git graph without repeating the 21-minute
admission pipeline. All 200 predecessor heads and 200 resulting mutation heads
matched the original report. This is fixture reconstruction, not a new admission
benchmark. The unmodified native cold reader then attempted 513 calls, sent 512,
and refused the last before network exchange. Sent requests comprised 501 blobs,
eight trees, one commit, one repository lookup and one branch lookup. Response
bytes were only 708,691; the call count was the immediate constraint.

A separate bounded local Git-object replay validated 500 records, 500 receipts
and 100 adverse reports. It retained the existing object/byte/deadline bounds and
generated a 1,377,903-byte proof with exact offline replay parity. That local
adapter is **not** a passing native transport test. Its 1,902 object reads and
4,414 visits also show why merely raising the request limit would leave other
growth constraints to solve.

## Reproduce

```sh
python -m unittest tests.test_cold_postmortem tests.test_growth_gates -v
python -m experiments.growth.cold_postmortem --output /new/path/postmortem.json
```

Output paths must not already exist. Optional `--artifact-output` retains the
bounded proof in a new private directory, with a manifest written last. Synthetic
fixture construction and diagnostic execution use no network or credentials.
Use the diagnostic's historical source revision when reproducing this failure:
the loader intentionally rejects measured-source changes instead of silently
calling a different implementation the same experiment.

[Raw result](../../experiments/growth/results/native-cold-postmortem-v1.json)
preserves implementation hashes, original report hash, exact graph checks,
counters and sanitized traceback. The run took 18.72 seconds locally; that timing
does not measure GitHub latency, Actions scheduling or production throughput.
The original failed report is unchanged.

Review subsequently found that the diagnostic could swallow its overall deadline
while recording the expected native failure. Deadline and user-interrupt signals
now propagate; focused tests cover that correction. The direct fixture module was
also added to future source-hash reports. The original18.72-second result retains
its original hashes rather than being rewritten to claim it ran corrected code.

Next experiment: batch immutable Git object reads while preserving the same
authenticated head, typed object hashes, canonical validation and resource limits.
The [small real GitHub field probe](graphql-field-probe.md) supports testing that
approach; it does not establish its capacity or reliability.
