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

It prints the long-lived access token on success so the E2E harness can take
over, and it is idempotent: running it against an already-onboarded instance
reports that and exits 0.

Usage:
    python tools/ha/onboard.py [--base http://localhost:8123]
                               [--user admin] [--password open-house-dev]
                               [--name "Open House"]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from collections.abc import Mapping

CLIENT_ID = "http://localhost:8123/"


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
        raise SystemExit(
            "this instance is already onboarded; run "
            "`docker compose down -v && docker compose up -d` for a fresh one"
        )

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


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base", default="http://localhost:8123")
    ap.add_argument("--user", default="admin")
    ap.add_argument("--password", default="open-house-dev")
    ap.add_argument("--name", default="Open House")
    args = ap.parse_args()

    wait_for_api(args.base)
    token = onboard(args.base, args.user, args.password, args.name)
    print(token)
    return 0


if __name__ == "__main__":
    sys.exit(main())
