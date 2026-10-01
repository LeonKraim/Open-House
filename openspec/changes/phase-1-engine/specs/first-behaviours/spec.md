# Spec Delta — first-behaviours

## Purpose

The first three behaviours the engine runs — motion lighting with its
lux-then-sun fallback, the manual override of a room's lighting, and the away
shutdown — written as **policy** in `engine/behaviours/` on top of the facilities
`engine-core` provides (slot binding, the config resolver, modes, arbitration,
override detection, rate limits, the decision log). This capability owns *what a
behaviour decides and when*; `engine-core` owns *how a decision becomes a command
and why it may not*. The seam is `design.md` D8's: mechanism in the core, policy
in `engine/behaviours/`, so Phase 2 can turn policy into pack data against a
boundary that already exists.

Each behaviour is derived from named rows of `catalog/behaviors.yaml` rather than
from a prose paraphrase, because the corpus is Phase 0's merged, provenance-tagged
distillation of four real estates and is the specification of the defaults. A
behaviour that read its slots, scope or concept anywhere other than its row would
be a second definition of a concept the corpus exists to define once — the drift
`reference-catalog`'s conformance rule already forbids. The three units and their
rows:

| Behaviour unit | Module | Corpus rows it is derived from |
| --- | --- | --- |
| `MotionLightingBehaviour` | `engine/behaviours/motion_lighting.py` | primary `lighting.motion_light_on`; also `lighting.motion_light_off`, `lighting.room_light_dim` (the optional lux slot), `lighting.solar_sun_light` (the sun branch) |
| `OverrideBehaviour` | `engine/behaviours/override.py` | primary `lighting.room_light_manual_on` |
| `AwayShutdownBehaviour` | `engine/behaviours/away_shutdown.py` | primary `lighting.away_shutdown`; also `modes.house_away`, `presence.house_emptied` |

Two things make these requirements testable in the way the phase intends rather
than merely true. First, the **decision log is the oracle** (`design.md` D2): a
requirement about a behaviour is asserted against the record its evaluation left,
not only against the light's final state, because "the light is off" is true
whether the timeout behaviour turned it off or it was never on — and a suite that
cannot tell those apart cannot measure mutation or explain a Hypothesis
counterexample. Every scenario below that could be satisfied by a state-only
assertion is written instead against a log field, and names it.

Second, the **corpus row is a checkable contract**, not a citation. A behaviour's
declared required and optional slots and its scope SHALL equal what its cited rows
say, so a behaviour cannot quietly gain a slot its row does not grant; the check
reads `catalog/behaviors.yaml` and fails on a divergence.

Two product rules bind this capability and appear here because this is where the
first behaviours are written: **every behaviour is OFF by default and can be
turned off on its own**, and **no behaviour may ever auto-unlock a door or
auto-open a garage**. Both are stated again in `product-invariants` as
cross-cutting rules; here they are requirements on the three units, because the
first author to violate them writes one of these three.

The concrete tunables of the three behaviours — the quiet timeout, the lux
threshold, the sun-elevation threshold, each behaviour's arbitrated priority and
its override reset conditions — are **content, not structure** (`design.md`,
Open Questions), settled by running the scenarios and the corpus. Nothing below
pins a number; every one of them is required to be a named config key resolved
through `engine-core`'s layered resolver and overridable per house and per room
without editing code, so the content can be tuned against a fixture rather than
against the source.

## ADDED Requirements

### Requirement: A behaviour is a policy unit bound to a corpus row
Each behaviour SHALL be a unit in `engine/behaviours/` implementing the
`Behaviour` protocol declared in `engine/behaviours/base.py`, carrying at least
`id`, `corpus_rows`, `scope`, `required_slots`, `optional_slots`, `priority` and
`enabled`, and a single `evaluate(ctx)` method that proposes commands through the
engine rather than actuating anything. The three units SHALL be
`MotionLightingBehaviour`, `OverrideBehaviour` and `AwayShutdownBehaviour`, and
the registry `engine/behaviours/__init__.py:default_behaviours()` SHALL return
them keyed by `id`.

`id` is the unit's stable identity: it keys the behaviour's enable flag
(Requirement: OFF by default), its entry in the arbitration tie-break, and the
`actor` field of every decision record it produces. Each proposed command
additionally carries, as its `rule`, the **corpus concept id it implements** —
`lighting.motion_light_on`, `lighting.motion_light_off`,
`lighting.away_shutdown`, `lighting.room_light_manual_on` — so a scenario can name
the concept rather than the unit. Two names, both load-bearing: the unit id says
*who decided* and the concept id says *which rule was matched*.

A check SHALL read each unit's `corpus_rows` in `catalog/behaviors.yaml` and
assert that the unit's `scope` equals its primary row's `scope`, that its
`required_slots` **equal** the primary row's `required_slots` exactly, and that
every slot it declares as `optional_slots` is an `optional_slots` entry of one of
its cited rows. A behaviour whose required slots are fewer than its row's, or
whose optional slot is not one its rows grant, is the drift this requirement
exists to catch.

#### Scenario: A behaviour declares a slot its corpus row does not grant
- **WHEN** `MotionLightingBehaviour.required_slots` is `["motion_sensor"]`,
  omitting `light_group` which `lighting.motion_light_on` requires
- **THEN** the conformance check fails and names the unit, the row and the
  missing slot

#### Scenario: An optional slot is invented
- **WHEN** a behaviour declares an `optional_slots` entry that appears in no row
  it cites
- **THEN** the conformance check fails and names the unit and the slot

#### Scenario: The registry is the single source of the behaviour set
- **WHEN** a behaviour unit exists under `engine/behaviours/` that
  `default_behaviours()` does not return
- **THEN** the check fails and names the unregistered unit, so a behaviour cannot
  be added without also gaining its enable flag and its log actor

### Requirement: Every behaviour is OFF by default and independently disableable
A freshly built house SHALL run no behaviour: each unit's `enabled` flag SHALL
default to `false`, and the engine's evaluation gate SHALL skip a disabled unit,
recording `skipped: disabled` with the unit's `id` as the `actor`. Turned on, a
behaviour SHALL be disableable **on its own** — disabling one leaves the others
acting exactly as before. The enable flag SHALL be a per-behaviour config key
(`behaviour.<id>.enabled`) resolved through the layered resolver, so a house or a
room can enable or disable a behaviour without editing code, and the module-level
enable flag `engine-core` defines SHALL gate a whole behaviour family by the same
mechanism.

This is the first of the two binding product rules, and it is a gate rather than a
convention (`design.md` D9): a behaviour that is not enabled is not evaluated,
and the skip is visible in the log rather than being an absence.

#### Scenario: A fresh house runs nothing
- **WHEN** a house is built from any fixture and time is advanced across a
  motion, a quiet timeout and an emptying, with no enable flag set
- **THEN** no command is proposed by any behaviour, and every evaluation records
  `skipped: disabled`

#### Scenario: Disabling one behaviour leaves the others working
- **WHEN** motion lighting and away shutdown are both enabled and motion lighting
  is then disabled
- **THEN** motion lighting's evaluations record `skipped: disabled` and away
  shutdown still turns the interior lights off when the house empties

#### Scenario: A behaviour is enabled at one layer and disabled at a higher one
- **WHEN** `behaviour.lighting.motion_light.enabled` is `true` at the house layer
  and `false` at the room layer for one room
- **THEN** the behaviour acts in the other rooms and records `skipped: disabled`
  in that one, and the deciding layer is named in the record

### Requirement: A behaviour proposes commands and never actuates
A behaviour SHALL NOT call the adapter to change state, add or remove an entity,
or set availability; it SHALL only propose commands to the engine, which
arbitrates, applies override and rate-limit suppression, checks the safety veto,
and actuates. This keeps the "why did nothing happen" question answerable from
one record — a behaviour that actuated directly would leave its action invisible
to arbitration and to the log.

In particular, **no behaviour SHALL propose a command that unlocks a `lock` or
opens a `cover`.** The engine's safety veto fails closed on such a command from
any non-user origin and records `refused: unsafe` (`engine-core`,
`product-invariants`); no unit in `engine/behaviours/` SHALL propose one, and
`AwayShutdownBehaviour` in particular SHALL command only `light_group` bindings —
its scope is lighting, not egress.

#### Scenario: A behaviour actuates the adapter directly
- **WHEN** any `engine/behaviours/` unit calls a write, add, remove, availability
  or fault operation on the adapter instead of proposing a command
- **THEN** the check fails and names the unit and the call

#### Scenario: Away shutdown proposes an unlock
- **WHEN** `AwayShutdownBehaviour` proposes any command targeting a `lock` or a
  `cover` binding
- **THEN** the safety veto refuses it, the record's outcome is `refused: unsafe`,
  and the check that no first behaviour names a `lock` or `cover` slot fails

### Requirement: Motion lighting turns a room on when dark and off on a quiet timeout
`MotionLightingBehaviour` SHALL, for a room whose bound `motion_sensor` reads
motion while the room is dark, propose turning the room's `light_group` on
(concept `lighting.motion_light_on`), and SHALL, once the room has been clear of
motion for the configured quiet timeout, propose turning that `light_group` off
(concept `lighting.motion_light_off`). Both concepts are read from the same unit
but recorded as distinct `rule` values, so a scenario distinguishes an on-decision
from an off-decision without reading device state.

The unit's `required_slots` SHALL be `[motion_sensor, light_group]`, matching
`lighting.motion_light_on`; a room in which either is unbound SHALL be skipped by
the engine with `skipped: unbound slot` naming the slot, not silently left dark.
A room already at the commanded state SHALL produce no command (an evaluation that
decides *not* to act still records a record, with the reason).

#### Scenario: Motion in a dark room switches the room on
- **WHEN** a room's bound motion sensor reads motion and the room is dark, with
  the behaviour enabled
- **THEN** the room's light group is on and a record exists whose `rule` is
  `lighting.motion_light_on` and whose outcome is `acted`

#### Scenario: The quiet timeout switches the room off
- **WHEN** motion stops and time is advanced by the quiet timeout with no further
  motion
- **THEN** the room's light group is off and a record exists whose `rule` is
  `lighting.motion_light_off`

#### Scenario: A brief motion pause does not switch the room off
- **WHEN** motion stops and time is advanced by less than the quiet timeout
- **THEN** no `lighting.motion_light_off` command is proposed and no off-record
  with outcome `acted` exists

#### Scenario: A required slot is unbound
- **WHEN** a room provides a `light_group` but binds no `motion_sensor`
- **THEN** the behaviour is skipped with `skipped: unbound slot` naming
  `motion_sensor`, and the room's lights are not switched on

#### Scenario: The room is already lit
- **WHEN** motion is detected in a dark room whose light group is already on
- **THEN** no command is proposed, and a record names the reason the behaviour
  declined rather than leaving the evaluation absent from the log

### Requirement: The dark test is a lux reading when bound and the sun when not
Motion lighting's dark test SHALL read the room's optional `lux_sensor` when it is
bound, and SHALL fall back to the computed sun position when it is not. The lux
slot SHALL be `optional_slots` — an unbound optional slot **degrades** the
behaviour, it does not skip it (this is `engine-core`'s optional-slot mechanism,
and it is the whole reason the fallback is expressible as data rather than as a
second behaviour). The sun branch SHALL be computed from the **virtual clock** and
the house's fixture location data (latitude, longitude and time zone carried as
fixture metadata, e.g. in `sim/fixtures.py`), because the phase has no wall clock.

Whichever branch decided SHALL be recorded as a named input on the decision
record — `dark_source: lux` or `dark_source: sun` — with the reading or the
computed elevation alongside it. This is the difference the log exists to carry:
on the no-lux fixture the same light turns on for a different reason, and a
scenario asserts *which* reason.

#### Scenario: The lux branch decides when the slot is bound
- **WHEN** a room binds a `lux_sensor` reading below the threshold and motion is
  detected
- **THEN** the light turns on, the record's `dark_source` is `lux`, and no sun
  position is consulted

#### Scenario: The sun branch decides when the slot is not bound
- **WHEN** the same scenario runs against the **no-lux fixture**, where no room
  binds a lux slot
- **THEN** the light turns on, the record's `dark_source` is `sun`, and the
  recorded elevation is the value computed for the current virtual time and the
  fixture's location

#### Scenario: A bright room is not lit although motion is present
- **WHEN** motion is detected while the bound lux reading is above the threshold,
  or while the computed sun is above the elevation threshold
- **THEN** the light is not switched on and a record names the light level as the
  reason for declining

#### Scenario: The sun branch follows the clock
- **WHEN** the same fixture is advanced from night to noon past the elevation
  threshold with motion present
- **THEN** the dark test flips from `sun`-dark to `sun`-bright without any change
  of seed, so the branch is a function of the virtual clock and the location and
  of nothing else

### Requirement: Behaviour timing is measured on the virtual clock
Every duration a behaviour observes — the quiet timeout, an override's
expiry — SHALL be measured against the engine's virtual clock, advanced only by
`advance_time`; no behaviour SHALL read a wall clock, sleep, or schedule a
callback. Advancing time by zero SHALL change no decision and produce no command.
This is what makes "the light goes off after the quiet timeout" a statement about
a number a scenario sets rather than about a scenario that waits.

#### Scenario: The timeout is a number, not a wait
- **WHEN** a scenario advances time by exactly the configured quiet timeout
- **THEN** the off command is proposed in that advance, and no test sleeps or
  reads the wall clock to observe it

#### Scenario: Advancing by zero changes nothing
- **WHEN** a scenario calls `advance_time` with a zero interval while a behaviour
  is mid-timeout
- **THEN** no decision changes, no command is proposed, and the same holds under
  the matching Hypothesis invariant

### Requirement: Multi-entity slot reads use a declared reduction
Where a slot resolves to more than one entity — the messy fixture binds the same
slot in two rooms, so a house-scoped `light_group` names several — the behaviour
SHALL read the slot through an explicit reduction declared by the behaviour, not
an inferred one: motion lighting SHALL read `motion_sensor` with the **any**
reduction (any member moving counts as motion) and SHALL command the
`light_group` as a whole. The reduction SHALL be part of the slot read the unit
performs, so `engine-core`'s reduction contract is exercised by a real behaviour
rather than only by a unit test.

#### Scenario: Any member of a multi-entity slot counts
- **WHEN** the messy fixture binds the same `light_group` slot in two rooms and
  only one of the two light groups is on
- **THEN** a read of the house-scoped `light_group` is true under the `any`
  reduction and false under `all`, and motion lighting commands the group as a
  whole

#### Scenario: The reduction is declared, not inferred
- **WHEN** a behaviour reads a multi-entity slot without naming a reduction
- **THEN** the check fails and names the behaviour and the slot, so no behaviour
  silently depends on an implicit rule

### Requirement: A manual change overrides the room's lighting until a reset condition
`OverrideBehaviour` SHALL detect that a room's `light_group` was last written by a
**user** — read from the change context the port carries (`house-adapter`), not
from a timestamp or a guess — and SHALL suppress the engine's own commands to that
light group until one of a closed set of **reset conditions** holds. The
suppression SHALL be recorded as outcome `overridden`, naming the reset condition
being waited on, not as an absent evaluation: the difference between "we chose not
to act because the user is in charge" and "we never considered acting" is exactly
what a state-only oracle cannot see. The unit is derived from
`lighting.room_light_manual_on`, whose corpus includes a room's lights turned on
by a manual cue; the behaviour makes that manual cue authoritative over the
automatic behaviour for the room.

The reset conditions are a closed set — **`override_timeout`** (the configured
override duration elapses on the virtual clock), **`room_emptied`** (the room's
motion sensor is clear for the quiet timeout), **`mode_changed`** (the room or
house leaves the mode the override was recorded under), and **`explicit_clear`**
(a `user_action` or a scenario step clears it). Each SHALL have its own test, and
the condition that ended an override SHALL appear in the record that resumes
acting.

#### Scenario: A user touch suppresses the automatic behaviour
- **WHEN** motion lighting is enabled and the user turns a room's light group on
  by a `user_action`
- **THEN** subsequent motion-lighting commands to that light group are suppressed
  with outcome `overridden`, and the record names the reset condition being
  awaited

#### Scenario: Each reset condition releases the override
- **WHEN** the override's `override_timeout` elapses, or the room empties for the
  quiet timeout, or the mode changes, or `explicit_clear` is called
- **THEN** the behaviour resumes acting on that light group and the resuming
  record names which condition released it

#### Scenario: An engine-origin write is not an override
- **WHEN** the light group was last written by the engine's own behaviour rather
  than by a user
- **THEN** no override is recorded and the behaviour continues to act, so the
  override turns on the change *context* and not on the fact that a change
  happened

### Requirement: Away shutdown turns interior lighting off when the house empties under away mode
`AwayShutdownBehaviour` SHALL, when the house empties **and** the house is in its
away mode, propose turning off the house's interior lighting (the `light_group`
bindings at room and house scope). It SHALL NOT fire while any person is home,
and it SHALL NOT fire when the house is away but a room's lighting was overridden
by a user for that room. The away mode SHALL be the mode the manner of
`schemas/mode/`'s `exclusive_group` makes exclusive — setting away clears its
group siblings — and the decision record SHALL name the mode, so a scenario can
assert the shutdown happened *because of* `modes.house_away` and not by accident
of the house being empty.

The unit's `required_slots` SHALL be `[light_group, house_mode]`, matching
`lighting.away_shutdown`'s row and the `house` scope `catalog/room_types.yaml`
provides; the emptying signal is derived from `presence.house_emptied` and the
mode from `modes.house_away`.

#### Scenario: The house empties in away mode
- **WHEN** the last person leaves, the `presence.house_emptied` signal is raised
  and `modes.house_away` is set
- **THEN** the interior light groups are off, and a record whose `rule` is
  `lighting.away_shutdown` names `house_away` as the mode that gated it

#### Scenario: One person is still home
- **WHEN** the house is otherwise empty but any person is home
- **THEN** no shutdown is proposed and no `lighting.away_shutdown` command with
  outcome `acted` exists

#### Scenario: Away mode is required, emptiness alone is not enough
- **WHEN** the house empties while no away mode is set
- **THEN** no shutdown is proposed, so emptiness and away mode are both required

#### Scenario: Away mode is exclusive
- **WHEN** `modes.house_away` is set while another mode in the same
  `exclusive_group` is active
- **THEN** the sibling is cleared, and the shutdown's record names `house_away`
  as the mode in force

### Requirement: Two behaviours on one entity resolve to one command
When more than one behaviour proposes a command to the same entity in one tick —
away shutdown and motion lighting can both name a light group, and a user action
can name it too — the engine SHALL reduce them to exactly one command by the
arbitration rule (`engine-core`), with a user action outranking every behaviour
and a deterministic tie-break by behaviour `id`. The decision records SHALL name
the winner and each loser, so a surprising tie is inspectable; the same input
SHALL yield the same winner across runs of the same seed. A behaviour SHALL NOT
implement its own conflict resolution, and no two first behaviours SHALL agree by
being scheduled in a particular order.

#### Scenario: Away shutdown and motion lighting contend for one light
- **WHEN** in one tick away shutdown proposes off and motion lighting proposes on
  for the same light group
- **THEN** exactly one command reaches the house, one record names the winner and
  another names the loser with outcome `lost arbitration`

#### Scenario: A user action outranks every behaviour
- **WHEN** a `user_action` and a behaviour both target one light group in one tick
- **THEN** the user's command wins, and the behaviour's record carries outcome
  `lost arbitration` naming the user's command

#### Scenario: The winner is stable across replays
- **WHEN** the same contending input is replayed at the same seed
- **THEN** the same behaviour wins, because the tie-break is by behaviour `id`
  rather than by evaluation order

### Requirement: Behaviour tunables resolve through the layered config resolver
Every tunable a behaviour reads — the quiet timeout, the lux threshold, the
sun-elevation threshold, the arbitrated `priority`, the override duration and the
reset conditions — SHALL resolve through `engine-core`'s layered config resolver
with the behaviour's own defaults as the lowest built-in layer, and the record
SHALL be able to name the layer that decided the value. A scenario SHALL be able
to change a tunable at the house or room layer and observe the behaviour's timing
or threshold change with no code edit, which is what lets the Open Questions'
content be settled by running scenarios rather than by editing source.

#### Scenario: A tunable is raised at the house layer
- **WHEN** the quiet timeout is set at the house layer to a value longer than the
  behaviour's default
- **THEN** the light stays on for the longer interval, and the record names the
  house layer as the deciding layer for that tunable

#### Scenario: No behaviour hardcodes a tunable
- **WHEN** a behaviour reads a timing or threshold value from a module constant
  rather than from the resolver
- **THEN** the check fails and names the unit and the value, so a tunable cannot
  bypass the resolver and become unoverridable

### Requirement: The decision log is the oracle for every behaviour claim
Every behaviour requirement in this capability SHALL be assertable from the
decision log alone. Each evaluation by a first behaviour SHALL append a record
carrying `engine-core`'s normative field set — virtual timestamp, `actor` (the
behaviour `id`), the inputs read (including `dark_source` where motion lighting
decided), the `rule` (the corpus concept id), the commands proposed, the outcome
(`acted`, `declined`, `lost arbitration`, `overridden`, `rate-limited`, `skipped:
unbound slot`, `skipped: disabled`, `refused: unsafe`) and the state delta — and the
scenario runner SHALL be able to assert on any of these fields. A behaviour that
could only be verified by reading device state SHALL be treated as unspecified,
because the "acted versus never-ran" distinction this phase's exit criterion turns
on is not observable in state.

#### Scenario: A scenario distinguishes acted from never-ran
- **WHEN** a scenario asserts a light is off *because the quiet timeout acted*
- **THEN** it passes only when a record with `rule`
  `lighting.motion_light_off` and outcome `acted` exists, and fails when the light
  was never on so no such record exists

#### Scenario: A behaviour evaluation leaves no record
- **WHEN** any enabled behaviour evaluates in a tick and appends no decision
  record naming its `actor`
- **THEN** the check fails and names the behaviour, so no behaviour can act or
  decline off the record
