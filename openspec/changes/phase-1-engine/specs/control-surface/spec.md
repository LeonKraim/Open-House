# Spec Delta — control-surface

## Purpose

The agent's entry point and the only place the engine and the simulator are
wired together. One library facade composes `engine/` with `sim/` — the
composition root `design.md` D12 places in a new top-level package rather than
in `engine/`, because a facade that lives in the engine would make the engine
import the simulator and break the one direction the port exists to keep — and
one set of ten operations is defined **once** in a registry and surfaced three
ways: as ordinary Python calls, as a CLI, and as an MCP server. Nothing in
`engine/` or `sim/` depends on this capability; it depends on both, and it is
the seam the exit criterion is phrased against: *an AI agent can build a house,
run scenarios, read the log, and iterate with no HA installed.*

The design's D12 records the choice not to put the facade in `engine/`; what it
left open is the composition root's **name**, and this capability names it
`openhouse/`, a top-level package beside `engine/`, `sim/` and `ha_adapter/`.
The name is fixed here rather than left to an implementer for the same reason
D12 gives: the addition to Phase 0's frozen layout is a decision, and a decision
without a name is an accretion. `architecture-invariants` requires the named
modules to exist; it does not forbid a package besides them, and the extension
of its purity scan to `openhouse/` is stated below so the new package is held to
the same boundary rather than exempted from it.

Three properties shape every requirement. **The registry is the single
definition of the surface**, so an operation added once appears on all three
faces and no face can offer something the library cannot (`design.md` D10): the
scenario runner's operation step verbs, the CLI's operation subcommands and the MCP
server's operation tools are all *names drawn from the registry*, and a check
compares the three sets so they cannot drift. The CLI and the MCP server each
additionally carry the scenario runner's one client entry point — `scenario run`
and `run_scenario` (`scenario-runner`) — which drives the same ten operations
rather than adding an eleventh and is excluded from the registry comparison
because it is a client of the registry and not a member of it. **The facade is
thin and stateless of its own**: it opens a
session against a house, forwards each operation to the engine or the
simulator that owns it, and holds no decision state the engine does not own —
which is what keeps the three surfaces honest about what they are. And **the
surface is where the two product rules are most exposed**, so it is where they
are stated for the caller: it exposes no operation that turns a behaviour on by
accident, and no operation but a direct `user_action` can produce a command that
unlocks a lock or opens a cover.

The concrete tunables and the tuning of a scenario are content and not
structure, and nothing here pins them: the facade is opened with a house, a seed
and a clock, and the operations advance that clock and change that house.

## ADDED Requirements

### Requirement: The composition root is a new top-level package that wires the engine to the simulator
`openhouse/` SHALL be a top-level Python package beside `engine/`, `sim/` and
`ha_adapter/`, carrying a `py.typed` marker and importable on its own. It SHALL
be the only package that imports both `engine/` and `sim/`, and it SHALL NOT be
imported by either: `engine/` and `sim/` SHALL gain no import of `openhouse/`,
directly or transitively. The purity scan `architecture-invariants` installs
over `engine/` SHALL be extended to cover `openhouse/` as well, with the
opposite direction of the one it already checks — the composition root may
import the engine and the simulator and may import no third-party module outside
the declared dependency list, and neither the engine nor the simulator may
import it.

The direction is the whole point of putting the facade here (`design.md` D12).
The engine's dependency graph has exactly one outward edge — the `HouseAdapter`
port (`house-adapter`) — and a facade inside `engine/` would give it a second
edge to the simulator, which would drag `sim/` into every engine test and make
the Phase 4 real adapter's parity claim uncheckable. Adding the package is a
permitted extension of Phase 0's frozen layout rather than a hole in it: the
`architecture-invariants` layout requirement enumerates the modules that must
*exist*, not the only modules that may, and the name `openhouse/` is recorded
here so the extension is reviewed as a decision.

#### Scenario: The engine imports the composition root
- **WHEN** any file under `engine/` or `sim/` imports `openhouse`, directly or
  under a guarded import
- **THEN** the purity scan fails and names the file and the import

#### Scenario: The composition root imports an undeclared dependency
- **WHEN** a file under `openhouse/` imports a third-party module absent from the
  root `pyproject.toml` `dependencies`
- **THEN** the purity scan fails and names the file and the module

#### Scenario: The composition root is importable and typed
- **WHEN** `openhouse` is imported on its own in a checkout with no Home
  Assistant installed
- **THEN** the import succeeds and the package carries a `py.typed` marker

### Requirement: The library facade opens a session and drives a house with no CLI, no MCP and no Home Assistant
`openhouse/facade.py` SHALL define `OpenHouse`, the library facade. A session
SHALL be opened against a house supplied either as an inline house document
validating against `schemas/house/1.0.0.json`, as a named fixture
(`minimal`, `messy`, `large`, `no_lux` — `simulation`), or as a document
produced by `export_config`; it SHALL be opened with a virtual-clock start and a
seed, and both SHALL be fixed for the session so its run replays. The facade
SHALL be drivable end to end — open a session, drive the operations, read the
decision log, snapshot and restore — in a test that imports no CLI module and no
MCP module and runs with Home Assistant absent.

House construction is the session's **entry**, not an eleventh operation: the
ten operations are the closed set `spec.txt` enumerates and the scenario runner
speaks (`design.md` D10), and opening a session against a house selects which
house those ten act on rather than adding a verb none of the other faces would
carry. The facade SHALL hold no decision state of its own — no bindings, no
modes, no enable flags, no clock position — because the engine (`engine-core`)
and the simulator (`simulation`) own those, and a facade that shadowed them
would be a third place for them to disagree.

#### Scenario: The facade drives a house without the surfaces
- **WHEN** a session is opened against the `minimal` fixture and its operations
  are driven from a Python test
- **THEN** the house is built, time advances, a decision record is readable and a
  snapshot restores, with no CLI module and no MCP module imported

#### Scenario: The facade holds no decision state
- **WHEN** the facade is inspected for bindings, modes, enable flags, override
  records, rate-limit windows or a clock position
- **THEN** none is present, because each is owned by the engine or the simulator
  and read through it

#### Scenario: The facade needs no Home Assistant
- **WHEN** `openhouse.facade` is imported and driven in a checkout where
  `homeassistant` is not installed
- **THEN** the session opens and drives, so the library half of the exit
  criterion holds without HA present

### Requirement: The operation registry is the closed, single definition of the surface
`openhouse/operations.py` SHALL define an `Operation` descriptor — carrying at
least the operation `name`, its `parameters` schema, its handler, and its
`scope` note — and a mapping `OPERATIONS` from name to descriptor that holds
**exactly** these ten operations and no others: `advance_time`, `set_state`,
`user_action`, `inject_fault`, `snapshot`, `restore`, `get_decision_log`,
`install_pack`, `export_config`, `import_config`. The set is closed and SHALL be
checked against `spec.txt`'s enumeration, so an operation that exists in neither
the registry nor the enumeration cannot be reached and an enumeration entry with
no registry operation fails the build. Every surface SHALL name its operations
by drawing them from `OPERATIONS` rather than by declaring its own list, and a
check SHALL fail when a surface exposes an operation the registry does not carry
or omits one it does. The composition root additionally exposes, outside the
registry, the **house-control** facility — `add_entity`, `remove_entity`,
`set_availability` and `restart`, the control face of the `HouseAdapter` port
(`house-adapter`) that builds and provokes the house — and the check compares
decision-driving operations only, ignoring the house-control facility in the
manner it already ignores the `run_scenario` client entry point.

The registry exists because three surfaces describing the same ten operations is
three places for them to disagree, and the disagreement that matters is silent:
a CLI subcommand the MCP server lacks, or a parameter accepted on one face and
dropped on another, is a difference an agent only discovers by trying. Defining
the descriptor once — including its parameter schema — makes "the three faces
offer the same thing" a set comparison rather than a review.

#### Scenario: An operation is added outside the registry
- **WHEN** an operation is added to the CLI, the MCP server or the facade without
  an entry in `OPERATIONS`
- **THEN** the registry check fails and names the operation and the surface

#### Scenario: The registry and the enumeration drift
- **WHEN** `OPERATIONS` carries a name `spec.txt` does not enumerate, or omits one
  it does
- **THEN** the registry check fails and names the divergent operation

#### Scenario: A parameter exists on one face only
- **WHEN** a CLI subcommand or an MCP tool accepts a parameter the registry's
  descriptor for that operation does not declare
- **THEN** the thin-adapter check fails and names the operation and the parameter

### Requirement: The CLI is a thin adapter over the registry and adds no behaviour of its own
`openhouse/cli.py` SHALL define a Typer application installed as the console
script `oh-house`, and it SHALL expose exactly one subcommand per registry
operation, generated from `OPERATIONS` rather than hand-written. Each subcommand
SHALL print a working `--help`, SHALL accept `--json` to emit its result as a
single JSON object, and SHALL exit non-zero on failure with a message naming the
offending operation and its cause. A session SHALL be selected by global options
naming the house — a house file, a fixture name, or nothing for an empty session
— with the seed and the clock start; the ten operation subcommands SHALL carry no
logic beyond binding their arguments to the registry descriptor and calling the
facade.

The CLI SHALL also expose the scenario runner's entry point as the `scenario run`
command (`oh-house scenario run <path>`, `scenario-runner`), a **client entry
point** composed over the ten operations rather than a registry operation:
building a house is the session's entry and running a scenario drives the same
operations an agent calls, so neither is an eleventh operation. The registry
equality below is over the operation subcommands, and the scenario entry point is
excluded from it for that reason.

The CLI is thin on purpose and the thinness is checked: a CLI that computed
anything the library did not would be a path the scenario runner and the MCP
client never take, so a scenario passing through the CLI would prove nothing
about the loop the agent actually runs (`design.md` D10). The subcommands are
generated from the registry so that adding an operation is one edit, not three.

#### Scenario: A registry operation has no subcommand
- **WHEN** the CLI's subcommand set is compared with `OPERATIONS`
- **THEN** every registry operation has a subcommand, and a missing one fails the
  thin-adapter check naming the operation

#### Scenario: A subcommand exists that the registry does not
- **WHEN** the CLI declares an *operation* subcommand whose name is not a registry
  operation
- **THEN** the thin-adapter check fails and names the subcommand, because the CLI
  may offer nothing the library cannot; the `scenario run` entry point is a client
  entry point and is not an operation subcommand

#### Scenario: A subcommand carries logic
- **WHEN** any `oh-house` subcommand computes a value, applies a policy or
  changes state other than by forwarding to the facade
- **THEN** the thin-adapter check fails and names the subcommand

#### Scenario: Help runs for every subcommand
- **WHEN** `oh-house <operation> --help` is invoked for each registry operation
- **THEN** each prints its usage and exits zero, so no subcommand is registered
  but unusable

### Requirement: The MCP server is a thin adapter over the same registry
`openhouse/mcp_server.py` SHALL define an MCP server using the official MCP
Python SDK, exposed under the server name `openhouse`, and it SHALL register
exactly one tool per registry operation, each tool named by the operation's
`name` and each tool's input schema derived from that operation's registry
descriptor. It SHALL also register the scenario runner's entry point as the tool
`run_scenario` (`scenario-runner`), a **client entry point** composed over the ten
operations rather than a registry operation, so an agent can run a scenario over
MCP as well as through the CLI. The server SHALL be drivable by an in-process
client in a test, so no transport process is required to prove the surface, and it
SHALL hold no logic beyond forwarding a tool call to the facade — no MCP-only
operation, no MCP-only parameter and no MCP-only default beyond the `run_scenario`
entry point.

The MCP server is the face an AI agent drives, so its correctness is the exit
criterion's: a tool that behaves differently from the library call the scenario
runner makes is a tool whose scenarios test a different system. Deriving the
tool schema from the registry descriptor is what keeps the two schemas — the
CLI's arguments and the MCP tool's inputs — one schema seen twice.

#### Scenario: A tool has no registry operation
- **WHEN** the MCP server registers an *operation* tool whose name is not a
  registry operation
- **THEN** the thin-adapter check fails and names the tool; the `run_scenario`
  entry point is a client entry point and is not compared as an operation

#### Scenario: A registry operation is not a tool
- **WHEN** the MCP server's tool set is compared with `OPERATIONS`
- **THEN** every registry operation is a tool, and a missing one fails the check
  naming the operation

#### Scenario: The tools are drivable in process
- **WHEN** an in-process MCP client calls each tool
- **THEN** each forwards to the facade and returns its result, with no subprocess
  and no network transport

### Requirement: The scenario runner's verbs are the control surface's verbs
The scenario runner (`scenario-runner`) SHALL have no step verbs of its own: the
verbs in a scenario's `when` block SHALL be the control surface's verbs and
nothing else, so a scenario and an agent's session are written in one vocabulary
and cannot drift (`design.md` D10). The control surface's verbs are the ten
registry operations together with the house-control facility the composition root
exposes outside the registry — `add_entity`, `remove_entity`, `set_availability`
and `restart` (`house-adapter`) — which the registry equality below excludes for
the same reason it excludes the `run_scenario` client entry point. A check SHALL
assert that every registry operation has a matching step verb and every
house-control verb has a matching step verb, failing when a step verb resolves to
neither. A `then` block's assertions remain the runner's own and are not
operations.

This is why the registry is stated in this capability rather than inferred: the
runner is a *client* of the surface, and the identity of the two vocabularies is
what makes the exit criterion's "run scenarios" and "iterate" the same loop
rather than two. A bespoke runner vocabulary (`turn_on`, `wait`, `expect`) was
rejected because it would be a second list to keep in step with the first, and a
scenario written in it would exercise a path the agent's own session never takes.

#### Scenario: A step verb no control-surface member backs
- **WHEN** a scenario uses a `when` verb that resolves to no registry operation
  and no house-control verb
- **THEN** the vocabulary check fails and names the verb, and the run reports the
  unknown verb rather than ignoring the step

#### Scenario: An operation with no step verb
- **WHEN** a registry operation has no corresponding scenario step verb
- **THEN** the vocabulary check fails and names the operation

#### Scenario: A scenario and a session share the vocabulary
- **WHEN** the same operation sequence is expressed once as a scenario and once
  as a sequence of facade calls
- **THEN** the two runs produce the same device states and the same decision
  records, because the verbs are the operations

### Requirement: Each operation states its scope, and a partial operation names the phase that completes it
Every `Operation` descriptor SHALL carry a `scope` note, and an operation whose
behaviour in this phase is a deliberate subset of the feature `spec.txt` assigns
to a later phase SHALL name that phase in its `scope`, so neither an agent nor a
later author mistakes the Phase 1 half for the whole. `install_pack`,
`export_config` and `import_config` are the three, and their scope is stated in
their own requirements below. The `scope` note SHALL be reachable from the
surface — a `--help` string or a tool description derived from the descriptor —
so the boundary is visible to a caller and not only to a reader of this spec.

Stating scope per operation is the same device as the Non-Goals section of
`design.md`, moved to the point of use: the control surface is the one place a
caller meets these operations, so it is the one place the "this is Phase 1 of a
Phase 2 feature" caveat can be delivered where it is needed.

#### Scenario: A partial operation does not name its phase
- **WHEN** a descriptor for `install_pack`, `export_config` or `import_config`
  omits the phase that completes it
- **THEN** the scope check fails and names the operation

#### Scenario: A scope note is unreachable from a surface
- **WHEN** an operation is invoked through the CLI or the MCP server and its
  `scope` note is not obtainable from that surface
- **THEN** the scope check fails and names the operation and the surface

### Requirement: `install_pack` validates a manifest and checks its slots, and nothing more
`install_pack` SHALL accept a pack manifest that validates against the current
`pack-manifest` schema (currently `schemas/pack-manifest/1.1.0.json`) and SHALL
check the manifest's `requires_slots` against the house the session is opened
against, resolving each slot name through `catalog/slots.yaml`. A manifest whose
required slot the house cannot supply SHALL fail naming the slot; a manifest
that validates and whose slots the house supplies — `packs/official/
example-pack.yaml`, whose required slots are `light_group` and `motion_sensor` —
SHALL install. `install_pack` SHALL NOT enable any behaviour the pack declares:
installing a pack that carries behaviours leaves every behaviour disabled until
something enables it (`product-invariants`), because activation is opt-in and
installation is not activation.

The capability sandbox, the banned-service list, the `engine_api` range check and
the conflict resolution that stop two modules fighting over one light are
**Phase 2's** (`design.md` Non-Goals), and this operation's `scope` note SHALL
name Phase 2 as the phase that supplies them. The boundary is drawn here because
Phase 1 has the corpus, the schemas and the slot vocabulary that make a manifest
*checkable*, and lacks the interpreter that makes it *runnable*; shipping the
check now is what lets the example pack be validated end to end without pretending
the sandbox exists.

#### Scenario: A required slot the house cannot supply
- **WHEN** a pack whose `requires_slots` names a slot the house binds nowhere is
  installed
- **THEN** installation fails and names the pack and the slot

#### Scenario: The example pack installs
- **WHEN** `packs/official/example-pack.yaml` is installed into a house that
  binds `light_group` and `motion_sensor`
- **THEN** installation succeeds, and the pack's declared behaviours are still
  disabled afterwards

#### Scenario: A manifest fails its schema
- **WHEN** a manifest is installed that does not validate against the current
  `pack-manifest` schema
- **THEN** installation fails and names the manifest and the schema violation

#### Scenario: Install is mistaken for enable
- **WHEN** a session installs a pack and then advances time with no enable flag
  set
- **THEN** no behaviour acts, because installation does not enable anything

### Requirement: `export_config` and `import_config` round-trip the house configuration
`export_config` SHALL produce the **house configuration** — the house and its
slot bindings — as a document validating against `schemas/house/1.0.0.json`, and
`import_config` SHALL reconstitute a session's house from such a document. For
any house, exporting and then importing SHALL yield a house whose configuration
is identical to the original, and the round-trip SHALL be asserted as a property
over generated houses rather than as a single example.

This is deliberately **not** the Phase 3 export document. The Phase 3 export
carries registry ids beside entity ids and is consumed by a dry-run re-link
wizard with an undo snapshot; this operation carries what the agent built and
nothing more, and its `scope` note SHALL say so. The two are separated because
Phase 1 has no device registry to re-link against, and a Phase 1 export that
tried to carry registry ids would either invent them or silently drop them — the
first is a fiction and the second is a lossy round-trip that would pass this
phase's identity check and fail Phase 3's (`design.md` Non-Goals).

#### Scenario: A house round-trips to an identical configuration
- **WHEN** a house is exported with `export_config` and re-imported with
  `import_config`
- **THEN** the imported house's rooms, types and bindings equal the original's,
  and the property holds over generated houses

#### Scenario: An exported document validates against the house schema
- **WHEN** `export_config` runs
- **THEN** its output validates against `schemas/house/1.0.0.json`

#### Scenario: A Phase 3 export is smuggled in
- **WHEN** `export_config` emits a field outside the house schema — a registry id
  for re-link, an export version, a profile — or `import_config` accepts one
- **THEN** the round-trip check fails and names the field, so this operation
  cannot grow into the Phase 3 document by accident

### Requirement: `snapshot` and `restore` round-trip the runtime state
`snapshot` SHALL return the runtime `Snapshot` document `simulation` composes —
the port's entity slice combined with the engine's state and the simulator's
positions — and `restore` SHALL reconstitute a session from such a document so
that the restored session continues to decide identically (`simulation`'s
restore-and-replay requirement). The operation SHALL carry the document's
`snapshot_version` and SHALL fail restore on a document whose version it does not
understand, rather than restoring a subset. Neither operation SHALL carry the
decision log, which is history rather than state.

Surfacing snapshot and restore as operations is what lets an agent checkpoint a
long session and resume it identically, and it is deliberately **not** how the
scenario runner expresses a restart: restoring returns the house to the state it
held, whereas a restart returns it to a defined startup condition in which an
unavailable device is not off (`house-adapter`), so the runner expresses a
mid-scenario restart through its house-control `restart` step and never through
`restore` (`scenario-runner`). Refusing an unknown `snapshot_version` at restore
is the difference between a versioned format and one that only appears to be:
a restore that guessed would resume from a state the format never promised.

#### Scenario: A snapshot round-trips through the surface
- **WHEN** a session takes a snapshot, restores it, and advances with the same
  seed
- **THEN** the decisions after the restore equal those an uninterrupted session
  made, and the document's `snapshot_version` is present

#### Scenario: A snapshot version is unknown
- **WHEN** `restore` is given a document whose `snapshot_version` the build does
  not understand
- **THEN** the restore fails and names the version rather than restoring a subset

#### Scenario: The log is not carried
- **WHEN** a snapshot is taken after a run has appended records and read back
- **THEN** it carries no decision record

### Requirement: `get_decision_log` returns a window of the log in order
`get_decision_log` SHALL return a window of `engine-core`'s decision records —
the most recent records, oldest first within the window — with the window size
resolved through the config resolver and defaulting to the log's own bound. The
returned records SHALL be the normative record shape `engine-core` defines and
SHALL NOT be reshaped or summarised by the surface, so what an agent reads and
what a scenario asserts against are the same record.

The operation is the exit criterion's "read the log" made concrete, and it is the
reason the field set is normative rather than conventional: the runner and the
agent parse these records, so a surface that renamed a field or dropped a null
would break the one artifact that tells *acted* from *never ran* (`design.md`
D2). Reading a window rather than the whole log is the surface half of the log's
bound: an operation that returned every record would make a long session's read
grow without limit.

#### Scenario: The window returns the most recent records in order
- **WHEN** `get_decision_log` is read with a window size after more evaluations
  than the window
- **THEN** it returns the most recent records, oldest first, and no earlier one

#### Scenario: A record is reshaped by the surface
- **WHEN** a record returned by `get_decision_log` omits a field of `engine-core`'s
  normative set, renames one, or adds one
- **THEN** the record-shape check fails and names the field, because the agent and
  the scenario runner parse the record unchanged

### Requirement: The session's write operations carry the change origins the engine reads
The surface SHALL produce each non-engine change origin through exactly one
operation: `set_state` SHALL write with a `world` origin — a scenario or an agent
changing the house is the world changing, which the engine must be able to tell
from its own actuation — `user_action` with a `user` origin, and `inject_fault`
with a `fault` origin, so the three write operations map one-for-one onto the
three non-engine origins `house-adapter` defines; the fourth origin, `engine`, is
produced only by the engine's own actuation through the port and by no surface
operation. `user_action` SHALL be the **only** operation that can produce a
`user`-origin change; no other operation, and no surface wrapper, SHALL construct
one.

The mapping is stated at the surface because it is the surface a scenario and an
agent drive, and manual-override detection (`engine-core`) reads the origin the
port carries: an operation that wrote with the wrong origin would either trip
override detection on a device no human touched or fail to trip it on one that
was touched. Making `user_action` the single `user` path is what makes "the user
did this" mean something a scenario can rely on, and a check SHALL assert that
no other operation in the registry produces a `user` origin.

#### Scenario: A write carries the wrong origin
- **WHEN** `set_state` or `inject_fault` produces a change whose origin is `user`
- **THEN** the origin check fails and names the operation and the origin

#### Scenario: The origin mapping is one-to-one
- **WHEN** the operations that mutate the house are enumerated
- **THEN** `set_state` is `world`-origin, `inject_fault` is `fault`-origin, and
  `user_action` is the only `user`-origin path, while the `engine` origin is
  produced only by the engine's own actuation

#### Scenario: A user action suppresses a behaviour through the surface
- **WHEN** a scenario enables motion lighting and calls `user_action` on a room's
  light group
- **THEN** the subsequent behaviour command is recorded `overridden`, because the
  `user_action` is what set the last writer's origin to `user`

### Requirement: The surface cannot turn a behaviour on, and cannot unlock or open except as a user
No operation SHALL enable a behaviour or a module: a session opened against a
freshly built house SHALL run nothing until a behaviour is enabled through
configuration, and no operation in the registry SHALL be a back door to that
enable flag (`product-invariants`, `engine-core`). And no operation other than
`user_action` SHALL be able to produce a command that unlocks a `lock` or opens a
`cover`: `set_state`, `inject_fault` and `install_pack` SHALL NOT be able to
smuggle such a command past the engine's veto, and `user_action`, when it is a
direct user unlock, SHALL be admitted. A non-user unlock reached through any
other operation SHALL appear in the decision log as `refused: unsafe` and SHALL
not reach the house.

These are the two product rules at their most exposed surface. The gate that
refuses a non-user unlock lives in `engine-core` and the rule itself is stated by
`product-invariants`; what this capability owes is that no operation it exposes
offers a route around them — an `install_pack` that enabled a pack's behaviours
on install, or a `set_state` that could unlock a door because it is "just a
write", would each be the product rule failing at the one place a caller can
reach.

#### Scenario: A session runs nothing until enabled
- **WHEN** a session is opened against any fixture and its operations are driven
  across a motion, a quiet timeout and an emptying with no enable flag set
- **THEN** no light changes, because no operation enabled a behaviour

#### Scenario: An operation unlocks a door
- **WHEN** any operation other than `user_action` attempts a state change that
  would unlock a `lock` or open a `cover`
- **THEN** the engine refuses it, the record's outcome is `refused: unsafe`, and
  no write reaches the house

#### Scenario: A user unlocks directly
- **WHEN** `user_action` unlocks a `lock`
- **THEN** the command is applied, because a user origin is admitted

#### Scenario: install_pack enables a behaviour
- **WHEN** `install_pack` installs a pack carrying behaviours and the house then
  runs
- **THEN** every installed behaviour is still disabled, because installation is
  not activation

### Requirement: Every operation is deterministic and the surface adds no state
Running the same operation sequence against the same house, seed and clock
position SHALL produce identical results and an identical decision log, and the
surface SHALL introduce no nondeterminism of its own — no wall-clock read, no
unordered iteration presented as order, no default drawn from the environment.
The facade SHALL read time only through the session's virtual clock and
randomness only through the simulator's seeded stream, so a session replayed from
a snapshot and a session run afresh from the same inputs are indistinguishable
through the surface.

This is `design.md` D3 carried to the face an agent drives. The exit criterion is
an agent that *iterates*, and iteration means a changed input produces a changed
output and an unchanged input produces an unchanged one; a surface that consulted
the wall clock or the process environment would make a scenario's failure depend
on when it ran, which is exactly what the phase's determinism rules exist to
forbid. The check is a scan over `openhouse/` for a wall-clock read, the same
scan `simulation` runs over its two packages.

#### Scenario: The same sequence replays
- **WHEN** an operation sequence is driven twice at the same seed and clock
- **THEN** the two runs produce identical device states and identical decision
  records

#### Scenario: The surface reads the wall clock
- **WHEN** any file under `openhouse/` reads `time.time`, `datetime.now` or a
  monotonic clock
- **THEN** the determinism scan fails and names the file and the call

#### Scenario: The surface holds state across operations
- **WHEN** the facade caches a value the engine or the simulator owns and a later
  operation reads the stale copy
- **THEN** the determinism check fails, because the two runs no longer match

### Requirement: The exit criterion is this capability's acceptance, proven in CI with no Home Assistant
CI SHALL run the end-to-end agent loop in a checkout where Home Assistant is not
installed: open a session through the facade, build a house from a fixture, drive
the operations to provoke a decision, read a decision record with
`get_decision_log`, and iterate — change an input and observe a changed record —
using the library, and again once through the CLI and once through the MCP
server. The loop SHALL also run the scenario runner and read its JSON failure
output. `openspec validate phase-1-engine --strict` SHALL pass. The check SHALL
fail when the no-HA guard is removed, so the acceptance is an assertion that runs
rather than a claim in prose.

This is `spec.txt`'s Phase 1 exit criterion — *an AI agent can build a house, run
scenarios, read the log, and iterate with no HA installed* — stated as the one
requirement that all the others serve. It is claimed here because the control
surface is the only capability the whole loop passes through: the port and the
fake carry it, the engine decides in it, the behaviours act in it and the runner
asserts on it, but an agent reaches all of them through this facade or not at
all. It is checked in CI rather than demonstrated by hand because "with no HA
installed" is a property of the environment the loop runs in, and an
unasserted property of an environment is a hope.

#### Scenario: The loop runs without Home Assistant
- **WHEN** the agent loop is executed in a checkout with `homeassistant` not
  installed
- **THEN** it builds a house, drives a decision, reads the record and iterates
  successfully, through the facade, the CLI and the MCP server

#### Scenario: The no-HA guard is removed
- **WHEN** the guard that fails the loop when Home Assistant is present is
  removed
- **THEN** the acceptance check fails, so the guard is exercised rather than
  merely declared

#### Scenario: The package does not validate
- **WHEN** `openspec validate phase-1-engine --strict` is run
- **THEN** it passes, so the package this capability belongs to is itself
  well-formed
