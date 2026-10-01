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

from engine.behaviours import enable_key
from engine.binding import RoomScope
from engine.install import InstalledSet
from engine.install import digest as pack_digest
from engine.solar import Location
from ha_adapter import live_modules
from ha_adapter.composition import LiveRoom, room_id
from ha_adapter.live import LiveSession, LiveSessionError
from ha_adapter.testing import FakeHaTransport
from openhouse import packs

from .packfactory import pack as generated_pack

ROOT = Path(__file__).resolve().parents[1]

LOCATION = Location(latitude=51.5, longitude=-0.1, time_zone="Europe/London")

#: The committed pack this suite installs. `example_pack` requires `light_group`
#: and `motion_sensor` and optionally uses `lux_sensor`, which is exactly the
#: three slots the `hall` room below binds -- so it is the pack a room satisfies,
#: and `kitchen` (seven required slots) is the pack one does not.
EXAMPLE = ROOT / "packs" / "official" / "example-pack.yaml"
KITCHEN = ROOT / "packs" / "official" / "kitchen.yaml"

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
            "lux_sensor": "sensor.hall_lux",
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
        "enabled",
        "behaviours",
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


def test_a_pack_the_house_cannot_satisfy_is_refused_and_changes_nothing() -> None:
    """The kitchen template needs seven slots the hall does not bind."""
    session = _session()
    with pytest.raises(LiveSessionError, match="climate_zone"):
        live_modules.install(session, KITCHEN)

    assert session.engine.installed == InstalledSet()


def test_a_file_that_is_not_a_pack_is_refused_by_name(tmp_path: Path) -> None:
    session = _session()
    broken = tmp_path / "not-a-pack.yaml"
    broken.write_text("just: a mapping\n", encoding="utf-8")

    with pytest.raises(LiveSessionError, match=r"not-a-pack\.yaml"):
        live_modules.install(session, broken)
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
    assert offer["optional_slots_present"] == ("lux_sensor",)


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
