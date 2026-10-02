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
  BindingSuggestion,
  Capabilities,
  DecisionLogEntry,
  HealthIssue,
  HouseOverview,
  HouseScope,
  ImportPreview,
  InstalledModule,
  ModuleInstallReply,
  ModuleOffer,
  ProfileRef,
  RoomDetail,
  RoomSummary,
  StoreEntry,
} from "../api/models.ts";
import type { JsonSchema } from "../components/schema-spec.ts";
import { COMMANDS } from "../api/protocol.ts";
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

const PROFILES: ProfileRef[] = [
  {
    name: "evening",
    label: "Evening",
    description: "Warm, dimmed lighting for the evening.",
    kind: "room",
    axis: "lighting",
    active: true,
  },
  {
    name: "dim",
    label: "Dim",
    description: "Low brightness for bedtime.",
    kind: "room",
    axis: "lighting",
    active: true,
  },
  {
    name: "vacation",
    label: "Vacation",
    description: "Presence simulation while nobody is home.",
    kind: "house",
    axis: null,
    active: false,
  },
];

const MODULES: InstalledModule[] = [
  {
    pack: "bedtime_button",
    name: "Bedtime button",
    version: "1.0.0",
    room_id: "bedroom",
    house: false,
    scope: "room",
    enabled: true,
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
      },
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
      },
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
        },
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
  return {
    ...module,
    behaviours: module.behaviours.map((behaviour) => {
      const rooms = atomRooms(module.pack, behaviour.id);
      return {
        ...behaviour,
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
 * Settings written for a room, over the packs' defaults.
 *
 * A room's form has to remember a save the way the engine does, or the write
 * looks like it went nowhere: `roomDetail` derives `options` from each pack's
 * `default` every read, so without somewhere to keep what was written the next
 * `roomGet` would answer with the default again.
 */
const ROOM_OPTIONS: Record<string, Record<string, unknown>> = {};

function roomDetail(id: string): RoomDetail {
  const summary = ROOMS.find((room) => room.id === id) ?? ROOMS[0]!;
  // Only the room's own modules' settings, keyed the way the server keys them.
  const packs = installed.filter((module) => module.room_id === summary.id);
  return {
    id: summary.id,
    name: summary.name,
    type: summary.type,
    type_label: summary.type_label,
    bindings: [
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
      },
    ],
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
    axes: [
      {
        id: "lighting",
        label: "Lighting",
        profiles: PROFILES.filter((profile) => profile.axis === "lighting"),
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
    case COMMANDS.roomCreate:
    case COMMANDS.roomUpdate:
      return roomDetail(String(payload.room_id));
    case COMMANDS.roomBind:
    case COMMANDS.roomReplace:
    case COMMANDS.roomUnbind: {
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
        })),
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
    case COMMANDS.modulesList:
      // Every installed module, the house-placed one included: the Modules tab
      // is the one screen that lists both homes, so a mock that answered only
      // the room ones would leave its "the house" chip unrendered everywhere.
      return { modules: installed.map(withReach) };
    case COMMANDS.profilesList:
      return { profiles: PROFILES };
    case COMMANDS.profileActivate:
      return roomDetail(String(payload.room_id));
    case COMMANDS.storeIndex:
      return { entries: STORE, generated_at: new Date().toISOString(), cached: false };
    case COMMANDS.storeInstall:
      return { installed: MODULES[0] };
    case COMMANDS.activityList:
      return { entries: ACTIVITY };
    case COMMANDS.healthList:
      return { issues: HEALTH };
    case COMMANDS.exportDocument:
      return {
        format_version: "1.1.0",
        house: "Ada's house",
        exported_at: new Date().toISOString(),
        rooms: ROOMS.map((room) => ({
          id: room.id,
          name: room.name,
          type: room.type,
          bindings: {},
        })),
      };
    case COMMANDS.importPreview:
      return {
        format_version: "1.1.0",
        compatible: true,
        diff: [
          {
            kind: "change",
            scope: "room",
            path: "rooms.kitchen.bindings.ceiling_light",
            before: null,
            after: "light.kitchen_ceiling",
          },
        ],
        relink: [
          {
            room_id: "kitchen",
            slot: "ceiling_light",
            registry_id: "reg-kitchen-ceiling",
            entity_id: null,
            candidates: CANDIDATES,
          },
        ],
        notes: ["One binding needs re-linking."],
      } satisfies ImportPreview;
    case COMMANDS.importApply:
      return { applied: true, snapshot_id: "snap-1", diff: [] };
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
    setTimeout(() => {
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
