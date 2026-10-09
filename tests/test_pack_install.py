"""The install lifecycle: resolving a pack, refusing it, recording it, removing it.

`pack-install`'s whole surface, and the half of `engine/install.py` a house is
needed to reach. The resolver is a pure function over an installed set and an
arrival, so its checks are driven directly here -- including the cycle check,
which an incremental install can never reach, because installing `a` requires `b`
first and a loop therefore closes only over a set that already holds an
unsatisfied dependency rather than over one built by installing.

Every check names the implementation that would falsify it. The recurring shape
is that a refusal is one defect with one remedy: a pack the schema refused is not
a pack whose slots were checked, and a refused install leaves a house nothing
happened to.

The packs are generated rather than committed, because most of what is under test
is a manifest *wrong in one way* -- a range nothing satisfies, a conflict, a
banned service -- and a committed fixture per failure mode would be a directory
of files whose defects a reader has to diff to find.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
import yaml

from engine import install as pack_install
from engine.behaviours import enable_key, module_enable_key
from engine.decision_log import DecisionRecord, Outcome
from engine.engine import EngineError
from engine.vocabulary import Vocabulary
from openhouse import packs
from openhouse.facade import open_session
from tools.catalog import paths

from .packfactory import SLOT, pack

if TYPE_CHECKING:
    from openhouse.facade import OpenHouse

ROOT = paths.ROOT
EXAMPLE = ROOT / "packs" / "official" / "example-pack.yaml"


def _unit_records(session: OpenHouse, unit: str) -> tuple[DecisionRecord, ...]:
    """Every record the unit left, whatever it decided."""
    return tuple(
        record for record in session.get_decision_log() if record.actor == unit
    )


@pytest.fixture(scope="module")
def vocabulary() -> Vocabulary:
    """The committed catalog, loaded once for the module."""
    return Vocabulary.load(ROOT)


@pytest.fixture
def session(vocabulary: Vocabulary) -> OpenHouse:
    """A session over the `minimal` fixture, which binds `light_group`."""
    return open_session(house="minimal", vocabulary=vocabulary)


# -- 5.1  dependencies and conflicts ---------------------------------------


def test_a_satisfied_dependency_is_recorded_with_the_version_that_answered_it(
    tmp_path: Path, session: OpenHouse
) -> None:
    """A dependency the installed set satisfies admits the pack, and is recorded.

    Falsified by a record that kept only the declared range: a person reading why
    a later uninstall is refused cannot see which version answered, and the
    re-check after a version change has nothing to read.
    """
    session.install_pack(str(pack(tmp_path, "base", version="1.4.2")))
    result = session.install_pack(
        str(pack(tmp_path, "dependent", dependencies=(("base", ">=1.0.0 <2.0.0"),)))
    )
    assert result["dependencies"] == (
        {"pack": "base", "range": ">=1.0.0 <2.0.0", "version": "1.4.2"},
    )

    record = session.snapshot()["engine_state"]["installed_packs"]["dependent"]
    assert record["dependencies"] == [
        {"name": "base", "range": ">=1.0.0 <2.0.0", "version": "1.4.2"}
    ]


def test_an_unsatisfied_dependency_is_refused_naming_the_pack_and_the_range(
    tmp_path: Path, session: OpenHouse
) -> None:
    """A range nothing installed answers fails, and the message names both sides.

    Falsified by a refusal naming only the pack that was refused: a reader cannot
    tell which of the pack's dependencies is the problem.
    """
    manifest = pack(tmp_path, "needy", dependencies=(("absent", ">=1.0.0"),))
    with pytest.raises(packs.PackError) as refusal:
        session.install_pack(str(manifest))
    assert refusal.value.pack == "needy"
    assert "absent" in refusal.value.reason
    assert ">=1.0.0" in refusal.value.reason
    assert "not installed" in refusal.value.reason


def test_an_installed_dependency_out_of_range_is_unsatisfied(
    tmp_path: Path, session: OpenHouse
) -> None:
    """Installed is not satisfied: the range has to admit the version that is there.

    Falsified by a check asking whether the pack was *present* rather than
    whether the version answered, which would admit a pack needing `2.x` into a
    house holding `1.0.0`.
    """
    session.install_pack(str(pack(tmp_path, "old", version="1.0.0")))
    with pytest.raises(packs.PackError) as refusal:
        session.install_pack(
            str(pack(tmp_path, "modern", dependencies=(("old", ">=2.0.0"),)))
        )
    assert "old" in refusal.value.reason


def test_a_conflict_is_refused_whichever_pack_declared_it(
    tmp_path: Path, vocabulary: Vocabulary, session: OpenHouse
) -> None:
    """A conflict is symmetric: one side declaring it is enough, in either order.

    Falsified by a check reading only the arriving pack's conflicts: the pair
    would install in whichever order put the silent pack second, so the same two
    packs would be admitted and refused by the order they arrived in -- the
    install-order dependence the exit criterion forbids.
    """
    first = pack(tmp_path, "first")
    second = pack(tmp_path, "second", conflicts=(("first", ">=1.0.0"),))

    session.install_pack(str(first))
    with pytest.raises(packs.PackError) as refusal:
        session.install_pack(str(second))
    assert "first" in refusal.value.reason

    other = open_session(house="minimal", vocabulary=vocabulary)
    other.install_pack(str(second))
    with pytest.raises(packs.PackError) as refusal:
        other.install_pack(str(first))
    assert "second" in refusal.value.reason


# -- 5.2  cycles ------------------------------------------------------------


def test_a_dependency_cycle_is_reported_as_a_cycle_not_as_unsatisfied() -> None:
    """A loop is refused as a loop, and the message names the chain.

    Falsified by a resolver that only asked whether each dependency was
    satisfied: it would report `b` as missing, which is true and useless, when the
    defect is that the two packs can never be installed in either order.

    The set is built here rather than installed, because an incremental install
    cannot reach this state: installing `a` requires `b` first, so the loop closes
    only over a set that already holds an unsatisfied dependency -- which is a
    hand-built set and not one any sequence of installs produces.
    """
    installed = pack_install.InstalledSet(
        packs={
            "a": pack_install.InstalledPack(
                name="a",
                version="1.0.0",
                digest="sha256:a",
                dependencies=((pack_install.Reference("b", ">=1.0.0"), "1.0.0"),),
            )
        }
    )
    arrival = pack_install.Arrival(
        name="b",
        version="1.0.0",
        digest="sha256:b",
        dependencies=(pack_install.Reference("a", ">=1.0.0"),),
    )
    with pytest.raises(pack_install.InstallRefusedError) as refusal:
        pack_install.install(installed, arrival)
    assert refusal.value.reasons == ("dependency_cycle",)
    assert "b -> a -> b" in refusal.value.failures[0].message


def test_an_ordinary_pack_is_not_reported_as_a_cycle(
    tmp_path: Path, session: OpenHouse
) -> None:
    """A pack that depends on nothing is not reported as a cycle.

    The negative of the check above, and what makes it a check rather than a
    refusal: a traversal that reported its start node as its own ancestor would
    refuse every pack.
    """
    session.install_pack(str(pack(tmp_path, "plain")))
    assert session.engine.installed.names == ("plain",)


# -- 5.3  the record --------------------------------------------------------


def test_the_record_answers_what_arrived_and_why(session: OpenHouse) -> None:
    """Every field the record's contract names is readable from the house.

    Falsified by a record carrying only a name: "what is installed here, and why"
    would be answerable only from the file a person remembers installing.
    """
    result = session.install_pack(str(EXAMPLE))
    assert result["pack"] == "example_pack"
    assert result["version"] == "1.0.0"
    assert result["digest"].startswith("sha256:")
    assert result["change"] == "install"
    assert result["replaced"] is None
    assert result["slots"]["light_group"] == ("light.foyer", "light.living_room")
    assert result["behaviours"] == ("example_pack.motion_turns_on_light",)

    record = session.snapshot()["engine_state"]["installed_packs"]["example_pack"]
    assert record["version"] == result["version"]
    assert record["digest"] == result["digest"]
    assert record["behaviours"] == list(result["behaviours"])
    assert record["slots"]["light_group"] == ["light.foyer", "light.living_room"]


def test_an_edited_manifest_makes_the_digest_disagree_with_the_version(
    tmp_path: Path, session: OpenHouse
) -> None:
    """A manifest changed without a bump reads equal by version and unequal by digest.

    Falsified by a record storing no digest, or one taken over the file's bytes:
    the first cannot answer the question at all, and the second reports an edit
    for a manifest only reformatted.

    The negative is the round trip: the same document serialised again is the same
    pack, so a digest that disagreed with itself after a rename of nothing would
    report a change that did not happen.
    """
    manifest = pack(tmp_path, "probe")
    session.install_pack(str(manifest))
    record = session.engine.installed.get("probe")
    assert record is not None
    original = yaml.safe_load(manifest.read_text(encoding="utf-8"))
    assert isinstance(original, dict)
    assert record.matches(original)

    edited = {**original, "description": "a pack for the test, edited"}
    assert edited["version"] == original["version"]
    assert not record.matches(edited)

    round_tripped = yaml.safe_load(yaml.safe_dump(original))
    assert record.matches(round_tripped)


def test_a_refused_install_writes_no_record(tmp_path: Path, session: OpenHouse) -> None:
    """A refused install leaves the installed set exactly as it was.

    Falsified by a resolver that recorded before it checked: the set would name a
    pack that is not fully there, and every later check would read it.
    """
    session.install_pack(str(pack(tmp_path, "kept")))
    before = session.snapshot()
    with pytest.raises(packs.PackError):
        session.install_pack(str(pack(tmp_path, "refused", requires=("no_such_slot",))))
    assert session.snapshot() == before
    assert session.engine.installed.names == ("kept",)


# -- 5.4  snapshots ---------------------------------------------------------


def test_the_record_round_trips_a_snapshot_and_a_restore(
    vocabulary: Vocabulary,
) -> None:
    """A restored house holds the packs it held, with the same facts about them.

    Falsified by a record kept outside the engine-owned state: the restore would
    resume a house that has forgotten what is installed in it, and the two halves
    of the run would decide differently.
    """
    session = open_session(house="minimal", vocabulary=vocabulary)
    session.install_pack(str(EXAMPLE))
    taken = session.snapshot()

    restored = open_session(house="minimal", vocabulary=vocabulary)
    restored.install_pack(str(EXAMPLE))
    restored.restore(taken)

    assert restored.engine.installed.names == ("example_pack",)
    assert restored.engine.installed.version_of("example_pack") == "1.0.0"
    assert (
        taken["engine_state"]["installed_packs"]
        == restored.snapshot()["engine_state"]["installed_packs"]
    )


def test_a_restore_cannot_resurrect_a_house_whose_packs_are_gone(
    vocabulary: Vocabulary,
) -> None:
    """A snapshot naming a pack this build does not register is refused.

    Falsified by a restore that adopted the record and left the behaviours
    behind: the house would hold a pack whose units nothing evaluates, and a tick
    would be recorded as though the pack had been consulted.
    """
    session = open_session(house="minimal", vocabulary=vocabulary)
    session.install_pack(str(EXAMPLE))
    taken = session.snapshot()

    plain = open_session(house="minimal", vocabulary=vocabulary)
    with pytest.raises(EngineError) as refusal:
        plain.restore(taken)
    assert "example_pack" in str(refusal.value)
    assert "does not register" in str(refusal.value)


# -- 5.5  installation is not activation ------------------------------------


def test_six_behaviours_arrive_six_disabled(tmp_path: Path, session: OpenHouse) -> None:
    """A pack declaring six behaviours adds six units, every one of them off.

    Falsified by an `install_pack` that applied what the manifest declared:
    arriving and acting would be one act, which is the one thing third-party data
    must not be able to do.

    The flags are read from the engine's own state rather than from the unit
    objects, because a pack unit's `enabled` is a constant `False` and a check
    against it could not fail. Each unit's entry is the resolved answer per scope
    -- the house's and every room's -- so "off" is asserted at every scope rather
    than at the one a fixture happens to evaluate.
    """
    result = session.install_pack(str(pack(tmp_path, "six", count=6)))
    units = set(result["behaviours"])
    assert len(units) == 6

    flags = session.snapshot()["engine_state"]["enable_flags"]
    assert isinstance(flags, Mapping)
    for unit in units:
        resolved = flags[unit]
        assert isinstance(resolved, Mapping), unit
        assert resolved["house"] is False, unit
        assert set(resolved["rooms"].values()) == {False}, unit

    records = session.advance_time(minutes=1)
    left = [record for record in records if record.actor in units]
    assert left != [], "a gated unit leaves a skip rather than nothing"
    assert [
        record for record in left if record.outcome is not Outcome.SKIPPED_DISABLED
    ] == []


def test_the_enabling_act_is_separate_and_names_what_it_enables(
    tmp_path: Path, vocabulary: Vocabulary
) -> None:
    """An installed pack proposes nothing until an act naming it opens both gates.

    Falsified by an install that enabled what it installed, and by an enabling
    act that did not name the unit.

    Two flags stand between the unit and proposing -- the pack's module flag and
    the unit's own -- and the second session sets both, because a check that set
    one would pass against an implementation ignoring the other. What is asserted
    is the *proposal* rather than the outcome: whether the command survives
    arbitration depends on what else wants the same light, and the gate is
    upstream of that.

    The proposed action is the *state* (`on`) and not the service the manifest
    declared (`light.turn_on`): `catalog/services.yaml` is the table that knows
    one from the other, and the interpreter holds the resolved state rather than
    the service name.
    """
    manifest = pack(tmp_path, "switched")
    unit = "switched.b0"

    partial = open_session(
        house="minimal", vocabulary=vocabulary, house_settings={enable_key(unit): True}
    )
    partial.install_pack(str(manifest))
    partial.advance_time(minutes=1)
    assert [record for record in _unit_records(partial, unit) if record.commands] == []

    full = open_session(
        house="minimal",
        vocabulary=vocabulary,
        house_settings={enable_key(unit): True, module_enable_key("switched"): True},
    )
    full.install_pack(str(manifest))
    full.advance_time(minutes=1)
    proposing = [record for record in _unit_records(full, unit) if record.commands]
    assert proposing != []
    assert proposing[0].commands[0].action == "on"


# -- 5.6  uninstall ---------------------------------------------------------


def test_an_uninstall_with_a_dependent_is_refused_naming_the_dependent(
    tmp_path: Path, session: OpenHouse
) -> None:
    """Removing a pack another depends on is refused rather than leaving a hole.

    Falsified by an uninstall that removed it anyway: the remaining pack's record
    would name a dependency nothing satisfies, and the set would be in a state its
    own record cannot explain.
    """
    session.install_pack(str(pack(tmp_path, "base")))
    session.install_pack(
        str(pack(tmp_path, "dependent", dependencies=(("base", ">=1.0.0"),)))
    )
    with pytest.raises(packs.PackError) as refusal:
        session.uninstall_pack("base")
    assert "dependent" in refusal.value.reason
    assert session.engine.installed.names == ("base", "dependent")


def test_an_uninstall_leaves_no_record_no_behaviour_and_no_proposal(
    tmp_path: Path, session: OpenHouse
) -> None:
    """What an uninstall takes back is everything the install brought.

    Falsified by an uninstall that removed the record and left the unit
    registered: the pack would keep proposing for a house that no longer holds it,
    which is worse than never having installed it.
    """
    session.install_pack(str(pack(tmp_path, "temporary")))
    assert "temporary.b0" in session.behaviours

    removed = session.uninstall_pack("temporary")
    assert removed == {
        "pack": "temporary",
        "installed": False,
        "behaviours": ("temporary.b0",),
    }
    assert "temporary" not in session.engine.installed.names
    assert "temporary.b0" not in session.behaviours
    assert "temporary" not in session.snapshot()["engine_state"]["installed_packs"]
    assert _unit_records(session, "temporary.b0") == ()


def test_uninstalling_something_that_is_not_installed_is_refused(
    session: OpenHouse,
) -> None:
    """An unknown name is refused rather than silently leaving the set alone.

    Falsified by a no-op uninstall: a caller that mistyped a pack's name would be
    told the act succeeded, and the pack would still be there.
    """
    with pytest.raises(packs.PackError) as refusal:
        session.uninstall_pack("absent")
    assert "absent" in refusal.value.reason


# -- 5.7  version changes ---------------------------------------------------


def test_a_new_version_replaces_the_record_rather_than_adding_one(
    tmp_path: Path, session: OpenHouse
) -> None:
    """Installing a version over another is one record, naming what it displaced.

    Falsified by an install keying the set by version: two records would hold one
    name, and the slots, flags and behaviours of the pack would be readable twice
    with no answer to which is live.
    """
    session.install_pack(str(pack(tmp_path, "moving", version="1.0.0")))
    result = session.install_pack(str(pack(tmp_path, "moving", version="1.1.0")))

    assert result["change"] == "upgrade"
    assert result["replaced"] == "1.0.0"
    assert session.engine.installed.names == ("moving",)
    assert session.engine.installed.version_of("moving") == "1.1.0"


def test_a_downgrade_out_of_a_dependents_range_is_refused(
    tmp_path: Path, session: OpenHouse
) -> None:
    """A version change that would strand a dependent is refused as breaking.

    Falsified by a resolver checking only the arriving pack's own dependencies:
    the downgrade would leave the set holding a dependent whose range nothing
    satisfies, which is the one state this check exists to make unreachable.
    """
    session.install_pack(str(pack(tmp_path, "library", version="3.0.0")))
    session.install_pack(
        str(pack(tmp_path, "consumer", dependencies=(("library", ">=2.0.0"),)))
    )
    with pytest.raises(packs.PackError) as refusal:
        session.install_pack(str(pack(tmp_path, "library", version="1.0.0")))
    assert "consumer" in refusal.value.reason
    assert session.engine.installed.version_of("library") == "3.0.0"


def test_a_downgrade_nothing_depends_on_is_admitted(
    tmp_path: Path, session: OpenHouse
) -> None:
    """The same downgrade with no dependent installed is an ordinary change.

    The negative of the check above, and the reason the rule is about *breaking*
    rather than about downgrading: a resolver refusing every downgrade would pass
    that check and refuse a legal act.
    """
    session.install_pack(str(pack(tmp_path, "solo", version="3.0.0")))
    result = session.install_pack(str(pack(tmp_path, "solo", version="1.0.0")))
    assert result["change"] == "downgrade"
    assert result["replaced"] == "3.0.0"
    assert session.engine.installed.version_of("solo") == "1.0.0"


# -- 5.8  atomicity ---------------------------------------------------------


def test_a_refusal_reports_every_reason_the_check_found(
    tmp_path: Path, session: OpenHouse
) -> None:
    """A pack wrong in two ways is reported wrong in two ways.

    Falsified by a sandbox raising on its first refusal: a person fixing a pack
    would fix one thing, install again, and be told about the next -- which is how
    a check turns into a queue.
    """
    manifest = pack(
        tmp_path,
        "twice",
        edits=(
            ("    services: [light.turn_on]", "    services: [shell_command.rm]"),
            (f"    slots: [{SLOT}]", "    slots: [ambient_light_sensor]"),
        ),
    )
    with pytest.raises(packs.PackError) as refusal:
        session.install_pack(str(manifest))
    assert "shell_command.rm" in refusal.value.reason
    assert "ambient_light_sensor" in refusal.value.reason


def test_no_check_writes_anything_when_it_refuses(
    tmp_path: Path, session: OpenHouse
) -> None:
    """A failure at each of the four checks leaves the house unchanged.

    Falsified by an install that recorded the pack and then validated it: the
    house would carry a pack a check refused, and the *next* valid install would
    be resolved against a set that never should have held it. Each case is
    followed by a valid install, so an implementation leaving the house in a
    state only a valid install could clear fails the second half rather than the
    first.
    """
    session.install_pack(str(pack(tmp_path, "present")))
    before = session.snapshot()

    refusals = {
        "schema": pack(
            tmp_path, "schematic", edits=(("name: schematic", "name: Not A Name"),)
        ),
        "house slots": pack(tmp_path, "wants_vacuum", requires=("no_such_slot",)),
        "sandbox": pack(tmp_path, "banned", services=("shell_command.rm",)),
        "resolution": pack(
            tmp_path, "unanswered", dependencies=(("absent", ">=1.0.0"),)
        ),
    }
    for label, manifest in refusals.items():
        with pytest.raises(packs.PackError):
            session.install_pack(str(manifest))
        assert session.snapshot() == before, label
        assert session.engine.installed.names == ("present",), label

    session.install_pack(
        str(pack(tmp_path, "after", dependencies=(("present", ">=1.0.0"),)))
    )
    assert session.engine.installed.names == ("after", "present")
