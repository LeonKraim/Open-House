/**
 * The Activity tab: the decision log, as "why did this happen".
 *
 * The engine's decision log doubles as the test oracle (spec.txt, Phase 1), and
 * this screen is its user-facing face. That shapes the design: the log's reason
 * is the product, not debug output, so every row can be expanded to read it in
 * full rather than being truncated into a table cell. Outcome is shown, because
 * "the motion light did not come on" is answered by finding the entry whose
 * outcome is `blocked` and reading why.
 *
 * The live stream is optional. It subscribes on request rather than on mount,
 * because a panel left open on a wall tablet should not hold a second
 * subscription open forever for a screen nobody is watching.
 */

import { html, nothing, type TemplateResult } from "lit";
import { OpenHouseElement, formatTimestamp, formatRelative } from "../base.ts";
import type {
  ActivityStreamEvent,
  DecisionLogEntry,
} from "../api/models.ts";

const OUTCOME_CHIP: Record<DecisionLogEntry["outcome"], string> = {
  applied: "ok",
  skipped: "",
  overridden: "warn",
  blocked: "error",
  error: "error",
};

export class ActivityTab extends OpenHouseElement {
  private entries: DecisionLogEntry[] = [];
  private isLoading = true;
  private error: ReturnType<OpenHouseElement["toError"]> | null = null;
  private live = false;
  private unsubscribe: (() => void) | null = null;
  private roomFilter = "";
  private outcomeFilter: DecisionLogEntry["outcome"] | "all" = "all";
  private expanded = new Set<string>();

  override connectedCallback(): void {
    super.connectedCallback();
    void this.load();
  }

  override disconnectedCallback(): void {
    this.stopLive();
    super.disconnectedCallback();
  }

  reload(): void {
    void this.load();
  }

  private async load(): Promise<void> {
    this.isLoading = true;
    this.error = null;
    this.requestUpdate();
    try {
      this.entries = await this.requireClient().activity(200);
    } catch (error) {
      this.error = this.toError(error);
    } finally {
      this.isLoading = false;
      this.requestUpdate();
    }
  }

  private async toggleLive(): Promise<void> {
    if (this.live) {
      this.stopLive();
      return;
    }
    this.error = null;
    try {
      const unsubscribe = await this.requireClient().subscribeActivity(
        (event: ActivityStreamEvent) => this.onStreamEvent(event),
      );
      this.unsubscribe = () => void unsubscribe();
      this.live = true;
    } catch (error) {
      this.error = this.toError(error);
    } finally {
      this.requestUpdate();
    }
  }

  private stopLive(): void {
    this.unsubscribe?.();
    this.unsubscribe = null;
    this.live = false;
    this.requestUpdate();
  }

  private onStreamEvent(event: ActivityStreamEvent): void {
    if (event.kind === "reset") {
      this.entries = [];
    } else if (event.entry) {
      this.entries = [event.entry, ...this.entries].slice(0, 500);
    }
    this.requestUpdate();
  }

  private get rooms(): string[] {
    const names = new Set<string>();
    for (const entry of this.entries) {
      if (entry.room) names.add(entry.room);
    }
    return [...names].sort();
  }

  protected override render(): TemplateResult {
    if (this.isLoading && this.entries.length === 0) {
      return this.loading("Reading the decision log...");
    }
    const visible = this.entries.filter(
      (entry) =>
        (this.roomFilter === "" || entry.room === this.roomFilter) &&
        (this.outcomeFilter === "all" || entry.outcome === this.outcomeFilter),
    );
    return html`
      ${this.errorBanner(this.error)}
      <div class="row spread wrap" style="margin-bottom:12px">
        <h1>Activity</h1>
        <div class="row">
          <button type="button" @click=${() => void this.toggleLive()}>
            ${this.live ? "Stop live updates" : "Follow live"}
          </button>
          <button type="button" @click=${() => void this.load()}>Refresh</button>
        </div>
      </div>
      <div class="row wrap" style="margin-bottom:12px">
        <select
          aria-label="Filter by room"
          @change=${(event: Event) => {
            this.roomFilter = (event.target as HTMLSelectElement).value;
          }}
        >
          <option value="">All rooms</option>
          ${this.rooms.map(
            (room) => html`<option value=${room} ?selected=${room === this.roomFilter}>
              ${room}
            </option>`,
          )}
        </select>
        <select
          aria-label="Filter by outcome"
          @change=${(event: Event) => {
            this.outcomeFilter = (event.target as HTMLSelectElement)
              .value as DecisionLogEntry["outcome"] | "all";
          }}
        >
          ${(["all", "applied", "skipped", "overridden", "blocked", "error"] as const).map(
            (outcome) => html`<option
              value=${outcome}
              ?selected=${outcome === this.outcomeFilter}
            >
              ${outcome}
            </option>`,
          )}
        </select>
        ${this.live ? html`<span class="chip ok">live</span>` : nothing}
      </div>
      ${visible.length === 0
        ? this.emptyState(
            "Nothing logged",
            "No decision has been recorded for this filter yet.",
          )
        : html`<table>
            <thead>
              <tr>
                <th>When</th>
                <th>Room</th>
                <th>Behaviour</th>
                <th>Action</th>
                <th>Outcome</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              ${visible.map((entry) => this.renderEntry(entry))}
            </tbody>
          </table>`}
    `;
  }

  private renderEntry(entry: DecisionLogEntry): TemplateResult {
    const open = this.expanded.has(entry.id);
    return html`<tr>
      <td title=${formatTimestamp(entry.at)}>${formatRelative(entry.at)}</td>
      <td>${entry.room ?? html`<span class="muted">house</span>`}</td>
      <td>${entry.behaviour ?? "-"}</td>
      <td>${entry.action}</td>
      <td>
        <span class="chip ${OUTCOME_CHIP[entry.outcome]}">${entry.outcome}</span>
      </td>
      <td>
        <button
          type="button"
          class="icon"
          aria-expanded=${open ? "true" : "false"}
          @click=${() => {
            const next = new Set(this.expanded);
            if (open) next.delete(entry.id);
            else next.add(entry.id);
            this.expanded = next;
          }}
        >
          Why
        </button>
      </td>
    </tr>
    ${open
      ? html`<tr>
          <td colspan="6">
            <div class="banner info" style="margin:0">
              <p>${entry.reason}</p>
              <p class="help">
                ${entry.entity_id ?? "no entity"}
                ${entry.priority === null
                  ? nothing
                  : html` &middot; priority ${entry.priority}`}
                &middot; ${formatTimestamp(entry.at)}
              </p>
            </div>
          </td>
        </tr>`
      : nothing}`;
  }
}

if (!customElements.get("open-house-tab-activity")) {
  customElements.define("open-house-tab-activity", ActivityTab);
}
