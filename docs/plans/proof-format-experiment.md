# Compare warm proof distribution formats

Prerequisite: reviewed batched-object candidate and a successful exact500 cold
comparison. This task measures formats; it does not change production protocols,
client caching, deployment or authority. No external requests or paid calls.

## Task1: bounded reproducible format comparison

Own new `experiments/growth/proof_formats.py`, focused tests and a new result file.
Reuse the exact original100-import graph reconstruction and its saved-report hash
binding. Generate a validated base proof with the current native fixture. Append
two five-record cohorts through the real local planning/publishing and canonical
proof paths: ten new records and receipts. No hand-waved acceptance or fabricated
receipt shortcuts for these successor imports.

Compare these four candidate families against the existing complete successor
bundle's actual compressed bytes:

- 64 and 256 buckets selected by leading OID bits;
- one compressed chunk per typed object;
- one bounded direct-predecessor update pack, with added objects and removed keys;
- the existing full download as control.

Use deterministic, head-independent typed-object framing inside reusable chunks,
stable sort order and gzip settings. Manifests are compact machine-readable bytes
and count toward transfer, not free metadata. Keep all payloads inert and local.
For every candidate reconstruct the exact successor typed-object map, encode the
ordinary full proof, and perform ordinary canonical replay. No candidate digest
substitutes for typed hashes or current-head validation.

Record full successor size, base size, current published bytes (including retained
full cold artifact), manifest bytes, changed payload bytes, total warm bytes,
additional requests, changed/removed objects, round-trip/parity checks and decode/
replay timings with explicit scope. Same-head reuse should need no proof chunks.
Do not compare against the larger sum of chunk sizes as the denominator. Preserve
poor ratios and candidates that fail bounds; no format is selected just because it
saves bytes while requiring dozens of extra requests.

Start with one successor. Only after its reviewed measurement is promising should
root authorize up to20 deterministic successor variants from the same base. Record
that limitation explicitly: varying updates against one history is not many
independent histories. Never repeat the full100-import admission timing pipeline.
The fixture can reuse its immutable base graph between independent scenarios, but
reports must not turn that reuse into an admission throughput claim.

## Bounds and tests

Maximum500base records, ten added records per scenario, initial one scenario,
600seconds overall armed before fixture work,120seconds per successor phase.
All existing native/object/proof limits remain. Reports use exclusive/no-follow
new targets; sanitize paths and arbitrary errors. No public synthetic records,
existing raw-result edits or installed-client changes.

Test deterministic encode/decode and manifest accounting; exact changed-object
selection; same-head zero chunks; corrupt/missing/wrong-base data rejected by the
experiment's round-trip checks; source-hash/graph binding; failed results preserved;
exclusive output. Run a small fixture test before the one authorized large-base
measurement. Publish commands, source hashes, fixture identity, raw result and
limitations. Root writes the public comparison notebook and chooses the next step.

The prototype codec is experimental tooling, not an accepted distribution parser.
Any chosen production format needs its own threat-model, negative tests, review,
backward-compatible full-download fallback and atomic-cache implementation task.
