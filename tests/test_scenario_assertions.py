"""The two halves of a `then` block: the house's read, and the log's oracle.

Tasks 9.1 and 9.2, and the `scenario-runner` requirements "State assertions read
the house, and availability is asserted apart from state" and "Decision-log
assertions make the log the oracle".

Two properties are what make these assertions about the system rather than about a
second implementation of it. A `StateExpectation` **reads** the port's view and
compares it to the expectation; it never asks the engine's rules what the value
should have been. A `LogExpectation` reads the records the engine appended, so
"the light is off" and "the timeout turned the light off" are different
assertions -- the distinction `design.md` D2 exists to keep and the one an
assertion on final device state alone cannot make.

A falsifying implementation is one that derives the expected value instead of
reading it, that folds availability into the entity's state, or that lets a
`because` citation be satisfied by a record that reached the rule and declined.
Each test names the shape it would fail against.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from engine.vocabulary import Vocabulary
from openhouse.facade import open_session
from sim.scenario import (
    Given,
    LoadError,
    LogExpectation,
    Scenario,
    ScenarioFailed,
    StateExpectation,
    Then,
    load_scenario,
    matches,
    run_scenario,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from openhouse.facade import OpenHouse

ROOT = Path(__file__).resolve().parents[1]

#: One acted record, in the engine's normative field form, so the log matchers
#: are exercised against a plausible document rather than an empty one.
_ACTED: Mapping[str, object] = {
    "actor": "motion_lighting",
    "rule": "lighting.motion_light_off",
    "outcome": "acted",
    "inputs": [
        {"kind": "read", "slot": "light_group", "entities": ["light.hallway"]},
    ],
    "commands": [
        {"slot": "light_group", "entities": ["light.hallway"], "action": "off"},
    ],
    "state_delta": [
        {"entity_id": "light.hallway", "before": "on", "after": "off"},
    ],
}

#: An attribute assertion that names its attribute under the schema's own key.
_ATTRIBUTE_BY_NAME = """\
given:
  house: minimal
when: []
then:
  - attribute:
      entity_id: sensor.living_room_lux
      name: unit_of_measurement
      is: lx
"""

#: The same assertion written with `attribute` where `name` belongs: the extra
#: key is refused, which is what pins the key's spelling.
_ATTRIBUTE_BY_WRONG_KEY = """\
given:
  house: minimal
when: []
then:
  - attribute:
      entity_id: sensor.living_room_lux
      name: unit_of_measurement
      attribute: unit_of_measurement
      is: lx
"""


@pytest.fixture(scope="module")
def vocabulary() -> Vocabulary:
    """The committed catalog, frozen, so one load serves the module."""
    return Vocabulary.load(ROOT)


@pytest.fixture(scope="module")
def surface(vocabulary: Vocabulary) -> OpenHouse:
    """A built `minimal` house: the assertions read the port's own view of it."""
    return open_session(house="minimal", vocabulary=vocabulary)


def _write(tmp_path: Path, text: str) -> Path:
    """A scenario document on disk, LF, no translation."""
    path = tmp_path / "scenario.yaml"
    path.write_text(text, encoding="utf-8", newline="")
    return path


# --------------------------------------------------------------------------
# State assertions read the house
# --------------------------------------------------------------------------


def test_a_state_assertion_passes_on_the_matching_value(surface: OpenHouse) -> None:
    """A read equal to the expectation is no mismatch.

    A falsifying implementation that compared the wrong field -- the entity id to
    the state, or the domain -- would report a mismatch here even though the
    house reads exactly what the assertion named.
    """
    entity = surface.read_entity("light.foyer")
    assert StateExpectation("light.foyer", "state", "off").evaluate(entity) is None


def test_a_state_assertion_fails_with_the_expected_and_actual_values(
    surface: OpenHouse,
) -> None:
    """A read that disagrees reports both values and what was asserted.

    A falsifying implementation that returned a bare `False`, or a message
    without the values, would leave a failure report naming neither what was
    wanted nor what was found -- so a red run would not say what to fix.
    """
    mismatch = StateExpectation("light.foyer", "state", "on").evaluate(
        surface.read_entity("light.foyer")
    )
    assert mismatch is not None
    assert mismatch.expected == "on"
    assert mismatch.actual == "off"
    assert "light.foyer" in mismatch.expectation
    assert "state" in mismatch.expectation


def test_an_availability_assertion_reads_availability_apart_from_state(
    vocabulary: Vocabulary,
) -> None:
    """An unavailable device is assertable as such without its state being read off.

    The falsifying implementation is one that folds availability into `state`:
    marking the light unavailable would then make its state read as "unavailable"
    rather than "off", and a scenario could no longer assert the fact the corpus's
    unavailable-and-return seed exists to protect -- that the device was never
    reported switched off.
    """
    session = open_session(house="minimal", vocabulary=vocabulary)
    session.set_availability("light.foyer", available=False)
    entity = session.read_entity("light.foyer")
    assert entity.available is False
    assert entity.state == "off"
    assert (
        StateExpectation("light.foyer", "availability", False).evaluate(entity) is None
    )
    assert (
        StateExpectation("light.foyer", "availability", True).evaluate(entity)
        is not None
    )
    # The two are separate fields: the state still reads "off" and is not
    # re-read as "unavailable", and an assertion on that state is not a proxy
    # for availability.
    assert StateExpectation("light.foyer", "state", "off").evaluate(entity) is None
    assert (
        StateExpectation("light.foyer", "state", "unavailable").evaluate(entity)
        is not None
    )


def test_an_attribute_assertion_reads_the_named_attribute(surface: OpenHouse) -> None:
    """The asserted field is the attribute's name, read off the entity's mapping.

    A falsifying implementation that looked the name up as a method, or read the
    state when an attribute was named, would report a mismatch for an attribute
    the house carries and reads correctly.
    """
    entity = surface.read_entity("sensor.living_room_lux")
    assert entity.attributes["unit_of_measurement"] == "lx"
    assert (
        StateExpectation(
            "sensor.living_room_lux", "unit_of_measurement", "lx"
        ).evaluate(entity)
        is None
    )
    assert (
        StateExpectation("sensor.living_room_lux", "unit_of_measurement", "W").evaluate(
            entity
        )
        is not None
    )


def test_the_loader_reads_the_attribute_name_from_the_name_key(tmp_path: Path) -> None:
    """An attribute assertion's attribute is named by `name`, and reaches `field`.

    A falsifying loader that read the key `attribute`, or that always asserted the
    state, would either refuse this document or produce an expectation whose
    `field` is not the attribute the document named.
    """
    scenario = load_scenario(_write(tmp_path, _ATTRIBUTE_BY_NAME))
    (expectation,) = scenario.then.expectations
    assert isinstance(expectation, StateExpectation)
    assert expectation.entity_id == "sensor.living_room_lux"
    assert expectation.field == "unit_of_measurement"
    assert expectation.expected == "lx"


def test_an_attribute_assertion_under_the_wrong_key_is_refused(
    tmp_path: Path,
) -> None:
    """`attribute` is not a key of an attribute assertion; `name` is.

    A falsifying loader that accepted `attribute` as a synonym would make two
    spellings for one field, and a scenario written with the wrong one would
    assert an attribute nobody declared.
    """
    with pytest.raises(LoadError) as info:
        load_scenario(_write(tmp_path, _ATTRIBUTE_BY_WRONG_KEY))
    assert "attribute" in str(info.value)


# --------------------------------------------------------------------------
# Log assertions make the log the oracle
# --------------------------------------------------------------------------


def test_a_must_matcher_fails_when_no_record_matches() -> None:
    """`must` holds only where a record satisfies it, and reports what it wanted.

    A falsifying implementation that treated an empty log as vacuously satisfying
    any `must` would make the strongest positive assertion pass against a run in
    which nothing happened.
    """
    expectation = LogExpectation(must=({"outcome": "acted"},))
    mismatch = expectation.evaluate(())
    assert mismatch is not None
    assert mismatch.expected == {"outcome": "acted"}
    assert expectation.evaluate((_ACTED,)) is None
    assert (
        LogExpectation(must=({"outcome": "declined"},)).evaluate((_ACTED,)) is not None
    )


def test_a_must_not_matcher_fails_when_a_record_matches() -> None:
    """`must_not` is what makes an absence assertable, and fails when it is present.

    A falsifying implementation that dropped `must_not`, or that read it as a
    `must`, would make "no switched-off record was produced" -- the corpus's
    unavailable-and-return assertion -- either unexpressible or exactly inverted.
    """
    expectation = LogExpectation(must_not=({"outcome": "acted"},))
    assert expectation.evaluate(()) is None
    mismatch = expectation.evaluate((_ACTED,))
    assert mismatch is not None
    assert mismatch.actual == _ACTED
    assert "no record matching" in mismatch.expectation


def test_a_because_citation_fails_when_no_record_satisfies_it() -> None:
    """A `because` holds only for a record matching its rule *and* its outcome.

    A falsifying implementation that matched a citation on either field alone
    would let a record that reached the rule and declined satisfy a claim that
    the timeout *acted*, which is exactly the acted-versus-never-ran distinction
    the log oracle exists to keep.
    """
    expectation = LogExpectation(
        because={"rule": "lighting.motion_light_off", "outcome": "acted"}
    )
    assert expectation.evaluate((_ACTED,)) is None
    declined = {"rule": "lighting.motion_light_off", "outcome": "declined"}
    mismatch = expectation.evaluate((declined,))
    assert mismatch is not None
    assert "because" in mismatch.expectation


def test_matches_contains_on_inputs_and_compares_the_rest_exactly() -> None:
    """Three list fields match by containment; every other field by equality.

    A falsifying implementation that matched `inputs` by position, or by equality
    on the whole list, would make a matcher naming one consulted input depend on
    the order an evaluation happened to read the others -- which is not part of
    what a record claims.
    """
    record = {
        "rule": "lighting.motion_light_off",
        "outcome": "acted",
        "inputs": [
            {"kind": "read", "slot": "light_group"},
            {"kind": "setting", "key": "quiet_timeout", "layer": "builtin"},
        ],
    }
    assert matches(record, {"inputs": [{"slot": "light_group"}]})
    assert matches(record, {"inputs": [{"kind": "setting"}, {"kind": "read"}]})
    assert not matches(record, {"inputs": [{"slot": "ambient_light_sensor"}]})
    assert matches(record, {"outcome": "acted"})
    assert not matches(record, {"outcome": "declined"})
    assert not matches(record, {"actor": "away_shutdown"})
    assert not matches(record, {"rule": None})


def test_a_null_rule_is_compared_by_equality() -> None:
    """`rule: null` matches a record that reached no rule, and no other.

    A falsifying implementation that skipped a `None` matcher as "unconstrained"
    would make a citation of a record that matched no rule indistinguishable
    from a citation of any record at all.
    """
    assert matches({"rule": None}, {"rule": None})
    assert not matches({"rule": "lighting.motion_light_off"}, {"rule": None})
    assert not matches({"rule": None}, {"rule": "lighting.motion_light_off"})


def test_an_assertion_on_an_entity_the_house_lacks_is_a_mismatch(
    vocabulary: Vocabulary,
) -> None:
    """A read the port refuses becomes a mismatch naming the port's reason.

    The falsifying implementation is one that lets the port's exception travel
    through the runner: the scenario named an entity the house does not hold, so
    the failure should be the assertion's own mismatch naming that reason, not a
    traceback about a device the scenario invented.
    """
    session = open_session(house="minimal", vocabulary=vocabulary)
    scenario = Scenario(
        name="an assertion naming a missing entity",
        path="<memory>",
        given=Given(house="minimal"),
        when=(),
        then=Then((StateExpectation("light.nowhere", "state", "off"),)),
    )
    with pytest.raises(ScenarioFailed) as info:
        run_scenario(scenario, session)
    report = info.value.report
    assert report.assertion == 0
    assert report.step is None
    assert report.expected == "off"
    assert isinstance(report.actual, str)
    assert report.actual.startswith("unreadable:")
    assert "light.nowhere" in report.actual
    assert "light.nowhere" in report.expectation
