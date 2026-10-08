"""A binding written to a record, and read back, is the same binding.

`InputBinding` is the answer to one of a module's inputs, and it is stored as a
plain JSON row -- a record is a file, and a dataclass is not. Two functions write
those rows: `modules._bindings_json`, for a hosted module's own record, and
`module_definitions.ModuleDefinition.with_answers`, for the answers a room lays
over a definition. Both are read back by the same `InputBinding(**row)`, so a
field one of them forgets is not a field that goes missing loudly: the row simply
does not carry it, the reader takes the dataclass default, and the module is
built from an answer nobody gave.

That is not a hypothetical. `part` and `scope` were both added to `InputBinding`
after those two writers were written, and both were left out of both of them --
so a person could put a module on one part of a split slot and watch the card say
so, while the record the module is actually built from went on naming the whole
slot; and a *global slot* answer, "the house's lights", was read back as a room
answer and resolved to whatever the room had bound under that name, which is a
different device in every room. Neither write was refused. Neither screen said
anything was wrong. `modules._bindings_json`'s own docstring had already said
what to do about it --

    A record is JSON, so an `InputBinding` is written out field by field --
    including the ones left empty, because `async_update` rebuilds a binding out
    of one of these rows and a field a reader had to guess at would be a module
    built from a binding nobody chose.

-- and the field list had drifted from the dataclass anyway, which is exactly the
kind of drift a comment cannot prevent and the first test here can.

`modules.py` imports `homeassistant` at import time, so it cannot be imported by
any test in this suite; its writer is read as source through `ast`, the way
`test_ws_contract.py` reads the same module and for the same reason.
"""

from __future__ import annotations

import ast
import dataclasses
from pathlib import Path

from ha_adapter import module_definitions, module_host

ROOT = Path(__file__).resolve().parents[1]

SOURCE = """\
blueprint:
  name: Dim a light
  domain: automation
  input:
    lights:
      name: Lights
      selector:
        entity:
          domain: light
actions:
  - action: light.turn_on
    target:
      entity_id: !input lights
"""


def _fields_written(dictionary: ast.Dict) -> set[str]:
    """The string keys of a dict literal, in the order they are written."""
    return {
        key.value
        for key in dictionary.keys
        if isinstance(key, ast.Constant) and isinstance(key.value, str)
    }


def _binding_row_written(path: Path, function: str) -> set[str]:
    """The keys of the binding row a named function builds.

    Found by name rather than by line, so the test does not break when the
    function moves, and the row is found by its shape -- one dict literal that
    names `kind` -- rather than by its position, because `_bindings_json` builds
    its row inside a dict *comprehension* while `with_answers` assigns one to a
    key. Read as a literal rather than by calling it: both modules are the ones
    this suite cannot import.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or node.name != function:
            continue
        for inner in ast.walk(node):
            if isinstance(inner, ast.Dict) and "kind" in _fields_written(inner):
                return _fields_written(inner)
    raise AssertionError(f"{path.name} has no {function} that builds a binding row")


def test_every_field_of_a_binding_is_written_to_a_record() -> None:
    """The writer names every field the dataclass declares, `part` and `scope` too.

    Asked of the dataclass rather than of a list written out here, because the
    list written out here is the thing that goes stale: a field added to
    `InputBinding` tomorrow and forgotten in a serialiser is the same defect as
    the one this file exists for, and it should fail this test the same way.
    """
    declared = {field.name for field in dataclasses.fields(module_host.InputBinding)}
    written = _binding_row_written(
        ROOT / "custom_components" / "open_house" / "modules.py", "_bindings_json"
    )
    assert declared - written == set(), (
        f"`_bindings_json` writes no row for {sorted(declared - written)}, so a "
        "module rebuilt from its record is rebuilt without them"
    )


def test_a_room_s_answer_names_every_field_too() -> None:
    """The other writer, which is read back by the same `InputBinding(**row)`."""
    declared = {field.name for field in dataclasses.fields(module_host.InputBinding)}
    written = _binding_row_written(
        ROOT / "ha_adapter" / "module_definitions.py", "with_answers"
    )
    assert declared - written == set(), (
        f"`with_answers` writes no row for {sorted(declared - written)}, so an "
        "install answered with one of them is built without it"
    )


def test_the_part_a_person_chose_survives_the_round_trip() -> None:
    """A part is the whole point of the two fields: it decides *which* device.

    A module on `light_group__a` acts on the device the house bound for that part
    -- so a row that loses the part does not lose a detail, it acts on a
    different device than the one the person named, and does it silently.
    """
    definition = module_definitions.ModuleDefinition(
        slug="dim_a_light",
        title="Dim a light",
        source=SOURCE,
        description="Turn a light on.",
        blueprint="blueprints/automation/dim-a-light.yaml",
        author="A Person",
        version="1.0.0",
        licence="mit",
    )
    answered = definition.with_answers(
        bindings={
            "lights": module_host.InputBinding(
                kind="slot", slot="light_group", part="a", scope="house"
            )
        },
        settings=None,
    )
    row = answered.bindings["lights"]
    read_back = module_host.InputBinding(**row)
    assert (read_back.part, read_back.scope) == (
        "a",
        "house",
    ), f"the round trip changed the answer: {row}"

    key = module_host.slot_key(read_back)
    assert key == "light_group__a", (
        f"a binding that names a part resolves under {key!r}, which is the slot "
        "itself rather than the part -- the device would be the wrong one"
    )
