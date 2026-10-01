# Proposal — Phase 2: The pack system

## Why

Phase 1 made the project executable: an engine that decides, a fake house it
decides against, three behaviours, a scenario runner, a control surface on three
faces, and two product rules enforced at the gate. What it did not make the
project is **extensible by anyone who is not us**. The product's central claim —
plug devices into placeholder slots and the home behaves — gets a user to a
working house, not to *their* house. The step from "the house behaves" to "the
house behaves the way I want" is a pack: a declarative document that adds a
behaviour (a Bed button), a room (a default room template), a mode bundle
(guest mode), or a whole house, without writing code, without Home Assistant
YAML, and without the document being trusted with more than the sandbox grants
it.

The raw material exists and is frozen. Phase 0 merged an **83-row behaviour
corpus** from four estates (`catalog/behaviors.yaml`), each row carrying the
repos it came from, its licence, its `reuse_status`, its `obligations` and its
retention class. It froze `schemas/pack-manifest/1.0.0.json` and `1.1.0.json` —
a manifest that names placeholders, never a house, a room or an entity, and
whose `behaviours` block resolves its terms against the published
`behavior-vocabulary/1.1.0.json` rather than restating them. It wrote
`packs/official/example-pack.yaml` by hand as the proof that a person can write
to that schema. Phase 1 then gave the engine the machinery a pack plugs into:
slot binding, the layered config resolver, modes with exclusive groups,
arbitration, the decision log, and `install_pack` at its Phase 1 scope — a
manifest is validated and its required slots are checked against the house, and
nothing more.

Phase 2 is where those meet, and it is **one change rather than several**
because its three hard parts are one part seen from three sides. The manifest
must be complete enough to state what a pack needs and what it may not do; the
interpreter must be weak enough that a hostile pack is a *validation failure*
rather than a runtime incident; and the installed result must be reconcilable,
so that two modules cannot fight over one light. A manifest without the sandbox
ships a pack format nothing can safely execute; a sandbox without the lifecycle
gives a safe thing that cannot be installed.

The boundary is already drawn by Phase 1, in a sentence this phase is written to
discharge — `control-surface/spec.md:326`: *"The capability sandbox, the
banned-service list, the `engine_api` range check and the conflict resolution
that stop two modules fighting over one light are **Phase 2's**."*

Exit: official packs pass validation and scenarios, and two modules cannot fight
over one light.

## What Changes

- **The manifest grows the clauses `spec.txt` names and the frozen schema does
  not yet carry.** `schemas/pack-manifest/1.2.0.json` supersedes `1.1.0` and adds
  `dependencies` and `conflicts` (a pack name plus a version range each),
  `engine_api` (a semver range checked against the engine's own declared
  version), `license` (a code from the published licence vocabulary, which this
  change gives an SPDX identifier each), and `i18n` (a default string per
  user-visible name, with locale overrides resolved over it). It widens
  `$defs.behaviour` too, which `1.1.0` closes to `name`/`trigger`/`condition`/
  `action`, with the three clauses the sandbox, arbitration and the trigger
  resolution need a manifest to state: `priority` (what arbitration ranks a
  competing behaviour by) and
  `services` (the services a behaviour may call, which is how a manifest declares
  its effective permissions) and `slots` (the pack-declared slots a behaviour
  reaches entities through, which a trigger reads too). A pack the derivation
  produces also carries
  `derives_from`, the corpus row ids it reproduces expression from, because
  provenance recorded only in a report on the machine that ran the derivation
  stops travelling as soon as the pack is copied. It extends
  `$defs.provided.class` too, which `1.1.0` closes at ten values, with `mode`:
  a `profile-set` pack confers a mode — guest mode is one — and among the ten
  there is no class a mode file could honestly be declared as. **BREAKING** for
  the pack schema in one respect: `kind` closes from a pattern to the five kinds
  `spec.txt` names — `module`, `room-template`, `behavior`, `profile-set`,
  `house-template` — because `1.1.0`'s own description defers exactly this
  closure until a corpus exists ("A pattern and not an enum because no corpus of
  packs exists to derive a closed set of kinds from"), and Phase 2 ships that
  corpus. The consequence is immediate and is not hidden: the hand-written
  `packs/official/example-pack.yaml` declares `kind: lighting`, which is a
  *category* and not a kind, so it stops validating and is corrected in this
  change.
- **A declarative-only interpreter with a capability sandbox.** A pack declares
  conditions, actions and services in the vocabulary; it never expresses control
  flow, and it never names an entity. The sandbox admits only what the manifest
  declared up front: entities reached through the pack's own bound slots, and
  services on the published list. A service on the banned list is a validation
  failure; a service that is merely dangerous is a *red flag* the install path
  surfaces, not a refusal, because locking a door on a schedule is a legitimate
  pack and an unlock is not.
- **The install lifecycle at Phase 2 scope**: `install_pack` grows from "validates
  and checks slots" to "resolves dependencies, refuses conflicts, checks the
  `engine_api` range, and records what it installed". Installation still SHALL
  NOT activate: every behaviour a pack declares stays disabled until something
  enables it, which is Phase 1's `product-invariants` rule and survives this
  phase unchanged.
- **Official packs**, the set that makes the corpus useful: the Bedtime button,
  the Roomba button, the bathroom fan, guest mode, the default room templates,
  and the packs derived from the merged catalogue — each derived row gated on its
  own `reuse_status` and licence, so a row that may not be reproduced does not
  become an official pack by being in the corpus.
- **Pack CLI**: `validate`, `test`, `diff-permissions` — the last one comparing a
  pack's effective permissions against a previous version's, so an update that
  quietly widens what a pack may do is visible before it is installed.
- **Triggers from a dashboard button or a physical button**, so an official pack
  is reachable the way a person reaches it, not only from a scenario.

## Capabilities

### New Capabilities

- `pack-manifest`: the pack document and what it may say. The 1.2.0 manifest —
  its five kinds, its dependencies and conflicts, its `engine_api` range, its
  SPDX licence and i18n strings — validated against the frozen schema chain, with
  vocabularies referenced and not restated, and with the licence and
  `reuse_status` of any corpus row a pack derives from resolved rather than
  assumed.
- `pack-sandbox`: what a pack may *do*, once it says it. The declarative-only
  interpreter, the declared-services list, the banned-service list, the red-flag
  permissions, and the rule that a pack reaches entities only through the slots
  it bound — so a pack that names an entity id or calls an undeclared service
  fails validation rather than exceeding its grant at runtime.
- `pack-install`: the lifecycle. Dependency resolution, conflict refusal, the
  `engine_api` range check, install and uninstall, and the reconciliation that
  makes *two modules cannot fight over one light* true — the arbitration Phase 1
  built, applied to the commands two installed packs propose in one tick.
- `official-packs`: the packs the project ships, as data and as scenarios. The
  Bedtime button, the Roomba button, the bathroom fan, guest mode, the default
  room templates, and the corpus-derived set with its licence gate; each with the
  scenario that proves it, and each passing the exit criterion.
- `pack-cli`: `validate`, `test` and `diff-permissions` on the packs in a
  directory, with the permission diff as a first-class output rather than a
  side-effect of validation.
- `pack-triggers`: a dashboard button and a physical button as pack triggers,
  bound to the pack's declared action and to nothing else, so a trigger is a way
  in and not a second execution path.

### Modified Capabilities

None. `openspec/specs/` is empty — no capability has been archived from a change
package yet, so every capability above is `ADDED` rather than a delta against an
existing spec. Where Phase 2 completes a requirement Phase 1 wrote (the four
items `control-surface/spec.md:326` hands over, and `install_pack`'s own
"validates a manifest and checks its slots, and nothing more"),
`pack-install` and `pack-sandbox` state the completed behaviour in full; the
Phase 1 requirement's `scope` note already names this phase, so the two are
consistent by construction rather than by repetition.

## Impact

- **New**: `packs/` as a real tree rather than an example directory — the
  official packs, their manifests and their scenarios; the corpus-to-pack
  derivation; `schemas/pack-manifest/1.2.0.json`; pack validation, sandbox and
  permission-diff code; pack scenarios in the committed corpus; and the CLI
  verbs.
- **Modified**: `install_pack`'s implementation and its descriptor's `scope` note
  (the note names Phase 2 as the completing phase, and that is now true);
  `packs/official/example-pack.yaml`, corrected to a real kind;
  `catalog/behaviors.yaml` is *read* by the derivation, not changed.
- **Depends on a decision this change must make and `spec.txt` does not**: packs
  declare an `engine_api` range, and nothing in the repository currently declares
  the engine's own version — `pyproject.toml` says `0.0.0`, which is not a
  version an author can write a range against. The design fixes where that
  version lives and how it moves; a range checked against `0.0.0` would be a
  check in name only.
- **Out of scope**, and named so it is not mistaken for unfinished work: the
  registry, store, tiers, signing and revocation (Phase 7); profile activation
  rules, hysteresis and the room-selection shape they drive (Phase 3), so this
  phase ships the mode a `profile-set` pack confers — the frozen
  `schemas/profile/1.0.0.json` being the whole-house bundle Phase 3 activates and
  having no room-selection section for a Phase 2 pack to fill — and neither
  declares nor activates the selections such a pack bundles; the Home Assistant adapter and the setup flow
  (Phase 4), so packs are exercised on the fake house and through the control
  surface with no HA installed; and the panel that renders a pack's settings
  (Phase 5), so this phase ships the schemas a panel will render and no
  pack-supplied JS, per `spec.txt`.
