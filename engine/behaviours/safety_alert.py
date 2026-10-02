"""The safety alert: a hazard turns the lights on, and nothing may silence it.

The first of the three claims `spec.txt`'s Phase 8 safety audit names -- "smoke/CO
and leak alerts bypass all modes, nothing auto-unlocks or opens garages,
sensor-death fallbacks". This unit is the alert: a smoke, CO or water-leak
detector that is alarming turns the house's lighting on so the warning reaches a
sleeping household, and no house mode, no person's recent hand on the light, no
rate limit and no better-prioritised behaviour may stop it.

Two facts make the response unsuppressible, and the unit owns neither of them.
It **never asks for a mode** -- there is no `mode_is_active` call below -- so
Sleep and Away cannot stand it down the way they stand down the shutdown; and the
command it proposes is marked `safety=True`, which is what the engine's tick
admits past the override and rate-limit stages and ranks above every behaviour in
arbitration (`engine/engine.py`, `engine/arbitration.py`). The split is deliberate:
this unit says *when to raise the alarm*, and the engine says *that an alarm is
raised whatever else wants the same light*. A second safety unit -- a CO siren, a
leak valve shutoff -- reaches the same unsuppressible path by marking its own
command, rather than by re-deriving the guarantee and getting it subtly different.

The hazard is recognised by device class and never by a slot (`engine/safety.py`).
The vocabulary has no smoke slot and must not grow one: a house that bound its
smoke detector to a slot could be reconfigured out of having a smoke alarm, while
a `binary_sensor` whose `device_class` is an alert class cannot. So
`ctx.hazards()` scans the house, and each alarming detector it finds is recorded as
a `HazardReading` in this evaluation's inputs, so the record names the detector the
light came on for rather than only the light.

The unit is house-scoped because a hazard is a fact about the whole house: a fire
in the kitchen is a reason to light every escape route at once, not the one room.
`priority` is declared high only for a reader's benefit -- the safety flag already
outranks every behaviour, and a number here could never be the thing that decided
it.
"""

from __future__ import annotations

from collections.abc import Mapping

from engine.behaviours.base import BehaviourContext, BehaviourScope

#: The rule this unit acts under: the corpus's smoke and fire alert. It is the
#: primary row, so its `scope` and `required_slots` are the unit's.
SMOKE_ALERT = "security.smoke_alert"

#: The rows this unit is derived from, primary first. The leak row is cited
#: because the same response answers a leak alarm -- a wet floor is a warning the
#: household wants lit for the same reason -- and it is the row that grants the
#: `light_group` optional slot alongside the primary.
CORPUS_ROWS = (SMOKE_ALERT, "security.water_leak_alert")

#: The unit declares no tunable of its own: whether a detector is alarming is a
#: fact about the device and not a number this unit may set. Declared as an empty
#: mapping rather than omitted so the shape is uniform with the other units and
#: the no-hardcoded-tunables check has one thing to read from every one of them.
DEFAULTS: Mapping[str, object] = {}

#: The state a light reads when the alert has lit it, and the state a detector
#: reads when it is alarming. Home Assistant's own spelling, and the fake's, so
#: the two implementations of the port agree without a translation.
_ACTIVE = "on"


class SafetyAlertBehaviour:
    """The unit: the house's alarms, and the house's lighting."""

    id = "safety_alert"
    corpus_rows = CORPUS_ROWS
    scope = BehaviourScope.HOUSE
    required_slots = ()
    optional_slots = ("light_group",)
    #: Above every other unit's, for a reader's benefit only: the `safety` flag on
    #: the command is what actually outranks them, and this number is what this
    #: unit would rank by if the flag were ever dropped.
    priority = 100
    module: str | None = None
    enabled = False
    defaults = DEFAULTS

    def evaluate(self, ctx: BehaviourContext) -> None:
        """Light the house if any detector is alarming, and name the alert either way."""
        hazards = ctx.hazards()
        # The rule is named before the branch so that both an alarm answered and a
        # quiet house are the *same* concept looked for: a decline with `rule:
        # null` would read as an evaluation that reached no rule, which is a
        # different fact from "the alert ran and found nothing burning".
        ctx.matched(SMOKE_ALERT)
        if not hazards:
            return
        if ctx.binding("light_group").is_empty:
            # A house that bound no light group still has the alarm recorded: the
            # hazard readings are already in this evaluation's inputs, so the
            # record shows the alert was raised and the house had nowhere to say
            # it. Proposing would raise, because `propose` refuses an unbound slot
            # -- which is the correct refusal for a command with no target.
            return
        ctx.propose(slot="light_group", action=_ACTIVE, rule=SMOKE_ALERT, safety=True)
