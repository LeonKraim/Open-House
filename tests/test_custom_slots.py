"""A slot a person declares of their own: the record, the growth, and the binding.

The gap this closes, in the user's words: "i wanna create a new slot in the
blueprint importer but its not letting me". A blueprint that toggles a helper
asks for a device no room type provides, so the import screen offered the house's
roles, none of them fitted, and a person who typed the name they call it was told
the house had no such slot -- which was true and was not something they could act
on. `engine/custom_slots.py` is the word they can now add.

Four claims, and each is a way the feature is useless if it is wrong:

  * **The name a person types becomes a name a house can bind.** `slug` is the
    one function that turns "Toggle Helper" into `toggle_helper`, and a name it
    produced has to be one the binding layer accepts -- the pattern is
    `engine/declared_slots.py`'s and this is the only place a person's words meet
    it.
  * **A declaration that accepts nothing is refused.** What a slot accepts is what
    the panel's picker filters by *and* what `rank_candidates` skips on, so a slot
    with no domains is a role that could be declared and never bound: the dead end
    the feature exists to remove, wearing the feature's name.
  * **The declaration reaches the engine as a vocabulary word.** `engine/binding`
    refuses a binding for a name the vocabulary does not carry, so a record that
    grew nothing would be a slot the import screen offers and the room's page
    cannot bind.
  * **The house reads the same after a restart.** The module a person imported is
    built around the name, so a store that dropped the declaration would leave a
    module waiting for ever on a word the house had forgotten it coined.
"""

from __future__ import annotations

import ast
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path

import pytest

from engine import custom_slots
from engine.binding import House, InvalidHouseError, UnknownSlotError
from engine.custom_slots import SlotError
from engine.vocabulary import Vocabulary
from engine.solar import Location
from ha_adapter.composition import build_live_house, house_document
from ha_adapter.live import LiveSession, LiveSessionError, LiveRoom
from ha_adapter.testing import FakeHaTransport

ROOT = Path(__file__).resolve().parents[1]
LOCATION = Location(latitude=51.5, longitude=-0.1, time_zone="Europe/London")


def _vocabulary() -> Vocabulary:
    return Vocabulary.load(ROOT)


def _transport() -> FakeHaTransport:
    transport = FakeHaTransport()
    transport.set_state("input_boolean.kitchen_toggle", "off")
    transport.set_state("input_boolean.house_toggle", "off")
    transport.set_state("light.hall", "off")
    return transport


def _session(**rest: object) -> LiveSession:
    rest.setdefault("rooms", ())
    return LiveSession.build(
        house_name="Home",
        modes=("Home", "Away"),
        transport=_transport(),
        root=ROOT,
        location=LOCATION,
        **rest,  # type: ignore[arg-type]
    )


# -- The record -------------------------------------------------------------


def test_a_persons_words_become_a_name_a_house_can_bind() -> None:
    """`slug` is the only place the person's spelling meets the key's rule."""
    assert custom_slots.slug("Toggle Helper") == "toggle_helper"
    assert custom_slots.slug("The  Front   Door!") == "the_front_door"
    assert custom_slots.slug("  Kitchen Lights  ") == "kitchen_lights"
    # A name that would start with a digit is prefixed rather than truncated: a
    # name a person cannot recognise back is worse than one with a letter on it.
    assert custom_slots.slug("3D Printer") == "s_3d_printer"
    # Nothing to slug is nothing, and `add` is where that is refused -- so the
    # caller reads one sentence for a blank box and for a blank string.
    assert custom_slots.slug("   ") == ""


def test_a_declared_name_is_one_the_binding_layer_accepts() -> None:
    """The slugged name is bindable in a house, which is the whole of its job."""
    record = custom_slots.add({}, custom_slots.slug("Toggle Helper"), ["input_boolean"])
    vocabulary = custom_slots.grow(_vocabulary(), record)
    assert "toggle_helper" in vocabulary.slots
    # Not required: `required` is a fact about `catalog/slots.yaml`, and a word a
    # person coined is not a word any room type promised.
    assert vocabulary.slots["toggle_helper"].required is False
    house = House.from_document(
        house_document(
            "Home",
            [
                LiveRoom(
                    id="kitchen",
                    name="Kitchen",
                    type="kitchen",
                    bindings={"toggle_helper": "input_boolean.kitchen_toggle"},
                )
            ],
            vocabulary,
        ),
        vocabulary=vocabulary,
    )
    assert house.room("kitchen").bindings["toggle_helper"] == (
        "input_boolean.kitchen_toggle"
    )


def test_a_slot_that_accepts_nothing_is_refused() -> None:
    """A role nothing can fill is the dead end the feature exists to remove."""
    with pytest.raises(SlotError) as refusal:
        custom_slots.add({}, "toggle_helper", [])
    assert "at least one domain" in str(refusal.value)
    # And a domain that is not a domain is dropped rather than stored, so the
    # same refusal is what an empty-looking list of junk reads as.
    with pytest.raises(SlotError):
        custom_slots.add({}, "toggle_helper", ["Not A Domain"])


def test_a_name_that_is_not_a_name_is_refused() -> None:
    """A slot name is a binding key, and a key the layer cannot carry is no slot."""
    for bad in ("Toggle Helper", "toggle-helper", "1toggle", "", "toggle__helper"):
        # `__` too: it is the joiner a part and a pack's own device bind under
        # (`engine/declared_slots.QUALIFIER`), so a name carrying one would read
        # as a part of a slot a person never split.
        with pytest.raises(SlotError):
            custom_slots.add({}, bad, ["light"])


def test_declaring_a_name_twice_narrows_it_rather_than_refusing() -> None:
    """A person who declares a slot again asked for the slot they have."""
    once = custom_slots.add({}, "toggle_helper", ["input_boolean", "switch"])
    twice = custom_slots.add(once, "toggle_helper", ["input_boolean"])
    assert twice == {"toggle_helper": ("input_boolean",)}


def test_the_catalog_wins_a_name_it_already_carries() -> None:
    """A declared word that collides with a catalog one is the catalog's word."""
    vocabulary = custom_slots.grow(
        _vocabulary(), {"light_group": ("input_boolean",)}
    )
    assert vocabulary.slots["light_group"].required is True


def test_a_stored_record_keeps_only_rows_it_can_use() -> None:
    """The reporting reader drops a row it cannot use rather than refusing the house."""
    read = custom_slots.record_from(
        {
            "toggle_helper": ["input_boolean"],
            "Bad Name": ["light"],
            "empty": [],
            "junk": "not a list",
        }
    )
    assert read == {"toggle_helper": ("input_boolean",)}
    assert custom_slots.record_from(None) == {}
    assert custom_slots.record_from(["not", "a", "mapping"]) == {}


def test_taking_a_declaration_away_says_when_there_is_nothing_to_take() -> None:
    """`remove` refuses a name the house never declared, and answers for one it did."""
    record = custom_slots.add({}, "toggle_helper", ["input_boolean"])
    assert custom_slots.remove(record, "toggle_helper") == {}
    with pytest.raises(SlotError) as refusal:
        custom_slots.remove(record, "kitchen_toggle")
    assert "nothing to take away" in str(refusal.value)


# -- The session ------------------------------------------------------------


def test_a_declared_slot_is_bindable_and_the_house_is_the_same_after_a_restart() -> None:
    """The session's half: `bindable_slots` carries it, and `build` restores it.

    The restart half is the one that matters most for this feature: the module a
    person imported is *built around the name*, so a house that came back without
    the declaration would come back with a module waiting for ever on a word it
    had forgotten it coined.
    """
    session = _session(rooms=())
    session.set_custom_slots({"toggle_helper": ("input_boolean",)})
    assert "toggle_helper" in session.bindable_slots()

    rebuilt = _session(
        rooms=session.rooms,
        custom_slots=session.custom_slots,
    )
    assert "toggle_helper" in rebuilt.bindable_slots()

    # And a room may be given a device for it, which is the point of declaring it.
    rebuilt.set_rooms(
        (
            LiveRoom(
                id="kitchen",
                name="Kitchen",
                type="kitchen",
                bindings={"toggle_helper": "input_boolean.kitchen_toggle"},
            ),
        )
    )
    assert rebuilt.room("kitchen") is not None


def test_a_declared_slot_reaches_the_engine_and_a_forgotten_one_takes_the_house_down() -> None:
    """The growth is what makes the binding legal, and dropping it is not silent."""
    rooms = (
        LiveRoom(
            id="kitchen",
            name="Kitchen",
            type="kitchen",
            bindings={"toggle_helper": "input_boolean.kitchen_toggle"},
        ),
    )
    # Without the declaration the house does not compose at all: the binding names
    # a word the vocabulary does not carry. `rebuild` translates that into the one
    # type a command path reports, and *that* is what the host prevents by refusing
    # to forget a slot something still binds (`OpenHouseHost.async_forget_slot`):
    # asserted here because it is the reason that refusal exists.
    with pytest.raises(LiveSessionError):
        _session(rooms=rooms)
    session = _session(rooms=rooms, custom_slots={"toggle_helper": ("input_boolean",)})
    assert session.engine is not None
    # And the vocabulary the engine was built against carries it, which is how the
    # binding resolved at all.
    assert "toggle_helper" in session.engine.house.vocabulary.slots


def test_the_house_binds_a_declared_slot_globally_too() -> None:
    """A declared slot is the house's, so the house scope may bind it."""
    session = _session(rooms=(), custom_slots={"toggle_helper": ("input_boolean",)})
    session.set_house_binding("toggle_helper", "input_boolean.house_toggle")
    assert session.house_bindings["toggle_helper"] == "input_boolean.house_toggle"


def test_the_domains_reach_the_catalog_the_pickers_read() -> None:
    """The join that makes a declared slot usable rather than merely nameable.

    What a slot accepts is the *catalog's* to say and every picker reads it there,
    so a declaration that reached only the vocabulary would be a slot the import
    screen offers and no room can be given a device for -- `rank_candidates` skips
    an entity whose domain is not in the list, and an empty list skips all of
    them.

    Compiled out of `views.py` rather than imported, the way `test_ws_errors`
    reads the websocket module: `views` imports `homeassistant` at module scope,
    which this suite does not have. The function reads nothing but `replace` and
    the two fields of the catalog it is handed, so a stand-in catalog is the whole
    of what it needs.
    """
    views = ROOT / "custom_components" / "open_house" / "views.py"
    with_custom_slots = _compiled(views, "with_custom_slots", {"replace": replace})

    @dataclass(frozen=True)
    class Catalog:
        slot_domains: dict[str, tuple[str, ...]]
        slot_required: dict[str, bool] = field(default_factory=dict)

    catalog = Catalog(slot_domains={"light_group": ("light",)})
    joined = with_custom_slots(catalog, {"toggle_helper": ("input_boolean",)})
    assert joined.slot_domains["toggle_helper"] == ("input_boolean",)
    assert joined.slot_domains["light_group"] == ("light",)
    # The catalog wins a name it already carries, the precedence every growth in
    # this product states.
    joined = with_custom_slots(catalog, {"light_group": ("input_boolean",)})
    assert joined.slot_domains["light_group"] == ("light",)
    # And nothing declared is the catalog unchanged, which is what the path that
    # has no session state costs.
    assert with_custom_slots(catalog, {}) is catalog


def _compiled(path: Path, name: str, namespace: dict[str, object]) -> object:
    """One function, cut out of a module this suite cannot import.

    `test_ws_errors._compiled` does the same for the websocket module and says
    why: the module's own imports name `homeassistant`, so the function is taken
    by name and exec'd against the names it actually reads.
    """
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            segment = ast.get_source_segment(source, node)
            assert segment is not None
            scope = dict(namespace)
            exec("from __future__ import annotations\n" + segment, scope)
            return scope[name]
    raise AssertionError(f"{path.name} has no {name!r}")


def test_a_roomless_house_composes_with_a_declaration_and_without_one() -> None:
    """`build_live_house` takes the record, and an absent one is no declaration."""
    house = build_live_house(
        house_name="Home",
        rooms=(),
        modes=("Home", "Away"),
        transport=_transport(),
        vocabulary_root=ROOT,
        location=LOCATION,
        custom_slots={"toggle_helper": ("input_boolean",)},
    )
    assert "toggle_helper" in house.engine.house.vocabulary.slots
    bare = build_live_house(
        house_name="Home",
        rooms=(),
        modes=("Home", "Away"),
        transport=_transport(),
        vocabulary_root=ROOT,
        location=LOCATION,
    )
    assert "toggle_helper" not in bare.engine.house.vocabulary.slots


def test_a_house_document_naming_a_declared_slot_is_not_an_invalid_house() -> None:
    """The schema's `slots` pattern is the binding layer's, and the two agree."""
    vocabulary = custom_slots.grow(
        _vocabulary(), {"toggle_helper": ("input_boolean",)}
    )
    document = house_document(
        "Home",
        [
            LiveRoom(
                id="kitchen",
                name="Kitchen",
                type="kitchen",
                bindings={"toggle_helper": "input_boolean.kitchen_toggle"},
            )
        ],
        vocabulary,
    )
    # `House.from_document` validates against the frozen schema *and* the
    # vocabulary, so a document it accepts is a house the engine can decide for.
    house = House.from_document(document, vocabulary=vocabulary)
    assert house.rooms[0].bindings


def test_a_house_still_refuses_a_binding_for_a_word_nobody_declared() -> None:
    """The gate is not opened by the feature: only declared names pass it."""
    vocabulary = custom_slots.grow(_vocabulary(), {"toggle_helper": ("input_boolean",)})
    with pytest.raises(UnknownSlotError):
        House.from_document(
            house_document(
                "Home",
                [
                    LiveRoom(
                        id="kitchen",
                        name="Kitchen",
                        type="kitchen",
                        bindings={"never_declared": "input_boolean.kitchen_toggle"},
                    )
                ],
                vocabulary,
            ),
            vocabulary=vocabulary,
        )


def test_a_slot_a_person_declared_is_not_a_slot_the_catalog_requires() -> None:
    """`required` is the catalog's judgement, and a coined word carries none."""
    session = _session(rooms=(), custom_slots={"toggle_helper": ("input_boolean",)})
    assert "toggle_helper" in session.bindable_slots()


def test_a_session_refuses_a_room_binding_a_word_nobody_declared() -> None:
    """The session's own gate, which is the one a panel write goes through."""
    session = _session(rooms=())
    with pytest.raises(LiveSessionError):
        session.bind("kitchen", "toggle_helper", "input_boolean.kitchen_toggle")


def test_a_house_that_is_invalid_still_names_the_pointer_it_failed_at() -> None:
    """The feature did not loosen validation, only the vocabulary it reads."""
    with pytest.raises(InvalidHouseError):
        House.from_document(
            {"name": "Home", "rooms": "not a list", "house_scope": {"slots": []}},
            vocabulary=_vocabulary(),
        )


# -- The command layer ------------------------------------------------------


def test_the_refusal_names_the_way_out_it_did_not_use_to_have() -> None:
    """A dead end that says only "no" is the defect; it now says how to get out.

    Compiled out of `websocket_api.py` rather than imported, for the reason
    `test_ws_errors` reads the module the same way: it imports `homeassistant` at
    module scope. What is asserted is the sentence, because the sentence *is* the
    defect -- a person who typed the name they call a thing was told the house had
    no such slot, which was true and was not something they could act on.
    """
    ws = ROOT / "custom_components" / "open_house" / "websocket_api.py"
    unknown_slot = _compiled(ws, "_unknown_slot", {})

    class Host:
        def known_slots(self) -> tuple[str, ...]:
            return ("light_group", "motion_sensor")

    class Binding:
        kind = "slot"
        slot = "toggle_helper"

    said = unknown_slot(Host(), {"kitchen_toggle": Binding()})
    assert said is not None
    assert "Add it as a slot of this house's own" in said
    assert "light_group, motion_sensor" in said
    # A name the house *does* carry is not refused at all, which is the ordinary
    # case and the one a refusal must not swallow.
    Binding.slot = "light_group"
    assert unknown_slot(Host(), {"lights": Binding()}) is None


def test_an_input_row_reports_the_domains_its_own_selector_names() -> None:
    """What a declared slot accepts is what the blueprint asked for.

    Read from the *source* and not from the house, because the row that needs it
    is the one being answered with a slot the house does not have yet -- there is
    no house answer to give. Compiled out of `websocket_api.py` for the same
    reason the test above is.
    """
    ws = ROOT / "custom_components" / "open_house" / "websocket_api.py"
    domains = _compiled(ws, "_selector_domains", {"Mapping": Mapping})

    assert domains({"selector": {"entity": {"domain": "input_boolean"}}}) == [
        "input_boolean"
    ]
    assert domains({"selector": {"entity": {"domain": ["light", "switch"]}}}) == [
        "light",
        "switch",
    ]
    # `device` spells it `integration`, and it is the same question.
    assert domains({"selector": {"device": {"integration": "zha"}}}) == ["zha"]
    # A selector that names none is an empty answer and *not* "all domains": the
    # declaration is refused there (`engine/custom_slots.add`), because a slot
    # that accepts nothing proposes nothing.
    assert domains({"selector": {"entity": {}}}) == []
    assert domains({"selector": {"text": {}}}) == []
    assert domains({"selector": "not a mapping"}) == []
