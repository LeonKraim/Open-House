"""The modules a house offers: a file each, and the file is the export.

`ha_adapter.module_definitions` is the third part of the pure hosting layer, and
the one with a job the other two do not have: it reads a document that came *from
somewhere else*. A module is an automation with a name, and a file that says it is
one is a file whose contents are going to run -- so the claim under test is that
nothing arrives half-understood. Every field is checked, every answer names
something the document actually asks for, and a refusal says which of the two
things is wrong rather than which line failed to parse.

The tests write to `tmp_path` for the reason `test_module_records` gives: the
thing being tested is a file a house owns, and a committed fixture would be a
second copy of the format to drift from the writer's.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ha_adapter import module_definitions, module_records
from ha_adapter.pack_authoring import AuthoringError

# A blueprint with the two shapes a definition cares about: an input answered
# with a device (`lights`), an input answered with a number, and an action that
# carries both through.
SOURCE = """\
blueprint:
  name: Dim a light
  description: Turn a light on at a brightness.
  domain: automation
  input:
    lights:
      name: Lights
      selector:
        entity:
          domain: light
    brightness_pct:
      name: Brightness
      default: 60
      selector:
        number:
          min: 1
          max: 100
    lux_sensor:
      name: Lux sensor
      selector:
        entity:
          domain: sensor
          device_class: illuminance
actions:
  - action: light.turn_on
    target:
      entity_id: !input lights
    data:
      brightness_pct: !input brightness_pct
  - condition: numeric_state
    entity_id: !input lux_sensor
    below: 40
"""


def _definition(**overrides: object) -> module_definitions.ModuleDefinition:
    fields: dict[str, object] = {
        "slug": "dim_a_light",
        "title": "Dim a light",
        "source": SOURCE,
        "description": "Turn a light on at a brightness.",
        "blueprint": "blueprints/automation/dim-a-light.yaml",
        "author": "A Person",
        "version": "1.0.0",
        "licence": "mit",
    }
    fields.update(overrides)
    return module_definitions.ModuleDefinition(**fields)  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# What a module is allowed to be called, and to say about itself
# --------------------------------------------------------------------------


def test_the_codes_a_module_may_carry_are_the_catalog_s_codes() -> None:
    """The one duplicate in this module, held against the thing it duplicates.

    The codes are restated rather than read, because a pure layer cannot go
    looking for a catalog at import time. That is only safe while something
    notices a sixth code arriving, and this is that something.
    """
    schema = json.loads(
        (
            Path(__file__).resolve().parent.parent / "schemas/catalog/licenses.json"
        ).read_text(encoding="utf-8")
    )
    published = schema["$defs"]["licenceValue"]["enum"]
    assert tuple(published) == module_definitions.LICENCES


def test_a_name_that_is_not_a_name_is_refused() -> None:
    with pytest.raises(AuthoringError, match="is not a module name"):
        _definition(slug="Dim A Light")


def test_a_module_needs_a_name_a_person_can_read() -> None:
    with pytest.raises(AuthoringError, match="a name a person can read"):
        _definition(title="   ")


def test_a_version_that_is_a_sentence_is_refused() -> None:
    with pytest.raises(AuthoringError, match="is not a version"):
        _definition(version="the second one")


def test_a_version_with_dots_in_it_is_kept_as_written() -> None:
    assert _definition(version="1.2.3").version == "1.2.3"


def test_a_licence_this_house_does_not_know_is_refused() -> None:
    with pytest.raises(AuthoringError, match="is not a licence this house knows"):
        _definition(licence="MIT")


# --------------------------------------------------------------------------
# The document, which is the module
# --------------------------------------------------------------------------


def test_a_module_that_carries_no_document_is_refused() -> None:
    with pytest.raises(AuthoringError, match="has to carry the document"):
        _definition(source="   ")


def test_a_document_that_will_not_parse_is_refused_by_name() -> None:
    with pytest.raises(AuthoringError, match="not valid YAML"):
        _definition(source="actions: [unclosed")


def test_an_answer_to_an_input_the_document_does_not_ask_for_is_refused() -> None:
    with pytest.raises(AuthoringError, match="asks for no input by that name"):
        _definition(bindings={"hue": {"kind": "literal", "value": "green"}})


def test_a_setting_the_document_does_not_ask_for_is_refused() -> None:
    with pytest.raises(AuthoringError, match="asks for no input by that name"):
        _definition(settings=("target_brightness",))


def test_the_answers_the_document_does_ask_for_are_kept() -> None:
    kept = _definition(bindings={"lights": {"kind": "slot", "slot": "ceiling_light"}})
    assert kept.bindings["lights"]["slot"] == "ceiling_light"


# --------------------------------------------------------------------------
# What a definition says about where it can go
# --------------------------------------------------------------------------


def test_a_module_answered_with_a_device_is_pinned_to_the_house_that_made_it() -> None:
    assert _definition(
        bindings={"lights": {"kind": "entity", "value": "light.kitchen"}}
    ).pinned


def test_a_module_answered_with_a_slot_is_not_pinned() -> None:
    assert not _definition(
        bindings={"lights": {"kind": "slot", "slot": "ceiling_light"}}
    ).pinned


def test_the_slots_a_module_reaches_through_are_named_once_each() -> None:
    """Two inputs onto one slot is one slot, not two.

    A module that dims a light *by* a lux reading answers two inputs -- the light
    and the sensor -- and reaches a room through two slots; a module whose
    brightness follows its light answers two inputs and reaches a room through
    one. What a room is asked to bind is the second list, so it has to be
    deduplicated and ordered rather than the answers in the order they were made.
    """
    definition = _definition(
        bindings={
            "lux_sensor": {"kind": "slot", "slot": "ambient_light_sensor"},
            "lights": {"kind": "slot", "slot": "ceiling_light"},
            "brightness_pct": {"kind": "slot", "slot": "ceiling_light"},
        }
    )
    assert definition.slots == ("ambient_light_sensor", "ceiling_light")


# --------------------------------------------------------------------------
# The round trip, and the export, which is the same trip
# --------------------------------------------------------------------------


def test_a_definition_survives_being_written_and_read(tmp_path: Path) -> None:
    written = _definition(
        bindings={"lights": {"kind": "slot", "slot": "ceiling_light"}},
        settings=("brightness_pct",),
        picks=(("service:light.turn_on:brightness_pct", "brightness"),),
    )
    module_definitions.write(tmp_path, written)
    assert (
        module_definitions.load(module_definitions.path_of(tmp_path, written.slug))
        == written
    )


def test_a_module_lives_in_a_file_named_after_it(tmp_path: Path) -> None:
    written = _definition()
    path = module_definitions.write(tmp_path, written)
    assert path.name == "dim_a_light.json"
    assert module_definitions.load_all(tmp_path) == (written,)


def test_a_house_that_has_offered_nothing_answers_with_nothing(tmp_path: Path) -> None:
    assert module_definitions.load_all(tmp_path / "nowhere") == ()


def test_every_module_a_house_offers_is_read_back_by_name(tmp_path: Path) -> None:
    """Written in one order, read in another: the store is a list of names.

    Nothing in the file says where it comes in the list, so the list cannot depend
    on the order a person happened to make them in -- which is the same reason
    `module_records.put` keeps a rebuilt module in its place.
    """
    one = _definition()
    two = _definition(slug="dim_a_lamp", title="Dim a lamp")
    module_definitions.write(tmp_path, one)
    module_definitions.write(tmp_path, two)
    assert module_definitions.load_all(tmp_path) == (two, one)


def test_a_file_that_calls_itself_something_else_is_refused(tmp_path: Path) -> None:
    """The file is the module, so the two names have to agree.

    A row in the store, an installed module and its output entities are all named
    from the slug; a file whose *name* said one thing and whose contents said
    another would make deleting it remove something else.
    """
    document = module_definitions.to_document(_definition())
    (tmp_path / "dim_a_lamp.json").write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(AuthoringError, match="a module's file is named after it"):
        module_definitions.load_all(tmp_path)


def test_the_export_is_the_file(tmp_path: Path) -> None:
    """What a person sends to another house is what the store writes.

    One shape, so an exported module can be dropped into a config directory and
    an imported one can be handed straight back out.
    """
    written = _definition()
    path = module_definitions.write(tmp_path, written)
    document = json.loads(path.read_text(encoding="utf-8"))
    assert document["open_house_module"] == 1
    assert module_definitions.from_document(document) == written


def test_a_file_that_says_it_is_a_module_and_is_a_version_this_house_does_not_read() -> (
    None
):
    with pytest.raises(AuthoringError, match="reads version"):
        module_definitions.from_document({"open_house_module": 2, "definition": {}})


def test_a_document_that_is_not_a_module_is_refused_as_such() -> None:
    with pytest.raises(AuthoringError, match="is not a module"):
        module_definitions.from_document({"profiles": []})


def test_a_module_with_no_definition_in_it_is_refused() -> None:
    with pytest.raises(AuthoringError, match="no definition in it"):
        module_definitions.from_document({"open_house_module": 1})


def test_an_answer_that_is_not_a_way_this_house_fills_an_input_is_refused() -> None:
    document = dict(module_definitions.to_document(_definition()))
    document["definition"] = {
        **document["definition"],
        "bindings": {"lights": {"kind": "whatever", "value": "light.kitchen"}},
    }
    with pytest.raises(AuthoringError, match="not a way this house fills an input"):
        module_definitions.from_document(document)


def test_a_slot_answer_that_names_no_slot_is_refused() -> None:
    """The one malformed answer that is otherwise silent.

    Every other bad shape fails loudly at install time. A slot with no name is
    carried happily to a room that then has nothing to bind, which is a module
    that waits forever with no screen able to say what for.
    """
    document = dict(module_definitions.to_document(_definition()))
    document["definition"] = {
        **document["definition"],
        "bindings": {"lights": {"kind": "slot"}},
    }
    with pytest.raises(AuthoringError, match="names no slot"):
        module_definitions.from_document(document)


# --------------------------------------------------------------------------
# Answers laid over the defaults, and taking a module back
# --------------------------------------------------------------------------


def test_an_install_s_answers_do_not_drop_the_definition_s_own() -> None:
    definition = _definition(
        bindings={
            "lights": {"kind": "slot", "slot": "ceiling_light"},
            "brightness_pct": {"kind": "literal", "value": 60},
        }
    )

    class _Binding:
        kind = "literal"
        value = 20
        module = ""
        key = ""
        slot = ""

    merged = definition.with_answers(
        bindings={"brightness_pct": _Binding()}, settings=None
    )
    assert merged.bindings["brightness_pct"] == {
        "kind": "literal",
        "value": 20,
        "module": "",
        "key": "",
        "slot": "",
    }
    assert merged.bindings["lights"]["slot"] == "ceiling_light"


def test_taking_a_module_back_leaves_the_house_without_it(tmp_path: Path) -> None:
    module_definitions.write(tmp_path, _definition())
    module_definitions.remove(tmp_path, "dim_a_light")
    assert module_definitions.load_all(tmp_path) == ()


def test_taking_back_a_module_that_was_never_offered_is_refused(tmp_path: Path) -> None:
    with pytest.raises(AuthoringError, match="nothing to remove"):
        module_definitions.remove(tmp_path, "dim_a_light")


# --------------------------------------------------------------------------
# Editing the module, and every installation made from it
# --------------------------------------------------------------------------
#
# `follow` is the one function in this package that answers a question about
# *two* things at once -- a module, and a room's copy of it -- so the tests are
# arranged as the answer is: what the module says, what the room has, and which
# of the two the result came from.

# The same blueprint after an edit that stops reading the lux sensor. An edit *is*
# a new document -- that is what makes editing the module different from changing
# a setting -- so it is written out rather than patched out of `SOURCE`.
EDITED = """\
blueprint:
  name: Dim a light
  description: Turn a light on at a brightness.
  domain: automation
  input:
    lights:
      name: Lights
      selector:
        entity:
          domain: light
    brightness_pct:
      name: Brightness
      default: 60
      selector:
        number:
          min: 1
          max: 100
actions:
  - action: light.turn_on
    target:
      entity_id: !input lights
    data:
      brightness_pct: !input brightness_pct
"""


def _installed(**overrides: object) -> module_records.ModuleRecord:
    fields: dict[str, object] = {
        "slug": "dim_a_light",
        "title": "Dim a light",
        "blueprint": "blueprints/automation/dim-a-light.yaml",
        "definition": "dim_a_light",
        "source": SOURCE,
        "inputs": {"lights": "light.kitchen"},
    }
    fields.update(overrides)
    return module_records.ModuleRecord(**fields)  # type: ignore[arg-type]


_SLOT = {"kind": "slot", "slot": "ceiling_light"}
_LAMP = {"kind": "slot", "slot": "floor_lamp"}


def test_an_installation_that_took_the_module_as_it_came_follows_it() -> None:
    """The point of the whole function: the module changed, so the room did."""
    result = module_definitions.follow(
        _definition(bindings={"lights": _SLOT}),
        _definition(bindings={"lights": _LAMP}),
        _installed(bindings={"lights": _SLOT}),
    )
    assert result.bindings["lights"] == _LAMP


def test_an_answer_the_room_moved_is_the_room_s() -> None:
    """Somebody pinned a real light; an edit elsewhere does not unpin it.

    This is the whole reason `follow` is not a copy. The edit says `floor_lamp`,
    the definition said `ceiling_light`, and this room said `light.kitchen` --
    three different things, and only one of them is a person's own decision about
    *their* house.
    """
    result = module_definitions.follow(
        _definition(bindings={"lights": _SLOT}),
        _definition(bindings={"lights": _LAMP}),
        _installed(bindings={"lights": {"kind": "entity", "value": "light.kitchen"}}),
    )
    assert result.bindings["lights"] == {"kind": "entity", "value": "light.kitchen"}


def test_the_edit_is_what_the_installation_is_now_built_from() -> None:
    """The document travels with the answers, or the answers mean nothing."""
    result = module_definitions.follow(
        _definition(bindings={"lights": _SLOT}),
        _definition(source=EDITED, bindings={"lights": _SLOT}),
        _installed(bindings={"lights": _SLOT}),
    )
    assert result.source == EDITED


def test_a_setting_the_module_starts_offering_is_a_dial_in_every_room() -> None:
    """What a module exposes is the module's, and that is what was asked for.

    *"change what it exposes input and output and how it exposes it"* -- an edit
    that turns an input into a setting has to reach the rooms that already
    installed the module, not only the ones that install it afterwards.
    """
    result = module_definitions.follow(
        _definition(bindings={"lights": _SLOT}, settings=("brightness_pct",)),
        _definition(
            bindings={"lights": _SLOT},
            settings=("brightness_pct", "lights"),
        ),
        _installed(bindings={"lights": _SLOT}, settings=("brightness_pct",)),
    )
    assert set(result.settings) == {"brightness_pct", "lights"}


def test_a_setting_the_module_stops_offering_stops_being_a_dial() -> None:
    result = module_definitions.follow(
        _definition(bindings={"lights": _SLOT}, settings=("brightness_pct", "lights")),
        _definition(bindings={"lights": _SLOT}, settings=("lights",)),
        _installed(bindings={"lights": _SLOT}, settings=("brightness_pct", "lights")),
    )
    assert set(result.settings) == {"lights"}


def test_a_setting_a_build_grew_is_not_taken_away_by_an_edit() -> None:
    """An input nothing answers is one the build makes settable, and it stays.

    `_async_build` grows a setting for every declared input with no default
    nobody answered, because that is the only way such a module can ever be
    given what it needs. Dropping it here would leave the module unbuildable --
    which is not an edit, it is a hole.
    """
    result = module_definitions.follow(
        _definition(bindings={"lights": _SLOT}, settings=("brightness_pct",)),
        _definition(bindings={"lights": _SLOT}, settings=("brightness_pct",)),
        _installed(
            bindings={"lights": _SLOT},
            settings=("brightness_pct", "lights"),
        ),
    )
    assert set(result.settings) == {"brightness_pct", "lights"}


def test_what_a_module_publishes_is_replaced_rather_than_merged() -> None:
    """Outputs are ticked on the module and by nothing a room ever does."""
    result = module_definitions.follow(
        _definition(bindings={"lights": _SLOT}, picks=(("old", "was"),)),
        _definition(bindings={"lights": _SLOT}, picks=(("new", "now"),)),
        _installed(bindings={"lights": _SLOT}, picks=(("old", "was"),)),
    )
    assert result.picks == (("new", "now"),)


def test_a_condition_the_person_wrote_is_left_alone() -> None:
    """The same rule as a binding, for the same reason: somebody chose it."""
    mine = {"condition": "state", "entity_id": "input_boolean.night", "state": "on"}
    result = module_definitions.follow(
        _definition(bindings={"lights": _SLOT}, derived={"lux_sensor": {"below": 40}}),
        _definition(
            bindings={"lights": _SLOT},
            derived={"lux_sensor": {"below": 60}},
        ),
        _installed(bindings={"lights": _SLOT}, derived={"lux_sensor": mine}),
    )
    assert result.derived["lux_sensor"] == mine


def test_a_condition_the_module_rewrites_reaches_the_rooms_that_took_it_as_it_was() -> (
    None
):
    result = module_definitions.follow(
        _definition(bindings={"lights": _SLOT}, derived={"lux_sensor": {"below": 40}}),
        _definition(
            bindings={"lights": _SLOT},
            derived={"lux_sensor": {"below": 60}},
        ),
        _installed(
            bindings={"lights": _SLOT},
            derived={"lux_sensor": {"below": 40}},
        ),
    )
    assert result.derived["lux_sensor"] == {"below": 60}


def test_an_answer_for_an_input_the_new_document_does_not_ask_for_goes() -> None:
    """A module that stopped reading the lux sensor stops being answered one.

    All four kinds of answer at once, because they are all keyed by the input
    name and all four would otherwise be written at a document that has nothing
    to put them in -- which is a module that cannot be built, not one that was
    edited.
    """
    result = module_definitions.follow(
        _definition(
            bindings={"lux_sensor": {"kind": "slot", "slot": "ambient_light_sensor"}},
            settings=("lux_sensor",),
            derived={"lux_sensor": {"below": 40}},
            flows=("lux_sensor",),
        ),
        _definition(source=EDITED),
        _installed(
            bindings={"lux_sensor": {"kind": "slot", "slot": "ambient_light_sensor"}},
            settings=("lux_sensor",),
            derived={"lux_sensor": {"below": 40}},
            flows={"lux_sensor": "node-red-flow-id"},
        ),
    )
    assert result.bindings == {}
    assert result.settings == ()
    assert result.derived == {}
    assert result.flows == {}


def test_a_flow_this_house_added_is_kept_beside_the_module_s() -> None:
    """A flow is the one answer that reaches out of the house, so its id is kept.

    The *name* is the module's -- an edit that stops answering an input by flow
    takes the flow off -- but the *id* is Node-RED's, and the module has never
    seen it. So a name the module carries keeps whatever id this house has for
    it, and a name only this house has is kept whole.
    """
    result = module_definitions.follow(
        _definition(bindings={"lights": _SLOT}, flows=("lux_sensor",)),
        _definition(bindings={"lights": _SLOT}, flows=("lux_sensor", "lights")),
        _installed(
            bindings={"lights": _SLOT},
            flows={"lux_sensor": "already-pushed", "brightness_pct": "house-only"},
        ),
    )
    assert result.flows == {
        "lux_sensor": "already-pushed",
        "lights": "",
        "brightness_pct": "house-only",
    }


def test_an_edit_reaches_every_configuration_the_module_holds() -> None:
    """Every setting of the answers follows, not only the one in use.

    Which inputs a module exposes is a fact about the module. A second
    configuration holding a setting the module no longer offers is one that
    cannot be switched back to, so it is not left holding it.
    """
    evening = module_records.Variant(
        bindings={"lights": _SLOT},
        settings=("brightness_pct", "lux_sensor"),
    )
    record = _installed(
        bindings={"lights": _SLOT},
        settings=("brightness_pct",),
        variants={"Default": evening},
        variant="Evening",
    )
    result = module_definitions.follow(
        _definition(
            bindings={"lights": _SLOT}, settings=("brightness_pct", "lux_sensor")
        ),
        _definition(
            bindings={"lights": _LAMP},
            settings=("brightness_pct",),
        ),
        record,
    )
    assert set(result.held_configurations) == {"Default", "Evening"}
    for name, configuration in result.held_configurations.items():
        assert configuration.bindings["lights"] == _LAMP, name
        assert set(configuration.settings) == {"brightness_pct"}, name


def test_the_installation_keeps_its_name_its_room_and_its_automation() -> None:
    """An edit is the same module answering differently, not a second module.

    The automation and the output entities are found by the module's name, so an
    edit that changed either would leave the old automation running beside the
    new one rather than replacing it.
    """
    record = _installed(
        bindings={"lights": _SLOT},
        room_id="kitchen",
        automation_id="automation.dim_a_light_kitchen",
    )
    result = module_definitions.follow(
        _definition(bindings={"lights": _SLOT}),
        _definition(bindings={"lights": _LAMP}),
        record,
    )
    assert result.slug == record.slug
    assert result.room_id == "kitchen"
    assert result.automation_id == "automation.dim_a_light_kitchen"
    assert result.definition == "dim_a_light"


# -- Publishing a row's logic: the switch beside a cast ---------------------
#
# The four below are the rule from the row to the store, with no screen and no
# Home Assistant in the way -- which is the half that can be tested here. The
# other half (writing the definition, rebuilding every installation that never
# moved the answer) is `modules.async_publish`, and the suite has no harness that
# can drive it; the walk in `panel/scripts/` is what proves that end.


def test_publishing_a_row_adds_the_pick_and_names_it_after_the_row() -> None:
    """The switch, as data: one pick, keyed by the row's own name.

    `cast:<input>` is the candidate name `module_host.output_candidates` offers
    for a row answered with logic, so this is the same pick the import screen
    makes when a person ticks that row's line -- which is the whole point of
    offering it from the row as well: one decision, so the two cannot disagree.
    """
    assert module_definitions.published_picks((), "min_lux", True) == (
        ("cast:min_lux", "min_lux"),
    )


def test_unpublishing_removes_that_pick_and_leaves_the_others() -> None:
    """Off is the same pick coming out, and nothing else moving.

    A module may publish several values, so what comes out is the one row's pick
    rather than the list: a switch that emptied the module's outputs would take
    away readings nobody touched.
    """
    picks = (("cast:min_lux", "floor"), ("input:lux_sensor", "lux"))
    assert module_definitions.published_picks(picks, "min_lux", False) == (
        ("input:lux_sensor", "lux"),
    )


def test_the_row_is_found_by_candidate_and_not_by_the_key_it_was_given() -> None:
    """A person may rename an output, and the switch still finds its own row.

    The key is theirs -- the import screen offers it as a field to fill in -- so
    a row is matched by the candidate it came from. Matching on the key would
    mean a value somebody renamed could no longer be switched off from the row
    that publishes it.
    """
    assert (
        module_definitions.published_picks(
            (("cast:min_lux", "floor_level"),), "min_lux", False
        )
        == ()
    )


def test_publishing_twice_under_one_name_is_refused() -> None:
    """Two outputs with one key are one entity, so the second is a refusal.

    Refused *before* the definition is written, which is what makes the refusal
    worth having at all: `declare_outputs` writes the same sentence, but it
    writes it at build time, when the store already holds a module that cannot
    be built.
    """
    with pytest.raises(AuthoringError):
        module_definitions.published_picks(
            (("input:lux_sensor", "min_lux"),), "min_lux", True
        )


def test_a_row_whose_name_cannot_be_a_key_is_refused() -> None:
    """An output key is part of an entity id, so it is refused rather than made up.

    A row named in a way no rule can map to a key is refused instead of being
    slugged into something nobody could have predicted -- and would not find
    again under the entity it landed on.
    """
    with pytest.raises(AuthoringError):
        module_definitions.published_key("---")
