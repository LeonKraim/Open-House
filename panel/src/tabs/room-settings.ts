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
 * pack-supplied JS." Every module's settings are a `<open-house-schema-form>`
 * fed the server's `options_schema` cut down to the keys that module owns
 * (`schemaForKeys(room.options_schema, module.option_keys)`), which is built
 * from the installed packs' schemas. Nothing in this file knows a pack by name;
 * a pack that adds an option adds it to the schema and the control appears.
 *
 * The options are drawn inside their module's card and not in a form of their
 * own, because a module is the unit a person installs and configures: the
 * switches are what it does and the settings are how it does it, and a screen
 * that put the switches in one table and every module's numbers in another left
 * a person to match "Door left open for" to the chip it belonged to by guessing.
 *
 * Every admin action is hidden for a non-admin rather than disabled with a
 * tooltip. A disabled button that can never be enabled is noise; the server
 * refuses the command anyway.
 */

import { html, nothing, type TemplateResult } from "lit";
import { OpenHouseElement, formatRelative } from "../base.ts";
import { HOUSE_REACH, reachControl } from "../components/reach.ts";
import {
  fieldOrder,
  schemaForKeys,
  withoutReachRoles,
} from "../components/schema-spec.ts";
import type {
  AxisRef,
  BindingStatus,
  BindingStatusKind,
  BindingSuggestion,
  InstalledModule,
  RoomDetail,
  RoomSummary,
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
  // Everything a click can change lives here: Rename, the picker opening and
  // its Cancel, the per-module option drafts, and the add-module dialog. Each
  // was a plain field, so each of those controls did nothing (see base.ts).
  static override properties = {
    ...OpenHouseElement.properties,
    roomId: { type: String },
    room: { state: true },
    isLoading: { state: true },
    error: { state: true },
    busy: { state: true },
    picker: { state: true },
    drafts: { state: true },
    dirty: { state: true },
    addingModule: { state: true },
    renaming: { state: true },
    renameValue: { state: true },
    rooms: { state: true },
  };

  declare roomId: string;

  private room: RoomDetail | null = null;
  /**
   * Every room, for the reach controls.
   *
   * A behaviour's reach is a set of places, and the answers include rooms that
   * are not this page's -- so a person on the Bedroom's page can switch the
   * bedroom's bedtime atom on in the Kitchen too, rather than having to open
   * the Kitchen and look for it there.
   */
  private rooms: RoomSummary[] = [];
  private isLoading = true;
  private error: ReturnType<OpenHouseElement["toError"]> | null = null;
  private busy = false;
  private picker: PickerState | null = null;
  /**
   * Unsaved settings, per module, keyed by pack name.
   *
   * Per module rather than one page-wide draft, because a module's card owns
   * its settings and its own Save: editing one module's threshold and then
   * another's leaves the first card's Save alone. The write is the same
   * whole-map command either way, and the server takes the map it is given.
   */
  private drafts: Record<string, Record<string, unknown>> = {};
  private dirty: Record<string, boolean> = {};
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
      const client = this.requireClient();
      const [room, rooms] = await Promise.all([
        client.room(this.roomId),
        client.rooms(),
      ]);
      this.rooms = rooms;
      this.apply(room);
    } catch (error) {
      this.error = this.toError(error);
    } finally {
      this.isLoading = false;
      this.requestUpdate();
    }
  }

  private apply(room: RoomDetail): void {
    this.room = room;
    // A reload is the server's answer, so every draft is discarded: keeping an
    // edit through a read that has just told us the stored value would show a
    // form disagreeing with the engine.
    this.drafts = {};
    this.dirty = {};
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

  /**
   * Save one module's settings, and only that module's.
   *
   * The command takes a map of values and writes each key it is given, so
   * sending one card's keys leaves the other cards' settings untouched -- which
   * is what makes the card the unit a person saves rather than the page.
   */
  private async saveModule(pack: string): Promise<void> {
    const values = this.drafts[pack];
    if (values === undefined) return;
    this.busy = true;
    this.error = null;
    this.requestUpdate();
    try {
      await this.requireClient().setRoomOptions(this.roomId, values);
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
      ${this.renderModules(room)}
      <open-house-add-module
        .client=${this.client}
        .roomId=${this.roomId}
        .open=${this.addingModule}
        @add-module-closed=${() => {
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
        Every slot the installed modules can act through. ${bound.length} of
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
        <div class="stack">
          <span
            >${binding.label}
            ${binding.required
              ? html`<span class="chip">required</span>`
              : html`<span class="chip">optional</span>`}</span
          >
        </div>
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
      <p class="help">
        Binding ${this.slotLabel(picker.slot)} &middot; accepts
        ${this.slotDomains(picker.slot)}
      </p>
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

  /** The slot's human name, so the picker says which device it is asking for. */
  private slotLabel(slot: string): string {
    return this.room?.bindings.find((entry) => entry.slot === slot)?.label ?? slot;
  }

  /**
   * One module's card: what it does and how it is set, together.
   *
   * A module's behaviours and its settings are the same subject -- "the door
   * has been open too long" is a behaviour, and how long "too long" is is one of
   * its settings -- so the card is the module and everything it owns is inside
   * it. The settings form is the page's schema cut to this module's keys, so a
   * module with no settings simply has no form and one with settings gets only
   * its own.
   */
  private renderModuleCard(
    module: InstalledModule,
    room: RoomDetail,
  ): TemplateResult {
    // `?? []` because a backend older than this field answers without it: a
    // missing list is a module with no settings yet, not a page that throws.
    const schema = withoutReachRoles(
      schemaForKeys(room.options_schema, module.option_keys ?? []),
    );
    const draft = { ...room.options, ...(this.drafts[module.pack] ?? {}) };
    const isDirty = this.dirty[module.pack] === true;
    return html`<div class="card">
      <div class="row spread wrap">
        <div class="stack">
          <h3>${module.name || module.pack}</h3>
          <p class="muted small">
            ${module.room_id === this.roomId
              ? null
              : html`<span
                  class="chip"
                  title=${`Installed in ${
                    module.room_id === "" ? "the whole house" : module.room_id
                  }, and it reaches this room because this room binds the roles it acts through.`}
                  >in ${module.room_id === "" ? "the house" : module.room_id}</span
                >`}
            v${module.version}
            ${module.satisfiable
              ? null
              : html`<span class="chip warn"
                  >needs ${module.missing_slots.join(", ")}</span
                >`}
          </p>
        </div>
        ${this.admin && module.room_id === this.roomId
          ? html`<div class="row">
              <button
                type="button"
                class="icon"
                ?disabled=${this.busy || (!module.enabled && !module.satisfiable)}
                title=${!module.enabled && !module.satisfiable
                  ? `Bind ${module.missing_slots.join(", ")} on this room's settings first.`
                  : ""}
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
                ?disabled=${this.busy}
                @click=${() =>
                  void this.mutate(async () => {
                    await this.requireClient().uninstallModule(
                      this.roomId,
                      module.pack,
                    );
                    return this.requireClient().room(this.roomId);
                  })}
              >
                Remove
              </button>
            </div>`
          : null}
      </div>

      ${module.behaviours.length === 0
        ? null
        : html`<div class="stack" style="margin-top:10px">
            <span class="muted small">Does this:</span>
            ${module.behaviours.map((behaviour) => {
              const ready = module.satisfiable || behaviour.enabled;
              return html`<div class="stack">
                <div class="row wrap">
                  <label class="toggle">
                    <input
                      type="checkbox"
                      .checked=${behaviour.enabled}
                      ?disabled=${this.busy || !ready || !this.admin}
                      title=${!ready
                        ? `Bind ${module.missing_slots.join(", ")} in this room first.`
                        : ""}
                      @change=${() =>
                        void this.mutate(async () => {
                          await this.requireClient().setModuleBehaviourEnabled(
                            this.roomId,
                            module.pack,
                            behaviour.id,
                            !behaviour.enabled,
                          );
                          return this.requireClient().room(this.roomId);
                        })}
                    />
                    <span>${behaviour.label || behaviour.id}</span>
                  </label>
                  ${this.renderReach(module, behaviour)}
                </div>
                ${behaviour.description
                  ? html`<p class="help" style="margin:0 0 0 2px">
                      ${behaviour.description}
                    </p>`
                  : null}
              </div>`;
            })}
          </div>`}
      ${schema === null
        ? null
        : html`<div style="margin-top:10px">
            <div class="row spread">
              <span class="muted small">Settings</span>
              ${this.admin && isDirty
                ? html`<div class="row">
                    <button
                      type="button"
                      ?disabled=${this.busy}
                      @click=${() => {
                        const { [module.pack]: _dropped, ...rest } = this.drafts;
                        this.drafts = rest;
                        this.dirty = { ...this.dirty, [module.pack]: false };
                      }}
                    >
                      Discard
                    </button>
                    <button
                      type="button"
                      class="primary"
                      ?disabled=${this.busy}
                      @click=${() => void this.saveModule(module.pack)}
                    >
                      Save
                    </button>
                  </div>`
                : null}
            </div>
            <open-house-schema-form
              .schema=${schema}
              .values=${draft}
              .readonly=${!this.admin}
              @value-changed=${(event: CustomEvent<Record<string, unknown>>) => {
                this.drafts = {
                  ...this.drafts,
                  [module.pack]: event.detail,
                };
                this.dirty = { ...this.dirty, [module.pack]: true };
              }}
            ></open-house-schema-form>
          </div>`}
    </div>`;
  }

  /**
   * Settings the room's schema carries that no listed module claims.
   *
   * Drawn rather than dropped. A key with no card would be a setting the engine
   * reads and the screen never showed -- a person could not set it, could not
   * see it, and would have to take the behaviour's word for what it was set to.
   */
  private renderUnclaimed(
    room: RoomDetail,
    claimed: Set<string>,
  ): TemplateResult | typeof nothing {
    const leftovers = fieldOrder(room.options_schema ?? {}).filter(
      (key) => !claimed.has(key),
    );
    if (leftovers.length === 0) return nothing;
    const schema = schemaForKeys(room.options_schema, leftovers);
    if (schema === null) return nothing;
    const draft = { ...room.options, ...(this.drafts[""] ?? {}) };
    const isDirty = this.dirty[""] === true;
    return html`<div class="card">
      <div class="row spread">
        <h3>Other settings</h3>
        ${this.admin && isDirty
          ? html`<div class="row">
              <button
                type="button"
                @click=${() => {
                  const { [""]: _dropped, ...rest } = this.drafts;
                  this.drafts = rest;
                  this.dirty = { ...this.dirty, "": false };
                }}
              >
                Discard
              </button>
              <button
                type="button"
                class="primary"
                ?disabled=${this.busy}
                @click=${() => void this.saveModule("")}
              >
                Save
              </button>
            </div>`
          : null}
      </div>
      <p class="help">
        Settings this room's form resolves that belong to no module installed
        here -- a pack that has since been removed.
      </p>
      <open-house-schema-form
        .schema=${schema}
        .values=${draft}
        .readonly=${!this.admin}
        @value-changed=${(event: CustomEvent<Record<string, unknown>>) => {
          this.drafts = { ...this.drafts, "": event.detail };
          this.dirty = { ...this.dirty, "": true };
        }}
      ></open-house-schema-form>
    </div>`;
  }

  /**
   * The atom's reach, beside its switch: a dropdown of tickable rooms.
   *
   * The setting a person wants on a bedroom's bedtime button -- lights and
   * thermostat in this room, in the Kitchen too, or every light in the house --
   * and the same control the Modules tab and the House tab draw, on the room's
   * own page. The house box is drawn disabled when the house scope cannot
   * resolve the atom's slots (`widenable`), which is the server's verdict and
   * not this screen's.
   */
  private renderReach(
    module: InstalledModule,
    behaviour: InstalledModule["behaviours"][number],
  ): TemplateResult {
    return reachControl({
      module,
      behaviour,
      rooms: this.rooms,
      disabled: this.busy,
      onToggle: (place, on) => void this.setReach(module, behaviour, place, on),
    });
  }

  /**
   * Tick or untick one place the atom applies to.
   *
   * The whole house is the atom widened (`scope: "house"`), one setting on the
   * atom. A room is the atom's own enable flag in that room: a room-scoped atom
   * is evaluated in every room and runs in the ones its flag is on in, so a
   * person on the Bedroom's page can switch its atom on in the Kitchen too --
   * one module, on in both rooms, and a second tick is not a second install.
   *
   * The page is re-read whole rather than through `mutate`, because a tick can
   * change which rooms a card's atoms reach, and the card is drawn from the
   * server's answer.
   */
  private async setReach(
    module: InstalledModule,
    behaviour: InstalledModule["behaviours"][number],
    place: string,
    on: boolean,
  ): Promise<void> {
    this.busy = true;
    this.error = null;
    this.requestUpdate();
    try {
      const client = this.requireClient();
      if (place === HOUSE_REACH) {
        await client.setModuleBehaviourScope(
          this.roomId,
          module.pack,
          behaviour.id,
          on ? "house" : "room",
        );
      } else {
        await client.setModuleBehaviourEnabled(
          place,
          module.pack,
          behaviour.id,
          on,
        );
      }
      await this.load();
    } catch (error) {
      this.error = this.toError(error);
    } finally {
      this.busy = false;
      this.requestUpdate();
    }
  }

  /**
   * The room's modules, one card each: what it does and how it is set, together.
   *
   * A module is the unit a person installs and configures, so it is the unit
   * this page draws. Two lists became one here -- a table of switches and a
   * form of every module's options -- because a screen that split them left a
   * person matching a setting to the chip it belonged to by guessing.
   */
  private renderModules(room: RoomDetail): TemplateResult {
    const claimed = new Set<string>();
    const cards = room.modules.map((module) => {
      for (const key of module.option_keys ?? []) claimed.add(key);
      return this.renderModuleCard(module, room);
    });
    return html`<h2 style="margin-bottom:8px">Modules</h2>
      <p class="help">
        The modules this room's devices are wired into. Each one's switches and
        its settings are here together, because they are the same thing: the
        behaviour is what it does and the settings are how it does it. A module
        installed in another room is drawn here too when this room binds the
        roles it acts through, because this room is where its settings are read
        -- the chip beside its name says where it lives.
      </p>
      ${room.modules.length === 0
        ? html`<div class="card">
            <p class="muted">No modules installed in this room yet.</p>
          </div>`
        : html`<div class="stack">${cards}</div>`}
      ${this.renderUnclaimed(room, claimed)}`;
  }
}

if (!customElements.get("open-house-room-settings")) {
  customElements.define("open-house-room-settings", RoomSettings);
}
