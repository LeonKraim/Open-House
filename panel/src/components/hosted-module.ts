/**
 * One module the house hosts, as a card -- anywhere one has to be shown.
 *
 * A module that was imported from an automation or a blueprint is not a pack: it
 * has no manifest, no behaviours and no place in the engine's installed set. It
 * has a document, the answers a person gave it, the outputs it publishes and the
 * settings they kept. That is a card of its own shape, and it is drawn in two
 * places -- the Dev tab, which is where modules are *made*, and a room's page,
 * which is where they are *put* -- so it is one element rather than two
 * renderings that would drift apart.
 *
 * **The card owns its own editing.** Its settings form, its Save and its Unhost
 * are all this element's, because they are all about this one module: a parent
 * that had to hold a draft map keyed by slug, a busy flag and a saving flag would
 * be a parent re-implementing the card, and the second parent would implement it
 * differently.
 *
 * Two things the card will not let a person edit, and both are the same rule:
 * a setting another module publishes into, and a setting the module's *room*
 * fills from a slot. A text box over either would hold a copy of a value that
 * the next publish or the next binding move overwrites, and nothing on the
 * screen would say the link had gone.
 */

import { html, nothing, type TemplateResult } from "lit";
import { OpenHouseElement } from "../base.ts";
// Registered by the import, not referenced here: the flow block below is
// `<open-house-node-red>` and this file only has to have the element defined.
import "./node-red-editor.ts";
// And the automation block, which is the same bargain with Home Assistant's own
// editor: the element is `<open-house-automation>` and this file only has to
// have it defined for the cast to be able to show one.
import "./automation-editor.ts";
// And the detach block: a cast a person wants to be a module of its own is a
// button beside the cast, and the element behind it is `<open-house-detach>`.
import "./detach.ts";
import type { DetachedDetail } from "./detach.ts";
// The edit screen is the *import* screen, opened on a module rather than on a
// source nobody has chosen yet -- one screen for both because they are the same
// questions, and this file only has to have the element defined.
import "../tabs/host-module.ts";
import type {
  HostedModule,
  HostedSlot,
  ModuleBinding,
  ModuleInputRow,
} from "../api/models.ts";
import { REFUSALS } from "../api/protocol.ts";
// The banner mark, so a card's warning and the suppression panel's notice are
// the same shape and carry the same `role`.
import { banner } from "./suppression.ts";
// The cast vocabulary lives beside this card rather than inside it, because the
// question it answers -- what a row may be answered *with* instead of a value --
// is asked on three screens: this card, the import screen, and a slot row. The
// rules about it are one set of rules and they are `casts.ts`.
//
// The `export *` is what keeps every caller that reached for them *here* still
// working: those names have lived in this module since it was the only screen
// that had them, and moving the file they are defined in is not a reason to make
// the import screen and its tests chase them somewhere new.
import {
  canCastSetting,
  canFlowWatch,
  castBinding,
  castHeldBy,
  castModeForSetting,
  castModeSelector,
  isTemplate,
  writtenCondition,
  type CastMode,
} from "./casts.ts";
export * from "./casts.ts";

/** A published value as one line a person can read. */
export function readValue(value: unknown): string {
  if (value === null || value === undefined) return "nothing yet";
  if (typeof value === "boolean") return value ? "on" : "off";
  if (typeof value === "string") return value || "(blank)";
  if (typeof value === "number") return String(value);
  return JSON.stringify(value);
}

/**
 * What supplies a setting the module does not, or `null` when it is the person's.
 *
 * A setting is theirs if the value is one they gave. Two things make it not
 * theirs, and both are shown rather than edited:
 *
 *   * another module publishing into it -- a live reading, not a number, and a
 *     box holding a copy would hold a value the next publish overwrites;
 *   * the device its *room* binds for it, when the input was answered with a
 *     slot -- typing an entity there would quietly replace the promise with one
 *     hard-coded device, and nothing on the screen would say the link had gone.
 *
 * The distinction is not cosmetic now that every input is kept by default: an
 * imported module's slot-answered inputs are exactly the ones this covers.
 */
export function boundElsewhere(setting: ModuleInputRow): string | null {
  if (setting.bound_kind === "output") {
    return `reads ${setting.bound_to}, so it is set by the module that publishes it`;
  }
  if (setting.bound_kind === "slot") {
    return (
      `is the device its room binds for ${setting.bound_to || "it"}, so it ` +
      "moves when the room's binding moves"
    );
  }
  return null;
}

/**
 * The entity ids in a control's answer, whichever shape it reported them in.
 *
 * A `target` control answers with `{entity_id: [...]}`, an entity control with
 * the id itself -- as a string when the input takes one, as a list when it takes
 * several. What the server wants is the ids: `module_host._bound_value` wraps
 * them into whatever the blueprint's own selector holds, so a screen that sent
 * the wrapper through would bind a target to a target.
 */
export function entityIds(value: unknown): string[] {
  const raw =
    value !== null && typeof value === "object" && "entity_id" in value
      ? (value as { entity_id: unknown }).entity_id
      : value;
  if (typeof raw === "string") return raw ? [raw] : [];
  if (Array.isArray(raw)) {
    return raw.filter((id): id is string => typeof id === "string" && id !== "");
  }
  return [];
}

/**
 * One setting's value as the binding the server reads.
 *
 * A device setting is bound as a *device* and not as whatever the control
 * happened to report: a target control's `{entity_id: [...]}` sent through as a
 * literal would put a mapping where the automation's target belongs, and the
 * module would run against a list of lights that is a string. Everything else
 * is the value the person typed, which is what the control for it holds.
 *
 * **A row the person did not type into is the row's own answer.** The form only
 * reports what it drew, so a row showing a cast instead of its own field reports
 * no value at all and the card records that as `undefined` -- which
 * `JSON.stringify` then drops, so the server would receive `{kind: "literal"}`
 * with no value rather than the answer the row still holds. The stored value is
 * what such a row means; a row that has none is bound to nothing rather than to
 * a key that vanishes on the wire.
 */
export function bindingForSetting(
  settings: readonly ModuleInputRow[],
  name: string,
  value: unknown,
): ModuleBinding {
  const row = settings.find((setting) => setting.name === name);
  const answer = value === undefined ? row?.value : value;
  if (row?.selector === "entity" || row?.selector === "target") {
    const ids = entityIds(answer);
    if (ids.length > 0) {
      return { kind: "entity", value: ids.length === 1 ? ids[0] : ids };
    }
  }
  return { kind: "literal", value: answer ?? null };
}

/**
 * The module's own options that nothing has set.
 *
 * Rows it keeps, that the blueprint gives no default, and that nothing else
 * fills: the person chose "ask me on the module" and has not answered yet. The
 * module is installed, its outputs exist, and nothing runs until one of these
 * is set -- a fact the card has to say, because an idle module with no
 * explanation reads as an import that failed.
 *
 * **A cast counts as an answer**, and each of the three is checked for a
 * different reason. A **flow** leaves a binding behind (the entity the flow
 * watches is what the row holds), so `bound` catches it. A **condition** and an
 * **automation** do not: Open House makes the entity, or makes the helper, and
 * the input reads what the condition decided or what the automation wrote --
 * neither is a value the person sent, so neither is in the record's inputs and
 * `bound` is false on a row that is perfectly answered. Left out of this test,
 * such a row would be named in the "waiting for" banner as something to go and
 * set, when the logic that answers
 * it is already built and running.
 */
export function unsetOptions(module: HostedModule): ModuleInputRow[] {
  return module.settings.filter(
    (setting) =>
      !setting.has_default &&
      !setting.bound &&
      !setting.cast &&
      !setting.automation_id,
  );
}

/**
 * The control an input's own selector asks for, whatever is being decided.
 *
 * Shared by the import screen and the settings form, because it is one question
 * about one declaration: a `select` is chosen from its menu, an `action` gets
 * the action editor -- a text box there would be a string where Home Assistant
 * requires a list of actions, which is a module the validator refuses -- and
 * everything this does not know is a text box, which is wrong for some of them
 * and at least visible, where hiding the input would not be.
 */
export function bySelector(input: ModuleInputRow): Record<string, unknown> {
  switch (input.selector) {
    case "number":
      return { number: { mode: "box" } };
    case "boolean":
      return { boolean: {} };
    case "time":
      return { time: {} };
    case "date":
      return { date: {} };
    case "target":
      return { target: {} };
    case "entity":
      // A `target` that names several -- the ellipse a blueprint asks a room's
      // lights with -- gets the control that takes several. One control for both
      // would offer one box for a question about three lights.
      return { entity: input.multiple ? { multiple: true } : {} };
    case "action":
      return { ui_action: {} };
    case "select":
      // The blueprint's own menu, in the order it wrote it. A select with no
      // options reads as free text rather than as a menu of nothing, and the
      // server sends the options for exactly this reason.
      return input.options.length > 0
        ? { select: { mode: "dropdown", options: input.options } }
        : { text: {} };
    default:
      return { text: {} };
  }
}

/**
 * The control a setting's stored value wants, which is not always its selector's.
 *
 * An entity input answered with a template holds a string no entity picker can
 * show: the control would say "unknown entity selected" about a value the
 * automation renders perfectly well. The value decides in that case, and the
 * declaration decides in every other, which is what `bySelector` answers.
 */
export function settingSelector(
  setting: ModuleInputRow,
  value: unknown,
): Record<string, unknown> {
  return isTemplate(value) ? { template: {} } : bySelector(setting);
}

/**
 * How long a change waits for the next one before the card saves.
 *
 * A form fires `value-changed` on every keystroke, and a module's settings are a
 * *rebuild* rather than a field write -- the automation is written again and
 * Home Assistant is asked to run it -- so one save per letter would rebuild a
 * room's automation a dozen times while somebody typed a number. Short enough
 * that nobody who stops typing is left waiting for it.
 */
export const AUTO_SAVE_MS = 600;

export class HostedModuleCard extends OpenHouseElement {
  static override properties = {
    ...OpenHouseElement.properties,
    module: { attribute: false },
    // Whether this card may be taken out of the house, and whether anything on
    // it may be edited at all. A viewer who is not an administrator reads the
    // card and changes nothing, which is the same rule every other screen
    // follows -- so this is also what gates the settings form and the
    // configuration controls, which a viewer who cannot send a write has no use
    // for. The parents hand the administrator flag in as `removable` (the same
    // person may do both); `admin` is read too, so a caller that sets it
    // directly is not silently read-only.
    removable: { type: Boolean },
    draft: { state: true },
    busy: { state: true },
    notice: { state: true },
    error: { state: true },
    // The publish toggles the card is showing flipped but has not heard back
    // about, by setting name. Reactive because the box is drawn from it: without
    // this the "Saving..." re-render would draw the row's old `published_key`
    // and snap the box back under the person's finger. Also the configuration
    // the menu is showing while a switch is in flight, for the same reason.
    publishing: { state: true },
    pendingConfig: { state: true },
    // Which configuration dialog is open, and the name typed into it. Reactive
    // because every one of them is a click: a plain field would leave the sheet
    // never drawn, which is the failure this card has already had once.
    configDialog: { state: true },
    configName: { state: true },
    // Whether the edit screen is up. Reactive for the same reason every other
    // dialog here is: it is opened by a click, and a plain field would leave the
    // sheet never drawn.
    editOpen: { state: true },
    // The house's profile revision the page this card sits on was read at, and
    // whether that page has since been told it is stale. Both are *given* by the
    // parent: a card does not read the revision itself, because it is the page
    // that fetched the house and only the page can say which house it fetched.
    revision: { type: Number },
    stale: { type: Boolean },
  };

  declare module: HostedModule;
  declare removable: boolean;
  declare revision: number;
  declare stale: boolean;

  private draft: Record<string, unknown> = {};
  /** A template cast typed over a setting's choice, by setting name. */
  private casting: Record<string, string> = {};
  /** A condition cast built over a setting's choice, by setting name. */
  private conditions: Record<string, unknown> = {};
  /** Which cast editor each touched setting is showing, by setting name. */
  private castModes: Record<string, CastMode> = {};
  /**
   * The auto-save waiting for the typing to stop.
   *
   * A plain field rather than a reactive one: nothing is drawn from it, and a
   * timer that redrew the card would be a render per keystroke -- the very thing
   * the wait is here to avoid.
   */
  private saveTimer: ReturnType<typeof setTimeout> | null = null;
  private busy = false;
  private notice: string | null = null;
  private error: ReturnType<OpenHouseElement["toError"]> | null = null;
  /** A publish toggle, or a configuration switch, waiting for a write to finish. */
  private publishing: Record<string, boolean> = {};
  /** The configuration the menu is showing while a switch is in flight. */
  private pendingConfig: string | null = null;
  /**
   * A publish the person flipped while another write was in flight.
   *
   * Held rather than dropped, and applied the moment that write is out: a
   * control that silently did nothing is a switch a person watches snap back.
   * Plain rather than reactive because nothing is drawn from it -- it is read in
   * `updated`, where the in-flight write's own re-render has cleared `busy`.
   */
  private queuedPublish: { setting: ModuleInputRow; publish: boolean } | null =
    null;
  /**
   * Whether the module update now arriving is one this card asked for.
   *
   * Every write here ends by telling the page, and the page answers by re-reading
   * the house and handing this card a fresh module. That reload is the card's own
   * doing and what the card is holding unsent is still owed to the server, so the
   * arrival must not be read as somebody else's answer landing over the top of
   * it. A change that arrives with this false came from outside -- another card,
   * or the page's own refresh -- and the answers on this card were decided
   * against a house that has moved.
   */
  private ownReload = false;
  /** `none`, or which of the two name dialogs is open. */
  private configDialog: "none" | "new" | "rename" = "none";
  /** What has been typed into that dialog, as typed. */
  private configName = "";
  /** Whether the edit screen is up over this card. */
  private editOpen = false;

  constructor() {
    super();
    this.removable = false;
    this.revision = 0;
    this.stale = false;
  }

  /**
   * Whether anything on this card may be written to at all.
   *
   * Two flags, because the same person arrives here two ways: the pages hand the
   * administrator flag in as `removable` -- they have no name for the card's own
   * right to be edited, and the person who may remove a module is the person who
   * may change it -- while a card inside the panel reads `admin` off the element
   * it inherited. Either is a yes. Reading only `admin` would make every card on
   * every page read-only, since no caller sets it; reading only `removable` would
   * ignore a caller that set the flag the panel uses everywhere else.
   */
  private get editable(): boolean {
    return this.admin || this.removable;
  }

  /**
   * What a flow-answered setting shows under its field: what it writes, and
   * Node-RED's own editor on the flow, in the page.
   *
   * **The editor, not a link to it.** The flow is this setting's answer and it
   * is edited here, beside the row it answers -- a link would send the person off
   * to a tab and back, which is where a half-built flow gets lost. What is
   * embedded is Node-RED itself, so nothing about it is second-hand.
   *
   * Three states, and they are all real. A setting whose module is hosted has a
   * flow, and the editor opens on that flow's tab. A setting just switched to
   * Node-RED has no flow *yet*, because the flows are pushed when the module is
   * saved; the sentence says so and the editor opens on its own landing page.
   * And a house with no Node-RED address set has neither, so the block names the
   * one place to put it.
   */
  private renderFlow(
    module: HostedModule,
    setting: ModuleInputRow,
  ): TemplateResult {
    const writes =
      setting.bound_kind === "flow" && setting.bound_to
        ? setting.bound_to
        : `sensor.open_house_flow_${module.slug}_${setting.name}`;
    // **Which half Open House built, said plainly**, because the two cases hand
    // over very different amounts of work and a person who expected the other one
    // would open the editor and find it not as they pictured. A row whose answer
    // is a device arrives wired end to end; a row holding a number arrives with
    // the output node alone, and that empty left-hand side is the point rather
    // than something left undone.
    const built = canFlowWatch(setting)
      ? html`The device this row holds is wired into its input node, so the flow
          runs on its own.`
      : html`Nothing is wired into it yet: this row's answer is not a device, so
          there was nothing for a trigger to watch -- build whatever starts the
          flow and wire it in.`;
    return html`<p class="help" data-flow=${setting.name}>
        Writes <code>${writes}</code>, which is what this setting reads. ${built}
        ${setting.flow_id
          ? nothing
          : html`The flow is created when you save.`}
      </p>
      <open-house-node-red
        .client=${this.client}
        .label=${`The flow for ${setting.title || setting.name}`}
        .flow=${setting.flow_id ?? ""}
      ></open-house-node-red>`;
  }

  /**
   * What an automation-answered setting shows under its field: the helper it
   * writes, and Home Assistant's own editor on the automation, in the page.
   *
   * **The mirror image of the flow block, and the difference is the whole reason
   * both exist.** A flow is built into an input that already holds a device: the
   * device is wired into the flow and the flow runs on its own. An automation is
   * the other way round -- it *writes a helper* of Open House's making, and the
   * input reads that helper -- so there is nothing wired in from this row, and
   * the sentence says what to add instead of leaving a person looking for a
   * device to connect.
   *
   * **The editor, not a link to it**, for the reason Node-RED's is: the
   * automation is this setting's answer, and opening its own tab is the trip
   * where the person loses the thread of which input they came here to fill. The
   * automation is Open House's own -- made with the helper when the module was
   * built and seeded with the action that sets it -- so this block only ever
   * opens it, and there is nothing here for the person to name.
   */
  private renderAutomation(
    module: HostedModule,
    setting: ModuleInputRow,
  ): TemplateResult {
    const name = setting.title || setting.name;
    return html`<p class="help" data-automation=${setting.name}>
        Open House wrote the action that sets
        <code>${setting.automation_entity || "the helper"}</code> into an
        automation of its own; what it needs is a trigger. Add one and
        ${module.title} reads that helper from then on -- between runs as much as
        during one.
      </p>
      <open-house-automation
        .client=${this.client}
        .automation=${setting.automation_id ?? ""}
        .entity=${setting.automation_entity ?? ""}
        .label=${`Home Assistant -- the automation for ${name}`}
      ></open-house-automation>`;
  }

  /** Which cast editor one setting is showing: what was clicked, else what it is. */
  private modeOf(name: string, row?: ModuleInputRow): CastMode {
    return this.castModes[name] ?? (row ? castModeForSetting(row) : "none");
  }

  /**
   * Save what the card holds, once the changes stop arriving.
   *
   * **The card has no Save button, and this is what took its place.** A module's
   * settings are answers to a document that is built again each time one moves,
   * so a person who changes a number and looks at the room expects the room to
   * be running the new number -- and a button they have to find first is a way
   * for a card to be showing one thing while the house runs another. Every
   * change re-arms this, so a burst of typing is one rebuild, and what is sent
   * is the whole of what the card is holding rather than the one field that
   * moved.
   */
  private queueSave(): void {
    if (this.saveTimer !== null) clearTimeout(this.saveTimer);
    this.saveTimer = setTimeout(() => {
      this.saveTimer = null;
      void this.save();
    }, AUTO_SAVE_MS);
  }

  /**
   * Drop a pending auto-save, for the things that make the draft meaningless.
   *
   * Binding another configuration, unhosting and being taken off the page all
   * leave the card holding answers that belong to something that is no longer
   * there -- and a timer that fired a moment later would write them over
   * whatever the card is showing by then.
   */
  private cancelSave(): void {
    if (this.saveTimer !== null) clearTimeout(this.saveTimer);
    this.saveTimer = null;
  }

  private async save(): Promise<void> {
    // A card on a page that has gone stale has nothing left worth sending: what
    // it holds was decided against a house that has moved, and the server would
    // refuse it anyway. The timer that got here is not rearmed either -- see
    // `disconnectedCallback`, which is the other caller.
    if (this.stale) return;
    // A write is already out. Every keystroke re-arms this timer, so this is the
    // timer firing again while the previous save is still in flight -- and the
    // change that re-armed it is still in the draft, which is what that save is
    // sending. Going now would race it on the same record; the write's own reply
    // re-arms the wait for whatever it did not carry.
    if (this.busy) return;
    const bindings: Record<string, ModuleBinding> = {};
    const casts: Record<string, unknown> = {};
    // The names this call actually put on the wire, so the reply can drop
    // exactly those and leave anything typed while it was out for the next save.
    const sent = new Set<string>();
    // The whole set of inputs answered by an *automation*, read off the module
    // for the flows' reason rather than off this save's touched rows: the set is
    // the answer, and the server takes a name that has dropped off it as an
    // automation -- and the helper it wrote -- to take away.
    const automations = new Set(
      this.module.settings
        .filter((setting) => this.modeOf(setting.name, setting) === "automation")
        .map((setting) => setting.name),
    );
    // The whole set of inputs answered by a flow, and not only the rows this save
    // touched, because the set *is* the answer: the server takes a name that has
    // dropped off it as a flow to take out of Node-RED, so a screen that sent
    // only its deltas would be claiming every flow it did not mention had been
    // removed. Read off the module -- including the rows the person never opened
    // -- rather than off the draft.
    const flows = new Set(
      this.module.settings
        .filter((setting) => this.modeOf(setting.name, setting) === "nodered")
        .map((setting) => setting.name),
    );
    for (const [name, value] of Object.entries(this.draft)) {
      const row = this.module.settings.find((one) => one.name === name);
      const mode = this.modeOf(name, row);
      // A **flow** travels as a name *and* as the row's own binding, which is the
      // one cast that does. The name is the flow; the binding is the entity that
      // flow *watches* -- the device under the row, or whatever the room binds
      // for the slot it names -- and it is read on the way to pushing the flow,
      // so dropping it here would leave the flow pointed at whatever the row
      // used to hold. What the input *reads* is not this binding: the server
      // binds it to the entity the flow writes, over the top of this, on the way
      // to building the automation.
      if (mode === "nodered") flows.add(name);
      if (mode === "automation") automations.add(name);
      // A **condition** travels as a condition and never as a binding: the server
      // makes an entity out of it and binds the input to that, and a device sent
      // beside it would be the answer that won.
      if (mode === "condition") {
        const written = writtenCondition(
          this.conditions[name] ?? row?.cast ?? null,
        );
        // An editor chosen and left empty is not an answer, it is a person on
        // their way to one -- whether or not a condition was stored before. The
        // menu is showing the editor, and the block inside it is deleted and
        // retyped often enough that reading the empty moment as "a removal"
        // would take the cast out from under the person mid-word. A condition
        // comes *off* by flipping the menu back to the input field, which is the
        // branch below, and never by leaving the editor empty.
        if (written === null) continue;
        casts[name] = written;
        sent.add(name);
        continue;
      }
      // An **automation** carries no binding either, for the condition's reason
      // and a further one: what fills the input is the *helper*, which the server
      // makes when it builds the module, so there is no value to write down here.
      // The name was added to the set above, with the flows; a row switched
      // *away* from this cast is a name simply absent from the set, which is how
      // the server is told to take the automation and its helper away.
      if (mode === "automation") {
        sent.add(name);
        continue;
      }
      // A **template** cast is the *whole* answer, exactly as it is on the import
      // screen: sending the device as well would write both into the automation,
      // and Home Assistant would take the device and ignore the logic rather than
      // the other way round.
      const cast = mode === "template" ? castBinding(this.casting[name]) : null;
      // And the same rule the condition editor gets: a template chosen and not
      // yet typed is nobody's answer. Sent, it would rebuild the row around a
      // device and take the cast already there with it, under a person's hands
      // mid-word. Taking a cast off is what the menu is for.
      if (mode === "template" && cast === null) continue;
      // The menu was flipped back to the choice above on a row a condition sits
      // behind, which is a person taking that condition off. `!= null` rather
      // than `!== undefined`: the server sends `null` for the settings that have
      // no condition, and sending a removal for each of those would be a write
      // nobody asked for.
      if (row?.cast != null) casts[name] = null;
      bindings[name] =
        cast ?? bindingForSetting(this.module.settings, name, value);
      sent.add(name);
    }
    // Nothing to send, and nothing to say about it: this is reached by the
    // auto-save, where the empty case is a form reporting a value it already
    // had rather than a person pressing a button that ought to answer. A cast
    // opened and still empty leaves the two empty as well -- the card keeps
    // holding it, which is what keeps its editor on the screen.
    if (
      Object.keys(bindings).length === 0 &&
      Object.keys(casts).length === 0
    ) {
      return;
    }
    this.busy = true;
    this.error = null;
    this.notice = null;
    let saved_ok = false;
    try {
      const reply = await this.requireClient().modulesSettings(
        this.module.slug,
        bindings,
        undefined,
        casts,
        [...flows],
        [...automations],
        // The house this card's answers were decided against. The page gives it,
        // and the server refuses the write when the house's profiles have moved
        // since -- see `wentStale`.
        this.revision,
      );
      const saved =
        reply.modules.find((row) => row.slug === this.module.slug) ?? this.module;
      saved_ok = true;
      // **Only what was sent.** A person who types into the next row while this
      // one is in flight has an answer that belongs to the save after this one,
      // and clearing the whole draft here is how it used to disappear: the reply
      // took the new keystroke with it, the re-armed timer found nothing to send
      // and stood down, and the value never left the card.
      this.dropSent(sent);
      this.moduleChanged();
      // What the save means is read off the module it produced rather than
      // assumed: a module whose slots its room has not bound yet, or whose own
      // options nothing has set, is saved and still not running, and saying
      // "Home Assistant is running it" about it would be the one thing this card
      // must never say.
      const waiting = [
        ...saved.slots
          .filter((slot) => !slot.bound)
          .map((slot) => `${slot.name} to be bound in ${saved.room_name}`),
        ...unsetOptions(saved).map(
          (setting) => `${setting.title || setting.name} to be set on it`,
        ),
      ];
      this.notice = waiting.length
        ? `${saved.title} is set up that way now. It is waiting for ` +
          `${waiting.join(", and for ")}, so its automation is not created ` +
          "until that is."
        : `${saved.title} is set up that way now, and Home Assistant is running it.`;
    } catch (error) {
      if (!this.wentStale(error)) this.error = this.toError(error);
    } finally {
      this.busy = false;
      // Whatever the reply did not carry is still owed to the server, and the
      // reply that just landed is what says so -- not the timer, which was
      // disarmed the moment this save started. Re-armed only after a write that
      // landed: a refusal leaves the draft on the screen for the person to see,
      // and retrying a refused write on a timer would be the card arguing with
      // the server.
      if (saved_ok && this.hasDraft()) this.queueSave();
      this.requestUpdate();
    }
  }

  /**
   * Drop from the card's per-row maps exactly the names the last save carried.
   *
   * Each map is what the row is showing while it is unsent, so a name that did
   * not travel keeps its entry -- and with it the editor a person is still
   * typing in, which the rebuild that follows would otherwise take away.
   */
  private dropSent(sent: Set<string>): void {
    if (sent.size === 0) return;
    this.draft = this.forgetMany(this.draft, sent);
    this.casting = this.forgetMany(this.casting, sent) as Record<string, string>;
    this.conditions = this.forgetMany(this.conditions, sent);
    this.castModes = this.forgetMany(this.castModes, sent) as Record<
      string,
      CastMode
    >;
  }

  /**
   * Tell the page this module moved, and own the reload that comes back.
   *
   * Every write on this card ends here, and the page answers by re-reading the
   * house and handing the card a fresh module. That is the card's own reload --
   * see `ownReload` -- so the incoming module is not read as an outside answer
   * landing over edits that have not been sent yet.
   */
  private moduleChanged(): void {
    // **A card off the page has nobody to tell.** Every caller binds
    // `module-changed` to the card element itself, so an event from a card that
    // has already been taken out of the tree travels nowhere -- which is the
    // case `disconnectedCallback` writes from, and the reason its flush used to
    // leave a save that landed with nothing to say about it. The write stands;
    // the page that draws this module reads it again when it next draws. And
    // `ownReload` is left alone here, because no reload is coming to own.
    if (!this.isConnected) return;
    this.ownReload = true;
    this.dispatchEvent(
      new CustomEvent("module-changed", { bubbles: true, composed: true }),
    );
  }

  /**
   * Whether a refusal is the page's house having moved, and say so if it is.
   *
   * A `stale_page` refusal is not a fault in the card's own answers. The house
   * went on a different profile while this card was holding them, and the write
   * was refused so it cannot land over that profile -- which is precisely the
   * bug this exists to stop, where a pending auto-save wrote a person's
   * pre-switch settings over the profile they had just switched to. The card
   * cannot answer the refusal by itself: what it holds is an answer to a house
   * that is no longer there. So it says nothing about it and tells the page
   * instead, and the page is the thing that knows how to stop being a live page.
   *
   * The event bubbles and composes because the card sits several levels below
   * the page that owns the revision; the page listens where it is, and nothing
   * in between has to know.
   */
  private wentStale(error: unknown): boolean {
    if (this.toError(error).code !== REFUSALS.stalePage) return false;
    this.cancelSave();
    if (!this.stale) {
      this.stale = true;
      this.dispatchEvent(
        new CustomEvent("page-stale", { bubbles: true, composed: true }),
      );
    }
    return true;
  }

  /**
   * Publish a row's logic as an entity anything may read, or stop publishing it.
   *
   * **Written on its own rather than through the auto-save**, because it is not
   * one of this module's *answers*: what a module publishes is decided in the
   * store and belongs to every room running it, while the auto-save writes the
   * answers of the one copy this card is showing. Folding the two together would
   * mean a switch on one room's card silently rewriting what the module is
   * everywhere -- which is the right outcome and the wrong way to arrive at it,
   * since the person is looking at a room.
   *
   * It is also the one control here that makes something exist *outside* the
   * module: an entity the rest of Home Assistant may bind to, or may already be
   * bound to. So it is sent as it is flipped, and the reply is not asked for --
   * `module-changed` is, which is what makes the page re-read the module and
   * this card render the entity id the server actually gave it.
   */
  private async setPublished(
    setting: ModuleInputRow,
    publish: boolean,
  ): Promise<void> {
    // A write is already out -- most often the auto-save, whose 600ms wait fired
    // as the switch was pressed. The toggle waits for it rather than being
    // dropped: a control that silently did nothing is a switch a person watches
    // snap back under their hand, and `updated` applies this the moment the
    // write in flight is done.
    if (this.busy) {
      this.queuedPublish = { setting, publish };
      return;
    }
    this.busy = true;
    // Drawn as flipped from here, so the "Saving..." re-render shows the person's
    // answer rather than the server's old one snapping the box back and re-ticks
    // it a moment later when the reply lands.
    this.publishing = { ...this.publishing, [setting.name]: publish };
    this.error = null;
    this.notice = null;
    this.requestUpdate();
    try {
      await this.requireClient().modulesPublish(
        this.module.slug,
        setting.name,
        publish,
        this.revision,
      );
      this.moduleChanged();
    } catch (error) {
      // Refused, so the box goes back to what the server still holds -- which is
      // what the row says, and not what was pressed.
      this.publishing = this.forgetMany(
        this.publishing,
        new Set([setting.name]),
      ) as Record<string, boolean>;
      if (!this.wentStale(error)) this.error = this.toError(error);
    } finally {
      this.busy = false;
      this.requestUpdate();
    }
  }

  /** Apply a publish that was flipped while another write was in flight. */
  protected override updated(): void {
    const queued = this.queuedPublish;
    if (queued && !this.busy) {
      this.queuedPublish = null;
      void this.setPublished(queued.setting, queued.publish);
    }
  }

  private async unhost(): Promise<void> {
    const title = this.module.title;
    // Before the module goes, so the auto-save cannot follow it: the card is
    // removed by the reload this causes, and a pending save would fire at a
    // module the house no longer runs.
    this.cancelSave();
    this.busy = true;
    this.error = null;
    this.notice = null;
    try {
      await this.requireClient().modulesUnhost(this.module.slug);
      this.moduleChanged();
      // No notice: the card is gone a moment later -- the list that drew it is
      // what was told to reload -- and a sentence about a module that is no
      // longer on the screen is a sentence nobody reads.
      void title;
    } catch (error) {
      this.error = this.toError(error);
    } finally {
      // Cleared on the way out of both paths, and not only the failed one: a
      // removal that answered without the card actually leaving -- which is what
      // happens whenever the page does not reload -- would otherwise leave every
      // button here disabled and the label reading "Removing..." for good.
      this.busy = false;
      this.requestUpdate();
    }
  }

  /**
   * One row's logic has become a module of its own: drop what was held for it.
   *
   * **Everything local about the row goes, and the row is not saved.** The
   * detach has already written the row's answer on the server -- an `output`
   * binding pointing at the new module -- so what the card holds for that row is
   * a copy of an answer that has moved, and the pending auto-save is a write
   * that would put the cast straight back over the binding the detach just made.
   * So the save is cancelled, the row's draft and cast state are dropped, and the
   * page is asked for the house again.
   *
   * **The sentence is the second half of the dialog's report.** The dialog said
   * what happened while it was up, and this is the same fact left on the card
   * afterwards, because a person who comes back to the room later has no dialog
   * to read and the row on its own does not say where its answer comes from.
   */
  private detached(name: string, detail: DetachedDetail): void {
    this.cancelSave();
    this.draft = this.forget(this.draft, name);
    this.casting = this.forget(this.casting, name) as Record<string, string>;
    this.conditions = this.forget(this.conditions, name);
    // The menu is told too, even though the record no longer holds a cast: the
    // reload that follows is not instantaneous, and until it lands the row would
    // otherwise draw the editor of a cast that is not there any more.
    this.castModes = { ...this.castModes, [name]: "none" };
    const where =
      detail.room_id === ""
        ? "in the whole house"
        : `in ${detail.room_id === this.module.room_id ? this.module.room_name : detail.room_id}`;
    const watching = detail.watched.length
      ? ` It runs when ${detail.watched.join(", ")} changes.`
      : "";
    this.notice =
      `${detail.title} is a module of its own now, ${where}, and ` +
      `${name} reads what it publishes.${watching}`;
    this.error = null;
    this.requestUpdate();
    this.moduleChanged();
  }

  /** A copy of one of the card's per-row maps without `name` in it. */
  private forget(
    record: Record<string, unknown>,
    name: string,
  ): Record<string, unknown> {
    return this.forgetMany(record, new Set([name]));
  }

  /** A copy of one of the card's per-row maps with none of `names` in it. */
  private forgetMany(
    record: Record<string, unknown>,
    names: Set<string>,
  ): Record<string, unknown> {
    const copy = { ...record };
    for (const name of names) delete copy[name];
    return copy;
  }

  /**
   * Make another of this module's configurations the one it is running.
   *
   * The module is built again from that configuration's answers, which is why
   * the draft is cleared: what the card was showing belonged to the
   * configuration being left, and keeping it over the incoming one's rows would
   * be showing values that belong to neither. An unsaved edit is *lost* by that,
   * so the notice says so rather than leaving the person to find out.
   */
  private async switchConfiguration(name: string): Promise<void> {
    if (name === this.module.config) return;
    const left = this.module.config;
    const unsaved = this.hasDraft();
    // **Before the switch, not after it.** A pending auto-save belongs to the
    // configuration being left, and the round trip below is long enough for it
    // to fire in the middle of one -- which would write those answers over the
    // configuration that had just become the running one.
    this.cancelSave();
    this.busy = true;
    // Shown as picked from here, so a refused switch puts the menu back rather
    // than leaving it on an option the module is not running.
    this.pendingConfig = name;
    this.error = null;
    this.notice = null;
    try {
      await this.requireClient().modulesConfigSwitch(
        this.module.slug,
        name,
        // The house this switch was decided on, for the reason `save` gives: a
        // configuration chosen against one house profile must not become the
        // running one under another.
        this.revision,
      );
      this.forgetDraft();
      this.notice =
        `${this.module.title} is running ${name} now, in the same automation ` +
        `and with the same outputs.` +
        (unsaved
          ? ` The edits made on ${left} and not saved were left behind.`
          : "");
      this.moduleChanged();
    } catch (error) {
      // The menu goes back to the configuration the module is still running.
      this.pendingConfig = null;
      if (!this.wentStale(error)) this.error = this.toError(error);
      // The switch did not happen, so nothing was left behind and the answers
      // on the card are still the ones this configuration is running: what the
      // cancel above took away is put back -- unless the page is stale, where
      // the answers are put back on a card that will not write them.
      if (unsaved && !this.stale) this.queueSave();
    } finally {
      this.busy = false;
      this.requestUpdate();
    }
  }

  /** Start a configuration from the running one, and switch the module to it. */
  private async addConfiguration(): Promise<void> {
    const name = this.configName.trim();
    if (name === "") return;
    const unsaved = this.hasDraft();
    // Before the write goes, like a switch: a pending auto-save belongs to the
    // configuration being left, and the round trip here is long enough for it to
    // fire in the middle of one -- putting two writes on the same record.
    this.cancelSave();
    this.busy = true;
    this.error = null;
    this.notice = null;
    try {
      await this.requireClient().modulesConfigAdd(this.module.slug, name);
      this.configDialog = "none";
      this.configName = "";
      this.forgetDraft();
      this.notice =
        `${this.module.title} is running ${name} now. It started as a copy of ` +
        "the configuration it was on, so change what makes it different.";
      this.moduleChanged();
    } catch (error) {
      this.error = this.toError(error);
      if (unsaved && !this.stale) this.queueSave();
    } finally {
      this.busy = false;
      this.requestUpdate();
    }
  }

  /** Give the running configuration a different name. Nothing else moves. */
  private async renameConfiguration(): Promise<void> {
    const from = this.module.config;
    const to = this.configName.trim();
    if (to === "" || to === from) return;
    const unsaved = this.hasDraft();
    // Cancelled for the same reason as a switch's: two writes must not race on
    // the one record. The answers themselves do not move, so they are put back
    // on the failure path and left to the reload on the success one.
    this.cancelSave();
    this.busy = true;
    this.error = null;
    this.notice = null;
    try {
      await this.requireClient().modulesConfigRename(this.module.slug, from, to);
      this.configDialog = "none";
      this.configName = "";
      this.notice = `${from} is called ${to} now. Its answers are unchanged.`;
      this.moduleChanged();
    } catch (error) {
      this.error = this.toError(error);
      if (unsaved && !this.stale) this.queueSave();
    } finally {
      this.busy = false;
      this.requestUpdate();
    }
  }

  /**
   * Drop the running configuration.
   *
   * The module moves to another one, so the draft goes for the same reason a
   * switch's does. The last configuration cannot be dropped at all -- the button
   * is off for that, and the server refuses it too, which is what the error
   * banner would show if the two ever disagreed.
   */
  private async removeConfiguration(): Promise<void> {
    const name = this.module.config;
    const unsaved = this.hasDraft();
    // Cancelled like a switch's, and for the same reason: the module moves to
    // another configuration, so a pending auto-save would be two writes racing
    // on one record.
    this.cancelSave();
    this.busy = true;
    this.error = null;
    this.notice = null;
    try {
      const reply = await this.requireClient().modulesConfigRemove(
        this.module.slug,
        name,
      );
      const now = reply.modules.find((row) => row.slug === this.module.slug);
      this.forgetDraft();
      this.notice = `${name} is gone. ${this.module.title} is running ${
        now?.config ?? "another configuration"
      } now.`;
      this.moduleChanged();
    } catch (error) {
      this.error = this.toError(error);
      if (unsaved && !this.stale) this.queueSave();
    } finally {
      this.busy = false;
      this.requestUpdate();
    }
  }

  /**
   * Whether a setting has been touched on this card and not saved.
   *
   * `castModes` is deliberately not counted. It is which editor a row is
   * *showing*, and merely picking a cast from the menu sets one with nothing
   * typed behind it -- so counting it warned a person that unsaved edits would
   * be left behind by a configuration switch that would in fact carry nothing.
   */
  private hasDraft(): boolean {
    return (
      Object.keys(this.draft).length > 0 ||
      Object.keys(this.casting).length > 0 ||
      Object.keys(this.conditions).length > 0
    );
  }

  /** Forget the draft, the way a save does: it belonged to what was on screen. */
  private forgetDraft(): void {
    this.draft = {};
    this.casting = {};
    this.conditions = {};
    this.castModes = {};
    // The pending save goes with it, because it is the same thing: a timer that
    // outlived the draft would write the answers of a card that is no longer
    // showing them -- over the configuration just switched to, or over nothing
    // at all.
    this.cancelSave();
  }

  /**
   * A card taken off the page saves what it was holding rather than dropping it.
   *
   * This is reached when the card is *removed* -- a tab change, a room's list
   * rebuilt without this module in it, a page torn down. A row that merely moves
   * does not come through here: the list is keyed by slug, so Lit reuses the
   * element and a move is a move. What does arrive is a person who typed into a
   * row and left in the same breath, and the wait is cut short for them rather
   * than the save thrown away. The two acts that *do* make the draft
   * meaningless, unhosting and switching configuration, cancel it before this
   * can see it.
   *
   * **The write lands and the page is not told**, which is not a bug here but a
   * fact about where the listener is: the pages bind `module-changed` to this
   * element, and an element that is no longer in the tree cannot deliver it. So
   * the flush gives the house the answer and `moduleChanged` stays quiet about
   * it, and the next read of the house picks the change up.
   *
   * **Except on a stale page, where the flush is the bug.** A card on a page the
   * house has moved out from under is holding answers from the old profile, and
   * this is the path that used to write them over the new one: a profile switch
   * rebuilds the room's list, the card is taken out, and the pending auto-save
   * lands the pre-switch answers on a module that has already been restored to
   * the profile just chosen. There is nothing to salvage here, because what the
   * card holds is not an edit to the house as it now is -- so the timer is
   * dropped and no save is made.
   */
  override disconnectedCallback(): void {
    super.disconnectedCallback();
    if (this.saveTimer !== null) {
      this.cancelSave();
      if (!this.stale) void this.save();
    }
  }

  protected override willUpdate(changed: Map<string, unknown>): void {
    if (!changed.has("module")) return;
    // A fresh answer for this module has changed hands, so the two things the
    // card was showing on the server's behalf stop being guesses and follow it
    // again: the publish toggle pressed a moment ago, and the configuration menu
    // a switch is on.
    this.publishing = {};
    this.pendingConfig = null;
    if (this.ownReload) {
      // The page answering a write this card made. What is left in the draft is
      // work the write did not carry, and it is still owed to the server -- so it
      // stays, and the save that re-armed for it sends it.
      this.ownReload = false;
      return;
    }
    // An outside reload -- another card's write, or the page's own refresh. The
    // draft was typed against a module that is no longer what the house runs, and
    // leaving it standing shows the person their own old values over the server's
    // new ones and writes them back over the top on the next keystroke.
    this.forgetDraft();
  }

  protected override render(): TemplateResult {
    const module = this.module;
    // **Which set of answers it is running goes on the line above everything
    // else.** It is the one fact that explains every value below it -- the same
    // module on another configuration is a different module in every row -- and
    // it used to be inside a disclosure, which is where a person reading the card
    // does not look for it.
    const running = this.pendingConfig ?? module.config;
    return html`<div class="nested">
      <div class="row spread wrap">
        <div class="grow">
          <h3>${module.title}</h3>
          <p class="help">
            <code>${module.slug}</code>
            ${module.room_id
              ? html` &middot; in ${module.room_name}`
              : html` &middot; in the whole house`}
            ${running ? html` &middot; running <strong>${running}</strong>` : null}
            ${module.automation_id
              ? html` &middot; runs as <code>${module.automation_id}</code>`
              : html` &middot; <span class="chip warn">not running</span>`}
            ${module.blueprint ? html` &middot; from ${module.blueprint}` : null}
          </p>
        </div>
        ${this.editable
          ? html`<div class="row">
              <button
                type="button"
                id="edit-${module.slug}"
                ?disabled=${this.busy}
                @click=${() => {
                  this.editOpen = true;
                }}
              >
                Edit this module
              </button>
              <button
                type="button"
                id="unhost-${module.slug}"
                ?disabled=${this.busy}
                @click=${() => void this.unhost()}
              >
                ${this.busy ? "Removing..." : "Remove from here"}
              </button>
            </div>`
          : nothing}
      </div>
      ${this.renderBanner(module)} ${this.renderSlots(module)}
      ${this.renderOutputs(module)} ${this.renderConfigurations(module)}
      ${this.renderSettings(module)} ${this.renderEdit()}
    </div>`;
  }

  /**
   * The one banner this card wears, and which of the three it is.
   *
   * **One, because they answer the same question.** An error, a module that is
   * waiting to be bound or set up, and a notice about a save that just landed are
   * three statements about what is happening to this module, and two of them
   * stacked would be a queue for a person to read rather than a card that says
   * one thing. So they are ranked -- a refusal outranks everything, because the
   * write did not happen; waiting outranks a notice, because a module that cannot
   * run yet is the more important fact about it; the notice is what is left --
   * and only the winner is drawn.
   *
   * **An error that belongs to a dialog is the dialog's.** The two name dialogs
   * are the only place a configuration write can be refused from, and the banner
   * here is drawn *behind* the backdrop: shown by the card and read by nobody.
   * While one of them is up, the card's own banner steps aside and the dialog
   * draws it (see `renderConfigDialog`).
   */
  private renderBanner(module: HostedModule): TemplateResult | typeof nothing {
    if (this.error && this.configDialog === "none") {
      return this.errorBanner(this.error);
    }
    const waiting = this.renderWaiting(module);
    if (waiting !== nothing) return waiting;
    return this.notice ? banner("info", this.notice) : nothing;
  }

  /**
   * What this module publishes, behind a disclosure.
   *
   * The rows are the point of a module -- an entity anything in the house may
   * read -- but they are a list that grows with the blueprint and about half of
   * it is the machinery: the key, what kind of output it is, the value it holds
   * now, the entity it is at, and the expression the engine renders it with. The
   * expression is the one nobody reads while using a module and everybody wants
   * while debugging one, so the list folds away and the expression folds again
   * inside it. What stays visible when the section is open is the two things a
   * person binds to: the key and the entity.
   */
  private renderOutputs(module: HostedModule): TemplateResult {
    if (module.outputs.length === 0) {
      return html`<p class="help">Publishes nothing.</p>`;
    }
    return html`<details
      class="nested"
      data-kind="outputs"
      data-module=${module.slug}
    >
      <summary>Outputs</summary>
      <div class="list">
        ${module.outputs.map(
          (output) => html`<div class="list-row">
            <span>
              <code>${output.key}</code>
              <span class="chip">${output.kind}</span>
            </span>
            <span class="readonly-value">${readValue(output.value)}</span>
            <p class="help"><code>${output.entity_id}</code></p>
            <details>
              <summary class="muted small">How it is worked out</summary>
              <p class="help"><code>${output.expression}</code></p>
            </details>
          </div>`,
        )}
      </div>
    </details>`;
  }

  /**
   * The edit screen, over the card, when Edit has been pressed.
   *
   * **The card is where it belongs.** A module's card is where a person finds
   * out what it does and what it publishes, so it is where the question "that is
   * not quite right, let me change it" is asked. And what is opened is the
   * *import* screen -- the same menu, the same rows, the same casts -- because
   * changing a module is answering the same questions again with the answers it
   * already has, and a second screen for the second half of that would be a
   * second place for the two to disagree.
   *
   * **What the save changes is the module, not this room.** That is what the
   * screen says while it is open and what its own closing message counts, and it
   * is why the event below goes up to the tab: every card in the house for this
   * module is now out of date, not just this one.
   */
  private renderEdit(): TemplateResult | typeof nothing {
    if (!this.editOpen) return nothing;
    return html`<open-house-dialog .heading=${`Editing ${this.module.title}`} .open=${true}
      @dialog-closed=${() => {
        this.editOpen = false;
      }}
    >
      <open-house-host-module
        .client=${this.client}
        .hass=${this.hass}
        .editing=${this.module.slug}
        @module-changed=${() => {
          this.editOpen = false;
          this.dispatchEvent(
            new CustomEvent("module-changed", { bubbles: true, composed: true }),
          );
        }}
      ></open-house-host-module>
    </open-house-dialog>`;
  }

  /**
   * Which set of answers this module is running, and the others it could.
   *
   * One placed module, several configurations, switched between in place. A
   * switch rebuilds the module from the set picked, into the same automation and
   * the same output entities -- so this is not a list of modules to choose
   * between but the *same* module answering differently, and only one of them is
   * ever running.
   *
   * Drawn for every module, including one that has only ever had one
   * configuration: the name is where the second one comes from, and a bar that
   * appeared only after the fact would be a feature nobody could find.
   */
  private renderConfigurations(module: HostedModule): TemplateResult {
    const many = module.configs.length > 1;
    const editable = this.editable;
    // Which one the menu shows, which is not always the module's: a switch in
    // flight is drawn on the option it was sent for, so a *refusal* puts the menu
    // back to the configuration the module is actually running rather than
    // leaving it on one that was never adopted.
    const running = this.pendingConfig ?? module.config;
    // `data-kind` is what tells this section from the settings one below it:
    // both are a `details.nested` carrying the module's slug, and a caller
    // asking for "this module's nested section" gets whichever comes first.
    return html`<details
      class="nested"
      data-kind="configs"
      data-module=${module.slug}
      data-config=${module.config}
    >
      <summary>Configuration</summary>
      <p class="help">
        Several sets of answers for this one module, switched between without
        making a second module: a switch builds the module again from the set you
        pick, into the same automation and the same outputs.
      </p>
      <div class="row wrap">
        <select
          id="config-${module.slug}"
          data-module=${module.slug}
          aria-label="${module.title} configuration"
          ?disabled=${this.busy || !editable}
          @change=${(event: Event) =>
            void this.switchConfiguration(
              (event.target as HTMLSelectElement).value,
            )}
        >
          ${module.configs.map(
            (name) => html`<option value=${name} ?selected=${name === running}>
              ${name}
            </option>`,
          )}
        </select>
        <button
          type="button"
          id="config-new-${module.slug}"
          ?disabled=${this.busy || !editable}
          @click=${() => {
            this.configDialog = "new";
            this.configName = "";
          }}
        >
          New...
        </button>
        <button
          type="button"
          id="config-rename-${module.slug}"
          ?disabled=${this.busy || !editable}
          @click=${() => {
            this.configDialog = "rename";
            this.configName = module.config;
          }}
        >
          Rename...
        </button>
        <button
          type="button"
          id="config-delete-${module.slug}"
          ?disabled=${this.busy || !many || !editable}
          @click=${() => void this.removeConfiguration()}
        >
          Delete
        </button>
      </div>
      ${many
        ? nothing
        : html`<p class="help">
            This is the only configuration. A module is always running one, so
            dropping it is what taking the module out of the house is for.
          </p>`}
    </details>
    ${this.renderConfigDialog()}`;
  }

  /**
   * The name dialog, for the two commands that need one.
   *
   * The panel's own sheet rather than `window.prompt`, which is what the two
   * would otherwise be: a browser prompt is drawn by the browser, on top of
   * everything, in a font this page did not choose.
   *
   * **Drawn outside the configuration section it belongs to.** The section is a
   * `<details>` and it is shut by default, and a dialog inside a shut disclosure
   * is a dialog that is not in the page at all -- the one a person opened would
   * simply never appear.
   *
   * **The refusal is drawn in here, with the button that caused it.** Both of
   * these commands write, and both can be refused for reasons the server words
   * better than this card can -- a name already taken, a module the house no
   * longer holds. The card's own banner is behind the backdrop while this is up,
   * so an error left to it is an error nobody sees: the same name stays in the
   * field, the button stays pressable, and the person presses it again.
   */
  private renderConfigDialog(): TemplateResult | typeof nothing {
    if (this.configDialog === "none") return nothing;
    const adding = this.configDialog === "new";
    return html`<open-house-dialog
      .heading=${adding
        ? `New configuration for ${this.module.title}`
        : `Rename ${this.module.config}`}
      .open=${true}
      @dialog-closed=${() => {
        this.configDialog = "none";
        this.configName = "";
        // Nothing on the card is about this dialog once it is gone, and a
        // refusal left standing would be read as a fault in the module -- the
        // command it belongs to has been closed, not failed again.
        this.error = null;
      }}
    >
      <div class="field">
        <label class="label" for="config-name">Name</label>
        <input
          id="config-name"
          type="text"
          class="grow"
          .value=${this.configName}
          placeholder=${adding ? "Evening" : this.module.config}
          @input=${(event: Event) => {
            this.configName = (event.target as HTMLInputElement).value;
          }}
        />
      </div>
      ${this.errorBanner(this.error)}
      <div class="row">
        <button
          type="button"
          id="config-confirm"
          ?disabled=${this.busy || this.configName.trim() === ""}
          @click=${() =>
            void (adding ? this.addConfiguration() : this.renameConfiguration())}
        >
          ${adding ? "Create it, and switch to it" : "Rename it"}
        </button>
      </div>
    </open-house-dialog>`;
  }

  /**
   * The module's settings, editable, which is what keeps it from being a fossil.
   *
   * Only the inputs the person kept at import appear, each with the value it was
   * built with. Saving builds the automation again from the same document, so a
   * change here is the same kind of change as the import was -- and the module
   * keeps its name, its automation and its outputs' entities.
   *
   * Three shapes, and the two outside the form are the two a viewer may still be
   * owed: a card nobody may write to draws the same rows as values rather than
   * fields (`renderSettingsReadonly`), and a module with nothing left settable
   * draws only what it was filled in with (`renderFilledIn`).
   */
  private renderSettings(module: HostedModule): TemplateResult | typeof nothing {
    // The record of what the module was filled in with is the whole of the
    // section when there is nothing left to edit, and it is not nothing: see
    // `renderFilledIn`.
    if (module.settings.length === 0) return this.renderFilledIn(module);
    if (!this.editable) return this.renderSettingsReadonly(module);
    // `data-module` is what makes this section addressable as *this* module's.
    // A rebuild moves a module to the end of the house's list, so a caller that
    // found the settings form by position -- `details.nested ha-form` -- would
    // read and write some other module's the moment anything was rebuilt.
    return html`<details
      class="nested"
      data-kind="settings"
      data-module=${module.slug}
      data-config=${module.config}
    >
      <summary>Settings</summary>
      <p class="help">
        Kept at import. Changing one builds the module's automation again from
        the same blueprint, so it keeps its name and its outputs.
      </p>
      ${module.settings.map((setting) => {
        const bound = this.draft[setting.name];
        const elsewhere = boundElsewhere(setting);
        if (elsewhere) {
          return html`<p class="help">
            <code>${setting.name}</code> ${elsewhere}.
          </p>`;
        }
        const value = bound ?? setting.value ?? setting.default ?? "";
        const selector = settingSelector(setting, value);
        // **A cast beside a device, here too.** The card is where a module is
        // lived with, and "the answer is right but I want it cast" arrives after
        // the import as often as during it. Same rule, same reasons, as the
        // import screen's row -- `canCastSetting` is that rule, written once.
        // A setting a condition is already behind keeps its editor whatever that
        // rule says: the condition is the person's, and hiding it would hide the
        // only place to change or clear it.
        const mode = castModeForSetting(setting, this.castModes[setting.name]);
        const castOffered =
          canCastSetting(setting, value) ||
          mode === "condition" ||
          mode === "nodered" ||
          mode === "automation";
        // **What the module *holds*, which is not what this form is showing.**
        // The button below detaches logic from the record, and the server reads
        // it from there -- so it is offered on the record's own cast rather than
        // on the menu's, and a person who has just flipped the menu to a cast
        // they have not saved yet is not offered a detach that would refuse.
        const held = castHeldBy(setting);
        // The output this row's logic is published as, when it is. Found in the
        // module's own published list rather than spelled out here, because the
        // entity id is a rule of the server's (`module_host.output_entity_id`)
        // and a second copy of it in the panel is a second thing that can
        // disagree with the first -- the one thing a screen may never do.
        const published = module.outputs.find(
          (output) => output.key === setting.published_key,
        );
        // **One thing answers the row, and only one of them is drawn.** A cast is
        // the answer and not a remark about it -- a template is bound in place of
        // the choice, a condition's entity is what the input ends up pointed at,
        // and a flow is what writes the value the input reads -- so the row's own
        // field goes while a cast is on. Two controls for one answer reads as two
        // answers, and the one above is the dead one.
        const schema: Record<string, unknown>[] = [];
        if (mode === "none") {
          schema.push({ name: setting.name, selector });
        }
        const data: Record<string, unknown> = { [setting.name]: value };
        if (castOffered) {
          schema.push({
            name: "cast_mode",
            selector: castModeSelector(setting.in_trigger),
          });
          data.cast_mode = mode;
          if (mode === "template") {
            schema.push({ name: "cast", selector: { template: {} } });
            data.cast = this.casting[setting.name] ?? "";
          }
          if (mode === "condition") {
            schema.push({ name: "cast", selector: { condition: {} } });
            const known = this.conditions[setting.name] ?? setting.cast;
            // Left unwritten when nothing is known, so Home Assistant's editor
            // draws its own empty state rather than one this screen made up.
            if (known != null) data.cast = known;
          }
        }
        return html`<div class="field">
          <label class="label" for=${`setting-${module.slug}-${setting.name}`}>
            ${setting.title || setting.name}
          </label>
          <ha-form
            data-module=${module.slug}
            data-setting=${setting.name}
            .hass=${this.hass}
            .data=${data}
            .schema=${schema}
            .computeLabel=${(item: { name: string }) => {
              if (item.name === "cast_mode") return "Set it to";
              if (item.name === "cast") {
                return mode === "condition" ? "The condition" : "The template";
              }
              return "";
            }}
            @value-changed=${(event: CustomEvent<{ value: Record<string, unknown> }>) => {
              const answered = event.detail.value;
              // The row's own field is off the form while a cast is on, and a
              // form reports what it drew -- so a name it did not carry is left
              // as it was rather than read as a setting someone cleared.
              this.draft = {
                ...this.draft,
                [setting.name]: answered[setting.name] ?? this.draft[setting.name],
              };
              // Queued before the cast bookkeeping below, and before the rows
              // that have no cast menu return: a value moved is an answer
              // whatever else the row offers.
              this.queueSave();
              if (!castOffered) return;
              const next = (answered.cast_mode ?? "none") as CastMode;
              this.castModes = { ...this.castModes, [setting.name]: next };
              if (next === "template") {
                this.casting = {
                  ...this.casting,
                  [setting.name]: String(answered.cast ?? ""),
                };
              }
              if (next === "condition") {
                this.conditions = {
                  ...this.conditions,
                  [setting.name]: answered.cast,
                };
              }
            }}
          ></ha-form>
          ${mode === "nodered" ? this.renderFlow(module, setting) : nothing}
          ${mode === "automation"
            ? this.renderAutomation(module, setting)
            : nothing}
          ${held !== "none"
            ? html`<open-house-detach
                .client=${this.client}
                .hass=${this.hass}
                .module=${module.slug}
                .moduleTitle=${module.title}
                .input=${setting.name}
                .inputTitle=${setting.title || setting.name}
                .cast=${held}
                .roomId=${module.room_id}
                .roomName=${module.room_name}
                @detach-done=${(event: CustomEvent<DetachedDetail>) => {
                  this.detached(setting.name, event.detail);
                }}
              ></open-house-detach>`
            : nothing}
          ${held !== "none"
            ? html`<label
                class="check"
                title="Home Assistant gets an entity holding this row's value, and any automation may read it -- one that has never heard of Open House included. The entity is taken away again when this is switched off."
              >
                <input
                  type="checkbox"
                  .checked=${this.publishing[setting.name] ??
                  Boolean(setting.published_key)}
                  ?disabled=${this.busy || !this.editable}
                  @change=${(event: Event) =>
                    void this.setPublished(
                      setting,
                      (event.target as HTMLInputElement).checked,
                    )}
                />
                <span>Also usable by any automation in Home Assistant</span>
              </label>
              ${published
                ? html`<p class="help">
                    Anything may read this row's value at
                    <code>${published.entity_id}</code>.
                  </p>`
                : nothing}`
            : nothing}
          ${mode === "template" && setting.in_trigger
            ? html`<p class="help warn">
                A trigger names the entities it watches, and Home Assistant
                matches that name against the entities the house has rather than
                reading it as an expression -- so a template there installs and
                never fires. Use a condition on this row and Open House works it
                out and points the trigger at the entity it makes.
              </p>`
            : nothing}
          ${mode === "automation" && setting.in_trigger
            ? html`<p class="help">
                A trigger names the entities it watches, and the helper this cast
                makes is a real entity -- so Home Assistant finds it, and the
                module runs when your automation writes it.
              </p>`
            : nothing}
          ${setting.description
            ? html`<p class="help">${setting.description}</p>`
            : nothing}
        </div>`;
      })}
      <p class="help">
        ${this.busy
          ? "Saving..."
          : "Saved as you change them: there is nothing to press. Each change " +
            "builds the module's automation again, into the same automation " +
            "and the same outputs."}
      </p>
      ${this.renderFilledIn(module)}
    </details>`;
  }

  /**
   * The same settings, read rather than written.
   *
   * A card drawn for a viewer who may not write is a card they may still read:
   * taking the values away because they cannot be changed would be hiding the
   * module to protect it. What goes is the machinery of changing them -- the
   * fields, the cast editors, the detach buttons, the publish switch -- because
   * every one of those is refused by the server for that viewer, and a control
   * that cannot do what it says is worse than no control.
   */
  private renderSettingsReadonly(module: HostedModule): TemplateResult {
    return html`<details
      class="nested"
      data-kind="settings"
      data-module=${module.slug}
      data-config=${module.config}
    >
      <summary>Settings</summary>
      <p class="help">
        Kept at import. Changing one builds the module's automation again from
        the same blueprint, which takes an administrator.
      </p>
      ${module.settings.map((setting) => {
        const elsewhere = boundElsewhere(setting);
        if (elsewhere) {
          return html`<p class="help">
            <code>${setting.name}</code> ${elsewhere}.
          </p>`;
        }
        // The stored value, and not the draft: nothing on this card is ever
        // typed into, which is what makes it a card rather than a form.
        return html`<p class="help">
          <code>${setting.name}</code>
          &rarr; ${readValue(setting.value ?? setting.default ?? "")}
        </p>`;
      })}
      ${this.renderFilledIn(module)}
    </details>`;
  }

  /**
   * What the module was filled in with: the blueprint's inputs, and their values.
   *
   * Folded into the Settings section rather than given a disclosure of its own,
   * because it is the same subject read a second way: the settings above are the
   * rows the person kept *settable*, and this is the whole of what the document
   * holds, including every input answered once and fixed inside the automation.
   * It is the record of what the module is, so it is drawn whether or not there
   * is anything left to edit.
   */
  private renderFilledIn(module: HostedModule): TemplateResult | typeof nothing {
    const filled = module.inputs.filter(
      (row) => row.value !== undefined && row.value !== null && row.value !== "",
    );
    if (filled.length === 0) return nothing;
    return html`<p class="help">Filled in with</p>
      ${filled.map(
        (row) => html`<p class="help">
          <code>${row.name}</code> &rarr; ${readValue(row.value)}
        </p>`,
      )}`;
  }

  /**
   * What a module reaches through, and which of those its room has not bound.
   *
   * Only shown when something is missing, because a slot that resolves is not a
   * fact anybody needs -- the module acts on the device the room bound and the
   * screen's job is done. A slot that does *not* resolve is the one thing that
   * explains a module sitting idle, and it names the device to go and bind.
   *
   * A missing *option* is the other thing that explains it, and it is the same
   * sentence with a different subject: an input the person said "ask me on the
   * module" about, which the blueprint gives no default, so there is nothing to
   * build the automation with yet. It is answered in the Settings section just
   * below this banner, which is why it is named rather than sent elsewhere.
   */
  private renderWaiting(module: HostedModule): TemplateResult | typeof nothing {
    const slots = module.slots.filter((slot) => !slot.bound);
    const options = unsetOptions(module);
    if (slots.length === 0 && options.length === 0) return nothing;
    return banner(
      "warn",
      html`${slots.length > 0
        ? html`Waiting for ${slots.map((slot) => slot.name).join(", ")} to be
            bound in ${module.room_name}.`
        : nothing}
      ${options.length > 0
        ? html`${options.map((setting) => setting.title || setting.name).join(", ")}
            ${options.length === 1 ? "is an option" : "are options"} of this
            module with no value yet, and the blueprint gives
            ${options.length === 1 ? "it" : "them"} no default. Set
            ${options.length === 1 ? "it" : "them"} under Settings below.`
        : nothing}
      The automation is not created until that is, so nothing it publishes is
      being written.`,
    );
  }

  /**
   * The devices this module does not own: the slots it reaches through.
   *
   * A module imported from a blueprint is answered with *roles*, not devices --
   * "the room's lights", "the room's lux sensor" -- and the room binds the device
   * behind each role once, for every module in it. So the row belongs on the card
   * rather than under Settings: what a person needs to see is which device this
   * module will actually act on, and the one thing they may change about it
   * without leaving the card is which **part** of a split slot it is on.
   *
   * Shown even when every slot resolves, unlike the waiting banner above, because
   * a slot that resolves is exactly the row a person came here to look at -- the
   * card that only spoke up when something was wrong could not answer "which
   * lights does this one actually drive?".
   */
  private renderSlots(module: HostedModule): TemplateResult | typeof nothing {
    if (module.slots.length === 0) {
      // **Said, rather than left as a gap.** This is the one place the module's
      // *whole* slot list is in hand -- the room's page passes only the rows set
      // apart from the room's binding, and folds the rest away -- so this is the
      // one place that can tell "its pack declares no slot" from "nothing about
      // its slots was changed". A module with no slot of its own is a mode-only
      // behaviour: it decides things rather than driving a device, and a card that
      // said nothing about it would read as a section that failed to load.
      return html`<div class="stack" style="margin-top:10px">
        <span class="muted small">Acts on</span>
        <p class="help" style="margin:4px 0 0">
          nothing -- this module's pack declares no slot for it to act through,
          so it decides things rather than driving a device.
        </p>
      </div>`;
    }
    return html`<div class="stack" style="margin-top:10px">
      <span class="muted small">Acts on</span>
      ${module.slots.map((slot) => this.renderSlot(module, slot))}
    </div>`;
  }

  /** One of those rows: the slot, the device it resolves to, and its part. */
  private renderSlot(
    module: HostedModule,
    slot: HostedSlot,
  ): TemplateResult {
    const chosen = slot.parts.find((part) => part.name === slot.part) ?? null;
    const id = `hosted-part-${module.slug}-${slot.name}`;
    return html`<div class="stack" data-hosted-slot=${slot.name} style="margin-top:8px">
      <div class="row wrap" style="align-items:center;gap:8px">
        <code>${slot.name}</code>
        <span
          class="chip"
          title=${slot.scope === "house"
            ? "A role the whole house resolves, so this is the same device in every room."
            : `Resolved in ${module.room_name}, by whichever device that room binds for it.`}
          >${slot.scope === "house"
            ? "whole house"
            : `in ${module.room_name}`}</span
        >
        ${slot.part
          ? html`<span
              class="chip"
              data-slot-part-chip=${slot.name}
              title="This module is on one part of a split slot. Everything on this part acts on the one device the house bound for it."
              >the ${chosen?.label ?? slot.part} part</span
            >`
          : nothing}
        <span class="grow"></span>
        ${slot.parts.length === 0
          ? nothing
          : html`<label class="muted small" for=${id}>Part</label>
              <select
                id=${id}
                data-slot-part=${slot.name}
                title="Which part of this split slot the module acts through. Everything on one part acts on one device."
                ?disabled=${this.busy || this.stale || !this.editable}
                .value=${slot.part}
                @change=${(event: Event) =>
                  void this.setSlotPart(
                    module,
                    slot,
                    (event.target as HTMLSelectElement).value,
                  )}
              >
                <option value="">The whole slot</option>
                ${slot.parts.map(
                  (part) => html`<option value=${part.name}>${part.label}</option>`,
                )}
              </select>`}
      </div>
      <p class="muted small" style="margin:0">
        ${slot.bound
          ? html`<span>${slot.bound_name || "a device"}</span>
              <code>${slot.bound}</code>`
          : html`Nothing bound${
              slot.part ? ` for the ${chosen?.label ?? slot.part} part` : ""
            } yet`}
      </p>
    </div>`;
  }

  /**
   * Put this module on one part of a split slot, or back on the whole slot.
   *
   * Written as a **binding** and not as a setting, because that is what a slot
   * answer is: the record keeps `{kind: "slot", slot, scope, part}` for the
   * input, and the server rebuilds the module from it -- the part is a binding
   * key of its own, so the device the automation is built with is the one the
   * house bound for that part (`module_host.slot_key`).
   *
   * Sent alone and with nothing else named. `modulesSettings` merges the
   * bindings it is given over the record and leaves every other kind of answer
   * alone when it is not sent at all -- so a card that passed the whole form
   * here would be re-sending answers the person never touched, and the empty
   * flow list in particular is how the server is told to take the flows *off*.
   */
  private async setSlotPart(
    module: HostedModule,
    slot: HostedSlot,
    part: string,
  ): Promise<void> {
    if (this.stale || this.busy || !this.editable) return;
    this.busy = true;
    this.error = null;
    this.notice = null;
    try {
      const reply = await this.requireClient().modulesSettings(
        module.slug,
        {
          [slot.input]: {
            kind: "slot",
            slot: slot.name,
            scope: slot.scope === "house" ? "house" : "room",
            part,
          },
        },
        undefined,
        undefined,
        undefined,
        undefined,
        this.revision,
      );
      const saved =
        reply.modules.find((row) => row.slug === module.slug) ?? module;
      this.module = saved;
      this.moduleChanged();
      const where = saved.slots.find((one) => one.name === slot.name);
      const named =
        part === ""
          ? "the whole slot"
          : `the ${where?.parts.find((one) => one.name === part)?.label ?? part} part`;
      // Two sentences, because two different things have happened: the module is
      // on the part either way, and whether it *acts* depends on whether the
      // house has bound that part to a device.
      this.notice = where?.bound
        ? `${module.title} now acts on ${named} of ${slot.name}: ${where.bound}.`
        : `${module.title} is on ${named} of ${slot.name}, and nothing is bound ` +
          "there yet -- bind a device for that part in the room it sits in, and " +
          "the module acts on it.";
    } catch (error) {
      if (!this.wentStale(error)) this.error = this.toError(error);
    } finally {
      this.busy = false;
      this.requestUpdate();
    }
  }
}

declare global {
  interface HTMLElementTagNameMap {
    "open-house-hosted-module": HostedModuleCard;
  }
}

if (!customElements.get("open-house-hosted-module")) {
  customElements.define("open-house-hosted-module", HostedModuleCard);
}
