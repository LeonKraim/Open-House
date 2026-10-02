"""The library facade: one session over one house, with no CLI and no MCP.

`control-surface`'s first requirement. The facade is the composition root's
library face and the only place `engine/` and `sim/` are wired together
(`design.md` D12) -- the engine may not import the simulator, so the package that
imports both sits beside them rather than inside either.

It holds no decision state of its own. The bindings, the modes, the enable flags,
the override records, the rate-limit windows and the clock position belong to the
engine and the simulator, and every one of them is read *through* those objects
rather than copied here, because a facade that shadowed one would be a third
place for it to disagree with the two that own it. What the facade holds is the
wiring: which house, which simulation, which engine.

Opening a session is the entry and not an eleventh operation. The ten are the
closed set `spec.txt` enumerates, and choosing a house selects which house those
ten act on rather than adding a verb none of the other faces would carry.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from engine import export as engine_export
from engine import install as pack_install
from engine import manifest as pack_manifest
from engine import sandbox as pack_sandbox
from engine import vocabulary as engine_vocabulary
from engine.adapter import ChangeContext, Fault, UnknownEntityError
from engine.behaviours import default_behaviours
from engine.behaviours.declared import DeclaredBehaviour, declared_units
from engine.binding import House, HouseScope, resolve_slot
from engine.decision_log import DecisionRecord, Outcome, ProposedCommand
from engine.declared_slots import grow_vocabulary
from engine.engine import Engine
from engine.modes import ModeSet
from engine.profiles import (
    ActivationResult,
    ActivationRule,
    ProfileActivator,
    ProfileSet,
    load_profile_schema,
)
from engine.safety import refuses
from engine.solar import Location
from openhouse import packs
from sim.fixtures import (
    DEFAULT_SEED,
    DEFAULT_STARTED_AT,
    build_fixture,
    materialise,
)
from sim.snapshot import Simulation, Snapshot, restore_snapshot
from tools.catalog import paths

if TYPE_CHECKING:
    from collections.abc import Sequence

    from engine.adapter import EntityView
    from engine.behaviours import Behaviour
    from engine.vocabulary import Vocabulary

__all__ = [
    "DEFAULT_LOCATION",
    "DEFAULT_MODES",
    "OpenHouse",
    "open_session",
]

#: Where a session is when its house is inline and names no place. A fixture
#: carries its own location, so this is reached only by a house that arrived as
#: a document -- and the sun fallback needs *a* place to compute an elevation
#: for, so the choice is made once here rather than defaulted at each call.
DEFAULT_LOCATION = Location(
    latitude=51.5074, longitude=-0.1278, time_zone="Europe/London"
)

#: The modes a session starts with. Phase 1 has no mode catalog -- the corpus
#: supplies the modes' *shape* (`schemas/mode/1.0.0.json`) and not a set of
#: them -- so the session carries the two the shipped behaviours name, in one
#: exclusive group, and a caller that wants others supplies them.
DEFAULT_MODES: tuple[Mapping[str, object], ...] = (
    {
        "name": "home",
        "description": "Somebody is in.",
        "exclusive_group": "presence",
    },
    {
        "name": "away",
        "description": "Nobody is in.",
        "exclusive_group": "presence",
    },
)


@dataclass(slots=True)
class OpenHouse:
    """One session: a house, the simulation it runs on, and the engine over it.

    Built by `open_session` rather than by hand, because the three have to agree
    -- the engine's adapter and clock are the simulation's, and the engine's
    house is the one the adapter was materialised from -- and a constructor
    taking them separately would let a caller assemble a session whose engine
    reads a clock nothing advances.
    """

    house: House
    simulation: Simulation
    engine: Engine
    source: str
    #: The repository root the session reads its artifacts from. A session
    #: resolves slots and house scopes against the vocabulary it was handed, but
    #: a pack install has to read artifacts the vocabulary does not carry -- the
    #: manifest schema, the published behaviour terms, the licence catalog -- and
    #: those are found by root and not by the vocabulary. It is a field rather
    #: than a parameter of `install_pack` because it is fixed when the session is
    #: opened: two installs in one session reading two roots would be two
    #: vocabularies disagreeing inside one house.
    root: Path
    #: The run's fixed inputs, kept as the session was opened at rather than read
    #: back off the simulation. The clock moves during a run and a restore moves
    #: it anywhere, so `started_at` here is the *start* and not the current
    #: instant -- a `RunResult` that read it from the clock would report the end
    #: of the run as its input, which is the one field a replay cannot do without.
    seed: int
    started_at: datetime
    location: Location
    behaviours: Mapping[str, Behaviour]
    modes: tuple[Mapping[str, object], ...]
    house_settings: Mapping[str, object]
    #: The profiles this session holds and the selections in force. Phase 3
    #: state, owned here rather than by the engine because the engine reads the
    #: two layers this object produces and does not hold the profiles themselves.
    profiles: ProfileSet
    #: The current `schemas/profile/` document, kept so a profile added later is
    #: validated against the same version the set was built with.
    profile_schema: Mapping[str, object]
    #: The vocabulary the session was opened with, before any pack's declared
    #: devices joined it. The house's vocabulary is grown *onto this* and never
    #: onto itself, because growth is not the only direction: uninstalling the
    #: pack that brought `fridge_contact` has to take the word back out of the
    #: house, or the room settings page goes on offering a device nothing
    #: declares. `None` only ever on a session assembled by hand -- `open_session`
    #: always passes it -- and then the house's own vocabulary is the base.
    vocabulary: engine_vocabulary.Vocabulary | None = None
    #: The manifest document of each installed pack, by pack name. Kept because a
    #: declared slot joins the house's vocabulary by being *read from a
    #: manifest*, and this session is where those manifests came from: the live
    #: path reads them out of the registry by name, and a simulator install is
    #: handed a file, so the file's document is what this session has to keep.
    #: Ordered by pack name when read, so a slot two packs declare is the same
    #: pack's on every run.
    pack_documents: dict[str, Mapping[str, object]] = field(default_factory=dict)
    #: The rule-driven activator, when the session has one. `None` until
    #: `set_profile_rules` gives it rules.
    activator: ProfileActivator | None = None
    #: The session state an import replaced, so `undo_import` can put it back.
    undo: _Undo | None = None

    # -- Reads a client makes -----------------------------------------------

    def now(self) -> datetime:
        """The virtual instant the session is at.

        The engine's clock and never the wall clock: `simulation` owns time, the
        session's only ways to move it are `advance_time` and a restore, and an
        instant read from anywhere else would make two runs of one scenario
        differ for a reason the scenario does not contain.
        """
        return self.engine.now()

    def read_entity(self, entity_id: str) -> EntityView:
        """Read one device through the port, or fail naming the id it does not hold.

        The port's own read and not a view assembled here, so what a scenario
        asserts against is what an agent driving the surface would see -- state,
        attributes and availability together, unreshaped. An id the house does
        not hold is the adapter's failure and is raised as itself rather than
        turned into `None`: "this device reads as nothing" and "there is no such
        device" are different facts, and a caller that wants the second to be an
        assertion failure is the one that knows it.
        """
        return self.simulation.adapter.read_entity(entity_id)

    # -- The registry operations --------------------------------------------

    def advance_time(self, *, minutes: float) -> tuple[DecisionRecord, ...]:
        """Move the session's clock by `minutes` and return what the tick decided.

        An advance of zero ticks nothing, which is the engine's own branch and
        not a check here: producing a record for an instant that did not pass
        would make "advancing by zero changes nothing" false at the surface.
        """
        return self.engine.advance(timedelta(minutes=minutes))

    def set_state(self, entity_id: str, state: str) -> EntityView:
        """Write `state` as the world changing, or refuse it and say so.

        A `world` origin, because a scenario or an agent changing the house is
        the world changing and the engine has to be able to tell that from its
        own actuation. A state that would unlock a `lock` or open a `cover` is
        refused here -- the surface's half of the product rule, checked with the
        engine's own `refuses` predicate rather than a second copy of it --
        recorded as `refused: unsafe`, and not written.
        """
        return self._write(entity_id, state, ChangeContext.world(), actor="set_state")

    def user_action(self, entity_id: str, state: str) -> EntityView:
        """Write `state` as a person doing it.

        The **only** operation that produces a `user`-origin change. Manual
        override detection reads that origin, so a second path to it would make
        "the user did this" mean two things; and it is the one origin the veto
        admits for an unlock, so a direct user unlock is applied.
        """
        return self._write(entity_id, state, ChangeContext.user(), actor="user_action")

    def inject_fault(self, entity_id: str, fault: str) -> EntityView:
        """Make `entity_id` report `fault`, as a `fault`-origin change."""
        self.simulation.adapter.inject_fault(
            entity_id, Fault(state=fault), context=ChangeContext.fault()
        )
        return self.simulation.adapter.read_entity(entity_id)

    def snapshot(self) -> Mapping[str, object]:
        """Return the session's runtime state as a document.

        The engine's state half is read from the engine at this instant and
        handed to the simulation, because the simulation holds the adapter, the
        clock and the stream and cannot see the engine -- the direction
        `design.md` D12 fixes. Nothing of the decision log is carried: state is
        restored and history is not.
        """
        self.simulation.engine_state = self.engine.state()
        return self.simulation.snapshot().to_document()

    def restore(self, document: Mapping[str, object]) -> None:
        """Reconstitute the session from a snapshot `snapshot` produced.

        A document whose `snapshot_version` this build does not understand is
        refused rather than partly applied, which is the difference between a
        versioned format and one that only appears to be.
        """
        snapshot = Snapshot.from_document(document)
        restored = restore_snapshot(snapshot)
        self.simulation = restored
        self.engine = self._rebuild(restored, state=restored.engine_state)

    def get_decision_log(
        self, *, window: int | None = None
    ) -> tuple[DecisionRecord, ...]:
        """The most recent decision records, oldest first within the window.

        The records are the engine's own and are not reshaped, because what an
        agent reads and what a scenario asserts against have to be the same
        record. The window defaults to the log's own bound.
        """
        size = self.engine.log.bound if window is None else window
        return self.engine.log.window(size)

    def install_pack(self, manifest: str) -> Mapping[str, object]:
        """Validate a pack, resolve it against this house, and record it.

        Phase 1's half is kept and completed rather than replaced: the manifest
        is validated against the current `pack-manifest` schema, its slots are
        checked against the house, and *then* the three things this phase adds
        run -- the sandbox, the installed set's resolution against the packs
        already in, and the registration of the pack's behaviours as disabled
        units with a record beside them. Installing enables nothing, so the
        result reports an empty set of enabled behaviours rather than leaving
        that to be assumed.

        The three checks are ordered by what each is a statement about, which is
        also the order a reader can act on them. Schema first: whether the file
        is a pack at all. The slots against the house second, which refuses a
        slot name no vocabulary declares and *returns* the names this house
        binds nowhere. The sandbox third: whether what it declares is permitted
        anywhere, which is about the repository and its policy rather than about
        who is asking. Resolution last, because it is the only step that needs
        the packs already in, and so the only one that can refuse a pack nothing
        is wrong with.

        A slot this house binds nowhere is not a refusal here either: the record
        carries it against an empty tuple, and that empty binding is what a later
        reader -- and the live path's enable gate -- reads as "installed, not yet
        wired". Refusing would have made the wiring unreachable on the live path,
        where the room's configurable devices are the modules' own slots.

        The order between the house check and the sandbox is not arbitrary and is
        not free: the sandbox refuses a declared slot no vocabulary defines, and
        so does the house check, and whichever runs second never reaches that
        branch for a *required* slot. `packs.check_slots` runs first because it
        is Phase 1's and its wording -- the distinction between a slot no house
        can supply and one this house does not -- is the one the surface has
        always reported. The sandbox keeps its own branch reachable through the
        case the house check does not read: an *optional* slot no vocabulary
        defines.

        The result grows beyond Phase 1's three keys, and that is a deliberate
        supersession rather than a drift: `pack-install` requires the record to
        answer what arrived and why, and this operation is the only place a
        caller without a snapshot can read it.

        On refusal nothing changes: every check runs before any of this session's
        state is touched, so a house that refused a pack is a house nothing
        happened to. The last three checks report everything they found; the
        schema check reports what it found once it has found something, which is
        Phase 1's shape and not this phase's to change.
        """
        path = Path(manifest)
        loaded = pack_manifest.load_manifest(path)
        name = loaded.name
        artifacts = engine_vocabulary.load_manifest_artifacts(self.root)
        published = engine_vocabulary.load_behaviour_vocabulary(self.root)

        house_vocabulary = self.house.vocabulary
        verdict = pack_manifest.validate_manifest(loaded, artifacts, house_vocabulary)
        if not verdict.ok:
            # The reason is carried beside the message, not folded into it: the
            # failure already names the constraint, and a caller branching on the
            # refusal needs the reason the schema was refused under -- `schema`,
            # `engine_api_mismatch`, `ideas_only_source` -- without parsing prose
            # for it. Dropping the token here is what would make the schema
            # indistinguishable from an incompatibility to anyone but a reader.
            raise packs.PackError(
                name,
                "; ".join(
                    f"{failure.reason}: {failure.message}"
                    for failure in verdict.failures
                ),
            )

        packs.check_slots(loaded.document, self.house)

        projected = pack_sandbox.load_pack(path)
        sandboxed = pack_sandbox.check_pack(
            projected, self.root, house_vocabulary, published
        )
        if not sandboxed.ok:
            raise packs.PackError(
                name,
                "; ".join(refusal.message for refusal in sandboxed.refusals),
            )

        arrival = _arrival(loaded)
        units = self._declared(loaded)
        try:
            resulting = pack_install.install(
                self.engine.installed,
                arrival,
                slots=self._bound(projected),
                behaviours=tuple(unit.id for unit in units),
                flags=tuple(flag.message for flag in sandboxed.flags),
            )
        except pack_install.InstallRefusedError as refusal:
            raise packs.PackError(
                name, "; ".join(failure.message for failure in refusal.failures)
            ) from refusal

        record = resulting.packs[arrival.name]
        registered = {**self.behaviours, **{unit.id: unit for unit in units}}
        self.behaviours = registered
        self.pack_documents[arrival.name] = loaded.document
        self._regrow()
        self.engine = self._rebuild(
            self.simulation, state=self._state_holding(resulting, registered)
        )
        return _install_result(record)

    def _regrow(self) -> None:
        """Rebuild the house's vocabulary from the packs this session installed.

        A pack that brings a device brings a *word*: `engine/binding.py` raises
        when a room binds a slot no vocabulary carries, and `slots.yaml` has no
        word for the button a bedtime pack declares or the contact a fridge pack
        declares. So the declared names join the house's vocabulary for the
        houses that install the pack, which is what makes the pack's own
        requirement fillable -- and it is why this is a rebuild and not an
        insertion: the merge is onto the session's *opening* vocabulary, so an
        uninstall takes the word back out with the pack.

        Rebuilt rather than mutated because the vocabulary is frozen and the
        engine holds it: the house handed to the engine is the one whose
        vocabulary the bindings were resolved against, and replacing a field
        under a live engine would leave the two disagreeing about which words
        the house has.
        """
        base = self.vocabulary if self.vocabulary is not None else self.house.vocabulary
        grown = grow_vocabulary(base, tuple(sorted(self.pack_documents.items())))
        if grown != self.house.vocabulary:
            self.house = replace(self.house, vocabulary=grown)

    def uninstall_pack(self, name: str) -> Mapping[str, object]:
        """Remove a pack, its behaviours and its record, refusing to strand a dependent.

        Not a disable, and the difference is the point: a disable is a unit that
        is still registered and still listed, and this removes both. It is also
        not one of the ten operations -- `control-surface` fixes that set and this
        phase does not add an eleventh -- so it is a composition-root method in
        the shape `add_entity` and its siblings have.
        """
        held = self.engine.installed
        record = held.get(name)
        try:
            resulting = pack_install.uninstall(held, name)
        except pack_install.InstallRefusedError as refusal:
            raise packs.PackError(
                name, "; ".join(failure.message for failure in refusal.failures)
            ) from refusal
        removed = set(() if record is None else record.behaviours)
        registered = {
            unit: declared
            for unit, declared in self.behaviours.items()
            if unit not in removed
        }
        self.behaviours = registered
        self.pack_documents.pop(name, None)
        self._regrow()
        self.engine = self._rebuild(
            self.simulation, state=self._state_holding(resulting, registered)
        )
        return {
            "pack": name,
            "installed": False,
            "behaviours": tuple(sorted(removed)),
        }

    def _state_holding(
        self,
        installed: pack_install.InstalledSet,
        behaviours: Mapping[str, Behaviour],
    ) -> Mapping[str, object]:
        """This session's runtime state, with `installed` as its installed set.

        The engine's own state is carried across rather than rebuilt empty: a
        pack arriving is not a reason for the house to forget which modes are
        active, which entities are overridden or how long a room has been quiet,
        and a rebuild that dropped them would make an install a restart. The
        installed set is the one field replaced, which is the only field the act
        changed.

        The enable flags are the exception, and they are projected rather than
        carried: the engine's state names a flag for *every* unit it registered,
        so the map an install produced is widened by one and the map an uninstall
        produced still names the unit that just left. Handing the second to the
        new engine is refused -- an engine must not be told about a unit this
        build does not register -- so the flags are filtered down to the units
        being registered, which is why the map is a parameter here rather than
        read off `self`: the projection has to be over the units the rebuild is
        about to be given, not over whatever the session holds at the moment of
        the call.
        """
        state = self.engine.state()
        registered = set(behaviours)
        flags = state.get("enable_flags")
        named = (
            {unit: flag for unit, flag in flags.items() if unit in registered}
            if isinstance(flags, Mapping)
            else {}
        )
        return {
            **state,
            "enable_flags": named,
            "installed_packs": installed.to_document(),
        }

    def _declared(
        self, loaded: pack_manifest.Manifest
    ) -> tuple[DeclaredBehaviour, ...]:
        """The pack's behaviours, as units the engine can evaluate while disabled.

        The projection itself is `engine.behaviours.declared.declared_units`, which
        the live path calls too: this method is the simulator's call site rather
        than a second builder, because two builders would be two answers to "what
        does this pack do" and the live house would give the different one. What
        is added here is the two artifacts the builder needs -- the published
        default priority, read from `catalog/pack-policy.yaml` rather than from a
        constant, and the service-to-state table, read from `catalog/services.yaml`
        so a declared service becomes the state the port writes.
        """
        return declared_units(
            loaded.name,
            loaded.document,
            default_priority=self.house.vocabulary.pack_policy.default_priority,
            service_states=engine_vocabulary.load_service_states(self.root),
        )

    def _bound(
        self, projected: pack_sandbox.Pack
    ) -> tuple[tuple[str, tuple[str, ...]], ...]:
        """The entities this house supplies for each slot the pack declares.

        Every room's binding and not one room's, because a pack lands in the
        house: which room a behaviour's evaluation is *for* is the engine's to
        decide each tick, and the record's job is to answer "what did this pack
        get hold of", which is every entity its declared slots reach. A slot the
        house binds nowhere is still recorded with an empty tuple, so "the house
        supplied nothing for this slot" is a fact in the record rather than an
        absence from it.

        House scope where the slot is one, so a house-scope slot's collection
        rule keeps its single statement in `resolve_slot`; a room slot is
        collected across the rooms in the order `House.rooms` lists them, which
        is the same order the house scope collects in.

        The key and not the written name, because that is what the record is
        joined against and what a unit reads (`engine/declared_slots.py`): a
        declaration the pack asked to hold separately reaches the house under a
        pack-qualified key, and a record keyed by the name the manifest wrote
        would answer "the house supplied nothing" for a device it had bound.
        """
        return tuple(
            (projected.bound_key(slot), self._reached(projected.bound_key(slot)))
            for slot in sorted(projected.declared_slots)
        )

    def _reached(self, slot: str) -> tuple[str, ...]:
        """The entities the house binds `slot` to, whatever scope it is a slot of."""
        house = self.house
        if slot in house.house_scope_slots:
            return resolve_slot(house, HouseScope(), slot).entities
        return tuple(
            entity_id
            for room in house.rooms
            if (entity_id := room.bindings.get(slot)) is not None
        )

    def export_config(self) -> Mapping[str, object]:
        """Write this session's house configuration out.

        The house and its bindings, in the shape `schemas/house/1.0.0.json`
        freezes. Deliberately not the Phase 3 export: no registry id beside an
        entity id, no export version, no profile, because Phase 1 has no device
        registry to re-link against and inventing one would be a fiction.
        """
        return {
            "name": self.house.name,
            "rooms": [
                {
                    "id": room.id,
                    "name": room.name,
                    "type": room.type,
                    "bindings": {
                        slot: {"entity_id": entity_id}
                        for slot, entity_id in room.bindings.items()
                    },
                }
                for room in self.house.rooms
            ],
            "house_scope": {"slots": list(self.house.house_scope_slots)},
        }

    def import_config(self, document: Mapping[str, object]) -> None:
        """Reconstitute this session's house from an exported configuration.

        The house is replaced and the devices it binds that the adapter does not
        already hold are added; a device the adapter holds and the new house
        does not bind is left in place, because removing a device is the
        house-control `remove_entity`'s own call and not a side effect of
        importing. The engine is rebuilt over the new house with no carried
        state: a house configuration says nothing about modes, enable flags or
        override records, so carrying them across would attribute one house's
        runtime history to another.
        """
        imported = House.from_document(document, vocabulary=self.house.vocabulary)
        materialise(imported, self.simulation.adapter)
        self.house = imported
        self.engine = self._rebuild(self.simulation, state=None)

    # -- Profiles -----------------------------------------------------------

    def add_profiles(
        self, documents: Sequence[Mapping[str, object]]
    ) -> tuple[str, ...]:
        """Validate and add profiles to the session, returning their names.

        Adding a profile does not select it: a profile a house holds and a room
        a profile is on are different facts, and the phase keeps them apart the
        same way `install_pack` keeps installation from activation.
        """
        added = [self.profiles.add(document).name for document in documents]
        return tuple(added)

    def select_profile(
        self, *, room_id: str, axis: str, name: str
    ) -> Mapping[str, object]:
        """Put a room on a profile for an axis, and apply the new layers."""
        self.profiles.select(room_id, axis, name)
        if self.activator is not None:
            self.activator.note_selection(room_id, axis, self.now())
        self._apply_profiles()
        return {"room": room_id, "axis": axis, "profile": name}

    def clear_profile(self, *, room_id: str, axis: str) -> Mapping[str, object]:
        """Take a room off an axis' profile, and apply the new layers."""
        self.profiles.clear(room_id, axis)
        if self.activator is not None:
            self.activator.note_selection(room_id, axis, self.now())
        self._apply_profiles()
        return {"room": room_id, "axis": axis, "profile": None}

    def select_house_profile(self, name: str) -> Mapping[str, object]:
        """Put the house on a profile, applying the room selections it bundles.

        Named `select` rather than `activate` so the surface keeps its promise not
        to expose a method whose name reads as turning a behaviour on: this moves
        the house onto a profile, and a profile carries no route to the engine's
        evaluation list that the layered config does not already have.
        """
        self.profiles.activate_house_profile(name)
        self._apply_profiles()
        return {"house_profile": name, "selections": dict(self.profiles.selections())}

    def active_profiles(self) -> Mapping[str, object]:
        """The selections in force, the house profile and the modes they carry."""
        return {
            "selections": dict(self.profiles.selections()),
            "house_profile": self.profiles.house_profile,
            "modes": self.profiles.modes(),
        }

    def set_profile_rules(self, rules: Sequence[ActivationRule]) -> None:
        """Give the session the rules its activator steps, replacing any before."""
        self.activator = ProfileActivator(self.profiles, rules)

    def run_profile_rules(
        self, *, states: Mapping[str, str] | None = None
    ) -> ActivationResult:
        """Step the activation rules once at the current instant.

        The trigger states a rule reads are supplied by the caller or read off
        the house by entity id; a rule naming an entity the house does not hold
        sees no state and so does not want its profile, which is the safe answer
        rather than an error in a background step.
        """
        if self.activator is None:
            raise packs.PackError(
                "profiles", "no activation rules are set on this session"
            )
        result = self.activator.step(
            self.now(),
            active_modes=sorted(self.engine.modes.active),
            states=states if states is not None else self._trigger_states(),
        )
        if result.activations:
            self._apply_profiles()
        return result

    def _trigger_states(self) -> Mapping[str, str]:
        """The state of every entity a trigger rule names, where the house holds it."""
        rules = () if self.activator is None else self.activator.rules
        states: dict[str, str] = {}
        for rule in rules:
            entity_id = rule.entity_id
            if entity_id is None or entity_id in states:
                continue
            try:
                states[entity_id] = self.simulation.adapter.read_entity(entity_id).state
            except UnknownEntityError:
                # A trigger rule naming an entity this house does not hold simply
                # does not fire; naming it in a step is not an error.
                continue
        return states

    def _apply_profiles(self) -> None:
        """Re-resolve the profile layers after a selection changed.

        The modes the profiles carry are activated first, on the engine that is
        about to be replaced, so the state the new engine adopts names them; the
        profile layers themselves arrive through the rebuild, because the
        resolver's layers are fixed at construction and a profile that changed a
        setting must be visible to the very next tick rather than to the next
        restart.
        """
        for name in self.profiles.modes():
            self.engine.modes.activate(name)
        state = dict(self.engine.state())
        flags = state.get("enable_flags")
        if isinstance(flags, Mapping):
            state["enable_flags"] = self._with_profile_flags(flags)
        self.engine = self._rebuild(self.simulation, state=state)

    def _with_profile_flags(self, flags: Mapping[str, object]) -> dict[str, object]:
        """The carried enable flags, with the active profiles' enables folded in.

        The flags the state carries were resolved under the *previous* profile
        layer, so a behaviour a profile now enables would otherwise be pinned to
        its old value. The profile's enables are additive -- a profile enables a
        behaviour and never disables one -- so folding them in is a join, and the
        engine's own `_keep_flag` then leaves the layers alone.
        """
        enabled = self.profiles.enabled_house()
        per_room = self.profiles.enabled_rooms()
        folded: dict[str, object] = {}
        for unit_id, entry in flags.items():
            if not isinstance(entry, Mapping):
                folded[unit_id] = entry
                continue
            house = entry.get("house")
            rooms = entry.get("rooms")
            room_flags: dict[str, object] = {}
            if isinstance(rooms, Mapping):
                for room_id, value in rooms.items():
                    room_flags[room_id] = (
                        True
                        if unit_id in per_room.get(str(room_id), frozenset())
                        else value
                    )
            folded[unit_id] = {
                "house": True if unit_id in enabled else house,
                "rooms": room_flags,
            }
        return folded

    # -- Export, scrub, relink and import -----------------------------------

    def export_backup(self) -> Mapping[str, object]:
        """Write the whole configuration out, as a versioned export document.

        The Phase 1 `export_config` is a different, smaller document -- the house
        and its bindings, with no registry id and no profiles -- and it is kept
        for the operation registry. This is the backup the round trip is defined
        on, which is why it is a method and not a replacement.
        """
        return engine_export.export_backup(
            house=self.house,
            profiles=self.profiles,
            modes=self.modes,
            exported_at=self.now().isoformat(),
        )

    def scrub_template(
        self,
        *,
        name: str,
        description: str,
        version: str = "1.0.0",
        exported_at: str | None = None,
    ) -> engine_export.TemplatePack:
        """Produce a shareable pack: the house's shape, with no device ids."""
        return engine_export.scrub_template(
            self.export_backup(),
            name=name,
            version=version,
            description=description,
            exported_at=exported_at,
        )

    def dry_run_import(self, document: Mapping[str, object]) -> engine_export.Diff:
        """Report what importing `document` would change, changing nothing."""
        return engine_export.dry_run(self.export_backup(), document)

    def relink_import(
        self, document: Mapping[str, object], registry: Mapping[str, str]
    ) -> engine_export.RelinkResult:
        """Rewrite a document's entity ids from a registry-id map, reporting gaps."""
        return engine_export.relink(document, registry)

    def import_backup(self, document: Mapping[str, object]) -> Mapping[str, object]:
        """Replace this session's configuration from an export, keeping an undo.

        A snapshot is taken before anything changes, so a wrong import is one
        call away from being undone rather than a house to rebuild by hand. The
        document is migrated first, so an export an older build wrote imports.
        """
        previous = _Undo(
            house=self.house,
            profiles=self.profiles,
            modes=self.modes,
            snapshot=self.snapshot(),
        )
        backup = engine_export.import_backup(
            document,
            vocabulary=self.house.vocabulary,
            profile_schema=self.profile_schema,
        )
        materialise(backup.house, self.simulation.adapter)
        self.house = backup.house
        self.profiles = backup.profiles
        self.modes = backup.modes if backup.modes else self.modes
        self.undo = previous
        self.engine = self._rebuild(self.simulation, state=None)
        return {
            "house": backup.house.name,
            "rooms": len(backup.house.rooms),
            "profiles": len(backup.profiles.profiles),
            "undo": True,
        }

    def undo_import(self) -> Mapping[str, object]:
        """Put the session back as it was before the last import."""
        undo = self.undo
        if undo is None:
            raise packs.PackError("import", "there is no import to undo")
        self.house = undo.house
        self.profiles = undo.profiles
        self.modes = undo.modes
        self.restore(undo.snapshot)
        self.undo = None
        return {"house": undo.house.name, "undone": True}

    # -- The house-control facility -----------------------------------------

    def add_entity(
        self,
        entity_id: str,
        state: str,
        *,
        attributes: Mapping[str, object] | None = None,
    ) -> EntityView:
        """Add a device to the house. The port's own operation, not an operation."""
        self.simulation.adapter.add_entity(
            entity_id,
            state,
            attributes=dict(attributes) if attributes else None,
            context=ChangeContext.world(),
        )
        return self.simulation.adapter.read_entity(entity_id)

    def remove_entity(self, entity_id: str) -> None:
        """Remove a device from the house."""
        self.simulation.adapter.remove_entity(entity_id, context=ChangeContext.world())

    def set_availability(self, entity_id: str, available: bool) -> None:
        """Mark a device available or unavailable, apart from its state."""
        self.simulation.adapter.set_availability(
            entity_id, available=available, context=ChangeContext.world()
        )

    def restart(self) -> None:
        """Return the house to its defined startup condition.

        Not how a snapshot is restored: a restart is the adapter's startup
        condition, in which an unavailable device is still unavailable and not
        off, while a restore returns the house to the state it held.
        """
        self.simulation.adapter.restart(context=ChangeContext.world())

    # -- Internals -----------------------------------------------------------

    def _write(
        self, entity_id: str, state: str, context: ChangeContext, *, actor: str
    ) -> EntityView:
        """Apply one write, or refuse it and record the refusal.

        `refuses` is `engine/safety.py`'s own predicate rather than a second
        statement of the rule, so the surface cannot drift from the gate. A
        refusal is recorded rather than silently dropped: "nothing happened" and
        "the engine refused this" are different facts and only the record tells
        them apart -- and `control-surface` requires the refusal to *be*
        `refused: unsafe`, which is a record's outcome and not a return value.

        Three fields of that record are deliberately not what a behaviour's
        record carries. `actor` is the operation, because the evaluating unit
        here is the surface and the log's `actor` names what asked. `rule` is
        `None`, because no corpus concept was matched -- the veto is a product
        rule, and naming a concept would attribute the refusal to a rule that
        never ran. `slot` is empty, because a slot is a behaviour's vocabulary
        and a direct write is not a behaviour's.

        A write that is *applied* records nothing, and that is the difference
        between this path and the engine's: the engine's decisions are what the
        log is a log of, and a direct write is the world or a person changing
        the house rather than the engine deciding anything. What reads a manual
        change is the adapter's own `last_origin`, which the write establishes.
        """
        if refuses(entity_id, state, context=context):
            self.engine.log.append(
                DecisionRecord(
                    at=self.engine.now(),
                    actor=actor,
                    inputs=(),
                    rule=None,
                    commands=(
                        ProposedCommand(
                            slot="",
                            entities=(entity_id,),
                            action=state,
                            context=context,
                        ),
                    ),
                    outcome=Outcome.REFUSED_UNSAFE,
                    state_delta=(),
                )
            )
            return self.simulation.adapter.read_entity(entity_id)
        self.simulation.adapter.actuate(entity_id, state, context=context)
        return self.simulation.adapter.read_entity(entity_id)

    def _rebuild(
        self, simulation: Simulation, *, state: Mapping[str, object] | None
    ) -> Engine:
        """A fresh engine over `simulation`, keeping this session's wiring."""
        return _engine(
            house=self.house,
            simulation=simulation,
            location=self.location,
            behaviours=self.behaviours,
            modes=self.modes,
            house_settings=self.house_settings,
            profile_settings=self.profiles.effective_house(),
            profile_room_settings=self.profiles.effective_rooms(),
            state=state,
        )


@dataclass(frozen=True, slots=True)
class _Undo:
    """The configuration an import replaced, kept whole so it can be put back.

    Not only a snapshot: `restore` rebuilds the adapter and the engine, but the
    house, the profiles and the modes are the session's own wiring and a restore
    does not touch them. Keeping all four together is what makes an undo an undo
    rather than a half-restored session.
    """

    house: House
    profiles: ProfileSet
    modes: tuple[Mapping[str, object], ...]
    snapshot: Mapping[str, object]


def _arrival(loaded: pack_manifest.Manifest) -> pack_install.Arrival:
    """What the validated manifest declares, as the installed set reads it.

    The unwrapping happens here rather than in `engine/install.py` so that module
    never reads a manifest: `Arrival` is a value with four fields and a resolver
    that took a document would have a second reading of the manifest format to
    keep in step with the validator's.
    """
    document = loaded.document
    return pack_install.Arrival(
        name=loaded.name,
        version=str(document.get("version", "")),
        digest=pack_install.digest(document),
        dependencies=_references(document, "dependencies"),
        conflicts=_references(document, "conflicts"),
    )


def _references(
    document: Mapping[str, object], field: str
) -> tuple[pack_install.Reference, ...]:
    """One manifest reference list -- `dependencies` or `conflicts` -- unwrapped.

    A projection and not a check: the schema has already refused a row that is
    not a name and a range, and this runs only on a manifest that validated. The
    list is read the way `engine/manifest.py` reads its own rows -- absent or not
    a list is empty, a row that is not a mapping is not a row -- because the two
    readings have to agree: a row this dropped is a constraint the resolver would
    never check, and the validator would have checked it as though it existed.
    """
    rows = document.get(field)
    if not isinstance(rows, list):
        return ()
    references: list[pack_install.Reference] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = row.get("name")
        span = row.get("range")
        if isinstance(name, str) and isinstance(span, str):
            references.append(pack_install.Reference(name=name, range=span))
    return tuple(references)


def _install_result(record: pack_install.InstalledPack) -> Mapping[str, object]:
    """What `install_pack` reports: the pack, and the facts that explain it.

    The first three keys are Phase 1's result unchanged -- the pack, that it is
    in, and that nothing it declared is on -- and everything after them is the
    record, reshaped only enough to be a document. That is deliberately more than
    a boolean: `pack-install` requires a caller to be able to see what it just
    gave a pack without opening a snapshot, and the record is the only place
    those facts exist.
    """
    return {
        "pack": record.name,
        "installed": True,
        "enabled": (),
        "version": record.version,
        "digest": record.digest,
        "change": record.change,
        "replaced": record.replaced,
        "slots": dict(record.slots),
        "dependencies": tuple(
            {"pack": reference.name, "range": reference.range, "version": version}
            for reference, version in record.dependencies
        ),
        "conflicts": tuple(
            {"pack": reference.name, "range": reference.range}
            for reference in record.conflicts
        ),
        "behaviours": record.behaviours,
        "flags": record.flags,
    }


def open_session(
    *,
    house: str | Mapping[str, object],
    vocabulary: Vocabulary,
    root: Path | None = None,
    seed: int = DEFAULT_SEED,
    started_at: datetime = DEFAULT_STARTED_AT,
    location: Location | None = None,
    modes: Sequence[Mapping[str, object]] | None = None,
    house_settings: Mapping[str, object] | None = None,
    behaviours: Mapping[str, Behaviour] | None = None,
) -> OpenHouse:
    """Open a session against `house`, fixed at `seed` and `started_at`.

    `house` is a fixture's name or an inline house document, which is the same
    choice a scenario's `given` block makes and the same one `import_config`
    produces -- a house document. Either way the house's devices reach the
    adapter through `add_entity` and never as a baked snapshot (`design.md`
    D11), so a session cannot begin in a state the fake could not have produced.

    The seed and the start instant are the run's fixed inputs and are what make
    a session replay; a session opened at the same inputs and driven the same
    way decides the same. The vocabulary is required rather than loaded here for
    the reason `build_fixture` gives: choosing a repository root is the caller's
    decision, not the library's.

    The location comes from the fixture, which is where a fixture house is, and
    an explicit `location` overrides it -- the sun branch is computed from it, so
    a caller driving a house at a different latitude is asking for a different
    run rather than for a contradiction, and silently keeping the fixture's would
    make the argument a no-op for exactly the houses that have one.

    `root` is where the artifacts live and defaults to the repository this
    package is in, because the library and the tree are one checkout while a
    session's *vocabulary* is a parameter. A caller that built the vocabulary
    from somewhere else passes that somewhere here too; a caller that took the
    default has one answer rather than two.
    """
    where_root = paths.ROOT if root is None else root
    chosen = behaviours if behaviours is not None else default_behaviours()
    chosen_modes = tuple(DEFAULT_MODES if modes is None else modes)
    profile_schema = load_profile_schema(where_root)
    profiles = ProfileSet([], schema=profile_schema)

    if isinstance(house, str):
        fixture = build_fixture(
            house, vocabulary=vocabulary, seed=seed, started_at=started_at
        )
        simulation = fixture.simulation
        built = fixture.house
        where = fixture.location if location is None else location
        settings: Mapping[str, object] = {
            **fixture.house_settings,
            **({} if house_settings is None else house_settings),
        }
        source = str(fixture.name)
    else:
        simulation = Simulation.start(seed=seed, started_at=started_at)
        built = House.from_document(house, vocabulary=vocabulary)
        materialise(built, simulation.adapter)
        where = DEFAULT_LOCATION if location is None else location
        settings = {} if house_settings is None else house_settings
        source = "inline"

    engine = _engine(
        house=built,
        simulation=simulation,
        location=where,
        behaviours=chosen,
        modes=chosen_modes,
        house_settings=settings,
        profile_settings=profiles.effective_house(),
        profile_room_settings=profiles.effective_rooms(),
        # Nothing is adopted. `state` is what a restore resumes from, and it is
        # checked against the house it was taken from; the empty engine state a
        # fresh `Simulation` carries is not a snapshot, and handing it over
        # would fail that check rather than start a run.
        state=None,
    )
    return OpenHouse(
        house=built,
        simulation=simulation,
        engine=engine,
        vocabulary=built.vocabulary,
        source=source,
        root=where_root,
        seed=seed,
        started_at=started_at,
        location=where,
        behaviours=chosen,
        modes=chosen_modes,
        house_settings=settings,
        profiles=profiles,
        profile_schema=profile_schema,
    )


def _engine(
    *,
    house: House,
    simulation: Simulation,
    location: Location,
    behaviours: Mapping[str, Behaviour],
    modes: Sequence[Mapping[str, object]],
    house_settings: Mapping[str, object],
    profile_settings: Mapping[str, object] | None = None,
    profile_room_settings: Mapping[str, Mapping[str, object]] | None = None,
    state: Mapping[str, object] | None,
) -> Engine:
    """Build the engine over a simulation, from the session's wiring."""
    return Engine(
        adapter=simulation.adapter,
        house=house,
        clock=simulation.clock,
        location=location,
        modes=ModeSet(list(modes), vocabulary=house.vocabulary),
        behaviours=behaviours.values(),
        house_settings=house_settings,
        profile_settings=profile_settings,
        profile_room_settings=profile_room_settings,
        state=state,
    )
