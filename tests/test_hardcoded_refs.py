"""The hardcoding audit and the check over it -- task 3.6.

`catalog/hardcoded_refs.yaml` is the second committed facts store, and its
three verify clauses are all about what an entry may *be*: a reference the
classifier called an entity rather than a service call, a scope and a
naming-convention label, and nothing that came from the source as prose. Like
the record store, it is a facts file, so the clauses are structural -- an entry
has nowhere to put an alias -- and the check is what makes that structural
rather than declarative.

The assertion that each entry resolves to a `slot` or a justified `constant`
is *not* here: task 3.6 defers it to 5.3, where `catalog/slots.yaml` exists, so
it lives in `tests/test_slots.py`. It is enforced twice now -- by `check_slots`,
which names the reference and its repo, and structurally by the generated entry
schema's entry-level `oneOf`, which refuses an entry with both fields null or
both set. The fixtures below keep both fields null only because this file's
check does not resolve them; they are not schema-validated.

Same fixture discipline as `test_normalise.py`: the committed tree for the
clauses that are about it, and a hand-built violating tree for the clause that
has to be seen to fail.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import yaml

from tools.catalog import facts, normalise, paths
from tools.catalog.errors import Report

from .conftest import write

if TYPE_CHECKING:
    from pathlib import Path

AUDIT_CHECK = "hardcoded-refs"

#: The audit's entry fields, as a minimal allowlist. Only the `hardcoded_fields`
#: section is read by `check_hardcoded_refs`, so the record half of the real file
#: is not reproduced here -- the check under test resolves the field set, not the
#: record schema.
_AUDIT_FIELDS = """$id: https://open-house.invalid/schemas/catalog/fact-fields.yaml
title: Fact fields
schema_version: "1.0.0"
supersedes: null
hardcoded_fields:
  entity_ref:
    shape: entity_ref
  repo:
    shape: slug
  scope:
    enum: [room, house]
  naming_convention:
    shape: slug
  slot:
    enum_ref: catalog/slots.yaml#name
    nullable: true
  constant:
    enum: [device_id, device_tracker, person, sun, time]
    nullable: true
"""


def _entry(
    entity_ref: str,
    repo: str = "ccostan",
    *,
    scope: str = "room",
    naming_convention: str | None = None,
    **extra: object,
) -> dict[str, object]:
    """A well-formed audit entry, before any test spoils one field."""
    entry: dict[str, object] = {
        "entity_ref": entity_ref,
        "repo": repo,
        "scope": scope,
        "naming_convention": naming_convention
        if naming_convention is not None
        else normalise.NAMING_CONVENTIONS[repo],
        "slot": None,
        "constant": None,
    }
    entry.update(extra)
    return entry


def _seed_audit(
    root: Path, entries: list[dict[str, object]], entity_refs: list[str]
) -> None:
    """A record store to draw provenance from and an audit to check against it.

    The record store is seeded with the entity references the audit is allowed to
    name, and nothing else: the union `check_hardcoded_refs` builds is exactly
    that set, so an entry naming something outside it is the clause-1 failure.
    """
    write(root, "schemas/catalog/fact-fields.yaml", _AUDIT_FIELDS)
    write(
        root,
        "catalog/raw-behaviors.json",
        json.dumps(
            {"records": [{"entity_refs": entity_refs}], "change_notice": None},
            indent=2,
        )
        + "\n",
    )
    write(
        root,
        "catalog/hardcoded_refs.yaml",
        normalise.render_hardcoded_refs({"refs": entries}),
    )


def _diagnostics() -> list[tuple[str, str]]:
    report = Report()
    normalise.check_hardcoded_refs(report)
    return [(d.where, d.message) for d in report.diagnostics]


def _read_committed() -> dict[str, object]:
    text = (paths.CATALOG / normalise.HARDCODED_REFS_FILE).read_bytes().decode("utf-8")
    return yaml.safe_load(text)


# --- clause 1: no service call appears ---------------------------------------


def test_no_service_call_appears_in_the_committed_audit(real_root: Path) -> None:
    """Task 3.6: "a test that no `service_call` appears in the file".

    The clause cannot be a shape test -- `light.turn_on` and `light.kitchen` are
    spelled identically -- so it is a membership test against the record store the
    audit was drawn from. Every entry's `entity_ref` has to be a reference some
    record's `entity_refs` carries, and a service call is in no record's set by
    construction, because the classifier keeps them out.
    """
    entries = _read_committed()["refs"]
    assert entries, "the committed audit is empty, so the clause is vacuous"

    records = json.loads(
        (paths.CATALOG / normalise.RAW_BEHAVIORS_FILE).read_bytes().decode("utf-8")
    )
    union = {ref for record in records["records"] for ref in record["entity_refs"]}
    offenders = sorted(
        entry["entity_ref"] for entry in entries if entry["entity_ref"] not in union
    )
    assert offenders == [], (
        f"audit entries that are not any record's entity_ref: {offenders}"
    )


def test_an_audit_entry_naming_a_service_call_fails(fake_root: Path) -> None:
    """The same clause on a tree built to violate it.

    `light.turn_on` is a service call the classifier recorded in *no* record's
    entity set, so it is absent from the union the check builds. The finding
    names the entry and the repo it claims, which is what a reader needs to find
    the entry rather than merely know one is wrong.
    """
    _seed_audit(
        fake_root,
        [_entry("light.turn_on"), _entry("light.kitchen")],
        entity_refs=["light.kitchen"],
    )
    diagnostics = _diagnostics()
    offenders = [message for where, message in diagnostics if "light.turn_on" in where]
    assert len(offenders) == 1, diagnostics
    assert "service call is spelled the same way" in offenders[0]


# --- clause 2: every entry carries a scope and a naming-convention label -----


def test_every_committed_entry_carries_a_scope_and_a_naming_convention_label(
    real_root: Path,
) -> None:
    """Task 3.6: "every entry carries a scope and a naming-convention label".

    Both are checked against committed sets rather than merely for presence: the
    scope against `{room, house}`, and the label against the label its own repo
    carries. A present-but-wrong label would satisfy a presence-only assertion
    and make two repos incomparable, which is the thing the label exists to
    prevent.
    """
    entries = _read_committed()["refs"]
    assert entries
    for entry in entries:
        assert entry["scope"] in ("room", "house"), entry
        assert (
            entry["naming_convention"] == normalise.NAMING_CONVENTIONS[entry["repo"]]
        ), entry


def test_an_entry_with_a_scope_or_label_that_is_not_committed_fails(
    fake_root: Path,
) -> None:
    """The two ways the clause-2 fields go wrong, each named.

    An out-of-set scope and a label that is not the repo's own are different
    defects -- one is a value nobody defined, the other is a value defined for a
    different repo -- and the messages say which one the reader is looking at.
    """
    _seed_audit(
        fake_root,
        [
            _entry("light.kitchen", scope="office"),
            _entry("light.hall", naming_convention="some_other_label"),
        ],
        entity_refs=["light.kitchen", "light.hall"],
    )
    diagnostics = _diagnostics()
    scope_message = [m for w, m in diagnostics if "light.kitchen" in w]
    assert len(scope_message) == 1 and "neither `room` nor `house`" in scope_message[0]
    label_message = [m for w, m in diagnostics if "light.hall" in w]
    assert len(label_message) == 1 and "naming_convention" in label_message[0]


# --- clause 3: no alias, display name, comment or YAML fragment ---------------


def test_no_committed_entry_carries_text_from_its_source(real_root: Path) -> None:
    """Task 3.6: "no entry carries an alias, display name, comment or YAML fragment".

    The clause is structural: an entry's key set is the closed audit field set,
    so there is no field an alias could be written into, and every value that is
    a string is a single token -- an identifier, a repo handle, a scope or a
    label -- so none can hold the prose a display name or a comment is. The two
    assertions are both required: the key set alone would admit a sentence in a
    value, and the value shapes alone would admit an `alias` field beside them.
    """
    entries = _read_committed()["refs"]
    assert entries
    fields = set(facts.hardcoded_fields())
    for entry in entries:
        assert set(entry) == fields, entry
        for key, value in entry.items():
            if isinstance(value, str):
                assert value == value.strip(), (key, value)
                assert " " not in value, (key, value)


def test_an_entry_carrying_a_field_outside_the_audit_set_fails(
    fake_root: Path,
) -> None:
    """The violating fixture: an entry with an `alias` written into it.

    `alias` is the field this whole store exists to keep out -- a source's
    authored text is exactly what the two non-granting repos withhold -- and the
    finding names both the entry and the field, because a message naming only the
    field would not say which of a thousand entries grew it.
    """
    _seed_audit(
        fake_root,
        [_entry("light.kitchen", alias="Kitchen Ceiling Light")],
        entity_refs=["light.kitchen"],
    )
    diagnostics = _diagnostics()
    message = [m for w, m in diagnostics if "light.kitchen" in w]
    assert len(message) == 1, diagnostics
    assert "carries `alias`" in message[0]


# --- the tree ships clean ----------------------------------------------------


def test_the_audit_check_passes_on_the_committed_tree(real_root: Path) -> None:
    """The check against the artifact it protects, which no fixture can show."""
    assert _diagnostics() == []
