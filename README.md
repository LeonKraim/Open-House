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

Open House is installed as a custom integration. With
[HACS](https://hacs.xyz/) the two steps are:

1. HACS → Integrations → ⋮ → **Custom repositories**, add
   `https://github.com/LeonKraim/Open-House` as a repository of category
   *Integration*.
2. Add **Open House** from HACS, restart Home Assistant, then add the
   integration from **Settings → Devices & services → Add integration**.

To install it by hand instead, copy `custom_components/open_house/` from a
checkout of this repository into your Home Assistant `config` directory and
restart.

The catalog of rooms, slots and vocabulary that the integration reads is
*committed* to this repository (`catalog/`) and is read from the checkout the
integration was loaded out of. It does not need a Home Assistant restart to
change, and it is not fetched from anywhere.

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
