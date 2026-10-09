"""The shipped templates, against the room catalog they reproduce.

`catalog/room_types.yaml` records the kinds of space and, in its `house` entry,
the slots the whole house offers. `packs/official/` ships a `room-template` for
each kind a stranger may be given and a `house-template` for the house entry, and
those seven documents are hand-written transcriptions of the catalog. A
transcription drifts: a type gains a slot, a `default` flag moves, a type is
renamed, and the template that claimed to reproduce it is quietly describing a
room that no longer exists. This module is the check that fails when they do.

**Why the pack's name is the join.** The manifest schema's own description of
`room-template` says the kind "declares what a room has -- its slots and the room
type it reproduces", and the schema carries a clause for the slots
(`requires_slots`) and none for the type. So the type has to be stated somewhere,
and a name is the one place a manifest already has: a pack called `bathroom` is
the `bathroom` type. The alternative was a key inside the pinned artefact, and
that artefact is documentation -- its class is `other`, no schema describes its
contents, and a check reading a shape nothing validates would be a check built on
a second unstated format. The name is a clause the schema does define.

**Why the house template is joined by kind instead.** There are six default room
types and one house, so the room templates are a set keyed by name and the house
template is the single document of its kind. That asymmetry is the catalog's, not
this check's: `room_types.yaml` keeps its types in a list and its house in a
single `house` key.

**What the schema cannot do and this does.** `1.2.0`'s conditional for
`room-template` and `house-template` refuses a `behaviours` clause, and that is
the whole of what it says about the two kinds -- neither is required to carry
`requires_slots` at all, and the property's own description defers the question
to "the per-kind conditionals below", which are silent. So a template with no
slot declaration, or with a slot its type does not provide, validates. Both are
findings here: the first because a template declaring nothing reproduces nothing,
the second because it is the rule the spec's scenario is written about. The
`behaviours` clause is deliberately *not* re-checked here, because the schema
refuses it before this check runs and a check nothing can reach is a claim the
code does not make good on.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from . import errors, paths, room_types, vcs
from .errors import CheckError, Report
from .narrow import as_mapping, as_sequence, as_text

TEMPLATES_CHECK = "templates"

#: The two kinds this check owns. Every other kind under `packs/official/` is
#: another check's business -- the examples are `examples.check_examples`'.
ROOM_TEMPLATE = "room-template"
HOUSE_TEMPLATE = "house-template"

#: The two declarations a template may make a slot in. Kept with their clause
#: names rather than merged into one set, because a finding has to be able to say
#: which clause carried the foreign slot: a template that requires a slot its type
#: merely offers and one that offers a slot its type has never heard of are
#: different mistakes, and only the message can tell them apart.
_SLOT_CLAUSES = ("requires_slots", "optional_slots")


@dataclass(frozen=True, slots=True)
class Template:
    """One shipped template pack, as the manifest spells it."""

    filename: str
    name: str
    kind: str
    slots: tuple[tuple[str, str], ...]

    @property
    def declared(self) -> tuple[str, ...]:
        """The slot names this template declares, in order and without repeats."""
        seen: list[str] = []
        for slot, _ in self.slots:
            if slot not in seen:
                seen.append(slot)
        return tuple(seen)


def _packs_directory() -> Path:
    return paths.PACKS / "official"


def load_templates() -> tuple[Template, ...]:
    """Every template pack shipped under `packs/official/`, or `CheckError`.

    Unreadable YAML raises rather than being skipped, for the reason
    `room_types.load_room_types` gives: a file read as absent is a template the
    drift check reports as missing, and a missing template and a corrupt one want
    different corrections. Only the two kinds are returned -- every other pack
    file in the directory is `examples.check_examples`' business, and this module
    has nothing to say about it.

    The files read are the ones the repository *ships*, not the ones the
    directory holds: the corpus is a working-tree artifact that is ignored and
    not committed (`packs/` in `.gitignore`), so a machine that keeps it for the
    demo house does not read as shipping seven templates. `vcs.listed_names_in`
    is that answer and is `None` where git cannot give one, in which case the
    directory is enumerated as it is.
    """
    directory = _packs_directory()
    if not directory.is_dir():
        return ()
    listed = vcs.listed_names_in(directory)
    templates: list[Template] = []
    for path in sorted(directory.iterdir()):
        if not path.is_file() or path.suffix not in {".yaml", ".yml"}:
            continue
        if listed is not None and path.name not in listed:
            continue
        relative = f"packs/official/{path.name}"
        try:
            text = errors.read_text(path)
        except (OSError, UnicodeDecodeError) as exc:
            raise CheckError(
                TEMPLATES_CHECK, relative, f"cannot be read: {exc}"
            ) from exc
        try:
            loaded: object = yaml.safe_load(text)
        except (yaml.YAMLError, RecursionError) as exc:
            raise CheckError(
                TEMPLATES_CHECK, relative, f"cannot be parsed: {exc}"
            ) from exc
        document = as_mapping(loaded)
        kind = as_text(document.get("kind")) or ""
        if kind not in (ROOM_TEMPLATE, HOUSE_TEMPLATE):
            continue
        templates.append(
            Template(
                filename=path.name,
                name=as_text(document.get("name")) or "",
                kind=kind,
                slots=tuple(
                    (slot, clause)
                    for clause in _SLOT_CLAUSES
                    for slot in _strings(document.get(clause))
                ),
            )
        )
    return tuple(templates)


def _strings(value: object) -> tuple[str, ...]:
    """The string entries of a list, in order, dropping anything else.

    The same reading `room_types._strings` makes, and for the same reason: a
    non-string is refused by the schema that describes the field, so reporting it
    here would be this check answering for a shape it does not own.
    """
    return tuple(
        text for text in (as_text(item) for item in as_sequence(value)) if text
    )


def check_templates(report: Report) -> None:
    """The shipped templates against `catalog/room_types.yaml`, both ways.

    The house template first, then the room templates as a set, because the two
    halves fail differently: there is at most one house template and the finding
    is about *the* house, while the room templates are one per type and the
    findings are about which one is missing, extra or wrong.

    Skipped whole when `packs/official/` is absent, and skipped again when it
    ships no template at all: the templates are a working-tree artifact and are
    not committed, so a checkout has none to drift, and a checkout whose whole
    `packs/official/` is the three schema examples has none either. Reporting
    either absence would fail the validator on the checkout this repository
    actually ships, for a set the product runs without.
    """
    if not _packs_directory().is_dir():
        return
    templates = load_templates()
    if not templates:
        return
    room_map = room_types.load_room_types()
    if not room_map.has_house:
        # `room_types.check_room_types` owns this finding, and repeating it here
        # would report one absent `house` key three times.
        return
    _check_house(report, templates, room_map.house_slots)
    _check_rooms(report, templates, room_map)


def _check_house(
    report: Report, templates: tuple[Template, ...], house_slots: tuple[str, ...]
) -> None:
    """There is one house template, and it declares the house entry's slots.

    Both directions, because the two drifts are different defects: a slot the
    catalog has and the template does not is a house-scoped pack with nowhere to
    land, and one the template has and the catalog does not is a template
    offering a placeholder nothing defines.
    """
    houses = [item for item in templates if item.kind == HOUSE_TEMPLATE]
    if not houses:
        report.add(
            TEMPLATES_CHECK,
            "packs/official",
            "ships no `house-template`; the house entry's slots are the ones no "
            "room owns, so a set without this template has no document a person "
            "can read them off",
        )
        return
    if len(houses) > 1:
        report.add(
            TEMPLATES_CHECK,
            "packs/official",
            f"ships {len(houses)} `house-template` packs "
            f"({', '.join(sorted(item.filename for item in houses))}); there is "
            "one house, so the second is a document no person was meant to choose",
        )
        return
    house = houses[0]
    at = f"packs/official/{house.filename}"
    if not house.declared:
        report.add(
            TEMPLATES_CHECK,
            at,
            "declares no slots; a house template exists to say what the whole "
            "house offers, and one that offers nothing represents no house",
        )
        return
    _check_foreign(report, house, set(house_slots), at, "the `house` entry")
    _check_absent(report, house, house_slots, at, "the `house` entry")


def _check_rooms(
    report: Report, templates: tuple[Template, ...], room_map: room_types.RoomTypeMap
) -> None:
    """One room template per `default: true` type, and each one's slots its type's.

    Only the default-marked types are expected, and the six-not-twenty rule is
    why: a template for `pool` is representable and is not shipped as a default,
    so finding one here is a finding rather than a rounding error.
    """
    by_name: dict[str, list[Template]] = {}
    for item in templates:
        if item.kind == ROOM_TEMPLATE:
            by_name.setdefault(item.name, []).append(item)

    wanted = {item.name: item for item in room_map.types if item.default}

    for name, group in sorted(by_name.items()):
        if len(group) > 1:
            report.add(
                TEMPLATES_CHECK,
                "packs/official",
                f"ships {len(group)} room templates named `{name}` "
                f"({', '.join(sorted(item.filename for item in group))}); the name "
                "is the join to the room type, so two of them speak for one type",
            )
            continue
        template = group[0]
        at = f"packs/official/{template.filename}"
        room_type = wanted.get(name)
        if room_type is None:
            report.add(
                TEMPLATES_CHECK,
                at,
                f"is named `{name}`, which is no room type the catalog marks "
                "`default: true`; the name is the join, and a template joined to "
                "nothing is a template for a kind a stranger is not offered",
            )
            continue
        if not template.declared:
            report.add(
                TEMPLATES_CHECK,
                at,
                "declares no slots; a room template exists to say what a room of "
                "its type offers, and one that offers nothing reproduces nothing",
            )
            continue
        _check_foreign(
            report,
            template,
            set(room_type.provides_slots),
            at,
            f"room type `{name}`",
        )
        _check_absent(
            report, template, room_type.provides_slots, at, f"room type `{name}`"
        )

    for name in sorted(wanted):
        if name not in by_name:
            report.add(
                TEMPLATES_CHECK,
                "packs/official",
                f"ships no room template for `{name}`; the catalog marks it "
                "`default: true`, which is the claim that a stranger may be given "
                "a room of that kind, and a kind with no template cannot be given",
            )


def _check_foreign(
    report: Report,
    template: Template,
    provided: set[str],
    at: str,
    source: str,
) -> None:
    """Every slot the template declares is one `source` provides.

    The finding names the template, the slot and the source, in that order,
    because the correction depends on which of the three is wrong and the message
    is the only place that can be worked out.
    """
    for slot, clause in template.slots:
        if slot in provided:
            continue
        report.add(
            TEMPLATES_CHECK,
            at,
            f"declares the slot `{slot}` in its `{clause}`, which {source} does "
            "not provide; a template reproduces a type's own slots and no others, "
            "so a slot borrowed from elsewhere is one its rooms cannot bind",
        )


def _check_absent(
    report: Report,
    template: Template,
    provided: tuple[str, ...],
    at: str,
    source: str,
) -> None:
    """Every slot `source` provides is one the template declares.

    The other direction, and it is not symmetry for its own sake: a type that
    gains a slot leaves every house built from the template unable to bind it,
    which is a room that is missing a placeholder and not a room that is wrong.
    """
    declared = set(template.declared)
    for slot in provided:
        if slot in declared:
            continue
        report.add(
            TEMPLATES_CHECK,
            at,
            f"does not declare the slot `{slot}`, which {source} provides; a "
            "template that drops a slot its type offers leaves every room built "
            "from it unable to bind that placeholder",
        )
