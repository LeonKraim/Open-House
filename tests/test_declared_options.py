"""A pack's `options` clause, from the manifest to the control the panel draws.

`pack-manifest/1.3.0`'s `options` is the clause a person actually sees: it is
what turns "the fridge has been open too long" into a number a household can set
without editing a YAML file. So there are three links between the manifest text
and the form, and each has a way of being broken that the others cannot see:

- **The declaration reaches the unit.** An installed record keeps a digest and
  not a document (`engine/install.py`), so the only place the manifest and the
  pack are still together is the build. A field that stopped being carried there
  would leave the panel asking a unit that has nothing to say, and the symptom
  would be an empty form rather than a failure.
- **The declaration becomes a drawable node.** The panel renders a *subset* of
  JSON Schema (`panel/src/components/schema-spec.ts`), and a keyword outside it
  draws as `unsupported`. So the node's `type` decides which control appears --
  checkbox, number field, select -- and the test asserts the type rather than
  the rendered widget, because the type is what the panel branches on.
- **The declaration keeps the author's words.** A form whose labels are
  humanised config keys (`option.motion.lux_threshold`) is a form nobody can
  read, which is the complaint the clause answers.

The house is the `minimal` fixture and the pack is the generated one, exactly as
in `test_declared_clauses.py`: a declaration that only ever met a hand-written
dictionary would be a declaration never checked against a manifest.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from engine.behaviours.declared import option_key, option_rows
from engine.vocabulary import Vocabulary
from ha_adapter.live_profiles import option_properties
from openhouse.facade import open_session
from tools.catalog import paths

from .packfactory import SLOT, pack

ROOT = paths.ROOT

#: The pack the tests build. One `boolean` and one `duration`, because the two
#: are the controls the product asks for by name ("set numbers booleans ... with
#: proper check boxes") and they are the two the projection could most easily get
#: wrong: a boolean drawn from a Python `True` is an `int` to every naive type
#: test, and a duration is an `integer` that means seconds.
DECLARED = (
    "description: a pack for the test\n"
    "options:\n"
    "  - key: enabled_for_rooms\n"
    "    type: boolean\n"
    "    default: true\n"
    "    title: Watch this room\n"
    "    description: Whether this room's fridge is watched at all.\n"
    "  - key: open_for\n"
    "    type: duration\n"
    "    default: 120\n"
    "    title: Door left open for\n"
    "    description: How long the door may stand open.\n"
    "    unit: seconds\n"
    "    minimum: 5\n"
    "    maximum: 3600\n"
)

BOOLEAN = option_key("declaring", "enabled_for_rooms")
DURATION = option_key("declaring", "open_for")


@pytest.fixture(scope="module")
def vocabulary() -> Vocabulary:
    """The committed catalog, loaded once for the module."""
    return Vocabulary.load(ROOT)


def _installed(tmp_path: Path, vocabulary: Vocabulary) -> object:
    """A session with the declaring pack installed, and its one unit returned."""
    session = open_session(house="minimal", vocabulary=vocabulary, house_settings={})
    session.install_pack(
        str(
            pack(
                tmp_path,
                "declaring",
                edits=(("description: a pack for the test", DECLARED),),
            )
        )
    )
    unit = session.engine.behaviours["declaring.b0"]
    return session, unit


def _rows() -> list[dict[str, object]]:
    """The two option rows as a manifest would hand them over."""
    return [dict(row) for row in option_rows({"options": _declared_document()})]


def _declared_document() -> list[dict[str, object]]:
    """The same rows as data, for the projection tests that need no session."""
    return [
        {
            "key": "enabled_for_rooms",
            "type": "boolean",
            "default": True,
            "title": "Watch this room",
            "description": "Whether this room's fridge is watched at all.",
        },
        {
            "key": "open_for",
            "type": "duration",
            "default": 120,
            "title": "Door left open for",
            "description": "How long the door may stand open.",
            "unit": "seconds",
            "minimum": 5,
            "maximum": 3600,
        },
    ]


# -- the declaration reaches the unit -----------------------------------------


def test_the_installed_unit_carries_the_packs_declared_options(
    tmp_path: Path, vocabulary: Vocabulary
) -> None:
    """What the manifest wrote is what the unit holds, byte for byte.

    The link the panel depends on and the one no other test can see: an installed
    record keeps the pack's digest rather than its text, so a build that dropped
    the clause would leave every room's settings page empty with nothing failing.
    """
    _session, unit = _installed(tmp_path, vocabulary)

    assert [row["key"] for row in unit.options] == ["enabled_for_rooms", "open_for"]
    assert unit.options[1]["default"] == 120


def test_a_pack_with_no_options_carries_none(tmp_path: Path) -> None:
    """Silence is not an empty declaration, and both read as "nothing to set"."""
    assert option_rows({"behaviours": []}) == ()
    assert option_rows({"options": "not a list"}) == ()


# -- the declaration becomes a drawable node ----------------------------------


def test_a_boolean_option_is_a_boolean_node() -> None:
    """`type: boolean` is a checkbox, and the default stays a boolean.

    The `bool`-before-`int` trap in the other direction: a projection that
    described the default's Python type would call `True` an integer and draw a
    number field, so the assertion is on the node's type rather than on the
    label.
    """
    node = option_properties("declaring", _rows())[BOOLEAN]

    assert node["type"] == "boolean"
    assert node["default"] is True


def test_a_duration_option_is_a_whole_number_with_a_floor() -> None:
    """A duration is seconds, bounded below, and says so in its own words.

    The floor matters more than it looks: it is what makes the panel's number
    input refuse a negative duration before the engine ever sees one, and the
    unit is what stops a person entering `120` into a field that never said
    whether that was seconds or minutes.
    """
    node = option_properties("declaring", _rows())[DURATION]

    assert node["type"] == "integer"
    assert node["minimum"] == 5
    assert node["maximum"] == 3600
    assert "seconds" in str(node["description"])


def test_the_authors_words_are_the_labels() -> None:
    """The title is the manifest's, not the key humanised.

    `enabled_for_rooms` humanised is `Enabled for rooms`, which is a sentence
    about the engine; `Watch this room` is the one the author wrote for a person.
    """
    node = option_properties("declaring", _rows())[BOOLEAN]

    assert node["title"] == "Watch this room"
    assert node["description"].startswith("Whether this room's fridge")


def test_the_declaration_is_addressable_by_the_key_a_value_resolves_under() -> None:
    """The property name is `module.<pack>.<key>` -- one spelling, three uses."""
    assert set(option_properties("declaring", _rows())) == {BOOLEAN, DURATION}
    assert BOOLEAN == "module.declaring.enabled_for_rooms"


def test_the_slot_the_pack_declares_still_reaches_it() -> None:
    """The generated pack's own slot resolves, so the fixture is the real thing."""
    assert SLOT == "light_group"


# -- the roles a module addresses ---------------------------------------------


def test_a_role_a_pack_acts_through_becomes_a_checkbox() -> None:
    """Every action slot draws a boolean, defaulting to reached.

    The control the product asks for by name -- a person unticking "act on the
    thermostats" rather than editing a manifest -- and the default is the whole
    of its compatibility: `True` means every pack installed before this setting
    existed behaves exactly as it did, so the field is additive rather than a
    switch that has to be flipped on.

    The description names the pack that declared the role, because a room's
    options are one form for every module installed in it: two modules that both
    act on the lights draw two boxes reading "Act on light group", and without
    the name the pair is indistinguishable.
    """
    from ha_adapter.live_profiles import reach_properties

    nodes = reach_properties("bedtime", ["light_group", "door_contact"])

    assert set(nodes) == {
        "module.bedtime.reach.light_group",
        "module.bedtime.reach.door_contact",
    }
    node = nodes["module.bedtime.reach.door_contact"]
    assert node["type"] == "boolean"
    assert node["default"] is True
    assert node["title"] == "Act on door contact"
    assert node["description"] == (
        "Untick to leave door contact alone: the module keeps running but "
        "stops writing to this role -- from the pack bedtime."
    )


def test_a_role_reached_twice_is_one_checkbox() -> None:
    """Two behaviours acting through one slot draw one field, not two.

    A pack with a "lights on" behaviour and a "lights off" one acts on
    `light_group` twice; a form that drew two checkboxes for the one role would
    be a form where unticking one of them appears to do nothing, because the
    other still writes through it.
    """
    from ha_adapter.live_profiles import reach_properties

    nodes = reach_properties("bedtime", ["light_group", "light_group"])

    assert list(nodes) == ["module.bedtime.reach.light_group"]


def test_a_pack_that_acts_on_nothing_has_no_role_fields() -> None:
    """A behaviour proposing a mode and no command contributes no checkbox."""
    from ha_adapter.live_profiles import reach_properties

    assert reach_properties("bedtime", []) == {}
