"""The provenance-resolved identifier gate -- task 4.6.

The gate's job is narrow and specific: a row may describe a withholding source's
behaviour in our own words, but it may not reproduce the entity ids that source
happened to use. What makes the gate *provenance-resolved* is that both the
trigger and the forbidden set are functions of the row's own citations, so the
tests here are all about that resolution rather than about a global blacklist:

- the same identifier is forbidden in a row that cites the withholding repo and
  permitted in a row that does not, even while both files sit in the same tree;
- the fields scanned come from the row schema, so a field the schema declares is
  scanned without the gate being told, and `expression` is not scanned because it
  is not a scalar string;
- a worked example is resolved against *its own* `raw_ids`, not the row's, since
  an example is drawn from one source of a possibly merged row.

The sixth test builds the case the brief names -- an example from a non-granting
repo -- and confirms it is the example's own provenance that decides.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import yaml

from tools.catalog import identifier_gate
from tools.catalog.errors import Report

from .conftest import write

if TYPE_CHECKING:
    from pathlib import Path

BEHAVIORS_PATH = "catalog/behaviors.yaml"
RAW_PATH = "catalog/raw-behaviors.json"
LICENCES_PATH = "catalog/licenses.yaml"
SCHEMA_PATH = "schemas/catalog/behaviors.json"

#: The identifier both sides of the tests agree on. It is a real shape --
#: `domain.object_id` -- because the gate matches that shape, not a symbol.
BORROWED = "light.kitchen_ceiling"


def _schema(root: Path, extra: dict[str, object] | None = None) -> None:
    """A row schema, trimmed to the fields the gate reads.

    Written rather than pointed at the real schema so that a test can add a
    field and watch the gate pick it up. That is the property under test: the
    scanned fields are the schema's, not a list kept beside the gate.
    """
    properties: dict[str, object] = {
        "id": {"type": "string"},
        "name": {"type": "string"},
        "description": {"type": "string"},
        "category": {"type": "string"},
        "concept": {"type": "string"},
        "change_notice": {"type": ["string", "null"]},
        "retention": {"type": ["string", "null"]},
        "expression": {"type": ["object", "null"]},
        "source_repos": {"type": "array"},
        "raw_ids": {"type": "array"},
        "scope": {"enum": ["room", "house"]},
    }
    if extra:
        properties.update(extra)
    document = {"$defs": {"behaviour": {"properties": properties}}}
    write(root, SCHEMA_PATH, json.dumps(document, indent=2) + "\n")


def _row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "id": "lighting.example",
        "name": "Example",
        "description": "In our own words.",
        "category": "lighting",
        "concept": "What it means, in our own words.",
        "change_notice": None,
        "retention": None,
        "expression": None,
        "source_repos": ["johnkoht"],
        "raw_ids": ["johnkoht_a_yaml"],
    }
    row.update(overrides)
    return row


def _record(raw_id: str, entity_refs: list[str] | None = None) -> dict[str, object]:
    return {"id": raw_id, "entity_refs": list(entity_refs or []), "unclaimed": None}


def _setup(
    root: Path,
    rows: list[dict[str, object]],
    records: list[dict[str, object]],
    codes: dict[str, str] | None = None,
) -> None:
    _schema(root)
    write(root, BEHAVIORS_PATH, yaml.safe_dump({"behaviors": rows}, sort_keys=False))
    write(
        root,
        RAW_PATH,
        json.dumps({"records": records}, indent=2, sort_keys=True) + "\n",
    )
    licences = codes or {
        "ccostan": "mit",
        "renemarc": "apache_2_0",
        "johnkoht": "no_licence",
    }
    write(
        root,
        LICENCES_PATH,
        yaml.safe_dump(
            {
                "repos": [
                    {"repo": repo, "author": repo, "license_code": code}
                    for repo, code in licences.items()
                ]
            },
            sort_keys=False,
        ),
    )


def _messages() -> str:
    report = Report()
    identifier_gate.check_identifier_gate(report)
    return "\n".join(
        f"{d.where}: {d.message}"
        for d in report.diagnostics
        if d.check == identifier_gate.IDENTIFIER_GATE_CHECK
    )


# --------------------------------------------------------------------------
# The identifier, field by field
# --------------------------------------------------------------------------


def test_an_identifier_in_concept_fails_naming_it(fake_root: Path) -> None:
    _setup(
        fake_root,
        [_row(concept=f"It mirrors {BORROWED} exactly.")],
        [_record("johnkoht_a_yaml", [BORROWED])],
    )
    messages = _messages()
    assert "lighting.example" in messages
    assert BORROWED in messages
    assert "concept" in messages


def test_an_identifier_in_name_fails_naming_it(fake_root: Path) -> None:
    _setup(
        fake_root,
        [_row(name=f"Turn on {BORROWED}")],
        [_record("johnkoht_a_yaml", [BORROWED])],
    )
    messages = _messages()
    assert "lighting.example" in messages
    assert BORROWED in messages
    assert "name" in messages


def test_an_identifier_in_change_notice_fails_naming_it(fake_root: Path) -> None:
    _setup(
        fake_root,
        [_row(change_notice=f"Adapted from a script that switched {BORROWED}.")],
        [_record("johnkoht_a_yaml", [BORROWED])],
    )
    messages = _messages()
    assert "lighting.example" in messages
    assert BORROWED in messages
    assert "change_notice" in messages


def test_a_clean_row_citing_a_withholding_repo_passes(fake_root: Path) -> None:
    _setup(
        fake_root,
        [_row(concept="Turns a room's main light on when somebody enters.")],
        [_record("johnkoht_a_yaml", [BORROWED])],
    )
    assert _messages() == ""


# --------------------------------------------------------------------------
# Resolution: the same identifier is judged per row
# --------------------------------------------------------------------------


def test_a_granting_only_row_may_use_an_identifier_a_withholding_repo_also_has(
    fake_root: Path,
) -> None:
    """The identifier is withheld *from the row that cites the withholding repo*.

    The withholding record is in the same store, and the granting row uses the
    same string, but the row does not cite that record -- so the string is not in
    the row's forbidden set, and the gate leaves it alone.
    """
    _setup(
        fake_root,
        [
            _row(
                id="lighting.granted",
                source_repos=["ccostan"],
                raw_ids=["ccostan_a_yaml"],
                concept=f"Turns {BORROWED} on.",
            )
        ],
        [
            _record("ccostan_a_yaml", [BORROWED]),
            _record("johnkoht_a_yaml", [BORROWED]),
        ],
    )
    assert _messages() == ""


def test_a_row_whose_expression_mentions_a_service_passes(fake_root: Path) -> None:
    """`expression` is an object and is *meant* to hold a source's spelling.

    The raw record it cites carries `light.turn_on` as an entity ref, so if the
    gate scanned `expression` this would fail -- which is exactly the point: it
    must not, because the field exists to carry that spelling when the licences
    permit it, and the schema is what says so.
    """
    _setup(
        fake_root,
        [
            _row(
                id="lighting.borrowed_shape",
                concept="Turns a light on.",
                expression={"action": ["light.turn_on"]},
            )
        ],
        [_record("johnkoht_a_yaml", ["light.turn_on"])],
    )
    assert _messages() == ""
    assert "expression" not in identifier_gate.string_fields()


def test_a_field_added_to_the_schema_is_scanned_without_being_named(
    fake_root: Path,
) -> None:
    """The gate reads the schema; a new string field is scanned the moment it is."""
    _setup(fake_root, [], [])
    _schema(fake_root, {"notes": {"type": "string"}})
    write(
        fake_root,
        BEHAVIORS_PATH,
        yaml.safe_dump(
            {"behaviors": [_row(notes=f"An aside about {BORROWED}.")]},
            sort_keys=False,
        ),
    )
    write(
        fake_root,
        RAW_PATH,
        json.dumps({"records": [_record("johnkoht_a_yaml", [BORROWED])]}, indent=2)
        + "\n",
    )
    messages = _messages()
    assert BORROWED in messages
    assert "notes" in messages


def test_a_non_string_field_holding_an_identifier_is_not_scanned(
    fake_root: Path,
) -> None:
    """Only prose is the concern; an array of slots is structure, not a copy."""
    _setup(fake_root, [], [])
    _schema(fake_root, {"extras": {"type": "array"}})
    write(
        fake_root,
        BEHAVIORS_PATH,
        yaml.safe_dump({"behaviors": [_row(extras=[BORROWED])]}, sort_keys=False),
    )
    write(
        fake_root,
        RAW_PATH,
        json.dumps({"records": [_record("johnkoht_a_yaml", [BORROWED])]}, indent=2)
        + "\n",
    )
    assert _messages() == ""


# --------------------------------------------------------------------------
# Worked examples resolve against their own raw ids
# --------------------------------------------------------------------------


def test_an_example_from_a_non_granting_repo_is_checked_against_its_own_raw_ids(
    fake_root: Path,
) -> None:
    """The sixth clause: an example cites its own provenance, not the row's."""
    _schema(fake_root)
    records = {
        "johnkoht_a_yaml": {"id": "johnkoht_a_yaml", "entity_refs": [BORROWED]},
        "ccostan_a_yaml": {"id": "ccostan_a_yaml", "entity_refs": ["light.hall"]},
    }
    statuses = {"ccostan": "reusable", "johnkoht": "ideas_only"}

    borrowed = {
        "source_repo": "johnkoht",
        "raw_ids": ["johnkoht_a_yaml"],
        "description": f"Sets {BORROWED} to on.",
    }
    assert identifier_gate.slot_example_leaks(borrowed, records, statuses) == (
        BORROWED,
    )

    granted = {
        "source_repo": "ccostan",
        "raw_ids": ["ccostan_a_yaml"],
        "description": "Sets the hall light to on.",
    }
    assert identifier_gate.slot_example_leaks(granted, records, statuses) == ()


def test_an_example_is_judged_against_its_own_raw_ids_not_the_rows(
    fake_root: Path,
) -> None:
    """An example drawn from a granting source passes though the row cites more.

    The row is merged and cites both sources; the example is drawn from the
    granting one, and the identifiers it may not copy are that source's -- so the
    withholding source's identifier, present in the tree, is irrelevant to it.
    """
    _schema(fake_root)
    records = {
        "johnkoht_a_yaml": {"id": "johnkoht_a_yaml", "entity_refs": [BORROWED]},
        "ccostan_a_yaml": {"id": "ccostan_a_yaml", "entity_refs": ["light.hall"]},
    }
    statuses = {"ccostan": "reusable", "johnkoht": "ideas_only"}
    example = {
        "source_repo": "ccostan",
        "raw_ids": ["ccostan_a_yaml"],
        "description": "Uses a motion sensor to switch a light.",
    }
    assert identifier_gate.slot_example_leaks(example, records, statuses) == ()


# --------------------------------------------------------------------------
# The committed corpus
# --------------------------------------------------------------------------


def test_the_committed_corpus_is_free_of_withheld_identifiers(real_root: Path) -> None:
    report = Report()
    identifier_gate.check_identifier_gate(report)
    assert report.ok, report.render()


def test_the_gate_actually_has_rows_to_judge(real_root: Path) -> None:
    """A gate that judged nothing would pass the test above by accident."""
    from tools.catalog import behaviors, licenses

    statuses = {rec.repo: rec.reuse_status_code for rec in licenses.load_licences()}
    rows = behaviors.load_behaviors()
    withheld = [
        row
        for row in rows
        if identifier_gate.withholds(
            [str(repo) for repo in row["source_repos"]],  # type: ignore[union-attr]
            statuses,
        )
    ]
    assert withheld, "no row cites a withholding repo; the gate would be vacuous"
