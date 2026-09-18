---
name: omarchy-knowledge-contribution
description: Use after solving or testing an Omarchy issue, creating or updating an Omarchy plugin, or when recording a preference, imported observation, or possible community contribution.
---

# Contribute Omarchy community knowledge

Prepare a faithful local draft first. Nothing becomes public until the user has reviewed the complete sharing preview and explicitly approved that exact version and destination.

After useful Omarchy work, offer to prepare a sanitized local draft even when the user did not ask to share. Creating the draft is local and reversible. Keep every public push behind the complete preview and explicit approval below.

## Prepare the draft

Search first. Reuse a relevant case and change ID for another outcome; create new records only when the problem or intervention is genuinely different. Record observed facts without filling missing fields by guesswork. Preserve whether the result was success, failure, partial, or inconclusive; keep uncertain causes uncertain. Label firsthand observations, user reports, and journal imports accurately.

Create the smallest useful records, then validate them into a new local draft:

```sh
omarchy-knowledge draft CANDIDATE_RECORDS DRAFT --existing EXISTING_RECORDS --json
```

Missing `gh` authentication never blocks local search, drafting, or previewing. Do not ask the user to paste a token or other secret into chat.

## Show a faithful sharing preview

Generate both the ordinary-language preview and optional exact JSON:

```sh
omarchy-knowledge preview DRAFT OWNER/REPOSITORY --existing EXISTING_RECORDS --title TITLE --body BODY --attribution PUBLIC_GITHUB_NAME
omarchy-knowledge preview DRAFT OWNER/REPOSITORY --existing EXISTING_RECORDS --title TITLE --body BODY --attribution PUBLIC_GITHUB_NAME --json
```

Use the same accepted-record context for draft and preview so linked report and event records are validated without copying accepted records into the contribution.

The ordinary-language preview is primary. Translate unfamiliar terms in place: for example, `x86_64` is a 64-bit computer, `rc` is a preview release, root requirements mean whether administrator permission is needed, and the compositor is the desktop's window and display settings. Keep exact literal values in the optional technical view or in parentheses when they matter. Also explain that a baseline is what happened before the change, a root cause is the reason if known, and relogin means signing out and back in. Cover every substantive fact from the exact records:

- the problem or preference, relevant equipment and software, what changed, and what happened;
- failures, limitations, uncertainty, provenance, commands/settings, and included links;
- the public repository, PR title/body, exact records, and chosen public GitHub name.

Offer the exact JSON as optional detail, but do not describe a short summary as the complete payload.

Before making a privacy assurance, inspect the full raw records, PR title and body, every link, and Git author and committer names/emails. Remove personal system details and unique equipment identifiers such as serial numbers, machine/device UUIDs, MAC or network addresses, hostnames, usernames/private paths, credentials, and unrelated logs. Relevant generic make/model, public vendor/product IDs, software versions, settings, and interactions may remain; name those remaining details in the preview. A passing automated check is only a heuristic.

After that inspection, use this plain-language shape, adjusted to the actual payload: “I've removed personal system details and unique equipment identifiers. This still names [relevant make/model/software or public product IDs] because they help explain the result. The exact records, contribution, and your chosen GitHub name [name] will be public.” Resolve or omit any suspect field before asking for approval; do not promise anonymity.

Any substantive record, title/body, link, destination, or attribution change requires a new preview and approval.

## Submit only after approval

After explicit approval, use ordinary `git` and `gh` tooling from a clean checkout of the public base. Stage only the approved record additions, inspect the exact staged diff and every commit that would be pushed, and use the user's approved GitHub no-reply identity. Create one clean data commit from the public base; never export private repository or journal history. Approval must precede the first public push as well as opening the pull request.

Automatic intake accepts only eligible data additions. Its checks do not establish technical truth, safety, or Omarchy endorsement; suspicious or invalid submissions can be rejected exceptionally without routine owner review.

Report states honestly:

- **drafted**: local only;
- **submitted**: the public push/PR exists;
- **PR merged**: GitHub reports that pull request merged, but trusted-main inclusion has not yet been verified;
- **accepted**: the approved records are verified as included in the trusted main branch;
- **unknown or pending**: PR or trusted-main status cannot yet be verified.

Never report “accepted” merely because local checks passed, a PR was submitted, or GitHub reports it merged.
