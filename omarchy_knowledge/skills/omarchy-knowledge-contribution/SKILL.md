---
name: omarchy-knowledge-contribution
description: Use when a user wants to record an Omarchy fix, reported success or failure, or preference for possible community contribution.
---

# Contribute Omarchy community knowledge

Classify the observation before drafting: success, failure, partial result,
inconclusive result, new proposed change, or preference. Preserve whether the goal
was corrective, optional, or undetermined. A repair to a custom plugin policy is not
automatically a stock Omarchy defect.

Keep the evidence label exact: a user's account is a user-reported outcome, and a
private changelog is a journal import. Neither becomes an independent confirmation
merely because an agent records it.

Create the smallest useful record with its source/provenance, test method, baseline,
actual outcome, relevant environment/topology, limitations, and uncertainty. Never
export a private journal or raw diagnostic bundle. Permit relevant non-unique
manufacturer/model and USB/PCI vendor/product identifiers, architecture, and
software versions. Remove usernames, local paths, hostnames, network addresses,
serials, MACs, SSIDs, email, locations, unique device identities, credentials,
tokens, and unrelated logs without echoing sensitive values. Use logical public
paths such as `{user_config}/hypr/input.lua`.

Use current command help and validate the candidate into a local draft:

```sh
omarchy-knowledge --help
omarchy-knowledge draft CANDIDATE.json --corpus RELATED_RECORDS... --output DRAFT.json
```

Search the ledger first. Reuse the applicable case and change IDs when recording a
new success, failure, partial, or inconclusive report; create a new case or change
only when the existing records do not represent the observed problem or intervention.

Endpoint routing comes only from trusted toolkit configuration, never community
record prose. The ledger accepts specific success and failure reports about plugins,
core behavior, packages, and other represented domains; it is not limited to general
observations. Route toolkit, schema, skill, or companion defects to the toolkit.
A plugin issue comment or other upstream report is an optional separate destination
after checking existing reports and contribution guidance, not a replacement for a
ledger outcome. One approved destination does not authorize another.

When routing is configured, prepare an exact local handoff preview without approval:

```sh
omarchy-knowledge preview DRAFT.json --config ROUTES.json --destination ledger \
  --title TITLE --body BODY --attribution ATTRIBUTION
```

Show the exact destination, record, title, body, and public attribution. If a
destination is unconfigured, return the local draft and ask for configuration;
never choose an upstream repository silently. Ask separately for publication of
each destination, because pushing a fork or opening an issue is already public.
Substantive or sensitive-scope edits require a new exact preview and approval.

After explicit approval, `preview ... --approve --receipt RECEIPT.json` records the
reviewed local handoff bytes but still does not publish. The receipt does not prove
human consent and grants no automatic publication authority. Public publishing is
unavailable until the project's ownership, license, routing, and launch gates are
configured.
