"""The slot vocabulary -- task 5.3.

`catalog/slots.yaml` is the controlled vocabulary the whole corpus names slots
by, and it is written from the role lexicon rather than beside it, so the tests
come in the same two kinds the lexicon's do. The first kind is a property of the
committed file: every name is a role, every role has a slot, every slot's
accepting domains are the ones its rule gives, every example is cited or
described, a multi-source slot has examples from two repos, and no example from
a repo that grants nothing transcribes an identifier of the records it cites.
Those are asserted against the committed tree, because a fixture that merely
resembles the repository cannot tell us the repository is right.

The second kind is a check seen to fail. Every clause the requirements state is
also exercised on a tree built to violate it, because a check proven only against
the tree it protects is a check that has never been observed to fail -- and the
acceptance stage wants exactly that observation, one violating fixture per
requirement.

The audit clause has two halves and both are checked here. Every committed
entry must resolve to a slot or a constant -- an entry with neither is a
reference nobody has decided what to do with -- and the one it names must be a
slot `catalog/slots.yaml` declares or a constant the spec's closed set fixes.
The all-null half was vacuous while the audit committed every entry with both
fields null, so it is asserted directly on the committed entries and on a
fixture that leaves both null.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import yaml

from tools.catalog import lexicon, licenses, normalise, paths, slots
from tools.catalog.errors import Report

from .conftest import write

if TYPE_CHECKING:
    from pathlib import Path

#: Four repos with the code licences the committed records carry, so an example's
#: `reuse_status_code` resolves the way it does on the real tree: the two granting
#: repos are `reusable` and the two that grant nothing are `ideas_only`.
_LICENCES = """repos:
  - repo: ccostan
    author: A
    license_code: mit
  - repo: renemarc
    author: B
    license_code: apache_2_0
  - repo: fwartner
    author: C
    license_code: no_licence
  - repo: johnkoht
    author: D
    license_code: no_licence
"""

#: The id one record carries in the fixtures below, so an example can cite it.
_RECORD = "ccostan_light"


def _example(**overrides: object) -> dict[str, object]:
    """A well-formed, cited example, before any test spoils one field."""
    example: dict[str, object] = {
        "repo": "ccostan",
        "reuse_status_code": "reusable",
        "raw_ids": [_RECORD],
    }
    example.update(overrides)
    return example


def _slot(**overrides: object) -> dict[str, object]:
    """A well-formed slot, before any test spoils one field."""
    slot: dict[str, object] = {
        "name": "light_group",
        "description": "The switchable lighting unit for a space.",
        "accepts_domains": ["light"],
        "required": True,
        "source_repos": ["ccostan"],
        "examples": [_example()],
    }
    slot.update(overrides)
    return slot


def _seed(
    root: Path,
    vocabulary: list[dict[str, object]],
    *,
    refs: list[dict[str, object]] | None = None,
    records: list[dict[str, object]] | None = None,
) -> None:
    """A tree carrying the vocabulary under test and the files it joins to.

    The licence records and the raw record store are written for every fixture,
    even the ones that do not read them, because `check_slots` cannot run without
    both -- it resolves an example's status through the first and a non-granting
    example's transcription through the second -- and a fixture that omitted them
    would be testing the guard rather than the clause.
    """
    write(root, "catalog/licenses.yaml", _LICENCES)
    write(
        root,
        "catalog/raw-behaviors.json",
        json.dumps(
            {"records": records if records is not None else [], "change_notice": None},
            indent=2,
        )
        + "\n",
    )
    write(
        root,
        "catalog/hardcoded_refs.yaml",
        yaml.safe_dump({"refs": refs if refs is not None else []}, sort_keys=False),
    )
    write(
        root,
        "catalog/slots.yaml",
        yaml.safe_dump({"slots": vocabulary}, sort_keys=False, allow_unicode=True),
    )


def _findings() -> list[tuple[str, str]]:
    """`check_slots`' findings over the committed tree or a fixture."""
    report = Report()
    slots.check_slots(report)
    return [
        (d.where, d.message) for d in report.diagnostics if d.check == slots.SLOTS_CHECK
    ]


def _messages(findings: list[tuple[str, str]]) -> str:
    return "\n".join(f"{where}: {message}" for where, message in findings)


# --------------------------------------------------------------------------
# The fixture itself
# --------------------------------------------------------------------------


def test_the_vocabulary_fixture_is_clean(fake_root: Path) -> None:
    """The base fixture satisfies every rule, so each negative test below is
    asserting the one defect it introduced rather than a hole in the fixture."""
    _seed(fake_root, [_slot()])
    assert _findings() == []


# --------------------------------------------------------------------------
# Task 5.3 -- the committed vocabulary against the lexicon it is written from
# --------------------------------------------------------------------------


def test_the_committed_vocabulary_passes_its_check(real_root: Path) -> None:
    """The check against the artifact it protects, which no fixture can show."""
    assert _findings() == []


def test_every_committed_slot_name_is_a_lexicon_role(real_root: Path) -> None:
    """Task 5.3: "every slot name is in the controlled vocabulary".

    Stated as a property of the data rather than through the diagnostic that
    enforces it: a name the lexicon does not carry is a spelling invented at the
    point of use, which is what the vocabulary exists to refuse.
    """
    vocabulary = slots.load_slots()
    names = {slot.name for slot in vocabulary.slots}
    assert names <= set(lexicon.ROLE_VOCABULARY), sorted(
        names - set(lexicon.ROLE_VOCABULARY)
    )


def test_every_lexicon_role_has_a_committed_slot(real_root: Path) -> None:
    """The other direction, so the vocabulary cannot shrink to the slots that
    happen to be easy: a role with no slot is one `room_types.yaml` provides and
    no pack can bind, and the map would name a placeholder nothing declares."""
    names = {slot.name for slot in slots.load_slots().slots}
    assert set(lexicon.ROLE_VOCABULARY) <= names, sorted(
        set(lexicon.ROLE_VOCABULARY) - names
    )


def test_every_committed_slot_accepts_the_domains_of_its_rule(real_root: Path) -> None:
    """The accepting domains are the lexicon's answer, not a second opinion.

    Compared on the committed file rather than through the check, because the
    claim is about the artifact: a slot whose domains disagree with the rule that
    decided the role could bind a device the role never meant.
    """
    for slot in slots.load_slots().slots:
        assert slot.accepts_domains == lexicon.accepts_domains(slot.name), slot.name


def test_every_committed_slot_carries_at_least_one_example(real_root: Path) -> None:
    """Task 5.3: "every slot has ≥1 example"."""
    for slot in slots.load_slots().slots:
        assert slot.examples, slot.name


def test_every_committed_example_carries_its_repos_own_status(real_root: Path) -> None:
    """Task 5.3: each example names a source repo and its `reuse_status_code`.

    Checked against the licence record rather than for presence: a code that is
    present but wrong is exactly what makes an example unauditable without the
    join it was copied to avoid.
    """
    statuses = {
        record.repo: record.reuse_status_code for record in licenses.load_licences()
    }
    assert statuses, "the licence records name no repos to compare against"
    for slot in slots.load_slots().slots:
        for example in slot.examples:
            assert example.repo in statuses, (slot.name, example.repo)
            assert example.reuse_status_code == statuses[example.repo], (
                slot.name,
                example.repo,
            )


def test_every_committed_example_is_cited_or_described(real_root: Path) -> None:
    """Task 5.3: an example carries `raw_ids`, or carries none with a description
    and a repo that grants reuse.

    Both halves of the alternative are asserted, because an uncited example from
    a repo that withholds its expression has no citation for the provenance rule
    to resolve, which is the case the spec fails outright.
    """
    statuses = {
        record.repo: record.reuse_status_code for record in licenses.load_licences()
    }
    for slot in slots.load_slots().slots:
        for example in slot.examples:
            if example.raw_ids:
                continue
            assert example.description, (
                slot.name,
                example.repo,
                "uncited, no description",
            )
            assert statuses[example.repo] == "reusable", (
                slot.name,
                example.repo,
                "uncited from a repo that grants nothing",
            )


def test_every_multi_source_slot_has_examples_from_two_repos(real_root: Path) -> None:
    """Task 5.3: every multi-source slot has examples from ≥2 repos."""
    for slot in slots.load_slots().slots:
        if len(set(slot.source_repos)) < 2:
            continue
        cited = {example.repo for example in slot.examples}
        assert len(cited) >= 2, (slot.name, sorted(cited))


def test_no_committed_non_granting_example_transcribes_its_own_records(
    real_root: Path,
) -> None:
    """Task 5.3: a non-granting example is a description and transcribes nothing.

    The identifiers are read out of the committed record store, because the
    clause is about what the example's own citations resolve to -- a description
    that happens to contain another record's identifier is a different thing and
    is not what this requirement withholds.
    """
    document = json.loads(
        (paths.CATALOG / normalise.RAW_BEHAVIORS_FILE).read_bytes().decode("utf-8")
    )
    by_id = {row["id"]: row.get("entity_refs") or [] for row in document["records"]}
    statuses = {
        record.repo: record.reuse_status_code for record in licenses.load_licences()
    }
    for slot in slots.load_slots().slots:
        for example in slot.examples:
            if statuses[example.repo] == "reusable":
                continue
            text = example.description or ""
            for record_id in example.raw_ids:
                for ref in by_id.get(record_id, []):
                    assert ref not in text, (slot.name, example.repo, ref)


def test_every_committed_audit_entry_resolves_to_a_slot_or_a_constant(
    real_root: Path,
) -> None:
    """The whole audit clause: an entry carries one of the two, and names one that exists.

    Both halves are asserted together because the first alone was vacuous: while
    every entry committed with both fields null, a loop over the entries whose
    `slot` was set passed on an audit that set none, and the clause the spec
    states -- that every entry resolves to a slot or a constant -- was never
    exercised. An entry with neither is a reference nobody has decided what to do
    with, and the residue that resolves to neither is not committed at all, so
    every committed entry must carry exactly one, and the one it carries must be a
    declared slot or a constant in the closed set.
    """
    document = yaml.safe_load(
        (paths.CATALOG / normalise.HARDCODED_REFS_FILE).read_bytes().decode("utf-8")
    )
    declared = {slot.name for slot in slots.load_slots().slots}
    assert document["refs"], "the audit names no entries to check"
    for entry in document["refs"]:
        slot = entry.get("slot")
        constant = entry.get("constant")
        where = (entry["entity_ref"], entry["repo"])
        assert (slot is None) != (constant is None), where
        if slot is not None:
            assert slot in declared, where
        if constant is not None:
            assert constant in slots.CONSTANTS, where


# --------------------------------------------------------------------------
# The check, on trees built to violate each clause
# --------------------------------------------------------------------------


def test_a_slot_named_outside_the_vocabulary_is_named(fake_root: Path) -> None:
    """Task 5.3: "every slot name is in the controlled vocabulary"."""
    _seed(fake_root, [_slot(name="banana_group")])
    findings = _findings()
    assert len(findings) == 1, _messages(findings)
    assert "banana_group" in findings[0][1]
    assert "outside the controlled vocabulary" in findings[0][1]


def test_a_slot_whose_domains_disagree_with_its_rule_is_named(fake_root: Path) -> None:
    """The accepting domains are the lexicon's, and a slot that says otherwise
    would bind a device the role never meant."""
    _seed(fake_root, [_slot(accepts_domains=["light", "switch"])])
    findings = _findings()
    assert len(findings) == 1, _messages(findings)
    assert "accepts" in findings[0][1] and "'light'" in findings[0][1]


def test_a_vocabulary_that_declares_no_slots_is_named(fake_root: Path) -> None:
    """An empty vocabulary maps no reference to any slot; the finding names the
    file rather than passing silently over an empty list."""
    _seed(fake_root, [])
    findings = _findings()
    assert len(findings) == 1, _messages(findings)
    assert "declares no slots" in findings[0][1]


def test_a_slot_with_no_example_is_named(fake_root: Path) -> None:
    """Task 5.3: "every slot has ≥1 example"."""
    _seed(fake_root, [_slot(examples=[])])
    findings = _findings()
    assert len(findings) == 1, _messages(findings)
    assert "carries no example" in findings[0][1]


def test_an_example_carrying_a_status_its_repo_does_not_have(fake_root: Path) -> None:
    """Task 5.3: each example names its repo's `reuse_status_code`.

    The status is derived from the record, so a `reusable` claim on a repo that
    grants nothing fails. The example carries a description, so the status claim
    is the only defect the fixture introduces and the finding under assertion is
    the one this case is about.
    """
    _seed(
        fake_root,
        [
            _slot(
                source_repos=["fwartner"],
                examples=[
                    {
                        "repo": "fwartner",
                        "reuse_status_code": "reusable",
                        "raw_ids": [_RECORD],
                        "description": "A lamp group switched as one unit.",
                    }
                ],
            )
        ],
    )
    findings = _findings()
    assert len(findings) == 1, _messages(findings)
    assert "fwartner" in findings[0][1] and "ideas_only" in findings[0][1]


def test_an_example_from_an_unknown_repo_is_named(fake_root: Path) -> None:
    """An example whose repo has no licence record cannot have its status
    resolved, so the finding names the repo and stops rather than reporting the
    missing resolution as a second defect."""
    _seed(
        fake_root,
        [_slot(source_repos=["someoneelse"], examples=[_example(repo="someoneelse")])],
    )
    findings = _findings()
    assert len(findings) == 1, _messages(findings)
    assert "someoneelse" in findings[0][1]
    assert "no licence record" in findings[0][1]


def test_an_uncited_example_with_no_description_is_named(fake_root: Path) -> None:
    """Task 5.3: an uncited example carries a description in our own words."""
    _seed(
        fake_root,
        [
            _slot(
                examples=[
                    {"repo": "ccostan", "reuse_status_code": "reusable", "raw_ids": []}
                ]
            )
        ],
    )
    findings = _findings()
    assert len(findings) == 1, _messages(findings)
    assert "no `raw_ids` and no `description`" in findings[0][1]


def test_an_uncited_example_from_a_non_granting_repo_is_named(fake_root: Path) -> None:
    """Task 5.3: an uncited example comes from a repo that grants reuse.

    A description is present and the example still fails, which is the point:
    with no citation there is nothing for the provenance rule to resolve, so the
    form is refused whatever the prose says.
    """
    _seed(
        fake_root,
        [
            _slot(
                source_repos=["fwartner"],
                examples=[
                    {
                        "repo": "fwartner",
                        "reuse_status_code": "ideas_only",
                        "raw_ids": [],
                        "description": "A lamp group switched as one unit.",
                    }
                ],
            )
        ],
    )
    findings = _findings()
    assert len(findings) == 1, _messages(findings)
    assert "uncited example comes from a repo that grants reuse" in findings[0][1]


def test_a_cited_non_granting_example_with_no_description_is_named(
    fake_root: Path,
) -> None:
    """Task 5.3: a non-granting example is recorded as a description.

    The citation is present, and the example still needs our own description,
    because nothing about the two repos that grant nothing may be transcribed.
    """
    _seed(
        fake_root,
        [
            _slot(
                source_repos=["fwartner"],
                examples=[
                    {
                        "repo": "fwartner",
                        "reuse_status_code": "ideas_only",
                        "raw_ids": ["fwartner_automations_yaml"],
                    }
                ],
            )
        ],
    )
    findings = _findings()
    assert len(findings) == 1, _messages(findings)
    assert "carries no `description`" in findings[0][1]


def test_a_non_granting_example_that_transcribes_its_own_record(
    fake_root: Path,
) -> None:
    """The violating fixture for the provenance clause: the description of a
    non-granting example contains an identifier of one of its own `raw_ids`.

    The finding names the slot and the identifier, which is what a reader needs
    to find the transcription rather than merely know one exists.
    """
    _seed(
        fake_root,
        [
            _slot(
                source_repos=["fwartner"],
                examples=[
                    {
                        "repo": "fwartner",
                        "reuse_status_code": "ideas_only",
                        "raw_ids": ["fwartner_automations_yaml"],
                        "description": "The light.alle_lampen group switched as one.",
                    }
                ],
            )
        ],
        records=[
            {"id": "fwartner_automations_yaml", "entity_refs": ["light.alle_lampen"]}
        ],
    )
    findings = _findings()
    assert len(findings) == 1, _messages(findings)
    assert "light.alle_lampen" in findings[0][1]
    assert "grants no reuse" in findings[0][1]
    assert "light_group" in findings[0][0]


def test_a_multi_source_slot_with_single_source_examples_is_named(
    fake_root: Path,
) -> None:
    """Task 5.3: "every multi-source slot has examples from ≥2 repos"."""
    _seed(
        fake_root,
        [_slot(source_repos=["ccostan", "renemarc"])],
    )
    findings = _findings()
    assert len(findings) == 1, _messages(findings)
    assert "two different repos" in findings[0][1]


def test_an_example_from_a_repo_the_slot_does_not_cite_is_named(
    fake_root: Path,
) -> None:
    """An example's repo is one of the slot's `source_repos`, because the sources
    are the usage the slot claims and an example outside them is untouched by it."""
    _seed(
        fake_root,
        [
            _slot(
                source_repos=["renemarc"],
                examples=[
                    {
                        "repo": "ccostan",
                        "reuse_status_code": "reusable",
                        "raw_ids": [_RECORD],
                    }
                ],
            )
        ],
    )
    findings = _findings()
    assert len(findings) == 1, _messages(findings)
    assert "does not name" in findings[0][1]


def test_an_audit_entry_naming_an_undeclared_slot_is_named(fake_root: Path) -> None:
    """The deferred clause: an entry's `slot` is one the vocabulary declares.

    The finding names the reference and its source repo, which is the pair a
    reader needs to find the entry in the committed audit.
    """
    _seed(
        fake_root,
        [_slot()],
        refs=[
            {
                "entity_ref": "light.kitchen",
                "repo": "ccostan",
                "scope": "room",
                "naming_convention": "opaque_fixture_ids",
                "slot": "kitchen_slot",
                "constant": None,
            }
        ],
    )
    findings = _findings()
    assert len(findings) == 1, _messages(findings)
    where, message = findings[0]
    assert "light.kitchen@ccostan" in where
    assert "kitchen_slot" in message
    assert "does not declare" in message


def test_an_audit_entry_carrying_an_out_of_set_constant_is_named(
    fake_root: Path,
) -> None:
    """The deferred clause: an entry's `constant` is inside the closed set.

    A value outside it justifies nothing, and the finding names the reference and
    its repo for the same reason the slot half does.
    """
    _seed(
        fake_root,
        [_slot()],
        refs=[
            {
                "entity_ref": "light.kitchen",
                "repo": "ccostan",
                "scope": "room",
                "naming_convention": "opaque_fixture_ids",
                "slot": None,
                "constant": "moon_phase",
            }
        ],
    )
    findings = _findings()
    assert len(findings) == 1, _messages(findings)
    where, message = findings[0]
    assert "light.kitchen@ccostan" in where
    assert "moon_phase" in message
    assert "closed set" in message


def test_an_audit_entry_with_neither_a_slot_nor_a_constant_is_named(
    fake_root: Path,
) -> None:
    """Task 5.3: an entry that maps to neither is the clause this check reports.

    The residue that resolves to neither is dropped before the audit is written,
    so no committed entry is both-null; the check still has to report one that is,
    because it is the half that keeps a hand-written entry from recording a
    reference nobody has decided what to do with. The finding names the reference
    and its source repo for the same reason the slot and constant halves do.
    """
    _seed(
        fake_root,
        [_slot()],
        refs=[
            {
                "entity_ref": "light.kitchen",
                "repo": "ccostan",
                "scope": "room",
                "naming_convention": "opaque_fixture_ids",
                "slot": None,
                "constant": None,
            }
        ],
    )
    findings = _findings()
    assert len(findings) == 1, _messages(findings)
    where, message = findings[0]
    assert "light.kitchen@ccostan" in where
    assert "neither a slot nor a constant" in message


def test_the_check_reports_every_defect_rather_than_the_first(fake_root: Path) -> None:
    """The check collects diagnostics rather than returning on the first, so a
    vocabulary with two broken slots is fixed in one pass."""
    _seed(
        fake_root,
        [_slot(name="banana_group"), _slot(examples=[])],
    )
    findings = _findings()
    assert len(findings) == 2, _messages(findings)
    assert {where for where, _ in findings} == {
        "catalog/slots.yaml:banana_group",
        "catalog/slots.yaml:light_group",
    }
