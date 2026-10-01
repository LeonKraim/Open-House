/**
 * The Profiles tab: what profiles exist, and which rooms are on one.
 *
 * Profiles are the "premade and preintegrated" idea (spec.txt, Phase 3). The
 * panel's job is not to author them -- a profile-set is a pack -- but to show
 * what is available and to let an admin move a room onto a different one on an
 * axis. Selecting is per room and per axis, so this screen keeps the room and
 * the axis together rather than presenting a flat list of profiles that would
 * lose which of them can run at the same time as which.
 */

import { html, type TemplateResult } from "lit";
import { OpenHouseElement } from "../base.ts";
import type { ProfileRef, RoomSummary } from "../api/models.ts";

export class ProfilesTab extends OpenHouseElement {
  private profiles: ProfileRef[] = [];
  private rooms: RoomSummary[] = [];
  private isLoading = true;
  private error: ReturnType<OpenHouseElement["toError"]> | null = null;
  private busy = false;

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
      const client = this.requireClient();
      const [profiles, rooms] = await Promise.all([
        client.profiles(),
        client.rooms(),
      ]);
      this.profiles = profiles;
      this.rooms = rooms;
    } catch (error) {
      this.error = this.toError(error);
    } finally {
      this.isLoading = false;
      this.requestUpdate();
    }
  }

  private async activate(
    roomId: string,
    axis: string,
    profile: string,
  ): Promise<void> {
    this.busy = true;
    this.error = null;
    try {
      await this.requireClient().activateProfile(roomId, axis, profile);
      await this.load();
    } catch (error) {
      this.error = this.toError(error);
    } finally {
      this.busy = false;
      this.requestUpdate();
    }
  }

  protected override render(): TemplateResult {
    if (this.isLoading && this.profiles.length === 0 && this.rooms.length === 0) {
      return this.loading("Reading profiles...");
    }
    const roomProfiles = this.profiles.filter((p) => p.kind === "room");
    const houseProfiles = this.profiles.filter((p) => p.kind === "house");
    return html`
      ${this.errorBanner(this.error)}
      <h1 style="margin-bottom:12px">Profiles</h1>

      <div class="card">
        <h2>House profiles</h2>
        <p class="help">
          A house profile bundles a selection for every room at once -- a
          "vacation" or "guests over" setup you can switch to whole.
        </p>
        ${houseProfiles.length === 0
          ? html`<p class="muted">No house profiles installed.</p>`
          : html`<div class="grid">
              ${houseProfiles.map(
                (profile) => html`<div class="card">
                  <h3>${profile.label}</h3>
                  <p class="muted small">${profile.description}</p>
                  ${profile.active
                    ? html`<span class="chip ok">active</span>`
                    : html`<button
                        type="button"
                        ?disabled=${!this.admin || this.busy}
                        @click=${() => void this.activate("house", "house", profile.name)}
                      >
                        Activate for the house
                      </button>`}
                </div>`,
              )}
            </div>`}
      </div>

      <div class="card">
        <h2>Room profiles</h2>
        <p class="help">
          Each room runs one profile per axis. Two profiles in one room must be
          on different axes.
        </p>
        ${roomProfiles.length === 0
          ? html`<p class="muted">No room profiles installed.</p>`
          : html`<table>
              <thead>
                <tr>
                  <th>Profile</th>
                  <th>Axis</th>
                  <th>Description</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                ${roomProfiles.map(
                  (profile) => html`<tr>
                    <td>${profile.label}</td>
                    <td>${profile.axis ?? "-"}</td>
                    <td class="muted small">${profile.description}</td>
                    <td>
                      <span class="chip ${profile.active ? "ok" : ""}"
                        >${profile.active ? "in use" : ""}</span
                      >
                    </td>
                  </tr>`,
                )}
              </tbody>
            </table>`}
      </div>

      <div class="card">
        <h2>Rooms</h2>
        ${this.rooms.length === 0
          ? html`<p class="muted">No rooms.</p>`
          : this.rooms.map((room) => this.renderRoom(room))}
      </div>
    `;
  }

  private renderRoom(room: RoomSummary): TemplateResult {
    const axes = new Map<string, ProfileRef[]>();
    for (const profile of this.profiles) {
      if (profile.kind !== "room" || profile.axis === null) continue;
      const bucket = axes.get(profile.axis) ?? [];
      bucket.push(profile);
      axes.set(profile.axis, bucket);
    }
    return html`<div class="field">
      <div class="row spread">
        <span class="label">${room.name}</span>
        <div class="row wrap">
          ${Object.entries(room.active_profiles).map(
            ([axis, profile]) => html`<span class="chip">${axis}: ${profile}</span>`,
          )}
        </div>
      </div>
      ${axes.size === 0
        ? html`<p class="muted small">No room profiles available.</p>`
        : html`<div class="grid">
            ${[...axes.entries()].map(
              ([axis, profiles]) => html`<div>
                <div class="label-row"><span class="label">${axis}</span></div>
                <select
                  aria-label="${room.name} ${axis}"
                  ?disabled=${!this.admin || this.busy}
                  @change=${(event: Event) =>
                    void this.activate(
                      room.id,
                      axis,
                      (event.target as HTMLSelectElement).value,
                    )}
                >
                  ${profiles.map(
                    (profile) => html`<option
                      value=${profile.name}
                      ?selected=${room.active_profiles[axis] === profile.name}
                    >
                      ${profile.label}
                    </option>`,
                  )}
                </select>
              </div>`,
            )}
          </div>`}
    </div>`;
  }
}

if (!customElements.get("open-house-tab-profiles")) {
  customElements.define("open-house-tab-profiles", ProfilesTab);
}
