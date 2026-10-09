# The pack manifest

A pack manifest is one document judged by four authorities, and no one of them
can answer another's question. `engine/manifest.py` is where they are asked, and
this page states what each clause is judged by, in what order, and what a refusal
says. Both examples below are whole manifests, and each is validated as written
by `tests/test_pack_manifest.py`, so an example that stopped being valid fails a
test here rather than misleading a reader.

## The four authorities

| Authority | Read from | Answers |
| --- | --- | --- |
| the schema | `schemas/pack-manifest/1.4.0.json` | what a document of this kind may be |
| the licence vocabulary | `schemas/catalog/licenses.json` | what a licence code means, and where it sits in the published order |
| the corpus | `catalog/behaviors.yaml` | what a row grants to a pack derived from it |
| the marker | `packs/official/HANDWRITTEN` | which files a person wrote rather than a derivation produced |

Every one of them is read through `engine/vocabulary.py`, the one module in the
engine that opens a frozen artifact, so this module holds no second definition of
anything the project publishes. Correcting a licence code's SPDX identifier,
changing a row's `reuse_status`, or retiring a clause is an edit to an artifact
and not a release of the engine.

## The order, and why it is an order

The schema runs first, and nothing else runs if it fails. A document the schema
refuses is not a manifest, so asking whether its `engine_api` admits the engine's
version would be asking about a field the document may not have. The spec states
this for one clause — `pack-manifest`'s "A missing or malformed range fails
validation, not the check" — and the rule is general, because a validator that
reached the range check anyway would report an *incompatibility* for a pack that
has no range at all, which is a different defect with a different remedy.

The order also makes one defect one failure. A manifest cannot be reported both
as failing to parse and as failing its derivation gate, because a document that
fails the schema never reaches the gate.

## The clauses

| Clause | Required for | Judged by |
| --- | --- | --- |
| `name`, `version` | every kind | the schema's patterns; the pair is the pack's identity |
| `description` | every kind | the schema |
| `kind` | every kind | the schema's five-value enum, and the per-kind conditionals below it |
| `engine_api` | every kind | the schema's range pattern, then the range check against the published API version |
| `license` | every kind | the schema's enum, then the derived pack's compatibility gate |
| `i18n` | every kind | the schema, then the coverage check against the names the document declares |
| `provides` | every kind | the schema; the *paths* are `engine/sandbox.py`'s, which resolves each one and checks the file is of the class declared |
| `requires_slots`, `behaviours` | `module`, `behavior` | the schema, and `engine/sandbox.py` for what a behaviour reaches and calls |
| `optional_slots` | optional | the schema; the sandbox refuses a name no vocabulary and no `slots` clause declares |
| `slots` | optional | the schema; `engine/declared_slots.py` reads the clause and the install merges it into the house's vocabulary |
| `dependencies`, `conflicts` | optional | the schema's grammar; the *resolution* against a house is the install's |
| `derives_from` | a derived pack only | the corpus and the marker |
| `options` | optional | the schema, then `engine/manifest.py`'s two cross-field checks |

`engine_api` and each `reference`'s `range` take the *same* range grammar, which
the schema publishes as a `pattern` on both, so a pack author has one range syntax
and not two. It is evaluated by `engine/semver.py` as an interval over
three-component versions: `>=1.0.0 <2.0.0` admits `1.0.0`, and `^1.2` admits
`1.9.9` and refuses `2.0.0`. The engine's own API version is published in
`schemas/engine-api/1.0.0.json` and never read from `pyproject.toml`, whose
`version` is a packaging value that moves for packaging reasons.

### What a behaviour may say

`1.2.0` fixed a behaviour's clauses to `name, trigger, condition, action,
priority, services, slots`, and every one of those named a *kind* with no value
beside it: a manifest could say `condition: state` and could not say which state.
`1.3.0` keeps those clauses and adds the values that make them say something.
`1.4.0` keeps all of it and adds one clause that reaches outside the declaring
pack: `suppresses`.

| Clause | What it is |
| --- | --- |
| `slots` | the roles the behaviour reaches. The **last** is the one `services` act through and the earlier ones are what it observes — a reading of the shipped example, not a clause anywhere |
| `services` | the calls it makes, each resolved to the state it writes through `catalog/services.yaml` |
| `scope` | `room` or `house`. `room` is what silence means, which is what every `1.2.0` pack meant because that version could not say otherwise |
| `match` | the readings the observed slot must currently hold. Absent is *not* `match: []`: a clause nobody wrote gates on nothing |
| `mode` | the house mode the behaviour enters when it acts. Additive, not alternative — a behaviour may turn lights off *and* put the house to sleep |
| `for` | the name of a `duration` option the pack declares: the matched reading must have held for that many seconds |
| `priority` | the arbitration number, from `catalog/pack-policy.yaml`'s default when unstated |
| `suppresses` | the packs this behaviour holds off while it is on — the one module-level override one pack may place over another |

`options` is a clause of the *pack* rather than of a behaviour — one typed entry
per tunable, with a title, a description, bounds and members — so two behaviours
of one pack that read `grace` read the same number. `for` names one of those by
key, and a `for` naming an option the pack does not declare as a `duration` is
the `unknown_option` refusal below.

`suppresses` is the module-level override, and it is deliberately the *only* one:
a behaviour that names another pack holds that whole module off while it is on.
Nothing about the target is written — its own switch stays where its person left
it — so the suppression is temporary by construction, and the moment the
suppressing behaviour goes off the target is back. Naming the pack rather than a
behaviour or a setting is what keeps that promise: anything finer would be one
pack editing another pack's configuration, and an uninstall could not put that
back. A pack may not name itself (`self_suppression` below), and naming a pack a
house has not installed holds nothing off rather than failing, because a manifest
does not know which house it will land in.

One setting a pack gets without declaring it: each role a behaviour acts through
becomes a per-room switch under `module.<pack>.reach.<slot>`. It is derived from
`slots` rather than written as an option, because a declared one could disagree
with what the pack actually acts on — a `reach.door_contact` for a pack that acts
on no doors is a checkbox that silently does nothing. Absent means *reached*, so
every pack written before this existed behaves exactly as it did.

## The fifteen reasons a manifest is refused

A failure carries a reason, the failing instance path (`behaviours/0/action`,
`<document>` for the whole document) and a message naming the constraint that
refused it. The reasons are finer than the *classes* a `pack-cli` report carries:
that list's `unknown term` covers `unknown_term` here, and its `derivation` covers
the four `derives_from` reasons, because a class is a summary a caller branches on
and a reason is what this module actually decided.

| Reason | What it means |
| --- | --- |
| `schema` | the current schema refused the document, structurally |
| `unknown_term` | a behaviour's trigger, condition or action is a term `behavior-vocabulary` does not publish |
| `unknown_licence` | `license` is not one of the five published codes |
| `retired_clause` | the manifest carries `min_engine_version`, which `1.2.0` retired in favour of `engine_api` |
| `engine_api_mismatch` | the declared range excludes the engine's declared API version |
| `self_dependency` | the pack names itself in `dependencies` |
| `unknown_source_row` | `derives_from` names an id no corpus row has |
| `ideas_only_source` | `derives_from` names a row whose `reuse_status` is `ideas_only` |
| `licence_too_restrictive` | the pack's code sits further up the published order than a named row's |
| `handwritten_derivation` | a marker-listed pack carries `derives_from` |
| `missing_default` | a declared name has no string in `i18n.default` |
| `override_without_default` | a locale overrides a name `i18n.default` does not declare |
| `option_mismatch` | an option's `default`, `enum` or bounds disagree with its own `type` |
| `unknown_option` | a behaviour's `for` names a `duration` option the pack does not declare |
| `self_suppression` | a behaviour's `suppresses` names its own pack |

Three of the fifteen are schema failures *reclassified*, and the reason is the
remedy. A term the vocabulary does not publish, a licence code outside the enum
and a clause the current version retired are three different things for an author
to do, so filing all three under `schema` would make the three distinctions the
class list exists for invisible. Nothing else is reclassified: a structural
failure stays `schema`, and its message names the JSON-Schema keyword that
refused it.

The last two are the one pair no schema can report, and that is the whole reason
they are reasons here rather than keywords there. Every check behind them compares
*two properties of one option* — a `default` against its `type`, an `enum` against
its `type`, a `minimum` against a `maximum` — and JSON Schema is a static document
that constrains one property at a time. `1.4.0` says so where it declares
`options`, and the Python here is where that promise is kept.

There is no `malformed_range` reason, and the absence is deliberate. The schema's
`pattern` refuses a malformed range before the range check is reached, and
`engine/semver.py` parses every string that pattern admits, so a range error
arriving here would be a defect in that module and not a fact about a pack. It is
not caught, because catching it would report a pack failure for the engine's own
bug.

## `i18n`: the two clause keys and one per behaviour

`i18n.default` carries one string per user-visible name the document declares: the
pack's own name and description, and one per declared behaviour's `name`. The
schema can require the block and its non-emptiness; it cannot require the *keys*,
because the keys are the names the document declares, so the coverage check is
here.

The two clause keys are spelled `pack` and `description`, and that spelling is the
one hand-written manifest's, in `packs/official/example-pack.yaml` — the schema
says which names need a default and cannot say what they are called. A behaviour's
key is its own `name`, which is why the first clause key is not spelled `name`: a
behaviour named `name` would collide with it.

Resolution falls back to the default when a locale has no override, and a locale
that overrides a name reads the override there and the default everywhere else.
An override with no default behind it is refused rather than resolved, because it
is a string that would disappear in every other locale.

## The derivation gate

A derived pack names the corpus rows it reproduces expression from. Three of the
four questions are the corpus's — whether the row exists, whether it may be
reproduced at all, and whether the pack's licence claims something narrower than
the row grants — and the fourth is the marker's: a pack listed in
`packs/official/HANDWRITTEN` may not carry `derives_from` at all, because a file a
person wrote reproduces no row's expression and a clause on one is a claim about
provenance that is false.

The corpus bounds what can ground a derived pack, and the bound is measured rather
than intended: all 83 rows are `reusable` (30 `mit`, 29 `public_domain`, 24
`apache_2_0`) and none is `ideas_only` under `no_licence`. The distinction still
bears: an `ideas_only` row may inform a hand-written pack, and may not be a source
of reproduced expression, so a corpus that grew one would narrow this mechanism's
reach without changing a line of it.

Licence compatibility is judged in the order `schemas/catalog/licenses.json`
publishes — `public_domain`, `mit`, `apache_2_0`, `cc_by_nc_sa`, `no_licence`,
least restrictive first. A pack's code may sit at or below each named row's, and a
code above it is refused: claiming a narrower licence over expression than its
source grants is a claim the source does not support. The comparison is a fact
about the artifact's published order and not a ranking written into the engine.

## A worked example: a hand-written module

```yaml
name: evening_lights
version: "1.0.0"
description: >-
  An evening lighting pack: a motion-triggered light that dims to a scene when the
  house is occupied and the room is dark.
kind: module
engine_api: ">=1.0.0 <2.0.0"
license: mit
requires_slots: [light_group, motion_sensor]
optional_slots: [ambient_light_sensor]
provides:
  - path: packs/example/evening_pack/evening_scene.yaml
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

The `provides` path names the artifact the pack confers and is not resolved here
— `engine/sandbox.py` resolves it, requires it to stay inside the pack's own
directory, and checks the file is of the class the entry declares. It is
illustrative in this example because the pack is not shipped.

## A device the catalog has no word for: the `slots` clause

`catalog/slots.yaml` is the house's vocabulary, and it is deliberately closed: a
vocabulary that grew a word every time a pack wanted one would stop being a
vocabulary. But a pack's point is sometimes a device the catalog has no word for.
"Warn me when the fridge has been open too long" needs a contact on the fridge
door, and no room type provides one, because a fridge is not a room.

So a manifest may declare its own devices, and three rules decide what a
declaration *means*:

- **A declared name the vocabulary already carries reuses that slot.** A pack that
  declares `door_contact` asks for the room's own door contact -- the same device
  every other pack naming `door_contact` gets. This is the default because it is
  what a person means by "the contact sensor": one room, one front door, and a
  second copy of it would be a device nobody has.
- **A declared name the vocabulary does not carry joins the house's vocabulary**
  for the houses that install the pack. `fridge_contact` becomes bindable in the
  room the pack lands in, which is what makes the pack's own requirement fillable.
- **`separate: true` gives the pack a device of its own.** It binds under a
  pack-qualified key -- `fridge_guard__fridge_contact` -- so two packs may each
  hold their own motion sensor while a third shares the room's. A pack asks for
  this when sharing would be wrong: a pack watching one specific appliance does
  not want the room's front door.

```yaml
name: fridge_guard
version: "1.0.0"
description: >-
  Watches the fridge door and raises the alarm when it has been left open.
kind: module
engine_api: ">=1.0.0 <2.0.0"
license: mit
requires_slots: [light_group]
slots:
  - name: fridge_contact
    accepts_domains: [binary_sensor]
    required: true
provides:
  - path: packs/official/fridge_guard/fridge_open.yaml
    class: automation
behaviours:
  - name: fridge_left_open
    trigger: state
    condition: state
    action: service
    priority: 30
    services: [light.turn_on]
    slots: [fridge_contact, light_group]
i18n:
  default:
    pack: Fridge guard
    description: Raises the alarm when the fridge has been left open.
    fridge_left_open: The fridge has been open too long
```

**The name is the whole of it.** The clause carries no prose of its own: a
declared name is what the device *is*, and the two rules above already make the
name do the work twice over -- it is the identity two packs share, and it is the
word the house's vocabulary gains. So a name that could mean two devices -- a
`lux` reading that might be daylight or a lamp's brightness, a contact that might
be the room's own door or the fridge's -- is a defect of the declaration, and the
remedy is the name rather than a sentence beside it. `fridge_contact` is the one
thing the device is, and it is read on the room's settings page as its own label.

`required: true` puts the device in the pack's required set beside the names
`requires_slots` carries, so the room's settings page shows one list and the
install reports one set of missing slots. Left off -- the default -- the device is
optional, which is what lets a pack offer a fridge contact and still run without
one.

This is the clause the fridge guard — a bundled example pack, kept in the working
tree rather than shipped — exists to exercise, and it is the only pack in that
corpus that declares a device of its own.

## A worked example: a derived pack

```yaml
name: smoke_watch
version: "1.0.0"
description: >-
  A derived pack: the smoke-alert row's expression, reproduced against the
  published vocabulary rather than a house.
kind: module
engine_api: ">=1.0.0 <2.0.0"
license: apache_2_0
derives_from: [security.smoke_alert]
requires_slots: [smoke_sensor]
provides:
  - path: packs/example/smoke_watch/smoke_alert.yaml
    class: automation
behaviours:
  - name: smoke_raises_the_alarm
    trigger: state
    condition: state
    action: service
    services: [notify.mobile_app]
    slots: [smoke_sensor]
i18n:
  default:
    pack: Smoke watch
    description: Raises the alarm when a smoke sensor trips.
    smoke_raises_the_alarm: Smoke raises the alarm
```

`security.smoke_alert` is a `reusable` row whose licence is `apache_2_0`, and the
pack's code is the same one, so the compatibility gate passes: a code at the
row's own rank is not more restrictive than the row's. A pack declaring `cc_by_nc_sa`
over the same row would be refused, naming both codes and the published order the
comparison used.

## What this validator does not do

It does not open a `provides` path. Whether the file is there, whether it stays
inside the pack, and whether it is of the class the entry declares are
`engine/sandbox.py`'s four rules, and asking them in two places would be two
answers to one question.

It does not resolve a dependency or a conflict against a house. Both are questions
about the set of packs actually installed, and a manifest on its own has no such
set — so an unsatisfied range, a conflicting pack and a dependency cycle are the
install's, checked when there is a house to check them against. What a manifest
can be wrong about on its own is naming *itself*, because the pack it asks for
cannot be installed beside the pack asking.

It does not refuse a pack that conflicts with itself. The spec requires that a
pack not depend on itself and says nothing about a self-*conflict*, and a
self-conflict is not caught at install either: the arriving pack is not yet in the
installed set, so a conflict it states against itself matches nothing. That is an
unchecked shape rather than a ruled-out one, and it is recorded as such.
