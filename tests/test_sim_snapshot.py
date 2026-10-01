"""Snapshot and restore -- task 3.4.

The snapshot's whole job is that a restored run decides the way the run it
interrupted would have. That is only true if the snapshot carries *every* piece
of runtime state, so the tests here are written the way the property is: a
decision function whose output depends on each enumerated field, an
interruption-and-resume that must reproduce the uninterrupted run, and a
*falling* fixture per field showing that dropping that field changes a decision.
A test that only round-tripped a document would pass against a snapshot that
carried nothing but a version string, so every admitting case is paired with the
omission it must reject.

The decision function stands in for the engine, which task 3.4 lands before. It
reads the clock, draws from the stream, reads the house, and reads the engine
half of the state, so a loss in any of the three owners shows up as a diverging
decision -- which is the engine-level property `scenario-runner` restates as a
Hypothesis invariant in task 9.4.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from engine.adapter import ChangeContext, ChangeOrigin, Fault
from sim.snapshot import (
    ENGINE_STATE_FIELDS,
    SNAPSHOT_VERSION,
    IncompleteSnapshotError,
    Simulation,
    Snapshot,
    UnsupportedSnapshotVersionError,
    restore_snapshot,
    take_snapshot,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

_START = datetime(2026, 1, 1, tzinfo=UTC)


def _engine_state(
    modes: Sequence[str] = ("home",),
    enabled: Mapping[str, bool] | None = None,
) -> dict[str, object]:
    """The engine-owned half, with every enumerated field present."""
    return {
        "bindings": {"light_group": ["light.kitchen"]},
        "modes": list(modes),
        "enable_flags": {"lighting.motion_light_on": True}
        if enabled is None
        else dict(enabled),
        "override_records": [],
        "rate_limit_windows": {},
        "room_timers": [],
        "installed_packs": {},
    }


def _start(*, unavailable: bool = False, seed: int = 11) -> Simulation:
    """A run with two entities, ready to be cut in half."""
    simulation = Simulation.start(
        seed=seed, started_at=_START, engine_state=_engine_state()
    )
    simulation.adapter.add_entity("light.kitchen", "off", context=ChangeContext.world())
    simulation.adapter.add_entity("sensor.hall", "12", context=ChangeContext.world())
    if unavailable:
        simulation.adapter.set_availability(
            "light.kitchen", available=False, context=ChangeContext.world()
        )
    return simulation


def _decide(simulation: Simulation) -> tuple[object, ...]:
    """One stand-in decision, depending on every enumerated piece of state.

    A run's decision touches the clock (when), the stream (how much randomness it
    has consumed), the house (what the devices say) and the engine's own state
    (which modes and behaviours are active). Folding all four into the output is
    what makes a snapshot that misses any one of them fail the replay tests below
    rather than pass.
    """
    simulation.clock.advance(timedelta(minutes=1))
    view = simulation.adapter.read_entity("light.kitchen")
    return (
        simulation.clock.now.isoformat(),
        round(simulation.stream.draw(), 6),
        view.state,
        view.available,
        simulation.engine_state.get("modes"),
        simulation.engine_state.get("enable_flags"),
    )


def _run(simulation: Simulation, steps: int) -> list[tuple[object, ...]]:
    return [_decide(simulation) for _ in range(steps)]


def _restart(simulation: Simulation) -> None:
    """Restart the house the way the runner does: an engine-context house control."""
    simulation.adapter.restart(context=ChangeContext.engine())


# --------------------------------------------------------------------------
# The contents: the snapshot carries them, and neither more nor less
# --------------------------------------------------------------------------


def test_a_snapshot_carries_the_enumerated_contents() -> None:
    """Entities, the engine's seven enumerated fields, the clock and the stream.

    A falsifying implementation that carried only the entities would leave the
    clock and the stream at their defaults, so `restore` would resume from a
    different instant and a different point in the sequence.
    """
    simulation = _start()
    simulation.clock.advance(timedelta(minutes=3))
    for _ in range(4):
        simulation.stream.draw()
    snapshot = simulation.snapshot()

    assert snapshot.snapshot_version == SNAPSHOT_VERSION
    assert set(snapshot.engine_state) >= set(ENGINE_STATE_FIELDS)
    assert snapshot.clock == simulation.clock.now.isoformat()
    assert snapshot.random_seed == simulation.stream.seed
    assert snapshot.random_position == simulation.stream.position
    assert [entry.entity_id for entry in snapshot.entities] == [
        "light.kitchen",
        "sensor.hall",
    ]


def test_the_snapshot_carries_no_decision_log() -> None:
    """No field of the document names a log, a record or history.

    The log is history, not state, and restoring it would make a replayed run's
    log depend on the run that preceded it. A falsifying implementation that put
    the log in the snapshot would be caught by any key naming it.
    """
    simulation = _start()
    document = simulation.snapshot().to_document()
    forbidden = ("log", "decision", "history")
    assert not any(word in key for key in document for word in forbidden)
    assert not any(
        word in key for key in simulation.snapshot().engine_state for word in forbidden
    )


def test_a_snapshot_round_trips_through_json() -> None:
    """A snapshot serialised and read back is an equal document.

    A falsifying implementation whose document order or container types changed
    across the round trip would produce two unequal documents for one state.
    """
    simulation = _start()
    simulation.adapter.set_availability(
        "light.kitchen", available=False, context=ChangeContext.world()
    )
    snapshot = simulation.snapshot()

    restored = Snapshot.from_json(snapshot.to_json())
    assert restored == snapshot
    assert restored.to_document() == snapshot.to_document()
    assert (
        restore_snapshot(restored).adapter.snapshot() == simulation.adapter.snapshot()
    )


def test_from_document_refuses_a_missing_field() -> None:
    """An absent top-level field is named, never defaulted."""
    document = _start().snapshot().to_document()
    del document["clock"]
    with pytest.raises(IncompleteSnapshotError) as raised:
        Snapshot.from_document(document)
    assert "clock" in raised.value.missing


def test_take_snapshot_refuses_an_engine_half_missing_a_field() -> None:
    """The engine's half must name every enumerated field, or capture fails.

    A falsifying implementation that accepted a partial engine mapping would let
    `mode` or `enable_flags` be silently dropped and a replay diverge for a reason
    no one can see.
    """
    simulation = _start()
    with pytest.raises(IncompleteSnapshotError) as raised:
        take_snapshot(
            simulation.adapter,
            clock=simulation.clock,
            stream=simulation.stream,
            engine_state={"bindings": {}, "modes": []},
        )
    assert set(raised.value.missing) == set(ENGINE_STATE_FIELDS) - {"bindings", "modes"}


def test_restore_refuses_an_unknown_snapshot_version() -> None:
    """A version this build does not understand is refused, naming it.

    A falsifying implementation that restored a subset would resume from a state
    the format never promised.
    """
    snapshot = _start().snapshot()
    broken = replace(snapshot, snapshot_version="999.0.0")
    with pytest.raises(UnsupportedSnapshotVersionError) as raised:
        restore_snapshot(broken)
    assert raised.value.version == "999.0.0"


# --------------------------------------------------------------------------
# Restore rebuilds each owner's state
# --------------------------------------------------------------------------


def test_restore_rebuilds_entities_state_attributes_availability_and_origin() -> None:
    """Every entity fact the port reports comes back, the origin included.

    A falsifying implementation that rebuilt state but not `last_origin` would
    silently un-override a manually-set lamp, so restore-then-replay would diverge
    on the first tick the engine consulted the override rule.
    """
    simulation = _start(unavailable=True)
    simulation.adapter.actuate("light.kitchen", "on", context=ChangeContext.user())
    simulation.adapter.add_entity(
        "cover.garage",
        "closed",
        attributes={"tilt": 0},
        context=ChangeContext.world(),
    )
    simulation.adapter.inject_fault(
        "sensor.hall", Fault(state="99"), context=ChangeContext.fault()
    )

    resumed = restore_snapshot(simulation.snapshot())
    assert resumed.adapter.snapshot() == simulation.adapter.snapshot()

    light = resumed.adapter.read_entity("light.kitchen")
    assert light.state == "on"
    assert light.available is False
    assert light.last_origin is ChangeOrigin.USER
    assert resumed.adapter.read_entity("sensor.hall").last_origin is ChangeOrigin.FAULT
    assert dict(resumed.adapter.read_entity("cover.garage").attributes) == {"tilt": 0}


def test_restore_repositions_the_clock_and_the_stream() -> None:
    """A restored run continues from the same instant and the same draw.

    A falsifying implementation that reset the stream to position zero would hand
    the resumed run a random sequence the original never saw.
    """
    simulation = _start()
    simulation.clock.advance(timedelta(minutes=7))
    for _ in range(4):
        simulation.stream.draw()

    resumed = restore_snapshot(simulation.snapshot())
    assert resumed.clock.now == simulation.clock.now
    assert resumed.stream.seed == simulation.stream.seed
    assert resumed.stream.position == simulation.stream.position
    assert resumed.stream.draw() == simulation.stream.draw()


def test_restore_carries_the_engine_half_unchanged() -> None:
    """The engine-owned state comes back exactly, so enablement survives a restore."""
    simulation = _start()
    simulation.engine_state = {
        **simulation.engine_state,
        "enable_flags": {"lighting.motion_light_on": True},
    }
    resumed = restore_snapshot(simulation.snapshot())
    assert resumed.engine_state == simulation.engine_state


# --------------------------------------------------------------------------
# The property: restore then replay is identical
# --------------------------------------------------------------------------


def test_restore_then_replay_produces_identical_decisions() -> None:
    """A run cut in half and resumed equals the run that was never interrupted.

    This is the property the whole enumeration exists for, and the reason a
    state-only assertion is not enough. A falsifying implementation that lost any
    enumerated field would make the tail below differ from the uninterrupted run's.
    """
    uninterrupted = _start()
    whole = _run(uninterrupted, 6)

    interrupted = _start()
    head = _run(interrupted, 3)
    snapshot = interrupted.snapshot()
    resumed = restore_snapshot(snapshot)
    tail = _run(resumed, 3)

    assert whole[:3] == head
    assert head + tail == whole
    assert resumed.adapter.snapshot() == interrupted.adapter.snapshot()


def test_a_restart_across_the_cut_reproduces_the_same_house() -> None:
    """A restart on each side of the cut lands the two runs in the same condition.

    The startup condition is what makes this hold, and it is why the snapshot
    carries one. `light.kitchen` is driven away from the state it was added with
    before the cut, so a snapshot carrying only the live condition would rebuild
    it as an entity that starts up actuated: restarting would then return the
    restored house to `on` while the uninterrupted house returns to `off`, and
    the replayed tail would diverge.
    """
    uninterrupted = _start()
    uninterrupted.adapter.actuate("light.kitchen", "on", context=ChangeContext.engine())
    whole = _run(uninterrupted, 3)
    _restart(uninterrupted)
    whole += _run(uninterrupted, 3)

    interrupted = _start()
    interrupted.adapter.actuate("light.kitchen", "on", context=ChangeContext.engine())
    head = _run(interrupted, 3)
    resumed = restore_snapshot(interrupted.snapshot())
    _restart(resumed)
    tail = _run(resumed, 3)

    assert head + tail == whole
    assert resumed.adapter.read_entity("light.kitchen").state == "off"
    assert resumed.adapter.snapshot() == uninterrupted.adapter.snapshot()


def test_a_snapshot_missing_the_random_stream_position_diverges() -> None:
    """Dropping the stream position changes the resumed decisions.

    The omission is caught rather than tolerated: with the position reset to zero
    the resumed run draws a different sequence, so the tail no longer equals the
    uninterrupted run's.
    """
    uninterrupted = _start()
    whole = _run(uninterrupted, 6)

    interrupted = _start()
    _run(interrupted, 3)
    broken = replace(interrupted.snapshot(), random_position=0)
    resumed = restore_snapshot(broken)
    assert _run(resumed, 3) != whole[3:]


def test_a_snapshot_missing_availability_changes_a_decision() -> None:
    """Dropping an entity's availability changes a decision that reads it.

    A device reported available when it was not is the unavailable-is-not-off
    mistake restored from a snapshot; the decision function reads availability, so
    the loss surfaces.
    """
    uninterrupted = _start(unavailable=True)
    whole = _run(uninterrupted, 6)

    interrupted = _start(unavailable=True)
    _run(interrupted, 3)
    snapshot = interrupted.snapshot()
    broken = replace(
        snapshot,
        entities=tuple(replace(entry, available=True) for entry in snapshot.entities),
    )
    resumed = restore_snapshot(broken)
    assert resumed.adapter.read_entity("light.kitchen").available is True
    assert _run(resumed, 3) != whole[3:]


def test_a_snapshot_missing_the_clock_position_changes_a_decision() -> None:
    """Dropping the clock position resumes the run at the wrong instant."""
    uninterrupted = _start()
    whole = _run(uninterrupted, 6)

    interrupted = _start()
    _run(interrupted, 3)
    broken = replace(interrupted.snapshot(), clock=_START.isoformat())
    resumed = restore_snapshot(broken)
    assert _run(resumed, 3) != whole[3:]


def test_a_snapshot_missing_an_engine_field_changes_a_decision() -> None:
    """Dropping the modes the engine is in changes a decision that reads them."""
    uninterrupted = _start()
    whole = _run(uninterrupted, 6)

    interrupted = _start()
    _run(interrupted, 3)
    snapshot = interrupted.snapshot()
    dropped: dict[str, object] = {**snapshot.engine_state, "modes": []}
    broken = replace(snapshot, engine_state=dropped)
    resumed = restore_snapshot(broken)
    assert _run(resumed, 3) != whole[3:]
