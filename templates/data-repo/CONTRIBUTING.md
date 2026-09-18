# Contributing an observation

Contributions are public, inert JSON records. Automatic acceptance checks their
shape, size, privacy warnings, history, and destination; it does not prove that a
technical claim is true, that a fix works everywhere, or that Omarchy endorses it.

Ask the Omarchy Community Knowledge contribution skill to prepare a local draft.
It should reuse existing case/change IDs when appropriate, but every new record
gets its own record UUID. A record UUID is not an equipment identifier: never put
a device serial, machine UUID, MAC/network address, hostname, username, private
path, credential, or unrelated log into record text, settings, or links.

Before submitting, the agent must:

1. Start from a fresh public `main`, not a private development branch. Stage only
   approved files under `records/cases`, `records/changes`, `records/reports`, or
   `records/events`.
2. Inspect the exact staged file contents, PR title/body, every link, and the Git
   author/committer identity. Prefer the contributor's approved GitHub no-reply
   address. A passing scanner is not proof that all personal information is gone.
3. Show the person the faithful plain-English sharing note. It must retain failed
   and uncertain outcomes and say which relevant make/model, software, settings,
   commands, versions, interactions, links, destination, and public name remain.
   Offer the exact records as optional technical detail.
4. Ask for explicit approval of that exact payload and destination. Any substantive
   change requires a new preview and approval.

Only after approval, use ordinary quoted Git and GitHub CLI arguments. A typical
handoff inspects `git diff --cached -- records/` and `git var GIT_AUTHOR_IDENT`,
creates exactly one clean data commit based on public `main`, pushes that commit to
the contributor's fork, and runs `gh pr create --repo "$repository"` with the
approved title/body. Do not squash or rewrite an unrelated branch to make it fit;
prepare a new clean branch instead. Never push private development history and
never contact an upstream project implicitly.

“Submitted” means GitHub received a pull request. “Accepted” means the repository's
hosted writer re-fetched the current PR and `main`, validated the entire resulting
record corpus, and completed an ordinary non-force push. A conflict, changed head,
changed base, policy failure, or delayed GitHub status can leave a PR pending. The
native workflow event or a numeric manual rerun is the retry mechanism; there is no
private queue or routine owner-review gate.

New claims that a fix was included in a release need a direct official Omarchy
release URL. An upstream pull request is useful research evidence, but is not by
itself proof that a fix shipped. Historical incomplete records remain readable.
