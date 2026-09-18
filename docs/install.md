# Install Omarchy Community Knowledge

The easiest route is the [Omarchy plugin](https://github.com/cylon58/omarchy-community-knowledge-plugin): install it, open its bar button, and choose **Set up my agent**. Its README explains updates and removing the companion before removing the plugin.

For standalone setup, start from the [release page](https://github.com/cylon58/omarchy-community-knowledge-tools/releases/latest), download the wheel and `omarchy-knowledge-setup.py`, and inspect the named release before approving installation. A useful request to your agent is:

> Install Omarchy Community Knowledge for my agent using this release.

The setup is explicit. It creates an isolated environment at `~/.local/share/omarchy-knowledge-next/venv`, an owned launcher at `~/.local/bin/omarchy-knowledge`, and two owned skill links. It reads `omarchy-default-agent` without arguments and never changes the default. Codex, OpenCode, and Gemini CLI use `~/.agents/skills`; Claude Code uses `~/.claude/skills`; Antigravity CLI (`agy`) uses `~/.gemini/antigravity-cli/skills`. If the setting is empty or names another agent, setup stops before changing files and explains the explicit `--agent codex|claude|opencode|gemini|agy` or manual skill-location fallback. It does not use `sudo`, edit the vendor Omarchy skill, or install through an Omarchy marketplace hook.

Preview the exact actions first:

```sh
python omarchy-knowledge-setup.py --wheel ./omarchy_community_knowledge_tools-0.3.0-py3-none-any.whl --dry-run
```

After reviewing that output, omit `--dry-run` to install. Setup attempts the first public knowledge refresh. If the network or public repository is unavailable, installation still succeeds and reports that refresh is pending. Search refreshes a snapshot that is at least 24 hours old; `omarchy-knowledge search --offline ...` bypasses refresh and visibly reports the accepted snapshot's age and last refresh error. Without an accepted snapshot, offline search stops with an actionable error.

Reading and local drafting require no project account, invitation, GitHub login, or project API key. Submitting a contribution uses the person's own GitHub account and normal `gh auth login` flow. Never paste a token or other secret into chat. Missing `gh` authentication does not block reading, drafting, or previewing.

Skill installation is not a guaranteed invocation hook. Start a fresh agent session if the skills do not appear, then invoke one explicitly with `$omarchy-knowledge-research` or `$omarchy-knowledge-contribution`. Additional agents can use their documented manual skill location until their discovery behavior is verified.

## Remove or upgrade

Remove only paths tracked by this setup:

```sh
python omarchy-knowledge-setup.py --remove
```

Removal follows the ownership receipts across earlier agent-default changes and preserves a launcher or skill link that changed after installation. To recover a broken owned Python environment without deleting drafts or the accepted cache, inspect the new wheel and run `python omarchy-knowledge-setup.py --wheel ./omarchy_community_knowledge_tools-0.3.0-py3-none-any.whl --repair`. Repair preserves the prior environment until the replacement works and restores it if installation fails.

Marketplace listing approval is separate from installing the plugin directly. Skill discovery and automatic use depend on each agent; neither is guaranteed on every request.
