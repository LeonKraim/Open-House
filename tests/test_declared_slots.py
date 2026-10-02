"""The devices a pack declares of its own: the `slots` clause and its three rules.

`catalog/slots.yaml` is a closed vocabulary, and a pack's point is sometimes a
device the vocabulary has no word for: "warn me when the fridge has been open
longer than ten minutes" needs a `fridge_contact`, and no room type provides one,
because a fridge is not a room. So a manifest may carry a `slots` clause, and
three rules decide what a declaration means -- a name the catalog carries is
*reused*, a name it does not carry *joins the house*, and `separate: true` gives
the pack a device of its own under a pack-qualified key.

Each rule is tested where it is decided, because each is decided in a different
place and a test that only drove the panel would be a test of the join rather
than of any of them:

  * the projection (`engine/declared_slots.py`) -- what a clause says;
  * the sandbox (`engine/sandbox.py`) -- which names a pack may reach through;
  * the install check (`openhouse/packs.py`) -- which names are refused and which
    are reported as the room not being finished;
  * the behaviour builder (`engine/behaviours/declared.py`) -- the key a unit
    carries, which is the key it resolves against a binding;
  * the vocabulary merge (`ha_adapter/declared_units.py`) -- what a house gains.

The one thing a test here deliberately does *not* do is check that a separate
declaration's two names are both spelled somewhere in the panel: that is the
browser step's job, and asserting it here would be asserting a string.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
import yaml

from engine import manifest as pack_manifest
from engine import sandbox, vocabulary
from engine.behaviours.declared import declared_units
from engine.binding import House
from engine.declared_slots import (
    declared_slots,
    key_of,
    optional_keys,
    required_keys,
)
from engine.install import InstalledPack, InstalledSet
from engine.solar import Location
from ha_adapter import declared_units as adapter_units
from ha_adapter import live_modules
from ha_adapter.composition import LiveRoom, room_id
from ha_adapter.live import LiveSession, LiveSessionError
from ha_adapter.testing import FakeHaTransport
from openhouse import packs as pack_checks
from tools.catalog import paths

from .conftest import write

if TYPE_CHECKING:
    from collections.abc import Mapping

ROOT = paths.ROOT

LOCATION = Location(latitude=51.5, longitude=-0.1, time_zone="Europe/London")

#: A device two packs could want and the catalog has no word for. The name is
#: the one the user's own example names, so a reader of the test and a reader of
#: the pack guide are looking at the same scenario.
FRIDGE = "fridge_contact"

#: The artifact a fixture manifest confers, written beside the manifest it is
#: declared in, and named absolutely -- `tests/packfactory.py`'s convention and
#: for its reason: a pack outside the checkout can only keep a `provides` path
#: inside its own directory by saying where that directory is.
PROVIDED = "warn.yaml"

#: Its contents, which `file_class` reads as `other`: not a mapping, so no shape
#: in `engine/sandbox.py` claims it and the class the manifest declared stands.
PROVIDED_TEXT = "# the artifact this pack confers: a comment is not a shape\n"


def _pinned(directory: Path, **overrides: object) -> dict[str, object]:
    """`_document`, with the artifact it confers pinned where the fixture put it."""
    return _document(
        provides=[{"path": (directory / PROVIDED).as_posix(), "class": "other"}],
        **overrides,
    )


def _stage(directory: Path, document: Mapping[str, object]) -> Path:
    """Write `document` and the artifact it provides into `directory`.

    One writer for both, because the manifest and the file it pins are checked
    against each other: `check_provides` refuses a declared path that lands
    outside the pack's own directory, and the pack's own directory is the one
    this function writes the manifest into.
    """
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "fridge-guard.yaml"
    path.write_text(yaml.safe_dump(dict(document), sort_keys=False), encoding="utf-8")
    (directory / PROVIDED).write_text(PROVIDED_TEXT, encoding="utf-8", newline="")
    return path


def _declared(**overrides: object) -> dict[str, object]:
    """One `slots` row, valid unless a test says otherwise."""
    row: dict[str, object] = {
        "name": FRIDGE,
        "accepts_domains": ["binary_sensor"],
    }
    row.update(overrides)
    return row


def _document(**overrides: object) -> dict[str, object]:
    """A manifest document declaring a device of its own, valid to the schema.

    Every clause a manifest must carry whether or not this file's subject reads
    them -- `description`, `kind`, `engine_api`, `license`, `i18n`, `provides`
    and, for a module, `requires_slots` -- is here so that the same document can
    be driven through the sandbox, the install check *and* a real install, which
    is what lets one fixture answer every section of this module.

    `provides` is empty here and filled in by `_pinned`, because its one entry
    has to name a file the fixture has written and this function has no directory
    to write to. Only the schema reads it, so the sections that drive a document
    through the sandbox or the install check do not need it at all.
    """
    document: dict[str, object] = {
        "name": "fridge_guard",
        "version": "1.0.0",
        "description": "warns when the fridge is left open",
        "kind": "module",
        "engine_api": ">=1.0.0 <2.0.0",
        "license": "mit",
        "i18n": {
            "default": {
                "pack": "Fridge guard",
                "description": "Warns when the fridge is left open.",
                "warn": "The fridge has been open too long",
            }
        },
        "requires_slots": ["motion_sensor"],
        "provides": [],
        "slots": [_declared(required=True)],
        "behaviours": [
            {
                "name": "warn",
                "trigger": "state",
                "condition": "state",
                "action": "service",
                "services": ["notify.send_message"],
                "slots": [FRIDGE],
            }
        ],
    }
    document.update(overrides)
    return document


def _installed(*names: str) -> InstalledSet:
    """An installed set naming `names`, with the record shape the engine holds.

    The merge reads the *names* -- what a pack declares is read back from the
    registry, for `ha_adapter.declared_units`' reason -- so the records here are
    the minimum a set can carry rather than a copy of what an install would have
    written.
    """
    digest = "sha256:" + "0" * 64
    return InstalledSet(
        {
            name: InstalledPack(name=name, version="1.0.0", digest=digest)
            for name in names
        }
    )


def _pack(tmp_path: Path, document: Mapping[str, object]) -> sandbox.Pack:
    """Write a manifest into a fixture directory and read it back as the sandbox."""
    return sandbox.load_pack(_stage(tmp_path, document))


# --------------------------------------------------------------------------
# The projection
# --------------------------------------------------------------------------


def test_a_declaration_carries_its_domains_flags_and_key() -> None:
    """The row's name, domains and flags, and the name it binds under.

    All of them at once because they are one projection: a reader that could get
    the name without the key would be a reader that could render the row and then
    look the wrong name up.
    """
    clause = [_declared(required=True), _declared(name="own_motion", separate=True)]
    declared = declared_slots({"slots": clause})

    assert [slot.name for slot in declared] == [FRIDGE, "own_motion"]
    fridge, own = declared
    assert fridge.accepts_domains == ("binary_sensor",)
    assert fridge.required is True
    assert fridge.separate is False
    assert key_of("fridge_guard", fridge) == FRIDGE
    assert key_of("fridge_guard", own) == "fridge_guard__own_motion"


def test_a_row_that_is_not_a_row_is_not_a_declaration() -> None:
    """A non-mapping row, and one with no name, are dropped rather than raised on.

    The validator refuses both, so a reader that carried them would be acting on
    a document the schema already rejected -- and the alternative, raising here,
    would turn a malformed catalog into a traceback inside a projection.
    """
    declared = declared_slots(
        {"slots": ["not a row", {"accepts_domains": ["binary_sensor"]}, 7]}
    )
    assert declared == ()


def test_an_absent_clause_declares_nothing() -> None:
    """A pack that declares no device of its own is the ordinary pack."""
    assert declared_slots({}) == ()
    assert declared_slots({"slots": "not a list"}) == ()


def test_the_flags_are_read_as_booleans_and_not_as_truthiness() -> None:
    """`required: "yes"` is not a declaration of a required slot.

    The schema types both flags as booleans, so a string here is a document the
    validator refuses; reading it as true would act on a clause the schema
    rejected, and the flags default to the safer reading -- optional, shared.
    """
    declared = declared_slots({"slots": [_declared(required="yes", separate="yes")]})
    (slot,) = declared
    assert slot.required is False
    assert slot.separate is False


def test_required_keys_is_the_manifest_s_lists_plus_its_own_declarations() -> None:
    """Both clauses that can require a device, as binding keys, deduplicated.

    A name in `requires_slots` and in the `slots` clause is one device: it
    appears at its first position, which is the position its author wrote first,
    so a refusal that lists it lists it where they are looking.
    """
    document = {
        "name": "fridge_guard",
        "requires_slots": ["light_group", FRIDGE],
        "optional_slots": ["ambient_light_sensor"],
        "slots": [
            _declared(required=True),
            _declared(name="own_motion", separate=True),
        ],
    }
    assert required_keys("fridge_guard", document) == ("light_group", FRIDGE)
    assert optional_keys("fridge_guard", document) == (
        "ambient_light_sensor",
        "fridge_guard__own_motion",
    )


def test_a_declaration_left_at_the_default_is_optional() -> None:
    """`required` defaults to false, which is what lets a pack run without it."""
    document = {"name": "fridge_guard", "slots": [_declared()]}
    assert required_keys("fridge_guard", document) == ()
    assert optional_keys("fridge_guard", document) == (FRIDGE,)


# --------------------------------------------------------------------------
# The sandbox: which names a pack may reach through
# --------------------------------------------------------------------------


def test_the_sandbox_admits_a_device_the_pack_declares_itself(tmp_path: Path) -> None:
    """A name no catalog carries is one this pack brought, and it is reachable.

    Without this the clause would be inert: `check_slot_claims` refuses every
    name `catalog/slots.yaml` does not define, so a pack that declared a fridge
    would be refused for reaching through the very device it declared.

    """
    pack = _pack(tmp_path, _document())
    result = sandbox.check_pack(
        pack,
        ROOT,
        vocabulary.Vocabulary.load(ROOT),
        vocabulary.load_behaviour_vocabulary(ROOT),
    )
    assert result.ok, [refusal.message for refusal in result.refusals]


def test_the_sandbox_still_refuses_a_name_no_authority_declares(tmp_path: Path) -> None:
    """A pack may mint a slot; it may not reach through a word nobody published.

    The distinction the rule keeps: `own_slots` widens *this* pack's vocabulary
    by what *this* pack declared, and by nothing else. A behaviour naming a
    device no catalog and no clause carries is the failure the rule exists for.
    """
    document = _document(
        # The clause declares `fridge_contact`; the behaviour reaches through a
        # different, undeclared name.
        behaviours=[
            {
                "name": "warn",
                "trigger": "state",
                "condition": "state",
                "action": "service",
                "services": ["notify.send_message"],
                "slots": ["kitchen_fridge_contact"],
            }
        ],
    )
    pack = _pack(tmp_path, document)
    result = sandbox.check_pack(
        pack,
        ROOT,
        vocabulary.Vocabulary.load(ROOT),
        vocabulary.load_behaviour_vocabulary(ROOT),
    )
    assert not result.ok
    assert [refusal.reason for refusal in result.refusals] == ["slot_not_declared"]


# --------------------------------------------------------------------------
# The install check: refused, or reported as the room not being finished
# --------------------------------------------------------------------------


def test_a_required_device_the_pack_brought_is_not_a_refusal() -> None:
    """The pack declares it, so "no vocabulary declares it" does not apply.

    This is the rule that makes a fridge module installable at all: the check
    that reads a manifest without an install *cannot* demand the house already
    have a name only this pack has ever written.
    """
    house = _house_for({"motion_sensor": "binary_sensor.hall_motion"})
    assert pack_checks.check_slots(_document(), house) == (FRIDGE,)


def test_a_name_nobody_declares_is_still_refused() -> None:
    """The refusal stays reachable for the case it is about."""
    house = _house_for({"motion_sensor": "binary_sensor.hall_motion"})
    document = _document(requires_slots=["nowhere_defined"])
    try:
        pack_checks.check_slots(document, house)
    except pack_checks.PackError as refusal:
        assert "nowhere_defined" in refusal.reason
    else:  # pragma: no cover - the assertion is the failure
        raise AssertionError("a slot no authority declares was accepted")


def test_a_separate_device_is_reported_by_the_key_a_room_binds() -> None:
    """A room binds `fridge_guard__fridge_contact`, and that is the name reported.

    The written name is the manifest's and the key is the house's; a report that
    named the first would send a person to their room's settings page looking for
    a row that is not there.
    """
    house = _house_for({"motion_sensor": "binary_sensor.hall_motion"})
    document = _document(
        requires_slots=[],
        slots=[_declared(required=True, separate=True)],
    )
    assert pack_checks.check_slots(document, house) == ("fridge_guard__fridge_contact",)


def _house_for(bindings: Mapping[str, str]) -> House:
    """A one-room house against the committed vocabulary, without any install.

    Used by the install checks, which ask what a *manifest* declares and never
    what a house holds beyond its bindings -- so the vocabulary is the catalog's
    and the house needs no extension.
    """
    committed = vocabulary.Vocabulary.load(ROOT)
    document = {
        "name": "Test House",
        "house_scope": {"slots": sorted(committed.house_slots)},
        "rooms": [
            {
                "id": "hall",
                "name": "Hall",
                "type": "hallway",
                "bindings": {
                    slot: {"entity_id": entity} for slot, entity in bindings.items()
                },
            }
        ],
    }
    return House.from_document(document, vocabulary=committed)


# --------------------------------------------------------------------------
# The behaviour builder: the key a unit reads a binding through
# --------------------------------------------------------------------------


def test_a_units_slots_are_the_keys_a_house_binds() -> None:
    """A separate declaration reaches the house under its qualified key.

    The unit is what reads a binding, so a unit carrying the written name would
    resolve to nothing for a device the room had bound -- a pack that installed,
    enabled, and did nothing. Both lists are asserted, because a separate
    declaration is a *required* device here and the two are translated by the
    same call.
    """
    document = _document(
        requires_slots=[], slots=[_declared(required=True, separate=True)]
    )
    (unit,) = declared_units(
        "fridge_guard",
        document,
        default_priority=50,
        service_states={},
    )
    assert unit.slots == ("fridge_guard__fridge_contact",)
    assert unit.required_slots == ("fridge_guard__fridge_contact",)


def test_a_unit_carries_its_pack_s_required_list_through_the_same_translation() -> None:
    """A required catalog slot is unchanged, and a required own device is keyed."""
    document = _document(requires_slots=["light_group"])
    (unit,) = declared_units(
        "fridge_guard",
        document,
        default_priority=50,
        service_states={},
    )
    assert unit.required_slots == ("light_group", FRIDGE)


# --------------------------------------------------------------------------
# The vocabulary merge: what a house gains
# --------------------------------------------------------------------------


def _registry(tmp_path: Path, document: Mapping[str, object]) -> Path:
    """A checkout with one published pack: its manifest and an index pointing at it.

    A fixture registry rather than a monkeypatched reader, because the merge's
    whole job is to read the *published* set: a stand-in would test the stand-in.
    """
    _stage(tmp_path / "packs" / "official", document)
    write(
        tmp_path,
        "registry/index.json",
        json.dumps(
            {
                "entries": [
                    {
                        "name": "fridge_guard",
                        "version": "1.0.0",
                        "repo": ".",
                        "commit": "0" * 40,
                        "path": "packs/official/fridge-guard.yaml",
                        "sha256": "sha256:" + "0" * 64,
                        "tier": "official",
                        "pointer": "pointers/official/fridge_guard/1.0.0.yaml",
                    }
                ]
            },
            indent=2,
        ),
    )
    return tmp_path


def test_an_installed_pack_adds_its_device_to_the_houses_vocabulary(
    tmp_path: Path,
) -> None:
    """The merge is what makes a room able to bind the device at all.

    `engine/binding.py` refuses a room binding a slot the vocabulary does not
    carry, so without this step the `fridge_contact` a pack declares could never
    be written into a house document -- not by the panel, not by an import.
    """
    root = _registry(tmp_path, _document())
    extended = adapter_units.with_declared_slots(
        root, _installed("fridge_guard"), vocabulary.Vocabulary.load(ROOT)
    )
    assert FRIDGE in extended.slots
    assert extended.slots[FRIDGE].required is True


def test_the_catalog_wins_a_name_it_already_carries(tmp_path: Path) -> None:
    """A pack declaring `door_contact` is reusing the room's own door contact.

    So the catalog's `required`-ness is the one that stands, and the pack's own
    claim about a name it does not own does not overwrite it. `door_contact` is
    optional in the catalog, which is the flag this asserts.
    """
    root = _registry(
        tmp_path,
        _document(slots=[_declared(name="door_contact", required=True)]),
    )
    committed = vocabulary.Vocabulary.load(ROOT)
    extended = adapter_units.with_declared_slots(
        root, _installed("fridge_guard"), committed
    )
    assert extended.slots["door_contact"] == committed.slots["door_contact"]


def test_no_installed_packs_leaves_the_vocabulary_alone() -> None:
    """`None` and an empty set are the same answer, and it is the same object."""
    committed = vocabulary.Vocabulary.load(ROOT)
    assert adapter_units.with_declared_slots(ROOT, None, committed) is committed
    assert adapter_units.with_declared_slots(ROOT, _installed(), committed) is committed


def test_a_room_can_bind_the_device_a_pack_declares(tmp_path: Path) -> None:
    """The end of the chain: the house document accepts the binding.

    This is the assertion the whole feature exists for. Everything before it is
    a name travelling; this is the name reaching `House.from_document`, which is
    where a name that is not in the vocabulary stops.
    """
    root = _registry(tmp_path, _document())
    extended = adapter_units.with_declared_slots(
        root, _installed("fridge_guard"), vocabulary.Vocabulary.load(ROOT)
    )
    document = {
        "name": "Test House",
        "house_scope": {"slots": sorted(extended.house_slots)},
        "rooms": [
            {
                "id": "kitchen",
                "name": "Kitchen",
                "type": "kitchen",
                "bindings": {FRIDGE: {"entity_id": "binary_sensor.fridge_door"}},
            }
        ],
    }
    house = House.from_document(document, vocabulary=extended)
    assert house.rooms[0].bindings[FRIDGE] == "binary_sensor.fridge_door"


# --------------------------------------------------------------------------
# The live path: the module installs, and cannot be switched on unwired
# --------------------------------------------------------------------------


def _session() -> LiveSession:
    """A one-room house, with nothing bound to a fridge."""
    transport = FakeHaTransport()
    transport.set_state("binary_sensor.fridge_door", "off")
    return LiveSession.build(
        house_name="Test House",
        rooms=(
            LiveRoom(
                id=room_id("hall"),
                name="Hall",
                type="hallway",
                bindings={"motion_sensor": "binary_sensor.hall_motion"},
            ),
        ),
        modes=("Home", "Away"),
        transport=transport,
        root=ROOT,
        location=LOCATION,
    )


def _catalog_row(
    tmp_path: Path, document: Mapping[str, object]
) -> live_modules._Published:
    """The module's own catalog row for a manifest written to a fixture."""
    loaded = pack_manifest.load_manifest(_stage(tmp_path, document))
    return live_modules._Published(
        name=loaded.name,
        version="1.0.0",
        tier="official",
        manifest=loaded,
        strings={},
    )


def test_a_module_that_brought_its_device_installs_and_cannot_be_enabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The user's own scenario, end to end through the live session.

    The module installs -- the room's configurable devices are the modules' slots,
    so it has to be in before the slot it wants can be filled -- and arrives
    disabled with the missing device named by the key a room binds it under. Every
    write in this test is a public operation; only the *catalog* is stood in for,
    because a pack that is not published has no declarations to read.

    The document is `_pinned`, because a real install is the one path here that
    validates against the schema -- and the schema is the one reader of
    `provides`, whose single entry has to name a file the fixture has written.
    """
    session = _session()
    document = _pinned(tmp_path)
    row = _catalog_row(tmp_path, document)
    monkeypatch.setattr(live_modules, "_published", lambda root: (row,))

    reply = live_modules.install(session, _stage(tmp_path, document), room_id="hall")
    installed = reply["installed"]
    assert installed["enabled"] is False
    assert installed["satisfiable"] is False
    assert installed["missing_slots"] == (FRIDGE,)

    # Refused, and the refusal names the row the person has to fill on the room's
    # settings page -- which is the whole reason the install was allowed.
    with pytest.raises(LiveSessionError, match=FRIDGE):
        live_modules.set_enabled(
            session, room_id="hall", pack="fridge_guard", enabled=True
        )
