/**
 * Unit tests for the websocket client.
 *
 * They run under Node's own test runner with type stripping (`npm test`), so
 * they exercise the same source the bundle ships rather than a compiled copy.
 * The thing under test is the *contract*: which command each method sends, how
 * it unwraps the answer, and that a refusal keeps its error code. The server
 * side of that contract is another track's; these tests pin the panel's half of
 * it so a rename here is a failing test rather than a silent break at runtime.
 */

import { strict as assert } from "node:assert";
import { readdirSync, readFileSync } from "node:fs";
import { test } from "node:test";

import { OpenHouseClient } from "./client.ts";
import { COMMANDS, REFUSALS } from "./protocol.ts";
import { PanelError, type HaConnection, type HassLike } from "./connection.ts";

interface Call {
  type: string;
  [key: string]: unknown;
}

/** A `hass` whose `callWS` records the message and answers from a table. */
function fakeHass(
  answers: Record<string, unknown> | ((message: Call) => unknown),
): { hass: HassLike; calls: Call[] } {
  const calls: Call[] = [];
  const hass: HassLike = {
    callWS<T>(message: Record<string, unknown>): Promise<T> {
      calls.push(message as Call);
      const answer =
        typeof answers === "function"
          ? answers(message as Call)
          : answers[message.type as string];
      if (answer instanceof Error) return Promise.reject(answer);
      return Promise.resolve(answer as T);
    },
  };
  return { hass, calls };
}

test("capabilities sends the capabilities command", async () => {
  const { hass, calls } = fakeHass({
    [COMMANDS.capabilities]: { admin: true, user_name: "Ada", version: "1", engine_api: "1.0.0", needs_setup: false },
  });
  const result = await new OpenHouseClient(hass).capabilities();
  assert.equal(result.admin, true);
  assert.deepEqual(calls[0], { type: "open_house/capabilities" });
});

test("rooms unwraps the rooms key", async () => {
  const { hass } = fakeHass({
    [COMMANDS.roomsList]: { rooms: [{ id: "kitchen", name: "Kitchen" }] },
  });
  const rooms = await new OpenHouseClient(hass).rooms();
  assert.equal(rooms.length, 1);
  assert.equal(rooms[0]?.id, "kitchen");
});

test("a missing collection key is an empty list, not undefined", async () => {
  const { hass } = fakeHass({ [COMMANDS.roomsList]: {} });
  const rooms = await new OpenHouseClient(hass).rooms();
  assert.deepEqual(rooms, []);
});

test("bind carries the room, slot and entity", async () => {
  const { hass, calls } = fakeHass(() => ({ id: "kitchen" }));
  await new OpenHouseClient(hass).bind("kitchen", "ceiling_light", "light.kitchen");
  assert.deepEqual(calls[0], {
    type: "open_house/rooms/bind",
    room_id: "kitchen",
    slot: "ceiling_light",
    entity_id: "light.kitchen",
  });
});

test("a write carries the revision the page was read at", async () => {
  // The revision is how the server tells a write decided against the house's
  // current profiles from one decided against a profile it has since replaced.
  // Without it the only thing that could stop a stale save is the panel, and
  // the panel is the half that cannot know -- it is not running the switch.
  const { hass, calls } = fakeHass(() => ({ id: "kitchen" }));
  const client = new OpenHouseClient(hass);
  await client.bind("kitchen", "ceiling_light", "light.kitchen", 7);
  await client.unbind("kitchen", "ceiling_light", 7);
  await client.setRoomOptions("kitchen", { mode: "away" }, 7);
  await client.setHouseOptions({ "module.fan.hold": 30 }, 7);
  await client.modulesSettings(
    "dim_a_light",
    {},
    undefined,
    undefined,
    [],
    undefined,
    7,
  );
  await client.modulesConfigSwitch("dim_a_light", "evening", 7);
  assert.deepEqual(
    calls.map((call) => [call.type, call.revision]),
    [
      ["open_house/rooms/bind", 7],
      ["open_house/rooms/unbind", 7],
      ["open_house/rooms/options/set", 7],
      ["open_house/rooms/options/set", 7],
      ["open_house/modules/settings", 7],
      ["open_house/modules/configs/switch", 7],
    ],
  );
});

test("a write with no revision sends no revision at all", async () => {
  // **Absent is not zero**, and the server reads the two differently: a write
  // with no revision is a caller that is not rendering a page, and it is let
  // through. Defaulting the key to `0` would make every such write stale the
  // moment the house's profiles had moved once.
  const { hass, calls } = fakeHass(() => ({ id: "kitchen" }));
  await new OpenHouseClient(hass).bind("kitchen", "ceiling_light", "light.kitchen");
  assert.equal("revision" in (calls[0] ?? {}), false);
});

test("a refusal keeps the server's error code", async () => {
  const { hass } = fakeHass(() => new PanelError("unauthorized", "Admins only."));
  await assert.rejects(
    () => new OpenHouseClient(hass).deleteRoom("kitchen"),
    (error: unknown) =>
      error instanceof PanelError &&
      error.code === "unauthorized" &&
      error.message === "Admins only.",
  );
});

test("a command refused while the house reloads is asked again", async () => {
  // `not_ready` is the one refusal that means "ask again": a room subentry was
  // just written, Home Assistant is reloading the entry, and the house is
  // unreadable for a moment. Answering it as final is what put "Room not found
  // -- This room is no longer in the house" on a room that had just been made.
  let attempts = 0;
  const { hass, calls } = fakeHass(() => {
    attempts += 1;
    if (attempts < 3) return new PanelError(REFUSALS.notReady, "reloading");
    return { id: "kitchen", name: "Kitchen" };
  });
  const room = await new OpenHouseClient(hass).room("kitchen");
  assert.equal(room.id, "kitchen");
  assert.equal(attempts, 3);
  assert.equal(calls.length, 3);
});

test("a refusal that is an answer is not asked again", async () => {
  // `not_setup` reads almost the same as `not_ready` and means the opposite:
  // no house has ever been made, and no amount of asking will change that.
  // Retrying it would delay the setup prompt by the length of the backoff.
  let attempts = 0;
  const { hass } = fakeHass(() => {
    attempts += 1;
    return new PanelError(REFUSALS.notSetup, "no house yet");
  });
  await assert.rejects(
    () => new OpenHouseClient(hass).rooms(),
    (error: unknown) =>
      error instanceof PanelError && error.code === REFUSALS.notSetup,
  );
  assert.equal(attempts, 1);
});

test("a plain Error becomes a PanelError with an unknown code", async () => {
  const { hass } = fakeHass(() => new Error("socket closed"));
  await assert.rejects(
    () => new OpenHouseClient(hass).health(),
    (error: unknown) =>
      error instanceof PanelError &&
      error.code === "unknown" &&
      error.message === "socket closed",
  );
});

test("availableModules unwraps the offers key", async () => {
  const { hass, calls } = fakeHass({
    [COMMANDS.roomAvailableModules]: { offers: [{ pack: "bedtime" }] },
  });
  const offers = await new OpenHouseClient(hass).availableModules("bedroom");
  assert.equal(offers[0]?.pack, "bedtime");
  assert.deepEqual(calls[0], {
    type: "open_house/rooms/available_modules",
    room_id: "bedroom",
  });
});

test("subscribeActivity refuses when there is no connection", async () => {
  const client = new OpenHouseClient({
    callWS: async <T>(_message: Record<string, unknown>) => ({}) as T,
  });
  await assert.rejects(
    () => client.subscribeActivity(() => undefined),
    (error: unknown) => error instanceof PanelError && error.code === "unavailable",
  );
});

/** A connection that answers nothing and immediately pushes one event. */
class FakeConnection implements HaConnection {
  unsubscribed = false;

  async sendMessagePromise<T>(_message: Record<string, unknown>): Promise<T> {
    return {} as T;
  }

  async subscribeMessage<T>(
    callback: (value: T) => void,
    _message: Record<string, unknown>,
  ): Promise<() => void> {
    callback({ kind: "entry" } as T);
    return () => {
      this.unsubscribed = true;
    };
  }
}

/**
 * The writes a page makes, and the receiver each is made on.
 *
 * Anchored to the receiver rather than to the method name alone, because
 * `replace` is also `String.replace` and a screen that trims a label would
 * otherwise be asked for a revision. The receivers here are the two the panel
 * writes through: the client it holds and the one it asks its parent for.
 */
const GUARDED_WRITES =
  /(?:requireClient\(\)|\bclient)\.(bind|replace|unbind|setRoomOptions|setHouseOptions|modulesSettings|modulesConfigSwitch)\(/g;

/** Every call above in one file, each as its own argument text. */
function guardedWritesIn(source: string): string[] {
  const found: string[] = [];
  GUARDED_WRITES.lastIndex = 0;
  for (let match = GUARDED_WRITES.exec(source); match; match = GUARDED_WRITES.exec(source)) {
    // To the matching close paren, counting depth: a call's arguments hold
    // further calls, and stopping at the first `)` would read half of one.
    let depth = 1;
    let at = match.index + match[0].length;
    while (at < source.length && depth > 0) {
      if (source[at] === "(") depth += 1;
      else if (source[at] === ")") depth -= 1;
      at += 1;
    }
    found.push(`${match[1]}(${source.slice(match.index + match[0].length, at - 1).replace(/\s+/g, " ")})`);
  }
  return found;
}

test("every write a page makes says which revision it was read at", () => {
  // The test above pins that a *write with no revision sends no revision* --
  // which the server has to allow, because a caller that is not rendering a
  // page is a real caller. A page is not one of those, and the difference is
  // invisible at the call site: `unbind(HOUSE_ID, slot)` on the House page's
  // slot row shipped that way, so the one control that clears a house slot
  // went on working on a page the server had already told to stop. Nothing
  // caught it, because the argument that was missing is optional and the
  // method it was missing from is one of seven.
  const screens = ["../tabs/", "../components/"];
  const missing: string[] = [];
  let found = 0;
  for (const screen of screens) {
    const dir = new URL(screen, import.meta.url);
    for (const name of readdirSync(dir)) {
      if (!name.endsWith(".ts") || name.endsWith(".test.ts")) continue;
      const source = readFileSync(new URL(name, dir), "utf8");
      for (const call of guardedWritesIn(source)) {
        found += 1;
        if (!/\brevision\b/.test(call)) missing.push(`${screen}${name}: ${call}`);
      }
    }
  }
  // The reads are the test: a regex that stopped matching would leave nothing
  // to check and pass, so the count is asserted too. It is a floor and not the
  // number, because a page adding a write should not fail this test.
  assert.ok(found >= 10, `only ${found} writes were read, so this test checked almost nothing`);
  assert.deepEqual(
    missing,
    [],
    "a page writes profile-governed state without the revision it read it at:\n" +
      missing.join("\n"),
  );
});

test("subscribeActivity forwards events and can unsubscribe", async () => {
  const connection = new FakeConnection();
  const client = new OpenHouseClient({ connection });
  let received: unknown = null;
  const unsubscribe = await client.subscribeActivity((event) => {
    received = event;
  });
  assert.deepEqual(received, { kind: "entry" });
  await unsubscribe();
  assert.equal(connection.unsubscribed, true);
});
