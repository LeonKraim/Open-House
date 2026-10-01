"""The failure the registry's operations raise when they cannot continue.

A check reports `CheckFinding`s and returns; it collects everything it found so
one run carries every reason rather than the first. This is for the operations
that cannot collect: a generator with two pointers claiming one name and
version has no index to write, and a store asked to install a pack whose digest
does not match has nothing to hand the engine. Raising is honest there, because
continuing would mean acting on something already known to be inconsistent.

`where` and `reason` are kept apart from the message so a caller branching on
the failure reads a field rather than parsing prose, and so a diagnostic that
said only "an index could not be built" would still name the file.
"""

from __future__ import annotations

__all__ = ["RegistryError"]


class RegistryError(Exception):
    """The registry cannot carry out the operation, naming what and why."""

    def __init__(self, where: str, reason: str) -> None:
        self.where = where
        self.reason = reason
        super().__init__(f"{where}: {reason}")
