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
import { test } from "node:test";

import { OpenHouseClient } from "./client.ts";
import { COMMANDS } from "./protocol.ts";
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
