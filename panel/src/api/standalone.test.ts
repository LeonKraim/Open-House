/**
 * Unit tests for the standalone websocket connection.
 *
 * `StandaloneConnection` is the panel's own Home Assistant client -- the one a
 * dev server, a preview deploy and these tests use in place of the `hass`
 * object. It is small, it is pure transport, and it is exactly the kind of code
 * a bug hides in: nothing imports it in the panel's own run, so nothing has ever
 * exercised the auth handshake or the unsubscribe on the wire.
 *
 * A real socket cannot be opened from a unit test, so a `WebSocket` stands in
 * that records every frame and lets the test deliver the server's replies. That
 * is the whole point: the *frames* are the contract, and a frame that is wrong
 * is invisible until it reaches a live server, which answers it -- or, worse,
 * refuses to.
 */

import { strict as assert } from "node:assert";
import { test } from "node:test";

import { PanelError } from "./connection.ts";
import { COMMANDS } from "./protocol.ts";
import { StandaloneConnection } from "./standalone.ts";

/** A `WebSocket` that records what was sent and replays what a test delivers. */
class FakeSocket {
  static last: FakeSocket | null = null;
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSING = 2;
  static readonly CLOSED = 3;

  readyState = FakeSocket.CONNECTING;
  readonly sent: string[] = [];
  readonly url: string;
  private readonly listeners = new Map<string, ((event: unknown) => void)[]>();

  // A parameter property (`constructor(readonly url: string)`) is not allowed
  // under Node's strip-only TypeScript, which these tests run under, so the
  // field is declared and assigned by hand.
  constructor(url: string) {
    this.url = url;
    FakeSocket.last = this;
  }

  addEventListener(type: string, listener: (event: unknown) => void): void {
    const list = this.listeners.get(type) ?? [];
    list.push(listener);
    this.listeners.set(type, list);
  }

  removeEventListener(type: string, listener: (event: unknown) => void): void {
    const list = this.listeners.get(type);
    if (!list) return;
    this.listeners.set(
      type,
      list.filter((fn) => fn !== listener),
    );
  }

  send(data: string): void {
    this.sent.push(data);
  }

  close(): void {
    this.readyState = FakeSocket.CLOSED;
    this.emit("close", {});
  }

  /** Hand a raw event to every listener for `type`. */
  emit(type: string, event: unknown): void {
    for (const listener of [...(this.listeners.get(type) ?? [])]) listener(event);
  }

  /** Deliver a server message as the socket would: JSON in `event.data`. */
  deliver(message: unknown): void {
    this.emit("message", { data: JSON.stringify(message) });
  }

  /** Every frame sent, parsed back into the object it was. */
  frames(): Record<string, unknown>[] {
    return this.sent.map((raw) => JSON.parse(raw) as Record<string, unknown>);
  }
}

/** The most recent frame, or a failure if nothing has been sent. */
function lastFrame(socket: FakeSocket): Record<string, unknown> {
  const frames = socket.frames();
  const frame = frames[frames.length - 1];
  assert.ok(frame, "no frame was sent");
  return frame;
}

/** Run `body` with the fake `WebSocket` installed, and restore it after. */
async function withFakeSocket(body: () => Promise<void>): Promise<void> {
  const real = globalThis.WebSocket;
  FakeSocket.last = null;
  globalThis.WebSocket = FakeSocket as unknown as typeof WebSocket;
  try {
    await body();
  } finally {
    globalThis.WebSocket = real;
  }
}

/** Connect and complete the handshake, leaving a ready connection. */
async function openConnection(): Promise<StandaloneConnection> {
  const pending = StandaloneConnection.connect("http://ha.local:8123", "secret");
  const socket = FakeSocket.last;
  assert.ok(socket, "connect did not open a socket");
  socket.deliver({ type: "auth_required", ha_version: "2025.1.0" });
  socket.deliver({ type: "auth_ok", ha_version: "2025.1.0" });
  return pending;
}

test("connect resolves only once the token has been accepted", async () => {
  // `auth_required` is the server saying hello, not saying yes. `connect` used
  // to resolve on it, handing back a connection that was not yet authenticated;
  // a command issued in that window is answered by Home Assistant closing the
  // socket, so every command the caller made in the gap failed as a drop.
  await withFakeSocket(async () => {
    let settled = false;
    const pending = StandaloneConnection.connect("http://ha.local", "secret").then(
      (connection) => {
        settled = true;
        return connection;
      },
    );
    const socket = FakeSocket.last;
    assert.ok(socket, "connect did not open a socket");

    socket.deliver({ type: "auth_required", ha_version: "2025.1.0" });
    assert.deepEqual(lastFrame(socket), { type: "auth", access_token: "secret" });
    assert.equal(settled, false, "connect resolved on the greeting, before auth_ok");

    socket.deliver({ type: "auth_ok", ha_version: "2025.1.0" });
    const connection = await pending;
    assert.equal(settled, true);
    assert.ok(connection instanceof StandaloneConnection);
  });
});

test("a refused token rejects as unauthorized rather than hanging", async () => {
  // `auth_invalid` used to fall through the handler -- it is neither
  // `auth_required` nor `auth_ok` -- and the socket simply sat there until HA
  // closed it. A refused token is a `unauthorized`: there is nothing to retry
  // and nothing wrong with the address, so the banner can say so.
  await withFakeSocket(async () => {
    const pending = StandaloneConnection.connect("http://ha.local", "bad");
    const socket = FakeSocket.last;
    assert.ok(socket, "connect did not open a socket");
    socket.deliver({ type: "auth_required" });
    socket.deliver({ type: "auth_invalid", message: "Invalid access token" });
    await assert.rejects(
      pending,
      (error: unknown) => error instanceof PanelError && error.code === "unauthorized",
    );
  });
});

test("a socket that closes before authenticating does not hang connect", async () => {
  // A bad URL or a refused token can close the socket before `auth_required`
  // ever arrives. Without a `close` handler the handshake waited for ever and
  // the panel sat on "Reading the house..." behind it.
  await withFakeSocket(async () => {
    const pending = StandaloneConnection.connect("http://ha.local", "x");
    const socket = FakeSocket.last;
    assert.ok(socket, "connect did not open a socket");
    socket.emit("close", {});
    await assert.rejects(
      pending,
      (error: unknown) => error instanceof PanelError && error.code === "connection_failed",
    );
  });
});

test("unsubscribing an activity stream uses Home Assistant's own command", async () => {
  // This is the bug the whole file was reached for. The unsubscribe used to be
  // a *per-topic* name, `open_house/activity/unsubscribe`, which the integration
  // does not register: the host answered `unknown_command` and kept its
  // listener, so the house went on pushing activity down a socket to nobody.
  // Home Assistant defines exactly one command for ending a message
  // subscription, and it is `unsubscribe_events`, carrying the id of the
  // command that subscribed.
  await withFakeSocket(async () => {
    const connection = await openConnection();
    let seen = 0;
    const subscribing = connection.subscribeMessage(
      () => {
        seen += 1;
      },
      { type: COMMANDS.activitySubscribe },
    );
    const socket = FakeSocket.last;
    assert.ok(socket, "no socket");
    const subscribeFrame = lastFrame(socket);
    assert.equal(subscribeFrame.type, COMMANDS.activitySubscribe);
    const subscriptionId = subscribeFrame.id as number;

    // The server acknowledges the subscription, then pushes an event.
    socket.deliver({ id: subscriptionId, success: true, result: null });
    const unsubscribe = await subscribing;
    socket.deliver({ id: subscriptionId, type: "event", event: { kind: "entry" } });
    assert.equal(seen, 1, "an event did not reach the subscriber");

    unsubscribe();
    const frame = lastFrame(socket);
    assert.equal(frame.type, "unsubscribe_events");
    assert.equal(frame.subscription, subscriptionId);
    assert.notEqual(frame.type, "open_house/activity/unsubscribe");
  });
});

test("a subscription the connection did not open is dropped locally, not sent", async () => {
  // The id sent with `unsubscribe_events` is a *command* id. A command that was
  // not a subscription has nothing to unsubscribe, so a `type` the connection
  // did not open is dropped locally and nothing goes on the wire -- rather than
  // a send of `"undefined/unsubscribe"` into the void.
  await withFakeSocket(async () => {
    const connection = await openConnection();
    const subscribing = connection.subscribeMessage(() => undefined, {
      type: "open_house/some/other/subscribe",
    });
    const socket = FakeSocket.last;
    assert.ok(socket, "no socket");
    const id = lastFrame(socket).id as number;
    socket.deliver({ id, success: true, result: null });
    const unsubscribe = await subscribing;

    const before = socket.frames().length;
    unsubscribe();
    assert.equal(socket.frames().length, before, "an unknown subscription was sent an unsubscribe");
  });
});

test("a command issued after the socket closes fails at once", async () => {
  // A promise that never settles is the worst answer: the screen waits with
  // nothing to say. A closed socket is `unavailable`, which the banner knows a
  // sentence for.
  await withFakeSocket(async () => {
    const connection = await openConnection();
    const socket = FakeSocket.last;
    assert.ok(socket, "no socket");
    socket.emit("close", {});
    await assert.rejects(
      () => connection.sendMessagePromise({ type: COMMANDS.roomsList }),
      (error: unknown) => error instanceof PanelError && error.code === "unavailable",
    );
  });
});

test("a command in flight when the socket closes is rejected, not left pending", async () => {
  await withFakeSocket(async () => {
    const connection = await openConnection();
    const socket = FakeSocket.last;
    assert.ok(socket, "no socket");
    const inFlight = connection.sendMessagePromise({ type: COMMANDS.roomsList });
    socket.emit("close", {});
    await assert.rejects(
      inFlight,
      (error: unknown) => error instanceof PanelError && error.code === "unavailable",
    );
  });
});
