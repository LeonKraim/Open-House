"""The layered config resolver -- task 5.2, `design.md` D5.

The resolver is where every tunable in the phase comes from, so what these tests
pin is not one behaviour's threshold but the shape of the stack: five layers, the
declared order between them, a room layer that answers only room scopes, and an
override layer that is the one layer changing mid-run. The order is asserted
against `LAYER_ORDER` as well as against resolutions, because `resolve` walks that
tuple -- a reordered tuple reorders precedence, and only a test that reads the
tuple catches a reorder before a scenario does.

The second half is the *readers*: `number`, `integer` and `flag` are the only
places a layer's value becomes a duration, a count or a flag, so they are where a
value of the wrong kind is turned away. Each refuses the kind it should not
accept -- including the two that look acceptable and are not, a `bool` where a
number is wanted and a whole float where a count is -- and each names the key and
the layer it came from, because a `TypeError` from the arithmetic names neither.

Each test names the behaviour and says, in its docstring, what a falsifying
implementation would look like, because a test that would pass against any
implementation exercises nothing.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

import pytest

from engine.binding import HouseScope, RoomScope, Scope
from engine.config import (
    BUILTIN_DEFAULTS,
    LAYER_ORDER,
    LOG_BOUND_KEY,
    PRESENCE_QUIET_TIMEOUT_KEY,
    RATE_LIMIT_BOUND_KEY,
    RATE_LIMIT_WINDOW_KEY,
    ConfigResolver,
    InvalidSettingError,
    Layer,
    ResolvedSetting,
    UnresolvedSettingError,
)

_KITCHEN = RoomScope("kitchen")
_BEDROOM = RoomScope("bedroom")
_HOUSE = HouseScope()

#: A key no built-in default supplies, so a resolution of it can only come from
#: the layers a test supplies. Using `LOG_BOUND_KEY` here would let the built-in
#: layer answer for a test that meant to exercise the house layer.
_KEY = "behaviour.quiet_timeout"


def _resolver(**layers: Any) -> ConfigResolver:
    return ConfigResolver(**layers)


# --------------------------------------------------------------------------
# The order
# --------------------------------------------------------------------------


def test_the_layer_order_is_the_declared_one() -> None:
    """Precedence runs built-in, house, room, profile, override -- lowest first.

    A falsifying implementation that reordered the tuple would resolve every
    setting to a different layer's value and still return a value, so nothing but
    reading the tuple distinguishes the two orders.
    """
    assert LAYER_ORDER == (
        Layer.BUILTIN,
        Layer.HOUSE,
        Layer.ROOM,
        Layer.PROFILE,
        Layer.OVERRIDE,
    )


@pytest.mark.parametrize(
    ("layers", "scope", "expected_layer", "expected_value"),
    [
        ({"builtin": {_KEY: "builtin"}}, _HOUSE, Layer.BUILTIN, "builtin"),
        (
            {"builtin": {_KEY: "builtin"}, "house": {_KEY: "house"}},
            _HOUSE,
            Layer.HOUSE,
            "house",
        ),
        (
            {
                "builtin": {_KEY: "builtin"},
                "house": {_KEY: "house"},
                "rooms": {"kitchen": {_KEY: "room"}},
            },
            _KITCHEN,
            Layer.ROOM,
            "room",
        ),
        (
            {
                "builtin": {_KEY: "builtin"},
                "house": {_KEY: "house"},
                "profile": {_KEY: "profile"},
            },
            _HOUSE,
            Layer.PROFILE,
            "profile",
        ),
        (
            {
                "builtin": {_KEY: "builtin"},
                "house": {_KEY: "house"},
                "overrides": {_KEY: "override"},
            },
            _HOUSE,
            Layer.OVERRIDE,
            "override",
        ),
    ],
)
def test_each_layer_overrides_the_one_below_it(
    layers: Mapping[str, object],
    scope: Scope,
    expected_layer: Layer,
    expected_value: object,
) -> None:
    """The highest layer that supplies the key decides the value and the layer.

    One row per layer, so a layer that stopped being consulted -- or one consulted
    out of order -- fails a row rather than only a test of the layer beside it. A
    falsifying implementation that returned the first match while walking
    downwards, or that recorded the layer it started from, would report the
    built-in default for a resolver whose house set the key, and the layer a
    record carries would name the wrong origin.
    """
    supplied = dict(layers)
    overrides = cast("Mapping[str, object]", supplied.pop("overrides", {}))
    resolver = _resolver(**supplied)
    for key, value in overrides.items():
        resolver.set_override(key, scope, value)
    assert resolver.resolve(_KEY, scope) == ResolvedSetting(
        key=_KEY, value=expected_value, layer=expected_layer
    )


def test_a_room_layer_overrides_the_house_layer_for_that_room() -> None:
    """A room's value decides for the room and the house's for everywhere else.

    A falsifying implementation that held one flat mapping would give the kitchen
    the house's value or the bedroom the kitchen's, and either mistake is a
    behaviour tuned for one room applying to another.
    """
    resolver = _resolver(
        house={_KEY: "house"},
        rooms={"kitchen": {_KEY: "kitchen"}},
    )
    assert resolver.resolve(_KEY, _KITCHEN).value == "kitchen"
    assert resolver.resolve(_KEY, _KITCHEN).layer is Layer.ROOM
    assert resolver.resolve(_KEY, _BEDROOM).value == "house"
    assert resolver.resolve(_KEY, _BEDROOM).layer is Layer.HOUSE


def test_a_room_layer_does_not_answer_a_house_scoped_question() -> None:
    """A key only a room sets is unresolved at house scope, not silently general.

    A falsifying implementation that read the room layer for any scope would let
    one room's tuning stand in for the house's, which reads as a working default
    until a second room disagrees.
    """
    resolver = _resolver(rooms={"kitchen": {_KEY: "kitchen"}})
    with pytest.raises(UnresolvedSettingError) as raised:
        resolver.resolve(_KEY, _HOUSE)
    assert raised.value.key == _KEY
    assert raised.value.where == "the house"


def test_an_empty_profile_layer_changes_nothing() -> None:
    """The reserved layer is inert this phase: an empty profile is the default.

    A falsifying implementation that treated the profile layer as "supplies
    everything" -- or that failed on a supplied-but-empty profile -- would make
    every setting's layer the profile's, or refuse a resolver a later phase will
    always hand one to.
    """
    without = _resolver(house={_KEY: "house"})
    with_empty = _resolver(house={_KEY: "house"}, profile={})
    assert with_empty.resolve(_KEY, _HOUSE) == without.resolve(_KEY, _HOUSE)


# --------------------------------------------------------------------------
# The override layer
# --------------------------------------------------------------------------


def test_an_override_outranks_every_layer_below_it() -> None:
    """A temporary override decides over house, room and profile alike.

    A falsifying implementation that stored overrides in the same mapping as a
    setting would lose to the layer that was consulted later, and a manual
    override -- the one thing in the phase that must outrank the house -- would
    quietly not apply.
    """
    resolver = _resolver(
        house={_KEY: "house"},
        rooms={"kitchen": {_KEY: "kitchen"}},
        profile={_KEY: "profile"},
    )
    resolver.set_override(_KEY, _HOUSE, "override")
    resolved = resolver.resolve(_KEY, _KITCHEN)
    assert resolved.value == "override"
    assert resolved.layer is Layer.OVERRIDE


def test_a_room_override_beats_the_house_override_for_that_room_only() -> None:
    """Within the override layer, a room's own is more specific than the house's.

    A falsifying implementation that keyed overrides by key alone would make one
    room's temporary override a house-wide one, which is the opposite of what a
    room's override is asked to do.
    """
    resolver = _resolver(house={_KEY: "house"})
    resolver.set_override(_KEY, _HOUSE, "house override")
    resolver.set_override(_KEY, _KITCHEN, "kitchen override")
    assert resolver.resolve(_KEY, _KITCHEN).value == "kitchen override"
    assert resolver.resolve(_KEY, _BEDROOM).value == "house override"


def test_a_room_override_is_unresolved_for_a_key_no_lower_layer_sets() -> None:
    """The override layer is not a house-wide answer for a room's override.

    A falsifying implementation that promoted a room override into the house
    layer would answer a key the house never set, and a setting the house does not
    have would appear to exist for every room.
    """
    resolver = _resolver()
    resolver.set_override(_KEY, _KITCHEN, "kitchen override")
    with pytest.raises(UnresolvedSettingError):
        resolver.resolve(_KEY, _BEDROOM)


def test_clearing_an_override_restores_the_layer_below() -> None:
    """After a clear the resolution is what it was before the set.

    The temporary layer is the phase's exit path for every override, so one that
    could be set and not removed would leave a room permanently outranking its own
    house.
    """
    resolver = _resolver(house={_KEY: "house"})
    resolver.set_override(_KEY, _KITCHEN, "override")
    assert resolver.resolve(_KEY, _KITCHEN).value == "override"
    resolver.clear_override(_KEY, _KITCHEN)
    assert resolver.resolve(_KEY, _KITCHEN) == ResolvedSetting(
        key=_KEY, value="house", layer=Layer.HOUSE
    )


def test_clearing_an_override_that_is_not_set_does_nothing() -> None:
    """Clearing twice is not a failure, because a lapse clears the same override.

    A falsifying implementation that raised on the second clear would make the
    order of a timeout and an explicit clear matter, and a behaviour whose
    override lapsed would have its own cleanup raise.
    """
    resolver = _resolver(house={_KEY: "house"})
    resolver.clear_override(_KEY, _KITCHEN)
    resolver.clear_override(_KEY, _KITCHEN)
    assert resolver.resolve(_KEY, _KITCHEN).value == "house"


# --------------------------------------------------------------------------
# The failures and the values a layer may supply
# --------------------------------------------------------------------------


def test_an_unset_key_fails_naming_the_key_and_the_scope() -> None:
    """No layer supplying a key is an error, not `None` and not a default.

    A falsifying implementation that returned `None` would make an unset setting
    and a setting whose value is nothing the same answer, and a behaviour reading
    the second for the first would act on a silent default.
    """
    with pytest.raises(UnresolvedSettingError) as raised:
        _resolver().resolve(_KEY, _KITCHEN)
    assert raised.value.key == _KEY
    assert raised.value.where == "the room 'kitchen'"


def test_a_layer_supplying_none_is_supplying_a_value() -> None:
    """`None` is a value, and only an absent entry is an absence.

    A falsifying implementation that tested truthiness -- or that used `None` for
    its own sentinel -- would report an explicit `None` as unset, and the layer
    that set it would be invisible in the record.
    """
    resolver = _resolver(house={_KEY: None})
    resolved = resolver.resolve(_KEY, _HOUSE)
    assert resolved.value is None
    assert resolved.layer is Layer.HOUSE


def test_the_built_in_layer_supplies_the_log_bound() -> None:
    """The one built-in default is the log's bound, and it is the bottom layer.

    A falsifying implementation that placed `LOG_BOUND_KEY` in `decision_log.py`
    would leave the log's bound unanswerable -- the setting a house may set like
    any other -- and the layer stack's lowest layer empty.
    """
    assert BUILTIN_DEFAULTS[LOG_BOUND_KEY] == 500
    assert _resolver().resolve(LOG_BOUND_KEY, _HOUSE) == ResolvedSetting(
        key=LOG_BOUND_KEY, value=500, layer=Layer.BUILTIN
    )


# --------------------------------------------------------------------------
# The readers, and the mistake each one refuses
# --------------------------------------------------------------------------


def _value(value: object, layer: Layer = Layer.HOUSE) -> ResolvedSetting:
    """A resolved setting of one value, with the key and layer under test."""
    return ResolvedSetting(key=_KEY, value=value, layer=layer)


def test_a_number_reads_an_int_and_a_float_alike() -> None:
    """Both numeric kinds a layer may supply come back as a float.

    A falsifying implementation that refused an `int` would make `500` an invalid
    log bound -- the value this module's own built-in default uses -- so the reader
    would reject the very thing it is for.
    """
    assert _value(500).number() == 500.0
    assert _value(0.5).number() == 0.5
    assert isinstance(_value(3).number(), float)


@pytest.mark.parametrize("value", [True, False])
def test_a_number_refuses_a_bool(value: bool) -> None:
    """A flag where a duration belongs is a mistake, not `1.0`.

    A falsifying implementation that accepted it -- `bool` is an `int`, so a naive
    `isinstance(value, (int, float))` passes -- would read an enable flag written
    in place of a timeout as a one-second timeout, and the mistake would present
    as an automation that fires almost immediately rather than as a refused file.
    """
    with pytest.raises(InvalidSettingError):
        _value(value).number()


@pytest.mark.parametrize("value", ["30", "30.0", None, [], {}])
def test_a_number_refuses_what_is_not_a_number(value: object) -> None:
    """A string a YAML file quoted, or an absent value, is refused.

    A falsifying implementation that coerced with `float(value)` would raise a
    bare `ValueError` naming neither the key nor the layer, and the search for the
    mistake would start in this module rather than in the file that set it.
    """
    with pytest.raises(InvalidSettingError):
        _value(value).number()


def test_an_integer_reads_an_int() -> None:
    """A count comes back as the int the layer wrote, unchanged."""
    assert _value(50).integer() == 50
    assert isinstance(_value(50).integer(), int)


@pytest.mark.parametrize("value", [True, False, 50.0, 50.5, "50", None])
def test_an_integer_refuses_everything_else_including_a_whole_float(
    value: object,
) -> None:
    """A float that happens to be whole is refused, which is the interesting case.

    A falsifying implementation that accepted `50.0` would silently truncate it,
    and the log's bound -- the reader that cares -- would come from a file whose
    author wrote a duration where a count belonged. Refusing it names the file
    instead of honouring a number nobody chose.
    """
    with pytest.raises(InvalidSettingError):
        _value(value).integer()


@pytest.mark.parametrize("value", [True, False])
def test_a_flag_reads_a_bool(value: bool) -> None:
    """The one kind a flag accepts, unchanged."""
    assert _value(value).flag() is value


@pytest.mark.parametrize("value", ["false", "true", "", 0, 1, None])
def test_a_flag_refuses_a_truthy_or_quoted_value(value: object) -> None:
    """`"false"` is refused, and it is the case the reader exists for.

    A falsifying implementation that used `bool(value)` would read the string
    `"false"` -- exactly what a YAML file produces for an unquoted `false` in some
    writers, and for any value a template rendered -- as enabled, turning the
    product rule's off-by-default into on-by-typo.
    """
    with pytest.raises(InvalidSettingError):
        _value(value).flag()


def test_the_error_names_the_key_the_value_and_the_layer() -> None:
    """The failure carries all three facts, on the exception and in the message.

    A falsifying implementation that raised a bare `TypeError` -- or that carried
    only the value -- would leave a house owner with a traceback pointing into the
    arithmetic, and the two facts that make the mistake findable are which setting
    and which file. `number()` is used as the example because all three readers
    raise through the same constructor.
    """
    with pytest.raises(InvalidSettingError) as raised:
        _value("thirty", layer=Layer.ROOM).number()
    error = raised.value
    assert error.key == _KEY
    assert error.value == "thirty"
    assert error.layer is Layer.ROOM
    assert _KEY in str(error)
    assert str(Layer.ROOM) in str(error)


def test_the_layer_the_error_names_is_the_one_that_decided() -> None:
    """The layer travels from the resolution, so the message names the real file.

    A falsifying implementation that defaulted the layer to `Layer.BUILTIN` -- or
    that re-derived it from the scope -- would name a layer that did not set the
    value, and the file the reader was sent to would be a file with nothing wrong
    in it.
    """
    resolver = _resolver(house={_KEY: "not a number"})
    with pytest.raises(InvalidSettingError) as raised:
        resolver.resolve(_KEY, _HOUSE).number()
    assert raised.value.layer is Layer.HOUSE


def test_every_built_in_default_reads_as_the_kind_its_reader_wants() -> None:
    """Each default passes the reader its consumer uses; a wrong kind fails here.

    The readers are the reason the built-ins carry `500` and `60` as ints and the
    two durations as floats, and nothing else in the suite resolves them. A
    falsifying implementation that wrote `RATE_LIMIT_BOUND_KEY` as `60.0` would
    make every resolution of it raise at the first tick of a real house, naming
    the setting rather than the typo -- so the mistake surfaces here instead.
    """
    for key in (LOG_BOUND_KEY, RATE_LIMIT_BOUND_KEY):
        assert _resolver().resolve(key, _HOUSE).integer() >= 0
    for key in (PRESENCE_QUIET_TIMEOUT_KEY, RATE_LIMIT_WINDOW_KEY):
        assert _resolver().resolve(key, _HOUSE).number() > 0.0
