# Releasing the companion

The active architecture is the Python helper, portable agent skills, public JSON
records and the thin Omarchy plugin. The former service, scheduler and scale
experiments are retired; their existing history is preserved for reference.

1. Review the changed source and run `python -m unittest discover -s tests -v`.
2. Build the wheel using `python -m pip wheel --no-deps --wheel-dir dist .`.
3. Test a fresh isolated setup, search, offline use, repair and owned removal.
4. Publish the reviewed toolkit commit and wheel, standalone setup script and
   SHA-256 checksums in a GitHub release. Use public noreply commit identities.
5. Bundle those exact artifacts in the plugin repository using its staging script.
   Validate the manifest and exercise the panel in the running Omarchy shell.
6. Publish the plugin update. Existing users update the plugin and explicitly
   choose its Update / repair action; there is no unattended installation hook.
7. Update the data intake workflow only when needed, pinning the reviewed toolkit
   commit, actions and dependency hashes. Never check out or execute PR code.
   Verify a rejected unsupported-path PR and a legitimate accepted contribution.
8. Preserve existing data IDs and attribution. Keep legacy workflows disabled and
   the pilot private. Maintain no-force/no-delete rules; intake needs ordinary
   merge commits, so a linear-history-only rule is incompatible.

Public exports must not contain private build reports, machine journals, caches,
credentials or private development history. The one-time archive-migration tool
and its machine-specific test are deliberately not part of this public toolkit.

For rollback, disable the intake workflow first. Use normal forward commits to
restore a previous known-good deployment and plugin bundle. Preserve records and
Git histories; never force-push a rollback. A user's companion can be removed
through its ownership-aware setup script without deleting drafts or the cache.
