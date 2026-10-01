"""Licence records, the derivation table and the row rule -- section 2.

Three things are tested here and they fail in different ways. The **derivation
table** is tested row by row, because a table with a wrong cell produces a
corpus that is wrong in a direction nobody chose. The **records** are tested
against both trees: the committed one, to assert the four repos are actually
recorded as the spec says, and violating ones, because a check seen only to pass
on the committed tree has never been seen to fail.

The **row rule** is the one that has no data yet. `behaviors.yaml` is empty
until section 4, so the rule is implemented in section 2 and exercised here on
trees built to violate it; on the committed tree it is silent by construction,
which is why none of these tests assert it there.

The prose side is deliberately thin. `docs/reference/*.md` is checked for
presence and for naming the author and the licence path, which is what task 2.3
asks for. The rule that no shipped artifact quotes an `ideas_only` repo's prose
is a Phase 0 requirement with no check in this suite behind it, and the reason
is structural rather than an omission: enforcing it means comparing a shipped
string against the verbatim store in `.local/`, which does not exist in CI. The
deferral is written down in the section of `docs/reference/phase-0-verification.md`
headed "The two checks that read the clones are local-only", and named again in
`catalog/README.md`; nothing is implied by silence here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
import yaml

from tools.catalog import licenses, paths, validate
from tools.catalog.errors import Report

from .conftest import write

if TYPE_CHECKING:
    from pathlib import Path

#: The four repos, with the record the spec assigns each. Kept as literals
#: rather than read out of `catalog/licenses.yaml`: a test that reads its
#: expectation from the file it is checking agrees with any file, including one
#: where a repo was dropped.
EXPECTED_RECORDS: dict[str, tuple[str, str, str | None]] = {
    "ccostan": ("Carlo Costanzo", "mit", "LICENSE"),
    "renemarc": ("René-Marc Simard", "apache_2_0", "LICENSE.txt"),
    "fwartner": ("Florian Wartner", "no_licence", None),
    "johnkoht": ("John Koht", "no_licence", None),
}

DERIVATION_ROWS = [
    ("public_domain", "reusable", ()),
    ("mit", "reusable", ("attribution",)),
    ("apache_2_0", "reusable", ("attribution", "state_changes")),
    ("cc_by_nc_sa", "ideas_only", ("attribution", "share_alike", "non_commercial")),
    ("no_licence", "ideas_only", ()),
]


def _diagnostics() -> list[tuple[str, str]]:
    report = Report()
    licenses.check_licenses(report)
    return [(d.where, d.message) for d in report.diagnostics]


def _only(diagnostics: list[tuple[str, str]], where: str) -> str:
    messages = [message for name, message in diagnostics if name == where]
    assert len(messages) == 1, (
        f"expected exactly one diagnostic for {where}: {diagnostics}"
    )
    return messages[0]


#: A contact block that satisfies the rule, so a test targeting a different
#: failure on an unlicensed repo reports only the failure it is about.
CONTACT: dict[str, object] = {
    "channel": "github-issue",
    "attempted_on": None,
    "outcome": "not_attempted",
    "reason": "not authorised",
}


def _record(repo: str, **overrides: object) -> dict[str, object]:
    """A valid record for a repo, before the caller breaks it."""
    author, licence, file = EXPECTED_RECORDS[repo]
    status, obligations = licenses.derive(licence)
    record: dict[str, object] = {
        "repo": repo,
        "author": author,
        "license_code": licence,
        "license_prose": licence,
        "license_file": file,
        "reuse_status_code": status,
        "reuse_status_prose": status,
        "obligations": list(obligations),
    }
    record.update(overrides)
    return record


def _seed(root: Path, *records: dict[str, object]) -> None:
    write(root, "catalog/licenses.yaml", yaml.safe_dump({"repos": list(records)}))


def _seed_row(root: Path, **row: object) -> None:
    write(root, "catalog/behaviors.yaml", yaml.safe_dump({"behaviors": [row]}))


# --- the derivation table ----------------------------------------------------


@pytest.mark.parametrize(("licence", "status", "obligations"), DERIVATION_ROWS)
def test_the_table_gives_each_licence_its_status_and_obligations(
    licence: str, status: str, obligations: tuple[str, ...]
) -> None:
    """Task 2.2's row-by-row test, against the table the spec publishes."""
    assert licenses.derive(licence) == (status, obligations)


def test_every_value_in_the_published_order_has_a_derivation_row() -> None:
    """A value that can be written into a record must have a status.

    Equality rather than a subset check, in both directions: a table row for a
    value the order does not carry is a row nothing can reach, and a value in
    the order with no row is a licence a record may legally declare and the
    validator then has no status for.
    """
    assert set(licenses.LICENCE_ORDER) == set(licenses.DERIVATION)


def test_an_unknown_licence_value_raises_naming_the_value() -> None:
    with pytest.raises(ValueError, match="not_a_licence"):
        licenses.derive("not_a_licence")


def test_the_most_restrictive_of_a_set_follows_the_published_order() -> None:
    assert licenses.most_restrictive(["mit", "apache_2_0"]) == "apache_2_0"
    assert licenses.most_restrictive(["no_licence", "mit"]) == "no_licence"
    assert licenses.most_restrictive(["public_domain", "mit"]) == "mit"


# --- the records -------------------------------------------------------------


def test_the_committed_tree_records_every_repo_exactly_once(
    real_root: Path,
) -> None:
    """Task 2.1's record set, on the tree that ships."""
    records = licenses.load_licences()
    assert [record.repo for record in records] == list(EXPECTED_RECORDS)


@pytest.mark.parametrize(("repo", "expected"), sorted(EXPECTED_RECORDS.items()))
def test_a_committed_record_states_what_the_spec_assigns(
    real_root: Path, repo: str, expected: tuple[str, str, str | None]
) -> None:
    """Author, code licence and licence file, per repo.

    The file path is asserted directly rather than only through the derived
    status, because the derivation cannot tell `mit` in a `LICENSE` from `mit`
    typed into a record: only the presence of the file makes the second one a
    fact rather than a claim.
    """
    author, licence, licence_file = expected
    record = next(r for r in licenses.load_licences() if r.repo == repo)
    assert record.author == author
    assert record.license_code == licence
    assert record.license_file == licence_file


def test_the_two_unlicensed_repos_grant_neither_code_nor_prose(
    real_root: Path,
) -> None:
    """The split that keeps the corpus honest, asserted on the shipped records."""
    for repo in ("fwartner", "johnkoht"):
        record = next(r for r in licenses.load_licences() if r.repo == repo)
        assert record.reuse_status_code == "ideas_only"
        assert record.reuse_status_prose == "ideas_only"


def test_renemarc_grants_code_and_withholds_prose(real_root: Path) -> None:
    """The one split record, where collapsing either half would be wrong."""
    record = next(r for r in licenses.load_licences() if r.repo == "renemarc")
    assert record.reuse_status_code == "reusable"
    assert record.reuse_status_prose == "ideas_only"
    assert record.obligations_code == ("attribution", "state_changes")


def test_a_hand_written_status_contradicting_the_table_is_reported(
    fake_root: Path,
) -> None:
    """Task 2.2's failing case, naming the record and the licence value."""
    _seed(
        fake_root,
        _record("fwartner", author_contact=dict(CONTACT), reuse_status_code="reusable"),
    )

    message = _only(_diagnostics(), "catalog/licenses.yaml:fwartner")
    assert "reuse_status_code: reusable" in message
    assert "no_licence" in message
    assert "ideas_only" in message


def test_a_hand_written_prose_status_contradicting_the_table_is_reported(
    fake_root: Path,
) -> None:
    """The prose half of the same clause, which is a different field.

    Separate because a record's two statuses come from two licences: `renemarc`
    is `reusable` for code and `ideas_only` for prose, so a check that compared
    only `reuse_status_code` would pass a record whose prose status is invented
    -- and the prose status is the one that decides whether a repo's writing may
    be quoted at all.
    """
    _seed(
        fake_root,
        _record(
            "ccostan",
            license_prose="cc_by_nc_sa",
            reuse_status_prose="reusable",
        ),
    )

    message = _only(_diagnostics(), "catalog/licenses.yaml:ccostan")
    assert "reuse_status_prose: reusable" in message
    assert "ideas_only" in message
    assert "cc_by_nc_sa" in message


def test_a_record_omitting_an_obligation_its_licence_incurs_is_reported(
    fake_root: Path,
) -> None:
    """A dropped obligation is the expensive mistake: it ships unhonoured."""
    _seed(fake_root, _record("renemarc", obligations=["attribution"]))

    message = _only(_diagnostics(), "catalog/licenses.yaml:renemarc")
    assert "omits" in message
    assert "state_changes" in message


def test_a_record_claiming_an_obligation_its_licence_does_not_incurs_is_reported(
    fake_root: Path,
) -> None:
    """The other direction, so the clause is not one that only ever subtracts."""
    _seed(fake_root, _record("ccostan", obligations=["attribution", "share_alike"]))

    message = _only(_diagnostics(), "catalog/licenses.yaml:ccostan")
    assert "adds" in message
    assert "share_alike" in message


def test_an_obligation_outside_the_closed_set_is_reported(fake_root: Path) -> None:
    """A vocabulary is closed or it is not a vocabulary.

    `gratitude` is not a weaker or stronger obligation than the four the spec
    names -- it is not one, and a record asserting it is asserting something
    nothing downstream can honour, because nothing downstream knows it exists.

    The message is pinned, not just the topic, because the value is also
    *in excess* of what `mit` derives: read as a set difference alone this
    reports "adds ['gratitude']", which is true and misleading -- it says the
    licence does not incur this obligation, when the fact is that no licence
    does, and the reader's next move is the closed set rather than the table.
    """
    _seed(fake_root, _record("ccostan", obligations=["attribution", "gratitude"]))

    message = _only(_diagnostics(), "catalog/licenses.yaml:ccostan")
    assert "gratitude" in message
    assert "outside the closed set" in message


def test_a_licences_file_that_will_not_parse_is_a_diagnostic_not_a_traceback(
    fake_root: Path,
) -> None:
    """The ninth check reads a hand-edited file, and a typo in it is not fatal.

    Asserted through `validate_all` rather than by catching the exception
    directly, because the exception type is not the property that matters: what
    matters is that the command which *is* the pre-commit hook returns a report.
    `validate_all` catches `CheckError` and nothing else, and a `YAMLError` is
    not one, so an unguarded read here escapes the check, escapes `validate_all`,
    and reaches the hook as a traceback -- losing every other check's findings,
    its own included. (A count does not belong in this sentence: it had one, and
    the count was wrong by the time the check registry grew.)
    """
    write(fake_root, "catalog/licenses.yaml", "repos: [\n")

    report = validate.validate_all()

    named = [d for d in report.diagnostics if d.check == licenses.LICENCE_CHECK]
    assert len(named) == 1, report.render()
    assert named[0].where == "catalog/licenses.yaml"
    assert "cannot be parsed" in named[0].message


def test_a_licence_value_outside_the_order_is_reported(fake_root: Path) -> None:
    _seed(fake_root, _record("ccostan", license_code="klingon"))

    message = _only(_diagnostics(), "catalog/licenses.yaml:ccostan")
    assert "license_code" in message
    assert "klingon" in message


def test_an_unlicensed_repo_without_a_contact_block_is_reported(
    fake_root: Path,
) -> None:
    """Task 2.4's failing case: the attempt is recorded whether or not made."""
    _seed(fake_root, _record("johnkoht"))

    assert "author_contact" in _only(_diagnostics(), "catalog/licenses.yaml:johnkoht")


def test_a_not_attempted_contact_carrying_a_date_is_reported(
    fake_root: Path,
) -> None:
    """There is no date on which nothing happened."""
    _seed(
        fake_root,
        _record(
            "johnkoht",
            author_contact={
                "channel": "github-issue",
                "attempted_on": "2026-10-01",
                "outcome": "not_attempted",
                "reason": "not authorised",
            },
        ),
    )

    message = _only(_diagnostics(), "catalog/licenses.yaml:johnkoht")
    assert "attempted_on" in message
    assert "not_attempted" in message


def test_a_not_attempted_contact_without_a_reason_is_reported(
    fake_root: Path,
) -> None:
    _seed(
        fake_root,
        _record(
            "johnkoht",
            author_contact={
                "channel": "github-issue",
                "attempted_on": None,
                "outcome": "not_attempted",
            },
        ),
    )

    assert "reason" in _only(_diagnostics(), "catalog/licenses.yaml:johnkoht")


def test_an_unrecognised_contact_outcome_is_reported(fake_root: Path) -> None:
    _seed(
        fake_root,
        _record(
            "johnkoht",
            author_contact={
                "channel": "github-issue",
                "attempted_on": None,
                "outcome": "maybe",
            },
        ),
    )

    message = _only(_diagnostics(), "catalog/licenses.yaml:johnkoht")
    assert "maybe" in message


def test_a_readme_claim_without_a_recorded_discrepancy_is_reported(
    fake_root: Path,
) -> None:
    """Task 2.1's fwartner case: a claim is not a grant.

    The claim is not itself the failure -- recording one is correct and the
    committed tree does it. The failure is a claim with nothing beside it
    saying the file does not support it, which is what a later reader would
    take as settled.
    """
    _seed(
        fake_root,
        _record("fwartner", author_contact=dict(CONTACT), readme_licence_claim="MIT"),
    )

    message = _only(_diagnostics(), "catalog/licenses.yaml:fwartner")
    assert "licence_claim_discrepancy" in message
    assert "MIT" in message


def test_a_claim_with_its_discrepancy_recorded_passes(fake_root: Path) -> None:
    """The other half of the pair, so the check is not merely always-on."""
    _seed(
        fake_root,
        _record(
            "fwartner",
            readme_licence_claim="MIT",
            licence_claim_discrepancy="no licence file ships with the repository",
            author_contact=dict(CONTACT),
        ),
    )

    assert _diagnostics() == []


def test_a_licensed_repo_needs_no_contact_block(fake_root: Path) -> None:
    """The contact rule reaches repos with no licence file and nothing else."""
    _seed(fake_root, _record("ccostan"))

    assert _diagnostics() == []


# --- the per-repo prose records ----------------------------------------------


@pytest.mark.parametrize("repo", sorted(EXPECTED_RECORDS))
def test_each_repo_has_exactly_one_licence_record_document(
    real_root: Path, repo: str
) -> None:
    """Task 2.3: one document per repo, naming its author and licence path."""
    author, _, licence_file = EXPECTED_RECORDS[repo]
    document = paths.ROOT / "docs/reference" / f"{repo}.md"
    assert document.is_file(), f"no licence record document for {repo}"
    text = document.read_text(encoding="utf-8")
    assert author in text, f"{repo}.md does not name its author"
    assert "catalog/licenses.yaml" in text, f"{repo}.md does not cite the record"
    if licence_file is not None:
        assert licence_file in text, f"{repo}.md does not name {licence_file}"


def test_the_contact_document_exists_and_names_both_asks(real_root: Path) -> None:
    """Task 2.4: the exact ask, written down, for each unlicensed repo."""
    document = paths.ROOT / "docs/reference/author-contact.md"
    assert document.is_file()
    text = document.read_text(encoding="utf-8")
    for repo, author in (("fwartner", "Florian Wartner"), ("johnkoht", "John Koht")):
        assert author in text, f"author-contact.md does not name {author}"
        assert repo in text, f"author-contact.md does not name {repo}"


# --- the row rule ------------------------------------------------------------


def test_a_mit_and_apache_merge_takes_the_most_restrictive_licence() -> None:
    """Task 2.6's headline case, stated as the spec states it."""
    derived = licenses.derive_row_license(["mit", "apache_2_0"])

    assert derived["license"] == "apache_2_0"
    assert derived["reuse_status"] == "reusable"
    assert derived["obligations"] == ["attribution", "state_changes"]


def test_a_row_without_sources_is_refused() -> None:
    with pytest.raises(ValueError, match="at least one source"):
        licenses.derive_row_license([])


def test_a_row_omitting_a_sources_obligation_is_reported(fake_root: Path) -> None:
    """Task 2.6's failing case, naming the row.

    The union is over the *sources*, not over the winning licence, so a set that
    happens to match the stricter source's obligations is still wrong when a
    permissive source contributes one it lacks.
    """
    _seed(fake_root, _record("ccostan"), _record("renemarc"))
    _seed_row(
        fake_root,
        id="morning_routine",
        source_repos=["ccostan", "renemarc"],
        license="apache_2_0",
        reuse_status="reusable",
        obligations=["attribution", "state_changes"],
    )

    assert _diagnostics() == []

    _seed_row(
        fake_root,
        id="morning_routine",
        source_repos=["ccostan", "renemarc"],
        license="apache_2_0",
        reuse_status="reusable",
        obligations=["attribution"],
    )

    message = _only(_diagnostics(), "catalog/behaviors.yaml:morning_routine")
    assert "omits" in message
    assert "state_changes" in message


def test_a_row_claiming_an_obligation_its_sources_do_not_incur_is_reported(
    fake_root: Path,
) -> None:
    """The other direction, so the rule is not one that only ever removes."""
    _seed(fake_root, _record("ccostan"))
    _seed_row(
        fake_root,
        id="lights_off",
        source_repos=["ccostan"],
        license="mit",
        reuse_status="reusable",
        obligations=["attribution", "state_changes"],
    )

    message = _only(_diagnostics(), "catalog/behaviors.yaml:lights_off")
    assert "adds" in message
    assert "state_changes" in message


def test_a_row_taking_the_permissive_licence_of_a_stricter_pair_is_reported(
    fake_root: Path,
) -> None:
    _seed(fake_root, _record("ccostan"), _record("renemarc"))
    _seed_row(
        fake_root,
        id="morning_routine",
        source_repos=["ccostan", "renemarc"],
        license="mit",
        reuse_status="reusable",
        obligations=["attribution", "state_changes"],
    )

    message = _only(_diagnostics(), "catalog/behaviors.yaml:morning_routine")
    assert "mit" in message
    assert "apache_2_0" in message


def test_a_row_whose_status_contradicts_its_derived_one_is_reported(
    fake_root: Path,
) -> None:
    """The third derived field on a row, and the one with no test before this.

    `license` and `obligations` each had one; a row could carry the right licence
    and the right obligations with a status invented beside them -- `ideas_only`
    on a `mit` row, which would withhold a behaviour the corpus is free to reuse.
    """
    _seed(fake_root, _record("ccostan"))
    _seed_row(
        fake_root,
        id="lights_off",
        source_repos=["ccostan"],
        license="mit",
        reuse_status="ideas_only",
        obligations=["attribution"],
    )

    message = _only(_diagnostics(), "catalog/behaviors.yaml:lights_off")
    assert "reuse_status: ideas_only" in message
    assert "reusable" in message


def test_a_row_citing_a_repo_whose_licence_is_unknown_is_reported(
    fake_root: Path,
) -> None:
    """A blocked row, reported rather than raised.

    The record's bad `license_code` is already a finding of its own; what this
    covers is the row that depends on it. `derive_row_license` orders its sources
    by the published order and `restrictiveness` raises `ValueError` on a value
    outside it -- and a `ValueError` is not a `CheckError`, so before the guard
    the second finding would have arrived as a traceback from the pre-commit
    hook, taking the first one with it.
    """
    _seed(fake_root, _record("ccostan", license_code="klingon"))
    _seed_row(
        fake_root,
        id="lights_off",
        source_repos=["ccostan"],
        license="mit",
        reuse_status="reusable",
        obligations=["attribution"],
    )

    diagnostics = _diagnostics()
    assert [where for where, _ in diagnostics] == [
        "catalog/licenses.yaml:ccostan",
        "catalog/behaviors.yaml:lights_off",
    ]
    message = _only(diagnostics, "catalog/behaviors.yaml:lights_off")
    assert "ccostan (klingon)" in message


def test_a_row_citing_an_unsettled_repo_is_reported(fake_root: Path) -> None:
    """The spec's "adaptation precedes the record": no record, no row."""
    _seed(fake_root, _record("ccostan"))
    _seed_row(
        fake_root,
        id="front_door",
        source_repos=["ccostan", "somebody_else"],
        license="mit",
        reuse_status="reusable",
        obligations=["attribution"],
    )

    message = _only(_diagnostics(), "catalog/behaviors.yaml:front_door")
    assert "somebody_else" in message
    assert "catalog/licenses.yaml" in message


def test_an_empty_behaviour_file_is_not_a_failure(fake_root: Path) -> None:
    """The committed state until section 4, and it must stay silent."""
    _seed(fake_root, _record("ccostan"))
    write(fake_root, "catalog/behaviors.yaml", "behaviors: []\n")

    assert _diagnostics() == []


def test_a_behaviours_file_that_will_not_load_is_not_a_traceback(
    fake_root: Path,
) -> None:
    """The row rule reads a file it does not own, and must not die reading it.

    Silent rather than reported, because `check_catalog_data_files` already
    reports an unparseable `behaviors.yaml` against that file with its schema;
    a second diagnostic from here would give one bad file two owners. What
    matters is that this returns at all: `yaml.safe_load` raises a `YAMLError`
    that is not a `CheckError`, and the command catches only those, so an
    unguarded read reaches the hook as a traceback.
    """
    _seed(fake_root, _record("ccostan"))
    write(fake_root, "catalog/behaviors.yaml", "behaviors: [\n")

    assert _diagnostics() == []


def test_the_committed_tree_has_no_row_findings(real_root: Path) -> None:
    """The row rule is silent on the shipped tree, which has no rows yet.

    Asserted rather than assumed, because the rule reads a file the corpus will
    fill in later: if section 4 starts writing rows that this rule rejects, the
    failure should surface here rather than at the first commit that adds one.
    """
    assert _diagnostics() == []
