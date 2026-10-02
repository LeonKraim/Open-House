"""The generated notice, and the drift check that keeps it true -- section 6.4.

Two claims are tested and they fail in different directions. The **generator**
must produce a notice that names every repo the corpus draws from with its
author, licence and obligations, and must produce the *same* bytes every time --
a generator whose output depends on dict order, locale or the clock cannot be
drifted from, because there is nothing stable to compare against. The **drift
check** must fail, and name the repo, when the committed file differs from a
fresh regeneration, and must pass on the file the generator actually writes.

That second pair is the whole point of the task: a hand-maintained notice drifts
from its records and nothing notices, so the check has to be exercised on trees
built to diverge -- the committed tree is by construction the one that agrees.
The check is also asserted to be silent when `catalog/licenses.yaml` will not
parse, because a check that raised there would escape `validate_all` as a
traceback and take every other check's findings with it; that failure belongs to
the licences check, and reporting it twice would give one defect two owners.

Everything here runs with no clones present. The notice is a function of
`catalog/licenses.yaml` alone, and the `fake_root` tests build a tree with
neither `ressources/` nor `.local/`, which is the shape of the CI checkout the
check has to pass in.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
import yaml

from tools.catalog import attribution, licenses
from tools.catalog.errors import Report

from .conftest import write

if TYPE_CHECKING:
    from pathlib import Path

#: repo -> (author, code licence, licence file, code obligations). Pinned as
#: literals rather than read out of `catalog/licenses.yaml`: a test that reads
#: its expectation from the file it is checking agrees with any file, including
#: one where a repo was dropped and its attribution with it.
TERMS: dict[str, tuple[str, str, str | None, tuple[str, ...]]] = {
    "ccostan": ("Carlo Costanzo", "mit", "LICENSE", ("attribution",)),
    "renemarc": (
        "René-Marc Simard",
        "apache_2_0",
        "LICENSE.txt",
        ("attribution", "state_changes"),
    ),
    "fwartner": ("Florian Wartner", "public_domain", None, ()),
    "johnkoht": ("John Koht", "public_domain", None, ()),
}

#: The fields the committed-notice test asserts, as parametrisation rows.
_APPEARANCE = [(repo, *term) for repo, term in TERMS.items()]

#: A contact block that satisfies the licences check, so a fixture targeting the
#: drift check does not also carry an unrelated licence failure.
CONTACT: dict[str, object] = {
    "channel": "github-issue",
    "attempted_on": None,
    "outcome": "not_attempted",
    "reason": "not authorised",
}


def _record(repo: str, **overrides: object) -> dict[str, object]:
    """A valid record for a repo, before the caller breaks it."""
    author, licence, licence_file, obligations = TERMS[repo]
    record: dict[str, object] = {
        "repo": repo,
        "author": author,
        "license_code": licence,
        "license_prose": licence,
        "license_file": licence_file,
        "reuse_status_code": licenses.derive(licence)[0],
        "reuse_status_prose": licenses.derive(licence)[0],
        "obligations": list(obligations),
    }
    if licence_file is None:
        record["author_contact"] = dict(CONTACT)
    record.update(overrides)
    return record


def _seed(root: Path, *records: dict[str, object]) -> None:
    write(root, "catalog/licenses.yaml", yaml.safe_dump({"repos": list(records)}))


def _regenerate() -> str:
    """Write the notice from the seeded records and return it as text."""
    return attribution.write_attribution().read_text(encoding="utf-8")


def _diagnostics() -> list[tuple[str, str]]:
    report = Report()
    attribution.check_attribution(report)
    return [(d.where, d.message) for d in report.diagnostics]


def _section_of(text: str, repo: str) -> str:
    """The committed block that opens with `repo`'s heading, up to the next one."""
    start = text.index(f"## {repo}\n")
    tail = text[start:]
    end = tail.find("\n## ", 1)
    return tail if end == -1 else tail[:end]


# --- the committed notice ----------------------------------------------------


def test_the_committed_notice_equals_a_fresh_regeneration(real_root: Path) -> None:
    """Task 6.4's byte-identity, on the tree that ships.

    Read as bytes and decoded, not through a translating text read: the file is
    written with LF and stored with LF under `eol=lf`, and a read that translated
    line endings would hide the difference between a file that changed and one
    that was merely checked out -- the exact failure the drift check exists to
    catch.
    """
    committed = attribution.attribution_path().read_bytes().decode("utf-8")
    assert committed == attribution.render(licenses.load_licences())


def test_the_committed_tree_passes_the_drift_check(real_root: Path) -> None:
    assert _diagnostics() == []


@pytest.mark.parametrize(
    ("repo", "author", "licence", "_file", "obligations"), _APPEARANCE
)
def test_every_incorporated_repo_appears_with_its_terms(
    real_root: Path,
    repo: str,
    author: str,
    licence: str,
    _file: str | None,
    obligations: tuple[str, ...],
) -> None:
    """The spec's "every incorporated repo appears with author, licence, obligations".

    Scoped to the repo's own block rather than the whole file, because a repo
    whose heading was dropped but whose name still appears in another repo's text
    is exactly the drift this is meant to catch, and a whole-file substring check
    would pass it.
    """
    text = attribution.attribution_path().read_text(encoding="utf-8")
    section = _section_of(text, repo)
    assert author in section, f"the {repo} entry does not name its author"
    assert f"`{licence}`" in section, f"the {repo} entry does not name its licence"
    if obligations:
        for obligation in obligations:
            assert f"`{obligation}`" in section, (
                f"the {repo} entry omits the {obligation} obligation"
            )
    else:
        assert "| Obligations | none |" in section


# --- the generator -----------------------------------------------------------


def test_render_is_deterministic(real_root: Path) -> None:
    """Drift is only meaningful against output that is stable in the first place."""
    records = licenses.load_licences()
    assert attribution.render(records) == attribution.render(records)


def test_writing_twice_is_byte_identical(fake_root: Path) -> None:
    """A regeneration that differed from itself could never settle a drift."""
    _seed(fake_root, *[_record(repo) for repo in TERMS])
    first = attribution.write_attribution().read_bytes()
    second = attribution.write_attribution().read_bytes()
    assert first == second


def test_a_reusable_repo_states_the_grant_it_reuses_under(real_root: Path) -> None:
    record = next(r for r in licenses.load_licences() if r.repo == "ccostan")
    section = attribution.render_section(record)
    assert "`mit`" in section
    assert "`attribution`" in section
    assert "Reused and adapted" in section


def test_a_state_changes_repo_states_the_change_notice(real_root: Path) -> None:
    """The one obligation whose absence is only visible as a missing field."""
    record = next(r for r in licenses.load_licences() if r.repo == "renemarc")
    section = attribution.render_section(record)
    assert "state_changes" in section
    assert "change notice" in section


def test_a_non_granting_repo_is_stated_as_facts_and_concepts_only(
    fake_root: Path,
) -> None:
    """A record that grants nothing says so, rather than reading as licensed.

    Built on a fixture because the two repos that withheld -- `fwartner` and
    `johnkoht` -- granted unrestricted reuse on 2026-10-02, so no shipped record
    carries `no_licence` any more and the rule would otherwise go untested.
    """
    _seed(
        fake_root,
        _record("fwartner", license_code="no_licence", license_prose="no_licence"),
    )
    record = next(r for r in licenses.load_licences() if r.repo == "fwartner")
    section = attribution.render_section(record)
    assert "`ideas_only`" in section
    assert "Not reused." in section
    assert "Not quoted." in section


# --- the drift check ---------------------------------------------------------


def test_a_notice_built_from_the_records_passes(fake_root: Path) -> None:
    """The no-clone tree the check has to pass in -- neither `ressources/` nor
    `.local/` exists under the fixture, and the check must not want them."""
    _seed(fake_root, *[_record(repo) for repo in TERMS])
    _regenerate()
    assert _diagnostics() == []


def test_a_divergent_repo_entry_is_named(fake_root: Path) -> None:
    """The spec's failing case: name the divergent repo, not merely the file."""
    _seed(fake_root, *[_record(repo) for repo in TERMS])
    text = _regenerate().replace("Carlo Costanzo", "Someone Else")
    write(fake_root, "docs/attribution.md", text)

    assert [where for where, _ in _diagnostics()] == ["docs/attribution.md:ccostan"]


def test_a_repo_block_the_notice_dropped_is_named(fake_root: Path) -> None:
    """A deleted entry is drift even though every remaining block still matches."""
    _seed(fake_root, *[_record(repo) for repo in TERMS])
    text = _regenerate()
    record = next(r for r in licenses.load_licences() if r.repo == "renemarc")
    write(
        fake_root,
        "docs/attribution.md",
        text.replace(attribution.render_section(record), ""),
    )

    assert [where for where, _ in _diagnostics()] == ["docs/attribution.md:renemarc"]


def test_a_repo_the_records_dropped_is_named(fake_root: Path) -> None:
    """A stale heading is the other direction: the file names a repo that is gone."""
    _seed(fake_root, *[_record(repo) for repo in TERMS])
    text = _regenerate() + "\n\n## ghost\n\n| | |\n| --- | --- |\n"
    write(fake_root, "docs/attribution.md", text)

    assert [where for where, _ in _diagnostics()] == ["docs/attribution.md:ghost"]


def test_a_divergence_outside_every_entry_is_named_against_the_file(
    fake_root: Path,
) -> None:
    """The preamble belongs to no repo, so there is no repo to name.

    Asserted so the fallback is a decision rather than a hole: a check that only
    compared repo blocks would pass a file whose every block matched and whose
    header had been rewritten, which is still a file that is not what the
    generator would write.
    """
    _seed(fake_root, *[_record(repo) for repo in TERMS])
    write(fake_root, "docs/attribution.md", "stray line\n" + _regenerate())

    assert [where for where, _ in _diagnostics()] == ["docs/attribution.md"]


def test_a_missing_notice_is_named(fake_root: Path) -> None:
    """The file has to be committed, so its absence is a failure, not a pass."""
    _seed(fake_root, *[_record(repo) for repo in TERMS])
    assert [where for where, _ in _diagnostics()] == ["docs/attribution.md"]


def test_a_record_file_that_will_not_parse_is_left_to_the_licences_check(
    fake_root: Path,
) -> None:
    """One defect, one owner, and no traceback.

    `licenses.check_licenses` reports an unreadable `catalog/licenses.yaml`
    against that file. Re-raising here would put the same defect in two checks'
    reports; raising something `validate_all` does not catch would reach the
    pre-commit hook as a traceback and lose every other check's findings. The
    notice cannot be regenerated from a file that will not parse either way, so
    this check stays silent and says nothing it cannot support.
    """
    write(fake_root, "catalog/licenses.yaml", "repos: [\n")

    report = Report()
    attribution.check_attribution(report)

    assert report.diagnostics == []
