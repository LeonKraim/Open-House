"""The normaliser, the two stores and the fact allowlist -- task 3.5.

This file carries task 3.5's fourteen verify clauses. They are not fourteen
claims about one thing: the clause list covers the classifier (a service call
and a template reference go to different sets), the committed record's
relationship to its allowlist (the key set is equal, closed in both
directions, and the schema is generated rather than edited), and the
filesystem position of the withheld store. So the tests below are grouped by
which of those three a clause is about, and each names the clause in its
docstring.

The fixtures-required discipline of `conftest` decides the shape of almost all
of them. A clause about the committed tree is asserted on `real_root`; a clause
about a *violating* tree -- an invented key, a permissive pattern, a schema
property with no allowlist entry -- is asserted on `fake_root`, because the
committed tree is by construction the one that passes and a check proven only
there has never been seen to fail.

The allowlist fixtures are hand-written and small rather than the committed
one copied and mutated. That is deliberate: a fixture that copies the real
files and changes a byte exercises the *diff* between the real allowlist and
the mutant, so a change to the real allowlist silently changes what the
fixture tests. A hand-written three-field allowlist means the fixture is the
whole input, and the clause under test is the only thing moving.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import jsonschema
import pytest

from tools.catalog import facts, inventory, normalise, paths
from tools.catalog.errors import Report

from .conftest import write

if TYPE_CHECKING:
    from pathlib import Path

FACT_CHECK = "fact-allowlist"

#: One behaviour, written twice: the old `service:`/`trigger:` spelling and the
#: modern `action:`/`triggers:` one. They are the two dialects the classifier has
#: to fold into one normalised form, and clause 1 is the assertion that it does.
#:
#: `platform: state` (old) and `trigger: state` (new) are the same trigger kind
#: spelled two ways; `service:` and `action:` are the same operation spelled two
#: ways. If the classifier read either key literally the two documents would
#: normalise differently and the vocabulary would fork on dialect.
_OLD_STYLE = """
- alias: Kitchen light on motion
  trigger:
    - platform: state
      entity_id: binary_sensor.kitchen_motion
      to: "on"
  condition:
    - condition: state
      entity_id: input_boolean.guest_mode
      state: "off"
  action:
    - service: light.turn_on
      target:
        entity_id: light.kitchen
"""

_NEW_STYLE = """
- alias: Kitchen light on motion
  triggers:
    - trigger: state
      entity_id: binary_sensor.kitchen_motion
      to: "on"
  conditions:
    - condition: state
      entity_id: input_boolean.guest_mode
      state: "off"
  actions:
    - action: light.turn_on
      target:
        entity_id: light.kitchen
"""

#: A minimal automation for the record-key-set and validation clauses. It cites
#: one entity and calls one service, so its record is not empty.
_AUTOMATION = """
- alias: Night light
  trigger:
    - platform: state
      entity_id: binary_sensor.hall_motion
  action:
    - service: light.turn_on
      target:
        entity_id: light.hall
"""

#: The four fact fields the classifier feeds, as the normaliser names them. Used
#: only to keep the clause-1 assertion readable.
_FACT_FIELDS = ("triggers", "conditions", "actions", "entity_refs", "service_calls")

# --- the allowlist fixture ---------------------------------------------------
#
# Three shapes, four record fields and six audit fields. `vocabulary` and
# `integer` fields are deliberately absent: a vocabulary field resolves through
# `schemas/behavior-vocabulary/`, and an integer needs nothing from this file.
# Every field here is an `enum`, a `shape` or a `list_of`, which is the smallest
# allowlist that still exercises the generator's whole vocabulary.

_SHAPES = """$id: https://open-house.invalid/schemas/catalog/fact-shapes.yaml
shapes:
  entity_ref:
    kind: pattern
    pattern: "^[a-z_]+\\\\.[a-z0-9_]+$"
  slug:
    kind: pattern
    pattern: "^[a-z][a-z0-9_]*$"
  selected_path:
    kind: membership
    source: catalog/inventory.json
"""

_FIELDS = """$id: https://open-house.invalid/schemas/catalog/fact-fields.yaml
title: Fact fields
schema_version: "1.0.0"
supersedes: null
fields:
  path:
    shape: selected_path
  id:
    shape: slug
  class:
    enum: [automation, other]
  entity_refs:
    list_of:
      shape: entity_ref
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


def _inventory(selected: tuple[str, ...]) -> str:
    files = [
        {"path": path, "class": "automation", "selected": True} for path in selected
    ]
    return json.dumps({"repos": [{"repo": "ccostan", "files": files}]}, indent=2) + "\n"


def _seed_allowlist(
    root: Path,
    *,
    fields: str = _FIELDS,
    shapes: str = _SHAPES,
    selected: tuple[str, ...] = ("config/x.yaml",),
) -> None:
    """A complete allowlist, shape set, inventory and the schemas generated from them.

    The committed schemas are always generated from the *unmutated* defaults and
    the caller's `fields`/`shapes` are written afterwards. That ordering is what
    lets a test hand in an allowlist a generator cannot resolve -- a field with no
    form, say -- without the fixture itself raising: the schema is built from the
    good allowlist, and the mutant is then the only difference the check sees.
    Generating from the mutant instead would make the fixture impossible to write
    for the very clauses about allowlists the generator refuses.
    """
    write(root, "schemas/catalog/fact-fields.yaml", _FIELDS)
    write(root, "schemas/catalog/fact-shapes.yaml", _SHAPES)
    write(root, "catalog/inventory.json", _inventory(selected))
    write(
        root,
        "schemas/catalog/raw-behaviors.json",
        facts.render_schema(facts.generate_raw_record_schema()),
    )
    write(
        root,
        "schemas/catalog/hardcoded_refs.json",
        facts.render_schema(facts.generate_hardcoded_refs_schema()),
    )
    if fields != _FIELDS:
        write(root, "schemas/catalog/fact-fields.yaml", fields)
    if shapes != _SHAPES:
        write(root, "schemas/catalog/fact-shapes.yaml", shapes)


def _diagnostics() -> list[tuple[str, str]]:
    report = Report()
    facts.check_fact_allowlist(report)
    return [(d.where, d.message) for d in report.diagnostics]


def _matching(diagnostics: list[tuple[str, str]], where: str, fragment: str) -> str:
    """The one message at `where` containing `fragment`.

    Not `_only` from `test_catalog_data.py`, because two of the clauses here
    produce a second finding at the same `where` -- a hand edit to the generated
    schema both adds a property *and* changes the bytes, so the property clause
    and the byte-identity clause fire together. Asserting on the fragment pins
    the finding under test rather than the count beside it.
    """
    hits = [
        message
        for name, message in diagnostics
        if name == where and fragment in message
    ]
    assert len(hits) == 1, (
        f"expected one {fragment!r} finding at {where}: {diagnostics}"
    )
    return hits[0]


# --- clause 1: one behaviour, two dialects, one normalisation ----------------


def test_the_two_yaml_dialects_normalise_to_the_same_facts() -> None:
    """Task 3.5: "fixtures per style asserting identical normalised output".

    The two documents say the same thing -- the same entity, the same service,
    the same trigger and condition kinds -- and differ only in the keys each
    dialect spells them with. The clauses of the extraction that describe the
    behaviour, and not the file, must therefore be equal. `repo`, `path` and
    `record_id` are excluded because they describe where the text came from, not
    what it does, and the two fixtures are different files by construction.
    """
    old = normalise.extract("ccostan", "config/old.yaml", "automation", _OLD_STYLE)
    new = normalise.extract("ccostan", "config/new.yaml", "automation", _NEW_STYLE)

    assert tuple(getattr(old, field) for field in _FACT_FIELDS) == tuple(
        getattr(new, field) for field in _FACT_FIELDS
    )
    # The shared value is the one the fixtures were built to produce, so the
    # equality above cannot be satisfied by both sides being empty.
    assert old.entity_refs == (
        "binary_sensor.kitchen_motion",
        "input_boolean.guest_mode",
        "light.kitchen",
    )
    assert old.service_calls == ("light.turn_on",)
    assert old.triggers == ("state",)
    assert old.actions == ("service",)


# --- clauses 2 and 3: the structural classifier ------------------------------


def test_a_service_key_yields_a_service_call_and_enters_no_entity_set() -> None:
    """Task 3.5's clause, verbatim: `service: light.turn_on`.

    The distinction is the whole reason the classifier is structural rather than
    a pattern match: `light.turn_on` and an entity id are spelled identically, so
    a rule over strings alone would put a service call in the entity set and the
    provenance gate would compare shipped prose against a name nobody's entity
    has.
    """
    entities, services = normalise.classify_references(
        [{"service": "light.turn_on", "target": {"entity_id": "light.kitchen"}}]
    )
    assert "light.turn_on" in services
    assert "light.turn_on" not in entities
    assert entities == frozenset({"light.kitchen"})
    # And the entity-only view -- the one the provenance gate reads -- agrees.
    assert "light.turn_on" not in normalise.entity_identifier_set(
        [{"service": "light.turn_on", "target": {"entity_id": "light.kitchen"}}]
    )


def test_a_template_reference_is_an_entity_and_a_templated_service_call_is_not() -> (
    None
):
    """Task 3.5: references embedded in Jinja templates, both directions.

    A template is a string to YAML, so neither reference here is reachable by
    walking structure; both are found by reading the block. The first is a
    `states(...)` argument and is an entity; the second is an argument of a
    service call -- and the third is the corpus's own shape, a template that *is*
    the value of an `action` key -- and neither enters the entity set.
    """
    entities, services = normalise.classify_references(
        {
            "value_template": "{{ states('sensor.temperature') | float > 20 }}",
            "action": [
                "{{ service('light.turn_on') }}",
                '{{ "hassio.host_reboot" if x else "homeassistant.restart" }}',
            ],
        }
    )
    assert entities == frozenset({"sensor.temperature"})
    assert services == frozenset(
        {"light.turn_on", "hassio.host_reboot", "homeassistant.restart"}
    )


# --- clause 4: the key set of a record for a non-granting source --------------


def test_a_record_for_a_non_granting_source_has_exactly_the_allowlist_keys(
    real_root: Path,
) -> None:
    """Task 3.5: the record is a fact set, equal to the allowlist and no more.

    `fwartner` grants no reuse, so its record is the one where "the normaliser is
    structurally incapable of emitting authored text" has to hold hardest. The
    equality is asserted as a set equality rather than a subset, because a subset
    would be satisfied by a normaliser that had simply stopped emitting fields.
    """
    record = normalise.extract(
        "fwartner", "automations/lights.yaml", "automation", _AUTOMATION
    ).as_record()
    assert set(record) == set(facts.fact_fields())
    # The text the file wrote is not reachable from any of those keys.
    assert "alias" not in record
    assert record["entity_refs"] == ["binary_sensor.hall_motion", "light.hall"]


# --- clause 5: an extra key fails validation, naming the key ------------------


def test_a_record_with_an_extra_key_fails_validation_naming_the_key(
    real_root: Path,
) -> None:
    """Task 3.5: a record "carrying any extra key fails validation naming the key".

    The schema is the generated one, so this exercises the guarantee end to end:
    a key that is not an allowlist entry is refused by name, which is what makes
    the closed field set a property of the artifact rather than of the code that
    wrote it. `additionalProperties: false` alone produces the message; the
    `required` list is not what this clause is about.
    """
    record = normalise.extract(
        "fwartner", "automations/lights.yaml", "automation", _AUTOMATION
    ).as_record()
    record["alias"] = "Authored prose the source wrote"

    validator = jsonschema.Draft202012Validator(facts.generate_raw_record_schema())
    messages = [error.message for error in validator.iter_errors({"records": [record]})]
    assert any("alias" in message for message in messages), messages


# --- clauses 6, 7 and 8: the schema and the allowlist, both directions --------


def test_a_schema_property_without_an_allowlist_entry_fails_naming_the_property(
    fake_root: Path,
) -> None:
    """Task 3.5's direction with a property the allowlist does not name.

    Adding the property to the committed schema *alone* is the one edit the whole
    generation mechanism exists to refuse: it widens the field set without the
    allowlist moving. The finding names the property, so the reader knows which
    edit to undo rather than merely that the schema is wrong.
    """
    _seed_allowlist(fake_root)
    document = json.loads(
        (fake_root / "schemas/catalog/raw-behaviors.json").read_text(encoding="utf-8")
    )
    document["$defs"]["raw_record"]["properties"]["alias"] = {"type": "string"}
    write(
        fake_root,
        "schemas/catalog/raw-behaviors.json",
        facts.render_schema(document),
    )

    message = _matching(
        _diagnostics(),
        "schemas/catalog/raw-behaviors.json",
        "carries property `alias`",
    )
    assert "fact-fields.yaml" in message


def test_an_allowlist_entry_absent_from_the_schema_fails_naming_it(
    fake_root: Path,
) -> None:
    """Task 3.5's other direction: the allowlist and the schema have drifted.

    The committed schema here was generated from the four-field allowlist and is
    left untouched; the allowlist then gains a fifth field. The finding is
    reported against the *allowlist*, because the schema is the description of it
    and a field the schema does not carry is an allowlist entry nothing produces.
    A one-directional check -- schema properties missing from the allowlist --
    would pass this fixture and miss the drift entirely.
    """
    _seed_allowlist(fake_root)
    grown = _FIELDS.replace(
        "  entity_refs:\n    list_of:\n      shape: entity_ref\n",
        "  entity_refs:\n    list_of:\n      shape: entity_ref\n  extra:\n    shape: slug\n",
    )
    assert grown != _FIELDS, "the fixture did not modify the allowlist"
    write(fake_root, "schemas/catalog/fact-fields.yaml", grown)

    message = _matching(
        _diagnostics(), "schemas/catalog/fact-fields.yaml", "allows `fields.extra`"
    )
    assert "raw-behaviors.json" in message


def test_a_committed_schema_that_differs_from_a_fresh_generation_is_reported(
    fake_root: Path,
) -> None:
    """Task 3.5: the committed schema equals a fresh generation from the allowlist.

    The fixture edits a field the check does *not* compare by name -- the
    document's own `title` -- so the property sets still agree and the *only*
    finding is the byte-identity one. That isolates the clause: a fixture that
    changed a property would also produce the property-set finding, and the test
    would pass for a reason that is not this clause.
    """
    _seed_allowlist(fake_root)
    document = json.loads(
        (fake_root / "schemas/catalog/raw-behaviors.json").read_text(encoding="utf-8")
    )
    document["title"] = "Hand-edited title"
    write(
        fake_root, "schemas/catalog/raw-behaviors.json", facts.render_schema(document)
    )

    message = _matching(
        _diagnostics(),
        "schemas/catalog/raw-behaviors.json",
        "differs from a fresh generation",
    )
    assert "regenerated rather than edited" in message


# --- clauses 9, 10 and 11: what a field may not be ---------------------------


def test_an_unconstrained_field_is_rejected_and_a_shaped_field_is_not(
    fake_root: Path,
) -> None:
    """Task 3.5's contrast: a free-form string is refused, a `shape` is admitted.

    Both halves matter. The first asserts the allowlist has no form for an
    unconstrained string, so there is no field to write one into; the second is
    the control that keeps the first from being satisfied by a check that refuses
    everything -- `entity_ref` names a committed shape and passes.
    """
    _seed_allowlist(
        fake_root,
        fields=_FIELDS.replace("  id:\n    shape: slug\n", "  id:\n    type: string\n"),
    )
    message = _matching(
        _diagnostics(), "schemas/catalog/fact-fields.yaml:fields.id", "declares none of"
    )
    assert "free-form field is not permitted" in message

    # The control: the same allowlist with `id` naming a committed shape is clean.
    _seed_allowlist(fake_root)
    assert _diagnostics() == []


def test_a_field_carrying_its_own_pattern_is_rejected(fake_root: Path) -> None:
    """Task 3.5: a field may name a shape, not restate a pattern.

    A pattern written into the allowlist is a second definition of a shape, free
    to drift from `fact-shapes.yaml`, which is exactly what the closed shape set
    exists to prevent -- and a pattern tight enough to be worth writing is tight
    enough that the drift would go unnoticed. The finding says the field carries
    its own pattern rather than that it declares no form, so the two fixes are
    distinguishable.
    """
    _seed_allowlist(
        fake_root,
        fields=_FIELDS.replace(
            "  id:\n    shape: slug\n", '  id:\n    pattern: "^[a-z]+$"\n'
        ),
    )
    message = _matching(
        _diagnostics(),
        "schemas/catalog/fact-fields.yaml:fields.id",
        "carries its own `pattern`",
    )
    assert "shape" in message


def test_a_permissive_shape_fails_naming_the_field_and_the_pattern(
    fake_root: Path,
) -> None:
    """Task 3.5: a shape admitting arbitrary printable text, `^.*$`.

    Two findings, at two locations, and the clause names both. The shape itself
    is reported, because a permissive shape is a defect in the guarantee however
    many fields name it; and the field naming it is reported with the pattern
    resolved, because a reader looking at the field needs to see the regex that
    admitted the prose. The probe string the check uses is real authored text, so
    a pattern is permissive exactly when it would have admitted the source.
    """
    permissive = _SHAPES + '  loose:\n    kind: pattern\n    pattern: "^.*$"\n'
    _seed_allowlist(
        fake_root,
        shapes=permissive,
        fields=_FIELDS.replace("  id:\n    shape: slug\n", "  id:\n    shape: loose\n"),
    )
    diagnostics = _diagnostics()
    shape_message = _matching(
        diagnostics, "schemas/catalog/fact-shapes.yaml:loose", "^.*$"
    )
    assert "arbitrary printable text" in shape_message
    field_message = _matching(
        diagnostics, "schemas/catalog/fact-fields.yaml:fields.id", "^.*$"
    )
    assert "arbitrary printable text" in field_message


# --- clauses 12 and 13: the membership shape carries the path fact -----------


def _validate(record: dict[str, object]) -> list[str]:
    validator = jsonschema.Draft202012Validator(facts.generate_raw_record_schema())
    return [error.message for error in validator.iter_errors({"records": [record]})]


_RECORD = {
    "path": "config/x.yaml",
    "id": "ccostan_config_x_yaml",
    "class": "automation",
    "entity_refs": ["light.kitchen"],
}


def test_a_path_fact_passes_because_it_is_in_the_selected_path_set(
    fake_root: Path,
) -> None:
    """Task 3.5: the `path` fact is admitted by membership, not by a pattern.

    `config/x.yaml` is a selected path in the fixture's inventory, and the
    generated `path` property enumerates exactly that set, so the record passes.
    This is the clause that says the path fact is carried by the *inventory* and
    not by a regex: a pattern tight enough to exclude authored text would also
    exclude real filenames, which is why this one field resolves by membership.
    """
    _seed_allowlist(fake_root, selected=("config/x.yaml",))
    assert _validate(dict(_RECORD)) == []


def test_a_path_shaped_string_that_is_not_a_selected_path_fails(
    fake_root: Path,
) -> None:
    """Task 3.5's other half: a well-formed path that nothing selected is refused.

    The value is path-shaped -- it would satisfy any regex the project could
    write -- and it is rejected because it is not an element of the committed
    selected-path set. That is the difference between admitting a *fact about the
    corpus* and admitting a string that merely looks like one.
    """
    _seed_allowlist(fake_root, selected=("config/x.yaml",))
    messages = _validate({**_RECORD, "path": "config/not_selected.yaml"})
    assert any("config/not_selected.yaml" in message for message in messages), messages


# --- clause 14: the withheld store is outside the committed tree -------------


def test_local_is_gitignored_and_holds_no_tracked_file(real_root: Path) -> None:
    """Task 3.5: `.local/raw-verbatim.json` is gitignored and nothing under it is tracked.

    The withheld store carries the aliases, display names, comments and YAML
    blocks of the two repos that grant nothing, so its being outside version
    control is not a convenience -- it is the licence position. Two assertions
    because either alone is weak: a file can be untracked today without the rule
    that would keep it untracked tomorrow, and a rule can be present while a file
    was force-added past it.

    The check runs against the real repository rather than a fixture: the claim
    is about *this* tree's ignore rule and index, and a fixture that wrote its own
    `.gitignore` would be asserting that git works.
    """
    import subprocess

    ignored = subprocess.run(
        ["git", "check-ignore", "-q", ".local/raw-verbatim.json"],
        cwd=real_root,
        capture_output=True,
    )
    assert ignored.returncode == 0, ".local/raw-verbatim.json is not gitignored"

    tracked = subprocess.run(
        ["git", "ls-files", ".local"],
        cwd=real_root,
        capture_output=True,
        text=True,
        check=True,
    )
    assert tracked.stdout.strip() == "", (
        f"tracked files under .local/: {tracked.stdout!r}"
    )


# --- the tree ships clean ----------------------------------------------------


def test_the_allowlist_check_passes_on_the_committed_tree(real_root: Path) -> None:
    """The check's behaviour on the tree it ships, which no fixture can show.

    A check proven only against fixtures is a check that has never been run on
    the artifact it protects. This is the other half of the pair, and it is also
    the assertion that would have caught a committed schema left behind by an
    allowlist edit.
    """
    assert _diagnostics() == []


# --- the generated schemas are generated -------------------------------------


def test_the_two_generated_schemas_equal_a_fresh_generation(real_root: Path) -> None:
    """Tasks 3.5 and 5.3: the committed schemas are generated, not edited.

    The allowlist and the closed shape set are the source; the two schemas under
    `schemas/catalog/` are rendered from them. `facts.check_fact_allowlist` is the
    CI enforcement, and this is the same claim as a direct byte comparison so the
    staleness stands out even when the check is not registered in the validator.
    It is not idle: a schema generated while `catalog/slots.yaml` was empty carried
    an empty `slots` enum, and only a re-generation catches that once the data has
    moved on.
    """
    for name, generate in (
        ("raw-behaviors.json", facts.generate_raw_record_schema),
        ("hardcoded_refs.json", facts.generate_hardcoded_refs_schema),
    ):
        committed = (real_root / "schemas/catalog" / name).read_bytes().decode("utf-8")
        assert committed == facts.render_schema(generate()), name


# --- the extraction reproduces from committed code ---------------------------

_clones_present = paths.RESSOURCES.is_dir()


@pytest.mark.skipif(not _clones_present, reason="the reference clones are absent")
def test_a_re_run_reproduces_all_four_extraction_outputs_byte_for_byte() -> None:
    """Task 3.6: the committed extraction re-runs from committed code alone.

    Before the unclaimed register moved into `normalise`, `catalog/raw-behaviors.json`
    was not reproducible: hundreds of records carried an `unclaimed` reason written
    only by a gitignored local script, and a re-run from committed code set the
    field back to null. Every marked record is now derived from the two committed
    stores, so the pass is a function of the corpus. Each of the four outputs is
    compared to the committed file by name, so a drift in one is named rather than
    reported as a corpus-wide mismatch.
    """
    stores = normalise.build()
    produced = {
        "catalog/inventory.json": inventory.render(inventory.build()),
        "catalog/raw-behaviors.json": normalise.render_json(stores.raw_behaviors),
        "catalog/hardcoded_refs.yaml": normalise.render_hardcoded_refs(
            stores.hardcoded_refs
        ),
        ".local/raw-verbatim.json": normalise.render_json(stores.verbatim),
    }
    for relative, rendered in produced.items():
        committed = (paths.ROOT / relative).read_bytes().decode("utf-8")
        assert committed == rendered, f"{relative} does not reproduce from a re-run"
