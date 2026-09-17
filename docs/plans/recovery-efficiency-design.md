# Recovery efficiency — candidate design

Status: investigation; not implemented or deployed.

The current recovery loop inspects up to 100 recent commits and reruns complete
historical admission validation even when all receipts already exist. Each missing
receipt is also written with a separate checked mutation. Measure these costs in
the growth baseline before deciding the production change.

## Proposed optimization

Authenticate current receipt coverage once using the same exact source/record
binding contract as canonical reads. Track coverage by accepted import commit and
the exact manifest addition, not only `(PR, head)`: a single receipt for a ten-record
import is not complete coverage.

For a recent import, skip repair only when all expected additions have authenticated
receipts with exact current and historical blob/path bindings. Missing, malformed,
mismatched or contradictory coverage must not authorize skipping. This removes
redundant recovery work; new admission and canonical builds retain full validation.

For incomplete imports, revalidate the immutable import once, construct all missing
receipts, and add them in one exact-base CAS with a governed grant bound to that
single accepted commit and the exact sorted receipt additions. Never mix imports
or accept contributor-provided receipt grants. On ambiguous success inspect exact
published bytes; never blindly retry a write or use current PR identity for repair.

## Tests required

- Completed multi-record import: no additional mutation, no repeated repair.
- Partial receipt coverage: remaining receipts reconstructed from immutable import.
- Forged receipt, omitted addition, changed current blob, wrong actor/commit/path:
  fail closed rather than treating the import as complete.
- Changed source PR during recovery: original persisted identity remains binding.
- CAS conflict and ambiguous success: no duplicate import or uncontrolled retry.
- Compare cold recovery results and canonical receipts before/after optimization.
- Run existing admission, coordinator, service and canonical transport regressions.

## Review concern

This changes which historical checks are repeated during idle reconciliation.
Review must establish that the unchanged canonical receipt authentication contract
is sufficient; do not label it a cache-only change or silently replace the contract
with receipt presence, a stored boolean or an unauthenticated summary.
