"""One module holding another off: `pack-manifest`'s `suppresses`, end to end.

The clause exists so that a pack can switch a *whole other module* off while it
is running, and the property that makes it worth having is that it is temporary:
nothing about the target is written, so its own switch reads back where its
person left it and the target returns the moment the holder goes off. That is a
claim about *where the answer lives* -- derived from which modules are on, never
stored against the target -- and the only way to test it is to look for the write
that should not be there.

Two packs are written to a temporary tree (`tests/packfactory`) and installed
into the `minimal` fixture, whose `light_group` both packs reach through, so the
records under assertion are real evaluations the engine arbitrated rather than
fields a builder stored. Three facts are pinned:

- **The target's atoms are skipped, and the record says by whom.** The outcome is
  `skipped: suppressed` and the input names the holder's pack and the behaviour
  of it that declared the clause, which is the sentence a person reading the
  Activity tab is looking for.
- **The target's settings are untouched.** The target's own switch still resolves
  at the layer the person set it at, so "held off" is distinguishable from
  "switched off" in the configuration and not only in the log.
- **The holder going off releases it.** The suppression is derived on read, so
  switching the holder's atom off is the whole of the release.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from engine.behaviours import enable_key, module_enable_key
from engine.behaviours import suppresses as declared_suppresses
from engine.behaviours.declared import declared_units
from engine.binding import HouseScope
from engine.config import Layer
from engine.decision_log import ModuleSuppression, Outcome
from engine.manifest import Manifest, validate_manifest
from engine.vocabulary import Vocabulary, load_manifest_artifacts
from openhouse.facade import OpenHouse, open_session
from tools.catalog import paths

from .packfactory import SLOT, pack

ROOT = paths.ROOT

#: One behaviour per generated pack, from `packfactory`'s `b{index}` naming.
UNIT = "b0"

#: The clause spliced into the *holder's* manifest. Anchored on the behaviour's
#: own `slots` line rather than appended at the end of the file, because the
#: clause belongs to a behaviour and a line added after `i18n` would be a
#: document the schema refuses -- which would make this module a test of YAML
#: rather than of the suppression.
SUPPRESSES_TARGET = (f"    slots: [{SLOT}]\n    suppresses: [target]",)


def _suppresses(names: str) -> tuple[tuple[str, str], ...]:
    """The edit that gives the generated pack's one behaviour a `suppresses`."""
    return ((f"    slots: [{SLOT}]", f"    slots: [{SLOT}]\n    suppresses: {names}"),)


@pytest.fixture(scope="module")
def vocabulary() -> Vocabulary:
    """The committed catalog, loaded once for the module."""
    return Vocabulary.load(ROOT)


def _settings(*names: str) -> dict[str, object]:
    """Both flags a pack's single behaviour needs, for each pack named."""
    flags: dict[str, object] = {}
    for name in names:
        flags[module_enable_key(name)] = True
        flags[enable_key(f"{name}.{UNIT}")] = True
    return flags


def _session(
    tmp_path: Path,
    vocabulary: Vocabulary,
    *,
    holder: str = "holder",
    target: str = "target",
    suppresses: str | None = "[target]",
) -> OpenHouse:
    """A session with both packs installed and both switched on.

    The holder's clause is a parameter because two tests need it absent or
    pointing elsewhere: the property under test is what the *clause* does, so a
    test that could not vary it would be testing the fixture.
    """
    session = open_session(
        house="minimal",
        vocabulary=vocabulary,
        house_settings=_settings(holder, target),
    )
    session.install_pack(
        str(
            pack(
                tmp_path,
                holder,
                edits=() if suppresses is None else _suppresses(suppresses),
            )
        )
    )
    session.install_pack(str(pack(tmp_path, target)))
    return session


def _of(session: OpenHouse, actor: str) -> tuple:
    """The records one pack's one behaviour left this tick, one per room."""
    return tuple(
        record
        for record in session.advance_time(minutes=1)
        if record.actor == f"{actor}.{UNIT}"
    )


# -- the clause holds the target off -----------------------------------------


def test_a_suppressing_behaviour_holds_the_target_off(
    tmp_path: Path, vocabulary: Vocabulary
) -> None:
    """The target's atoms are skipped, and the record names the holder.

    Falsified by a gate that reported the plain `skipped: disabled`: the two
    reads the same in the log, and the person whose module is on but not running
    would be told to look at a switch that is already where they want it.
    """
    session = _session(tmp_path, vocabulary)
    records = _of(session, "target")

    assert records, "the target's behaviour left no record at all"
    assert {record.outcome for record in records} == {Outcome.SKIPPED_SUPPRESSED}
    shown = {
        (entry.module, entry.by, entry.behaviour)
        for record in records
        for entry in record.inputs
        if isinstance(entry, ModuleSuppression)
    }
    assert shown == {("target", "holder", f"holder.{UNIT}")}


def test_the_holder_itself_still_runs(tmp_path: Path, vocabulary: Vocabulary) -> None:
    """Suppressing is not symmetric: the holder is not held off by its own act.

    Falsified by a reading that counted the holder among the suppressed -- which
    a self-referential derivation would do, and which would make the clause
    useless the moment it was used.
    """
    session = _session(tmp_path, vocabulary)
    outcomes = {record.outcome for record in _of(session, "holder")}
    assert Outcome.SKIPPED_SUPPRESSED not in outcomes


def test_the_target_s_own_switch_is_left_where_its_person_put_it(
    tmp_path: Path, vocabulary: Vocabulary
) -> None:
    """Held off is not switched off: no layer of the target's config changed.

    Falsified by an implementation that expressed the suppression as a write --
    an override on the target's module flag, or on its atoms' flags. That write
    would be the module being *switched off* by another module, it would survive
    the holder going away, and the target's own switch would read back off.

    The layer is asserted rather than only the value, because the value is `True`
    either way: the point is that the setting is still the *house* one the person
    set and not an override this derivation placed on top of it.
    """
    session = _session(tmp_path, vocabulary)

    module_flag = session.engine.resolve_or(
        module_enable_key("target"), HouseScope(), False
    )
    atom_flag = session.engine.resolve_or(
        enable_key(f"target.{UNIT}"), HouseScope(), False
    )
    assert module_flag.layer is Layer.HOUSE
    assert atom_flag.layer is Layer.HOUSE


def test_turning_the_holder_off_releases_the_target(
    tmp_path: Path, vocabulary: Vocabulary
) -> None:
    """The release is the holder's switch and nothing else.

    Falsified by a suppression stored at the moment it was derived: a stored
    suppression would outlive the thing that caused it, and the target would stay
    down until somebody found and cleared it.
    """
    session = _session(tmp_path, vocabulary)
    assert {record.outcome for record in _of(session, "target")} == {
        Outcome.SKIPPED_SUPPRESSED
    }

    session.engine.settings.set_house_setting(enable_key(f"holder.{UNIT}"), False)
    released = _of(session, "target")
    assert Outcome.SKIPPED_SUPPRESSED not in {record.outcome for record in released}


def test_a_pack_naming_a_pack_nobody_installed_holds_nothing_off(
    tmp_path: Path, vocabulary: Vocabulary
) -> None:
    """A clause may name a pack this house does not have, and nothing happens.

    A manifest does not know which house it lands in (`mode` is checked the same
    way), so naming a pack that is not installed has to be a no-op rather than a
    failure or a phantom suppression.
    """
    session = _session(tmp_path, vocabulary, suppresses="[ghost]")
    outcomes = {
        record.outcome
        for record in session.advance_time(minutes=1)
        if record.actor.startswith(("holder.", "target."))
    }
    assert Outcome.SKIPPED_SUPPRESSED not in outcomes


def test_two_packs_naming_each_other_both_go_quiet(
    tmp_path: Path, vocabulary: Vocabulary
) -> None:
    """A cycle is a fixpoint, not a hang.

    Each pack is switched on, so each holds the other off, and both are skipped.
    This is the shape that would recurse forever under a derivation that asked
    "is the suppressor running?" through the suppression itself -- which is why
    the engine asks that question with the suppression stage removed.
    """
    session = open_session(
        house="minimal",
        vocabulary=vocabulary,
        house_settings=_settings("left", "right"),
    )
    session.install_pack(str(pack(tmp_path, "left", edits=_suppresses("[right]"))))
    session.install_pack(str(pack(tmp_path, "right", edits=_suppresses("[left]"))))
    records = session.advance_time(minutes=1)
    skipped = {
        record.actor
        for record in records
        if record.outcome is Outcome.SKIPPED_SUPPRESSED
    }
    assert skipped == {f"left.{UNIT}", f"right.{UNIT}"}


# -- the manifest clause itself ----------------------------------------------


def test_a_pack_naming_its_own_name_is_refused(
    tmp_path: Path, vocabulary: Vocabulary
) -> None:
    """A self-suppression is a module that can never run, and is refused.

    Refused at validation rather than at evaluation because by then the module is
    installed and switched on and simply never acts -- the failure mode hardest
    to trace from the outside, and one no switch explains.
    """
    path = pack(tmp_path, "ouroboros", edits=_suppresses("[ouroboros]"))
    result = validate_manifest(
        Manifest(path=path, document=_document(path)),
        load_manifest_artifacts(ROOT),
        vocabulary,
    )
    assert [failure.reason for failure in result.failures] == ["self_suppression"]


def test_the_clause_reaches_the_unit_the_engine_reads(
    tmp_path: Path, vocabulary: Vocabulary
) -> None:
    """The manifest's clause survives the projection into the engine's facts.

    The engine reads `suppresses` off the unit rather than off the document, so a
    clause dropped by the builder would be a manifest that validated and did
    nothing.
    """
    path = pack(tmp_path, "publisher", edits=_suppresses("[target]"))
    document = _document(path)
    units = declared_units(
        "publisher",
        document,
        default_priority=0,
        service_states={},
    )
    assert [declared_suppresses(unit) for unit in units] == [("target",)]


def _document(path: Path) -> dict[str, object]:
    """A generated manifest, as the mutable document the validator takes."""
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return dict(loaded)
