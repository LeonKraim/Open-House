"""The real rooms: one record per room, with every file that names it.

`catalog/rooms.yaml` is the evidence base the room-type map is derived from, so
the thing it has to be is *complete per repo* and *tied to a real file*. Two
properties carry that.

The first is that the unit is a room and not a file. A room almost always turns
up in more than one file -- johnkoht's `office` is a package directory, a
dashboard and a set of helpers; renemarc's `kitchen` is one file per lighting
action; ccostan's `living_room` is an automation, a scene and a dashboard popup.
Recording that as one entry per file would make the file a path inventory with a
room column, and "does this setup have a kitchen" would have as many answers as
the room has files. So a room is recorded once and carries its files as
`paths`: the sources the room was read from, every one of them.

The second is that a room is *named by* its own sources. A record says `name:
office`, `repo: johnkoht`, and a list of paths; the claim those paths make is
that each of them names the room. That claim is checked rather than assumed, and
it is what stops the list decaying into a hand-kept set of arbitrary files: a
path that does not name the room is a diagnostic, and so is a selected file that
names the room and is *not* in the list. Both directions together fix the list
exactly -- which is the property that makes dropping one visible, since the file
cannot silently shed a room's evidence.

Naming is the repo's, never ours. `wohnzimmer`, `master_bath` and
`upstairs_hallway` are kept as the repos spell them, including a German name no
other repo has and the abbreviation one repo uses for a bathroom. The one piece
of judgement is which names are rooms at all, and it is confined to the `paths`
membership rule this module implements; the room *names* are written down, not
derived, because a rule that derived them from the trees could only recognise a
room it had already been told about.

Everything here reads committed artifacts -- `rooms.yaml` and the committed
`inventory.json` -- so it runs in CI, where the clones are absent. The clones are
an input to the one-time extraction whose output is committed, exactly as for
`inventory.json` itself.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

import yaml

from . import errors, inventory, licenses, paths
from .errors import CheckError, Report
from .narrow import as_bool, as_mapping, as_sequence, as_text

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

ROOMS_CHECK = "rooms"

ROOMS_FILE = "rooms.yaml"

#: A path segment's trailing extension, removed before tokenising so that
#: `living_room.yaml` and `living_room` name the room the same way.
_EXTENSION = re.compile(r"\.[A-Za-z0-9]+$")

#: What separates the words of a name. Underscores and hyphens are the two the
#: repos use (`living_room`, `bt-proxy-wohnzimmer`); spaces are included because
#: a room name taken from a `friendly_name` may carry them. A name is compared
#: case-insensitively, because johnkoht writes the same room `office` in a path
#: and `Office room` in a title.
_SEPARATORS = re.compile(r"[_\-\s]+")


@dataclass(frozen=True, slots=True)
class Room:
    """One room, as its repo names it, with the files that name it."""

    name: str
    repo: str
    paths: tuple[str, ...]


def words(text: str) -> tuple[str, ...]:
    """The words of a name, folded for comparison.

    Kept as its own function because the room name and the path segment are
    tokenised by exactly the same rule: splitting one differently from the other
    is how `main_bedroom` stops matching `main_bedroom_lights_off`.
    """
    return tuple(part for part in _SEPARATORS.split(text.casefold()) if part)


def _segment_words(segment: str) -> tuple[str, ...]:
    return words(_EXTENSION.sub("", segment))


def names_room(name: str, path: str) -> bool:
    """Whether a path names a room.

    The room's words must appear **contiguously** in one segment of the path --
    as the whole segment (`packages/office/`), or as a run inside it
    (`automations/areas/kitchen_on.yaml`, whose stem carries `kitchen` before the
    action). Contiguity is what keeps `room` from matching `bedroom` and `main`
    from matching `maintenance`: a set membership test would fire on both, and a
    substring test on `room` would match half the tree.

    The comparison is per segment rather than across the whole path, so
    `areas/kitchen/on.yaml` and `areas/kitchen_on.yaml` both match and
    `kitchen/areas/on.yaml` matches nothing -- the words of a name are adjacent
    in the name a person wrote, not scattered across a directory walk.
    """
    wanted = words(name)
    if not wanted:
        return False
    for segment in path.split("/"):
        segment_words = _segment_words(segment)
        span = len(wanted)
        for start in range(len(segment_words) - span + 1):
            if segment_words[start : start + span] == wanted:
                return True
    return False


def naming_paths(name: str, candidates: Iterable[str]) -> tuple[str, ...]:
    """Every candidate the room is named by, sorted.

    Sorted so the list is a function of the inputs and not of iteration order,
    and returned whole rather than filtered by the caller: the same result has to
    serve the extraction that first wrote `rooms.yaml` and the check that
    re-derives it, or the two would disagree about the file they are meant to
    hold together.
    """
    return tuple(
        sorted(candidate for candidate in candidates if names_room(name, candidate))
    )


def selected_paths() -> dict[str, tuple[str, ...]]:
    """Each repo's selected files, from the committed inventory.

    Read from `inventory.json` rather than from a clone, and read as *selected*
    files rather than tracked ones: `rooms.yaml` records the rooms of the
    configuration, and the excluded paths in these repos are vendored bundles,
    prose and screenshots -- `www/screenshots/group-bedroom.png` names a room and
    is not evidence that a room exists in anyone's configuration.
    """
    document = inventory.load_inventory()
    out: dict[str, tuple[str, ...]] = {}
    for entry in as_sequence(document.get("repos")):
        row = as_mapping(entry)
        repo = as_text(row.get("repo"))
        if not repo:
            continue
        out[repo] = tuple(sorted(_selected_of(row.get("files"))))
    return out


def _selected_of(files: object) -> list[str]:
    out: list[str] = []
    for item in as_sequence(files):
        row = as_mapping(item)
        path = as_text(row.get("path"))
        if path and as_bool(row.get("selected")) is True:
            out.append(path)
    return out


def load_rooms() -> tuple[Room, ...]:
    """The committed rooms, or `CheckError` if the file cannot be read.

    Raising rather than returning `[]`, for the reason `licenses.load_licences`
    gives: an empty list is the answer for "there are no rooms", so a check that
    read a corrupt file as an empty one would report nothing -- the failure the
    completeness clause exists to prevent, arriving through the guard meant to
    prevent it. `validate_all` catches `CheckError` and nothing else, so an
    unguarded `yaml.safe_load` would reach the pre-commit hook as a traceback
    instead of naming the file.
    """
    path = paths.CATALOG / ROOMS_FILE
    relative = f"catalog/{ROOMS_FILE}"
    if not path.is_file():
        raise CheckError(ROOMS_CHECK, relative, "does not exist")
    try:
        text = errors.read_text(path)
    except (OSError, UnicodeDecodeError) as exc:
        raise CheckError(ROOMS_CHECK, relative, f"cannot be read: {exc}") from exc
    try:
        loaded: object = yaml.safe_load(text)
    except (yaml.YAMLError, RecursionError) as exc:
        raise CheckError(ROOMS_CHECK, relative, f"cannot be parsed: {exc}") from exc
    document = as_mapping(loaded)

    out: list[Room] = []
    for entry in as_sequence(document.get("rooms")):
        row = as_mapping(entry)
        if not row:
            continue
        out.append(
            Room(
                name=as_text(row.get("name")) or "",
                repo=as_text(row.get("repo")) or "",
                paths=tuple(
                    text
                    for text in (
                        as_text(item) for item in as_sequence(row.get("paths"))
                    )
                    if text
                ),
            )
        )
    return tuple(out)


def check_rooms(report: Report) -> None:
    """Every room against the committed inventory it claims its paths from.

    Two of the findings are the task's own verify clauses: a repo the project
    draws from contributes no room, and a room's source path is not a selected
    file. The rest are what make the list a check rather than a list, and the
    strongest is the completeness one -- a selected file that names the room and
    is missing from its `paths`. That and the per-path name test pin the file
    from both sides: the paths it lists must name the room, and the selected
    paths it does not list must not, so a room's evidence cannot be quietly
    reduced.
    """
    records = load_rooms()
    where = f"catalog/{ROOMS_FILE}"
    if not records:
        report.add(ROOMS_CHECK, where, "records no rooms")
        return

    selected = selected_paths()
    recorded = [record.repo for record in licenses.load_licences() if record.repo]

    _check_repo_coverage(report, records, recorded, where)
    for room in records:
        _check_room(report, room, selected, recorded)
    _check_distinct(report, records)


def _check_repo_coverage(
    report: Report,
    records: tuple[Room, ...],
    recorded: list[str],
    where: str,
) -> None:
    """Every repo the project draws from has at least one room.

    The repo list is `licenses.yaml`'s -- already the project's record of which
    repositories it draws from -- so a fifth clone added there contributes no
    room to a file nothing would otherwise notice was missing it. It is also the
    one direction in which a *removed* room is visible: a repo whose rooms are
    all deleted fails here, which is the most a committed artifact can say
    without the clone.
    """
    present = {room.repo for room in records}
    for repo in recorded:
        if repo not in present:
            report.add(
                ROOMS_CHECK,
                where,
                f"records no room in `{repo}`; it is a repository the project "
                "draws from, so its rooms emptying out would be a loss the room-"
                "type map could not tell from a repo that never had one",
            )


def _check_room(
    report: Report,
    room: Room,
    selected: Mapping[str, tuple[str, ...]],
    recorded: list[str],
) -> None:
    """One record: it carries a name and a repo, and the repo is one we know.

    The early returns are deliberate and each costs a later finding, which is
    the point: a record with no repo cannot be asked which inventory its paths
    come from, so the honest report is the missing repo and not a cascade of
    path findings derived from a repo that was never named.
    """
    where = (
        f"catalog/{ROOMS_FILE}:{room.repo or '<unnamed>'}/{room.name or '<unnamed>'}"
    )

    if not room.name:
        report.add(ROOMS_CHECK, where, "carries no `name`")
        return
    if not room.repo:
        report.add(ROOMS_CHECK, where, "carries no `repo`")
        return
    if room.repo not in recorded:
        report.add(
            ROOMS_CHECK,
            where,
            f"names `{room.repo}`, which is no repository in "
            "`catalog/licenses.yaml`; a room has to belong to a repo the project "
            "records, or its provenance is a claim nothing can resolve",
        )
        return

    known = selected.get(room.repo, ())
    if not room.paths:
        report.add(
            ROOMS_CHECK,
            where,
            "names no source path; the record exists to say which file the room "
            "was read from, and one that names none says nothing",
        )
        return

    _check_paths(report, room, where, known)
    _check_completeness(report, room, where, known)


def _check_paths(
    report: Report, room: Room, where: str, known: tuple[str, ...]
) -> None:
    """Each listed path is a selected file, and it names the room.

    The two are tested with `elif` rather than in sequence, so a path that is
    not a selected file is reported as such and not also as not naming the room:
    the second test is the weaker finding, and reporting both would bury the
    first under a claim about a path that is not a fact about the repo at all.
    """
    for path in room.paths:
        if path not in known:
            report.add(
                ROOMS_CHECK,
                where,
                f"names `{path}`, which is not a selected file in `{room.repo}`'s "
                "committed inventory; a source path is a fact about the repo, so "
                "one that no selected file matches is a path about somewhere else",
            )
        elif not names_room(room.name, path):
            report.add(
                ROOMS_CHECK,
                where,
                f"names `{path}`, which does not name the room `{room.name}`; the "
                "list is the room's evidence, and a file that does not mention the "
                "room is evidence of nothing",
            )


def _check_completeness(
    report: Report, room: Room, where: str, known: tuple[str, ...]
) -> None:
    """The other direction: every selected file naming the room is listed.

    Without this the `paths` list could keep only the files somebody happened to
    type, and the room would look fully evidenced while half its configuration
    went unrecorded -- the failure a completeness rule exists to catch, arriving
    as the rule's own omission.
    """
    listed = set(room.paths)
    missing = [path for path in naming_paths(room.name, known) if path not in listed]
    if missing:
        report.add(
            ROOMS_CHECK,
            where,
            f"omits {len(missing)} selected file(s) that name the room, the first "
            f"being {missing[0]!r}; a room's evidence is every file that names it, "
            "so a list that stops short is a room the map would under-count",
        )


def _check_distinct(report: Report, records: tuple[Room, ...]) -> None:
    """One record per (repo, name).

    Two records for one room would each carry its evidence, so the check above
    would pass on both while the map counted the room twice; and a name repeated
    inside one repo is the ordinary way a copy-pasted entry hides.
    """
    seen: set[tuple[str, str]] = set()
    for room in records:
        key = (room.repo.casefold(), room.name.casefold())
        if key in seen:
            report.add(
                ROOMS_CHECK,
                f"catalog/{ROOMS_FILE}:{room.repo}/{room.name}",
                "is recorded twice; the unit is the room, so a second record for "
                "it splits one room's evidence across two entries and doubles it "
                "in every count taken from this file",
            )
            continue
        seen.add(key)
