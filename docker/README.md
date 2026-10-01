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

Two directories are involved and they are not the same thing. `docker/ha-config/`
is the container's `/config`: the database, logs, registries and everything
onboarding writes — generated, and gitignored wholesale so a run can never commit
a token or a device registry. `docker/ha/` is the configuration this repository
*authors*, mounted over the generated directory's copy so a fresh `up` gets it
from the tree.

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

## Wire the broker

The MQTT integration cannot be declared in `configuration.yaml`. It was a YAML
block once and a current instance refuses it, because MQTT is a *config entry*
now — the log says `'broker' is an invalid option for 'mqtt'`, with the
`mqtt->0->broker` path that means the block was read as a sequence. So the broker
address is set by driving the same config flow the frontend uses:

```bash
python tools/ha/broker.py            # --broker mocks --port 1883 by default
```

Idempotent, like the onboarder: an existing `mqtt` entry is reported and left
alone. The address is the **compose service name** (`mocks`), not `localhost`,
because Home Assistant and the broker are two containers on one compose network.

A configuration change made after the container started needs the instance
restarted before it takes effect:

```bash
docker compose -f docker/docker-compose.yml restart homeassistant
```

## Publish the mock house

`tools/ha/fleet.py` announces a fixture house — the same
`sim.fixtures.build_fixture` houses every scenario is written against — to Home
Assistant over MQTT discovery. Each advertised entity gets a state topic, an
availability topic, and a command topic where the domain takes one; a command
Home Assistant sends is applied to the fixture's adapter through the
`HouseAdapter` port, and the resulting state is republished.

```bash
python -m tools.ha.fleet --fixture minimal          # serve commands
python -m tools.ha.fleet --fixture messy --once     # announce and exit
```

It prints what it advertised and what it skipped, each skip with its reason: a
discovery config is not only a topic, and several platforms (`select`, `climate`,
`media_player`, `vacuum`) require fields describing the *device* that a fixture
device — built by `add_entity(id, INITIAL[domain])`, carrying no attributes —
cannot supply. Those are reported rather than faked.

The fleet is a **host** process, not a container and not a `sim/` module: `sim/`
may not import `socket` and every scenario run executes under `tools/netguard.py`,
which fails a run the moment one opens. So the guard keeps the fleet and the
scenarios apart by construction.

Then Home Assistant has a house:

```bash
curl -s -H "Authorization: Bearer $HA_TOKEN" http://localhost:8123/api/states \
  | python -c "import json,sys; print([s['entity_id'] for s in json.load(sys.stdin) if 'mock_fleet' in s['entity_id']])"
```

## Reset

`docker/ha-config/` is gitignored runtime state. To return to a pristine,
un-onboarded instance:

```bash
docker compose -f docker/docker-compose.yml down
rm -rf docker/ha-config/*
docker compose -f docker/docker-compose.yml up -d
python tools/ha/onboard.py     # owner account + token
python tools/ha/broker.py      # the mqtt config entry
```

A wiped `ha-config/` takes the `mqtt` config entry with it, and the entry lives in
`.storage/` rather than in any file this repository owns — so both setup steps are
needed after a reset, and the second one is easy to forget because nothing else
about the container looks wrong.

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
