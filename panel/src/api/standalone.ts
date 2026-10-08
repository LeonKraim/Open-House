/**
 * A websocket connection to Home Assistant, without the frontend.
 *
 * Inside Home Assistant a panel is handed a `hass` object and its connection.
 * A Vite dev server, a preview deploy and the panel's own unit tests have no
 * such object, so this class speaks Home Assistant's websocket protocol
 * directly and satisfies the same `HaConnection` interface the client expects.
 * It exists so the client has exactly one contract rather than two code paths
 * ("connected by HA" versus "connected by us") that can drift.
 *
 * The protocol, for the record, because it is small enough to state:
 *
 *   1. On open, the client sends `{type: "auth", access_token}`.
 *      The server replies `{type: "auth_ok"}` or `{type: "auth_invalid"}`.
 *   2. A command is `{id, type, ...}`. The answer carries the same `id` and
 *      either `{success: true, result}` or `{success: false, error: {code,
 *      message}}`.
 *   3. A subscription's answer is `{success: true, result: null}`; each event
 *      after it is `{id, type: "event", event: {...}}`.
 *
 * The token is read from `?auth_token=` on the URL, or from the `hassTokens`
 * entry the Home Assistant frontend keeps in `localStorage`. Neither is a
 * secret the panel invents: in both cases it is the token an already
 * authenticated session holds, and the panel is one more view over it.
 */

import {
  PanelError,
  asPanelError,
  type HaConnection,
  type UnsubscribeFunc,
} from "./connection.ts";

interface Pending {
  resolve: (value: unknown) => void;
  reject: (error: Error) => void;
}

interface Subscription {
  callback: (value: unknown) => void;
}

/**
 * The subscribe commands this connection may unsubscribe, by name.
 *
 * A set and not a map, because Home Assistant has exactly **one** unsubscribe
 * command for every message subscription: `unsubscribe_events`, carrying the id
 * of the command that subscribed. This used to be a table of per-topic names --
 * `open_house/activity/subscribe` -> `open_house/activity/unsubscribe` -- and the
 * second of those is not a command the integration registers, so the unsubscribe
 * was answered `unknown_command` and the *host* kept its listener: the panel
 * stopped being called (the id was dropped locally, so the screen looked right)
 * while the house went on pushing an activity entry down a socket to nobody,
 * once per tick, for as long as the page was open.
 *
 * The `type` still has to be named here, and that is deliberate: the id this
 * connection sends the generic unsubscribe with is a *command* id, and a command
 * that was not a subscription has nothing to unsubscribe. A `type` this set does
 * not name is not one the panel opened.
 */
const SUBSCRIBED_COMMANDS = new Set<string>([
  "open_house/activity/subscribe",
]);

/** Home Assistant's own command for ending a message subscription, by id. */
const UNSUBSCRIBE_COMMAND = "unsubscribe_events";

/** How the token is found, in priority order. */
export function findAccessToken(
  search: string = globalThis.location?.search ?? "",
): string | null {
  try {
    const fromUrl = new URLSearchParams(search).get("auth_token");
    if (fromUrl) return fromUrl;
  } catch {
    // A malformed search string is not a reason to stop looking.
  }
  try {
    const raw = globalThis.localStorage?.getItem("hassTokens");
    if (!raw) return null;
    const parsed = JSON.parse(raw) as { access_token?: unknown };
    return typeof parsed.access_token === "string" ? parsed.access_token : null;
  } catch {
    return null;
  }
}

/** The websocket URL for an HTTP(S) HA origin. */
export function websocketUrl(hassUrl: string): string {
  const url = new URL("/api/websocket", hassUrl);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  return url.toString();
}

export class StandaloneConnection implements HaConnection {
  private readonly socket: WebSocket;
  private nextId = 1;
  private readonly pending = new Map<number, Pending>();
  private readonly subscriptions = new Map<number, Subscription>();
  /** Whether the socket has gone away; set once, and never cleared. */
  private closed = false;
  /** Why it went, kept so a command issued afterwards fails with the reason. */
  private closedError: PanelError | null = null;

  private constructor(socket: WebSocket) {
    this.socket = socket;
    this.socket.addEventListener("message", (event) =>
      this.onMessage(String((event as MessageEvent).data)),
    );
    // A socket that goes away -- an HA restart, an expired token, a network
    // blip -- must not leave every caller waiting: without these, every
    // in-flight command and every later one hangs for ever, and the panel sits
    // on "Reading the house..." with nothing to say. Reject the lot, and
    // remember why so the next call fails at once.
    this.socket.addEventListener("close", () =>
      this.onClosed("The connection to Home Assistant closed."),
    );
    this.socket.addEventListener("error", () =>
      this.onClosed("The connection to Home Assistant failed."),
    );
  }

  /** The socket is gone: fail everything outstanding, once. */
  private onClosed(reason: string): void {
    if (this.closed) return;
    this.closed = true;
    const error = new PanelError("unavailable", reason);
    this.closedError = error;
    for (const pending of this.pending.values()) pending.reject(error);
    this.pending.clear();
    // A subscription has no reply channel to reject on, so it is simply dropped;
    // its unsubscribe checks `closed` and does nothing rather than sending on a
    // socket that is no longer there.
    this.subscriptions.clear();
  }

  /**
   * Authenticate, and resolve once the server has *accepted the token*.
   *
   * `auth_required` is the server saying hello, not saying yes: it arrives, the
   * token goes back, and the answer to the token is a second message --
   * `auth_ok` or `auth_invalid`. This resolved on the hello, so `connect()`
   * returned a connection that was not yet authenticated and a caller could
   * command it in the gap. Home Assistant answers a command sent before `auth`
   * by closing the socket, so the cost of getting this wrong is every command
   * issued in that window failing as a dropped connection.
   *
   * `auth_invalid` is handled for the same reason: it used to fall through --
   * it is not `auth_required`, so nothing happened, and the socket simply sat
   * there until Home Assistant closed it. A refused token is `unauthorized`
   * (there is nothing to retry and nothing wrong with the address), while a
   * socket that never got as far as the handshake is `connection_failed`.
   */
  static async connect(
    hassUrl: string,
    token: string,
  ): Promise<StandaloneConnection> {
    const socket = new WebSocket(websocketUrl(hassUrl));
    const connection = new StandaloneConnection(socket);
    await new Promise<void>((resolve, reject) => {
      // Every listener is removed on every path. The handshake used to leave
      // `first` attached when it settled from the already-open branch or on
      // error, and -- worse -- had no `close` handler at all, so a socket that
      // closed before `auth_required` arrived (a bad URL, a refused token) left
      // `connect()` pending for ever and the panel with it.
      let answered = false;
      const onMessage = (event: MessageEvent): void => {
        let data: { type?: string };
        try {
          data = JSON.parse(String(event.data)) as { type?: string };
        } catch {
          return;
        }
        if (data.type === "auth_required") {
          if (answered) return;
          answered = true;
          socket.send(JSON.stringify({ type: "auth", access_token: token }));
          return;
        }
        if (data.type === "auth_ok") {
          settle();
          resolve();
          return;
        }
        if (data.type === "auth_invalid") {
          settle();
          reject(
            new PanelError(
              "unauthorized",
              "Home Assistant refused the token this page holds. Sign in again " +
                "and reopen the panel.",
            ),
          );
        }
      };
      const onError = (): void => {
        settle();
        reject(new PanelError("connection_failed", "The socket did not open."));
      };
      const onClose = (): void => {
        settle();
        reject(
          new PanelError(
            "connection_failed",
            "The socket closed before it authenticated.",
          ),
        );
      };
      const settle = (): void => {
        socket.removeEventListener("message", onMessage);
        socket.removeEventListener("error", onError);
        socket.removeEventListener("close", onClose);
      };
      socket.addEventListener("message", onMessage);
      socket.addEventListener("error", onError);
      socket.addEventListener("close", onClose);
      if (socket.readyState === WebSocket.OPEN) {
        // A socket already open has missed the `auth_required` greeting, so the
        // token goes without waiting to be asked; the answer is still `auth_ok`
        // or `auth_invalid`, and this still waits for it.
        answered = true;
        socket.send(JSON.stringify({ type: "auth", access_token: token }));
      }
    });
    return connection;
  }

  private onMessage(raw: string): void {
    let message: Record<string, unknown>;
    try {
      message = JSON.parse(raw) as Record<string, unknown>;
    } catch {
      return;
    }
    const id = typeof message.id === "number" ? message.id : null;
    if (id === null) return;

    if (message.type === "event") {
      this.subscriptions.get(id)?.callback(message.event);
      return;
    }
    const pending = this.pending.get(id);
    if (!pending) return;
    this.pending.delete(id);
    if (message.success === true) {
      pending.resolve(message.result);
    } else {
      const error = (message.error ?? {}) as { code?: string; message?: string };
      pending.reject(
        new PanelError(error.code ?? "unknown", error.message ?? "Command failed."),
      );
    }
  }

  private send(message: Record<string, unknown>): number {
    const id = this.nextId++;
    this.socket.send(JSON.stringify({ id, ...message }));
    return id;
  }

  sendMessagePromise<T = unknown>(
    message: Record<string, unknown>,
  ): Promise<T> {
    // A command after the socket has gone fails at once, with the reason the
    // socket gave, rather than hanging on a reply that will never come.
    if (this.closed) return Promise.reject(this.closure());
    return new Promise<T>((resolve, reject) => {
      const id = this.send(message);
      this.pending.set(id, {
        resolve: (value) => resolve(value as T),
        reject,
      });
    }).catch((error: unknown) => {
      throw asPanelError(error);
    });
  }

  subscribeMessage<T = unknown>(
    callback: (value: T) => void,
    message: Record<string, unknown>,
  ): Promise<UnsubscribeFunc> {
    if (this.closed) return Promise.reject(this.closure());
    const subscribable =
      typeof message.type === "string" && SUBSCRIBED_COMMANDS.has(message.type);
    return new Promise<UnsubscribeFunc>((resolve, reject) => {
      const id = this.send(message);
      this.pending.set(id, {
        resolve: () => {
          this.subscriptions.set(id, {
            callback: (value) => callback(value as T),
          });
          resolve(() => {
            this.subscriptions.delete(id);
            // The local drop above is what the panel sees; the send below is
            // what the *house* sees. A `type` the set does not name is a
            // subscription the panel cannot address on the wire, so the
            // unsubscribe is the local drop and nothing more -- rather than a
            // send of `"undefined/unsubscribe"` into the void. And it is the
            // generic `unsubscribe_events`, by the *command id*, because that is
            // the one Home Assistant defines for a message subscription; a
            // per-topic name is not a command at all, and answering the host
            // `unknown_command` left it listening.
            if (!subscribable || this.closed) return undefined;
            this.socket.send(
              JSON.stringify({
                id: this.nextId++,
                type: UNSUBSCRIBE_COMMAND,
                subscription: id,
              }),
            );
            return undefined;
          });
        },
        reject,
      });
    }).catch((error: unknown) => {
      throw asPanelError(error);
    });
  }

  /** The error a call after closure fails with. */
  private closure(): PanelError {
    return (
      this.closedError ??
      new PanelError("unavailable", "The connection to Home Assistant closed.")
    );
  }

  close(): void {
    this.socket.close();
  }
}

/**
 * Connect to the Home Assistant origin the page was served from.
 *
 * Returns `null` when no token is available, which is the honest answer for a
 * plain preview with no Home Assistant behind it -- the caller renders the
 * "not connected" state rather than an unhandled rejection.
 */
export async function connectToOrigin(): Promise<StandaloneConnection | null> {
  const token = findAccessToken();
  const origin = globalThis.location?.origin;
  if (!token || !origin) return null;
  return StandaloneConnection.connect(origin, token);
}
