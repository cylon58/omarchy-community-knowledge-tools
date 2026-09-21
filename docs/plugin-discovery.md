# Plugin discovery

Community Knowledge primarily shares hardware and system fixes and observations.
The plugin directory is a separate discovery source, not another set of tested
community reports.

## Use

```sh
omarchy-knowledge plugins search 'clipboard' --json
omarchy-knowledge plugins show omarchy.clipboard --json
omarchy-knowledge plugins search 'audio' --offline --json
omarchy-knowledge plugins sync
omarchy-knowledge plugins status --json
omarchy-knowledge sync --plugins
```

Normal plugin search and show check the public marketplace on every invocation.
An HTTP conditional request uses its ETag and Last-Modified values when available.
A 304 response reuses the index; a changed, valid feed atomically replaces it.
An identical 200 response updates check metadata without rebuilding the index.
There is no daemon, timer, model service, or account requirement. New installations
attempt an initial index refresh. The companion's Refresh action updates both
knowledge records and the plugin index; either source can fail independently.
Existing users update the companion and choose Update / repair first.

An empty search browses the directory. Searches match all query words across
names, descriptions, tags, IDs, declared authors and GitHub repository owners.
SQLite FTS5 ranks matches, weighting names more strongly than descriptions.
The default returns five results; `--limit` accepts 1 through 100. Use shorter or
related terms for broader results. Query text stays on this machine.

## Credit and interpretation

Every result includes the marketplace's declared `author`, `github_owner`,
`github_owner_url`, original `repo`, source type, availability and verification
fields. Repository owners can be organizations or maintainers rather than the
original author. Agents should name the declared author and link the repository
owner without conflating the two. Built-in plugins are included and distinguished.

A catalog entry does not establish local installation, security, compatibility,
creator participation, endorsement, or a successful community test. Agents must
inspect relevant repositories and community reports before recommending changes.
Remote descriptions remain inert untrusted text. This feature neither executes
install commands nor installs or contacts anyone.

## Freshness and failure

`source.generated_at` is the marketplace's publication time. `checked_at` is the
last successful HTTP check. A recent check does not prove the marketplace itself
has recently refreshed its upstream data. Warnings from the source are preserved.
The JSON `source.state` is one of:

- `refreshed`: a valid new catalog was indexed.
- `not-modified`: the server returned 304.
- `unchanged`: a 200 response had the same bytes.
- `stale`: refresh failed; results use the last good index and expose the error.
- `offline`: the user explicitly skipped the network; any prior error remains visible.

Status does not access the network. Searches with a fallback return success with
an explicit cached-data warning on stderr and in JSON. Explicit sync returns a
failure exit code if refresh failed, even when a usable index remains. First-use
failure returns an actionable error, never a successful empty list. An empty,
malformed, oversized, duplicate-ID or older feed cannot replace an accepted index.
A valid nonempty replacement includes new/changed entries and removes missing
entries. This is catalog membership, not a statement that missing projects were
deleted or abandoned.

## Storage and bounds

The locally constructed SQLite index is `plugins.sqlite3` alongside the companion
cache, normally `~/.cache/omarchy-knowledge-next`. `--cache` changes this location.
It is independent of `accepted.json` and never uses the knowledge-record count
limit. The index stores normalized listing metadata and an FTS index, not repository
code, email addresses, installation commands, or downloaded SQLite databases.

The fixed source is https://plugins.omarchy.org/catalog.json. Fetching uses HTTPS,
no credentials, no ambient proxy configuration, and no redirects. Downloads are
bounded to 32 MiB and 20,000 listings, with a 20-second read deadline and 5-second
socket timeouts. Refreshes serialize with a bounded local file lock. Failed writes
and invalid downloads preserve the last usable snapshot. SQLite FTS5 support is
required. Limits are deliberate and failures are reported; they are not silently
raised when the marketplace grows.

Knowledge refresh and plugin refresh remain separate trust domains. Marketplace
content is validated for shape and bounded fields, not signed or independently
verified. This cache is not a security boundary against software running as the
same local user. Ownership-aware companion removal preserves cached data.
