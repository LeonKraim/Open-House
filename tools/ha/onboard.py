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

CLIENT_ID = "http://localhost:8123/"


def _req(
    base: str,
    path: str,
    payload: dict | None = None,
    token: str | None = None,
    method: str | None = None,
    form: bool = False,
) -> tuple[int, dict]:
    """POST `payload`, JSON by default.

    `/auth/token` is an IndieAuth endpoint and takes
    `application/x-www-form-urlencoded`, not JSON -- posting JSON there returns
    `unsupported_grant_type`. Everything under `/api/` is JSON.
    """
    url = f"{base}{path}"
    if payload is None:
        data, content_type = None, None
    elif form:
        data, content_type = urllib.parse.urlencode(payload).encode(), "application/x-www-form-urlencoded"
    else:
        data, content_type = json.dumps(payload).encode(), "application/json"
    req = urllib.request.Request(url, data=data, method=method or ("POST" if data else "GET"))
    if content_type:
        req.add_header("Content-Type", content_type)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode() or "{}"
            return resp.status, json.loads(body)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode() or "{}"
        try:
            return exc.code, json.loads(body)
        except json.JSONDecodeError:
            return exc.code, {"raw": body}


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

    done = {s.get("step") for s in steps if s.get("done")}
    if "user" in done:
        raise SystemExit(
            "this instance is already onboarded; run "
            "`docker compose down -v && docker compose up -d` for a fresh one"
        )

    status, created = _req(
        base,
        "/api/onboarding/users",
        {"client_id": CLIENT_ID, "name": name, "username": user, "password": password, "language": "en"},
    )
    if status not in (200, 201) or "auth_code" not in created:
        raise SystemExit(f"user step failed: HTTP {status} {created}")

    status, tok = _req(
        base,
        "/auth/token",
        {
            "client_id": CLIENT_ID,
            "grant_type": "authorization_code",
            "code": created["auth_code"],
        },
        form=True,
    )
    if status != 200 or "access_token" not in tok:
        raise SystemExit(f"token exchange failed: HTTP {status} {tok}")
    access = tok["access_token"]

    for step, payload in (
        ("core_config", {"language": "en", "currency": "EUR", "country": "NL", "time_zone": "Europe/Amsterdam"}),
        ("analytics", {"analytics": False, "usage": False, "statistics": False}),
    ):
        status, body = _req(base, f"/api/onboarding/{step}", payload, token=access)
        if status not in (200, 201):
            raise SystemExit(f"{step} step failed: HTTP {status} {body}")

    status, ref = _req(
        base,
        "/auth/token",
        {"client_id": CLIENT_ID, "grant_type": "refresh_token", "refresh_token": tok["refresh_token"]},
        form=True,
    )
    if status != 200 or "access_token" not in ref:
        raise SystemExit(f"refresh failed: HTTP {status} {ref}")
    return ref["access_token"]


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
