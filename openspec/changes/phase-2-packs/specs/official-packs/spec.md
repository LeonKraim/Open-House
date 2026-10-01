# Spec Delta — official-packs

## Purpose

The packs the project ships, which are the phase's answer to "what does a person
get on day one". `spec.txt:58` names them: the "Bedtime button (lights off, Sleep
mode, optional thermostat, locks off by default), Roomba button (idle starts,
cleaning sends home, paused resumes, stuck notifies), bathroom fan, guest mode,
default room templates, plus packs derived from the merged catalog". This
capability is that list as artefacts — a manifest and a scenario each — and as the
licence rule that decides which of them may be derived from the corpus and which
must be written by hand.

That rule is not hypothetical here, and the corpus decides two of the entries in
ways a reader would not guess. **The bathroom fan has no slot and no row.** The
controlled vocabulary `tools/catalog/lexicon.py` fixes is a closed fourteen
(`climate_zone` … `vacuum`) and contains no fan, so `catalog/slots.yaml` cannot
declare one and `tools/catalog/slots.py` fails a slot outside the tuple — while
the corpus *does* carry fan references — `fan.mudroom_bathroom_fan` and
`fan.powder_room_fan`, recorded as entity references of raw records in
`catalog/raw-behaviors.json`, whose source paths `catalog/rooms.yaml` names and
which `catalog/behaviors.yaml` reaches only through the raw ids of
`modes.room_mode_off`. There is no behaviour row for a bathroom fan at all: the
fan appears only among that row's sixty-six `raw_ids`, and the row's `license` is
`no_licence` and its `reuse_status` is `ideas_only`. So the
fan pack cannot be derived from that row, and the slot it needs does not exist —
two facts that fix what this change must do. **And the same gate bounds the
derived set**: the corpus is 83 rows of which **19 are `reusable`** (10 `mit`, 9
`apache_2_0`) and 64 are `ideas_only` with no licence, so "plus packs derived
from the merged catalog" is a set of at most nineteen groundings, not a set of
eighty-three.

One structural consequence runs through both. `packs/official/HANDWRITTEN` is a
marker file whose own comment fixes its rule: *"every pack file in this directory
to be named here, and every name here to be a file that exists"*. A generated
pack placed in `packs/official/` would therefore have to be listed as a file a
person wrote, which is false — so the derived packs live in a tree of their own,
and the marker keeps meaning what it says.

## ADDED Requirements

### Requirement: The shipped set is the phase's list, and each entry is a valid pack of a declared kind

The project SHALL ship exactly the packs `spec.txt:58` names — the Bedtime
button, the Roomba button, the bathroom fan, guest mode, the default room
templates, and the corpus-derived set — each as a manifest valid under the
current schema and each declaring one of the five kinds. An entry SHALL map to
**one or more** manifests: four of the six entries are one pack each — the Bedtime
button, the Roomba button, the bathroom fan and guest mode — one is fixed at
seven by the frozen room-type catalog it reproduces, and one, the corpus-derived
set, holds however many manifests the derivation's own gate admits. How many that
is, is the corpus's property and not this requirement's to assert, and the
requirement therefore fixes only that the entry carries one or more; asserting a
count here would be the package claiming a number the derivation decides. An
entry with no manifest, or a manifest in the shipped tree belonging to no entry,
SHALL fail the check, so the list and the tree cannot drift apart.

#### Scenario: Every named pack exists and validates

- **WHEN** the shipped packs are validated against the current schema
- **THEN** each of the six entries has at least one manifest that validates and
  declares a kind from the five: the Bedtime button is a `module`, the Roomba
  button and the bathroom fan are `module`s, guest mode is a `profile-set`, and
  the default room templates entry is seven `1.2.0`-valid manifests — six
  `room-template`s and one `house-template`

#### Scenario: A named pack with no manifest fails the check

- **WHEN** an entry `spec.txt:58` names has no manifest in the shipped tree
- **THEN** the check fails, naming the entry, rather than the set quietly
  containing five entries where six were promised

#### Scenario: The mapping is one-or-many and is stated

- **WHEN** a manifest in the shipped tree is attributed to an entry
- **THEN** it is attributed to exactly one entry, and the entries that carry more
  than a single manifest are named as such — the default room templates, which
  the frozen room-type catalog fixes at seven, and the corpus-derived set, whose
  count the derivation decides and no count is asserted for — so a manifest that
  belongs to no entry is a failure and not an oversight

### Requirement: The Bedtime button turns the lights off, enters Sleep mode, and leaves locks alone by default

The Bedtime module SHALL, as one act, turn the room's lighting off and enter Sleep
mode, and SHALL carry a clause for the thermostat and a clause for locks. The
thermostat clause SHALL be optional — satisfied by a house that binds a climate
zone and inert in one that does not. The lock clause SHALL be **disabled by
default**, so that a default install of the Bedtime button does not touch a lock,
and the enabling SHALL be the act that makes it act.

#### Scenario: One act turns the lights off and enters Sleep mode

- **WHEN** the Bedtime button is pressed in a room whose light group is on
- **THEN** the light is commanded off and the house enters Sleep mode

#### Scenario: The thermostat clause is optional

- **WHEN** the pack is installed in a house whose room binds no climate zone
- **THEN** the install succeeds and the thermostat clause is inert

#### Scenario: Locks are untouched unless the lock clause is enabled

- **WHEN** the Bedtime button acts in a room with a bound lock and the lock clause
  has not been enabled
- **THEN** no command reaches the lock — "locks off by default" is a clause
  disabled by default, and the default install is observable as the absence of a
  lock command

#### Scenario: Sleep mode is a mode with an exclusive group

- **WHEN** the installed mode is read
- **THEN** it declares an `exclusive_group`, so entering Sleep mode and remaining
  in another mode of that group are the same contradiction `schemas/mode/1.0.0`
  names

### Requirement: The Roomba button acts on the vacuum's four states

The Roomba module SHALL declare behaviours for exactly the four states
`spec.txt:58` names — **idle starts, cleaning sends home, paused resumes, stuck
notifies** — each reached through the `vacuum` slot, and each with its own
scenario. The notification the stuck case raises SHALL be a declared service and
SHALL be flagged rather than banned, because notifying is legitimate and is not a
device command.

#### Scenario: Idle starts and cleaning sends home

- **WHEN** the bound vacuum reports `idle` and the button is pressed
- **THEN** it is commanded to start; **WHEN** it reports `cleaning` and the
  button is pressed, it is commanded to return home

#### Scenario: Paused resumes and stuck notifies

- **WHEN** the bound vacuum reports `paused` and the button is pressed
- **THEN** it resumes; **WHEN** it reports `stuck`, a notification is sent and the
  vacuum is not commanded

#### Scenario: The four states are the whole set

- **WHEN** the vacuum reports a state the pack declares no behaviour for
- **THEN** nothing is commanded and nothing is notified, and the pack's scenario
  names the five reported states and the one that is deliberately inert

### Requirement: The bathroom fan pack is hand-written, and this change adds the slot it needs

The bathroom fan pack SHALL be hand-written, because the corpus holds no row that
could ground it: the only row citing a fan is `modes.room_mode_off`, whose
`reuse_status` is `ideas_only` and whose `license` is `no_licence`, so its
expression may not be reproduced. This change SHALL add a `fan` role to
`tools/catalog/lexicon.py`'s controlled vocabulary and a matching slot to
`catalog/slots.yaml`, and the addition SHALL be **derived and not invented**:
`fan.mudroom_bathroom_fan` and `fan.powder_room_fan` are observed references, the
slot's `accepts_domains` is `[fan]`, and `tools/catalog/slots.py`'s drift check is
what proves the two files agree. The pack SHALL run the fan while the room is
occupied or humid and SHALL keep it running for a declared run-on period after
the condition clears, which `delay` expresses as a declarative timer.

#### Scenario: The fan slot is added on observed evidence

- **WHEN** the `fan` slot is added and `tools/catalog/slots.py` recomputes the
  vocabulary
- **THEN** the role and the slot agree, and the slot's examples cite the observed
  `fan.*` references rather than a spelling invented for the pack

#### Scenario: The fan pack is hand-written and says why

- **WHEN** the bathroom fan pack's provenance is checked
- **THEN** it is listed in `HANDWRITTEN` as a file a person wrote, and the reason
  it is not derived — the only adjacent row being `ideas_only` — is recorded

#### Scenario: The fan runs on and stops

- **WHEN** the room's humidity rises above the declared threshold and then falls
- **THEN** the fan is commanded on, and commanded off only after the declared
  run-on period has elapsed

#### Scenario: The fan pack does not reproduce the non-granting row

- **WHEN** the fan pack's behaviours are compared with
  `modes.room_mode_off`'s cited records
- **THEN** no expression is reproduced, because the row grants nothing — its
  pattern may inform the pack and its text may not be copied

### Requirement: Guest mode is a mode this phase ships, and its bundled profile selections are Phase 3's

Guest mode SHALL be a pack of kind `profile-set` conferring a mode with an
`exclusive_group`, validated against `schemas/mode/1.0.0.json` and installed
disabled like every other pack's contribution. The room-profile selections a
guest mode bundles SHALL NOT be declared by this phase: their shape is a
selection schema Phase 3 publishes, and the current `schemas/profile/1.0.0.json`
carries `name`, `description`, `modes` and `enabled_behaviours` and closes the
object, so a clause expressing a *room* selection has no shape today and `1.2.0`
adds none — a manifest clause for one would be this phase inventing a later
phase's format. `1.2.0`'s
`additionalProperties: false` SHALL be what makes that a refusal rather than a
silently ignored clause, and this phase SHALL ship the mode and its exclusivity
and nothing about the selections.

#### Scenario: Guest mode installs disabled

- **WHEN** guest mode is installed and no enabling act follows
- **THEN** the mode exists and is not active, and a tick produces no command of
  its doing

#### Scenario: A bundle of selections has no clause to live in

- **WHEN** a manifest declares room-profile selections for the mode it confers
- **THEN** validation fails against the current schema, because the clause is
  Phase 3's to add and the closed schema is what keeps this phase from stating a
  format it does not own — the boundary is a refusal with a message rather than
  an omission a reader has to infer

#### Scenario: Entering guest mode is exclusive with the modes it conflicts with

- **WHEN** guest mode and another mode of its `exclusive_group` are asked to be
  active together
- **THEN** the exclusivity Phase 1's `engine/modes.py` enforces refuses the pair

### Requirement: The default room templates cover the default room types, and the house template covers the house's slots

The project SHALL ship a `room-template` for each room type
`catalog/room_types.yaml` marks `default: true` — six of the twenty:
`bathroom`, `bedroom`, `driveway`, `garage`, `kitchen`, `living_room` — and a
`house-template` for the catalog's `house` entry, whose eight slots
(`contact_sensor`, `house_mode`, `leak_sensor`, `light_group`, `lock`,
`media_player`, `scene_selector`, `vacuum`) are the ones no single room owns. Each
template SHALL declare only slots its type or the house provides, read from the
catalog — a room type's `provides_slots` and the house entry's `slots`, which are
the two keys `catalog/room_types.yaml` uses, one per level; a template declaring a
foreign slot SHALL be refused, naming the
template, the slot and the source. The two kinds are the catalog's own two levels:
a `room-template` is a room type and a `house-template` is the house entry, so a
person selecting a room is choosing one of the six the catalog marks default.

#### Scenario: A template's slots are its type's

- **WHEN** the bathroom template is compared with `catalog/room_types.yaml`'s
  `bathroom` entry
- **THEN** every slot it declares appears in that entry's `provides_slots` —
  `humidity_sensor`, `light_group`, `motion_sensor` — and it declares no other

#### Scenario: The house template carries the house-scope slots

- **WHEN** the house template's slots are read
- **THEN** they are the house entry's slots and not a room's, so the two levels
  the catalog distinguishes are the two kinds the schema distinguishes

#### Scenario: A template claiming a foreign slot is refused

- **WHEN** a room template declares a slot its room type does not provide, or a
  room type's slot in the house template
- **THEN** validation fails, naming the template, the slot and the source the slot
  belongs to

#### Scenario: The templates and the catalog do not drift

- **WHEN** the room types are read and the shipped templates are read
- **THEN** every template names a type the catalog has with `default` set, the
  house template matches the `house` entry, and the check fails if a type is
  renamed or a `default` flag moves without its template

#### Scenario: The six and not the twenty

- **WHEN** the shipped room templates are counted
- **THEN** there are six, one per default-marked type, and a template for a
  non-default type — `pool`, `gazebo`, `sunroom` — is not shipped as a default
  even by accident

### Requirement: Derived packs are gated on each row's `reuse_status` and licence, and the bound is reported

A pack derived from `catalog/behaviors.yaml` SHALL name the row ids it derives
from in its `derives_from` clause, SHALL be derived only from rows whose
`reuse_status` is `reusable`, and SHALL declare a licence compatible with each
named row's. The derivation SHALL
report how many rows it used and how many it skipped, and why each was skipped,
so the set's size is a stated consequence of the corpus rather than a number a
reader has to trust. The bound is a measurement: **19 reusable rows (10 `mit`, 9
`apache_2_0`) against 64 `ideas_only` rows with `no_licence`**, so at most
nineteen rows can ground a derived pack, and no derived pack exists for the other
sixty-four.

#### Scenario: Only reusable rows ground a pack

- **WHEN** the derivation runs over the corpus
- **THEN** every pack it produces names only rows whose status is `reusable`, and
  every row it named carries a licence compatible with the pack's

#### Scenario: The skipped majority is reported with reasons

- **WHEN** the derivation reports
- **THEN** it names each skipped row and whether it was skipped for `ideas_only`
  or for a licence, and it does not silently cap its output

#### Scenario: An ideas-only row produces no pack

- **WHEN** a row whose status is `ideas_only` is the only candidate for a pack
- **THEN** no pack is produced for it, and the report names the row rather than
  the derivation inventing a hand-written pack under a derived label

### Requirement: Generated packs do not live in `packs/official/`, and the marker's rule is enforced

`packs/official/HANDWRITTEN` states its own rule — every pack file in that
directory is named in it, and every name in it is a file that exists — and that
rule means a file there is one a person wrote. A generated pack SHALL live in a
tree of its own and SHALL NOT be listed in `HANDWRITTEN`. A generated pack placed
in `packs/official/`, and a name in `HANDWRITTEN` with no file, SHALL each fail
the check.

#### Scenario: The derived tree is separate

- **WHEN** the generated packs' location is read
- **THEN** none of them is under `packs/official/`, so no generated file is
  listed as hand-written

#### Scenario: The marker's rule still holds in both directions

- **WHEN** the `handwritten-examples` check runs
- **THEN** every pack file in `packs/official/` is named in `HANDWRITTEN` and
  every name there is an existing file — the rule the marker's comment states,
  unchanged by this phase

#### Scenario: The generation is reproducible

- **WHEN** the derivation is run twice from the same corpus
- **THEN** it produces byte-identical packs, so a diff of the derived tree shows
  a corpus change and not a generation race

### Requirement: Every shipped pack passes validation and its scenario, which is the exit criterion

Each shipped pack SHALL have a scenario that exercises it in a fixture house that
binds its required slots, SHALL pass validation under the current schema, and
SHALL complete its scenario without a sandbox failure. The Bedtime button and
guest mode SHALL each have a scenario in which two installed modules propose for
one entity and exactly one command results — that is, the phase's exit criterion
exercised by a shipped pack and not only by a fixture pack.

#### Scenario: Each pack's scenario runs and passes

- **WHEN** the shipped packs' scenarios run against the fixture houses
- **THEN** every one passes, and a pack whose required slot its fixture house does
  not bind is reported as a fixture error rather than a pack failure

#### Scenario: Two shipped modules cannot fight over one light

- **WHEN** the Bedtime button and a second module both propose a command for one
  light in one tick
- **THEN** exactly one command reaches the light, the record names both the
  winner and the loser, and both outcomes are reachable by installing the two
  packs in either order

#### Scenario: The exit criterion is checked against the shipped set

- **WHEN** the exit criterion is evaluated
- **THEN** it reads the shipped packs, so "official packs pass validation and
  scenarios" is about the packs the project ships rather than about a test
  fixture named similarly
