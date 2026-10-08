/**
 * The Rooms tab: the list, and the way into one room's settings page.
 *
 * The list stays mounted behind the settings page rather than being replaced,
 * so "back" is a state change and not a refetch of a list the user has already
 * seen -- and the room they return to still shows the counts they left.
 */

import { html, type TemplateResult } from "lit";
import { OpenHouseElement } from "../base.ts";
import type { RoomSummary } from "../api/models.ts";

import "../components/house-profile.ts";

export class RoomsTab extends OpenHouseElement {
  // `selectedRoomId` and `creating` are clicks, not fetches: nothing else in
  // this element changes when they do, so they have to be reactive themselves
  // (see base.ts). As plain fields, "Add room" and a room's own name did
  // nothing at all.
  static override properties = {
    ...OpenHouseElement.properties,
    initialRoomId: { type: String },
    rooms: { state: true },
    selectedRoomId: { state: true },
    isLoading: { state: true },
    error: { state: true },
    creating: { state: true },
    newRoomName: { state: true },
    newRoomType: { state: true },
  };

  /**
   * A room to open on arrival, set by the root when another tab asks to
   * navigate here. It is read once and then ignored, so a later re-render does
   * not yank the user back to the room they were sent to ten minutes ago.
   */
  declare initialRoomId: string;

  private rooms: RoomSummary[] = [];
  private selectedRoomId: string | null = null;
  private isLoading = true;
  private error: ReturnType<OpenHouseElement["toError"]> | null = null;
  private creating = false;
  private newRoomName = "";
  private newRoomType = "";

  constructor() {
    super();
    this.initialRoomId = "";
  }

  override connectedCallback(): void {
    super.connectedCallback();
    if (this.initialRoomId !== "") this.selectedRoomId = this.initialRoomId;
    void this.load();
  }

  override updated(changed: Map<string, unknown>): void {
    const requested = changed.get("initialRoomId");
    if (typeof requested === "string" && requested !== "") {
      this.selectedRoomId = requested;
      this.initialRoomId = "";
    }
  }

  reload(): void {
    void this.load();
  }

  private async load(): Promise<void> {
    this.isLoading = true;
    this.error = null;
    this.requestUpdate();
    try {
      this.rooms = await this.requireClient().rooms();
    } catch (error) {
      this.error = this.toError(error);
    } finally {
      this.isLoading = false;
      this.requestUpdate();
    }
  }

  private async createRoom(): Promise<void> {
    const name = this.newRoomName.trim();
    const type = this.newRoomType.trim() || "room";
    if (!name) return;
    this.error = null;
    try {
      const room = await this.requireClient().createRoom(name, type);
      this.creating = false;
      this.newRoomName = "";
      this.newRoomType = "";
      await this.load();
      this.selectedRoomId = room.id;
    } catch (error) {
      this.error = this.toError(error);
    } finally {
      this.requestUpdate();
    }
  }

  protected override render(): TemplateResult {
    if (this.selectedRoomId !== null) {
      return html`<open-house-room-settings
        .client=${this.client}
        .admin=${this.admin}
        .narrow=${this.narrow}
        .hass=${this.hass}
        .roomId=${this.selectedRoomId}
        @closed=${() => {
          this.selectedRoomId = null;
          void this.load();
        }}
      ></open-house-room-settings>`;
    }
    if (this.isLoading && this.rooms.length === 0) {
      return this.loading("Reading rooms...");
    }
    return html`
      ${this.errorBanner(this.error)}
      <div class="row spread wrap" style="margin-bottom:12px">
        <h1>Rooms</h1>
        ${this.admin
          ? html`<button
              type="button"
              class="primary"
              @click=${() => {
                this.creating = !this.creating;
              }}
            >
              ${this.creating ? "Cancel" : "Add room"}
            </button>`
          : null}
      </div>
      ${this.admin
        ? html`<open-house-house-profile
            .client=${this.client}
            .hass=${this.hass}
            .admin=${this.admin}
            @profiles-changed=${() => void this.load()}
          ></open-house-house-profile>`
        : null}
      ${this.creating ? this.renderCreateForm() : null}
      ${this.rooms.length === 0
        ? this.emptyState(
            "No rooms",
            "No rooms exist yet. A room is a Home Assistant area with a room type; add one to begin.",
          )
        : html`<table>
            <thead>
              <tr>
                <th>Room</th>
                <th>Type</th>
                <th>Slots</th>
                <th>Mode</th>
                <th>Status</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              ${this.rooms.map((room) => this.renderRow(room))}
            </tbody>
          </table>`}
    `;
  }

  private renderCreateForm(): TemplateResult {
    return html`<div class="card">
      <h3>New room</h3>
      <div class="field">
        <div class="label-row"><span class="label">Name</span></div>
        <input
          type="text"
          .value=${this.newRoomName}
          placeholder="Kitchen"
          @input=${(event: Event) => {
            this.newRoomName = (event.target as HTMLInputElement).value;
          }}
        />
      </div>
      <div class="field">
        <div class="label-row"><span class="label">Room type</span></div>
        <input
          type="text"
          .value=${this.newRoomType}
          placeholder="kitchen"
          @input=${(event: Event) => {
            this.newRoomType = (event.target as HTMLInputElement).value;
          }}
        />
        <p class="help">
          The catalog's room-type name. Leave empty for a plain room.
        </p>
      </div>
      <button type="button" class="primary" @click=${() => void this.createRoom()}>
        Create room
      </button>
    </div>`;
  }

  private renderRow(room: RoomSummary): TemplateResult {
    const unfinished = room.required_unbound.length > 0;
    return html`<tr>
      <td>
        <a
          href="#"
          @click=${(event: Event) => {
            event.preventDefault();
            this.selectedRoomId = room.id;
          }}
          >${room.name}</a
        >
        ${room.issue_count > 0
          ? html`<span class="chip warn small">${room.issue_count}</span>`
          : null}
      </td>
      <td class="muted">${room.type_label}</td>
      <td>${room.bound_slots}/${room.total_slots}</td>
      <td><span class="chip">${room.mode}</span></td>
      <td>
        ${unfinished
          ? html`<span class="chip warn"
              >missing ${room.required_unbound.length}</span
            >`
          : html`<span class="chip ok">ready</span>`}
      </td>
      <td>
        <button
          type="button"
          class="icon"
          @click=${() => {
            this.selectedRoomId = room.id;
          }}
        >
          Settings
        </button>
      </td>
    </tr>`;
  }
}

if (!customElements.get("open-house-tab-rooms")) {
  customElements.define("open-house-tab-rooms", RoomsTab);
}
