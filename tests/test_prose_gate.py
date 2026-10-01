"""The prose gate -- section 6, task 6.6.

The requirement is one sentence: no shipped artifact, `docs/` included, may
contain prose quoted from a repo whose prose terms are `ideas_only`. What makes
it a task rather than a rule is the word *quoted*, and the two tests that decide
the outcome are the ones the spec names -- a passage reproduced verbatim fails
naming the artifact and the repo, and the same idea in our own words passes. The
rest of this file pins the decisions that sit between those two: the threshold
that separates a quote from an ordinary-English coincidence, and the token
boundary that stops a shared path or URL from reading as a quote at all.

Every fixture here is a tree we build, for the reason `tests/conftest.py` gives:
the check's behaviour on a violating tree cannot be exercised on the committed
one, because the committed one is the tree that passes. The one exception is at
the foot of the file, and it is the half that keeps the gate honest against the
real repository.

The check is deliberately **not** in `validate.py`'s registry and never should
be: it reads the gitignored `.local/raw-verbatim.json`, which CI does not have,
so it runs locally beside `repos.check_ha_versions`. Its own tests call it
directly, which is the same reach the coordinator wires on approval -- reported
rather than made, since `tools/catalog/validate.py` and
`tests/test_check_registry.py` are owned elsewhere.

The passages below are invented. Committing a real passage from a non-permissive
repo -- even one the gate is supposed to reject -- would be the very thing the
gate exists to prevent, so the fixtures quote text no source author wrote and
label it with a repo handle the licence record marks `ideas_only`.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
import yaml

from tools.catalog import normalise, paths, prose_gate
from tools.catalog.errors import CheckError, Report

from .conftest import write

if TYPE_CHECKING:
    from pathlib import Path

CHECK = prose_gate.PROSE_GATE_CHECK

#: A non-permissive passage long enough to be quoted and caught. Invented.
SEED = (
    "Only the porch lamp follows the sun as it sinks, and it dims itself gently "
    "over several minutes once the household has gone quiet for the night."
)

#: The same idea, restated. Shares no run of `MIN_QUOTE_TOKENS` with `SEED`.
OWN_WORDS = (
    "As dusk arrives, the entrance light tracks the fading daylight and fades "
    "out slowly a few minutes after everyone has settled down indoors."
)

#: A passage from the one repo whose prose is reusable, so quoting it is allowed.
PERMISSIVE = (
    "A fragment from a repository whose code and prose are both licensed "
    "permissively, so reproducing it verbatim is permitted anywhere."
)

#: Words whose one distinguishing property is that no source wrote them in this
#: order, for the threshold test. They are not prose and are never mistaken for it.
THRESHOLD_WORDS = [
    "alpha",
    "bravo",
    "charlie",
    "delta",
    "echo",
    "foxtrot",
    "golf",
    "hotel",
    "india",
    "juliet",
]

_COMMITTED = paths.CATALOG / "raw-behaviors.json"
_STORE = paths.LOCAL / prose_gate.VERBATIM_FILE

#: Two records: the non-permissive source the gate is about, and a permissive
#: one the gate must leave alone.
RENEMARC = {
    "repo": "renemarc",
    "author": "René-Marc Simard",
    "license_code": "apache_2_0",
    "license_prose": "cc_by_nc_sa",
    "license_file": "LICENSE.txt",
}
CCOSTAN = {
    "repo": "ccostan",
    "author": "Carlo Costanzo",
    "license_code": "mit",
    "license_prose": "mit",
    "license_file": "LICENSE",
}


def _record(record_id: str, **fields: object) -> dict[str, object]:
    """One verbatim-store record, complete enough to carry a passage."""
    row: dict[str, object] = {
        "id": record_id,
        "path": "packages/lighting.yaml",
        "class": "package",
        "aliases": [],
        "names": [],
        "comments": [],
    }
    row.update(fields)
    return row


def _seed(root: Path, records: list[dict[str, object]] | None = None) -> None:
    """Give a fixture tree the licence records and the store the gate reads."""
    write(root, ".gitignore", ".local/\nressources/\n")
    write(
        root,
        "catalog/licenses.yaml",
        yaml.safe_dump({"repos": [RENEMARC, CCOSTAN]}, sort_keys=False),
    )
    if records is None:
        records = [
            _record("renemarc_packages_lighting_yaml", comments=[SEED]),
            _record("ccostan_config_automation_yaml", comments=[PERMISSIVE]),
        ]
    write(
        root,
        ".local/raw-verbatim.json",
        json.dumps({"change_notice": None, "records": records}, indent=2) + "\n",
    )


def _run() -> Report:
    report = Report()
    prose_gate.check_prose_gate(report)
    return report


# --------------------------------------------------------------------------
# The two scenarios the spec names
# --------------------------------------------------------------------------


def test_a_quoted_passage_fails_naming_the_artifact_and_the_repo(
    fake_root: Path,
) -> None:
    """The spec's failure case: a verbatim passage, named by artifact and repo."""
    _seed(fake_root)
    write(fake_root, "docs/quote.md", f"Notes on arrival lighting:\n\n{SEED}\n")

    report = _run()

    assert not report.ok
    assert len(report.diagnostics) == 1
    finding = report.diagnostics[0]
    assert finding.check == CHECK
    assert finding.where == "docs/quote.md"
    assert "renemarc" in finding.message


def test_the_idea_in_our_own_words_passes(fake_root: Path) -> None:
    """The spec's passing case: the same idea, no verbatim passage reproduced."""
    _seed(fake_root)
    write(
        fake_root,
        "docs/restated.md",
        f"Arrival lighting, in our words:\n\n{OWN_WORDS}\n",
    )

    assert _run().ok


def test_a_quoted_passage_from_a_permissive_repo_passes(fake_root: Path) -> None:
    """The rule reaches only non-permissive prose, so a reusable quote is silent."""
    _seed(fake_root)
    write(fake_root, "docs/allowed.md", f"Quoted with permission:\n\n{PERMISSIVE}\n")

    assert _run().ok


# --------------------------------------------------------------------------
# The decisions between those two scenarios
# --------------------------------------------------------------------------


def test_the_withheld_store_is_not_scanned(fake_root: Path) -> None:
    """The gate compares against the store, so the store must not be an artifact.

    The store holds the passage verbatim; if it were enumerated as a shipped file
    every run would report it against itself, and the pass would depend on an
    ignore rule. This pins that the ignore rule is what keeps it out.
    """
    _seed(fake_root)
    write(fake_root, "docs/quote.md", SEED)

    names = {finding.where for finding in _run().diagnostics}

    assert names == {"docs/quote.md"}


def test_a_shared_path_is_not_a_quote(fake_root: Path) -> None:
    """A structured string shared whole is not quoted prose.

    A path, a URL or a dotted identifier is a recorded fact, not expression, and
    the tokenizer keeps one as a single token so a shared run cannot form out of
    it. Split on every separator instead and this fixture fails -- which is what
    the first implementation did.
    """
    path = "/config/dashboards/templates/includes/card_mod/base/kohbo_horizontal_stack_buttons_bg.yaml"
    _seed(
        fake_root,
        [
            _record(
                "renemarc_packages_lighting_yaml",
                comments=[f"See the include at {path} for the base card."],
            ),
        ],
    )
    write(
        fake_root,
        "catalog/inventory.json",
        json.dumps({"path": path}, indent=2) + "\n",
    )

    assert _run().ok


def test_the_threshold_separates_a_quote_from_a_coincidence(fake_root: Path) -> None:
    """A run of `MIN_QUOTE_TOKENS` fails; one token shorter passes.

    This is the boundary the module docstring justifies against the committed
    tree, pinned here so a future change to the threshold has to face it.
    """
    _seed(
        fake_root,
        [
            _record(
                "renemarc_packages_lighting_yaml",
                comments=[" ".join(THRESHOLD_WORDS)],
            ),
        ],
    )
    short = " ".join(THRESHOLD_WORDS[: prose_gate.MIN_QUOTE_TOKENS - 1])
    long = " ".join(THRESHOLD_WORDS[: prose_gate.MIN_QUOTE_TOKENS])
    write(fake_root, "docs/short.md", short)
    write(fake_root, "docs/long.md", long)

    findings = _run().diagnostics

    assert [finding.where for finding in findings] == ["docs/long.md"]


def test_a_missing_store_is_reported_rather_than_passed(fake_root: Path) -> None:
    """With no store the gate cannot compare anything, so it says so."""
    write(fake_root, ".gitignore", ".local/\n")
    write(
        fake_root,
        "catalog/licenses.yaml",
        yaml.safe_dump({"repos": [RENEMARC]}, sort_keys=False),
    )

    with pytest.raises(CheckError) as raised:
        _run()

    assert raised.value.where == f".local/{prose_gate.VERBATIM_FILE}"


def test_missing_licence_records_are_reported(fake_root: Path) -> None:
    """Which repos are non-permissive comes from the records, or from nowhere."""
    write(fake_root, ".gitignore", ".local/\n")
    write(fake_root, ".local/raw-verbatim.json", json.dumps({"records": []}))

    with pytest.raises(CheckError) as raised:
        _run()

    assert raised.value.where == "catalog/licenses.yaml"


# --------------------------------------------------------------------------
# The committed tree, and the constants this module must not re-spell
# --------------------------------------------------------------------------


def test_the_declared_store_filename_matches_the_normaliser() -> None:
    """The gate names the store it reads; the normaliser names the one it writes."""
    assert prose_gate.VERBATIM_FILE == normalise.VERBATIM_FILE


@pytest.mark.skipif(
    not (_COMMITTED.is_file() and _STORE.is_file()),
    reason="the committed and withheld stores are absent",
)
def test_the_prose_fields_are_exactly_what_the_verbatim_store_adds() -> None:
    """A quote can only be taken from a field the committed store withholds."""
    committed = json.loads(_COMMITTED.read_text(encoding="utf-8"))["records"][0]
    verbatim = json.loads(_STORE.read_text(encoding="utf-8"))["records"][0]

    assert set(verbatim) - set(committed) == set(prose_gate.PROSE_FIELDS)


@pytest.mark.skipif(not _STORE.is_file(), reason="the withheld store is absent")
def test_the_committed_tree_reproduces_no_non_permissive_prose() -> None:
    """The gate is green on the repository as committed."""
    report = Report()
    prose_gate.check_prose_gate(report)

    assert report.ok, report.render()
