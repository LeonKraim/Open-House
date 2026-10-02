/**
 * The websocket command registry the panel speaks.
 *
 * This file *is* the requested API shape. `custom_components/open_house` is
 * built in parallel, so nothing here is read from it; instead the panel names
 * exactly the commands it needs, in one place, with the request and response
 * types for each -- and the integration implements this registry. Every command
 * is namespaced `open_house/` and, unless noted, is answered by the
 * integration's own `websocket_api` handlers.
 *
 * Two rules the integration has to honour for the panel to work:
 *
 *   1. Every command except `capabilities` requires an admin user and answers
 *      with an error code of `unauthorized` otherwise. The panel hides the
 *      admin-only screens from a non-admin rather than letting the calls fail,
 *      but the server is the boundary, not the panel.
 *   2. All CRUD in Phase 5 goes through this API. The panel writes no YAML and
 *      reads no files; the exit criterion "the whole journey runs with no YAML"
 *      is met only if the server can create, bind, install and export over
 *      these commands alone.
 */

export const COMMANDS = {
  /** `{}` -> `Capabilities`. Answers for any authenticated user. */
  capabilities: "open_house/capabilities",

  /** `{}` -> `HouseOverview`. */
  overview: "open_house/overview",

  /** `{}` -> `{ rooms: RoomSummary[] }`. */
  roomsList: "open_house/rooms/list",

  /** `{ room_id }` -> `RoomDetail`. */
  roomGet: "open_house/rooms/get",

  /** `{ name, room_type }` -> `RoomDetail`. */
  roomCreate: "open_house/rooms/create",

  /** `{ room_id, name? }` -> `RoomDetail`. */
  roomUpdate: "open_house/rooms/update",

  /** `{ room_id }` -> `{ room_id }`. */
  roomDelete: "open_house/rooms/delete",

  /** `{ room_id, slot, entity_id }` -> `RoomDetail`. */
  roomBind: "open_house/rooms/bind",

  /**
   * `{ room_id, slot, entity_id }` -> `RoomDetail`.
   *
   * Replace is bind with the intent made explicit: the server records the
   * device it took the slot from in the decision log, which is what makes
   * "replaced the kitchen light" answerable later. A rebind that overwrote a
   * binding silently would be indistinguishable from an initial bind.
   */
  roomReplace: "open_house/rooms/replace",

  /** `{ room_id, slot }` -> `RoomDetail`. Leaves the slot unbound. */
  roomUnbind: "open_house/rooms/unbind",

  /** `{ room_id, slot, query?, limit? }` -> `{ candidates: BindingSuggestion[] }`. */
  roomCandidates: "open_house/rooms/candidates",

  /** `{ room_id }` -> `{ schema, values }`. The high-level options, from the pack schemas. */
  roomOptionsGet: "open_house/rooms/options/get",

  /** `{ room_id, values }` -> `{ schema, values }`. */
  roomOptionsSet: "open_house/rooms/options/set",

  /**
   * `{ room_id }` -> `{ offers: ModuleOffer[] }`.
   *
   * Every offer is returned, each already carrying its satisfiability verdict
   * (`satisfiable`, `missing_slots`) and its conflict check -- the panel renders
   * "add module to room" from this one answer and performs no satisfiability or
   * conflict reasoning of its own.
   */
  roomAvailableModules: "open_house/rooms/available_modules",

  /** `{ room_id, pack }` -> `{ installed: InstalledModule, room: RoomDetail }`. */
  moduleInstall: "open_house/modules/install",

  /** `{ room_id, pack }` -> `RoomDetail`. */
  moduleUninstall: "open_house/modules/uninstall",

  /** `{ room_id, pack, enabled }` -> `InstalledModule`. */
  moduleSetEnabled: "open_house/modules/set_enabled",

  /**
   * `{ room_id, pack, behaviour, enabled }` -> `InstalledModule`.
   *
   * The atom's own switch, and the reason the modules screen lists behaviours at
   * all. A pack is a bag of atoms -- "bedtime" is lights off, plus the
   * thermostat, plus the locks -- and a person who wants the lights and not the
   * locks turns one of them off here rather than not installing the pack.
   * `behaviour` is the `InstalledModule.behaviours[].id` the listing already
   * carries, which is pack-qualified (`bedtime.lights_off`); the declared bare
   * name is accepted too.
   *
   * It answers the whole `InstalledModule` rather than the one behaviour,
   * because the pack's own `enabled` chip is derived from its behaviours -- turn
   * one atom off and the pack is no longer fully on -- and a caller that had to
   * recompute that from a partial answer would be re-implementing the server's
   * rule.
   */
  moduleSetBehaviourEnabled: "open_house/modules/set_behaviour_enabled",

  /**
   * `{ room_id, pack, behaviour, scope }` -> `InstalledModule`.
   *
   * The atom's reach, beside the atom's switch. `scope: "room"` runs a
   * house-wide atom in the one room its module was installed into; `scope:
   * "house"` runs a room's atom for every room. The manifest's declared scope
   * is what a house that has never been asked gets, so this is a preference and
   * not a required choice.
   *
   * It answers the whole `InstalledModule`, for the reason
   * `moduleSetBehaviourEnabled` does: the module's own `scope` chip is derived
   * from its behaviours' scopes, and the atom rows carry `widenable` -- the
   * server's verdict on whether "house" is available to that atom at all.
   */
  moduleSetBehaviourScope: "open_house/modules/set_behaviour_scope",

  /** `{}` -> `{ modules: InstalledModule[] }`. */
  modulesList: "open_house/modules/list",

  /**
   * `{}` -> `HouseScope`.
   *
   * The house's own slots -- every light, every door, every thermostat, as the
   * rooms' bindings collected in room order -- and the modules that act at
   * house scope. Read-only: a house slot is not bound, it is collected, so the
   * only edit to "all the lights" is a light in a room.
   */
  houseScope: "open_house/house/scope",

  /** `{}` -> `{ profiles: ProfileRef[] }`. */
  profilesList: "open_house/profiles/list",

  /** `{ room_id, axis, profile }` -> `RoomDetail`. */
  profileActivate: "open_house/profiles/activate",

  /** `{}` -> `{ entries: StoreEntry[], generated_at, cached }`. */
  storeIndex: "open_house/store/index",

  /** `{ pack, tier }` -> `{ installed: InstalledModule }`. */
  storeInstall: "open_house/store/install",

  /** `{ limit?, before? }` -> `{ entries: DecisionLogEntry[] }`. */
  activityList: "open_house/activity/list",

  /**
   * `{}` -> stream of `DecisionLogEntry`.
   *
   * Delivered by `subscribeMessage`, not `callWS`: it is the one command that
   * pushes rather than answers.
   */
  activitySubscribe: "open_house/activity/subscribe",

  /** `{}` -> `{ issues: HealthIssue[] }`. */
  healthList: "open_house/health/list",

  /** `{}` -> `ExportDocument` (the frozen schema in `schemas/export-document`). */
  exportDocument: "open_house/import_export/export",

  /** `{ document }` -> `ImportPreview`. A dry run; changes nothing. */
  importPreview: "open_house/import_export/preview",

  /** `{ document, snapshot: true }` -> `{ applied: true, snapshot_id, diff }`. */
  importApply: "open_house/import_export/apply",

  /** `{ room_id }` -> `{ created: boolean, url_path }`. */
  dashboardGenerate: "open_house/dashboard/generate",
} as const;

export type CommandName = (typeof COMMANDS)[keyof typeof COMMANDS];

/** The `open_house/` domain every error code is read against. */
export const API_DOMAIN = "open_house";

/**
 * The refusals a command answers with, spelled as the server spells them.
 *
 * `websocket_api.py` names each of these once for the same reason: a code
 * compared as a string literal in ten places is ten places a typo hides, and a
 * typo in a comparison is not a build error -- it is a branch that silently
 * never runs. They are here, beside `COMMANDS`, because both are the same
 * contract read from the same two files.
 */
export const REFUSALS = {
  /** The caller is not an administrator. */
  unauthorized: "unauthorized",
  /** No house has ever been made; the setup flow has not been run. */
  notSetup: "not_setup",
  /** A house exists but is between loads. The one worth asking again. */
  notReady: "not_ready",
  /** The room, slot, pack or profile named does not exist. */
  notFound: "not_found",
  /** The request itself is malformed, or a value is refused on its merits. */
  invalidFormat: "invalid_format",
} as const;

export type RefusalCode = (typeof REFUSALS)[keyof typeof REFUSALS];
