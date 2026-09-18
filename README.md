# Omarchy Community Knowledge

Omarchy Community Knowledge is a small, local-first companion for finding what
other users tried, including what failed, and sharing a cleaned-up observation
after you approve it. Community procedures remain inert text: the agent checks
your actual system and asks before changing anything.

## Join through the Omarchy plugin

Install the [Omarchy companion plugin](https://github.com/cylon58/omarchy-community-knowledge-plugin),
open its bar button, and choose **Set up my agent**. It connects the agent selected
in Omarchy to the shared knowledge. No account is needed to read it.

For a standalone install, open the [release](https://github.com/cylon58/omarchy-community-knowledge-tools/releases/latest)
and ask your agent:

> Install Omarchy Community Knowledge for my agent using this release.

Inspect the named wheel and standalone setup script first. Preview exactly what
setup will do:

```sh
python omarchy-knowledge-setup.py \
  --wheel ./omarchy_community_knowledge_tools-0.3.0-py3-none-any.whl \
  --dry-run
```

After approval, omit `--dry-run`. Setup creates an isolated venv and owned
launcher under `~/.local`, detects Omarchy's configured default agent without
changing it, links two skills in that agent's documented location, and attempts
the first public sync. Codex, OpenCode, and Gemini CLI use `~/.agents/skills`;
Claude Code uses `~/.claude/skills`, and Antigravity CLI (`agy`, selected by newer
Omarchy versions) uses `~/.gemini/antigravity-cli/skills`. An unknown or unset default stops safely and
shows the explicit `--agent` choices and manual fallback. Setup does not use
`sudo`, ask for secrets, or edit Omarchy's vendor skill. A fresh agent session
may be needed; explicit invocations are `$omarchy-knowledge-research` and
`$omarchy-knowledge-contribution`. Temporary-home packaging is tested, while
host discovery depends on the agent and its settings. See
[installation and removal](docs/install.md).

Reading and local drafting need no project account, invitation, GitHub account,
or project API key. Sharing needs the contributor's own GitHub account and normal
`gh auth login`; never paste a token into chat. Missing GitHub authentication does
not stop offline search or drafting.

## Research locally

```sh
omarchy-knowledge sync
omarchy-knowledge status
omarchy-knowledge search 'dock keyboard after resume'
omarchy-knowledge show CASE_ID --related
```

Search reads a validated local cache and labels its source revision and sync time.
A search refreshes first when the accepted snapshot is at least 24 hours old.
`search --offline` skips that refresh and visibly reports snapshot age and the
last refresh error, if any. A refresh outage preserves the previous accepted
snapshot and reports the error; without a cache, search waits for a successful
sync. Results include negative and contextual evidence. An upstream-resolution
event is always a community claim:
open its current official release link, check the installed version/channel and
availability, and retest locally before recommending an update or removing a
workaround.

Local trees can be checked with `omarchy-knowledge validate records/`. Drafting
and preview are deliberately separate:

```sh
omarchy-knowledge draft ./new-records ./draft --existing ./accepted-records
omarchy-knowledge preview ./draft owner/data-repo --existing ./accepted-records \
  --title 'Observed dock behavior' --body 'What was observed.' \
  --attribution '@your-public-github-name'
```

The primary approval view explains the problem, relevant equipment/software,
what changed, what happened, limitations or failures, links, destination, and
public name in ordinary language. Exact JSON remains available. The agent checks
records, PR text, links, and Git attribution, then names the generic make/model or
software details that remain. Privacy lint is not an anonymization guarantee.
Only the exact preview you explicitly approve should be submitted.

Eligible data-only additions are accepted automatically after bounded checks and
complete revalidation against current `main`. Existing case/change IDs should be
reused when relevant; failures, corrections, disputes, and later retests are new
linked records, not edits. Automatic acceptance means format/policy eligibility,
not technical truth, safety, owner review, or Omarchy endorsement.

## Trust, security, and licenses

This release changes the old trust model. It trusts the configured GitHub data
repository and maintainers for the published set, and relies on Git integrity for
transfer. Contributor statements, GitHub attribution, and release claims remain
claims; it does not recreate the old receipt/authority service. Normal sync does
not upload machine facts or discover credentials, and records are never executed.

Owners still handle abuse, privacy removal, compromised automation, and emergency
workflow shutdown. Report sensitive material through private vulnerability
reporting, never a public issue. Details are in [security](docs/security.md).

Toolkit code is MIT licensed. The preserved record corpus and original data
documentation are CC BY 4.0; stable IDs, source links, provenance, and attribution
must remain. The release inventory records every original path and SHA-256 digest.
See [NOTICE.md](NOTICE.md) and the data template's attribution file.

The local suite and wheel smoke test do not prove public anonymous sync, GitHub
token permissions/rulesets/event delivery, or fresh-session skill discovery.
Those are explicit rollout checks. The exact non-destructive release order and
rollback are in [re-release](docs/re-release.md).
