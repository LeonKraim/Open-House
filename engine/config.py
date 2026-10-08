"""The layered config resolver (task 5.2; `design.md` D5).

Every behaviour tunable resolves through this module -- the quiet timeout, the
lux threshold, the log's bound, an arbitrated priority, an override's duration --
so that no tunable is a module constant and every resolved value can be recorded
with the layer that decided it. A setting is resolved by precedence and the
answer carries the layer, which is what makes a decision record explainable
without re-deriving the stack.

The layer order is fixed in this phase although one of its layers is empty:

    built-in defaults < house < room < reserved profile layer < temporary override

The profile layer is reserved and empty in Phase 1, and the reason it is here
rather than deferred is that precedence is the one thing every later layer's
meaning depends on: a refactor that inserts a layer is free to reorder the others
silently, whereas a layer that exists now is a layer Phase 3 fills in *between
room and override* and cannot move anything else. `LAYER_ORDER` is the single
declaration of that order -- `resolve` walks it rather than testing layers one by
one -- so the constant is the behaviour, and the test that pins it is pinning the
resolver rather than a comment.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from engine.binding import HouseScope, RoomScope, Scope


class ConfigError(Exception):
    """Base for the failures this module defines."""


class InvalidSettingError(ConfigError):
    """A resolved setting whose value is not the kind its reader needs.

    Raised with the key *and* the layer, rather than left to fail inside the
    arithmetic or the `timedelta` that read it: a `TypeError` from a comparison
    names neither the setting nor the file that set it, and the two facts that
    make the mistake findable are exactly those. A `bool` is refused everywhere a
    number is wanted even though it is an `int` in Python, because a layer that
    set an enable flag where a duration belonged would otherwise resolve to `1.0`
    and read as a one-second timeout rather than as the mistake it is.
    """

    def __init__(self, key: str, value: object, layer: Layer, wanted: str) -> None:
        super().__init__(
            f"{key!r} resolved to {value!r} from the {layer} layer, "
            f"which is not {wanted}"
        )
        self.key = key
        self.value = value
        self.layer = layer


class UnresolvedSettingError(ConfigError):
    """A setting no layer supplies, so there is no value to return.

    Returning `None` would make "the setting is unset" indistinguishable from a
    setting whose value is nothing, and a behaviour that read the second for the
    first would act on a silent default. The key is named because the caller's
    mistake is a key, and the scope is named because a key can be set for one
    room and not another.
    """

    def __init__(self, key: str, where: str) -> None:
        super().__init__(f"no layer sets {key!r} for {where}")
        self.key = key
        self.where = where


class Layer(StrEnum):
    """One layer of the stack, lowest precedence first."""

    BUILTIN = "builtin"
    HOUSE = "house"
    ROOM = "room"
    PROFILE = "profile"
    OVERRIDE = "override"


#: The declared order, lowest precedence first. `resolve` walks this tuple, so
#: reordering it reorders precedence; the order is a checked artifact rather than
#: a comment (`engine-core`).
LAYER_ORDER: tuple[Layer, ...] = (
    Layer.BUILTIN,
    Layer.HOUSE,
    Layer.ROOM,
    Layer.PROFILE,
    Layer.OVERRIDE,
)

#: The key the decision log's bound resolves under, and its built-in default.
#: The bound lives here rather than in `decision_log.py` because it is a setting
#: like any other: a house that wants a longer log sets this key, and the log
#: itself never reads a configuration file (`engine-core`: the log is bounded
#: "with the bound supplied through the `ConfigResolver`").
LOG_BOUND_KEY = "engine.decision_log.bound"

#: The key the presence mechanism's quiet timeout resolves under. It is the
#: engine's own rather than a behaviour's because two different requirements read
#: it -- an override lapsing by `room_emptied`, and the house reading as empty for
#: the away shutdown -- and a duration those two disagree about would make "the
#: house is empty" and "the room has emptied" mean different things.
PRESENCE_QUIET_TIMEOUT_KEY = "engine.presence.quiet_timeout_seconds"

#: The per-entity, per-window rate limit's two settings (`engine-core`: "The
#: bound and the window SHALL resolve through the `ConfigResolver`, so they are
#: layered settings rather than constants").
RATE_LIMIT_BOUND_KEY = "engine.rate_limit.bound"
RATE_LIMIT_WINDOW_KEY = "engine.rate_limit.window_seconds"

#: The built-in defaults: the bottom layer, and the value every other layer
#: overrides. It is deliberately small -- a layer with no entries is one no
#: resolution can fall back to -- and it holds only what the engine itself
#: defines; every behaviour's tunables are contributed by the behaviour that owns
#: them (`engine/behaviours/__init__.py`), so the layer a value came from is
#: answerable for every tunable the engine has and no table here can fall behind
#: the units. The rate limit's defaults are a burst of sixty a minute: generous
#: enough that no rule in this phase can reach it, which is the point -- the
#: limit is a backstop against a misbehaving pack, not a policy.
#:
#: **The log's bound is sized in ticks and not in rows, because a tick is what
#: fills it.** The engine records one row per behaviour per scope per tick, and a
#: house of any size reaches three hundred of them -- measured at 302 in the
#: nine-room example house, every thirty seconds. A bound of five hundred rows
#: therefore held *under two ticks*, and the consequence was not a short history
#: but a blind spot: the panel reads the newest rows first, so a behaviour
#: evaluated early in a tick (its own action, `motion_lighting` among them) had
#: already been pushed out of the window by the rest of its own tick before
#: anyone could read it. The house would turn a light on and the Activity tab
#: would show nothing that said so -- observed, not reasoned: a stimulus, a light
#: that came on within five seconds, and five hundred rows of "skipped" spanning
#: exactly the tick that did it. Ten thousand rows is roughly a third of an hour
#: of house at that rate and a few megabytes of records, which buys a window that
#: can hold the tick it is reading.
BUILTIN_DEFAULTS: Mapping[str, object] = {
    LOG_BOUND_KEY: 10_000,
    PRESENCE_QUIET_TIMEOUT_KEY: 300.0,
    RATE_LIMIT_BOUND_KEY: 60,
    RATE_LIMIT_WINDOW_KEY: 60.0,
}

#: Distinguishes "no layer supplies this key" from a layer that supplies `None`.
_UNSET = object()


@dataclass(frozen=True, slots=True)
class ResolvedSetting:
    """A setting's value, and the layer that decided it.

    Also the form a resolved setting takes in a decision record's `inputs`, which
    is why the layer travels with the value: a record that named the threshold a
    behaviour consulted but not whether the room or the house set it would leave
    the interesting question -- why *this* value -- unanswered.
    """

    key: str
    value: object
    layer: Layer

    def number(self) -> float:
        """The value as a float, or `InvalidSettingError` naming key and layer."""
        if isinstance(self.value, bool) or not isinstance(self.value, (int, float)):
            raise InvalidSettingError(self.key, self.value, self.layer, "a number")
        return float(self.value)

    def integer(self) -> int:
        """The value as an int, or `InvalidSettingError` naming key and layer.

        A float that happens to be whole is *not* accepted. The two readers of an
        integer setting -- the log's bound and the rate limit's -- are counts, and
        a layer that wrote `50.0` for a count is a layer whose author meant a
        duration; refusing it here says so at the file rather than truncating it
        silently into a bound nobody chose.
        """
        if isinstance(self.value, bool) or not isinstance(self.value, int):
            raise InvalidSettingError(self.key, self.value, self.layer, "an integer")
        return self.value

    def flag(self) -> bool:
        """The value as a bool, or `InvalidSettingError` naming key and layer.

        A truthy string is refused: `"false"` is exactly the value a YAML file
        gets wrong, and reading it as enabled would turn the product rule's
        off-by-default into on-by-typo.
        """
        if not isinstance(self.value, bool):
            raise InvalidSettingError(self.key, self.value, self.layer, "a boolean")
        return self.value

    def text(self) -> str:
        """The value as a string, or `InvalidSettingError` naming key and layer.

        The accessor for a setting whose value is a *word* rather than a number
        or a switch -- a behaviour's chosen scope is the one so far. It is here
        rather than left to the caller's `str()` because the callers that matter
        compare the answer against a closed set of words, and `str()` over a
        `None` or a `True` would hand back `"None"` or `"True"`: words that match
        nothing, so the comparison would fall through to a default in silence and
        the layer that wrote the wrong value would never be named.
        """
        if not isinstance(self.value, str):
            raise InvalidSettingError(self.key, self.value, self.layer, "a string")
        return self.value


class ConfigResolver:
    """The layered settings stack, resolved by precedence.

    The layers below the override are supplied at construction, from wherever
    they come from -- the engine's own defaults, a house's settings, a room's.
    They are read once by a build and are *not* re-read during a run, which is
    why the persistent layers have writers of their own: `set_house_setting` and
    `set_room_setting` (with their `clear_` pairs) change what the running
    resolver answers without rebuilding the engine around it. A session that
    recorded a person's setting without also telling the live resolver would
    have a switch that appeared to do nothing until something else happened to
    rebuild -- and, worse, a *cleared* setting that kept its old value, because
    the snapshot taken at construction outlived the record it came from.

    The override layer is separate and stays what it was: temporary by
    definition, set and cleared through `set_override` and `clear_override`,
    and above every persistent layer so a person's immediate choice outranks a
    profile the same key appears in.
    """

    def __init__(
        self,
        *,
        builtin: Mapping[str, object] = BUILTIN_DEFAULTS,
        house: Mapping[str, object] | None = None,
        rooms: Mapping[str, Mapping[str, object]] | None = None,
        profile: Mapping[str, object] | None = None,
        profile_rooms: Mapping[str, Mapping[str, object]] | None = None,
    ) -> None:
        self._layers: dict[Layer, dict[str, object]] = {
            Layer.BUILTIN: dict(builtin),
            Layer.HOUSE: dict(house) if house is not None else {},
            Layer.PROFILE: dict(profile) if profile is not None else {},
        }
        #: The profile layer's room half: a room profile's deltas apply in the
        #: room it is selected in, so the layer is one layer with a house-global
        #: part and a per-room part over it -- the same shape the override layer
        #: has, and for the same reason. Phase 1 left the whole profile layer
        #: empty; Phase 3 fills it without moving the order around it.
        self._profile_rooms: Mapping[str, Mapping[str, object]] = (
            {room_id: dict(values) for room_id, values in profile_rooms.items()}
            if profile_rooms is not None
            else {}
        )
        self._rooms: dict[str, dict[str, object]] = (
            {room_id: dict(values) for room_id, values in rooms.items()}
            if rooms is not None
            else {}
        )
        self._overrides: dict[tuple[str, str], object] = {}

    def resolve(self, key: str, scope: Scope) -> ResolvedSetting:
        """Resolve `key` for `scope`, reporting the layer that decided it."""
        found: ResolvedSetting | None = None
        for layer in LAYER_ORDER:
            value = self._value_at(layer, key, scope)
            if value is not _UNSET:
                found = ResolvedSetting(key=key, value=value, layer=layer)
        if found is None:
            raise UnresolvedSettingError(key, _describe(scope))
        return found

    def resolve_or(self, key: str, scope: Scope, default: object) -> ResolvedSetting:
        """Resolve `key`, falling back to `default` at the built-in layer.

        For the settings whose lowest layer is not this module's to declare: a
        behaviour's enable flag and its arbitrated priority are the *unit's*
        defaults, and the unit is where the product rule's "off unless enabled"
        is legible. The fallback is reported as `Layer.BUILTIN` rather than as a
        layer of its own, because that is what it is -- the bottom of the same
        stack a `BUILTIN_DEFAULTS` entry would have sat at -- and a record naming
        the builtin layer for a value no file set is telling the truth about
        where the value came from.
        """
        try:
            return self.resolve(key, scope)
        except UnresolvedSettingError:
            return ResolvedSetting(key=key, value=default, layer=Layer.BUILTIN)

    def set_override(self, key: str, scope: Scope, value: object) -> None:
        """Set a temporary override, which outranks every other layer."""
        self._overrides[(_scope_key(scope), key)] = value

    def clear_override(self, key: str, scope: Scope) -> None:
        """Clear a temporary override. Clearing one that is not set does nothing.

        A lapse and a clear are the same event from the resolver's side, and an
        override that a timeout already dropped should not turn its own explicit
        clear into a failure.
        """
        self._overrides.pop((_scope_key(scope), key), None)

    def set_house_setting(self, key: str, value: object) -> None:
        """Set a value in the house layer of *this* resolver.

        The persistent counterpart of `set_override` for a caller that owns the
        house's settings -- a live session, whose own record of the value is what
        a rebuild and a restart read back. Writing both keeps the running
        resolver and the recorded document saying the same thing; writing only
        the document leaves the engine deciding by a value it was built with.

        Nothing here reaches the document: this layer is a copy taken at
        construction, so a caller that means to persist must also record it where
        the session keeps its settings (`ha_adapter.live.LiveSession`).
        """
        self._layers[Layer.HOUSE][key] = value

    def clear_house_setting(self, key: str) -> None:
        """Remove a house-layer value, revealing whatever layer is beneath it."""
        self._layers[Layer.HOUSE].pop(key, None)

    def set_room_setting(self, key: str, room_id: str, value: object) -> None:
        """Set a value in one room's layer of *this* resolver.

        The room half of `set_house_setting`, and the same warning applies: this
        is the running copy, not the record. A room the resolver was not built
        with is given a layer of its own rather than refused, because the caller
        that has just added a room has a session that will rebuild in a moment
        and a resolver that must still answer sensibly until it does.
        """
        self._rooms.setdefault(room_id, {})[key] = value

    def clear_room_setting(self, key: str, room_id: str) -> None:
        """Remove a room-layer value, revealing whatever layer is beneath it.

        A room with nothing left in its layer keeps the empty mapping rather than
        losing the entry: the two answer identically to every reader here, and
        dropping the key would make `_rooms` change shape under a caller that is
        iterating it.
        """
        held = self._rooms.get(room_id)
        if held is not None:
            held.pop(key, None)

    def _value_at(self, layer: Layer, key: str, scope: Scope) -> object:
        if layer is Layer.ROOM:
            if not isinstance(scope, RoomScope):
                return _UNSET
            return self._rooms.get(scope.room_id, {}).get(key, _UNSET)
        if layer is Layer.PROFILE:
            return self._profile_at(key, scope)
        if layer is Layer.OVERRIDE:
            return self._override_at(key, scope)
        return self._layers[layer].get(key, _UNSET)

    def _profile_at(self, key: str, scope: Scope) -> object:
        """The profile layer: the active house profiles, then the room's over them.

        A room profile's deltas are the room's, so within the one layer a room
        scope sees the house-global profile entries and then its own room
        profile's, the more specific winning -- exactly how `_override_at` folds
        the override layer, and for the same reason.
        """
        value = self._layers[Layer.PROFILE].get(key, _UNSET)
        if isinstance(scope, RoomScope):
            room_value = self._profile_rooms.get(scope.room_id, {}).get(key, _UNSET)
            if room_value is not _UNSET:
                value = room_value
        return value

    def _override_at(self, key: str, scope: Scope) -> object:
        """The override layer: the house's own, then a room's over it.

        The layer is one layer, but within it a room's temporary override is more
        specific than the house's, so a room scope sees both and the room's wins.
        """
        value = self._overrides.get((_scope_key(HouseScope()), key), _UNSET)
        if isinstance(scope, RoomScope):
            room_value = self._overrides.get((_scope_key(scope), key), _UNSET)
            if room_value is not _UNSET:
                value = room_value
        return value


def _scope_key(scope: Scope) -> str:
    """The key one scope's overrides are stored under.

    A room's own id, or the empty string for the house. The vocabulary's room ids
    match `^[a-z][a-z0-9_]*$`, so the empty string cannot collide with a room.
    """
    return "" if isinstance(scope, HouseScope) else scope.room_id


def _describe(scope: Scope) -> str:
    return (
        "the house" if isinstance(scope, HouseScope) else f"the room {scope.room_id!r}"
    )
