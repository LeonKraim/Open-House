# The scenario corpus

Every seed in `catalog/edge_cases.yaml` that Phase 1 claims, written as a scenario
the runner can execute. The corpus exists so the seeds are a reading list that
runs rather than one that is read: `scenario-runner`'s requirement is that the
step vocabulary and the two assertion kinds are *sufficient* to express each
`phase_1` seed, and sufficiency is only checkable by expressing them.

## Running it

```
uv run oh-house scenario run scenarios/
```

A directory runs every `*.yaml` in it, each in its own session; a single file runs
just that one. `--seed` and `--started-at` override the `given` block for the
whole invocation, and `--json` reports the runs as one document.

## The convention

**One file per seed, named `<seed number>-<slug>.yaml`.** The first line of every
file is a comment naming the seed it expresses, in the form
`# seed: N of catalog/edge_cases.yaml -- <the seed's own words>`. A seed with two
halves that Phase 1 can decide separately gets two files and the second carries
`b` (`05-…` and `05b-…`); seed 5 is the watchdog not firing inside the quiet
timeout and firing after it.

**The header comment says what Phase 1 can and cannot decide.** Below the seed's
own words, each file explains how Phase 1 expresses the seed: what the scenario
provokes, which assertion is the honest one, and — where Phase 1 has no behaviour
for the seed — which later phase is expected to change it.

**The house and the clock are stated, never assumed.** `given.house` names a
fixture (`minimal`, `messy`, `large`, `no_lux`); `given.started_at` is a *quoted*
ISO timestamp, because an unquoted one is parsed as a `datetime` and fails the
schema; `given.seed` fixes the run so the scenario replays.

**Every step advances the clock.** `advance_time: minutes: 0` evaluates nothing at
all, so a scenario that needs a decision always advances at least a minute.
Behaviour modules are off unless the `given` block's `enable_flags` names them —
that is `product-invariants`' OFF-by-default rule reaching the corpus, and most
seeds do not enable anything because most seeds are about devices, not decisions.

**The negative assertion is a tripwire, not a claim about behaviour.** Where Phase
1 has no behaviour for a seed, the scenario provokes the seed exactly and asserts
what Phase 1 actually does — the device state, the availability, the readings —
plus `log: {must_not: [{outcome: acted}]}`. That last assertion is true today for
the plain reason that nothing is enabled to act, and it is written to go **red**
when the phase that adds the behaviour lands and acts without the seed's guard.
A red corpus is how the seed stops being a comment and starts being a test.

## Coverage

| Phase 1 behaviour | Seeds that decide it |
| --- | --- |
| `motion_lighting` (on, off-after-timeout, lux gate) | 5, 5b |
| everything else | provoked and asserted on state; `must_not: acted` holds |

Every seed from 1 to 26 is expressed. Seeds whose behaviour Phase 1 does not ship
are the tripwires described above. `away_shutdown` is the one seeded behaviour
Phase 1 *does* ship, and it is still a tripwire here: its gate is an active house
mode, no operation activates one except `restore`, and no scenario in this corpus
restores a document that does. The route therefore exists and is simply not what
these seeds are about -- a `restore` step accepts an inline document
(`sim/scenario/dsl.py`), so a scenario can make away mode true.
