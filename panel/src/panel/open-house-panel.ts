/**
 * The panel root: the sidebar entry, its nine tabs, and the admin boundary.
 *
 * Home Assistant's `panel_custom` creates this element with `embed_iframe:
 * false` and assigns `hass`, `narrow`, `route` and `panel` before the first
 * render. Everything the panel knows arrives through `hass`, and the client
 * built from it is handed down to every tab, so there is one websocket contract
 * and one place it is constructed.
 *
 * The admin boundary is enforced in three places and only one of them is here:
 * the server refuses a mutating command from a non-admin, the tabs hide the
 * controls a non-admin cannot use, and this element hides the two tabs that are
 * entirely administrative. Hiding is the weakest of the three by design -- it is
 * a courtesy, not a control.
 */

import { html, type TemplateResult } from "lit";
import { OpenHouseElement } from "../base.ts";
import { OpenHouseClient } from "../api/client.ts";
import { isConnected } from "../api/connection.ts";
import type { Capabilities } from "../api/models.ts";
import { sharedStyles } from "../styles.ts";
import { TABS, tabsFor, type TabDefinition, type TabId } from "../tabs/types.ts";

// Import for their side effect: each registers its own element.
import "../tabs/overview.ts";
import "../tabs/rooms.ts";
import "../tabs/house.ts";
import "../tabs/profiles.ts";
import "../tabs/store.ts";
import "../tabs/activity.ts";
import "../tabs/health.ts";
import "../tabs/dev.ts";
import "../tabs/room-settings.ts";
import "../tabs/add-module.ts";
import "../components/dialog.ts";
import "../components/schema-form.ts";

/**
 * The tab the panel was last on, carried in the URL so a reload lands back
 * there instead of on Overview.
 *
 * A reload of `…/open-house` used to open the overview every time, because the
 * showing tab was a plain field and nothing outside the element ever knew it.
 * The query string is the record: it is what a reload re-reads, it survives a
 * bookmark, and it can be pasted to somebody else. The room a tab asked Rooms
 * to open rides along for the same reason -- `?tab=rooms&room=kitchen` is a
 * link to the kitchen.
 *
 * `sessionStorage` repeats the tab for the one case the URL cannot cover: Home
 * Assistant's own router rewrites the address of an embedded panel when the
 * sidebar is used, and a query parameter it does not know is not guaranteed to
 * survive that. The URL is read first, and is what makes a link shareable.
 */
const TAB_PARAM = "open_house_tab";
const ROOM_PARAM = "open_house_room";

function recalled(key: string): string | null {
  try {
    return window.sessionStorage.getItem(key);
  } catch {
    // Storage can be denied outright (a hardened browser, a sandboxed frame);
    // the URL is then the only record, which is the one that matters anyway.
    return null;
  }
}

function remembered(key: string, value: string): void {
  try {
    window.sessionStorage.setItem(key, value);
  } catch {
    // As above: nothing here is worth failing a render over.
  }
}

/** The tab the address bar asks for, or the last one, or the first ever. */
function tabFromUrl(): TabId {
  const named = new URLSearchParams(window.location.search).get(TAB_PARAM);
  if (named && TABS.some((tab) => tab.id === named)) return named as TabId;
  const recalledTab = recalled(TAB_PARAM);
  if (recalledTab && TABS.some((tab) => tab.id === recalledTab)) {
    return recalledTab as TabId;
  }
  return "overview";
}

/** The room the address bar asked Rooms to open, if it asked for one. */
function roomFromUrl(): string {
  return (
    new URLSearchParams(window.location.search).get(ROOM_PARAM) ??
    recalled(ROOM_PARAM) ??
    ""
  );
}

export class OpenHousePanel extends OpenHouseElement {
  /**
   * Which tab is showing, and the room another tab asked Rooms to open.
   *
   * Both are Lit *state* rather than plain fields, and that is the fix for a
   * defect that made the panel unusable: as plain fields they were written by
   * `selectTab`/`onNavigate` without anything scheduling a re-render, so a click
   * on the tab bar changed a property nothing observed and the screen stayed on
   * Overview for ever. Declaring them reactive is what makes the assignment the
   * re-render, rather than relying on some later, unrelated `hass` update to
   * carry the change to the DOM.
   */
  static override properties = {
    ...OpenHouseElement.properties,
    activeTab: { state: true },
    navigateRoomId: { state: true },
  };

  private activeTab: TabId = tabFromUrl();
  private capabilities: Capabilities | null = null;
  private capabilityError: ReturnType<OpenHouseElement["toError"]> | null = null;
  private loadingCapabilities = false;
  /** A room another tab asked Rooms to open. */
  private navigateRoomId = roomFromUrl();
  /** The last `hass` the client was built from, to rebuild only on change. */
  private clientHass: unknown = null;
  /** Whether the automation editor's strings have been asked for yet. */
  private askedForConfigStrings = false;

  override updated(changed: Map<string, unknown>): void {
    // `hass` is reassigned by the frontend on every state change, so compare by
    // identity: rebuilding the client each time would be cheap but pointless,
    // and skipping the rebuild entirely would leave a client pointed at a stale
    // `hass` after a reconnect.
    if (changed.has("hass") && this.hass !== this.clientHass) {
      this.clientHass = this.hass;
      this.client = isConnected(this.hass)
        ? OpenHouseClient.fromHass(this.hass)
        : null;
      void this.loadCapabilities();
    }
    void this.loadConfigStrings();
  }

  /**
   * Ask the frontend for the strings Home Assistant's condition editor is
   * labelled with, and re-render once they are here.
   *
   * A cast may be Home Assistant's own condition builder, and that component
   * reads every one of its labels through `hass.localize` -- the "Add condition"
   * button, the condition-type menu, the fields inside each type. The strings
   * are not in the fragment a `panel_custom` panel is served: the frontend loads
   * one translation fragment per panel (`config` for the config panel, `custom`
   * for this one), and the automation editor's are in `config`. Unloaded, the
   * whole editor renders with empty labels -- a bare `+` and a menu of blank
   * rows, which reads as "there is no UI for this" rather than as a missing
   * translation.
   *
   * Asked for once, at the panel root, because both places a condition is built
   * (the import screen and a hosted module's card) are below it. The promise is
   * not awaited: nothing here depends on the strings, and a failure leaves the
   * panel exactly as it was -- a screen that still works, with an editor that
   * reads poorly.
   */
  private async loadConfigStrings(): Promise<void> {
    if (this.askedForConfigStrings) return;
    const ask = this.hass?.loadFragmentTranslation;
    if (typeof ask !== "function") return;
    this.askedForConfigStrings = true;
    try {
      await ask.call(this.hass, "config");
      this.requestUpdate();
    } catch {
      // A frontend that will not answer is not a panel that cannot run.
    }
  }

  private async loadCapabilities(): Promise<void> {
    if (!this.client) return;
    this.loadingCapabilities = true;
    this.requestUpdate();
    try {
      this.capabilities = await this.client.capabilities();
      this.admin = this.capabilities.admin;
      const allowed = tabsFor(this.admin);
      if (!allowed.some((tab) => tab.id === this.activeTab)) {
        this.activeTab = "overview";
        // A tab this person may not see must not stay in the address bar either,
        // or a reload would keep asking for a screen that is not there.
        this.rememberLocation();
      }
    } catch (error) {
      this.capabilityError = this.toError(error);
    } finally {
      this.loadingCapabilities = false;
      this.requestUpdate();
    }
  }

  /**
   * Write the showing tab -- and the room it is showing -- into the address bar.
   *
   * `replaceState` rather than `pushState`: a tab click is not a page in
   * somebody's history, and pushing one per click would make the browser's Back
   * button a tour of the tab bar instead of a way out of the panel. The state
   * Home Assistant's router put beside the URL is carried through untouched,
   * because that router reads it back on its way past.
   *
   * The overview is written as *no* parameter, so the address of the default
   * screen stays the panel's plain address rather than one carrying a
   * parameter that means what its absence already means.
   */
  private rememberLocation(): void {
    const params = new URLSearchParams(window.location.search);
    if (this.activeTab === "overview") params.delete(TAB_PARAM);
    else params.set(TAB_PARAM, this.activeTab);
    if (this.activeTab === "rooms" && this.navigateRoomId) {
      params.set(ROOM_PARAM, this.navigateRoomId);
    } else {
      params.delete(ROOM_PARAM);
    }
    remembered(TAB_PARAM, this.activeTab);
    if (this.navigateRoomId) remembered(ROOM_PARAM, this.navigateRoomId);
    const query = params.toString();
    const address = `${window.location.pathname}${query ? `?${query}` : ""}`;
    if (address !== `${window.location.pathname}${window.location.search}`) {
      history.replaceState(history.state, "", address);
    }
  }

  private selectTab(id: TabId): void {
    this.activeTab = id;
    if (id !== "rooms") this.navigateRoomId = "";
    this.rememberLocation();
  }

  private onNavigate(event: Event): void {
    const detail = (event as CustomEvent<{ tab?: TabId; roomId?: string }>).detail;
    if (!detail?.tab) return;
    this.activeTab = detail.tab;
    if (detail.roomId) this.navigateRoomId = detail.roomId;
    this.rememberLocation();
  }

  private onRepair(event: Event): void {
    const detail = (event as CustomEvent<{ flow_id?: string }>).detail;
    if (!detail?.flow_id) return;
    // Home Assistant's own Repairs dialog is the right place to run a repair
    // flow; the panel navigates to it rather than re-implementing it.
    history.pushState(null, "", `/config/repairs?flow=${detail.flow_id}`);
    window.dispatchEvent(new Event("location-changed"));
  }

  protected override render(): TemplateResult {
    return html`
      <style>
        ${sharedStyles}
      </style>
      <div class="layout" @navigate=${this.onNavigate} @repair=${this.onRepair}>
        ${this.renderHeader()}
        ${this.renderConnectionState()}
        <nav class="tabs" role="tablist">
          ${tabsFor(this.admin).map((tab) => this.renderTabButton(tab))}
        </nav>
        <div style="margin-top:16px">${this.renderActiveTab()}</div>
      </div>
    `;
  }

  private renderHeader(): TemplateResult {
    const language = this.hass?.language ?? "en";
    return html`<div class="row spread wrap" style="margin-bottom:8px">
      <div class="stack">
        <h1>Open House</h1>
        <p class="muted small">
          ${this.capabilities?.user_name
            ? this.admin
              ? `${this.capabilities.user_name} (administrator)`
              : `${this.capabilities.user_name} (limited view)`
            : ""}
          ${this.capabilities
            ? `· engine API ${this.capabilities.engine_api}`
            : ""}
        </p>
      </div>
      <span class="visually-hidden">Language ${language}</span>
    </div>`;
  }

  private renderConnectionState(): TemplateResult {
    if (this.capabilityError) return this.errorBanner(this.capabilityError);
    if (this.loadingCapabilities && !this.capabilities) {
      return this.loading("Connecting to Open House...");
    }
    if (!this.capabilities) {
      return html`<div class="banner warn">
        This panel is not connected to Home Assistant. Open it from the Open
        House sidebar entry.
      </div>`;
    }
    if (this.capabilities.needs_setup) {
      return html`<div class="banner info">
        Open House has not been set up yet.
        <a
          href="#"
          @click=${(event: Event) => {
            event.preventDefault();
            history.pushState(null, "", "/config/integrations/integration/open_house");
            window.dispatchEvent(new Event("location-changed"));
          }}
          >Finish setup</a
        >
        to choose your rooms and people.
      </div>`;
    }
    if (!this.admin) {
      return html`<div class="banner info">
        You are viewing Open House in the limited view. Changes are made by an
        administrator.
      </div>`;
    }
    return html``;
  }

  private renderTabButton(tab: TabDefinition): TemplateResult {
    const selected = tab.id === this.activeTab;
    return html`<button
      type="button"
      class="tab"
      role="tab"
      id="tab-${tab.id}"
      aria-selected=${selected ? "true" : "false"}
      aria-controls="panel-${tab.id}"
      @click=${() => this.selectTab(tab.id)}
    >
      <ha-icon icon=${tab.iconName}></ha-icon>
      ${tab.label}
    </button>`;
  }

  /**
   * The tab body, or nothing until there is a client to render it with.
   *
   * The client is assigned in `updated`, which runs *after* the first render,
   * so a tab element mounted on that first pass is constructed with `client:
   * null` -- and every screen loads its data in `connectedCallback`, which has
   * already run by the time the client arrives. The result was a panel whose
   * every tab showed "open-house panel element has no client" and never
   * retried. Withholding the element until the client exists means the first
   * one constructed is constructed with it.
   */
  private renderActiveTab(): TemplateResult {
    if (!this.client) return html``;
    const tab = TABS.find((entry) => entry.id === this.activeTab) ?? TABS[0]!;
    const roomId =
      tab.id === "rooms" && this.navigateRoomId ? this.navigateRoomId : "";
    return html`<section
      role="tabpanel"
      id="panel-${tab.id}"
      aria-labelledby="tab-${tab.id}"
    >
      ${this.renderTabElement(tab.tag, roomId)}
    </section>`;
  }

  /**
   * One case per tab element.
   *
   * Each element is named literally rather than built from the registry's `tag`
   * string, because a tag built at runtime would need Lit's `unsafeStatic` --
   * and handing Lit an interpolated tag name is exactly the habit that turns a
   * template into a string a pack could reach. The registry still decides
   * *which* element, so adding a tab stays one array entry plus one branch.
   */
  private renderTabElement(tag: string, roomId: string): TemplateResult {
    // `hass` rides with the client into every tab, not only the Dev one. A tab
    // reaches it through the client for its own data, but Home Assistant's own
    // components do not: a condition editor is drawn by `ha-automation-condition`
    // and reads `hass` to know which entities exist and what to call them, so a
    // card that hands the frontend's components no `hass` draws an editor with
    // nothing in it -- a label, an empty box, and no way to build anything.
    const common = {
      client: this.client,
      admin: this.admin,
      narrow: this.narrow,
      hass: this.hass,
    };
    switch (tag) {
      case "open-house-tab-rooms":
        return html`<open-house-tab-rooms
          .client=${common.client}
          .admin=${common.admin}
          .narrow=${common.narrow}
          .hass=${common.hass}
          .initialRoomId=${roomId}
        ></open-house-tab-rooms>`;
      case "open-house-tab-house":
        return html`<open-house-tab-house
          .client=${common.client}
          .admin=${common.admin}
          .hass=${common.hass}
        ></open-house-tab-house>`;
      case "open-house-tab-profiles":
        return html`<open-house-tab-profiles
          .client=${common.client}
          .admin=${common.admin}
        ></open-house-tab-profiles>`;
      case "open-house-tab-store":
        return html`<open-house-tab-store
          .client=${common.client}
          .admin=${common.admin}
        ></open-house-tab-store>`;
      case "open-house-tab-activity":
        return html`<open-house-tab-activity
          .client=${common.client}
          .admin=${common.admin}
        ></open-house-tab-activity>`;
      case "open-house-tab-health":
        return html`<open-house-tab-health
          .client=${common.client}
          .admin=${common.admin}
        ></open-house-tab-health>`;
      case "open-house-tab-dev":
        return html`<open-house-tab-dev
          .client=${common.client}
          .admin=${common.admin}
          .hass=${this.hass}
        ></open-house-tab-dev>`;
      default:
        return html`<open-house-tab-overview
          .client=${common.client}
          .admin=${common.admin}
          .narrow=${common.narrow}
        ></open-house-tab-overview>`;
    }
  }
}

if (!customElements.get("open-house-panel")) {
  customElements.define("open-house-panel", OpenHousePanel);
}
