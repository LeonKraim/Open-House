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

  /** `{ name, type }` -> `RoomDetail`. */
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

  /** `{}` -> `{ modules: InstalledModule[] }`. */
  modulesList: "open_house/modules/list",

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
