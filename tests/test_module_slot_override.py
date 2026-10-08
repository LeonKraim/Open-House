"""The per-module, per-slot override: which device one module acts on.

A room binds one device per slot and every module reaching that slot acts on it
-- which is right until it is not. A person wants their own lamps shut by the
bedtime pack and the room's whole group by the built-in lighting; a pack watching
an appliance wants *that* appliance's contact rather than the front door's. So a
module may point its own slots somewhere else, and name them for itself, and the
room's binding does not move.

The module is in two halves. **The decision** is the first: the session is driven
-- a tick, a written override, another tick -- so what is checked is the entity
the engine actually reads and writes, which is the property a recording that
nothing consulted would fail. **The recording** is the second, the other side of
the same coin: what `set_slot` writes, what it refuses, and what the module's
record then says, which is the property a decision nothing could reach would
fail. Both halves are needed because either alone is satisfiable by a broken
pair -- an override nothing consults, or a decision with nothing to consult.

Two facts are the ones a plausible implementation gets wrong, and each has its
own test:

- **The override resolves where the module acts.** A behaviour that resolved its
  slot one way and proposed to it another would be worse than one that ignored
  the override entirely: `binding`, `read` and `propose` go through one
  resolution, and the test that shows it is a tick that writes the module's own
  lamp rather than the room's.
- **A module's own device is *a different device*.** The duration registry is
  keyed by `(room, slot)`, so a module acting on its own lamp and the room's
  premade binding for the same slot would otherwise share one record: the module's
  lamp would age the room's reading, and a `for` clause would fire on a hold it
  never observed. The dwell key folds the override in; the second test drives a
  `for` clause across an override and asserts the hold restarts.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from engine.behaviours.declared import (
    option_key,
    slot_entity_key,
    slot_label_key,
    slot_rule_key,
)
from engine.binding import RoomScope, resolve_slot
from engine.decision_log import Outcome
from engine.solar import Location
from ha_adapter import live_modules
from ha_adapter.composition import LiveRoom, room_id
from ha_adapter.live import LiveSession, LiveSessionError
from ha_adapter.testing import FakeHaTransport
from sim.clock import VirtualClock

ROOT = Path(__file__).resolve().parents[1]

LOCATION = Location(latitude=51.5, longitude=-0.1, time_zone="Europe/London")

#: A motion-triggered lighting pack: requires `light_group` and `motion_sensor`,
#: optionally uses `ambient_light_sensor` -- the three slots the hall binds.
EXAMPLE = ROOT / "packs" / "official" / "example-pack.yaml"

#: The pack with a `for` clause: it watches a `fridge_contact` no room type
#: provides and waits `open_for` seconds before raising the alarm through
#: `light_group`. Declared, so the room cannot bind it in the document -- the
#: vocabulary grows the word on install -- which makes it the fixture for both
#: properties at once: a device only the override can point, and a hold that has
#: to be measured against the device the module actually reads.
FRIDGE = ROOT / "packs" / "official" / "fridge-guard.yaml"

#: The contact the pack declares. Its own name and not a qualified key, because
#: the pack does not write `separate: true`: a fridge is one fridge.
FRIDGE_CONTACT = "fridge_contact"

EXAMPLE_ATOM = "example_pack.motion_turns_on_light"
FRIDGE_ATOM = "fridge_guard.fridge_left_open"

#: Late evening UTC, so the sun is down and an ambient-light gate is satisfied.
_AT = datetime(2026, 1, 1, 22, 0, tzinfo=UTC)

#: The pack's own wait, from `options: open_for`. Assertions are placed a whole
#: wait either side of it so no boundary rule decides a test.
OPEN_FOR = timedelta(seconds=120)


def _transport() -> FakeHaTransport:
    transport = FakeHaTransport()
    transport.set_state("binary_sensor.hall_motion", "off")
    transport.set_state("sensor.hall_lux", "12")
    transport.set_state("light.hall", "off")
    return transport


def _room() -> LiveRoom:
    """The hall, with its own auto-lighting switch off.

    Off, because the built-in `motion_lighting` reads the same motion sensor and
    writes the same lamp the pack's atom does: a test that left it on would pass
    whichever of the two acted and would be testing neither.
    """
    return LiveRoom(
        id="hall",
        name="Hall",
        type="hallway",
        bindings={
            "motion_sensor": "binary_sensor.hall_motion",
            "ambient_light_sensor": "sensor.hall_lux",
            "light_group": "light.hall",
        },
        auto_lighting=False,
    )


def _empty_room(name: str = "spare") -> LiveRoom:
    """A room binding nothing, which is where a required slot has to be overridden."""
    return LiveRoom(
        id=room_id(name),
        name=name.title(),
        type="hallway",
        bindings={},
        auto_lighting=False,
    )


def _session(
    transport: FakeHaTransport,
    clock: VirtualClock,
    rooms: tuple[LiveRoom, ...] | None = None,
) -> LiveSession:
    return LiveSession.build(
        house_name="Test House",
        rooms=rooms if rooms is not None else (_room(),),
        modes=("Home",),
        transport=transport,
        root=ROOT,
        location=LOCATION,
        clock=clock,
    )


def _advance(session: LiveSession, clock: VirtualClock, delta: timedelta) -> tuple:
    """Move the clock forward and tick, which is how time reaches the engine."""
    clock.advance(delta)
    return session.tick()


def _outcomes(records: tuple, actor: str) -> list[Outcome]:
    return [record.outcome for record in records if record.actor == actor]


def _state(transport: FakeHaTransport, entity_id: str) -> str:
    view = transport.state(entity_id)
    assert view is not None, f"no state for {entity_id}"
    return view.state


def test_a_module_acts_on_the_device_it_is_pointed_at() -> None:
    """The control first, then the override: the lamp that moves is the chosen one.

    Without the override the pack lights the room's `light.hall`; with it the pack
    lights `light.study` and the room's lamp stays dark. The first half is what
    makes the second falsifiable -- a session where the pack had never acted would
    pass an assertion that `light.study` is dark for the wrong reason.
    """
    transport = _transport()
    transport.set_state("light.study", "off")
    clock = VirtualClock.started_at(_AT)
    session = _session(transport, clock)
    live_modules.install(session, EXAMPLE)
    live_modules.set_behaviour_enabled(
        session,
        room_id="hall",
        pack="example_pack",
        behaviour="motion_turns_on_light",
        enabled=True,
    )

    transport.set_state("binary_sensor.hall_motion", "on")
    transport.set_state("sensor.hall_lux", "4")
    control = _advance(session, clock, timedelta(seconds=30))

    assert _outcomes(control, EXAMPLE_ATOM) == [Outcome.ACTED]
    assert _state(transport, "light.hall") == "on"

    # The room's lamp back off, so the second half is about the override and not
    # about a lamp that was already lit.
    transport.set_state("light.hall", "off")
    live_modules.set_slot(
        session,
        room_id="hall",
        pack="example_pack",
        slot="light_group",
        entity_id="light.study",
        label=None,
    )
    transport.set_state("binary_sensor.hall_motion", "off")

    transport.set_state("binary_sensor.hall_motion", "on")
    overridden = _advance(session, clock, timedelta(seconds=30))

    assert _outcomes(overridden, EXAMPLE_ATOM) == [Outcome.ACTED]
    assert _state(transport, "light.study") == "on"
    assert _state(transport, "light.hall") == "off"


def test_the_rooms_binding_is_what_everything_else_still_uses() -> None:
    """The override is the module's, so the engine's answer for the room is unmoved."""
    transport = _transport()
    transport.set_state("light.study", "off")
    clock = VirtualClock.started_at(_AT)
    session = _session(transport, clock)
    live_modules.install(session, EXAMPLE)

    live_modules.set_slot(
        session,
        room_id="hall",
        pack="example_pack",
        slot="light_group",
        entity_id="light.study",
        label="The study lamp",
    )

    binding = resolve_slot(session.engine.house, RoomScope("hall"), "light_group")
    assert binding.entities == ("light.hall",)


def test_a_hold_is_measured_against_the_device_the_module_reads() -> None:
    """An override restarts a `for` clause, because it is a different device.

    The fridge guard proposes only once its contact has read `on` for `open_for`
    seconds. Filled with the hall's contact it fires a whole wait later; pointed
    at the study's -- also open, but observed by nobody until now -- it must *not*
    fire on the first tick after the override. An implementation keyed on the slot
    alone would read the hall contact's accumulated hold and fire at once: an
    alarm raised about a door the module never looked at.

    The slot is filled by the override rather than bound in the room because it
    cannot be: `fridge_contact` is one of the pack's own devices, and the room
    vocabulary grows the word only when the pack is installed. That is the same
    story from the other end -- a declared device is one a person points.
    """
    transport = _transport()
    transport.set_state("binary_sensor.hall_fridge", "on")
    transport.set_state("binary_sensor.study_fridge", "on")
    clock = VirtualClock.started_at(_AT)
    session = _session(transport, clock)
    live_modules.install(session, FRIDGE, room_id="hall")
    live_modules.set_slot(
        session,
        room_id="hall",
        pack="fridge_guard",
        slot=FRIDGE_CONTACT,
        entity_id="binary_sensor.hall_fridge",
        label=None,
    )
    live_modules.set_behaviour_enabled(
        session,
        room_id="hall",
        pack="fridge_guard",
        behaviour="fridge_left_open",
        enabled=True,
    )

    # Half a wait, then a whole one: the second tick is long past `open_for`.
    assert _outcomes(_advance(session, clock, OPEN_FOR / 2), FRIDGE_ATOM) == [
        Outcome.DECLINED
    ]
    assert _outcomes(_advance(session, clock, OPEN_FOR * 2), FRIDGE_ATOM) == [
        Outcome.ACTED
    ]
    assert _state(transport, "light.hall") == "on"

    # The module is pointed at the study's contact -- open, but never read by this
    # module -- and the room's lamp is put back so the next act is visible.
    transport.set_state("light.hall", "off")
    live_modules.set_slot(
        session,
        room_id="hall",
        pack="fridge_guard",
        slot=FRIDGE_CONTACT,
        entity_id="binary_sensor.study_fridge",
        label=None,
    )

    assert _outcomes(_advance(session, clock, OPEN_FOR / 2), FRIDGE_ATOM) == [
        Outcome.DECLINED
    ]
    assert _state(transport, "light.hall") == "off"

    # And then it does fire, a whole wait after the module first read the study's
    # contact -- so the decline above is a restarted hold and not an atom that
    # stopped working.
    assert _outcomes(_advance(session, clock, OPEN_FOR * 2), FRIDGE_ATOM) == [
        Outcome.ACTED
    ]
    assert _state(transport, "light.hall") == "on"


def _split_room() -> LiveRoom:
    """The hall, with `light_group` split in two and a device bound to each half.

    The parent is bound *as well as* the parts, deliberately: it is what makes the
    two tests below able to tell a part's device from the slot's, which is the
    whole claim -- a house where the part had nothing of its own would pass either
    way.
    """
    return LiveRoom(
        id="hall",
        name="Hall",
        type="hallway",
        bindings={
            "motion_sensor": "binary_sensor.hall_motion",
            "ambient_light_sensor": "sensor.hall_lux",
            "light_group": "light.hall",
            "light_group__a": "light.study",
            "light_group__b": "light.attic",
        },
        auto_lighting=False,
    )


def _split_session(transport: FakeHaTransport, clock: VirtualClock) -> LiveSession:
    return LiveSession.build(
        house_name="Test House",
        rooms=(_split_room(),),
        modes=("Home",),
        transport=transport,
        root=ROOT,
        location=LOCATION,
        clock=clock,
        slot_parts={"light_group": ("a", "b")},
    )


def test_two_modules_on_one_part_act_on_the_one_device_it_is_bound_to() -> None:
    """**What makes a part a fact rather than a promise.**

    Two modules are put on part `a`, whose device is `light.study`, while the role
    itself is bound to `light.hall` and part `b` to `light.attic`. Both modules act
    on the study's lamp, and neither touches the other two: the part is one binding
    looked up twice, which is the only arrangement in which "these two share a
    device" is true rather than likely.

    The example pack *and* the fridge guard, because they reach `light_group` from
    opposite ends -- one proposes to it, the other warns through it -- and a
    resolution that worked for one shape of behaviour and not the other would still
    be a broken part.
    """
    transport = _transport()
    for lamp in ("light.study", "light.attic", "light.hall"):
        transport.set_state(lamp, "off")
    transport.set_state("binary_sensor.hall_fridge", "on")
    clock = VirtualClock.started_at(_AT)
    session = _split_session(transport, clock)
    live_modules.install(session, EXAMPLE)
    live_modules.install(session, FRIDGE, room_id="hall")
    # The fridge guard's own device first, because a module cannot be switched on
    # while a device it requires is bound to nothing.
    live_modules.set_slot(
        session,
        room_id="hall",
        pack="fridge_guard",
        slot=FRIDGE_CONTACT,
        entity_id="binary_sensor.hall_fridge",
        label=None,
    )
    for pack, behaviour in (
        ("example_pack", "motion_turns_on_light"),
        ("fridge_guard", "fridge_left_open"),
    ):
        live_modules.set_slot(
            session,
            room_id="hall",
            pack=pack,
            slot="light_group",
            entity_id=None,
            label=None,
            part="a",
        )
        live_modules.set_behaviour_enabled(
            session, room_id="hall", pack=pack, behaviour=behaviour, enabled=True
        )

    # The motion pack proposes to the part's device, not the role's.
    transport.set_state("binary_sensor.hall_motion", "on")
    transport.set_state("sensor.hall_lux", "4")
    assert _outcomes(_advance(session, clock, timedelta(seconds=30)), EXAMPLE_ATOM) == [
        Outcome.ACTED
    ]
    assert _state(transport, "light.study") == "on"
    assert _state(transport, "light.hall") == "off"
    assert _state(transport, "light.attic") == "off"

    # And the fridge guard warns through the same lamp, because it is on the same
    # part -- one device, two modules, which is the point of splitting a slot.
    transport.set_state("light.study", "off")
    assert _outcomes(_advance(session, clock, OPEN_FOR * 2), FRIDGE_ATOM) == [
        Outcome.ACTED
    ]
    assert _state(transport, "light.study") == "on"
    assert _state(transport, "light.hall") == "off"


def test_a_module_moved_to_another_part_moves_to_that_part_s_device() -> None:
    """The control moves the module, and the device it acts on moves with it.

    Part `b` is bound to `light.attic`, so the same module on `b` acts on the
    attic's lamp and stops touching the study's. Without this half, a resolution
    that ignored `part` entirely and always answered with some one part's device
    would pass the test above.
    """
    transport = _transport()
    for lamp in ("light.study", "light.attic", "light.hall"):
        transport.set_state(lamp, "off")
    clock = VirtualClock.started_at(_AT)
    session = _split_session(transport, clock)
    live_modules.install(session, EXAMPLE)
    live_modules.set_slot(
        session,
        room_id="hall",
        pack="example_pack",
        slot="light_group",
        entity_id=None,
        label=None,
        part="b",
    )
    live_modules.set_behaviour_enabled(
        session,
        room_id="hall",
        pack="example_pack",
        behaviour="motion_turns_on_light",
        enabled=True,
    )

    transport.set_state("binary_sensor.hall_motion", "on")
    transport.set_state("sensor.hall_lux", "4")
    assert _outcomes(_advance(session, clock, timedelta(seconds=30)), EXAMPLE_ATOM) == [
        Outcome.ACTED
    ]
    assert _state(transport, "light.attic") == "on"
    assert _state(transport, "light.study") == "off"
    assert _state(transport, "light.hall") == "off"


def test_a_rename_moves_every_module_on_the_part_and_says_which() -> None:
    """**A rename is a rewrite, and the modules are the evidence.**

    A part's name *is* the key a module names, so a rename that only changed the
    record would leave both modules naming a part the slot no longer has -- and
    they would fall back to the slot's own device, silently, while the page looked
    right. `rename_slot_part` moves the settings and answers with the modules it
    moved, which is what the caller rebuilds and what a person is told.
    """
    transport = _transport()
    clock = VirtualClock.started_at(_AT)
    session = _split_session(transport, clock)
    live_modules.install(session, EXAMPLE)
    live_modules.install(session, FRIDGE, room_id="hall")
    for pack in ("example_pack", "fridge_guard"):
        live_modules.set_slot(
            session,
            room_id="hall",
            pack=pack,
            slot="light_group",
            entity_id=None,
            label=None,
            part="a",
        )

    assert live_modules.modules_on_part(session, parent="light_group", part="a") == (
        "Example pack",
        "Fridge guard",
    )

    moved = live_modules.rename_slot_part(
        session, parent="light_group", was="a", name="study"
    )
    assert moved == (("example_pack", "hall"), ("fridge_guard", "hall"))
    assert live_modules.modules_on_part(
        session, parent="light_group", part="study"
    ) == (
        "Example pack",
        "Fridge guard",
    )
    # And every module now names the new part, which is what makes the rebuild
    # worth doing: the old name is gone from the settings.
    assert live_modules.modules_on_part(session, parent="light_group", part="a") == ()

    # The bindings, moved in the same breath and *before* the record stops
    # carrying the old key: a part's device is bound in a room, and a rename that
    # moved only the record would leave the next rebuild refusing the whole house
    # for a binding no vocabulary word defines.
    assert session.rebind_slot_part("light_group", "a", "study") == ("hall",)
    room = session.require_room("hall")
    assert room.bindings["light_group__study"] == "light.study"
    assert "light_group__a" not in room.bindings

    # And then the record, which is the one rebuild -- against rooms and settings
    # that already name the new part.
    session.set_slot_parts({"light_group": ("study", "b")})
    assert live_modules.slot_parts_of(session, room_id="hall", pack="example_pack") == {
        "light_group": "study"
    }
    assert live_modules.slot_parts_of(session, room_id="hall", pack="fridge_guard") == {
        "light_group": "study"
    }


def test_a_bound_part_is_named_by_the_reader_a_removal_is_refused_with() -> None:
    """**The device's side of the removal refusal, which the modules cannot see.**

    A part can hold a device with no module on it at all -- a person binds both
    halves and has so far moved one module onto one of them -- and dropping the
    part then would leave that room naming a key the vocabulary no longer carries.
    The rebuild the removal itself triggers refuses the whole house for it
    (`engine.binding._validate`), so the removal has to be refused *before* it,
    and the refusal has to say where the device is. This is the reader that
    answers that, and the two cases it has to get right: a part bound by a room,
    and a part bound by the house as well.
    """
    session = _split_session(_transport(), VirtualClock.started_at(_AT))

    # Both halves are bound in the hall, by the fixture -- and the names come back
    # as a person reads them, because the refusal is read by a person choosing
    # which page to go to.
    assert session.part_bound_in("light_group", "a") == ("Hall",)
    # A part nobody has bound is not in the way of anything.
    session.set_slot_parts({"light_group": ("a", "b", "c")})
    assert session.part_bound_in("light_group", "c") == ()

    # And the house's own binding counts too: a global slot's device is bound once
    # for everybody, so a part of a house role is bound somewhere the room's page
    # cannot show.
    session.set_house_binding("light_group__b", "light.house")
    assert session.part_bound_in("light_group", "b") == ("Hall", "the house")


def test_a_part_the_house_does_not_carry_is_refused_and_named() -> None:
    """A module on a part nobody created waits forever, so it is refused.

    The refusal names the parts the slot *does* have, because the person who typed
    the wrong one is the person who can act on it -- the same rule the slot
    reachability refusal follows.
    """
    transport = _transport()
    clock = VirtualClock.started_at(_AT)
    session = _split_session(transport, clock)
    live_modules.install(session, EXAMPLE)

    with pytest.raises(LiveSessionError) as refusal:
        live_modules.set_slot(
            session,
            room_id="hall",
            pack="example_pack",
            slot="light_group",
            entity_id=None,
            label=None,
            part="c",
        )
    assert "'a'" in str(refusal.value)
    assert "'b'" in str(refusal.value)


def test_the_override_survives_a_rebuild() -> None:
    """It is a setting and not engine state, so a rebuild carries it.

    A profile activation rebuilds the engine; an override that lived only in the
    engine's transient layer would be gone the moment somebody switched to
    Vacation, and the module would quietly go back to acting on the room's device.
    """
    transport = _transport()
    transport.set_state("light.study", "off")
    clock = VirtualClock.started_at(_AT)
    session = _session(transport, clock)
    live_modules.install(session, EXAMPLE)
    live_modules.set_slot(
        session,
        room_id="hall",
        pack="example_pack",
        slot="light_group",
        entity_id="light.study",
        label=None,
    )

    session.rebuild()

    module = live_modules.installed_modules(session)[0]
    rows: tuple[Mapping[str, object], ...] = module["slots"]  # type: ignore[assignment]
    row = next(entry for entry in rows if entry["slot"] == "light_group")
    assert row["entity_id"] == "light.study"
    assert row["overridden"] is True
    assert row["default_entity_id"] == "light.hall"


# --------------------------------------------------------------------------
# The recording: what the command writes, refuses, and reports back
# --------------------------------------------------------------------------


def _entity_key(pack: str, slot: str) -> str:
    return option_key(pack, slot_entity_key(slot))


def _label_key(pack: str, slot: str) -> str:
    return option_key(pack, slot_label_key(slot))


def _installed(session: LiveSession, room_id: str = "hall") -> Mapping[str, object]:
    """The module as the panel is shown it, found through the engine's own set."""
    (module,) = (
        entry
        for entry in live_modules.installed_modules(session)
        if entry["room_id"] == room_id
    )
    return module


def _row(module: Mapping[str, object], slot: str) -> Mapping[str, object]:
    slots: tuple[Mapping[str, object], ...] = module["slots"]  # type: ignore[assignment]
    return next(entry for entry in slots if entry["slot"] == slot)


def test_pointing_a_slot_records_both_settings_at_the_rooms_scope() -> None:
    """The device and the name are one act, so they land in one scope.

    Read through `session.setting` and not the reply, because the reply is this
    module's own summary: a command that reported the override it meant to write
    without writing it would pass a check on its return value.
    """
    transport = _transport()
    transport.set_state("light.study", "off")
    clock = VirtualClock.started_at(_AT)
    session = _session(transport, clock)
    live_modules.install(session, EXAMPLE)

    live_modules.set_slot(
        session,
        room_id="hall",
        pack="example_pack",
        slot="light_group",
        entity_id="light.study",
        label="  The study lamp  ",
    )

    scope = RoomScope("hall")
    assert session.setting(_entity_key("example_pack", "light_group"), scope) == (
        "light.study"
    )
    # Stripped: the name is for reading, and a name a person typed with stray
    # spaces around it is the name they meant.
    assert session.setting(_label_key("example_pack", "light_group"), scope) == (
        "The study lamp"
    )


def test_the_record_names_the_rooms_device_beside_the_modules_own() -> None:
    """One row per slot, with all three answers the control needs.

    The presence half (`entity_id`) and the fallback half (`default_entity_id`)
    are both stated, because a reset control has to say what it will fall back to
    without the panel resolving a binding itself, and the name is carried beside
    the slot's own (`declared_label`) so a renamed row can still show the name
    the rest of the house uses.

    The three `part` keys are the row's fourth answer -- where in the role this
    module stands -- and they are stated here for the same reason as the others:
    the panel reads them by name, and a key this row stopped sending would be a
    control that silently drew nothing rather than an error.
    """
    transport = _transport()
    transport.set_state("light.study", "off")
    clock = VirtualClock.started_at(_AT)
    session = _session(transport, clock)
    live_modules.install(session, EXAMPLE)
    live_modules.set_slot(
        session,
        room_id="hall",
        pack="example_pack",
        slot="light_group",
        entity_id="light.study",
        label="The study lamp",
    )

    row = _row(_installed(session), "light_group")

    assert set(row) == {
        "slot",
        "label",
        "declared_label",
        "entity_id",
        "default_entity_id",
        "overridden",
        "named",
        "required",
        "separate",
        "house_scope",
        "bound",
        "friendly_name",
        "domain",
        "state",
        "status",
        "rule_kind",
        "rule_summary",
        "rule_picks_device",
        "rule_device",
        "part",
        "part_label",
        "parts",
    }
    assert row["label"] == "The study lamp"
    assert row["declared_label"] == "light_group"
    assert row["entity_id"] == "light.study"
    assert row["default_entity_id"] == "light.hall"
    assert row["overridden"] is True
    assert row["named"] is True
    assert row["bound"] is True
    assert row["state"] == "off"
    # A row a person has *picked* a device for holds no rule, and says so with
    # four absences rather than by leaving the keys off: a panel reads a row's
    # shape, and a field that appears only sometimes is a field every reader has
    # to guard.
    assert row["rule_kind"] is None
    assert row["rule_summary"] is None
    assert row["rule_picks_device"] is None
    assert row["rule_device"] is None

    # An untouched slot is the same row with the override absent, which is what
    # makes the two halves of the flag meaningful rather than decorative.
    untouched = _row(_installed(session), "motion_sensor")
    assert untouched["entity_id"] == "binary_sensor.hall_motion"
    assert untouched["default_entity_id"] == "binary_sensor.hall_motion"
    assert untouched["overridden"] is False
    assert untouched["named"] is False


def test_a_rule_is_reported_on_the_row_it_was_put_on_and_takes_the_device_with_it() -> (
    None
):
    """**The claim a rule rests on**: the logic is on the row, and it decides the device.

    Three of the four kinds produce an entity id, so nothing about *what decides
    the slot* is visible in `entity_id` at all -- the row has to state the kind
    and say the sentence, or a card would show a device the person never chose
    with no way to see why. The device the person had picked is forgotten, because
    from the moment a rule is on the row the override is the *rule's* to write:
    a producing rule writes it, and a condition's watcher has to be able to empty
    it, so a leftover pick sitting in it would put the gated device back the
    moment the condition failed.
    """
    transport = _transport()
    transport.set_state("light.study", "off")
    clock = VirtualClock.started_at(_AT)
    session = _session(transport, clock)
    live_modules.install(session, EXAMPLE)
    live_modules.set_slot(
        session,
        room_id="hall",
        pack="example_pack",
        slot="light_group",
        entity_id="light.study",
        label=None,
    )
    live_modules.set_slot_rule(
        session,
        room_id="hall",
        pack="example_pack",
        slot="light_group",
        kind="script",
        value="hall_lux",
        when=["sensor.lux"],
    )

    row = _row(_installed(session), "light_group")
    assert row["rule_kind"] == "script"
    assert row["rule_summary"] == "decided by a script, when sensor.lux changes"
    assert row["rule_picks_device"] is True
    # The pick is gone, and the row falls back to what the room binds until the
    # script has been called once and said otherwise.
    assert row["overridden"] is False
    assert row["entity_id"] == "light.hall"
    assert row["rule_device"] is None

    # A **condition** is the one kind that carries a device, because it decides
    # whether and never what: the device it gates travels inside the rule.
    live_modules.set_slot(
        session,
        room_id="hall",
        pack="example_pack",
        slot="light_group",
        entity_id="light.study",
        label=None,
    )
    live_modules.set_slot_rule(
        session,
        room_id="hall",
        pack="example_pack",
        slot="light_group",
        kind="condition",
        value={"condition": "state", "entity_id": "binary_sensor.dark", "state": "on"},
        device="light.study",
    )
    gated = _row(_installed(session), "light_group")
    assert gated["rule_kind"] == "condition"
    assert gated["rule_summary"] == (
        "decided by a condition: light.study while it holds"
    )
    assert gated["rule_picks_device"] is False
    # The gated device is on the row, and the module is on the room's binding until
    # the watcher has evaluated the condition once -- which is the whole shape of
    # the rule: *this device, while this*, and that key belongs to the watcher.
    assert gated["rule_device"] == "light.study"
    assert gated["overridden"] is False
    assert gated["entity_id"] == "light.hall"


def test_clearing_a_rule_leaves_the_slot_on_the_room_s_binding() -> None:
    """One empty write, and nothing of the rule or the device it wrote is left.

    Clearing is how a person takes the logic back off a row, and the honest answer
    is the room's binding: the device override was written *by* the rule, so
    leaving the last entity it happened to write behind would be a slot pointed at
    a device nobody has chosen any more.
    """
    transport = _transport()
    transport.set_state("light.study", "off")
    clock = VirtualClock.started_at(_AT)
    session = _session(transport, clock)
    live_modules.install(session, EXAMPLE)
    live_modules.set_slot_rule(
        session,
        room_id="hall",
        pack="example_pack",
        slot="light_group",
        kind="script",
        value="hall_lux",
        when=["sensor.lux"],
    )
    scope = RoomScope("hall")
    key = lambda fact: option_key(  # noqa: E731 - one spelling, used four times
        "example_pack", slot_rule_key("light_group", fact)
    )
    assert session.setting(key("kind"), scope) == "script"
    assert session.setting(key("when"), scope) == ["sensor.lux"]

    live_modules.set_slot_rule(
        session, room_id="hall", pack="example_pack", slot="light_group", kind=""
    )

    cleared = _row(_installed(session), "light_group")
    assert cleared["rule_kind"] is None
    assert cleared["rule_summary"] is None
    assert cleared["overridden"] is False
    assert cleared["entity_id"] == "light.hall"
    # Every key is gone, including the `when` list the last rule left -- a
    # forgotten rule that leaves its entities behind is a listener nothing owns.
    for fact in ("kind", "rule", "when", "device"):
        assert session.setting(key(fact), scope) is None


def test_a_rule_on_a_slot_the_module_does_not_reach_is_refused_by_naming_the_ones_it_does() -> (
    None
):
    transport = _transport()
    transport.set_state("light.study", "off")
    clock = VirtualClock.started_at(_AT)
    session = _session(transport, clock)
    live_modules.install(session, EXAMPLE)
    with pytest.raises(LiveSessionError) as refused:
        live_modules.set_slot_rule(
            session,
            room_id="hall",
            pack="example_pack",
            slot="not_a_slot",
            kind="template",
            value="{{ 'light.study' }}",
        )
    assert "does not reach a slot" in str(refused.value)


def test_a_half_written_rule_is_refused_where_it_is_written() -> None:
    """The writer's reader is the strict one, so a half-rule never lands.

    `recorded_rule` is tolerant because a state file is a file; this is the path a
    screen takes, and a screen that sent "a script" with no script has made a
    mistake worth naming rather than a rule worth recording.
    """
    transport = _transport()
    transport.set_state("light.study", "off")
    clock = VirtualClock.started_at(_AT)
    session = _session(transport, clock)
    live_modules.install(session, EXAMPLE)
    with pytest.raises(LiveSessionError) as refused:
        live_modules.set_slot_rule(
            session,
            room_id="hall",
            pack="example_pack",
            slot="light_group",
            kind="script",
            value="hall_lux",
            when=[],
        )
    assert "needs to know when to run" in str(refused.value)


def test_an_override_naming_the_bound_entity_is_still_an_override() -> None:
    """`overridden` is a fact about the recorded layer, not a string comparison.

    A person may point a module at the very entity the room already binds -- to
    rename it, or because they picked it from the list -- and a control that
    compared the two ids would call that "not overridden" and hide the reset that
    clears it.
    """
    transport = _transport()
    clock = VirtualClock.started_at(_AT)
    session = _session(transport, clock)
    live_modules.install(session, EXAMPLE)
    live_modules.set_slot(
        session,
        room_id="hall",
        pack="example_pack",
        slot="light_group",
        entity_id="light.hall",
        label=None,
    )

    row = _row(_installed(session), "light_group")
    assert row["entity_id"] == row["default_entity_id"] == "light.hall"
    assert row["overridden"] is True
    assert row["named"] is False
    # Unnamed, so the row shows the slot's own name rather than a person's.
    assert row["label"] == "light_group"


def test_a_slot_the_module_does_not_reach_is_refused_by_naming_the_ones_it_does() -> (
    None
):
    """A wrong slot name is refused, and the refusal is the list of right ones.

    The panel offers a row per slot, so a command naming something else has been
    handed a wrong name; writing it would leave a setting no resolver ever reads.
    """
    transport = _transport()
    clock = VirtualClock.started_at(_AT)
    session = _session(transport, clock)
    live_modules.install(session, EXAMPLE)

    with pytest.raises(LiveSessionError) as refusal:
        live_modules.set_slot(
            session,
            room_id="hall",
            pack="example_pack",
            slot="door_contact",
            entity_id="light.study",
            label=None,
        )

    message = str(refusal.value)
    assert "door_contact" in message
    # The two slots a behaviour reaches through: what the atom names and what the
    # pack requires. `ambient_light_sensor` is not one of them -- the pack lists it
    # as optional and no clause of the atom reads it, so there is no row to point
    # and nothing the refusal should offer.
    assert "'light_group', 'motion_sensor'" in message


def test_an_entity_the_house_does_not_hold_is_refused() -> None:
    """A pointer at nothing is refused at the write, not discovered at the tick.

    The engine resolves an override straight to a binding with no adapter read in
    between, so an entity the house does not hold would turn every tick the module
    ran into an unavailable reading rather than a sentence a person could act on.
    """
    transport = _transport()
    clock = VirtualClock.started_at(_AT)
    session = _session(transport, clock)
    live_modules.install(session, EXAMPLE)

    with pytest.raises(LiveSessionError) as refusal:
        live_modules.set_slot(
            session,
            room_id="hall",
            pack="example_pack",
            slot="light_group",
            entity_id="light.nowhere",
            label=None,
        )

    assert "light.nowhere" in str(refusal.value)

    # And nothing was written: a refusal that had already recorded half the pair
    # would leave the slot overridden against an entity that does not exist.
    assert (
        session.setting(_entity_key("example_pack", "light_group"), RoomScope("hall"))
        is None
    )


def test_clearing_the_override_puts_the_slot_back_on_the_rooms_binding() -> None:
    """`None` is the reset, and "no override" is an absent setting, not a `None`.

    A written `None` would resolve as a value and shadow the room's binding
    instead of falling back to it, which is why the clear has to forget.
    """
    transport = _transport()
    transport.set_state("light.study", "off")
    clock = VirtualClock.started_at(_AT)
    session = _session(transport, clock)
    live_modules.install(session, EXAMPLE)
    live_modules.set_slot(
        session,
        room_id="hall",
        pack="example_pack",
        slot="light_group",
        entity_id="light.study",
        label="The study lamp",
    )

    module = live_modules.set_slot(
        session,
        room_id="hall",
        pack="example_pack",
        slot="light_group",
        entity_id=None,
        label=None,
    )

    scope = RoomScope("hall")
    assert session.setting(_entity_key("example_pack", "light_group"), scope) is None
    assert session.setting(_label_key("example_pack", "light_group"), scope) is None
    row = _row(module, "light_group")
    assert row["entity_id"] == "light.hall"
    assert row["overridden"] is False
    assert row["named"] is False
    # And the engine itself is back on the room's device, which is the thing the
    # settings exist to move.
    assert resolve_slot(session.engine.house, scope, "light_group").entities == (
        "light.hall",
    )


def test_a_name_of_only_whitespace_is_no_name() -> None:
    """A blank name is read as none, for the switches' "off is the absence" rule."""
    transport = _transport()
    transport.set_state("light.study", "off")
    clock = VirtualClock.started_at(_AT)
    session = _session(transport, clock)
    live_modules.install(session, EXAMPLE)

    module = live_modules.set_slot(
        session,
        room_id="hall",
        pack="example_pack",
        slot="light_group",
        entity_id="light.study",
        label="   ",
    )

    assert (
        session.setting(_label_key("example_pack", "light_group"), RoomScope("hall"))
        is None
    )
    row = _row(module, "light_group")
    assert row["named"] is False
    assert row["label"] == "light_group"
    # The device half still landed: the two travel together but they are two
    # facts, and a blank name is no reason to lose the pointer.
    assert row["entity_id"] == "light.study"


def test_an_overridden_required_slot_counts_as_wired() -> None:
    """A module a person pointed at its own devices can be switched on.

    The house is one empty room, so nothing binds either required slot: without
    the override the enable is refused and *names both*, which is the control --
    the assertions after it are about an override making the same call succeed,
    and a house where the slots were half-bound anyway would not show that.
    """
    transport = _transport()
    transport.set_state("light.study", "off")
    transport.set_state("binary_sensor.study_motion", "off")
    clock = VirtualClock.started_at(_AT)
    session = _session(transport, clock, rooms=(_empty_room(),))
    live_modules.install(session, EXAMPLE, room_id="spare")

    with pytest.raises(LiveSessionError) as refusal:
        live_modules.set_enabled(
            session, room_id="spare", pack="example_pack", enabled=True
        )

    message = str(refusal.value)
    assert "light_group" in message
    assert "motion_sensor" in message

    for slot, entity_id in (
        ("light_group", "light.study"),
        ("motion_sensor", "binary_sensor.study_motion"),
    ):
        live_modules.set_slot(
            session,
            room_id="spare",
            pack="example_pack",
            slot=slot,
            entity_id=entity_id,
            label=None,
        )

    enabled = live_modules.set_enabled(
        session, room_id="spare", pack="example_pack", enabled=True
    )
    assert enabled["enabled"] is True


# --------------------------------------------------------------------------
# What the module is *built* from, which is a different map from the row's
# --------------------------------------------------------------------------


def test_a_module_s_own_override_reaches_the_map_a_module_is_built_from() -> None:
    """**The gap this reader exists to close.**

    The engine has always honoured a per-module override -- `engine.engine
    ._slot_override` reads the same key when it evaluates a pack's behaviour -- but
    a *hosted* module's own automation is built from a different map
    (`host.bound_slots`), and that map has only ever carried the house's and the
    room's bindings. So a person who pointed a module's slot at their own lamp
    moved the engine and left the module's automation on the room's device: the
    row said one thing and the automation did another.

    `slot_overrides` is the reader that closes it, and what it has to answer is
    narrow and exact: the slots *this* module has overridden, with the entity, and
    nothing about the slots it has not -- because a caller merges the answer over
    the room's map, and an entry for an untouched slot would shadow the room's
    binding with the value the room already had.
    """
    transport = _transport()
    transport.set_state("light.study", "off")
    clock = VirtualClock.started_at(_AT)
    session = _session(transport, clock)
    live_modules.install(session, EXAMPLE)

    # Nothing overridden yet: the room's map is the whole answer.
    assert (
        live_modules.slot_overrides(session, pack="example_pack", room_id="hall") == {}
    )

    live_modules.set_slot(
        session,
        room_id="hall",
        pack="example_pack",
        slot="light_group",
        entity_id="light.study",
        label=None,
    )

    assert live_modules.slot_overrides(
        session, pack="example_pack", room_id="hall"
    ) == {"light_group": "light.study"}
    # And the room's binding did not move, which is what makes it an override
    # rather than an edit of the room.
    room = session.room("hall")
    assert room is not None
    assert room.bindings["light_group"] == "light.hall"


def test_a_rule_that_has_worked_out_a_device_is_read_back_as_the_override() -> None:
    """`settle_slot_rule` writes the same key a person's device pick writes.

    **One key, so no two paths can disagree.** The template's rendered entity, the
    script's returned one, the flow's published one, and the device a condition
    gates all become `slot.<slot>.entity` -- the very key `set_slot` writes -- so
    the engine, the module's card and the module's own automation read one answer,
    and there is no way to be told about the rule by one path and about the device
    by another.

    Answered `True` only when the value moved, because the caller is a watcher: a
    template that renders the same entity on every state change is the common case
    rather than the exception, and a save and a rebuild per render would make a
    slot following a sensor cost an automation reload each time it twitched.
    """
    transport = _transport()
    transport.set_state("light.study", "off")
    clock = VirtualClock.started_at(_AT)
    session = _session(transport, clock)
    live_modules.install(session, EXAMPLE)
    live_modules.set_slot_rule(
        session,
        room_id="hall",
        pack="example_pack",
        slot="light_group",
        kind="template",
        value="{{ 'light.study' }}",
    )

    assert (
        live_modules.settle_slot_rule(
            session,
            room_id="hall",
            pack="example_pack",
            slot="light_group",
            entity_id="light.study",
        )
        is True
    )
    row = _row(_installed(session), "light_group")
    assert row["entity_id"] == "light.study"
    assert row["overridden"] is True
    assert row["rule_kind"] == "template"
    assert live_modules.slot_overrides(
        session, pack="example_pack", room_id="hall"
    ) == {"light_group": "light.study"}

    # The same answer again is not a change.
    assert (
        live_modules.settle_slot_rule(
            session,
            room_id="hall",
            pack="example_pack",
            slot="light_group",
            entity_id="light.study",
        )
        is False
    )

    # `None` is a real answer -- a template whose entities are not in this house,
    # or a condition that does not hold -- and it puts the module back on what the
    # room binds rather than on nothing.
    assert (
        live_modules.settle_slot_rule(
            session,
            room_id="hall",
            pack="example_pack",
            slot="light_group",
            entity_id=None,
        )
        is True
    )
    fallen_back = _row(_installed(session), "light_group")
    assert fallen_back["overridden"] is False
    assert fallen_back["entity_id"] == "light.hall"
    assert fallen_back["rule_kind"] == "template"


def test_a_condition_s_watcher_moves_the_slot_between_the_device_and_the_room() -> None:
    """The one kind of rule that has two answers, and both of them are real.

    A condition gates a device instead of producing one, so the watcher hands over
    the gated device while the condition holds and `None` while it does not -- and
    the second is what the rule's own *device* key is for. Had the gated device
    stayed in `slot.<slot>.entity` where the person picked it, emptying that key to
    fall back to the room would have emptied the very thing the condition is about.
    """
    transport = _transport()
    transport.set_state("light.study", "off")
    transport.set_state("binary_sensor.dark", "on")
    clock = VirtualClock.started_at(_AT)
    session = _session(transport, clock)
    live_modules.install(session, EXAMPLE)
    live_modules.set_slot_rule(
        session,
        room_id="hall",
        pack="example_pack",
        slot="light_group",
        kind="condition",
        value={"condition": "state", "entity_id": "binary_sensor.dark", "state": "on"},
        device="light.study",
    )

    assert (
        live_modules.settle_slot_rule(
            session,
            room_id="hall",
            pack="example_pack",
            slot="light_group",
            entity_id="light.study",
        )
        is True
    )
    holding = _row(_installed(session), "light_group")
    assert holding["entity_id"] == "light.study"
    assert holding["rule_device"] == "light.study"
    # What is in force is the rule's, not the person's: `overridden` reports the
    # recorded key, which from the moment the rule was set is the watcher's -- so
    # the row shows a device something can move rather than a pick that would read
    # as their own choice.
    assert holding["overridden"] is True

    assert (
        live_modules.settle_slot_rule(
            session,
            room_id="hall",
            pack="example_pack",
            slot="light_group",
            entity_id=None,
        )
        is True
    )
    failed = _row(_installed(session), "light_group")
    assert failed["entity_id"] == "light.hall"
    assert failed["rule_device"] == "light.study"
    assert failed["rule_kind"] == "condition"


def test_a_settle_for_a_rule_that_is_gone_is_refused_rather_than_written() -> None:
    """A write with no rule behind it would be a device nobody chose.

    The only caller that can arrive holding a stale rule is a watcher whose change
    was already in flight when somebody took the rule off the row, and a caller in
    that position has to be *told* so it can stop listening -- a quiet success
    would leave a listener writing an override for a row that shows no rule at all.
    """
    transport = _transport()
    transport.set_state("light.study", "off")
    clock = VirtualClock.started_at(_AT)
    session = _session(transport, clock)
    live_modules.install(session, EXAMPLE)

    with pytest.raises(LiveSessionError) as refused:
        live_modules.settle_slot_rule(
            session,
            room_id="hall",
            pack="example_pack",
            slot="light_group",
            entity_id="light.study",
        )
    assert "has no rule on a slot" in str(refused.value)
    # And the override is untouched: refusing to write is refusing to write.
    assert _row(_installed(session), "light_group")["overridden"] is False

    live_modules.set_slot_rule(
        session,
        room_id="hall",
        pack="example_pack",
        slot="light_group",
        kind="template",
        value="{{ 'light.study' }}",
    )
    with pytest.raises(LiveSessionError) as unknown:
        live_modules.settle_slot_rule(
            session,
            room_id="hall",
            pack="example_pack",
            slot="light_group",
            entity_id="light.nowhere",
        )
    assert "there is no entity" in str(unknown.value)
