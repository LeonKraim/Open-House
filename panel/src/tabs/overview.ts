/**
 * The Overview tab: is anything wrong, and what is the house doing.
 *
 * It answers both questions from one command. Splitting "the house" and "the
 * alerts" into two calls would make the panel show a room list and a health
 * count that were taken a moment apart, and the one thing this screen is for is
 * a coherent single answer.
 */

import { html, type TemplateResult } from "lit";
import { OpenHouseElement, formatRelative } from "../base.ts";
import type { HouseOverview, RoomSummary } from "../api/models.ts";

export class OverviewTab extends OpenHouseElement {
  private overview: HouseOverview | null = null;
  private isLoading = true;
  private error: ReturnType<OpenHouseElement["toError"]> | null = null;

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
      this.overview = await this.requireClient().overview();
    } catch (error) {
      this.error = this.toError(error);
    } finally {
      this.isLoading = false;
      this.requestUpdate();
    }
  }

  protected override render(): TemplateResult {
    if (this.isLoading && !this.overview) {
      return this.loading("Reading the house...");
    }
    return html`
      ${this.errorBanner(this.error)}
      ${this.overview
        ? this.renderOverview(this.overview)
        : this.emptyState(
            "No overview",
            "The panel could not read the house state.",
          )}
    `;
  }

  private renderOverview(overview: HouseOverview): TemplateResult {
    return html`
      <div class="row spread wrap" style="margin-bottom:12px">
        <div class="stack">
          <h1>${overview.name || "Open House"}</h1>
          <p class="muted">
            Mode <span class="chip">${overview.mode}</span>
            &middot; ${overview.rooms.length} rooms
            &middot; ${overview.modules_installed} modules
          </p>
        </div>
        <div class="row">
          <button type="button" @click=${() => this.reload()}>Refresh</button>
        </div>
      </div>

      ${this.renderIssueSummary(overview)} ${this.renderPeople(overview.people)}
      <h2 style="margin:12px 0 8px">Rooms</h2>
      ${overview.rooms.length === 0
        ? this.emptyState(
            "No rooms yet",
            "Open House has no rooms. Add one from the Rooms tab, or finish setup.",
          )
        : html`<div class="grid">
            ${overview.rooms.map((room) => this.renderRoomCard(room))}
          </div>`}

      <p class="help">Updated ${formatRelative(overview.updated_at)}</p>
    `;
  }

  private renderIssueSummary(overview: HouseOverview): TemplateResult {
    const { error, warning, info } = overview.issues;
    const total = error + warning + info;
    if (total === 0) {
      return html`<div class="banner info">
        Everything looks healthy: no open issues.
      </div>`;
    }
    return html`<div class="banner ${error > 0 ? "error" : warning > 0 ? "warn" : "info"}">
      <div class="row wrap">
        ${error > 0 ? html`<span class="chip error">${error} errors</span>` : null}
        ${warning > 0 ? html`<span class="chip warn">${warning} warnings</span>` : null}
        ${info > 0 ? html`<span class="chip">${info} notes</span>` : null}
      </div>
      <p class="help">See the Health tab for the details.</p>
    </div>`;
  }

  private renderPeople(people: HouseOverview["people"]): TemplateResult {
    if (people.length === 0) return html``;
    return html`<div class="card">
      <h3>People</h3>
      <div class="row wrap">
        ${people.map(
          (person) => html`<span class="chip ${person.home ? "ok" : ""}">
            ${person.name}: ${person.home ? "home" : "away"}
          </span>`,
        )}
      </div>
    </div>`;
  }

  private renderRoomCard(room: RoomSummary): TemplateResult {
    const unfinished = room.required_unbound.length > 0;
    return html`<div class="card">
      <div class="row spread">
        <h3>${room.name}</h3>
        ${room.issue_count > 0
          ? html`<span class="chip ${unfinished ? "error" : "warn"}"
              >${room.issue_count} issues</span
            >`
          : html`<span class="chip ok">ok</span>`}
      </div>
      <p class="muted small">${room.type_label}</p>
      <div class="row wrap" style="margin-top:8px">
        <span class="chip">mode ${room.mode}</span>
        <span class="chip"
          >${room.bound_slots}/${room.total_slots} slots</span
        >
        ${room.occupied ? html`<span class="chip ok">occupied</span>` : null}
        ${Object.entries(room.active_profiles).map(
          ([axis, profile]) =>
            html`<span class="chip">${axis}: ${profile}</span>`,
        )}
      </div>
      ${unfinished
        ? html`<p class="help warn">
            Missing required slots: ${room.required_unbound.join(", ")}
          </p>`
        : null}
    </div>`;
  }
}

if (!customElements.get("open-house-tab-overview")) {
  customElements.define("open-house-tab-overview", OverviewTab);
}
