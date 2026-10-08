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
  /**
   * Where this house's Node-RED is, as the instance's admin configured it, or
   * `""` for none.
   *
   * Configured and not discovered: Node-RED runs beside Home Assistant on one
   * install and behind the add-on's ingress on another, and the address that
   * reaches it is a fact about the network the browser is on. The panel embeds
   * Node-RED's own editor at this address, on the row a flow answers.
   */
  node_red_url?: string;
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

/**
 * The four keys a slot row carries when something other than a person decides it.
 *
 * A mixin rather than four fields written twice, because `BindingStatus` and
 * `ModuleSlot` are the same question asked of the same slot -- the room's table
 * and the module's card both have to say that a slot's device is *worked out*
 * rather than chosen -- and four fields spelled in two places are eight chances
 * to disagree. `HouseSlot` deliberately leaves them off: a house-scope slot's
 * rule is not in this plan (the plan flags it rather than assuming it), so a
 * global slot answers without them and the type says so.
 */
export interface SlotRuleFacts {
  /**
   * The kind of logic deciding this slot, or `null` for a plain device.
   *
   * `template`, `condition`, `flow` or `script`. A slot with a rule is one whose
   * device is worked out rather than chosen -- the server watches whatever the
   * kind needs and writes the row's entity as the world moves -- so the row draws
   * the rule beside the device instead of only the device.
   */
  rule_kind: ModuleSlotRuleKind | null;
  /**
   * What the rule does, in a sentence the server built.
   *
   * Built rather than assembled here for the reason every other sentence on a
   * card is: the wording has to agree with what the watcher actually runs, and a
   * panel that composed "decided by a script, when …" from `kind` and `when`
   * would be a second copy of the rule's meaning.
   */
  rule_summary: string | null;
  /**
   * Whether the rule decides *what* the slot is, rather than *whether* it applies.
   *
   * `true` for a template, a flow and a script: each produces the entity the slot
   * resolves to, so a device under them is an answer and the row shows it. `false`
   * for a condition, which produces yes or no and cannot name a device -- it
   * decides whether the slot uses the room's device or falls back.
   */
  rule_picks_device: boolean | null;
  /**
   * The device a *condition* rule gates, or `null` for every other kind.
   *
   * The one thing a condition rule has to be told, because it cannot work it out:
   * a condition never names an entity, so the person does. Without it the rule
   * would have nothing to decide about.
   */
  rule_device: string | null;
}

/** The four kinds of logic a slot row can be set to. */
export type ModuleSlotRuleKind = "template" | "condition" | "flow" | "script";

/**
 * One part a slot has been split into, with the device bound to it.
 *
 * A part is a role's half, and it is *still the same slot*: `light_group` split
 * into `a` and `b` is one role with one name that two modules can share without
 * sharing a device. What makes the sharing real rather than a promise is that a
 * part is bound **once**, for the room or the house, exactly as the slot is --
 * `slot` is the part's own binding key (`light_group__a`), so two modules on
 * `a` provably act on one entity instead of each having one of its own.
 *
 * The keys are a slot row's, so the panel draws a part with the same device
 * control it draws the slot with. `accepts_domains` is the *parent's*: a part is
 * the same role, and the catalog -- which is where domains come from -- has no
 * entry for a key a person invented.
 */
export interface SlotPart {
  /** The part's binding key, e.g. `light_group__a`. */
  slot: string;
  /** The part's own name, e.g. `a`. What a module's "which part" control offers. */
  name: string;
  /** The part's name as a person reads it. */
  label: string;
  entity_id: string | null;
  registry_id: string | null;
  friendly_name: string | null;
  domain: string | null;
  state: string | null;
  status: BindingStatusKind;
  /**
   * ISO-8601 of the entity's last change, when bound.
   *
   * Optional because a *house* slot's part rows are built by the adapter from the
   * engine's own view (`live_modules._part_rows`), which has the entity's state
   * and not Home Assistant's registry to date it from -- the same reason
   * `HouseSlot` itself omits the field.
   */
  last_changed?: string | null;
  /** The parent's domains, joined in by the server (`views._with_part_domains`). */
  accepts_domains: string[];
}

/** One part of a split slot, as a module's "which part" control offers it. */
export interface SlotPartChoice {
  name: string;
  label: string;
}

/** A bound (or bindable) slot, with its live status. */
export interface BindingStatus extends SlotRuleFacts {
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
  /**
   * The modules that act through this slot, by the name a person installed them
   * under.
   *
   * The mirror of `HouseSlot.modules`, on the page where the slot actually
   * lives: "Light group" is a role, and "the bedtime button shuts them" is what
   * makes the row about something. Both kinds of module count -- a pack the room
   * holds and a blueprint the room imported -- because both are things a person
   * put there and both are what a binding here is for.
   *
   * Empty for a slot nothing reaches, which is a real answer and not a failure:
   * a slot left in the list because the room bound it before the module that
   * needed it was removed is exactly the row a person came to unbind.
   */
  modules: string[];
  /**
   * The parts this slot has been split into, each with the device bound to it.
   *
   * Drawn *under* the slot's own row rather than beside it, because the parts are
   * still one role with one name: a row per `light_group__a` next to
   * `light_group` would read as two roles that happen to share a prefix. Empty
   * for a slot nobody has split, which is most of them.
   */
  parts: SlotPart[];
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
  /**
   * The house's own global slots this room's modules act through.
   *
   * Listed apart from `bindings` because they are bound *somewhere else*: a
   * global slot is written to the house and answers in every room, so the room
   * page draws them in their own "Whole house" section rather than among the
   * room's own. `room_entity_id` is what this room bound for the same name --
   * the one place a global binding does not reach -- so the row can say that the
   * room answers for itself.
   */
  global_bindings: GlobalBinding[];
  /** The high-level options, as a JSON Schema built from the installed packs. */
  options_schema: JsonSchema | null;
  options: Record<string, unknown>;
  modules: InstalledModule[];
  active_profiles: Record<string, string>;
  mode: string;
  /** Human-readable names for the axes, so the panel hard-codes none. */
  axes: AxisRef[];
  /**
   * The house's profile revision this page was read at.
   *
   * Sent back with every write the page makes. If the house's profiles have
   * moved since -- somebody switched a house profile in another tab -- the write
   * is refused with `stale_page`, because the values in it were decided against
   * a profile that is no longer in force.
   */
  revision: number;
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
   * The pack currently holding this module off, or `null` when nothing is.
   *
   * A *temporary* override another module placed over this one: the module's own
   * switch is untouched, so `enabled` still reads where the person left it and
   * its atoms simply are not running. The panel draws that as a red panel over
   * the card naming the holder -- the module is not misconfigured, it is being
   * held down, and the difference decides what a person does next.
   */
  suppressed_by: string | null;
  /**
   * The behaviour of the holder whose declaration does the suppressing.
   *
   * The atom rather than the pack, because "go and look at the motion lighting
   * module" is not the instruction the person needs: the one chip to switch off
   * is this one, and a pack with a dozen atoms should not have to be turned off
   * whole to release this.
   */
  suppressed_behaviour: string | null;
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
  /**
   * The devices this module acts through, each with the entity it is pointed at.
   *
   * The module's own slots rather than the room's bindings, because the override
   * is the module's: two modules in one room may reach one slot and act on two
   * different devices. One row per slot the pack's behaviours name or require,
   * so a device the pack merely *requires* -- the one that gates the whole
   * module -- can be pointed somewhere too.
   */
  slots: ModuleSlot[];
}

/**
 * One device an installed module acts through, and what its person made of it.
 *
 * The per-module, per-slot override: a module may act on a device of its own
 * rather than on the one the room bound, and may call the slot something the
 * room does not. The room's binding is untouched, so every other module keeps
 * acting on the device the room bound.
 */
export interface ModuleSlot extends SlotRuleFacts {
  /** The slot, as the key the house binds it under (`light_group`, `pack__sensor`). */
  slot: string;
  /**
   * What the module calls the slot: the person's name when they gave one, and
   * otherwise the slot's own name.
   *
   * Display-only. The engine never reads it, and the key the house resolves the
   * slot under is always `slot`.
   */
  label: string;
  /** The slot's own name, so a renamed row can still say what it really is. */
  declared_label: string;
  /**
   * The one part of a split slot this module is on, or `null` for the slot itself.
   *
   * The third thing a row can say about where the module reaches: not the
   * device, not the name, but *which half of the role*. Two modules on part `a`
   * act on the one device the room bound for `a`, which is what makes sharing a
   * part a fact rather than a promise.
   */
  part: string | null;
  /** The part's name as a person reads it, or `null` for a slot with no part. */
  part_label: string | null;
  /**
   * Every part this slot has been split into -- the "which part" control's options.
   *
   * Sent rather than fetched because the server has already read the record to
   * answer `part`: a control that went and asked again would be a second reading
   * of a fact this row is already holding, free to disagree with it.
   */
  parts: SlotPartChoice[];
  /** The entity this module acts on now: the override, or the house's binding. */
  entity_id: string | null;
  /**
   * The entity the house binds for this slot, which is what a reset falls back to.
   *
   * `null` when the house binds nothing -- a slot nothing has filled, which the
   * module either requires (it cannot run) or leaves alone.
   */
  default_entity_id: string | null;
  /**
   * Whether a person has pointed this slot at an entity of its own.
   *
   * Not derived by comparing `entity_id` to `default_entity_id`: an override that
   * happens to name the bound entity is still a decision, and the reset control
   * that clears it has to be there.
   */
  overridden: boolean;
  /** Whether a person has named the slot; `label` is then their name. */
  named: boolean;
  /** Whether the slot's definition requires it: a required one that is unbound disables the module. */
  required: boolean;
  /** Whether the pack declared the slot `separate`, so it binds under its own key. */
  separate: boolean;
  /**
   * Home Assistant domains a device for this slot may be, from the catalog.
   *
   * The *type* of the slot, and the thing the picker is filtered by: the panel
   * hands these to Home Assistant's own entity selector rather than filtering
   * candidates itself, so a role that takes several domains is HA's list and not
   * a second opinion about it. Empty for a role the catalog states no domains
   * for, which the selector reads as "any".
   */
  accepts_domains: string[];
  /** Whether the slot is one the house scope resolves (a whole-house role). */
  house_scope: boolean;
  /** Whether anything at all is bound or overridden for this slot. */
  bound: boolean;
  /** The bound entity's friendly name, when it reports one. */
  friendly_name: string | null;
  /** The entity's domain, e.g. `light`. */
  domain: string | null;
  /** The entity's live state, or `null` when it is unknown. */
  state: string | null;
  /** The binding's health, in the same closed set a room's binding row uses. */
  status: BindingStatusKind;
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
  /**
   * The rank arbitration decides this atom's competition by, now.
   *
   * When two behaviours propose for one device in one tick the higher rank wins,
   * and the pack's own order is the tie-break -- so this is the number that
   * settles a motion rule against a bedtime shutdown over the same light. The
   * server resolves it the way the engine does, at house scope, so it is the
   * number the next tick ranks by.
   */
  priority: number;
  /** The rank the pack itself declared, which is what "reset" returns to. */
  default_priority: number;
  /**
   * Whether somebody in this house has chosen a rank for this atom.
   *
   * The server's answer, not `priority !== default_priority`: a person may
   * deliberately set the declared rank back, and the two facts a control needs
   * are the ranking and whether there is anything to reset -- not whether the
   * numbers happen to differ today.
   */
  priority_set: boolean;
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
  /** The house's profile revision this page was read at. See `RoomDetail`. */
  revision: number;
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
export interface HouseSlot
  extends Omit<BindingStatus, "last_changed" | keyof SlotRuleFacts> {
  /** The rooms whose own binding this global one stands in front of. */
  rooms: string[];
  /** The installed modules that reach this role, by display name. */
  modules: string[];
}

/**
 * A house slot drawn on a *room's* page: the same row, seen from one room.
 *
 * The two extra fields are the room's side of it, and both exist to answer the
 * one question a global slot raises on a room page -- "why did the room's lights
 * not move?". A room that bound the role itself answers for itself, and the
 * global binding stands behind it rather than over it, which is what
 * `room_entity_id` says.
 */
export interface GlobalBinding extends HouseSlot {
  /** What this room bound for the same name, or `null` when it bound none. */
  room_entity_id: string | null;
  /** Whether the room answers for this role itself, in front of the global one. */
  overridden: boolean;
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

/**
 * The `subscribeMessage` payload for the Activity stream.
 *
 * **`reset` is in the vocabulary and the server does not send it.** The one
 * publisher is `host.async_publish_activity`, and it writes `{"kind": "entry"}`
 * and nothing else -- so the `kind === "reset"` branch the activity screen keeps
 * is a branch that cannot run, and a reader of this type would conclude the
 * server had a way to say "the log was cleared" that it does not have. It is
 * kept here rather than removed because the branch is the *screen's* and taking
 * the word out of the type would make it a type error in a file this one does
 * not own; what the type can do is say so.
 */
export interface ActivityStreamEvent {
  kind: "entry" | "reset";
  entry?: DecisionLogEntry;
}

// -- The Dev tab -----------------------------------------------------------
//
// One journey's types, in the order the commands in `protocol.ts` are called.
// They mirror `ha_adapter.pack_authoring`'s dataclasses field for field, written
// out rather than generated: the server's `_as_json` names every field it sends,
// so a field added on one side and not the other is a compile error here instead
// of an `undefined` rendered into a label.

/** One thing a person may import: an automation, or a blueprint. */
export interface DevSource {
  /** The stable handle: an entity id, or a blueprint's path within its folder. */
  key: string;
  name: string;
  description: string;
  /** Present on blueprints only: where the blueprint was imported from. */
  source_url?: string;
  /** Present on blueprints only: the domain the blueprint is for. */
  domain?: string;
}

/** A module a person has already authored, as the saved list shows it. */
export interface DevSaved {
  name: string;
  title: string;
  description: string;
  version: string;
  behaviours: number;
  options: number;
  file: string;
}

/** One entity the source names, and what a person decided it is. */
export interface DevEntityRow {
  key: string;
  label: string;
  entity_id: string;
  /** From the input's selector or the entity id's own prefix. */
  domain: string;
  /** How many places in the document name it. */
  count: number;
  /** A blueprint input that may be left empty. */
  optional: boolean;
  /** Where it was found, as breadcrumbs. */
  places: string[];
  /** The slot this domain usually binds to, or the empty string. */
  suggested_slot: string;
}

/** One scalar the source carries: a blueprint input, or a literal. */
export interface DevValueRow {
  key: string;
  label: string;
  /** `boolean`, `integer`, `number`, `string`, `enum`, `duration` or `time`. */
  kind: string;
  default: unknown;
  description: string;
  minimum: number | null;
  maximum: number | null;
  unit: string | null;
  choices: string[];
  places: string[];
}

/** One service call the source makes, as a candidate behaviour. */
export interface DevServiceRow {
  key: string;
  service: string;
  /**
   * Whether the engine's closed service list has this service.
   *
   * `false` is not a suggestion: the sandbox refuses a pack that calls outside
   * the list, so the row may be read and may not be kept. `null` when the server
   * had no vocabulary to check against.
   */
  supported: boolean | null;
  /** The entity-row keys this call's target names. */
  acts_on: string[];
  data_keys: string[];
  where: string;
  depth: number;
}

/** Everything an import decision can be made about, and nothing else. */
export interface DevAnalysis {
  title: string;
  description: string;
  blueprint: boolean;
  entities: DevEntityRow[];
  values: DevValueRow[];
  services: DevServiceRow[];
  /** The trigger platforms the document uses. */
  triggers: string[];
  /** The condition kinds it uses; `template` may appear and cannot be carried. */
  conditions: string[];
  /** What the reading could not carry, in the source's own words. */
  dropped: string[];
}

/** One slot a person may bind an entity to. */
export interface DevSlot {
  name: string;
  domains: string[];
  suggested: boolean;
}

/** The `dev/read` reply: the reading, and the vocabularies it was made against. */
export interface DevReadReply {
  analysis: DevAnalysis;
  slots: DevSlot[];
  /** The engine's closed service list. */
  services: string[];
}

/** One person's decision about one entity row. */
export interface DevEntityPlan {
  decision: "slot" | "ignore";
  slot?: string;
  /** Whether the module cannot run without it. */
  required?: boolean;
}

/** One person's decision about one value row. */
export interface DevValuePlan {
  decision: "setting" | "constant" | "ignore";
  key?: string;
  type?: string;
  title?: string;
  description?: string;
  default?: unknown;
  minimum?: number | null;
  maximum?: number | null;
  unit?: string | null;
  enum?: string[];
}

/** One person's decision about one service call. */
export interface DevBehaviourPlan {
  key: string;
  keep: boolean;
  name?: string;
  /** The slot the call writes through. */
  slot?: string;
  /** The slots it watches, written before the acted-on slot. */
  watched?: string[];
  trigger?: string;
  condition?: string;
  /**
   * The `duration` setting this behaviour waits out, by option key.
   *
   * The manifest's `for`, and the one clause that reads a setting: the behaviour
   * proposes only once the slot it watches has read something for that long.
   * Absent for a behaviour that acts immediately.
   */
  for?: string;
  scope?: "room" | "house";
  priority?: number;
}

/** The whole set of decisions, which is what `dev/save` is asked to write. */
export interface DevPlan {
  name: string;
  title?: string;
  description?: string;
  version?: string;
  license?: string;
  entities: Record<string, DevEntityPlan>;
  values: Record<string, DevValuePlan>;
  behaviours: DevBehaviourPlan[];
}

/** The `dev/export` reply: a module's behaviours, as automations. */
export interface DevExportReply {
  pack: string;
  automations: Record<string, unknown>[];
  yaml: string;
  /**
   * The roles the asked-about room fills nothing for, so the export names none.
   *
   * A behaviour's acted slot that resolved to nothing is written as a target
   * naming nothing rather than as an absent target -- an absent one is Home
   * Assistant's "every entity of that domain", which is a different automation
   * -- so the YAML is correct and, on its own, silent about why it would do
   * nothing. Empty when every role resolved, which is the usual answer.
   */
  unresolved: string[];
}

// -- Hosted modules --------------------------------------------------------
//
// The other half of the Dev tab: instead of translating a source into a pack,
// host it as a Home Assistant automation and let it publish what a person chose.
// These mirror `ha_adapter.module_records` and `module_host`'s dataclasses, and
// the server's `_hosted`/`ws_modules_read` name every field they send, so a
// field added on one side and not the other is a compile error here.

/** A value one module published, and the entity it lives at. */
export interface ModuleOutput {
  key: string;
  /** `number`, `boolean`, `string` or `enum` -- chosen at import. */
  kind: string;
  /** The expression the automation reads it from, as the blueprint wrote it. */
  expression: string;
  /** `sensor.open_house_<module>_<key>`, which is what a consumer binds to. */
  entity_id: string;
  /**
   * What it last read, or `null` for an output nothing has published yet.
   *
   * `null` is not zero: a module that has not run its publish step has published
   * nothing, and the panel says unknown rather than showing an invented reading.
   */
  value: unknown;
}

/** One module the house hosts. */
/**
 * One slot a hosted module reaches through, and how it resolves.
 *
 * `name` is the slot itself -- `light_group` -- and `bound` is the device the
 * module *acts on*, which is the part's device when it is on a part. `input` is
 * the module's input this slot answers, which is what a change is written back
 * to: a slot row on the card is a row of the module's answers even though the
 * answer is a device the room names rather than one the person picked.
 *
 * `parts` is the house's split of this slot (`ha_adapter.slot_parts`), one entry
 * per part with the device that part is bound to, so a person can put this
 * module on a part of a shared role without leaving the card. Empty for a slot
 * nobody has split, which is every slot until somebody does.
 */
export interface HostedSlot {
  name: string;
  /** The input of the module this slot answers. */
  input: string;
  /** `house` for a global slot, `room` for the one the module's room binds. */
  scope: string;
  /** Which part of the slot this module is on, empty for the slot itself. */
  part: string;
  /** The device the module acts on now, or empty when nothing is bound yet. */
  bound: string;
  /** What Home Assistant calls that device, or empty when it has no name. */
  bound_name: string;
  parts: { name: string; label: string; bound: string }[];
}

export interface HostedModule {
  /** The name its outputs' entity ids carry. */
  slug: string;
  title: string;
  /** The blueprint it came from -- a path, or empty for a pasted document. */
  blueprint: string;
  /**
   * The store module this was installed from, or empty for a document hosted
   * directly. Provenance, not a reference: the installation keeps its own copy
   * of the document, so removing the definition does not touch it.
   */
  definition: string;
  /** The room it was imported into, by id, or empty for the whole house. */
  room_id: string;
  /** That room as a person reads it, or "the whole house". */
  room_name: string;
  /**
   * The slots this module reaches through, and what each one answers.
   *
   * A slot with an empty `bound` is one the module is *waiting* for: it is
   * hosted, its outputs exist, and its automation is not created until the room
   * has a device for it. That is what the screen has to say, because the
   * alternative reading of an idle module is that importing it did nothing.
   */
  slots: HostedSlot[];
  /** The `automation.*` entity Home Assistant runs, or empty for none yet. */
  automation_id: string;
  /**
   * The configuration this module is running, by the name the person gave it.
   *
   * Everything else on this module -- `inputs`, `settings`, `derived`, `flows`
   * -- is *that* configuration's answers, so the card cannot show where it is
   * editing without this.
   */
  config: string;
  /**
   * Every configuration this module holds, in the order they were made.
   *
   * One placed module, several sets of answers, switched between in place: the
   * name above is always one of these. `Default` for a module whose answers were
   * imported before a module could hold more than one, which is what such a
   * module is.
   */
  configs: string[];
  /**
   * The inputs this module answers with a **condition**, by input name, each as
   * Home Assistant's own condition config.
   *
   * Kept apart from `inputs` because it is not a value the module holds: Open
   * House evaluates the condition and publishes the answer as an entity of its
   * own -- `binary_sensor.open_house_<slug>_<input>` -- and the input is bound to
   * that. Shown because it is what a person authored, and a module's logic is
   * the thing they will want to read back.
   */
  derived: Record<string, unknown>;
  /**
   * The inputs this module answers with a **flow of nodes**, by input name.
   *
   * A third kind of thing, beside a value and a condition. `entity_id` is the
   * reading the input is bound to -- what the flow writes -- and `flow_id` is
   * the flow itself, in the Node-RED this house pushes to. Both are here
   * because the row shows one and opens the other. `url` is that flow's page in
   * the editor, built by the server because only it knows both the address the
   * person configured and the id Node-RED assigned.
   */
  flows: Record<string, { flow_id: string; entity_id: string; url?: string }>;
  /**
   * The inputs this module answers with a **script**, by input name.
   *
   * The fourth kind of thing, and the only one with no entity behind it: what the
   * input reads is what the script hands back when the automation runs, so the
   * only thing to show is which script it calls and where to open it. `url` is
   * Home Assistant's own script editor on that script.
   */
  scripts: Record<string, { script_id: string; url?: string }>;
  /** The blueprint's inputs as the person filled them. */
  inputs: { name: string; value: unknown }[];
  /**
   * The inputs the module kept settable, as rows to edit.
   *
   * This is the subset the person ticked at import, and it is the whole of what
   * the module shows: every other input was answered once and is now fixed
   * inside the automation. The rows are the same shape the import screen
   * offers, because a setting is an import row that was kept.
   */
  settings: ModuleInputRow[];
  outputs: ModuleOutput[];
}

/** One blueprint input, as the import screen offers it. */
export interface ModuleInputRow {
  name: string;
  title: string;
  description: string;
  default: unknown;
  /** Whether the blueprint gives it a default, so it may be left unfilled. */
  has_default: boolean;
  /** Whether its selector takes more than one value. */
  multiple: boolean;
  /** Whether the person has filled it in this session. */
  bound: boolean;
  value: unknown;
  /** Whether the source can be built at all without another decision. */
  satisfied: boolean;
  /**
   * Whether the **trigger** names this input -- the field it is matched by, not
   * a field it reads.
   *
   * The one place a *template* cast cannot go, and it fails silently rather than
   * loudly: Home Assistant matches a trigger's `entity_id` against the entities
   * the house actually has instead of rendering it, so a template written there
   * matches nothing and the automation installs and never fires, saying nothing
   * while it does not. Everywhere else -- an action's `entity_id`, a service
   * call's data, a variable -- the value is rendered on every run.
   *
   * A **condition** cast works here and is the reason it exists: Open House
   * evaluates the condition itself, publishes the answer as an entity
   * (`binary_sensor.open_house_<slug>_<input>`) and binds the input to *that*, so
   * what the trigger is matched against is a real entity after all.
   */
  in_trigger: boolean;
  /** `entity`, `target`, `number`, `boolean`, `select`, `action`, `text`, ... */
  selector: string;
  /** A `select` input's menu, empty for every other kind. */
  options: string[];
  /**
   * How the module's setting is filled: `literal`, `entity`, `output`, `slot`,
   * or empty for one left on the blueprint's own default. Only meaningful on a
   * module's `settings` rows, where it is what tells an editable value from one
   * something else supplies -- another module's reading, or the device its room
   * binds.
   */
  bound_kind?: string;
  /** What supplies it, when the module does not: `<module>/<key>`, or a slot. */
  bound_to?: string;
  /**
   * The **condition** this input was answered with, when it was one: Home
   * Assistant's own condition config, exactly as the person built it, or
   * `undefined` for an input answered any other way.
   *
   * It is what the card opens on, because the condition is the thing the person
   * authored. What the input is *bound to* is the entity Open House made out of
   * it -- `bound_kind` is `condition` and `bound_to` is that entity id -- and
   * that is not theirs to edit here: it is the machinery, and the condition is
   * the answer.
   */
  cast?: unknown;
  /**
   * The **flow** this input is answered by, by its id in Node-RED, or `""`.
   *
   * Set on a setting whose answer is a flow rather than a value: `bound_kind`
   * is `flow` and `bound_to` is the entity the flow writes
   * (`sensor.open_house_flow_<slug>_<input>`), which the input is bound to and
   * which nobody edits here. What is theirs is the flow, and this is the id the
   * card opens Node-RED on.
   */
  flow_id?: string;
  /** That flow, opened in the Node-RED editor, or `""` when none is configured. */
  flow_url?: string;
  /**
   * The **script** this input is answered by, as the id a `script.` call takes
   * (`turn_it_on`), or `""`.
   *
   * Without the domain, because that is what the call is built from and what the
   * card holds: the panel strips it on the way in and puts it back for the
   * picker, which names whole entities. Set on a setting whose answer is a script
   * rather than a value: `bound_kind` is `script` and `bound_to` is that script's
   * entity id, which is where the answer *comes from* and not something anybody
   * edits here. What is theirs is the script, and this is the id the card opens
   * Home Assistant's script editor on.
   *
   * A script returns rather than holds -- `stop:` with `response_variable` is how
   * one hands a value back -- which is what makes it a cast and not just another
   * thing that writes an entity.
   */
  script_id?: string;
  /** That script, opened in Home Assistant's editor, or `""`. */
  script_url?: string;
  /**
   * What this row's own logic is **published as**, or `""` for one that is not.
   *
   * A row answered with logic holds a value worth reading, and publishing it puts
   * that value at `sensor.open_house_<slug>_<key>` -- an ordinary entity, which
   * is what makes it usable by any automation rather than only inside the module
   * that worked it out. This is the key it was published under, which is what the
   * switch on the row reads to know whether it is on, and what the card names in
   * the sentence it writes under it.
   *
   * Keyed by the row rather than by the key: the key is what a person called the
   * value and could be anything, while this answers "is *this* row's logic
   * published", which is the question the switch asks.
   */
  published_key?: string;
}

/**
 * One thing the source carries that could become an output.
 *
 * `kind` is where it came from -- the blueprint's own `variables:`, an entity
 * input it reads, a `response_variable` a call handed back, a service call that
 * drove a device, or a row the person answered with logic (`cast`) -- which is
 * what tells a person whether it will report on every run or only inside a
 * branch.
 */
export interface ModuleCandidate {
  name: string;
  kind: "variable" | "entity" | "response" | "service" | "cast";
  /** A guess at the value kind, from the shape of the definition. */
  value_kind: string;
  expression: string;
  /** Set inside a branch, so only in scope there. */
  branch_only: boolean;
  /** A name a person could call the output; empty when none can be made. */
  suggested_key: string;
}

/** How one of a source's inputs is filled at import. */
export interface ModuleBinding {
  kind: "literal" | "entity" | "output" | "slot";
  value?: unknown;
  /** For an `output` binding: the module that publishes it. */
  module?: string;
  /** For an `output` binding: the output's own key. */
  key?: string;
  /**
   * For a `slot` binding: the name of the slot this input reaches through.
   *
   * The fourth answer, and the only one that names a *role* rather than a
   * device: "the room's lux sensor" instead of one particular sensor. It
   * resolves against the room the module sits in, so the same module imported
   * into two rooms watches two different devices.
   */
  slot?: string;
  /**
   * For a `slot` binding: where the slot is looked up.
   *
   * Absent (and the only meaning before global slots existed) is the module's
   * own room, with the house's global binding standing behind it. `"house"` is a
   * **global slot**: the house's own binding and only that, so it means the same
   * device in every room -- including a room that bound the role itself.
   */
  scope?: "room" | "house";
  /**
   * For a `slot` binding: which part of a split slot this module is on.
   *
   * A slot a person has divided into parts (`ha_adapter.slot_parts`) is several
   * binding keys under one name, so two modules on part `a` act on one device
   * while a third on part `b` acts on another -- and all of them are still
   * "the slot". Absent, or empty, is the slot itself, which is what every
   * module is on until somebody splits it.
   */
  part?: string;
}

/** The `modules/read` reply: the source, what it could publish, and the house. */
export interface ModuleReadReply {
  source: {
    title: string;
    description: string;
    blueprint: string;
    inputs: number;
  };
  inputs: ModuleInputRow[];
  candidates: ModuleCandidate[];
  /**
   * Every slot this house carries, which is what an input may be answered with.
   *
   * The whole of the closed vocabulary a person may choose from -- the catalog's
   * words plus whatever the installed packs declare of their own -- and the same
   * set the bind gate accepts, so a name offered here is a name a room can be
   * given a device for. A name that is not here is a name this house has no slot
   * called, which is what typing a new one at import would be refused for.
   */
  slots: ModuleSlotWord[];
  /** The rooms a module can be imported into, for the picker. */
  rooms: { id: string; name: string }[];
  /**
   * The licence codes a module may be saved under, least to most restrictive.
   *
   * Offered by the server rather than restated here, for the reason the slot
   * names are: a module that travels to another house is a published thing, and
   * the codes are a vocabulary with one spelling.
   */
  licences: string[];
  /** Every module the house already hosts, to bind an input from. */
  hosted: HostedModule[];
  /**
   * The module's own document, when the read named one.
   *
   * Present only on a reading *about a module* -- the Edit screen -- because it
   * can be a whole blueprint and there is no reason to send it with every
   * listing of the house. It is what the screen hands back on save, so a module
   * edited twice in a row keeps the same document rather than a re-encoded copy
   * of it.
   */
  text?: string;
  /** What was decided about the module being edited, when one was named. */
  editing?: ModuleEditSeed;
}

/**
 * The import screen's starting point when it is opened *on* a module.
 *
 * The menu is the same one a fresh import draws; this is the half of it that a
 * fresh import does not have -- the module's own document and the answers that
 * were given about it. Read from the store row the module was made from where
 * there is one, and from the installation itself for a document hosted directly,
 * because that is the only difference between the two: a module the house offers
 * and one it merely runs.
 */
export interface ModuleEditSeed {
  /** The name the *house* hosts it under: what the edit is sent back to. */
  module: string;
  /** The store row it was made from, empty for a document hosted directly. */
  definition: string;
  /** The document itself, which the screen sends back unchanged on save. */
  text: string;
  title: string;
  description: string;
  author: string;
  version: string;
  licence: string;
  blueprint: string;
  /** The module's own answers: what each input is filled with by default. */
  bindings: Record<string, ModuleBinding>;
  /** Which of those inputs the module keeps settable. */
  settings: string[];
  /** The inputs the module answers with a condition, as it built them. */
  casts: Record<string, unknown>;
  /**
   * The inputs the module answers by a Node-RED flow, by name, and the id of
   * the flow each is answered by in *this* house's Node-RED -- `""` for a name
   * the installation in front of the person has not pushed yet.
   */
  flows: Record<string, string>;
  /**
   * The inputs the module answers with a script, by name, and the id of the
   * script each is answered by -- `""` for a name the module's definition
   * carries and this house has not picked a script for.
   */
  scripts: Record<string, string>;
  /** The output candidates the module was told to publish, each with its key. */
  picks: { name: string; key: string }[];
  /**
   * Every installation the save will rebuild, which is the thing a person about
   * to press it is owed: an edit is the module, and the module is all of these.
   */
  installs: { slug: string; room_id: string; room_name: string }[];
}

/**
 * One module this house *offers*: a blueprint saved as a reusable definition.
 *
 * The document itself is not here -- it is the file, and it can be a whole
 * blueprint. What is here is what a row is drawn from and what a person needs to
 * decide whether to install the module or to send it to somebody else.
 */
export interface ModuleOfferRow {
  /** The definition's name: the store row's id and its file's name. */
  slug: string;
  title: string;
  description: string;
  author: string;
  version: string;
  licence: string;
  /** The blueprint it was made from, for provenance. */
  blueprint: string;
  /**
   * Whether the module names devices from the house that defined it.
   *
   * An input answered with a device is one house's device, so a pinned module
   * installs here and not in somebody else's house; one answered with slots is
   * reusable anywhere. This is the one thing the sender of a file knows and the
   * receiver cannot see, which is why the store says it.
   */
  pinned: boolean;
  /** The slots it reaches through, which is what an installing room binds. */
  slots: string[];
  /**
   * The inputs it answers with a **Node-RED flow**, by name.
   *
   * A fact about the definition rather than about any one installation: which
   * inputs are answered by a flow is a property of the module, so it travels
   * with the file and every house that installs it pushes its own flow for each
   * of these. The flow's *id*, unlike its name, belongs to the installing house.
   */
  flows: string[];
  /**
   * The inputs it answers with a **script**, by name.
   *
   * The same split as the flows, and the same reason: which inputs are answered
   * by a program is a property of the module, so the *names* travel with the
   * file -- while the `script.<id>` belongs to the Home Assistant that picked
   * it, and that house names its own script when it installs this.
   */
  scripts: string[];
  /**
   * The ones the placement being asked about has no device for.
   *
   * Empty when the question was asked about the house, where the answer is the
   * union of every room's bindings -- a house that binds `light_group` anywhere
   * answers it. The verdict comes from the server for the same reason a pack's
   * does: "can this room answer this module" is a fact about the house, and a
   * screen that worked it out would be a second implementation of it.
   */
  missing_slots: string[];
  /** Where this definition is installed, one row per installation. */
  deployed: {
    slug: string;
    room_id: string;
    room_name: string;
    /** Whether its automation is running, or it is waiting on a slot. */
    running: boolean;
  }[];
}

/** One slot the house carries, as the import screen offers it. */
export interface ModuleSlotWord {
  /** The name as it is written: `ambient_light_sensor`. */
  name: string;
  /** The name as a person reads it: `Ambient light sensor`. */
  label: string;
  /**
   * Whether the *house* answers this role -- a slot at house scope.
   *
   * It is the difference an import asks about: naming a room-scoped slot means
   * "whatever this module's room binds", and naming a global one means "the
   * house's own device, in every room". Only a role the house resolves can be
   * named the second way, which is why the server states it rather than the
   * screen inferring it from the name.
   */
  house_scope: boolean;
}
