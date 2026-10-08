"""The reload a restore triggers, and why it must not land in the middle of one.

`custom_components/open_house/__init__.py` imports `homeassistant` at import
time, so no test here can import it -- the wall `test_ws_errors.py` describes.
The one function that decides the question is pure enough to compile out of the
source and call, and it is called here three ways, because "a restore is not
interrupted" is only worth anything if the *ordinary* reload still happens.

The defect this is about, observed on the running house:

  * Put the house on a profile and it went on showing the old one in force. The
    session had moved and the answer said so, and the screen went on saying
    otherwise. Written down, the store and the live session named two different
    profiles -- and the store was the *newer* one, which is the tell: the write
    had landed and something had thrown it away afterwards.
  * What threw it away was the reload. A restore writes each room's bindings
    back through its config subentry, and a subentry write is what `_async_reload_entry`
    is registered for -- so a reload lands partway through every restore, by
    construction. A reload composes the house from the subentries *and the
    store together*, and partway through a restore both are only some of the way
    there. The house that came up was the one from before the restore, on the
    profile from before it.
  * The other half is the order of the writes in `ws_profile_activate_house`: the
    store has to carry the new profile *before* the first subentry write, or a
    reload that lands in the window reads a store still naming the old one. Its
    sibling is `async_set_slot_parts`, which already saves its record before
    writing any room, for exactly this reason.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

INIT_MODULE = ROOT / "custom_components" / "open_house" / "__init__.py"
HOST_MODULE = ROOT / "custom_components" / "open_house" / "host.py"
WS_MODULE = ROOT / "custom_components" / "open_house" / "websocket_api.py"

_DOMAIN = "open_house"
_SESSION_KEY = "sessions"


def _function(path: Path, name: str) -> ast.AST:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name == name:
            return node
    raise AssertionError(f"{path.name} has no {name!r}")


def _compiled(path: Path, name: str, namespace: dict[str, Any]) -> Any:
    """One function, compiled out of the source it lives in, as `test_ws_errors` does."""
    source = path.read_text(encoding="utf-8")
    segment = ast.get_source_segment(source, _function(path, name))
    assert segment is not None
    scope = dict(namespace)
    exec("from __future__ import annotations\n" + segment, scope)
    return scope[name]


class _Entries:
    """`hass.config_entries`, remembering which entry was asked to reload."""

    def __init__(self) -> None:
        self.reloaded: list[str] = []

    async def async_reload(self, entry_id: str) -> None:
        self.reloaded.append(entry_id)


class _Hass:
    def __init__(self, data: dict[str, Any]) -> None:
        self.data = data
        self.config_entries = _Entries()


class _Host:
    """A live host, with only the one fact the listener reads."""

    def __init__(self, restoring: bool) -> None:
        self.is_restoring = restoring


def _listener() -> Any:
    return _compiled(
        INIT_MODULE,
        "_async_reload_entry",
        {
            "DOMAIN": _DOMAIN,
            "_session_data_key": lambda: _SESSION_KEY,
            "host": None,  # the module's lazy import is not reached by this function
        },
    )


def _entry_id() -> str:
    return "entry-1"


def _hass_with(host: Any) -> _Hass:
    sessions = {} if host is None else {_entry_id(): host}
    return _Hass({_DOMAIN: {_SESSION_KEY: sessions}})


def test_a_reload_is_skipped_while_a_profile_is_being_put_back() -> None:
    """**A reload partway through a restore is the restore being thrown away.**

    See the module docstring: the session moves, the rooms are written one at a
    time, and the reload that the first of them schedules composes the house out
    of a store and a set of subentries that are both halfway to the profile. The
    house that comes up is the one from before the activation, and nothing
    reloads again -- so the person's click is undone by the act of performing it
    and the screen says the old profile is still in force.
    """
    hass = _hass_with(_Host(restoring=True))
    _run(_listener(), hass)
    assert hass.config_entries.reloaded == []


def test_an_ordinary_reload_still_happens() -> None:
    """The suppression is the exception and not the rule: a settled host reloads.

    A room's bindings are written by an ordinary edit too, and the reload that
    follows it is how the house is composed again -- taking that away would be a
    panel that stopped re-reading its own writes.
    """
    hass = _hass_with(_Host(restoring=False))
    _run(_listener(), hass)
    assert hass.config_entries.reloaded == [_entry_id()]


def test_an_entry_with_no_live_host_is_still_reloaded() -> None:
    """No session is not a restore in progress.

    The host is absent while the engine could not be imported, and while the
    entry is being unloaded -- and a reload arriving then is a reload that should
    happen rather than one that should be swallowed.
    """
    hass = _hass_with(None)
    _run(_listener(), hass)
    assert hass.config_entries.reloaded == [_entry_id()]


def test_the_store_is_written_before_the_restore_writes_a_room() -> None:
    """**The save comes first, and the order is the whole point.**

    A restore's first room write is what schedules the reload. Saved after the
    last of them, a reload landing in the window reads a store still naming the
    profile the house was on a moment ago -- so the order is asserted on the
    lines the handler has, not on the behaviour the store happens to show.
    `async_set_slot_parts` states the same rule for the same reason.
    """
    handler = _function(WS_MODULE, "ws_profile_activate_house")
    saved = _call_lines(handler, "_saved")
    restored = _call_lines(handler, "async_restore_setup")
    assert saved, "the handler no longer writes the store itself"
    assert restored, "the handler no longer puts the profile's house back"
    assert min(saved) < min(restored), (
        "the store is written after the restore instead of before it"
    )


def test_the_host_reports_whether_it_is_restoring() -> None:
    """The flag the listener reads is the host's own, and it is named for the question."""
    node = _function(HOST_MODULE, "is_restoring")
    assert isinstance(node, ast.FunctionDef)
    segment = ast.get_source_segment(HOST_MODULE.read_text(encoding="utf-8"), node)
    assert segment is not None
    scope: dict[str, Any] = {"property": property}
    exec("from __future__ import annotations\n" + segment, scope)

    class _Stub:
        def __init__(self, restoring: bool) -> None:
            self._restoring = restoring

    accessor = scope["is_restoring"].__get__  # type: ignore[attr-defined]
    assert accessor(_Stub(True))() is True
    assert accessor(_Stub(False))() is False


def _call_lines(node: ast.AST, name: str) -> list[int]:
    """The lines at which `name(...)` is called, in source order.

    Matched on the last segment, so `host.async_restore_setup(...)` is a call to
    `async_restore_setup` -- the handler reads both spellings and the order
    between them is the claim.
    """
    return sorted(
        inner.lineno
        for inner in ast.walk(node)
        if isinstance(inner, ast.Call) and _dotted(inner.func).split(".")[-1] == name
    )


def _dotted(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{_dotted(node.value)}.{node.attr}"
    return ast.unparse(node)


def _run(coro: Any, hass: _Hass) -> None:
    import asyncio

    asyncio.run(coro(hass, type("_Entry", (), {"entry_id": _entry_id()})()))
