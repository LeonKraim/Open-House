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

/** A pack installed into a room. */
export interface InstalledModule {
  pack: string;
  name: string;
  version: string;
  room_id: string;
  enabled: boolean;
  behaviours: { id: string; label: string; enabled: boolean }[];
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
