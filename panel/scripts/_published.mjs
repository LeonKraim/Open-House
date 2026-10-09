// Walk the published Store through the integration, against a live container.
//
//   python store/check.py http://127.0.0.1:8090     # a neighbour's module to find
//   node panel/scripts/_published.mjs
//
// `store/check.py` is what puts somebody *else's* module on the Store: a house
// cannot rate, comment on or install its own work, so every one of those needs a
// module this house did not publish, and publishing one for the walk would be
// publishing it as this house. So the neighbour is stood up first, by the script
// that already proves the backend answers, and this walk is the integration
// talking to the same Store through the commands the panel calls.
//
// It needs `HA_TOKEN` and `HA_BASE_URL` in `.env.local`, and the integration's
// `store_url` set to a Store that is running.

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
    // A payload may not carry its own `id`. A Home Assistant websocket message
    // is one flat object whose `id` is the message's own number, so a payload
    // key of that name overwrites it rather than travelling beside it: the reply
    // is matched to nothing and the call waits for ever, which is exactly how
    // `published/install` was broken until a walk hung on it.
    if ("id" in payload) {
      throw new Error(
        `a websocket payload may not carry 'id' (${payload.type}); name it for what it is -- module_id, room_id, slug`
      );
    }
    const id = ++this.#id;
    return new Promise((ok) => {
      this.#pending.set(id, ok);
      this.#ws.send(JSON.stringify({ id, ...payload }));
    });
  }

  async #handshake() {
    await new Promise((ok) => {
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
  console.log(`${condition ? "ok  " : "FAIL"}  ${label}${detail ? `  (${detail})` : ""}`);
}

async function main() {
  const api = await Api.open();

  // -- The address is configured, and nobody has claimed a name yet ----------
  const status = await api.call("open_house/published/status");
  check("status succeeds", status.success === true, JSON.stringify(status.error ?? ""));
  const url = status.result?.url ?? "";
  check("status names the configured Store", url.startsWith("http"), url);
  if (!url.startsWith("http")) {
    console.log("\nNo Store is configured, so there is nothing else to walk.");
    api.close();
    return;
  }

  // -- The listing, as the tab opens it --------------------------------------
  const browse = await api.call("open_house/published/browse", { search: "" });
  check("browse succeeds", browse.success === true, JSON.stringify(browse.error ?? ""));
  const notInstalled = browse.result?.not_installed ?? [];
  const installed = browse.result?.installed ?? [];
  check(
    "the split is two lists",
    Array.isArray(notInstalled) && Array.isArray(installed),
    JSON.stringify(browse.result ?? {})
  );
  // The neighbour is looked for on both sides, because a second run of this walk
  // has already installed it: what the walk is about is the module, and which
  // side it is on is one of the things being checked rather than where it is
  // found.
  const isInstalled = installed.some((row) => row.slug === "evening_lighting");
  const neighbour = isInstalled
    ? installed.find((row) => row.slug === "evening_lighting")
    : notInstalled.find((row) => row.slug === "evening_lighting");
  check(
    "the neighbour's module is on the Store",
    Boolean(neighbour),
    [...installed, ...notInstalled].map((row) => row.slug).join(",")
  );
  if (neighbour) {
    check("it is not marked mine", neighbour.mine === false);
    check("it carries its publisher's name", neighbour.publisher === "marqbarq", neighbour.publisher);
    check("it carries a rating", typeof neighbour.rating === "number", String(neighbour.rating));
    check("it carries no document", !("document" in neighbour));
  }

  // -- Claiming a name, once ------------------------------------------------
  // A name is claimed once and kept, so a second run of this walk meets the
  // name it claimed on the first. That is the walk repeating, not the Store
  // changing its mind, and it is why "already this house's" is accepted here.
  const before = status.result?.name ?? "";
  const claimed = await api.call("open_house/published/claim", { name: "walkhouse" });
  check(
    "the name was free, or is already this house's",
    claimed.success === true || before === "walkhouse",
    JSON.stringify(claimed.error ?? claimed.result ?? "")
  );
  const after = await api.call("open_house/published/status");
  check("the name is kept", after.result?.name === "walkhouse", JSON.stringify(after.result ?? {}));

  // The refusal the claim form exists for: a name somebody else holds. The
  // sentence is the Store's own, and the panel shows it word for word.
  const again = await api.call("open_house/published/claim", { name: "marqbarq" });
  check("claiming a name already taken is refused", again.success === false, JSON.stringify(again.result ?? ""));
  check(
    "and the refusal says what to do",
    /pick another name/i.test(String(again.error?.message ?? "")),
    JSON.stringify(again.error ?? {})
  );

  // -- Installing the neighbour's module -------------------------------------
  // Skipped on a re-run: the module is in this house already, and `install`
  // without `replace` is right to refuse a module that is already offered. What
  // a re-run still exercises is everything that is *about* the module rather
  // than bringing it in.
  if (neighbour && !isInstalled) {
    const install = await api.call("open_house/published/install", { module_id: neighbour.id });
    check("installing succeeds", install.success === true, JSON.stringify(install.error ?? ""));
    check("and it says which module landed", install.result?.module === "evening_lighting", JSON.stringify(install.result ?? {}));

    const listed = await api.call("open_house/published/browse", { search: "" });
    const nowInstalled = (listed.result?.installed ?? []).find((row) => row.slug === "evening_lighting");
    check("it moves to the installed side", Boolean(nowInstalled), JSON.stringify(listed.result?.not_installed ?? []));
  } else if (neighbour) {
    console.log("(evening_lighting is already installed here -- skipping the install half)");
  }

  // -- Rating and commenting on it -------------------------------------------
  if (neighbour) {
    const rated = await api.call("open_house/published/rate", { module_id: neighbour.id, stars: 5 });
    check("rating a stranger's module succeeds", rated.success === true, JSON.stringify(rated.error ?? ""));

    const said = await api.call("open_house/published/comment", { module_id: neighbour.id, body: "Falls over at dusk, as promised." });
    check("commenting succeeds", said.success === true, JSON.stringify(said.error ?? ""));

    const read = await api.call("open_house/published/comments", { module_id: neighbour.id });
    check("the comment reads back", read.success === true, JSON.stringify(read.error ?? ""));
    const listed = read.result?.comments ?? [];
    check(
      "and is the one that was said",
      listed.some((row) => String(row.body ?? "").includes("Falls over at dusk")),
      JSON.stringify(listed)
    );

    const reread = await api.call("open_house/published/rate", { module_id: neighbour.id, stars: 3 });
    check("rating again is an update, not a refusal", reread.success === true, JSON.stringify(reread.error ?? ""));
  }

  // -- Publishing this house's own work --------------------------------------
  // `modules/store` rather than `modules/list`: the latter is what this house has
  // *running* (a pack in a room), and publishing is about a definition the house
  // offers, which is what the store listing is.
  const own = await api.call("open_house/modules/store", { room_id: "" });
  check("the house's own offerings read back", own.success === true, JSON.stringify(own.error ?? ""));
  const offered = own.result?.store ?? [];
  const mine = offered.find((row) => row.slug === "evening_lighting");
  if (mine) {
    const published = await api.call("open_house/published/publish", { module: "evening_lighting", summary: "Published by the walk." });
    check("publishing this house's module succeeds", published.success === true, JSON.stringify(published.error ?? ""));

    const listed = await api.call("open_house/published/browse", { search: "" });
    const both = [...(listed.result?.installed ?? []), ...(listed.result?.not_installed ?? [])];
    const mineRows = both.filter((row) => row.slug === "evening_lighting" && row.mine === true);
    check("and it comes back marked as this house's", mineRows.length >= 1, JSON.stringify(both.map((r) => [r.slug, r.mine])));

    const ownRate = await api.call("open_house/published/rate", { module_id: mineRows[0]?.id ?? "", stars: 5 });
    check("the Store refuses a house rating its own module", ownRate.success === false, JSON.stringify(ownRate.result ?? ""));
  } else {
    console.log("(no module named evening_lighting in this house -- skipping the publish half)");
  }

  api.close();
}

await main();

console.log(`\n${checked - failures.length}/${checked} checks passed`);
if (failures.length) {
  console.log("failed:");
  for (const line of failures) console.log(`  - ${line}`);
  process.exitCode = 1;
}
