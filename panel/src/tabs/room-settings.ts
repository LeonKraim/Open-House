/**
 * The per-room settings page: every bound device, and only high-level options.
 *
 * Two clauses of spec.txt meet here.
 *
 * "Lists every bound device with status and rebind/replace actions." The table
 * below is every slot the room type provides: bound ones show the device, its
 * live state and its health; unbound ones show what the slot wants and offer to
 * fill it. Status is the server's verdict (`BindingStatus.status`), not the
 * panel's guess at it -- the panel does not know whether `unavailable` should
 * be an error or a warning for a given slot, and the engine does.
 *
 * "Shows only high-level options, rendered from pack schemas with no
 * pack-supplied JS." The options block is a `<open-house-schema-form>` fed the
 * server's `options_schema`, which is built from the installed packs' schemas.
 * Nothing in this file knows a pack by name; a pack that adds an option adds it
 * to the schema and the control appears.
 *
 * Every admin action is hidden for a non-admin rather than disabled with a
 * tooltip. A disabled button that can never be enabled is noise; the server
 * refuses the command anyway.
 */

import { html, nothing, type TemplateResult } from "lit";
import { OpenHouseElement, formatRelative } from "../base.ts";
import type {
  AxisRef,
  BindingStatus,
  BindingStatusKind,
  BindingSuggestion,
  RoomDetail,
} from "../api/models.ts";

interface PickerState {
  slot: string;
  /** True when this picker is replacing an existing binding. */
  replace: boolean;
  candidates: BindingSuggestion[];
  query: string;
  loading: boolean;
}

const STATUS_LABEL: Record<BindingStatusKind, string> = {
  ok: "ok",
  unavailable: "unavailable",
  unknown: "unknown",
  missing: "missing",
  domain_mismatch: "wrong domain",
  unbound: "unbound",
};

const STATUS_CHIP: Record<BindingStatusKind, string> = {
  ok: "ok",
  unavailable: "warn",
  unknown: "warn",
  missing: "error",
  domain_mismatch: "error",
  unbound: "",
};

export class RoomSettings extends OpenHouseElement {
  static override properties = {
    ...OpenHouseElement.properties,
    roomId: { type: String },
  };

  declare roomId: string;

  private room: RoomDetail | null = null;
  private isLoading = true;
  private error: ReturnType<OpenHouseElement["toError"]> | null = null;
  private busy = false;
  private picker: PickerState | null = null;
  private optionsDraft: Record<string, unknown> | null = null;
  private optionsDirty = false;
  private addingModule = false;
  private renaming = false;
  private renameValue = "";

  constructor() {
    super();
    this.roomId = "";
  }

  override connectedCallback(): void {
    super.connectedCallback();
    void this.load();
  }

  override updated(changed: Map<string, unknown>): void {
    if (changed.has("roomId") && this.roomId !== "" && changed.get("roomId")) {
      void this.load();
    }
  }

  private async load(): Promise<void> {
    if (this.roomId === "") return;
    this.isLoading = true;
    this.error = null;
    this.requestUpdate();
    try {
      this.apply(await this.requireClient().room(this.roomId));
    } catch (error) {
      this.error = this.toError(error);
    } finally {
      this.isLoading = false;
      this.requestUpdate();
    }
  }

  private apply(room: RoomDetail): void {
    this.room = room;
    this.optionsDraft = { ...room.options };
    this.optionsDirty = false;
  }

  private async mutate(operation: () => Promise<RoomDetail>): Promise<void> {
    this.busy = true;
    this.error = null;
    this.requestUpdate();
    try {
      this.apply(await operation());
      this.picker = null;
    } catch (error) {
      this.error = this.toError(error);
    } finally {
      this.busy = false;
      this.requestUpdate();
    }
  }

  private async openPicker(slot: string, replace: boolean): Promise<void> {
    this.picker = {
      slot,
      replace,
      candidates: [],
      query: "",
      loading: true,
    };
    this.requestUpdate();
    try {
      const candidates = await this.requireClient().candidates(
        this.roomId,
        slot,
      );
      if (this.picker && this.picker.slot === slot) {
        this.picker = { ...this.picker, candidates, loading: false };
      }
    } catch (error) {
      this.error = this.toError(error);
      this.picker = null;
    } finally {
      this.requestUpdate();
    }
  }

  private async search(query: string): Promise<void> {
    const picker = this.picker;
    if (!picker) return;
    this.picker = { ...picker, query };
    this.requestUpdate();
    try {
      const candidates = await this.requireClient().candidates(
        this.roomId,
        picker.slot,
        query,
      );
      if (this.picker && this.picker.slot === picker.slot) {
        this.picker = { ...this.picker, candidates, query };
      }
    } catch (error) {
      this.error = this.toError(error);
    } finally {
      this.requestUpdate();
    }
  }

  private async choose(suggestion: BindingSuggestion): Promise<void> {
    const picker = this.picker;
    if (!picker) return;
    await this.mutate(() =>
      picker.replace
        ? this.requireClient().replace(this.roomId, picker.slot, suggestion.entity_id)
        : this.requireClient().bind(this.roomId, picker.slot, suggestion.entity_id),
    );
  }

  private async saveOptions(): Promise<void> {
    if (!this.optionsDraft) return;
    this.busy = true;
    this.error = null;
    this.requestUpdate();
    try {
      await this.requireClient().setRoomOptions(this.roomId, this.optionsDraft);
      this.optionsDirty = false;
      await this.load();
    } catch (error) {
      this.error = this.toError(error);
    } finally {
      this.busy = false;
      this.requestUpdate();
    }
  }

  protected override render(): TemplateResult {
    if (this.isLoading && !this.room) return this.loading("Reading the room...");
    const room = this.room;
    if (!room) {
      return html`${this.errorBanner(this.error)}
      ${this.emptyState("Room not found", "This room is no longer in the house.")}`;
    }
    return html`
      ${this.errorBanner(this.error)} ${this.renderHeader(room)}
      ${this.renderProfiles(room)} ${this.renderBindings(room)}
      ${this.renderRoomOptions()} ${this.renderModules(room)}
      <open-house-add-module
        .client=${this.client}
        .roomId=${this.roomId}
        .open=${this.addingModule}
        @closed=${() => {
          this.addingModule = false;
        }}
        @module-installed=${() => void this.load()}
      ></open-house-add-module>
    `;
  }

  private renderHeader(room: RoomDetail): TemplateResult {
    return html`<div class="row spread wrap" style="margin-bottom:12px">
      <div class="row">
        <button
          type="button"
          class="icon"
          @click=${() =>
            this.dispatchEvent(new CustomEvent("closed", { bubbles: true, composed: true }))}
        >
          Back
        </button>
        <div class="stack">
          ${this.renaming
            ? html`<div class="row">
                <input
                  type="text"
                  .value=${this.renameValue}
                  aria-label="Room name"
                  @input=${(event: Event) => {
                    this.renameValue = (event.target as HTMLInputElement).value;
                  }}
                />
                <button
                  type="button"
                  class="primary"
                  @click=${() => {
                    this.renaming = false;
                    void this.mutate(() =>
                      this.requireClient().updateRoom(this.roomId, this.renameValue),
                    );
                  }}
                >
                  Save
                </button>
              </div>`
            : html`<h1>
                ${room.name}
                ${this.admin
                  ? html`<button
                      type="button"
                      class="icon"
                      @click=${() => {
                        this.renameValue = room.name;
                        this.renaming = true;
                      }}
                    >
                      Rename
                    </button>`
                  : null}
              </h1>`}
          <p class="muted">
            ${room.type_label || room.type} &middot;
            <span class="chip">mode ${room.mode}</span>
          </p>
        </div>
      </div>
      <div class="row">
        <button
          type="button"
          class="secondary"
          @click=${() => void this.generateDashboard()}
        >
          Generate dashboard
        </button>
        ${this.admin
          ? html`<button
              type="button"
              class="primary"
              @click=${() => {
                this.addingModule = true;
              }}
            >
              Add module to room
            </button>`
          : null}
      </div>
    </div>`;
  }

  private async generateDashboard(): Promise<void> {
    this.error = null;
    try {
      await this.requireClient().generateDashboard(this.roomId);
    } catch (error) {
      this.error = this.toError(error);
    } finally {
      this.requestUpdate();
    }
  }

  private renderProfiles(room: RoomDetail): TemplateResult {
    if (room.axes.length === 0) return html``;
    return html`<div class="card">
      <h2>Profiles</h2>
      <p class="help">
        A room can run one profile per axis at once, so lighting and climate are
        chosen independently.
      </p>
      <div class="grid" style="margin-top:8px">
        ${room.axes.map((axis) => this.renderAxis(axis))}
      </div>
    </div>`;
  }

  private renderAxis(axis: AxisRef): TemplateResult {
    const active = axis.profiles.find((profile) => profile.active);
    return html`<div class="field">
      <div class="label-row"><span class="label">${axis.label}</span></div>
      ${axis.profiles.length === 0
        ? html`<p class="muted">No profiles on this axis.</p>`
        : html`<select
            aria-label=${axis.label}
            ?disabled=${!this.admin || this.busy}
            @change=${(event: Event) => {
              const name = (event.target as HTMLSelectElement).value;
              void this.mutate(() =>
                this.requireClient().activateProfile(this.roomId, axis.id, name),
              );
            }}
          >
            ${axis.profiles.map(
              (profile) => html`<option
                value=${profile.name}
                ?selected=${profile.name === active?.name}
              >
                ${profile.label}
              </option>`,
            )}
          </select>`}
      ${active ? html`<p class="help">${active.description}</p>` : null}
    </div>`;
  }

  private renderBindings(room: RoomDetail): TemplateResult {
    const bound = room.bindings.filter((binding) => binding.entity_id !== null);
    return html`<div class="card">
      <h2>Devices</h2>
      <p class="help">
        Every slot this room type provides. ${bound.length} of
        ${room.bindings.length} are bound.
      </p>
      <table>
        <thead>
          <tr>
            <th>Slot</th>
            <th>Device</th>
            <th>State</th>
            <th>Status</th>
            <th>Changed</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          ${room.bindings.map((binding) => this.renderBinding(binding))}
        </tbody>
      </table>
      ${this.picker ? this.renderPicker() : null}
    </div>`;
  }

  private renderBinding(binding: BindingStatus): TemplateResult {
    return html`<tr>
      <td>
        ${binding.label}
        ${binding.required
          ? html`<span class="chip">required</span>`
          : html`<span class="chip">optional</span>`}
      </td>
      <td>
        ${binding.entity_id
          ? html`<div class="stack">
              <span>${binding.friendly_name ?? binding.entity_id}</span>
              <span class="muted small">${binding.entity_id}</span>
            </div>`
          : html`<span class="muted">Nothing bound</span>`}
      </td>
      <td>${binding.state ?? html`<span class="muted">-</span>`}</td>
      <td>
        <span class="chip ${STATUS_CHIP[binding.status]}"
          >${STATUS_LABEL[binding.status]}</span
        >
      </td>
      <td class="muted small">${formatRelative(binding.last_changed)}</td>
      <td>
        ${this.admin
          ? html`<div class="row">
              ${binding.entity_id
                ? html`<button
                      type="button"
                      class="icon"
                      @click=${() => void this.openPicker(binding.slot, true)}
                      >Replace</button
                    >
                    <button
                      type="button"
                      class="icon"
                      @click=${() => void this.openPicker(binding.slot, false)}
                      >Rebind</button
                    >
                    <button
                      type="button"
                      class="icon"
                      @click=${() =>
                        void this.mutate(() =>
                          this.requireClient().unbind(this.roomId, binding.slot),
                        )}
                      >Unbind</button
                    >`
                : html`<button
                    type="button"
                    class="icon primary"
                    @click=${() => void this.openPicker(binding.slot, false)}
                    >Bind</button
                  >`}
            </div>`
          : null}
      </td>
    </tr>`;
  }

  private renderPicker(): TemplateResult {
    const picker = this.picker;
    if (!picker) return html``;
    return html`<div class="banner info" role="group" aria-label="Choose a device">
      <div class="row spread wrap">
        <strong>
          ${picker.replace ? "Replace" : "Bind"} the ${picker.slot} slot
        </strong>
        <button
          type="button"
          class="icon"
          @click=${() => {
            this.picker = null;
          }}
        >
          Cancel
        </button>
      </div>
      <input
        type="text"
        class="grow"
        placeholder="Search devices"
        aria-label="Search devices"
        .value=${picker.query}
        @input=${(event: Event) =>
          void this.search((event.target as HTMLInputElement).value)}
      />
      ${picker.loading
        ? html`<p class="muted">Looking for devices...</p>`
        : picker.candidates.length === 0
          ? html`<p class="muted">
              No match. This slot accepts ${this.slotDomains(picker.slot)}.
            </p>`
          : html`<div class="stack" style="margin-top:8px">
              ${picker.candidates.map(
                (candidate) => html`<div class="row spread">
                  <div class="stack">
                    <span>${candidate.friendly_name}</span>
                    <span class="muted small"
                      >${candidate.entity_id} &middot; ${candidate.domain}</span
                    >
                  </div>
                  <button
                    type="button"
                    class="primary"
                    ?disabled=${this.busy}
                    @click=${() => void this.choose(candidate)}
                  >
                    Use this
                  </button>
                </div>`,
              )}
            </div>`}
    </div>`;
  }

  private slotDomains(slot: string): string {
    const binding = this.room?.bindings.find((entry) => entry.slot === slot);
    return binding ? binding.accepts_domains.join(", ") : "any device";
  }

  private renderRoomOptions(): TemplateResult {
    const room = this.room;
    if (!room) return html``;
    if (!room.options_schema) {
      return html`<div class="card">
        <h2>Options</h2>
        <p class="muted">
          No installed module declares options for this room.
        </p>
      </div>`;
    }
    return html`<div class="card">
      <div class="row spread">
        <h2>Options</h2>
        ${this.admin && this.optionsDirty
          ? html`<div class="row">
              <button
                type="button"
                @click=${() => {
                  this.optionsDraft = { ...room.options };
                  this.optionsDirty = false;
                }}
              >
                Discard
              </button>
              <button
                type="button"
                class="primary"
                ?disabled=${this.busy}
                @click=${() => void this.saveOptions()}
              >
                Save
              </button>
            </div>`
          : null}
      </div>
      <p class="help">
        These are the high-level options the installed modules declare. They are
        rendered from the packs' own schemas, so a pack can add one without the
        panel changing.
      </p>
      <open-house-schema-form
        .schema=${room.options_schema}
        .values=${this.optionsDraft ?? room.options}
        .readonly=${!this.admin}
        @value-changed=${(event: CustomEvent<Record<string, unknown>>) => {
          this.optionsDraft = event.detail;
          this.optionsDirty = true;
        }}
      ></open-house-schema-form>
    </div>`;
  }

  private renderModules(room: RoomDetail): TemplateResult {
    return html`<div class="card">
      <h2>Modules</h2>
      ${room.modules.length === 0
        ? html`<p class="muted">No modules installed in this room yet.</p>`
        : html`<table>
            <thead>
              <tr>
                <th>Module</th>
                <th>Version</th>
                <th>Behaviours</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              ${room.modules.map(
                (module) => html`<tr>
                  <td>${module.name || module.pack}</td>
                  <td class="muted">v${module.version}</td>
                  <td>
                    <div class="row wrap">
                      ${module.behaviours.map(
                        (behaviour) =>
                          html`<span class="chip ${behaviour.enabled ? "ok" : ""}"
                            >${behaviour.label || behaviour.id}</span
                          >`,
                      )}
                    </div>
                  </td>
                  <td>
                    ${this.admin
                      ? html`<div class="row">
                          <button
                            type="button"
                            class="icon"
                            @click=${() =>
                              void this.mutate(async () => {
                                await this.requireClient().setModuleEnabled(
                                  this.roomId,
                                  module.pack,
                                  !module.enabled,
                                );
                                return this.requireClient().room(this.roomId);
                              })}
                          >
                            ${module.enabled ? "Disable" : "Enable"}
                          </button>
                          <button
                            type="button"
                            class="icon danger"
                            @click=${() =>
                              void this.mutate(() =>
                                this.requireClient().uninstallModule(
                                  this.roomId,
                                  module.pack,
                                ),
                              )}
                          >
                            Remove
                          </button>
                        </div>`
                      : nothing}
                  </td>
                </tr>`,
              )}
            </tbody>
          </table>`}
    </div>`;
  }
}

if (!customElements.get("open-house-room-settings")) {
  customElements.define("open-house-room-settings", RoomSettings);
}
