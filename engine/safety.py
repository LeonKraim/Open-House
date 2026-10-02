"""The safety veto, and the two facts a hazard alert is recognised by.

Task 11.1's mechanism, and the second product rule's enforcement point
(`product-invariants`: "nothing in the system may ever auto-unlock a door or
auto-open a garage"). The rule is a ban on the *system* acting, not on the user:
a direct user action that unlocks a door is a person opening their own door, and
refusing it would be a house that will not obey. So the gate asks one question --
is this command an egress action, and if so, is its origin positively the
user's? -- and answers it before the command reaches the port.

The second half of the module is the hazard classification the safety-audit task
names: a smoke, CO or water-leak detector is an entity this module can recognise
without a slot for it. There is deliberately no `smoke_sensor` slot in the
vocabulary, and the corpus agrees -- `security.smoke_alert` requires no slot at
all and takes `light_group` as an optional one -- so a hazard is
matched by what the device *is* (a `binary_sensor` whose `device_class` is one of
the alert classes, reading `on`) rather than by what a house happened to bind it
to. A house that bound a smoke detector to a slot could be reconfigured out of
having a smoke alarm; a device class cannot.

It lives in the engine rather than in the adapter (`design.md` D9). An adapter
that refused such a command would have already let a behaviour *propose* it, so
the proposal would be invisible in the decision log and the guarantee would
become a runtime surprise instead of a recorded refusal. `house-adapter` states
the same boundary from the port's side: the port applies what it is asked to
apply, and the engine is where a command is judged.

**The gate fails closed.** Only a command whose origin is *positively* the
user's is permitted, so a context that is absent, or that carries an origin this
module does not recognise, is refused rather than waved through. That is the
whole of `engine-core`'s "when the origin of a command is unknown or ambiguous,
the command is refused": the safe answer is the one that does not depend on
recognising the unsafe case.

The action spelling is a small closed set per domain rather than a single verb,
because the port writes a *state* (`actuate(entity_id, state)`) while the
corpus speaks of unlocking and opening. A `lock` written to `on` is a lock
opened; a `cover` written to `open` is a garage door opened; and the alternative
-- matching only the literal strings `unlock` and `open` -- would pass a command
that writes `on` to a lock straight through, which is the mistake this module
exists to prevent.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from engine.adapter import ChangeContext, ChangeOrigin, EntityView, domain_of

#: The actions that constitute an egress action, by entity domain. A domain
#: absent from this mapping has no egress action at all -- a light has no
#: "unlock" -- so a command to it is never refused on this ground.
EGRESS_ACTIONS: Mapping[str, frozenset[str]] = {
    "lock": frozenset({"on", "unlocked", "unlock"}),
    "cover": frozenset({"on", "open", "opening"}),
}

#: The state a hazard detector reads when it is alarming. Home Assistant's own
#: spelling for an `on` binary sensor, and the fake's, so the two implementations
#: of the port agree without a translation.
ALARM_STATE = "on"

#: The device classes whose alarming reading is one of the alerts the safety
#: audit names, mapped to the kind the decision log records. `moisture` and
#: `water` are both the leak class because an integration spells a water contact
#: either way and the household's response is the same.
HAZARD_DEVICE_CLASSES: Mapping[str, str] = {
    "smoke": "smoke",
    "carbon_monoxide": "carbon_monoxide",
    "gas": "gas",
    "moisture": "leak",
    "water": "leak",
}


@dataclass(frozen=True, slots=True)
class Hazard:
    """One alarming device the engine found, and which alert it raises."""

    entity_id: str
    kind: str


def hazard_kind(view: EntityView) -> str | None:
    """Which hazard alert `view` raises, or `None` if it raises none.

    Three things must hold and each is a distinct reason a device is not
    alarming: it must be readable (`available` -- an unavailable detector's last
    state is not a reading), it must read `on` (a clear detector is not an
    alert however it is classed), and its `device_class` must be one of the alert
    classes. A device with no `device_class`, or one outside the set, is not a
    hazard whatever it is named -- a motion sensor reading `on` is motion, not
    fire.
    """
    if not view.available or view.state != ALARM_STATE:
        return None
    device_class = view.attributes.get("device_class")
    if not isinstance(device_class, str):
        return None
    return HAZARD_DEVICE_CLASSES.get(device_class)


def is_egress(domain: str, action: str) -> bool:
    """Whether `action` against a `domain` entity is an unlock or an open.

    A domain with no entry has no egress action, so the answer is `False` and no
    caller has to enumerate the domains that are safe.
    """
    return action in EGRESS_ACTIONS.get(domain, frozenset())


def refuses(entity_id: str, action: str, *, context: ChangeContext | None) -> bool:
    """Whether the veto refuses writing `action` to `entity_id`.

    The origin is asked first and the domain second only as an optimisation
    opportunity, not as an order the caller can observe: a command to a light is
    permitted whatever its origin, and a command that unlocks a lock is permitted
    only for a user. `context=None` is the ambiguous case and is refused.
    """
    if context is not None and context.origin is ChangeOrigin.USER:
        return False
    return is_egress(domain_of(entity_id), action)
