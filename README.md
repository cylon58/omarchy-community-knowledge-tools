# Omarchy Community Knowledge

A community notebook for observations, proposed changes, and evidence about
Omarchy environments. Search results are investigation candidates. The toolkit
does not apply fixes, execute record snippets, or collect telemetry.

- [Canonical data](https://github.com/cylon58/omarchy-community-knowledge):
  repository ID 1373429914; data licensed CC BY 4.0.
- [Toolkit](https://github.com/cylon58/omarchy-community-knowledge-tools):
  repository ID 1373429982; code licensed MIT.
- [Operational pilot](https://github.com/cylon58/omarchy-community-knowledge-pilot):
  repository ID 1373467908; explicitly selected and separate from production.

This is an independent community project with no Omarchy endorsement. Original
sources retain their licenses. Linking a source does not grant permission to copy
its contents. An authenticated account is not proof of a distinct person or machine;
imported journals and external reports never become firsthand tests.

## Install and sync

Linux prerequisites: Git, Python 3.11 or newer, and Python's venv module. Install
a reviewed toolkit checkout into an isolated environment:

```sh
git clone https://github.com/cylon58/omarchy-community-knowledge-tools.git
cd omarchy-community-knowledge-tools
python -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/omarchy-knowledge --help
```

For a reproducible installation, select a reviewed full toolkit commit before
installing. Deployment owners publish a `deployment.json` containing the actual
reviewed toolkit and policy commits; see [native deployment](docs/deployment.md).
Inspect this configuration and use it with the matching toolkit installation:

```sh
.venv/bin/omarchy-knowledge sync --config deployment.json --cache cache
.venv/bin/omarchy-knowledge status --cache cache
.venv/bin/omarchy-knowledge search --cache cache --query 'dock keyboard' --intent corrective
.venv/bin/omarchy-knowledge explain CHANGE_UUID --cache cache --environment environment.json
```

Alternatively supply `--deployment production --toolkit-revision FULL_COMMIT
--policy-revision FULL_COMMIT` instead of `--config`. These must be actual reviewed
40-character commits, not the illustrative words in this sentence. The client
uses anonymous, bounded reads from GitHub's fixed API and never discovers account
credentials. API quotas or unsupported objects can make sync unavailable; an
existing cache remains usable.

Sync verifies repository numeric identity, main, immutable Git hashes, full record
validation, and receipt bindings. Offline queries display source, revision, age,
and staleness. Static JSON hashes establish integrity only. Ordinary local index
imports are attributed claims and clear canonical cache provenance.
[The trust boundary](docs/security.md#canonical-cache-trust) explains local seals.
An empty ledger is valid and yields zero results. No seed data is required.

Sync and the scheduled static build refresh official release, PR, declaration and
package-catalog facts. The bounded provider covers Omarchy's own package pair on
stable/rc x86_64. Empty ledgers still show catalog health. Optional system `vercmp`
enables Arch comparisons; missing support yields unknown. Zstandard catalogs need
the system `libzstd.so.1`; hosted read-only jobs provision both prerequisites.
The real maintainer authority list starts empty: facts alone yield investigation.
Only an authenticated, current supplier assertion joined with a published release
and every exact package condition permits update advice. This is not independent
binary/source proof. See [resolution evidence and declarations](docs/resolution.md).

## Draft and approve a contribution

Search existing cases, plugins, and upstream issues first. Draft only the relevant
facts; preserve failures, unknowns, original source links, and deviations. See the
[record architecture](docs/architecture.md) and [privacy guidance](docs/privacy.md).

```sh
.venv/bin/omarchy-knowledge draft candidate.json --corpus related-case.json --output draft.json
.venv/bin/omarchy-knowledge routes > routing.json
.venv/bin/omarchy-knowledge preview draft.json --config routing.json --destination ledger \
  --title 'Observed behavior' --body 'Reviewed public account of the observation.' \
  --attribution '@contributor'
```

The routing configuration names the actual ledger and toolkit. Upstream and plugin
destinations are null until explicitly selected; they never inherit the ledger
destination. Review the exact record bytes, destination, PR title/body, and
attribution. Only after the user approves that exact payload, repeat the preview
with `--approve --receipt consent.json`. The consent note binds the bytes but
does not prove who approved or authorize later changed content.

An agent may then use its normal `gh` workflow to push only approved record files
to the contributor's fork and open a PR against the approved repository's main.
It must recheck the consent hash, numeric destination identity, and exact staged
diff immediately before publishing. Any substantive change needs a new preview
and approval. Publication is this explicit agent handoff; the local CLI never
automatically posts. See [contribution and invitation policy](docs/contributing.md).

## Intake and distribution

The deployable native service uses separate PR-target and hourly/manual workflows,
a read-only plan, independently revalidated publication, canonical static build,
and a separate Pages job. Deployment files are generated only after the public
toolkit commit exists. Local tests do not establish that a hosted service is
active; verify [the launch checklist](docs/launch-checklist.md) against actual runs.

Status distinguishes accepted, receipt-pending, rejected, unavailable, and retry.
Accepted names the PR number and exact source head H imported into main. PRs
remain open by design; a later push is a new candidate. No import is called merged.
Native token writes do not trigger ordinary push workflows, so the same run builds
and deploys its own bounded Pages artifact.

Each run can import one snapshot after reconciliation, with at most 20 preparation
attempts in a rotating window of the 200 newest open PRs. Pending receipts stop
new imports. The shared queue holds at most 100 pending runs; larger bursts and
older PRs can require operator attention. Recovery covers 100 recent commits.
Schedules, quotas, and GitHub availability are not an SLA. See
[limits and recovery](docs/recovery.md).

## Optional local skills and panel

The wheel bundles portable research and contribution skills. Exposure is opt-in:

```sh
.venv/bin/omarchy-knowledge skills --agent codex
.venv/bin/omarchy-knowledge skills --agent codex --install
.venv/bin/omarchy-knowledge skills --agent codex --remove
.venv/bin/omarchy-knowledge skills --hermes-profile coder --install
```

Profiles include generic, Codex, Claude, Pi, Hermes, and Antigravity. Existing links
are never overwritten; removal touches only tracked matching links. Named Hermes
profiles must already exist. Links target the installed wheel, so remove exposures
before moving its environment.

The optional repository-root Omarchy panel provides local search/status/preview.
It has no apply/publication action and is not installed by the CLI. Validate a
checkout with `omarchy plugin validate .` and `qmllint Panel.qml`; live desktop
installation is a separate explicit choice.

## Development and support

```sh
.venv/bin/python -m unittest discover -s tests -q
.venv/bin/python -m examples.synthetic_demo
```

Tests and the demonstration use synthetic records. The ordinary toolkit CI is
read-only on Python 3.11/3.13. The native service fixes its runtime to Python 3.13
and pins its dependencies separately. The wheel backend packages the Python
modules, schemas, and skills without requiring build dependencies.

Public corrections belong in the relevant repository's issues or data PRs.
Use the private [toolkit security report](https://github.com/cylon58/omarchy-community-knowledge-tools/security/advisories/new)
or [ledger privacy/security report](https://github.com/cylon58/omarchy-community-knowledge/security/advisories/new)
for sensitive disclosures. If credentials leaked, revoke them at the provider
first. Public deletion cannot recall downloaded copies.
