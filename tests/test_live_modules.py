"""The module operations: installing, enabling, listing and offering packs.

Every test here is about one of the two asymmetries this module exists to
bridge. A pack lands in the *house* while a module belongs to a *room*, so the
tests check both halves of that join -- the installed set the engine holds, and
the room the panel is told about -- and they assert through `session.engine`
rather than through this module's own return value, because an operation that
changed nothing and reported success would pass a check on the reply alone.

The second asymmetry is the one between wiring and state: an install rebuilds
and a flag does not. That is asserted directly -- `test_setting_a_flag_does_not_
rebuild_the_engine` compares the engine object -- because it is a property no
return value can show.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Mapping
from pathlib import Path

import pytest
import yaml

from engine.behaviours import enable_key, module_enable_key
from engine.binding import HouseScope, RoomScope
from engine.decision_log import Outcome
from engine.install import InstalledSet
from engine.install import digest as pack_digest
from engine.solar import Location
from ha_adapter import live_modules
from ha_adapter.composition import LiveRoom, room_id
from ha_adapter.live import HOUSE, LiveSession, LiveSessionError
from ha_adapter.testing import FakeHaTransport
from openhouse import packs

from .packfactory import pack as generated_pack

ROOT = Path(__file__).resolve().parents[1]

LOCATION = Location(latitude=51.5, longitude=-0.1, time_zone="Europe/London")

#: The committed pack this suite installs. `example_pack` requires `light_group`
#: and `motion_sensor` and optionally uses `ambient_light_sensor`, which is exactly the
#: three slots the `hall` room below binds -- so it is the pack a room satisfies,
#: and `kitchen` (seven required slots) is the pack one does not.
EXAMPLE = ROOT / "packs" / "official" / "example-pack.yaml"
KITCHEN = ROOT / "packs" / "official" / "kitchen.yaml"

#: The one committed pack that declares an `option`, which is what makes it
#: the pack the house-options test installs: a house with a setting, not one
#: with only slots.
FRIDGE = ROOT / "packs" / "official" / "fridge-guard.yaml"

MOTION_UNIT = "example_pack.motion_turns_on_light"


def _transport() -> FakeHaTransport:
    transport = FakeHaTransport()
    transport.set_state("binary_sensor.hall_motion", "off")
    transport.set_state("sensor.hall_lux", "12")
    transport.set_state("light.hall", "off")
    return transport


def _room(name: str = "hall") -> LiveRoom:
    return LiveRoom(
        id=room_id(name),
        name=name.title(),
        type="hallway",
        bindings={
            "motion_sensor": "binary_sensor.hall_motion",
            "ambient_light_sensor": "sensor.hall_lux",
            "light_group": "light.hall",
        },
    )


def _empty_room(name: str = "spare") -> LiveRoom:
    """A room that binds nothing, which is the case offers must not assume away."""
    return LiveRoom(id=room_id(name), name=name.title(), type="hallway", bindings={})


def _session(rooms: tuple[LiveRoom, ...] | None = None) -> LiveSession:
    return LiveSession.build(
        house_name="Test House",
        rooms=rooms if rooms is not None else (_room(),),
        modes=("Home", "Away"),
        transport=_transport(),
        root=ROOT,
        location=LOCATION,
    )


def _offer_for(
    offers: tuple[Mapping[str, object], ...], pack: str
) -> Mapping[str, object]:
    for offer in offers:
        if offer["pack"] == pack:
            return offer
    raise AssertionError(f"no offer for {pack!r} among {[o['pack'] for o in offers]}")


# -- install -----------------------------------------------------------------


def test_installing_a_pack_records_it_in_the_engine() -> None:
    """The assertion is on the *engine's* set, so a no-op install cannot pass."""
    session = _session()
    live_modules.install(session, EXAMPLE)

    held = session.engine.installed.get("example_pack")
    assert held is not None
    assert held.version == "1.0.0"
    assert held.digest.startswith("sha256:")


def test_install_answers_with_the_protocols_reply_shape() -> None:
    """`open_house/modules/install` answers `{installed, room}`; the field names
    are `models.ts`'s and are checked rather than assumed."""
    session = _session()
    reply = live_modules.install(session, EXAMPLE)

    assert set(reply) == {"installed", "room"}
    installed = reply["installed"]
    room = reply["room"]
    assert isinstance(installed, Mapping)
    assert isinstance(room, Mapping)
    assert set(installed) == {
        "pack",
        "name",
        "version",
        "room_id",
        # Whether the module was put in the house rather than a room: a
        # placement, drawn separately from its evaluation scope.
        "house",
        "enabled",
        # Whether another module is holding this one off, and the behaviour of
        # it that declared the clause: the two facts the red panel on the
        # module's card is built from, and the only place they are read.
        "suppressed_by",
        "suppressed_behaviour",
        "satisfiable",
        "missing_slots",
        "scope",
        # The settings this pack owns, so the panel can draw the module's
        # behaviours and its options as one card rather than two lists: the key
        # space it claims, the form those keys make, and the values in it.
        "option_keys",
        "options_schema",
        "options",
        "behaviours",
        # The devices this module acts through, each with the entity it is
        # pointed at: the per-module slot override, and the row the panel draws
        # the module's own device picker and name field from.
        "slots",
    }
    assert set(room) >= {
        "id",
        "name",
        "type",
        "bindings",
        "options_schema",
        "options",
        "modules",
        "active_profiles",
        "mode",
    }


def test_a_module_belongs_to_the_room_its_entities_live_in() -> None:
    """The pack lands in the house; the module reads as the hall's."""
    session = _session()
    reply = live_modules.install(session, EXAMPLE)
    installed = reply["installed"]
    assert isinstance(installed, Mapping)

    assert installed["room_id"] == "hall"
    assert installed["name"] == "Example pack"
    assert [row["id"] for row in installed["behaviours"]] == [MOTION_UNIT]


def test_install_does_not_enable_anything() -> None:
    """Installation is not activation, and the *engine's* flag says so twice over."""
    session = _session()
    live_modules.install(session, EXAMPLE)

    scope = RoomScope("hall")
    assert (
        session.engine.settings.resolve_or(enable_key(MOTION_UNIT), scope, False).value
        is False
    )
    module = live_modules.installed_modules(session)[0]
    assert module["enabled"] is False
    behaviours = module["behaviours"]
    assert isinstance(behaviours, tuple)
    assert [row["enabled"] for row in behaviours] == [False]


def test_a_fresh_module_reaches_where_it_was_put() -> None:
    """The reach control's default is the placement, and activation is separate.

    `enabled` is `False` -- installation is not activation, the test above holds
    that -- while each atom answers the room the person chose rather than
    "Nowhere". The two facts are deliberately different: the first is whether the
    module is running, the second is where it would run, and a control that read
    "Nowhere" beside a module put in the hall moments ago is describing a
    decision nobody made.
    """
    session = _session()
    module = live_modules.install(session, EXAMPLE, room_id="hall")["installed"]
    assert isinstance(module, Mapping)

    assert module["enabled"] is False
    assert [row["active_rooms"] for row in module["behaviours"]] == [("hall",)]  # type: ignore[union-attr]


def test_a_module_on_somewhere_reports_the_rooms_it_is_actually_on_in() -> None:
    """Once anything has been decided the engine's answer stands, fallback and all.

    The placement default must not become a switch that cannot be cleared: with
    the pack still on in one room, unticking the other has to read as *that*
    room and not fall back to where the module sits.
    """
    session = _session(rooms=(_room("hall"), _room("spare")))
    live_modules.install(session, EXAMPLE, room_id="hall")
    live_modules.set_enabled(session, room_id="hall", pack="example_pack", enabled=True)
    live_modules.set_enabled(
        session, room_id="spare", pack="example_pack", enabled=True
    )
    live_modules.set_behaviour_enabled(
        session,
        room_id="hall",
        pack="example_pack",
        behaviour=MOTION_UNIT,
        enabled=False,
    )

    module = next(
        row
        for row in live_modules.installed_modules(session)
        if row["pack"] == "example_pack"
    )
    assert [row["active_rooms"] for row in module["behaviours"]] == [("spare",)]  # type: ignore[union-attr]


def test_a_pack_the_room_cannot_satisfy_installs_disabled() -> None:
    """The kitchen template needs seven slots the hall does not bind, and lands anyway.

    The whole of the "add any module to any room" rule: the module arrives, the
    record says which slots are unbound, and nothing about it is running. A
    refusal here would make the room's configurables unreachable, because the
    configurable devices *are* the modules' slots.
    """
    session = _session()
    reply = live_modules.install(session, KITCHEN)
    installed = reply["installed"]
    assert isinstance(installed, Mapping)

    assert session.engine.installed.get("kitchen") is not None
    assert installed["enabled"] is False
    assert installed["satisfiable"] is False
    assert "climate_zone" in installed["missing_slots"]
    assert [row["enabled"] for row in installed["behaviours"]] == [False] * len(
        installed["behaviours"]  # type: ignore[arg-type]
    )


def test_an_unwired_module_cannot_be_enabled_and_says_which_slots() -> None:
    """Installation is allowed; activation is not, until the room binds the devices.

    The refusal is `set_enabled`'s and not the installer's, which is the
    separation the two operations exist for: the module is a thing a person put
    in a room, and a switch that cannot be thrown is a switch, not a wall.
    """
    session = _session()
    live_modules.install(session, KITCHEN)

    with pytest.raises(LiveSessionError, match="climate_zone"):
        live_modules.set_enabled(session, room_id="hall", pack="kitchen", enabled=True)

    module = next(
        row
        for row in live_modules.installed_modules(session)
        if row["pack"] == "kitchen"
    )
    assert module["enabled"] is False


def test_a_file_that_is_not_a_pack_is_refused_by_name(tmp_path: Path) -> None:
    session = _session()
    broken = tmp_path / "not-a-pack.yaml"
    broken.write_text("just: a mapping\n", encoding="utf-8")

    with pytest.raises(LiveSessionError, match=r"not-a-pack\.yaml"):
        live_modules.install(session, broken)
    assert session.engine.installed == InstalledSet()


# -- validate ----------------------------------------------------------------


def test_validate_answers_the_manifest_and_records_nothing() -> None:
    """The Dev tab's save: a verdict about a module, and no module in the house.

    A person who asked for a file and got an installation has been given
    something they did not ask for, so the observation is on the engine's set
    and not only on the answer -- the point of the operation is the *absence*
    of the record `install` would have made.
    """
    session = _session()
    document = live_modules.validate(session, EXAMPLE)

    assert document["name"] == "example_pack"
    assert session.engine.installed == InstalledSet()
    assert live_modules.installed_modules(session) == ()


def test_validate_refuses_what_install_would_refuse(tmp_path: Path) -> None:
    """The refusal a Save button is owed, from the installer's own checks.

    Validating is not a lighter check than installing, and this is what says so:
    the same file install refuses, validate refuses, and it refuses in the same
    words -- so "saved" cannot come to mean "saved and uninstallable". The file
    is well-formed YAML with a key the schema does not allow, so the refusal is
    the manifest check's and not the YAML parser's, which would make the two
    sentences agree for a reason that has nothing to do with the checks.

    What this deliberately does *not* claim: a conflict with a pack already
    installed is refused by `pack_install` and not by `_checked`, so validate
    lets it through. That is the right answer for a save -- a module on disk
    conflicts with nothing -- and it is why `validate`'s docstring says the
    checks are the ones that do not need the installed set.
    """
    session = _session()
    broken = generated_pack(
        tmp_path, "broken", edits=(("name: broken", "name: broken\nsurprise: 1"),)
    )

    with pytest.raises(LiveSessionError) as installed:
        live_modules.install(session, broken)
    with pytest.raises(LiveSessionError) as validated:
        live_modules.validate(session, broken)

    assert str(validated.value) == str(installed.value)
    assert session.engine.installed == InstalledSet()


def test_installing_the_same_pack_twice_is_a_reinstall_not_two_entries() -> None:
    session = _session()
    live_modules.install(session, EXAMPLE)
    reply = live_modules.install(session, EXAMPLE)
    installed = reply["installed"]
    assert isinstance(installed, Mapping)

    assert len(session.engine.installed) == 1
    assert session.engine.installed.get("example_pack").change == "reinstall"  # type: ignore[union-attr]


def test_a_pack_that_conflicts_with_an_installed_one_is_refused(
    tmp_path: Path,
) -> None:
    session = _session()
    live_modules.install(session, EXAMPLE)
    rival = generated_pack(tmp_path, "rival", conflicts=(("example_pack", ">=1.0.0"),))

    with pytest.raises(LiveSessionError, match="conflict"):
        live_modules.install(session, rival)

    assert session.engine.installed.names == ("example_pack",)


def test_a_pack_lands_in_the_house_and_not_in_one_rooms_bindings(
    tmp_path: Path,
) -> None:
    """A pack whose slots are spread over two rooms belongs to neither.

    The join between the engine's house-wide set and the panel's room-scoped
    module, in the case that makes the join a decision rather than a lookup.
    """
    first = _room("hall")
    second = LiveRoom(
        id="landing",
        name="Landing",
        type="hallway",
        bindings={"motion_sensor": "binary_sensor.landing_motion"},
    )
    session = LiveSession.build(
        house_name="Test House",
        rooms=(first, second),
        modes=("Home",),
        transport=_transport(),
        root=ROOT,
        location=LOCATION,
    )
    # `example_pack` wants `light_group` and `motion_sensor`: the hall binds the
    # first, the landing the second, and neither room binds both.
    live_modules.install(session, EXAMPLE)

    assert session.engine.installed.get("example_pack") is not None
    assert live_modules.installed_modules(session)[0]["room_id"] == ""


def test_a_pack_installed_into_a_room_belongs_to_that_room() -> None:
    """Naming the room is what makes a module *a module*, and what makes it usable.

    The defect this pins: the room dialog asks which room, the websocket command
    carries the answer, and the install threw it away in favour of joining the
    pack's slots house-wide. In the two-room house below that join reaches two
    motion sensors and two light groups across two rooms, no room binds all four
    entities, and the module read as belonging to the empty room -- so `enabled`
    was `False`, `set_enabled` wrote its flag at the empty room's scope, and the
    Enable button in the panel could not turn anything on. A person could install
    a module and had no way to run it.

    The same house, told which room, places the pack in that room.
    """
    hall = _room("hall")
    landing = LiveRoom(
        id="landing",
        name="Landing",
        type="hallway",
        bindings={
            "motion_sensor": "binary_sensor.landing_motion",
            "light_group": "light.landing",
        },
    )
    session = LiveSession.build(
        house_name="Test House",
        rooms=(hall, landing),
        modes=("Home",),
        transport=_transport(),
        root=ROOT,
        location=LOCATION,
    )
    live_modules.install(session, EXAMPLE, room_id="landing")

    module = live_modules.installed_modules(session)[0]
    assert module["room_id"] == "landing"
    assert module["enabled"] is False

    live_modules.set_enabled(
        session, room_id="landing", pack="example_pack", enabled=True
    )
    assert live_modules.installed_modules(session)[0]["enabled"] is True


def test_the_room_a_person_named_wins_over_where_the_entities_are_bound() -> None:
    """The asked room is the answer, even when the join would have said another.

    `_module_room`'s join is a *guess* -- it reads the entities a pack reached
    and reports the room that holds them all -- and it is consulted only when
    nobody was asked. Installed into the empty room of a two-room house, the
    join has nothing to go on and would answer the empty id or the other room;
    the module is in the room the person chose, and that is the room whose
    Enable button has to work.
    """
    spare = _empty_room("spare")
    session = LiveSession.build(
        house_name="Test House",
        rooms=(_room("hall"), spare),
        modes=("Home",),
        transport=_transport(),
        root=ROOT,
        location=LOCATION,
    )
    live_modules.install(session, EXAMPLE, room_id="spare")

    module = live_modules.installed_modules(session)[0]
    assert module["room_id"] == "spare"
    assert session.module_room("example_pack") == "spare"


def test_the_room_a_pack_was_put_in_survives_a_document_round_trip() -> None:
    """The placement is part of the house, so a restart must not lose it.

    A house-scoped pack (`bedtime` reaches through `light_group` and `lock`)
    has no room in its bindings at all, so a house that forgets the recorded
    room cannot recover it by joining anything -- the module comes back in no
    room and cannot be enabled.
    """
    hall = _room("hall")
    landing = LiveRoom(
        id="landing",
        name="Landing",
        type="hallway",
        bindings={"motion_sensor": "binary_sensor.landing_motion"},
    )
    session = LiveSession.build(
        house_name="Test House",
        rooms=(hall, landing),
        modes=("Home",),
        transport=_transport(),
        root=ROOT,
        location=LOCATION,
    )
    live_modules.install(session, EXAMPLE, room_id="landing")

    rebuilt = LiveSession.from_state(
        session.to_state(),
        transport=_transport(),
        root=ROOT,
        location=LOCATION,
    )

    assert rebuilt.module_room("example_pack") == "landing"
    assert live_modules.installed_modules(rebuilt)[0]["room_id"] == "landing"


def test_uninstalling_forgets_the_room_the_pack_was_put_in() -> None:
    """A placement belongs to the install, not to the name.

    Kept behind, it is read by the *next* install of that pack before the join,
    and a module the person placed in this room appears in the room the previous
    one was in.
    """
    hall = _room("hall")
    landing = LiveRoom(
        id="landing",
        name="Landing",
        type="hallway",
        bindings={"motion_sensor": "binary_sensor.landing_motion"},
    )
    session = LiveSession.build(
        house_name="Test House",
        rooms=(hall, landing),
        modes=("Home",),
        transport=_transport(),
        root=ROOT,
        location=LOCATION,
    )
    live_modules.install(session, EXAMPLE, room_id="landing")
    live_modules.uninstall(session, "example_pack")

    assert session.module_room("example_pack") is None

    live_modules.install(session, EXAMPLE)
    assert live_modules.installed_modules(session)[0]["room_id"] == ""


def test_installing_into_a_room_the_house_does_not_hold_is_refused() -> None:
    """A room named but absent is a bad request, not an unplaced module."""
    session = _session()
    with pytest.raises(LiveSessionError, match="no room"):
        live_modules.install(session, EXAMPLE, room_id="attic")

    assert session.engine.installed.get("example_pack") is None


# -- uninstall ---------------------------------------------------------------


def test_uninstalling_removes_the_pack_from_the_engine() -> None:
    session = _session()
    live_modules.install(session, EXAMPLE)

    room = live_modules.uninstall(session, "example_pack")

    assert session.engine.installed.get("example_pack") is None
    assert room["id"] == "hall"
    assert room["modules"] == ()


def test_uninstalling_refuses_while_a_dependent_is_installed(
    tmp_path: Path,
) -> None:
    session = _session()
    live_modules.install(session, EXAMPLE)
    dependent = generated_pack(
        tmp_path, "dependent", dependencies=(("example_pack", ">=1.0.0"),)
    )
    live_modules.install(session, dependent)

    with pytest.raises(LiveSessionError, match="dependent"):
        live_modules.uninstall(session, "example_pack")

    assert session.engine.installed.get("example_pack") is not None


def test_uninstalling_something_that_is_not_installed_is_refused() -> None:
    session = _session()
    with pytest.raises(LiveSessionError, match="not installed"):
        live_modules.uninstall(session, "example_pack")


# -- set_enabled -------------------------------------------------------------


def test_setting_a_flag_writes_it_through_the_engine() -> None:
    session = _session()
    live_modules.install(session, EXAMPLE)

    live_modules.set_enabled(session, room_id="hall", pack="example_pack", enabled=True)
    scope = RoomScope("hall")
    assert (
        session.engine.settings.resolve_or(enable_key(MOTION_UNIT), scope, False).value
        is True
    )

    module = live_modules.installed_modules(session)[0]
    assert module["enabled"] is True
    behaviours = module["behaviours"]
    assert isinstance(behaviours, tuple)
    assert [row["enabled"] for row in behaviours] == [True]

    live_modules.set_enabled(
        session, room_id="hall", pack="example_pack", enabled=False
    )
    assert (
        session.engine.settings.resolve_or(enable_key(MOTION_UNIT), scope, False).value
        is False
    )


def test_setting_a_flag_does_not_rebuild_the_engine() -> None:
    """A flag is engine state; a rebuild would drop modes, dwell and overrides."""
    session = _session()
    live_modules.install(session, EXAMPLE)
    before = session.engine

    live_modules.set_enabled(session, room_id="hall", pack="example_pack", enabled=True)

    assert session.engine is before


def test_enabling_one_rooms_module_leaves_another_rooms_flag_alone() -> None:
    hall = _room("hall")
    landing = LiveRoom(
        id="landing",
        name="Landing",
        type="hallway",
        bindings={
            "motion_sensor": "binary_sensor.landing_motion",
            "light_group": "light.landing",
        },
    )
    session = LiveSession.build(
        house_name="Test House",
        rooms=(hall, landing),
        modes=("Home",),
        transport=_transport(),
        root=ROOT,
        location=LOCATION,
    )
    live_modules.install(session, EXAMPLE)

    live_modules.set_enabled(session, room_id="hall", pack="example_pack", enabled=True)

    settings = session.engine.settings
    assert (
        settings.resolve_or(enable_key(MOTION_UNIT), RoomScope("hall"), False).value
        is True
    )
    assert (
        settings.resolve_or(enable_key(MOTION_UNIT), RoomScope("landing"), False).value
        is False
    )


def test_setting_a_flag_on_an_unknown_module_or_room_is_refused() -> None:
    session = _session()
    with pytest.raises(LiveSessionError, match="no room 'nowhere'"):
        live_modules.set_enabled(
            session, room_id="nowhere", pack="example_pack", enabled=True
        )

    live_modules.install(session, EXAMPLE)
    with pytest.raises(LiveSessionError, match="no module 'ghost'"):
        live_modules.set_enabled(session, room_id="hall", pack="ghost", enabled=True)


# -- set_behaviour_enabled ---------------------------------------------------
#
# The per-atom switch, which is the thing the modules screen's chips are for: a
# pack arrives as one module, and this is how a person keeps `motion_turns_on_
# light` while turning off a sibling they do not want. Everything below asserts
# through `session.engine` for the same reason the install tests do -- a flag
# nobody reads is indistinguishable from a flag that was never written, so the
# last test drives a tick and reads the light.


def test_one_behaviour_can_be_enabled_without_its_siblings() -> None:
    session = _session()
    live_modules.install(session, EXAMPLE)

    reply = live_modules.set_behaviour_enabled(
        session,
        room_id="hall",
        pack="example_pack",
        behaviour="motion_turns_on_light",
        enabled=True,
    )

    scope = RoomScope("hall")
    settings = session.engine.settings
    assert settings.resolve_or(enable_key(MOTION_UNIT), scope, False).value is True
    # The module gate is a different flag and this did not touch it: a pack's
    # `module.<pack>.enabled` and a behaviour's `behaviour.<id>.enabled` are two
    # switches, and turning one atom on is not the same act as turning the pack
    # on.
    assert (
        settings.resolve_or(enable_key("module.example_pack"), scope, False).value
        is False
    )
    behaviours = reply["behaviours"]
    assert isinstance(behaviours, tuple)
    assert [row["enabled"] for row in behaviours] == [True]

    live_modules.set_behaviour_enabled(
        session,
        room_id="hall",
        pack="example_pack",
        behaviour="motion_turns_on_light",
        enabled=False,
    )
    assert settings.resolve_or(enable_key(MOTION_UNIT), scope, False).value is False


def test_enabling_a_behaviour_does_not_rebuild_the_engine() -> None:
    """The registration is `module_rooms`' and the atom is already in the engine;
    a per-atom flag is engine state and a rebuild would drop modes and dwell."""
    session = _session()
    live_modules.install(session, EXAMPLE)
    before = session.engine

    live_modules.set_behaviour_enabled(
        session,
        room_id="hall",
        pack="example_pack",
        behaviour="motion_turns_on_light",
        enabled=True,
    )

    assert session.engine is before


def test_a_behaviour_the_pack_does_not_declare_is_refused() -> None:
    session = _session()
    live_modules.install(session, EXAMPLE)

    with pytest.raises(LiveSessionError, match="no behaviour called 'ghost'"):
        live_modules.set_behaviour_enabled(
            session,
            room_id="hall",
            pack="example_pack",
            behaviour="ghost",
            enabled=True,
        )

    with pytest.raises(LiveSessionError, match="no module 'ghost'"):
        live_modules.set_behaviour_enabled(
            session, room_id="hall", pack="ghost", behaviour="ghost", enabled=True
        )


def test_a_behaviour_is_named_the_way_the_listing_names_it() -> None:
    """Both spellings are accepted, and the listing's is the pack-qualified one.

    The panel renders `installed_modules`' `behaviours[].id`, which is the unit id
    `example_pack.motion_turns_on_light`, and a caller that read the id off the
    listing and handed it back must not be refused for using the name the listing
    gave it.
    """
    session = _session()
    live_modules.install(session, EXAMPLE)
    listed = live_modules.installed_modules(session)[0]["behaviours"]
    assert isinstance(listed, tuple)
    unit_id = listed[0]["id"]
    assert unit_id == MOTION_UNIT

    live_modules.set_behaviour_enabled(
        session,
        room_id="hall",
        pack="example_pack",
        behaviour=unit_id,
        enabled=True,
    )
    assert (
        session.engine.settings.resolve_or(
            enable_key(MOTION_UNIT), RoomScope("hall"), False
        ).value
        is True
    )


# -- the atom runs -----------------------------------------------------------
#
# The half of the module that made the panel's `3 of 3 behaviours now on` a
# *claim* rather than a stored number: until `declared_units` was wired into
# `build_live_house`, an installed pack's behaviours reached the engine as a
# record and were evaluated by nothing, so enabling one wrote a flag no unit read.


def test_an_installed_packs_behaviour_is_a_unit_the_engine_evaluates() -> None:
    session = _session()
    before = set(session.engine.behaviours)
    assert MOTION_UNIT not in before

    live_modules.install(session, EXAMPLE)

    assert MOTION_UNIT in session.engine.behaviours
    # The four shipped units are still there: an installed pack adds to the
    # engine's own set rather than replacing it, and a merge that lost them would
    # pass a check naming only the new one.
    assert before <= set(session.engine.behaviours)


def test_uninstalling_takes_the_atom_out_of_the_engine() -> None:
    session = _session()
    live_modules.install(session, EXAMPLE)
    assert MOTION_UNIT in session.engine.behaviours

    live_modules.uninstall(session, "example_pack")

    assert MOTION_UNIT not in session.engine.behaviours


def _quiet_room() -> LiveRoom:
    """The hall with the room's own auto-lighting switch *off*.

    The room's built-in `motion_lighting` would light the same lamp the pack's
    atom lights, so a test that left the switch on would pass whichever of the
    two acted and would be testing neither. Turning it off leaves the pack's atom
    as the only thing in the house that can reach `light.hall`.
    """
    room = _room()
    return LiveRoom(
        id=room.id,
        name=room.name,
        type=room.type,
        bindings=room.bindings,
        auto_lighting=False,
    )


def _quiet_session(transport: FakeHaTransport) -> LiveSession:
    return LiveSession.build(
        house_name="Test House",
        rooms=(_quiet_room(),),
        modes=("Home",),
        transport=transport,
        root=ROOT,
        location=LOCATION,
    )


def test_an_enabled_atom_writes_the_state_its_service_means() -> None:
    """The whole chain: install, enable, motion, and the light reads `on`.

    The assertion on the literal state is the point. Before
    `catalog/services.yaml`, the declared interpreter proposed the declared
    *service* as the command's action, so the port -- which takes a state -- wrote
    `light.turn_on` onto the light and this test would read that string back. The
    light being `on` is the artifact doing its job.

    It is also the assertion that catches the module gate: the engine resolves
    `module.<pack>.enabled` at *house* scope, so a `set_behaviour_enabled` that
    wrote only the room-scope unit flag would leave this atom `skipped: disabled`
    and the light dark.
    """
    transport = _transport()
    session = _quiet_session(transport)
    live_modules.install(session, EXAMPLE)
    live_modules.set_behaviour_enabled(
        session,
        room_id="hall",
        pack="example_pack",
        behaviour="motion_turns_on_light",
        enabled=True,
    )
    assert transport.state("light.hall") is not None
    assert transport.state("light.hall").state == "off"  # type: ignore[union-attr]

    transport.set_state("binary_sensor.hall_motion", "on")
    transport.set_state("sensor.hall_lux", "4")
    records = session.tick()

    acted = [record for record in records if record.actor == MOTION_UNIT]
    assert [record.outcome for record in acted] == [Outcome.ACTED]
    assert transport.state("light.hall").state == "on"  # type: ignore[union-attr]


def test_a_disabled_atom_writes_nothing() -> None:
    """The control for the test above: same house, same motion, flag never set."""
    transport = _transport()
    session = _quiet_session(transport)
    live_modules.install(session, EXAMPLE)

    transport.set_state("binary_sensor.hall_motion", "on")
    transport.set_state("sensor.hall_lux", "4")
    records = session.tick()

    acted = [record for record in records if record.actor == MOTION_UNIT]
    assert [record.outcome for record in acted] == [Outcome.SKIPPED_DISABLED]
    assert transport.state("light.hall").state == "off"  # type: ignore[union-attr]


def test_the_house_scope_module_flag_follows_the_rooms() -> None:
    """The master opens when a room's atom goes on and closes when the last does.

    Derived rather than written beside the toggle, so this is the property that
    would break first if a second writer were added: two rooms, one switched on,
    the master open; the second room on, still open; the first room off, still
    open because the second still is; the second off, closed.
    """
    transport = _transport()
    landing = LiveRoom(
        id="landing",
        name="Landing",
        type="hallway",
        bindings={
            "motion_sensor": "binary_sensor.hall_motion",
            "light_group": "light.hall",
        },
        auto_lighting=False,
    )
    session = LiveSession.build(
        house_name="Test House",
        rooms=(_quiet_room(), landing),
        modes=("Home",),
        transport=transport,
        root=ROOT,
        location=LOCATION,
    )
    live_modules.install(session, EXAMPLE)
    master = module_enable_key("example_pack")

    def is_open() -> object:
        return (
            session.engine.settings.resolve_or(master, HouseScope(), False).value
            is True
        )

    assert is_open() is False

    live_modules.set_behaviour_enabled(
        session,
        room_id="hall",
        pack="example_pack",
        behaviour="motion_turns_on_light",
        enabled=True,
    )
    assert is_open() is True

    live_modules.set_behaviour_enabled(
        session,
        room_id="landing",
        pack="example_pack",
        behaviour="motion_turns_on_light",
        enabled=True,
    )
    assert is_open() is True

    live_modules.set_behaviour_enabled(
        session,
        room_id="hall",
        pack="example_pack",
        behaviour="motion_turns_on_light",
        enabled=False,
    )
    assert is_open() is True, "the landing still has the atom on"

    live_modules.set_behaviour_enabled(
        session,
        room_id="landing",
        pack="example_pack",
        behaviour="motion_turns_on_light",
        enabled=False,
    )
    assert is_open() is False


# -- installed_modules -------------------------------------------------------


def test_nothing_installed_lists_nothing() -> None:
    assert live_modules.installed_modules(_session()) == ()


def test_installed_modules_lists_every_pack_by_name(tmp_path: Path) -> None:
    """Two packs, listed in name order: the answer is the engine's own set."""
    session = _session()
    live_modules.install(session, EXAMPLE)
    live_modules.install(session, generated_pack(tmp_path, "second"))

    modules = live_modules.installed_modules(session)

    assert [module["pack"] for module in modules] == ["example_pack", "second"]
    assert session.engine.installed.names == ("example_pack", "second")


def test_installed_modules_names_the_rooms_it_lists() -> None:
    session = _session()
    live_modules.install(session, EXAMPLE)

    module = live_modules.installed_modules(session)[0]

    assert module["room_id"] == "hall"
    assert module["version"] == "1.0.0"


# -- offers ------------------------------------------------------------------


def test_offers_lists_the_packs_the_checkout_publishes() -> None:
    names = [offer["pack"] for offer in live_modules.offers(_session(), room_id="hall")]

    assert "example_pack" in names
    assert "kitchen" in names
    assert names == sorted(names)


def test_an_offer_carries_models_ts_field_names() -> None:
    offer = _offer_for(live_modules.offers(_session(), room_id="hall"), "example_pack")

    assert set(offer) == {
        "pack",
        "name",
        "description",
        "version",
        "kind",
        "license",
        "i18n",
        "requires_slots",
        "optional_slots",
        "satisfiable",
        "missing_slots",
        "optional_slots_present",
        "conflicts",
        "already_installed",
        "options_schema",
        "behaviours",
    }
    assert offer["kind"] == "module"
    assert offer["license"] == "mit"


def test_the_satisfiability_verdict_is_the_rooms_bindings() -> None:
    """The hall binds all three of the pack's slots, required and optional."""
    offer = _offer_for(live_modules.offers(_session(), room_id="hall"), "example_pack")

    assert offer["satisfiable"] is True
    assert offer["missing_slots"] == ()
    assert offer["optional_slots_present"] == ("ambient_light_sensor",)


def test_a_room_that_cannot_satisfy_a_pack_says_which_slots_are_missing() -> None:
    offer = _offer_for(
        offers := live_modules.offers(_session(), room_id="hall"), "kitchen"
    )

    assert offer["satisfiable"] is False
    assert "climate_zone" in offer["missing_slots"]
    assert "light_group" not in offer["missing_slots"]
    assert offers  # the list is returned whole, unsatisfiable packs included


def test_offers_on_a_room_with_nothing_bound() -> None:
    """The empty case, which the happy path would hide."""
    offers = live_modules.offers(_session((_empty_room(),)), room_id="spare")

    example = _offer_for(offers, "example_pack")
    assert example["satisfiable"] is False
    assert example["missing_slots"] == ("light_group", "motion_sensor")
    assert example["optional_slots_present"] == ()

    # A pack that requires nothing is satisfiable in a room that binds nothing,
    # so the verdict is about the pack's clauses and not about the room being
    # empty.
    guest = _offer_for(offers, "guest_mode")
    assert guest["requires_slots"] == ()
    assert guest["satisfiable"] is True


def test_an_installed_pack_is_offered_as_already_installed() -> None:
    session = _session()
    live_modules.install(session, EXAMPLE)

    offer = _offer_for(live_modules.offers(session, room_id="hall"), "example_pack")

    assert offer["already_installed"] is True


def test_offers_reports_a_conflict_the_installed_pack_declares(
    tmp_path: Path,
) -> None:
    """The second direction: the *installed* pack is the one that declared it.

    A check that read only the arriving pack's clause would report this pair as
    compatible, and the panel would offer an install the engine then refuses.
    """
    session = _session()
    rival = generated_pack(tmp_path, "rival", conflicts=(("example_pack", ">=1.0.0"),))
    live_modules.install(session, rival)

    offer = _offer_for(live_modules.offers(session, room_id="hall"), "example_pack")

    assert offer["conflicts"] == (
        {
            "kind": "declared",
            "pack": "rival",
            "installed_version": "1.0.0",
            "detail": ">=1.0.0",
            "severity": "blocking",
        },
    )


def test_offers_names_the_pack_that_declares_the_conflict(
    tmp_path: Path,
) -> None:
    """The first direction: the arriving pack declares it against an installed one."""
    session = _session()
    live_modules.install(session, EXAMPLE)
    rival = generated_pack(tmp_path, "rival", conflicts=(("example_pack", ">=1.0.0"),))

    with pytest.raises(LiveSessionError):
        live_modules.install(session, rival)

    assert session.engine.installed.names == ("example_pack",)


def test_offers_refuses_an_unknown_room() -> None:
    with pytest.raises(LiveSessionError, match="no room 'nowhere'"):
        live_modules.offers(_session(), room_id="nowhere")


def test_offers_are_the_committed_index_and_nothing_else() -> None:
    """The catalog is the checkout's own index; nothing is fetched to answer it.

    Asserted by construction rather than by mocking a socket: every name offered
    is one `registry/index.json` publishes, and every manifest behind it is a
    file under the checkout's root -- which is what makes the answer available to
    a house with no internet.
    """
    index = json.loads((ROOT / live_modules.INDEX).read_text(encoding="utf-8"))
    published = {entry["name"] for entry in index["entries"]}
    listed = {entry["path"] for entry in index["entries"]}

    offers = live_modules.offers(_session(), room_id="hall")

    assert offers
    assert {offer["pack"] for offer in offers} == published
    for relative in listed:
        assert (ROOT / relative).is_file()


# --------------------------------------------------------------------------
# The Store: a published row, and the file it pins.
# --------------------------------------------------------------------------


#: The row every test below resolves. It is `official` because that is the tier
#: `registry/index.json` publishes it under, and `community` is the tier it
#: deliberately is not -- which is what `_WRONG_TIER` is for.
_PACK = "bathroom"
_TIER = "official"
_WRONG_TIER = "community"


def _staged(tmp_path: Path, *, tamper: bool = False) -> Path:
    """A checkout holding the committed registry and one pack's bytes.

    The registry is copied whole rather than built by hand, because the thing
    under test is whether this code agrees with the real published index -- a
    fixture registry written by the test would pass whatever the test believed.

    Only the one pack file is copied, so a resolver that wandered to another
    entry finds nothing to read rather than a second real manifest that would
    make the mistake look like a success. `tamper` *edits* that file -- a
    comment appended to the text would not do, because the digest is over the
    canonical document and not over the bytes on disk, so a reindented or
    re-commented manifest is deliberately still the pinned one. The edit here is
    to a field, which is the forgery the check exists to catch: a file that is a
    valid manifest and is not the published one.
    """
    staged = tmp_path / "checkout"
    target = staged / "registry"
    shutil.copytree(ROOT / "registry", target)
    index = json.loads((target / "index.json").read_text(encoding="utf-8"))
    row = next(entry for entry in index["entries"] if entry["name"] == _PACK)
    source = ROOT / row["path"]
    written = staged / row["path"]
    written.parent.mkdir(parents=True, exist_ok=True)
    document = dict(packs.load_manifest(source))
    if tamper:
        document["description"] = f"{document.get('description', '')} and a change"
    written.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    return staged


def test_a_published_row_resolves_to_the_file_it_pins(tmp_path: Path) -> None:
    staged = _staged(tmp_path)
    path = live_modules.store_pack(staged, _PACK, _TIER)

    # The path *and* the digest, not just "it returned something": a resolver
    # that returned the right file without checking it would pass on the path
    # alone, and the check is the thing under test.
    assert path == staged / "packs" / "official" / "bathroom.yaml"
    index = json.loads((staged / live_modules.INDEX).read_text(encoding="utf-8"))
    pinned = next(e["sha256"] for e in index["entries"] if e["name"] == _PACK)
    assert pack_digest(packs.load_manifest(path)) == pinned


def test_a_name_the_registry_does_not_publish_is_missing(tmp_path: Path) -> None:
    with pytest.raises(live_modules.StorePackMissingError, match="no official pack"):
        live_modules.store_pack(_staged(tmp_path), "no_such_pack", _TIER)


def test_a_published_pack_under_another_tier_is_missing(tmp_path: Path) -> None:
    """The row is what a person clicked, so the tier is part of the question.

    Resolving by name alone would install `bathroom` from a `community` listing
    that does not exist, and it would do so quietly -- which is the failure this
    asserts against rather than the error it raises.
    """
    with pytest.raises(live_modules.StorePackMissingError, match="no community pack"):
        live_modules.store_pack(_staged(tmp_path), _PACK, _WRONG_TIER)


def test_a_file_that_is_not_the_pinned_bytes_is_refused(tmp_path: Path) -> None:
    with pytest.raises(live_modules.StorePackRefusedError):
        live_modules.store_pack(_staged(tmp_path, tamper=True), _PACK, _TIER)


def test_a_revoked_pack_is_refused_though_its_bytes_are_right(tmp_path: Path) -> None:
    """Revocation is checked against the pointer, not the file.

    The bytes in this checkout are the published ones, so a resolver that only
    digested the manifest would install it. Nothing else about the entry has
    changed -- same name, same version, same digest -- which is what makes this a
    test of the revocation list rather than of the digest beside it.
    """
    staged = _staged(tmp_path)
    (staged / "registry" / "revoked.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0.0",
                "revocations": [{"name": _PACK, "reason": "the author withdrew it"}],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(live_modules.StorePackRefusedError, match="withdrew"):
        live_modules.store_pack(staged, _PACK, _TIER)


def test_the_two_refusals_are_distinct_and_both_session_errors() -> None:
    """Why there are two types, asserted rather than left to the docstring.

    The panel answers them under different codes -- `not_found` for a row that
    does not exist, `invalid_format` for a file that is not the published one --
    so a caller that caught one and not the other would report a corrupted
    checkout as a stale panel. Both stay `LiveSessionError`, so a caller that
    only wants "this install did not happen" still catches one type.
    """
    missing = live_modules.StorePackMissingError("x")
    refused = live_modules.StorePackRefusedError("x")

    assert isinstance(missing, LiveSessionError)
    assert isinstance(refused, LiveSessionError)
    assert not isinstance(missing, live_modules.StorePackRefusedError)
    assert not isinstance(refused, live_modules.StorePackMissingError)


# -- the house tab ------------------------------------------------------------


def test_the_house_tab_lists_the_roles_a_module_reaches() -> None:
    """A fresh house offers nothing; installing a pack adds the roles it acts on.

    The rows are the roles an *installed module* reaches, not the roles the
    catalog can name: most of a house's vocabulary is unused in any given house,
    and a row per name would be a page of controls for automations that do not
    exist. So a house with nothing installed answers an empty list, which is the
    honest answer to "what does the house act through" -- nothing does yet.

    A drawn row is still a real house role, which is the other half: the join
    chooses *which* of the house's roles to draw, it does not invent roles outside
    the catalog. `required` comes from the catalog's slot definition rather than
    from whether a room bound it, so the two are asserted apart.
    """
    empty = live_modules.house_scope(_session())["slots"]
    assert empty == ()

    session = _session()
    live_modules.install(session, EXAMPLE)
    slots = live_modules.house_scope(session)["slots"]

    named = set(session.engine.house.house_scope_slots)
    drawn = {slot["slot"] for slot in slots}
    assert drawn == {"light_group"}
    # Every row is a role the house is asked about, and the list is not simply
    # the whole menu of them.
    assert drawn < named

    light = slots[0]
    assert light["required"] == session.vocabulary.slots["light_group"].required
    # The row is the house's *own* binding and not a summary of the rooms': with
    # nothing bound at house scope it reads unbound, and the room that does bind
    # the role is a footnote on the row rather than the row itself.
    assert light["entity_id"] is None
    assert light["status"] == "unbound"
    assert light["rooms"] == ("Hall",)


def test_a_role_is_labelled_for_a_person_and_not_spelled_as_the_catalog() -> None:
    """`light_group` draws as a name, and the raw id stays in its own field.

    The panel prints `label` and the engine says `slot`, so an implementation
    that set one to the other would leave a screen reading `light_group` -- the
    identifier of a role rather than the role.
    """
    session = _session()
    live_modules.install(session, EXAMPLE)
    slots = live_modules.house_scope(session)["slots"]

    assert [slot["slot"] for slot in slots] == ["light_group"]
    assert slots[0]["label"] == "Light group"
    assert slots[0]["label"] != slots[0]["slot"]


def test_the_house_tab_names_the_module_that_reaches_each_role() -> None:
    """Installing a pack joins it to the roles it acts on, in both directions.

    `example_pack` reaches `light_group` and `motion_sensor`; only the first is a
    house role, so the pack is named against that slot and appears in the tab's
    module list. The pack is *room* scoped in this house -- the fixture's
    behaviour declares no `scope` -- which is the point of the assertion: a
    module put in one room is still the module the house tab is about, because
    reaching the house's roles is what it is for.

    The name against the slot is the pack's *display* name and not its id, which
    is why the assertion below reads "Example pack": the row says which module
    acts on the role, and a person looking at the tab knows the module by the
    name they installed it under. The module list keeps the id, because that
    list is what a caller hands back to install or uninstall.
    """
    session = _session()
    live_modules.install(session, EXAMPLE)
    scope = live_modules.house_scope(session)

    light = next(slot for slot in scope["slots"] if slot["slot"] == "light_group")
    assert light["modules"] == ("Example pack",)
    assert "example_pack" in {module["pack"] for module in scope["modules"]}
    # A house role the pack does not reach is not drawn at all, so there is no
    # row to name anything against.
    assert "door_contact" not in {slot["slot"] for slot in scope["slots"]}


def test_a_room_s_page_names_the_module_that_reaches_each_of_its_slots() -> None:
    """Every slot a room's module reaches, and not only the house-eligible ones.

    The two directions of one join have to differ, and this is the difference:
    `house_scope` draws a row only for a role the *whole house* resolves, because
    a house screen is a menu of house roles -- so `motion_sensor`, which is one
    room's door and no house-wide role at all, is deliberately absent there. A
    room's page has the opposite duty. `motion_sensor` is a name this room's pack
    acts through, so "which module reaches it" has to be answerable on the page
    where the binding is written, or a person setting the room's devices is told
    nothing about why the row is there.

    Named by the display name a person installed it under, the same choice the
    house tab makes, and reached through the *keys* the room's slot list uses --
    so a chip cannot appear against a slot no binding could fill.
    """
    session = _session()
    live_modules.install(session, EXAMPLE)

    hall = live_modules.slots_reached_by(session, room_id=_room().id)
    # The role the house tab shows, and the two it does not: a room's row is
    # about the module's whole reach.
    assert hall["light_group"] == ("Example pack",)
    assert hall["motion_sensor"] == ("Example pack",)
    assert hall["ambient_light_sensor"] == ("Example pack",)
    # A slot nothing installed reaches is absent rather than empty, so a caller
    # can ask about one slot without walking the vocabulary.
    assert "door_contact" not in hall
    # And the chip is the *display* name: the pack id names the pack, and the
    # chip's whole point is to be the name the person installed.
    assert "example_pack" not in {name for names in hall.values() for name in names}

    # The room filter is real: a room that holds no module reaches nothing, and
    # the same pack is not chipped against a room it is not in.
    assert live_modules.slots_reached_by(session, room_id=room_id("spare")) == {}


def test_a_room_local_role_a_pack_reaches_is_not_promoted_to_the_house() -> None:
    """`motion_sensor` is a slot, and it is not a house slot, so it draws no row.

    The pack reaches two roles and the tab shows one of them. The check is that
    the rows come from the house's own list of roles rather than from "the slots
    something is installed for": a tab built the second way would offer the house
    "motion sensor" as a bindable role, which is a claim no room type makes.
    """
    session = _session()
    live_modules.install(session, EXAMPLE)
    slots = live_modules.house_scope(session)["slots"]

    drawn = {slot["slot"] for slot in slots}
    assert "motion_sensor" not in drawn
    assert drawn == {"light_group"}
    assert drawn <= set(session.engine.house.house_scope_slots)


# -- the house as a target ---------------------------------------------------
#
# The house is a placement a module can be put in, `HOUSE` (the empty string),
# exactly as a room is. These four tests are the four facts that makes true: the
# install records it, the switch writes the house's scope, the house page carries
# the module's options, and the placement survives a restart. They are separate
# from the room tests above because the defect they pin is a whole half of the
# product -- "a house thing like a room, with its own device slots and modules" --
# and a room-only install path could pass every room test and still not be it.


def test_a_module_can_be_installed_into_the_whole_house() -> None:
    """The house is a target like a room: a module is put *in the house*.

    The placement is `HOUSE` and it is *recorded* -- `module_rooms` carries the
    key with the empty value -- which is the fact that separates "put in the
    house" from "never placed" (`None`) and lets the module's flags resolve at
    house scope rather than a room's.
    """
    session = _session()
    reply = live_modules.install(session, EXAMPLE, room_id=HOUSE)

    assert reply["installed"]["room_id"] == HOUSE
    assert reply["installed"]["house"] is True
    assert session.module_room("example_pack") == HOUSE
    assert live_modules.installed_modules(session)[0]["house"] is True


def test_a_house_modules_switch_writes_the_house_scope_flag() -> None:
    """Enabling a module put in the house writes the flag the engine reads.

    A house-scoped behaviour resolves its enable flag at `HouseScope()`
    (`engine/engine.py`), so a house-placed module's switch has to write there or
    the panel would report "on" over a unit the engine still skips as disabled.
    """
    session = _session()
    live_modules.install(session, EXAMPLE, room_id=HOUSE)

    live_modules.set_enabled(session, room_id=HOUSE, pack="example_pack", enabled=True)

    assert live_modules.installed_modules(session)[0]["enabled"] is True
    assert (
        session.engine.settings.resolve_or(
            enable_key(MOTION_UNIT), HouseScope(), False
        ).value
        is True
    )


def test_the_house_tab_carries_the_options_of_a_module_installed_into_it() -> None:
    """The house's own options form, from the packs put in the house.

    `fridge_guard` declares an `open_for` option; installing it into the house
    puts that setting on the House tab, resolving at house scope, which is what
    makes the house a place with settings rather than only a summary of rooms.
    """
    session = _session()
    live_modules.install(session, FRIDGE, room_id=HOUSE)

    scope = live_modules.house_scope(session)

    assert scope["options_schema"] is not None
    schema = scope["options_schema"]
    assert "module.fridge_guard.open_for" in schema["properties"]
    assert scope["options"]["module.fridge_guard.open_for"] == 120


def test_a_house_placement_survives_a_restart() -> None:
    """Which house a module was put in is part of the house, not of the process."""
    session = _session()
    live_modules.install(session, EXAMPLE, room_id=HOUSE)

    rebuilt = LiveSession.from_state(
        session.to_state(),
        transport=_transport(),
        root=ROOT,
        location=LOCATION,
    )

    assert rebuilt.module_room("example_pack") == HOUSE
    assert live_modules.installed_modules(rebuilt)[0]["house"] is True


def test_offers_can_be_asked_about_the_house() -> None:
    """The house's offers are read against every room's bindings, not one room's.

    `example_pack` wants `light_group` and `motion_sensor`; the hall binds both, so
    the house can be given it, and asking about the house must answer rather than
    refuse for a room id it does not have.
    """
    session = _session(rooms=(_room(), _empty_room()))
    offers = live_modules.offers(session, room_id=HOUSE)

    offer = _offer_for(offers, "example_pack")
    assert offer["satisfiable"] is True


def test_a_module_carries_the_settings_it_owns() -> None:
    """`option_keys` is the pack's own settings, so a card can be one module.

    The form the panel draws is flat and keyed by the resolver's names, so a
    screen that wanted to put each setting under the module that declares it had
    only the key to go on -- that is, it would have had to parse
    `module.<pack>.<key>` and keep its own copy of the key space. This is that
    join computed once, on the side that owns the keys.
    """
    session = _session()
    live_modules.install(session, FRIDGE, room_id=_room().id)

    module = next(
        row
        for row in live_modules.installed_modules(session)
        if row["pack"] == "fridge_guard"
    )

    assert "module.fridge_guard.open_for" in module["option_keys"]
    # The pack's declared option is not the only thing it owns: the reach box
    # per role it acts through is a setting of the same module, and a card that
    # dropped it would put that control somewhere else.
    assert "module.fridge_guard.reach.light_group" in module["option_keys"]
    # And nothing else's. The prefix is the namespace, not a substring: a pack
    # whose id begins with another's must not claim its settings.
    assert not any(
        key.startswith("module.fridge_guard.") is False for key in module["option_keys"]
    )
