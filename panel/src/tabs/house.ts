/**
 * The House tab: the whole house, as a target in its own right.
 *
 * Two screens were one before this. The Rooms tab answers "what is in this
 * room"; this answers "what is the house", and the house is a place a person can
 * *do* things -- install a module into it, switch its modules on, set its
 * options -- and not only a summary to read. So it is drawn like a room's page:
 * its slots, its options, its modules, each with the same controls a room's page
 * draws, because a module put in the house and a module put in a room are the
 * same kind of thing in two different places.
 *
 * A house slot is not bound here. It is *collected* (`engine.binding.resolve_slot`
 * gathers it from every room, in room order), so the one edit a person can make
 * to "all the lights" is a light in a room, and this screen is where that fact is
 * legible rather than something a person has to infer from a behaviour that
 * reached through it. A role no room has filled yet is drawn anyway, empty: "the
 * house has no door contacts" is exactly what a person needs to see before
 * wondering why the alarm never fires.
 *
 * The house's *options* and its *modules* are not collections, though. A module
 * can be installed into the house (`""` is the house's placement), and that
 * module's settings are the house's, resolving at house scope: they are the
 * controls this tab owns rather than borrows.
 */

import { html, nothing, type TemplateResult } from "lit";
import { OpenHouseElement } from "../base.ts";
import { HOUSE_REACH, reachControl } from "../components/reach.ts";
import {
  fieldOrder,
  schemaForKeys,
  withoutReachRoles,
} from "../components/schema-spec.ts";
import type {
  BindingStatusKind,
  BindingSuggestion,
  HouseScope,
  HouseSlot,
  InstalledModule,
  RoomSummary,
} from "../api/models.ts";

/** The house's own binding, as a room's picker sees it: no room, just the house. */
const HOUSE_ID = "";

interface PickerState {
  slot: string;
  /** True when this picker is replacing a binding the house already holds. */
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
  unknown: "",
  missing: "warn",
  domain_mismatch: "warn",
  unbound: "",
};

export class HouseTab extends OpenHouseElement {
  static override properties = {
    ...OpenHouseElement.properties,
    scope: { state: true },
    isLoading: { state: true },
    error: { state: true },
    busy: { state: true },
    drafts: { state: true },
    dirty: { state: true },
    addingModule: { state: true },
    picker: { state: true },
    rooms: { state: true },
  };

  private scope: HouseScope | null = null;
  private isLoading = true;
  private error: ReturnType<OpenHouseElement["toError"]> | null = null;
  private busy: string | null = null;
  /**
   * Unsaved settings, per module.
   *
   * Per module rather than one page-wide draft, because a module is now the
   * unit: its card owns its settings and its own Save, so editing one module's
   * threshold and then editing another's leaves the first card's Save alone
   * where it was. The write is the same whole-map command either way, and the
   * server takes the map it is given.
   */
  private drafts: Record<string, Record<string, unknown>> = {};
  private dirty: Record<string, boolean> = {};
  private addingModule = false;
  private picker: PickerState | null = null;
  /** Every room, so a behaviour's reach control can name where it applies. */
  private rooms: RoomSummary[] = [];

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
      const [scope, rooms] = await Promise.all([
        client.houseScope(),
        client.rooms(),
      ]);
      this.rooms = rooms;
      this.apply(scope);
    } catch (error) {
      this.error = this.toError(error);
    } finally {
      this.isLoading = false;
      this.requestUpdate();
    }
  }

  private apply(scope: HouseScope): void {
    this.scope = scope;
    // A reload is the server's answer, so every draft is discarded: keeping an
    // edit through a read that has just told us the stored value would show a
    // form disagreeing with the engine.
    this.drafts = {};
    this.dirty = {};
    // The picker too: a slot just bound is not one to go on offering devices for.
    this.picker = null;
  }

  /** Run a write, then re-read the house. The house is the page being redrawn. */
  private async act(key: string, operation: () => Promise<unknown>): Promise<void> {
    this.busy = key;
    this.error = null;
    this.requestUpdate();
    try {
      await operation();
      await this.load();
    } catch (error) {
      this.error = this.toError(error);
    } finally {
      this.busy = null;
      this.requestUpdate();
    }
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
    this.busy = `options:${pack}`;
    this.error = null;
    this.requestUpdate();
    try {
      await this.requireClient().setHouseOptions(values);
      await this.load();
    } catch (error) {
      this.error = this.toError(error);
    } finally {
      this.busy = null;
      this.requestUpdate();
    }
  }

  /**
   * Ask the server which devices could fill one global slot.
   *
   * The same call a room's picker makes, with the house as the placement: the
   * server answers with every entity the house holds that the slot accepts,
   * because a global slot is a role no room owns (`views.candidates`).
   */
  private async openPicker(slot: string, replace: boolean): Promise<void> {
    this.picker = { slot, replace, candidates: [], query: "", loading: true };
    this.requestUpdate();
    try {
      const candidates = await this.requireClient().candidates(
        HOUSE_ID,
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
        HOUSE_ID,
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

  /**
   * Bind the chosen device to the global slot, or replace what is there.
   *
   * `bind` and `replace` are the room commands with the house as the room: the
   * server routes `HOUSE_ID` to the house's own binding, which is what makes a
   * global slot writable at all.
   */
  private async choose(suggestion: BindingSuggestion): Promise<void> {
    const picker = this.picker;
    if (!picker) return;
    await this.act(`binding:${picker.slot}`, () =>
      picker.replace
        ? this.requireClient().replace(HOUSE_ID, picker.slot, suggestion.entity_id)
        : this.requireClient().bind(HOUSE_ID, picker.slot, suggestion.entity_id),
    );
  }

  protected override render(): TemplateResult {
    if (this.isLoading && this.scope === null) {
      return this.loading("Reading the house...");
    }
    const scope = this.scope;
    if (scope === null) {
      return html`${this.errorBanner(this.error)}
      ${this.emptyState("No house", "This house could not be read.")}`;
    }
    return html`
      ${this.errorBanner(this.error)} ${this.renderHeader(scope)}
      ${this.renderSlots(scope)} ${this.renderModules(scope)}
      <open-house-add-module
        .client=${this.client}
        .roomId=${""}
        .heading=${"Add module to house"}
        .open=${this.addingModule}
        @add-module-closed=${() => {
          this.addingModule = false;
        }}
        @module-installed=${() => void this.load()}
      ></open-house-add-module>
    `;
  }

  private renderHeader(scope: HouseScope): TemplateResult {
    return html`<div class="row spread wrap" style="margin-bottom:12px">
      <div class="stack">
        <h1>${scope.name || "House"}</h1>
        <p class="muted">
          The whole house &middot;
          <span class="chip">${scope.modules.length} modules</span>
        </p>
      </div>
      <div class="row">
        ${this.admin
          ? html`<button
              type="button"
              class="primary"
              @click=${() => {
                this.addingModule = true;
              }}
            >
              Add module to house
            </button>`
          : null}
      </div>
    </div>`;
  }

  /**
   * The house's slots: one row per role a module actually reaches.
   *
   * Drawn the way a room's devices are, because it is the same act: pick the
   * device a role points at. What is different is what the binding *means* --
   * this one device stands in for the role in the house scope and in every room
   * that bound none of its own -- so the row says so where a room's row does not
   * need to.
   *
   * The list is the server's, already cut to the roles an installed module
   * reaches: a role nothing acts through has no behaviour behind the control.
   */
  private renderSlots(scope: HouseScope): TemplateResult {
    return html`<div class="card">
      <h2>House slots</h2>
      <p class="help">
        The devices the whole house's automations act through. A slot bound here
        is global: it fills that role for the house, and for every room that has
        not bound one of its own. Only the slots a module on this page reaches
        are listed, so every row has an automation behind it.
      </p>
      ${scope.slots.length === 0
        ? html`<p class="muted">
            No module needs a house-wide device yet, so there is nothing to bind
            here. Installing one adds the slots it reaches.
          </p>`
        : html`<table>
              <thead>
                <tr>
                  <th>Slot</th>
                  <th>Device</th>
                  <th>State</th>
                  <th>Status</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                ${scope.slots.map((slot) => this.renderSlot(slot))}
              </tbody>
            </table>
            ${this.picker ? this.renderPicker(scope) : null}`}
    </div>`;
  }

  private renderModuleCard(
    module: InstalledModule,
    scope: HouseScope,
  ): TemplateResult {
    // `?? []` because a backend older than this field answers without it: a
    // missing list is a module with no settings yet, not a tab that throws.
    const keys = module.option_keys ?? [];
    const schema = withoutReachRoles(schemaForKeys(scope.options_schema, keys));
    const draft = { ...scope.options, ...(this.drafts[module.pack] ?? {}) };
    const isDirty = this.dirty[module.pack] === true;
    const busy = this.busy === `options:${module.pack}`;
    return html`<div class="card">
      <div class="row spread wrap">
        <div class="stack">
          <h3>${module.name || module.pack}</h3>
          <p class="muted small">
            ${module.house
              ? html`<span class="chip">the house</span>`
              : html`<a
                  href="#"
                  @click=${(event: Event) => {
                    event.preventDefault();
                    this.openRoom(module.room_id);
                  }}
                  >${module.room_id}</a
                >`}
            &middot; v${module.version}
            ${module.satisfiable
              ? null
              : html`<span class="chip warn"
                  >needs ${module.missing_slots.join(", ")}</span
                >`}
          </p>
        </div>
        ${this.admin
          ? html`<div class="row">
              <button
                type="button"
                class="icon"
                ?disabled=${this.busy !== null ||
                (!module.enabled && !module.satisfiable)}
                title=${!module.enabled && !module.satisfiable
                  ? `Bind ${module.missing_slots.join(", ")} on a room's settings first.`
                  : ""}
                @click=${() =>
                  void this.act(module.pack, () =>
                    this.requireClient().setModuleEnabled(
                      module.room_id,
                      module.pack,
                      !module.enabled,
                    ),
                  )}
              >
                ${module.enabled ? "Disable" : "Enable"}
              </button>
              <button
                type="button"
                class="icon danger"
                ?disabled=${this.busy !== null}
                @click=${() =>
                  void this.act(module.pack, () =>
                    this.requireClient().uninstallModule(
                      module.room_id,
                      module.pack,
                    ),
                  )}
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
                      ?disabled=${this.busy !== null || !ready || !this.admin}
                      title=${!ready
                        ? `Bind ${module.missing_slots.join(", ")} on a room's settings first.`
                        : ""}
                      @change=${() =>
                        void this.act(`${module.pack}:${behaviour.id}`, () =>
                          this.requireClient().setModuleBehaviourEnabled(
                            module.room_id,
                            module.pack,
                            behaviour.id,
                            !behaviour.enabled,
                          ),
                        )}
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
                      ?disabled=${busy}
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
                      ?disabled=${this.busy !== null}
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
   * Settings the page's schema carries that no listed module claims.
   *
   * Drawn rather than dropped. A key with no card would be a setting the engine
   * reads and the screen never showed -- a person could not set it, could not
   * see it, and would have to take the behaviour's word for what it was set to.
   * It is rare, and being rare is why it needs a place to go rather than being
   * the assumption the whole screen rests on.
   */
  private renderUnclaimed(scope: HouseScope, claimed: Set<string>): TemplateResult | typeof nothing {
    const leftovers = fieldOrder(scope.options_schema ?? {}).filter(
      (key) => !claimed.has(key),
    );
    if (leftovers.length === 0) return nothing;
    const schema = schemaForKeys(scope.options_schema, leftovers);
    if (schema === null) return nothing;
    const draft = { ...scope.options, ...(this.drafts[""] ?? {}) };
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
                ?disabled=${this.busy !== null}
                @click=${() => void this.saveModule("")}
              >
                Save
              </button>
            </div>`
          : null}
      </div>
      <p class="help">
        Settings the house's form resolves that belong to no module on this page
        -- a module whose atoms reach the house from a room that is not listed
        here, or a pack that has since been removed.
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
   * One global slot: the role, the device bound to it, and what acts through it.
   *
   * The modules are chips under the slot's name rather than a column, because
   * they answer the question the row raises -- "what is this for?" -- and a
   * person reading "Door contact" needs "the bedtime button shuts them" beside
   * it. The rooms are the muted footnote: they are what the global binding
   * stands in front of, not what the row is.
   */
  private renderSlot(slot: HouseSlot): TemplateResult {
    return html`<tr>
      <td>
        <div class="stack">
          <span
            >${slot.label}
            ${slot.required
              ? html`<span class="chip">required</span>`
              : html`<span class="chip">optional</span>`}</span
          >
          ${slot.modules.length === 0
            ? null
            : html`<span class="row wrap"
                >${slot.modules.map(
                  (name) => html`<span class="chip">${name}</span>`,
                )}</span
              >`}
        </div>
      </td>
      <td>
        ${slot.entity_id
          ? html`<div class="stack">
              <span>${slot.friendly_name ?? slot.entity_id}</span>
              <span class="muted small">${slot.entity_id}</span>
              ${slot.rooms.length === 0
                ? null
                : html`<span class="muted small"
                    >also bound in ${slot.rooms.join(", ")}</span
                  >`}
            </div>`
          : html`<span class="muted"
              >Nothing bound${slot.rooms.length === 0
                ? ""
                : html` — the rooms use their own`}</span
            >`}
      </td>
      <td>${slot.state ?? html`<span class="muted">-</span>`}</td>
      <td>
        <span class="chip ${STATUS_CHIP[slot.status]}"
          >${STATUS_LABEL[slot.status]}</span
        >
      </td>
      <td>
        ${this.admin
          ? html`<div class="row">
              ${slot.entity_id
                ? html`<button
                      type="button"
                      class="icon"
                      @click=${() => void this.openPicker(slot.slot, true)}
                      >Replace</button
                    >
                    <button
                      type="button"
                      class="icon"
                      @click=${() =>
                        void this.act(`binding:${slot.slot}`, () =>
                          this.requireClient().unbind(HOUSE_ID, slot.slot),
                        )}
                      >Unbind</button
                    >`
                : html`<button
                    type="button"
                    class="icon primary"
                    @click=${() => void this.openPicker(slot.slot, false)}
                    >Bind</button
                  >`}
            </div>`
          : null}
      </td>
    </tr>`;
  }

  /**
   * The device picker, drawn under the table like a room's.
   *
   * `renderSlots` draws it inside the card so it cannot be shown for a slot the
   * card is not listing -- the two are the same subject.
   */
  private renderPicker(scope: HouseScope): TemplateResult {
    const picker = this.picker;
    if (!picker) return html``;
    const slot = scope.slots.find((entry) => entry.slot === picker.slot);
    const domains = (slot?.accepts_domains ?? []).join(", ") || "any device";
    return html`<div class="banner info" role="group" aria-label="Choose a device">
      <div class="row spread wrap">
        <strong>
          ${picker.replace ? "Replace" : "Bind"} the ${slot?.label ?? picker.slot}
          slot
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
        Binding the whole house's ${slot?.label ?? picker.slot} &middot; accepts
        ${domains}
      </p>
      ${picker.loading
        ? html`<p class="muted">Looking for devices...</p>`
        : picker.candidates.length === 0
          ? html`<p class="muted">
              No match. This slot accepts ${domains}.
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
                    ?disabled=${this.busy !== null}
                    @click=${() => void this.choose(candidate)}
                  >
                    Use this
                  </button>
                </div>`,
              )}
            </div>`}
    </div>`;
  }

  /**
   * The house's modules, one card each: what it does and how it is set, together.
   *
   * Two lists became one here. A module's behaviours and its settings are the
   * same subject -- "the fridge has been open too long" is a behaviour, and how
   * long "too long" is is one of its settings -- and a screen that drew the
   * switches in a table and the numbers in a form elsewhere left a person to
   * match them up by guessing which "Door left open for" went with which chip.
   * The card is the module; everything the module owns is inside it.
   *
   * A card is drawn for every module that acts on a role the whole house reads,
   * wherever it was installed, and for every module put into the house itself: a
   * bedtime button sitting in a bedroom belongs on this page, because reaching
   * the house's doors is what it is for. Whether the engine decides it once for
   * the house or once per room is then the module's own per-atom setting.
   */
  private renderModules(scope: HouseScope): TemplateResult {
    const claimed = new Set<string>();
    const cards = scope.modules.map((module) => {
      for (const key of module.option_keys ?? []) claimed.add(key);
      return this.renderModuleCard(module, scope);
    });
    return html`<h2 style="margin-bottom:8px">Modules</h2>
      <p class="help">
        Every module that acts on the whole house, and every module installed
        into the house itself. Each one's switches and its settings are here
        together, because they are the same thing: the behaviour is what it does
        and the settings are how it does it.
      </p>
      ${scope.modules.length === 0
        ? html`<div class="card">
            <p class="muted">No module acts on the whole house yet.</p>
          </div>`
        : html`<div class="stack">${cards}</div>`}
      ${this.renderUnclaimed(scope, claimed)}`;
  }

  /**
   * The atom's reach, beside its switch: a dropdown of tickable rooms.
   *
   * The same control the Modules tab and the room's page draw, because the
   * question is the same one: where does this apply. On this page "the whole
   * house" is the usual answer and a room is it narrowed, which is the same
   * setting read from the other end -- and a person who wants the atom in two
   * rooms says so by ticking two boxes.
   */
  private renderReach(
    module: InstalledModule,
    behaviour: InstalledModule["behaviours"][number],
  ): TemplateResult {
    return reachControl({
      module,
      behaviour,
      rooms: this.rooms,
      disabled: this.busy !== null,
      onToggle: (place, on) => void this.setReach(module, behaviour, place, on),
    });
  }

  /**
   * Tick or untick one place the atom applies to.
   *
   * The whole house is the atom widened (`scope: "house"`), one setting on the
   * atom. A room is the atom's own enable flag in that room: a room-scoped atom
   * is evaluated in every room and runs in the ones its flag is on in, so a
   * person who wants it in two rooms ticks two boxes and the module stays one.
   */
  private async setReach(
    module: InstalledModule,
    behaviour: InstalledModule["behaviours"][number],
    place: string,
    on: boolean,
  ): Promise<void> {
    const client = this.requireClient();
    await this.act(`${module.pack}:${behaviour.id}:scope`, async () => {
      if (place === HOUSE_REACH) {
        await client.setModuleBehaviourScope(
          module.room_id,
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
    });
  }

  private openRoom(roomId: string): void {
    this.dispatchEvent(
      new CustomEvent("navigate", {
        detail: { tab: "rooms", roomId },
        bubbles: true,
        composed: true,
      }),
    );
  }
}

if (!customElements.get("open-house-tab-house")) {
  customElements.define("open-house-tab-house", HouseTab);
}
