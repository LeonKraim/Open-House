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
 * A house slot *is* bound here, and it is the house's own device. A room's role
 * is the collected one (`engine.binding.resolve_slot` gathers it from every room,
 * in room order), so the edit a person makes to a room's "all the lights" is a
 * light in that room; what this page binds is the other, standing thing -- one
 * global device that fills the role for the house scope and for every room that
 * bound none of its own (`HouseSlot`). Only the roles an installed module
 * actually reaches are listed, so every row here has an automation behind it.
 *
 * The house's *options* and its *modules* are not collections, though. A module
 * can be installed into the house (`""` is the house's placement), and that
 * module's settings are the house's, resolving at house scope: they are the
 * controls this tab owns rather than borrows.
 */

import { html, nothing, type TemplateResult } from "lit";
import { repeat } from "lit/directives/repeat.js";
import { OpenHouseElement } from "../base.ts";
import { REFUSALS } from "../api/protocol.ts";
// Registered by the import: the notice this page shows once the house has moved
// under it -- which a profile switch made here does.
import "../components/page-stale.ts";
import { HOUSE_REACH, reachControl } from "../components/reach.ts";
// The card a module out of the house's own store is drawn with, in the house.
// The same element the Dev tab and a room's page draw, because it is the same
// thing: a module this house hosts, with its settings and its outputs.
import "../components/hosted-module.ts";
import "../components/house-profile.ts";
import { devicePicker } from "../components/slot-devices.ts";
// The parts a role has been split into, drawn inside its row. The same block a
// room's page draws, because the parts are one house-level record whichever page
// a person splits them from.
import { slotParts, type SlotPartAction } from "../components/slot-parts.ts";
// The house's own status wording, beside the room and module pages' so the two
// meanings of a collected role's `missing` are visible in one file.
import {
  HOUSE_STATUS_CHIP,
  HOUSE_STATUS_LABEL,
} from "../components/binding-status.ts";
import {
  fieldOrder,
  schemaForKeys,
  withoutReachRoles,
} from "../components/schema-spec.ts";
import type {
  HostedModule,
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
  /**
   * The entities the choice is drawn from: every entity the house holds that
   * the slot accepts, as the server scoped it (`views.candidates`, with the
   * house as the placement). Handed to Home Assistant's entity selector as
   * `include_entities`, because the selector has no area filter of its own and
   * a global slot belongs to no room at all.
   */
  includes: string[];
  /** The domains the slot accepts, from the catalog. */
  accepts: string[];
  loading: boolean;
}

export class HouseTab extends OpenHouseElement {
  static override properties = {
    ...OpenHouseElement.properties,
    scope: { state: true },
    isLoading: { state: true },
    error: { state: true },
    busy: { state: true },
    drafts: { state: true },
    partDrafts: { state: true },
    dirty: { state: true },
    addingModule: { state: true },
    picker: { state: true },
    rooms: { state: true },
    hosted: { state: true },
    // The house this page was read at, and whether it has since been told that
    // house has moved. Reactive, because the page stops being live on a click
    // or a fetch and a plain field would leave the notice undrawn.
    revision: { state: true },
    stale: { state: true },
  };

  private scope: HouseScope | null = null;
  private isLoading = true;
  private error: ReturnType<OpenHouseElement["toError"]> | null = null;
  private busy: string | null = null;
  /**
   * The house's own modules placed in the whole house, which are not packs.
   *
   * The other placement a stored module can be added to, and the reason this
   * list exists here: a module added to the house from the dialog above is
   * hosted by the house rather than installed from a pack, so `scope.modules`
   * knows nothing about it -- and without a card it could be added and never
   * taken out. A room's page draws the ones placed in that room; these are the
   * ones placed in no room.
   */
  private hosted: HostedModule[] = [];
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
  /**
   * The half-typed name of a part, per control of the parts block.
   *
   * Held here rather than in the block for the reason a module's settings are held
   * on this page: the row is rebuilt on every `requestUpdate`, so a name kept in
   * the block would be a name a person types twice. The keys are the block's own
   * (`components/slot-parts.ts`), so this page does not have to know how the two
   * kinds of draft are named.
   */
  private partDrafts: Record<string, string> = {};
  private dirty: Record<string, boolean> = {};
  private addingModule = false;
  private picker: PickerState | null = null;
  /** Every room, so a behaviour's reach control can name where it applies. */
  private rooms: RoomSummary[] = [];
  /**
   * The house's profile revision this page was read at.
   *
   * Sent with every write the page makes, so the server can refuse one that was
   * decided against a profile which has since been replaced. This is also the
   * page that *switches* profiles, so the notice below is not only an error
   * path here: a switch made on this page moves the house out from under it,
   * and the page says so rather than redrawing itself as though the settings it
   * was showing had always been the new profile's.
   */
  private revision = 0;
  /** Whether the page has stopped being the live page. Sticky until a reload. */
  private stale = false;

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
      // Read separately, and its fault swallowed: a house that cannot list its
      // own modules still has slots, devices and packs, and a page that went
      // blank for that reason would hide the half of itself that works.
      this.hosted = await client
        .modulesHosted()
        .then((reply) =>
          reply.modules.filter((module) => module.room_id === ""),
        )
        .catch(() => [] as HostedModule[]);
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
    // The house this page is now showing, sent back with every write below.
    this.revision = scope.revision;
    // A reload is the server's answer, so every draft is discarded: keeping an
    // edit through a read that has just told us the stored value would show a
    // form disagreeing with the engine.
    this.drafts = {};
    this.dirty = {};
    this.partDrafts = {};
    // The picker too: a slot just bound is not one to go on offering devices for.
    this.picker = null;
  }

  /**
   * Stop being the live page: the house moved and everything here is old.
   *
   * The same act a room's page makes (`room-settings.ts`), and for the same
   * reason: a `stale_page` refusal means the values on this page were decided
   * against a profile that no longer applies, and writing them would land them
   * over the profile that does. The page is not wrong -- it is what the house
   * looked like -- but nothing on it may be acted on, so it is made inert and
   * the notice is drawn. Sticky: only a reload puts the page back in agreement
   * with the house.
   */
  private goStale(): void {
    this.stale = true;
    this.picker = null;
    this.requestUpdate();
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
      if (this.toError(error).code === REFUSALS.stalePage) this.goStale();
      else this.error = this.toError(error);
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
      await this.requireClient().setHouseOptions(values, this.revision);
      await this.load();
    } catch (error) {
      if (this.toError(error).code === REFUSALS.stalePage) this.goStale();
      else this.error = this.toError(error);
    } finally {
      this.busy = null;
      this.requestUpdate();
    }
  }

  /**
   * The row a bind is about, whether it is a global slot or a *part* of one.
   *
   * A part is bound under a key of its own (`light_group__a`) and is drawn one
   * level down, inside its slot's row -- so a picker opened on a part has to look
   * in both places, or it would find no row and offer no domains for a half of
   * the lights.
   */
  private slotRow(
    slot: string,
  ): Pick<HouseSlot, "slot" | "label" | "entity_id" | "accepts_domains"> | undefined {
    const rows = this.scope?.slots ?? [];
    const own = rows.find((entry) => entry.slot === slot);
    if (own) return own;
    for (const row of rows) {
      const part = (row.parts ?? []).find((entry) => entry.slot === slot);
      if (part) return part;
    }
    return undefined;
  }

  /**
   * Split a global role, rename one half, or rejoin one.
   *
   * The same three a room's page offers, over the same *house-level* record: a
   * part belongs to no room, so the tab that shows the house's own roles is a
   * natural place to split one. The write goes through the page's ordinary `act`,
   * so a refusal (a part a module still names) is drawn where every other refusal
   * on this page is, and a success redraws the row from what the server holds.
   */
  private setPart(
    slot: string,
    action: SlotPartAction,
    name: string,
    newName = "",
  ): Promise<void> {
    return this.act(`part:${slot}`, async () => {
      await this.requireClient().setSlotParts(slot, action, name, newName);
      // This slot's drafts only: a split, a rename and a rejoin all end with the
      // control that asked for them closed, and a name left in a field the server
      // has already taken is a form disagreeing with the record. Matched as the
      // exact key or a `slot/part` key, never a bare prefix: `light` is the new-
      // part field for the `light` slot and `light_group` is another slot's, and
      // `startsWith("light")` would wipe the second while clearing the first.
      this.partDrafts = Object.fromEntries(
        Object.entries(this.partDrafts).filter(
          ([key]) => key !== slot && !key.startsWith(`${slot}/`),
        ),
      );
    });
  }

  /** Write one part-name draft, and open or close the control it belongs to. */
  private editPart(key: string, value: string | null): void {
    if (value === null) {
      const { [key]: _closed, ...rest } = this.partDrafts;
      this.partDrafts = rest;
    } else {
      this.partDrafts = { ...this.partDrafts, [key]: value };
    }
    this.requestUpdate();
  }

  /**
   * Ask the server which devices could fill one global slot.
   *
   * The same call a room's picker makes, with the house as the placement: the
   * server answers with every entity the house holds that the slot accepts,
   * because a global slot is a role no room owns (`views.candidates`).
   */
  private async openPicker(slot: string, replace: boolean): Promise<void> {
    const accepts = this.slotRow(slot)?.accepts_domains ?? [];
    this.picker = { slot, replace, accepts, includes: [], loading: true };
    this.requestUpdate();
    try {
      const candidates = await this.requireClient().candidates(HOUSE_ID, slot);
      if (this.picker && this.picker.slot === slot) {
        this.picker = {
          ...this.picker,
          includes: candidates.map((candidate) => candidate.entity_id),
          loading: false,
        };
      }
    } catch (error) {
      this.error = this.toError(error);
      this.picker = null;
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
  private async choose(entityId: string): Promise<void> {
    const picker = this.picker;
    if (!picker) return;
    await this.act(`binding:${picker.slot}`, () =>
      picker.replace
        ? this.requireClient().replace(HOUSE_ID, picker.slot, entityId, this.revision)
        : this.requireClient().bind(HOUSE_ID, picker.slot, entityId, this.revision),
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
    // Inert when stale: nothing behind the notice may be reached by pointer or
    // keyboard, because every write from this page would be refused and a
    // control that silently does nothing reads as the panel being broken.
    return html`
      <div ?inert=${this.stale} @page-stale=${() => this.goStale()}>
        ${this.errorBanner(this.error)} ${this.renderHeader(scope)}
        <!-- The whole house's profile, above everything it changes. It is the one
             control whose answer is about the whole house at once, so it is drawn
             before the house's own settings rather than inside any of them -- and
             it is on the Rooms tab too, where a person looking at their house as a
             list of rooms will reach for it. -->
        <open-house-house-profile
          .client=${this.client}
          .hass=${this.hass}
          .admin=${this.admin}
          @profiles-changed=${() => this.goStale()}
        ></open-house-house-profile>
        ${this.renderSlots(scope)} ${this.renderModules(scope)}
        ${this.renderHosted()}
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
      </div>
      <open-house-page-stale
        .open=${this.stale}
        .reason=${`This is the house page.`}
      ></open-house-page-stale>
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
      <p
        class="help"
        title=${"A slot bound here is global: it fills that role for the house and for every room that has not bound one of its own. Only the slots a module reaches are listed, so every row has an automation behind it."}
      >
        The devices the whole house's automations act through.
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
            ${this.picker ? this.renderPicker() : null}`}
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
                  >${this.roomName(module.room_id)}</a
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
      <p
        class="help"
        title=${"A module whose atoms reach the house from a room that is not listed here, or a pack that has since been removed."}
      >
        Settings that belong to no module on this page.
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
          ${this.renderParts(slot)}
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
        <span class="chip ${HOUSE_STATUS_CHIP[slot.status]}"
          >${HOUSE_STATUS_LABEL[slot.status]}</span
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
                          this.requireClient().unbind(
                            HOUSE_ID,
                            slot.slot,
                            this.revision,
                          ),
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
   * The parts this global role has been split into, and how to change them.
   *
   * A global slot is split here for the same reason it is bound here: a role the
   * whole house reads is the house's, and a half of it is the same role. The parts
   * are a house-level record, so a person who splits "Light group" on this page
   * and a person who splits the same role from a room's page are writing the one
   * fact -- the block is the same component on both pages, deliberately.
   *
   * Binding a part goes through the *house's* picker (`openPicker`, the same one
   * the slot itself uses), because a part's device is a house binding under the
   * part's own key -- and taking that device off again is the house's unbind, one
   * row down. Nothing on the block writes: a part is added, renamed or rejoined,
   * and its device bound or cleared, by the same controls a room's page draws.
   */
  private renderParts(slot: HouseSlot): TemplateResult | typeof nothing {
    if (!this.admin && slot.parts.length === 0) return nothing;
    return slotParts({
      slot: slot.slot,
      parts: slot.parts,
      admin: this.admin,
      busy: this.busy !== null,
      draft: (key) => this.partDrafts[key],
      onDraft: (key, value) => this.editPart(key, value),
      onBind: (part) => void this.openPicker(part.slot, part.entity_id !== null),
      onUnbind: (part) =>
        void this.act(`binding:${part.slot}`, () =>
          this.requireClient().unbind(HOUSE_ID, part.slot, this.revision),
        ),
      onAct: (action, name, newName) =>
        void this.setPart(slot.slot, action, name, newName ?? ""),
    });
  }

  /**
   * The device picker, drawn under the table like a room's.
   *
   * `renderSlots` draws it inside the card so it cannot be shown for a slot the
   * card is not listing -- the two are the same subject. The control is Home
   * Assistant's own entity selector, the same one a room's page draws, over the
   * same list -- every entity the house holds (a room's page leads with that
   * room's own, and a global slot has no room to lead with). A global slot is a
   * role like any other; nothing about it is room-shaped.
   *
   * The row is found through `slotRow`, not by name, because a picker may have
   * been opened on one **part** of a split slot (`renderParts`) and that row is
   * drawn inside its parent's rather than beside it.
   */
  private renderPicker(): TemplateResult {
    const picker = this.picker;
    if (!picker) return html``;
    const slot = this.slotRow(picker.slot);
    return devicePicker({
      heading: `${picker.replace ? "Replace" : "Bind"} the ${
        slot?.label ?? picker.slot
      } slot`,
      accepts: picker.accepts,
      includes: picker.includes,
      // No room leads a global slot's list: it belongs to no room, so there is
      // no room whose own devices would be the honest thing to show first.
      first: null,
      value: slot?.entity_id ?? null,
      loading: picker.loading,
      disabled: this.busy !== null,
      hass: this.hass,
      onCancel: () => {
        this.picker = null;
      },
      onChoose: (entityId) => void this.choose(entityId),
    });
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
      <p
        class="help"
        title=${"Every module that acts on the whole house, and every module installed into the house itself. A card's switches and its settings are together because they are the same thing: the behaviour is what it does and the settings are how it does it."}
      >
        Modules acting on the whole house, and those installed into it.
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

  /**
   * The house's own modules, placed in the whole house.
   *
   * A separate list from the one above, because they are a separate thing: a
   * pack is installed into a room and a stored module is *added* to a placement,
   * and the two live in different records. It is drawn here because here is
   * where the "Add module to house" button is -- a module added by that button
   * and then visible nowhere would be one a person could put somewhere and not
   * take back. Nothing is drawn when there are none.
   *
   * The list is keyed by slug: a rebuild moves a module to the end of the house's
   * own list, and an unkeyed list re-binds the card at that position to whatever
   * module is there now -- taking the notice a save just wrote with it, and
   * re-drawing a module nobody touched.
   */
  private renderHosted(): TemplateResult | typeof nothing {
    if (this.hosted.length === 0) return nothing;
    return html`<h2 style="margin-top:24px;margin-bottom:8px">Your modules</h2>
      <p
        class="help"
        title=${"Each module has its own automation and its own copy of the answers you gave when you defined it, so editing it here changes this one only."}
      >
        Modules from your store added to the whole house.
      </p>
      <div class="stack">
        ${repeat(
          this.hosted,
          (module) => module.slug,
          (module) => html`<open-house-hosted-module
            .client=${this.client}
            .hass=${this.hass}
            .module=${module}
            .removable=${this.admin}
            .revision=${this.revision}
            .stale=${this.stale}
            @module-changed=${() => void this.load()}
          ></open-house-hosted-module>`,
        )}
      </div>`;
  }

  /**
   * A room's display name, for the surfaces a module's placement is named on.
   *
   * A module carries the room it sits in by id; every other surface in the panel
   * shows the room's name, so a card that printed the id would be the one place a
   * person reads `living_room` instead of "Living room". Falls back to the id when
   * the room list does not carry it, which is honest -- the module names a room the
   * page's own read did not.
   */
  private roomName(roomId: string): string {
    return this.rooms.find((room) => room.id === roomId)?.name ?? roomId;
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
