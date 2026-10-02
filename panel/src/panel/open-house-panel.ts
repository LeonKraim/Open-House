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
import "../tabs/modules.ts";
import "../tabs/profiles.ts";
import "../tabs/store.ts";
import "../tabs/activity.ts";
import "../tabs/health.ts";
import "../tabs/import-export.ts";
import "../tabs/room-settings.ts";
import "../tabs/add-module.ts";
import "../components/dialog.ts";
import "../components/schema-form.ts";

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

  private activeTab: TabId = "overview";
  private capabilities: Capabilities | null = null;
  private capabilityError: ReturnType<OpenHouseElement["toError"]> | null = null;
  private loadingCapabilities = false;
  /** A room another tab asked Rooms to open. */
  private navigateRoomId = "";
  /** The last `hass` the client was built from, to rebuild only on change. */
  private clientHass: unknown = null;

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
      }
    } catch (error) {
      this.capabilityError = this.toError(error);
    } finally {
      this.loadingCapabilities = false;
      this.requestUpdate();
    }
  }

  private selectTab(id: TabId): void {
    this.activeTab = id;
    if (id !== "rooms") this.navigateRoomId = "";
  }

  private onNavigate(event: Event): void {
    const detail = (event as CustomEvent<{ tab?: TabId; roomId?: string }>).detail;
    if (!detail?.tab) return;
    this.activeTab = detail.tab;
    if (detail.roomId) this.navigateRoomId = detail.roomId;
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
    const common = { client: this.client, admin: this.admin, narrow: this.narrow };
    switch (tag) {
      case "open-house-tab-rooms":
        return html`<open-house-tab-rooms
          .client=${common.client}
          .admin=${common.admin}
          .narrow=${common.narrow}
          .initialRoomId=${roomId}
        ></open-house-tab-rooms>`;
      case "open-house-tab-house":
        return html`<open-house-tab-house
          .client=${common.client}
          .admin=${common.admin}
        ></open-house-tab-house>`;
      case "open-house-tab-modules":
        return html`<open-house-tab-modules
          .client=${common.client}
          .admin=${common.admin}
        ></open-house-tab-modules>`;
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
      case "open-house-tab-import-export":
        return html`<open-house-tab-import-export
          .client=${common.client}
          .admin=${common.admin}
        ></open-house-tab-import-export>`;
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
