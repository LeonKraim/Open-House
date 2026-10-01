"""The shared result envelope: one document, reported by both faces.

`control-surface`'s "one schema seen twice" is written about a tool's *inputs*.
`openhouse/results.py` is the other half of the same rule -- what a call *gives
back* -- and it exists so the CLI's `--json` and the MCP server's `TextContent`
are the same document built once rather than two renderings that agree today.

What is checked here is that the envelope is one shape and that `jsonable`
normalises each kind of result without inspecting what it means. Each test names
the implementation that would falsify it.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType
from typing import cast

import pytest

from engine.adapter import ChangeOrigin, EntityView
from engine.vocabulary import Vocabulary
from openhouse.facade import open_session
from openhouse.results import jsonable, result_document
from openhouse.scenarios import run_file
from sim.scenario import RunResult

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def vocabulary() -> Vocabulary:
    """The committed catalog, frozen for the module."""
    return Vocabulary.load(ROOT)


@dataclass(frozen=True, slots=True)
class _Documented:
    """A result that knows its own document form."""

    value: int

    def to_document(self) -> dict[str, object]:
        return {"kind": "documented", "value": self.value}


def _dump(value: object) -> str:
    """The document as JSON text, which is what a face has to be able to write."""
    return json.dumps(value, sort_keys=True)


def test_the_envelope_names_the_operation_and_carries_the_result() -> None:
    """`result_document` is exactly `{"operation": name, "result": ...}`.

    Falsified by an envelope that nested the result under the operation name, or
    that added a field: both faces would then report a document no other caller
    of that operation would recognise.
    """
    assert result_document("snapshot", {"a": 1}) == {
        "operation": "snapshot",
        "result": {"a": 1},
    }


def test_the_envelope_applies_jsonable_to_its_result() -> None:
    """A result that is not JSON-safe is normalised inside the envelope.

    The envelope is what a transport writes, so a caller that had to normalise
    the result first would be able to skip it -- and the CLI and the MCP server
    would each have to remember to.
    """
    document = result_document("get_decision_log", (_Documented(1), _Documented(2)))
    assert document["result"] == [
        {"kind": "documented", "value": 1},
        {"kind": "documented", "value": 2},
    ]
    assert _dump(document)


def test_a_result_that_knows_its_document_is_asked_for_it() -> None:
    """`to_document` wins over the dataclass's own fields.

    Falsified by a `jsonable` that converted every dataclass field-by-field:
    `RunResult` would then report `final_snapshot` as a raw mapping and `log` as
    a tuple of records rather than the documents the class defines.
    """
    assert jsonable(_Documented(7)) == {"kind": "documented", "value": 7}


def test_a_run_result_reports_its_own_document_through_the_envelope(
    vocabulary: Vocabulary,
) -> None:
    """The one result the runner produces round-trips through the envelope.

    This is the claim the module exists for: `scenario run --json` and the
    `run_scenario` tool report the same `RunResult.to_document()`, so a CI job
    and an agent read one document.
    """
    run = run_file(
        ROOT / "scenarios" / "05-hallway-light-watchdog.yaml", vocabulary=vocabulary
    )
    document = result_document("run_scenario", run)
    assert document["operation"] == "run_scenario"
    assert document["result"] == run.to_document()
    assert _dump(document)


def test_a_dataclass_is_converted_field_by_field() -> None:
    """A dataclass becomes its fields, without `dataclasses.asdict`.

    `asdict` deep-copies, and an `EntityView` holds a read-only mapping proxy:
    copying one raises `TypeError` for a reason that has nothing to do with this
    face. Falsified by implementing `jsonable` with `asdict`.
    """
    view = EntityView(
        entity_id="light.foyer",
        state="on",
        attributes=MappingProxyType({"brightness": 128}),
        available=True,
        last_origin=ChangeOrigin.WORLD,
    )
    assert jsonable(view) == {
        "entity_id": "light.foyer",
        "state": "on",
        "attributes": {"brightness": 128},
        "available": True,
        "last_origin": "world",
    }


def test_a_read_through_the_port_is_json_safe_through_the_envelope(
    vocabulary: Vocabulary,
) -> None:
    """A real `read_entity` reaches a written document unchanged.

    The check above builds the view by hand; this one reads it from a session,
    which is where a proxy actually comes from.
    """
    session = open_session(house="minimal", vocabulary=vocabulary)
    document = result_document("set_state", session.set_state("light.foyer", "on"))
    result = cast("Mapping[str, object]", document["result"])
    assert result["entity_id"] == "light.foyer"
    assert result["state"] == "on"
    assert _dump(document)


def test_a_tuple_and_a_list_normalise_to_the_same_json() -> None:
    """A tuple is a document's array, not a string.

    Falsified by a `jsonable` that left a tuple alone: `json.dumps` writes it as
    an array anyway, but a caller comparing the value in Python -- which both
    faces do before rendering -- would see a tuple where the other saw a list.
    """
    assert jsonable((1, 2)) == [1, 2]
    assert jsonable([1, 2]) == [1, 2]


def test_a_scalar_passes_through_unchanged() -> None:
    """Nothing is stringified that JSON already writes.

    A `jsonable` that called `str` on everything would report every number as
    text, and a caller that read a count back would have to parse it.
    """
    for value in ("text", 1, 2.5, True, False, None):
        assert jsonable(value) == value


def test_a_set_is_sorted_so_two_documents_compare_equal() -> None:
    """A set is written as a sorted list, not in hash order.

    Falsified by leaving the set to `plain`, which sorts by `repr` -- the
    ordering is the point, because two runs that produced the same members must
    produce the same document.
    """
    assert jsonable({"b", "a", "c"}) == ["a", "b", "c"]


def test_an_instant_is_written_as_its_isoform() -> None:
    """A `datetime` becomes a string rather than reaching the transport.

    `json.dumps` refuses a `datetime` outright, so a result carrying one would
    make the face fail on a document the other face had already written.
    """
    moment = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    assert jsonable(moment) == "2026-01-01T12:00:00+00:00"


def test_an_unknown_object_is_stringified_rather_than_refused() -> None:
    """A result the envelope does not know is written, not raised on.

    A face that raised here would fail a call that had already succeeded, which
    is the worst place for a rendering problem to surface. The cost -- an object
    reported as its `repr` -- is the one the plan chose deliberately.
    """

    class _Opaque:
        def __repr__(self) -> str:
            return "<opaque>"

    assert jsonable(_Opaque()) == "<opaque>"


def test_a_mapping_keeps_its_keys_as_strings() -> None:
    """A non-string key becomes a string rather than failing the transport.

    JSON object keys are strings; a document written with an integer key would
    be refused by `json.dumps` with `skipkeys` off, which is how both faces
    write.
    """
    assert jsonable({1: "a"}) == {"1": "a"}


def test_the_envelope_of_a_none_result_is_still_a_document() -> None:
    """An operation that returns nothing still reports a document.

    `set_availability` and `restart` return `None`; a face that omitted the
    `result` key for one of them would make a client that read `document
    ["result"]` fail on exactly the operations that had nothing to say.
    """
    assert result_document("restart", None) == {"operation": "restart", "result": None}
    assert _dump(result_document("restart", None))


def test_a_run_result_and_its_envelope_differ_only_by_the_wrapper(
    vocabulary: Vocabulary,
) -> None:
    """The envelope adds a key and changes nothing else.

    This is the equality the two faces rely on: the MCP server writes the
    envelope as its text, and the CLI's `--json` writes the same object. A
    `jsonable` that reshaped the result would make one face's document a
    superset of the other's.
    """
    run = run_file(
        ROOT / "scenarios" / "05-hallway-light-watchdog.yaml", vocabulary=vocabulary
    )
    assert set(result_document("run_scenario", run)) == {"operation", "result"}
    assert result_document("run_scenario", run)["result"] == jsonable(run)


def test_jsonable_of_a_run_result_is_json_writable(vocabulary: Vocabulary) -> None:
    """Every part of a real run reaches JSON.

    The log's records carry tuples and the snapshot carries proxies; a
    normaliser that missed one would fail here rather than at a caller's
    transport.
    """
    run = run_file(
        ROOT / "scenarios" / "05-hallway-light-watchdog.yaml", vocabulary=vocabulary
    )
    assert isinstance(run, RunResult)
    written = json.loads(json.dumps(jsonable(run), sort_keys=True))
    assert written["name"] == run.name
    assert written["seed"] == run.seed
    assert isinstance(written["log"], list)
