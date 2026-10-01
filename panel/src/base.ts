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
 */

import { LitElement, html, type TemplateResult } from "lit";
import { OpenHouseClient } from "./api/client.ts";
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

  protected errorBanner(error: PanelError | null): TemplateResult {
    if (!error) return html``;
    const suggestion =
      error.code === "unauthorized"
        ? "This action needs an administrator."
        : error.code === "not_found"
          ? "That item no longer exists; reload the panel."
          : error.code === "unavailable"
            ? "Home Assistant is not reachable right now."
            : "";
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
