"""The mock device fleet: a fixture house published to Home Assistant over MQTT.

`docker/docker-compose.yml` has said since it was written that the broker is
"only the broker: nothing connects to it, and the fake fleet that drives it
arrives with the Phase 1 scenario runner". The runner arrived in Phase 1; this is
that fleet, and it is what makes the container the *place* the brief asked for
rather than a broker beside it.

**What it is.** One fixture house -- `sim.fixtures.build_fixture`, so the same
devices every scenario is written against -- announced to a real Home Assistant
through MQTT discovery. Each advertised entity gets a state topic, an
availability topic and (where the domain takes a command) a command topic HA
writes to; a command HA sends is applied to the fixture's adapter through the
`HouseAdapter` port, and the resulting state is republished. So a person can open
Home Assistant, see the house the scenarios see, and press things.

**Why discovery rather than a custom component.** `catalog/integrations.yaml`
records `mqtt` as a `require` disposition for all four merged repositories, and
the broker is already part of the stack. Discovery is the integration Home
Assistant already ships: it is how the four reference configurations reach their
own devices, so the mock house arrives the way a real one does, and the wiring
that has to be right is the wiring a real install would exercise. A custom
component would put a second integration in the path of every test.

**Why a separate process.** `sim/` may not import `socket` and every scenario run
executes under `tools/netguard.py`'s guard, which fails a run the moment one
opens. So the fleet cannot be a `sim/` module and cannot run *inside* a scenario:
it is a process beside the container that drives the same fixtures the scenarios
do, and the guard keeps the two apart by construction rather than by convention.

**What it does not advertise, and why that is the honest answer.** A discovery
config is not a name and a topic: several platforms require fields that describe
the *device*, not the topic. HA's `select` requires an `options` list; `climate`
requires `modes` and a mode command; `media_player` requires the feature flags
whose commands exist; `vacuum` requires a state schema. A fixture device is built
by `add_entity(entity_id, INITIAL[domain], context=...)` and carries **no
attributes at all**, so for those platforms there is nothing truthful to put in
the required field -- an `options: []` is a select a person can never change, and
inventing a mode list would be the fleet asserting a product decision that
belongs to `catalog/`. So those domains are not advertised, and they are
**reported** in `Announcement.skipped` with the missing field named. A fleet that
announced a house of twenty and said nothing about the four it left out would be
indistinguishable from a house of twenty.

**The one judgement call.** A command arriving from Home Assistant is applied
with `ChangeContext.world()`. The fleet cannot tell a person pressing a button
from an automation firing, and the port is explicit that only the manual
user-action path may carry `user` -- "a false `user` would suppress exactly the
behaviour being asserted" (`sim/adapter.py`). So an ambiguous command takes the
origin that cannot suppress a behaviour, and the day the fleet can tell the two
apart is the day it says so.

Usage:
    python -m tools.ha.fleet --fixture minimal --host localhost
    python -m tools.ha.fleet --fixture messy --once      # announce and exit
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from engine.adapter import ChangeContext, domain_of
from engine.vocabulary import Vocabulary
from sim.fixtures import build_fixture
from tools.catalog import paths

from .mqtt import Client, Message, MqttError

if TYPE_CHECKING:
    from collections.abc import Mapping

    from sim.adapter import FakeHouseAdapter
    from sim.fixtures import Fixture

__all__ = [
    "Announcement",
    "Fleet",
    "Skip",
    "UnknownDomainError",
    "human_name",
]

#: The MQTT discovery prefix Home Assistant listens on by default, and the prefix
#: `docker/ha/configuration.yaml` declares. Named here rather than written twice,
#: because a fleet and a container that disagree about the prefix produce a house
#: with no entities and no error.
DISCOVERY_PREFIX = "homeassistant"

#: Where the fleet's own topics live. Every topic the fleet writes is under this,
#: so a broker shared with something else cannot have its messages mistaken for
#: this house's -- and so one subscription shows everything the fleet said.
TOPIC_PREFIX = "openhouse"

#: The entity domain each controlled slot addresses, projected onto the MQTT
#: discovery component Home Assistant will create. The two are not always the
#: same word, and the difference is the reason this table exists rather than the
#: domain being used directly: HA has no MQTT `input_boolean` platform, so a
#: house's `input_boolean` is discovered as a `switch`.
#:
#: Every platform here has a discovery shape that a device with no attributes
#: satisfies completely -- a state topic, and for the commandable ones the
#: payload words the fixture's adapter already holds. A domain whose shape is not
#: self-contained is in `_UNSUPPORTED` instead, never here with a guessed field.
_PLATFORMS: Mapping[str, str] = {
    "binary_sensor": "binary_sensor",
    "cover": "cover",
    "input_boolean": "switch",
    "light": "light",
    "lock": "lock",
    "sensor": "sensor",
    "switch": "switch",
}

#: The words a domain's state can be, as the discovery keys that declare them.
#:
#: This table exists because of a defect the first live run found and nothing
#: static could have: a `binary_sensor` advertised with only a state topic read
#: **`unknown`** in Home Assistant, because the platform's own defaults are
#: `ON`/`OFF` and the fixture's adapter holds `on`/`off`. A payload the platform
#: does not recognise is not an error it logs -- it is a state of `unknown`,
#: which looks exactly like a device that has never reported. So every domain
#: whose state words are not the platform's defaults declares them here, whether
#: or not the domain takes a command: a `binary_sensor` cannot be written to and
#: still has to be *read* correctly.
_PAYLOADS: Mapping[str, Mapping[str, str]] = {
    "binary_sensor": {"payload_on": "on", "payload_off": "off"},
    "light": {"payload_on": "on", "payload_off": "off"},
    "switch": {"payload_on": "on", "payload_off": "off"},
    "input_boolean": {"payload_on": "on", "payload_off": "off"},
    "cover": {
        "payload_open": "open",
        "payload_close": "closed",
        "payload_stop": "closed",
    },
    "lock": {"payload_lock": "locked", "payload_unlock": "unlocked"},
}

#: The domains Home Assistant can also *write* to, and so the ones that get a
#: command topic. A domain in `_PLATFORMS` and `_PAYLOADS` but not here is
#: advertised read-only -- HA shows it and cannot change it, which is the honest
#: shape for a sensor and for anything whose command vocabulary the fixture does
#: not model.
_COMMANDED: frozenset[str] = frozenset(
    {"light", "switch", "input_boolean", "cover", "lock"}
)

#: The domains the fleet does not advertise, each with the field that would have
#: to have a value and does not. Read as a list of things to model, not as a list
#: of things that do not work: every one of these becomes advertisable the day
#: the fixture's devices carry the attribute the platform asks for.
_UNSUPPORTED: Mapping[str, str] = {
    "input_select": "HA's select platform requires an `options` list, and the "
    "fixture's devices carry no attributes",
    "climate": "HA's climate platform requires a `modes` list and a mode command, "
    "and the fixture's devices carry no attributes",
    "media_player": "HA's media_player platform requires the feature flags whose "
    "commands exist, and the fixture's devices carry no attributes",
    "vacuum": "HA's vacuum platform requires an activity-state schema, and the "
    "fixture's devices carry no attributes",
    "person": "HA has no MQTT platform for a person; it is composed from device "
    "trackers rather than discovered",
}


class UnknownDomainError(Exception):
    """A fixture holds an entity whose domain no table here mentions.

    Raised rather than skipped, and raised rather than defaulted to `sensor`: an
    unmentioned domain is a domain nobody has decided about, and a fleet that
    guessed would advertise a house HA believes and the fixture does not mean.
    The fixture's domains are fixed by `catalog/slots.yaml`, so this is reachable
    only when a new slot lands and neither table has been told.
    """

    def __init__(self, entity_id: str, domain: str) -> None:
        self.entity_id = entity_id
        self.domain = domain
        known = ", ".join(sorted({*_PLATFORMS, *_UNSUPPORTED}))
        super().__init__(
            f"no decision for the domain {domain!r} (entity {entity_id!r}); the "
            f"domains this fleet knows are {known}"
        )


def human_name(entity_id: str) -> str:
    """A display name for an entity id: `sensor.bathroom_02_humidity` ->
    `Bathroom 02 Humidity`.

    Derived here rather than read from the fixture, because the fixture is a
    device list and carries no i18n: the names a person sees are the pack layer's
    business (`packs/`), and a mock fleet inventing display names for the engine
    would be a second naming authority. What this produces is honest about being
    a mechanical derivation from the id.
    """
    return entity_id.split(".", 1)[1].replace("_", " ").title()


@dataclass(frozen=True, slots=True)
class Skip:
    """One entity the fleet did not advertise, and the field it was missing."""

    entity_id: str
    reason: str


@dataclass(frozen=True, slots=True)
class Announcement:
    """What one `announce` did: the entities it advertised, and what it left out.

    The two are disjoint by construction and their union is the fixture's entity
    list, which is the claim "no device is dropped in silence" -- a claim about a
    partition, so a reader can check it without a second reading of the loop.
    """

    advertised: tuple[str, ...] = ()
    skipped: tuple[Skip, ...] = ()

    def to_document(self) -> dict[str, object]:
        """The announcement as a plain value, for the CLI to print."""
        return {
            "advertised": list(self.advertised),
            "skipped": [
                {"entity_id": skip.entity_id, "reason": skip.reason}
                for skip in self.skipped
            ],
        }


@dataclass(slots=True)
class Fleet:
    """One fixture house, announced to one broker through one client."""

    fixture: Fixture
    client: Client
    #: The prefix discovery topics carry. A parameter so a caller can use a
    #: non-default discovery prefix without editing the fleet, and fixed to the
    #: container's declared prefix by default.
    discovery_prefix: str = DISCOVERY_PREFIX
    topic_prefix: str = TOPIC_PREFIX
    #: The entity ids announced, filled by `announce` and driven by `serve`.
    _advertised: list[str] = field(default_factory=list[str])
    #: entity id -> (device identifier, device name), built once from the rooms.
    _owners: Mapping[str, tuple[str, str]] | None = field(default=None, repr=False)
    #: entity id -> the area its room is filed under, built once from the rooms.
    _areas: Mapping[str, str] | None = field(default=None, repr=False)

    @property
    def adapter(self) -> FakeHouseAdapter:
        """The fake house the commands are applied to."""
        return self.fixture.adapter

    def announce(self) -> Announcement:
        """Publish discovery, state and availability for every entity it can.

        Discovery and state are both retained, in that order per entity, so a
        Home Assistant that restarts after the fleet announced finds a complete
        house -- a retained discovery config referring to a state topic whose
        value was never retained would show every entity as unknown.
        """
        advertised: list[str] = []
        skipped: list[Skip] = []
        for entity_id in self.fixture.entities():
            domain = domain_of(entity_id)
            if domain in _UNSUPPORTED:
                skipped.append(Skip(entity_id, _UNSUPPORTED[domain]))
                continue
            if domain not in _PLATFORMS:
                raise UnknownDomainError(entity_id, domain)
            self.client.publish(
                self._discovery_topic(domain, entity_id),
                json.dumps(self._config(domain, entity_id), sort_keys=True),
                retain=True,
            )
            self._publish_state(entity_id)
            advertised.append(entity_id)
        self._advertised = advertised
        return Announcement(advertised=tuple(advertised), skipped=tuple(skipped))

    def subscribe(self) -> None:
        """Subscribe to every announced entity's command topic, in one filter.

        One wildcard rather than one subscription per entity: the topic carries
        the entity id, so the filter that matches all of them is a single string,
        and a subscription that had to be reissued whenever the house changed
        would be state the caller has to keep.
        """
        self.client.subscribe(f"{self.topic_prefix}/+/set")

    def serve(
        self, *, timeout: float = 1.0, refresh: float = 5.0, ping: float = 20.0
    ) -> None:
        """Apply commands until the broker stops answering.

        Reads with a short timeout so the loop can republish on a cadence: the
        fixture is driven in-process too -- a scenario, a test, a hand at the
        console -- and nothing tells the broker about a change the fleet did not
        make. Republishing every announced state on `refresh` is what keeps Home
        Assistant's view of the house in step with the adapter's, and it is
        deliberately a poll rather than a publication the port would emit: the
        port has no change notification, so a fleet that waited to be told would
        never be told.

        **`ping` is why this loop survives at all.** The client writes its
        keepalive into CONNECT (`Client.keepalive`, 60 seconds) and the broker
        drops a connection that has said nothing for one and a half of them --
        so a fleet that only listened was disconnected about ninety seconds
        after it announced, taking every device in the house offline mid-run.
        `Client.ping` exists and says in its own docstring that "the caller's
        loop decides when"; this is that loop, and until this argument existed
        nobody decided. The interval is a third of the keepalive, which leaves
        two chances to be late without being disconnected.
        """
        refresh_at = time.monotonic() + refresh
        ping_at = time.monotonic() + ping
        while True:
            message = self.client.poll(timeout=timeout)
            if message is not None:
                self.handle(message)
            now = time.monotonic()
            if now >= ping_at:
                self.client.ping()
                ping_at = now + ping
            if now >= refresh_at:
                for entity_id in self._advertised:
                    self._publish_state(entity_id)
                refresh_at = now + refresh

    def handle(self, message: Message) -> str | None:
        """Apply one inbound message; return the entity it changed, or `None`.

        A message that is not a command for an announced entity is ignored rather
        than raising: the client holds one wildcard subscription, and a broker
        may relay a topic the fleet did not expect. Ignoring is right here for
        the same reason raising is right elsewhere -- this is not a caller error,
        it is a message for somebody else.
        """
        entity_id = self._entity_of(message.topic)
        if entity_id is None:
            return None
        self.adapter.actuate(entity_id, message.text, context=ChangeContext.world())
        self._publish_state(entity_id)
        return entity_id

    # -- Internals ----------------------------------------------------------

    def _entity_of(self, topic: str) -> str | None:
        """The entity a command topic addresses, or `None` if it addresses none."""
        head, _, tail = topic.partition("/")
        if head != self.topic_prefix or not tail.endswith("/set"):
            return None
        # The topic carries the entity id with its dot intact -- `light.foyer` --
        # so the round trip is exact and an id the fixture does not hold is
        # refused by the adapter rather than by a second check here.
        return tail[: -len("/set")] or None

    def _discovery_topic(self, domain: str, entity_id: str) -> str:
        platform = _PLATFORMS[domain]
        return f"{self.discovery_prefix}/{platform}/{self._slug(entity_id)}/config"

    def _slug(self, entity_id: str) -> str:
        """One topic-safe token for an entity id: `light.foyer` ->
        `openhouse_light_foyer`.

        A dot is not legal in a discovery `object_id`, and the fleet's own name is
        prefixed so that every entity it owns is visibly its own in a broker that
        may carry more than one house.
        """
        return f"openhouse_{entity_id.replace('.', '_')}"

    def _device(self, entity_id: str) -> dict[str, object]:
        """The Home Assistant device an entity belongs to: one per room.

        Not one device for the whole fleet, which is what this used to publish
        and what it cost. Home Assistant files an *entity* under an area only
        through its **device**, and the panel's device picker proposes only
        entities filed under the room's own area
        (`custom_components/open_house/views.py`'s `candidates` ->
        `async_entries_for_area`). A house announced as one device is therefore a
        house whose every entity can only ever be filed into one area, and a room
        set up against it can bind nothing at all -- every slot answers "No
        match" and the reason is invisible from the panel.

        A real house has a device per room, so this one does too: the room a
        binding names owns the device its entities appear on, and anything the
        house binds nowhere stays on a fleet-level device of its own.

        `suggested_area` is what puts that device in the room rather than merely
        naming it after one. Home Assistant reads it from a discovery payload and
        files the device under an area of that name, creating the area if the
        house has not made one -- which is how a real MQTT device arrives in the
        right room, and the difference between a mock house a person can set up
        and one where every slot answers "No match".
        """
        owner = self._owner_of().get(entity_id)
        identifier, name = (
            owner
            if owner is not None
            else ("openhouse_fleet", f"Open House mock fleet ({self.fixture.name})")
        )
        device: dict[str, object] = {
            "identifiers": [identifier],
            "name": name,
            "manufacturer": "Open House",
            "model": "sim",
        }
        area = self._area_of().get(entity_id)
        if area is not None:
            device["suggested_area"] = area
        return device

    def _area_of(self) -> Mapping[str, str]:
        """entity id -> the name of the area its room is filed under."""
        if self._areas is None:
            areas: dict[str, str] = {}
            for room in self.fixture.house.rooms:
                for entity_id in room.bindings.values():
                    areas.setdefault(entity_id, room.name)
            self._areas = areas
        return self._areas

    def _owner_of(self) -> Mapping[str, tuple[str, str]]:
        """entity id -> (device identifier, device name), from the house's rooms."""
        if self._owners is None:
            owners: dict[str, tuple[str, str]] = {}
            for room in self.fixture.house.rooms:
                owner = (
                    f"openhouse_room_{room.id}",
                    f"{room.name} ({self.fixture.name})",
                )
                for entity_id in room.bindings.values():
                    owners.setdefault(entity_id, owner)
            self._owners = owners
        return self._owners

    def _config(self, domain: str, entity_id: str) -> dict[str, object]:
        """The discovery payload for one entity."""
        config: dict[str, object] = {
            "name": human_name(entity_id),
            "unique_id": self._slug(entity_id),
            "object_id": self._slug(entity_id),
            "state_topic": f"{self.topic_prefix}/{entity_id}/state",
            "availability_topic": f"{self.topic_prefix}/{entity_id}/availability",
            "payload_available": "online",
            "payload_not_available": "offline",
            "device": self._device(entity_id),
        }
        config.update(_PAYLOADS.get(domain, {}))
        if domain in _COMMANDED:
            config["command_topic"] = f"{self.topic_prefix}/{entity_id}/set"
        return config

    def _publish_state(self, entity_id: str) -> None:
        """Publish one entity's current state and availability, both retained."""
        view = self.adapter.read_entity(entity_id)
        self.client.publish(
            f"{self.topic_prefix}/{entity_id}/state", view.state, retain=True
        )
        self.client.publish(
            f"{self.topic_prefix}/{entity_id}/availability",
            "online" if view.available else "offline",
            retain=True,
        )


def main(argv: list[str] | None = None) -> int:
    """Run the fleet against a fixture and a broker, until interrupted."""
    parser = argparse.ArgumentParser(
        description="Publish a fixture house to Home Assistant over MQTT discovery."
    )
    parser.add_argument(
        "--fixture",
        default="minimal",
        help="the fixture house to publish (minimal, messy, large, no_lux)",
    )
    parser.add_argument("--host", default="localhost", help="the MQTT broker host")
    parser.add_argument("--port", type=int, default=1883, help="the MQTT broker port")
    parser.add_argument(
        "--once",
        action="store_true",
        help="announce and exit, rather than serving commands",
    )
    arguments = parser.parse_args(argv)

    vocabulary = Vocabulary.load(paths.ROOT)
    fixture = build_fixture(arguments.fixture, vocabulary=vocabulary)
    with Client(arguments.host, arguments.port) as client:
        fleet = Fleet(fixture=fixture, client=client)
        announcement = fleet.announce()
        print(json.dumps(announcement.to_document(), indent=2))
        if arguments.once:
            return 0
        fleet.subscribe()
        try:
            fleet.serve()
        except KeyboardInterrupt:
            return 0
        except MqttError as error:
            print(f"the broker connection failed: {error}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
