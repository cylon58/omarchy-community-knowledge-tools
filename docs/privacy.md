# Explicit contributions, not telemetry

Collect only relevant nonunique model/vendor/device IDs, software versions,
configuration facts and topology. Avoid full machine inventories. Never export a
private journal wholesale, and never infer historical test versions from today's
machine inventory.

Do not collect serials, MAC/IP addresses, local usernames, hostnames, SSIDs,
emails, location, tokens, private repository data, EDID serials, Thunderbolt unique
identities or credential files. Public GitHub attribution is intentional and is
previewed separately. A hash of a forbidden identifier is not a privacy solution.

Use logical paths such as `{user_config}/hypr/input.lua`; logs are minimal reviewed
excerpts. No raw diagnostic attachments in MVP. Optional system tokens, if added,
must be random, contributor/case scoped and never derived from device identity.

Local preview happens BEFORE any public fork push, PR, issue or comment. Rejected
public submissions may already be visible in caches, notifications and Git history.
Show exact payload, destination and attribution; substantial changes invalidate
prior approval. Approval for a ledger contribution does not approve a second
submission to Omarchy or another plugin repository.

Automated secret/PII checks are conservative guardrails, not a guarantee. They
cannot reliably recognize every personal identifier in free text. A publication
helper cannot prove that a human actually consented; the local agent must honor
the conversation's approval boundary.

Known straightforward encodings are checked before publication, including
percent-encoded secret-key names in URLs, and IPv6 literals are treated as network
identifiers in free text. These bounded checks still cannot decode every possible
encoding or recognize every context-specific identifier. Rejected text is not
echoed in CLI diagnostics.

The entropy guard recognizes only bounded GitHub source-reference forms: numbered
issues/pulls, full-commit commit/blob/tree links, and full-commit compares. It scans
their path, query, fragment, and surrounding prose as separate semantic components
so a normal public path is not mistaken for one encoded token. Unrecognized URL
shapes retain the conservative whole-token check. This is only a false-positive
reduction: a URL or hash does not prove that a source exists, is public, is immutable
in every meaningful sense, or is safe. The heuristic can still miss encoded or split
secrets, so exact local preview and human review remain required.

Deletion cannot recall downloaded copies. Report sensitive privacy/security issues
privately through the [ledger security form](https://github.com/cylon58/omarchy-community-knowledge/security/advisories/new)
or [toolkit security form](https://github.com/cylon58/omarchy-community-knowledge-tools/security/advisories/new).
Revoke exposed credentials at their provider first. The owner pauses intake and
derived publication, investigates, and performs governed removal/correction and
rebuilds as needed. Privacy removal is an exception to append-mostly history.
Public corrections can use ledger issues or data PRs; never include sensitive
payloads in public reports. See [recovery](recovery.md).
