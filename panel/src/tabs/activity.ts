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
 * ## What a person reads, and what stays for the machine
 *
 * The log's own vocabulary is not the panel's. A behaviour arrives as an id --
 * `system.update_available`, `safety_alert` -- and an id is a key, not a
 * sentence; it is turned into words by `behaviourLabel` and the exact id is kept
 * one hover away in a `title`. The outcome words (`applied`, `skipped`,
 * `overridden`) are left exactly as the wire spells them, because those *are*
 * the words a person is looking for ("find the row that says blocked") and the
 * end-to-end walk matches on them. The filters carry a visible label apiece, so
 * "All rooms" and "all" are not two bare dropdowns a person has to guess at: the
 * outcome menu reads "Any outcome" and every option a word.
 *
 * ## An empty log and a filter that matched nothing are different screens
 *
 * They both used to draw "Nothing logged", which is wrong for the second: the
 * log is full and the filter is the reason nothing is on screen. So the two say
 * different things, and the filter's version carries the way out of it -- a
 * button that clears the filters -- rather than leaving a person to work out
 * which of two dropdowns hid their entry.
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
  // `skipped` is the one outcome that is neither good nor bad -- the engine
  // looked and chose to do nothing -- so it carries the neutral chip class
  // rather than the empty string, which drew as a coloured chip's plainer
  // neighbour with nothing saying that was deliberate.
  skipped: "neutral",
  overridden: "warn",
  blocked: "error",
  error: "error",
};

/**
 * The outcome menu's options: the wire's value, and the word a person reads.
 *
 * The values are the engine's (`DecisionLogEntry["outcome"]`); the labels are
 * the same words capitalised and, for the "no filter" entry, said out loud.
 * "all" as a menu option reads as a value that failed to load; "Any outcome"
 * reads as the choice it is.
 */
const OUTCOME_FILTERS: readonly {
  value: DecisionLogEntry["outcome"] | "all";
  label: string;
}[] = [
  { value: "all", label: "Any outcome" },
  { value: "applied", label: "Applied" },
  { value: "skipped", label: "Skipped" },
  { value: "overridden", label: "Overridden" },
  { value: "blocked", label: "Blocked" },
  { value: "error", label: "Error" },
];

/**
 * The same words, for the chip in the table.
 *
 * The filter and the row it found are one outcome seen twice, so they read the
 * same way: a person who filtered for "Skipped" and then meets a chip saying
 * `skipped` has been shown the wire's word where they were promised a sentence.
 */
const OUTCOME_LABELS = new Map(
  OUTCOME_FILTERS.map((choice) => [choice.value, choice.label]),
);

/**
 * A behaviour's id, made readable: `system.update_available` reads
 * "System: update available".
 *
 * The id is a key -- snake_case, dot-separated -- and a column of keys is the
 * one thing on this screen that is written for the engine rather than for the
 * person asking why the light stayed off. The transform is honest and knows no
 * vocabulary: the dots become a colon, the underscores become spaces, and the
 * first letter is capitalised, so a behaviour this panel has never seen reads as
 * well as one the packs ship. Nothing is invented and nothing is translated
 * away; the exact id is on the cell's `title` for a person who wants to grep
 * for it.
 */
export function behaviourLabel(id: string): string {
  const words = (part: string) => part.replace(/_/g, " ").trim();
  const label = id.split(".").map(words).filter(Boolean).join(": ");
  return label.charAt(0).toUpperCase() + label.slice(1);
}

/**
 * The entries a room and an outcome leave showing.
 *
 * Pulled out of `render` so it can be read and tested on its own: it is the one
 * piece of reasoning the screen does about the server's answer, and the two
 * filters are exactly where a wrong `===` against `""` would show every entry
 * or none.
 */
export function visibleEntries(
  entries: readonly DecisionLogEntry[],
  room: string,
  outcome: DecisionLogEntry["outcome"] | "all",
): DecisionLogEntry[] {
  return entries.filter(
    (entry) =>
      (room === "" || entry.room === room) &&
      (outcome === "all" || entry.outcome === outcome),
  );
}

export class ActivityTab extends OpenHouseElement {
  // The filters and the live toggle are clicks (see base.ts).
  static override properties = {
    ...OpenHouseElement.properties,
    entries: { state: true },
    isLoading: { state: true },
    error: { state: true },
    live: { state: true },
    liveStarting: { state: true },
    roomFilter: { state: true },
    outcomeFilter: { state: true },
    expanded: { state: true },
  };

  private entries: DecisionLogEntry[] = [];
  private isLoading = true;
  private error: ReturnType<OpenHouseElement["toError"]> | null = null;
  private live = false;
  /**
   * Whether a subscription is being opened right now.
   *
   * Its own flag rather than a use of `live`, because `live` is only set *after*
   * the await -- so a double-click on "Follow live" passed the `if (this.live)`
   * test twice, opened two subscriptions, and the second overwrote the first's
   * unsubscribe, leaving the first streaming for the life of the page: entries
   * appended twice, and "Stop live updates" stopping only one of them.
   */
  private liveStarting = false;
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
      // No window named here on purpose: the client's default is the one sized
      // against how much the engine writes per tick (see `activity` there), and
      // a second number here would be a second answer to the same question.
      this.entries = await this.requireClient().activity();
    } catch (error) {
      this.error = this.toError(error);
    } finally {
      this.isLoading = false;
      this.requestUpdate();
    }
  }

  private async toggleLive(): Promise<void> {
    // One subscription at a time. The guard is the whole of the "follow live"
    // button's correctness: without it a double-click opens two.
    if (this.liveStarting) return;
    if (this.live) {
      this.stopLive();
      return;
    }
    this.liveStarting = true;
    this.error = null;
    let opened: (() => void) | null = null;
    try {
      const unsubscribe = await this.requireClient().subscribeActivity(
        (event: ActivityStreamEvent) => this.onStreamEvent(event),
      );
      opened = () => void unsubscribe();
    } catch (error) {
      this.error = this.toError(error);
    } finally {
      this.liveStarting = false;
    }
    // The subscription may have been opened for an element that has since gone
    // -- a tab switch during the await, whose `disconnectedCallback` ran
    // `stopLive` before we got here. Taking it on as ours would leave it
    // streaming into nothing, so it is dropped instead.
    if (opened !== null && this.isConnected) {
      this.unsubscribe = opened;
      this.live = true;
    } else {
      opened?.();
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

  /** Forget both filters, so a hidden entry comes back. */
  private clearFilters(): void {
    this.roomFilter = "";
    this.outcomeFilter = "all";
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
    const visible = visibleEntries(
      this.entries,
      this.roomFilter,
      this.outcomeFilter,
    );
    return html`
      ${this.errorBanner(this.error)} ${this.renderHead()} ${this.renderFilters()}
      ${this.renderBody(visible)}
    `;
  }

  /**
   * The title row: what this screen is, its live switch and its Refresh.
   *
   * "Follow live" is a status whose label says which way the switch points
   * ("Follow live" to start, "Stop live updates" to stop) rather than a checkbox
   * that leaves a person to work out the current state from a tick.
   */
  private renderHead(): TemplateResult {
    return html`<div class="tab-head">
      <div class="stack">
        <h1>Activity</h1>
        <p class="muted small">
          Why the house did what it did, newest first.
        </p>
      </div>
      <div class="row wrap">
        <button
          type="button"
          ?disabled=${this.liveStarting}
          @click=${() => void this.toggleLive()}
        >
          ${this.live
            ? "Stop live updates"
            : this.liveStarting
              ? "Following..."
              : "Follow live"}
        </button>
        <button type="button" class="primary" @click=${() => void this.load()}>
          Refresh
        </button>
      </div>
    </div>`;
  }

  /**
   * The two filters, each with the word for what it narrows.
   *
   * `aria-labelledby` rather than `aria-label`, so there is one name and it is
   * the one on screen: a visible label and a hidden one would be two strings to
   * keep in step, and the visible one is what a person checks their reading
   * against. The live chip rides here because it is the other thing about what
   * is being shown.
   */
  private renderFilters(): TemplateResult {
    return html`<div class="filters">
      <div class="filter">
        <span class="label" id="activity-room-label">Room</span>
        <select
          aria-labelledby="activity-room-label"
          @change=${(event: Event) => {
            this.roomFilter = (event.target as HTMLSelectElement).value;
          }}
        >
          <option value="" ?selected=${this.roomFilter === ""}>All rooms</option>
          ${this.rooms.map(
            (room) => html`<option value=${room} ?selected=${room === this.roomFilter}>
              ${room}
            </option>`,
          )}
        </select>
      </div>
      <div class="filter">
        <span class="label" id="activity-outcome-label">Outcome</span>
        <select
          aria-labelledby="activity-outcome-label"
          @change=${(event: Event) => {
            this.outcomeFilter = (event.target as HTMLSelectElement)
              .value as DecisionLogEntry["outcome"] | "all";
          }}
        >
          ${OUTCOME_FILTERS.map(
            (choice) => html`<option
              value=${choice.value}
              ?selected=${choice.value === this.outcomeFilter}
            >
              ${choice.label}
            </option>`,
          )}
        </select>
      </div>
      ${this.live ? html`<span class="chip ok">live</span>` : nothing}
    </div>`;
  }

  /**
   * The log, or the honest reason there is nothing below the filters.
   *
   * Three states, and the screen tells them apart: a failure (the banner above
   * already says so, and an empty state under it would only repeat the sentence
   * -- the same choice Overview makes); an empty log, which says what to do to
   * fill it; and a filter that matched nothing, which says the filter is why and
   * offers to clear it.
   */
  private renderBody(visible: DecisionLogEntry[]): TemplateResult {
    if (visible.length === 0) {
      if (this.error) return html``;
      if (this.entries.length === 0) {
        return this.emptyState(
          "Nothing yet",
          "Open House has not recorded a decision yet. Turn a module on from " +
            "the Rooms tab, and what it does appears here.",
        );
      }
      return this.emptyState(
        "No matching decisions",
        "The log has entries, but none match these filters.",
        html`<button type="button" @click=${() => this.clearFilters()}>
          Clear filters
        </button>`,
      );
    }
    return html`<div
      class="table-scroll"
      role="region"
      aria-label="Decision log"
      tabindex="0"
    >
      <table>
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
      </table>
    </div>`;
  }

  private renderEntry(entry: DecisionLogEntry): TemplateResult {
    const open = this.expanded.has(entry.id);
    return html`<tr>
      <td class="nowrap" title=${formatTimestamp(entry.at)}>
        ${formatRelative(entry.at)}
      </td>
      <td>
        ${entry.room ?? html`<span class="muted">Whole house</span>`}
      </td>
      <td>
        ${entry.behaviour
          ? html`<span title=${entry.behaviour}
              >${behaviourLabel(entry.behaviour)}</span
            >`
          : html`<span class="muted">—</span>`}
      </td>
      <td>${entry.action}</td>
      <td>
        <span class="chip ${OUTCOME_CHIP[entry.outcome]}"
          >${OUTCOME_LABELS.get(entry.outcome) ?? entry.outcome}</span
        >
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
