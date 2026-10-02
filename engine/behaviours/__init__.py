"""The behaviour package, and the one registry the behaviour set comes from.

`default_behaviours()` is the single source of which units exist, for the reason
`first-behaviours` gives: the registry is what makes "a behaviour cannot be added
without also gaining its enable flag and its log actor" checkable rather than
hopeful. Each unit's `id` keys the mapping, and the same `id` keys the enable
flag the engine resolves, the entry arbitration ranks it under and the `actor`
every record it produces carries.

`behaviour_defaults()` is the other half: the built-in layer of the layered
resolver. A behaviour's numbers reach the configuration stack because the unit
declares them, not because a central table happens to hold them, so a unit that
grows a tunable cannot forget to make it configurable -- the check that no
behaviour reads a tunable from a module constant reads the same declaration.

Importing the units here rather than in `base.py` keeps the protocol free of its
implementations: `base.py` is the seam a Phase 2 pack interpreter is written
against, and a seam that named the three units of Phase 1 would not be one.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from engine.behaviours.away_shutdown import AwayShutdownBehaviour
from engine.behaviours.base import (
    Behaviour,
    BehaviourContext,
    BehaviourError,
    BehaviourScope,
    enable_key,
    module_enable_key,
    priority_key,
    scope_key,
)
from engine.behaviours.motion_lighting import MotionLightingBehaviour
from engine.behaviours.override import OverrideBehaviour
from engine.behaviours.safety_alert import SafetyAlertBehaviour

__all__ = [
    "AwayShutdownBehaviour",
    "Behaviour",
    "BehaviourContext",
    "BehaviourError",
    "BehaviourScope",
    "MotionLightingBehaviour",
    "OverrideBehaviour",
    "SafetyAlertBehaviour",
    "behaviour_defaults",
    "default_behaviours",
    "enable_key",
    "module_enable_key",
    "priority_key",
    "scope_key",
]


def default_behaviours() -> Mapping[str, Behaviour]:
    """The units this phase ships, keyed by `id` in ascending order.

    An ordered mapping rather than a set, because the engine evaluates them in
    this order and two runs of one scenario must append their records in the same
    sequence. The order is the tie-break the arbitration rule names anyway, so a
    reader who wonders why the record order is what it is finds the same answer
    in both places.
    """
    units = (
        MotionLightingBehaviour(),
        OverrideBehaviour(),
        AwayShutdownBehaviour(),
        SafetyAlertBehaviour(),
    )
    return {unit.id: unit for unit in sorted(units, key=lambda unit: unit.id)}


def behaviour_defaults(behaviours: Iterable[Behaviour]) -> Mapping[str, object]:
    """The built-in layer every unit contributes to the config resolver.

    Four keys per unit, and all four are the same kind of fact: the unit's
    declared answer, which any house or room layer may overrule. `enabled` is
    here rather than left to the engine because the product rule is that a fresh
    house runs nothing, and the value that makes that true should be readable in
    the unit that would otherwise run. `scope` is here for the same reason on the
    other axis: a unit that states what it is about should be readable as saying
    so, and the resolver's answer for an untouched house should be the unit's own
    word rather than an absence the engine fills in.

    A later unit whose defaults collide with an earlier one's is a unit reusing
    another's key, so the later value would silently win; the mapping is built
    with an explicit collision check rather than a plain `update` so that mistake
    fails where it is made.
    """
    defaults: dict[str, object] = {}
    for unit in behaviours:
        contributed = {
            enable_key(unit.id): unit.enabled,
            priority_key(unit.id): unit.priority,
            scope_key(unit.id): str(unit.scope),
            **unit.defaults,
        }
        for key, value in contributed.items():
            if key in defaults:
                raise BehaviourError(
                    f"two behaviours both declare the setting {key!r}: "
                    f"{defaults[key]!r} and {value!r}"
                )
            defaults[key] = value
    return defaults
