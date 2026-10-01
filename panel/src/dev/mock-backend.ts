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
  ImportPreview,
  InstalledModule,
  ModuleOffer,
  ProfileRef,
  RoomDetail,
  RoomSummary,
  StoreEntry,
} from "../api/models.ts";
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
    enabled: true,
    behaviours: [
      { id: "lights_off", label: "Lights off", enabled: true },
      { id: "sleep_mode", label: "Sleep mode", enabled: true },
    ],
  },
  {
    pack: "motion_lighting",
    name: "Motion lighting",
    version: "1.2.0",
    room_id: "kitchen",
    enabled: true,
    behaviours: [{ id: "lux_motion", label: "Lux-gated motion", enabled: true }],
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

function roomDetail(id: string): RoomDetail {
  const summary = ROOMS.find((room) => room.id === id) ?? ROOMS[0]!;
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
        slot: "lux_sensor",
        label: "Lux sensor",
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
      properties: {
        motion_hold_seconds: {
          type: "number",
          title: "Motion hold",
          description: "How long the light stays on after motion stops.",
          default: 120,
        },
        lux_threshold: {
          type: "number",
          title: "Lux threshold",
          description: "Turn the light on only below this lux.",
          default: 80,
        },
        night_mode: {
          type: "boolean",
          title: "Night mode",
          description: "Use a dimmer level between 23:00 and 06:00.",
          default: true,
        },
        level: {
          type: "string",
          title: "Default level",
          enum: ["low", "medium", "high"],
          default: "medium",
        },
      },
    },
    options: { motion_hold_seconds: 120, lux_threshold: 80, night_mode: true, level: "medium" },
    modules: MODULES.filter((module) => module.room_id === summary.id),
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
    case COMMANDS.roomBind:
    case COMMANDS.roomReplace:
    case COMMANDS.roomUnbind:
      return roomDetail(String(payload.room_id));
    case COMMANDS.roomCandidates:
      return { candidates: CANDIDATES };
    case COMMANDS.roomOptionsGet:
    case COMMANDS.roomOptionsSet:
      return { schema: roomDetail("kitchen").options_schema, values: payload.values ?? {} };
    case COMMANDS.roomAvailableModules:
      return { offers: OFFERS };
    case COMMANDS.moduleInstall:
      return { installed: MODULES[0], room: roomDetail(String(payload.room_id)) };
    case COMMANDS.moduleUninstall:
      return roomDetail(String(payload.room_id));
    case COMMANDS.moduleSetEnabled:
      return MODULES[0];
    case COMMANDS.modulesList:
      return { modules: MODULES };
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
