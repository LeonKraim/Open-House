/**
 * The element every screen in the panel extends.
 *
 * It exists to hold the three decisions that would otherwise be made
 * independently in fifteen components and made differently in three of them:
 *
 *   * Rendering into the light DOM. Home Assistant's own panels do this, and
 *     the reason is concrete: a shadow root would block the frontend's
 *     stylesheet, so the panel would have to re-implement every Home Assistant
 *     control it embeds. Rendering in the light DOM means `ha-icon` and the
 *     CSS custom properties work as they do everywhere else, and the panel
 *     injects its own stylesheet once, from the root element.
 *   * Owning the client. A screen never reaches for `hass` directly; it gets a
 *     client, so the command contract has one reader.
 *   * Owning the load state machine. `loading` / `error` / `data` is the same
 *     in every screen, and a helper here is cheaper than the same four lines
 *     repeated and one of them forgetting to clear `error`.
 *
 * ## Screen state must be declared, not merely assigned
 *
 * Lit re-renders an element when a *reactive* property changes, and a reactive
 * property is one named in `static properties` (or `static state`). A plain
 * class field is an own data property: writing it schedules nothing.
 *
 * Every screen below used to keep its state in plain fields and lean on
 * hand-written `this.requestUpdate()` calls after each `await`. That worked for
 * anything driven by a fetch -- the call was right there in the `finally` -- and
 * failed for everything driven by a *click*, because a click handler that
 * assigned a field and returned looked like it had done something and had not.
 * The symptoms were spread out and looked unrelated: "Add room" never opened
 * its form, a room's name was not a link to its settings, Rename never appeared,
 * the device picker's Cancel never closed it, the module filter never filtered.
 *
 * So: a screen's own mutable state goes in `static properties` as
 * `{ state: true }`. `state` rather than `attribute`, because these values come
 * from the screen and never from markup -- nothing in the DOM should be able to
 * write them. The `this.requestUpdate()` calls that remain are harmless and are
 * left where they are; they are simply no longer the only thing keeping the
 * screen alive.
 */

import { LitElement, html, type TemplateResult } from "lit";
import { OpenHouseClient } from "./api/client.ts";
import type { RefusalCode } from "./api/protocol.ts";
import {
  asPanelError,
  type HassLike,
  type PanelError,
  type PanelInfo,
  type RouteInfo,
} from "./api/connection.ts";

/** The properties Home Assistant sets on the `panel_custom` element. */
export interface PanelProperties {
  hass: HassLike;
  narrow: boolean;
  route: RouteInfo;
  panel: PanelInfo;
}

/** A screen that can be told to reload the data it shows. */
export interface Reloadable {
  reload(): void;
}

/**
 * A sentence a person can act on, per error code the server sends.
 *
 * One key per code the server can send, plus the codes a *transport* adds
 * (`unavailable`, and `unknown` for a thrown value that carried no code of its
 * own), so no refusal reaches a screen as a bare `Code: ...` line.
 *
 * The type is the list, and that is the point of it: `Record<BannerCode, string>`
 * with `BannerCode` built out of `REFUSALS` means a code added to the server's
 * closed set is a *compile error* here until somebody writes the sentence for
 * it, rather than a `Code: not_setup` line nobody noticed. `invalid_format` maps
 * to the empty string deliberately: its message is the one the live layer wrote
 * for a person to read ("ambient_light_sensor is required", in so many words),
 * and a suggestion under it would be a second sentence saying less.
 *
 * `not_ready` is here even though the client retries it, because the sentence a
 * person sees is the one from *after* the retry window -- the house has been
 * away for fifteen seconds by then, and "try again" is the truthful thing to
 * say.
 */
type BannerCode =
  | RefusalCode
  | "unavailable"
  | "home_assistant_error"
  | "unknown_error"
  | "unknown";

const SUGGESTIONS: Record<BannerCode, string> = {
  unauthorized: "This action needs an administrator.",
  not_setup: "Open House has not been set up in this house yet.",
  not_ready: "The house is reloading; try again in a moment.",
  not_found: "That item no longer exists; reload the panel.",
  invalid_format: "",
  unavailable: "Home Assistant is not reachable right now.",
  stale_page:
    "This page was read before the house's profiles moved; reload the panel " +
    "to read it again.",
  home_assistant_error: "Home Assistant refused this; its log has the details.",
  unknown_error: "Something went wrong; Home Assistant's log has the details.",
  unknown: "Something went wrong; Home Assistant's log has the details.",
};

export abstract class OpenHouseElement extends LitElement {
  static override properties = {
    hass: { attribute: false },
    narrow: { type: Boolean },
    route: { attribute: false },
    panel: { attribute: false },
    client: { attribute: false },
    admin: { type: Boolean },
  };

  declare hass: HassLike;
  declare narrow: boolean;
  declare route: RouteInfo;
  declare panel: PanelInfo;

  /**
   * Whether the viewer is an administrator.
   *
   * It mirrors the server's answer from `open_house/capabilities` and decides
   * only what is rendered. It is not a permission: every mutating command is
   * refused by the server for a non-admin whatever this says.
   */
  declare admin: boolean;

  /**
   * The websocket client. Assigned by the parent element rather than derived
   * from `hass`, so every screen in one panel shares one client and a screen
   * added later cannot accidentally build a second one against a stale `hass`.
   */
  declare client: OpenHouseClient | null;

  constructor() {
    super();
    this.narrow = false;
    this.admin = false;
    this.client = null;
    this.route = {};
    this.panel = {};
  }

  protected override createRenderRoot(): HTMLElement | DocumentFragment {
    return this;
  }

  /** The client, or an error when the element was mounted without one. */
  protected requireClient(): OpenHouseClient {
    if (!this.client) {
      throw new Error(
        "open-house panel element has no client; it must be rendered by " +
          "<open-house-panel>, which builds one from `hass`.",
      );
    }
    return this.client;
  }

  /** Whether the panel is connected to a Home Assistant it can command. */
  protected get connected(): boolean {
    return this.client !== null;
  }

  // -- render helpers ------------------------------------------------------

  protected loading(message = "Loading..."): TemplateResult {
    return html`<div class="empty" role="status">
      <span class="spinner"></span>
      <p class="muted">${message}</p>
    </div>`;
  }

  /**
   * What a person can *do* about a refusal, by the server's own code.
   *
   * A table rather than a chain of ternaries, and every code the server can
   * actually send appears in it: the banner used to know three (`unauthorized`,
   * `not_found`, `unavailable`) and print the other four as a bare `Code: ...`
   * line, which is a word and not an answer. `stale_page` was the worst of them
   * -- it is the one refusal whose fix is entirely local, and the screen that
   * met it said nothing at all about reloading. `home_assistant_error` and
   * `unknown_error` are Home Assistant's own, sent when a handler raises
   * something nobody expected, and there the log is the only useful pointer.
   */
  protected errorBanner(error: PanelError | null): TemplateResult {
    if (!error) return html``;
    // The code is a `string` because it comes off the wire, so the table is
    // indexed through the cast and the `?? ""` is the door for a code this panel
    // has never heard of -- a newer server, or a transport's own invention. A
    // missing suggestion is a shorter banner, never a broken one.
    const suggestion = SUGGESTIONS[error.code as BannerCode] ?? "";
    return html`<div class="banner error" role="alert">
      <strong>${error.message}</strong>
      ${suggestion ? html`<p class="help">${suggestion}</p>` : null}
      <p class="help">Code: ${error.code}</p>
    </div>`;
  }

  protected emptyState(
    title: string,
    detail: string,
    action?: TemplateResult,
  ): TemplateResult {
    return html`<div class="empty">
      <h3>${title}</h3>
      <p class="muted">${detail}</p>
      ${action ?? null}
    </div>`;
  }

  /** Turn any thrown value into a `PanelError` for `errorBanner`. */
  protected toError(error: unknown): PanelError {
    return asPanelError(error);
  }
}

/** Format an ISO-8601 timestamp for a reader, falling back to the raw string. */
export function formatTimestamp(value: string | null): string {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString();
}

/** Format an ISO-8601 timestamp as a coarse relative age. */
export function formatRelative(value: string | null): string {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  const seconds = Math.round((Date.now() - date.getTime()) / 1000);
  if (seconds < 60) return "just now";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} h ago`;
  const days = Math.round(hours / 24);
  if (days < 30) return `${days} d ago`;
  return date.toLocaleDateString();
}

/** A Home Assistant icon, or nothing when the frontend is not present. */
export function icon(name: string, label?: string): TemplateResult {
  return html`<ha-icon
    icon=${name}
    aria-label=${label ?? ""}
    role=${label ? "img" : "presentation"}
  ></ha-icon>`;
}
