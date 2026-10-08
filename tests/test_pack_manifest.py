"""The manifest validator -- tasks 4.1 to 4.6.

Five authorities judge a manifest and this module checks each of them, plus the
two properties the requirements are written around. The first is that the schema's
three reclassifications are real distinctions and not wording: an unpublished
vocabulary term, an unpublishable licence code and a retired clause are three
different remedies, so each is asserted by its own reason and its own message
rather than as "it failed". The second is the order: the schema runs first and
nothing else runs if it fails, which is asserted by showing that a document with
no `engine_api` is reported once, as a schema failure, and never as an
incompatibility.

The corpus and the licence vocabulary are the committed ones, handed in through
`engine/vocabulary.py`, so a test that would pass against a hand-built corpus
fails here -- which is the point, because "the corpus is the source of the status,
not the manifest" is only true if the corpus is the committed file. One row is
spliced into that corpus for the tests that need a withholding source: no
committed row is `ideas_only` since the `fwartner` and `johnkoht` authors granted
unrestricted reuse on 2026-10-02, and the `ideas_only_source` refusal would
otherwise be unreachable.

`test_every_reason_is_reachable` is the section to check first if you doubt the
suite covers the module: it produces one failure for every reason `REASONS`
declares, so a reason no document can reach fails here rather than sitting in the
tuple looking like coverage.
"""

from __future__ import annotations

import ast
import re
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
import yaml

from engine import manifest, vocabulary
from tools.catalog import paths

from .conftest import write

if TYPE_CHECKING:
    from collections.abc import Mapping

ROOT = paths.ROOT
MODULE = ROOT / "engine" / "manifest.py"
EXAMPLE = ROOT / "packs" / "official" / "example-pack.yaml"
SCHEMA = ROOT / "schemas" / "pack-manifest" / "1.2.0.json"

#: The five kinds `1.2.0` closes `kind` to.
KINDS = ("module", "behavior", "room-template", "house-template", "pack-set")


@pytest.fixture
def artifacts(real_root: Path) -> vocabulary.ManifestArtifacts:
    """The four authorities, read from the committed tree."""
    return vocabulary.load_manifest_artifacts(real_root)


@pytest.fixture
def inspected(
    real_root: Path,
) -> tuple[vocabulary.ManifestArtifacts, vocabulary.Vocabulary]:
    """The artifacts and the engine's own vocabulary, which carries the API version."""
    return (
        vocabulary.load_manifest_artifacts(real_root),
        vocabulary.Vocabulary.load(real_root),
    )


def _example() -> dict[str, object]:
    """The shipped example pack, as a mutable document."""
    loaded = yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return dict(loaded)


#: A corpus row that withholds. The committed corpus held sixty-four of these
#: until the `fwartner` and `johnkoht` authors granted unrestricted reuse on
#: 2026-10-02, which left no shipped row that reaches `ideas_only_source`.
WITHHOLDING_ROW = vocabulary.CorpusRow(
    id="fixture.withheld", reuse_status="ideas_only", license="no_licence"
)


def _withholding(
    artifacts: vocabulary.ManifestArtifacts,
) -> vocabulary.ManifestArtifacts:
    """The four authorities with one withholding row spliced into the corpus.

    The corpus is the committed one plus this row: the gate reads the corpus and
    nothing else, so a row the committed file no longer holds is the only way to
    reach the refusal that row's status produces.
    """
    return replace(
        artifacts,
        corpus={**artifacts.corpus, WITHHOLDING_ROW.id: WITHHOLDING_ROW},
    )


def _validate(
    tmp_path: Path,
    artifacts: vocabulary.ManifestArtifacts,
    engine: vocabulary.Vocabulary,
    document: Mapping[str, object],
    name: str = "built.yaml",
) -> manifest.ManifestResult:
    """Write a document into a fixture directory and validate it."""
    path = write(tmp_path, name, yaml.safe_dump(dict(document), sort_keys=False))
    return manifest.validate_manifest(manifest.load_manifest(path), artifacts, engine)


def _reasons(result: manifest.ManifestResult) -> list[str]:
    return [failure.reason for failure in result.failures]


def _messages(result: manifest.ManifestResult) -> str:
    return " | ".join(failure.message for failure in result.failures)


# --------------------------------------------------------------------------
# 4.1 -- the current version only, and the failing path named
# --------------------------------------------------------------------------


def test_the_shipped_example_validates(inspected: tuple, tmp_path: Path) -> None:
    """The one hand-written pack, judged by all four authorities."""
    artifacts, engine = inspected
    result = manifest.validate_manifest(
        manifest.load_manifest(EXAMPLE), artifacts, engine
    )
    assert result.ok, _messages(result)


def test_the_shipped_example_declares_one_of_the_five_kinds() -> None:
    assert _example()["kind"] in KINDS


def test_the_example_is_listed_as_handwritten(
    artifacts: vocabulary.ManifestArtifacts,
) -> None:
    """The marker names it, and it carries no derivation clause to contradict."""
    relative = EXAMPLE.relative_to(ROOT).as_posix()
    assert relative in artifacts.handwritten
    assert "derives_from" not in _example()


def test_a_document_valid_under_the_previous_version_is_refused(
    inspected: tuple, tmp_path: Path
) -> None:
    """`min_engine_version` is named as retired, not as a typo.

    The document here is what `1.1.0` accepted: no `engine_api`, no `license`, no
    `i18n`, and the lower-bounded clause that `1.2.0` retired. The failure has to
    send the author to a migration rather than to a spell-checker, so it names the
    clause, the range that replaced it and the version that refused it.
    """
    artifacts, engine = inspected
    document = _example()
    for clause in ("engine_api", "license", "i18n"):
        document.pop(clause)
    document["min_engine_version"] = "1.0.0"

    result = _validate(tmp_path, artifacts, engine, document, "retired.yaml")

    assert not result.ok
    assert "retired_clause" in _reasons(result)
    assert "min_engine_version" in _messages(result)
    assert "engine_api" in _messages(result)
    assert artifacts.schema.version in _messages(result)
    assert _reasons(result).count("retired_clause") == 1


def test_a_structural_failure_names_its_path_and_its_constraint(
    inspected: tuple, tmp_path: Path
) -> None:
    artifacts, engine = inspected
    document = _example()
    document.pop("description")

    result = _validate(tmp_path, artifacts, engine, document, "missing.yaml")

    assert _reasons(result) == ["schema"]
    assert result.failures[0].path == "<document>"
    assert "required" in result.failures[0].message


def test_a_broken_clause_is_located_by_its_instance_path(
    inspected: tuple, tmp_path: Path
) -> None:
    """A failure below the root names where it is, not just what it is."""
    artifacts, engine = inspected
    document = _example()
    document["version"] = "1.2"

    result = _validate(tmp_path, artifacts, engine, document, "version.yaml")

    assert _reasons(result) == ["schema"]
    assert result.failures[0].path == "version"


# --------------------------------------------------------------------------
# 4.2 -- the engine API range, with its own reason
# --------------------------------------------------------------------------


def test_a_range_excluding_the_engine_is_refused(
    inspected: tuple, tmp_path: Path
) -> None:
    artifacts, engine = inspected
    document = _example()
    document["engine_api"] = ">=2.0.0 <3.0.0"

    result = _validate(tmp_path, artifacts, engine, document, "api.yaml")

    assert _reasons(result) == ["engine_api_mismatch"]
    assert ">=2.0.0 <3.0.0" in _messages(result)
    assert engine.engine_api_version in _messages(result)


def test_a_range_containing_the_engine_is_admitted(
    inspected: tuple, tmp_path: Path
) -> None:
    """The other half of the requirement, which a check that refused all would pass."""
    artifacts, engine = inspected
    document = _example()
    document["engine_api"] = f">={engine.engine_api_version}"

    assert _validate(tmp_path, artifacts, engine, document, "admit.yaml").ok


def test_the_range_check_is_distinct_from_a_schema_failure(
    inspected: tuple, tmp_path: Path
) -> None:
    """A malformed range fails validation, so the check is never reached.

    This is `pack-manifest`'s scenario stated exactly: the failure is a schema
    failure and *not* an incompatibility, and the single failure is the evidence
    that the range check did not also run.
    """
    artifacts, engine = inspected
    document = _example()
    document["engine_api"] = "two-ish"

    result = _validate(tmp_path, artifacts, engine, document, "malformed.yaml")

    assert _reasons(result) == ["schema"]
    assert result.failures[0].path == "engine_api"


def test_a_missing_range_fails_validation_rather_than_the_check(
    inspected: tuple, tmp_path: Path
) -> None:
    artifacts, engine = inspected
    document = _example()
    document.pop("engine_api")

    result = _validate(tmp_path, artifacts, engine, document, "absent.yaml")

    assert _reasons(result) == ["schema"]
    assert "engine_api_mismatch" not in _reasons(result)


def test_the_range_check_is_distinct_from_a_dependency_failure(
    inspected: tuple, tmp_path: Path
) -> None:
    """Two refusals of the same *shape* -- a range -- with two reasons."""
    artifacts, engine = inspected
    document = _example()
    document["engine_api"] = ">=2.0.0 <3.0.0"
    document["dependencies"] = [{"name": "evening_lights", "range": ">=1.0.0 <2.0.0"}]
    document["name"] = "smoke_watch"

    result = _validate(tmp_path, artifacts, engine, document, "two.yaml")

    assert _reasons(result) == ["engine_api_mismatch"]
    assert _reasons(result) != ["self_dependency"]


# --------------------------------------------------------------------------
# 4.3 -- the licence vocabulary and the derivation gate
# --------------------------------------------------------------------------


def test_a_published_code_validates_and_carries_its_spdx_identifier(
    artifacts: vocabulary.ManifestArtifacts,
) -> None:
    for code in artifacts.licences.codes:
        assert artifacts.licences.spdx[code]


def test_the_published_codes_are_the_five_in_their_order(
    artifacts: vocabulary.ManifestArtifacts,
) -> None:
    assert artifacts.licences.codes == (
        "public_domain",
        "mit",
        "apache_2_0",
        "cc_by_nc_sa",
        "no_licence",
    )
    assert [artifacts.licences.rank(code) for code in artifacts.licences.codes] == [
        0,
        1,
        2,
        3,
        4,
    ]


def test_the_order_is_the_one_the_licence_is_compared_in(
    artifacts: vocabulary.ManifestArtifacts,
) -> None:
    assert artifacts.licences.more_restrictive("cc_by_nc_sa", "mit")
    assert not artifacts.licences.more_restrictive("mit", "cc_by_nc_sa")
    assert not artifacts.licences.more_restrictive("mit", "mit")


@pytest.mark.parametrize("code", ["MIT", "Free-To-Use-4U", "apache2", "GPL-3.0"])
def test_a_code_outside_the_enum_is_refused_naming_it(
    inspected: tuple, tmp_path: Path, code: str
) -> None:
    artifacts, engine = inspected
    document = _example()
    document["license"] = code

    result = _validate(tmp_path, artifacts, engine, document, "licence.yaml")

    assert _reasons(result) == ["unknown_licence"]
    assert repr(code) in _messages(result)
    assert result.failures[0].path == "license"
    for published in artifacts.licences.codes:
        assert published in _messages(result)


def test_a_pack_over_reusable_rows_is_accepted(
    inspected: tuple, tmp_path: Path
) -> None:
    artifacts, engine = inspected
    reusable = sorted(
        row.id for row in artifacts.corpus.values() if row.reuse_status == "reusable"
    )
    document = _example()
    document["derives_from"] = reusable[:2]
    document["license"] = "public_domain"

    assert _validate(tmp_path, artifacts, engine, document, "derived.yaml").ok


def test_a_pack_over_an_ideas_only_row_is_refused(
    inspected: tuple, tmp_path: Path
) -> None:
    """The refusal a withholding row earns, on a corpus built to hold one.

    Spliced in because no committed row is `ideas_only` any more, and the gate
    reads the corpus rather than any list beside it.
    """
    artifacts, engine = inspected
    artifacts = _withholding(artifacts)
    document = _example()
    document["derives_from"] = [WITHHOLDING_ROW.id]

    result = _validate(tmp_path, artifacts, engine, document, "ideas.yaml")

    assert _reasons(result) == ["ideas_only_source"]
    assert WITHHOLDING_ROW.id in _messages(result)
    assert "ideas_only" in _messages(result)
    assert "hand-written" in result.failures[0].message


def test_the_corpus_is_the_source_of_the_status_not_the_manifest(
    inspected: tuple, tmp_path: Path
) -> None:
    """A manifest cannot assert a row is reusable; the corpus is the authority.

    There is no field a manifest could assert it in, which is the design: the
    gate reads `catalog/behaviors.yaml` and never the document's own account of
    its sources. So an attempt to carry one is refused by the schema's
    `additionalProperties: false` -- there is no clause to ignore -- and the
    corpus's own `reuse_status` is what a derivation is judged against. The
    withholding row is spliced in because no committed row carries that status
    any more.
    """
    artifacts, engine = inspected
    artifacts = _withholding(artifacts)
    document = _example()
    document["derives_from"] = [WITHHOLDING_ROW.id]
    document["reuse_status"] = "reusable"

    result = _validate(tmp_path, artifacts, engine, document, "assert.yaml")

    assert _reasons(result) == ["schema"]
    assert "reuse_status" in _messages(result)
    assert artifacts.corpus[WITHHOLDING_ROW.id].reuse_status == "ideas_only"


def test_a_misspelled_row_id_is_refused(inspected: tuple, tmp_path: Path) -> None:
    artifacts, engine = inspected
    document = _example()
    document["derives_from"] = ["lighting.no_such_row"]

    result = _validate(tmp_path, artifacts, engine, document, "unknown_row.yaml")

    assert _reasons(result) == ["unknown_source_row"]
    assert "lighting.no_such_row" in _messages(result)


def test_an_empty_derivation_clause_is_a_schema_failure(
    inspected: tuple, tmp_path: Path
) -> None:
    """An empty list is not a derivation, and the schema is where that is said."""
    artifacts, engine = inspected
    document = _example()
    document["derives_from"] = []

    result = _validate(tmp_path, artifacts, engine, document, "empty.yaml")

    assert _reasons(result) == ["schema"]
    assert result.failures[0].path == "derives_from"


def test_a_more_restrictive_licence_is_refused(
    inspected: tuple, tmp_path: Path
) -> None:
    artifacts, engine = inspected
    row = next(
        row
        for row in sorted(artifacts.corpus.values(), key=lambda row: row.id)
        if row.reuse_status == "reusable"
    )
    document = _example()
    document["derives_from"] = [row.id]
    document["license"] = "cc_by_nc_sa"

    result = _validate(tmp_path, artifacts, engine, document, "narrow.yaml")

    assert _reasons(result) == ["licence_too_restrictive"]
    assert "cc_by_nc_sa" in _messages(result)
    assert row.license in _messages(result)
    assert artifacts.licences.codes[0] in _messages(result)


def test_a_relaxed_licence_over_a_reusable_row_is_admitted(
    inspected: tuple, tmp_path: Path
) -> None:
    """`public_domain` is below every reusable row's code, so nothing is claimed."""
    artifacts, engine = inspected
    row = next(
        row
        for row in sorted(artifacts.corpus.values(), key=lambda row: row.id)
        if row.reuse_status == "reusable"
    )
    document = _example()
    document["derives_from"] = [row.id]
    document["license"] = "public_domain"

    assert _validate(tmp_path, artifacts, engine, document, "relaxed.yaml").ok


def test_a_handwritten_pack_may_not_carry_a_derivation_clause(
    inspected: tuple,
) -> None:
    """The marker, not the path: the same document elsewhere is accepted.

    The clause is refused on a *listed* pack, which is why the check is over the
    path the manifest was read from. A copy of the same document outside
    `packs/official/` is a file nobody claimed was hand-written, and it is judged
    by the corpus alone.
    """
    artifacts, engine = inspected
    row = next(
        row
        for row in sorted(artifacts.corpus.values(), key=lambda row: row.id)
        if row.reuse_status == "reusable"
    )
    document = _example()
    document["derives_from"] = [row.id]
    document["license"] = "public_domain"

    listed = manifest.validate_manifest(
        manifest.Manifest(path=EXAMPLE, document=document), artifacts, engine
    )
    assert _reasons(listed) == ["handwritten_derivation"]
    assert "HANDWRITTEN" not in _messages(listed)


# --------------------------------------------------------------------------
# 4.4 -- i18n: the default, the override, and the refusal between them
# --------------------------------------------------------------------------


def test_a_locale_without_an_override_falls_back_to_the_default() -> None:
    pack = manifest.Manifest(path=EXAMPLE, document=_example())
    assert dict(manifest.strings(pack, "fr")) == dict(manifest.strings(pack))


def test_an_override_wins_where_it_exists() -> None:
    pack = manifest.Manifest(path=EXAMPLE, document=_example())
    resolved = manifest.strings(pack, "de")
    assert resolved["motion_turns_on_light"] == "Bewegung schaltet das Licht ein"
    assert resolved["pack"] == manifest.strings(pack)["pack"]


def test_the_resolution_covers_the_two_clause_keys_and_each_behaviour() -> None:
    pack = manifest.Manifest(path=EXAMPLE, document=_example())
    resolved = manifest.strings(pack)
    assert {"pack", "description", "motion_turns_on_light"} <= set(resolved)


def test_a_name_with_no_default_is_refused(inspected: tuple, tmp_path: Path) -> None:
    artifacts, engine = inspected
    document = _example()
    document["i18n"] = {"default": {"pack": "x", "description": "y"}}

    result = _validate(tmp_path, artifacts, engine, document, "no_default.yaml")

    assert _reasons(result) == ["missing_default"]
    assert "motion_turns_on_light" in _messages(result)


def test_a_behaviour_with_no_name_needs_no_default(
    inspected: tuple, tmp_path: Path
) -> None:
    """A behaviour that declares no name declares no user-visible name."""
    artifacts, engine = inspected
    document = _example()
    document["behaviours"] = [
        {
            key: value
            for key, value in _example()["behaviours"][0].items()
            if key != "name"
        }
    ]
    document["i18n"] = {"default": {"pack": "x", "description": "y"}}

    assert _validate(tmp_path, artifacts, engine, document, "anon.yaml").ok


def test_an_override_without_a_default_is_refused(
    inspected: tuple, tmp_path: Path
) -> None:
    artifacts, engine = inspected
    document = _example()
    locales = dict(_example()["i18n"]["locales"])
    locales["fr"] = {"nosuchname": "x"}
    document["i18n"] = {**_example()["i18n"], "locales": locales}

    result = _validate(tmp_path, artifacts, engine, document, "orphan.yaml")

    assert _reasons(result) == ["override_without_default"]
    assert "nosuchname" in _messages(result)
    assert "fr" in _messages(result)


# --------------------------------------------------------------------------
# 4.5 -- the vocabulary is referenced, never restated
# --------------------------------------------------------------------------


@pytest.mark.parametrize("axis", ["trigger", "condition", "action"])
def test_an_unpublished_term_is_refused_naming_the_pack_and_the_term(
    inspected: tuple, tmp_path: Path, axis: str
) -> None:
    artifacts, engine = inspected
    document = _example()
    behaviour = dict(_example()["behaviours"][0])
    behaviour[axis] = "while_light_is_off"
    document["behaviours"] = [behaviour]

    result = _validate(tmp_path, artifacts, engine, document, f"{axis}.yaml")

    assert _reasons(result) == ["unknown_term"]
    assert "example_pack" in _messages(result)
    assert "while_light_is_off" in _messages(result)
    assert axis in _messages(result)
    assert result.failures[0].path == f"behaviours/0/{axis}"


@pytest.mark.parametrize("axis", ["trigger", "condition", "action"])
def test_the_schema_contributes_no_term_of_its_own(axis: str) -> None:
    """The clause is a `$ref` into the vocabulary and carries no enum of its own.

    A schema that listed the terms would be the second copy the requirement
    forbids -- and it would drift the first time the vocabulary published one.
    """
    schema = yaml.safe_load(SCHEMA.read_text(encoding="utf-8"))
    clause = schema["$defs"]["behaviour"]["properties"][axis]
    assert "enum" not in clause
    assert sorted(key for key in clause if key != "description") == ["$ref"]
    assert clause["$ref"].startswith(
        "https://open-house.invalid/schemas/behavior-vocabulary/"
    )


def test_every_axis_the_schema_references_is_published(
    real_root: Path,
) -> None:
    """The three axes resolve, and each publishes terms rather than an empty list."""
    published = vocabulary.load_behaviour_vocabulary(real_root)
    assert published.triggers and published.conditions and published.actions


# --------------------------------------------------------------------------
# 4.6 -- dependencies and conflicts
# --------------------------------------------------------------------------


def test_a_self_dependency_is_refused(inspected: tuple, tmp_path: Path) -> None:
    artifacts, engine = inspected
    document = _example()
    document["dependencies"] = [{"name": "example_pack", "range": ">=1.0.0 <2.0.0"}]

    result = _validate(tmp_path, artifacts, engine, document, "self.yaml")

    assert _reasons(result) == ["self_dependency"]
    assert "example_pack" in _messages(result)
    assert result.failures[0].path == "dependencies/0"


def test_a_dependency_on_another_pack_is_admitted(
    inspected: tuple, tmp_path: Path
) -> None:
    artifacts, engine = inspected
    document = _example()
    document["dependencies"] = [{"name": "guest_mode", "range": ">=1.2.0 <2.0.0"}]

    assert _validate(tmp_path, artifacts, engine, document, "other.yaml").ok


def test_a_conflict_declaration_is_admitted(inspected: tuple, tmp_path: Path) -> None:
    """Conflicts are parsed and validated; resolving them needs a house."""
    artifacts, engine = inspected
    document = _example()
    document["conflicts"] = [{"name": "legacy_lighting", "range": "1.x"}]

    assert _validate(tmp_path, artifacts, engine, document, "conflict.yaml").ok


def test_a_malformed_range_is_a_schema_failure_naming_the_range(
    inspected: tuple, tmp_path: Path
) -> None:
    """The range grammar is the schema's, and a bad range never reaches a check."""
    artifacts, engine = inspected
    document = _example()
    document["dependencies"] = [{"name": "guest_mode", "range": "two-ish"}]

    result = _validate(tmp_path, artifacts, engine, document, "range.yaml")

    assert _reasons(result) == ["schema"]
    assert result.failures[0].path == "dependencies/0/range"


def test_the_two_range_clauses_take_the_same_grammar() -> None:
    """One range syntax, published once on `engine_api` and on `reference.range`."""
    schema = yaml.safe_load(SCHEMA.read_text(encoding="utf-8"))
    engine_api = schema["properties"]["engine_api"]["pattern"]
    reference = schema["$defs"]["reference"]["properties"]["range"]["pattern"]
    assert engine_api == reference


# --------------------------------------------------------------------------
# The reason space, and the module's own statements
# --------------------------------------------------------------------------


def test_the_reasons_are_closed_and_distinct() -> None:
    assert len(set(manifest.REASONS)) == len(manifest.REASONS)
    assert len(manifest.REASONS) == 15


def test_a_failure_carries_three_facts_and_no_class_of_its_own() -> None:
    """A reason is the finest distinction this module draws; a face folds it."""
    assert sorted(manifest.Failure.__dataclass_fields__) == [
        "message",
        "path",
        "reason",
    ]


def test_every_reason_is_reachable(inspected: tuple, tmp_path: Path) -> None:
    """One failure for every reason the module declares.

    A reason no document can reach is a reason a caller handles for nothing, and
    one whose check was reordered away would otherwise pass this suite. The
    corpus is the committed one with a withholding row spliced in, because no
    shipped row is `ideas_only` since the `fwartner` and `johnkoht` authors
    granted unrestricted reuse on 2026-10-02, and `ideas_only_source` would
    otherwise be unreachable.
    """
    artifacts, engine = inspected
    artifacts = _withholding(artifacts)
    reusable = sorted(
        row.id for row in artifacts.corpus.values() if row.reuse_status == "reusable"
    )
    ideas = sorted(
        row.id for row in artifacts.corpus.values() if row.reuse_status == "ideas_only"
    )

    def reasons(document: dict[str, object], name: str) -> list[str]:
        return _reasons(_validate(tmp_path, artifacts, engine, document, name))

    retired = _example()
    for clause in ("engine_api", "license", "i18n"):
        retired.pop(clause)
    retired["min_engine_version"] = "1.0.0"

    broken = _example()
    broken.pop("description")

    mismatched = _example()
    mismatched["engine_api"] = ">=2.0.0 <3.0.0"

    late = _example()
    late["license"] = "MIT"

    unpublished = _example()
    unpublished["behaviours"] = [
        {**_example()["behaviours"][0], "action": "while_light_is_off"}
    ]

    selfdep = _example()
    selfdep["dependencies"] = [{"name": "example_pack", "range": "1.x"}]

    unknown_row = _example()
    unknown_row["derives_from"] = ["lighting.no_such_row"]

    ideas_row = _example()
    ideas_row["derives_from"] = ideas[:1]

    narrow = _example()
    narrow["derives_from"] = reusable[:1]
    narrow["license"] = "cc_by_nc_sa"

    orphan = _example()
    orphan["i18n"] = {
        **_example()["i18n"],
        "locales": {**_example()["i18n"]["locales"], "fr": {"nosuchname": "x"}},
    }

    # A name the document declares (`pack`) with no string in `i18n.default`:
    # the schema requires the block and its non-emptiness but cannot name the
    # keys a document will declare, so `missing_default` is reachable only here.
    defaultless = _example()
    defaultless["i18n"] = {
        **_example()["i18n"],
        "default": {
            key: value
            for key, value in _example()["i18n"]["default"].items()
            if key != "pack"
        },
    }

    # An option whose default is not of its own declared type: the comparison a
    # static schema cannot make, and the reason it is made here.
    misdefaulted = _example()
    misdefaulted["options"] = [
        {
            "key": "grace",
            "type": "integer",
            "default": "5",
            "title": "Grace",
        }
    ]

    # A behaviour waiting on a duration option the pack does not declare.
    unwaited = _example()
    unwaited["behaviours"] = [{**_example()["behaviours"][0], "for": "nosuchgrace"}]

    # A behaviour naming its own pack in `suppresses`: a module that could never
    # run, refused at validation because by evaluation time the only symptom
    # would be a module that is on and never acts.
    selfsuppressed = _example()
    selfsuppressed["behaviours"] = [
        {**_example()["behaviours"][0], "suppresses": ["example_pack"]}
    ]

    found: set[str] = set()
    found.update(reasons(retired, "r1.yaml"))
    found.update(reasons(broken, "r2.yaml"))
    found.update(reasons(mismatched, "r3.yaml"))
    found.update(reasons(late, "r4.yaml"))
    found.update(reasons(unpublished, "r5.yaml"))
    found.update(reasons(selfdep, "r6.yaml"))
    found.update(reasons(unknown_row, "r7.yaml"))
    found.update(reasons(ideas_row, "r8.yaml"))
    found.update(reasons(narrow, "r9.yaml"))
    found.update(reasons(orphan, "r11.yaml"))
    found.update(reasons(defaultless, "r12.yaml"))
    found.update(reasons(misdefaulted, "r13.yaml"))
    found.update(reasons(unwaited, "r14.yaml"))
    found.update(reasons(selfsuppressed, "r15.yaml"))
    found.update(
        _reasons(
            manifest.validate_manifest(
                manifest.Manifest(path=EXAMPLE, document=narrow), artifacts, engine
            )
        )
    )

    assert found == set(manifest.REASONS), sorted(set(manifest.REASONS) - found)


def test_a_file_that_is_not_a_manifest_is_not_a_pack_failure(
    tmp_path: Path,
) -> None:
    """A usage error and a pack failure are different, and `pack-cli` says so."""
    path = write(tmp_path, "broken.yaml", "just a string\n")
    with pytest.raises(manifest.MalformedManifestError):
        manifest.load_manifest(path)
    with pytest.raises(manifest.MalformedManifestError):
        manifest.load_manifest(tmp_path / "absent.yaml")


def test_the_module_opens_no_catalog_or_schema_path_of_its_own() -> None:
    """Everything it reads comes through the gateway, so nothing is restated."""
    source = MODULE.read_text(encoding="utf-8")
    literals: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        function = node.func
        name = (
            function.id
            if isinstance(function, ast.Name)
            else function.attr
            if isinstance(function, ast.Attribute)
            else ""
        )
        if name not in {"Path", "open", "read_text", "write_text", "joinpath"}:
            continue
        for argument in [
            *node.args,
            *(keyword.value for keyword in node.keywords if keyword.value is not None),
        ]:
            if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                literals.add(argument.value)
    assert [x for x in literals if "catalog/" in x or "schemas/" in x] == []
    assert "engine.vocabulary" in {
        node.module
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.ImportFrom) and node.module
    }


# --------------------------------------------------------------------------
# The documentation -- task 4.8
# --------------------------------------------------------------------------

DOCUMENT = ROOT / "docs" / "reference" / "pack-manifest.md"

#: A fenced YAML block, which on this page is a whole manifest.
_FENCED = re.compile(r"^```yaml\n(.*?)^```$", re.DOTALL | re.MULTILINE)


def _examples() -> list[str]:
    return _FENCED.findall(DOCUMENT.read_text(encoding="utf-8"))


def test_the_document_exists_and_names_the_four_authorities() -> None:
    text = DOCUMENT.read_text(encoding="utf-8")
    for authority in (
        "the schema",
        "the licence vocabulary",
        "the corpus",
        "the marker",
    ):
        assert authority in text, authority


def test_the_document_names_every_reason_the_module_can_report() -> None:
    """Every reason the module can carry is named on the page, in backticks.

    The page is the only place a pack author reads the taxonomy, so a reason the
    module declares and the page omits is a refusal nobody can look up.
    """
    text = DOCUMENT.read_text(encoding="utf-8")
    missing = [reason for reason in manifest.REASONS if f"`{reason}`" not in text]
    assert missing == [], missing


def test_the_document_carries_its_worked_examples() -> None:
    """A hand-written module, a pack that declares a device, and a derived pack."""
    assert len(_examples()) == 3


@pytest.mark.parametrize("index", [0, 1, 2])
def test_each_worked_example_validates_as_written(
    index: int, inspected: tuple, tmp_path: Path
) -> None:
    """The page's claim, checked: an example that stopped being valid fails here.

    The examples are whole manifests rather than fragments, so they are judged by
    all four authorities exactly as an author's file would be.
    """
    artifacts, engine = inspected
    document = yaml.safe_load(_examples()[index])
    assert isinstance(document, dict)
    result = _validate(tmp_path, artifacts, engine, document, f"example{index}.yaml")
    assert result.ok, _messages(result)
