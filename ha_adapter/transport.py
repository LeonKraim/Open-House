"""The seam between `HAAdapter` and one Home Assistant instance.

`HAAdapter` implements the `HouseAdapter` port, and the port is synchronous and
free of any `homeassistant` type. Home Assistant is neither: its API is JSON over
HTTP and its objects are its own. So the adapter is written against this
*transport* -- the small, synchronous, Home-Assistant-shaped surface it needs --
and two things implement it: `RestTransport`, which speaks to a real instance,
and `FakeHaTransport` (`ha_adapter/testing.py`), which is an in-memory state
machine the contract suite drives without a network.

**Why a transport and not just the REST calls inline.** Two reasons, and the
second is the one that matters. The obvious one is testability: the contract
suite must run `HAAdapter` without a live container, and a seam is the only way
to do that without the suite knowing about HTTP. The load-bearing one is that
Home Assistant's data model and the port's differ in exactly one place -- `state`
and availability are one field there (`"unavailable"` is a state) and two here --
and that difference has to be reconciled somewhere. Reconciling it in the
transport keeps `HAAdapter` a straight projection and makes the reconciliation
the subject of its own tests.

**Home Assistant's shape, not ours.** `HaState` mirrors a Home Assistant state
object: a state string, a mapping of attributes, and the `user_id` of the context
that produced it. A transport is free to reconstruct the pieces Home Assistant
does not hand out directly -- most notably the last known state of an entity it
currently reports as `"unavailable"` -- as long as `HaState` is what a caller
sees.

**The `state` and `available` fields are separate by contract.** A transport
reports the entity's *state* and, independently, whether it is available. An
entity that is unavailable keeps whatever state it last reported; a transport
that conflated the two would make `"unavailable"` a state the engine could act
on, which is the failure the port's `EntityView` exists to prevent
(`house-adapter`: unavailable is not off).

**`attributes=None` and `available=None` mean "leave unchanged".** A set
operation carries only what it means to change, so an actuation that writes a
state does not silently clear the attributes a read handed out, and a fault that
takes a device offline does not also wipe its state.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Protocol

__all__ = [
    "UNAVAILABLE_STATE",
    "HaApiError",
    "HaState",
    "HaTransport",
    "RestTransport",
]

#: The state string Home Assistant uses for an entity whose integration is not
#: reporting. It is a *state* on the wire and an *availability* through this
#: seam, and naming it once is what keeps the translation in one place.
UNAVAILABLE_STATE = "unavailable"


class HaApiError(Exception):
    """A transport could not complete an operation against Home Assistant.

    One type for a refused HTTP status, a body that is not the JSON it should
    be, and a socket that stopped answering, because every caller does the same
    thing with all three: report and stop. The message carries the status and the
    path so a failure names the call it came from rather than a stack frame.

    `status` is the HTTP status when there was one, and `None` when the failure
    was below HTTP (a socket that never answered). It is a field rather than
    something to parse out of the message, because `state` and `remove` distinguish
    a 404 -- "Home Assistant does not hold this", an answer -- from everything
    else, which is a failure.
    """

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True, slots=True)
class HaState:
    """One entity as Home Assistant presents it, in the port's terms.

    `state` is the entity's state and `available` is whether Home Assistant can
    currently vouch for it -- the two fields the port keeps apart. `user_id` is
    the `context.user_id` of the change that last wrote the entity, or `None`
    when the write had no user behind it (an automation, a restart, an
    integration). It is the raw fact `HAAdapter` turns into a `ChangeOrigin`; the
    transport does not decide the origin, because only the adapter knows which
    writes were its own.
    """

    entity_id: str
    state: str
    attributes: Mapping[str, object]
    available: bool
    user_id: str | None = None


class HaTransport(Protocol):
    """What `HAAdapter` needs from one Home Assistant instance.

    Four operations, and the set is closed for the same reason the port's is:
    every one is a thing the adapter cannot do for itself. Reads come from the
    house; writes go to it; and there is no operation that names a slot, a mode
    or a behaviour, because the adapter is the only layer that knows those.
    """

    def states(self) -> Sequence[HaState]:
        """Every entity the transport holds, in a stable order."""
        ...

    def state(self, entity_id: str) -> HaState | None:
        """One entity, or `None` when the transport does not hold it."""
        ...

    def set_state(
        self,
        entity_id: str,
        state: str,
        *,
        attributes: Mapping[str, object] | None = None,
        available: bool | None = None,
        user_id: str | None = None,
    ) -> HaState:
        """Write `state`, and whichever of attributes/availability are given.

        `attributes=None` and `available=None` leave those parts of the entity
        untouched. The entity is created when it does not exist, so the port's
        `add_entity` and `actuate` are both this one call.
        """
        ...

    def remove(self, entity_id: str) -> bool:
        """Remove an entity, returning whether it was there to remove."""
        ...


@dataclass(slots=True)
class RestTransport:
    """A transport over Home Assistant's REST API.

    Four endpoints, all documented and all verified against the live container
    this project ships (`docker/docker-compose.yml`, Home Assistant 2026.9.4):

    - ``GET /api/states`` and ``GET /api/states/<entity_id>`` for reads.
    - ``POST /api/states/<entity_id>`` for writes, which Home Assistant accepts
      for any entity and which creates one that does not exist. This is the
      endpoint that lets the port's `add_entity` and `actuate` both be honest
      against a real instance.
    - ``DELETE /api/states/<entity_id>`` for `remove_entity`.

    **Why `POST /api/states` rather than a domain service call.** A service call
    is how a real device is driven, but it only exists for domains Home Assistant
    has a platform for, and the port must apply *any* write it is handed and
    re-decide nothing (`house-adapter`: the safety veto is the engine's). The
    state endpoint applies exactly what it is given, for any domain, which is the
    port's requirement. The cost is real and stated rather than hidden: a write
    to a device-backed entity sets that entity's state until the device reports
    again, so this transport is the right one for a harness and for a
    state-carrying house, and a device-driving transport is a later addition --
    it is a different `HaTransport`, not a change to the adapter.

    **Availability on the wire is a state.** Home Assistant has no availability
    bit, so an unavailable entity is the one whose state is `"unavailable"`, and
    it does not remember what it read before. This transport keeps that memory --
    the last non-unavailable state and attributes per entity -- so that a read
    through the seam can report the last known state beside `available=False`,
    which is what the port requires. `HaState.available` is what callers see; the
    wire's `"unavailable"` never leaves this class.
    """

    base_url: str
    token: str
    timeout: float = 30.0
    #: The last state and attributes each entity reported while available, so an
    #: entity Home Assistant now calls `"unavailable"` can still be read in the
    #: port's terms. Keyed by entity id; the values are the two facts the wire
    #: forgets.
    _last_known: dict[str, tuple[str, Mapping[str, object]]] = field(
        default_factory=dict, init=False
    )
    #: The entities Home Assistant currently reports as `"unavailable"`. Kept
    #: beside `_last_known` because the port's `actuate` must not restore an
    #: entity's availability -- a write to an unavailable entity changes its last
    #: known state and leaves it unavailable -- and the wire, which has no
    #: availability field, cannot say that on its own.
    _unavailable: set[str] = field(default_factory=set, init=False)

    # -- Reads --------------------------------------------------------------

    def states(self) -> Sequence[HaState]:
        """Every entity, sorted by id so two enumerations of one house agree."""
        raw = self._request("GET", "/api/states")
        if not isinstance(raw, list):
            raise HaApiError("GET /api/states did not return a list of states")
        parsed = [self._to_state(entry) for entry in raw]
        return tuple(sorted(parsed, key=lambda state: state.entity_id))

    def state(self, entity_id: str) -> HaState | None:
        """One entity, or `None` when Home Assistant answers 404 for it."""
        try:
            raw = self._request("GET", f"/api/states/{entity_id}")
        except HaApiError as error:
            if error.status == 404:
                return None
            raise
        return self._to_state(raw)

    # -- Writes -------------------------------------------------------------

    def set_state(
        self,
        entity_id: str,
        state: str,
        *,
        attributes: Mapping[str, object] | None = None,
        available: bool | None = None,
        user_id: str | None = None,
    ) -> HaState:
        """Write `state` through `POST /api/states/<entity_id>`.

        `user_id` is accepted for interface parity and deliberately ignored: Home
        Assistant stamps the write with the authenticated token's own user, so
        the value a caller here could pass would be overwritten anyway. The
        adapter does not rely on it -- it records which writes were its own.
        """
        del user_id
        previous = self._last_known.get(entity_id)
        if available is False or (available is None and entity_id in self._unavailable):
            # The port says an unavailable entity keeps its last known state, and
            # that a write to it does not restore availability. The wire has one
            # field for both facts, so the write is `"unavailable"` and the state
            # the caller wrote lives in the memory a read reports from.
            attributes_of_write = self._attributes_of(previous, attributes)
            self._last_known[entity_id] = (state, MappingProxyType(attributes_of_write))
            self._unavailable.add(entity_id)
            self._request(
                "POST",
                f"/api/states/{entity_id}",
                {"state": UNAVAILABLE_STATE, "attributes": attributes_of_write},
            )
            return HaState(
                entity_id=entity_id,
                state=state,
                attributes=MappingProxyType(attributes_of_write),
                available=False,
                user_id=None,
            )

        self._unavailable.discard(entity_id)
        payload: dict[str, object] = {"state": state}
        if attributes is not None:
            payload["attributes"] = dict(attributes)
        body = self._request("POST", f"/api/states/{entity_id}", payload)
        return self._to_state(body)

    def remove(self, entity_id: str) -> bool:
        """Delete the entity's state; `True` when it was there."""
        try:
            self._request("DELETE", f"/api/states/{entity_id}")
        except HaApiError as error:
            if error.status == 404:
                return False
            raise
        self._last_known.pop(entity_id, None)
        self._unavailable.discard(entity_id)
        return True

    # -- Internals ----------------------------------------------------------

    def _to_state(self, raw: object) -> HaState:
        """One wire state object, projected to `HaState`.

        The translation the module docstring describes lives here: a wire state of
        `"unavailable"` becomes `available=False` and the last known state, and a
        wire state of anything else updates the memory and becomes the state.
        """
        entry = _object(raw, "a state object")
        entity_id = _str(entry.get("entity_id"), "entity_id")
        wire_state = _str(entry.get("state"), "state")
        attributes = entry.get("attributes")
        attribute_mapping: Mapping[str, object] = (
            MappingProxyType(dict(attributes))
            if isinstance(attributes, dict)
            else MappingProxyType({})
        )
        user_id = self._user_id(entry.get("context"))

        if wire_state == UNAVAILABLE_STATE:
            self._unavailable.add(entity_id)
            last = self._last_known.get(entity_id)
            return HaState(
                entity_id=entity_id,
                state=last[0] if last is not None else UNAVAILABLE_STATE,
                attributes=last[1] if last is not None else attribute_mapping,
                available=False,
                user_id=user_id,
            )

        self._unavailable.discard(entity_id)
        self._last_known[entity_id] = (wire_state, attribute_mapping)
        return HaState(
            entity_id=entity_id,
            state=wire_state,
            attributes=attribute_mapping,
            available=True,
            user_id=user_id,
        )

    @staticmethod
    def _user_id(context: object) -> str | None:
        if not isinstance(context, dict):
            return None
        value = context.get("user_id")
        return value if isinstance(value, str) else None

    @staticmethod
    def _attributes_of(
        previous: tuple[str, Mapping[str, object]] | None,
        attributes: Mapping[str, object] | None,
    ) -> dict[str, object]:
        if attributes is not None:
            return dict(attributes)
        if previous is not None:
            return dict(previous[1])
        return {}

    def _request(
        self, method: str, path: str, payload: Mapping[str, object] | None = None
    ) -> object:
        """One HTTP call, returning the parsed body or raising `HaApiError`.

        A missing status on the error is `None`, so the `state` and `remove`
        methods can treat "Home Assistant does not hold this" as an answer rather
        than as a failure without matching on a message.
        """
        url = f"{self.base_url.rstrip('/')}{path}"
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(url, data=data, method=method)
        request.add_header("Authorization", f"Bearer {self.token}")
        request.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = response.read().decode("utf-8")
        except urllib.error.HTTPError as error:
            raise HaApiError(
                f"{method} {path} failed with HTTP {error.code}", status=error.code
            ) from error
        except OSError as error:
            raise HaApiError(f"{method} {path} failed: {error}") from error
        if not body:
            return {}
        try:
            return json.loads(body)
        except json.JSONDecodeError as error:
            raise HaApiError(
                f"{method} {path} returned a body that is not JSON: {error}"
            ) from error


def _object(value: object, what: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise HaApiError(f"{what} was not a JSON object")
    return value


def _str(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise HaApiError(f"a state object had no string {field_name!r}")
    return value
