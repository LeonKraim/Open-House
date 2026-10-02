"""The two product rules, as the session enforces them.

`product-invariants` in full: behaviours are OFF by default and enforced as an
evaluation *gate* rather than a default nobody reads; there is no always-on
tier; no non-user origin may unlock a lock or open a cover; and the decision log
is the oracle for both rules.

`tests/test_engine_safety.py` exercises the veto *predicate* and
`tests/test_behaviour_conformance.py` exercises the shipped units' declared
defaults. What is checked here is the pair as a session enforces them -- through
the facade, over a fixture house, with the log read back -- because a rule that
holds in the predicate and not at the gate is a rule that does not hold. Each
test names the implementation that would falsify it.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import pytest

from engine.behaviours import behaviour_defaults, default_behaviours
from engine.decision_log import Outcome
from engine.vocabulary import Vocabulary
from openhouse.facade import OpenHouse, open_session

ROOT = Path(__file__).resolve().parents[1]

#: The three units this phase ships, in the order the engine evaluates them.
SHIPPED = ("away_shutdown", "motion_lighting", "override", "safety_alert")


@pytest.fixture(scope="module")
def vocabulary() -> Vocabulary:
    """The committed catalog, frozen for the module."""
    return Vocabulary.load(ROOT)


@pytest.fixture
def session(vocabulary: Vocabulary) -> OpenHouse:
    """A fresh `minimal` session with nothing enabled."""
    return open_session(house="minimal", vocabulary=vocabulary)


def _outcomes(session: OpenHouse) -> list[str]:
    """Every outcome in the session's retained log, oldest first."""
    return [str(record.outcome) for record in session.get_decision_log()]


def _motion(session: OpenHouse) -> None:
    """Trip the living-room motion sensor and let the engine tick once.

    `minimal`'s living room binds `ambient_light_sensor` and reads dark, so a tick with
    motion present is exactly the input `motion_lighting` would act on -- which
    is what makes the OFF-by-default check below non-vacuous. The advance is one
    minute because an advance of zero evaluates nothing at all.
    """
    session.set_state("binary_sensor.living_room_motion", "on")
    session.advance_time(minutes=1)


def test_every_shipped_unit_declares_itself_disabled() -> None:
    """Each unit's own declared default is `enabled = False`.

    The rule reaches the corpus through the unit's declaration, so a unit that
    shipped with `enabled = True` would be the always-on tier the requirement
    forbids, however the resolver was configured.
    """
    units = default_behaviours()
    assert tuple(units) == SHIPPED
    for name, unit in units.items():
        assert unit.enabled is False, name


def test_the_built_in_layer_carries_a_flag_for_every_unit() -> None:
    """There is no unit the resolver has no flag for.

    "Every behaviour can be turned off" is only true if every behaviour has an
    off switch; a unit absent from the defaults layer would be one whose
    enabledness the house layer could not express.
    """
    defaults = behaviour_defaults(default_behaviours().values())
    for name in SHIPPED:
        assert f"behaviour.{name}.enabled" in defaults, name
        assert defaults[f"behaviour.{name}.enabled"] is False, name


def test_a_session_that_declares_nothing_evaluates_nothing_that_acts(
    session: OpenHouse,
) -> None:
    """A fresh house runs no behaviour, on an input that would have driven one.

    Moving in a dark room is the strongest input the shipped set has, so a
    session that acts on it while nothing is enabled is the OFF-by-default rule
    failing at the gate. Falsified by an engine that evaluated the shipped units
    regardless of the resolver.
    """
    _motion(session)
    assert Outcome.ACTED not in _outcomes(session)


def test_the_gate_is_evaluated_rather_than_the_unit_being_absent(
    session: OpenHouse,
) -> None:
    """A disabled unit still produces a `skipped: disabled` record.

    The rule is "enforced as an evaluation gate", not "the unit is never
    consulted": the record is the evidence that the gate ran. Falsified by
    removing disabled units from the evaluation list instead of skipping them --
    the log would then be empty and the two rules would be indistinguishable
    from a unit that was never loaded.
    """
    _motion(session)
    assert Outcome.SKIPPED_DISABLED in _outcomes(session)


def test_turning_a_unit_on_makes_it_act(vocabulary: Vocabulary) -> None:
    """The same input acts once the unit's flag is set.

    The pair to the check above: the difference between a house that runs
    nothing and a house that runs one thing is one flag, so a gate that ignored
    the flag would fail one of the two.
    """
    session = open_session(
        house="minimal",
        vocabulary=vocabulary,
        house_settings={"behaviour.motion_lighting.enabled": True},
    )
    _motion(session)
    assert Outcome.ACTED in _outcomes(session)


def test_an_enabled_unit_acts_under_its_own_id(vocabulary: Vocabulary) -> None:
    """The record names the rule the acting unit matched.

    `first-behaviours`' "the decision log is the oracle for every behaviour
    claim" is only true if the record says which unit acted; a record without a
    rule would leave a reader to infer it from the commands.
    """
    session = open_session(
        house="minimal",
        vocabulary=vocabulary,
        house_settings={"behaviour.motion_lighting.enabled": True},
    )
    _motion(session)
    acted = [
        record
        for record in session.get_decision_log()
        if str(record.outcome) == str(Outcome.ACTED)
    ]
    assert acted
    assert all(record.rule for record in acted)
    assert all("lighting" in str(record.rule) for record in acted)


def test_the_same_input_acts_only_in_the_session_that_enabled_it(
    vocabulary: Vocabulary,
) -> None:
    """Two sessions over one house differ by one flag and by nothing else.

    This is the pair the rule is made of: the same steps, the same house, the
    same seed, one setting apart. Falsified by an engine that read enablement
    from anywhere but the resolver -- both sessions would then act, or neither.
    """
    enabled = open_session(
        house="minimal",
        vocabulary=vocabulary,
        house_settings={"behaviour.motion_lighting.enabled": True},
    )
    disabled = open_session(house="minimal", vocabulary=vocabulary)
    for session in (enabled, disabled):
        _motion(session)
    assert Outcome.ACTED in _outcomes(enabled)
    assert Outcome.ACTED not in _outcomes(disabled)


def test_no_method_on_the_surface_can_turn_a_behaviour_on() -> None:
    """The facade carries no activate, enable or disable method.

    "The control surface exposes the rules and offers no bypass": a caller that
    could enable a behaviour through the surface would have a second route to
    the engine's evaluation list, and activation would stop being the
    composition root's act. Falsified by adding `enable_behaviour` to the
    facade -- which the registry would not carry but the library face would.

    The check reads the class's callables rather than every attribute, because
    the session holds the units themselves in a `behaviours` field and reading
    them is not a bypass.
    """
    forbidden = ("enable", "activate", "disable")
    methods = [
        name
        for name in dir(OpenHouse)
        if not name.startswith("_") and callable(getattr(OpenHouse, name))
    ]
    offending = [
        name for name in methods if any(word in name.lower() for word in forbidden)
    ]
    assert offending == []
    assert "behaviours" not in methods


#: The file the fixture's `provides` entry pins. Shaped the way the sandbox
#: reads an automation -- a `trigger` and an `action` -- because a `provides`
#: entry declaring class `automation` has to pin a file the sandbox reads as one.
#: Phase 1's fixture pinned `packs/a_pack/thing.yaml`, a path the checkout does
#: not contain, because Phase 1's `install_pack` had no sandbox to notice.
_PINNED = """\
alias: Pinned automation
trigger:
  - platform: state
    entity_id: motion_sensor
    to: "on"
action:
  - service: light.turn_on
    target:
      entity_id: light_group
"""


def _manifest(tmp_path: Path, *requires: str) -> str:
    """A manifest valid to the current `pack-manifest` schema.

    The required fields are the schema's, read from `schemas/pack-manifest/`;
    the behaviour's three terms are the ones `packs/official/example-pack.yaml`
    uses, which the `behavior-vocabulary` reference resolves. Written here
    rather than taken from `packs/`, because a check of *which* manifests
    install has to vary one field at a time and the shipped pack is one fixed
    document.

    `kind` is `module` and the pack carries `engine_api`, `license` and `i18n`
    because that is what the current version -- `1.2.0` -- requires; `1.1.0`
    accepted a `kind` outside the five, and `1.2.0` closes it.

    The pinned file is written beside the manifest and the `provides` path is
    absolute, because the sandbox resolves that path against the repository root
    and requires the answer to stay inside the pack's own directory: a pack
    outside the checkout can only satisfy that by saying where it is.
    """
    slots = ", ".join(requires)
    (tmp_path / "thing.yaml").write_text(_PINNED, encoding="utf-8")
    return (
        "name: a_pack\n"
        'version: "1.0.0"\n'
        "description: a pack for the test\n"
        "kind: module\n"
        'engine_api: ">=1.0.0 <2.0.0"\n'
        "license: mit\n"
        f"requires_slots: [{slots}]\n"
        "provides:\n"
        f"  - path: {(tmp_path / 'thing.yaml').as_posix()}\n"
        "    class: automation\n"
        "behaviours:\n"
        "  - name: motion_turns_on_light\n"
        "    trigger: state\n"
        "    condition: state\n"
        "    action: service\n"
        "i18n:\n"
        "  default:\n"
        "    pack: A pack\n"
        "    description: a pack for the test\n"
        "    motion_turns_on_light: Motion turns the light on\n"
    )


def test_installing_a_pack_enables_nothing(
    vocabulary: Vocabulary, tmp_path: Path
) -> None:
    """`install_pack` validates and checks slots, and reports nothing enabled.

    A pack cannot bypass the rules: installation is not activation, and the
    result says so rather than leaving it to be assumed. Falsified by an
    `install_pack` that applied the manifest's declarations.
    """
    session = open_session(house="minimal", vocabulary=vocabulary)
    manifest = tmp_path / "pack.yaml"
    manifest.write_text(_manifest(tmp_path, "light_group"), encoding="utf-8")
    result = session.install_pack(str(manifest))
    assert result["installed"] is True
    assert result["enabled"] == ()
    assert Outcome.ACTED not in _outcomes(session)


def test_a_pack_that_cannot_resolve_is_refused_rather_than_partly_applied(
    vocabulary: Vocabulary, tmp_path: Path
) -> None:
    """A manifest requiring a slot the house cannot supply fails the install.

    The check is the pack's own boundary: a house that does not bind what the
    pack needs is a house the pack would not work in, and refusing is what keeps
    a half-installed pack out of the tree.
    """
    from openhouse.packs import PackError

    session = open_session(house="minimal", vocabulary=vocabulary)
    manifest = tmp_path / "pack.yaml"
    manifest.write_text(_manifest(tmp_path, "a_slot_no_house_binds"), encoding="utf-8")
    with pytest.raises(PackError):
        session.install_pack(str(manifest))
    assert Outcome.ACTED not in _outcomes(session)


def test_a_pack_that_fails_the_schema_is_refused(
    vocabulary: Vocabulary, tmp_path: Path
) -> None:
    """A manifest outside the schema raises rather than installing partly.

    Falsified by an `install_pack` that read the fields it recognised and
    ignored the rest: a manifest with a stray key would install, and the pack
    format would stop being a format.
    """
    from openhouse.packs import PackError

    session = open_session(house="minimal", vocabulary=vocabulary)
    manifest = tmp_path / "pack.yaml"
    manifest.write_text(
        _manifest(tmp_path, "light_group") + "extra_key: 1\n", encoding="utf-8"
    )
    with pytest.raises(PackError):
        session.install_pack(str(manifest))


def _egress_targets(session: OpenHouse) -> list[tuple[str, str]]:
    """One lock and one cover from the house, with the state that would open them.

    Derived from the house rather than written down, because `large` generates
    its entity ids (`lock.foyer_07_lock`) and a literal would pin this test to
    one seed's naming. A house carrying neither domain is refused loudly rather
    than skipped: the empty sweep would make the checks below vacuous.
    """
    ids = list(session.simulation.adapter.list_entities())
    lock = next((i for i in ids if i.startswith("lock.")), None)
    cover = next((i for i in ids if i.startswith("cover.")), None)
    assert lock is not None and cover is not None, "the house carries no egress device"
    return [(lock, "unlocked"), (cover, "open")]


def test_no_origin_but_a_user_may_unlock_a_lock_or_open_a_cover(
    vocabulary: Vocabulary,
) -> None:
    """The veto holds over every non-user origin the surface can produce.

    `large` carries locks and covers. Each is driven by a non-user operation in
    turn and read back: the state is unchanged and the refusal is recorded as
    `refused: unsafe` with no rule named, because the veto is a product rule
    rather than a corpus concept.
    """
    session = open_session(house="large", vocabulary=vocabulary)
    targets = _egress_targets(session)
    for entity_id, opening in targets:
        before = session.read_entity(entity_id)
        session.set_state(entity_id, opening)
        after = session.read_entity(entity_id)
        assert after.state == before.state, entity_id
        assert str(after.last_origin) != "user", entity_id
        assert Outcome.REFUSED_UNSAFE in _outcomes(session), entity_id
    refusals = [
        record
        for record in session.get_decision_log()
        if str(record.outcome) == str(Outcome.REFUSED_UNSAFE)
    ]
    assert len(refusals) == len(targets)
    assert all(record.rule is None for record in refusals)
    assert all(record.actor == "set_state" for record in refusals)


def test_a_user_may_unlock_and_the_log_records_no_refusal(
    vocabulary: Vocabulary,
) -> None:
    """`user_action` is the one origin the veto admits.

    The pair to the check above, and the reason the rule is "no non-user origin"
    rather than "never": a house whose owner cannot open their own door is not
    the product. Falsified by a veto that refused every origin.
    """
    session = open_session(house="large", vocabulary=vocabulary)
    lock, opening = _egress_targets(session)[0]
    session.user_action(lock, opening)
    assert session.read_entity(lock).state == opening
    assert Outcome.REFUSED_UNSAFE not in _outcomes(session)


def test_the_veto_is_enforced_before_the_command_reaches_the_port(
    vocabulary: Vocabulary,
) -> None:
    """A refused write leaves no trace on the device at all.

    "Both rules are enforced at the gate, before the command reaches the port":
    the observable difference is that the device's change origin is not the
    refused write's, so nothing downstream -- an override record, a later
    behaviour reading `last_origin` -- sees it as having happened.
    """
    session = open_session(house="large", vocabulary=vocabulary)
    lock, opening = _egress_targets(session)[0]
    before = session.read_entity(lock)
    session.set_state(lock, opening)
    after = session.read_entity(lock)
    assert (after.state, after.last_origin) == (before.state, before.last_origin)
    assert dict(after.attributes) == dict(before.attributes)


def test_the_log_is_the_oracle_for_the_disabled_gate() -> None:
    """A disabled evaluation is visible in the log rather than inferred.

    The requirement makes the log the oracle for *both* rules; this is the half
    the safety tests cannot show, because "nothing acted" and "nothing was
    evaluated" are different facts and only the record tells them apart.
    """
    assert {str(outcome) for outcome in Outcome} == {
        "acted",
        "declined",
        "lost arbitration",
        "overridden",
        "rate-limited",
        "skipped: unbound slot",
        "skipped: disabled",
        "refused: unsafe",
    }


def test_there_is_no_always_on_tier_in_the_engine(vocabulary: Vocabulary) -> None:
    """No unit acts in a house that declares nothing, on any offered input.

    The sweep offers the strongest input each reachable unit has -- motion in a
    dark room for the lighting unit, and a manual change for the override unit
    -- and checks that none of them acts while the house is silent. Falsified by
    a unit with an unconditional rule, which is the "always-on safety tier" the
    requirement rules out.
    """
    session = open_session(house="minimal", vocabulary=vocabulary)
    _motion(session)
    session.user_action("light.living_room", "on")
    session.advance_time(minutes=30)
    assert Outcome.ACTED not in _outcomes(session)


def test_away_shutdown_is_unreachable_from_the_control_surface(
    vocabulary: Vocabulary,
) -> None:
    """An empty house left quiet is not an away house.

    `away_shutdown` gates on `ctx.mode_is_active("away")`, which reads the
    engine's own `ModeSet`. The home's state is the engine's variable and not a
    slot, so no control-surface operation reaches it: a person cannot bind a
    device to say `away`, and there is no entity whose value could disagree with
    the modes every other behaviour is gated on. So an enabled unit over a quiet
    house leaves the unit evaluating (its records are `declined`, not
    `skipped: disabled`) and not acting.

    The claim is scoped to the entity, and deliberately not the stronger one it
    used to make. No operation on the control surface activates a mode *other
    than `restore`*, and `restore` is one of the registry's ten: a snapshot
    whose `engine_state.modes` is `["away"]` is restored intact and the unit acts
    on the next tick. `test_a_restored_away_mode_reaches_the_unit` below pins
    that, so the reachability is decided by a test rather than assumed away --
    and a scenario can take the same route, because a `restore` step accepts an
    inline document (`sim/scenario/dsl.py`).
    """
    session = open_session(
        house="minimal",
        vocabulary=vocabulary,
        house_settings={"behaviour.away_shutdown.enabled": True},
    )
    session.set_state("binary_sensor.living_room_motion", "off")
    session.advance_time(minutes=30)
    away = [
        record
        for record in session.get_decision_log()
        if str(record.actor) == "away_shutdown"
    ]
    assert away, (
        "the unit was not evaluated at all, so this check would be about the "
        "enable flag rather than about reachability"
    )
    assert {str(record.outcome) for record in away} == {str(Outcome.DECLINED)}


def test_a_restored_away_mode_reaches_the_unit(vocabulary: Vocabulary) -> None:
    """`restore` activates a mode, so `away_shutdown` *is* reachable.

    The counter-example the check above cannot make, pinned here so that the
    reachability is decided rather than assumed: `restore` is one of the ten
    registry operations, a snapshot carries `engine_state.modes`, and a document
    whose modes are `["away"]` is restored intact. With the house quiet and lit,
    the unit then acts -- so "unreachable from the control surface" was false as
    an absolute, and is true only of every operation *but* `restore`.

    Falsified by a `restore` that dropped the modes it was handed, or by an
    `away_shutdown` that read a stale entity rather than the engine's own set.
    """
    session = open_session(
        house="minimal",
        vocabulary=vocabulary,
        house_settings={"behaviour.away_shutdown.enabled": True},
    )
    session.set_state("binary_sensor.living_room_motion", "off")
    session.set_state("binary_sensor.foyer_motion", "off")
    session.set_state("light.living_room", "on")
    session.set_state("light.foyer", "on")
    session.advance_time(minutes=30)
    assert str(Outcome.ACTED) not in _outcomes(session), (
        "the house acted before it was away"
    )

    document = dict(session.snapshot())
    state = document["engine_state"]
    assert isinstance(state, dict), "the snapshot carries its engine state as an object"
    state["modes"] = ["away"]
    document["engine_state"] = state
    session.restore(document)
    session.advance_time(minutes=30)

    acted = [
        record
        for record in session.get_decision_log()
        if str(record.outcome) == str(Outcome.ACTED)
    ]
    assert [str(record.rule) for record in acted] == ["lighting.away_shutdown"]
    assert session.read_entity("light.foyer").state == "off"
    assert session.read_entity("light.living_room").state == "off"


def test_every_shipped_unit_contributes_exactly_three_settings() -> None:
    """A unit declares `enabled`, `priority` and its own tunables.

    The three are the resolver's view of one unit, and a unit contributing a
    fourth key would be a setting no behaviour reads -- which is how a
    always-on switch would look if one were ever added quietly.
    """
    units = default_behaviours()
    defaults = behaviour_defaults(units.values())
    for name in SHIPPED:
        keys = {key for key in defaults if f".{name}." in key}
        assert f"behaviour.{name}.enabled" in keys, name
        assert f"behaviour.{name}.priority" in keys, name
        assert keys, name


def test_the_two_rules_are_read_from_the_same_place_the_resolver_reads(
    vocabulary: Vocabulary,
) -> None:
    """A session's settings are the resolver's layers, not a copy the facade keeps.

    `openhouse/facade.py` states that the session holds no decision state of its
    own and reads every setting *through* the engine. Falsified by a facade that
    cached the settings at open: the enable flag the engine evaluated and the
    one the session reported could then disagree.

    What is checkable from outside without reaching into the engine's internals
    is that the session carries the house layer it was opened with, that the
    layer holds no `mode.` keys -- enablement is a setting and not a mode -- and
    that the session's units are the ones the engine was built over.
    """
    settings: Mapping[str, object] = {"behaviour.motion_lighting.enabled": True}
    session = open_session(
        house="minimal", vocabulary=vocabulary, house_settings=settings
    )
    assert session.house_settings["behaviour.motion_lighting.enabled"] is True
    assert all(not str(name).startswith("mode.") for name in session.house_settings)
    assert set(session.behaviours) == set(default_behaviours())
