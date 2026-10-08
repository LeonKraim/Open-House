/**
 * A module's devices: the slots it acts through, and what each one is pointed at.
 *
 * A room binds one device per slot and every module reaching that slot acts on
 * it -- which is right until it is not. A person wants their lamps shut by one
 * pack and the room's whole group by another; a pack watching an appliance wants
 * *that* appliance's contact rather than the front door's. So each module may
 * point its own slots somewhere else, and name them for itself, without the
 * room's binding moving at all.
 *
 * Two facts per slot, and the control says both:
 *
 *   * **which device** -- the entity the module acts on, with what the house
 *     binds beside it so a person can see what they are departing from, and a
 *     Reset that puts it back. The server sends both (`entity_id`,
 *     `default_entity_id`) and the panel resolves neither: the engine's own
 *     resolution is the answer, and a second copy of it here would be free to
 *     disagree about which device a module writes to.
 *   * **what it is called** -- a text field, committed on change. Blank means
 *     the slot's own name, which is what the field's placeholder shows. The name
 *     is display-only; the key the house resolves the slot under never changes.
 *
 * The device picker itself is not here: it needs the client and the room's
 * candidate list, which belong to the page, so the row asks for one with
 * `onPick` and the page opens it.
 */

import { html, nothing, type TemplateResult } from "lit";
import type { HassLike } from "../api/connection.ts";
import type { ModuleSlot, ModuleSlotRuleKind } from "../api/models.ts";
import { STATUS_CHIP, STATUS_LABEL } from "./binding-status.ts";
import type { DetachedDetail } from "./detach.ts";
import "./detach.ts";

/**
 * The rule one slot row is being given, as the screen holds it while it is written.
 *
 * One shape for four kinds, and the unused fields simply go unread -- the same
 * reason `slot_rules.SlotRule` has one `value`: only one of them is ever the
 * answer, and the kind in hand is what says which. The *draft* rather than the
 * stored rule because a rule is written a field at a time and a half-written one
 * is not something a person should be made to send: a condition with no device is
 * refused by the server ("a condition decides whether the slot's device is used,
 * so it has to be told which device that is"), and the draft is what makes that
 * refusal happen on a button press rather than mid-keystroke.
 *
 * Seeded from the row the server sent, so a slot that already holds a rule opens
 * showing it rather than empty.
 */
export interface SlotRuleDraft {
  kind: ModuleSlotRuleKind | "";
  /** A template's text, or a condition's builder config, or an entity id. */
  value: unknown;
  /** The entities a *script* rule should be called on. */
  when: string[];
  /** The device a *condition* rule gates. */
  device: string;
}

/** A draft holding nothing, which is the state of a slot with no rule. */
export function emptyRuleDraft(): SlotRuleDraft {
  return { kind: "", value: undefined, when: [], device: "" };
}

/**
 * The draft a row opens on, read off the rule the server reported.
 *
 * The payload is not read back out of the row: `_module_slots` reports the rule's
 * *sentence* (`rule_summary`) and the four facts a row branches on, deliberately
 * not the whole authored config -- a condition is a builder's JSON and a template
 * is text, and sending both back on every listing of the house would put a
 * person's logic into every page's payload to answer a question only the one
 * screen that is editing it asks. So this opens the menu on the right kind and
 * leaves the fields for the person to fill: changing a rule means writing it
 * again, which is honest about what the row is.
 */
export function ruleDraftOf(slot: ModuleSlot): SlotRuleDraft {
  const draft = emptyRuleDraft();
  if (!slot.rule_kind) return draft;
  draft.kind = slot.rule_kind;
  draft.device = slot.rule_device ?? "";
  return draft;
}

/**
 * What every caller of a slot row has to say, whether it draws one row or many.
 *
 * Split out because the section and the row take the same dozen answers and a
 * second copy of them is a second place for the two to drift: the row is drawn
 * from the section, so a field the section forgot to pass would be a field the row
 * silently lost.
 */
export interface SlotRowCommon {
  /** Whether the module sits in the whole house, for the words the row uses. */
  house: boolean;
  /** True while a write is in flight, or for a reader who may not write. */
  disabled: boolean;
  onPick: (slot: ModuleSlot) => void;
  onReset: (slot: ModuleSlot) => void;
  onRename: (slot: ModuleSlot, label: string) => void;
  /**
   * Home Assistant itself, for the selectors a rule's fields are drawn with.
   *
   * The same reason the device picker needs it: a condition is Home Assistant's
   * condition editor and an entity field is Home Assistant's entity picker, and
   * neither is ours to draw. Optional so the row still renders for a caller that
   * has no `hass` yet -- it then shows the menu and no fields rather than
   * throwing on a page that has not finished loading.
   */
  hass?: HassLike;
  /** The module the row belongs to, named for the detach dialog. */
  pack?: string;
  /** The module's title, for the detach dialog's suggested name. */
  moduleTitle?: string;
  /** The module's room, and its name: what "this room" means in a detach. */
  roomId?: string;
  roomName?: string;
  /**
   * Put this module on one part of a split slot, or back on the whole slot.
   *
   * The third thing a row says about where the module reaches, beside the device
   * and the name. An empty string is the whole slot, which is where every module
   * stands until somebody splits it.
   */
  onPart?: (slot: ModuleSlot, part: string) => void;
  /**
   * What the row holds while a rule is being written, when one is open.
   *
   * Asked of the slot rather than handed down as one value, because the state is
   * *per row*: the section draws every slot of a module, and a section-level
   * field would open the rule editor on all of them at once. The page is what has
   * the map, so the page is asked.
   */
  rule?: (slot: ModuleSlot) => SlotRuleDraft | null;
  /** Whether a rule write is in flight for this row. */
  ruleBusy?: (slot: ModuleSlot) => boolean;
  /** The server's refusal of the last rule write on this row, or `""`. */
  ruleFailed?: (slot: ModuleSlot) => string;
  onRuleDraft?: (slot: ModuleSlot, draft: SlotRuleDraft) => void;
  onRuleSave?: (slot: ModuleSlot, draft: SlotRuleDraft) => void;
  onRuleClear?: (slot: ModuleSlot) => void;
  /** What a detach of this row's rule did, so the page can redraw the house. */
  onDetached?: (detail: DetachedDetail) => void;
}

export interface SlotDeviceRowOptions extends SlotRowCommon {
  slot: ModuleSlot;
}

/**
 * Where the row says the fallback binding lives: the room's, or the house's.
 *
 * The noun and not the phrase, because every sentence that uses it writes its own
 * article -- "the room's binding" and "the room binds" are different cases of the
 * same word, and a helper returning `the room` gives the page "the the room's
 * binding".
 */
function home(house: boolean): string {
  return house ? "house" : "room";
}

/** One slot of one module: its name, the device, and how to change either. */
export function slotDeviceRow(options: SlotDeviceRowOptions): TemplateResult {
  const { slot, house, disabled, onPick, onReset, onRename } = options;
  const fallback = home(house);
  return html`<div class="stack" style="margin-top:8px">
    <div class="row wrap" style="align-items:center;gap:8px">
      <input
        type="text"
        style="min-width:11rem"
        aria-label=${`What this module calls the ${slot.declared_label} slot`}
        title="A name for this module only. The house still resolves the slot under its own name."
        placeholder=${slot.declared_label}
        .value=${slot.label}
        ?disabled=${disabled}
        @change=${(event: Event) =>
          onRename(slot, (event.target as HTMLInputElement).value)}
      />
      <span class="chip ${STATUS_CHIP[slot.status]}"
        >${STATUS_LABEL[slot.status]}</span
      >
      ${slot.required
        ? html`<span class="chip" title="This module cannot run without it."
            >required</span
          >`
        : nothing}
      ${slot.separate
        ? html`<span class="chip" title="A device of this pack's own."
            >its own</span
          >`
        : nothing}
      ${slot.house_scope
        ? html`<span class="chip" title="A role the whole house resolves."
            >whole house</span
          >`
        : nothing}
      ${slot.part !== null
        ? html`<span
            class="chip"
            data-slot-part-chip=${slot.slot}
            title="This module is on one part of a split slot. Everything on this part acts on the one device the ${fallback} bound for it."
            >the ${slot.part_label ?? slot.part} part</span
          >`
        : nothing}
      ${slot.overridden || slot.named
        ? html`<span
            class="chip"
            title="This module only; the ${fallback} is untouched."
            >this module only</span
          >`
        : nothing}
      <span class="grow"></span>
      ${slot.parts.length === 0 || options.onPart === undefined
        ? nothing
        : html`<label class="muted small" for=${`slot-part-${slot.slot}`}
              >Part</label
            >
            <select
              id=${`slot-part-${slot.slot}`}
              data-slot-part=${slot.slot}
              title="Which part of this split slot the module acts through. Everything on one part acts on one device."
              ?disabled=${disabled}
              .value=${slot.part ?? ""}
              @change=${(event: Event) =>
                options.onPart?.(
                  slot,
                  (event.target as HTMLSelectElement).value,
                )}
            >
              <option value="">The whole slot</option>
              ${slot.parts.map(
                (part) => html`<option value=${part.name}>${part.label}</option>`,
              )}
            </select>`}
      <button
        type="button"
        class="icon"
        ?disabled=${disabled}
        @click=${() => onPick(slot)}
      >
        ${slot.bound ? "Change device" : "Choose device"}
      </button>
      ${slot.overridden || slot.named
        ? html`<button
            type="button"
            class="icon"
            ?disabled=${disabled}
            @click=${() => onReset(slot)}
          >
            Reset
          </button>`
        : nothing}
    </div>
    <p class="muted small" style="margin:0">
      ${slot.bound
        ? html`<span>${slot.friendly_name ?? slot.entity_id}</span>
            <code>${slot.entity_id}</code>
            ${slot.overridden
              ? html`&middot; the ${fallback} binds
                  <code>${slot.default_entity_id ?? "nothing"}</code>`
              : html`&middot; the ${fallback}'s binding`}`
        : html`Nothing is bound or chosen here${slot.required
              ? ", and this module needs it"
              : ""}.`}
    </p>
    ${slotRuleRow(options)}
  </div>`;
}

/**
 * The device is worked out, when a person has put logic on this slot.
 *
 * One sentence under the device line, and it is the server's (`rule_summary`) for
 * the reason every other sentence on a card is the server's: the wording has to
 * agree with what the watcher actually runs. The device line above it already
 * shows the entity the module acts on *now* -- which, for a rule that is being
 * worked out, is something the person never chose and cannot recognise, so a row
 * that said only the device would be showing them a stranger.
 *
 * `rule_picks_device` decides what the sentence *means here*, and it is drawn
 * rather than inferred: `true` for a template, a flow and a script, which produce
 * the entity and so displace the room's binding entirely; `false` for a condition,
 * which decides whether the room's device is used and so leaves it in place the
 * rest of the time.
 */
function slotRuleRow(options: SlotDeviceRowOptions): TemplateResult | typeof nothing {
  const { slot, disabled } = options;
  const editing = options.rule?.(slot) ?? null;
  const busy = disabled || options.ruleBusy?.(slot) === true;
  const fallback = home(options.house);
  const kind: ModuleSlotRuleKind | "" = editing?.kind ?? "";
  const open = kind !== "";
  // `!= null` and not `!== null`: the field is reported by the listing that has
  // a rule to report, and a slot whose rule nothing has said is one with the key
  // absent as much as one with it set to `null`. Read strictly, the absent one is
  // a *rule* -- the chip renders the word "undefined" and the row offers to clear
  // something that is not there.
  const hasRule = slot.rule_kind != null;
  return html`<div class="stack" style="margin-top:6px">
    <div class="row wrap" style="align-items:center;gap:8px">
      <label class="muted small" for=${`slot-rule-${slot.slot}`}>Set it to</label>
      <select
        id=${`slot-rule-${slot.slot}`}
        data-slot-rule=${slot.slot}
        ?disabled=${busy}
        .value=${kind}
        @change=${(event: Event) =>
          options.onRuleDraft?.(
            slot,
            {
              ...(editing ?? emptyRuleDraft()),
              kind: (event.target as HTMLSelectElement)
                .value as ModuleSlotRuleKind | "",
            },
          )}
      >
        <option value="">A device (above)</option>
        <option value="template">A template (an expression)</option>
        <option value="condition">A condition</option>
        <option value="flow">A Node-RED flow</option>
        <option value="script">HAOS script logic</option>
      </select>
      ${hasRule
        ? html`<span class="chip" data-slot-rule-chip=${slot.slot}
            >${slot.rule_kind}</span
          >`
        : nothing}
      ${hasRule && !open
        ? html`<button
            type="button"
            class="icon"
            ?disabled=${busy}
            @click=${() => options.onRuleClear?.(slot)}
          >
            Clear
          </button>`
        : nothing}
    </div>
    ${hasRule
      ? html`<p class="muted small" style="margin:0" data-slot-rule-summary=${slot.slot}>
          ${slot.rule_summary}
          ${slot.rule_picks_device
            ? html`&middot; so the ${fallback}'s binding is not used while this is
                set`
            : html`&middot; and the ${fallback}'s binding is used when it does not
                hold`}
        </p>`
      : nothing}
    ${open ? ruleFields(options, editing) : nothing}
    ${hasRule ? detachButton(options, slot) : nothing}
  </div>`;
}

/**
 * Detach this slot's rule into a module of its own.
 *
 * Beside the rule, and offered on the *rule the server holds* rather than on the
 * menu: the detach reads the logic from the settings and the panel cannot send
 * what it has not saved. A person who has just picked a kind and not pressed
 * "Set it" has a draft and no rule, so the button is exactly where there is
 * something to move out.
 *
 * The dialog is the import screen's own component (`components/detach.ts`), the
 * same one an input's cast detaches through, because the two questions a detach
 * cannot answer for itself -- what to call the module, and where it sits -- are
 * the same two either way. Only the row it names differs, and the slot row names
 * a *slot*.
 */
function detachButton(
  options: SlotDeviceRowOptions,
  slot: ModuleSlot,
): TemplateResult | typeof nothing {
  const { pack, roomId, roomName, moduleTitle } = options;
  if (!pack) return nothing;
  return html`<open-house-detach
    .module=${pack}
    .moduleTitle=${moduleTitle ?? pack}
    .slot=${slot.slot}
    .cast=${slot.rule_kind ?? ""}
    .roomId=${roomId ?? ""}
    .roomName=${roomName ?? ""}
    @detach-done=${(event: CustomEvent<DetachedDetail>) => {
      event.stopPropagation();
      options.onDetached?.(event.detail);
    }}
  ></open-house-detach>`;
}

/**
 * The fields the chosen kind needs, and the two things to do with them.
 *
 * Every kind asks for a different thing, which is the point of the menu rather
 * than four menus: a template is text, a condition is a builder *and* the device
 * it gates, a flow is the entity it writes, a script is a script *and* what should
 * call it. The four are drawn through Home Assistant's own selectors, so an
 * entity field is HA's picker and a condition is HA's condition editor -- the same
 * controls the automation editor uses, and the same ones the module input rows
 * already reach for (`components/casts.ts` decides *which*; the editors are the
 * platform's).
 *
 * **Nothing is written until "Set it".** A rule is written a field at a time and
 * the server refuses a half-written one by design -- a condition that gates no
 * device, a script with nothing to call it -- so writing on every change would
 * put four refusals in front of a person while they typed one sentence. The
 * draft is held on the page and this button is what sends it.
 */
function ruleFields(
  options: SlotDeviceRowOptions,
  draft: SlotRuleDraft | null,
): TemplateResult {
  const { slot, disabled } = options;
  const rule = draft ?? emptyRuleDraft();
  const kind = rule.kind;
  const busy = disabled || options.ruleBusy?.(slot) === true;
  const ruleFailed = options.ruleFailed?.(slot) ?? "";
  const write = (change: Partial<SlotRuleDraft>): void =>
    options.onRuleDraft?.(slot, { ...rule, ...change });
  const schema: Record<string, unknown>[] = [];
  const data: Record<string, unknown> = {};
  if (kind === "template") {
    schema.push({ name: "value", selector: { template: {} } });
    data.value = typeof rule.value === "string" ? rule.value : "";
  }
  if (kind === "condition") {
    schema.push({ name: "value", selector: { condition: {} } });
    if (rule.value != null) data.value = rule.value;
    // The device the condition gates, typed by what the slot accepts -- a
    // condition on a light slot gates a light, and offering the whole house
    // would let a person name something no module could act through.
    schema.push({
      name: "device",
      selector: { entity: { domain: slot.accepts_domains } },
    });
    if (rule.device) data.device = rule.device;
  }
  if (kind === "flow") {
    schema.push({
      name: "value",
      selector: { entity: { domain: ["sensor", "binary_sensor"] } },
    });
    if (typeof rule.value === "string") data.value = rule.value;
  }
  if (kind === "script") {
    schema.push({ name: "value", selector: { entity: { domain: ["script"] } } });
    if (typeof rule.value === "string") data.value = rule.value;
    schema.push({ name: "when", selector: { entity: { multiple: true } } });
    if (rule.when.length) data.when = rule.when;
  }
  return html`<div class="stack" style="margin-top:6px">
    <ha-form
      .hass=${options.hass}
      data-slot-rule-form=${slot.slot}
      .data=${data}
      .schema=${schema}
      .computeLabel=${(item: { name: string }) => {
        if (item.name === "value") {
          if (kind === "condition") return "The condition";
          if (kind === "template") return "The template";
          if (kind === "flow") return "The flow's entity";
          return "The script";
        }
        if (item.name === "device") return "The device it gates";
        return "Call it when any of these change";
      }}
      @value-changed=${(
        event: CustomEvent<{ value: Record<string, unknown> }>,
      ) => {
        const answered = event.detail.value;
        write({
          value: answered.value ?? rule.value,
          when: Array.isArray(answered.when)
            ? (answered.when as string[])
            : rule.when,
          device:
            typeof answered.device === "string" ? answered.device : rule.device,
        });
      }}
    ></ha-form>
    <p class="help" style="margin:0">
      ${kind === "template"
        ? "The template renders to an entity id, and that is the device this module acts on."
        : kind === "condition"
          ? "A condition chooses yes or no, so it decides *whether* this slot uses that device: while it holds the module acts on it, and while it does not the room's own binding is used."
          : kind === "flow"
            ? "The flow writes an entity of its own, and the id it holds is the device. Set this once the flow has been saved."
            : "The script is called whenever one of those entities changes, and the entity id it returns is the device."}
    </p>
    ${ruleFailed
      ? html`<div class="banner error" role="alert" data-slot-rule-error=${slot.slot}>
          ${ruleFailed}
        </div>`
      : nothing}
    <div class="row wrap">
      <button
        type="button"
        class="primary"
        data-slot-rule-save=${slot.slot}
        ?disabled=${busy}
        @click=${() => options.onRuleSave?.(slot, rule)}
      >
        ${options.ruleBusy?.(slot) ? "Setting..." : "Set it"}
      </button>
      <button
        type="button"
        ?disabled=${busy}
        @click=${() => options.onRuleDraft?.(slot, emptyRuleDraft())}
      >
        Not now
      </button>
    </div>
  </div>`;
}

/**
 * The state one module-slot picker holds while it is open.
 *
 * Here rather than in each tab because both tabs draw the same picker: a room's
 * page and the Modules tab ask one question -- which device does *this module*
 * act on for this slot -- and a state shape spelled twice is two places for the
 * fields to drift apart. The page owns the fetching; this is only what it holds.
 */
export interface ModuleSlotPickerState {
  pack: string;
  /**
   * The placement whose devices the candidates lead with: the module's room, or
   * `""` for the house. Any module may be aimed at any entity the house holds;
   * a module in a room just sees that room's own devices first, which is the
   * rule the server applies.
   */
  roomId: string;
  slot: string;
  /**
   * Whether the module already points this slot somewhere of its own.
   *
   * Read from the server's `overridden` rather than derived from the entity
   * chosen, because the picker's "Nothing here" is offered only when there is
   * something to put back.
   */
  overridden: boolean;
  /**
   * The entities the choice is drawn from, as the server ranked them: the
   * house's, with the module's room's own first (and no room in front when the
   * module is placed in the house).
   */
  includes: string[];
  /** The domains the slot accepts, which is what the selector is typed by. */
  accepts: string[];
  loading: boolean;
}

export interface SlotDevicesOptions extends SlotRowCommon {
  slots: ModuleSlot[];
}

/**
 * A module's device rows, in the order the server sent them.
 *
 * **The list is the caller's, and it is not always the module's whole set.** The
 * room's page passes the rows that are *set apart from the room's own binding*,
 * with the rest folded into a disclosure beside it -- so an empty list here is
 * the ordinary case of a module reaching through bindings it did not change, and
 * a sentence claiming the pack declares no slot would be flatly untrue there.
 * The case where the section is genuinely empty -- a module whose pack declares
 * no slot, a mode-only behaviour -- is said where the whole slot list is in hand
 * (`hosted-module.ts`, the card's own "Acts on" section), which is the only place
 * that can tell the two apart.
 */
export function slotDevices(options: SlotDevicesOptions): TemplateResult | typeof nothing {
  const { slots } = options;
  if (slots.length === 0) return nothing;
  return html`<div style="margin-top:10px">
    <span class="muted small">Acts on:</span>
    ${slots.map((slot) => slotDeviceRow({ ...options, slot }))}
  </div>`;
}

export interface DevicePickerOptions {
  /** What is being chosen for, e.g. `Bind the light_group slot`. */
  heading: string;
  /** The domains the slot accepts, from the catalog (`accepts_domains`). */
  accepts: string[];
  /**
   * The entities the choice is drawn from, as the server ranked them
   * (`open_house/rooms/candidates`): the whole house's, with the room's own
   * first for a room's picker.
   */
  includes: string[];
  /**
   * The room whose own devices head the list, by name; `null` for a picker with
   * no room in front of it (the house's, and a house-placed module's).
   *
   * Named rather than derived, because the picker cannot tell a room from the
   * house by looking at a list of entity ids: the list is the whole house's in
   * both cases, and the only difference is which of it the server ranked first.
   * The words are the page's to know because the page is what has the room.
   */
  first: string | null;
  /** The entity the slot is on now, so the control opens on it. */
  value: string | null;
  /** Whether the scoped set is still being read. */
  loading: boolean;
  disabled: boolean;
  /** Home Assistant itself, which is what draws the control. */
  hass: HassLike;
  /**
   * Offered when there is something to put back: the module's own choice, or a
   * binding the picker is replacing. Absent when there is nothing to clear.
   */
  onClear?: () => void;
  onCancel: () => void;
  onChoose: (entityId: string) => void;
}

/**
 * The device picker: Home Assistant's own entity selector.
 *
 * **The control is Home Assistant's, and that is the point.** A slot's *type* is
 * its `accepts_domains` (`catalog/slots.yaml`), and the entity selector is what
 * Home Assistant already renders for "pick a device of these domains" -- the
 * same control the automation editor, the blueprint importer and every other
 * pick-a-device box in the product uses. It searches by friendly name, resolves
 * device and area names, shows what is unavailable, and knows what a domain is,
 * none of which a hand-rolled list of ours would. A slot that accepts several
 * domains is therefore Home Assistant's list, not a second opinion about it.
 *
 * Which entities the control may offer is still ours: the selector has no area
 * filter, so the ids the server ranked travel as `include_entities`. **The list
 * is the whole house's**, for a room's slot as much as for a global one -- a
 * room's picker is a starting point, not a fence, because the devices a person
 * put in a room and the devices Home Assistant filed under that room's area are
 * two different things and only the second is something HA can be asked about
 * (see `views.candidates`). What a room changes is the *order*: its own devices
 * come first, still ranked by the flow's own rule, so the guess the setup flow
 * would have made is at the top and the flow's guess and this picker cannot
 * disagree about which device belongs first.
 *
 * The write is on change and there is no "Use this": the selector hands back a
 * chosen id and the page writes it, which is one fewer step than before and the
 * same one an automation's own device field takes.
 */
export function devicePicker(options: DevicePickerOptions): TemplateResult {
  const {
    heading,
    accepts,
    includes,
    first,
    value,
    loading,
    disabled,
    hass,
    onClear,
    onCancel,
    onChoose,
  } = options;
  // Where the offered list comes from, in one sentence: the whole house either
  // way, since a room's picker is no longer cut to the room -- only ordered by
  // it. A person who can see their hall lamp in Home Assistant is never told the
  // room "has none of those" while it is sitting right there.
  const from =
    first === null
      ? "every device the house holds"
      : `the devices ${first} holds first, then the rest of the house`;
  return html`<div class="card" style="margin-top:8px">
    <div class="row spread wrap">
      <strong>${heading}</strong>
      <div class="row">
        ${onClear === undefined
          ? nothing
          : html`<button
              type="button"
              class="icon"
              ?disabled=${disabled}
              @click=${() => onClear()}
            >
              Nothing here
            </button>`}
        <button type="button" class="icon" @click=${() => onCancel()}>
          Cancel
        </button>
      </div>
    </div>
    ${loading
      ? html`<p class="muted">Looking for devices...</p>`
      : includes.length === 0
        ? html`<p class="muted">
            Nothing here can fill this slot${accepts.length === 0
              ? ""
              : `: it accepts ${accepts.join(", ")}, and the house holds none of those`}.
          </p>`
        : html`<ha-selector
            .hass=${hass}
            .selector=${{
              entity: {
                domain: accepts,
                multiple: false,
                include_entities: includes,
              },
            }}
            .value=${value ?? ""}
            .disabled=${disabled}
            @value-changed=${(
              event: CustomEvent<{ value: string | undefined }>,
            ) => {
              // The selector's own clear reads as an empty string, and a slot
              // that stops naming a device is a slot whose module is held back:
              // the page owns that decision, so it is told and decides.
              const chosen = event.detail.value ?? "";
              if (chosen !== "") onChoose(chosen);
            }}
          ></ha-selector>`}
    <p class="help">
      ${accepts.length === 0
        ? `Everything the house holds is offered${
            first === null ? "" : `, ${first}'s own devices first`
          }.`
        : `Accepts ${accepts.join(", ")}, from ${from}.`}
    </p>
  </div>`;
}
