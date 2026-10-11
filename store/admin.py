"""A local panel for running the Store: see everything, fix anything.

    python store/admin.py
    python store/admin.py --url http://127.0.0.1:8090 --port 8765 --open

**Why this is a script and not a test.** It binds a port and talks to a Store
that is already running, and the repository's tests do neither: nothing in
`tests/` may reach the network, because a test that needs a server in the room
is a test that fails on the machine of somebody who has not started one. This
is the same standing as `store/check.py`, for the same reason.

**Why not just use PocketBase's own admin UI.** `/_/` is a general-purpose
record editor: it is on the deployment rather than on the machine you are
sitting at, it has never heard of a module definition, and it draws a record as
a form of forty fields. What is here knows the five collections, shows the
counts worth watching, and prints an edit as the sentence it is.

**Why it imports nothing from this repository.** No `ha_adapter`, no Home
Assistant, no third-party package -- only the standard library. That is what
lets this file be copied into the deployment repository
(`LeonKraim/open-house-backend`) and run there on a bare `python:3.12-alpine`,
which is exactly the thing `store/check.py` cannot do. The four address builders
below are therefore spelled again rather than imported, and
`tests/test_store_admin.py` asserts each one equals its
`ha_adapter.store_api` counterpart -- so the duplication is checked rather than
trusted.

**Why it is bound to loopback.** It holds an admin token: the Store's own
superuser, which can read and write every record in it. A panel like that on a
LAN address is a Store with no lock on it, so `--host` refuses anything that is
not a loopback address unless you say `--allow-network` on purpose.

**The login is the Store's own.** No second password to invent and none to
write down: the form asks for the admin account the Store was deployed with
(`PB_ADMIN_EMAIL` / `PB_ADMIN_PASSWORD`), authenticates against PocketBase, and
keeps the token in this process, keyed by a cookie. Nothing is written to disk,
and the password is never logged and never stored.
"""

from __future__ import annotations

import argparse
import html
import json
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

#: The Store this panel opens on. The same address the integration ships with
#: (`custom_components/open_house/store.DEFAULT_URL`), and `tests/test_store_admin.py`
#: fails if the two ever disagree -- so this is a mirror that cannot go stale.
#: A house running its own Store passes `--url http://127.0.0.1:8090`, or types
#: the address into the login form, which is what the field there is for.
DEFAULT_URL = "https://openhouse-store.leonkraim.de"

#: The port this binds. Nothing else here uses it: the Store is 8090, Home
#: Assistant 8123, Node-RED 1880, its broker 1883, the panel's dev server 5173.
DEFAULT_PORT = 8765

#: The five collections the Store is. Typed here because a route that took a
#: collection name from the URL would otherwise be a route that could address
#: `_superusers`, which is the Store's own account table and not a thing this
#: panel manages.
COLLECTIONS = ("publishers", "modules", "ratings", "comments", "installs")

#: PocketBase's own admin table. Probed rather than assumed: a Store is asked
#: whether this collection answers before the login form offers it.
SUPERUSERS = "_superusers"

#: How long a signed-in session may sit idle before it is forgotten. The token it
#: holds is the Store's, and an admin token nobody is using is worth expiring.
SESSION_IDLE_SECONDS = 8 * 60 * 60

#: The cookie the session id rides in.
COOKIE = "oh_admin"


# -- The Store's addresses ----------------------------------------------------
#
# PocketBase's REST shape. The same four strings `ha_adapter.store_api` builds,
# and the test beside this file is what keeps them the same.


def _base(base: str) -> str:
    return base.rstrip("/")


def records_path(base: str, collection: str) -> str:
    return f"{_base(base)}/api/collections/{collection}/records"


def record_path(base: str, collection: str, record: str) -> str:
    return f"{records_path(base, collection)}/{urllib.parse.quote(str(record))}"


def auth_path(base: str, collection: str) -> str:
    return f"{_base(base)}/api/collections/{collection}/auth-with-password"


#: The addresses that mean "this machine". Spelled once, because two questions
#: are asked of them: is this Store on this machine, and is this panel about to
#: be served to the network.
LOOPBACK = ("127.0.0.1", "localhost", "::1", "")


def is_local(url: str) -> bool:
    """Whether this Store is one on this machine.

    The one mistake this panel could cause that typing cannot undo is a delete
    aimed at the wrong Store, so every page says which one it is on -- and says
    it loudly when the answer is "the live one".
    """
    return (urllib.parse.urlsplit(url).hostname or "").lower() in LOOPBACK


def is_loopback_host(host: str) -> bool:
    """Whether an interface to bind is this machine's own.

    A bare host and not a URL, so this is a separate question from `is_local`:
    `0.0.0.0` is a name for *every* interface rather than for this one, and it is
    exactly the value a person reaches for when they mean to be helpful.
    """
    return host.strip().lower() in LOOPBACK


# -- The client ---------------------------------------------------------------


class StoreError(Exception):
    """The Store answered, and the answer was no.

    Carries the Store's own sentence rather than a generic one, for the reason
    every refusal in this repository does: "Failed to authenticate." is a thing
    a person can act on and "400" is not.
    """

    def __init__(self, status: int, body: object) -> None:
        message = ""
        if isinstance(body, Mapping):
            message = str(body.get("message") or "")
        self.status = status
        self.body = body
        self.message = " ".join(message.split())[:300] or f"the Store answered {status}"
        super().__init__(self.message)


class PocketBase:
    """One Store, spoken to with a token that belongs to nobody until you log in.

    `opener` is a parameter and not a module-level call so the tests can hand in
    a function that answers from a script. Nothing else here is injectable: what
    is worth testing is that a given call becomes a given request and that a
    given answer is read correctly, and both of those are visible through this
    one seam.
    """

    def __init__(
        self,
        url: str,
        token: str = "",
        *,
        opener: Any = urllib.request.urlopen,
        timeout: float = 15.0,
    ) -> None:
        self.url = url
        self.token = token
        self.timeout = timeout
        self._open = opener

    # -- the wire ------------------------------------------------------------

    def call(
        self,
        method: str,
        path: str,
        body: object = None,
        *,
        params: Mapping[str, Any] | None = None,
    ) -> tuple[int, dict[str, Any]]:
        """One request, answered with its status and parsed body."""
        url = path if path.startswith("http") else f"{_base(self.url)}{path}"
        if params:
            query = urllib.parse.urlencode(
                {key: value for key, value in params.items() if value not in (None, "")}
            )
            if query:
                url = f"{url}{'&' if '?' in url else '?'}{query}"
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(url, data=data, method=method)
        request.add_header("Accept", "application/json")
        if data is not None:
            request.add_header("Content-Type", "application/json")
        if self.token:
            request.add_header("Authorization", self.token)
        try:
            with self._open(request, timeout=self.timeout) as reply:
                raw = reply.read()
                status = reply.status
        except urllib.error.HTTPError as error:
            raw = error.read()
            status = error.code
        except urllib.error.URLError as failure:
            raise StoreError(
                0, {"message": f"the Store could not be reached: {failure}"}
            ) from failure
        try:
            parsed = json.loads(raw or b"{}")
        except ValueError:
            parsed = {}
        body_out = parsed if isinstance(parsed, dict) else {"data": parsed}
        if status >= 400:
            raise StoreError(status, body_out)
        return status, body_out

    # -- what the panel asks of it -------------------------------------------

    def authenticate(self, email: str, password: str) -> str:
        """The Store's admin token for one address, or a refusal saying why not.

        Superusers are records in `_superusers`, so this is an ordinary
        record-auth call -- the same one `store/check.py` makes for a publisher,
        against a different collection.
        """
        _, body = self.call(
            "POST",
            auth_path(self.url, SUPERUSERS),
            {"identity": email, "password": password},
        )
        return str(body.get("token") or "")

    def health(self) -> str:
        _, body = self.call("GET", "/api/health")
        return str(body.get("message") or "the Store answered")

    def count(self, collection: str, search: str = "") -> int:
        """How many records there are, read from the listing's own total.

        `perPage=1` because the number is in the envelope: a count asked for by
        fetching every row is a count that gets slower as the Store grows, and
        the one that gets slow is the one a dashboard asks for first.
        """
        params: dict[str, Any] = {"perPage": 1, "page": 1}
        pattern = _search_filter(collection, search)
        if pattern:
            params["filter"] = pattern
        _, body = self.call("GET", records_path(self.url, collection), params=params)
        return int(body.get("totalItems") or 0)

    def page(
        self,
        collection: str,
        *,
        search: str = "",
        page: int = 1,
        per_page: int = 25,
        sort: str = "-created",
    ) -> tuple[list[dict[str, Any]], int, int]:
        """One page of a collection: the rows, the page, and how many there are."""
        params: dict[str, Any] = {
            "perPage": per_page,
            "page": max(1, page),
            "sort": sort,
            "expand": _expands(collection),
        }
        pattern = _search_filter(collection, search)
        if pattern:
            params["filter"] = pattern
        _, body = self.call("GET", records_path(self.url, collection), params=params)
        items = body.get("items")
        rows = (
            [row for row in items if isinstance(row, dict)]
            if isinstance(items, list)
            else []
        )
        return rows, int(body.get("page") or 1), int(body.get("totalPages") or 1)

    def get(self, collection: str, record: str) -> dict[str, Any]:
        _, body = self.call(
            "GET",
            record_path(self.url, collection, record),
            params={"expand": _expands(collection)},
        )
        return body

    def update(
        self, collection: str, record: str, changes: Mapping[str, Any]
    ) -> dict[str, Any]:
        _, body = self.call(
            "PATCH", record_path(self.url, collection, record), dict(changes)
        )
        return body

    def delete(self, collection: str, record: str) -> None:
        """Delete one record, and believe the Store over the status code.

        PocketBase 0.40 cascades a module's ratings, comments and installs away
        and *then* trips over what it has already removed in its own after-delete
        hook, answering a `400` -- "make sure that the record is not part of a
        required relation reference" -- for a delete that happened. A record with
        children on it is exactly the one somebody opens this panel to remove, so
        a refusal is followed by a read: a `404` is the answer that means done,
        and anything else is the refusal it looked like.
        """
        try:
            self.call("DELETE", record_path(self.url, collection, record))
        except StoreError:
            if self.gone(collection, record):
                return
            raise

    def gone(self, collection: str, record: str) -> bool:
        """Whether the Store no longer holds this record.

        Only a `404` counts. A Store that cannot be reached, or that refuses the
        read, has not said the record is gone -- and a panel that took silence
        for an answer would report a delete it never confirmed.
        """
        try:
            self.get(collection, record)
        except StoreError as refusal:
            return refusal.status == 404
        return False

    def logs(self, per_page: int = 25) -> list[dict[str, Any]]:
        """The Store's own log, or an empty list where it does not keep one.

        A Store built without logging answers a 404 here, and a dashboard that
        failed because of it would be a dashboard about the wrong thing.
        """
        try:
            _, body = self.call(
                "GET", "/api/logs", params={"perPage": per_page, "sort": "-created"}
            )
        except StoreError:
            return []
        items = body.get("items")
        return (
            [row for row in items if isinstance(row, dict)]
            if isinstance(items, list)
            else []
        )


def _search_filter(collection: str, search: str) -> str:
    """The Store's own filter syntax for a search, per collection.

    A module is searched in its title and its summary because those are the two
    things a person reads on a Store row; everything else is searched in the one
    field that names it.
    """
    term = " ".join(str(search or "").split())
    if not term:
        return ""
    quoted = term.replace("'", "\\'")
    if collection == "modules":
        return f"(title ~ '{quoted}' || summary ~ '{quoted}' || slug ~ '{quoted}')"
    if collection == "publishers":
        return f"(name ~ '{quoted}')"
    if collection == "comments":
        return f"(body ~ '{quoted}')"
    return ""


def _expands(collection: str) -> str:
    """Which relations to expand, so a row reads as names rather than as ids."""
    if collection in ("modules", "comments"):
        return "publisher"
    if collection in ("ratings", "installs"):
        return "publisher,module"
    return ""


# -- What a record is, to a person --------------------------------------------


@dataclass(frozen=True)
class Field:
    """One thing on a record a person may change, and how to draw it."""

    name: str
    label: str
    kind: str = "text"
    options: tuple[str, ...] = ()
    help: str = ""


#: The fields the panel offers as a form. Anything not here is still reachable
#: through the raw editor on the same page, which is the escape hatch for the
#: one thing a form did not anticipate.
EDITABLE: Mapping[str, tuple[Field, ...]] = {
    "publishers": (
        Field(
            "name",
            "Name",
            help=(
                "The publisher's name is also the address of everything they "
                "published, and Open House matches a house's modules on it. "
                "Renaming is for a typo, not for a new identity."
            ),
        ),
    ),
    "modules": (
        Field("slug", "Slug", help="What a house calls the module it installed."),
        Field("title", "Title"),
        Field(
            "summary",
            "Summary",
            "textarea",
            help="The blurb on the Store row. 400 characters, and the Store is what enforces it.",
        ),
        Field("version", "Version"),
        Field("tier", "Tier", "select", ("official", "verified", "community")),
        Field(
            "review", "Review", "textarea", help="Shown when the Store has reviewed it."
        ),
    ),
    "comments": (Field("body", "Comment", "textarea"),),
    "ratings": (Field("stars", "Stars", "number", help="One to five whole stars.")),
    "installs": (),
}

#: Fields PocketBase owns. Dropped from a raw edit before it is sent: a body
#: carrying `created` or `collectionId` is a body the Store refuses for saying
#: something it decides itself.
SYSTEM_FIELDS = frozenset(
    {"id", "collectionId", "collectionName", "created", "updated", "expand"}
)

TIER_NAMES = {"official": "Official", "verified": "Verified", "community": "Community"}

LOG_LEVELS = {0: "debug", 4: "info", 8: "warning", 16: "error"}


def raw_changes(parsed: Mapping[str, Any]) -> dict[str, Any]:
    """A pasted record with the fields PocketBase owns taken out.

    The raw editor hands a person the whole record so that nothing is hidden,
    and hands the Store back only what a person may decide: `id`, the collection
    it belongs to and the two timestamps are the Store's to say, and a body
    carrying them is a body it refuses. Dropping them here rather than asking a
    person to is what makes "copy the record, change one line, paste it back"
    work at all.
    """
    return {key: value for key, value in parsed.items() if key not in SYSTEM_FIELDS}


def record_title(collection: str, record: Mapping[str, Any]) -> str:
    """One record named the way a person would name it."""
    if collection == "publishers":
        return str(record.get("name") or record.get("id") or "")
    if collection == "modules":
        return str(record.get("title") or record.get("slug") or record.get("id") or "")
    if collection == "comments":
        body = " ".join(str(record.get("body") or "").split())
        return body[:60] + ("…" if len(body) > 60 else "")
    if collection == "ratings":
        return f"{record.get('stars')} stars"
    return str(record.get("id") or "")


def related_name(record: Mapping[str, Any], field: str) -> str:
    """The name behind a relation, out of whatever PocketBase expanded it to.

    A listing that showed `q10yjj5bzdo062q` where a publisher's name belongs is
    a listing nobody can read; when the expansion is missing the id is still
    better than an empty cell, so it is what is shown.
    """
    expand = record.get("expand")
    if isinstance(expand, Mapping):
        related = expand.get(field)
        if isinstance(related, Mapping):
            return str(
                related.get("name") or related.get("title") or related.get("id") or ""
            )
    return str(record.get(field) or "")


# -- Rendering ----------------------------------------------------------------


def esc(value: object) -> str:
    """Everything a person typed, on its way into markup.

    One function rather than an `html.escape` at each of the thirty places a
    value is drawn, because the one that gets forgotten is the one that matters:
    a module titled `<script>` is a Store with a cross-site script in it, and the
    test beside this file pins the escaping rather than trusting the review.
    """
    return html.escape(str(value if value is not None else ""), quote=True)


STYLE = """
:root { color-scheme: light dark; --line: #d6d6d6; --muted: #6b6b6b; --bg: #fff; --ink: #1a1a1a; }
@media (prefers-color-scheme: dark) {
  :root { --line: #3a3a3a; --muted: #9a9a9a; --bg: #16181c; --ink: #e8e8e8; }
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--ink);
  font: 14px/1.5 ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif; }
header { display: flex; align-items: center; gap: 12px; flex-wrap: wrap;
  padding: 10px 20px; border-bottom: 1px solid var(--line); position: sticky; top: 0;
  background: var(--bg); }
header h1 { font-size: 15px; margin: 0; letter-spacing: .02em; }
header .grow { flex: 1 1 auto; }
main { padding: 20px; max-width: 1100px; }
h2 { font-size: 15px; margin: 28px 0 8px; }
h2:first-child { margin-top: 0; }
a { color: inherit; }
nav a { margin-right: 14px; text-decoration: none; border-bottom: 1px solid transparent; }
nav a:hover { border-bottom-color: currentColor; }
table { border-collapse: collapse; width: 100%; }
th, td { text-align: left; padding: 6px 10px 6px 0; border-bottom: 1px solid var(--line);
  vertical-align: top; }
th { font-weight: 600; color: var(--muted); font-size: 12px; text-transform: uppercase;
  letter-spacing: .04em; }
.muted { color: var(--muted); }
.small { font-size: 12px; }
.chip { display: inline-block; padding: 1px 8px; border: 1px solid var(--line);
  border-radius: 999px; font-size: 11px; text-transform: uppercase; letter-spacing: .06em; }
.chip.live { background: #b3261e; border-color: #b3261e; color: #fff; font-weight: 700; }
.chip.ok { border-color: #2e7d32; color: #2e7d32; }
.banner { margin: 0 0 16px; padding: 10px 12px; border-radius: 6px;
  border: 1px solid var(--line); }
.banner.error { border-color: #b3261e; color: #b3261e; }
.banner.ok { border-color: #2e7d32; color: #2e7d32; }
form { margin: 0; }
label { display: block; margin: 10px 0 4px; font-size: 12px; color: var(--muted);
  text-transform: uppercase; letter-spacing: .04em; }
input, textarea, select { width: 100%; max-width: 640px; padding: 6px 8px; font: inherit;
  color: inherit; background: transparent; border: 1px solid var(--line); border-radius: 4px; }
textarea { min-height: 90px; font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
  font-size: 12px; }
button { margin-top: 12px; padding: 7px 14px; font: inherit; color: inherit;
  background: transparent; border: 1px solid var(--line); border-radius: 4px; cursor: pointer; }
button:hover { border-color: var(--ink); }
button.danger { border-color: #b3261e; color: #b3261e; }
.row { display: flex; gap: 10px; align-items: baseline; flex-wrap: wrap; }
.grow { flex: 1 1 auto; }
.help { color: var(--muted); font-size: 12px; margin: 4px 0 0; max-width: 640px; }
pre { background: rgba(127,127,127,.1); padding: 10px; border-radius: 6px; overflow: auto;
  font-size: 12px; }
.count { font-variant-numeric: tabular-nums; }
"""


def page(
    title: str,
    body: str,
    *,
    store_url: str = "",
    who: str = "",
    notice: str = "",
    error: str = "",
) -> str:
    """One screen: the header every page carries, and the body it was given.

    The header is where the Store's address lives, on every page, because the
    address is the thing a person is most likely to be wrong about -- a local
    panel and a live Store look identical otherwise, and one of the two has no
    undo.
    """
    if store_url:
        chip = (
            '<span class="chip ok">local</span>'
            if is_local(store_url)
            else '<span class="chip live">live</span>'
        )
        where = f"{chip} <span class='muted small'>{esc(store_url)}</span>"
    else:
        where = ""
    signed_in = (
        f'<span class="muted small">{esc(who)} &middot; '
        f'<a href="/logout">sign out</a></span>'
        if who
        else ""
    )
    banners = ""
    if error:
        banners += f'<div class="banner error">{esc(error)}</div>'
    if notice:
        banners += f'<div class="banner ok">{esc(notice)}</div>'
    return (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<title>{esc(title)} &middot; Open House Store</title>"
        f"<style>{STYLE}</style></head><body>"
        "<header>"
        "<h1>Open House Store</h1>"
        f"<nav class='small'><a href='/'>Overview</a>"
        + "".join(f"<a href='/collection/{name}'>{name}</a>" for name in COLLECTIONS)
        + "<a href='/logs'>Logs</a></nav>"
        f"<span class='grow'></span>{where} {signed_in}</header>"
        f"<main>{banners}{body}</main></body></html>"
    )


def table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    """A table of already-escaped cells, or a line saying there are none."""
    if not rows:
        return '<p class="muted">Nothing here.</p>'
    head = "".join(f"<th>{esc(header)}</th>" for header in headers)
    body = "".join(
        "<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>" for row in rows
    )
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


# -- Sessions -----------------------------------------------------------------


@dataclass
class Session:
    """One signed-in browser: a Store, an admin token, and a CSRF token.

    The Store's token lives here and never in the page. The cookie carries an id
    this process minted, so a stolen cookie is a session that expires rather
    than a bearer token that does not -- and no token appears in a URL, a log
    line or a form field.
    """

    url: str
    email: str
    token: str
    csrf: str
    seen: float


SESSIONS: dict[str, Session] = {}


def new_session(url: str, email: str, token: str) -> tuple[str, Session]:
    session = Session(
        url=url,
        email=email,
        token=token,
        csrf=secrets.token_urlsafe(24),
        seen=time.time(),
    )
    sid = secrets.token_urlsafe(32)
    SESSIONS[sid] = session
    return sid, session


def lookup(sid: str) -> Session | None:
    """The live session for one cookie, pruning the ones nobody is using."""
    now = time.time()
    for key, held in list(SESSIONS.items()):
        if now - held.seen > SESSION_IDLE_SECONDS:
            del SESSIONS[key]
    session = SESSIONS.get(sid)
    if session is not None:
        session.seen = now
    return session


# -- The server ---------------------------------------------------------------


class Handler(BaseHTTPRequestHandler):
    """Every route of the panel, and the two rules that guard them.

    **A session is required for everything except the login page.** So is the
    CSRF token on every POST. Both are checked here, in one place, before a route
    is picked -- so a route added later is guarded by having been added, rather
    than by somebody remembering to guard it.
    """

    server_version = "OpenHouseStoreAdmin"
    protocol_version = "HTTP/1.1"

    #: The Store the login form opens on, set from `--url` in `main`: the flag is
    #: how a person points this at their own Store, and the field on the form is
    #: how they point it somewhere else for one session.
    login_url = DEFAULT_URL

    # -- plumbing ------------------------------------------------------------

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stderr.write("admin: " + (fmt % args) + "\n")

    def _send(
        self,
        status: int,
        text: str,
        content_type: str = "text/html; charset=utf-8",
        extra: Mapping[str, str] | None = None,
    ) -> None:
        payload = text.encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(payload)

    def _redirect(self, location: str, extra: Mapping[str, str] | None = None) -> None:
        payload = b""
        self.send_response(303)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(payload)

    def _form(self) -> dict[str, str]:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length).decode("utf-8", "replace") if length else ""
        fields = urllib.parse.parse_qs(raw, keep_blank_values=True)
        return {key: values[0] for key, values in fields.items()}

    def _cookie(self) -> str:
        header = self.headers.get("Cookie") or ""
        for piece in header.split(";"):
            name, _, value = piece.strip().partition("=")
            if name == COOKIE:
                return value
        return ""

    def _session(self) -> Session | None:
        return lookup(self._cookie())

    # -- routes --------------------------------------------------------------

    def do_GET(self) -> None:
        path, _, query = self.path.partition("?")
        parts = [piece for piece in path.split("/") if piece]
        params = {
            key: values[0] for key, values in urllib.parse.parse_qs(query).items()
        }
        if parts == ["login"]:
            self._login_page()
            return
        session = self._session()
        if session is None:
            self._redirect("/login")
            return
        store = PocketBase(session.url, session.token)
        try:
            self._route_get(store, session, parts, params)
        except StoreError as refusal:
            self._send(
                refusal.status or 502,
                page(
                    "Refused",
                    f"<p>{esc(refusal.message)}</p>",
                    store_url=session.url,
                    who=session.email,
                    error=refusal.message,
                ),
            )

    def do_POST(self) -> None:
        path, _, _ = self.path.partition("?")
        parts = [piece for piece in path.split("/") if piece]
        fields = self._form()
        if parts == ["login"]:
            self._log_in(fields)
            return
        session = self._session()
        if session is None:
            self._redirect("/login")
            return
        if not secrets.compare_digest(fields.get("csrf", ""), session.csrf):
            self._send(
                403,
                page(
                    "Refused",
                    "<p>That form did not come from this panel.</p>",
                    store_url=session.url,
                    who=session.email,
                ),
            )
            return
        store = PocketBase(session.url, session.token)
        try:
            self._route_post(store, session, parts, fields)
        except StoreError as refusal:
            self._send(
                refusal.status or 502,
                page(
                    "Refused",
                    f"<p>{esc(refusal.message)}</p>",
                    store_url=session.url,
                    who=session.email,
                    error=refusal.message,
                ),
            )

    # -- the login -----------------------------------------------------------

    def _login_page(self, error: str = "", url: str = "", email: str = "") -> None:
        target = url or self.login_url
        body = f"""
<h2>Sign in to the Store</h2>
<p class="muted">This panel holds the Store's admin account for as long as you are
signed in. Nothing is written to disk.</p>
<form method="post" action="/login">
  <label for="url">Store</label>
  <input id="url" name="url" value="{esc(target)}" autocomplete="off">
  <label for="email">Admin email</label>
  <input id="email" name="email" value="{esc(email)}" autocomplete="username">
  <label for="password">Admin password</label>
  <input id="password" name="password" type="password" autocomplete="current-password">
  <button type="submit">Sign in</button>
</form>"""
        self._send(200, page("Sign in", body, error=error))

    def _log_in(self, fields: Mapping[str, str]) -> None:
        url = (fields.get("url") or "").strip().rstrip("/")
        email = (fields.get("email") or "").strip()
        password = fields.get("password") or ""
        if not url or not email or not password:
            self._login_page(
                "Fill in all three: the Store, the admin email and the password.",
                url,
                email,
            )
            return
        try:
            token = PocketBase(url).authenticate(email, password)
        except StoreError as refusal:
            self._login_page(refusal.message, url, email)
            return
        if not token:
            self._login_page("The Store answered without a token.", url, email)
            return
        sid, _ = new_session(url, email, token)
        self._redirect(
            "/",
            {
                "Set-Cookie": f"{COOKIE}={sid}; HttpOnly; SameSite=Strict; Path=/",
            },
        )

    # -- what the pages are --------------------------------------------------

    def _route_get(
        self,
        store: PocketBase,
        session: Session,
        parts: Sequence[str],
        params: Mapping[str, str],
    ) -> None:
        if not parts:
            self._overview(store, session)
            return
        if parts == ["logout"]:
            SESSIONS.pop(self._cookie(), None)
            self._redirect("/login", {"Set-Cookie": f"{COOKIE}=; Path=/; Max-Age=0"})
            return
        if parts == ["logs"]:
            self._logs(store, session)
            return
        if len(parts) == 2 and parts[0] == "collection" and parts[1] in COLLECTIONS:
            self._listing(store, session, parts[1], params)
            return
        if len(parts) == 3 and parts[0] == "record" and parts[1] in COLLECTIONS:
            self._record(store, session, parts[1], parts[2], params)
            return
        self._send(
            404,
            page(
                "Not found",
                "<p>No such page.</p>",
                store_url=session.url,
                who=session.email,
            ),
        )

    def _route_post(
        self,
        store: PocketBase,
        session: Session,
        parts: Sequence[str],
        fields: Mapping[str, str],
    ) -> None:
        if len(parts) == 3 and parts[0] == "record" and parts[1] in COLLECTIONS:
            self._save(store, session, parts[1], parts[2], fields)
            return
        if len(parts) == 4 and parts[0] == "record" and parts[1] in COLLECTIONS:
            if parts[3] == "delete":
                self._delete(store, session, parts[1], parts[2], fields)
                return
            if parts[3] == "raw":
                self._save_raw(store, session, parts[1], parts[2], fields)
                return
        self._send(
            404,
            page(
                "Not found",
                "<p>No such action.</p>",
                store_url=session.url,
                who=session.email,
            ),
        )

    # -- one page each -------------------------------------------------------

    def _overview(self, store: PocketBase, session: Session) -> None:
        try:
            health = store.health()
        except StoreError as refusal:
            health = refusal.message
        counts = [(name, store.count(name)) for name in COLLECTIONS]
        rows = [
            [
                f"<a href='/collection/{esc(name)}'>{esc(name)}</a>",
                f"<span class='count'>{count}</span>",
                f"<a class='small' href='/collection/{esc(name)}'>browse</a>",
            ]
            for name, count in counts
        ]
        # Sorted on the timestamps rather than on the cells that carry them: the
        # drawn cell is markup, and ordering markup is ordering the tags before
        # the dates they wrap.
        latest: list[tuple[str, str, str, str]] = []
        for name, _ in counts:
            found, _, _ = store.page(name, per_page=5, sort="-created")
            for record in found:
                latest.append(
                    (
                        str(record.get("created") or ""),
                        name,
                        str(record.get("id") or ""),
                        record_title(name, record),
                    )
                )
        latest.sort(reverse=True)
        newest = [
            [
                f"<a href='/collection/{esc(name)}'>{esc(name)}</a>",
                f"<a href='/record/{esc(name)}/{esc(record_id)}'>{esc(title)}</a>",
                f"<span class='muted small'>{esc(_when(created))}</span>",
            ]
            for created, name, record_id, title in latest
        ]
        entries = [
            [
                f"<span class='muted small'>{esc(_when(entry.get('created')))}</span>",
                esc(LOG_LEVELS.get(entry.get("level"), entry.get("level"))),
                esc(" ".join(str(entry.get("message") or "").split())[:140]),
            ]
            for entry in store.logs(per_page=10)
        ]
        body = (
            f"<h2>Health</h2><p>{esc(health)}</p>"
            "<h2>Records</h2>"
            + table(["Collection", "Count", ""], rows)
            + "<h2>Newest</h2>"
            + table(["Collection", "Record", "Created"], newest[:15])
            + "<h2>Recent log</h2>"
            + (
                table(["When", "Level", "Message"], entries)
                if entries
                else '<p class="muted">The Store keeps no log, or has nothing to say.</p>'
            )
        )
        self._send(
            200, page("Overview", body, store_url=session.url, who=session.email)
        )

    def _listing(
        self,
        store: PocketBase,
        session: Session,
        collection: str,
        params: Mapping[str, str],
    ) -> None:
        search = params.get("q", "")
        try:
            page_number = int(params.get("page") or 1)
        except ValueError:
            page_number = 1
        found, number, pages = store.page(collection, search=search, page=page_number)
        total = store.count(collection, search)
        rows = [
            [
                f"<a href='/record/{esc(collection)}/{esc(record.get('id'))}'>"
                f"{esc(record_title(collection, record))}</a>",
                f"<span class='muted small'>{esc(_subtitle(collection, record))}</span>",
            ]
            for record in found
        ]
        pager = ""
        if pages > 1:
            previous = (
                f"<a href='/collection/{esc(collection)}?q={esc(search)}&page={number - 1}'>previous</a>"
                if number > 1
                else "<span class='muted'>previous</span>"
            )
            following = (
                f"<a href='/collection/{esc(collection)}?q={esc(search)}&page={number + 1}'>next</a>"
                if number < pages
                else "<span class='muted'>next</span>"
            )
            pager = f"<p class='small'>{previous} &middot; page {number} of {pages} &middot; {following}</p>"
        body = (
            f"<h2>{esc(collection)}</h2>"
            f"<form method='get' action='/collection/{esc(collection)}'>"
            f"<label for='q'>Search</label>"
            f"<input id='q' name='q' value='{esc(search)}' autocomplete='off'>"
            "<button type='submit'>Search</button></form>"
            f"<p class='muted small'>{total} record{'s' if total != 1 else ''}.</p>"
            + table(["Record", ""], rows)
            + pager
        )
        self._send(
            200, page(collection, body, store_url=session.url, who=session.email)
        )

    def _record(
        self,
        store: PocketBase,
        session: Session,
        collection: str,
        record_id: str,
        params: Mapping[str, str],
    ) -> None:
        record = store.get(collection, record_id)
        fields = EDITABLE.get(collection, ())
        inputs = "".join(_field_input(field, record) for field in fields)
        editable = (
            f"<form method='post' action='/record/{esc(collection)}/{esc(record_id)}'>"
            f"<input type='hidden' name='csrf' value='{esc(session.csrf)}'>"
            f"{inputs}<button type='submit'>Save</button></form>"
            if fields
            else '<p class="muted">Nothing on a record here is editable as a field; use the raw editor below.</p>'
        )
        raw = json.dumps(
            {key: value for key, value in record.items() if key not in ("expand",)},
            indent=2,
            sort_keys=True,
        )
        document = record.get("document")
        shown_document = (
            "<details><summary>module definition</summary>"
            f"<pre>{esc(json.dumps(document, indent=2, sort_keys=True))}</pre></details>"
            if isinstance(document, Mapping)
            else ""
        )
        body = (
            f"<h2>{esc(record_title(collection, record))}</h2>"
            f"<p class='muted small'>{esc(collection)} &middot; {esc(record_id)}"
            f" &middot; created {esc(_when(record.get('created')))}"
            f" &middot; updated {esc(_when(record.get('updated')))}</p>"
            + _relations(collection, record)
            + f"<h2>Edit</h2>{editable}"
            + shown_document
            + "<h2>Raw record</h2>"
            "<p class='muted small'>The whole record, as JSON. What PocketBase owns is dropped "
            "before it is sent. For a module, this is where the definition itself lives.</p>"
            f"<form method='post' action='/record/{esc(collection)}/{esc(record_id)}/raw'>"
            f"<input type='hidden' name='csrf' value='{esc(session.csrf)}'>"
            f"<label for='json'>JSON</label><textarea id='json' name='json'>{esc(raw)}</textarea>"
            "<button type='submit'>Save raw</button></form>"
            "<h2>Delete</h2>"
            + _delete_warning(collection)
            + f"<form method='post' action='/record/{esc(collection)}/{esc(record_id)}/delete'>"
            f"<input type='hidden' name='csrf' value='{esc(session.csrf)}'>"
            f"<label for='confirm'>Type {esc(record_id)} to confirm</label>"
            "<input id='confirm' name='confirm' autocomplete='off'>"
            "<button type='submit' class='danger'>Delete this record</button></form>"
        )
        notice = params.get("saved", "")
        self._send(
            200,
            page(
                record_title(collection, record),
                body,
                store_url=session.url,
                who=session.email,
                notice=notice,
                error=params.get("error", ""),
            ),
        )

    def _logs(self, store: PocketBase, session: Session) -> None:
        entries = store.logs(per_page=50)
        rows = [
            [
                f"<span class='muted small'>{esc(_when(entry.get('created')))}</span>",
                esc(LOG_LEVELS.get(entry.get("level"), entry.get("level"))),
                esc(entry.get("message")),
                f"<span class='muted small'>{esc(json.dumps(entry.get('data') or {}, sort_keys=True))[:200]}</span>",
            ]
            for entry in entries
        ]
        body = "<h2>Logs</h2>" + (
            table(["When", "Level", "Message", "Data"], rows)
            if rows
            else '<p class="muted">The Store keeps no log, or has nothing to say.</p>'
        )
        self._send(200, page("Logs", body, store_url=session.url, who=session.email))

    # -- the writes ----------------------------------------------------------

    def _save(
        self,
        store: PocketBase,
        session: Session,
        collection: str,
        record_id: str,
        fields: Mapping[str, str],
    ) -> None:
        changes: dict[str, Any] = {}
        for field in EDITABLE.get(collection, ()):
            if field.name not in fields:
                continue
            value: Any = fields[field.name]
            if field.kind == "number":
                try:
                    value = int(str(value).strip())
                except ValueError:
                    self._back(
                        collection,
                        record_id,
                        f"{field.label} has to be a whole number.",
                    )
                    return
            changes[field.name] = value
        if not changes:
            self._back(collection, record_id, "Nothing to save.")
            return
        store.update(collection, record_id, changes)
        self._done(collection, record_id, "Saved.")

    def _save_raw(
        self,
        store: PocketBase,
        session: Session,
        collection: str,
        record_id: str,
        fields: Mapping[str, str],
    ) -> None:
        text = fields.get("json") or ""
        try:
            parsed = json.loads(text)
        except ValueError as failure:
            self._back(collection, record_id, f"That is not JSON: {failure}")
            return
        if not isinstance(parsed, dict):
            self._back(collection, record_id, "A record is a JSON object.")
            return
        store.update(collection, record_id, raw_changes(parsed))
        self._done(collection, record_id, "Saved.")

    def _delete(
        self,
        store: PocketBase,
        session: Session,
        collection: str,
        record_id: str,
        fields: Mapping[str, str],
    ) -> None:
        if fields.get("confirm", "").strip() != record_id:
            self._back(
                collection,
                record_id,
                "The confirmation did not match the record's id, so nothing was deleted.",
            )
            return
        store.delete(collection, record_id)
        self._send(
            200,
            page(
                "Deleted",
                f"<p>{esc(collection)} {esc(record_id)} is gone.</p>"
                f"<p><a href='/collection/{esc(collection)}'>Back to {esc(collection)}</a></p>",
                store_url=session.url,
                who=session.email,
                notice="Deleted.",
            ),
        )

    def _done(self, collection: str, record_id: str, message: str) -> None:
        self._redirect(
            f"/record/{urllib.parse.quote(collection)}/{urllib.parse.quote(record_id)}"
            f"?saved={urllib.parse.quote(message)}"
        )

    def _back(self, collection: str, record_id: str, error: str) -> None:
        self._redirect(
            f"/record/{urllib.parse.quote(collection)}/{urllib.parse.quote(record_id)}"
            f"?error={urllib.parse.quote(error)}"
        )


def _field_input(field: Field, record: Mapping[str, Any]) -> str:
    """One editable field, drawn as the kind of thing it is."""
    value = record.get(field.name)
    shown = "" if value is None else str(value)
    help_text = f"<p class='help'>{esc(field.help)}</p>" if field.help else ""
    if field.kind == "textarea":
        control = f"<textarea id='f-{esc(field.name)}' name='{esc(field.name)}'>{esc(shown)}</textarea>"
    elif field.kind == "select":
        options = "".join(
            f"<option value='{esc(option)}'"
            f"{' selected' if option == shown else ''}>{esc(TIER_NAMES.get(option, option))}</option>"
            for option in field.options
        )
        control = f"<select id='f-{esc(field.name)}' name='{esc(field.name)}'>{options}</select>"
    elif field.kind == "number":
        control = f"<input id='f-{esc(field.name)}' name='{esc(field.name)}' type='number' value='{esc(shown)}'>"
    else:
        control = f"<input id='f-{esc(field.name)}' name='{esc(field.name)}' value='{esc(shown)}'>"
    return f"<label for='f-{esc(field.name)}'>{esc(field.label)}</label>{control}{help_text}"


def _relations(collection: str, record: Mapping[str, Any]) -> str:
    """The records this one points at, as links where they can be links."""
    links = []
    for field in ("publisher", "module"):
        if field not in record:
            continue
        target = {"publisher": "publishers", "module": "modules"}[field]
        raw = record.get(field)
        name = related_name(record, field) or str(raw or "")
        expand = record.get("expand")
        expanded = expand.get(field) if isinstance(expand, Mapping) else None
        identifier = (
            str(expanded.get("id"))
            if isinstance(expanded, Mapping) and expanded.get("id")
            else str(raw or "")
        )
        if identifier:
            links.append(
                f"<div class='small'>{esc(field)}: "
                f"<a href='/record/{esc(target)}/{esc(identifier)}'>{esc(name)}</a></div>"
            )
    return f"<div class='row'>{''.join(links)}</div>" if links else ""


def _delete_warning(collection: str) -> str:
    """What a delete takes with it, said before the button is pressed."""
    if collection == "publishers":
        return (
            "<p class='help'>Deleting a publisher deletes everything they published, "
            "and every rating, comment and install on all of it. The Store's schema "
            "cascades, and there is no undo.</p>"
        )
    if collection == "modules":
        return (
            "<p class='help'>Deleting a module deletes its ratings, comments and "
            "installs with it. A house that installed it keeps its own copy -- "
            "unpublishing is not uninstalling.</p>"
        )
    return ""


def _subtitle(collection: str, record: Mapping[str, Any]) -> str:
    """The line under a record's name in a listing."""
    if collection == "modules":
        publisher = related_name(record, "publisher")
        return f"{record.get('slug')} v{record.get('version')} by {publisher}"
    if collection == "publishers":
        return f"{record.get('id')}"
    if collection == "ratings":
        return (
            f"{related_name(record, 'module')} by {related_name(record, 'publisher')}"
        )
    if collection == "installs":
        return (
            f"{related_name(record, 'module')} in {related_name(record, 'publisher')}"
        )
    if collection == "comments":
        return f"{related_name(record, 'publisher')} on {record.get('module')}"
    return ""


def _when(value: Any) -> str:
    """A timestamp, short enough for a table."""
    text = str(value or "")
    return text.replace("T", " ")[:19] if text else ""


# -- Starting it --------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="admin.py",
        description="A local panel for running an Open House Store.",
    )
    parser.add_argument(
        "--url", default=DEFAULT_URL, help="the Store the login form opens on"
    )
    parser.add_argument("--host", default="127.0.0.1", help="the interface to bind")
    parser.add_argument(
        "--port", type=int, default=DEFAULT_PORT, help="the port to bind"
    )
    parser.add_argument(
        "--allow-network",
        action="store_true",
        help="bind something other than loopback, on purpose",
    )
    parser.add_argument(
        "--open", action="store_true", help="open a browser at the panel"
    )
    options = parser.parse_args(argv)

    Handler.login_url = options.url

    if not is_loopback_host(options.host) and not options.allow_network:
        sys.stderr.write(
            f"admin: {options.host} is not a loopback address, and this panel holds "
            "the Store's admin account.\n"
            "admin: pass --allow-network if you mean to serve it to your network.\n"
        )
        return 2

    server = ThreadingHTTPServer((options.host, options.port), Handler)
    where = f"http://{options.host}:{options.port}/"
    sys.stderr.write(f"admin: Open House Store admin panel on {where}\n")
    sys.stderr.write(f"admin: it will ask for the admin account of {options.url}\n")
    sys.stderr.write("admin: Ctrl-C to stop\n")
    if options.open:
        webbrowser.open(where)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        sys.stderr.write("\nadmin: stopped\n")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
