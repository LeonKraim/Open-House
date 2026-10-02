"""A behaviour is a policy unit bound to a corpus row, and these are the bindings.

`first-behaviours` makes the corpus the single definition of what a behaviour is:
a unit's `scope` and `required_slots` are its primary row's, its optional slots
are granted by the rows it cites, its `id` keys the enable flag and the log actor,
and its tunables reach the resolver rather than a module constant. Each of those
is a relation between `engine/behaviours/` and `catalog/behaviors.yaml`, so none
of them can be checked by reading one file and all of them are checked here.

Two of the checks are *scans* rather than assertions about declared facts, and
they are the two a declared fact cannot express. The adapter scan reads the units'
source for any call to a port operation, because "a behaviour SHALL NOT call the
adapter to change state" is a claim about code that does not exist rather than
about a value the unit carries -- and `BehaviourContext` cannot express it
either, since the protocol not exposing `actuate` is what makes the call
impossible rather than merely absent. The tunable scan reads the same source for
every config key a unit resolves and compares it to the defaults the unit
declares, so a unit that reads a tunable it does not declare, or declares one it
never reads, fails where it is written.

Every check except the scans runs over the *shipped* registry and the *committed*
corpus, which is the point: the tests could pass on a fixture and the units still
ship broken.

Each test says, in its docstring, what a falsifying implementation would look
like.
"""

from __future__ import annotations

import ast
import importlib
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
import yaml

from engine.adapter import PORT_OPERATIONS
from engine.behaviours import (
    Behaviour,
    BehaviourContext,
    BehaviourError,
    BehaviourScope,
    behaviour_defaults,
    default_behaviours,
)
from engine.binding import Reduction
from engine.config import BUILTIN_DEFAULTS
from engine.vocabulary import Vocabulary

if TYPE_CHECKING:
    from collections.abc import Mapping

ROOT = Path(__file__).resolve().parents[1]
_BEHAVIOURS_DIR = ROOT / "engine" / "behaviours"

#: The operations a behaviour may not reach. `snapshot` is the port's one
#: operation in both faces, and a behaviour reading the whole house's state
#: through it would be reading around the engine just as surely as one writing.
_FORBIDDEN_CALLS: tuple[str, ...] = tuple(sorted(PORT_OPERATIONS))

#: The functions a unit resolves a setting through. Named here rather than
#: matched by shape, because the scan's job is to find the keys a unit *reads*,
#: and a wider pattern would sweep in every call in the module.
_SETTING_READERS: frozenset[str] = frozenset(
    {"setting", "_number", "_seconds", "_flag"}
)


@pytest.fixture(scope="module")
def vocabulary() -> Vocabulary:
    """The committed catalog, frozen, so one load serves the module."""
    return Vocabulary.load(ROOT)


@pytest.fixture(scope="module")
def corpus() -> Mapping[str, Mapping[str, Any]]:
    """`catalog/behaviors.yaml`'s rows, keyed by concept id."""
    document = yaml.safe_load((ROOT / "catalog" / "behaviors.yaml").read_text("utf-8"))
    return {row["id"]: row for row in document["behaviors"]}


@pytest.fixture(scope="module")
def units() -> Mapping[str, Behaviour]:
    """The shipped registry."""
    return default_behaviours()


def _module_of(unit: Behaviour) -> object:
    """The module a unit is defined in, imported rather than guessed."""
    return importlib.import_module(type(unit).__module__)


def _source_of(unit: Behaviour) -> str:
    module = _module_of(unit)
    path = Path(module.__file__)  # type: ignore[arg-type]
    return path.read_text("utf-8")


def _keys_read(unit: Behaviour) -> set[str]:
    """Every config key the unit's module resolves through a setting reader.

    A key reaches a reader in one of two spellings -- a module-level constant
    resolved through the module's own namespace, or a string literal written at
    the call -- and both are collected, because a unit that read a literal key
    would otherwise read a tunable this check could not see.
    """
    module = _module_of(unit)
    keys: set[str] = set()
    for node in ast.walk(ast.parse(_source_of(unit))):
        if not isinstance(node, ast.Call):
            continue
        callee = node.func
        name = (
            callee.attr
            if isinstance(callee, ast.Attribute)
            else callee.id
            if isinstance(callee, ast.Name)
            else None
        )
        if name not in _SETTING_READERS or not node.args:
            continue
        argument = node.args[-1]
        if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
            keys.add(argument.value)
        elif isinstance(argument, ast.Name) and isinstance(
            resolved := getattr(module, argument.id, None), str
        ):
            keys.add(resolved)
    return keys


def _call_name(node: ast.Call) -> str | None:
    """The name a call is made through, whether `f(...)` or `thing.f(...)`."""
    callee = node.func
    if isinstance(callee, ast.Attribute):
        return callee.attr
    if isinstance(callee, ast.Name):
        return callee.id
    return None


def _port_operations_bound(tree: ast.Module) -> dict[str, str]:
    """The local name each import binds a port operation to, aliases included.

    An import is the only place an alias can be read. `from engine.adapter import
    actuate as poke` binds the operation to a name that appears in no operation
    list, and the call through it is spelled exactly like any other call -- so a
    scan that reads call names and nothing else cannot see it.

    The import is matched by the name it brings in rather than by the module it
    comes from, because a re-export reaches the adapter just as surely; a unit
    importing any name the port owns is the thing to look at.
    """
    bound: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        for imported in node.names:
            if imported.name in _FORBIDDEN_CALLS:
                bound[imported.asname or imported.name] = imported.name
    return bound


def _names_a_reduction(node: ast.expr, module: object) -> bool:
    """Whether `node` is a `Reduction` member, however it is spelled."""
    if isinstance(node, ast.Attribute):
        return node.attr in {member.name for member in Reduction}
    if isinstance(node, ast.Name):
        return isinstance(getattr(module, node.id, None), Reduction)
    return False


# --------------------------------------------------------------------------
# The registry
# --------------------------------------------------------------------------


def test_the_registry_holds_exactly_the_shipped_units(
    units: Mapping[str, Behaviour],
) -> None:
    """Three units ship, and their ids are the ones every record's `actor` names.

    A falsifying implementation that shipped a fourth unit would give a house a
    behaviour no phase has reviewed; one that shipped two would leave a corpus
    concept this phase claims to implement unimplemented.
    """
    assert sorted(units) == [
        "away_shutdown",
        "motion_lighting",
        "override",
        "safety_alert",
    ]


def test_the_registry_is_ordered_by_id(units: Mapping[str, Behaviour]) -> None:
    """The mapping's own order is ascending id, which is the evaluation order.

    A falsifying implementation that returned a set -- or a mapping in the order
    the unit classes happen to be written -- would make the decision log's
    sequence depend on an edit to this file rather than on a rule, and the
    arbitration tie-break would name an order the engine did not walk.
    """
    assert list(units) == sorted(units)


def test_every_unit_satisfies_the_behaviour_protocol(
    units: Mapping[str, Behaviour],
) -> None:
    """Each unit carries the seven declared facts and the one method.

    A falsifying implementation that forgot `defaults` would raise an
    `AttributeError` inside the engine's constructor, three frames from the unit
    that caused it; the protocol is `runtime_checkable` so the failure lands
    here.
    """
    for unit in units.values():
        assert isinstance(unit, Behaviour)


def test_every_unit_id_is_distinct_from_every_corpus_concept_id(
    units: Mapping[str, Behaviour], corpus: Mapping[str, Mapping[str, Any]]
) -> None:
    """The unit id says who decided and the concept id says what was matched.

    A falsifying implementation that named a unit after its primary row would
    make a record's `actor` and `rule` the same string, so a scenario could no
    longer tell which unit acted under which concept -- and a second unit
    implementing the same concept would collide.
    """
    for unit in units.values():
        assert unit.id not in corpus
        assert unit.id not in unit.corpus_rows


# --------------------------------------------------------------------------
# The declared facts against the corpus
# --------------------------------------------------------------------------


def test_a_units_scope_and_required_slots_are_its_primary_rows(
    units: Mapping[str, Behaviour], corpus: Mapping[str, Mapping[str, Any]]
) -> None:
    """The first cited row's `scope` and `required_slots` are the unit's, exactly.

    A falsifying implementation that declared a slot its row does not grant would
    read a device the corpus never said the behaviour needs; one that omitted a
    slot the row requires would run on a house that has not bound a device the
    behaviour cannot work without. Both directions are the same mistake -- a
    second definition of a concept the corpus defines once -- so the assertion is
    an equality on the whole list, in order.
    """
    for unit in units.values():
        primary = corpus[unit.corpus_rows[0]]
        assert unit.scope.value == primary["scope"], unit.id
        assert list(unit.required_slots) == list(primary["required_slots"]), unit.id


def test_every_cited_row_exists_in_the_corpus(
    units: Mapping[str, Behaviour], corpus: Mapping[str, Mapping[str, Any]]
) -> None:
    """Every row a unit cites is a row the corpus has, primary row included.

    A falsifying implementation that cited a row the corpus does not carry would
    be a unit claiming a concept nobody defined, and nothing else in the suite
    would look the string up.
    """
    for unit in units.values():
        assert unit.corpus_rows, unit.id
        for row in unit.corpus_rows:
            assert row in corpus, f"{unit.id} cites {row!r}"


def test_every_optional_slot_is_granted_by_a_cited_row(
    units: Mapping[str, Behaviour], corpus: Mapping[str, Mapping[str, Any]]
) -> None:
    """An optional slot comes from a row the unit cites, required or optional there.

    A falsifying implementation that invented an optional slot would read a
    device no corpus concept mentions, and because an optional slot degrades the
    behaviour rather than skipping it, the unit would silently take one branch on
    a house that binds the slot and another on a house that does not -- with
    nothing in the record to say the branch was never authorised.

    The first cited row's own `optional_slots` counts, as does any later row's,
    and a slot a later row *requires* is granted too: the citation is what makes
    it the unit's.
    """
    for unit in units.values():
        granted = {
            slot
            for row in unit.corpus_rows
            for slot in (*corpus[row]["required_slots"], *corpus[row]["optional_slots"])
        }
        for slot in unit.optional_slots:
            assert slot in granted, (
                f"{unit.id} reads {slot!r}, which no cited row grants"
            )


def test_a_units_primary_row_declares_no_slot_the_unit_does_not_carry(
    units: Mapping[str, Behaviour], corpus: Mapping[str, Mapping[str, Any]]
) -> None:
    """The primary row's optional slots are the unit's, so none is silently ignored.

    A falsifying implementation that declared an optional slot on its primary row
    and then never read it would have a corpus concept whose behaviour differs
    from its documentation, and a scenario reading the corpus to decide what to
    bind would bind a slot that changes nothing.
    """
    for unit in units.values():
        primary = corpus[unit.corpus_rows[0]]
        declared = set(unit.required_slots) | set(unit.optional_slots)
        assert set(primary["optional_slots"]) <= declared, unit.id


def test_every_slot_a_unit_names_is_one_the_vocabulary_defines(
    units: Mapping[str, Behaviour], vocabulary: Vocabulary
) -> None:
    """Scope and slots are checked against `catalog/slots.yaml`, not only the corpus.

    A falsifying implementation that read a slot the vocabulary does not define
    would fail at `resolve_slot` on the first tick of a real house, naming the
    house rather than the unit -- and the engine would have no way to record a
    skip for a slot it cannot name.
    """
    for unit in units.values():
        for slot in (*unit.required_slots, *unit.optional_slots):
            assert slot in vocabulary.slots, f"{unit.id} names {slot!r}"


# --------------------------------------------------------------------------
# The declared facts about the mechanism
# --------------------------------------------------------------------------


def test_every_unit_ships_off(units: Mapping[str, Behaviour]) -> None:
    """The product rule: a fresh house runs nothing until its owner turns it on.

    A falsifying implementation that shipped a unit enabled would run a behaviour
    on a house whose owner never opted into it, and the enable flag's whole
    purpose -- "everything off and toggleable" -- would depend on every house
    remembering to disable it.
    """
    for unit in units.values():
        assert unit.enabled is False, unit.id


def test_the_enable_flag_is_declared_once_per_unit_in_the_defaults(
    units: Mapping[str, Behaviour],
) -> None:
    """`behaviour_defaults` carries each unit's `enabled` and `priority`.

    A falsifying implementation that left the flag out would make `resolve_or`
    fall back to `unit.enabled` in one place and to a missing key in another, and
    the two would agree only by luck.
    """
    defaults = behaviour_defaults(units.values())
    for unit in units.values():
        assert defaults[f"behaviour.{unit.id}.enabled"] is unit.enabled
        assert defaults[f"behaviour.{unit.id}.priority"] == unit.priority


def test_two_units_sharing_a_setting_key_are_refused(
    units: Mapping[str, Behaviour],
) -> None:
    """A key two units both declare fails where the second is added.

    A falsifying implementation that let the later value win would make one
    unit's tunable depend on the order the registry was built in, and a house
    setting it would tune a behaviour it did not name.
    """
    colliding: Behaviour = _TwinBehaviour()
    with pytest.raises(BehaviourError):
        behaviour_defaults([*units.values(), colliding])


def test_no_unit_belongs_to_a_module_in_this_phase(
    units: Mapping[str, Behaviour],
) -> None:
    """Every unit's `module` is `None`, because modules are Phase 2.

    A falsifying implementation that declared a module would gate the unit on a
    module flag no layer sets, so the unit would be skipped for a reason no
    configuration could undo -- and the record would name the module rather than
    the flag's absence.
    """
    for unit in units.values():
        assert unit.module is None, unit.id


def test_the_shutdown_outranks_motion_lighting_on_a_shared_light(
    units: Mapping[str, Behaviour],
) -> None:
    """Away shutdown's priority is above motion lighting's, and the override is below both.

    A falsifying implementation that ranked motion lighting above the shutdown
    would light a room the house is trying to shut down, and the decision log
    would show an `acted` motion record and a `lost arbitration` shutdown -- a
    house that appears not to be away.
    """
    assert units["away_shutdown"].priority > units["motion_lighting"].priority
    assert units["override"].priority < units["motion_lighting"].priority


# --------------------------------------------------------------------------
# The scans
# --------------------------------------------------------------------------


def test_no_behaviour_module_calls_a_port_operation(
    units: Mapping[str, Behaviour],
) -> None:
    """No unit's source calls an operation of the `HouseAdapter` port, two ways.

    A falsifying implementation that actuated directly -- `ctx` carries no
    adapter, but a unit could import one -- would write to the house outside the
    arbitration, override, rate-limit and safety stages, and the write would be
    invisible in the decision log: the failure `first-behaviours`' rule exists to
    make unwritable rather than merely detectable.

    The scan is run twice because it has two blind spots and they are different.
    A textual scan sees the operation named anywhere in the module -- including in
    a comment or a docstring -- which the syntax tree does not. The syntax tree
    sees a *call* through a bare imported name, which the textual scan does not:
    `from engine.adapter import actuate; actuate(...)` contains no `".actuate("`.
    A call through a *renamed* import is invisible to both -- `poke(...)` is not
    an `".actuate("` substring and `poke` is not an operation -- so the scan also
    reads the imports, which are the only place that says what such a name is.
    """
    for unit in units.values():
        source = _source_of(unit)
        tree = ast.parse(source)
        bound = _port_operations_bound(tree)
        for operation in _FORBIDDEN_CALLS:
            assert f".{operation}(" not in source, f"{unit.id} calls {operation}"
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = _call_name(node)
            assert name not in _FORBIDDEN_CALLS, f"{unit.id} calls {name}"
            assert name not in bound, f"{unit.id} calls {bound.get(name)} as {name}"


def test_a_port_operation_imported_under_another_name_is_caught() -> None:
    """An alias is caught by the import, because nothing else names the operation.

    `from engine.adapter import actuate as poke; poke(...)` is invisible to the
    textual and the call-name passes taken alone: there is no `".actuate("` to
    match, and `poke` is no more an operation than any other name a unit might
    choose. Only the import says what `poke` is, which is why the scan reads it.

    A falsifying implementation that dropped the import pass would leave a unit
    one rename away from reaching the house directly -- and the rename is the
    obvious way to make the other two passes agree that nothing happened.
    """
    source = (
        "from engine.adapter import actuate as poke\n"
        "\n"
        "\n"
        "def go():\n"
        "    poke('light.kitchen')\n"
    )
    tree = ast.parse(source)
    bound = _port_operations_bound(tree)
    assert bound == {"poke": "actuate"}
    assert ".actuate(" not in source
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
    assert calls
    assert all(_call_name(node) in bound for node in calls)


def test_every_read_a_unit_makes_names_a_reduction(
    units: Mapping[str, Behaviour],
) -> None:
    """Every `read` a unit makes passes a `Reduction` member explicitly.

    `first-behaviours` requires a behaviour reading a multi-entity slot to *name* a
    reduction rather than infer one, because ANY and ALL decide differently on the
    same two entities: a room whose light group holds one lamp on and one off is
    "lit" under ANY and "not lit" under ALL. A unit that let the choice be implied
    would make the difference between those two readings unreadable from the unit,
    which is exactly the fact a decision record exists to keep.

    The parameter has no default, so no unit *can* omit it -- which is why this
    scan is written as a check on the argument's presence and not on its value:
    it pins that no unit works around the signature, with a `**kwargs` unpack or a
    helper that supplies one on the unit's behalf.
    """
    for unit in units.values():
        module = _module_of(unit)
        for node in ast.walk(ast.parse(_source_of(unit))):
            if not isinstance(node, ast.Call) or _call_name(node) != "read":
                continue
            arguments = [*node.args, *(keyword.value for keyword in node.keywords)]
            assert any(_names_a_reduction(item, module) for item in arguments), (
                f"{unit.id} reads a slot without naming a reduction"
            )


def test_the_context_protocol_exposes_no_port_operation() -> None:
    """`BehaviourContext` names no operation a house can be changed through.

    The scan above catches a unit that imports an adapter; this catches the other
    door -- a context that grew an `actuate` passthrough, which would make every
    unit's guard hold only as long as nobody used it.
    """
    exposed = {name for name in dir(BehaviourContext) if not name.startswith("_")}
    assert exposed & PORT_OPERATIONS == set()
    assert "adapter" not in exposed
    assert "house_adapter" not in exposed


def test_every_tunable_a_unit_reads_is_one_it_declares(
    units: Mapping[str, Behaviour],
) -> None:
    """The keys a unit resolves are exactly the keys its `defaults` carries.

    A falsifying implementation that read a key it did not declare would resolve
    it through some other layer or not at all, so the unit's own default would be
    a number nothing reaches -- the drift the layered resolver exists to remove.
    The equality is two-directional on purpose: a declared key the unit never
    reads is a knob a house can set that changes nothing, which is the same
    mistake seen from the other side.
    """
    for unit in units.values():
        assert _keys_read(unit) == set(unit.defaults), unit.id


def test_every_unit_tunable_is_scoped_to_the_unit_that_declares_it(
    units: Mapping[str, Behaviour],
) -> None:
    """A unit's keys are prefixed with its own id, and none collides with the engine's.

    A falsifying implementation that declared `engine.rate_limit.bound` under a
    behaviour's defaults would silently change the engine's own backstop, and
    because every layer is one flat namespace the collision would be invisible.
    """
    for unit in units.values():
        for key in unit.defaults:
            assert key.startswith(f"behaviour.{unit.id}."), key
            assert key not in BUILTIN_DEFAULTS, key


#: A second unit that declares the first unit's keys, so the collision check has
#: something to refuse. Defined here rather than in `conftest.py` because it is a
#: violating subject for one test and nothing else reads it.
class _TwinBehaviour:
    """A unit that re-declares `motion_lighting`'s enable flag and tunables."""

    id = "twin"
    corpus_rows = ("lighting.motion_light_on",)
    scope = BehaviourScope.ROOM
    required_slots: tuple[str, ...] = ()
    optional_slots: tuple[str, ...] = ()
    priority = 0
    module = None
    enabled = False
    defaults: Mapping[str, object] = {
        "behaviour.motion_lighting.quiet_timeout_seconds": 1.0
    }

    def evaluate(self, ctx: BehaviourContext) -> None:
        """Do nothing; the collision is with the defaults, not the evaluation."""
