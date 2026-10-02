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
 */

import { html, nothing, type TemplateResult } from "lit";
import { OpenHouseElement } from "../base.ts";
import type { HealthIssue } from "../api/models.ts";

const SEVERITY_ORDER: HealthIssue["severity"][] = ["error", "warning", "info"];

const SEVERITY_CHIP: Record<HealthIssue["severity"], string> = {
  error: "error",
  warning: "warn",
  info: "",
};

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
      ${this.errorBanner(this.error)}
      <div class="row spread wrap" style="margin-bottom:12px">
        <h1>Health</h1>
        <button type="button" @click=${() => void this.load()}>Re-check</button>
      </div>
      ${sorted.length === 0
        ? this.emptyState(
            "All healthy",
            "No problems found. Open House is running as configured.",
          )
        : html`<div class="stack">
            ${sorted.map((issue) => this.renderIssue(issue))}
          </div>`}
    `;
  }

  private renderIssue(issue: HealthIssue): TemplateResult {
    return html`<div class="card" data-code=${issue.code}>
      <div class="row spread wrap">
        <div class="row">
          <span class="chip ${SEVERITY_CHIP[issue.severity]}">${issue.severity}</span>
          <h3>${issue.title}</h3>
        </div>
        <div class="row">
          ${issue.room_id
            ? html`<button
                type="button"
                class="icon"
                @click=${() =>
                  this.dispatchEvent(
                    new CustomEvent("navigate", {
                      detail: { tab: "rooms", roomId: issue.room_id },
                      bubbles: true,
                      composed: true,
                    }),
                  )}
              >
                Open room
              </button>`
            : nothing}
          ${issue.repairs_flow_id
            ? html`<button
                type="button"
                class="icon"
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
      ${issue.entity_id
        ? html`<p class="help">${issue.entity_id} &middot; ${issue.code}</p>`
        : html`<p class="help">${issue.code}</p>`}
    </div>`;
  }
}

if (!customElements.get("open-house-tab-health")) {
  customElements.define("open-house-tab-health", HealthTab);
}
