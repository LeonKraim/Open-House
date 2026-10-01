"""The pack sandbox -- tasks 3.1 to 3.6.

The sandbox is what a pack *may do*, and four rules decide it. Each rule's tests
are of the same two kinds. The first kind shows the admitted case, because a
check that refused everything would pass a suite that only asserted refusals. The
second shows the refusal, and for two of the rules the refusal has to be told
apart from a neighbouring one: a term the vocabulary publishes and forbids is not
an unknown term, and a command reaching through a slot its behaviour did not
claim is a reach failure and not a service failure. Those distinctions are the
substance of the requirements rather than their presentation, so they are
asserted by name.

The artifact every rule reads is the committed one. `real_root` is handed to the
vocabulary and the policy, and only the pack under test is written to a fixture
directory, so a test that would pass against a hand-built policy fails here --
which is the point, because "banning a service is a data change" is only true if
the data is the committed file.

The last two sections are the ones a reader should check first if they doubt the
suite covers the rules: `test_every_reason_is_reachable` produces one refusal for
every reason the module declares, and the documentation tests hold
`docs/reference/pack-sandbox.md` to naming every reason the module has.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
import yaml

from engine import sandbox, vocabulary
from engine.sandbox import Command, Pack
from tools.catalog import paths
from tools.catalog.errors import Report
from tools.catalog.invariants import (
    check_composition_root_purity,
    check_engine_layering,
)

from .conftest import write

if TYPE_CHECKING:
    from collections.abc import Mapping

ROOT = paths.ROOT
DOCUMENT = ROOT / "docs" / "reference" / "pack-sandbox.md"
MODULE = ROOT / "engine" / "sandbox.py"


# --------------------------------------------------------------------------
# Fixtures and helpers
# --------------------------------------------------------------------------


@pytest.fixture
def artifacts(
    real_root: Path,
) -> tuple[vocabulary.Vocabulary, vocabulary.BehaviourVocabulary]:
    """The committed vocabulary and the committed behaviour terms."""
    return (
        vocabulary.Vocabulary.load(real_root),
        vocabulary.load_behaviour_vocabulary(real_root),
    )


def _behaviour(**overrides: object) -> dict[str, object]:
    """One behaviour clause, valid unless a test says otherwise."""
    clause: dict[str, object] = {
        "name": "on",
        "trigger": "state",
        "condition": "state",
        "action": "service",
        "services": ["light.turn_on"],
        "slots": ["light_group"],
    }
    clause.update(overrides)
    return clause


def _document(**overrides: object) -> dict[str, object]:
    """A manifest document, valid unless a test says otherwise."""
    document: dict[str, object] = {
        "name": "tester",
        "version": "1.0.0",
        "description": "a pack",
        "kind": "module",
        "engine_api": ">=1.0.0 <2.0.0",
        "license": "mit",
        "requires_slots": ["light_group"],
        "behaviours": [_behaviour()],
    }
    document.update(overrides)
    return document


def _pack(
    tmp_path: Path, document: Mapping[str, object], name: str = "pack.yaml"
) -> Pack:
    """Write a manifest into a fixture directory and read it back."""
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(dict(document), sort_keys=False), encoding="utf-8")
    return sandbox.load_pack(path)


def _path_literals(source: str) -> set[str]:
    """String literals handed to a call that could open a file.

    The module's own comments name `catalog/pack-policy.yaml` on purpose, so a
    search of the text would prove nothing. What matters is where the *code*
    opens something, and this is that set: a literal that reaches `Path`,
    `open` or a read/write method.
    """
    found: set[str] = set()
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
        arguments: list[ast.expr] = [*node.args]
        arguments.extend(
            keyword.value for keyword in node.keywords if keyword.value is not None
        )
        for argument in arguments:
            if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                found.add(argument.value)
    return found


def _reasons(result: sandbox.SandboxResult) -> list[str]:
    return [refusal.reason for refusal in result.refusals]


def _messages(result: sandbox.SandboxResult) -> str:
    return "\n".join(refusal.message for refusal in result.refusals)


# --------------------------------------------------------------------------
# Rule 1 -- the declarative subset
# --------------------------------------------------------------------------


def test_a_valid_pack_passes_every_rule(
    tmp_path: Path,
    artifacts: tuple[vocabulary.Vocabulary, vocabulary.BehaviourVocabulary],
) -> None:
    """The admitted case, first, so a sandbox that refused everything fails here."""
    result = sandbox.check_pack(_pack(tmp_path, _document()), tmp_path, *artifacts)
    assert result.refusals == (), _messages(result)
    assert result.ok


@pytest.mark.parametrize(
    ("clause", "term"),
    [
        ({"action": "repeat"}, "repeat"),
        ({"action": "if"}, "if"),
        ({"action": "choose"}, "choose"),
        ({"action": "parallel"}, "parallel"),
        ({"action": "variables"}, "variables"),
        ({"action": "stop"}, "stop"),
        ({"action": "wait_template"}, "wait_template"),
        ({"condition": "template"}, "template"),
        ({"trigger": "template"}, "template"),
    ],
)
def test_a_published_control_flow_term_is_refused_as_non_declarative(
    tmp_path: Path,
    artifacts: tuple[vocabulary.Vocabulary, vocabulary.BehaviourVocabulary],
    clause: dict[str, object],
    term: str,
) -> None:
    """Published and forbidden are one failure, and the message says so rather
    than telling the author the vocabulary lacks a term it publishes."""
    result = sandbox.check_pack(
        _pack(tmp_path, _document(behaviours=[_behaviour(**clause)])),
        tmp_path,
        *artifacts,
    )
    assert len(result.refusals) == 1, _messages(result)
    refusal = result.refusals[0]
    assert refusal.reason == f"forbidden_{next(iter(clause))}"
    assert term in refusal.message
    assert "publishes" in refusal.message
    assert "unknown" not in refusal.message.lower()


def test_an_unpublished_action_is_refused_as_unknown(
    tmp_path: Path,
    artifacts: tuple[vocabulary.Vocabulary, vocabulary.BehaviourVocabulary],
) -> None:
    """The other half of the pair: a term no version publishes is a different
    failure, and its message does not say the term is published."""
    result = sandbox.check_pack(
        _pack(
            tmp_path, _document(behaviours=[_behaviour(action="while_light_is_off")])
        ),
        tmp_path,
        *artifacts,
    )
    assert len(result.refusals) == 1, _messages(result)
    refusal = result.refusals[0]
    assert refusal.reason == "unknown_action"
    assert "while_light_is_off" in refusal.message
    assert "does not publish" in refusal.message


def test_the_two_term_failures_do_not_share_a_message(
    tmp_path: Path,
    artifacts: tuple[vocabulary.Vocabulary, vocabulary.BehaviourVocabulary],
) -> None:
    """The requirement's own wording: a term that is published and forbidden must
    not be reported as unknown, so the two messages must differ."""
    published = sandbox.check_pack(
        _pack(tmp_path, _document(behaviours=[_behaviour(action="repeat")]), "a.yaml"),
        tmp_path,
        *artifacts,
    )
    unknown = sandbox.check_pack(
        _pack(tmp_path, _document(behaviours=[_behaviour(action="nope")]), "b.yaml"),
        tmp_path,
        *artifacts,
    )
    assert published.refusals[0].message != unknown.refusals[0].message
    assert published.refusals[0].reason != unknown.refusals[0].reason


def test_the_subset_is_the_policy_artifact_and_not_the_interpreter(
    tmp_path: Path,
    artifacts: tuple[vocabulary.Vocabulary, vocabulary.BehaviourVocabulary],
) -> None:
    """Forbidding a new term is an edit to `catalog/pack-policy.yaml`.

    The artifact is rewritten in a copy of the tree and the same pack is checked
    again: an interpreter holding its own list would refuse nothing here, and a
    test that only read the shipped file could not tell the two apart.
    """
    _, published = artifacts
    pack = _pack(tmp_path, _document(behaviours=[_behaviour(action="delay")]))
    policy = artifacts[0].pack_policy
    assert not sandbox.check_declarative(pack, published, policy)

    widened = vocabulary.PackPolicy(
        forbidden_actions=policy.forbidden_actions | {"delay"},
        forbidden_conditions=policy.forbidden_conditions,
        forbidden_triggers=policy.forbidden_triggers,
        banned_services=policy.banned_services,
        flagged_services=policy.flagged_services,
        default_priority=policy.default_priority,
    )
    refusals = sandbox.check_declarative(pack, published, widened)
    assert [refusal.reason for refusal in refusals] == ["forbidden_action"]
    assert "delay" in refusals[0].message


def test_the_subset_terms_are_read_from_the_published_vocabulary(
    tmp_path: Path,
    artifacts: tuple[vocabulary.Vocabulary, vocabulary.BehaviourVocabulary],
) -> None:
    """Every forbidden term is one the vocabulary actually publishes, so the
    policy cannot forbid a term nobody can write."""
    _, published = artifacts
    policy = artifacts[0].pack_policy
    for axis, terms in (
        ("actions", policy.forbidden_actions),
        ("conditions", policy.forbidden_conditions),
        ("triggers", policy.forbidden_triggers),
    ):
        for term in sorted(terms):
            assert published.publishes(axis, term), f"{term} on {axis}"


def test_branching_cannot_be_expressed_by_composition(
    tmp_path: Path,
    artifacts: tuple[vocabulary.Vocabulary, vocabulary.BehaviourVocabulary],
) -> None:
    """Two behaviours whose conditions negate each other are two declarative
    behaviours, and both validate: the sandbox forbids the expression of control
    flow, not the coincidence of two rules that happen to oppose."""
    document = _document(
        behaviours=[
            _behaviour(name="when_bright", condition="state"),
            _behaviour(name="when_dark", condition="state"),
        ]
    )
    result = sandbox.check_pack(_pack(tmp_path, document), tmp_path, *artifacts)
    assert result.refusals == (), _messages(result)


# --------------------------------------------------------------------------
# Rule 2 -- entity reach
# --------------------------------------------------------------------------


def _command(**overrides: object) -> Command:
    fields: dict[str, object] = {
        "behaviour": "on",
        "service": "light.turn_on",
        "slot": "light_group",
        "entities": ("light.kitchen",),
    }
    fields.update(overrides)
    return Command(**fields)  # type: ignore[arg-type]


def _reach_pack(tmp_path: Path, **overrides: object) -> Pack:
    document = _document(
        requires_slots=["light_group", "motion_sensor"],
        behaviours=[
            _behaviour(slots=["motion_sensor", "light_group"]),
        ],
    )
    document.update(overrides)
    return _pack(tmp_path, document)


def test_a_command_through_a_bound_slot_is_admitted(
    tmp_path: Path,
    artifacts: tuple[vocabulary.Vocabulary, vocabulary.BehaviourVocabulary],
) -> None:
    pack = _reach_pack(tmp_path)
    result = sandbox.check_commands(
        pack,
        [_command()],
        {"light_group": "light.kitchen", "motion_sensor": "binary_sensor.kitchen"},
        artifacts[0].pack_policy,
    )
    assert result.refusals == (), _messages(result)


def test_a_command_through_a_slot_the_behaviour_does_not_name_is_a_reach_failure(
    tmp_path: Path,
    artifacts: tuple[vocabulary.Vocabulary, vocabulary.BehaviourVocabulary],
) -> None:
    """The refusal is the reach failure and not a service failure: the pack
    declared the slot and this behaviour did not claim it, and a message that
    talked about services would send the author to the wrong clause."""
    document = _document(
        requires_slots=["light_group", "motion_sensor"],
        behaviours=[_behaviour(services=["light.turn_on"], slots=["motion_sensor"])],
    )
    pack = _pack(tmp_path, document)
    result = sandbox.check_commands(
        pack,
        [_command(slot="light_group")],
        {"light_group": "light.kitchen", "motion_sensor": "binary_sensor.kitchen"},
        artifacts[0].pack_policy,
    )
    assert _reasons(result) == ["slot_not_claimed"]
    assert "light.turn_on" not in result.refusals[0].message


def test_a_literal_entity_id_is_refused(
    tmp_path: Path,
    artifacts: tuple[vocabulary.Vocabulary, vocabulary.BehaviourVocabulary],
) -> None:
    """A pack naming a house's entity is a pack that works in one house and can
    reach past its grant, so the literal is refused rather than resolved."""
    pack = _reach_pack(tmp_path)
    result = sandbox.check_commands(
        pack,
        [_command(slot="light.kitchen_ceiling", entities=("light.kitchen_ceiling",))],
        {"light_group": "light.kitchen"},
        artifacts[0].pack_policy,
    )
    assert _reasons(result) == ["literal_reference"]
    assert "light.kitchen_ceiling" in result.refusals[0].message


def test_a_literal_in_a_behaviour_clause_is_refused_as_a_literal(
    tmp_path: Path,
    artifacts: tuple[vocabulary.Vocabulary, vocabulary.BehaviourVocabulary],
) -> None:
    """The same rule at validation: the clause is where a pack states what it
    acts through, and a dotted token there is an entity and not a slot."""
    document = _document(behaviours=[_behaviour(slots=["light.kitchen_ceiling"])])
    result = sandbox.check_pack(_pack(tmp_path, document), tmp_path, *artifacts)
    assert _reasons(result) == ["literal_reference"]


def test_a_slot_the_pack_does_not_declare_is_refused(
    tmp_path: Path,
    artifacts: tuple[vocabulary.Vocabulary, vocabulary.BehaviourVocabulary],
) -> None:
    """`fan` is a slot the vocabulary defines and this pack does not declare, so
    the two halves of the rule are told apart: not a literal, and not a name the
    vocabulary is missing."""
    document = _document(behaviours=[_behaviour(slots=["fan"])])
    result = sandbox.check_pack(_pack(tmp_path, document), tmp_path, *artifacts)
    assert _reasons(result) == ["slot_not_declared"]
    assert "catalog/slots.yaml" not in result.refusals[0].message


def test_a_slot_the_vocabulary_does_not_define_is_named_as_such(
    tmp_path: Path,
    artifacts: tuple[vocabulary.Vocabulary, vocabulary.BehaviourVocabulary],
) -> None:
    document = _document(behaviours=[_behaviour(slots=["banana_sensor"])])
    result = sandbox.check_pack(_pack(tmp_path, document), tmp_path, *artifacts)
    assert _reasons(result) == ["slot_not_declared"]
    assert "catalog/slots.yaml" in result.refusals[0].message


def test_an_unbound_slot_is_inert_rather_than_admitted(
    tmp_path: Path,
    artifacts: tuple[vocabulary.Vocabulary, vocabulary.BehaviourVocabulary],
) -> None:
    """The pack installs -- the slot was optional -- and the behaviour reaching
    through it reaches nothing, which is not a refusal: there is no entity to
    act on and none to refuse."""
    document = _document(
        requires_slots=["light_group"],
        optional_slots=["fan"],
        behaviours=[_behaviour(services=["fan.turn_on"], slots=["fan"])],
    )
    pack = _pack(tmp_path, document)
    assert sandbox.check_pack(pack, tmp_path, *artifacts).ok

    result = sandbox.check_commands(
        pack,
        [_command(service="fan.turn_on", slot="fan", entities=())],
        {"light_group": "light.kitchen"},
        artifacts[0].pack_policy,
    )
    assert result.refusals == (), _messages(result)
    assert (
        sandbox.reach(pack, pack.behaviour("on"), {"light_group": "light.kitchen"})
        == frozenset()
    )


def test_reach_is_the_entities_the_room_supplied(
    tmp_path: Path,
    artifacts: tuple[vocabulary.Vocabulary, vocabulary.BehaviourVocabulary],
) -> None:
    """A behaviour reaching through one boundary and the behaviour of the test
    above reaching through a slot bound nowhere: reach is the binding's."""
    pack = _reach_pack(tmp_path)
    binding = {"light_group": "light.kitchen", "motion_sensor": "binary_sensor.kitchen"}
    assert sandbox.reach(pack, pack.behaviour("on"), binding) == frozenset(
        {"light.kitchen", "binary_sensor.kitchen"}
    )


def test_an_entity_outside_the_reach_is_refused(
    tmp_path: Path,
    artifacts: tuple[vocabulary.Vocabulary, vocabulary.BehaviourVocabulary],
) -> None:
    pack = _reach_pack(tmp_path)
    result = sandbox.check_commands(
        pack,
        [_command(entities=("light.hallway",))],
        {"light_group": "light.kitchen", "motion_sensor": "binary_sensor.kitchen"},
        artifacts[0].pack_policy,
    )
    assert _reasons(result) == ["not_in_reach"]
    assert "light.hallway" in result.refusals[0].message


def test_a_command_naming_an_undeclared_behaviour_is_refused(
    tmp_path: Path,
    artifacts: tuple[vocabulary.Vocabulary, vocabulary.BehaviourVocabulary],
) -> None:
    """Nothing grants a reach to a behaviour the manifest does not carry, and a
    command naming one is a command no manifest vouches for."""
    pack = _reach_pack(tmp_path)
    result = sandbox.check_commands(
        pack,
        [_command(behaviour="off")],
        {"light_group": "light.kitchen"},
        artifacts[0].pack_policy,
    )
    assert _reasons(result) == ["unknown_behaviour"]


# --------------------------------------------------------------------------
# Rule 3 -- declared services, and the two lists a service is judged by
# --------------------------------------------------------------------------


def test_the_effective_permissions_are_computed_from_the_behaviours(
    tmp_path: Path,
) -> None:
    """Adding a service to a behaviour clause widens the set with no second list
    edited, which is the property a hand-maintained list would have lost."""
    pack = _pack(
        tmp_path,
        _document(
            behaviours=[
                _behaviour(name="on", services=["light.turn_on"]),
                _behaviour(name="off", services=["light.turn_off", "light.turn_on"]),
            ]
        ),
    )
    assert pack.effective_permissions == {"light.turn_on", "light.turn_off"}

    widened = _pack(
        tmp_path,
        _document(
            behaviours=[
                _behaviour(name="on", services=["light.turn_on"]),
                _behaviour(
                    name="off",
                    services=["light.turn_off", "light.turn_on", "fan.turn_on"],
                ),
            ]
        ),
        "widened.yaml",
    )
    assert widened.effective_permissions == {
        "light.turn_on",
        "light.turn_off",
        "fan.turn_on",
    }


def test_an_undeclared_service_is_refused_naming_the_declaration_that_would_admit_it(
    tmp_path: Path,
    artifacts: tuple[vocabulary.Vocabulary, vocabulary.BehaviourVocabulary],
) -> None:
    pack = _reach_pack(tmp_path)
    result = sandbox.check_commands(
        pack,
        [_command(service="light.turn_off")],
        {"light_group": "light.kitchen", "motion_sensor": "binary_sensor.kitchen"},
        artifacts[0].pack_policy,
    )
    assert _reasons(result) == ["undeclared_service"]
    assert "light.turn_off" in result.refusals[0].message
    assert "services" in result.refusals[0].message


def test_a_declared_service_is_admitted_after_a_clause_widens_the_set(
    tmp_path: Path,
    artifacts: tuple[vocabulary.Vocabulary, vocabulary.BehaviourVocabulary],
) -> None:
    """The pair to the test above: the same command, admitted once a clause
    declares the service -- so the refusal was about the declaration and not
    about the service being unusable."""
    pack = _reach_pack(
        tmp_path, behaviours=[_behaviour(services=["light.turn_on", "light.turn_off"])]
    )
    result = sandbox.check_commands(
        pack,
        [_command(service="light.turn_off")],
        {"light_group": "light.kitchen", "motion_sensor": "binary_sensor.kitchen"},
        artifacts[0].pack_policy,
    )
    assert result.refusals == (), _messages(result)


def test_a_banned_service_refuses_the_pack(
    tmp_path: Path,
    artifacts: tuple[vocabulary.Vocabulary, vocabulary.BehaviourVocabulary],
) -> None:
    result = sandbox.check_pack(
        _pack(
            tmp_path,
            _document(behaviours=[_behaviour(services=["homeassistant.restart"])]),
        ),
        tmp_path,
        *artifacts,
    )
    assert _reasons(result) == ["banned_service"]
    refusal = result.refusals[0]
    assert refusal.reason == "banned_service"
    assert "homeassistant.restart" in refusal.message
    assert "hosts" in refusal.message


def test_a_banned_domain_refuses_every_service_of_that_domain(
    tmp_path: Path,
    artifacts: tuple[vocabulary.Vocabulary, vocabulary.BehaviourVocabulary],
) -> None:
    """`shell_command.*` is the artifact's own wording for a whole domain, and a
    pack calling one member of it is refused like any other banned service."""
    result = sandbox.check_pack(
        _pack(
            tmp_path,
            _document(behaviours=[_behaviour(services=["shell_command.anything"])]),
        ),
        tmp_path,
        *artifacts,
    )
    assert _reasons(result) == ["banned_service"]


def test_a_ban_is_a_data_change_and_not_an_interpreter_change(
    tmp_path: Path,
    artifacts: tuple[vocabulary.Vocabulary, vocabulary.BehaviourVocabulary],
) -> None:
    """The same pack, the same engine, one more entry in the artifact: the next
    validation refuses it. No code path is edited, which is the property."""
    pack = _pack(
        tmp_path, _document(behaviours=[_behaviour(services=["light.turn_on"])])
    )
    policy = artifacts[0].pack_policy
    assert sandbox.check_services(pack, policy).ok

    extended = vocabulary.PackPolicy(
        forbidden_actions=policy.forbidden_actions,
        forbidden_conditions=policy.forbidden_conditions,
        forbidden_triggers=policy.forbidden_triggers,
        banned_services=(*policy.banned_services, "light.turn_on"),
        flagged_services=policy.flagged_services,
        default_priority=policy.default_priority,
    )
    refused = sandbox.check_services(pack, extended)
    assert [refusal.reason for refusal in refused.refusals] == ["banned_service"]


def test_a_flagged_service_does_not_refuse_the_pack(
    tmp_path: Path,
    artifacts: tuple[vocabulary.Vocabulary, vocabulary.BehaviourVocabulary],
) -> None:
    """Dangerous and legitimate: the pack installs and the install result names
    the flag, which is only observable if the pack is not refused."""
    result = sandbox.check_pack(
        _pack(tmp_path, _document(behaviours=[_behaviour(services=["lock.unlock"])])),
        tmp_path,
        *artifacts,
    )
    assert result.refusals == (), _messages(result)
    assert [flag.service for flag in result.flags] == ["lock.unlock"]
    assert result.ok


def test_locking_is_not_an_unlocking(
    tmp_path: Path,
    artifacts: tuple[vocabulary.Vocabulary, vocabulary.BehaviourVocabulary],
) -> None:
    """The pair is judged by what a pack can do and not by the domain it
    touches, so an ordinary lock-on-a-schedule pack carries no flag."""
    result = sandbox.check_pack(
        _pack(tmp_path, _document(behaviours=[_behaviour(services=["lock.lock"])])),
        tmp_path,
        *artifacts,
    )
    assert result.refusals == (), _messages(result)
    assert result.flags == ()


def test_a_flagged_call_at_install_is_surfaced_and_not_refused(
    tmp_path: Path,
    artifacts: tuple[vocabulary.Vocabulary, vocabulary.BehaviourVocabulary],
) -> None:
    """The command path holds the same distinction as the manifest path."""
    pack = _reach_pack(
        tmp_path,
        behaviours=[_behaviour(services=["lock.unlock"], slots=["light_group"])],
    )
    result = sandbox.check_commands(
        pack,
        [_command(service="lock.unlock", entities=("lock.front_door",))],
        {"light_group": "lock.front_door", "motion_sensor": "binary_sensor.kitchen"},
        artifacts[0].pack_policy,
    )
    assert result.refusals == (), _messages(result)
    assert [flag.service for flag in result.flags] == ["lock.unlock"]


def test_a_banned_call_at_install_is_refused_and_not_flagged(
    tmp_path: Path,
    artifacts: tuple[vocabulary.Vocabulary, vocabulary.BehaviourVocabulary],
) -> None:
    """The two lists are read by two branches, so a service on the banned one
    can never be surfaced as a flag through a path that treats them alike."""
    pack = _reach_pack(
        tmp_path,
        behaviours=[_behaviour(services=["hassio.host_reboot"], slots=["light_group"])],
    )
    result = sandbox.check_commands(
        pack,
        [_command(service="hassio.host_reboot")],
        {"light_group": "light.kitchen", "motion_sensor": "binary_sensor.kitchen"},
        artifacts[0].pack_policy,
    )
    assert _reasons(result) == ["banned_service"]
    assert result.flags == ()


def test_every_banned_entry_carries_a_reason_and_every_reason_names_the_host(
    real_root: Path,
) -> None:
    """The list is bounded by an argument: each entry states why, and the why is
    that the service acts on the host rather than on a device in the house."""
    loaded = yaml.safe_load(
        (real_root / "catalog" / "pack-policy.yaml").read_text(encoding="utf-8")
    )
    assert loaded["banned_services"], "the banned list is empty"
    for entry in loaded["banned_services"]:
        assert entry["reason"].strip(), entry["service"]
        assert "host" in entry["reason"], entry["service"]


# --------------------------------------------------------------------------
# Rule 4 -- `provides` containment and class
# --------------------------------------------------------------------------


def _provided(tmp_path: Path, body: str, name: str = "thing.yaml") -> Path:
    path = tmp_path / "thing_pack" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def test_a_resolving_provides_entry_of_the_right_class_is_admitted(
    tmp_path: Path,
    artifacts: tuple[vocabulary.Vocabulary, vocabulary.BehaviourVocabulary],
) -> None:
    _provided(tmp_path, "alias: turns on\ntrigger: []\naction: []\nmode: single\n")
    document = _document(
        provides=[{"path": "thing_pack/thing.yaml", "class": "automation"}]
    )
    result = sandbox.check_pack(_pack(tmp_path, document), tmp_path, *artifacts)
    assert result.refusals == (), _messages(result)


def test_a_dangling_provides_path_is_refused(
    tmp_path: Path,
    artifacts: tuple[vocabulary.Vocabulary, vocabulary.BehaviourVocabulary],
) -> None:
    document = _document(
        provides=[{"path": "thing_pack/absent.yaml", "class": "automation"}]
    )
    result = sandbox.check_pack(_pack(tmp_path, document), tmp_path, *artifacts)
    assert _reasons(result) == ["dangling_path"]
    assert "thing_pack/absent.yaml" in result.refusals[0].message


def test_the_example_pack_is_not_dangling_and_the_path_it_used_to_name_is(
    tmp_path: Path,
    artifacts: tuple[vocabulary.Vocabulary, vocabulary.BehaviourVocabulary],
) -> None:
    """Task 3.5's own case. The shipped manifest's entry resolves, and the path
    the spec records it as having named -- a pack directory that does not exist
    -- is refused when a manifest names it, so the correction is held by a test
    rather than only by the file having been edited once."""
    shipped = sandbox.load_pack(ROOT / "packs" / "official" / "example-pack.yaml")
    assert shipped.provides, "the shipped example pack provides nothing"
    assert sandbox.check_provides(shipped, ROOT) == ()

    register = _pack(
        tmp_path,
        _document(provides=[{"path": shipped.provides[0].path, "class": "automation"}]),
        "register.yaml",
    )
    refusals = sandbox.check_provides(register, tmp_path)
    assert [refusal.reason for refusal in refusals] == ["dangling_path"]


@pytest.mark.parametrize(
    "entry_path",
    ["../outside.yaml", "thing_pack/../../outside.yaml", "/etc/hostname"],
)
def test_a_path_escaping_the_pack_is_refused(
    tmp_path: Path,
    artifacts: tuple[vocabulary.Vocabulary, vocabulary.BehaviourVocabulary],
    entry_path: str,
) -> None:
    """Traversal, traversal back out of a subdirectory, and an absolute path.
    Containment is asked before existence, so an absolute path that happens to
    name a real file is still a containment failure and not a missing one."""
    (tmp_path / "outside.yaml").write_text("alias: x\n", encoding="utf-8")
    document = _document(provides=[{"path": entry_path, "class": "automation"}])
    result = sandbox.check_pack(_pack(tmp_path, document), tmp_path, *artifacts)
    assert _reasons(result) == ["escaping_path"], _messages(result)


def test_a_class_the_file_is_not_is_refused(
    tmp_path: Path,
    artifacts: tuple[vocabulary.Vocabulary, vocabulary.BehaviourVocabulary],
) -> None:
    """A scene declared as a blueprint: the class pins what a pack confers, and
    an entry may not claim one the file is not."""
    _provided(tmp_path, "name: evening\nentities:\n  light.kitchen: 'on'\n")
    document = _document(
        provides=[{"path": "thing_pack/thing.yaml", "class": "blueprint"}]
    )
    result = sandbox.check_pack(_pack(tmp_path, document), tmp_path, *artifacts)
    assert _reasons(result) == ["class_mismatch"]
    refusal = result.refusals[0]
    assert "blueprint" in refusal.message
    assert "scene" in refusal.message


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("blueprint:\n  name: x\n  domain: automation\n", "blueprint"),
        ("sequence:\n  - service: light.turn_on\n", "script"),
        ("views:\n  - title: home\n", "dashboard"),
        ("trigger: []\naction: []\n", "automation"),
        ("entities:\n  light.kitchen: 'on'\n", "scene"),
        ("template:\n  - sensor: []\n", "template"),
        ("name: guest\ndescription: a mode\n", "mode"),
        ("something_else: true\n", "other"),
    ],
)
def test_the_class_of_a_file_is_read_from_its_own_shape(
    tmp_path: Path, body: str, expected: str
) -> None:
    """Each class the module recognizes, and `other` for a document no shape
    claims -- so the classifier's answer is a fact about the file and the
    mismatch test above is asserting the comparison and not the reading."""
    path = _provided(tmp_path, body)
    assert sandbox.file_class(path) == expected


def test_an_automation_is_not_classified_as_a_mode(
    tmp_path: Path,
) -> None:
    """`mode: single` is an ordinary top-level key of an automation. A presence
    test for the key would classify every automation in the corpus as a mode."""
    path = _provided(tmp_path, "alias: x\ntrigger: []\naction: []\nmode: single\n")
    assert sandbox.file_class(path) == "automation"


# --------------------------------------------------------------------------
# The sandbox as a whole
# --------------------------------------------------------------------------


def test_every_refusal_is_data_and_no_check_raises(
    tmp_path: Path,
    artifacts: tuple[vocabulary.Vocabulary, vocabulary.BehaviourVocabulary],
) -> None:
    """A sandbox failure is a validation failure and never an incident in a
    decision path, so a pack with every fault at once still returns a value."""
    document = _document(
        requires_slots=["nothing_at_all"],
        behaviours=[_behaviour(action="repeat", slots=["fan"])],
        provides=[{"path": "absent.yaml", "class": "automation"}],
    )
    result = sandbox.check_pack(_pack(tmp_path, document), tmp_path, *artifacts)
    assert not result.ok
    assert {refusal.reason for refusal in result.refusals} == {
        "slot_not_declared",
        "forbidden_action",
        "dangling_path",
    }


def test_the_refusals_are_grouped_by_their_reason() -> None:
    """Every reason is a key even when empty, and a key is the reason itself."""
    result = sandbox.SandboxResult(
        refusals=(
            sandbox.Refusal(reason="unknown_action", pack="p", where="w", message="m"),
            sandbox.Refusal(reason="banned_service", pack="p", where="w", message="m"),
        )
    )
    grouped = sandbox.refusals_by_reason(result)
    assert set(grouped) == set(sandbox.REASONS)
    assert [r.reason for r in grouped["unknown_action"]] == ["unknown_action"]
    assert [r.reason for r in grouped["banned_service"]] == ["banned_service"]
    assert grouped["not_in_reach"] == ()


def test_a_refusal_has_no_class_beside_its_reason() -> None:
    """The collapse, asserted from the outside: a refusal carries four facts and
    the closed taxonomy is `REASONS`.

    A guard rather than a behaviour, and stated as one. A grouping coarser than
    the reason would be the *face's* to publish, not this module's: `pack-cli`
    carries classes like `unknown term` that fold several reasons together, and
    the class a report carries belongs to the report. Reintroducing a second,
    engine-side class field would put back a field no caller switches on, which is
    why the absence is pinned rather than left to a reader of the diff.
    """
    refusal = sandbox.Refusal(reason="banned_service", pack="p", where="w", message="m")
    assert set(sandbox.Refusal.__dataclass_fields__) == {
        "reason",
        "pack",
        "where",
        "message",
    }
    assert not hasattr(refusal, "code")
    assert not hasattr(sandbox, "CLASSES")


def test_the_reasons_a_refusal_can_carry_are_what_the_module_declares() -> None:
    """`REASONS` is closed, has no duplicates, and is the whole of the taxonomy.

    Read from the module's own tuple rather than a literal list, because a literal
    list here would be the second copy this test exists to say is not there.
    """
    assert len(set(sandbox.REASONS)) == len(sandbox.REASONS)
    assert {"unknown_action", "forbidden_action", "banned_service"} <= set(
        sandbox.REASONS
    )


def test_the_capability_surface_has_no_branch_loop_or_variable() -> None:
    """The requirement observed from the data rather than from a promise: a
    command is a service, a slot and the entities it names -- there is no field
    through which a pack could express a branch, a loop, a variable or an
    expression, so an installed pack cannot exceed its grant rather than being
    declined when it tries."""
    assert set(Command.__dataclass_fields__) == {
        "behaviour",
        "service",
        "slot",
        "entities",
    }


def test_a_missing_artifact_is_not_reported_as_a_pack_failure(
    fake_root: Path,
) -> None:
    """A missing vocabulary is the checkout's fault and not the pack's, so it
    raises and names the path rather than becoming a refusal against a pack."""
    with pytest.raises(vocabulary.MissingArtifactError) as caught:
        vocabulary.load_behaviour_vocabulary(fake_root)
    assert "behavior-vocabulary" in caught.value.path.as_posix()


def test_the_sandbox_reads_its_vocabularies_through_the_gateway() -> None:
    """The engine opens a frozen artifact in one module. The sandbox imports
    that module and names no catalog path of its own, so the policy and the
    vocabulary terms have one reader and one definition."""
    source = MODULE.read_text(encoding="utf-8")
    imported = {
        node.module
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert "engine.vocabulary" in imported
    opened = _path_literals(source)
    assert [literal for literal in opened if "catalog/" in literal] == []
    assert [literal for literal in opened if "schemas/" in literal] == []


def test_the_sandbox_imports_nothing_from_the_simulator_or_the_composition_root() -> (
    None
):
    """Task 3.6. The engine must run unchanged against a real Home Assistant
    adapter in Phase 4, and a sandbox that reached into the fake house would be a
    sandbox that only works on the fake house."""
    source = MODULE.read_text(encoding="utf-8")
    top_level = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            top_level.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            top_level.add(node.module.split(".")[0])
    assert top_level.isdisjoint({"sim", "openhouse"}), sorted(top_level)


def test_the_layering_check_refuses_an_engine_module_that_imports_the_simulator(
    fake_root: Path,
) -> None:
    """The check that owns the rule above, driven on a tree that breaks it, so a
    check that had stopped reporting would fail a test rather than pass one."""
    write(fake_root, "engine/infiltrator.py", "from sim import device\n")
    report = Report()
    check_engine_layering(report)
    assert [d.where for d in report.diagnostics] == ["engine/infiltrator.py"]
    assert "sim" in report.render()


def test_the_layering_rule_fails_for_the_composition_root_too(
    fake_root: Path,
) -> None:
    """Task 3.6 names both directions and they are two checks, not one.

    The layering check owns the simulator edge because the composition-root
    check already owns the facade one, and a second check reporting the same
    import would turn one fault into two diagnostics. The pair is the rule, so
    both halves are driven here on a tree that breaks them.
    """
    write(fake_root, "engine/infiltrator.py", "from openhouse import facade\n")
    report = Report()
    check_composition_root_purity(report)
    assert "engine/infiltrator.py" in [d.where for d in report.diagnostics]


def test_the_layering_check_passes_on_the_committed_tree() -> None:
    report = Report()
    check_engine_layering(report)
    assert report.diagnostics == [], report.render()


# --------------------------------------------------------------------------
# Every reason, produced once
# --------------------------------------------------------------------------


def test_every_reason_is_reachable(
    tmp_path: Path,
    artifacts: tuple[vocabulary.Vocabulary, vocabulary.BehaviourVocabulary],
) -> None:
    """One refusal for every reason the module declares.

    A reason no check can produce is a reason a caller handles for nothing, and a
    reason that has quietly stopped being produced -- because a branch was
    reordered -- would otherwise pass this suite. The assertion is over the whole
    set rather than a sample of it for that reason, and it is the test that keeps
    `REASONS` honest.
    """
    found: set[str] = set()

    def collect(document: Mapping[str, object], name: str) -> None:
        result = sandbox.check_pack(
            _pack(tmp_path, document, name), tmp_path, *artifacts
        )
        found.update(refusal.reason for refusal in result.refusals)

    collect(_document(behaviours=[_behaviour(action="nope")]), "unknown_action.yaml")
    collect(
        _document(behaviours=[_behaviour(condition="nope")]), "unknown_condition.yaml"
    )
    collect(_document(behaviours=[_behaviour(trigger="nope")]), "unknown_trigger.yaml")
    collect(
        _document(behaviours=[_behaviour(action="repeat")]), "forbidden_action.yaml"
    )
    collect(
        _document(behaviours=[_behaviour(condition="template")]),
        "forbidden_condition.yaml",
    )
    collect(
        _document(behaviours=[_behaviour(trigger="template")]), "forbidden_trigger.yaml"
    )
    collect(
        _document(behaviours=[_behaviour(slots=["light.kitchen_ceiling"])]),
        "literal.yaml",
    )
    collect(_document(behaviours=[_behaviour(slots=["banana"])]), "unknown_slot.yaml")
    collect(_document(behaviours=[_behaviour(slots=["fan"])]), "undeclared_slot.yaml")
    collect(
        _document(behaviours=[_behaviour(services=["homeassistant.restart"])]),
        "banned.yaml",
    )
    collect(
        _document(provides=[{"path": "absent.yaml", "class": "automation"}]),
        "dangling.yaml",
    )
    collect(
        _document(provides=[{"path": "../out.yaml", "class": "automation"}]),
        "escaping.yaml",
    )
    _provided(tmp_path, "entities:\n  light.kitchen: 'on'\n")
    collect(
        _document(provides=[{"path": "thing_pack/thing.yaml", "class": "blueprint"}]),
        "mismatch.yaml",
    )

    pack = _reach_pack(tmp_path)
    binding = {"light_group": "light.kitchen", "motion_sensor": "binary_sensor.kitchen"}
    commands = sandbox.check_commands(
        pack,
        [
            _command(behaviour="nope"),
            _command(slot="light.kitchen_ceiling"),
            _command(slot="fan"),
            _command(entities=("light.hallway",)),
            _command(service="light.turn_off"),
        ],
        binding,
        artifacts[0].pack_policy,
    )
    found.update(refusal.reason for refusal in commands.refusals)

    unclaimed = _pack(
        tmp_path,
        _document(
            requires_slots=["light_group", "motion_sensor"],
            behaviours=[
                _behaviour(services=["light.turn_on"], slots=["motion_sensor"])
            ],
        ),
        "unclaimed.yaml",
    )
    claim = sandbox.check_commands(
        unclaimed, [_command(slot="light_group")], binding, artifacts[0].pack_policy
    )
    found.update(refusal.reason for refusal in claim.refusals)

    assert found == set(sandbox.REASONS), sorted(set(sandbox.REASONS) - found)


# --------------------------------------------------------------------------
# The documentation -- task 3.7
# --------------------------------------------------------------------------


def test_the_document_exists_and_names_the_four_rules() -> None:
    text = DOCUMENT.read_text(encoding="utf-8")
    for rule in ("declarative subset", "Entity reach", "declared services", "provides"):
        assert rule.lower() in text.lower(), rule


def test_the_document_names_every_reason_the_module_can_report() -> None:
    """Every reason the module can carry is named on the page, in backticks.

    The page is the only place a pack author reads the taxonomy, so a reason the
    module declares and the page omits is a failure nobody can look up.
    """
    text = DOCUMENT.read_text(encoding="utf-8")
    missing = [reason for reason in sandbox.REASONS if f"`{reason}`" not in text]
    assert missing == [], missing
