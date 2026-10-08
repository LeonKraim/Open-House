"""Detaching a cast: a row's logic turned into a module of its own.

`ha_adapter.cast_document` is pure -- a cast in, a document out -- so the claim
here is checkable without Home Assistant: the document it writes is one the
importer already reads, its pick is one `declare_outputs` already honours, and
the three things a cast *is* (its logic, its entities, and what starts it) arrive
in the document intact.

The load-bearing case is the third one. A cast costs nothing to attach precisely
because it rides whatever run its host already has; a module of its own has no
run, so the interesting failures here are the ones where the trigger is wrong or
absent, and both are asserted rather than assumed.
"""

from __future__ import annotations

import pytest
import yaml

from ha_adapter import cast_document, module_host
from ha_adapter.pack_authoring import AuthoringError


def _detach(**kwargs: object) -> cast_document.Detached:
    base: dict[str, object] = {
        "title": "Hall light level",
        "input_name": "min_lux",
        "cast": "template",
        "template": "{{ states('sensor.lux') | int }}",
        "trigger": ["sensor.lux"],
    }
    base.update(kwargs)
    return cast_document.detached_document(**base)  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# The document is a document: the importer reads it, the build publishes from it
# --------------------------------------------------------------------------


def test_the_document_a_detached_cast_writes_is_one_the_importer_reads() -> None:
    """The whole point of writing a *document* is that nothing else is new.

    A detached cast is not a fifth cast kind with its own storage: it is a
    document, hosted by the path an import takes, which is what makes it appear in
    the room's module list and every screen that already lists modules.
    """
    detached = _detach()
    text = yaml.safe_dump(dict(detached.document), sort_keys=False)
    source = module_host.read_module_source(text)
    assert source.title == "Hall light level"
    # And the automation Home Assistant would run is that document, filled in.
    assert module_host.instantiate(source, {})["alias"] == "Hall light level"


def test_the_pick_a_detach_reports_is_one_the_build_honours() -> None:
    """`pick` is answered here, not guessed by the screen.

    The variable the document writes is one this module put there. A screen
    working the candidate out from the outside would be guessing at the thing
    that was just decided, so the pair is handed over -- and the assertion is that
    the *build* accepts it, not merely that it has the shape a build would want.
    """
    detached = _detach()
    text = yaml.safe_dump(dict(detached.document), sort_keys=False)
    source = module_host.read_module_source(text)
    outputs = module_host.declare_outputs(source, {}, [detached.pick])
    assert len(outputs) == 1
    output = outputs[0]
    assert output.key == "min_lux"
    assert output.expression == cast_document.VALUE
    # **A variable carries its own name**, which is what places the publisher in
    # the branch that defines it rather than at the end of the list where it is no
    # longer in scope.
    assert output.variables == (cast_document.VALUE,)
    assert output.template is False


def test_the_entity_a_detached_cast_publishes_is_named_where_every_output_is() -> None:
    detached = _detach()
    assert module_host.output_entity_id("hall_light_level", detached.pick[1]) == (
        "sensor.open_house_hall_light_level_min_lux"
    )


# --------------------------------------------------------------------------
# A condition's answer is published in whichever arm ran
# --------------------------------------------------------------------------


def test_a_condition_s_answer_is_published_by_the_arm_that_worked_it_out() -> None:
    """**The claim the two-armed shape rests on**, and it is not obvious.

    The value is named in *both* arms of the `if`, so there is no one list it is
    in scope in -- and the end of the automation, which is where a reading's
    publisher goes, is past the point where either arm has closed. Home
    Assistant's own rule is that a `variables:` is in scope for the rest of the
    list it was set in, so the publisher has to be appended to each arm, and the
    correct place for it is decided by `_scope_lists` rather than by this module.

    So the assertion is about *placement*: one publisher in `then`, one in
    `else`, and none at the top level. A document that failed this would be an
    automation that installs, runs, and publishes a template error for a variable
    that is not defined any more -- which is the silent kind of broken, and the
    reason detaching a condition is not simply "wrap the condition in an if".
    """
    detached = _detach(
        cast="condition",
        template="",
        condition={
            "condition": "state",
            "entity_id": "binary_sensor.away",
            "state": "on",
        },
        trigger=(),
    )
    source = module_host.read_module_source(
        yaml.safe_dump(dict(detached.document), sort_keys=False)
    )
    outputs = module_host.declare_outputs(source, {}, [detached.pick])
    assert [output.key for output in outputs] == ["min_lux"]
    published = module_host.publish_actions(
        dict(detached.document), outputs, module="hall_light_level"
    )
    branch = published["action"][0]
    assert _publishers(branch["then"]) == ["min_lux"]
    assert _publishers(branch["else"]) == ["min_lux"]
    assert _publishers(published["action"]) == []
    # And what each arm publishes is the arm's own answer, so a run through
    # either one writes a value rather than a name it cannot resolve.
    assert branch["then"] == [
        {"variables": {cast_document.VALUE: "on"}},
        *branch["then"][1:],
    ]
    assert _published(branch["then"]) == "{{ oh_value }}"
    assert _published(branch["else"]) == "{{ oh_value }}"


def _publishers(actions: list[object]) -> list[str]:
    """The output keys one action list publishes, in order."""
    found: list[str] = []
    for step in actions:
        if not isinstance(step, dict):
            continue
        if step.get("service") != module_host.PUBLISH_SERVICE:
            continue
        data = step.get("data")
        if isinstance(data, dict):
            found.append(str(data.get("key")))
    return found


def _published(actions: list[object]) -> str:
    """The value the one publisher in a list writes."""
    for step in actions:
        if (
            isinstance(step, dict)
            and step.get("service") == module_host.PUBLISH_SERVICE
        ):
            data = step.get("data")
            if isinstance(data, dict):
                return str(data.get("value"))
    raise AssertionError("no publisher in this list")


# --------------------------------------------------------------------------
# What starts it: derived where the cast knows, asked where it does not
# --------------------------------------------------------------------------


def test_a_condition_watches_the_entities_it_decides_about() -> None:
    """A condition says what it decides about, so nothing has to be asked.

    The trigger is derived rather than requested, which is what makes detaching a
    condition a single click: the person already wrote down the entities, in the
    condition editor, as the condition's own `entity_id`s.
    """
    detached = _detach(
        cast="condition",
        template="",
        condition={
            "condition": "state",
            "entity_id": "binary_sensor.away",
            "state": "on",
        },
        trigger=(),
    )
    assert detached.watched == ("binary_sensor.away",)
    assert detached.document["trigger"] == [
        {"trigger": "state", "entity_id": ["binary_sensor.away"]}
    ]
    # The person's own condition, unchanged, deciding the value.
    branch = detached.document["action"][0]
    assert branch["if"] == {
        "condition": "state",
        "entity_id": "binary_sensor.away",
        "state": "on",
    }
    assert branch["then"] == [{"variables": {cast_document.VALUE: "on"}}]
    assert branch["else"] == [{"variables": {cast_document.VALUE: "off"}}]


def test_a_condition_nested_over_several_entities_watches_each_of_them() -> None:
    """`watched_by` walks, because a condition builder nests.

    An `and` of two `or`s is an ordinary condition a person writes, and only
    walking finds the ids inside it -- a test that only read the top level would
    pass on a flat condition and produce a module that never fires on a real one.
    """
    condition = {
        "condition": "and",
        "conditions": [
            {
                "condition": "or",
                "conditions": [
                    {
                        "condition": "state",
                        "entity_id": "binary_sensor.a",
                        "state": "on",
                    },
                    {
                        "condition": "state",
                        "entity_id": "binary_sensor.b",
                        "state": "on",
                    },
                ],
            },
            {"condition": "state", "entity_id": ["binary_sensor.c"], "state": "off"},
            # Named twice across the tree, and watched once.
            {"condition": "state", "entity_id": "binary_sensor.a", "state": "off"},
        ],
    }
    detached = _detach(cast="condition", template="", condition=condition, trigger=())
    assert detached.watched == (
        "binary_sensor.a",
        "binary_sensor.b",
        "binary_sensor.c",
    )


def test_a_condition_that_names_no_entity_asks_what_should_start_it() -> None:
    """A `time` condition decides something real and watches nothing.

    This is the honest failure: the cast cannot say what should run it, so the
    person is asked rather than handed a module with a trigger that never fires.
    """
    with pytest.raises(AuthoringError) as refused:
        _detach(
            cast="condition",
            template="",
            condition={"condition": "time", "after": "22:00:00"},
            trigger=(),
        )
    assert "start it" in str(refused.value)
    # And the answer, when given, is what the module watches.
    detached = _detach(
        cast="condition",
        template="",
        condition={"condition": "time", "after": "22:00:00"},
        trigger=["sensor.dusk"],
    )
    assert detached.watched == ("sensor.dusk",)


def test_the_person_s_trigger_is_added_to_the_cast_s_own_rather_than_replacing_it() -> (
    None
):
    """A row watching three things can be given a fourth without losing the three."""
    detached = _detach(
        cast="condition",
        template="",
        condition={
            "condition": "state",
            "entity_id": "binary_sensor.away",
            "state": "on",
        },
        trigger=["sensor.lux"],
    )
    assert detached.watched == ("binary_sensor.away", "sensor.lux")
    assert detached.document["trigger"][0]["entity_id"] == [
        "binary_sensor.away",
        "sensor.lux",
    ]


def test_a_flow_watches_the_entity_it_already_writes() -> None:
    """A flow runs on its own, so it is *followed* rather than started.

    That is the whole difference between a flow and the other three, and it is why
    detaching one asks the person nothing: the entity the flow writes is exactly
    what changes when the flow has something to say.
    """
    flow = cast_document.flow_entity_for("hall_light", "min_lux")
    assert flow == "sensor.open_house_flow_hall_light_min_lux"
    detached = _detach(cast="flow", template="", flow_entity=flow, trigger=())
    assert detached.watched == (flow,)
    assert detached.document["trigger"][0]["entity_id"] == [flow]
    assert detached.document["action"] == [
        {"variables": {cast_document.VALUE: f"{{{{ states('{flow}') }}}}"}}
    ]


def test_a_flow_with_no_entity_yet_is_refused_rather_than_guessed() -> None:
    with pytest.raises(AuthoringError) as refused:
        _detach(cast="flow", template="", trigger=["sensor.lux"])
    assert "no entity yet" in str(refused.value)


def test_a_template_and_a_script_are_asked_what_should_start_them() -> None:
    """Neither names an entity on its own, and both say so rather than guessing."""
    for kwargs in (
        {"cast": "template", "template": "{{ states('sensor.lux') }}"},
        {"cast": "script", "template": "", "script": "hall_lux"},
    ):
        with pytest.raises(AuthoringError) as refused:
            _detach(trigger=(), **kwargs)
        assert "start it" in str(refused.value)


# --------------------------------------------------------------------------
# The logic travels, and each kind travels in its own shape
# --------------------------------------------------------------------------


def test_a_template_cast_carries_its_own_text_verbatim() -> None:
    """Not wrapped, not re-escaped: the text a person wrote, in the document."""
    text = "{{ states('sensor.lux') | int }}"
    detached = _detach(template=text)
    assert detached.document["action"] == [{"variables": {cast_document.VALUE: text}}]


def test_a_script_cast_calls_the_script_and_names_the_answer() -> None:
    """Two steps, because two things happen: the call, then the naming.

    The name is the *input's*, spelled by `module_host.script_variable`'s rule, so
    a person reading the new module reads the same variable the row's own cast
    would have bound -- which is what makes a detach a move rather than a rewrite.
    """
    detached = _detach(cast="script", template="", script="script.hall_lux")
    assert detached.document["action"] == [
        {"action": "script.hall_lux", "response_variable": cast_document.RESPONSE},
        {"variables": {cast_document.VALUE: f"{{{{ {cast_document.RESPONSE} }}}}"}},
    ]


def test_a_script_cast_takes_the_id_with_or_without_its_domain() -> None:
    for spelling in ("hall_lux", "script.hall_lux"):
        detached = _detach(cast="script", template="", script=spelling)
        assert detached.document["action"][0]["action"] == "script.hall_lux"


# --------------------------------------------------------------------------
# What is not a cast, and what is an empty one
# --------------------------------------------------------------------------


def test_a_row_that_holds_a_value_has_nothing_to_detach() -> None:
    with pytest.raises(AuthoringError) as refused:
        _detach(cast="value")
    assert "not a cast" in str(refused.value)


def test_an_empty_cast_is_refused_rather_than_detached_into_nothing() -> None:
    for kwargs, expected in (
        ({"cast": "condition", "template": "", "condition": {}}, "cast is empty"),
        ({"cast": "template", "template": "   "}, "cast is empty"),
        ({"cast": "script", "template": "", "script": "script."}, "names no script"),
    ):
        with pytest.raises(AuthoringError) as refused:
            _detach(trigger=["sensor.lux"], **kwargs)
        assert expected in str(refused.value)


# --------------------------------------------------------------------------
# The output key, which is what the source row will point at
# --------------------------------------------------------------------------


def test_the_key_takes_the_shape_every_name_in_the_tree_takes() -> None:
    """An input name is free to be something a key cannot be.

    `min_lux_%` is a perfectly good input name and not an entity-key name, and the
    alternative to fixing it here is refusing a detach over a character -- which
    would be refusing the person's own naming of a value they already have.
    """
    assert cast_document.key_for("min_lux") == "min_lux"
    assert cast_document.key_for("Min Lux") == "min_lux"
    assert cast_document.key_for("min_lux_%") == "min_lux"
    assert cast_document.key_for("2_not_a_key") == "detached_2_not_a_key"
    assert cast_document.key_for("!!") == "value"
    # Every one of them is a key `output_entity_id` accepts, which is the claim.
    for name in ("min_lux", "Min Lux", "min_lux_%", "2_not_a_key", "!!"):
        module_host.output_entity_id("module", cast_document.key_for(name))
