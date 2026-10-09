"""The frozen artifacts as the engine reads them -- tasks 5.0's gateway.

`design.md` D7's promise is that the engine carries no vocabulary of its own, and
the only way that is true is if the engine's vocabulary comes from files. These
tests hold the loader to reading the committed artifacts, to projecting the facts
the rules use, and to failing by naming the file when an artifact is missing or
has been reshaped -- a loader that returned an empty vocabulary for a missing
file would turn "the catalog is not where I think it is" into "this house binds
no slots", which is the silent-drift failure the whole capability is written
against.

The last section turns that promise around and reads the engine rather than the
files: the names `engine/`'s own source spells are checked against the artifacts,
so a name the engine restates fails naming the name and the file it stands in.
That is a fact about the source text and about no resolution, which is why
nothing that runs can see it.

Phase 2 adds two artifacts to the same gateway and both are covered here: the
engine API version -- what a pack's `engine_api` range is checked against, read
from `schemas/engine-api/` through the succession rule rather than declared as a
constant -- and `catalog/pack-policy.yaml`, whose four sections the sandbox reads
(task 3.1) and whose ban list is the only place a ban can come from.

Each test names the behaviour and says what a falsifying implementation would
look like, because a test that would pass against any implementation exercises
nothing.
"""

from __future__ import annotations

import ast
import json
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import yaml

from engine.vocabulary import (
    MalformedArtifactError,
    MissingArtifactError,
    SlotDefinition,
    Vocabulary,
)
from tools.catalog.schemas import current_version, load_versions

from .conftest import write, write_version

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def vocabulary() -> Vocabulary:
    """The committed catalog, loaded once: it is frozen and read-only."""
    return Vocabulary.load(ROOT)


def test_the_slot_vocabulary_comes_from_the_committed_catalog(
    vocabulary: Vocabulary,
) -> None:
    """Every slot `catalog/slots.yaml` defines is present, with its `required` flag.

    The expected mapping is built from the file's own rows rather than written
    here, so the test fails when the loader and the catalog disagree. A test that
    asserted a literal table of names and flags would pass against an engine
    reading a different file, and would keep passing after the catalog changed,
    which is the drift this capability is written against. The count on the last
    line is the one literal and it is not a second table: the comparison above
    has already failed by then if the two disagree, so it is here only to catch a
    file read as empty, which an equality between two empty mappings would not.
    """
    document = yaml.safe_load(
        (ROOT / "catalog" / "slots.yaml").read_text(encoding="utf-8")
    )
    expected = {
        row["name"]: SlotDefinition(required=row["required"])
        for row in document["slots"]
    }
    assert dict(vocabulary.slots) == expected
    assert len(expected) == 14


def test_the_house_scope_slots_come_from_the_room_type_catalog(
    vocabulary: Vocabulary,
) -> None:
    """The house's slots are `catalog/room_types.yaml`'s `house.slots`.

    A falsifying implementation that read the room types' `provides_slots` union
    as the house scope would admit `ambient_light_sensor`, which no room type offers at
    house scope, and reject nothing a real house names -- a difference only a
    check against the file's `house` key can see, which is why the expectation is
    read from that key rather than written out here.
    """
    document = yaml.safe_load(
        (ROOT / "catalog" / "room_types.yaml").read_text(encoding="utf-8")
    )
    assert vocabulary.house_slots == frozenset(document["house"]["slots"])
    assert "ambient_light_sensor" not in vocabulary.house_slots


def test_the_house_schema_is_the_frozen_one(vocabulary: Vocabulary) -> None:
    """The schema the loader hands out is `schemas/house/1.0.0.json`.

    A falsifying implementation that carried a hand-written schema would validate
    a house against the engine's idea of a house rather than the frozen one, and
    the two would part company the first time the schema changed.
    """
    assert vocabulary.house_schema["$id"] == (
        "https://open-house.invalid/schemas/house/1.0.0.json"
    )
    assert vocabulary.house_schema["schema_version"] == "1.0.0"


def test_the_mode_schema_is_the_frozen_one(vocabulary: Vocabulary) -> None:
    """Modes are validated against `schemas/mode/1.0.0.json`, and nothing else.

    There is no mode catalog for the engine to read, so the schema is the whole
    of what fixes a mode's shape; a loader that handed out the house schema here
    -- or a hand-written mode schema -- would let `engine/modes.py` validate
    definitions against something other than the artifact Phase 0 froze.
    """
    assert vocabulary.mode_schema["$id"] == (
        "https://open-house.invalid/schemas/mode/1.0.0.json"
    )
    assert vocabulary.mode_schema["schema_version"] == "1.0.0"
    assert vocabulary.mode_schema != vocabulary.house_schema


def test_a_missing_mode_schema_fails_naming_the_file(tmp_path: Path) -> None:
    """The fourth artifact is loaded and checked like the other three."""
    (tmp_path / "catalog").mkdir()
    (tmp_path / "catalog" / "slots.yaml").write_text(
        "slots:\n  - name: light_group\n    required: true\n", encoding="utf-8"
    )
    (tmp_path / "catalog" / "room_types.yaml").write_text(
        "house:\n  slots: [light_group]\n", encoding="utf-8"
    )
    (tmp_path / "schemas" / "house").mkdir(parents=True)
    (tmp_path / "schemas" / "house" / "1.0.0.json").write_text("{}", encoding="utf-8")
    with pytest.raises(MissingArtifactError) as raised:
        Vocabulary.load(tmp_path)
    assert raised.value.path == tmp_path / "schemas" / "mode" / "1.0.0.json"


def test_a_missing_artifact_fails_naming_the_file(tmp_path: Path) -> None:
    """A vocabulary pointed at a tree with no catalog says so, and names it.

    A falsifying implementation that defaulted to an empty vocabulary would let a
    test suite pass while the engine read nothing, which is exactly the failure
    the check exists for.
    """
    with pytest.raises(MissingArtifactError) as raised:
        Vocabulary.load(tmp_path)
    assert raised.value.path == tmp_path / "catalog" / "slots.yaml"


@pytest.mark.parametrize(
    ("body", "field"),
    [
        ("- name: light_group\n", "mapping"),
        ("slots:\n  - name: light_group\n", "slot row's `name` and `required`"),
        ("slots: light_group\n", "`slots` list"),
    ],
)
def test_a_reshaped_artifact_fails_naming_the_field(
    tmp_path: Path, body: str, field: str
) -> None:
    """A catalog that no longer carries the field says which field is gone.

    A falsifying implementation that accessed the row with `[]` would raise a
    `KeyError` that names neither the file nor the field, leaving a reshaped
    catalog indistinguishable from a bug in the resolver. The single-string case
    is the one a `Sequence` test alone gets wrong, because a string is iterable.
    """
    (tmp_path / "catalog").mkdir()
    (tmp_path / "schemas" / "house").mkdir(parents=True)
    (tmp_path / "catalog" / "slots.yaml").write_text(body, encoding="utf-8")
    (tmp_path / "catalog" / "room_types.yaml").write_text(
        "house:\n  slots: []\n", encoding="utf-8"
    )
    with pytest.raises(MalformedArtifactError) as raised:
        Vocabulary.load(tmp_path)
    assert raised.value.field == field
    assert raised.value.path == tmp_path / "catalog" / "slots.yaml"


def test_a_missing_house_schema_fails_naming_the_file(tmp_path: Path) -> None:
    """The third artifact is loaded and checked like the other two."""
    (tmp_path / "catalog").mkdir()
    (tmp_path / "catalog" / "slots.yaml").write_text(
        "slots:\n  - name: light_group\n    required: true\n", encoding="utf-8"
    )
    (tmp_path / "catalog" / "room_types.yaml").write_text(
        "house:\n  slots: [light_group]\n", encoding="utf-8"
    )
    with pytest.raises(MissingArtifactError) as raised:
        Vocabulary.load(tmp_path)
    assert raised.value.path == tmp_path / "schemas" / "house" / "1.0.0.json"


# --------------------------------------------------------------------------
# Phase 2's two additions to the gateway
# --------------------------------------------------------------------------

#: A policy written for a fixture tree. Every section differs from the committed
#: one's -- a different ban, a different priority, a different subset -- so a
#: loader reading *this* tree cannot be mistaken for one reading the repository.
_FIXTURE_POLICY = """\
declarative_subset:
  forbidden_actions: [if, repeat]
  forbidden_conditions: [template]
  forbidden_triggers: [template]
banned_services:
  - service: light.turn_on
    reason: A fixture ban, so a ban is demonstrably the file's.
flagged_services:
  - service: lock.unlock
    reason: A fixture flag, kept so the two sections cannot be read as one.
default_priority: 7
"""

#: The version the fixture tree publishes, and a successor to it. Neither number
#: is the committed one: a fixture publishing `1.0.0` would agree with the
#: repository, and the test that the version comes from the tree would then pass
#: on a loader reading the repository instead.
_FIXTURE_API_VERSION = "2.0.0"
_FIXTURE_API_SUCCESSOR = "2.1.0"

#: The shared parts of a policy that passes every check, so each body in the
#: reshaped-policy cases below spoils exactly one field and the failure names
#: that field rather than an earlier one.
_POLICY_PREFIX = """\
declarative_subset:
  forbidden_actions: [if]
  forbidden_conditions: [template]
  forbidden_triggers: [template]
"""
_POLICY_SUFFIX = """\
banned_services:
  - service: light.turn_on
    reason: A fixture ban.
flagged_services: []
default_priority: 0
"""


def _tree(
    tmp_path: Path,
    *,
    policy: str | None = _FIXTURE_POLICY,
    api: tuple[tuple[str, str | None], ...] = ((_FIXTURE_API_VERSION, None),),
) -> Path:
    """A tree carrying every artifact a vocabulary reads, with at most one spoiled.

    The other artifacts are written minimal but complete, so a failure under test
    cannot be a missing file somewhere else reported under the same exception.
    `api` is a tuple of `(version, supersedes)` pairs rather than a list of
    versions because the succession rule -- not the filename -- decides which one
    is current, and a tree that cannot express a `supersedes` link cannot exercise
    it.
    """
    write(
        tmp_path,
        "catalog/slots.yaml",
        "slots:\n  - name: light_group\n    required: true\n",
    )
    write(tmp_path, "catalog/room_types.yaml", "house:\n  slots: [light_group]\n")
    write(tmp_path, "schemas/house/1.0.0.json", "{}")
    write(tmp_path, "schemas/mode/1.0.0.json", "{}")
    if policy is not None:
        write(tmp_path, "catalog/pack-policy.yaml", policy)
    for version, supersedes in api:
        write_version(tmp_path, "engine-api", version, supersedes)
    return tmp_path


def _policy_document() -> dict[str, Any]:
    """The committed policy file, read as the test's own expectation of it."""
    loaded: object = yaml.safe_load(
        (ROOT / "catalog" / "pack-policy.yaml").read_text(encoding="utf-8")
    )
    assert isinstance(loaded, dict)
    return loaded


def test_the_declared_api_version_is_the_published_one(vocabulary: Vocabulary) -> None:
    """Task 1.2: the API version is the artifact's, and not the packaging one.

    `pyproject.toml` carries a packaging version, and that is a value that moves
    for packaging reasons; a manifest's `engine_api` range checked against it
    would be a check in name only, and a constant in the engine would be the same
    promise published twice. The expectation is read through the succession rule
    rather than spelled as `"1.0.0"`, so publishing a successor leaves this test
    passing and a hard-coded loader failing.
    """
    current = current_version(load_versions("engine-api"))
    assert current is not None, "the concept publishes no single current version"
    assert vocabulary.engine_api_version == current.version

    packaging = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    # Not `== "0.0.0"`: the packaging version is deliberately free to move, and
    # the only fact this test needs is that it is *not* the artifact's.
    assert packaging["project"]["version"] != vocabulary.engine_api_version


def test_the_api_version_is_the_tree_s_and_not_the_repository_s(
    tmp_path: Path,
) -> None:
    """The version is a fact about the artifacts handed to the gateway.

    A loader that resolved `schemas/engine-api/` from the repository root would
    answer `1.0.0` for every tree, including this one, which publishes `2.0.0`.
    That difference is the whole of what makes "read from the artifact" observable
    rather than asserted.
    """
    assert Vocabulary.load(_tree(tmp_path)).engine_api_version == _FIXTURE_API_VERSION


def test_a_successor_is_the_current_api_version(tmp_path: Path) -> None:
    """Current-ness is the succession rule and not the newest filename.

    The ordinary case, read from the other side by the fork test below: two
    versions where the later names the earlier, and the later one is the answer.
    """
    root = _tree(
        tmp_path,
        api=(
            (_FIXTURE_API_VERSION, None),
            (_FIXTURE_API_SUCCESSOR, _FIXTURE_API_VERSION),
        ),
    )
    assert Vocabulary.load(root).engine_api_version == _FIXTURE_API_SUCCESSOR


def test_two_current_api_versions_fail_naming_the_directory(tmp_path: Path) -> None:
    """A fork has no single current version, and the gateway says so.

    Picking the highest would implement `supersedes` as a sort order: which
    version a pack's `engine_api` range is checked against would be decided by a
    filename, and the version a pack was written against could be retired with
    nobody naming its successor.
    """
    root = _tree(
        tmp_path,
        api=((_FIXTURE_API_VERSION, None), (_FIXTURE_API_SUCCESSOR, None)),
    )
    with pytest.raises(MalformedArtifactError) as raised:
        Vocabulary.load(root)
    assert raised.value.path == root / "schemas" / "engine-api"
    assert raised.value.field == "single current version"


def test_a_missing_engine_api_artifact_fails_naming_the_directory(
    tmp_path: Path,
) -> None:
    """An absent concept is a checkout's fault, and names a directory, not a file.

    The concept *is* a directory of versions, so a tree publishing none has no
    file to name: reporting `schemas/engine-api/1.0.0.json` would send a reader
    looking for a file whose absence was never the question.
    """
    root = _tree(tmp_path, api=())
    with pytest.raises(MissingArtifactError) as raised:
        Vocabulary.load(root)
    assert raised.value.path == root / "schemas" / "engine-api"


def test_the_policy_sections_are_the_files_own_entries(vocabulary: Vocabulary) -> None:
    """Task 1.3: every section is read from `catalog/pack-policy.yaml`.

    The expectations are built from the file's entries rather than typed here,
    because a hand-typed copy passes against an engine reading a different file
    and keeps passing after the file it claims to check has changed -- the failure
    a policy-as-data capability exists to rule out. Order is part of it for the
    ban list: it is projected as published, so a diagnostic listing bans lists
    them where a reader of the file would find them.
    """
    document = _policy_document()
    subset = document["declarative_subset"]
    policy = vocabulary.pack_policy
    assert policy.forbidden_actions == frozenset(subset["forbidden_actions"])
    assert policy.forbidden_conditions == frozenset(subset["forbidden_conditions"])
    assert policy.forbidden_triggers == frozenset(subset["forbidden_triggers"])
    assert policy.banned_services == tuple(
        row["service"] for row in document["banned_services"]
    )
    assert policy.flagged_services == frozenset(
        row["service"] for row in document["flagged_services"]
    )
    assert policy.default_priority == document["default_priority"]


def test_every_policy_entry_states_why_it_is_there() -> None:
    """A ban without a reason is a ban nobody can safely lift.

    The requirement is the artifact's -- `schemas/catalog/pack-policy.json`
    requires a `reason` on every entry, and `oh-catalog validate` holds the file
    to it -- and this is the suite's own reading of the committed file, so an
    entry stripped of its reason fails here even when the schema that required it
    is relaxed in the same edit.
    """
    document = _policy_document()
    for section in ("banned_services", "flagged_services"):
        entries = document[section]
        assert entries, f"`{section}` is empty, so the list judges nothing"
        for row in entries:
            assert row.get("reason", "").strip(), f"{section}: {row.get('service')!r}"


def test_the_ban_matcher_reads_the_two_forms_the_artifact_allows(
    vocabulary: Vocabulary,
) -> None:
    """`domain.service` and `domain.*` are the two forms, and there is no third.

    Both directions of the wildcard are asserted because both are silent: a
    `shell_command.*` that matched nothing would let a pack run a command on the
    host, and a matcher reading it as a *prefix* would refuse a service of an
    unrelated domain. Both come from the file, so adding a ban does not break the
    test. The flagged list is asserted to be unbanned so that a matcher which
    merged the two sections fails, since a flag is surfaced and never converted
    into a refusal.
    """
    document = _policy_document()
    policy = vocabulary.pack_policy
    for row in document["banned_services"]:
        service = row["service"]
        assert policy.bans(service), service
        if service.endswith(".*"):
            domain = service[:-2]
            assert policy.bans(f"{domain}.made_up_service")
            assert not policy.bans(f"{domain}er.service")
    exact = [
        row["service"]
        for row in document["banned_services"]
        if not row["service"].endswith(".*")
    ]
    domain = exact[0].partition(".")[0]
    assert not policy.bans(f"{domain}.made_up_service")
    for row in document["flagged_services"]:
        assert not policy.bans(row["service"]), (
            "a flagged service is surfaced and never refused"
        )


def test_banning_a_service_is_an_edit_to_the_file(tmp_path: Path) -> None:
    """Task 1.3: a ban is data, so it is added by editing and not by releasing.

    The fixture tree bans `light.turn_on`, which the committed tree does not, and
    the same loader refuses it with no line of code changed. That is the property
    the requirement asks for, and the committed-tree test above is what stops this
    one from passing on a loader that reads nothing at all.
    """
    policy = Vocabulary.load(_tree(tmp_path)).pack_policy
    assert policy.bans("light.turn_on")
    assert not policy.bans("light.turn_off")
    assert not policy.bans("light.turn_on_forever")
    assert policy.default_priority == 7
    assert policy.forbidden_actions == frozenset({"if", "repeat"})


@pytest.mark.parametrize(
    ("body", "field"),
    [
        ("declarative_subset: []\n" + _POLICY_SUFFIX, "`declarative_subset` mapping"),
        (
            _POLICY_PREFIX.replace("forbidden_actions: [if]", "forbidden_actions: if")
            + _POLICY_SUFFIX,
            "`declarative_subset.forbidden_actions` list",
        ),
        (
            _POLICY_PREFIX.replace("[if]", "[if, 7]") + _POLICY_SUFFIX,
            "`declarative_subset.forbidden_actions` names",
        ),
        (
            _POLICY_PREFIX
            + "banned_services: light.turn_on\nflagged_services: []\n"
            + "default_priority: 0\n",
            "`banned_services` list",
        ),
        (
            _POLICY_PREFIX
            + "banned_services:\n  - reason: A fixture ban.\n"
            + "flagged_services: []\ndefault_priority: 0\n",
            "`banned_services` row's `service`",
        ),
        (
            _POLICY_PREFIX
            + "banned_services: []\nflagged_services: 7\ndefault_priority: 0\n",
            "`flagged_services` list",
        ),
        (
            _POLICY_PREFIX
            + "banned_services: []\nflagged_services: []\ndefault_priority: true\n",
            "`default_priority` integer",
        ),
        (
            _POLICY_PREFIX + "banned_services: []\nflagged_services: []\n",
            "`default_priority` integer",
        ),
    ],
)
def test_a_reshaped_policy_fails_naming_the_file_and_the_field(
    tmp_path: Path, body: str, field: str
) -> None:
    """A policy that has been reshaped says which field it no longer carries.

    A loader that took the fields on trust would fail inside the first check that
    read one -- a `TypeError` on iteration, or a ban list of characters -- and
    that is indistinguishable from a bug in the sandbox once a traceback is the
    only evidence. `default_priority: true` is the case worth naming: `bool` is an
    `int` to `isinstance`, so a `true` written where a rank belongs would be
    projected as the rank 1 in silence.
    """
    root = _tree(tmp_path, policy=body)
    with pytest.raises(MalformedArtifactError) as raised:
        Vocabulary.load(root)
    assert raised.value.field == field
    assert raised.value.path == root / "catalog" / "pack-policy.yaml"


def test_a_missing_policy_fails_naming_the_file(tmp_path: Path) -> None:
    """The fifth artifact is read and checked like the other four."""
    root = _tree(tmp_path, policy=None)
    with pytest.raises(MissingArtifactError) as raised:
        Vocabulary.load(root)
    assert raised.value.path == root / "catalog" / "pack-policy.yaml"


# --------------------------------------------------------------------------
# The read is cached on the tree's stamp, and the cache is not stale
# --------------------------------------------------------------------------


def test_a_tree_nobody_touched_is_read_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A second `Vocabulary.load` of one tree reads no artifact at all.

    This is the defect's claim in the form it can be checked: a rebuild on Home
    Assistant's event loop read all six artifacts again, and the fix is that a
    rebuild of a tree nobody touched does not. The proof is a spy on
    `Path.read_text`, installed *after* the first load so the cache is already
    warm, which the second load must not call. Falsified by the pre-fix
    `Vocabulary.load`, which read the tree every time -- the spy would catch the
    artifacts and the identity assertion would fail with it.
    """
    root = _tree(tmp_path)
    first = Vocabulary.load(root)
    reads: list[Path] = []
    real_read_text = Path.read_text

    def counting(self: Path, *args: object, **kwargs: object) -> str:
        reads.append(self)
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", counting)
    second = Vocabulary.load(root)
    assert second is first
    assert reads == [], "a warm load re-read the tree"


def test_a_republished_artifact_is_re_read(tmp_path: Path) -> None:
    """A tree that changes on disk is read again, not served from the cache.

    The other half of the caching claim, and the one a bare `lru_cache` on the
    path would fail: the key carries the artifacts' stamp, so an edited file is
    seen and served fresh. The edited policy differs in length as well as in
    contents, so the size in the stamp moves even where a filesystem's
    modification-time resolution is coarse -- the test does not rest on two
    writes within one tick landing on different nanoseconds.
    """
    root = _tree(tmp_path)
    before = Vocabulary.load(root)
    assert before.pack_policy.default_priority == 7
    write(
        root,
        "catalog/pack-policy.yaml",
        _FIXTURE_POLICY.replace("default_priority: 7", "default_priority: 70"),
    )
    after = Vocabulary.load(root)
    assert after is not before
    assert after.pack_policy.default_priority == 70


def test_an_added_api_version_is_re_read(tmp_path: Path) -> None:
    """A version file *appearing* is a change the stamp sees.

    The API version is not one of the five fixed files, so its artifacts are the
    directory and each version in it: the directory's own stamp catches a file
    added or removed, and each version file's stamp catches an edit to one
    already present. This is the added-file half, which a cache keyed on the five
    files alone would miss -- it would answer the old version for a tree that had
    published a successor.
    """
    root = _tree(tmp_path)
    assert Vocabulary.load(root).engine_api_version == _FIXTURE_API_VERSION
    write_version(root, "engine-api", _FIXTURE_API_SUCCESSOR, _FIXTURE_API_VERSION)
    assert Vocabulary.load(root).engine_api_version == _FIXTURE_API_SUCCESSOR


def test_a_failed_load_is_not_remembered(tmp_path: Path) -> None:
    """A tree that was incomplete and is completed loads: a failure is no cache.

    `lru_cache` stores no exception, and the stamp moves when the missing
    artifact is written, so a checkout that was missing a file and then had it
    restored -- or a fixture completed between two loads -- is read rather than
    handed the earlier failure. Falsified by any cache that remembered the
    exception, or a stamp that did not carry the appearing file.
    """
    root = _tree(tmp_path, policy=None)
    with pytest.raises(MissingArtifactError):
        Vocabulary.load(root)
    write(root, "catalog/pack-policy.yaml", _FIXTURE_POLICY)
    assert Vocabulary.load(root).pack_policy.default_priority == 7


# --------------------------------------------------------------------------
# The engine's own source
# --------------------------------------------------------------------------

#: The engine's package. The check reads its *text* rather than its imported
#: modules, because a restated name is a fact a reviewer sees in a diff.
_ENGINE_ROOT = ROOT / "engine"

#: Where each call takes its slot. The position is per call, because the two
#: disagree: `Binding.read(self, slot, reduction)` takes the slot first, and
#: `resolve_slot(house, scope, slot)` takes it third. Reading position zero for
#: both would leave the `resolve_slot` limb inert, since position zero is a house.
_SLOT_CALLS: dict[str, int] = {"read": 0, "resolve_slot": 2}

#: What makes a binding's name a declaration of one kind of vocabulary name.
#: Each pattern is anchored at a word boundary and at the end, because a constant
#: named `_SLOTS_PATH` holds path parts rather than slots -- it ends in `PATH`.
_KINDS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("slot", re.compile(r"(?:^|_)(?:slot|slots)$", re.IGNORECASE)),
    ("room type", re.compile(r"(?:^|_)(?:room_?type|room_?types)$", re.IGNORECASE)),
    ("mode", re.compile(r"(?:^|_)(?:mode|modes)$", re.IGNORECASE)),
)

#: The keywords a name of each kind is passed as.
_KEYWORDS: dict[str, str] = {"slot": "slot", "room_type": "room type", "mode": "mode"}

#: A name as the two catalogs that hold names spell one. It is what tells a slot
#: from a value that merely mentions one: `Outcome.SKIPPED_UNBOUND_SLOT` is named
#: for a slot and holds the wire string `"skipped: unbound slot"`.
_NAME = re.compile(r"[a-z][a-z0-9_]*")


@dataclass(frozen=True, slots=True)
class Declaration:
    """One vocabulary name `engine/` spells, and where the source spells it."""

    path: Path
    line: int
    kind: str
    name: str

    @property
    def where(self) -> str:
        """The file, relative to the repository when it is inside it."""
        try:
            return self.path.relative_to(ROOT).as_posix()
        except ValueError:
            return self.path.as_posix()

    def named(self) -> str:
        """The failure message: the file, the line, the kind and the name."""
        return f"{self.where}:{self.line} names the {self.kind} {self.name!r}"


@dataclass(frozen=True, slots=True)
class _Catalog:
    """The two facts about a name the `Vocabulary` projection does not carry.

    `Vocabulary` holds the slots and the house scope because a resolution reads
    them. The room types' names and the mode schema's pattern are on no
    resolution's path, so this check reads them from the committed files itself.
    """

    room_types: frozenset[str]
    mode_pattern: str


@pytest.fixture(scope="module")
def catalog() -> _Catalog:
    """The room-type names and the mode name pattern, from the frozen files."""
    document = yaml.safe_load(
        (ROOT / "catalog" / "room_types.yaml").read_text(encoding="utf-8")
    )
    schema = json.loads(
        (ROOT / "schemas" / "mode" / "1.0.0.json").read_text(encoding="utf-8")
    )
    return _Catalog(
        room_types=frozenset(row["name"] for row in document["room_types"]),
        mode_pattern=schema["properties"]["name"]["pattern"],
    )


def _strings(value: ast.expr | None) -> list[str]:
    """Every string literal an expression is, or holds in a literal collection."""
    if isinstance(value, ast.Constant) and isinstance(value.value, str):
        return [value.value]
    if isinstance(value, (ast.List, ast.Set, ast.Tuple)):
        return [
            item.value
            for item in value.elts
            if isinstance(item, ast.Constant) and isinstance(item.value, str)
        ]
    return []


def _kind_of(bound: str) -> str | None:
    """Which kind of vocabulary name a binding's own name declares, if any."""
    for kind, pattern in _KINDS:
        if pattern.search(bound):
            return kind
    return None


def _names_in(value: ast.expr | None, kind: str) -> list[str]:
    """The vocabulary names an expression spells, in the kind's own spelling.

    A slot or a room type is a name one of the catalogs lists, so a string that
    is not shaped like one is not a restatement of one and is passed over. A mode
    has no catalog of names to be a member of -- `engine/vocabulary.py` says so
    -- so every string a mode-named binding carries is returned, and the mode
    schema's own pattern is what judges it.
    """
    return [
        text
        for text in _strings(value)
        if kind == "mode" or _NAME.fullmatch(text) is not None
    ]


def _from_binding(path: Path, node: ast.Assign | ast.AnnAssign) -> list[Declaration]:
    """The vocabulary names a binding declares, by the name it is bound to."""
    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    found: list[Declaration] = []
    for target in targets:
        if not isinstance(target, ast.Name):
            continue
        kind = _kind_of(target.id)
        if kind is None:
            continue
        found += [
            Declaration(path, node.lineno, kind, text)
            for text in _names_in(node.value, kind)
        ]
    return found


def _from_call(path: Path, node: ast.Call) -> list[Declaration]:
    """The vocabulary names a call passes, positionally or by keyword."""
    found: list[Declaration] = []
    callee = node.func
    if isinstance(callee, ast.Attribute):
        called = callee.attr
    elif isinstance(callee, ast.Name):
        called = callee.id
    else:
        called = None
    position = _SLOT_CALLS.get(called) if called is not None else None
    if position is not None and position < len(node.args):
        found += [
            Declaration(path, node.lineno, "slot", text)
            for text in _names_in(node.args[position], "slot")
        ]
    for keyword in node.keywords:
        kind = _KEYWORDS.get(keyword.arg or "")
        if kind is None:
            continue
        found += [
            Declaration(path, node.lineno, kind, text)
            for text in _names_in(keyword.value, kind)
        ]
    return found


def _declarations_in(path: Path) -> list[Declaration]:
    """Every vocabulary name `path` spells, in the positions one can take.

    A name stands in one of three places: as the value of a binding whose own
    name ends with the kind, as the argument of the call that takes one -- at the
    position that call takes it, which is why `_SLOT_CALLS` carries an index
    rather than a membership -- or as a keyword argument named for it. Nothing
    else is read, because a slot name and any other lowercase string are the same
    characters wide: `engine/safety.py`'s `EGRESS_ACTIONS` is keyed by entity
    *domain*, and its `"lock"` and `"cover"` keys are domains spelled like two of
    the slots.
    """
    found: list[Declaration] = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Call):
            found += _from_call(path, node)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            found += _from_binding(path, node)
    return found


def _declarations(root: Path) -> list[Declaration]:
    """Every vocabulary name every Python module under `root` spells."""
    return [
        declaration
        for path in sorted(root.rglob("*.py"))
        for declaration in _declarations_in(path)
    ]


def _resolves(
    declaration: Declaration, vocabulary: Vocabulary, catalog: _Catalog
) -> bool:
    """Whether the artifact that judges the kind admits the name it spells.

    Slots and room types are judged by membership, because the two catalogs list
    names and a name either is one of them or is not. A mode is judged by the
    mode schema's `name` pattern, because there is no catalog of mode names for
    one to be a member of -- a difference the caller must not read as the mode
    limb being the same kind of check.
    """
    if declaration.kind == "slot":
        return (
            declaration.name in vocabulary.slots
            or declaration.name in vocabulary.house_slots
        )
    if declaration.kind == "room type":
        return declaration.name in catalog.room_types
    return re.fullmatch(catalog.mode_pattern, declaration.name) is not None


def test_the_engine_spells_no_vocabulary_name_the_artifacts_do_not_define(
    vocabulary: Vocabulary, catalog: _Catalog
) -> None:
    """Every slot, room type and mode `engine/` spells is one the artifacts admit.

    `engine-core` forbids the engine a vocabulary of its own, and a restatement
    is a name in the engine's text that the catalog does not carry. The check
    names the file, the line and the name, which is what makes it fixable: "a
    name is undefined somewhere in `engine/`" is not a defect anyone can act on.

    The scan is deliberately not "every string literal in `engine/`", because a
    domain is spelled like a slot and a wire string is not a name at all. A name
    is in scope because of where it stands -- a binding named for the kind, a
    call's own argument, a keyword -- and that position is the whole of what
    distinguishes it.

    The mode limb is the one that is a *shape* check rather than a membership
    check, and the difference is a limit worth stating rather than a detail: a
    mode has no catalog of names to be absent from, so the artifact that judges
    one is the mode schema's `name` pattern and any lowercase identifier passes
    it. The limb is closed only as far as Phase 1 can express it -- it cannot
    catch a mode name the engine restated, because there is nothing to restate
    one from, and a hard-coded `MODES = ("away", "home")` would be collected and
    admitted. Reading it as the same kind of check as the other two would be
    reading a guarantee into it that it does not give.

    A falsifying implementation that added a private `SLOT_NAMES` tuple, read a
    slot through a `read("...")` argument or a `resolve_slot(house, scope, "...")`
    one, or named a mode the mode schema does not admit would be reported here;
    the shipped tree passes only because every name it spells is one the
    artifacts already carry.
    """
    declarations = _declarations(_ENGINE_ROOT)
    assert {item.name for item in declarations} >= {"light_group", "motion_sensor"}, (
        "the scan collected none of the slots the shipped units require, so it "
        "is reading nothing"
    )
    unknown = sorted(
        {item for item in declarations if not _resolves(item, vocabulary, catalog)},
        key=lambda item: (item.where, item.line, item.name),
    )
    assert not unknown, "; ".join(item.named() for item in unknown)


def test_the_scan_reports_a_restated_name_by_the_name_and_the_file(
    vocabulary: Vocabulary, catalog: _Catalog, tmp_path: Path
) -> None:
    """A module spelling a name the artifacts lack is reported, and named.

    This is the half of the check that can be wrong without the other half
    noticing: a scan that collected nothing would pass the test above on a tree
    that restated every name in the vocabulary. The subject here is a module
    written for the test, which is the only subject that can be *known* to
    violate, and it holds both halves of the scenario -- the name is collected,
    and the name does not resolve. It also pins the rule that a mode is judged by
    the mode schema's pattern, since a mode has no catalog to be absent from.

    The two calls at the end pin the position each call takes its slot at. The
    shipped tree names no literal slot in either of them -- all four
    `resolve_slot` sites pass a variable third -- so without these a `_SLOT_CALLS`
    that read one index for both calls adds nothing on `engine/` and fails
    nothing, which is how the `resolve_slot` limb came to be inert. The first
    call puts its name third because that is where `resolve_slot` takes it.

    A falsifying implementation that scanned only `engine/behaviours/`, or that
    resolved a name by prefix or by falling back to the house scope, would report
    one of these five as admitted, and one that collected nothing would report
    none of them at all.
    """
    (tmp_path / "restated.py").write_text(
        "CEILING_SLOT = 'ceiling_light'\n"
        "ROOM_TYPE = 'ballroom'\n"
        "SLEEP_MODE = ('Sleep',)\n"
        "UNSCANNED = 'not a name in any position the scan reads'\n"
        "\n"
        "\n"
        "def go():\n"
        "    resolve_slot(house, scope, 'loft_sensor')\n"
        "    binding.read('cellar_lamp', Reduction.ANY)\n",
        encoding="utf-8",
    )
    declarations = _declarations(tmp_path)
    assert {(item.kind, item.name) for item in declarations} == {
        ("slot", "ceiling_light"),
        ("room type", "ballroom"),
        ("mode", "Sleep"),
        ("slot", "loft_sensor"),
        ("slot", "cellar_lamp"),
    }
    assert not any(_resolves(item, vocabulary, catalog) for item in declarations)
    where = (tmp_path / "restated.py").as_posix()
    assert sorted(item.named() for item in declarations) == [
        f"{where}:1 names the slot 'ceiling_light'",
        f"{where}:2 names the room type 'ballroom'",
        f"{where}:3 names the mode 'Sleep'",
        f"{where}:8 names the slot 'loft_sensor'",
        f"{where}:9 names the slot 'cellar_lamp'",
    ]


def test_a_value_named_for_a_slot_but_not_shaped_like_one_is_not_a_slot(
    tmp_path: Path,
) -> None:
    """`SKIPPED_UNBOUND_SLOT` is named for a slot and holds a wire string.

    The engine's own closed sets -- the log's eight outcomes, the four change
    origins -- have members named for things that are not catalog slots, and one
    of them is named for a slot. A scan that collected it would fail the engine
    for restating a name it never restated, which is how a check that is right
    about the rule is still wrong about the code. The shape guard is what tells
    the two apart, and it is checked here on both sides so that removing it fails
    something.
    """
    assert "skipped: unbound slot" not in {
        item.name for item in _declarations_in(ROOT / "engine" / "decision_log.py")
    }
    (tmp_path / "wire.py").write_text(
        "SKIPPED_SLOT = 'skipped: unbound slot'\nNAMED_SLOT = 'light_group'\n",
        encoding="utf-8",
    )
    assert {(item.kind, item.name) for item in _declarations(tmp_path)} == {
        ("slot", "light_group")
    }


def test_the_engine_spells_no_banned_service_in_its_source() -> None:
    """No ban lives in the engine's code, so the file is the only place one comes from.

    A ban also spelled in `engine/` would be a second definition of it: it would
    survive the file being edited and would be a ban nobody could lift by changing
    data, which is the property `catalog/pack-policy.yaml` exists to have. The
    scan is over every string constant in the package rather than over the ban
    matcher alone, because a `BANNED = ("homeassistant.restart",)` tuple anywhere
    in the engine is the same restatement as a hard-coded comparison.

    This is the source-text limb of task 1.3 and it is deliberately not a
    membership test of the *file*: a ban added to the artifact is admitted here
    the moment it is added, since the expectation is read from the file.
    """
    document = _policy_document()
    published = {row["service"] for row in document["banned_services"]}
    assert published, "the policy bans nothing, so this scan reads nothing"
    spelled = {
        node.value
        for path in sorted(_ENGINE_ROOT.rglob("*.py"))
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    assert spelled, "the scan collected no string constants from engine/"
    assert not (published & spelled), sorted(published & spelled)
