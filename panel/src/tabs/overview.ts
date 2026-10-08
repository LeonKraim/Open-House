/**
 * The Overview tab: is anything wrong, and what is the house doing.
 *
 * It answers both questions from one command. Splitting "the house" and "the
 * alerts" into two calls would make the panel show a room list and a health
 * count that were taken a moment apart, and the one thing this screen is for is
 * a coherent single answer.
 *
 * The screen is a summary, so it is one line and two blocks: the house's name
 * with its mode, its counts and a single status pill; the people as a row of
 * chips; and the rooms as a table. It used to be a title row, an always-on blue
 * "no open issues" banner, a People card and a grid of room cards carrying up
 * to six chips apiece -- a screen that took a scroll to read three facts. The
 * table is the shape that holds them: one row per room, one column per fact,
 * and the empty cells stay silent rather than drawing a chip that says nothing.
 */

import { html, type TemplateResult } from "lit";
import { OpenHouseElement, formatRelative } from "../base.ts";
import type {
  HouseOverview,
  PersonStatus,
  RoomSummary,
} from "../api/models.ts";

/** The three severities, in the order a chip should be coloured by. */
interface IssueCounts {
  info: number;
  warning: number;
  error: number;
}

/**
 * The server's counts, with every field defaulted.
 *
 * The answer is read field by field rather than destructured, because a `null`
 * in any one of them would make the sum `NaN` and every banner render blank --
 * and a single unguarded read inside `render` blanks the *whole tab*, with no
 * message, because the throw happens while Lit is building the template.
 */
function countsOf(overview: HouseOverview): IssueCounts {
  return {
    info: numberOr(overview.issues?.info),
    warning: numberOr(overview.issues?.warning),
    error: numberOr(overview.issues?.error),
  };
}

function numberOr(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

export class OverviewTab extends OpenHouseElement {
  static override properties = {
    ...OpenHouseElement.properties,
    overview: { state: true },
    isLoading: { state: true },
    error: { state: true },
  };

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
      // The last good reading is kept rather than cleared: a Refresh that fails
      // leaves a house worth looking at, and dropping it would replace real
      // numbers with an empty screen. What the failure has to do is *say* the
      // numbers are old, which `render` does from `error` being set.
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
    const overview = this.overview;
    if (!overview) {
      // One message, not two. The error banner already says the read failed, so
      // an empty state drawn under it would repeat the same sentence.
      return this.error
        ? this.errorBanner(this.error)
        : this.emptyState(
            "No overview",
            "The panel could not read the house state.",
          );
    }
    return html`
      ${this.error ? this.renderStale() : null}
      ${this.renderSummary(overview)}
      ${this.renderPeople(overview.people ?? [])}
      ${this.renderRooms(overview.rooms ?? [])}
    `;
  }

  /**
   * The house on one line: its name, its mode, its counts and a status pill.
   *
   * The pill replaces the banner the screen used to carry. A banner that says
   * "no open issues" is a permanent blue box about nothing on a healthy house;
   * a pill says the same thing without spending a row, and the sentence that
   * named the two things that were wrong ("3 errors · 1 warning") is what the
   * pill itself reads.
   */
  private renderSummary(overview: HouseOverview): TemplateResult {
    const mode = (overview.mode ?? "").trim();
    const rooms = overview.rooms ?? [];
    return html`<div class="tab-head">
      <div class="stack">
        <h1>${overview.name || "Open House"}</h1>
        <div class="row wrap muted small">
          ${mode ? html`<span class="chip">${mode}</span>` : null}
          <span
            >${rooms.length} rooms &middot;
            ${numberOr(overview.modules_installed)} modules</span
          >
          <span>&middot; Updated ${formatRelative(overview.updated_at ?? null)}</span>
        </div>
      </div>
      <div class="row wrap">
        ${this.renderStatusPill(countsOf(overview))}
        <button type="button" @click=${() => this.reload()}>Refresh</button>
      </div>
    </div>`;
  }

  /**
   * What the house's issues add up to, in the one chip the summary carries.
   *
   * Coloured by the worst severity it is counting -- a house with any error is
   * an error chip, one with only warnings is a warning chip, and one with only
   * notes is neutral rather than an empty class, so the pill never reads as a
   * word sitting on its own.
   */
  private renderStatusPill(issues: IssueCounts): TemplateResult {
    if (issues.error === 0 && issues.warning === 0 && issues.info === 0) {
      return html`<span class="chip ok">All healthy</span>`;
    }
    const parts: string[] = [];
    if (issues.error > 0) {
      parts.push(`${issues.error} error${issues.error === 1 ? "" : "s"}`);
    }
    if (issues.warning > 0) {
      parts.push(`${issues.warning} warning${issues.warning === 1 ? "" : "s"}`);
    }
    if (issues.info > 0) {
      parts.push(`${issues.info} note${issues.info === 1 ? "" : "s"}`);
    }
    const colour =
      issues.error > 0 ? "error" : issues.warning > 0 ? "warn" : "neutral";
    return html`<span class="chip ${colour}">${parts.join(" · ")}</span>`;
  }

  /** A failed refresh over a reading that is still on screen. */
  private renderStale(): TemplateResult {
    return html`<div class="banner warn" role="status">
      <strong>Could not refresh the house.</strong>
      <p class="help">Showing the last reading.</p>
    </div>`;
  }

  private renderPeople(people: PersonStatus[]): TemplateResult {
    if (people.length === 0) return html``;
    return html`<div class="row wrap people">
      <span class="muted small">People</span>
      ${people.map(
        (person) => html`<span class="chip ${person.home ? "ok" : "neutral"}">
          ${person.name ?? person.entity_id}: ${person.home ? "home" : "away"}
        </span>`,
      )}
    </div>`;
  }

  private renderRooms(rooms: RoomSummary[]): TemplateResult {
    if (rooms.length === 0) {
      return this.emptyState(
        "No rooms yet",
        "Open House has no rooms. Add one from the Rooms tab, or finish setup.",
      );
    }
    return html`<table>
      <thead>
        <tr>
          <th>Room</th>
          <th>Type</th>
          <th>Mode</th>
          <th>Slots</th>
          <th>Profiles</th>
          <th>Status</th>
        </tr>
      </thead>
      <tbody>
        ${rooms.map((room) => this.renderRoomRow(room))}
      </tbody>
    </table>`;
  }

  private renderRoomRow(room: RoomSummary): TemplateResult {
    const missing = room.required_unbound ?? [];
    const issues = numberOr(room.issue_count);
    const mode = (room.mode ?? "").trim();
    const profiles = Object.entries(room.active_profiles ?? {});
    return html`<tr>
      <td>
        ${room.name ?? ""}
        ${room.occupied
          ? html`<span class="chip ok small">occupied</span>`
          : null}
        ${missing.length > 0
          ? html`<details class="missing">
              <summary>
                ${missing.length} required slot${missing.length === 1 ? "" : "s"}
                missing
              </summary>
              <p class="help warn">
                Missing required slots: ${missing.join(", ")}
              </p>
            </details>`
          : null}
      </td>
      <td class="muted">${room.type_label ?? ""}</td>
      <td>
        ${mode
          ? html`<span class="chip">${mode}</span>`
          : html`<span class="muted">—</span>`}
      </td>
      <td>${this.renderSlots(room.bound_slots, room.total_slots)}</td>
      <td>
        ${profiles.length === 0
          ? html`<span class="muted">—</span>`
          : profiles.map(
              ([axis, profile]) =>
                html`<span class="chip">${axis}: ${profile}</span>`,
            )}
      </td>
      <td>${this.renderRoomStatus(issues, missing)}</td>
    </tr>`;
  }

  /**
   * How full a room is, told honestly when there is nothing to fill.
   *
   * A room whose type has no configurable slots answers `0/0`, which reads as
   * "none of nothing bound" -- a fraction of a thing that does not exist. The
   * count is only drawn when there is a count to draw.
   */
  private renderSlots(bound: unknown, total: unknown): TemplateResult {
    const have = numberOr(bound);
    const of = numberOr(total);
    if (of <= 0) {
      return html`<span
        class="muted"
        title="This room type has no configurable slots"
        >no slots</span
      >`;
    }
    return html`<span title="${have} of ${of} slots bound">${have}/${of}</span>`;
  }

  /**
   * The room's status chip, counting the room's issues.
   *
   * `required_unbound` and `issue_count` are different sets: a room can hold an
   * unbound required slot that no health issue has been raised about, and it
   * can hold issues with no unbound slot. So the chip is not coloured by the
   * *count* of one set over the text of the other. It reads the issue count and
   * is coloured by the worst severity the room is known to hold -- an unbound
   * required slot is the server's `required_slot_unbound`, an error, and a room
   * with one is an error chip whatever else it holds; a room whose only problem
   * is not that reads its count in warning colour. A room with neither is fine.
   */
  private renderRoomStatus(
    issues: number,
    missing: readonly string[],
  ): TemplateResult {
    if (issues > 0) {
      return html`<span class="chip ${missing.length > 0 ? "error" : "warn"}"
        >${issues} issue${issues === 1 ? "" : "s"}</span
      >`;
    }
    if (missing.length > 0) {
      // The count is zero because the server raised no issue row for it, but an
      // unbound required slot is not "ok" -- it is the room's own error, named
      // in the expander beside it.
      return html`<span class="chip error"
        >${missing.length} missing</span
      >`;
    }
    return html`<span class="chip ok">ok</span>`;
  }
}

if (!customElements.get("open-house-tab-overview")) {
  customElements.define("open-house-tab-overview", OverviewTab);
}
