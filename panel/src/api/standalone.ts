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

  private constructor(socket: WebSocket) {
    this.socket = socket;
    this.socket.addEventListener("message", (event) =>
      this.onMessage(String((event as MessageEvent).data)),
    );
  }

  /** Authenticate and resolve once the server has accepted the token. */
  static async connect(
    hassUrl: string,
    token: string,
  ): Promise<StandaloneConnection> {
    const socket = new WebSocket(websocketUrl(hassUrl));
    const connection = new StandaloneConnection(socket);
    await new Promise<void>((resolve, reject) => {
      socket.addEventListener("error", () =>
        reject(new PanelError("connection_failed", "The socket did not open.")),
      );
      socket.addEventListener("message", function first(event) {
        const data = JSON.parse(String((event as MessageEvent).data)) as {
          type?: string;
        };
        if (data.type !== "auth_required") return;
        socket.removeEventListener("message", first);
        socket.send(JSON.stringify({ type: "auth", access_token: token }));
        resolve();
      });
      if (socket.readyState === WebSocket.OPEN) {
        socket.send(JSON.stringify({ type: "auth", access_token: token }));
        resolve();
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
    return new Promise<UnsubscribeFunc>((resolve, reject) => {
      const id = this.send(message);
      this.pending.set(id, {
        resolve: () => {
          this.subscriptions.set(id, {
            callback: (value) => callback(value as T),
          });
          resolve(() => {
            this.subscriptions.delete(id);
            this.socket.send(
              JSON.stringify({
                id: this.nextId++,
                type: message.type + "/unsubscribe",
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
