"""The websocket contract between the panel and the integration.

The panel has exactly one way to reach the engine -- `callWS` against a command
name -- and for as long as this file did not exist, nothing checked that the
names the panel sends are names the integration registers. What that cost is
worth recording, because it is the whole reason this is a test and not a review
habit: `ws_room_create` was decorated with a schema whose `type` key appeared
**twice** --

    {vol.Required("type"): ROOM_CREATE, vol.Required("name"): str,
     vol.Required("type"): str}

-- and a Python dict literal that names a key twice keeps the last value. The
schema that reached Home Assistant therefore said the command was `str`, the
handler was registered under the command `"str"`, and `open_house/rooms/create`
did not exist. Clicking "Create room" in the panel answered `unknown_command`.
Nothing caught it: the module imports `homeassistant` at import time, so no test
in this suite could import it, and `_HANDLERS` -- whose comment promises that
"is every command registered" is "a question about one list a test can read" --
was read by no test at all.

So this file reads `websocket_api.py` as *text*, through `ast`, and never
imports it. That is not a workaround; it is the only vantage point from which
the defect is visible. Importing the module would run the decorators, and the
decorators are precisely what silently disagreed with the panel.

Three questions, in the order the failure would reach a user:

  * Does any schema name a key twice? That is the defect above, and it is
    invisible in the rendered source to any reader who is not counting.
  * Does every command the panel sends exist in the integration, and does every
    command the integration defines appear in the panel's protocol table? A
    command on one side only is a button that cannot work or a handler nothing
    can reach.
  * Is every decorated handler in `_HANDLERS`? The registration loop iterates
    that tuple, so a handler that defines a command and is not in the tuple
    registers nothing.
"""

from __future__ import annotations

import ast
import re
from typing import TYPE_CHECKING

from tools.catalog import paths

if TYPE_CHECKING:
    pass

WS_MODULE = "custom_components/open_house/websocket_api.py"
PROTOCOL = "panel/src/api/protocol.ts"

#: The value in a schema that names the command, by the schema's own key.
_TYPE_KEY = "type"


def _module() -> ast.Module:
    source = (paths.ROOT / WS_MODULE).read_text(encoding="utf-8")
    return ast.parse(source, filename=WS_MODULE)


def _schema_key(node: ast.expr) -> str:
    """The key a schema uses, as Home Assistant sees it.

    `vol.Required("room_type")` and `vol.Optional("room_type")` are both the key
    `room_type`; a bare string key is itself. Two keys that render differently
    but name the same key are the duplicate this file exists to find, so the
    comparison is on this normalised form rather than on the source text.
    """
    if isinstance(node, ast.Call) and node.args:
        inner = node.args[0]
        if isinstance(inner, ast.Constant) and isinstance(inner.value, str):
            return inner.value
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return ast.unparse(node)


def _constant_table(module: ast.Module) -> dict[str, str]:
    """Module-level `NAME = "open_house/..."` assignments."""
    table: dict[str, str] = {}
    for node in module.body:
        if (
            isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
            and node.value.value.startswith("open_house/")
        ):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    table[target.id] = node.value.value
    return table


def _schemas(module: ast.Module) -> list[tuple[int, ast.Dict, str]]:
    """Every `websocket_command({...})` schema, with its line and handler name."""
    found: list[tuple[int, ast.Dict, str]] = []
    for node in ast.walk(module):
        # `AsyncFunctionDef` is a sibling of `FunctionDef`, not a subclass of
        # it, and every handler here is `async def`. Filtering on `FunctionDef`
        # alone found no handlers at all, which made three of the tests below
        # pass by having nothing to check -- so this loop and the assertions it
        # feeds are deliberately paired: an empty result fails a test rather
        # than silencing one.
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call) or not decorator.args:
                continue
            callee = ast.unparse(decorator.func)
            if not callee.endswith("websocket_command"):
                continue
            schema = decorator.args[0]
            if isinstance(schema, ast.Dict):
                found.append((schema.lineno, schema, node.name))
    return found


def _defined_commands(module: ast.Module) -> set[str]:
    return set(_constant_table(module).values())


def test_no_schema_names_a_key_twice() -> None:
    """The defect that made "Create room" impossible.

    A repeated key is not a syntax error and not a type error. It is a silent
    overwrite whose only symptom is a command that does not exist, and the
    overwrite is only visible if you notice that the same call appears twice in
    one literal.
    """
    repeats: list[str] = []
    for line, schema, handler in _schemas(_module()):
        seen: dict[str, int] = {}
        for key in schema.keys:
            if key is None:
                continue  # `{**other}` -- not a named key
            name = _schema_key(key)
            if name in seen:
                repeats.append(
                    f"{WS_MODULE}:{line} {handler}: key {name!r} at line {seen[name]} "
                    f"is repeated"
                )
            seen[name] = key.lineno
    assert not repeats, "a schema names the same key twice:\n" + "\n".join(repeats)


def test_every_schema_declares_the_command_it_is_registered_under() -> None:
    """A schema with no `type` key registers nothing findable."""
    missing = [
        f"{WS_MODULE}:{line} {handler}"
        for line, schema, handler in _schemas(_module())
        if _TYPE_KEY not in {_schema_key(k) for k in schema.keys if k is not None}
    ]
    assert not missing, "a websocket schema declares no command name:\n" + "\n".join(
        missing
    )


def test_every_decorated_handler_registers_a_defined_command() -> None:
    """`type` names a constant, and the constant is one the module defines."""
    module = _module()
    table = _constant_table(module)
    problems: list[str] = []
    for line, schema, handler in _schemas(module):
        for key, value in zip(schema.keys, schema.values, strict=True):
            if key is None or _schema_key(key) != _TYPE_KEY:
                continue
            if not isinstance(value, ast.Name):
                problems.append(
                    f"{WS_MODULE}:{line} {handler}: the command is not a constant "
                    f"({ast.unparse(value)})"
                )
            elif value.id not in table:
                problems.append(
                    f"{WS_MODULE}:{line} {handler}: {value.id} is not defined as an "
                    f"open_house/... command"
                )
    assert not problems, "\n".join(problems)


def test_registered_commands_are_exactly_the_defined_commands() -> None:
    """Nothing is defined and left unregistered, and nothing registers twice.

    A constant with no decorator naming it is a command the panel can send and
    the integration will refuse. Two decorators naming one constant is a
    duplicate registration, which `async_register_command` raises for.
    """
    module = _module()
    table = _constant_table(module)
    named = [
        table[value.id]
        for _line, schema, _handler in _schemas(module)
        for key, value in zip(schema.keys, schema.values, strict=True)
        if key is not None
        and _schema_key(key) == _TYPE_KEY
        and isinstance(value, ast.Name)
        and value.id in table
    ]
    defined = set(table.values())
    assert sorted(named) == sorted(defined), (
        "defined but never registered: "
        f"{sorted(defined - set(named))}; "
        f"registered twice: {sorted({name for name in named if named.count(name) > 1})}"
    )


def test_every_command_is_in_the_handler_tuple() -> None:
    """`_HANDLERS` is what the registration loop iterates.

    A decorated handler that no tuple entry names defines a command and
    registers nothing -- the decorator alone does not register.
    """
    module = _module()
    decorated = {handler for _line, _schema, handler in _schemas(module)}
    listed: set[str] = set()
    for node in module.body:
        if not isinstance(node, ast.AnnAssign) or not isinstance(node.target, ast.Name):
            continue
        if node.target.id != "_HANDLERS" or not isinstance(node.value, ast.Tuple):
            continue
        listed = {
            element.id for element in node.value.elts if isinstance(element, ast.Name)
        }
    assert listed, f"{WS_MODULE}: _HANDLERS is not a tuple of names"
    assert decorated == listed, (
        f"decorated but not in _HANDLERS: {sorted(decorated - listed)}; "
        f"in _HANDLERS but not decorated: {sorted(listed - decorated)}"
    )


def test_the_panel_and_the_integration_agree_on_every_command() -> None:
    """The two tables are the same set.

    This is the assertion the shipped defect would have failed: the panel lists
    `open_house/rooms/create` and the integration, having registered its handler
    under `"str"`, did not.
    """
    integration = _defined_commands(_module())
    panel_source = (paths.ROOT / PROTOCOL).read_text(encoding="utf-8")
    block = panel_source.split("export const COMMANDS", 1)
    assert len(block) == 2, f"{PROTOCOL} no longer declares COMMANDS"
    # The table is `as const`, so every command is a string literal. Reading the
    # literals rather than evaluating the module keeps this test free of node.
    panel = set(re.findall(r'"(open_house/[^"]+)"', block[1]))
    assert panel, f"{PROTOCOL}: COMMANDS names no open_house command"
    assert panel == integration, (
        f"the panel sends and the integration does not register: "
        f"{sorted(panel - integration)}; "
        f"the integration registers and the panel never sends: "
        f"{sorted(integration - panel)}"
    )
