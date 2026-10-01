"""Override: a person's hand on a light outranks the engine's rule for it.

Task 7.1, derived from `lighting.room_light_manual_on` -- a room's lights coming
on at a manual cue -- which the corpus includes precisely because a house that
follows its rules over its occupants is the failure mode. The unit reads the
change *context* the port attaches to every change (`house-adapter`) and never a
timestamp or a guess: an `engine`-, `world`- or `fault`-origin change does not
create an override, which is what makes the rule about *who* acted rather than
about the fact that something did.

The unit does two things and deliberately not a third. It **registers** an
override when it finds a user's touch, and it **reports** an override the engine
released this tick, so the condition that ended a suppression appears in the log
at the moment acting resumed. It does **not** suppress: suppression is
`engine-core`'s stage, applied to whatever behaviour proposed a command to an
overridden entity, and a unit that hid its own commands would leave the
suppressed proposal invisible — which is the difference between "we chose not to
act" and "we never considered acting" that this phase's oracle exists to keep.

What "a user's touch" means is not decided here. The unit hands the registry
every entity's `last_origin` and lets it answer, because the port carries an
entity's last *writer* and not when it wrote: whether a user origin is a new
touch or the same one the registry has already accounted for is the question the
registry's own memory exists to answer, and a unit that filtered first would have
to duplicate it (`engine/overrides.py`).

`priority` is negative on purpose. This unit never proposes a command, so its
priority decides nothing today; it is declared below every commander anyway so
that a future pack that gives it one cannot silently outrank the behaviour it
exists to stand down.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import timedelta

from engine.behaviours.base import BehaviourContext, BehaviourScope
from engine.binding import Reduction
from engine.decision_log import OverrideNote

#: The row this unit is derived from: a manual cue making the automatic
#: behaviour for that room stand down.
MANUAL_ON = "lighting.room_light_manual_on"

CORPUS_ROWS = (MANUAL_ON,)

#: How long an override stands before `override_timeout` releases it. A tunable
#: like every other, so a house can hold a room longer than the default hour.
DURATION_KEY = "behaviour.override.duration_seconds"

DEFAULTS: Mapping[str, object] = {DURATION_KEY: 3600.0}


class OverrideBehaviour:
    """The unit: one room's light group, and whoever last touched it."""

    id = "override"
    corpus_rows = CORPUS_ROWS
    scope = BehaviourScope.ROOM
    required_slots = ("light_group",)
    optional_slots = ()
    priority = -1
    module: str | None = None
    enabled = False
    defaults = DEFAULTS

    def evaluate(self, ctx: BehaviourContext) -> None:
        """Register a user's touch, and report any override this tick released."""
        lights = ctx.read("light_group", Reduction.ANY)
        ctx.matched(MANUAL_ON)

        for entity_id, condition in ctx.lapsed():
            ctx.consult(OverrideNote(entity_id=entity_id, released=condition))

        duration = timedelta(seconds=_seconds(ctx))
        for view in ctx.views(lights):
            if ctx.is_overridden(view.entity_id):
                # Already in force. Re-noting would replace the record and push
                # its expiry forward every tick, so `override_timeout` could never
                # come true -- see `BehaviourContext.is_overridden`.
                continue
            ctx.register_override(
                view.entity_id, origin=view.last_origin, duration=duration
            )


def _seconds(ctx: BehaviourContext) -> float:
    """The override duration, resolved through the resolver and asserted numeric.

    The assertion lives on `ResolvedSetting`, so a layer supplying a string fails
    here naming the key and the layer rather than inside `timedelta`, where the
    traceback would name neither.
    """
    return ctx.setting(DURATION_KEY).number()
