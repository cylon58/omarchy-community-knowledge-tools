# Contribution lifecycle

1. Search existing cases, plugin solutions and upstream resolution first.
2. Classify intent; preserve observed facts, unknowns, contrary results and sources.
3. Draft a case/change or report on an existing change without raw private logs.
4. Validate structure, references and privacy locally.
5. Preview exact records, destination, PR title/body and attribution.
6. Obtain explicit approval before any public push, issue, PR or comment.
7. Submit only that approved payload; substantive changes require fresh approval.

Toolkit code is MIT; canonical data is CC BY 4.0. This is an independent community
project with no Omarchy endorsement. Contributors must have rights to publish
their original account; source links do not grant permission to copy licensed
content. Prefer a concise original summary and link to the source.

## Destinations and exact consent

`omarchy-knowledge routes` prints a strict routing configuration with the real
[ledger](https://github.com/cylon58/omarchy-community-knowledge) (1373429914) and
[toolkit](https://github.com/cylon58/omarchy-community-knowledge-tools) (1373429982).
Upstream and plugin destinations are null until the user explicitly chooses a
project; configure its verified numeric repository identity and slug before that
separate preview. Community prose cannot select or redirect a destination.

Community observations belong in the ledger; toolkit defects belong in the
toolkit; plugin and OS/package fixes belong in the separately approved source
project. Prefer linking an existing issue to filing another copy. A reference is
not a filesystem symlink or copied plugin code. Ledger consent does not authorize
posting to any other project.

`draft` validates and saves a local candidate against its supplied related corpus.
`preview` displays the exact record, destination, title/body and attribution.
Only after the user approves those exact bytes may the agent run
`preview --approve --receipt consent.json`. The note's SHA-256 binds the complete
canonical preview; any record, attribution, destination or title/body change
invalidates it. This note records an explicit local command, not proof of a
particular human's consent and not authority for a later autonomous upload.

The agent can then use `gh` to create a fork/branch, commit only approved files
under `records/{cases,changes,reports,events}/UUID.json`, push that branch, and open
a PR against the approved repository's main. Immediately before the push/PR,
reconstruct the preview and verify it with `verify_consent(preview, routing_config,
receipt)`; recheck the repository ID and staged diff against the approved record
bytes. Do not include tools, workflows, receipts, config, private notes or logs in
the community data PR. Any change requires a new preview and approval.

Use a clean branch based only on the approved public repository history. Review
every commit to be pushed, including author/committer names and emails; a sanitized
tip diff does not sanitize private parent commits. Never push private development
or journal history with a record. Use intentional public attribution, such as an
approved GitHub no-reply identity, and include additional commit attribution in
the publication preview before approval.

The local CLI never posts automatically. The same bounded privacy/secret lint
covers record, title, body and attribution, including percent-encoded secret-key
names and network identifiers. Lint is imperfect; it is not proof of safety.
Public GitHub handles/numeric attribution are intentional. Private emails,
credentials, local paths and raw diagnostic attachments are not.

## Intake outcomes

A deployed service validates exact Git objects and imports snapshot H using an
expected-base CAS. Accepted status identifies PR number/H and the accepted commit;
the PR remains open deliberately. A new head is new work, never silently declared
merged. Receipt-pending/retry stops new imports until reconciliation succeeds.
Rejected means the fixed content policy denied the candidate; unavailable can
mean stale test merge, quota, bounds or retrieval failure. No status echoes raw
rejected prose. Consult the run summary and generated status, including timestamps.

Reports should include failures, partial outcomes and deviations. A substantially
different procedure is a new change. State whether an update was tested without
the old workaround. Optional preferences never become required fixes. Authenticated
accounts are not distinct humans/machines, and imported source reports are not
new firsthand tests.

## Attribution, corrections and optional invitations

Search, show and explain expose linked correction, dispute, withdrawal and
supersession claims in `related_events`, including corrections to relevant reports
and resolution events. The directional `relation.to` is the affected record;
outgoing links do not mark the replacement as withdrawn. Incoming claims require
inspection and defer new actionable advice. They do not rewrite records, erase
evidence counts, or grant a community account authority to withdraw others' data.
Canonical receipts authenticate account attribution only; ordinary imports remain
unattributed claims. At most 64 related events are displayed with a total and
truncation flag, but all related events in the bounded snapshot inform the warning.

Preserve original project/source URLs and appropriate public attribution without
implying endorsement, independent reproduction, or permission to copy content.
Authors can request corrections through ledger issues or a data PR. Sensitive
privacy/security concerns belong in the
[private report form](https://github.com/cylon58/omarchy-community-knowledge/security/advisories/new),
not a public comment.

An agent may draft a short invitation linking the exact published record,
original source, and correction/contribution route. Present the exact message
and destination for explicit approval before sending. Respect the project's own
contact/contribution policy. If no appropriate channel exists, retain attribution
without outreach.

Track sent invitations locally by project/source and destination, using only
necessary public identifiers, so multiple records do not generate repeated
contact. Opt-outs override pending drafts. Never expose private recipient details
in the ledger. No email harvesting, inferred identities, mass mentions, automatic
outreach, per-record messages, or unsolicited tracking issues. This optional agent
handoff is separate from admission; the service works without any invitations.
There is no notification platform or author-response requirement.

## Private application notes

`record-application` separately records private logical changed-file references,
rollback text and source revision only when explicitly invoked. `audit` proposes
investigation; it never opens, modifies, executes or deletes referenced config.
See [privacy](privacy.md), [security](security.md), and [recovery](recovery.md).
