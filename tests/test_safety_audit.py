"""The safety audit: the three claims Phase 8 names, each one asserted.

`spec.txt`'s safety-audit line is three requirements, and this module is one test
group per requirement:

1. **A smoke, CO or leak alert is never suppressed.** Not by a house mode -- Sleep
   or Away -- and not by the three stages the engine applies to an ordinary
   command: a person's recent hand on the light (the override), the engine's own
   rate limit, or a better-prioritised behaviour winning the same entity in
   arbitration. In every one of those houses the alert still reaches the light.
2. **Nothing the engine decides unlocks a lock or opens a garage cover** unless a
   person asked for it in that act, and a pack cannot declare its way past the
   rule. A unit that *requires* a `lock` slot, or that cites a corpus row about
   locks, is still refused at the veto -- and a *safety* command that reaches for a
   lock is refused too, because the two halves of the audit must not cancel.
3. **A sensor that dies mid-run produces a fallback and a repair, never a silent
   "fine".** Home Assistant's "unavailable is not off" reaching the engine: a dead
   motion sensor is not an empty room, so the room is held rather than darkened,
   the engine names the dead device in a repair, and a recovered sensor resumes
   deciding. A dead *hazard* detector is likewise not a clear one.

The alert itself is `engine/behaviours/safety_alert.py`; the guarantees are the
engine's (`engine/safety.py`, `engine/arbitration.py`, `engine/engine.py`). The
unit is injected here rather than shipped in `default_behaviours()`, because the
shipped registry is exactly the three Phase 1 units and a fourth would be a Phase
2 decision; what this module proves is that *any* unit that marks a command
`safety` reaches the unsuppressible path, which is the guarantee a pack interpreter
will be built against.

Each test says, in its docstring, what a falsifying implementation would look like.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from engine.adapter import ChangeContext, ChangeOrigin, EntityView, domain_of
from engine.behaviours import BehaviourScope, default_behaviours, enable_key
from engine.behaviours.safety_alert import SafetyAlertBehaviour
from engine.binding import House
from engine.config import RATE_LIMIT_BOUND_KEY
from engine.decision_log import DecisionRecord, HazardReading, Outcome, Repair
from engine.dwell import DwellRegistry
from engine.engine import Engine
from engine.modes import ModeSet
from engine.safety import Hazard, hazard_kind, refuses
from engine.solar import Location
from engine.vocabulary import Vocabulary
from sim.adapter import FakeHouseAdapter
from sim.clock import VirtualClock
from sim.entropy import RandomStream

if TYPE_CHECKING:
    from engine.behaviours import Behaviour, BehaviourContext

ROOT = Path(__file__).resolve().parents[1]

#: A winter night in London, so a room's light is wanted when somebody moves.
NIGHT = datetime(2026, 1, 1, 22, 0, tzinfo=UTC)

LOCATION = Location(latitude=51.5, longitude=-0.1, time_zone="Europe/London")

#: Home, Away and Sleep, all exclusive with one another. Three rather than the
#: harness's usual two because the audit names Sleep and Away separately and the
#: alert must survive each; they share an exclusive group so activating one stands
#: the others down, exactly as a real household's presence modes do.
MODES: tuple[Mapping[str, object], ...] = (
    {
        "name": "home",
        "description": "Somebody is in.",
        "exclusive_group": "house_presence",
    },
    {
        "name": "away",
        "description": "Nobody is in.",
        "exclusive_group": "house_presence",
    },
    {
        "name": "sleep",
        "description": "The household is asleep.",
        "exclusive_group": "house_presence",
    },
)

#: The kitchen every harness starts from, with a mode selector so a house-scoped
#: unit's `house_mode` required slot can resolve (a house-scope slot is gathered
#: from the rooms).
KITCHEN: Mapping[str, str] = {
    "motion_sensor": "binary_sensor.kitchen_motion",
    "light_group": "light.kitchen",
    "house_mode": "input_select.house_mode",
}

#: The state a fresh entity of each domain is added in.
_INITIAL: Mapping[str, str] = {
    "binary_sensor": "off",
    "light": "off",
    "switch": "off",
    "sensor": "5",
    "input_select": "home",
    "lock": "locked",
    "cover": "closed",
}

_ALL_ENABLED = (
    enable_key("motion_lighting"),
    enable_key("override"),
    enable_key("away_shutdown"),
    enable_key("safety_alert"),
)

#: An alarming smoke detector's attributes. A hazard is recognised by device class
#: (`engine/safety.py`), so this mapping is what makes the entity an alarm at all.
_SMOKE: Mapping[str, object] = {"device_class": "smoke"}

#: The classic five minutes a room is held after its last motion.
_QUIET = timedelta(minutes=5)

#: Longer than any timeout under test, so a room that was seen reading is quiet by
#: the next tick.
_A_WHILE = timedelta(minutes=10)


# --------------------------------------------------------------------------
# Test units: the alert, a plain competitor, and the pack-declared egress units
# --------------------------------------------------------------------------


class _LightOnBehaviour:
    """A unit that proposes `on` to the light group and marks nothing safety.

    It is the control for the alert: the same proposal in the same house, without
    the `safety` flag, is what the override and the rate limit are supposed to
    stop. A test that only ever ran the alert could not tell "the alert is
    unsuppressible" from "nothing in this house suppresses anything".
    """

    id = "plain_on"
    corpus_rows = ("lighting.motion_light_on",)
    scope = BehaviourScope.HOUSE
    required_slots = ("house_mode",)
    optional_slots = ("light_group",)
    priority = 100
    module: str | None = None
    enabled = True
    defaults: Mapping[str, object] = {}

    def evaluate(self, ctx: BehaviourContext) -> None:
        """Propose the light on, under the same rule the alert would name."""
        ctx.matched("lighting.motion_light_on")
        if ctx.binding("light_group").is_empty:
            return
        ctx.propose(slot="light_group", action="on", rule="lighting.motion_light_on")


class _LoudCompetitorBehaviour:
    """A behaviour with a priority *above* the alert's, on the same light.

    The alert declares `priority = 100`; this one declares 1000, so if
    arbitration ranked by priority alone the competitor would win and the alert
    would be silenced. It is the unit that makes the safety flag's own tier
    load-bearing: `test_the_safety_flag_outranks_a_higher_priority_behaviour`
    would pass without it (the alert's 100 already beats the shutdown's 10), so
    without a louder competitor "ranked above every behaviour" is untested.
    """

    id = "loud_competitor"
    corpus_rows = ("lighting.motion_light_on",)
    scope = BehaviourScope.HOUSE
    required_slots = ("house_mode",)
    optional_slots = ("light_group",)
    priority = 1000
    module: str | None = None
    enabled = True
    defaults: Mapping[str, object] = {}

    def evaluate(self, ctx: BehaviourContext) -> None:
        """Propose the light off, which the alert must still outrank."""
        ctx.matched("lighting.motion_light_on")
        if ctx.binding("light_group").is_empty:
            return
        ctx.propose(slot="light_group", action="off", rule="lighting.motion_light_on")


class _PackUnlockBehaviour:
    """A pack-declared unit that wants an entry door unlocked.

    It cites `security.lock_auto_lock` and *requires* the `lock` slot, which is the
    strongest form a pack can take of "I am about locks": it has declared the row,
    declared the slot and bound the device. If declaring a lock were enough to
    reach one, this unit would open the door; "a pack cannot declare its way to
    one" is exactly the claim that it does not.
    """

    corpus_rows = ("security.lock_auto_lock",)

    def __init__(
        self, *, identifier: str = "pack_unlocker", safety: bool = False
    ) -> None:
        self.id = identifier
        self._safety = safety

    scope = BehaviourScope.ROOM
    required_slots = ("lock",)
    optional_slots = ()
    priority = 1000
    module: str | None = None
    enabled = True
    defaults: Mapping[str, object] = {}

    def evaluate(self, ctx: BehaviourContext) -> None:
        """Propose the door unlocked. The engine, not this unit, refuses it."""
        ctx.matched("security.lock_auto_lock")
        ctx.propose(
            slot="lock",
            action="unlocked",
            rule="security.lock_auto_lock",
            safety=self._safety,
        )


class _PackRelockBehaviour:
    """A pack-declared unit that locks a door, which is not an egress action.

    The control for the two unlock tests: the veto is about the *direction* of the
    door -- locked is safe, unlocked is not -- so a unit that secures a door must
    be allowed to, or the rule would be "no behaviour may name a lock at all" and
    every auto-lock in the corpus would be unimplementable.
    """

    id = "pack_relocker"
    corpus_rows = ("security.lock_auto_lock",)
    scope = BehaviourScope.ROOM
    required_slots = ("lock",)
    optional_slots = ()
    priority = 1000
    module: str | None = None
    enabled = True
    defaults: Mapping[str, object] = {}

    def evaluate(self, ctx: BehaviourContext) -> None:
        """Propose the door locked, which the veto permits."""
        ctx.matched("security.lock_auto_lock")
        ctx.propose(slot="lock", action="locked", rule="security.lock_auto_lock")


class _PackGarageBehaviour:
    """A pack-declared unit that wants a garage door opened.

    It cites `security.door_left_open`, whose optional slot is `cover`, so the unit
    is a faithful implementation of a corpus concept and still may not open the
    door: what stops it is the domain of what it writes, not the row it claims.
    """

    id = "pack_garage"
    corpus_rows = ("security.door_left_open",)
    scope = BehaviourScope.ROOM
    required_slots = ("cover",)
    optional_slots = ()
    priority = 1000
    module: str | None = None
    enabled = True
    defaults: Mapping[str, object] = {}

    def evaluate(self, ctx: BehaviourContext) -> None:
        """Propose the garage open, which the veto refuses."""
        ctx.matched("security.door_left_open")
        ctx.propose(slot="cover", action="open", rule="security.door_left_open")


# --------------------------------------------------------------------------
# Building a harness
# --------------------------------------------------------------------------


def _document(
    layout: Mapping[str, Mapping[str, str]],
    house_scope_slots: Sequence[str],
) -> dict[str, object]:
    """A house document in the frozen schema's shape."""
    return {
        "name": "the test house",
        "rooms": [
            {
                "id": room_id,
                "name": room_id.replace("_", " ").title(),
                "type": "kitchen",
                "bindings": {
                    slot: {"entity_id": entity_id}
                    for slot, entity_id in bindings.items()
                },
            }
            for room_id, bindings in layout.items()
        ],
        "house_scope": {"slots": list(house_scope_slots)},
    }


def _registry(*extra: Behaviour) -> tuple[Behaviour, ...]:
    """The shipped units, so a test's injected unit runs alongside the real ones."""
    return (*default_behaviours().values(), *extra)


def _build(
    vocabulary: Vocabulary,
    *,
    at: datetime = NIGHT,
    layout: Mapping[str, Mapping[str, str]] | None = None,
    entities: Mapping[str, str] | None = None,
    attributes: Mapping[str, Mapping[str, object]] | None = None,
    settings: Mapping[str, object] | None = None,
    behaviours: Sequence[Behaviour] | None = None,
    house_scope_slots: Sequence[str] = ("house_mode", "light_group"),
) -> tuple[Engine, FakeHouseAdapter, VirtualClock]:
    """A house, a fake and an engine over them, all the units enabled.

    The default is the single kitchen every audit test starts from, with the
    shipped units on. A test adds the alert, a competitor or a pack unit through
    `behaviours`, and adds a detector through `entities` and `attributes` -- the
    last only because a hazard is a device *class* and the harness has no other way
    to say what a mock device is.
    """
    rooms = dict(layout) if layout is not None else {"kitchen": dict(KITCHEN)}
    house_settings: dict[str, object] = dict.fromkeys(_ALL_ENABLED, True)
    house_settings.update(settings or {})
    supplied = attributes or {}

    clock = VirtualClock.started_at(at)
    adapter = FakeHouseAdapter(clock=clock, random_stream=RandomStream.from_seed(1))
    initial = {
        entity_id: _INITIAL[domain_of(entity_id)]
        for bindings in rooms.values()
        for entity_id in bindings.values()
    }
    initial.update(entities or {})
    for entity_id, value in initial.items():
        adapter.add_entity(
            entity_id,
            value,
            attributes=supplied.get(entity_id, {}),
            context=ChangeContext.world(),
        )

    house = House.from_document(
        _document(rooms, house_scope_slots), vocabulary=vocabulary
    )
    engine = Engine(
        adapter=adapter,
        house=house,
        clock=clock,
        location=LOCATION,
        modes=ModeSet(MODES, vocabulary=vocabulary),
        behaviours=behaviours,
        house_settings=house_settings,
    )
    return engine, adapter, clock


def _alarm(
    adapter: FakeHouseAdapter, entity_id: str = "binary_sensor.smoke_sensor"
) -> None:
    """Add an alarming smoke detector to a running house."""
    adapter.add_entity(
        entity_id, "on", attributes=_SMOKE, context=ChangeContext.world()
    )


@pytest.fixture(scope="module")
def vocabulary() -> Vocabulary:
    """The committed catalog, frozen, so one load serves the module."""
    return Vocabulary.load(ROOT)


def _one(records: Sequence[DecisionRecord], actor: str) -> DecisionRecord:
    """The single record `actor` left this tick."""
    found = [record for record in records if record.actor == actor]
    assert len(found) == 1, f"{actor} left {len(found)} records"
    return found[0]


def _readings[Entry](record: DecisionRecord, kind: type[Entry]) -> list[Entry]:
    """Record `record`'s inputs of one kind, in the order it consulted them."""
    return [entry for entry in record.inputs if isinstance(entry, kind)]


# --------------------------------------------------------------------------
# 1. A hazard alert is never suppressed
# --------------------------------------------------------------------------


def test_a_smoke_alarm_lights_the_house(vocabulary: Vocabulary) -> None:
    """An alarming detector turns the light on, and the record names the detector.

    A falsifying implementation that lit the light without recording the hazard
    would leave a scenario asserting "the light is on" unable to tell the alert
    from any other reason a light comes on, which is exactly the fact the
    `HazardReading` input exists to carry.
    """
    engine, adapter, _ = _build(
        vocabulary,
        behaviours=_registry(SafetyAlertBehaviour()),
        entities={"binary_sensor.smoke_sensor": "on"},
        attributes={"binary_sensor.smoke_sensor": _SMOKE},
    )
    record = _one(engine.tick(), "safety_alert")
    assert record.outcome is Outcome.ACTED
    assert record.rule == "security.smoke_alert"
    assert _readings(record, HazardReading) == [
        HazardReading(entity_id="binary_sensor.smoke_sensor", kind="smoke")
    ]
    assert [command.safety for command in record.commands] == [True]
    assert adapter.read_entity("light.kitchen").state == "on"


def test_a_leak_alarm_lights_the_house(vocabulary: Vocabulary) -> None:
    """A water contact is the same alert under a different class, and it is named.

    A falsifying implementation that recognised only `smoke` would leave a leak
    unannounced while every smoke test stayed green, and the household would learn
    of the flood from the floor.
    """
    engine, adapter, _ = _build(
        vocabulary,
        behaviours=_registry(SafetyAlertBehaviour()),
        entities={"binary_sensor.utility_leak": "on"},
        attributes={"binary_sensor.utility_leak": {"device_class": "moisture"}},
    )
    record = _one(engine.tick(), "safety_alert")
    assert record.outcome is Outcome.ACTED
    assert _readings(record, HazardReading) == [
        HazardReading(entity_id="binary_sensor.utility_leak", kind="leak")
    ]
    assert adapter.read_entity("light.kitchen").state == "on"


def test_a_co_alarm_lights_the_house(vocabulary: Vocabulary) -> None:
    """A CO detector is the alert the audit names *first* after smoke, and it is named.

    The audit lists "smoke/CO and leak" by name, so the CO class is a claim and not
    only a member of the class table: a falsifying implementation whose response
    unit read a narrower set -- or that named the wrong alarm -- would leave a
    carbon-monoxide warning indistinguishable from a fire one in the record, which
    is the alarm a sleeping household most needs to be told apart.
    """
    engine, adapter, _ = _build(
        vocabulary,
        behaviours=_registry(SafetyAlertBehaviour()),
        entities={"binary_sensor.co_alarm": "on"},
        attributes={"binary_sensor.co_alarm": {"device_class": "carbon_monoxide"}},
    )
    record = _one(engine.tick(), "safety_alert")
    assert record.outcome is Outcome.ACTED
    assert _readings(record, HazardReading) == [
        HazardReading(entity_id="binary_sensor.co_alarm", kind="carbon_monoxide")
    ]
    assert adapter.read_entity("light.kitchen").state == "on"


def test_the_safety_flag_outranks_a_higher_priority_behaviour(
    vocabulary: Vocabulary,
) -> None:
    """A behaviour whose priority is *above* the alert's still loses the light.

    This is the half of "ranked above every behaviour" the away test cannot show:
    the shutdown declares priority 10 and the alert 100, so a ranking by priority
    alone would already let the alert win, and the safety tier would be untested.
    Here the competitor declares 1000, so only the tier can save the alert -- a
    falsifying implementation that dropped the safety tier (or gated alerts on
    arbitration like any other command) would darken the house on a fire.
    """
    engine, adapter, _ = _build(
        vocabulary,
        behaviours=_registry(SafetyAlertBehaviour(), _LoudCompetitorBehaviour()),
        entities={"binary_sensor.smoke_sensor": "on", "light.kitchen": "on"},
        attributes={"binary_sensor.smoke_sensor": _SMOKE},
    )
    records = engine.tick()
    assert _one(records, "safety_alert").outcome is Outcome.ACTED
    assert _one(records, "loud_competitor").outcome is Outcome.LOST_ARBITRATION
    assert adapter.read_entity("light.kitchen").state == "on"


def test_away_mode_cannot_silence_the_alert(vocabulary: Vocabulary) -> None:
    """Away and an empty house want the light off; the alarm outranks both.

    This is the claim's hardest case, because two mechanisms are against the
    alert at once: away mode is a house mode, and the shutdown that answers it is
    a higher-priority behaviour contending for the same light. A falsifying
    implementation that gated the alert on a mode would leave the house dark at
    the moment it most needs to be lit -- and the shutdown's `lost arbitration`
    record is what proves the contention really happened rather than the shutdown
    merely declining. (The alert's safety *tier* is isolated separately, by the
    louder competitor above, since the shutdown's priority of 10 does not reach it.)
    """
    engine, adapter, clock = _build(
        vocabulary,
        behaviours=_registry(SafetyAlertBehaviour()),
        entities={"light.kitchen": "on"},
    )
    engine.modes.activate("away")
    engine.tick()
    clock.advance(_A_WHILE)
    _alarm(adapter)
    records = engine.tick()
    assert _one(records, "safety_alert").outcome is Outcome.ACTED
    assert _one(records, "away_shutdown").outcome is Outcome.LOST_ARBITRATION
    assert adapter.read_entity("light.kitchen").state == "on"


def test_sleep_mode_cannot_silence_the_alert(vocabulary: Vocabulary) -> None:
    """Sleep stands the household down; it does not stand the alarm down.

    A falsifying implementation that read the active mode the way the shutdown
    does would suppress the alert in the one mode a fire is most dangerous in,
    because everyone is asleep and the light is the warning.
    """
    engine, adapter, _ = _build(
        vocabulary,
        behaviours=_registry(SafetyAlertBehaviour()),
        entities={"binary_sensor.smoke_sensor": "on"},
        attributes={"binary_sensor.smoke_sensor": _SMOKE},
    )
    engine.modes.activate("sleep")
    record = _one(engine.tick(), "safety_alert")
    assert record.outcome is Outcome.ACTED
    assert adapter.read_entity("light.kitchen").state == "on"


def test_a_person_s_recent_hand_cannot_silence_the_alert(
    vocabulary: Vocabulary,
) -> None:
    """An override is in force and the alarm still reaches the light.

    A falsifying implementation that ran the alert through the ordinary
    suppression stages would report `overridden` for a house on fire, because the
    override is a person's hand on a lamp an hour ago and the alarm is a detector
    now. The override is asserted to *still stand* afterwards: the alert bypasses
    it, it does not cancel it, so a lamp somebody is holding keeps its exemption.
    """
    engine, adapter, _ = _build(
        vocabulary, behaviours=_registry(SafetyAlertBehaviour())
    )
    adapter.actuate("light.kitchen", "off", context=ChangeContext.user())
    _alarm(adapter)
    record = _one(engine.tick(), "safety_alert")
    assert record.outcome is Outcome.ACTED
    assert adapter.read_entity("light.kitchen").state == "on"
    assert engine.overrides.is_overridden("light.kitchen")


def test_the_same_proposal_without_the_flag_is_overridden(
    vocabulary: Vocabulary,
) -> None:
    """The control: an identical proposal that is not safety is suppressed.

    A falsifying implementation that never suppressed anything -- or a harness in
    which the override was never armed -- would make the test above pass for a
    reason that has nothing to do with the `safety` flag, and the flag's whole
    purpose would go unproven.
    """
    engine, adapter, _ = _build(vocabulary, behaviours=_registry(_LightOnBehaviour()))
    adapter.actuate("light.kitchen", "off", context=ChangeContext.user())
    record = _one(engine.tick(), "plain_on")
    assert record.outcome is Outcome.OVERRIDDEN
    assert adapter.read_entity("light.kitchen").state == "off"


def test_the_rate_limit_cannot_silence_the_alert(vocabulary: Vocabulary) -> None:
    """The light's allowance is already spent, and the alarm still writes it.

    A falsifying implementation that consulted the limiter before the safety flag
    would leave the alarm unlit because an earlier motion command had used the
    budget -- the alarm would be starved by the very automation it exists to
    override. The allowance is spent by a real motion command first, so the limit
    genuinely holds when the alarm arrives.
    """
    engine, adapter, _ = _build(
        vocabulary,
        behaviours=_registry(SafetyAlertBehaviour()),
        settings={RATE_LIMIT_BOUND_KEY: 1, "engine.rate_limit.window_seconds": 3600.0},
    )
    adapter.actuate("binary_sensor.kitchen_motion", "on", context=ChangeContext.world())
    assert _one(engine.tick(), "motion_lighting").outcome is Outcome.ACTED
    adapter.actuate(
        "binary_sensor.kitchen_motion", "off", context=ChangeContext.world()
    )
    adapter.actuate("light.kitchen", "off", context=ChangeContext.world())
    _alarm(adapter)
    record = _one(engine.tick(), "safety_alert")
    assert record.outcome is Outcome.ACTED
    assert adapter.read_entity("light.kitchen").state == "on"


def test_the_same_proposal_without_the_flag_is_rate_limited(
    vocabulary: Vocabulary,
) -> None:
    """The control: the same exhausted house suppresses a non-safety proposal.

    Without it, the test above could pass on a house whose limiter was never
    reached, and "the alert bypassed the limit" would be indistinguishable from
    "the limit did not apply".
    """
    engine, adapter, _ = _build(
        vocabulary,
        behaviours=_registry(_LightOnBehaviour()),
        settings={RATE_LIMIT_BOUND_KEY: 1, "engine.rate_limit.window_seconds": 3600.0},
    )
    adapter.actuate("binary_sensor.kitchen_motion", "on", context=ChangeContext.world())
    engine.tick()
    # Whoever won, the light moved, and light.kitchen's single allowance is spent.
    assert adapter.read_entity("light.kitchen").state == "on"
    adapter.actuate(
        "binary_sensor.kitchen_motion", "off", context=ChangeContext.world()
    )
    adapter.actuate("light.kitchen", "off", context=ChangeContext.world())
    assert _one(engine.tick(), "plain_on").outcome is Outcome.RATE_LIMITED
    assert adapter.read_entity("light.kitchen").state == "off"


def test_a_detector_that_is_not_alarming_raises_nothing(
    vocabulary: Vocabulary,
) -> None:
    """A clear detector makes the alert decline, and the record says which rule.

    A falsifying implementation that treated presence as alarm would light the
    house for every detector that exists, and the decline's `rule` is what keeps
    "the alert ran and found nothing" apart from "the alert never ran".
    """
    engine, adapter, _ = _build(
        vocabulary,
        behaviours=_registry(SafetyAlertBehaviour()),
        entities={"binary_sensor.smoke_sensor": "off"},
        attributes={"binary_sensor.smoke_sensor": _SMOKE},
    )
    record = _one(engine.tick(), "safety_alert")
    assert record.outcome is Outcome.DECLINED
    assert record.rule == "security.smoke_alert"
    assert _readings(record, HazardReading) == []
    assert adapter.read_entity("light.kitchen").state == "off"


@pytest.mark.parametrize(
    ("device_class", "kind"),
    [
        ("smoke", "smoke"),
        ("carbon_monoxide", "carbon_monoxide"),
        ("gas", "gas"),
        ("moisture", "leak"),
        ("water", "leak"),
    ],
)
def test_every_alert_class_is_recognised(device_class: str, kind: str) -> None:
    """Each named alert class is a hazard, and each keeps its own name.

    A falsifying implementation that collapsed the leak classes into smoke, or
    that recognised only the two the smoke tests happen to use, would name the
    wrong alarm in the record and hide which detector spoke.
    """
    view = EntityView(
        entity_id="binary_sensor.detector",
        state="on",
        attributes={"device_class": device_class},
        available=True,
        last_origin=ChangeOrigin.WORLD,
    )
    assert hazard_kind(view) == kind


def test_a_device_with_no_alert_class_is_not_a_hazard() -> None:
    """A motion sensor reading `on` is motion, not fire.

    A falsifying implementation that matched on state alone would treat every
    binary sensor that is on as an alarm, and the house would light up whenever
    somebody walked into a room.
    """
    view = EntityView(
        entity_id="binary_sensor.kitchen_motion",
        state="on",
        attributes={"device_class": "motion"},
        available=True,
        last_origin=ChangeOrigin.WORLD,
    )
    assert hazard_kind(view) is None


def test_an_unavailable_detector_raises_no_alert() -> None:
    """A dead detector is not an alarming one: unavailable is not `on`.

    A falsifying implementation that read the stale state of a detector an
    integration can no longer confirm would light the house on a reading nobody
    can trust. The unknown case belongs to the repairs (below), not to the alarm.
    """
    view = EntityView(
        entity_id="binary_sensor.smoke_sensor",
        state="on",
        attributes={"device_class": "smoke"},
        available=False,
        last_origin=ChangeOrigin.FAULT,
    )
    assert hazard_kind(view) is None


def test_the_engine_finds_every_alarming_detector_in_the_house(
    vocabulary: Vocabulary,
) -> None:
    """`Engine.hazards` scans the house and orders what it finds by entity id.

    A falsifying implementation that read hazards from a slot would find none --
    the vocabulary has no smoke slot -- and one that enumerated unordered would
    make the record's input order depend on the registry's iteration rather than
    on a rule.
    """
    engine, adapter, _ = _build(vocabulary)
    _alarm(adapter, "binary_sensor.smoke_sensor")
    _alarm(adapter, "binary_sensor.upstairs_smoke")
    # A motion sensor reading `on` is present the whole time and must not appear:
    # it is the device class, not the state, that makes a detector a hazard.
    adapter.actuate("binary_sensor.kitchen_motion", "on", context=ChangeContext.world())
    assert engine.hazards() == (
        Hazard(entity_id="binary_sensor.smoke_sensor", kind="smoke"),
        Hazard(entity_id="binary_sensor.upstairs_smoke", kind="smoke"),
    )


# --------------------------------------------------------------------------
# 2. Nothing the engine decides reaches an egress domain
# --------------------------------------------------------------------------


def test_a_behaviour_that_requires_a_lock_cannot_unlock_it(
    vocabulary: Vocabulary,
) -> None:
    """Declaring the row, the slot and the device is still not permission.

    A falsifying implementation that honoured a pack's declaration -- its corpus
    row, its required slot -- would let a shipped or downloaded pack unlock a
    front door by saying it was about locks, which is the "declare its way to one"
    the audit forbids. The door's state is the load-bearing assertion.
    """
    engine, adapter, _ = _build(
        vocabulary,
        layout={"kitchen": {**KITCHEN, "lock": "lock.front_door"}},
        behaviours=_registry(_PackUnlockBehaviour()),
    )
    record = _one(engine.tick(), "pack_unlocker")
    assert record.outcome is Outcome.REFUSED_UNSAFE
    assert [command.action for command in record.commands] == ["unlocked"]
    assert record.state_delta == ()
    assert adapter.read_entity("lock.front_door").state == "locked"


def test_a_pack_cannot_declare_its_way_to_an_open_garage(
    vocabulary: Vocabulary,
) -> None:
    """A garage cover is the second egress domain, and it is refused the same way.

    A falsifying implementation that guarded only the `lock` domain would leave
    every garage door in the product openable by a behaviour, and the audit names
    garages explicitly because a cover's `open` is spelled like a safe light's.
    """
    engine, adapter, _ = _build(
        vocabulary,
        layout={"kitchen": {**KITCHEN, "cover": "cover.garage"}},
        behaviours=_registry(_PackGarageBehaviour()),
    )
    record = _one(engine.tick(), "pack_garage")
    assert record.outcome is Outcome.REFUSED_UNSAFE
    assert adapter.read_entity("cover.garage").state == "closed"


def test_locking_a_door_from_a_behaviour_is_allowed(vocabulary: Vocabulary) -> None:
    """Securing a door is not an egress action, so the veto permits it.

    A falsifying implementation that refused every command naming a `lock` would
    make every auto-lock in the corpus unimplementable, and the rule the audit
    states -- about unlocking and opening -- would be enforced as a rule about
    locks in general.
    """
    engine, adapter, _ = _build(
        vocabulary,
        layout={"kitchen": {**KITCHEN, "lock": "lock.front_door"}},
        behaviours=_registry(_PackRelockBehaviour()),
        entities={"lock.front_door": "unlocked"},
    )
    record = _one(engine.tick(), "pack_relocker")
    assert record.outcome is Outcome.ACTED
    assert adapter.read_entity("lock.front_door").state == "locked"


def test_a_safety_command_that_reaches_for_a_lock_is_still_refused(
    vocabulary: Vocabulary,
) -> None:
    """The two halves of the audit do not cancel: safety is not a key to a door.

    A falsifying implementation that let the `safety` flag skip the veto -- the
    obvious way to make the override and rate-limit bypass look uniform -- would
    turn the alarm path into the one door-opener in the product, which is a worse
    hole than either rule alone.
    """
    engine, adapter, _ = _build(
        vocabulary,
        layout={"kitchen": {**KITCHEN, "lock": "lock.front_door"}},
        behaviours=_registry(
            _PackUnlockBehaviour(identifier="pack_safety_unlocker", safety=True)
        ),
    )
    record = _one(engine.tick(), "pack_safety_unlocker")
    assert record.outcome is Outcome.REFUSED_UNSAFE
    assert adapter.read_entity("lock.front_door").state == "locked"


def test_only_a_user_origin_command_may_unlock() -> None:
    """The veto fails closed: only a person's own act is admitted.

    A falsifying implementation that permitted an unknown origin -- treating
    "not recognisably a behaviour" as "probably a person" -- would open a door for
    a context that carries no origin at all, which is the ambiguous case the rule
    exists to answer.
    """
    for entity_id, action in (
        ("lock.front_door", "unlocked"),
        ("cover.garage", "open"),
    ):
        assert refuses(entity_id, action, context=ChangeContext.engine())
        assert refuses(entity_id, action, context=ChangeContext.world())
        assert refuses(entity_id, action, context=None)
        assert not refuses(entity_id, action, context=ChangeContext.user())
    assert not refuses("light.kitchen", "on", context=ChangeContext.engine())


def test_no_shipped_behaviour_reaches_an_egress_domain(
    vocabulary: Vocabulary,
) -> None:
    """With a lock and a garage bound, a full away tick writes neither.

    A falsifying implementation that added an egress path to a shipped unit would
    be caught here even though every unit's own tests still passed, because the
    claim is about the registry as a whole and not about any one unit's rules.
    """
    engine, adapter, clock = _build(
        vocabulary,
        layout={
            "kitchen": {
                **KITCHEN,
                "lock": "lock.front_door",
                "cover": "cover.garage",
            }
        },
        entities={"light.kitchen": "on"},
    )
    engine.modes.activate("away")
    engine.tick()
    clock.advance(_A_WHILE)
    records = engine.tick()
    assert all(
        domain_of(change.entity_id) not in {"lock", "cover"}
        for record in records
        for change in record.state_delta
    )
    assert adapter.read_entity("lock.front_door").state == "locked"
    assert adapter.read_entity("cover.garage").state == "closed"


# --------------------------------------------------------------------------
# 3. A sensor that dies produces a fallback and a repair
# --------------------------------------------------------------------------


def test_a_dead_motion_sensor_is_recorded_as_a_repair(
    vocabulary: Vocabulary,
) -> None:
    """The dead device is named in the evaluation that met it.

    A falsifying implementation that treated silence as "clear" would leave a
    room apparently empty and the light dark, and nothing in the record would say
    a detector had died. The repair is the fact that makes the fallback
    explainable, and it names the room, the slot and the device.
    """
    engine, adapter, clock = _build(vocabulary)
    adapter.actuate("binary_sensor.kitchen_motion", "on", context=ChangeContext.world())
    engine.tick()
    adapter.set_availability(
        "binary_sensor.kitchen_motion", available=False, context=ChangeContext.fault()
    )
    clock.advance(_A_WHILE)
    record = _one(engine.tick(), "motion_lighting")
    assert _readings(record, Repair) == [
        Repair(
            room_id="kitchen",
            slot="motion_sensor",
            entity_id="binary_sensor.kitchen_motion",
        )
    ]
    assert record.outcome is Outcome.DECLINED


def test_a_dead_motion_sensor_holds_the_room_rather_than_darkening_it(
    vocabulary: Vocabulary,
) -> None:
    """The light stays on, because a sensor that died did not see the room empty.

    A falsifying implementation that ran the quiet timeout on an unreadable sensor
    would darken a room whose occupant is still in it, and the one moment the
    failure matters most -- somebody asleep under a light nobody can turn back on
    -- is the one it would fail. The `declined` outcome with `rule:
    lighting.motion_light_on` is the hold: the evaluation reached the on-half and
    would not act, which is different from the off-half ever having run.
    """
    engine, adapter, clock = _build(vocabulary)
    adapter.actuate("binary_sensor.kitchen_motion", "on", context=ChangeContext.world())
    engine.tick()
    adapter.set_availability(
        "binary_sensor.kitchen_motion", available=False, context=ChangeContext.fault()
    )
    clock.advance(_A_WHILE)
    record = _one(engine.tick(), "motion_lighting")
    assert record.outcome is Outcome.DECLINED
    assert record.rule == "lighting.motion_light_on"
    assert record.state_delta == ()
    assert adapter.read_entity("light.kitchen").state == "on"


def test_a_dead_sensor_is_not_a_quiet_room() -> None:
    """The registry refuses to call an unreadable slot quiet, and says no.

    A falsifying implementation that stored the unreadable reading as a clear one
    would make "we cannot see this room" and "this room is empty" the same fact,
    which is the two-mistakes-one-reading failure the dwell record's `known` flag
    exists to answer. An unobserved slot is asserted too, because "never watched"
    must not read as "empty" either.
    """
    registry = DwellRegistry()
    registry.observe("kitchen", "motion_sensor", active=False, at=NIGHT, known=False)
    later = NIGHT + timedelta(hours=1)
    assert not registry.quiet("kitchen", "motion_sensor", at=later, timeout=_QUIET)
    assert not registry.quiet("attic", "motion_sensor", at=later, timeout=_QUIET)
    registry.observe("kitchen", "motion_sensor", active=False, at=later, known=True)
    assert registry.quiet("kitchen", "motion_sensor", at=later, timeout=_QUIET)


def test_the_engine_reports_the_repairs_it_can_see(vocabulary: Vocabulary) -> None:
    """`Engine.repairs` names the dead device while it is dead, and stops when it is not.

    A falsifying implementation that derived repairs from stored state would keep
    reporting a device that had come back, and a person trained to ignore a repair
    that never clears is the same as one who never saw it.
    """
    engine, adapter, _ = _build(vocabulary)
    assert engine.repairs() == ()
    adapter.set_availability(
        "binary_sensor.kitchen_motion", available=False, context=ChangeContext.fault()
    )
    assert engine.repairs() == (
        Repair(
            room_id="kitchen",
            slot="motion_sensor",
            entity_id="binary_sensor.kitchen_motion",
        ),
    )
    adapter.set_availability(
        "binary_sensor.kitchen_motion", available=True, context=ChangeContext.world()
    )
    assert engine.repairs() == ()


def test_a_recovered_sensor_resumes_deciding(vocabulary: Vocabulary) -> None:
    """Once the detector reads again, the ordinary off-half fires as it would have.

    A falsifying implementation that left the room permanently "unknown" after an
    outage -- or that restarted the quiet period at the recovery -- would leave a
    light on forever after one fault, which is the failure the fallback would have
    traded the dark room for.
    """
    engine, adapter, clock = _build(vocabulary)
    adapter.actuate("binary_sensor.kitchen_motion", "on", context=ChangeContext.world())
    engine.tick()
    adapter.set_availability(
        "binary_sensor.kitchen_motion", available=False, context=ChangeContext.fault()
    )
    clock.advance(_A_WHILE)
    assert _one(engine.tick(), "motion_lighting").outcome is Outcome.DECLINED

    adapter.actuate(
        "binary_sensor.kitchen_motion", "off", context=ChangeContext.world()
    )
    adapter.set_availability(
        "binary_sensor.kitchen_motion", available=True, context=ChangeContext.world()
    )
    record = _one(engine.tick(), "motion_lighting")
    assert record.rule == "lighting.motion_light_off"
    assert record.outcome is Outcome.ACTED
    assert _readings(record, Repair) == []
    assert adapter.read_entity("light.kitchen").state == "off"
