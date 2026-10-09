# Changelog

All notable changes to this project are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project aims
to follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0]

The first release. Open House is a Home Assistant custom integration that runs a
house's lighting for the people in it, configured entirely through a sidebar
panel rather than YAML.

### Added

- **The engine.** A pure decision engine (`engine/`) that reads a house's rooms,
  devices and bindings and decides what each behaviour should do, with an
  arbitration order and a per-tick decision log.
- **The Home Assistant adapter.** `ha_adapter/` and the integration in
  `custom_components/open_house/`, which compose the engine from the instance's
  areas and devices and call its services.
- **The panel.** A Lit web-component sidebar panel (`panel/`) for making rooms,
  adding devices, enabling behaviours, choosing scopes and priorities, and
  watching the activity feed.
- **Modules and blueprints.** A module format for describing what a behaviour
  does; a blueprint is imported from Home Assistant automations, Node-RED flows
  or a module document through the Dev tab.
- **The catalog.** A frozen vocabulary of rooms, slots, domains and terms
  (`catalog/`), committed and read at runtime rather than fetched.
- **The published Store.** A PocketBase-backed store (`store/`) that a house can
  publish its modules to and install other houses' modules from.
- **The tooling.** `tools/` carries the catalog generator, the validators and
  the pack CLI, and each phase has a verification record under
  `docs/reference/`.
- **An install that carries what it needs.** The integration is thin — it imports
  `engine/`, `ha_adapter/`, `tools/` and the catalog rather than copying them —
  so `tools/package_integration.py` builds `open_house.zip` with those packages
  inside the integration, and `hacs.json` names it as the release asset. A HACS
  install of a release is therefore self-contained.

### Notes

- A release must attach `dist/open_house.zip` as an asset; installing from the
  source tree alone does not carry the packages the integration imports.
- The repository ships no bundled module corpus. A house is built from the
  blueprints a person imports; the catalog is what is committed.
- Licence pending: the repository carries no `LICENSE` file yet.

[0.1.0]: https://github.com/LeonKraim/Open-House/releases/tag/v0.1.0
