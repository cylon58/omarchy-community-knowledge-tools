# Security and privacy

Omarchy Community Knowledge treats every community record as untrusted, inert
data. The toolkit validates bounded JSON and searches it locally; it never runs a
record's commands, loads its configuration as code, or automatically repairs or
updates a machine. Normal sync accepts only the configured GitHub `owner/name`,
isolates Git configuration and credentials, performs no checkout, and retains the
last accepted cache when refresh fails.

Contribution checks accept only new canonical record files. They reject edits,
deletions, workflow/code changes, symlinks, submodules, hidden multi-commit
history, oversized input, known dangerous command patterns, and likely private
values. These checks are heuristics, not proof of anonymization or technical
truth. Before sharing, inspect the exact records, PR title/body, links, and Git
attribution; remove personal paths, hostnames, account identifiers, credentials,
network addresses, serial numbers, and unique device IDs. Relevant public
make/model/software identifiers may remain and must be named in the preview.

Automatic acceptance preserves both main and contribution ancestry with an
ordinary merge commit. Validation is repeated in the writer job, pushes are never
forced, and uncertainty after a push is reported rather than guessed. Acceptance
means only that a contribution met data and policy checks. Repository owners and
GitHub remain trusted; abuse, privacy removal, compromised automation, and
emergency workflow disablement remain owner responsibilities.

Report a suspected secret, personal-data exposure, or vulnerability through
[GitHub private vulnerability reporting](https://github.com/cylon58/omarchy-community-knowledge-tools/security/advisories/new),
never a public issue or contribution. If that private form is unavailable, withhold
the sensitive details rather than posting them publicly. Disable intake before containment;
preserve evidence privately, rotate exposed credentials outside this project,
and use reviewed forward commits or GitHub's documented sensitive-data process.

Release claims always require a current official source check and local retest.
The local suite does not verify live GitHub token permissions, rulesets, event
delivery, public anonymous sync, or fresh-agent skill discovery.
