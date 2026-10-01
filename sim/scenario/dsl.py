"""Compiling a step into a control-surface call, and nothing else.

`scenario-runner` fixes the vocabulary here and only here: a `when` step names one
of the control surface's verbs -- the ten operations of the registry plus the four
house-control verbs -- and `compile_step` turns it into the call that verb names.
The module has **no verb of its own**. A bespoke DSL (`turn_on`, `wait`, `expect`)
was rejected for the reason `design.md` D10 gives: it would be a second vocabulary
to keep in step with the first, and the agent's loop runs through the control
surface, so a scenario written in another vocabulary would test a path the agent
never takes. `STEP_VERBS` is therefore a claim about the control surface, and the
conformance check compares it to the registry rather than trusting it.

A compiled step is a call **and** where its result goes. Two verbs produce a
document another verb consumes -- `snapshot` and `export_config` are the two a
`restore` and an `import_config` need -- so a step may name the document it
produced (`save`) and a consuming step names the one it wants (`{from: <name>}`).
That naming is the runner's bookkeeping and not a second parameter of the
operation: the call made is still exactly the registry's, with the registry's own
parameter names.

The surface is typed as `ScenarioSurface` (`.surface`) rather than as the facade
that implements it. `sim/` may not import the composition root (`design.md` D12),
so the shape a step calls is stated on this side and the facade satisfies it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from .model import Step
    from .surface import ScenarioSurface

__all__ = [
    "STEP_VERBS",
    "InvalidStepError",
    "StepCall",
    "UnknownDocumentError",
    "UnknownVerbError",
    "compile_step",
]


@dataclass(frozen=True, slots=True)
class StepCall:
    """One step, compiled: the call to make, and where its result is kept.

    `save` is `None` for the verbs whose result is not a document. A step that
    produces one without naming it is stored under `step_<index>` by the runner,
    so a `restore` can name a snapshot the scenario never labelled -- the index is
    the position in the `when` block, and the runner passes it.
    """

    verb: str
    save: str | None
    invoke: Callable[[ScenarioSurface, Mapping[str, Mapping[str, object]]], object]


class UnknownVerbError(Exception):
    """A step whose verb names no control-surface member."""

    def __init__(self, verb: str) -> None:
        self.verb = verb
        self.known: tuple[str, ...] = STEP_VERBS
        super().__init__(
            f"no step verb is named {verb!r}; the verbs are "
            f"{', '.join(repr(item) for item in STEP_VERBS)}"
        )


class InvalidStepError(Exception):
    """A step whose parameter is missing or is not what the verb takes.

    The loader's schema refuses these shapes long before a step is compiled, so
    this is the failure a hand-built `Step` meets: it names the verb, the
    parameter and what was found, because a compiler that failed with a bare
    `KeyError` would name neither of the first two.
    """

    def __init__(self, verb: str, parameter: str, reason: str) -> None:
        self.verb = verb
        self.parameter = parameter
        self.reason = reason
        super().__init__(f"the step {verb!r} takes {parameter!r}: {reason}")


class UnknownDocumentError(Exception):
    """A `{from: <name>}` naming a document no earlier step saved."""

    def __init__(self, name: str, held: tuple[str, ...]) -> None:
        self.name = name
        self.held = held
        super().__init__(
            f"no step saved a document named {name!r}; the ones saved are "
            f"{', '.join(repr(item) for item in held) or 'none'}"
        )


class _Parameters:
    """One step's parameters, read by name with the verb in every failure.

    The schema has already refused a wrong shape, so these readers exist for the
    two things a schema cannot do: convert a number that arrived as an integer
    into the type the call wants, and name the verb and the parameter when a
    hand-built `Step` is missing one.
    """

    def __init__(self, verb: str, parameters: Mapping[str, object]) -> None:
        self._verb = verb
        self._parameters = parameters

    def _required(self, name: str) -> object:
        if name not in self._parameters:
            raise InvalidStepError(self._verb, name, "it is required and is absent")
        return self._parameters[name]

    def text(self, name: str) -> str:
        """A required string parameter."""
        value = self._required(name)
        if not isinstance(value, str):
            raise InvalidStepError(self._verb, name, f"{value!r} is not a string")
        return value

    def optional_text(self, name: str) -> str | None:
        """An optional string parameter, or `None` when absent."""
        if name not in self._parameters:
            return None
        return self.text(name)

    def number(self, name: str) -> float:
        """A required number, accepting an integer for a `number` parameter."""
        value = self._required(name)
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise InvalidStepError(self._verb, name, f"{value!r} is not a number")
        return float(value)

    def count(self, name: str) -> int:
        """A required integer, refusing a boolean that is an integer in Python."""
        value = self._required(name)
        if isinstance(value, bool) or not isinstance(value, int):
            raise InvalidStepError(self._verb, name, f"{value!r} is not an integer")
        return value

    def optional_count(self, name: str) -> int | None:
        """An optional integer parameter, or `None` when absent."""
        if name not in self._parameters:
            return None
        return self.count(name)

    def flag(self, name: str) -> bool:
        """A required boolean parameter."""
        value = self._required(name)
        if not isinstance(value, bool):
            raise InvalidStepError(self._verb, name, f"{value!r} is not a boolean")
        return value

    def mapping(self, name: str) -> Mapping[str, object]:
        """A required object parameter."""
        value = self._required(name)
        if not isinstance(value, dict):
            raise InvalidStepError(self._verb, name, f"{value!r} is not a mapping")
        return cast("Mapping[str, object]", value)

    def optional_mapping(self, name: str) -> Mapping[str, object] | None:
        """An optional object parameter, or `None` when absent."""
        if name not in self._parameters:
            return None
        return self.mapping(name)

    def document_name(self, name: str) -> str | None:
        """The name a `{from: <name>}` document parameter references, or `None`.

        `None` means the document was written inline, which is the other branch
        and not a fault: the schema has already refused a document that is both,
        and a document that is neither is an inline one. A `from` whose value is
        not a non-empty string is a fault rather than an inline document, so it
        is refused here rather than passed on as a name that could not resolve.
        """
        document = self.mapping(name)
        if set(document) != {"from"}:
            return None
        reference = document["from"]
        if not isinstance(reference, str) or not reference:
            raise InvalidStepError(
                self._verb, "from", f"{reference!r} is not a document name"
            )
        return reference


# The builders, one per verb. Each is one line of closure: the call is the
# registry's own, spelled with the registry's parameter names, and anything more
# than that would be behaviour the control surface does not have.


def _advance_time(parameters: _Parameters) -> StepCall:
    minutes = parameters.number("minutes")
    return StepCall(
        "advance_time",
        None,
        lambda surface, documents: surface.advance_time(minutes=minutes),
    )


def _set_state(parameters: _Parameters) -> StepCall:
    entity_id = parameters.text("entity_id")
    state = parameters.text("state")
    return StepCall(
        "set_state",
        None,
        lambda surface, documents: surface.set_state(entity_id, state),
    )


def _user_action(parameters: _Parameters) -> StepCall:
    entity_id = parameters.text("entity_id")
    state = parameters.text("state")
    return StepCall(
        "user_action",
        None,
        lambda surface, documents: surface.user_action(entity_id, state),
    )


def _inject_fault(parameters: _Parameters) -> StepCall:
    entity_id = parameters.text("entity_id")
    fault = parameters.text("fault")
    return StepCall(
        "inject_fault",
        None,
        lambda surface, documents: surface.inject_fault(entity_id, fault),
    )


def _add_entity(parameters: _Parameters) -> StepCall:
    entity_id = parameters.text("entity_id")
    state = parameters.text("state")
    attributes = parameters.optional_mapping("attributes")
    return StepCall(
        "add_entity",
        None,
        lambda surface, documents: surface.add_entity(
            entity_id, state, attributes=attributes
        ),
    )


def _remove_entity(parameters: _Parameters) -> StepCall:
    entity_id = parameters.text("entity_id")
    return StepCall(
        "remove_entity",
        None,
        lambda surface, documents: surface.remove_entity(entity_id),
    )


def _set_availability(parameters: _Parameters) -> StepCall:
    entity_id = parameters.text("entity_id")
    available = parameters.flag("available")
    return StepCall(
        "set_availability",
        None,
        lambda surface, documents: surface.set_availability(entity_id, available),
    )


def _restart(parameters: _Parameters) -> StepCall:
    return StepCall("restart", None, lambda surface, documents: surface.restart())


def _snapshot(parameters: _Parameters) -> StepCall:
    return StepCall(
        "snapshot",
        parameters.optional_text("save"),
        lambda surface, documents: surface.snapshot(),
    )


def _export_config(parameters: _Parameters) -> StepCall:
    return StepCall(
        "export_config",
        parameters.optional_text("save"),
        lambda surface, documents: surface.export_config(),
    )


def _restore(parameters: _Parameters) -> StepCall:
    document = _document(parameters)
    return StepCall(
        "restore",
        None,
        lambda surface, documents: surface.restore(document(documents)),
    )


def _import_config(parameters: _Parameters) -> StepCall:
    document = _document(parameters)
    return StepCall(
        "import_config",
        None,
        lambda surface, documents: surface.import_config(document(documents)),
    )


def _get_decision_log(parameters: _Parameters) -> StepCall:
    window = parameters.optional_count("window")
    return StepCall(
        "get_decision_log",
        None,
        lambda surface, documents: surface.get_decision_log(window=window),
    )


def _install_pack(parameters: _Parameters) -> StepCall:
    manifest = parameters.text("manifest")
    return StepCall(
        "install_pack",
        None,
        lambda surface, documents: surface.install_pack(manifest),
    )


#: Every verb a step may name, in the order the registry and the port list them.
#: The keys of the dispatch table are the claim: the conformance check compares
#: this tuple to the control surface's operations plus its house-control verbs,
#: and to the schema's step keys, so neither can drift from the other two.
_BUILDERS: Mapping[str, Callable[[_Parameters], StepCall]] = {
    "advance_time": _advance_time,
    "set_state": _set_state,
    "user_action": _user_action,
    "inject_fault": _inject_fault,
    "snapshot": _snapshot,
    "restore": _restore,
    "get_decision_log": _get_decision_log,
    "install_pack": _install_pack,
    "export_config": _export_config,
    "import_config": _import_config,
    "add_entity": _add_entity,
    "remove_entity": _remove_entity,
    "set_availability": _set_availability,
    "restart": _restart,
}

#: The verbs, in that order.
STEP_VERBS: tuple[str, ...] = tuple(_BUILDERS)


def compile_step(step: Step) -> StepCall:
    """Compile one `Step` into the control-surface call it names.

    Takes the step rather than its two fields so the dispatch is the only thing
    here that reads a verb: a caller cannot compile a verb and a parameter set
    that came from two different steps.
    """
    try:
        builder = _BUILDERS[step.verb]
    except KeyError:
        raise UnknownVerbError(step.verb) from None
    return builder(_Parameters(step.verb, step.parameters))


def _document(
    parameters: _Parameters,
) -> Callable[[Mapping[str, Mapping[str, object]]], Mapping[str, object]]:
    """A document parameter, resolved against the documents earlier steps saved.

    The document is either written inline -- in which case it is handed straight
    through, because the operation validates it and the runner has no business
    reading a snapshot -- or named by `{from: <name>}`, in which case the name is
    looked up in what the run has saved so far and its absence is an error naming
    the name and the ones that do exist.

    The name is looked up when the call is *made* rather than when the step is
    compiled, because what a run has saved is a fact about the run: a `restore`
    that named its snapshot before the `snapshot` step executed would be a
    scenario whose order the compiler had silently repaired.
    """
    inline = parameters.mapping("document")
    name = parameters.document_name("document")
    if name is None:
        return lambda documents: inline

    def saved(documents: Mapping[str, Mapping[str, object]]) -> Mapping[str, object]:
        try:
            return documents[name]
        except KeyError:
            raise UnknownDocumentError(name, tuple(documents)) from None

    return saved
