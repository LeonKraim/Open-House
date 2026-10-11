"""The local admin panel, without a socket and without a Store.

`store/admin.py` is a script: it binds a port and holds the Store's admin
account, and neither of those is a thing a test may do. What *is* here is the
half that can be wrong without anybody noticing -- the addresses it builds, the
escaping it does on the way to markup, the two guards that stand in front of
every page and every write, and what it does with an answer the Store gave.

**The Store is replaced by a function.** `PocketBase` takes its opener as a
parameter for exactly this reason: what is worth pinning is that a given call
becomes a given request, and that a given answer is read the right way -- both
of which are visible through that one seam and neither of which needs a
PocketBase in the room.

**The panel is driven through an in-memory connection.** `http.server` handlers
read a request from a file object and write the reply into another, so a
handler can be run with a `BytesIO` on each side. That is what lets the two
guards be checked -- a page with no session, a write with no CSRF token --
without binding anything. It is fiddly, and it is the only way to check the rule
that matters most: that a route added tomorrow is guarded by having been added.
"""

from __future__ import annotations

import importlib.util
import io
import json
import re
import sys
import time
import types
import urllib.error
from pathlib import Path
from typing import Any

import pytest

from ha_adapter import store_api

ROOT = Path(__file__).resolve().parents[1]


def _load_admin() -> types.ModuleType:
    """`store/admin.py`, loaded from its path.

    It is a script in a directory rather than a module in a package, so it is
    loaded the way `tests/test_store_client.py` loads the integration's client:
    registered in `sys.modules` while it loads, because `@dataclass` reads the
    defining module back out of there, and taken out again afterwards so a
    module this file loaded by hand is not one an import would find.

    Nothing is stubbed, and that is the point of the file: `store/admin.py`
    imports the standard library and nothing else, so it loads anywhere.
    """
    path = ROOT / "store" / "admin.py"
    spec = importlib.util.spec_from_file_location("oh_store_admin", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    previous = sys.modules.get(spec.name)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        if previous is None:
            del sys.modules[spec.name]
        else:
            sys.modules[spec.name] = previous
    return module


admin = _load_admin()


@pytest.fixture(autouse=True)
def _no_sessions() -> Any:
    """Every test starts with nobody signed in."""
    admin.SESSIONS.clear()
    yield
    admin.SESSIONS.clear()


# -- the addresses -------------------------------------------------------------


@pytest.mark.parametrize("collection", admin.COLLECTIONS)
def test_the_addresses_are_the_ones_the_integration_builds(collection: str) -> None:
    """The four strings are spelled again here, and this is what keeps them true.

    `store/admin.py` deliberately does not import `ha_adapter` -- that is what
    lets it run in the deployment repository, where `ha_adapter` is not -- so the
    paths are written out a second time. A copy nobody checks is a copy that
    drifts, so the equality is asserted rather than the spelling reviewed.
    """
    base = "https://store.example"
    assert admin.records_path(base, collection) == store_api.records_path(
        base, collection
    )
    assert admin.auth_path(base, collection) == store_api.auth_path(base, collection)
    assert admin.record_path(base, collection, "abc123") == store_api.record_path(
        base, collection, "abc123"
    )


def test_the_admin_table_is_the_one_pocketbase_uses() -> None:
    # Superusers are an auth collection like any other in 0.40, which is why the
    # login is the same call a publisher's is, against a different name.
    assert admin.SUPERUSERS == "_superusers"
    assert admin.auth_path(
        "https://store.example", admin.SUPERUSERS
    ) == store_api.auth_path("https://store.example", admin.SUPERUSERS)


def test_a_trailing_slash_is_not_a_second_store() -> None:
    assert admin.records_path(
        "https://store.example/", "modules"
    ) == admin.records_path("https://store.example", "modules")


def test_the_default_store_is_the_one_the_integration_ships() -> None:
    """The address is mirrored, and the mirror is checked.

    `DEFAULT_URL` in `custom_components/open_house/store.py` is what a build of
    Open House talks to; this is what the panel opens on. Two copies of one
    address is exactly the kind of thing that goes stale in silence -- a panel
    pointed at the old Store looks completely normal -- so the constant is read
    out of that file and compared.

    Read with a regular expression rather than imported: importing the module
    needs Home Assistant stubbed into `sys.modules`, and this is one string.
    """
    source = (ROOT / "custom_components" / "open_house" / "store.py").read_text()
    found = re.search(r'^DEFAULT_URL = "([^"]+)"', source, re.MULTILINE)
    assert found is not None, "store.py no longer states DEFAULT_URL as a literal"
    assert found.group(1) == admin.DEFAULT_URL


def test_only_a_loopback_store_is_quiet_about_being_live() -> None:
    assert admin.is_local("http://127.0.0.1:8090")
    assert admin.is_local("http://localhost:8090")
    assert not admin.is_local("https://openhouse-store.leonkraim.de")


def test_the_interface_it_binds_has_to_be_this_machine() -> None:
    # `0.0.0.0` is the name a person reaches for when they mean to be helpful,
    # and it is every interface rather than this one -- so it is not loopback
    # and `--allow-network` is what it takes to serve there.
    assert admin.is_loopback_host("127.0.0.1")
    assert admin.is_loopback_host("localhost")
    assert not admin.is_loopback_host("0.0.0.0")
    assert not admin.is_loopback_host("192.168.31.40")


# -- what a person typed, on its way into markup -------------------------------


def test_a_module_called_script_is_not_a_script() -> None:
    # The one bug this file exists to not have. A Store row is text somebody
    # else wrote, and a panel that drew it raw would be a cross-site script
    # served from a page holding an admin token.
    assert "<script>" not in admin.esc("<script>alert(1)</script>")
    assert "&lt;script&gt;" in admin.esc("<script>alert(1)</script>")


def test_a_record_title_is_escaped_when_it_is_drawn() -> None:
    row = {"id": "x", "title": "<img src=x onerror=alert(1)>"}
    drawn = admin.table(["Record"], [[admin.esc(admin.record_title("modules", row))]])
    assert "<img" not in drawn
    assert "&lt;img" in drawn


def test_an_address_with_a_quote_cannot_break_out_of_the_header() -> None:
    drawn = admin.page(
        "t", "<p>body</p>", store_url='https://x/"><script>bad()</script>'
    )
    assert "<script>" not in drawn


def test_the_page_says_which_store_it_is_on_and_how_loudly() -> None:
    local = admin.page("t", "", store_url="http://127.0.0.1:8090")
    live = admin.page("t", "", store_url="https://openhouse-store.leonkraim.de")
    assert ">local<" in local
    assert ">live<" in live


# -- the client, against a Store that answers from a script --------------------


class _Reply:
    """One canned answer, in the shape `admin.PocketBase.call` reads it."""

    def __init__(self, status: int, body: object) -> None:
        self.status = status
        self._body = json.dumps(body).encode()

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> _Reply:
        return self

    def __exit__(self, *_: object) -> bool:
        return False


def _answering(*replies: tuple[int, object]) -> tuple[Any, list[Any]]:
    """An opener that hands back the given answers in order, and records requests."""
    seen: list[Any] = []
    remaining = list(replies)

    def open_(request: Any, timeout: float | None = None) -> _Reply:
        seen.append(request)
        status, body = remaining.pop(0) if remaining else (200, {})
        if status >= 400:
            raise urllib.error.HTTPError(
                request.full_url,
                status,
                "refused",
                {},
                io.BytesIO(json.dumps(body).encode()),
            )
        return _Reply(status, body)

    return open_, seen


def test_a_login_reads_the_token_out_of_the_stores_own_answer() -> None:
    open_, seen = _answering((200, {"token": "tok123", "record": {"id": "abc"}}))
    client = admin.PocketBase("https://store.example", opener=open_)
    assert client.authenticate("me@example.com", "secret") == "tok123"
    request = seen[0]
    assert request.method == "POST"
    assert (
        request.full_url
        == "https://store.example/api/collections/_superusers/auth-with-password"
    )
    assert json.loads(request.data) == {
        "identity": "me@example.com",
        "password": "secret",
    }


def test_a_refused_login_carries_the_stores_own_sentence() -> None:
    # What a person gets for a wrong password. PocketBase's words and not ours,
    # because the Store is the one that knows which of the two was wrong.
    open_, _ = _answering((400, {"message": "Failed to authenticate."}))
    with pytest.raises(admin.StoreError) as refusal:
        admin.PocketBase("https://store.example", opener=open_).authenticate(
            "a@b.c", "nope"
        )
    assert refusal.value.message == "Failed to authenticate."
    assert refusal.value.status == 400


def test_a_store_that_does_not_answer_says_so_in_words() -> None:
    def broken(request: Any, timeout: float | None = None) -> Any:
        raise urllib.error.URLError("name or service not known")

    with pytest.raises(admin.StoreError) as refusal:
        admin.PocketBase("https://store.example", opener=broken).health()
    assert "could not be reached" in refusal.value.message


def test_a_malformed_body_is_a_refusal_rather_than_a_crash() -> None:
    # A proxy in front of the Store answers HTML, and a panel that died on
    # `json.loads` would report a Store problem as a bug in itself.
    class _Html:
        status = 200

        def read(self) -> bytes:
            return b"<html>nope</html>"

        def __enter__(self) -> _Html:
            return self

        def __exit__(self, *_: object) -> bool:
            return False

    client = admin.PocketBase("https://store.example", opener=lambda *a, **k: _Html())
    assert client.health() == "the Store answered"


def test_a_count_comes_out_of_the_listing_envelope() -> None:
    open_, seen = _answering((200, {"items": [], "totalItems": 128, "totalPages": 6}))
    assert (
        admin.PocketBase("https://store.example", opener=open_).count("modules") == 128
    )
    # Asked for with `perPage=1`: the number is in the envelope, and fetching the
    # rows to count them is a dashboard that slows down as the Store grows.
    assert "perPage=1" in seen[0].full_url


def test_an_edit_is_a_patch_to_that_records_own_address() -> None:
    open_, seen = _answering((200, {"id": "m1", "title": "New"}))
    client = admin.PocketBase("https://store.example", token="tok", opener=open_)
    client.update("modules", "m1", {"title": "New"})
    request = seen[0]
    assert request.method == "PATCH"
    assert (
        request.full_url == "https://store.example/api/collections/modules/records/m1"
    )
    assert request.get_header("Authorization") == "tok"


def test_a_delete_is_a_delete_of_that_address() -> None:
    open_, seen = _answering((204, {}))
    admin.PocketBase("https://store.example", token="tok", opener=open_).delete(
        "modules", "m1"
    )
    assert seen[0].method == "DELETE"
    assert seen[0].full_url.endswith("/api/collections/modules/records/m1")


def test_a_store_with_no_log_is_not_an_error() -> None:
    # Logging is a Store setting, and a dashboard that failed because a Store
    # keeps no log would be a dashboard about the wrong thing.
    open_, _ = _answering((404, {"message": "Not found"}))
    assert admin.PocketBase("https://store.example", opener=open_).logs() == []


def test_a_raw_edit_drops_what_the_store_decides_for_itself() -> None:
    changes = admin.raw_changes(
        {
            "id": "m1",
            "collectionId": "pbc_1",
            "collectionName": "modules",
            "created": "2026-01-01 00:00:00.000Z",
            "updated": "2026-01-01 00:00:00.000Z",
            "title": "Kept",
            "document": {"a": 1},
        }
    )
    assert changes == {"title": "Kept", "document": {"a": 1}}


def test_a_search_term_with_a_quote_cannot_end_the_filter_early() -> None:
    built = admin._search_filter("modules", "it's")
    assert built == "(title ~ 'it\\'s' || summary ~ 'it\\'s' || slug ~ 'it\\'s')"
    assert admin._search_filter("modules", "   ") == ""


def test_a_delete_says_what_goes_with_it() -> None:
    publisher = admin._delete_warning("publishers")
    module = admin._delete_warning("modules")
    assert "everything they published" in publisher
    assert "ratings, comments and installs" in module
    # A rating is a row and nothing hangs off it, so there is nothing to warn of.
    assert admin._delete_warning("ratings") == ""


# -- the panel, driven through an in-memory connection -------------------------


class _Connection:
    """The two file objects a handler reads and writes, and nowhere to send."""

    def __init__(self, request: bytes) -> None:
        self.reader = io.BytesIO(request)
        self.writer = io.BytesIO()

    def makefile(self, mode: str, *_: object) -> Any:
        return self.reader if "r" in mode else self.writer

    def sendall(self, data: bytes) -> None:
        self.writer.write(data)

    def close(self) -> None:
        pass


class _Quiet(admin.Handler):
    """The handler with its request log turned off, so the test output is the test."""

    def log_message(self, fmt: str, *args: Any) -> None:
        pass


def _request(method: str, path: str, *, cookies: str = "", body: str = "") -> bytes:
    payload = body.encode()
    lines = [
        f"{method} {path} HTTP/1.1",
        "Host: 127.0.0.1:8765",
        "Connection: close",
    ]
    if cookies:
        lines.append(f"Cookie: {cookies}")
    if payload:
        lines.append("Content-Type: application/x-www-form-urlencoded")
        lines.append(f"Content-Length: {len(payload)}")
    head = ("\r\n".join(lines) + "\r\n\r\n").encode()
    return head + payload


def _exchange(request: bytes) -> str:
    connection = _Connection(request)
    _Quiet(connection, ("127.0.0.1", 54321), types.SimpleNamespace())
    return connection.writer.getvalue().decode("utf-8", "replace")


def _sign_in() -> str:
    """A live session in the module's table, and the cookie that reaches it.

    `seen` is *now*: a session that has been idle past its window is pruned on
    the way past, so a fixed timestamp here would be a session the panel
    correctly refuses -- which is the behaviour `test_a_cookie_nobody_minted`
    checks, and not this.
    """
    admin.SESSIONS["sid-1"] = admin.Session(
        url="https://store.example",
        email="me@example.com",
        token="tok",
        csrf="csrf-token",
        seen=time.time(),
    )
    return f"{admin.COOKIE}=sid-1"


def test_the_login_page_is_the_one_page_that_needs_no_session() -> None:
    reply = _exchange(_request("GET", "/login"))
    assert "200 OK" in reply
    assert admin.DEFAULT_URL in reply


def test_a_page_with_no_session_sends_you_to_the_login() -> None:
    reply = _exchange(_request("GET", "/"))
    assert "303" in reply
    assert "Location: /login" in reply


def test_a_page_with_a_cookie_nobody_minted_sends_you_to_the_login() -> None:
    reply = _exchange(_request("GET", "/", cookies="oh_admin=made-up"))
    assert "303" in reply
    assert "Location: /login" in reply


def test_a_write_without_the_csrf_token_changes_nothing() -> None:
    # The rule the whole panel leans on: a page the browser is showing is not on
    # its own permission to write. Without the token this never reaches a route.
    cookie = _sign_in()
    reply = _exchange(
        _request(
            "POST",
            "/record/modules/m1/delete",
            cookies=cookie,
            body="confirm=m1",
        )
    )
    assert "403" in reply
    assert "did not come from this panel" in reply


def test_a_write_with_the_wrong_csrf_token_is_refused_too() -> None:
    cookie = _sign_in()
    reply = _exchange(
        _request(
            "POST",
            "/record/modules/m1/delete",
            cookies=cookie,
            body="csrf=not-the-token&confirm=m1",
        )
    )
    assert "403" in reply


def test_a_page_that_is_not_a_page_is_a_404_and_not_a_traceback() -> None:
    cookie = _sign_in()
    reply = _exchange(_request("GET", "/nothing-here", cookies=cookie))
    assert "404" in reply
    assert "No such page" in reply


def test_a_collection_outside_the_five_is_not_a_collection() -> None:
    # The one route that takes a name out of the URL. `_superusers` is the
    # Store's own account table, and a panel that could be pointed at it would
    # be a panel that could delete its own way in.
    cookie = _sign_in()
    reply = _exchange(_request("GET", "/collection/_superusers", cookies=cookie))
    assert "404" in reply


def test_a_cascade_refused_with_a_four_hundred_is_read_as_having_happened() -> None:
    """PocketBase 0.40 answers `400` for a cascaded delete that went through.

    A module's ratings, comments and installs go first and the backend then trips
    over what it has already removed in its own after-delete hook. The record is
    gone and the status says otherwise -- and a record with children on it is the
    one somebody opens this panel to remove -- so the refusal is checked against
    the Store rather than shown.
    """
    open_, seen = _answering(
        (400, {"message": "Failed to delete record."}),
        (404, {"message": "The requested resource wasn't found."}),
    )
    admin.PocketBase("https://store.example", token="tok", opener=open_).delete(
        "modules", "m1"
    )
    assert [request.method for request in seen] == ["DELETE", "GET"]


def test_a_delete_that_really_failed_keeps_its_refusal() -> None:
    # The check above must not turn every refusal into a success: the record is
    # still there, so the panel says so.
    open_, _ = _answering(
        (400, {"message": "Failed to delete record."}), (200, {"id": "m1"})
    )
    with pytest.raises(admin.StoreError):
        admin.PocketBase("https://store.example", token="tok", opener=open_).delete(
            "modules", "m1"
        )


def test_a_delete_that_went_through_asks_nothing_further() -> None:
    open_, seen = _answering((204, {}))
    admin.PocketBase("https://store.example", token="tok", opener=open_).delete(
        "modules", "m1"
    )
    assert [request.method for request in seen] == ["DELETE"]


def test_a_store_that_cannot_be_asked_has_not_said_the_record_is_gone() -> None:
    # "I could not tell" is not "it is gone": a read that fails for any reason
    # other than a 404 leaves the delete refused rather than reported as done.
    open_, _ = _answering((400, {"message": "Failed to delete record."}), (500, {}))
    client = admin.PocketBase("https://store.example", token="tok", opener=open_)
    assert client.gone("modules", "m1") is False
