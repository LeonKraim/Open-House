"""What blocks the pack triggers -- the gap `pack-triggers` states and this phase does not close.

`spec.txt:59` names "triggers from a dashboard button or a physical button", and
`openspec/changes/phase-2-packs/specs/pack-triggers` spells out what each half
needs. Neither half can be built from the paths this phase owns, and this module
records *why* as executable facts rather than as a note: every assertion below
names the one file whose change would falsify it, so the phase that closes the
gap deletes the test that pinned it.

**A physical button needs a slot, and the vocabulary carries none.**
`pack-triggers` requires a press to reach its behaviour "through a slot the
behaviour names in its own `slots` clause" and forbids the pack naming the
button's device id. `catalog/slots.yaml` declares no button, remote or press
role -- it is the frozen controlled vocabulary of `tools/catalog/lexicon.py` --
so there is no slot for a pack to name, and `catalog/` is not a path this phase
owns. That half is blocked on `catalog/slots.yaml`. (`trigger: device` and
`trigger: event` already validate, so the manifest side of a physical button is
ready; only the slot is missing.)

**A dashboard button needs `user_action` to name a behaviour, and it cannot.**
`pack-triggers` requires a dashboard press to be "delivered as a `user_action`
naming the behaviour". Phase 1's `user_action(entity_id, state)` takes an entity
and a state and nothing that could name a pack's behaviour, and both the facade
and the operation registry it is spelled in -- `openhouse/facade.py` and
`openhouse/operations.py` -- are outside this phase's paths.

**A trigger binding has nowhere to be declared.** `pack-manifest/1.2.0` is closed
(`additionalProperties: false`), so a top-level `triggers` clause is refused
however it is written, and the behaviour's own clauses carry the trigger *kind*
but no binding to a button.

**The interpreter reads no trigger.** `DeclaredBehaviour`'s facts are the
manifest's and its field list does not include the trigger, so a declared
behaviour is proposed on every evaluation rather than when its trigger fires --
the engine-side half of "a trigger decides when one is evaluated" is not written
either, and `engine/behaviours/declared.py` is an existing file this phase does
not own.

Each test therefore asserts a blocker, not a capability. A green run here means
the gap is open, which is what the phase reports.
"""

from __future__ import annotations

import dataclasses
import inspect
import itertools
from pathlib import Path
from typing import Any

import yaml

from engine.behaviours.declared import DeclaredBehaviour
from engine.vocabulary import load_behaviour_vocabulary
from openhouse import pack_verbs
from openhouse.facade import OpenHouse
from openhouse.operations import OPERATIONS
from tools.catalog import paths

#: The trigger kinds `pack-triggers` says a physical button must use: `device`
#: for a device automation's press and `event` for a button's event. Both are
#: published, so a pack can declare one today.
PRESS_KINDS = ("device", "event")

#: Terms that would name a button, a press or a remote on the trigger axis. None
#: is published, and `pack-triggers` forbids inventing one.
BUTTON_TERMS = frozenset({"button", "press", "remote", "switch"})

#: A per-session counter, so two packs in one test land in two trees rather than
#: one. Named by the trigger so a failure reads as the pack it was about.
_TREES = itertools.count()


def _pack(tmp_path: Path, *, trigger: str, **extra: object) -> Path:
    """Write one pack whose single behaviour declares `trigger`, and return its tree.

    The pinned automation is written beside the manifest so the only finding a
    validation can raise is the one the trigger names -- a `provides` path left
    dangling would otherwise fail a test about the trigger for a reason the test
    is not about.
    """
    tree = tmp_path / f"{trigger}-{next(_TREES)}"
    (tree / "probe").mkdir(parents=True)
    (tree / "probe" / "act.yaml").write_text(
        yaml.safe_dump({"alias": "pinned", "trigger": [], "action": []}),
        encoding="utf-8",
        newline="\n",
    )
    document: dict[str, Any] = {
        "name": "probe",
        "version": "1.0.0",
        "description": "A pack whose one behaviour names one trigger kind.",
        "kind": "module",
        "engine_api": ">=1.0.0 <2.0.0",
        "license": "mit",
        "requires_slots": ["motion_sensor"],
        "optional_slots": [],
        "provides": [{"path": "probe/act.yaml", "class": "automation"}],
        "behaviours": [
            {
                "name": "press",
                "trigger": trigger,
                "action": "service",
                "services": ["light.turn_on"],
                "slots": ["motion_sensor"],
            }
        ],
        "i18n": {"default": {"pack": "probe", "description": "d", "press": "p"}},
    }
    document.update(extra)
    (tree / "pack.yaml").write_text(
        yaml.safe_dump(document, sort_keys=False), encoding="utf-8", newline="\n"
    )
    return tree


def _klasses(tree: Path) -> list[str]:
    """The classes every finding against the one manifest in `tree` raised."""
    report = pack_verbs.validate_directory(tree, root=tree)
    return [
        finding.klass for manifest in report.reports for finding in manifest.findings
    ]


# -- the vocabulary publishes the press kinds and no button term --------------


def test_the_vocabulary_publishes_the_two_press_kinds_and_no_button_term() -> None:
    """`device` and `event` are published triggers; no button term is.

    This half is ready and that is the fact to keep: a physical button is meant to
    use the two published kinds, and `pack-triggers` forbids adding a button term
    to a vocabulary whose description fixes its terms as what the four estates
    were observed to use. Falsified by a button or press term appearing on the
    trigger axis, which would be the term the requirement says not to add.
    """
    published = load_behaviour_vocabulary(paths.ROOT)
    assert set(PRESS_KINDS) <= published.triggers
    assert not published.triggers & BUTTON_TERMS


def test_a_behaviour_may_declare_a_device_or_event_trigger(tmp_path: Path) -> None:
    """A behaviour naming `device` or `event` validates, so the manifest half is ready.

    Falsified by either press kind being refused: `pack-triggers` resolves a
    physical button to one of these two, so a manifest that cannot name them
    could not express a physical-button trigger at all -- and this is the half
    the phase *can* state as working, unlike the slot that would bind the button.
    """
    for kind in PRESS_KINDS:
        tree = _pack(tmp_path, trigger=kind)
        report = pack_verbs.validate_directory(tree, root=tree)
        assert report.ok, [f.message for m in report.reports for f in m.findings]


def test_a_button_trigger_term_is_refused(tmp_path: Path) -> None:
    """`trigger: button` fails validation, naming the term and the axis.

    Falsified by the vocabulary publishing a button term -- the sentence above
    read the other way: the term is refused today, which is what makes inventing
    one a vocabulary change rather than a spelling this phase could choose.
    """
    tree = _pack(tmp_path, trigger="button")
    report = pack_verbs.validate_directory(tree, root=tree)
    assert not report.ok
    messages = " ".join(f.message for m in report.reports for f in m.findings)
    assert "button" in messages
    assert "unknown_term" in _klasses(tree)


# -- a trigger binding has nowhere to be declared -----------------------------


def test_a_manifest_cannot_declare_a_trigger_binding_of_its_own(
    tmp_path: Path,
) -> None:
    """A top-level `triggers` clause is refused, even when it is an empty list.

    Falsified by the schema having a place for a trigger binding. `pack-manifest`
    is closed, so the clause a pack would use to bind a button to a behaviour is
    an `additionalProperties` failure rather than a feature -- and the empty list
    is refused too, which is what shows the closure rather than the contents is
    the reason.
    """
    for binding in ([{"behaviour": "press", "kind": "device"}], []):
        tree = _pack(tmp_path, trigger="device", triggers=binding)
        assert not pack_verbs.validate_directory(tree, root=tree).ok
        assert "schema" in _klasses(tree)
        messages = " ".join(
            finding.message
            for manifest in pack_verbs.validate_directory(tree, root=tree).reports
            for finding in manifest.findings
        )
        assert "triggers" in messages


# -- the interpreter keeps no trigger among its facts -------------------------


def test_the_declared_interpreter_keeps_no_trigger_among_its_facts() -> None:
    """`DeclaredBehaviour`'s fields are the manifest's minus the trigger.

    Falsified by a `trigger` field appearing on the interpreter's fact set, which
    is what the engine-side half of "a trigger decides when one is evaluated"
    would need: today a declared behaviour is proposed on every evaluation, so the
    trigger a manifest names is stored nowhere the engine reads and the press
    cannot gate it.
    """
    names = {unit.name for unit in dataclasses.fields(DeclaredBehaviour)}
    assert "trigger" not in names
    assert names == {
        "pack",
        "name",
        "services",
        "slots",
        "required_slots",
        "optional_slots",
        "priority",
        "scope",
        "corpus_rows",
        "defaults",
    }


# -- a physical button has no slot to be reached through ----------------------


def test_no_slot_binds_a_physical_button() -> None:
    """`catalog/slots.yaml` declares no button role and accepts no button domain.

    Falsified by a button, remote or press role being added to the slot
    vocabulary. `pack-triggers` requires a press to be reached through a slot the
    behaviour names in `slots`, and the slot has to exist before a pack can name
    it -- so this is the file whose change unblocks the physical-button half, and
    it is one this phase does not own.
    """
    document = yaml.safe_load((paths.CATALOG / "slots.yaml").read_text("utf-8"))
    slots = document["slots"]
    domains = {domain for slot in slots for domain in slot.get("accepts_domains", [])}
    names = " ".join(slot["name"] for slot in slots).lower()
    assert not domains & BUTTON_TERMS
    assert not any(term in names for term in BUTTON_TERMS)


# -- a dashboard button cannot name a behaviour -------------------------------


def test_no_operation_names_a_pack_behaviour() -> None:
    """`user_action` takes an entity and a state and no behaviour to name.

    Falsified by `user_action` growing a `behaviour` parameter, which is what
    "a dashboard button is delivered as a `user_action` naming the behaviour"
    would need. The registry and the facade are the two spellings of the one
    signature, so both are read -- and both live outside this phase's paths.
    """
    parameters = [parameter.name for parameter in OPERATIONS["user_action"].parameters]
    assert parameters == ["entity_id", "state"]
    signature = inspect.signature(OpenHouse.user_action)
    assert list(signature.parameters) == ["self", "entity_id", "state"]
    assert not any("behaviour" in name for name in parameters)


def test_no_operation_in_the_registry_reaches_a_behaviour() -> None:
    """No name in the ten-operation registry is a pack behaviour.

    Falsified by an operation whose parameter set includes a behaviour to invoke:
    the registry is `spec.txt`'s closed ten and a press that named a behaviour
    would have to arrive through one of them, so the absence is the fact that
    makes the dashboard button unbuildable without widening the registry.
    """
    for name, operation in OPERATIONS.items():
        assert all(
            "behaviour" not in parameter.name for parameter in operation.parameters
        ), name
