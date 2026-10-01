"""The operation registry: one definition of the surface, drawn from three ways.

`control-surface`'s "The operation registry is the closed, single definition of
the surface" and "The scenario runner's verbs are the control surface's verbs",
plus the descriptor-level halves of "Each operation states its scope" and "The
surface cannot turn a behaviour on, and cannot unlock or open except as a user".

What is checked here is the *descriptor*, not any behaviour: the set is closed
and matches the enumeration `spec.txt` writes down, every operation carries a
scope, no operation's parameters could enable a behaviour, and the runner's verb
table is the registry plus the port's house-control verbs and nothing else. Each
test names the implementation that would falsify it.
"""

from __future__ import annotations

import inspect
from collections.abc import Mapping, Sequence
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import cast

import pytest

from engine.vocabulary import Vocabulary
from openhouse.facade import open_session
from openhouse.operations import (
    HOUSE_CONTROL,
    HOUSE_CONTROL_NAMES,
    OPERATION_NAMES,
    OPERATIONS,
    Operation,
    Parameter,
    input_schema,
)
from sim.scenario.dsl import STEP_VERBS

ROOT = Path(__file__).resolve().parents[1]

#: `spec.txt`'s enumeration of the surface, in its own words, with the fourth
#: pair expanded. Written here as the *reading* of that sentence rather than
#: parsed from it, because the sentence is prose in a document that is not a
#: machine-readable list -- and the check's value is that a human's reading of
#: the sentence and the registry agree, which a parse of the same sentence could
#: not establish.
SPEC_TXT_OPERATIONS: tuple[str, ...] = (
    "advance_time",
    "set_state",
    "user_action",
    "inject_fault",
    "snapshot",
    "restore",
    "get_decision_log",
    "install_pack",
    "export_config",
    "import_config",
)


@pytest.fixture(scope="module")
def vocabulary() -> Vocabulary:
    """The committed catalog, frozen for the module."""
    return Vocabulary.load(ROOT)


def test_the_registry_is_exactly_the_enumeration() -> None:
    """A registry with an eleventh operation, or missing one, fails here.

    Both directions are checked. A missing name is the failure a reader of
    `spec.txt` notices; an extra one is the failure only this comparison
    notices, and it is the one that matters -- `run_scenario` and the
    house-control verbs are deliberately outside the registry, so a registry
    that absorbed one of them would still contain every name the enumeration
    lists.
    """
    assert OPERATION_NAMES == SPEC_TXT_OPERATIONS


def test_the_house_control_verbs_are_outside_the_registry() -> None:
    """The port's control face is not an operation of the engine's surface.

    Falsified by adding `restart` or `set_availability` to `OPERATIONS`, which
    would make the registry eleven or fourteen and give a scenario a way to
    provoke the house that the ten operations do not describe.
    """
    assert set(HOUSE_CONTROL_NAMES).isdisjoint(OPERATION_NAMES)
    assert HOUSE_CONTROL_NAMES == (
        "add_entity",
        "remove_entity",
        "set_availability",
        "restart",
    )


def test_the_registry_keys_are_the_operation_names() -> None:
    """A mapping keyed by something other than `operation.name` fails here.

    The key is how both surfaces look an operation up, so a key that disagreed
    with the descriptor's own name would make the CLI call one operation and
    advertise another.
    """
    for name, operation in OPERATIONS.items():
        assert operation.name == name


def test_every_operation_states_a_scope() -> None:
    """An operation whose scope is blank, or is its summary repeated, fails here.

    `control-surface` requires the scope to be surfaced through the CLI's
    `--help` and the tool's description, so a scope that says nothing would put
    the boundary in front of a caller without telling them anything.
    """
    for operation in (*OPERATIONS.values(), *HOUSE_CONTROL.values()):
        assert operation.scope.strip()
        assert operation.scope != operation.summary


def test_a_partial_operation_names_the_phase_that_completes_it() -> None:
    """The three partial operations must name their later phase in the scope.

    `install_pack`, `export_config` and `import_config` are each a deliberate
    subset of a feature a later phase owns. A scope that described the subset
    without naming the phase would leave a caller to discover the boundary by
    finding it; this check is what makes the naming a requirement rather than a
    courtesy.

    Phase 2 completed `install_pack` and rewrote its scope to say so, in the
    words "Phase 1's operation, completed in Phase 2". The entry below is kept
    and not removed, because the map's question is which phase a scope has to
    name and `install_pack` still has to name Phase 2 -- the phase that completed
    it rather than the phase that was still to come. Only `export_config` and
    `import_config` still name a phase that has not arrived.
    """
    partial = {
        "install_pack": "Phase 2",
        "export_config": "Phase 3",
        "import_config": "Phase 3",
    }
    for name, phase in partial.items():
        assert phase in OPERATIONS[name].scope


def test_no_operation_can_enable_a_behaviour() -> None:
    """No operation takes a parameter that could turn a behaviour on.

    `control-surface` has a clause for this and `product-invariants` has the
    rule behind it: activation is the composition root's act, so a surface that
    could switch a behaviour on would be a second way to change what the engine
    evaluates. Falsified by an operation taking an `enabled`, `enable_flag` or
    `behaviour` parameter -- so the check reads the *parameter names* of every
    operation rather than trusting the set of operations to be the right one.
    """
    forbidden = {"enabled", "enable", "enable_flag", "behaviour", "behaviours"}
    for operation in (*OPERATIONS.values(), *HOUSE_CONTROL.values()):
        taken = {parameter.name for parameter in operation.parameters}
        assert taken.isdisjoint(forbidden), operation.name


def test_an_operation_that_could_open_does_so_only_through_a_user_action() -> None:
    """`user_action` is the only write whose origin admits an unlock or an open.

    The registry cannot express an origin, so what is checkable at the
    descriptor is that the two write operations are named for their origins and
    that no third write exists: a `write_state` or `set_device` added beside
    them would be a write whose origin a reader has to look up.
    """
    writers = [
        operation.name
        for operation in OPERATIONS.values()
        if {parameter.name for parameter in operation.parameters}
        >= {"entity_id", "state"}
    ]
    assert writers == ["set_state", "user_action"]


@pytest.mark.parametrize("name", OPERATION_NAMES)
def test_the_input_schema_names_every_parameter_exactly_once(name: str) -> None:
    """The derived schema's properties are the descriptor's parameters.

    The MCP tool's input schema is *this* object, so a parameter the schema
    dropped would be an argument no tool could accept, and a property the
    descriptor does not carry would be one no handler receives.
    """
    operation = OPERATIONS[name]
    schema = input_schema(operation)
    assert schema["type"] == "object"
    properties = cast("Mapping[str, object]", schema["properties"])
    assert tuple(properties) == tuple(
        parameter.name for parameter in operation.parameters
    )
    assert schema["additionalProperties"] is False


@pytest.mark.parametrize("name", OPERATION_NAMES)
def test_the_required_list_is_the_required_parameters(name: str) -> None:
    """A parameter's `required` flag and the schema's `required` array agree."""
    operation = OPERATIONS[name]
    schema = input_schema(operation)
    assert schema["required"] == [
        parameter.name for parameter in operation.parameters if parameter.required
    ]


def test_a_required_parameter_never_carries_a_default() -> None:
    """A parameter that is required *and* defaulted is a contradiction.

    The two surfaces would resolve it differently -- a CLI would pass the
    default, a JSON Schema client would refuse the call for a missing required
    property -- so the registry rejects the shape rather than leaving the
    surfaces to pick.
    """
    for operation in (*OPERATIONS.values(), *HOUSE_CONTROL.values()):
        for parameter in operation.parameters:
            if parameter.required:
                assert parameter.default is None, (operation.name, parameter.name)


def test_every_parameter_kind_is_one_both_surfaces_can_express() -> None:
    """No parameter carries a kind outside the closed `Kind` literal.

    Falsified by a parameter declared `"float"`: Typer would need an annotation
    for it and the JSON Schema would carry a type no validator knows.
    """
    known = {"string", "integer", "number", "boolean", "object", "array"}
    for operation in (*OPERATIONS.values(), *HOUSE_CONTROL.values()):
        for parameter in operation.parameters:
            assert parameter.kind in known, (operation.name, parameter.name)


def test_the_runners_verbs_are_the_registry_plus_the_port() -> None:
    """The DSL's verb table is the ten operations and the four control verbs.

    `scenario-runner` requires the step verbs to be the control surface's verbs
    and no others. The comparison is two-sided on purpose: a verb in the DSL
    that no operation implements is a step no surface could perform, and an
    operation the DSL lacks is a decision a scenario cannot provoke. This check
    lives here rather than in `sim/` because the registry is what it compares
    against, and `sim/` may not import the composition root.
    """
    assert (*OPERATION_NAMES, *HOUSE_CONTROL_NAMES) == STEP_VERBS


def test_every_handler_is_callable_through_the_facade(vocabulary: Vocabulary) -> None:
    """Each operation's handler is a public method of an open session.

    "Every operation in the registry is callable through the facade" is the
    claim `operations.py` makes by storing a callable rather than a method name.
    This exercises it: a session is opened and each handler is looked up on it,
    so a handler wired to a method that does not exist fails here rather than
    when a caller first invokes it.
    """
    session = open_session(house="minimal", vocabulary=vocabulary)
    for operation in (*OPERATIONS.values(), *HOUSE_CONTROL.values()):
        assert callable(operation.handler)
    # The handlers close over the facade's methods; the operation names are the
    # method names, which is what a surface relies on when it reports a result.
    for name in (*OPERATION_NAMES, *HOUSE_CONTROL_NAMES):
        assert callable(getattr(session, name))


def test_an_operation_is_a_frozen_descriptor() -> None:
    """The descriptor cannot be mutated after import.

    A surface reads the descriptor at import time and again at call time; a
    mutable one would let a caller change what a later call accepts.
    """
    operation = OPERATIONS["advance_time"]
    with pytest.raises(FrozenInstanceError):
        operation.name = "something_else"  # type: ignore[misc]


def test_the_two_mappings_are_read_only_views_of_one_definition() -> None:
    """`OPERATIONS` and `HOUSE_CONTROL` are built once, from tuples of descriptors.

    Falsified by a module that rebuilt either mapping per call: the CLI's help
    and the MCP tool list would then be able to disagree about the same session.
    """
    assert isinstance(OPERATIONS, Mapping)
    assert isinstance(HOUSE_CONTROL, Mapping)
    assert tuple(OPERATIONS) == OPERATION_NAMES
    assert tuple(HOUSE_CONTROL) == HOUSE_CONTROL_NAMES


def test_the_scope_of_a_partial_operation_is_not_a_promise_of_the_whole() -> None:
    """A partial operation's scope names what it does *not* do.

    Each of the three says what the later phase owns, so a caller reading the
    tool description is told the boundary rather than only the capability. The
    check is the negative: an `install_pack` scope that read "installs a pack"
    without naming the sandbox would pass every other test in this module.

    `install_pack`'s scope was rewritten in Phase 2 and its assertions here are
    unchanged: it still says in its own words what it does not do -- installing
    does not enable what the pack declares -- which is a different boundary from
    the one Phase 1 stated and the same kind of statement. Phase 1's boundary,
    that the sandbox was Phase 2's, is gone because the sandbox is here.
    """
    assert "Phase 2" in OPERATIONS["install_pack"].scope
    assert "not" in OPERATIONS["install_pack"].scope
    assert "Phase 3" in OPERATIONS["export_config"].scope
    assert "not" in OPERATIONS["export_config"].scope


def test_parameters_are_a_tuple_of_parameter_values() -> None:
    """Every operation's parameters are immutable and of the declared type."""
    for operation in (*OPERATIONS.values(), *HOUSE_CONTROL.values()):
        assert isinstance(operation.parameters, tuple)
        for parameter in operation.parameters:
            assert isinstance(parameter, Parameter)
            assert isinstance(operation, Operation)


def test_the_ten_operations_are_the_only_ones_that_tick() -> None:
    """`advance_time` is the only operation that moves the clock.

    A second operation taking `minutes` would be a second way for time to pass,
    and `simulation`'s "the virtual clock is the only source of time" is only
    true of the surface if the surface has one such operation. Checked on the
    descriptor's parameter names, which is where a second one would show.
    """
    ticking = [
        operation.name
        for operation in (*OPERATIONS.values(), *HOUSE_CONTROL.values())
        if "minutes" in {parameter.name for parameter in operation.parameters}
    ]
    assert ticking == ["advance_time"]


def test_no_operation_takes_a_clock_or_an_instant() -> None:
    """Nothing on the surface takes a time, because the clock owns it.

    Falsified by a `restore_at` or an `advance_to` taking a timestamp: the
    session's instant would then be a caller's argument rather than a function
    of the clock, and `deterministic_replay` would stop being a property of the
    run's inputs.
    """
    forbidden = {"at", "now", "instant", "clock", "timestamp", "date"}
    for operation in (*OPERATIONS.values(), *HOUSE_CONTROL.values()):
        taken = {parameter.name for parameter in operation.parameters}
        assert taken.isdisjoint(forbidden), operation.name


def test_the_parameter_free_operations_take_nothing() -> None:
    """A no-parameter operation is one the surfaces can call with nothing.

    `snapshot` and `export_config` take nothing, and so does the control verb
    `restart`. This is asserted rather than assumed because the CLI builds a
    command with no arguments from the same descriptor, and an empty tuple that
    was actually an absent one would produce a command that could not be called.
    """
    for name in ("snapshot", "export_config", "restart"):
        operation = OPERATIONS.get(name) or HOUSE_CONTROL[name]
        assert operation.parameters == ()


def test_the_parameter_names_a_handler_can_accept_are_the_declared_ones() -> None:
    """The declared parameter names are what the handler is called with.

    Both faces call `handler(session, **values)`, so a declared name the
    handler does not accept is a `TypeError` at the first call rather than a
    schema disagreement. The handler's own signature is inspected here without
    calling it.
    """
    for operation in (*OPERATIONS.values(), *HOUSE_CONTROL.values()):
        signature = inspect.signature(operation.handler)
        keywords = {
            name
            for name, parameter in signature.parameters.items()
            if parameter.kind is inspect.Parameter.KEYWORD_ONLY
        }
        declared = {parameter.name for parameter in operation.parameters}
        assert declared <= keywords, operation.name


def test_the_registry_is_ordered_as_the_enumerations_read() -> None:
    """The order is `spec.txt`'s, and the house-control order is the port's.

    Order is not behaviour, but it is what a reader of `--help` and a reader of
    the spec see, and a registry that reordered itself would make the two
    differ for no reason a caller could see.
    """
    assert OPERATION_NAMES[0] == "advance_time"
    assert OPERATION_NAMES[-1] == "import_config"
    assert HOUSE_CONTROL_NAMES[0] == "add_entity"
    assert HOUSE_CONTROL_NAMES[-1] == "restart"


def test_the_names_are_a_plain_tuple_and_not_a_view() -> None:
    """`OPERATION_NAMES` and `HOUSE_CONTROL_NAMES` are tuples of strings.

    A view over a mutable mapping would be a name list that changed when a
    caller changed the mapping, and the surfaces build their command and tool
    lists from these. The stability across *imports* cannot be checked
    in-process -- reloading the module would replace the mapping a later test
    compares against -- so what is checked is the type.
    """
    assert isinstance(OPERATION_NAMES, tuple)
    assert isinstance(HOUSE_CONTROL_NAMES, tuple)
    assert all(isinstance(name, str) for name in OPERATION_NAMES)
    assert all(isinstance(name, str) for name in HOUSE_CONTROL_NAMES)


def test_house_control_parameters_are_the_ports_own() -> None:
    """The control verbs take the port's arguments and nothing more.

    `set_availability` takes a boolean availability *beside* a state, which is
    the shape `house-adapter` requires: availability is separate from state, so
    a control verb that folded the two into one parameter would make
    "unavailable is not off" inexpressible from a scenario.
    """
    availability = HOUSE_CONTROL["set_availability"]
    kinds = {parameter.name: parameter.kind for parameter in availability.parameters}
    assert kinds == {"entity_id": "string", "available": "boolean"}
    added = HOUSE_CONTROL["add_entity"]
    assert {parameter.name for parameter in added.parameters} == {
        "entity_id",
        "state",
        "attributes",
    }


def test_a_scope_is_a_sentence_and_not_a_restatement_of_the_summary() -> None:
    """Every scope reads as a sentence: it ends in a full stop and runs on.

    This is a prose check on purpose. The scope is what a caller meets in
    `--help` and in a tool description, and the failure it guards against -- a
    scope written as a label like "Phase 1" -- is one no type can catch.
    """
    for operation in (*OPERATIONS.values(), *HOUSE_CONTROL.values()):
        assert operation.scope.endswith("."), operation.name
        assert len(operation.scope.split()) >= 8, operation.name


def test_the_handler_count_is_one_per_operation() -> None:
    """No two operations share a handler.

    A shared handler would be two names for one call, which is the drift the
    registry exists to prevent: a caller would read two descriptions and get one
    behaviour.
    """
    handlers = [operation.handler for operation in OPERATIONS.values()]
    assert len(set(map(id, handlers))) == len(handlers)


def test_nothing_in_the_registry_is_a_sequence_of_strings() -> None:
    """The registry holds descriptors, not names.

    Falsified by a registry written as a tuple of strings with the descriptors
    assembled elsewhere: the surfaces would then have a second place to look,
    which is the arrangement the module's docstring rules out.
    """
    for name, operation in OPERATIONS.items():
        assert not isinstance(operation, str), name


def test_the_parameters_of_an_operation_are_in_the_schemas_property_order() -> None:
    """A schema's properties are in the descriptor's parameter order.

    A client rendering a form from the schema shows the parameters in this
    order, so an order that changed between the descriptor and the schema would
    make the same operation present its arguments two ways.
    """
    for name, operation in OPERATIONS.items():
        properties = cast("Mapping[str, object]", input_schema(operation)["properties"])
        assert list(properties) == [
            parameter.name for parameter in operation.parameters
        ], name


def test_an_operation_with_no_parameters_declares_an_empty_required_list() -> None:
    """`required` is present and empty rather than absent.

    Two forms of "takes nothing" would make a client that reads `required`
    unconditionally fail on one of them, and a client that defaults a missing
    `required` to "all properties" would refuse a valid call.
    """
    schema = input_schema(OPERATIONS["snapshot"])
    assert schema["required"] == []
    assert schema["properties"] == {}


def test_the_descriptor_types_are_importable_from_the_registry_module() -> None:
    """`Operation` and `Parameter` are the registry's own types.

    A surface that declared its own pair would be the third definition the
    module's docstring is written against.
    """
    from openhouse import operations as module

    assert module.Operation is Operation
    assert module.Parameter is Parameter


def test_input_schema_returns_a_fresh_mapping_per_call() -> None:
    """The schema is built per call, so a mutation of one call's result is local.

    Falsified by a cached schema: a surface that added a key to the object it
    was handed would change what every later caller sees.
    """
    first = input_schema(OPERATIONS["advance_time"])
    second = input_schema(OPERATIONS["advance_time"])
    assert first == second
    assert first is not second


def test_no_operation_is_named_for_a_corpus_slot(vocabulary: Vocabulary) -> None:
    """No operation takes a parameter named for one of the corpus's slots.

    The engine carries no vocabulary of its own, and the surface is the face of
    the decisions, not of the corpus. A `get_slot_state` or a `slot` parameter
    would put corpus names on the surface where a client would read them as the
    surface's vocabulary.
    """
    forbidden = set(vocabulary.slots)
    assert forbidden, "the corpus defines no slots, so this check would be vacuous"
    for operation in (*OPERATIONS.values(), *HOUSE_CONTROL.values()):
        taken = {parameter.name for parameter in operation.parameters}
        assert taken.isdisjoint(forbidden), operation.name


def test_a_sequence_of_descriptors_is_hashable() -> None:
    """Descriptors are frozen, so a set of them is a set of distinct operations.

    This is what lets the surfaces compare descriptor identity rather than
    re-deriving equality from fields.
    """
    assert len(set(OPERATIONS.values())) == len(OPERATIONS)
    assert len(set(HOUSE_CONTROL.values())) == len(HOUSE_CONTROL)


def test_the_operations_are_exposed_through_the_package_interface() -> None:
    """The registry module exports exactly the names its `__all__` lists."""
    from openhouse import operations as module

    for name in module.__all__:
        assert hasattr(module, name), name


def test_every_scope_names_what_the_operation_does_not_reach() -> None:
    """Each scope draws a boundary, in the words "not" or a phase name.

    Weaker than the partial-operation check above and deliberately so: it holds
    for all fourteen descriptors rather than the three that name a phase, and it
    is what makes "a scope states its boundary" checkable without asserting the
    boundary is the right one -- which is the critic's reading, not a test's.
    """
    for operation in (*OPERATIONS.values(), *HOUSE_CONTROL.values()):
        scope = operation.scope
        assert "not" in scope or "Phase" in scope, operation.name


def test_the_parameter_descriptions_are_not_empty() -> None:
    """Every parameter carries a description a client can show.

    The description reaches the JSON Schema unchanged, so an empty one would
    reach a caller as a parameter with no explanation.
    """
    for operation in (*OPERATIONS.values(), *HOUSE_CONTROL.values()):
        for parameter in operation.parameters:
            assert parameter.description.strip(), (operation.name, parameter.name)


def test_the_registry_is_the_same_object_for_every_importer() -> None:
    """`OPERATIONS` is a single mapping, not a factory's output."""
    from openhouse import operations as module

    assert module.OPERATIONS is OPERATIONS


def test_scope_text_never_promises_a_later_phase_operation_now() -> None:
    """A scope naming a later phase says the capability is *not* this phase's.

    Falsified by rewriting a scope to describe a later phase's work as present:
    the string naming that phase would still be there, so the check reads for the
    phrase's direction rather than its presence.

    `install_pack` is still in the tuple after Phase 2 completed it, and it is
    the case the check now has to be read carefully against: its scope says
    "Phase 1's operation, completed in Phase 2", which keeps the "Phase 1"
    mention without claiming that Phase 1 is where its work stopped. A scope that
    dropped the "Phase 1" mention would fail here, which is the direction this
    check exists for.
    """
    for name in ("install_pack", "export_config", "import_config"):
        scope = OPERATIONS[name].scope
        assert "Phase 1" in scope, name


def test_the_ten_are_listed_before_the_four_where_both_are_shown() -> None:
    """A caller reading the surfaces sees the ten, then the control verbs.

    This is the order the CLI registers them in and the order the MCP tool list
    uses; asserting it here keeps the two from choosing differently.
    """
    assert (
        *OPERATION_NAMES,
        *HOUSE_CONTROL_NAMES,
    ) == OPERATION_NAMES + HOUSE_CONTROL_NAMES
    both: Sequence[str] = (*OPERATION_NAMES, *HOUSE_CONTROL_NAMES)
    assert len(set(both)) == len(both)
