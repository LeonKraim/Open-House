"""The live export, its preview, its apply, and the Activity projection.

Two properties are asserted here and each has a test that would fail if the
operation were a no-op. The first is that export, preview and apply read and
write the *engine's* house: every assertion about a configuration goes through
`session.engine`, because a session that changed its own fields without
rebuilding would pass a check on `session.rooms` and hand the panel a house the
engine knows nothing about. The second is that the outcome mapping is the whole
of the engine's closed set: the walk below visits every member of `Outcome`, so
a ninth outcome added later fails the suite rather than vanishing from the
Activity tab.
"""

from __future__ import annotations

import ast
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest

from engine.adapter import ChangeContext
from engine.binding import Reduction
from engine.decision_log import (
    DecisionRecord,
    ModeReading,
    Outcome,
    ProposedCommand,
    SlotRead,
    StateChange,
)
from engine.export import validate_export
from engine.solar import Location
from ha_adapter import live_export
from ha_adapter.composition import LiveRoom, room_id
from ha_adapter.live import LiveSession, LiveSessionError
from ha_adapter.testing import FakeHaTransport
from tools.catalog import schemas as catalog_schemas

ROOT = Path(__file__).resolve().parents[1]

LOCATION = Location(latitude=51.5, longitude=-0.1, time_zone="Europe/London")

#: The panel's own union, copied from `panel/src/api/models.ts` so a change to
#: the panel's vocabulary fails here rather than quietly widening the mapping.
PANEL_OUTCOME_VALUES = ("applied", "skipped", "overridden", "blocked", "error")

#: The exact keys `panel/src/api/models.ts` gives each response. Asserted as a
#: set rather than field by field, so a key this module invented, dropped or
#: misspelled fails the same assertion.
DECISION_LOG_ENTRY_KEYS = {
    "id",
    "at",
    "room",
    "behaviour",
    "entity_id",
    "action",
    "reason",
    "priority",
    "outcome",
}
IMPORT_DIFF_ROW_KEYS = {"kind", "scope", "path", "before", "after"}
RELINK_REQUEST_KEYS = {
    "room_id",
    "slot",
    "registry_id",
    "entity_id",
    "candidates",
}
BINDING_SUGGESTION_KEYS = {
    "entity_id",
    "registry_id",
    "friendly_name",
    "domain",
    "score",
}

HALL_MOTION = "binary_sensor.hall_motion"
HALL_LUX = "sensor.hall_lux"
HALL_LIGHT = "light.hall"


def _transport() -> FakeHaTransport:
    transport = FakeHaTransport()
    transport.set_state(HALL_MOTION, "off", attributes={"friendly_name": "Hall motion"})
    transport.set_state(HALL_LUX, "12", attributes={"friendly_name": "Hall lux"})
    transport.set_state(HALL_LIGHT, "off", attributes={"friendly_name": "Hall light"})
    return transport


def _hall() -> LiveRoom:
    return LiveRoom(
        id=room_id("hall"),
        name="Hall",
        type="hallway",
        bindings={
            "motion_sensor": HALL_MOTION,
            "lux_sensor": HALL_LUX,
            "light_group": HALL_LIGHT,
        },
    )


def _session() -> LiveSession:
    return LiveSession.build(
        house_name="Test House",
        rooms=(_hall(),),
        modes=("Home", "Away"),
        transport=_transport(),
        root=ROOT,
        location=LOCATION,
        clock=_Clock(),
    )


class _Clock:
    """A clock that does not move, so an export's `exported_at` is assertable."""

    @property
    def now(self) -> datetime:
        return datetime(2026, 1, 1, 9, 0, tzinfo=UTC)


def _document(
    rooms: tuple[Mapping[str, object], ...] | None = None,
    *,
    house: str = "Imported House",
    profiles: tuple[Mapping[str, object], ...] = (),
    active: Mapping[str, object] | None = None,
    format_version: str = "1.1.0",
) -> dict[str, object]:
    """An export document, in the frozen schema's shape.

    `house_scope` is deliberately absent: the live composition declares the
    vocabulary's whole house-slot list on every rebuild, so a document cannot
    narrow it and a test that wrote one would be asserting against a field the
    live path reads and ignores.
    """
    return {
        "format_version": format_version,
        "house": house,
        "rooms": list(rooms if rooms is not None else (_kitchen(),)),
        "profiles": list(profiles),
        "active": (
            {"rooms": {}, "house_profile": None} if active is None else dict(active)
        ),
    }


def _kitchen() -> Mapping[str, object]:
    return {
        "id": "kitchen",
        "name": "Kitchen",
        "type": "kitchen",
        "bindings": {
            "light_group": {
                "registry_id": "kitchen_ceiling",
                "entity_id": "light.kitchen_ceiling",
            },
            "motion_sensor": {
                "registry_id": "kitchen_motion",
                "entity_id": "binary_sensor.kitchen_motion",
            },
        },
    }


# --------------------------------------------------------------------------
# Export
# --------------------------------------------------------------------------


def test_the_export_is_the_frozen_export_document() -> None:
    """The one shape a backup has, checked against the schema rather than by eye."""
    document = live_export.export_document(_session())
    current = catalog_schemas.current_version(
        catalog_schemas.load_versions("export-document")
    )
    assert current is not None
    validate_export(document, cast("Mapping[str, object]", current.document))
    assert document["format_version"] == current.version
    assert document["house"] == "Test House"
    assert document["exported_at"] == "2026-01-01T09:00:00+00:00"


def test_the_export_describes_the_house_the_engine_is_deciding_for() -> None:
    """Read back through `session.engine`, not through the session's own rooms."""
    session = _session()
    bindings = cast("Mapping[str, object]", live_export.export_document(session))[
        "rooms"
    ]
    rooms = cast("list[Mapping[str, object]]", bindings)
    assert [room["id"] for room in rooms] == [
        room.id for room in session.engine.house.rooms
    ]
    assert rooms[0]["bindings"] == {
        "light_group": {"registry_id": "hall", "entity_id": HALL_LIGHT},
        "lux_sensor": {"registry_id": "hall_lux", "entity_id": HALL_LUX},
        "motion_sensor": {"registry_id": "hall_motion", "entity_id": HALL_MOTION},
    }


def test_a_registry_id_from_the_caller_is_written_beside_the_entity() -> None:
    """Keyed by entity id, which is the handle Home Assistant's registry answers."""
    document = live_export.export_document(
        _session(), registry_ids={HALL_LIGHT: "device-registry-7"}
    )
    rooms = cast("list[Mapping[str, object]]", document["rooms"])
    written = cast("Mapping[str, object]", rooms[0]["bindings"])["light_group"]
    assert written == {
        "registry_id": "device-registry-7",
        "entity_id": HALL_LIGHT,
    }


def test_the_export_carries_the_modes_the_session_gates_on() -> None:
    """The labels a person reads are projected to the names the engine gates on."""
    document = live_export.export_document(_session())
    modes = cast("list[Mapping[str, object]]", document["modes"])
    assert [mode["name"] for mode in modes] == ["home", "away"]


def test_an_entity_with_no_registry_id_keeps_the_derived_one() -> None:
    document = live_export.export_document(_session(), registry_ids={})
    rooms = cast("list[Mapping[str, object]]", document["rooms"])
    written = cast("Mapping[str, object]", rooms[0]["bindings"])["lux_sensor"]
    assert cast("Mapping[str, object]", written)["registry_id"] == "hall_lux"


# --------------------------------------------------------------------------
# Preview
# --------------------------------------------------------------------------


def test_previewing_the_sessions_own_export_would_change_nothing() -> None:
    session = _session()
    preview = live_export.preview(session, live_export.export_document(session))
    assert preview["compatible"] is True
    assert preview["diff"] == ()
    assert preview["relink"] == ()
    assert "Nothing would change." in cast("tuple[str, ...]", preview["notes"])


def test_preview_changes_nothing() -> None:
    session = _session()
    before = session.to_state()
    live_export.preview(session, _document())
    assert session.to_state() == before


def test_preview_reports_a_change_with_the_panels_field_names() -> None:
    preview = live_export.preview(_session(), _document())
    rows = cast("tuple[Mapping[str, object], ...]", preview["diff"])
    assert rows, "importing another house has to differ from this one"
    for row in rows:
        assert set(row) == IMPORT_DIFF_ROW_KEYS
        assert row["kind"] in {"add", "change", "remove"}
        assert isinstance(row["scope"], str)
        assert isinstance(row["path"], str)
    assert {row["kind"] for row in rows} >= {"change"}


def test_a_document_from_a_later_build_is_refused_with_a_reason() -> None:
    preview = live_export.preview(_session(), _document(format_version="9.9.9"))
    assert preview["compatible"] is False
    assert preview["diff"] == ()
    notes = cast("tuple[str, ...]", preview["notes"])
    assert len(notes) == 1
    assert "9.9.9" in notes[0]


def test_a_document_that_is_not_an_export_at_all_is_refused_with_a_reason() -> None:
    preview = live_export.preview(_session(), {"hello": "world"})
    assert preview["compatible"] is False
    notes = cast("tuple[str, ...]", preview["notes"])
    assert "format_version" in notes[0]


def test_a_rooms_bindings_this_house_cannot_resolve_are_asked_to_be_relinked() -> None:
    preview = live_export.preview(_session(), _document())
    rows = cast("tuple[Mapping[str, object], ...]", preview["relink"])
    assert rows, "the kitchen's registry ids are ones this house has never held"
    for row in rows:
        assert set(row) == RELINK_REQUEST_KEYS
        assert row["room_id"] == "kitchen"
        assert row["entity_id"] is None
        assert isinstance(row["registry_id"], str)
        for candidate in cast("tuple[Mapping[str, object], ...]", row["candidates"]):
            assert set(candidate) == BINDING_SUGGESTION_KEYS
    assert "need re-linking" in " ".join(cast("tuple[str, ...]", preview["notes"]))


def test_a_binding_whose_registry_id_this_house_knows_needs_no_relinking() -> None:
    """The registry id is the identity, and the entity id is what a re-pair moves."""
    session = _session()
    document = dict(live_export.export_document(session))
    rooms = [
        dict(room) for room in cast("list[Mapping[str, object]]", document["rooms"])
    ]
    bindings = dict(cast("Mapping[str, object]", rooms[0]["bindings"]))
    bindings["light_group"] = {"registry_id": "hall", "entity_id": "light.hall_again"}
    rooms[0]["bindings"] = bindings
    document["rooms"] = rooms

    preview = live_export.preview(session, document)
    assert preview["compatible"] is True
    assert preview["relink"] == ()
    assert preview["diff"], "the entity id did move, so the diff is not empty"


def test_a_file_naming_another_house_says_the_name_is_not_imported() -> None:
    notes = cast(
        "tuple[str, ...]",
        live_export.preview(_session(), _document(house="Ada's house"))["notes"],
    )
    assert any("Ada's house" in note for note in notes)


def _legacy_document() -> dict[str, object]:
    """A 1.0.0 export: a flat binding list, the shape before rooms existed."""
    return {
        "format_version": "1.0.0",
        "house": "Legacy House",
        "bindings": [
            {
                "room": "hall",
                "slot": "light_group",
                "registry_id": "hall",
                "entity_id": "light.hall",
            }
        ],
    }


def test_a_file_from_an_older_build_says_the_version_it_would_be_imported_as() -> None:
    """The note names the version the *file* declares, not the migrated copy.

    The migration has already restamped the document it returns, so a note read
    off that copy could only ever say the current version and would never fire.
    """
    preview = live_export.preview(_session(), _legacy_document())
    assert preview["compatible"] is True
    notes = cast("tuple[str, ...]", preview["notes"])
    assert any("1.0.0" in note and "1.1.0" in note for note in notes)


def test_apply_reads_a_file_from_an_older_build() -> None:
    session = _session()
    assert live_export.apply(session, _legacy_document())["applied"] is True
    assert session.engine.house.room("hall").name == "Hall"


# --------------------------------------------------------------------------
# Apply
# --------------------------------------------------------------------------


def test_apply_replaces_the_configuration_the_engine_decides_for() -> None:
    session = _session()
    result = live_export.apply(session, _document())
    assert result["applied"] is True

    house = session.engine.house
    assert [room.id for room in house.rooms] == ["kitchen"]
    assert house.room("kitchen").bindings["light_group"] == "light.kitchen_ceiling"
    assert session.room("hall") is None


def test_apply_keeps_the_rooms_the_document_also_names() -> None:
    """A room the document carries is a room the house keeps, unchanged."""
    session = _session()
    rooms = list(
        cast(
            "list[Mapping[str, object]]", live_export.export_document(session)["rooms"]
        )
    )
    rooms.append(_kitchen())
    live_export.apply(session, _document(rooms=tuple(rooms)))
    house = session.engine.house
    assert house.room("hall").bindings["light_group"] == HALL_LIGHT
    assert (
        house.room("kitchen").bindings["motion_sensor"]
        == "binary_sensor.kitchen_motion"
    )


def test_apply_restores_the_profiles_and_the_selections() -> None:
    session = _session()
    document = _document(
        profiles=(
            {
                "name": "bright",
                "kind": "room",
                "description": "Brighter than usual.",
                "axis": "lighting",
                "deltas": {},
            },
        ),
        active={"rooms": {"kitchen": {"lighting": "bright"}}, "house_profile": None},
    )
    live_export.apply(session, document)
    assert session.profiles.selections() == {"kitchen": {"lighting": "bright"}}
    assert "bright" in session.profiles.profiles


def test_apply_restores_the_modes_the_document_declares() -> None:
    session = _session()
    document = _document()
    document["modes"] = [
        {
            "name": "guest",
            "description": "A guest is staying.",
            "exclusive_group": "presence",
        }
    ]
    live_export.apply(session, document)
    assert session.modes == ("guest",)
    session.set_house_mode("guest")
    assert session.engine.modes.active == frozenset({"guest"})


def test_apply_leaves_the_house_name_where_home_assistant_keeps_it() -> None:
    session = _session()
    live_export.apply(session, _document(house="Ada's house"))
    assert session.engine.house.name == "Test House"


def test_apply_does_not_invent_the_devices_a_document_names() -> None:
    """A live house does not own its entities: a backup must not conjure one."""
    session = _session()
    live_export.apply(session, _document())
    transport = session.transport
    assert isinstance(transport, FakeHaTransport)
    assert transport.state("light.kitchen_ceiling") is None


def test_apply_refuses_an_incompatible_document_by_name() -> None:
    with pytest.raises(LiveSessionError, match="cannot be imported"):
        live_export.apply(_session(), _document(format_version="9.9.9"))


def test_apply_reports_the_diff_it_applied() -> None:
    session = _session()
    expected = live_export.preview(session, _document())["diff"]
    result = live_export.apply(session, _document())
    assert set(result) == {"applied", "snapshot_id", "diff"}
    assert result["diff"] == expected
    assert isinstance(result["snapshot_id"], str)


def test_apply_refuses_before_it_touches_anything() -> None:
    session = _session()
    before = session.to_state()
    with pytest.raises(LiveSessionError):
        live_export.apply(session, {"hello": "world"})
    assert session.to_state() == before


# --------------------------------------------------------------------------
# Activity: the eight-to-five mapping
# --------------------------------------------------------------------------


def _record(
    outcome: Outcome,
    *,
    actor: str = "motion_lighting",
    rule: str | None = "motion.light_on",
    inputs: tuple[object, ...] = (),
    commands: tuple[ProposedCommand, ...] = (),
    state_delta: tuple[StateChange, ...] = (),
    at: datetime | None = None,
    seconds: int = 0,
) -> DecisionRecord:
    return DecisionRecord(
        at=datetime(2026, 1, 1, 12, seconds, tzinfo=UTC) if at is None else at,
        actor=actor,
        inputs=cast("tuple", inputs),
        rule=rule,
        commands=commands,
        outcome=outcome,
        state_delta=state_delta,
    )


def test_every_engine_outcome_maps_to_a_panel_outcome() -> None:
    """The walk that makes a ninth outcome a failure rather than a silently dropped row.

    Every member of the closed set goes through the public projection, so a
    member added to `Outcome` and forgotten here raises out of
    `activity_entry` -- a `LiveSessionError` naming the outcome -- instead of
    appearing in the Activity tab as nothing at all.
    """
    for outcome in Outcome:
        entry = live_export.activity_entry(_record(outcome))
        assert entry["outcome"] in PANEL_OUTCOME_VALUES, outcome


def test_the_mapping_covers_exactly_the_engines_outcomes() -> None:
    assert set(live_export._PANEL_OUTCOME) == set(Outcome)


def test_every_panel_outcome_the_table_produces_is_one_the_panel_declares() -> None:
    assert set(live_export._PANEL_OUTCOME.values()) <= set(PANEL_OUTCOME_VALUES)


def test_the_table_uses_the_whole_panel_vocabulary() -> None:
    """All five, so no bucket the panel offers is dead code behind this mapping."""
    assert set(live_export._PANEL_OUTCOME.values()) == set(PANEL_OUTCOME_VALUES)


def test_an_outcome_with_no_row_fails_instead_of_defaulting() -> None:
    with pytest.raises(LiveSessionError, match="no panel outcome"):
        live_export._panel_outcome("a ninth outcome")


def test_an_outcome_with_no_disposition_sentence_fails_instead_of_defaulting() -> None:
    with pytest.raises(LiveSessionError, match="no disposition sentence"):
        live_export._disposition("a ninth outcome")


# --------------------------------------------------------------------------
# Activity: the entry
# --------------------------------------------------------------------------


def test_an_entry_has_the_field_names_the_panel_declares() -> None:
    entry = live_export.activity_entry(_record(Outcome.DECLINED))
    assert set(entry) == DECISION_LOG_ENTRY_KEYS
    assert entry["behaviour"] == "motion_lighting"
    assert entry["priority"] is None
    assert isinstance(entry["id"], str) and entry["id"]


def test_the_reason_is_plain_language_built_from_the_records_own_fields() -> None:
    record = _record(
        Outcome.ACTED,
        inputs=(
            SlotRead(
                slot="lux_sensor",
                entities=(HALL_LUX,),
                reduction=Reduction.ANY,
            ),
            ModeReading(mode="home", active=True),
        ),
        commands=(
            ProposedCommand(
                slot="light_group",
                entities=(HALL_LIGHT,),
                action="on",
                context=ChangeContext.engine(),
            ),
        ),
        state_delta=(StateChange(entity_id=HALL_LIGHT, before="off", after="on"),),
    )
    entry = live_export.activity_entry(record)
    reason = cast("str", entry["reason"])
    assert reason.startswith("motion_lighting acted.")
    assert "motion.light_on" in reason
    assert "lux_sensor" in reason
    assert HALL_LUX in reason
    assert "The home mode was active." in reason
    assert f"It changed {HALL_LIGHT} from 'off' to 'on'." in reason
    assert entry["entity_id"] == HALL_LIGHT
    assert entry["action"] == "on"


def test_the_reason_names_what_was_proposed_but_not_applied() -> None:
    record = _record(
        Outcome.LOST_ARBITRATION,
        commands=(
            ProposedCommand(
                slot="light_group",
                entities=(HALL_LIGHT,),
                action="on",
                context=ChangeContext.engine(),
            ),
        ),
    )
    reason = cast("str", live_export.activity_entry(record)["reason"])
    assert "lost arbitration" in reason
    assert "which was not applied" in reason


def test_the_id_of_a_record_is_stable_and_two_rooms_do_not_collide() -> None:
    """A digest of what the record carries, not a position in a bounded log."""
    first = _record(Outcome.ACTED, actor="motion_lighting")
    same = _record(Outcome.ACTED, actor="motion_lighting")
    other = _record(Outcome.ACTED, actor="override")
    assert (
        live_export.activity_entry(first)["id"]
        == live_export.activity_entry(same)["id"]
    )
    assert (
        live_export.activity_entry(first)["id"]
        != live_export.activity_entry(other)["id"]
    )


# --------------------------------------------------------------------------
# Activity: the list
# --------------------------------------------------------------------------


def _logged(outcomes: tuple[Outcome, ...]) -> LiveSession:
    session = _session()
    for index, outcome in enumerate(outcomes):
        session.engine.log.append(_record(outcome, seconds=index))
    return session


def test_activity_is_newest_first_and_bounded_by_the_limit() -> None:
    session = _logged((Outcome.DECLINED, Outcome.ACTED, Outcome.RATE_LIMITED))
    entries = live_export.activity(session, limit=2)
    assert [entry["outcome"] for entry in entries] == ["blocked", "applied"]


def test_activity_reads_the_engines_own_log() -> None:
    session = _session()
    assert live_export.activity(session) == ()
    session.engine.log.append(_record(Outcome.ACTED))
    assert len(live_export.activity(session)) == 1


def test_a_cursor_returns_what_is_strictly_older_than_it() -> None:
    session = _logged((Outcome.DECLINED, Outcome.ACTED, Outcome.RATE_LIMITED))
    newest = live_export.activity(session, limit=1)[0]
    older = live_export.activity(session, limit=3, before=cast("str", newest["id"]))
    assert [entry["outcome"] for entry in older] == ["applied", "skipped"]
    assert cast("str", newest["id"]) not in {entry["id"] for entry in older}


def test_a_cursor_the_window_does_not_hold_returns_the_whole_window() -> None:
    session = _logged((Outcome.DECLINED, Outcome.ACTED))
    entries = live_export.activity(session, limit=2, before="not-a-cursor")
    assert [entry["outcome"] for entry in entries] == ["applied", "skipped"]


def test_a_negative_limit_is_refused() -> None:
    with pytest.raises(LiveSessionError, match="cannot be negative"):
        live_export.activity(_session(), limit=-1)


def test_the_list_is_the_same_projection_the_subscription_would_push() -> None:
    """`activity` and the websocket subscription share one projection of a record."""
    session = _logged((Outcome.OVERRIDDEN,))
    record = session.engine.log.window(1)[0]
    assert live_export.activity(session) == (live_export.activity_entry(record),)


def test_a_mode_gate_appears_in_the_reason_as_the_record_wrote_it() -> None:
    record = _record(
        Outcome.DECLINED,
        inputs=(ModeReading(mode="away", active=False),),
        commands=(),
    )
    reason = cast("str", live_export.activity_entry(record)["reason"])
    assert "decided that nothing needed doing" in reason
    assert "The away mode was not active." in reason


def test_the_module_imports_no_home_assistant() -> None:
    """`ha_adapter/**` must not import Home Assistant, prose about it included.

    Parsed rather than grepped, because this module's docstring *names* Home
    Assistant three times while importing nothing from it -- which is the point
    of the seam, and a substring check would forbid saying so.
    """
    tree = ast.parse(Path(live_export.__file__).read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert "homeassistant" not in imported
