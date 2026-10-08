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
import { repeat } from "lit/directives/repeat.js";
import { OpenHouseElement, formatRelative } from "../base.ts";
import { REFUSALS } from "../api/protocol.ts";
// Registered by the import: the notice this page shows once the house has moved
// under it, and the only way back from there.
import "../components/page-stale.ts";
import { HOUSE_REACH, reachControl } from "../components/reach.ts";
import { priorityControl } from "../components/priority.ts";
import { suppressionBanner } from "../components/suppression.ts";
import { STATUS_CHIP, STATUS_LABEL } from "../components/binding-status.ts";
// The parts block, shared with the House tab: the record is the house's, so the
// two pages draw one control over one fact.
import { slotParts } from "../components/slot-parts.ts";
import {
  devicePicker,
  slotDevices,
  type ModuleSlotPickerState,
  type SlotRuleDraft,
} from "../components/slot-devices.ts";
import {
  fieldOrder,
  schemaForKeys,
  withoutReachRoles,
} from "../components/schema-spec.ts";
// The card a module out of the house's own store is drawn with, in this room.
// The same element the Dev tab draws, because it is the same thing: a module
// this house hosts, with its settings and its outputs.
import "../components/hosted-module.ts";
import type {
  AxisRef,
  BindingStatus,
  GlobalBinding,
  HostedModule,
  InstalledModule,
  ModuleSlot,
  RoomDetail,
  RoomSummary,
  SlotPart,
} from "../api/models.ts";

interface PickerState {
  slot: string;
  /** True when this picker is replacing an existing binding. */
  replace: boolean;
  /** Whether the row being rebound is a *house* slot rather than the room's. */
  house: boolean;
  /**
   * The entities this slot may be filled with here: the room's area, or the
   * whole house for a global slot.
   *
   * Read from the server's own picker (`open_house/rooms/candidates`), which is
   * the rule that decides what belongs to a room -- and handed to Home
   * Assistant's entity selector as `include_entities`, because the selector has
   * no area filter of its own. The list is the *scope*; the domains are the
   * type, and those are what the selector is built with.
   */
  includes: string[];
  /** The domains the slot accepts, from the catalog. */
  accepts: string[];
  loading: boolean;
}

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
    slotPicker: { state: true },
    drafts: { state: true },
    dirty: { state: true },
    addingModule: { state: true },
    renaming: { state: true },
    renameValue: { state: true },
    rooms: { state: true },
    hosted: { state: true },
    // The house this page was read at, and whether it has since been told that
    // house has moved. Both are clicks-or-fetches: the page stops being live
    // because of one of them, and a plain field would leave the popup undrawn.
    revision: { state: true },
    stale: { state: true },
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
  /**
   * The house's own modules placed in this room, which are not packs.
   *
   * A module defined in the Dev tab and added here is hosted by the house, not
   * installed from a pack, so it is nowhere in `room.modules` -- and a module a
   * person added that then had no card and no Remove button would be a module
   * they could put into a room and never get out. `modulesHosted` answers the
   * whole house's, so this page keeps the rows whose placement is this room.
   */
  private hosted: HostedModule[] = [];
  private isLoading = true;
  private error: ReturnType<OpenHouseElement["toError"]> | null = null;
  private busy = false;
  private picker: PickerState | null = null;
  private slotPicker: ModuleSlotPickerState | null = null;
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
  /**
   * The rule being written on a slot row, keyed `pack:slot`.
   *
   * Keyed by both because a slot's rule is per *module*, not per slot: two
   * modules in one room may hold different rules on the same slot, and a draft
   * keyed by slot alone would open one module's card showing another's.
   *
   * An entry present means the row is *open* -- the person has picked a kind and
   * the fields for it are showing. Removing it closes the row, which is what
   * "Not now" does, and is a separate act from clearing a saved rule.
   */
  private ruleDrafts: Record<string, SlotRuleDraft> = {};
  /** The slot rules a write is in flight for, keyed the same way. */
  private ruleBusy: Record<string, boolean> = {};
  /** What the server refused, per row, so the sentence shows under its own card. */
  private ruleFailed: Record<string, string> = {};
  /**
   * The part names being typed, keyed by what is being named.
   *
   * `slot` for a new part of a slot, and `slot/part` for a rename of one. Here
   * rather than in the row because a row is re-drawn on every `requestUpdate`,
   * and a name half-typed in a control that was rebuilt is a name a person has to
   * type twice. An entry present means the input is showing, the same "the row is
   * open" rule `ruleDrafts` follows.
   */
  private partDrafts: Record<string, string> = {};
  private addingModule = false;
  private renaming = false;
  private renameValue = "";
  /**
   * The house's profile revision this page's data was read at.
   *
   * Sent with every write the page makes. The house profiles moving -- a switch
   * in another tab, or from the Rooms tab this page was opened from -- make
   * everything on this page an answer to a house that is no longer there, and
   * the server refuses the write rather than let it land over the profile that
   * is (`REFUSALS.stalePage`). What the page does then is stop being live; see
   * `goStale`.
   */
  private revision = 0;
  /** Whether the page has stopped being the live page. */
  private stale = false;

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
      // Read separately from the room's own answer, and its fault swallowed: a
      // house that cannot list its own modules still has rooms, devices and
      // bindings, and a page that went blank for that reason would hide the
      // half of itself that works.
      this.hosted = await client
        .modulesHosted()
        .then((reply) =>
          reply.modules.filter((module) => module.room_id === this.roomId),
        )
        .catch(() => [] as HostedModule[]);
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
    // The house that this page is now showing. Every write below sends it back,
    // and it arrives with each answer because each answer *is* a fresh reading
    // of the house -- one the page has just been brought into agreement with.
    this.revision = room.revision;
    // A reload is the server's answer, so every draft is discarded: keeping an
    // edit through a read that has just told us the stored value would show a
    // form disagreeing with the engine.
    this.drafts = {};
    this.dirty = {};
  }

  /**
   * Stop being the live page: the house moved and everything here is old.
   *
   * Called when the server refuses a write with `stale_page`, and by the card
   * that got the refusal and told us instead (`page-stale`). The page is not
   * wrong -- it is what the house looked like -- but nothing on it may be acted
   * on, because every write would be refused for the same reason and a person
   * would read the refusal as the panel being broken. `render` draws the notice
   * and makes the page inert; the only way back is a reload.
   */
  private goStale(): void {
    this.stale = true;
    this.picker = null;
    this.slotPicker = null;
    this.requestUpdate();
  }

  private async mutate(operation: () => Promise<RoomDetail>): Promise<void> {
    this.busy = true;
    this.error = null;
    this.requestUpdate();
    try {
      this.apply(await operation());
      this.picker = null;
    } catch (error) {
      if (this.toError(error).code === REFUSALS.stalePage) this.goStale();
      else this.error = this.toError(error);
    } finally {
      this.busy = false;
      this.requestUpdate();
    }
  }

  /**
   * Change the *house's* own binding from this room's page, and redraw the room.
   *
   * A global slot is written with the house as the room (`room_id: ""`), and the
   * reply to such a write is the House tab's answer rather than a room's -- so
   * this is a reload and not an `apply`. Two things moved, not one: the slot the
   * person just set, and every room that was falling back to it, which is exactly
   * the "Whole house" section's own row on this page.
   */
  private async mutateHouse(operation: () => Promise<unknown>): Promise<void> {
    this.busy = true;
    this.error = null;
    this.requestUpdate();
    try {
      await operation();
      this.picker = null;
      await this.load();
    } catch (error) {
      if (this.toError(error).code === REFUSALS.stalePage) this.goStale();
      else this.error = this.toError(error);
    } finally {
      this.busy = false;
      this.requestUpdate();
    }
  }

  /**
   * Open the device picker for one of the room's slots, or for a global one.
   *
   * The scoped entity set is the server's answer and it is fetched, not derived:
   * which entities belong to a room is a fact about Home Assistant's area
   * registry, and the panel has never held one. What the panel does with it is
   * pass it to Home Assistant's entity selector, which draws the control.
   *
   * `house` is what makes this the *house's* binding rather than the room's: a
   * global slot is written to the house and answers in every room, so the write
   * `choose` makes goes to `HOUSE` and the candidate set is the whole house's.
   */
  private async openPicker(
    slot: string,
    replace: boolean,
    house = false,
  ): Promise<void> {
    const accepts = this.slotRow(slot, house)?.accepts_domains ?? [];
    this.picker = { slot, replace, house, accepts, includes: [], loading: true };
    this.requestUpdate();
    try {
      const candidates = await this.requireClient().candidates(
        house ? "" : this.roomId,
        slot,
      );
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

  private async choose(entityId: string): Promise<void> {
    const picker = this.picker;
    if (!picker) return;
    // The house is the empty room id (`HOUSE_REACH`), which is the sentinel
    // `open_house/rooms/bind` has always read as "the house's own binding".
    const client = this.requireClient();
    // The house is the empty room id (`HOUSE_REACH`), which is the sentinel
    // `open_house/rooms/bind` has always read as "the house's own binding".
    if (picker.house) {
      await this.mutateHouse(() =>
        picker.replace
          ? client.replace(HOUSE_REACH, picker.slot, entityId, this.revision)
          : client.bind(HOUSE_REACH, picker.slot, entityId, this.revision),
      );
      return;
    }
    await this.mutate(() =>
      picker.replace
        ? client.replace(this.roomId, picker.slot, entityId, this.revision)
        : client.bind(this.roomId, picker.slot, entityId, this.revision),
    );
  }

  /**
   * Open the device picker for one module's own slot.
   *
   * The candidate room is the module's placement: a module put in the house may
   * be aimed at any entity the house holds, and one in a room at the entities
   * filed under that room's area. That is the same rule the room's own picker
   * follows, and the server applies it (`open_house/rooms/candidates`).
   */
  private async openSlotPicker(
    module: InstalledModule,
    slot: ModuleSlot,
  ): Promise<void> {
    this.slotPicker = {
      pack: module.pack,
      roomId: module.room_id,
      slot: slot.slot,
      overridden: slot.overridden,
      accepts: slot.accepts_domains ?? [],
      includes: [],
      loading: true,
    };
    this.requestUpdate();
    try {
      const candidates = await this.requireClient().candidates(
        module.room_id,
        slot.slot,
      );
      if (this.slotPicker && this.slotPicker.slot === slot.slot) {
        this.slotPicker = {
          ...this.slotPicker,
          includes: candidates.map((candidate) => candidate.entity_id),
          loading: false,
        };
      }
    } catch (error) {
      this.error = this.toError(error);
      this.slotPicker = null;
    } finally {
      this.requestUpdate();
    }
  }

  /**
   * Point one module's slot at a device, and leave the room's binding alone.
   *
   * The reply is the whole module, so the card redraws from what the server
   * recorded -- the room page reloads rather than patching the one row, for the
   * reason `ModulesTab.setPriority` gives: the module's chips and its
   * `overridden` flags are the server's answers and recomputing them here would
   * be a second copy of the server's rule.
   */
  private async chooseSlot(entityId: string): Promise<void> {
    const picker = this.slotPicker;
    if (!picker) return;
    await this.writeSlot(picker.pack, picker.slot, entityId, undefined);
  }

  /**
   * Put one module's slot back: the room's device, and the slot's own name.
   *
   * Both halves, because "Reset" means the slot as the room has it -- a reset
   * that cleared the device and left a private name would leave the row still
   * marked "this module only" with a Reset still offered, which reads as the
   * click having failed. The picker's own "Nothing here" is the narrower act:
   * it clears the device and leaves a name alone, because a name is not what
   * the picker is choosing.
   */
  private async resetSlot(pack: string, slot: ModuleSlot): Promise<void> {
    await this.writeSlot(pack, slot.slot, null, null);
  }

  private async renameSlot(
    pack: string,
    slot: ModuleSlot,
    label: string,
  ): Promise<void> {
    await this.writeSlot(pack, slot.slot, undefined, label);
  }

  /**
   * Put one module on a part of a split slot, or back on the whole slot.
   *
   * The third thing a module's slot row says, beside the device and the name: on
   * part `a` of `light_group`, the module acts on the one device the room bound
   * for `a` -- so two modules on one part share it, which is what a person
   * splitting a role is asking for.
   *
   * The device and the name are left alone (`undefined`): changing which half of
   * a role a module is on says nothing about the device it was pointed at.
   */
  private async partSlot(
    pack: string,
    slot: ModuleSlot,
    part: string,
  ): Promise<void> {
    await this.writeSlot(pack, slot.slot, undefined, undefined, part);
  }

  /**
   * Split a slot in two, rename one half, or take one away.
   *
   * Three actions behind one command and one reload, because all three edit the
   * *house's* vocabulary rather than a room's binding: a part binds under a key
   * of its own (`light_group__a`) and the binding layer refuses a name the
   * vocabulary does not carry, so adding one is a change to what the house can
   * hold -- which is why this is `mutateHouse` and not `mutate`.
   *
   * A *removal* is refused while a module still names the part, and the refusal
   * names the modules: everything on a part acts on the one device bound for it,
   * so taking it away would move them all without saying so. The sentence is the
   * server's and is shown where every other refusal on this page is.
   */
  private async setPart(
    slot: string,
    action: "add" | "rename" | "remove",
    name: string,
    newName = "",
  ): Promise<void> {
    await this.mutateHouse(async () => {
      await this.requireClient().setSlotParts(slot, action, name, newName);
      this.partDrafts = Object.fromEntries(
        Object.entries(this.partDrafts).filter(([key]) => !key.startsWith(slot)),
      );
    });
  }

  /** Write one draft, and open or close the control it belongs to. */
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
   * One write to one module's slot, with the halves the caller is not changing
   * left as they were.
   *
   * The command takes both the entity and the name, so a caller changing one
   * must state the other's current value -- the schema requires both keys. An
   * `undefined` means "leave this half alone", and it is resolved from the
   * module the page already holds: a reset sends `entity_id: null` and the
   * module's own name, a rename sends the entity it is pointed at and the new
   * name. Neither is a guess about what the server holds, because the module
   * came from the server on the last read.
   *
   * `part` is the third half and is *not* resolved the same way: `undefined`
   * sends `null`, which the server reads as "not touched" rather than as "the
   * whole slot". A person resetting the device has not asked for the module to be
   * taken off its part, and there is a blank option in the part control for a
   * person who does mean it.
   */
  private async writeSlot(
    pack: string,
    slot: string,
    entityId: string | null | undefined,
    label: string | null | undefined,
    part?: string,
  ): Promise<void> {
    const module = this.room?.modules.find((entry) => entry.pack === pack);
    const row = module?.slots.find((entry) => entry.slot === slot);
    if (!module || !row) return;
    this.busy = true;
    this.error = null;
    this.requestUpdate();
    try {
      await this.requireClient().setModuleSlot(
        module.room_id,
        pack,
        slot,
        entityId === undefined ? (row.overridden ? row.entity_id : null) : entityId,
        label === undefined ? (row.named ? row.label : null) : label,
        part ?? null,
      );
      this.slotPicker = null;
      await this.load();
    } catch (error) {
      this.error = this.toError(error);
    } finally {
      this.busy = false;
      this.requestUpdate();
    }
  }

  /** The draft key for one module's slot: the rule is per module, per slot. */
  private ruleKey(pack: string, slot: string): string {
    return `${pack}:${slot}`;
  }

  /** Hold a rule draft while it is being written, and open or close the row. */
  private editRule(pack: string, slot: ModuleSlot, draft: SlotRuleDraft): void {
    const key = this.ruleKey(pack, slot.slot);
    if (draft.kind === "") {
      // No kind is "nothing is being written here", which closes the row rather
      // than recording an empty draft: the difference between a row a person has
      // opened and one they have not is whether the fields are showing, and an
      // empty draft left behind would open every row it had ever been used on.
      const { [key]: _closed, ...rest } = this.ruleDrafts;
      this.ruleDrafts = rest;
    } else {
      this.ruleDrafts = { ...this.ruleDrafts, [key]: draft };
    }
    this.ruleFailed = { ...this.ruleFailed, [key]: "" };
    this.requestUpdate();
  }

  /**
   * Put a rule on one module's slot, or take the one that is there off it.
   *
   * `setModuleSlotRule` takes the whole rule at once and the server refuses a
   * half-written one in its own words -- a condition that gates no device, a
   * script with nothing to call it, a flow with no entity yet -- so the refusal
   * is shown under the row it belongs to rather than at the top of the page: it
   * is about *this* rule, and a page-level banner would leave a person hunting
   * for which of six slot rows it meant.
   *
   * Clearing sends an empty kind, which is the one spelling of "no rule": the
   * server reads the absence of a kind as the absence of a rule, so a clear and a
   * never-set row are the same state rather than two.
   */
  private async setRule(
    pack: string,
    slot: ModuleSlot,
    rule: SlotRuleDraft | null,
  ): Promise<void> {
    const module = this.room?.modules.find((entry) => entry.pack === pack);
    if (!module) return;
    const key = this.ruleKey(pack, slot.slot);
    this.ruleBusy = { ...this.ruleBusy, [key]: true };
    this.ruleFailed = { ...this.ruleFailed, [key]: "" };
    try {
      await this.requireClient().setModuleSlotRule(module.room_id, pack, slot.slot, {
        kind: rule?.kind ?? "",
        value: rule?.value ?? null,
        when: rule?.when ?? [],
        device: rule?.device ?? null,
      });
      // The row closes on a successful write, because what it was showing is now
      // the saved rule and the row draws that instead -- leaving the editor open
      // would put the same rule on the screen twice, once as a draft and once as
      // the sentence under it.
      const { [key]: _done, ...rest } = this.ruleDrafts;
      this.ruleDrafts = rest;
      await this.load();
    } catch (error) {
      this.ruleFailed = { ...this.ruleFailed, [key]: this.toError(error).message };
    } finally {
      this.ruleBusy = { ...this.ruleBusy, [key]: false };
      this.requestUpdate();
    }
  }

  /**
   * Redraw after a rule was detached into a module of its own.
   *
   * The page is read again rather than patched, for the reason `chooseSlot`
   * gives: a detach makes a *module* and points the slot at what it publishes, so
   * the room's module list, the card and the slot row all move at once.
   */
  private async detached(): Promise<void> {
    await this.load();
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
      await this.requireClient().setRoomOptions(this.roomId, values, this.revision);
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
    // **Inert when stale**, which is not a styling choice: the page is what the
    // house looked like, every write from it would be refused, and a person
    // clicking a control that silently does nothing reads the panel as broken.
    // Nothing behind the notice may be reached by pointer or by keyboard.
    return html`
      <div ?inert=${this.stale} @page-stale=${() => this.goStale()}>
        ${this.errorBanner(this.error)} ${this.renderHeader(room)}
        ${this.renderGlobalBindings(room)} ${this.renderProfiles(room)}
        ${this.renderBindings(room)} ${this.renderModules(room)}
        ${this.renderHosted()}
        <open-house-add-module
          .client=${this.client}
          .roomId=${this.roomId}
          .open=${this.addingModule}
          @add-module-closed=${() => {
            this.addingModule = false;
          }}
          @module-installed=${() => void this.load()}
        ></open-house-add-module>
      </div>
      <open-house-page-stale
        .open=${this.stale}
        .reason=${`This is the ${room.name} page.`}
      ></open-house-page-stale>
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
      ${this.picker && !this.picker.house ? this.renderPicker() : null}
    </div>`;
  }

  /**
   * The house's own global slots this room's modules act through.
   *
   * A room's page is where a person is standing when they think "this room's
   * lights", so it is also where they have to be able to see -- and set -- the
   * role the *house* answers, which every room without an answer of its own falls
   * back to. Written here as the house's binding (`HOUSE_REACH`), because that is
   * what it is: one device standing in for the role everywhere.
   *
   * The "This room" column is the fact a person needs before wondering why their
   * lights did not move: a room that bound the role itself answers for itself,
   * and the global binding stands *behind* it rather than over it. Which is also
   * the reason this section is not the room's binding table with more rows in it.
   */
  private renderGlobalBindings(room: RoomDetail): TemplateResult | typeof nothing {
    if (room.global_bindings.length === 0) return nothing;
    return html`<div class="card">
      <h2>Whole house</h2>
      <p class="help">
        Roles the whole house answers. A room that binds the role itself keeps its
        own device; every other room uses what is set here.
      </p>
      <table>
        <thead>
          <tr>
            <th>Slot</th>
            <th>Device</th>
            <th>Status</th>
            <th>This room</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          ${room.global_bindings.map((row) => this.renderGlobalBinding(row))}
        </tbody>
      </table>
      ${this.picker && this.picker.house ? this.renderPicker() : null}
    </div>`;
  }

  private renderGlobalBinding(binding: GlobalBinding): TemplateResult {
    return html`<tr data-house-slot=${binding.slot}>
      <td>
        <div class="stack">
          <span
            >${binding.label}
            ${binding.required
              ? html`<span class="chip">required</span>`
              : nothing}</span
          >
          ${binding.modules.length === 0
            ? null
            : html`<span class="row wrap"
                >${binding.modules.map(
                  (name) => html`<span class="chip">${name}</span>`,
                )}</span
              >`}
        </div>
      </td>
      <td>
        ${binding.entity_id
          ? html`<div class="stack">
              <span>${binding.friendly_name ?? binding.entity_id}</span>
              <span class="muted small">${binding.entity_id}</span>
            </div>`
          : html`<span class="muted">The house binds nothing</span>`}
      </td>
      <td>
        <span class="chip ${STATUS_CHIP[binding.status]}"
          >${STATUS_LABEL[binding.status]}</span
        >
      </td>
      <td>
        ${binding.overridden
          ? html`<span class="muted small"
              >Its own: <code>${binding.room_entity_id}</code></span
            >`
          : html`<span class="muted small">Uses the house's</span>`}
      </td>
      <td>
        ${this.admin
          ? html`<div class="row">
              ${binding.entity_id
                ? html`<button
                      type="button"
                      class="icon"
                      @click=${() => void this.openPicker(binding.slot, true, true)}
                      >Replace</button
                    >
                    <button
                      type="button"
                      class="icon"
                      @click=${() =>
                        void this.mutateHouse(() =>
                          this.requireClient().unbind(
                            HOUSE_REACH,
                            binding.slot,
                            this.revision,
                          ),
                        )}
                      >Clear</button
                    >`
                : html`<button
                    type="button"
                    class="icon primary"
                    @click=${() => void this.openPicker(binding.slot, false, true)}
                    >Bind for the house</button
                  >`}
            </div>`
          : null}
      </td>
    </tr>`;
  }

  /**
   * The parts one of the room's slots has been split into, and how to change them.
   *
   * The block itself is `components/slot-parts.ts`, shared with the House tab:
   * the record is the *house's* -- a part is a role's half and the split is not a
   * room's to own -- so the two pages draw one control over one fact, and a room's
   * page that could see the halves while the House tab could not would be two
   * answers to one question.
   */
  private renderParts(binding: BindingStatus): TemplateResult {
    return slotParts({
      slot: binding.slot,
      parts: binding.parts ?? [],
      admin: this.admin,
      busy: this.busy,
      draft: (key) => this.partDrafts[key],
      onDraft: (key, value) => this.editPart(key, value),
      onBind: (part) => void this.openPicker(part.slot, part.entity_id !== null),
      onUnbind: (part) => void this.unbindPart(part.slot),
      onAct: (action, name, newName) =>
        void this.setPart(binding.slot, action, name, newName ?? ""),
    });
  }

  /**
   * Take the device off one of the room's parts, leaving the part in place.
   *
   * The way out of the refusal `Delete` gives while a part is bound: a removal is
   * refused rather than silently unbinding, so the page that draws the refusal has
   * to offer the control that clears it. It writes the room's binding for the
   * part's own key -- a part is bound once for a room, which is what makes two
   * modules on one part one device.
   */
  private unbindPart(slot: string): Promise<void> {
    return this.mutate(() =>
      this.requireClient().unbind(this.roomId, slot, this.revision),
    );
  }

  /**
   * One of the room's own slots: the role, the device, and what acts through it.
   *
   * The modules are chips under the slot's name, the same join the House tab
   * draws on a global slot -- "the Light group is a role" and "the bedtime
   * button shuts them" are two facts a person needs together, and a screen that
   * showed the role alone would leave them to guess which module put it there.
   * Both kinds of module are named, because a binding here is what a pack and an
   * imported blueprint both wait for.
   */
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
          ${binding.modules.length === 0
            ? null
            : html`<span class="row wrap"
                >${binding.modules.map(
                  (name) => html`<span class="chip">${name}</span>`,
                )}</span
              >`}
          ${this.renderParts(binding)}
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
                          this.requireClient().unbind(
                            this.roomId,
                            binding.slot,
                            this.revision,
                          ),
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
    return devicePicker({
      heading: `${picker.replace ? "Replace" : "Bind"} the ${
        picker.house ? "whole house's " : ""
      }${this.slotLabel(picker.slot)} slot`,
      accepts: picker.accepts,
      includes: picker.includes,
      first: this.pickerFirst(picker.house),
      value: this.slotValue(picker.slot, picker.house),
      loading: picker.loading,
      disabled: this.busy,
      hass: this.hass,
      onClear: undefined,
      onCancel: () => {
        this.picker = null;
      },
      onChoose: (entityId) => void this.choose(entityId),
    });
  }

  /**
   * The picker opened from a module's own slot row.
   *
   * `onClear` is offered only when the module has an override to clear: a
   * button that says "Nothing here" on a module already taking the room's
   * binding would send a write that changes nothing, and a person would be left
   * unsure whether it had. The `domains` phrase is the room's, because the
   * module's slot is one of the room's own slots and the room's binding table
   * is where the accepted domains are known.
   */
  private renderSlotPicker(): TemplateResult {
    const picker = this.slotPicker;
    if (!picker) return html``;
    return devicePicker({
      heading: `This module's ${this.slotLabel(picker.slot)} slot`,
      accepts: picker.accepts,
      includes: picker.includes,
      // The module's own placement, which is what its candidates were ranked
      // for (`openSlotPicker`): a module put in the house leads with no room,
      // and one in a room leads with that room's own devices -- either way the
      // list is the whole house's, which is what lets a module reach past the
      // room it sits in.
      first: this.pickerFirst(picker.roomId === ""),
      value: this.moduleSlotValue(picker),
      loading: picker.loading,
      disabled: this.busy,
      hass: this.hass,
      onClear: picker.overridden ? () => void this.clearSlot() : undefined,
      onCancel: () => {
        this.slotPicker = null;
      },
      onChoose: (entityId) => void this.chooseSlot(entityId),
    });
  }

  /**
   * The room whose own devices head a picker's list, or `null` for a picker with
   * no room in front of it.
   *
   * The list is the whole house's either way -- a room's picker is a starting
   * point, not a fence (see `views.candidates`) -- so what a room decides is the
   * *order*, and the sentence has to say which devices the person is being shown
   * first. Named rather than derived: the picker cannot tell a room from the
   * house by looking at a list of entity ids, and the page is what knows the
   * room's name.
   */
  private pickerFirst(house: boolean): string | null {
    return house ? null : (this.room?.name ?? "this room");
  }

  /**
   * The row a bind is about, whether it is a slot or a *part* of one.
   *
   * A part is bound under a key of its own (`light_group__a`) and is drawn one
   * level down, inside its slot's row -- so every control that takes a slot key
   * has to look in both places, or a picker opened on a part would find no row,
   * offer no domains and open on nothing.
   */
  private slotRow(
    slot: string,
    house: boolean,
  ): Pick<SlotPart, "slot" | "label" | "entity_id" | "accepts_domains"> | undefined {
    const room = this.room;
    if (!room) return undefined;
    const rows = house ? room.global_bindings : room.bindings;
    const own = rows.find((row) => row.slot === slot);
    if (own) return own;
    for (const row of rows) {
      const part = (row.parts ?? []).find((entry) => entry.slot === slot);
      if (part) return part;
    }
    return undefined;
  }

  /** What one of the room's slots, or the house's, is bound to now. */
  private slotValue(slot: string, house: boolean): string | null {
    return this.slotRow(slot, house)?.entity_id ?? null;
  }

  /** What a module points one of its own slots at, so the control opens there. */
  private moduleSlotValue(picker: ModuleSlotPickerState): string | null {
    const module = this.room?.modules.find((entry) => entry.pack === picker.pack);
    return module?.slots.find((entry) => entry.slot === picker.slot)?.entity_id ??
      null;
  }

  /** Put one module's slot back on the room's binding. */
  private async clearSlot(): Promise<void> {
    const picker = this.slotPicker;
    if (!picker) return;
    await this.writeSlot(picker.pack, picker.slot, null, undefined);
  }

  /** The slot's human name, so the picker says which device it is asking for. */
  private slotLabel(slot: string): string {
    return this.slotRow(slot, false)?.label ?? slot;
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
    // `data-pack` is what makes one card addressable as *this* module's, the
    // same hook the add-module dialog's card carries: a room draws a card per
    // module it holds and the list re-orders as packs are installed and
    // removed, so a reader that counted cards would follow the wrong one.
    return html`<div class="card" data-pack=${module.pack}>
      ${suppressionBanner(module)}
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
                  ${priorityControl({
                    behaviour,
                    disabled: this.busy || !this.admin,
                    onSet: (priority) =>
                      void this.setPriority(module, behaviour, priority),
                    onReset: () =>
                      void this.setPriority(
                        module,
                        behaviour,
                        behaviour.default_priority,
                      ),
                  })}
                </div>
                ${behaviour.description
                  ? html`<p class="help" style="margin:0 0 0 2px">
                      ${behaviour.description}
                    </p>`
                  : null}
              </div>`;
            })}
          </div>`}
      ${slotDevices({
        // `?? []` for the same reason `option_keys` has it: a backend older
        // than this field answers without it, and a module whose devices are
        // unknown is one with no rows to draw, not a page that throws.
        slots: module.slots ?? [],
        house: module.house,
        disabled: this.busy || !this.admin,
        onPick: (slot) => void this.openSlotPicker(module, slot),
        onReset: (slot) => void this.resetSlot(module.pack, slot),
        onRename: (slot, label) => void this.renameSlot(module.pack, slot, label),
        onPart: (slot, part) => void this.partSlot(module.pack, slot, part),
        // "Set it to", on a slot row (`components/slot-devices.ts`): the same
        // four kinds a module's input rows offer, held by the slot. The draft is
        // read from the row the server sent, so a slot that already holds a rule
        // opens showing its kind rather than empty.
        hass: this.hass,
        pack: module.pack,
        moduleTitle: module.name || module.pack,
        roomId: module.room_id,
        roomName: this.room?.name ?? "",
        rule: (slot) => this.ruleDrafts[this.ruleKey(module.pack, slot.slot)] ?? null,
        ruleBusy: (slot) => this.ruleBusy[this.ruleKey(module.pack, slot.slot)] === true,
        ruleFailed: (slot) => this.ruleFailed[this.ruleKey(module.pack, slot.slot)] ?? "",
        onRuleDraft: (slot, draft) => this.editRule(module.pack, slot, draft),
        onRuleSave: (slot, draft) => void this.setRule(module.pack, slot, draft),
        onRuleClear: (slot) => void this.setRule(module.pack, slot, null),
        onDetached: () => void this.detached(),
      })}
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
   * Rank one behaviour: the number that settles it against a rival module.
   *
   * The rank resolves at house scope wherever the module sits -- an entity can be
   * named by units evaluated in different rooms, so a rank that varied per room
   * would order the same two proposals differently depending on which room was
   * asked -- but the control belongs here as much as on the Modules tab, because
   * this is the card a person is looking at when they notice two modules fighting
   * over one light. The room page is reloaded through `mutate`, which is the
   * same path a reach tick takes and the reason the atom's Reset button appears
   * on the row that was just ranked.
   */
  private async setPriority(
    module: InstalledModule,
    behaviour: InstalledModule["behaviours"][number],
    priority: number,
  ): Promise<void> {
    await this.mutate(async () => {
      await this.requireClient().setModuleBehaviourPriority(
        this.roomId,
        module.pack,
        behaviour.id,
        priority,
      );
      return this.requireClient().room(this.roomId);
    });
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
      ${this.slotPicker ? this.renderSlotPicker() : null}
      ${this.renderUnclaimed(room, claimed)}`;
  }

  /**
   * The house's own modules that have been put in this room.
   *
   * A separate list from the one above, because they are a separate thing: a
   * pack is installed into a room and a stored module is *added* to it, and the
   * two live in different records. It is drawn on this page because this page is
   * where a room is looked at -- a module added from the room's dialog and then
   * visible nowhere in that room would be a module a person could put somewhere
   * and not take back. Nothing is drawn when the room has none, since the list
   * above already speaks for the room's modules.
   */
  private renderHosted(): TemplateResult | typeof nothing {
    if (this.hosted.length === 0) return nothing;
    return html`<h2 style="margin-top:24px;margin-bottom:8px">Your modules</h2>
      <p class="help">
        Modules from your store that you have added to this room. Each has its
        own automation and its own copy of the answers you gave when you defined
        it, so editing it here changes it here only.
      </p>
      <div class="stack">
      // Keyed by slug: a rebuild moves a module to the end of the house's own
      // list, and an unkeyed list re-binds the card at that position to whatever
      // module is there now -- taking the notice a save just wrote with it, and
      // re-drawing a module nobody touched.
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
}

if (!customElements.get("open-house-room-settings")) {
  customElements.define("open-house-room-settings", RoomSettings);
}
