# Installing a pack

Installing a pack is four checks in a fixed order, a record that survives a
snapshot, and a set of behaviours that arrive disabled. `engine/install.py` is
the resolution half, `engine/sandbox.py` is the capability half,
`engine/manifest.py` is the schema half, and `openhouse/facade.py`'s
`install_pack` is the one place the four are ordered. This page states what each
check answers, what a refusal says, and what the record holds.

`tests/test_pack_install.py` asserts the field names below against the record the
code builds, so a field renamed in one place and not the other fails a test here
rather than leaving this page describing a record that no longer exists.

## The four checks, in the order they run

| Order | Check | Answers | Refuses by |
| --- | --- | --- | --- |
| 1 | [`engine/manifest.py`](../../engine/manifest.py) | is this document a pack at all? | `MalformedManifestError`, or a `ManifestResult` whose failures name the clause and the constraint |
| 2 | [`openhouse/packs.py`](../../openhouse/packs.py) | is it a pack **for this house**? | `PackError` naming the slot |
| 3 | [`engine/sandbox.py`](../../engine/sandbox.py) | is what it declares permitted **anywhere**? | `PackError` naming every refusal the rules found |
| 4 | [`engine/install.py`](../../engine/install.py) | does **the installed set** admit it? | `PackError` naming every failure, folded from `InstallRefusedError` |

The order is what makes each refusal one defect with one remedy. A document the
schema refuses is not a manifest, so asking whether its slots resolve would be
asking about fields it may not have. The house check runs before the sandbox
because it is the first question whose answer is a fact about a *house* rather
than a file — and because both refuse a declared slot no vocabulary defines, so
whichever ran second would never reach that branch for a required slot. The
sandbox keeps its own branch reachable through the case the house check does not
read: an **optional** slot no vocabulary defines.

Resolution runs last because it is the only check that needs the packs already
installed, and so the only one that can refuse a pack nothing is wrong with.

Nothing is written until all four have passed, which is what makes a refused
install an install that did not happen rather than one that happened partly.

## What the record holds

| Field | Is | Read by |
| --- | --- | --- |
| `name` | the pack's name | every refusal message, and the installed set's key |
| `version` | the version the manifest declared | the dependency check, the change classification |
| `digest` | `sha256:` over the manifest document's canonical JSON | `matches`, to answer whether a later edit happened without a bump |
| `slots` | the slots the pack declared, each with the entities the house supplied | a person asking what the pack got hold of |
| `dependencies` | each declared range **and the version that satisfied it** | the re-check after a version change; a person asking what answered |
| `conflicts` | the conflicts the pack declared | a *later* pack's arrival, which is the only moment it is consulted |
| `behaviours` | the unit ids the pack registered | uninstall, and a restore's check that this build registers them |
| `flags` | the dangerous-but-legitimate permissions the pack carries | a person about to grant them |
| `replaced` | the version this install replaced, or `None` | the change classification |
| `change` | `install`, `upgrade`, `downgrade` or `reinstall` — derived from `replaced`, not stored | a person asking what just happened |

The digest is over the document's canonical JSON rather than the file's bytes, so
reindenting a manifest is not an edit to a pack. The version and the digest
together are the report a person acts on: a manifest changed without a version
bump compares **equal** by version and **unequal** by
`InstalledPack.matches`, and that pair of answers is the whole of "the version
says no change and the digest says otherwise".

The record lives in the engine-owned half of the snapshot, under
`engine_state.installed_packs`, so `snapshot` is how "what is installed here, and
why" is answered after the fact — and `install_pack`'s own result carries the
same fields, so a caller without a snapshot need not take one to read what it
just installed.

## Installation is not activation

A pack's behaviours arrive **disabled**, and two flags stand between an installed
behaviour and acting:

- the behaviour's own enable flag, `behaviour.<pack>.<name>.enabled`, whose
  absence reads as `False`;
- its pack's module flag, `module.<pack>.enabled` — the key
  `engine/behaviours/base.py`'s `module_enable_key` builds, with the pack as the
  family — which gates the whole family ahead of the unit's own flag.

Both are settings, supplied in the `house_settings` a session is opened with, and
the enabling act is the one that names the behaviour. Nothing in the operation
registry enables anything — `control-surface`'s ten have no such parameter, and
`tests/test_operations.py` asserts that by reading parameter names rather than
trusting the set. A tick over an installed, unenabled pack therefore produces no
proposal of the pack's, which is what "arriving" and "acting" being two acts
means when the arriving thing is third-party data.

## Two gaps this phase does not close

Both are recorded here rather than papered over, because each is a decision about
a boundary this phase does not own.

**A declared service is proposed as the command's action, and the port writes it
as a state.** A pack's behaviour says `services: [light.turn_on]` and the
interpreter proposes exactly that string, because the sandbox's rule is that a
behaviour calls a *declared service* on the entity its slot resolved to. But
`ProposedCommand.action` is documented as the state to write, and
`HouseAdapter.actuate` takes a state — so what reaches `light.foyer` is the
string `light.turn_on`, which is not a state any light has. Nothing in this
repository maps one to the other, and a service-to-state table invented here
would be a vocabulary living in code rather than in an artifact. Closing this
means either widening the port with a service call the Home Assistant adapter can
implement directly, or publishing a `service → state` artifact the engine reads.
**Until it is closed, a pack's behaviour cannot be enabled in a real house**, and
the phase's exit criterion — two packs proposing for one light, reduced to one
command — cannot be demonstrated end to end.

**There is no in-session enabling act.** Enable flags are `house_settings`, fixed
when a session is opened, so a pack installed *during* a run cannot be enabled in
that run. A caller can open a session with the flags set and then install, but a
scenario that installs two packs and then lets them compete has no way to switch
them on. The enabling act the spec requires is separate and named; it is not yet
addressable after the session has started.

## Upgrades, downgrades and uninstall

An install of a pack already present **replaces** its record rather than adding a
second: the installed set is keyed by name, so a second arrival is a version
change and the record's `replaced` names the version it displaced. Because a
dependency is recorded as its declared range *and* its satisfying version, the
dependency check runs again over the resulting set — so a downgrade that moves a
pack out of a dependent's range is refused as a breaking downgrade, naming the
dependent, rather than leaving the set in a state its own record cannot explain.

`uninstall` refuses when a pack still installed depends on the one being removed,
naming the dependent. A successful uninstall removes the pack's record *and* its
behaviours, so a later tick produces nothing of its doing. It is not one of the
ten operations: `control-surface` fixes that set, and this phase adds no
eleventh, so it is a composition-root act beside `add_entity`.
