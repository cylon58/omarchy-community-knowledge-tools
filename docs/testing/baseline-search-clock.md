# Historical baseline clock correction

CI [run35265816419](https://github.com/cylon58/omarchy-community-knowledge-tools/actions/runs/35265816419)
failed on Python3.13: a test injecting cleanup failure instead encountered an
earlier cold/warm search mismatch. The two searches compared full responses,
including status timestamps that legitimately advanced across a second boundary.

Root reproduced this deterministically with ages1 and2. A regression first failed
without the correction, then passed when both status calls received the fixture's
explicit `FIXED_NOW`. Real monotonic timing and full response comparison remain
unchanged. This is experimental harness behavior, not a frozen production clock.

The focused baseline suite passed six tests, and the original cleanup-injection
test passed independently. Root's fresh six-test run passed in5.52 seconds.
Independent task review found no spec or quality issues.

The correction subsequently passed hosted
[CI run35267643176](https://github.com/cylon58/omarchy-community-knowledge-tools/actions/runs/35267643176)
on commit `b33067068d7b546683b84eed03ffd6734e9c9af4`.

Reproduce with:

```sh
python -m unittest tests.test_growth_baseline
```

Old raw timing reports and their source revisions remain unchanged. The separate
native growth harness already used a fixed status clock; this correction closes
the same gap in the older baseline rather than changing production search.
