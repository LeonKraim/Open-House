# Local Home Assistant

A real Home Assistant instance we control, used as the integration and E2E
target for the whole project. Everything in Phases 1+ is developed against this
container plus the pure-Python simulator; the container is what proves the
simulator stays honest.

## Stack

| Service | Image | Purpose |
| --- | --- | --- |
| `open-house-ha` | `ghcr.io/home-assistant/home-assistant:stable` | The Home Assistant instance. UI on <http://localhost:8123>. |
| `open-house-mocks` | `eclipse-mosquitto:2` | MQTT broker the mock device fleet publishes through. |

## Start

```bash
docker compose -f docker/docker-compose.yml up -d
docker compose -f docker/docker-compose.yml logs -f homeassistant
```

First boot takes a minute; the healthcheck gates on `/manifest.json`.

## Onboard headlessly

A fresh container serves only the onboarding API. `tools/ha/onboard.py` walks
the documented flow (owner account, core config, analytics opt-out) and then
mints a long-lived access token, writing it to `.env.local`:

```bash
python tools/ha/onboard.py
```

It is safe to re-run: a token the instance already accepts is left alone, and
only when the stored one no longer authenticates does it log in and mint
another. Long-lived tokens are uniquely named, so a replacement gets a
timestamped name rather than colliding with the one it replaces — the old token
stays valid until it is revoked from the profile page.

The token lands in `.env.local` as `HA_TOKEN` (gitignored); pass `--stdout` if
you want it echoed as well. Verify:

```bash
curl -s -H "Authorization: Bearer $HA_TOKEN" http://localhost:8123/api/config
```

## Reset

`docker/ha-config/` is gitignored runtime state. To return to a pristine,
un-onboarded instance:

```bash
docker compose -f docker/docker-compose.yml down
rm -rf docker/ha-config/*
docker compose -f docker/docker-compose.yml up -d
```

## Notes

- **Ports are published, not `network_mode: host`.** On Docker Desktop for
  Windows, host networking means the Linux VM's namespace, which the Windows
  host cannot reach, so `localhost:8123` refuses. Published ports are the
  portable choice.
- **`/auth/token` is form-encoded.** It is an IndieAuth endpoint; posting JSON
  returns `unsupported_grant_type`. Everything under `/api/` is JSON.
- The image now runs Python 3.14 internally. The project's own code targets
  3.12+, so pin the engine's supported range deliberately rather than following
  the container.
