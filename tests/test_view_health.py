"""An engine read that fails is a health issue, not an empty Rooms tab.

`views.health_issues` is where the Rooms tab and the Overview tab count their
issues from, and `room_summaries` computes that tuple *once* and hands the same
one to every row -- so a read that raised inside it did not lose a row, it lost
the screen. That is what a device leaving the house used to do: the engine's
repair read raised out of a slot whose member was gone, and the tab came back
empty instead of saying what was wrong.

The engine's own read path no longer raises for a name the house does not hold
(`engine/binding.py` reads a gone member as present-and-unreadable), so the
table below is not what makes that case work. It is the belt to that fix's
braces: `health_issues` must not depend on no read ever raising, because a later
phase will add reads it did not anticipate, and "the engine could not answer" is
exactly the kind of thing the Health tab exists to say. The property checked is
the structural one the defect turns on -- both engine reads sit inside a `try`
whose handler reports a row, and neither is called anywhere outside one.

`custom_components/open_house/views.py` imports `homeassistant` at import time,
so no test in this suite can import it; its functions are read as source through
`ast`, the way `test_binding_rows.py` reads `modules.py` and `test_ws_contract.py`
reads the same package. The *behaviour* of the engine half -- a slot read and
`repairs()` surviving a removed device -- is proven by execution in
`tests/test_engine_missing_device.py`; what cannot be executed here is checked
for shape.
"""

from __future__ import annotations

import ast
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: The module under test, and the function the two tabs reach through.
_VIEWS = ROOT / "custom_components" / "open_house" / "views.py"

#: The engine reads that must not escape `health_issues`, by the attribute they
#: are called through. Both are on the same `host.session.engine` object.
_ENGINE_READS = ("repairs", "hazards")


def _function(tree: ast.Module, name: str) -> ast.FunctionDef:
    """The named top-level function, found by name so a move does not break this."""
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{_VIEWS.name} has no {name}")


def _calls(node: ast.AST) -> Counter[str]:
    """How often each attribute is called anywhere under `node`, by attribute name.

    Counted rather than collected into a set, because the failure this guards
    against is a *second* call: an implementation that wrapped the read in a
    `try` and left a bare one beside it would satisfy a set-membership test and
    still raise through the bare one.
    """
    return Counter(
        inner.func.attr
        for inner in ast.walk(node)
        if isinstance(inner, ast.Call) and isinstance(inner.func, ast.Attribute)
    )


def _guarded_and_total(function: ast.FunctionDef) -> tuple[Counter[str], Counter[str]]:
    """Calls inside every `try` body in `function`, and calls in all of it."""
    guarded: Counter[str] = Counter()
    for node in ast.walk(function):
        if not isinstance(node, ast.Try):
            continue
        for statement in node.body:
            guarded += _calls(statement)
    return guarded, _calls(function)


def test_health_issues_wraps_both_engine_reads() -> None:
    """Neither engine read is called outside a `try` that has a handler.

    Falsified by the pre-fix `health_issues`, which iterated
    `host.session.engine.repairs()` and `.hazards()` in bare `for` statements:
    a raise from either escaped into `room_summaries` and emptied the tab. A fix
    that wrapped one read and left the other bare is failed here too, because
    each is required to appear the same number of times guarded as in total.
    """
    function = _function(ast.parse(_VIEWS.read_text(encoding="utf-8")), "health_issues")
    guarded, total = _guarded_and_total(function)

    for read in _ENGINE_READS:
        assert guarded[read] >= 1, (
            f"health_issues calls engine.{read}() outside a try, so a failing "
            "read escapes into room_summaries and empties the Rooms tab"
        )
        assert guarded[read] == total[read], (
            f"health_issues calls engine.{read}() {total[read]} time(s) but "
            f"guards only {guarded[read]}, so the unguarded one still escapes"
        )


def test_the_handler_reports_a_row_and_not_a_bare_swallow() -> None:
    """The failed read becomes a health issue row, attributed to no room.

    A `try` that caught the error and did nothing would pass the test above and
    still hide the fault: the whole point is that the Health tab says what is
    wrong. So the handler must call the row builder, and that row must be an
    error with `room_id: None` -- `room_summary` counts issues by room id, so a
    row carrying a room would move that room's badge for a failure that is not
    the room's.
    """
    tree = ast.parse(_VIEWS.read_text(encoding="utf-8"))
    health = _function(tree, "health_issues")
    handler_calls = Counter(
        inner.func.id
        for node in ast.walk(health)
        if isinstance(node, ast.Try)
        for statement in (child for handler in node.handlers for child in handler.body)
        for inner in ast.walk(statement)
        if isinstance(inner, ast.Call) and isinstance(inner.func, ast.Name)
    )
    assert handler_calls["_engine_read_failed"] >= 1, (
        "a health_issues handler swallows the failure without reporting a row"
    )

    row = _function(tree, "_engine_read_failed")
    fields = {
        key.value: value
        for inner in ast.walk(row)
        if isinstance(inner, ast.Dict)
        for key, value in zip(inner.keys, inner.values, strict=True)
        if isinstance(key, ast.Constant) and isinstance(key.value, str)
    }
    severity = fields.get("severity")
    assert isinstance(severity, ast.Constant) and severity.value == "error", (
        "the engine-read-failed row is not an error"
    )
    room_id = fields.get("room_id")
    assert isinstance(room_id, ast.Constant) and room_id.value is None, (
        "the engine-read-failed row names a room, so a room's badge moves for it"
    )
    assert {"code", "title", "detail", "entity_id", "repairs_flow_id"} <= set(fields), (
        f"the row is missing fields the panel's HealthIssue reads: {sorted(fields)}"
    )
