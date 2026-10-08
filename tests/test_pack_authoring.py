"""The Dev tab's conversion: reading a source, drafting a module, exporting one.

`ha_adapter.pack_authoring` is the half of the Dev tab that is pure -- a document
in, a mapping out -- and this is the module that holds it to its contract without
a Home Assistant in the room. The plan the panel sends, the manifest the draft
writes and the automations the export emits are three restatements of one
reading, so the assertions here are mostly about *agreement*: the row the reading
found, the clause the manifest carries, the entity the export names.

**The committed blueprint is the fixture, and it is the real one.** The Dynamic
Lighting blueprint under `docker/ha-config/blueprints` is the document the Dev tab
was built against -- 18 inputs, four entities among them, three calls the engine's
service list does not have -- so a test that read a hand-made stand-in would pass
while the screen did not. It is read as text rather than imported, because the
importing is Home Assistant's job and `dev_authoring`'s; what is under test is
what `read_source` makes of the text Home Assistant hands over.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from engine import vocabulary as engine_vocabulary
from ha_adapter import pack_authoring

ROOT = Path(__file__).resolve().parents[1]

#: The engine's closed service list, which is what the Dev tab's reading is given
#: and is why the logbook calls come back marked unsupported. A hand-written
#: stand-in would be a test of the stand-in: the claim under test is about this
#: list and these calls.
KNOWN_SERVICES = tuple(engine_vocabulary.load_service_states(ROOT))

#: The two roles the module is drafted around, and the domains the catalog says
#: they accept. The real catalog's answer for these two, spelled here so that a
#: draft test's refusal names the slot rather than the fixture.
SLOTS_ACCEPTING = {"ambient_light_sensor": ("sensor",), "light_group": ("light",)}

#: The blueprint the whole feature exists for, as it is deployed beside the dev
#: house. A file rather than an inline string so that the reading is tested
#: against the document a person imports, not against a paraphrase of it.
DYNAMIC_LIGHTING = (
    ROOT
    / "docker"
    / "ha-config"
    / "blueprints"
    / "automation"
    / "MarqBarq"
    / "dynamic-lighting.yaml"
)

#: The blueprint's four entity inputs, and the slot each one is a role for. The
#: two that carry no `default` are the ones the module must require -- reading
#: `default`'s value rather than its presence made both optional, which produced
#: a module that asked a room for nothing.
EXPECTED_ENTITIES = {
    "input:lux_sensor": "ambient_light_sensor",
    "input:lights": "light_group",
}


def _read() -> pack_authoring.Analysis:
    return pack_authoring.analyse(
        pack_authoring.read_source(DYNAMIC_LIGHTING.read_text(encoding="utf-8")),
        known_services=KNOWN_SERVICES,
    )


def _row(analysis: pack_authoring.Analysis, key: str) -> pack_authoring.EntityRow:
    for row in analysis.entities:
        if row.key == key:
            return row
    raise AssertionError(
        f"the reading found no entity {key!r}; it found "
        f"{[row.key for row in analysis.entities]}"
    )


def _service(
    analysis: pack_authoring.Analysis, tail: str
) -> pack_authoring.BehaviourRow:
    for row in analysis.services:
        if row.service.endswith(f".{tail}"):
            return row
    raise AssertionError(
        f"the reading found no call to {tail!r}; it found "
        f"{[row.service for row in analysis.services]}"
    )


def _value(analysis: pack_authoring.Analysis, key: str) -> pack_authoring.ValueRow:
    for row in analysis.values:
        if row.key == key:
            return row
    raise AssertionError(
        f"the reading found no value {key!r}; it found "
        f"{[row.key for row in analysis.values]}"
    )


# -- reading -----------------------------------------------------------------


def test_a_blueprint_is_read_as_a_blueprint() -> None:
    """The `blueprint:` block is what makes it one, and the reading says so."""
    source = pack_authoring.read_source(DYNAMIC_LIGHTING.read_text(encoding="utf-8"))

    assert source.blueprint is True
    assert source.title == "Dynamic Lighting for Better Sleep"
    assert source.inputs, "a blueprint with no inputs is not this blueprint"


def test_every_input_becomes_a_row() -> None:
    """Eighteen inputs, four of them entities and fourteen of them values.

    The split is the whole of what the panel draws: an entity row is a slot to
    bind, a value row is a setting to name. A reading that put a sensor in the
    settings table would offer a person a number where a device belongs.
    """
    analysis = _read()

    assert len(analysis.entities) == 4
    assert len(analysis.values) == 14
    assert {row.key for row in analysis.entities} == {
        "input:lux_sensor",
        "input:lights",
        "input:weather_entity",
        "input:bypass_light",
    }


def test_an_input_with_no_default_is_required_and_one_with_a_default_is_not() -> None:
    """`default`'s *presence* is the test, not its value.

    Home Assistant refuses to create an automation from a blueprint input that
    carries no `default` at all, and accepts one whose `default:` is explicitly
    null. Reading the value instead of the key marked the lux sensor and the
    lights -- neither of which can have a default -- as optional, and the module
    that came out required no slots: it installed into rooms it could not act in.
    """
    analysis = _read()

    assert _row(analysis, "input:lux_sensor").optional is False
    assert _row(analysis, "input:lights").optional is False
    assert _row(analysis, "input:bypass_light").optional is True


def test_a_target_selector_is_read_through_its_list() -> None:
    """`lights` is a `target:`, whose `entity:` is a *list* of selectors.

    The one shape that made the lights input suggest no slot at all: every other
    entity input names `selector: {entity: {domain: [sensor]}}`, and this one
    names `selector: {target: {entity: [{domain: [light]}]}}`, one list deeper.
    """
    analysis = _read()

    assert _row(analysis, "input:lights").domain == "light"
    assert _row(analysis, "input:lux_sensor").domain == "sensor"


def test_a_call_the_engine_cannot_make_is_read_and_marked() -> None:
    """The three `logbook.log` calls are found and reported as unsupported.

    Not dropped: the screen has to be able to say that the source did something
    the engine will not, and a reading that silently omitted them would make the
    saved module a shorter list of behaviours than the automation it came from
    with nothing to explain the difference.
    """
    analysis = _read()

    logs = [row for row in analysis.services if row.service == "logbook.log"]
    assert len(logs) == 3
    assert all(row.supported is False for row in logs)


def test_the_service_list_decides_what_is_supported() -> None:
    """With no vocabulary every call is unknown, and with one the answer is real.

    `supported` is `None` when the caller had no list to check against, and the
    panel's greying is driven by it -- so "the caller did not say" and "the
    engine cannot" have to stay distinguishable.
    """
    source = pack_authoring.read_source(DYNAMIC_LIGHTING.read_text(encoding="utf-8"))

    without = pack_authoring.analyse(source)
    with_list = pack_authoring.analyse(source, known_services=("light.turn_on",))

    assert all(row.supported is None for row in without.services)
    assert _service(with_list, "turn_on").supported is True
    assert _service(with_list, "turn_off").supported is False


def test_suggested_slots_are_named_from_the_input_that_wants_them() -> None:
    """A domain maps to many roles, and the input's own name picks between them.

    `sensor` is the domain of the lux sensor and of any temperature sensor, and
    the catalog's preference for the domain is `temperature_sensor`. Without the
    hint the lux sensor suggested a role for a device it is not -- a module that
    would never bind in a room that had the device the blueprint asked for.
    """
    assert (
        pack_authoring.suggest_slot(
            "sensor", label="Lux sensor", slots={"ambient_light_sensor": ("sensor",)}
        )
        == "ambient_light_sensor"
    )


def test_a_suggestion_is_none_when_no_slot_accepts_the_domain() -> None:
    """The honest empty answer, rather than a role the catalog would refuse."""
    assert (
        pack_authoring.suggest_slot("weather", slots={"light_group": ("light",)}) == ""
    )


def test_a_document_that_is_not_an_automation_is_refused() -> None:
    """A shopping list is not a module, and the refusal says which way."""
    with pytest.raises(pack_authoring.AuthoringError):
        pack_authoring.read_source("milk: 1\neggs: 6\n")


def test_yaml_that_will_not_parse_is_refused_by_name() -> None:
    with pytest.raises(pack_authoring.AuthoringError, match="not valid YAML"):
        pack_authoring.read_source("alias: [unclosed\n")


# -- drafting ----------------------------------------------------------------


def _plan(analysis: pack_authoring.Analysis, **overrides: object) -> dict:
    """The decisions a person would make on the Dynamic Lighting reading.

    The lux sensor and the lights are slots, the weather entity and the bypass
    are not part of the module, the two `light.*` calls are kept and the logbook
    calls are dropped -- which is what the panel pre-fills and what a person
    clicking through with the defaults would send.
    """
    plan: dict = {
        "name": "dynamic_lighting_for_better_sleep",
        "title": analysis.title,
        "description": "A tidy description.",
        "version": "1.0.0",
        "license": "mit",
        "entities": {
            "input:lux_sensor": {
                "decision": "slot",
                "slot": "ambient_light_sensor",
                "required": True,
            },
            "input:lights": {
                "decision": "slot",
                "slot": "light_group",
                "required": True,
            },
            "input:weather_entity": {"decision": "ignore"},
            "input:bypass_light": {"decision": "ignore"},
        },
        "values": {row.key: {"decision": "constant"} for row in analysis.values},
        "behaviours": [
            {
                "key": row.key,
                "keep": row.supported is True,
                # The panel's own defaults: the tail of the service, the first
                # trigger the reading found, and the role the call targets.
                "name": row.service.split(".")[-1],
                "slot": "light_group",
                "watched": [],
                "trigger": analysis.triggers[0] if analysis.triggers else "state",
                "condition": "",
                "scope": "room",
                "priority": 10,
            }
            for row in analysis.services
        ],
    }
    plan.update(overrides)
    return plan


def test_a_draft_names_the_slots_the_decisions_chose() -> None:
    analysis = _read()
    draft = pack_authoring.draft_module(
        analysis, _plan(analysis), slots=SLOTS_ACCEPTING
    )

    document = draft.document
    assert document["name"] == "dynamic_lighting_for_better_sleep"
    assert document["requires_slots"] == ["ambient_light_sensor", "light_group"]


def test_a_draft_keeps_only_the_behaviours_that_were_kept() -> None:
    """Three logbook calls were found and two were dropped; two survive."""
    analysis = _read()
    draft = pack_authoring.draft_module(analysis, _plan(analysis))

    behaviours = draft.document["behaviours"]
    assert [row["name"] for row in behaviours] == ["turn_off", "turn_on"]
    assert [row["services"] for row in behaviours] == [
        ["light.turn_off"],
        ["light.turn_on"],
    ]


def test_a_draft_requires_a_slot_only_once() -> None:
    """Two inputs that want the same role is a refusal, not a silent second use.

    A slot is one device: a pack that named it twice would resolve both to
    whatever one room bound, and act on the wrong thing.
    """
    analysis = _read()
    plan = _plan(analysis)
    plan["entities"]["input:weather_entity"] = {
        "decision": "slot",
        "slot": "ambient_light_sensor",
        "required": True,
    }

    with pytest.raises(pack_authoring.AuthoringError, match="two entities"):
        pack_authoring.draft_module(analysis, plan)


def test_a_draft_refuses_a_slot_the_catalog_does_not_accept() -> None:
    """The domain check runs when the caller has the vocabulary, and it refuses."""
    analysis = _read()
    plan = _plan(analysis)
    plan["entities"]["input:lux_sensor"] = {
        "decision": "slot",
        "slot": "light_group",
        "required": True,
    }

    with pytest.raises(pack_authoring.AuthoringError):
        pack_authoring.draft_module(analysis, plan, slots={"light_group": ("light",)})


def test_a_draft_refuses_a_decision_about_a_row_nobody_read() -> None:
    """The plan is checked against the reading, not trusted."""
    analysis = _read()
    plan = _plan(analysis)
    plan["entities"]["input:imaginary"] = {
        "decision": "slot",
        "slot": "light_group",
        "required": True,
    }

    with pytest.raises(pack_authoring.AuthoringError, match="imaginary"):
        pack_authoring.draft_module(analysis, plan)


def test_the_pinned_artifact_is_shaped_as_an_automation() -> None:
    """A `provides` entry declaring class `automation` must pin an automation.

    The class is read off the file, from the *singular* `trigger`/`action` keys,
    so a draft written with the modern plural spelling is a pack the sandbox
    refuses as a class mismatch -- which is what every save answered with until
    the spelling was fixed.
    """
    analysis = _read()
    draft = pack_authoring.draft_module(analysis, _plan(analysis))

    assert re.search(r"^trigger:$", draft.artifact_text, re.MULTILINE)
    assert re.search(r"^action:$", draft.artifact_text, re.MULTILINE)
    assert re.search(r"^triggers:$", draft.artifact_text, re.MULTILINE) is None
    assert re.search(r"^actions:$", draft.artifact_text, re.MULTILINE) is None


def test_the_pinned_artifact_names_the_role_each_action_goes_through() -> None:
    """A slot name where an entity would be, as `packs/official` writes them."""
    analysis = _read()
    draft = pack_authoring.draft_module(analysis, _plan(analysis))

    assert "entity_id: light_group" in draft.artifact_text


def test_a_setting_becomes_an_option_with_its_bounds() -> None:
    """The blueprint's min/max/unit survive into the manifest's `options`."""
    analysis = _read()
    # The seconds-valued row rather than the first row with a minimum: the
    # blueprint's first such row is a colour temperature in `K`, and a wait
    # measured in kelvin is refused (see the two tests below).
    row = next(row for row in analysis.values if row.key == "input:transition_time")
    plan = _plan(analysis)
    plan["values"][row.key] = {
        "decision": "setting",
        "key": "the_setting",
        "type": "integer",
        "title": row.label,
        "description": row.description,
        "default": 40,
        "minimum": row.minimum,
        "maximum": row.maximum,
        "unit": row.unit,
    }
    # On every row, because the reading's first three calls are logbook calls the
    # plan does not keep -- a `for` on one of those is never written and never
    # checked, which is the behaviour the test would then be testing.
    for decision in plan["behaviours"]:
        decision["for"] = "the_setting"

    draft = pack_authoring.draft_module(analysis, plan)
    option = next(
        option for option in draft.document["options"] if option["key"] == "the_setting"
    )

    assert option["default"] == 40
    assert option["minimum"] == row.minimum
    assert option["maximum"] == row.maximum
    assert option["unit"] == row.unit
    # Forced, because the wait is what makes the value readable and the panel's
    # own type came from a selector that said `integer`.
    assert option["type"] == "duration"
    assert draft.document["behaviours"][0]["for"] == "the_setting"


def test_a_wait_measured_in_minutes_is_written_in_seconds() -> None:
    """A unit the source counted in is not the unit the engine waits in.

    The engine reads a `for` as a number of seconds and the manifest's `unit` is
    a label it never parses (`schemas/pack-manifest/1.4.0.json`), so a setting
    left at `default: 3` with `unit: minutes` is a control reading "3 minutes"
    over a behaviour that waits three seconds. This blueprint declares both
    `trigger_interval` and `color_transition_minutes` that way.
    """
    analysis = _read()
    row = _value(analysis, "input:trigger_interval")
    assert row.unit == "minutes"
    assert row.default == 3
    plan = _plan(analysis)
    plan["values"][row.key] = {
        "decision": "setting",
        "key": "trigger_interval",
        "type": row.kind,
        "title": row.label,
        "description": row.description,
        "default": row.default,
        "minimum": row.minimum,
        "maximum": row.maximum,
        "unit": row.unit,
    }
    for decision in plan["behaviours"]:
        decision["for"] = "trigger_interval"

    draft = pack_authoring.draft_module(analysis, plan)
    option = next(
        option
        for option in draft.document["options"]
        if option["key"] == "trigger_interval"
    )

    assert option["default"] == 180
    assert option["minimum"] == 60
    assert option["maximum"] == 300
    assert option["unit"] == "seconds"
    assert option["type"] == "duration"
    # The title named the unit the value used to be in; a control counting
    # seconds under the word "minutes" is the same defect in prose.
    assert option["title"] == "Trigger Interval"


def test_a_wait_measured_in_something_that_is_not_a_length_of_time_is_refused() -> None:
    """A wait is a number of seconds, so a brightness cannot be one.

    `max_brightness_percent` is a `number` row with a `%` unit and a default of
    100. Left alone it would become a duration of a hundred seconds -- a wait
    sixty-times-over wrong in a way no clause of the manifest could record, which
    is why the refusal names the unit rather than writing a number that means
    something else.
    """
    analysis = _read()
    row = _value(analysis, "input:max_brightness_percent")
    assert row.unit == "%"
    plan = _plan(analysis)
    plan["values"][row.key] = {
        "decision": "setting",
        "key": "brightness",
        "type": row.kind,
        "title": row.label,
        "description": row.description,
        "default": row.default,
        "minimum": row.minimum,
        "maximum": row.maximum,
        "unit": row.unit,
    }
    for decision in plan["behaviours"]:
        decision["for"] = "brightness"

    with pytest.raises(pack_authoring.AuthoringError, match="length of time"):
        pack_authoring.draft_module(analysis, plan)


def test_a_setting_nothing_reads_is_refused() -> None:
    """The schema's own rule, enforced where the plan can be named.

    "An option is only worth declaring if something reads it ... the panel will
    render a control for it and the control will do nothing"
    (`schemas/pack-manifest/1.4.0.json`). The engine reads a `duration` through a
    behaviour's `for` and a role's reach switch and nothing else, so a setting
    no behaviour waits on is exactly the control that warning is about.
    """
    analysis = _read()
    plan = _plan(analysis)
    row = analysis.values[0]
    plan["values"][row.key] = {
        "decision": "setting",
        "key": "unread",
        "type": "integer",
        "title": row.label,
        "default": 40,
    }

    with pytest.raises(pack_authoring.AuthoringError, match="read by nothing"):
        pack_authoring.draft_module(analysis, plan)


def test_a_wait_whose_default_is_not_a_number_is_refused() -> None:
    """A duration is a number of seconds, so its default has to be one.

    The panel offers every value row as something to wait for unless it is told
    not to, and a blueprint's booleans are rows like any other: waiting on one
    produced `{type: duration, default: True}`, which the manifest validator
    refuses as a mismatched option -- a message about a clause, for a field a
    person set. The refusal belongs here, where the field can be named.
    """
    analysis = _read()
    plan = _plan(analysis)
    row = analysis.values[0]
    plan["values"][row.key] = {
        "decision": "setting",
        "key": "a_boolean",
        "type": "boolean",
        "title": row.label,
        "default": True,
        # The row this stands in for is a colour temperature in `K`, and a unit
        # is a fault of its own, tested above; the default is what is under test
        # here, so the unit is taken out of the way.
        "unit": None,
    }
    for decision in plan["behaviours"]:
        decision["for"] = "a_boolean"

    with pytest.raises(pack_authoring.AuthoringError, match="length of time"):
        pack_authoring.draft_module(analysis, plan)


def test_a_hold_must_name_a_setting_the_module_declares() -> None:
    """`for` names a duration option, and one this module does not declare is a
    manifest the validator would refuse with a message about the clause rather
    than about the behaviour."""
    analysis = _read()
    plan = _plan(analysis)
    for decision in plan["behaviours"]:
        decision["for"] = "not_a_setting"

    with pytest.raises(pack_authoring.AuthoringError, match="not_a_setting"):
        pack_authoring.draft_module(analysis, plan)


# -- exporting ---------------------------------------------------------------


def test_an_export_names_the_entities_a_slot_resolved_to() -> None:
    documents = pack_authoring.automation_documents(
        [
            {
                "name": "turn_on",
                "trigger": "time_pattern",
                "services": ["light.turn_on"],
                "slots": ["light_group"],
            }
        ],
        bindings={"light_group": ("light.hall", "light.landing")},
        options={},
        title="Example",
    )

    action = documents[0]["actions"][0]
    assert action["service"] == "light.turn_on"
    assert action["target"]["entity_id"] == ["light.hall", "light.landing"]


def test_a_single_entity_is_not_wrapped_in_a_list() -> None:
    documents = pack_authoring.automation_documents(
        [
            {
                "name": "turn_on",
                "trigger": "state",
                "services": ["light.turn_on"],
                "slots": ["light_group"],
            }
        ],
        bindings={"light_group": ("light.hall",)},
        options={},
        title="Example",
    )

    assert documents[0]["actions"][0]["target"]["entity_id"] == "light.hall"


def test_a_slot_that_resolved_to_nothing_names_nothing() -> None:
    """The one translation error that changes what a house does.

    `service: light.turn_off` with no `target` at all is Home Assistant's "every
    light there is", so a module whose slot this room does not bind would export
    as a house-wide action where the module decided nothing. The export must say
    *no entity* rather than say nothing.
    """
    documents = pack_authoring.automation_documents(
        [
            {
                "name": "turn_off",
                "trigger": "time_pattern",
                "services": ["light.turn_off"],
                "slots": ["light_group"],
            }
        ],
        bindings={"light_group": ()},
        options={},
        title="Example",
    )

    action = documents[0]["actions"][0]
    assert "target" in action
    assert action["target"]["entity_id"] == []


def test_a_behaviour_with_no_slot_carries_no_target() -> None:
    """No role is not the same as a role that resolved to nothing.

    A call that needs no device is written without a target; a call that needed
    one and found none is written with an empty one. Collapsing the two would
    turn the second back into the first, which is the whole error.
    """
    documents = pack_authoring.automation_documents(
        [
            {
                "name": "say_hello",
                "trigger": "time",
                "services": ["notify.send_message"],
                "slots": [],
            }
        ],
        bindings={},
        options={},
        title="Example",
    )

    assert "target" not in documents[0]["actions"][0]


def test_the_exported_text_is_the_documents_it_was_given() -> None:
    """Two automations, two YAML documents, in order -- what the editor takes."""
    documents = (
        {"alias": "one", "triggers": [], "actions": []},
        {"alias": "two", "triggers": [], "actions": []},
    )
    text = pack_authoring.automation_text(documents)

    assert text.index("alias: one") < text.index("alias: two")
