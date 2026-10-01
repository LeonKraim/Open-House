"""Profiles: room and house deltas over the base, and the rules that pick them.

Phase 3's first three requirements. A profile is a *delta* -- the settings it
changes -- so a room has one base and a stack of profiles over it rather than a
set of complete configurations that must agree with each other. The five layers
the phase names are already the resolver's order, and this module is what fills
the two that were reserved: the house-global profile entries and the room
profile's, both above the room layer and below a temporary override.

Two facts about the shape, and each is why the code is not a dictionary:

- **Axes make profiles simultaneous.** A room profile declares the axis it
  governs -- lighting, climate, media -- and a room holds at most one profile per
  axis. Lighting and Climate are then active at once because their axes differ,
  and a second lighting profile is a *replacement* of the first rather than a
  conflict, because the axis says which question each answers.
- **A house profile bundles room selections.** "Vacation" is not a room's delta;
  it is the set of room profiles every room should be on, which is why activating
  one is `select` for each room it names rather than a delta applied at house
  scope. A house profile may also carry a house-scope delta of its own, and that
  is the one part of it that is not a room selection.

Activation is the second half. A rule names a room, an axis, the profile it wants
and how it is decided -- a person, a schedule, a house mode, a trigger -- and
`ProfileActivator.step` is what turns "the rule wants this" into "the room is on
this now". Three brakes stop a rule from flapping, and they are the phase's own
requirement rather than a nicety: `hysteresis` is how long the rule's condition
must hold before it fires, `min_dwell` is how long a selection must stay before it
may change, and cycle detection refuses a change that would return to a profile
the room has recently been on. All three are reported when they block, because a
profile that did not change for a reason is not the same as one that had no rule
asking.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from enum import StrEnum
from pathlib import Path
from typing import cast

import jsonschema

from engine.behaviours.base import enable_key
from tools.catalog.schemas import current_version, load_versions

__all__ = [
    "Activation",
    "ActivationResult",
    "ActivationRule",
    "Blocked",
    "BlockedReason",
    "Profile",
    "ProfileActivator",
    "ProfileError",
    "ProfileKind",
    "ProfileSet",
    "RuleKind",
    "load_profile_schema",
]

PROFILE_CONCEPT = "profile"


class ProfileError(Exception):
    """Base for the failures this module defines."""


class InvalidProfileError(ProfileError):
    """A profile document that does not validate against the frozen schema."""

    def __init__(self, index: int, message: str) -> None:
        super().__init__(f"profile {index} does not validate: {message}")
        self.index = index
        self.message = message


class UnknownProfileError(ProfileError):
    """A profile name the set does not hold, or one used on the wrong axis."""

    def __init__(self, name: str, detail: str) -> None:
        super().__init__(f"the profile {name!r} {detail}")
        self.name = name
        self.detail = detail


class ProfileKind(StrEnum):
    """What a profile is selected *as*."""

    ROOM = "room"
    HOUSE = "house"


@dataclass(frozen=True, slots=True)
class Profile:
    """One profile: its axis (a room profile's) and the deltas it applies."""

    name: str
    kind: ProfileKind
    description: str
    axis: str | None
    deltas: Mapping[str, object]
    enabled_behaviours: tuple[str, ...]
    modes: tuple[str, ...]
    selections: Mapping[str, Mapping[str, str]]

    @classmethod
    def from_document(
        cls, document: Mapping[str, object], *, schema: Mapping[str, object], index: int
    ) -> Profile:
        """Validate one profile against the frozen schema and project it."""
        _validate(document, schema, index)
        kind = ProfileKind(cast("str", document["kind"]))
        return cls(
            name=cast("str", document["name"]),
            kind=kind,
            description=cast("str", document["description"]),
            axis=_optional_str(document.get("axis")),
            deltas=_mapping(document.get("deltas")),
            enabled_behaviours=_names(document.get("enabled_behaviours")),
            modes=_names(document.get("modes")),
            selections=_selections(document.get("selections")),
        )

    def to_document(self) -> dict[str, object]:
        """The profile as a document, identical for equal profiles."""
        document: dict[str, object] = {
            "name": self.name,
            "kind": str(self.kind),
            "description": self.description,
        }
        if self.axis is not None:
            document["axis"] = self.axis
        if self.deltas:
            document["deltas"] = dict(self.deltas)
        if self.enabled_behaviours:
            document["enabled_behaviours"] = list(self.enabled_behaviours)
        if self.modes:
            document["modes"] = list(self.modes)
        # A house profile carries `selections` whether or not it names any room:
        # the schema requires the field for the house kind, so omitting an empty
        # one would write a document that no longer validates as what it is.
        if self.selections or self.kind is ProfileKind.HOUSE:
            document["selections"] = {
                room: dict(axes) for room, axes in self.selections.items()
            }
        return document


class ProfileSet:
    """Every profile a house holds, and which one each room is on per axis.

    The set owns both halves because a selection is meaningless without the
    profile it names: `select` is the only way to move a room, and it refuses a
    profile that is not a room profile or whose axis is not the one being set, so
    the two facts cannot disagree in the state this object keeps.
    """

    def __init__(
        self, profiles: Sequence[Mapping[str, object]], *, schema: Mapping[str, object]
    ) -> None:
        self._schema = schema
        self._profiles: dict[str, Profile] = {}
        for index, document in enumerate(profiles):
            profile = Profile.from_document(document, schema=schema, index=index)
            if profile.name in self._profiles:
                raise UnknownProfileError(profile.name, "is declared twice")
            self._profiles[profile.name] = profile
        self._selections: dict[str, dict[str, str]] = {}
        self._house_profile: str | None = None

    def add(self, document: Mapping[str, object]) -> Profile:
        """Validate and add one profile, refusing a name already held."""
        profile = Profile.from_document(
            document, schema=self._schema, index=len(self._profiles)
        )
        if profile.name in self._profiles:
            raise UnknownProfileError(profile.name, "is declared twice")
        self._profiles[profile.name] = profile
        return profile

    # -- Reads --------------------------------------------------------------

    @property
    def profiles(self) -> Mapping[str, Profile]:
        """Every held profile, keyed by name."""
        return dict(self._profiles)

    def profile(self, name: str) -> Profile:
        """The profile called `name`, or a failure naming it."""
        try:
            return self._profiles[name]
        except KeyError:
            raise UnknownProfileError(name, "is not held by this set") from None

    @property
    def house_profile(self) -> str | None:
        """The house profile in force, if one is."""
        return self._house_profile

    def selection(self, room_id: str) -> Mapping[str, str]:
        """The room's active profile per axis, keyed by axis."""
        return dict(self._selections.get(room_id, {}))

    def selections(self) -> Mapping[str, Mapping[str, str]]:
        """Every room's selection, ordered by room id and axis."""
        return {
            room_id: {axis: self._selections[room_id][axis] for axis in sorted(axes)}
            for room_id, axes in sorted(self._selections.items())
        }

    # -- Selection ----------------------------------------------------------

    def select(self, room_id: str, axis: str, name: str) -> None:
        """Put `room_id` on `name` for `axis`, replacing what was there.

        The profile must be a room profile and must declare this axis: a
        lighting profile selected on the climate axis would either silently do
        nothing or silently do the wrong thing, and naming the mismatch is the
        only answer a caller can act on.
        """
        profile = self.profile(name)
        if profile.kind is not ProfileKind.ROOM:
            raise UnknownProfileError(name, "is a house profile, not one a room is on")
        if profile.axis != axis:
            raise UnknownProfileError(
                name, f"is on the axis {profile.axis!r}, not {axis!r}"
            )
        self._selections.setdefault(room_id, {})[axis] = name

    def clear(self, room_id: str, axis: str) -> None:
        """Take `room_id` off its `axis` profile. Clearing an axis not set does nothing."""
        axes = self._selections.get(room_id)
        if axes is None:
            return
        axes.pop(axis, None)
        if not axes:
            del self._selections[room_id]

    def activate_house_profile(self, name: str) -> None:
        """Put the house on `name`, applying the room selections it bundles.

        The bundle is applied over the current selections rather than replacing
        them: a house profile that names a room's lighting and nothing else is
        saying nothing about that room's climate, and clearing what it does not
        name would make every house profile a total statement it is not.
        """
        profile = self.profile(name)
        if profile.kind is not ProfileKind.HOUSE:
            raise UnknownProfileError(name, "is a room profile, not a house one")
        for room_id, axes in profile.selections.items():
            for axis, chosen in axes.items():
                self.select(room_id, axis, chosen)
        self._house_profile = name

    def deactivate_house_profile(self) -> None:
        """Note that no house profile is in force. The selections it set stay."""
        self._house_profile = None

    # -- The layers a resolver reads ---------------------------------------

    def effective_house(self) -> dict[str, object]:
        """The house-global profile layer: the active house profile's entries."""
        entries: dict[str, object] = {}
        if self._house_profile is not None:
            profile = self._profiles[self._house_profile]
            entries.update(profile.deltas)
            for behaviour_id in profile.enabled_behaviours:
                entries[enable_key(behaviour_id)] = True
        return entries

    def effective_rooms(self) -> dict[str, dict[str, object]]:
        """The room profile layer: each room's active profiles merged by axis.

        Axes are merged in sorted order so a collision between two axes resolves
        the same way on every run -- the axes govern different settings by
        intent, and the sort is what makes a collision deterministic rather than
        a fact about dict insertion.
        """
        rooms: dict[str, dict[str, object]] = {}
        for room_id, axes in self.selections().items():
            entries: dict[str, object] = {}
            for axis in axes:
                profile = self._profiles[axes[axis]]
                entries.update(profile.deltas)
                for behaviour_id in profile.enabled_behaviours:
                    entries[enable_key(behaviour_id)] = True
            rooms[room_id] = entries
        return rooms

    def enabled_house(self) -> frozenset[str]:
        """The behaviours the active house profile enables."""
        if self._house_profile is None:
            return frozenset()
        return frozenset(self._profiles[self._house_profile].enabled_behaviours)

    def enabled_rooms(self) -> Mapping[str, frozenset[str]]:
        """Per room, the behaviours the room's active profiles enable."""
        enabled: dict[str, frozenset[str]] = {}
        for room_id, axes in self.selections().items():
            names: set[str] = set()
            for axis in axes:
                names.update(self._profiles[axes[axis]].enabled_behaviours)
            if names:
                enabled[room_id] = frozenset(names)
        return enabled

    def modes(self) -> tuple[str, ...]:
        """Every mode the active profiles activate, ordered and deduplicated."""
        active: set[str] = set()
        if self._house_profile is not None:
            active.update(self._profiles[self._house_profile].modes)
        for axes in self._selections.values():
            for name in axes.values():
                active.update(self._profiles[name].modes)
        return tuple(sorted(active))

    # -- Documents ----------------------------------------------------------

    def to_document(self) -> dict[str, object]:
        """The whole set: every profile and the selections in force."""
        return {
            "profiles": [
                self._profiles[name].to_document() for name in sorted(self._profiles)
            ],
            "selections": self.selections(),
            "house_profile": self._house_profile,
        }

    @classmethod
    def from_document(
        cls, document: Mapping[str, object], *, schema: Mapping[str, object]
    ) -> ProfileSet:
        """Rebuild a set from `to_document`'s form, failing by naming what is wrong."""
        profiles = document.get("profiles")
        if not isinstance(profiles, list):
            raise ProfileError("a profile document must hold a 'profiles' list")
        rebuilt = cls(cast("list[Mapping[str, object]]", profiles), schema=schema)
        selections = document.get("selections")
        if isinstance(selections, Mapping):
            for room_id, axes in selections.items():
                if not isinstance(axes, Mapping):
                    raise ProfileError(f"selections for {room_id!r} are not an object")
                for axis, name in axes.items():
                    rebuilt.select(str(room_id), str(axis), str(name))
        house_profile = document.get("house_profile")
        if house_profile is not None:
            rebuilt._house_profile = str(house_profile)
        return rebuilt


# --------------------------------------------------------------------------
# Activation: the rules and the brakes that stop them flapping.
# --------------------------------------------------------------------------


class RuleKind(StrEnum):
    """How a rule decides it wants its profile."""

    MANUAL = "manual"
    SCHEDULE = "schedule"
    HOUSE_MODE = "house_mode"
    TRIGGER = "trigger"


class BlockedReason(StrEnum):
    """Why a change a rule wanted did not happen."""

    MIN_DWELL = "min_dwell"
    CYCLE = "cycle"


@dataclass(frozen=True, slots=True)
class ActivationRule:
    """One way a room's axis comes to be on a profile.

    The kind-specific fields are optional and read only by the kind that owns
    them: a schedule rule reads `at`/`until`/`days`, a house-mode rule `mode`, a
    trigger rule `entity_id`/`state`. A manual rule reads none, because a manual
    pick is the user calling `ProfileSet.select` and the rule's only job is to be
    the record of that intent.
    """

    name: str
    room_id: str
    axis: str
    profile: str
    kind: RuleKind
    hysteresis: timedelta = timedelta(0)
    min_dwell: timedelta = timedelta(0)
    mode: str | None = None
    at: time | None = None
    until: time | None = None
    days: frozenset[int] | None = None
    entity_id: str | None = None
    state: str | None = None

    def wants(
        self,
        now: datetime,
        *,
        active_modes: Sequence[str] = (),
        states: Mapping[str, str] | None = None,
    ) -> bool:
        """Whether the rule's condition holds at `now`, before any brake."""
        if self.kind is RuleKind.MANUAL:
            return False
        if self.kind is RuleKind.HOUSE_MODE:
            return self.mode is not None and self.mode in active_modes
        if self.kind is RuleKind.TRIGGER:
            if self.entity_id is None or self.state is None:
                return False
            return (states or {}).get(self.entity_id) == self.state
        return self._scheduled(now)

    def _scheduled(self, now: datetime) -> bool:
        if self.at is None or self.until is None:
            return False
        if self.days is not None and now.weekday() not in self.days:
            return False
        moment = now.time()
        if self.at <= self.until:
            return self.at <= moment < self.until
        return moment >= self.at or moment < self.until


@dataclass(frozen=True, slots=True)
class Activation:
    """One selection this step changed."""

    room_id: str
    axis: str
    rule: str
    before: str | None
    after: str


@dataclass(frozen=True, slots=True)
class Blocked:
    """One change a rule wanted and a brake refused."""

    room_id: str
    axis: str
    rule: str
    wanted: str
    reason: BlockedReason


@dataclass(frozen=True, slots=True)
class ActivationResult:
    """What one step did: the changes it made and the ones it refused."""

    activations: tuple[Activation, ...] = ()
    blocked: tuple[Blocked, ...] = ()


@dataclass(slots=True)
class _History:
    """One (room, axis)'s recent selections, for the cycle brake."""

    changes: list[tuple[datetime, str]] = field(
        default_factory=list[tuple[datetime, str]]
    )


class ProfileActivator:
    """Applies activation rules to a `ProfileSet`, with hysteresis and brakes.

    It holds the *timing* state -- how long each rule's condition has held, when
    each axis last changed, and each axis's recent profile history -- because
    those are facts about the run's clock and not about the profiles themselves,
    and a `ProfileSet` shared by two activators would otherwise have to invent a
    time source it has no business holding.
    """

    def __init__(
        self,
        profiles: ProfileSet,
        rules: Sequence[ActivationRule],
        *,
        cycle_window: timedelta = timedelta(minutes=30),
        cycle_limit: int = 3,
    ) -> None:
        self._profiles = profiles
        self._rules = tuple(sorted(rules, key=lambda rule: rule.name))
        self._cycle_window = cycle_window
        self._cycle_limit = cycle_limit
        self._held: dict[str, datetime | None] = {
            rule.name: None for rule in self._rules
        }
        self._since: dict[tuple[str, str], datetime] = {}
        #: The min-dwell the reference in force was placed under: the rule that
        #: made a change states how long its selection must stay, and it is that
        #: dwell the *next* change is measured against -- not the incoming
        #: rule's, which would let a zero-dwell rule override a settled room.
        self._dwell: dict[tuple[str, str], timedelta] = {}
        self._history: dict[tuple[str, str], _History] = {}

    @property
    def rules(self) -> tuple[ActivationRule, ...]:
        """The rules, ordered by name."""
        return self._rules

    def step(
        self,
        now: datetime,
        *,
        active_modes: Sequence[str] = (),
        states: Mapping[str, str] | None = None,
    ) -> ActivationResult:
        """Move every room whose rule now wants a different profile.

        Rules are considered in name order and one winner is chosen per (room,
        axis) -- the first due rule by name -- so the outcome is a function of the
        rules rather than of the order they were declared in.
        """
        due: dict[tuple[str, str], ActivationRule] = {}
        for rule in self._rules:
            wants = rule.wants(now, active_modes=active_modes, states=states)
            if wants and self._held[rule.name] is None:
                self._held[rule.name] = now
            elif not wants:
                self._held[rule.name] = None
            held_since = self._held[rule.name]
            if not wants or held_since is None or now - held_since < rule.hysteresis:
                continue
            due.setdefault((rule.room_id, rule.axis), rule)

        activations: list[Activation] = []
        blocked: list[Blocked] = []
        for (room_id, axis), rule in sorted(due.items()):
            current = self._profiles.selection(room_id).get(axis)
            if current == rule.profile:
                continue
            reason = self._brake(room_id, axis, rule, now)
            if reason is not None:
                blocked.append(
                    Blocked(
                        room_id=room_id,
                        axis=axis,
                        rule=rule.name,
                        wanted=rule.profile,
                        reason=reason,
                    )
                )
                continue
            self._profiles.select(room_id, axis, rule.profile)
            self._since[(room_id, axis)] = now
            self._dwell[(room_id, axis)] = rule.min_dwell
            self._record(room_id, axis, rule.profile, now)
            activations.append(
                Activation(
                    room_id=room_id,
                    axis=axis,
                    rule=rule.name,
                    before=current,
                    after=rule.profile,
                )
            )
        return ActivationResult(activations=tuple(activations), blocked=tuple(blocked))

    def note_selection(self, room_id: str, axis: str, at: datetime) -> None:
        """Record that something other than a rule changed a selection.

        A manual pick is a change like any other for the two time brakes, so it
        has to enter the same history: a manual switch immediately followed by a
        rule's switch back is exactly the flap the cycle brake exists to catch.
        """
        selected = self._profiles.selection(room_id).get(axis)
        self._since[(room_id, axis)] = at
        # A manual pick names no dwell of its own, so it drops whatever dwell the
        # rule it displaced had asked for rather than inheriting it.
        self._dwell[(room_id, axis)] = timedelta(0)
        if selected is not None:
            self._record(room_id, axis, selected, at)

    def _brake(
        self, room_id: str, axis: str, rule: ActivationRule, now: datetime
    ) -> BlockedReason | None:
        since = self._since.get((room_id, axis))
        dwell = self._dwell.get((room_id, axis), timedelta(0))
        if since is not None and now - since < dwell:
            return BlockedReason.MIN_DWELL
        if self._cycles(room_id, axis, rule.profile, now):
            return BlockedReason.CYCLE
        return None

    def _cycles(self, room_id: str, axis: str, wanted: str, now: datetime) -> bool:
        """Whether moving to `wanted` would be a flap the window already shows.

        A cycle is the same profile being taken up `cycle_limit` times inside
        `cycle_window`: two rules taking turns satisfy this and settle, because
        the third time one of them wins within the window is refused and the room
        stays where it is. Counting entries that are still inside the window is
        what makes the brake lift once the flapping has aged out.
        """
        history = self._history.get((room_id, axis))
        if history is None:
            return False
        live = [
            profile
            for at, profile in history.changes
            if now - at <= self._cycle_window and profile == wanted
        ]
        return len(live) >= self._cycle_limit

    def _record(self, room_id: str, axis: str, profile: str, at: datetime) -> None:
        history = self._history.setdefault((room_id, axis), _History())
        history.changes.append((at, profile))
        history.changes = [
            entry for entry in history.changes if at - entry[0] <= self._cycle_window
        ]


# --------------------------------------------------------------------------
# Schema loading and document projection. The document conforms before any
# projection runs, so the casts below are the schema's guarantees.
# --------------------------------------------------------------------------


def load_profile_schema(root: Path) -> Mapping[str, object]:
    """The current `schemas/profile/` schema, read from the tree at `root`.

    Read the way the engine's other schemas are -- the current version of the
    concept -- rather than spelled as a file name, so a later bump is followed
    rather than missed.
    """
    versions = load_versions(PROFILE_CONCEPT, root=root)
    if not versions:
        raise ProfileError(f"no {PROFILE_CONCEPT} schema is present under {root}")
    current = current_version(versions)
    if current is None:
        raise ProfileError(
            f"the {PROFILE_CONCEPT} schemas have no single current version"
        )
    return cast("Mapping[str, object]", current.document)


def _validate(
    document: Mapping[str, object], schema: Mapping[str, object], index: int
) -> None:
    validator = jsonschema.Draft202012Validator(cast("dict[str, object]", schema))
    errors = sorted(
        validator.iter_errors(document),
        key=lambda error: (list(error.absolute_path), error.message),
    )
    if errors:
        raise InvalidProfileError(index, errors[0].message)


def _optional_str(value: object) -> str | None:
    return None if value is None else cast("str", value)


def _mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        return {}
    return cast("Mapping[str, object]", value)


def _names(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(cast("str", item) for item in value)


def _selections(value: object) -> Mapping[str, Mapping[str, str]]:
    if not isinstance(value, Mapping):
        return {}
    rooms = cast("Mapping[str, object]", value)
    return {
        str(room): {
            str(axis): str(name)
            for axis, name in cast("Mapping[str, object]", axes).items()
        }
        for room, axes in rooms.items()
    }
