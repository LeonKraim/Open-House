"""Throwaway: list the hosted modules, and take named ones out of their room.

Reads HA_TOKEN from the environment; never prints it.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any, cast

import aiohttp

SOCKET = "http://localhost:8123/api/websocket"


async def main() -> int:
    token = os.environ.get("HA_TOKEN", "")
    if not token:
        print("HA_TOKEN is not set", file=sys.stderr)
        return 2
    async with (
        aiohttp.ClientSession() as session,
        session.ws_connect(SOCKET, max_msg_size=0) as socket,
    ):
        await socket.receive_str()
        await socket.send_json({"type": "auth", "access_token": token})
        greeting = cast("dict[str, Any]", json.loads(await socket.receive_str()) or {})
        assert greeting.get("type") == "auth_ok"
        counter = 0

        async def call(payload: dict[str, Any]) -> dict[str, Any]:
            nonlocal counter
            counter += 1
            await socket.send_json({"id": counter, **payload})
            while True:
                answer = json.loads(await socket.receive_str())
                if answer.get("id") == counter and answer.get("type") == "result":
                    if not answer.get("success"):
                        raise SystemExit(f"{payload['type']} failed: {answer}")
                    return answer["result"]

        hosted = await call({"type": "open_house/modules/hosted"})
        rows = cast("list[dict[str, Any]]", hosted["modules"])
        for row in rows:
            print(
                f"{row['slug']:40} room={row.get('room_id')!r} title={row['title']!r}"
            )
        for slug in sys.argv[1:]:
            result = await call({"type": "open_house/modules/unhost", "module": slug})
            del result
            print(f"unhosted {slug}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
