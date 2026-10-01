"""The shipped set -- task 7.4's pack, and the set the phase promises.

Two things live here, and they are at opposite ends of the phase's state.

The green half is guest mode. It is the six entries' one `profile-set`, and what
can be asserted about it today is real: the pack declares a `mode` and only a
mode, the artefact it pins *is* a mode as `engine.sandbox.file_class` reads one,
that document validates against `schemas/mode/1.0.0.json`, the pack installs and
confers no behaviours, the mode carries the `exclusive_group` `engine/modes.py`
acts on, and a manifest that declares room-profile selections is refused against
the current schema -- the boundary with Phase 3, stated as a refusal.

The red half is the set itself. `spec.txt:58` names six entries and the tree
ships three of them: guest mode, the default room templates, and the
corpus-derived set that task 8.1 has not yet produced. The Bedtime button, the
Roomba button and the bathroom fan are missing, and they are missing for a reason
that is not "nobody wrote the file yet" -- the frozen `1.2.0` behaviour clause
carries no parameter, so entering a mode, dispatching on a vacuum's state and
holding a `delay` for a run-on period are acts no manifest can express today.
`test_the_shipped_set_is_the_one_the_phase_names` is therefore red on purpose,
and its failure message says which entries are missing and why. It is left red
rather than marked `xfail`, because a set three entries short is the phase's most
important fact and a suite that reports it as an expected failure is a suite that
has stopped saying it.

The three blocked entries are recorded in `.local/phase2-package-state.md` with
the file and line each missing mechanism would have to be read from.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import pytest
import yaml
from jsonschema.validators import Draft202012Validator

from engine import sandbox
from engine.manifest import Manifest, validate_manifest
from engine.vocabulary import Vocabulary, load_manifest_artifacts
from openhouse.facade import open_session
from tools.catalog import paths

ROOT = paths.ROOT
PACKS = ROOT / "packs" / "official"
GUEST_MODE = PACKS / "guest-mode.yaml"
GUEST_MODE_ARTEFACT = PACKS / "guest_mode" / "mode.yaml"
MODE_SCHEMA = ROOT / "schemas" / "mode" / "1.0.0.json"

#: The pack files that are phase 0 and 1's *examples* rather than phase 2's
#: shipped set. Named here so a count of the shipped set does not include the
#: file that is the schema's demonstration rather than a pack the phase promises.
EXAMPLES = frozenset({"example-pack.yaml", "example-house.yaml", "example-export.yaml"})


def _document(path: Path) -> dict[str, object]:
    """A pack file's contents, asserting it is a mapping first."""
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(loaded, Mapping), path
    return dict(loaded)


def _shipped() -> dict[str, str]:
    """Every pack manifest in the shipped tree, as filename -> declared kind.

    A file with no `kind` is not a pack -- `example-house.yaml` is a house
    document and `example-export.yaml` an export -- so it is left out rather than
    counted as an entry of some unnamed kind.
    """
    shipped: dict[str, str] = {}
    for path in sorted(PACKS.glob("*.yaml")):
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
        if isinstance(loaded, Mapping) and "kind" in loaded:
            shipped[path.name] = str(loaded["kind"])
    return shipped


@pytest.fixture(scope="module")
def vocabulary() -> Vocabulary:
    """The committed catalog, loaded once for the module."""
    return Vocabulary.load(ROOT)


# --------------------------------------------------------------------------
# Guest mode -- the green half
# --------------------------------------------------------------------------


def test_the_guest_mode_pack_is_a_profile_set_conferring_a_mode() -> None:
    """The pack's kind, and the mode it must therefore contain.

    Falsified by a `profile-set` whose `provides` carries no `mode` entry, which
    `1.2.0`'s conditional for the kind refuses (`provides` `contains` a `mode`):
    a profile-set that confers no mode is a pack that activates nothing and says
    nothing about it.
    """
    document = _document(GUEST_MODE)
    assert document["kind"] == "profile-set"
    provides = document["provides"]
    assert isinstance(provides, list)
    assert [entry["class"] for entry in provides] == ["mode"]


def test_the_mode_document_is_what_the_pack_declares_it_to_be() -> None:
    """The pinned artefact is a mode as the sandbox reads one, not merely named so.

    Falsified by a pinned file whose keys are not a subset of the mode schema's:
    `file_class` would read it as `other` and an install would refuse the pack
    with `class_mismatch`, because the class is read off the file rather than
    trusted from the declaration beside it.
    """
    assert sandbox.file_class(GUEST_MODE_ARTEFACT) == "mode"


def test_the_mode_document_validates_against_the_mode_schema() -> None:
    """`schemas/mode/1.0.0.json` is closed, so this is the whole of a mode.

    Falsified by a key the schema does not carry -- a clause for the selections
    Phase 3 will publish, most likely -- which the closed object refuses rather
    than silently ignoring, and by a missing `exclusive_group`, which is what
    makes a mode exclusive with anything at all.
    """
    schema = _document(MODE_SCHEMA)
    document = _document(GUEST_MODE_ARTEFACT)
    Draft202012Validator(schema).validate(document)
    assert document["exclusive_group"]


def test_the_pack_installs_and_confers_no_behaviours(vocabulary: Vocabulary) -> None:
    """Installing it succeeds, and it contributes no behaviour.

    Falsified by a profile-set that arrived acting: a mode is a state a behaviour
    is *gated on*, so a pack conferring one has nothing to evaluate and no unit
    to enable. The install result is asserted to name no behaviour and the
    session to hold the pack, which is the two halves of "it installed".
    """
    session = open_session(house="minimal", vocabulary=vocabulary)
    result = session.install_pack(str(GUEST_MODE))

    assert result["installed"] is True
    assert result["pack"] == "guest_mode"
    assert tuple(result["behaviours"]) == ()
    assert tuple(result["flags"]) == ()


def test_the_shipped_mode_carries_the_exclusive_group_the_engine_acts_on() -> None:
    """The mode's `exclusive_group` is the one `engine/modes.py` clears siblings in.

    Falsified by a mode shipped with no group, or with one no other mode shares:
    the engine's rule is a rule about a group's members, so a mode that names a
    group of one is exclusive with nothing and the pack's whole contribution
    reduces to a name.

    The engine *clears* the sibling rather than raising -- `ModeSet.activate`
    returns the names it deactivated -- so what is asserted is that the two can
    never be active together, which is the property the exclusivity is for.
    """
    from engine.modes import ModeSet

    shipped = _document(GUEST_MODE_ARTEFACT)
    group = shipped["exclusive_group"]
    sibling = {
        "name": "sleep",
        "description": "The household is asleep.",
        "exclusive_group": group,
    }
    modes = ModeSet([shipped, sibling], vocabulary=Vocabulary.load(ROOT))

    assert modes.activate("guest") == ()
    assert modes.activate("sleep") == ("guest",)
    assert modes.active == frozenset({"sleep"})


def test_a_manifest_declaring_room_profile_selections_is_refused(
    vocabulary: Vocabulary,
) -> None:
    """The boundary with Phase 3, as a refusal rather than an omission.

    Falsified by a schema that ignored an unrecognised clause: a manifest could
    then declare room-profile selections in whatever shape its author chose, and
    the phase would have invented a later phase's format by accepting it. The
    clause is refused by `additionalProperties: false` and the failure names the
    clause, so a reader learns *why* rather than that something was rejected.
    """
    document = _document(GUEST_MODE)
    document["profiles"] = [{"room": "kitchen", "profile": "guest"}]
    result = validate_manifest(
        Manifest(path=GUEST_MODE, document=document),
        load_manifest_artifacts(ROOT),
        vocabulary,
    )

    # JSON Schema reports an `additionalProperties` violation at the object's own
    # path -- the pointer is `<document>`, and the offending name is carried in
    # the message -- so what is asserted is that the refusal *names* the clause,
    # which is what a reader needs to know which one to remove.
    assert not result.ok
    assert any("profiles" in failure.message for failure in result.failures)


# --------------------------------------------------------------------------
# The set -- red on purpose
# --------------------------------------------------------------------------


def test_the_shipped_set_is_the_one_the_phase_names() -> None:
    """The six entries `spec.txt:58` names, of which the tree ships three.

    Red rather than `xfail`, and the message below is the point of the test: the
    three missing entries are not unwritten files but inexpressible ones. `1.2.0`
    closes `$defs.behaviour` to `name, trigger, condition, action, priority,
    services, slots` and none of the seven carries a value, so `DeclaredBehaviour`
    can propose exactly one thing --
    `ctx.propose(slot=slot, action=service, rule=self.name)`
    (`engine/behaviours/declared.py:146`). Entering Sleep mode needs an option no
    clause carries, the four Roomba states need a dispatch `choose` is forbidden
    from providing, and the fan's run-on period needs a duration `action: delay`
    has nowhere to put. So the shipped set is three short, and the phase's exit
    criterion -- every shipped pack validates and passes its scenario -- would
    report green with all three absent, because `delay` is a legal `action` and
    a scenario that never runs its timer cannot fail.
    """
    shipped = _shipped()
    modules = {
        name
        for name, kind in shipped.items()
        if kind == "module" and name not in EXAMPLES
    }
    derived = ROOT / "packs" / "derived"
    produced = (
        sorted(path.name for path in derived.glob("*.yaml")) if derived.is_dir() else []
    )

    missing = []
    if len(modules) < 3:
        missing.append(
            f"the three `module` packs -- the Bedtime button, the Roomba button and "
            f"the bathroom fan -- of which the tree ships {len(modules)} "
            f"({sorted(modules)}), because no `1.2.0` clause can express entering a "
            "mode, dispatching on a state or holding a delay"
        )
    if not produced:
        missing.append(
            "the corpus-derived set, which task 8.1 has not emitted -- `packs/derived/` "
            "is empty or absent"
        )
    assert not missing, "the shipped set is incomplete: " + "; ".join(missing)
