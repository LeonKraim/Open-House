"""House modes and their exclusive groups -- task 6.0.

A mode is a house-wide state a behaviour can be gated on -- `home`, `away`,
`sleep`, `holiday` -- and `schemas/mode/1.0.0.json`'s `exclusive_group` is what
makes "only one of these at a time" expressible without a rule enumerating every
forbidden pair. Four modes in one group need no engine change to become
mutually exclusive and a fifth group needs none to be added, which is the whole
reason exclusivity is read from the schema rather than written here.

Two things about this module are not the obvious choices:

- **There is no mode catalog.** `catalog/` has no `modes.yaml`, so a mode is not
  read from a file the way a slot is: the definitions are supplied -- by a
  fixture house today, by a pack in Phase 2 -- and validated against the frozen
  schema, which is the only mode artifact there is. `ModeSet` therefore takes
  documents rather than a path, and `engine/vocabulary.py` hands it the schema.
- **A mode gate that is unmet is `declined`, not `skipped`.** A behaviour gated
  on a mode that is not active is an evaluation that reached no command because
  its *condition* was unmet, which `engine-core` calls `declined`; `skipped` is
  reserved for an evaluation the engine never ran. This module decides only
  whether a mode is active -- which disposition that produces is the engine's,
  and folding the two together here would put a log vocabulary in a mode set.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import cast

import jsonschema

from engine.vocabulary import Vocabulary


class ModeError(Exception):
    """Base for the failures this module defines."""


class InvalidModeError(ModeError):
    """A mode document that does not validate against the frozen mode schema.

    The index is named rather than the mode, because a document invalid enough to
    fail here may not carry a usable `name` -- naming a row that has none is how
    a failure ends up unreadable.
    """

    def __init__(self, index: int, message: str) -> None:
        super().__init__(f"mode {index} does not validate: {message}")
        self.index = index
        self.message = message


class DuplicateModeError(ModeError):
    """Two documents declaring one mode name.

    The schema states a name's shape but cannot state its uniqueness within a
    set, so a second definition would silently replace the first -- and the mode
    that survived would be the one later in the list, which nothing states.
    """

    def __init__(self, name: str) -> None:
        super().__init__(f"the mode {name!r} is declared twice")
        self.name = name


class UnknownModeError(ModeError):
    """A mode name the set does not declare.

    Activating, deactivating or gating on an undeclared mode is a caller's
    mistake and not an ungrouped mode: answering it with "not active" would let a
    typo gate a behaviour off forever without anything saying so.
    """

    def __init__(self, name: str) -> None:
        super().__init__(f"no mode named {name!r} is declared")
        self.name = name


@dataclass(frozen=True, slots=True)
class Mode:
    """One declared mode, as the set's rules see it."""

    name: str
    #: The group whose members are mutually exclusive with this one, or `None`
    #: for a mode that excludes nothing and is excluded by nothing.
    exclusive_group: str | None


class ModeSet:
    """The house's declared modes and the subset currently active.

    `activate` is the only mutating operation and it is where exclusivity lives:
    activating a mode clears every active sibling in its group and returns their
    names, so the caller can record what changed rather than diffing the set
    afterwards.
    """

    def __init__(
        self,
        definitions: Sequence[Mapping[str, object]],
        *,
        vocabulary: Vocabulary,
    ) -> None:
        self._declared = _declared(definitions, vocabulary)
        self._active: set[str] = set()

    @property
    def declared(self) -> Mapping[str, Mode]:
        """Every declared mode, keyed by name."""
        return dict(self._declared)

    @property
    def active(self) -> frozenset[str]:
        """The active modes. A set because no order is part of a mode's meaning."""
        return frozenset(self._active)

    def activate(self, name: str) -> tuple[str, ...]:
        """Activate `name`, clearing its group siblings; return the cleared names.

        The cleared names come back sorted, so a caller recording them writes the
        same record whichever order the active set happened to iterate in.
        """
        mode = self._mode(name)
        cleared: tuple[str, ...] = ()
        if mode.exclusive_group is not None:
            cleared = tuple(
                sorted(
                    other
                    for other in self._active
                    if other != name
                    and self._declared[other].exclusive_group == mode.exclusive_group
                )
            )
        self._active -= set(cleared)
        self._active.add(name)
        return cleared

    def deactivate(self, name: str) -> None:
        """Deactivate `name`. Deactivating an inactive mode does nothing."""
        self._mode(name)
        self._active.discard(name)

    def is_active(self, name: str) -> bool:
        """Whether `name` is one of the active modes."""
        return self._mode(name).name in self._active

    def group_of(self, name: str) -> str | None:
        """`name`'s exclusive group, or `None`."""
        return self._mode(name).exclusive_group

    def _mode(self, name: str) -> Mode:
        try:
            return self._declared[name]
        except KeyError:
            raise UnknownModeError(name) from None


def _declared(
    definitions: Sequence[Mapping[str, object]], vocabulary: Vocabulary
) -> Mapping[str, Mode]:
    """Validate every definition against the frozen schema, keyed by name.

    A mode is validated the way a house is (`engine/binding.py`), because the
    engine reads its vocabulary from the frozen artifacts rather than carrying
    one: the schema says what a mode *is* and this function only projects the two
    facts the set's rules read.
    """
    validator = jsonschema.Draft202012Validator(vocabulary.mode_schema)
    modes: dict[str, Mode] = {}
    for index, document in enumerate(definitions):
        errors = sorted(
            validator.iter_errors(document),
            key=lambda error: (list(error.absolute_path), error.message),
        )
        if errors:
            raise InvalidModeError(index, errors[0].message)
        name = cast("str", document["name"])
        if name in modes:
            raise DuplicateModeError(name)
        modes[name] = Mode(
            name=name,
            exclusive_group=cast("str | None", document.get("exclusive_group")),
        )
    return modes
