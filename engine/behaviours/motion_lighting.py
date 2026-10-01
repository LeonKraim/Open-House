"""Motion lighting: on when somebody moves in the dark, off on a quiet timeout.

Task 7.0, derived from `catalog/behaviors.yaml`. The unit's primary row is
`lighting.motion_light_on` (`scope: room`, `required_slots: [motion_sensor,
light_group]`), and it cites three more rows rather than paraphrasing them: the
off half is `lighting.motion_light_off`, the optional lux slot is granted by
`lighting.room_light_dim` (the only cited row that declares `lux_sensor` as an
optional slot), and the sun branch is `lighting.solar_sun_light`. A behaviour
that read its slots or its scope anywhere else would be a second definition of a
concept the corpus exists to define once.

Three things about the evaluation are policy rather than mechanism, and each is
here rather than in `engine-core` because `design.md` D8 puts *what a behaviour
decides* in this package:

- **The dark test is a lux reading when bound and the sun when not.** Whichever
  branch decided is recorded as a `DarkSourceReading`, because the same light
  turning on for a different reason is exactly the difference a state-only
  oracle cannot see.
- **A bound lux sensor that cannot answer does not decide.** An unavailable
  sensor, or one whose state is not a number, falls back to the sun and says so.
  `catalog/edge_cases.yaml` names "a luminance sensor that stops reporting"; the
  alternative readings are worse -- deciding the room is dark because a dead
  sensor reads `unavailable` turns lights on in daylight, and deciding it is
  bright leaves a room dark -- so the branch with a value is taken and the
  fallback is recorded.
- **A bound motion sensor that cannot answer holds the room.** The engine's dwell
  registry refuses to call a room quiet while its sensor is unreadable, and the
  matching refusal here is to act at all: the evaluation records a `Repair`
  naming the dead device and declines rather than treating silence as "empty" and
  darkening the room. This is the same rule as the lux fallback one bullet up,
  applied to the slot the behaviour cannot run without.
- **The quiet timeout is measured from the last reading that showed motion**,
  which is the engine's dwell registry (`engine/dwell.py`) and not a timer this
  unit owns. A behaviour holding its own clock would be state outside the
  snapshot, and a restored run would decide differently from the run it resumed.

Whatever the evaluation concludes, it leaves one record: a decision to act names
the rule it acted under, and a decision *not* to act names the rule it was
evaluating via `ctx.matched`. A room already lit at the moment motion arrives is
the commonest decline and the one the phase's oracle exists to distinguish from
"the light was never on".
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import timedelta

from engine.adapter import EntityView
from engine.behaviours.base import BehaviourContext, BehaviourScope
from engine.binding import Reduction, RoomScope, SlotRead
from engine.decision_log import DarkSource, DarkSourceReading, Repair

#: The corpus concept this unit implements, in its two halves. Two rules from one
#: unit, recorded as distinct `rule` values, so a scenario distinguishes an
#: on-decision from an off-decision without reading a device.
MOTION_ON = "lighting.motion_light_on"
MOTION_OFF = "lighting.motion_light_off"

#: The rows this unit is derived from, primary first. Read by the conformance
#: check, which asserts the declared scope and required slots against the first
#: and the optional slots against the rest.
CORPUS_ROWS = (
    MOTION_ON,
    MOTION_OFF,
    "lighting.room_light_dim",
    "lighting.solar_sun_light",
)

#: The unit's config keys. The keys are named here because a key is this unit's
#: vocabulary; the *values* are in `DEFAULTS` below and reach the resolver as its
#: built-in layer, so no number in this module is read as a tunable.
QUIET_TIMEOUT_KEY = "behaviour.motion_lighting.quiet_timeout_seconds"
LUX_THRESHOLD_KEY = "behaviour.motion_lighting.lux_threshold"
SUN_ELEVATION_KEY = "behaviour.motion_lighting.sun_elevation_threshold"

#: The unit's declared defaults: the bottom of the resolver's stack. The quiet
#: timeout is five minutes because that is the corpus's commonest dwell; the lux
#: threshold is the usual "dusk" reading of a cheap indoor sensor; the elevation
#: threshold is civil twilight, below which the sky is dark enough to want a
#: light. All three are content rather than structure -- they are meant to be
#: tuned against a fixture, not defended.
DEFAULTS: Mapping[str, object] = {
    QUIET_TIMEOUT_KEY: 300.0,
    LUX_THRESHOLD_KEY: 20.0,
    SUN_ELEVATION_KEY: -6.0,
}

#: The state a binary sensor reads when it reports activity, and the state a
#: light reads when it is on. Home Assistant's own spelling, and the one the fake
#: writes, so the two implementations of the port agree without a translation.
_ACTIVE = "on"


class MotionLightingBehaviour:
    """The unit: one room's motion sensor and one room's light group."""

    #: The stable unit identity. It is *not* the corpus concept id -- the unit id
    #: says who decided and the concept id says which rule was matched -- which is
    #: why it is short and why `lighting.motion_light` would have been wrong.
    id = "motion_lighting"
    corpus_rows = CORPUS_ROWS
    scope = BehaviourScope.ROOM
    required_slots = ("motion_sensor", "light_group")
    optional_slots = ("lux_sensor",)
    #: Ranked below the shutdown: when both want a light, leaving the house
    #: unlit outranks lighting it.
    priority = 0
    #: Phase 2 owns modules; no family gates this unit yet.
    module: str | None = None
    #: The product rule: a fresh house runs nothing.
    enabled = False
    defaults = DEFAULTS

    def evaluate(self, ctx: BehaviourContext) -> None:
        """Turn the room on if somebody is moving in the dark, off if it has gone quiet."""
        motion = ctx.read("motion_sensor", Reduction.ANY)
        lights = ctx.read("light_group", Reduction.ANY)
        if not _readable(ctx, motion):
            # A motion sensor that has gone silent is not a room that is empty.
            # The engine records the repair, this evaluation holds the room as it
            # is rather than darkening it on a reading nobody can trust, and the
            # decline names the rule it was evaluating so the hold is a decision
            # in the log and not a behaviour that fell silent.
            ctx.matched(MOTION_ON)
            return
        moving = ctx.holds(motion, _reports_motion)
        lit = ctx.holds(lights, _is_on)

        if moving:
            # Every exit from the motion branch names the rule, including the
            # one where the room is bright enough to leave alone: a decline whose
            # `rule` was `null` would read as a record that reached no rule, and
            # "motion lighting looked and the room was already visible" is a
            # decision, not an absence of one.
            if lit:
                # The room is already what motion lighting would command, so it
                # proposes nothing -- but it still says which rule it evaluated,
                # because "already lit" and "the behaviour never ran" are the two
                # facts this phase's oracle exists to keep apart.
                ctx.matched(MOTION_ON)
                return
            if _is_dark(ctx):
                ctx.propose(slot="light_group", action=_ACTIVE, rule=MOTION_ON)
            else:
                ctx.matched(MOTION_ON)
            return

        if not lit:
            ctx.matched(MOTION_OFF)
            return
        timeout = timedelta(seconds=_number(ctx, QUIET_TIMEOUT_KEY))
        if ctx.quiet_for("motion_sensor", timeout):
            ctx.propose(slot="light_group", action="off", rule=MOTION_OFF)
        else:
            ctx.matched(MOTION_OFF)


def _readable(ctx: BehaviourContext, motion: SlotRead) -> bool:
    """Whether the room's motion can be read at all; if not, record the repair.

    The bound slot's members are checked for availability, and a slot whose every
    member is unavailable is the case this unit must not decide through: the
    dwell registry has already refused to call the room quiet (`engine.py`), and
    this is the matching refusal to act. The repair is recorded here rather than
    only in the engine's own `repairs()` so the record of the decision that met
    the dead sensor names it -- the difference between "we held because we could
    not see" and "we held for no stated reason". An unbound slot is not this
    case: it is a skip the engine records before this method is reached.
    """
    views = ctx.views(motion)
    if not views or any(view.available for view in views):
        return True
    scope = ctx.scope
    room_id = scope.room_id if isinstance(scope, RoomScope) else ""
    for view in views:
        ctx.consult(Repair(room_id=room_id, slot=motion.slot, entity_id=view.entity_id))
    return False


def _is_dark(ctx: BehaviourContext) -> bool:
    """Whether the room is dark enough to light, by the lux branch or the sun's.

    The lux slot is optional, so "bound" and "empty" are different from "reads
    zero": an unbound slot degrades the behaviour to the sun branch, which is the
    fallback `engine-core`'s optional-slot mechanism exists to make expressible as
    data rather than as a second behaviour.
    """
    lux = ctx.binding("lux_sensor")
    if not lux.is_empty:
        reading = ctx.read("lux_sensor", Reduction.ANY)
        value = _lux(ctx.views(reading))
        if value is not None:
            ctx.consult(DarkSourceReading(source=DarkSource.LUX, value=value))
            return value < _number(ctx, LUX_THRESHOLD_KEY)
        # A bound sensor that cannot answer: fall through to the sun rather than
        # deciding on a reading nobody can trust.
    elevation = ctx.sun_elevation()
    ctx.consult(DarkSourceReading(source=DarkSource.SUN, value=elevation))
    return elevation < _number(ctx, SUN_ELEVATION_KEY)


def _lux(views: Sequence[EntityView]) -> float | None:
    """The first member's reading, or `None` if no member can supply one.

    `None` covers both "the sensor is unavailable" and "the state is not a
    number", which are the same answer to the question the caller is asking: is
    there a luminance reading here to decide on.
    """
    for view in views:
        if not view.available:
            continue
        try:
            return float(view.state)
        except ValueError:
            continue
    return None


def _number(ctx: BehaviourContext, key: str) -> float:
    """A tunable, resolved through the layered resolver and asserted numeric.

    The assertion lives on `ResolvedSetting` rather than here so that every
    reader of a numeric tunable fails the same way, naming the key and the layer
    that set it: a layer that supplied a string would otherwise reach a threshold
    comparison and raise a `TypeError` from inside an arithmetic expression, a
    traceback naming neither.
    """
    return ctx.setting(key).number()


def _reports_motion(view: EntityView) -> bool:
    """Whether one member of a motion slot is reporting activity now.

    Availability is part of the predicate rather than ignored, because a sensor
    that stopped reporting keeps its last state in the fake (`house-adapter`: a
    read never fabricates a state) and a stale `on` would otherwise hold a room
    lit forever.
    """
    return view.available and view.state == _ACTIVE


def _is_on(view: EntityView) -> bool:
    """Whether one member of a light slot is on. Availability is not consulted.

    A light an integration cannot confirm still reads the state it was left in,
    and the question here is whether the room is *already* what motion lighting
    would command -- a room whose lamp is out of contact is not one to darken
    without cause.
    """
    return view.state == _ACTIVE
