// Drive the open_house websocket API against the live container and check the
// answers against the panel's own view models.
//
// The panel renders `panel/src/api/models.ts`; the live path serves the same
// shapes from `custom_components/open_house/views.py`. Nothing in the build
// forces the two to agree -- the panel reads a missing field as `undefined` and
// renders an empty row rather than raising -- so the agreement has to be
// asserted somewhere, and this is that place.
//
// Run it against a stack that is up:
//   node panel/scripts/ws-probe.mjs
//
// It needs `HA_TOKEN` and `HA_BASE_URL` in `.env.local` at the checkout root.

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const root = join(here, "..", "..");

function env(name) {
  const text = readFileSync(join(root, ".env.local"), "utf8");
  for (const line of text.split(/\r?\n/)) {
    const at = line.indexOf("=");
    if (at > 0 && line.slice(0, at).trim() === name) {
      return line.slice(at + 1).trim();
    }
  }
  throw new Error(`${name} is not in .env.local`);
}

const BASE = env("HA_BASE_URL").replace(/\/$/, "");
const TOKEN = env("HA_TOKEN");

class Api {
  #ws;
  #id = 0;
  #pending = new Map();

  static async open() {
    const api = new Api();
    api.#ws = new WebSocket(`${BASE.replace(/^http/, "ws")}/api/websocket`);
    await new Promise((ok, fail) => {
      api.#ws.onerror = () => fail(new Error("websocket refused"));
      api.#ws.onopen = ok;
    });
    api.#ws.onmessage = (event) => api.#receive(JSON.parse(event.data));
    await api.#handshake();
    return api;
  }

  #receive(message) {
    if (message.type === "result") {
      const waiting = this.#pending.get(message.id);
      this.#pending.delete(message.id);
      if (waiting) waiting(message);
    }
  }

  #send(payload) {
    const id = ++this.#id;
    return new Promise((ok) => {
      this.#pending.set(id, ok);
      this.#ws.send(JSON.stringify({ id, ...payload }));
    });
  }

  async #handshake() {
    const hello = await new Promise((ok) => {
      const previous = this.#ws.onmessage;
      this.#ws.onmessage = (event) => {
        const message = JSON.parse(event.data);
        if (message.type === "auth_required") {
          this.#ws.send(JSON.stringify({ type: "auth", access_token: TOKEN }));
        } else if (message.type === "auth_ok") {
          this.#ws.onmessage = previous;
          ok(message);
        } else if (message.type === "auth_invalid") {
          throw new Error(`auth refused: ${message.message}`);
        }
      };
    });
    if (!hello) throw new Error("no auth_ok");
  }

  call(command, extra = {}) {
    return this.#send({ type: command, ...extra });
  }

  close() {
    this.#ws.close();
  }
}

const failures = [];
let checked = 0;

function check(label, condition, detail = "") {
  checked += 1;
  if (!condition) failures.push(`${label}${detail ? ` -- ${detail}` : ""}`);
}

function has(object, keys) {
  return keys.filter((key) => !(key in (object ?? {})));
}

function isMapping(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

async function main() {
  const api = await Api.open();

  const caps = await api.call("open_house/capabilities");
  check("capabilities succeeds", caps.success === true, JSON.stringify(caps));
  if (caps.success) {
    check("capabilities shape", has(caps.result, ["admin", "user_name", "version", "engine_api", "needs_setup"]).length === 0, JSON.stringify(has(caps.result, ["admin", "user_name", "version", "engine_api", "needs_setup"])));
  }

  const overview = await api.call("open_house/overview");
  check("overview succeeds", overview.success === true, JSON.stringify(overview.error ?? ""));
  if (overview.success) {
    const missing = has(overview.result, ["name", "mode", "rooms", "people", "modules_installed", "issues", "updated_at"]);
    check("overview shape", missing.length === 0, missing.join(","));
    const rooms = overview.result.rooms;
    check("overview.rooms is a list", Array.isArray(rooms), typeof rooms);
    check("overview.issues has three counts", isMapping(overview.result.issues)
      && ["info", "warning", "error"].every((k) => typeof overview.result.issues[k] === "number"),
      JSON.stringify(overview.result.issues));
    if (Array.isArray(rooms) && rooms.length) {
      const missingRoom = has(rooms[0], [
        "id", "name", "type", "type_label", "bound_slots", "total_slots",
        "required_unbound", "mode", "active_profiles", "occupied",
        "auto_lighting", "issue_count",
      ]);
      check("room summary shape", missingRoom.length === 0, missingRoom.join(","));
    }
  }

  const list = await api.call("open_house/rooms/list");
  check("rooms/list succeeds", list.success === true, JSON.stringify(list.error ?? ""));
  const rooms = list.success && Array.isArray(list.result) ? list.result
    : list.success && Array.isArray(list.result?.rooms) ? list.result.rooms : [];
  check("rooms/list yields rooms", rooms.length > 0, JSON.stringify(list.result)?.slice(0, 200));

  for (const room of rooms.slice(0, 3)) {
    const detail = await api.call("open_house/rooms/get", { room_id: room.id });
    check(`rooms/get ${room.id}`, detail.success === true, JSON.stringify(detail.error ?? ""));
    if (!detail.success) continue;
    const missing = has(detail.result, [
      "id", "name", "type", "type_label", "bindings", "options_schema",
      "options", "modules", "active_profiles", "mode", "axes",
    ]);
    check(`rooms/get ${room.id} shape`, missing.length === 0, missing.join(","));
    for (const binding of detail.result.bindings ?? []) {
      const miss = has(binding, [
        "slot", "label", "required", "accepts_domains", "entity_id",
        "registry_id", "friendly_name", "domain", "state", "status", "last_changed",
      ]);
      check(`binding ${room.id}/${binding.slot} shape`, miss.length === 0, miss.join(","));
      check(
        `binding ${room.id}/${binding.slot} status is known`,
        ["ok", "unavailable", "unknown", "missing", "domain_mismatch", "unbound"].includes(binding.status),
        String(binding.status),
      );
    }
  }

  const store = await api.call("open_house/store/index");
  check("store/index succeeds", store.success === true, JSON.stringify(store.error ?? ""));
  if (store.success) {
    check("store/index shape", has(store.result, ["entries", "generated_at", "cached"]).length === 0);
    check("store/index lists packs", Array.isArray(store.result.entries) && store.result.entries.length > 0,
      `entries=${store.result.entries?.length}`);
    for (const entry of (store.result.entries ?? []).slice(0, 3)) {
      const miss = has(entry, [
        "pack", "name", "description", "version", "author", "tier", "license",
        "available", "installed_version", "update_available",
        "update_requires_review", "abandoned", "sha256",
      ]);
      check(`store entry ${entry.pack} shape`, miss.length === 0, miss.join(","));
    }
  }

  const health = await api.call("open_house/health/list");
  check("health/list succeeds", health.success === true, JSON.stringify(health.error ?? ""));
  if (health.success) {
    check("health/list shape", has(health.result, ["issues"]).length === 0);
    for (const issue of health.result.issues ?? []) {
      const miss = has(issue, ["severity", "code", "title", "detail", "room_id", "entity_id", "repairs_flow_id"]);
      check("health issue shape", miss.length === 0, miss.join(","));
      check("health severity is a panel word", ["info", "warning", "error"].includes(issue.severity), String(issue.severity));
    }
  }

  const activity = await api.call("open_house/activity/list");
  check("activity/list succeeds", activity.success === true, JSON.stringify(activity.error ?? ""));

  const modules = await api.call("open_house/modules/list");
  check("modules/list succeeds", modules.success === true, JSON.stringify(modules.error ?? ""));

  const profiles = await api.call("open_house/profiles/list");
  check("profiles/list succeeds", profiles.success === true, JSON.stringify(profiles.error ?? ""));

  // `open_house/import_export/export` was asserted here and is a command that
  // exists nowhere: not in `websocket_api.py`, not in `api/protocol.ts`. The
  // assertion therefore failed on every run and reported `unknown_command` --
  // a real answer to a made-up question, and one that reads as a broken export
  // rather than as a probe pointed at a name nobody ever registered. The
  // command a person actually reaches for is the profile export, which with no
  // profile named answers the whole set as one document.
  const exported = await api.call("open_house/profiles/export");
  check("profiles/export succeeds", exported.success === true, JSON.stringify(exported.error ?? ""));
  if (exported.success) {
    const document = exported.result?.document ?? exported.result;
    check("export carries a document", isMapping(document), typeof document);
  }

  const missingRoom = await api.call("open_house/rooms/get", { room_id: "no-such-room" });
  check("an unknown room is an error, not a crash", missingRoom.success === false, JSON.stringify(missingRoom));
  check("an unknown room answers not_found", missingRoom.error?.code === "not_found", JSON.stringify(missingRoom.error));

  if (rooms.length) {
    const options = await api.call("open_house/rooms/options/get", { room_id: rooms[0].id });
    check("rooms/options/get succeeds", options.success === true, JSON.stringify(options.error ?? ""));
    const candidates = await api.call("open_house/rooms/candidates", { room_id: rooms[0].id, slot: "motion_sensor" });
    check("rooms/candidates succeeds", candidates.success === true, JSON.stringify(candidates.error ?? ""));
    const available = await api.call("open_house/rooms/available_modules", { room_id: rooms[0].id });
    check("rooms/available_modules succeeds", available.success === true, JSON.stringify(available.error ?? ""));
  }

  // **The paste box, which is the third way a source arrives and the one no
  // other assertion here touches.** A picker is read from Home Assistant and a
  // module from its own record, but a *paste* is text the panel sent -- so it is
  // the one path whose reader has nothing to consult, and the one that would
  // break silently if `_dev_text` stopped taking `kind: "text"`. Read rather
  // than hosted: this asserts the reading, and hosting is a mutation.
  //
  // The document is an automation, because that is the kind that arrives flat --
  // no `blueprint:` block, no inputs -- and a flat document is the case that
  // once came back as Home Assistant's generic "Unknown error" rather than a
  // reading (the annotation on an automation's `raw_config` cannot be written by
  // PyYAML's safedumper). A paste does not go through that door, and this pins
  // that the door it does go through answers.
  const pasted = await api.call("open_house/modules/read", {
    kind: "text",
    text: [
      "alias: A probe that pastes",
      "triggers:",
      "  - trigger: state",
      "    entity_id: sensor.open_house_probe",
      "actions:",
      "  - action: input_boolean.turn_on",
      "    target:",
      "      entity_id: input_boolean.open_house_probe",
    ].join("\n"),
  });
  check("a pasted document reads", pasted.success === true, JSON.stringify(pasted.error ?? ""));
  if (pasted.success) {
    check("a pasted document has a title", typeof pasted.result?.source?.title === "string"
      && pasted.result.source.title.length > 0, JSON.stringify(pasted.result?.source));
    check("a pasted document offers its service as a candidate",
      Array.isArray(pasted.result?.candidates)
      && pasted.result.candidates.some((row) => String(row.name ?? "").includes("input_boolean.turn_on")),
      JSON.stringify((pasted.result?.candidates ?? []).map((row) => row.name)));
  }

  const nothingPasted = await api.call("open_house/modules/read", { kind: "text", text: "   " });
  check("an empty paste is refused, not crashed", nothingPasted.success === false, JSON.stringify(nothingPasted));
  check("an empty paste says so in words",
    /nothing pasted/i.test(String(nothingPasted.error?.message ?? "")),
    JSON.stringify(nothingPasted.error));

  api.close();

  console.log(`${checked - failures.length}/${checked} assertions hold`);
  for (const failure of failures) console.log("  FAIL", failure);
  process.exit(failures.length ? 1 : 0);
}

main().catch((error) => {
  console.error("probe failed:", error.message);
  process.exit(1);
});
