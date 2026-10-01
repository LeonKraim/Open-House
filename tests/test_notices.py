"""State-change notices -- task 6.5.

The obligation is one, at two units, and each unit has a case that the other
cannot cover: a row whose source imposes `state_changes` and that says nothing,
and a file whose rows say that much but whose own front matter does not. Both
are asserted here, together with the reverse error -- a notice on a row that no
such source produced, which is the blanket disclaimer the row-level rule exists
to refuse.

The source that imposes the obligation is written into the fixture as
`apache_2_0`, and it is deliberately not named in the check: the fixtures carry
a second repo under a licence that does not impose it, so a check that fired on
every row, or on the wrong repo, would disagree with one of the cases below.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import yaml

from tools.catalog import licenses, notices, paths
from tools.catalog.errors import Report

from .conftest import write

if TYPE_CHECKING:
    from pathlib import Path

BEHAVIORS_PATH = "catalog/behaviors.yaml"
LICENCES_PATH = "catalog/licenses.yaml"

#: The two repos the fixtures settle, one under each side of the obligation.
_OBLIGING = "renemarc"
_PLAIN = "ccostan"

#: A notice that satisfies both halves of the sentence: what changed, and by
#: whom. Used wherever a case is about the *other* unit.
_NOTICE = f"Adapted from an Apache-2.0 source and restated in our own words; {notices.ADAPTER}."


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------


def _row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "id": "lighting.example",
        "source_repos": [_PLAIN],
        "change_notice": None,
    }
    row.update(overrides)
    return row


def _licences(root: Path) -> None:
    """Settle both repos: one imposes `state_changes`, the other does not."""
    write(
        root,
        LICENCES_PATH,
        yaml.safe_dump(
            {
                "repos": [
                    {
                        "repo": _OBLIGING,
                        "author": _OBLIGING,
                        "license_code": "apache_2_0",
                    },
                    {"repo": _PLAIN, "author": _PLAIN, "license_code": "mit"},
                ]
            },
            sort_keys=False,
        ),
    )


def _behaviors(
    root: Path, rows: list[dict[str, object]], notice: object = None
) -> None:
    write(
        root,
        BEHAVIORS_PATH,
        yaml.safe_dump({"behaviors": rows, "change_notice": notice}, sort_keys=False),
    )


def _messages() -> str:
    report = Report()
    notices.check_notices(report)
    return "\n".join(f"{d.where}: {d.message}" for d in report.diagnostics)


# --------------------------------------------------------------------------
# The row
# --------------------------------------------------------------------------


def test_an_adapted_row_without_a_change_notice_is_named(fake_root: Path) -> None:
    _licences(fake_root)
    _behaviors(
        fake_root,
        [_row(id="lighting.adapted", source_repos=[_OBLIGING], change_notice=None)],
        notice=_NOTICE,
    )

    messages = _messages()
    assert "lighting.adapted" in messages
    assert "change_notice" in messages


def test_a_blank_notice_is_read_as_no_notice(fake_root: Path) -> None:
    """A field whose type is satisfied and whose meaning is empty says nothing."""
    _licences(fake_root)
    _behaviors(
        fake_root,
        [_row(id="lighting.adapted", source_repos=[_OBLIGING], change_notice="   ")],
        notice=_NOTICE,
    )
    assert "lighting.adapted" in _messages()


def test_a_notice_that_does_not_name_the_adapter_is_named(fake_root: Path) -> None:
    """The obligation is to say by whom, and Phase 0's adapter is us."""
    _licences(fake_root)
    _behaviors(
        fake_root,
        [
            _row(
                id="lighting.adapted",
                source_repos=[_OBLIGING],
                change_notice="We changed it.",
            )
        ],
        notice=_NOTICE,
    )

    messages = _messages()
    assert "lighting.adapted" in messages
    assert notices.ADAPTER in messages


def test_an_adapted_row_carrying_a_notice_validates(fake_root: Path) -> None:
    _licences(fake_root)
    _behaviors(
        fake_root,
        [_row(id="lighting.adapted", source_repos=[_OBLIGING], change_notice=_NOTICE)],
        notice=_NOTICE,
    )
    assert _messages() == ""


def test_a_row_from_no_state_changes_source_needs_no_notice(fake_root: Path) -> None:
    _licences(fake_root)
    _behaviors(
        fake_root,
        [_row(id="lighting.own", source_repos=[_PLAIN], change_notice=None)],
        notice=None,
    )
    assert _messages() == ""


def test_a_notice_on_a_row_that_no_state_changes_source_produced_is_named(
    fake_root: Path,
) -> None:
    """The reverse error, and the reason the file-level notice is not enough."""
    _licences(fake_root)
    _behaviors(
        fake_root,
        [_row(id="lighting.own", source_repos=[_PLAIN], change_notice=_NOTICE)],
        notice=None,
    )
    assert "lighting.own" in _messages()


def test_a_row_citing_both_kinds_of_source_is_adapted(fake_root: Path) -> None:
    """One obliging source among several is enough to incur the obligation."""
    _licences(fake_root)
    _behaviors(
        fake_root,
        [
            _row(
                id="lighting.merged",
                source_repos=[_PLAIN, _OBLIGING],
                change_notice=None,
            )
        ],
        notice=_NOTICE,
    )
    assert "lighting.merged" in _messages()


# --------------------------------------------------------------------------
# The file
# --------------------------------------------------------------------------


def test_a_file_with_an_adapted_row_and_no_file_level_notice_is_named(
    fake_root: Path,
) -> None:
    _licences(fake_root)
    _behaviors(
        fake_root,
        [_row(id="lighting.adapted", source_repos=[_OBLIGING], change_notice=_NOTICE)],
        notice=None,
    )
    assert BEHAVIORS_PATH in _messages()


def test_a_file_with_no_adapted_row_is_left_alone(fake_root: Path) -> None:
    """No adaptation, no declaration: the notice is not a blanket disclaimer."""
    _licences(fake_root)
    _behaviors(
        fake_root,
        [_row(id="lighting.own", source_repos=[_PLAIN], change_notice=None)],
        notice=None,
    )
    assert _messages() == ""


def test_a_file_level_notice_without_any_adapted_row_is_accepted(
    fake_root: Path,
) -> None:
    """The file-level rule is one-directional; only the row field is `null`
    otherwise, and a file that declares more than it owes is not the error the
    obligation names."""
    _licences(fake_root)
    _behaviors(
        fake_root,
        [_row(id="lighting.own", source_repos=[_PLAIN], change_notice=None)],
        notice=_NOTICE,
    )
    assert _messages() == ""


# --------------------------------------------------------------------------
# The committed tree
# --------------------------------------------------------------------------


def _committed_adapted_rows() -> list[dict[str, object]]:
    document = yaml.safe_load(
        (paths.CATALOG / "behaviors.yaml").read_text(encoding="utf-8")
    )
    obliging = {
        record.repo
        for record in licenses.load_licences()
        if "state_changes" in record.obligations_code
    }
    return [
        row
        for row in document["behaviors"]
        if set(row.get("source_repos") or []) & obliging
    ]


def test_the_committed_tree_records_every_adaptation(real_root: Path) -> None:
    report = Report()
    notices.check_notices(report)
    assert report.ok, report.render()


def test_the_committed_adaptation_is_not_vacuous(real_root: Path) -> None:
    """A corpus with nothing adapted would satisfy the rule by saying nothing."""
    adapted = _committed_adapted_rows()
    assert adapted

    document = yaml.safe_load(
        (paths.CATALOG / "behaviors.yaml").read_text(encoding="utf-8")
    )
    assert document["change_notice"]
