"""The merged behaviour corpus -- tasks 4.3 to 4.8.

`catalog/behaviors.yaml` is the deliverable of stage B, and the rules it has to
satisfy are relations between files rather than a shape one file can carry: a
row's `raw_ids` must resolve into `catalog/raw-behaviors.json`, every raw record
must be claimed once or explained, `expression` follows the sources' licences, a
single-source default needs a written justification, and every merge is recorded
in `catalog/overlap.md` in a ranked order derived rather than asserted.

Each of those is exercised twice. On a tree the test builds, where the violating
fixture is written by hand and the check is seen to fire and to name what it
found; and on the committed tree, where the assertion is that the corpus that
actually ships is green -- because a check proven only against a fixture is a
check that has never been shown to accept anything.

The checks are called directly rather than through `validate.validate_all`,
which is the difference from the other test modules here: `validate_all` only
runs the checks registered in `tools/catalog/validate.py`'s `_CHECKS`, and these
two modules are registered by the coordinator as part of the same change, so a
test that went through the validator would be asserting a registration the test
does not own. The property under test is the check's own findings, and the check
returns them either way.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import yaml

from tools.catalog import behaviors, paths
from tools.catalog.errors import Report

from .conftest import write

if TYPE_CHECKING:
    from pathlib import Path

BEHAVIORS_PATH = "catalog/behaviors.yaml"
RAW_PATH = "catalog/raw-behaviors.json"
OVERLAP_PATH = "catalog/overlap.md"
EXCEPTIONS_PATH = "catalog/overlap_exceptions.yaml"
LICENCES_PATH = "catalog/licenses.yaml"


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------


def _row(**overrides: object) -> dict[str, object]:
    """A minimal row that satisfies every rule, so one override is one defect.

    `source_repos` is two repos and the classification is `module_candidate`:
    both are the choices that need no justification, so a test about some *other*
    rule does not accidentally trip the single-source-generic or the
    overlap-report rule and report a failure it did not mean to.
    """
    row: dict[str, object] = {
        "id": "lighting.example",
        "name": "Example",
        "description": "A description in our own words.",
        "category": "lighting",
        "scope": "room",
        "source_repos": ["ccostan", "renemarc"],
        "required_slots": [],
        "optional_slots": [],
        "concept": "What it means, in our own words.",
        "raw_ids": ["ccostan_a_yaml"],
        "license": "mit",
        "reuse_status": "reusable",
        "obligations": ["attribution"],
        "classification": "module_candidate",
        "retention": None,
        "change_notice": None,
        "expression": None,
    }
    row.update(overrides)
    return row


def _record(
    raw_id: str, unclaimed: object = None, repo: str = "ccostan"
) -> dict[str, object]:
    return {"id": raw_id, "repo": repo, "entity_refs": [], "unclaimed": unclaimed}


def _behaviors(root: Path, rows: list[dict[str, object]]) -> None:
    write(root, BEHAVIORS_PATH, yaml.safe_dump({"behaviors": rows}, sort_keys=False))


def _raw(root: Path, records: list[dict[str, object]]) -> None:
    write(
        root,
        RAW_PATH,
        json.dumps({"records": records}, indent=2, sort_keys=True) + "\n",
    )


def _licences(root: Path, codes: dict[str, str]) -> None:
    write(
        root,
        LICENCES_PATH,
        yaml.safe_dump(
            {
                "repos": [
                    {"repo": repo, "author": repo, "license_code": code}
                    for repo, code in codes.items()
                ]
            },
            sort_keys=False,
        ),
    )


def _exceptions(root: Path, ids: list[str]) -> None:
    write(
        root,
        EXCEPTIONS_PATH,
        yaml.safe_dump(
            {
                "exceptions": [
                    {"id": row_id, "justification": "somebody wrote it down"}
                    for row_id in ids
                ]
            },
            sort_keys=False,
        ),
    )


def _overlap(root: Path, entries: list[tuple[str, list[str]]]) -> None:
    lines = ["# Cross-repo overlap", ""]
    for row_id, repos in entries:
        lines.append(f"## {row_id}")
        lines.append("")
        lines.append(f"- sources: {', '.join(repos)}")
        lines.append("")
    write(root, OVERLAP_PATH, "\n".join(lines) + "\n")


def _messages(check: str) -> str:
    """Run the corpus checks and return the named check's findings as text.

    Both checks always run, because the overlap rules and the corpus rules read
    the same two files and a test that ran only one would leave the other's
    disagreement with the fixture unobserved.
    """
    report = Report()
    behaviors.check_behaviors(report)
    behaviors.check_overlap(report)
    return "\n".join(
        f"{d.where}: {d.message}" for d in report.diagnostics if d.check == check
    )


def _behaviors_messages() -> str:
    return _messages(behaviors.BEHAVIORS_CHECK)


def _overlap_messages() -> str:
    return _messages(behaviors.OVERLAP_CHECK)


# --------------------------------------------------------------------------
# Coverage: every raw record is claimed once, or explained
# --------------------------------------------------------------------------


def test_a_raw_record_no_row_claims_and_no_reason_covers_is_named(
    fake_root: Path,
) -> None:
    """The register's whole purpose: silence about a record is the failure."""
    _raw(fake_root, [_record("ccostan_a_yaml"), _record("ccostan_b_yaml")])
    _behaviors(fake_root, [_row(raw_ids=["ccostan_a_yaml"])])

    messages = _behaviors_messages()
    assert "ccostan_b_yaml" in messages
    assert "no `unclaimed` reason" in messages


def test_a_raw_record_with_a_reason_from_the_closed_set_passes(
    fake_root: Path,
) -> None:
    _raw(
        fake_root,
        [
            _record("ccostan_a_yaml"),
            _record("ccostan_b_yaml", unclaimed="non_behavior_file"),
        ],
    )
    _behaviors(fake_root, [_row(raw_ids=["ccostan_a_yaml"])])
    assert _behaviors_messages() == ""


def test_a_reason_outside_the_closed_set_is_named(fake_root: Path) -> None:
    _raw(
        fake_root,
        [
            _record("ccostan_a_yaml"),
            _record("ccostan_b_yaml", unclaimed="because"),
        ],
    )
    _behaviors(fake_root, [_row(raw_ids=["ccostan_a_yaml"])])
    messages = _behaviors_messages()
    assert "ccostan_b_yaml" in messages
    assert "because" in messages


def test_a_raw_record_claimed_by_two_rows_is_named(fake_root: Path) -> None:
    _raw(fake_root, [_record("ccostan_a_yaml")])
    _behaviors(
        fake_root,
        [
            _row(id="lighting.first", raw_ids=["ccostan_a_yaml"]),
            _row(id="lighting.second", raw_ids=["ccostan_a_yaml"]),
        ],
    )
    messages = _behaviors_messages()
    assert "ccostan_a_yaml" in messages
    assert "lighting.first" in messages
    assert "lighting.second" in messages


def test_a_citation_to_a_record_that_does_not_exist_is_named(
    fake_root: Path,
) -> None:
    _raw(fake_root, [_record("ccostan_a_yaml")])
    _behaviors(fake_root, [_row(raw_ids=["ccostan_a_yaml", "ccostan_ghost_yaml"])])
    messages = _behaviors_messages()
    assert "ccostan_ghost_yaml" in messages
    assert "not a record" in messages


def test_a_claimed_record_carrying_an_unclaimed_reason_is_named(
    fake_root: Path,
) -> None:
    """The two states cannot both hold; the marker is null on a claimed record."""
    _raw(fake_root, [_record("ccostan_a_yaml", unclaimed="duplicate_of")])
    _behaviors(fake_root, [_row(raw_ids=["ccostan_a_yaml"])])
    messages = _behaviors_messages()
    assert "ccostan_a_yaml" in messages
    assert "duplicate_of" in messages


def test_the_unclaimed_count_is_the_number_of_explained_records(
    fake_root: Path,
) -> None:
    """The count is a first-class output, not a by-product of a failure."""
    _raw(
        fake_root,
        [
            _record("ccostan_a_yaml"),
            _record("ccostan_b_yaml", unclaimed="vendor_config"),
            _record("ccostan_c_yaml", unclaimed="malformed"),
        ],
    )
    _behaviors(fake_root, [_row(raw_ids=["ccostan_a_yaml"])])
    assert behaviors.unclaimed_count() == 2


def test_a_duplicate_row_id_is_named_with_the_first_use(fake_root: Path) -> None:
    _raw(fake_root, [_record("ccostan_a_yaml")])
    _behaviors(
        fake_root,
        [
            _row(id="lighting.same", raw_ids=["ccostan_a_yaml"]),
            _row(id="lighting.same", raw_ids=[]),
        ],
    )
    messages = _behaviors_messages()
    assert "duplicate id" in messages
    assert "lighting.same" in messages


# --------------------------------------------------------------------------
# Expression follows the sources' licences
# --------------------------------------------------------------------------


def test_a_row_whose_source_withholds_reuse_may_not_carry_expression(
    fake_root: Path,
) -> None:
    _licences(fake_root, {"ccostan": "mit", "johnkoht": "no_licence"})
    _raw(fake_root, [_record("johnkoht_a_yaml", repo="johnkoht")])
    _behaviors(
        fake_root,
        [
            _row(
                id="lighting.borrowed",
                source_repos=["johnkoht"],
                raw_ids=["johnkoht_a_yaml"],
                license="no_licence",
                reuse_status="ideas_only",
                obligations=[],
                expression={"action": ["light.turn_on"]},
            )
        ],
    )
    messages = _behaviors_messages()
    assert "lighting.borrowed" in messages
    assert "expression" in messages


def test_a_row_whose_sources_all_grant_reuse_may_carry_expression(
    fake_root: Path,
) -> None:
    _licences(fake_root, {"ccostan": "mit", "renemarc": "apache_2_0"})
    _raw(fake_root, [_record("ccostan_a_yaml")])
    _behaviors(
        fake_root,
        [
            _row(
                source_repos=["ccostan", "renemarc"],
                expression={"action": ["light.turn_on"]},
            )
        ],
    )
    assert _behaviors_messages() == ""


def test_a_null_expression_on_a_withholding_row_is_accepted(fake_root: Path) -> None:
    """The field's presence with a null value is the record the question was asked."""
    _licences(fake_root, {"johnkoht": "no_licence"})
    _raw(fake_root, [_record("johnkoht_a_yaml", repo="johnkoht")])
    _behaviors(
        fake_root,
        [
            _row(
                source_repos=["johnkoht"],
                raw_ids=["johnkoht_a_yaml"],
                license="no_licence",
                reuse_status="ideas_only",
                obligations=[],
                expression=None,
            )
        ],
    )
    assert _behaviors_messages() == ""


# --------------------------------------------------------------------------
# Retention, classification and the closed category set
# --------------------------------------------------------------------------


def test_a_discard_row_is_retained_for_audit(fake_root: Path) -> None:
    _raw(fake_root, [_record("ccostan_a_yaml")])
    _behaviors(
        fake_root,
        [_row(id="modes.personal", classification="discard", retention="audit")],
    )
    assert _behaviors_messages() == ""


def test_a_discard_row_without_audit_retention_is_named(fake_root: Path) -> None:
    _raw(fake_root, [_record("ccostan_a_yaml")])
    _behaviors(fake_root, [_row(id="modes.personal", classification="discard")])
    messages = _behaviors_messages()
    assert "modes.personal" in messages
    assert "audit" in messages


def test_a_non_discard_row_carrying_retention_is_named(fake_root: Path) -> None:
    _raw(fake_root, [_record("ccostan_a_yaml")])
    _behaviors(
        fake_root,
        [
            _row(
                id="lighting.kept", classification="module_candidate", retention="audit"
            )
        ],
    )
    messages = _behaviors_messages()
    assert "lighting.kept" in messages
    assert "audit" in messages


def test_a_single_source_generic_row_without_an_exception_is_named(
    fake_root: Path,
) -> None:
    _raw(fake_root, [_record("ccostan_a_yaml")])
    _behaviors(
        fake_root,
        [_row(id="lighting.solo", source_repos=["ccostan"], classification="generic")],
    )
    messages = _behaviors_messages()
    assert "lighting.solo" in messages
    assert "overlap_exceptions" in messages


def test_a_single_source_generic_row_listed_as_an_exception_passes(
    fake_root: Path,
) -> None:
    _raw(fake_root, [_record("ccostan_a_yaml")])
    _behaviors(
        fake_root,
        [_row(id="lighting.solo", source_repos=["ccostan"], classification="generic")],
    )
    _exceptions(fake_root, ["lighting.solo"])
    assert _behaviors_messages() == ""


def test_a_multi_source_generic_row_needs_no_exception(fake_root: Path) -> None:
    _raw(fake_root, [_record("ccostan_a_yaml")])
    _behaviors(fake_root, [_row(id="lighting.agreed", classification="generic")])
    assert _behaviors_messages() == ""


def test_a_category_outside_the_closed_set_is_named(fake_root: Path) -> None:
    _raw(fake_root, [_record("ccostan_a_yaml")])
    _behaviors(fake_root, [_row(id="lighting.odd", category="whimsy")])
    messages = _behaviors_messages()
    assert "lighting.odd" in messages
    assert "whimsy" in messages


def test_the_categories_are_the_ten_the_brief_names() -> None:
    assert set(behaviors.CATEGORIES) == {
        "cleaning",
        "climate",
        "laundry",
        "lighting",
        "media",
        "modes",
        "notifications",
        "presence",
        "security_safety",
        "system",
    }


# --------------------------------------------------------------------------
# Overlap: every merge recorded, in the ranked order
# --------------------------------------------------------------------------


def _two_merges(root: Path) -> None:
    _raw(
        root,
        [
            _record("ccostan_a_yaml"),
            _record("renemarc_a_yaml", repo="renemarc"),
        ],
    )
    _behaviors(
        root,
        [
            _row(
                id="lighting.permissive",
                source_repos=["ccostan", "renemarc"],
                raw_ids=["ccostan_a_yaml", "renemarc_a_yaml"],
                license="mit",
            ),
            _row(
                id="lighting.withheld",
                source_repos=["ccostan", "renemarc"],
                raw_ids=[],
                license="no_licence",
            ),
        ],
    )


def test_every_multi_source_row_must_appear_in_the_overlap_report(
    fake_root: Path,
) -> None:
    _two_merges(fake_root)
    _overlap(fake_root, [("lighting.permissive", ["ccostan", "renemarc"])])
    messages = _overlap_messages()
    assert "lighting.withheld" in messages
    assert "does not appear" in messages


def test_an_overlap_entry_naming_one_repo_is_named(fake_root: Path) -> None:
    _two_merges(fake_root)
    _overlap(
        fake_root,
        [
            ("lighting.permissive", ["ccostan"]),
            ("lighting.withheld", ["ccostan", "renemarc"]),
        ],
    )
    messages = _overlap_messages()
    assert "lighting.permissive" in messages
    assert "at least two" in messages


def test_an_overlap_entry_naming_the_wrong_repos_is_named(fake_root: Path) -> None:
    _two_merges(fake_root)
    _overlap(
        fake_root,
        [
            ("lighting.permissive", ["ccostan", "johnkoht"]),
            ("lighting.withheld", ["ccostan", "renemarc"]),
        ],
    )
    messages = _overlap_messages()
    assert "lighting.permissive" in messages
    assert "cites" in messages


def test_overlap_entries_in_the_ranked_order_pass(fake_root: Path) -> None:
    _two_merges(fake_root)
    _overlap(
        fake_root,
        [
            ("lighting.permissive", ["ccostan", "renemarc"]),
            ("lighting.withheld", ["ccostan", "renemarc"]),
        ],
    )
    assert _overlap_messages() == ""


def test_overlap_entries_out_of_the_ranked_order_are_named(fake_root: Path) -> None:
    """The order is the report's reason to exist; a shuffled one is not a report."""
    _two_merges(fake_root)
    _overlap(
        fake_root,
        [
            ("lighting.withheld", ["ccostan", "renemarc"]),
            ("lighting.permissive", ["ccostan", "renemarc"]),
        ],
    )
    messages = _overlap_messages()
    assert "out of order" in messages
    assert "lighting.permissive" in messages


def test_a_stale_overlap_entry_for_a_row_that_is_not_merged_is_named(
    fake_root: Path,
) -> None:
    _raw(fake_root, [_record("ccostan_a_yaml")])
    _behaviors(
        fake_root,
        [
            _row(
                id="lighting.solo", source_repos=["ccostan"], raw_ids=["ccostan_a_yaml"]
            )
        ],
    )
    _overlap(fake_root, [("lighting.solo", ["ccostan", "renemarc"])])
    messages = _overlap_messages()
    assert "lighting.solo" in messages
    assert "not a multi-source row" in messages


# --------------------------------------------------------------------------
# The committed corpus
# --------------------------------------------------------------------------


def test_the_committed_corpus_satisfies_every_rule(real_root: Path) -> None:
    report = Report()
    behaviors.check_behaviors(report)
    behaviors.check_overlap(report)
    assert report.ok, report.render()


def test_the_committed_corpus_claims_every_raw_record_exactly_once(
    real_root: Path,
) -> None:
    rows = behaviors.load_behaviors()
    records = behaviors.load_raw_records()
    claimed = behaviors.claimed_ids(rows)

    known = {str(record["id"]) for record in records}
    assert set(claimed) <= known
    assert len(claimed) == len(
        [raw_id for row in rows for raw_id in row["raw_ids"]]  # type: ignore[union-attr]
    )

    unexplained = known - set(claimed)
    reasons = {
        str(record.get("unclaimed"))
        for record in records
        if record["id"] in unexplained
    }
    assert reasons and reasons <= set(behaviors.UNCLAIMED_REASONS)
    assert behaviors.unclaimed_count() == len(unexplained)


def test_the_committed_corpus_is_not_vacuous(real_root: Path) -> None:
    """A corpus with no rows would satisfy every rule above by saying nothing."""
    rows = behaviors.load_behaviors()
    assert len(rows) >= 40
    assert any(row["classification"] == "discard" for row in rows)
    assert sum(1 for row in rows if len(row["source_repos"]) >= 2) >= 10  # type: ignore[arg-type]


def test_the_shipped_default_set_excludes_discard_rows(real_root: Path) -> None:
    rows = behaviors.load_behaviors()
    discarded = {row["id"] for row in rows if row["classification"] == "discard"}
    assert discarded, "the corpus retains at least one discipline case"

    shipped = behaviors.shipped_rows(rows)
    assert all(row["classification"] != "discard" for row in shipped)
    assert {row["id"] for row in shipped} == {row["id"] for row in rows} - discarded


def test_every_committed_row_carries_a_valid_scope_and_classification(
    real_root: Path,
) -> None:
    for row in behaviors.load_behaviors():
        assert row["scope"] in {"room", "house"}, row["id"]
        assert row["classification"] in behaviors.CLASSIFICATIONS, row["id"]
        assert row["category"] in behaviors.CATEGORIES, row["id"]
        assert row["description"], row["id"]


def test_every_committed_single_source_generic_row_is_on_the_exception_list(
    real_root: Path,
) -> None:
    justified = set(behaviors.load_exceptions())
    for row in behaviors.load_behaviors():
        if row["classification"] == "generic" and len(row["source_repos"]) == 1:  # type: ignore[arg-type]
            assert row["id"] in justified, row["id"]


def test_the_committed_overlap_report_accounts_for_every_merge(
    real_root: Path,
) -> None:
    rows = behaviors.load_behaviors()
    merged = {row["id"] for row in rows if len(row["source_repos"]) >= 2}  # type: ignore[arg-type]
    assert merged

    text = (paths.CATALOG / "overlap.md").read_text(encoding="utf-8")
    entries = behaviors.parse_overlap(text)
    assert {entry_id for entry_id, _ in entries} == merged
    assert all(len(repos) >= 2 for _, repos in entries)
