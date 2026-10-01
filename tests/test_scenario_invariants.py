"""The six invariants, each with a property and a falsifying bench -- task 9.4.

`sim/scenario/invariants.py` owns the six claims and, until this module, nothing
ran them: the module is unwired, which is the gap the acceptance mapping reports
against `scenario-runner`'s "Invariants are properties over the running engine,
each with a falsifying input". This is the suite its docstring describes -- the
one that decorates each claim with `@given` and runs the sabotaged `Bench` its
`falsified_by` names.

**Both halves are needed, and neither is decoration.** The property test drives
real sessions and asserts the claim *holds*, which is the claim's content. The
falsifier builds the input the invariant says falsifies it and asserts the claim
*fails* on it, which is the only thing separating a claim from a tautology: a
check that returned without looking would pass every property test here. The
`falsified_by` field was prose until this module; each falsifier below is that
prose made into an input, and `test_every_invariant_has_a_falsifying_bench`
fails when one is missing.

**Four falsifiers are sessions that lie, and that is the design.** The claim is
about what a *record* or a *document* says, and the engine will not write the
record that would break it -- a skip that carries a command, a tick that applies
two commands to one entity, a snapshot with the stream's position gone. So the
falsifier hands the claim a session whose one member answers with what the claim
must not be able to read, and the claim is proven falsifiable rather than assumed
so. The two that are not lies are the two where the claim reads something the
suite supplies: a session factory that opens the second run at another seed (the
replay is caught in the devices, not in a record), and an egress oracle that
calls an ordinary command an egress (the claim reads the oracle, so a false
oracle is the input). Neither needs the engine to misbehave, which is precisely
why they are falsifiers rather than assertions about the engine.
"""

from __future__ import annotations

import copy
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from engine.adapter import ChangeOrigin
from engine.decision_log import Outcome
from engine.safety import is_egress
from engine.vocabulary import Vocabulary
from openhouse.facade import open_session
from sim.fixtures import DEFAULT_SEED
from sim.scenario import load_scenario
from sim.scenario.invariants import (
    DETERMINISTIC_REPLAY,
    INVARIANTS_BY_NAME,
    NO_NON_USER_EGRESS,
    NO_UNBOUND_REQUIRED_ACTION,
    RESTORE_REPLAY_IDENTITY,
    SINGLE_COMMAND_PER_ENTITY,
    ZERO_ADVANCE_IS_NOOP,
    Bench,
    InvariantViolated,
    Script,
    Step,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sim.scenario.surface import ScenarioSurface

ROOT = Path(__file__).resolve().parents[1]

#: The scenario whose drive this module borrows: motion, a minute, then the
#: motion clearing and six more, which is the corpus's shortest run that makes a
#: behaviour *act* rather than decline.
_WATCHDOG = ROOT / "scenarios" / "05b-hallway-light-watchdog-fires.yaml"

#: The two houses the benches are built on. `minimal` for the claims that need a
#: behaviour to act; `messy` for the two replay claims, because it is the one
#: fixture with unavailable candidates, so it is the one that *draws* from the
#: stream at build time -- and a replay claim tested on a house that draws
#: nothing would hold for a reason that has nothing to do with replaying.
_DRIVEN_HOUSE = "minimal"
_REPLAY_HOUSE = "messy"

#: The motion sensor and the light in `minimal`'s living room, which is the room
#: the watchdog behaviour watches. Named rather than discovered because the step
#: alphabet is a strategy and a strategy cannot ask a session that does not exist
#: yet what it holds.
_MOTION = "binary_sensor.living_room_motion"
_LIGHT = "light.living_room"

#: The house two claims are made over when `minimal` cannot provoke them. It is
#: the same house for both, for two different reasons: it holds rooms whose slots
#: the behaviour's binding never fills, which is the only way a `skipped: unbound
#: slot` record exists at all; and it holds locks and covers, so the egress claim
#: is made over a house that has the kind of device the rule is about. It is also
#: the only house that does both -- `minimal` has no egress device, and `no_lux`
#: holds a cover but no lock, and at the clock it is built with (Sydney daylight)
#: its lighting behaviour declines rather than acts, so a drive over it would
#: hand the egress oracle no command to read. It is not that `no_lux` can never
#: act: at a night clock the same behaviour falls back to the sun and commands a
#: light. What it cannot do is act on its cover, which is the device this claim
#: reads.
_UNBOUND_HOUSE = "large"
_EGRESS_HOUSE = "large"

#: The entities the drives below move when they have to name a house that is not
#: `minimal`. `_MOTION`/`_LIGHT` are `minimal`'s, and `messy` shares neither, so
#: its drive names its own motion; `large` suffixes every room, so its drive
#: names the garage's pair and its own cover.
_COVER = "cover.garage_08_cover"
_GARAGE_MOTION = "binary_sensor.garage_08_motion"
_MESSY_MOTION = "binary_sensor.dining_room_motion"

#: Both behaviours on at once. One enabled behaviour cannot contend with itself,
#: and contention is what the arbitration claim settles.
_BOTH: Mapping[str, bool] = {
    "behaviour.motion_lighting.enabled": True,
    "behaviour.away_shutdown.enabled": True,
}


@pytest.fixture(scope="module")
def vocabulary() -> Vocabulary:
    """The catalog's vocabulary, loaded once for the module.

    Module-scoped on purpose: a function-scoped fixture under `@given` trips
    Hypothesis' own health check, and the check is right -- reloading the catalog
    for every example would make the property test about the loader.
    """
    return Vocabulary.load(ROOT)


# --------------------------------------------------------------------------
# The drive: steps and the bench that carries them
# --------------------------------------------------------------------------


def _motion_on(surface: ScenarioSurface) -> None:
    surface.set_state(_MOTION, "on")


def _motion_off(surface: ScenarioSurface) -> None:
    surface.set_state(_MOTION, "off")


def _light_on(surface: ScenarioSurface) -> None:
    surface.set_state(_LIGHT, "on")


def _light_off(surface: ScenarioSurface) -> None:
    surface.set_state(_LIGHT, "off")


def _user_sets_the_light(surface: ScenarioSurface) -> None:
    """A change with the user's own origin, which is the one origin egress allows."""
    surface.user_action(_LIGHT, "off")


def _a_minute(surface: ScenarioSurface) -> None:
    surface.advance_time(minutes=1)


def _six_minutes(surface: ScenarioSurface) -> None:
    surface.advance_time(minutes=6)


def _thirty_minutes(surface: ScenarioSurface) -> None:
    """Long enough for the watchdog's window *and* the shutdown to fall due."""
    surface.advance_time(minutes=30)


def _foyer_motion_off(surface: ScenarioSurface) -> None:
    surface.set_state("binary_sensor.foyer_motion", "off")


def _foyer_light_on(surface: ScenarioSurface) -> None:
    surface.set_state("light.foyer", "on")


def _open_the_garage(surface: ScenarioSurface) -> None:
    """A user opening a cover -- the one origin the egress rule allows.

    A `user_action` rather than a `set_state`, because the surface refuses to
    open an egress device any other way: the step has to be the door a person
    uses, or it is testing the refusal instead of the rule.
    """
    surface.user_action(_COVER, "open")


def _garage_motion_on(surface: ScenarioSurface) -> None:
    surface.set_state(_GARAGE_MOTION, "on")


def _garage_motion_off(surface: ScenarioSurface) -> None:
    surface.set_state(_GARAGE_MOTION, "off")


def _messy_motion_on(surface: ScenarioSurface) -> None:
    surface.set_state(_MESSY_MOTION, "on")


def _messy_motion_off(surface: ScenarioSurface) -> None:
    surface.set_state(_MESSY_MOTION, "off")


def _away(surface: ScenarioSurface) -> None:
    """Put the house in `away`, which is engine state and not a device.

    No step can write a mode through the surface -- `set_state` reaches devices
    and the mode is not one -- so the snapshot carries it: take the document,
    set the mode inside its `engine_state`, restore it. That is the same door the
    rule-level tests use, and without it a mode-gated behaviour could not be
    provoked from a script at all.
    """
    document = dict(surface.snapshot())
    state = document["engine_state"]
    if isinstance(state, dict):
        state["modes"] = ["away"]
    surface.restore(document)


#: The step alphabet the property tests draw from. Small on purpose: every member
#: names an entity the driven house holds, so a generated script cannot fail for
#: a reason that is about the generator rather than about the claim.
_ALPHABET: tuple[Step, ...] = (
    _motion_on,
    _motion_off,
    _light_on,
    _light_off,
    _user_sets_the_light,
    _a_minute,
    _six_minutes,
)

#: The corpus drive, as a `Bench` script. Used by the falsifiers, which need a
#: run that produces an acted record rather than a generated one.
WATCHDOG_DRIVE: Script = (_motion_on, _a_minute, _motion_off, _six_minutes)

#: `messy`'s own motion pair, so the two replay claims run over the one house
#: that draws from the stream *and* over a drive with a middle in it. The empty
#: script these claims used to be given made the restore claim vacuous: a script
#: is split at its midpoint, so a script of no steps has no midpoint to restore
#: from and the claim was comparing two runs that had both done nothing.
_MESSY_DRIVE: Script = (_messy_motion_on, _a_minute, _messy_motion_off, _six_minutes)

#: `large`, with no motion at all: the rooms whose slots the behaviour's binding
#: never fills are evaluated on every tick and record the skip. Needed because
#: `minimal` binds every slot the behaviour needs, so no drive over it can
#: provoke the skip the claim is about.
_UNBOUND_DRIVE: Script = (_a_minute, _six_minutes)

#: `large`'s garage, driven to act *and* to have its cover opened by the user.
#: The claim needs an acted record with a non-user origin carrying a command to
#: read, and the house it reads it from holds the devices the rule is about -- so
#: a command that got past the gate here would be the violation, rather than a
#: light command in a house where no lock or cover exists to be commanded.
_EGRESS_DRIVE: Script = (
    _garage_motion_on,
    _a_minute,
    _garage_motion_off,
    _six_minutes,
    _open_the_garage,
)

#: `minimal` with both behaviours enabled and the house emptied into `away`: the
#: watchdog's timeout and the away shutdown fall due in the same tick, and one of
#: them loses the arbitration. That is the contention the claim settles, and a
#: drive with one behaviour enabled cannot produce it.
_CONTENTION_DRIVE: Script = (
    _motion_off,
    _foyer_motion_off,
    _light_on,
    _foyer_light_on,
    _thirty_minutes,
    _away,
    _thirty_minutes,
)

_SCRIPTS = st.lists(st.sampled_from(_ALPHABET), max_size=6)
_SEEDS = st.integers(min_value=0, max_value=2**31 - 1)

#: Five examples, no deadline and no database. The database is off to keep the
#: examples from being carried between runs -- a stored counterexample is not a
#: property -- and the deadline is off because a few hundred milliseconds of
#: session-building is not a failure. It is *not* off to keep the repository
#: clean: Hypothesis still writes `.hypothesis/`, whose own `.gitignore` ignores
#: it, and a run that finds one there is expected rather than a defect.
_SETTINGS = settings(max_examples=5, deadline=None, database=None)


def _egress(entity_id: str, action: str) -> bool:
    """Whether writing `action` to `entity_id` unlocks a lock or opens a cover.

    The question `Bench.egress` asks, answered by `engine/safety.py`'s own table
    rather than by a second copy of it -- the copy that drifted would be the one
    guarding the product rule.
    """
    return is_egress(entity_id.split(".")[0], action)


def _open(
    vocabulary: Vocabulary,
    house: str,
    seed: int,
    flags: Mapping[str, bool] | None = None,
) -> ScenarioSurface:
    """A session over `house`, with the watchdog scenario's own enable flags.

    Opened through `open_session` rather than `open_scenario` because the house
    is this module's choice and not the scenario's: the `given` block would fix
    it, and two of the claims need a house that draws from the stream. The enable
    flags still come from the scenario, because they are what makes the watchdog
    behaviour act rather than decline -- and a bench on which nothing acts is a
    bench on which most of these claims hold for want of anything happening.
    `flags` overrides them where a claim needs a second behaviour enabled, which
    is one claim: the arbitration, which nothing can contend over alone.
    """
    given = load_scenario(_WATCHDOG).given
    return open_session(
        house=house,
        vocabulary=vocabulary,
        seed=seed,
        started_at=given.started_at,
        house_settings=given.enable_flags if flags is None else flags,
    )


def _bench(
    vocabulary: Vocabulary,
    *,
    house: str = _DRIVEN_HOUSE,
    seed: int = DEFAULT_SEED,
    script: Script = WATCHDOG_DRIVE,
    egress: Callable[[str, str], bool] | None = None,
    lies: Mapping[str, object] | None = None,
    flags: Mapping[str, bool] | None = None,
) -> Bench:
    """A `Bench` over one house, with an optional lying session.

    `lies` maps a surface member's name to what it should answer instead. The
    session is opened for real and only the named member is overridden, so a
    falsifier differs from its property in exactly the one way its invariant's
    `falsified_by` describes. `flags` is the session's enable state, defaulting
    to the scenario's own.
    """

    def session() -> ScenarioSurface:
        opened = _open(vocabulary, house, seed, flags)
        return opened if lies is None else _lying(opened, lies)

    return Bench(
        session=session,
        script=script,
        egress=_egress if egress is None else egress,
    )


def _permissive(entity_id: str, action: str) -> bool:
    """An egress oracle that says every action is one.

    The falsifier for `no_non_user_egress`: the claim is that no *egress* command
    is applied under a non-user origin, and with the engine correct the engine
    will never apply one, so the oracle is made to call an ordinary light
    command an egress. That proves the claim reads the oracle and can fail; it
    does not pretend the engine did something it did not.
    """
    return True


def _lying(real: ScenarioSurface, lies: Mapping[str, object]) -> ScenarioSurface:
    """A session answering `lies` where given and `real` everywhere else.

    Not a `ScenarioSurface` by construction, which is the point: it is a session
    that lies, and the cast says so at the one place it is made. Every member the
    claims do not name still reaches the real session, so a falsifier cannot
    break a claim other than its own by accident.
    """

    class _Liar:
        def __getattr__(self, name: str) -> object:
            if name in lies:
                return lies[name]
            return getattr(real, name)

    return cast("ScenarioSurface", _Liar())


# --------------------------------------------------------------------------
# The properties: every claim, on real sessions
# --------------------------------------------------------------------------


@given(seed=_SEEDS)
@_SETTINGS
def test_a_replay_at_any_seed_decides_the_same(
    vocabulary: Vocabulary, seed: int
) -> None:
    """Two sessions at one seed, on a house that draws, are indistinguishable.

    `messy` and a drive with a middle in it: the stream is drawn once when this
    fixture is built and for no other house, so a property that held here held
    because the replay was faithful rather than because nothing was ever drawn --
    and the drive reaches decisions, so the records compared are not both empty.
    """
    INVARIANTS_BY_NAME[DETERMINISTIC_REPLAY](
        _bench(vocabulary, house=_REPLAY_HOUSE, seed=seed, script=_MESSY_DRIVE)
    )


@given(seed=_SEEDS)
@_SETTINGS
def test_a_restored_run_continues_as_the_uninterrupted_one(
    vocabulary: Vocabulary, seed: int
) -> None:
    """A snapshot taken and restored mid-run resumes where the run was.

    Mid-run is the whole claim: the check splits the script and restores at the
    seam, so the drive has to be long enough to have a seam. An empty script has
    none, and the claim over one compares two runs that both did nothing.
    """
    INVARIANTS_BY_NAME[RESTORE_REPLAY_IDENTITY](
        _bench(vocabulary, house=_REPLAY_HOUSE, seed=seed, script=_MESSY_DRIVE)
    )


@given(seed=_SEEDS)
@_SETTINGS
def test_no_drive_applies_an_egress_command_under_a_non_user_origin(
    vocabulary: Vocabulary, seed: int
) -> None:
    """The gate holds the egress rule, over a house where an egress device exists.

    `large` and a fixed drive rather than a generated one, for two reasons that
    are properties of the fixtures rather than of the claim: `minimal` holds no
    lock or cover, so the claim over it could only ever be about a light, and
    `large`'s entities are suffixed per room, so a generated script naming
    `minimal`'s would fail on the entity rather than on the rule. The drive does
    what the claim needs and nothing else -- an acted, non-user command to read,
    and a cover the user opens in the same run.
    """
    INVARIANTS_BY_NAME[NO_NON_USER_EGRESS](
        _bench(vocabulary, house=_EGRESS_HOUSE, seed=seed, script=_EGRESS_DRIVE)
    )


@given(seed=_SEEDS)
@_SETTINGS
def test_no_skip_for_an_unbound_slot_carries_an_action(
    vocabulary: Vocabulary, seed: int
) -> None:
    """A record that says "this did not happen" carries neither command nor delta.

    Over `large`, and over a fixed drive rather than a generated one: the skip
    the claim is about needs a room whose slot the binding cannot fill, and the
    generated alphabet names `minimal`'s entities, which `large` does not hold.
    The bench is asserted to provoke the skip in
    `test_the_unbound_bench_records_a_skipped_slot` below, so this property is
    not one that holds for want of a record to read.
    """
    INVARIANTS_BY_NAME[NO_UNBOUND_REQUIRED_ACTION](
        _bench(vocabulary, house=_UNBOUND_HOUSE, seed=seed, script=_UNBOUND_DRIVE)
    )


@given(seed=_SEEDS, script=_SCRIPTS)
@_SETTINGS
def test_a_zero_advance_ticks_nothing(
    vocabulary: Vocabulary, seed: int, script: list[Step]
) -> None:
    """Advancing by zero is not a tick, on any drive that led up to it."""
    INVARIANTS_BY_NAME[ZERO_ADVANCE_IS_NOOP](
        _bench(vocabulary, seed=seed, script=tuple(script))
    )


@given(seed=_SEEDS)
@_SETTINGS
def test_one_command_per_entity_per_tick(vocabulary: Vocabulary, seed: int) -> None:
    """Arbitration settles a tick's contention, whatever contended.

    Both behaviours enabled, and the house emptied into `away`: one enabled
    behaviour cannot contend with itself, so the drive this claim needs is the
    one that makes the watchdog's timeout and the away shutdown fall due in the
    same tick. `test_the_contention_bench_loses_an_arbitration` below asserts
    that it does, rather than leaving the claim to hold on a tick where nothing
    was contended.
    """
    INVARIANTS_BY_NAME[SINGLE_COMMAND_PER_ENTITY](
        _bench(vocabulary, seed=seed, script=_CONTENTION_DRIVE, flags=_BOTH)
    )


# --------------------------------------------------------------------------
# The benches provoke what they claim to: non-vacuity, asserted
# --------------------------------------------------------------------------
#
# A bench on which nothing happens makes the claim over it hold for a reason that
# has nothing to do with the claim -- the shape of green that says least. The
# tests below are the other half of the four benches above: each asserts that the
# drive it is paired with produces the record its claim reads, so a fixture or a
# rule that stopped provoking one fails here rather than quietly turning its
# property into a tautology.


def _driven_over(
    vocabulary: Vocabulary,
    *,
    house: str,
    script: Script,
    seed: int = DEFAULT_SEED,
    flags: Mapping[str, bool] | None = None,
) -> ScenarioSurface:
    """Drive one session through `script` and hand the session back.

    Not a claim and not a `Bench`: a `Bench` is what a claim is evaluated over,
    and evaluating a claim is not how one shows a bench provokes. This is the
    same drive, stopped one step earlier so the tests below can look at both the
    devices and the records.
    """
    session = _open(vocabulary, house, seed, flags)
    for step in script:
        step(session)
    return session


def _records_read(session: ScenarioSurface) -> list[Mapping[str, object]]:
    """The session's decision log, as the documents every surface reports."""
    return [record.to_document() for record in session.get_decision_log()]


def _outcomes(documents: Sequence[Mapping[str, object]]) -> dict[str, int]:
    """How often each outcome appears, which is all these tests ask of a log."""
    counted: dict[str, int] = {}
    for document in documents:
        outcome = str(document.get("outcome"))
        counted[outcome] = counted.get(outcome, 0) + 1
    return counted


def _commands(document: Mapping[str, object]) -> list[Mapping[str, object]]:
    """The commands one record carries, as documents.

    A document is a `Mapping[str, object]`, so the list check is the runtime
    guard and the element type is the cast: past it, the record's own format is
    the contract -- `sim` writes command documents there -- and a test that
    re-verified each one would be a second reading of a shape this suite does not
    own. A record whose `commands` is not a list is read as carrying none, which
    is what the claim above does with it.
    """
    commands = document.get("commands")
    if not isinstance(commands, list):
        return []
    return list(cast("list[Mapping[str, object]]", commands))


def _non_user_commands(
    document: Mapping[str, object],
) -> list[Mapping[str, object]]:
    """The commands one record applied that did not carry the user's origin.

    The same reading the egress claim makes, so that "there was a command to
    read" is asserted with the claim's own notion of whose command it is.
    """
    return [
        command
        for command in _commands(document)
        if command.get("origin") != ChangeOrigin.USER.value
    ]


def _state_of(session: ScenarioSurface, entity_id: str) -> str:
    """One device's state, read from the snapshot the surface reports.

    The surface exposes no single-entity read -- a read is a slot read or the
    whole snapshot -- so the snapshot is where a test looks for a device.
    """
    entities = session.snapshot()["entities"]
    if not isinstance(entities, list):
        raise AssertionError(f"the snapshot's entities are not a list: {entities!r}")
    for document in cast("list[Mapping[str, object]]", entities):
        if document.get("entity_id") == entity_id:
            return str(document.get("state"))
    raise AssertionError(f"{entity_id} is not in the house: {entities}")


def test_the_unbound_bench_records_a_skipped_slot(vocabulary: Vocabulary) -> None:
    """`large`, driven with no motion, really does leave slots unbound.

    The claim above says a skip carries no action. If no skip is ever recorded it
    says nothing, and this is the assertion that the record exists -- counted
    rather than shown, because the claim is about every record and all this needs
    to prove is that there is at least one to be about.
    """
    session = _driven_over(vocabulary, house=_UNBOUND_HOUSE, script=_UNBOUND_DRIVE)
    counted = _outcomes(_records_read(session))
    assert counted.get(Outcome.SKIPPED_UNBOUND_SLOT.value, 0) > 0, counted


def test_the_contention_bench_loses_an_arbitration(vocabulary: Vocabulary) -> None:
    """Two enabled behaviours fall due in one tick, and one of them loses.

    `lost arbitration` is the only observable that a contention happened at all,
    so its absence is exactly how this bench would go quiet without failing.
    """
    session = _driven_over(
        vocabulary,
        house=_DRIVEN_HOUSE,
        script=_CONTENTION_DRIVE,
        flags=_BOTH,
    )
    counted = _outcomes(_records_read(session))
    assert counted.get(Outcome.LOST_ARBITRATION.value, 0) > 0, counted


def test_the_egress_bench_acts_and_the_user_opens_the_garage(
    vocabulary: Vocabulary,
) -> None:
    """The egress claim has a command to read, in a house whose cover moved.

    Two facts, asserted apart. The first is that some `acted` record carries a
    command under a non-user origin -- without one the claim's loop body never
    runs and the claim is green with nothing examined. The second is that this
    run really did open the cover, by the user, which is what makes the absence
    of a non-user cover command mean the rule held rather than that nothing could
    have tested it.
    """
    session = _driven_over(vocabulary, house=_EGRESS_HOUSE, script=_EGRESS_DRIVE)
    documents = _records_read(session)
    read_a_command = [
        document
        for document in documents
        if document.get("outcome") == Outcome.ACTED.value
        and _non_user_commands(document)
    ]
    assert read_a_command, documents
    assert _state_of(session, _COVER) == "open", documents


def test_the_replay_bench_draws_and_has_a_middle(vocabulary: Vocabulary) -> None:
    """`messy` draws from the stream, and its drive has a seam to restore at.

    Two ways these claims could be vacuous at once: a house that draws nothing
    (so a seeded replay has nothing to disagree about) and a script with no
    midpoint (so the restore claim resumes a run that never started). Both are
    asserted here because both are properties of the bench rather than of the
    engine, and neither is visible from the claim's own result.
    """
    session = _open(vocabulary, _REPLAY_HOUSE, DEFAULT_SEED)
    assert session.snapshot()["random_position"], "messy drew nothing"
    assert len(_MESSY_DRIVE) > 1, _MESSY_DRIVE


# --------------------------------------------------------------------------
# The falsifiers: one input per claim, and the claim must fail on it
# --------------------------------------------------------------------------


def _two_seeds(vocabulary: Vocabulary) -> Bench:
    """`deterministic_replay`, falsified: the second session opens at another seed.

    `messy` is the house that draws, so the two sessions hold different
    unavailable lights and the device comparison is what catches them. On a house
    that draws nothing the seed would change the snapshot's `random_seed` and
    nothing the claim compares -- which is why the falsifier is not built on the
    house the property test uses for the other four claims. The drive is the
    property's own, so a claim that only compared two empty logs would be caught
    here rather than passing.
    """
    opened = 0

    def session() -> ScenarioSurface:
        nonlocal opened
        seed = DEFAULT_SEED + opened
        opened += 1
        return _open(vocabulary, _REPLAY_HOUSE, seed)

    return Bench(session=session, script=_MESSY_DRIVE, egress=_egress)


def _a_snapshot_without_its_stream_position(vocabulary: Vocabulary) -> Bench:
    """`restore_replay_identity`, falsified: the stream's position is gone.

    The document is real in every other way. What the claim needs to catch is a
    snapshot that would replay its draws differently, and a document that cannot
    say where the stream was is that snapshot's extreme form: the restore refuses
    it, and the claim reports the refusal as itself failing rather than letting
    the exception escape as "the harness is broken". The lie is built when the
    session opens -- `_snapshot_without_position` reads the session at
    construction -- so the document it strips the key from is the *opening*
    snapshot, and the claim then restores its seam from that document. Where the
    document was taken is not the falsification; the missing key is.
    """

    def session() -> ScenarioSurface:
        opened = _open(vocabulary, _REPLAY_HOUSE, DEFAULT_SEED)
        return _lying(opened, {"snapshot": _snapshot_without_position(opened)})

    return Bench(session=session, script=_MESSY_DRIVE, egress=_egress)


def _snapshot_without_position(real: ScenarioSurface) -> object:
    """A `snapshot` that returns the real document minus `random_position`."""
    taken = real.snapshot()

    def snapshot() -> Mapping[str, object]:
        document = copy.deepcopy(dict(taken))
        document.pop("random_position", None)
        return document

    return snapshot


def _a_permissive_egress_oracle(vocabulary: Vocabulary) -> Bench:
    """`no_non_user_egress`, falsified: the oracle calls an ordinary command egress.

    The drive makes the watchdog act on the house that holds the cover, so an
    `acted` record with a non-user origin and a command exists; the oracle then
    answers "that was an egress", and the claim fails. Without the record the
    claim would have nothing to read and the falsifier would prove nothing, which
    is why the script is the egress drive rather than an empty one.
    """
    return _bench(
        vocabulary,
        house=_EGRESS_HOUSE,
        script=_EGRESS_DRIVE,
        egress=_permissive,
    )


def _a_skip_that_carries_a_command(vocabulary: Vocabulary) -> Bench:
    """`no_unbound_required_action`, falsified: a skip carrying what a skip cannot.

    The record below is the one the claim exists to catch -- `skipped: unbound
    slot` with a command and a state change beside it -- and the engine will
    never write it. Handing it to the claim is the only way to show the claim
    reads the record rather than the outcome string alone.
    """
    record: dict[str, object] = {
        "at": "2026-01-01T23:00:00+00:00",
        "actor": "lighting.motion_light",
        "outcome": "skipped: unbound slot",
        "rule": None,
        "commands": [
            {
                "action": "on",
                "entities": [_LIGHT],
                "origin": "engine",
            }
        ],
        "state_delta": [{"entity_id": _LIGHT, "state": "on"}],
    }
    return _bench(vocabulary, lies={"get_decision_log": _only_record(_Record(record))})


def _only_record(record: _Record) -> Callable[..., tuple[_Record, ...]]:
    """A `get_decision_log` that returns one record and ignores the window.

    Written out rather than left as a lambda so the signature it must satisfy is
    visible: `get_decision_log` is keyword-only, and a lie that took no arguments
    would be a session that raised `TypeError` where the claim called it.
    """

    def get_decision_log(*, window: int | None = None) -> tuple[_Record, ...]:
        del window
        return (record,)

    return get_decision_log


def _a_zero_advance_that_ticks(vocabulary: Vocabulary) -> Bench:
    """`zero_advance_is_noop`, falsified: a clock that moves on a zero advance.

    The lie is a side effect rather than a returned value, because the claim
    compares the house before and after and not what `advance_time` returned: a
    zero advance that wrote a device would be a tick, whatever it reported.
    """

    def session() -> ScenarioSurface:
        real = _open(vocabulary, _DRIVEN_HOUSE, DEFAULT_SEED)
        advance = real.advance_time

        def ticking_advance(*, minutes: float) -> object:
            records = advance(minutes=minutes)
            if minutes == 0:
                real.set_state(_LIGHT, "on")
            return records

        return _lying(real, {"advance_time": ticking_advance})

    return Bench(session=session, script=WATCHDOG_DRIVE, egress=_egress)


def _two_commands_to_one_entity(vocabulary: Vocabulary) -> Bench:
    """`single_command_per_entity`, falsified: two deltas, one entity, one instant.

    Counted from `state_delta` because that is what the claim counts from -- a
    falsifier that duplicated a *command* instead would leave the claim passing
    and would be testing a rule the claim does not make.
    """
    at = "2026-01-01T23:00:00+00:00"
    records = tuple(
        _Record(
            {
                "at": at,
                "actor": actor,
                "outcome": "acted",
                "rule": "lighting.motion_light",
                "commands": [],
                "state_delta": [{"entity_id": _LIGHT, "state": state}],
            }
        )
        for actor, state in (
            ("lighting.motion_light", "on"),
            ("lighting.timeout", "off"),
        )
    )
    return _bench(
        vocabulary,
        lies={
            "get_decision_log": _two_records(records),
        },
    )


def _two_records(records: tuple[_Record, ...]) -> Callable[..., tuple[_Record, ...]]:
    """A `get_decision_log` returning both records whatever window is asked for."""

    def get_decision_log(*, window: int | None = None) -> tuple[_Record, ...]:
        del window
        return records

    return get_decision_log


class _Record:
    """A record-shaped stand-in carrying only `to_document`, which is all `sim/` reads."""

    def __init__(self, document: Mapping[str, object]) -> None:
        self._document = document

    def to_document(self) -> Mapping[str, object]:
        return self._document


#: Every claim's falsifying bench, by claim. The mapping is the whole of task
#: 9.4's second half: a name missing here is a claim whose `falsified_by` is
#: still prose.
_FALSIFIERS: Mapping[str, Callable[[Vocabulary], Bench]] = {
    DETERMINISTIC_REPLAY: _two_seeds,
    RESTORE_REPLAY_IDENTITY: _a_snapshot_without_its_stream_position,
    NO_NON_USER_EGRESS: _a_permissive_egress_oracle,
    NO_UNBOUND_REQUIRED_ACTION: _a_skip_that_carries_a_command,
    ZERO_ADVANCE_IS_NOOP: _a_zero_advance_that_ticks,
    SINGLE_COMMAND_PER_ENTITY: _two_commands_to_one_entity,
}


def test_every_invariant_has_a_falsifying_bench() -> None:
    """No claim is left with a `falsified_by` that is only a sentence.

    The requirement's own words: "each with a falsifying input". A claim added to
    `INVARIANTS` without one fails here rather than passing unexamined in the
    parametrized test below, which would silently cover one fewer name.
    """
    assert set(_FALSIFIERS) == set(INVARIANTS_BY_NAME)
    for name, invariant in INVARIANTS_BY_NAME.items():
        assert invariant.falsified_by.strip(), name


@pytest.mark.parametrize("name", sorted(INVARIANTS_BY_NAME))
def test_each_invariant_fails_on_its_falsifying_bench(
    name: str, vocabulary: Vocabulary
) -> None:
    """Each claim raises, naming itself, on the input it says falsifies it.

    Naming itself matters as much as raising: the claims are evaluated in one
    suite and a counterexample is reported by name, so a claim that failed under
    another claim's name would send its reader to the wrong rule.
    """
    bench = _FALSIFIERS[name](vocabulary)
    with pytest.raises(InvariantViolated) as info:
        INVARIANTS_BY_NAME[name](bench)
    assert info.value.name == name


def test_the_claims_are_the_six_the_spec_names() -> None:
    """The registry is the six, so a seventh cannot arrive unnoticed.

    `scenario-runner` enumerates six: deterministic replay, restore-then-continue,
    no non-user unlock or open-cover, no action from an unbound required slot, a
    zero advance changing nothing, and one command per entity per tick. This
    suite's coverage is the registry's, so the count is asserted here rather than
    left to the mapping document to notice.
    """
    assert sorted(INVARIANTS_BY_NAME) == [
        DETERMINISTIC_REPLAY,
        NO_NON_USER_EGRESS,
        NO_UNBOUND_REQUIRED_ACTION,
        RESTORE_REPLAY_IDENTITY,
        SINGLE_COMMAND_PER_ENTITY,
        ZERO_ADVANCE_IS_NOOP,
    ]
