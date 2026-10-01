"""The slot vocabulary, and the half of the audit clause that needs it.

`catalog/slots.yaml` is the controlled vocabulary of placeholders a device is
bound to, and it is written *from* `lexicon.ROLE_VOCABULARY` rather than
alongside it: the lexicon is where the step from a reference to a role is
recorded once, so a slot name that is not a role and a slot whose accepting
domains disagree with the rule that decided the role are both second opinions
where one derivation already exists. The check recomputes each from the lexicon
and fails the disagreement, which is what keeps the two files from drifting.

The rest of the check is the cross-record half of the `reference-catalog`
requirements, the half no JSON Schema can state because it needs a second file.
An example is either cited to the raw records it was drawn from, or uncited --
and an uncited example needs our own description *and* a repo that grants reuse,
because with no citation there is nothing the provenance rule can resolve
against. An example from one of the two repos that grant nothing is recorded as
a description of its own, and the check reads that description against the
records the example cites to confirm it transcribes none of their identifiers.
A multi-source slot carries examples from at least two repos: an overlap is the
strongest signal a slot belongs in the defaults, and a single repo's usage does
not supply it.

The last clause is the one task 3.6 defers: that a hardcoded entry resolves to a
slot this file declares, or to a `constant` the spec's closed set fixes -- and
that it carries one of the two at all. That first half is enforced here because
it needs both files, and it is enforced here and not in the generated entry
schema so the two do not report one defect twice; the schema closes the entry
field set, and this check closes the entry's meaning.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

import yaml

from . import errors, lexicon, licenses, normalise, paths
from .errors import CheckError, Report
from .narrow import as_bool, as_mapping, as_sequence, as_text

if TYPE_CHECKING:
    from pathlib import Path

SLOTS_CHECK = "slots"

SLOTS_FILE = "slots.yaml"

#: The repository-relative path every diagnostic against the vocabulary carries.
_SLOTS_PATH = f"catalog/{SLOTS_FILE}"

#: The closed set of fixed things a hardcoded reference may target instead of a
#: slot, as the `reference-catalog` spec states it. `device_id` is a targeting
#: key and the rest are entity domains, and the set is heterogeneous on purpose:
#: the property it needs is a single one, that these are what a reference may
#: aim at when no room or house scope supplies a slot.
CONSTANTS: tuple[str, ...] = ("device_id", "device_tracker", "person", "sun", "time")


@dataclass(frozen=True, slots=True)
class Example:
    """One citing of a slot: the repo, its reuse status, and what it rests on.

    `description` is our own words and is read on two different grounds -- it
    stands in for a citation when there is none, and it is what a non-granting
    example is recorded as -- so it is carried here rather than inferred from
    whether `raw_ids` is empty.
    """

    repo: str
    reuse_status_code: str
    raw_ids: tuple[str, ...]
    description: str | None


@dataclass(frozen=True, slots=True)
class Slot:
    """One placeholder: its name, the domains it accepts, and its examples.

    `required` is `bool | None` for the reason `room_types.default` is: a slot
    that carries no flag at all has not said whether a room can be set up
    without it, and reading a missing flag as `False` would turn an omission into
    a decision nobody made.
    """

    name: str
    description: str | None
    accepts_domains: tuple[str, ...]
    required: bool | None
    source_repos: tuple[str, ...]
    examples: tuple[Example, ...]


@dataclass(frozen=True, slots=True)
class SlotVocabulary:
    """The whole file."""

    slots: tuple[Slot, ...]


def load_slots() -> SlotVocabulary:
    """The committed vocabulary, or `CheckError` if the file cannot be read.

    Raising rather than returning an empty vocabulary, for the reason
    `rooms.load_rooms` gives: an unreadable file read as an empty one would leave
    every per-slot check passing on nothing, which on the committed tree is the
    difference between the vocabulary being right and it being gone.
    `validate_all` catches `CheckError` and nothing else, so an unguarded
    `yaml.safe_load` would reach the pre-commit hook as a traceback instead of
    naming the file.
    """
    document = _read_mapping(paths.CATALOG / SLOTS_FILE, _SLOTS_PATH)
    return SlotVocabulary(
        slots=tuple(_slot(entry) for entry in as_sequence(document.get("slots")))
    )


def _slot(value: object) -> Slot:
    row = as_mapping(value)
    return Slot(
        name=as_text(row.get("name")) or "",
        description=as_text(row.get("description")),
        accepts_domains=_strings(row.get("accepts_domains")),
        required=as_bool(row.get("required")),
        source_repos=_strings(row.get("source_repos")),
        examples=tuple(_example(item) for item in as_sequence(row.get("examples"))),
    )


def _example(value: object) -> Example:
    row = as_mapping(value)
    return Example(
        repo=as_text(row.get("repo")) or "",
        reuse_status_code=as_text(row.get("reuse_status_code")) or "",
        raw_ids=_strings(row.get("raw_ids")),
        description=as_text(row.get("description")),
    )


def _strings(value: object) -> tuple[str, ...]:
    """The string entries of a list, in order, dropping anything else.

    A non-string is refused by the schema that describes the field, so reporting
    it here as a type error would be this check answering for a shape it does not
    own; what is kept is the names, which are what the checks below join on.
    """
    return tuple(
        text for text in (as_text(item) for item in as_sequence(value)) if text
    )


def _read_mapping(path: Path, relative: str) -> dict[str, object]:
    """A committed YAML mapping, or `CheckError` naming the file."""
    if not path.is_file():
        raise CheckError(SLOTS_CHECK, relative, "does not exist")
    try:
        text = errors.read_text(path)
    except (OSError, UnicodeDecodeError) as exc:
        raise CheckError(SLOTS_CHECK, relative, f"cannot be read: {exc}") from exc
    try:
        loaded: object = yaml.safe_load(text)
    except (yaml.YAMLError, RecursionError) as exc:
        raise CheckError(SLOTS_CHECK, relative, f"cannot be parsed: {exc}") from exc
    return as_mapping(loaded)


def _repo_statuses() -> dict[str, str]:
    """Each repo's code reuse status, from the licence records.

    Read from `licenses.yaml` rather than kept as a second table here, because
    an example's status is copied from the record so the example is auditable
    without a join, and a second table would be free to disagree with the one the
    copy came from.
    """
    return {
        record.repo: record.reuse_status_code
        for record in licenses.load_licences()
        if record.repo
    }


def _raw_entity_refs() -> dict[str, frozenset[str]]:
    """Every raw record's entity references, keyed by record id.

    Read because the one thing the check has to confirm about a non-granting
    example is that it transcribes none of the identifiers of the records it
    cites, and the record store is where those identifiers are written down.
    Raising rather than returning `{}`: with no record store every transcription
    check would pass on nothing, which is the failure this clause exists to
    catch, arriving through the guard meant to keep it out.
    """
    relative = f"catalog/{normalise.RAW_BEHAVIORS_FILE}"
    path = paths.CATALOG / normalise.RAW_BEHAVIORS_FILE
    if not path.is_file():
        raise CheckError(
            SLOTS_CHECK,
            relative,
            "does not exist; a non-granting example is checked against the "
            "entity references of the records it cites, so the record store "
            "cannot be absent",
        )
    try:
        document = as_mapping(json.loads(errors.read_text(path)))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CheckError(
            SLOTS_CHECK, relative, f"cannot be read as JSON: {exc}"
        ) from exc

    out: dict[str, frozenset[str]] = {}
    for record in as_sequence(document.get("records")):
        row = as_mapping(record)
        record_id = as_text(row.get("id"))
        if record_id:
            out[record_id] = frozenset(
                text
                for text in (
                    as_text(item) for item in as_sequence(row.get("entity_refs"))
                )
                if text
            )
    return out


def _audit_entries() -> list[dict[str, object]]:
    """The committed audit's entries, from the store the ledger is written to."""
    relative = f"catalog/{normalise.HARDCODED_REFS_FILE}"
    document = _read_mapping(paths.CATALOG / normalise.HARDCODED_REFS_FILE, relative)
    return [
        row
        for row in (as_mapping(entry) for entry in as_sequence(document.get("refs")))
        if row
    ]


def check_slots(report: Report) -> None:
    """The vocabulary against the lexicon, itself, and the audit it names.

    The order is deliberate. A vocabulary that declares nothing is reported and
    stops, because every per-slot finding below would be about a slot that is
    not there and the audit half would report every entry at once. Otherwise each
    slot is checked from the inside against the rule that decided it, and the
    audit is checked last, because it is the only half that needs both files.
    """
    vocabulary = load_slots()
    if not vocabulary.slots:
        report.add(
            SLOTS_CHECK,
            _SLOTS_PATH,
            "declares no slots; the vocabulary exists to fix the placeholders a "
            "pack binds to, and one that names none answers nothing",
        )
        return

    statuses = _repo_statuses()
    raw_refs = _raw_entity_refs()
    declared = {slot.name for slot in vocabulary.slots if slot.name}
    for slot in vocabulary.slots:
        _check_slot(report, slot, statuses, raw_refs)
    _check_audit(report, declared)


def _check_slot(
    report: Report,
    slot: Slot,
    statuses: dict[str, str],
    raw_refs: dict[str, frozenset[str]],
) -> None:
    """One slot: its name, its domains, its sources, and its examples.

    The name and the accepting domains are checked against the lexicon, which is
    the derivation the file is written from. Everything after that is the
    cross-record half: the examples, then the multi-source rule over them.
    """
    at = f"{_SLOTS_PATH}:{slot.name or '<unnamed>'}"

    if not slot.name:
        report.add(SLOTS_CHECK, at, "carries no `name`")
    elif slot.name not in lexicon.ROLE_VOCABULARY:
        report.add(
            SLOTS_CHECK,
            at,
            f"is named `{slot.name}`, which is outside the controlled vocabulary "
            f"{list(lexicon.ROLE_VOCABULARY)}; a slot name invented at a point of "
            "use is what the vocabulary exists to refuse",
        )
    else:
        derived = lexicon.accepts_domains(slot.name)
        if slot.accepts_domains != derived:
            report.add(
                SLOTS_CHECK,
                at,
                f"accepts {list(slot.accepts_domains)}, but the rule that decides "
                f"`{slot.name}` accepts {list(derived)}; the accepting domains are "
                "read off the lexicon so one vocabulary is shared, and a slot that "
                "disagrees with it is a second opinion where one is derived",
            )

    if not slot.source_repos:
        report.add(
            SLOTS_CHECK,
            at,
            "names no source repo; a slot exists because real usage produced it, "
            "and one no repo corroborates is a placeholder nothing binds to",
        )
    if not slot.examples:
        report.add(
            SLOTS_CHECK,
            at,
            "carries no example; a slot with nothing drawn from usage is a name "
            "somebody chose, and the examples are what make it a derivation",
        )

    for index, example in enumerate(slot.examples):
        _check_example(report, slot, example, index, statuses, raw_refs, at)

    if len(set(slot.source_repos)) >= 2:
        cited = {example.repo for example in slot.examples if example.repo}
        if len(cited) < 2:
            report.add(
                SLOTS_CHECK,
                at,
                f"names the source repos {list(slot.source_repos)}, but its "
                f"examples come from {sorted(cited) or 'no repo'}; an overlap "
                "across repos is the strongest signal a slot belongs in the "
                "defaults, so a multi-source slot carries examples from at least "
                "two different repos",
            )


def _check_example(
    report: Report,
    slot: Slot,
    example: Example,
    index: int,
    statuses: dict[str, str],
    raw_refs: dict[str, frozenset[str]],
    at: str,
) -> None:
    """One example: its repo, its status, its citation, and its provenance."""
    where = f"{at}#{index + 1}"
    repo = example.repo
    expected = statuses.get(repo)

    if repo and expected is None:
        report.add(
            SLOTS_CHECK,
            where,
            f"cites repo `{repo}`, which has no licence record; an example's "
            "reuse status is read off its repo's record, so one with no record is "
            "an example there is nothing to check it against",
        )
        return
    if expected is not None and example.reuse_status_code != expected:
        report.add(
            SLOTS_CHECK,
            where,
            f"cites `{repo}` as `{example.reuse_status_code}`, but `{repo}` is "
            f"`{expected}`; the code is copied from the licence record so the "
            "example is auditable without a join, and one that disagrees is a "
            "second opinion where the record is the answer",
        )
    if repo and slot.source_repos and repo not in slot.source_repos:
        report.add(
            SLOTS_CHECK,
            where,
            f"is drawn from `{repo}`, which the slot's `source_repos` "
            f"{list(slot.source_repos)} does not name; a slot's sources are the "
            "repos its usage came from, and an example outside them comes from "
            "usage the slot does not claim",
        )

    granting = expected == "reusable"
    if not example.raw_ids:
        if example.description is None:
            report.add(
                SLOTS_CHECK,
                where,
                "carries no `raw_ids` and no `description`; an example is either "
                "cited to the raw records it was drawn from, or recorded as a "
                "description of the pattern in our own words",
            )
        if not granting:
            report.add(
                SLOTS_CHECK,
                where,
                f"carries no `raw_ids` and is drawn from `{repo}`, which grants no "
                "reuse; with no citation there is nothing the provenance rule can "
                "resolve against, so an uncited example comes from a repo that "
                "grants reuse",
            )
    else:
        if not granting and example.description is None:
            report.add(
                SLOTS_CHECK,
                where,
                f"is drawn from `{repo}`, which grants no reuse, and carries no "
                "`description`; such a repo's expression may not be transcribed, "
                "so the example is recorded as a description of the pattern in "
                "our own words",
            )
        elif not granting:
            _check_not_transcribed(report, example, raw_refs, where, repo)


def _check_not_transcribed(
    report: Report,
    example: Example,
    raw_refs: dict[str, frozenset[str]],
    where: str,
    repo: str,
) -> None:
    """A non-granting example contains no identifier of the records it cites.

    The identifiers are read off the records the example itself cites, so the
    clause is resolved against the example's own provenance rather than against
    every reference the corpus holds -- the same discipline the gate applies to
    rows, with the example's citations playing the part a row's `raw_ids` play.
    """
    text = example.description or ""
    if not text:
        return
    for ref in sorted(
        {ref for rid in example.raw_ids for ref in raw_refs.get(rid, ())}
    ):
        if ref not in text:
            continue
        report.add(
            SLOTS_CHECK,
            where,
            f"is drawn from `{repo}`, which grants no reuse, and contains the "
            f"entity reference `{ref}` from one of its own `raw_ids`; an "
            "identifier is a fact we may record, but a non-granting repo's "
            "spelling is what the licence position withholds, so the example is a "
            "description of the pattern in our own words",
        )


def _check_audit(report: Report, declared: set[str]) -> None:
    """Every audit entry maps to a slot or a constant, and names one that exists.

    Three clauses, and the first is the one task 3.6 deferred to 5.3: an entry
    with neither a `slot` nor a `constant` fails, because the requirement is
    that every committed entry resolves to one of the two and the residue that
    resolves to neither is not committed at all. The other two are the halves
    that need `catalog/slots.yaml`: a `slot` this file declares, and a
    `constant` the spec's closed set fixes. Each finding names the reference and
    its source repo, which is what a reader needs to find the entry rather than
    merely know one is wrong.
    """
    for entry in _audit_entries():
        ref = as_text(entry.get("entity_ref")) or "<unnamed>"
        repo = as_text(entry.get("repo")) or "<unnamed>"
        where = f"catalog/{normalise.HARDCODED_REFS_FILE}:{ref}@{repo}"

        slot = as_text(entry.get("slot"))
        constant = as_text(entry.get("constant"))
        if slot is None and constant is None:
            report.add(
                SLOTS_CHECK,
                where,
                "maps to neither a slot nor a constant; every audit entry "
                "resolves to a slot in `catalog/slots.yaml` or a constant the "
                "spec's closed set fixes, and an entry with neither records a "
                "reference nobody has decided what to do with",
            )
        if slot is not None and slot not in declared:
            report.add(
                SLOTS_CHECK,
                where,
                f"maps to slot `{slot}`, which `{_SLOTS_PATH}` does not declare; a "
                "slot is a placeholder the controlled vocabulary fixes, so an "
                "entry naming one outside it names a placeholder no pack can bind",
            )

        if constant is not None and constant not in CONSTANTS:
            report.add(
                SLOTS_CHECK,
                where,
                f"justifies itself with constant `{constant}`, which is outside "
                f"the closed set {list(CONSTANTS)}; those are the fixed things a "
                "reference may target instead of a slot, so a value outside them "
                "justifies nothing",
            )
