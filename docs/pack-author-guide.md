# Writing a pack

A pack is a description of intent the engine carries out, never a program the
engine runs. That one sentence is the whole design, and everything below follows
from it: a pack says *what it needs* and *what it may do*, and the interpreter
that acts on it can do exactly two things — resolve a slot to a bound entity and
call a service the pack declared on it. There is no branch, loop, variable or
expression anywhere in a pack, not because the validator forbids one but because
the format has no field to put one in.

This guide takes a person from an empty file to an installed pack. It is the
practical companion to four reference pages, and where a question is answered
there in full this page names the page rather than repeating it: the manifest
format is [`docs/reference/pack-manifest.md`](reference/pack-manifest.md), the
capability rules are [`docs/reference/pack-sandbox.md`](reference/pack-sandbox.md),
the install lifecycle is [`docs/reference/pack-install.md`](reference/pack-install.md),
and the version chain and licence codes are
[`docs/reference/pack-versions-and-licences.md`](reference/pack-versions-and-licences.md).
Every repository path this page spells in backticks is a file that exists in the
checkout, checked by `tests/test_reference_docs.py`, so a path that moves fails a
test here rather than misleading a reader quietly.

## What a pack is made of

A pack is one manifest file. The manifest is YAML, it lives in the pack's own
directory, and everything the pack declares — its identity, what it needs, the
artifact it hands over and the behaviours it would run — is a clause in that one
document. The artifact the pack confers is a second file the manifest pins by
path, but the manifest is the pack: nothing the engine evaluates comes from the
pinned file.

The manifest format is published and frozen as
`schemas/pack-manifest/1.3.0.json`. That schema is the normative answer to what a
manifest may be, and the loader that applies it is `engine/manifest.py`. Every
superseded version — `schemas/pack-manifest/1.2.0.json`,
`schemas/pack-manifest/1.1.0.json` and `schemas/pack-manifest/1.0.0.json` — is
retained unedited, so a manifest written
against an older format is still readable as what it was.

## The clauses, and the four authorities that judge them

`engine/manifest.py` validates a manifest against four authorities, and no one of
them can answer another's question:

| Authority | Read from | Answers |
| --- | --- | --- |
| the schema | `schemas/pack-manifest/1.3.0.json` | what a document of this kind may be |
| the licence vocabulary | `schemas/catalog/licenses.json` | what a licence code means, and where it sits in the published order |
| the corpus | `catalog/behaviors.yaml` | what a row grants to a pack derived from it |
| the marker | `packs/official/HANDWRITTEN` | which files a person wrote rather than a derivation produced |

The schema runs first and nothing else runs if it fails, because a document the
schema refuses is not a manifest and asking whether its `engine_api` admits the
engine's version would be asking about a field the document may not have.

Every clause is a string, a list of strings, or a small closed object, and the
schema states which of them a given `kind` must carry. `name`, `version`,
`description`, `kind`, `engine_api`, `license`, `i18n` and `provides` are
required of every kind; a `module` and a `behavior` must also carry
`requires_slots` and `behaviours`, while `room-template` and `house-template`
must not carry `behaviours` at all, because a template declares what a room
*has* and not what it *does*.

Three clauses are worth stating because a first draft usually gets them wrong.
`engine_api` is a semver range, not a minimum: `>=1.0.0 <2.0.0` is the range a
pack written for this engine declares, and the engine's own API version — `1.0.0`,
published in `schemas/engine-api/1.0.0.json` — must satisfy it. The range grammar
is `engine/semver.py`'s, the same one every `dependencies` and `conflicts` range
takes, so a pack author has one range syntax and not two. `i18n.default` must
carry one string for every user-visible name the pack declares: the pack's own
name under the key `pack`, its description under `description`, and one key per
declared behaviour, named after the behaviour. The two clause keys are spelled
`pack` and `description` — the schema says which names need a default and cannot
say what they are called — and a locale's override must name a key that
`i18n.default` already holds, because an override with no default behind it is a
string that disappears in every other locale.

`provides` is the one clause whose correctness the schema cannot judge. Each
entry names a repo-relative path and the class of the artifact there, and
`engine/sandbox.py` resolves the path, requires it to stay inside the pack's own
directory, and checks the file is of the class the entry declares. A path that
dangles, one that escapes the pack, and one whose file is the wrong class are
three different refusals, named `dangling_path`, `escaping_path` and
`class_mismatch`.

## A device the catalog has no word for

A pack names slots, and almost always those are words `catalog/slots.yaml`
already carries — a room is a room, so a bathroom has a `fan` and a hall has a
`motion_sensor`. The exception is a pack whose point is a device the catalog has
no word for: "warn me when the fridge has been open too long" needs a contact on
the fridge door, no room type provides one, and widening the vocabulary for every
appliance a pack might watch is what would stop it being a vocabulary.

Such a pack declares the device itself, in a `slots` clause:

```yaml
slots:
  - name: fridge_contact
    accepts_domains: [binary_sensor]
    required: true
```

Three rules decide what the declaration means, and they are the product's rules
rather than the clause's:

- **A name the vocabulary already carries reuses that slot.** A pack declaring
  `door_contact` asks for the room's own door contact, not a second one.
- **A name it does not carry joins the house's vocabulary** for the houses that
  install the pack, so the room can bind it — which is what makes the pack's own
  requirement fillable.
- **`separate: true` gives the pack a device of its own**, bound under
  `fridge_guard__fridge_contact`, so two packs may each hold their own motion
  sensor while a third shares the room's.

The name is the whole of the declaration: there is no sentence beside it to
explain what the device is for, so the name has to *be* the answer — the front
door, or the fridge? `fridge_contact` says which, and a name that could mean two
devices is a defect rather than a prompt for a clarifying sentence.
`required: true` puts the device in the pack's required set beside the names
`requires_slots` carries, so the room's settings page shows one list; left off,
the device is optional and the pack runs without one.

`packs/official/fridge-guard.yaml` is the shipped example and the only pack that
declares a device of its own.

## What a behaviour may say

A behaviour names a trigger, an optional condition and — always — an action, each
drawn from the axes `schemas/behavior-vocabulary/1.1.0.json` publishes. The
schema `$ref`s that vocabulary rather than restating its terms, so a behaviour
term outside the published set fails validation naming the pack, the axis and the
term. The vocabulary publishes fifteen triggers, eleven conditions and fourteen
actions.

Beside the three axes a behaviour carries `priority`, `services` and `slots`.
`services` and `slots` are what the sandbox reads: the services a behaviour names
*are* the pack's permission set, computed from those clauses rather than written
a second time, and the slots a behaviour names are the entities it may reach.
`priority` is the arbitration rank a behaviour competes at when two behaviours
propose for one entity in one tick; it is optional, and a behaviour that states
none takes the default `catalog/pack-policy.yaml` publishes, which is `0`.

## The forbidden terms, and why they are forbidden

The vocabulary is wider than the sandbox. Several of its actions are control
flow, so a pack may name them and the sandbox will still refuse the pack.
`catalog/pack-policy.yaml`'s `declarative_subset` lists the terms a pack **may
not** use, and it is a deny-list rather than an allow-list on purpose: an
allow-list would silently admit whatever term a future vocabulary version adds,
where a deny-list refuses only what the project has actually judged.

Forbidden on the action axis: `if` and `choose` (branching), `repeat` and
`parallel` (iteration), `variables` (assignment, and the `stop` that only means
something once a pack can branch), `wait_template` and `wait_for_trigger`
(ordering — a wait suspends until the house reaches some state, which is a branch
written as a delay). Forbidden on the condition and trigger axes: `template`,
which evaluates an expression over the house. A condition that reads state is
declarative; a condition that computes is not.

A term the vocabulary publishes and this file forbids is refused as
`forbidden_action`, `forbidden_condition` or `forbidden_trigger` — a different
failure from `unknown_action` and its siblings, so an author who wrote `choose`
is not told the project has never heard of it.

## The sandbox: what your pack can and cannot make the house do

`engine/sandbox.py` enforces four rules, all at validation, and the rules are the
boundary of what a pack can do:

1. **The declarative subset.** Every term a behaviour names is published and none
   is forbidden.
2. **Entity reach.** A behaviour reaches an entity only through a slot the pack
   declares and the behaviour itself names in its `slots` clause, in the room the
   pack is installed in. A literal entity id, device id or room name is not a
   slot; a token with a dot in it where a slot belongs is refused as
   `literal_reference`, because a slot name can never contain a dot.
3. **Declared services.** The pack's effective permissions are exactly the
   services its behaviours name. A call outside that set is `undeclared_service`.
4. **`provides` containment and class.** Every pinned path resolves to a file
   inside the pack's own directory and is of the class the entry declares.

The service rule has two published lists and they behave differently, which is
the distinction a pack author most needs to know. A service on
`catalog/pack-policy.yaml`'s `banned_services` refuses the pack, because every
entry there acts on the system that hosts the pack rather than on a device in the
house: `homeassistant.restart` and `homeassistant.stop` (restarting or stopping
the thing that runs the house), `hassio.host_reboot` and `hassio.host_shutdown`,
`hassio.addon_stdin`, the whole of `shell_command.*` and `python_script.*`
(arbitrary code or commands on the host, which is the code execution a
declarative-only pack exists to not have), and `update.install`. None of these is
tierable: a tier can permit a *flagged* service and cannot unban a banned one.

A service on `flagged_services` is dangerous and legitimate, so the pack installs
and the install result names the flag. There are three: `lock.unlock`,
`lock.open` and `alarm_control_panel.alarm_disarm`. Locking a door on a schedule
is an ordinary pack and unlocking is the one that needs saying out loud, which is
why the pair is judged by what a pack can *do* and not by the domain it touches.
A flag never becomes a refusal.

Two things the sandbox deliberately does not do. It does not check a binding's
domain against the slot it is bound to, so a `light_group` slot bound to a
`cover` passes. And it does not guard at runtime: every rule is decided at
validation, and a pack that validates cannot exceed its grant because the
interpreter has no facility to express the excess.

## The interpreter: what actually runs

Once a pack is installed, its behaviours are evaluated by
`engine/behaviours/declared.py`, the one interpreter that runs a manifest rather
than a Python class. Its whole evaluation is short, and the shortness is the point:
it reads the behaviour's action slot, and proposes each declared service through
it. `engine/behaviours/motion_lighting.py` is the shipped unit to read as a
contrast — it declares its facts, reads a lux sensor, consults the sun and checks
a dwell timer, none of which a pack can do.

Three facts about the interpreter shape what a pack author can express, and the
first is a convention rather than a clause. The **action slot is the last slot the
behaviour declares**: `slots: [motion_sensor, light_group]` with
`services: [light.turn_on]` reads trigger-first and action-last, and the last slot
is what the services act on. Nothing in the schema pairs a service with a slot, so
this is a reading of the shipped example, and a schema clause naming the action
slot would replace it. A pack behaviour is **room-scoped**, because a pack lands in
rooms and `1.2.0` declares no other scope. And a behaviour whose action slot is
bound nowhere is **inert**: it installs, and nothing happens, which is not a
failure — there is nothing to refuse.

## Two limitations that will stop a first draft

Both of these are stated plainly because an author meets them immediately, and
neither is papered over in the repository.

**A behaviour's action is a kind, not a value.** The `action` axis resolves to the
vocabulary's action enum, whose members are block kinds — `service`, `scene`,
`delay`, `device` — with no value beside them. So a manifest can say a behaviour
ends in a service call, and it cannot say *which state it sets* or *that it enters
a named mode*. `packs/official/bedtime.yaml` and `packs/official/roomba.yaml` both
record the clause they would need and cannot have: bedtime cannot express
*entering* Sleep mode, and Roomba cannot express a dispatch on the vacuum's
current state. `choose` is on the forbidden-action list for the same reason, so a
pack expresses intent rather than branching.

**Nothing evaluates a trigger or a condition.** `engine/behaviours/declared.py`
reads neither. A behaviour's `trigger` and `condition` are validated as published,
declarative terms and are then not consulted at evaluation, so **every behaviour a
pack declares fires together on every tick**, and the thing that separates them is
`priority`: the arbitration rule in `engine/arbitration.py` ranks the proposals
for one entity by negated priority first, then by the behaviour's unit id. A pack
that wants one behaviour to matter more than another says so with a number, and
nothing else ranks them.

## Validating and installing

Validation is four checks in a fixed order, ordered and run by
`openhouse/facade.py`'s `install_pack` in the simulator and by
`ha_adapter/live_modules.py`'s `install` in a live house. Nothing is written until
all four pass, which is what makes a refused install an install that did not
happen rather than one that happened partly.

1. `engine/manifest.py` — is this document a pack at all? The schema, then the
   range, licence, `i18n` and derivation checks.
2. `openhouse/packs.py`'s `check_slots` — is it a pack **for this house**? A
   required slot the vocabulary does not declare means the pack installs nowhere;
   a required slot this house binds nowhere means the pack is right and the house
   is not the one for it.
3. `engine/sandbox.py`'s `check_pack` — is what it declares permitted **anywhere**?
   The four rules above.
4. `engine/install.py` — does **the installed set** admit it? Dependencies,
   conflicts and cycles, read against the packs already in. This runs last because
   it is the only check that needs the installed set, and so the only one that can
   refuse a pack nothing is wrong with.

To validate a directory of manifests without installing anything, run
`openhouse.pack_verbs.validate_directory`, which applies the schema and then the
sandbox to every manifest it finds. The repository's own `oh-catalog validate`
runs the schema half over the shipped example packs among its other checks, but
it is `validate_directory` that runs the sandbox against a pack of your own. To
install, the simulator exposes the operation `install_pack` (declared in
`openhouse/operations.py`) whose one parameter is the path to the manifest.

**Installation is not activation.** Every behaviour a pack declares arrives
disabled, and two settings stand between an installed behaviour and acting: the
behaviour's own enable flag, `behaviour.<pack>.<name>.enabled`, and the pack's
module flag, `module.<pack>.enabled`, which gates the whole family. Both are
house settings, and no operation on the control surface enables anything — so a
tick over an installed, unenabled pack produces no proposal of the pack's.

## Versions, licences, tiers and revocation

A published format version is a file that is never written to again, and each
version names the one it replaces, with the arrow pointing backwards:
`schemas/pack-manifest/1.2.0.json` supersedes `1.1.0`, which supersedes `1.0.0`.
The engine API version is published the same way, as
`schemas/engine-api/1.0.0.json`, and it moves only when what a manifest may
*declare* changes incompatibly — never when the engine's implementation changes,
and never with `pyproject.toml`'s packaging version, which is `0.0.0` and means
nothing to a manifest.

A manifest declares its licence as one of five published codes, never as an SPDX
string: `public_domain`, `mit`, `apache_2_0`, `cc_by_nc_sa` and `no_licence`, in
that order, least to most restrictive. The codes and their SPDX identifiers sit
side by side in `schemas/catalog/licenses.json`. The order matters only to a
*derived* pack: a pack that carries `derives_from` may sit at or below each named
row's code in that order, and a code more restrictive than a row's is refused as
`licence_too_restrictive`. The corpus bounds what can ground a derivation —
`catalog/behaviors.yaml` holds 83 rows, all of them `reusable` (30 `mit`, 29
`public_domain`, 24 `apache_2_0`) and none `ideas_only` under `no_licence` — and a
`HANDWRITTEN` pack may not carry `derives_from` at all.

Packs are published in tiers. `registry/tiers.yaml` names four — `official`,
`verified`, `community` and `local` — and the field that decides behaviour is
`permits_flagged`: a `community` pack may carry no dangerous permission at all,
while an `official` or `verified` pack may, because the signature or the human
review is what a household trusts instead of reading the permission list. A
revocation is not a deletion: `registry/revocations.yaml` is the source of truth,
`registry/revoked.json` is generated from it, and the pointer and index entry
stay while the store refuses the install and says why.

## The packs the project ships

`packs/official/` holds fifteen files ending in `.yaml`, of which thirteen are
pack manifests; the other two, `packs/official/example-house.yaml` and
`packs/official/example-export.yaml`, are the hand-written examples of the house
and export-document concepts that live in the same directory. `packs/derived/`
holds one manifest per corpus *namespace*, so the checkout ships twenty-three
pack manifests.

**A derived pack is a family of rows, not a row.** The corpus namespaces its
rows by the idea they are about, and the derivation groups by that namespace
rather than emitting a card per row: every `lighting.*` row is one `lighting`
module, whose behaviours are the rows and whose per-behaviour enable keys are the
switches. A person installs one Lighting thing and turns on the parts of it they
have, instead of choosing between fifteen near-identical cards.

| Pack | `kind` | Declares |
| --- | --- | --- |
| `packs/official/example-pack.yaml` | `module` | a motion-triggered light: `requires_slots: [light_group, motion_sensor]`, `optional_slots: [ambient_light_sensor]`, one behaviour calling `light.turn_on` |
| `packs/official/bedtime.yaml` | `module` | the bedtime button: lights off, thermostat down, lock up, as three behaviours; the lock behaviour calls `lock.lock`, which is not flagged |
| `packs/official/roomba.yaml` | `module` | four behaviours over a `vacuum` slot, one of which notifies through no slot at all |
| `packs/official/bathroom_fan.yaml` | `module` | an extractor fan on a `fan` slot, on and off |
| `packs/official/fridge-guard.yaml` | `module` | the fridge guard: the only pack that declares a device of its own, a `fridge_contact` in its `slots` clause, and calls `light.turn_on` through `light_group` when it trips |
| `packs/official/bathroom.yaml`, `bedroom.yaml`, `driveway.yaml`, `garage.yaml`, `kitchen.yaml`, `living_room.yaml` | `room-template` | the slots each default room type offers, transcribed from `catalog/room_types.yaml`; no behaviours |
| `packs/official/house.yaml` | `house-template` | the slots the whole house offers, read from the room catalog's `house` entry |
| `packs/official/guest-mode.yaml` | `profile-set` | one house mode, `class: mode`, pinned to `packs/official/guest_mode/mode.yaml` |
| `packs/derived/*.yaml` | `module` | one module per corpus namespace, one behaviour per row: `cleaning`, `climate`, `laundry`, `lighting`, `media`, `modes`, `notifications`, `presence`, `security`, `system`. Each names every row it reproduces in `derives_from`, and requires no slot — a module is installable in any room and the behaviour whose device is missing is inert. |

`registry/index.json` publishes twenty-three of them, every one `official`: the
six room templates and the house, `guest_mode`, the four module packs (`bedtime`,
`roomba`, `bathroom_fan`, `fridge_guard`), `example_pack`, and the ten derived
family modules. The published set and the directory are not the same set, and the
difference is deliberate: the index is what a live panel offers —
`ha_adapter/live_modules.py` reads it and offers only what it names — and a pack
present under `packs/` but absent from the index is not offered. Here the
difference is the two example documents, which are not packs at all.

## What a pack cannot do yet, stated plainly

An author will hit each of these within an hour, so they are listed rather than
left to be discovered.

- **A behaviour's action is a state, not a value.** `services` names service
  calls and `catalog/services.yaml` maps each to the state it writes, so
  `light.turn_on` becomes "write `on`". A service with no row in that table — a
  setpoint, a notification, a `scene.turn_on` — is not an actuation the port can
  perform, and a behaviour declaring only those proposes nothing. Widening the
  port to carry a service *call* rather than a state is what would change this.
- **`choose`, loops and expressions are forbidden.** The sandbox fixes what a
  behaviour may reach to "resolve a slot and call a declared service on it", with
  no branch, variable or expression evaluation (`design.md` D5,
  `pack-sandbox`). `match`, `mode` and `for` are the three clauses that stand in
  for the branches an author would otherwise write.
- **A trigger is a tick.** Every behaviour is evaluated once per tick and the
  clauses narrow what it proposes; there is no event subscription and no
  per-device trigger, and `priority` is the only thing that separates two
  behaviours that disagree.
- **A pack may not declare behaviour code.** `provides` points at the artifacts a
  pack was extracted from, and the engine does not execute them: the manifest's
  clauses are the whole of what a pack does.

Three things an author might expect to be missing are not. A pack's behaviours
are built into live units at install (`ha_adapter/declared_units.py`), so an
enabled behaviour in an installed pack actuates a real house. A pack's top-level
`options` clause becomes the fields on a room's settings page, typed and bounded
as the author wrote them. And the roles a pack acts through become per-room
switches of their own: every slot a behaviour acts on draws an "Act on …"
checkbox beside the pack's own options, so a household can leave the thermostats
out of a bathroom pack without editing it.

## A worked manifest

The manifest below is minimal for its kind and validates against
`schemas/pack-manifest/1.2.0.json` as written: it carries the eight clauses every
kind requires, and the two more a `module` requires — `requires_slots` and
`behaviours`. Its `i18n.default` gives the pack's two clause strings and one
string for its single behaviour, and its one locale override names a key the
default holds.

```yaml
name: evening_lights
version: "1.0.0"
description: >-
  An evening lighting pack: a motion-triggered light that dims to a scene when the
  room is dark.
kind: module
engine_api: ">=1.0.0 <2.0.0"
license: mit
requires_slots: [light_group, motion_sensor]
optional_slots: [ambient_light_sensor]
provides:
  - path: packs/official/evening_lights/evening_scene.yaml
    class: automation
behaviours:
  - name: motion_lights_the_room
    trigger: state
    condition: state
    action: service
    priority: 10
    services: [light.turn_on]
    slots: [motion_sensor, light_group]
i18n:
  default:
    pack: Evening lights
    description: Lights a room when somebody walks in.
    motion_lights_the_room: Motion lights the room
  locales:
    de:
      motion_lights_the_room: Bewegung schaltet das Licht ein
```

The schema validates this document; the *sandbox* is a second question, and it
reads the `provides` path. For the pack to install, the file the entry pins must
exist at that path, inside the manifest's own directory, and be of class
`automation` — a document with a `trigger` or an `action` key. Write the manifest
to packs/official/evening_lights.yaml beside that file, and then
`openhouse.pack_verbs.validate_directory` will judge both halves. The path in the
manifest above is repo-relative, and the containment rule is what makes resolving
manifest above is repo-relative, and the containment rule is what makes resolving
it against the repository root sound: a pinned path must land back inside the
pack's own directory, so a pack pins what it confers and nothing else.
