# Omarchy Community Knowledge

Omarchy Community Knowledge is a shared knowledge base of what people have fixed,
changed, and built to make their Omarchy systems work. It helps your AI agent reuse
other users' hardware workarounds, fixes that haven't shipped with Omarchy,
configuration changes, and plugins built to solve particular problems.
Before spending time and tokens investigating from scratch or building something
new, your agent can check for relevant community experience.

The **companion helper is a small program on your computer**. It keeps a validated
local copy of the shared knowledge and a separate index of marketplace plugins.
It searches these locally and returns a short list to your agent, so the agent
can read the relevant evidence without loading the entire collection into its
conversation. This is designed to reduce token use as well as repeated work.

## How it helps

For example, if a dock stops waking a display, your agent can look for reports
about that hardware and symptom, inspect what others changed and whether it
worked, and check whether their solution applies to your system. If someone built
a plugin for the problem, the agent can investigate that existing work before
proposing a new one. These are examples of the intended workflow; results depend
on what the community has contributed.

The knowledge includes failures, limitations, and later corrections as well as
successful fixes. After working through your own problem, you can approve a
cleaned-up contribution so the next person can benefit too.

## Small searches, less context to read

- **Cache locally:** the helper downloads community records and keeps a local
  marketplace index. Your search terms stay on your machine.
- **Find a shortlist first:** local search ranks matches and returns five results
  by default. Plain-text knowledge search shows case IDs, titles, and evidence
  warnings; plugin search returns compact listings.
- **Read details when needed:** the agent opens a selected case with
  `show CASE_ID --related` to inspect its changes, results, failures, and corrections.
  It can request a plugin's full details separately.
- **Use ordinary code for retrieval:** SQLite full-text search does the ranking;
  searching requires no model call, embedding service, or project API key.
  Your agent still uses tokens to reason about the results and read the details.

The saving comes from keeping the collection outside the agent's context and
retrieving relevant evidence in stages. Actual token use depends on the agent,
query, and amount of evidence it reads; we do not claim a measured percentage.

Knowledge search attempts a refresh when its saved copy is at least a day old.
Plugin discovery checks for catalog updates on each normal search. Both support
explicit offline use and retain their last good cache if a refresh fails.

## Join through the Omarchy plugin

Install the [Omarchy companion plugin](https://github.com/cylon58/omarchy-community-knowledge-plugin),
open its bar button, and choose **Connect my agent**. It connects the agent selected
in Omarchy to the shared knowledge. No account is needed to read it.

For a standalone install, open the [release](https://github.com/cylon58/omarchy-community-knowledge-tools/releases/latest)
and ask your agent:

> Install Omarchy Community Knowledge for my agent using this release.

Inspect the named wheel and standalone setup script first. Preview exactly what
setup will do:

```sh
python omarchy-knowledge-setup.py \
  --wheel ./omarchy_community_knowledge_tools-0.4.1-py3-none-any.whl \
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

## How retrieval stays efficient

The helper separates storage, search, and reading:

1. **Store records outside the conversation.** Validated community records live in
   the local cache. The agent receives command output, not the entire cache.
2. **Rank locally.** Knowledge search builds an in-memory SQLite FTS5 index from
   case descriptions and linked hardware/software context. It weights titles,
   recognizes aliases such as sleep/suspend and trackpad/touchpad, and filters
   weak matches. If FTS5 is unavailable, it labels its literal-search fallback.
   The knowledge search index is rebuilt per query; the saved records are cached.
3. **Bound the first response.** Knowledge search defaults to five cases.
   Plain-text output is the smallest view; `--json` includes the full case records
   and evidence flags. `--limit` lets the caller choose more or fewer results.
4. **Expand a selected case.** `show CASE_ID --related` retrieves the linked changes,
   reports, and events, including adverse evidence. This keeps unrelated cases out
   of the conversation without hiding failures for the selected case.
5. **Keep plugin discovery compact too.** Marketplace listings use a separate,
   persistent SQLite index. Unchanged catalogs reuse it. Search JSON omits bulky
   catalog diagnostics and extra listing metadata by default; `--full` and
   `plugins show` provide detail when needed.

This is local keyword retrieval, not AI semantic search. It reduces the material
an agent needs to read, but a match still needs applicability checks. A complex
case can require substantial reading. See [plugin discovery](docs/plugin-discovery.md)
for catalog refresh and storage details.

## Discover plugins

The research skill uses a local marketplace index that checks for updates on every
normal plugin search. It preserves creator credit and original repository links.
Outages keep the last good snapshot with a visible warning; use `--offline` to
explicitly skip the network.

Search JSON returns compact listings and a warning count by default; add `--full`
for diagnostic metadata and all catalog warnings. Request wording such as
"better screenshot plugin" is reduced to capability terms. All-term matches are
preferred; `match.mode: any-term-fallback` explicitly identifies broader partial
matches when no listing matches every term. Check candidate capabilities before
recommending them. This is lexical discovery, not semantic or compatibility proof.

```sh
omarchy-knowledge plugins search 'clipboard' --json
omarchy-knowledge plugins show omarchy.clipboard --json
omarchy-knowledge plugins status --json
```

Plugin listings remain separate from community hardware and system reports.
See [plugin discovery](docs/plugin-discovery.md) for freshness, bounds and attribution.

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

## One project, three repositories

| Repository | What belongs here | Who starts here |
| --- | --- | --- |
| [Plugin](https://github.com/cylon58/omarchy-community-knowledge-plugin) | Omarchy bar interface and a bundled tools release | People installing or updating through Omarchy |
| [Tools](https://github.com/cylon58/omarchy-community-knowledge-tools) | Python CLI, search, validation, agent skills, and setup | Code contributors and standalone users |
| [Knowledge](https://github.com/cylon58/omarchy-community-knowledge) | Shared observations, changes, results, and evidence | People contributing or browsing community experience |

Install the plugin once; it supplies the tools, which read the shared knowledge.
You do not need to clone or install all three repositories.

The plugin follows Omarchy's plugin packaging and update flow. The tools also work
without the bar interface. Keeping records separate lets people contribute
knowledge without changing executable code, and preserves the data's CC BY 4.0
license alongside the code's MIT license. These are parts of one project.

For maintenance, use the [release guide](https://github.com/cylon58/omarchy-community-knowledge-plugin/blob/main/MAINTAINING.md).
