"""Narrowing helpers for values read out of YAML and JSON.

`json.loads` and `yaml.safe_load` both return `Any`, and strict type checking is
right to refuse to guess what a document holds: the whole point of these checks
is that a file which is not what it claims to be is reported by name. Casting
would assert a shape nothing has verified, and would move the failure from a
diagnostic to a traceback.

So these narrow by inspection instead. Anything that is not the expected kind
becomes an empty value of that kind, and the checks that follow then report the
missing or malformed field by name -- which is what the requirements ask for and
what a reader can act on.
"""

from __future__ import annotations

from typing import cast


def as_mapping(value: object) -> dict[str, object]:
    """A mapping with string keys, or an empty one.

    Keys are stringified rather than required to be strings: YAML permits an
    integer key, and a document that used one should produce a named-field
    diagnostic rather than an exception on the way in.
    """
    if not isinstance(value, dict):
        return {}
    items = cast("dict[object, object]", value)
    return {str(key): item for key, item in items.items()}


def as_sequence(value: object) -> list[object]:
    """A list, or an empty one. Tuples and sets are not lists and are refused."""
    return list(cast("list[object]", value)) if isinstance(value, list) else []


def as_text(value: object) -> str | None:
    """A value that is a string, or None. Never `str(value)`.

    Stringifying would turn a number, a list or a nested mapping into something
    that looks like the value a check is looking for, and the checks here exist
    so that an out-of-vocabulary value fails *by name*.
    """
    return value if isinstance(value, str) else None


def as_bool(value: object) -> bool | None:
    """A value that is a boolean, or None.

    Written out rather than left to truthiness because `"false"` is a string, a
    non-empty one, and therefore true -- which is how a YAML typo becomes a
    silently enabled feature.
    """
    return value if isinstance(value, bool) else None
