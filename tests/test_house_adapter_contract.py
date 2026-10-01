"""The `HouseAdapter` contract suite -- tasks 2.1 and 2.2.

This file is a *pure function of the port*. Every assertion about a house is
made through members of `HouseAdapter` and the types `engine/adapter.py`
defines, so the suite can be pointed at the simulator's fake this phase and at
the Home Assistant adapter in Phase 4 without a line changing. That is not
tidiness for its own sake: a suite with a fake-specific line is a suite that
must be edited -- and so weakened -- exactly when it is first asked to hold a
second implementation to the same contract, which is the moment it most needs
to be strong.

Two kinds of check live here, and they fail differently:

- **Against a subject.** The parametrized tests take an implementation from
  `tests/adapter_subjects.py` and drive it through the port. They are the half
  that Phase 4 reuses; the registry is where implementations are named, so the
  suite itself names none.
- **Against the port's shape and purity.** The un-parameterized tests read
  `engine/adapter.py` and assert the *contract* rather than an implementation of
  it: the closed operation set, the two faces, the absence of a Home Assistant
  type and of any policy concept, and that the snapshot carries adapter state
  and no engine state.

The last of these is a guard on the suite itself
(`test_the_suite_references_only_port_members`): it parses this file and fails
if any access on the subject is not a member of `HouseAdapter`, so a
fake-specific line added here later is caught by the suite's own check rather
than by a reviewer's eye. It asserts it found at least one access, because a
guard over an empty set of accesses would pass for the wrong reason.

Nothing here is run before the phase's gate approves it.
"""

from __future__ import annotations

import ast
import sys
from typing import TYPE_CHECKING

import pytest

from engine.adapter import (
    CONTROL_FACING_OPERATIONS,
    ENGINE_FACING_OPERATIONS,
    PORT_OPERATIONS,
    AdapterSnapshot,
    ChangeContext,
    ChangeOrigin,
    EntitySnapshot,
    EntityView,
    Fault,
    HouseAdapter,
    InvalidEntityIdError,
    UnknownEntityError,
)
from tools.catalog import paths

from .adapter_subjects import FAKE_HOUSE_ADAPTER, IMPLEMENTATIONS

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from pathlib import Path

_ROOT = paths.ROOT
_PORT_PATH = _ROOT / "engine" / "adapter.py"
_SELF_PATH = _ROOT / "tests" / "test_house_adapter_contract.py"

#: The nine operations the port declares, as the spec enumerates them. Written
#: literally rather than read from the module, so the module's own constant is
#: checked against the spec instead of against itself.
_DECLARED_OPERATIONS = frozenset(
    {
        "read_entity",
        "list_entities",
        "actuate",
        "snapshot",
        "add_entity",
        "remove_entity",
        "set_availability",
        "inject_fault",
        "restart",
    }
)

#: The two faces, written literally for the same reason. `snapshot` is the one
#: operation in both, since the engine reads it during a tick and the control
#: surface calls it to cut a run in half.
_ENGINE_FACING = frozenset({"read_entity", "list_entities", "actuate", "snapshot"})
_CONTROL_FACING = frozenset(
    {
        "add_entity",
        "remove_entity",
        "set_availability",
        "inject_fault",
        "restart",
        "snapshot",
    }
)
_FACES_SHARED = frozenset({"snapshot"})

#: Concepts that belong to the engine and not to the house. A port operation that
#: mentions one has let a behaviour reach around the engine, so none may appear
#: in a signature.
_POLICY_WORDS = (
    "slot",
    "behaviour",
    "behavior",
    "binding",
    "mode",
    "enable",
    "pack",
    "priority",
    "arbitration",
    "override",
    "rate",
    "safety",
)

#: Fields a snapshot may not carry: every one belongs to the engine or the
#: simulator, not to the adapter (`house-adapter`: the port snapshots its own
#: state and nothing else).
_FOREIGN_SNAPSHOT_FIELDS = (
    "binding",
    "mode",
    "enable",
    "override",
    "rate",
    "clock",
    "random",
    "seed",
    "decision",
    "log",
    "window",
)

#: Ids that are outside `^[a-z_]+\.[a-z0-9_]+$`, one per way it can be wrong:
#: an uppercase domain, no dot at all, an empty object id, an empty domain, and
#: a hyphen where the shape has none.
_MALFORMED_IDS = (
    "Light.kitchen",
    "kitchen",
    "light.",
    ".kitchen",
    "light-kitchen",
)

_SUBJECT_NAMES = sorted(IMPLEMENTATIONS)


# --------------------------------------------------------------------------
# Helpers that read source. Kept as functions so the tests that use them read
# as the claim they make rather than as string matching.
# --------------------------------------------------------------------------


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _imported_roots(path: Path) -> set[str]:
    """Top-level module names imported anywhere in a file.

    Both `import a.b` and `from a.b import c` yield `a`, because the root is the
    package whose import the purity rule is about.
    """
    roots: set[str] = set()
    for node in ast.walk(_parse(path)):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    return roots


def _attribute_names(path: Path, receiver: str | None = None) -> set[str]:
    """Attribute names used in a file, optionally only on a named receiver.

    With `receiver` given, only `x.attr` where `x` is that bare name is
    collected -- which is how the suite checks its own use of the subject
    without matching an unrelated `foo.snapshot`.
    """
    names: set[str] = set()
    for node in ast.walk(_parse(path)):
        if not isinstance(node, ast.Attribute):
            continue
        if receiver is None or (
            isinstance(node.value, ast.Name) and node.value.id == receiver
        ):
            names.add(node.attr)
    return names


def _signature_texts(path: Path, class_name: str) -> list[tuple[str, str]]:
    """Every method of `class_name` as (name, "arg names and annotations").

    Docstrings are excluded deliberately: the port's prose explains what it does
    *not* carry, and a scan that read prose would fail on the sentence "carries
    no notion of a slot".
    """
    module = _parse(path)
    cls = next(
        (
            node
            for node in module.body
            if isinstance(node, ast.ClassDef) and node.name == class_name
        ),
        None,
    )
    assert cls is not None, f"{class_name} is not defined in {path.name}"

    signatures: list[tuple[str, str]] = []
    for node in cls.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        args = [*node.args.args, *node.args.kwonlyargs]
        parts = [arg.arg for arg in args]
        parts += [ast.unparse(arg.annotation) for arg in args if arg.annotation]
        if node.returns is not None:
            parts.append(ast.unparse(node.returns))
        signatures.append((node.name, " ".join(parts)))
    return signatures


def _port_member_names() -> set[str]:
    """The methods `HouseAdapter` declares, read from the port's source.

    Read from the AST rather than from `__protocol_attrs__` for two reasons: the
    latter is a `typing` internal a type checker does not know, and the port's
    shape *is* its class body -- the same source the other checks in this file
    read -- so a method added to the class is what the assertion should see.
    """
    module = _parse(_PORT_PATH)
    cls = next(
        (
            node
            for node in module.body
            if isinstance(node, ast.ClassDef) and node.name == "HouseAdapter"
        ),
        None,
    )
    assert cls is not None, "HouseAdapter is not defined in engine/adapter.py"
    return {
        node.name
        for node in cls.body
        if isinstance(node, ast.FunctionDef) and not node.name.startswith("__")
    }


def _missing_subjects(registry: Mapping[str, object]) -> list[str]:
    """Names the registry is missing, empty when it is complete.

    A function rather than an inline assertion so the guard's own behaviour on
    an empty registry is testable: a guard nothing exercises is a guard that
    might itself be vacuous.
    """
    missing: list[str] = []
    if not registry:
        missing.append(
            "the registry is empty; no implementation is held to the contract"
        )
    elif FAKE_HOUSE_ADAPTER not in registry:
        missing.append(f"{FAKE_HOUSE_ADAPTER} is not registered")
    return missing


# --------------------------------------------------------------------------
# The subject, parametrized over the registry. Every test that takes `subject`
# runs once per registered implementation.
# --------------------------------------------------------------------------


@pytest.fixture(params=_SUBJECT_NAMES)
def subject(request: pytest.FixtureRequest) -> HouseAdapter:
    """A freshly built adapter, driven only through the port."""
    return IMPLEMENTATIONS[request.param]()


# --------------------------------------------------------------------------
# The port's shape and purity -- the contract, not an implementation of it.
# --------------------------------------------------------------------------


def test_the_port_declares_exactly_the_closed_operation_set() -> None:
    """Nine operations and no tenth.

    A port that grew a `set_mode` or a `bind_slot` would compile and pass every
    subject test while giving a behaviour a way around the engine; only an
    exact-set assertion catches that. A port missing an operation fails the
    subject tests, but this one names the omission.
    """
    declared = _port_member_names()
    assert declared == _DECLARED_OPERATIONS
    assert set(PORT_OPERATIONS) == _DECLARED_OPERATIONS


def test_the_two_faces_partition_the_port() -> None:
    """The engine-facing and control-facing sets cover the port exactly once.

    `snapshot` is the one operation in both faces -- the engine reads it during
    a tick and the control surface calls it to cut a run in half -- so the
    assertion is that the intersection is exactly `{snapshot}` and not empty.
    An operation absent from both sets would be one no boundary check guarded.
    """
    assert ENGINE_FACING_OPERATIONS | CONTROL_FACING_OPERATIONS == PORT_OPERATIONS
    assert ENGINE_FACING_OPERATIONS & CONTROL_FACING_OPERATIONS == _FACES_SHARED
    assert ENGINE_FACING_OPERATIONS == _ENGINE_FACING
    assert CONTROL_FACING_OPERATIONS == _CONTROL_FACING


def test_the_port_imports_no_implementation_and_no_home_assistant() -> None:
    """The port is the engine's only outward edge, and it points nowhere yet.

    `ha_adapter` names the second implementation's home and importing it would
    drag Home Assistant in the moment Phase 4 lands; `sim` and the composition
    root are the other implementations of the same port. The engine may import
    none of them, and neither may the port it defines.
    """
    forbidden = {"sim", "ha_adapter", "openhouse", "homeassistant"}
    assert not (_imported_roots(_PORT_PATH) & forbidden)


def test_the_port_imports_only_the_standard_library() -> None:
    """The port's own dependency list is empty, so it cannot be the thing that
    drags anything into the engine.

    Stated as a positive -- everything the port imports is stdlib -- rather than
    as a denylist, so a new third-party import is caught even if it is not Home
    Assistant, which is the case the declared-dependency rule exists for.
    """
    standard_library = set(sys.stdlib_module_names)
    roots = _imported_roots(_PORT_PATH) - {"__future__"}
    assert roots <= standard_library, sorted(roots - standard_library)


def test_no_engine_module_calls_a_control_facing_only_operation() -> None:
    """The control face is unreachable from the engine's own code.

    A behaviour may actuate a lamp; it may not add an entity, make a device
    unavailable, inject a fault or restart a house. `snapshot` is excluded
    because it is in both faces; the five operations that are control-face only
    are the ones no `engine/` module may name.
    """
    control_only = set(CONTROL_FACING_OPERATIONS) - set(ENGINE_FACING_OPERATIONS)
    offenders: dict[str, set[str]] = {}
    for path in sorted((_ROOT / "engine").glob("*.py")):
        if path.name == "adapter.py":
            continue
        used = _attribute_names(path) & control_only
        if used:
            offenders[path.name] = used
    assert offenders == {}


def test_no_engine_module_imports_an_implementation_of_the_port() -> None:
    """The engine imports the port and no implementation of it.

    The port is `engine.adapter`; `sim` implements it this phase and
    `ha_adapter` will in Phase 4, and the composition root wires both. The
    engine may import none of them -- if it did, Phase 4 would have to change
    the engine to swap the fake for the real adapter, which is the whole reason
    the port exists. The Phase 0 purity scan admits first-party packages, so
    this direction is not covered by it; it is the port's own boundary.
    """
    forbidden = {"sim", "ha_adapter", "openhouse"}
    offenders = {
        path.name: sorted(_imported_roots(path) & forbidden)
        for path in sorted((_ROOT / "engine").glob("*.py"))
        if _imported_roots(path) & forbidden
    }
    assert offenders == {}


def test_the_port_signatures_name_no_home_assistant_type() -> None:
    """No signature carries an HA name, so the port imports with HA absent.

    Scanned over argument names and annotations only; the docstring's prose
    names Home Assistant on purpose, to say the port is free of it.
    """
    for name, text in _signature_texts(_PORT_PATH, "HouseAdapter"):
        lowered = text.lower()
        assert "hass" not in lowered, f"{name} names a Home Assistant type"
        assert "homeassistant" not in lowered, f"{name} names a Home Assistant type"


def test_the_port_signatures_name_no_policy_concept() -> None:
    """A signature that mentions a slot, mode, enable flag or binding has leaked
    the engine's concepts into the house's contract."""
    for name, text in _signature_texts(_PORT_PATH, "HouseAdapter"):
        lowered = text.lower()
        for word in _POLICY_WORDS:
            assert word not in lowered, f"{name} names the engine concept {word!r}"


def test_the_snapshot_types_carry_only_adapter_state() -> None:
    """The snapshot's fields are the adapter's, never the engine's or the
    simulator's.

    Bindings, modes, enable flags, override records and rate-limit windows are
    `engine-core`'s to snapshot; the clock and the random stream are
    `simulation`'s. If one appeared here the port would claim state a second
    adapter could not honestly reproduce.
    """
    fields = set(EntitySnapshot.__dataclass_fields__) | set(
        AdapterSnapshot.__dataclass_fields__
    )
    for field_name in fields:
        for word in _FOREIGN_SNAPSHOT_FIELDS:
            assert word not in field_name.lower(), f"snapshot field {field_name!r}"


def test_the_suite_references_only_port_members() -> None:
    """The suite's own guard: it touches the subject only through the port.

    If a later edit reaches for a fake-specific method -- `subject.make_user_action`
    or `subject.registry` -- the access is not a member of `HouseAdapter` and
    this check fails naming it. The non-emptiness assertion is the point: a
    guard that found no accesses would pass while checking nothing.
    """
    accessed = _attribute_names(_SELF_PATH, receiver="subject")
    assert accessed, "the suite never touches its subject; this guard is vacuous"
    extras = accessed - _port_member_names()
    assert not extras, (
        f"the suite uses non-port members of the subject: {sorted(extras)}"
    )


# --------------------------------------------------------------------------
# The registry guards. A green run over an empty parameter list is the failure
# these exist to rule out.
# --------------------------------------------------------------------------


def test_the_registry_holds_the_fake() -> None:
    """`FakeHouseAdapter` is registered, so the suite has a subject this phase."""
    assert _missing_subjects(IMPLEMENTATIONS) == [], _missing_subjects(IMPLEMENTATIONS)


def test_the_registry_guard_rejects_an_empty_registry() -> None:
    """The guard above is not vacuous: it fails on an empty registry."""
    assert _missing_subjects({}) != []
    assert _missing_subjects({"Other": object}) != []


# --------------------------------------------------------------------------
# The contract, exercised against a subject. Every name here is a member of
# `HouseAdapter`.
# --------------------------------------------------------------------------


def test_a_read_carries_domain_state_attributes_and_availability(
    subject: HouseAdapter,
) -> None:
    """One read yields the four facts, each addressable without a second call."""
    subject.add_entity(
        "light.kitchen",
        "on",
        attributes={"brightness": 200},
        context=ChangeContext.world(),
    )
    view = subject.read_entity("light.kitchen")
    assert isinstance(view, EntityView)
    assert view.entity_id == "light.kitchen"
    assert view.domain == "light"
    assert view.state == "on"
    assert dict(view.attributes) == {"brightness": 200}
    assert view.available is True


def test_an_entity_added_without_attributes_reads_an_empty_mapping(
    subject: HouseAdapter,
) -> None:
    """Attributes are a mapping, never `None`, so a caller can index it."""
    subject.add_entity("light.kitchen", "off", context=ChangeContext.world())
    assert dict(subject.read_entity("light.kitchen").attributes) == {}


def test_a_read_of_an_unknown_entity_fails_naming_it(subject: HouseAdapter) -> None:
    """No default state: a default is a silent "off" the engine cannot tell from
    a real one, so the read must fail rather than answer."""
    with pytest.raises(UnknownEntityError) as raised:
        subject.read_entity("light.ghost")
    assert raised.value.entity_id == "light.ghost"


@pytest.mark.parametrize("bad", _MALFORMED_IDS)
def test_a_read_rejects_an_id_outside_the_schema_shape(
    subject: HouseAdapter, bad: str
) -> None:
    """An id the house schema could never bind is refused by name, not read."""
    with pytest.raises(InvalidEntityIdError) as raised:
        subject.read_entity(bad)
    assert raised.value.entity_id == bad


def test_an_actuation_rejects_an_id_outside_the_schema_shape(
    subject: HouseAdapter,
) -> None:
    """The shape check guards the write path too, so a fixture cannot bind an
    entity the house schema could not name."""
    with pytest.raises(InvalidEntityIdError):
        subject.actuate("Light.kitchen", "on", context=ChangeContext.engine())


def test_a_write_without_a_context_is_rejected_by_construction(
    subject: HouseAdapter,
) -> None:
    """The requirement the whole override mechanism rests on.

    A write that could omit its context could default to `user`, and an
    engine-origin write presenting itself as manual would suppress the very
    behaviour that made it. The omission is a `TypeError` at the call, before
    the adapter is reached.
    """
    with pytest.raises(TypeError):
        subject.actuate("light.kitchen", "on")  # type: ignore[call-arg]


def _context_free_calls() -> list[tuple[str, Callable[[HouseAdapter], object]]]:
    return [
        ("actuate", lambda s: s.actuate("light.kitchen", "on")),  # type: ignore[call-arg]
        ("add_entity", lambda s: s.add_entity("light.kitchen", "on")),  # type: ignore[call-arg]
        ("remove_entity", lambda s: s.remove_entity("light.kitchen")),  # type: ignore[call-arg]
        (
            "set_availability",
            lambda s: s.set_availability("light.kitchen", available=False),  # type: ignore[call-arg]
        ),
        (
            "inject_fault",
            lambda s: s.inject_fault("light.kitchen", Fault(state="off")),  # type: ignore[call-arg]
        ),
        ("restart", lambda s: s.restart()),  # type: ignore[call-arg]
    ]


@pytest.mark.parametrize(
    ("operation", "call"),
    _context_free_calls(),
    ids=[name for name, _ in _context_free_calls()],
)
def test_every_mutating_operation_requires_a_context(
    subject: HouseAdapter, operation: str, call: Callable[[HouseAdapter], object]
) -> None:
    """No mutating operation, not one, has a default context.

    The parameter list is a second reading of the port's surface: if a new
    mutating operation is added and does not require a context, it will not
    appear here, and `test_the_port_declares_exactly_the_closed_operation_set`
    is what fails instead.
    """
    with pytest.raises(TypeError):
        call(subject)


def test_an_actuation_writes_state_that_reads_back(subject: HouseAdapter) -> None:
    subject.add_entity("light.kitchen", "off", context=ChangeContext.world())
    subject.actuate("light.kitchen", "on", context=ChangeContext.engine())
    assert subject.read_entity("light.kitchen").state == "on"


def test_the_four_origins_are_distinguishable(subject: HouseAdapter) -> None:
    """User, engine, world and fault are told apart by context alone -- the
    single field override, rate-limit and arbitration all read."""
    subject.add_entity("light.kitchen", "off", context=ChangeContext.world())
    assert subject.read_entity("light.kitchen").last_origin is ChangeOrigin.WORLD

    subject.actuate("light.kitchen", "on", context=ChangeContext.user())
    assert subject.read_entity("light.kitchen").last_origin is ChangeOrigin.USER

    subject.actuate("light.kitchen", "on", context=ChangeContext.engine())
    assert subject.read_entity("light.kitchen").last_origin is ChangeOrigin.ENGINE

    subject.actuate("light.kitchen", "on", context=ChangeContext.world())
    assert subject.read_entity("light.kitchen").last_origin is ChangeOrigin.WORLD

    subject.inject_fault(
        "light.kitchen", Fault(state="off"), context=ChangeContext.fault()
    )
    assert subject.read_entity("light.kitchen").last_origin is ChangeOrigin.FAULT


def test_an_injected_fault_is_recorded_as_a_fault(subject: HouseAdapter) -> None:
    """A fault is its own origin: the engine's recovery turns on telling it from
    the engine's own write and from a user's."""
    subject.add_entity("sensor.hall", "42", context=ChangeContext.world())
    subject.inject_fault(
        "sensor.hall", Fault(available=False), context=ChangeContext.fault()
    )
    view = subject.read_entity("sensor.hall")
    assert view.last_origin is ChangeOrigin.FAULT
    assert view.available is False
    assert view.state == "42"


def test_an_unavailable_entity_is_not_off(subject: HouseAdapter) -> None:
    """`available: false` and `state == "on"` coexist; the read distinguishes
    them, which is what lets a behaviour hold the last state rather than mistake
    silence for evidence."""
    subject.add_entity("light.kitchen", "on", context=ChangeContext.world())
    subject.set_availability(
        "light.kitchen", available=False, context=ChangeContext.world()
    )
    view = subject.read_entity("light.kitchen")
    assert view.available is False
    assert view.state == "on"
    assert view.state != "off"


def test_an_unavailable_entity_returns_without_an_actuation(
    subject: HouseAdapter,
) -> None:
    """Returning to available is not an actuation: the state is untouched and
    only availability moves."""
    subject.add_entity("light.kitchen", "on", context=ChangeContext.world())
    subject.set_availability(
        "light.kitchen", available=False, context=ChangeContext.world()
    )
    subject.set_availability(
        "light.kitchen", available=True, context=ChangeContext.world()
    )
    view = subject.read_entity("light.kitchen")
    assert view.available is True
    assert view.state == "on"


def test_an_actuation_does_not_change_availability(subject: HouseAdapter) -> None:
    """Availability is a separate field with a separate operation; a write to an
    unavailable entity leaves it unavailable."""
    subject.add_entity("light.kitchen", "on", context=ChangeContext.world())
    subject.set_availability(
        "light.kitchen", available=False, context=ChangeContext.world()
    )
    subject.actuate("light.kitchen", "off", context=ChangeContext.engine())
    view = subject.read_entity("light.kitchen")
    assert view.available is False
    assert view.state == "off"


def _malformed_id_calls() -> list[tuple[str, Callable[[HouseAdapter], object]]]:
    """One call per mutating operation, each with a malformed id."""
    bad = "Light.kitchen"
    return [
        ("actuate", lambda s: s.actuate(bad, "on", context=ChangeContext.engine())),
        (
            "add_entity",
            lambda s: s.add_entity(bad, "on", context=ChangeContext.world()),
        ),
        (
            "remove_entity",
            lambda s: s.remove_entity(bad, context=ChangeContext.world()),
        ),
        (
            "set_availability",
            lambda s: s.set_availability(
                bad, available=False, context=ChangeContext.world()
            ),
        ),
        (
            "inject_fault",
            lambda s: s.inject_fault(
                bad, Fault(state="on"), context=ChangeContext.fault()
            ),
        ),
    ]


@pytest.mark.parametrize(
    ("operation", "call"),
    _malformed_id_calls(),
    ids=[name for name, _ in _malformed_id_calls()],
)
def test_every_mutating_operation_rejects_a_malformed_id(
    subject: HouseAdapter, operation: str, call: Callable[[HouseAdapter], object]
) -> None:
    """The id shape guards every write, so a fixture cannot name an entity the
    house schema could never bind."""
    with pytest.raises(InvalidEntityIdError) as raised:
        call(subject)
    assert raised.value.entity_id == "Light.kitchen"


def test_an_added_entity_is_enumerable_and_absent_after_removal(
    subject: HouseAdapter,
) -> None:
    """Removal means absent -- not `off`. A removed entity is not listed and a
    read of it fails, because `off` is a state the engine would act on."""
    subject.add_entity("light.kitchen", "off", context=ChangeContext.world())
    assert "light.kitchen" in set(subject.list_entities())
    subject.remove_entity("light.kitchen", context=ChangeContext.world())
    assert "light.kitchen" not in set(subject.list_entities())
    with pytest.raises(UnknownEntityError):
        subject.read_entity("light.kitchen")


def test_only_a_user_origin_actuation_reads_back_as_a_user(
    subject: HouseAdapter,
) -> None:
    """Every operation but the user-action path leaves a non-user origin.

    The failure mode is a *false* `user`, so the assertion is negative: a fault,
    an availability change, a removal and a restart must none of them claim a
    person, while an explicit user-origin actuation must.
    """
    subject.add_entity("light.kitchen", "off", context=ChangeContext.world())
    subject.set_availability(
        "light.kitchen", available=False, context=ChangeContext.world()
    )
    assert subject.read_entity("light.kitchen").last_origin is not ChangeOrigin.USER
    subject.set_availability(
        "light.kitchen", available=True, context=ChangeContext.world()
    )
    assert subject.read_entity("light.kitchen").last_origin is not ChangeOrigin.USER

    subject.inject_fault(
        "light.kitchen", Fault(state="on"), context=ChangeContext.fault()
    )
    assert subject.read_entity("light.kitchen").last_origin is not ChangeOrigin.USER

    subject.restart(context=ChangeContext.world())
    for entity_id in subject.list_entities():
        assert subject.read_entity(entity_id).last_origin is not ChangeOrigin.USER

    subject.actuate("light.kitchen", "off", context=ChangeContext.user())
    assert subject.read_entity("light.kitchen").last_origin is ChangeOrigin.USER


def test_a_restart_is_reproducible(subject: HouseAdapter) -> None:
    """The same configuration restarted twice yields the same startup
    condition, so a scenario's seed reproduces the same post-restart decisions."""
    subject.add_entity("light.kitchen", "on", context=ChangeContext.world())
    subject.restart(context=ChangeContext.world())
    first = subject.snapshot()
    subject.restart(context=ChangeContext.world())
    assert subject.snapshot() == first


def test_the_snapshot_carries_the_adapters_entities_and_only_those(
    subject: HouseAdapter,
) -> None:
    """The snapshot holds the entities present, with their state, attributes and
    availability -- the port's own state and nothing the engine owns."""
    subject.add_entity(
        "light.kitchen",
        "on",
        attributes={"brightness": 3},
        context=ChangeContext.world(),
    )
    subject.set_availability(
        "light.kitchen", available=False, context=ChangeContext.world()
    )
    snapshot = subject.snapshot()
    assert isinstance(snapshot, AdapterSnapshot)
    assert all(isinstance(entry, EntitySnapshot) for entry in snapshot.entities)

    by_id = {entry.entity_id: entry for entry in snapshot.entities}
    assert set(by_id) == set(subject.list_entities())
    entry = by_id["light.kitchen"]
    assert entry.state == "on"
    assert dict(entry.attributes) == {"brightness": 3}
    assert entry.available is False


def test_an_unlock_reaches_the_house_from_any_origin_the_port_is_given(
    subject: HouseAdapter,
) -> None:
    """The safety veto is the engine's action gate, not the adapter's.

    An unlock the engine's gate admits -- a `user` one always, a non-user one
    never, because the gate refuses it first (`product-invariants`) -- must pass
    through the port unre-decided. An adapter that refused a non-user unlock
    would have let the behaviour *propose* it already, hiding the proposal from
    the decision log and losing the guarantee that a behaviour cannot propose an
    unlock at all. So the port applies what it is handed and re-decides nothing.
    """
    subject.add_entity("lock.front_door", "locked", context=ChangeContext.world())
    subject.actuate("lock.front_door", "unlocked", context=ChangeContext.engine())
    assert subject.read_entity("lock.front_door").state == "unlocked"

    subject.actuate("lock.front_door", "locked", context=ChangeContext.world())
    subject.actuate("lock.front_door", "unlocked", context=ChangeContext.user())
    assert subject.read_entity("lock.front_door").state == "unlocked"
