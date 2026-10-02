"""The corpus-derived packs -- tasks 8.1 to 8.4.

Five properties hold this module together, and each is a way the derivation could
report a set it did not earn.

**The bound is counted, not restated.** The requirement named nineteen reusable
rows against sixty-four `ideas_only` ones, and a derivation that printed those
two numbers would keep printing them after the corpus changed -- as it since has,
when the `fwartner` and `johnkoht` authors granted unrestricted reuse on
2026-10-02 and every row became reusable. The report counts them, and the count
is asserted here, so the derived set's size is a consequence of
`catalog/behaviors.yaml` and not of a constant.

**Every row is accounted for, emitted or skipped.** `accounted` is asserted
against the corpus's own size and against the corpus's own id set, because "it does
not silently cap its output" is only checkable when the report's rows can be
compared with the file's.

**No pack exists for an `ideas_only` row.** Asserted in both directions -- no
emitted pack names one, and every row skipped for that reason carries the status --
so a derivation that produced the right count by skipping the wrong rows fails.

**An emitted pack validates, and pins a file inside itself.** The pack is judged
by the same two authorities the shipped tree is judged by, and its `provides`
entry is resolved rather than read.

**The tree on disk is the derivation's output.** The committed files are compared
byte for byte against the report that renders them, so "the documented command
reproduces the shipped tree" is a check and not a sentence in a document.

The four tests that need a corpus the committed one does not hold -- an
`ideas_only` row that is otherwise emittable, a reusable row licensed
`no_licence`, a row whose expression names a banned service -- write a corpus into
a temporary directory and point `tools.catalog.paths.CATALOG` at it. They are the
tests that make three of the six skip reasons reachable rather than declared.

**The act decides which slot the pack commands.** A derived behaviour's `slots`
are the row's own list and the slots its acts name, and the file the pack pins
targets those slots. Both come from `catalog/slots.yaml`'s `accepts_domains`,
which is the authority the vocabulary check already compares every hand-written
slot against -- so `light.turn_off` happening through `light_group` is the
vocabulary's claim and not a second list kept in the derivation.
"""

from __future__ import annotations

import shutil
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from engine import manifest as engine_manifest
from engine import sandbox
from engine.vocabulary import Vocabulary, load_manifest_artifacts
from openhouse import pack_verbs
from tools.catalog import examples as catalog_examples
from tools.catalog import paths
from tools.catalog.errors import Report

ROOT = paths.ROOT
DERIVED = ROOT / "packs" / "derived"
OFFICIAL = ROOT / "packs" / "official"

#: A row the committed corpus grounds a behaviour from, and the module it lands
#: in. Named rather than searched for, so the tests about a module's shape -- its
#: `provides` entries, its `derives_from` -- agree on one exemplar. The module is
#: the row's own namespace, because a pack is a family of rows and not a row.
SOURCE_ROW = "lighting.outdoor_landscape"
SOURCE_BEHAVIOUR = "outdoor_landscape"
DERIVED_NAME = "lighting"

#: The catalog files a fixture project has to carry beside its own corpus: the
#: slot vocabulary, the room types the house scope is read off, and the policy
#: that bans and flags services. Copied rather than rewritten so a fixture cannot
#: agree with a stale copy of a fact the project publishes.
_CATALOG = ("slots.yaml", "room_types.yaml", "pack-policy.yaml")


def _corpus(root: Path, rows: list[dict[str, object]]) -> Path:
    """Write a corpus under a project root, replacing whatever was there."""
    (root / "catalog").mkdir(parents=True, exist_ok=True)
    (root / "catalog" / "behaviors.yaml").write_text(
        yaml.safe_dump({"behaviors": rows}, sort_keys=False), encoding="utf-8"
    )
    return root


def _row(identifier: str, **overrides: object) -> dict[str, object]:
    """One corpus row, with the fields the derivation reads and nothing else."""
    row: dict[str, object] = {
        "id": identifier,
        "name": f"The {identifier} behaviour",
        "description": f"{identifier}, as the corpus records it.",
        "category": "lighting",
        "scope": "room",
        "required_slots": ["light_group"],
        "optional_slots": [],
        "concept": f"The idea behind {identifier}.",
        "expression": None,
        "source_repos": ["fixture"],
        "obligations": ["attribution"],
        "license": "mit",
        "reuse_status": "reusable",
        "classification": "module_candidate",
    }
    row.update(overrides)
    return row


@pytest.fixture
def corpus(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A project whose corpus is the test's and whose other artifacts are the real ones.

    The whole root moves, not just `catalog/`, because a corpus row the
    validator has never heard of is an `unknown_source_row` -- the derivation and
    the gate that judges its output have to be reading one file, and a fixture
    that moved only one of them would test a pack against a corpus it did not
    come from. Move the root and copy the artifacts a root is judged by, and the
    split the production code has -- one corpus, one gate -- is the split the
    fixture has too.
    """
    shutil.copytree(ROOT / "schemas", tmp_path / "schemas")
    (tmp_path / "catalog").mkdir()
    for name in _CATALOG:
        shutil.copy2(ROOT / "catalog" / name, tmp_path / "catalog" / name)
    (tmp_path / "packs" / "official").mkdir(parents=True)
    shutil.copy2(
        OFFICIAL / "HANDWRITTEN", tmp_path / "packs" / "official" / "HANDWRITTEN"
    )
    monkeypatch.setattr(paths, "ROOT", tmp_path, raising=True)
    monkeypatch.setattr(paths, "CATALOG", tmp_path / "catalog", raising=True)
    monkeypatch.setattr(paths, "SCHEMAS", tmp_path / "schemas", raising=True)
    monkeypatch.setattr(
        paths, "SCHEMA_CATALOG", tmp_path / "schemas" / "catalog", raising=True
    )
    monkeypatch.setattr(paths, "PACKS", tmp_path / "packs", raising=True)
    return _corpus(tmp_path, [])


# -- 8.1: the derivation over the corpus ------------------------------------


def test_the_derived_set_is_every_row_the_corpus_can_ground() -> None:
    """The committed corpus grounds fifty-nine behaviours in ten modules.

    The report counts both numbers, so neither the derived set's size nor the
    shape it is gathered into is a constant this module asserts on its own.

    The corpus held sixty-four `ideas_only` rows against nineteen reusable ones
    until the `fwartner` and `johnkoht` authors granted unrestricted reuse on
    2026-10-02; every row is now reusable and carries an authored `expression`.
    Two gates still stand between a reusable row and a pack, and both are the
    corpus's own word about the row rather than a rule this derivation invented:
    one row is classified `discard`, which the corpus's own requirement keeps for
    the audit and excludes from the shipped set, and twenty-three do nothing but
    flip an `input_boolean` or an `input_select` -- a helper, which is state a
    house keeps rather than a device it has, so the row is the machinery behind a
    state the engine already models for itself and there is nothing to install.

    Falsified by a derivation that skips a row it could ground -- `reasons()`
    would then carry a non-zero count while `accounted` still summed to the
    corpus -- and by a report that hard-codes the bound, since the counts are
    read off the corpus and asserted against it.
    """
    report = pack_verbs.derive_packs(write=False)

    assert report.bound.rows == 83
    assert report.bound.reusable == 83
    assert report.bound.ideas_only == 0
    assert report.bound.licences == {"apache_2_0": 24, "mit": 30, "public_domain": 29}

    rows = [
        row["id"]
        for row in yaml.safe_load(
            (ROOT / "catalog" / "behaviors.yaml").read_text(encoding="utf-8")
        )["behaviors"]
    ]
    emitted = [identifier for pack in report.emitted for identifier in pack.rows]
    skipped = {row.row for row in report.skipped}

    assert report.reasons() == {
        "ideas_only": 0,
        "licence_too_restrictive": 0,
        "discarded": 1,
        "helper_act": 23,
        "no_expression": 0,
        "refused": 0,
    }
    # Emitted rows are the corpus's rows less the ones a gate took, so the set's
    # size is read off the corpus rather than pinned -- and the order is the
    # families' because that is what a report and a Store listing show, with each
    # family's own rows in the corpus's order.
    assert sorted(emitted) == sorted(row for row in rows if row not in skipped)
    for pack in report.emitted:
        assert list(pack.rows) == [row for row in rows if row in set(pack.rows)]
    assert len(emitted) == 59
    # And the rows are gathered into one module per id namespace, which is the
    # shape a Store card has: ten ideas, not fifty-nine cards.
    assert len(report.emitted) == 10
    assert sorted(pack.name for pack in report.emitted) == [
        "cleaning",
        "climate",
        "laundry",
        "lighting",
        "media",
        "modes",
        "notifications",
        "presence",
        "security",
        "system",
    ]


def test_the_report_names_every_row_and_caps_nothing() -> None:
    """Every corpus row is emitted or skipped, and none is left out.

    Falsified by a report that stops at the first few rows, or that counts a
    total without naming the rows behind it: `accounted` compared against the
    corpus's own id set is what makes "it does not silently cap its output" a
    check rather than a claim.
    """
    report = pack_verbs.derive_packs(write=False)
    rows = {
        row["id"]
        for row in yaml.safe_load(
            (ROOT / "catalog" / "behaviors.yaml").read_text(encoding="utf-8")
        )["behaviors"]
    }
    named = {identifier for pack in report.emitted for identifier in pack.rows} | {
        row.row for row in report.skipped
    }

    assert report.accounted == report.bound.rows == len(rows)
    assert named == rows


def test_no_pack_is_derived_from_an_ideas_only_row() -> None:
    """The gate holds in both directions, over the committed corpus.

    Falsified by a derivation that reached the right set size by the wrong gate:
    an emitted pack naming a row whose status is not `reusable`, or a row called
    `ideas_only` that does not carry that status, both fail here.
    """
    rows = {
        row["id"]: row
        for row in yaml.safe_load(
            (ROOT / "catalog" / "behaviors.yaml").read_text(encoding="utf-8")
        )["behaviors"]
    }
    report = pack_verbs.derive_packs(write=False)

    for pack in report.emitted:
        for identifier in pack.rows:
            assert rows[identifier]["reuse_status"] == "reusable"
        document = yaml.safe_load(pack.manifest_text)
        assert document["derives_from"] == list(pack.rows)
    for skipped in report.skipped:
        if skipped.reason == "ideas_only":
            assert rows[skipped.row]["reuse_status"] == "ideas_only"


def test_an_emitted_pack_validates_under_the_current_schema() -> None:
    """The shipped derived tree is valid, and it is valid because it validates.

    Falsified by a derivation that trusts its own renderer: the pack is judged by
    `validate_directory`, which runs the schema and the sandbox, and by
    `validate_manifest`, so a pack whose `provides` path dangled or whose licence
    outran its row's would fail here rather than in a reader's hands.
    """
    report = pack_verbs.validate_directory(DERIVED)
    assert report.ok
    assert report.checked == len(pack_verbs.derive_packs(write=False).emitted)
    assert report.unreadable == ()

    artifacts = load_manifest_artifacts(ROOT)
    vocabulary = Vocabulary.load(ROOT)
    for path in sorted(DERIVED.glob("*.yaml")):
        manifest = engine_manifest.load_manifest(path)
        assert engine_manifest.validate_manifest(manifest, artifacts, vocabulary).ok, (
            path.name
        )


def test_the_emitted_pack_pins_a_file_inside_its_own_directory() -> None:
    """The module confers one file per behaviour, each inside the module's directory.

    Falsified by a `provides` path that resolves anywhere else -- the escaping
    path the sandbox refuses -- or by a pinned file whose class the entry
    misdeclares, and by a pinned file that does not exist at all. One pinned file
    per behaviour is the claim: a module that conferred one file for fifteen
    behaviours would install fifteen switches over one automation.
    """
    manifest = engine_manifest.load_manifest(DERIVED / f"{DERIVED_NAME}.yaml")
    entries = manifest.document["provides"]
    behaviours = manifest.document["behaviours"]
    assert isinstance(entries, list)
    assert isinstance(behaviours, list)
    assert len(entries) == len(behaviours) == len(manifest.document["derives_from"])

    names: list[str] = []
    for entry in entries:
        assert isinstance(entry, dict)
        pinned = ROOT / str(entry["path"])
        assert pinned.is_file()
        assert pinned.parent == DERIVED / DERIVED_NAME
        assert sandbox.file_class(pinned) == entry["class"] == "automation"
        names.append(pinned.stem)
    assert SOURCE_BEHAVIOUR in names


def test_the_emitted_pack_declares_every_row_it_derives_from() -> None:
    """A module's licence is one every row's grant supports, and it says which.

    Falsified by a module whose `derives_from` names nothing, or names a row it
    did not come from, or claims a licence narrower than any one of its sources
    grants -- the three claims `engine.manifest`'s derivation gate exists to
    judge. The licence is checked against *every* row rather than the first,
    because a module is the widest of its rows' grants and a check that read one
    row would pass a module that borrowed another row's narrower terms.
    """
    artifacts = load_manifest_artifacts(ROOT)
    manifest = engine_manifest.load_manifest(DERIVED / f"{DERIVED_NAME}.yaml")
    declared = manifest.document["derives_from"]
    assert isinstance(declared, list)

    assert SOURCE_ROW in declared
    for identifier in declared:
        row = artifacts.corpus[str(identifier)]
        assert not artifacts.licences.more_restrictive(
            str(manifest.document["license"]), row.license
        )


# -- 8.2 and 8.4: the report, and reproducibility ---------------------------


def test_two_runs_produce_byte_identical_packs() -> None:
    """The same corpus twice renders the same bytes, so a diff shows a change.

    Falsified by a renderer that reads a clock, a temporary path or a dict whose
    order came from a set: two runs would then differ by something that is not a
    change to the corpus, and the derived tree would be unreviewable.
    """
    first = pack_verbs.derive_packs(write=False)
    second = pack_verbs.derive_packs(write=False)

    assert [(pack.name, pack.manifest_text) for pack in first.emitted] == [
        (pack.name, pack.manifest_text) for pack in second.emitted
    ]
    assert [pack.artefacts for pack in first.emitted] == [
        pack.artefacts for pack in second.emitted
    ]


def test_the_committed_tree_is_the_derivations_own_output() -> None:
    """The files under `packs/derived/` are the bytes the derivation renders.

    Falsified by a tree edited by hand after it was generated -- the one way a
    derived pack stops being reproducible -- and by a derivation whose output is
    not what was committed.
    """
    report = pack_verbs.derive_packs(write=False)
    written = {
        path.relative_to(ROOT).as_posix(): path.read_text(encoding="utf-8")
        for path in DERIVED.rglob("*.yaml")
    }
    rendered = {pack.manifest_path: pack.manifest_text for pack in report.emitted}
    for pack in report.emitted:
        rendered.update(pack.artefacts)

    assert written == rendered


def test_the_derivation_writes_only_inside_its_destination(tmp_path: Path) -> None:
    """A run writes the packs it reports, and every `provides` path resolves.

    Falsified by a writer that emits a manifest and not the file it pins: the
    requirement says the emitted files are the ones its own `provides` entries
    name, so the tree is written into a temporary root and then validated there,
    with that root as the tree the paths are relative to.
    """
    destination = tmp_path / "packs" / "derived"
    report = pack_verbs.derive_packs(destination, root=tmp_path)

    assert report.written
    for pack in report.emitted:
        assert (tmp_path / pack.manifest_path).is_file()
        for path, _ in pack.artefacts:
            assert (tmp_path / path).is_file()
    assert pack_verbs.validate_directory(destination, root=tmp_path).ok
    assert not (tmp_path / "packs" / "official").exists()


def test_the_derivation_sweeps_its_own_earlier_output_and_nothing_else(
    tmp_path: Path,
) -> None:
    """A stale generated file goes; a person's note beside it stays.

    Falsified by a sweep that deletes by extension, which would remove a file the
    derivation never wrote -- and by no sweep at all, which would leave a pack
    whose row has left the corpus in the tree nothing accounts for.
    """
    destination = tmp_path / "packs" / "derived"
    destination.mkdir(parents=True)
    stale = destination / "gone_since_last_run.yaml"
    stale.write_text(
        f"{pack_verbs.GENERATED_MARKER}\nfrom a row the corpus no longer holds.\n",
        encoding="utf-8",
    )
    note = destination / "notes.md"
    note.write_text("A person wrote this.\n", encoding="utf-8")

    report = pack_verbs.derive_packs(destination, root=tmp_path)

    assert not stale.exists()
    assert note.is_file()
    assert sorted(path.name for path in destination.glob("*.yaml")) == sorted(
        f"{pack.name}.yaml" for pack in report.emitted
    )


def test_a_destination_outside_the_tree_is_a_usage_error(tmp_path: Path) -> None:
    """A destination `provides` could not name is the caller's mistake.

    Falsified by a derivation that writes the packs anyway: a `provides` path is
    a path within a tree, so a pack outside the tree it is named in could not
    name the file it confers, and the failure would surface later as a dangling
    path rather than here as a mistyped argument.
    """
    with pytest.raises(pack_verbs.UsageError) as raised:
        pack_verbs.derive_packs(tmp_path, root=ROOT)
    assert raised.value.about == str(tmp_path)
    assert "not inside" in raised.value.reason


def test_an_empty_corpus_is_a_usage_error(corpus: Path) -> None:
    """A corpus that holds no rows is not a derivation that produced nothing.

    Falsified by reading an absent or empty corpus as "nothing to derive": the
    two are the same answer to a caller, and one of them means a file nothing
    opened.
    """
    with pytest.raises(pack_verbs.UsageError):
        pack_verbs.derive_packs(write=False)


# -- 8.3: generated packs and the marker ------------------------------------


def test_no_derived_file_is_under_the_official_tree() -> None:
    """Generated packs live in a tree of their own, and no official file is one.

    Falsified by a derived pack written into `packs/official/`, which is the
    directory `HANDWRITTEN` states is the work of people -- and by a shipped
    official file that carries the derivation's marker, which would be a
    generated file hiding under a hand-written claim.
    """
    marker = pack_verbs.GENERATED_MARKER
    assert DERIVED != OFFICIAL
    for path in DERIVED.rglob("*.yaml"):
        assert OFFICIAL not in path.parents
        assert path.read_text(encoding="utf-8").startswith(marker)
    for path in OFFICIAL.glob("*.yaml"):
        assert not path.read_text(encoding="utf-8").startswith(marker)


def test_the_marker_rule_still_holds_in_both_directions() -> None:
    """`handwritten-examples` is clean over the shipped tree, as it was before.

    Falsified by a derived file added to `packs/official/` without a marker
    line, and by a marker line whose file was deleted -- the two ways the
    allowlist rots, and the check the requirement says this phase must not
    weaken.
    """
    report = Report()
    catalog_examples.check_examples(report)
    assert report.ok, report.render()


def test_a_derived_pack_placed_under_the_official_tree_is_refused() -> None:
    """A derived pack claimed as hand-written is a false claim about provenance.

    Falsified by a validator that let the two clauses coexist: `derives_from`
    says a person did not write the file, and the marker says a person did, so a
    manifest carrying both -- at the one path that makes the marker apply -- is
    refused under its own reason rather than under a schema failure.
    """
    document = yaml.safe_load(
        (DERIVED / f"{DERIVED_NAME}.yaml").read_text(encoding="utf-8")
    )
    artifacts = load_manifest_artifacts(ROOT)
    placed = f"packs/official/{DERIVED_NAME}.yaml"
    result = engine_manifest.validate_manifest(
        engine_manifest.Manifest(
            path=OFFICIAL / f"{DERIVED_NAME}.yaml", document=document
        ),
        replace(artifacts, handwritten=artifacts.handwritten | {placed}),
        Vocabulary.load(ROOT),
    )

    assert [failure.reason for failure in result.failures] == ["handwritten_derivation"]


# -- the gate, exercised on corpora the committed one does not hold ---------


def test_a_reusable_row_licensed_no_licence_is_skipped_for_the_licence(
    corpus: Path,
) -> None:
    """A row whose licence grants nothing grounds no pack, whatever its status.

    Falsified by a derivation that gates on `reuse_status` alone: a corpus edited
    by hand can carry a reusable row under `no_licence`, and a pack derived from
    it would claim a grant the source does not make.
    """
    _corpus(
        corpus,
        [
            _row(
                "fixture.reusable_but_ungranted",
                license="no_licence",
                expression={"trigger": ["state"], "action": ["light.turn_on"]},
            )
        ],
    )
    report = pack_verbs.derive_packs(write=False)

    assert report.emitted == ()
    assert [(row.row, row.reason) for row in report.skipped] == [
        ("fixture.reusable_but_ungranted", "licence_too_restrictive")
    ]


def test_a_row_whose_expression_names_a_banned_service_is_refused(
    corpus: Path,
) -> None:
    """A row the derivation cannot turn into a valid pack is named, not written.

    Falsified by a derivation that trusts the corpus: `homeassistant.restart` is
    a service the corpus could name and the policy bans, so a pack built from it
    would be refused by `validate` the moment it reached the shipped tree. The
    refusal is reported against the row instead.
    """
    _corpus(
        corpus,
        [
            _row(
                "fixture.banned",
                expression={"trigger": ["state"], "action": ["homeassistant.restart"]},
            )
        ],
    )
    report = pack_verbs.derive_packs(write=False)

    assert report.emitted == ()
    assert [row.reason for row in report.skipped] == ["refused"]
    assert "homeassistant.restart" in report.skipped[0].message


def test_an_ideas_only_row_is_skipped_before_anything_else_is_asked(
    corpus: Path,
) -> None:
    """The status gate comes first, so the reason a row is skipped is its own.

    Falsified by a derivation that checked the expression or the licence first: a
    row that is both `ideas_only` and expressionless would then be reported under
    a reason that is not the one the corpus states, and the report's count of the
    skipped majority would be wrong.
    """
    _corpus(corpus, [_row("fixture.ideas", reuse_status="ideas_only", expression=None)])
    report = pack_verbs.derive_packs(write=False)

    assert [(row.row, row.reason) for row in report.skipped] == [
        ("fixture.ideas", "ideas_only")
    ]
    assert report.bound.ideas_only == 1
    assert report.bound.reusable == 0


def test_an_expression_a_single_behaviour_clause_cannot_hold_is_skipped(
    corpus: Path,
) -> None:
    """An expression naming two triggers is not truncated into a pack.

    Falsified by a derivation that took the first term of each axis and dropped
    the rest: the manifest's behaviour clause carries one trigger and one
    condition, so a row naming two cannot be reproduced whole, and a pack built
    from half of it would silently be a different behaviour.
    """
    _corpus(
        corpus,
        [
            _row(
                "fixture.wide",
                expression={
                    "trigger": ["sun", "state"],
                    "action": ["light.turn_on"],
                },
            )
        ],
    )
    report = pack_verbs.derive_packs(write=False)

    assert report.emitted == ()
    assert [row.reason for row in report.skipped] == ["no_expression"]


def test_a_reproducible_row_is_emitted_with_the_terms_its_expression_names(
    corpus: Path,
) -> None:
    """The happy path over a corpus of one, so the gate is not only a refusal.

    Falsified by a derivation that emits nothing at all -- a set that is empty
    for every corpus passes the gates above and grounds nothing -- and by one
    that leaves the row's own terms out of the pack it builds.
    """
    _corpus(
        corpus,
        [
            _row(
                "fixture.lights",
                expression={
                    "trigger": ["sun"],
                    "condition": ["state"],
                    "action": ["light.turn_on"],
                },
                optional_slots=["ambient_light_sensor"],
            )
        ],
    )
    report = pack_verbs.derive_packs(write=False)

    assert report.ok
    [pack] = report.emitted
    document = yaml.safe_load(pack.manifest_text)
    assert document["name"] == "fixture"
    assert document["license"] == "mit"
    assert document["kind"] == "module"
    # Nothing is required and everything the module can read is optional, so a
    # room missing the light still installs the module and the behaviour that
    # wanted it is inert rather than the module being uninstallable.
    assert document["requires_slots"] == []
    assert document["optional_slots"] == ["light_group", "ambient_light_sensor"]
    assert document["derives_from"] == ["fixture.lights"]
    assert document["behaviours"] == [
        {
            "name": "lights",
            "scope": "room",
            "trigger": "sun",
            "condition": "state",
            "action": "service",
            "services": ["light.turn_on"],
            "slots": ["light_group"],
        }
    ]
