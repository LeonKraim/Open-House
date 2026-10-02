"""The safety audit -- the three guarantees `spec.txt:98` names for the whole.

The phase's last clause asks for an audit rather than a unit: "smoke/CO and leak
alerts bypass all modes, nothing auto-unlocks or opens garages, sensor-death
fallbacks". Each is a claim about the *house*, so each is driven here through
`openhouse.facade.open_session` -- the fake house, the virtual clock, no network
-- and read off the decision log, which is where a refusal and a fallback leave
the evidence a person could be shown.

`tests/test_engine_safety.py` tests the veto `engine/safety.py` implements, one
verdict per origin. This module is the other end of it: whether the guarantees
*survive the way a house is actually driven*, which is the question an audit asks
and a unit test does not. So an engine-origin unlock is produced through the
engine's own evaluation path, a world-origin write is made through the facade's
setup write, and the alert is raised in a house already suppressed by an override
and already in away mode.

Two of the three guarantees hold and are asserted green. The third -- an alert
that answers a hazard -- has every piece of its mechanism in place, *including* a
unit that uses it (`engine/behaviours/safety_alert.py`), but that unit is not in
the shipped registry (`default_behaviours()`), so the product this module drives
answers nothing; the test that says so is red on purpose, with a message naming
exactly what is missing.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime

import pytest
import yaml

from engine.behaviours import BehaviourContext, BehaviourScope, default_behaviours
from engine.decision_log import (
    DarkSource,
    DarkSourceReading,
    DecisionRecord,
    Outcome,
    Repair,
)
from engine.safety import EGRESS_ACTIONS, Hazard, hazard_kind
from engine.vocabulary import Vocabulary
from openhouse.facade import OpenHouse, open_session
from tools.catalog import paths

ROOT = paths.ROOT
PACKS = ROOT / "packs" / "official"

#: A winter night, so the sun branch is decidably dark wherever a test reaches it.
NIGHT = datetime(2026, 1, 1, 22, 0, tzinfo=UTC)

#: The corpus rows the brief's first guarantee is about, named so a failure can
#: point at what has no implementation.
ALERT_ROWS = ("security.smoke_alert", "security.water_leak_alert")


@pytest.fixture(scope="module")
def vocabulary() -> Vocabulary:
    """The committed catalog, loaded once for the module."""
    return Vocabulary.load(ROOT)


class _UnlockUnit:
    """A unit that proposes an unlock, so the veto has an engine origin to refuse.

    None of the shipped units can reach an egress domain -- that is the property
    `test_no_shipped_unit_can_propose_an_egress_action` asserts -- so without this
    unit there is no way to ask the audit's real question: not "does a world write
    get refused" but "does the *engine's own* evaluation get refused". It is the
    same double `tests/test_engine_tick.py` uses to make `refused: unsafe`
    producible, and it is passed through the documented `behaviours` seam rather
    than installed, because it is a test's stand-in and not a pack.
    """

    id = "unlocker"
    corpus_rows = ("lighting.room_light_manual_on",)
    scope = BehaviourScope.ROOM
    required_slots = ("lock",)
    optional_slots = ()
    priority = 0
    module: str | None = None
    enabled = True
    defaults: Mapping[str, object] = {}

    def evaluate(self, ctx: BehaviourContext) -> None:
        """Propose an unlock. The engine, not this unit, is what refuses it."""
        ctx.matched("lighting.room_light_manual_on")
        ctx.propose(
            slot="lock", action="unlocked", rule="lighting.room_light_manual_on"
        )


class _AlertUnit:
    """A unit that answers a hazard, so the audit can watch the bypassing path.

    The engine's safety path is real and is exercised here from within: the unit
    reads the hazards the engine found (`ctx.hazards()`) and proposes with
    `safety=True` when a detector is alarming. It stands in for the real
    `engine/behaviours/safety_alert.py`'s `SafetyAlertBehaviour`, which says the
    same thing but is absent from `default_behaviours()`; `test_a_smoke_alarm_raises_a_response`
    asserts the other half -- that the shipped registry carries no such unit, so
    the composed product answers nothing.
    """

    id = "alert"
    corpus_rows = ("security.smoke_alert",)
    scope = BehaviourScope.HOUSE
    required_slots = ()
    optional_slots = ()
    priority = 0
    module: str | None = None
    enabled = True
    defaults: Mapping[str, object] = {}

    def evaluate(self, ctx: BehaviourContext) -> None:
        """Raise the alert as a safety command when a detector is alarming."""
        if ctx.hazards():
            ctx.propose(
                slot="light_group",
                action="on",
                rule="security.smoke_alert",
                safety=True,
            )
        else:
            ctx.matched("security.smoke_alert")


def _house(bindings: Mapping[str, str]) -> dict[str, object]:
    """A one-room house document, `bindings` mapping each slot to its entity id."""
    return {
        "name": "the audit house",
        "rooms": [
            {
                "id": "hall",
                "name": "Hall",
                "type": "hallway",
                "bindings": {
                    slot: {"entity_id": entity_id}
                    for slot, entity_id in bindings.items()
                },
            }
        ],
        "house_scope": {"slots": ["light_group", "lock"]},
    }


def _since(session: OpenHouse, mark: int) -> tuple[DecisionRecord, ...]:
    """Every record written since `mark`."""
    return session.get_decision_log()[mark:]


def _acted(records: Sequence[DecisionRecord]) -> list[DecisionRecord]:
    """Every record that reached the house."""
    return [record for record in records if record.outcome is Outcome.ACTED]


# --------------------------------------------------------------------------
# Nothing auto-unlocks or opens garages
# --------------------------------------------------------------------------


def _shipped_pack_services() -> set[str]:
    """Every service any shipped pack's behaviours declare."""
    services: set[str] = set()
    for path in sorted(PACKS.glob("*.yaml")):
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(loaded, Mapping) or "behaviours" not in loaded:
            continue
        for behaviour in loaded["behaviours"] or ():
            services.update(behaviour.get("services") or ())
    return services


def test_no_shipped_pack_declares_an_egress_service() -> None:
    """No pack the project ships can ask for an unlock or an open.

    The veto is the enforcement point; this is the belt beside it. A pack that
    cannot *say* `lock.unlock` cannot propose an unlock for the veto to refuse.
    `catalog/pack-policy.yaml` flags `lock.unlock` rather than banning it -- a pack
    may legitimately want it -- so a future pack could declare one, and this test
    makes that a deliberate decision rather than a quiet arrival. A falsifying
    implementation would be a shipped pack naming a service in `EGRESS_ACTIONS`.
    """
    egress = set().union(*EGRESS_ACTIONS.values())
    shipped = _shipped_pack_services()
    assert shipped, "no shipped pack declares any service; the audit would be vacuous"
    assert shipped.isdisjoint(egress), (
        f"a shipped pack declares an egress service: {sorted(shipped & egress)}"
    )


def test_no_shipped_unit_can_propose_an_egress_action() -> None:
    """No unit in the shipped registry reaches a lock or a cover slot.

    The shipped units act through lighting and presence slots only, so no
    evaluation of theirs can produce an egress command and the veto is never even
    reached from the shipped registry. This is the structural half of "nothing
    auto-unlocks"; the runtime half is the refusals the next tests produce.
    """
    shipped = default_behaviours()
    assert set(shipped) == {
        "away_shutdown",
        "motion_lighting",
        "override",
        "safety_alert",
    }
    egress_slots = {"lock", "cover"}
    for unit in shipped.values():
        reached = set(unit.required_slots) | set(unit.optional_slots)
        assert reached.isdisjoint(egress_slots), f"{unit.id} reaches {sorted(reached)}"


def test_an_automations_attempt_to_unlock_is_refused_and_recorded(
    vocabulary: Vocabulary,
) -> None:
    """An engine-origin unlock is refused, logged, and the door does not open.

    The rule is about *the system*, so the interesting origin is the engine's own:
    a behaviour that decides to unlock must be stopped and must leave a
    `refused: unsafe` record naming the attempt, because a refusal that vanished
    without a trace would be a guarantee nobody could audit. A falsifying
    implementation that refused only external origins would open the door here,
    and one that refused silently would leave the house locked with no evidence.
    """
    session = open_session(
        house=_house({"lock": "lock.front_door", "light_group": "light.hall"}),
        vocabulary=vocabulary,
        started_at=NIGHT,
        behaviours={"unlocker": _UnlockUnit()},
    )
    mark = len(session.get_decision_log())
    session.advance_time(minutes=1)

    record = next(
        record for record in _since(session, mark) if record.actor == "unlocker"
    )
    assert record.outcome is Outcome.REFUSED_UNSAFE
    assert [(command.slot, command.action) for command in record.commands] == [
        ("lock", "unlocked")
    ]
    assert session.read_entity("lock.front_door").state == "locked"


def test_a_world_write_cannot_unlock_a_lock_or_open_a_garage(
    vocabulary: Vocabulary,
) -> None:
    """A write from outside the engine is refused on both egress domains.

    `set_state` is the facade's world-origin write -- what an integration or a
    stray script looks like -- and it must be refused on a lock and on a garage
    door alike, with the entity left exactly as it was. A falsifying implementation
    that vetted only the engine's writes would let the first integration that
    reached the port open the garage.
    """
    session = open_session(
        house=_house({"lock": "lock.front_door", "light_group": "light.hall"}),
        vocabulary=vocabulary,
        started_at=NIGHT,
    )
    session.add_entity("cover.garage_door", "closed")

    for entity_id, attempted in (
        ("lock.front_door", "unlocked"),
        ("cover.garage_door", "open"),
    ):
        mark = len(session.get_decision_log())
        session.set_state(entity_id, attempted)
        refusals = [
            record
            for record in _since(session, mark)
            if record.outcome is Outcome.REFUSED_UNSAFE
        ]
        assert [record.actor for record in refusals] == ["set_state"], entity_id

    assert session.read_entity("lock.front_door").state == "locked"
    assert session.read_entity("cover.garage_door").state == "closed"


def test_a_persons_own_unlock_is_permitted(vocabulary: Vocabulary) -> None:
    """The rule bans the system, not the occupant, so a person's own hand lands.

    `user_action` is the only path that carries a user origin, and it must be
    allowed through: a house that refused its own resident's unlock would be a
    house that will not obey, which is the failure the product rule is *not*
    about. A falsifying implementation that refused every egress action would pass
    every test above and lock a person out of their own house.
    """
    session = open_session(
        house=_house({"lock": "lock.front_door", "light_group": "light.hall"}),
        vocabulary=vocabulary,
        started_at=NIGHT,
    )

    session.user_action("lock.front_door", "unlocked")

    assert session.read_entity("lock.front_door").state == "unlocked"


# --------------------------------------------------------------------------
# Smoke, CO and leak alerts -- mechanism present, no unit to use it
# --------------------------------------------------------------------------


def test_a_hazard_detector_is_recognised_by_what_it_is(vocabulary: Vocabulary) -> None:
    """A smoke, CO or leak detector is found by its device class, not by a slot.

    The vocabulary has no smoke slot to bind and the corpus agrees, so the engine
    scans the house and matches a `binary_sensor`'s `device_class` -- which is why
    a house cannot be reconfigured out of having a smoke alarm. A clear detector
    and a device whose class is not an alert class raise nothing. A falsifying
    implementation that matched on an entity's *name* would answer a device called
    `binary_sensor.smoke_alarm` and miss the real one.
    """
    session = open_session(
        house=_house({"light_group": "light.hall"}),
        vocabulary=vocabulary,
        started_at=NIGHT,
    )
    session.add_entity(
        "binary_sensor.smoke_alarm", "on", attributes={"device_class": "smoke"}
    )
    session.add_entity(
        "binary_sensor.co_alarm", "on", attributes={"device_class": "carbon_monoxide"}
    )
    session.add_entity(
        "binary_sensor.kitchen_leak", "on", attributes={"device_class": "moisture"}
    )
    session.add_entity(
        "binary_sensor.hall_motion", "on", attributes={"device_class": "motion"}
    )
    session.add_entity(
        "binary_sensor.quiet_smoke", "off", attributes={"device_class": "smoke"}
    )

    assert session.engine.hazards() == (
        Hazard(entity_id="binary_sensor.co_alarm", kind="carbon_monoxide"),
        Hazard(entity_id="binary_sensor.kitchen_leak", kind="leak"),
        Hazard(entity_id="binary_sensor.smoke_alarm", kind="smoke"),
    )
    assert hazard_kind(session.read_entity("binary_sensor.hall_motion")) is None
    assert hazard_kind(session.read_entity("binary_sensor.quiet_smoke")) is None


def test_a_safety_alert_bypasses_every_suppression(vocabulary: Vocabulary) -> None:
    """A hazard response is admitted though an override and a mode both say no.

    "Bypass all modes" is a claim about the one stage that can suppress a command:
    an override in force is not consulted for a `safety=True` proposal, so the
    alert acts while a softer behaviour's command to the *same* entity loses. The
    house is put in away mode as well, so the alert is raised in the one state a
    person would least want it silenced. A falsifying implementation that ran the
    override stage first would leave the alert suppressed and the light off -- an
    alarm nobody hears.
    """
    session = open_session(
        house=_house(
            {
                "motion_sensor": "binary_sensor.hall_motion",
                "light_group": "light.hall",
                "ambient_light_sensor": "sensor.hall_lux",
            }
        ),
        vocabulary=vocabulary,
        started_at=NIGHT,
        behaviours={**default_behaviours(), "alert": _AlertUnit()},
        house_settings={
            "behaviour.motion_lighting.enabled": True,
            "behaviour.override.enabled": True,
            "behaviour.away_shutdown.enabled": True,
        },
    )
    session.set_state("sensor.hall_lux", "5")
    session.set_state("binary_sensor.hall_motion", "on")
    session.advance_time(minutes=1)
    assert session.read_entity("light.hall").state == "on"

    session.engine.modes.activate("away")
    session.set_state("binary_sensor.hall_motion", "off")
    session.user_action("light.hall", "on")
    session.add_entity(
        "binary_sensor.smoke_alarm", "on", attributes={"device_class": "smoke"}
    )

    mark = len(session.get_decision_log())
    session.advance_time(minutes=10)

    records = _since(session, mark)
    assert session.engine.overrides.is_overridden("light.hall")
    alert = next(record for record in records if record.actor == "alert")
    assert alert.outcome is Outcome.ACTED
    assert alert.rule == "security.smoke_alert"
    assert [(command.slot, command.action) for command in alert.commands] == [
        ("light_group", "on")
    ]
    assert session.read_entity("light.hall").state == "on"
    # The softer behaviours wanted the same light and did not get it: the alert is
    # what reached the house, and it reached it while the light was overridden.
    for actor in ("motion_lighting", "away_shutdown"):
        softer = [record for record in records if record.actor == actor]
        assert all(record.outcome is not Outcome.ACTED for record in softer)


def test_a_smoke_alarm_raises_a_response(vocabulary: Vocabulary) -> None:
    """An alarming detector makes the shipped product do something.

    The claim is about the *registry a household installs*, not about a unit
    somewhere in the tree: `default_behaviours()` is what `ha_adapter/composition.py`
    composes, so a hazard-answering unit that existed and was not in it would be a
    person's smoke alarm shrieking while the house did precisely nothing.

    Red before `SafetyAlertBehaviour` was registered. The two halves are asserted
    separately because they fail for different reasons: the first says a shipped
    unit answers the corpus's alert rows, and the second says a real alarm reaches
    the house through it.
    """
    cited = {row for unit in default_behaviours().values() for row in unit.corpus_rows}
    assert not cited.isdisjoint(ALERT_ROWS), (
        "no shipped unit cites an alert row, so nothing in the registry the "
        "integration composes could answer a hazard"
    )

    session = open_session(
        house=_house({"light_group": "light.hall"}),
        vocabulary=vocabulary,
        started_at=NIGHT,
        house_settings={"behaviour.safety_alert.enabled": True},
    )
    session.add_entity(
        "binary_sensor.smoke_alarm", "on", attributes={"device_class": "smoke"}
    )
    assert session.engine.hazards(), (
        "the detector must be recognised for this to be a fair test"
    )

    mark = len(session.get_decision_log())
    session.advance_time(minutes=1)
    acted = _acted(_since(session, mark))
    assert acted != [], (
        "an alarming smoke detector produced no response from the shipped product"
    )


def test_the_alert_ships_off_and_is_the_only_thing_that_answers_a_hazard() -> None:
    """Registered is not enabled, and that is the policy rather than an oversight.

    A module ships off and a household opts in, with no always-on tier -- so a
    smoke alarm reaching the registry must not make a fresh house act. The
    second assertion is the one that would catch the alert being *replaced*: a
    registry that answered hazards with some other unit would leave this alarm
    unreachable, which is the failure the registration was for.
    """
    units = default_behaviours()
    assert units["safety_alert"].enabled is False
    answerings = [
        unit.id for unit in units.values() if set(unit.corpus_rows) & set(ALERT_ROWS)
    ]
    assert answerings == ["safety_alert"]


# --------------------------------------------------------------------------
# Sensor-death fallbacks
# --------------------------------------------------------------------------


def test_a_dead_sensor_is_silence_and_not_an_empty_house(
    vocabulary: Vocabulary,
) -> None:
    """A house whose only motion sensor died is not a house that has emptied.

    The dangerous fallback, and the one "nothing may be decided from silence" is
    really about: an away shutdown that read a dead sensor as "the room is clear"
    would turn a house's lighting off on an outage, which is a fault escalating
    into an action. The engine's emptiness answer must stay `False` while the only
    sensor that could answer is unreadable, and the dead slot must be named in a
    `Repair` so the hold is visible rather than merely absent.
    """
    session = open_session(
        house=_house(
            {
                "motion_sensor": "binary_sensor.hall_motion",
                "light_group": "light.hall",
            }
        ),
        vocabulary=vocabulary,
        started_at=NIGHT,
        house_settings={
            "behaviour.motion_lighting.enabled": True,
            "behaviour.away_shutdown.enabled": True,
        },
    )
    session.set_state("light.hall", "on")
    session.engine.modes.activate("away")
    session.set_availability("binary_sensor.hall_motion", False)

    mark = len(session.get_decision_log())
    session.advance_time(minutes=30)

    records = _since(session, mark)
    assert _acted(records) == []
    shutdown = next(record for record in records if record.actor == "away_shutdown")
    assert shutdown.outcome is Outcome.DECLINED
    assert session.read_entity("light.hall").state == "on"
    lighting = next(record for record in records if record.actor == "motion_lighting")
    repairs = [entry for entry in lighting.inputs if isinstance(entry, Repair)]
    assert [repair.entity_id for repair in repairs] == ["binary_sensor.hall_motion"]


def test_a_dead_ambient_light_sensor_falls_back_to_the_sun(
    vocabulary: Vocabulary,
) -> None:
    """A lux sensor that stops reporting hands the dark test to the sun.

    `catalog/edge_cases.yaml` names "a luminance sensor that stops reporting", and
    the fallback is the branch with a value rather than a guess: deciding the room
    is dark because a dead sensor reads `unavailable` turns lights on in daylight,
    and deciding it is bright leaves a room dark. The record names which branch
    decided, so the fallback is a decision in the log and not a silent default. A
    falsifying implementation that read the dead sensor's state as a number would
    raise, and one that kept the lux branch's default would light a bright room.
    """
    session = open_session(
        house=_house(
            {
                "motion_sensor": "binary_sensor.hall_motion",
                "light_group": "light.hall",
                "ambient_light_sensor": "sensor.hall_lux",
            }
        ),
        vocabulary=vocabulary,
        started_at=NIGHT,
        house_settings={"behaviour.motion_lighting.enabled": True},
    )
    session.set_availability("sensor.hall_lux", False)
    session.set_state("binary_sensor.hall_motion", "on")

    mark = len(session.get_decision_log())
    session.advance_time(minutes=1)

    lighting = next(
        record for record in _since(session, mark) if record.actor == "motion_lighting"
    )
    sources = [
        entry.source
        for entry in lighting.inputs
        if isinstance(entry, DarkSourceReading)
    ]
    assert sources == [DarkSource.SUN]


def test_a_dead_hazard_detector_is_not_a_clear_reading(vocabulary: Vocabulary) -> None:
    """A smoke alarm that stopped reporting is unknown, not "no fire".

    `hazard_kind` requires `available` before it will read a detector's state, so a
    dead alarm raises no hazard -- the fail-safe direction, since a detector that
    drops its last `on` reading must not keep an alarm going forever. The audit
    states it explicitly because the opposite mistake (a dead detector read as
    alarming, or a stale `on` held indefinitely) is the one a reader would assume
    away. A falsifying implementation that ignored availability would answer a
    smoke alarm that has been unplugged for a week.
    """
    session = open_session(
        house=_house({"light_group": "light.hall"}),
        vocabulary=vocabulary,
        started_at=NIGHT,
    )
    session.add_entity(
        "binary_sensor.smoke_alarm", "on", attributes={"device_class": "smoke"}
    )
    session.set_availability("binary_sensor.smoke_alarm", False)

    assert session.engine.hazards() == ()
    assert hazard_kind(session.read_entity("binary_sensor.smoke_alarm")) is None
