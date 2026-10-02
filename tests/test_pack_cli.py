"""The pack verbs -- tasks 10.1, 10.4 and 10.6.

Three properties hold this module together, and each is why a verb is not a
boolean:

**A failure carries a class.** "Invalid" collapses a schema failure, a policy
decision and a licence grant, which are three different remedies. So each check
asserts the class and not only that something failed -- and the fallback arm is
asserted too, because a refusal reason outside the map is reported under its own
name rather than bucketed into a class that would misdescribe it.

**Nothing absent is reported as passing.** An empty directory, a file that will
not parse, and a manifest that failed all have to be distinguishable from nine
packs that validated, which is asserted by the count and by the `unreadable`
list rather than by `ok` alone.

**The diff is a reading and not a validation.** `diff-permissions` runs over a
document the schema would refuse, because the question it answers -- what can
this pack do -- has to be askable before installation is decided. That is
asserted by diffing a manifest that fails validation and watching the diff come
back anyway.

`test_the_vocabulary_comes_from_the_project_and_not_from_the_tree_root` is the
one to read first if the two roots look like one: `provides` paths resolve
against the tree the packs live in, while the schema and the vocabulary are the
project's wherever the packs are, and a version of this module with a single root
passes every other test here and fails that one.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from engine import manifest as engine_manifest
from engine import sandbox
from openhouse import pack_verbs
from tools.catalog import paths

ROOT = paths.ROOT
SHIPPED = ROOT / "packs" / "official"

#: The six room templates the phase ships, which are the templates the shipped
#: set is required to carry -- named here so a directory that validates but has
#: lost three of them fails rather than reporting a clean pass over a smaller set.
ROOM_TEMPLATES = ("bathroom", "bedroom", "driveway", "garage", "kitchen", "living_room")

#: The shape `engine.sandbox.file_class` reads as an automation: a mapping with
#: `trigger` or `action` among its top-level keys. Written rather than copied from
#: the corpus so a test that fails names its own fixture.
AUTOMATION: dict[str, object] = {
    "alias": "pinned beside the pack",
    "trigger": [{"platform": "state", "entity_id": "light_group", "to": "on"}],
    "action": [{"service": "light.turn_on", "target": {"entity_id": "light_group"}}],
}


def _document(
    name: str,
    services: list[str],
    *,
    provides: str = "automation.yaml",
    **overrides: object,
) -> dict[str, object]:
    """A manifest the current schema accepts, pinned to one file beside it.

    `provides` is `minItems: 1`, so a pack that hands nothing over is not a
    smaller valid pack -- it is an invalid one, and a fixture that left it empty
    would fail for a reason the test is not about. `requires_slots` may be empty
    -- a pack that acts through a service and no placeholder needs nothing of the
    room -- so the fixture names `light_group` to leave the pinning to `provides`.
    """
    document: dict[str, object] = {
        "name": name,
        "version": "1.0.0",
        "description": f"{name}, written by the test.",
        "kind": "module",
        "engine_api": ">=1.0.0 <2.0.0",
        "license": "mit",
        "requires_slots": ["light_group"],
        "optional_slots": [],
        "provides": [{"path": provides, "class": "automation"}],
        "behaviours": [{"name": "act", "action": "service", "services": services}],
        "i18n": {"default": {"pack": name, "description": name, "act": "Act"}},
    }
    document.update(overrides)
    return document


def _put(directory: Path, filename: str, document: object) -> Path:
    """Write one YAML file into `directory`, verbatim when it is a string."""
    path = directory / filename
    body = (
        document
        if isinstance(document, str)
        else yaml.safe_dump(document, sort_keys=False)
    )
    path.write_text(body, encoding="utf-8")
    return path


def _tree(tmp_path: Path, *documents: tuple[str, object]) -> Path:
    """A directory of packs, pinned file and all.

    The pinned file is written first and is itself a YAML file in the pack's own
    directory, because that is what the shipped tree looks like -- and a version
    of the verb that counted it as a candidate would report it.
    """
    _put(tmp_path, "automation.yaml", AUTOMATION)
    for filename, document in documents:
        _put(tmp_path, filename, document)
    return tmp_path


def _classes(report: pack_verbs.ManifestReport) -> list[str]:
    """The classes one manifest's findings were reported under, in order."""
    return [finding.klass for finding in report.findings]


# -- 10.1: validate over a directory ----------------------------------------


def test_the_shipped_packs_validate(real_root: Path) -> None:
    """Every manifest under `packs/official/` validates, and the thirteen are there.

    Falsified by a check that reports a pass over a directory it did not read:
    the six room templates and the four module packs are named, so a scan that
    skipped them, or a `provides` path left dangling, fails here rather than
    reporting a clean pass over thirteen names that happen to parse.

    The count is thirteen because the shipped set is the six default room
    templates, the house template, the guest-mode pack, the four module packs
    tasks 7.1-7.3 add (the Bedtime button, the Roomba button, the bathroom fan
    and the fridge guard), and `example-pack.yaml` -- itself a pack file in this
    directory and a `module`, which the phase's *set* does not name. So the count
    is the directory's claim and the set is `test_official_packs.py`'s.
    """
    report = pack_verbs.validate_directory(SHIPPED)
    assert report.ok
    assert report.checked == 13
    assert report.unreadable == ()
    names = {manifest.name for manifest in report.reports}
    assert set(ROOM_TEMPLATES) <= names
    assert {"bedtime", "roomba", "bathroom_fan", "fridge_guard"} <= names
    assert all(manifest.ok for manifest in report.reports)


def test_an_empty_directory_is_not_a_pass(tmp_path: Path) -> None:
    """A directory holding no pack fails, because nothing is not the same as fine.

    Falsified by `ok` computed as "no finding was raised", which is vacuously true
    of a directory the verb never read -- the reading that makes a mistyped path
    and a directory of nine good packs the same answer.
    """
    report = pack_verbs.validate_directory(tmp_path)
    assert report.checked == 0
    assert not report.ok
    assert report.reports == ()


def test_a_directory_that_is_not_there_is_a_usage_error(tmp_path: Path) -> None:
    """A path that is not a directory is the caller's mistake, not a pack's.

    Falsified by reporting it as a pack failure: no pack was named invalid,
    because no pack was reached, and a caller who mistyped a path has a different
    thing to fix than a pack author does.
    """
    with pytest.raises(pack_verbs.UsageError) as raised:
        pack_verbs.validate_directory(tmp_path / "no-such-directory")
    assert "no-such-directory" in raised.value.about
    assert raised.value.reason


def test_one_bad_pack_is_named_among_good_ones(tmp_path: Path) -> None:
    """A directory of three manifests reports the one that is wrong, by path.

    Falsified by a verb that stops at the first failure, or that reports a
    directory-level verdict with no manifest named -- a caller with nine packs
    needs to know which one, and a verb that refuses the whole directory for one
    pack's sake cannot be run over a tree that is being fixed.
    """
    tree = _tree(
        tmp_path,
        ("aaa-good.yaml", _document("good", ["light.turn_on"])),
        ("mmm-unparsable.yaml", "kind: [pack\n  name: :\n"),
        (
            "zzz-unpublished.yaml",
            _document("unpublished", ["light.turn_on"], kind="lighting"),
        ),
        ("notes.yaml", {"about": "not a manifest"}),
    )
    report = pack_verbs.validate_directory(tree, root=tree)
    assert not report.ok
    assert report.checked == 3
    assert [manifest.path for manifest in report.reports] == [
        "aaa-good.yaml",
        "mmm-unparsable.yaml",
        "zzz-unpublished.yaml",
    ]
    assert report.reports[0].ok
    assert [manifest.path for manifest in report.reports if not manifest.ok] == [
        "mmm-unparsable.yaml",
        "zzz-unpublished.yaml",
    ]


def test_a_pinned_file_beside_a_pack_is_not_a_candidate(tmp_path: Path) -> None:
    """The file a pack confers is not itself read as a pack.

    Falsified by counting every `.yaml` file in the directory as a manifest: the
    pinned automation is a candidate by extension and by nothing else, and a verb
    that reported it would report a failure for every pack that confers anything.
    """
    tree = _tree(tmp_path, ("pack.yaml", _document("pack", ["light.turn_on"])))
    report = pack_verbs.validate_directory(tree, root=tree)
    assert [manifest.path for manifest in report.reports] == ["pack.yaml"]
    assert report.ok


def test_an_unparsable_file_is_a_finding_and_not_a_skip(tmp_path: Path) -> None:
    """A file nothing can read is reported under `schema`, and is not silent.

    Falsified by dropping a candidate the loader refused: a file in a pack
    directory that cannot be parsed is the one case where silence would be read as
    approval, so it is a finding and `unreadable` stays for the files that could
    not even be opened.
    """
    tree = _tree(tmp_path, ("broken.yaml", "kind: [pack\n  name: :\n"))
    report = pack_verbs.validate_directory(tree, root=tree)
    assert report.unreadable == ()
    assert [manifest.path for manifest in report.reports] == ["broken.yaml"]
    assert _classes(report.reports[0]) == ["schema"]
    assert not report.ok


def test_each_failure_carries_its_class(tmp_path: Path) -> None:
    """A schema failure and a sandbox refusal are reported under different classes.

    Falsified by one class for everything, or by an unclassified finding: the
    kinds are the reason `validate` is a verb and not a boolean, so a manifest
    refused by the enum and one refused for a banned service must not read alike.
    """
    tree = _tree(
        tmp_path,
        ("banned.yaml", _document("banned", ["homeassistant.restart"])),
        ("kind.yaml", _document("kind", ["light.turn_on"], kind="lighting")),
    )
    report = pack_verbs.validate_directory(tree, root=tree)
    by_path = {manifest.path: manifest for manifest in report.reports}
    assert _classes(by_path["kind.yaml"]) == ["schema"]
    assert _classes(by_path["banned.yaml"]) == ["banned_service"]
    assert all(
        finding.klass and finding.message
        for manifest in report.reports
        for finding in manifest.findings
    )


def test_a_class_the_map_does_not_name_is_reported_under_its_own_reason(
    tmp_path: Path,
) -> None:
    """A refusal no class claims is still reported, under its own name.

    Falsified by a map used as a whitelist: a slot a behaviour names and the pack
    does not declare is a structural failure this phase's list does not name, and
    the requirement says such a failure is reported with its constraint named
    rather than dropped for not fitting.
    """
    tree = _tree(
        tmp_path,
        (
            "unclaimed.yaml",
            _document(
                "unclaimed",
                ["light.turn_on"],
                behaviours=[
                    {
                        "name": "act",
                        "action": "service",
                        "services": ["light.turn_on"],
                        "slots": ["ambient_light_sensor"],
                    }
                ],
            ),
        ),
    )
    report = pack_verbs.validate_directory(tree, root=tree)
    classes = _classes(report.reports[0])
    assert "slot_not_declared" in classes
    assert "slot_not_declared" not in pack_verbs._CLASSES


def test_the_class_map_invents_no_reason(tmp_path: Path) -> None:
    """Every key of the class map is a reason one of the two modules declares.

    Falsified by a key that is a plausible-sounding class no check produces: the
    map's whole claim is that it folds the reasons the engine has, so a key
    neither `engine.manifest.REASONS` nor `engine.sandbox.REASONS` carries is a
    class nothing can reach.
    """
    assert set(pack_verbs._CLASSES) <= set(engine_manifest.REASONS) | set(
        sandbox.REASONS
    )


def test_the_vocabulary_comes_from_the_project_and_not_from_the_tree_root(
    tmp_path: Path,
) -> None:
    """A pack tree outside the repository validates when given its own root.

    Falsified by one root doing both jobs: `provides` resolves against the tree a
    pack lives in, and the schema, the licence list and the vocabulary are the
    project's. A single root either cannot validate a copied tree or lets a caller
    validate against a vocabulary they supplied, and both are the same defect.
    """
    tree = _tree(tmp_path, ("pack.yaml", _document("outside", ["light.turn_on"])))
    assert not pack_verbs.validate_directory(tree).ok
    report = pack_verbs.validate_directory(tree, root=tree)
    assert report.ok
    assert report.reports[0].findings == ()


# -- 10.4: diff-permissions over two manifests ------------------------------


def test_a_gained_permission_is_a_gain_and_a_removed_one_is_a_loss(
    tmp_path: Path,
) -> None:
    """The two directions are read off the two manifests, each in its own field.

    Falsified by a symmetric difference reported as one list: a caller reading
    what a pack gained is deciding whether to install it, and a permission it
    stopped declaring is the opposite answer to that question.
    """
    tree = _tree(
        tmp_path,
        ("before.yaml", _document("before", ["light.turn_on"])),
        ("after.yaml", _document("after", ["light.turn_on", "fan.turn_on"])),
    )
    forward = pack_verbs.diff_permissions(tree / "before.yaml", tree / "after.yaml")
    assert [gain.service for gain in forward.gained] == ["fan.turn_on"]
    assert forward.lost == ()
    assert not forward.narrows

    backward = pack_verbs.diff_permissions(tree / "after.yaml", tree / "before.yaml")
    assert backward.gained == ()
    assert backward.lost == ("fan.turn_on",)
    assert backward.narrows


def test_a_gained_permission_is_classified_flagged_or_banned(tmp_path: Path) -> None:
    """Each gain carries the class installation would apply, asked early.

    Falsified by a diff that lists services without judging them: the point of
    reading two manifests before installing either is that a pack that grew a
    banned call, or a flagged one, is visible while it can still be refused.
    """
    tree = _tree(
        tmp_path,
        ("before.yaml", _document("before", ["light.turn_on"])),
        (
            "after.yaml",
            _document(
                "after",
                [
                    "light.turn_on",
                    "lock.unlock",
                    "homeassistant.restart",
                    "fan.turn_on",
                ],
            ),
        ),
    )
    diff = pack_verbs.diff_permissions(tree / "before.yaml", tree / "after.yaml")
    assert {gain.service: gain.klass for gain in diff.gained} == {
        "fan.turn_on": "plain",
        "lock.unlock": "flagged",
        "homeassistant.restart": "banned",
    }


def test_a_gain_names_the_behaviour_clause_that_declares_it(tmp_path: Path) -> None:
    """Each gain points at the clause a reader can go to.

    Falsified by a gain that names no behaviour, which leaves a reader holding a
    service and no place in the file to look -- the manifest's `services` clauses
    are where a permission is declared, so a diff that does not name one is a
    report of the effect without its cause.
    """
    tree = _tree(
        tmp_path,
        ("before.yaml", _document("before", ["light.turn_on"])),
        (
            "after.yaml",
            _document(
                "after",
                ["light.turn_on"],
                behaviours=[
                    {"name": "act", "action": "service", "services": ["light.turn_on"]},
                    {
                        "name": "vent",
                        "action": "service",
                        "services": ["fan.turn_on"],
                    },
                ],
            ),
        ),
    )
    diff = pack_verbs.diff_permissions(tree / "before.yaml", tree / "after.yaml")
    assert [(gain.service, gain.behaviour) for gain in diff.gained] == [
        ("fan.turn_on", "vent")
    ]


def test_diff_permissions_reads_a_manifest_the_schema_would_refuse(
    tmp_path: Path,
) -> None:
    """The diff runs over a document validation refuses, because it must.

    Falsified by a `diff-permissions` that validates first: the question is asked
    before installation is decided, and a verb that needed the manifest to be
    valid would answer it only for packs that no longer need the answer.
    """
    tree = _tree(
        tmp_path,
        ("before.yaml", _document("before", ["light.turn_on"])),
        (
            "after.yaml",
            _document("after", ["light.turn_on", "fan.turn_on"], kind="lighting"),
        ),
    )
    assert not pack_verbs.validate_directory(tree, root=tree).reports[0].ok
    diff = pack_verbs.diff_permissions(tree / "before.yaml", tree / "after.yaml")
    assert [gain.service for gain in diff.gained] == ["fan.turn_on"]


def test_diff_permissions_refuses_a_manifest_it_cannot_read(tmp_path: Path) -> None:
    """A file that is not a pack is a usage error, not an empty diff.

    Falsified by returning no gains for a manifest nothing could read: an empty
    diff for an unreadable file says "this version grants what the last one did",
    which is the one wrong answer a caller would act on.
    """
    base = _tree(tmp_path, ("before.yaml", _document("before", ["light.turn_on"])))
    with pytest.raises(pack_verbs.UsageError):
        pack_verbs.diff_permissions(base / "before.yaml", tmp_path / "absent.yaml")
    with pytest.raises(pack_verbs.UsageError):
        pack_verbs.diff_permissions(base / "before.yaml", base / "notes.yaml")


# -- 10.6: the three exit statuses ------------------------------------------


def test_the_three_exit_statuses_are_distinct() -> None:
    """Success, a pack failure and a usage error are three different numbers.

    Falsified by a verb that exits zero for a failing pack, or by two statuses
    sharing a value: a script driving this has to tell "the pack is broken" from
    "I called it wrong", and neither from a clean run.
    """
    statuses = {
        pack_verbs.EXIT_OK,
        pack_verbs.EXIT_PACK_FAILURE,
        pack_verbs.EXIT_USAGE,
    }
    assert len(statuses) == 3
    assert statuses == {0, 1, 2}


def test_a_skipped_manifest_is_never_counted_as_passing(tmp_path: Path) -> None:
    """A directory whose only YAML file is not a pack reports nothing checked.

    Falsified by counting candidates rather than manifests: `checked` is what a
    caller reads to know the verb reached anything, so a file that was never a
    candidate must not raise it, and an absent set must not read as a pass.
    """
    tree = _tree(tmp_path, ("notes.yaml", {"about": "not a manifest"}))
    report = pack_verbs.validate_directory(tree, root=tree)
    assert report.checked == 0
    assert not report.ok
    assert report.unreadable == ()
