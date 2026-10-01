"""The scenario DSL: a `when` step is a control-surface call and nothing else.

Task 9.0's YAML DSL, and the `scenario-runner` requirements "A scenario is a
validated YAML document with three blocks" and "Scenario step verbs are the
control surface's verbs, and no others".

The check the requirement is made of is mechanical: the verb table is compared to
the `control-surface` operation registry plus the port's house-control verbs, and
every verb is compiled from its descriptor's *own* required parameters, so a
bespoke DSL -- a `turn_on`, a `wait`, an `expect` -- fails here instead of passing
as a vocabulary that merely resembles the surface. The loader's half is the closed
document: an unknown block, a missing block, a second verb key and an extra
parameter are each refused, because a scenario that "passes" on a misspelled key
is a green run that proves nothing.

A falsifying implementation is one that carries a verb the registry does not, one
that compiles a step missing a required parameter, or a loader that accepts an
unknown key. Each test names the shape it would fail against.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from engine.vocabulary import Vocabulary
from openhouse.facade import open_session
from openhouse.operations import (
    HOUSE_CONTROL,
    HOUSE_CONTROL_NAMES,
    OPERATION_NAMES,
    OPERATIONS,
)
from sim.scenario import (
    STEP_VERBS,
    InvalidStepError,
    LoadError,
    Step,
    UnknownDocumentError,
    UnknownVerbError,
    load_scenario,
)
from sim.scenario.dsl import compile_step

if TYPE_CHECKING:
    from collections.abc import Mapping

    from openhouse.facade import OpenHouse

ROOT = Path(__file__).resolve().parents[1]

#: The pieces every well-formed document in this module is built from, so a test
#: about one block does not have to restate the other two and fail on those.
_GIVEN = "given:\n  house: minimal"
_THEN = 'then:\n  - state:\n      entity_id: light.foyer\n      is: "off"'

#: A sample value per parameter kind, so a verb can be compiled from the
#: parameters its descriptor declares without the values being meaningful: the
#: compiler checks a parameter's kind and never its meaning.
_SAMPLE_VALUES: Mapping[str, object] = {
    "string": "a-value",
    "integer": 1,
    "number": 1.0,
    "boolean": True,
    "object": {},
    "array": [],
}


@pytest.fixture(scope="module")
def vocabulary() -> Vocabulary:
    """The committed catalog, frozen, so one load serves the module."""
    return Vocabulary.load(ROOT)


@pytest.fixture(scope="module")
def surface(vocabulary: Vocabulary) -> OpenHouse:
    """A built house for the compiler tests that resolve a document at call time."""
    return open_session(house="minimal", vocabulary=vocabulary)


def _write(tmp_path: Path, text: str, name: str = "scenario.yaml") -> Path:
    """A scenario document on disk, LF, no translation."""
    path = tmp_path / name
    path.write_text(text, encoding="utf-8", newline="")
    return path


def _scenario(when: str) -> str:
    """A scenario document whose `when` block is written out by the caller."""
    return f"{_GIVEN}\n{when}\n{_THEN}\n"


# --------------------------------------------------------------------------
# The verb table is the control surface's
# --------------------------------------------------------------------------


def test_the_step_verbs_are_the_ten_operations_and_the_four_control_verbs() -> None:
    """`STEP_VERBS` is the registry in order, then the port's verbs in their order.

    A falsifying implementation that invented a verb, dropped one, or reordered
    the two halves would still compile something for every name it kept, so only
    the equality distinguishes it from a table that is the surface's own.
    """
    assert (*OPERATION_NAMES, *HOUSE_CONTROL_NAMES) == STEP_VERBS
    assert len(STEP_VERBS) == 14


@pytest.mark.parametrize("verb", STEP_VERBS)
def test_every_verb_takes_the_descriptors_own_parameters(verb: str) -> None:
    """A verb compiles from its descriptor's required parameters and no fewer.

    Two falsifying shapes are caught at once. A builder reading a parameter the
    descriptor does not declare makes the first compilation fail; a builder that
    stopped requiring a parameter the descriptor marks required makes the
    omission compile, which the inner `pytest.raises` would not see. An
    operation with no required parameters -- `snapshot`, `export_config`,
    `restart` -- compiles from nothing and the loop is empty, which is the
    correct reading of a parameterless operation rather than a skipped case.
    """
    operation = OPERATIONS.get(verb) or HOUSE_CONTROL[verb]
    parameters = {
        parameter.name: _SAMPLE_VALUES[parameter.kind]
        for parameter in operation.parameters
        if parameter.required
    }
    call = compile_step(Step(verb=verb, parameters=parameters))
    assert call.verb == verb
    for parameter in operation.parameters:
        if not parameter.required:
            continue
        reduced = {
            name: value for name, value in parameters.items() if name != parameter.name
        }
        with pytest.raises(InvalidStepError) as info:
            compile_step(Step(verb=verb, parameters=reduced))
        assert info.value.parameter == parameter.name


def test_a_parameterless_verb_takes_no_parameters() -> None:
    """`restart` and `snapshot` are declared with an empty parameter tuple.

    A falsifying implementation that gave either one an argument would make it a
    verb with a parameter the port does not have -- and the loader's schema, which
    refuses a property on both, is the other half of the same claim.
    """
    assert OPERATIONS["snapshot"].parameters == ()
    assert HOUSE_CONTROL["restart"].parameters == ()
    assert compile_step(Step(verb="restart", parameters={})).verb == "restart"
    assert compile_step(Step(verb="snapshot", parameters={})).verb == "snapshot"


def test_only_the_two_document_producers_name_what_they_saved() -> None:
    """`save` is the bookkeeping of `snapshot` and `export_config`, and of nothing else.

    The name is where the runner keeps a produced document, not an argument the
    operation receives; a falsifying implementation that let an `advance_time`
    step claim a name would record a result under a document slot no later step
    could consume and no operation returned.
    """
    assert compile_step(Step(verb="snapshot", parameters={"save": "before"})).save == (
        "before"
    )
    assert compile_step(Step(verb="export_config", parameters={})).save is None
    assert (
        compile_step(Step(verb="advance_time", parameters={"minutes": 1})).save is None
    )


# --------------------------------------------------------------------------
# The compiler refuses what the surface does not have
# --------------------------------------------------------------------------


def test_an_unknown_verb_is_refused_and_the_known_ones_are_named() -> None:
    """A verb the registry does not carry fails the compiler, naming itself.

    A falsifying implementation with a fallback verb, or one that treated an
    unknown name as a no-op, would make `turn_on` a step that does nothing rather
    than a scenario that will not load.
    """
    with pytest.raises(UnknownVerbError) as info:
        compile_step(Step(verb="turn_on", parameters={}))
    assert info.value.verb == "turn_on"
    assert info.value.known == STEP_VERBS
    assert "turn_on" in str(info.value)
    assert "advance_time" in str(info.value)


@pytest.mark.parametrize(
    ("verb", "parameters", "missing"),
    (
        ("advance_time", {}, "minutes"),
        ("set_state", {"entity_id": "light.foyer"}, "state"),
        ("user_action", {"entity_id": "light.foyer"}, "state"),
        ("inject_fault", {"entity_id": "light.foyer"}, "fault"),
        ("add_entity", {"entity_id": "switch.new"}, "state"),
        ("remove_entity", {}, "entity_id"),
        ("set_availability", {"entity_id": "light.foyer"}, "available"),
        ("restore", {}, "document"),
        ("import_config", {}, "document"),
        ("install_pack", {}, "manifest"),
    ),
)
def test_a_missing_parameter_names_the_verb_and_the_parameter(
    verb: str, parameters: Mapping[str, object], missing: str
) -> None:
    """A hand-built step missing a required parameter fails naming both halves.

    The loader's schema refuses this shape long before a step is compiled, so
    what this pins is the compiler's own failure for a `Step` built in memory:
    a bare `KeyError` would name neither the verb nor the parameter, and a report
    downstream could not name the scenario's mistake.
    """
    with pytest.raises(InvalidStepError) as info:
        compile_step(Step(verb=verb, parameters=parameters))
    assert info.value.verb == verb
    assert info.value.parameter == missing
    assert verb in str(info.value)
    assert missing in str(info.value)


@pytest.mark.parametrize(
    ("verb", "parameters", "parameter"),
    (
        ("advance_time", {"minutes": "soon"}, "minutes"),
        ("advance_time", {"minutes": True}, "minutes"),
        ("set_state", {"entity_id": 5, "state": "on"}, "entity_id"),
        (
            "set_availability",
            {"entity_id": "light.foyer", "available": "yes"},
            "available",
        ),
        (
            "add_entity",
            {"entity_id": "switch.new", "state": "on", "attributes": []},
            "attributes",
        ),
    ),
)
def test_a_parameter_of_the_wrong_kind_is_refused(
    verb: str, parameters: Mapping[str, object], parameter: str
) -> None:
    """A parameter of the wrong kind fails, naming the parameter it found.

    A falsifying implementation that coerced whatever it was given would let
    `minutes: true` advance the clock by one minute and `attributes: []` reach
    `add_entity` as a mapping it is not, so the failure would surface three
    frames away from the step that caused it.
    """
    with pytest.raises(InvalidStepError) as info:
        compile_step(Step(verb=verb, parameters=parameters))
    assert info.value.parameter == parameter


def test_a_document_reference_to_an_unsaved_name_fails_naming_it(
    surface: OpenHouse,
) -> None:
    """A `{from: <name>}` no earlier step saved fails when the call is made.

    The reference is resolved at call time rather than at compile time, so what
    this pins is that an unresolvable name is still refused -- naming the name and
    the documents that do exist -- instead of reaching `restore` as a document it
    would read as a snapshot.
    """
    call = compile_step(
        Step(verb="restore", parameters={"document": {"from": "snapshot_before"}})
    )
    documents: dict[str, Mapping[str, object]] = {}
    with pytest.raises(UnknownDocumentError) as info:
        call.invoke(surface, documents)
    assert info.value.name == "snapshot_before"
    assert info.value.held == ()
    assert "snapshot_before" in str(info.value)


# --------------------------------------------------------------------------
# The loader's closed document
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("when", "token"),
    (
        ("when:\n  - turn_on:\n      entity_id: light.foyer", "turn_on"),
        ("when:\n  - advance_time:\n      minutes: 1\n      seconds: 3", "seconds"),
        ("when:\n  - restart:\n      minutes: 5", "minutes"),
    ),
)
def test_a_step_key_outside_the_vocabulary_is_refused(
    when: str, token: str, tmp_path: Path
) -> None:
    """An unknown verb, an extra parameter and a parameter on `restart` all fail.

    The schema's `additionalProperties: false` is what refuses each, so a
    falsifying loader that accepted any YAML would let a misspelled verb become a
    step nothing performs and a misspelled parameter a value nothing reads.
    """
    path = _write(tmp_path, _scenario(when))
    with pytest.raises(LoadError) as info:
        load_scenario(path)
    assert token in str(info.value)
    assert info.value.location.startswith("when/0")


def test_a_step_naming_two_verbs_is_refused(tmp_path: Path) -> None:
    """A step is exactly one key, so it cannot carry a second verb it never calls.

    A falsifying implementation that took the step's first key, or its last,
    would make one of the two verbs silently unexecuted and the scenario would
    still "pass" -- which is the failure the schema's `maxProperties: 1` exists
    to prevent.
    """
    when = (
        "when:\n"
        "  - advance_time:\n"
        "      minutes: 1\n"
        "    set_state:\n"
        "      entity_id: light.foyer\n"
        '      state: "on"'
    )
    path = _write(tmp_path, _scenario(when))
    with pytest.raises(LoadError) as info:
        load_scenario(path)
    assert info.value.location == "when/0"


def test_a_scenario_with_an_unknown_top_level_block_is_refused(tmp_path: Path) -> None:
    """A fourth top-level key fails and is named.

    A falsifying implementation that ignored keys it did not know would let a
    `scope:` or a misspelled `wen:` sit in a document that loads, so the reader
    would believe a block was honoured that nothing read.
    """
    document = f"{_GIVEN}\nwhen: []\n{_THEN}\nscope: house\n"
    path = _write(tmp_path, document)
    with pytest.raises(LoadError) as info:
        load_scenario(path)
    assert "scope" in str(info.value)
    assert info.value.location == "<root>"


def test_a_scenario_without_a_when_block_is_refused(tmp_path: Path) -> None:
    """A scenario missing one of its three blocks fails and names the block.

    A falsifying implementation that defaulted a missing `when` to an empty list
    would make a scenario that never ran its steps indistinguishable from one
    that ran none, which is the green-run-that-proves-nothing the schema's
    `required` list exists to refuse.
    """
    path = _write(tmp_path, f"{_GIVEN}\n{_THEN}\n")
    with pytest.raises(LoadError) as info:
        load_scenario(path)
    assert "when" in str(info.value)


def test_a_then_block_that_asserts_nothing_is_refused(tmp_path: Path) -> None:
    """An empty `then` fails loading, so it cannot be mistaken for a passing run.

    A falsifying implementation that accepted `then: []` would report a green run
    for a scenario that asserted nothing, and the report would be indistinguishable
    from one that asserted and held.
    """
    path = _write(tmp_path, f"{_GIVEN}\nwhen: []\nthen: []\n")
    with pytest.raises(LoadError) as info:
        load_scenario(path)
    assert "then" in str(info.value)
