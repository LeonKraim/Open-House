"""How a refusal reaches the panel: which type is caught, and which code is sent.

`websocket_api.py` imports `homeassistant` at import time, so no test in this
suite can import it -- the same wall `test_ws_contract.py` and
`test_binding_rows.py` describe. What is checkable without importing it is read
here two ways: the *handlers* are read as source through `ast`, and the two
functions that are pure -- `_missing` and `_error` -- are compiled out of the
source and called, so the claim is about behaviour rather than about text.

Four claims, one per defect:

  * `ws_profile_rename` catches the type `_profile_name` actually raises. It
    caught `ProfileError`, but the name is validated by
    `live._profile_name`, which raises `LiveSessionError` -- the two are
    unrelated siblings -- so a bad name left the handler as `ERR_UNKNOWN_ERROR`
    with a traceback instead of the sentence written for a person to read.
  * `ws_modules_detach` catches the session's refusal too. The module record is
    written by `modules.async_detach_slot`, but a *slot* detach then writes the
    session through `live_modules.set_slot_rule`/`set_slot`, which refuse in
    `LiveSessionError` -- so half an applied detach escaped as a raw 500.
  * `_bind` does not report a landed write as a failure when the *rebuild*
    behind it fails. The session and the subentry are written before the modules
    are built again, so a `ModuleHostError` there is the follow-up failing, and
    the handler has to answer with the room rather than with the error.
  * `ws_profile_activate_house` does the same for the profile half. The session
    is put on the profile first and the profile's *house* is restored after, so a
    refusal from the restore -- a snapshot naming a slot part this house has
    since dropped -- was answered as though the activation had failed. It had
    not: the house was on the new profile, and the panel, told otherwise, drew no
    refresh and went on showing the old one in force.
  * Every "there is no <thing>" refusal the live layer writes is classified
    `not_found`, and every device selector a blueprint can declare is named for
    the panel's `isDeviceInput` rather than falling back to `text`.
  * `_saved` saves. It read `await _saved(host)` -- itself -- so no change made
    in the panel ever reached the store, and the `RecursionError` that came of
    it was caught and logged as a store that refused the house. The whole suite
    passed: nothing here had asked whether the write happened, only what the
    reply said.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

WS_MODULE = ROOT / "custom_components" / "open_house" / "websocket_api.py"
PANEL_MODULE = ROOT / "panel" / "src" / "tabs" / "host-module.ts"

#: The live layer's refusals, which the classifier has to read. `views`/`host`
#: are not consulted: the family `_error` classifies is the one these write.
LIVE_MODULES = (
    ROOT / "ha_adapter" / "live.py",
    ROOT / "ha_adapter" / "live_modules.py",
    ROOT / "ha_adapter" / "live_profiles.py",
)

#: The phrase every "a named thing is not here" refusal begins with -- the same
#: constant `websocket_api._MISSING` holds, asserted equal below so a change to
#: one is a change to both.
_MISSING = "there is no "

#: What the panel reads a device input off (`isDeviceInput` over
#: `DEVICE_SELECTORS`); the server's names have to be these, exactly.
_DEVICE_BLOCK = re.compile(
    r"DEVICE_SELECTORS[^=]*=\s*new Set\(\[(?P<body>.*?)\]\)", re.DOTALL
)


def _function(path: Path, name: str) -> ast.AST:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name == name:
            return node
    raise AssertionError(f"{path.name} has no {name!r}")


def _compiled(path: Path, name: str, namespace: dict[str, Any]) -> Any:
    """One function, compiled out of the source it lives in.

    The module cannot be imported, so the function is cut out by name and
    exec'd against the names it reads -- the constants and sibling functions
    passed in `namespace`. `from __future__ import annotations` keeps the
    signature's own annotations, which name modules this test cannot import,
    from being evaluated.
    """
    source = path.read_text(encoding="utf-8")
    node = _function(path, name)
    segment = ast.get_source_segment(source, node)
    assert segment is not None
    scope = dict(namespace)
    exec("from __future__ import annotations\n" + segment, scope)
    return scope[name]


def _constants(path: Path) -> dict[str, str]:
    """Module-level `NAME = "literal"` assignments."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    table: dict[str, str] = {}
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    table[target.id] = node.value.value
    return table


def _dotted(node: ast.expr) -> str:
    """`ModuleHostError` or `modules.ModuleHostError`, as written."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{_dotted(node.value)}.{node.attr}"
    return ast.unparse(node)


def _caught(handler: ast.AST) -> set[str]:
    """Every exception type the handler catches, by its dotted name.

    Read over the whole body rather than the first `try`: a handler with two of
    them -- one per layer that can refuse -- is exactly the shape the defect
    about, so the test must see both.
    """
    caught: set[str] = set()
    for node in ast.walk(handler):
        if isinstance(node, ast.Try):
            for case in node.handlers:
                if case.type is not None:
                    caught.add(_dotted(case.type))
    return caught


def _handler_for(handler: ast.AST, dotted: str) -> ast.ExceptHandler:
    for node in ast.walk(handler):
        if isinstance(node, ast.Try):
            for case in node.handlers:
                if case.type is not None and _dotted(case.type) == dotted:
                    return case
    raise AssertionError(f"no except {dotted} in {getattr(handler, 'name', '?')}")


def _calls(node: ast.AST) -> set[str]:
    return {
        _dotted(inner.func) for inner in ast.walk(node) if isinstance(inner, ast.Call)
    }


def _declared_missing_phrases() -> set[str]:
    """Every "there is no <thing>" sentence the live layer writes, as text.

    The literal head of each refusal: `f"there is no room {x!r} ..."` yields
    `"there is no room "`, which is the whole of what the classifier keys on.
    """
    phrases: set[str] = set()
    for path in LIVE_MODULES:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.JoinedStr) and node.values:
                head = node.values[0]
                if (
                    isinstance(head, ast.Constant)
                    and isinstance(head.value, str)
                    and head.value.startswith("there is no")
                ):
                    phrases.add(head.value)
            elif (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and node.value.startswith("there is no")
            ):
                phrases.add(node.value)
    return phrases


def _panel_device_selectors() -> set[str]:
    """The names the panel answers a device input for, read from its source."""
    source = PANEL_MODULE.read_text(encoding="utf-8")
    match = _DEVICE_BLOCK.search(source)
    assert match is not None, "the panel's DEVICE_SELECTORS is gone or rewritten"
    return set(re.findall(r'"([^"]+)"', match.group("body")))


# --------------------------------------------------------------------------
# The type each handler catches
# --------------------------------------------------------------------------


def test_profile_rename_catches_the_type_the_name_raises() -> None:
    """**The type `_profile_name` raises, not the set's beside it.**

    `rename` validates the new name through the live layer, which refuses in
    `LiveSessionError`; the profile *set* refuses in `ProfileError`. They are
    unrelated classes, so catching only `ProfileError` let a bad name -- `"!!!"`
    -- reach the panel as an unknown error. Both are caught now.
    """
    caught = _caught(_function(WS_MODULE, "ws_profile_rename"))
    assert "LiveSessionError" in caught
    assert "ProfileError" in caught


def test_modules_detach_catches_the_session_refusal() -> None:
    """A slot detach writes two layers, and either can refuse.

    The record is `modules.async_detach_slot`'s (`ModuleHostError`); the rule and
    the slot it leaves behind are the session's (`LiveSessionError`). A handler
    that caught only the first left a half-applied detach as a raw 500.
    """
    caught = _caught(_function(WS_MODULE, "ws_modules_detach"))
    assert "LiveSessionError" in caught
    assert "modules.ModuleHostError" in caught


def test_bind_does_not_report_a_landed_write_after_a_rebuild_failure() -> None:
    """**The write landed; only the rebuild behind it did not.**

    `host.async_set_binding` edits the session and writes the subentry *before*
    it builds the modules again, so a `ModuleHostError` from it is the follow-up
    failing. Reported as a failure it would tell a person the slot was not bound
    when everything reads back that it was -- so the branch notes the rebuild and
    the reply is the room, exactly as the success path answers.
    """
    bind = _function(WS_MODULE, "_bind")
    branch = _handler_for(bind, "modules.ModuleHostError")
    assert "connection.send_error" not in _calls(branch)
    assert "_unsettled" in _calls(branch)


def test_activating_a_profile_reports_the_activation_and_not_the_restore() -> None:
    """**The house is on the profile; only the restore behind it was refused.**

    `activate_house` puts the session on the profile, and the *house* the profile
    carries is put back afterwards. That second half can refuse on its own merits
    -- a snapshot names the parts the house had when it was taken, and a part
    since removed from this house is not there to bind -- and the handler
    answered the whole call with that refusal. So a person who put the house on a
    profile was told it had failed while it had not: the panel's `act` raised,
    drew no refresh, and left the screen showing the profile that had just been
    replaced. The refusal is worth reading, so it goes to the log with the
    profile it was about; the reply says what happened.

    Read as two branches, because both have to be right: the *activation*'s own
    refusal still answers with the error (nothing has landed, and a person needs
    the sentence), and the *restore*'s does not.
    """
    handler = _function(WS_MODULE, "ws_profile_activate_house")

    activation = _handler_for(handler, "LiveSessionError")
    assert "_error" in _calls(activation), (
        "a profile that could not be activated now answers without saying why"
    )

    restore = _handler_for(handler, "Exception")
    assert "connection.send_error" not in _calls(restore)
    assert "_error" not in _calls(restore)
    assert "_unsettled" in _calls(restore)


# --------------------------------------------------------------------------
# Which code a refusal is answered with
# --------------------------------------------------------------------------


class _Connection:
    """Enough of a websocket connection to catch one `send_error`."""

    def __init__(self) -> None:
        self.sent: list[tuple[Any, str, str]] = []

    def send_error(self, msg_id: Any, code: str, text: str) -> None:
        self.sent.append((msg_id, code, text))


def _error_function() -> Any:
    constants = _constants(WS_MODULE)
    missing = _compiled(WS_MODULE, "_missing", {"_MISSING": constants["_MISSING"]})
    return _compiled(
        WS_MODULE,
        "_error",
        {
            "_missing": missing,
            "NOT_FOUND": constants["NOT_FOUND"],
            "INVALID_FORMAT": constants["INVALID_FORMAT"],
        },
    )


def test_the_classifier_keys_on_the_phrase_the_live_layer_writes() -> None:
    """The scanner and the module have to agree on the phrase, or one is stale."""
    assert _constants(WS_MODULE)["_MISSING"] == _MISSING
    assert _declared_missing_phrases(), "the live layer writes no 'there is no'"


def test_every_missing_thing_refusal_is_read_as_not_found() -> None:
    """**Every producer, not the three substrings that were there before.**

    `_error` used to test for `"no room"`, `"no slot"` and `"no option"` -- which
    missed a missing *module* and a missing *entity* (the two commonest), and one
    of the three did not even match its own sentence, `there is no house slot`
    containing no `no slot`. The classifier now keys on the one phrase the whole
    family is written with, and this reads that family out of the live layer's
    own source -- so a fifth producer is covered the day it is written.
    """
    missing = _compiled(WS_MODULE, "_missing", {"_MISSING": _MISSING})
    phrases = _declared_missing_phrases()

    for phrase in sorted(phrases):
        assert phrase.startswith(_MISSING), f"{phrase!r} is not a 'there is no'"
        refusal = Exception(f"{phrase}the thing in this house")
        assert missing(refusal) is True

    # And the family is the six things a person can name, so a scanner that
    # silently found nothing cannot make this pass.
    named = {phrase.removeprefix(_MISSING).strip() for phrase in phrases}
    assert {"room", "house slot", "slot", "entity", "module", "option"} <= named


def test_a_refusal_that_is_not_a_missing_thing_is_not_read_as_not_found() -> None:
    """The other half of the classification, and the reason it matters.

    `not_found` is "that item no longer exists; reload the panel" and
    `invalid_format` is a value refused on its merits, kept in the form. A slot a
    module does not reach is the second kind: the row is right there and the
    answer is wrong, so it must not send a person to reload.
    """
    missing = _compiled(WS_MODULE, "_missing", {"_MISSING": _MISSING})
    for sentence in (
        "the module 'bedtime' does not reach a slot 'light_group'; the slots it "
        "acts through are 'motion_sensor'",
        "a house needs at least one room",
        "",
    ):
        assert missing(Exception(sentence)) is False


def test_the_code_sent_for_each_kind_is_the_panel_s() -> None:
    """`_error` itself, called: a missing thing is `not_found`, and it is not."""
    constants = _constants(WS_MODULE)
    send = _error_function()

    connection = _Connection()
    send(connection, {"id": 7}, Exception("there is no module 'x' in this house"))
    assert connection.sent == [
        (7, constants["NOT_FOUND"], "there is no module 'x' in this house")
    ]

    connection = _Connection()
    send(connection, {"id": 8}, Exception("that is not a number"))
    assert connection.sent == [(8, constants["INVALID_FORMAT"], "that is not a number")]


# --------------------------------------------------------------------------
# The write that follows a change that landed
# --------------------------------------------------------------------------


class _Host:
    """Enough of an `OpenHouseHost` to see which save a handler reached for."""

    def __init__(self) -> None:
        self.saves = 0

    async def async_save(self) -> None:
        self.saves += 1


def _save_function() -> Any:
    """`_saved`, compiled with the names it reads -- including itself.

    It must have itself in scope, because that is exactly the defect: under the
    bug the body's own call found *this* function rather than the host's, and a
    save that never happened was reported as a house that would not be written.
    """
    notes: list[Exception] = []

    def _unsaved(refusal: Exception) -> None:
        notes.append(refusal)

    compiled = _compiled(WS_MODULE, "_saved", {"_unsaved": _unsaved})
    return compiled, notes


def test_the_write_after_a_change_is_the_hosts_save_and_not_itself() -> None:
    """**The house is actually written down after a change lands.**

    `_saved` reads `await _saved(host)`. That is a call to itself: no mutation
    reached `host.async_save`, so nothing the panel did survived a restart --
    and it failed *quietly*. The recursion raised `RecursionError` ~1000 frames
    down, the `except Exception` beside the call caught it a frame above, and
    the handler reported the result of a save that never ran. Every screen read
    the new value back from the live session, so the only trace was a log line
    saying the store had refused a house that was never offered to it.

    Counted rather than inspected: the host's save is called exactly once, and
    a save that refuses is still noted rather than raised.
    """
    import asyncio

    saved, notes = _save_function()
    host = _Host()
    asyncio.run(saved(host))
    assert host.saves == 1, (
        "a change that landed did not reach the store: every edit in the panel "
        "was live and would have been gone at the next restart"
    )
    assert notes == [], "a save that worked was reported as one that did not"


def test_a_refusing_save_is_noted_and_not_raised() -> None:
    """The follow-up rule holds for the save: the change landed, so it answers.

    The handler has already changed the session by the time this runs, so a
    store that refuses must leave the reply alone and write the fact to the log
    -- the panel draws no field for it, and a 500 here would say the edit
    failed when it did not.
    """
    import asyncio

    class _Refusing(_Host):
        async def async_save(self) -> None:
            raise OSError("the disk is full")

    saved, notes = _save_function()
    host = _Refusing()
    asyncio.run(saved(host))  # nothing raised, or this test does not get here
    assert [type(note) for note in notes] == [OSError]


# --------------------------------------------------------------------------
# The names a selector reaches the panel under
# --------------------------------------------------------------------------


def test_every_device_selector_the_panel_knows_is_named_for_it() -> None:
    """**A blueprint's `device:`/`area:`/`floor:`/`label:`/`attribute:` too.**

    The panel answers an input with a *slot* rather than a setting when
    `isDeviceInput` matches its selector, and it matches by exact name
    (`DEVICE_SELECTORS`). A selector reported as anything else reaches the panel
    as `text` and is answered as a plain setting -- a device stored as a value on
    a module that then acts on nothing. Every kind in that set is named for
    itself here, and `target` keeps the name the panel already had for it.
    """
    kind = _compiled(WS_MODULE, "_selector_kind", {"Mapping": Mapping})

    for name in sorted(_panel_device_selectors()):
        assert kind({"selector": {name: {}}}) == name, name

    # A selector the panel does *not* call a device is not dressed as one: the
    # controls beside it (a menu, a number box) are still named right.
    assert kind({"selector": {"number": {"min": 0}}}) == "number"
    assert kind({"selector": {"select": {"options": ["a"]}}}) == "select"
    assert kind({"selector": {"colour_rgb": {}}}) == "text"
    assert kind({}) == "text"
