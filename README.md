# Open House

Open House is a Home Assistant custom integration that runs a house's lighting
for the people in it. It reads the areas and devices the instance already has,
asks a person which of those areas are rooms, and then decides what to do from
the bindings those rooms carry — no automations to write, no YAML to edit.

Everything a person does after setup happens in a sidebar panel: making rooms,
adding a device to one, turning a behaviour on or off, and watching what the
house did. A house is built from a *blueprint* — a module description imported
from the Dev tab — and a behaviour is a thing a module does, with the settings
and the room it acts in chosen on the room's own page.

## Install

Open House is a custom integration with a repository behind it, and an install is
the two together. The integration in `custom_components/open_house/` is
deliberately thin: the decisions it makes live in this repository's own Python
packages (`engine/`, `ha_adapter/`, `tools/`) and in its committed catalog
(`catalog/`, `schemas/`, `registry/`), which it imports and reads at runtime.
Those are not copied into the integration directory *in the repository*, because
they are also what the simulator, the CLI and the test suite run against, and a
second copy would be a second source of truth.

A release makes them one thing. `tools/package_integration.py` builds
`dist/open_house.zip` — the integration with those packages inside it — and a
release attaches that archive as its asset, which is what `hacs.json` points at
(`zip_release`). So there are two ways to install, and either leaves the
integration able to find everything it needs:

1. **HACS.** Add this repository as a custom repository of type *Integration*,
   install Open House from it, and restart Home Assistant. HACS unpacks the
   release asset, which carries the packages, so nothing else has to be fetched
   or copied. Add the integration from **Settings → Devices & services**. (A
   release of this repository *must* carry the asset: cloning the source and
   dropping `custom_components/open_house/` in by hand does not, and the
   integration will not load. Build the asset with
   `python -m tools.package_integration`.)
2. **The bundled container.** `docker compose up` in `docker/` stands up the
   whole stack — Home Assistant, the panel, the Store and Node-RED — for
   development. It mounts the integration at `/config/custom_components/open_house`
   and the repository's packages read-only at `/openhouse-src`, which is where
   the integration looks for them by default. This is the quickest way to see
   everything running.

If you are running from a checkout rather than the archive, the packages have to
be reachable some other way. Home Assistant puts its config directory on
`sys.path` while it loads custom integrations (`homeassistant/loader.py`), so a
checkout that *is* your `config` directory works as it stands; and
`OPEN_HOUSE_SRC`, set to a checkout's root, is how you say where the packages
are when the integration lives somewhere else.

Either way the catalog is the one committed to this repository and shipped in the
install. It is never fetched from anywhere.

## What is in this repository

| Path | What it holds |
| --- | --- |
| `custom_components/open_house/` | The integration: the config flow, the panel view, the websocket commands. |
| `engine/` | The decision engine. Pure, in the sense that it imports nothing outside the standard library and this repository. |
| `ha_adapter/` | The bridge between the engine and Home Assistant: entities in, service calls out. |
| `panel/` | The Lit web-component panel, bundled with Vite. |
| `catalog/` | The frozen vocabulary — rooms, slots, domains, terms — read at runtime. |
| `tools/` | The validators, the catalog generator and the pack CLI. |
| `store/` | The PocketBase-backed published Store, for publishing modules between houses. |
| `docker/` | A compose file that stands up Home Assistant, the panel and a store on one machine. |

## Documentation

- [`docs/user-guide.md`](docs/user-guide.md) — what the integration does, and
  where the gaps are.
- [`docs/pack-author-guide.md`](docs/pack-author-guide.md) — how a module is
  written.
- [`docs/reference/`](docs/reference/) — the schemas, the arbitration rules and
  the per-phase verification notes.

The same documentation is served by MkDocs: `mkdocs serve`.

## Development

The short version: `uv sync`, then `uv run python -m pytest -q` and
`uv run python -m tools.catalog.cli validate`. The panel is checked with
`cd panel && npm ci && npm run typecheck && npm run build && npm test`.
[`CONTRIBUTING.md`](CONTRIBUTING.md) has the whole of it, including the hooks
and the container.

## Licence

Licence pending. The repository carries no `LICENSE` file yet; until one is
added, all rights are reserved by the author.
