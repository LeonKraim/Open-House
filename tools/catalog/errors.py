"""Diagnostics.

Every check in this package reports the same shape of object, because every
check has to satisfy the same requirement: when it fails it must *name the
thing it failed on*. The acceptance gate in task 8.1 runs each requirement's
violating fixture and fails if the check passes, so a check that fails without a
name is a check nobody can act on.

A `Diagnostic` is therefore never constructed without a `message`, and the
message is expected to carry the specific file, rule, row or field at fault
rather than a generic complaint about the category.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


@dataclass(frozen=True, slots=True)
class Diagnostic:
    """One failed check, naming what failed.

    `where` is the artifact at fault -- a path, a row id, a rule order -- and is
    kept separate from `message` so callers can group or count by location
    without parsing prose.
    """

    check: str
    where: str
    message: str

    def __str__(self) -> str:
        return f"[{self.check}] {self.where}: {self.message}"


class CheckError(Exception):
    """Raised when a check cannot continue.

    Checks that can enumerate every problem collect `Diagnostic`s and return
    them. This is for the ones that cannot: a schema that will not parse, a git
    history that cannot be read. Raising is honest there, because continuing
    would mean reporting findings derived from something already known to be
    broken.
    """

    def __init__(self, check: str, where: str, message: str) -> None:
        super().__init__(f"[{check}] {where}: {message}")
        self.check = check
        self.where = where
        self.message = message


@dataclass(slots=True)
class Report:
    """A collection of diagnostics from one or more checks."""

    # Parameterised rather than a bare `list`: `field` is typed as returning
    # `Any`, so a bare `list` leaves the attribute partially unknown and every
    # later read of it inherits that.
    diagnostics: list[Diagnostic] = field(default_factory=list[Diagnostic])

    def add(self, check: str, where: str, message: str) -> None:
        self.diagnostics.append(Diagnostic(check=check, where=where, message=message))

    def extend(self, other: Report) -> None:
        self.diagnostics.extend(other.diagnostics)

    @property
    def ok(self) -> bool:
        return not self.diagnostics

    def __bool__(self) -> bool:
        return self.ok

    def render(self) -> str:
        return "\n".join(str(d) for d in self.diagnostics)


def read_text(path: Path) -> str:
    """Read a text file as UTF-8, byte for byte, with no newline translation.

    Decoding the bytes is not the same thing as `Path.read_text`. Text mode
    translates `\\r\\n` to `\\n` on the way in, and two Phase 0 checks compare
    content against what git has stored; a translated read would hide the
    difference between a file that changed and one that was merely checked out,
    which is the exact failure the immutability rule exists to catch.

    (`Path.read_text(newline=...)` would say this directly, but the parameter
    arrives in Python 3.13 and this project runs on 3.12.)
    """
    return path.read_bytes().decode("utf-8")
