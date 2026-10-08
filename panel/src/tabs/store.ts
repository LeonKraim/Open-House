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
import type { ModuleOfferRow, StoreEntry } from "../api/models.ts";

const TIER_CHIP: Record<StoreEntry["tier"], string> = {
  official: "ok",
  verified: "ok",
  community: "",
  local: "warn",
};

/**
 * The tier as a menu choice, and as a chip: one value, one word.
 *
 * The wire's tier is a lower-case key (`official`, `local`); a person reads a
 * capitalised word in both the filter and the chip beside the pack, so the
 * thing they filtered for is the thing they then see. "All tiers" rather than
 * "all" for the same reason Activity's "Any outcome" is spelled out: a bare
 * value in a menu reads as a setting that failed to load.
 */
const TIERS: readonly { value: StoreEntry["tier"] | "all"; label: string }[] = [
  { value: "all", label: "All tiers" },
  { value: "official", label: "Official" },
  { value: "verified", label: "Verified" },
  { value: "community", label: "Community" },
  { value: "local", label: "Local" },
];

const TIER_LABELS = new Map(TIERS.map((tier) => [tier.value, tier.label]));

export class StoreTab extends OpenHouseElement {
  // `confirming` is a click that only arms a button, `tierFilter` and the
  // per-module pickers are choices; all are invisible to Lit as plain fields
  // (see base.ts).
  static override properties = {
    ...OpenHouseElement.properties,
    entries: { state: true },
    generatedAt: { state: true },
    cached: { state: true },
    isLoading: { state: true },
    error: { state: true },
    busy: { state: true },
    confirming: { state: true },
    tierFilter: { state: true },
    offers: { state: true },
    removing: { state: true },
    moduleReplace: { state: true },
    fileLabel: { state: true },
    notice: { state: true },
    moduleError: { state: true },
  };

  private entries: StoreEntry[] = [];
  private generatedAt: string | null = null;
  private cached = false;
  private isLoading = true;
  private error: ReturnType<OpenHouseElement["toError"]> | null = null;
  private busy: string | null = null;
  private confirming: string | null = null;
  private tierFilter: StoreEntry["tier"] | "all" = "all";

  /** The modules this house made: what an import saved rather than installed. */
  private offers: ModuleOfferRow[] = [];
  /** Its own error, so a house that cannot answer for its modules still lists packs. */
  private moduleError: ReturnType<OpenHouseElement["toError"]> | null = null;
  /** The module whose Remove button has been armed. */
  private removing: string | null = null;
  /** Whether an imported file may take the place of a module of its name. */
  private moduleReplace = false;
  private fileLabel = "";
  private notice: string | null = null;

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
      const index = await this.requireClient().storeIndex();
      this.entries = index.entries;
      this.generatedAt = index.generated_at;
      this.cached = index.cached;
    } catch (error) {
      this.error = this.toError(error);
    }
    try {
      // Asked separately, and its failure kept separate: the modules half is an
      // addition to a screen whose subject is the pack index, and a house that
      // cannot answer for its own modules still has an index worth showing.
      const store = await this.requireClient().modulesStore();
      this.offers = store.store;
    } catch (error) {
      this.moduleError = this.toError(error);
    } finally {
      this.isLoading = false;
      this.requestUpdate();
    }
  }

  /** Stop offering a definition. What is installed from it keeps running. */
  private async removeModule(offer: ModuleOfferRow): Promise<void> {
    this.busy = offer.slug;
    this.error = null;
    this.notice = null;
    try {
      const response = await this.requireClient().modulesRemove(offer.slug);
      this.removing = null;
      this.offers = response.store;
      this.notice =
        response.installed === 0
          ? `${offer.title} is no longer offered.`
          : `${offer.title} is no longer offered. ` +
            `The ${response.installed} ` +
            `${response.installed === 1 ? "room" : "rooms"} running it keep running.`;
    } catch (error) {
      this.error = this.toError(error);
    } finally {
      this.busy = null;
      this.requestUpdate();
    }
  }

  /** One definition as a file, for a person to give to somebody else. */
  private async download(offer: ModuleOfferRow): Promise<void> {
    this.busy = offer.slug;
    this.error = null;
    this.notice = null;
    try {
      const document = await this.requireClient().modulesExport(offer.slug);
      this.save(document, `${offer.slug}.json`);
      this.notice = `${offer.title} downloaded. Send the file to somebody else to install.`;
    } catch (error) {
      this.error = this.toError(error);
    } finally {
      this.busy = null;
      this.requestUpdate();
    }
  }

  private save(document: unknown, filename: string): void {
    const blob = new Blob([JSON.stringify(document, null, 2)], {
      type: "application/json",
    });
    const url = URL.createObjectURL(blob);
    const anchor = globalThis.document.createElement("a");
    anchor.href = url;
    anchor.download = filename;
    anchor.click();
    URL.revokeObjectURL(url);
  }

  /**
   * Read somebody else's module file into this house's store.
   *
   * Nothing is installed: reading a file is not consenting to run it, and the
   * file never touches a room. A module this house already offers is refused
   * unless the replace box is ticked, which is why that box is on the screen
   * rather than a default.
   */
  private async onFile(event: Event): Promise<void> {
    const input = event.target as HTMLInputElement;
    const file = input.files?.[0];
    if (!file) return;
    this.fileLabel = file.name;
    this.busy = "import";
    this.error = null;
    this.notice = null;
    this.requestUpdate();
    try {
      const document = JSON.parse(await file.text()) as unknown;
      const result = await this.requireClient().modulesImport(
        document,
        this.moduleReplace,
      );
      this.moduleReplace = false;
      this.offers = result.store;
      this.notice = result.replaced
        ? `Imported ${result.imported}, replacing the one this house had.`
        : `Imported ${result.imported}. Install it into any room below.`;
    } catch (error) {
      this.error = this.toError(error);
    } finally {
      this.busy = null;
      // **Cleared, so the same file can be chosen twice.** A `<input type=file>`
      // fires no `change` when it is handed the value it already holds, so
      // without this, re-importing a file somebody has just corrected does
      // nothing at all and looks like the read failed.
      input.value = "";
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
    if (this.isLoading && this.entries.length === 0 && this.offers.length === 0) {
      return this.loading("Reading the pack index...");
    }
    const visible = this.entries.filter(
      (entry) => this.tierFilter === "all" || entry.tier === this.tierFilter,
    );
    return html`
      ${this.errorBanner(this.error)}
      ${this.notice ? html`<div class="banner info">${this.notice}</div>` : null}
      <h1>Store</h1>
      ${this.renderModules()}
      ${this.cached
        ? html`<div class="banner warn">
            Showing a cached index${this.generatedAt
              ? html` from ${this.generatedAt}`
              : null}. It may be out of date.
          </div>`
        : null}
      <div class="row spread wrap" style="margin-bottom:12px">
        <span class="label" id="store-tier-label">Tier</span>
        <select
          aria-labelledby="store-tier-label"
          @change=${(event: Event) => {
            this.tierFilter = (event.target as HTMLSelectElement)
              .value as StoreEntry["tier"] | "all";
          }}
        >
          ${TIERS.map(
            (tier) => html`<option value=${tier.value} ?selected=${tier.value === this.tierFilter}>
              ${tier.label}
            </option>`,
          )}
        </select>
      </div>
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

  // -- the modules this house made ------------------------------------------

  /**
   * The store's local half: modules a person imported and saved.
   *
   * These are not packs -- a pack is a catalog's, and installing one is picking
   * it out of the index. A module is this house's own, and *installing* it means
   * nothing more than keeping it: it is a file in this house's store, offered
   * here as something you have. Putting it in a room is the other act, and it
   * belongs where the room is -- the room's own page, under *Add module to
   * room*. The two are different things and the screen keeps them apart: this
   * one says what you have and where each copy is running, and never moves one.
   */
  private renderModules(): TemplateResult {
    return html`<section class="card">
      <div class="row spread wrap">
        <div class="grow">
          <h2>Modules you made</h2>
          <p class="help">
            What the Dev tab saved. A module is yours to keep: add it to a room
            from that room's page, or to the whole house from the House page, as
            many times as you like -- each room gets its own copy and its own
            outputs. A module file is self-contained: somebody else needs
            neither the blueprint nor the network to install it.
          </p>
        </div>
      </div>
      ${this.moduleError
        ? html`<div class="banner warn">
            Your modules could not be read: ${this.moduleError.message} The pack
            index below is unaffected.
          </div>`
        : nothing}
      ${this.offers.length === 0
        ? html`<p class="muted">
            None yet. Import an automation or a blueprint in the Dev tab and save
            it as a module, or import a file somebody gave you below.
          </p>`
        : html`<div class="stack">
            ${this.offers.map((offer) => this.renderModule(offer))}
          </div>`}
      ${this.renderImport()}
    </section>`;
  }

  private renderModule(offer: ModuleOfferRow): TemplateResult {
    const busy = this.busy === offer.slug;
    const armed = this.removing === offer.slug;
    // Addressed by slug rather than by position: a store row is one module, and
    // a walk that clicked "the second Remove button" would be a walk that broke
    // the day a module was added above it.
    return html`<div class="nested" id="store-module-${offer.slug}">
      <div class="row spread wrap">
        <div class="grow">
          <h3>${offer.title}</h3>
          <p class="muted small">
            <code>${offer.slug}</code>
            ${offer.author ? html` &middot; ${offer.author}` : null}
            &middot; v${offer.version} &middot; ${offer.licence}
          </p>
        </div>
        <div class="row">
          <span class="chip ok">installed</span>
          ${offer.pinned
            ? html`<span
                class="chip warn"
                title="This module names devices from this house, so it would install somewhere else only if that house holds the same devices."
              >
                your devices
              </span>`
            : html`<span class="chip">any house</span>`}
        </div>
      </div>
      <p>${offer.description}</p>
      <p class="help">
        ${offer.blueprint ? html`From ${offer.blueprint}. ` : null}
        ${offer.slots.length === 0
          ? "Reaches through no slots."
          : html`Reaches through ${offer.slots.join(", ")}.`}
      </p>
      <p class="help">
        ${offer.deployed.length === 0
          ? html`In no room yet. Add it to one from that room's page --
              <em>Add module to room</em>.`
          : html`In ${offer.deployed.map(
              (where, index) => html`${index > 0 ? ", " : ""}${where.room_name}
                ${where.running
                  ? null
                  : html`<span class="chip warn">not running</span>`}`,
            )}.`}
      </p>
      <div class="row wrap" style="margin-top:8px">
        <button
          type="button"
          id="store-download-${offer.slug}"
          ?disabled=${busy}
          @click=${() => void this.download(offer)}
        >
          Download the file
        </button>
        ${armed
          ? html`<button
                type="button"
                class="danger"
                ?disabled=${busy}
                @click=${() => void this.removeModule(offer)}
              >
                Yes, stop offering it
              </button>
              <button type="button" @click=${() => (this.removing = null)}>
                Cancel
              </button>`
          : html`<button
              type="button"
              id="store-remove-${offer.slug}"
              @click=${() => (this.removing = offer.slug)}
            >
              Remove
            </button>`}
      </div>
      ${armed
        ? html`<p class="help">
            Removing this stops it being offered. Anything already installed from
            it keeps its own copy of the document and keeps running -- unbind the
            room it is in to stop that.
          </p>`
        : nothing}
    </div>`;
  }

  private renderImport(): TemplateResult {
    return html`<div class="nested">
      <h3>Install a module somebody gave you</h3>
      <p class="help">
        A <code>.json</code> file from this panel's Download. It carries the
        blueprint and the answers, so nothing else is needed. Importing only
        adds it here -- it is installed into a room afterwards, above.
      </p>
      <div class="field">
        <div class="label-row">
          <span class="label">Import a module file</span>
        </div>
        <input
          type="file"
          accept="application/json,.json"
          aria-label="Choose a module file"
          ?disabled=${!this.admin || this.busy === "import"}
          @change=${(event: Event) => void this.onFile(event)}
        />
      </div>
      <label class="toggle">
        <input
          type="checkbox"
          .checked=${this.moduleReplace}
          @change=${(event: Event) => {
            this.moduleReplace = (event.target as HTMLInputElement).checked;
          }}
        />
        <span>Replace a module this house already offers by that name</span>
      </label>
      ${this.fileLabel
        ? html`<p class="muted small">
            ${this.busy === "import" ? "Reading" : "Last file:"} ${this.fileLabel}
          </p>`
        : nothing}
    </div>`;
  }

  private renderEntry(entry: StoreEntry): TemplateResult {
    const review = this.confirming === entry.pack;
    return html`<div class="card">
      <div class="row spread wrap">
        <div class="grow">
          <h3>${entry.name || entry.pack}</h3>
          <p
            class="muted small"
            title=${entry.sha256 ? `SHA-256 ${entry.sha256}` : ""}
          >
            ${entry.author} &middot; v${entry.version} &middot; ${entry.license}
          </p>
        </div>
        <span class="chip ${TIER_CHIP[entry.tier]}"
          >${TIER_LABELS.get(entry.tier) ?? entry.tier}</span
        >
      </div>
      <p>${entry.description}</p>
      <div class="row wrap">
        ${entry.abandoned
          ? html`<span class="chip warn">Abandoned</span>`
          : nothing}
        ${entry.installed_version
          ? html`<span class="chip">Installed v${entry.installed_version}</span>`
          : nothing}
        ${entry.update_available
          ? html`<span class="chip warn">Update available</span>`
          : nothing}
        ${!entry.available ? html`<span class="chip warn">Not in index</span>` : nothing}
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
    </div>`;
  }
}

if (!customElements.get("open-house-tab-store")) {
  customElements.define("open-house-tab-store", StoreTab);
}
