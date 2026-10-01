#!/usr/bin/env python3
"""Drive the Home Assistant onboarding flow headlessly against a fresh container.

A brand-new HA container serves the onboarding API on :8123 and refuses
everything else until an owner account exists. CI and the E2E suite need that
out of the way without a browser, so this script walks the documented flow:

    GET  /api/onboarding                       -> which steps are still pending
    POST /api/onboarding/users                 -> create the owner
    POST /auth/token                           -> exchange code for a refresh token
    POST /api/onboarding/core_config           -> name/units/location
    POST /api/onboarding/analytics             -> opt out
    POST /api/onboarding/integration           -> optional, none required

It writes a **long-lived** access token to `.env.local` so the E2E harness can
take over, and it is idempotent: running it against an already-onboarded
instance logs in with the same credentials rather than reporting failure, and
running it again while the stored token still authenticates mints nothing new.

The token deserves a note, because getting this wrong is invisible for about
thirty minutes. Home Assistant mints two quite different things. The
`/auth/token` exchange below returns an OAuth *access* token, which is what the
onboarding flow hands out and which expires in **30 minutes**. The token a
`HA_TOKEN` in `.env.local` is meant to be is a *long-lived* access token, and
Home Assistant will only create one over the **websocket** API
(`auth/long_lived_access_token`); there is no REST endpoint for it. An earlier
version of this script stored the 30-minute token, which meant every consumer
worked for half an hour after onboarding and then failed with a 401 that looked
like a bad password. So the script now does both: obtain a short-lived token to
authenticate the websocket, then use it to mint the one worth storing.

Long-lived tokens are also **uniquely named**. Home Assistant refuses a second
token called `open-house` and reports it as a bare `unknown_error` -- the reason
(`ValueError: open-house already exists`) appears only in the container log. So
the script reuses the stored token when it still works, which is the ordinary
re-run, and falls back to a timestamped name when it has to mint after the old
token was lost or revoked.

The websocket client below is hand-rolled, which needs justifying. The command
it carries is one JSON message in, one JSON message out. Taking a `websockets`
dependency for that would put a network library into the dependency list that
`specs/architecture-invariants` reads as the engine's declared boundary -- a
boundary the project deliberately keeps to things the engine itself imports. A
framing bug here fails loudly on the first run against a live container, which
is how this one was written.

Usage:
    python tools/ha/onboard.py [--base http://localhost:8123]
                               [--user admin] [--password open-house-dev]
                               [--name "Open House"]
                               [--env .env.local] [--stdout]
"""

from __future__ import annotations

import argparse
import base64
import json
import secrets
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from collections.abc import Mapping

CLIENT_ID = "http://localhost:8123/"

#: Where the minted token is kept. Gitignored, and the only place the E2E
#: harness reads it from, so the script writes it rather than printing it for
#: somebody to copy -- a token pasted into a shell ends up in that shell's
#: history.
DEFAULT_ENV = ".env.local"

#: What Home Assistant records against the minted token, and how long it lives.
#: Ten years matches the default the profile page offers; the point is that the
#: token outlives the checkout, since it is written to `.env.local` and read by
#: every later run.
TOKEN_CLIENT_NAME = "open-house"
TOKEN_LIFESPAN_DAYS = 3650


def _parsed(text: str) -> object:
    """A response body as a JSON value, falling back to the raw text.

    Home Assistant returns JSON on every path this script touches, but an error
    page from a proxy in front of it does not, and an exception there would hide
    the HTTP status that says what actually went wrong.
    """
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"raw": text}


def _raw(value: object, key: str) -> object:
    """A field of a JSON object, or None when the value is not an object."""
    if not isinstance(value, dict):
        return None
    return cast("dict[str, object]", value).get(key)


def _str_field(value: object, key: str) -> str | None:
    """A field of a JSON object when it is a string, and None otherwise.

    Narrowed rather than stringified: every one of these fields is a token or a
    code, and a response that carried a number where a string belongs is a
    response to report, not one to coerce.
    """
    item = _raw(value, key)
    return item if isinstance(item, str) else None


def _items(value: object) -> list[object]:
    """A JSON array as a list, or an empty one.

    The onboarding state endpoint returns a *list* of step objects. Typing it as
    a mapping was the earlier mistake here: iterating a dict yields its keys, so
    `for step in steps` would have walked strings and failed on the first
    `.get`.
    """
    return list(cast("list[object]", value)) if isinstance(value, list) else []


def _req(
    base: str,
    path: str,
    payload: Mapping[str, object] | None = None,
    token: str | None = None,
    method: str | None = None,
    form: bool = False,
) -> tuple[int, object]:
    """POST `payload`, JSON by default, returning (status, parsed body).

    `/auth/token` is an IndieAuth endpoint and takes
    `application/x-www-form-urlencoded`, not JSON -- posting JSON there returns
    `unsupported_grant_type`. Everything under `/api/` is JSON.
    """
    url = f"{base}{path}"
    if payload is None:
        data, content_type = None, None
    elif form:
        data, content_type = (
            urllib.parse.urlencode(payload).encode(),
            "application/x-www-form-urlencoded",
        )
    else:
        data, content_type = json.dumps(payload).encode(), "application/json"
    req = urllib.request.Request(
        url, data=data, method=method or ("POST" if data else "GET")
    )
    if content_type:
        req.add_header("Content-Type", content_type)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, _parsed(resp.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        return exc.code, _parsed(exc.read().decode() or "{}")


def wait_for_api(base: str, timeout: float = 300.0) -> None:
    """Block until the onboarding API answers, which is our readiness signal."""
    deadline = time.monotonic() + timeout
    last: str = "no attempt made"
    while time.monotonic() < deadline:
        try:
            status, body = _req(base, "/api/onboarding")
            if status == 200:
                return
            last = f"HTTP {status}: {body}"
        except OSError as exc:
            last = str(exc)
        time.sleep(3)
    raise SystemExit(f"Home Assistant did not become ready within {timeout}s ({last})")


def onboard(base: str, user: str, password: str, name: str) -> str:
    status, steps = _req(base, "/api/onboarding")
    if status != 200:
        raise SystemExit(f"cannot read onboarding state: HTTP {status} {steps}")

    done: set[str] = set()
    for entry in _items(steps):
        if _raw(entry, "done") is not True:
            continue
        step = _str_field(entry, "step")
        if step is not None:
            done.add(step)
    if "user" in done:
        # Idempotent on purpose. A second run against a live container is the
        # ordinary case rather than an error -- re-minting a token is usually
        # *why* the script was run again -- so it logs in instead of refusing.
        return login(base, user, password)

    status, created = _req(
        base,
        "/api/onboarding/users",
        {
            "client_id": CLIENT_ID,
            "name": name,
            "username": user,
            "password": password,
            "language": "en",
        },
    )
    auth_code = _str_field(created, "auth_code")
    if status not in (200, 201) or auth_code is None:
        raise SystemExit(f"user step failed: HTTP {status} {created}")

    status, tok = _req(
        base,
        "/auth/token",
        {
            "client_id": CLIENT_ID,
            "grant_type": "authorization_code",
            "code": auth_code,
        },
        form=True,
    )
    access = _str_field(tok, "access_token")
    if status != 200 or access is None:
        raise SystemExit(f"token exchange failed: HTTP {status} {tok}")

    for step, payload in (
        (
            "core_config",
            {
                "language": "en",
                "currency": "EUR",
                "country": "NL",
                "time_zone": "Europe/Amsterdam",
            },
        ),
        ("analytics", {"analytics": False, "usage": False, "statistics": False}),
    ):
        status, body = _req(base, f"/api/onboarding/{step}", payload, token=access)
        if status not in (200, 201):
            raise SystemExit(f"{step} step failed: HTTP {status} {body}")

    refresh = _str_field(tok, "refresh_token")
    if refresh is None:
        raise SystemExit(f"token response carried no refresh_token: {tok}")

    status, ref = _req(
        base,
        "/auth/token",
        {
            "client_id": CLIENT_ID,
            "grant_type": "refresh_token",
            "refresh_token": refresh,
        },
        form=True,
    )
    refreshed = _str_field(ref, "access_token")
    if status != 200 or refreshed is None:
        raise SystemExit(f"refresh failed: HTTP {status} {ref}")
    return refreshed


def login(base: str, user: str, password: str) -> str:
    """A short-lived access token for an instance that already has an owner.

    The onboarding flow is the only path that hands out a code without
    credentials, so an already-onboarded instance is authenticated the way a
    browser does it: open a login flow, answer it with the owner's credentials,
    and exchange the resulting code. The token this returns is the same
    thirty-minute kind the onboarding flow yields -- it exists to authenticate
    the websocket call that mints the one worth keeping.
    """
    status, flow = _req(
        base,
        "/auth/login_flow",
        {
            "client_id": CLIENT_ID,
            "handler": ["homeassistant", None],
            "redirect_uri": CLIENT_ID,
        },
    )
    flow_id = _str_field(flow, "flow_id")
    if status != 200 or flow_id is None:
        raise SystemExit(f"cannot open a login flow: HTTP {status} {flow}")

    status, answer = _req(
        base,
        f"/auth/login_flow/{flow_id}",
        {"client_id": CLIENT_ID, "username": user, "password": password},
    )
    code = _str_field(answer, "result")
    if status != 200 or code is None:
        raise SystemExit(
            f"login failed: HTTP {status} {answer}. A rejected password and an "
            "unknown user both answer this way; the account is the one the "
            "onboarding flow created, not the `--name` given on that run."
        )

    status, tok = _req(
        base,
        "/auth/token",
        {
            "client_id": CLIENT_ID,
            "grant_type": "authorization_code",
            "code": code,
        },
        form=True,
    )
    access = _str_field(tok, "access_token")
    if status != 200 or access is None:
        raise SystemExit(f"token exchange failed: HTTP {status} {tok}")
    return access


def _ws_endpoint(base: str) -> tuple[str, int]:
    """The websocket host and port for an HTTP base URL."""
    parsed = urllib.parse.urlsplit(base)
    host = parsed.hostname or "localhost"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    return host, port


class _WebSocket:
    """The smallest websocket client that can carry one JSON request.

    Text frames only, and no continuation handling: every message Home
    Assistant sends on this connection is a single unfragmented text frame
    well under 64 KiB, so the 126/127 length forms exist for completeness
    rather than because anything here needs them. Control frames other than
    close are read past, since the server may ping while it works.
    """

    def __init__(self, sock: socket.socket, buffered: bytes) -> None:
        self._sock = sock
        self._buf = buffered

    def _read(self, count: int) -> bytes:
        while len(self._buf) < count:
            chunk = self._sock.recv(65536)
            if not chunk:
                raise SystemExit("the websocket closed mid-message")
            self._buf += chunk
        out, self._buf = self._buf[:count], self._buf[count:]
        return out

    def send(self, text: str) -> None:
        """One masked text frame, which is what a client must send."""
        payload = text.encode()
        frame = bytearray([0x81])
        size = len(payload)
        if size < 126:
            frame.append(0x80 | size)
        elif size < 65536:
            frame.append(0x80 | 126)
            frame += size.to_bytes(2, "big")
        else:
            frame.append(0x80 | 127)
            frame += size.to_bytes(8, "big")
        mask = secrets.token_bytes(4)
        frame += mask
        frame += bytes(byte ^ mask[i % 4] for i, byte in enumerate(payload))
        self._sock.sendall(bytes(frame))

    def recv(self) -> str:
        """The next text message, skipping the frames that carry no message."""
        while True:
            first, second = self._read(2)
            opcode = first & 0x0F
            size = second & 0x7F
            if size == 126:
                size = int.from_bytes(self._read(2), "big")
            elif size == 127:
                size = int.from_bytes(self._read(8), "big")
            if second & 0x80:
                self._read(4)  # a server frame is never masked, but be total
            data = self._read(size)
            if opcode == 0x8:
                raise SystemExit("the websocket was closed by the server")
            if opcode in (0x1, 0x0):
                return data.decode()


def _connect_websocket(host: str, port: int) -> _WebSocket:
    """Open `/api/websocket`, returning the client with any early bytes kept.

    The handshake response can arrive in the same TCP segment as the server's
    greeting frame, so the bytes after the header block are handed to the
    client rather than discarded -- losing them loses `auth_required` and the
    next read blocks forever.
    """
    key = base64.b64encode(secrets.token_bytes(16)).decode()
    sock = socket.create_connection((host, port), timeout=30)
    sock.sendall(
        (
            "GET /api/websocket HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "\r\n"
        ).encode()
    )
    header = b""
    while b"\r\n\r\n" not in header:
        chunk = sock.recv(4096)
        if not chunk:
            raise SystemExit("the websocket handshake closed without answering")
        header += chunk
    status_line, _, buffered = header.partition(b"\r\n\r\n")
    if b" 101 " not in status_line.split(b"\r\n")[0]:
        raise SystemExit(f"the websocket handshake failed: {status_line!r}")
    return _WebSocket(sock, buffered)


def long_lived_token(
    base: str,
    access: str,
    name: str = TOKEN_CLIENT_NAME,
    lifespan: int = TOKEN_LIFESPAN_DAYS,
) -> str | None:
    """Mint a long-lived access token, the one worth writing to `.env.local`.

    Home Assistant exposes this over the websocket API only, so the sequence is
    the documented one: read `auth_required`, answer with the short-lived token,
    wait for `auth_ok`, then issue the command by id and read until the reply
    carrying that id comes back.

    Returns None when the request is refused, which in practice means the name
    is taken -- the server says `unknown_error` and puts the real reason only in
    its log. A caller that has another name to try should, rather than treating
    a taken name as a dead container.
    """
    host, port = _ws_endpoint(base)
    socket_client = _connect_websocket(host, port)

    greeting = cast("dict[str, object]", json.loads(socket_client.recv()))
    if greeting.get("type") != "auth_required":
        raise SystemExit(f"the websocket did not ask for auth: {greeting}")
    socket_client.send(json.dumps({"type": "auth", "access_token": access}))
    answer = cast("dict[str, object]", json.loads(socket_client.recv()))
    if answer.get("type") != "auth_ok":
        raise SystemExit(
            f"the websocket rejected the short-lived token: {answer}. That token "
            "expires in thirty minutes, so a slow run can outlive it."
        )

    socket_client.send(
        json.dumps(
            {
                "id": 1,
                "type": "auth/long_lived_access_token",
                "client_name": name,
                "lifespan": lifespan,
            }
        )
    )
    while True:
        message = cast("dict[str, object]", json.loads(socket_client.recv()))
        if message.get("id") != 1:
            continue
        if message.get("success") is not True:
            return None
        token = message.get("result")
        return token if isinstance(token, str) else None


def _stored_token(path: str) -> str | None:
    """The `HA_TOKEN` in an env file, or None when absent or blank."""
    target = Path(path)
    if not target.exists():
        return None
    for line in target.read_text(encoding="utf-8").splitlines():
        name, _, value = line.partition("=")
        if name.strip() == "HA_TOKEN" and value.strip():
            return value.strip()
    return None


def _authenticates(base: str, token: str) -> bool:
    """Whether the instance accepts this token.

    `/api/config` rather than a cheaper path because it is the endpoint the
    README tells a reader to verify with, so a token that passes here is one
    that passes for them too.
    """
    status, _ = _req(base, "/api/config", token=token)
    return status == 200


def _write_env(path: str, base: str, token: str) -> None:
    """Write `HA_BASE_URL` and `HA_TOKEN`, replacing any earlier pair.

    Line-wise rather than a rewrite, so a `.env.local` that has grown other
    keys keeps them; only the two this script owns are touched.
    """
    target = Path(path)
    kept: list[str] = []
    if target.exists():
        kept = [
            line
            for line in target.read_text(encoding="utf-8").splitlines()
            if not line.startswith(("HA_BASE_URL=", "HA_TOKEN="))
        ]
    kept += [f"HA_BASE_URL={base}", f"HA_TOKEN={token}"]
    target.write_text("\n".join(kept) + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base", default="http://localhost:8123")
    ap.add_argument("--user", default="admin")
    ap.add_argument("--password", default="open-house-dev")
    ap.add_argument("--name", default="Open House")
    ap.add_argument("--env", default=DEFAULT_ENV, help="where to write the token")
    ap.add_argument("--stdout", action="store_true", help="also print the token")
    args = ap.parse_args()

    wait_for_api(args.base)

    stored = _stored_token(args.env)
    if stored is not None and _authenticates(args.base, stored):
        print(f"{args.env} already holds a token this instance accepts")
        if args.stdout:
            print(stored)
        return 0

    access = onboard(args.base, args.user, args.password, args.name)
    token = long_lived_token(args.base, access)
    if token is None:
        # Taken, and the refusal does not say so. A unique name is the way out:
        # the old token cannot be revoked from here, only from the profile page.
        token = long_lived_token(
            args.base, access, name=f"{TOKEN_CLIENT_NAME}-{int(time.time())}"
        )
    if token is None:
        raise SystemExit(
            "Home Assistant refused to mint a long-lived token under either "
            "name. Its log carries the reason; the usual one is that both are "
            "already in use, which is fixed by revoking the old tokens in the "
            "profile page."
        )

    _write_env(args.env, args.base, token)
    print(f"wrote a long-lived token to {args.env} (valid {TOKEN_LIFESPAN_DAYS} days)")
    if args.stdout:
        print(token)
    return 0


if __name__ == "__main__":
    sys.exit(main())
