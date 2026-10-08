"""Hosting a blueprint as a Home Assistant automation, and the dataflow around it.

`ha_adapter.module_host` is the half of "import a blueprint" that is pure -- a
document in, a document out -- and this is the module that holds it to its
contract with no Home Assistant in the room. The claim under test is that a
blueprint is *hosted* rather than translated: its branches, templates, waits and
device actions arrive in the automation exactly as written, and the only thing
added is the make-public step for the outputs a person chose.

**The committed blueprint is the fixture, and it is the real one.** The MarqBarq
Dynamic Lighting blueprint under `docker/ha-config/blueprints` is the document
the Dev tab was built against, and it is the hardest case in the corpus: branches,
templates, per-branch variables and a `stop`. A test against a hand-made stand-in
would pass while the real screen did not.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
import yaml

from ha_adapter import module_host
from ha_adapter.pack_authoring import AuthoringError
from tools.catalog import paths

ROOT = Path(__file__).resolve().parents[1]

#: The blueprints other people wrote that this repository keeps for exactly this:
#: the hardest evidence that "any blueprint" is a claim and not a hope.
CORPUS = paths.RESSOURCES

DYNAMIC_LIGHTING = (
    ROOT
    / "docker"
    / "ha-config"
    / "blueprints"
    / "automation"
    / "MarqBarq"
    / "dynamic-lighting.yaml"
)

#: The inputs the MarqBarq blueprint declares with no `default`, and a value each.
#: A blueprint cannot be instantiated without one, which is the refusal
#: `test_a_required_input_with_no_value_is_refused` holds.
REQUIRED_INPUTS: dict[str, Any] = {
    "lux_sensor": "sensor.lux_test",
    "lights": {"entity_id": "light.test"},
}


def _source() -> module_host.HostedSource:
    return module_host.read_module_source(DYNAMIC_LIGHTING.read_text(encoding="utf-8"))


def _inputs(source: module_host.HostedSource) -> dict[str, Any]:
    """A value for every input no default of its own can answer.

    Asked of the server's own rule rather than of a copy of it: an input that
    takes a device has no default whatever the document declares, so
    `bypass_light` -- `default:` with nothing after it, on an `entity` selector
    -- is one of the inputs an import has to answer. A fixture written as
    `"default" not in block` would be asserting a rule the server no longer
    follows, and the automation it built would be one nobody could build.
    """
    chosen = dict(REQUIRED_INPUTS)
    for name, block in source.inputs.items():
        if not module_host.declares_default(block):
            chosen.setdefault(name, "x")
    return chosen


def _automation() -> dict[str, Any]:
    source = _source()
    return module_host.instantiate(source, _inputs(source), alias="Dynamic Lighting")


def _walk(node: object) -> list[Any]:
    found: list[Any] = []
    if isinstance(node, dict):
        found.append(node)
        for value in node.values():
            found.extend(_walk(value))
    elif isinstance(node, list):
        for item in node:
            found.extend(_walk(item))
    return found


# --------------------------------------------------------------------------
# Reading
# --------------------------------------------------------------------------


def test_a_blueprint_is_read_as_a_blueprint() -> None:
    source = _source()
    assert source.title
    assert len(source.inputs) == 18
    assert "lux_sensor" in source.inputs


def test_a_blueprints_section_is_not_mistaken_for_an_input() -> None:
    """A section *contains* inputs, and only the ones inside it are fillable.

    Home Assistant's section syntax nests a group of inputs one level down under
    a mapping that carries `name`/`icon`/`collapsed` beside its own `input:`. A
    reader that stopped at the top level would list "What (optional)" as a thing
    to fill and would not find the declaration for `!input update_exclusions`,
    refusing a blueprint that is entirely fillable -- which is how the corpus
    refused `auto_update_scheduled.yaml` before this was flattened.
    """
    text = "\n".join(
        [
            "blueprint:",
            "  name: Sectioned",
            "  input:",
            "    when_section:",
            "      name: When",
            "      collapsed: true",
            "      input:",
            "        at_time:",
            "          selector:",
            "            time: {}",
            "        days:",
            "          default: []",
            "          selector:",
            "            select:",
            "              options: [mon, tue]",
            "trigger: []",
            # The marker is what tells the two readers apart: without the
            # section's declaration, this input has no default and is refused.
            "action:",
            "  - service: logbook.log",
            "    data:",
            "      message: !input days",
        ]
    )
    source = module_host.read_module_source(text)
    assert set(source.inputs) == {"at_time", "days"}
    automation = module_host.instantiate(source, {}, alias="Sectioned")
    assert automation["action"][0]["data"]["message"] == []


def test_the_description_is_one_line() -> None:
    source = _source()
    assert "\n" not in source.description


# --------------------------------------------------------------------------
# Instantiating
# --------------------------------------------------------------------------


def test_instantiate_drops_the_blueprint_block_and_names_the_alias() -> None:
    automation = _automation()
    assert "blueprint" not in automation
    assert automation["alias"] == "Dynamic Lighting"


def test_a_named_input_is_substituted_for_its_marker() -> None:
    automation = _automation()
    assert not [
        node for node in _walk(automation) if set(node) == {module_host.INPUT_MARKER}
    ]
    # The blueprint names the bound entity into a variable, so the substitution
    # is visible as that variable's value rather than as a bare `entity_id`.
    assert any(
        node.get("lux_sensor_entity") == "sensor.lux_test" for node in _walk(automation)
    )


def test_a_default_is_used_when_no_value_is_named() -> None:
    source = _source()
    # `day_color_temp` carries `default: 5500`; name every input but that one and
    # the default must be what lands in the automation.
    chosen = _inputs(source)
    chosen.pop("day_color_temp", None)
    automation = module_host.instantiate(source, chosen)
    assert not [
        node for node in _walk(automation) if set(node) == {module_host.INPUT_MARKER}
    ]
    assert 5500 in [value for node in _walk(automation) for value in node.values()]


def test_a_required_input_with_no_value_is_refused() -> None:
    source = _source()
    with pytest.raises(AuthoringError, match="has no value and no default"):
        module_host.instantiate(source, {}, alias="Dynamic Lighting")


def test_nothing_is_translated() -> None:
    """Every branch and every template survives, which is the whole point.

    The claim is not that the document is byte-identical -- substituting an input
    necessarily replaces a marker with a value -- but that the *constructs* are
    all still there. A lossy walk would strip `choose` and the templates and still
    produce a document that loads, so counting them is what tells the two apart.
    """
    source = _source()
    automation = _automation()
    body = {key: value for key, value in source.document.items() if key != "blueprint"}
    assert _branches(body) == _branches(automation)
    assert _templates(body) == _templates(automation)
    assert _branches(automation) > 0 and _templates(automation)


def _branches(node: object) -> int:
    """How many branching constructs a tree carries."""
    count = 0
    if isinstance(node, dict):
        count += sum(1 for key in node if key in ("choose", "if", "repeat", "parallel"))
        for value in node.values():
            count += _branches(value)
    elif isinstance(node, list):
        for item in node:
            count += _branches(item)
    return count


def _templates(node: object) -> set[str]:
    """Every string in a tree that carries a Jinja template."""
    found: set[str] = set()
    if isinstance(node, dict):
        for value in node.values():
            found |= _templates(value)
    elif isinstance(node, list):
        for item in node:
            found |= _templates(item)
    elif isinstance(node, str) and ("{{" in node or "{%" in node):
        found.add(node)
    return found


# --------------------------------------------------------------------------
# What may be published
# --------------------------------------------------------------------------


def test_the_blueprints_variables_are_candidates() -> None:
    names = {c.name for c in module_host.output_candidates(_source())}
    assert "min_lux_clear_value" in names


def test_an_entity_input_is_a_candidate_only_once_it_is_bound() -> None:
    source = _source()
    unbound = module_host.output_candidates(source)
    assert not [c for c in unbound if c.kind == "entity"]
    bound = module_host.output_candidates(source, REQUIRED_INPUTS)
    readings = [c for c in bound if c.kind == "entity"]
    assert readings and readings[0].expression == "states('sensor.lux_test')"


def test_a_branch_variable_is_marked_as_such() -> None:
    """A variable set inside a branch is flagged, one set at the top is not.

    The MarqBarq blueprint sets every variable it has in top-level `- variables:`
    actions, so it cannot exercise this on its own and a synthetic document does.
    """
    text = "\n".join(
        [
            "blueprint:",
            "  name: Branchy",
            "  input: {}",
            "trigger: []",
            "action:",
            "  - choose:",
            "      - conditions: []",
            "        sequence:",
            "          - variables:",
            "              decided: 'on'",
            "  - variables:",
            "      top: 1",
        ]
    )
    source = module_host.read_module_source(text)
    by_name = {c.name: c for c in module_host.output_candidates(source)}
    assert by_name["decided"].branch_only is True
    assert by_name["top"].branch_only is False


def test_a_picked_candidate_becomes_the_output_they_named() -> None:
    source = _source()
    chosen = _inputs(source)
    outputs = module_host.declare_outputs(
        source, chosen, [("min_lux_clear_value", "floor")]
    )
    assert outputs == (
        module_host.Output(
            key="floor",
            expression="min_lux_clear_value",
            kind="number",
            variables=("min_lux_clear_value",),
        ),
    )


def test_a_reading_carries_no_variable_names() -> None:
    """An entity reading is a value, so its publisher may go anywhere.

    Naming a variable it does not use would send the publisher looking for a
    scope that does not exist, and it would land at the end of the automation by
    accident rather than by rule.
    """
    source = _source()
    chosen = _inputs(source)
    outputs = module_host.declare_outputs(source, chosen, [("input:lux_sensor", "lux")])
    assert outputs[0].expression == "states('sensor.lux_test')"
    assert outputs[0].variables == ()


def test_a_reading_is_offered_before_the_room_has_bound_the_slot() -> None:
    """A module is defined before it has a room, so its readings have no device yet.

    The alternative -- hiding the reading until the slot is bound -- would mean a
    person defining a module could not publish the one value it is most obviously
    about: the sensor its room is going to hand it.
    """
    source = _source()
    bindings = {
        "lux_sensor": module_host.InputBinding(kind="slot", slot="ambient_light_sensor")
    }
    chosen = module_host.bind_inputs(source, module_host.resolve_slots(bindings, {}))
    readings = [
        candidate
        for candidate in module_host.output_candidates(source, chosen, bindings)
        if candidate.kind == "entity"
    ]
    assert [candidate.name for candidate in readings] == ["input:lux_sensor"]
    assert readings[0].expression == ""


def test_a_reading_answered_with_a_slot_reads_whatever_the_room_binds() -> None:
    """The same pick, one room later, is an expression over that room's device.

    This is the whole of what a definition buys: the answer is a role, so the same
    ticked box publishes the kitchen's lux in the kitchen and the study's in the
    study, and the expression is written when the room has answered.
    """
    source = _source()
    bindings = {
        "lux_sensor": module_host.InputBinding(kind="slot", slot="ambient_light_sensor")
    }
    bound = {"ambient_light_sensor": "sensor.kitchen_lux"}
    chosen = module_host.bind_inputs(source, module_host.resolve_slots(bindings, bound))
    outputs = module_host.declare_outputs(
        source, chosen, [("input:lux_sensor", "lux")], bindings
    )
    assert outputs[0].expression == "states('sensor.kitchen_lux')"
    assert outputs[0].variables == ()


def test_a_reading_answered_with_a_part_reads_the_part_not_the_slot() -> None:
    """**The claim a shared part rests on.**

    A person split `ambient_light_sensor` into `a` and `b` and put this module on
    `a`. If the binding resolved against the parent's key, two modules on two
    different parts would act on the *same* device while the screen said
    otherwise -- and nothing would look broken, because both would be reading a
    real sensor. So the part's key is the one looked up, and the parent's binding
    is present in the same map to prove the lookup is not merely falling back.
    """
    source = _source()
    bindings = {
        "lux_sensor": module_host.InputBinding(
            kind="slot", slot="ambient_light_sensor", part="a"
        )
    }
    bound = {
        "ambient_light_sensor": "sensor.kitchen_lux",
        "ambient_light_sensor__a": "sensor.study_lux",
    }
    chosen = module_host.bind_inputs(source, module_host.resolve_slots(bindings, bound))
    outputs = module_host.declare_outputs(
        source, chosen, [("input:lux_sensor", "lux")], bindings
    )
    assert outputs[0].expression == "states('sensor.study_lux')"


def test_a_part_nothing_has_bound_leaves_the_reading_waiting() -> None:
    """A split role is bound half by half, so half a split is half answered.

    The parent being bound is not the part being bound: a person who split the
    role and filled only `a` has said something true about a house that is
    half-built, and a module on `b` waits rather than silently reading `a`'s
    device or the parent's.
    """
    bindings = {
        "lux_sensor": module_host.InputBinding(
            kind="slot", slot="ambient_light_sensor", part="b"
        )
    }
    bound = {
        "ambient_light_sensor": "sensor.kitchen_lux",
        "ambient_light_sensor__a": "sensor.study_lux",
    }
    assert module_host.resolve_slots(bindings, bound) == {}
    assert module_host.unresolved_slots(bindings, bound) == ("ambient_light_sensor__b",)


def test_a_reading_is_kept_while_the_room_still_owes_the_slot() -> None:
    """The output exists and publishes nothing, which is what waiting means.

    A refusal here would be a module that could not be defined, installed or
    listed until every room it might live in had answered -- and the module has no
    room at all yet.
    """
    source = _source()
    bindings = {
        "lux_sensor": module_host.InputBinding(kind="slot", slot="ambient_light_sensor")
    }
    chosen = module_host.bind_inputs(source, module_host.resolve_slots(bindings, {}))
    outputs = module_host.declare_outputs(
        source, chosen, [("input:lux_sensor", "lux")], bindings
    )
    assert outputs[0].key == "lux"
    assert outputs[0].expression == ""


def test_a_cast_row_is_offered_as_something_to_publish() -> None:
    """A row answered with logic is a value like any other, once a person says so.

    This is the whole of "expose it": the answer the row holds -- the binary
    sensor Open House made for the condition -- is published under a name of the
    person's choosing, exactly as an entity input's reading is.
    """
    source = _source()
    bindings = {
        "lux_sensor": module_host.InputBinding(
            kind="entity", value="binary_sensor.open_house_lights_lux_sensor"
        )
    }
    casts = module_host.cast_answers(bindings, conditions=("lux_sensor",))
    assert casts == {"lux_sensor": "condition"}
    offered = {
        candidate.name: candidate
        for candidate in module_host.output_candidates(source, {}, bindings, casts)
    }
    cast = offered["cast:lux_sensor"]
    assert cast.kind == "cast"
    assert cast.expression == "states('binary_sensor.open_house_lights_lux_sensor')"
    assert cast.template is False


def test_a_template_answer_is_a_cast_row_with_nothing_to_record_it() -> None:
    """The fourth cast is the answer itself, so the binding is the whole of it.

    A condition, a flow and a script are remembered on the record because the
    build has to know what to make of them; a template is a value the input is
    *built with*, so nothing remembers it and nothing needs to. Which is why this
    is asked of the answers rather than of a list of cast rows.
    """
    source = _source()
    bindings = {
        "lux_sensor": module_host.InputBinding(
            kind="literal", value="{{ states('sensor.kitchen_lux') }}"
        )
    }
    casts = module_host.cast_answers(bindings)
    assert casts == {"lux_sensor": "template"}
    outputs = module_host.declare_outputs(
        source,
        module_host.bind_inputs(source, bindings),
        [("cast:lux_sensor", "kitchen_lux")],
        bindings,
        casts,
    )
    assert outputs[0].expression == "{{ states('sensor.kitchen_lux') }}"
    assert outputs[0].template is True
    # Nothing to place it by: a template is rendered where the publisher lands,
    # which is the end of the action list.
    assert outputs[0].variables == ()


def test_the_cast_a_row_holds_decides_the_kind_it_publishes() -> None:
    """A condition publishes a yes-or-no whatever row it was written over.

    Every cast is offered on every row, so a condition may end up on a number
    input -- and what the row then holds is the state of a binary sensor, which
    is `on` or `off`. Typing it from the input's own selector would put `on`
    behind a number sensor.
    """
    text = "\n".join(
        [
            "blueprint:",
            "  name: Castable",
            "  input:",
            "    brightness:",
            "      selector:",
            "        number:",
            "          min: 0",
            "          max: 100",
            "trigger: []",
            "action:",
            "  - delay: '{{ brightness }}'",
        ]
    )
    source = module_host.read_module_source(text)
    bindings = {
        "brightness": module_host.InputBinding(
            kind="entity", value="binary_sensor.open_house_dim_brightness"
        )
    }
    casts = module_host.cast_answers(bindings, conditions=("brightness",))
    offered = {
        c.name: c for c in module_host.output_candidates(source, {}, bindings, casts)
    }
    assert offered["cast:brightness"].value_kind == "boolean"


def test_a_cast_row_nothing_has_answered_yet_is_still_offered() -> None:
    """The screen offers what the module *will* publish, not only what it can read.

    On a fresh import there is no module, so there is no entity a condition made
    and no variable a script fills -- and the candidate is offered anyway for the
    same reason an unbound slot's reading is: a candidate list that changed shape
    between importing a blueprint and installing it would offer less than the
    module can do. The expression is written when the module is built.
    """
    source = _source()
    casts = module_host.cast_answers({}, flows=("lux_sensor",))
    offered = {c.name: c for c in module_host.output_candidates(source, {}, {}, casts)}
    assert offered["cast:lux_sensor"].expression == ""


def test_a_cast_row_bound_to_several_devices_has_nothing_to_read() -> None:
    """A target several lights hung off is answered, and publishes no one value.

    The row is a flow cast, so it is a row that *can* publish -- but what it
    holds is a list, and there is no one state a reading could be. Picking the
    first would be inventing a value rather than reporting one.
    """
    source = _source()
    bindings = {
        "lights": module_host.InputBinding(
            kind="entity", value=["light.kitchen", "light.hall"]
        )
    }
    casts = module_host.cast_answers(bindings, flows=("lights",))
    names = {c.name for c in module_host.output_candidates(source, {}, bindings, casts)}
    assert "cast:lights" not in names


def test_publishing_a_cast_row_nothing_answers_is_refused() -> None:
    """`cast:x` is only a candidate because a cast answers `x`, and the two agree.

    A screen that offered the row and a build that had stopped recognising it
    would be a tick that reached a refusal at the far end of a settled screen.
    """
    source = _source()
    with pytest.raises(AuthoringError, match="carries nothing called"):
        module_host.declare_outputs(
            source, _inputs(source), [("cast:lux_sensor", "lux")], {}, {}
        )


def test_publishing_something_the_source_does_not_carry_is_refused() -> None:

    source = _source()
    with pytest.raises(AuthoringError, match="carries nothing called"):
        module_host.declare_outputs(source, _inputs(source), [("invented", "x")])


def test_two_outputs_may_not_share_a_name() -> None:
    """They would be one entity, and the second would overwrite the first."""
    source = _source()
    chosen = _inputs(source)
    with pytest.raises(AuthoringError, match="both called"):
        module_host.declare_outputs(
            source,
            chosen,
            [("min_lux_clear_value", "same"), ("max_lux_clear_value", "same")],
        )


# --------------------------------------------------------------------------
# The make-public step
# --------------------------------------------------------------------------


def test_publishing_appends_and_never_replaces() -> None:
    automation = _automation()
    before = len(automation["action"])
    output = module_host.Output(
        key="lux",
        expression="min_lux_clear_value",
        kind="number",
        variables=("min_lux_clear_value",),
    )
    published = module_host.publish_actions(
        automation, [output], module="dynamic_lighting"
    )
    assert len(published["action"]) >= before
    assert _published(published) == 1


def test_publishing_does_not_mutate_the_document_it_read() -> None:
    automation = _automation()
    snapshot = yaml.safe_dump(automation)
    output = module_host.Output(
        key="lux",
        expression="min_lux_clear_value",
        kind="number",
        variables=("min_lux_clear_value",),
    )
    module_host.publish_actions(automation, [output], module="dynamic_lighting")
    assert yaml.safe_dump(automation) == snapshot


def test_a_variable_is_published_where_it_is_in_scope() -> None:
    """A branch variable's publisher goes inside the branch that defines it.

    Publishing it at the end of the automation would be a template naming a
    variable that closed with the branch, which Home Assistant resolves to
    nothing -- a silent empty output rather than a failure.
    """
    document = {
        "alias": "branchy",
        "action": [
            {
                "choose": [
                    {
                        "conditions": [
                            {
                                "condition": "state",
                                "entity_id": "light.a",
                                "state": "on",
                            }
                        ],
                        "sequence": [{"variables": {"decided": "on"}}],
                    }
                ]
            }
        ],
    }
    output = module_host.Output(
        key="decided", expression="decided", kind="string", variables=("decided",)
    )
    published = module_host.publish_actions(document, [output], module="branch_module")
    branch = published["action"][0]["choose"][0]["sequence"]
    assert _published(branch) == 1
    # And not as a direct child of the top level, where the branch's variable is
    # out of scope.
    assert not [
        step
        for step in published["action"]
        if isinstance(step, dict) and step.get("service") == module_host.PUBLISH_SERVICE
    ]


def test_a_variable_set_in_an_if_arm_is_published_in_that_arm() -> None:
    """The same rule, for the `if` spelling of a branch.

    An `if` element keeps its two arms under `then` and `else`, and a `choose`
    element keeps its fallback under `default`. They are action lists like
    `sequence:` is, and a name set inside one is out of scope everywhere else --
    so a publisher that does not know about them lands at the top level, names a
    variable that is not there, and the output is never written. That is silent
    from outside: the module runs, and the output stays unknown.
    """
    document = {
        "alias": "branchy",
        "action": [
            {
                "if": [{"condition": "state", "entity_id": "light.a", "state": "on"}],
                "then": [{"variables": {"wanted": 40}}],
                "else": [{"variables": {"wanted": 10}}],
            }
        ],
    }
    output = module_host.Output(
        key="wanted", expression="wanted", kind="number", variables=("wanted",)
    )
    published = module_host.publish_actions(document, [output], module="branch_module")
    element = published["action"][0]
    assert _published(element["then"]) == 1
    assert _published(element["else"]) == 1
    assert not [
        step
        for step in published["action"]
        if isinstance(step, dict) and step.get("service") == module_host.PUBLISH_SERVICE
    ]


def test_a_variable_set_in_a_choose_fallback_is_published_there() -> None:
    """`default:` is the third name for a branch body."""
    document = {
        "alias": "branchy",
        "action": [
            {
                "choose": [
                    {
                        "conditions": [
                            {
                                "condition": "state",
                                "entity_id": "light.a",
                                "state": "on",
                            }
                        ],
                        "sequence": [{"variables": {"picked": 1}}],
                    }
                ],
                "default": [{"variables": {"picked": 2}}],
            }
        ],
    }
    output = module_host.Output(
        key="picked", expression="picked", kind="number", variables=("picked",)
    )
    published = module_host.publish_actions(document, [output], module="branch_module")
    element = published["action"][0]
    assert _published(element["default"]) == 1


def test_a_variable_set_in_a_parallel_branch_is_published_in_its_branch() -> None:
    """A `parallel` element's branches are a mapping of action lists, not one list."""
    document = {
        "alias": "branchy",
        "action": [
            {"parallel": {"one": [{"variables": {"fanned": 1}}]}},
        ],
    }
    output = module_host.Output(
        key="fanned", expression="fanned", kind="number", variables=("fanned",)
    )
    published = module_host.publish_actions(document, [output], module="branch_module")
    assert _published(published["action"][0]["parallel"]["one"]) == 1


# --------------------------------------------------------------------------
# The corpus, which is what "any blueprint" has to mean
# --------------------------------------------------------------------------


def test_every_variable_a_real_blueprint_carries_is_published_in_scope() -> None:
    """Every real blueprint's every value can be published and be *in scope*.

    This is the corpus check for the claim the whole feature rests on. A
    blueprint's variables are set all over its action tree -- inside `choose`
    branches, inside the `then` and `else` arms of an `if`, inside a `default`,
    inside a `parallel` element's branches -- and a variable is in scope only
    from where it is set to the end of that list. A publisher appended anywhere
    else names a variable that is not there: the module still runs, the output
    is simply never written, and from outside that is indistinguishable from a
    module that has nothing to say.

    Checked by building each one rather than by reading the walk, because the
    walk and the placement are the two halves that have to agree. A variable is
    in scope if the list the publisher landed in is one that defines it, or if
    the document declares it for the whole automation -- `variables:` and
    `trigger_variables:` at the top level, which Home Assistant scopes to
    everything.

    Skipped where there is no corpus: CI runs from a checkout without one.
    """
    if not CORPUS.is_dir():
        pytest.skip("no blueprint corpus in this checkout")
    misplaced: list[str] = []
    seen = 0
    for path in sorted(CORPUS.rglob("*.yaml")):
        if "blueprint" not in str(path):
            continue
        try:
            source = module_host.read_module_source(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not source.inputs or module_host.action_key(source.document) is None:
            continue
        declared = {
            name
            for key in ("variables", "trigger_variables")
            if isinstance(source.document.get(key), Mapping)
            for name in source.document[key]
        }
        for candidate in module_host.output_candidates(source, {}):
            if candidate.kind != "variable":
                continue
            seen += 1
            output = module_host.Output(
                key="probe",
                expression=candidate.expression,
                kind="string",
                variables=(candidate.name,),
            )
            built = module_host.publish_actions(
                source.document, [output], module="probe"
            )
            in_a_defining_list = any(
                candidate.name in module_host._defined_in(sequence)
                for sequence in module_host._all_sequences(built)
            )
            at_the_end = any(
                isinstance(step, dict)
                and step.get("service") == module_host.PUBLISH_SERVICE
                for step in (built.get("action") or built.get("actions") or [])
            )
            if not (in_a_defining_list or (at_the_end and candidate.name in declared)):
                misplaced.append(f"{path.name}: {candidate.name}")
    assert seen > 500, f"the corpus walk found only {seen} variables"
    assert not misplaced, (
        f"{len(misplaced)} of {seen} variables would be published where they are "
        f"not in scope: {misplaced[:8]}"
    )


def test_a_document_with_no_actions_is_not_an_automation() -> None:
    """A script blueprint's document is a bare `sequence:`.

    Refused by the caller in one sentence about what the person chose, rather
    than handed to Home Assistant to be refused as a document missing a key.
    """
    script = module_host.read_module_source(
        "blueprint:\n  name: A script\nsequence: []\n"
    )
    assert module_host.action_key(script.document) is None
    assert module_host.action_key({"action": []}) == "action"
    assert module_host.action_key({"actions": []}) == "actions"


def test_a_target_input_bound_to_one_entity_is_offered_as_a_reading() -> None:
    """A `target` binding is wrapped, and one entity in it is still one entity.

    `bind_inputs` writes a target as `{entity_id: …}`, so a candidate for it has
    to look inside that to find the entity a reading could be over. Not looking
    is why the entity a module *acts through* -- often the most interesting thing
    it knows -- could not be published at all.
    """
    source = _bindable()
    values = {"some_lights": {"entity_id": "light.kitchen"}}
    candidates = {
        candidate.name: candidate
        for candidate in module_host.output_candidates(source, values)
    }
    assert candidates["input:some_lights"].expression == "states('light.kitchen')"


def test_a_target_input_bound_to_many_entities_is_not_a_reading() -> None:
    """Several entities are not one state, and the first of them is not the answer."""
    source = _bindable()
    values = {"some_lights": {"entity_id": ["light.kitchen", "light.hall"]}}
    candidates = {
        candidate.name for candidate in module_host.output_candidates(source, values)
    }
    assert "input:some_lights" not in candidates


# --------------------------------------------------------------------------
# What the blueprint does, which is also something a person may read
# --------------------------------------------------------------------------

#: A blueprint whose whole body is two calls that drive something, and one that
#: drives nothing. Written here rather than taken from the corpus because the
#: corpus's real blueprint writes every value it sets as a template -- the literal
#: and the non-device call are the two shapes it cannot exercise.
DIMMER = """
blueprint:
  name: Dimmer
  input: {}
trigger: []
action:
  - service: light.turn_on
    target:
      entity_id: light.kitchen
    data:
      brightness_pct: 60
      transition: '{{ 2 | int }}'
  - action: logbook.log
    data:
      name: Dimmer
      message: done
  - service: light.turn_off
    target:
      entity_id: light.hall
"""


def _candidates(
    source: module_host.HostedSource, chosen: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    return {c.name: c for c in module_host.output_candidates(source, chosen)}


def test_the_values_a_call_sets_are_candidates() -> None:
    """A call is the blueprint doing something, and what it set is a reading.

    The requirement in its own words: any time the blueprint talks to a device or
    sets a value, that is something a person may want published. The MarqBarq
    blueprint's `light.turn_on` carries the brightness it drove the light to, and
    that value exists nowhere as a name.
    """
    candidates = _candidates(_source(), REQUIRED_INPUTS)
    brightness = candidates["service:light.turn_on:brightness"]
    assert brightness.kind == "service"
    assert brightness.value_kind == "string"
    # The expression is the call's own text, not a name: there is no variable
    # called `brightness` that is the brightness the light was given.
    assert brightness.expression == "{{ brightness | int }}"
    assert brightness.branch_only is True


def test_the_devices_a_call_acts_on_are_a_candidate() -> None:
    """`target: !input lights` resolved, so an output can name what it drove.

    The same resolution `instantiate` does, which is what makes the value honest:
    it is the device the automation really acts on rather than the marker.
    """
    candidates = _candidates(_source(), REQUIRED_INPUTS)
    devices = candidates["service:light.turn_on"]
    assert devices.expression == "light.test"
    # `action[4]` is the `choose` element and `default` is its fallback arm -- a
    # sibling of `choose`, which is what makes the call's branch flag a thing the
    # walk has to compute rather than inherit from `choose` alone.
    assert devices.after == ("action", 4, "default", 0)
    assert devices.branch_only is True


def test_a_calls_values_are_offered_before_any_input_is_answered() -> None:
    """The screen this list appears on is one nobody has filled in yet.

    Which device it drives is the `!input` they have not picked; *that it sets a
    brightness* is true of the document regardless, and hiding it until they pick
    a light would hide the value they came to publish.
    """
    candidates = _candidates(_source())
    assert "service:light.turn_on:brightness" in candidates
    assert "service:light.turn_on" not in candidates


def test_a_call_that_drives_nothing_is_not_a_candidate() -> None:
    """A logbook line and a notification are not things a module controls.

    Offered, they would put `message` and `name` in the list as though the module
    "set" them, and every dashboard offered them would be offered prose.
    """
    names = set(_candidates(module_host.read_module_source(DIMMER)))
    assert "service:logbook.log" not in names
    assert not [name for name in names if name.startswith("service:logbook")]


def test_a_literal_carries_its_number_and_a_template_its_braces() -> None:
    """Sixty is a number and `{{ 2 | int }}` is already a template.

    Two kinds of text in one call, and the publisher has to know which is which:
    `{{ 60 }}` renders to the number the call set, where `{{ {{ 2 | int }} }}`
    renders to nothing and logs an error on every run.
    """
    candidates = _candidates(module_host.read_module_source(DIMMER))
    number = candidates["service:light.turn_on:brightness_pct"]
    assert (number.expression, number.template, number.value_kind) == (
        "60",
        False,
        "number",
    )
    template = candidates["service:light.turn_on:transition"]
    assert (template.expression, template.template) == ("{{ 2 | int }}", True)


def test_two_calls_of_one_service_are_two_candidates() -> None:
    """Two branches lighting two different rooms are two things a person reads.

    The second is not a duplicate of the first: it is what the module did on the
    runs that took the other branch, and dropping it would make the choice
    invisible on exactly the runs the first output cannot describe.
    """
    text = "\n".join(
        [
            "blueprint:",
            "  name: Two Ways",
            "  input: {}",
            "trigger: []",
            "action:",
            "  - service: light.turn_on",
            "    target: {entity_id: light.kitchen}",
            "  - service: light.turn_on",
            "    target: {entity_id: light.hall}",
        ]
    )
    candidates = _candidates(module_host.read_module_source(text))
    assert candidates["service:light.turn_on"].expression == "light.kitchen"
    assert candidates["service:light.turn_on #2"].expression == "light.hall"


def test_a_value_a_call_set_is_published_beside_the_call() -> None:
    """Right after the action it reads, and inside the branch that runs it.

    Where a variable's publisher goes where the name is in scope, this one goes
    where the call *happened*: `brightness` is set again by every run, and a
    publisher at the end of the automation reports the value of whichever branch
    ran last rather than of the action the output is named after.
    """
    source = _source()
    chosen = _inputs(source)
    outputs = module_host.declare_outputs(
        source,
        chosen,
        [
            ("service:light.turn_on:brightness", "brightness"),
            ("service:light.turn_off:transition", "off_transition"),
        ],
    )
    published = module_host.publish_actions(
        module_host.instantiate(source, chosen), outputs, module="dynamic_lighting"
    )
    turn_on = published["action"][4]["default"]
    assert turn_on[0]["service"] == "light.turn_on"
    assert turn_on[1]["data"]["key"] == "brightness"
    turn_off = published["action"][4]["choose"][0]["sequence"]
    assert turn_off[1]["service"] == "light.turn_off"
    assert turn_off[2]["data"]["key"] == "off_transition"
    # And nothing was tacked onto the end of the automation for either of them.
    assert not [
        step
        for step in published["action"]
        if isinstance(step, dict) and step.get("service") == module_host.PUBLISH_SERVICE
    ]


def test_two_calls_in_one_list_keep_their_own_publishers() -> None:
    """Inserting after the first call does not move the second's place.

    The publisher for the earlier action is inserted *after* it, which shifts
    every index in that list -- so the later call is found before anything is
    inserted. Looking it up afterwards finds the publisher, and the second
    output is published beside the wrong action.
    """
    source = module_host.read_module_source(DIMMER)
    outputs = module_host.declare_outputs(
        source,
        {},
        [
            ("service:light.turn_on:brightness_pct", "brightness"),
            ("service:light.turn_off", "off_devices"),
        ],
    )
    published = module_host.publish_actions(
        module_host.instantiate(source, {}), outputs, module="dimmer"
    )

    def called(step: Mapping[str, Any]) -> str:
        # `service:` and `action:` are the same step under two spellings, and the
        # text between this document's `logbook.log` and a publish is the whole
        # subject of the test.
        return str(step.get("service") or step.get("action"))

    assert [called(step) for step in published["action"]] == [
        "light.turn_on",
        module_host.PUBLISH_SERVICE,
        "logbook.log",
        "light.turn_off",
        module_host.PUBLISH_SERVICE,
    ]
    assert published["action"][1]["data"]["value"] == "{{ 60 }}"
    assert published["action"][4]["data"]["value"] == "light.hall"


def test_the_published_step_calls_the_publish_service() -> None:
    automation = _automation()
    output = module_host.Output(
        key="lux",
        expression="min_lux_clear_value",
        kind="number",
        variables=("min_lux_clear_value",),
    )
    published = module_host.publish_actions(
        automation, [output], module="dynamic_lighting"
    )
    steps = [
        n for n in _walk(published) if n.get("service") == module_host.PUBLISH_SERVICE
    ]
    assert len(steps) == 1
    assert steps[0]["data"]["module"] == "dynamic_lighting"
    assert steps[0]["data"]["key"] == "lux"


# --------------------------------------------------------------------------
# The entity an output lives at
# --------------------------------------------------------------------------


def test_the_output_entity_id_has_one_spelling() -> None:
    assert module_host.output_entity_id("dynamic_lighting", "lux") == (
        "sensor.open_house_dynamic_lighting_lux"
    )


@pytest.mark.parametrize("bad", ["Bad", "9lives", "with space", ""])
def test_a_name_that_is_not_a_key_is_refused(bad: str) -> None:
    with pytest.raises(AuthoringError):
        module_host.output_entity_id(bad, "lux")


# --------------------------------------------------------------------------
# The automation configuration id a module is known by
# --------------------------------------------------------------------------


def test_the_config_id_is_derived_from_the_name_and_from_nothing_else() -> None:
    """The same module is the same automation, now and after a restart.

    This is the whole of importing a module twice being a replacement: the id
    is what Home Assistant matches the automation by, so a second import of the
    same name has to land on the first import's id. Anything that varied with
    time, with the process or with an input's value would leave the first
    automation running beside the second.
    """
    first = module_host.config_id_for("dynamic_lighting")
    assert first == module_host.config_id_for("dynamic_lighting")
    assert first != module_host.config_id_for("dynamic_lighting_bedroom")


def test_the_config_id_is_a_home_assistant_config_id() -> None:
    """A 32-character lowercase hex string: what Home Assistant writes itself.

    `automations.yaml` entries carry an `id` of exactly this shape, and a module
    automation is one of those entries, so an id of any other shape would be a
    module the editor sees as different in kind from the ones it made.
    """
    import re

    assert re.fullmatch(r"[0-9a-f]{32}", module_host.config_id_for("dynamic_lighting"))


def test_the_config_id_is_pinned() -> None:
    """Derived *and* fixed: the namespace is part of the contract.

    Module automations already in a house are matched by this id, so changing
    the namespace would orphan every hosted module on upgrade -- the automation
    would still run, and be a different automation from the one the record
    names. This asserts the value rather than the derivation for that reason.
    """
    assert module_host.config_id_for("dynamic_lighting") == (
        "16004dd7cbe85057a0fb3a216075cc79"
    )


def _published(node: object) -> int:
    """How many publish steps sit in a tree."""
    return sum(
        1 for item in _walk(node) if item.get("service") == module_host.PUBLISH_SERVICE
    )


# --------------------------------------------------------------------------
# Filling an input
# --------------------------------------------------------------------------

#: A blueprint with one input of each shape a binding has to answer for: an
#: entity, a target, a number and a select. Small and synthetic because the real
#: corpus has no single blueprint covering all four.
_BINDABLE = "\n".join(
    [
        "blueprint:",
        "  name: Bindable",
        "  input:",
        "    a_light:",
        "      selector:",
        "        entity:",
        "          domain: light",
        "    some_lights:",
        "      selector:",
        "        target:",
        "          entity:",
        "            domain: light",
        "    threshold:",
        "      selector:",
        "        number:",
        "          min: 0",
        "          max: 100",
        "trigger: []",
        "action: []",
    ]
)


def _bindable() -> module_host.HostedSource:
    return module_host.read_module_source(_BINDABLE)


def test_a_literal_binding_is_the_value_itself() -> None:
    values = module_host.bind_inputs(
        _bindable(), {"threshold": module_host.InputBinding(kind="literal", value=42)}
    )
    assert values["threshold"] == 42


def test_an_entity_binding_is_the_entity_id() -> None:
    values = module_host.bind_inputs(
        _bindable(),
        {"a_light": module_host.InputBinding(kind="entity", value="light.kitchen")},
    )
    assert values["a_light"] == "light.kitchen"


def test_a_target_binding_is_wrapped() -> None:
    values = module_host.bind_inputs(
        _bindable(),
        {"some_lights": module_host.InputBinding(kind="entity", value="light.hall")},
    )
    assert values["some_lights"] == {"entity_id": "light.hall"}


def test_an_output_bound_to_an_entity_input_is_the_output_entity() -> None:
    values = module_host.bind_inputs(
        _bindable(),
        {
            "a_light": module_host.InputBinding(
                kind="output", module="other", key="chosen"
            )
        },
    )
    assert values["a_light"] == "sensor.open_house_other_chosen"


def test_an_output_bound_to_a_value_input_is_a_template_reading_it() -> None:
    """The one way one automation consumes another's reading in Home Assistant.

    A `number` selector cannot hold an entity id, so the value that lands in the
    blueprint is a template over the output entity rather than the entity itself
    -- and the template *converts*, for the reason `_template_for` gives: an
    output's state is text, and text is not the value it spells.
    """
    values = module_host.bind_inputs(
        _bindable(),
        {
            "threshold": module_host.InputBinding(
                kind="output", module="lux", key="level"
            )
        },
    )
    assert (
        values["threshold"] == "{{ states('sensor.open_house_lux_level') | float(0) }}"
    )


def test_a_number_input_reads_the_output_as_a_number() -> None:
    """`| float(0)` and not the bare state, which is the difference between the
    blueprint computing with the producer's value and computing with a string."""
    assert _bound_template("number:") == (
        "{{ states('sensor.open_house_lux_level') | float(0) }}"
    )


def test_a_boolean_input_reads_the_output_as_a_boolean() -> None:
    """`is_state` and not the bare state: `"off"` is a non-empty string, and a
    non-empty string is true, so a blueprint branching on it would take the `on`
    branch while its entity says `off` -- a module that never turns anything off
    and logs nothing about it."""
    assert _bound_template("boolean:") == (
        "{{ is_state('sensor.open_house_lux_level', 'on') }}"
    )


def test_a_text_input_reads_the_output_as_its_state() -> None:
    """A text input is the state, which is the one case where the two agree."""
    assert _bound_template("text:") == "{{ states('sensor.open_house_lux_level') }}"


def _bound_template(selector: str) -> str:
    """What the output binding composes for an input with this selector."""
    document = [
        "blueprint:",
        "  name: Typed",
        "  input:",
        "    threshold:",
        "      selector:",
        f"        {selector}",
        "trigger: []",
        "action: []",
    ]
    values = module_host.bind_inputs(
        module_host.read_module_source("\n".join(document)),
        {
            "threshold": module_host.InputBinding(
                kind="output", module="lux", key="level"
            )
        },
    )
    return str(values["threshold"])


def test_an_unbound_input_is_left_out() -> None:
    assert module_host.bind_inputs(_bindable(), {}) == {}


def test_a_binding_that_is_not_a_way_to_fill_an_input_is_refused() -> None:
    with pytest.raises(AuthoringError, match="is not a way to fill an input"):
        module_host.bind_inputs(
            _bindable(), {"threshold": module_host.InputBinding(kind="telepathy")}
        )


# --------------------------------------------------------------------------
# Answering an input with a slot
# --------------------------------------------------------------------------


def test_a_bound_slot_is_the_device_the_room_names() -> None:
    """The whole of what a slot is: a name the room answers for, resolved late.

    The module asked for `ambient_light_sensor` and got `sensor.kitchen_lux` --
    and would have got whatever else the room binds for that name, without the
    module being imported again.
    """
    values = module_host.bind_inputs(
        _bindable(),
        module_host.resolve_slots(
            {"a_light": module_host.InputBinding(kind="slot", slot="light_group")},
            {"light_group": "light.kitchen"},
        ),
    )
    assert values["a_light"] == "light.kitchen"


def test_a_slot_that_is_bound_reaches_a_target_the_way_a_device_does() -> None:
    """Resolved *before* the wrapping, so a target input gets a target's shape."""
    values = module_host.bind_inputs(
        _bindable(),
        module_host.resolve_slots(
            {"some_lights": module_host.InputBinding(kind="slot", slot="light_group")},
            {"light_group": "light.kitchen"},
        ),
    )
    assert values["some_lights"] == {"entity_id": "light.kitchen"}


def test_an_unbound_slot_is_left_out_rather_than_written_in() -> None:
    """A slot name is not a device id, and the document must never say otherwise.

    Left out, the input takes the blueprint's own default -- the module is built
    as though nothing had answered it -- and `unresolved_slots` is what tells the
    caller which device is still missing, by the name the person gave it.
    """
    bindings = {
        "a_light": module_host.InputBinding(kind="slot", slot="ambient_light_sensor")
    }
    assert (
        module_host.bind_inputs(_bindable(), module_host.resolve_slots(bindings, {}))
        == {}
    )
    assert module_host.unresolved_slots(bindings, {}) == ("ambient_light_sensor",)


def test_a_global_slot_is_the_house_s_device_in_every_room() -> None:
    """A `house`-scoped answer skips the room's own binding for that name.

    A room that lights itself with its own lamp while the house's global
    `light_group` is the hall is the *only* case where a room-scoped answer and
    a global one differ -- which makes it the only case that can tell them
    apart, and the case a global slot exists for.
    """
    bindings = {
        "a_light": module_host.InputBinding(
            kind="slot", slot="light_group", scope=module_host.HOUSE_SCOPE
        )
    }
    bound = module_host.BoundSlots(
        room={"light_group": "light.study"},
        house={"light_group": "light.hall"},
    )
    values = module_host.bind_inputs(
        _bindable(), module_host.resolve_slots(bindings, bound)
    )
    assert values["a_light"] == "light.hall"


def test_the_same_binding_room_scoped_is_the_room_s_own_device() -> None:
    """The other half of the pair: the default scope is unchanged by any of this."""
    bindings = {
        "a_light": module_host.InputBinding(
            kind="slot", slot="light_group", scope=module_host.ROOM_SCOPE
        )
    }
    bound = module_host.BoundSlots(
        room={"light_group": "light.study"},
        house={"light_group": "light.hall"},
    )
    values = module_host.bind_inputs(
        _bindable(), module_host.resolve_slots(bindings, bound)
    )
    assert values["a_light"] == "light.study"


def test_a_global_slot_the_house_has_not_bound_waits() -> None:
    """A room's binding for the name does not answer a global slot.

    Reported waiting under the name the person gave it, because the device that
    is missing is the *house's*: filling it in from the room would be naming a
    device the module never reads, and the module would look bound and do
    nothing.
    """
    bindings = {
        "a_light": module_host.InputBinding(
            kind="slot", slot="light_group", scope=module_host.HOUSE_SCOPE
        )
    }
    bound = module_host.BoundSlots(room={"light_group": "light.study"}, house={})
    assert module_host.unresolved_slots(bindings, bound) == ("light_group",)
    assert module_host.resolve_slots(bindings, bound) == {}


def test_the_house_view_can_be_handed_in_on_its_own() -> None:
    """The explicit argument, for a caller holding the two maps apart already."""
    bindings = {
        "a_light": module_host.InputBinding(
            kind="slot", slot="light_group", scope=module_host.HOUSE_SCOPE
        )
    }
    values = module_host.bind_inputs(
        _bindable(),
        module_host.resolve_slots(
            bindings, {"light_group": "light.study"}, {"light_group": "light.hall"}
        ),
    )
    assert values["a_light"] == "light.hall"


def test_bound_slots_reads_as_the_room_view_it_always_was() -> None:
    """What lets `host.bound_slots` grow a second view without touching callers.

    Every reader that only ever asked "what does this room answer" holds a
    mapping and gets the room's answers; `house` is there for the readers that
    ask the other question, and for nobody else.
    """
    bound = module_host.BoundSlots(room={"a": "light.one"}, house={"a": "light.two"})
    assert bound == {"a": "light.one"}
    assert dict(bound) == {"a": "light.one"}
    assert set(bound) == {"a"}
    assert bound.house == {"a": "light.two"}


def test_a_slot_binding_that_reaches_a_bound_value_unchanged_refuses() -> None:
    """The invariant `resolve_slots` exists to keep, held at the boundary.

    A caller that skipped the resolution would write the slot's *name* where a
    device id belongs, and the automation would read a device nobody has. The
    refusal names the slot rather than the input, because the slot is what is
    missing.
    """
    with pytest.raises(AuthoringError, match="is a slot and not a device yet"):
        module_host.bind_inputs(
            _bindable(),
            {"a_light": module_host.InputBinding(kind="slot", slot="light_group")},
        )


def test_slots_reached_names_every_slot_the_bindings_use() -> None:
    """What a rebinding is measured against: bound or not, this is the set."""
    bindings = {
        "a_light": module_host.InputBinding(kind="slot", slot="light_group"),
        "threshold": module_host.InputBinding(kind="literal", value=1),
        "some_lights": module_host.InputBinding(kind="slot", slot="light_group"),
        "lux": module_host.InputBinding(kind="slot", slot="ambient_light_sensor"),
    }
    assert module_host.slots_reached(bindings) == (
        "ambient_light_sensor",
        "light_group",
    )
    assert module_host.slots_reached({}) == ()


# --------------------------------------------------------------------------
# What a module waits for: an option nobody has set yet
# --------------------------------------------------------------------------

_OPTIONS = "\n".join(
    [
        "blueprint:",
        "  name: Options",
        "  input:",
        "    lights:",
        "      selector:",
        "        target:",
        "          entity:",
        "            domain: light",
        "    lux_sensor:",
        "      selector:",
        "        entity:",
        "          domain: sensor",
        "    threshold:",
        "      default: 30",
        "      selector:",
        "        number:",
        "          min: 0",
        "    message:",
        "      default:",
        "      selector:",
        "        text:",
        # Two inputs that *do* declare a default and take a device anyway, which
        # is what an author's `default:` on one of these is: the field's
        # placeholder, not an answer.
        "    presence:",
        "      default: device_tracker.me",
        "      selector:",
        "        entity:",
        "          domain: device_tracker",
        "    some_lights:",
        "      default: light.kitchen",
        "      selector:",
        "        target: {}",
        # And one whose default names nothing, which is the line between the
        # two: an empty default selects no device and is a state the blueprint
        # may be run in.
        "    bypass_light:",
        "      default:",
        "      selector:",
        "        entity:",
        "          domain: light",
        "trigger: []",
        "action: []",
    ]
)


def _options() -> module_host.HostedSource:
    return module_host.read_module_source(_OPTIONS)


def test_an_option_nobody_has_answered_is_one_the_module_waits_for() -> None:
    """The input a person answered "Blueprint default" about.

    It is kept as an option and nothing has a value for it, so there is nothing
    to build the automation from. Held back rather than refused is what lets an
    import end in something a person can install and finish setting up -- the
    same shape a module waiting for a slot has.
    """
    assert module_host.unset_settings(_options(), {}, ("lights", "lux_sensor")) == (
        "lights",
        "lux_sensor",
    )


def test_a_default_is_an_answer_the_module_can_be_built_from() -> None:
    """A blueprint's own default is a value, so nothing is waiting for one.

    `message` is the case that reads as a trap: `default:` with nothing after it
    is `default: null`, and only the key's *presence* counts -- the rule
    `_substitute` reads too, and what makes a null default an input a blueprint
    may be built with.
    """
    assert module_host.unset_settings(_options(), {}, ("threshold", "message")) == ()


def test_an_option_somebody_has_answered_is_not_waited_for() -> None:
    bindings = {"lights": module_host.InputBinding(kind="entity", value="light.hall")}
    assert module_host.unset_settings(_options(), bindings, ("lights",)) == ()


def test_a_name_the_document_no_longer_declares_is_not_an_option() -> None:
    """A record outlives the document it was made from.

    A setting row for an input the document no longer declares is a name with
    nothing behind it, and waiting for it would hold the module back for ever.
    """
    assert module_host.unset_settings(_options(), {}, ("gone", "lights")) == ("lights",)


def test_the_options_are_named_in_the_order_the_module_kept_them() -> None:
    """The blueprint's order, which is the order a person read them in.

    So the sentence a screen writes names them as they were read rather than
    sorted into an order nobody chose.
    """
    assert module_host.unset_settings(_options(), {}, ("lux_sensor", "lights")) == (
        "lux_sensor",
        "lights",
    )


def test_a_default_that_names_a_device_is_not_an_answer_for_one() -> None:
    """`default: device_tracker.me` is a placeholder, not a device.

    It is what the author of a blueprint writes to say where the field goes, and
    the name is one on *their* installation. Read as an answer it would point the
    automation at a stranger's phone and -- worse, because nothing would look
    wrong -- let the input read as filled, so the module would be built and run
    against a device nobody here chose.
    """
    source = _options()
    assert not module_host.declares_default(source.inputs["presence"])
    assert not module_host.declares_default(source.inputs["some_lights"])
    # Every other selector keeps its default: a number or a message the author
    # wrote is a value this installation can use as it stands.
    assert module_host.declares_default(source.inputs["threshold"])
    assert module_host.declares_default(source.inputs["message"])
    # An input with no default at all is the case that was always this way.
    assert not module_host.declares_default(source.inputs["lux_sensor"])


def test_a_default_that_names_no_device_is_still_an_answer() -> None:
    """The line is between naming something and naming nothing.

    `default:` with nothing after it, on an input that takes a device, selects no
    device -- which is a state the blueprint is written to run in, because that
    is what the key being optional means. There is no device to be wrong about,
    so it holds nothing back; demanding an answer would turn every "(Optional)"
    device input into one an import cannot do without.
    """
    source = _options()
    assert module_host.declares_default(source.inputs["bypass_light"])
    assert module_host.unset_settings(_options(), {}, ("bypass_light",)) == ()


def test_a_named_device_default_leaves_the_module_waiting_for_a_person() -> None:
    """So the module is held back rather than built from the placeholder.

    The same shape as an option with no default at all: recorded, its outputs
    made, and no automation until somebody answers it -- which is what makes an
    entity input *undefined* on the import screen and on the module's own
    settings form.
    """
    assert module_host.unset_settings(
        _options(), {}, ("presence", "some_lights", "threshold")
    ) == ("presence", "some_lights")


_DEVICE_DEFAULT = "\n".join(
    [
        "blueprint:",
        "  name: Device default",
        "  input:",
        "    presence:",
        "      default: device_tracker.me",
        "      selector:",
        "        entity:",
        "          domain: device_tracker",
        "trigger: []",
        "action:",
        "  - service: light.turn_on",
        "    target:",
        "      entity_id: !input presence",
    ]
)


def test_a_device_default_is_never_written_into_the_automation() -> None:
    """The refusal that makes the rule airtight when nobody kept the input.

    A module is only held back for the inputs it *keeps*, so an input the person
    did not keep has nothing waiting on it -- and reaching the build with no
    answer must still not write `device_tracker.me` into an automation. It is
    named instead, which is the message a person sees and the one thing that
    cannot be mistaken for a working module.
    """
    with pytest.raises(module_host.AuthoringError) as refusal:
        module_host.instantiate(module_host.read_module_source(_DEVICE_DEFAULT), {})
    assert "presence" in str(refusal.value)


def test_a_template_answer_is_written_through_as_the_person_wrote_it() -> None:
    """**A condition on the input's own terms**, which is what a template is.

    Home Assistant renders a `{{ ... }}` in the field an automation reads, so a
    template is how one input answers differently for different states of the
    house -- "the sleep entity is this one while the house is asleep" -- which no
    single entity id can say. The server's whole part in it is to write the text
    through untouched: it travels as a literal, and nothing here tries to read
    the template, because the reading is Home Assistant's at run time.
    """
    condition = (
        "{{ 'input_boolean.sleep' "
        "if is_state('input_text.home_state', 'sleep') "
        "else 'input_boolean.awake' }}"
    )
    document = module_host.instantiate(
        module_host.read_module_source(_DEVICE_DEFAULT),
        {"presence": condition},
    )
    assert document["action"][0]["target"]["entity_id"] == condition


_TRIGGER_INPUT = "\n".join(
    [
        "blueprint:",
        "  name: Triggered by a device",
        "  input:",
        "    sleep_entity:",
        "      selector:",
        "        entity:",
        "    day_color_temp:",
        "      default: 5500",
        "      selector:",
        "        number:",
        "trigger:",
        "  - platform: state",
        "    entity_id: !input sleep_entity",
        "    to: 'on'",
        "action:",
        "  - service: light.turn_on",
        "    data:",
        "      kelvin: !input day_color_temp",
    ]
)


def test_the_input_a_trigger_names_is_told_apart_from_a_call_that_uses_one() -> None:
    """Which inputs a cast cannot be written over, read off the document.

    `sleep_entity` is the trigger's own `entity_id`, and that is the field Home
    Assistant does not render: a person's condition written there is compared as
    text against real entity ids and matches none of them, so the automation
    installs and never fires. `day_color_temp` reaches a service call, which is
    rendered on every run -- so a cast belongs there and nowhere near the
    trigger.

    Both are entity inputs a person might want to cast, which is exactly why the
    answer cannot be "entity inputs" and has to be "this input, in this
    document".
    """
    source = module_host.read_module_source(_TRIGGER_INPUT)
    assert module_host.input_in_trigger(source, "sleep_entity") is True
    assert module_host.input_in_trigger(source, "day_color_temp") is False
    # A name the document does not have at all is not in the trigger either,
    # rather than being an error: the question is asked about rows the server is
    # already building, and a row is never a name the blueprint dropped.
    assert module_host.input_in_trigger(source, "no_such_input") is False


_TRIGGER_FIELDS = "\n".join(
    [
        "blueprint:",
        "  name: The fields a trigger has",
        "  input:",
        "    watched:",
        "      selector:",
        "        entity:",
        "    state_wanted:",
        "      selector:",
        "        text:",
        "    watched_too:",
        "      selector:",
        "        entity:",
        "trigger:",
        "  - platform: state",
        "    entity_id:",
        "      - !input watched",
        "      - light.hall",
        "    to: !input state_wanted",
        "  - platform: state",
        "    entity_id: !input watched_too",
        "action: []",
    ]
)


def test_only_the_fields_a_trigger_matches_by_count_as_matched() -> None:
    """Which `!input`s under `trigger:` a cast cannot be written over.

    A state trigger renders its `to:` and its `for:` on every run, and only the
    fields naming the devices to watch are matched against real entity ids. So
    an input the trigger *watches* is one a condition would silently break, and
    an input the trigger merely *compares against* is one a condition works
    perfectly well in. Reading the document any wider -- "any input under
    `trigger:`" -- would take a cast away from an input that can take one, which
    is the person's answer being removed to prevent a mistake they were not
    making.
    """
    source = module_host.read_module_source(_TRIGGER_FIELDS)
    assert module_host.input_in_trigger(source, "watched") is True
    assert module_host.input_in_trigger(source, "watched_too") is True
    assert module_host.input_in_trigger(source, "state_wanted") is False
    assert module_host.input_in_trigger(source, "no_such_input") is False


def test_the_plural_clause_is_the_same_clause() -> None:
    """Home Assistant's own spelling of `trigger:`, which is not the file's.

    A blueprint validated by Home Assistant comes back with its clause renamed
    (`cv.renamed("trigger", "triggers")`), and the instance's own blueprint list
    is read by re-emitting what Home Assistant holds -- so the document the panel
    reads for a blueprint chosen from the picker carries `triggers:`. Reading one
    spelling and not the other would answer "nothing is watched" for every
    blueprint in the list and correctly for the same text pasted in, which is a
    difference nobody would think to look for.
    """
    plural = _TRIGGER_INPUT.replace("trigger:\n", "triggers:\n", 1)
    assert "triggers:" in plural
    source = module_host.read_module_source(plural)
    assert module_host.input_in_trigger(source, "sleep_entity") is True
    assert module_host.input_in_trigger(source, "day_color_temp") is False


def test_a_trigger_that_names_a_device_in_prose_names_nothing() -> None:
    """The marker is the input; a word that looks like it is a word.

    A blueprint is free to write an input's name in a description, a `to:` value
    or an alias, and none of those is the trigger being matched against a device
    -- offering no cast for them would be taking away an answer for a reason the
    document does not support.
    """
    source = module_host.read_module_source(
        "\n".join(
            [
                "blueprint:",
                "  name: A name in prose",
                "  input:",
                "    sleep_entity:",
                "      selector:",
                "        entity:",
                "trigger:",
                "  - platform: state",
                "    entity_id: light.hall",
                "    to: sleep_entity",
                "    alias: sleep_entity",
                "action: []",
            ]
        )
    )
    assert module_host.input_in_trigger(source, "sleep_entity") is False


def test_a_target_bound_to_several_devices_is_wrapped_as_a_list() -> None:
    """What a blueprint asks for when it wants "the lights" rather than "this one".

    The value is the ids and the wrapping is the selector's: a `target` takes a
    list of them under `entity_id`, which is the shape Home Assistant's own
    automation editor writes for one.
    """
    values = module_host.bind_inputs(
        _options(),
        {
            "lights": module_host.InputBinding(
                kind="entity", value=["light.kitchen", "light.hall"]
            )
        },
    )
    assert values["lights"] == {"entity_id": ["light.kitchen", "light.hall"]}


def test_a_condition_cast_gets_an_entity_of_its_own() -> None:
    """The entity a condition-cast input is bound to, spelled in one place.

    A condition cannot be written into a trigger's `entity_id` -- Home Assistant
    matches it against the entities the house has rather than rendering it, so
    logic there installs and never fires, silently -- so Open House makes the
    condition into an entity and binds the input to that. Which is why the name
    matters: the maker, the binding and the panel all address this one id.
    """
    assert (
        module_host.derived_entity_id("dynamic_lighting", "sleep_entity")
        == "binary_sensor.open_house_dynamic_lighting_sleep_entity"
    )


def test_an_entity_that_is_not_a_module_has_no_derived_entity() -> None:
    with pytest.raises(AuthoringError, match="not a module name"):
        module_host.derived_entity_id("Not A Module", "sleep_entity")
    with pytest.raises(AuthoringError, match="not an input name"):
        module_host.derived_entity_id("ok", "Not An Input")


# --------------------------------------------------------------------------
# The script cast: an input answered by what a script hands back
# --------------------------------------------------------------------------

#: The three shapes a script cast has to answer, in one document: an input that
#: takes a device, one that takes a value, and one the *trigger* names. Written
#: here rather than taken from the corpus because a corpus blueprint that happens
#: to answer one of them would leave the other two untested -- and the trigger is
#: the case the cast has to *refuse*, which no working blueprint exercises.
SCRIPTED = """
blueprint:
  name: Scripted
  input:
    lux_sensor:
      name: Lux sensor
      selector:
        entity:
          domain: sensor
    brightness:
      name: Brightness
      selector:
        number:
          min: 0
          max: 100
    watched:
      name: The device the trigger watches
      selector:
        entity:
trigger:
  - platform: state
    entity_id: !input watched
action:
  - service: light.turn_on
    target:
      entity_id: light.kitchen
    data:
      brightness_pct: !input brightness
"""


def _scripted() -> module_host.HostedSource:
    return module_host.read_module_source(SCRIPTED)


def test_a_script_answers_a_device_input_with_the_variable_it_wrote() -> None:
    """A call that returns a value, and the input that reads it.

    **Both halves come out of one call to `answer_with_scripts`**, and that is the
    point of it: the variable a script's answer lands in is the name the binding
    reads, so an input bound to `{{ oh_lux_sensor }}` beside a call that writes
    some `other_name` is an automation built against nothing -- it renders to an
    empty string, and nothing in the log says why.
    """
    bindings, calls = module_host.answer_with_scripts(
        _scripted(), {"lux_sensor": "work_it_out"}
    )
    assert bindings["lux_sensor"] == module_host.InputBinding(
        kind="entity", value="{{ oh_lux_sensor }}"
    )
    assert calls == (("work_it_out", "oh_lux_sensor"),)


def test_a_script_answers_a_value_input_with_a_template_reading_it() -> None:
    """A number input takes the value as a template and not as a device id.

    The same decision `binding_to_entity` makes for a flow's entity: an input that
    takes a *thing* is given the thing, and one that takes a value is given a
    template reading it -- because what a call hands back is a value, and writing
    the variable's raw name where a number belongs is a blueprint doing arithmetic
    on a string.
    """
    bindings, _calls = module_host.answer_with_scripts(
        _scripted(), {"brightness": "work_out_the_brightness"}
    )
    assert bindings["brightness"] == module_host.InputBinding(
        kind="literal", value="{{ oh_brightness }}"
    )


def test_a_script_the_trigger_names_is_refused() -> None:
    """The one place a script cast cannot go, refused rather than written in.

    A trigger's `entity_id` is matched against the entities a house has and never
    rendered, so a variable written there is compared as the text `{{ oh_watched }}`
    and matches nothing -- an automation that installs, reports nothing wrong, and
    never fires. A refusal the person can read is the only honest answer.
    """
    with pytest.raises(AuthoringError, match="named by the automation's trigger"):
        module_host.answer_with_scripts(_scripted(), {"watched": "work_it_out"})


def test_a_script_cast_names_a_script_that_exists() -> None:
    """The call is the person's script, however the panel spelled it.

    A definition moves between houses and the id travels as it was written, so a
    `script.` may arrive attached or not -- and a second `script.` on the front
    would be a step naming a service that does not exist, in an automation that
    otherwise installs perfectly.
    """
    for written in ("work_it_out", "script.work_it_out"):
        _bindings, calls = module_host.answer_with_scripts(
            _scripted(), {"lux_sensor": written}
        )
        assert calls == (("work_it_out", "oh_lux_sensor"),)


def test_a_name_with_no_script_behind_it_answers_nothing() -> None:
    """What an installation whose definition names an input the house has not
    picked a script for holds: the *name* and an empty id.

    Not a call to `script.` -- which is a step naming no script at all -- and not
    a binding either: the input goes back to being one nothing fills, which is a
    state the build already knows how to hold a module in.
    """
    bindings, calls = module_host.answer_with_scripts(
        _scripted(), {"lux_sensor": "", "brightness": ""}
    )
    assert bindings == {}
    assert calls == ()


def test_a_variable_the_document_already_takes_is_numbered_around() -> None:
    """The script's value does not clobber a variable the blueprint reads.

    A name is introduced into the person's own automation beside every variable the
    document declares, so the collision is real: overwriting one would show up as
    the wrong answer arriving from the wrong place, which is the worst kind to
    trace back. Numbered with an underscore rather than `_unique`'s `" #2"`,
    because a template variable is an identifier and a name with a space in it
    renders to nothing.
    """
    text = SCRIPTED.replace(
        "  - service: light.turn_on",
        "  - variables:\n      oh_lux_sensor: the blueprint's own reading\n"
        "  - service: light.turn_on",
    )
    bindings, calls = module_host.answer_with_scripts(
        module_host.read_module_source(text), {"lux_sensor": "work_it_out"}
    )
    assert bindings["lux_sensor"].value == "{{ oh_lux_sensor_2 }}"
    assert calls == (("work_it_out", "oh_lux_sensor_2"),)


def test_a_script_is_called_before_anything_uses_what_it_returned() -> None:
    """The call goes at the top of the action list, and that is the correctness.

    A script runs only when it is called, and the value it hands back is in scope
    for the rest of the list the call sits in -- so a use *above* the call renders
    the variable as nothing. Prepend rather than insert-beside, because where the
    first use is depends on the blueprint's own branches.
    """
    document = _scripted().document
    built = module_host.script_calls(document, (("work_it_out", "oh_lux_sensor"),))
    steps = built["action"]
    assert steps[0] == {
        "action": "script.work_it_out",
        "response_variable": "oh_lux_sensor",
    }
    assert len(steps) == len(document["action"]) + 1
    # The blueprint's own steps are untouched and still in their order.
    assert steps[1:] == list(document["action"])


def test_a_document_with_no_calls_is_copied_rather_than_returned() -> None:
    """Nothing answered by a script means nothing to add -- and the document that
    comes back is not the one handed in.

    `script_calls` runs on the instantiated automation, and callers go on to
    append to what it answers; handing back the same object would have them
    editing the caller's own document.
    """
    document = _scripted().document
    built = module_host.script_calls(document, ())
    assert built == document
    assert built is not document
