/**
 * The cast: the logic a row can be answered with instead of a value.
 *
 * A `ha-form` row asks one question -- what fills this input -- and a cast is the
 * second answer to it: not a value but the thing that works the value out. Which
 * is why the vocabulary lives here rather than inside the card that first needed
 * it. Three screens reach for the same menu and the same rules about it, and a
 * copy in each is three answers to "what does an empty template mean" that agree
 * only until one of them is edited.
 *
 * What is *not* here: the editors themselves. The condition editor and the
 * template box are Home Assistant's own (`ha-form` draws them from the selector
 * these return), and the Node-RED and automation editors are embeds of those
 * applications (`node-red-editor.ts`, `automation-editor.ts`). This module decides
 * which one a row shows, and what a row holds while it shows it.
 *
 * The four casts and the one thing they do not share:
 *
 *   * **template** -- an expression, rendered wherever the input lands. A value,
 *     so it travels as a literal binding.
 *   * **condition** -- a builder rather than an expression. It is *not* written
 *     into the module: Open House makes an entity out of it and binds the input to
 *     that, because a trigger's `entity_id` is matched against real entities and
 *     never rendered, so a condition written there would match nothing.
 *   * **nodered** -- a flow, built in Node-RED, which writes the value the input
 *     reads. The row's own answer stays, because it is what the flow watches.
 *   * **automation** -- a Home Assistant automation, built in Home Assistant's own
 *     editor, which *writes* the value. The one cast that runs on its own: Open
 *     House makes a helper out of the row's selector kind and seeds the automation
 *     with the action that sets it, so the person opens the editor on the syntax
 *     they need and adds the trigger. What the input reads is the helper's state.
 */

import { html, type TemplateResult } from "lit";

import type { ModuleBinding, ModuleInputRow } from "../api/models.ts";

/**
 * Which of the cast editors a row is showing, or none at all.
 *
 * `none` is not the same as an empty cast: a row with no cast uses the choice
 * above it, and a row showing the template box with nothing typed in it does
 * too -- which is why the mode is asked for separately rather than inferred from
 * emptiness. A person who has opened the condition editor and not built anything
 * yet has not answered, and the screen has to tell that from a row they never
 * touched.
 */
export type CastMode =
  | "none"
  | "template"
  | "condition"
  | "nodered"
  | "automation";

/**
 * A condition as an answer, or `null` for nothing written.
 *
 * "Empty" is asked of the *value* and not of its length. Home Assistant's
 * condition editor reports an untouched answer as `{}` and one opened and
 * abandoned as `[]`, and either is a person who has built nothing -- so neither
 * is an answer, and sending one would replace the choice above with a question
 * that asks nothing.
 */
export function writtenCondition(condition: unknown): unknown | null {
  if (condition === undefined || condition === null) return null;
  if (Array.isArray(condition)) return condition.length > 0 ? condition : null;
  if (typeof condition === "object") {
    return Object.keys(condition).length > 0 ? condition : null;
  }
  return null;
}

/**
 * Whether a stored value is logic rather than a value.
 *
 * Home Assistant's own reading: a field holding `{{ ... }}` or `{% ... %}` is a
 * template and is rendered before the automation uses it. The distinction has to
 * be made here because the two are *shown* differently -- a template in an entity
 * picker reads as an unknown entity, which is the one thing a screen must not
 * say about a value that works.
 */
export function isTemplate(value: unknown): boolean {
  return typeof value === "string" && /{{|{%/.test(value);
}

/**
 * What a Node-RED entry says in a house that has none.
 *
 * Exported because more than one menu offers a Node-RED entry -- a row's cast,
 * and a slot rule's kind -- and the same fact has to read the same way on both.
 * A person who learns "not set up" on one screen should not read a second phrase
 * for it on the next.
 */
export const NODE_RED_UNAVAILABLE = "A Node-RED flow -- Node-RED is not set up";

/**
 * Whether a resolved Node-RED address means there is one to use.
 *
 * **`null` is "not asked yet", and it reads as yes.** A screen is given its
 * module (or its room) and renders before the address has been read, so an
 * initial "no address" would grey a working house's Node-RED features for the
 * length of a round trip -- a control flickering on every open. And the only
 * answer entitled to take a feature away is the house's own, so an unanswered
 * question leaves it offered. An empty string is the house saying there is none.
 *
 * Every screen that offers a Node-RED entry holds the address this way and asks
 * this, so the rule is one function rather than one per screen.
 */
export function hasNodeRed(url: string | null): boolean {
  return url === null || url !== "";
}

/**
 * One entry in a cast menu: what picking it means, and what it reads as.
 *
 * `disabled` marks the one entry a house may not be able to reach yet -- a
 * **Node-RED flow** in a house that has no Node-RED. It is offered rather than
 * withheld, because the menu is how a person finds out the cast exists at all:
 * a flow of a neighbour's that they have seen working is a thing they will look
 * for here, and a menu of three where there are four is a person who never
 * learns what they could have had. Its own label says what is missing.
 */
export interface CastOption {
  value: CastMode;
  label: string;
  disabled: boolean;
}

/**
 * The menu a row's cast offers, which is not the same menu on every row.
 *
 * All four editors are offered everywhere -- a cast is something a person may
 * reach for on any row, and a row that offers none is a row they have to leave
 * the screen to answer. The one difference is the row a **trigger names**: Home
 * Assistant matches a trigger's `entity_id` against the entities a house has and
 * never renders it, so a template written there is text that matches nothing and
 * an automation that installs and never fires. The option stays -- it is not
 * this screen's business to take an answer away -- and its own label says
 * plainly what would happen.
 *
 * A **condition**, on the other hand, is *better* there than anywhere else: Open
 * House evaluates it itself and points the trigger at the entity it makes.
 *
 * **The flow is the one entry that depends on the house rather than the row**,
 * which is why this takes a second argument. A house with no Node-RED -- no
 * address set in the integration's options and the bundled add-on not installed
 * -- has nowhere to build a flow and nothing to push one to, so the entry is
 * disabled and says so. Which is also the whole rule the user asked for: every
 * Node-RED feature uses the person's own Node-RED when they have one (see
 * `node_red.async_client`, which prefers the configured address and falls back
 * to the add-on), and until there is one, the cast is on the menu but out of
 * reach.
 */
export function castMenuOptions(
  inTrigger: boolean,
  nodeRedAvailable: boolean,
): CastOption[] {
  return [
    // The row's own field, and the default: for most settings the answer is
    // a value or a device, and the menu is not what they are here for.
    { value: "none", label: "Input field", disabled: false },
    // **First, and this is the whole point of the menu.** A condition is
    // what a person reaches for when the answer is not a thing but a
    // question about the house, and it is the only one of the four that
    // works on a row a trigger names -- Open House works the condition out
    // and points the trigger at the entity it makes.
    {
      value: "condition",
      label: "A condition (Home Assistant's editor)",
      disabled: false,
    },
    // **On every row, and that is deliberate.** A flow is code, and what a
    // person does with it is theirs to decide: the output node -- the half
    // that hands a value back to this input -- is made whichever way the row
    // was answered, and the input node is made when the row's own answer
    // names an entity to trigger on. A row holding a number gets the output
    // node and an empty left-hand side to build into, which is the case this
    // was asked for. Offering it only where Open House could guess the
    // trigger would take the option away from the inputs most worth
    // programming.
    //
    // The label says what is missing when there is no Node-RED, so the reason
    // is on the screen rather than in a tooltip or in nothing at all.
    {
      value: "nodered",
      label: nodeRedAvailable
        ? "A Node-RED flow (nodes, in Node-RED)"
        : NODE_RED_UNAVAILABLE,
      disabled: !nodeRedAvailable,
    },
    // **Home Assistant's own automation, which writes the value.** The one
    // cast that *runs on its own*: a script only runs when something calls
    // it, so it can never keep an input current between runs, while an
    // automation triggers on the house moving and writes a helper the row
    // reads. Open House makes the helper and seeds the automation with the
    // action that sets it, so the person opens Home Assistant's own editor on
    // it, sees the syntax, and adds the trigger.
    {
      value: "automation",
      label: "HAOS automation (writes the value)",
      disabled: false,
    },
    {
      value: "template",
      label: inTrigger
        ? "A template -- will not work here, see below"
        : "A template (an expression)",
      disabled: false,
    },
  ];
}

/** What a cast menu is showing, and what to do when another entry is picked. */
export interface CastMenu {
  /** The id the label carries, so the select is named by the label on screen. */
  id: string;
  /** What the menu is called above it. */
  label: string;
  /** The entry showing now. */
  value: CastMode;
  /** Whether the row is one a trigger names, which relabels the template. */
  inTrigger: boolean;
  /** Whether this house has a Node-RED to build a flow in and push it to. */
  nodeRedAvailable: boolean;
  onChoose: (mode: CastMode) => void;
}

/**
 * The cast menu itself, drawn by this module rather than by `ha-form`.
 *
 * **It left the form's schema for one reason: Home Assistant's select selector
 * cannot disable an option.** `SelectOptionDict` is `{value, label}`, and the
 * selector validates its options strictly -- a `disabled` key is not an unknown
 * field it ignores, it is refused outright (`not a valid option at
 * 'options[0].disabled'`, checked against the version the container runs), so a
 * menu drawn from a schema has no way to offer an entry a person can see and
 * cannot pick. A native `<select>` has exactly that, and this panel already
 * draws its own selects wherever it needs one -- `activity.ts` and this card's
 * own configuration menu among them -- so the cast menu is one of those, built
 * from the same option list both screens share.
 *
 * **A disabled entry that is also the current answer stays selected.** A row a
 * flow already answers goes on reading as one when the Node-RED that built it
 * is taken away: the menu shows what the row *holds*, and moving the marker to
 * another entry would be this screen quietly answering for the person. A
 * browser draws a disabled option as the selected one and will not let it be
 * picked again -- which is the right pair of behaviours for a row that is still
 * a flow and can no longer be edited into one.
 */
export function castMenu(menu: CastMenu): TemplateResult {
  return html`<div class="field">
    <span class="label" id=${menu.id}>${menu.label}</span>
    <select
      aria-labelledby=${menu.id}
      @change=${(event: Event) => {
        menu.onChoose((event.target as HTMLSelectElement).value as CastMode);
      }}
    >
      ${castMenuOptions(menu.inTrigger, menu.nodeRedAvailable).map(
        (option) => html`<option
          value=${option.value}
          ?selected=${option.value === menu.value}
          ?disabled=${option.disabled}
        >
          ${option.label}
        </option>`,
      )}
    </select>
  </div>`;
}

/**
 * Which cast editor a stored setting opens on, and which one it is showing now.
 *
 * A setting with a **condition** behind it opens on the condition editor,
 * because the condition is the thing the person authored and the entity id the
 * input is bound to is machinery they never chose. A setting the person has
 * flipped the menu on since is what the menu says. The same rule for an
 * **automation** and a **flow**: what the person authored is the automation or the
 * flow, and the value it writes is not theirs to edit here.
 */
export function castModeForSetting(
  setting: ModuleInputRow,
  chosen?: CastMode,
): CastMode {
  if (chosen) return chosen;
  // A setting a flow answers opens on the flow, for the reason a condition does:
  // the flow is the thing the person authored, and the entity it writes is
  // machinery they never chose.
  if (setting.flow_id) return "nodered";
  // An automation the same again: the automation is the answer, and the helper
  // it writes is read rather than written here.
  if (setting.automation_id) return "automation";
  // **Truthiness, not presence.** The server sends `"cast": null` for every
  // setting that has none -- `record.derived.get(name)`, which is `None` for
  // all but the cast ones -- and `null !== undefined`, so a presence test opens
  // *every* setting on the condition editor and, worse, saves each one as a
  // cast rather than as the value the person typed. A condition is a mapping the
  // person built, and only a mapping is one.
  return setting.cast ? "condition" : "none";
}

/**
 * The cast the *module* holds for this row, read off the row the server sent.
 *
 * Read apart from `castModeForSetting`, which answers a different question --
 * *which editor to open* -- and so calls a **template** row `"none"`: a
 * template is already the row's own box, and opening the cast menu on one would
 * put a second template box under the first. The record does hold a cast there,
 * though: the row's answer *is* template text, which is the definition of the
 * template cast, and it is what makes the row one that could be detached.
 *
 * So this is the question "is there logic on this row at all", and the one place
 * that asks it is the detach button -- which is offered exactly where there is
 * something to move out.
 */
export function castHeldBy(setting: ModuleInputRow): CastMode {
  const mode = castModeForSetting(setting);
  if (mode !== "none") return mode;
  return isTemplate(setting.value) ? "template" : "none";
}

/**
 * Whether a flow can be started by *this* row's answer, with no one asked.
 *
 * **This narrows what Open House builds, and no longer what it offers.** Every
 * setting can be answered by a flow; this says only whether the push will arrive
 * with its input node already made. It is about the trigger and not about the
 * row: a state trigger watches exactly one entity, so it can be built when the
 * row's own answer resolves to one -- a device the person picked, a target, a
 * slot the room binds, another module's output. A row holding a number or a
 * piece of text has no "when this changes" to hand Node-RED, so that row's flow
 * comes with the output node alone and an empty left-hand side, and whatever
 * starts it is the person's to build.
 *
 * An **automation** cast does not come through here: an automation triggers on
 * its own and writes the helper the row reads, so whether a flow could watch
 * that row's answer is the same question it is for any other row.
 *
 * `multiple` is the one case that is neither: a target set to several entities
 * is a list, and a trigger given a list would fire on whichever of them was
 * written first -- which is not a thing anybody can be told about their module.
 * The output node is still made; only the trigger is withheld.
 */
export function canFlowWatch(setting: ModuleInputRow): boolean {
  if (setting.multiple) return false;
  const kind = setting.bound_kind ?? "";
  if (kind === "entity" || kind === "output" || kind === "slot") return true;
  // A row a flow already answers is bound to the flow's own entity, so what it
  // watches is a fact the record holds rather than something to work out here.
  if (setting.flow_id) return true;
  return setting.selector === "entity" || setting.selector === "target";
}

/**
 * Whether this setting is one a cast can be written over at all.
 *
 * Not a rule about *which* cast any more -- all four are offered on every row --
 * but about whether the row's control is itself the editor the cast would be. A
 * setting whose stored value is already a template is showing the template box,
 * so a second one under it would be two ways to write one thing; a literal is
 * text the person types. Everything else -- a device, a target, a number, a
 * text input -- is a choice a cast can be written over.
 */
export function canCastSetting(setting: ModuleInputRow, value: unknown): boolean {
  if (isTemplate(value)) return false;
  return setting.bound_kind !== "condition" && setting.bound_kind !== "flow";
}

/**
 * The binding a **template** cast stands for, or `null` when there is none.
 *
 * A cast is the *whole* answer: the device under it is what the person picked
 * while deciding, and what the automation is built with is the logic they wrote.
 * Empty is how the choice comes back, which is what makes the box on-demand
 * rather than a second, competing answer.
 *
 * A *condition*, *flow* or *automation* cast is deliberately not here: none of
 * the three is a binding but a `casts` (or `flows`, or `automations`) entry,
 * because the server makes an entity or a helper out of it and binds the input to
 * that -- and a binding sent beside it would be the answer that lost.
 */
export function castBinding(cast: string | undefined): ModuleBinding | null {
  const text = (cast ?? "").trim();
  return text ? { kind: "literal", value: text } : null;
}
