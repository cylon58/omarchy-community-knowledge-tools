# A shared notebook, with evidence attached

This project helps an agent answer three questions: has someone encountered this
problem, does their solution fit this environment, and has an upstream update made
the workaround unnecessary? It is not a repair-script runner or a telemetry agent.

## Two repositories, different authority

The data repository holds contributed observations and evidence. The toolkit
repository holds the rules and software that interpret them: schemas, validation,
search, agent skills, and the companion interface. Keeping these separate prevents
a normal data contribution from changing the code that accepts contributions.

Git is the canonical store. A generated index is a disposable read-optimized copy,
not another source of truth. No database server is required. The generated static
site includes component hashes and source identities; it does not change the
record model or acceptance rules.

Canonical records use `records/cases`, `records/changes`, `records/reports`, and
`records/events`, with each filename equal to its UUID. Snapshot construction takes
that explicit `records` path rather than recursively discovering a repository. It
validates path/type/ID alignment and the full linked corpus. Provenance receipts
remain a separate authority lane and are never promoted to trusted inputs merely
because an indexer encounters files near the records.

`build_snapshot` accepts a mutable local directory and therefore treats its revision
as a caller label. Admission-integrated callers use `build_snapshot_from_admitted_tree`,
which reruns exact-tree admission, reads content-addressed blobs from the evaluated
tree, and forces the manifest data revision to the evaluated commit. A checkout
mutation cannot retain the admitted commit label while changing snapshot bytes.

## Four record types

| Record | What it says |
| --- | --- |
| Case | What happened, what was expected, and whether that expectation is corrective or optional |
| Change | A proposed remedy, workaround, enhancement, or preference, including applicability and rollback |
| Report | What happened when someone observed the case or tried a particular change in a stated environment |
| Event | A later correction, dispute, supersession, withdrawal, or claimed upstream resolution |

Each record is a separate file. Later reports and events refer to earlier records,
so contrary results remain visible. Append-mostly history is useful accountability,
not a promise of immutability; administrators retain control and privacy takedowns
may require exceptional removal.

The environment is a small relevant graph, not a laptop fingerprint. For example,
a keyboard connected through a dock can differ from that keyboard connected
directly. Unknown connections remain unknown. There is no collection of serials,
network identifiers, or full machine inventories.

## Where knowledge belongs

The ledger stores a concise original account, its applicability, supporting reports,
and references. Existing plugins and upstream issues remain in their own projects.
A link is an ordinary reference, not a symlink and not a copied implementation.

An agent may prepare both a ledger report and a useful upstream issue comment, but
those are separate destinations requiring separate approval. Trusted local
configuration selects destinations; community prose cannot redirect a submission.
Public-source discovery creates attributed leads, never invented firsthand tests.

## Evidence, not votes

Display outcomes and relevant conditions separately. Three authenticated accounts
are three accounts, not proof of three independent humans. Ten imported comments
are not ten new independent tests. A failure on another topology may reveal a
boundary rather than invalidate every success. Preferences never become required
system changes because they are popular.

Trusted ingestion receipts bind accepted data to an API-observed account and exact
content. They are separate from the contributor's claims. Checksums in a downloaded
index detect corruption; they do not establish that its receipts are authentic.

## How an old workaround retires

An upstream claim starts as a claim. Separately obtained observations establish
whether the upstream change is relevant, included in source, distributed for the
user's channel and architecture, and activated locally. A maintainer can provide a
declaration through a governed identity binding; it does not itself prove package
availability. Missing information produces an explicit unknown, not guessed advice.

Later reverts, corrections, and stale observations must prevent an old positive
claim from continuing to recommend an update incorrectly. A local application
receipt can support an audit of changes previously made, but the audit never
silently removes configuration or executes a replacement.

## Local MVP and public service are different milestones

The local toolkit can validate, search, prepare drafts, and test admission against
immutable local Git trees. The native service renderer and canonical API sync are
implemented; public automatic acceptance requires the separately deployed trusted
coordinator and tested repository protections. Source code does not establish
hosted activation. See [the launch checklist](launch-checklist.md).

The intended steady state needs no routine technical curator for ordinary bounded
data records. Security governance, abuse handling, privacy removals, and recovery
still need accountable owners. Offline cached reads remain useful during outages.
