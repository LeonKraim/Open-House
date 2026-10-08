"""A slot's rule: the logic a slot row holds instead of a device.

`ha_adapter.slot_rules` is pure -- recorded settings in, a rule and its sentence
out -- so the claim here is checkable without Home Assistant: that the three keys
a rule is written under spell one decision, that the four kinds are read in the
four shapes they are stored in, and that each kind answers the two questions the
rest of the product asks of it (*which devices does something have to watch*, and
*does this say which device or whether*).

The load-bearing case is the last one. A condition is the only kind that cannot
name a device, and everything downstream -- whether the person's own pick is kept
or forgotten, whether the row says "the device above while it holds" -- turns on
that one difference, so it is asserted rather than assumed.

It is also the one kind that *carries* a device of its own: a producing rule
displaces the person's pick, and a condition has to be able to move the slot
between the device it gates and the room's binding, which is a distinction one
setting cannot hold. So the two claims about a condition are that it names no
device it *produces* (`picks_the_device` false) and that it does name the device
it *gates* (`device_of`), in the one payload the row records.
"""

from __future__ import annotations

import pytest

from engine.behaviours.declared import option_key, slot_rule_key
from ha_adapter import slot_rules
from ha_adapter.pack_authoring import AuthoringError


def _rule(**kwargs: object) -> slot_rules.SlotRule:
    rule = slot_rules.rule_from(**kwargs)  # type: ignore[arg-type]
    assert rule is not None
    return rule


# --------------------------------------------------------------------------
# What is a rule, and what is nothing at all
# --------------------------------------------------------------------------


def test_an_empty_kind_is_no_rule() -> None:
    """The whole of the clearing story: clearing a rule is one empty write.

    A person who has opened the menu and chosen nothing has decided nothing, so
    there is no rule to read -- which is why the panel can clear a rule by
    sending an empty kind rather than the screen having to know which of three
    keys the last rule happened to write.
    """
    for kind in (None, "", "   ", 4, []):
        assert slot_rules.rule_from(kind) is None
    # And a payload left behind by a previous rule does not make one either.
    assert slot_rules.rule_from("", {"condition": "state"}) is None


def test_the_four_kinds_are_read_in_the_four_shapes_they_are_stored_in() -> None:
    condition = {"condition": "state", "entity_id": "binary_sensor.away", "state": "on"}
    assert _rule(
        kind="condition", value=condition, device="light.study"
    ) == slot_rules.SlotRule(kind="condition", value=condition, device="light.study")
    assert _rule(kind="template", value=" {{ states('sensor.lux') }} ").value == (
        "{{ states('sensor.lux') }}"
    )
    assert _rule(kind="flow", value="sensor.open_house_flow_hall_min_lux").value == (
        "sensor.open_house_flow_hall_min_lux"
    )
    assert _rule(
        kind="script", value="hall_lux", when=["sensor.lux"]
    ) == slot_rules.SlotRule(kind="script", value="hall_lux", when=("sensor.lux",))


def test_a_kind_that_is_not_one_of_the_four_is_refused() -> None:
    with pytest.raises(AuthoringError) as refused:
        slot_rules.rule_from("automation", "x")
    assert "not a kind of slot rule" in str(refused.value)


def test_a_kind_with_nothing_under_it_is_refused_rather_than_read_as_no_rule() -> None:
    """The one shape that would be half a rule, and the reason it is loud.

    A row that says "a script" and names no script looks answered on every screen
    and never fires, so the difference between it and an empty kind -- which is
    silence by design -- is the difference between a person's decision and a
    screen's mistake. The half-rule is refused by naming what is missing.
    """
    for kwargs, expected in (
        (
            {"kind": "condition", "value": {}, "device": "light.study"},
            "condition rule is empty",
        ),
        (
            {"kind": "condition", "value": {"condition": "state"}, "device": ""},
            "gates no device",
        ),
        ({"kind": "template", "value": "   "}, "template rule is empty"),
        ({"kind": "flow", "value": ""}, "flow has no entity"),
        ({"kind": "script", "value": "  ", "when": ["sensor.lux"]}, "names no script"),
    ):
        with pytest.raises(AuthoringError) as refused:
            slot_rules.rule_from(**kwargs)  # type: ignore[arg-type]
        assert expected in str(refused.value)


def test_a_condition_gates_a_device_and_is_refused_without_one() -> None:
    """A condition decides *whether*, so the device it asks about has to be named.

    It cannot be the row's `slot.<slot>.entity` either: that key is what the module
    acts through, and a condition's watcher has to be able to empty it while the
    condition is false -- so a pick still sitting in it would put the gated device
    back the moment the condition failed, which is the exact opposite of what the
    rule says. The device therefore travels inside the rule.
    """
    rule = _rule(kind="condition", value={"condition": "state"}, device=" light.study ")
    assert slot_rules.device_of(rule) == "light.study"
    # Every other kind reads as gating nothing, whether or not it was given one:
    # a caller must not have to know which kind it holds before it may ask.
    assert slot_rules.device_of(_rule(kind="template", value="{{ 'a.b' }}")) == ""
    assert (
        slot_rules.device_of(
            _rule(kind="template", value="{{ 'a.b' }}", device="light.study")
        )
        == ""
    )


def test_a_script_with_nothing_to_watch_is_refused() -> None:
    """A script runs when something calls it, so "who calls it" is asked for.

    Nothing here can work that out: unlike a condition (which names the entities
    it decides about) and a flow (which writes an entity of its own), a script is
    a sequence of a person's own and says nothing about what should set it off.
    """
    with pytest.raises(AuthoringError) as refused:
        slot_rules.rule_from("script", "hall_lux", when=[])
    assert "needs to know when to run" in str(refused.value)
    # An entity list that holds nothing a trigger can use is the same answer.
    with pytest.raises(AuthoringError):
        slot_rules.rule_from("script", "hall_lux", when=[None, ""])


def test_a_script_takes_its_id_with_or_without_its_domain() -> None:
    """The picker holds `script.hall_lux` and a call takes `hall_lux`."""
    for spelling in ("hall_lux", "script.hall_lux"):
        rule = _rule(kind="script", value=spelling, when=["sensor.lux"])
        assert rule.value == "hall_lux"


def test_an_entity_list_beside_a_kind_that_does_not_use_one_is_dropped() -> None:
    """Switching a row from a script to a template must not keep asking for one."""
    rule = _rule(kind="template", value="{{ 'light.hall' }}", when=["sensor.lux"])
    assert rule.when == ()
    assert "when" not in slot_rules.facts_of(rule)


def test_the_entities_a_when_list_names_are_deduplicated_in_order() -> None:
    rule = _rule(
        kind="script", value="hall_lux", when=["sensor.b", "sensor.a", "sensor.b"]
    )
    assert rule.when == ("sensor.b", "sensor.a")


# --------------------------------------------------------------------------
# Which device, or whether -- the one difference that changes what is kept
# --------------------------------------------------------------------------


def test_three_of_the_kinds_say_which_device_and_a_condition_says_whether() -> None:
    """**The claim the row's device behaviour rests on.**

    A template, a flow and a script each *produce* an entity id, so the device a
    person picked is displaced and is forgotten when the rule is set. A condition
    produces a yes or a no and never an entity, so the person's pick is the thing
    it gates and is kept -- and a screen that got this wrong would either throw
    away a device the condition still needs, or leave a lying "Change device"
    beside a rule that ignores it.
    """
    assert slot_rules.picks_the_device(_rule(kind="template", value="{{ 'a.b' }}"))
    assert slot_rules.picks_the_device(
        _rule(kind="flow", value="sensor.open_house_flow_hall_min_lux")
    )
    assert slot_rules.picks_the_device(
        _rule(kind="script", value="hall_lux", when=["sensor.lux"])
    )
    assert not slot_rules.picks_the_device(
        _rule(
            kind="condition",
            value={"condition": "state", "entity_id": "a.b"},
            device="c.d",
        )
    )


# --------------------------------------------------------------------------
# What something has to watch, per kind
# --------------------------------------------------------------------------


def test_a_condition_is_watched_by_the_entities_it_decides_about() -> None:
    """The same walk the cast path uses, reused rather than written twice."""
    rule = _rule(
        kind="condition",
        device="light.study",
        value={
            "condition": "and",
            "conditions": [
                {"condition": "state", "entity_id": "binary_sensor.a", "state": "on"},
                {
                    "condition": "or",
                    "conditions": [
                        {"condition": "state", "entity_id": ["binary_sensor.b"]},
                        {"condition": "state", "entity_id": "binary_sensor.a"},
                    ],
                },
            ],
        },
    )
    assert slot_rules.watched_by(rule) == ("binary_sensor.a", "binary_sensor.b")


def test_a_flow_is_watched_by_the_entity_it_writes_and_a_script_by_its_list() -> None:
    flow = _rule(kind="flow", value="sensor.open_house_flow_hall_min_lux")
    assert slot_rules.watched_by(flow) == ("sensor.open_house_flow_hall_min_lux",)
    script = _rule(kind="script", value="hall_lux", when=["sensor.lux", "sensor.sun"])
    assert slot_rules.watched_by(script) == ("sensor.lux", "sensor.sun")


def test_a_template_is_watched_by_nothing_here() -> None:
    """Not a gap: Home Assistant's own tracker reads the text and finds the rest.

    A reader here would be re-implementing a template parser to answer a question
    the platform already answers better, so the honest answer is that *this*
    module has nobody to listen to -- and the watcher is what knows to hand the
    text to `async_track_template_result` instead of to a state listener.
    """
    rule = _rule(kind="template", value="{{ states('sensor.lux') }}")
    assert slot_rules.watched_by(rule) == ()


# --------------------------------------------------------------------------
# What the row says under the device
# --------------------------------------------------------------------------


def test_each_kind_says_what_it_is_and_a_script_says_when_it_runs() -> None:
    assert slot_rules.summary(_rule(kind="template", value="{{ 'a.b' }}")) == (
        "decided by a template"
    )
    assert (
        slot_rules.summary(
            _rule(kind="flow", value="sensor.open_house_flow_hall_min_lux")
        )
        == "decided by a flow"
    )
    # A condition's sentence has to say the thing that cannot be inferred: the
    # device above is still used, some of the time.
    assert (
        slot_rules.summary(
            _rule(
                kind="condition",
                value={"condition": "state", "entity_id": "a.b"},
                device="light.study",
            )
        )
        == "decided by a condition: light.study while it holds"
    )
    # A condition read back out of a file that predates the gated device says what
    # it can rather than nothing -- `recorded_rule` is the reporting reader.
    assert (
        slot_rules.summary(
            slot_rules.recorded_rule(  # type: ignore[arg-type]
                "condition", {"condition": "state", "entity_id": "a.b"}
            )
        )
        == "decided by a condition: the device above while it holds"
    )
    # And a script's says what sets it off, because that is the interesting part.
    assert (
        slot_rules.summary(_rule(kind="script", value="hall_lux", when=["sensor.lux"]))
        == "decided by a script, when sensor.lux changes"
    )
    assert (
        slot_rules.summary(
            _rule(kind="script", value="hall_lux", when=["sensor.lux", "sensor.sun"])
        )
        == "decided by a script, when any of sensor.lux, sensor.sun changes"
    )


# --------------------------------------------------------------------------
# Where a rule is written, which is in the module's own namespace
# --------------------------------------------------------------------------


def test_a_rule_is_recorded_in_the_module_s_own_namespace_in_the_room_it_is_in() -> (
    None
):
    """**A rule is a module's**, which is what makes it per-module and per-room.

    A slot's *device* is already per module per room (`live_modules.set_slot`), and
    a rule decides that device -- so it is recorded under the same namespace and
    inherits the same two facts: the module it belongs to and the room it was set
    in. A rule on the room's binding instead would decide every module at once,
    which is a different feature.
    """
    assert slot_rules.keys_of("light_group") == (
        "slot.light_group.kind",
        "slot.light_group.rule",
        "slot.light_group.when",
        "slot.light_group.device",
    )
    key = option_key("bedtime", slot_rule_key("light_group", "kind"))
    assert key == "module.bedtime.slot.light_group.kind"
    # And a rule writes two facts when it has no `when` and three when it has.
    template = slot_rules.facts_of(_rule(kind="template", value="{{ 'a.b' }}"))
    assert template == {"kind": "template", "rule": "{{ 'a.b' }}"}
    script = slot_rules.facts_of(
        _rule(kind="script", value="hall_lux", when=["sensor.lux"])
    )
    assert script == {
        "kind": "script",
        "rule": "hall_lux",
        "when": ["sensor.lux"],
    }
    # A condition writes the device it gates under its own key, rather than in a
    # mapping beside the payload: a condition's config is *itself* a mapping with
    # a `condition` key, so a nested shape and a bare config are the same JSON and
    # no reader could tell which it was holding.
    condition = slot_rules.facts_of(
        _rule(kind="condition", value={"condition": "state"}, device="light.study")
    )
    assert condition == {
        "kind": "condition",
        "rule": {"condition": "state"},
        "device": "light.study",
    }


def test_a_condition_written_and_read_back_is_the_same_rule() -> None:
    """The recorded shape round-trips, which is what a restart asks of it."""
    rule = _rule(
        kind="condition",
        value={"condition": "state", "entity_id": "binary_sensor.away", "state": "on"},
        device="light.study",
    )
    facts = slot_rules.facts_of(rule)
    assert (
        slot_rules.recorded_rule(
            facts["kind"], facts["rule"], facts.get("when"), facts.get("device")
        )
        == rule
    )
    # And a record written before a condition carried a device is still read as
    # the condition it is rather than refused: `recorded_rule` reports.
    older = slot_rules.recorded_rule(  # type: ignore[arg-type]
        "condition", {"condition": "state", "entity_id": "binary_sensor.away"}
    )
    assert older is not None
    assert older.value == {"condition": "state", "entity_id": "binary_sensor.away"}
    assert older.device == ""


def test_a_script_s_answer_comes_back_under_the_same_name_a_cast_s_does() -> None:
    """One name for "the value this logic worked out", in both screens.

    A detached cast's value is `cast_document.VALUE` and a script rule's answer is
    read out of a `stop: response_variable:` under the same key, because a script
    should not have to know which screen called it -- and because a second
    constant that happened to spell the same two words would be a second thing to
    keep in step.
    """
    from ha_adapter.cast_document import VALUE

    assert slot_rules.RESPONSE == VALUE == "oh_value"


# --------------------------------------------------------------------------
# A rule as a module of its own (a detach)
# --------------------------------------------------------------------------


def test_a_detached_rule_publishes_under_the_slot_s_own_name() -> None:
    """The output a detached slot rule makes is named after the *slot*.

    An input's cast is keyed by the input's name, and a slot's rule has to be
    keyed by the slot's -- a new module whose output was called `value` would tell
    a person nothing about what it decided, and the slot it came from is right
    there in the name.
    """
    detached = slot_rules.detached(
        _rule(kind="template", value="{{ states('sensor.lux') }}"),
        slot="light_group",
        title="Bedtime: light group",
        trigger=("sensor.lux",),
    )
    assert detached.pick == ("oh_value", "light_group")
    # A template names nothing to watch, so what starts the new module is entirely
    # the person's answer -- which is why it is passed in rather than derived.
    assert detached.watched == ("sensor.lux",)


def test_a_detached_script_rule_watches_what_called_it() -> None:
    """A script's `when` travels with it, and is not asked for again.

    The `when` is part of the rule -- it is what the *watcher* calls the script on
    -- so a detach knows it already. Re-asking would be asking a person to state
    something the house has recorded, and the answer could disagree with what the
    row was already doing.
    """
    detached = slot_rules.detached(
        _rule(kind="script", value="hall_lux", when=("sensor.lux",)),
        slot="light_group",
        title="Bedtime: light group",
        trigger=("binary_sensor.away",),
    )
    # The rule's own entity, then the person's: watched beside it, not instead.
    assert detached.watched == ("sensor.lux", "binary_sensor.away")
    assert detached.document["trigger"] == [
        {"trigger": "state", "entity_id": ["sensor.lux", "binary_sensor.away"]}
    ]
    assert {"action": "script.hall_lux", "response_variable": "oh_response"} in (
        detached.document["action"]
    )


def test_a_detached_condition_rule_keeps_the_device_it_gates() -> None:
    """A condition's gated device is the rule's, and the detach does not lose it.

    The device a condition gates lives nowhere else -- it is not the slot's entity
    (that is what the watcher *writes*) -- so a detach that dropped it would leave
    a module that could not be read as saying anything about a device at all. The
    watched list is derived from the condition itself, which is the one thing
    about a detached condition a person could not have worked out.
    """
    detached = slot_rules.detached(
        _rule(
            kind="condition",
            value={
                "condition": "state",
                "entity_id": "binary_sensor.away",
                "state": "on",
            },
            device="light.study",
        ),
        slot="light_group",
        title="Bedtime: light group",
    )
    assert detached.watched == ("binary_sensor.away",)
    assert detached.pick == ("oh_value", "light_group")


def test_a_half_written_rule_is_refused_by_the_detach_in_its_own_words() -> None:
    """The refusals a person reads come from `cast_document`, not from here.

    The two screens ask the same question of the same four kinds, so the sentence
    that says why a script cast cannot be detached on its own is written once --
    and a slot rule is refused for the same reasons and in the same words.
    """
    with pytest.raises(AuthoringError, match="needs something to start it"):
        slot_rules.detached(
            _rule(kind="template", value="{{ 1 }}"),
            slot="light_group",
            title="Bedtime: light group",
        )
