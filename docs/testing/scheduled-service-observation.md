# Live scheduled-service observation

Observed 2026-09-17 at approximately 18:43 UTC using read-only public GitHub API
and Pages requests. No workflow was dispatched, configuration changed or synthetic
contribution created by this check.

Production reconciliation workflow 359979026 was active and its default-branch
configuration specified `17 * * * *`. The five-most-recent scheduled-runs query
returned these four runs:

| Created at (UTC) | Run | Conclusion |
|---|---|---|
| 2026-09-17 14:59:53 | [35237303074](https://github.com/cylon58/omarchy-community-knowledge/actions/runs/35237303074) | success |
| 2026-09-17 09:59:08 | [35208060589](https://github.com/cylon58/omarchy-community-knowledge/actions/runs/35208060589) | success |
| 2026-09-17 04:52:52 | [35183582741](https://github.com/cylon58/omarchy-community-knowledge/actions/runs/35183582741) | success |
| 2026-09-16 23:38:56 | [35163214914](https://github.com/cylon58/omarchy-community-knowledge/actions/runs/35163214914) | success |

These observations do not demonstrate hourly execution. The cause of the gaps is
not established by this check. GitHub documents that scheduled events can be delayed
or dropped under load; that is a known platform limitation, not a diagnosis of
these particular gaps. [GitHub scheduling documentation](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)

Production Pages reported `status=idle`, 26 records/26 receipted records,
`verified_at=2026-09-17T15:03:04.476661Z`, and data revision
`3139acf1d07453d99e3ae72e00f96153eb23e060`. Hosted toolkit/policy remained
`9720575ed4d73b19549ef118f442cf611e009526`: no growth-branch change was deployed.
The pilot, configured with the same cron expression, had a successful scheduled
[run 35258010438](https://github.com/cylon58/omarchy-community-knowledge-pilot/actions/runs/35258010438)
created at 18:18:21 UTC that day.

## Reproduce the observations

```sh
gh api repos/cylon58/omarchy-community-knowledge/actions/workflows
gh api 'repos/cylon58/omarchy-community-knowledge/actions/workflows/359979026/runs?event=schedule&per_page=5'
curl --fail --silent --show-error --max-time 15 https://cylon58.github.io/omarchy-community-knowledge/status.json
```

Results evolve; the timestamps above are historical observations, not expected
output assertions. Only aggregate public status and run metadata are recorded here.

## Consequences for launch testing

A second bounded read-only run query at 21:50 UTC returned a newer successful
[run 35262441490](https://github.com/cylon58/omarchy-community-knowledge/actions/runs/35262441490),
created at 19:02:05 UTC on the same `3139acf1` data revision. Its creation was
4 hours 2 minutes after the preceding scheduled run, and the latest creation was
about 2 hours 48 minutes old when queried. This remains evidence of non-hourly
observed cadence, not a diagnosis of the cause or a measurement of completion age.
No new Pages fetch, dispatch or configuration change accompanied this second check.
The query was the same endpoint above with `per_page=6`.

- Check both publication age and absence of expected scheduled runs. Green
  historical workflow results alone cannot establish current service health.
- A four-hour freshness warning must not be silently relaxed to hide these gaps.
- A checker on GitHub Actions shares scheduler dependencies and cannot guarantee
  timely detection of an Actions-wide scheduling outage. Expose a standalone
  read-only check for independently operated monitoring.
- Do not promise hourly upstream-resolution detection or queue throughput from a
  cron expression. Measure the actual unattended cadence during the pilot.
- Additional external scheduling/notification services are separate operational
  decisions, not silently introduced by this experiment.
