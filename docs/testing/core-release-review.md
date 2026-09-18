# Growth core integration review

Reviewed range: `2250d3cbb517e8a9c40dd1e49ac1c61b8edc48a0` through
`3c86a067a3b4266b5919f55f0ce992bcd2c21661`. This was a read-only independent
integration/security review of the frozen core, not a new benchmark or a claim
that no defects can remain. The later unattended health workflow was outside
this range and needs its own review.

Verdict: no new critical or important production-core finding; ready for the
remaining release gates, **not launch approval**.

## Boundaries checked

- Proof caches/update packs remain inert transport. Current-head authentication
  and canonical replay cannot be replaced by cached/static assertions.
- Batched reads validate complete responses before installation and share existing
  budgets; prefetching does not grant evidence authority.
- Receipt batching binds the exact accepted import. Recovery shortcuts do not
  replace full admission or canonical build validation.
- Cursor progress depends on the publication outcome. Uncertain scans fail closed;
  retries and direct events preserve previously published progress.
- Health reads stay fixed-origin, anonymous and bounded, with operational status
  separated from canonical evidence.

## Findings and corrections

The security document still said the hosted publisher never consumed its own Pages
proof. That was stale after reviewed proof reuse/update generation. Its corrected
paragraph describes optional bounded cache seeding, current API anchoring and
independent offline proof replay—not trust in the previous Pages assertion.

The native100-import gate does not execute optional update-pack generation for
each import. It measures the native canonical data path. The separate actual
publisher500→510 experiment measures update-pack integration at that size. Those
are complementary scopes, not interchangeable claims; the testing and growth
documents now make that explicit.

## Hosted CI failure still blocks the candidate

[CI run35289823828](https://github.com/cylon58/omarchy-community-knowledge-tools/actions/runs/35289823828)
failed one of492 tests in both Python versions (with one separate skip). The
failing hosted-proof-reuse test patched a global request cap after creating its
reader, but transport limits had become instance-bound. That test hook no longer
exhausted the reader. The correction must exercise the actual instance budget and
retain its no-new-request/shared-budget assertions; a failing assertion must not
simply be removed. Correction verification and green CI remain required.

The worker reproduced that failure and changed only the test's budget hook to
the reader instance. The focused test passed in0.089 seconds, asserting the failed
seed's consumed call/bytes and that the subsequent rejected read makes no new
request or byte-count change. No production budget code was changed for this fix.
Independent correction review and a new hosted CI run remain pending.

The Task2 addendum independently accepted the budget-fixture correction and the
read-only workflow after one portability correction. The installed smoke initially
hardcoded a private wheelhouse; it now explicitly opts in through an environment
variable and otherwise skips. See the [health test ledger](service-health.md).
No important finding remains in that scoped review. A new hosted CI run is still
required before acceptance of the combined candidate.

## Remaining gates at review time

The unattended workflow, installed-entrypoint smoke, bounded live observations,
frozen-source ten-import calibration, final500-record native gate, supported beta
envelope and pilot rollout remain separate work. Historical schedule gaps and
optional-pack lifecycle measurement limits stay visible even if later checks pass.
No production rollout or installed-client change was performed by this review.
