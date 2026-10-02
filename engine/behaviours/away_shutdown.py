"""Away shutdown: interior lighting off when the house empties under away mode.

Task 7.2, derived from `lighting.away_shutdown` (`scope: house`,
`required_slots: [light_group]`) with `modes.house_away` and
`presence.house_emptied` cited for the two gates. Both gates are required and
neither is sufficient: a house that empties with nobody having set away mode is a
house whose occupants are still expected back, and a house in away mode with
somebody in it is a mistake away mode should not act on.

Three readings the requirement leaves to this module are settled here and stated
because a reader would otherwise have to infer them:

- **The mode is the engine's, not a device's.** The home's state is the engine's
  own variable, so this behaviour requires no slot for it and a house binds none:
  the gate is `ctx.mode_is_active`, which reads the `ModeSet` whose
  `exclusive_group` is what makes away exclusive with home (`engine-core`,
  "House modes are mutually exclusive within an exclusive group"). Reading it off
  an entity would let a stale `input_select` disagree with the modes every other
  behaviour is gated on, and would make a person bind a device to say something
  the engine already knows.
- **Emptiness is derived from the rooms, not from a device.** No slot in the
  vocabulary reports a house's emptiness, so `ctx.house_is_empty()` is the
  engine's own claim about the rooms it observes, recorded as a `HousePresence`
  so the claim shows which rooms it was made from. `presence.house_emptied` is
  that derivation, and it is the same dwell mechanism `room_emptied` uses.
- **A house that is already dark is a decline, not a command.** Proposing `off`
  to a house whose lights are all off would append an `acted` record with an
  empty state delta, and a scenario asserting that the shutdown *did* something
  could not tell it from the shutdown firing on a house with nothing to shut down.

This unit commands `light_group` bindings and nothing else. It is a lighting
shutdown, not an egress one: no path through it can name a `lock` or a `cover`,
which is the first-behaviours check that no first behaviour reaches the second
product rule's domains.
"""

from __future__ import annotations

from collections.abc import Mapping

from engine.adapter import EntityView
from engine.behaviours.base import BehaviourContext, BehaviourScope
from engine.binding import Reduction

#: The rule this unit acts under, and the mode and signal that gate it.
AWAY_SHUTDOWN = "lighting.away_shutdown"
AWAY_MODE = "away"

#: The rows this unit is derived from, primary first.
CORPUS_ROWS = (AWAY_SHUTDOWN, "modes.house_away", "presence.house_emptied")

#: The unit declares no tunable of its own. Every number it reads -- the quiet
#: timeout that decides emptiness -- belongs to the engine's presence mechanism
#: and resolves under the engine's own key, so a unit that invented a default
#: here would be a second place the same duration is decided. It is declared as
#: an empty mapping rather than omitted so the shape is uniform across units and
#: the no-hardcoded-tunables check has one thing to read from every one of them.
DEFAULTS: Mapping[str, object] = {}

_ACTIVE = "on"


class AwayShutdownBehaviour:
    """The unit: the house's lighting, its away mode and its emptiness."""

    id = "away_shutdown"
    corpus_rows = CORPUS_ROWS
    scope = BehaviourScope.HOUSE
    required_slots = ("light_group",)
    optional_slots = ()
    #: Above motion lighting's: when both want the same light, the house being
    #: empty outranks somebody walking through it.
    priority = 10
    module: str | None = None
    enabled = False
    defaults = DEFAULTS

    def evaluate(self, ctx: BehaviourContext) -> None:
        """Turn the house's lighting off, if it is empty and away and lit."""
        lights = ctx.read("light_group", Reduction.ANY)
        ctx.matched(AWAY_SHUTDOWN)

        if not ctx.mode_is_active(AWAY_MODE):
            return
        if not ctx.house_is_empty():
            return
        if not any(_is_on(view) for view in ctx.views(lights)):
            return
        ctx.propose(slot="light_group", action="off", rule=AWAY_SHUTDOWN)


def _is_on(view: EntityView) -> bool:
    """Whether one member of the house's light group is on.

    Availability is not consulted: the question is whether there is a light to
    shut down, and a lamp an integration cannot confirm still reads the state it
    was left in.
    """
    return view.state == _ACTIVE
