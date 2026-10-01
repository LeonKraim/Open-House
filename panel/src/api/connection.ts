/**
 * The slice of Home Assistant's frontend contract the panel depends on.
 *
 * A `panel_custom` element is handed a `hass` object, and `hass` is the *only*
 * channel through which a panel may reach Home Assistant state (the developer
 * documentation is explicit about this). Two members of it matter here:
 *
 *   * `hass.callWS(message)` -- the convenience wrapper over the websocket API.
 *   * `hass.connection.subscribeMessage(callback, message)` -- the same
 *     connection, for the one command that streams rather than answers.
 *
 * `connection.sendMessagePromise` is the layer under `callWS` and is declared
 * too, because a `hass` object that carries only a `connection` is the shape a
 * standalone (non-`panel_custom`) mount has, and the client prefers `callWS`
 * when it is there and falls back to the connection when it is not.
 *
 * The interfaces are structural and deliberately minimal: they name what this
 * panel uses and nothing else, so a change to Home Assistant's frontend that
 * this panel does not touch cannot break its build.
 */

/** Handle returned by a subscription; calling it stops the stream. */
export type UnsubscribeFunc = () => Promise<void> | void;

/** The websocket connection Home Assistant exposes on `hass.connection`. */
export interface HaConnection {
  sendMessagePromise<T = unknown>(
    message: Record<string, unknown>,
  ): Promise<T>;
  subscribeMessage<T = unknown>(
    callback: (value: T) => void,
    message: Record<string, unknown>,
  ): Promise<UnsubscribeFunc>;
}

/** Home Assistant's authenticated user, as the frontend exposes it. */
export interface HassAuth {
  data?: {
    hassUrl?: string;
    access_token?: string;
    expires?: number;
    refresh_token?: string;
  } | null;
}

/** The `hass` object, reduced to what the panel reads. */
export interface HassLike {
  connection?: HaConnection;
  callWS?: <T = unknown>(message: Record<string, unknown>) => Promise<T>;
  auth?: HassAuth;
  language?: string;
}

/**
 * The `panel_custom` properties Home Assistant sets on the element.
 *
 * `embed_iframe: false` (the default) means the element is created directly in
 * the frontend and these are assigned before the first render.
 */
export interface PanelInfo {
  config?: Record<string, unknown>;
  title?: string;
  url_path?: string;
}

/** The frontend route, used only to keep the panel reproducible across reloads. */
export interface RouteInfo {
  path?: string;
  prefix?: string;
}

/**
 * The connection the client actually talks through.
 *
 * `callWS` is preferred when present, because it is the documented entry point;
 * `sendMessagePromise` is the fallback for a bare connection, which is what the
 * standalone mount supplies.
 */
export function sendMessage<T>(
  hass: HassLike,
  message: Record<string, unknown>,
): Promise<T> {
  if (typeof hass.callWS === "function") {
    return hass.callWS<T>(message);
  }
  if (hass.connection) {
    return hass.connection.sendMessagePromise<T>(message);
  }
  return Promise.reject(
    new PanelError(
      "unavailable",
      "The panel has no Home Assistant connection. It must be opened from the " +
        "Open House sidebar entry rather than as a standalone page.",
    ),
  );
}

/** Whether the `hass` object can carry a command at all. */
export function isConnected(hass: HassLike | undefined): boolean {
  return (
    hass !== undefined &&
    (typeof hass.callWS === "function" || hass.connection !== undefined)
  );
}

/**
 * An error carrying the `code` Home Assistant's websocket API returns.
 *
 * A command the server refuses answers with `{code, message}`; passing the code
 * through rather than stringifying it lets a screen branch on `not_found` or
 * `unauthorized` instead of matching on prose.
 */
export class PanelError extends Error {
  readonly code: string;

  constructor(code: string, message: string) {
    super(message);
    this.name = "PanelError";
    this.code = code;
  }
}

/** Coerce any thrown value into a `PanelError` so callers branch on one shape. */
export function asPanelError(error: unknown): PanelError {
  if (error instanceof PanelError) return error;
  if (error && typeof error === "object") {
    const candidate = error as { code?: unknown; message?: unknown };
    const code = typeof candidate.code === "string" ? candidate.code : "unknown";
    const message =
      typeof candidate.message === "string"
        ? candidate.message
        : String(error);
    return new PanelError(code, message);
  }
  return new PanelError("unknown", String(error));
}
