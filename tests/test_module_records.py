"""The file a house's hosted modules live in: one record each, and what they publish.

`ha_adapter.module_records` is the second half of the pure hosting layer -- the
first is `module_host`, which builds the automation, and this one is what Open
House remembers about it afterwards. The claim under test is that the file is
the whole state: a record written is a record read back, byte for byte, and a
file that is missing, empty or hand-edited to something impossible is answered
honestly rather than by inventing a module.

The tests write to `tmp_path` rather than to a fixture, because the thing being
tested is a file that a house owns and edits; a committed fixture would be a
second copy of the format that could drift from the one the writer produces.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from ha_adapter import module_host, module_records
from ha_adapter.pack_authoring import AuthoringError


def _output(**overrides: object) -> module_host.Output:
    fields: dict[str, object] = {
        "key": "lux",
        "expression": "min_lux_clear_value",
        "kind": "number",
        "variables": ("min_lux_clear_value",),
    }
    fields.update(overrides)
    return module_host.Output(**fields)  # type: ignore[arg-type]


def _record(**overrides: object) -> module_records.ModuleRecord:
    fields: dict[str, object] = {
        "slug": "dynamic_lighting",
        "title": "Dynamic Lighting",
        "blueprint": "MarqBarq/dynamic-lighting.yaml",
        "automation_id": "automation.dynamic_lighting",
        "inputs": {"lux_sensor": "sensor.lux_test"},
        "outputs": (_output(),),
    }
    fields.update(overrides)
    return module_records.ModuleRecord(**fields)  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# Naming
# --------------------------------------------------------------------------


def test_a_title_becomes_the_name_its_outputs_are_addressed_by() -> None:
    assert module_records.slug("Lights: Evening Scene") == "lights_evening_scene"


def test_a_title_with_nothing_nameable_in_it_is_refused() -> None:
    with pytest.raises(AuthoringError, match="does not have a name in it"):
        module_records.slug("...")


def test_a_record_whose_name_is_not_a_name_is_refused() -> None:
    with pytest.raises(AuthoringError, match="is not a module name"):
        _record(slug="Not A Name")


# --------------------------------------------------------------------------
# The round trip
# --------------------------------------------------------------------------


def test_a_record_survives_being_written_and_read(tmp_path: Path) -> None:
    """A file written is a file read back, with its one configuration named.

    The one thing that moves is the map. A record made in memory and not yet
    built holds no configurations, and *writing* it names the one it is holding
    -- which is what a file has to say for the answers in it to be one of a set
    rather than the only set there could ever be.
    """
    written = _record()
    module_records.write(tmp_path, [written])
    assert module_records.load(tmp_path) == (module_records.keeping_answers(written),)


def test_a_file_written_before_a_module_could_hold_several_still_reads(
    tmp_path: Path,
) -> None:
    """The map and the name are absent, so the answers are the only configuration.

    Not a migration and not a version bump: the flat five fields are what the
    file has always carried, so a file that says nothing about configurations is
    a module with one, called what the default is called.
    """
    path = module_records.write(tmp_path, [_record()])
    row = json.loads(path.read_text(encoding="utf-8"))["modules"][0]
    for written in ("variant", "variants"):
        row.pop(written)
    path.write_text(json.dumps({"version": 1, "modules": [row]}), encoding="utf-8")

    (loaded,) = module_records.load(tmp_path)
    assert loaded.variant == module_records.DEFAULT_VARIANT
    assert loaded.configurations == (module_records.DEFAULT_VARIANT,)
    assert loaded.variants == {module_records.DEFAULT_VARIANT: module_records.Variant()}


# --------------------------------------------------------------------------
# Configurations
# --------------------------------------------------------------------------


def test_a_module_holds_the_configuration_it_is_running() -> None:
    """The five flat fields *are* the active configuration, and it is in the map.

    Including in a record that was made in memory and never built, which holds no
    configurations at all until something writes it: the name has to be
    answerable either way, because it is what a switcher shows and what a switch
    is asked for.
    """
    record = _record(bindings={"lux_sensor": {"kind": "literal", "value": 4}})
    assert record.variants == {}
    assert record.configurations == (module_records.DEFAULT_VARIANT,)
    assert record.held_configurations[module_records.DEFAULT_VARIANT].bindings == {
        "lux_sensor": {"kind": "literal", "value": 4}
    }


def test_the_live_fields_win_over_the_stored_configuration() -> None:
    """A value changed and not yet written back is still the one being held.

    This is the rule that makes switching away from a configuration safe: what is
    written back when the module moves is what the module is *holding*, not what
    the last save put in the file.
    """
    record = _record(
        bindings={"lux_sensor": {"kind": "literal", "value": 9}},
        variants={
            module_records.DEFAULT_VARIANT: module_records.Variant(
                bindings={"lux_sensor": {"kind": "literal", "value": 2}}
            )
        },
    )
    held = record.held_configurations[module_records.DEFAULT_VARIANT]
    assert held.bindings == {"lux_sensor": {"kind": "literal", "value": 9}}


def test_a_configuration_keeps_the_answers_and_not_the_identity() -> None:
    """`Variant.of` takes the five answers and nothing a rebuild is given."""
    record = _record(
        bindings={"lux_sensor": {"kind": "literal", "value": 1}},
        settings=("lux_sensor",),
        picks=(("lux", "brightness"),),
        derived={"sleep": {"condition": "state", "entity_id": "input_boolean.x"}},
        flows={"presence_entity": "abc123"},
        room_id="kitchen",
    )
    assert module_records.Variant.of(record) == module_records.Variant(
        bindings={"lux_sensor": {"kind": "literal", "value": 1}},
        settings=("lux_sensor",),
        picks=(("lux", "brightness"),),
        derived={"sleep": {"condition": "state", "entity_id": "input_boolean.x"}},
        flows={"presence_entity": "abc123"},
    )


def test_applying_a_configuration_leaves_the_module_where_it_is() -> None:
    """Same slug, same room, same automation, same outputs -- only the answers move.

    Which is the whole reason switching is not a second module: the entity ids
    and the automation are found by the module's name, so a switch has to land on
    the ones that are already there.
    """
    record = _record(room_id="kitchen", automation_id="automation.dynamic_lighting")
    evening = module_records.Variant(
        bindings={"lux_sensor": {"kind": "literal", "value": 3}}
    )
    moved = evening.applied_to(record)
    assert moved.slug == record.slug
    assert moved.room_id == "kitchen"
    assert moved.automation_id == "automation.dynamic_lighting"
    assert moved.outputs == record.outputs
    assert moved.bindings == {"lux_sensor": {"kind": "literal", "value": 3}}


def test_several_configurations_survive_the_file(tmp_path: Path) -> None:
    """Every configuration is in the file, and the active one is the flat fields."""
    written = replace(
        module_records.keeping_answers(
            _record(bindings={"lux_sensor": {"kind": "literal", "value": 10}})
        ),
        variants={
            module_records.DEFAULT_VARIANT: module_records.Variant(
                bindings={"lux_sensor": {"kind": "literal", "value": 10}}
            ),
            "Evening": module_records.Variant(
                bindings={"lux_sensor": {"kind": "literal", "value": 3}},
                settings=("bypass_light",),
            ),
        },
    )
    module_records.write(tmp_path, [written])
    (loaded,) = module_records.load(tmp_path)

    assert loaded.configurations == (module_records.DEFAULT_VARIANT, "Evening")
    assert loaded.variant == module_records.DEFAULT_VARIANT
    assert loaded.bindings == {"lux_sensor": {"kind": "literal", "value": 10}}
    assert loaded.variants["Evening"].bindings == {
        "lux_sensor": {"kind": "literal", "value": 3}
    }
    assert loaded.variants["Evening"].settings == ("bypass_light",)


def test_a_record_that_has_never_been_built_signs_its_configuration_name(
    tmp_path: Path,
) -> None:
    """A module made in memory gains its one configuration on the way to the file.

    Nothing has to call `keeping_answers` first: the answers are written flat as
    they always were, and the map written beside them names the configuration
    they are, which is what makes the file readable as a module with a set.
    """
    module_records.write(tmp_path, [_record()])
    (loaded,) = module_records.load(tmp_path)
    assert list(loaded.variants) == [module_records.DEFAULT_VARIANT]
    assert loaded.variant == module_records.DEFAULT_VARIANT


def test_a_record_with_no_name_for_its_configuration_is_refused() -> None:
    with pytest.raises(AuthoringError, match="active configuration needs a name"):
        _record(variant="   ")


def test_the_outputs_are_kept_in_the_order_they_were_ticked(tmp_path: Path) -> None:
    """Order is information: it is the order the panel lists them in."""
    records = (
        _record(
            outputs=(
                _output(key="second", expression="b"),
                _output(key="first", expression="a"),
            )
        ),
    )
    module_records.write(tmp_path, records)
    loaded = module_records.load(tmp_path)
    assert [output.key for output in loaded[0].outputs] == ["second", "first"]


def test_the_variables_an_output_reads_survive(tmp_path: Path) -> None:
    """They decide where the publisher is placed, so losing them moves it."""
    module_records.write(tmp_path, [_record()])
    assert module_records.load(tmp_path)[0].outputs[0].variables == (
        "min_lux_clear_value",
    )


def test_where_an_output_is_published_survives(tmp_path: Path) -> None:
    """A value a call set is published beside that call, and the path is how.

    Losing it does not fail loudly: the publisher goes to the end of the
    automation, where a template that read the call's value renders against a
    later run's light instead -- a reading that is plausible and wrong.
    """
    output = _output(
        key="brightness",
        expression="{{ brightness | int }}",
        kind="string",
        variables=(),
        after=("action", 4, "default", 0),
        template=True,
    )
    module_records.write(tmp_path, [_record(outputs=(output,))])
    loaded = module_records.load(tmp_path)[0].outputs[0]
    assert loaded.after == ("action", 4, "default", 0)
    # The index stays a number across the file, which is what lets it address a
    # list rather than a key that happens to be spelt the same.
    assert loaded.after[1] == 4
    assert loaded.template is True


def test_the_room_a_module_sits_in_survives(tmp_path: Path) -> None:
    """A slot input resolves against it, so losing it moves the module's devices.

    A module read back with no room is a module that resolves its slots at house
    scope instead -- quietly acting on the whole house's devices where it was
    imported to act on one room's.
    """
    module_records.write(tmp_path, [_record(room_id="kitchen")])
    assert module_records.load(tmp_path)[0].room_id == "kitchen"


def test_a_module_in_no_room_is_the_house(tmp_path: Path) -> None:
    """The empty room is a real answer, and a file that spells it is read as one."""
    module_records.write(tmp_path, [_record()])
    assert module_records.load(tmp_path)[0].room_id == ""


def test_the_definition_a_module_was_installed_from_survives(tmp_path: Path) -> None:
    """It is what says where a store row is installed, and it can be absent.

    A module hosted straight from a document -- pasted in, answered and hosted --
    has no definition behind it and never will, so the empty answer has to be a
    thing the file says rather than a field it leaves out.
    """
    module_records.write(
        tmp_path, [_record(definition="dim_a_light"), _record(slug="pasted_in")]
    )
    read = module_records.load(tmp_path)
    assert read[0].definition == "dim_a_light"
    assert read[1].definition == ""


def test_a_module_built_again_keeps_the_place_it_had() -> None:
    """Rebuilding is constant -- every save and every slot binding does it -- and
    the file's order is the panel's, so a rebuilt module that moved to the end
    would move the row a person was reading out from under them."""
    records = (_record(slug="first"), _record(slug="second"), _record(slug="third"))
    put = module_records.put(records, _record(slug="second", title="Renamed"))
    assert [row.slug for row in put] == ["first", "second", "third"]
    assert put[1].title == "Renamed"


def test_a_module_that_is_new_goes_at_the_end() -> None:
    """A module nobody has seen is a module nothing has an order for yet."""
    records = (_record(slug="first"),)
    put = module_records.put(records, _record(slug="second"))
    assert [row.slug for row in put] == ["first", "second"]


def test_the_first_module_of_a_house_is_the_only_one() -> None:
    assert [row.slug for row in module_records.put((), _record())] == [
        "dynamic_lighting"
    ]


def test_a_house_with_no_modules_is_not_a_failure(tmp_path: Path) -> None:
    assert module_records.load(tmp_path) == ()


def test_writing_replaces_rather_than_appends(tmp_path: Path) -> None:
    module_records.write(tmp_path, [_record()])
    module_records.write(tmp_path, [])
    assert module_records.load(tmp_path) == ()


def test_writing_makes_the_directory_it_names(tmp_path: Path) -> None:
    """A first save is the first thing that ever writes here."""
    root = tmp_path / "open_house"
    module_records.write(root, [_record()])
    assert (root / module_records.FILENAME).is_file()


# --------------------------------------------------------------------------
# A file that is not what it should be
# --------------------------------------------------------------------------


def test_a_file_that_will_not_parse_is_named_rather_than_swallowed(
    tmp_path: Path,
) -> None:
    """Forgetting every module silently is the failure this refusal prevents."""
    (tmp_path / module_records.FILENAME).write_text("{not json", encoding="utf-8")
    with pytest.raises(AuthoringError, match="not valid JSON"):
        module_records.load(tmp_path)


def test_a_file_whose_modules_are_not_a_list_is_refused(tmp_path: Path) -> None:
    (tmp_path / module_records.FILENAME).write_text(
        json.dumps({"version": 1, "modules": "no"}), encoding="utf-8"
    )
    with pytest.raises(AuthoringError, match="not a list"):
        module_records.load(tmp_path)


def test_a_module_with_an_impossible_name_is_refused(tmp_path: Path) -> None:
    """A hand edit that names a module wrongly is caught at the read, not later.

    An unchecked name would become part of an entity id no publisher agrees with,
    which surfaces as a consumer reading unknown rather than as an error.
    """
    (tmp_path / module_records.FILENAME).write_text(
        json.dumps({"version": 1, "modules": [{"slug": "Bad Name"}]}), encoding="utf-8"
    )
    with pytest.raises(AuthoringError, match="whose name is"):
        module_records.load(tmp_path)


def test_a_module_with_an_impossible_output_is_refused(tmp_path: Path) -> None:
    (tmp_path / module_records.FILENAME).write_text(
        json.dumps(
            {"version": 1, "modules": [{"slug": "ok", "outputs": [{"key": "Bad"}]}]}
        ),
        encoding="utf-8",
    )
    with pytest.raises(AuthoringError, match="not an output name"):
        module_records.load(tmp_path)


def test_a_condition_cast_round_trips(tmp_path: Path) -> None:
    """The condition a person wrote is kept as they wrote it, not summarised.

    The entity is built from this, so a condition that came back as anything
    other than what the editor built would be an entity answering a question
    nobody asked -- and a rebuild would then bake the wrong logic in.
    """
    condition = [
        {"condition": "state", "entity_id": ["input_text.home_state"], "state": "sleep"}
    ]
    record = module_records.ModuleRecord(
        slug="dynamic_lighting",
        title="Dynamic Lighting",
        derived={"sleep_entity": condition},
    )
    module_records.write(tmp_path, [record])
    (loaded,) = module_records.load(tmp_path)
    assert loaded.derived == {"sleep_entity": condition}


def test_a_module_with_no_conditions_reads_back_with_none(tmp_path: Path) -> None:
    """A file written before this field existed is not a file that is broken."""
    (tmp_path / module_records.FILENAME).write_text(
        json.dumps({"version": 1, "modules": [{"slug": "ok"}]}), encoding="utf-8"
    )
    (loaded,) = module_records.load(tmp_path)
    assert loaded.derived == {}


def test_a_flow_cast_round_trips(tmp_path: Path) -> None:
    """The id of the flow is kept, because nothing else can work it out.

    Node-RED mints a tab's id and reassigns it on a push, so the id in the
    record is the only spelling that names the flow -- a rebuild that lost it
    would push a *second* flow and leave the first running, and the module would
    then be answered by whichever of the two wrote last.
    """
    record = module_records.ModuleRecord(
        slug="dynamic_lighting",
        title="Dynamic Lighting",
        flows={"bypass_light": "8bd567c1b81870db"},
    )
    module_records.write(tmp_path, [record])
    (loaded,) = module_records.load(tmp_path)
    assert loaded.flows == {"bypass_light": "8bd567c1b81870db"}


def test_a_module_with_no_flows_reads_back_with_none(tmp_path: Path) -> None:
    """And a file written before *this* field existed is not a broken file."""
    (tmp_path / module_records.FILENAME).write_text(
        json.dumps({"version": 1, "modules": [{"slug": "ok"}]}), encoding="utf-8"
    )
    (loaded,) = module_records.load(tmp_path)
    assert loaded.flows == {}
