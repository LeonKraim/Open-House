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
// And the script block, which is the same bargain with Home Assistant's own
// editor: the element is `<open-house-script>` and this file only has to have
// it defined for the cast to be able to show one.
import "./script-editor.ts";
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
  ModuleBinding,
  ModuleInputRow,
} from "../api/models.ts";
import { REFUSALS } from "../api/protocol.ts";
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
  scriptIdOf,
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
 */
export function bindingForSetting(
  settings: readonly ModuleInputRow[],
  name: string,
  value: unknown,
): ModuleBinding {
  const row = settings.find((setting) => setting.name === name);
  if (row?.selector === "entity" || row?.selector === "target") {
    const ids = entityIds(value);
    if (ids.length > 0) {
      return { kind: "entity", value: ids.length === 1 ? ids[0] : ids };
    }
  }
  return { kind: "literal", value };
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
 * watches is what the row holds), so `bound` catches it. A **condition** and a
 * **script** do not: Open House makes the entity, or makes the call, and the
 * input reads what the script hands back -- neither is a value the person sent,
 * so neither is in the record's inputs and `bound` is false on a row that is
 * perfectly answered. Left out of this test, such a row would be named in the
 * "waiting for" banner as something to go and set, when the logic that answers
 * it is already built and running.
 */
export function unsetOptions(module: HostedModule): ModuleInputRow[] {
  return module.settings.filter(
    (setting) =>
      !setting.has_default &&
      !setting.bound &&
      !setting.cast &&
      !setting.script_id,
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
    // Whether this card may be taken out of the house, and whether a settings
    // form is drawn at all. A viewer who is not an administrator reads the card
    // and changes nothing, which is the same rule every other screen follows.
    removable: { type: Boolean },
    draft: { state: true },
    busy: { state: true },
    notice: { state: true },
    error: { state: true },
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
  /**
   * A script cast picked over a setting's choice, by setting name.
   *
   * The id without its domain, which is what a `script.` call takes. The one
   * cast whose answer is *named* rather than written: the others are built here
   * (a condition, a template) or pushed here (a flow), and a script is the
   * person's own object in Home Assistant -- so what the card holds is a
   * reference to it, and the only thing it may do is point at a different one.
   */
  private scripts: Record<string, string> = {};
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
   * What a script-answered setting shows under its field: the call that fills
   * it, and Home Assistant's own editor on the script, in the page.
   *
   * **The mirror image of the flow block, and the difference is the whole reason
   * both exist.** A flow is built into an input that already holds a device: the
   * device is wired into the flow and the flow runs on its own. A script is the
   * other way round -- it is *called* when the module runs and hands its answer
   * back -- so there is nothing to wire in and nothing watched, and the sentence
   * says so rather than leaving a person looking for a trigger to connect.
   *
   * **The editor, not a link to it**, for the reason Node-RED's is: the script is
   * this setting's answer, and opening its own tab is the trip where the person
   * loses the thread of which input they came here to fill. It is a script the
   * house already has, or one they make in the editor the block opens.
   */
  private renderScript(
    module: HostedModule,
    setting: ModuleInputRow,
  ): TemplateResult {
    const name = setting.title || setting.name;
    return html`<p class="help" data-script=${setting.name}>
        Runs when ${module.title} runs and hands its answer back, which is what
        this input is built from -- so it is worked out afresh every time the
        module runs, and it needs nothing wired into it.
        ${setting.script_id
          ? nothing
          : html`Pick a script below, or write one in the editor.`}
      </p>
      <open-house-script
        .client=${this.client}
        .script=${this.scripts[setting.name] ?? setting.script_id ?? ""}
        .label=${`Home Assistant -- the script for ${name}`}
        @script-chosen=${(event: CustomEvent<{ script_id: string }>) => {
          this.chooseScript(setting.name, event.detail.script_id);
        }}
      ></open-house-script>`;
  }

  /**
   * A setting's script, as the editor has just left it.
   *
   * The event carries only the id, and which setting it belongs to is not in it
   * -- so the handler is built per row and names the row it was drawn for, the
   * way every other control on this card does. Written to the draft as well as
   * to the scripts, because the draft is what `save` walks: a script picked on a
   * row nobody otherwise touched is still an answer, and a save that skipped it
   * would drop the very thing the person just chose.
   */
  private chooseScript(name: string, scriptId: string): void {
    if (!scriptId) return;
    this.scripts = { ...this.scripts, [name]: scriptId };
    if (!(name in this.draft)) {
      const setting = this.module.settings.find((row) => row.name === name);
      if (setting) this.draft = { ...this.draft, [name]: setting.value ?? "" };
    }
    this.queueSave();
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
    const bindings: Record<string, ModuleBinding> = {};
    const casts: Record<string, unknown> = {};
    // The inputs answered by a *script*, by name. Unlike the flows this is not
    // read off the module for every setting -- a flow's set is the whole answer
    // (the server takes a name that dropped off it as a flow to take out of
    // Node-RED), while a script is the person's own object that Open House only
    // names, so what is sent is the rows that changed and nothing else. The one
    // consequence: a row left alone keeps its script through the server's merge.
    const scripts: Record<string, string> = {};
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
      // A **condition** travels as a condition and never as a binding: the server
      // makes an entity out of it and binds the input to that, and a device sent
      // beside it would be the answer that won. An editor opened and left empty is
      // not a condition but the *absence* of one, and a name sent as `null` is how
      // a cast comes back off -- the same name not sent at all means "keep".
      if (mode === "condition") {
        const written = writtenCondition(
          this.conditions[name] ?? row?.cast ?? null,
        );
        // An editor chosen and left empty is not an answer, it is a person on
        // their way to one. Sending it would send `null` -- the same removal as
        // taking the cast off -- and the rebuild that came back would draw the
        // row without its cast: the editor they are typing into, gone. Held,
        // and saved by the change that carries the condition itself.
        if (written === null && row?.cast == null) continue;
        casts[name] = written;
        continue;
      }
      // A **script** is the fourth answer and the one with no binding either,
      // for the condition's reason and a further one: what fills the input is
      // what the script hands back *when the module runs*, so there is no value
      // to write down here and nothing for the server to bind except a template
      // reading the variable its own call fills. Sent as the id it names, and
      // only when there is one -- a row switched to a script and not yet given
      // one is a person on their way to an answer, and the rebuild that came
      // back would draw the row without the editor they are working in.
      if (mode === "script") {
        const script = this.scripts[name];
        if (script) scripts[name] = script;
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
      // And the same for a script, which is the one cast that needs it: the
      // script branch above has already continued for every row still answered
      // by one, so a row that *had* a script and got here is a row the person
      // moved to something else -- a value, a device, a template, a flow. The
      // empty id is how the server is told to drop it, and reaching this line at
      // all is the proof that nothing else is answering the input now.
      if (row?.script_id) scripts[name] = "";
      bindings[name] =
        cast ?? bindingForSetting(this.module.settings, name, value);
    }
    // Nothing to send, and nothing to say about it: this is reached by the
    // auto-save, where the empty case is a form reporting a value it already
    // had rather than a person pressing a button that ought to answer. A cast
    // opened and still empty leaves the two empty as well -- the card keeps
    // holding it, which is what keeps its editor on the screen.
    if (
      Object.keys(bindings).length === 0 &&
      Object.keys(casts).length === 0 &&
      Object.keys(scripts).length === 0
    ) {
      return;
    }
    this.busy = true;
    this.error = null;
    this.notice = null;
    try {
      const reply = await this.requireClient().modulesSettings(
        this.module.slug,
        bindings,
        undefined,
        casts,
        [...flows],
        scripts,
        // The house this card's answers were decided against. The page gives it,
        // and the server refuses the write when the house's profiles have moved
        // since -- see `wentStale`.
        this.revision,
      );
      const saved =
        reply.modules.find((row) => row.slug === this.module.slug) ?? this.module;
      this.draft = {};
      this.casting = {};
      this.conditions = {};
      this.scripts = {};
      this.castModes = {};
      this.dispatchEvent(
        new CustomEvent("module-changed", { bubbles: true, composed: true }),
      );
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
      this.requestUpdate();
    }
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
      this.dispatchEvent(
        new CustomEvent("module-changed", { bubbles: true, composed: true }),
      );
      // No notice: the card is gone a moment later -- the list that drew it is
      // what was told to reload -- and a sentence about a module that is no
      // longer on the screen is a sentence nobody reads.
      void title;
    } catch (error) {
      this.error = this.toError(error);
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
    this.scripts = this.forget(this.scripts, name) as Record<string, string>;
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
    this.dispatchEvent(
      new CustomEvent("module-changed", { bubbles: true, composed: true }),
    );
  }

  /** A copy of one of the card's per-row maps without `name` in it. */
  private forget(
    record: Record<string, unknown>,
    name: string,
  ): Record<string, unknown> {
    const copy = { ...record };
    delete copy[name];
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
      this.dispatchEvent(
        new CustomEvent("module-changed", { bubbles: true, composed: true }),
      );
    } catch (error) {
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
      this.dispatchEvent(
        new CustomEvent("module-changed", { bubbles: true, composed: true }),
      );
    } catch (error) {
      this.error = this.toError(error);
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
    this.busy = true;
    this.error = null;
    this.notice = null;
    try {
      await this.requireClient().modulesConfigRename(this.module.slug, from, to);
      this.configDialog = "none";
      this.configName = "";
      this.notice = `${from} is called ${to} now. Its answers are unchanged.`;
      this.dispatchEvent(
        new CustomEvent("module-changed", { bubbles: true, composed: true }),
      );
    } catch (error) {
      this.error = this.toError(error);
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
      this.dispatchEvent(
        new CustomEvent("module-changed", { bubbles: true, composed: true }),
      );
    } catch (error) {
      this.error = this.toError(error);
    } finally {
      this.busy = false;
      this.requestUpdate();
    }
  }

  /** Whether a setting has been touched on this card and not saved. */
  private hasDraft(): boolean {
    return (
      Object.keys(this.draft).length > 0 ||
      Object.keys(this.casting).length > 0 ||
      Object.keys(this.conditions).length > 0 ||
      Object.keys(this.scripts).length > 0 ||
      Object.keys(this.castModes).length > 0
    );
  }

  /** Forget the draft, the way a save does: it belonged to what was on screen. */
  private forgetDraft(): void {
    this.draft = {};
    this.casting = {};
    this.conditions = {};
    this.scripts = {};
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
   * Leaving the page is not the same as abandoning the change: a reload that
   * rebuilds the list, or a row that moves, takes the element out and puts it
   * back, and a person who typed a number and clicked away in the same breath
   * meant it. So the wait is cut short rather than the save thrown away -- and
   * the two acts that *do* make the draft meaningless, unhosting and switching
   * configuration, cancel it before this can see it.
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

  protected override render(): TemplateResult {
    const module = this.module;
    return html`<div class="nested">
      <div class="row spread wrap">
        <div class="grow">
          <h3>${module.title}</h3>
          <p class="help">
            <code>${module.slug}</code>
            ${module.room_id
              ? html` &middot; in ${module.room_name}`
              : html` &middot; in the whole house`}
            ${module.automation_id
              ? html` &middot; runs as <code>${module.automation_id}</code>`
              : html` &middot; <span class="chip warn">not running</span>`}
            ${module.blueprint ? html` &middot; from ${module.blueprint}` : null}
          </p>
        </div>
        ${this.removable
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
      ${this.errorBanner(this.error)}
      ${this.notice ? html`<div class="banner info">${this.notice}</div>` : nothing}
      ${this.renderWaiting(module)}
      ${module.outputs.length === 0
        ? html`<p class="help">Publishes nothing.</p>`
        : html`<div class="list">
            ${module.outputs.map(
              (output) => html`<div class="list-row">
                <span>
                  <code>${output.key}</code>
                  <span class="chip">${output.kind}</span>
                </span>
                <span class="readonly-value">${readValue(output.value)}</span>
                <p class="help">
                  <code>${output.entity_id}</code> &middot;
                  ${output.expression}
                </p>
              </div>`,
            )}
          </div>`}
      ${this.renderConfigurations(module)} ${this.renderSettings(module)}
      ${this.inputsOf(module)} ${this.renderEdit()}
    </div>`;
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
    // `data-kind` is what tells this section from the settings one below it:
    // both are a `details.nested` carrying the module's slug, and a caller
    // asking for "this module's nested section" gets whichever comes first.
    return html`<details
      class="nested"
      open
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
          ?disabled=${this.busy}
          @change=${(event: Event) =>
            void this.switchConfiguration(
              (event.target as HTMLSelectElement).value,
            )}
        >
          ${module.configs.map(
            (name) => html`<option value=${name} ?selected=${name === module.config}>
              ${name}
            </option>`,
          )}
        </select>
        <button
          type="button"
          id="config-new-${module.slug}"
          ?disabled=${this.busy}
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
          ?disabled=${this.busy}
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
          ?disabled=${this.busy || !many}
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
      ${this.renderConfigDialog()}
    </details>`;
  }

  /**
   * The name dialog, for the two commands that need one.
   *
   * The panel's own sheet rather than `window.prompt`, which is what the two
   * would otherwise be: a browser prompt is drawn by the browser, on top of
   * everything, in a font this page did not choose.
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
   */
  private renderSettings(module: HostedModule): TemplateResult | typeof nothing {
    if (module.settings.length === 0) return nothing;
    // `data-module` is what makes this section addressable as *this* module's.
    // A rebuild moves a module to the end of the house's list, so a caller that
    // found the settings form by position -- `details.nested ha-form` -- would
    // read and write some other module's the moment anything was rebuilt.
    return html`<details
      class="nested"
      open
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
          mode === "script";
        // **What the module *holds*, which is not what this form is showing.**
        // The button below detaches logic from the record, and the server reads
        // it from there -- so it is offered on the record's own cast rather than
        // on the menu's, and a person who has just flipped the menu to a cast
        // they have not saved yet is not offered a detach that would refuse.
        const held = castHeldBy(setting);
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
          if (mode === "script") {
            // A script is **picked, not written**: it is Home Assistant's own
            // object, so the control is the entity picker over the `script`
            // domain -- the same one the import screen shows, for the same
            // reason. The card holds the id without its domain, which is what a
            // `script.` call takes, and the domain goes back on for the control,
            // which names whole entities. Left unwritten when there is nothing
            // chosen yet, so the picker draws its own empty state.
            schema.push({
              name: "script",
              selector: { entity: { domain: ["script"] } },
            });
            const chosen = this.scripts[setting.name] ?? setting.script_id ?? "";
            if (chosen) data.script = `script.${scriptIdOf(chosen)}`;
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
              if (item.name === "script") return "The script it reads";
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
              if (next === "script") {
                // Only a *picked* script is recorded: the form reports this
                // field on every change, including the one that switched the
                // menu to a script a moment ago, when there is nothing in the
                // picker yet. An empty answer there is a person on their way to
                // one, not a choice of nothing.
                const picked = scriptIdOf(answered.script);
                if (picked) {
                  this.scripts = { ...this.scripts, [setting.name]: picked };
                }
              }
            }}
          ></ha-form>
          ${mode === "nodered" ? this.renderFlow(module, setting) : nothing}
          ${mode === "script" ? this.renderScript(module, setting) : nothing}
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
          ${mode === "template" && setting.in_trigger
            ? html`<p class="help warn">
                A trigger names the entities it watches, and Home Assistant
                matches that name against the entities the house has rather than
                reading it as an expression -- so a template there installs and
                never fires. Use a condition on this row and Open House works it
                out and points the trigger at the entity it makes.
              </p>`
            : nothing}
          ${mode === "script" && setting.in_trigger
            ? html`<p class="help warn">
                A trigger names the entities it watches, and Home Assistant
                matches that name against the entities the house has -- it never
                runs anything written there -- so a script on this row would not
                be called and the automation would install and never fire. Use a
                condition here instead.
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
    </details>`;
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
    return html`<div class="banner warn">
      ${slots.length > 0
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
      being written.
    </div>`;
  }

  private inputsOf(module: HostedModule): TemplateResult | typeof nothing {
    const filled = module.inputs.filter(
      (row) => row.value !== undefined && row.value !== null && row.value !== "",
    );
    if (filled.length === 0) return nothing;
    return html`<details class="nested">
      <summary>Filled in with</summary>
      ${filled.map(
        (row) => html`<p class="help">
          <code>${row.name}</code> &rarr; ${readValue(row.value)}
        </p>`,
      )}
    </details>`;
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
