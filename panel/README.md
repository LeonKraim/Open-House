# Open House panel

The custom sidebar panel for the Open House integration: TypeScript + Lit web
components, built with Vite into one ES module that Home Assistant's
`panel_custom` loads. Types for every frozen document come from the JSON Schemas
in `../schemas/`, generated rather than hand-copied.

```
npm install
npm run gen:types   # schemas/ -> src/types/generated.ts
npm run typecheck   # tsc --noEmit
npm test            # node --test, the pure-logic unit tests
npm run build       # dist/open-house-panel.js
npm run dev         # Vite dev server against a mock backend
```

`npm run build` emits exactly one file, `dist/open-house-panel.js` (plus a
source map), with Lit bundled in and no dynamic imports, because `panel_custom`
loads one module URL and makes no second request.

## What the integration has to do

Two things, and they are the whole contract between this repository's panel and
the `custom_components/open_house` integration.

### 1. Register the panel

```python
hass.components.frontend.async_register_built_in_panel(...)  # or panel_custom
async_register_panel(
    hass,
    frontend_url_path="open-house",
    webcomponent_name="open-house-panel",
    sidebar_title="Open House",
    sidebar_icon="mdi:home-assistant",
    module_url="/open_house/panel.js",
    embed_iframe=False,
    config={"open_house": True},
)
```

* `webcomponent_name` **must** be `open-house-panel`. The bundle registers that
  element, and also `ha-panel-open-house` as a second name, in case the
  frontend derives the name from the panel instead of reading
  `webcomponent_name`.
* `embed_iframe` **must** be false. The panel is a custom element that reads
  `hass`; it is not an iframe and does not re-authenticate.
* Serve the built file from a static route the integration registers (the
  `module_url` above is a placeholder path; any path under `/api/`-free static
  serving works). Bump `?v=` when the integration version changes, because the
  browser caches the module by URL.

### 2. Implement the websocket API

`src/api/protocol.ts` is the authoritative list. Every command is namespaced
`open_house/` and answered by a `websocket_api` handler. The panel sends exactly
these `type` strings and reads exactly these response shapes (`src/api/models.ts`
mirrors them in TypeScript).

| Command | Request | Response (top-level key) |
| --- | --- | --- |
| `open_house/capabilities` | `{}` | `Capabilities` |
| `open_house/overview` | `{}` | `HouseOverview` |
| `open_house/rooms/list` | `{}` | `{ rooms: RoomSummary[] }` |
| `open_house/rooms/get` | `{room_id}` | `RoomDetail` |
| `open_house/rooms/create` | `{name, type}` | `RoomDetail` |
| `open_house/rooms/update` | `{room_id, name}` | `RoomDetail` |
| `open_house/rooms/delete` | `{room_id}` | `{ room_id }` |
| `open_house/rooms/bind` | `{room_id, slot, entity_id}` | `RoomDetail` |
| `open_house/rooms/replace` | `{room_id, slot, entity_id}` | `RoomDetail` |
| `open_house/rooms/unbind` | `{room_id, slot}` | `RoomDetail` |
| `open_house/rooms/candidates` | `{room_id, slot, query?, limit?}` | `{ candidates: BindingSuggestion[] }` |
| `open_house/rooms/options/get` | `{room_id}` | `{ schema, values }` |
| `open_house/rooms/options/set` | `{room_id, values}` | `{ schema, values }` |
| `open_house/rooms/available_modules` | `{room_id}` | `{ offers: ModuleOffer[] }` |
| `open_house/modules/install` | `{room_id, pack}` | `{ installed, room }` |
| `open_house/modules/uninstall` | `{room_id, pack}` | `RoomDetail` |
| `open_house/modules/set_enabled` | `{room_id, pack, enabled}` | `InstalledModule` |
| `open_house/modules/list` | `{}` | `{ modules: InstalledModule[] }` |
| `open_house/modules/set_behaviour_enabled` | `{room_id, pack, behaviour, enabled}` | `InstalledModule` |
| `open_house/modules/set_behaviour_scope` | `{room_id, pack, behaviour, scope}` | `InstalledModule` |
| `open_house/modules/set_behaviour_priority` | `{room_id, pack, behaviour, priority}` | `InstalledModule` |
| `open_house/modules/set_slot` | `{room_id, pack, slot, entity_id, label}` | `InstalledModule` |
| `open_house/modules/hosted` | `{}` | `{ modules: HostedModule[] }` |
| `open_house/modules/read` | `{kind, key?, text?, bindings?}` | `{ module, inputs, candidates, hosted, slots, rooms }` |
| `open_house/modules/host` | `{kind, key?, text?, title, room_id?, bindings?, outputs?, settings?}` | `{ module, modules: HostedModule[] }` |
| `open_house/modules/settings` | `{module, bindings?, settings?}` | `{ module, modules: HostedModule[] }` |
| `open_house/house/scope` | `{}` | `HouseScope` |
| `open_house/profiles/list` | `{}` | `{ profiles: ProfileRef[] }` |
| `open_house/profiles/activate` | `{room_id, axis, profile}` | `RoomDetail` |
| `open_house/profiles/activate_house` | `{profile}` | `{ profiles: ProfileRef[] }` |
| `open_house/profiles/deactivate_house` | `{}` | `{ profiles: ProfileRef[] }` |
| `open_house/profiles/export` | `{profile?}` | `{ document }` |
| `open_house/profiles/import` | `{document, replace?}` | `{ imported, replaced, profiles }` |
| `open_house/activity/list` | `{limit?, before?}` | `{ entries: DecisionLogEntry[] }` |
| `open_house/activity/subscribe` | `{}` | stream of `ActivityStreamEvent` |
| `open_house/health/list` | `{}` | `{ issues: HealthIssue[] }` |
| `open_house/dashboard/generate` | `{room_id}` | `{ created, url_path }` |

Three rules the panel relies on:

1. **Every command except `capabilities` requires an administrator.** A
   non-admin gets `{code: "unauthorized"}`. The panel hides admin controls for a
   non-admin, but hiding is a courtesy; the server is the boundary.
2. **A refusal carries a `code`.** The panel branches on `unauthorized`,
   `not_found` and `unavailable` to write a better message than the raw text.
3. **`activity/subscribe` is answered by a subscription**, not a reply: the
   answer is `null` and each entry follows as an event. Everything else is a
   normal `callWS` reply.

The panel does **no** conflict reasoning, slot-satisfiability reasoning or
schema building of its own: `available_modules` returns each offer already
carrying `satisfiable`, `missing_slots` and `conflicts`, and `options/get`
returns the high-level options as a JSON Schema. That is deliberate -- the
engine that decides "can this room satisfy this pack" must be the only
implementation of it.

**Hosting a blueprint is not translating it.** `modules/*` read any automation
or blueprint, keep it as a real Home Assistant automation, and add Open House's
own placement, outputs and settings around it. A module sits in a room
(`room_id`, empty meaning the whole house), and one of the answers the import
screen offers for an input is a **slot** -- a role the module's room binds,
rather than one device. `ModuleBinding` is therefore
`{kind: "literal" | "entity" | "output" | "slot"}`, and `modules/read` answers
with the two vocabularies that answer needs: `slots` (every name this house
carries) and `rooms` (where the module may sit). A slot the room has not bound
yet is not an error: the module is hosted, `automation_id` stays empty, and
`HostedModule.slots` names the device it is waiting for. Binding that slot in
the room creates the automation.

What `modules/read` offers to publish is read out of the source: its `variables:`,
its entity inputs, its `response_variable`s, and its **service calls that drive a
device** -- named `service:<service_id>` for the devices the call acted on and
`service:<service_id>:<data key>` for each value it set. A candidate that reads a
value only one action produces carries `after`, the path to that action, and
`publish_actions` inserts its publisher immediately after it rather than at the
end of the automation. `template` says the expression is already template text
(a call's `'{{ brightness | int }}'` is) and must not be wrapped in `{{ }}` again.

## Layout

```
scripts/generate-types.mjs   schemas/ -> src/types/generated.ts
src/main.ts                  the bundle entry; registers the elements
src/panel/open-house-panel.ts  the root element: sidebar chrome and eight tabs
src/api/connection.ts        the slice of `hass` the panel uses
src/api/protocol.ts          the command registry (the contract above)
src/api/models.ts            the response view models
src/api/client.ts            one typed method per command
src/api/standalone.ts        a raw websocket connection, for dev and previews
src/components/schema-spec.ts  pure JSON Schema classification (tested)
src/components/schema-form.ts  the schema-driven form element
src/tabs/*.ts                the eight tabs, plus room settings and add-module
src/dev/*.ts                 the dev harness and its mock backend (not shipped)
tests/test_types_generation.py  the drift check between schemas/ and the types
```

## The eight tabs

Overview, Rooms, Modules, Profiles, Store, Activity, Health, Import/Export. The
two most detailed screens are the room settings page (`src/tabs/room-settings.ts`)
and "add module to room" (`src/tabs/add-module.ts`), which is where spec.txt's
requirements about bound-device status, rebind/replace, schema-rendered
high-level options and the pre-install conflict check are met.

A non-admin sees the six non-administrative tabs and read-only screens; the
Store and Import/Export tabs are hidden entirely.

## Regenerating types

`src/types/generated.ts` is generated and must not be edited by hand. Run
`npm run gen:types` after any change under `schemas/`; the drift check in
`tests/test_types_generation.py` fails if the committed file and the schemas
disagree.

The generator picks, per concept, the version no other version names in
`supersedes` -- so `pack-manifest/1.2.0` is emitted and `1.0.0`/`1.1.0` are not.
Value constraints (`pattern`, `minLength`, `minItems`) and the conditional
clauses a schema states as `allOf`/`if`/`then` are **not** encoded; TypeScript
cannot express them, and a field such a clause conditionalises is emitted
optional. Validation stays the server's and the CI's job.
