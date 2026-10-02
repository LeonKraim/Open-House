/**
 * The view models the panel renders.
 *
 * These are the *responses* of the commands in `protocol.ts`, and they are
 * deliberately not the frozen schemas in `schemas/`. A frozen schema describes
 * a document at rest -- a `House` is rooms and bindings and nothing else. A
 * screen needs the same data joined to what live Home Assistant state knows:
 * whether a bound entity is available right now, what else could fill the slot,
 * which installed pack is fighting for it. That join is the server's to make in
 * one round trip, and these types are its result.
 */

import type { JsonSchema } from "../components/schema-spec.ts";

/** Who is looking at the panel, and what they may do. */
export interface Capabilities {
  admin: boolean;
  /** `null` when Home Assistant has no authenticated user name to give. */
  user_name: string | null;
  /** The integration's version, for the Store and the diagnostics link. */
  version: string;
  /** The engine API version packs are checked against (`schemas/engine-api`). */
  engine_api: string;
  /** True when this HA has never completed the Open House setup flow. */
  needs_setup: boolean;
}

/** The Overview tab's one answer. */
export interface HouseOverview {
  name: string;
  mode: string;
  rooms: RoomSummary[];
  people: PersonStatus[];
  modules_installed: number;
  /** Counts by severity, so the Health badge needs no second call. */
  issues: { info: number; warning: number; error: number };
  /** ISO-8601, the server's clock. */
  updated_at: string;
}

/** A person the away detector follows. */
export interface PersonStatus {
  entity_id: string;
  name: string;
  state: string;
  /** Whether this person counts as home for the away rule. */
  home: boolean;
}

/** One row of the Rooms tab. */
export interface RoomSummary {
  id: string;
  name: string;
  type: string;
  /** The room type's human name, resolved from the catalog. */
  type_label: string;
  bound_slots: number;
  total_slots: number;
  /** Required slots with nothing bound: the room is not finished. */
  required_unbound: string[];
  mode: string;
  active_profiles: Record<string, string>;
  occupied: boolean;
  auto_lighting: boolean;
  issue_count: number;
}

/** A bound (or bindable) slot, with its live status. */
export interface BindingStatus {
  slot: string;
  /** The slot's human name, resolved from the catalog. */
  label: string;
  required: boolean;
  /** Home Assistant domains a binding may use, from the slot schema. */
  accepts_domains: string[];
  /** `null` when the slot is unbound. */
  entity_id: string | null;
  registry_id: string | null;
  friendly_name: string | null;
  domain: string | null;
  state: string | null;
  status: BindingStatusKind;
  /** ISO-8601 of the entity's last change, when bound. */
  last_changed: string | null;
}

export type BindingStatusKind =
  | "ok"
  | "unavailable"
  | "unknown"
  | "missing"
  | "domain_mismatch"
  | "unbound";

/** A device the server proposes for a slot, best first. */
export interface BindingSuggestion {
  entity_id: string;
  registry_id: string | null;
  friendly_name: string;
  domain: string;
  /** 0..1; the server's confidence, so the list order is the server's to own. */
  score: number;
}

/** The room settings page's whole answer. */
export interface RoomDetail {
  id: string;
  name: string;
  type: string;
  type_label: string;
  /** What the room type provides, in the catalog's order. */
  bindings: BindingStatus[];
  /** The high-level options, as a JSON Schema built from the installed packs. */
  options_schema: JsonSchema | null;
  options: Record<string, unknown>;
  modules: InstalledModule[];
  active_profiles: Record<string, string>;
  mode: string;
  /** Human-readable names for the axes, so the panel hard-codes none. */
  axes: AxisRef[];
}

/** A configurability axis a profile may be selected on (lighting, climate, ...). */
export interface AxisRef {
  id: string;
  label: string;
  profiles: ProfileRef[];
}

/** A profile a room (or the house) may be put on. */
export interface ProfileRef {
  name: string;
  label: string;
  description: string;
  kind: "room" | "house";
  axis: string | null;
  active: boolean;
}

/** A pack installed into a room, or into the whole house. */
export interface InstalledModule {
  pack: string;
  name: string;
  version: string;
  /** The room it was put in, or `""` when it was put in the whole house. */
  room_id: string;
  /**
   * Whether the module was put in the whole house rather than in a room.
   *
   * A placement and not a scope: `room_id` is `""` for it. A module installed
   * into a bedroom with a house-scoped atom is in the room and acts on the
   * house, so the two facts are drawn separately.
   */
  house: boolean;
  /** `house` when every behaviour the pack registered is house-scoped. */
  scope: "room" | "house";
  enabled: boolean;
  /**
   * Whether the room binds every slot the pack requires. False means the
   * module is in the room and switched off, and cannot be switched on until
   * `missing_slots` are bound: installation is allowed, activation is not.
   */
  satisfiable: boolean;
  /** The required slots the room binds nothing to. Empty when satisfiable. */
  missing_slots: string[];
  /**
   * The settings this module owns, in the option schema's key space.
   *
   * The join that lets a module be drawn as one subject: the schema a page
   * receives is flat, so without this the panel would have to parse
   * `module.<pack>.<key>` to know which setting belongs to which card -- a
   * second copy of the key space, free to disagree with the server's. Keys the
   * page's schema does not carry are skipped, which is the honest reading:
   * a setting that does not resolve here is not a setting this page can set.
   */
  option_keys: string[];
  /**
   * The module's own settings, as the JSON Schema its card renders.
   *
   * The slice of the page's option form that belongs to this pack -- the server
   * cuts it to `option_keys` -- so a card draws exactly its own settings with the
   * same control per type the schema form draws anywhere. `null` when the pack
   * declares no options, which is "nothing to configure here" rather than a form
   * with no fields in it.
   */
  options_schema: JsonSchema | null;
  /** The current values for `options_schema`'s keys. */
  options: Record<string, unknown>;
  behaviours: InstalledBehaviour[];
}

/** One atom of an installed pack, with its switch and its reach. */
export interface InstalledBehaviour {
  /** Pack-qualified, e.g. `bedtime.lights_off`. */
  id: string;
  label: string;
  /**
   * What switching this on actually does, in a sentence the server built from
   * the behaviour's own declaration -- the device it watches, the reading it
   * waits for, how long, and what it writes. A label alone ("The fridge has been
   * open too long") does not answer the question a switch raises.
   */
  description: string;
  enabled: boolean;
  /**
   * The rooms this atom actually runs in, in the house's own order.
   *
   * The engine's answer (`Engine.active_rooms`), not the panel's reading of the
   * module's placement: a behaviour a pack declared room-scoped is *evaluated*
   * in every room, and its enable flag is what decides which of them it runs in.
   * So this is the reach control's tick set, and the rooms it omits are the ones
   * the control exists to let somebody add. Empty for an atom that runs once for
   * the whole house -- that is `scope`'s answer, not this one's.
   */
  active_rooms: string[];
  /** The scope this atom runs in now; the declared one until somebody chose. */
  scope: "room" | "house";
  /** What the pack itself declared, which is what "reset" would return to. */
  declared_scope: "room" | "house";
  /**
   * Whether `house` is available to this atom at all.
   *
   * False for an atom whose slots the house scope cannot resolve -- one reading
   * a device only a room type provides -- where widening would put the atom on
   * every tick as a refusal. The server decides this, so the panel draws no
   * switch it could not honour.
   */
  widenable: boolean;
}

/**
 * One entry of "add module to room".
 *
 * `satisfiable` is the server's verdict on whether the room's current bindings
 * meet the pack's `requires_slots`; `missing_slots` names what is absent when
 * it is false. `conflicts` is the conflict check the spec requires before
 * install, computed against the installed set in both directions.
 */
export interface ModuleOffer {
  pack: string;
  name: string;
  description: string;
  version: string;
  kind: string;
  license: string;
  /** Locale-resolved display strings, keyed by the i18n key. */
  i18n: Record<string, string>;
  requires_slots: string[];
  optional_slots: string[];
  satisfiable: boolean;
  missing_slots: string[];
  optional_slots_present: string[];
  conflicts: ModuleConflict[];
  already_installed: boolean;
  /** The pack's own high-level options, to preview before installing. */
  options_schema: JsonSchema | null;
  behaviours: { id: string; label: string; priority: number | null }[];
}

/** One reason an offer cannot be installed as things stand. */
export interface ModuleConflict {
  kind: "declared" | "slot_contention" | "engine_api";
  /** The installed pack on the other side of the conflict. */
  pack: string;
  installed_version: string | null;
  /** The range or slot that makes it a conflict. */
  detail: string;
  severity: "blocking" | "warning";
}

/** The House tab's whole answer: the house's own slots, its modules, its options. */
export interface HouseScope {
  name: string;
  /** One row per role the house can be asked about, empty ones included. */
  slots: HouseSlot[];
  /**
   * The house's own modules: those that reach a house-wide role wherever they
   * sit, and those a person installed into the house itself.
   */
  modules: InstalledModule[];
  /** The house's own high-level options, or null when nothing declares one. */
  options_schema: JsonSchema | null;
  options: Record<string, unknown>;
}

/** The reply to installing a module: the module, and the page it landed on. */
export interface ModuleInstallReply {
  installed: InstalledModule;
  /** The room's page when the module went into a room. */
  room?: RoomDetail | null;
  /** The house's page when the module went into the house. */
  house?: HouseScope | null;
}

/** The reply to removing a module: the room's page, or the house's. */
export type ModuleUninstallReply = RoomDetail | { room_id: string; house: HouseScope };

/**
 * One role the whole house reads, bound once for every room.
 *
 * The same shape a room's binding row has, because it is the same kind of thing
 * drawn by the same controls: a slot, the entity bound to it, and that entity's
 * live status. What makes it the house's is that the entity is *the house's own*
 * -- one global device standing in for the role in the house scope and in every
 * room that bound none of its own -- rather than a summary of what the rooms
 * happened to bind.
 *
 * A slot is only sent when an installed module reaches it; the server drops the
 * roles nothing acts through, so a page of them is a page of real automations.
 */
export interface HouseSlot extends Omit<BindingStatus, "last_changed"> {
  /** The rooms whose own binding this global one stands in front of. */
  rooms: string[];
  /** The installed modules that reach this role, by display name. */
  modules: string[];
}

/** One line of the Activity tab, and the decision-log oracle's public shape. */
export interface DecisionLogEntry {
  id: string;
  /** ISO-8601. */
  at: string;
  room: string | null;
  behaviour: string | null;
  entity_id: string | null;
  action: string;
  /** Why it happened, in plain language -- the "why did this happen" answer. */
  reason: string;
  priority: number | null;
  outcome: "applied" | "skipped" | "overridden" | "blocked" | "error";
}

/** One row of the Health tab. */
export interface HealthIssue {
  severity: "info" | "warning" | "error";
  code: string;
  title: string;
  detail: string;
  room_id: string | null;
  entity_id: string | null;
  /** The Repairs flow this issue maps to, when it has one. */
  repairs_flow_id: string | null;
}

/** One row of the Store tab. */
export interface StoreEntry {
  pack: string;
  name: string;
  description: string;
  version: string;
  author: string;
  tier: "official" | "verified" | "community" | "local";
  license: string;
  /** False when a cached index is being shown offline. */
  available: boolean;
  installed_version: string | null;
  update_available: boolean;
  /** True when the update widens the pack's permissions, so it must be opted in. */
  update_requires_review: boolean;
  abandoned: boolean;
  sha256: string;
}

/** The import/export tab's preview, the dry-run diff before an import applies. */
export interface ImportPreview {
  format_version: string;
  compatible: boolean;
  /** Rows the import would add, change or remove. */
  diff: ImportDiffRow[];
  /** Bindings whose entity no longer exists and need re-linking. */
  relink: RelinkRequest[];
  notes: string[];
}

export interface ImportDiffRow {
  kind: "add" | "change" | "remove" | "unchanged";
  scope: string;
  path: string;
  before: string | null;
  after: string | null;
}

export interface RelinkRequest {
  room_id: string;
  slot: string;
  registry_id: string;
  entity_id: string | null;
  candidates: BindingSuggestion[];
}

/** The `subscribeMessage` payload for the Activity stream. */
export interface ActivityStreamEvent {
  kind: "entry" | "reset";
  entry?: DecisionLogEntry;
}
