"""The shipped templates against the room catalog -- task 7.5.

`packs/official/` ships a `room-template` for each room type
`catalog/room_types.yaml` marks `default: true` and a `house-template` for the
catalog's `house` entry. Both are hand-written transcriptions of the catalog, and
a transcription drifts: a type gains a slot, a type is renamed, a `default` flag
moves. `tools/catalog/templates.py` is the check that fails when they do, and
this file is what tells apart a check that catches that and a check that only
agrees with a tree that happens to be right.

Three things about the check shape what is asserted here.

**The join is the pack's name.** The manifest schema's description of the kind
says a `room-template` declares "what a room has ... and the room type it
reproduces", and the schema carries a clause for the slots and none for the type
-- so the type is the pack's `name`, which is the one place a manifest already
has. That is why a template for a type the catalog does not mark `default` is a
finding rather than a template about a type nobody offers.

**Both directions are checked, and they are not the same defect.** A slot the
template declares and the type does not is a room that cannot bind its own
placeholder; a slot the type provides and the template drops is a room that is
merely incomplete. The two corrections differ, so they are different tests.

**The schema admits a template that declares nothing.** `1.2.0`'s conditional for
`room-template` refuses a `behaviours` clause and says nothing else -- neither
kind is required to carry `requires_slots`, and the property's own description
defers to "the per-kind conditionals below", which are silent. So a template with
no slot declaration validates, and the finding for it lives here. The
`behaviours` clause is deliberately not re-tested: the schema refuses it before
this check can see it, and a test for a branch nothing can reach is a claim the
suite does not make good on.

`room_types.check_room_types` is exercised on the same fixture tree, because this
check calls it: every fake tree below therefore carries a `catalog/room_types.yaml`
as well as a `packs/official/`, and the room-type findings are filtered out of
what these tests read so that a defect in the map is not reported as a defect in
the templates.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import yaml

from tools.catalog import paths, templates
from tools.catalog.errors import CheckError, Report

from .conftest import write

if TYPE_CHECKING:
    from pathlib import Path

ROOM_TYPES_PATH = "catalog/room_types.yaml"

#: The two default-marked types every fixture map carries, and the house entry
#: beside them. The committed tree has six and the real catalog's own names; a
#: fixture with two is the smallest tree on which "one template per type, and no
#: others" can be stated at all.
_TYPES = {"kitchen": ("light_group", "motion_sensor"), "lounge": ("light_group",)}
_HOUSE = ("house_mode", "vacuum")


def _room_types_document() -> str:
    """A map with two default types and a house entry, and nothing else.

    Both types are marked `default: true` and each is corroborated by two repos,
    so `room_types.check_room_types` has no finding of its own on this tree --
    otherwise every assertion below would be reading a report that is already
    dirty for a reason this file is not about.
    """
    return yaml.safe_dump(
        {
            "room_types": [
                {
                    "name": name,
                    "default": True,
                    "source_rooms": [
                        {"repo": "x", "room": name},
                        {"repo": "y", "room": name},
                    ],
                    "provides_slots": list(slots),
                }
                for name, slots in _TYPES.items()
            ],
            "house": {"slots": list(_HOUSE)},
        },
        sort_keys=False,
    )


def _manifest(
    name: str,
    kind: str,
    *,
    requires: tuple[str, ...] | None,
    optional: tuple[str, ...] = (),
) -> str:
    """One template manifest, with the clauses this check reads and no others.

    `requires=None` writes no `requires_slots` clause at all, which is the case
    the schema admits and the check has to catch.
    """
    lines = [
        f"name: {name}",
        'version: "1.0.0"',
        "description: a template for the test",
        f"kind: {kind}",
        'engine_api: ">=1.0.0 <2.0.0"',
        "license: mit",
    ]
    if requires is not None:
        lines.append(f"requires_slots: [{', '.join(requires)}]")
    if optional:
        lines.append(f"optional_slots: [{', '.join(optional)}]")
    lines += [
        "provides:",
        f"  - path: packs/official/{name}/thing.yaml",
        "    class: other",
        "i18n:",
        "  default:",
        f"    pack: {name}",
        "    description: a template for the test",
    ]
    return "\n".join(lines) + "\n"


def _tree(
    root: Path,
    *,
    room_templates: dict[str, tuple[str, ...] | None] | None = None,
    house: bool | tuple[str, ...] | None = ("house_mode", "vacuum"),
) -> None:
    """A fixture tree carrying the map and the templates named.

    `house` is three-valued on purpose: the default writes one house template
    with the house entry's slots, `True` writes one with the catalog's slots
    (the same thing, spelled for readability), a tuple writes one with those
    slots instead, and `False` writes none.
    """
    write(root, ROOM_TYPES_PATH, _room_types_document())
    for name, slots in (room_templates if room_templates is not None else {}).items():
        write(
            root,
            f"packs/official/{name}.yaml",
            _manifest(name, "room-template", requires=slots),
        )
    if house is False:
        return
    slots = _HOUSE if house is True else house
    write(
        root,
        "packs/official/house.yaml",
        _manifest("house", "house-template", requires=slots),
    )


def _every_room() -> dict[str, tuple[str, ...] | None]:
    """A template for each fixture type, declaring exactly its type's slots."""
    return dict(_TYPES)


def _diagnostics() -> list[tuple[str, str]]:
    """The template check's findings, collected the way the hook collects them.

    `check_templates` is registered in `validate._CHECKS`, but this helper calls
    it directly and reproduces the `CheckError` arm rather than borrowing
    `validate_all`, so a finding is attributed to this check alone. The arm is
    not decoration: the unparseable-file test is about a broken file reaching the
    hook as a diagnostic, and without it a `CheckError` would escape as a
    traceback and that test would still pass.
    """
    report = Report()
    try:
        templates.check_templates(report)
    except CheckError as exc:
        report.add(exc.check, exc.where, exc.message)
    return [
        (d.where, d.message)
        for d in report.diagnostics
        if d.check == templates.TEMPLATES_CHECK
    ]


def _messages() -> str:
    return "\n".join(f"{where}: {message}" for where, message in _diagnostics())


# --------------------------------------------------------------------------
# The committed tree
# --------------------------------------------------------------------------


def test_the_committed_templates_pass_their_own_checks() -> None:
    """The shipped set and the catalog agree, which is the phase's green half.

    Falsified by any drift at all between `catalog/room_types.yaml` and the seven
    documents under `packs/official/`: a slot added to a type without its
    template, a template renamed away from its type, a seventh default type with
    no template. The check is the phase's, so this test is the one that would
    notice the shipped set shipping wrong rather than the check being wrong.
    """
    assert _diagnostics() == []


def test_the_committed_set_is_six_room_templates_and_one_house() -> None:
    """Six and one, and the six are the default-marked types by name.

    Falsified by a seventh room template -- a `pool`, a `gazebo`, a `sunroom` --
    which the catalog marks `default: false` and which the phase therefore does
    not ship, and by a missing house template, which would leave the house-scope
    slots with no document a person can read them off.
    """
    loaded = templates.load_templates()
    rooms = sorted(item.name for item in loaded if item.kind == templates.ROOM_TEMPLATE)
    houses = [item for item in loaded if item.kind == templates.HOUSE_TEMPLATE]

    assert rooms == [
        "bathroom",
        "bedroom",
        "driveway",
        "garage",
        "kitchen",
        "living_room",
    ]
    assert [item.name for item in houses] == ["house"]


def test_each_committed_template_declares_exactly_its_type_s_slots() -> None:
    """The declaration is the type's list, read off the catalog rather than retyped.

    Falsified by a template that declares a slot its type does not provide or
    drops one it does. The catalog is read here rather than restated, so this
    test cannot agree with the check by sharing its copy of the list.
    """
    from tools.catalog import room_types

    room_map = room_types.load_room_types()
    provided = {item.name: item.provides_slots for item in room_map.types}

    for item in templates.load_templates():
        if item.kind != templates.ROOM_TEMPLATE:
            continue
        assert item.declared == provided[item.name], item.filename


def test_the_house_template_declares_the_house_entry_s_slots() -> None:
    """The house template is the catalog's `house` entry and not a room's.

    Falsified by a house template carrying a room type's slots, which would make
    the two levels `catalog/room_types.yaml` distinguishes -- a type's
    `provides_slots` and the house entry's `slots` -- into one list.
    """
    from tools.catalog import room_types

    room_map = room_types.load_room_types()
    house = next(
        item
        for item in templates.load_templates()
        if item.kind == templates.HOUSE_TEMPLATE
    )
    assert house.declared == room_map.house_slots


def test_the_committed_templates_are_the_files_the_allowlist_names() -> None:
    """Every shipped template is a pack file `HANDWRITTEN` lists.

    Falsified by a template landing under `packs/official/` unlisted, which
    `examples.check_examples` refuses in the pre-commit hook; asserted here
    because the two halves of this change arrived together and a reader of this
    file should be able to see the join.
    """
    listed = (paths.ROOT / "packs" / "official" / "HANDWRITTEN").read_text(
        encoding="utf-8"
    )
    names = {line.strip() for line in listed.splitlines() if not line.startswith("#")}
    for item in templates.load_templates():
        assert item.filename in names, item.filename


# --------------------------------------------------------------------------
# A slot the type does not provide -- the rule the spec's scenario is about
# --------------------------------------------------------------------------


def test_a_foreign_required_slot_is_named_with_its_template_and_source(
    fake_root: Path,
) -> None:
    """A required slot the type does not provide names all three.

    Falsified by a check that compares the two lists only for equality and
    reports "they differ": the correction depends on whether the template has
    something extra or is missing something, and on which of the two files is
    wrong.
    """
    _tree(
        fake_root,
        room_templates={**_every_room(), "kitchen": ("light_group", "vacuum")},
    )
    assert "declares the slot `vacuum` in its `requires_slots`" in _messages()
    assert "room type `kitchen` does not provide" in _messages()
    assert "packs/official/kitchen.yaml" in _messages()


def test_a_foreign_optional_slot_is_named_too(fake_root: Path) -> None:
    """`optional_slots` is a declaration as well, and carries the clause's own name.

    Falsified by a check that reads only `requires_slots`: a template offering a
    placeholder its type has never heard of is the same defect however softly it
    is asked for.
    """
    _tree(fake_root, room_templates=_every_room())
    write(
        fake_root,
        "packs/official/kitchen.yaml",
        _manifest(
            "kitchen",
            "room-template",
            requires=_TYPES["kitchen"],
            optional=("vacuum",),
        ),
    )
    assert "declares the slot `vacuum` in its `optional_slots`" in _messages()


def test_a_slot_the_type_provides_and_the_template_drops_is_named(
    fake_root: Path,
) -> None:
    """The other direction: a dropped slot leaves a room unable to bind it.

    Falsified by a check that only refuses what the template adds. A type that
    gains a slot and a template that does not is a house missing a placeholder,
    which reads as a room that works rather than one that is incomplete.
    """
    _tree(fake_root, room_templates={**_every_room(), "kitchen": ("light_group",)})
    assert "does not declare the slot `motion_sensor`" in _messages()
    assert "room type `kitchen` provides" in _messages()
    assert "declares the slot" not in _messages()


# --------------------------------------------------------------------------
# The set: one per default type, and no others
# --------------------------------------------------------------------------


def test_a_default_type_with_no_template_is_named(fake_root: Path) -> None:
    """A `default: true` type with no shipped template.

    Falsified by a check that only walks the templates it finds: the claim
    `default: true` makes is that a stranger may be given a room of that kind,
    and a kind with no template cannot be given.
    """
    _tree(fake_root, room_templates={"kitchen": _TYPES["kitchen"]})
    assert "ships no room template for `lounge`" in _messages()


def test_a_template_named_for_a_non_default_type_is_named(fake_root: Path) -> None:
    """A template for a type the catalog does not mark `default`.

    Falsified by a check that joins in one direction only. The name is the join,
    so a template named `pool` is a template joined to a kind a stranger is not
    offered -- representable, and not shipped.
    """
    _tree(fake_root, room_templates={**_every_room(), "pool": ("motion_sensor",)})
    assert "is named `pool`, which is no room type the catalog marks" in _messages()


def test_two_templates_for_one_type_are_named(fake_root: Path) -> None:
    """Two room templates whose name is the same type.

    Falsified by a check that folds the templates into a mapping and lets the
    second overwrite the first: one of the two would never be read, and which one
    that is would depend on the directory listing.
    """
    _tree(
        fake_root,
        room_templates={**_every_room(), "kitchen": _TYPES["kitchen"]},
    )
    write(
        fake_root,
        "packs/official/kitchen_two.yaml",
        _manifest("kitchen", "room-template", requires=_TYPES["kitchen"]),
    )
    assert "ships 2 room templates named `kitchen`" in _messages()


def test_a_missing_house_template_is_named(fake_root: Path) -> None:
    """No house template at all.

    Falsified by a check that treats the house half as optional, which would
    leave the house-scope slots -- the ones no room owns -- with nothing.
    """
    _tree(fake_root, room_templates=_every_room(), house=False)
    assert "ships no `house-template`" in _messages()


def test_two_house_templates_are_named(fake_root: Path) -> None:
    """Two documents of a kind there is one of.

    Falsified by a check that reads the first and stops. There is one house, so
    the second is a document no person was meant to choose, and which one was
    read would be the directory listing's decision.
    """
    _tree(fake_root, room_templates=_every_room())
    write(
        fake_root,
        "packs/official/house_again.yaml",
        _manifest("house", "house-template", requires=_HOUSE),
    )
    assert "ships 2 `house-template` packs" in _messages()


# --------------------------------------------------------------------------
# A declaration that is absent, which the schema admits
# --------------------------------------------------------------------------


def test_a_room_template_declaring_no_slots_is_named(fake_root: Path) -> None:
    """A `room-template` with no `requires_slots` at all.

    This is the finding the schema cannot make. `1.2.0`'s conditional for the
    kind refuses a `behaviours` clause and requires nothing else, so a template
    that declares no slots validates -- and a template that declares nothing
    reproduces nothing.
    """
    _tree(
        fake_root,
        room_templates={**_every_room(), "kitchen": None},
    )
    assert "declares no slots" in _messages()
    assert "packs/official/kitchen.yaml" in _messages()


def test_a_house_template_declaring_no_slots_is_named(fake_root: Path) -> None:
    """The same absence on the other kind, asserted separately.

    The two kinds reach the finding by different branches, and a test for one
    says nothing about the other; a house template that declares nothing is a
    house nothing can be plugged into.
    """
    _tree(fake_root, room_templates=_every_room(), house=False)
    write(
        fake_root,
        "packs/official/house.yaml",
        _manifest("house", "house-template", requires=None),
    )
    assert "declares no slots" in _messages()


# --------------------------------------------------------------------------
# The house template's own drift, and the file that will not parse
# --------------------------------------------------------------------------


def test_the_house_template_s_own_drift_is_named_in_both_directions(
    fake_root: Path,
) -> None:
    """A house template with a slot too many, and then one too few.

    Falsified by a house check that only counts slots: the two drifts want
    different corrections -- a placeholder nothing defines, and a house-scoped
    pack with nowhere to land -- and the direction each message names is what
    says which is which.
    """
    _tree(
        fake_root,
        room_templates=_every_room(),
        house=(*_HOUSE, "fan"),
    )
    assert "declares the slot `fan` in its `requires_slots`" in _messages()
    assert "the `house` entry does not provide" in _messages()

    _tree(
        fake_root,
        room_templates=_every_room(),
        house=("house_mode",),
    )
    assert "does not declare the slot `vacuum`" in _messages()


def test_a_template_that_will_not_parse_is_a_diagnostic_not_a_traceback(
    fake_root: Path,
) -> None:
    """A pack file `yaml.safe_load` cannot read reaches the hook as a diagnostic.

    Falsified by an unguarded `safe_load`, which would reach the pre-commit hook
    as a traceback instead of naming the file -- and the traceback would be
    indistinguishable from the check itself being broken.
    """
    _tree(fake_root, room_templates=_every_room())
    write(fake_root, "packs/official/kitchen.yaml", "name: [unclosed\n")
    assert _diagnostics() != []
    assert "cannot be parsed" in _messages()
    assert "packs/official/kitchen.yaml" in _messages()


def test_a_missing_pack_directory_is_not_a_crash(fake_root: Path) -> None:
    """A tree with no `packs/official/` at all reports absences, not a traceback.

    `load_templates` returns nothing rather than raising, because the directory's
    absence is a fact about the layout and `invariants.check_layout` owns it -- a
    second finding for the same missing directory would be one defect reported
    twice. What is left is the set's own claim: every default type is missing its
    template and so is the house, which is why the findings are all "ships no".
    """
    write(fake_root, ROOM_TYPES_PATH, _room_types_document())
    findings = _diagnostics()
    assert findings != []
    assert all("ships no" in message for _, message in findings)
