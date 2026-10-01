"""The engine, ticking on Home Assistant's own event loop.

Everything else in this integration *shows* a house: the selects and switches
record a choice on `RoomRuntime`, the occupancy sensor reads the bound device,
and none of them moves a light. This module is the part that moves one. It drives
the session `host.py` built (`ha_adapter.live.LiveSession`) from the two things
Home Assistant gives a component -- a periodic callback and state changes -- so
that a room's bound motion and lux sensors drive its bound lights through the
engine's decisions and nothing else's.

**There is exactly one engine, and the session owns it.** An earlier version of
this module composed its own house from `ha_adapter.composition` and the config
entry, which meant the panel's commands would have edited a *different* session
from the one deciding. The tick and the panel must drive one object, so the
composition moved into `host.async_setup_host` and this module was reduced to
scheduling: it holds the host, and `host.session` is what it ticks.

**Why the tick is a callback and not a task per event.** The engine is a pure
function of the state it starts from (`engine/engine.py`): one tick reads the
house, decides, and writes. So the *when* is Home Assistant's and the *what* is
the engine's, and this module's whole job is to choose the when. Two triggers,
and each earns its place: a state change on an entity the engine reads makes the
engine react to a person immediately rather than at the next interval, and the
interval makes it notice the things no event announces -- chiefly that a room has
been quiet long enough to turn its light off, which is a fact about the passage
of time and not about any device's state.

**The tick is also the publish.** The engine's decision log notifies nobody
(`engine/decision_log.py`: `append` writes and stops), so the activity stream the
panel subscribes to is fed from here -- after each tick, which is the only moment
the log can have grown. A publish on any other schedule would be guessing.

**What is watched, and why not everything.** Only the entities the engine
*reads*. An entity the engine writes is not watched, because a state change this
component caused would wake this component again -- a tick that actuates a light
would schedule the tick after it, for as long as the loop held -- and because the
engine's own write is already in its decision log. The rule is drawn from the
domain rather than from a slot name so it needs no maintenance when a slot is
added: a `binary_sensor` or `sensor` in a room is an input, and a `light` is the
thing being decided about.

**A tick never raises into the event loop.** One malformed room, one adapter
refusal, and Home Assistant would otherwise log a traceback from a timer and keep
calling it. The failure is logged with the traceback and the tick is abandoned;
the next interval tries again, which is the honest behaviour for a house whose
devices are still coming up.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timedelta
from functools import partial

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_interval,
)

from .host import OpenHouseHost, RoomRef
from .runtime import OpenHouseRuntime, RoomRuntime

__all__ = ["HomeAutomation", "async_setup_automation"]

_LOGGER = logging.getLogger(__name__)

#: How often the engine is ticked when nothing has changed. It bounds how late a
#: quiet room's light can turn off, and nothing else: the engine's own quiet
#: timeout is the five minutes `engine.presence.quiet_timeout_seconds` defaults
#: to, and a tick every half minute notices the timeout within half a minute of
#: it being met. A shorter interval buys precision nobody can perceive at the
#: cost of an evaluation per room per second.
TICK_INTERVAL = timedelta(seconds=30)

#: The entity domains the engine only *reads*: a change to one of these is a
#: reason to decide, never a decision this component made. `light` and the other
#: commandable domains are absent on purpose (see the module docstring).
_INPUT_DOMAINS = frozenset({"binary_sensor", "sensor"})


class HomeAutomation:
    """The live session, and the Home Assistant subscriptions that drive it."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        runtime: OpenHouseRuntime,
        host: OpenHouseHost,
    ) -> None:
        self._hass = hass
        self._entry = entry
        self._runtime = runtime
        self._host = host
        #: The rooms keyed by the subentry that describes them, because a
        #: `RoomRuntime` change arrives naming its subentry and the session speaks
        #: the engine's room ids (`host.RoomRef` carries both, which is why it
        #: exists).
        self._by_subentry: dict[str, RoomRef] = {
            room.subentry_id: room for room in host.rooms.values()
        }
        self._unsubscribers: list[Callable[[], None]] = []
        #: True while a tick is running, so a state change this component's own
        #: actuation caused cannot re-enter the engine before it has finished.
        self._ticking = False

    def async_start(self) -> None:
        """Subscribe to the interval and to every input the engine reads."""
        self._unsubscribers.append(
            async_track_time_interval(self._hass, self._tick, TICK_INTERVAL)
        )
        watched = sorted(
            {
                entity_id
                for room in self._host.rooms.values()
                for entity_id in _inputs(room)
            }
        )
        if watched:
            self._unsubscribers.append(
                async_track_state_change_event(self._hass, watched, self._input_changed)
            )
        # A room's switch or mode select writes `RoomRuntime` and redraws its
        # entities; the engine reads that state, so the same fan-out is what tells
        # the session the choice moved rather than only the screen.
        for subentry_id, room in self._runtime.rooms.items():
            room.listen(partial(self._room_changed, subentry_id, room))

    @callback
    def async_stop(self) -> None:
        """Drop every subscription. Idempotent, because unload can run twice."""
        for unsubscribe in self._unsubscribers:
            unsubscribe()
        self._unsubscribers.clear()

    # -- Triggers -----------------------------------------------------------

    @callback
    def _input_changed(self, event: Event) -> None:
        """A sensor the engine reads moved; decide again now rather than later.

        The event's instant is deliberately not used. The engine reads its own
        clock, and `Event` carries `time_fired` while the port's readings carry
        the state-change time -- passing one through as if it were the other is
        how a tick ends up judging a dwell against the wrong instant. The engine's
        clock is the single source of "now", so the event is only the signal.
        """
        self._tick()

    @callback
    def _room_changed(self, subentry_id: str, room: RoomRuntime) -> None:
        """A room's switch or mode select moved; apply it, then decide."""
        ref = self._by_subentry.get(subentry_id)
        if ref is None:
            return
        self._host.session.set_room_auto_lighting(ref.id, on=room.auto_lighting)
        if room.mode:
            self._host.session.set_house_mode(room.mode)
        self._tick()

    @callback
    def _tick(self, now: datetime | None = None) -> None:
        """Run one engine tick, absorbing a failure rather than losing the timer.

        `now` is accepted because `async_track_time_interval` hands its action the
        instant it fired; the engine reads its own clock, so the argument is not
        passed on -- naming it here rather than accepting `*_` keeps the two Home
        Assistant callbacks' signatures honest about what they receive.

        The publish is inside the `try` and after the tick, so a tick that raised
        publishes nothing: the records a half-finished evaluation appended are
        exactly the ones a person would act on wrongly, and the decision log is
        the panel's account of *what the house did*, which a failed tick did not
        finish doing.
        """
        if self._ticking:
            return
        self._ticking = True
        try:
            self._host.session.tick()
            self._host.async_publish_activity()
        except Exception as error:
            _LOGGER.exception(
                "the Open House engine could not complete a tick for entry %s "
                "(%s); the next interval will try again",
                self._entry.entry_id,
                type(error).__name__,
            )
        finally:
            self._ticking = False


async def async_setup_automation(
    hass: HomeAssistant,
    entry: ConfigEntry,
    runtime: OpenHouseRuntime,
    host: OpenHouseHost | None,
) -> HomeAutomation | None:
    """Start the session ticking, or answer `None` when there is no session.

    `None` rather than a failure, and for the reason `host.async_setup_host` gives
    about its own: a house with no engine is still a house whose entities a person
    can see and whose repairs explain what is missing, and refusing the entry would
    take even that away. The composed engine is not built here any more -- it is
    the session's, built once by the host, so that the panel's edits and the tick
    are the same object and cannot disagree.
    """
    if host is None or not runtime.rooms:
        return None
    automation = HomeAutomation(hass, entry, runtime, host)
    automation.async_start()
    return automation


def _inputs(room: RoomRef) -> tuple[str, ...]:
    """The entities of a room the engine only reads.

    Every bound entity whose domain is an input domain. A slot bound to the wrong
    domain is left out rather than watched, because watching it would only make
    the engine decide at a moment nothing it reads had changed.
    """
    return tuple(
        entity_id
        for entity_id in room.bindings.values()
        if entity_id.partition(".")[0] in _INPUT_DOMAINS
    )
