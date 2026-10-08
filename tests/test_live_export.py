"""The Activity projection: the engine's decisions in the panel's words.

One property is asserted here and it has a test that would fail if the
projection were a no-op: the outcome mapping is the *whole* of the engine's
closed set. The walk below visits every member of `Outcome`, so a tenth outcome
added later fails the suite rather than vanishing from the Activity tab. The
rest pins the row's shape -- the keys `panel/src/api/models.ts` declares -- and
the reason sentence built from the record's own fields.
"""

from __future__ import annotations

import ast
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
from engine.solar import Location
from ha_adapter import live_export
from ha_adapter.composition import LiveRoom, room_id
from ha_adapter.live import LiveSession, LiveSessionError
from ha_adapter.testing import FakeHaTransport

ROOT = Path(__file__).resolve().parents[1]

LOCATION = Location(latitude=51.5, longitude=-0.1, time_zone="Europe/London")

#: The panel's own union, copied from `panel/src/api/models.ts` so a change to
#: the panel's vocabulary fails here rather than quietly widening the mapping.
PANEL_OUTCOME_VALUES = ("applied", "skipped", "overridden", "blocked", "error")

#: The exact keys `panel/src/api/models.ts` gives the row. Asserted as a set
#: rather than field by field, so a key this module invented, dropped or
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
            "ambient_light_sensor": HALL_LUX,
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


# --------------------------------------------------------------------------
# Activity: the nine-to-five mapping
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
    """The walk that makes a tenth outcome a failure rather than a silently dropped row.

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
        live_export._panel_outcome("a tenth outcome")


def test_an_outcome_with_no_disposition_sentence_fails_instead_of_defaulting() -> None:
    with pytest.raises(LiveSessionError, match="no disposition sentence"):
        live_export._disposition("a tenth outcome")


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
                slot="ambient_light_sensor",
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
    assert "ambient_light_sensor" in reason
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
