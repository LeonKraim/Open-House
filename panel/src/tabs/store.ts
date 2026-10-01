/**
 * The Store tab: browse, install, update.
 *
 * Phase 5 only has to show the tab and talk to the index; the registry itself
 * is Phase 7, so this screen is written against the *answer* a registry would
 * give (`StoreEntry`) and a cached index it can render offline. Two of the
 * Phase 7 rules are already visible in what it refuses to hide:
 *
 *   * `update_requires_review` gates an update behind an explicit confirmation,
 *     because a version that widens a pack's permissions is a version the user
 *     has not consented to yet.
 *   * `available: false` marks a stale cached entry, so an offline install is a
 *     choice rather than a surprise.
 */

import { html, nothing, type TemplateResult } from "lit";
import { OpenHouseElement } from "../base.ts";
import type { StoreEntry } from "../api/models.ts";

const TIER_CHIP: Record<StoreEntry["tier"], string> = {
  official: "ok",
  verified: "ok",
  community: "",
  local: "warn",
};

export class StoreTab extends OpenHouseElement {
  private entries: StoreEntry[] = [];
  private generatedAt: string | null = null;
  private cached = false;
  private isLoading = true;
  private error: ReturnType<OpenHouseElement["toError"]> | null = null;
  private busy: string | null = null;
  private confirming: string | null = null;
  private tierFilter: StoreEntry["tier"] | "all" = "all";

  override connectedCallback(): void {
    super.connectedCallback();
    void this.load();
  }

  reload(): void {
    void this.load();
  }

  private async load(): Promise<void> {
    this.isLoading = true;
    this.error = null;
    this.requestUpdate();
    try {
      const response = await this.requireClient().storeIndex();
      this.entries = response.entries;
      this.generatedAt = response.generated_at;
      this.cached = response.cached;
    } catch (error) {
      this.error = this.toError(error);
    } finally {
      this.isLoading = false;
      this.requestUpdate();
    }
  }

  private async install(entry: StoreEntry): Promise<void> {
    this.busy = entry.pack;
    this.error = null;
    try {
      await this.requireClient().storeInstall(entry.pack, entry.tier);
      this.confirming = null;
      await this.load();
    } catch (error) {
      this.error = this.toError(error);
    } finally {
      this.busy = null;
      this.requestUpdate();
    }
  }

  protected override render(): TemplateResult {
    if (this.isLoading && this.entries.length === 0) {
      return this.loading("Reading the pack index...");
    }
    const visible = this.entries.filter(
      (entry) => this.tierFilter === "all" || entry.tier === this.tierFilter,
    );
    return html`
      ${this.errorBanner(this.error)}
      <div class="row spread wrap" style="margin-bottom:12px">
        <h1>Store</h1>
        <select
          aria-label="Filter by tier"
          @change=${(event: Event) => {
            this.tierFilter = (event.target as HTMLSelectElement)
              .value as StoreEntry["tier"] | "all";
          }}
        >
          ${(["all", "official", "verified", "community", "local"] as const).map(
            (tier) => html`<option value=${tier} ?selected=${tier === this.tierFilter}>
              ${tier}
            </option>`,
          )}
        </select>
      </div>
      ${this.cached
        ? html`<div class="banner warn">
            Showing a cached index${this.generatedAt
              ? html` from ${this.generatedAt}`
              : null}. It may be out of date.
          </div>`
        : null}
      ${visible.length === 0
        ? this.emptyState(
            "Nothing here",
            this.tierFilter === "all"
              ? "The pack index is empty. The community registry arrives in a later phase."
              : `No ${this.tierFilter} packs are listed.`,
          )
        : html`<div class="grid">
            ${visible.map((entry) => this.renderEntry(entry))}
          </div>`}
    `;
  }

  private renderEntry(entry: StoreEntry): TemplateResult {
    const review = this.confirming === entry.pack;
    return html`<div class="card">
      <div class="row spread wrap">
        <div class="grow">
          <h3>${entry.name || entry.pack}</h3>
          <p class="muted small">
            ${entry.author} &middot; v${entry.version} &middot; ${entry.license}
          </p>
        </div>
        <span class="chip ${TIER_CHIP[entry.tier]}">${entry.tier}</span>
      </div>
      <p>${entry.description}</p>
      <div class="row wrap">
        ${entry.abandoned
          ? html`<span class="chip warn">abandoned</span>`
          : nothing}
        ${entry.installed_version
          ? html`<span class="chip">installed v${entry.installed_version}</span>`
          : nothing}
        ${entry.update_available
          ? html`<span class="chip warn">update available</span>`
          : nothing}
        ${!entry.available ? html`<span class="chip warn">not in index</span>` : nothing}
      </div>
      ${entry.update_requires_review && !review
        ? html`<div class="banner warn">
            This update widens the pack's permissions. Review before installing.
          </div>`
        : null}
      <div class="row" style="margin-top:8px">
        ${review
          ? html`<button
              type="button"
              class="danger"
              ?disabled=${this.busy === entry.pack}
              @click=${() => void this.install(entry)}
            >
              ${this.busy === entry.pack ? "Installing..." : "Yes, install this update"}
            </button>
            <button type="button" @click=${() => (this.confirming = null)}>
              Cancel
            </button>`
          : html`<button
              type="button"
              class="primary"
              ?disabled=${!this.admin || !entry.available}
              title=${entry.available ? "" : "This pack is not in the index right now."}
              @click=${() => {
                if (entry.update_requires_review) this.confirming = entry.pack;
                else void this.install(entry);
              }}
            >
              ${entry.installed_version
                ? entry.update_available
                  ? "Update"
                  : "Reinstall"
                : "Install"}
            </button>`}
      </div>
      <p class="help">SHA-256 ${entry.sha256.slice(0, 16)}...</p>
    </div>`;
  }
}

if (!customElements.get("open-house-tab-store")) {
  customElements.define("open-house-tab-store", StoreTab);
}
