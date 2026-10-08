/**
 * The Health tab: what is degraded, and where to fix it.
 *
 * It is separate from Activity on purpose. Activity is a stream of what the
 * engine *did*; Health is a list of what is *wrong right now* -- a dead sensor,
 * a room with a required slot unbound, a pack whose engine range no longer
 * matches. A stream cannot answer "is anything wrong", because a fault that
 * happens once and persists appears in it once and then scrolls away.
 *
 * Each issue links to the room it concerns, so the fix is one click from the
 * diagnosis rather than a hunt through Rooms.
 *
 * ## The card reads as a sentence, and the ids fold away
 *
 * Every issue carries an entity id and a code -- `occupancy_sensor_unavailable`,
 * `binary_sensor.open_house_mock_fleet_demo_kitchen_motion` -- and those are the
 * engine's names for the fault, not a person's. Drawn as a bare line under every
 * card they were the loudest thing on the screen and the least useful: the same
 * sensor id, twenty times. They are kept, because a person filing a bug or
 * grepping logs needs them, but they are folded into a "Technical details"
 * expander so the card reads first as the sentence it is. The server's severity
 * is called what the rest of the panel calls it -- `info` is a "Note", the word
 * Overview's status pill already uses for that count.
 *
 * The list is a summary, so it leads with a count: a chip per severity, so the
 * answer to "how bad is it" is one line rather than a scroll.
 */

import { html, nothing, type TemplateResult } from "lit";
import { OpenHouseElement } from "../base.ts";
import type { HealthIssue } from "../api/models.ts";

const SEVERITY_ORDER: HealthIssue["severity"][] = ["error", "warning", "info"];

const SEVERITY_CHIP: Record<HealthIssue["severity"], string> = {
  error: "error",
  warning: "warn",
  // `info` is neither a fault nor a success -- a note about the house -- so it
  // carries the neutral chip class rather than the empty string, which drew as a
  // plain grey pill indistinguishable from a severity the map had forgotten.
  info: "neutral",
};

/**
 * The severity in the panel's own words.
 *
 * `info` reads "Note" because that is what Overview's status pill calls the same
 * count; a screen that called it "info" and a screen that called it a note would
 * be two names for one thing, and the person reading both is the person who has
 * to notice they are the same.
 */
const SEVERITY_LABEL: Record<HealthIssue["severity"], string> = {
  error: "Error",
  warning: "Warning",
  info: "Note",
};

/**
 * How many issues of each severity, for the one-line summary.
 *
 * Counted here rather than trusted from a call: Health's own read is the list it
 * is drawn from, so the counts and the rows cannot disagree.
 */
export function severityCounts(
  issues: readonly HealthIssue[],
): Record<HealthIssue["severity"], number> {
  return {
    error: issues.filter((issue) => issue.severity === "error").length,
    warning: issues.filter((issue) => issue.severity === "warning").length,
    info: issues.filter((issue) => issue.severity === "info").length,
  };
}

export class HealthTab extends OpenHouseElement {
  static override properties = {
    ...OpenHouseElement.properties,
    issues: { state: true },
    isLoading: { state: true },
    error: { state: true },
  };

  private issues: HealthIssue[] = [];
  private isLoading = true;
  private error: ReturnType<OpenHouseElement["toError"]> | null = null;

  override connectedCallback(): void {
    super.connectedCallback();
    void this.load();
  }

  reload(): void {
    void this.load();
  }

  /**
   * Ask again, but not while an answer is already on its way.
   *
   * `load` sets `isLoading` before its first `await`, so a second press lands
   * while the first is in flight and is dropped here -- otherwise two reads race
   * and the slower one's answer is the one that stays on screen.
   */
  private recheck(): void {
    if (this.isLoading) return;
    void this.load();
  }

  private async load(): Promise<void> {
    this.isLoading = true;
    this.error = null;
    this.requestUpdate();
    try {
      this.issues = await this.requireClient().health();
    } catch (error) {
      this.error = this.toError(error);
    } finally {
      this.isLoading = false;
      this.requestUpdate();
    }
  }

  protected override render(): TemplateResult {
    if (this.isLoading && this.issues.length === 0) {
      return this.loading("Checking the house...");
    }
    const sorted = [...this.issues].sort(
      (a, b) =>
        SEVERITY_ORDER.indexOf(a.severity) - SEVERITY_ORDER.indexOf(b.severity),
    );
    return html`
      ${this.errorBanner(this.error)} ${this.renderHead(sorted)}
      ${sorted.length === 0
        ? this.error
          ? nothing
          : this.emptyState(
              "All healthy",
              "No problems found. Open House is running as configured.",
            )
        : html`<div class="stack">
            ${sorted.map((issue) => this.renderIssue(issue))}
          </div>`}
    `;
  }

  /**
   * The title, and the one line that says how bad it is.
   *
   * The summary chips are the same colouring the issues below carry, so the
   * header answers "how much, and how severe" before the first card; when the
   * house is healthy the chips are absent and the empty state speaks instead.
   */
  private renderHead(sorted: HealthIssue[]): TemplateResult {
    const counts = severityCounts(sorted);
    return html`<div class="tab-head">
      <div class="stack">
        <h1>Health</h1>
        ${sorted.length === 0
          ? html`<p class="muted small">
              What is wrong right now, and where to fix it.
            </p>`
          : html`<div class="row wrap muted small">
              ${this.renderCount(counts.error, "error")}
              ${this.renderCount(counts.warning, "warning")}
              ${this.renderCount(counts.info, "info")}
            </div>`}
      </div>
      <button
        type="button"
        class="primary"
        ?disabled=${this.isLoading}
        @click=${() => this.recheck()}
      >
        ${this.isLoading ? "Checking..." : "Re-check"}
      </button>
    </div>`;
  }

  /** One severity's count, or nothing when there are none of it. */
  private renderCount(
    count: number,
    severity: HealthIssue["severity"],
  ): TemplateResult | typeof nothing {
    if (count === 0) return nothing;
    return html`<span class="chip ${SEVERITY_CHIP[severity]}"
      >${count} ${SEVERITY_LABEL[severity].toLowerCase()}${count === 1
        ? ""
        : "s"}</span
    >`;
  }

  /**
   * Open the room an issue names, but only if the house still has it.
   *
   * The issue was read into `this.issues` when this tab loaded, and a room can
   * be deleted from another tab while Health sits open. Handing that id on
   * would send the panel to a room that is gone. The report is re-read first,
   * and the navigation names a room the fresh report still has; a deleted room
   * takes its issue with it, so the press does nothing rather than walk into a
   * dead page.
   */
  private async openRoom(issue: HealthIssue): Promise<void> {
    if (this.isLoading) return;
    await this.load();
    const roomId = this.issues.find(
      (row) => row.room_id !== null && row.room_id === issue.room_id,
    )?.room_id;
    if (!roomId) return;
    this.dispatchEvent(
      new CustomEvent("navigate", {
        detail: { tab: "rooms", roomId },
        bubbles: true,
        composed: true,
      }),
    );
  }

  private renderIssue(issue: HealthIssue): TemplateResult {
    return html`<div class="card" data-code=${issue.code}>
      <div class="row spread wrap">
        <div class="row wrap">
          <span class="chip ${SEVERITY_CHIP[issue.severity]}"
            >${SEVERITY_LABEL[issue.severity]}</span
          >
          <h2 class="issue-title">${issue.title}</h2>
        </div>
        <div class="row">
          ${issue.room_id
            ? html`<button
                type="button"
                class="icon"
                @click=${() => void this.openRoom(issue)}
              >
                Open room
              </button>`
            : nothing}
          ${issue.repairs_flow_id
            ? html`<button
                type="button"
                class="icon primary"
                @click=${() =>
                  this.dispatchEvent(
                    new CustomEvent("repair", {
                      detail: { flow_id: issue.repairs_flow_id },
                      bubbles: true,
                      composed: true,
                    }),
                  )}
              >
                Fix
              </button>`
            : nothing}
        </div>
      </div>
      <p>${issue.detail}</p>
      <details class="tech">
        <summary>Technical details</summary>
        <p class="help">
          ${issue.entity_id
            ? html`${issue.entity_id} &middot; ${issue.code}`
            : issue.code}
        </p>
      </details>
    </div>`;
  }
}

if (!customElements.get("open-house-tab-health")) {
  customElements.define("open-house-tab-health", HealthTab);
}
