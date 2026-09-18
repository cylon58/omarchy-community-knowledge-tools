# Omarchy Community Knowledge data

This repository is the public, append-mostly data ledger for Omarchy Community
Knowledge. It contains inert JSON observations: problems, proposed changes,
results (including failures), and evidence claims. It is an independent community
project, not an official Omarchy service or a source of automatically executable
repairs.

Join through the [Omarchy plugin](https://github.com/cylon58/omarchy-community-knowledge-plugin).
Install it, open its bar button, and choose **Set up my agent**. It reads the agent
you selected in Omarchy and installs the research and contribution skills for
supported agents. The plugin documents exactly what setup changes. Reading and searching require
no project account, invitation, or GitHub login. Sharing requires your own GitHub
account and the normal `gh auth login` flow; never paste a token into chat.

The local loop is `search` and `show --related`, with `sync` for a manual refresh.
Search refreshes on use when the saved knowledge is at least a day old; `--offline`
uses the existing copy without a network request. A failed refresh keeps
the last validated cache and reports that it is stale. With no accepted cache,
search waits for a successful sync. Release events are community claims: open
their official links, check the installed version/channel and availability, and
retest locally before treating a workaround as obsolete.

An agent can prepare a draft and a plain-English preview covering the problem,
relevant equipment/software, what changed, what happened, limitations, links,
destination, and public GitHub name. Exact JSON remains available for inspection.
Only after explicit approval should the contributor create a clean, data-only PR.
Eligible additions are accepted automatically after bounded validation and full
revalidation; this is format/policy acceptance, not proof, endorsement, or a
privacy guarantee. Invalid or suspicious changes stay unaccepted. Owners handle
exceptional abuse and privacy reports.

The trust model is intentionally simple: readers trust the configured GitHub
repository and its maintainers for the published set, while contributor text,
authorship, and release statements remain claims. Git transfer integrity does not
make those statements true. Community procedures are data and must never be run
without independent applicability checks and user approval.

Records and original data documentation are CC BY 4.0; retain record IDs, source
links, provenance, and attribution. Workflow code is MIT. See `ATTRIBUTION.md`,
`LICENSE`, `LICENSE-MIT`, and `CONTRIBUTING.md`. Report secrets or vulnerabilities
through [private vulnerability reporting](https://github.com/cylon58/omarchy-community-knowledge/security/advisories/new),
not a public issue. Do not include raw machine logs or credentials in a contribution.
