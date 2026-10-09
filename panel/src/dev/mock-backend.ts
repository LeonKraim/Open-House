/**
 * A mock of the integration, for `npm run dev` and for looking at the panel.
 *
 * It answers the commands in `protocol.ts` from a fixed house, so the panel can
 * be developed and reviewed without Home Assistant, without the `open_house`
 * integration and without a pack installed. This is a development aid and is not
 * part of the bundle's entry point, so none of it ships.
 *
 * Its answers are typed against the same view models the real server returns --
 * `import type` only, erased at build -- so a mock that disagreed with the
 * models would be a type error rather than a screen that renders blank.
 */

import type {
  ActivityStreamEvent,
  BindingStatus,
  BindingSuggestion,
  Capabilities,
  DecisionLogEntry,
  DevAnalysis,
  DevSaved,
  DevSlot,
  DevSource,
  HealthIssue,
  HostedModule,
  HouseOverview,
  HouseScope,
  InstalledModule,
  ModuleBinding,
  ModuleCandidate,
  ModuleEditSeed,
  ModuleInputRow,
  ModuleInstallReply,
  ModuleOffer,
  ModuleOfferRow,
  ModuleReadReply,
  ModuleSlot,
  ModuleSlotRuleKind,
  ModuleSlotWord,
  ProfileRef,
  RoomDetail,
  RoomSummary,
  SlotRuleFacts,
  StoreEntry,
} from "../api/models.ts";
import type { JsonSchema } from "../components/schema-spec.ts";
import { COMMANDS, REFUSALS } from "../api/protocol.ts";
import type { HaConnection, HassLike, UnsubscribeFunc } from "../api/connection.ts";

const CAPABILITIES: Capabilities = {
  admin: true,
  user_name: "Ada",
  version: "0.0.0-dev",
  engine_api: "1.0.0",
  needs_setup: false,
};

const ROOMS: RoomSummary[] = [
  {
    id: "kitchen",
    name: "Kitchen",
    type: "kitchen",
    type_label: "Kitchen",
    bound_slots: 2,
    total_slots: 3,
    required_unbound: ["ceiling_light"],
    mode: "home",
    active_profiles: { lighting: "evening" },
    occupied: true,
    auto_lighting: true,
    issue_count: 1,
  },
  {
    id: "bedroom",
    name: "Bedroom",
    type: "bedroom",
    type_label: "Bedroom",
    bound_slots: 3,
    total_slots: 3,
    required_unbound: [],
    mode: "home",
    active_profiles: { lighting: "dim" },
    occupied: false,
    auto_lighting: true,
    issue_count: 0,
  },
];

/**
 * The mock's profiles, as the documents the server trades in.
 *
 * Documents rather than rows, because export and import are the point: a mock
 * that held only the panel's `ProfileRef` could not answer `profiles/export`
 * with anything the importer would read back. The panel rows are derived from
 * these on every read (`profileRef`), so the two can never disagree.
 */
const PROFILE_DOCUMENTS: Record<string, Record<string, unknown>> = {
  evening: {
    name: "evening",
    kind: "room",
    axis: "lighting",
    description: "Warm, dimmed lighting for the evening.",
  },
  dim: {
    name: "dim",
    kind: "room",
    axis: "lighting",
    description: "Low brightness for bedtime.",
  },
  vacation: {
    name: "vacation",
    kind: "house",
    description: "Presence simulation while nobody is home.",
    selections: {},
  },
};

/** Which room profiles the mock house is on. */
const ACTIVE_PROFILE_NAMES = new Set(["evening", "dim"]);

/** The house profile in force, if one is. */
let houseProfile: string | null = null;

/**
 * How many times the mock house's profiles have moved.
 *
 * The mock's half of the server's `LiveSession.revision`: sent on every page
 * that a person renders controls from, and echoed back by the writes those
 * controls make. A switch moves it, and a write carrying the revision from
 * before the switch is refused with `stale_page` -- which is what lets the dev
 * harness exercise the disabled-page notice without a second Home Assistant.
 */
let profilesRevision = 0;

/**
 * Refuse a write that was decided against a house which has since moved.
 *
 * The mock's stand-in for the server's `_stale` guard, and it is deliberately
 * the same rule: a write *with* a revision must match, and one with no revision
 * at all is let through, because "I am not rendering a page" is a real caller.
 */
function guardStale(payload: Record<string, unknown>): void {
  const sent = payload.revision;
  if (typeof sent !== "number" || sent === profilesRevision) return;
  throw refuse(
    REFUSALS.stalePage,
    "This page was read before the house's profiles moved, so its answers " +
      "belong to a profile that is no longer in force.",
  );
}

/**
 * A refusal in the shape the client reads: a code, and a sentence for a person.
 *
 * The mock threw bare `Error`s for the input a *server* would refuse -- a room
 * that is not there, a profile already held -- and the client maps a plain
 * `Error` to the code `unknown`, so the panel rendered "Something went wrong"
 * over a message the fixture had written out in full for exactly that case.
 * `guardStale` above and the refusals below both go through here now, so a
 * refusal the mock makes is a refusal the panel draws as one.
 */
function refuse(code: string, message: string): unknown {
  return { code, message };
}

/** A name as the id the panel will address it by: lower case, underscores. */
function slugOf(text: string): string {
  const slug = text
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "");
  return slug === "" ? "room" : slug;
}

/** A room type as a person reads it: `living_room` -> `Living room`. */
function labelOf(type: string): string {
  const words = type.split("_").filter((word) => word !== "");
  if (words.length === 0) return "";
  return [words[0]!.charAt(0).toUpperCase() + words[0]!.slice(1), ...words.slice(1)].join(
    " ",
  );
}

/** Every profile as the panel's row, newest state included. */
/** The rooms a store row's picker offers, as the server sends them. */
function roomsOf(rooms: RoomSummary[]): { id: string; name: string }[] {
  return rooms.map((room) => ({ id: room.id, name: room.name }));
}

function profileRows(): ProfileRef[] {
  return Object.values(PROFILE_DOCUMENTS)
    .map(profileRef)
    .sort((left, right) => left.name.localeCompare(right.name));
}

function profileRef(document: Record<string, unknown>): ProfileRef {
  const name = String(document.name);
  const words = name.split("_");
  return {
    name,
    label: words
      .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
      .join(" "),
    description: String(document.description ?? ""),
    kind: document.kind === "house" ? "house" : "room",
    axis: document.axis === undefined ? null : String(document.axis),
    active: houseProfile === name || ACTIVE_PROFILE_NAMES.has(name),
  };
}

/** The profile documents a file holds, in either of the two accepted forms. */
function profileDocuments(document: unknown): Record<string, unknown>[] {
  if (typeof document !== "object" || document === null) {
    throw refuse(REFUSALS.invalidFormat, "the document is not an object");
  }
  const held = (document as Record<string, unknown>).profiles;
  if (held === undefined) return [document as Record<string, unknown>];
  if (!Array.isArray(held)) {
    throw refuse(REFUSALS.invalidFormat, "the document's 'profiles' is not a list");
  }
  return held as Record<string, unknown>[];
}

/**
 * One slot a fixture's pack reaches, before any device is resolved.
 *
 * The fixture states the slot's identity and what the pack declares about it --
 * that it is required, and that is all -- and leaves the device facts empty.
 * Which device the slot points at is the room's binding or the house's, and it
 * is resolved on every read (`moduleSlots`), so a fixture cannot claim a
 * fallback the room's own page does not have.
 */
function slotRow(slot: string, flags: { required?: boolean } = {}): ModuleSlot {
  return {
    slot,
    label: slot,
    declared_label: slot,
    entity_id: null,
    default_entity_id: null,
    overridden: false,
    named: false,
    required: flags.required ?? false,
    separate: slot.includes("__"),
    // A fixture's slots are the pack's own devices rather than house roles, and
    // the catalog's domains are the server's half: a fixture that invented one
    // would be claiming a type nothing declared.
    accepts_domains: [],
    house_scope: false,
    bound: false,
    friendly_name: null,
    domain: null,
    state: null,
    status: "unbound",
    // A fixture starts on no part of a split slot and on a slot nobody has split:
    // `moduleSlots` and the parts session below are where either is set.
    part: null,
    part_label: null,
    parts: [],
    // A fixture starts with no logic on any slot; `moduleSlots` overlays whatever
    // was set this session.
    ...noRule(),
  };
}

const MODULES: InstalledModule[] = [
  {
    pack: "bedtime_button",
    name: "Bedtime button",
    version: "1.0.0",
    room_id: "bedroom",
    house: false,
    scope: "room",
    enabled: true,
    // Nothing holds the holder off, so this one is the suppressor rather than
    // the suppressed; `withReach` derives both fields on every read.
    suppressed_by: null,
    suppressed_behaviour: null,
    satisfiable: true,
    missing_slots: [],
    // The settings this module owns. The bedtime button's are its reach boxes:
    // the pack declares no numbers, and "shut the lights, leave the thermostats"
    // is one box per role its atoms act through.
    option_keys: [
      "module.bedtime_button.reach.light_group",
      "module.bedtime_button.reach.climate_zone",
      "module.bedtime_button.reach.lock",
    ],
    options_schema: {
      type: "object",
      title: "Bedtime button",
      properties: {
        "module.bedtime_button.reach.light_group": {
          type: "boolean",
          title: "Lights",
          description: "Whether this module addresses the house's lights.",
          default: true,
        },
        "module.bedtime_button.reach.climate_zone": {
          type: "boolean",
          title: "Thermostat",
          description: "Whether this module drops the thermostat for the night.",
          default: true,
        },
        "module.bedtime_button.reach.lock": {
          type: "boolean",
          title: "Locks",
          description: "Whether this module locks the doors.",
          default: true,
        },
      },
    },
    options: {
      "module.bedtime_button.reach.light_group": true,
      "module.bedtime_button.reach.climate_zone": true,
      "module.bedtime_button.reach.lock": true,
    },
    behaviours: [
      {
        id: "lights_off",
        label: "Lights off",
        description: "When the bedtime button is pressed, it sets the Light group to off.",
        enabled: true,
        // Widened to the house, so it runs once above the rooms and in none of
        // them -- the same answer `Engine.active_rooms` gives for a house-scoped
        // atom. The fixture's `enabled` is only the seed for the per-room flag.
        active_rooms: [],
        scope: "house",
        declared_scope: "house",
        widenable: true,
        // `packs/official/bedtime.yaml`: the lights rank above the sleep-mode
        // switch, so a rival pack's lighting atom loses to this one's.
        priority: 50,
        default_priority: 50,
        priority_set: false,
      },
      {
        id: "sleep_mode",
        label: "Sleep mode",
        description: "When the bedtime button is pressed, it puts the house in Sleep mode.",
        enabled: true,
        active_rooms: [],
        scope: "house",
        declared_scope: "house",
        widenable: true,
        priority: 10,
        default_priority: 10,
        priority_set: false,
      },
    ],
    // `packs/official/bedtime.yaml`: `requires_slots: [light_group]`,
    // `optional_slots: [climate_zone, lock]`.
    slots: [
      slotRow("light_group", { required: true }),
      slotRow("climate_zone"),
      slotRow("lock"),
    ],
  },
  {
    pack: "motion_lighting",
    name: "Motion lighting",
    version: "1.2.0",
    room_id: "kitchen",
    house: false,
    scope: "room",
    enabled: true,
    // The fixture declares no suppression over this pack, so nothing holds it
    // off; `withReach` derives both fields on every read anyway.
    suppressed_by: null,
    suppressed_behaviour: null,
    satisfiable: true,
    missing_slots: [],
    option_keys: [
      "module.motion_lighting.motion_hold_seconds",
      "module.motion_lighting.lux_threshold",
      "module.motion_lighting.night_mode",
      "module.motion_lighting.level",
      "module.motion_lighting.reach.light_group",
    ],
    options_schema: {
      type: "object",
      title: "Motion lighting",
      properties: {
        "module.motion_lighting.motion_hold_seconds": {
          type: "number",
          title: "Motion hold",
          description: "How long the light stays on after the last motion, in seconds.",
          default: 120,
          minimum: 5,
          maximum: 3600,
        },
        "module.motion_lighting.lux_threshold": {
          type: "number",
          title: "Lux threshold",
          description: "Only light the room when the lux reading is below this.",
          default: 80,
          minimum: 0,
          maximum: 1000,
        },
        "module.motion_lighting.night_mode": {
          type: "boolean",
          title: "Night mode",
          description: "Dim the light instead of turning it fully on after dark.",
          default: false,
        },
        "module.motion_lighting.level": {
          type: "integer",
          title: "Brightness",
          description: "The brightness to set, as a percentage.",
          default: 100,
          minimum: 1,
          maximum: 100,
        },
        "module.motion_lighting.reach.light_group": {
          type: "boolean",
          title: "Lights",
          description: "Whether this module addresses the room's lights.",
          default: true,
        },
      },
    },
    options: {
      "module.motion_lighting.motion_hold_seconds": 120,
      "module.motion_lighting.lux_threshold": 80,
      "module.motion_lighting.night_mode": false,
      "module.motion_lighting.level": 100,
      "module.motion_lighting.reach.light_group": true,
    },
    behaviours: [
      {
        id: "lux_motion",
        label: "Lux-gated motion",
        description:
          "When the Motion sensor changes to on and the Lux sensor is below the “Lux threshold” setting, it sets the Light group to on.",
        enabled: true,
        // Switched on in the room it sits in; ticking a second room adds one
        // more id here without a second install.
        active_rooms: ["kitchen"],
        scope: "room",
        declared_scope: "room",
        widenable: true,
        // The pack declares none, so the catalog's default applies
        // (`catalog/pack-policy.yaml`: `default_priority: 0`).
        priority: 0,
        default_priority: 0,
        priority_set: false,
      },
    ],
    // The motion atom's roles: the reading it watches, the light level it is
    // gated on, and the light it switches.
    slots: [
      slotRow("motion_sensor", { required: true }),
      slotRow("ambient_light_sensor"),
      slotRow("light_group"),
    ],
  },
];

const OFFERS: ModuleOffer[] = [
  {
    pack: "bathroom_fan",
    name: "Bathroom fan",
    description: "Runs the fan while humidity is high, then for a cooldown.",
    version: "1.0.0",
    kind: "module",
    license: "mit",
    i18n: {},
    requires_slots: ["ceiling_light", "humidity_sensor"],
    optional_slots: ["fan_switch"],
    satisfiable: false,
    missing_slots: ["ceiling_light"],
    optional_slots_present: [],
    conflicts: [],
    already_installed: false,
    options_schema: {
      type: "object",
      title: "Bathroom fan",
      properties: {
        humidity_threshold: {
          type: "number",
          title: "Humidity threshold",
          description: "Run the fan above this percentage.",
          default: 65,
          minimum: 40,
          maximum: 95,
        },
        cooldown_minutes: {
          type: "number",
          title: "Cooldown minutes",
          default: 10,
        },
      },
    },
    behaviours: [{ id: "humidity_fan", label: "Humidity fan", priority: 20 }],
  },
  {
    pack: "roomba_button",
    name: "Roomba button",
    description: "A dashboard button that starts, pauses and docks the vacuum.",
    version: "1.1.0",
    kind: "module",
    license: "apache_2_0",
    i18n: {},
    requires_slots: [],
    optional_slots: ["vacuum"],
    satisfiable: true,
    missing_slots: [],
    optional_slots_present: [],
    conflicts: [
      {
        kind: "slot_contention",
        pack: "motion_lighting",
        installed_version: "1.2.0",
        detail: "Both want to control vacuum.kitchen.",
        severity: "warning",
      },
    ],
    already_installed: false,
    options_schema: null,
    behaviours: [{ id: "vacuum_button", label: "Vacuum button", priority: null }],
  },
];

/**
 * The parts each slot has been split into, this session.
 *
 * One record for the whole house, which is where the server keeps it: a part is
 * a role's half and the split is the *house's*, so every room's page draws the
 * same halves and only the devices differ. Seeded with two so the controls are on
 * screen in `npm run dev`, and written by `slotSetParts`.
 *
 * **Declared up here, above the first fixture that reads them, and that is not
 * tidiness.** `HOUSE_SCOPE` below is a `const` initialised at module evaluation
 * and it asks `partRows` for its slot rows; `partRows` is a function declaration
 * and therefore hoisted, but `SLOT_PARTS` is a `const` like `HOUSE_SCOPE` and was
 * declared *after* it. Reading it from inside a fixture that runs first threw
 * `Cannot access 'SLOT_PARTS' before initialization` at import time -- so the
 * whole mock module failed to load, and it failed for every caller: `mockHass()`
 * was never reached, and no test had ever imported the file to notice.
 */
const SLOT_PARTS: Record<string, string[]> = {
  light_group: ["a", "b"],
  ceiling_light: ["left", "right"],
};

/** Which part of a split slot one module is on, by override key. */
const SLOT_MEMBER: Record<string, string> = {};

/** What each part is bound to, by the part's own key (`light_group__a`). */
const SLOT_PART_BINDINGS: Record<string, string | null> = {
  "light_group__a": "light.kitchen_lights",
  "light_group__b": null,
  "ceiling_light__left": "light.bedroom_left",
  "ceiling_light__right": null,
};

/**
 * The house's own page, for the House tab.
 *
 * A hand-written answer rather than one derived from `ROOMS`, because the point
 * of the mock is to be read: a person looking at this tab in `npm run dev`
 * should see the slots, the options and the module rows the real server sends,
 * with the house-placed module (`house: true`) among them so the "the house"
 * chip is on screen.
 */
const HOUSE_SCOPE: HouseScope = {
  name: "Ada's house",
  // Only the roles a module reaches, and each one a *binding*: the global device
  // the house's automations act through, not every room's separate one. The
  // door_contact row is deliberately unbound with rooms of its own, which is the
  // state a person opens this tab to fix.
  slots: [
    {
      slot: "light_group",
      label: "Light group",
      required: false,
      accepts_domains: ["light"],
      entity_id: "light.house_lights",
      registry_id: null,
      friendly_name: "All the lights",
      domain: "light",
      state: "on",
      status: "ok",
      rooms: ["Kitchen", "Bedroom"],
      modules: ["Motion lighting", "Bedtime button"],
      // A split role, so the house tab's part controls are on screen in
      // `npm run dev`: two halves bound to two devices, both of them lights.
      parts: partRows("light_group", ["light"], ""),
    },
    {
      slot: "door_contact",
      label: "Door contact",
      required: false,
      accepts_domains: ["binary_sensor"],
      entity_id: null,
      registry_id: null,
      friendly_name: null,
      domain: null,
      state: null,
      status: "unbound",
      rooms: ["Kitchen"],
      modules: ["Bedtime button"],
      parts: [],
    },
  ],
  modules: [
    ...MODULES,
    {
      pack: "house_fan",
      name: "Whole-house fan",
      version: "1.0.0",
      room_id: "",
      house: true,
      scope: "house",
      enabled: false,
      suppressed_by: null,
      suppressed_behaviour: null,
      satisfiable: false,
      missing_slots: ["humidity_sensor"],
      option_keys: [
        "module.house_fan.humidity_threshold",
        "module.house_fan.cooldown_minutes",
      ],
      options_schema: {
        type: "object",
        title: "Whole-house fan",
        properties: {
          "module.house_fan.humidity_threshold": {
            type: "number",
            title: "Humidity threshold",
            description: "Run the fan above this percentage.",
            default: 65,
            minimum: 40,
            maximum: 95,
          },
          "module.house_fan.cooldown_minutes": {
            type: "integer",
            title: "Cooldown",
            description: "How long the fan keeps running afterwards, in seconds.",
            default: 600,
            minimum: 0,
            maximum: 3600,
          },
        },
      },
      options: {
        "module.house_fan.humidity_threshold": 65,
        "module.house_fan.cooldown_minutes": 600,
      },
      behaviours: [
        {
          id: "humidity_fan",
          label: "Humidity fan",
          description:
            "When the Humidity sensor reads above the “Humidity threshold” setting, it sets the Fan to on, then lets it run for the “Cooldown” setting after the humidity falls.",
          enabled: false,
          active_rooms: [],
          scope: "house",
          declared_scope: "house",
          widenable: true,
          // The same act `bathroom_fan` performs, and the same rank.
          priority: 20,
          default_priority: 20,
          priority_set: false,
        },
      ],
      // The reading that gates the whole module, and the fan it runs -- and
      // `humidity_sensor` is what the fixture leaves unbound, which is why the
      // module is drawn unsatisfiable.
      slots: [
        slotRow("humidity_sensor", { required: true }),
        slotRow("fan"),
      ],
    },
  ],
  options_schema: {
    type: "object",
    title: "House options",
    properties: {
      "module.house_fan.humidity_threshold": {
        type: "number",
        title: "Humidity threshold",
        description: "Run the fan above this percentage.",
        default: 65,
        minimum: 40,
        maximum: 95,
      },
      "module.house_fan.cooldown_minutes": {
        type: "integer",
        title: "Cooldown",
        description: "How long the fan keeps running afterwards, in seconds.",
        default: 600,
        minimum: 0,
        maximum: 3600,
      },
    },
  },
  options: {
    "module.house_fan.humidity_threshold": 65,
    "module.house_fan.cooldown_minutes": 600,
  },
  // Placeholder: `houseScopeView` is what every caller reads, and it answers the
  // live revision rather than this one. The field is here because the fixture is
  // typed as the whole page.
  revision: 0,
};

/**
 * The house's option values, as a person has set them in this dev session.
 *
 * The fixture's `options` is the seed; this is what a Save writes into, so the
 * House tab's form round-trips in `npm run dev` the way it does against the
 * real server. A mock that answered every read with the seed would make a
 * correct save look like it silently failed.
 */
let houseOptions: Record<string, unknown> = { ...HOUSE_SCOPE.options };

/**
 * Every installed module, wherever it sits, as this dev session leaves it.
 *
 * One list of *placements* rather than a list of packs: the same pack in the
 * Kitchen and the Hall is two entries, because that is what the engine holds
 * and what a behaviour's reach is read from. Installing appends, uninstalling
 * filters, and flipping one atom's scope rewrites the one placement it names --
 * so a mock that answered these and then re-listed a fixed array would make
 * every successful edit look like it had silently done nothing, which is the
 * failure mode the panel's own reload exists to avoid.
 */
let installed: InstalledModule[] = [...HOUSE_SCOPE.modules];

/**
 * Where each atom is switched on, keyed `<pack>/<behaviour>` -> room ids.
 *
 * The engine holds a room-scoped atom's enable flag per room, so a reach is a
 * *set* of rooms and not a field on one placement: ticking the Kitchen and the
 * Hall is one module with its flag on in both. This is the mock's copy of that
 * set — seeded from the fixtures (a room-scoped atom from its own
 * `active_rooms`, any atom the fixture marks enabled from the room it sits in)
 * and written by `moduleSetBehaviourEnabled`. `withReach` answers every read
 * from it, so a tick survives the reload the panel always makes afterwards; a
 * mock that re-listed the fixtures would make every tick spring back.
 */
const ATOM_ROOMS: Record<string, Set<string>> = {};

/** The rooms one atom is switched on in, creating its set on first sight. */
function atomRooms(pack: string, behaviour: string): Set<string> {
  return (ATOM_ROOMS[`${pack}/${behaviour}`] ??= new Set<string>());
}

/**
 * The temporary override the fixture declares: bedtime holds motion lighting off.
 *
 * The mock's copy of `pack-manifest`'s `suppresses` clause, and seeded with one
 * entry rather than left empty because a page nobody can see the feature on is a
 * page nobody can review: the Modules tab in `npm run dev` draws the red panel
 * over the motion-lighting card, and switching the bedtime module off makes it
 * disappear -- which is the whole semantics, demonstrated rather than described.
 *
 * Derived on read (`suppressorOf`) and never stored, exactly as the engine
 * derives it: nothing is written against the target, so the target's own switch
 * reads back where the fixture left it and the release is a consequence of the
 * holder going off rather than a second write.
 */
const SUPPRESSES: Record<string, { pack: string; behaviour: string }> = {
  bedtime_button: { pack: "motion_lighting", behaviour: "bedtime_button.lights_off" },
};

/** The module holding `pack` off right now, or `null`. */
function suppressorOf(pack: string): { pack: string; behaviour: string } | null {
  for (const [holder, target] of Object.entries(SUPPRESSES)) {
    if (target.pack !== pack) continue;
    const module = installed.find((row) => row.pack === holder);
    if (module === undefined || !module.enabled) continue;
    const atom = module.behaviours.find((row) => row.id === target.behaviour);
    if (atom?.enabled === true) return target;
  }
  return null;
}

/**
 * The ranks a person in this session has chosen, `<pack>/<behaviour>` -> number.
 *
 * Only the *chosen* ranks are held here; the declared one stays on the fixture
 * row as `default_priority`, which is what "reset" returns to and what the
 * server reports as `priority` until somebody edits it. An absence is therefore
 * meaningful -- it is exactly the server's `priority_set: false` -- so a map
 * seeded with the declared ranks would erase the distinction the control reads.
 * `withReach` overlays this on every read, which is what makes a typed rank
 * survive the reload the panel always makes afterwards.
 */
const PRIORITY_OVERRIDES: Record<string, number> = {};

for (const module of [...MODULES, ...HOUSE_SCOPE.modules]) {
  for (const behaviour of module.behaviours) {
    const rooms = atomRooms(module.pack, behaviour.id);
    if (behaviour.scope !== "house") {
      for (const room of behaviour.active_rooms) rooms.add(room);
    }
    // A fixture row that is switched on is on in the room it sits in, whether
    // or not it declares `active_rooms` -- the enabled flag is per room too.
    if (behaviour.enabled && module.room_id !== "") rooms.add(module.room_id);
  }
}

/**
 * One module as the panel reads it: every atom's reach and switch derived.
 *
 * A read-time overlay, not a rewrite of the stored list: `enabled` is "on in the
 * room the module sits in" and `active_rooms` is the whole set, both read from
 * `ATOM_ROOMS`. Kept out of `installed` so the raw placements stay comparable by
 * identity in `updatePlacement`.
 */
function withReach(module: InstalledModule): InstalledModule {
  // Mirror `live_modules._reach`: once a room has been ticked the engine's set
  // is the answer, and an atom nobody has decided about applies where its module
  // sits rather than nowhere. "The pack is on somewhere" is the master flag the
  // server reads, approximated here the same way the fixture stores state.
  const packOn = module.behaviours.some(
    (behaviour) => atomRooms(module.pack, behaviour.id).size > 0,
  );
  const holder = suppressorOf(module.pack);
  return {
    ...module,
    suppressed_by: holder?.pack ?? null,
    suppressed_behaviour: holder?.behaviour ?? null,
    // Which device each slot is pointed at is a read, not a fixture: the
    // fallback is the room's or the house's binding and the override is
    // whatever this session wrote, so a row cannot go stale behind a write.
    slots: moduleSlots(module),
    behaviours: module.behaviours.map((behaviour) => {
      const rooms = atomRooms(module.pack, behaviour.id);
      const chosen = PRIORITY_OVERRIDES[`${module.pack}/${behaviour.id}`];
      return {
        ...behaviour,
        // The rank now, and whether a person is the one who set it -- read from
        // one place, so the number and the Reset button can never disagree.
        priority: chosen ?? behaviour.default_priority,
        priority_set: chosen !== undefined,
        active_rooms:
          behaviour.scope === "house"
            ? []
            : rooms.size > 0
              ? [...rooms]
              : packOn
                ? []
                : [module.room_id],
        enabled: rooms.has(module.room_id),
      };
    }),
  };
}

/** The installed copy of a pack at one placement: a room id, or `""` for the house. */
function placement(roomId: string, pack: unknown): InstalledModule | undefined {
  return installed.find(
    (module) => module.room_id === roomId && module.pack === pack,
  );
}

/** Rewrite one placement in place, answering the new module. */
function updatePlacement(
  roomId: string,
  pack: unknown,
  change: (module: InstalledModule) => InstalledModule,
): InstalledModule | undefined {
  const before = placement(roomId, pack);
  if (before === undefined) return undefined;
  const after = change(before);
  installed = installed.map((module) => (module === before ? after : module));
  return after;
}

/** The install reply: the module, and the page it landed on. */
function landedReply(landed: InstalledModule, roomId: string): ModuleInstallReply {
  return roomId === ""
    ? { installed: withReach(landed), house: houseScopeView() }
    : { installed: withReach(landed), room: roomDetail(roomId) };
}

/**
 * The house's own binding per slot, as a person has set them in this session.
 *
 * Seeded from the fixture and `null`-able, because unbinding a global slot is a
 * real edit: the row goes back to "nothing bound" and the rooms' own bindings
 * are what the house scope resolves again. A store that could only be written
 * would make Unbind look like it worked and change nothing.
 */
let houseBindings: Record<string, string | null> = Object.fromEntries(
  HOUSE_SCOPE.slots.map((slot) => [slot.slot, slot.entity_id]),
);

/** The House tab's page, from the fixtures and whatever this session changed. */
function houseScopeView(): HouseScope {
  return {
    ...HOUSE_SCOPE,
    slots: HOUSE_SCOPE.slots.map((slot) => {
      const entityId =
        slot.slot in houseBindings ? houseBindings[slot.slot] : slot.entity_id;
      if (entityId === null || entityId === undefined) {
        return {
          ...slot,
          entity_id: null,
          friendly_name: null,
          domain: null,
          state: null,
          status: "unbound" as const,
        };
      }
      const known = CANDIDATES.find((entry) => entry.entity_id === entityId);
      return {
        ...slot,
        entity_id: entityId,
        friendly_name: known?.friendly_name ?? entityId,
        domain: entityId.split(".")[0] ?? null,
        state: "on",
        status: "ok" as const,
      };
    }),
    modules: installed.map(withReach),
    options: houseOptions,
    revision: profilesRevision,
  };
}

/**
 * The settings each pack declares, keyed the way the server keys them.
 *
 * `module.<pack>.<key>` rather than a bare name, because that is the key space
 * the resolver writes into and the option schema is drawn from -- a fixture
 * using `lux_threshold` would render a form whose saves went nowhere, and would
 * hide the fact that the panel groups settings by the prefix of the key rather
 * than by guessing.
 */
const MODULE_SETTINGS: Record<string, Record<string, JsonSchema>> = {
  motion_lighting: {
    "module.motion_lighting.motion_hold_seconds": {
      type: "number",
      title: "Motion hold",
      description: "How long the light stays on after motion stops.",
      default: 120,
    },
    "module.motion_lighting.lux_threshold": {
      type: "number",
      title: "Lux threshold",
      description: "Turn the light on only below this lux.",
      default: 80,
    },
    "module.motion_lighting.night_mode": {
      type: "boolean",
      title: "Night mode",
      description: "Use a dimmer level between 23:00 and 06:00.",
      default: true,
    },
    "module.motion_lighting.level": {
      type: "string",
      title: "Default level",
      enum: ["low", "medium", "high"],
      default: "medium",
    },
    "module.motion_lighting.reach.light_group": {
      type: "boolean",
      title: "Lights",
      description:
        "Whether this module addresses the room's lights. Unticked, it keeps its behaviour and stops writing to them -- from the pack motion_lighting.",
      default: true,
    },
  },
  bedtime_button: {
    "module.bedtime_button.reach.light_group": {
      type: "boolean",
      title: "Lights",
      description:
        "Whether this module addresses the room's lights. Unticked, it keeps its behaviour and stops writing to them -- from the pack bedtime_button.",
      default: true,
    },
    "module.bedtime_button.reach.lock": {
      type: "boolean",
      title: "Lock",
      description:
        "Whether this module addresses the room's locks. Unticked, it keeps its behaviour and stops writing to them -- from the pack bedtime_button.",
      default: true,
    },
  },
};

/** The settings belonging to `packs`, in the order the modules list them. */
function moduleSettings(packs: readonly InstalledModule[]): Record<string, JsonSchema> {
  const properties: Record<string, JsonSchema> = {};
  for (const pack of packs) {
    for (const key of pack.option_keys) {
      const node = MODULE_SETTINGS[pack.pack]?.[key];
      if (node !== undefined) properties[key] = node;
    }
  }
  return properties;
}

const ACTIVITY: DecisionLogEntry[] = [
  {
    id: "e1",
    at: new Date(Date.now() - 60_000).toISOString(),
    room: "kitchen",
    behaviour: "motion_lighting",
    entity_id: "light.kitchen",
    action: "light.turn_on",
    reason:
      "Motion was detected and the lux reading (42) was below the threshold (80), so the light was turned on.",
    priority: 10,
    outcome: "applied",
  },
  {
    id: "e2",
    at: new Date(Date.now() - 120_000).toISOString(),
    room: "kitchen",
    behaviour: "motion_lighting",
    entity_id: "light.kitchen",
    action: "light.turn_on",
    reason:
      "Motion was detected but the room was already at the target brightness, so nothing was sent.",
    priority: 10,
    outcome: "skipped",
  },
];

const HEALTH: HealthIssue[] = [
  {
    severity: "error",
    code: "required_slot_unbound",
    title: "Kitchen is missing a required device",
    detail:
      "The kitchen room type requires ceiling_light, and nothing is bound to it. Motion lighting cannot run without it.",
    room_id: "kitchen",
    entity_id: null,
    repairs_flow_id: "open_house_bind_slot_kitchen_ceiling_light",
  },
  {
    severity: "warning",
    code: "entity_unavailable",
    title: "A bound device is unavailable",
    detail:
      "sensor.bedroom_lux has been unavailable for 12 minutes. Open House treats unavailable as unknown and will not act on it.",
    room_id: "bedroom",
    entity_id: "sensor.bedroom_lux",
    repairs_flow_id: null,
  },
];

const STORE: StoreEntry[] = [
  {
    pack: "bedtime_button",
    name: "Bedtime button",
    description: "Lights off, Sleep mode, optional thermostat.",
    version: "1.0.0",
    author: "open-house",
    tier: "official",
    license: "mit",
    available: true,
    installed_version: "1.0.0",
    update_available: false,
    update_requires_review: false,
    abandoned: false,
    sha256: "a".repeat(64),
  },
  {
    pack: "guest_mode",
    name: "Guest mode",
    description: "A house profile for guests staying over.",
    version: "1.1.0",
    author: "open-house",
    tier: "official",
    license: "mit",
    available: true,
    installed_version: "1.0.0",
    update_available: true,
    update_requires_review: true,
    abandoned: false,
    sha256: "b".repeat(64),
  },
];

/**
 * The Dev tab's fixtures: what may be imported, and the reading of one source.
 *
 * The mock has no document parser -- the server's whole authoring stack sits
 * behind `dev/read`, and re-implementing it here would be a second one that
 * could disagree with it. So the reading is of a *fixed* document: the rows are
 * the same shape the server sends, the choices a person makes travel in
 * `dev/save`'s payload, and the verdict that matters is `dev/save`'s. What this
 * buys is the thing the mock exists for -- a screen with every row on it.
 */
const DEV_AUTOMATIONS: DevSource[] = [
  {
    key: "automation.hallway_motion",
    name: "Hallway motion",
    description: "The hall light on when motion is seen after dark.",
  },
];

const DEV_BLUEPRINTS: DevSource[] = [
  {
    key: "homeassistant/motion_light.yaml",
    name: "Motion-activated Light",
    description: "Turn on a light when motion is detected.",
    source_url: "https://www.home-assistant.io/blueprints/motion_light/",
    domain: "light",
  },
];

/** The reading of that fixed source, field for field with `pack_authoring.analyse`. */
const DEV_ANALYSIS: DevAnalysis = {
  title: "Hallway motion",
  description: "The hall light on when motion is seen after dark.",
  blueprint: false,
  entities: [
    {
      key: "motion",
      label: "Motion",
      entity_id: "binary_sensor.kitchen_motion",
      domain: "binary_sensor",
      count: 2,
      optional: false,
      places: ["trigger", "condition"],
      suggested_slot: "motion_sensor",
    },
    {
      key: "light",
      label: "Light",
      entity_id: "light.kitchen",
      domain: "light",
      count: 1,
      optional: false,
      places: ["action"],
      suggested_slot: "ceiling_light",
    },
  ],
  values: [
    {
      key: "delay",
      label: "Delay",
      kind: "duration",
      default: 120,
      description: "How long the light stays on.",
      minimum: null,
      maximum: null,
      unit: "seconds",
      choices: [],
      places: ["action"],
    },
  ],
  services: [
    {
      key: "turn_on",
      service: "light.turn_on",
      supported: true,
      acts_on: ["light"],
      data_keys: ["brightness_pct"],
      where: "action",
      depth: 0,
    },
  ],
  triggers: ["state"],
  conditions: [],
  dropped: [],
};

/** The two vocabularies a decision is made against, sent with the reading. */
const DEV_SLOTS: DevSlot[] = [
  { name: "ceiling_light", domains: ["light"], suggested: true },
  { name: "motion_sensor", domains: ["binary_sensor"], suggested: true },
  { name: "ambient_light_sensor", domains: ["sensor"], suggested: false },
];

const DEV_SERVICES: string[] = ["light.turn_on", "light.turn_off", "switch.turn_on"];

/** The inputs a hosted module's read offers, as the import screen's rows. */
const MODULE_INPUTS: ModuleInputRow[] = [
  {
    name: "motion",
    title: "Motion",
    description: "The sensor the module watches.",
    default: "binary_sensor.kitchen_motion",
    has_default: true,
    multiple: false,
    bound: true,
    value: "binary_sensor.kitchen_motion",
    satisfied: true,
    // A trigger's `entity_id` is matched against real entities rather than
    // rendered, which is why `in_trigger` is the flag the cast picker reads.
    in_trigger: true,
    selector: "entity",
    options: [],
  },
  {
    name: "brightness",
    title: "Brightness",
    description: "How bright the light comes on.",
    default: 60,
    has_default: true,
    multiple: false,
    bound: false,
    value: 60,
    satisfied: true,
    in_trigger: false,
    selector: "number",
    options: [],
  },
];

/** What the fixed source could publish, as the tick-list on the import screen. */
const MODULE_CANDIDATES: ModuleCandidate[] = [
  {
    name: "brightness",
    kind: "variable",
    value_kind: "number",
    expression: "{{ brightness }}",
    branch_only: false,
    suggested_key: "brightness",
  },
  {
    name: "light",
    kind: "entity",
    value_kind: "string",
    expression: "{{ states('light.kitchen') }}",
    branch_only: false,
    suggested_key: "light_state",
  },
];

/** The house's slot words, as a dropdown offers them. */
const SLOT_WORDS: ModuleSlotWord[] = [
  { name: "ambient_light_sensor", label: "Ambient light sensor", house_scope: false },
  { name: "ceiling_light", label: "Ceiling light", house_scope: false },
  { name: "motion_sensor", label: "Motion sensor", house_scope: false },
];

/** The licence codes a module may be saved under (`module_definitions.LICENCES`). */
const LICENCES: string[] = [
  "public_domain",
  "mit",
  "apache_2_0",
  "cc_by_nc_sa",
  "no_licence",
];

/**
 * The modules this mock house made, as the Store tab's local half shows them.
 *
 * Two deliberately opposite rows: one that reaches through a slot and so would
 * work in anybody's house, and one that names this house's own device and so
 * would only install somewhere that holds the same one. The chip a person reads
 * before sending a file is the whole reason both are here.
 *
 * Mutable, because Define, Deploy, Remove and Import all answer with the store
 * they left behind -- the same contract the server has, and what makes the
 * buttons on this screen look like they did something in `npm run dev`.
 */
const STORED_MODULES: ModuleOfferRow[] = [
  {
    slug: "evening_lighting",
    title: "Evening lighting",
    description: "Warm the lights down as the room's light level falls.",
    author: "Ada",
    version: "1.0.0",
    licence: "mit",
    blueprint: "blueprints/automation/homeassistant/motion_light.yaml",
    pinned: false,
    flows: [],
    automations: [],
    slots: ["ambient_light_sensor", "ceiling_light"],
    missing_slots: ["ceiling_light"],
    deployed: [
      {
        slug: "evening_lighting_kitchen",
        room_id: "kitchen",
        room_name: "Kitchen",
        running: true,
      },
      {
        slug: "evening_lighting_bedroom",
        room_id: "bedroom",
        room_name: "Bedroom",
        running: false,
      },
    ],
  },
  {
    slug: "porch_lamp",
    title: "Porch lamp",
    description: "The porch lamp on, half an hour before sunset.",
    author: "Ada",
    version: "0.2.0",
    licence: "no_licence",
    blueprint: "",
    pinned: true,
    flows: [],
    automations: [],
    slots: [],
    missing_slots: [],
    deployed: [],
  },
];

/**
 * The modules this house *hosts*: a definition with answers, running somewhere.
 *
 * A different list from `STORED_MODULES`, which is what the house offers, and
 * from `MODULES`, which is what packs installed. One module can appear in all
 * three -- that is the point of the two acts: defining it offers it, adding it
 * to a room hosts a copy, and the copy is what a room's page draws.
 *
 * Mutable, because unhosting one has to leave the list it answered with.
 */
const HOSTED_MODULES: HostedModule[] = [
  {
    slug: "evening_lighting_kitchen",
    title: "Evening lighting (Kitchen)",
    blueprint: "blueprints/automation/homeassistant/motion_light.yaml",
    definition: "evening_lighting",
    room_id: "kitchen",
    room_name: "Kitchen",
    // One slot answered and one not, which is the pair of states the card has
    // to draw differently: the module runs, and it is waiting for a device.
    slots: [
      {
        name: "ambient_light_sensor",
        input: "lux_sensor",
        scope: "room",
        part: "",
        bound: "sensor.kitchen_lux",
        bound_name: "Kitchen lux",
        parts: [],
      },
      // Split into two parts, one bound and one not, because a split slot is the
      // case the row's own control exists for: the module is on `west` and the
      // device it acts on is the one that part was given.
      {
        name: "ceiling_light",
        input: "lights",
        scope: "room",
        part: "west",
        bound: "",
        bound_name: "",
        parts: [
          { name: "west", label: "West", bound: "" },
          { name: "east", label: "East", bound: "light.kitchen_east" },
        ],
      },
    ],
    automation_id: "automation.evening_lighting_kitchen",
    // Two configurations on the kitchen module, because that is what the
    // switcher is for and a mock with one of everything shows nothing: the
    // other module has the ordinary answer, a module with a single
    // configuration it cannot drop.
    config: "Default",
    configs: ["Default", "Evening"],
    derived: {},
    flows: {},
    // No script on either module in the mock: the cast is offered on every
    // settings row and a mock that shipped one would be showing a state the
    // fixture cannot keep true -- nothing here calls anything.
    automations: {},
    inputs: [{ name: "threshold", value: 40 }],
    settings: [
      {
        name: "threshold",
        title: "Lux threshold",
        description: "The reading at which the lights come on.",
        default: 40,
        has_default: true,
        multiple: false,
        bound: true,
        value: 40,
        satisfied: true,
        in_trigger: false,
        selector: "number",
        options: [],
      },
    ],
    outputs: [
      {
        key: "scene",
        kind: "string",
        expression: "{{ states('light.kitchen') }}",
        entity_id: "sensor.open_house_evening_lighting_kitchen_scene",
        value: "on",
      },
    ],
  },
  {
    slug: "porch_lamp",
    title: "Porch lamp",
    blueprint: "",
    definition: "porch_lamp",
    room_id: "",
    room_name: "the whole house",
    slots: [],
    automation_id: "automation.porch_lamp",
    config: "Default",
    configs: ["Default"],
    derived: {},
    flows: {},
    automations: {},
    inputs: [],
    settings: [],
    outputs: [
      {
        key: "on",
        kind: "boolean",
        expression: "{{ is_state('switch.porch', 'on') }}",
        entity_id: "sensor.open_house_porch_lamp_on",
        value: true,
      },
    ],
  },
];

/**
 * What each configuration of each module was last holding.
 *
 * The mock has no records and no automations, so this is the whole of what makes
 * a switch *look* like a switch in `npm run dev`: without it, clicking another
 * configuration would move the select and leave the values the old one was
 * showing, which is the one thing a switch cannot do.
 *
 * Keyed by slug, then by configuration name, because that is what the two
 * commands below address them by.
 */
const CONFIG_VALUES: Record<string, Record<string, Record<string, unknown>>> = {};

/** Remember what a module is holding, under the configuration holding it. */
function holdConfiguration(module: HostedModule): void {
  (CONFIG_VALUES[module.slug] ??= {})[module.config] = Object.fromEntries(
    module.settings.map((setting) => [setting.name, setting.value]),
  );
}

/** Put back what a configuration was holding, for the rows it has something for. */
function showConfiguration(module: HostedModule): void {
  const held = CONFIG_VALUES[module.slug]?.[module.config];
  if (!held) return;
  for (const setting of module.settings) {
    if (setting.name in held) setting.value = held[setting.name];
  }
}

/** Settings written for a room, over the packs' defaults.
 *
 * A room's form has to remember a save the way the engine does, or the write
 * looks like it went nowhere: `roomDetail` derives `options` from each pack's
 * `default` every read, so without somewhere to keep what was written the next
 * `roomGet` would answer with the default again.
 */
const ROOM_OPTIONS: Record<string, Record<string, unknown>> = {};

/**
 * The bindings a room page is built from, and the same rows a module's slot
 * falls back to.
 *
 * One place, because a module's slot row says what the room binds beside its
 * own choice: a second copy of this table would let a card claim a fallback the
 * room's page does not have. `id` decides the one device the room is bound to
 * (`light.bedroom` in the bedroom and nothing elsewhere) and the two readings
 * every room shares.
 */
function roomBindingFixtures(id: string): BindingStatus[] {
  return [
    {
      slot: "ceiling_light",
      label: "Ceiling light",
      required: true,
      accepts_domains: ["light"],
      entity_id: id === "bedroom" ? "light.bedroom" : null,
      registry_id: id === "bedroom" ? "reg-light-bedroom" : null,
      friendly_name: id === "bedroom" ? "Bedroom light" : null,
      domain: id === "bedroom" ? "light" : null,
      state: id === "bedroom" ? "off" : null,
      status: id === "bedroom" ? "ok" : "unbound",
      last_changed: id === "bedroom" ? new Date().toISOString() : null,
      ...noRule(),
      modules: ["Bedtime button"],
      // The halves the house has split this role into (`SLOT_PARTS`), if any --
      // the split is the house's, so every room's row draws the same two and only
      // the devices differ.
      parts: partRows("ceiling_light", ["light"], id),
    },
    {
      slot: "motion_sensor",
      label: "Motion sensor",
      required: true,
      accepts_domains: ["binary_sensor"],
      entity_id: "binary_sensor.kitchen_motion",
      registry_id: "reg-motion-kitchen",
      friendly_name: "Kitchen motion",
      domain: "binary_sensor",
      state: "off",
      status: "ok",
      last_changed: new Date().toISOString(),
      ...noRule(),
      modules: ["Bedtime button"],
      parts: partRows("motion_sensor", ["binary_sensor"], id),
    },
    {
      slot: "ambient_light_sensor",
      label: "Ambient light sensor",
      required: false,
      accepts_domains: ["sensor"],
      entity_id: "sensor.kitchen_lux",
      registry_id: "reg-lux-kitchen",
      friendly_name: "Kitchen lux",
      domain: "sensor",
      state: "unavailable",
      status: "unavailable",
      last_changed: new Date().toISOString(),
      ...noRule(),
      modules: ["Bedtime button"],
      parts: partRows("ambient_light_sensor", ["sensor"], id),
    },
  ];
}

/**
 * What the mock knows about one entity, as a binding row would state it.
 *
 * Read from the binding fixtures rather than invented, so a module's row shows
 * the same status the room's own table shows for the same device -- including
 * `unavailable`, which the room's ambient light sensor deliberately is.
 */
function entityFacts(
  entityId: string | null,
): Pick<ModuleSlot, "friendly_name" | "domain" | "state" | "status"> {
  if (entityId === null) {
    return { friendly_name: null, domain: null, state: null, status: "unbound" };
  }
  const known = [
    ...ROOMS.flatMap((room) => roomBindingFixtures(room.id)),
    ...HOUSE_SCOPE.slots,
  ].find((row) => row.entity_id === entityId);
  if (known !== undefined) {
    return {
      friendly_name: known.friendly_name,
      domain: known.domain,
      state: known.state,
      status: known.status,
    };
  }
  const candidate = CANDIDATES.find((entry) => entry.entity_id === entityId);
  return {
    friendly_name: candidate?.friendly_name ?? entityId,
    domain: entityId.split(".")[0] ?? null,
    // The mock holds no live state for an entity no fixture mentions. "on" is
    // what its other resolved rows say; `null` would read as unknown.
    state: "on",
    status: "ok",
  };
}

/**
 * Where one module's slot override lives, keyed the way the server scopes it.
 *
 * By placement -- the room id, or `""` for the house -- and then the pack and
 * the slot, because the server records the override in the placement's own
 * settings layer: the same pack twice in two rooms is two overrides, and
 * clearing one leaves the other.
 */
function overrideKey(roomId: string, pack: string, slot: string): string {
  return `${roomId}::${pack}::${slot}`;
}

/** What a person pointed one module's slot at this session, or nothing. */
const SLOT_OVERRIDES: Record<
  string,
  { entity_id: string | null; label: string | null }
> = {};

/** The key a part of a slot binds under, which is what a module names. */
function partKey(slot: string, part: string): string {
  return `${slot}__${part}`;
}

/** One slot's parts, as binding rows: the device each half is bound to. */
function partRows(
  slot: string,
  accepts: string[],
  roomId: string,
): BindingStatus["parts"] {
  return (SLOT_PARTS[slot] ?? []).map((name) => {
    const key = partKey(slot, name);
    // A room's own ceiling light is the one split a *room's* page shows bound:
    // the fixture's single-room binding, carried down to its halves.
    const bound =
      key === "ceiling_light__left" && roomId !== "bedroom"
        ? null
        : (SLOT_PART_BINDINGS[key] ?? null);
    const entityId = bound;
    return {
      slot: key,
      name,
      label: name.charAt(0).toUpperCase() + name.slice(1),
      entity_id: entityId,
      registry_id: null,
      friendly_name: entityId === null ? null : `${slot.replace(/_/g, " ")} ${name}`,
      domain: entityId === null ? null : entityId.split(".")[0]!,
      state: entityId === null ? null : "off",
      status: entityId === null ? "unbound" : "ok",
      last_changed: null,
      // A part is the same role, so it takes what the role takes.
      accepts_domains: accepts,
    };
  });
}

/** The four rule facts at rest: a slot row nothing has put logic on. */
function noRule(): SlotRuleFacts {
  return {
    rule_kind: null,
    rule_summary: null,
    rule_picks_device: null,
    rule_device: null,
  };
}

/** The logic a person put on one module's slot this session, if any. */
const SLOT_RULES: Record<
  string,
  {
    kind: ModuleSlotRuleKind;
    value: unknown;
    when: string[];
    device: string;
  }
> = {};

/**
 * The four facts a slot rule reports, in the server's own words.
 *
 * The sentence is built here rather than left to the fixture, for the reason the
 * server builds it: it is what the row *says*, and a screen composing its own
 * would be a second copy of what a rule means -- free to disagree with the real
 * one about a script's entities in particular, which is the part a person cannot
 * infer from the word "script".
 */
function slotRuleFacts(
  rule: { kind: ModuleSlotRuleKind; value: unknown; when: string[]; device: string },
): SlotRuleFacts {
  if (rule.kind === "condition") {
    return {
      rule_kind: "condition",
      rule_summary: rule.device
        ? `decided by a condition: ${rule.device} while it holds`
        : "decided by a condition: the device above while it holds",
      rule_picks_device: false,
      rule_device: rule.device || null,
    };
  }
  if (rule.kind === "script") {
    const list =
      rule.when.length === 1 ? rule.when[0]! : `any of ${rule.when.join(", ")}`;
    return {
      rule_kind: "script",
      rule_summary: `decided by a script, when ${list} changes`,
      rule_picks_device: true,
      rule_device: null,
    };
  }
  return {
    rule_kind: rule.kind,
    rule_summary: `decided by a ${rule.kind}`,
    rule_picks_device: true,
    rule_device: null,
  };
}

/**
 * One module's slots, with the device each is pointed at resolved.
 *
 * The fixture states which slots the pack reaches; this states what each one
 * points at. The fallback is read from the same binding fixtures the room page
 * and the house page are built from -- a room's binding for a room-scoped slot,
 * the house's for a whole-house role -- so a row cannot say the room binds a
 * device the room's own page does not. A written override wins over the
 * fallback, and `null` in it means the fallback again, which is what Reset
 * writes.
 */
function moduleSlots(module: InstalledModule): ModuleSlot[] {
  return module.slots.map((row) => {
    const houseScope = HOUSE_SCOPE.slots.some((slot) => slot.slot === row.slot);
    const fallback = houseScope
      ? (houseBindings[row.slot] ?? null)
      : roomBound(module.room_id, row.slot);
    const key = overrideKey(module.room_id, module.pack, row.slot);
    const override = SLOT_OVERRIDES[key];
    const rule = SLOT_RULES[key];
    // Which half of a split slot this module is on, and what that half resolves
    // to: a part is a binding of its own, so the module acts on the device bound
    // for `light_group__a` rather than on the role's own. `null` when the house
    // does not carry the part, which leaves the module on the slot itself -- the
    // same drop `live_modules.slot_parts_of` makes.
    const chosen = SLOT_MEMBER[key];
    const part =
      chosen !== undefined && (SLOT_PARTS[row.slot] ?? []).includes(chosen)
        ? chosen
        : null;
    const partFallback =
      part === null
        ? fallback
        : (SLOT_PART_BINDINGS[partKey(row.slot, part)] ??
          (houseScope ? (houseBindings[row.slot] ?? null) : null));
    const entityId = override?.entity_id ?? partFallback;
    return {
      ...row,
      label: override?.label ?? row.slot,
      declared_label: row.slot,
      entity_id: entityId,
      default_entity_id: partFallback,
      overridden: (override?.entity_id ?? null) !== null,
      named: override?.label !== null && override?.label !== undefined,
      part,
      part_label: part === null ? null : part.charAt(0).toUpperCase() + part.slice(1),
      parts: (SLOT_PARTS[row.slot] ?? []).map((name) => ({
        name,
        label: name.charAt(0).toUpperCase() + name.slice(1),
      })),
      separate: row.slot.includes("__"),
      house_scope: houseScope,
      bound: entityId !== null,
      ...entityFacts(entityId),
      ...(rule ? slotRuleFacts(rule) : noRule()),
    };
  });
}

/** The entity a room binds for one slot, or `null` when it binds none. */
function roomBound(roomId: string, slot: string): string | null {
  const row = roomBindingFixtures(roomId).find((binding) => binding.slot === slot);
  return row?.entity_id ?? null;
}

function roomDetail(id: string): RoomDetail {
  // An unknown room is `not_found` and not the first room in the fixture. This
  // fell back to `ROOMS[0]`, so a *deleted* room, or a mistyped one, answered
  // with the Kitchen's page under the name that was asked for -- which is the
  // one way a mock can be worse than silent: the screen renders a room that does
  // not exist and looks entirely right doing it.
  const summary = ROOMS.find((room) => room.id === id);
  if (summary === undefined) throw refuse(REFUSALS.notFound, `no room ${id}`);
  // Only the room's own modules' settings, keyed the way the server keys them.
  const packs = installed.filter((module) => module.room_id === summary.id);
  return {
    id: summary.id,
    name: summary.name,
    type: summary.type,
    type_label: summary.type_label,
    bindings: roomBindingFixtures(id),
    // A fixture house binds nothing globally: the house's own slots are what the
    // House tab's fixtures answer, and inventing rows here would put a section on
    // a room's page that the fixture never set up.
    global_bindings: [],
    options_schema: {
      type: "object",
      title: "Room options",
      properties: moduleSettings(packs),
    },
    options: {
      ...Object.fromEntries(
        Object.entries(moduleSettings(packs))
          .filter(([, node]) => node.default !== undefined)
          .map(([key, node]) => [key, node.default]),
      ),
      ...(ROOM_OPTIONS[summary.id] ?? {}),
    },
    modules: packs.map(withReach),
    active_profiles: summary.active_profiles,
    mode: summary.mode,
    revision: profilesRevision,
    axes: [
      {
        id: "lighting",
        label: "Lighting",
        profiles: profileRows().filter((profile) => profile.axis === "lighting"),
      },
    ],
  };
}

const CANDIDATES: BindingSuggestion[] = [
  {
    entity_id: "light.kitchen_ceiling",
    registry_id: "reg-kitchen-ceiling",
    friendly_name: "Kitchen ceiling",
    domain: "light",
    score: 0.95,
  },
  {
    entity_id: "light.kitchen_spot",
    registry_id: "reg-kitchen-spot",
    friendly_name: "Kitchen spots",
    domain: "light",
    score: 0.6,
  },
];

const SUBSCRIBERS = new Set<(value: ActivityStreamEvent) => void>();

function answer(type: string, payload: Record<string, unknown>): unknown {
  switch (type) {
    case COMMANDS.capabilities:
      return CAPABILITIES;
    case COMMANDS.overview:
      return {
        name: "Ada's house",
        mode: "home",
        rooms: ROOMS,
        people: [
          { entity_id: "person.ada", name: "Ada", state: "home", home: true },
          { entity_id: "person.sam", name: "Sam", state: "not_home", home: false },
        ],
        modules_installed: MODULES.length,
        issues: { info: 0, warning: 1, error: 1 },
        updated_at: new Date().toISOString(),
      } satisfies HouseOverview;
    case COMMANDS.roomsList:
      return { rooms: ROOMS };
    case COMMANDS.roomGet:
      return roomDetail(String(payload.room_id));
    case COMMANDS.roomCreate: {
      // A *new* room, and not the first one the fixture holds. This used to be
      // `return roomDetail(String(payload.room_id))` beside `roomUpdate`, and a
      // create sends no `room_id` at all -- the id is the server's to mint from
      // the name -- so the reply was the Kitchen's page whatever was typed, the
      // room a person made never appeared in `rooms/list`, and every later
      // command that addressed it by its real id fell back to the Kitchen too.
      // The id is slugged from the name the way the server's area id is, because
      // the panel addresses the room by it from here on.
      const name = String(payload.name ?? "");
      const id = slugOf(name);
      ROOMS.push({
        id,
        name,
        type: String(payload.room_type ?? ""),
        type_label: labelOf(String(payload.room_type ?? "")),
        bound_slots: 0,
        total_slots: 3,
        // A room nobody has bound anything in is a room the engine cannot act
        // in, which is exactly what the fixture's own rows show.
        required_unbound: ["ceiling_light", "motion_sensor"],
        mode: "home",
        active_profiles: {},
        occupied: false,
        auto_lighting: false,
        issue_count: 0,
      });
      return roomDetail(id);
    }
    case COMMANDS.roomUpdate: {
      // The rename only. The mock answered the page for a room id it was never
      // sent (`roomUpdate` sends `room_id` and `name`), so renaming a room left
      // the old name on screen and the row in `rooms/list` unchanged.
      const room = ROOMS.find((entry) => entry.id === String(payload.room_id));
      if (room === undefined) throw refuse(REFUSALS.notFound, `no room ${String(payload.room_id)}`);
      if (typeof payload.name === "string" && payload.name !== "") room.name = payload.name;
      return roomDetail(room.id);
    }
    case COMMANDS.roomDelete: {
      // The room goes and the area stays, which is the server's rule and the
      // whole reason this answers the id rather than the house: the panel drops
      // the room from its list and leaves Home Assistant's layout alone.
      const at = ROOMS.findIndex((entry) => entry.id === String(payload.room_id));
      if (at === -1) throw refuse(REFUSALS.notFound, `no room ${String(payload.room_id)}`);
      ROOMS.splice(at, 1);
      return { room_id: String(payload.room_id) };
    }
    case COMMANDS.roomBind:
    case COMMANDS.roomReplace:
    case COMMANDS.roomUnbind: {
      guardStale(payload);
      // The house is a placement like a room, and binding a global slot answers
      // with the house's page rather than a room's -- the same branch the live
      // handler makes.
      if (payload.room_id === "") {
        houseBindings[String(payload.slot)] =
          type === COMMANDS.roomUnbind ? null : String(payload.entity_id);
        return houseScopeView();
      }
      return roomDetail(String(payload.room_id));
    }
    case COMMANDS.roomCandidates:
      return { candidates: CANDIDATES };
    case COMMANDS.roomOptionsGet:
    case COMMANDS.roomOptionsSet:
      if (type === COMMANDS.roomOptionsSet) guardStale(payload);
      if (payload.room_id === "") {
        if (type === COMMANDS.roomOptionsSet && payload.values !== undefined) {
          houseOptions = { ...houseOptions, ...(payload.values as Record<string, unknown>) };
        }
        return { schema: HOUSE_SCOPE.options_schema, values: houseOptions };
      }
      {
        const id = String(payload.room_id);
        if (type === COMMANDS.roomOptionsSet && payload.values !== undefined) {
          ROOM_OPTIONS[id] = {
            ...(ROOM_OPTIONS[id] ?? {}),
            ...(payload.values as Record<string, unknown>),
          };
        }
        const detail = roomDetail(id);
        return { schema: detail.options_schema, values: detail.options };
      }
    case COMMANDS.houseScope:
      return houseScopeView();
    case COMMANDS.roomAvailableModules:
      return { offers: OFFERS };
    case COMMANDS.moduleInstall: {
      // The reply is the page the module landed on, which is the house's when
      // the placement is the house -- the same branch the live handler makes.
      const roomId = String(payload.room_id);
      const pack = String(payload.pack);
      const already = placement(roomId, pack);
      if (already !== undefined) return landedReply(already, roomId);
      // A second placement of a pack already somewhere else is a copy of that
      // module re-placed, because the mock has no manifest to build a fresh one
      // from and the copy's atoms are the ones the reach control lists.
      const template = installed.find((module) => module.pack === pack);
      if (template !== undefined) {
        const copy: InstalledModule = {
          ...template,
          room_id: roomId,
          house: roomId === "",
        };
        installed = [...installed, copy];
        return landedReply(copy, roomId);
      }
      const offer = OFFERS.find((entry) => entry.pack === pack);
      const landed: InstalledModule = {
        pack: String(payload.pack),
        name: offer?.name ?? String(payload.pack),
        version: offer?.version ?? "1.0.0",
        room_id: roomId,
        house: roomId === "",
        scope: "room",
        enabled: false,
        suppressed_by: null,
        suppressed_behaviour: null,
        satisfiable: offer?.satisfiable ?? true,
        missing_slots: offer?.missing_slots ?? [],
        // The settings the offer's schema declares, so the card that appears
        // after the install has its form already filled in.
        option_keys: Object.keys(offer?.options_schema?.properties ?? {}),
        options_schema: offer?.options_schema ?? null,
        options: Object.fromEntries(
          Object.entries(offer?.options_schema?.properties ?? {}).map(([key, node]) => [
            key,
            (node as { default?: unknown }).default,
          ]),
        ),
        behaviours: (offer?.behaviours ?? []).map((behaviour) => ({
          id: behaviour.id,
          label: behaviour.label,
          // A preview carries no declaration to build a sentence from, so the
          // mock says only what it knows: this atom came with the pack.
          description: `One of the acts “${offer?.name ?? payload.pack}” performs.`,
          enabled: false,
          active_rooms: [],
          scope: "room" as const,
          declared_scope: "room" as const,
          widenable: false,
          // The offer carries the pack's own rank, or `null` when it declares
          // none -- which the catalog answers with its default of 0.
          priority: behaviour.priority ?? 0,
          default_priority: behaviour.priority ?? 0,
          priority_set: false,
        })),
        // The offer's own declaration of what the pack needs and what it may
        // use, which is the same list the installed module's rows are built
        // from -- so a freshly installed pack shows its devices at once.
        slots: [
          ...(offer?.requires_slots ?? []).map((slot) =>
            slotRow(slot, { required: true }),
          ),
          ...(offer?.optional_slots ?? []).map((slot) => slotRow(slot)),
        ],
      };
      installed = [...installed, landed];
      return landedReply(landed, roomId);
    }
    case COMMANDS.moduleUninstall: {
      const roomId = String(payload.room_id);
      installed = installed.filter(
        (module) => !(module.room_id === roomId && module.pack === payload.pack),
      );
      if (roomId !== "") return roomDetail(roomId);
      return { room_id: "", house: houseScopeView() };
    }
    case COMMANDS.moduleSetEnabled: {
      const updated = updatePlacement(
        String(payload.room_id),
        payload.pack,
        (module) => ({ ...module, enabled: Boolean(payload.enabled) }),
      );
      return withReach(updated ?? MODULES[0]!);
    }
    case COMMANDS.moduleSetBehaviourEnabled: {
      // One atom switched on in one room. The engine's flag is per room, so the
      // reach control's ticks are written here -- ticking the Kitchen and the
      // Hall is two calls and one atom, not a second install. `room_id` is the
      // room added to (or dropped from) the set, which need not be the module's
      // own room: that is the module staying one and running in two rooms.
      const rooms = atomRooms(
        String(payload.pack),
        String(payload.behaviour),
      );
      if (payload.enabled) rooms.add(String(payload.room_id));
      else rooms.delete(String(payload.room_id));
      const module = placement(String(payload.room_id), payload.pack);
      return withReach(module ?? MODULES[0]!);
    }
    case COMMANDS.moduleSetBehaviourScope: {
      // One atom's reach: `house` widens it to run once for the whole house,
      // `room` narrows it to a room. The reach control reads this back, so a
      // mock that answered the unchanged module would leave the tick springing
      // back the moment the page reloaded.
      const scope = payload.scope === "house" ? "house" : "room";
      const updated = updatePlacement(
        String(payload.room_id),
        payload.pack,
        (module) => ({
          ...module,
          behaviours: module.behaviours.map((row) =>
            row.id === payload.behaviour ? { ...row, scope } : row,
          ),
        }),
      );
      return withReach(updated ?? MODULES[0]!);
    }
    case COMMANDS.moduleSetBehaviourPriority: {
      // One atom's rank. `PRIORITY_OVERRIDES` holds only the *chosen* ranks, so
      // writing the declared one back is a delete here exactly as it is a
      // `forget` on the server -- the Reset button is the same command as any
      // other, and this is the line that makes it leave nothing behind.
      const key = `${String(payload.pack)}/${String(payload.behaviour)}`;
      const module = placement(String(payload.room_id), payload.pack);
      const declared = module?.behaviours.find(
        (row) => row.id === payload.behaviour,
      )?.default_priority;
      const wanted = Number(payload.priority);
      if (declared !== undefined && wanted === declared) delete PRIORITY_OVERRIDES[key];
      else PRIORITY_OVERRIDES[key] = wanted;
      return withReach(module ?? MODULES[0]!);
    }
    case COMMANDS.moduleSetSlot: {
      // One module's own device for one slot, and its own name for it. Kept by
      // placement, because the server scopes it that way, and read back through
      // `withReach` -- so the row the panel redraws from is the row a reload
      // would show, which is the whole point of the override.
      const roomId = String(payload.room_id);
      const pack = String(payload.pack);
      const slot = String(payload.slot);
      const written = typeof payload.label === "string" ? payload.label.trim() : "";
      const key = overrideKey(roomId, pack, slot);
      SLOT_OVERRIDES[key] = {
        entity_id: typeof payload.entity_id === "string" ? payload.entity_id : null,
        label: written === "" ? null : written,
      };
      // `part` is *not* resolved the way the other two are: an absent or `null`
      // one means **not touched**, so a reset of the device leaves the module on
      // the part it is on. An empty string is the whole slot and is what clears
      // it -- the same distinction the server's schema makes.
      const part = payload.part;
      if (typeof part === "string") {
        if (part === "") delete SLOT_MEMBER[key];
        else SLOT_MEMBER[key] = part;
      }
      return withReach(placement(roomId, pack) ?? MODULES[0]!);
    }
    case COMMANDS.slotSetParts: {
      // Split a slot, rename one half, or rejoin one. Kept in the house's own
      // record (`SLOT_PARTS`), which is where the server keeps it and why every
      // room's page draws the same halves.
      const slot = String(payload.slot);
      const name = String(payload.name);
      const action = String(payload.action);
      const held = SLOT_PARTS[slot] ?? [];
      if (action === "add") {
        if (!held.includes(name)) SLOT_PARTS[slot] = [...held, name];
      } else if (action === "rename") {
        const newName = String(payload.new_name ?? "");
        SLOT_PARTS[slot] = held.map((entry) => (entry === name ? newName : entry));
        // The part binds under a key that carries its name, so the device moves
        // with the rename rather than being left under a key nobody names.
        const was = partKey(slot, name);
        if (was in SLOT_PART_BINDINGS) {
          SLOT_PART_BINDINGS[partKey(slot, newName)] = SLOT_PART_BINDINGS[was] ?? null;
          delete SLOT_PART_BINDINGS[was];
        }
        // Every module that was on the old name moves with it, because a part's
        // name *is* the key a module names.
        for (const key of Object.keys(SLOT_MEMBER)) {
          if (key.endsWith(`::${slot}`) && SLOT_MEMBER[key] === name) {
            SLOT_MEMBER[key] = newName;
          }
        }
      } else {
        SLOT_PARTS[slot] = held.filter((entry) => entry !== name);
      }
      return {
        slot,
        parts: (SLOT_PARTS[slot] ?? []).map((entry) => ({ name: entry })),
      };
    }
    case COMMANDS.moduleSetSlotRule: {
      // "Set it to", on a slot row. The same key space the device override uses,
      // because the two are one decision: a rule takes the device away from the
      // person (`live_modules.set_slot_rule` forgets it either way), so setting a
      // rule clears the override and clearing a rule leaves the slot on whatever
      // the room binds.
      const roomId = String(payload.room_id);
      const pack = String(payload.pack);
      const slot = String(payload.slot);
      const key = overrideKey(roomId, pack, slot);
      const kind = String(payload.kind ?? "");
      if (kind === "") delete SLOT_RULES[key];
      else {
        delete SLOT_OVERRIDES[key];
        SLOT_RULES[key] = {
          kind: kind as ModuleSlotRuleKind,
          value: payload.value,
          when: Array.isArray(payload.when) ? (payload.when as string[]) : [],
          device: typeof payload.device === "string" ? payload.device : "",
        };
      }
      return withReach(placement(roomId, pack) ?? MODULES[0]!);
    }
    case COMMANDS.modulesList:
      // Every installed module, the house-placed one included: the Modules tab
      // is the one screen that lists both homes, so a mock that answered only
      // the room ones would leave its "the house" chip unrendered everywhere.
      return { modules: installed.map(withReach) };
    case COMMANDS.profilesList:
      return { profiles: profileRows() };
    case COMMANDS.profileActivate:
      return roomDetail(String(payload.room_id));
    case COMMANDS.profileActivateHouse: {
      const name = String(payload.profile);
      if (PROFILE_DOCUMENTS[name]?.kind !== "house") {
        throw refuse(REFUSALS.notFound, `the profile ${name} is not a house profile`);
      }
      houseProfile = name;
      // The house moved, so every page rendered before this is stale -- which is
      // the whole point of the revision, and what the disabled-page notice is
      // for. Bumped here rather than by the callers of this handler, because
      // this is where the move happens.
      profilesRevision += 1;
      return { profiles: profileRows() };
    }
    case COMMANDS.profileDeactivateHouse:
      houseProfile = null;
      profilesRevision += 1;
      return { profiles: profileRows() };
    case COMMANDS.profileCapture: {
      // A profile made *out of* the house: what it is on and set to, named. The
      // mock's house is a fixture, so the document records what a person can see
      // rather than a whole inventory -- enough for the list to grow a row and
      // for the house to go on it, which is the whole of what the screen draws.
      //
      // Capture puts the house on the new profile as part of taking it, so this
      // is `activate_house` by another door: the revision moves here for the same
      // reason it moves there, and a page rendered before it is stale.
      const name = slugOf(String(payload.name ?? ""));
      if (PROFILE_DOCUMENTS[name] !== undefined) {
        throw refuse(
          REFUSALS.invalidFormat,
          `this house already holds a profile called ${name}`,
        );
      }
      PROFILE_DOCUMENTS[name] = {
        name,
        kind: "house",
        description: `What this house was on and set to on ${new Date().toISOString().slice(0, 10)}.`,
        selections: {},
      };
      houseProfile = name;
      profilesRevision += 1;
      return { profiles: profileRows() };
    }
    case COMMANDS.profileRename: {
      const from = String(payload.profile);
      const to = slugOf(String(payload.to ?? ""));
      const held = PROFILE_DOCUMENTS[from];
      if (held === undefined) throw refuse(REFUSALS.notFound, `no profile called ${from}`);
      // A rename and not a remove-and-add: every selection that named the
      // profile moves with it, which is why the active set and the house's own
      // pointer are rewritten rather than left naming a document that is gone.
      delete PROFILE_DOCUMENTS[from];
      PROFILE_DOCUMENTS[to] = { ...held, name: to };
      if (ACTIVE_PROFILE_NAMES.delete(from)) ACTIVE_PROFILE_NAMES.add(to);
      if (houseProfile === from) houseProfile = to;
      return { profiles: profileRows() };
    }
    case COMMANDS.profileRemove: {
      const name = String(payload.profile);
      if (PROFILE_DOCUMENTS[name] === undefined) {
        throw refuse(REFUSALS.notFound, `no profile called ${name}`);
      }
      delete PROFILE_DOCUMENTS[name];
      // Removing takes every selection that named it off with it, and a house
      // profile in force is released -- and a release is a house move, so the
      // revision moves exactly as it does for `deactivate_house`.
      ACTIVE_PROFILE_NAMES.delete(name);
      if (houseProfile === name) {
        houseProfile = null;
        profilesRevision += 1;
      }
      return { profiles: profileRows() };
    }
    case COMMANDS.profileExport: {
      const wanted = payload.profile === undefined ? null : String(payload.profile);
      if (wanted !== null && PROFILE_DOCUMENTS[wanted] === undefined) {
        throw refuse(REFUSALS.notFound, `the profile ${wanted} is not held by this house`);
      }
      return {
        document:
          wanted === null
            ? { profiles: Object.values(PROFILE_DOCUMENTS) }
            : PROFILE_DOCUMENTS[wanted],
      };
    }
    case COMMANDS.profileImport: {
      const wanted = profileDocuments(payload.document);
      const replace = payload.replace === true;
      const conflicts = wanted
        .map((entry) => String((entry as Record<string, unknown>).name))
        .filter((name) => PROFILE_DOCUMENTS[name] !== undefined);
      if (conflicts.length > 0 && !replace) {
        throw refuse(
          REFUSALS.invalidFormat,
          `this house already holds ${conflicts.join(", ")}; import again with replace`,
        );
      }
      const imported: string[] = [];
      for (const entry of wanted) {
        const document = entry as Record<string, unknown>;
        const name = String(document.name);
        if (conflicts.includes(name)) continue;
        PROFILE_DOCUMENTS[name] = document;
        imported.push(name);
      }
      return { imported, replaced: replace ? conflicts : [], profiles: profileRows() };
    }
    case COMMANDS.storeIndex:
      return { entries: STORE, generated_at: new Date().toISOString(), cached: false };
    case COMMANDS.storeInstall: {
      // The pack the Store asked for, installed into the house, by the same road
      // `modules/install` takes -- so the two cannot disagree about what an
      // install produces. This used to answer `MODULES[0]` whatever button was
      // pressed, so every pack in the Store installed "Motion lighting" and the
      // one that was actually installed was nowhere in the house.
      const landed = answer(COMMANDS.moduleInstall, {
        room_id: "",
        pack: String(payload.pack),
      }) as ModuleInstallReply;
      return { installed: landed.installed };
    }
    case COMMANDS.modulesStore: {
      // The verdict is the placement's, as the server's is: the same module can
      // be missing a device in one room and not in another, so the rows are
      // ranked against what the asked-about placement binds rather than sent as
      // a fixture. A slot with no device is not an answer, which is how a room's
      // own bindings read -- an entry per slot somebody has filled.
      const roomId = String(payload.room_id ?? "");
      const answered = (id: string): string[] =>
        roomBindingFixtures(id)
          .filter((row) => row.entity_id !== null)
          .map((row) => row.slot);
      const bound = new Set(
        roomId === "" ? ROOMS.flatMap((room) => answered(room.id)) : answered(roomId),
      );
      return {
        store: STORED_MODULES.map((offer) => ({
          ...offer,
          missing_slots: offer.slots.filter((slot) => !bound.has(slot)),
        })),
        rooms: roomsOf(ROOMS),
      };
    }
    case COMMANDS.modulesHosted:
      return { modules: HOSTED_MODULES };
    case COMMANDS.modulesRead: {
      // The import screen's one read: what the document asks for, what it could
      // publish, and what the house already has to fill those inputs. The mock
      // has no document parser -- the server's whole authoring stack is behind
      // this -- so it answers with the reading of a *fixed* source, which is
      // what a fixture is for: the screen renders its tables, the bindings a
      // person makes travel in the payload, and the next command
      // (`modules/host`) is what has to be honest about the result.
      //
      // Naming a `module` reads that module instead, and the reply then also
      // carries its own document and every answer given about it -- which is
      // what the card's Edit opens on.
      const module = typeof payload.module === "string" ? payload.module : "";
      const hosted = module === "" ? undefined : HOSTED_MODULES.find((row) => row.slug === module);
      if (module !== "" && hosted === undefined) {
        throw refuse(
          REFUSALS.invalidFormat,
          `this house hosts no module called ${module}, so there is nothing to edit`,
        );
      }
      const definition = hosted?.definition ?? "";
      const stored = STORED_MODULES.find((row) => row.slug === definition);
      if (hosted === undefined) {
        return {
          source: {
            title: "Evening lighting",
            description: "Warm the lights down as the room's light level falls.",
            blueprint: STORED_MODULES[0]!.blueprint,
            inputs: MODULE_INPUTS.length,
          },
          inputs: MODULE_INPUTS,
          candidates: MODULE_CANDIDATES,
          slots: SLOT_WORDS,
          rooms: roomsOf(ROOMS),
          licences: LICENCES,
          hosted: HOSTED_MODULES,
        } satisfies ModuleReadReply;
      }
      // A module carries its own document, so the screen is handed *that* rather
      // than anything the panel sent: a copy taken on the way out would be a
      // second version of the module.
      const installs = HOSTED_MODULES.filter(
        (row) => row.definition !== "" && row.definition === hosted.definition,
      ).map((row) => ({
        slug: row.slug,
        room_id: row.room_id,
        room_name: row.room_name,
      }));
      return {
        source: {
          title: hosted.title,
          description: stored?.description ?? "",
          blueprint: hosted.blueprint,
          inputs: hosted.settings.length,
        },
        inputs: hosted.settings.map((setting) => ({ ...setting })),
        candidates: MODULE_CANDIDATES,
        slots: SLOT_WORDS,
        rooms: roomsOf(ROOMS),
        licences: LICENCES,
        hosted: HOSTED_MODULES,
        text: `# ${hosted.title}\n`,
        editing: {
          module: hosted.slug,
          definition,
          text: `# ${hosted.title}\n`,
          title: hosted.title,
          description: stored?.description ?? "",
          author: stored?.author ?? "",
          version: stored?.version ?? "1.0.0",
          licence: stored?.licence ?? "no_licence",
          blueprint: hosted.blueprint,
          bindings: {},
          settings: hosted.settings.map((setting) => setting.name),
          casts: hosted.derived,
          // The *names* off the definition and the *ids* from the installation:
          // which inputs a module answers by a flow is the module's, and the id
          // belongs to the Node-RED that installed it.
          flows: Object.fromEntries(
            Object.entries(hosted.flows).map(([name, flow]) => [name, flow.flow_id]),
          ),
          automations: Object.fromEntries(
            Object.entries(hosted.automations).map(([name, row]) => [
              name,
              row.automation_id,
            ]),
          ),
          // The mock's outputs carry only the key, where the server keeps the
          // input's own name *and* the key the person chose; the key is what the
          // row is addressed by, so it stands in for both here.
          picks: hosted.outputs.map((output) => ({ name: output.key, key: output.key })),
          installs:
            installs.length > 0
              ? installs
              : [{ slug: hosted.slug, room_id: hosted.room_id, room_name: hosted.room_name }],
        } satisfies ModuleEditSeed,
      } satisfies ModuleReadReply;
    }
    case COMMANDS.modulesHost: {
      // Hosting a source is what makes a module exist, and the reply is the house
      // it landed in. The mock builds the module from what it was sent rather
      // than from a document it cannot parse: the slots the person answered, the
      // settings they kept, and the outputs they ticked -- which together *are*
      // the module, because a module is its answers.
      const title = String(payload.title ?? "");
      const slug = slugOf(title);
      const roomId = String(payload.room_id ?? "");
      const roomName =
        roomId === ""
          ? "the whole house"
          : (ROOMS.find((room) => room.id === roomId)?.name ?? roomId);
      const bindings = (payload.bindings ?? {}) as Record<string, ModuleBinding>;
      const outputs = Array.isArray(payload.outputs)
        ? (payload.outputs as { name?: unknown; key?: unknown }[])
        : [];
      const settings = Array.isArray(payload.settings) ? (payload.settings as unknown[]) : [];
      const hosted: HostedModule = {
        slug,
        title,
        blueprint: typeof payload.key === "string" ? payload.key : "",
        // A document hosted directly has no definition behind it, which is not a
        // deficiency -- it is a module the house runs without offering.
        definition: "",
        room_id: roomId,
        room_name: roomName,
        // One row per input answered with a *slot*, which is what a room binds:
        // the module reaches the device through the role, not through the id.
        slots: Object.entries(bindings)
          .filter(([, binding]) => binding.kind === "slot")
          .map(([name, binding]) => ({
            name: String(binding.slot ?? name),
            input: name,
            scope: binding.scope === "house" ? "house" : "room",
            part: String(binding.part ?? ""),
            bound: roomBound(roomId, String(binding.slot ?? name)) ?? "",
            bound_name: "",
            parts: [],
          })),
        automation_id: `automation.${slug}`,
        // Waiting for anything the mock cannot resolve, which is the state the
        // card is most needed for: a module hosted with no device in the room is
        // hosted and idle, not absent.
        config: "Default",
        configs: ["Default"],
        derived: {},
        flows: Object.fromEntries(
          (Array.isArray(payload.flows) ? (payload.flows as unknown[]) : []).map((name) => [
            String(name),
            { flow_id: "", entity_id: `sensor.open_house_flow_${slug}_${String(name)}` },
          ]),
        ),
        automations: Object.fromEntries(
          (Array.isArray(payload.automations)
            ? (payload.automations as unknown[])
            : []
          ).map((name) => [
            String(name),
            {
              automation_id: `open_house_${slug}_${String(name)}`,
              entity_id: `input_text.open_house_${slug}_${String(name)}`,
            },
          ]),
        ),
        inputs: Object.entries(bindings)
          .filter(([, binding]) => binding.kind === "literal")
          .map(([name, binding]) => ({ name, value: binding.value })),
        settings: MODULE_INPUTS.filter((row) => settings.includes(row.name)),
        outputs: outputs.map((output) => {
          const key = String(output.key ?? "");
          return {
            key,
            kind: "string",
            expression: "",
            entity_id: `sensor.open_house_${slug}_${key}`,
            value: null,
          };
        }),
      };
      HOSTED_MODULES.push(hosted);
      return { module: slug, modules: HOSTED_MODULES };
    }
    case COMMANDS.modulesSettings: {
      // The settings a card can change are the module's own rows, so a save
      // writes what it was given back onto the row and answers with the list --
      // which is what makes the save look like it went somewhere in `npm run
      // dev`, the same way the store's buttons do.
      guardStale(payload);
      const slug = String(payload.module);
      const bindings = (payload.bindings ?? {}) as Record<string, unknown>;
      // The whole set of inputs answered by an automation, which is what the
      // card sends: a name that has dropped off it is a cast taken away, and the
      // helper and the automation it stood for go with it.
      const automations = new Set(
        (Array.isArray(payload.automations)
          ? (payload.automations as unknown[])
          : []
        ).map(String),
      );
      const module = HOSTED_MODULES.find((row) => row.slug === slug);
      if (module) {
        for (const setting of module.settings) {
          if (setting.name in bindings) {
            setting.value = bindings[setting.name];
            setting.bound = true;
          }
        }
        // The automation cast is a *set*, kept on the module's own map the way
        // the server keeps it -- one entry per input, holding the automation's id
        // and the helper it writes -- with each row told both, so the card opens
        // its editor and says which entity is being read. A name that has dropped
        // off the set is the cast coming back off, which the card sends by
        // leaving it out.
        for (const name of Object.keys(module.automations)) {
          if (automations.has(name)) continue;
          delete module.automations[name];
          const row = module.settings.find((setting) => setting.name === name);
          if (row) {
            delete row.automation_id;
            delete row.automation_entity;
            delete row.automation_url;
            row.bound_kind = "literal";
          }
        }
        for (const name of automations) {
          const made = {
            automation_id: `open_house_${slug}_${name}`,
            entity_id: `input_text.open_house_${slug}_${name}`,
            url: `/config/automation/edit/open_house_${slug}_${name}`,
          };
          module.automations[name] = made;
          const row = module.settings.find((setting) => setting.name === name);
          if (row) {
            row.automation_id = made.automation_id;
            row.automation_entity = made.entity_id;
            row.automation_url = made.url;
            row.bound_kind = "automation";
          }
        }
      }
      return { module: slug, modules: HOSTED_MODULES };
    }
    case COMMANDS.modulesPublish: {
      // The switch beside a cast, and the one command here that makes an
      // *entity* rather than changing a row: on, the module's published list
      // gains the value and the row remembers the key it went out under; off,
      // both go away again. The id is spelled the way the server spells it
      // (`sensor.open_house_<slug>_<key>`) because the card reads the entity id
      // off the module rather than building one, and a mock that left it out
      // would draw a switch whose sentence never appeared.
      guardStale(payload);
      const slug = String(payload.module);
      const name = String(payload.setting);
      const publish = payload.publish === true;
      const module = HOSTED_MODULES.find((row) => row.slug === slug);
      const setting = module?.settings.find((row) => row.name === name);
      if (module && setting) {
        const key = setting.published_key ?? "";
        if (publish && !key) {
          const made = name;
          setting.published_key = made;
          module.outputs.push({
            key: made,
            kind: "string",
            expression: "",
            entity_id: `sensor.open_house_${slug}_${made}`,
            value: null,
          });
        } else if (!publish && key) {
          setting.published_key = "";
          const at = module.outputs.findIndex((output) => output.key === key);
          if (at !== -1) module.outputs.splice(at, 1);
        }
      }
      // The store comes back too, because publishing rewrites the definition
      // behind the module exactly as an edit does -- a screen listing what the
      // house offers is stale otherwise, and the client's own type says so.
      return { module: slug, modules: HOSTED_MODULES, store: STORED_MODULES };
    }
    case COMMANDS.modulesConfigSwitch: {
      guardStale(payload);
      const module = HOSTED_MODULES.find(
        (row) => row.slug === String(payload.module),
      );
      const name = String(payload.config);
      if (module && module.configs.includes(name)) {
        holdConfiguration(module);
        module.config = name;
        showConfiguration(module);
      }
      return { module: String(payload.module), modules: HOSTED_MODULES };
    }
    case COMMANDS.modulesConfigAdd: {
      const module = HOSTED_MODULES.find(
        (row) => row.slug === String(payload.module),
      );
      const name = String(payload.config);
      if (module && name !== "" && !module.configs.includes(name)) {
        // A copy of the running one, so it starts where the module is -- which
        // for a mock that only tracks values means holding nothing of its own
        // until something is edited under it.
        holdConfiguration(module);
        module.configs.push(name);
        module.config = name;
      }
      return { module: String(payload.module), modules: HOSTED_MODULES };
    }
    case COMMANDS.modulesConfigRename: {
      const module = HOSTED_MODULES.find(
        (row) => row.slug === String(payload.module),
      );
      const name = String(payload.config);
      const to = String(payload.to);
      if (
        module &&
        module.configs.includes(name) &&
        to !== "" &&
        !module.configs.includes(to)
      ) {
        module.configs[module.configs.indexOf(name)] = to;
        if (module.config === name) module.config = to;
        const held = CONFIG_VALUES[module.slug];
        const values = held?.[name];
        if (held && values) {
          held[to] = values;
          delete held[name];
        }
      }
      return { module: String(payload.module), modules: HOSTED_MODULES };
    }
    case COMMANDS.modulesConfigRemove: {
      const module = HOSTED_MODULES.find(
        (row) => row.slug === String(payload.module),
      );
      const name = String(payload.config);
      // The last one is refused rather than thrown: a mock that raised here
      // would break the screen for the one press the real server answers with a
      // sentence, and the screen shows the sentence.
      if (module && module.configs.length > 1 && module.configs.includes(name)) {
        module.configs.splice(module.configs.indexOf(name), 1);
        const held = CONFIG_VALUES[module.slug];
        if (held) delete held[name];
        const next = module.configs[0];
        if (module.config === name && next !== undefined) {
          module.config = next;
          showConfiguration(module);
        }
      }
      return { module: String(payload.module), modules: HOSTED_MODULES };
    }
    case COMMANDS.modulesUnhost: {
      const slug = String(payload.module);
      const at = HOSTED_MODULES.findIndex((row) => row.slug === slug);
      if (at !== -1) HOSTED_MODULES.splice(at, 1);
      return { module: slug, modules: HOSTED_MODULES };
    }
    case COMMANDS.modulesEdit: {
      // The one module command whose subject is the module rather than a copy of
      // it: the definition behind it is rewritten and every room running it is
      // built again. The mock has no definitions to rewrite, so it records what
      // the screen would read back -- the module's own name and document -- and
      // answers with the whole house and the store, which is what the client's
      // type asks for and what makes either listing redraw from one reply.
      const slug = String(payload.module);
      const module = HOSTED_MODULES.find((row) => row.slug === slug);
      if (module === undefined) {
        throw refuse(
          REFUSALS.invalidFormat,
          `this house hosts no module called ${slug}, so there is nothing to edit`,
        );
      }
      // **Required, unlike the title above.** These six are the module's
      // answers, and there is no reading of an absent one that is not a reading
      // of an empty one -- a caller who left one out would silently take every
      // answer away from a module installed in five rooms. The server's schema
      // says `vol.Required` for exactly this reason; the client's type said
      // "optional" and sent nothing, so the refusal arrived as `invalid_format`
      // with a sentence about a field the caller could not have known it owed.
      for (const field of [
        "bindings",
        "outputs",
        "settings",
        "casts",
        "flows",
        "automations",
      ]) {
        if (!(field in payload)) {
          throw refuse(REFUSALS.invalidFormat, `${field} is required to edit a module`);
        }
      }
      const title = String(payload.title ?? module.title);
      module.title = title;
      module.blueprint = typeof payload.key === "string" ? payload.key : module.blueprint;
      // The store row it was made from follows the module, because that is where
      // the next install reads its name from.
      const stored = STORED_MODULES.find((offer) => offer.slug === module.definition);
      if (stored !== undefined) stored.title = title;
      return { module: slug, modules: HOSTED_MODULES, store: STORED_MODULES };
    }
    case COMMANDS.modulesDetach: {
      // Give one row's logic a module of its own, and point the row at it. The
      // row is named either way round (`input` or `slot`), and what changes is
      // where its value comes from: the new module computes it, and the row
      // reads the output.
      const slug = String(payload.module);
      const source = HOSTED_MODULES.find((row) => row.slug === slug);
      if (source === undefined) {
        throw refuse(REFUSALS.notFound, `this house hosts no module called ${slug}`);
      }
      const row = typeof payload.input === "string" ? payload.input : String(payload.slot ?? "");
      const title = String(payload.title ?? "") || `${source.title} (${row})`;
      const newSlug = slugOf(title);
      const key = row === "" ? "on" : row;
      const roomId = String(payload.room_id ?? "");
      const roomName =
        roomId === ""
          ? "the whole house"
          : (ROOMS.find((entry) => entry.id === roomId)?.name ?? roomId);
      HOSTED_MODULES.push({
        slug: newSlug,
        title,
        blueprint: "",
        definition: "",
        room_id: roomId,
        room_name: roomName,
        slots: [],
        automation_id: `automation.${newSlug}`,
        config: "Default",
        configs: ["Default"],
        derived: {},
        flows: {},
        automations: {},
        inputs: [],
        settings: [],
        outputs: [
          {
            key,
            kind: "string",
            expression: "",
            entity_id: `sensor.open_house_${newSlug}_${key}`,
            value: null,
          },
        ],
      });
      // The row it left now *reads* that output rather than holding its own
      // logic, which is the whole of what a detach changes and what the row's
      // own published-key chip draws.
      const setting = source.settings.find((entry) => entry.name === row);
      if (setting !== undefined) setting.published_key = key;
      return {
        module: newSlug,
        title,
        room_id: roomId,
        key,
        // What will start it. The mock has no rule to read a trigger off, and a
        // module that never runs says so rather than inventing one.
        watched: Array.isArray(payload.trigger)
          ? (payload.trigger as unknown[]).map(String)
          : [],
        // The module the rule came from, and the row it left: the same shape the
        // input path answers with, so a screen holding either goes stale the same
        // way.
        source: slug,
        modules: HOSTED_MODULES,
      };
    }
    case COMMANDS.modulesDefine: {
      // The definition is *written to the store*, and the row it made is what the
      // reply names. This used to answer the title with the store unchanged, so a
      // module a person had just defined appeared in no list anywhere: the screen
      // said "saved" over a store that had not moved, and the next install of it
      // fell back to a fixture.
      const title = String(payload.title ?? "module");
      const slug = slugOf(title);
      const bindings = (payload.bindings ?? {}) as Record<string, ModuleBinding>;
      const at = STORED_MODULES.findIndex((offer) => offer.slug === slug);
      if (at !== -1 && payload.replace !== true) {
        // The server refuses to overwrite a definition nobody asked to replace,
        // for the reason an import does: re-importing a blueprint somebody has
        // edited is an update, and overwriting a module they authored is a
        // decision.
        throw refuse(
          REFUSALS.invalidFormat,
          `this house already offers a module called ${slug}; define it again with replace`,
        );
      }
      const row: ModuleOfferRow = {
        slug,
        title,
        description: String(payload.description ?? ""),
        author: String(payload.author ?? ""),
        version: String(payload.version ?? "1.0.0"),
        licence: String(payload.licence ?? "no_licence"),
        // The document a person picked, by key, as its provenance -- the same
        // path the row keeps for a blueprint.
        blueprint: typeof payload.key === "string" ? payload.key : "",
        // A definition is pinned when it names this house's *devices*: an input
        // answered with an entity cannot travel, which is the one thing the
        // sender of a file knows and the receiver cannot see.
        pinned: Object.values(bindings).some((binding) => binding.kind === "entity"),
        // The roles it reaches through, which is what an installing room binds.
        slots: Object.values(bindings)
          .filter((binding) => binding.kind === "slot" && binding.slot !== undefined)
          .map((binding) => String(binding.slot)),
        flows: Array.isArray(payload.flows) ? (payload.flows as string[]).map(String) : [],
        automations: (Array.isArray(payload.automations)
          ? (payload.automations as string[])
          : []
        ).map(String),
        missing_slots: [],
        // A define never installs, so a definition that replaces another keeps
        // the placements the old one had: those are rooms running it, and they
        // are not this command's to touch.
        deployed: at === -1 ? [] : STORED_MODULES[at]!.deployed,
      };
      if (at === -1) STORED_MODULES.push(row);
      else STORED_MODULES.splice(at, 1, row);
      return { module: slug, store: STORED_MODULES };
    }
    case COMMANDS.modulesDeploy: {
      const slug = String(payload.module);
      const roomId = String(payload.room_id ?? "");
      const row = STORED_MODULES.find((offer) => offer.slug === slug);
      if (row && !row.deployed.some((where) => where.room_id === roomId)) {
        const name =
          roomId === ""
            ? "the whole house"
            : (ROOMS.find((room) => room.id === roomId)?.name ?? roomId);
        const installed = roomId === "" ? slug : `${slug}_${roomId}`;
        row.deployed.push({
          slug: installed,
          room_id: roomId,
          room_name: name,
          running: true,
        });
        // The installation itself, so the room it was added to draws its card:
        // adding a module that then appeared nowhere would be the one thing
        // this journey must not do, and it is what the room's page reads.
        HOSTED_MODULES.push({
          slug: installed,
          title: `${row.title} (${name})`,
          blueprint: row.blueprint,
          definition: slug,
          room_id: roomId,
          room_name: name,
          // Waiting for everything the definition reaches through: a mock has
          // no devices to resolve them against, and an unanswered slot is the
          // state the card is most needed for.
          slots: row.slots.map((slot) => ({
            name: slot,
            input: slot,
            scope: "room",
            part: "",
            bound: "",
            bound_name: "",
            parts: [],
          })),
          automation_id: "",
          config: "Default",
          configs: ["Default"],
          derived: {},
          flows: {},
          automations: {},
          inputs: [],
          settings: [],
          outputs: [],
        });
      }
      // The *hosted* list, not `MODULES`: what comes back is what the house is
      // running, and this command has just added one to it. Answering with the
      // installed-pack fixture made deploying a module look like it had replaced
      // the house's packs with the fixture's.
      return { module: slug, modules: HOSTED_MODULES, store: STORED_MODULES };
    }
    case COMMANDS.modulesRemove: {
      const slug = String(payload.module);
      const at = STORED_MODULES.findIndex((offer) => offer.slug === slug);
      const installed = at === -1 ? 0 : STORED_MODULES[at]!.deployed.length;
      if (at !== -1) STORED_MODULES.splice(at, 1);
      return { removed: slug, installed, store: STORED_MODULES };
    }
    case COMMANDS.modulesExport:
      return {
        document: {
          open_house_module: 1,
          definition: STORED_MODULES.find(
            (offer) => offer.slug === String(payload.module),
          ) ?? { slug: String(payload.module) },
        },
      };
    case COMMANDS.modulesImport: {
      const document = payload.document as { definition?: ModuleOfferRow } | undefined;
      const arrived = document?.definition;
      if (!arrived?.slug) {
        throw refuse(REFUSALS.invalidFormat, "this is not a module");
      }
      const at = STORED_MODULES.findIndex((offer) => offer.slug === arrived.slug);
      const replaced = at !== -1;
      if (replaced) STORED_MODULES.splice(at, 1, { ...arrived, deployed: [] });
      else STORED_MODULES.push({ ...arrived, deployed: [] });
      return { imported: arrived.slug, replaced, store: STORED_MODULES };
    }
    case COMMANDS.devSources:
      // The one Dev command answerable with no house, which is why it is a
      // fixture rather than a page: a person can see what their automations
      // would become before they have finished setting the house up.
      return {
        automations: DEV_AUTOMATIONS,
        blueprints: DEV_BLUEPRINTS,
        // Read off the *store*, because that is where a saved module lives: a
        // mock whose `saved` list did not grow when `dev/save` ran would say a
        // save went nowhere.
        saved: STORED_MODULES.map((offer) => ({
          name: offer.slug,
          title: offer.title,
          description: offer.description,
          version: offer.version,
          // The mock has no manifest to count atoms from, so it counts what it
          // does know: the roles the definition reaches through.
          behaviours: offer.slots.length,
          options: 0,
          file: `${offer.slug}.yaml`,
        })),
      };
    case COMMANDS.devRead:
      // The reading, and the two vocabularies a decision is made against --
      // which come *with* the reading rather than from a second command, so a
      // screen renders one table from one reply.
      return { analysis: DEV_ANALYSIS, slots: DEV_SLOTS, services: DEV_SERVICES };
    case COMMANDS.devSave: {
      // The file is written and the verdict is about the file: the mock writes
      // the module into the store under the plan's own name, which is what makes
      // the Dev tab's saved list and the store agree afterwards.
      const plan = (payload.plan ?? {}) as { name?: unknown; title?: unknown; behaviours?: unknown };
      const name = slugOf(String(plan.name ?? "module"));
      const title =
        typeof plan.title === "string" && plan.title !== "" ? plan.title : name;
      const behaviours = Array.isArray(plan.behaviours) ? plan.behaviours.length : 0;
      const saved: DevSaved & { yaml: string } = {
        name,
        title,
        description: "",
        version: "1.0.0",
        behaviours,
        options: 0,
        file: `${name}.yaml`,
        yaml: `# ${title}\n`,
      };
      const at = STORED_MODULES.findIndex((offer) => offer.slug === name);
      const row: ModuleOfferRow = {
        slug: name,
        title,
        description: "",
        author: "",
        version: "1.0.0",
        licence: "no_licence",
        blueprint: typeof payload.key === "string" ? payload.key : "",
        pinned: false,
        slots: [],
        flows: [],
        automations: [],
        missing_slots: [],
        deployed: [],
      };
      if (at === -1) STORED_MODULES.push(row);
      else STORED_MODULES.splice(at, 1, { ...row, deployed: STORED_MODULES[at]!.deployed });
      // "Save and install there" installs in the same call, and a refusal to
      // install leaves the file alone -- so the write above stands and only the
      // placement does not happen. The mock has nothing that refuses a
      // placement, so it always places.
      if (payload.install === true) {
        answer(COMMANDS.moduleInstall, {
          room_id: String(payload.room_id ?? ""),
          pack: name,
        });
      }
      return { saved, modules: installed.map(withReach) };
    }
    case COMMANDS.devInstall: {
      // The module is named by pack name and read from the file, so what installs
      // is what is on disk. The mock has no disk, so a name it has never been
      // given is the one refusal it can make honestly -- an install of something
      // nobody authored is a `not_found`, not an empty list.
      const name = String(payload.name);
      if (!STORED_MODULES.some((offer) => offer.slug === name)) {
        throw refuse(REFUSALS.notFound, `no authored module is called ${name}`);
      }
      answer(COMMANDS.moduleInstall, {
        room_id: String(payload.room_id ?? ""),
        pack: name,
      });
      return { modules: installed.map(withReach) };
    }
    case COMMANDS.devExport: {
      // One installed module's behaviours as the automations that would do the
      // same. The automations name the entities the *room* holds, which is the
      // whole difference between an export and a copy of the manifest -- so the
      // mock reads the room's own bindings and writes them into the document.
      const pack = String(payload.pack);
      const record = installed.find((module) => module.pack === pack);
      if (record === undefined) {
        throw refuse(REFUSALS.notFound, `no module called ${pack} is installed`);
      }
      const roomId = typeof payload.room_id === "string" ? payload.room_id : record.room_id;
      const target = roomBound(roomId, "ceiling_light");
      const automations = [
        {
          alias: record.name,
          trigger: [{ platform: "state", entity_id: roomBound(roomId, "motion_sensor") }],
          action: target === null ? [] : [{ service: "light.turn_on", target: { entity_id: target } }],
        },
      ];
      // A role this room fills nothing for is named as a target that names
      // nothing, which is Home Assistant's "every entity of that domain" -- so
      // the YAML is correct and silent about why it would do nothing, and this
      // is the why.
      return {
        pack,
        automations,
        yaml: automations.map((automation) => `alias: ${automation.alias}\n`).join("---\n"),
        unresolved: target === null ? ["ceiling_light"] : [],
      };
    }
    case COMMANDS.activityList:
      return { entries: ACTIVITY };
    case COMMANDS.healthList:
      return { issues: HEALTH };
    case COMMANDS.dashboardGenerate:
      return { created: true, url_path: "open-house-kitchen" };
    default:
      throw new Error(`mock backend has no answer for ${type}`);
  }
}

class MockConnection implements HaConnection {
  async sendMessagePromise<T>(message: Record<string, unknown>): Promise<T> {
    return answer(String(message.type), message) as T;
  }

  async subscribeMessage<T>(
    callback: (value: T) => void,
    _message: Record<string, unknown>,
  ): Promise<UnsubscribeFunc> {
    const listener = (value: ActivityStreamEvent): void => callback(value as T);
    SUBSCRIBERS.add(listener);
    // Push one entry soon after subscribing, so the live stream is visible
    // without anything actually happening.
    //
    // The timer is held and cleared on unsubscribe, because a subscription a
    // screen has *dropped* is a subscription that must not deliver: the panel
    // unsubscribes when its element disconnects, and a timer that survived that
    // fired one more event into a callback pointing at a removed element -- the
    // one thing the real connection cannot do, and so the one thing the mock
    // must not teach.
    let timer: ReturnType<typeof setTimeout> | null = setTimeout(() => {
      timer = null;
      listener({
        kind: "entry",
        entry: {
          ...ACTIVITY[0]!,
          id: `live-${Date.now()}`,
          at: new Date().toISOString(),
          reason: "Streamed live from the mock backend.",
        },
      });
    }, 1500);
    return () => {
      SUBSCRIBERS.delete(listener);
      if (timer !== null) {
        clearTimeout(timer);
        timer = null;
      }
    };
  }
}

/** A `hass` the dev harness can hand to the panel. */
export function mockHass(): HassLike {
  return {
    connection: new MockConnection(),
    language: "en",
  };
}
